"""Library: sources and their immutable versions (the original files)."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
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
    created_at: Mapped[datetime] = created_at_col()

    source: Mapped[Source] = relationship(back_populates="versions")
