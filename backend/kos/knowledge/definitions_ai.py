"""«Generar con IA»: a definition proposal grounded in the library.

The model sees the concept sheet (its claims with who says them and their fragments)
plus fragments found by search for the concept's names. Every citation must name a
fragment that was sent and its quote must be found in it; the others are dropped. A
definition left without any valid citation is marked as general knowledge, never as
coming from the library. The result is a proposal: the user accepts, edits or
discards it.
"""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from kos.ai import query as aq
from kos.ai import service as ai
from kos.config import get_settings
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.knowledge import definitions as defs
from kos.knowledge.extraction import locate_quote
from kos.models import AIRun, Entity, EntityAlias

SEARCH_FRAGMENTS = 12


class CitationOut(BaseModel):
    fragment: str
    quote: str


class DefinitionOut(BaseModel):
    short: str = Field(description="1-2 sentences")
    extended: str | None = None
    basis: Literal["library", "partial", "general"]
    citations: list[CitationOut] = Field(default_factory=list)
    note: str | None = None


async def build_context(db, entity: Entity) -> aq.Context:
    from kos.api.routes.search import hybrid_hits

    ctx = aq.Context()
    sheet = await aq.concept_sheet(db, entity, ctx)
    # the saved definition is what the user wants to improve: not a source
    sheet = "\n".join(
        line for line in sheet.splitlines() if not line.startswith("Definición guardada:")
    )
    ctx.sheets.append(sheet)
    names = (
        await db.scalars(select(EntityAlias.alias).where(EntityAlias.entity_id == entity.id))
    ).all()
    query = " ".join(dict.fromkeys([entity.canonical_name, *names]))[:300]
    hits = await hybrid_hits(
        db,
        query,
        "hybrid",
        SEARCH_FRAGMENTS,
        get_settings().semantic_min_score,
        None,
        any_words=True,
    )
    for h in hits:
        row = await aq._segment_row(db, h["segment_id"])
        if row:
            ctx.add(row[0], row[1], row[2], row[3], row[4])
    return ctx


def validate(result: DefinitionOut, ctx: aq.Context) -> tuple[str, list[dict[str, Any]], int]:
    """Keep the citations whose quote is in its fragment; returns (basis, citations,
    dropped)."""
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for c in result.citations:
        frag = ctx.fragments.get(c.fragment.strip())
        found = locate_quote(frag["text"], c.quote) if frag else None
        if not found or frag["segment_id"] in seen:
            dropped += 1 if not found else 0
            continue
        seen.add(frag["segment_id"])
        start, exact = found
        kept.append(
            {
                "segment_id": frag["segment_id"],
                "quote": exact,
                "char_start": start,
                "source_id": frag["source_id"],
                "source_title": frag["source_title"],
                "version_id": frag["version_id"],
                "mime": frag["mime"],
                "label": aq._label(frag),
                **{k: frag.get(k) for k in ("page", "slide", "t_ms") if frag.get(k) is not None},
            }
        )
    basis = result.basis
    if not kept:
        basis = "general"
    return basis, kept, dropped


@handler(defs.DEFINE_KIND)
async def define(ctx_job: JobContext) -> dict[str, Any]:
    try:
        entity_id = uuid.UUID(ctx_job.payload["entity_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None
    async with ctx_job.sessionmaker() as db:
        entity = await db.get(Entity, entity_id)
        if entity is None or entity.status == "merged":
            raise PermanentJobError("concepto no encontrado")
        ctx = await build_context(db, entity)
        docs = [
            ai.Document(ref=f"k{i + 1}", label="ficha de concepto", text=s)
            for i, s in enumerate(ctx.sheets)
        ] + [
            ai.Document(ref=r, label=aq._label(f), text=f["text"]) for r, f in ctx.fragments.items()
        ]
        try:
            result: DefinitionOut = await ai.run_structured(
                db,
                task="define",
                job_id=ctx_job.job_id,
                prompt_key="define_concept",
                schema=DefinitionOut,
                instructions=f"Define «{entity.canonical_name}».",
                documents=docs,
                variables={"concept": entity.canonical_name},
            )
        except ai.ProviderFailure as exc:
            if exc.retryable and not ctx_job.is_last_attempt:
                raise
            raise PermanentJobError(str(exc)) from None
        except ai.AIError as exc:
            raise PermanentJobError(f"{type(exc).__name__}: {exc}") from None
        run_id = await db.scalar(
            select(AIRun.id)
            .where(AIRun.job_id == ctx_job.job_id)
            .order_by(AIRun.created_at.desc())
            .limit(1)
        )
        basis, citations, dropped = validate(result, ctx)
        note = (result.note or "").strip()
        if basis == "general" and result.basis != "general":
            note = (note + " " if note else "") + (
                "Ninguna cita se encontró literalmente en tus fuentes: trátala como"
                " conocimiento general."
            )
        try:
            d = await defs.propose(
                db,
                entity,
                short=result.short,
                extended=result.extended,
                origin="ai",
                basis=basis,
                citations=citations,
                note=note,
                ai_run_id=run_id,
            )
        except defs.DefinitionError as exc:
            raise PermanentJobError(str(exc)) from None
        await db.commit()
        return {
            "definition_id": str(d.id),
            "basis": basis,
            "citations": len(citations),
            "dropped_citations": dropped,
            "fragments_sent": len(ctx.fragments),
        }
