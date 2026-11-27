"""Knowledge model: entities (concepts, principles, ...), aliases, typed relationships,
claims with verifiable evidence, people and publications, review queue.

Everything an AI proposes is stored with ``origin="ai"`` and a reviewable status;
nothing is merged or deleted silently. Evidence points to a source segment and
holds a quote that is verified to be an exact substring of that segment.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kos.models.base import Base, created_at_col, uuid_pk


def _fk(target: str, ondelete: str = "RESTRICT", **kw: Any) -> Any:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), **kw)


class EntityType(Base):
    __tablename__ = "entity_types"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RelationshipType(Base):
    __tablename__ = "relationship_types"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)  # "A <label> B"
    inverse_label: Mapped[str] = mapped_column(String(100), nullable=False)  # "B <inv> A"
    is_symmetric: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Discipline(Base):
    """Tree: discipline > area > sub-area."""

    __tablename__ = "disciplines"

    id: Mapped[uuid.UUID] = uuid_pk()
    parent_id: Mapped[uuid.UUID | None] = _fk("disciplines.id")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = uuid_pk()
    type_key: Mapped[str] = mapped_column(ForeignKey("entity_types.key"), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False)
    slug: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    short_definition: Mapped[str | None] = mapped_column(Text)
    # proposed | active | merged | archived
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    merged_into_id: Mapped[uuid.UUID | None] = _fk("entities.id")
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="user")  # ai|user
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EntityDiscipline(Base):
    __tablename__ = "entity_disciplines"

    entity_id: Mapped[uuid.UUID] = _fk("entities.id", "CASCADE", primary_key=True)
    discipline_id: Mapped[uuid.UUID] = _fk("disciplines.id", "CASCADE", primary_key=True)


class EntityAlias(Base):
    """Names an entity is known by (synonyms, translations, abbreviations).
    ``alias_normalized`` (lowercase, no accents or punctuation) is what matching uses."""

    __tablename__ = "entity_aliases"
    __table_args__ = (
        UniqueConstraint("entity_id", "alias_normalized"),
        Index(
            "ix_entity_aliases_trgm",
            "alias_normalized",
            postgresql_using="gin",
            postgresql_ops={"alias_normalized": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = _fk("entities.id", "CASCADE", nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(300), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    language: Mapped[str | None] = mapped_column(String(10))
    # canonical | synonym | translation | abbreviation | variant
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="synonym")
    source_segment_id: Mapped[uuid.UUID | None] = _fk("source_segments.id")


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str | None] = mapped_column(String(30))  # teacher | researcher | author
    affiliation: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    doi: Mapped[str | None] = mapped_column(String(200), unique=True)
    journal: Mapped[str | None] = mapped_column(Text)
    study_type: Mapped[str | None] = mapped_column(String(100))
    design: Mapped[str | None] = mapped_column(Text)
    population: Mapped[str | None] = mapped_column(Text)
    sample_size: Mapped[int | None] = mapped_column(Integer)
    variables: Mapped[str | None] = mapped_column(Text)
    results: Mapped[str | None] = mapped_column(Text)
    limitations: Mapped[str | None] = mapped_column(Text)
    # not_assessed | low | moderate | high — never inferred, only set by the user
    methodological_quality: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_assessed"
    )
    quality_notes: Mapped[str | None] = mapped_column(Text)
    quality_assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authors_text: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    # Bibliographic record type as given by the registry (journal-article, ...)
    record_type: Mapped[str | None] = mapped_column(String(60))
    # manual | crossref — where the bibliographic metadata came from
    metadata_source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    metadata_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


CLAIM_TYPES = (
    "definition",
    "reported_fact",
    "hypothesis",
    "interpretation",
    "opinion",
    "recommendation",
    "ai_inference",
    "personal_conclusion",
)


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(
            "claim_type IN (" + ",".join(f"'{t}'" for t in CLAIM_TYPES) + ")", name="claim_type"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(30), nullable=False)
    attributed_person_id: Mapped[uuid.UUID | None] = _fk("persons.id")
    publication_id: Mapped[uuid.UUID | None] = _fk("publications.id")
    context: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(10))
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    # pending | accepted | rejected | edited
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="user")
    ai_run_id: Mapped[uuid.UUID | None] = _fk("ai_runs.id", "SET NULL")
    created_at: Mapped[datetime] = created_at_col()


class ClaimEntity(Base):
    __tablename__ = "claim_entities"

    claim_id: Mapped[uuid.UUID] = _fk("claims.id", "CASCADE", primary_key=True)
    entity_id: Mapped[uuid.UUID] = _fk("entities.id", "CASCADE", primary_key=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="subject")


class Evidence(Base):
    """Where a claim comes from. ``quote`` is an exact substring of the segment text
    at [quote_char_start, quote_char_end). A cited segment cannot be deleted."""

    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = uuid_pk()
    claim_id: Mapped[uuid.UUID] = _fk("claims.id", "CASCADE", nullable=False, index=True)
    segment_id: Mapped[uuid.UUID] = _fk("source_segments.id", nullable=False, index=True)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = created_at_col()


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id", "type_key"),
        CheckConstraint("source_entity_id <> target_entity_id", name="no_self_loop"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_entity_id: Mapped[uuid.UUID] = _fk("entities.id", "CASCADE", nullable=False, index=True)
    target_entity_id: Mapped[uuid.UUID] = _fk("entities.id", "CASCADE", nullable=False, index=True)
    type_key: Mapped[str] = mapped_column(ForeignKey("relationship_types.key"), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    conditions: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="user")
    ai_run_id: Mapped[uuid.UUID | None] = _fk("ai_runs.id", "SET NULL")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class RelationshipEvidence(Base):
    __tablename__ = "relationship_evidence"
    __table_args__ = (
        CheckConstraint(
            "claim_id IS NOT NULL OR segment_id IS NOT NULL", name="has_claim_or_segment"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    relationship_id: Mapped[uuid.UUID] = _fk("relationships.id", "CASCADE", nullable=False)
    claim_id: Mapped[uuid.UUID | None] = _fk("claims.id")
    segment_id: Mapped[uuid.UUID | None] = _fk("source_segments.id")
    note: Mapped[str | None] = mapped_column(Text)


class Contradiction(Base):
    __tablename__ = "contradictions"
    __table_args__ = (UniqueConstraint("claim_a_id", "claim_b_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    claim_a_id: Mapped[uuid.UUID] = _fk("claims.id", "CASCADE", nullable=False)
    claim_b_id: Mapped[uuid.UUID] = _fk("claims.id", "CASCADE", nullable=False)
    # candidate | real | context_difference | dismissed
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="candidate")
    analysis_md: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="user")  # user|ai
    ai_run_id: Mapped[uuid.UUID | None] = _fk("ai_runs.id", "SET NULL")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class EntityMerge(Base):
    """Record of a merge with everything it moved, so it can be undone exactly."""

    __tablename__ = "entity_merges"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_entity_id: Mapped[uuid.UUID] = _fk("entities.id", nullable=False)
    target_entity_id: Mapped[uuid.UUID] = _fk("entities.id", nullable=False)
    moved: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewTask(Base):
    """Something a human must decide: a possible duplicate, a proposed relationship,
    a contradiction, an AI claim..."""

    __tablename__ = "review_tasks"
    __table_args__ = (
        Index(
            "uq_review_tasks_open_subject",
            "kind",
            "subject_key",
            unique=True,
            postgresql_where="status = 'open'",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # entity_merge, ...
    # Stable identity of what is being reviewed, to avoid duplicate open tasks.
    subject_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # open | accepted | rejected | dismissed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    resolution_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EntityComparison(Base):
    """An AI comparison of the explanations of one concept. Every finding points to
    claim ids that existed when it was made (``claim_ids`` is that snapshot)."""

    __tablename__ = "entity_comparisons"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = _fk("entities.id", "CASCADE", nullable=False, index=True)
    ai_run_id: Mapped[uuid.UUID | None] = _fk("ai_runs.id", "SET NULL")
    claim_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dropped: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # findings citing unknown claims
    created_at: Mapped[datetime] = created_at_col()


class KnowledgeExtraction(Base):
    """One AI extraction run over a source version. ``last_ordinal`` is the last
    segment fully processed, so an interrupted run resumes where it stopped."""

    __tablename__ = "knowledge_extractions"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_version_id: Mapped[uuid.UUID] = _fk(
        "source_versions.id", "CASCADE", nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = _fk("jobs.id", "SET NULL")
    # running | completed | stopped (budget/error; resumable)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    last_ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EntityDefinition(Base):
    """What a concept is: the current definition, proposals (AI, MCP) waiting for the
    user, and the history of earlier ones. The current one is mirrored in
    ``Entity.short_definition`` (used by search, extraction and MCP)."""

    __tablename__ = "entity_definitions"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = _fk("entities.id", "CASCADE", nullable=False, index=True)
    short: Mapped[str] = mapped_column(Text, nullable=False)  # 1-2 sentences
    extended: Mapped[str | None] = mapped_column(Text)  # optional explanation
    origin: Mapped[str] = mapped_column(String(10), nullable=False)  # user|ai|mcp|imported
    # proposed | current | replaced | rejected
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="proposed")
    # library: from the user's sources | partial | general: general knowledge (AI)
    basis: Mapped[str | None] = mapped_column(String(10))
    # verified quotes: [{segment_id, quote, source_id, source_title, version_id, mime, page...}]
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_run_id: Mapped[uuid.UUID | None] = _fk("ai_runs.id", "SET NULL")
    created_at: Mapped[datetime] = created_at_col()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
