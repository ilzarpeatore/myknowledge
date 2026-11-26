"""knowledge model

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26 05:42:45.648295
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "disciplines",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["disciplines.id"],
            name=op.f("fk_disciplines_parent_id_disciplines"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_disciplines")),
        sa.UniqueConstraint("slug", name=op.f("uq_disciplines_slug")),
    )
    op.create_table(
        "entity_types",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_entity_types")),
    )
    op.create_table(
        "persons",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=True),
        sa.Column("affiliation", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_persons")),
    )
    op.create_table(
        "publications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("doi", sa.String(length=200), nullable=True),
        sa.Column("journal", sa.Text(), nullable=True),
        sa.Column("study_type", sa.String(length=100), nullable=True),
        sa.Column("design", sa.Text(), nullable=True),
        sa.Column("population", sa.Text(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("variables", sa.Text(), nullable=True),
        sa.Column("results", sa.Text(), nullable=True),
        sa.Column("limitations", sa.Text(), nullable=True),
        sa.Column("methodological_quality", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publications")),
        sa.UniqueConstraint("doi", name=op.f("uq_publications_doi")),
    )
    op.create_table(
        "relationship_types",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("inverse_label", sa.String(length=100), nullable=False),
        sa.Column("is_symmetric", sa.Boolean(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_relationship_types")),
    )
    op.create_table(
        "review_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("subject_key", sa.String(length=200), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_tasks")),
    )
    op.create_index(
        "uq_review_tasks_open_subject",
        "review_tasks",
        ["kind", "subject_key"],
        unique=True,
        postgresql_where="status = 'open'",
    )
    op.create_table(
        "entities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("type_key", sa.String(length=50), nullable=False),
        sa.Column("canonical_name", sa.String(length=300), nullable=False),
        sa.Column("slug", sa.String(length=300), nullable=False),
        sa.Column("short_definition", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("merged_into_id", sa.UUID(), nullable=True),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["merged_into_id"],
            ["entities.id"],
            name=op.f("fk_entities_merged_into_id_entities"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["type_key"], ["entity_types.key"], name=op.f("fk_entities_type_key_entity_types")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entities")),
        sa.UniqueConstraint("slug", name=op.f("uq_entities_slug")),
    )
    op.create_table(
        "claims",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=30), nullable=False),
        sa.Column("attributed_person_id", sa.UUID(), nullable=True),
        sa.Column("publication_id", sa.UUID(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column("review_status", sa.String(length=20), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("ai_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "claim_type IN ('definition','reported_fact','hypothesis','interpretation',"
            "'opinion','recommendation','ai_inference','personal_conclusion')",
            name=op.f("ck_claims_claim_type"),
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_runs.id"],
            name=op.f("fk_claims_ai_run_id_ai_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["attributed_person_id"],
            ["persons.id"],
            name=op.f("fk_claims_attributed_person_id_persons"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"],
            ["publications.id"],
            name=op.f("fk_claims_publication_id_publications"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claims")),
    )
    op.create_table(
        "entity_disciplines",
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("discipline_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["discipline_id"],
            ["disciplines.id"],
            name=op.f("fk_entity_disciplines_discipline_id_disciplines"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_entity_disciplines_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("entity_id", "discipline_id", name=op.f("pk_entity_disciplines")),
    )
    op.create_table(
        "entity_merges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_entity_id", sa.UUID(), nullable=False),
        sa.Column("target_entity_id", sa.UUID(), nullable=False),
        sa.Column("moved", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_entity_id"],
            ["entities.id"],
            name=op.f("fk_entity_merges_source_entity_id_entities"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_entity_id"],
            ["entities.id"],
            name=op.f("fk_entity_merges_target_entity_id_entities"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity_merges")),
    )
    op.create_table(
        "relationships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_entity_id", sa.UUID(), nullable=False),
        sa.Column("target_entity_id", sa.UUID(), nullable=False),
        sa.Column("type_key", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("conditions", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("ai_run_id", sa.UUID(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_entity_id <> target_entity_id", name=op.f("ck_relationships_no_self_loop")
        ),
        sa.ForeignKeyConstraint(
            ["ai_run_id"],
            ["ai_runs.id"],
            name=op.f("fk_relationships_ai_run_id_ai_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_entity_id"],
            ["entities.id"],
            name=op.f("fk_relationships_source_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_entity_id"],
            ["entities.id"],
            name=op.f("fk_relationships_target_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["type_key"],
            ["relationship_types.key"],
            name=op.f("fk_relationships_type_key_relationship_types"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relationships")),
        sa.UniqueConstraint(
            "source_entity_id",
            "target_entity_id",
            "type_key",
            name=op.f("uq_relationships_source_entity_id"),
        ),
    )
    op.create_index(
        op.f("ix_relationships_source_entity_id"),
        "relationships",
        ["source_entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_relationships_target_entity_id"),
        "relationships",
        ["target_entity_id"],
        unique=False,
    )
    op.create_table(
        "claim_entities",
        sa.Column("claim_id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name=op.f("fk_claim_entities_claim_id_claims"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_claim_entities_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("claim_id", "entity_id", name=op.f("pk_claim_entities")),
    )
    op.create_table(
        "contradictions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("claim_a_id", sa.UUID(), nullable=False),
        sa.Column("claim_b_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("analysis_md", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claim_a_id"],
            ["claims.id"],
            name=op.f("fk_contradictions_claim_a_id_claims"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["claim_b_id"],
            ["claims.id"],
            name=op.f("fk_contradictions_claim_b_id_claims"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contradictions")),
        sa.UniqueConstraint("claim_a_id", "claim_b_id", name=op.f("uq_contradictions_claim_a_id")),
    )
    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("alias", sa.String(length=300), nullable=False),
        sa.Column("alias_normalized", sa.String(length=300), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("source_segment_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=op.f("fk_entity_aliases_entity_id_entities"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_segment_id"],
            ["source_segments.id"],
            name=op.f("fk_entity_aliases_source_segment_id_source_segments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity_aliases")),
        sa.UniqueConstraint(
            "entity_id", "alias_normalized", name=op.f("uq_entity_aliases_entity_id")
        ),
    )
    op.create_index(
        op.f("ix_entity_aliases_alias_normalized"),
        "entity_aliases",
        ["alias_normalized"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_aliases_entity_id"), "entity_aliases", ["entity_id"], unique=False
    )
    op.create_index(
        "ix_entity_aliases_trgm",
        "entity_aliases",
        ["alias_normalized"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"alias_normalized": "gin_trgm_ops"},
    )
    op.create_table(
        "evidence",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=False),
        sa.Column("segment_id", sa.UUID(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_char_start", sa.Integer(), nullable=False),
        sa.Column("quote_char_end", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name=op.f("fk_evidence_claim_id_claims"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["source_segments.id"],
            name=op.f("fk_evidence_segment_id_source_segments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
    )
    op.create_index(op.f("ix_evidence_claim_id"), "evidence", ["claim_id"], unique=False)
    op.create_index(op.f("ix_evidence_segment_id"), "evidence", ["segment_id"], unique=False)
    op.create_table(
        "relationship_evidence",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("relationship_id", sa.UUID(), nullable=False),
        sa.Column("claim_id", sa.UUID(), nullable=True),
        sa.Column("segment_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "claim_id IS NOT NULL OR segment_id IS NOT NULL",
            name=op.f("ck_relationship_evidence_has_claim_or_segment"),
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name=op.f("fk_relationship_evidence_claim_id_claims"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id"],
            ["relationships.id"],
            name=op.f("fk_relationship_evidence_relationship_id_relationships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["source_segments.id"],
            name=op.f("fk_relationship_evidence_segment_id_source_segments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relationship_evidence")),
    )

    et = sa.table(
        "entity_types",
        sa.column("key", sa.String),
        sa.column("label", sa.String),
        sa.column("description", sa.String),
        sa.column("is_builtin", sa.Boolean),
    )
    op.bulk_insert(
        et,
        [
            {"key": k, "label": label, "description": d, "is_builtin": True}
            for k, label, d in [
                ("concept", "Concepto", "Idea o noción (p. ej. proximidad al fallo)."),
                ("principle", "Principio", "Regla general del entrenamiento."),
                ("theory", "Teoría", "Explicación sistemática de un fenómeno."),
                ("model", "Modelo", "Representación simplificada (p. ej. modelo fitness-fatiga)."),
                ("mechanism", "Mecanismo", "Proceso fisiológico o biomecánico."),
                ("methodology", "Metodología", "Forma organizada de entrenar o evaluar."),
                (
                    "variable",
                    "Variable",
                    "Magnitud manipulable o medible (volumen, intensidad...).",
                ),
                ("author", "Autor", "Persona citada como fuente de conocimiento."),
                ("study", "Estudio", "Investigación concreta."),
                ("institution", "Institución", "Organización."),
                ("case", "Caso", "Caso práctico."),
                ("application", "Aplicación", "Uso práctico del conocimiento."),
                ("comparison", "Comparación", "Contraste entre conceptos o posturas."),
                ("synthesis", "Síntesis", "Visión integradora de un tema."),
                ("open_question", "Pregunta abierta", "Cuestión no resuelta."),
            ]
        ],
    )
    rt = sa.table(
        "relationship_types",
        sa.column("key", sa.String),
        sa.column("label", sa.String),
        sa.column("inverse_label", sa.String),
        sa.column("is_symmetric", sa.Boolean),
        sa.column("is_builtin", sa.Boolean),
    )
    op.bulk_insert(
        rt,
        [
            {"key": k, "label": label, "inverse_label": i, "is_symmetric": sym, "is_builtin": True}
            for k, label, i, sym in [
                ("is_a", "es un tipo de", "incluye el tipo", False),
                ("part_of", "forma parte de", "se compone de", False),
                ("causes", "causa", "es causado por", False),
                ("contributes_to", "contribuye a", "recibe contribución de", False),
                ("influences", "influye en", "está influido por", False),
                ("requires", "requiere", "es requerido por", False),
                ("contradicts", "contradice a", "es contradicho por", True),
                ("supports", "apoya", "es apoyado por", False),
                ("explains", "explica", "es explicado por", False),
                ("associated_with", "se asocia con", "se asocia con", True),
                ("measured_by", "se mide con", "mide", False),
                ("applied_in", "se aplica en", "aplica", False),
                ("derived_from", "deriva de", "da origen a", False),
                ("compared_with", "se compara con", "se compara con", True),
            ]
        ],
    )


def downgrade() -> None:
    op.drop_table("relationship_evidence")
    op.drop_index(op.f("ix_evidence_segment_id"), table_name="evidence")
    op.drop_index(op.f("ix_evidence_claim_id"), table_name="evidence")
    op.drop_table("evidence")
    op.drop_index(
        "ix_entity_aliases_trgm",
        table_name="entity_aliases",
        postgresql_using="gin",
        postgresql_ops={"alias_normalized": "gin_trgm_ops"},
    )
    op.drop_index(op.f("ix_entity_aliases_entity_id"), table_name="entity_aliases")
    op.drop_index(op.f("ix_entity_aliases_alias_normalized"), table_name="entity_aliases")
    op.drop_table("entity_aliases")
    op.drop_table("contradictions")
    op.drop_table("claim_entities")
    op.drop_index(op.f("ix_relationships_target_entity_id"), table_name="relationships")
    op.drop_index(op.f("ix_relationships_source_entity_id"), table_name="relationships")
    op.drop_table("relationships")
    op.drop_table("entity_merges")
    op.drop_table("entity_disciplines")
    op.drop_table("claims")
    op.drop_table("entities")
    op.drop_index(
        "uq_review_tasks_open_subject",
        table_name="review_tasks",
        postgresql_where="status = 'open'",
    )
    op.drop_table("review_tasks")
    op.drop_table("relationship_types")
    op.drop_table("publications")
    op.drop_table("persons")
    op.drop_table("entity_types")
    op.drop_table("disciplines")
