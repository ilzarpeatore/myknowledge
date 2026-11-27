# ruff: noqa: S608  (constant SQL built from TASKS above)
"""Haiku 5.5 for the simple, high-volume AI tasks

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-27 12:30:00

Extracting knowledge from a 221-page book was estimated at ~1.55 USD with Opus 5.5
and ~0.04 USD with Haiku 5.5 (0.10/0.50 USD per million tokens vs 4/20). Nothing the
AI proposes is accepted on its own and quotes are checked against the source, so the
quality risk of a cheaper model is bounded. Only routes still on the default model
change: a route the user already chose is kept. Reversible in the «IA» page.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASKS = "('extract', 'classify', 'resolve', 'check')"


def upgrade() -> None:
    op.execute(
        "UPDATE ai_task_routes SET model_name = 'claude-haiku-5-5' "
        f"WHERE task IN {TASKS} AND model_name = 'claude-opus-5-5' "
        "AND EXISTS (SELECT 1 FROM ai_models WHERE name = 'claude-haiku-5-5' AND enabled)"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE ai_task_routes SET model_name = 'claude-opus-5-5' "
        f"WHERE task IN {TASKS} AND model_name = 'claude-haiku-5-5'"
    )
