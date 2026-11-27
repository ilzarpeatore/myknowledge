# ruff: noqa: S608  (SQL joined only from constant fragments here; every value is a bound parameter)
"""Browse every concept: filters, importance order, pages, facets, bulk actions.

Importance puts what the library is about first and catalogue detail last:
3 per source that backs it, 2 per course whose materials mention it, 2 per
relationship (not counting «is a kind of / part of»: structure, not knowledge),
1 per claim, 1 for having a definition and 1 per concept of its family.
"""

import uuid
from typing import Any, Literal

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from kos.knowledge import service as ks
from kos.models import AuditLog, Discipline, Entity, EntityAlias, EntityDiscipline, EntityType

Sort = Literal["importance", "name", "recent"]
Flag = Literal[
    "no_definition", "single_source", "no_claims", "no_discipline", "ai_branch", "no_family"
]
BulkAction = Literal[
    "accept",
    "archive",
    "add_discipline",
    "remove_discipline",
    "confirm_branches",
    "set_type",
    "merge",
    "set_family",
    "remove_family",
]
MAX_BULK = 500

STATS = """
with ev as (
  select distinct ce.entity_id, sv.source_id
  from claim_entities ce
  join evidence e on e.claim_id = ce.claim_id
  join source_segments sg on sg.id = e.segment_id
  join source_versions sv on sv.id = sg.source_version_id
),
src as (select entity_id, count(*) n from ev group by 1),
crs as (
  select ev.entity_id, count(distinct cm.course_id) n
  from ev join course_materials cm on cm.source_id = ev.source_id group by 1
),
cl as (select entity_id, count(*) n from claim_entities group by 1),
rel as (
  select eid, count(*) n from (
    select source_entity_id eid from relationships
    where status <> 'rejected' and type_key not in ('is_a', 'part_of')
    union all
    select target_entity_id from relationships
    where status <> 'rejected' and type_key not in ('is_a', 'part_of')
  ) r group by 1
),
fam as (
  select parent_id eid, count(*) n from entities
  where parent_id is not null and status in ('active', 'proposed') group by 1
),
st as (
  select e.id, e.canonical_name, e.created_at,
    coalesce(src.n, 0) sources, coalesce(crs.n, 0) courses,
    coalesce(cl.n, 0) claims, coalesce(rel.n, 0) relationships, coalesce(fam.n, 0) members,
    3 * coalesce(src.n, 0) + 2 * coalesce(crs.n, 0) + 2 * coalesce(rel.n, 0)
      + coalesce(cl.n, 0) + coalesce(fam.n, 0)
      + case when coalesce(e.short_definition, '') <> '' then 1 else 0 end importance
  from entities e
  left join src on src.entity_id = e.id
  left join crs on crs.entity_id = e.id
  left join cl on cl.entity_id = e.id
  left join rel on rel.eid = e.id
  left join fam on fam.eid = e.id
)
"""

ORDER = {
    "importance": "st.importance desc, lower(st.canonical_name)",
    "name": "lower(st.canonical_name)",
    "recent": "st.created_at desc",
}


def _where(
    *,
    status: str,
    type_key: str | None,
    discipline: list[uuid.UUID] | Literal["none"] | None,
    source_id: uuid.UUID | None,
    flags: list[str],
    q_ids: list[uuid.UUID] | None,
    params: dict[str, Any],
    family: uuid.UUID | None = None,
    skip: str = "",
) -> str:
    """WHERE over «e» (entities) and «st» (stats). ``skip`` leaves one facet out."""
    w = ["e.status = any(:statuses)"]
    params["statuses"] = list(ks.LIVE) if status == "live" else [status]
    if type_key and skip != "type":
        w.append("e.type_key = :type_key")
        params["type_key"] = type_key
    if discipline and skip != "discipline":
        if discipline == "none":
            w.append("not exists (select 1 from entity_disciplines d where d.entity_id = e.id)")
        else:
            w.append(
                "exists (select 1 from entity_disciplines d where d.entity_id = e.id"
                " and d.discipline_id = any(:disciplines))"
            )
            params["disciplines"] = discipline
    if source_id:
        w.append(
            "exists (select 1 from claim_entities ce join evidence ev on ev.claim_id = ce.claim_id"
            " join source_segments sg on sg.id = ev.segment_id"
            " join source_versions sv on sv.id = sg.source_version_id"
            " where ce.entity_id = e.id and sv.source_id = :source_id)"
        )
        params["source_id"] = source_id
    if q_ids is not None:
        w.append("e.id = any(:q_ids)")
        params["q_ids"] = q_ids
    if family:  # the head and its members
        w.append("(e.parent_id = :family or e.id = :family)")
        params["family"] = family
    if "no_family" in flags:
        w.append("e.parent_id is null and st.members = 0")
    if "no_definition" in flags:
        w.append("coalesce(e.short_definition, '') = ''")
    if "single_source" in flags:
        w.append("st.sources = 1")
    if "no_claims" in flags:
        w.append("st.claims = 0")
    if "ai_branch" in flags:
        w.append(
            "exists (select 1 from entity_disciplines d where d.entity_id = e.id"
            " and d.origin = 'ai')"
        )
    if "no_discipline" in flags and skip != "discipline":
        w.append("not exists (select 1 from entity_disciplines d where d.entity_id = e.id)")
    return " and ".join(w)


async def _matching(db: AsyncSession, q: str) -> list[uuid.UUID]:
    n = ks.normalize(q)
    rows = await db.scalars(
        select(EntityAlias.entity_id)
        .where(
            or_(
                EntityAlias.alias_normalized.contains(n),
                func.similarity(EntityAlias.alias_normalized, n) > 0.3,
            )
        )
        .distinct()
    )
    return list(rows.all())


async def browse(
    db: AsyncSession,
    *,
    q: str | None = None,
    status: str = "live",
    type_key: str | None = None,
    discipline: list[uuid.UUID] | Literal["none"] | None = None,
    source_id: uuid.UUID | None = None,
    family: uuid.UUID | None = None,
    flags: list[str] | None = None,
    sort: Sort = "importance",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    flags = flags or []
    q_ids = await _matching(db, q) if q and q.strip() else None
    args = dict(
        status=status,
        type_key=type_key,
        discipline=discipline,
        source_id=source_id,
        flags=flags,
        q_ids=q_ids,
        family=family,
    )
    base = STATS + " select {cols} from entities e join st on st.id = e.id where {where}"

    params: dict[str, Any] = {}
    where = _where(**args, params=params)
    total = await db.scalar(text(base.format(cols="count(*)", where=where)), params)
    params["limit"], params["offset"] = page_size, (page - 1) * page_size
    rows = (
        await db.execute(
            text(
                base.format(
                    cols="e.id, st.sources, st.courses, st.claims, st.relationships,"
                    " st.members, st.importance",
                    where=where,
                )
                + f" order by {ORDER[sort]} limit :limit offset :offset"
            ),
            params,
        )
    ).all()
    ids = [r.id for r in rows]
    entities = {e.id: e for e in (await db.scalars(select(Entity).where(Entity.id.in_(ids))))}
    branches: dict[uuid.UUID, list[dict[str, str]]] = {}
    for eid, did, name, origin in await db.execute(
        select(EntityDiscipline.entity_id, Discipline.id, Discipline.name, EntityDiscipline.origin)
        .join(Discipline, Discipline.id == EntityDiscipline.discipline_id)
        .where(EntityDiscipline.entity_id.in_(ids))
        .order_by(Discipline.name)
    ):
        branches.setdefault(eid, []).append({"id": str(did), "name": name, "origin": origin})
    heads = {
        h.id: h
        for h in await db.scalars(
            select(Entity).where(
                Entity.id.in_({e.parent_id for e in entities.values() if e.parent_id})
            )
        )
    }
    items = []
    for r in rows:
        e = entities[r.id]
        head = heads.get(e.parent_id)
        family = None
        if head is not None:
            family = {"id": str(head.id), "slug": head.slug, "name": head.canonical_name}
        items.append(
            {
                "id": str(e.id),
                "slug": e.slug,
                "canonical_name": e.canonical_name,
                "type_key": e.type_key,
                "short_definition": e.short_definition,
                "status": e.status,
                "origin": e.origin,
                "sources": r.sources,
                "courses": r.courses,
                "claims": r.claims,
                "relationships": r.relationships,
                "importance": r.importance,
                "members": r.members,
                "family": family,
                "disciplines": branches.get(e.id, []),
            }
        )

    # facets: how many match each branch / type, the other filters applied
    fp: dict[str, Any] = {}
    fwhere = _where(**args, params=fp, skip="discipline")
    by_branch = (
        await db.execute(
            text(
                STATS + " select d.discipline_id, count(*) from entities e join st on st.id = e.id"
                " join entity_disciplines d on d.entity_id = e.id"
                f" where {fwhere} group by 1"
            ),
            fp,
        )
    ).all()
    none = await db.scalar(
        text(
            base.format(
                cols="count(*)",
                where=fwhere
                + " and not exists (select 1 from entity_disciplines d where d.entity_id = e.id)",
            )
        ),
        fp,
    )
    tp: dict[str, Any] = {}
    twhere = _where(**args, params=tp, skip="type")
    by_type = (
        await db.execute(
            text(
                STATS + " select e.type_key, count(*) from entities e join st on st.id = e.id"
                f" where {twhere} group by 1 order by 2 desc"
            ),
            tp,
        )
    ).all()
    return {
        "items": items,
        "total": total or 0,
        "page": page,
        "page_size": page_size,
        "facets": {
            "disciplines": {str(d): n for d, n in by_branch},
            "no_discipline": none or 0,
            "types": {k: n for k, n in by_type},
        },
    }


class BulkError(ValueError):
    pass


async def _family(db: AsyncSession, found: list[Entity], head_id: uuid.UUID | None) -> dict:
    """Put concepts in the family of ``head_id`` (or take them out of theirs)."""
    above: set[uuid.UUID] = set()
    if head_id is not None:
        head = await db.get(Entity, head_id)
        if head is None or head.status not in ks.LIVE:
            raise BulkError("elige el concepto que encabeza la familia")
        cur: Entity | None = head
        while cur is not None and cur.id not in above:  # no loops
            above.add(cur.id)
            cur = await db.get(Entity, cur.parent_id) if cur.parent_id else None
    changed = 0
    for e in found:
        if e.id in above or e.parent_id == head_id:
            continue
        e.parent_id = head_id
        changed += 1
    db.add(
        AuditLog(
            actor="user",
            action="entities.bulk_family",
            subject_table="entities",
            after={"ids": [str(e.id) for e in found], "head": str(head_id) if head_id else None},
        )
    )
    await db.commit()
    return {"changed": changed}


async def bulk(
    db: AsyncSession,
    ids: list[uuid.UUID],
    action: BulkAction,
    *,
    discipline_id: uuid.UUID | None = None,
    type_key: str | None = None,
    into: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Apply one action to many concepts. Returns how many changed."""
    ids = list(dict.fromkeys(ids))[:MAX_BULK]
    found = list(
        (
            await db.scalars(select(Entity).where(Entity.id.in_(ids), Entity.status.in_(ks.LIVE)))
        ).all()
    )
    if action in ("set_family", "remove_family"):
        return await _family(db, found, into if action == "set_family" else None)
    if action == "merge":
        if into is None:
            raise BulkError("falta el concepto en el que fusionar")
        merged = 0
        for e in found:
            if e.id != into:
                await ks.merge_entities(db, e.id, into)  # commits, with its own audit
                merged += 1
        return {"changed": merged}

    changed = 0
    if action == "confirm_branches":  # the AI's branches become the user's
        r = await db.execute(
            update(EntityDiscipline)
            .where(
                EntityDiscipline.entity_id.in_([e.id for e in found]),
                EntityDiscipline.origin == "ai",
            )
            .values(origin="user")
        )
        changed = r.rowcount or 0
    elif action in ("accept", "archive"):
        new = "active" if action == "accept" else "archived"
        for e in found:
            if e.status != new:
                e.status = new
                changed += 1
    elif action == "set_type":
        if not type_key or await db.get(EntityType, type_key) is None:
            raise BulkError("tipo desconocido")
        for e in found:
            if e.type_key != type_key:
                e.type_key = type_key
                changed += 1
    else:
        if discipline_id is None or await db.get(Discipline, discipline_id) is None:
            raise BulkError("rama desconocida")
        have = set(
            (
                await db.scalars(
                    select(EntityDiscipline.entity_id).where(
                        EntityDiscipline.discipline_id == discipline_id,
                        EntityDiscipline.entity_id.in_([e.id for e in found]),
                    )
                )
            ).all()
        )
        if action == "add_discipline":
            if have:  # already there (maybe by the AI): now it is the user's choice
                await db.execute(
                    update(EntityDiscipline)
                    .where(
                        EntityDiscipline.discipline_id == discipline_id,
                        EntityDiscipline.entity_id.in_(have),
                    )
                    .values(origin="user")
                )
            new_rows = [e.id for e in found if e.id not in have]
            db.add_all(EntityDiscipline(entity_id=i, discipline_id=discipline_id) for i in new_rows)
            changed = len(new_rows)
        else:
            await db.execute(
                delete(EntityDiscipline).where(
                    EntityDiscipline.discipline_id == discipline_id,
                    EntityDiscipline.entity_id.in_(have),
                )
            )
            changed = len(have)
    db.add(
        AuditLog(
            actor="user",
            action=f"entities.bulk_{action}",
            subject_table="entities",
            after={
                "ids": [str(e.id) for e in found],
                "discipline_id": str(discipline_id) if discipline_id else None,
                "type_key": type_key,
                "changed": changed,
            },
        )
    )
    await db.commit()
    return {"changed": changed}
