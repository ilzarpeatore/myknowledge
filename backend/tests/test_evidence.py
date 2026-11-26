"""Phase 4: studies, provenance of claims, quote audit, contradictions with context.

Crossref is never called: a MockTransport serves a real response saved in
tests/fixtures/crossref_work.json (trimmed).
"""

import json
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text

from kos.evidence import doi as doi_mod
from kos.evidence import service as ev
from kos.knowledge import service as ks
from kos.models import AuditLog, Claim, Person, Publication, SourceSegment

from .test_knowledge import entity, run_jobs

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "crossref_work.json").read_text())
DOI = "10.1519/JSC.0000000000001796"


@pytest.fixture
def crossref(monkeypatch):
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.lower() == f"/works/{DOI.lower()}":
            return httpx.Response(200, json=FIXTURE)
        if "unavailable" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(404, text="Resource not found.")

    monkeypatch.setattr(doi_mod, "TRANSPORT", httpx.MockTransport(handle))
    return calls


def test_doi_normalization():
    for v in (DOI, f"doi:{DOI}", f"https://doi.org/{DOI}", f" http://dx.doi.org/{DOI} "):
        assert doi_mod.normalize_doi(v) == DOI.lower()
    with pytest.raises(doi_mod.InvalidDOI):
        doi_mod.normalize_doi("JSC.0000000000001796")


def test_crossref_record_is_parsed_into_plain_fields():
    m = doi_mod.parse_work(FIXTURE["message"])
    assert m.doi == DOI.lower() and m.year == 2018
    assert m.title.startswith("The Age-Related Performance Decline in Ironman")
    assert m.journal == "Journal of Strength and Conditioning Research"
    assert m.authors_text.startswith("Käch, Ilja W.; Rüst, Christoph A.")
    assert m.record_type == "journal-article"
    assert "<" not in m.abstract and not m.abstract.lower().startswith("abstract")


async def test_study_from_doi_starts_unassessed_and_only_the_user_assesses_it(
    auth_client, crossref, db
):
    preview = (await auth_client.post("/api/publications/lookup", json={"doi": DOI})).json()
    assert preview["existing_id"] is None and preview["metadata"]["year"] == 2018
    assert await db.scalar(select(Publication.id)) is None  # lookup saves nothing
    # Only the DOI is sent; no personal data in the request.
    assert crossref[0].headers["user-agent"] == doi_mod.USER_AGENT
    assert "mailto" not in str(crossref[0].url)

    r = await auth_client.post("/api/publications/from-doi", json={"doi": f"https://doi.org/{DOI}"})
    assert r.status_code == 201, r.text
    pub = r.json()
    assert pub["metadata_source"] == "crossref" and pub["methodological_quality"] == "not_assessed"
    assert pub["quality_assessed_at"] is None

    again = await auth_client.post("/api/publications/from-doi", json={"doi": DOI})
    assert again.status_code == 409 and again.json()["existing"]["id"] == pub["id"]

    r = await auth_client.patch(
        f"/api/publications/{pub['id']}",
        json={
            "methodological_quality": "moderate",
            "quality_notes": "Observacional, sin control",
            "design": "Transversal",
            "population": "Triatletas Ironman",
            "sample_size": 1500,
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert out["methodological_quality"] == "moderate" and out["quality_assessed_at"]
    log = await db.scalar(select(AuditLog).where(AuditLog.action == "publication.updated"))
    assert log.before["methodological_quality"] == "not_assessed"

    bad = await auth_client.patch(
        f"/api/publications/{pub['id']}", json={"methodological_quality": "excellent"}
    )
    assert bad.status_code == 422


async def test_doi_errors(auth_client, crossref):
    r = await auth_client.post("/api/publications/lookup", json={"doi": "10.1234/missing"})
    assert r.status_code == 404
    r = await auth_client.post("/api/publications/lookup", json={"doi": "10.1234/unavailable"})
    assert r.status_code == 502
    r = await auth_client.post("/api/publications/lookup", json={"doi": "nada"})
    assert r.status_code == 400


async def test_manual_study_and_search(auth_client):
    r = await auth_client.post(
        "/api/publications",
        json={"title": "Efecto del RIR en la hipertrofia", "year": 2021, "study_type": "ECA"},
    )
    assert r.status_code == 201
    assert r.json()["metadata_source"] == "manual"
    assert r.json()["methodological_quality"] == "not_assessed"
    found = (await auth_client.get("/api/publications?q=RIR")).json()
    assert [p["title"] for p in found] == ["Efecto del RIR en la hipertrofia"]


async def _source(client, db, name, body):
    r = await client.post("/api/sources", files={"file": (name, body.encode())})
    await run_jobs()
    sid = r.json()["source"]["id"]
    seg = await db.scalar(
        select(SourceSegment).where(SourceSegment.text.contains(body.split(". ")[0][-20:]))
    )
    return sid, seg


async def test_provenance_filter_and_study_link(auth_client, db):
    """A study source's claims become the study's; unlinking undoes exactly that;
    each claim in the concept page says where it comes from."""
    rir = await entity(auth_client, "RIR")
    study_src, study_seg = await _source(
        auth_client, db, "estudio.md", "Los grupos a 0 RIR y 3 RIR ganaron masa similar."
    )
    course_src, course_seg = await _source(
        auth_client, db, "curso.md", "Entrena siempre a 0 RIR para crecer."
    )
    teacher = Person(name="Prof. Pérez", role="teacher")
    db.add(teacher)
    await db.commit()

    c_study = await ks.add_claim(
        db,
        text_="0 y 3 RIR dan hipertrofia similar",
        claim_type="reported_fact",
        entities=[(uuid.UUID(rir["id"]), "subject")],
        evidence=[(study_seg.id, "ganaron masa similar")],
    )
    await ks.add_claim(
        db,
        text_="Hay que entrenar a 0 RIR",
        claim_type="recommendation",
        entities=[(uuid.UUID(rir["id"]), "subject")],
        evidence=[(course_seg.id, "Entrena siempre a 0 RIR")],
        person_id=teacher.id,
    )
    await ks.add_claim(
        db,
        text_="Probablemente el RIR importa poco",
        claim_type="ai_inference",
        entities=[(uuid.UUID(rir["id"]), "subject")],
        evidence=[(study_seg.id, "masa similar")],
    )

    pub = (await auth_client.post("/api/publications", json={"title": "Estudio RIR"})).json()
    r = await auth_client.put(
        f"/api/sources/{study_src}/publication", json={"publication_id": pub["id"]}
    )
    assert r.json()["claims_changed"] == 2  # both claims cited only from the study

    detail = (await auth_client.get(f"/api/entities/{rir['slug']}")).json()
    kinds = {c["text"]: c["provenance"]["kind"] for c in detail["claims"]}
    assert kinds == {
        "0 y 3 RIR dan hipertrofia similar": "study",
        "Hay que entrenar a 0 RIR": "teacher",
        "Probablemente el RIR importa poco": "study",
    }
    study_claim = next(c for c in detail["claims"] if c["id"] == str(c_study.id))
    assert study_claim["provenance"]["publication"]["methodological_quality"] == "not_assessed"
    sources = (await auth_client.get("/api/sources")).json()["items"]
    assert {s["id"]: s["publication_id"] for s in sources}[study_src] == pub["id"]

    # Comparison: the study is its own perspective, with its context.
    await auth_client.patch(
        f"/api/publications/{pub['id']}", json={"design": "ECA", "population": "Jóvenes entrenados"}
    )
    comp = (await auth_client.get(f"/api/entities/{rir['id']}/comparison")).json()
    study_group = next(g for g in comp["perspectives"] if g["kind"] == "study")
    assert study_group["label"] == "Estudio RIR"
    assert "población: Jóvenes entrenados" in study_group["claims"][0]["context"]
    assert "calidad metodológica: no evaluada" in study_group["claims"][0]["context"]

    r = await auth_client.put(
        f"/api/sources/{study_src}/publication", json={"publication_id": None}
    )
    assert r.json()["claims_changed"] == 2
    assert await db.scalar(select(Claim.id).where(Claim.publication_id.is_not(None))) is None
    detail = (await auth_client.get(f"/api/entities/{rir['slug']}")).json()
    assert {c["provenance"]["kind"] for c in detail["claims"]} == {"source", "teacher", "ai"}


async def test_extraction_from_a_study_source_attributes_claims_to_the_study(
    auth_client, db, monkeypatch
):
    from kos.ai import providers

    from .test_extraction import TEXT, SimulatedModel, extract

    model = SimulatedModel()
    monkeypatch.setattr(providers, "get_provider", lambda: model)
    r = await auth_client.post("/api/sources", files={"file": ("paper.md", TEXT.encode())})
    await run_jobs()
    sid = r.json()["source"]["id"]
    pub = await ev.create(db, {"title": "Fatiga y fallo (2020)"})
    await auth_client.put(f"/api/sources/{sid}/publication", json={"publication_id": str(pub.id)})
    await extract(auth_client, sid)
    claims = (await db.scalars(select(Claim))).all()
    assert claims and {c.publication_id for c in claims} == {pub.id}
    assert {c.attributed_person_id for c in claims} == {None}
    assert "texto de un estudio científico" in model.calls[0]["user_content"][0]["text"]


async def test_quote_audit_detects_any_mismatch(auth_client, db):
    rir = await entity(auth_client, "RIR")
    _, seg = await _source(auth_client, db, "a.md", "El RIR mide las repeticiones en reserva.")
    await ks.add_claim(
        db,
        text_="Definición de RIR",
        claim_type="definition",
        entities=[(uuid.UUID(rir["id"]), "subject")],
        evidence=[(seg.id, "repeticiones en reserva")],
    )
    r = (await auth_client.get("/api/evidence/audit")).json()
    assert r == {"checked": 1, "mismatches": [], "ok": True}
    # Simulate corruption behind the application's back.
    await db.execute(text("UPDATE evidence SET quote = 'repeticiones de reserva'"))
    await db.commit()
    r = (await auth_client.get("/api/evidence/audit")).json()
    assert r["ok"] is False and len(r["mismatches"]) == 1


async def test_evidence_endpoints_require_session(client):
    for method, url in (
        ("get", "/api/publications"),
        ("get", "/api/evidence/audit"),
        ("post", "/api/publications/lookup"),
    ):
        r = await getattr(client, method)(
            url, **({"json": {"doi": DOI}} if method == "post" else {})
        )
        assert r.status_code == 401
