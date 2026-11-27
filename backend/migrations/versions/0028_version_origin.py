"""where each stored original came from

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-27 23:30:00

The daily backup leaves out the files the user uploaded (they keep their own
copy), so every version records its origin: «upload», «youtube» (downloaded by
the worker) or «mcp» (a note an agent saved). Existing rows are classified from
the audit log; when in doubt a file is NOT «upload», so it stays in the backups.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_versions",
        sa.Column("origin", sa.String(length=10), server_default="upload", nullable=False),
    )
    op.execute(
        "UPDATE source_versions v SET origin = 'youtube' FROM sources s "
        "WHERE s.id = v.source_id AND (s.url ~* '(youtube\\.com|youtu\\.be)' OR EXISTS ("
        "SELECT 1 FROM audit_logs a WHERE a.action = 'source.youtube_fetched' "
        "AND a.subject_id = s.id::text))"
    )
    op.execute(
        "UPDATE source_versions v SET origin = 'mcp' FROM sources s "
        "WHERE s.id = v.source_id AND v.origin = 'upload' AND (s.authors_text = 'Claude (MCP)' "
        "OR EXISTS (SELECT 1 FROM audit_logs a WHERE a.action = 'mcp.save_note' "
        "AND a.subject_id = s.id::text))"
    )


def downgrade() -> None:
    op.drop_column("source_versions", "origin")
