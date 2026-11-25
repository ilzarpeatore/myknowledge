"""Full-text search over source segments (Spanish + English stemming)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from kos.api.deps import DbDep, UserDep

router = APIRouter(prefix="/api/search", tags=["search"])

# Highlight markers: control characters never present in stored text (they are
# stripped at extraction), so the client can split on them safely.
HL_START, HL_END = "\x02", "\x03"

SEARCH_SQL = text(
    """
    WITH q AS (
        SELECT websearch_to_tsquery('spanish', :q) || websearch_to_tsquery('english', :q) AS query
    )
    SELECT seg.id AS segment_id, seg.loc_page_start AS page, seg.loc_section AS section,
           s.id AS source_id, s.title AS source_title, v.id AS version_id, v.mime AS mime,
           ts_rank_cd(seg.tsv, q.query) AS rank,
           ts_headline('spanish', seg.text, q.query,
               'StartSel=' || chr(2) || ', StopSel=' || chr(3) ||
               ', MaxFragments=2, MaxWords=35, MinWords=12, FragmentDelimiter=" … "') AS snippet
    FROM source_segments seg
    JOIN source_versions v ON v.id = seg.source_version_id
    JOIN sources s ON s.id = v.source_id, q
    WHERE seg.tsv @@ q.query AND s.status <> 'archived'
    ORDER BY rank DESC, s.title, seg.ordinal
    LIMIT :limit
    """
)


class Hit(BaseModel):
    segment_id: uuid.UUID
    source_id: uuid.UUID
    version_id: uuid.UUID
    source_title: str
    mime: str
    page: int | None
    section: str | None
    snippet: str
    rank: float


@router.get("", response_model=list[Hit])
async def search(
    db: DbDep,
    _: UserDep,
    q: Annotated[str, Query(min_length=1, max_length=300)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    rows = (await db.execute(SEARCH_SQL, {"q": q, "limit": limit})).mappings().all()
    return [Hit(**row) for row in rows]
