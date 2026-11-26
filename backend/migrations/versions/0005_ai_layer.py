"""ai layer

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26 05:33:44.456984
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_budgets",
        sa.Column("month", sa.String(length=7), nullable=False),
        sa.Column("limit_usd", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("month", name=op.f("pk_ai_budgets")),
    )
    op.create_table(
        "ai_models",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("input_usd_per_mtok", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("output_usd_per_mtok", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("cache_read_usd_per_mtok", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("cache_write_usd_per_mtok", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("supports_fallback", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("prices_as_of", sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_ai_models")),
    )
    op.create_table(
        "prompts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompts")),
        sa.UniqueConstraint("key", "version", name=op.f("uq_prompts_key")),
    )
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task", sa.String(length=50), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=True),
        sa.Column("prompt_id", sa.UUID(), nullable=True),
        sa.Column("model_requested", sa.String(length=100), nullable=False),
        sa.Column("model_served", sa.String(length=100), nullable=True),
        sa.Column("effort", sa.String(length=10), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("stop_reason", sa.String(length=30), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_ai_runs_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["prompt_id"], ["prompts.id"], name=op.f("fk_ai_runs_prompt_id_prompts")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_runs")),
    )
    op.create_index(op.f("ix_ai_runs_task"), "ai_runs", ["task"], unique=False)
    op.create_table(
        "ai_task_routes",
        sa.Column("task", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("effort", sa.String(length=10), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["model_name"], ["ai_models.name"], name=op.f("fk_ai_task_routes_model_name_ai_models")
        ),
        sa.PrimaryKeyConstraint("task", name=op.f("pk_ai_task_routes")),
    )

    # Seed data. Prices: USD per million tokens, Anthropic first-party list prices as
    # of 2026-11-03; editable in the ai_models table. Haiku 5.5 prices apply to
    # prompts up to 100K tokens (higher beyond); its cache prices are estimates
    # (10% read / 125% write of input), not published figures.
    models = sa.table(
        "ai_models",
        sa.column("name", sa.String),
        sa.column("provider", sa.String),
        sa.column("input_usd_per_mtok", sa.Numeric),
        sa.column("output_usd_per_mtok", sa.Numeric),
        sa.column("cache_read_usd_per_mtok", sa.Numeric),
        sa.column("cache_write_usd_per_mtok", sa.Numeric),
        sa.column("supports_fallback", sa.Boolean),
        sa.column("enabled", sa.Boolean),
        sa.column("prices_as_of", sa.String),
    )
    op.bulk_insert(
        models,
        [
            {
                "name": "claude-opus-5-5",
                "provider": "anthropic",
                "input_usd_per_mtok": 4,
                "output_usd_per_mtok": 20,
                "cache_read_usd_per_mtok": 0.20,
                "cache_write_usd_per_mtok": 5,
                "supports_fallback": True,
                "enabled": True,
                "prices_as_of": "2026-11-03",
            },
            {
                "name": "claude-sonnet-5-5",
                "provider": "anthropic",
                "input_usd_per_mtok": 2,
                "output_usd_per_mtok": 10,
                "cache_read_usd_per_mtok": 0.10,
                "cache_write_usd_per_mtok": 2.5,
                "supports_fallback": True,
                "enabled": True,
                "prices_as_of": "2026-11-03",
            },
            {
                "name": "claude-haiku-5-5",
                "provider": "anthropic",
                "input_usd_per_mtok": 0.10,
                "output_usd_per_mtok": 0.50,
                "cache_read_usd_per_mtok": 0.01,
                "cache_write_usd_per_mtok": 0.125,
                "supports_fallback": False,
                "enabled": True,
                "prices_as_of": "2026-11-03",
            },
        ],
    )
    routes = sa.table(
        "ai_task_routes",
        sa.column("task", sa.String),
        sa.column("model_name", sa.String),
        sa.column("effort", sa.String),
        sa.column("max_tokens", sa.Integer),
    )
    op.bulk_insert(
        routes,
        [
            {"task": t, "model_name": "claude-opus-5-5", "effort": e, "max_tokens": m}
            for t, e, m in [
                ("extract", "low", 16000),
                ("classify", "low", 4000),
                ("resolve", "low", 4000),
                ("synthesize", "high", 16000),
                ("compare", "high", 16000),
                ("query", "medium", 16000),
                ("review", "medium", 8000),
                ("check", "low", 1000),
            ]
        ],
    )


def downgrade() -> None:
    op.drop_table("ai_task_routes")
    op.drop_index(op.f("ix_ai_runs_task"), table_name="ai_runs")
    op.drop_table("ai_runs")
    op.drop_table("prompts")
    op.drop_table("ai_models")
    op.drop_table("ai_budgets")
