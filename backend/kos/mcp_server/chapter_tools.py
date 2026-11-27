"""MCP tools for the chapters of a book and their summaries.

Agents read a book by chapters: the list (with page ranges and which summaries exist),
a chapter's summary as structured points whose citations carry fragment ids and pages
(readable in full with ``get_fragments``), and the fragment ids of a chapter to read
its text. Asking for a new summary spends money, so it needs the «propose» scope.
"""

import uuid
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from sqlalchemy import select

from kos.library import chapter_summaries as sums
from kos.library import chapters as chs
from kos.models import AuditLog, ChapterSummary, Source, SourceChapter, SourceVersion

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
REQUEST = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)
FRAGMENTS_PAGE = 60


def _id(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ToolError(f"{what} no válido: {value}") from None


def register(mcp, sm, require, actor) -> None:
    @mcp.tool(annotations=READ)
    async def list_chapters(source_id: str) -> dict[str, Any]:
        """Chapters and points of a book, in reading order: pages, whether they are
        content (not cover, index...) and which summary levels exist."""
        async with sm() as db:
            source = await db.get(Source, _id(source_id, "source_id"))
            if source is None:
                raise ToolError("fuente no encontrada")
            version = await db.scalar(
                select(SourceVersion)
                .where(SourceVersion.source_id == source.id)
                .order_by(SourceVersion.created_at.desc())
                .limit(1)
            )
            if version is None:
                return {"source": source.title, "chapters": []}
            rows = await chs.chapters_of(db, version.id)
            have = (
                await db.execute(
                    select(ChapterSummary.chapter_id, ChapterSummary.level).where(
                        ChapterSummary.chapter_id.in_([r.id for r in rows])
                    )
                )
            ).all()
            levels: dict[uuid.UUID, set[str]] = {}
            for cid, level in have:
                levels.setdefault(cid, set()).add(level)
            return {
                "source": source.title,
                "page_count": version.page_count,
                "chapters": [
                    {
                        "id": str(r.id),
                        "parent_id": str(r.parent_id) if r.parent_id else None,
                        "title": r.title,
                        "pages": [r.page_start, r.page_end],
                        "content": r.is_content,
                        "summaries": sorted(levels.get(r.id, set())),
                    }
                    for r in rows
                ],
            }

    @mcp.tool(annotations=READ)
    async def get_chapter(
        chapter_id: str, level: str = "study", fragments_offset: int = 0
    ) -> dict[str, Any]:
        """A chapter's summary at ``level`` (essential|study|detailed): Markdown and
        points citing fragment_id and page; plus its fragment ids (paginated) to read
        with ``get_fragments``."""
        if level not in sums.LEVELS:
            raise ToolError("level: essential | study | detailed")
        async with sm() as db:
            ch = await db.get(SourceChapter, _id(chapter_id, "chapter_id"))
            if ch is None:
                raise ToolError("capítulo no encontrado")
            version = await db.get(SourceVersion, ch.source_version_id)
            source = await db.get(Source, version.source_id)
            current = await sums.current(db, ch.id)
            segs = await chs.segments_of(db, ch)
            start = max(0, fragments_offset)
            page = segs[start : start + FRAGMENTS_PAGE]
            out: dict[str, Any] = {
                "id": str(ch.id),
                "title": ch.title,
                "source": {"id": str(source.id), "title": source.title},
                "pages": [ch.page_start, ch.page_end],
                "summaries_available": sorted(current),
                "generating": sorted(await sums.in_flight(db, ch.id)),
                "fragments": [{"id": str(s.id), "page": s.loc_page_start} for s in page],
                "fragments_total": len(segs),
            }
            if start + FRAGMENTS_PAGE < len(segs):
                out["next_fragments_offset"] = start + FRAGMENTS_PAGE
            s = current.get(level)
            if s is not None:
                out["summary"] = {
                    "level": s.level,
                    "created_at": s.created_at.isoformat(),
                    "words": s.words,
                    "uncited_points": s.uncited_points,
                    "markdown": s.markdown,
                    "content": s.content,
                }
            return out

    @mcp.tool(annotations=REQUEST)
    async def summarize_chapter(chapter_id: str, level: str = "study") -> dict[str, Any]:
        """Queue a new summary (essential|study|detailed); it costs money, so only when
        the user wants it: tell them the estimate. Read it later with ``get_chapter``."""
        require("propose")
        if level not in sums.LEVELS:
            raise ToolError("level: essential | study | detailed")
        async with sm() as db:
            ch = await db.get(SourceChapter, _id(chapter_id, "chapter_id"))
            if ch is None:
                raise ToolError("capítulo no encontrado")
            if level in await sums.in_flight(db, ch.id):
                return {"queued": False, "reason": "ya se está generando ese resumen"}
            est = await sums.estimate(db, ch, level)
            if est["tokens"] == 0 or est["too_long"]:
                raise ToolError(
                    "el capítulo no tiene texto"
                    if est["tokens"] == 0
                    else "el capítulo es demasiado largo: hay que dividirlo en puntos"
                )
            job = sums.summarize_job(ch.id, level, origin="mcp")
            db.add(job)
            db.add(
                AuditLog(
                    actor=actor(),
                    action="mcp.summarize_chapter",
                    subject_table="source_chapters",
                    subject_id=str(ch.id),
                    after={"level": level, "usd_estimate": est["usd"]},
                )
            )
            await db.commit()
            return {"queued": True, "job_id": str(job.id), "estimate": est}
