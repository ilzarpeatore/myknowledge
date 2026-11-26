"""Phase 6: the MCP server. A real MCP client (the official SDK) talks to the app
in-process over streamable HTTP with a personal token."""

import contextlib
import json
import uuid

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import select

from kos.api.main import create_app
from kos.knowledge import service as ks
from kos.mcp_server import tokens
from kos.models import ApiToken, AuditLog, Claim, Entity, Relationship, SourceSegment

from .test_knowledge import TEXT, entity, run_jobs


@contextlib.asynccontextmanager
async def serve():
    """The app with its MCP session manager running (entered in the test's own task:
    anyio scopes cannot span pytest fixture setup and teardown)."""
    a = create_app()
    async with a.state.mcp.session_manager.run():
        yield a


@contextlib.asynccontextmanager
async def mcp_client(app, token: str | None, mode: str = "auto"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver", headers=headers
    )
    async with (
        http,
        Client(
            streamable_http_client("http://testserver/api/mcp", http_client=http), mode=mode
        ) as c,
    ):
        yield c


def data(result):
    assert not result.is_error, result.content
    return result.structured_content or json.loads(result.content[0].text)


@pytest.fixture
async def library(auth_client, db):
    await auth_client.post("/api/sources", files={"file": ("curso.md", TEXT.encode())})
    await run_jobs()
    fallo = await entity(auth_client, "Proximidad al fallo", aliases=["RIR"])
    seg = await db.scalar(select(SourceSegment).where(SourceSegment.text.contains("fallo")))
    await ks.add_claim(
        db,
        text_="Entrenar cerca del fallo aumenta la fatiga",
        claim_type="reported_fact",
        entities=[(uuid.UUID(fallo["id"]), "subject")],
        evidence=[(seg.id, "Entrenar cerca del fallo aumenta la fatiga acumulada")],
    )
    return {"fallo": fallo, "seg": seg}


async def test_requests_without_a_valid_token_are_refused(db):
    async with serve() as app:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            for headers in ({}, {"Authorization": "Bearer kos_falso"}):
                r = await http.post(
                    "/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=headers
                )
                assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        tok, raw = await tokens.create(db, "revocado", propose=False)
        await tokens.revoke(db, tok)
        with pytest.raises(Exception):  # noqa: B017 - the client surfaces the 401 its own way
            async with mcp_client(app, raw) as c:
                await c.list_tools()


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_read_tools_with_both_protocol_generations(db, library, mode):
    async with serve() as app:
        _, raw = await tokens.create(db, "lector", propose=False)
        async with mcp_client(app, raw, mode) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert {"search", "get_fragment", "get_concept", "propose_claim"} <= names
            hits = data(await c.call_tool("search", {"query": "fallo fatiga", "mode": "keyword"}))
            assert hits["results"] and hits["results"][0]["source_title"] == "curso"
            frag = data(
                await c.call_tool(
                    "get_fragment", {"fragment_id": hits["results"][0]["fragment_id"]}
                )
            )
            assert "fallo" in frag["text"]
            concept = data(await c.call_tool("get_concept", {"concept": "rir"}))  # by alias
            assert concept["name"] == "Proximidad al fallo"
            assert concept["claims"][0]["evidence"][0]["quote"].startswith("Entrenar cerca")
            assert concept["claims"][0]["provenance"]["kind"] == "source"
            found = data(await c.call_tool("find_concepts", {"query": "proximidad"}))
            assert found["concepts"][0]["slug"] == library["fallo"]["slug"]
            # a read-only token cannot propose anything
            r = await c.call_tool("propose_concept", {"name": "Algo nuevo"})
            assert r.is_error and "permiso" in r.content[0].text
        assert (await db.scalar(select(ApiToken.last_used_at))) is not None


async def test_proposals_never_become_accepted_knowledge(db, library):
    async with serve() as app:
        _, raw = await tokens.create(db, "Claude Code", propose=True)
        seg = library["seg"]
        async with mcp_client(app, raw) as c:
            new = data(
                await c.call_tool(
                    "propose_concept",
                    {"name": "Fatiga acumulada", "definition": "Fatiga que se suma entre series"},
                )
            )
            assert new["created"] and new["status"] == "proposed"
            dup = data(await c.call_tool("propose_concept", {"name": "proximidad al fallo"}))
            assert dup["created"] is False and dup["existing"]["name"] == "Proximidad al fallo"

            claim = data(
                await c.call_tool(
                    "propose_claim",
                    {
                        "text": "Cerca del fallo se acumula fatiga",
                        "claim_type": "reported_fact",
                        "concepts": ["Proximidad al fallo", "Fatiga acumulada"],
                        "fragment_id": str(seg.id),
                        "quote": "aumenta la fatiga acumulada",
                    },
                )
            )
            assert claim["review_status"] == "pending"
            bad = await c.call_tool(
                "propose_claim",
                {
                    "text": "x",
                    "claim_type": "reported_fact",
                    "concepts": ["Proximidad al fallo"],
                    "fragment_id": str(seg.id),
                    "quote": "una frase que el curso no dice",
                },
            )
            assert bad.is_error and "literalmente" in bad.content[0].text

            rel = data(
                await c.call_tool(
                    "propose_relationship",
                    {
                        "source_concept": "Proximidad al fallo",
                        "relationship_type": "influences",
                        "target_concept": "Fatiga acumulada",
                        "fragment_id": str(seg.id),
                    },
                )
            )
            assert rel["status"] == "proposed"

        e = await db.scalar(select(Entity).where(Entity.canonical_name == "Fatiga acumulada"))
        assert (e.status, e.origin) == ("proposed", "mcp")
        c = await db.get(Claim, uuid.UUID(claim["id"]))
        assert (c.review_status, c.origin) == ("pending", "mcp")
        r = await db.get(Relationship, uuid.UUID(rel["id"]))
        assert (r.status, r.origin) == ("proposed", "mcp")
        actors = set(
            (await db.scalars(select(AuditLog.actor).where(AuditLog.action.like("mcp.%")))).all()
        )
        assert actors == {"mcp:Claude Code"}


async def test_tokens_are_managed_from_the_app_and_shown_once(auth_client, db):
    async with serve() as app:
        r = await auth_client.post("/api/tokens", json={"name": "Claude Desktop", "propose": True})
        assert r.status_code == 201
        raw = r.json()["token"]
        assert raw.startswith("kos_") and r.json()["scopes"] == "read,propose"
        listed = (await auth_client.get("/api/tokens")).json()
        assert "token" not in listed[0] and listed[0]["prefix"] == raw[:10]
        stored = await db.scalar(select(ApiToken.token_hash))
        assert raw not in stored  # only the hash is kept
        assert (await auth_client.delete(f"/api/tokens/{listed[0]['id']}")).status_code == 204
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as http:
            r = await http.post(
                "/api/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Authorization": f"Bearer {raw}"},
            )
            assert r.status_code == 401
