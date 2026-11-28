"""Library: sources and their immutable versions (the original files)."""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Enum,
    Float,
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

if TYPE_CHECKING:
    from kos.models.knowledge import Person


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
    # When the source is the text of a study: claims extracted from it are the study's.
    publication_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("publications.id", ondelete="SET NULL"), index=True
    )

    versions: Mapped[list["SourceVersion"]] = relationship(
        back_populates="source", order_by="SourceVersion.created_at"
    )
    section_rows: Mapped[list["SourceSection"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="SourceSection.section_key"
    )
    discipline_rows: Mapped[list["SourceDiscipline"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan"
    )
    author_rows: Mapped[list["SourceAuthor"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="SourceAuthor.position"
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
    # Where the file came from: «upload» (the user has it: left out of the daily
    # backups), «youtube» (downloaded by the worker) or «mcp» (a note an agent saved).
    origin: Mapped[str] = mapped_column(
        String(10), nullable=False, default="upload", server_default="upload"
    )
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
    # Timed media: [[offset in text, start ms], ...], one entry per cue.
    loc_timeline: Mapped[list[list[int]] | None] = mapped_column(JSONB)
    speaker: Mapped[str | None] = mapped_column(Text)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(TSV_EXPR, persisted=True))


class SegmentEmbedding(Base):
    """Vector of a segment for semantic search. ``model`` records which model made
    it: vectors from different models are never compared."""

    __tablename__ = "segment_embeddings"
    __table_args__ = (
        Index(
            "ix_segment_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    segment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_segments.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(384), nullable=False)


class TranscriptChunk(Base):
    """One transcribed section of an audio/video version. Saved as soon as it is
    done, so a stopped transcription resumes after the last saved section."""

    __tablename__ = "transcript_chunks"
    __table_args__ = (UniqueConstraint("source_version_id", "model", "idx"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # [[start_ms, end_ms, text], ...] in absolute time
    cues: Mapped[list[list[Any]]] = mapped_column(JSONB, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = created_at_col()


class LibrarySection(Base):
    """A section of the library (Cursos, Libros, Estudios...). Built-in ones can be
    renamed; you can add your own."""

    __tablename__ = "library_sections"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SourceSection(Base):
    """A source can be in several sections (a class video: Cursos and Vídeos)."""

    __tablename__ = "source_sections"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    section_key: Mapped[str] = mapped_column(
        ForeignKey("library_sections.key", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True,
        index=True,
    )
    # auto (suggested from the format, a course or a study) | user
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="user")


class SourceLink(Base):
    """A link you make between two sources (e.g. a book a course recommends), stored
    once per pair (a_id < b_id)."""

    __tablename__ = "source_links"
    __table_args__ = (UniqueConstraint("a_id", "b_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    # related | expands | summarizes | contradicts | applies | recommends
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="related")
    # accepted | proposed | rejected (a proposal waits in Revisión)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="accepted")
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="user")  # user|auto|ai
    score: Mapped[float | None] = mapped_column(Float)
    # the passages that justify it: [{"a": segment id, "b": segment id, "similarity": 0.9}]
    evidence: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_runs.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class SourceDiscipline(Base):
    """The branches a source belongs to (Nutrición, Biomecánica, Fisiología...): the
    areas of the Disciplinas tree. A source can be in several."""

    __tablename__ = "source_disciplines"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    discipline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("disciplines.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    discipline: Mapped["Discipline"] = relationship(lazy="selectin")  # noqa: F821


class SourceAuthor(Base):
    """Who wrote or gave a source (the authors of a study, the teacher of a class, the
    author of a book), in order. They are people of the Autores library."""

    __tablename__ = "source_authors"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    person: Mapped["Person"] = relationship(lazy="selectin")


class PdfAnnotation(Base):
    """The user's highlights and notes on a PDF, made in the reader. They are kept here,
    never written into the original file. ``data`` is the viewer's own annotation object
    (Inkbar), stored as it comes; ``text`` is the text that was selected when it was
    made, for lists and search."""

    __tablename__ = "pdf_annotations"
    __table_args__ = (UniqueConstraint("version_id", "ink_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ink_id: Mapped[str] = mapped_column(String(80), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-based, as the viewer
    text: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    reply_to: Mapped[str | None] = mapped_column(String(80))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # what the user made from it: a claim citing the passage, a study card
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="SET NULL")
    )
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flashcards.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceChapter(Base):
    """A chapter or a point inside one (``parent_id``) of a book or long document, by
    page range of a version. Detected from the PDF bookmarks, the printed index or the
    headings, and editable by hand; ``is_content`` is False for the cover, the rights
    page, the index or the bibliography."""

    __tablename__ = "source_chapters"
    __table_args__ = (
        Index("ix_source_chapters_version_position", "source_version_id", "position"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_chapters.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # reading order
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 1 chapter, 2 point
    title: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)  # inclusive
    is_content: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # bookmarks | index | headings | manual
    origin: Mapped[str] = mapped_column(String(12), nullable=False)
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChapterSummary(Base):
    """A summary of a chapter at one level (essential, study, detailed). Each point
    cites the fragments it comes from with a verified quote. Regenerating keeps the
    earlier ones: the newest per level is the current one."""

    __tablename__ = "chapter_summaries"
    __table_args__ = (Index("ix_chapter_summaries_chapter_level", "chapter_id", "level"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    chapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_chapters.id", ondelete="CASCADE"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(12), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    words: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    citations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uncited_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    origin: Mapped[str] = mapped_column(String(10), nullable=False, default="ai")  # ai|mcp
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_runs.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()


class ChapterProgress(Base):
    """Where the user is with a chapter: reading, read, studied."""

    __tablename__ = "chapter_progress"

    chapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_chapters.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
