"""Possible duplicate concepts: found by name and by meaning, judged by the AI.

Two searches propose pairs (review tasks «entity_merge»): similar names (trigrams,
``service.scan_duplicates``) and similar meaning (e5 embeddings of name and
definition), which catches synonyms and translations («RIR» / «repeticiones en
reserva»). Most similar pairs are not duplicates (internal / external intercostals,
concentric / eccentric), so Haiku judges each one:

- same: stays open for the user, with the AI's reason and which name to keep;
- different or related (one is a kind or a part of the other): closed as rejected
  with the reason, so it is not proposed again; the user can reopen it. «Related»
  keeps the relation, a hint for building families.

Nothing is ever merged without the user.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
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
    Job,
    JobStatus,
    ReviewTask,
)

SCAN_KIND = "concepts.find_duplicates"
JUDGE_KIND = "concepts.judge_duplicates"
TASK = "resolve"
PER_BATCH = 40
EMBED_MIN = 0.93  # cosine of «name: definition»; the AI filters what is not a duplicate
EMBED_MAX_PAIRS = 400


class VerdictOut(BaseModel):
    ref: str = Field(description="id of the pair, e.g. p3")
    verdict: Literal["same", "different", "related"]
    keep: Literal["a", "b"] | None = Field(default=None, description="if same: the better name")
    relation: Literal["a_kind_of_b", "b_kind_of_a", "a_part_of_b", "b_part_of_a"] | None = None
    reason: str = Field(description="one short sentence, in Spanish")


class JudgeOut(BaseModel):
    items: list[VerdictOut]


def _unjudged():
    return (
        ReviewTask.kind == "entity_merge",
        ReviewTask.status == "open",
        ~ReviewTask.payload.has_key("ai"),
    )


async def in_flight(db: AsyncSession) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.kind.in_([SCAN_KIND, JUDGE_KIND]),
                Job.status.in_([JobStatus.pending, JobStatus.processing]),
            )
        )
        or 0
    )


async def overview(db: AsyncSession) -> dict[str, Any]:
    def count(*where):
        return db.scalar(select(func.count()).select_from(ReviewTask).where(*where))

    merge = ReviewTask.kind == "entity_merge"
    unjudged = await count(*_unjudged())
    return {
        "same": await count(
            merge, ReviewTask.status == "open", ReviewTask.payload["ai"]["verdict"].astext == "same"
        ),
        "unjudged": unjudged,
        "rejected_by_ai": await count(
            merge, ReviewTask.status == "rejected", ReviewTask.resolution_note.like("IA:%")
        ),
        "running": await in_flight(db),
        "usd": await estimate(db, unjudged),
    }


async def estimate(db: AsyncSession, pairs: int) -> float | None:
    """Before measuring: ~80 tokens in and ~50 out per pair, plus the prompt."""
    route = await db.get(AITaskRoute, TASK)
    model = await db.get(AIModel, route.model_name) if route else None
    if model is None:
        return None
    batches = -(-pairs // PER_BATCH)
    in_tokens, out_tokens = pairs * 80 + batches * 1200, pairs * 50
    usd = (
        Decimal(in_tokens) * model.input_usd_per_mtok
        + Decimal(out_tokens) * model.output_usd_per_mtok
    ) / ai.MTOK
    return round(float(usd), 4)


def scan_job() -> Job:
    return Job(kind=SCAN_KIND, status=JobStatus.pending, max_attempts=2, payload={})


async def _seen(db: AsyncSession, key: str) -> bool:
    return bool(
        await db.scalar(
            select(ReviewTask.id).where(
                ReviewTask.kind == "entity_merge",
                ReviewTask.subject_key == key,
                ReviewTask.status.in_(["open", "rejected", "accepted"]),
            )
        )
    )


async def by_meaning(db: AsyncSession) -> int:
    """Pairs of live concepts that mean almost the same (embeddings), as review tasks."""
    import numpy as np

    from kos import embeddings

    try:
        embedder = embeddings.get_embedder()
    except embeddings.EmbeddingsUnavailable:
        return 0
    rows = (
        await db.execute(
            select(Entity.id, Entity.canonical_name, Entity.short_definition)
            .where(Entity.status.in_(ks.LIVE))
            .order_by(Entity.id)
        )
    ).all()
    if len(rows) < 2:
        return 0
    texts = [f"{n}: {d}" if d else n for _, n, d in rows]
    vec = np.array(await asyncio.to_thread(embedder.embed_passages, texts), dtype=np.float32)
    sim = vec @ vec.T
    np.fill_diagonal(sim, 0)
    i, j = np.where(np.triu(sim, 1) >= EMBED_MIN)
    order = np.argsort(-sim[i, j])[:EMBED_MAX_PAIRS]
    created = 0
    for k in order:
        a, b = sorted((rows[i[k]], rows[j[k]]), key=lambda r: str(r[0]))
        key = f"{a[0]}:{b[0]}"
        if await _seen(db, key):
            continue
        db.add(
            ReviewTask(
                kind="entity_merge",
                subject_key=key,
                payload={
                    "entity_a": str(a[0]),
                    "entity_b": str(b[0]),
                    "name_a": a[1],
                    "name_b": b[1],
                    "score": round(float(sim[i[k], j[k]]), 3),
                    "found_by": "meaning",
                },
            )
        )
        created += 1
    await db.commit()
    return created


def judge_jobs(task_ids: list[uuid.UUID]) -> list[Job]:
    return [
        Job(
            kind=JUDGE_KIND,
            status=JobStatus.pending,
            max_attempts=3,
            payload={"task_ids": [str(t) for t in task_ids[n : n + PER_BATCH]]},
        )
        for n in range(0, len(task_ids), PER_BATCH)
    ]


@handler(SCAN_KIND)
async def find_duplicates(ctx: JobContext) -> dict[str, Any]:
    async with ctx.sessionmaker() as db:
        by_name = await ks.scan_duplicates(db)
        meaning = await by_meaning(db)
        ids = list((await db.scalars(select(ReviewTask.id).where(*_unjudged()))).all())
        db.add_all(judge_jobs(ids))
        await db.commit()
        return {"by_name": by_name, "by_meaning": meaning, "to_judge": len(ids)}


async def _describe(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    ents = {e.id: e for e in (await db.scalars(select(Entity).where(Entity.id.in_(ids))))}
    branches: dict[uuid.UUID, list[str]] = {}
    for eid, name in await db.execute(
        select(EntityDiscipline.entity_id, Discipline.name)
        .join(Discipline, Discipline.id == EntityDiscipline.discipline_id)
        .where(EntityDiscipline.entity_id.in_(ids))
    ):
        branches.setdefault(eid, []).append(name)
    out = {}
    for eid, e in ents.items():
        bits = [e.type_key]
        if e.short_definition:
            bits.append(" ".join(e.short_definition.split())[:140])
        if eid in branches:
            bits.append("ramas: " + ", ".join(sorted(branches[eid])))
        out[eid] = f"«{e.canonical_name}» ({'; '.join(bits)})"
    return out


async def judge(db: AsyncSession, task_ids: list[uuid.UUID], job_id: uuid.UUID | None) -> dict:
    tasks = list(
        await db.scalars(select(ReviewTask).where(ReviewTask.id.in_(task_ids), *_unjudged()))
    )
    now = datetime.now(UTC)
    live: list[ReviewTask] = []
    for t in tasks:
        a = await db.get(Entity, uuid.UUID(t.payload["entity_a"]))
        b = await db.get(Entity, uuid.UUID(t.payload["entity_b"]))
        if a is None or b is None or a.status not in ks.LIVE or b.status not in ks.LIVE:
            t.status, t.resolved_at = "dismissed", now
            t.resolution_note = "uno de los dos ya no existe (fusionado o archivado)"
        else:
            live.append(t)
    if not live:
        await db.commit()
        return {"pairs": 0}
    ids = {uuid.UUID(t.payload[k]) for t in live for k in ("entity_a", "entity_b")}
    desc = await _describe(db, ids)
    refs = {f"p{n + 1}": t for n, t in enumerate(live)}
    lines = []
    for r, t in refs.items():
        a_desc = desc[uuid.UUID(t.payload["entity_a"])]
        b_desc = desc[uuid.UUID(t.payload["entity_b"])]
        lines.append(f"{r} · A: {a_desc} | B: {b_desc}")
    try:
        result: JudgeOut = await ai.run_structured(
            db,
            task=TASK,
            job_id=job_id,
            prompt_key="judge_duplicates",
            schema=JudgeOut,
            instructions="Di si cada pareja es el mismo concepto.",
            documents=[
                ai.Document(ref="pairs", label="parejas (id · A | B)", text="\n".join(lines))
            ],
        )
    except ai.ProviderFailure as exc:
        if exc.retryable:
            raise
        raise PermanentJobError(str(exc)) from None
    except ai.AIError as exc:
        raise PermanentJobError(f"{type(exc).__name__}: {exc}") from None

    stats = {"pairs": len(live), "same": 0, "different": 0, "related": 0}
    for v in result.items:
        t = refs.pop(v.ref.strip(), None)
        if t is None:
            continue
        verdict = {"verdict": v.verdict, "reason": v.reason.strip()[:300]}
        if v.verdict == "same" and v.keep:
            verdict["keep"] = t.payload["entity_a" if v.keep == "a" else "entity_b"]
        if v.verdict == "related" and v.relation:
            verdict["relation"] = v.relation
        t.payload = {**t.payload, "ai": verdict}
        stats[v.verdict] += 1
        if v.verdict != "same":
            label = "distintos" if v.verdict == "different" else "relacionados, no iguales"
            t.status, t.resolved_at = "rejected", now
            t.resolution_note = f"IA: {label}. {verdict['reason']}"[:2000]
    db.add(AuditLog(actor="worker", action="concepts.duplicates_judged", after=stats))
    await db.commit()
    return stats


@handler(JUDGE_KIND)
async def judge_job(ctx: JobContext) -> dict[str, Any]:
    try:
        ids = [uuid.UUID(i) for i in ctx.payload["task_ids"]]
    except (KeyError, ValueError, TypeError):
        raise PermanentJobError("payload no válido") from None
    async with ctx.sessionmaker() as db:
        return await judge(db, ids, ctx.job_id)


async def reopen(db: AsyncSession, task_id: uuid.UUID) -> ReviewTask:
    """Undo the AI's «not a duplicate»: back to the user's queue."""
    t = await db.get(ReviewTask, task_id)
    if t is None or t.kind != "entity_merge" or t.status != "rejected":
        raise ks.NotFound("tarea no encontrada")
    t.status, t.resolved_at, t.resolution_note = "open", None, None
    db.add(
        AuditLog(
            actor="user",
            action="review_task.reopened",
            subject_table="review_tasks",
            subject_id=str(t.id),
        )
    )
    await db.commit()
    return t
