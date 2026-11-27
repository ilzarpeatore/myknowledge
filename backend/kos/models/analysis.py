"""Crossing and analysing content: bibliographic references found in the sources,
claim vectors (to find what other sources say the same), and the checks of a
teacher's claims against the studies. Everything computed or proposed by the AI is
stored with its origin and a reviewable status, as in the knowledge model."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kos.models.base import Base, created_at_col, uuid_pk


def _fk(target: str, ondelete: str = "RESTRICT", **kw: Any) -> Any:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), **kw)


class Reference(Base):
    """A work cited somewhere in the library, grouped by first author and year
    («schoenfeld 2017»), or by DOI when that is all there is."""

    __tablename__ = "bib_references"

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    first_author: Mapped[str | None] = mapped_column(String(300))
    year: Mapped[int | None] = mapped_column(Integer)
    authors_text: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(200), index=True)
    # Two bibliography entries with this key had different titles: maybe two works.
    ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    publication_id: Mapped[uuid.UUID | None] = _fk("publications.id", "SET NULL", index=True)
    # «son la misma»: later scans put this key's mentions on the other reference
    merged_into_id: Mapped[uuid.UUID | None] = _fk("bib_references.id", "SET NULL")
    # active | dismissed (not a reference: «Curso (2019)»)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferenceMention(Base):
    """One place where a reference appears: the fragment and the exact characters."""

    __tablename__ = "reference_mentions"
    __table_args__ = (UniqueConstraint("segment_id", "char_start"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    reference_id: Mapped[uuid.UUID] = _fk(
        "bib_references.id", "CASCADE", nullable=False, index=True
    )
    segment_id: Mapped[uuid.UUID] = _fk("source_segments.id", "CASCADE", nullable=False)
    source_id: Mapped[uuid.UUID] = _fk("sources.id", "CASCADE", nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # in_text|bibliography|doi
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)


class ClaimEmbedding(Base):
    __tablename__ = "claim_embeddings"

    claim_id: Mapped[uuid.UUID] = _fk("claims.id", "CASCADE", primary_key=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    text_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(384), nullable=False)


class ClaimCheck(Base):
    """A teacher's claim (or any non-study claim) checked against a study's claim."""

    __tablename__ = "claim_checks"
    __table_args__ = (UniqueConstraint("claim_id", "study_claim_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    claim_id: Mapped[uuid.UUID] = _fk("claims.id", "CASCADE", nullable=False, index=True)
    study_claim_id: Mapped[uuid.UUID] = _fk("claims.id", "CASCADE", nullable=False, index=True)
    # supports | contradicts | partial | unrelated
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    similarity: Mapped[float | None] = mapped_column(Float)
    # proposed | accepted | rejected
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="ai")
    ai_run_id: Mapped[uuid.UUID | None] = _fk("ai_runs.id", "SET NULL")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
