"""The comprehension reading of a lesson and the feedback on the user's explanations.

The reading has three layers that are never mixed: what the lesson's material says
(cited, quote checked against the slide), what other sources of the library add
(cited the same way), and the AI's own explanation (shown as general knowledge).
A library point without a valid citation is dropped; a material point without one is
kept and marked. «Explícalo con tus palabras» is graded against the material and
the reading; the best score is the lesson's «understanding», and 70 or more marks it
as understood (completed).
"""

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.ai import service as ai
from kos.config import get_settings
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.knowledge.extraction import locate_quote
from kos.models import (
    AIModel,
    AIRun,
    AITaskRoute,
    AuditLog,
    Claim,
    ClaimEntity,
    Course,
    CourseModule,
    Entity,
    Evidence,
    Job,
    JobStatus,
    Lesson,
    LessonExplanation,
    LessonProgress,
    LessonReading,
    SourceSegment,
    SourceVersion,
)
from kos.study import lessons as ls

READING_KIND = "lesson.reading"
FEEDBACK_KIND = "lesson.feedback"
TASK = "teach"
LIBRARY_FRAGMENTS = 10
UNDERSTOOD = 70


class CiteOut(BaseModel):
    fragment: str
    quote: str


class PointOut(BaseModel):
    text: str
    citations: list[CiteOut] = Field(default_factory=list)


class SectionOut(BaseModel):
    heading: str
    material: list[PointOut] = Field(default_factory=list)
    library: list[PointOut] = Field(default_factory=list)
    explanation: str | None = None


class CheckOut(BaseModel):
    question: str
    answer: str
    citations: list[CiteOut] = Field(default_factory=list)


class ReadingOut(BaseModel):
    objectives: list[str]
    sections: list[SectionOut]
    checks: list[CheckOut] = Field(default_factory=list)


class FeedbackOut(BaseModel):
    score: int = Field(ge=0, le=100)
    understood: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    misconceptions: list[str] = Field(default_factory=list)
    next_step: str | None = None


def target_words(tokens: int) -> int:
    # slides are terse: the reading explains them, so it is longer than the material
    return int(max(500, min(2000, tokens * 0.75 * 1.2)))


async def lesson_context(db: AsyncSession, lesson: Lesson) -> tuple[Course, CourseModule]:
    module = await db.get(CourseModule, lesson.module_id)
    return await db.get(Course, module.course_id), module


async def estimate(db: AsyncSession, lesson: Lesson) -> dict[str, Any]:
    segs = await ls.segments_of(db, await ls.parts_of(db, lesson.id))
    tokens = sum(s.token_count for s in segs)
    words = target_words(tokens)
    route = await db.get(AITaskRoute, TASK)
    model = await db.get(AIModel, route.model_name) if route else None
    usd = None
    if model is not None:
        # same calibration as chapter summaries, plus the library fragments
        in_tokens = int((tokens + LIBRARY_FRAGMENTS * 350) * 2.3) + 900
        usd = float(
            (
                Decimal(in_tokens) * model.input_usd_per_mtok
                + Decimal(words * 9) * model.output_usd_per_mtok
            )
            / ai.MTOK
        )
    return {
        "tokens": tokens,
        "target_words": words,
        "model": model.name if model else None,
        "usd": round(usd, 4) if usd is not None else None,
        "too_long": sum(len(s.text) for s in segs) > ls.MAX_LESSON_CHARS,
    }


async def topic_names(db: AsyncSession, seg_ids: list[uuid.UUID], limit: int = 8) -> list[str]:
    """Concepts with claims cited from these fragments, most cited first."""
    if not seg_ids:
        return []
    n = func.count(func.distinct(Claim.id))
    rows = (
        await db.execute(
            select(Entity.canonical_name)
            .join(ClaimEntity, ClaimEntity.entity_id == Entity.id)
            .join(Claim, Claim.id == ClaimEntity.claim_id)
            .join(Evidence, Evidence.claim_id == Claim.id)
            .where(Evidence.segment_id.in_(seg_ids), Entity.status.in_(("active", "proposed")))
            .group_by(Entity.canonical_name)
            .order_by(n.desc())
            .limit(limit)
        )
    ).all()
    return [r[0] for r in rows]


async def library_fragments(
    db: AsyncSession, query: str, own_sources: set[uuid.UUID]
) -> list[tuple[SourceSegment, str, uuid.UUID, SourceVersion]]:
    """Fragments of OTHER sources of the library about the lesson's topics."""
    from kos.ai.query import _segment_row
    from kos.api.routes.search import hybrid_hits

    hits = await hybrid_hits(
        db, query[:300], "hybrid", 40, get_settings().semantic_min_score, None, any_words=True
    )
    out = []
    for h in hits:
        row = await _segment_row(db, h["segment_id"])
        if row is None or row[2] in own_sources:
            continue
        out.append((row[0], row[1], row[2], row[3]))
        if len(out) >= LIBRARY_FRAGMENTS:
            break
    return out


def _cite(cites: list[CiteOut], frags: dict[str, dict[str, Any]], prefix: str) -> list[dict]:
    kept, seen = [], set()
    for c in cites:
        ref = c.fragment.strip()
        f = frags.get(ref)
        if f is None or not ref.startswith(prefix):
            continue
        found = locate_quote(f["text"], c.quote)
        if not found or (ref, found[0]) in seen:
            continue
        seen.add((ref, found[0]))
        kept.append({k: v for k, v in f.items() if k != "text"} | {"quote": found[1]})
    return kept


def validate(result: ReadingOut, frags: dict[str, dict[str, Any]]) -> dict[str, Any]:
    sections, cited, uncited, dropped = [], 0, 0, 0
    for s in result.sections:
        material = []
        for p in s.material:
            if not p.text.strip():
                continue
            cites = _cite(p.citations, frags, "m")
            cited += len(cites)
            uncited += 0 if cites else 1
            material.append({"text": p.text.strip(), "citations": cites})
        library = []
        for p in s.library:
            cites = _cite(p.citations, frags, "o")
            if not cites or not p.text.strip():
                dropped += 1  # the library layer only says what a fragment says
                continue
            cited += len(cites)
            library.append({"text": p.text.strip(), "citations": cites})
        explanation = (s.explanation or "").strip() or None
        if material or library or explanation:
            sections.append(
                {
                    "heading": s.heading.strip(),
                    "material": material,
                    "library": library,
                    "explanation": explanation,
                }
            )
    checks = [
        {
            "question": c.question.strip(),
            "answer": c.answer.strip(),
            "citations": _cite(c.citations, frags, "m") + _cite(c.citations, frags, "o"),
        }
        for c in result.checks
        if c.question.strip() and c.answer.strip()
    ]
    return {
        "objectives": [o.strip() for o in result.objectives if o.strip()],
        "sections": sections,
        "checks": checks,
        "_counts": {"citations": cited, "uncited": uncited, "dropped": dropped},
    }


def _where(cites: list[dict[str, Any]]) -> str:
    if not cites:
        return " [sin cita]"
    parts = []
    for c in cites:
        label = f"p. {c['page']}" if c.get("page") else ""
        if not c.get("own"):
            label = f"{c['source_title']}{', ' + label if label else ''}"
        if label and label not in parts:
            parts.append(label)
    return f" [{'; '.join(parts)}]" if parts else ""


def to_markdown(lesson: str, module: str, content: dict[str, Any]) -> str:
    out = [f"# {lesson}", f"*{module}*", ""]
    if content["objectives"]:
        out += ["**Al terminar deberías entender:**", ""]
        out += [f"- {o}" for o in content["objectives"]] + [""]
    for s in content["sections"]:
        out += [f"## {s['heading']}", ""]
        if s["material"]:
            out += ["**El material:**", ""]
            out += [f"- {p['text']}{_where(p['citations'])}" for p in s["material"]] + [""]
        if s["library"]:
            out += ["**Lo que añade tu biblioteca:**", ""]
            out += [f"- {p['text']}{_where(p['citations'])}" for p in s["library"]] + [""]
        if s["explanation"]:
            out += [f"**Explicación (IA, conocimiento general):** {s['explanation']}", ""]
    if content["checks"]:
        out += ["## Comprueba que lo has entendido", ""]
        for c in content["checks"]:
            out += [f"- **{c['question']}** {c['answer']}{_where(c['citations'])}"]
        out.append("")
    return "\n".join(out).strip() + "\n"


def _words(content: dict[str, Any]) -> int:
    parts = list(content["objectives"])
    for s in content["sections"]:
        parts += [p["text"] for p in s["material"] + s["library"]] + [s["explanation"] or ""]
    parts += [c["question"] + " " + c["answer"] for c in content["checks"]]
    return len(re.findall(r"\w+", " ".join(parts)))


def reading_job(lesson_id: uuid.UUID) -> Job:
    return Job(
        kind=READING_KIND,
        status=JobStatus.pending,
        max_attempts=3,
        payload={"lesson_id": str(lesson_id)},
    )


def feedback_job(explanation_id: uuid.UUID) -> Job:
    return Job(
        kind=FEEDBACK_KIND,
        status=JobStatus.pending,
        max_attempts=3,
        payload={"explanation_id": str(explanation_id)},
    )


async def in_flight(db: AsyncSession, lesson_id: uuid.UUID) -> str | None:
    job = await db.scalar(
        select(Job.id)
        .where(
            Job.kind == READING_KIND,
            Job.payload["lesson_id"].astext == str(lesson_id),
            Job.status.in_([JobStatus.pending, JobStatus.processing]),
        )
        .limit(1)
    )
    return str(job) if job else None


async def last_failure(db: AsyncSession, lesson_id: uuid.UUID) -> str | None:
    job = await db.scalar(
        select(Job)
        .where(Job.kind == READING_KIND, Job.payload["lesson_id"].astext == str(lesson_id))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if job is None or job.status != JobStatus.failed:
        return None
    return job.last_error or "la generación falló"


async def _call(ctx: JobContext, db: AsyncSession, **kw: Any) -> Any:
    try:
        return await ai.run_structured(db, task=TASK, job_id=ctx.job_id, **kw)
    except ai.ProviderFailure as exc:
        if exc.retryable and not ctx.is_last_attempt:
            raise
        raise PermanentJobError(str(exc)) from None
    except ai.AIError as exc:
        raise PermanentJobError(f"{type(exc).__name__}: {exc}") from None


async def _run_id(db: AsyncSession, job_id: uuid.UUID) -> uuid.UUID | None:
    return await db.scalar(
        select(AIRun.id).where(AIRun.job_id == job_id).order_by(AIRun.created_at.desc()).limit(1)
    )


def _frag(seg: SourceSegment, title: str, sid: uuid.UUID, v: SourceVersion, own: bool) -> dict:
    return {
        "text": seg.text,
        "fragment_id": str(seg.id),
        "page": seg.loc_page_start,
        "source_id": str(sid),
        "source_title": title,
        "version_id": str(v.id),
        "mime": v.mime,
        "own": own,
    }


def _label(f: dict[str, Any]) -> str:
    page = f"p. {f['page']}" if f["page"] else ""
    if f["own"]:
        return page
    return f"{f['source_title']}, {page}" if page else f["source_title"]


@handler(READING_KIND)
async def generate_reading(ctx: JobContext) -> dict[str, Any]:
    from kos.ai.query import _segment_row

    try:
        lesson_id = uuid.UUID(ctx.payload["lesson_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None
    async with ctx.sessionmaker() as db:
        lesson = await db.get(Lesson, lesson_id)
        if lesson is None:
            raise PermanentJobError("lección no encontrada")
        course, module = await lesson_context(db, lesson)
        parts = await ls.parts_of(db, lesson.id)
        segs = await ls.segments_of(db, parts)
        if not segs:
            raise PermanentJobError("la lección no tiene material con texto")
        if sum(len(s.text) for s in segs) > ls.MAX_LESSON_CHARS:
            raise PermanentJobError("la lección es demasiado larga: divídela en dos")
        frags: dict[str, dict[str, Any]] = {}
        own_sources: set[uuid.UUID] = set()
        for i, seg in enumerate(segs):
            row = await _segment_row(db, seg.id)
            own_sources.add(row[2])
            frags[f"m{i + 1}"] = _frag(seg, row[1], row[2], row[3], own=True)
        topics = await topic_names(db, [s.id for s in segs])
        query = " ".join([lesson.title, *topics])
        library = await library_fragments(db, query, own_sources)
        for i, (seg, title, sid, v) in enumerate(library):
            frags[f"o{i + 1}"] = _frag(seg, title, sid, v, own=False)
        docs = [ai.Document(ref=r, label=_label(f), text=f["text"]) for r, f in frags.items()]
        tokens = sum(s.token_count for s in segs)
        result: ReadingOut = await _call(
            ctx,
            db,
            prompt_key="lesson_reading",
            schema=ReadingOut,
            instructions=f"Prepara la lectura de comprensión de «{lesson.title}».",
            documents=docs,
            variables={
                "lesson": lesson.title,
                "module": module.title,
                "course": course.title,
                "target_words": str(target_words(tokens)),
            },
        )
        content = validate(result, frags)
        counts = content.pop("_counts")
        if not content["sections"]:
            raise PermanentJobError("la IA no devolvió ninguna sección utilizable")
        content["library_sources"] = sorted(
            {f["source_title"] for f in frags.values() if not f["own"]}
        )
        row = LessonReading(
            lesson_id=lesson.id,
            content=content,
            markdown=to_markdown(lesson.title, module.title, content),
            words=_words(content),
            citations=counts["citations"],
            origin=ctx.payload.get("origin", "ai"),
            ai_run_id=await _run_id(db, ctx.job_id),
        )
        db.add(row)
        db.add(
            AuditLog(
                actor="worker",
                action="lesson.reading_generated",
                subject_table="lessons",
                subject_id=str(lesson.id),
                after=counts,
            )
        )
        await db.commit()
        return {"reading_id": str(row.id), "words": row.words, **counts}


async def current_reading(db: AsyncSession, lesson_id: uuid.UUID) -> LessonReading | None:
    return await db.scalar(
        select(LessonReading)
        .where(LessonReading.lesson_id == lesson_id)
        .order_by(LessonReading.created_at.desc())
        .limit(1)
    )


@handler(FEEDBACK_KIND)
async def grade_explanation(ctx: JobContext) -> dict[str, Any]:
    try:
        explanation_id = uuid.UUID(ctx.payload["explanation_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None
    async with ctx.sessionmaker() as db:
        ex = await db.get(LessonExplanation, explanation_id)
        if ex is None:
            raise PermanentJobError("explicación no encontrada")
        lesson = await db.get(Lesson, ex.lesson_id)
        segs = await ls.segments_of(db, await ls.parts_of(db, lesson.id))
        docs, size = [], 0
        reading = await current_reading(db, lesson.id)
        if reading is not None:
            docs.append(ai.Document(ref="lectura", label="lectura", text=reading.markdown))
        for i, s in enumerate(segs):
            if size > 60_000:
                break
            size += len(s.text)
            docs.append(ai.Document(ref=f"m{i + 1}", label=f"p. {s.loc_page_start}", text=s.text))
        docs.append(ai.Document(ref="alumno", label="explicación del alumno", text=ex.text))
        try:
            result: FeedbackOut = await _call(
                ctx,
                db,
                prompt_key="lesson_feedback",
                schema=FeedbackOut,
                instructions="Corrige la explicación del alumno (documento «alumno»).",
                documents=docs,
                variables={"lesson": lesson.title},
            )
        except PermanentJobError:
            ex.status = "failed"
            await db.commit()
            raise
        ex.status, ex.score = "done", result.score
        ex.feedback = result.model_dump()
        ex.ai_run_id = await _run_id(db, ctx.job_id)
        p = await db.get(LessonProgress, lesson.id) or LessonProgress(
            lesson_id=lesson.id, status="not_started", time_spent_minutes=0
        )
        db.add(p)
        now = datetime.now(UTC)
        p.understanding = max(p.understanding or 0, result.score)
        p.last_access_at = now
        if p.understanding >= UNDERSTOOD and p.status != "completed":
            p.status, p.completed_at = "completed", now
        elif p.status == "not_started":
            p.status = "in_progress"
        await db.commit()
        return {"score": result.score, "understanding": p.understanding}
