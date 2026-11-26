"""Job queue operations. Every function takes an open session and commits.

Claiming uses ``FOR UPDATE SKIP LOCKED`` so several workers can run safely.
A job left in ``processing`` past its lease (worker crashed) is claimable again.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kos.models import Job, JobStatus


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    max_attempts: int = 3,
    parent_job_id: uuid.UUID | None = None,
    run_after: datetime | None = None,
) -> Job:
    job = Job(
        kind=kind,
        payload=payload or {},
        max_attempts=max_attempts,
        parent_job_id=parent_job_id,
        status=JobStatus.pending,
    )
    if run_after is not None:
        job.run_after = run_after
    db.add(job)
    await db.commit()
    return job


async def release_own(db: AsyncSession, worker_id: str) -> int:
    """At start-up, put back the jobs this worker id held when it died (in a container
    the id survives a restart), instead of waiting for their lease to expire."""
    res = await db.execute(
        update(Job)
        .where(Job.status == JobStatus.processing, Job.locked_by == worker_id)
        .values(
            status=JobStatus.pending, locked_by=None, locked_at=None, run_after=datetime.now(UTC)
        )
    )
    await db.commit()
    return res.rowcount


async def claim_next(db: AsyncSession, worker_id: str, lease_seconds: int) -> Job | None:
    now = datetime.now(UTC)
    lease_expired = now - timedelta(seconds=lease_seconds)
    stmt = (
        select(Job)
        .where(
            or_(
                and_(Job.status == JobStatus.pending, Job.run_after <= now),
                and_(Job.status == JobStatus.processing, Job.locked_at < lease_expired),
            )
        )
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = await db.scalar(stmt)
    if job is None:
        await db.rollback()
        return None
    job.status = JobStatus.processing
    job.attempts += 1
    job.locked_by = worker_id
    job.locked_at = now
    job.started_at = job.started_at or now
    await db.commit()
    return job


async def complete(db: AsyncSession, job_id: uuid.UUID, result: dict[str, Any] | None) -> None:
    await db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=JobStatus.completed,
            progress=1.0,
            result=result,
            locked_by=None,
            locked_at=None,
            last_error=None,
            finished_at=datetime.now(UTC),
        )
    )
    await db.commit()


async def fail(
    db: AsyncSession, job_id: uuid.UUID, error: str, retry_base_seconds: int, retryable: bool
) -> JobStatus:
    """Record a failure; reschedule with exponential backoff while attempts remain."""
    job = await db.get(Job, job_id, with_for_update=True)
    if job is None:
        await db.rollback()
        return JobStatus.failed
    now = datetime.now(UTC)
    job.last_error = error[:4000]
    job.locked_by = None
    job.locked_at = None
    if retryable and job.attempts < job.max_attempts:
        job.status = JobStatus.pending
        job.run_after = now + timedelta(seconds=retry_base_seconds * 2 ** (job.attempts - 1))
    else:
        job.status = JobStatus.failed
        job.finished_at = now
    status = job.status
    await db.commit()
    return status


async def set_progress(db: AsyncSession, job_id: uuid.UUID, progress: float) -> None:
    await db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(progress=max(0.0, min(1.0, progress)), locked_at=datetime.now(UTC))
    )
    await db.commit()


async def cancel(db: AsyncSession, job_id: uuid.UUID) -> bool:
    """Cancel a job that has not finished. A running handler sees it via is_cancelled."""
    res = await db.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.in_([JobStatus.pending, JobStatus.processing, JobStatus.awaiting_review]),
        )
        .values(status=JobStatus.cancelled, finished_at=datetime.now(UTC))
    )
    await db.commit()
    return res.rowcount > 0


async def is_cancelled(db: AsyncSession, job_id: uuid.UUID) -> bool:
    status = await db.scalar(select(Job.status).where(Job.id == job_id))
    return status == JobStatus.cancelled
