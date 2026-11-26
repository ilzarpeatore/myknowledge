"""AI workspace: answer a question from the library, with verified citations.

1. Context: fragments retrieved by hybrid search (limited to the scope: one source,
   a course, a discipline, a concept or everything) plus, for the concepts the
   question is about, a "concept sheet" from the database (claims with provenance,
   relationships, courses, contradictions) whose claims bring their own fragments.
2. If nothing relevant is found the answer says so without calling the model.
3. The model answers in parts: source / synthesis / inference / external.
4. Validation: every citation must name a fragment that was sent and its quote must
   be found in it (the stored quote is the original text). A "source" part left
   without a valid citation is downgraded to "inference" and flagged; with no valid
   citation at all the coverage cannot be "sufficient".
"""

import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.ai import service as ai
from kos.config import get_settings
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.knowledge import service as ks
from kos.knowledge.extraction import locate_quote
from kos.library import locate
from kos.models import (
    AIRun,
    Course,
    CourseMaterial,
    Discipline,
    Entity,
    EntityAlias,
    Job,
    JobStatus,
    QueryRecord,
    Source,
    SourceSegment,
    SourceVersion,
)

QUERY_KIND = "ai.query"
MAX_FRAGMENTS = 24
MAX_CONTEXT_CHARS = 60_000
MAX_CONCEPTS = 3

MODE_RULES = {
    "research": (
        'Mode: research. Answer only from the library. Do not use the "external" kind; if '
        "something is not in the fragments, report it in gaps."
    ),
    "application": (
        'Mode: application. The user wants to apply this knowledge. You may add "inference" '
        'parts that apply what the sources say to the user\'s situation, and "external" parts '
        "with general knowledge, always marked as such and never as if the library said it."
    ),
}
EXTERNAL_RULES = {
    "research": "Not allowed in this mode.",
    "application": "Allowed, clearly separated; never present it as coming from the library.",
}


# ---- structured output -------------------------------------------------------------------


class Citation(BaseModel):
    fragment: str = Field(description='ref of a fragment, e.g. "f3"')
    quote: str = Field(description="5-40 words copied word for word from that fragment")


class Part(BaseModel):
    kind: Literal["source", "synthesis", "inference", "external"]
    text: str
    citations: list[Citation]


class AnswerResult(BaseModel):
    coverage: Literal["sufficient", "partial", "insufficient"]
    coverage_note: str
    parts: list[Part]
    gaps: list[str]


# ---- scope and retrieval -------------------------------------------------------------------


async def scope_sources(
    db: AsyncSession, kind: str, scope_id: uuid.UUID | None, scope_key: str | None = None
) -> list[uuid.UUID] | None:
    """Source ids a scope covers (None = the whole library)."""
    if kind == "section" and scope_key:
        from kos.library.sections import section_sources

        return await section_sources(db, scope_key)
    if kind in ("global", "concept") or scope_id is None:
        return None
    if kind == "source":
        return [scope_id]
    if kind == "course":
        course_ids = [scope_id]
    elif kind == "discipline":
        ids, frontier = {scope_id}, [scope_id]
        while frontier:  # the discipline and its sub-areas
            children = (
                await db.scalars(select(Discipline.id).where(Discipline.parent_id.in_(frontier)))
            ).all()
            frontier = [c for c in children if c not in ids]
            ids.update(frontier)
        course_ids = (
            await db.scalars(select(Course.id).where(Course.discipline_id.in_(ids)))
        ).all()
    else:
        raise PermanentJobError(f"ámbito no válido: {kind}")
    return list(
        (
            await db.scalars(
                select(CourseMaterial.source_id)
                .where(CourseMaterial.course_id.in_(course_ids))
                .distinct()
            )
        ).all()
    )


async def concepts_in(db: AsyncSession, text: str) -> list[Entity]:
    """Live concepts whose name or alias appears (as whole words) in ``text``."""
    q = f" {ks.normalize(text)} "
    rows = (
        await db.execute(
            select(Entity, EntityAlias.alias_normalized)
            .join(EntityAlias, EntityAlias.entity_id == Entity.id)
            .where(
                Entity.status.in_(ks.LIVE),
                func.length(EntityAlias.alias_normalized) >= 3,
                func.strpos(q, func.concat(" ", EntityAlias.alias_normalized, " ")) > 0,
            )
            .order_by(func.length(EntityAlias.alias_normalized).desc())
        )
    ).all()
    out: list[Entity] = []
    for e, _alias in rows:
        if e not in out:
            out.append(e)
    return out[:MAX_CONCEPTS]


class Context:
    """Fragments sent to the model, with short refs, plus the concept sheets."""

    def __init__(self):
        self.fragments: dict[str, dict[str, Any]] = {}  # ref -> info
        self._by_segment: dict[uuid.UUID, str] = {}
        self.sheets: list[str] = []
        self.chars = 0

    def add(
        self,
        seg: SourceSegment,
        title: str,
        source_id: uuid.UUID,
        version: SourceVersion,
        url: str | None,
    ) -> str | None:
        if seg.id in self._by_segment:
            return self._by_segment[seg.id]
        if len(self.fragments) >= MAX_FRAGMENTS or self.chars + len(seg.text) > MAX_CONTEXT_CHARS:
            return None
        ref = f"f{len(self.fragments) + 1}"
        self._by_segment[seg.id] = ref
        self.chars += len(seg.text)
        self.fragments[ref] = {
            "segment_id": str(seg.id),
            "text": seg.text,
            "source_id": str(source_id),
            "source_title": title,
            "version_id": str(version.id),
            "mime": version.mime,
            "source_url": url,
            **locate.location(seg),
        }
        return ref


async def _segment_row(db, segment_id):
    return (
        await db.execute(
            select(SourceSegment, Source.title, Source.id, SourceVersion, Source.url)
            .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
            .join(Source, Source.id == SourceVersion.source_id)
            .where(SourceSegment.id == segment_id)
        )
    ).first()


async def concept_sheet(
    db: AsyncSession, e: Entity, ctx: Context, sources: list[uuid.UUID] | None = None
) -> str:
    """Structured facts about a concept. Within a scope, only claims cited from the
    scope's sources are included (and their fragments)."""
    from kos.api.routes.knowledge import get_entity
    from kos.api.routes.learning import entity_courses
    from kos.knowledge import compare

    d = await get_entity(str(e.id), db, None)
    lines = [f"Concepto: {d['canonical_name']} ({d['type_key']})"]
    if d.get("short_definition"):
        lines.append(f"Definición guardada: {d['short_definition']}")
    aliases = [a["alias"] for a in d["aliases"] if a["alias"] != d["canonical_name"]]
    if aliases:
        lines.append("Otros nombres: " + ", ".join(aliases))
    lines.append("Afirmaciones (quién lo dice → fragmento que lo respalda):")
    allowed = {str(x) for x in sources} if sources is not None else None
    for c in d["claims"]:
        if c["review_status"] == "rejected":
            continue
        evidence = [ev for ev in c["evidence"] if allowed is None or ev["source_id"] in allowed]
        if not evidence:
            continue
        refs = []
        for ev in evidence:
            row = await _segment_row(db, uuid.UUID(ev["segment_id"]))
            if row and (ref := ctx.add(row[0], row[1], row[2], row[3], row[4])):
                refs.append(ref)
        p = c["provenance"]
        who = p["label"]
        if p.get("publication"):
            pub = p["publication"]
            who += f": {pub['title']}" + (f" ({pub['year']})" if pub.get("year") else "")
            who += f", calidad metodológica {QUALITY.get(pub['methodological_quality'], '?')}"
        elif p.get("person"):
            who += f": {p['person']}"
        status = {"accepted": "aceptada", "pending": "pendiente de revisar"}.get(
            c["review_status"], ""
        )
        lines.append(
            f"- [{', '.join(refs) or 'sin fragmento en contexto'}] ({who}; {c['claim_type']};"
            f" {status}) {c['text']}"
        )
    rels = [r for r in d["relationships"] if r["status"] != "rejected"]
    if rels:
        lines.append("Relaciones:")
        lines += [
            f"- {d['canonical_name']} {r['label']} {r['other']['name']}"
            f" ({'verificada' if r['status'] == 'verified' else 'propuesta'})"
            for r in rels
        ]
    courses = await entity_courses(e.id, db, None, limit=50)
    if courses:
        lines.append(
            "Cursos donde aparece: "
            + "; ".join(
                c["title"]
                + (
                    f" (lecciones: {', '.join(x['title'] for x in c['lessons'])})"
                    if c["lessons"]
                    else ""
                )
                for c in courses
            )
        )
    comp = await compare.comparison(db, e.id)
    claim_text = {c["id"]: c["text"] for p in comp["perspectives"] for c in p["claims"]}
    for k in comp["contradictions"]:
        lines.append(
            f"Contradicción registrada ({k['status']}): «{claim_text.get(k['claim_a_id'], '?')}»"
            f" frente a «{claim_text.get(k['claim_b_id'], '?')}»"
        )
    return "\n".join(lines)


QUALITY = {"not_assessed": "no evaluada", "low": "baja", "moderate": "moderada", "high": "alta"}


async def build_context(db: AsyncSession, q: QueryRecord) -> tuple[Context, list[Entity]]:
    from kos.api.routes.search import hybrid_hits

    ctx = Context()
    concepts: list[Entity] = []
    if q.scope_kind == "concept" and q.scope_id and (e := await db.get(Entity, q.scope_id)):
        concepts.append(e)
    if q.parent_id and (parent := await db.get(QueryRecord, q.parent_id)) and parent.context:
        for cid in parent.context.get("concepts", []):
            if (e := await db.get(Entity, uuid.UUID(cid))) and e not in concepts:
                concepts.append(e)
    for e in await concepts_in(db, q.question):
        if e not in concepts:
            concepts.append(e)
    concepts = concepts[:MAX_CONCEPTS]
    sources = await scope_sources(db, q.scope_kind, q.scope_id, q.scope_key)
    for e in concepts:
        ctx.sheets.append(await concept_sheet(db, e, ctx, sources))

    if sources == []:
        return ctx, concepts
    search_text = q.question + " " + " ".join(e.canonical_name for e in concepts)
    hits = await hybrid_hits(
        db,
        search_text[:300],
        "hybrid",
        MAX_FRAGMENTS,
        get_settings().semantic_min_score,
        sources,
        any_words=True,
    )
    for h in hits:
        row = await _segment_row(db, h["segment_id"])
        if row:
            ctx.add(row[0], row[1], row[2], row[3], row[4])
    return ctx, concepts


# ---- validation ----------------------------------------------------------------------------


def validate(result: AnswerResult, ctx: Context, mode: str) -> dict[str, Any]:
    parts, dropped = [], 0
    for p in result.parts:
        cites = []
        for c in p.citations:
            frag = ctx.fragments.get(c.fragment.strip())
            found = locate_quote(frag["text"], c.quote) if frag else None
            if not found:
                dropped += 1
                continue
            cites.append(
                {
                    "ref": c.fragment.strip(),
                    "quote": found[1],
                    **{k: v for k, v in frag.items() if k != "text"},
                }
            )
        kind, flags = p.kind, []
        if kind == "source" and not cites:
            kind, flags = "inference", ["sin cita verificable"]
        if kind == "external" and mode == "research":
            flags.append("conocimiento externo en modo investigación")
        parts.append({"kind": kind, "text": p.text.strip(), "citations": cites, "flags": flags})
    cited = any(p["citations"] for p in parts)
    coverage = result.coverage
    if not cited and coverage != "insufficient":
        coverage = "insufficient" if not ctx.fragments else "partial"
    return {
        "coverage": coverage,
        "coverage_note": result.coverage_note.strip(),
        "parts": parts,
        "gaps": [g.strip() for g in result.gaps if g.strip()],
        "citations_dropped": dropped,
        "fragments_sent": len(ctx.fragments),
    }


def no_information(scope_kind: str) -> dict[str, Any]:
    where = "en la biblioteca" if scope_kind == "global" else "en el ámbito elegido"
    return {
        "coverage": "insufficient",
        "coverage_note": f"No hay fragmentos relacionados con la pregunta {where}.",
        "parts": [
            {
                "kind": "synthesis",
                "citations": [],
                "flags": [],
                "text": f"No hay información suficiente {where} para responder. "
                "No se ha consultado a la IA.",
            }
        ],
        "gaps": [],
        "citations_dropped": 0,
        "fragments_sent": 0,
    }


# ---- job -------------------------------------------------------------------------------------


def job(query_id: uuid.UUID) -> Job:
    return Job(
        kind=QUERY_KIND,
        status=JobStatus.pending,
        max_attempts=3,
        payload={"query_id": str(query_id)},
    )


@handler(QUERY_KIND)
async def answer(ctx: JobContext) -> dict[str, Any]:
    try:
        query_id = uuid.UUID(ctx.payload["query_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None
    async with ctx.sessionmaker() as db:
        q = await db.get(QueryRecord, query_id)
        if q is None:
            raise PermanentJobError("consulta no encontrada")
        q.status, q.job_id, q.error = "running", ctx.job_id, None
        await db.commit()
        context, concepts = await build_context(db, q)
        q.context = {
            "concepts": [str(e.id) for e in concepts],
            "fragments": {r: f["segment_id"] for r, f in context.fragments.items()},
        }
        await db.commit()

        if not context.fragments:
            return await _finish(db, q, no_information(q.scope_kind), None)

        docs = [
            ai.Document(
                ref=f"k{i + 1}", label="ficha de concepto (datos de la aplicación)", text=sheet
            )
            for i, sheet in enumerate(context.sheets)
        ]
        docs += [
            ai.Document(ref=ref, label=_label(f), text=f["text"])
            for ref, f in context.fragments.items()
        ]
        if q.parent_id and (parent := await db.get(QueryRecord, q.parent_id)) and parent.answer:
            prev = "\n".join(p["text"] for p in parent.answer.get("parts", []))
            docs.insert(
                0,
                ai.Document(
                    ref="prev",
                    label="pregunta y respuesta anteriores",
                    text=f"Pregunta: {parent.question}\nRespuesta: {prev}"[:6000],
                ),
            )
        try:
            result: AnswerResult = await ai.run_structured(
                db,
                task="query",
                prompt_key="answer_question",
                schema=AnswerResult,
                instructions=f"Pregunta: {q.question}",
                documents=docs,
                job_id=ctx.job_id,
                variables={
                    "mode_rule": MODE_RULES[q.mode],
                    "external_rule": EXTERNAL_RULES[q.mode],
                },
            )
        except ai.ProviderFailure as exc:
            if exc.retryable and not ctx.is_last_attempt:
                raise
            await _fail(db, q, str(exc))
            raise PermanentJobError(str(exc)) from None
        except ai.AIError as exc:
            await _fail(db, q, f"{type(exc).__name__}: {exc}")
            raise PermanentJobError(str(exc)) from None
        run_id = await db.scalar(
            select(AIRun.id)
            .where(AIRun.job_id == ctx.job_id)
            .order_by(AIRun.created_at.desc())
            .limit(1)
        )
        return await _finish(db, q, validate(result, context, q.mode), run_id)


def _label(f: dict[str, Any]) -> str:
    where = locate.clock(f["t_ms"]) if f.get("t_ms") is not None else None
    parts = [
        f["source_title"],
        f"p. {f['page']}" if f.get("page") else None,
        f"diap. {f['slide']}" if f.get("slide") else None,
        where,
        f.get("section"),
    ]
    return " · ".join(re.sub(r"\s+", " ", str(p)) for p in parts if p)


async def _finish(db, q: QueryRecord, answer_: dict[str, Any], run_id) -> dict[str, Any]:
    q.answer, q.ai_run_id, q.status = answer_, run_id, "completed"
    q.finished_at = datetime.now(UTC)
    await db.commit()
    return {"coverage": answer_["coverage"], "parts": len(answer_["parts"])}


async def _fail(db, q: QueryRecord, error: str) -> None:
    q.status, q.error, q.finished_at = "failed", error[:2000], datetime.now(UTC)
    await db.commit()
