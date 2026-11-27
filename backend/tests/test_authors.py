"""Authors of sources and the Autores library: search as you type (accents, word
starts, order, typos), no duplicates by name, merge, and filtering the library."""

import json
import uuid

from sqlalchemy import select

from kos.knowledge import service as ks
from kos.models import AuditLog, Person, SourceSegment

from .test_knowledge import run_jobs


async def upload(client, name, authors=None, section=None, text="# T\n\nTexto de prueba."):
    data = {}
    if authors is not None:
        data["authors"] = json.dumps(authors)
    if section:
        data["section"] = section
    r = await client.post("/api/sources", files={"file": (name, text.encode())}, data=data)
    assert r.status_code == 200, r.text
    return r.json()["source"]


async def names(client, q):
    r = await client.get("/api/persons", params={"q": q, "limit": 10})
    assert r.status_code == 200
    return [p["name"] for p in r.json()]


async def test_upload_with_authors_creates_them_once_and_in_order(auth_client, db):
    s = await upload(
        auth_client,
        "PRUEBA estudio.md",
        [{"name": "Brad J. Schoenfeld"}, {"name": "José López Chicharro"}],
        section="estudios",
    )
    assert [a["name"] for a in s["authors"]] == ["Brad J. Schoenfeld", "José López Chicharro"]
    assert s["authors"][0]["role"] == "researcher"  # from the section
    assert s["authors_text"] == "Brad J. Schoenfeld; José López Chicharro"
    # the same name (other case, no accent) reuses the person
    s2 = await upload(
        auth_client, "PRUEBA otro.md", [{"name": "jose lopez  chicharro"}], text="# O\n\nOtro."
    )
    assert s2["authors"][0]["id"] == s["authors"][1]["id"]
    assert len((await db.scalars(select(Person))).all()) == 2
    r = await auth_client.post(
        "/api/sources", files={"file": ("x.md", b"# x\n\ny")}, data={"authors": "no es json"}
    )
    assert r.status_code == 400


async def test_search_as_you_type(auth_client):
    for n in ("Brad J. Schoenfeld", "José López Chicharro", "Mike Israetel", "Ana Lopera"):
        assert (await auth_client.post("/api/persons", json={"name": n})).status_code == 201
    assert await names(auth_client, "schoe") == ["Brad J. Schoenfeld"]  # start of a word
    assert await names(auth_client, "LOPEZ") == ["José López Chicharro"]  # case, accents
    assert set(await names(auth_client, "lop")) == {"José López Chicharro", "Ana Lopera"}
    assert await names(auth_client, "chicharro jose") == ["José López Chicharro"]  # any order
    assert await names(auth_client, "Schonfeld") == ["Brad J. Schoenfeld"]  # close spelling
    assert (await names(auth_client, "ana"))[0] == "Ana Lopera"  # name start first
    assert await names(auth_client, "zzz") == []
    assert await names(auth_client, "%") == []  # no wildcards
    r = await auth_client.post("/api/persons", json={"name": "mike  israetel"})
    assert r.json()["existing"] is True


async def test_set_authors_filter_library_and_detail(auth_client):
    a = await upload(auth_client, "PRUEBA a.md", [{"name": "Profesora A"}], section="cursos")
    b = await upload(auth_client, "PRUEBA b.md", text="# B\n\nOtro texto.")
    pid = a["authors"][0]["id"]
    assert a["authors"][0]["role"] == "teacher"
    r = await auth_client.put(
        f"/api/sources/{b['id']}/authors", json={"authors": [{"id": pid}, {"name": "Coautor B"}]}
    )
    assert [x["name"] for x in r.json()["authors"]] == ["Profesora A", "Coautor B"]
    lst = (await auth_client.get("/api/sources", params={"author": pid})).json()
    assert {s["id"] for s in lst["items"]} == {a["id"], b["id"]}
    d = (await auth_client.get(f"/api/persons/{pid}")).json()
    assert len(d["sources"]) == 2 and ["Coautor B"] in [s["co_authors"] for s in d["sources"]]
    listing = (await auth_client.get("/api/persons")).json()
    assert {p["name"]: p["sources"] for p in listing} == {"Profesora A": 2, "Coautor B": 1}
    r = await auth_client.put(
        f"/api/sources/{b['id']}/authors", json={"authors": [{"id": str(uuid.uuid4())}]}
    )
    assert r.status_code == 400


async def test_merge_moves_sources_and_claims_and_rename(auth_client, db):
    s = await upload(
        auth_client,
        "PRUEBA c.md",
        [{"name": "B. Schoenfeld"}],
        text="# C\n\nLa hipertrofia depende del volumen.",
    )
    s2 = await upload(
        auth_client,
        "PRUEBA d.md",
        [{"name": "Brad Schoenfeld"}, {"name": "B. Schoenfeld"}],
        text="# D\n\nOtra.",
    )
    await run_jobs()
    dup = uuid.UUID(s["authors"][0]["id"])
    keep = s2["authors"][0]["id"]
    seg = await db.scalar(select(SourceSegment).where(SourceSegment.text.contains("hipertrofia")))
    e = await ks.create_entity(db, type_key="concept", name="Hipertrofia PRUEBA")
    claim = await ks.add_claim(
        db,
        text_="Depende del volumen",
        claim_type="interpretation",
        entities=[(e.id, "subject")],
        person_id=dup,
        evidence=[(seg.id, "La hipertrofia depende del volumen")],
    )
    r = await auth_client.post(f"/api/persons/{dup}/merge", json={"into": keep})
    assert r.status_code == 200 and r.json()["claims"] == 1
    assert await db.get(Person, dup) is None
    await db.refresh(claim)
    assert str(claim.attributed_person_id) == keep
    src2 = (await auth_client.get(f"/api/sources/{s2['id']}")).json()
    assert [a["name"] for a in src2["authors"]] == ["Brad Schoenfeld"]  # no repeat
    assert (await auth_client.get(f"/api/sources/{s['id']}")).json()[
        "authors_text"
    ] == "Brad Schoenfeld"
    # rename keeps the text of its sources in step; a name taken by someone else is refused
    r = await auth_client.patch(f"/api/persons/{keep}", json={"name": "Brad J. Schoenfeld"})
    assert r.status_code == 200
    assert (await auth_client.get(f"/api/sources/{s['id']}")).json()[
        "authors_text"
    ] == "Brad J. Schoenfeld"
    other = (await auth_client.post("/api/persons", json={"name": "Otro"})).json()
    r = await auth_client.patch(f"/api/persons/{other['id']}", json={"name": "brad j schoenfeld"})
    assert r.status_code == 400
    # deleting: only someone with nothing attached
    assert (await auth_client.delete(f"/api/persons/{keep}")).status_code == 409
    assert (await auth_client.delete(f"/api/persons/{other['id']}")).status_code == 204
    actions = set((await db.scalars(select(AuditLog.action))).all())
    assert {"person.merged", "person.updated", "person.deleted", "source.authors"} <= actions


async def test_youtube_with_authors(auth_client):
    r = await auth_client.post(
        "/api/sources/youtube",
        json={"url": "https://youtu.be/dQw4w9WgXcQ", "authors": [{"name": "Canal PRUEBA"}]},
    )
    assert r.status_code == 200
    a = r.json()["source"]["authors"]
    assert a[0]["name"] == "Canal PRUEBA" and a[0]["role"] == "teacher"
