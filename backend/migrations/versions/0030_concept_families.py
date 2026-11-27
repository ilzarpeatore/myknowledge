"""families of concepts

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-28 01:00:00

A family groups concepts under a head concept («Sentadilla» → bilateral, completa,
lateral…; «Músculos del cuello» → estilohioideo…). It is organisation, not a claim:
it lives in ``entities.parent_id`` instead of the relationships, which always need
cited evidence. The AI proposes families per branch (task «organize», Haiku) as
review tasks; the user approves each one, choosing its members. Also the route
«dedupe» (Sonnet): a second opinion on the pairs Haiku calls duplicates.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("entities", sa.Column("parent_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_entities_parent_id_entities"),
        "entities",
        "entities",
        ["parent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_entities_parent_id"), "entities", ["parent_id"])
    op.execute(
        "INSERT INTO ai_task_routes (task, model_name, effort, max_tokens) "
        "SELECT 'organize', COALESCE("
        "(SELECT name FROM ai_models WHERE name = 'claude-haiku-5-5' AND enabled), "
        "(SELECT model_name FROM ai_task_routes WHERE task = 'classify')), 'medium', 16000 "
        "WHERE EXISTS (SELECT 1 FROM ai_task_routes WHERE task = 'classify') "
        "ON CONFLICT (task) DO NOTHING"
    )
    # second opinion on the pairs Haiku calls duplicates (a wrong «same» is costly)
    op.execute(
        "INSERT INTO ai_task_routes (task, model_name, effort, max_tokens) "
        "SELECT 'dedupe', COALESCE("
        "(SELECT name FROM ai_models WHERE name = 'claude-sonnet-5-5' AND enabled), "
        "(SELECT model_name FROM ai_task_routes WHERE task = 'resolve')), 'medium', 8000 "
        "WHERE EXISTS (SELECT 1 FROM ai_task_routes WHERE task = 'resolve') "
        "ON CONFLICT (task) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM ai_task_routes WHERE task IN ('organize', 'dedupe')")
    op.drop_index(op.f("ix_entities_parent_id"), table_name="entities")
    op.drop_constraint(op.f("fk_entities_parent_id_entities"), "entities", type_="foreignkey")
    op.drop_column("entities", "parent_id")
