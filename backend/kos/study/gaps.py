"""Gap report: what the *library* does not cover well, kept apart from what the
*user* does not master. A concept can appear in both, for different reasons.

Library side (per concept): claims with a source, accepted claims, distinct sources,
claims backed by studies. User side: flashcard reviews (FSRS recall probability,
lapses, share of "again") and quiz answers (accuracy).
"""

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.knowledge import service as ks
from kos.models import (
    Claim,
    ClaimEntity,
    CourseMaterial,
    Entity,
    Evidence,
    Flashcard,
    FlashcardReview,
    QuizAnswer,
    QuizQuestion,
    SourceSegment,
    SourceVersion,
)
from kos.study import cards

# Thresholds (documented in the report itself)
MIN_SOURCES = 2
MIN_CLAIMS = 3
LOW_ACCURACY = 0.6
MIN_ANSWERS = 3
HIGH_AGAIN = 0.4
MIN_REVIEWS = 3
LOW_RECALL = 0.7
MANY_LAPSES = 3
RECENT_DAYS = 60


async def _concept_ids(db: AsyncSession, course_id: uuid.UUID | None) -> list[uuid.UUID]:
    if course_id is None:
        return list((await db.scalars(select(Entity.id).where(Entity.status.in_(ks.LIVE)))).all())
    sources = select(CourseMaterial.source_id).where(CourseMaterial.course_id == course_id)
    return list(
        (
            await db.scalars(
                select(Entity.id)
                .join(ClaimEntity, ClaimEntity.entity_id == Entity.id)
                .join(Evidence, Evidence.claim_id == ClaimEntity.claim_id)
                .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
                .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
                .where(SourceVersion.source_id.in_(sources), Entity.status.in_(ks.LIVE))
                .distinct()
            )
        ).all()
    )


async def _library(db, ids) -> dict[uuid.UUID, dict[str, Any]]:
    rows = (
        await db.execute(
            select(
                ClaimEntity.entity_id,
                Claim.id,
                Claim.review_status,
                Claim.publication_id,
                SourceVersion.source_id,
            )
            .join(Claim, Claim.id == ClaimEntity.claim_id)
            .join(Evidence, Evidence.claim_id == Claim.id)
            .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
            .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
            .where(ClaimEntity.entity_id.in_(ids), Claim.review_status != "rejected")
        )
    ).all()
    acc: dict[uuid.UUID, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for eid, cid, status, pub, sid in rows:
        a = acc[eid]
        a["claims"].add(cid)
        a["sources"].add(sid)
        if status == "accepted":
            a["accepted"].add(cid)
        if pub:
            a["studies"].add(cid)
    return {
        eid: {k: len(acc[eid][k]) for k in ("claims", "accepted", "sources", "studies")}
        for eid in ids
    }


async def _user(db, ids, now) -> dict[uuid.UUID, dict[str, Any]]:
    out: dict[uuid.UUID, dict[str, Any]] = {
        i: {
            "cards": 0,
            "reviews": 0,
            "again": 0,
            "lapses": 0,
            "recall": [],
            "answers": 0,
            "right": 0,
        }
        for i in ids
    }
    for card in (
        await db.scalars(
            select(Flashcard).where(Flashcard.entity_id.in_(ids), Flashcard.status == "active")
        )
    ).all():
        u = out[card.entity_id]
        u["cards"] += 1
        u["lapses"] += card.lapses
        if (r := cards.retrievability(card, now)) is not None:
            u["recall"].append(r)
    since = now - timedelta(days=RECENT_DAYS)
    for eid, rating in (
        await db.execute(
            select(Flashcard.entity_id, FlashcardReview.rating)
            .join(Flashcard, Flashcard.id == FlashcardReview.card_id)
            .where(Flashcard.entity_id.in_(ids), FlashcardReview.reviewed_at >= since)
        )
    ).all():
        out[eid]["reviews"] += 1
        out[eid]["again"] += rating == 1
    for eid, correct in (
        await db.execute(
            select(QuizQuestion.entity_id, QuizAnswer.correct)
            .join(QuizQuestion, QuizQuestion.id == QuizAnswer.question_id)
            .where(QuizQuestion.entity_id.in_(ids), QuizAnswer.correct.is_not(None))
        )
    ).all():
        out[eid]["answers"] += 1
        out[eid]["right"] += bool(correct)
    return out


async def report(
    db: AsyncSession, course_id: uuid.UUID | None = None, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    ids = await _concept_ids(db, course_id)
    names = dict(
        (await db.execute(select(Entity.id, Entity.canonical_name).where(Entity.id.in_(ids)))).all()
    )
    slugs = dict((await db.execute(select(Entity.id, Entity.slug).where(Entity.id.in_(ids)))).all())
    lib, usr = await _library(db, ids), await _user(db, ids, now)
    library_gaps, learning_gaps, not_studied = [], [], []
    for eid in ids:
        base = {"id": str(eid), "name": names[eid], "slug": slugs[eid]}
        L = lib[eid]
        reasons = []
        if L["claims"] == 0:
            reasons.append("sin afirmaciones con fuente")
        else:
            if L["sources"] < MIN_SOURCES:
                reasons.append("una sola fuente" if L["sources"] == 1 else "sin fuentes")
            if L["claims"] < MIN_CLAIMS:
                reasons.append(f"solo {L['claims']} afirmación(es)")
            if L["accepted"] == 0:
                reasons.append("ninguna afirmación revisada")
            if L["studies"] == 0:
                reasons.append("sin respaldo de estudios")
        severe = L["claims"] == 0 or L["sources"] < MIN_SOURCES or L["claims"] < MIN_CLAIMS
        if severe:
            library_gaps.append(
                {
                    **base,
                    **L,
                    "kind": "sin fuentes" if L["claims"] == 0 else "poco desarrollado",
                    "reasons": reasons,
                }
            )
        U = usr[eid]
        recall = round(sum(U["recall"]) / len(U["recall"]), 2) if U["recall"] else None
        accuracy = round(U["right"] / U["answers"], 2) if U["answers"] else None
        again_rate = round(U["again"] / U["reviews"], 2) if U["reviews"] else None
        why = []
        if accuracy is not None and U["answers"] >= MIN_ANSWERS and accuracy < LOW_ACCURACY:
            why.append(f"aciertos en cuestionarios {round(accuracy * 100)} %")
        if again_rate is not None and U["reviews"] >= MIN_REVIEWS and again_rate >= HIGH_AGAIN:
            why.append(f"«otra vez» en el {round(again_rate * 100)} % de los repasos")
        if recall is not None and recall < LOW_RECALL:
            why.append(f"recuerdo estimado {round(recall * 100)} %")
        if U["lapses"] >= MANY_LAPSES:
            why.append(f"{U['lapses']} olvidos")
        user_metrics = {
            "cards": U["cards"],
            "reviews": U["reviews"],
            "recall": recall,
            "quiz_answers": U["answers"],
            "accuracy": accuracy,
            "lapses": U["lapses"],
        }
        if why:
            learning_gaps.append({**base, **user_metrics, "reasons": why})
        elif U["reviews"] == 0 and U["answers"] == 0 and L["claims"] > 0:
            not_studied.append({**base, "cards": U["cards"], "claims": L["claims"]})
    library_gaps.sort(key=lambda x: (x["claims"], x["sources"], x["name"]))
    learning_gaps.sort(
        key=lambda x: (
            x["accuracy"] if x["accuracy"] is not None else 1,
            x["recall"] if x["recall"] is not None else 1,
        )
    )
    return {
        "concepts": len(ids),
        "library_gaps": library_gaps,
        "learning_gaps": learning_gaps,
        "not_studied": sorted(not_studied, key=lambda x: x["name"]),
        "thresholds": {
            "min_sources": MIN_SOURCES,
            "min_claims": MIN_CLAIMS,
            "low_accuracy": LOW_ACCURACY,
            "high_again": HIGH_AGAIN,
            "low_recall": LOW_RECALL,
            "many_lapses": MANY_LAPSES,
            "recent_days": RECENT_DAYS,
        },
    }


async def count_due(db: AsyncSession, now: datetime | None = None) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(Flashcard)
        .where(Flashcard.status == "active", Flashcard.due_at <= (now or datetime.now(UTC)))
    )
