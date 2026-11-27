"""MCP tools that write: batch proposals (contradictions, merges, checks against
studies, links between sources), notes saved to Apuntes, the review queue and, with a
token that allows it, decisions on what is pending.

Designed to spend little of the client's context (and so of the user's plan):
- batches: one call proposes or decides up to 25 items, not one call per item;
- results are short (index, status, id, error) and errors say how to fix the call, so
  the model does not retry blindly;
- idempotent: proposing something that already exists returns «exists» instead of
  creating a duplicate or failing;
- the review queue is paginated and compact, so the model does not have to explore.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from kos.knowledge import compare
from kos.knowledge import service as ks
from kos.models import (
    AuditLog,
    Claim,
    ClaimCheck,
    Contradiction,
    Entity,
    ReviewTask,
    Source,
    SourceLink,
)

MAX_BATCH = 25
PROPOSE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
DECIDE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


class ContradictionIn(BaseModel):
    claim_a: str = Field(description="claim id")
    claim_b: str = Field(description="claim id")
    explanation: str = Field(max_length=2000)


class MergeIn(BaseModel):
    keep: str = Field(description="concept (id, slug or name) that stays")
    merge: str = Field(description="duplicate concept that would be merged into keep")
    reason: str = Field(max_length=1000)


class StudyCheckIn(BaseModel):
    claim: str = Field(description="claim id (teacher, course: not from a study)")
    study_claim: str = Field(description="claim id reported by a study")
    verdict: Literal["supports", "contradicts", "partial"]
    explanation: str = Field(max_length=2000)


class LinkIn(BaseModel):
    source_a: str = Field(description="source id")
    source_b: str = Field(description="source id")
    kind: Literal["related", "expands", "summarizes", "contradicts", "applies", "recommends"]
    note: str = Field(max_length=1000)


class DecisionIn(BaseModel):
    kind: Literal["task", "claim", "check", "link", "concept"]
    id: str
    action: Literal["accept", "reject"]
    keep: str | None = Field(
        default=None, description="for a merge task: the concept id that stays"
    )
    contradiction: Literal["real", "context_difference"] | None = Field(
        default=None, description="for a contradiction task accepted: which kind"
    )


def _uuid(v: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except (ValueError, TypeError):
        raise ValueError(f"{what}: no es un id válido ({v[:40]!r})") from None


def _check_batch(items: list) -> None:
    if not items:
        raise ToolError("lista vacía")
    if len(items) > MAX_BATCH:
        raise ToolError(f"máximo {MAX_BATCH} elementos por llamada: divide la lista")


def register(mcp, sm, require, actor, entity_ref) -> None:  # noqa: C901 - one place for all
    async def audit(db, action: str, table: str, subject: str, after: dict) -> None:
        db.add(
            AuditLog(
                actor=actor(), action=action, subject_table=table, subject_id=subject, after=after
            )
        )

    @mcp.tool(annotations=PROPOSE)
    async def propose_contradictions(items: list[ContradictionIn]) -> dict[str, Any]:
        """Propose pairs of claims that contradict each other (up to 25), each with a
        short explanation. They go to Revisión; an existing pair returns «exists»."""
        require("propose")
        _check_batch(items)
        out = []
        async with sm() as db:
            for i, it in enumerate(items):
                try:
                    a, b = _uuid(it.claim_a, "claim_a"), _uuid(it.claim_b, "claim_b")
                    x, y = sorted((a, b), key=str)
                    if await db.scalar(
                        select(Contradiction.id).where(
                            Contradiction.claim_a_id == x, Contradiction.claim_b_id == y
                        )
                    ):
                        out.append({"i": i, "status": "exists"})
                        continue
                    c = await compare.add_contradiction(
                        db, a, b, analysis=f"[MCP] {it.explanation}", origin="mcp"
                    )
                    await audit(db, "mcp.propose_contradiction", "contradictions", str(c.id), {})
                    await db.commit()
                    out.append({"i": i, "status": "created", "id": str(c.id)})
                except (ValueError, ks.KnowledgeError) as exc:
                    await db.rollback()
                    out.append({"i": i, "status": "error", "error": str(exc)[:200]})
        return {"results": out}

    @mcp.tool(annotations=PROPOSE)
    async def propose_merges(items: list[MergeIn]) -> dict[str, Any]:
        """Propose that two concepts are the same (up to 25). The user confirms in
        Revisión which one stays; merges can be undone there."""
        require("propose")
        _check_batch(items)
        out = []
        async with sm() as db:
            for i, it in enumerate(items):
                try:
                    keep, dup = await entity_ref(db, it.keep), await entity_ref(db, it.merge)
                except ToolError as exc:
                    out.append({"i": i, "status": "error", "error": str(exc)})
                    continue
                if keep.id == dup.id:
                    out.append({"i": i, "status": "error", "error": "es el mismo concepto"})
                    continue
                a, b = sorted((keep.id, dup.id), key=str)
                key = f"{a}:{b}"
                if await db.scalar(
                    select(ReviewTask.id).where(
                        ReviewTask.kind == "entity_merge",
                        ReviewTask.subject_key == key,
                        ReviewTask.status.in_(["open", "rejected"]),
                    )
                ):
                    out.append({"i": i, "status": "exists"})
                    continue
                ea, eb = (keep, dup) if keep.id == a else (dup, keep)
                t = ReviewTask(
                    kind="entity_merge",
                    subject_key=key,
                    payload={
                        "entity_a": str(ea.id),
                        "entity_b": str(eb.id),
                        "name_a": ea.canonical_name,
                        "name_b": eb.canonical_name,
                        "suggested_keep": str(keep.id),
                        "origin": "mcp",
                        "reason": it.reason[:1000],
                    },
                )
                db.add(t)
                await db.flush()
                await audit(db, "mcp.propose_merge", "review_tasks", str(t.id), {"key": key})
                await db.commit()
                out.append({"i": i, "status": "created", "id": str(t.id)})
        return {"results": out}

    @mcp.tool(annotations=PROPOSE)
    async def propose_study_checks(items: list[StudyCheckIn]) -> dict[str, Any]:
        """Say whether a study's claim supports, contradicts or partly supports a claim
        that is not from a study (up to 25). Shown on the claim as «por revisar»."""
        require("propose")
        _check_batch(items)
        out = []
        async with sm() as db:
            for i, it in enumerate(items):
                try:
                    cid, sid = _uuid(it.claim, "claim"), _uuid(it.study_claim, "study_claim")
                    c, s = await db.get(Claim, cid), await db.get(Claim, sid)
                    if c is None or s is None:
                        raise ValueError("afirmación no encontrada")
                    if c.publication_id:
                        raise ValueError("«claim» es de un estudio: compara dos estudios aparte")
                    if not s.publication_id:
                        raise ValueError("«study_claim» no es de un estudio (vincula su fuente)")
                except ValueError as exc:
                    out.append({"i": i, "status": "error", "error": str(exc)})
                    continue
                existing = await db.scalar(
                    select(ClaimCheck).where(
                        ClaimCheck.claim_id == cid, ClaimCheck.study_claim_id == sid
                    )
                )
                if existing is not None:
                    out.append({"i": i, "status": "exists", "id": str(existing.id)})
                    continue
                ck = ClaimCheck(
                    claim_id=cid,
                    study_claim_id=sid,
                    verdict=it.verdict,
                    explanation=it.explanation[:2000],
                    status="proposed",
                    origin="mcp",
                )
                db.add(ck)
                await db.flush()
                await audit(db, "mcp.propose_study_check", "claim_checks", str(ck.id), {})
                await db.commit()
                out.append({"i": i, "status": "created", "id": str(ck.id)})
        return {"results": out}

    @mcp.tool(annotations=PROPOSE)
    async def propose_source_links(items: list[LinkIn]) -> dict[str, Any]:
        """Propose links between sources with their kind (up to 25): related, expands,
        summarizes, contradicts, applies, recommends. A pair already linked or
        rejected by the user returns «exists»."""
        require("propose")
        _check_batch(items)
        out = []
        async with sm() as db:
            for i, it in enumerate(items):
                try:
                    a, b = _uuid(it.source_a, "source_a"), _uuid(it.source_b, "source_b")
                    if a == b:
                        raise ValueError("una fuente no se enlaza consigo misma")
                    if await db.get(Source, a) is None or await db.get(Source, b) is None:
                        raise ValueError("fuente no encontrada")
                except ValueError as exc:
                    out.append({"i": i, "status": "error", "error": str(exc)})
                    continue
                x, y = sorted((a, b), key=str)
                if await db.scalar(
                    select(SourceLink.id).where(SourceLink.a_id == x, SourceLink.b_id == y)
                ):
                    out.append({"i": i, "status": "exists"})
                    continue
                lk = SourceLink(
                    a_id=x,
                    b_id=y,
                    kind=it.kind,
                    status="proposed",
                    origin="mcp",
                    note=it.note[:1000],
                )
                db.add(lk)
                await db.flush()
                db.add(
                    ReviewTask(
                        kind="source_link",
                        subject_key=str(lk.id),
                        payload={"link_id": str(lk.id), "a_id": str(x), "b_id": str(y)},
                    )
                )
                await audit(db, "mcp.propose_source_link", "source_links", str(lk.id), {})
                await db.commit()
                out.append({"i": i, "status": "created", "id": str(lk.id)})
        return {"results": out}

    @mcp.tool(annotations=PROPOSE)
    async def save_note(
        title: str, content: str, related_sources: list[str] | None = None
    ) -> dict[str, Any]:
        """Save a synthesis, summary or mind map (Markdown, up to 60 000 characters) as a
        note in the Apuntes section. It becomes searchable like any document; optional
        related_sources (source ids, up to 20) are linked to it. Saving the same text
        again returns the existing note."""
        require("propose")
        from kos.library import blobstore
        from kos.library import service as library

        title = title.strip()[:200]
        if not title or not content.strip():
            raise ToolError("faltan el título o el contenido")
        if len(content) > 60_000:
            raise ToolError("máximo 60 000 caracteres: divide la nota en varias")
        body = f"# {title}\n\n{content.strip()}\n\n---\nNota guardada por Claude vía MCP.\n"
        data = body.encode()
        store = blobstore.get_store()
        async with sm() as db:
            staged = await _stage_bytes(store, data)
            r = await library.add_upload(db, store, staged, f"{title}.md", title, "apuntes")
            src = r.source
            if not r.duplicate:
                src.authors_text = "Claude (MCP)"
            linked = 0
            for sid in (related_sources or [])[:20]:
                try:
                    other = _uuid(sid, "related_sources")
                except ValueError:
                    continue
                if other == src.id or await db.get(Source, other) is None:
                    continue
                x, y = sorted((src.id, other), key=str)
                if not await db.scalar(
                    select(SourceLink.id).where(SourceLink.a_id == x, SourceLink.b_id == y)
                ):
                    db.add(
                        SourceLink(
                            a_id=x,
                            b_id=y,
                            kind="summarizes",
                            status="accepted",
                            origin="mcp",
                            note="citada en la nota",
                        )
                    )
                    linked += 1
            await audit(db, "mcp.save_note", "sources", str(src.id), {"title": title})
            await db.commit()
            return {
                "status": "exists" if r.duplicate else "created",
                "source_id": str(src.id),
                "linked_sources": linked,
            }

    @mcp.tool(annotations=READ)
    async def review_queue(
        kind: Literal[
            "all",
            "entity_merge",
            "contradiction",
            "relationship",
            "source_link",
            "claim",
            "check",
            "concept",
        ] = "all",
        limit: int = 15,
        offset: int = 0,
    ) -> dict[str, Any]:
        """What is waiting for review, compact and paginated: counts per kind and the
        items of one kind (or all). Use the ids with decide (if allowed) or tell the
        user what to review in the app."""
        limit = max(1, min(limit, 50))
        async with sm() as db:
            counts = dict(
                (
                    await db.execute(
                        select(ReviewTask.kind, func.count())
                        .where(ReviewTask.status == "open")
                        .group_by(ReviewTask.kind)
                    )
                ).all()
            )
            counts["claim"] = await db.scalar(
                select(func.count()).select_from(Claim).where(Claim.review_status == "pending")
            )
            counts["check"] = await db.scalar(
                select(func.count()).select_from(ClaimCheck).where(ClaimCheck.status == "proposed")
            )
            counts["concept"] = await db.scalar(
                select(func.count()).select_from(Entity).where(Entity.status == "proposed")
            )
            items: list[dict[str, Any]] = []
            want = (lambda k: True) if kind == "all" else (lambda k: k == kind)
            task_kinds = ("entity_merge", "contradiction", "relationship", "source_link")
            if any(want(k) for k in task_kinds):
                q = select(ReviewTask).where(ReviewTask.status == "open")
                if kind != "all":
                    q = q.where(ReviewTask.kind == kind)
                for t in (
                    await db.scalars(q.order_by(ReviewTask.created_at).offset(offset).limit(limit))
                ).all():
                    p = {
                        k: v
                        for k, v in (t.payload or {}).items()
                        if k not in ("score", "pairs") and v not in (None, "")
                    }
                    items.append({"type": "task", "id": str(t.id), "kind": t.kind, **p})
            if want("claim"):
                for c in (
                    await db.scalars(
                        select(Claim)
                        .where(Claim.review_status == "pending")
                        .order_by(Claim.created_at)
                        .offset(offset)
                        .limit(limit)
                    )
                ).all():
                    items.append({"type": "claim", "id": str(c.id), "text": c.text[:300]})
            if want("check"):
                for ck in (
                    await db.scalars(
                        select(ClaimCheck)
                        .where(ClaimCheck.status == "proposed")
                        .order_by(ClaimCheck.created_at)
                        .offset(offset)
                        .limit(limit)
                    )
                ).all():
                    items.append(
                        {
                            "type": "check",
                            "id": str(ck.id),
                            "claim": str(ck.claim_id),
                            "study_claim": str(ck.study_claim_id),
                            "verdict": ck.verdict,
                        }
                    )
            if want("concept"):
                for e in (
                    await db.scalars(
                        select(Entity)
                        .where(Entity.status == "proposed")
                        .order_by(Entity.created_at)
                        .offset(offset)
                        .limit(limit)
                    )
                ).all():
                    items.append({"type": "concept", "id": str(e.id), "name": e.canonical_name})
            items = items[:limit]
            return {
                "pending": {k: v for k, v in counts.items() if v},
                "items": items,
                "next_offset": offset + limit if len(items) == limit else None,
            }

    @mcp.tool(annotations=DECIDE)
    async def decide(items: list[DecisionIn]) -> dict[str, Any]:
        """Accept or reject pending items (up to 25), only when the user has asked you
        to and with a token that allows it. kind: task (merge, contradiction,
        relationship, source link), claim, check, concept. A merge needs keep; an
        accepted contradiction can say real or context_difference."""
        require("decide")
        _check_batch(items)
        out = []
        async with sm() as db:
            for i, it in enumerate(items):
                try:
                    res = await _decide_one(db, it)
                    await audit(
                        db,
                        "mcp.decide",
                        it.kind,
                        it.id[:64],
                        {"action": it.action, "result": res},
                    )
                    await db.commit()
                    out.append({"i": i, "status": res})
                except (ValueError, ks.KnowledgeError) as exc:
                    await db.rollback()
                    out.append({"i": i, "status": "error", "error": str(exc)[:200]})
        return {"results": out}


async def _decide_one(db, it: DecisionIn) -> str:
    oid = _uuid(it.id, "id")
    accept = it.action == "accept"
    now = datetime.now(UTC)
    if it.kind == "task":
        t = await db.get(ReviewTask, oid)
        if t is None or t.status != "open":
            raise ValueError("tarea no encontrada o ya resuelta (mira review_queue)")
        if t.kind == "contradiction":
            status = (it.contradiction or "real") if accept else "dismissed"
            await compare.classify_contradiction(
                db, uuid.UUID(t.payload["contradiction_id"]), status
            )
            return status
        if t.kind == "entity_merge" and accept:
            if not it.keep:
                raise ValueError("para fusionar indica keep (el id del concepto que se queda)")
            await ks.resolve_review_task(db, oid, "accept", _uuid(it.keep, "keep"))
            return "merged"
        await ks.resolve_review_task(db, oid, "accept" if accept else "reject")
        return "accepted" if accept else "rejected"
    if it.kind == "claim":
        c = await db.get(Claim, oid)
        if c is None:
            raise ValueError("afirmación no encontrada")
        c.review_status = "accepted" if accept else "rejected"
        return c.review_status
    if it.kind == "check":
        ck = await db.get(ClaimCheck, oid)
        if ck is None:
            raise ValueError("comprobación no encontrada")
        ck.status, ck.reviewed_at = ("accepted" if accept else "rejected"), now
        return ck.status
    if it.kind == "link":
        lk = await db.get(SourceLink, oid)
        if lk is None:
            raise ValueError("enlace no encontrado")
        lk.status, lk.reviewed_at = ("accepted" if accept else "rejected"), now
        for t in (
            await db.scalars(
                select(ReviewTask).where(
                    ReviewTask.kind == "source_link",
                    ReviewTask.subject_key == str(lk.id),
                    ReviewTask.status == "open",
                )
            )
        ).all():
            t.status, t.resolved_at = lk.status, now
        return lk.status
    e = await db.get(Entity, oid)
    if e is None or e.status not in ("proposed", "active"):
        raise ValueError("concepto no encontrado o no está pendiente")
    e.status = "active" if accept else "archived"
    return e.status


async def _stage_bytes(store, data: bytes):
    """Stage a note's bytes like an upload (the library dedups by content)."""

    async def chunks():
        yield data

    return await store.stage(chunks(), len(data) + 1)
