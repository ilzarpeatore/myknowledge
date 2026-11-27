"""Bibliographic references found in the library (Referencias)."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from kos.api.deps import DbDep, UserDep
from kos.evidence import service as ev
from kos.evidence.doi import DOIError
from kos.models import Reference, Source
from kos.references import service as refs

router = APIRouter(prefix="/api", tags=["references"])


async def _get(db, ref_id: uuid.UUID) -> Reference:
    ref = await db.get(Reference, ref_id)
    if ref is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "referencia no encontrada")
    return ref


@router.get("/references")
async def list_references(
    db: DbDep,
    _: UserDep,
    q: str | None = Query(default=None, max_length=200),
    status_: Literal["active", "dismissed"] = Query(default="active", alias="status"),
    linked: bool | None = None,
    section: str | None = Query(default=None, max_length=40),
    source_id: uuid.UUID | None = None,
    sort: Literal["mentions", "sources", "year", "author"] = "mentions",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await refs.listing(
        db,
        q=q,
        status=status_,
        linked=linked,
        section=section,
        source_id=source_id,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get("/references/{ref_id}")
async def get_reference(ref_id: uuid.UUID, db: DbDep, _: UserDep):
    return await refs.detail(db, await _get(db, ref_id))


class ReferencePatch(BaseModel):
    title: str | None = Field(default=None, max_length=2000)
    authors_text: str | None = Field(default=None, max_length=2000)
    first_author: str | None = Field(default=None, max_length=300)
    year: int | None = Field(default=None, ge=1500, le=2100)
    doi: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    status: Literal["active", "dismissed"] | None = None


@router.patch("/references/{ref_id}")
async def patch_reference(ref_id: uuid.UUID, body: ReferencePatch, db: DbDep, _: UserDep):
    ref = await _get(db, ref_id)
    try:
        await refs.update_fields(db, ref, body.model_dump(exclude_unset=True))
    except (refs.ReferenceError_, DOIError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return await refs.detail(db, ref)


class LinkIn(BaseModel):
    publication_id: uuid.UUID | None


@router.put("/references/{ref_id}/publication")
async def link_publication(ref_id: uuid.UUID, body: LinkIn, db: DbDep, _: UserDep):
    ref = await _get(db, ref_id)
    try:
        await refs.link(db, ref, body.publication_id)
    except refs.ReferenceError_ as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return await refs.detail(db, ref)


@router.post("/references/{ref_id}/publication", status_code=status.HTTP_201_CREATED)
async def create_publication(ref_id: uuid.UUID, db: DbDep, _: UserDep):
    """Add it to Estudios (Crossref when it has a DOI) and link it."""
    ref = await _get(db, ref_id)
    try:
        pub = await refs.create_publication(db, ref)
    except (refs.ReferenceError_, ev.EvidenceError, DOIError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return {"publication_id": str(pub.id), **(await refs.detail(db, ref))}


class MergeIn(BaseModel):
    into_id: uuid.UUID


@router.post("/references/{ref_id}/merge")
async def merge_reference(ref_id: uuid.UUID, body: MergeIn, db: DbDep, _: UserDep):
    src, into = await _get(db, ref_id), await _get(db, body.into_id)
    try:
        await refs.merge(db, src, into)
    except refs.ReferenceError_ as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    return await refs.detail(db, into)


@router.post("/references/rescan", status_code=status.HTTP_202_ACCEPTED)
async def rescan(db: DbDep, _: UserDep):
    """Look for references again in every source (e.g. after an update)."""
    return {"queued": await refs.rescan_all(db)}


@router.get("/sources/{source_id}/references")
async def source_references(source_id: uuid.UUID, db: DbDep, _: UserDep):
    if await db.get(Source, source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fuente no encontrada")
    return await refs.listing(db, source_id=source_id, limit=200)
