"""entity comparisons

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-26 08:30:07.685680
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_comparisons",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("ai_run_id", sa.UUID(), nullable=True),
        sa.Column("claim_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("dropped", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_runs.id"],
            name=op.f("fk_entity_comparisons_ai_run_id_ai_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_entity_comparisons_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity_comparisons")),
    )
    op.create_index(
        op.f("ix_entity_comparisons_entity_id"), "entity_comparisons", ["entity_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_entity_comparisons_entity_id"), table_name="entity_comparisons")
    op.drop_table("entity_comparisons")
