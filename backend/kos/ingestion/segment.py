"""Split extracted units into citable segments.

Rules:
- A segment never crosses a unit boundary (a PDF page), so every segment has
  one exact page.
- Segment text is always an exact slice ``unit.text[char_start:char_end]``;
  quotes can therefore be verified against the original text.
- Paragraphs are kept together up to ``target_chars``; a paragraph longer than
  ``max_chars`` is cut at sentence or word boundaries.
- In Markdown, headings start a new segment and give the section breadcrumb.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from kos.ingestion.extract import Extracted, Unit

TARGET_CHARS = 1500
MAX_CHARS = 3000

_PARA_BREAK = re.compile(r"\n[ \t]*\n")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_SENTENCE_END = re.compile(r"[.!?…][\"'»”)]*\s+")


@dataclass
class Segment:
    page_no: int | None
    char_start: int
    char_end: int
    text: str
    section: str | None


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token) for budgeting, not billing."""
    return max(1, round(len(text) / 4))


def _paragraphs(text: str) -> Iterator[tuple[int, int]]:
    """Yield (start, end) of non-blank paragraphs, trimmed of surrounding whitespace."""
    pos = 0
    for m in _PARA_BREAK.finditer(text):
        yield from _trimmed(text, pos, m.start())
        pos = m.end()
    yield from _trimmed(text, pos, len(text))


def _trimmed(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if end > start:
        yield start, end


def _split_long(text: str, start: int, end: int, max_chars: int) -> Iterator[tuple[int, int]]:
    """Cut [start, end) into pieces <= max_chars at sentence, then word, boundaries."""
    while end - start > max_chars:
        window_end = start + max_chars
        cut = None
        for m in _SENTENCE_END.finditer(text, start, window_end):
            if m.end() - start >= max_chars // 3:
                cut = m.end()
        if cut is None:
            space = text.rfind(" ", start + max_chars // 3, window_end)
            cut = space + 1 if space != -1 else window_end
        yield from _trimmed(text, start, cut)
        start = cut
    yield from _trimmed(text, start, end)


def _heading(text: str, start: int, end: int) -> tuple[int, str] | None:
    first_line = text[start:end].split("\n", 1)[0]
    m = _HEADING.match(first_line)
    return (len(m.group(1)), m.group(2).strip()) if m else None


def segment_unit(
    unit: Unit,
    markdown: bool,
    target_chars: int = TARGET_CHARS,
    max_chars: int = MAX_CHARS,
    breadcrumb: list[tuple[int, str]] | None = None,
) -> list[Segment]:
    text = unit.text
    crumbs = breadcrumb if breadcrumb is not None else []
    out: list[Segment] = []
    cur_start: int | None = None
    cur_end = 0
    cur_section: str | None = None

    def flush() -> None:
        nonlocal cur_start
        if cur_start is not None:
            out.append(
                Segment(unit.page_no, cur_start, cur_end, text[cur_start:cur_end], cur_section)
            )
            cur_start = None

    def section() -> str | None:
        return " > ".join(title for _, title in crumbs) or None

    for p_start, p_end in _paragraphs(text):
        if markdown and (h := _heading(text, p_start, p_end)):
            flush()
            level, title = h
            while crumbs and crumbs[-1][0] >= level:
                crumbs.pop()
            crumbs.append((level, title))
        for s, e in _split_long(text, p_start, p_end, max_chars):
            if cur_start is not None and e - cur_start > target_chars:
                flush()
            if cur_start is None:
                cur_start, cur_section = s, section()
            cur_end = e
    flush()
    return out


def segment_document(doc: Extracted, markdown: bool = False) -> list[Segment]:
    crumbs: list[tuple[int, str]] = []
    segments: list[Segment] = []
    for unit in doc.units:
        segments.extend(segment_unit(unit, markdown, breadcrumb=crumbs))
    return segments
