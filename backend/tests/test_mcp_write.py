# ruff: noqa: E501  (test data on one line)
"""MCP tools that write: batch proposals, notes in Apuntes, the review queue and
decisions (only with a «decide» token). Also checks that what the client receives
stays small: tool definitions and paginated results (they spend the user's plan)."""

import json
import uuid

from sqlalchemy import select

from kos.knowledge import service as ks
from kos.mcp_server import tokens
from kos.models import (
    ClaimCheck,
    Contradiction,
    Entity,
    ReviewTask,
    Source,
    SourceLink,
    SourceSegment,
)

from .test_knowledge import entity, run_jobs
from .test_mcp import data, mcp_client, serve

TEXTS = {
    "PRUEBA curso.md": "# Fallo\n\nHay que llegar siempre al fallo para crecer.\n\n# Fatiga\n\nEl fallo aumenta la fatiga.",
    "PRUEBA estudio.md": "# Resultados\n\nEl grupo a 1-3 RIR creció igual que el grupo al fallo.",
}


async def setup(auth_client, db):
    ids = {}
    for name, text in TEXTS.items():
        r = await auth_client.post("/api/sources", files={"file": (name, text.encode())})
        ids[name] = r.json()["source"]["id"]
    await run_jobs()
    fallo = uuid.UUID((await entity(auth_client, "Proximidad al fallo", aliases=["RIR"]))["id"])
    dup = uuid.UUID((await entity(auth_client, "Cercanía al fallo muscular"))["id"])
    from kos.evidence import service as ev

    pub = await ev.create(db, {"title": "Estudio PRUEBA", "year": 2024})
    await ev.link_source(db, await db.get(Source, uuid.UUID(ids["PRUEBA estudio.md"])), pub.id)

    async def seg(words):
        return await db.scalar(select(SourceSegment).where(SourceSegment.text.contains(words)))

    teacher = await ks.add_claim(
        db,
        text_="Siempre al fallo",
        claim_type="recommendation",
        entities=[(fallo, "subject")],
        evidence=[((await seg("siempre")).id, "llegar siempre al fallo")],
    )
    study = await ks.add_claim(
        db,
        text_="1-3 RIR iguala al fallo",
        claim_type="reported_fact",
        entities=[(fallo, "subject")],
        publication_id=pub.id,
        evidence=[((await seg("1-3 RIR")).id, "creció igual que el grupo al fallo")],
    )
    return {"ids": ids, "fallo": fallo, "dup": dup, "teacher": teacher, "study": study}


async def test_batch_proposals_are_idempotent_and_go_to_review(auth_client, db):
    lib = await setup(auth_client, db)
    t, s = str(lib["teacher"].id), str(lib["study"].id)
    a, b = lib["ids"]["PRUEBA curso.md"], lib["ids"]["PRUEBA estudio.md"]
    async with serve() as app:
        _, raw = await tokens.create(db, "Claude Desktop", propose=True)
        async with mcp_client(app, raw) as c:
            r = data(
                await c.call_tool(
                    "propose_contradictions",
                    {
                        "items": [
                            {"claim_a": t, "claim_b": s, "explanation": "Opuestas sobre el fallo."},
                            {
                                "claim_a": s,
                                "claim_b": t,
                                "explanation": "La misma pareja al revés.",
                            },
                            {"claim_a": t, "claim_b": "no-es-un-id", "explanation": "x"},
                        ]
                    },
                )
            )["results"]
            assert [x["status"] for x in r] == ["created", "exists", "error"]
            assert "id válido" in r[2]["error"]

            r = data(
                await c.call_tool(
                    "propose_merges",
                    {
                        "items": [
                            {
                                "keep": "Proximidad al fallo",
                                "merge": "Cercanía al fallo muscular",
                                "reason": "Sinónimos",
                            },
                            {
                                "keep": "Cercanía al fallo muscular",
                                "merge": "rir",
                                "reason": "otra vez",
                            },
                            {"keep": "Proximidad al fallo", "merge": "No existe", "reason": "x"},
                        ]
                    },
                )
            )["results"]
            assert [x["status"] for x in r] == ["created", "exists", "error"]

            r = data(
                await c.call_tool(
                    "propose_study_checks",
                    {
                        "items": [
                            {
                                "claim": t,
                                "study_claim": s,
                                "verdict": "contradicts",
                                "explanation": "El estudio no lo respalda.",
                            },
                            {
                                "claim": s,
                                "study_claim": t,
                                "verdict": "supports",
                                "explanation": "al revés",
                            },
                        ]
                    },
                )
            )["results"]
            assert r[0]["status"] == "created" and r[1]["status"] == "error"

            r = data(
                await c.call_tool(
                    "propose_source_links",
                    {
                        "items": [
                            {
                                "source_a": a,
                                "source_b": b,
                                "kind": "contradicts",
                                "note": "Choque sobre el fallo",
                            },
                            {"source_a": b, "source_b": a, "kind": "related", "note": "repetido"},
                        ]
                    },
                )
            )["results"]
            assert [x["status"] for x in r] == ["created", "exists"]

            # one call is enough to see what is pending, compactly
            q = data(await c.call_tool("review_queue", {}))
            assert q["pending"] == {
                **q["pending"],
                "contradiction": 1,
                "entity_merge": 1,
                "source_link": 1,
                "check": 1,
            }
            one = data(await c.call_tool("review_queue", {"kind": "contradiction", "limit": 1}))
            assert len(one["items"]) == 1 and one["items"][0]["kind"] == "contradiction"

            # a token without «decide» cannot decide
            r = await c.call_tool(
                "decide", {"items": [{"kind": "claim", "id": t, "action": "accept"}]}
            )
            assert r.is_error and "permiso" in r.content[0].text

    contra = await db.scalar(select(Contradiction))
    assert (contra.status, contra.origin) == ("candidate", "mcp")
    ck = await db.scalar(select(ClaimCheck))
    assert (ck.verdict, ck.status, ck.origin) == ("contradicts", "proposed", "mcp")
    lk = await db.scalar(select(SourceLink))
    assert (lk.kind, lk.status, lk.origin) == ("contradicts", "proposed", "mcp")
    merge = await db.scalar(select(ReviewTask).where(ReviewTask.kind == "entity_merge"))
    assert merge.payload["suggested_keep"] == str(lib["fallo"]) and merge.payload["origin"] == "mcp"


async def test_decide_with_a_decide_token(auth_client, db):
    lib = await setup(auth_client, db)
    t, s = str(lib["teacher"].id), str(lib["study"].id)
    async with serve() as app:
        _, raw = await tokens.create(db, "Claude con permiso", propose=True, decide=True)
        async with mcp_client(app, raw) as c:
            data(
                await c.call_tool(
                    "propose_contradictions",
                    {"items": [{"claim_a": t, "claim_b": s, "explanation": "Opuestas."}]},
                )
            )
            ck = data(
                await c.call_tool(
                    "propose_study_checks",
                    {
                        "items": [
                            {
                                "claim": t,
                                "study_claim": s,
                                "verdict": "contradicts",
                                "explanation": "No lo respalda.",
                            }
                        ]
                    },
                )
            )
            merge = data(
                await c.call_tool(
                    "propose_merges",
                    {
                        "items": [
                            {
                                "keep": "Proximidad al fallo",
                                "merge": "Cercanía al fallo muscular",
                                "reason": "Sinónimos",
                            }
                        ]
                    },
                )
            )
            q = data(await c.call_tool("review_queue", {"kind": "contradiction"}))
            task_id = q["items"][0]["id"]
            r = data(
                await c.call_tool(
                    "decide",
                    {
                        "items": [
                            {
                                "kind": "task",
                                "id": task_id,
                                "action": "accept",
                                "contradiction": "context_difference",
                            },
                            {"kind": "check", "id": ck["results"][0]["id"], "action": "accept"},
                            {
                                "kind": "task",
                                "id": merge["results"][0]["id"],
                                "action": "accept",
                            },  # no keep
                            {
                                "kind": "task",
                                "id": merge["results"][0]["id"],
                                "action": "accept",
                                "keep": str(lib["fallo"]),
                            },
                            {"kind": "claim", "id": t, "action": "reject"},
                        ]
                    },
                )
            )["results"]
            assert [x["status"] for x in r] == [
                "context_difference",
                "accepted",
                "error",
                "merged",
                "rejected",
            ]
            assert "keep" in r[2]["error"]
    db.expire_all()
    assert (await db.scalar(select(Contradiction))).status == "context_difference"
    assert (await db.get(Entity, lib["dup"])).status == "merged"


async def test_save_note_in_apuntes(auth_client, db):
    lib = await setup(auth_client, db)
    a = lib["ids"]["PRUEBA curso.md"]
    async with serve() as app:
        _, raw = await tokens.create(db, "Claude", propose=True)
        async with mcp_client(app, raw) as c:
            args = {
                "title": "Síntesis: proximidad al fallo",
                "content": "## Idea\n\nEl estudio y el curso discrepan.",
                "related_sources": [a],
            }
            first = data(await c.call_tool("save_note", args))
            again = data(await c.call_tool("save_note", args))
            assert first["status"] == "created" and first["linked_sources"] == 1
            assert again == {**again, "status": "exists", "source_id": first["source_id"]}
            r = await c.call_tool("save_note", {"title": "x", "content": "a" * 60_001})
            assert r.is_error and "60 000" in r.content[0].text
    await run_jobs()
    src = (await auth_client.get(f"/api/sources/{first['source_id']}")).json()
    assert src["sections"] == ["apuntes"] and src["status"] == "ready"
    assert src["authors_text"] == "Claude (MCP)"
    hits = (
        await auth_client.get("/api/search", params={"q": "discrepan", "mode": "keyword"})
    ).json()
    assert any(h["source_id"] == first["source_id"] for h in hits)


async def test_what_the_client_receives_stays_small(auth_client, db):
    """Tool definitions travel with every message, and results fill the context."""
    lib = await setup(auth_client, db)
    seg = await db.scalar(select(SourceSegment).where(SourceSegment.text.contains("siempre")))
    for n in range(60):  # a concept with many claims
        await ks.add_claim(
            db,
            text_=f"Afirmación de relleno número {n} " + "con texto largo " * 20,
            claim_type="opinion",
            entities=[(lib["fallo"], "subject")],
            evidence=[(seg.id, "llegar siempre al fallo")],
        )
    async with serve() as app:
        _, raw = await tokens.create(db, "Claude", propose=True)
        async with mcp_client(app, raw) as c:
            tools = (await c.list_tools()).tools
            size = sum(
                len(t.name) + len(t.description or "") + len(json.dumps(t.input_schema))
                for t in tools
            )
            assert size < 16_000, size  # all definitions together, sent with each message
            assert all(len(t.description or "") < 600 for t in tools)
            page = data(await c.call_tool("get_concept", {"concept": "rir"}))
            assert (
                len(page["claims"]) == 15
                and page["claims_total"] == 62
                and page["next_offset"] == 15
            )
            assert len(json.dumps(page, ensure_ascii=False)) < 15_000
            last = data(await c.call_tool("get_concept", {"concept": "rir", "offset": 60}))
            assert len(last["claims"]) == 2 and "next_offset" not in last
            hits = data(await c.call_tool("search", {"query": "fallo", "mode": "keyword"}))
            assert all(len(h["text"]) <= 500 for h in hits["results"])
            assert all(None not in h.values() for h in hits["results"])  # no empty fields
