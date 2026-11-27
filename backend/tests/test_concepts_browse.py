"""The concepts page: every concept, ordered by importance, filters and bulk actions."""

import uuid

import pytest
from sqlalchemy import select

from kos.models import Discipline, Entity, SourceSegment

from .test_knowledge import TEXT, entity, run_jobs


@pytest.fixture
async def lib(auth_client, db):
    r = await auth_client.post("/api/sources", files={"file": ("curso.md", TEXT.encode())})
    await run_jobs()
    seg = await db.scalar(select(SourceSegment).order_by(SourceSegment.ordinal))
    return {"src": r.json()["source"], "seg": seg}


async def _claim(client, e, seg, quote):
    r = await client.post(
        "/api/claims",
        json={
            "text": quote,
            "claim_type": "reported_fact",
            "entities": [{"id": e["id"]}],
            "evidence": [{"segment_id": str(seg.id), "quote": quote}],
        },
    )
    assert r.status_code == 201, r.text


async def _branches(client, db):
    await client.post("/api/disciplines/seed-training-science")
    rows = (await db.scalars(select(Discipline).where(Discipline.parent_id.is_not(None)))).all()
    return {d.name: str(d.id) for d in rows}


async def test_every_concept_is_listed_by_importance_with_pages(auth_client, lib):
    fatiga = await entity(auth_client, "Fatiga periférica")
    await entity(auth_client, "Abducción")  # alphabetically first, but backed by nothing
    await _claim(auth_client, fatiga, lib["seg"], "reduce la producción de fuerza")
    await _claim(auth_client, fatiga, lib["seg"], "La fatiga periférica")

    r = (await auth_client.get("/api/concepts")).json()
    assert [x["canonical_name"] for x in r["items"]] == ["Fatiga periférica", "Abducción"]
    top = r["items"][0]
    assert top["sources"] == 1 and top["claims"] == 2 and top["importance"] == 5
    assert r["total"] == 2 and r["facets"]["no_discipline"] == 2

    p = (await auth_client.get("/api/concepts", params={"sort": "name", "page_size": 1})).json()
    assert p["total"] == 2 and [x["canonical_name"] for x in p["items"]] == ["Abducción"]
    p2 = await auth_client.get("/api/concepts", params={"sort": "name", "page_size": 1, "page": 2})
    assert [x["canonical_name"] for x in p2.json()["items"]] == ["Fatiga periférica"]

    lonely = await auth_client.get("/api/concepts", params={"flag": ["no_claims"]})
    assert [x["canonical_name"] for x in lonely.json()["items"]] == ["Abducción"]
    by_source = await auth_client.get("/api/concepts", params={"source_id": lib["src"]["id"]})
    assert [x["canonical_name"] for x in by_source.json()["items"]] == ["Fatiga periférica"]
    found = await auth_client.get("/api/concepts", params={"q": "fatiga"})
    assert [x["canonical_name"] for x in found.json()["items"]] == ["Fatiga periférica"]
    srcs = (await auth_client.get("/api/concepts/sources")).json()
    assert srcs == [{"id": lib["src"]["id"], "title": "curso", "concepts": 1}]


async def test_bulk_branches_types_archive_and_merge(auth_client, db):
    b = await _branches(auth_client, db)
    a = await entity(auth_client, "ejercicio de facilitación")
    c = await entity(auth_client, "ejercicios de facilitación de la cadera")
    d = await entity(auth_client, "Glucógeno")
    ids = [a["id"], c["id"]]

    async def bulk(**body):
        r = await auth_client.post("/api/concepts/bulk", json=body)
        assert r.status_code == 200, r.text
        return r.json()["changed"]

    assert await bulk(ids=ids, action="add_discipline", discipline_id=b["Biomecánica"]) == 2
    assert await bulk(ids=ids, action="add_discipline", discipline_id=b["Biomecánica"]) == 0
    two = [d["id"]]
    await bulk(ids=two, action="add_discipline", discipline_id=b["Nutrición"])
    await bulk(ids=two, action="add_discipline", discipline_id=b["Fisiología"])  # up to two
    r = (await auth_client.get("/api/concepts", params={"discipline": b["Biomecánica"]})).json()
    assert {x["canonical_name"] for x in r["items"]} == {a["canonical_name"], c["canonical_name"]}
    assert r["facets"]["disciplines"][b["Nutrición"]] == 1 and r["facets"]["no_discipline"] == 0
    glu = (await auth_client.get("/api/concepts", params={"discipline": b["Nutrición"]})).json()
    assert [x["name"] for x in glu["items"][0]["disciplines"]] == ["Fisiología", "Nutrición"]
    # the root branch includes its areas
    root = await db.scalar(select(Discipline.id).where(Discipline.parent_id.is_(None)))
    assert (await auth_client.get("/api/concepts", params={"discipline": str(root)})).json()[
        "total"
    ] == 3
    none = await auth_client.get("/api/concepts", params={"discipline": "none"})
    assert none.json()["total"] == 0

    assert await bulk(ids=two, action="remove_discipline", discipline_id=b["Fisiología"]) == 1
    assert await bulk(ids=ids, action="set_type", type_key="methodology") == 2
    bad = await auth_client.post(
        "/api/concepts/bulk", json={"ids": ids, "action": "set_type", "type_key": "nope"}
    )
    assert bad.status_code == 400

    assert await bulk(ids=ids, action="merge", into=a["id"]) == 1
    merged = await db.get(Entity, uuid.UUID(c["id"]))
    await db.refresh(merged)
    assert merged.status == "merged" and merged.merged_into_id == uuid.UUID(a["id"])

    assert await bulk(ids=[a["id"]], action="archive") == 1
    live = (await auth_client.get("/api/concepts")).json()
    assert [x["canonical_name"] for x in live["items"]] == ["Glucógeno"]
    arch = (await auth_client.get("/api/concepts", params={"status_filter": "archived"})).json()
    assert [x["canonical_name"] for x in arch["items"]] == [a["canonical_name"]]
