"""Taking a quiz. Choice and true/false questions are graded automatically; short
answers are graded by the user against the model answer and the source quote (no
AI judges them). Every correction shows the passage of the source that settles it."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.library import locate
from kos.models import (
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
    Source,
    SourceSegment,
    SourceVersion,
)


class QuizError(Exception):
    pass


async def reference(db: AsyncSession, segment_id: uuid.UUID | None) -> dict[str, Any] | None:
    """Where a fragment is (for the link to the source)."""
    if segment_id is None:
        return None
    row = (
        await db.execute(
            select(SourceSegment, SourceVersion, Source)
            .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
            .join(Source, Source.id == SourceVersion.source_id)
            .where(SourceSegment.id == segment_id)
        )
    ).first()
    if row is None:
        return None
    seg, ver, src = row
    return {
        "segment_id": str(seg.id),
        "source_id": str(src.id),
        "version_id": str(ver.id),
        "mime": ver.mime,
        "source_title": src.title,
        **locate.location(seg),
    }


def grade(q: QuizQuestion, answer: Any) -> bool | None:
    if q.kind == "choice":
        return isinstance(answer, int) and answer == q.correct
    if q.kind == "true_false":
        return isinstance(answer, bool) and answer == q.correct
    return None  # short: the user compares with the model answer and the source


async def submit(db: AsyncSession, attempt: QuizAttempt, answers: list[dict[str, Any]]) -> None:
    if attempt.finished_at is not None:
        raise QuizError("este intento ya está corregido")
    questions = {
        q.id: q
        for q in (
            await db.scalars(select(QuizQuestion).where(QuizQuestion.quiz_id == attempt.quiz_id))
        ).all()
    }
    seen = set()
    for a in answers:
        qid = uuid.UUID(str(a["question_id"]))
        q = questions.get(qid)
        if q is None or qid in seen:
            raise QuizError("pregunta no válida")
        seen.add(qid)
        db.add(
            QuizAnswer(
                attempt_id=attempt.id,
                question_id=qid,
                answer=a.get("answer"),
                correct=grade(q, a.get("answer")),
            )
        )
    await db.flush()
    await _score(db, attempt)
    await db.commit()


async def self_grade(db: AsyncSession, answer: QuizAnswer, correct: bool) -> None:
    q = await db.get(QuizQuestion, answer.question_id)
    if q.kind != "short":
        raise QuizError("solo se autoevalúan las respuestas abiertas")
    answer.correct = correct
    await db.flush()
    await _score(db, await db.get(QuizAttempt, answer.attempt_id))
    await db.commit()


async def _score(db: AsyncSession, attempt: QuizAttempt) -> None:
    graded = [
        a.correct
        for a in (
            await db.scalars(select(QuizAnswer).where(QuizAnswer.attempt_id == attempt.id))
        ).all()
    ]
    done = [g for g in graded if g is not None]
    attempt.score = round(sum(done) / len(done), 3) if done else None
    if graded and len(done) == len(graded):
        attempt.finished_at = attempt.finished_at or datetime.now(UTC)
