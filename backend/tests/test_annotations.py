"""Highlights and notes made in the PDF reader: stored apart from the original file,
replaced as a list per file version, listed and searched across the library."""

import uuid

from sqlalchemy import select

from kos.models import PdfAnnotation, SourceVersion

from .pdfgen import make_pdf


def hl(ink_id, page=0, contents=None, **extra):
    d = {
        "id": ink_id,
        "type": "highlight",
        "page": page,
        "quads": [],
        "color": "#ff0",
        "rect": {"x": 0, "y": 0, "width": 1, "height": 1},
    }
    if contents:
        d["contents"] = contents
    return {**d, **extra}


async def pdf(client, pages=(("Hola",), ("Adios",))):
    r = await client.post(
        "/api/sources", files={"file": ("PRUEBA anotar.pdf", make_pdf([list(p) for p in pages]))}
    )
    s = r.json()["source"]
    return s["id"], s["versions"][0]["id"]


async def test_save_replace_and_keep_text(auth_client, db):
    sid, vid = await pdf(auth_client)
    url = f"/api/sources/{sid}/versions/{vid}/annotations"
    assert (await auth_client.get(url)).json() == {"annotations": [], "items": []}
    r = await auth_client.put(
        url,
        json={
            "annotations": [
                {"data": hl("a1", 1), "text": "  Adios  "},
                {"data": hl("n1", 0, contents="Revisar esto", type="note")},
                {"data": hl("r1", 0, contents="respuesta", inReplyTo="n1", type="note")},
            ]
        },
    )
    assert r.json() == {"saved": 3, "removed": 0}
    got = (await auth_client.get(url)).json()
    assert {a["id"] for a in got["annotations"]} == {"a1", "n1", "r1"}
    a1 = next(i for i in got["items"] if i["id"] == "a1")
    assert a1["page"] == 2 and a1["text"] == "Adios"
    # after a reload the reader no longer knows the selected text: it is kept
    r = await auth_client.put(url, json={"annotations": [{"data": hl("a1", 1, color="#0f0")}]})
    assert r.json() == {"saved": 1, "removed": 2}
    row = await db.scalar(select(PdfAnnotation).where(PdfAnnotation.ink_id == "a1"))
    assert row.text == "Adios" and row.data["color"] == "#0f0"
    # the original file is never touched
    v = await db.get(SourceVersion, uuid.UUID(vid))
    assert v.size_bytes == len(make_pdf([["Hola"], ["Adios"]]))


async def test_list_and_search_across_library(auth_client):
    sid, vid = await pdf(auth_client)
    url = f"/api/sources/{sid}/versions/{vid}/annotations"
    await auth_client.put(
        url,
        json={
            "annotations": [
                {"data": hl("a1"), "text": "La hipertrofia depende del volumen"},
                {"data": hl("n1", contents="Comparar con el libro", type="note")},
                {"data": hl("r1", contents="hecho", inReplyTo="n1", type="note")},
            ]
        },
    )
    r = (await auth_client.get("/api/annotations")).json()
    assert r["total"] == 2 and r["items"][0]["source_title"] == "PRUEBA anotar"  # no replies
    assert [
        i["id"] for i in (await auth_client.get("/api/annotations?q=hipertrof")).json()["items"]
    ] == ["a1"]
    assert [
        i["id"] for i in (await auth_client.get("/api/annotations?q=libro")).json()["items"]
    ] == ["n1"]


async def test_invalid_lists_are_refused(auth_client):
    sid, vid = await pdf(auth_client)
    url = f"/api/sources/{sid}/versions/{vid}/annotations"
    for bad in (
        [{"data": {"type": "highlight", "page": 0}}],
        [{"data": hl("x", page=-1)}],
        [{"data": hl("x")}, {"data": hl("x")}],
        [{"data": hl("x", ink=["p" * 300_000])}],
    ):
        assert (await auth_client.put(url, json={"annotations": bad})).status_code == 400
    other = (await pdf(auth_client, (("Otro",),)))[0]
    assert (
        await auth_client.get(f"/api/sources/{other}/versions/{vid}/annotations")
    ).status_code in (404,)


async def processed_pdf(client, db):
    pages = [
        ["Introduccion", "El volumen semanal es una variable clave de la hipertrofia."],
        ["Resultados", "Por encima de 12 series semanales no hubo beneficio adicional."],
    ]
    r = await client.post(
        "/api/sources",
        files={"file": ("PRUEBA conv.pdf", make_pdf(pages))},
        data={"authors": '[{"name": "Investigadora PRUEBA"}]'},
    )
    s = r.json()["source"]
    from .test_knowledge import run_jobs

    await run_jobs()
    return s["id"], s["versions"][0]["id"]


async def test_highlight_becomes_claim_and_card(auth_client, db):
    from kos.models import Claim, Evidence, Flashcard

    sid, vid = await processed_pdf(auth_client, db)
    url = f"/api/sources/{sid}/versions/{vid}/annotations"
    await auth_client.put(
        url,
        json={
            "annotations": [
                {
                    "data": hl("h1", 1, contents="Clave para programar"),
                    "text": "Por encima de 12 series semanales no hubo beneficio adicional.",
                },
                {"data": hl("h2", 0)},  # no text: cannot be cited
                {"data": hl("h3", 0), "text": "una frase que no esta en el documento"},
            ]
        },
    )
    r = await auth_client.post(
        f"/api/annotations/{vid}/h1/claim",
        json={
            "entity": {"name": "Volumen de entrenamiento PRUEBA"},
            "text": "Más de 12 series por semana no aporta más",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["concept"]["name"] == "Volumen de entrenamiento PRUEBA"
    claim = await db.get(Claim, uuid.UUID(r.json()["claim_id"]))
    ev = await db.scalar(select(Evidence).where(Evidence.claim_id == claim.id))
    assert claim.text == "Más de 12 series por semana no aporta más"
    assert claim.origin == "user" and claim.context == "Clave para programar"
    assert claim.attributed_person_id is not None  # the single author of the source
    assert ev.quote == "Por encima de 12 series semanales no hubo beneficio adicional."
    again = await auth_client.post(
        f"/api/annotations/{vid}/h1/claim", json={"entity": {"name": "Otro"}}
    )
    assert again.status_code == 409
    # a card on the same (now existing) concept, by name: no duplicate concept
    r = await auth_client.post(
        f"/api/annotations/{vid}/h1/card",
        json={
            "entity": {"name": "volumen de entrenamiento prueba"},
            "front": "¿Más de 12 series semanales aportan más?",
        },
    )
    assert r.status_code == 201, r.text
    card = await db.get(Flashcard, uuid.UUID(r.json()["card_id"]))
    assert card.back.startswith("Por encima de 12") and card.claim_id == claim.id
    assert card.status == "active" and card.segment_id == ev.segment_id
    items = (await auth_client.get("/api/annotations?q=series")).json()["items"]
    assert items[0]["claim_id"] and items[0]["card_id"]
    assert items[0]["concept"]["name"] == "Volumen de entrenamiento PRUEBA"
    # nothing to cite
    for ink in ("h2", "h3"):
        r = await auth_client.post(
            f"/api/annotations/{vid}/{ink}/claim", json={"entity": {"name": "X PRUEBA"}}
        )
        assert r.status_code == 409
    r = await auth_client.post(f"/api/annotations/{vid}/h1/card", json={"entity": {}, "front": "x"})
    assert r.status_code in (400, 409)
