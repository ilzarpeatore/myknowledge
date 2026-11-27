"""who put a concept in a branch

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-28 00:10:00

Branches can now be assigned by the AI (task «classify», Haiku). Each assignment
records whether it came from the user or the AI, so the user can review the AI's
and a new pass never overrides theirs. ``entities.classified_at`` marks what the AI
already looked at, so a concept it left without a branch is not sent again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "entity_disciplines",
        sa.Column("origin", sa.String(length=10), server_default="user", nullable=False),
    )
    op.add_column("entities", sa.Column("classified_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("entities", "classified_at")
    op.drop_column("entity_disciplines", "origin")
