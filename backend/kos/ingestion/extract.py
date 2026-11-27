"""Text extraction with location data.

Each extractor returns *units*: the smallest piece of the original for which the
location is known (a PDF page, or the whole file for plain text). The text is
stored exactly as returned here; segment offsets point into it.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.version as pdfium_version

MAX_PDF_PAGES = 5000
# A page with less visible text than this is probably scanned (needs OCR).
MIN_PAGE_TEXT_CHARS = 20

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f￾￿]")


class ExtractionError(Exception):
    """The file cannot be extracted (corrupt, encrypted...). Not retryable."""


@dataclass
class Unit:
    page_no: int | None  # 1-based PDF page; None when the format has no pages
    text: str
    slide_no: int | None = None  # 1-based slide (presentations)
    # Timed media: (char_start in text, start_ms, end_ms) of each cue, in order.
    cues: list[tuple[int, int, int]] | None = None


@dataclass
class Extracted:
    units: list[Unit]
    page_count: int | None
    extractor: str
    extractor_version: str
    info: dict[str, Any] = field(default_factory=dict)
    # True when the text uses Markdown headings (sections are taken from them).
    markdown: bool = False


def package_version(name: str) -> str:
    """Installed version of ``name``, or "unknown" (e.g. tests without the extra)."""
    from importlib import metadata

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


def normalize(text: str) -> str:
    """Unify line breaks and drop control characters. Applied once, before storage."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("", text)


def extract_pdf(path: Path) -> Extracted:
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        msg = str(exc).lower()
        if "password" in msg:
            raise ExtractionError("el PDF está protegido con contraseña") from None
        raise ExtractionError(f"no se pudo abrir el PDF ({exc})") from None
    try:
        count = len(pdf)
        if count > MAX_PDF_PAGES:
            raise ExtractionError(f"el PDF tiene {count} páginas (máximo {MAX_PDF_PAGES})")
        units: list[Unit] = []
        empty: list[int] = []
        for i in range(count):
            page = pdf[i]
            textpage = page.get_textpage()
            try:
                text = normalize(textpage.get_text_range())
            finally:
                textpage.close()
                page.close()
            if len(text.strip()) < MIN_PAGE_TEXT_CHARS:
                empty.append(i + 1)
            units.append(Unit(page_no=i + 1, text=text))
    finally:
        pdf.close()
    info: dict[str, Any] = {}
    if empty:
        info["pages_without_text"] = empty
    return Extracted(
        units=units,
        page_count=count,
        extractor="pdfium",
        extractor_version=str(pdfium_version.PYPDFIUM_INFO),
        info=info,
    )


def extract_text_file(path: Path, mime: str = "text/plain") -> Extracted:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExtractionError("el archivo no está en UTF-8") from None
    return Extracted(
        units=[Unit(page_no=None, text=normalize(text))],
        page_count=None,
        extractor="text",
        extractor_version="1",
        # Stored originals have no extension: the format comes from the detected MIME.
        markdown=mime == "text/markdown",
    )


SUBTITLE_MIMES = ("text/vtt", "application/x-subrip")
OFFICE_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/epub+zip": "epub",
}


def extract(path: Path, mime: str) -> Extracted:
    if mime == "application/pdf":
        return _extract_pdf_best(path)
    if mime in ("text/markdown", "text/plain"):
        return extract_text_file(path, mime)
    if mime in SUBTITLE_MIMES:
        from kos.ingestion.subtitles import extract_subtitles

        return extract_subtitles(path)
    if mime in OFFICE_MIMES:
        from kos.ingestion import docling_extract

        if not docling_extract.available():
            raise ExtractionError(
                f"los archivos {OFFICE_MIMES[mime].upper()} necesitan Docling (imagen worker)"
            )
        return docling_extract.extract_office(path, OFFICE_MIMES[mime])
    raise ExtractionError(f"no hay extractor para {mime}")


def _extract_pdf_best(path: Path) -> Extracted:
    """Docling when enabled and installed; pdfium otherwise, or if Docling fails
    on this file (the source stays searchable and the fallback is recorded)."""
    from kos.config import get_settings
    from kos.ingestion import docling_extract

    choice = get_settings().pdf_extractor
    if choice == "pdfium" or (choice == "auto" and not docling_extract.available()):
        return extract_pdf(path)
    try:
        return docling_extract.extract_pdf_docling(path)
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 - any Docling failure falls back to pdfium
        doc = extract_pdf(path)
        doc.info["fallback_from"] = "docling"
        doc.info["fallback_reason"] = f"{type(exc).__name__}: {exc}"[:500]
        return doc
