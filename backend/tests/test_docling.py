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
