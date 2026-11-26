"""Knowledge operations. Deterministic rules only; AI pipelines call these too.

Duplicate detection follows the layered approach in Graphiti's dedup helpers
(exact normalized match first, then fuzzy similarity, the expensive judgement
last), implemented here with PostgreSQL pg_trgm trigram similarity. Nothing is
merged automatically: likely duplicates become review tasks.
"""

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kos.models import (
    AuditLog,
    Claim,
    ClaimEntity,
    Entity,
    EntityAlias,
    EntityMerge,
    EntityType,
    Evidence,
    Relationship,
    RelationshipEvidence,
    RelationshipType,
    ReviewTask,
    SourceSegment,
)

LIVE = ("active", "proposed")


class KnowledgeError(Exception):
    pass


class NotFound(KnowledgeError):
    pass


class DuplicateName(KnowledgeError):
    def __init__(self, existing: Entity):
        super().__init__(f"ya existe «{existing.canonical_name}» con ese nombre o alias")
        self.existing = existing


class QuoteNotFound(KnowledgeError):
    pass


class InvalidOperation(KnowledgeError):
    pass


# ---- names ----------------------------------------------------------------------------


def normalize(name: str) -> str:
    """Lowercase, no accents, punctuation as spaces, single spaces."""
    s = unicodedata.normalize("NFKD", name.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slugify(name: str) -> str:
    return normalize(name).replace(" ", "-")[:280] or "entidad"


async def _unique_slug(db: AsyncSession, name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while await db.scalar(select(Entity.id).where(Entity.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


async def entity_with_alias(
    db: AsyncSession, name: str, exclude: uuid.UUID | None = None
) -> Entity | None:
    stmt = (
        select(Entity)
        .join(EntityAlias, EntityAlias.entity_id == Entity.id)
        .where(EntityAlias.alias_normalized == normalize(name), Entity.status.in_(LIVE))
    )
    if exclude:
        stmt = stmt.where(Entity.id != exclude)
    return await db.scalar(stmt.limit(1))


async def candidates(
    db: AsyncSession, name: str, limit: int = 10, min_similarity: float = 0.3
) -> list[dict[str, Any]]:
    """Live entities whose name or aliases resemble ``name`` (trigram similarity)."""
    q = normalize(name)
    rows = await db.execute(
        select(
            Entity.id,
            Entity.canonical_name,
            Entity.type_key,
            func.max(func.similarity(EntityAlias.alias_normalized, q)).label("score"),
        )
        .join(EntityAlias, EntityAlias.entity_id == Entity.id)
        .where(
            Entity.status.in_(LIVE),
            func.similarity(EntityAlias.alias_normalized, q) >= min_similarity,
        )
        .group_by(Entity.id)
        .order_by(text("score DESC"))
        .limit(limit)
    )
    return [dict(r._mapping) for r in rows]


# ---- entities and aliases -------------------------------------------------------------


async def create_entity(
    db: AsyncSession,
    *,
    type_key: str,
    name: str,
    definition: str | None = None,
    aliases: list[tuple[str, str | None]] | None = None,  # (alias, language)
    origin: str = "user",
    status: str = "active",
) -> Entity:
    """Create an entity. Refuses an exact name/alias match with a live entity
    (raise DuplicateName); similar names are allowed but can be checked with
    ``candidates`` first."""
    name = name.strip()
    if not name:
        raise InvalidOperation("el nombre está vacío")
    if await db.get(EntityType, type_key) is None:
        raise InvalidOperation(f"tipo desconocido: {type_key}")
    for n in [name, *(a for a, _ in aliases or [])]:
        if existing := await entity_with_alias(db, n):
            raise DuplicateName(existing)
    entity = Entity(
        type_key=type_key,
        canonical_name=name,
        slug=await _unique_slug(db, name),
        short_definition=definition,
        origin=origin,
        status=status,
    )
    db.add(entity)
    await db.flush()
    seen = {normalize(name)}
    db.add(
        EntityAlias(
            entity_id=entity.id, alias=name, alias_normalized=normalize(name), kind="canonical"
        )
    )
    for alias, lang in aliases or []:
        if normalize(alias) and normalize(alias) not in seen:
            seen.add(normalize(alias))
            db.add(
                EntityAlias(
                    entity_id=entity.id,
                    alias=alias.strip(),
                    alias_normalized=normalize(alias),
                    language=lang,
                    kind="synonym",
                )
            )
    db.add(
        AuditLog(
            actor=origin,
            action="entity.created",
            subject_table="entities",
            subject_id=str(entity.id),
            after={"name": name, "type": type_key},
        )
    )
    await db.commit()
    return entity


async def add_alias(
    db: AsyncSession,
    entity_id: uuid.UUID,
    alias: str,
    *,
    kind: str = "synonym",
    language: str | None = None,
    source_segment_id: uuid.UUID | None = None,
) -> EntityAlias:
    entity = await db.get(Entity, entity_id)
    if entity is None or entity.status not in LIVE:
        raise NotFound("concepto no encontrado")
    norm = normalize(alias)
    if not norm:
        raise InvalidOperation("alias vacío")
    if other := await entity_with_alias(db, alias, exclude=entity_id):
        raise DuplicateName(other)
    existing = await db.scalar(
        select(EntityAlias).where(
            EntityAlias.entity_id == entity_id, EntityAlias.alias_normalized == norm
        )
    )
    if existing:
        return existing
    row = EntityAlias(
        entity_id=entity_id,
        alias=alias.strip(),
        alias_normalized=norm,
        kind=kind,
        language=language,
        source_segment_id=source_segment_id,
    )
    db.add(row)
    await db.commit()
    return row


# ---- claims and evidence --------------------------------------------------------------


async def add_claim(
    db: AsyncSession,
    *,
    text_: str,
    claim_type: str,
    entities: list[tuple[uuid.UUID, str]],  # (entity_id, role)
    evidence: list[tuple[uuid.UUID, str]],  # (segment_id, exact quote)
    person_id: uuid.UUID | None = None,
    publication_id: uuid.UUID | None = None,
    context: str | None = None,
    origin: str = "user",
    confidence: float | None = None,
    ai_run_id: uuid.UUID | None = None,
) -> Claim:
    """A claim must cite at least one quote, and every quote must appear verbatim
    in its segment: nothing is attributed to a source that does not say it."""
    if not evidence:
        raise InvalidOperation("una afirmación necesita al menos una evidencia")
    if not entities:
        raise InvalidOperation("una afirmación debe referirse a algún concepto")
    located: list[tuple[uuid.UUID, str, int]] = []
    for segment_id, quote in evidence:
        seg_text = await db.scalar(select(SourceSegment.text).where(SourceSegment.id == segment_id))
        if seg_text is None:
            raise NotFound(f"fragmento {segment_id} no encontrado")
        quote = quote.strip()
        pos = seg_text.find(quote) if quote else -1
        if pos < 0:
            raise QuoteNotFound("la cita no aparece literalmente en el fragmento")
        located.append((segment_id, quote, pos))
    for entity_id, _ in entities:
        e = await db.get(Entity, entity_id)
        if e is None or e.status not in LIVE:
            raise NotFound(f"concepto {entity_id} no encontrado")

    claim = Claim(
        text=text_.strip(),
        claim_type=claim_type,
        attributed_person_id=person_id,
        publication_id=publication_id,
        context=context,
        origin=origin,
        extraction_confidence=confidence,
        ai_run_id=ai_run_id,
        review_status="accepted" if origin == "user" else "pending",
    )
    db.add(claim)
    await db.flush()
    for entity_id, role in dict(entities).items():
        db.add(ClaimEntity(claim_id=claim.id, entity_id=entity_id, role=role))
    for segment_id, quote, pos in located:
        db.add(
            Evidence(
                claim_id=claim.id,
                segment_id=segment_id,
                quote=quote,
                quote_char_start=pos,
                quote_char_end=pos + len(quote),
            )
        )
    db.add(
        AuditLog(
            actor=origin, action="claim.created", subject_table="claims", subject_id=str(claim.id)
        )
    )
    await db.commit()
    return claim


# ---- relationships --------------------------------------------------------------------


async def add_relationship(
    db: AsyncSession,
    *,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    type_key: str,
    evidence: list[dict[str, Any]],  # [{"claim_id"| "segment_id", "note"}]
    description: str | None = None,
    conditions: str | None = None,
    origin: str = "user",
    confidence: float | None = None,
    ai_run_id: uuid.UUID | None = None,
) -> Relationship:
    """Every relationship needs evidence. User-created ones start verified; AI ones
    start proposed and go to review. Symmetric types are stored once."""
    rtype = await db.get(RelationshipType, type_key)
    if rtype is None:
        raise InvalidOperation(f"tipo de relación desconocido: {type_key}")
    if source_id == target_id:
        raise InvalidOperation("una relación necesita dos conceptos distintos")
    if not evidence or not all(ev.get("claim_id") or ev.get("segment_id") for ev in evidence):
        raise InvalidOperation("una relación necesita evidencia (afirmación o fragmento)")
    for eid in (source_id, target_id):
        e = await db.get(Entity, eid)
        if e is None or e.status not in LIVE:
            raise NotFound(f"concepto {eid} no encontrado")
    if rtype.is_symmetric and str(source_id) > str(target_id):
        source_id, target_id = target_id, source_id

    existing = await db.scalar(
        select(Relationship).where(
            Relationship.source_entity_id == source_id,
            Relationship.target_entity_id == target_id,
            Relationship.type_key == type_key,
        )
    )
    rel = existing or Relationship(
        source_entity_id=source_id,
        target_entity_id=target_id,
        type_key=type_key,
        description=description,
        conditions=conditions,
        origin=origin,
        confidence=confidence,
        ai_run_id=ai_run_id,
        status="verified" if origin == "user" else "proposed",
    )
    if existing is None:
        db.add(rel)
        await db.flush()
    for ev in evidence:
        db.add(
            RelationshipEvidence(
                relationship_id=rel.id,
                claim_id=ev.get("claim_id"),
                segment_id=ev.get("segment_id"),
                note=ev.get("note"),
            )
        )
    if origin != "user" and existing is None:
        db.add(
            ReviewTask(
                kind="relationship",
                subject_key=str(rel.id),
                payload={"relationship_id": str(rel.id)},
            )
        )
    db.add(
        AuditLog(
            actor=origin,
            action="relationship.created",
            subject_table="relationships",
            subject_id=str(rel.id),
        )
    )
    await db.commit()
    return rel


async def set_relationship_status(db: AsyncSession, rel_id: uuid.UUID, status: str) -> Relationship:
    if status not in ("proposed", "verified", "rejected"):
        raise InvalidOperation("estado no válido")
    rel = await db.get(Relationship, rel_id)
    if rel is None:
        raise NotFound("relación no encontrada")
    before = rel.status
    rel.status = status
    rel.reviewed_at = datetime.now(UTC)
    await db.execute(
        update(ReviewTask)
        .where(
            ReviewTask.kind == "relationship",
            ReviewTask.subject_key == str(rel_id),
            ReviewTask.status == "open",
        )
        .values(
            status="accepted" if status == "verified" else "rejected", resolved_at=datetime.now(UTC)
        )
    )
    db.add(
        AuditLog(
            actor="user",
            action="relationship.status",
            subject_table="relationships",
            subject_id=str(rel_id),
            before={"status": before},
            after={"status": status},
        )
    )
    await db.commit()
    return rel


# ---- merge / undo ---------------------------------------------------------------------


def _row(obj: Any) -> dict[str, Any]:
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns}


def _json(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, uuid.UUID | datetime) else v) for k, v in d.items()}


async def merge_entities(
    db: AsyncSession, source_id: uuid.UUID, target_id: uuid.UUID, actor: str = "user"
) -> EntityMerge:
    """Fold ``source`` into ``target``. Aliases, claim links and relationships move
    to the target; exact duplicates are dropped. Everything moved or dropped is
    recorded so ``undo_merge`` restores the previous state."""
    if source_id == target_id:
        raise InvalidOperation("no se puede fusionar un concepto consigo mismo")
    source, target = await db.get(Entity, source_id), await db.get(Entity, target_id)
    if source is None or target is None or source.status not in LIVE or target.status not in LIVE:
        raise NotFound("concepto no encontrado o no activo")

    moved: dict[str, list[Any]] = {
        "aliases_moved": [],
        "aliases_dropped": [],
        "claim_links_moved": [],
        "claim_links_dropped": [],
        "rels_repointed": [],
        "rels_dropped": [],
        "rel_evidence_moved": [],
    }
    target_aliases = set(
        (
            await db.scalars(
                select(EntityAlias.alias_normalized).where(EntityAlias.entity_id == target_id)
            )
        ).all()
    )
    for a in (
        await db.scalars(select(EntityAlias).where(EntityAlias.entity_id == source_id))
    ).all():
        if a.alias_normalized in target_aliases:
            moved["aliases_dropped"].append(_json(_row(a)))
            await db.delete(a)
        else:
            moved["aliases_moved"].append({"id": str(a.id), "kind": a.kind})
            a.entity_id = target_id
            if a.kind == "canonical":
                a.kind = "synonym"

    target_claims = set(
        (
            await db.scalars(select(ClaimEntity.claim_id).where(ClaimEntity.entity_id == target_id))
        ).all()
    )
    for link in (
        await db.scalars(select(ClaimEntity).where(ClaimEntity.entity_id == source_id))
    ).all():
        if link.claim_id in target_claims:
            moved["claim_links_dropped"].append(_json(_row(link)))
            await db.delete(link)
        else:
            moved["claim_links_moved"].append(str(link.claim_id))
            await db.execute(
                update(ClaimEntity)
                .where(ClaimEntity.claim_id == link.claim_id, ClaimEntity.entity_id == source_id)
                .values(entity_id=target_id)
            )

    rels = (
        await db.scalars(
            select(Relationship).where(
                or_(
                    Relationship.source_entity_id == source_id,
                    Relationship.target_entity_id == source_id,
                )
            )
        )
    ).all()
    for rel in rels:
        new_src = target_id if rel.source_entity_id == source_id else rel.source_entity_id
        new_tgt = target_id if rel.target_entity_id == source_id else rel.target_entity_id
        evidence = (
            await db.scalars(
                select(RelationshipEvidence).where(RelationshipEvidence.relationship_id == rel.id)
            )
        ).all()
        clash = None
        if new_src != new_tgt:
            clash = await db.scalar(
                select(Relationship).where(
                    Relationship.source_entity_id == new_src,
                    Relationship.target_entity_id == new_tgt,
                    Relationship.type_key == rel.type_key,
                    Relationship.id != rel.id,
                )
            )
        if new_src == new_tgt or clash is not None:
            # Self-loop after merging, or the same relationship already exists on the target.
            record = {"rel": _json(_row(rel)), "evidence": [str(e.id) for e in evidence]}
            if clash is not None:
                for e in evidence:
                    moved["rel_evidence_moved"].append({"id": str(e.id), "from": str(rel.id)})
                    e.relationship_id = clash.id
            else:
                record["evidence_rows"] = [_json(_row(e)) for e in evidence]
            moved["rels_dropped"].append(record)
            await db.flush()
            await db.delete(rel)
        else:
            moved["rels_repointed"].append(
                {
                    "id": str(rel.id),
                    "source": rel.source_entity_id == source_id,
                    "target": rel.target_entity_id == source_id,
                }
            )
            rel.source_entity_id, rel.target_entity_id = new_src, new_tgt

    source.status = "merged"
    source.merged_into_id = target_id
    source.updated_at = datetime.now(UTC)
    merge = EntityMerge(source_entity_id=source_id, target_entity_id=target_id, moved=moved)
    db.add(merge)
    await db.flush()
    db.add(
        AuditLog(
            actor=actor,
            action="entity.merged",
            subject_table="entities",
            subject_id=str(source_id),
            after={"into": str(target_id), "merge_id": str(merge.id)},
        )
    )
    await db.commit()
    return merge


async def undo_merge(db: AsyncSession, merge_id: uuid.UUID) -> Entity:
    merge = await db.get(EntityMerge, merge_id)
    if merge is None or merge.undone_at is not None:
        raise NotFound("fusión no encontrada o ya deshecha")
    source = await db.get(Entity, merge.source_entity_id)
    if source.status != "merged" or source.merged_into_id != merge.target_entity_id:
        raise InvalidOperation("el concepto ha cambiado desde la fusión; no se puede deshacer")
    m, src, tgt = merge.moved, merge.source_entity_id, merge.target_entity_id

    # Order matters: restore dropped relationships before moving evidence back to them.
    for rec in m["rels_dropped"]:
        await db.execute(insert(Relationship).values(**_restore(rec["rel"], Relationship)))
        for ev in rec.get("evidence_rows", []):
            await db.execute(
                insert(RelationshipEvidence).values(**_restore(ev, RelationshipEvidence))
            )
    for mv in m["rel_evidence_moved"]:
        await db.execute(
            update(RelationshipEvidence)
            .where(RelationshipEvidence.id == uuid.UUID(mv["id"]))
            .values(relationship_id=uuid.UUID(mv["from"]))
        )
    for rp in m["rels_repointed"]:
        values = {}
        if rp["source"]:
            values["source_entity_id"] = src
        if rp["target"]:
            values["target_entity_id"] = src
        await db.execute(
            update(Relationship).where(Relationship.id == uuid.UUID(rp["id"])).values(**values)
        )
    for claim_id in m["claim_links_moved"]:
        await db.execute(
            update(ClaimEntity)
            .where(ClaimEntity.claim_id == uuid.UUID(claim_id), ClaimEntity.entity_id == tgt)
            .values(entity_id=src)
        )
    for row in m["claim_links_dropped"]:
        await db.execute(insert(ClaimEntity).values(**_restore(row, ClaimEntity)))
    for a in m["aliases_moved"]:
        await db.execute(
            update(EntityAlias)
            .where(EntityAlias.id == uuid.UUID(a["id"]))
            .values(entity_id=src, kind=a["kind"])
        )
    for row in m["aliases_dropped"]:
        await db.execute(insert(EntityAlias).values(**_restore(row, EntityAlias)))

    source.status = "active"
    source.merged_into_id = None
    source.updated_at = datetime.now(UTC)
    merge.undone_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor="user",
            action="entity.merge_undone",
            subject_table="entities",
            subject_id=str(src),
            after={"merge_id": str(merge_id)},
        )
    )
    await db.commit()
    return source


def _restore(row: dict[str, Any], model: Any) -> dict[str, Any]:
    out = {}
    for col in model.__table__.columns:
        if col.key not in row:
            continue
        v = row[col.key]
        if v is not None and isinstance(col.type.python_type, type):
            try:
                if col.type.python_type is uuid.UUID:
                    v = uuid.UUID(v)
                elif col.type.python_type is datetime:
                    v = datetime.fromisoformat(v)
            except NotImplementedError:
                pass
        out[col.key] = v
    return out


# ---- review queue: duplicates ---------------------------------------------------------


async def scan_duplicates(db: AsyncSession, min_similarity: float = 0.5) -> int:
    """Create review tasks for pairs of live entities with similar names/aliases.
    Pairs a human already rejected are not proposed again."""
    pairs = await db.execute(
        text(
            """
        SELECT a.entity_id AS a, b.entity_id AS b,
               max(similarity(a.alias_normalized, b.alias_normalized)) AS score
        FROM entity_aliases a
        JOIN entity_aliases b ON a.entity_id < b.entity_id
             AND a.alias_normalized % b.alias_normalized
        JOIN entities ea ON ea.id = a.entity_id AND ea.status IN ('active', 'proposed')
        JOIN entities eb ON eb.id = b.entity_id AND eb.status IN ('active', 'proposed')
        GROUP BY a.entity_id, b.entity_id
        HAVING max(similarity(a.alias_normalized, b.alias_normalized)) >= :min
        """
        ),
        {"min": min_similarity},
    )
    created = 0
    for a, b, score in pairs.all():
        key = f"{a}:{b}"
        seen = await db.scalar(
            select(ReviewTask.id).where(
                ReviewTask.kind == "entity_merge",
                ReviewTask.subject_key == key,
                ReviewTask.status.in_(["open", "rejected"]),
            )
        )
        if seen:
            continue
        ea, eb = await db.get(Entity, a), await db.get(Entity, b)
        db.add(
            ReviewTask(
                kind="entity_merge",
                subject_key=key,
                payload={
                    "entity_a": str(a),
                    "entity_b": str(b),
                    "name_a": ea.canonical_name,
                    "name_b": eb.canonical_name,
                    "score": round(float(score), 3),
                },
            )
        )
        created += 1
    try:
        await db.commit()
    except IntegrityError:  # a concurrent scan created the same task
        await db.rollback()
    return created


async def resolve_review_task(
    db: AsyncSession,
    task_id: uuid.UUID,
    action: str,
    keep_entity_id: uuid.UUID | None = None,
    note: str | None = None,
) -> ReviewTask:
    task = await db.get(ReviewTask, task_id)
    if task is None or task.status != "open":
        raise NotFound("tarea no encontrada o ya resuelta")
    if action not in ("accept", "reject", "dismiss"):
        raise InvalidOperation("acción no válida")
    if task.kind == "entity_merge" and action == "accept":
        a, b = uuid.UUID(task.payload["entity_a"]), uuid.UUID(task.payload["entity_b"])
        if keep_entity_id not in (a, b):
            raise InvalidOperation("indica qué concepto se conserva")
        merge = await merge_entities(db, b if keep_entity_id == a else a, keep_entity_id)
        task.payload = {**task.payload, "merge_id": str(merge.id)}
    elif task.kind == "relationship" and action in ("accept", "reject"):
        await set_relationship_status(
            db,
            uuid.UUID(task.payload["relationship_id"]),
            "verified" if action == "accept" else "rejected",
        )
        task = await db.get(ReviewTask, task_id)
        return task
    task.status = {"accept": "accepted", "reject": "rejected", "dismiss": "dismissed"}[action]
    task.resolution_note = note
    task.resolved_at = datetime.now(UTC)
    await db.commit()
    return task


async def segments_cited(db: AsyncSession, version_id: uuid.UUID) -> bool:
    """True if any segment of this version backs knowledge (evidence, relationship
    evidence or an alias). Such segments must never be deleted."""
    seg_ids = select(SourceSegment.id).where(SourceSegment.source_version_id == version_id)
    for col in (
        Evidence.segment_id,
        RelationshipEvidence.segment_id,
        EntityAlias.source_segment_id,
    ):
        if await db.scalar(select(func.count()).where(col.in_(seg_ids))):
            return True
    return False
