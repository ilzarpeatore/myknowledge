"""Concepts put in branches by the AI: real branch names only, at most two, marked
as the AI's, types fixed only on what the AI created, the user's never touched."""

import uuid

from sqlalchemy import select

from kos.ai import providers
from kos.ai.providers import ProviderResult, Usage
from kos.knowledge import classify as cl
from kos.models import Discipline, Entity, EntityDiscipline

from .test_knowledge import entity, run_jobs
from .test_study import docs_in

ANSWERS = {
    "músculo glúteo medio": (["Anatomía", "Biomecánica"], "concept"),
    "glucógeno": (["nutricion", "Fisiología", "Recuperación"], "concept"),  # 3: two kept
    "Sentadilla búlgara": (["Rama inventada", "Fuerza"], "methodology"),
    "Alwyn Cosgrove": ([], "author"),
}


class Librarian:
    def __init__(self):
        self.calls = []

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        lines = docs_in(kw)["concepts"].splitlines()
        items = []
        for line in lines:
            ref, name = line.split(" · ")[:2]
            branches, type_key = ANSWERS[name]
            items.append(cl.ItemOut(ref=ref, branches=branches, type_key=type_key))
        return ProviderResult(
            parsed=cl.ClassifyOut(items=items),
            stop_reason="end_turn",
            model_served="claude-haiku-5-5",
            usage=Usage(input_tokens=500, output_tokens=200),
            request_id="req",
        )


async def test_the_ai_places_concepts_in_real_branches(auth_client, db, monkeypatch):
    m = Librarian()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    await auth_client.post("/api/disciplines/seed-training-science")
    b = {d.name: d.id for d in (await db.scalars(select(Discipline))).all()}
    made = {n: await entity(auth_client, n) for n in ANSWERS}
    mine = await entity(auth_client, "Proximidad al fallo")  # already in a branch: not sent
    await auth_client.put(
        f"/api/entities/{mine['id']}/disciplines", json={"discipline_ids": [str(b["Fuerza"])]}
    )
    ai_made = await db.get(Entity, uuid.UUID(made["músculo glúteo medio"]["id"]))
    ai_made.origin, ai_made.type_key = "ai", "variable"  # the AI's mistake, to be fixed
    user_made = await db.get(Entity, uuid.UUID(made["Sentadilla búlgara"]["id"]))
    user_made.type_key = "concept"  # the user's choice: stays
    await db.commit()

    st = (await auth_client.get("/api/concepts/classify")).json()
    assert st["unassigned"] == 4 and st["batches_left"] == 0 and st["usd"] > 0
    r = await auth_client.post("/api/concepts/classify")
    assert r.status_code == 202 and r.json() == {"queued": 4, "batches": 1}
    assert (await auth_client.post("/api/concepts/classify")).status_code == 409
    await run_jobs()

    sent = docs_in(m.calls[0])
    assert "Proximidad al fallo" not in sent["concepts"] and "Fuerza" in sent["branches"]
    rows = (await db.execute(select(EntityDiscipline))).scalars().all()
    got: dict[str, set] = {}
    names = {v: k for k, v in b.items()}
    for row in rows:
        e = await db.get(Entity, row.entity_id)
        got.setdefault(e.canonical_name, set()).add((names[row.discipline_id], row.origin))
    assert got == {
        "músculo glúteo medio": {("Anatomía", "ai"), ("Biomecánica", "ai")},
        "glucógeno": {("Nutrición", "ai"), ("Fisiología", "ai")},
        "Sentadilla búlgara": {("Fuerza", "ai")},
        "Proximidad al fallo": {("Fuerza", "user")},
    }
    await db.refresh(ai_made)
    await db.refresh(user_made)
    assert ai_made.type_key == "concept" and user_made.type_key == "concept"

    # the person it could not place is not sent again
    st = (await auth_client.get("/api/concepts/classify")).json()
    assert st["unassigned"] == 0 and st["left_out"] == 1
    assert await cl.enqueue(db) == 0

    flagged = (await auth_client.get("/api/concepts", params={"flag": ["ai_branch"]})).json()
    assert flagged["total"] == 3
    gl = made["glucógeno"]["id"]
    confirm = {"ids": [gl], "action": "confirm_branches"}
    r = await auth_client.post("/api/concepts/bulk", json=confirm)
    assert r.json()["changed"] == 2
    flagged = (await auth_client.get("/api/concepts", params={"flag": ["ai_branch"]})).json()
    assert flagged["total"] == 2
