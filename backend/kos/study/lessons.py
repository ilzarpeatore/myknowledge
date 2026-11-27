"""A course built from its materials: modules and lessons that cover stretches of the
material (the chapters of its documents, later the time ranges of its videos).

- One document: its chapters are the modules and their points the lessons (a chapter
  without points is one lesson).
- Several documents: one module per document, one lesson per chapter.
- A document without chapters: one lesson for the whole of it.
Pages that are not content (cover, index, bibliography) never become lessons.
"""

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.library import chapters as chs
from kos.models import (
    AuditLog,
    Course,
    CourseMaterial,
    CourseModule,
    Lesson,
    LessonPart,
    Source,
    SourceChapter,
    SourceSegment,
    SourceVersion,
)

MAX_LESSON_CHARS = 150_000


class LessonError(Exception):
    pass


async def _documents(db: AsyncSession, course_id: uuid.UUID) -> list[tuple[Source, SourceVersion]]:
    """The course's document materials (latest version each), by title."""
    out = []
    rows = (
        await db.execute(
            select(Source)
            .join(CourseMaterial, CourseMaterial.source_id == Source.id)
            .where(CourseMaterial.course_id == course_id, Source.status == "ready")
            .distinct()
        )
    ).scalars()
    for s in sorted(rows, key=lambda x: x.title.lower()):
        v = await db.scalar(
            select(SourceVersion)
            .where(SourceVersion.source_id == s.id)
            .order_by(SourceVersion.created_at.desc())
            .limit(1)
        )
        if v is not None and not v.mime.startswith(("audio/", "video/", "text/vtt")):
            out.append((s, v))
    return out


def _part(v: SourceVersion, ch: SourceChapter | None) -> dict[str, Any]:
    """A chapter's pages, or (no chapter) the whole document."""
    if ch is None:
        return {"source_version_id": v.id, "kind": "document"}
    return {
        "source_version_id": v.id,
        "kind": "slides",
        "chapter_id": ch.id,
        "page_start": ch.page_start,
        "page_end": ch.page_end,
    }


async def plan(db: AsyncSession, course_id: uuid.UUID) -> list[dict[str, Any]]:
    """[{title, lessons: [{title, part}]}] without saving anything."""
    docs = await _documents(db, course_id)
    if not docs:
        raise LessonError("el curso no tiene materiales con texto: añade sus documentos")
    modules: list[dict[str, Any]] = []
    for s, v in docs:
        rows = [r for r in await chs.chapters_of(db, v.id) if r.is_content]
        tops = [r for r in rows if r.parent_id is None]
        if len(docs) == 1 and tops:
            for t in tops:
                modules.append({"title": t.title, "lessons": _chapter_lessons(v, t, rows)})
            continue
        lessons = [{"title": t.title, "part": _part(v, t)} for t in tops]
        if not lessons:
            lessons = [{"title": s.title, "part": _part(v, None)}]
        modules.append({"title": s.title, "lessons": lessons})
    return modules


def _chapter_lessons(
    v: SourceVersion, t: SourceChapter, rows: list[SourceChapter]
) -> list[dict[str, Any]]:
    """A chapter's points as lessons (with the pages before the first point as an
    introduction), or the chapter itself when it has none."""
    points = [r for r in rows if r.parent_id == t.id]
    if not points:
        return [{"title": t.title, "part": _part(v, t)}]
    lessons = [{"title": p.title, "part": _part(v, p)} for p in points]
    if points[0].page_start > t.page_start:
        intro = {**_part(v, t), "page_end": points[0].page_start - 1}
        lessons.insert(0, {"title": f"{t.title}: introducción", "part": intro})
    return lessons


async def build(db: AsyncSession, course_id: uuid.UUID, replace: bool = False) -> dict[str, int]:
    course = await db.get(Course, course_id)
    if course is None:
        raise LessonError("curso no encontrado")
    existing = (
        await db.scalars(select(CourseModule.id).where(CourseModule.course_id == course_id))
    ).all()
    if existing and not replace:
        raise LessonError("el curso ya tiene módulos: confirma que quieres sustituirlos")
    modules = await plan(db, course_id)
    if existing:
        await db.execute(delete(CourseModule).where(CourseModule.course_id == course_id))
    n_lessons = 0
    for mpos, m in enumerate(modules):
        mod = CourseModule(
            id=uuid.uuid4(), course_id=course_id, position=mpos, title=m["title"][:300]
        )
        db.add(mod)
        for lpos, les in enumerate(m["lessons"]):
            p = les["part"]
            lesson = Lesson(
                id=uuid.uuid4(),
                module_id=mod.id,
                position=lpos,
                title=les["title"][:300],
                description=f"p. {p['page_start']}–{p['page_end']}" if "page_start" in p else None,
            )
            db.add(lesson)
            db.add(LessonPart(lesson_id=lesson.id, position=0, **p))
            n_lessons += 1
    db.add(
        AuditLog(
            actor="user",
            action="course.structure_built",
            subject_table="courses",
            subject_id=str(course_id),
            after={"modules": len(modules), "lessons": n_lessons, "replaced": len(existing)},
        )
    )
    await db.commit()
    return {"modules": len(modules), "lessons": n_lessons}


async def parts_of(db: AsyncSession, lesson_id: uuid.UUID) -> list[LessonPart]:
    return list(
        (
            await db.scalars(
                select(LessonPart)
                .where(LessonPart.lesson_id == lesson_id)
                .order_by(LessonPart.position)
            )
        ).all()
    )


async def segments_of(db: AsyncSession, parts: list[LessonPart]) -> list[SourceSegment]:
    out: list[SourceSegment] = []
    for p in parts:
        stmt = select(SourceSegment).where(SourceSegment.source_version_id == p.source_version_id)
        if p.page_start is not None:  # otherwise: the whole document
            stmt = stmt.where(
                SourceSegment.loc_page_start >= p.page_start,
                SourceSegment.loc_page_start <= (p.page_end or p.page_start),
            )
        out += (await db.scalars(stmt.order_by(SourceSegment.ordinal))).all()
    return out
