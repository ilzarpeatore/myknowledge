"""ingestion: pages and segments

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-25 22:07:15.280124
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_pages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.id"],
            name=op.f("fk_source_pages_source_version_id_source_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_pages")),
        sa.UniqueConstraint(
            "source_version_id", "page_no", name=op.f("uq_source_pages_source_version_id")
        ),
    )
    op.create_table(
        "source_segments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("loc_page_start", sa.Integer(), nullable=True),
        sa.Column("loc_page_end", sa.Integer(), nullable=True),
        sa.Column("loc_slide", sa.Integer(), nullable=True),
        sa.Column("loc_t_start_ms", sa.BigInteger(), nullable=True),
        sa.Column("loc_t_end_ms", sa.BigInteger(), nullable=True),
        sa.Column("loc_section", sa.Text(), nullable=True),
        sa.Column("loc_char_start", sa.Integer(), nullable=True),
        sa.Column("loc_char_end", sa.Integer(), nullable=True),
        sa.Column("speaker", sa.Text(), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('spanish', text) || to_tsvector('english', text)", persisted=True
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.id"],
            name=op.f("fk_source_segments_source_version_id_source_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_segments")),
        sa.UniqueConstraint(
            "source_version_id", "ordinal", name=op.f("uq_source_segments_source_version_id")
        ),
    )
    op.create_index(
        "ix_source_segments_tsv", "source_segments", ["tsv"], unique=False, postgresql_using="gin"
    )
    op.add_column(
        "source_versions",
        sa.Column("extraction_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "source_versions", sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("source_versions", "extracted_at")
    op.drop_column("source_versions", "extraction_info")
    op.drop_index("ix_source_segments_tsv", table_name="source_segments", postgresql_using="gin")
    op.drop_table("source_segments")
    op.drop_table("source_pages")
