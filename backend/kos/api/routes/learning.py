"""Learning Center API: disciplines, programs, courses, modules, lessons, materials,
progress, and the course <-> concept links derived from evidence."""

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import Select, distinct, func, select
from sqlalchemy.exc import IntegrityError

from kos.api.deps import DbDep, UserDep
from kos.knowledge.service import slugify
from kos.models import (
    AuditLog,
    ClaimEntity,
    Course,
    CourseMaterial,
    CourseModule,
    Discipline,
    Entity,
    Evidence,
    Lesson,
    LessonProgress,
    Person,
    Program,
    Source,
    SourceSegment,
    SourceVersion,
)

router = APIRouter(prefix="/api", tags=["learning"])
Status = Literal["pending", "in_progress", "paused", "completed", "archived"]
LessonStatus = Literal["not_started", "in_progress", "completed"]
MaterialRole = Literal["material", "slides", "transcript", "notes", "exercise", "reading"]

# Initial structure from the master document (§19.1); created only on request.
TRAINING_SCIENCE = (
    "Ciencias del entrenamiento",
    [
        "Anatomía",
        "Fisiología",
        "Biomecánica",
        "Neurofisiología",
        "Hipertrofia",
        "Fuerza",
        "Resistencia",
        "Programación",
        "Nutrición",
        "Recuperación",
        "Evaluación",
    ],
)


async def _audit(db, action: str, table: str, subject: Any, after: dict | None = None):
    db.add(
        AuditLog(
            actor="user", action=action, subject_table=table, subject_id=str(subject), after=after
        )
    )


def _404(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} no encontrado")


# ---- disciplines ----------------------------------------------------------------------


class DisciplineIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=5000)


@router.get("/disciplines")
async def list_disciplines(db: DbDep, _: UserDep):
    rows = await db.scalars(select(Discipline).order_by(Discipline.name))
    return [
        {
            "id": str(d.id),
            "name": d.name,
            "slug": d.slug,
            "parent_id": str(d.parent_id) if d.parent_id else None,
        }
        for d in rows
    ]


async def _create_discipline(db, name: str, parent_id: uuid.UUID | None) -> Discipline:
    base = slugify(name)
    slug, n = base, 2
    while await db.scalar(select(Discipline.id).where(Discipline.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    d = Discipline(name=name.strip(), parent_id=parent_id, slug=slug)
    db.add(d)
    await db.flush()
    return d


@router.post("/disciplines", status_code=status.HTTP_201_CREATED)
async def create_discipline(body: DisciplineIn, db: DbDep, _: UserDep):
    if body.parent_id and await db.get(Discipline, body.parent_id) is None:
        raise _404("área padre")
    d = await _create_discipline(db, body.name, body.parent_id)
    d.description = body.description
    await _audit(db, "discipline.created", "disciplines", d.id, {"name": d.name})
    await db.commit()
    return {"id": str(d.id), "slug": d.slug}


@router.post("/disciplines/seed-training-science")
async def seed_training_science(db: DbDep, _: UserDep):
    """Create 'Ciencias del entrenamiento' and its areas if they do not exist yet."""
    root_name, areas = TRAINING_SCIENCE
    root = await db.scalar(
        select(Discipline).where(Discipline.name == root_name, Discipline.parent_id.is_(None))
    )
    created = 0
    if root is None:
        root = await _create_discipline(db, root_name, None)
        created += 1
    existing = set(
        (await db.scalars(select(Discipline.name).where(Discipline.parent_id == root.id))).all()
    )
    for area in areas:
        if area not in existing:
            await _create_discipline(db, area, root.id)
            created += 1
    await _audit(db, "discipline.seeded", "disciplines", root.id, {"created": created})
    await db.commit()
    return {"root_id": str(root.id), "created": created}


# ---- programs -------------------------------------------------------------------------


class ProgramIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    provider: str | None = Field(default=None, max_length=300)
    teacher_person_id: uuid.UUID | None = None
    discipline_id: uuid.UUID | None = None
    status: Status = "pending"
    start_date: date | None = None
    end_date: date | None = None
    url: HttpUrl | None = None
    goals: str | None = Field(default=None, max_length=10000)
    notes: str | None = Field(default=None, max_length=20000)


class ProgramPatch(ProgramIn):
    name: str | None = Field(default=None, min_length=1, max_length=300)  # type: ignore[assignment]
    status: Status | None = None  # type: ignore[assignment]


def _clean(body: BaseModel, partial: bool) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=partial)
    if data.get("url") is not None:
        data["url"] = str(data["url"])
    return data


def _lesson_counts(course_ids: Select | list) -> Select:
    """(course_id, total lessons, completed lessons, minutes spent) per course."""
    return (
        select(
            CourseModule.course_id,
            func.count(Lesson.id),
            func.count(Lesson.id).filter(LessonProgress.status == "completed"),
            func.coalesce(func.sum(LessonProgress.time_spent_minutes), 0),
        )
        .join(Lesson, Lesson.module_id == CourseModule.id)
        .outerjoin(LessonProgress, LessonProgress.lesson_id == Lesson.id)
        .where(CourseModule.course_id.in_(course_ids))
        .group_by(CourseModule.course_id)
    )


async def _progress(db, course_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    out = {cid: {"lessons": 0, "completed": 0, "minutes": 0, "percent": 0} for cid in course_ids}
    if course_ids:
        for cid, total, done, minutes in (await db.execute(_lesson_counts(course_ids))).all():
            out[cid] = {
                "lessons": total,
                "completed": done,
                "minutes": int(minutes),
                "percent": round(100 * done / total) if total else 0,
            }
    return out


def _program_dict(p: Program) -> dict[str, Any]:
    return {
        k: (str(v) if isinstance(v, uuid.UUID) else v)
        for k, v in ((c.key, getattr(p, c.key)) for c in Program.__table__.columns)
    }


@router.get("/programs")
async def list_programs(db: DbDep, _: UserDep, include_archived: bool = False):
    stmt = select(Program).order_by(Program.created_at)
    if not include_archived:
        stmt = stmt.where(Program.status != "archived")
    programs = (await db.scalars(stmt)).all()
    courses = (
        await db.scalars(select(Course).where(Course.program_id.in_([p.id for p in programs])))
    ).all()
    prog = await _progress(db, [c.id for c in courses])
    out = []
    for p in programs:
        mine = [c for c in courses if c.program_id == p.id]
        lessons = sum(prog[c.id]["lessons"] for c in mine)
        done = sum(prog[c.id]["completed"] for c in mine)
        out.append(
            {
                **_program_dict(p),
                "courses": len(mine),
                "lessons": lessons,
                "completed": done,
                "percent": round(100 * done / lessons) if lessons else 0,
            }
        )
    return out


@router.post("/programs", status_code=status.HTTP_201_CREATED)
async def create_program(body: ProgramIn, db: DbDep, _: UserDep):
    p = Program(**_clean(body, False))
    db.add(p)
    await db.flush()
    await _audit(db, "program.created", "programs", p.id, {"name": p.name})
    await db.commit()
    return _program_dict(p)


@router.patch("/programs/{program_id}")
async def patch_program(program_id: uuid.UUID, body: ProgramPatch, db: DbDep, _: UserDep):
    p = await db.get(Program, program_id)
    if p is None:
        raise _404("formación")
    changes = _clean(body, True)
    for k, v in changes.items():
        setattr(p, k, v)
    p.updated_at = datetime.now(UTC)
    await _audit(db, "program.updated", "programs", p.id, {k: str(v) for k, v in changes.items()})
    await db.commit()
    return _program_dict(p)


# ---- courses --------------------------------------------------------------------------


class CourseIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    program_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=10000)
    teacher_person_id: uuid.UUID | None = None
    discipline_id: uuid.UUID | None = None
    status: Status = "pending"
    position: int = 0
    url: HttpUrl | None = None
    goals: str | None = Field(default=None, max_length=10000)
    notes: str | None = Field(default=None, max_length=20000)


class CoursePatch(CourseIn):
    title: str | None = Field(default=None, min_length=1, max_length=300)  # type: ignore[assignment]
    status: Status | None = None  # type: ignore[assignment]
    position: int | None = None  # type: ignore[assignment]


async def _course_out(db, c: Course, progress: dict[str, Any]) -> dict[str, Any]:
    teacher = await db.get(Person, c.teacher_person_id) if c.teacher_person_id else None
    program = await db.get(Program, c.program_id) if c.program_id else None
    return {
        **{
            k: (str(v) if isinstance(v, uuid.UUID) else v)
            for k, v in ((col.key, getattr(c, col.key)) for col in Course.__table__.columns)
        },
        "teacher": teacher.name if teacher else None,
        "program": program.name if program else None,
        "progress": progress,
    }


@router.get("/courses")
async def list_courses(
    db: DbDep,
    _: UserDep,
    program_id: uuid.UUID | None = None,
    status_filter: Status | None = None,
):
    stmt = select(Course).order_by(Course.position, Course.created_at)
    if program_id:
        stmt = stmt.where(Course.program_id == program_id)
    if status_filter:
        stmt = stmt.where(Course.status == status_filter)
    courses = (await db.scalars(stmt)).all()
    prog = await _progress(db, [c.id for c in courses])
    return [await _course_out(db, c, prog[c.id]) for c in courses]


@router.post("/courses", status_code=status.HTTP_201_CREATED)
async def create_course(body: CourseIn, db: DbDep, _: UserDep):
    if body.program_id and await db.get(Program, body.program_id) is None:
        raise _404("formación")
    c = Course(**_clean(body, False))
    db.add(c)
    await db.flush()
    await _audit(db, "course.created", "courses", c.id, {"title": c.title})
    await db.commit()
    return await _course_out(db, c, (await _progress(db, [c.id]))[c.id])


@router.patch("/courses/{course_id}")
async def patch_course(course_id: uuid.UUID, body: CoursePatch, db: DbDep, _: UserDep):
    c = await db.get(Course, course_id)
    if c is None:
        raise _404("curso")
    changes = _clean(body, True)
    for k, v in changes.items():
        setattr(c, k, v)
    c.updated_at = datetime.now(UTC)
    await _audit(db, "course.updated", "courses", c.id, {k: str(v) for k, v in changes.items()})
    await db.commit()
    return await _course_out(db, c, (await _progress(db, [c.id]))[c.id])


@router.get("/courses/{course_id}")
async def get_course(course_id: uuid.UUID, db: DbDep, _: UserDep):
    c = await db.get(Course, course_id)
    if c is None:
        raise _404("curso")
    modules = (
        await db.scalars(
            select(CourseModule)
            .where(CourseModule.course_id == c.id)
            .order_by(CourseModule.position, CourseModule.title)
        )
    ).all()
    lessons = (
        await db.execute(
            select(Lesson, LessonProgress)
            .outerjoin(LessonProgress, LessonProgress.lesson_id == Lesson.id)
            .where(Lesson.module_id.in_([m.id for m in modules]))
            .order_by(Lesson.position, Lesson.title)
        )
    ).all()
    materials = (
        await db.execute(
            select(CourseMaterial, Source)
            .join(Source, Source.id == CourseMaterial.source_id)
            .where(CourseMaterial.course_id == c.id)
            .order_by(Source.title)
        )
    ).all()

    def mat(m: CourseMaterial, s: Source) -> dict[str, Any]:
        return {
            "id": str(m.id),
            "source_id": str(s.id),
            "title": s.title,
            "role": m.role,
            "status": s.status,
            "lesson_id": str(m.lesson_id) if m.lesson_id else None,
        }

    return {
        **await _course_out(db, c, (await _progress(db, [c.id]))[c.id]),
        "modules": [
            {
                "id": str(m.id),
                "title": m.title,
                "description": m.description,
                "position": m.position,
                "lessons": [
                    {
                        "id": str(lesson.id),
                        "title": lesson.title,
                        "description": lesson.description,
                        "position": lesson.position,
                        "duration_minutes": lesson.duration_minutes,
                        "url": lesson.url,
                        "progress": {
                            "status": p.status if p else "not_started",
                            "time_spent_minutes": p.time_spent_minutes if p else 0,
                            "notes": p.notes if p else None,
                            "completed_at": p.completed_at if p else None,
                        },
                        "materials": [
                            mat(cm, s) for cm, s in materials if cm.lesson_id == lesson.id
                        ],
                    }
                    for lesson, p in lessons
                    if lesson.module_id == m.id
                ],
            }
            for m in modules
        ],
        "materials": [mat(cm, s) for cm, s in materials if cm.lesson_id is None],
    }


# ---- modules and lessons --------------------------------------------------------------


class ModuleIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    position: int | None = None


async def _next_position(db, col, parent_col, parent_id) -> int:
    return (
        await db.scalar(select(func.coalesce(func.max(col), -1)).where(parent_col == parent_id))
    ) + 1


@router.post("/courses/{course_id}/modules", status_code=status.HTTP_201_CREATED)
async def create_module(course_id: uuid.UUID, body: ModuleIn, db: DbDep, _: UserDep):
    if await db.get(Course, course_id) is None:
        raise _404("curso")
    pos = (
        body.position
        if body.position is not None
        else await _next_position(db, CourseModule.position, CourseModule.course_id, course_id)
    )
    m = CourseModule(
        course_id=course_id, title=body.title, description=body.description, position=pos
    )
    db.add(m)
    await db.commit()
    return {"id": str(m.id), "position": m.position}


@router.patch("/modules/{module_id}")
async def patch_module(module_id: uuid.UUID, body: ModuleIn, db: DbDep, _: UserDep):
    m = await db.get(CourseModule, module_id)
    if m is None:
        raise _404("módulo")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    await db.commit()
    return {"id": str(m.id)}


@router.delete("/modules/{module_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_module(module_id: uuid.UUID, db: DbDep, _: UserDep):
    m = await db.get(CourseModule, module_id)
    if m is None:
        raise _404("módulo")
    await _audit(db, "module.deleted", "course_modules", m.id, {"title": m.title})
    await db.delete(m)
    await db.commit()


class LessonIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    duration_minutes: int | None = Field(default=None, ge=0, le=100000)
    url: HttpUrl | None = None
    position: int | None = None


@router.post("/modules/{module_id}/lessons", status_code=status.HTTP_201_CREATED)
async def create_lesson(module_id: uuid.UUID, body: LessonIn, db: DbDep, _: UserDep):
    if await db.get(CourseModule, module_id) is None:
        raise _404("módulo")
    data = _clean(body, False)
    if data["position"] is None:
        data["position"] = await _next_position(db, Lesson.position, Lesson.module_id, module_id)
    lesson = Lesson(module_id=module_id, **data)
    db.add(lesson)
    await db.commit()
    return {"id": str(lesson.id), "position": lesson.position}


@router.patch("/lessons/{lesson_id}")
async def patch_lesson(lesson_id: uuid.UUID, body: LessonIn, db: DbDep, _: UserDep):
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise _404("lección")
    for k, v in _clean(body, True).items():
        setattr(lesson, k, v)
    await db.commit()
    return {"id": str(lesson.id)}


@router.delete("/lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lesson(lesson_id: uuid.UUID, db: DbDep, _: UserDep):
    lesson = await db.get(Lesson, lesson_id)
    if lesson is None:
        raise _404("lección")
    await _audit(db, "lesson.deleted", "lessons", lesson.id, {"title": lesson.title})
    await db.delete(lesson)
    await db.commit()


class ProgressIn(BaseModel):
    status: LessonStatus | None = None
    time_spent_minutes: int | None = Field(default=None, ge=0, le=100000)
    add_minutes: int | None = Field(default=None, ge=0, le=1440)
    notes: str | None = Field(default=None, max_length=50000)


@router.put("/lessons/{lesson_id}/progress")
async def set_progress(lesson_id: uuid.UUID, body: ProgressIn, db: DbDep, _: UserDep):
    if await db.get(Lesson, lesson_id) is None:
        raise _404("lección")
    p = await db.get(LessonProgress, lesson_id) or LessonProgress(
        lesson_id=lesson_id, status="not_started", time_spent_minutes=0
    )
    db.add(p)
    now = datetime.now(UTC)
    if body.status:
        p.status = body.status
        p.completed_at = now if body.status == "completed" else None
    if body.time_spent_minutes is not None:
        p.time_spent_minutes = body.time_spent_minutes
    if body.add_minutes:
        p.time_spent_minutes += body.add_minutes
    if "notes" in body.model_fields_set:
        p.notes = body.notes
    p.last_access_at = now
    await db.commit()
    return {
        "lesson_id": str(lesson_id),
        "status": p.status,
        "time_spent_minutes": p.time_spent_minutes,
    }


# ---- materials ------------------------------------------------------------------------


class MaterialIn(BaseModel):
    source_id: uuid.UUID
    lesson_id: uuid.UUID | None = None
    role: MaterialRole = "material"


@router.post("/courses/{course_id}/materials", status_code=status.HTTP_201_CREATED)
async def add_material(course_id: uuid.UUID, body: MaterialIn, db: DbDep, _: UserDep):
    if await db.get(Course, course_id) is None:
        raise _404("curso")
    if await db.get(Source, body.source_id) is None:
        raise _404("fuente")
    if body.lesson_id:
        lesson_course = await db.scalar(
            select(CourseModule.course_id)
            .join(Lesson, Lesson.module_id == CourseModule.id)
            .where(Lesson.id == body.lesson_id)
        )
        if lesson_course != course_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "la lección no pertenece a este curso")
    m = CourseMaterial(course_id=course_id, **body.model_dump())
    db.add(m)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "ese material ya está asociado") from None
    return {"id": str(m.id)}


@router.delete("/materials/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_material(material_id: uuid.UUID, db: DbDep, _: UserDep):
    m = await db.get(CourseMaterial, material_id)
    if m is None:
        raise _404("material")
    await db.delete(m)
    await db.commit()


# ---- course <-> concept links (derived from evidence) ---------------------------------


def _concepts_query():
    """Entities whose claims cite segments of a course's materials."""
    return (
        select(
            CourseMaterial.course_id,
            CourseMaterial.lesson_id,
            Entity.id,
            Entity.slug,
            Entity.canonical_name,
            func.count(distinct(Evidence.claim_id)).label("claims"),
        )
        .join(SourceVersion, SourceVersion.source_id == CourseMaterial.source_id)
        .join(SourceSegment, SourceSegment.source_version_id == SourceVersion.id)
        .join(Evidence, Evidence.segment_id == SourceSegment.id)
        .join(ClaimEntity, ClaimEntity.claim_id == Evidence.claim_id)
        .join(Entity, Entity.id == ClaimEntity.entity_id)
        .where(Entity.status.in_(("active", "proposed")))
        .group_by(CourseMaterial.course_id, CourseMaterial.lesson_id, Entity.id)
    )


@router.get("/courses/{course_id}/concepts")
async def course_concepts(course_id: uuid.UUID, db: DbDep, _: UserDep):
    rows = (await db.execute(_concepts_query().where(CourseMaterial.course_id == course_id))).all()
    merged: dict[uuid.UUID, dict[str, Any]] = {}
    for _cid, lesson_id, eid, slug, name, claims in rows:
        e = merged.setdefault(
            eid, {"id": str(eid), "slug": slug, "name": name, "claims": 0, "lessons": []}
        )
        e["claims"] += claims
        if lesson_id:
            e["lessons"].append(str(lesson_id))
    return sorted(merged.values(), key=lambda e: (-e["claims"], e["name"]))


@router.get("/entities/{entity_id}/courses")
async def entity_courses(
    entity_id: uuid.UUID, db: DbDep, _: UserDep, limit: Annotated[int, Query(ge=1, le=200)] = 100
):
    rows = (await db.execute(_concepts_query().where(Entity.id == entity_id))).all()
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for course_id, lesson_id, *_rest, claims in rows:
        c = await db.get(Course, course_id)
        entry = out.setdefault(
            course_id,
            {
                "course_id": str(course_id),
                "title": c.title,
                "status": c.status,
                "claims": 0,
                "lessons": [],
            },
        )
        entry["claims"] += claims
        if lesson_id:
            lesson = await db.get(Lesson, lesson_id)
            entry["lessons"].append({"id": str(lesson_id), "title": lesson.title})
    return list(out.values())[:limit]


# ---- dashboard ------------------------------------------------------------------------


@router.get("/learning/overview")
async def overview(db: DbDep, _: UserDep):
    courses = (
        await db.scalars(
            select(Course)
            .where(Course.status == "in_progress")
            .order_by(Course.updated_at.desc().nulls_last())
        )
    ).all()
    prog = await _progress(db, [c.id for c in courses])
    counts = {
        "programs": await db.scalar(select(func.count()).select_from(Program)),
        "courses": await db.scalar(select(func.count()).select_from(Course)),
        "sources": await db.scalar(
            select(func.count()).select_from(Source).where(Source.status != "archived")
        ),
        "concepts": await db.scalar(
            select(func.count())
            .select_from(Entity)
            .where(Entity.status.in_(("active", "proposed")))
        ),
    }
    from kos.models import ReviewTask  # local import keeps the dependency obvious

    counts["open_reviews"] = await db.scalar(
        select(func.count()).select_from(ReviewTask).where(ReviewTask.status == "open")
    )
    return {
        "counts": counts,
        "in_progress": [await _course_out(db, c, prog[c.id]) for c in courses[:10]],
    }
