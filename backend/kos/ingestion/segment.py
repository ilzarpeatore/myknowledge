"""Split extracted units into citable segments.

Rules:
- A segment never crosses a unit boundary (a PDF page), so every segment has
  one exact page.
- Segment text is always an exact slice ``unit.text[char_start:char_end]``;
  quotes can therefore be verified against the original text.
- Paragraphs are kept together up to ``target_chars``; a paragraph longer than
  ``max_chars`` is cut at sentence or word boundaries.
- In Markdown, headings start a new segment and give the section breadcrumb.
- A Markdown table is always a segment of its own (split between rows if long).
- Slides keep their number; timed transcripts keep, per segment, its time span and
  a timeline (offset in the segment -> ms) so a quote maps to its own cue.
"""

import bisect
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
    slide_no: int | None = None
    t_start_ms: int | None = None
    t_end_ms: int | None = None
    # [[offset in segment text, start ms], ...] for each cue starting in the segment
    timeline: list[list[int]] | None = None


def _add_timing(seg: Segment, cues: list[tuple[int, int, int]]) -> None:
    starts = [c[0] for c in cues]
    # the cue the segment starts in (it may start mid-cue) and every cue after it
    i = max(bisect.bisect_right(starts, seg.char_start) - 1, 0)
    inside = [c for c in cues[i:] if c[0] < seg.char_end]
    if not inside:
        return
    seg.t_start_ms, seg.t_end_ms = inside[0][1], max(c[2] for c in inside)
    seg.timeline = [[max(c[0] - seg.char_start, 0), c[1]] for c in inside]


def time_at(timeline: list[list[int]] | None, offset: int) -> int | None:
    """Start time (ms) of the cue containing ``offset`` (an offset in the segment)."""
    if not timeline:
        return None
    i = bisect.bisect_right([t[0] for t in timeline], offset) - 1
    return timeline[max(i, 0)][1]


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


def _split_lines(text: str, start: int, end: int, max_chars: int) -> Iterator[tuple[int, int]]:
    """Cut a Markdown table at row boundaries (a row is never split)."""
    piece_start = start
    pos = start
    while pos < end:
        nl = text.find("\n", pos, end)
        line_end = end if nl == -1 else nl
        if line_end - piece_start > max_chars and pos > piece_start:
            yield from _trimmed(text, piece_start, pos)
            piece_start = pos
        pos = line_end + 1
    yield from _trimmed(text, piece_start, end)


def _split_long(text: str, start: int, end: int, max_chars: int) -> Iterator[tuple[int, int]]:
    """Cut [start, end) into pieces <= max_chars at sentence, then word, boundaries.
    Markdown tables are cut between rows instead."""
    if text.startswith("|", start):
        yield from _split_lines(text, start, end, max_chars)
        return
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
            seg = Segment(
                unit.page_no,
                cur_start,
                cur_end,
                text[cur_start:cur_end],
                cur_section,
                slide_no=unit.slide_no,
            )
            if unit.cues:
                _add_timing(seg, unit.cues)
            out.append(seg)
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
        is_table = text.startswith("|", p_start)
        if is_table:
            flush()  # a table is always its own segment: the natural unit to cite
        for s, e in _split_long(text, p_start, p_end, max_chars):
            if cur_start is not None and (is_table or e - cur_start > target_chars):
                flush()
            if cur_start is None:
                cur_start, cur_section = s, section()
            cur_end = e
        if is_table:
            flush()
    flush()
    return out


def segment_document(doc: Extracted, markdown: bool = False) -> list[Segment]:
    crumbs: list[tuple[int, str]] = []
    segments: list[Segment] = []
    for unit in doc.units:
        segments.extend(segment_unit(unit, markdown, breadcrumb=crumbs))
    return segments
