"""A concept's definition: write it, generate a proposal with AI, accept (as is or
edited) or discard proposals, bring back an earlier one."""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from kos.api.deps import DbDep, UserDep
from kos.knowledge import definitions as defs
from kos.models import Entity, EntityDefinition

router = APIRouter(prefix="/api", tags=["definitions"])


async def _entity(db, entity_id: uuid.UUID) -> Entity:
    e = await db.get(Entity, entity_id)
    if e is None or e.status == "merged":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concepto no encontrado")
    return e


async def _definition(db, e: Entity, definition_id: uuid.UUID) -> EntityDefinition:
    d = await db.get(EntityDefinition, definition_id)
    if d is None or d.entity_id != e.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "definición no encontrada")
    return d


def _bad(exc: defs.DefinitionError):
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/entities/{entity_id}/definitions")
async def overview(entity_id: uuid.UUID, db: DbDep, _: UserDep):
    e = await _entity(db, entity_id)
    data = await defs.overview(db, e)
    await db.commit()  # an adopted older definition is stored
    return data


class DefinitionIn(BaseModel):
    short: str = Field(max_length=defs.SHORT_MAX)
    extended: str | None = Field(default=None, max_length=defs.EXTENDED_MAX)


@router.put("/entities/{entity_id}/definition")
async def write(entity_id: uuid.UUID, body: DefinitionIn, db: DbDep, _: UserDep):
    e = await _entity(db, entity_id)
    try:
        d = await defs.write(db, e, body.short, body.extended)
    except defs.DefinitionError as exc:
        _bad(exc)
    await db.commit()
    return defs.out(d)


@router.delete("/entities/{entity_id}/definition", status_code=status.HTTP_204_NO_CONTENT)
async def clear(entity_id: uuid.UUID, db: DbDep, _: UserDep):
    e = await _entity(db, entity_id)
    await defs.clear(db, e)
    await db.commit()


@router.post("/entities/{entity_id}/definitions/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate(entity_id: uuid.UUID, db: DbDep, _: UserDep):
    e = await _entity(db, entity_id)
    if await defs.generating(db, e.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "ya se está generando una definición")
    if await defs.pending_count(db, e.id) >= defs.MAX_PROPOSALS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "revisa o descarta las propuestas que ya tienes antes de pedir otra",
        )
    job = defs.define_job(e.id)
    db.add(job)
    await db.commit()
    return {"job_id": str(job.id)}


class AcceptIn(BaseModel):
    short: str | None = Field(default=None, max_length=defs.SHORT_MAX)
    extended: str | None = Field(default=None, max_length=defs.EXTENDED_MAX)


@router.post("/entities/{entity_id}/definitions/{definition_id}/accept")
async def accept(
    entity_id: uuid.UUID, definition_id: uuid.UUID, body: AcceptIn, db: DbDep, _: UserDep
):
    e = await _entity(db, entity_id)
    d = await _definition(db, e, definition_id)
    try:
        await defs.accept(db, e, d, body.short, body.extended)
    except defs.DefinitionError as exc:
        _bad(exc)
    await db.commit()
    return defs.out(d)


@router.post("/entities/{entity_id}/definitions/{definition_id}/reject")
async def reject(entity_id: uuid.UUID, definition_id: uuid.UUID, db: DbDep, _: UserDep):
    e = await _entity(db, entity_id)
    d = await _definition(db, e, definition_id)
    try:
        await defs.reject(db, d)
    except defs.DefinitionError as exc:
        _bad(exc)
    await db.commit()
    return defs.out(d)
