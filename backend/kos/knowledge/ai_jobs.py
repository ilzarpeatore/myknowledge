"""AI job handlers for the knowledge model.

- ``entity.compare``: compare the explanations of a concept across perspectives.
- ``entity.propose_relationships``: propose typed relationships from claims that
  mention several concepts. Proposals are stored as ``proposed`` with review tasks.

Model output is never trusted blindly: every finding must cite claim ids that
were actually sent, and anything else is dropped (and recorded).
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from kos.ai import service as ai
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.knowledge import compare
from kos.knowledge import service as ks
from kos.models import (
    AIRun,
    ClaimEntity,
    Discipline,
    Entity,
    EntityComparison,
    EntityDiscipline,
    Job,
    JobStatus,
    RelationshipType,
)

COMPARE_KIND = "entity.compare"
PROPOSE_KIND = "entity.propose_relationships"
MAX_CLAIMS = 80


class Finding(BaseModel):
    description: str
    claim_ids: list[str]


class PossibleContradiction(BaseModel):
    claim_a: str
    claim_b: str
    explanation: str


class ComparisonResult(BaseModel):
    summary: str
    agreements: list[Finding]
    terminology_differences: list[Finding]
    conceptual_differences: list[Finding]
    context_differences: list[Finding]
    possible_contradictions: list[PossibleContradiction]
    open_questions: list[str]


class ProposedRelationship(BaseModel):
    source_id: str
    target_id: str
    type_key: str
    claim_ids: list[str]
    condition: str | None = None
    confidence: float = Field(ge=0, le=1)


class RelationshipProposals(BaseModel):
    proposals: list[ProposedRelationship]


def job(kind: str, entity_id: uuid.UUID) -> Job:
    return Job(
        kind=kind, payload={"entity_id": str(entity_id)}, status=JobStatus.pending, max_attempts=3
    )


async def in_flight(db, kind: str, entity_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.kind == kind,
                Job.payload["entity_id"].astext == str(entity_id),
                Job.status.in_([JobStatus.pending, JobStatus.processing]),
            )
        )
    )


def _entity_id(ctx: JobContext) -> uuid.UUID:
    try:
        return uuid.UUID(ctx.payload["entity_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload sin entity_id válido") from None


async def _discipline_label(db, entity_id: uuid.UUID) -> str:
    names = (
        await db.scalars(
            select(Discipline.name)
            .join(EntityDiscipline, EntityDiscipline.discipline_id == Discipline.id)
            .where(EntityDiscipline.entity_id == entity_id)
        )
    ).all()
    return ", ".join(names) or "ciencias del entrenamiento"


async def _run(db, ctx: JobContext, **kw: Any) -> Any:
    """Map AI errors to job semantics: only transient provider errors are retried."""
    try:
        return await ai.run_structured(db, job_id=ctx.job_id, **kw)
    except ai.ProviderFailure as exc:
        if exc.retryable:
            raise
        raise PermanentJobError(str(exc)) from None
    except ai.AIError as exc:
        raise PermanentJobError(f"{type(exc).__name__}: {exc}") from None


def _doc_label(row: dict[str, Any]) -> str:
    ref = row["ref"] or {}
    where = f"{ref.get('source_title', '')}{', p. ' + str(ref['page']) if ref.get('page') else ''}"
    return f"{row['perspective']['label']} — {where}".strip(" —")


@handler(COMPARE_KIND)
async def compare_entity(ctx: JobContext) -> dict[str, Any]:
    entity_id = _entity_id(ctx)
    async with ctx.sessionmaker() as db:
        entity = await db.get(Entity, entity_id)
        if entity is None:
            raise PermanentJobError("concepto no encontrado")
        rows = (await compare.claim_rows(db, entity_id))[:MAX_CLAIMS]
        if len({(r["perspective"]["kind"], r["perspective"]["id"]) for r in rows}) < 2:
            raise PermanentJobError(
                "hacen falta afirmaciones de al menos dos autores, cursos o fuentes"
            )
        docs = [
            ai.Document(
                ref=f"claim:{r['id']}",
                label=_doc_label(r),
                text=f"[{r['claim_type']}] {r['text']}"
                + (f"\nCita: «{r['ref']['quote']}»" if r["ref"] else ""),
            )
            for r in rows
        ]
        result: ComparisonResult = await _run(
            db,
            ctx,
            task="compare",
            prompt_key="compare_explanations",
            schema=ComparisonResult,
            instructions=f"Compara las explicaciones de «{entity.canonical_name}».",
            documents=docs,
            variables={
                "concept": entity.canonical_name,
                "discipline": await _discipline_label(db, entity_id),
            },
        )
        known = {r["id"] for r in rows}
        data = result.model_dump()
        dropped: dict[str, list] = {}
        for key in (
            "agreements",
            "terminology_differences",
            "conceptual_differences",
            "context_differences",
        ):
            keep = []
            for f in data[key]:
                f["claim_ids"] = [c.removeprefix("claim:") for c in f["claim_ids"]]
                if f["claim_ids"] and set(f["claim_ids"]) <= known:
                    keep.append(f)
                else:
                    dropped.setdefault(key, []).append(f)
            data[key] = keep
        pairs = []
        for p in data["possible_contradictions"]:
            a, b = p["claim_a"].removeprefix("claim:"), p["claim_b"].removeprefix("claim:")
            if a in known and b in known and a != b:
                pairs.append({**p, "claim_a": a, "claim_b": b})
            else:
                dropped.setdefault("possible_contradictions", []).append(p)
        data["possible_contradictions"] = pairs
        run_id = await db.scalar(
            select(AIRun.id)
            .where(AIRun.job_id == ctx.job_id)
            .order_by(AIRun.created_at.desc())
            .limit(1)
        )
        db.add(
            EntityComparison(
                entity_id=entity_id,
                ai_run_id=run_id,
                claim_ids=sorted(known),
                result=data,
                dropped=dropped or None,
            )
        )
        await db.commit()
        for p in pairs:
            await compare.add_contradiction(
                db, uuid.UUID(p["claim_a"]), uuid.UUID(p["claim_b"]), p["explanation"], origin="ai"
            )
    return {
        "findings": sum(len(data[k]) for k in data if isinstance(data[k], list)),
        "contradictions": len(pairs),
        "dropped": sum(len(v) for v in dropped.values()),
    }


@handler(PROPOSE_KIND)
async def propose_relationships(ctx: JobContext) -> dict[str, Any]:
    entity_id = _entity_id(ctx)
    async with ctx.sessionmaker() as db:
        entity = await db.get(Entity, entity_id)
        if entity is None:
            raise PermanentJobError("concepto no encontrado")
        rows = (await compare.claim_rows(db, entity_id))[:MAX_CLAIMS]
        mentions: dict[str, dict[str, str]] = {}
        for r in rows:
            ents = (
                await db.execute(
                    select(Entity.id, Entity.canonical_name)
                    .join(ClaimEntity, ClaimEntity.entity_id == Entity.id)
                    .where(ClaimEntity.claim_id == uuid.UUID(r["id"]), Entity.status.in_(ks.LIVE))
                )
            ).all()
            if len(ents) >= 2:
                mentions[r["id"]] = {str(i): n for i, n in ents}
        if not mentions:
            raise PermanentJobError("no hay afirmaciones que mencionen este concepto junto a otro")
        types = (await db.scalars(select(RelationshipType).order_by(RelationshipType.key))).all()
        docs = [
            ai.Document(
                ref=f"claim:{cid}",
                label=_doc_label(next(r for r in rows if r["id"] == cid)),
                text=next(r for r in rows if r["id"] == cid)["text"]
                + "\nConceptos: "
                + "; ".join(f"{i}: {n}" for i, n in ents.items()),
            )
            for cid, ents in mentions.items()
        ]
        result: RelationshipProposals = await _run(
            db,
            ctx,
            task="extract",
            prompt_key="propose_relationships",
            schema=RelationshipProposals,
            instructions=f"Propón relaciones para «{entity.canonical_name}».",
            documents=docs,
            variables={
                "discipline": await _discipline_label(db, entity_id),
                "relationship_types": "\n".join(f"- {t.key}: {t.label}" for t in types),
            },
        )
        run_id = await db.scalar(
            select(AIRun.id)
            .where(AIRun.job_id == ctx.job_id)
            .order_by(AIRun.created_at.desc())
            .limit(1)
        )
        valid_types = {t.key for t in types}
        created, rejected = 0, 0
        for p in result.proposals:
            claim_ids = [c.removeprefix("claim:") for c in p.claim_ids]
            ok = (
                p.type_key in valid_types
                and p.source_id != p.target_id
                and claim_ids
                and all(
                    c in mentions and {p.source_id, p.target_id} <= set(mentions[c])
                    for c in claim_ids
                )
            )
            if not ok:
                rejected += 1
                continue
            try:
                await ks.add_relationship(
                    db,
                    source_id=uuid.UUID(p.source_id),
                    target_id=uuid.UUID(p.target_id),
                    type_key=p.type_key,
                    conditions=p.condition,
                    origin="ai",
                    confidence=p.confidence,
                    ai_run_id=run_id,
                    evidence=[{"claim_id": uuid.UUID(c)} for c in claim_ids],
                )
                created += 1
            except ks.KnowledgeError:
                rejected += 1
    return {"proposed": created, "discarded": rejected}
