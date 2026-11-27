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
MAX_CHAPTER_CHARS = 400_000  # ~110k tokens in one call; longer chapters: split in points
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
            .where(
                Job.kind == SUMMARIZE_KIND, Job.payload["chapter_id"].astext == str(chapter_id)
            )
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


@handler(SUMMARIZE_KIND)
async def summarize(ctx: JobContext) -> dict[str, Any]:
    try:
        chapter_id = uuid.UUID(ctx.payload["chapter_id"])
        level = ctx.payload.get("level", "study")
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None
    if level not in LEVELS:
        raise PermanentJobError("nivel desconocido")
    async with ctx.sessionmaker() as db:
        ch = await db.get(SourceChapter, chapter_id)
        if ch is None:
            raise PermanentJobError("capítulo no encontrado (¿se volvieron a detectar?)")
        version = await db.get(SourceVersion, ch.source_version_id)
        source = await db.get(Source, version.source_id)
        segs = await chs.segments_of(db, ch)
        if not segs:
            raise PermanentJobError("el capítulo no tiene texto en esas páginas")
        if sum(len(s.text) for s in segs) > MAX_CHAPTER_CHARS:
            raise PermanentJobError(
                "el capítulo es demasiado largo para resumirlo de una vez: divídelo en puntos"
            )
        frags = {f"f{i + 1}": s for i, s in enumerate(segs)}
        docs = [
            ai.Document(
                ref=ref,
                label=f"p. {s.loc_page_start}" if s.loc_page_start else "",
                text=s.text,
            )
            for ref, s in frags.items()
        ]
        tokens = sum(s.token_count for s in segs)
        spec = LEVELS[level]
        try:
            result: SummaryOut = await ai.run_structured(
                db,
                task=TASK,
                job_id=ctx.job_id,
                prompt_key="summarize_chapter",
                schema=SummaryOut,
                instructions=f"Resume «{ch.title}» (páginas {ch.page_start}-{ch.page_end}).",
                documents=docs,
                variables={
                    "chapter": ch.title,
                    "source": source.title,
                    "level_name": spec["name"],
                    "level_rule": spec["rule"],
                    "target_words": str(target_words(tokens, level)),
                },
            )
        except ai.ProviderFailure as exc:
            if exc.retryable and not ctx.is_last_attempt:
                raise
            raise PermanentJobError(str(exc)) from None
        except ai.AIError as exc:
            raise PermanentJobError(f"{type(exc).__name__}: {exc}") from None
        run_id = await db.scalar(
            select(AIRun.id)
            .where(AIRun.job_id == ctx.job_id)
            .order_by(AIRun.created_at.desc())
            .limit(1)
        )
        content = validate(result, frags)
        counts = content.pop("_counts")
        if not content["sections"]:
            raise PermanentJobError("la IA no devolvió ningún punto utilizable")
        content["chapter"] = {
            "title": ch.title,
            "page_start": ch.page_start,
            "page_end": ch.page_end,
        }
        row = ChapterSummary(
            chapter_id=ch.id,
            level=level,
            content=content,
            markdown=to_markdown(ch.title, source.title, level, content),
            words=word_count(content),
            citations=counts["citations"],
            uncited_points=counts["uncited"],
            origin=ctx.payload.get("origin", "ai"),
            ai_run_id=run_id,
        )
        db.add(row)
        db.add(
            AuditLog(
                actor="worker",
                action="chapter.summarized",
                subject_table="source_chapters",
                subject_id=str(ch.id),
                after={"level": level, **counts},
            )
        )
        await db.commit()
        return {"summary_id": str(row.id), "level": level, "words": row.words, **counts}
