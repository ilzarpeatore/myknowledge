import uuid

import pytest
from sqlalchemy import select, text

import kos.ingestion.pipeline  # noqa: F401  (registers job handlers)
from kos.config import get_settings
from kos.db import get_sessionmaker
from kos.jobs import worker
from kos.knowledge import service as ks
from kos.models import SourceSegment

TEXT = (
    "# Fatiga\n\nLa fatiga periférica reduce la producción de fuerza del músculo.\n\n"
    "## Proximidad al fallo\n\nEntrenar cerca del fallo aumenta la fatiga acumulada."
)


async def run_jobs():
    while await worker.run_once(get_sessionmaker(), get_settings(), "test"):
        pass


@pytest.fixture
async def segments(auth_client, db):
    r = await auth_client.post("/api/sources", files={"file": ("curso.md", TEXT.encode())})
    await run_jobs()
    segs = (await db.scalars(select(SourceSegment).order_by(SourceSegment.ordinal))).all()
    assert len(segs) == 2
    return {"src": r.json()["source"], "fatiga": segs[0], "fallo": segs[1]}


async def entity(client, name, type_key="concept", aliases=()):
    r = await client.post(
        "/api/entities",
        json={"type_key": type_key, "name": name, "aliases": [{"alias": a} for a in aliases]},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_normalize_ignores_case_accents_and_punctuation():
    assert ks.normalize("  Proximidad-al  FALLO ") == "proximidad al fallo"
    assert ks.normalize("Periodización") == ks.normalize("periodizacion")


async def test_exact_duplicates_are_refused_with_the_existing_entity(auth_client):
    a = await entity(auth_client, "Proximidad al fallo", aliases=["RIR"])
    for name in ("proximidad al FALLO", "Proximidad-al-fallo", "rir"):
        r = await auth_client.post("/api/entities", json={"type_key": "concept", "name": name})
        assert r.status_code == 409
        assert r.json()["existing_id"] == a["id"]


async def test_similar_names_are_reported_as_candidates(auth_client):
    a = await entity(auth_client, "Reclutamiento de unidades motoras")
    await entity(auth_client, "Volumen de entrenamiento")
    cands = (
        await auth_client.get(
            "/api/entities/candidates", params={"name": "reclutamiento unidades motoras"}
        )
    ).json()
    assert cands[0]["id"] == a["id"] and cands[0]["score"] > 0.5
    assert len(cands) == 1


async def test_alias_cannot_belong_to_two_entities(auth_client):
    await entity(auth_client, "Repeticiones en reserva", aliases=["RIR"])
    b = await entity(auth_client, "Esfuerzo percibido")
    r = await auth_client.post(f"/api/entities/{b['id']}/aliases", json={"alias": "rir"})
    assert r.status_code == 409


async def test_claim_quotes_are_verified_against_the_segment(auth_client, segments, db):
    e = await entity(auth_client, "Fatiga periférica")
    seg = segments["fatiga"]
    quote = "reduce la producción de fuerza"
    ok = await auth_client.post(
        "/api/claims",
        json={
            "text": "La fatiga periférica reduce la fuerza",
            "claim_type": "reported_fact",
            "entities": [{"id": e["id"]}],
            "evidence": [{"segment_id": str(seg.id), "quote": quote}],
        },
    )
    assert ok.status_code == 201, ok.text
    bad = await auth_client.post(
        "/api/claims",
        json={
            "text": "x",
            "claim_type": "opinion",
            "entities": [{"id": e["id"]}],
            "evidence": [{"segment_id": str(seg.id), "quote": "reduce mucho la fuerza"}],
        },
    )
    assert bad.status_code == 422
    no_ev = await auth_client.post(
        "/api/claims",
        json={"text": "x", "claim_type": "opinion", "entities": [{"id": e["id"]}], "evidence": []},
    )
    assert no_ev.status_code == 422

    detail = (await auth_client.get(f"/api/entities/{e['slug']}")).json()
    ev = detail["claims"][0]["evidence"][0]
    assert ev["quote"] == quote and ev["source_title"] == "curso" and ev["section"] == "Fatiga"
    start = seg.text.find(quote)
    row = (await db.execute(text("SELECT quote_char_start, quote_char_end FROM evidence"))).one()
    assert (row[0], row[1]) == (start, start + len(quote))


async def test_relationships_need_evidence_and_symmetric_ones_are_stored_once(
    auth_client, segments
):
    a = await entity(auth_client, "Fatiga")
    b = await entity(auth_client, "Proximidad al fallo")
    seg = str(segments["fallo"].id)
    no_ev = await auth_client.post(
        "/api/relationships",
        json={"source_id": b["id"], "target_id": a["id"], "type_key": "influences", "evidence": []},
    )
    assert no_ev.status_code == 422
    r = await auth_client.post(
        "/api/relationships",
        json={
            "source_id": b["id"],
            "target_id": a["id"],
            "type_key": "influences",
            "evidence": [{"segment_id": seg}],
        },
    )
    assert r.status_code == 201 and r.json()["status"] == "verified"
    s1 = await auth_client.post(
        "/api/relationships",
        json={
            "source_id": a["id"],
            "target_id": b["id"],
            "type_key": "associated_with",
            "evidence": [{"segment_id": seg}],
        },
    )
    s2 = await auth_client.post(
        "/api/relationships",
        json={
            "source_id": b["id"],
            "target_id": a["id"],
            "type_key": "associated_with",
            "evidence": [{"segment_id": seg}],
        },
    )
    assert s1.json()["id"] == s2.json()["id"]
    rels = (await auth_client.get(f"/api/entities/{a['id']}")).json()["relationships"]
    labels = sorted(r["label"] for r in rels)
    assert labels == ["está influido por", "se asocia con"]


async def _state(db):
    """Snapshot of all knowledge rows, to compare before/after merge+undo."""
    out = {}
    for t in (
        "entities",
        "entity_aliases",
        "claim_entities",
        "relationships",
        "relationship_evidence",
    ):
        cols = "*" if t != "entities" else "id, canonical_name, status, merged_into_id"
        rows = (await db.execute(text(f"SELECT {cols} FROM {t}"))).mappings().all()  # noqa: S608
        out[t] = sorted(tuple(sorted((k, str(v)) for k, v in r.items())) for r in rows)
    return out


async def test_merge_moves_everything_and_undo_restores_exactly(auth_client, segments, db):
    seg = str(segments["fallo"].id)
    src = await entity(
        auth_client, "Activación de unidades motoras", aliases=["motor unit activation"]
    )
    tgt = await entity(auth_client, "Reclutamiento de unidades motoras", aliases=["MU recruitment"])
    other = await entity(auth_client, "Fuerza")
    # Same normalised alias on both (the API forbids it, but legacy/AI data may have it):
    # it must be dropped on merge and restored on undo.
    await auth_client.post(
        f"/api/entities/{tgt['id']}/aliases", json={"alias": "Activación motora"}
    )
    await db.execute(
        text(
            "INSERT INTO entity_aliases (id, entity_id, alias, alias_normalized, kind) "
            "VALUES (:id, :e, 'activacion motora!', 'activacion motora', 'synonym')"
        ),
        {"id": uuid.uuid4(), "e": uuid.UUID(src["id"])},
    )
    await db.commit()
    # relationship that will clash with an identical one on the target
    for s in (src, tgt):
        await auth_client.post(
            "/api/relationships",
            json={
                "source_id": s["id"],
                "target_id": other["id"],
                "type_key": "contributes_to",
                "evidence": [{"segment_id": seg, "note": s["canonical_name"]}],
            },
        )
    # relationship between the two merged entities -> becomes a self-loop and is dropped
    await auth_client.post(
        "/api/relationships",
        json={
            "source_id": src["id"],
            "target_id": tgt["id"],
            "type_key": "associated_with",
            "evidence": [{"segment_id": seg}],
        },
    )
    # a claim linked to both, and one only to the source
    for ents in ([src, tgt], [src]):
        await auth_client.post(
            "/api/claims",
            json={
                "text": "Aumenta con la fatiga",
                "claim_type": "interpretation",
                "entities": [{"id": e["id"]} for e in ents],
                "evidence": [{"segment_id": seg, "quote": "aumenta la fatiga acumulada"}],
            },
        )

    before = await _state(db)
    r = await auth_client.post(f"/api/entities/{src['id']}/merge", json={"into": tgt["id"]})
    assert r.status_code == 200, r.text
    merge_id = r.json()["merge_id"]

    merged = (await auth_client.get(f"/api/entities/{tgt['id']}")).json()
    names = {a["alias"] for a in merged["aliases"]}
    assert {"Activación de unidades motoras", "motor unit activation"} <= names
    assert len(merged["claims"]) == 2
    contrib = [r for r in merged["relationships"] if r["type_key"] == "contributes_to"]
    assert len(contrib) == 1  # clash folded into one relationship
    assert not [r for r in merged["relationships"] if r["type_key"] == "associated_with"]
    old = (await auth_client.get(f"/api/entities/{src['id']}")).json()
    assert old["status"] == "merged" and old["merged_into"]["id"] == tgt["id"]
    # the merged name now resolves to the target
    dup = await auth_client.post(
        "/api/entities", json={"type_key": "concept", "name": "activación de unidades motoras"}
    )
    assert dup.json()["existing_id"] == tgt["id"]

    assert (await auth_client.post(f"/api/merges/{merge_id}/undo")).status_code == 200
    db.expire_all()
    assert await _state(db) == before
    assert (await auth_client.post(f"/api/merges/{merge_id}/undo")).status_code == 404


async def test_duplicate_scan_review_and_rejection_memory(auth_client):
    a = await entity(auth_client, "Periodización ondulante")
    b = await entity(auth_client, "Periodizacion ondulante diaria")
    await entity(auth_client, "Nutrición")
    assert (await auth_client.post("/api/review-tasks/scan-duplicates")).json()["created"] == 1
    assert (await auth_client.post("/api/review-tasks/scan-duplicates")).json()["created"] == 0
    task = (await auth_client.get("/api/review-tasks")).json()[0]
    assert {task["payload"]["name_a"], task["payload"]["name_b"]} == {
        a["canonical_name"],
        b["canonical_name"],
    }

    r = await auth_client.post(f"/api/review-tasks/{task['id']}/resolve", json={"action": "reject"})
    assert r.json()["status"] == "rejected"
    assert (await auth_client.post("/api/review-tasks/scan-duplicates")).json()["created"] == 0


async def test_accepting_a_duplicate_merges_into_the_chosen_entity(auth_client):
    a = await entity(auth_client, "Periodización ondulante")
    b = await entity(auth_client, "Periodizacion ondulante diaria")
    await auth_client.post("/api/review-tasks/scan-duplicates")
    task = (await auth_client.get("/api/review-tasks")).json()[0]
    bad = await auth_client.post(
        f"/api/review-tasks/{task['id']}/resolve", json={"action": "accept"}
    )
    assert bad.status_code == 400
    r = await auth_client.post(
        f"/api/review-tasks/{task['id']}/resolve",
        json={"action": "accept", "keep_entity_id": a["id"]},
    )
    assert r.json()["status"] == "accepted" and r.json()["payload"]["merge_id"]
    assert (await auth_client.get(f"/api/entities/{b['id']}")).json()["status"] == "merged"


async def test_ai_relationships_go_to_review(auth_client, segments, db):
    a = await entity(auth_client, "Volumen")
    b = await entity(auth_client, "Hipertrofia")
    rel = await ks.add_relationship(
        db,
        source_id=uuid.UUID(a["id"]),
        target_id=uuid.UUID(b["id"]),
        type_key="contributes_to",
        evidence=[{"segment_id": segments["fallo"].id}],
        origin="ai",
        confidence=0.7,
    )
    assert rel.status == "proposed"
    task = (await auth_client.get("/api/review-tasks")).json()[0]
    assert task["kind"] == "relationship"
    r = await auth_client.post(f"/api/review-tasks/{task['id']}/resolve", json={"action": "accept"})
    assert r.json()["status"] == "accepted"
    rels = (await auth_client.get(f"/api/entities/{a['id']}")).json()["relationships"]
    assert rels[0]["status"] == "verified" and rels[0]["origin"] == "ai"


async def test_cited_sources_cannot_be_reprocessed(auth_client, segments):
    e = await entity(auth_client, "Fatiga periférica")
    await auth_client.post(
        "/api/claims",
        json={
            "text": "t",
            "claim_type": "reported_fact",
            "entities": [{"id": e["id"]}],
            "evidence": [
                {"segment_id": str(segments["fatiga"].id), "quote": "La fatiga periférica"}
            ],
        },
    )
    r = await auth_client.post(f"/api/sources/{segments['src']['id']}/reprocess")
    assert r.status_code == 409 and "citados" in r.json()["detail"]


async def test_knowledge_endpoints_require_session(client):
    for path in ("/api/entities", "/api/review-tasks", "/api/knowledge/types"):
        assert (await client.get(path)).status_code == 401
