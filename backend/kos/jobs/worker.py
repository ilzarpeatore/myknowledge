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
    import kos.ingestion.pipeline  # noqa: F401
    import kos.knowledge.ai_jobs  # noqa: F401
    import kos.knowledge.extraction  # noqa: F401
    import kos.media.youtube  # noqa: F401

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run_forever())
