from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from kos.auth import service
from kos.models import AuditLog, UserSession
from tests.conftest import PASSWORD


async def test_health_is_public(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_protected_endpoints_require_session(client):
    for path in ("/api/auth/me", "/api/status", "/api/jobs"):
        assert (await client.get(path)).status_code == 401


async def test_login_sets_httponly_cookie_and_me_works(client, user):
    r = await client.post("/api/auth/login", json={"username": "Owner ", "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    me = await client.get("/api/auth/me")
    assert me.json() == {"username": "owner"}


async def test_session_token_is_stored_hashed(client, user, db):
    await client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    raw = client.cookies["kos_session"]
    stored = (await db.scalars(select(UserSession.token_hash))).all()
    assert stored == [service.hash_token(raw)]
    assert raw not in stored


async def test_wrong_password_and_unknown_user_look_the_same(client, user):
    a = await client.post("/api/auth/login", json={"username": "owner", "password": "nope"})
    b = await client.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


async def test_login_is_throttled_after_repeated_failures(client, user):
    for _ in range(5):
        await client.post("/api/auth/login", json={"username": "owner", "password": "bad"})
    r = await client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 429


async def test_logout_revokes_session(auth_client):
    assert (await auth_client.post("/api/auth/logout")).status_code == 204
    assert (await auth_client.get("/api/auth/me")).status_code == 401


async def test_expired_session_is_rejected(auth_client, db):
    await db.execute(
        update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
    assert (await auth_client.get("/api/auth/me")).status_code == 401


async def test_cross_origin_post_with_cookie_is_rejected(auth_client):
    auth_client.headers["origin"] = "https://evil.example"
    assert (await auth_client.post("/api/auth/logout")).status_code == 403
    del auth_client.headers["origin"]
    assert (await auth_client.post("/api/auth/logout")).status_code == 403


async def test_only_one_account_can_exist(db, user):
    with pytest.raises(service.AuthError):
        await service.create_single_user(db, "second", PASSWORD)


async def test_short_password_rejected(db):
    with pytest.raises(ValueError):
        await service.create_single_user(db, "owner", "short")


async def test_reset_password_closes_sessions(auth_client, db):
    await service.reset_password(db, "owner", "another long password 123")
    assert (await auth_client.get("/api/auth/me")).status_code == 401


async def test_auth_events_are_audited(client, user, db):
    await client.post("/api/auth/login", json={"username": "owner", "password": "bad"})
    await client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    actions = (await db.scalars(select(AuditLog.action).order_by(AuditLog.created_at))).all()
    assert actions == ["auth.user_created", "auth.login_failed", "auth.login"]
    assert await db.scalar(select(func.count()).select_from(AuditLog)) == 3
