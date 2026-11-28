"""Families of concepts: the AI proposes them per branch, the user approves each one.

A family is a head concept and the concepts that hang from it («Sentadilla» →
sentadilla bilateral, completa, lateral; «Músculos del cuello» → estilohioideo…;
«Pliometría» → fase 1… fase 4). The head is a concept of the list or a new one the
AI names. It is organisation, stored in ``entities.parent_id`` (relationships
always need cited evidence; a family does not claim anything).

Haiku (task «organize») sees the concepts of one branch that have no family yet,
and the «is a kind of / is part of» pairs found while judging duplicates as hints.
Each family is a review task «concept_family»: the user unticks the members that
do not fit and approves it, and only then is anything written. Members are ids from
the list, so the model cannot invent concepts; a member is in one family at most.
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
    ReviewTask,
)

KIND = "concepts.propose_families"
TASK = "organize"
REVIEW_KIND = "concept_family"
MAX_PER_CALL = 350  # concepts of a branch per call; bigger branches go in parts
MIN_MEMBERS = 2


class FamilyError(ValueError):
    pass


class FamilyOut(BaseModel):
    head: str | None = Field(default=None, description="id of the head concept (c12) if listed")
    new_head: str | None = Field(default=None, description="name of a new head, if none fits")
    head_type: str | None = Field(default=None, description="type key of a new head")
    members: list[str] = Field(description="ids of the concepts in the family")
    reason: str = Field(description="what the family groups, one short sentence in Spanish")


class FamiliesOut(BaseModel):
    families: list[FamilyOut]


async def areas(db: AsyncSession) -> list[Discipline]:
    rows = list((await db.scalars(select(Discipline).order_by(Discipline.name))).all())
    return [d for d in rows if d.parent_id is not None] or rows


PENDING = text(
    "select jsonb_array_elements(payload -> 'members') ->> 'id' from review_tasks"
    " where kind = 'concept_family' and status = 'open'"
)


async def candidates(db: AsyncSession, discipline_id: uuid.UUID) -> list[Entity]:
    """Live concepts of a branch with no family and in no open proposal."""
    pending = set((await db.scalars(PENDING)).all())  # already in an open proposal
    rows = await db.scalars(
        select(Entity)
        .join(EntityDiscipline, EntityDiscipline.entity_id == Entity.id)
        .where(
            EntityDiscipline.discipline_id == discipline_id,
            Entity.status.in_(ks.LIVE),
            Entity.parent_id.is_(None),
        )
        .order_by(func.lower(Entity.canonical_name))
    )
    return [e for e in rows.all() if str(e.id) not in pending]


async def in_flight(db: AsyncSession) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.kind == KIND, Job.status.in_([JobStatus.pending, JobStatus.processing]))
        )
        or 0
    )


async def estimate(db: AsyncSession, n: int) -> float | None:
    """Before measuring: ~45 tokens in and ~15 out per concept, plus the prompt."""
    route = await db.get(AITaskRoute, TASK)
    model = await db.get(AIModel, route.model_name) if route else None
    if model is None:
        return None
    calls = -(-n // MAX_PER_CALL) if n else 0
    in_tokens, out_tokens = n * 45 + calls * 1800, n * 15
    usd = (
        Decimal(in_tokens) * model.input_usd_per_mtok
        + Decimal(out_tokens) * model.output_usd_per_mtok
    ) / ai.MTOK
    return round(float(usd), 4)


async def overview(db: AsyncSession) -> dict[str, Any]:
    todo = len({e.id for d in await areas(db) for e in await candidates(db, d.id)})
    families = await db.scalar(
        select(func.count(func.distinct(Entity.parent_id))).where(
            Entity.parent_id.is_not(None), Entity.status.in_(ks.LIVE)
        )
    )
    proposals = await db.scalar(
        select(func.count())
        .select_from(ReviewTask)
        .where(ReviewTask.kind == REVIEW_KIND, ReviewTask.status == "open")
    )
    return {
        "families": families or 0,
        "proposals": proposals or 0,
        "without_family": todo,
        "running": await in_flight(db),
        "usd": await estimate(db, todo),
    }


async def enqueue(db: AsyncSession, discipline_id: uuid.UUID | None = None) -> int:
    """One job per branch (the biggest first); the caller commits."""
    branches = await areas(db)
    if discipline_id:
        branches = [d for d in branches if d.id == discipline_id]
    sized = [(len(await candidates(db, d.id)), d) for d in branches]
    jobs = [
        Job(
            kind=KIND,
            status=JobStatus.pending,
            max_attempts=2,
            payload={"discipline_id": str(d.id)},
        )
        for n, d in sorted(sized, key=lambda x: -x[0])
        if n > MIN_MEMBERS
    ]
    db.add_all(jobs)
    return len(jobs)


async def _hints(db: AsyncSession, ids: set[str]) -> list[str]:
    """«A is a kind of / part of B» pairs found while judging duplicates."""
    rows = await db.scalars(
        select(ReviewTask).where(
            ReviewTask.kind == "entity_merge",
            ReviewTask.payload["ai"]["verdict"].astext == "related",
        )
    )
    text = {
        "a_kind_of_b": "«{a}» es un tipo de «{b}»",
        "b_kind_of_a": "«{b}» es un tipo de «{a}»",
        "a_part_of_b": "«{a}» forma parte de «{b}»",
        "b_part_of_a": "«{b}» forma parte de «{a}»",
    }
    out = []
    for t in rows.all():
        p = t.payload
        rel = p.get("ai", {}).get("relation")
        if rel in text and (p["entity_a"] in ids or p["entity_b"] in ids):
            out.append(text[rel].format(a=p["name_a"], b=p["name_b"]))
    return out[:300]


async def propose(db: AsyncSession, discipline_id: uuid.UUID, job_id: uuid.UUID | None) -> dict:
    branch = await db.get(Discipline, discipline_id)
    if branch is None:
        raise PermanentJobError("rama no encontrada")
    ents = await candidates(db, discipline_id)
    types = list((await db.scalars(select(EntityType).order_by(EntityType.key))).all())
    type_keys = {t.key for t in types}
    made = 0
    for start in range(0, len(ents), MAX_PER_CALL):
        part = ents[start : start + MAX_PER_CALL]
        if len(part) < MIN_MEMBERS + 1:
            break
        refs = {f"c{i + 1}": e for i, e in enumerate(part)}
        lines = []
        for r, e in refs.items():
            line = f"{r} · {e.canonical_name} · {e.type_key}"
            if e.short_definition:
                line += f" · {' '.join(e.short_definition.split())[:100]}"
            lines.append(line)
        docs = [
            ai.Document(
                ref="concepts",
                label=f"conceptos de la rama {branch.name} (id · nombre · tipo · definición)",
                text="\n".join(lines),
            ),
            ai.Document(
                ref="types",
                label="tipos (clave: nombre)",
                text="\n".join(f"{t.key}: {t.label}" for t in types),
            ),
        ]
        hints = await _hints(db, {str(e.id) for e in part})
        if hints:
            seen = "\n".join(hints)
            docs.append(ai.Document(ref="hints", label="relaciones ya vistas", text=seen))
        try:
            result: FamiliesOut = await ai.run_structured(
                db,
                task=TASK,
                job_id=job_id,
                prompt_key="propose_families",
                schema=FamiliesOut,
                instructions=f"Agrupa en familias los conceptos de la rama «{branch.name}».",
                documents=docs,
                variables={"branch": branch.name},
            )
        except ai.ProviderFailure as exc:
            if exc.retryable:
                raise
            raise PermanentJobError(str(exc)) from None
        except ai.AIError as exc:
            raise PermanentJobError(f"{type(exc).__name__}: {exc}") from None
        made += await _store(db, branch, refs, result, type_keys)
    await db.commit()
    return {"branch": branch.name, "concepts": len(ents), "proposals": made}


async def _store(
    db: AsyncSession,
    branch: Discipline,
    refs: dict[str, Entity],
    result: FamiliesOut,
    type_keys: set[str],
) -> int:
    used: set[str] = set()
    made = 0
    for f in result.families:
        head = refs.get((f.head or "").strip())
        head_name = head.canonical_name if head else " ".join((f.new_head or "").split())[:300]
        if not head_name:
            continue
        if head is None and (same := await ks.entity_with_alias(db, head_name)):
            head = same if same.status in ks.LIVE else None
        members = []
        for m in dict.fromkeys(x.strip() for x in f.members):
            e = refs.get(m)
            if e is None or m in used or (head is not None and e.id == head.id):
                continue
            members.append(e)
        if len(members) < MIN_MEMBERS:
            continue
        used.update(r for r, e in refs.items() if e in members)
        head_type = (f.head_type or "").strip()
        key = f"family:{branch.id}:{head.id if head else ks.normalize(head_name)}"[:200]
        rows = [{"id": str(e.id), "name": e.canonical_name} for e in members]
        # The same head twice (in this answer, or in the other part of a big branch):
        # one proposal with all the members.
        same_head = await db.scalar(
            select(ReviewTask).where(
                ReviewTask.kind == REVIEW_KIND,
                ReviewTask.subject_key == key,
                ReviewTask.status == "open",
            )
        )
        if same_head is not None:
            old = same_head.payload["members"]
            have = {m["id"] for m in old}
            more = [r for r in rows if r["id"] not in have]
            same_head.payload = {**same_head.payload, "members": old + more}
            await db.flush()
            continue
        db.add(
            ReviewTask(
                kind=REVIEW_KIND,
                subject_key=key,
                payload={
                    "discipline_id": str(branch.id),
                    "discipline": branch.name,
                    "head_id": str(head.id) if head else None,
                    "head_name": head_name,
                    "head_type": head_type if head_type in type_keys else "concept",
                    "members": rows,
                    "reason": f.reason.strip()[:300],
                },
            )
        )
        await db.flush()  # so a repeated head in this answer finds it
        made += 1
    return made


async def _ancestors(db: AsyncSession, e: Entity) -> set[uuid.UUID]:
    seen: set[uuid.UUID] = set()
    cur = e
    while cur is not None and cur.parent_id and cur.parent_id not in seen:
        seen.add(cur.parent_id)
        cur = await db.get(Entity, cur.parent_id)
    return seen


async def accept(
    db: AsyncSession,
    task_id: uuid.UUID,
    member_ids: list[uuid.UUID],
    head_name: str | None = None,
) -> dict[str, Any]:
    """Approve a family with the members the user kept (and maybe another head name)."""
    t = await db.get(ReviewTask, task_id)
    if t is None or t.kind != REVIEW_KIND or t.status != "open":
        raise FamilyError("propuesta no encontrada o ya resuelta")
    p = t.payload
    proposed = {uuid.UUID(m["id"]) for m in p["members"]}
    keep = [m for m in dict.fromkeys(member_ids) if m in proposed]
    if not keep:
        raise FamilyError("elige al menos un concepto de la familia")
    head = await db.get(Entity, uuid.UUID(p["head_id"])) if p.get("head_id") else None
    name = " ".join((head_name or "").split()) or p["head_name"]
    if head is not None and name != head.canonical_name:
        head = None  # the user named another head
    if head is None:
        head = await ks.entity_with_alias(db, name)
        if head is None:
            head = await ks.create_entity(
                db, type_key=p.get("head_type") or "concept", name=name, origin="user"
            )
    if head.status not in ks.LIVE:
        raise FamilyError(f"«{head.canonical_name}» no está activo")
    branch = uuid.UUID(p["discipline_id"])
    if not await db.get(EntityDiscipline, (head.id, branch)):
        db.add(EntityDiscipline(entity_id=head.id, discipline_id=branch, origin="user"))
    above = await _ancestors(db, head) | {head.id}
    placed = []
    for mid in keep:
        e = await db.get(Entity, mid)
        if e is None or e.status not in ks.LIVE or e.id in above:
            continue  # gone, or it would make a loop
        e.parent_id = head.id
        placed.append(str(e.id))
    now = datetime.now(UTC)
    t.status, t.resolved_at = "accepted", now
    t.payload = {
        **p,
        "accepted": placed,
        "head_id": str(head.id),
        "head_name": head.canonical_name,
    }
    db.add(
        AuditLog(
            actor="user",
            action="concepts.family_accepted",
            subject_table="entities",
            subject_id=str(head.id),
            after={"members": placed, "task_id": str(t.id)},
        )
    )
    await db.commit()
    out = {"id": str(head.id), "slug": head.slug, "name": head.canonical_name}
    return {"head": out, "members": len(placed)}


async def reject(db: AsyncSession, task_id: uuid.UUID) -> None:
    t = await db.get(ReviewTask, task_id)
    if t is None or t.kind != REVIEW_KIND or t.status != "open":
        raise FamilyError("propuesta no encontrada o ya resuelta")
    t.status, t.resolved_at = "rejected", datetime.now(UTC)
    await db.commit()


@handler(KIND)
async def propose_job(ctx: JobContext) -> dict[str, Any]:
    try:
        did = uuid.UUID(ctx.payload["discipline_id"])
    except (KeyError, ValueError, TypeError):
        raise PermanentJobError("payload no válido") from None
    async with ctx.sessionmaker() as db:
        return await propose(db, did, ctx.job_id)
