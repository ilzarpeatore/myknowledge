"""Studies (publications), their link to sources, and the quote audit."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from kos.api.deps import DbDep, UserDep
from kos.evidence import doi as doi_mod
from kos.evidence import service as ev
from kos.library import service as library
from kos.models import Claim, ClaimEntity, Entity, Publication

router = APIRouter(prefix="/api", tags=["evidence"])


def _fail(exc: Exception):
    if isinstance(exc, ev.DuplicateDOI):
        return JSONResponse(
            status_code=409,
            content={
                "detail": str(exc),
                "existing": {"id": str(exc.existing.id), "title": exc.existing.title},
            },
        )
    code = {
        doi_mod.InvalidDOI: 400,
        doi_mod.DOINotFound: 404,
        doi_mod.RegistryUnavailable: 502,
        ev.EvidenceError: 400,
    }.get(type(exc), 400)
    raise HTTPException(code, str(exc)) from None


async def _pub(db, pub_id: uuid.UUID) -> Publication:
    pub = await db.get(Publication, pub_id)
    if pub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "estudio no encontrado")
    return pub


@router.get("/publications")
async def list_publications(db: DbDep, _: UserDep, q: str | None = Query(None, max_length=200)):
    stmt = select(Publication).order_by(Publication.year.desc().nulls_last(), Publication.title)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Publication.title.ilike(like),
                Publication.authors_text.ilike(like),
                Publication.doi.ilike(like),
            )
        )
    return [await ev.summary(db, p) for p in (await db.scalars(stmt.limit(200))).all()]


class DOIIn(BaseModel):
    doi: str = Field(min_length=3, max_length=300)


@router.post("/publications/lookup")
async def lookup(body: DOIIn, db: DbDep, _: UserDep):
    """Preview Crossref metadata for a DOI without saving anything."""
    try:
        doi = doi_mod.normalize_doi(body.doi)
        existing = await ev.by_doi(db, doi)
        meta = await doi_mod.fetch(doi)
    except doi_mod.DOIError as exc:
        return _fail(exc)
    return {"metadata": meta.as_dict(), "existing_id": str(existing.id) if existing else None}


@router.post("/publications/from-doi", status_code=status.HTTP_201_CREATED)
async def create_from_doi(body: DOIIn, db: DbDep, _: UserDep):
    try:
        pub = await ev.create_from_doi(db, body.doi)
    except (doi_mod.DOIError, ev.EvidenceError) as exc:
        return _fail(exc)
    return await ev.summary(db, pub)


class PublicationIn(BaseModel):
    title: str = Field(min_length=1, max_length=2000)
    year: int | None = Field(default=None, ge=1500, le=2100)
    doi: str | None = Field(default=None, max_length=300)
    journal: str | None = Field(default=None, max_length=500)
    authors_text: str | None = Field(default=None, max_length=5000)
    abstract: str | None = Field(default=None, max_length=20000)
    url: str | None = Field(default=None, max_length=500)
    study_type: str | None = Field(default=None, max_length=100)
    design: str | None = Field(default=None, max_length=5000)
    population: str | None = Field(default=None, max_length=5000)
    sample_size: int | None = Field(default=None, ge=0)
    variables: str | None = Field(default=None, max_length=5000)
    results: str | None = Field(default=None, max_length=10000)
    limitations: str | None = Field(default=None, max_length=10000)


@router.post("/publications", status_code=status.HTTP_201_CREATED)
async def create_publication(body: PublicationIn, db: DbDep, _: UserDep):
    try:
        pub = await ev.create(db, body.model_dump(exclude_none=True))
    except (doi_mod.DOIError, ev.EvidenceError) as exc:
        return _fail(exc)
    return await ev.summary(db, pub)


@router.get("/publications/{pub_id}")
async def get_publication(pub_id: uuid.UUID, db: DbDep, _: UserDep):
    pub = await _pub(db, pub_id)
    out = await ev.summary(db, pub)
    claims = (
        await db.scalars(
            select(Claim).where(Claim.publication_id == pub.id).order_by(Claim.created_at)
        )
    ).all()
    out["claim_list"] = []
    for c in claims:
        ents = (
            await db.execute(
                select(Entity.slug, Entity.canonical_name)
                .join(ClaimEntity, ClaimEntity.entity_id == Entity.id)
                .where(ClaimEntity.claim_id == c.id)
            )
        ).all()
        out["claim_list"].append(
            {
                "id": str(c.id),
                "text": c.text,
                "claim_type": c.claim_type,
                "review_status": c.review_status,
                "origin": c.origin,
                "entities": [{"slug": s, "name": n} for s, n in ents],
            }
        )
    return out


class PublicationPatch(PublicationIn):
    title: str | None = Field(default=None, min_length=1, max_length=2000)
    methodological_quality: Literal["not_assessed", "low", "moderate", "high"] | None = None
    quality_notes: str | None = Field(default=None, max_length=10000)


@router.patch("/publications/{pub_id}")
async def patch_publication(pub_id: uuid.UUID, body: PublicationPatch, db: DbDep, _: UserDep):
    pub = await _pub(db, pub_id)
    try:
        await ev.update_fields(db, pub, body.model_dump(exclude_unset=True))
    except (doi_mod.DOIError, ev.EvidenceError) as exc:
        return _fail(exc)
    return await ev.summary(db, pub)


class SourcePublicationIn(BaseModel):
    publication_id: uuid.UUID | None


@router.put("/sources/{source_id}/publication")
async def link_source(source_id: uuid.UUID, body: SourcePublicationIn, db: DbDep, _: UserDep):
    source = await library.get_source(db, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fuente no encontrada")
    try:
        changed = await ev.link_source(db, source, body.publication_id)
    except ev.EvidenceError as exc:
        return _fail(exc)
    return {
        "publication_id": str(body.publication_id) if body.publication_id else None,
        "claims_changed": changed,
    }


@router.get("/evidence/audit")
async def quote_audit(db: DbDep, _: UserDep):
    return await ev.audit_quotes(db)
