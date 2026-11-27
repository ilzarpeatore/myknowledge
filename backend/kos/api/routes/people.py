"""The Autores library: search as you type, create, edit, merge duplicates, see an
author's sources. (An author's stance by concept is in /persons/{id}/profile.)"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from kos.api.deps import DbDep, UserDep
from kos.library import authors
from kos.models import Claim, Course, Person, Program, SourceAuthor

router = APIRouter(prefix="/api", tags=["people"])
Role = Literal["teacher", "researcher", "author"]


def _bad(exc: authors.AuthorError):
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


async def _person(db, person_id: uuid.UUID) -> Person:
    p = await db.get(Person, person_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "persona no encontrada")
    return p


@router.get("/persons")
async def list_persons(
    db: DbDep,
    _: UserDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    role: Role | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """People by name, or ordered by how well they match ``q`` (for autocomplete)."""
    items, _total = await authors.search(db, q, role, limit, offset)
    return items


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    role: Role | None = None
    affiliation: str | None = Field(default=None, max_length=1000)


@router.post("/persons", status_code=status.HTTP_201_CREATED)
async def create_person(body: PersonIn, db: DbDep, _: UserDep):
    """Creates a person, or returns the one with the same name (``existing``: true)."""
    try:
        p, created = await authors.create(db, body.name, body.role, body.affiliation)
    except authors.AuthorError as exc:
        _bad(exc)
    await db.commit()
    return {**authors.person_out(p), "existing": not created}


@router.get("/persons/{person_id}")
async def get_person(person_id: uuid.UUID, db: DbDep, _: UserDep):
    return await authors.detail(db, await _person(db, person_id))


class PersonPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    role: Role | None = None
    affiliation: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=5000)


@router.patch("/persons/{person_id}")
async def patch_person(person_id: uuid.UUID, body: PersonPatch, db: DbDep, _: UserDep):
    p = await _person(db, person_id)
    try:
        await authors.update_person(db, p, body.model_dump(exclude_unset=True))
    except authors.AuthorError as exc:
        _bad(exc)
    await db.commit()
    return authors.person_out(p)


class MergeIn(BaseModel):
    into: uuid.UUID


@router.post("/persons/{person_id}/merge")
async def merge_person(person_id: uuid.UUID, body: MergeIn, db: DbDep, _: UserDep):
    """Merge this person (a duplicate) into ``into``; this one disappears."""
    dup, keep = await _person(db, person_id), await _person(db, body.into)
    try:
        moved = await authors.merge(db, dup, keep)
    except authors.AuthorError as exc:
        _bad(exc)
    await db.commit()
    return {"into": authors.person_out(keep), **moved}


@router.delete("/persons/{person_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_person(person_id: uuid.UUID, db: DbDep, _: UserDep):
    """Only someone created by mistake: with sources, claims or courses, merge instead."""
    p = await _person(db, person_id)
    used = 0
    for stmt in (
        select(func.count()).select_from(SourceAuthor).where(SourceAuthor.person_id == p.id),
        select(func.count()).select_from(Claim).where(Claim.attributed_person_id == p.id),
        select(func.count()).select_from(Course).where(Course.teacher_person_id == p.id),
        select(func.count()).select_from(Program).where(Program.teacher_person_id == p.id),
    ):
        used += await db.scalar(stmt) or 0
    if used:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "tiene fuentes, afirmaciones o cursos: quítaselos o fusiónala con otra persona",
        )
    from kos.models import AuditLog

    db.add(
        AuditLog(
            actor="user",
            action="person.deleted",
            subject_table="persons",
            subject_id=str(p.id),
            before={"name": p.name},
        )
    )
    await db.delete(p)
    await db.commit()
