"""The concepts page: every concept with filters, importance order and bulk actions."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from kos.api.deps import DbDep, UserDep
from kos.knowledge import browse as br
from kos.knowledge import classify as cl
from kos.knowledge import duplicates as dup
from kos.knowledge import families as fam
from kos.knowledge import service as ks
from kos.library import disciplines as disc
from kos.models import ReviewTask

router = APIRouter(prefix="/api/concepts", tags=["concepts"])


@router.get("")
async def list_concepts(
    db: DbDep,
    _: UserDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Literal["live", "proposed", "active", "archived"] = "live",
    type_key: str | None = None,
    discipline: Annotated[list[str] | None, Query()] = None,  # ids, or «none»
    source_id: uuid.UUID | None = None,
    family: uuid.UUID | None = None,
    flag: Annotated[list[br.Flag] | None, Query()] = None,
    sort: br.Sort = "importance",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
):
    branches: list[uuid.UUID] | Literal["none"] | None = None
    if discipline == ["none"]:
        branches = "none"
    elif discipline:
        try:
            ids = [uuid.UUID(d) for d in discipline]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "rama no válida") from None
        branches = list(await disc.with_descendants(db, ids))
    return await br.browse(
        db,
        q=q,
        status=status_filter,
        type_key=type_key,
        discipline=branches,
        source_id=source_id,
        family=family,
        flags=list(flag or []),
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("/sources")
async def concept_sources(db: DbDep, _: UserDep):
    """Sources that back at least one concept, with how many (for the filter)."""
    rows = await db.execute(
        text(
            "select s.id, s.title, count(distinct ce.entity_id) n from claim_entities ce"
            " join entities e on e.id = ce.entity_id and e.status in ('active', 'proposed')"
            " join evidence ev on ev.claim_id = ce.claim_id"
            " join source_segments sg on sg.id = ev.segment_id"
            " join source_versions sv on sv.id = sg.source_version_id"
            " join sources s on s.id = sv.source_id group by 1, 2 order by 3 desc"
        )
    )
    return [{"id": str(i), "title": t, "concepts": n} for i, t, n in rows.all()]


class BulkIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=br.MAX_BULK)
    action: br.BulkAction
    discipline_id: uuid.UUID | None = None
    type_key: str | None = None
    into: uuid.UUID | None = None


@router.post("/bulk")
async def bulk(body: BulkIn, db: DbDep, _: UserDep):
    try:
        return await br.bulk(
            db,
            body.ids,
            body.action,
            discipline_id=body.discipline_id,
            type_key=body.type_key,
            into=body.into,
        )
    except br.BulkError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except ks.KnowledgeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.get("/classify")
async def classify_status(db: DbDep, _: UserDep):
    """How many concepts have no branch, what classifying them costs, and progress."""
    pending = len(await cl.unassigned(db))
    return {
        "unassigned": pending,
        "left_out": await cl.left_out(db),
        "batches_left": await cl.in_flight(db),
        "usd": await cl.estimate(db, pending),
    }


@router.post("/classify", status_code=status.HTTP_202_ACCEPTED)
async def classify_start(db: DbDep, _: UserDep):
    """Queue every concept without a branch (Haiku). The user's branches stay."""
    if await cl.in_flight(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "ya se están clasificando")
    queued = await cl.enqueue(db)
    await db.commit()
    return {"queued": queued, "batches": await cl.in_flight(db)}


@router.get("/duplicates")
async def duplicates_status(db: DbDep, _: UserDep):
    """Possible duplicates: confirmed by the AI (to review), not judged yet, closed by it."""
    return await dup.overview(db)


@router.post("/duplicates/scan", status_code=status.HTTP_202_ACCEPTED)
async def duplicates_scan(db: DbDep, _: UserDep):
    """Look for duplicates by name and by meaning, then let the AI judge each pair."""
    if await dup.in_flight(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "ya se están buscando")
    db.add(dup.scan_job())
    await db.commit()
    return {"started": True}


@router.get("/duplicates/rejected")
async def duplicates_rejected(db: DbDep, _: UserDep):
    """Pairs the AI closed as not duplicates, newest first, so the user can reopen one."""
    rows = await db.scalars(
        select(ReviewTask)
        .where(
            ReviewTask.kind == "entity_merge",
            ReviewTask.status == "rejected",
            ReviewTask.resolution_note.like("IA:%"),
        )
        .order_by(ReviewTask.resolved_at.desc())
        .limit(500)
    )
    return [{"id": str(t.id), "payload": t.payload, "note": t.resolution_note} for t in rows]


@router.post("/duplicates/{task_id}/reopen")
async def duplicates_reopen(task_id: uuid.UUID, db: DbDep, _: UserDep):
    try:
        t = await dup.reopen(db, task_id)
    except ks.KnowledgeError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return {"id": str(t.id), "status": t.status}


@router.get("/families")
async def families_status(db: DbDep, _: UserDep):
    """Families made, proposals waiting, concepts without a family, cost of proposing."""
    return await fam.overview(db)


@router.get("/families/proposals")
async def family_proposals(db: DbDep, _: UserDep):
    rows = await db.scalars(
        select(ReviewTask)
        .where(ReviewTask.kind == fam.REVIEW_KIND, ReviewTask.status == "open")
        .order_by(ReviewTask.created_at)
        .limit(1000)
    )
    return [{"id": str(t.id), **t.payload} for t in rows]


class ProposeIn(BaseModel):
    discipline_id: uuid.UUID | None = None


@router.post("/families/propose", status_code=status.HTTP_202_ACCEPTED)
async def families_propose(body: ProposeIn, db: DbDep, _: UserDep):
    """The AI proposes families for every branch (or one), concepts without family only."""
    if await fam.in_flight(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "ya se están proponiendo")
    jobs = await fam.enqueue(db, body.discipline_id)
    await db.commit()
    return {"branches": jobs}


class AcceptFamilyIn(BaseModel):
    members: list[uuid.UUID] = Field(min_length=1, max_length=500)
    head_name: str | None = Field(default=None, max_length=300)


@router.post("/families/{task_id}/accept")
async def family_accept(task_id: uuid.UUID, body: AcceptFamilyIn, db: DbDep, _: UserDep):
    try:
        return await fam.accept(db, task_id, body.members, body.head_name)
    except fam.FamilyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except ks.KnowledgeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.post("/families/{task_id}/reject")
async def family_reject(task_id: uuid.UUID, db: DbDep, _: UserDep):
    try:
        await fam.reject(db, task_id)
    except fam.FamilyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return {"status": "rejected"}
