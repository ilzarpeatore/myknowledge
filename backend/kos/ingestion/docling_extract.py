"""Layout-aware PDF extraction with Docling: headings, tables and OCR.

The text of each page is composed from Docling's items in reading order:
headings become Markdown headings (so the segmenter records sections), tables
become Markdown tables, running page headers/footers are dropped. That composed
page text is what gets stored, and segment offsets point into it.

Docling and its models are optional (``uv sync --extra docling``); OCR uses the
Tesseract binary when it is installed.
"""

import multiprocessing
import shutil
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
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


def release() -> None:
    """Drop the loaded layout/table models (to make room for the speech model)."""
    _converter.cache_clear()
    import gc

    gc.collect()


def _models_ready():
    """Release the speech model before loading the PDF models (see transcribe._model)."""
    from kos.media import transcribe

    transcribe.release()
    return _converter()


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


def convert_pages(path: str, first: int, last: int) -> tuple[dict[int, list[str]], int]:
    """Blocks of pages ``first``..``last`` (1-based, inclusive) and their table count."""
    doc = _models_ready().convert(path, raises_on_error=True, page_range=(first, last)).document
    pages: dict[int, list[str]] = {}
    for item, _level in doc.iterate_items():
        prov = getattr(item, "prov", None)
        if not prov:
            continue
        block = _block(item, doc)
        if block:
            pages.setdefault(prov[0].page_no, []).append(block)
    return pages, len(doc.tables)


def _batches(page_count: int, size: int) -> list[tuple[int, int]]:
    return [(a, min(a + size - 1, page_count)) for a in range(1, page_count + 1, size)]


def _run_batches(path: Path, page_count: int):
    """Yield ``(first, last, result or exception)`` for each batch of pages.

    Docling's memory grows with the pages it has converted, even across calls
    (measured on the VPS: a 221-page book reached the worker's 3 GB limit and the
    kernel killed it, again on every retry). By default each batch runs in a new
    process, so its memory is returned when the batch ends, and running out of
    memory kills only that process: the batch fails, not the worker."""
    s = get_settings()
    for first, last in _batches(page_count, s.docling_batch_pages):
        try:
            if not s.docling_in_subprocess:
                yield first, last, convert_pages(str(path), first, last)
                continue
            ctx = multiprocessing.get_context("spawn")
            with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
                yield first, last, pool.submit(convert_pages, str(path), first, last).result()
        except BrokenProcessPool:
            yield first, last, MemoryError("el proceso de Docling terminó (¿sin memoria?)")
        except Exception as exc:  # noqa: BLE001 - one bad batch must not lose the rest
            yield first, last, exc


def extract_pdf_docling(path: Path) -> Extracted:
    # pdfium first: cheap, validates the file (encrypted/corrupt -> ExtractionError)
    # and tells which pages have no text layer, i.e. which ones needed OCR.
    plain = extract_pdf(path)
    no_text_layer = plain.info.get("pages_without_text", [])
    plain_text = {u.page_no: u.text for u in plain.units}

    page_count = plain.page_count or 0
    pages: dict[int, list[str]] = {n: [] for n in range(1, page_count + 1)}
    tables = 0
    basic: list[int] = []  # pages left with pdfium's text because their batch failed
    reasons: list[str] = []
    for first, last, out in _run_batches(path, page_count):
        if isinstance(out, BaseException):
            if len(basic) + (last - first + 1) == page_count:
                raise out  # nothing converted: the caller falls back to pdfium
            basic.extend(range(first, last + 1))
            reasons.append(f"{first}-{last}: {type(out).__name__}: {out}"[:200])
            for n in range(first, last + 1):
                pages[n] = [plain_text.get(n, "")]
            continue
        batch_pages, batch_tables = out
        tables += batch_tables
        for n, blocks in batch_pages.items():
            pages.setdefault(n, []).extend(blocks)

    units = [
        Unit(page_no=n, text=normalize("\n\n".join(blocks))) for n, blocks in sorted(pages.items())
    ]
    info: dict[str, Any] = {"tables": tables}
    if basic:
        info["basic_extraction_pages"] = basic
        info["basic_extraction_reasons"] = reasons
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
