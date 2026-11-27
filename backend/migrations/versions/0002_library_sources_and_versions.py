"""library: sources and versions

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-25 21:03:06.080212
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("document", "video", "audio", "web", "note", name="source_kind"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("uploaded", "processing", "ready", "failed", "archived", name="source_status"),
            nullable=False,
        ),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("authors_text", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_before_archive", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
    )
    op.create_table(
        "source_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("extractor", sa.String(length=100), nullable=True),
        sa.Column("extractor_version", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_source_versions_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_versions")),
        sa.UniqueConstraint("sha256", name=op.f("uq_source_versions_sha256")),
    )
    op.create_index(
        op.f("ix_source_versions_source_id"), "source_versions", ["source_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_source_versions_source_id"), table_name="source_versions")
    op.drop_table("source_versions")
    op.drop_table("sources")
    sa.Enum(name="source_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="source_kind").drop(op.get_bind(), checkfirst=True)
