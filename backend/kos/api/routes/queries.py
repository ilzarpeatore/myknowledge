"""AI workspace: ask the library. Answers are produced by a job (see kos.ai.query)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from kos.ai import query as aq
from kos.api.deps import DbDep, UserDep
from kos.models import (
    AIRun,
    AuditLog,
    Course,
    Discipline,
    Entity,
    LibrarySection,
    QueryRecord,
    Source,
)

router = APIRouter(prefix="/api/queries", tags=["queries"])

SCOPE_MODELS = {"source": Source, "course": Course, "discipline": Discipline, "concept": Entity}


class QueryIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    scope_kind: Literal["global", "source", "course", "discipline", "concept", "section"] = "global"
    scope_id: uuid.UUID | None = None
    scope_key: str | None = Field(default=None, max_length=40)
    mode: Literal["research", "application"] = "research"
    parent_id: uuid.UUID | None = None


class QueryOut(BaseModel):
    id: uuid.UUID
    question: str
    scope_kind: str
    scope_id: uuid.UUID | None
    scope_key: str | None = None
    scope_label: str | None = None
    mode: str
    parent_id: uuid.UUID | None
    status: str
    answer: dict[str, Any] | None
    error: str | None
    cost_usd: float | None = None
    model: str | None = None
    created_at: datetime
    finished_at: datetime | None


async def _out(db, q: QueryRecord) -> QueryOut:
    out = QueryOut.model_validate(q, from_attributes=True)
    if q.scope_key and (sec := await db.get(LibrarySection, q.scope_key)):
        out.scope_label = f"Sección {sec.name}"
    if q.scope_id and (m := SCOPE_MODELS.get(q.scope_kind)):
        obj = await db.get(m, q.scope_id)
        out.scope_label = (
            getattr(obj, "title", None)
            or getattr(obj, "name", None)
            or getattr(obj, "canonical_name", None)
        )
    if q.ai_run_id and (run := await db.get(AIRun, q.ai_run_id)):
        out.cost_usd, out.model = float(run.cost_usd), run.model_served or run.model_requested
    return out


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=QueryOut)
async def ask(body: QueryIn, db: DbDep, _: UserDep):
    if body.scope_kind == "section":
        if not body.scope_key or await db.get(LibrarySection, body.scope_key) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "sección no encontrada")
    elif body.scope_kind != "global":
        if (
            body.scope_id is None
            or await db.get(SCOPE_MODELS[body.scope_kind], body.scope_id) is None
        ):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "ámbito no encontrado")
    if body.parent_id and await db.get(QueryRecord, body.parent_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "consulta anterior no encontrada")
    q = QueryRecord(
        question=body.question.strip(),
        scope_kind=body.scope_kind,
        scope_id=body.scope_id if body.scope_kind not in ("global", "section") else None,
        scope_key=body.scope_key if body.scope_kind == "section" else None,
        mode=body.mode,
        parent_id=body.parent_id,
    )
    db.add(q)
    await db.flush()
    db.add(aq.job(q.id))
    db.add(
        AuditLog(
            actor="user",
            action="query.asked",
            subject_table="queries",
            subject_id=str(q.id),
            after={"scope": body.scope_kind, "mode": body.mode},
        )
    )
    await db.commit()
    return await _out(db, q)


@router.get("", response_model=list[QueryOut])
async def history(db: DbDep, _: UserDep, limit: int = Query(30, ge=1, le=200)):
    rows = (
        await db.scalars(select(QueryRecord).order_by(QueryRecord.created_at.desc()).limit(limit))
    ).all()
    return [await _out(db, q) for q in rows]


@router.get("/{query_id}", response_model=QueryOut)
async def get_query(query_id: uuid.UUID, db: DbDep, _: UserDep):
    q = await db.get(QueryRecord, query_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "consulta no encontrada")
    return await _out(db, q)
