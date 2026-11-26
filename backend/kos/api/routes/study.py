"""Study: flashcards with FSRS, quizzes per module and the gap report."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from kos.api.deps import DbDep, UserDep
from kos.models import (
    AuditLog,
    CourseModule,
    Entity,
    Flashcard,
    FlashcardReview,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
)
from kos.study import ai_jobs, cards, gaps
from kos.study import quiz as qz

router = APIRouter(prefix="/api", tags=["study"])


def _bad(exc: Exception):
    raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


async def _card_out(db, c: Flashcard, with_preview: bool = False) -> dict[str, Any]:
    e = await db.get(Entity, c.entity_id)
    out = {
        "id": str(c.id),
        "front": c.front,
        "back": c.back,
        "quote": c.quote,
        "kind": c.kind,
        "origin": c.origin,
        "status": c.status,
        "due_at": c.due_at,
        "reps": c.reps,
        "lapses": c.lapses,
        "retrievability": cards.retrievability(c),
        "entity": {"id": str(e.id), "slug": e.slug, "name": e.canonical_name},
        "reference": await qz.reference(db, c.segment_id),
        "claim_id": str(c.claim_id) if c.claim_id else None,
    }
    if with_preview:
        out["preview"] = cards.preview(c)
    return out


# ---- flashcards -----------------------------------------------------------------------


class GenerateIn(BaseModel):
    ai: bool = False


@router.post("/entities/{entity_id}/flashcards/generate")
async def generate(entity_id: uuid.UUID, body: GenerateIn, db: DbDep, _: UserDep):
    e = await cards.resolve_entity(db, entity_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concepto no encontrado")
    created = await cards.generate_from_claims(db, e)
    job_id = None
    if body.ai:
        job = ai_jobs.cards_job(e.id)
        db.add(job)
        await db.commit()
        job_id = str(job.id)
    return {"created": len(created), "job_id": job_id}


@router.get("/flashcards")
async def list_cards(
    db: DbDep,
    _: UserDep,
    entity_id: uuid.UUID | None = None,
    status_filter: Literal["proposed", "active", "suspended", "archived"] | None = None,
    limit: int = Query(200, ge=1, le=1000),
):
    stmt = select(Flashcard)
    if entity_id:
        stmt = stmt.where(Flashcard.entity_id == entity_id)
    if status_filter:
        stmt = stmt.where(Flashcard.status == status_filter)
    else:
        stmt = stmt.where(Flashcard.status != "archived")
    rows = (await db.scalars(stmt.order_by(Flashcard.created_at).limit(limit))).all()
    return [await _card_out(db, c) for c in rows]


class CardIn(BaseModel):
    entity_id: uuid.UUID
    front: str = Field(min_length=2, max_length=2000)
    back: str = Field(min_length=1, max_length=5000)


@router.post("/flashcards", status_code=status.HTTP_201_CREATED)
async def create_card(body: CardIn, db: DbDep, _: UserDep):
    e = await cards.resolve_entity(db, body.entity_id)
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concepto no encontrado")
    c = cards.add_card(
        db, entity_id=e.id, front=body.front, back=body.back, kind="qa", origin="user"
    )
    await db.commit()
    return await _card_out(db, c)


class CardPatch(BaseModel):
    status: Literal["active", "suspended", "archived"] | None = None
    front: str | None = Field(default=None, min_length=2, max_length=2000)
    back: str | None = Field(default=None, min_length=1, max_length=5000)


@router.patch("/flashcards/{card_id}")
async def patch_card(card_id: uuid.UUID, body: CardPatch, db: DbDep, _: UserDep):
    c = await db.get(Flashcard, card_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tarjeta no encontrada")
    changes = body.model_dump(exclude_unset=True)
    before = {k: getattr(c, k) for k in changes}
    for k, v in changes.items():
        setattr(c, k, v)
    if changes.get("status") == "active" and before.get("status") == "proposed":
        c.fsrs, c.due_at = cards.new_state()  # a just-accepted card starts fresh
    db.add(
        AuditLog(
            actor="user",
            action="flashcard.updated",
            subject_table="flashcards",
            subject_id=str(c.id),
            before=before,
            after=changes,
        )
    )
    await db.commit()
    return await _card_out(db, c)


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=4)
    duration_ms: int | None = Field(default=None, ge=0, le=3_600_000)


@router.post("/flashcards/{card_id}/review")
async def review(card_id: uuid.UUID, body: ReviewIn, db: DbDep, _: UserDep):
    c = await db.get(Flashcard, card_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tarjeta no encontrada")
    try:
        log = await cards.review(db, c, body.rating, body.duration_ms)
    except cards.StudyError as exc:
        _bad(exc)
    return {"due_at": c.due_at, "reps": c.reps, "lapses": c.lapses, "review_id": str(log.id)}


@router.get("/flashcards/{card_id}/reviews")
async def card_history(card_id: uuid.UUID, db: DbDep, _: UserDep):
    rows = (
        await db.scalars(
            select(FlashcardReview)
            .where(FlashcardReview.card_id == card_id)
            .order_by(FlashcardReview.reviewed_at)
        )
    ).all()
    return [
        {
            "rating": r.rating,
            "reviewed_at": r.reviewed_at,
            "retrievability": r.retrievability,
            "due_after": r.state_after.get("due"),
        }
        for r in rows
    ]


@router.get("/study/due")
async def due(
    db: DbDep, _: UserDep, limit: int = Query(20, ge=1, le=200), entity_id: uuid.UUID | None = None
):
    stmt = select(Flashcard).where(
        Flashcard.status == "active", Flashcard.due_at <= datetime.now(UTC)
    )
    if entity_id:
        stmt = stmt.where(Flashcard.entity_id == entity_id)
    rows = (await db.scalars(stmt.order_by(Flashcard.due_at).limit(limit))).all()
    return [await _card_out(db, c, with_preview=True) for c in rows]


@router.get("/study/summary")
async def summary(db: DbDep, _: UserDep):
    now = datetime.now(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async def n(stmt):
        return await db.scalar(stmt)

    return {
        "due_now": await gaps.count_due(db, now),
        "due_tomorrow": await n(
            select(func.count())
            .select_from(Flashcard)
            .where(Flashcard.status == "active", Flashcard.due_at <= now + timedelta(days=1))
        ),
        "active": await n(
            select(func.count()).select_from(Flashcard).where(Flashcard.status == "active")
        ),
        "proposed": await n(
            select(func.count()).select_from(Flashcard).where(Flashcard.status == "proposed")
        ),
        "reviewed_today": await n(
            select(func.count())
            .select_from(FlashcardReview)
            .where(FlashcardReview.reviewed_at >= start)
        ),
    }


# ---- quizzes ---------------------------------------------------------------------------


@router.post("/modules/{module_id}/quizzes", status_code=status.HTTP_202_ACCEPTED)
async def create_quiz(module_id: uuid.UUID, db: DbDep, _: UserDep):
    m = await db.get(CourseModule, module_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "módulo no encontrado")
    if not await ai_jobs.module_sources(db, module_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "el módulo no tiene materiales: añade fuentes a sus lecciones"
        )
    count = await db.scalar(
        select(func.count()).select_from(Quiz).where(Quiz.module_id == module_id)
    )
    quiz = Quiz(
        course_id=m.course_id, module_id=m.id, title=f"{m.title} · cuestionario {count + 1}"
    )
    db.add(quiz)
    await db.flush()
    db.add(ai_jobs.quiz_job(quiz.id))
    await db.commit()
    return {"id": str(quiz.id), "status": quiz.status}


async def _quiz_summary(db, q: Quiz) -> dict[str, Any]:
    attempts = (
        await db.scalars(
            select(QuizAttempt).where(QuizAttempt.quiz_id == q.id).order_by(QuizAttempt.created_at)
        )
    ).all()
    return {
        "id": str(q.id),
        "title": q.title,
        "status": q.status,
        "error": q.error,
        "stats": q.stats,
        "module_id": str(q.module_id) if q.module_id else None,
        "course_id": str(q.course_id) if q.course_id else None,
        "created_at": q.created_at,
        "attempts": [
            {
                "id": str(a.id),
                "score": a.score,
                "finished_at": a.finished_at,
                "created_at": a.created_at,
            }
            for a in attempts
        ],
    }


@router.get("/quizzes")
async def list_quizzes(
    db: DbDep, _: UserDep, course_id: uuid.UUID | None = None, module_id: uuid.UUID | None = None
):
    stmt = select(Quiz)
    if course_id:
        stmt = stmt.where(Quiz.course_id == course_id)
    if module_id:
        stmt = stmt.where(Quiz.module_id == module_id)
    rows = (await db.scalars(stmt.order_by(Quiz.created_at.desc()))).all()
    return [await _quiz_summary(db, q) for q in rows]


@router.get("/quizzes/{quiz_id}")
async def get_quiz(quiz_id: uuid.UUID, db: DbDep, _: UserDep):
    """The questions without their answers (to take the quiz)."""
    q = await db.get(Quiz, quiz_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cuestionario no encontrado")
    questions = (
        await db.scalars(
            select(QuizQuestion).where(QuizQuestion.quiz_id == q.id).order_by(QuizQuestion.position)
        )
    ).all()
    return {
        **await _quiz_summary(db, q),
        "questions": [
            {"id": str(x.id), "kind": x.kind, "prompt": x.prompt, "options": x.options}
            for x in questions
        ],
    }


class AnswerIn(BaseModel):
    question_id: uuid.UUID
    answer: int | bool | str | None = None


class AttemptIn(BaseModel):
    answers: list[AnswerIn] = Field(max_length=100)


@router.post("/quizzes/{quiz_id}/attempts", status_code=status.HTTP_201_CREATED)
async def take(quiz_id: uuid.UUID, body: AttemptIn, db: DbDep, _: UserDep):
    q = await db.get(Quiz, quiz_id)
    if q is None or q.status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "el cuestionario no está listo")
    attempt = QuizAttempt(quiz_id=q.id)
    db.add(attempt)
    await db.flush()
    try:
        await qz.submit(db, attempt, [a.model_dump() for a in body.answers])
    except qz.QuizError as exc:
        await db.rollback()
        _bad(exc)
    return await attempt_result(attempt.id, db, None)


@router.get("/quiz-attempts/{attempt_id}")
async def attempt_result(attempt_id: uuid.UUID, db: DbDep, _: UserDep):
    """Correction: each answer with the right one, the explanation and the source passage."""
    a = await db.get(QuizAttempt, attempt_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "intento no encontrado")
    answers = {
        x.question_id: x
        for x in (await db.scalars(select(QuizAnswer).where(QuizAnswer.attempt_id == a.id))).all()
    }
    questions = (
        await db.scalars(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == a.quiz_id)
            .order_by(QuizQuestion.position)
        )
    ).all()
    items = []
    for x in questions:
        ans = answers.get(x.id)
        e = await db.get(Entity, x.entity_id) if x.entity_id else None
        items.append(
            {
                "question_id": str(x.id),
                "kind": x.kind,
                "prompt": x.prompt,
                "options": x.options,
                "correct_answer": x.correct,
                "explanation": x.explanation,
                "quote": x.quote,
                "reference": await qz.reference(db, x.segment_id),
                "concept": {"slug": e.slug, "name": e.canonical_name} if e else None,
                "answer_id": str(ans.id) if ans else None,
                "answer": ans.answer if ans else None,
                "correct": ans.correct if ans else None,
            }
        )
    return {
        "id": str(a.id),
        "quiz_id": str(a.quiz_id),
        "score": a.score,
        "finished_at": a.finished_at,
        "items": items,
    }


class SelfGradeIn(BaseModel):
    correct: bool


@router.patch("/quiz-answers/{answer_id}")
async def self_grade(answer_id: uuid.UUID, body: SelfGradeIn, db: DbDep, _: UserDep):
    ans = await db.get(QuizAnswer, answer_id)
    if ans is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "respuesta no encontrada")
    try:
        await qz.self_grade(db, ans, body.correct)
    except qz.QuizError as exc:
        _bad(exc)
    return await attempt_result(ans.attempt_id, db, None)


# ---- gaps --------------------------------------------------------------------------------


@router.get("/study/gaps")
async def gap_report(db: DbDep, _: UserDep, course_id: uuid.UUID | None = None):
    return await gaps.report(db, course_id)
