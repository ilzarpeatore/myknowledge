"""AI configuration and traceability: models and prices, task routing, versioned
prompts, one row per model call, monthly budget."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kos.models.base import Base, created_at_col, uuid_pk


class AIModel(Base):
    """A model and its prices (USD per million tokens). Prices change: they are
    data, editable without a code change, and every run stores its own cost."""

    __tablename__ = "ai_models"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)  # e.g. claude-opus-5-5
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    input_usd_per_mtok: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    output_usd_per_mtok: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    cache_read_usd_per_mtok: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    cache_write_usd_per_mtok: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    # Server-side refusal fallback (`fallbacks: "default"`); not offered on every model.
    supports_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    prices_as_of: Mapped[str | None] = mapped_column(String(20))


class AITaskRoute(Base):
    """Which model (and effort) handles each kind of task."""

    __tablename__ = "ai_task_routes"

    task: Mapped[str] = mapped_column(String(50), primary_key=True)
    model_name: Mapped[str] = mapped_column(ForeignKey("ai_models.name"), nullable=False)
    effort: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=16000)


class Prompt(Base):
    """A prompt version as used. Content is hashed: editing a prompt file without
    bumping its version is refused, so every run points to the exact text it used."""

    __tablename__ = "prompts"
    __table_args__ = (UniqueConstraint("key", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_col()


class AIRun(Base):
    __tablename__ = "ai_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    task: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prompts.id"))
    model_requested: Mapped[str] = mapped_column(String(100), nullable=False)
    model_served: Mapped[str | None] = mapped_column(String(100))  # differs after a fallback
    effort: Mapped[str | None] = mapped_column(String(10))
    # ok | refused | invalid_output | error | budget_exceeded
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(30))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    input_sha256: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at_col()


class AIBudget(Base):
    """Monthly spending limit. A missing row for the month uses the default from settings."""

    __tablename__ = "ai_budgets"

    month: Mapped[str] = mapped_column(String(7), primary_key=True)  # YYYY-MM
    limit_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QueryRecord(Base):
    """A question asked in the AI workspace, its scope, what was retrieved and the
    validated answer. Kept as history; every answer links to its ai_run."""

    __tablename__ = "queries"

    id: Mapped[uuid.UUID] = uuid_pk()
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # global | source | course | discipline | concept
    scope_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="global")
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # research: only the library | application: may reason and add marked general knowledge
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="research")
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("queries.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    answer: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # what was given to the model: concepts and fragments (ids), for traceability
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_runs.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
