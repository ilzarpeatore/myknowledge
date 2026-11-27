"""Definitions of concepts: the current one, proposals waiting for the user and the
history. The user writes one, or asks the AI for one (grounded in the library, with
verified quotes) or Claude proposes one over MCP; a proposal becomes the definition
only when the user accepts it (as is or edited). The current definition is mirrored
in ``Entity.short_definition``, which search, extraction, the mind map and MCP read.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.models import AuditLog, Entity, EntityDefinition, Job, JobStatus

DEFINE_KIND = "concept.define"
SHORT_MAX = 1200
EXTENDED_MAX = 6000
MAX_PROPOSALS = 5  # waiting at once per concept (MCP cannot flood the page)


class DefinitionError(Exception):
    pass


def _clean(short: str | None, extended: str | None) -> tuple[str, str | None]:
    short = (short or "").strip()
    extended = (extended or "").strip() or None
    if not short:
        raise DefinitionError("la definición está vacía")
    if len(short) > SHORT_MAX:
        raise DefinitionError(f"la definición corta admite hasta {SHORT_MAX} caracteres")
    if extended and len(extended) > EXTENDED_MAX:
        raise DefinitionError(f"la explicación admite hasta {EXTENDED_MAX} caracteres")
    return short, extended


async def current(db: AsyncSession, entity: Entity) -> EntityDefinition | None:
    """The current definition. A definition saved another way (an older version, the
    concept's creation, extraction) is adopted here, so it is never lost."""
    cur = await db.scalar(
        select(EntityDefinition).where(
            EntityDefinition.entity_id == entity.id, EntityDefinition.status == "current"
        )
    )
    text = (entity.short_definition or "").strip()
    if cur is not None and (cur.short == text or not text):
        if not text:  # cleared elsewhere
            cur.status, cur.decided_at = "replaced", datetime.now(UTC)
            return None
        return cur
    if not text:
        return None
    if cur is not None:
        cur.status, cur.decided_at = "replaced", datetime.now(UTC)
    adopted = EntityDefinition(
        entity_id=entity.id, short=text, origin="imported", status="current", edited=False
    )
    db.add(adopted)
    await db.flush()
    return adopted


async def _make_current(db: AsyncSession, entity: Entity, d: EntityDefinition) -> None:
    old = await current(db, entity)
    now = datetime.now(UTC)
    if old is not None and old.id != d.id:
        old.status, old.decided_at = "replaced", now
    d.status, d.decided_at = "current", now
    entity.short_definition = d.short


def _audit(db, actor: str, action: str, d: EntityDefinition, before=None, after=None) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            subject_table="entity_definitions",
            subject_id=str(d.id),
            before=before,
            after=after or {"entity_id": str(d.entity_id), "short": d.short, "origin": d.origin},
        )
    )


async def write(
    db: AsyncSession, entity: Entity, short: str, extended: str | None, note: str | None = None
) -> EntityDefinition:
    """The user's own definition: it becomes the current one at once."""
    short, extended = _clean(short, extended)
    old = await current(db, entity)
    if old is not None and old.short == short and (old.extended or None) == extended:
        return old
    d = EntityDefinition(
        entity_id=entity.id, short=short, extended=extended, origin="user", note=note
    )
    db.add(d)
    await db.flush()
    await _make_current(db, entity, d)
    _audit(db, "user", "definition.written", d, before={"short": old.short} if old else None)
    return d


async def clear(db: AsyncSession, entity: Entity) -> None:
    old = await current(db, entity)
    if old is not None:
        old.status, old.decided_at = "replaced", datetime.now(UTC)
        _audit(db, "user", "definition.cleared", old, before={"short": old.short}, after={})
    entity.short_definition = None


async def pending_count(db: AsyncSession, entity_id: uuid.UUID) -> int:
    rows = await db.scalars(
        select(EntityDefinition.id).where(
            EntityDefinition.entity_id == entity_id, EntityDefinition.status == "proposed"
        )
    )
    return len(rows.all())


async def propose(
    db: AsyncSession,
    entity: Entity,
    *,
    short: str,
    extended: str | None,
    origin: str,
    basis: str | None,
    citations: list[dict[str, Any]] | None = None,
    note: str | None = None,
    ai_run_id: uuid.UUID | None = None,
    actor: str = "ai",
) -> EntityDefinition:
    short, extended = _clean(short, extended)
    if await pending_count(db, entity.id) >= MAX_PROPOSALS:
        raise DefinitionError(f"ya hay {MAX_PROPOSALS} propuestas sin revisar para este concepto")
    d = EntityDefinition(
        entity_id=entity.id,
        short=short,
        extended=extended,
        origin=origin,
        status="proposed",
        basis=basis,
        citations=citations or [],
        note=(note or "").strip() or None,
        ai_run_id=ai_run_id,
    )
    db.add(d)
    await db.flush()
    _audit(db, actor, "definition.proposed", d)
    return d


async def accept(
    db: AsyncSession,
    entity: Entity,
    d: EntityDefinition,
    short: str | None = None,
    extended: str | None = None,
) -> EntityDefinition:
    """Accept a proposal, or bring back an earlier definition. Edited text keeps the
    proposal's citations and is marked as edited by the user."""
    if d.entity_id != entity.id:
        raise DefinitionError("la definición es de otro concepto")
    if d.status not in ("proposed", "replaced", "rejected"):
        raise DefinitionError("esta definición ya es la actual")
    if short is not None or extended is not None:
        new_short, new_ext = _clean(
            short if short is not None else d.short,
            extended if extended is not None else d.extended,
        )
        if (new_short, new_ext) != (d.short, d.extended):
            d.short, d.extended, d.edited = new_short, new_ext, True
    before = d.status
    await _make_current(db, entity, d)
    _audit(
        db,
        "user",
        "definition.accepted" if before == "proposed" else "definition.restored",
        d,
        before={"status": before},
    )
    return d


async def reject(db: AsyncSession, d: EntityDefinition) -> None:
    if d.status != "proposed":
        raise DefinitionError("solo se descarta una propuesta")
    d.status, d.decided_at = "rejected", datetime.now(UTC)
    _audit(db, "user", "definition.rejected", d, before={"status": "proposed"})


def out(d: EntityDefinition) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "short": d.short,
        "extended": d.extended,
        "origin": d.origin,
        "status": d.status,
        "basis": d.basis,
        "citations": d.citations or [],
        "note": d.note,
        "edited": d.edited,
        "ai_run_id": str(d.ai_run_id) if d.ai_run_id else None,
        "created_at": d.created_at,
        "decided_at": d.decided_at,
    }


async def from_sources(db: AsyncSession, entity: Entity) -> list[dict[str, Any]]:
    """How the sources define it: the concept's claims of type «definition», with who
    says it and where."""
    from kos.api.routes.knowledge import get_entity

    data = await get_entity(str(entity.id), db, None)
    rows = []
    for c in data["claims"]:
        if c["claim_type"] != "definition" or c["review_status"] == "rejected":
            continue
        rows.append(
            {
                "claim_id": c["id"],
                "text": c["text"],
                "review_status": c["review_status"],
                "provenance": c["provenance"],
                "person": c["person"],
                "evidence": c["evidence"][:3],
            }
        )
    return rows


async def generating(db: AsyncSession, entity_id: uuid.UUID) -> Job | None:
    return await db.scalar(
        select(Job)
        .where(
            Job.kind == DEFINE_KIND,
            Job.payload["entity_id"].astext == str(entity_id),
            Job.status.in_([JobStatus.pending, JobStatus.processing]),
        )
        .limit(1)
    )


async def last_failure(db: AsyncSession, entity_id: uuid.UUID) -> str | None:
    """Why the last generation failed, if it is newer than the last proposal."""
    job = await db.scalar(
        select(Job)
        .where(Job.kind == DEFINE_KIND, Job.payload["entity_id"].astext == str(entity_id))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if job is None or job.status != JobStatus.failed:
        return None
    return job.last_error or "la generación falló"


async def overview(db: AsyncSession, entity: Entity) -> dict[str, Any]:
    cur = await current(db, entity)
    rows = (
        await db.scalars(
            select(EntityDefinition)
            .where(
                EntityDefinition.entity_id == entity.id,
                EntityDefinition.status.in_(["proposed", "replaced"]),
            )
            .order_by(EntityDefinition.created_at.desc())
        )
    ).all()
    job = await generating(db, entity.id)
    return {
        "current": out(cur) if cur else None,
        "proposals": [out(d) for d in rows if d.status == "proposed"],
        "history": [out(d) for d in rows if d.status == "replaced"][:20],
        "from_sources": await from_sources(db, entity),
        "generating": str(job.id) if job else None,
        "failed": None if job else await last_failure(db, entity.id),
    }


def define_job(entity_id: uuid.UUID) -> Job:
    return Job(
        kind=DEFINE_KIND,
        status=JobStatus.pending,
        max_attempts=3,
        payload={"entity_id": str(entity_id)},
    )
