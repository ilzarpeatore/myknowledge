"""Job handlers of the ingestion pipeline.

``source.extract``: original file -> pages -> segments (with locations) -> FTS.
Re-running it for the same version replaces its pages and segments, so a retry
or a reprocess never duplicates data.
"""

import asyncio
import contextlib
import multiprocessing
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos import embeddings
from kos.config import get_settings
from kos.embeddings import EmbeddingsUnavailable
from kos.ingestion.extract import Extracted, ExtractionError, extract, package_version
from kos.ingestion.segment import estimate_tokens, segment_document
from kos.jobs.worker import JobContext, PermanentJobError, handler, on_abandon
from kos.library.blobstore import get_store
from kos.models import (
    AuditLog,
    Job,
    JobStatus,
    SegmentEmbedding,
    Source,
    SourcePage,
    SourceSegment,
    SourceStatus,
    SourceVersion,
    TranscriptChunk,
)
from kos.references.service import refs_job

EXTRACT_KIND = "source.extract"


def is_media(mime: str) -> bool:
    return mime.startswith(("audio/", "video/"))


class Cancelled(PermanentJobError):
    pass


async def _section_runner(stack: contextlib.ExitStack):
    """Run each section of a transcription in a separate process (default): the speech
    model (~2 GB) is fully returned to the system when the job ends, so it never adds
    up with the PDF models (measured: both in one process exceeded a 4 GB worker)."""
    from kos.ingestion import docling_extract
    from kos.media import transcribe as tr

    if not get_settings().transcribe_in_subprocess:
        return lambda *a: asyncio.to_thread(tr.transcribe_section, *a)
    docling_extract.release()
    pool = stack.enter_context(
        ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    )
    loop = asyncio.get_running_loop()
    return lambda *a: loop.run_in_executor(pool, tr.transcribe_section, *a)


async def transcribe_media(
    ctx: JobContext, version_id: uuid.UUID, path: Path, language: str | None = None
) -> Extracted:
    """Transcribe section by section, saving each one; sections already saved for
    this version and model are reused, so a retry continues where it stopped."""
    from kos.ingestion.subtitles import build_transcript
    from kos.media import transcribe as tr

    if not tr.available():
        raise ExtractionError(
            "la transcripción necesita la imagen worker (faster-whisper y ffmpeg)"
        )
    model = tr.model_ref()
    total = await asyncio.to_thread(tr.probe_duration_ms, path)
    async with ctx.sessionmaker() as db:
        done = (
            await db.scalars(
                select(TranscriptChunk)
                .where(
                    TranscriptChunk.source_version_id == version_id, TranscriptChunk.model == model
                )
                .order_by(TranscriptChunk.idx)
            )
        ).all()
        sections = [(c.idx, c.end_ms, c.cues, c.language) for c in done]
    resumed = len(sections)
    pos = sections[-1][1] if sections else 0
    with contextlib.ExitStack() as stack:
        run = await _section_runner(stack)
        while pos < total - 500:
            if await ctx.cancelled():
                raise Cancelled("cancelado")
            sec = await run(path, pos, total, language)
            if sec.end_ms <= pos:  # nothing advanced: never loop forever
                break
            idx = len(sections)
            async with ctx.sessionmaker() as db:
                db.add(
                    TranscriptChunk(
                        source_version_id=version_id,
                        model=model,
                        idx=idx,
                        start_ms=sec.start_ms,
                        end_ms=sec.end_ms,
                        cues=sec.cues,
                        language=sec.language,
                    )
                )
                await db.commit()
            sections.append((idx, sec.end_ms, sec.cues, sec.language))
            pos = sec.end_ms
            await ctx.progress(min(0.9, 0.9 * pos / max(total, 1)))
    cues = [(int(a), int(b), t) for _, _, cs, _ in sections for a, b, t in cs]
    if not cues:
        raise ExtractionError("no se reconoció habla en el audio")
    languages = sorted({lang for *_, lang in sections if lang})
    return Extracted(
        units=[build_transcript(cues)],
        page_count=None,
        extractor="faster-whisper",
        extractor_version=f"{package_version('faster-whisper')}/{get_settings().whisper_model}",
        info={
            "duration_ms": total,
            "cues": len(cues),
            "sections": len(sections),
            "resumed_sections": resumed,
            "languages": languages,
        },
    )


EMBED_KIND = "source.embed"


def embed_job(version_id: uuid.UUID) -> Job:
    return Job(
        kind=EMBED_KIND,
        payload={"source_version_id": str(version_id)},
        status=JobStatus.pending,
        max_attempts=3,
    )


def extract_job(version_id: uuid.UUID) -> Job:
    """Build (not commit) the extraction job for a source version."""
    return Job(
        kind=EXTRACT_KIND,
        payload={"source_version_id": str(version_id)},
        status=JobStatus.pending,
        max_attempts=3,
    )


async def extraction_in_flight(db: AsyncSession, version_id: uuid.UUID) -> bool:
    found = await db.scalar(
        select(Job.id)
        .where(
            Job.kind == EXTRACT_KIND,
            Job.payload["source_version_id"].astext == str(version_id),
            Job.status.in_([JobStatus.pending, JobStatus.processing]),
        )
        .limit(1)
    )
    return found is not None


def _set_status(source: Source, status: SourceStatus) -> None:
    # An archived source stays archived; the new state applies when restored.
    if source.status == SourceStatus.archived:
        source.status_before_archive = status.value
    else:
        source.status = status
    source.updated_at = datetime.now(UTC)


async def _mark(ctx: JobContext, source_id: uuid.UUID, status: SourceStatus) -> None:
    async with ctx.sessionmaker() as db:
        source = await db.get(Source, source_id, with_for_update=True)
        if source is not None:
            _set_status(source, status)
            await db.commit()


@handler(EXTRACT_KIND)
async def extract_source(ctx: JobContext) -> dict[str, Any]:
    try:
        version_id = uuid.UUID(ctx.payload["source_version_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload sin source_version_id válido") from None

    async with ctx.sessionmaker() as db:
        version = await db.get(SourceVersion, version_id)
        if version is None:
            raise PermanentJobError(f"no existe la versión {version_id}")
        source_id, mime = version.source_id, version.mime
        path = Path(get_store().path_for(version.storage_key))
        language = (await db.get(Source, source_id)).language
    await _mark(ctx, source_id, SourceStatus.processing)

    try:
        if is_media(mime):
            doc = await transcribe_media(ctx, version_id, path, language)
        else:
            doc = await asyncio.to_thread(extract, path, mime)
        segments = await asyncio.to_thread(segment_document, doc, doc.markdown)
        await ctx.progress(0.95 if is_media(mime) else 0.6)

        async with ctx.sessionmaker() as db:
            await db.execute(
                delete(SourceSegment).where(SourceSegment.source_version_id == version_id)
            )
            await db.execute(delete(SourcePage).where(SourcePage.source_version_id == version_id))
            db.add_all(
                SourcePage(
                    source_version_id=version_id, page_no=u.page_no or u.slide_no or 1, text=u.text
                )
                for u in doc.units
            )
            db.add_all(
                SourceSegment(
                    source_version_id=version_id,
                    ordinal=i,
                    text=seg.text,
                    token_count=estimate_tokens(seg.text),
                    loc_page_start=seg.page_no,
                    loc_page_end=seg.page_no,
                    loc_section=seg.section,
                    loc_char_start=seg.char_start,
                    loc_char_end=seg.char_end,
                    loc_slide=seg.slide_no,
                    loc_t_start_ms=seg.t_start_ms,
                    loc_t_end_ms=seg.t_end_ms,
                    loc_timeline=seg.timeline,
                )
                for i, seg in enumerate(segments)
            )
            version = await db.get(SourceVersion, version_id)
            version.page_count = doc.page_count
            version.extractor = doc.extractor
            version.extractor_version = doc.extractor_version
            version.extraction_info = doc.info or None
            version.extracted_at = datetime.now(UTC)
            source = await db.get(Source, source_id, with_for_update=True)
            _set_status(source, SourceStatus.ready)
            summary = {
                "pages": len(doc.units),
                "segments": len(segments),
                **(
                    {"pages_without_text": doc.info["pages_without_text"]}
                    if "pages_without_text" in doc.info
                    else {}
                ),
            }
            db.add(embed_job(version_id))
            db.add(refs_job(version_id))
            db.add(
                AuditLog(
                    actor="worker",
                    action="source.extracted",
                    subject_table="sources",
                    subject_id=str(source_id),
                    after={"version_id": str(version_id), **summary},
                )
            )
            await db.commit()
        return summary
    except ExtractionError as exc:
        await _mark(ctx, source_id, SourceStatus.failed)
        raise PermanentJobError(str(exc)) from None
    except Cancelled:
        await _mark(ctx, source_id, SourceStatus.failed)
        raise
    except Exception:
        if ctx.is_last_attempt:
            await _mark(ctx, source_id, SourceStatus.failed)
        raise


@on_abandon(EXTRACT_KIND)
async def extract_abandoned(ctx: JobContext) -> None:
    """The worker died on every attempt: the source must not stay «processing»."""
    try:
        version_id = uuid.UUID(ctx.payload["source_version_id"])
    except (KeyError, ValueError):
        return
    async with ctx.sessionmaker() as db:
        version = await db.get(SourceVersion, version_id)
    if version is not None:
        await _mark(ctx, version.source_id, SourceStatus.failed)


EMBED_BATCH = 64


@handler(EMBED_KIND)
async def embed_source(ctx: JobContext) -> dict[str, Any]:
    """Compute vectors for the segments of a version that lack one for the current
    model. Idempotent: safe to retry, and a model change re-embeds everything."""
    try:
        version_id = uuid.UUID(ctx.payload["source_version_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload sin source_version_id válido") from None
    try:
        embedder = await asyncio.to_thread(embeddings.get_embedder)
    except EmbeddingsUnavailable as exc:
        raise PermanentJobError(str(exc)) from None

    async with ctx.sessionmaker() as db:
        rows = (
            await db.execute(
                select(SourceSegment.id, SourceSegment.text)
                .outerjoin(
                    SegmentEmbedding,
                    (SegmentEmbedding.segment_id == SourceSegment.id)
                    & (SegmentEmbedding.model == embedder.model_id),
                )
                .where(
                    SourceSegment.source_version_id == version_id,
                    SegmentEmbedding.segment_id.is_(None),
                )
                .order_by(SourceSegment.ordinal)
            )
        ).all()

    done = 0
    for i in range(0, len(rows), EMBED_BATCH):
        batch = rows[i : i + EMBED_BATCH]
        vectors = await asyncio.to_thread(embedder.embed_passages, [r.text for r in batch])
        async with ctx.sessionmaker() as db:
            # Replace any vector from a previous model for these segments.
            await db.execute(
                delete(SegmentEmbedding).where(
                    SegmentEmbedding.segment_id.in_([r.id for r in batch])
                )
            )
            db.add_all(
                SegmentEmbedding(segment_id=r.id, model=embedder.model_id, embedding=v)
                for r, v in zip(batch, vectors, strict=True)
            )
            await db.commit()
        done += len(batch)
        await ctx.progress(done / len(rows))
    return {"embedded": done, "model": embedder.model_id}
