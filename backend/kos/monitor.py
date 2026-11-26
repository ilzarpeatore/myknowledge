"""Operational health: worker heartbeats, checks, alerts and the daily study reminder.

``kos monitor`` (cron, every 15 min) runs the checks and sends an alert when the set of
problems changes, or again once a day while something stays wrong. ``kos remind``
(cron, once a day) sends how many cards are due. Both use ``KOS_ALERT_URL``.
"""

import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kos.config import Settings, get_settings
from kos.models import Job, JobStatus, Source, SourceStatus, SystemEvent, WorkerHeartbeat

HEARTBEAT_EVERY = timedelta(seconds=30)
WORKER_WARN = timedelta(minutes=3)
WORKER_FAIL = timedelta(minutes=15)
QUEUE_WARN = timedelta(minutes=30)
STUCK_SOURCE = timedelta(hours=3)
BACKUP_WARN = timedelta(hours=26)
BACKUP_FAIL = timedelta(days=3)
REALERT_EVERY = timedelta(hours=24)


# ---- worker side ------------------------------------------------------------------------


def capabilities() -> dict[str, Any]:
    """What this worker can process (reported in its heartbeat)."""
    from kos import embeddings
    from kos.ingestion import docling_extract
    from kos.jobs.worker import registered_kinds
    from kos.media import transcribe

    def version(pkg):
        try:
            return metadata.version(pkg)
        except metadata.PackageNotFoundError:
            return None

    return {
        "handlers": registered_kinds(),
        "docling": docling_extract.available(),
        "ocr": docling_extract.ocr_available(),
        "transcription": transcribe.available(),
        "whisper_model": transcribe.model_installed() if transcribe.available() else False,
        "embeddings": embeddings.embeddings_available(),
        "yt_dlp": version("yt-dlp"),
    }


async def heartbeat(db: AsyncSession, worker_id: str, info: dict[str, Any]) -> None:
    now = datetime.now(UTC)
    stmt = insert(WorkerHeartbeat).values(worker_id=worker_id, last_seen=now, info=info)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[WorkerHeartbeat.worker_id], set_={"last_seen": now, "info": info}
        )
    )
    # forget workers stopped a week ago (every restart gets a new id)
    await db.execute(
        delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen < now - timedelta(days=7))
    )
    await db.commit()


# ---- checks ----------------------------------------------------------------------------------


@dataclass
class Check:
    key: str
    label: str
    status: str  # ok | warn | fail
    detail: str


def _ago(delta: timedelta) -> str:
    s = int(delta.total_seconds())
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} min"
    if s < 172800:
        return f"{s // 3600} h"
    return f"{s // 86400} días"


async def run_checks(
    db: AsyncSession, s: Settings | None = None, now: datetime | None = None
) -> list[Check]:
    from kos.ai import service as ai

    s = s or get_settings()
    now = now or datetime.now(UTC)
    out: list[Check] = []

    # worker
    hb = await db.scalar(
        select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen.desc()).limit(1)
    )
    if hb is None:
        out.append(Check("worker", "Worker", "fail", "ningún worker ha dado señales de vida"))
    else:
        age = now - hb.last_seen
        st = "ok" if age < WORKER_WARN else "warn" if age < WORKER_FAIL else "fail"
        missing = [
            k
            for k in ("docling", "ocr", "transcription", "whisper_model", "embeddings")
            if hb.info.get(k) is False
        ]
        detail = f"última señal hace {_ago(age)}"
        if missing:
            detail += "; sin: " + ", ".join(missing)
            st = "warn" if st == "ok" else st
        out.append(Check("worker", "Worker", st, detail))

    # queue
    oldest = await db.scalar(
        select(func.min(Job.created_at)).where(
            Job.status == JobStatus.pending, Job.run_after <= now
        )
    )
    failed = await db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.status == JobStatus.failed, Job.finished_at >= now - timedelta(days=1))
    )
    waiting = now - oldest if oldest else timedelta(0)
    st = "warn" if waiting > QUEUE_WARN or failed else "ok"
    out.append(
        Check(
            "queue",
            "Cola de trabajos",
            st,
            (f"trabajo más antiguo esperando {_ago(waiting)}; " if oldest else "sin espera; ")
            + f"{failed} fallido(s) en 24 h",
        )
    )

    # stuck sources
    stuck = await db.scalar(
        select(func.count())
        .select_from(Source)
        .where(
            Source.status == SourceStatus.processing,
            func.coalesce(Source.updated_at, Source.created_at) < now - STUCK_SOURCE,
        )
    )
    out.append(
        Check(
            "sources",
            "Fuentes en proceso",
            "warn" if stuck else "ok",
            f"{stuck} atascada(s) más de 3 h" if stuck else "ninguna atascada",
        )
    )

    # disk
    try:
        usage = shutil.disk_usage(Path(s.storage_root))
        free = usage.free / usage.total
        st = "fail" if free < 0.05 else "warn" if free < 0.15 else "ok"
        out.append(
            Check("disk", "Disco (almacén)", st, f"{usage.free / 1e9:.1f} GB libres ({free:.0%})")
        )
    except OSError as exc:
        out.append(Check("disk", "Disco (almacén)", "fail", f"no accesible: {exc}"))

    # backups
    last = await db.scalar(
        select(SystemEvent)
        .where(SystemEvent.kind == "backup_ok")
        .order_by(SystemEvent.created_at.desc())
        .limit(1)
    )
    if last is None:
        out.append(
            Check("backup", "Copia de seguridad", "warn", "no hay registro de ninguna copia")
        )
    else:
        age = now - last.created_at
        st = "ok" if age < BACKUP_WARN else "warn" if age < BACKUP_FAIL else "fail"
        out.append(
            Check(
                "backup",
                "Copia de seguridad",
                st,
                f"última hace {_ago(age)} ({last.detail.get('size', '?')})",
            )
        )

    # AI budget
    spent, limit = await ai.month_spend(db), await ai.month_limit(db)
    ratio = float(spent / limit) if limit else 1.0
    st = "fail" if ratio >= 1 else "warn" if ratio >= 0.8 else "ok"
    out.append(
        Check(
            "ai_budget",
            "Gasto de IA del mes",
            st,
            f"{spent:.2f} de {limit:.2f} USD ({ratio:.0%})"
            + (" — los trabajos de IA están parados" if ratio >= 1 else ""),
        )
    )
    return out


# ---- notifications -----------------------------------------------------------------------------


async def notify(
    title: str,
    text: str,
    s: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    s = s or get_settings()
    if not s.alert_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=15, transport=transport) as c:
            if "ntfy" in httpx.URL(s.alert_url).host:
                r = await c.post(
                    s.alert_url,
                    content=text.encode(),
                    headers={"Title": title.encode().decode("latin-1", "ignore"), "Tags": "brain"},
                )
            else:
                body = f"*{title}*\n{text}"
                r = await c.post(s.alert_url, json={"text": body, "content": body})
        return r.status_code < 300
    except httpx.HTTPError:
        return False


def _summary(checks: list[Check]) -> str:
    return "\n".join(
        f"{'❌' if c.status == 'fail' else '⚠️'} {c.label}: {c.detail}"
        for c in checks
        if c.status != "ok"
    )


async def monitor(
    db: AsyncSession, s: Settings | None = None, now: datetime | None = None, transport=None
) -> dict[str, Any]:
    """Run checks; alert when the problems change, or daily while they persist, and once
    when everything is fine again."""
    s = s or get_settings()
    now = now or datetime.now(UTC)
    checks = await run_checks(db, s, now)
    problems = sorted(f"{c.key}:{c.status}" for c in checks if c.status != "ok")
    last = await db.scalar(
        select(SystemEvent)
        .where(SystemEvent.kind == "alert_state")
        .order_by(SystemEvent.created_at.desc())
        .limit(1)
    )
    previous = last.detail.get("problems", []) if last else []
    changed = problems != previous
    stale = last is not None and problems and now - last.created_at > REALERT_EVERY
    sent = False
    if changed or stale:
        url = s.public_url or s.app_origin
        if problems:
            sent = await notify(
                "Knowledge OS: revisar", _summary(checks) + f"\n{url}/system", s, transport
            )
        elif previous:
            sent = await notify(
                "Knowledge OS: todo en orden",
                "Los avisos anteriores se han resuelto.",
                s,
                transport,
            )
        db.add(SystemEvent(kind="alert_state", detail={"problems": problems, "sent": sent}))
        await db.commit()
    return {"checks": [asdict(c) for c in checks], "alerted": sent, "changed": changed}


async def remind(db: AsyncSession, s: Settings | None = None, transport=None) -> dict[str, Any]:
    from kos.study import gaps

    s = s or get_settings()
    due = await gaps.count_due(db)
    sent = False
    if due:
        url = s.public_url or s.app_origin
        sent = await notify(
            "Repaso de hoy", f"Tienes {due} tarjeta(s) por repasar.\n{url}/study", s, transport
        )
    db.add(SystemEvent(kind="reminder", detail={"due": due, "sent": sent}))
    await db.commit()
    return {"due": due, "sent": sent}


async def record(db: AsyncSession, kind: str, detail: dict[str, Any]) -> None:
    db.add(SystemEvent(kind=kind, detail=detail))
    await db.commit()
