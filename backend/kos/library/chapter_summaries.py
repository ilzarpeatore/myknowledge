"""Summary of one chapter, generated only when asked (button or MCP).

The model receives the chapter's fragments in reading order and returns sections of
points following the chapter's own structure; every citation must name a fragment that
was sent and its quote must be found in it, or it is dropped. A point left without any
valid citation is kept but marked «sin cita», so it can be checked. Regenerating keeps
the earlier summaries; the newest per level is the current one.

The length is proportional to the chapter (a 2-page introduction and a 60-page chapter
cannot share a word count), in three levels: essential, study and detailed.
"""

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.ai import service as ai
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.knowledge.extraction import locate_quote
from kos.library import chapters as chs
from kos.models import (
    AIModel,
    AIRun,
    AITaskRoute,
    AuditLog,
    ChapterSummary,
    Job,
    JobStatus,
    Source,
    SourceChapter,
    SourceSegment,
    SourceVersion,
)

SUMMARIZE_KIND = "chapter.summarize"
TASK = "summarize"
PART_CHARS = 120_000  # ~35k tokens: longer chapters are summarised by parts, more faithful
MAX_CHAPTER_CHARS = 2_000_000
WORDS_PER_TOKEN = Decimal("0.75")

LEVELS: dict[str, dict[str, Any]] = {
    "essential": {
        "name": "Esencial",
        "rule": "Only the 5 to 10 ideas someone must remember; one section is enough.",
        "ratio": Decimal("0.03"),
        "min": 120,
        "max": 300,
    },
    "study": {
        "name": "Estudio",
        "rule": (
            "Enough to study without rereading: every heading of the chapter with its "
            "definitions, classifications, figures and key data."
        ),
        "ratio": Decimal("0.09"),
        "min": 250,
        "max": 1500,
    },
    "detailed": {
        "name": "Detallado",
        "rule": (
            "Point by point, close to the text: it should replace reading the chapter. "
            "Keep every relevant detail, list and figure."
        ),
        "ratio": Decimal("0.22"),
        "min": 500,
        "max": 4000,
    },
}


class CitationOut(BaseModel):
    fragment: str
    quote: str


class PointOut(BaseModel):
    text: str
    citations: list[CitationOut] = Field(default_factory=list)


class SectionOut(BaseModel):
    heading: str
    points: list[PointOut]


class TermOut(BaseModel):
    term: str
    definition: str
    citations: list[CitationOut] = Field(default_factory=list)


class SummaryOut(BaseModel):
    overview: str
    sections: list[SectionOut]
    key_terms: list[TermOut] = Field(default_factory=list)


def target_words(tokens: int, level: str) -> int:
    spec = LEVELS[level]
    words = Decimal(tokens) * WORDS_PER_TOKEN * spec["ratio"]
    return int(max(spec["min"], min(spec["max"], words)))


async def estimate(db: AsyncSession, ch: SourceChapter, level: str) -> dict[str, Any]:
    """Expected cost before generating: the chapter's tokens in, the target out."""
    tokens = await chs.tokens_of(db, ch)
    route = await db.get(AITaskRoute, TASK)
    model = await db.get(AIModel, route.model_name) if route else None
    words = target_words(tokens, level)
    out_tokens = int(words * 2.5)  # citations and JSON add about as much as the text
    usd = None
    if model is not None:
        usd = float(
            (
                Decimal(tokens + 1500) * model.input_usd_per_mtok
                + Decimal(out_tokens) * model.output_usd_per_mtok
            )
            / ai.MTOK
        )
    return {
        "level": level,
        "tokens": tokens,
        "target_words": words,
        "model": model.name if model else None,
        "usd": round(usd, 4) if usd is not None else None,
        "too_long": tokens * 4 > MAX_CHAPTER_CHARS,
        "by_parts": tokens * 4 > PART_CHARS,
    }


def summarize_job(chapter_id: uuid.UUID, level: str, origin: str = "ai") -> Job:
    return Job(
        kind=SUMMARIZE_KIND,
        status=JobStatus.pending,
        max_attempts=3,
        payload={"chapter_id": str(chapter_id), "level": level, "origin": origin},
    )


async def in_flight(db: AsyncSession, chapter_id: uuid.UUID) -> dict[str, str]:
    """level -> job id of the summaries being generated for a chapter."""
    rows = (
        await db.scalars(
            select(Job).where(
                Job.kind == SUMMARIZE_KIND,
                Job.payload["chapter_id"].astext == str(chapter_id),
                Job.status.in_([JobStatus.pending, JobStatus.processing]),
            )
        )
    ).all()
    return {j.payload.get("level", "study"): str(j.id) for j in rows}


async def last_failures(db: AsyncSession, chapter_id: uuid.UUID) -> dict[str, str]:
    """level -> why the newest generation failed, when it is newer than the summary."""
    jobs = (
        await db.scalars(
            select(Job)
            .where(Job.kind == SUMMARIZE_KIND, Job.payload["chapter_id"].astext == str(chapter_id))
            .order_by(Job.created_at.desc())
        )
    ).all()
    newest: dict[str, Job] = {}
    for j in jobs:
        newest.setdefault(j.payload.get("level", "study"), j)
    out = {}
    for level, j in newest.items():
        if j.status == JobStatus.failed:
            out[level] = j.last_error or "la generación falló"
    return out


async def current(db: AsyncSession, chapter_id: uuid.UUID) -> dict[str, ChapterSummary]:
    rows = (
        await db.scalars(
            select(ChapterSummary)
            .where(ChapterSummary.chapter_id == chapter_id)
            .order_by(ChapterSummary.created_at.desc())
        )
    ).all()
    out: dict[str, ChapterSummary] = {}
    for r in rows:
        out.setdefault(r.level, r)
    return out


def _cite(
    citations: list[CitationOut], frags: dict[str, SourceSegment], seen: set
) -> list[dict[str, Any]]:
    kept = []
    for c in citations:
        seg = frags.get(c.fragment.strip())
        found = locate_quote(seg.text, c.quote) if seg is not None else None
        if not found or (seg.id, found[0]) in seen:
            continue
        seen.add((seg.id, found[0]))
        kept.append(
            {
                "fragment_id": str(seg.id),
                "page": seg.loc_page_start,
                "quote": found[1],
                "char_start": found[0],
            }
        )
    return kept


def validate(result: SummaryOut, frags: dict[str, SourceSegment]) -> dict[str, Any]:
    """The stored content: only verified citations; counts of kept and uncited."""
    sections, cited, uncited, dropped = [], 0, 0, 0
    for s in result.sections:
        points = []
        for p in s.points:
            text = p.text.strip()
            if not text:
                continue
            cites = _cite(p.citations, frags, set())
            dropped += len(p.citations) - len(cites)
            cited += len(cites)
            uncited += 0 if cites else 1
            points.append({"text": text, "citations": cites})
        if points:
            sections.append({"heading": s.heading.strip(), "points": points})
    terms = []
    for t in result.key_terms:
        if t.term.strip() and t.definition.strip():
            cites = _cite(t.citations, frags, set())
            cited += len(cites)
            terms.append(
                {"term": t.term.strip(), "definition": t.definition.strip(), "citations": cites}
            )
    return {
        "overview": result.overview.strip(),
        "sections": sections,
        "key_terms": terms,
        "_counts": {"citations": cited, "uncited": uncited, "dropped": dropped},
    }


def _pages(cites: list[dict[str, Any]]) -> str:
    pages = sorted({c["page"] for c in cites if c.get("page")})
    return f" [p. {', '.join(map(str, pages))}]" if pages else " [sin cita]"


def to_markdown(title: str, source_title: str, level: str, content: dict[str, Any]) -> str:
    out = [f"# {title}", f"*{source_title}* — resumen {LEVELS[level]['name'].lower()}", ""]
    if content.get("overview"):
        out += [content["overview"], ""]
    for s in content.get("sections", []):
        if s.get("heading"):
            out += [f"## {s['heading']}", ""]
        out += [f"- {p['text']}{_pages(p['citations'])}" for p in s["points"]]
        out.append("")
    if content.get("key_terms"):
        out += ["## Términos clave", ""]
        out += [
            f"- **{t['term']}**: {t['definition']}{_pages(t['citations'])}"
            for t in content["key_terms"]
        ]
        out.append("")
    return "\n".join(out).strip() + "\n"


def word_count(content: dict[str, Any]) -> int:
    parts = [content.get("overview", "")]
    for s in content.get("sections", []):
        parts += [p["text"] for p in s["points"]]
    parts += [f"{t['term']} {t['definition']}" for t in content.get("key_terms", [])]
    return len(re.findall(r"\w+", " ".join(parts)))


@dataclass
class Part:
    """A piece of a long chapter summarised on its own: one of its points (whose own
    summary it also becomes) or a run of pages."""

    title: str | None
    chapter: SourceChapter | None
    segments: list[SourceSegment]

    @property
    def chars(self) -> int:
        return sum(len(s.text) for s in self.segments)


def _by_pages(segs: list[SourceSegment]) -> list[Part]:
    parts: list[list[SourceSegment]] = []
    cur: list[SourceSegment] = []
    size = 0
    for s in segs:
        new_page = not cur or s.loc_page_start != cur[-1].loc_page_start
        if cur and size + len(s.text) > PART_CHARS and new_page:
            parts.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += len(s.text)
    if cur:
        parts.append(cur)
    return [Part(f"p. {p[0].loc_page_start}–{p[-1].loc_page_start}", None, p) for p in parts]


async def plan_parts(db: AsyncSession, ch: SourceChapter, segs: list[SourceSegment]) -> list[Part]:
    """One part when the chapter fits; otherwise its points (plus what lies between
    them) when each fits, or runs of pages."""
    if sum(len(s.text) for s in segs) <= PART_CHARS:
        return [Part(None, None, segs)]
    points = [r for r in await chs.chapters_of(db, ch.source_version_id) if r.parent_id == ch.id]
    if points:
        taken: set[uuid.UUID] = set()
        parts: list[Part] = []
        for p in points:
            mine = [
                s
                for s in segs
                if s.id not in taken and p.page_start <= (s.loc_page_start or 0) <= p.page_end
            ]
            taken |= {s.id for s in mine}
            if mine:
                parts.append(Part(p.title, p, mine))
        rest = [s for s in segs if s.id not in taken]
        if rest:
            parts.append(Part(None, None, rest))
        parts.sort(key=lambda x: x.segments[0].ordinal)
        if all(x.chars <= PART_CHARS * 1.5 for x in parts):
            return parts
    return _by_pages(segs)


async def _call(ctx: JobContext, db: AsyncSession, **kw: Any) -> Any:
    try:
        return await ai.run_structured(db, task=TASK, job_id=ctx.job_id, **kw)
    except ai.ProviderFailure as exc:
        if exc.retryable and not ctx.is_last_attempt:
            raise
        raise PermanentJobError(str(exc)) from None
    except ai.AIError as exc:
        raise PermanentJobError(f"{type(exc).__name__}: {exc}") from None


async def _summarize_part(
    ctx: JobContext,
    db: AsyncSession,
    *,
    title: str,
    source: str,
    level: str,
    part: Part,
    words: int,
) -> tuple[dict[str, Any], dict[str, int]]:
    frags = {f"f{i + 1}": s for i, s in enumerate(part.segments)}
    docs = [
        ai.Document(ref=r, label=f"p. {s.loc_page_start}" if s.loc_page_start else "", text=s.text)
        for r, s in frags.items()
    ]
    first, last = part.segments[0].loc_page_start, part.segments[-1].loc_page_start
    result: SummaryOut = await _call(
        ctx,
        db,
        prompt_key="summarize_chapter",
        schema=SummaryOut,
        instructions=f"Resume «{title}» (páginas {first}-{last}).",
        documents=docs,
        variables={
            "chapter": title,
            "source": source,
            "level_name": LEVELS[level]["name"],
            "level_rule": LEVELS[level]["rule"],
            "target_words": str(words),
        },
    )
    content = validate(result, frags)
    return content, content.pop("_counts")


class OverviewOut(BaseModel):
    overview: str


def _merge(parts: list[tuple[Part, dict[str, Any]]]) -> dict[str, Any]:
    sections, terms, seen = [], [], set()
    for part, content in parts:
        for sec in content["sections"]:
            heading = sec["heading"]
            if part.title and part.title.lower() not in heading.lower():
                heading = f"{part.title} · {heading}" if heading else part.title
            sections.append({**sec, "heading": heading})
        for t in content["key_terms"]:
            if t["term"].lower() not in seen and len(terms) < 25:
                seen.add(t["term"].lower())
                terms.append(t)
    return {"sections": sections, "key_terms": terms}


def _row(
    ch: SourceChapter, source: str, level: str, content: dict, counts: dict, origin: str, run_id
) -> ChapterSummary:
    content["chapter"] = {"title": ch.title, "page_start": ch.page_start, "page_end": ch.page_end}
    return ChapterSummary(
        chapter_id=ch.id,
        level=level,
        content=content,
        markdown=to_markdown(ch.title, source, level, content),
        words=word_count(content),
        citations=counts["citations"],
        uncited_points=counts["uncited"],
        origin=origin,
        ai_run_id=run_id,
    )


@handler(SUMMARIZE_KIND)
async def summarize(ctx: JobContext) -> dict[str, Any]:
    try:
        chapter_id = uuid.UUID(ctx.payload["chapter_id"])
        level = ctx.payload.get("level", "study")
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None
    if level not in LEVELS:
        raise PermanentJobError("nivel desconocido")
    origin = ctx.payload.get("origin", "ai")
    async with ctx.sessionmaker() as db:
        ch = await db.get(SourceChapter, chapter_id)
        if ch is None:
            raise PermanentJobError("capítulo no encontrado (¿se volvieron a detectar?)")
        version = await db.get(SourceVersion, ch.source_version_id)
        source = (await db.get(Source, version.source_id)).title
        segs = await chs.segments_of(db, ch)
        if not segs:
            raise PermanentJobError("el capítulo no tiene texto en esas páginas")
        if sum(len(s.text) for s in segs) > MAX_CHAPTER_CHARS:
            raise PermanentJobError("el capítulo es demasiado largo: divídelo en apartados")
        total_tokens = max(1, sum(s.token_count for s in segs))
        target = target_words(total_tokens, level)
        parts = await plan_parts(db, ch, segs)
        done: list[tuple[Part, dict[str, Any]]] = []
        counts = {"citations": 0, "uncited": 0, "dropped": 0}
        for i, part in enumerate(parts):
            share = sum(s.token_count for s in part.segments) / total_tokens
            words = target if len(parts) == 1 else max(80, round(target * share))
            title = ch.title if part.title is None else f"{ch.title} — {part.title}"
            content, c = await _summarize_part(
                ctx, db, title=title, source=source, level=level, part=part, words=words
            )
            for k in counts:
                counts[k] += c[k]
            if content["sections"]:
                done.append((part, content))
            if len(parts) > 1:
                await ctx.progress((i + 1) / (len(parts) + 1))
        if not done:
            raise PermanentJobError("la IA no devolvió ningún punto utilizable")
        if len(parts) == 1:
            content = done[0][1]
        else:
            content = _merge(done)
            overview: OverviewOut = await _call(
                ctx,
                db,
                prompt_key="summarize_overview",
                schema=OverviewOut,
                instructions=f"Escribe la visión de conjunto de «{ch.title}».",
                documents=[
                    ai.Document(
                        ref=f"part{n + 1}",
                        label=p.title or "",
                        text=to_markdown(p.title or ch.title, source, level, c),
                    )
                    for n, (p, c) in enumerate(done)
                ],
                variables={"chapter": ch.title, "source": source},
            )
            content["overview"] = overview.overview.strip()
        run_id = await db.scalar(
            select(AIRun.id)
            .where(AIRun.job_id == ctx.job_id)
            .order_by(AIRun.created_at.desc())
            .limit(1)
        )
        row = _row(ch, source, level, content, counts, origin, run_id)
        db.add(row)
        # a long chapter summarised by points leaves each point summarised too
        for part, c in done:
            if part.chapter is not None and len(parts) > 1:
                n_cites = sum(len(p["citations"]) for s in c["sections"] for p in s["points"])
                n_uncited = sum(1 for s in c["sections"] for p in s["points"] if not p["citations"])
                pc = {"citations": n_cites, "uncited": n_uncited}
                db.add(_row(part.chapter, source, level, dict(c), pc, origin, run_id))
        db.add(
            AuditLog(
                actor="worker",
                action="chapter.summarized",
                subject_table="source_chapters",
                subject_id=str(ch.id),
                after={"level": level, "parts": len(parts), **counts},
            )
        )
        await db.commit()
        return {
            "summary_id": str(row.id),
            "level": level,
            "words": row.words,
            "parts": len(parts),
            **counts,
        }
