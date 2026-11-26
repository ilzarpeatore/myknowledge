"""Phase 7: flashcards with FSRS, quizzes per module and the gap report."""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from kos.ai import providers
from kos.ai.providers import ProviderResult, Usage
from kos.knowledge import service as ks
from kos.models import (
    Course,
    CourseMaterial,
    CourseModule,
    Flashcard,
    FlashcardReview,
    Lesson,
    Person,
    SourceSegment,
)
from kos.study import ai_jobs, cards, gaps

from .test_knowledge import entity, run_jobs

TEXT = (
    "# Proximidad al fallo\n\nEntrenar a 0-3 RIR maximiza la hipertrofia con menos fatiga acumulada.\n\n"  # noqa: E501
    "## Volumen\n\nEl volumen semanal se mide en series efectivas por grupo muscular y por semana."
)


def docs_in(call):
    blob = call["user_content"][-1]["text"]
    return dict(
        re.findall(
            r'<source_document ref="([^"]+)" label="[^"]*">\n(.*?)\n</source_document>', blob, re.S
        )
    )


class SimulatedModel:
    def __init__(self):
        self.calls = []

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        frags = {r: t for r, t in docs_in(kw).items() if r.startswith("f")}
        ref, text = next(iter(frags.items()))
        sentence = text.split("\n")[-1].rstrip(".")
        if kw["schema"] is ai_jobs.CardsResult:
            parsed = ai_jobs.CardsResult(
                cards=[
                    ai_jobs.CardOut(
                        front="¿Qué RIR maximiza la hipertrofia?",
                        back="Entre 0 y 3.",
                        fragment=ref,
                        quote=sentence,
                    ),
                    ai_jobs.CardOut(
                        front="Pregunta inventada",
                        back="x",
                        fragment=ref,
                        quote="esto no lo dice la fuente",
                    ),
                ]
            )
        else:
            Q = ai_jobs.QuestionOut
            parsed = ai_jobs.QuizResult(
                questions=[
                    Q(
                        kind="choice",
                        prompt="¿Qué RIR recomienda el curso?",
                        options=["0-3", "5-7", "10"],
                        correct_option=0,
                        explanation="Lo dice el curso.",
                        fragment=ref,
                        quote=sentence,
                        concept="Proximidad al fallo",
                    ),
                    Q(
                        kind="true_false",
                        prompt="El RIR alto maximiza la hipertrofia.",
                        correct_bool=False,
                        explanation="El curso dice 0-3.",
                        fragment=ref,
                        quote=sentence,
                    ),
                    Q(
                        kind="short",
                        prompt="¿Por qué no entrenar siempre al fallo?",
                        model_answer="Porque 0-3 RIR da la misma hipertrofia con menos fatiga.",
                        explanation="Menos fatiga.",
                        fragment=ref,
                        quote=sentence,
                        concept="Proximidad al fallo",
                    ),
                    Q(
                        kind="choice",
                        prompt="Mala",
                        options=["a"],
                        correct_option=3,
                        explanation="x",
                        fragment=ref,
                        quote=sentence,
                    ),  # invalid options
                    Q(
                        kind="true_false",
                        prompt="Inventada",
                        correct_bool=True,
                        explanation="x",
                        fragment=ref,
                        quote="frase que no existe en la fuente",
                    ),
                ]
            )
        return ProviderResult(
            parsed=parsed,
            stop_reason="end_turn",
            model_served="claude-opus-5-5",
            usage=Usage(input_tokens=2000, output_tokens=500),
            request_id="req",
        )


@pytest.fixture
def model(monkeypatch):
    m = SimulatedModel()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    return m


@pytest.fixture
async def setup(auth_client, db):
    r = await auth_client.post("/api/sources", files={"file": ("PRUEBA clase.md", TEXT.encode())})
    source_id = uuid.UUID(r.json()["source"]["id"])
    await run_jobs()
    seg = await db.scalar(select(SourceSegment).where(SourceSegment.text.contains("0-3 RIR")))
    teacher = Person(name="Profesora A", role="teacher")
    db.add(teacher)
    await db.commit()
    fallo = await entity(auth_client, "Proximidad al fallo")
    fid = uuid.UUID(fallo["id"])
    accepted = await ks.add_claim(
        db,
        text_="0-3 RIR maximiza la hipertrofia",
        claim_type="recommendation",
        entities=[(fid, "subject")],
        person_id=teacher.id,
        evidence=[(seg.id, "Entrenar a 0-3 RIR maximiza la hipertrofia")],
    )
    await ks.add_claim(
        db,
        text_="Propuesta de la IA sin revisar",
        claim_type="reported_fact",
        entities=[(fid, "subject")],
        origin="ai",
        evidence=[(seg.id, "con menos fatiga acumulada")],
    )
    course = Course(title="PRUEBA Curso", status="in_progress")
    db.add(course)
    await db.flush()
    module = CourseModule(course_id=course.id, title="Módulo 1")
    db.add(module)
    await db.flush()
    lesson = Lesson(module_id=module.id, title="Lección 1")
    db.add(lesson)
    await db.flush()
    db.add(CourseMaterial(course_id=course.id, lesson_id=lesson.id, source_id=source_id))
    await db.commit()
    return {"fallo": fallo, "seg": seg, "accepted": accepted, "module": module, "course": course}


async def test_cards_from_reviewed_claims_link_concept_and_source(auth_client, setup, db):
    r = await auth_client.post(f"/api/entities/{setup['fallo']['id']}/flashcards/generate", json={})
    assert r.json()["created"] == 1  # the unreviewed AI claim is not study material
    again = await auth_client.post(
        f"/api/entities/{setup['fallo']['id']}/flashcards/generate", json={}
    )
    assert again.json()["created"] == 0
    [card] = (await auth_client.get(f"/api/flashcards?entity_id={setup['fallo']['id']}")).json()
    assert card["front"] == "Proximidad al fallo: ¿qué recomienda Profesora A?"
    assert card["entity"]["slug"] == setup["fallo"]["slug"]
    assert card["reference"]["source_title"] == "PRUEBA clase"
    seg = await db.get(SourceSegment, uuid.UUID(card["reference"]["segment_id"]))
    assert card["quote"] in seg.text and card["status"] == "active"


async def test_fsrs_schedules_and_keeps_the_history(db, setup):
    fid = uuid.UUID(setup["fallo"]["id"])
    card = cards.add_card(db, entity_id=fid, front="¿Q?", back="R", kind="qa", origin="user")
    await db.commit()
    t = datetime(2026, 11, 1, 9, tzinfo=UTC)
    await cards.review(db, card, 3, now=t)  # learning step
    assert timedelta(minutes=5) < card.due_at - t < timedelta(hours=1)
    await cards.review(db, card, 3, now=card.due_at)  # graduates to review
    first_interval = card.due_at - t
    assert first_interval >= timedelta(days=1)
    t2 = card.due_at
    await cards.review(db, card, 3, now=t2)
    assert card.due_at - t2 > first_interval  # intervals grow
    await cards.review(db, card, 1, now=card.due_at)  # forgotten
    assert card.lapses == 1 and card.reps == 4
    logs = (
        await db.scalars(
            select(FlashcardReview)
            .where(FlashcardReview.card_id == card.id)
            .order_by(FlashcardReview.reviewed_at)
        )
    ).all()
    assert [x.rating for x in logs] == [3, 3, 3, 1]
    assert logs[0].retrievability is None and 0 < logs[-1].retrievability <= 1
    assert set(cards.preview(card)) == {1, 2, 3, 4}


async def test_review_api_and_due_queue(auth_client, setup):
    await auth_client.post(f"/api/entities/{setup['fallo']['id']}/flashcards/generate", json={})
    due = (await auth_client.get("/api/study/due")).json()
    assert len(due) == 1 and set(due[0]["preview"]) == {"1", "2", "3", "4"}
    r = await auth_client.post(f"/api/flashcards/{due[0]['id']}/review", json={"rating": 4})
    assert r.status_code == 200
    assert (await auth_client.get("/api/study/due")).json() == []
    s = (await auth_client.get("/api/study/summary")).json()
    assert s["reviewed_today"] == 1 and s["active"] == 1 and s["due_now"] == 0
    hist = (await auth_client.get(f"/api/flashcards/{due[0]['id']}/reviews")).json()
    assert [h["rating"] for h in hist] == [4]
    bad = await auth_client.post(f"/api/flashcards/{due[0]['id']}/review", json={"rating": 7})
    assert bad.status_code == 422


async def test_ai_cards_are_proposals_with_verified_quotes(auth_client, setup, model, db):
    r = await auth_client.post(
        f"/api/entities/{setup['fallo']['id']}/flashcards/generate", json={"ai": True}
    )
    assert r.json()["job_id"]
    await run_jobs()
    proposed = (await auth_client.get("/api/flashcards?status_filter=proposed")).json()
    assert [c["front"] for c in proposed] == [
        "¿Qué RIR maximiza la hipertrofia?"
    ]  # invented dropped
    assert proposed[0]["origin"] == "ai" and proposed[0]["reference"]
    assert (await auth_client.get("/api/study/due")).json()[0]["origin"] == "auto"  # not proposals
    r = await auth_client.patch(f"/api/flashcards/{proposed[0]['id']}", json={"status": "active"})
    assert r.json()["status"] == "active"


async def test_module_quiz_is_corrected_with_the_source(auth_client, setup, model, db):
    r = await auth_client.post(f"/api/modules/{setup['module'].id}/quizzes")
    assert r.status_code == 202
    await run_jobs()
    quiz = (await auth_client.get(f"/api/quizzes/{r.json()['id']}")).json()
    assert quiz["status"] == "ready" and quiz["stats"]["questions"] == 3
    assert quiz["stats"]["dropped"] == 2  # invalid options and invented quote
    assert all("correct" not in q for q in quiz["questions"])  # answers hidden while taking it
    choice, tf, short = quiz["questions"]
    res = (
        await auth_client.post(
            f"/api/quizzes/{quiz['id']}/attempts",
            json={
                "answers": [
                    {"question_id": choice["id"], "answer": 0},
                    {"question_id": tf["id"], "answer": True},
                    {"question_id": short["id"], "answer": "Por la fatiga"},
                ]
            },
        )
    ).json()
    items = {i["question_id"]: i for i in res["items"]}
    assert items[choice["id"]]["correct"] is True and items[tf["id"]]["correct"] is False
    assert items[short["id"]]["correct"] is None and res["finished_at"] is None
    for i in res["items"]:  # every correction shows the passage of the source
        seg = await db.get(SourceSegment, uuid.UUID(i["reference"]["segment_id"]))
        assert i["quote"] in seg.text and i["reference"]["source_title"] == "PRUEBA clase"
    assert items[choice["id"]]["concept"]["name"] == "Proximidad al fallo"
    res = (
        await auth_client.patch(
            f"/api/quiz-answers/{items[short['id']]['answer_id']}", json={"correct": True}
        )
    ).json()
    assert res["score"] == pytest.approx(0.667, abs=0.001) and res["finished_at"]


async def test_quiz_needs_module_materials(auth_client, db, setup):
    empty = CourseModule(course_id=setup["course"].id, title="Vacío")
    db.add(empty)
    await db.commit()
    r = await auth_client.post(f"/api/modules/{empty.id}/quizzes")
    assert r.status_code == 409


async def test_gap_report_separates_library_from_learning(auth_client, setup, db):
    # library gap: a concept with no sourced claims; another one with a single source
    await entity(auth_client, "Periodización ondulante")
    fid = uuid.UUID(setup["fallo"]["id"])
    # learning gap: the well-documented-enough concept the user keeps forgetting
    card = cards.add_card(db, entity_id=fid, front="¿Q?", back="R", kind="qa", origin="user")
    await db.commit()
    t = datetime.now(UTC) - timedelta(days=10)
    for i, rating in enumerate([1, 1, 3, 1]):
        await cards.review(db, card, rating, now=t + timedelta(hours=i))
    rep = (await auth_client.get("/api/study/gaps")).json()
    lib = {g["name"]: g for g in rep["library_gaps"]}
    learn = {g["name"]: g for g in rep["learning_gaps"]}
    assert lib["Periodización ondulante"]["kind"] == "sin fuentes"
    assert lib["Proximidad al fallo"]["kind"] == "poco desarrollado"  # one source, 2 claims
    assert "una sola fuente" in lib["Proximidad al fallo"]["reasons"]
    assert "Proximidad al fallo" in learn and "Periodización ondulante" not in learn
    assert any("otra vez" in r for r in learn["Proximidad al fallo"]["reasons"])
    assert rep["thresholds"]["min_sources"] == gaps.MIN_SOURCES
    # scoped to the course: only concepts its materials support
    scoped = (await auth_client.get(f"/api/study/gaps?course_id={setup['course'].id}")).json()
    assert scoped["concepts"] == 1
    assert await db.scalar(select(func.count()).select_from(Flashcard)) == 1
