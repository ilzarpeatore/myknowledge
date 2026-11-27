"""Library sections (Cursos, Libros, Estudios, Vídeos...) and crossing between them."""

import uuid

from sqlalchemy import select

from kos.knowledge import service as ks
from kos.models import SourceSegment

from .docgen import VTT
from .test_knowledge import entity, run_jobs


async def upload(client, name, data, section=None):
    r = await client.post(
        "/api/sources", files={"file": (name, data)}, data={"section": section} if section else None
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["source"]


async def test_default_sections_and_automatic_placement(auth_client):
    keys = [s["key"] for s in (await auth_client.get("/api/sections")).json()]
    assert keys == [
        "cursos",
        "libros",
        "manuales",
        "estudios",
        "articulos",
        "videos",
        "audios",
        "apuntes",
        "recursos",
    ]
    # «Manuales» is a fixed section: it can be renamed but not deleted
    secs = {s["key"]: s for s in (await auth_client.get("/api/sections")).json()}
    assert secs["manuales"]["name"] == "Manuales" and secs["manuales"]["builtin"]
    note = await upload(auth_client, "apuntes.md", b"# Fuerza\n\nNotas.")
    assert note["sections"] == ["recursos"]  # nothing says what it is: Recursos
    subs = await upload(auth_client, "clase.vtt", VTT.encode(), section="cursos")
    assert subs["sections"] == ["cursos"]  # uploaded from a section: goes there
    mp3 = await upload(auth_client, "podcast.mp3", b"ID3\x04" + b"\x00" * 200)
    assert mp3["sections"] == ["audios"]
    yt = (
        await auth_client.post("/api/sources/youtube", json={"url": "https://youtu.be/jNQXAC9IVRw"})
    ).json()
    assert yt["source"]["sections"] == ["videos"]
    counts = {s["key"]: s["count"] for s in (await auth_client.get("/api/sections")).json()}
    assert counts["recursos"] == 1 and counts["cursos"] == 1 and counts["videos"] == 1


async def test_studies_and_courses_add_their_section(auth_client, db):
    src = await upload(auth_client, "paper.md", b"# Resultados\n\nEl grupo ganaron masa.")
    pub = (await auth_client.post("/api/publications", json={"title": "Estudio"})).json()
    await auth_client.put(
        f"/api/sources/{src['id']}/publication", json={"publication_id": pub["id"]}
    )
    course = (await auth_client.post("/api/courses", json={"title": "Curso"})).json()
    r = await auth_client.post(
        f"/api/courses/{course['id']}/materials", json={"source_id": src["id"]}
    )
    assert r.status_code in (200, 201), r.text
    got = (await auth_client.get(f"/api/sources/{src['id']}")).json()["sections"]
    assert set(got) == {"recursos", "estudios", "cursos"}  # added, never replaced


async def test_user_sets_sections_and_filters(auth_client):
    a = await upload(auth_client, "libro.md", b"# Libro\n\nTexto.")
    r = await auth_client.put(
        f"/api/sources/{a['id']}/sections", json={"sections": ["libros", "cursos"]}
    )
    assert sorted(r.json()["sections"]) == ["cursos", "libros"]
    bad = await auth_client.put(f"/api/sources/{a['id']}/sections", json={"sections": ["nope"]})
    assert bad.status_code == 400
    empty = await auth_client.put(f"/api/sources/{a['id']}/sections", json={"sections": []})
    assert empty.status_code == 422
    await upload(auth_client, "otro.md", b"# Otro\n\nTexto distinto.")
    libros = (await auth_client.get("/api/sources?section=libros")).json()
    assert [s["title"] for s in libros["items"]] == ["libro"]


async def test_custom_sections(auth_client):
    r = await auth_client.post("/api/sections", json={"name": "Congresos y jornadas"})
    assert r.json()["key"] == "congresos-y-jornadas"
    assert (
        await auth_client.post("/api/sections", json={"name": "Congresos y jornadas"})
    ).status_code == 400
    src = await upload(auth_client, "jornada.md", b"# J\n\nTexto.", section="congresos-y-jornadas")
    r = await auth_client.patch("/api/sections/congresos-y-jornadas", json={"name": "Congresos"})
    assert r.json()["name"] == "Congresos"
    assert (await auth_client.delete("/api/sections/libros")).status_code == 400  # built-in
    r = await auth_client.delete("/api/sections/congresos-y-jornadas")
    assert r.json() == {"moved_to_recursos": 1}  # its source is not left without a section
    assert (await auth_client.get(f"/api/sources/{src['id']}")).json()["sections"] == ["recursos"]


async def _claim(db, seg_text_part, entity_id, quote):
    seg = await db.scalar(select(SourceSegment).where(SourceSegment.text.contains(seg_text_part)))
    await ks.add_claim(
        db,
        text_=quote,
        claim_type="reported_fact",
        entities=[(uuid.UUID(entity_id), "subject")],
        evidence=[(seg.id, quote)],
    )


async def test_crossing_sections_through_concepts_and_links(auth_client, db):
    course = await upload(
        auth_client,
        "curso.md",
        b"# RIR\n\nEntrenar a 0-3 RIR maximiza la hipertrofia.",
        section="cursos",
    )
    book = await upload(
        auth_client,
        "libro.md",
        b"# Fallo\n\nEl fallo muscular aumenta la fatiga.",
        section="libros",
    )
    study = await upload(
        auth_client,
        "estudio.md",
        b"# Resultados\n\nA 3 RIR la masa fue similar.",
        section="estudios",
    )
    other = await upload(
        auth_client, "nutri.md", b"# Proteina\n\nLa proteina importa.", section="apuntes"
    )
    await run_jobs()
    rir = await entity(auth_client, "Proximidad al fallo")
    await _claim(db, "0-3 RIR", rir["id"], "Entrenar a 0-3 RIR maximiza la hipertrofia")
    await _claim(db, "fallo muscular", rir["id"], "El fallo muscular aumenta la fatiga")
    await _claim(db, "masa fue similar", rir["id"], "A 3 RIR la masa fue similar")
    r = await auth_client.post(
        f"/api/sources/{course['id']}/links",
        json={"other_id": other["id"], "note": "el curso lo recomienda"},
    )
    assert r.status_code == 201
    rel = (await auth_client.get(f"/api/sources/{course['id']}/related")).json()
    assert [c["name"] for c in rel["concepts"]] == ["Proximidad al fallo"]
    by_title = {x["title"]: x for x in rel["related"]}
    assert by_title["libro"]["sections"] == ["libros"] and by_title["estudio"]["sections"] == [
        "estudios"
    ]
    assert by_title["libro"]["shared_concepts"] == ["Proximidad al fallo"]
    assert by_title["nutri"]["link"]["note"] == "el curso lo recomienda"  # manual link
    where = (await auth_client.get(f"/api/entities/{rir['id']}/sources")).json()
    assert [(g["key"], [s["title"] for s in g["sources"]]) for g in where] == [
        ("cursos", ["curso"]),
        ("libros", ["libro"]),
        ("estudios", ["estudio"]),
    ]
    hits = (await auth_client.get("/api/search?q=RIR&mode=keyword&section=estudios")).json()
    assert {h["source_title"] for h in hits} == {"estudio"}
    # the link can be removed; nothing else changes
    assert (
        await auth_client.delete(f"/api/source-links/{by_title['nutri']['link']['id']}")
    ).status_code == 204
    _ = book, study


async def test_ask_within_a_section(auth_client, monkeypatch):
    r = await auth_client.post(
        "/api/queries",
        json={"question": "¿Qué dicen los libros?", "scope_kind": "section", "scope_key": "libros"},
    )
    assert r.status_code == 202 and r.json()["scope_label"] == "Sección Libros"
    bad = await auth_client.post(
        "/api/queries", json={"question": "¿x?", "scope_kind": "section", "scope_key": "nope"}
    )
    assert bad.status_code == 400
