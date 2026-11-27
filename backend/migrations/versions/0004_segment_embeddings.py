"""segment embeddings

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26 05:20:28.685786
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "segment_embeddings",
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=False),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["source_segments.id"],
            name=op.f("fk_segment_embeddings_segment_id_source_segments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("segment_id", name=op.f("pk_segment_embeddings")),
    )
    op.create_index(
        "ix_segment_embeddings_hnsw",
        "segment_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_segment_embeddings_hnsw",
        table_name="segment_embeddings",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("segment_embeddings")
