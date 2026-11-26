"""Layout-aware PDF extraction with Docling: headings, tables and OCR.

The text of each page is composed from Docling's items in reading order:
headings become Markdown headings (so the segmenter records sections), tables
become Markdown tables, running page headers/footers are dropped. That composed
page text is what gets stored, and segment offsets point into it.

Docling and its models are optional (``uv sync --extra docling``); OCR uses the
Tesseract binary when it is installed.
"""

import shutil
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

from kos.config import get_settings
from kos.ingestion.extract import MIN_PAGE_TEXT_CHARS, Extracted, Unit, extract_pdf, normalize

SKIPPED_LABELS = {"page_header", "page_footer"}


def available() -> bool:
    try:
        import docling  # noqa: F401
    except ImportError:
        return False
    return True


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


@lru_cache
def _converter():
    """Built once per process: loading the models takes several seconds."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    s = get_settings()
    opts = PdfPipelineOptions(
        do_table_structure=True,
        do_ocr=ocr_available(),
        document_timeout=float(s.docling_timeout_seconds),
        artifacts_path=s.docling_artifacts_path,
    )
    if opts.do_ocr:
        opts.ocr_options = TesseractCliOcrOptions(lang=s.ocr_languages.split(","))
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def _block(item: Any, doc: Any) -> str | None:
    label = str(getattr(item, "label", "")).rsplit(".", 1)[-1].lower()
    if label in SKIPPED_LABELS:
        return None
    if label == "table":
        table = item.export_to_markdown(doc=doc).strip()
        caption = item.caption_text(doc).strip() if hasattr(item, "caption_text") else ""
        return f"{caption}\n\n{table}" if caption and table else table or None
    text = (getattr(item, "text", "") or "").strip()
    if not text:
        return None
    if label == "title":
        return f"# {text}"
    if label == "section_header":
        level = int(getattr(item, "level", 1) or 1)
        return f"{'#' * min(level + 1, 6)} {text}"
    if label == "list_item":
        return f"- {text}"
    return text


def extract_pdf_docling(path: Path) -> Extracted:
    # pdfium first: cheap, validates the file (encrypted/corrupt -> ExtractionError)
    # and tells which pages have no text layer, i.e. which ones needed OCR.
    plain = extract_pdf(path)
    no_text_layer = plain.info.get("pages_without_text", [])

    result = _converter().convert(str(path), raises_on_error=True)
    doc = result.document
    pages: dict[int, list[str]] = {n: [] for n in range(1, (plain.page_count or 0) + 1)}
    for item, _level in doc.iterate_items():
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        block = _block(item, doc)
        if block:
            pages.setdefault(prov[0].page_no, []).append(block)

    units = [
        Unit(page_no=n, text=normalize("\n\n".join(blocks))) for n, blocks in sorted(pages.items())
    ]
    info: dict[str, Any] = {"tables": len(doc.tables)}
    if no_text_layer:
        info["ocr_pages" if ocr_available() else "pages_without_text_layer"] = no_text_layer
    empty = [u.page_no for u in units if len(u.text.strip()) < MIN_PAGE_TEXT_CHARS]
    if empty:
        info["pages_without_text"] = empty
    return Extracted(
        units=units,
        page_count=plain.page_count,
        extractor="docling",
        extractor_version=metadata.version("docling"),
        info=info,
        markdown=True,
    )


def extract_office(path: Path, fmt: str) -> Extracted:
    """DOCX, PPTX and EPUB. No layout model or OCR is needed: Docling reads their
    structure. Presentations give one unit per slide; the rest one Markdown unit
    whose headings become the section of each segment (chapter in an EPUB)."""
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter

    allowed = {"docx": InputFormat.DOCX, "pptx": InputFormat.PPTX, "epub": InputFormat.EPUB}
    try:
        result = DocumentConverter(allowed_formats=[allowed[fmt]]).convert(
            str(path), raises_on_error=True
        )
    except Exception as exc:  # noqa: BLE001 - any parser failure means an unreadable file
        from kos.ingestion.extract import ExtractionError

        raise ExtractionError(f"no se pudo leer el {fmt.upper()} ({type(exc).__name__})") from None
    doc = result.document
    if fmt == "pptx":
        slides: dict[int, list[str]] = {}
        for item, _level in doc.iterate_items():
            prov = getattr(item, "prov", None)
            block = _block(item, doc)
            if block and prov:
                slides.setdefault(prov[0].page_no, []).append(block)
        count = max(slides, default=0)
        units = [
            Unit(page_no=None, slide_no=n, text=normalize("\n\n".join(slides.get(n, []))))
            for n in range(1, count + 1)
        ]
        info: dict[str, Any] = {"slides": count}
        empty = [u.slide_no for u in units if len(u.text.strip()) < MIN_PAGE_TEXT_CHARS]
        if empty:
            info["slides_without_text"] = empty
    else:
        blocks = [b for item, _ in doc.iterate_items() if (b := _block(item, doc))]
        units = [Unit(page_no=None, text=normalize("\n\n".join(blocks)))]
        info = {"tables": len(doc.tables)}
    return Extracted(
        units=units,
        page_count=None,
        extractor=f"docling-{fmt}",
        extractor_version=metadata.version("docling"),
        info=info,
        markdown=True,
    )
