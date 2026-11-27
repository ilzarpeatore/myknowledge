"""lessons built from the course materials, their readings and explanations

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-27 21:30:00

A lesson covers a stretch of material (pages of slides, later a time range of the
class video). Its comprehension reading and the feedback on the user's own
explanations use a new AI task, «teach», on Sonnet.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fk(col: str, table: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [col], [f"{target}.id"], name=op.f(f"fk_{table}_{col}_{target}"), ondelete=ondelete
    )


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.add_column("lesson_progress", sa.Column("understanding", sa.Integer(), nullable=True))
    op.create_table(
        "lesson_parts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("lesson_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_version_id", sa.UUID(), nullable=False),
        sa.Column("chapter_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("t_start_ms", sa.Integer(), nullable=True),
        sa.Column("t_end_ms", sa.Integer(), nullable=True),
        _fk("lesson_id", "lesson_parts", "lessons", "CASCADE"),
        _fk("source_version_id", "lesson_parts", "source_versions", "CASCADE"),
        _fk("chapter_id", "lesson_parts", "source_chapters", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lesson_parts")),
    )
    op.create_index(op.f("ix_lesson_parts_lesson_id"), "lesson_parts", ["lesson_id"])
    op.create_index(
        op.f("ix_lesson_parts_source_version_id"), "lesson_parts", ["source_version_id"]
    )
    op.create_table(
        "lesson_readings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("lesson_id", sa.UUID(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("words", sa.Integer(), nullable=False),
        sa.Column("citations", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("ai_run_id", sa.UUID(), nullable=True),
        _created(),
        _fk("lesson_id", "lesson_readings", "lessons", "CASCADE"),
        _fk("ai_run_id", "lesson_readings", "ai_runs", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lesson_readings")),
    )
    op.create_index(op.f("ix_lesson_readings_lesson_id"), "lesson_readings", ["lesson_id"])
    op.create_table(
        "lesson_explanations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("lesson_id", sa.UUID(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("feedback", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("ai_run_id", sa.UUID(), nullable=True),
        _created(),
        _fk("lesson_id", "lesson_explanations", "lessons", "CASCADE"),
        _fk("ai_run_id", "lesson_explanations", "ai_runs", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lesson_explanations")),
    )
    op.create_index(op.f("ix_lesson_explanations_lesson_id"), "lesson_explanations", ["lesson_id"])
    op.execute(
        "INSERT INTO ai_task_routes (task, model_name, effort, max_tokens) "
        "SELECT 'teach', COALESCE("
        "(SELECT name FROM ai_models WHERE name = 'claude-sonnet-5-5' AND enabled), "
        "(SELECT model_name FROM ai_task_routes WHERE task = 'synthesize')), 'medium', 16000 "
        "WHERE EXISTS (SELECT 1 FROM ai_task_routes WHERE task = 'synthesize') "
        "ON CONFLICT (task) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM ai_task_routes WHERE task = 'teach'")
    op.drop_table("lesson_explanations")
    op.drop_table("lesson_readings")
    op.drop_table("lesson_parts")
    op.drop_column("lesson_progress", "understanding")
