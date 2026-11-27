"""PDF extraction with Docling (tables, headings, OCR) and the pdfium fallback.

The Docling tests need the `docling` extra and its models (downloaded on first use)
and, for OCR, the tesseract binary with Spanish data. They are skipped otherwise.
"""

from pathlib import Path

import pytest

from kos.config import get_settings
from kos.ingestion import docling_extract
from kos.ingestion.extract import extract
from kos.ingestion.segment import segment_document
from tests.realistic_pdf import build

needs_docling = pytest.mark.skipif(not docling_extract.available(), reason="docling not installed")
needs_ocr = pytest.mark.skipif(
    not docling_extract.ocr_available(), reason="tesseract not installed"
)


@pytest.fixture
def pdf_path(tmp_path) -> Path:
    path = tmp_path / "sintetico.pdf"
    path.write_bytes(build())
    return path


@pytest.fixture
def use_extractor(monkeypatch):
    def set_(name: str):
        monkeypatch.setattr(get_settings(), "pdf_extractor", name)

    return set_


@pytest.fixture(scope="module")
def docling_result(tmp_path_factory):
    if not docling_extract.available():
        pytest.skip("docling not installed")
    path = tmp_path_factory.mktemp("d") / "sintetico.pdf"
    path.write_bytes(build())
    return docling_extract.extract_pdf_docling(path)


@needs_docling
def test_docling_extracts_tables_as_markdown_with_page(docling_result):
    doc = docling_result
    assert doc.extractor == "docling" and doc.markdown
    assert doc.info["tables"] == 2
    page1 = doc.units[0].text
    assert "| Series semanales" in page1 and "| Autor A (2020)" in page1
    segs = segment_document(doc, doc.markdown)
    table_segs = [s for s in segs if "| Autor A (2020)" in s.text]
    assert table_segs and table_segs[0].page_no == 1


@needs_docling
def test_docling_sections_and_exact_offsets(docling_result):
    doc = docling_result
    segs = segment_document(doc, doc.markdown)
    assert any(s.section and "Fatiga central" in s.section for s in segs)
    for s in segs:
        assert doc.units[s.page_no - 1].text[s.char_start : s.char_end] == s.text


@needs_docling
def test_two_columns_keep_reading_order(docling_result):
    page2 = docling_result.units[1].text
    assert page2.index("Fatiga periférica") < page2.index("Fatiga central")


@needs_docling
@needs_ocr
def test_scanned_page_is_read_with_ocr(docling_result):
    assert docling_result.info["ocr_pages"] == [3]
    assert "sistema nervioso" in docling_result.units[2].text
    assert "pages_without_text" not in docling_result.info


def test_pdfium_mode_never_uses_docling(pdf_path, use_extractor):
    use_extractor("pdfium")
    doc = extract(pdf_path, "application/pdf")
    assert doc.extractor == "pdfium" and doc.info["pages_without_text"] == [3]


def test_docling_failure_falls_back_to_pdfium(pdf_path, use_extractor, monkeypatch):
    use_extractor("docling")

    def boom(path):
        raise RuntimeError("model crashed")

    monkeypatch.setattr(docling_extract, "extract_pdf_docling", boom)
    doc = extract(pdf_path, "application/pdf")
    assert doc.extractor == "pdfium"
    assert doc.info["fallback_from"] == "docling"
    assert "model crashed" in doc.info["fallback_reason"]


@pytest.fixture
def batch_of(monkeypatch):
    def set_(pages: int, subprocess: bool = False):
        monkeypatch.setattr(get_settings(), "docling_batch_pages", pages)
        monkeypatch.setattr(get_settings(), "docling_in_subprocess", subprocess)

    return set_


@needs_docling
def test_batches_give_the_same_pages_and_tables(pdf_path, docling_result, batch_of):
    batch_of(1)
    doc = docling_extract.extract_pdf_docling(pdf_path)
    assert [u.text for u in doc.units] == [u.text for u in docling_result.units]
    assert doc.info["tables"] == docling_result.info["tables"]
    assert "basic_extraction_pages" not in doc.info


@needs_docling
def test_batches_in_a_separate_process(pdf_path, docling_result, batch_of):
    batch_of(2, subprocess=True)
    doc = docling_extract.extract_pdf_docling(pdf_path)
    assert [u.text for u in doc.units] == [u.text for u in docling_result.units]


def test_a_failed_batch_keeps_the_rest_and_uses_basic_text(pdf_path, batch_of, monkeypatch):
    """A batch that runs out of memory must not lose the pages Docling did convert."""
    batch_of(1)

    def fake(path, first, last, ocr=True):
        if first == 2:
            raise MemoryError("sin memoria")
        return {first: [f"# Página {first} por Docling"]}, 1

    monkeypatch.setattr(docling_extract, "convert_pages", fake)
    doc = docling_extract.extract_pdf_docling(pdf_path)
    assert doc.extractor == "docling"
    assert doc.units[0].text == "# Página 1 por Docling"
    assert "Fatiga" in doc.units[1].text  # pdfium's text of page 2
    assert doc.info["basic_extraction_pages"] == [2]
    assert "MemoryError" in doc.info["basic_extraction_reasons"][0]
    assert doc.info["tables"] == 2


def test_every_batch_failing_falls_back_to_pdfium(pdf_path, use_extractor, batch_of, monkeypatch):
    use_extractor("docling")
    batch_of(2)

    def fake(path, first, last, ocr=True):
        raise MemoryError("sin memoria")

    monkeypatch.setattr(docling_extract, "convert_pages", fake)
    monkeypatch.setattr(docling_extract, "available", lambda: True)
    doc = extract(pdf_path, "application/pdf")
    assert doc.extractor == "pdfium" and doc.info["fallback_from"] == "docling"


def test_ocr_only_on_pages_without_text_layer():
    plan = docling_extract.plan
    # covers scanned (1 and 221): OCR only there, the rest in batches without OCR
    p = plan(221, 20, {1, 221})
    assert p[0] == (1, 1, True) and p[-1] == (221, 221, True)
    assert all(not ocr for a, b, ocr in p[1:-1]) and sum(b - a + 1 for a, b, _ in p) == 221
    assert max(b - a + 1 for a, b, _ in p) == 20
    assert plan(5, 20, {2, 3}) == [(1, 1, False), (2, 3, True), (4, 5, False)]
    assert plan(45, 20, None) == [(1, 20, True), (21, 40, True), (41, 45, True)]  # "all"
    assert plan(3, 20, set()) == [(1, 3, False)]


@needs_docling
@needs_ocr
def test_scanned_page_is_read_when_ocr_is_only_where_needed(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "pdf_ocr", "auto")
    path = tmp_path / "s.pdf"
    path.write_bytes(build())
    doc = docling_extract.extract_pdf_docling(path)
    assert doc.info["ocr_scope"] == "auto" and doc.info["ocr_pages"] == [3]
    assert "sistema nervioso" in doc.units[2].text  # the scanned page still read
    assert "| Autor A (2020)" in doc.units[0].text  # tables still read without OCR
