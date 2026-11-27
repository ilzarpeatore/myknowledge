"""Where a fragment (or a quote inside it) is in its original: page, section,
slide, and time for audio/video/subtitles."""

from typing import Any

from kos.ingestion.segment import time_at
from kos.models import SourceSegment


def location(seg: SourceSegment, quote_offset: int | None = None) -> dict[str, Any]:
    t_ms = seg.loc_t_start_ms
    if quote_offset is not None and seg.loc_timeline:
        t_ms = time_at(seg.loc_timeline, quote_offset)
    return {
        "page": seg.loc_page_start,
        "section": seg.loc_section,
        "slide": seg.loc_slide,
        "t_ms": t_ms,
    }


def label(seg: SourceSegment) -> str:
    """Short human label, e.g. 'p. 4', 'diap. 3', '12:05'."""
    parts = [seg.loc_section] if seg.loc_section else []
    if seg.loc_page_start:
        parts.append(f"p. {seg.loc_page_start}")
    if seg.loc_slide:
        parts.append(f"diap. {seg.loc_slide}")
    if seg.loc_t_start_ms is not None:
        parts.append(clock(seg.loc_t_start_ms))
    return " · ".join(parts)


def clock(ms: int) -> str:
    s = ms // 1000
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
