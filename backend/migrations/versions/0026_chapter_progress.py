"""reading progress by chapter

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-27 20:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chapter_progress",
        sa.Column("chapter_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chapter_id"],
            ["source_chapters.id"],
            name=op.f("fk_chapter_progress_chapter_id_source_chapters"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chapter_id", name=op.f("pk_chapter_progress")),
    )


def downgrade() -> None:
    op.drop_table("chapter_progress")
