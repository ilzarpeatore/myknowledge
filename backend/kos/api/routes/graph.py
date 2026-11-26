"""Graph, tree and concept-discipline assignment endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, select

from kos.api.deps import DbDep, UserDep
from kos.knowledge import graph as kg
from kos.models import AuditLog, Discipline, Entity, EntityDiscipline

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/graph/neighbourhood")
async def neighbourhood(
    db: DbDep,
    _: UserDep,
    entity: uuid.UUID,
    depth: Annotated[int, Query(ge=1, le=3)] = 1,
    types: Annotated[list[str] | None, Query()] = None,
    include_proposed: bool = True,
    discipline: Annotated[list[uuid.UUID] | None, Query()] = None,
    source: Annotated[list[uuid.UUID] | None, Query()] = None,
    person: Annotated[list[uuid.UUID] | None, Query()] = None,
    max_nodes: Annotated[int, Query(ge=10, le=1000)] = 300,
):
    if await db.get(Entity, entity) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concepto no encontrado")
    f = kg.GraphFilters(
        relationship_types=types or None,
        statuses=("verified", "proposed") if include_proposed else ("verified",),
        discipline_ids=discipline,
        source_ids=source,
        person_ids=person,
    )
    g = await kg.neighbourhood(db, entity, depth, f, max_nodes)
    return {
        "nodes": list(g.nodes.values()),
        "edges": list(g.edges.values()),
        "truncated": g.truncated,
    }


@router.get("/graph/tree")
async def tree(db: DbDep, _: UserDep, discipline: uuid.UUID | None = None):
    return await kg.tree(db, discipline)


class DisciplinesIn(BaseModel):
    discipline_ids: list[uuid.UUID]


@router.put("/entities/{entity_id}/disciplines")
async def set_disciplines(entity_id: uuid.UUID, body: DisciplinesIn, db: DbDep, _: UserDep):
    if await db.get(Entity, entity_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concepto no encontrado")
    ids = set(body.discipline_ids)
    found = set((await db.scalars(select(Discipline.id).where(Discipline.id.in_(ids)))).all())
    if found != ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "disciplina desconocida")
    await db.execute(delete(EntityDiscipline).where(EntityDiscipline.entity_id == entity_id))
    db.add_all(EntityDiscipline(entity_id=entity_id, discipline_id=d) for d in ids)
    db.add(
        AuditLog(
            actor="user",
            action="entity.disciplines",
            subject_table="entities",
            subject_id=str(entity_id),
            after={"discipline_ids": sorted(map(str, ids))},
        )
    )
    await db.commit()
    return {"discipline_ids": sorted(map(str, ids))}


@router.get("/entities/{entity_id}/disciplines")
async def get_disciplines(entity_id: uuid.UUID, db: DbDep, _: UserDep):
    rows = await db.execute(
        select(Discipline.id, Discipline.name)
        .join(EntityDiscipline, EntityDiscipline.discipline_id == Discipline.id)
        .where(EntityDiscipline.entity_id == entity_id)
        .order_by(Discipline.name)
    )
    return [{"id": str(i), "name": n} for i, n in rows.all()]
