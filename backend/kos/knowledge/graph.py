"""Graph views over the knowledge model: contextual neighbourhood and hierarchy tree.

The neighbourhood is built breadth-first from a centre entity, one SQL query per
level, so it never loads the whole graph; filters apply while expanding.
The tree uses the hierarchical relationship types (is_a, part_of): a child is
the *source* of "child is_a parent" / "child part_of parent".
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.models import (
    ClaimEntity,
    Discipline,
    Entity,
    EntityDiscipline,
    Evidence,
    Relationship,
    RelationshipType,
    SourceSegment,
    SourceVersion,
)
from kos.models.knowledge import Claim

HIERARCHY_TYPES = ("is_a", "part_of")
LIVE = ("active", "proposed")


@dataclass
class GraphFilters:
    relationship_types: list[str] | None = None
    statuses: tuple[str, ...] = ("verified", "proposed")
    discipline_ids: list[uuid.UUID] | None = None
    source_ids: list[uuid.UUID] | None = None
    person_ids: list[uuid.UUID] | None = None


@dataclass
class Graph:
    nodes: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    edges: dict[uuid.UUID, dict[str, Any]] = field(default_factory=dict)
    truncated: bool = False


async def _allowed_nodes(db: AsyncSession, ids: set[uuid.UUID], f: GraphFilters) -> set[uuid.UUID]:
    """Subset of ``ids`` that pass the node filters (discipline, source, author)."""
    allowed = set(ids)
    if f.discipline_ids:
        rows = await db.scalars(
            select(EntityDiscipline.entity_id).where(
                EntityDiscipline.entity_id.in_(allowed),
                EntityDiscipline.discipline_id.in_(f.discipline_ids),
            )
        )
        allowed &= set(rows.all())
    if f.source_ids and allowed:
        rows = await db.scalars(
            select(ClaimEntity.entity_id)
            .join(Evidence, Evidence.claim_id == ClaimEntity.claim_id)
            .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
            .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
            .where(ClaimEntity.entity_id.in_(allowed), SourceVersion.source_id.in_(f.source_ids))
        )
        allowed &= set(rows.all())
    if f.person_ids and allowed:
        rows = await db.scalars(
            select(ClaimEntity.entity_id)
            .join(Claim, Claim.id == ClaimEntity.claim_id)
            .where(ClaimEntity.entity_id.in_(allowed), Claim.attributed_person_id.in_(f.person_ids))
        )
        allowed &= set(rows.all())
    return allowed


async def neighbourhood(
    db: AsyncSession,
    center: uuid.UUID,
    depth: int = 1,
    f: GraphFilters | None = None,
    max_nodes: int = 300,
) -> Graph:
    f = f or GraphFilters()
    g = Graph()
    labels = {
        t.key: (t.label, t.inverse_label, t.is_symmetric)
        for t in (await db.scalars(select(RelationshipType))).all()
    }
    seen = {center}
    frontier = {center}
    for _ in range(max(1, min(depth, 3))):
        if not frontier:
            break
        stmt = select(Relationship).where(
            or_(
                Relationship.source_entity_id.in_(frontier),
                Relationship.target_entity_id.in_(frontier),
            ),
            Relationship.status.in_(f.statuses),
        )
        if f.relationship_types:
            stmt = stmt.where(Relationship.type_key.in_(f.relationship_types))
        rels = (await db.scalars(stmt)).all()
        candidates = {
            r.target_entity_id if r.source_entity_id in frontier else r.source_entity_id
            for r in rels
        } - seen
        live = (
            set(
                (
                    await db.scalars(
                        select(Entity.id).where(Entity.id.in_(candidates), Entity.status.in_(LIVE))
                    )
                ).all()
            )
            if candidates
            else set()
        )
        allowed = await _allowed_nodes(db, live, f)
        room = max_nodes - len(seen)
        if len(allowed) > room:
            g.truncated = True
            allowed = set(sorted(allowed, key=str)[:room])
        for r in rels:
            a, b = r.source_entity_id, r.target_entity_id
            if (a in seen or a in allowed) and (b in seen or b in allowed):
                g.edges[r.id] = {
                    "id": str(r.id),
                    "source": str(a),
                    "target": str(b),
                    "type_key": r.type_key,
                    "label": labels[r.type_key][0],
                    "status": r.status,
                    "origin": r.origin,
                }
        seen |= allowed
        frontier = allowed

    entities = (await db.scalars(select(Entity).where(Entity.id.in_(seen)))).all()
    disc = defaultdict(list)
    for eid, did in (
        await db.execute(
            select(EntityDiscipline.entity_id, EntityDiscipline.discipline_id).where(
                EntityDiscipline.entity_id.in_(seen)
            )
        )
    ).all():
        disc[eid].append(str(did))
    # Total degree lets the UI show which nodes can be expanded further.
    degree: dict[uuid.UUID, int] = defaultdict(int)
    for col in (Relationship.source_entity_id, Relationship.target_entity_id):
        for (eid,) in (
            await db.execute(select(col).where(col.in_(seen), Relationship.status.in_(f.statuses)))
        ).all():
            degree[eid] += 1
    for e in entities:
        g.nodes[e.id] = {
            "id": str(e.id),
            "slug": e.slug,
            "name": e.canonical_name,
            "type_key": e.type_key,
            "status": e.status,
            "center": e.id == center,
            "disciplines": disc[e.id],
            "degree": degree[e.id],
        }
    return g


async def tree(db: AsyncSession, root_discipline: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """Disciplines (as a tree) with their entities, each entity with its is_a/part_of
    descendants. Entities without a discipline appear under a 'Sin disciplina' node."""
    disciplines = (await db.scalars(select(Discipline).order_by(Discipline.name))).all()
    entities = {
        e.id: e for e in (await db.scalars(select(Entity).where(Entity.status.in_(LIVE)))).all()
    }
    children: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = defaultdict(list)
    has_parent: set[uuid.UUID] = set()
    for r in (
        await db.scalars(
            select(Relationship).where(
                Relationship.type_key.in_(HIERARCHY_TYPES), Relationship.status != "rejected"
            )
        )
    ).all():
        if r.source_entity_id in entities and r.target_entity_id in entities:
            children[r.target_entity_id].append((r.source_entity_id, r.type_key))
            has_parent.add(r.source_entity_id)
    for e in entities.values():  # families (organisation, not relationships)
        if e.parent_id in entities and e.parent_id != e.id:
            if all(c != e.id for c, _ in children[e.parent_id]):
                children[e.parent_id].append((e.id, "family"))
                has_parent.add(e.id)
    assigned: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for eid, did in (
        await db.execute(select(EntityDiscipline.entity_id, EntityDiscipline.discipline_id))
    ).all():
        if eid in entities:
            assigned[did].add(eid)
    entity_has_discipline = {eid for ids in assigned.values() for eid in ids}

    def entity_node(eid: uuid.UUID, via: str | None, path: frozenset) -> dict[str, Any]:
        e = entities[eid]
        kids = sorted(
            (c for c in children.get(eid, []) if c[0] not in path),
            key=lambda c: entities[c[0]].canonical_name.lower(),
        )
        return {
            "kind": "entity",
            "id": str(eid),
            "slug": e.slug,
            "name": e.canonical_name,
            "type_key": e.type_key,
            "via": via,
            "children": [entity_node(c, t, path | {c}) for c, t in kids],
        }

    def roots_of(ids: set[uuid.UUID]) -> list[uuid.UUID]:
        """Entities shown at the top of a group: those whose parent is not in the group.
        Members of a cycle with no such root get one (by name), so nothing disappears."""
        parents = defaultdict(set)
        for p, kids in children.items():
            for c, _ in kids:
                parents[c].add(p)

        def name(i: uuid.UUID) -> str:
            return entities[i].canonical_name.lower()

        roots = sorted((i for i in ids if not (parents[i] & ids)), key=name)
        reached: set[uuid.UUID] = set()
        stack = list(roots)
        while True:
            while stack:
                node = stack.pop()
                if node in reached:
                    continue
                reached.add(node)
                stack.extend(c for c, _ in children.get(node, []) if c in ids)
            rest = sorted(ids - reached, key=name)
            if not rest:
                return roots
            roots.append(rest[0])
            stack.append(rest[0])

    by_parent: dict[uuid.UUID | None, list[Discipline]] = defaultdict(list)
    for d in disciplines:
        by_parent[d.parent_id].append(d)

    def discipline_node(d: Discipline) -> dict[str, Any]:
        return {
            "kind": "discipline",
            "id": str(d.id),
            "name": d.name,
            "children": [discipline_node(c) for c in by_parent[d.id]]
            + [entity_node(e, None, frozenset({e})) for e in roots_of(assigned[d.id])],
        }

    if root_discipline:
        top = [d for d in disciplines if d.id == root_discipline]
        return [discipline_node(d) for d in top]
    out = [discipline_node(d) for d in by_parent[None]]
    # Entities without a discipline go under "Sin disciplina", unless they already
    # hang below an entity that has one (they are shown there).
    covered: set[uuid.UUID] = set()
    stack = list(entity_has_discipline)
    while stack:
        for c, _ in children.get(stack.pop(), []):
            if c not in covered:
                covered.add(c)
                stack.append(c)
    loose = {eid for eid in entities if eid not in entity_has_discipline and eid not in covered}
    loose_roots = roots_of(loose)
    if loose_roots:
        out.append(
            {
                "kind": "discipline",
                "id": None,
                "name": "Sin disciplina",
                "children": [entity_node(e, None, frozenset({e})) for e in loose_roots],
            }
        )
    return out
