"""Semantic and hybrid search.

Most tests use a deterministic fake embedder (word hashing plus a tiny synonym
table) so they run anywhere. One test uses the real local model when
KOS_TEST_EMBEDDING_MODEL_DIR points at it (see `kos download-models`)."""

import hashlib
import math
import re

import pytest
from sqlalchemy import func, select

import kos.ingestion.pipeline  # noqa: F401  (registers job handlers)
from kos import embeddings
from kos.config import get_settings
from kos.db import get_sessionmaker
from kos.jobs import worker
from kos.models import Job, JobStatus, SegmentEmbedding

SYNONYMS = {"recruitment": "reclutamiento", "motor": "motoras", "unit": "unidades"}


class FakeEmbedder:
    model_id = "fake:v1"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * embeddings.EMBEDDING_DIM
        for w in re.findall(r"\w+", text.lower()):
            w = SYNONYMS.get(w, w)[:6]
            h = int(hashlib.blake2b(w.encode(), digest_size=8).hexdigest(), 16)
            v[h % embeddings.EMBEDDING_DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def fake_embedder(monkeypatch):
    fake = FakeEmbedder()
    monkeypatch.setattr(embeddings, "get_embedder", lambda: fake)
    monkeypatch.setattr(get_settings(), "semantic_min_score", 0.3)
    return fake


async def run_all_jobs():
    while await worker.run_once(get_sessionmaker(), get_settings(), "test"):
        pass


async def add_md(client, name, body):
    r = await client.post("/api/sources", files={"file": (name, body.encode())})
    assert r.status_code == 200, r.text
    return r.json()["source"]


async def test_extraction_chains_embedding_job(auth_client, db, fake_embedder):
    await add_md(auth_client, "a.md", "# Fatiga\n\nLa fatiga periférica.\n\n## Volumen\n\nSeries.")
    await run_all_jobs()
    embed_job = await db.scalar(select(Job).where(Job.kind == "source.embed"))
    assert embed_job.status == JobStatus.completed
    assert embed_job.result == {"embedded": 2, "model": "fake:v1"}
    assert await db.scalar(select(func.count()).select_from(SegmentEmbedding)) == 2


async def test_embedding_is_idempotent(auth_client, db, fake_embedder):
    src = await add_md(auth_client, "a.md", "Texto uno.")
    await run_all_jobs()
    async with get_sessionmaker()() as s:
        s.add(kos.ingestion.pipeline.embed_job(__import__("uuid").UUID(src["versions"][0]["id"])))
        await s.commit()
    await run_all_jobs()
    assert await db.scalar(select(func.count()).select_from(SegmentEmbedding)) == 1


async def test_semantic_finds_other_language_without_shared_words(auth_client, fake_embedder):
    await add_md(auth_client, "neuro.md", "El reclutamiento de unidades motoras aumenta.")
    await add_md(auth_client, "otro.md", "Proteína y recuperación tras el entrenamiento.")
    await run_all_jobs()
    q = {"q": "motor unit recruitment"}
    assert (await auth_client.get("/api/search", params={**q, "mode": "keyword"})).json() == []
    hits = (await auth_client.get("/api/search", params={**q, "mode": "semantic"})).json()
    assert hits[0]["source_title"] == "neuro" and hits[0]["match"] == "semantic"
    assert "reclutamiento" in hits[0]["snippet"]


async def test_hybrid_merges_and_marks_both(auth_client, fake_embedder):
    await add_md(
        auth_client, "fallo.md", "Las repeticiones en reserva miden la proximidad al fallo."
    )
    await add_md(auth_client, "otro.md", "Nada que ver con el tema.")
    await run_all_jobs()
    hits = (await auth_client.get("/api/search", params={"q": "proximidad al fallo"})).json()
    assert hits[0]["source_title"] == "fallo" and hits[0]["match"] == "both"
    assert hits[0]["keyword_score"] and hits[0]["semantic_score"]


async def test_hybrid_works_without_embedding_model(auth_client, monkeypatch):
    def missing():
        raise embeddings.EmbeddingsUnavailable("not installed")

    monkeypatch.setattr(embeddings, "get_embedder", missing)
    await add_md(auth_client, "fallo.md", "Proximidad al fallo.")
    await run_all_jobs()  # the embed job fails permanently: model not installed
    hits = (await auth_client.get("/api/search", params={"q": "fallo"})).json()
    assert len(hits) == 1 and hits[0]["match"] == "keyword"
    r = await auth_client.get("/api/search", params={"q": "fallo", "mode": "semantic"})
    assert r.status_code == 503


async def test_vectors_from_another_model_are_ignored(auth_client, db, fake_embedder, monkeypatch):
    await add_md(auth_client, "neuro.md", "El reclutamiento de unidades motoras aumenta.")
    await run_all_jobs()
    monkeypatch.setattr(fake_embedder, "model_id", "fake:v2")
    r = await auth_client.get("/api/search", params={"q": "reclutamiento", "mode": "semantic"})
    assert r.json() == []


@pytest.mark.skipif(not embeddings.embeddings_available(), reason="real embedding model not set")
async def test_real_model_cross_language(auth_client):
    await add_md(
        auth_client, "neuro.md", "El reclutamiento de unidades motoras aumenta con la intensidad."
    )
    await add_md(
        auth_client, "nutri.md", "La ingesta de proteína favorece la síntesis proteica muscular."
    )
    await run_all_jobs()
    hits = (
        await auth_client.get(
            "/api/search", params={"q": "motor unit recruitment", "mode": "semantic"}
        )
    ).json()
    assert hits and hits[0]["source_title"] == "neuro"
