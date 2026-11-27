"""Analysis of a source against the rest of the library, run after its knowledge is
extracted (or on demand). Three steps, each saved on its own:

1. link proposals to other sources (no AI): passages that say the same, grouped by
   source; then the AI names the kind of link (amplía, resume, contradice, aplica);
2. possible contradictions with claims of other sources about the same concepts
   (AI), into Revisión as candidates;
3. checks of non-study claims (teachers, courses) against study claims (AI), as
   proposals you accept or reject.

Without the AI (no key, budget reached) step 1 still proposes «relacionado» links and
the job records why the AI steps were skipped; nothing is presented as verified.
"""

import uuid
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kos.ai import service as ai
from kos.analysis.vectors import claim_vectors, cosine
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.knowledge import compare
from kos.models import (
    Claim,
    ClaimCheck,
    ClaimEntity,
    Contradiction,
    Evidence,
    Job,
    JobStatus,
    Person,
    Publication,
    ReviewTask,
    Source,
    SourceLink,
    SourceSegment,
    SourceVersion,
)

ANALYZE_KIND = "source.analyze"
CHECK_ENTITY_KIND = "analysis.check_entity"
LINK_MIN_SIMILARITY = 0.90  # passages on the same point; unrelated topics reach 0.86
LINK_MIN_PAIRS = 2
CONTRA_MIN_SIMILARITY = 0.78
CHECK_MIN_SIMILARITY = 0.72
MAX_PAIRS = 40
BATCH = 15


def analyze_job(source_id: uuid.UUID) -> Job:
    return Job(
        kind=ANALYZE_KIND,
        payload={"source_id": str(source_id)},
        status=JobStatus.pending,
        max_attempts=2,
    )


def check_entity_job(entity_id: uuid.UUID) -> Job:
    return Job(
        kind=CHECK_ENTITY_KIND,
        payload={"entity_id": str(entity_id)},
        status=JobStatus.pending,
        max_attempts=2,
    )


# ---- 1. link proposals -------------------------------------------------------------

PAIRS_SQL = text(
    """
    SELECT a.segment_id AS a_seg, n.segment_id AS b_seg, n.source_id AS b_src, n.sim
    FROM segment_embeddings a
    JOIN source_segments sa ON sa.id = a.segment_id
    JOIN source_versions va ON va.id = sa.source_version_id
    CROSS JOIN LATERAL (
        SELECT e.segment_id, v.source_id, 1 - (e.embedding <=> a.embedding) AS sim
        FROM segment_embeddings e
        JOIN source_segments s ON s.id = e.segment_id
        JOIN source_versions v ON v.id = s.source_version_id
        JOIN sources so ON so.id = v.source_id
        WHERE v.source_id <> :src AND so.status <> 'archived' AND e.model = a.model
          AND NOT EXISTS (SELECT 1 FROM reference_mentions rm
                          WHERE rm.segment_id = e.segment_id AND rm.kind = 'bibliography')
        ORDER BY e.embedding <=> a.embedding
        LIMIT 5
    ) n
    WHERE va.source_id = :src AND n.sim >= :min
      -- a list of references is not a passage that «says» something
      AND NOT EXISTS (SELECT 1 FROM reference_mentions rm
                      WHERE rm.segment_id = a.segment_id AND rm.kind = 'bibliography')
    """
)


async def suggest_links(
    db: AsyncSession,
    source_id: uuid.UUID,
    *,
    min_similarity: float = LINK_MIN_SIMILARITY,
    min_pairs: int = LINK_MIN_PAIRS,
) -> list[SourceLink]:
    """Propose a link to each source that shares several near-identical passages.
    An existing link (any status, also one you rejected) is never proposed again."""
    rows = (await db.execute(PAIRS_SQL, {"src": source_id, "min": min_similarity})).all()
    per: dict[uuid.UUID, list[tuple[uuid.UUID, uuid.UUID, float]]] = defaultdict(list)
    for a, b, other, sim in rows:
        per[other].append((a, b, float(sim)))
    created = []
    for other, pairs in per.items():
        uniq = {}
        for a, b, s in sorted(pairs, key=lambda p: -p[2]):
            uniq.setdefault(a, (a, b, s))  # one partner per passage
        pairs = list(uniq.values())
        if len(pairs) < min_pairs and pairs[0][2] < 0.93:
            continue
        x, y = sorted((source_id, other), key=str)
        if await db.scalar(select(SourceLink.id).where(SourceLink.a_id == x, SourceLink.b_id == y)):
            continue
        top = pairs[:3]
        flip = x != source_id  # evidence is stored as (a side, b side) of the link
        lk = SourceLink(
            a_id=x,
            b_id=y,
            kind="related",
            status="proposed",
            origin="auto",
            score=round(sum(p[2] for p in top) / len(top), 3),
            evidence=[
                {
                    "a": str(p[1] if flip else p[0]),
                    "b": str(p[0] if flip else p[1]),
                    "similarity": round(p[2], 3),
                }
                for p in top
            ],
        )
        db.add(lk)
        await db.flush()
        db.add(
            ReviewTask(
                kind="source_link",
                subject_key=str(lk.id),
                payload={
                    "link_id": str(lk.id),
                    "a_id": str(x),
                    "b_id": str(y),
                    "pairs": len(pairs),
                },
            )
        )
        created.append(lk)
    await db.commit()
    return created


class LinkKind(BaseModel):
    kind: Literal["related", "expands", "summarizes", "contradicts", "applies"]
    deeper: Literal["a", "b"] | None = None
    explanation: str = Field(max_length=600)
    confidence: float = Field(ge=0, le=1)


async def classify_link(db: AsyncSession, lk: SourceLink, job_id: uuid.UUID | None) -> LinkKind:
    a, b = await db.get(Source, lk.a_id), await db.get(Source, lk.b_id)
    docs = []
    for i, ev in enumerate(lk.evidence or [], 1):
        for side, sid in (("a", ev["a"]), ("b", ev["b"])):
            seg = await db.get(SourceSegment, uuid.UUID(sid))
            if seg:
                docs.append(
                    ai.Document(
                        ref=f"{side}{i}",
                        text=seg.text[:2500],
                        label=(a if side == "a" else b).title,
                    )
                )
    result = await ai.run_structured(
        db,
        task="classify",
        prompt_key="classify_source_link",
        schema=LinkKind,
        instructions="Clasifica la relación entre las dos fuentes según estos pasajes.",
        documents=docs,
        variables={"source_a": a.title, "source_b": b.title},
        job_id=job_id,
    )
    lk.kind = result.kind
    deeper = {"a": a.title, "b": b.title}.get(result.deeper or "")
    lk.note = result.explanation + (
        f" (más detallada: {deeper})" if deeper and result.kind != "related" else ""
    )
    lk.ai_run_id = await _last_run(db, job_id)
    await db.commit()
    return result


async def _last_run(db, job_id):
    from kos.models import AIRun

    if job_id is None:
        return None
    return await db.scalar(
        select(AIRun.id).where(AIRun.job_id == job_id).order_by(AIRun.created_at.desc()).limit(1)
    )


# ---- claims of a source and their labels --------------------------------------------


async def source_claims(db: AsyncSession, source_id: uuid.UUID) -> list[Claim]:
    return list(
        (
            await db.scalars(
                select(Claim)
                .join(Evidence, Evidence.claim_id == Claim.id)
                .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
                .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
                .where(SourceVersion.source_id == source_id, Claim.review_status != "rejected")
                .distinct()
            )
        ).all()
    )


QUALITY_ES = {"not_assessed": "no evaluada", "low": "baja", "moderate": "moderada", "high": "alta"}


async def claim_label(db: AsyncSession, c: Claim) -> str:
    who = None
    if c.publication_id:
        pub = await db.get(Publication, c.publication_id)
        q = QUALITY_ES.get(pub.methodological_quality, pub.methodological_quality)
        year = f" ({pub.year})" if pub.year else ""
        who = f"estudio: {pub.title}{year}; calidad según el usuario: {q}"
    elif c.attributed_person_id:
        who = (await db.get(Person, c.attributed_person_id)).name
    src = await db.scalar(
        select(Source.title)
        .join(SourceVersion, SourceVersion.source_id == Source.id)
        .join(SourceSegment, SourceSegment.source_version_id == SourceVersion.id)
        .join(Evidence, Evidence.segment_id == SourceSegment.id)
        .where(Evidence.claim_id == c.id)
        .limit(1)
    )
    return " — ".join(x for x in (who, src) if x) or "sin origen"


async def claim_sources(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, set[uuid.UUID]]:
    rows = (
        await db.execute(
            select(Evidence.claim_id, SourceVersion.source_id)
            .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
            .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
            .where(Evidence.claim_id.in_(ids))
        )
    ).all()
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for c, s in rows:
        out[c].add(s)
    return out


async def neighbours(
    db: AsyncSession,
    claims: list[Claim],
    *,
    exclude_source: uuid.UUID | None = None,
    include_given: bool = False,
) -> dict[uuid.UUID, list[Claim]]:
    """Other non-rejected claims that share a concept with each claim."""
    if not claims:
        return {}
    ents = (
        await db.execute(
            select(ClaimEntity.claim_id, ClaimEntity.entity_id).where(
                ClaimEntity.claim_id.in_([c.id for c in claims])
            )
        )
    ).all()
    by_entity: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for cid, eid in ents:
        by_entity[eid].add(cid)
    others = (
        await db.execute(
            select(Claim, ClaimEntity.entity_id)
            .join(ClaimEntity, ClaimEntity.claim_id == Claim.id)
            .where(ClaimEntity.entity_id.in_(list(by_entity)), Claim.review_status != "rejected")
        )
    ).all()
    out: dict[uuid.UUID, dict[uuid.UUID, Claim]] = defaultdict(dict)
    mine = {c.id for c in claims}
    for other, eid in others:
        for cid in by_entity[eid]:
            if other.id != cid and (include_given or other.id not in mine):
                out[cid][other.id] = other
    if exclude_source:
        srcs = await claim_sources(db, [o for d in out.values() for o in d])
        for cid in out:
            out[cid] = {
                k: v for k, v in out[cid].items() if exclude_source not in srcs.get(k, set())
            }
    return {k: list(v.values()) for k, v in out.items()}


# ---- 2. contradictions --------------------------------------------------------------


class PairVerdict(BaseModel):
    pair: int
    verdict: Literal["agree", "contradict", "context_difference", "unrelated"]
    explanation: str = Field(max_length=800)
    confidence: float = Field(ge=0, le=1)


class PairVerdicts(BaseModel):
    pairs: list[PairVerdict]


async def _pair_docs(db, pairs: list[tuple[Claim, Claim]]) -> list[ai.Document]:
    docs = []
    for n, (x, y) in enumerate(pairs, 1):
        docs.append(ai.Document(ref=f"p{n}-x", text=x.text, label=await claim_label(db, x)))
        docs.append(ai.Document(ref=f"p{n}-y", text=y.text, label=await claim_label(db, y)))
    return docs


async def find_contradictions(db, source_id: uuid.UUID, job_id) -> dict[str, int]:
    mine = await source_claims(db, source_id)
    near = await neighbours(db, mine, exclude_source=source_id)
    allc = mine + [o for v in near.values() for o in v]
    vecs = await claim_vectors(db, allc) or {}
    existing = {
        tuple(sorted((a, b), key=str))
        for a, b in (
            await db.execute(select(Contradiction.claim_a_id, Contradiction.claim_b_id))
        ).all()
    }
    pairs: list[tuple[Claim, Claim, float]] = []
    for c in mine:
        cands = []
        for o in near.get(c.id, []):
            if tuple(sorted((c.id, o.id), key=str)) in existing:
                continue
            sim = cosine(vecs[c.id], vecs[o.id]) if c.id in vecs and o.id in vecs else 1.0
            if sim >= CONTRA_MIN_SIMILARITY:
                cands.append((c, o, sim))
        pairs += sorted(cands, key=lambda p: -p[2])[:3]
    pairs = sorted(pairs, key=lambda p: -p[2])[:MAX_PAIRS]
    found = 0
    for i in range(0, len(pairs), BATCH):
        chunk = [(x, y) for x, y, _ in pairs[i : i + BATCH]]
        out = await ai.run_structured(
            db,
            task="review",
            prompt_key="detect_contradictions",
            schema=PairVerdicts,
            instructions=(
                "Indica para cada par si coinciden, se contradicen, difieren por contexto "
                "o no tienen relación."
            ),
            documents=await _pair_docs(db, chunk),
            job_id=job_id,
        )
        for v in out.pairs:
            if 1 <= v.pair <= len(chunk) and v.verdict == "contradict":
                x, y = chunk[v.pair - 1]
                await compare.add_contradiction(db, x.id, y.id, analysis=v.explanation, origin="ai")
                found += 1
        await db.commit()
    return {"pairs_checked": len(pairs), "contradictions": found}


# ---- 3. non-study claims checked against study claims -------------------------------


class CheckVerdict(BaseModel):
    pair: int
    verdict: Literal["supports", "contradicts", "partial", "unrelated"]
    explanation: str = Field(max_length=800)
    confidence: float = Field(ge=0, le=1)


class CheckVerdicts(BaseModel):
    pairs: list[CheckVerdict]


async def check_claims(db, claims: list[Claim], job_id) -> dict[str, int]:
    """Pairs (non-study claim, study claim) about the same concept, the most similar
    first; a pair checked before is not checked again."""
    near = await neighbours(db, claims, include_given=True)
    pool = {c.id: c for c in claims}
    for v in near.values():
        for o in v:
            pool[o.id] = o
    done = {
        (a, b)
        for a, b in (await db.execute(select(ClaimCheck.claim_id, ClaimCheck.study_claim_id))).all()
    }
    raw: list[tuple[Claim, Claim]] = []
    for c in claims:
        for o in near.get(c.id, []):
            if c.publication_id and not o.publication_id:
                raw.append((o, c))
            elif o.publication_id and not c.publication_id:
                raw.append((c, o))
    uniq = {(x.id, y.id): (x, y) for x, y in raw}
    raw = [p for k, p in uniq.items() if k not in done]
    vecs = await claim_vectors(db, list({x.id: x for p in raw for x in p}.values())) or {}
    scored = []
    per_claim: dict[uuid.UUID, int] = defaultdict(int)
    for x, y in raw:
        sim = cosine(vecs[x.id], vecs[y.id]) if x.id in vecs and y.id in vecs else None
        if sim is None or sim >= CHECK_MIN_SIMILARITY:
            scored.append((x, y, sim))
    scored.sort(key=lambda p: -(p[2] or 0))
    pairs = []
    for x, y, sim in scored:
        if per_claim[x.id] < 3:
            per_claim[x.id] += 1
            pairs.append((x, y, sim))
    pairs = pairs[:MAX_PAIRS]
    counts: dict[str, int] = defaultdict(int)
    for i in range(0, len(pairs), BATCH):
        chunk = pairs[i : i + BATCH]
        out = await ai.run_structured(
            db,
            task="review",
            prompt_key="check_against_studies",
            schema=CheckVerdicts,
            instructions=(
                "Indica para cada par si el estudio respalda, contradice, respalda en parte "
                "o no trata la afirmación."
            ),
            documents=await _pair_docs(db, [(x, y) for x, y, _ in chunk]),
            job_id=job_id,
        )
        run_id = await _last_run(db, job_id)
        for v in out.pairs:
            if not 1 <= v.pair <= len(chunk):
                continue
            x, y, sim = chunk[v.pair - 1]
            db.add(
                ClaimCheck(
                    claim_id=x.id,
                    study_claim_id=y.id,
                    verdict=v.verdict,
                    explanation=v.explanation,
                    confidence=v.confidence,
                    similarity=sim,
                    status="proposed",
                    origin="ai",
                    ai_run_id=run_id,
                )
            )
            counts[v.verdict] += 1
        await db.commit()
    return {"pairs_checked": len(pairs), **counts}


# ---- the jobs ------------------------------------------------------------------------


async def _ai_step(results: dict, name: str, coro) -> None:
    try:
        results[name] = await coro
    except ai.BudgetExceeded as exc:
        results[name] = {"skipped": f"presupuesto de IA agotado: {exc}"}
    except ai.ProviderFailure as exc:
        if exc.retryable:
            raise
        results[name] = {"skipped": f"IA no disponible: {exc}"}
    except ai.AIError as exc:
        results[name] = {"skipped": f"IA no disponible: {exc}"}


@handler(ANALYZE_KIND)
async def analyze_source(ctx: JobContext) -> dict[str, Any]:
    try:
        source_id = uuid.UUID(ctx.payload["source_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload sin source_id válido") from None
    results: dict[str, Any] = {}
    async with ctx.sessionmaker() as db:
        if await db.get(Source, source_id) is None:
            raise PermanentJobError("fuente no encontrada")
        links = await suggest_links(db, source_id)
        results["links_proposed"] = len(links)
        classified = 0
        for lk in links:
            step: dict[str, Any] = {}
            await _ai_step(step, "kind", classify_link(db, lk, ctx.job_id))
            if isinstance(step["kind"], dict):  # skipped: the rest stay «relacionado»
                results["links_kind"] = step["kind"]
                break
            classified += 1
        results["links_classified"] = classified
        await _ai_step(results, "contradictions", find_contradictions(db, source_id, ctx.job_id))
        await _ai_step(
            results,
            "study_checks",
            check_claims(db, await source_claims(db, source_id), ctx.job_id),
        )
    return results


@handler(CHECK_ENTITY_KIND)
async def check_entity(ctx: JobContext) -> dict[str, Any]:
    try:
        entity_id = uuid.UUID(ctx.payload["entity_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload sin entity_id válido") from None
    async with ctx.sessionmaker() as db:
        claims = (
            await db.scalars(
                select(Claim)
                .join(ClaimEntity, ClaimEntity.claim_id == Claim.id)
                .where(ClaimEntity.entity_id == entity_id, Claim.review_status != "rejected")
            )
        ).all()
        results: dict[str, Any] = {}
        await _ai_step(results, "study_checks", check_claims(db, list(claims), ctx.job_id))
        if "skipped" in results["study_checks"]:
            raise PermanentJobError(results["study_checks"]["skipped"])
        return results
