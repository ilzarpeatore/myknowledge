"""A synthetic PDF that looks like course material: headings, a bordered and a
borderless table, two columns and an image-only (scanned) page. Test fixture only."""

import io

import pypdfium2 as pdfium
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BODY = (
    "La proximidad al fallo se cuantifica con las repeticiones en reserva (RIR). "
    "Entrenar a 0-3 RIR produce estímulos de hipertrofia similares con volúmenes equiparados. "
)


def build() -> bytes:
    ss = getSampleStyleSheet()

    def p(text, style="BodyText"):
        return Paragraph(text, ss[style])

    bordered = Table(
        [
            ["Variable", "Hipertrofia", "Fuerza"],
            ["Series semanales", "10-20", "6-12"],
            ["RIR", "0-3", "1-3"],
        ]
    )
    bordered.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    borderless = Table(
        [
            ["Estudio", "Muestra", "Resultado"],
            ["Autor A (2020)", "24", "Sin diferencias"],
            ["Autor B (2022)", "36", "Ventaja 1-2 RIR"],
        ]
    )

    scan_buf = io.BytesIO()
    SimpleDocTemplate(scan_buf, pagesize=A4).build(
        [p("Página escaneada", "Heading2"), p("La fatiga central depende del sistema nervioso.")]
    )
    page = pdfium.PdfDocument(scan_buf.getvalue())[0]
    png = io.BytesIO()
    page.render(scale=2).to_pil().save(png, "PNG")
    png.seek(0)

    out = io.BytesIO()
    w, h = A4
    doc = BaseDocTemplate(out, pagesize=A4)
    doc.addPageTemplates(
        [
            PageTemplate("one", [Frame(50, 50, w - 100, h - 100)]),
            PageTemplate(
                "two",
                [
                    Frame(50, 50, (w - 110) / 2, h - 100),
                    Frame(60 + (w - 110) / 2, 50, (w - 110) / 2, h - 100),
                ],
            ),
        ]
    )
    doc.build(
        [
            p("Proximidad al fallo", "Heading1"),
            p(BODY),
            p("Tabla 1. Recomendaciones", "Heading3"),
            bordered,
            Spacer(1, 12),
            p("Tabla 2. Estudios", "Heading3"),
            borderless,
            NextPageTemplate("two"),
            PageBreak(),
            p("Fatiga periférica", "Heading2"),
            p(BODY * 4),
            p("Fatiga central", "Heading2"),
            p(BODY * 6),
            NextPageTemplate("one"),
            PageBreak(),
            Image(png, width=w - 120, height=h - 140),
        ]
    )
    return out.getvalue()
