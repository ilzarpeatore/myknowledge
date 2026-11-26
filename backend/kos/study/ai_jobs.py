"""AI-written study material: flashcards for a concept and quizzes for a module.

Both are grounded the same way as answers in the AI workspace: the model sees
fragments with refs, must cite one with a verbatim quote for every card or question,
and anything whose quote is not found in its fragment is dropped (and counted).
AI flashcards start as proposals; quiz questions show their source on correction.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from kos.ai import query as aq
from kos.ai import service as ai
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.knowledge.extraction import locate_quote
from kos.models import (
    AIRun,
    AuditLog,
    Claim,
    ClaimEntity,
    CourseMaterial,
    Entity,
    Evidence,
    Job,
    JobStatus,
    Lesson,
    Quiz,
    QuizQuestion,
    SourceSegment,
    SourceVersion,
)
from kos.study import cards

CARDS_KIND = "study.flashcards"
QUIZ_KIND = "study.quiz"
MAX_CARDS = 10
MAX_QUESTIONS = 10
QUIZ_FRAGMENTS = 20


class CardOut(BaseModel):
    front: str
    back: str
    fragment: str
    quote: str


class CardsResult(BaseModel):
    cards: list[CardOut]


class QuestionOut(BaseModel):
    kind: Literal["choice", "true_false", "short"]
    prompt: str
    options: list[str] = Field(default_factory=list)
    correct_option: int | None = None
    correct_bool: bool | None = None
    model_answer: str | None = None
    explanation: str
    fragment: str
    quote: str
    concept: str | None = None


class QuizResult(BaseModel):
    questions: list[QuestionOut]


def cards_job(entity_id: uuid.UUID) -> Job:
    return Job(
        kind=CARDS_KIND,
        status=JobStatus.pending,
        max_attempts=3,
        payload={"entity_id": str(entity_id)},
    )


def quiz_job(quiz_id: uuid.UUID) -> Job:
    return Job(
        kind=QUIZ_KIND, status=JobStatus.pending, max_attempts=3, payload={"quiz_id": str(quiz_id)}
    )


def _docs(ctx: aq.Context) -> list[ai.Document]:
    docs = [
        ai.Document(ref=f"k{i + 1}", label="ficha de concepto", text=s)
        for i, s in enumerate(ctx.sheets)
    ]
    return docs + [
        ai.Document(ref=r, label=aq._label(f), text=f["text"]) for r, f in ctx.fragments.items()
    ]


async def _run(db, ctx_job: JobContext, **kw):
    try:
        return await ai.run_structured(db, task="study", job_id=ctx_job.job_id, **kw)
    except ai.ProviderFailure as exc:
        if exc.retryable and not ctx_job.is_last_attempt:
            raise
        raise PermanentJobError(str(exc)) from None
    except ai.AIError as exc:
        raise PermanentJobError(f"{type(exc).__name__}: {exc}") from None


async def _last_run(db, job_id):
    return await db.scalar(
        select(AIRun.id).where(AIRun.job_id == job_id).order_by(AIRun.created_at.desc()).limit(1)
    )


@handler(CARDS_KIND)
async def generate_cards(ctx_job: JobContext) -> dict[str, Any]:
    try:
        entity_id = uuid.UUID(ctx_job.payload["entity_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None
    async with ctx_job.sessionmaker() as db:
        entity = await cards.resolve_entity(db, entity_id)
        if entity is None:
            raise PermanentJobError("concepto no encontrado")
        ctx = aq.Context()
        ctx.sheets.append(await aq.concept_sheet(db, entity, ctx))
        if not ctx.fragments:
            raise PermanentJobError("el concepto no tiene afirmaciones con fuente")
        result: CardsResult = await _run(
            db,
            ctx_job,
            prompt_key="generate_flashcards",
            schema=CardsResult,
            instructions=f"Escribe tarjetas sobre «{entity.canonical_name}».",
            documents=_docs(ctx),
            variables={"concept": entity.canonical_name, "max_cards": str(MAX_CARDS)},
        )
        run_id = await _last_run(db, ctx_job.job_id)
        made, dropped = 0, 0
        fronts = set(
            (
                await db.scalars(
                    select(func.lower(cards.Flashcard.front)).where(
                        cards.Flashcard.entity_id == entity.id
                    )
                )
            ).all()
        )
        for c in result.cards[:MAX_CARDS]:
            frag = ctx.fragments.get(c.fragment.strip())
            found = locate_quote(frag["text"], c.quote) if frag else None
            if not found or not c.front.strip() or c.front.strip().lower() in fronts:
                dropped += 1
                continue
            fronts.add(c.front.strip().lower())
            cards.add_card(
                db,
                entity_id=entity.id,
                front=c.front,
                back=c.back,
                kind="qa",
                origin="ai",
                status="proposed",
                segment_id=uuid.UUID(frag["segment_id"]),
                quote=found[1],
                ai_run_id=run_id,
            )
            made += 1
        db.add(
            AuditLog(
                actor="ai",
                action="flashcards.generated",
                subject_table="entities",
                subject_id=str(entity.id),
                after={"proposed": made, "dropped": dropped},
            )
        )
        await db.commit()
        return {"proposed": made, "dropped": dropped}


async def module_sources(db, module_id: uuid.UUID) -> list[uuid.UUID]:
    lessons = select(Lesson.id).where(Lesson.module_id == module_id)
    return list(
        (
            await db.scalars(
                select(CourseMaterial.source_id)
                .where(CourseMaterial.lesson_id.in_(lessons))
                .distinct()
            )
        ).all()
    )


async def quiz_context(db, sources: list[uuid.UUID]) -> tuple[aq.Context, dict[str, Entity]]:
    """Fragments spread evenly over the module's materials, and the concepts the
    library links to them (through claims cited from those sources)."""
    ctx = aq.Context()
    segs = (
        await db.execute(
            select(SourceSegment.id, SourceVersion.source_id)
            .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
            .where(SourceVersion.source_id.in_(sources))
            .order_by(SourceVersion.source_id, SourceSegment.ordinal)
        )
    ).all()
    step = max(1, len(segs) // QUIZ_FRAGMENTS)
    for seg_id, _sid in segs[::step][:QUIZ_FRAGMENTS]:
        row = await aq._segment_row(db, seg_id)
        if row and len(row[0].text.strip()) > 80:
            ctx.add(row[0], row[1], row[2], row[3], row[4])
    concepts = (
        await db.scalars(
            select(Entity)
            .join(ClaimEntity, ClaimEntity.entity_id == Entity.id)
            .join(Claim, Claim.id == ClaimEntity.claim_id)
            .join(Evidence, Evidence.claim_id == Claim.id)
            .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
            .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
            .where(SourceVersion.source_id.in_(sources), Entity.status.in_(("active", "proposed")))
            .distinct()
        )
    ).all()
    return ctx, {e.canonical_name.lower(): e for e in concepts}


def _valid_question(q: QuestionOut) -> bool:
    if not q.prompt.strip():
        return False
    if q.kind == "choice":
        return (
            3 <= len(q.options) <= 5
            and q.correct_option is not None
            and 0 <= q.correct_option < len(q.options)
        )
    if q.kind == "true_false":
        return q.correct_bool is not None
    return bool(q.model_answer and q.model_answer.strip())


@handler(QUIZ_KIND)
async def generate_quiz(ctx_job: JobContext) -> dict[str, Any]:
    try:
        quiz_id = uuid.UUID(ctx_job.payload["quiz_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None
    async with ctx_job.sessionmaker() as db:
        quiz = await db.get(Quiz, quiz_id)
        if quiz is None or quiz.module_id is None:
            raise PermanentJobError("cuestionario no encontrado")
        quiz.job_id = ctx_job.job_id
        sources = await module_sources(db, quiz.module_id)
        ctx, concepts = await quiz_context(db, sources)
        if not ctx.fragments:
            await _quiz_failed(db, quiz, "el módulo no tiene materiales con texto en la biblioteca")
            raise PermanentJobError(quiz.error)
        concept_list = "\n".join(f"- {e.canonical_name}" for e in concepts.values()) or "(ninguno)"
        docs = [ai.Document(ref="k1", label="conceptos vinculados", text=concept_list), *_docs(ctx)]
        try:
            result: QuizResult = await _run(
                db,
                ctx_job,
                prompt_key="generate_quiz",
                schema=QuizResult,
                instructions=f"Cuestionario: {quiz.title}",
                documents=docs,
                variables={"max_questions": str(MAX_QUESTIONS)},
            )
        except PermanentJobError as exc:
            await _quiz_failed(db, quiz, str(exc))
            raise
        kept, dropped = 0, 0
        for q in result.questions[:MAX_QUESTIONS]:
            frag = ctx.fragments.get(q.fragment.strip())
            found = locate_quote(frag["text"], q.quote) if frag else None
            if not found or not _valid_question(q):
                dropped += 1
                continue
            correct: Any = {
                "choice": q.correct_option,
                "true_false": q.correct_bool,
                "short": (q.model_answer or "").strip(),
            }[q.kind]
            ent = concepts.get((q.concept or "").strip().lower())
            db.add(
                QuizQuestion(
                    quiz_id=quiz.id,
                    position=kept,
                    kind=q.kind,
                    prompt=q.prompt.strip(),
                    options=[o.strip() for o in q.options] if q.kind == "choice" else None,
                    correct=correct,
                    explanation=q.explanation.strip(),
                    segment_id=uuid.UUID(frag["segment_id"]),
                    quote=found[1],
                    entity_id=ent.id if ent else None,
                )
            )
            kept += 1
        quiz.ai_run_id = await _last_run(db, ctx_job.job_id)
        quiz.stats = {"questions": kept, "dropped": dropped, "fragments": len(ctx.fragments)}
        quiz.status = "ready" if kept else "failed"
        if not kept:
            quiz.error = "ninguna pregunta superó la comprobación de citas"
        await db.commit()
        return quiz.stats


async def _quiz_failed(db, quiz: Quiz, reason: str) -> None:
    quiz.status, quiz.error = "failed", reason[:2000]
    quiz.stats = {"failed_at": datetime.now(UTC).isoformat()}
    await db.commit()
