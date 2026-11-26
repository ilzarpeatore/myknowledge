import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from kos.config import get_settings
from kos.db import get_sessionmaker
from kos.jobs import queue, worker
from kos.models import Job, JobStatus

calls: list[str] = []


@worker.handler("test.ok")
async def _ok(ctx: worker.JobContext):
    calls.append(ctx.payload["value"])
    await ctx.progress(0.5)
    return {"echo": ctx.payload["value"]}


@worker.handler("test.flaky")
async def _flaky(ctx: worker.JobContext):
    if ctx.attempt < 2:
        raise RuntimeError("transient")
    return {"attempt": ctx.attempt}


@worker.handler("test.bad_input")
async def _bad(ctx: worker.JobContext):
    raise worker.PermanentJobError("unsupported format")


async def run(n: int = 1) -> None:
    for _ in range(n):
        await worker.run_once(get_sessionmaker(), get_settings(), "test-worker")


async def test_job_runs_to_completion(db):
    job = await queue.enqueue(db, "test.ok", {"value": "a"})
    await run()
    await db.refresh(job)
    assert job.status == JobStatus.completed
    assert job.result == {"echo": "a"}
    assert job.progress == 1.0 and job.attempts == 1 and job.finished_at


async def test_transient_failure_is_retried(db):
    job = await queue.enqueue(db, "test.flaky", max_attempts=3)
    await run()
    await db.refresh(job)
    assert job.status == JobStatus.pending and "transient" in job.last_error
    await run()
    await db.refresh(job)
    assert job.status == JobStatus.completed and job.result == {"attempt": 2}


async def test_retries_stop_at_max_attempts(db):
    job = await queue.enqueue(db, "test.flaky", max_attempts=1)
    await run()
    await db.refresh(job)
    assert job.status == JobStatus.failed


async def test_permanent_error_is_not_retried(db):
    job = await queue.enqueue(db, "test.bad_input", max_attempts=5)
    await run()
    await db.refresh(job)
    assert job.status == JobStatus.failed and job.attempts == 1


async def test_unknown_kind_fails_permanently(db):
    job = await queue.enqueue(db, "does.not.exist")
    await run()
    await db.refresh(job)
    assert job.status == JobStatus.failed
    assert "no handler" in job.last_error


async def test_future_jobs_wait(db):
    job = await queue.enqueue(
        db, "test.ok", {"value": "later"}, run_after=datetime.now(UTC) + timedelta(hours=1)
    )
    await run()
    await db.refresh(job)
    assert job.status == JobStatus.pending


async def test_stale_lease_is_reclaimed(db):
    job = await queue.enqueue(db, "test.ok", {"value": "crashed"})
    await db.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(
            status=JobStatus.processing,
            locked_by="dead-worker",
            locked_at=datetime.now(UTC) - timedelta(days=1),
            attempts=1,
        )
    )
    await db.commit()
    await run()
    await db.refresh(job)
    assert job.status == JobStatus.completed and job.attempts == 2


async def test_concurrent_workers_never_claim_the_same_job(db):
    calls.clear()
    for i in range(10):
        await queue.enqueue(db, "test.ok", {"value": str(i)})
    sm, s = get_sessionmaker(), get_settings()
    await asyncio.gather(*(worker.run_once(sm, s, f"w{i}") for i in range(10)))
    assert sorted(calls) == [str(i) for i in range(10)]


async def test_cancel_via_api(auth_client, db):
    job = await queue.enqueue(db, "test.ok", {"value": "x"})
    r = await auth_client.post(f"/api/jobs/{job.id}/cancel")
    assert r.status_code == 204
    await db.refresh(job)
    assert job.status == JobStatus.cancelled
    assert (await auth_client.post(f"/api/jobs/{job.id}/cancel")).status_code == 409
    await run()
    await db.refresh(job)
    assert job.status == JobStatus.cancelled


async def test_status_reports_real_counts(auth_client, db):
    await queue.enqueue(db, "test.ok", {"value": "1"})
    await queue.enqueue(db, "test.ok", {"value": "2"})
    await run()
    body = (await auth_client.get("/api/status")).json()
    assert body["jobs"]["completed"] == 1 and body["jobs"]["pending"] == 1
    assert body["max_upload_mb"] == 1
    listed = (await auth_client.get("/api/jobs")).json()
    assert len(listed) == 2


async def test_a_restarted_worker_takes_back_its_unfinished_jobs(db):
    """A worker killed mid-job (e.g. out of memory) restarts with the same id in its
    container: its job is put back at once instead of waiting for the lease."""
    job = await queue.enqueue(db, "demo.kind", {})
    claimed = await queue.claim_next(db, "host:1", lease_seconds=900)
    assert claimed.id == job.id and claimed.status == JobStatus.processing
    assert await queue.claim_next(db, "host:1", lease_seconds=900) is None  # still leased
    assert await queue.release_own(db, "other:7") == 0  # someone else's: untouched
    assert await queue.release_own(db, "host:1") == 1
    again = await queue.claim_next(db, "host:1", lease_seconds=900)
    assert again.id == job.id and again.attempts == 2
