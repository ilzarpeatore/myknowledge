"""Start and follow the automatic knowledge extraction of a source."""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from kos.api.deps import DbDep, UserDep
from kos.knowledge import extraction as kx
from kos.library import service as library
from kos.models import AuditLog, KnowledgeExtraction, SourceStatus

router = APIRouter(prefix="/api/sources", tags=["extraction"])


async def _version(db, source_id: uuid.UUID):
    source = await library.get_source(db, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fuente no encontrada")
    return source, source.versions[-1]


def _out(ex: KnowledgeExtraction | None):
    if ex is None:
        return None
    return {
        "id": str(ex.id),
        "status": ex.status,
        "last_ordinal": ex.last_ordinal,
        "stats": ex.stats,
        "error": ex.error,
        "created_at": ex.created_at,
        "finished_at": ex.finished_at,
    }


@router.get("/{source_id}/knowledge-extraction")
async def get_extraction(source_id: uuid.UUID, db: DbDep, _: UserDep):
    _, version = await _version(db, source_id)
    latest = await db.scalar(
        select(KnowledgeExtraction)
        .where(KnowledgeExtraction.source_version_id == version.id)
        .order_by(KnowledgeExtraction.created_at.desc())
    )
    after = latest.last_ordinal if latest is not None and latest.status != "completed" else -1
    return {
        "latest": _out(latest),
        "in_flight": await kx.in_flight(db, version.id),
        "estimate": await kx.estimate(db, version.id, after),
    }


class StartIn(BaseModel):
    restart: bool = False


@router.post("/{source_id}/knowledge-extraction", status_code=status.HTTP_202_ACCEPTED)
async def start_extraction(source_id: uuid.UUID, body: StartIn, db: DbDep, _: UserDep):
    source, version = await _version(db, source_id)
    if source.status != SourceStatus.ready:
        raise HTTPException(status.HTTP_409_CONFLICT, "la fuente aún no está procesada")
    if await kx.in_flight(db, version.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "ya hay una extracción en curso")
    ex, job = await kx.start(db, version, body.restart)
    db.add(
        AuditLog(
            actor="user",
            action="source.knowledge_extraction",
            subject_table="sources",
            subject_id=str(source_id),
            after={"extraction_id": str(ex.id), "resume": ex.last_ordinal >= 0},
        )
    )
    await db.commit()
    return {
        "extraction_id": str(ex.id),
        "job_id": str(job.id),
        "resumes_after_ordinal": ex.last_ordinal,
    }
