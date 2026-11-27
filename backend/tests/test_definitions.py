# ruff: noqa: E501  (test data on one line)
"""A concept's definition: written by the user, proposed by the AI (grounded, with
verified quotes) or over MCP, accepted as is or edited, discarded, and the history."""

import uuid

import pytest
from sqlalchemy import select

from kos.ai import providers
from kos.ai.providers import ProviderResult, Usage
from kos.knowledge import definitions as defs
from kos.knowledge import definitions_ai as dai
from kos.knowledge import service as ks
from kos.mcp_server import tokens
from kos.models import AuditLog, Entity, EntityDefinition, SourceSegment

from .test_knowledge import entity, run_jobs
from .test_mcp import data, mcp_client, serve
from .test_study import docs_in

TEXT = (
    "# RIR\n\nLas repeticiones en reserva (RIR) son las repeticiones que quedan antes del fallo muscular al terminar una serie.\n\n"
    "## Uso\n\nEl RIR se usa para regular la intensidad del entrenamiento de fuerza."
)


class Model:
    def __init__(self, quote=None, basis="library"):
        self.calls, self.quote, self.basis = [], quote, basis

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        frags = {r: t for r, t in docs_in(kw).items() if r.startswith("f")}
        ref = next(r for r, t in frags.items() if "repeticiones que quedan" in t)
        parsed = dai.DefinitionOut(
            short="Número de repeticiones que quedan antes del fallo al terminar una serie.",
            extended="Se usa para regular la intensidad.",
            basis=self.basis,
            citations=[
                dai.CitationOut(
                    fragment=ref,
                    quote=self.quote or "las repeticiones que quedan antes del fallo muscular",
                ),
                dai.CitationOut(fragment="f99", quote="no existe"),
            ],
            note=None,
        )
        return ProviderResult(
            parsed=parsed,
            stop_reason="end_turn",
            model_served="claude-opus-5-5",
            usage=Usage(input_tokens=1500, output_tokens=200),
            request_id="req",
        )


@pytest.fixture
async def rir(auth_client, db):
    await auth_client.post("/api/sources", files={"file": ("PRUEBA rir.md", TEXT.encode())})
    await run_jobs()
    e = await entity(auth_client, "Repeticiones en reserva", aliases=["RIR"])
    seg = await db.scalar(
        select(SourceSegment).where(SourceSegment.text.contains("repeticiones que quedan"))
    )
    await ks.add_claim(
        db,
        text_="El RIR son las repeticiones que quedan antes del fallo",
        claim_type="definition",
        entities=[(uuid.UUID(e["id"]), "subject")],
        evidence=[(seg.id, "las repeticiones que quedan antes del fallo muscular")],
    )
    await ks.add_claim(
        db,
        text_="El RIR regula la intensidad",
        claim_type="recommendation",
        entities=[(uuid.UUID(e["id"]), "subject")],
        evidence=[(seg.id, "las repeticiones que quedan")],
    )
    return e


async def test_write_replace_and_history(auth_client, rir, db):
    eid = rir["id"]
    r = await auth_client.get(f"/api/entities/{eid}/definitions")
    body = r.json()
    assert body["current"] is None and body["proposals"] == []
    # «Cómo lo definen tus fuentes»: only definition claims
    assert [x["text"] for x in body["from_sources"]] == [
        "El RIR son las repeticiones que quedan antes del fallo"
    ]
    r = await auth_client.put(
        f"/api/entities/{eid}/definition", json={"short": "  Mi definición.  "}
    )
    assert r.status_code == 200 and r.json()["origin"] == "user"
    r = await auth_client.put(
        f"/api/entities/{eid}/definition",
        json={"short": "Mejor definición.", "extended": "Con explicación."},
    )
    body = (await auth_client.get(f"/api/entities/{eid}/definitions")).json()
    assert body["current"]["short"] == "Mejor definición."
    assert body["current"]["extended"] == "Con explicación."
    assert [h["short"] for h in body["history"]] == ["Mi definición."]
    e = await db.get(Entity, uuid.UUID(eid))
    await db.refresh(e)
    assert e.short_definition == "Mejor definición."  # mirrored for search, MCP…
    # bring back the earlier one
    old = body["history"][0]["id"]
    r = await auth_client.post(f"/api/entities/{eid}/definitions/{old}/accept", json={})
    assert r.status_code == 200
    body = (await auth_client.get(f"/api/entities/{eid}/definitions")).json()
    assert body["current"]["short"] == "Mi definición."
    # empty is refused; clearing is explicit
    r = await auth_client.put(f"/api/entities/{eid}/definition", json={"short": "   "})
    assert r.status_code == 400
    assert (await auth_client.delete(f"/api/entities/{eid}/definition")).status_code == 204
    body = (await auth_client.get(f"/api/entities/{eid}/definitions")).json()
    assert body["current"] is None and len(body["history"]) == 2
    actions = set((await db.scalars(select(AuditLog.action))).all())
    assert {"definition.written", "definition.restored", "definition.cleared"} <= actions


async def test_a_definition_saved_elsewhere_is_adopted_not_lost(auth_client, rir, db):
    eid = rir["id"]
    # the old way (PATCH on the concept) also goes through the history
    r = await auth_client.patch(f"/api/entities/{eid}", json={"short_definition": "Vieja."})
    assert r.status_code == 200 and r.json()["short_definition"] == "Vieja."
    e = await db.get(Entity, uuid.UUID(eid))
    await db.refresh(e)
    e.short_definition = "Escrita por otra vía."  # e.g. at creation or by extraction
    await db.commit()
    body = (await auth_client.get(f"/api/entities/{eid}/definitions")).json()
    assert body["current"]["short"] == "Escrita por otra vía."
    assert body["current"]["origin"] == "imported"
    assert [h["short"] for h in body["history"]] == ["Vieja."]


async def test_ai_proposal_is_grounded_and_waits_for_the_user(auth_client, rir, db, monkeypatch):
    m = Model()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    eid = rir["id"]
    await auth_client.put(f"/api/entities/{eid}/definition", json={"short": "La mía."})
    r = await auth_client.post(f"/api/entities/{eid}/definitions/generate")
    assert r.status_code == 202
    again = await auth_client.post(f"/api/entities/{eid}/definitions/generate")
    assert again.status_code == 409  # one at a time
    assert (await auth_client.get(f"/api/entities/{eid}/definitions")).json()["generating"]
    await run_jobs()
    # the saved definition is not sent as if it were a source
    blob = m.calls[0]["user_content"][-1]["text"]
    assert "La mía." not in blob and "repeticiones que quedan" in blob
    body = (await auth_client.get(f"/api/entities/{eid}/definitions")).json()
    assert body["current"]["short"] == "La mía."  # unchanged until accepted
    [p] = body["proposals"]
    assert p["origin"] == "ai" and p["basis"] == "library" and p["ai_run_id"]
    [c] = p["citations"]  # the invented fragment was dropped
    seg = await db.get(SourceSegment, uuid.UUID(c["segment_id"]))
    assert c["quote"] in seg.text and c["source_title"] == "PRUEBA rir"
    # accept it edited
    r = await auth_client.post(
        f"/api/entities/{eid}/definitions/{p['id']}/accept",
        json={"short": "Repeticiones que quedan hasta el fallo al acabar la serie."},
    )
    assert r.status_code == 200 and r.json()["edited"] is True
    body = (await auth_client.get(f"/api/entities/{eid}/definitions")).json()
    assert body["current"]["id"] == p["id"] and body["current"]["citations"]
    assert body["proposals"] == [] and body["history"][0]["short"] == "La mía."


async def test_without_a_valid_quote_it_is_general_knowledge(auth_client, rir, monkeypatch):
    monkeypatch.setattr(providers, "get_provider", lambda: Model(quote="frase inventada"))
    eid = rir["id"]
    await auth_client.post(f"/api/entities/{eid}/definitions/generate")
    await run_jobs()
    [p] = (await auth_client.get(f"/api/entities/{eid}/definitions")).json()["proposals"]
    assert p["basis"] == "general" and p["citations"] == []
    assert "conocimiento general" in p["note"]
    r = await auth_client.post(f"/api/entities/{eid}/definitions/{p['id']}/reject")
    assert r.json()["status"] == "rejected"
    body = (await auth_client.get(f"/api/entities/{eid}/definitions")).json()
    assert body["proposals"] == [] and body["current"] is None


async def test_proposals_are_capped(rir, db):
    e = await db.get(Entity, uuid.UUID(rir["id"]))
    for i in range(defs.MAX_PROPOSALS):
        await defs.propose(db, e, short=f"P{i}", extended=None, origin="mcp", basis="general")
    with pytest.raises(defs.DefinitionError):
        await defs.propose(db, e, short="otra", extended=None, origin="mcp", basis="general")


async def test_mcp_proposes_and_decides_definitions(auth_client, rir, db):
    seg = await db.scalar(
        select(SourceSegment).where(SourceSegment.text.contains("repeticiones que quedan"))
    )
    async with serve() as app:
        _, raw = await tokens.create(db, "Claude", propose=True, decide=True)
        async with mcp_client(app, raw) as c:
            item = {
                "concept": "RIR",
                "short": "Repeticiones que faltan para el fallo.",
                "citations": [
                    {
                        "segment_id": str(seg.id),
                        "quote": "las repeticiones que quedan antes del fallo",
                    },
                    {"segment_id": str(seg.id), "quote": "inventada"},
                ],
            }
            r = data(
                await c.call_tool(
                    "propose_definitions",
                    {"items": [item, item, {"concept": "No existe", "short": "x"}]},
                )
            )["results"]
            assert [x["status"] for x in r] == ["created", "exists", "error"]
            assert r[0]["dropped_quotes"] == 1
            q = data(await c.call_tool("review_queue", {"kind": "definition"}))
            assert (
                q["pending"]["definition"] == 1
                and q["items"][0]["concept"] == "Repeticiones en reserva"
            )
            r = data(
                await c.call_tool(
                    "decide",
                    {"items": [{"kind": "definition", "id": r[0]["id"], "action": "accept"}]},
                )
            )
            assert r["results"][0]["status"] == "accepted"
    d = await db.scalar(select(EntityDefinition).where(EntityDefinition.origin == "mcp"))
    await db.refresh(d)
    assert d.status == "current" and d.basis == "library" and len(d.citations) == 1
