import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select, text

from kos.api.deps import DbDep, SettingsDep, UserDep
from kos.jobs import queue
from kos.models import AuditLog, Job, JobStatus

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health(db: DbDep):
    """Unauthenticated liveness check, including database reachability."""
    await db.execute(text("SELECT 1"))
    return {"status": "ok"}


class StatusOut(BaseModel):
    jobs: dict[str, int]
    max_upload_mb: int


@router.get("/status", response_model=StatusOut)
async def system_status(db: DbDep, s: SettingsDep, _: UserDep):
    rows = (await db.execute(select(Job.status, func.count()).group_by(Job.status))).all()
    counts = {s.value: 0 for s in JobStatus}
    counts.update({st.value: n for st, n in rows})
    return StatusOut(jobs=counts, max_upload_mb=s.max_upload_mb)


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: JobStatus
    progress: float
    attempts: int
    max_attempts: int
    last_error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(db: DbDep, _: UserDep, status_filter: JobStatus | None = None, limit: int = 50):
    stmt = select(Job).order_by(Job.created_at.desc()).limit(max(1, min(limit, 200)))
    if status_filter is not None:
        stmt = stmt.where(Job.status == status_filter)
    return [JobOut.model_validate(j, from_attributes=True) for j in await db.scalars(stmt)]


@router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_job(job_id: uuid.UUID, db: DbDep, _: UserDep):
    if not await queue.cancel(db, job_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "job not found or already finished")
    db.add(
        AuditLog(actor="user", action="job.cancel", subject_table="jobs", subject_id=str(job_id))
    )
    await db.commit()
