"""Job handlers of the ingestion pipeline.

``source.extract``: original file -> pages -> segments (with locations) -> FTS.
Re-running it for the same version replaces its pages and segments, so a retry
or a reprocess never duplicates data.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos import embeddings
from kos.embeddings import EmbeddingsUnavailable
from kos.ingestion.extract import ExtractionError, extract
from kos.ingestion.segment import estimate_tokens, segment_document
from kos.jobs.worker import JobContext, PermanentJobError, handler
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
)

EXTRACT_KIND = "source.extract"
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
    await _mark(ctx, source_id, SourceStatus.processing)

    try:
        doc = await asyncio.to_thread(extract, path, mime)
        segments = await asyncio.to_thread(segment_document, doc, doc.markdown)
        await ctx.progress(0.6)

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
    except Exception:
        if ctx.is_last_attempt:
            await _mark(ctx, source_id, SourceStatus.failed)
        raise


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
