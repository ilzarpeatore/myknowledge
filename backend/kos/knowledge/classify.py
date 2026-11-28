"""Put concepts in branches (Anatomía, Nutrición…) and fix their type, with Haiku.

The model receives the branches, the types and a batch of concepts (name, type,
definition, the sources that back them and those sources' branches) and answers
one or two branch names and the best type per concept. Names are matched against
the real branches, so it cannot invent one. Assignments are stored as «ai»: the
user reviews them with the filter, and a new pass never touches the user's. Types
change only on concepts the AI created. About 4 cents for 1,300 concepts.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kos.ai import service as ai
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.knowledge import service as ks
from kos.models import (
    AIModel,
    AITaskRoute,
    AuditLog,
    Discipline,
    Entity,
    EntityDiscipline,
    EntityType,
    Job,
    JobStatus,
)

KIND = "concepts.classify"
TASK = "classify"
BATCH = 60  # ~1,500 output tokens: well inside the route's 4,000
MAX_BRANCHES = 2


class ItemOut(BaseModel):
    ref: str = Field(description="id of the concept, e.g. c12")
    branches: list[str] = Field(default_factory=list, description="1-2 branch names as listed")
    type_key: str | None = Field(default=None, description="best type key, as listed")


class ClassifyOut(BaseModel):
    items: list[ItemOut]


def _no_branch():
    has = select(EntityDiscipline.entity_id).where(EntityDiscipline.entity_id == Entity.id)
    return (Entity.status.in_(ks.LIVE), ~has.exists())


async def unassigned(db: AsyncSession) -> list[uuid.UUID]:
    """Live concepts in no branch that the AI has not looked at yet, oldest first."""
    rows = await db.scalars(
        select(Entity.id)
        .where(*_no_branch(), Entity.classified_at.is_(None))
        .order_by(Entity.created_at, Entity.id)
    )
    return list(rows.all())


async def left_out(db: AsyncSession) -> int:
    """Concepts the AI looked at and could not place (people, loose terms…)."""
    return (
        await db.scalar(
            select(func.count())
            .select_from(Entity)
            .where(*_no_branch(), Entity.classified_at.is_not(None))
        )
        or 0
    )


async def areas(db: AsyncSession) -> list[Discipline]:
    """The branches to choose from: the areas under a root (or every branch)."""
    rows = list((await db.scalars(select(Discipline).order_by(Discipline.name))).all())
    leaves = [d for d in rows if d.parent_id is not None]
    return leaves or rows


async def in_flight(db: AsyncSession) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == KIND, Job.status.in_([JobStatus.pending, JobStatus.processing]))
        )
        or 0
    )


def jobs_for(ids: list[uuid.UUID]) -> list[Job]:
    return [
        Job(
            kind=KIND,
            status=JobStatus.pending,
            max_attempts=3,
            payload={"entity_ids": [str(i) for i in ids[n : n + BATCH]]},
        )
        for n in range(0, len(ids), BATCH)
    ]


async def enqueue(db: AsyncSession) -> int:
    """Queue every concept still without a branch (none if a pass is running).
    Returns how many concepts were queued; the caller commits."""
    if await in_flight(db):
        return 0
    ids = await unassigned(db)
    db.add_all(jobs_for(ids))
    return len(ids)


async def estimate(db: AsyncSession, n: int) -> float | None:
    """Expected cost of classifying ``n`` concepts. Measured on a real batch of 60
    (2026-11-28): 5,052 tokens in and 1,905 out; the full run, ~43 out per concept."""
    route = await db.get(AITaskRoute, TASK)
    model = await db.get(AIModel, route.model_name) if route else None
    if model is None:
        return None
    batches = -(-n // BATCH)
    in_tokens, out_tokens = n * 60 + batches * 1500, n * 45
    usd = (
        Decimal(in_tokens) * model.input_usd_per_mtok
        + Decimal(out_tokens) * model.output_usd_per_mtok
    ) / ai.MTOK
    return round(float(usd), 4)


async def _context(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """For each concept: the sources that back it and their branches, as one line."""
    rows = await db.execute(
        text(
            "select distinct ce.entity_id, s.title, d.name from claim_entities ce"
            " join evidence ev on ev.claim_id = ce.claim_id"
            " join source_segments sg on sg.id = ev.segment_id"
            " join source_versions sv on sv.id = sg.source_version_id"
            " join sources s on s.id = sv.source_id"
            " left join source_disciplines sd on sd.source_id = s.id"
            " left join disciplines d on d.id = sd.discipline_id"
            " where ce.entity_id = any(:ids)"
        ),
        {"ids": ids},
    )
    by: dict[uuid.UUID, dict[str, set[str]]] = {}
    for eid, title, branch in rows.all():
        by.setdefault(eid, {}).setdefault(title, set())
        if branch:
            by[eid][title].add(branch)
    out = {}
    for eid, sources in by.items():
        parts = []
        for title, branches in list(sources.items())[:3]:
            tag = f" ({', '.join(sorted(branches))})" if branches else ""
            parts.append(title[:80] + tag)
        out[eid] = "; ".join(parts)
    return out


async def classify(
    db: AsyncSession, ids: list[uuid.UUID], job_id: uuid.UUID | None, apply: bool = True
) -> dict:
    """Classify a batch. ``apply=False`` only returns what it would do (``proposed``)."""
    entities = list(
        (
            await db.scalars(
                select(Entity)
                .where(Entity.id.in_(ids), Entity.status.in_(ks.LIVE))
                .order_by(Entity.created_at, Entity.id)
            )
        ).all()
    )
    done = set(
        (
            await db.scalars(
                select(EntityDiscipline.entity_id).where(
                    EntityDiscipline.entity_id.in_([e.id for e in entities])
                )
            )
        ).all()
    )
    entities = [e for e in entities if e.id not in done]  # placed meanwhile: leave them
    if not entities:
        return {"concepts": 0}
    branches = await areas(db)
    if not branches:
        raise PermanentJobError("no hay ramas: créalas en Disciplinas")
    types = list((await db.scalars(select(EntityType).order_by(EntityType.key))).all())
    ctx = await _context(db, [e.id for e in entities])
    refs = {f"c{i + 1}": e for i, e in enumerate(entities)}
    lines = []
    for r, e in refs.items():
        line = f"{r} · {e.canonical_name} · {e.type_key}"
        if e.short_definition:
            line += f" · {' '.join(e.short_definition.split())[:160]}"
        if e.id in ctx:
            line += f" · fuentes: {ctx[e.id]}"
        lines.append(line)
    docs = [
        ai.Document(ref="branches", label="ramas", text="\n".join(d.name for d in branches)),
        ai.Document(
            ref="types",
            label="tipos (clave: nombre, qué es)",
            text="\n".join(f"{t.key}: {t.label}, {t.description or ''}" for t in types),
        ),
        ai.Document(
            ref="concepts",
            label="conceptos (id · nombre · tipo actual · definición · fuentes (sus ramas))",
            text="\n".join(lines),
        ),
    ]
    try:
        result: ClassifyOut = await ai.run_structured(
            db,
            task=TASK,
            job_id=job_id,
            prompt_key="classify_concepts",
            schema=ClassifyOut,
            instructions="Pon cada concepto en sus ramas y dale el tipo que le corresponde.",
            documents=docs,
        )
    except ai.ProviderFailure as exc:
        if exc.retryable:
            raise
        raise PermanentJobError(str(exc)) from None
    except ai.AIError as exc:
        raise PermanentJobError(f"{type(exc).__name__}: {exc}") from None

    by_name = {ks.normalize(d.name): d.id for d in branches}
    type_keys = {t.key for t in types}
    names = {d.id: d.name for d in branches}
    now = datetime.now(UTC)
    placed, retyped, missing, proposed = 0, [], [], []
    seen: set[str] = set()
    for item in result.items:
        e = refs.get(item.ref.strip())
        if e is None or item.ref in seen:
            continue
        seen.add(item.ref)
        chosen = list(
            dict.fromkeys(by_name[n] for b in item.branches if (n := ks.normalize(b)) in by_name)
        )[:MAX_BRANCHES]
        placed += bool(chosen)
        if not chosen:
            missing.append(e.canonical_name)
        new_type = (item.type_key or "").strip()
        retype = e.origin == "ai" and new_type in type_keys and new_type != e.type_key
        if retype:
            retyped.append({"id": str(e.id), "from": e.type_key, "to": new_type})
        proposed.append(
            {
                "name": e.canonical_name,
                "branches": [names[d] for d in chosen],
                "type": new_type if retype else e.type_key,
                "was": e.type_key,
            }
        )
        if apply:
            db.add_all(
                EntityDiscipline(entity_id=e.id, discipline_id=d, origin="ai") for d in chosen
            )
            e.classified_at = now
            if retype:
                e.type_key = new_type
    missing += [e.canonical_name for r, e in refs.items() if r not in seen]
    stats = {"concepts": len(entities), "placed": placed, "retyped": len(retyped)}
    if not apply:
        return {**stats, "proposed": proposed, "without_branch": missing}
    db.add(
        AuditLog(
            actor="worker",
            action="concepts.classified",
            subject_table="entities",
            after={**stats, "types": retyped, "without_branch": missing[:200]},
        )
    )
    await db.commit()
    return stats


@handler(KIND)
async def classify_job(ctx: JobContext) -> dict[str, Any]:
    try:
        ids = [uuid.UUID(i) for i in ctx.payload["entity_ids"]]
    except (KeyError, ValueError, TypeError):
        raise PermanentJobError("payload no válido") from None
    async with ctx.sessionmaker() as db:
        return await classify(db, ids, ctx.job_id)
