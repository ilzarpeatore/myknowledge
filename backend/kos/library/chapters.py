"""Chapters of a book or long document: page ranges of a version, as a tree
(chapter → points), so it can be read, summarised and studied by parts.

Where they come from, best first:
1. the PDF bookmarks (exact pages, free);
2. the printed index (read with AI) and the headings found while extracting — see
   ``chapter_index``;
3. by hand: every chapter can be renamed, moved, split or marked as not content
   (cover, rights, index, bibliography), and a detection never overwrites a list the
   user edited unless asked to.
"""

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.models import (
    AuditLog,
    Job,
    JobStatus,
    SourceChapter,
    SourceSegment,
    SourceVersion,
)

DETECT_KIND = "source.chapters"
MAX_LEVEL = 2  # chapter and point: deeper bookmarks are folded into their point
NOT_CONTENT = re.compile(
    r"^\s*((el|la|los|las|the)\s+)?(portada|cubierta|contraportada|cover|t[ií]tulo|"
    r"title page|cr[eé]ditos|derechos|copyright|[ií]ndice|contenidos?|sumario|"
    r"table of contents|contents|"
    r"bibliograf[ií]a|referencias( bibliogr[aá]ficas)?|references|agradecimientos|"
    r"acknowledg(e)?ments|[ií]ndice (alfab[eé]tico|anal[ií]tico|tem[aá]tico)|"
    r"sobre el autor|about the author|notas?)\b",
    re.IGNORECASE,
)
PAGE_LIKE = re.compile(r"^\s*(p[aá]g(ina)?|page|p\.)\s*\d+\s*$", re.IGNORECASE)


class ChapterError(Exception):
    pass


@dataclass
class Detected:
    level: int  # 1 chapter, 2 point
    title: str
    page_start: int
    page_end: int = 0
    is_content: bool = True


def is_content_title(title: str) -> bool:
    return not NOT_CONTENT.match(title)


def with_ranges(items: list[Detected], page_count: int) -> list[Detected]:
    """Fill ``page_end`` (inclusive): a chapter ends where the next one of the same or
    a higher level starts (or on that same page if it starts there too); a point never
    goes past its chapter. Items are in reading order."""
    out = [i for i in items if 1 <= i.page_start <= max(page_count, 1)]
    for n, it in enumerate(out):
        nxt = next((o for o in out[n + 1 :] if o.level <= it.level), None)
        end = nxt.page_start - 1 if nxt and nxt.page_start > it.page_start else it.page_start
        it.page_end = max(it.page_start, end if nxt else page_count)
        if it.level > 1:
            parent = next((o for o in reversed(out[:n]) if o.level < it.level), None)
            if parent is not None:
                it.page_end = min(it.page_end, max(parent.page_end, it.page_start))
    return out


def read_bookmarks(path: Path) -> tuple[list[Detected], int]:
    """Blocking: the PDF outline as chapters (level 1) and points (level 2)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        page_count = len(doc)
        items: list[Detected] = []
        for b in doc.get_toc(max_depth=8):
            dest = b.get_dest()
            index = dest.get_index() if dest is not None else None
            title = re.sub(r"\s+", " ", (b.get_title() or "")).strip()
            if index is None or not title:
                continue
            if b.level + 1 > MAX_LEVEL:
                continue  # sub-sub-points stay inside their point
            items.append(Detected(b.level + 1, title[:500], index + 1))
    finally:
        doc.close()
    # Outlines made of «Página 1, Página 2…» say nothing about the structure.
    if items and sum(bool(PAGE_LIKE.match(i.title)) for i in items) > len(items) / 2:
        items = []
    if len({(i.title, i.page_start) for i in items}) < 2:
        items = []
    for i in items:
        i.is_content = is_content_title(i.title)
    return items, page_count


async def chapters_of(db: AsyncSession, version_id: uuid.UUID) -> list[SourceChapter]:
    return list(
        (
            await db.scalars(
                select(SourceChapter)
                .where(SourceChapter.source_version_id == version_id)
                .order_by(SourceChapter.position)
            )
        ).all()
    )


async def save_detected(
    db: AsyncSession,
    version_id: uuid.UUID,
    items: list[Detected],
    origin: str,
    *,
    force: bool = False,
) -> int:
    """Replace the detected chapters of a version. A list the user edited is kept
    unless ``force`` (the summaries of replaced chapters go with them)."""
    if not force and await db.scalar(
        select(SourceChapter.id)
        .where(SourceChapter.source_version_id == version_id, SourceChapter.edited.is_(True))
        .limit(1)
    ):
        return 0
    await db.execute(delete(SourceChapter).where(SourceChapter.source_version_id == version_id))
    parent: SourceChapter | None = None
    for pos, it in enumerate(items):
        row = SourceChapter(
            source_version_id=version_id,
            parent_id=parent.id if it.level > 1 and parent is not None else None,
            position=pos,
            level=it.level if parent is not None or it.level == 1 else 1,
            title=it.title,
            page_start=it.page_start,
            page_end=it.page_end,
            is_content=it.is_content,
            origin=origin,
        )
        row.id = uuid.uuid4()
        db.add(row)
        if row.level == 1:
            parent = row
    await db.flush()
    return len(items)


def detect_job(version_id: uuid.UUID, force: bool = False) -> Job:
    return Job(
        kind=DETECT_KIND,
        status=JobStatus.pending,
        max_attempts=2,
        payload={"source_version_id": str(version_id), "force": force},
    )


async def detecting(db: AsyncSession, version_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(Job.id)
            .where(
                Job.kind.in_([DETECT_KIND, "source.extract"]),
                Job.payload["source_version_id"].astext == str(version_id),
                Job.status.in_([JobStatus.pending, JobStatus.processing]),
            )
            .limit(1)
        )
    )


async def segments_of(db: AsyncSession, ch: SourceChapter) -> list[SourceSegment]:
    return list(
        (
            await db.scalars(
                select(SourceSegment)
                .where(
                    SourceSegment.source_version_id == ch.source_version_id,
                    SourceSegment.loc_page_start >= ch.page_start,
                    SourceSegment.loc_page_start <= ch.page_end,
                )
                .order_by(SourceSegment.ordinal)
            )
        ).all()
    )


async def tokens_of(db: AsyncSession, ch: SourceChapter) -> int:
    return int(
        await db.scalar(
            select(func.coalesce(func.sum(SourceSegment.token_count), 0)).where(
                SourceSegment.source_version_id == ch.source_version_id,
                SourceSegment.loc_page_start >= ch.page_start,
                SourceSegment.loc_page_start <= ch.page_end,
            )
        )
        or 0
    )


# ---- editing --------------------------------------------------------------------------


async def _page_count(db: AsyncSession, version_id: uuid.UUID) -> int:
    v = await db.get(SourceVersion, version_id)
    return (v.page_count if v else None) or 1


def _check_pages(start: int, end: int, page_count: int) -> None:
    if start < 1 or end < start:
        raise ChapterError("las páginas no son válidas: la final no puede ser anterior")
    if end > page_count:
        raise ChapterError(f"el documento tiene {page_count} páginas")


async def _renumber(db: AsyncSession, version_id: uuid.UUID) -> None:
    """Reading order: by first page, chapters before their points, then as they were."""
    rows = await chapters_of(db, version_id)
    tops = sorted(
        (r for r in rows if r.parent_id is None), key=lambda r: (r.page_start, r.position)
    )
    kids: dict[uuid.UUID, list[SourceChapter]] = {}
    for r in rows:
        if r.parent_id is not None:
            kids.setdefault(r.parent_id, []).append(r)
    pos = 0
    for t in tops:
        t.position, pos = pos, pos + 1
        for k in sorted(kids.get(t.id, []), key=lambda r: (r.page_start, r.position)):
            k.position, pos = pos, pos + 1


def _audit(action: str, ch: SourceChapter, before: dict | None, after: dict | None) -> AuditLog:
    return AuditLog(
        actor="user",
        action=action,
        subject_table="source_chapters",
        subject_id=str(ch.id),
        before=before,
        after=after,
    )


def _state(ch: SourceChapter) -> dict[str, Any]:
    return {
        "title": ch.title,
        "page_start": ch.page_start,
        "page_end": ch.page_end,
        "is_content": ch.is_content,
        "parent_id": str(ch.parent_id) if ch.parent_id else None,
    }


async def create(
    db: AsyncSession,
    version_id: uuid.UUID,
    *,
    title: str,
    page_start: int,
    page_end: int,
    parent_id: uuid.UUID | None = None,
    is_content: bool = True,
) -> SourceChapter:
    title = title.strip()
    if not title:
        raise ChapterError("falta el título")
    _check_pages(page_start, page_end, await _page_count(db, version_id))
    level = 1
    if parent_id is not None:
        parent = await db.get(SourceChapter, parent_id)
        if parent is None or parent.source_version_id != version_id or parent.parent_id:
            raise ChapterError("el capítulo padre no es válido")
        level = 2
    ch = SourceChapter(
        id=uuid.uuid4(),
        source_version_id=version_id,
        parent_id=parent_id,
        position=10**6,
        level=level,
        title=title[:500],
        page_start=page_start,
        page_end=page_end,
        is_content=is_content,
        origin="manual",
        edited=True,
    )
    db.add(ch)
    await db.flush()
    await _mark_edited(db, version_id)
    await _renumber(db, version_id)
    db.add(_audit("chapter.created", ch, None, _state(ch)))
    await db.commit()
    return ch


async def _mark_edited(db: AsyncSession, version_id: uuid.UUID) -> None:
    """One edit protects the whole list from being replaced by a new detection."""
    for r in await chapters_of(db, version_id):
        r.edited = True


async def update(db: AsyncSession, ch: SourceChapter, changes: dict[str, Any]) -> SourceChapter:
    before = _state(ch)
    if "title" in changes:
        title = (changes["title"] or "").strip()
        if not title:
            raise ChapterError("falta el título")
        ch.title = title[:500]
    start = changes.get("page_start", ch.page_start)
    end = changes.get("page_end", ch.page_end)
    _check_pages(start, end, await _page_count(db, ch.source_version_id))
    ch.page_start, ch.page_end = start, end
    if "is_content" in changes:
        ch.is_content = bool(changes["is_content"])
    ch.updated_at = datetime.now(UTC)
    await _mark_edited(db, ch.source_version_id)
    await _renumber(db, ch.source_version_id)
    db.add(_audit("chapter.updated", ch, before, _state(ch)))
    await db.commit()
    return ch


async def remove(db: AsyncSession, ch: SourceChapter) -> None:
    """Delete a chapter; its points move up to the top level (they are not lost)."""
    version_id = ch.source_version_id
    for k in (
        await db.scalars(select(SourceChapter).where(SourceChapter.parent_id == ch.id))
    ).all():
        k.parent_id, k.level = None, 1
    await db.flush()
    db.add(_audit("chapter.deleted", ch, _state(ch), None))
    await db.delete(ch)
    await db.flush()
    await _mark_edited(db, version_id)
    await _renumber(db, version_id)
    await db.commit()


# ---- the detection job ------------------------------------------------------------------


async def detect(db: AsyncSession, version: SourceVersion, force: bool = False) -> dict[str, Any]:
    """Detect and save the chapters of a version. Returns what was found and how."""
    from kos.library.blobstore import get_store

    if version.mime != "application/pdf":
        return {"chapters": 0, "origin": None, "reason": "solo PDF por ahora"}
    path = Path(get_store().path_for(version.storage_key))
    items, page_count = await asyncio.to_thread(read_bookmarks, path)
    if not items:
        return {"chapters": 0, "origin": None, "reason": "el PDF no trae marcadores"}
    items = with_ranges(items, version.page_count or page_count)
    saved = await save_detected(db, version.id, items, "bookmarks", force=force)
    if saved == 0 and items:
        return {"chapters": 0, "origin": "bookmarks", "reason": "editados a mano: no se tocan"}
    return {"chapters": saved, "origin": "bookmarks"}


@handler(DETECT_KIND)
async def detect_chapters(ctx: JobContext) -> dict[str, Any]:
    try:
        version_id = uuid.UUID(ctx.payload["source_version_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None
    async with ctx.sessionmaker() as db:
        version = await db.get(SourceVersion, version_id)
        if version is None:
            raise PermanentJobError("versión no encontrada")
        result = await detect(db, version, force=bool(ctx.payload.get("force")))
        db.add(
            AuditLog(
                actor="worker",
                action="source.chapters_detected",
                subject_table="sources",
                subject_id=str(version.source_id),
                after={"version_id": str(version_id), **result},
            )
        )
        await db.commit()
        return result
