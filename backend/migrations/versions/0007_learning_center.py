"""learning center

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-26 05:51:41.757340
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "programs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("provider", sa.String(length=300), nullable=True),
        sa.Column("teacher_person_id", sa.UUID(), nullable=True),
        sa.Column("discipline_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("goals", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','in_progress','paused','completed','archived')",
            name=op.f("ck_programs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["discipline_id"],
            ["disciplines.id"],
            name=op.f("fk_programs_discipline_id_disciplines"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_person_id"],
            ["persons.id"],
            name=op.f("fk_programs_teacher_person_id_persons"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_programs")),
    )
    op.create_table(
        "courses",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("program_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("teacher_person_id", sa.UUID(), nullable=True),
        sa.Column("discipline_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("goals", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','in_progress','paused','completed','archived')",
            name=op.f("ck_courses_status"),
        ),
        sa.ForeignKeyConstraint(
            ["discipline_id"],
            ["disciplines.id"],
            name=op.f("fk_courses_discipline_id_disciplines"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["programs.id"],
            name=op.f("fk_courses_program_id_programs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_person_id"],
            ["persons.id"],
            name=op.f("fk_courses_teacher_person_id_persons"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_courses")),
    )
    op.create_index(op.f("ix_courses_program_id"), "courses", ["program_id"], unique=False)
    op.create_table(
        "course_modules",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("course_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_course_modules_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_modules")),
    )
    op.create_index(
        op.f("ix_course_modules_course_id"), "course_modules", ["course_id"], unique=False
    )
    op.create_table(
        "lessons",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("module_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["module_id"],
            ["course_modules.id"],
            name=op.f("fk_lessons_module_id_course_modules"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lessons")),
    )
    op.create_index(op.f("ix_lessons_module_id"), "lessons", ["module_id"], unique=False)
    op.create_table(
        "course_materials",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("course_id", sa.UUID(), nullable=False),
        sa.Column("lesson_id", sa.UUID(), nullable=True),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name=op.f("fk_course_materials_course_id_courses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id"],
            ["lessons.id"],
            name=op.f("fk_course_materials_lesson_id_lessons"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_course_materials_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_course_materials")),
        sa.UniqueConstraint(
            "course_id",
            "lesson_id",
            "source_id",
            name=op.f("uq_course_materials_course_id"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_course_materials_course_id"), "course_materials", ["course_id"], unique=False
    )
    op.create_index(
        op.f("ix_course_materials_lesson_id"), "course_materials", ["lesson_id"], unique=False
    )
    op.create_index(
        op.f("ix_course_materials_source_id"), "course_materials", ["source_id"], unique=False
    )
    op.create_table(
        "lesson_progress",
        sa.Column("lesson_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("time_spent_minutes", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_access_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('not_started','in_progress','completed')",
            name=op.f("ck_lesson_progress_status"),
        ),
        sa.ForeignKeyConstraint(
            ["lesson_id"],
            ["lessons.id"],
            name=op.f("fk_lesson_progress_lesson_id_lessons"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("lesson_id", name=op.f("pk_lesson_progress")),
    )


def downgrade() -> None:
    op.drop_table("lesson_progress")
    op.drop_index(op.f("ix_course_materials_source_id"), table_name="course_materials")
    op.drop_index(op.f("ix_course_materials_lesson_id"), table_name="course_materials")
    op.drop_index(op.f("ix_course_materials_course_id"), table_name="course_materials")
    op.drop_table("course_materials")
    op.drop_index(op.f("ix_lessons_module_id"), table_name="lessons")
    op.drop_table("lessons")
    op.drop_index(op.f("ix_course_modules_course_id"), table_name="course_modules")
    op.drop_table("course_modules")
    op.drop_index(op.f("ix_courses_program_id"), table_name="courses")
    op.drop_table("courses")
    op.drop_table("programs")
