"""Knowledge API: entities, aliases, claims with evidence, relationships, review queue."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from kos.api.deps import DbDep, UserDep
from kos.evidence import service as evidence_svc
from kos.knowledge import ai_jobs, compare
from kos.knowledge import service as ks
from kos.library.locate import location
from kos.models import (
    AuditLog,
    Claim,
    ClaimEntity,
    Entity,
    EntityAlias,
    EntityMerge,
    EntityType,
    Evidence,
    Person,
    Relationship,
    RelationshipType,
    ReviewTask,
    Source,
    SourceSegment,
    SourceVersion,
)

router = APIRouter(prefix="/api", tags=["knowledge"])
ClaimType = Literal[
    "definition",
    "reported_fact",
    "hypothesis",
    "interpretation",
    "opinion",
    "recommendation",
    "ai_inference",
    "personal_conclusion",
]


def _http(exc: ks.KnowledgeError) -> HTTPException | JSONResponse:
    if isinstance(exc, ks.DuplicateName):
        return JSONResponse(
            {
                "detail": str(exc),
                "existing_id": str(exc.existing.id),
                "existing_slug": exc.existing.slug,
            },
            status_code=status.HTTP_409_CONFLICT,
        )
    code = {
        ks.NotFound: status.HTTP_404_NOT_FOUND,
        ks.QuoteNotFound: status.HTTP_422_UNPROCESSABLE_CONTENT,
    }.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return HTTPException(code, str(exc))


def _fail(exc: ks.KnowledgeError):
    r = _http(exc)
    if isinstance(r, JSONResponse):
        return r
    raise r from None


# ---- types ----------------------------------------------------------------------------


@router.get("/knowledge/types")
async def types(db: DbDep, _: UserDep):
    ets = await db.scalars(select(EntityType).order_by(EntityType.label))
    rts = await db.scalars(select(RelationshipType).order_by(RelationshipType.label))
    return {
        "entity_types": [
            {"key": t.key, "label": t.label, "description": t.description} for t in ets
        ],
        "relationship_types": [
            {
                "key": t.key,
                "label": t.label,
                "inverse_label": t.inverse_label,
                "is_symmetric": t.is_symmetric,
            }
            for t in rts
        ],
    }


# ---- entities -------------------------------------------------------------------------


class AliasIn(BaseModel):
    alias: str = Field(min_length=1, max_length=300)
    language: str | None = Field(default=None, max_length=10)
    kind: Literal["synonym", "translation", "abbreviation", "variant"] = "synonym"


class EntityIn(BaseModel):
    type_key: str
    name: str = Field(min_length=1, max_length=300)
    definition: str | None = Field(default=None, max_length=5000)
    aliases: list[AliasIn] = []


class EntitySummary(BaseModel):
    id: uuid.UUID
    slug: str
    type_key: str
    canonical_name: str
    short_definition: str | None
    status: str
    origin: str
    claims: int = 0
    relationships: int = 0


@router.get("/entities", response_model=list[EntitySummary])
async def list_entities(
    db: DbDep,
    _: UserDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    type_key: str | None = None,
    status_filter: Literal["active", "proposed", "merged", "archived", "live"] = "live",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
):
    stmt = select(Entity)
    stmt = stmt.where(
        Entity.status.in_(ks.LIVE) if status_filter == "live" else Entity.status == status_filter
    )
    if type_key:
        stmt = stmt.where(Entity.type_key == type_key)
    if q:
        sub = select(EntityAlias.entity_id).where(
            or_(
                EntityAlias.alias_normalized.contains(ks.normalize(q)),
                func.similarity(EntityAlias.alias_normalized, ks.normalize(q)) > 0.3,
            )
        )
        stmt = stmt.where(Entity.id.in_(sub))
    entities = (await db.scalars(stmt.order_by(Entity.canonical_name).limit(limit))).all()
    ids = [e.id for e in entities]
    claim_counts = dict(
        (
            await db.execute(
                select(ClaimEntity.entity_id, func.count())
                .where(ClaimEntity.entity_id.in_(ids))
                .group_by(ClaimEntity.entity_id)
            )
        ).all()
    )
    rel_counts: dict[uuid.UUID, int] = {}
    for col in (Relationship.source_entity_id, Relationship.target_entity_id):
        for eid, n in (
            await db.execute(
                select(col, func.count())
                .where(col.in_(ids), Relationship.status != "rejected")
                .group_by(col)
            )
        ).all():
            rel_counts[eid] = rel_counts.get(eid, 0) + n
    return [
        EntitySummary(
            **{k: getattr(e, k) for k in EntitySummary.model_fields if hasattr(e, k)},
            claims=claim_counts.get(e.id, 0),
            relationships=rel_counts.get(e.id, 0),
        )
        for e in entities
    ]


@router.get("/entities/candidates")
async def entity_candidates(
    db: DbDep, _: UserDep, name: Annotated[str, Query(min_length=1, max_length=300)]
):
    return [
        {**c, "id": str(c["id"]), "score": round(float(c["score"]), 3)}
        for c in await ks.candidates(db, name)
    ]


@router.post("/entities", status_code=status.HTTP_201_CREATED, response_model=EntitySummary)
async def create_entity(body: EntityIn, db: DbDep, _: UserDep):
    try:
        e = await ks.create_entity(
            db,
            type_key=body.type_key,
            name=body.name,
            definition=body.definition,
            aliases=[(a.alias, a.language) for a in body.aliases],
        )
    except ks.KnowledgeError as exc:
        return _fail(exc)
    return EntitySummary.model_validate(e, from_attributes=True)


async def _resolve(db, ref: str) -> Entity:
    try:
        e = await db.get(Entity, uuid.UUID(ref))
    except ValueError:
        e = await db.scalar(select(Entity).where(Entity.slug == ref))
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concepto no encontrado")
    return e


@router.get("/entities/{ref}")
async def get_entity(ref: str, db: DbDep, _: UserDep) -> dict[str, Any]:
    e = await _resolve(db, ref)
    aliases = (
        await db.scalars(
            select(EntityAlias)
            .where(EntityAlias.entity_id == e.id)
            .order_by(EntityAlias.kind, EntityAlias.alias)
        )
    ).all()
    claim_rows = (
        await db.execute(
            select(Claim, ClaimEntity.role)
            .join(ClaimEntity, ClaimEntity.claim_id == Claim.id)
            .where(ClaimEntity.entity_id == e.id)
            .order_by(Claim.created_at)
        )
    ).all()
    claims = []
    for claim, role in claim_rows:
        ev_rows = (
            await db.execute(
                select(Evidence, SourceSegment, SourceVersion, Source)
                .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
                .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
                .join(Source, Source.id == SourceVersion.source_id)
                .where(Evidence.claim_id == claim.id)
            )
        ).all()
        person = (
            await db.get(Person, claim.attributed_person_id) if claim.attributed_person_id else None
        )
        claims.append(
            {
                "id": str(claim.id),
                "text": claim.text,
                "claim_type": claim.claim_type,
                "role": role,
                "review_status": claim.review_status,
                "origin": claim.origin,
                "person": person.name if person else None,
                "person_id": str(person.id) if person else None,
                "context": claim.context,
                "provenance": await evidence_svc.provenance(db, claim),
                "evidence": [
                    {
                        "id": str(ev.id),
                        "quote": ev.quote,
                        "segment_id": str(seg.id),
                        **location(seg, ev.quote_char_start),
                        "source_id": str(src.id),
                        "source_title": src.title,
                        "version_id": str(ver.id),
                        "mime": ver.mime,
                    }
                    for ev, seg, ver, src in ev_rows
                ],
            }
        )
    other = aliased(Entity)
    rels = []
    for direction, mine, theirs in (
        ("out", Relationship.source_entity_id, Relationship.target_entity_id),
        ("in", Relationship.target_entity_id, Relationship.source_entity_id),
    ):
        rows = (
            await db.execute(
                select(Relationship, other, RelationshipType)
                .join(other, other.id == theirs)
                .join(RelationshipType, RelationshipType.key == Relationship.type_key)
                .where(mine == e.id)
            )
        ).all()
        for rel, oth, rt in rows:
            label = rt.label if direction == "out" or rt.is_symmetric else rt.inverse_label
            rels.append(
                {
                    "id": str(rel.id),
                    "type_key": rel.type_key,
                    "label": label,
                    "direction": direction,
                    "other": {"id": str(oth.id), "slug": oth.slug, "name": oth.canonical_name},
                    "status": rel.status,
                    "origin": rel.origin,
                    "description": rel.description,
                    "conditions": rel.conditions,
                }
            )
    merges = (
        await db.scalars(
            select(EntityMerge).where(
                EntityMerge.target_entity_id == e.id, EntityMerge.undone_at.is_(None)
            )
        )
    ).all()
    merged_from = []
    for m in merges:
        src = await db.get(Entity, m.source_entity_id)
        merged_from.append({"merge_id": str(m.id), "name": src.canonical_name, "at": m.created_at})
    into = await db.get(Entity, e.merged_into_id) if e.merged_into_id else None
    return {
        "id": str(e.id),
        "slug": e.slug,
        "type_key": e.type_key,
        "canonical_name": e.canonical_name,
        "short_definition": e.short_definition,
        "status": e.status,
        "origin": e.origin,
        "merged_into": {"id": str(into.id), "slug": into.slug, "name": into.canonical_name}
        if into
        else None,
        "aliases": [
            {"id": str(a.id), "alias": a.alias, "kind": a.kind, "language": a.language}
            for a in aliases
        ],
        "claims": claims,
        "relationships": rels,
        "merged_from": merged_from,
    }


class EntityPatch(BaseModel):
    canonical_name: str | None = Field(default=None, min_length=1, max_length=300)
    short_definition: str | None = Field(default=None, max_length=5000)
    type_key: str | None = None
    status: Literal["active", "archived"] | None = None


@router.patch("/entities/{entity_id}", response_model=EntitySummary)
async def patch_entity(entity_id: uuid.UUID, body: EntityPatch, db: DbDep, _: UserDep):
    e = await db.get(Entity, entity_id)
    if e is None or e.status == "merged":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concepto no encontrado")
    changes = body.model_dump(exclude_unset=True)
    if "type_key" in changes and await db.get(EntityType, changes["type_key"]) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "tipo desconocido")
    if (name := changes.get("canonical_name")) and (
        other := await ks.entity_with_alias(db, name, exclude=e.id)
    ):
        return _fail(ks.DuplicateName(other))
    before = {k: getattr(e, k) for k in changes}
    if "short_definition" in changes:  # goes through the definition history
        text = (changes.pop("short_definition") or "").strip()
        from kos.knowledge import definitions as defs

        try:
            if text:
                await defs.write(db, e, text, None)
            else:
                await defs.clear(db, e)
        except defs.DefinitionError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
        changes["short_definition"] = e.short_definition
    for k, v in changes.items():
        setattr(e, k, v)
    if name:
        await db.flush()
        if not await db.scalar(
            select(EntityAlias.id).where(
                EntityAlias.entity_id == e.id, EntityAlias.alias_normalized == ks.normalize(name)
            )
        ):
            db.add(
                EntityAlias(
                    entity_id=e.id, alias=name, alias_normalized=ks.normalize(name), kind="variant"
                )
            )
    db.add(
        AuditLog(
            actor="user",
            action="entity.updated",
            subject_table="entities",
            subject_id=str(e.id),
            before=before,
            after=changes,
        )
    )
    await db.commit()
    return EntitySummary.model_validate(e, from_attributes=True)


@router.post("/entities/{entity_id}/aliases", status_code=status.HTTP_201_CREATED)
async def add_alias(entity_id: uuid.UUID, body: AliasIn, db: DbDep, _: UserDep):
    try:
        a = await ks.add_alias(db, entity_id, body.alias, kind=body.kind, language=body.language)
    except ks.KnowledgeError as exc:
        return _fail(exc)
    return {"id": str(a.id), "alias": a.alias}


@router.delete("/entities/{entity_id}/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alias(entity_id: uuid.UUID, alias_id: uuid.UUID, db: DbDep, _: UserDep):
    a = await db.get(EntityAlias, alias_id)
    if a is None or a.entity_id != entity_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alias no encontrado")
    if a.kind == "canonical":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "el nombre principal no se puede borrar")
    await db.delete(a)
    db.add(
        AuditLog(
            actor="user",
            action="alias.deleted",
            subject_table="entity_aliases",
            subject_id=str(alias_id),
            before={"alias": a.alias, "entity_id": str(entity_id)},
        )
    )
    await db.commit()


class MergeIn(BaseModel):
    into: uuid.UUID


@router.post("/entities/{entity_id}/merge")
async def merge(entity_id: uuid.UUID, body: MergeIn, db: DbDep, _: UserDep):
    try:
        m = await ks.merge_entities(db, entity_id, body.into)
    except ks.KnowledgeError as exc:
        return _fail(exc)
    return {"merge_id": str(m.id)}


@router.post("/merges/{merge_id}/undo")
async def undo(merge_id: uuid.UUID, db: DbDep, _: UserDep):
    try:
        e = await ks.undo_merge(db, merge_id)
    except ks.KnowledgeError as exc:
        return _fail(exc)
    return {"id": str(e.id), "slug": e.slug, "status": e.status}


# ---- claims ---------------------------------------------------------------------------


class ClaimEntityIn(BaseModel):
    id: uuid.UUID
    role: Literal["subject", "object", "mentioned"] = "subject"


class EvidenceIn(BaseModel):
    segment_id: uuid.UUID
    quote: str = Field(min_length=1, max_length=5000)


class ClaimIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    claim_type: ClaimType
    entities: list[ClaimEntityIn] = Field(min_length=1)
    evidence: list[EvidenceIn] = Field(min_length=1)
    person_id: uuid.UUID | None = None
    context: str | None = Field(default=None, max_length=5000)


@router.post("/claims", status_code=status.HTTP_201_CREATED)
async def create_claim(body: ClaimIn, db: DbDep, _: UserDep):
    try:
        c = await ks.add_claim(
            db,
            text_=body.text,
            claim_type=body.claim_type,
            entities=[(x.id, x.role) for x in body.entities],
            evidence=[(x.segment_id, x.quote) for x in body.evidence],
            person_id=body.person_id,
            context=body.context,
        )
    except ks.KnowledgeError as exc:
        return _fail(exc)
    return {"id": str(c.id)}


class ClaimPatch(BaseModel):
    review_status: Literal["accepted", "rejected", "pending"]


@router.patch("/claims/{claim_id}")
async def patch_claim(claim_id: uuid.UUID, body: ClaimPatch, db: DbDep, _: UserDep):
    c = await db.get(Claim, claim_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "afirmación no encontrada")
    before = c.review_status
    c.review_status = body.review_status
    db.add(
        AuditLog(
            actor="user",
            action="claim.review",
            subject_table="claims",
            subject_id=str(c.id),
            before={"review_status": before},
            after={"review_status": body.review_status},
        )
    )
    await db.commit()
    return {"id": str(c.id), "review_status": c.review_status}


# ---- relationships --------------------------------------------------------------------


class RelEvidenceIn(BaseModel):
    claim_id: uuid.UUID | None = None
    segment_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


class RelationshipIn(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID
    type_key: str
    description: str | None = Field(default=None, max_length=5000)
    conditions: str | None = Field(default=None, max_length=5000)
    evidence: list[RelEvidenceIn] = Field(min_length=1)


@router.post("/relationships", status_code=status.HTTP_201_CREATED)
async def create_relationship(body: RelationshipIn, db: DbDep, _: UserDep):
    try:
        r = await ks.add_relationship(
            db,
            source_id=body.source_id,
            target_id=body.target_id,
            type_key=body.type_key,
            description=body.description,
            conditions=body.conditions,
            evidence=[ev.model_dump() for ev in body.evidence],
        )
    except ks.KnowledgeError as exc:
        return _fail(exc)
    return {"id": str(r.id), "status": r.status}


class RelationshipPatch(BaseModel):
    status: Literal["proposed", "verified", "rejected"]


@router.patch("/relationships/{rel_id}")
async def patch_relationship(rel_id: uuid.UUID, body: RelationshipPatch, db: DbDep, _: UserDep):
    try:
        r = await ks.set_relationship_status(db, rel_id, body.status)
    except ks.KnowledgeError as exc:
        return _fail(exc)
    return {"id": str(r.id), "status": r.status}


# ---- review queue ---------------------------------------------------------------------


class ReviewTaskOut(BaseModel):
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    status: str
    created_at: datetime


@router.get("/review-tasks", response_model=list[ReviewTaskOut])
async def list_tasks(
    db: DbDep,
    _: UserDep,
    status_filter: Literal["open", "accepted", "rejected", "dismissed"] = "open",
):
    rows = await db.scalars(
        select(ReviewTask)
        .where(ReviewTask.status == status_filter)
        .order_by(ReviewTask.created_at)
        .limit(500)
    )
    return [ReviewTaskOut.model_validate(t, from_attributes=True) for t in rows]


@router.post("/review-tasks/scan-duplicates")
async def scan(db: DbDep, _: UserDep):
    return {"created": await ks.scan_duplicates(db)}


class ResolveIn(BaseModel):
    action: Literal["accept", "reject", "dismiss"]
    keep_entity_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2000)


@router.post("/review-tasks/{task_id}/resolve", response_model=ReviewTaskOut)
async def resolve(task_id: uuid.UUID, body: ResolveIn, db: DbDep, _: UserDep):
    try:
        t = await ks.resolve_review_task(db, task_id, body.action, body.keep_entity_id, body.note)
    except ks.KnowledgeError as exc:
        return _fail(exc)
    return ReviewTaskOut.model_validate(t, from_attributes=True)


# ---- people and segments --------------------------------------------------------------


class PersonIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    role: Literal["teacher", "researcher", "author"] | None = None
    affiliation: str | None = Field(default=None, max_length=1000)


@router.get("/persons")
async def list_persons(db: DbDep, _: UserDep):
    return [
        {"id": str(p.id), "name": p.name, "role": p.role, "affiliation": p.affiliation}
        for p in await db.scalars(select(Person).order_by(Person.name))
    ]


@router.post("/persons", status_code=status.HTTP_201_CREATED)
async def create_person(body: PersonIn, db: DbDep, _: UserDep):
    p = Person(**body.model_dump())
    db.add(p)
    await db.commit()
    return {"id": str(p.id), "name": p.name}


@router.get("/segments/{segment_id}")
async def get_segment(segment_id: uuid.UUID, db: DbDep, _: UserDep):
    row = (
        await db.execute(
            select(SourceSegment, SourceVersion, Source)
            .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
            .join(Source, Source.id == SourceVersion.source_id)
            .where(SourceSegment.id == segment_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fragmento no encontrado")
    seg, ver, src = row
    return {
        "id": str(seg.id),
        "text": seg.text,
        **location(seg),
        "source_id": str(src.id),
        "source_title": src.title,
        "version_id": str(ver.id),
        "mime": ver.mime,
    }


# ---- comparison, contradictions, AI analyses ------------------------------------------


@router.get("/entities/{entity_id}/comparison")
async def get_comparison(entity_id: uuid.UUID, db: DbDep, _: UserDep):
    try:
        return await compare.comparison(db, entity_id)
    except ks.KnowledgeError as exc:
        return _fail(exc)


async def _enqueue(db, kind: str, entity_id: uuid.UUID):
    if await db.get(Entity, entity_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concepto no encontrado")
    if await ai_jobs.in_flight(db, kind, entity_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "ya hay un análisis en curso")
    j = ai_jobs.job(kind, entity_id)
    db.add(j)
    db.add(
        AuditLog(
            actor="user",
            action=f"{kind}.requested",
            subject_table="entities",
            subject_id=str(entity_id),
        )
    )
    await db.commit()
    return {"job_id": str(j.id)}


@router.post("/entities/{entity_id}/comparison/analyze", status_code=status.HTTP_202_ACCEPTED)
async def analyze(entity_id: uuid.UUID, db: DbDep, _: UserDep):
    return await _enqueue(db, ai_jobs.COMPARE_KIND, entity_id)


@router.post("/entities/{entity_id}/relationships/propose", status_code=status.HTTP_202_ACCEPTED)
async def propose(entity_id: uuid.UUID, db: DbDep, _: UserDep):
    return await _enqueue(db, ai_jobs.PROPOSE_KIND, entity_id)


class ContradictionIn(BaseModel):
    claim_a_id: uuid.UUID
    claim_b_id: uuid.UUID
    analysis: str | None = Field(default=None, max_length=5000)


@router.post("/contradictions", status_code=status.HTTP_201_CREATED)
async def create_contradiction(body: ContradictionIn, db: DbDep, _: UserDep):
    try:
        c = await compare.add_contradiction(db, body.claim_a_id, body.claim_b_id, body.analysis)
    except ks.KnowledgeError as exc:
        return _fail(exc)
    return {"id": str(c.id), "status": c.status}


class ContradictionPatch(BaseModel):
    status: Literal["candidate", "real", "context_difference", "dismissed"]
    analysis: str | None = Field(default=None, max_length=5000)


@router.patch("/contradictions/{contradiction_id}")
async def patch_contradiction(
    contradiction_id: uuid.UUID, body: ContradictionPatch, db: DbDep, _: UserDep
):
    try:
        c = await compare.classify_contradiction(db, contradiction_id, body.status, body.analysis)
    except ks.KnowledgeError as exc:
        return _fail(exc)
    return {"id": str(c.id), "status": c.status}


@router.get("/claims/{claim_id}")
async def get_claim(claim_id: uuid.UUID, db: DbDep, _: UserDep):
    c = await db.get(Claim, claim_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "afirmación no encontrada")
    ents = (
        await db.execute(
            select(Entity.id, Entity.slug, Entity.canonical_name)
            .join(ClaimEntity, ClaimEntity.entity_id == Entity.id)
            .where(ClaimEntity.claim_id == claim_id)
        )
    ).all()
    return {
        "id": str(c.id),
        "text": c.text,
        "claim_type": c.claim_type,
        "entities": [{"id": str(i), "slug": s, "name": n} for i, s, n in ents],
    }
