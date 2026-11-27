"""Crossing and analysing content: similar passages, coverage, consensus, checks of
claims against studies, link proposals, what a source adds, teacher profiles."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from kos.analysis import ai_jobs as aj
from kos.analysis import crossing, reports
from kos.api.deps import DbDep, UserDep
from kos.library.locate import label as loc_label
from kos.models import (
    AuditLog,
    Claim,
    ClaimCheck,
    ClaimEntity,
    Entity,
    Job,
    JobStatus,
    Person,
    ReviewTask,
    Source,
    SourceLink,
    SourceSegment,
    SourceVersion,
)

router = APIRouter(prefix="/api", tags=["analysis"])


def _404(what: str):
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} no encontrado")


@router.get("/segments/{segment_id}/similar")
async def similar(
    segment_id: uuid.UUID,
    db: DbDep,
    _: UserDep,
    limit: int = Query(default=8, ge=1, le=30),
    min_similarity: float = Query(default=crossing.SIMILAR_PASSAGE, ge=0, le=1),
):
    try:
        return await crossing.similar_segments(
            db, segment_id, limit=limit, min_similarity=min_similarity
        )
    except LookupError:
        _404("fragmento")


@router.get("/analysis/coverage")
async def coverage(
    db: DbDep,
    _: UserDep,
    section: str | None = Query(default=None, max_length=40),
    max_entities: int = Query(default=40, ge=1, le=200),
    max_sources: int = Query(default=30, ge=1, le=100),
):
    return await crossing.coverage(
        db, section=section, max_entities=max_entities, max_sources=max_sources
    )


async def _entity(db, entity_id: uuid.UUID) -> Entity:
    e = await db.get(Entity, entity_id)
    if e is None:
        _404("concepto")
    return e


@router.get("/entities/{entity_id}/consensus")
async def consensus(
    entity_id: uuid.UUID,
    db: DbDep,
    _: UserDep,
    threshold: float = Query(default=crossing.SAME_CLAIM, ge=0.5, le=0.99),
):
    await _entity(db, entity_id)
    return await crossing.consensus(db, entity_id, threshold=threshold)


@router.get("/entities/{entity_id}/study-status")
async def entity_study_status(entity_id: uuid.UUID, db: DbDep, _: UserDep):
    await _entity(db, entity_id)
    claims = (
        await db.scalars(
            select(Claim)
            .join(ClaimEntity, ClaimEntity.claim_id == Claim.id)
            .where(ClaimEntity.entity_id == entity_id, Claim.review_status != "rejected")
        )
    ).all()
    st = await reports.study_status(db, list(claims))
    summary: dict[str, int] = {}
    for v in st.values():
        summary[v["status"]] = summary.get(v["status"], 0) + 1
    return {"claims": {str(k): v for k, v in st.items()}, "summary": summary}


@router.post("/entities/{entity_id}/study-checks", status_code=status.HTTP_202_ACCEPTED)
async def start_study_checks(entity_id: uuid.UUID, db: DbDep, _: UserDep):
    """Check this concept's non-study claims against its study claims (AI)."""
    await _entity(db, entity_id)
    busy = await db.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.kind == aj.CHECK_ENTITY_KIND,
            Job.payload["entity_id"].astext == str(entity_id),
            Job.status.in_([JobStatus.pending, JobStatus.processing]),
        )
    )
    if busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "ya hay una comprobación en marcha")
    job = aj.check_entity_job(entity_id)
    db.add(job)
    await db.commit()
    return {"job_id": str(job.id)}


class CheckPatch(BaseModel):
    status: Literal["accepted", "rejected", "proposed"]


@router.patch("/claim-checks/{check_id}")
async def review_check(check_id: uuid.UUID, body: CheckPatch, db: DbDep, _: UserDep):
    ck = await db.get(ClaimCheck, check_id)
    if ck is None:
        _404("comprobación")
    before = ck.status
    ck.status = body.status
    ck.reviewed_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor="user",
            action="claim_check.review",
            subject_table="claim_checks",
            subject_id=str(ck.id),
            before={"status": before},
            after={"status": body.status},
        )
    )
    await db.commit()
    return await reports.check_out(db, ck)


@router.get("/sources/{source_id}/contribution")
async def contribution(source_id: uuid.UUID, db: DbDep, _: UserDep):
    if await db.get(Source, source_id) is None:
        _404("fuente")
    return await reports.contribution(db, source_id)


@router.post("/sources/{source_id}/analysis", status_code=status.HTTP_202_ACCEPTED)
async def start_analysis(source_id: uuid.UUID, db: DbDep, _: UserDep):
    """Cross this source with the library again (links, contradictions, checks)."""
    if await db.get(Source, source_id) is None:
        _404("fuente")
    job = aj.analyze_job(source_id)
    db.add(job)
    await db.commit()
    return {"job_id": str(job.id)}


async def _passage(db, seg_id: str) -> dict | None:
    seg = await db.get(SourceSegment, uuid.UUID(seg_id))
    if seg is None:
        return None
    ver = await db.get(SourceVersion, seg.source_version_id)
    return {
        "segment_id": seg_id,
        "text": seg.text[:600],
        "where": loc_label(seg),
        "version_id": str(ver.id),
        "mime": ver.mime,
        "page": seg.loc_page_start,
        "slide": seg.loc_slide,
        "t_ms": seg.loc_t_start_ms,
    }


async def link_out(db, lk: SourceLink) -> dict:
    a, b = await db.get(Source, lk.a_id), await db.get(Source, lk.b_id)
    task = await db.scalar(
        select(ReviewTask.id).where(
            ReviewTask.kind == "source_link",
            ReviewTask.subject_key == str(lk.id),
            ReviewTask.status == "open",
        )
    )
    return {
        "id": str(lk.id),
        "a": {"id": str(a.id), "title": a.title},
        "b": {"id": str(b.id), "title": b.title},
        "kind": lk.kind,
        "status": lk.status,
        "origin": lk.origin,
        "score": lk.score,
        "note": lk.note,
        "review_task_id": str(task) if task else None,
        "evidence": [
            {
                "similarity": e["similarity"],
                "a": await _passage(db, e["a"]),
                "b": await _passage(db, e["b"]),
            }
            for e in (lk.evidence or [])
        ],
    }


@router.get("/source-links")
async def list_links(
    db: DbDep,
    _: UserDep,
    status_: Literal["proposed", "accepted", "rejected"] = Query(
        default="proposed", alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=200),
):
    rows = (
        await db.scalars(
            select(SourceLink)
            .where(SourceLink.status == status_)
            .order_by(SourceLink.score.desc().nulls_last(), SourceLink.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [await link_out(db, lk) for lk in rows]


class LinkPatch(BaseModel):
    status: Literal["proposed", "accepted", "rejected"] | None = None
    kind: (
        Literal["related", "expands", "summarizes", "contradicts", "applies", "recommends"] | None
    ) = None
    note: str | None = Field(default=None, max_length=2000)


@router.patch("/source-links/{link_id}")
async def patch_link(link_id: uuid.UUID, body: LinkPatch, db: DbDep, _: UserDep):
    lk = await db.get(SourceLink, link_id)
    if lk is None:
        _404("enlace")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(lk, k, v)
    if "status" in changes:
        lk.reviewed_at = datetime.now(UTC)
        for t in (
            await db.scalars(
                select(ReviewTask).where(
                    ReviewTask.kind == "source_link",
                    ReviewTask.subject_key == str(lk.id),
                    ReviewTask.status == "open",
                )
            )
        ).all():
            if changes["status"] != "proposed":
                t.status = "accepted" if changes["status"] == "accepted" else "rejected"
                t.resolved_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor="user",
            action="source_link.updated",
            subject_table="source_links",
            subject_id=str(lk.id),
            after=changes,
        )
    )
    await db.commit()
    return await link_out(db, lk)


@router.get("/persons/{person_id}/profile")
async def person_profile(person_id: uuid.UUID, db: DbDep, _: UserDep):
    p = await db.get(Person, person_id)
    if p is None:
        _404("persona")
    return await reports.person_profile(db, p)
