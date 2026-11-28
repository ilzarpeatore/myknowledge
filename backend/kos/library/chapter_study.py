"""Studying a book by chapters: reading progress, the concepts each chapter deals
with (claims cited from its pages), and study cards made from the key terms of its
summary (free: no AI call, each card keeps the verified quote it comes from)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.knowledge import service as ks
from kos.library import chapter_summaries as sums
from kos.models import (
    ChapterProgress,
    Claim,
    ClaimEntity,
    Entity,
    Evidence,
    Flashcard,
    SourceChapter,
    SourceSegment,
)
from kos.study import cards

PROGRESS = ("reading", "read", "studied")
MAX_CONCEPTS = 40


async def progress_of(db: AsyncSession, chapter_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not chapter_ids:
        return {}
    rows = (
        await db.execute(
            select(ChapterProgress.chapter_id, ChapterProgress.status).where(
                ChapterProgress.chapter_id.in_(chapter_ids)
            )
        )
    ).all()
    return {cid: st for cid, st in rows}


async def set_progress(db: AsyncSession, ch: SourceChapter, status: str | None) -> str | None:
    row = await db.get(ChapterProgress, ch.id)
    if status is None:
        if row is not None:
            await db.delete(row)
    elif row is None:
        db.add(ChapterProgress(chapter_id=ch.id, status=status, updated_at=datetime.now(UTC)))
    else:
        row.status, row.updated_at = status, datetime.now(UTC)
    await db.commit()
    return status


async def concepts_of(db: AsyncSession, ch: SourceChapter) -> list[dict[str, Any]]:
    """Concepts with claims cited from the chapter's pages, most cited first."""
    n = func.count(func.distinct(Claim.id)).label("n")
    rows = (
        await db.execute(
            select(Entity.id, Entity.slug, Entity.canonical_name, n)
            .join(ClaimEntity, ClaimEntity.entity_id == Entity.id)
            .join(Claim, Claim.id == ClaimEntity.claim_id)
            .join(Evidence, Evidence.claim_id == Claim.id)
            .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
            .where(
                SourceSegment.source_version_id == ch.source_version_id,
                SourceSegment.loc_page_start >= ch.page_start,
                SourceSegment.loc_page_start <= ch.page_end,
                Entity.status.in_(ks.LIVE),
                Claim.review_status != "rejected",
            )
            .group_by(Entity.id, Entity.slug, Entity.canonical_name)
            .order_by(n.desc(), Entity.canonical_name)
            .limit(MAX_CONCEPTS)
        )
    ).all()
    return [{"id": str(i), "slug": s, "name": name, "claims": c} for i, s, name, c in rows]


async def cards_from_summary(db: AsyncSession, ch: SourceChapter) -> dict[str, Any]:
    """One card per key term of the chapter's newest summary that is a concept of the
    library and has a verified quote; terms that are not concepts are reported."""
    current = await sums.current(db, ch.id)
    levels = [lvl for lvl in ("study", "detailed", "essential") if lvl in current]
    summary = current[levels[0]] if levels else None
    if summary is None:
        return {"created": 0, "existing": 0, "not_concepts": [], "no_summary": True}
    created, existing, missing = 0, 0, []
    for t in summary.content.get("key_terms", []):
        cites = t.get("citations") or []
        entity = await ks.entity_with_alias(db, t["term"])
        if entity is None:
            missing.append(t["term"])
            continue
        if not cites:
            continue
        front = f"¿Qué es «{t['term']}»?"
        if await db.scalar(
            select(Flashcard.id).where(Flashcard.entity_id == entity.id, Flashcard.front == front)
        ):
            existing += 1
            continue
        cards.add_card(
            db,
            entity_id=entity.id,
            front=front,
            back=t["definition"],
            kind="qa",
            origin="auto",
            segment_id=uuid.UUID(cites[0]["fragment_id"]),
            quote=cites[0]["quote"],
        )
        created += 1
    await db.commit()
    return {"created": created, "existing": existing, "not_concepts": missing, "no_summary": False}
