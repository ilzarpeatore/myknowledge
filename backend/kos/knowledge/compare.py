"""Comparison of explanations of one concept, and contradictions between claims.

Perspectives: claims about a concept are grouped by who says them — the
attributed person, else the course whose materials contain the cited source,
else the source itself. The deterministic view (side by side, with quotes) needs
no AI; the AI analysis is stored separately and only ever points to claim ids.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.knowledge.service import InvalidOperation, NotFound
from kos.models import (
    AuditLog,
    Claim,
    ClaimEntity,
    Contradiction,
    Course,
    CourseMaterial,
    Entity,
    EntityComparison,
    Evidence,
    Person,
    Publication,
    ReviewTask,
    Source,
    SourceSegment,
    SourceVersion,
)

CONTRADICTION_STATUSES = ("candidate", "real", "context_difference", "dismissed")


QUALITY_LABELS = {
    "not_assessed": "no evaluada",
    "low": "baja",
    "moderate": "moderada",
    "high": "alta",
}


def study_context(pub: Publication) -> list[str]:
    """Methodological context of a study, as short labelled parts."""
    parts = [
        f"{label}: {v}"
        for label, v in (
            ("tipo", pub.study_type),
            ("diseño", pub.design),
            ("población", pub.population),
            ("muestra", f"n = {pub.sample_size}" if pub.sample_size else None),
        )
        if v
    ]
    return parts + [f"calidad metodológica: {QUALITY_LABELS[pub.methodological_quality]}"]


async def claim_rows(db: AsyncSession, entity_id: uuid.UUID) -> list[dict[str, Any]]:
    """Non-rejected claims about the entity, each with its perspective and first quote."""
    claims = (
        await db.scalars(
            select(Claim)
            .join(ClaimEntity, ClaimEntity.claim_id == Claim.id)
            .where(ClaimEntity.entity_id == entity_id, Claim.review_status != "rejected")
            .order_by(Claim.created_at)
        )
    ).all()
    out = []
    for c in claims:
        ev = (
            await db.execute(
                select(Evidence, SourceSegment, SourceVersion, Source)
                .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
                .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
                .join(Source, Source.id == SourceVersion.source_id)
                .where(Evidence.claim_id == c.id)
                .limit(1)
            )
        ).first()
        ref = None
        perspective = None
        context = [c.context] if c.context else []
        if c.publication_id:
            pub = await db.get(Publication, c.publication_id)
            label = pub.title + (f" ({pub.year})" if pub.year else "")
            perspective = {
                "kind": "study",
                "id": str(pub.id),
                "label": label,
                "quality": pub.methodological_quality,
            }
            context += study_context(pub)
        elif c.attributed_person_id:
            p = await db.get(Person, c.attributed_person_id)
            perspective = {"kind": "person", "id": str(p.id), "label": p.name}
        if ev:
            e, seg, ver, src = ev
            ref = {
                "quote": e.quote,
                "source_id": str(src.id),
                "version_id": str(ver.id),
                "source_title": src.title,
                "page": seg.loc_page_start,
                "section": seg.loc_section,
                "mime": ver.mime,
            }
            if perspective is None:
                course = (
                    await db.execute(
                        select(Course)
                        .join(CourseMaterial, CourseMaterial.course_id == Course.id)
                        .where(CourseMaterial.source_id == src.id)
                        .order_by(Course.title)
                        .limit(1)
                    )
                ).scalar()
                perspective = (
                    {"kind": "course", "id": str(course.id), "label": course.title}
                    if course
                    else {"kind": "source", "id": str(src.id), "label": src.title}
                )
        perspective = perspective or {"kind": "unknown", "id": None, "label": "Sin origen"}
        out.append(
            {
                "id": str(c.id),
                "text": c.text,
                "claim_type": c.claim_type,
                "origin": c.origin,
                "review_status": c.review_status,
                "perspective": perspective,
                "context": "; ".join(context) or None,
                "ref": ref,
            }
        )
    return out


def group_by_perspective(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        p = r["perspective"]
        g = groups.setdefault((p["kind"], p["id"]), {**p, "claims": []})
        g["claims"].append(r)
    return sorted(groups.values(), key=lambda g: (-len(g["claims"]), g["label"].lower()))


async def comparison(db: AsyncSession, entity_id: uuid.UUID) -> dict[str, Any]:
    if await db.get(Entity, entity_id) is None:
        raise NotFound("concepto no encontrado")
    rows = await claim_rows(db, entity_id)
    ids = [uuid.UUID(r["id"]) for r in rows]
    contradictions = []
    if ids:
        for c in (
            await db.scalars(
                select(Contradiction).where(
                    Contradiction.claim_a_id.in_(ids) | Contradiction.claim_b_id.in_(ids)
                )
            )
        ).all():
            contradictions.append(
                {
                    "id": str(c.id),
                    "claim_a_id": str(c.claim_a_id),
                    "claim_b_id": str(c.claim_b_id),
                    "status": c.status,
                    "analysis": c.analysis_md,
                }
            )
    latest = await db.scalar(
        select(EntityComparison)
        .where(EntityComparison.entity_id == entity_id)
        .order_by(EntityComparison.created_at.desc())
        .limit(1)
    )
    analysis = None
    if latest:
        current = {r["id"] for r in rows}
        analysis = {
            "id": str(latest.id),
            "created_at": latest.created_at,
            "result": latest.result,
            "stale": set(latest.claim_ids) != current,
        }
    return {
        "perspectives": group_by_perspective(rows),
        "contradictions": contradictions,
        "analysis": analysis,
    }


async def add_contradiction(
    db: AsyncSession,
    claim_a: uuid.UUID,
    claim_b: uuid.UUID,
    analysis: str | None = None,
    origin: str = "user",
) -> Contradiction:
    if claim_a == claim_b:
        raise InvalidOperation("una afirmación no se contradice a sí misma")
    for cid in (claim_a, claim_b):
        if await db.get(Claim, cid) is None:
            raise NotFound(f"afirmación {cid} no encontrada")
    a, b = sorted((claim_a, claim_b), key=str)
    existing = await db.scalar(
        select(Contradiction).where(Contradiction.claim_a_id == a, Contradiction.claim_b_id == b)
    )
    if existing:
        return existing
    c = Contradiction(
        claim_a_id=a,
        claim_b_id=b,
        analysis_md=analysis,
        status="candidate" if origin != "user" else "real",
    )
    db.add(c)
    await db.flush()
    if origin != "user":
        db.add(
            ReviewTask(
                kind="contradiction",
                subject_key=str(c.id),
                payload={
                    "contradiction_id": str(c.id),
                    "claim_a_id": str(a),
                    "claim_b_id": str(b),
                    "analysis": analysis,
                },
            )
        )
    db.add(
        AuditLog(
            actor=origin,
            action="contradiction.created",
            subject_table="contradictions",
            subject_id=str(c.id),
        )
    )
    await db.commit()
    return c


async def classify_contradiction(
    db: AsyncSession, cid: uuid.UUID, status: str, analysis: str | None = None
) -> Contradiction:
    """Classify; claims are never deleted because they disagree (master doc §6.4)."""
    if status not in CONTRADICTION_STATUSES:
        raise InvalidOperation("estado no válido")
    c = await db.get(Contradiction, cid)
    if c is None:
        raise NotFound("contradicción no encontrada")
    before = c.status
    c.status = status
    if analysis is not None:
        c.analysis_md = analysis
    c.reviewed_at = datetime.now(UTC)
    task = await db.scalar(
        select(ReviewTask).where(
            ReviewTask.kind == "contradiction",
            ReviewTask.subject_key == str(cid),
            ReviewTask.status == "open",
        )
    )
    if task is not None and status != "candidate":
        task.status = "dismissed" if status == "dismissed" else "accepted"
        task.resolution_note = status
        task.resolved_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor="user",
            action="contradiction.classified",
            subject_table="contradictions",
            subject_id=str(cid),
            before={"status": before},
            after={"status": status},
        )
    )
    await db.commit()
    return c
