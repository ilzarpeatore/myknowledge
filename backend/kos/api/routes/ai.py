"""AI configuration and spending: budget, task routing, recent runs."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from kos.ai import credentials, service
from kos.api.deps import DbDep, UserDep
from kos.models import AIBudget, AIModel, AIRun, AITaskRoute, AuditLog

router = APIRouter(prefix="/api/ai", tags=["ai"])
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class ModelOut(BaseModel):
    name: str
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    supports_fallback: bool
    enabled: bool
    prices_as_of: str | None


class RouteOut(BaseModel):
    task: str
    model_name: str
    effort: str
    max_tokens: int


class RunOut(BaseModel):
    id: uuid.UUID
    task: str
    model_requested: str
    model_served: str | None
    status: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    error: str | None
    created_at: datetime


class Overview(BaseModel):
    credentials_configured: bool
    month: str
    spent_usd: Decimal
    limit_usd: Decimal
    models: list[ModelOut]
    routes: list[RouteOut]
    recent_runs: list[RunOut]


@router.get("", response_model=Overview)
async def overview(db: DbDep, _: UserDep, runs: Annotated[int, Query(ge=0, le=200)] = 20):
    month = service.current_month()
    return Overview(
        credentials_configured=(await credentials.status(db))["configured"],
        month=month,
        spent_usd=await service.month_spend(db, month),
        limit_usd=await service.month_limit(db, month),
        models=[
            ModelOut.model_validate(m, from_attributes=True)
            for m in await db.scalars(select(AIModel).order_by(AIModel.name))
        ],
        routes=[
            RouteOut.model_validate(r, from_attributes=True)
            for r in await db.scalars(select(AITaskRoute).order_by(AITaskRoute.task))
        ],
        recent_runs=[
            RunOut.model_validate(r, from_attributes=True)
            for r in await db.scalars(select(AIRun).order_by(AIRun.created_at.desc()).limit(runs))
        ],
    )


class BudgetIn(BaseModel):
    limit_usd: Decimal = Field(ge=0, le=100000)


@router.put("/budget", response_model=Overview)
async def set_budget(body: BudgetIn, db: DbDep, user: UserDep):
    month = service.current_month()
    row = await db.get(AIBudget, month)
    before = str(row.limit_usd) if row else None
    if row is None:
        row = AIBudget(month=month, limit_usd=body.limit_usd)
        db.add(row)
    row.limit_usd = body.limit_usd
    row.updated_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor="user",
            action="ai.budget_set",
            subject_table="ai_budgets",
            subject_id=month,
            before={"limit_usd": before},
            after={"limit_usd": str(body.limit_usd)},
        )
    )
    await db.commit()
    return await overview(db, user)


class RouteIn(BaseModel):
    model_name: str
    effort: Effort
    max_tokens: int = Field(ge=256, le=128000)


@router.put("/routes/{task}", response_model=RouteOut)
async def set_route(task: str, body: RouteIn, db: DbDep, _: UserDep):
    route = await db.get(AITaskRoute, task)
    if route is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown task")
    model = await db.get(AIModel, body.model_name)
    if model is None or not model.enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown or disabled model")
    before = {
        "model_name": route.model_name,
        "effort": route.effort,
        "max_tokens": route.max_tokens,
    }
    route.model_name, route.effort, route.max_tokens = body.model_name, body.effort, body.max_tokens
    db.add(
        AuditLog(
            actor="user",
            action="ai.route_set",
            subject_table="ai_task_routes",
            subject_id=task,
            before=before,
            after=body.model_dump(),
        )
    )
    await db.commit()
    return RouteOut.model_validate(route, from_attributes=True)


# ---- the Anthropic API key, set from the web -----------------------------------------


@router.get("/credentials")
async def get_credentials(db: DbDep, _: UserDep):
    """Whether a key is configured and where from; never the key itself."""
    return await credentials.status(db)


class KeyIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=400)


@router.put("/credentials")
async def set_credentials(body: KeyIn, db: DbDep, _: UserDep):
    try:
        return await credentials.set_key(db, body.api_key)
    except credentials.CredentialsError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.delete("/credentials")
async def delete_credentials(db: DbDep, _: UserDep):
    return await credentials.delete_key(db)


class _Pong(BaseModel):
    ok: bool


@router.post("/credentials/test")
async def test_credentials(db: DbDep, _: UserDep):
    """One minimal call with the key in use (task «check»): costs a fraction of a cent."""
    try:
        await service.run_structured(
            db,
            task="check",
            prompt_key="connection_check",
            schema=_Pong,
            instructions="Responde ok=true.",
        )
    except service.AIError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    run = await db.scalar(select(AIRun).order_by(AIRun.created_at.desc()).limit(1))
    return {
        "ok": True,
        "model": run.model_served if run else None,
        "cost_usd": str(run.cost_usd) if run else None,
    }
