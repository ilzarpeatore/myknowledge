"""Claim vectors (same local model as the fragments) and similarity helpers.

A claim's vector is cached in claim_embeddings and recomputed when its text or the
model changes. Without the model installed, callers get ``None`` and fall back to
what can be done without similarity (each claim on its own)."""

import asyncio
import hashlib
import uuid

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kos import embeddings
from kos.models import Claim, ClaimEmbedding


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def claim_vectors(
    db: AsyncSession, claims: list[Claim]
) -> dict[uuid.UUID, np.ndarray] | None:
    if not claims:
        return {}
    try:
        embedder = await asyncio.to_thread(embeddings.get_embedder)
    except embeddings.EmbeddingsUnavailable:
        return None
    ids = [c.id for c in claims]
    cached = {
        e.claim_id: e
        for e in (
            await db.scalars(select(ClaimEmbedding).where(ClaimEmbedding.claim_id.in_(ids)))
        ).all()
    }
    todo = [
        c
        for c in claims
        if c.id not in cached
        or cached[c.id].text_sha != _sha(c.text)
        or cached[c.id].model != embedder.model_id
    ]
    out: dict[uuid.UUID, np.ndarray] = {
        k: np.asarray(v.embedding, dtype=np.float32) for k, v in cached.items()
    }
    if todo:
        vecs = await asyncio.to_thread(embedder.embed_passages, [c.text for c in todo])
        for c, v in zip(todo, vecs, strict=True):
            await db.execute(
                insert(ClaimEmbedding)
                .values(claim_id=c.id, model=embedder.model_id, text_sha=_sha(c.text), embedding=v)
                .on_conflict_do_update(
                    index_elements=[ClaimEmbedding.claim_id],
                    set_={"model": embedder.model_id, "text_sha": _sha(c.text), "embedding": v},
                )
            )
            out[c.id] = np.asarray(v, dtype=np.float32)
        await db.commit()
    return out


def unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n else v


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(unit(a), unit(b)))


def group(
    ids: list[uuid.UUID],
    vecs: dict[uuid.UUID, np.ndarray] | None,
    threshold: float,
    apart: dict[uuid.UUID, set[uuid.UUID]] | None = None,
):
    """Greedy grouping: each claim joins the most similar group whose centroid is at
    least ``threshold`` similar, else starts its own. Deterministic (input order).
    Claims recorded as contradicting each other (``apart``) never share a group: the
    vectors capture the topic, not the stance (measured: «siempre al fallo» vs «1-3 RIR
    basta» scored 0.91 with multilingual-e5-small)."""
    if not vecs:
        return [[i] for i in ids]
    apart = apart or {}
    groups: list[list[uuid.UUID]] = []
    centroids: list[np.ndarray] = []
    for i in ids:
        v = unit(vecs[i])
        best, best_sim = None, threshold
        for g, c in enumerate(centroids):
            if any(j in apart.get(i, ()) for j in groups[g]):
                continue
            s = float(np.dot(v, unit(c)))
            if s > best_sim or (best is None and s >= best_sim):  # ties: the earlier group
                best, best_sim = g, s
        if best is None:
            groups.append([i])
            centroids.append(v.copy())
        else:
            groups[best].append(i)
            centroids[best] = centroids[best] + v
    return groups
