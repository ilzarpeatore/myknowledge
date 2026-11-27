"""Crossing and analysing content on a synthetic library (PRUEBA): similar passages,
coverage, consensus with evidence level, link proposals with their kind, automatic
contradictions, checks of teachers against studies, what a source adds, teacher
profiles. The vectors and the AI are simulated; what is tested is the logic."""

import re
import uuid

import pytest
from sqlalchemy import select

from kos import embeddings
from kos.ai import providers
from kos.ai.providers import ProviderError, ProviderResult, Usage
from kos.analysis import ai_jobs as aj
from kos.analysis.ai_jobs import CheckVerdict, CheckVerdicts, LinkKind, PairVerdict, PairVerdicts
from kos.evidence import service as ev
from kos.knowledge import service as ks
from kos.models import Contradiction, Person, Source, SourceSegment

from .test_knowledge import entity, run_jobs

TOPICS = [
    "rir",
    "hipert",
    "fallo",
    "fatiga",
    "recupe",
    "protei",
    "siempr",
    "misma",
    "kilo",
    "volume",
]


class TopicEmbedder:
    """A vector per topic word: texts with the same topic words are identical."""

    model_id = "topics:v1"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * embeddings.EMBEDDING_DIM
        for w in re.findall(r"\w+", text.lower()):
            w = w.replace("ó", "o").replace("í", "i")[:6]
            if w in TOPICS:
                v[TOPICS.index(w)] = 1.0
        v[-1] = 0.01  # never a zero vector
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v]

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture(autouse=True)
def topic_embedder(monkeypatch):
    monkeypatch.setattr(embeddings, "get_embedder", lambda: TopicEmbedder())


def _docs(kw) -> dict[str, str]:
    blob = kw["user_content"][-1]["text"]
    return dict(
        re.findall(
            r'<source_document ref="([^"]+)" label="[^"]*">\n(.*?)\n</source_document>', blob, re.S
        )
    )


class SimulatedModel:
    def __init__(self):
        self.calls = []

    async def complete_structured(self, *, schema, **kw):
        self.calls.append(schema.__name__)
        docs = _docs(kw)
        pairs = sorted({int(k[1:].split("-")[0]) for k in docs if k.startswith("p")})
        if schema is LinkKind:
            parsed = LinkKind(
                kind="expands",
                deeper="b",
                explanation="Ambas tratan la proximidad al fallo.",
                confidence=0.8,
            )
        elif schema is PairVerdicts:
            parsed = PairVerdicts(
                pairs=[
                    PairVerdict(
                        pair=n,
                        verdict="contradict"
                        if ("siempre" in docs[f"p{n}-x"]) != ("siempre" in docs[f"p{n}-y"])
                        else "agree",
                        explanation="Recomendaciones opuestas sobre llegar al fallo.",
                        confidence=0.7,
                    )
                    for n in pairs
                ]
            )
        else:
            parsed = CheckVerdicts(
                pairs=[
                    CheckVerdict(
                        pair=n,
                        verdict="contradicts" if "siempre" in docs[f"p{n}-x"] else "supports",
                        explanation="Compara la recomendación con el resultado del estudio.",
                        confidence=0.8,
                    )
                    for n in pairs
                ]
            )
        return ProviderResult(
            parsed=parsed,
            stop_reason="end_turn",
            model_served="claude-opus-5-5",
            usage=Usage(input_tokens=1000, output_tokens=200),
            request_id="req",
        )


@pytest.fixture
def model(monkeypatch):
    m = SimulatedModel()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    return m


TEXTS = {
    "PRUEBA curso A.md": "# Proximidad\n\nEntrenar a 1-3 RIR produce la misma hipertrofia que entrenar al fallo.\n\n# Fatiga\n\nEl fallo aumenta la fatiga y alarga la recuperación.",  # noqa: E501
    "PRUEBA estudio.md": "# Resultados\n\nEl grupo a 1-3 RIR ganó la misma hipertrofia que el grupo al fallo.\n\n# Discusión\n\nEl fallo aumentó la fatiga y la recuperación fue más lenta.",  # noqa: E501
    "PRUEBA curso B.md": "# Fallo\n\nHay que llegar siempre al fallo para ganar hipertrofia.\n\n# Volumen\n\nEl volumen semanal se cuenta en series.",  # noqa: E501
    "PRUEBA nutrición.md": "# Proteína\n\nLa proteína diaria ronda 1,6 g por kilo.",
}


async def seg_with(db, words):
    return await db.scalar(select(SourceSegment).where(SourceSegment.text.contains(words)))


@pytest.fixture
async def library(auth_client, db, monkeypatch):
    monkeypatch.setattr(aj, "CONTRA_MIN_SIMILARITY", 0.5)
    monkeypatch.setattr(aj, "CHECK_MIN_SIMILARITY", 0.5)
    ids = {}
    for name, text in TEXTS.items():
        r = await auth_client.post("/api/sources", files={"file": (name, text.encode())})
        ids[name.removesuffix(".md")] = uuid.UUID(r.json()["source"]["id"])
    await run_jobs()
    a, b = Person(name="Profesora A", role="teacher"), Person(name="Profesor B", role="teacher")
    db.add_all([a, b])
    await db.commit()
    fallo = uuid.UUID(
        (await entity(auth_client, "Proximidad al fallo", "principle", aliases=["RIR"]))["id"]
    )
    fatiga = uuid.UUID((await entity(auth_client, "Fatiga"))["id"])
    volumen = uuid.UUID((await entity(auth_client, "Volumen de entrenamiento"))["id"])
    pub = await ev.create(db, {"title": "Estudio sintético RIR", "year": 2024})
    await ev.update_fields(db, pub, {"methodological_quality": "high"})
    src = {k: await db.get(Source, v) for k, v in ids.items()}
    await ev.link_source(db, src["PRUEBA estudio"], pub.id)

    async def claim(text, ent, seg_words, quote, **kw):
        seg = await seg_with(db, seg_words)
        return await ks.add_claim(
            db,
            text_=text,
            claim_type="recommendation",
            entities=[(ent, "subject")],
            evidence=[(seg.id, quote)],
            **kw,
        )

    c = {
        "A": await claim(
            "1-3 RIR produce la misma hipertrofia que el fallo",
            fallo,
            "Entrenar a 1-3",
            "la misma hipertrofia",
            person_id=a.id,
        ),
        "A2": await claim(
            "El fallo aumenta la fatiga y la recuperación",
            fatiga,
            "alarga la",
            "aumenta la fatiga",
            person_id=a.id,
        ),
        "S": await claim(
            "1-3 RIR logra la misma hipertrofia que el fallo",
            fallo,
            "El grupo a 1-3",
            "la misma hipertrofia",
            publication_id=pub.id,
        ),
        "S2": await claim(
            "El fallo aumenta la fatiga y retrasa la recuperación",
            fatiga,
            "más lenta",
            "aumentó la fatiga",
            publication_id=pub.id,
        ),
        "B": await claim(
            "Hay que llegar siempre al fallo para la hipertrofia",
            fallo,
            "llegar siempre",
            "llegar siempre al fallo",
            person_id=b.id,
        ),
        "B2": await claim(
            "El volumen se cuenta en series",
            volumen,
            "series",
            "se cuenta en series",
            person_id=b.id,
        ),
    }
    return {"ids": ids, "claims": c, "fallo": fallo, "volumen": volumen, "a": a, "b": b, "pub": pub}


async def test_similar_passages_in_other_sources(auth_client, library, db):
    seg = await seg_with(db, "Entrenar a 1-3")
    r = (await auth_client.get(f"/api/segments/{seg.id}/similar")).json()
    assert r["available"] is True
    assert [i["source_title"] for i in r["items"]] == ["PRUEBA estudio"]  # same source excluded
    assert r["items"][0]["similarity"] > 0.99 and "ganó la misma" in r["items"][0]["text"]
    loose = (await auth_client.get(f"/api/segments/{seg.id}/similar?min_similarity=0.5")).json()
    assert {i["source_title"] for i in loose["items"]} == {"PRUEBA estudio", "PRUEBA curso B"}


async def test_coverage_map(auth_client, library):
    r = (await auth_client.get("/api/analysis/coverage")).json()
    ents = {e["name"]: e for e in r["entities"]}
    assert (
        r["entities"][0]["name"] == "Proximidad al fallo"
        and ents["Proximidad al fallo"]["sources"] == 3
    )
    assert ents["Volumen de entrenamiento"]["single_source"] is True
    cell = {(c["entity_id"], c["source_id"]): c["claims"] for c in r["cells"]}
    assert cell[(str(library["fallo"]), str(library["ids"]["PRUEBA curso B"]))] == 1
    # by section: only what is in Estudios
    est = (await auth_client.get("/api/analysis/coverage?section=estudios")).json()
    assert [s["title"] for s in est["sources"]] == ["PRUEBA estudio"]


async def test_consensus_groups_by_meaning_with_evidence_level(auth_client, library):
    r = (await auth_client.get(f"/api/entities/{library['fallo']}/consensus")).json()
    assert r["grouped_by_meaning"] is True and "tú asignaste" in r["note"]
    first, second = r["groups"]
    assert first["count"] == 2 and first["sources"] == 2  # teacher A + the study say the same
    assert first["teachers"] == ["Profesora A"] and first["studies"][0]["quality"] == "high"
    assert first["evidence_level"] == "moderate"  # one high-quality study (two make it «high»)
    assert second["teachers"] == ["Profesor B"] and second["evidence_level"] == "none"


async def test_analysis_proposes_links_contradictions_and_checks(auth_client, library, model, db):
    r = await auth_client.post(f"/api/sources/{library['ids']['PRUEBA curso A']}/analysis")
    assert r.status_code == 202
    await run_jobs()
    c = library["claims"]

    # 1. a link to the study (two passages say the same), named by the AI, in Revisión
    links = (await auth_client.get("/api/source-links")).json()
    assert len(links) == 1
    lk = links[0]
    assert {lk["a"]["title"], lk["b"]["title"]} == {"PRUEBA curso A", "PRUEBA estudio"}
    assert lk["kind"] == "expands" and lk["origin"] == "auto" and len(lk["evidence"]) == 2
    assert lk["evidence"][0]["a"]["text"] and lk["review_task_id"]
    r = await auth_client.post(
        f"/api/review-tasks/{lk['review_task_id']}/resolve", json={"action": "accept"}
    )
    assert r.status_code == 200
    related = (
        await auth_client.get(f"/api/sources/{library['ids']['PRUEBA curso A']}/related")
    ).json()
    linked = [x for x in related["related"] if x["link"]]
    assert linked[0]["title"] == "PRUEBA estudio" and linked[0]["link"]["status"] == "accepted"

    # 2. the teacher who says «siempre al fallo» contradicts A: a candidate from the AI
    contra = (await db.scalars(select(Contradiction))).all()
    pairs = {frozenset((x.claim_a_id, x.claim_b_id)) for x in contra}
    assert frozenset((c["A"].id, c["B"].id)) in pairs
    assert all(x.origin == "ai" and x.status == "candidate" for x in contra)

    # 3. A's claims are checked against the study claims: supported, pending review
    st = (await auth_client.get(f"/api/entities/{library['fallo']}/study-status")).json()
    a_status = st["claims"][str(c["A"].id)]
    assert a_status["status"] == "supported" and a_status["pending_review"] is True
    assert st["claims"][str(c["S"].id)]["status"] == "study"
    assert st["claims"][str(c["B"].id)]["status"] == "unchecked"  # B's source not analysed yet

    # checking the concept covers B too: contradicted by the study
    r = await auth_client.post(f"/api/entities/{library['fallo']}/study-checks")
    assert r.status_code == 202
    await run_jobs()
    st = (await auth_client.get(f"/api/entities/{library['fallo']}/study-status")).json()
    b_status = st["claims"][str(c["B"].id)]
    assert b_status["status"] == "contradicted"
    assert b_status["checks"][0]["publication"]["quality"] == "high"
    # rejecting the check takes it out of the count
    ck = b_status["checks"][0]["id"]
    assert (
        await auth_client.patch(f"/api/claim-checks/{ck}", json={"status": "rejected"})
    ).status_code == 200
    st = (await auth_client.get(f"/api/entities/{library['fallo']}/study-status")).json()
    assert st["claims"][str(c["B"].id)]["status"] == "unchecked"
    assert set(model.calls) == {"LinkKind", "PairVerdicts", "CheckVerdicts"}


async def test_what_a_source_adds_and_teacher_profile(auth_client, library, model):
    await auth_client.post(f"/api/sources/{library['ids']['PRUEBA curso B']}/analysis")
    await run_jobs()
    rep = (
        await auth_client.get(f"/api/sources/{library['ids']['PRUEBA curso B']}/contribution")
    ).json()
    assert rep["claims"] == 2
    assert [x["name"] for x in rep["concepts"]["new"]] == ["Volumen de entrenamiento"]
    assert [x["name"] for x in rep["concepts"]["shared"]] == ["Proximidad al fallo"]
    assert [x["text"] for x in rep["in_conflict"]] == [library["claims"]["B"].text]
    assert rep["against_studies"]["contradicted"] == 1 and rep["against_studies"]["no_studies"] == 1
    assert rep["analysis"]["status"] == "completed"

    rep_a = (
        await auth_client.get(f"/api/sources/{library['ids']['PRUEBA curso A']}/contribution")
    ).json()
    assert {x["text"] for x in rep_a["confirmed"]} == {
        library["claims"]["A"].text,
        library["claims"]["A2"].text,
    }

    prof = (await auth_client.get(f"/api/persons/{library['b'].id}/profile")).json()
    assert prof["claims"] == 2 and prof["summary"]["contradicted"] == 1
    assert prof["peers"][0]["name"] == "Profesora A" and prof["peers"][0]["disagree"] == 1
    assert {c["name"] for c in prof["concepts"]} == {
        "Proximidad al fallo",
        "Volumen de entrenamiento",
    }
    assert prof["sources"][0]["title"] == "PRUEBA curso B"


async def test_without_ai_links_are_still_proposed(auth_client, library, monkeypatch):
    class NoKey:
        async def complete_structured(self, **kw):
            raise ProviderError("Anthropic credentials error: no API key", retryable=False)

    monkeypatch.setattr(providers, "get_provider", lambda: NoKey())
    await auth_client.post(f"/api/sources/{library['ids']['PRUEBA curso A']}/analysis")
    await run_jobs()
    links = (await auth_client.get("/api/source-links")).json()
    assert len(links) == 1 and links[0]["kind"] == "related"
    rep = (
        await auth_client.get(f"/api/sources/{library['ids']['PRUEBA curso A']}/contribution")
    ).json()
    assert rep["analysis"]["status"] == "completed"
    assert "IA no disponible" in rep["analysis"]["result"]["contradictions"]["skipped"]
    # a rejected proposal is not proposed again
    await auth_client.patch(f"/api/source-links/{links[0]['id']}", json={"status": "rejected"})
    await auth_client.post(f"/api/sources/{library['ids']['PRUEBA curso A']}/analysis")
    await run_jobs()
    assert (await auth_client.get("/api/source-links")).json() == []


async def test_extraction_queues_the_analysis(auth_client, db):
    from kos.knowledge import extraction  # noqa: F401 - the hook lives there

    src = inspect_hook()
    assert "analyze_job" in src


def inspect_hook() -> str:
    import inspect

    from kos.knowledge import extraction

    return inspect.getsource(extraction)


def test_contradicting_claims_never_share_a_group():
    import numpy as np

    from kos.analysis.vectors import group

    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    same = np.ones(4, dtype=np.float32)  # the model sees the same topic in all three
    vecs = {a: same, b: same, c: same}
    assert group([a, b, c], vecs, 0.95) == [[a, b, c]]
    assert group([a, b, c], vecs, 0.95, apart={a: {b}, b: {a}}) == [[a, c], [b]]
