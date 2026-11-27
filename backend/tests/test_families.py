"""Families of concepts: proposed by the AI per branch, approved by the user with the
members they keep, shown in the list and the tree, never forming loops."""

import uuid

from sqlalchemy import select

from kos.ai import providers
from kos.ai.providers import ProviderResult, Usage
from kos.knowledge import families as fam
from kos.models import Discipline, Entity, EntityDiscipline

from .test_knowledge import entity, run_jobs
from .test_study import docs_in

NAMES = [
    "Sentadilla",
    "sentadilla búlgara",
    "sentadilla frontal",
    "medias sentadillas",
    "músculo estilohioideo",
    "músculo omohioideo",
    "Proximidad al fallo",
]


class Organizer:
    def __init__(self):
        self.calls = []

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        lines = docs_in(kw)["concepts"].splitlines()
        ref = {line.split(" · ")[1]: line.split(" · ")[0] for line in lines}
        families = []
        if "Sentadilla" in ref:
            families.append(
                fam.FamilyOut(
                    head=ref["Sentadilla"],
                    members=[
                        ref["sentadilla búlgara"],
                        ref["sentadilla frontal"],
                        ref["medias sentadillas"],
                        ref["Sentadilla"],  # never its own member
                        "c999",  # not in the list
                    ],
                    reason="Variantes de la sentadilla.",
                )
            )
        if "músculo omohioideo" in ref:
            families.append(
                fam.FamilyOut(
                    new_head="Músculos del cuello",
                    head_type="concept",
                    members=[ref["músculo estilohioideo"], ref["músculo omohioideo"]],
                    reason="Músculos de la región del cuello.",
                )
            )
        if "Proximidad al fallo" in ref:  # one member: not a family
            families.append(
                fam.FamilyOut(new_head="Fallo", members=[ref["Proximidad al fallo"]], reason="x")
            )
        return ProviderResult(
            parsed=fam.FamiliesOut(families=families),
            stop_reason="end_turn",
            model_served="claude-haiku-5-5",
            usage=Usage(input_tokens=600, output_tokens=200),
            request_id="req",
        )


async def test_families_are_proposed_approved_and_shown(auth_client, db, monkeypatch):
    m = Organizer()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    await auth_client.post("/api/disciplines/seed-training-science")
    fuerza = await db.scalar(select(Discipline.id).where(Discipline.name == "Fuerza"))
    made = {n: await entity(auth_client, n) for n in NAMES}
    ids = [e["id"] for e in made.values()]
    bulk = {"ids": ids, "action": "add_discipline", "discipline_id": str(fuerza)}
    assert (await auth_client.post("/api/concepts/bulk", json=bulk)).status_code == 200

    ov = (await auth_client.get("/api/concepts/families")).json()
    assert ov["without_family"] == 7 and ov["families"] == 0 and ov["usd"] > 0
    r = await auth_client.post("/api/concepts/families/propose", json={})
    assert r.status_code == 202 and r.json() == {"branches": 1}
    assert (await auth_client.post("/api/concepts/families/propose", json={})).status_code == 409
    await run_jobs()
    assert docs_in(m.calls[0])["concepts"].count("\n") == 6  # the 7 concepts of the branch

    props = (await auth_client.get("/api/concepts/families/proposals")).json()
    by_head = {p["head_name"]: p for p in props}
    assert set(by_head) == {"Sentadilla", "Músculos del cuello"}  # one member is no family
    sq = by_head["Sentadilla"]
    assert sq["head_id"] == made["Sentadilla"]["id"] and sq["discipline"] == "Fuerza"
    assert [x["name"] for x in sq["members"]] == [
        "sentadilla búlgara",
        "sentadilla frontal",
        "medias sentadillas",
    ]
    # concepts in a pending proposal are not proposed again
    assert (await auth_client.get("/api/concepts/families")).json()["without_family"] == 2

    keep = [made["sentadilla búlgara"]["id"], made["sentadilla frontal"]["id"]]
    r = await auth_client.post(f"/api/concepts/families/{sq['id']}/accept", json={"members": keep})
    assert r.status_code == 200 and r.json()["members"] == 2
    neck = by_head["Músculos del cuello"]
    accept_neck = f"/api/concepts/families/{neck['id']}/accept"
    members = {"members": [x["id"] for x in neck["members"]]}
    r = await auth_client.post(accept_neck, json=members)
    head = r.json()["head"]
    assert head["name"] == "Músculos del cuello"
    new = await db.get(Entity, uuid.UUID(head["id"]))
    assert await db.get(EntityDiscipline, (new.id, fuerza))  # the new head is in the branch
    again = await auth_client.post(accept_neck, json=members)
    assert again.status_code == 400

    medias = await db.get(Entity, uuid.UUID(made["medias sentadillas"]["id"]))
    await db.refresh(medias)
    assert medias.parent_id is None  # the user unticked it
    family = {"family": made["Sentadilla"]["id"]}
    lst = (await auth_client.get("/api/concepts", params=family)).json()
    assert {x["canonical_name"] for x in lst["items"]} == {
        "Sentadilla",
        "sentadilla búlgara",
        "sentadilla frontal",
    }
    row = next(x for x in lst["items"] if x["canonical_name"] == "sentadilla frontal")
    assert row["family"]["name"] == "Sentadilla"
    assert next(x for x in lst["items"] if x["canonical_name"] == "Sentadilla")["members"] == 2
    loose = (await auth_client.get("/api/concepts", params={"flag": ["no_family"]})).json()
    assert {x["canonical_name"] for x in loose["items"]} == {
        "medias sentadillas",
        "Proximidad al fallo",
    }
    assert (await auth_client.get("/api/concepts/families")).json()["families"] == 2

    tree = (await auth_client.get("/api/graph/tree")).json()

    def find(nodes, name):
        for n in nodes:
            if n.get("name") == name and n["kind"] == "entity":
                return n
            if (hit := find(n["children"], name)) is not None:
                return hit
        return None

    sq_node = find(tree, "Sentadilla")
    assert {(c["name"], c["via"]) for c in sq_node["children"]} == {
        ("sentadilla búlgara", "family"),
        ("sentadilla frontal", "family"),
    }

    # no loops: the head cannot go inside its own member
    loop = {
        "ids": [made["Sentadilla"]["id"], made["sentadilla búlgara"]["id"]],
        "action": "set_family",
        "into": made["sentadilla búlgara"]["id"],
    }
    assert (await auth_client.post("/api/concepts/bulk", json=loop)).json()["changed"] == 0
    out = {"ids": [made["sentadilla frontal"]["id"]], "action": "remove_family"}
    assert (await auth_client.post("/api/concepts/bulk", json=out)).json()["changed"] == 1
