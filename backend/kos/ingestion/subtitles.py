"""SubRip (.srt) and WebVTT (.vtt) subtitles -> timed transcript.

The transcript is the cue texts in order, one cue per line, with a blank line
every ~minute so segments stay a readable size. Each cue keeps its offset in the
text and its time, so any quote can be located to its exact cue (and minute).
"""

import re
from pathlib import Path

from kos.ingestion.extract import Extracted, ExtractionError, Unit, normalize

GROUP_MS = 60_000
GROUP_CHARS = 1000
MAX_CUES = 200_000

_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_ARROW = re.compile(rf"^\s*{_TIME}\s*-->\s*{_TIME}")
_TAG = re.compile(r"</?[a-zA-Z][^>]*>|<\d{1,2}:\d{2}[^>]*>")


def _ms(h, m, s, frac) -> int:
    return ((int(h or 0) * 60 + int(m)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0")[:3])


def parse_cues(text: str) -> list[tuple[int, int, str]]:
    """(start_ms, end_ms, text) for every cue with text. Tolerant of both formats:
    numeric ids, WEBVTT header, NOTE/STYLE blocks, voice and styling tags."""
    cues: list[tuple[int, int, list[str]]] = []
    for block in re.split(r"\n[ \t]*\n", normalize(text).strip()):
        lines = block.split("\n")
        idx = next((i for i, ln in enumerate(lines) if _ARROW.match(ln)), None)
        if idx is None:
            continue  # header, NOTE, STYLE, REGION...
        m = _ARROW.match(lines[idx])
        start, end = _ms(*m.groups()[:4]), _ms(*m.groups()[4:])
        rows = [
            re.sub(
                r"\s+", " ", _TAG.sub("", ln).replace("&nbsp;", " ").replace("&amp;", "&")
            ).strip()
            for ln in lines[idx + 1 :]
        ]
        rows = [r for r in rows if r]
        if rows and end >= start:
            cues.append((start, end, rows))
        if len(cues) > MAX_CUES:
            raise ExtractionError("demasiados subtítulos")
    # YouTube's automatic captions scroll: each cue repeats the line(s) already shown
    # («A / B», then «B», then «B / C»). Keep only the lines not seen in the last few
    # cues, so every sentence appears once, at the time it starts being said.
    out: list[tuple[int, int, str]] = []
    recent: list[str] = []
    for start, end, rows in cues:
        new = [r for r in rows if r not in recent]
        recent = (recent + rows)[-4:]
        if not new:
            if out:
                out[-1] = (out[-1][0], max(out[-1][1], end), out[-1][2])
            continue
        out.append((start, end, " ".join(new)))
    return out


def build_transcript(cues: list[tuple[int, int, str]]) -> Unit:
    parts: list[str] = []
    timed: list[tuple[int, int, int]] = []
    pos = 0
    group_start = group_chars = None
    for start, end, body in cues:
        if parts:
            new_group = start - group_start >= GROUP_MS or group_chars >= GROUP_CHARS
            sep = "\n\n" if new_group else "\n"
            parts.append(sep)
            pos += len(sep)
            if new_group:
                group_start, group_chars = start, 0
        else:
            group_start, group_chars = start, 0
        timed.append((pos, start, end))
        parts.append(body)
        pos += len(body)
        group_chars += len(body)
    return Unit(page_no=None, text="".join(parts), cues=timed)


def extract_subtitles(path: Path) -> Extracted:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExtractionError("los subtítulos deben estar en UTF-8") from None
    cues = parse_cues(text)
    if not cues:
        raise ExtractionError("no se encontraron subtítulos con tiempo")
    unit = build_transcript(cues)
    return Extracted(
        units=[unit],
        page_count=None,
        extractor="subtitles",
        extractor_version="1",
        info={"cues": len(cues), "duration_ms": cues[-1][1]},
    )
