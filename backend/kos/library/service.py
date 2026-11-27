"""Library operations: accept an upload, deduplicate, register the source."""

import asyncio
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from kos.ingestion.pipeline import extract_job, extraction_in_flight
from kos.library.blobstore import LocalBlobStore, StagedFile
from kos.models import (
    AuditLog,
    Job,
    JobStatus,
    Source,
    SourceKind,
    SourcePage,
    SourceSegment,
    SourceStatus,
    SourceVersion,
)


class UnsupportedFile(Exception):
    pass


@dataclass(frozen=True)
class DetectedFormat:
    mime: str
    kind: SourceKind


TEXT_EXTENSIONS = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".vtt": "text/vtt",
    ".srt": "application/x-subrip",
}
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
EPUB = "application/epub+zip"
ZIP_MAX_ENTRIES = 20_000
ZIP_MAX_UNCOMPRESSED = 2 * 1024**3


def _detect_media(head: bytes) -> DetectedFormat | None:
    """Audio/video containers by their signature."""
    a, v = SourceKind.audio, SourceKind.video
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return DetectedFormat("audio/mp4", a)
        if brand == b"qt  ":
            return DetectedFormat("video/quicktime", v)
        return DetectedFormat("video/mp4", v)
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return DetectedFormat("video/x-matroska" if b"matroska" in head[:64] else "video/webm", v)
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return DetectedFormat("audio/wav", a)
    if head.startswith(b"RIFF") and head[8:12] == b"AVI ":
        return DetectedFormat("video/x-msvideo", v)
    if head.startswith(b"OggS"):
        return DetectedFormat("audio/ogg", a)
    if head.startswith(b"fLaC"):
        return DetectedFormat("audio/flac", a)
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return DetectedFormat("audio/mpeg", a)
    return None


def _detect_zip(path) -> str:
    """DOCX, PPTX or EPUB from the package contents (not the extension), refusing
    archives that would expand to an unreasonable size."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if (
                len(infos) > ZIP_MAX_ENTRIES
                or sum(i.file_size for i in infos) > ZIP_MAX_UNCOMPRESSED
            ):
                raise UnsupportedFile("el archivo comprimido es demasiado grande al descomprimirlo")
            names = {i.filename for i in infos}
            if "mimetype" in names and z.read("mimetype")[:64].strip() == EPUB.encode():
                return EPUB
            if "[Content_Types].xml" in names:
                if "word/document.xml" in names:
                    return DOCX
                if "ppt/presentation.xml" in names:
                    return PPTX
    except zipfile.BadZipFile:
        raise UnsupportedFile("el archivo comprimido está dañado") from None
    raise UnsupportedFile("archivo comprimido no reconocido (se admiten DOCX, PPTX y EPUB)")


def clean_filename(name: str | None) -> str | None:
    """Keep only the final path component, without control characters."""
    if not name:
        return None
    base = PurePath(name.replace("\\", "/")).name
    base = "".join(c for c in base if unicodedata.category(c)[0] != "C").strip()
    return base[:255] or None


def detect_format(staged: StagedFile, filename: str | None) -> DetectedFormat:
    """Decide the format from the content, using the extension only for plain text."""
    if staged.size == 0:
        raise UnsupportedFile("el archivo está vacío")
    ext = PurePath(filename or "").suffix.lower()
    if staged.head.startswith(b"%PDF-"):
        return DetectedFormat("application/pdf", SourceKind.document)
    if ext == ".pdf":
        raise UnsupportedFile("tiene extensión .pdf pero no es un PDF válido")
    if media := _detect_media(staged.head):
        return media
    if staged.head.startswith(b"PK\x03\x04"):
        return DetectedFormat(_detect_zip(staged.path), SourceKind.document)
    if ext in TEXT_EXTENSIONS:
        data = staged.path.read_bytes()
        if b"\x00" in data:
            raise UnsupportedFile("el archivo de texto contiene datos binarios")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise UnsupportedFile("los archivos de texto deben estar en UTF-8") from None
        return DetectedFormat(TEXT_EXTENSIONS[ext], SourceKind.document)
    raise UnsupportedFile(
        "formato no admitido (se admiten PDF, DOCX, PPTX, EPUB, Markdown, TXT, subtítulos"
        " SRT/VTT, audio y vídeo)"
    )


def _default_title(filename: str | None) -> str:
    if filename:
        stem = PurePath(filename).stem.strip()
        if stem:
            return stem
    return "Sin título"


@dataclass
class AddResult:
    source: Source
    duplicate: bool


async def get_source(db: AsyncSession, source_id: Any) -> Source | None:
    return await db.scalar(
        select(Source).where(Source.id == source_id).options(selectinload(Source.versions))
    )


async def _source_for_sha(db: AsyncSession, sha256: str) -> Source | None:
    version = await db.scalar(select(SourceVersion).where(SourceVersion.sha256 == sha256))
    return await get_source(db, version.source_id) if version else None


async def add_upload(
    db: AsyncSession,
    store: LocalBlobStore,
    staged: StagedFile,
    filename: str | None,
    title: str | None,
    section: str | None = None,
) -> AddResult:
    """Register an uploaded file. The same content is never stored twice: a
    duplicate returns the existing source. Always consumes ``staged``."""
    filename = clean_filename(filename)
    try:
        fmt = await asyncio.to_thread(detect_format, staged, filename)
        existing = await _source_for_sha(db, staged.sha256)
        if existing is not None:
            return AddResult(existing, duplicate=True)

        key = await asyncio.to_thread(store.commit, staged)
        source = Source(
            title=(title or "").strip()[:500] or _default_title(filename),
            kind=fmt.kind,
            status=SourceStatus.uploaded,
        )
        version = SourceVersion(
            source=source,
            sha256=staged.sha256,
            storage_key=key,
            original_filename=filename,
            mime=fmt.mime,
            size_bytes=staged.size,
        )
        db.add_all([source, version])
        await db.flush()
        from kos.library import sections

        if section:  # uploaded from a section of the library
            await sections.add(db, source.id, section, origin="user")
        else:
            await sections.add(db, source.id, sections.suggest(fmt.kind, fmt.mime))
        db.add(extract_job(version.id))
        db.add(
            AuditLog(
                actor="user",
                action="source.added",
                subject_table="sources",
                subject_id=str(source.id),
                after={"title": source.title, "sha256": staged.sha256, "mime": fmt.mime},
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            # A concurrent upload of the same file won the race.
            await db.rollback()
            existing = await _source_for_sha(db, staged.sha256)
            if existing is None:
                raise
            return AddResult(existing, duplicate=True)
        return AddResult(await get_source(db, source.id), duplicate=False)
    finally:
        staged.discard()


EDITABLE = ("title", "authors_text", "language", "url", "notes")


async def update_metadata(db: AsyncSession, source: Source, changes: dict[str, Any]) -> Source:
    before = {f: getattr(source, f) for f in EDITABLE if f in changes}
    for field, value in changes.items():
        if field not in EDITABLE:
            raise ValueError(f"field {field!r} is not editable")
        setattr(source, field, value)
    source.updated_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor="user",
            action="source.updated",
            subject_table="sources",
            subject_id=str(source.id),
            before=before,
            after={f: getattr(source, f) for f in before},
        )
    )
    await db.commit()
    return source


async def set_archived(db: AsyncSession, source: Source, archived: bool) -> Source:
    if archived and source.status != SourceStatus.archived:
        source.status_before_archive = source.status.value
        source.status = SourceStatus.archived
    elif not archived and source.status == SourceStatus.archived:
        source.status = SourceStatus(source.status_before_archive or SourceStatus.uploaded)
        source.status_before_archive = None
    else:
        return source
    source.updated_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor="user",
            action="source.archived" if archived else "source.restored",
            subject_table="sources",
            subject_id=str(source.id),
        )
    )
    await db.commit()
    return source


class AlreadyProcessing(Exception):
    pass


class CitedSegments(Exception):
    """Reprocessing would delete segments that knowledge cites as evidence."""


async def reprocess(db: AsyncSession, source: Source) -> None:
    """Queue extraction again for the latest version (after a failure, or with a
    newer extractor). Existing pages and segments are replaced, never duplicated."""
    if source.status == SourceStatus.archived:
        raise ValueError("restore the source before reprocessing it")
    if not source.versions:
        # A YouTube source whose download failed: try fetching it again.
        from kos.media import youtube

        if not source.url:
            raise ValueError("la fuente no tiene contenido")
        pending = await db.scalar(
            select(Job.id).where(
                Job.kind == youtube.FETCH_KIND,
                Job.payload["source_id"].astext == str(source.id),
                Job.status.in_([JobStatus.pending, JobStatus.processing]),
            )
        )
        if pending:
            raise AlreadyProcessing()
        source.status = SourceStatus.uploaded
        db.add(youtube.job(source.id))
        await db.commit()
        return
    version = source.versions[-1]
    if await extraction_in_flight(db, version.id):
        raise AlreadyProcessing()
    from kos.knowledge.service import segments_cited

    if await segments_cited(db, version.id):
        raise CitedSegments()
    db.add(extract_job(version.id))
    db.add(
        AuditLog(
            actor="user",
            action="source.reprocess",
            subject_table="sources",
            subject_id=str(source.id),
        )
    )
    await db.commit()


async def refetch_youtube(db: AsyncSession, source: Source) -> None:
    """Download a YouTube video again (e.g. after captions in the wrong language).
    The files already stored are kept; their fragments are derived data and are
    replaced by the new download's, unless knowledge cites them."""
    from kos.knowledge.service import segments_cited
    from kos.media import youtube

    if not (source.url or "").startswith("https://www.youtube.com/"):
        raise ValueError("solo para vídeos de YouTube")
    if source.status == SourceStatus.archived:
        raise ValueError("restaura la fuente antes de volver a descargarla")
    busy = await db.scalar(
        select(Job.id).where(
            Job.kind == youtube.FETCH_KIND,
            Job.payload["source_id"].astext == str(source.id),
            Job.status.in_([JobStatus.pending, JobStatus.processing]),
        )
    )
    if busy or any([await extraction_in_flight(db, v.id) for v in source.versions]):
        raise AlreadyProcessing()
    for v in source.versions:
        if await segments_cited(db, v.id):
            raise CitedSegments()
    ids = [v.id for v in source.versions]
    if ids:
        await db.execute(delete(SourceSegment).where(SourceSegment.source_version_id.in_(ids)))
        await db.execute(delete(SourcePage).where(SourcePage.source_version_id.in_(ids)))
    source.status = SourceStatus.uploaded
    job = youtube.job(source.id)
    job.payload = {**job.payload, "refetch": True}
    db.add(job)
    db.add(
        AuditLog(
            actor="user",
            action="source.youtube_refetch",
            subject_table="sources",
            subject_id=str(source.id),
            after={"previous_versions": [str(i) for i in ids]},
        )
    )
    await db.commit()
