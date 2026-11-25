"""Library operations: accept an upload, deduplicate, register the source."""

import asyncio
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from kos.ingestion.pipeline import extract_job, extraction_in_flight
from kos.library.blobstore import LocalBlobStore, StagedFile
from kos.models import AuditLog, Source, SourceKind, SourceStatus, SourceVersion


class UnsupportedFile(Exception):
    pass


@dataclass(frozen=True)
class DetectedFormat:
    mime: str
    kind: SourceKind


TEXT_EXTENSIONS = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain"}


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
    if ext in TEXT_EXTENSIONS:
        data = staged.path.read_bytes()
        if b"\x00" in data:
            raise UnsupportedFile("el archivo de texto contiene datos binarios")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise UnsupportedFile("los archivos de texto deben estar en UTF-8") from None
        return DetectedFormat(TEXT_EXTENSIONS[ext], SourceKind.document)
    raise UnsupportedFile("formato no admitido (por ahora: PDF, Markdown y TXT)")


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


async def reprocess(db: AsyncSession, source: Source) -> None:
    """Queue extraction again for the latest version (after a failure, or with a
    newer extractor). Existing pages and segments are replaced, never duplicated."""
    if source.status == SourceStatus.archived:
        raise ValueError("restore the source before reprocessing it")
    version = source.versions[-1]
    if await extraction_in_flight(db, version.id):
        raise AlreadyProcessing()
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
