"""Possible duplicates judged by the AI: «same» stays for the user with the name to
keep, «different» and «related» are closed (and can be reopened), nothing merged."""

import re
import uuid

from sqlalchemy import select

from kos.ai import providers
from kos.ai.providers import ProviderResult, Usage
from kos.knowledge import duplicates as dup
from kos.models import ReviewTask

from .test_knowledge import entity, run_jobs
from .test_study import docs_in

SAME = {"ejercicio de facilitación", "ejercicios de facilitación"}
KIND_OF = {"prevención de lesiones de LCA", "prevención de lesiones"}


class Judge:
    def __init__(self):
        self.calls = []

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        items = []
        for line in docs_in(kw)["pairs"].splitlines():
            ref = line.split(" · ")[0]
            a, b = re.findall(r"«([^»]+)»", line)[:2]
            if {a, b} == SAME:
                keep = "a" if "ejercicio " in a else "b"
                why = "plural del mismo ejercicio"
                v = dup.VerdictOut(ref=ref, verdict="same", keep=keep, reason=why)
            elif {a, b} == KIND_OF:
                rel = "a_kind_of_b" if "LCA" in a else "b_kind_of_a"
                why = "es un tipo de prevención de lesiones"
                v = dup.VerdictOut(ref=ref, verdict="related", relation=rel, reason=why)
            else:
                v = dup.VerdictOut(ref=ref, verdict="different", reason="son músculos distintos")
            items.append(v)
        return ProviderResult(
            parsed=dup.JudgeOut(items=items),
            stop_reason="end_turn",
            model_served="claude-haiku-5-5",
            usage=Usage(input_tokens=400, output_tokens=150),
            request_id="req",
        )


async def test_the_ai_sorts_out_which_pairs_are_duplicates(auth_client, db, monkeypatch):
    m = Judge()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    made = {}
    inter_names = ("músculos intercostales externos", "músculos intercostales internos")
    for name in [*SAME, *KIND_OF, *inter_names]:
        made[name] = await entity(auth_client, name)

    r = await auth_client.post("/api/concepts/duplicates/scan")
    assert r.status_code == 202
    assert (await auth_client.post("/api/concepts/duplicates/scan")).status_code == 409
    await run_jobs()
    assert m.calls, "the AI judged the pairs"

    open_ = (await auth_client.get("/api/review-tasks")).json()
    merges = [t for t in open_ if t["kind"] == "entity_merge"]
    assert len(merges) == 1
    t = merges[0]
    assert {t["payload"]["name_a"], t["payload"]["name_b"]} == SAME
    assert t["payload"]["ai"]["verdict"] == "same"
    assert t["payload"]["ai"]["keep"] == made["ejercicio de facilitación"]["id"]

    closed = (await auth_client.get("/api/concepts/duplicates/rejected")).json()
    by_names = {frozenset((x["payload"]["name_a"], x["payload"]["name_b"])): x for x in closed}
    lca = by_names[frozenset(KIND_OF)]
    assert lca["note"].startswith("IA: relacionados") and lca["payload"]["ai"]["relation"]
    inter = by_names[frozenset(inter_names)]
    assert inter["note"] == "IA: distintos. son músculos distintos"

    st = (await auth_client.get("/api/concepts/duplicates")).json()
    assert st["same"] == 1 and st["unjudged"] == 0 and st["running"] == 0
    assert st["rejected_by_ai"] == len(closed)

    # the user disagrees with the AI: back to the list, and it is not judged again
    r = await auth_client.post(f"/api/concepts/duplicates/{inter['id']}/reopen")
    assert r.status_code == 200
    calls = len(m.calls)
    await auth_client.post("/api/concepts/duplicates/scan")
    await run_jobs()
    assert len(m.calls) == calls  # nothing new to judge: closed pairs are not proposed again
    reopened = await db.get(ReviewTask, uuid.UUID(inter["id"]))
    await db.refresh(reopened)
    assert reopened.status == "open"
    # nothing was merged by the AI
    assert not (await db.scalars(select(ReviewTask).where(ReviewTask.status == "accepted"))).all()
