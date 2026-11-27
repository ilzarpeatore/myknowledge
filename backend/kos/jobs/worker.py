"""Background worker: claims jobs and dispatches them to registered handlers.

Handlers are registered per job kind with :func:`handler`. A handler receives a
:class:`JobContext` and returns a JSON-serialisable result (or None).
Raise :class:`PermanentJobError` for failures that retrying cannot fix.
"""

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kos.config import Settings, get_settings
from kos.db import get_sessionmaker
from kos.jobs import queue
from kos.models import JobStatus

log = logging.getLogger("kos.worker")


class PermanentJobError(Exception):
    """A failure that must not be retried (bad input, unsupported format...)."""


@dataclass
class JobContext:
    job_id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    sessionmaker: async_sessionmaker[AsyncSession]

    async def progress(self, value: float) -> None:
        async with self.sessionmaker() as db:
            await queue.set_progress(db, self.job_id, value)

    @property
    def is_last_attempt(self) -> bool:
        return self.attempt >= self.max_attempts

    async def cancelled(self) -> bool:
        async with self.sessionmaker() as db:
            return await queue.is_cancelled(db, self.job_id)


Handler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]
_HANDLERS: dict[str, Handler] = {}


def handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        if kind in _HANDLERS:
            raise ValueError(f"handler already registered for {kind!r}")
        _HANDLERS[kind] = fn
        return fn

    return register


AbandonHook = Callable[[JobContext], Awaitable[None]]
_ON_ABANDON: dict[str, AbandonHook] = {}


def on_abandon(kind: str) -> Callable[[AbandonHook], AbandonHook]:
    """Register what to clean up when a job of this kind is given up without its
    handler being able to run (the worker died during every attempt)."""

    def register(fn: AbandonHook) -> AbandonHook:
        _ON_ABANDON[kind] = fn
        return fn

    return register


def registered_kinds() -> list[str]:
    return sorted(_HANDLERS)


async def run_once(
    sessionmaker: async_sessionmaker[AsyncSession], settings: Settings, worker_id: str
) -> bool:
    """Claim and run at most one job. Returns True if a job was processed."""
    async with sessionmaker() as db:
        job = await queue.claim_next(db, worker_id, settings.job_lease_seconds)
        if job is None:
            return False
        ctx = JobContext(
            job.id, job.kind, dict(job.payload), job.attempts, job.max_attempts, sessionmaker
        )
        abandoned = job.status == JobStatus.failed

    if abandoned:
        log.error("job %s (%s) given up: %s", ctx.job_id, ctx.kind, job.last_error)
        if hook := _ON_ABANDON.get(ctx.kind):
            try:
                await hook(ctx)
            except Exception:
                log.exception("abandon hook of %s failed", ctx.kind)
        return True

    fn = _HANDLERS.get(ctx.kind)
    try:
        if fn is None:
            raise PermanentJobError(f"no handler registered for job kind {ctx.kind!r}")
        result = await fn(ctx)
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded on the job
        retryable = not isinstance(exc, PermanentJobError)
        async with sessionmaker() as db:
            status = await queue.fail(
                db,
                ctx.job_id,
                f"{type(exc).__name__}: {exc}",
                settings.job_retry_base_seconds,
                retryable,
            )
        log.warning("job %s (%s) failed -> %s: %s", ctx.job_id, ctx.kind, status, exc)
        return True

    async with sessionmaker() as db:
        if await queue.is_cancelled(db, ctx.job_id):
            log.info("job %s finished after cancellation; result discarded", ctx.job_id)
            return True
        await queue.complete(db, ctx.job_id, result)
    log.info("job %s (%s) -> %s", ctx.job_id, ctx.kind, JobStatus.completed)
    return True


async def run_forever() -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    log.info("worker %s started; handlers: %s", worker_id, registered_kinds() or "none")
    from kos import monitor

    async with sessionmaker() as db:
        if released := await queue.release_own(db, worker_id):
            log.warning(
                "re-queued %d job(s) left unfinished by a previous run of %s", released, worker_id
            )
    info = monitor.capabilities()
    log.info("capabilities: %s", info)
    # Beat from its own task, not between jobs: a long job (a 75 MB PDF through Docling
    # took over 20 minutes on 2026-11-27) made the Sistema page report the worker as
    # gone while it was working. Heavy work runs in threads or subprocesses, so the
    # event loop stays free to beat.
    beat = asyncio.create_task(beat_forever(sessionmaker, worker_id, info))
    try:
        await _loop(sessionmaker, settings, worker_id)
    finally:
        beat.cancel()


async def beat_forever(sessionmaker, worker_id: str, info: dict[str, Any]) -> None:
    from kos import monitor

    while True:
        try:
            async with sessionmaker() as db:
                await monitor.heartbeat(db, worker_id, info)
        except Exception:
            log.exception("heartbeat failed")
        await asyncio.sleep(monitor.HEARTBEAT_EVERY.total_seconds())


async def _loop(sessionmaker, settings, worker_id: str) -> None:
    while True:
        try:
            processed = await run_once(sessionmaker, settings, worker_id)
        except Exception:
            log.exception("worker loop error")
            processed = False
        if not processed:
            await asyncio.sleep(settings.worker_poll_seconds)


def main() -> None:
    """Entry point (``kos worker``). Must not be run as ``python -m kos.jobs.worker``:
    that would load this module twice and handlers would register on the other copy."""
    # Importing a module registers its job handlers.
    import kos.ai.query  # noqa: F401
    import kos.analysis.ai_jobs  # noqa: F401
    import kos.ingestion.pipeline  # noqa: F401
    import kos.knowledge.ai_jobs  # noqa: F401
    import kos.knowledge.definitions_ai  # noqa: F401
    import kos.knowledge.extraction  # noqa: F401
    import kos.media.youtube  # noqa: F401
    import kos.references.service  # noqa: F401
    import kos.study.ai_jobs  # noqa: F401

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run_forever())
