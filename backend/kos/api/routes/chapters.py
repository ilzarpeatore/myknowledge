"""Chapters of a source and their summaries."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from kos.api.deps import DbDep, UserDep
from kos.library import chapter_summaries as sums
from kos.library import chapters as chs
from kos.models import AuditLog, ChapterSummary, Source, SourceChapter, SourceVersion

router = APIRouter(prefix="/api", tags=["chapters"])

Level = Literal["essential", "study", "detailed"]


async def _latest_version(db, source_id: uuid.UUID) -> tuple[Source, SourceVersion | None]:
    source = await db.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fuente no encontrada")
    version = await db.scalar(
        select(SourceVersion)
        .where(SourceVersion.source_id == source_id)
        .order_by(SourceVersion.created_at.desc())
        .limit(1)
    )
    return source, version


async def _chapter(db, chapter_id: uuid.UUID) -> SourceChapter:
    ch = await db.get(SourceChapter, chapter_id)
    if ch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "capítulo no encontrado")
    return ch


def _brief(ch: SourceChapter) -> dict:
    return {
        "id": str(ch.id),
        "parent_id": str(ch.parent_id) if ch.parent_id else None,
        "position": ch.position,
        "level": ch.level,
        "title": ch.title,
        "page_start": ch.page_start,
        "page_end": ch.page_end,
        "pages": ch.page_end - ch.page_start + 1,
        "is_content": ch.is_content,
        "origin": ch.origin,
        "edited": ch.edited,
    }


@router.get("/sources/{source_id}/chapters")
async def list_chapters(source_id: uuid.UUID, db: DbDep, _: UserDep):
    source, version = await _latest_version(db, source_id)
    if version is None:
        return {"version_id": None, "items": [], "detecting": False, "can_detect": False}
    rows = await chs.chapters_of(db, version.id)
    have = (
        await db.execute(
            select(ChapterSummary.chapter_id, ChapterSummary.level)
            .join(SourceChapter, SourceChapter.id == ChapterSummary.chapter_id)
            .where(SourceChapter.source_version_id == version.id)
            .distinct()
        )
    ).all()
    levels: dict[uuid.UUID, set[str]] = {}
    for cid, level in have:
        levels.setdefault(cid, set()).add(level)
    return {
        "version_id": str(version.id),
        "mime": version.mime,
        "page_count": version.page_count,
        "detecting": await chs.detecting(db, version.id),
        "can_detect": version.mime == "application/pdf",
        "items": [{**_brief(r), "summaries": sorted(levels.get(r.id, set()))} for r in rows],
    }


@router.post("/sources/{source_id}/chapters/detect", status_code=status.HTTP_202_ACCEPTED)
async def detect(
    source_id: uuid.UUID,
    db: DbDep,
    _: UserDep,
    force: Annotated[bool, Query()] = False,
):
    source, version = await _latest_version(db, source_id)
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "la fuente aún no tiene archivo")
    if await chs.detecting(db, version.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "ya se están detectando")
    job = chs.detect_job(version.id, force=force)
    db.add(job)
    await db.commit()
    return {"job_id": str(job.id)}


class ChapterIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    parent_id: uuid.UUID | None = None
    is_content: bool = True


@router.post("/sources/{source_id}/chapters", status_code=status.HTTP_201_CREATED)
async def create(source_id: uuid.UUID, body: ChapterIn, db: DbDep, _: UserDep):
    source, version = await _latest_version(db, source_id)
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "la fuente aún no tiene archivo")
    try:
        ch = await chs.create(
            db,
            version.id,
            title=body.title,
            page_start=body.page_start,
            page_end=body.page_end,
            parent_id=body.parent_id,
            is_content=body.is_content,
        )
    except chs.ChapterError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return _brief(ch)


class ChapterPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    is_content: bool | None = None


@router.patch("/chapters/{chapter_id}")
async def update(chapter_id: uuid.UUID, body: ChapterPatch, db: DbDep, _: UserDep):
    ch = await _chapter(db, chapter_id)
    try:
        ch = await chs.update(db, ch, body.model_dump(exclude_unset=True, exclude_none=True))
    except chs.ChapterError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return _brief(ch)


@router.delete("/chapters/{chapter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(chapter_id: uuid.UUID, db: DbDep, _: UserDep):
    await chs.remove(db, await _chapter(db, chapter_id))


def _summary_out(s: ChapterSummary) -> dict:
    return {
        "id": str(s.id),
        "level": s.level,
        "content": s.content,
        "markdown": s.markdown,
        "words": s.words,
        "citations": s.citations,
        "uncited_points": s.uncited_points,
        "origin": s.origin,
        "created_at": s.created_at,
    }


@router.get("/chapters/{chapter_id}")
async def get_chapter(chapter_id: uuid.UUID, db: DbDep, _: UserDep):
    ch = await _chapter(db, chapter_id)
    version = await db.get(SourceVersion, ch.source_version_id)
    source = await db.get(Source, version.source_id)
    siblings = await chs.chapters_of(db, ch.source_version_id)
    idx = next(i for i, r in enumerate(siblings) if r.id == ch.id)
    history = (
        await db.scalars(
            select(ChapterSummary)
            .where(ChapterSummary.chapter_id == ch.id)
            .order_by(ChapterSummary.created_at.desc())
        )
    ).all()
    current = await sums.current(db, ch.id)
    return {
        **_brief(ch),
        "source": {"id": str(source.id), "title": source.title},
        "version": {"id": str(version.id), "mime": version.mime},
        "points": [_brief(r) for r in siblings if r.parent_id == ch.id],
        "prev": _brief(siblings[idx - 1]) if idx > 0 else None,
        "next": _brief(siblings[idx + 1]) if idx + 1 < len(siblings) else None,
        "current": {lvl: _summary_out(s) for lvl, s in current.items()},
        "history": [
            {"id": str(s.id), "level": s.level, "words": s.words, "created_at": s.created_at}
            for s in history
        ],
        "estimates": {lvl: await sums.estimate(db, ch, lvl) for lvl in sums.LEVELS},
        "generating": await sums.in_flight(db, ch.id),
        "failed": await sums.last_failures(db, ch.id),
    }


@router.get("/chapters/{chapter_id}/summaries/{summary_id}")
async def get_summary(chapter_id: uuid.UUID, summary_id: uuid.UUID, db: DbDep, _: UserDep):
    s = await db.get(ChapterSummary, summary_id)
    if s is None or s.chapter_id != chapter_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resumen no encontrado")
    return _summary_out(s)


class SummarizeIn(BaseModel):
    level: Level = "study"


@router.post("/chapters/{chapter_id}/summaries", status_code=status.HTTP_202_ACCEPTED)
async def summarize(chapter_id: uuid.UUID, body: SummarizeIn, db: DbDep, _: UserDep):
    ch = await _chapter(db, chapter_id)
    if body.level in await sums.in_flight(db, ch.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "ya se está generando ese resumen")
    est = await sums.estimate(db, ch, body.level)
    if est["tokens"] == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "el capítulo no tiene texto en esas páginas")
    if est["too_long"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "el capítulo es demasiado largo para resumirlo de una vez: divídelo en puntos",
        )
    job = sums.summarize_job(ch.id, body.level)
    db.add(job)
    db.add(
        AuditLog(
            actor="user",
            action="chapter.summary_requested",
            subject_table="source_chapters",
            subject_id=str(ch.id),
            after={"level": body.level, "usd_estimate": est["usd"]},
        )
    )
    await db.commit()
    return {"job_id": str(job.id), "estimate": est}
