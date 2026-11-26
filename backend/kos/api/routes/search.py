"""Search over source segments.

- keyword: PostgreSQL full-text search (Spanish + English stemming), highlighted.
- semantic: nearest segments by local embedding (finds paraphrases and other
  languages: "motor unit recruitment" matches "reclutamiento de unidades motoras").
- hybrid (default): both lists merged with Reciprocal Rank Fusion.
"""

import asyncio
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import text

from kos import embeddings
from kos.api.deps import DbDep, SettingsDep, UserDep
from kos.embeddings import EmbeddingsUnavailable

router = APIRouter(prefix="/api/search", tags=["search"])

# Highlight markers: control characters never present in stored text (they are
# stripped at extraction), so the client can split on them safely.
HL_START, HL_END = "\x02", "\x03"
RRF_K = 60
CANDIDATES = 50
PLAIN_SNIPPET_CHARS = 320

_COMMON = """
    seg.id AS segment_id, seg.loc_page_start AS page, seg.loc_section AS section,
    seg.loc_slide AS slide, seg.loc_t_start_ms AS t_ms,
    s.id AS source_id, s.title AS source_title, v.id AS version_id, v.mime AS mime,
    seg.text AS text
"""

# The f-strings below only splice in the constant column list _COMMON; every
# value (query, vector, model, limit) is a bound parameter.
KEYWORD_SQL = text(
    f"""
    WITH q AS (
        SELECT websearch_to_tsquery('spanish', :q) || websearch_to_tsquery('english', :q) AS query
    )
    SELECT {_COMMON}, ts_rank_cd(seg.tsv, q.query) AS score,
           ts_headline('spanish', seg.text, q.query,
               'StartSel=' || chr(2) || ', StopSel=' || chr(3) ||
               ', MaxFragments=2, MaxWords=35, MinWords=12, FragmentDelimiter=" … "') AS snippet
    FROM source_segments seg
    JOIN source_versions v ON v.id = seg.source_version_id
    JOIN sources s ON s.id = v.source_id, q
    WHERE seg.tsv @@ q.query AND s.status <> 'archived'
      AND (CAST(:sources AS uuid[]) IS NULL OR s.id = ANY(CAST(:sources AS uuid[])))
    ORDER BY score DESC, s.title, seg.ordinal
    LIMIT :limit
    """  # noqa: S608
)

SEMANTIC_SQL = text(
    f"""
    SELECT {_COMMON}, 1 - (e.embedding <=> CAST(:qv AS vector)) AS score, NULL AS snippet
    FROM segment_embeddings e
    JOIN source_segments seg ON seg.id = e.segment_id
    JOIN source_versions v ON v.id = seg.source_version_id
    JOIN sources s ON s.id = v.source_id
    WHERE e.model = :model AND s.status <> 'archived'
      AND (CAST(:sources AS uuid[]) IS NULL OR s.id = ANY(CAST(:sources AS uuid[])))
    ORDER BY e.embedding <=> CAST(:qv AS vector)
    LIMIT :limit
    """  # noqa: S608
)


class Hit(BaseModel):
    segment_id: uuid.UUID
    source_id: uuid.UUID
    version_id: uuid.UUID
    source_title: str
    mime: str
    page: int | None
    section: str | None
    slide: int | None = None
    t_ms: int | None = None
    snippet: str
    match: Literal["keyword", "semantic", "both"]
    keyword_score: float | None = None
    semantic_score: float | None = None


def _plain_snippet(t: str) -> str:
    t = " ".join(t.split())
    return t if len(t) <= PLAIN_SNIPPET_CHARS else t[:PLAIN_SNIPPET_CHARS].rsplit(" ", 1)[0] + " …"


async def _semantic(db, q: str, limit: int, min_score: float, sources=None) -> list[dict]:
    try:
        embedder = await asyncio.to_thread(embeddings.get_embedder)
    except EmbeddingsUnavailable:
        return []
    qv = await asyncio.to_thread(embedder.embed_query, q)
    vec = "[" + ",".join(f"{x:.7f}" for x in qv) + "]"
    rows = (
        (
            await db.execute(
                SEMANTIC_SQL,
                {"qv": vec, "model": embedder.model_id, "limit": limit, "sources": sources},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows if r["score"] >= min_score]


class SemanticUnavailable(Exception):
    pass


async def hybrid_hits(
    db,
    q: str,
    mode: str = "hybrid",
    limit: int = 30,
    min_score: float = 0.8,
    sources: list[uuid.UUID] | None = None,
) -> list[dict]:
    """Ranked fragments for ``q`` (Reciprocal Rank Fusion of words and meaning),
    optionally limited to some sources. Each dict has the segment text and location."""
    keyword: list[dict] = []
    semantic: list[dict] = []
    if mode in ("hybrid", "keyword"):
        res = await db.execute(KEYWORD_SQL, {"q": q, "limit": CANDIDATES, "sources": sources})
        keyword = [dict(r) for r in res.mappings().all()]
    if mode in ("hybrid", "semantic"):
        semantic = await _semantic(db, q, CANDIDATES, min_score, sources)
        if mode == "semantic" and not semantic and not await asyncio.to_thread(_available):
            raise SemanticUnavailable()

    merged: dict[uuid.UUID, dict] = {}
    for rank, row in enumerate(keyword):
        merged[row["segment_id"]] = {
            **row,
            "rrf": 1 / (RRF_K + rank + 1),
            "keyword_score": row["score"],
            "match": "keyword",
        }
    for rank, row in enumerate(semantic):
        hit = merged.get(row["segment_id"])
        if hit is None:
            merged[row["segment_id"]] = hit = {**row, "rrf": 0.0, "match": "semantic"}
        else:
            hit["match"] = "both"
        hit["rrf"] += 1 / (RRF_K + rank + 1)
        hit["semantic_score"] = row["score"]
    return sorted(merged.values(), key=lambda h: h["rrf"], reverse=True)[:limit]


@router.get("", response_model=list[Hit])
async def search(
    db: DbDep,
    s: SettingsDep,
    _: UserDep,
    q: Annotated[str, Query(min_length=1, max_length=300)],
    mode: Literal["hybrid", "keyword", "semantic"] = "hybrid",
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    try:
        ordered = await hybrid_hits(db, q, mode, limit, s.semantic_min_score)
    except SemanticUnavailable:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "búsqueda por significado no disponible"
        ) from None
    return [
        Hit(
            **{k: h[k] for k in ("segment_id", "source_id", "version_id", "source_title", "mime")},
            page=h["page"],
            section=h["section"],
            slide=h.get("slide"),
            t_ms=h.get("t_ms"),
            snippet=h["snippet"] or _plain_snippet(h["text"]),
            match=h["match"],
            keyword_score=h.get("keyword_score"),
            semantic_score=h.get("semantic_score"),
        )
        for h in ordered
    ]


def _available() -> bool:
    try:
        embeddings.get_embedder()
    except EmbeddingsUnavailable:
        return False
    return True
