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
os.environ.setdefault("KOS_SECRET_KEY", "test-master-key-" + "x" * 32)
os.environ["KOS_APP_ORIGIN"] = "http://testserver"
os.environ["KOS_JOB_RETRY_BASE_SECONDS"] = "0"
os.environ["KOS_MAX_UPLOAD_MB"] = "1"
# Fast, deterministic extractor by default; Docling tests opt in explicitly.
os.environ["KOS_PDF_EXTRACTOR"] = "pdfium"
# The fake speech model is patched in this process; a subprocess would not see it.
os.environ["KOS_TRANSCRIBE_IN_SUBPROCESS"] = "false"
os.environ["KOS_DOCLING_IN_SUBPROCESS"] = "false"
# Real embedding model only when explicitly provided; tests use a fake otherwise.
os.environ["KOS_EMBEDDING_MODEL_DIR"] = os.environ.get(
    "KOS_TEST_EMBEDDING_MODEL_DIR", "/nonexistent-embedding-model"
)

import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from sqlalchemy import text  # noqa: E402

import kos.ai.query  # noqa: E402,F401
import kos.ingestion.pipeline  # noqa: E402,F401  (registers job handlers)
import kos.knowledge.ai_jobs  # noqa: E402,F401
import kos.knowledge.extraction  # noqa: E402,F401
import kos.library.chapter_summaries  # noqa: E402,F401
import kos.library.chapters  # noqa: E402,F401
import kos.media.youtube  # noqa: E402,F401
import kos.study.ai_jobs  # noqa: E402,F401
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


@pytest.fixture(scope="session", autouse=True)
def storage_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("storage")
    os.environ["KOS_STORAGE_ROOT"] = str(root)
    from kos.config import get_settings
    from kos.library.blobstore import get_store

    get_settings.cache_clear()
    get_store.cache_clear()
    return root


# Reference data inserted by migrations: restored after each test.
SEED_TABLES = (
    "ai_models",
    "ai_task_routes",
    "entity_types",
    "relationship_types",
    "library_sections",
)
_seed_rows: dict[str, list[dict]] = {}


@pytest.fixture(autouse=True)
async def clean_tables(migrated_db):
    if not _seed_rows:
        async with get_engine().connect() as conn:
            for t in SEED_TABLES:
                rows = await conn.execute(text(f"SELECT * FROM {t}"))  # noqa: S608
                _seed_rows[t] = [dict(r) for r in rows.mappings()]
    yield
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        for t in SEED_TABLES:  # parents first: ai_models before ai_task_routes
            await conn.execute(Base.metadata.tables[t].insert(), _seed_rows[t])


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
