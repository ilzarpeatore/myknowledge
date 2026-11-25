from kos.ingestion.extract import Extracted, Unit, normalize
from kos.ingestion.segment import segment_document, segment_unit


def doc(*units):
    return Extracted(units=list(units), page_count=None, extractor="t", extractor_version="1")


def test_segments_are_exact_slices():
    text = "Primer párrafo.\n\n  Segundo párrafo con más texto.  \n\n\nTercero."
    segs = segment_unit(Unit(1, text), markdown=False, target_chars=20)
    assert [s.text for s in segs] == [
        "Primer párrafo.",
        "Segundo párrafo con más texto.",
        "Tercero.",
    ]
    for s in segs:
        assert text[s.char_start : s.char_end] == s.text
        assert s.page_no == 1


def test_short_paragraphs_are_grouped_up_to_target():
    text = "\n\n".join(["a" * 100] * 10)
    segs = segment_unit(Unit(None, text), markdown=False, target_chars=450)
    assert len(segs) == 3
    assert all(len(s.text) <= 450 for s in segs)
    assert segs[0].text == text[segs[0].char_start : segs[0].char_end]


def test_long_paragraph_is_split_at_sentences_within_limit():
    sentence = "La fatiga periférica reduce la producción de fuerza del músculo. "
    text = sentence * 80
    segs = segment_unit(Unit(2, text), markdown=False, target_chars=1000, max_chars=1000)
    assert len(segs) > 1
    assert all(len(s.text) <= 1000 for s in segs)
    assert all(s.text.endswith(".") for s in segs)
    assert "".join(text[s.char_start : s.char_end] for s in segs).replace(" ", "") == text.replace(
        " ", ""
    )


def test_segments_never_cross_pages():
    segs = segment_document(doc(Unit(1, "Página uno."), Unit(2, "Página dos.")))
    assert [(s.page_no, s.text) for s in segs] == [(1, "Página uno."), (2, "Página dos.")]


def test_markdown_headings_start_segments_and_set_breadcrumb():
    md = (
        "# Hipertrofia\n\nIntro.\n\n## Mecanismos\n\nTensión mecánica.\n\n"
        "### Tensión\n\nDetalle.\n\n## Variables\n\nVolumen."
    )
    segs = segment_document(doc(Unit(None, md)), markdown=True)
    sections = [(s.section, s.text.split("\n")[0]) for s in segs]
    assert sections == [
        ("Hipertrofia", "# Hipertrofia"),
        ("Hipertrofia > Mecanismos", "## Mecanismos"),
        ("Hipertrofia > Mecanismos > Tensión", "### Tensión"),
        ("Hipertrofia > Variables", "## Variables"),
    ]


def test_blank_text_gives_no_segments():
    assert segment_unit(Unit(1, " \n\n \t "), markdown=False) == []


def test_normalize_unifies_newlines_and_strips_controls():
    assert normalize("a\r\nb\rc\x00d\x02e") == "a\nb\ncde"
