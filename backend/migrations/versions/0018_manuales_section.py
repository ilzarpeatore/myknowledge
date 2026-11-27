"""library section «Manuales»

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-27 10:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Right after Libros. A user section already called «Manuales» becomes this one.
    op.execute(
        "UPDATE library_sections SET position = position + 1 "
        "WHERE position > (SELECT position FROM library_sections WHERE key = 'libros') "
        "AND key <> 'manuales'"
    )
    op.execute(
        "INSERT INTO library_sections (key, name, description, position, builtin) "
        "SELECT 'manuales', 'Manuales', 'Manuales técnicos, guías y documentación de uso', "
        "position + 1, true FROM library_sections WHERE key = 'libros' "
        "ON CONFLICT (key) DO UPDATE SET builtin = true, "
        "position = EXCLUDED.position"
    )
    op.execute(
        "UPDATE library_sections SET description = 'Libros' "
        "WHERE key = 'libros' AND description = 'Libros y manuales'"
    )


def downgrade() -> None:
    # Sources only in Manuales go back to Recursos, as when a section is deleted.
    op.execute(
        "INSERT INTO source_sections (source_id, section_key, origin) "
        "SELECT ss.source_id, 'recursos', 'auto' FROM source_sections ss "
        "WHERE ss.section_key = 'manuales' AND NOT EXISTS (SELECT 1 FROM source_sections o "
        "WHERE o.source_id = ss.source_id AND o.section_key <> 'manuales') "
        "ON CONFLICT DO NOTHING"
    )
    op.execute("DELETE FROM source_sections WHERE section_key = 'manuales'")
    op.execute("DELETE FROM library_sections WHERE key = 'manuales'")
    op.execute(
        "UPDATE library_sections SET position = position - 1 "
        "WHERE position > (SELECT position FROM library_sections WHERE key = 'libros') + 1"
    )
    op.execute("UPDATE library_sections SET description = 'Libros y manuales' WHERE key = 'libros'")
