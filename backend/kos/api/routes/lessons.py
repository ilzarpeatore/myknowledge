"""Course structure from its materials, and studying a lesson: its reading, the
comprehension checks and «explícalo con tus palabras»."""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from kos.api.deps import DbDep, UserDep
from kos.models import (
    AuditLog,
    CourseModule,
    Lesson,
    LessonExplanation,
    LessonProgress,
    LessonReading,
    Source,
    SourceVersion,
)
from kos.study import lesson_ai as la
from kos.study import lessons as ls

router = APIRouter(prefix="/api", tags=["lessons"])


class StructureIn(BaseModel):
    replace: bool = False


@router.get("/courses/{course_id}/structure/preview")
async def preview(course_id: uuid.UUID, db: DbDep, _: UserDep):
    try:
        modules = await ls.plan(db, course_id)
    except ls.LessonError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return {
        "modules": [
            {
                "title": m["title"],
                "lessons": [
                    {
                        "title": x["title"],
                        "pages": [x["part"].get("page_start"), x["part"].get("page_end")],
                    }
                    for x in m["lessons"]
                ],
            }
            for m in modules
        ]
    }


@router.post("/courses/{course_id}/structure", status_code=status.HTTP_201_CREATED)
async def build(course_id: uuid.UUID, body: StructureIn, db: DbDep, _: UserDep):
    try:
        return await ls.build(db, course_id, replace=body.replace)
    except ls.LessonError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


async def _lesson(db, lesson_id: uuid.UUID) -> Lesson:
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lección no encontrada")
    return lesson


def _reading_out(r: LessonReading) -> dict:
    return {
        "id": str(r.id),
        "content": r.content,
        "markdown": r.markdown,
        "words": r.words,
        "citations": r.citations,
        "created_at": r.created_at,
    }


@router.get("/lessons/{lesson_id}/study")
async def study(lesson_id: uuid.UUID, db: DbDep, _: UserDep):
    lesson = await _lesson(db, lesson_id)
    course, module = await la.lesson_context(db, lesson)
    siblings = (
        await db.execute(
            select(Lesson.id, Lesson.title)
            .join(CourseModule, CourseModule.id == Lesson.module_id)
            .where(CourseModule.course_id == course.id)
            .order_by(CourseModule.position, Lesson.position)
        )
    ).all()
    idx = next(i for i, (lid, _t) in enumerate(siblings) if lid == lesson.id)
    parts = []
    for p in await ls.parts_of(db, lesson.id):
        v = await db.get(SourceVersion, p.source_version_id)
        s = await db.get(Source, v.source_id)
        parts.append(
            {
                "kind": p.kind,
                "source": {"id": str(s.id), "title": s.title},
                "version": {"id": str(v.id), "mime": v.mime},
                "chapter_id": str(p.chapter_id) if p.chapter_id else None,
                "page_start": p.page_start,
                "page_end": p.page_end,
            }
        )
    reading = await la.current_reading(db, lesson.id)
    progress = await db.get(LessonProgress, lesson.id)
    explanations = (
        await db.scalars(
            select(LessonExplanation)
            .where(LessonExplanation.lesson_id == lesson.id)
            .order_by(LessonExplanation.created_at.desc())
            .limit(10)
        )
    ).all()

    def brief(i: int) -> dict | None:
        if 0 <= i < len(siblings):
            return {"id": str(siblings[i][0]), "title": siblings[i][1]}
        return None

    return {
        "id": str(lesson.id),
        "title": lesson.title,
        "course": {"id": str(course.id), "title": course.title},
        "module": {"id": str(module.id), "title": module.title},
        "parts": parts,
        "prev": brief(idx - 1),
        "next": brief(idx + 1),
        "reading": _reading_out(reading) if reading else None,
        "estimate": await la.estimate(db, lesson),
        "generating": await la.in_flight(db, lesson.id),
        "failed": await la.last_failure(db, lesson.id),
        "progress": {
            "status": progress.status if progress else "not_started",
            "understanding": progress.understanding if progress else None,
        },
        "explanations": [
            {
                "id": str(e.id),
                "text": e.text,
                "status": e.status,
                "score": e.score,
                "feedback": e.feedback,
                "created_at": e.created_at,
            }
            for e in explanations
        ],
    }


@router.post("/lessons/{lesson_id}/reading", status_code=status.HTTP_202_ACCEPTED)
async def generate_reading(lesson_id: uuid.UUID, db: DbDep, _: UserDep):
    lesson = await _lesson(db, lesson_id)
    if await la.in_flight(db, lesson.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "ya se está preparando la lectura")
    est = await la.estimate(db, lesson)
    if est["tokens"] == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "la lección no tiene material con texto")
    if est["too_long"]:
        raise HTTPException(status.HTTP_409_CONFLICT, "la lección es demasiado larga: divídela")
    job = la.reading_job(lesson.id)
    db.add(job)
    db.add(
        AuditLog(
            actor="user",
            action="lesson.reading_requested",
            subject_table="lessons",
            subject_id=str(lesson.id),
            after={"usd_estimate": est["usd"]},
        )
    )
    await db.commit()
    return {"job_id": str(job.id), "estimate": est}


class ExplanationIn(BaseModel):
    text: str = Field(min_length=30, max_length=8000)


@router.post("/lessons/{lesson_id}/explanations", status_code=status.HTTP_202_ACCEPTED)
async def explain(lesson_id: uuid.UUID, body: ExplanationIn, db: DbDep, _: UserDep):
    lesson = await _lesson(db, lesson_id)
    ex = LessonExplanation(
        id=uuid.uuid4(), lesson_id=lesson.id, text=body.text.strip(), status="pending"
    )
    db.add(ex)
    db.add(la.feedback_job(ex.id))
    await db.commit()
    return {"id": str(ex.id), "status": ex.status}
