# ruff: noqa: E501  (test data on one line)
"""Branches of a source (Nutrición, Biomecánica...): set when uploading, edited later,
used to filter the library, search, questions and MCP. A branch includes its
sub-areas and the sources of the courses classified in it."""

import uuid

from kos.ai import query as aq

from .test_knowledge import run_jobs
from .test_mcp import data, mcp_client, serve


async def branches(client):
    await client.post("/api/disciplines/seed-training-science")
    rows = (await client.get("/api/disciplines")).json()
    return {d["name"]: d["id"] for d in rows}


async def upload(client, name, text, **form):
    r = await client.post("/api/sources", files={"file": (name, text.encode())}, data=form)
    assert r.status_code == 200, r.text
    return r.json()["source"]


async def test_set_at_upload_edit_and_filter(auth_client, db):
    b = await branches(auth_client)
    nut, bio, root = b["Nutrición"], b["Biomecánica"], b["Ciencias del entrenamiento"]
    proteina = await upload(
        auth_client, "PRUEBA proteína.md", "# Proteína\n\n1,6 g por kilo al día.", disciplines=[nut]
    )
    palanca = await upload(
        auth_client,
        "PRUEBA palancas.md",
        "# Palancas\n\nEl brazo de momento cambia con el ángulo.",
        disciplines=f"{bio},{nut}",
    )
    libre = await upload(auth_client, "PRUEBA otra.md", "# Otra\n\nSin rama.")
    assert [d["name"] for d in proteina["disciplines"]] == ["Nutrición"]
    assert [d["name"] for d in palanca["disciplines"]] == ["Biomecánica", "Nutrición"]
    assert libre["disciplines"] == []

    def titles(r):
        return {s["title"] for s in r.json()["items"]}

    assert titles(await auth_client.get(f"/api/sources?discipline={nut}")) == {
        "PRUEBA proteína",
        "PRUEBA palancas",
    }
    assert titles(await auth_client.get(f"/api/sources?discipline={bio}")) == {"PRUEBA palancas"}
    # the root branch includes its sub-areas
    assert titles(await auth_client.get(f"/api/sources?discipline={root}")) == {
        "PRUEBA proteína",
        "PRUEBA palancas",
    }

    r = await auth_client.put(
        f"/api/sources/{palanca['id']}/disciplines", json={"discipline_ids": [bio]}
    )
    assert [d["name"] for d in r.json()["disciplines"]] == ["Biomecánica"]
    assert titles(await auth_client.get(f"/api/sources?discipline={nut}")) == {"PRUEBA proteína"}

    # unknown or malformed branches are refused with a clear message
    r = await auth_client.put(
        f"/api/sources/{libre['id']}/disciplines", json={"discipline_ids": [str(uuid.uuid4())]}
    )
    assert r.status_code == 400 and "rama" in r.json()["detail"]
    r = await auth_client.post(
        "/api/sources", files={"file": ("x.md", b"# x\n\ny")}, data={"disciplines": "no-es-id"}
    )
    assert r.status_code == 400

    # a duplicate upload keeps its branches and gains the new ones
    again = await upload(
        auth_client, "PRUEBA proteína.md", "# Proteína\n\n1,6 g por kilo al día.", disciplines=[bio]
    )
    assert {d["name"] for d in again["disciplines"]} == {"Nutrición", "Biomecánica"}


async def test_search_questions_and_mcp_by_branch(auth_client, db):
    b = await branches(auth_client)
    nut = b["Nutrición"]
    p = await upload(
        auth_client,
        "PRUEBA proteína.md",
        "# Proteína\n\nLa proteína ayuda a la hipertrofia.",
        disciplines=[nut],
    )
    await upload(
        auth_client, "PRUEBA fuerza.md", "# Fuerza\n\nLa fuerza y la hipertrofia van unidas."
    )
    await run_jobs()
    hits = (
        await auth_client.get(
            "/api/search", params={"q": "hipertrofia", "mode": "keyword", "discipline": nut}
        )
    ).json()
    assert {h["source_title"] for h in hits} == {"PRUEBA proteína"}
    assert await aq.scope_sources(db, "discipline", uuid.UUID(nut)) == [uuid.UUID(p["id"])]

    from kos.mcp_server import tokens

    async with serve() as app:
        _, raw = await tokens.create(db, "Claude", propose=False)
        async with mcp_client(app, raw) as c:
            lst = data(await c.call_tool("list_sources", {"discipline": "nutrición"}))
            assert [s["title"] for s in lst["sources"]] == ["PRUEBA proteína"]
            assert lst["sources"][0]["branches"] == ["Nutrición"]
            hits = data(
                await c.call_tool(
                    "search", {"query": "hipertrofia", "mode": "keyword", "discipline": "Nutrición"}
                )
            )
            assert {h["source_title"] for h in hits["results"]} == {"PRUEBA proteína"}
            r = await c.call_tool("list_sources", {"discipline": "Astrología"})
            assert r.is_error and "rama no encontrada" in r.content[0].text
