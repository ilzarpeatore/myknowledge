# ruff: noqa: E501  (fixture markup)
"""Synthetic office documents for tests (generated, never real material)."""

import io
import zipfile


def make_docx() -> bytes:
    import docx

    d = docx.Document()
    d.add_heading("Fatiga neuromuscular", level=1)
    d.add_paragraph("La fatiga periférica reduce la producción de fuerza del músculo.")
    d.add_heading("Recuperación", level=2)
    d.add_paragraph("La recuperación completa puede requerir entre 48 y 72 horas.")
    t = d.add_table(rows=3, cols=2)
    for r, (a, b) in enumerate([("RIR", "Series"), ("0", "3"), ("3", "4")]):
        t.cell(r, 0).text, t.cell(r, 1).text = a, b
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_pptx() -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    p = Presentation()
    for title, body in [
        ("Proximidad al fallo", "Entrenar cerca del fallo aumenta la fatiga acumulada."),
        ("Volumen", "El volumen semanal se mide en series efectivas por grupo muscular."),
        ("Conclusión", "Dejar 1 a 3 repeticiones en reserva es suficiente para hipertrofia."),
    ]:
        s = p.slides.add_slide(p.slide_layouts[1])
        s.shapes.title.text = title
        s.placeholders[1].text = body
        s.notes_slide.notes_text_frame.text = f"Nota del ponente sobre {title.lower()}."
    _ = Inches
    buf = io.BytesIO()
    p.save(buf)
    return buf.getvalue()


def make_epub() -> bytes:
    chapters = [
        (
            "Capítulo 1. Fuerza",
            "<h1>Capítulo 1. Fuerza</h1><p>La fuerza máxima depende del reclutamiento de unidades motoras.</p>",
        ),
        (
            "Capítulo 2. Hipertrofia",
            "<h1>Capítulo 2. Hipertrofia</h1><p>La tensión mecánica es el principal estímulo de la hipertrofia.</p><h2>Volumen</h2><p>Más series producen más crecimiento hasta un punto.</p>",
        ),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>""",
        )
        items = "".join(
            f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
            for i in range(len(chapters))
        )
        spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
        z.writestr(
            "OEBPS/content.opf",
            f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">prueba</dc:identifier>
<dc:title>PRUEBA - Libro sintético</dc:title><dc:language>es</dc:language></metadata>
<manifest>{items}</manifest><spine>{spine}</spine></package>""",
        )
        for i, (title, body) in enumerate(chapters):
            z.writestr(
                f"OEBPS/c{i}.xhtml",
                f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head><body>{body}</body></html>""",
            )
    return buf.getvalue()


VTT = """WEBVTT

00:00:01.000 --> 00:00:04.500
Hoy hablamos de la proximidad al fallo.

00:00:04.500 --> 00:00:09.000
Entrenar cerca del fallo aumenta la fatiga acumulada.

01:02:03.250 --> 01:02:07.000
<v Ponente>Dejar tres repeticiones en reserva es suficiente.
"""

SRT = """1
00:00:01,000 --> 00:00:04,500
Hoy hablamos de la proximidad al fallo.

2
00:00:04,500 --> 00:00:09,000
Entrenar cerca del fallo
aumenta la fatiga acumulada.

3
01:02:03,250 --> 01:02:07,000
Dejar tres repeticiones en reserva es suficiente.
"""
