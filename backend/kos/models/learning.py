"""Learning Center: what the user studies and their progress.

Discipline/area (``disciplines`` tree) > program ("formación") > course > module > lesson.
Course materials are library sources, optionally attached to a lesson; the link
to knowledge is derived (claims cite segments of those sources), never copied.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kos.models.base import Base, created_at_col, uuid_pk

STATUSES = ("pending", "in_progress", "paused", "completed", "archived")
_STATUS_CHECK = "status IN (" + ",".join(f"'{s}'" for s in STATUSES) + ")"


def _fk(target: str, ondelete: str, **kw):
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), **kw)


class Program(Base):
    """A training programme ("formación"), e.g. a master or certification."""

    __tablename__ = "programs"
    __table_args__ = (CheckConstraint(_STATUS_CHECK, name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(300))  # institution / school
    teacher_person_id: Mapped[uuid.UUID | None] = _fk("persons.id", "SET NULL")
    discipline_id: Mapped[uuid.UUID | None] = _fk("disciplines.id", "SET NULL")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str | None] = mapped_column(Text)
    goals: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (CheckConstraint(_STATUS_CHECK, name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    program_id: Mapped[uuid.UUID | None] = _fk("programs.id", "SET NULL", index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    teacher_person_id: Mapped[uuid.UUID | None] = _fk("persons.id", "SET NULL")
    discipline_id: Mapped[uuid.UUID | None] = _fk("disciplines.id", "SET NULL")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    url: Mapped[str | None] = mapped_column(Text)
    goals: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CourseModule(Base):
    __tablename__ = "course_modules"

    id: Mapped[uuid.UUID] = uuid_pk()
    course_id: Mapped[uuid.UUID] = _fk("courses.id", "CASCADE", nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Lesson(Base):
    __tablename__ = "lessons"

    id: Mapped[uuid.UUID] = uuid_pk()
    module_id: Mapped[uuid.UUID] = _fk("course_modules.id", "CASCADE", nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)


class CourseMaterial(Base):
    """A library source used in a course, optionally for a specific lesson."""

    __tablename__ = "course_materials"
    __table_args__ = (
        UniqueConstraint("course_id", "lesson_id", "source_id", postgresql_nulls_not_distinct=True),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    course_id: Mapped[uuid.UUID] = _fk("courses.id", "CASCADE", nullable=False, index=True)
    lesson_id: Mapped[uuid.UUID | None] = _fk("lessons.id", "CASCADE", index=True)
    source_id: Mapped[uuid.UUID] = _fk("sources.id", "CASCADE", nullable=False, index=True)
    # material | slides | transcript | notes | exercise | reading
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="material")


class LessonProgress(Base):
    __tablename__ = "lesson_progress"
    __table_args__ = (
        CheckConstraint("status IN ('not_started','in_progress','completed')", name="status"),
    )

    lesson_id: Mapped[uuid.UUID] = _fk("lessons.id", "CASCADE", primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_started")
    time_spent_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)  # personal study notes
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_access_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # best score (0-100) of the user's own explanations of the lesson
    understanding: Mapped[int | None] = mapped_column(Integer)


class LessonPart(Base):
    """The stretch of material a lesson covers: pages of a document (slides, a book
    chapter) and, for recorded classes, a time range of a video."""

    __tablename__ = "lesson_parts"

    id: Mapped[uuid.UUID] = uuid_pk()
    lesson_id: Mapped[uuid.UUID] = _fk("lessons.id", "CASCADE", nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_version_id: Mapped[uuid.UUID] = _fk(
        "source_versions.id", "CASCADE", nullable=False, index=True
    )
    chapter_id: Mapped[uuid.UUID | None] = _fk("source_chapters.id", "SET NULL")
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="slides")
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    t_start_ms: Mapped[int | None] = mapped_column(Integer)
    t_end_ms: Mapped[int | None] = mapped_column(Integer)


class LessonReading(Base):
    """The comprehension reading of a lesson, in layers: what the material says, what
    the rest of the library adds, and the AI's explanation. The newest is current."""

    __tablename__ = "lesson_readings"

    id: Mapped[uuid.UUID] = uuid_pk()
    lesson_id: Mapped[uuid.UUID] = _fk("lessons.id", "CASCADE", nullable=False, index=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    words: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    citations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="ai")
    ai_run_id: Mapped[uuid.UUID | None] = _fk("ai_runs.id", "SET NULL")
    created_at: Mapped[datetime] = created_at_col()


class LessonExplanation(Base):
    """«Explícalo con tus palabras»: what the user wrote and the AI's feedback."""

    __tablename__ = "lesson_explanations"

    id: Mapped[uuid.UUID] = uuid_pk()
    lesson_id: Mapped[uuid.UUID] = _fk("lessons.id", "CASCADE", nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    feedback: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    score: Mapped[int | None] = mapped_column(Integer)
    ai_run_id: Mapped[uuid.UUID | None] = _fk("ai_runs.id", "SET NULL")
    created_at: Mapped[datetime] = created_at_col()
