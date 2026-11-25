"""Test setup. Requires a PostgreSQL database with the pgvector extension available.

KOS_TEST_DATABASE_URL points at a disposable database: every table is dropped and
recreated by running the migrations from scratch at the start of the session.
"""

import os

TEST_DB = os.environ.get(
    "KOS_TEST_DATABASE_URL", "postgresql+psycopg://kos:kos@localhost:5432/kos_test"
)
os.environ["KOS_DATABASE_URL"] = TEST_DB
os.environ["KOS_COOKIE_SECURE"] = "false"
os.environ["KOS_APP_ORIGIN"] = "http://testserver"
os.environ["KOS_JOB_RETRY_BASE_SECONDS"] = "0"

import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from sqlalchemy import text  # noqa: E402

from kos.api.main import create_app  # noqa: E402
from kos.cli import alembic_config  # noqa: E402
from kos.db import get_engine, get_sessionmaker  # noqa: E402
from kos.models import Base  # noqa: E402

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
async def clean_tables(migrated_db):
    yield
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def db():
    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def user(db):
    from kos.auth.service import create_single_user

    return await create_single_user(db, "owner", PASSWORD)


@pytest.fixture
async def auth_client(client, user):
    r = await client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 200
    client.headers["origin"] = "http://testserver"
    return client
