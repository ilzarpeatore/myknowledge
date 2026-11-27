"""Flashcards and FSRS scheduling.

Cards always belong to a concept and, when generated, to the fragment (and claim)
that backs them, so the answer can be checked against the source. Automatic cards
come from the concept's claims; AI cards are proposals until accepted. Reviews are
appended to ``flashcard_reviews`` and never deleted.
"""

import uuid
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from fsrs import Card, Rating, Scheduler, State
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.config import get_settings
from kos.models import (
    AuditLog,
    Claim,
    ClaimEntity,
    Entity,
    Evidence,
    Flashcard,
    FlashcardReview,
    Person,
    Publication,
)

VERB = {
    "definition": "define",
    "recommendation": "recomienda",
    "reported_fact": "informa",
    "hypothesis": "plantea como hipótesis",
    "interpretation": "interpreta",
    "opinion": "opina",
    "personal_conclusion": "concluyes",
    "ai_inference": "infiere la IA",
}


class StudyError(Exception):
    pass


@lru_cache(maxsize=1)
def scheduler() -> Scheduler:
    return Scheduler(desired_retention=get_settings().study_desired_retention)


def new_state(now: datetime | None = None) -> tuple[dict[str, Any], datetime]:
    card = Card(due=now or datetime.now(UTC))
    return card.to_dict(), card.due


async def resolve_entity(db: AsyncSession, entity_id: uuid.UUID) -> Entity | None:
    e = await db.get(Entity, entity_id)
    while e is not None and e.status == "merged" and e.merged_into_id:
        e = await db.get(Entity, e.merged_into_id)
    return e


async def _who(db, claim: Claim) -> str:
    if claim.publication_id and (pub := await db.get(Publication, claim.publication_id)):
        return f"el estudio «{pub.title}»"
    if claim.attributed_person_id and (p := await db.get(Person, claim.attributed_person_id)):
        return p.name
    return "la fuente"


def add_card(
    db,
    *,
    entity_id,
    front,
    back,
    kind,
    origin,
    status="active",
    claim_id=None,
    segment_id=None,
    quote=None,
    ai_run_id=None,
) -> Flashcard:
    state, due = new_state()
    card = Flashcard(
        entity_id=entity_id,
        claim_id=claim_id,
        segment_id=segment_id,
        quote=quote,
        front=front.strip(),
        back=back.strip(),
        kind=kind,
        origin=origin,
        status=status,
        fsrs=state,
        due_at=due,
        ai_run_id=ai_run_id,
    )
    db.add(card)
    return card


async def generate_from_claims(db: AsyncSession, entity: Entity) -> list[Flashcard]:
    """One card per reviewed claim (accepted, or written by the user) that has no card
    yet. Unreviewed AI claims are not study material until the user accepts them."""
    claims = (
        await db.scalars(
            select(Claim)
            .join(ClaimEntity, ClaimEntity.claim_id == Claim.id)
            .where(ClaimEntity.entity_id == entity.id, Claim.review_status == "accepted")
            .order_by(Claim.created_at)
        )
    ).all()
    existing = set(
        (
            await db.scalars(
                select(Flashcard.claim_id).where(
                    Flashcard.entity_id == entity.id, Flashcard.claim_id.is_not(None)
                )
            )
        ).all()
    )
    created = []
    for c in claims:
        if c.id in existing:
            continue
        ev = await db.scalar(select(Evidence).where(Evidence.claim_id == c.id).limit(1))
        if ev is None:
            continue
        who = await _who(db, c)
        if c.claim_type == "definition":
            front = f"¿Cómo define {who} «{entity.canonical_name}»?"
        else:
            front = f"{entity.canonical_name}: ¿qué {VERB.get(c.claim_type, 'dice')} {who}?"
        created.append(
            add_card(
                db,
                entity_id=entity.id,
                front=front,
                back=c.text,
                kind="claim",
                origin="auto",
                claim_id=c.id,
                segment_id=ev.segment_id,
                quote=ev.quote,
            )
        )
    if created:
        db.add(
            AuditLog(
                actor="user",
                action="flashcards.generated",
                subject_table="entities",
                subject_id=str(entity.id),
                after={"cards": len(created), "origin": "auto"},
            )
        )
    await db.commit()
    return created


def retrievability(card: Flashcard, now: datetime | None = None) -> float | None:
    c = Card.from_dict(card.fsrs)
    if c.last_review is None:
        return None
    return float(scheduler().get_card_retrievability(c, current_datetime=now or datetime.now(UTC)))


def preview(card: Flashcard, now: datetime | None = None) -> dict[int, str]:
    """When the card would come back for each rating (for the buttons)."""
    now = now or datetime.now(UTC)
    out = {}
    for r in Rating:
        nxt, _ = scheduler().review_card(Card.from_dict(card.fsrs), r, now)
        out[int(r)] = _human(nxt.due - now)
    return out


def _human(delta) -> str:
    s = max(delta.total_seconds(), 0)
    if s < 3600:
        return f"{max(1, round(s / 60))} min"
    if s < 86400:
        return f"{round(s / 3600)} h"
    if s < 86400 * 60:
        return f"{round(s / 86400)} d"
    return f"{round(s / 86400 / 30)} mes"


async def review(
    db: AsyncSession,
    card: Flashcard,
    rating: int,
    duration_ms: int | None = None,
    now: datetime | None = None,
) -> FlashcardReview:
    if card.status != "active":
        raise StudyError("solo se repasan tarjetas activas")
    if rating not in (1, 2, 3, 4):
        raise StudyError("valoración no válida")
    now = now or datetime.now(UTC)
    before = Card.from_dict(card.fsrs)
    r_before = retrievability(card, now)
    after, _log = scheduler().review_card(before, Rating(rating), now, review_duration=duration_ms)
    log = FlashcardReview(
        card_id=card.id,
        rating=rating,
        reviewed_at=now,
        duration_ms=duration_ms,
        retrievability=r_before,
        state_before=card.fsrs,
        state_after=after.to_dict(),
    )
    if rating == 1 and before.state == State.Review:
        card.lapses += 1
    card.reps += 1
    card.fsrs, card.due_at = after.to_dict(), after.due
    db.add(log)
    await db.commit()
    return log
