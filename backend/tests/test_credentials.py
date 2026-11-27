"""The Anthropic key set from the «IA» page: stored encrypted, never returned, used for
the AI calls when the server's .env has none (the .env wins when it has one)."""

import pytest
from sqlalchemy import select

from kos.ai import credentials, providers
from kos.ai.providers import ProviderResult, Usage
from kos.config import get_settings
from kos.models import AppSecret, AuditLog

KEY = "sk-ant-api03-" + "A1b2C3d4" * 10 + "WXYZ"


class KeyRecorder:
    def __init__(self):
        self.keys = []

    async def complete_structured(self, *, schema, **kw):
        self.keys.append(credentials.current_key.get())
        return ProviderResult(
            parsed=schema(ok=True),
            stop_reason="end_turn",
            model_served=kw["model"],
            usage=Usage(input_tokens=100, output_tokens=10),
            request_id="r",
        )


@pytest.fixture
def no_env_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


@pytest.fixture
def recorder(monkeypatch):
    r = KeyRecorder()
    monkeypatch.setattr(providers, "get_provider", lambda: r)
    return r


async def test_set_test_and_remove_the_key(auth_client, db, no_env_key, recorder):
    st = (await auth_client.get("/api/ai/credentials")).json()
    assert st["configured"] is False and st["can_store"] is True
    assert (await auth_client.get("/api/ai")).json()["credentials_configured"] is False

    r = await auth_client.put("/api/ai/credentials", json={"api_key": f"  {KEY}\n"})
    assert r.status_code == 200
    st = r.json()
    assert st == {**st, "configured": True, "source": "app", "hint": "WXYZ"}
    # never returned, stored encrypted, not in the audit log
    for path in ("/api/ai/credentials", "/api/ai"):
        assert KEY not in (await auth_client.get(path)).text
    row = await db.get(AppSecret, "anthropic_api_key")
    assert KEY not in row.ciphertext and KEY[10:30] not in row.ciphertext
    audits = (
        await db.scalars(select(AuditLog).where(AuditLog.subject_table == "app_secrets"))
    ).all()
    assert audits and all(KEY not in str(a.after) for a in audits)

    # the AI call uses it
    t = (await auth_client.post("/api/ai/credentials/test")).json()
    assert t["ok"] is True and recorder.keys == [KEY]
    assert credentials.current_key.get() is None  # not left set after the call

    st = (await auth_client.delete("/api/ai/credentials")).json()
    db.expire_all()
    assert st["configured"] is False and await db.get(AppSecret, "anthropic_api_key") is None


async def test_env_key_wins(auth_client, monkeypatch, recorder):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env-" + "z" * 30)
    await auth_client.put("/api/ai/credentials", json={"api_key": KEY})
    st = (await auth_client.get("/api/ai/credentials")).json()
    assert st["source"] == "environment"
    await auth_client.post("/api/ai/credentials/test")
    assert recorder.keys == [None]  # the SDK reads the environment itself


async def test_bad_key_and_missing_master_key(auth_client, monkeypatch, no_env_key):
    r = await auth_client.put("/api/ai/credentials", json={"api_key": "hola-esto-no-es-una-clave"})
    assert r.status_code == 400 and "sk-ant-" in r.json()["detail"]
    monkeypatch.setattr(get_settings(), "secret_key", None)
    assert (await auth_client.get("/api/ai/credentials")).json()["can_store"] is False
    r = await auth_client.put("/api/ai/credentials", json={"api_key": KEY})
    assert r.status_code == 400 and "KOS_SECRET_KEY" in r.json()["detail"]


async def test_a_changed_master_key_makes_the_stored_key_unreadable(
    auth_client, monkeypatch, no_env_key
):
    await auth_client.put("/api/ai/credentials", json={"api_key": KEY})
    monkeypatch.setattr(get_settings(), "secret_key", "another-master-key-" + "y" * 32)
    st = (await auth_client.get("/api/ai/credentials")).json()
    assert st["configured"] is False and st["stored_unreadable"] is True


async def test_auth_required(client):
    client.cookies.clear()
    assert (await client.get("/api/ai/credentials")).status_code == 401
    assert (await client.put("/api/ai/credentials", json={"api_key": KEY})).status_code in (
        401,
        403,
    )
