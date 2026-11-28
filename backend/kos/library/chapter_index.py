"""Chapters of a PDF without bookmarks: chosen by AI among the headings of the text.

The model never invents a page. It receives the headings found while extracting
(each with an id and its page) and, when there is one, the printed index; it picks
which headings start a chapter (level 1) or a main point (level 2) and may clean up
their titles (OCR errors). The page always comes from the heading it picked, so a
chapter can only start where the text really has that heading. Haiku: about 0.2 cents
per book (measured on books of 33, 68 and 260 pages).
"""

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.ai import service as ai
from kos.library.chapters import Detected, is_content_title
from kos.models import SourcePage

TASK = "extract"
MIN_PAGES = 20  # shorter documents rarely have chapters worth it
MAX_CANDIDATES = 2500
INDEX_PAGES_SCAN = 25
HEADING = re.compile(r"^#{1,4}\s+(.+?)\s*$")
INDEX_TITLE = re.compile(
    r"^\W*(#+\s*)?(\W\s*)?(índice|indice|contenidos?|sumario|tabla de contenidos?|"
    r"table of contents|contents)\b",
    re.IGNORECASE | re.MULTILINE,
)


class PickOut(BaseModel):
    heading: str = Field(description="id of the heading, e.g. h12")
    level: Literal[1, 2]
    title: str


class ChaptersOut(BaseModel):
    chapters: list[PickOut]


def _short_lines(text: str) -> list[str]:
    """Headings of a text without Markdown marks: short lines that do not read like a
    sentence (no final period), at most two per page."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if 3 <= len(line) <= 90 and re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{3}", line):
            if not line.endswith((".", ",", ";", ":")):
                out.append(line)
        if len(out) == 2:
            break
    return out


def candidates(pages: list[tuple[int, str]]) -> list[tuple[str, int, str]]:
    """(ref, page, text) of every heading, in reading order."""
    marked = any(HEADING.match(line) for _, t in pages for line in t.splitlines())
    out: list[tuple[str, int, str]] = []
    for page_no, text in pages:
        if marked:
            heads = [m.group(1) for line in text.splitlines() if (m := HEADING.match(line))]
        else:
            heads = _short_lines(text)
        for h in heads:
            h = re.sub(r"\s+", " ", h).strip(" ·|-")
            if len(h) < 3 or len(h) > 150:
                continue
            if out and out[-1][1] == page_no and out[-1][2] == h:
                continue
            out.append((f"h{len(out) + 1}", page_no, h))
            if len(out) >= MAX_CANDIDATES:
                return out
    return out


def index_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [(n, t[:6000]) for n, t in pages[:INDEX_PAGES_SCAN] if INDEX_TITLE.search(t)][:4]


def picks_to_items(result: ChaptersOut, cands: list[tuple[str, int, str]]) -> list[Detected]:
    """Validated: known heading ids, pages never going back, a point only after a
    chapter, and at least two chapters."""
    by_ref = {r: (p, t) for r, p, t in cands}
    order = {r: i for i, (r, _, _) in enumerate(cands)}
    items: list[Detected] = []
    last = -1
    for pick in result.chapters:
        ref = pick.heading.strip()
        if ref not in by_ref or order[ref] <= last:
            continue
        last = order[ref]
        page, text = by_ref[ref]
        title = re.sub(r"\s+", " ", pick.title).strip() or text
        level = pick.level if items else 1
        items.append(Detected(level, title[:500], page, is_content=is_content_title(title)))
    if sum(1 for i in items if i.level == 1) < 2:
        return []
    return items


async def ai_available(db: AsyncSession) -> bool:
    from kos.ai import credentials

    return credentials.from_environment() or bool(await credentials.stored_key(db))


async def detect(
    db: AsyncSession, version_id: uuid.UUID, page_count: int, job_id: uuid.UUID | None
) -> tuple[list[Detected], str | None]:
    """(chapters, reason when there are none)."""
    if page_count < MIN_PAGES:
        return [], f"documento corto (menos de {MIN_PAGES} páginas)"
    rows = (
        await db.execute(
            select(SourcePage.page_no, SourcePage.text)
            .where(SourcePage.source_version_id == version_id)
            .order_by(SourcePage.page_no)
        )
    ).all()
    pages = [(r[0], r[1]) for r in rows]
    cands = candidates(pages)
    if len(cands) < 3:
        return [], "no se encuentran títulos en el texto"
    if not await ai_available(db):
        return [], "no hay clave de IA para leer el índice"
    docs = [
        ai.Document(
            ref="headings",
            label="títulos del texto (id · página · título)",
            text="\n".join(f"{r} · p. {p} · {t}" for r, p, t in cands),
        )
    ]
    for n, t in index_pages(pages):
        docs.append(ai.Document(ref=f"index-p{n}", label=f"página {n}: índice impreso", text=t))
    try:
        result: ChaptersOut = await ai.run_structured(
            db,
            task=TASK,
            job_id=job_id,
            prompt_key="detect_chapters",
            schema=ChaptersOut,
            instructions="Elige los títulos que empiezan cada capítulo y sus apartados.",
            documents=docs,
            variables={"pages": str(page_count)},
        )
    except ai.AIError as exc:
        return [], f"la IA no pudo leer el índice: {exc}"[:300]
    items = picks_to_items(result, cands)
    return items, None if items else "la IA no encontró capítulos claros"
