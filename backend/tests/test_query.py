"""Phase 6: AI workspace. A simulated model answers from what it is sent; the tests
check retrieval, the concept sheet, citation validation and the 8 pilot questions
(§19.3 of the master document) on a synthetic library."""

import re
import uuid

import pytest
from sqlalchemy import func, select

from kos.ai import providers
from kos.ai import query as aq
from kos.ai.providers import ProviderResult, Usage
from kos.knowledge import service as ks
from kos.models import (
    AIRun,
    Course,
    CourseMaterial,
    Discipline,
    Person,
    QueryRecord,
    SourceSegment,
)

from .test_knowledge import entity, run_jobs

PILOT = [
    "¿Qué he aprendido sobre proximidad al fallo?",
    "¿Qué profesores han explicado este concepto?",
    "¿En qué coinciden?",
    "¿En qué discrepan?",
    "¿Qué relación tiene con el volumen de entrenamiento?",
    "¿Qué conceptos necesito comprender para entenderlo mejor?",
    "¿Qué cursos contienen información relacionada?",
    "¿Qué información está respaldada por estudios y cuál procede de explicaciones de profesores?",
]

TEXTS = {
    "PRUEBA curso A.md": "# Proximidad al fallo\n\nEntrenar a 0-3 RIR maximiza la hipertrofia con menos fatiga.",  # noqa: E501
    "PRUEBA curso B.md": "# RIR\n\nLas repeticiones en reserva no importan si se iguala el volumen de series.",  # noqa: E501
    "PRUEBA estudio.md": "# Resultados\n\nEl grupo a 3 RIR ganó la misma masa muscular que el grupo al fallo.",  # noqa: E501
    "PRUEBA otro.md": "# Nutrición\n\nLa proteína diaria recomendada ronda 1,6 g por kilo.",
}


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
        docs = docs_in(kw)
        frags = {r: t for r, t in docs.items() if r.startswith("f")}
        parts = []
        for ref, text in list(frags.items())[:3]:
            sentence = text.split("\n")[-1].rstrip(".")
            parts.append(
                aq.Part(
                    kind="source",
                    text=f"Según {ref}: {sentence}.",
                    citations=[aq.Citation(fragment=ref, quote=sentence)],
                )
            )
        parts.append(
            aq.Part(
                kind="source",
                text="Algo que ninguna fuente dice.",
                citations=[aq.Citation(fragment="f1", quote="frase inventada por el modelo")],
            )
        )
        parts.append(aq.Part(kind="external", text="Conocimiento general.", citations=[]))
        result = aq.AnswerResult(
            coverage="sufficient",
            coverage_note="Cubierto en parte.",
            parts=parts,
            gaps=["Faltan estudios sobre mujeres."],
        )
        return ProviderResult(
            parsed=result,
            stop_reason="end_turn",
            model_served="claude-opus-5-5",
            usage=Usage(input_tokens=3000, output_tokens=600),
            request_id="req",
        )


@pytest.fixture
def model(monkeypatch):
    m = SimulatedModel()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    return m


@pytest.fixture
async def library(auth_client, db):
    ids = {}
    for name, text in TEXTS.items():
        r = await auth_client.post("/api/sources", files={"file": (name, text.encode())})
        ids[name] = uuid.UUID(r.json()["source"]["id"])
    await run_jobs()
    seg = {
        n: await db.scalar(
            select(SourceSegment).where(SourceSegment.text.contains(t.split("\n")[-1][:30]))
        )
        for n, t in TEXTS.items()
    }
    disc = Discipline(name="Hipertrofia", slug="hipertrofia")
    a, b = Person(name="Profesora A", role="teacher"), Person(name="Profesor B", role="teacher")
    db.add_all([disc, a, b])
    await db.flush()
    ca = Course(
        title="Curso de la profesora A",
        teacher_person_id=a.id,
        discipline_id=disc.id,
        status="in_progress",
    )
    cb = Course(title="Curso del profesor B", teacher_person_id=b.id, status="in_progress")
    db.add_all([ca, cb])
    await db.flush()
    db.add_all(
        [
            CourseMaterial(course_id=ca.id, source_id=ids["PRUEBA curso A.md"]),
            CourseMaterial(course_id=cb.id, source_id=ids["PRUEBA curso B.md"]),
        ]
    )
    await db.commit()
    fallo = await entity(auth_client, "Proximidad al fallo", "principle", aliases=["RIR"])
    volumen = await entity(auth_client, "Volumen de entrenamiento", aliases=["volumen"])
    fid, vid = uuid.UUID(fallo["id"]), uuid.UUID(volumen["id"])
    c1 = await ks.add_claim(
        db,
        text_="0-3 RIR maximiza la hipertrofia",
        claim_type="recommendation",
        entities=[(fid, "subject")],
        person_id=a.id,
        evidence=[(seg["PRUEBA curso A.md"].id, "Entrenar a 0-3 RIR maximiza la hipertrofia")],
    )
    c2 = await ks.add_claim(
        db,
        text_="El RIR no importa a igual volumen",
        claim_type="interpretation",
        entities=[(fid, "subject"), (vid, "object")],
        person_id=b.id,
        evidence=[(seg["PRUEBA curso B.md"].id, "no importan si se iguala el volumen de series")],
    )
    from kos.evidence import service as ev

    pub = await ev.create(db, {"title": "Estudio sintético RIR", "year": 2024})
    await ev.link_source(
        db,
        await db.get(
            __import__("kos.models", fromlist=["Source"]).Source, ids["PRUEBA estudio.md"]
        ),
        pub.id,
    )
    await ks.add_claim(
        db,
        text_="3 RIR iguala la ganancia de masa",
        claim_type="reported_fact",
        entities=[(fid, "subject")],
        publication_id=pub.id,
        evidence=[(seg["PRUEBA estudio.md"].id, "ganó la misma masa muscular")],
    )
    await ks.add_relationship(
        db, source_id=fid, target_id=vid, type_key="associated_with", evidence=[{"claim_id": c2.id}]
    )
    from kos.knowledge import compare

    await compare.add_contradiction(db, c1.id, c2.id, analysis=None, origin="user")
    return {"ids": ids, "course_a": ca, "disc": disc, "fallo": fallo, "seg": seg}


async def ask(client, question, **kw):
    r = await client.post("/api/queries", json={"question": question, **kw})
    assert r.status_code == 202, r.text
    await run_jobs()
    return (await client.get(f"/api/queries/{r.json()['id']}")).json()


async def test_pilot_questions_get_verified_citations_and_separated_parts(
    auth_client, library, model, db
):
    parent = None
    for question in PILOT:
        q = await ask(auth_client, question, parent_id=parent, mode="research")
        assert q["status"] == "completed", q
        a = q["answer"]
        sources = [p for p in a["parts"] if p["kind"] == "source"]
        assert sources and all(p["citations"] for p in sources), question
        # the invented quote was dropped and its part downgraded, visibly
        assert a["citations_dropped"] == 1
        assert any(
            p["flags"] == ["sin cita verificable"] and p["kind"] == "inference" for p in a["parts"]
        )
        assert any(
            "modo investigación" in " ".join(p["flags"])
            for p in a["parts"]
            if p["kind"] == "external"
        )
        # every stored quote is literally in its fragment
        for p in a["parts"]:
            for c in p["citations"]:
                seg = await db.get(SourceSegment, uuid.UUID(c["segment_id"]))
                assert c["quote"] in seg.text
        parent = q["id"]  # follow-up questions keep the concept

    # The model received what the structural questions need: teachers, courses,
    # study provenance with quality, relationship with volume, contradiction.
    sent = "\n".join(docs_in(model.calls[1]).values())
    for needle in (
        "Profesora A",
        "Profesor B",
        "estudio: Estudio sintético RIR (2024)",
        "calidad metodológica no evaluada",
        "Volumen de entrenamiento",
        "Curso de la profesora A",
        "Contradicción registrada",
    ):
        assert needle in sent, needle
    assert "prev" in docs_in(model.calls[1])  # follow-up carries the previous exchange
    assert await db.scalar(select(func.count()).select_from(AIRun)) == len(PILOT)
    assert all(q.ai_run_id for q in (await db.scalars(select(QueryRecord))).all())


async def test_no_relevant_material_answers_insufficient_without_calling_the_model(
    auth_client, library, model
):
    q = await ask(auth_client, "¿Qué dice la biblioteca sobre ajedrez?")
    assert q["answer"]["coverage"] == "insufficient" and model.calls == []
    assert "No se ha consultado a la IA" in q["answer"]["parts"][0]["text"]


async def test_scope_limits_the_fragments_sent(auth_client, library, model):
    await ask(
        auth_client,
        "¿Qué es la proteína y el RIR?",
        scope_kind="course",
        scope_id=str(library["course_a"].id),
    )
    assert fragment_sources(model.calls[-1]) == {"PRUEBA curso A"}
    # a discipline reaches the courses classified under it (only course A is)
    await ask(
        auth_client,
        "¿Qué es el RIR y la proteína?",
        scope_kind="discipline",
        scope_id=str(library["disc"].id),
    )
    assert fragment_sources(model.calls[-1]) == {"PRUEBA curso A"}
    # the whole library reaches every source with matching words
    await ask(auth_client, "¿Qué es el RIR y la proteína?")
    assert {"PRUEBA curso A", "PRUEBA curso B", "PRUEBA otro"} <= fragment_sources(model.calls[-1])


def fragment_sources(call) -> set[str]:
    """Source titles of the fragments (not concept sheets) sent in a call. In this test
    the scope question names no concept, so only retrieved fragments appear."""
    blob = call["user_content"][-1]["text"]
    labels = re.findall(r'<source_document ref="f\d+" label="([^"]*)"', blob)
    return {lbl.split(" · ")[0] for lbl in labels}


async def test_application_mode_allows_marked_external_knowledge(auth_client, library, model):
    q = await ask(
        auth_client, "¿Cómo aplico la proximidad al fallo a mi rutina?", mode="application"
    )
    ext = [p for p in q["answer"]["parts"] if p["kind"] == "external"]
    assert ext and ext[0]["flags"] == []
    assert "Mode: application" in model.calls[-1]["system"]


async def test_bad_scope_and_auth(auth_client, client):
    r = await auth_client.post(
        "/api/queries",
        json={"question": "¿Algo?", "scope_kind": "course", "scope_id": str(uuid.uuid4())},
    )
    assert r.status_code == 400
    client.cookies.clear()
    assert (await client.get("/api/queries")).status_code == 401
