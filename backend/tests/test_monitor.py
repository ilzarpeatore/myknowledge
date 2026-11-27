"""Phase 8: monitoring, alerts, reminder, and the AI budget stopping jobs."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from kos import monitor
from kos.ai import service as ai
from kos.config import get_settings
from kos.models import AIBudget, AIRun, SystemEvent, WorkerHeartbeat
from kos.study import cards

from .test_knowledge import entity


def statuses(checks):
    return {c.key: c.status for c in checks}


class Sent(list):
    transport: httpx.MockTransport


@pytest.fixture
def sent(monkeypatch):
    out = Sent()

    def handle(request: httpx.Request) -> httpx.Response:
        out.append(request)
        return httpx.Response(200)

    monkeypatch.setattr(get_settings(), "alert_url", "https://ntfy.sh/kos-prueba")
    out.transport = httpx.MockTransport(handle)
    return out


async def test_checks_flag_a_missing_or_silent_worker_and_old_backups(db):
    now = datetime.now(UTC)
    st = statuses(await monitor.run_checks(db, now=now))
    assert st["worker"] == "fail" and st["backup"] == "warn" and st["ai_budget"] == "ok"
    await monitor.heartbeat(
        db,
        "w1",
        {
            "docling": True,
            "ocr": True,
            "transcription": True,
            "whisper_model": True,
            "embeddings": True,
        },
    )
    assert statuses(await monitor.run_checks(db, now=now))["worker"] == "ok"
    assert (
        statuses(await monitor.run_checks(db, now=now + timedelta(minutes=5)))["worker"] == "warn"
    )
    assert statuses(await monitor.run_checks(db, now=now + timedelta(hours=1)))["worker"] == "fail"
    # a capability missing (e.g. the speech model not downloaded) is a warning
    await monitor.heartbeat(db, "w1", {"whisper_model": False})
    c = {x.key: x for x in await monitor.run_checks(db)}["worker"]
    assert c.status == "warn" and "whisper_model" in c.detail
    await monitor.record(db, "backup_ok", {"size": "1,2G"})
    assert statuses(await monitor.run_checks(db))["backup"] == "ok"
    assert statuses(await monitor.run_checks(db, now=now + timedelta(days=4)))["backup"] == "fail"


async def test_budget_reached_shows_and_stops_ai_work(db, monkeypatch):
    db.add(AIBudget(month=ai.current_month(), limit_usd=Decimal("1")))
    db.add(
        AIRun(
            task="extract", model_requested="claude-opus-5-5", status="ok", cost_usd=Decimal("1.2")
        )
    )
    await db.commit()
    c = {x.key: x for x in await monitor.run_checks(db)}["ai_budget"]
    assert c.status == "fail" and "parados" in c.detail and "1.20 de 1.00" in c.detail
    called = []

    class Never:
        async def complete_structured(self, **kw):
            called.append(kw)

    from kos.ai import providers

    monkeypatch.setattr(providers, "get_provider", lambda: Never())
    from pydantic import BaseModel

    class Out(BaseModel):
        ok: bool

    with pytest.raises(ai.BudgetExceeded):
        await ai.run_structured(
            db, task="check", prompt_key="connection_check", schema=Out, instructions="x"
        )
    assert called == []  # the model is never called once the budget is used up


async def test_alerts_only_on_change_daily_while_failing_and_on_recovery(db, sent, monkeypatch):
    from collections import namedtuple

    monkeypatch.setattr(
        monitor.shutil,
        "disk_usage",
        lambda p: namedtuple("U", "total used free")(100e9, 20e9, 80e9),
    )
    t = datetime.now(UTC)
    r = await monitor.monitor(db, now=t, transport=sent.transport)
    assert r["alerted"] and len(sent) == 1
    req = sent[0]
    assert req.url.host == "ntfy.sh" and req.headers["title"] == "Knowledge OS: revisar"
    assert "Worker" in req.content.decode()
    await monitor.monitor(db, now=t + timedelta(minutes=15), transport=sent.transport)
    assert len(sent) == 1  # same problems: no repeat
    await monitor.monitor(db, now=t + timedelta(hours=25), transport=sent.transport)
    assert len(sent) == 2  # still failing a day later: reminded once
    await monitor.heartbeat(db, "w", {})
    await monitor.record(db, "backup_ok", {"size": "1G"})
    r = await monitor.monitor(db, transport=sent.transport)
    assert not [c for c in r["checks"] if c["status"] != "ok"]
    assert sent[-1].headers["title"] == "Knowledge OS: todo en orden"


async def test_json_webhooks_and_no_url(db, sent, monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_url", "https://hooks.slack.com/services/x")
    assert await monitor.notify("Hola", "texto", transport=sent.transport)
    body = sent[-1].read().decode()
    assert '"text"' in body and '"content"' in body
    monkeypatch.setattr(get_settings(), "alert_url", None)
    assert await monitor.notify("Hola", "texto") is False


async def test_daily_reminder_counts_due_cards(auth_client, db, sent):
    e = await entity(auth_client, "Proximidad al fallo")
    import uuid

    cards.add_card(
        db, entity_id=uuid.UUID(e["id"]), front="¿Q?", back="R", kind="qa", origin="user"
    )
    await db.commit()
    r = await monitor.remind(db, transport=sent.transport)
    assert r == {"due": 1, "sent": True}
    assert "1 tarjeta" in sent[-1].content.decode() and "/study" in sent[-1].content.decode()
    assert await db.scalar(select(SystemEvent.kind).where(SystemEvent.kind == "reminder"))


async def test_health_report_endpoint(auth_client, db):
    await monitor.heartbeat(db, "worker-1", {"handlers": ["source.extract"], "docling": True})
    r = (await auth_client.get("/api/system/health")).json()
    assert {c["key"] for c in r["checks"]} == {
        "worker",
        "queue",
        "sources",
        "disk",
        "backup",
        "ai_budget",
    }
    assert r["workers"][0]["id"] == "worker-1" and r["alerts_configured"] is False
    assert await db.scalar(select(WorkerHeartbeat.worker_id)) == "worker-1"


async def test_worker_keeps_beating_while_a_long_job_runs(monkeypatch):
    """2026-11-27: a 20-minute PDF extraction made Sistema report the worker as gone,
    because the beat only happened between jobs."""
    import asyncio
    import contextlib
    import time
    from datetime import timedelta as td

    from kos.jobs import worker

    beats = []

    async def fake_heartbeat(db, worker_id, info):
        beats.append(worker_id)

    class NoSession:
        def __call__(self):
            return contextlib.nullcontext(None)

    monkeypatch.setattr(monitor, "heartbeat", fake_heartbeat)
    monkeypatch.setattr(monitor, "HEARTBEAT_EVERY", td(seconds=0.05))
    beat = asyncio.create_task(worker.beat_forever(NoSession(), "w-long", {}))
    await asyncio.to_thread(time.sleep, 0.4)  # the long job, off the event loop
    beat.cancel()
    assert len(beats) >= 4
