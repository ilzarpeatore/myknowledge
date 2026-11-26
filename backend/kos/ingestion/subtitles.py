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
    cues: list[tuple[int, int, str]] = []
    for block in re.split(r"\n[ \t]*\n", normalize(text).strip()):
        lines = block.split("\n")
        idx = next((i for i, ln in enumerate(lines) if _ARROW.match(ln)), None)
        if idx is None:
            continue  # header, NOTE, STYLE, REGION...
        m = _ARROW.match(lines[idx])
        start, end = _ms(*m.groups()[:4]), _ms(*m.groups()[4:])
        body = " ".join(_TAG.sub("", ln).strip() for ln in lines[idx + 1 :])
        body = re.sub(r"\s+", " ", body.replace("&nbsp;", " ").replace("&amp;", "&")).strip()
        if body and end >= start:
            cues.append((start, end, body))
        if len(cues) > MAX_CUES:
            raise ExtractionError("demasiados subtítulos")
    # Auto-generated captions repeat the previous line as it scrolls: drop exact repeats.
    out: list[tuple[int, int, str]] = []
    for c in cues:
        if out and c[2] == out[-1][2]:
            out[-1] = (out[-1][0], c[1], c[2])
        else:
            out.append(c)
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
