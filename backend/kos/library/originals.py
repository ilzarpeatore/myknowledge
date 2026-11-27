"""The stored originals the daily backup leaves out, and the ones missing from the store.

Files the user uploaded are theirs: they keep a copy, so the backup only keeps
what was extracted from them (in the database). If the server is lost, uploading
the same file again puts its original back (see ``service.add_upload``).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.library.blobstore import LocalBlobStore
from kos.models import Source, SourceVersion

NOT_BACKED_UP = "upload"


async def _rows(db: AsyncSession, origin: str | None = None) -> list[dict[str, Any]]:
    q = select(SourceVersion, Source.title).join(Source, Source.id == SourceVersion.source_id)
    if origin:
        q = q.where(SourceVersion.origin == origin)
    rows = (await db.execute(q.order_by(SourceVersion.created_at))).all()
    return [
        {
            "key": v.storage_key,
            "size_bytes": v.size_bytes,
            "filename": v.original_filename or "",
            "title": title,
            "origin": v.origin,
            "source_id": str(v.source_id),
        }
        for v, title in rows
    ]


async def excluded(db: AsyncSession) -> list[dict[str, Any]]:
    """Originals left out of the backup: everything the user uploaded."""
    return await _rows(db, NOT_BACKED_UP)


async def missing(db: AsyncSession, store: LocalBlobStore) -> list[dict[str, Any]]:
    """Versions whose original is not in the store (after restoring a backup)."""
    return [r for r in await _rows(db) if not store.exists(r["key"])]


def tsv(rows: list[dict[str, Any]]) -> str:
    """One line per file: key, size, file name, title (tabs and newlines removed)."""

    def clean(x: Any) -> str:
        return " ".join(str(x).split())

    cols = ("key", "size_bytes", "filename", "title")
    return "".join("\t".join(clean(r[c]) for c in cols) + "\n" for r in rows)
