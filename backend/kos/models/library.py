"""Library: sources and their immutable versions (the original files)."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kos.models.base import Base, created_at_col, uuid_pk


class SourceKind(enum.StrEnum):
    document = "document"
    video = "video"
    audio = "audio"
    web = "web"
    note = "note"


class SourceStatus(enum.StrEnum):
    uploaded = "uploaded"  # original stored, not yet extracted
    processing = "processing"
    ready = "ready"
    failed = "failed"
    archived = "archived"  # logical delete: hidden, never physically removed


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[SourceKind] = mapped_column(_enum(SourceKind, "source_kind"), nullable=False)
    status: Mapped[SourceStatus] = mapped_column(
        _enum(SourceStatus, "source_status"), nullable=False, default=SourceStatus.uploaded
    )
    language: Mapped[str | None] = mapped_column(String(16))
    authors_text: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Status before archiving, so restore returns the source to where it was.
    status_before_archive: Mapped[str | None] = mapped_column(String(32))

    versions: Mapped[list["SourceVersion"]] = relationship(
        back_populates="source", order_by="SourceVersion.created_at"
    )


class SourceVersion(Base):
    __tablename__ = "source_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # SHA-256 of the file content; unique => the same file is never stored twice.
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    extractor: Mapped[str | None] = mapped_column(String(100))
    extractor_version: Mapped[str | None] = mapped_column(String(50))
    # Extraction diagnostics, e.g. {"pages_without_text": [3, 4]} (scanned pages).
    extraction_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()

    source: Mapped[Source] = relationship(back_populates="versions")


class SourcePage(Base):
    """Full extracted text of one page of a version (PDF page; whole file for text)."""

    __tablename__ = "source_pages"
    __table_args__ = (UniqueConstraint("source_version_id", "page_no"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based
    text: Mapped[str] = mapped_column(Text, nullable=False)


# Full-text search over Spanish and English at once (sources are multilingual).
TSV_EXPR = "to_tsvector('spanish', text) || to_tsvector('english', text)"


class SourceSegment(Base):
    """A citable fragment. Locators are only stored when the extractor knows them.

    For PDFs ``loc_char_start``/``loc_char_end`` are offsets into the page text
    (``source_pages.text``); for text files, into the whole file.
    """

    __tablename__ = "source_segments"
    __table_args__ = (
        UniqueConstraint("source_version_id", "ordinal"),
        Index("ix_source_segments_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    loc_page_start: Mapped[int | None] = mapped_column(Integer)
    loc_page_end: Mapped[int | None] = mapped_column(Integer)
    loc_slide: Mapped[int | None] = mapped_column(Integer)
    loc_t_start_ms: Mapped[int | None] = mapped_column(BigInteger)
    loc_t_end_ms: Mapped[int | None] = mapped_column(BigInteger)
    loc_section: Mapped[str | None] = mapped_column(Text)
    loc_char_start: Mapped[int | None] = mapped_column(Integer)
    loc_char_end: Mapped[int | None] = mapped_column(Integer)
    speaker: Mapped[str | None] = mapped_column(Text)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(TSV_EXPR, persisted=True))
