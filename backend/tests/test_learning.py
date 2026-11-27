"""Learning Center, including the Phase 3 acceptance criteria (ROADMAP)."""

from sqlalchemy import select

import kos.ingestion.pipeline  # noqa: F401  (registers job handlers)
from kos.config import get_settings
from kos.db import get_sessionmaker
from kos.jobs import worker
from kos.models import SourceSegment, SourceVersion


async def run_jobs():
    while await worker.run_once(get_sessionmaker(), get_settings(), "test"):
        pass


async def upload(client, name, body):
    r = await client.post("/api/sources", files={"file": (name, body.encode())})
    assert r.status_code == 200, r.text
    return r.json()["source"]


async def segment_of(db, source_id):
    return await db.scalar(
        select(SourceSegment)
        .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
        .where(SourceVersion.source_id == source_id)
        .order_by(SourceSegment.ordinal)
    )


async def build_course(client, title, program_id=None):
    c = (
        await client.post(
            "/api/courses", json={"title": title, "program_id": program_id, "status": "in_progress"}
        )
    ).json()
    m = (await client.post(f"/api/courses/{c['id']}/modules", json={"title": "Módulo 1"})).json()
    l1 = (
        await client.post(f"/api/modules/{m['id']}/lessons", json={"title": "Lección 1.1"})
    ).json()
    l2 = (
        await client.post(f"/api/modules/{m['id']}/lessons", json={"title": "Lección 1.2"})
    ).json()
    return c, m, l1, l2


async def test_disciplines_seed_is_idempotent(auth_client):
    r1 = (await auth_client.post("/api/disciplines/seed-training-science")).json()
    r2 = (await auth_client.post("/api/disciplines/seed-training-science")).json()
    assert r1["created"] == 12 and r2["created"] == 0
    names = {d["name"] for d in (await auth_client.get("/api/disciplines")).json()}
    assert {"Ciencias del entrenamiento", "Hipertrofia", "Biomecánica"} <= names


async def test_course_structure_and_ordering(auth_client):
    c, m, l1, l2 = await build_course(auth_client, "Hipertrofia avanzada")
    assert (l1["position"], l2["position"]) == (0, 1)
    detail = (await auth_client.get(f"/api/courses/{c['id']}")).json()
    assert [x["title"] for x in detail["modules"][0]["lessons"]] == ["Lección 1.1", "Lección 1.2"]
    assert detail["progress"] == {"lessons": 2, "completed": 0, "minutes": 0, "percent": 0}


async def test_progress_is_recorded_and_aggregated(auth_client):
    prog = (await auth_client.post("/api/programs", json={"name": "Máster en fuerza"})).json()
    c, m, l1, l2 = await build_course(auth_client, "Curso A", prog["id"])
    r = await auth_client.put(
        f"/api/lessons/{l1['id']}/progress",
        json={"status": "completed", "add_minutes": 45, "notes": "Repasar RIR"},
    )
    assert r.json()["time_spent_minutes"] == 45
    await auth_client.put(f"/api/lessons/{l1['id']}/progress", json={"add_minutes": 15})
    detail = (await auth_client.get(f"/api/courses/{c['id']}")).json()
    p1 = detail["modules"][0]["lessons"][0]["progress"]
    assert (
        p1["status"] == "completed"
        and p1["time_spent_minutes"] == 60
        and p1["notes"] == "Repasar RIR"
    )
    assert detail["progress"] == {"lessons": 2, "completed": 1, "minutes": 60, "percent": 50}
    programs = (await auth_client.get("/api/programs")).json()
    assert programs[0]["percent"] == 50 and programs[0]["courses"] == 1
    ov = (await auth_client.get("/api/learning/overview")).json()
    assert ov["in_progress"][0]["title"] == "Curso A" and ov["counts"]["courses"] == 1


async def test_material_lesson_must_belong_to_course(auth_client):
    c1, *_ = await build_course(auth_client, "Curso 1")
    _, _, other_lesson, _ = await build_course(auth_client, "Curso 2")
    src = await upload(auth_client, "x.md", "# X\n\ntexto")
    r = await auth_client.post(
        f"/api/courses/{c1['id']}/materials",
        json={"source_id": src["id"], "lesson_id": other_lesson["id"]},
    )
    assert r.status_code == 400
    ok = await auth_client.post(f"/api/courses/{c1['id']}/materials", json={"source_id": src["id"]})
    assert ok.status_code == 201
    dup = await auth_client.post(
        f"/api/courses/{c1['id']}/materials", json={"source_id": src["id"]}
    )
    assert dup.status_code == 409


async def test_phase3_concepts_link_courses_and_lessons_both_ways(auth_client, db):
    """ROADMAP Fase 3: a course's concepts link to their lesson; from a concept you
    reach every course/lesson treating it; the same concept in two courses is one
    entity linked to both."""
    ca, _, la1, _ = await build_course(auth_client, "Curso del profesor A")
    cb, _, lb1, _ = await build_course(auth_client, "Curso del profesor B")
    sa = await upload(auth_client, "a.md", "# Fallo\n\nEntrenar a 0-3 RIR es suficiente.")
    sb = await upload(auth_client, "b.md", "# RIR\n\nEl RIR mide la proximidad al fallo.")
    await run_jobs()
    await auth_client.post(
        f"/api/courses/{ca['id']}/materials", json={"source_id": sa["id"], "lesson_id": la1["id"]}
    )
    await auth_client.post(
        f"/api/courses/{cb['id']}/materials", json={"source_id": sb["id"], "lesson_id": lb1["id"]}
    )

    entity = (
        await auth_client.post(
            "/api/entities",
            json={
                "type_key": "concept",
                "name": "Proximidad al fallo",
                "aliases": [{"alias": "RIR"}],
            },
        )
    ).json()
    for src, quote in ((sa, "Entrenar a 0-3 RIR"), (sb, "El RIR mide la proximidad al fallo")):
        seg = await segment_of(db, src["id"])
        r = await auth_client.post(
            "/api/claims",
            json={
                "text": quote,
                "claim_type": "recommendation",
                "entities": [{"id": entity["id"]}],
                "evidence": [{"segment_id": str(seg.id), "quote": quote}],
            },
        )
        assert r.status_code == 201, r.text

    courses = (await auth_client.get(f"/api/entities/{entity['id']}/courses")).json()
    assert {c["title"] for c in courses} == {"Curso del profesor A", "Curso del profesor B"}
    lesson_ids = {lesson["id"] for c in courses for lesson in c["lessons"]}
    assert lesson_ids == {la1["id"], lb1["id"]}

    concepts_a = (await auth_client.get(f"/api/courses/{ca['id']}/concepts")).json()
    assert concepts_a == [
        {
            "id": entity["id"],
            "slug": entity["slug"],
            "name": "Proximidad al fallo",
            "claims": 1,
            "lessons": [la1["id"]],
        }
    ]
    names = [e["canonical_name"] for e in (await auth_client.get("/api/entities")).json()]
    assert names.count("Proximidad al fallo") == 1


async def test_deleting_a_module_removes_its_lessons_and_progress(auth_client):
    c, m, l1, _ = await build_course(auth_client, "Curso")
    await auth_client.put(f"/api/lessons/{l1['id']}/progress", json={"status": "completed"})
    assert (await auth_client.delete(f"/api/modules/{m['id']}")).status_code == 204
    assert (await auth_client.get(f"/api/courses/{c['id']}")).json()["modules"] == []


async def test_invalid_status_is_rejected(auth_client):
    r = await auth_client.post("/api/courses", json={"title": "x", "status": "done"})
    assert r.status_code == 422


async def test_learning_requires_session(client):
    for path in ("/api/courses", "/api/programs", "/api/disciplines", "/api/learning/overview"):
        assert (await client.get(path)).status_code == 401
