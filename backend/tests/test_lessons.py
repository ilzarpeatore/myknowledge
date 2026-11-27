"""Learning: a course built from the chapters of its materials, the comprehension
reading of a lesson in three layers, and «explícalo con tus palabras»."""

import pytest
from sqlalchemy import select

from kos.ai import providers
from kos.ai.providers import ProviderResult, Usage
from kos.models import LessonProgress
from kos.study import lesson_ai as la
from tests.pdfgen import make_pdf

from .test_chapters import BOOK, OUTLINE
from .test_ingestion import upload
from .test_knowledge import run_jobs
from .test_mcp import data, mcp_client, serve
from .test_study import docs_in

OTHER = (
    "# Crecimiento oseo\n\n"
    "Los huesos largos crecen en longitud gracias al cartilago de conjuncion de la metafisis."
)


@pytest.fixture
async def course(auth_client):
    book = await upload(auth_client, "PRUEBA anatomia.pdf", make_pdf(BOOK, OUTLINE))
    other = await upload(auth_client, "PRUEBA otro libro.md", OTHER.encode())
    await run_jobs()
    c = (await auth_client.post("/api/courses", json={"title": "Curso de anatomía"})).json()
    r = await auth_client.post(f"/api/courses/{c['id']}/materials", json={"source_id": book["id"]})
    assert r.status_code == 201, r.text
    return {"id": c["id"], "book": book, "other": other}


def lessons_of(course_json):
    return [(m["title"], [x["title"] for x in m["lessons"]]) for m in course_json["modules"]]


async def test_slides_give_one_module_and_a_lesson_per_block(auth_client, course):
    cid = course["id"]
    preview = (await auth_client.get(f"/api/courses/{cid}/structure/preview")).json()
    assert [[x["title"] for x in m["lessons"]] for m in preview["modules"]] == [
        ["Capitulo I. Generalidades", "Capitulo II. Tronco"]
    ]  # cover, index and bibliography are not lessons; points stay inside their block
    r = await auth_client.post(f"/api/courses/{cid}/structure", json={})
    assert r.status_code == 201 and r.json() == {"modules": 1, "lessons": 2}
    c = (await auth_client.get(f"/api/courses/{cid}")).json()
    blocks = ["Capitulo I. Generalidades", "Capitulo II. Tronco"]
    assert lessons_of(c) == [("PRUEBA anatomia", blocks)]
    assert c["progress"]["lessons"] == 2
    # never silently replaced
    assert (await auth_client.post(f"/api/courses/{cid}/structure", json={})).status_code == 409
    r = await auth_client.post(f"/api/courses/{cid}/structure", json={"replace": True})
    assert r.status_code == 201


async def test_a_book_gives_modules_from_chapters_and_splits_long_ones(
    auth_client, course, monkeypatch
):
    from kos.study import lessons as ls

    cid = course["id"]
    monkeypatch.setattr(ls, "SLIDES_TOKENS_PER_PAGE", 0)  # read it as a book
    plan = (await auth_client.get(f"/api/courses/{cid}/structure/preview")).json()["modules"]
    assert [(m["title"], [x["title"] for x in m["lessons"]]) for m in plan] == [
        ("Capitulo I. Generalidades", ["Capitulo I. Generalidades"]),  # short: one lesson
        ("Capitulo II. Tronco", ["Capitulo II. Tronco"]),
    ]
    monkeypatch.setattr(ls, "SPLIT_BOOK_PAGES", 1)
    plan = (await auth_client.get(f"/api/courses/{cid}/structure/preview")).json()["modules"]
    assert [x["title"] for x in plan[0]["lessons"]] == ["Huesos", "Articulaciones"]
    assert plan[0]["lessons"][1]["pages"] == [4, 4]


async def test_several_documents_give_one_module_each(auth_client, course):
    cid = course["id"]
    other = {"source_id": course["other"]["id"]}
    await auth_client.post(f"/api/courses/{cid}/materials", json=other)
    await auth_client.post(f"/api/courses/{cid}/structure", json={})
    c = (await auth_client.get(f"/api/courses/{cid}")).json()
    assert lessons_of(c) == [
        ("PRUEBA anatomia", ["Capitulo I. Generalidades", "Capitulo II. Tronco"]),
        ("PRUEBA otro libro", ["PRUEBA otro libro"]),  # no chapters: the whole document
    ]
    whole = c["modules"][1]["lessons"][0]["id"]
    study = (await auth_client.get(f"/api/lessons/{whole}/study")).json()
    assert study["estimate"]["tokens"] > 0  # the whole text, although it has no pages


class Teacher:
    def __init__(self, score=80):
        self.calls, self.score = [], score

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        if kw["schema"] is la.FeedbackOut:
            parsed = la.FeedbackOut(
                score=self.score,
                understood=["El hueso es un órgano vivo."],
                missing=["Las partes del hueso largo."],
                misconceptions=[],
                next_step="Repasa la diáfisis.",
            )
        else:
            frags = docs_in(kw)
            mine = next(r for r, t in frags.items() if r.startswith("m") and "organo vivo" in t)
            other = next(r for r in frags if r.startswith("o"))
            cite = la.CiteOut
            parsed = la.ReadingOut(
                objectives=["Por qué el hueso es un órgano vivo."],
                sections=[
                    la.SectionOut(
                        heading="El hueso",
                        material=[
                            la.PointOut(
                                text="El hueso es un órgano vivo.",
                                citations=[cite(fragment=mine, quote="El hueso es un organo vivo")],
                            )
                        ],
                        library=[
                            la.PointOut(
                                text="Crece por el cartílago de conjunción.",
                                citations=[cite(fragment=other, quote="cartilago de conjuncion")],
                            ),
                            la.PointOut(  # cites the lesson's own material: not «library»
                                text="Otra cosa.",
                                citations=[cite(fragment=mine, quote="El hueso es un organo")],
                            ),
                        ],
                        explanation="Se remodela con la carga, como cuando entrenas fuerza.",
                    )
                ],
                checks=[
                    la.CheckOut(
                        question="¿Por qué decimos que es vivo?",
                        answer="Porque tiene tejido óseo y médula.",
                        citations=[cite(fragment=mine, quote="tejido oseo y medula")],
                    )
                ],
            )
        return ProviderResult(
            parsed=parsed,
            stop_reason="end_turn",
            model_served="claude-sonnet-5-5",
            usage=Usage(input_tokens=800, output_tokens=400),
            request_id="req",
        )


async def _lesson(auth_client, course, title):
    await auth_client.post(f"/api/courses/{course['id']}/structure", json={})
    c = (await auth_client.get(f"/api/courses/{course['id']}")).json()
    return next(x["id"] for m in c["modules"] for x in m["lessons"] if x["title"] == title)


async def test_the_reading_keeps_material_library_and_ai_apart(auth_client, course, monkeypatch):
    m = Teacher()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    lid = await _lesson(auth_client, course, "Capitulo I. Generalidades")
    study = (await auth_client.get(f"/api/lessons/{lid}/study")).json()
    assert study["reading"] is None and study["estimate"]["model"] == "claude-sonnet-5-5"
    assert study["parts"][0]["page_start"] == 3 and study["next"]["title"] == "Capitulo II. Tronco"
    r = await auth_client.post(f"/api/lessons/{lid}/reading")
    assert r.status_code == 202
    assert (await auth_client.post(f"/api/lessons/{lid}/reading")).status_code == 409
    await run_jobs()

    sent = docs_in(m.calls[0])
    own = [t for r, t in sent.items() if r.startswith("m")]
    lib = [t for r, t in sent.items() if r.startswith("o")]
    assert any("organo vivo" in t for t in own) and not any("columna" in t for t in own)
    assert lib and all("cartilago" in t for t in lib)  # other sources only, never the book
    reading = (await auth_client.get(f"/api/lessons/{lid}/study")).json()["reading"]
    sec = reading["content"]["sections"][0]
    assert sec["material"][0]["citations"][0]["page"] == 3
    assert [p["text"] for p in sec["library"]] == ["Crece por el cartílago de conjunción."]
    assert sec["library"][0]["citations"][0]["source_title"] == "PRUEBA otro libro"
    assert sec["explanation"].startswith("Se remodela")
    assert reading["content"]["library_sources"] == ["PRUEBA otro libro"]
    assert "**Explicación (IA, conocimiento general):**" in reading["markdown"]
    assert "[PRUEBA otro libro" in reading["markdown"] and "[p. 3]" in reading["markdown"]


async def test_explaining_it_back_measures_understanding(auth_client, course, db, monkeypatch):
    m = Teacher(score=80)
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    lid = await _lesson(auth_client, course, "Capitulo I. Generalidades")
    short = await auth_client.post(f"/api/lessons/{lid}/explanations", json={"text": "hueso"})
    assert short.status_code == 422
    text = "El hueso es un organo vivo porque tiene tejido y medula, y cambia con la carga."
    r = await auth_client.post(f"/api/lessons/{lid}/explanations", json={"text": text})
    assert r.status_code == 202 and r.json()["status"] == "pending"
    await run_jobs()
    assert "alumno" in docs_in(m.calls[-1])
    study = (await auth_client.get(f"/api/lessons/{lid}/study")).json()
    assert study["progress"] == {"status": "completed", "understanding": 80}
    assert study["explanations"][0]["feedback"]["missing"] == ["Las partes del hueso largo."]
    m.score = 50  # a worse attempt does not lower what was already understood
    await auth_client.post(f"/api/lessons/{lid}/explanations", json={"text": text})
    await run_jobs()
    p = await db.scalar(select(LessonProgress))
    await db.refresh(p)
    assert p.understanding == 80 and p.status == "completed"


async def test_agents_read_lessons_over_mcp(auth_client, course, db, monkeypatch):
    from kos.mcp_server import tokens

    monkeypatch.setattr(providers, "get_provider", lambda: Teacher())
    lid = await _lesson(auth_client, course, "Capitulo I. Generalidades")
    await auth_client.post(f"/api/lessons/{lid}/reading")
    await run_jobs()
    async with serve() as app:
        _, raw = await tokens.create(db, "lector", propose=False)
        async with mcp_client(app, raw) as c:
            listing = data(await c.call_tool("list_course_lessons", {"course_id": course["id"]}))
            first = next(x for x in listing["lessons"] if x["id"] == lid)
            assert first["reading"] and first["module"] == "PRUEBA anatomia"
            got = data(await c.call_tool("get_lesson", {"lesson_id": lid}))
            assert got["reading"].startswith("# Capitulo I. Generalidades")
