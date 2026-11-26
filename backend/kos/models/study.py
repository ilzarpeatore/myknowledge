"""Phase 7 - Learning intelligence: flashcards with FSRS spaced repetition, quizzes,
and the history both leave behind (never deleted: the gap report reads it)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kos.models.base import Base, created_at_col, uuid_pk


def _fk(target: str, ondelete: str = "RESTRICT", **kw: Any) -> Any:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), **kw)


class Flashcard(Base):
    """A card about a concept, backed by a fragment of a source (and the claim it
    comes from, if any). AI-written cards start ``proposed`` until accepted."""

    __tablename__ = "flashcards"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = _fk("entities.id", "CASCADE", nullable=False, index=True)
    claim_id: Mapped[uuid.UUID | None] = _fk("claims.id", "SET NULL")
    segment_id: Mapped[uuid.UUID | None] = _fk("source_segments.id", "SET NULL")
    quote: Mapped[str | None] = mapped_column(Text)  # verbatim, from the segment
    front: Mapped[str] = mapped_column(Text, nullable=False)
    back: Mapped[str] = mapped_column(Text, nullable=False)
    # definition | claim | qa
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="qa")
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="user")  # user|auto|ai
    # proposed | active | suspended | archived
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    fsrs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # fsrs.Card.to_dict()
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    reps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lapses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_run_id: Mapped[uuid.UUID | None] = _fk("ai_runs.id", "SET NULL")
    created_at: Mapped[datetime] = created_at_col()


class FlashcardReview(Base):
    __tablename__ = "flashcard_reviews"

    id: Mapped[uuid.UUID] = uuid_pk()
    card_id: Mapped[uuid.UUID] = _fk("flashcards.id", "CASCADE", nullable=False, index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 again, 2 hard, 3 good, 4 easy
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # probability of recall just before this review (FSRS), None for a new card
    retrievability: Mapped[float | None] = mapped_column(Float)
    state_before: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state_after: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[uuid.UUID] = uuid_pk()
    course_id: Mapped[uuid.UUID | None] = _fk("courses.id", "CASCADE", index=True)
    module_id: Mapped[uuid.UUID | None] = _fk("course_modules.id", "CASCADE", index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # generating | ready | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="generating")
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    job_id: Mapped[uuid.UUID | None] = _fk("jobs.id", "SET NULL")
    ai_run_id: Mapped[uuid.UUID | None] = _fk("ai_runs.id", "SET NULL")
    created_at: Mapped[datetime] = created_at_col()


class QuizQuestion(Base):
    """Every question rests on a verbatim quote from a fragment: the correction shows it."""

    __tablename__ = "quiz_questions"

    id: Mapped[uuid.UUID] = uuid_pk()
    quiz_id: Mapped[uuid.UUID] = _fk("quizzes.id", "CASCADE", nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # choice | true_false | short
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[str] | None] = mapped_column(JSONB)
    correct: Mapped[Any] = mapped_column(JSONB, nullable=False)  # index | bool | model answer
    explanation: Mapped[str | None] = mapped_column(Text)
    segment_id: Mapped[uuid.UUID | None] = _fk("source_segments.id", "SET NULL")
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = _fk("entities.id", "SET NULL", index=True)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    quiz_id: Mapped[uuid.UUID] = _fk("quizzes.id", "CASCADE", nullable=False, index=True)
    score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = created_at_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QuizAnswer(Base):
    __tablename__ = "quiz_answers"

    id: Mapped[uuid.UUID] = uuid_pk()
    attempt_id: Mapped[uuid.UUID] = _fk("quiz_attempts.id", "CASCADE", nullable=False, index=True)
    question_id: Mapped[uuid.UUID] = _fk("quiz_questions.id", "CASCADE", nullable=False)
    answer: Mapped[Any] = mapped_column(JSONB, nullable=False)
    # graded automatically (choice, true/false) or by the user against the source (short)
    correct: Mapped[bool | None] = mapped_column()
    created_at: Mapped[datetime] = created_at_col()
