"""Automatic extraction of concepts and claims from a source (Fase 1 core).

``source.extract_knowledge`` processes a source version in batches of consecutive
segments. For each batch the model returns concepts and claims with exact quotes;
nothing it returns is trusted blindly:

- a claim is kept only if its quote is found in the cited fragment (the stored
  quote is always the original text);
- concepts are resolved against the existing ones in layers: exact name/alias,
  then candidates by text similarity (pg_trgm) and by meaning (local embeddings),
  then the model chooses only among those candidates; a doubtful match creates a
  *proposed* concept plus a review task instead of merging;
- everything created is marked ``origin="ai"`` (concepts ``proposed``, claims
  ``pending``) for human review.

Progress is saved after every batch (``knowledge_extractions.last_ordinal``), so a
stopped or failed run resumes without repeating work; re-runs skip claims that
already exist for the same fragment.
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos import embeddings
from kos.ai import service as ai
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.knowledge import service as ks
from kos.models import (
    AIModel,
    AIRun,
    AITaskRoute,
    Claim,
    Course,
    CourseMaterial,
    Discipline,
    Entity,
    EntityType,
    Evidence,
    Job,
    JobStatus,
    KnowledgeExtraction,
    Person,
    ReviewTask,
    SourceSegment,
    SourceVersion,
)

EXTRACT_KIND = "source.extract_knowledge"
BATCH_CHARS = 12_000
BATCH_MAX_SEGMENTS = 10
CANDIDATES = 5
LEXICAL_MIN = 0.3
SEMANTIC_MIN = 0.86  # E5 cosine; scores are compressed, see D-15
LINK_CONFIDENCE = 0.8
REVIEW_LEXICAL = 0.5
PROMPT_OVERHEAD_CHARS = 3_500  # system prompt + instructions per call (estimate)
OUTPUT_RATIO = 0.35  # output tokens per input content token (estimate)


# ---- structured outputs ---------------------------------------------------------------


class ExtractedConcept(BaseModel):
    name: str
    type_key: str
    definition: str | None = None
    aliases: list[str] = []
    language: str | None = None


class ExtractedClaim(BaseModel):
    text: str
    claim_type: Literal[
        "definition", "reported_fact", "hypothesis", "interpretation", "opinion", "recommendation"
    ]
    quote: str
    fragment: str
    concepts: list[str]
    attributed_to: str | None = None
    confidence: float = Field(ge=0, le=1)


class ExtractionResult(BaseModel):
    concepts: list[ExtractedConcept]
    claims: list[ExtractedClaim]


class ResolveDecision(BaseModel):
    term: str
    match_id: str | None
    confidence: float = Field(ge=0, le=1)


class ResolveResult(BaseModel):
    decisions: list[ResolveDecision]


# ---- helpers ----------------------------------------------------------------------------


def locate_quote(text: str, quote: str) -> tuple[int, str] | None:
    """Find ``quote`` in ``text``: exactly, or allowing different whitespace.
    Returns (start, exact substring of ``text``) or None."""
    quote = quote.strip().strip('«»"“”')
    if not quote:
        return None
    pos = text.find(quote)
    if pos >= 0:
        return pos, quote
    words = quote.split()
    if not words:
        return None
    m = re.search(r"\s+".join(map(re.escape, words)), text)
    return (m.start(), m.group(0)) if m else None


def batches(segments: list[SourceSegment]) -> list[list[SourceSegment]]:
    out: list[list[SourceSegment]] = []
    cur: list[SourceSegment] = []
    size = 0
    for s in segments:
        if cur and (size + len(s.text) > BATCH_CHARS or len(cur) >= BATCH_MAX_SEGMENTS):
            out.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += len(s.text)
    if cur:
        out.append(cur)
    return out


def job(version_id: uuid.UUID, extraction_id: uuid.UUID) -> Job:
    return Job(
        kind=EXTRACT_KIND,
        status=JobStatus.pending,
        max_attempts=3,
        payload={"source_version_id": str(version_id), "extraction_id": str(extraction_id)},
    )


async def in_flight(db: AsyncSession, version_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.kind == EXTRACT_KIND,
                Job.payload["source_version_id"].astext == str(version_id),
                Job.status.in_([JobStatus.pending, JobStatus.processing]),
            )
        )
    )


async def estimate(
    db: AsyncSession, version_id: uuid.UUID, after_ordinal: int = -1
) -> dict[str, Any]:
    """Rough cost estimate (USD) of extracting the remaining segments. It is an
    estimate: real cost depends on how much the model writes."""
    segs = (
        await db.scalars(
            select(SourceSegment)
            .where(
                SourceSegment.source_version_id == version_id, SourceSegment.ordinal > after_ordinal
            )
            .order_by(SourceSegment.ordinal)
        )
    ).all()
    calls = len(batches(list(segs)))
    chars = sum(len(s.text) for s in segs)
    route = await db.get(AITaskRoute, "extract")
    model = await db.get(AIModel, route.model_name) if route else None
    in_tok = (chars + calls * PROMPT_OVERHEAD_CHARS) / 3.5
    out_tok = chars / 3.5 * OUTPUT_RATIO
    cost = None
    if model:
        cost = round(
            float(
                (
                    in_tok * float(model.input_usd_per_mtok)
                    + out_tok * float(model.output_usd_per_mtok)
                )
                / 1_000_000
            )
            * 1.25,
            4,
        )
    return {
        "segments": len(segs),
        "calls": calls,
        "input_tokens": int(in_tok),
        "output_tokens": int(out_tok),
        "model": model.name if model else None,
        "cost_usd": cost,
        "note": "estimación (+25 % por la resolución de duplicados)",
    }


# ---- concept resolution ------------------------------------------------------------------


class Resolver:
    """Maps extracted concept names to entity ids for one extraction run."""

    def __init__(self, db: AsyncSession, ctx: JobContext, run_ai, discipline: str):
        self.db, self.ctx, self.run_ai, self.discipline = db, ctx, run_ai, discipline
        self.cache: dict[str, uuid.UUID] = {}  # normalized name -> entity id
        self.stats = {"concepts_new": 0, "concepts_linked": 0, "merge_reviews": 0}
        self._emb_ids: list[uuid.UUID] | None = None
        self._emb: np.ndarray | None = None

    async def _semantic(self, text: str) -> list[tuple[uuid.UUID, float]]:
        try:
            embedder = await asyncio.to_thread(embeddings.get_embedder)
        except embeddings.EmbeddingsUnavailable:
            return []
        if self._emb is None:
            rows = (
                await self.db.execute(
                    select(Entity.id, Entity.canonical_name, Entity.short_definition).where(
                        Entity.status.in_(ks.LIVE)
                    )
                )
            ).all()
            self._emb_ids = [r.id for r in rows]
            texts = [f"{r.canonical_name}: {r.short_definition or ''}" for r in rows]
            self._emb = (
                np.array(await asyncio.to_thread(embedder.embed_passages, texts))
                if texts
                else np.zeros((0, 1))
            )
        if not self._emb_ids:
            return []
        q = np.array(await asyncio.to_thread(embedder.embed_query, text))
        scores = self._emb @ q
        order = np.argsort(-scores)[:CANDIDATES]
        return [(self._emb_ids[i], float(scores[i])) for i in order if scores[i] >= SEMANTIC_MIN]

    def _remember(self, entity_id: uuid.UUID, c: ExtractedConcept) -> None:
        for n in [c.name, *c.aliases]:
            if ks.normalize(n):
                self.cache[ks.normalize(n)] = entity_id
        if self._emb_ids is not None:
            self._emb = None  # recompute lazily with the new entity

    async def _link(self, entity_id: uuid.UUID, c: ExtractedConcept) -> None:
        for alias in [c.name, *c.aliases]:
            try:
                await ks.add_alias(self.db, entity_id, alias, language=c.language)
            except ks.KnowledgeError:
                pass  # already known, or belongs to another concept: never steal it
        self.stats["concepts_linked"] += 1
        self._remember(entity_id, c)

    async def _create(self, c: ExtractedConcept, types: set[str]) -> uuid.UUID:
        e = await ks.create_entity(
            self.db,
            type_key=c.type_key if c.type_key in types else "concept",
            name=c.name,
            definition=c.definition,
            aliases=[
                (a, c.language) for a in c.aliases if not await ks.entity_with_alias(self.db, a)
            ],
            origin="ai",
            status="proposed",
        )
        self.stats["concepts_new"] += 1
        self._remember(e.id, c)
        return e.id

    async def resolve(self, concepts: list[ExtractedConcept]) -> None:
        types = set((await self.db.scalars(select(EntityType.key))).all())
        pending: list[tuple[ExtractedConcept, list[dict[str, Any]]]] = []
        for c in concepts:
            if not ks.normalize(c.name) or ks.normalize(c.name) in self.cache:
                continue
            # 1. exact name or alias
            exact = None
            for n in [c.name, *c.aliases]:
                if (exact := await ks.entity_with_alias(self.db, n)) is not None:
                    break
            if exact is not None:
                await self._link(exact.id, c)
                continue
            # 2. candidates by text and by meaning
            cands: dict[uuid.UUID, dict[str, Any]] = {}
            for row in await ks.candidates(
                self.db, c.name, limit=CANDIDATES, min_similarity=LEXICAL_MIN
            ):
                cands[row["id"]] = {
                    "id": row["id"],
                    "lexical": float(row["score"]),
                    "semantic": 0.0,
                }
            for eid, score in await self._semantic(f"{c.name}: {c.definition or ''}"):
                cands.setdefault(eid, {"id": eid, "lexical": 0.0, "semantic": 0.0})["semantic"] = (
                    score
                )
            if not cands:
                await self._create(c, types)
            else:
                pending.append((c, list(cands.values())))
        if not pending:
            return
        # 3. the model chooses only among the candidates
        docs, by_term = [], {}
        for i, (c, cands) in enumerate(pending):
            lines = []
            for cand in cands:
                e = await self.db.get(Entity, cand["id"])
                lines.append(
                    f"- id {e.id}: {e.canonical_name}"
                    + (f" — {e.short_definition}" if e.short_definition else "")
                )
            term = f"t{i + 1}"
            by_term[term] = (c, {str(x["id"]): x for x in cands})
            docs.append(
                ai.Document(
                    ref=term,
                    label=c.name,
                    text=(
                        f"Término nuevo: {c.name}\n"
                        f"Definición o contexto: {c.definition or '(sin definición)'}\n"
                        "Candidatos existentes:\n" + "\n".join(lines)
                    ),
                )
            )
        result: ResolveResult = await self.run_ai(
            task="resolve",
            prompt_key="resolve_concepts",
            schema=ResolveResult,
            instructions=(
                "Para cada término (ref t1, t2…), indica en 'term' su ref y el id del "
                "candidato que sea el mismo concepto, o null."
            ),
            documents=docs,
            variables={"discipline": self.discipline},
        )
        decided = {d.term: d for d in result.decisions}
        for term, (c, cands) in by_term.items():
            d = decided.get(term)
            match = d.match_id if d and d.match_id in cands else None
            if match and d.confidence >= LINK_CONFIDENCE:
                await self._link(uuid.UUID(match), c)
                continue
            new_id = await self._create(c, types)
            # doubtful: a suggested match, or a strong text resemblance -> human decides
            suspect = match or max(cands.values(), key=lambda x: x["lexical"])["id"]
            suspect_row = cands.get(str(suspect))
            if match or (suspect_row and suspect_row["lexical"] >= REVIEW_LEXICAL):
                await self._review_merge(
                    new_id, uuid.UUID(str(suspect)), d.confidence if d else None
                )

    async def _review_merge(self, a: uuid.UUID, b: uuid.UUID, confidence: float | None) -> None:
        x, y = sorted((a, b), key=str)
        key = f"{x}:{y}"
        if await self.db.scalar(
            select(ReviewTask.id).where(
                ReviewTask.kind == "entity_merge", ReviewTask.subject_key == key
            )
        ):
            return
        ea, eb = await self.db.get(Entity, x), await self.db.get(Entity, y)
        self.db.add(
            ReviewTask(
                kind="entity_merge",
                subject_key=key,
                payload={
                    "entity_a": str(x),
                    "entity_b": str(y),
                    "name_a": ea.canonical_name,
                    "name_b": eb.canonical_name,
                    "score": confidence or 0,
                    "origin": "extraction",
                },
            )
        )
        await self.db.commit()
        self.stats["merge_reviews"] += 1

    async def entity_for(self, name: str) -> uuid.UUID | None:
        n = ks.normalize(name)
        if n in self.cache:
            return self.cache[n]
        e = await ks.entity_with_alias(self.db, name)
        if e is not None:
            self.cache[n] = e.id
            return e.id
        return None


# ---- the job ----------------------------------------------------------------------------


async def _context(db: AsyncSession, version: SourceVersion) -> tuple[uuid.UUID | None, str]:
    """Teacher to attribute claims to, and the discipline label, from the course(s)
    that use this source."""
    course = (
        await db.execute(
            select(Course)
            .join(CourseMaterial, CourseMaterial.course_id == Course.id)
            .where(CourseMaterial.source_id == version.source_id)
            .order_by(Course.teacher_person_id.is_(None), Course.title)
            .limit(1)
        )
    ).scalar()
    teacher = course.teacher_person_id if course else None
    discipline = None
    if course and course.discipline_id:
        discipline = await db.scalar(
            select(Discipline.name).where(Discipline.id == course.discipline_id)
        )
    return teacher, discipline or "ciencias del entrenamiento"


@handler(EXTRACT_KIND)
async def extract_knowledge(ctx: JobContext) -> dict[str, Any]:
    try:
        version_id = uuid.UUID(ctx.payload["source_version_id"])
        extraction_id = uuid.UUID(ctx.payload["extraction_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None

    async with ctx.sessionmaker() as db:
        ex = await db.get(KnowledgeExtraction, extraction_id)
        version = await db.get(SourceVersion, version_id)
        if ex is None or version is None:
            raise PermanentJobError("extracción o versión no encontrada")
        ex.status, ex.error, ex.job_id = "running", None, ctx.job_id
        await db.commit()
        teacher, discipline = await _context(db, version)
        types = ", ".join((await db.scalars(select(EntityType.key).order_by(EntityType.key))).all())
        segs = (
            await db.scalars(
                select(SourceSegment)
                .where(
                    SourceSegment.source_version_id == version_id,
                    SourceSegment.ordinal > ex.last_ordinal,
                )
                .order_by(SourceSegment.ordinal)
            )
        ).all()
        groups = batches(list(segs))
        stats = dict(ex.stats or {})

        async def run_ai(**kw: Any) -> Any:
            try:
                return await ai.run_structured(db, job_id=ctx.job_id, **kw)
            except ai.ProviderFailure as exc:
                if exc.retryable:
                    raise
                raise PermanentJobError(str(exc)) from None
            except ai.AIError as exc:
                raise PermanentJobError(f"{type(exc).__name__}: {exc}") from None

        resolver = Resolver(db, ctx, run_ai, discipline)
        try:
            for n, group in enumerate(groups):
                refs = {f"s{i + 1}": s for i, s in enumerate(group)}
                docs = [
                    ai.Document(
                        ref=r,
                        label=f"{s.loc_section or ''} p. {s.loc_page_start or '-'}".strip(),
                        text=s.text,
                    )
                    for r, s in refs.items()
                ]
                result: ExtractionResult = await run_ai(
                    task="extract",
                    prompt_key="extract_knowledge",
                    schema=ExtractionResult,
                    instructions="Extrae conceptos y afirmaciones de estos fragmentos.",
                    documents=docs,
                    variables={"discipline": discipline, "entity_types": types},
                )
                run_id = await db.scalar(
                    select(AIRun.id)
                    .where(AIRun.job_id == ctx.job_id)
                    .order_by(AIRun.created_at.desc())
                    .limit(1)
                )
                await resolver.resolve(result.concepts)
                for cl in result.claims:
                    await _store_claim(db, cl, refs, resolver, teacher, run_id, stats)
                ex = await db.get(KnowledgeExtraction, extraction_id)
                ex.last_ordinal = group[-1].ordinal
                for k, v in resolver.stats.items():
                    stats[k] = stats.get(k, 0) + v
                resolver.stats = dict.fromkeys(resolver.stats, 0)
                stats["batches"] = stats.get("batches", 0) + 1
                ex.stats = dict(stats)
                await db.commit()
                await ctx.progress((n + 1) / len(groups))
        except PermanentJobError as exc:
            await db.rollback()
            ex = await db.get(KnowledgeExtraction, extraction_id)
            ex.status, ex.error = "stopped", str(exc)[:2000]
            await db.commit()
            raise
        ex = await db.get(KnowledgeExtraction, extraction_id)
        ex.status, ex.finished_at = "completed", datetime.now(UTC)
        await db.commit()
        return dict(ex.stats)


async def _store_claim(
    db, cl: ExtractedClaim, refs, resolver: Resolver, teacher, run_id, stats
) -> None:
    seg = refs.get(cl.fragment.strip())
    if seg is None:
        stats["claims_unknown_fragment"] = stats.get("claims_unknown_fragment", 0) + 1
        return
    found = locate_quote(seg.text, cl.quote)
    if found is None:
        stats["claims_quote_not_found"] = stats.get("claims_quote_not_found", 0) + 1
        return
    _, quote = found
    entity_ids = []
    for name in cl.concepts:
        eid = await resolver.entity_for(name)
        if eid is None:  # a concept the model used in a claim but did not list
            await resolver.resolve([ExtractedConcept(name=name, type_key="concept")])
            eid = await resolver.entity_for(name)
        if eid is not None and eid not in entity_ids:
            entity_ids.append(eid)
    if not entity_ids:
        stats["claims_without_concept"] = stats.get("claims_without_concept", 0) + 1
        return
    dup = await db.scalar(
        select(Claim.id)
        .join(Evidence, Evidence.claim_id == Claim.id)
        .where(Evidence.segment_id == seg.id, Claim.text == cl.text.strip())
    )
    if dup:
        stats["claims_duplicate"] = stats.get("claims_duplicate", 0) + 1
        return
    try:
        await ks.add_claim(
            db,
            text_=cl.text,
            claim_type=cl.claim_type,
            entities=[(e, "subject") for e in entity_ids],
            evidence=[(seg.id, quote)],
            person_id=teacher,
            origin="ai",
            confidence=cl.confidence,
            ai_run_id=run_id,
            context=f"Atribuido en el texto a: {cl.attributed_to}" if cl.attributed_to else None,
        )
        stats["claims_new"] = stats.get("claims_new", 0) + 1
    except ks.KnowledgeError:
        stats["claims_rejected"] = stats.get("claims_rejected", 0) + 1


async def start(
    db: AsyncSession, version: SourceVersion, restart: bool = False
) -> tuple[KnowledgeExtraction, Job]:
    """Queue an extraction: resumes the latest stopped one unless ``restart``."""
    latest = await db.scalar(
        select(KnowledgeExtraction)
        .where(KnowledgeExtraction.source_version_id == version.id)
        .order_by(KnowledgeExtraction.created_at.desc())
    )
    if latest is not None and latest.status != "completed" and not restart:
        ex = latest
    else:
        ex = KnowledgeExtraction(source_version_id=version.id, status="running", stats={})
        db.add(ex)
        await db.flush()
    j = job(version.id, ex.id)
    db.add(j)
    await db.commit()
    return ex, j


async def person_name(db: AsyncSession, person_id: uuid.UUID | None) -> str | None:
    return (await db.get(Person, person_id)).name if person_id else None
