"""Branches (disciplines) of a source: Nutrición, Biomecánica, Fisiología... The areas
come from the Disciplinas tree, shared with concepts and courses. Filtering by a
branch includes its sub-areas, and the sources of courses classified in it."""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.models import AuditLog, Course, CourseMaterial, Discipline, Source, SourceDiscipline


class DisciplineError(Exception):
    pass


async def with_descendants(db: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    found, frontier = set(ids), list(ids)
    while frontier:
        children = (
            await db.scalars(select(Discipline.id).where(Discipline.parent_id.in_(frontier)))
        ).all()
        frontier = [c for c in children if c not in found]
        found.update(frontier)
    return found


async def discipline_sources(db: AsyncSession, discipline_id: uuid.UUID) -> list[uuid.UUID]:
    """Sources classified in the branch or a sub-area, or used in a course of it."""
    ids = await with_descendants(db, [discipline_id])
    tagged = select(SourceDiscipline.source_id).where(SourceDiscipline.discipline_id.in_(ids))
    via_course = (
        select(CourseMaterial.source_id)
        .join(Course, Course.id == CourseMaterial.course_id)
        .where(Course.discipline_id.in_(ids))
    )
    return list((await db.scalars(tagged.union(via_course))).all())


async def parse_ids(db: AsyncSession, raw: list[str] | None) -> list[uuid.UUID]:
    """Validate discipline ids (from a form or JSON); unknown ones are an error."""
    out: list[uuid.UUID] = []
    for v in raw or []:
        for part in str(v).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                did = uuid.UUID(part)
            except ValueError:
                raise DisciplineError(f"rama no válida: {part[:40]}") from None
            if await db.get(Discipline, did) is None:
                raise DisciplineError("rama no encontrada (¿se borró?)")
            if did not in out:
                out.append(did)
    return out


async def set_disciplines(
    db: AsyncSession, source: Source, ids: list[uuid.UUID], *, actor: str = "user"
) -> None:
    before = sorted(str(r.discipline_id) for r in source.discipline_rows)
    await db.execute(delete(SourceDiscipline).where(SourceDiscipline.source_id == source.id))
    db.add_all(SourceDiscipline(source_id=source.id, discipline_id=d) for d in ids)
    db.add(
        AuditLog(
            actor=actor,
            action="source.disciplines",
            subject_table="sources",
            subject_id=str(source.id),
            before={"disciplines": before},
            after={"disciplines": sorted(str(d) for d in ids)},
        )
    )
    await db.flush()
    await db.refresh(source, ["discipline_rows"])


def out(source: Source) -> list[dict]:
    return sorted(
        ({"id": str(r.discipline_id), "name": r.discipline.name} for r in source.discipline_rows),
        key=lambda x: x["name"],
    )
