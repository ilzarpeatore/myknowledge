"""Chapters of a book: detected from the PDF bookmarks, edited by hand, summarised
by chapter with verified citations, and read over MCP."""

import uuid

import pytest
from sqlalchemy import func, select

from kos.ai import providers
from kos.ai.providers import ProviderResult, Usage
from kos.library import chapter_index as ci
from kos.library import chapter_summaries as cs
from kos.library import chapters as chs
from kos.mcp_server import tokens
from kos.models import ChapterSummary, SourceChapter
from tests.pdfgen import make_pdf

from .test_ingestion import upload
from .test_knowledge import run_jobs
from .test_mcp import data, mcp_client, serve
from .test_study import docs_in

BOOK = [
    ["Anatomia basica", "Manual de prueba"],
    ["Indice", "Capitulo I .... 3", "Capitulo II .... 5"],
    [
        "CAPITULO I. GENERALIDADES",
        "El hueso es un organo vivo formado por tejido oseo y medula.",
        "Los huesos largos tienen una diafisis y dos epifisis.",
    ],
    ["Las articulaciones unen dos o mas huesos y permiten el movimiento."],
    ["CAPITULO II. TRONCO", "La columna vertebral tiene 33 vertebras en el adulto."],
    ["Bibliografia", "Autor A. Libro B."],
]
OUTLINE = [
    ("Portada", 0, 0),
    ("Indice", 1, 0),
    ("Capitulo I. Generalidades", 2, 0),
    ("Huesos", 2, 1),
    ("Articulaciones", 3, 1),
    ("Capitulo II. Tronco", 4, 0),
    ("Bibliografia", 5, 0),
]


@pytest.fixture
async def book(auth_client):
    src = await upload(auth_client, "PRUEBA anatomia.pdf", make_pdf(BOOK, OUTLINE))
    await run_jobs()
    r = await auth_client.get(f"/api/sources/{src['id']}/chapters")
    assert r.status_code == 200
    return {"id": src["id"], "chapters": r.json()}


def by_title(listing, title):
    return next(c for c in listing["items"] if c["title"] == title)


def test_ranges_end_where_the_next_chapter_starts_and_points_stay_inside():
    items = [
        chs.Detected(1, "Cap I", 3),
        chs.Detected(2, "Punto 1", 3),
        chs.Detected(2, "Punto 2", 4),
        chs.Detected(1, "Cap II", 5),
        chs.Detected(1, "Cap III", 5),  # starts on the same page
    ]
    out = chs.with_ranges(items, 9)
    assert [(i.title, i.page_start, i.page_end) for i in out] == [
        ("Cap I", 3, 4),
        ("Punto 1", 3, 3),
        ("Punto 2", 4, 4),
        ("Cap II", 5, 5),
        ("Cap III", 5, 9),
    ]
    assert not chs.is_content_title("Los derechos de autor")
    assert not chs.is_content_title("Bibliografía")
    assert chs.is_content_title("Capítulo I. Generalidades")


async def test_bookmarks_become_chapters_with_pages_when_a_pdf_is_extracted(book):
    listing = book["chapters"]
    assert listing["can_detect"] and not listing["detecting"]
    got = [(c["title"], c["level"], c["page_start"], c["page_end"]) for c in listing["items"]]
    assert got == [
        ("Portada", 1, 1, 1),
        ("Indice", 1, 2, 2),
        ("Capitulo I. Generalidades", 1, 3, 4),
        ("Huesos", 2, 3, 3),
        ("Articulaciones", 2, 4, 4),
        ("Capitulo II. Tronco", 1, 5, 5),
        ("Bibliografia", 1, 6, 6),
    ]
    content = {c["title"]: c["is_content"] for c in listing["items"]}
    assert content["Portada"] is False and content["Bibliografia"] is False
    assert content["Capitulo I. Generalidades"] is True
    cap1 = by_title(listing, "Capitulo I. Generalidades")
    assert by_title(listing, "Huesos")["parent_id"] == cap1["id"]
    assert all(c["origin"] == "bookmarks" for c in listing["items"])


async def test_edits_are_kept_unless_detection_is_forced(auth_client, book, db):
    sid, listing = book["id"], book["chapters"]
    huesos = by_title(listing, "Huesos")
    r = await auth_client.patch(f"/api/chapters/{huesos['id']}", json={"title": "Los huesos"})
    assert r.status_code == 200 and r.json()["title"] == "Los huesos" and r.json()["edited"]
    r = await auth_client.patch(f"/api/chapters/{huesos['id']}", json={"page_end": 40})
    assert r.status_code == 400 and "6 páginas" in r.json()["detail"]
    r = await auth_client.patch(f"/api/chapters/{huesos['id']}", json={"page_end": 2})
    assert r.status_code == 400
    r = await auth_client.post(
        f"/api/sources/{sid}/chapters",
        json={"title": "Anexo", "page_start": 6, "page_end": 6, "is_content": False},
    )
    assert r.status_code == 201 and r.json()["origin"] == "manual"
    cap1 = by_title(listing, "Capitulo I. Generalidades")
    assert (await auth_client.delete(f"/api/chapters/{cap1['id']}")).status_code == 204
    now = (await auth_client.get(f"/api/sources/{sid}/chapters")).json()
    titles = [c["title"] for c in now["items"]]
    assert "Capitulo I. Generalidades" not in titles
    assert by_title(now, "Los huesos")["parent_id"] is None  # points move up, not lost
    # a new detection does not undo the user's work...
    r = await auth_client.post(f"/api/sources/{sid}/chapters/detect")
    assert r.status_code == 202
    await run_jobs()
    kept = (await auth_client.get(f"/api/sources/{sid}/chapters")).json()
    assert [c["title"] for c in kept["items"]] == titles
    # ...unless asked to
    await auth_client.post(f"/api/sources/{sid}/chapters/detect", params={"force": True})
    await run_jobs()
    again = (await auth_client.get(f"/api/sources/{sid}/chapters")).json()
    assert len(again["items"]) == 7 and not any(c["edited"] for c in again["items"])


class Summarizer:
    def __init__(self):
        self.calls = []

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        frags = docs_in(kw)
        bone = next(r for r, t in frags.items() if "organo vivo" in t)
        joint = next(r for r, t in frags.items() if "articulaciones unen" in t)
        parsed = cs.SummaryOut(
            overview="Generalidades del aparato locomotor.",
            sections=[
                cs.SectionOut(
                    heading="Huesos",
                    points=[
                        cs.PointOut(
                            text="El hueso es un órgano vivo de tejido óseo y médula.",
                            citations=[
                                cs.CitationOut(fragment=bone, quote="El hueso es un organo vivo"),
                                cs.CitationOut(fragment="f99", quote="no existe"),
                            ],
                        ),
                        cs.PointOut(
                            text="Idea que el texto no dice.",
                            citations=[cs.CitationOut(fragment=bone, quote="frase inventada")],
                        ),
                    ],
                ),
                cs.SectionOut(
                    heading="Articulaciones",
                    points=[
                        cs.PointOut(
                            text="Unen huesos y permiten el movimiento.",
                            citations=[
                                cs.CitationOut(
                                    fragment=joint, quote="Las articulaciones unen dos o mas huesos"
                                )
                            ],
                        )
                    ],
                ),
            ],
            key_terms=[cs.TermOut(term="Diáfisis", definition="Cuerpo del hueso largo.")],
        )
        return ProviderResult(
            parsed=parsed,
            stop_reason="end_turn",
            model_served="claude-sonnet-5-5",
            usage=Usage(input_tokens=900, output_tokens=300),
            request_id="req",
        )


async def test_a_chapter_summary_cites_only_what_its_pages_say(auth_client, book, db, monkeypatch):
    m = Summarizer()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    cap1 = by_title(book["chapters"], "Capitulo I. Generalidades")
    detail = (await auth_client.get(f"/api/chapters/{cap1['id']}")).json()
    assert detail["current"] == {}
    assert [p["title"] for p in detail["points"]] == ["Huesos", "Articulaciones"]
    est = detail["estimates"]["study"]
    assert est["tokens"] > 0 and est["model"] == "claude-sonnet-5-5" and est["usd"] > 0
    r = await auth_client.post(f"/api/chapters/{cap1['id']}/summaries", json={"level": "study"})
    assert r.status_code == 202
    again = await auth_client.post(f"/api/chapters/{cap1['id']}/summaries", json={"level": "study"})
    assert again.status_code == 409  # already being generated
    busy = (await auth_client.get(f"/api/chapters/{cap1['id']}")).json()["generating"]
    assert busy == {"study": r.json()["job_id"]}
    await run_jobs()

    sent = "\n".join(docs_in(m.calls[0]).values())
    assert "organo vivo" in sent and "articulaciones unen" in sent
    assert "columna vertebral" not in sent  # chapter II is not part of it
    assert "Estudio" in m.calls[0]["system"]
    detail = (await auth_client.get(f"/api/chapters/{cap1['id']}")).json()
    s = detail["current"]["study"]
    assert s["citations"] == 2 and s["uncited_points"] == 1
    first = s["content"]["sections"][0]["points"][0]["citations"]
    assert len(first) == 1 and first[0]["page"] == 3 and first[0]["quote"].startswith("El hueso")
    assert "[p. 3]" in s["markdown"] and "[p. 4]" in s["markdown"]
    assert "Idea que el texto no dice. [sin cita]" in s["markdown"]
    assert "**Diáfisis**" in s["markdown"]
    listing = (await auth_client.get(f"/api/sources/{book['id']}/chapters")).json()
    assert by_title(listing, "Capitulo I. Generalidades")["summaries"] == ["study"]

    # regenerating keeps the earlier one
    await auth_client.post(f"/api/chapters/{cap1['id']}/summaries", json={"level": "study"})
    await run_jobs()
    detail = (await auth_client.get(f"/api/chapters/{cap1['id']}")).json()
    assert len(detail["history"]) == 2
    old = detail["history"][1]["id"]
    r = await auth_client.get(f"/api/chapters/{cap1['id']}/summaries/{old}")
    assert r.status_code == 200 and r.json()["level"] == "study"
    assert await db.scalar(select(func.count()).select_from(ChapterSummary)) == 2


async def test_target_length_grows_with_the_chapter_within_limits():
    assert cs.target_words(100, "study") == 250  # short: the minimum
    assert cs.target_words(20_000, "study") == 1350
    assert cs.target_words(200_000, "study") == 1500  # long: the cap
    assert cs.target_words(20_000, "essential") < cs.target_words(20_000, "detailed")


async def test_agents_read_chapters_and_summaries_over_mcp(auth_client, book, db, monkeypatch):
    monkeypatch.setattr(providers, "get_provider", lambda: Summarizer())
    cap1 = by_title(book["chapters"], "Capitulo I. Generalidades")
    async with serve() as app:
        _, reader = await tokens.create(db, "lector", propose=False)
        async with mcp_client(app, reader) as c:
            listing = data(await c.call_tool("list_chapters", {"source_id": book["id"]}))
            assert [x["title"] for x in listing["chapters"]][:3] == [
                "Portada",
                "Indice",
                "Capitulo I. Generalidades",
            ]
            ch = data(await c.call_tool("get_chapter", {"chapter_id": cap1["id"]}))
            assert ch["pages"] == [3, 4] and "summary" not in ch and ch["fragments"]
            r = await c.call_tool("summarize_chapter", {"chapter_id": cap1["id"]})
            assert r.is_error and "permiso" in r.content[0].text
        _, writer = await tokens.create(db, "agente", propose=True)
        async with mcp_client(app, writer) as c:
            queued = data(await c.call_tool("summarize_chapter", {"chapter_id": cap1["id"]}))
            assert queued["queued"] and queued["estimate"]["usd"] > 0
        await run_jobs()
        async with mcp_client(app, reader) as c:
            ch = data(await c.call_tool("get_chapter", {"chapter_id": cap1["id"]}))
            assert ch["summary"]["markdown"].startswith("# Capitulo I. Generalidades")
            cited = ch["summary"]["content"]["sections"][0]["points"][0]["citations"][0]
            ids = {"fragment_ids": [cited["fragment_id"]]}
            frag = data(await c.call_tool("get_fragments", ids))
            assert "organo vivo" in frag["fragments"][0]["text"]
    row = await db.scalar(select(ChapterSummary))
    assert row.origin == "mcp"
    assert await db.get(SourceChapter, uuid.UUID(cap1["id"])) is not None


SENTENCE = "La fuerza maxima depende del area de seccion transversal del musculo."
NO_BOOKMARKS = (
    [["Libro de fuerza"], ["Contenido", "Capitulo 1 .... 3", "Capitulo 2 .... 11"]]
    + [["Capitulo 1. La fuerza", SENTENCE]]
    + [[SENTENCE, SENTENCE] for _ in range(7)]
    + [["Capitulo 2. La potencia", "La potencia es fuerza por velocidad."]]
    + [[SENTENCE] for _ in range(9)]
    + [["Bibliografia", "Autor A. Libro B."]]
    + [[SENTENCE] for _ in range(3)]
)


class Picker:
    """Chooses the «Capitulo» and «Bibliografia» headings, plus an unknown id and one
    going back, which must be ignored."""

    def __init__(self):
        self.calls = []

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        lines = docs_in(kw)["headings"].splitlines()
        refs = {line.split(" · ")[2]: line.split(" · ")[0] for line in lines}
        cap1 = refs["Capitulo 1. La fuerza"]
        picks = [
            ci.PickOut(heading=cap1, level=1, title="Capítulo 1. La fuerza"),
            ci.PickOut(heading="h999", level=1, title="inventado"),
            ci.PickOut(heading=refs["Capitulo 2. La potencia"], level=1, title="Capítulo 2"),
            ci.PickOut(heading=cap1, level=2, title="hacia atrás"),
            ci.PickOut(heading=refs["Bibliografia"], level=1, title="Bibliografía"),
        ]
        return ProviderResult(
            parsed=ci.ChaptersOut(chapters=picks),
            stop_reason="end_turn",
            model_served="claude-haiku-5-5",
            usage=Usage(input_tokens=500, output_tokens=100),
            request_id="req",
        )


async def test_without_bookmarks_the_ai_picks_chapters_among_real_headings(
    auth_client, monkeypatch
):
    m = Picker()
    monkeypatch.setattr(providers, "get_provider", lambda: m)

    async def yes(db):
        return True

    monkeypatch.setattr(ci, "ai_available", yes)
    assert len(NO_BOOKMARKS) == 24
    src = await upload(auth_client, "PRUEBA fuerza.pdf", make_pdf(NO_BOOKMARKS))
    await run_jobs()
    listing = (await auth_client.get(f"/api/sources/{src['id']}/chapters")).json()
    got = [(c["title"], c["page_start"], c["page_end"], c["is_content"]) for c in listing["items"]]
    assert got == [
        ("Capítulo 1. La fuerza", 3, 10, True),
        ("Capítulo 2", 11, 20, True),
        ("Bibliografía", 21, 24, False),
    ]
    assert {c["origin"] for c in listing["items"]} == {"headings"}
    sent = docs_in(m.calls[0])
    assert "index-p2" in sent  # the printed index goes along
    assert SENTENCE not in sent["headings"]  # sentences are not headings


async def test_short_documents_without_bookmarks_have_no_chapters_and_no_ai_call(
    auth_client, monkeypatch
):
    m = Picker()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    src = await upload(auth_client, "PRUEBA corto.pdf", make_pdf(NO_BOOKMARKS[:6]))
    await run_jobs()
    listing = (await auth_client.get(f"/api/sources/{src['id']}/chapters")).json()
    assert listing["items"] == [] and listing["can_detect"] and m.calls == []


class PartSummarizer:
    def __init__(self):
        self.calls = []

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        if kw["schema"] is cs.OverviewOut:
            parsed = cs.OverviewOut(overview="Visión de conjunto de las partes.")
        else:
            points = [
                cs.PointOut(
                    text=f"Idea de {ref}.",
                    citations=[cs.CitationOut(fragment=ref, quote=" ".join(t.split()[:5]))],
                )
                for ref, t in docs_in(kw).items()
            ]
            parsed = cs.SummaryOut(
                overview="Parte.",
                sections=[cs.SectionOut(heading="Ideas", points=points)],
                key_terms=[cs.TermOut(term="Hueso", definition="Órgano vivo.")],
            )
        return ProviderResult(
            parsed=parsed,
            stop_reason="end_turn",
            model_served="claude-sonnet-5-5",
            usage=Usage(input_tokens=300, output_tokens=100),
            request_id="req",
        )


async def test_a_long_chapter_is_summarised_by_its_points(auth_client, book, monkeypatch):
    m = PartSummarizer()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    monkeypatch.setattr(cs, "PART_CHARS", 120)  # the chapter is «long», each point fits
    cap1 = by_title(book["chapters"], "Capitulo I. Generalidades")
    est = (await auth_client.get(f"/api/chapters/{cap1['id']}")).json()["estimates"]["study"]
    assert est["by_parts"]
    await auth_client.post(f"/api/chapters/{cap1['id']}/summaries", json={"level": "study"})
    await run_jobs()
    assert [c["schema"].__name__ for c in m.calls] == ["SummaryOut", "SummaryOut", "OverviewOut"]
    assert "Generalidades — Huesos" in m.calls[0]["user_content"][0]["text"]
    s = (await auth_client.get(f"/api/chapters/{cap1['id']}")).json()["current"]["study"]
    c = s["content"]
    assert c["overview"] == "Visión de conjunto de las partes."
    assert [x["heading"] for x in c["sections"]] == ["Huesos · Ideas", "Articulaciones · Ideas"]
    assert [t["term"] for t in c["key_terms"]] == ["Hueso"]  # not repeated
    assert s["citations"] >= 2 and s["uncited_points"] == 0
    # each point keeps its own summary
    huesos = by_title(book["chapters"], "Huesos")
    mine = (await auth_client.get(f"/api/chapters/{huesos['id']}")).json()["current"]
    assert mine["study"]["content"]["sections"][0]["heading"] == "Ideas"
