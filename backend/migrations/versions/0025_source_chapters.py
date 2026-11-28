"""chapters of books and their summaries

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-27 19:30:00

A book is studied by chapters: each one gets its page range (from the PDF bookmarks,
the printed index or the headings, editable by hand) and summaries at three levels,
generated only when asked. Summaries use their own AI task, «summarize», on Sonnet:
quality matters more than for extraction and a 60-page chapter costs ~5 cents.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_chapters",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("is_content", sa.Boolean(), nullable=False),
        sa.Column("origin", sa.String(length=12), nullable=False),
        sa.Column("edited", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["source_chapters.id"],
            name=op.f("fk_source_chapters_parent_id_source_chapters"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.id"],
            name=op.f("fk_source_chapters_source_version_id_source_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_chapters")),
    )
    op.create_index(
        op.f("ix_source_chapters_parent_id"), "source_chapters", ["parent_id"], unique=False
    )
    op.create_index(
        "ix_source_chapters_version_position",
        "source_chapters",
        ["source_version_id", "position"],
        unique=False,
    )
    op.create_table(
        "chapter_summaries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("chapter_id", sa.UUID(), nullable=False),
        sa.Column("level", sa.String(length=12), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("words", sa.Integer(), nullable=False),
        sa.Column("citations", sa.Integer(), nullable=False),
        sa.Column("uncited_points", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("ai_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_runs.id"],
            name=op.f("fk_chapter_summaries_ai_run_id_ai_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["chapter_id"],
            ["source_chapters.id"],
            name=op.f("fk_chapter_summaries_chapter_id_source_chapters"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chapter_summaries")),
    )
    op.create_index(
        "ix_chapter_summaries_chapter_level",
        "chapter_summaries",
        ["chapter_id", "level"],
        unique=False,
    )
    # Sonnet when it is there and enabled, the model of «synthesize» otherwise.
    op.execute(
        "INSERT INTO ai_task_routes (task, model_name, effort, max_tokens) "
        "SELECT 'summarize', COALESCE("
        "(SELECT name FROM ai_models WHERE name = 'claude-sonnet-5-5' AND enabled), "
        "(SELECT model_name FROM ai_task_routes WHERE task = 'synthesize')), 'medium', 16000 "
        "WHERE EXISTS (SELECT 1 FROM ai_task_routes WHERE task = 'synthesize') "
        "ON CONFLICT (task) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM ai_task_routes WHERE task = 'summarize'")
    op.drop_index("ix_chapter_summaries_chapter_level", table_name="chapter_summaries")
    op.drop_table("chapter_summaries")
    op.drop_index("ix_source_chapters_version_position", table_name="source_chapters")
    op.drop_index(op.f("ix_source_chapters_parent_id"), table_name="source_chapters")
    op.drop_table("source_chapters")
