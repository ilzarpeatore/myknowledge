"""Library sections and crossing between them."""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from kos.api.deps import DbDep, UserDep
from kos.library import sections as secs
from kos.library import service as library
from kos.models import LibrarySection, SourceLink

router = APIRouter(prefix="/api", tags=["sections"])


def _bad(exc: Exception):
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.get("/sections")
async def list_sections(db: DbDep, _: UserDep):
    counts = await secs.counts(db)
    rows = (await db.scalars(select(LibrarySection).order_by(LibrarySection.position))).all()
    return [
        {
            "key": s.key,
            "name": s.name,
            "description": s.description,
            "builtin": s.builtin,
            "count": counts.get(s.key, 0),
        }
        for s in rows
    ]


class SectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


@router.post("/sections", status_code=status.HTTP_201_CREATED)
async def create_section(body: SectionIn, db: DbDep, _: UserDep):
    try:
        s = await secs.create_section(db, body.name, body.description)
    except secs.SectionError as exc:
        _bad(exc)
    return {"key": s.key, "name": s.name}


class SectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    position: int | None = Field(default=None, ge=0, le=1000)


@router.patch("/sections/{key}")
async def patch_section(key: str, body: SectionPatch, db: DbDep, _: UserDep):
    s = await db.get(LibrarySection, key)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sección no encontrada")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    await db.commit()
    return {"key": s.key, "name": s.name, "description": s.description, "position": s.position}


@router.delete("/sections/{key}")
async def delete_section(key: str, db: DbDep, _: UserDep):
    s = await db.get(LibrarySection, key)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sección no encontrada")
    try:
        moved = await secs.delete_section(db, s)
    except secs.SectionError as exc:
        _bad(exc)
    return {"moved_to_recursos": moved}


async def _source(db, source_id):
    s = await library.get_source(db, source_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fuente no encontrada")
    return s


class SourceSectionsIn(BaseModel):
    sections: list[str] = Field(min_length=1, max_length=20)


@router.put("/sources/{source_id}/sections")
async def put_sections(source_id: uuid.UUID, body: SourceSectionsIn, db: DbDep, _: UserDep):
    s = await _source(db, source_id)
    try:
        await secs.set_sections(db, s, body.sections)
    except secs.SectionError as exc:
        _bad(exc)
    return {"sections": sorted(r.section_key for r in s.section_rows)}


@router.get("/sources/{source_id}/related")
async def related(source_id: uuid.UUID, db: DbDep, _: UserDep):
    return await secs.related(db, await _source(db, source_id))


class LinkIn(BaseModel):
    other_id: uuid.UUID
    note: str | None = Field(default=None, max_length=1000)


@router.post("/sources/{source_id}/links", status_code=status.HTTP_201_CREATED)
async def add_link(source_id: uuid.UUID, body: LinkIn, db: DbDep, _: UserDep):
    await _source(db, source_id)
    await _source(db, body.other_id)
    try:
        lk = await secs.link(db, source_id, body.other_id, body.note)
    except secs.SectionError as exc:
        _bad(exc)
    return {"id": str(lk.id)}


@router.delete("/source-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(link_id: uuid.UUID, db: DbDep, _: UserDep):
    lk = await db.get(SourceLink, link_id)
    if lk is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "enlace no encontrado")
    await db.delete(lk)
    await db.commit()


@router.get("/entities/{entity_id}/sources")
async def concept_sources(entity_id: uuid.UUID, db: DbDep, _: UserDep):
    """Where a concept appears, grouped by section."""
    return await secs.concept_sources(db, entity_id)
