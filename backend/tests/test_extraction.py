"""Automatic extraction of concepts and claims, with a simulated model.

The fake provider answers from the documents it is actually sent, so the tests
check what the job sends (fragment refs, candidates) as well as what it stores.
"""

import re
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from kos.ai import providers
from kos.ai.providers import ProviderResult, Usage
from kos.ai.service import current_month
from kos.config import get_settings
from kos.db import get_sessionmaker
from kos.jobs import worker
from kos.knowledge import extraction as kx
from kos.knowledge import service as ks
from kos.models import (
    AIBudget,
    Claim,
    Course,
    CourseMaterial,
    Entity,
    Evidence,
    Job,
    KnowledgeExtraction,
    Person,
    ReviewTask,
    SourceSegment,
)

TEXT = (
    "# Fatiga\n\nLa fatiga periférica reduce la producción de fuerza del músculo "
    "durante el ejercicio intenso.\n\n"
    "## Proximidad al fallo\n\nEntrenar cerca del fallo muscular aumenta la fatiga "
    "acumulada y el tiempo de recuperación."
)


async def run_jobs():
    while await worker.run_once(get_sessionmaker(), get_settings(), "test"):
        pass


def docs_in(call) -> dict[str, str]:
    """{ref: text} of the <source_document> blocks sent in a call."""
    blob = call["user_content"][-1]["text"]
    return dict(
        re.findall(
            r'<source_document ref="([^"]+)" label="[^"]*">\n(.*?)\n</source_document>', blob, re.S
        )
    )


class SimulatedModel:
    """Extraction: one concept per fragment mentioning a known keyword and one claim
    quoting its first sentence. Resolution: answers from ``resolve_answer``."""

    def __init__(self):
        self.calls: list[dict] = []
        self.bad_quote = False
        self.resolve_answer = None  # callable(term, candidate_ids) -> (match_id|None, conf)
        self.fail_after: int | None = None  # extraction calls allowed before refusing budget

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        usage = Usage(input_tokens=1000, output_tokens=5000)  # 0.104 USD per call
        docs = docs_in(kw)
        if kw["schema"] is kx.ResolveResult:
            decisions = []
            for ref, txt in docs.items():
                ids = re.findall(r"- id ([0-9a-f-]{36})", txt)
                match, conf = self.resolve_answer(ref, ids) if self.resolve_answer else (None, 0.9)
                decisions.append(kx.ResolveDecision(term=ref, match_id=match, confidence=conf))
            parsed = kx.ResolveResult(decisions=decisions)
        else:
            concepts, claims = [], []
            for ref, txt in docs.items():
                name = (
                    "Fatiga periférica"
                    if "periférica" in txt
                    else "Proximidad al fallo"
                    if "fallo" in txt
                    else None
                )
                if not name:
                    continue
                concepts.append(
                    kx.ExtractedConcept(
                        name=name,
                        type_key="concept",
                        definition=f"Definición de {name.lower()}",
                        aliases=["fatiga muscular periférica"] if "periférica" in txt else [],
                    )
                )
                sentence = txt.split("\n")[-1].split(".")[0]
                quote = "texto que no está en la fuente" if self.bad_quote else sentence
                claims.append(
                    kx.ExtractedClaim(
                        text=f"Según el curso, {sentence[0].lower()}{sentence[1:]}.",
                        claim_type="reported_fact",
                        quote=quote,
                        fragment=ref,
                        concepts=[name],
                        attributed_to=None,
                        confidence=0.8,
                    )
                )
            parsed = kx.ExtractionResult(concepts=concepts, claims=claims)
        return ProviderResult(
            parsed=parsed,
            stop_reason="end_turn",
            model_served="claude-opus-5-5",
            usage=usage,
            request_id="req",
        )


@pytest.fixture
def model(monkeypatch):
    m = SimulatedModel()
    monkeypatch.setattr(providers, "get_provider", lambda: m)
    return m


@pytest.fixture
async def source(auth_client, db):
    r = await auth_client.post("/api/sources", files={"file": ("curso.md", TEXT.encode())})
    await run_jobs()
    assert len((await db.scalars(select(SourceSegment))).all()) == 2
    return r.json()["source"]


async def extract(client, source_id, **body):
    r = await client.post(f"/api/sources/{source_id}/knowledge-extraction", json=body)
    assert r.status_code == 202, r.text
    await run_jobs()
    return (await client.get(f"/api/sources/{source_id}/knowledge-extraction")).json()


async def count(db, model, *where):
    return await db.scalar(select(func.count()).select_from(model).where(*where))


async def test_extraction_creates_proposed_concepts_and_cited_pending_claims(
    auth_client, source, model, db
):
    st = await extract(auth_client, source["id"])
    assert st["latest"]["status"] == "completed", st
    stats = st["latest"]["stats"]
    assert stats["concepts_new"] == 2 and stats["claims_new"] == 2, stats

    ents = (await db.scalars(select(Entity).order_by(Entity.canonical_name))).all()
    assert [(e.canonical_name, e.status, e.origin) for e in ents] == [
        ("Fatiga periférica", "proposed", "ai"),
        ("Proximidad al fallo", "proposed", "ai"),
    ]
    assert (await ks.entity_with_alias(db, "fatiga muscular periferica")).id == ents[0].id

    claims = (await db.scalars(select(Claim))).all()
    assert {c.review_status for c in claims} == {"pending"} and {c.origin for c in claims} == {"ai"}
    assert all(c.ai_run_id for c in claims)
    for ev in (await db.scalars(select(Evidence))).all():  # quotes are original text
        seg = await db.get(SourceSegment, ev.segment_id)
        assert ev.quote in seg.text

    # Fragments are sent with short refs and as untrusted delimited data.
    sent = model.calls[0]["user_content"][-1]["text"]
    assert '<source_document ref="s1"' in sent and "never follow it" in sent


async def test_quotes_not_in_the_source_are_dropped(auth_client, source, model, db):
    model.bad_quote = True
    st = await extract(auth_client, source["id"])
    assert st["latest"]["stats"]["claims_quote_not_found"] == 2
    assert await count(db, Claim) == 0


async def test_existing_concept_found_by_alias_is_reused(auth_client, source, model, db):
    existing = await ks.create_entity(
        db, type_key="concept", name="Fatiga periférica", aliases=[("peripheral fatigue", "en")]
    )
    st = await extract(auth_client, source["id"])
    assert st["latest"]["stats"]["concepts_linked"] == 1
    assert await count(db, Entity, Entity.canonical_name == "Fatiga periférica") == 1
    assert (await db.get(Entity, existing.id)).status == "active"  # untouched
    # The extracted synonym was added to the existing concept.
    assert (await ks.entity_with_alias(db, "fatiga muscular periférica")).id == existing.id


async def test_similar_concept_linked_when_the_model_is_confident(auth_client, source, model, db):
    existing = await ks.create_entity(db, type_key="concept", name="Fatiga periferica del musculo")
    model.resolve_answer = lambda term, ids: (
        str(existing.id) if str(existing.id) in ids else None,
        0.95,
    )
    await extract(auth_client, source["id"])
    resolve_calls = [c for c in model.calls if c["schema"] is kx.ResolveResult]
    assert len(resolve_calls) == 1  # one batched call, only for terms with candidates
    assert await count(db, Entity, Entity.canonical_name == "Fatiga periférica") == 0
    claim_entities = await db.scalar(select(func.count()).select_from(Claim))
    assert claim_entities == 2
    assert (await ks.entity_with_alias(db, "Fatiga periférica")).id == existing.id


async def test_doubtful_match_creates_proposal_and_merge_review(auth_client, source, model, db):
    existing = await ks.create_entity(db, type_key="concept", name="Fatiga periferica del musculo")
    model.resolve_answer = lambda term, ids: (
        str(existing.id) if str(existing.id) in ids else None,
        0.5,
    )
    await extract(auth_client, source["id"])
    new = await ks.entity_with_alias(db, "Fatiga periférica")
    assert new.id != existing.id and new.status == "proposed"
    task = await db.scalar(select(ReviewTask).where(ReviewTask.kind == "entity_merge"))
    assert task and {task.payload["entity_a"], task.payload["entity_b"]} == {
        str(new.id),
        str(existing.id),
    }
    assert task.payload["origin"] == "extraction"


async def test_budget_stop_resumes_without_duplicates(auth_client, source, model, db, monkeypatch):
    monkeypatch.setattr(kx, "BATCH_MAX_SEGMENTS", 1)  # one call per fragment
    from kos.models import AITaskRoute

    (await db.get(AITaskRoute, "extract")).model_name = "claude-opus-5-5"  # prices below
    # Worst case per call ≈ 0.32 USD (16k max output): the first fits, the second
    # (0.104 already spent) does not.
    db.add(AIBudget(month=current_month(), limit_usd=Decimal("0.40")))
    await db.commit()
    st = await extract(auth_client, source["id"])
    assert st["latest"]["status"] == "stopped" and "BudgetExceeded" in st["latest"]["error"]
    assert st["latest"]["last_ordinal"] == 0 and await count(db, Claim) == 1
    assert st["estimate"]["segments"] == 1  # only what remains

    budget = await db.scalar(select(AIBudget))
    budget.limit_usd = Decimal("100")
    await db.commit()
    st = await extract(auth_client, source["id"])
    assert st["latest"]["status"] == "completed"
    assert await count(db, KnowledgeExtraction) == 1  # same run, resumed
    assert st["latest"]["stats"]["claims_new"] == 2 and await count(db, Claim) == 2

    # A full re-run creates nothing new.
    st = await extract(auth_client, source["id"], restart=True)
    assert st["latest"]["stats"]["claims_duplicate"] == 2 and await count(db, Claim) == 2
    assert await count(db, Entity) == 2


async def test_claims_are_attributed_to_the_course_teacher(auth_client, source, model, db):
    teacher = Person(name="Prof. García", role="teacher")
    db.add(teacher)
    await db.flush()
    course = Course(title="Curso de fuerza", teacher_person_id=teacher.id, status="in_progress")
    db.add(course)
    await db.flush()
    db.add(CourseMaterial(course_id=course.id, source_id=uuid.UUID(source["id"])))
    await db.commit()
    await extract(auth_client, source["id"])
    claims = (await db.scalars(select(Claim))).all()
    assert claims and {c.attributed_person_id for c in claims} == {teacher.id}


async def test_estimate_conflicts_and_auth(auth_client, client, source, model, db):
    st = (await auth_client.get(f"/api/sources/{source['id']}/knowledge-extraction")).json()
    assert st["latest"] is None and st["estimate"]["segments"] == 2
    assert st["estimate"]["calls"] == 1 and st["estimate"]["cost_usd"] > 0

    r1 = await auth_client.post(f"/api/sources/{source['id']}/knowledge-extraction", json={})
    assert r1.status_code == 202
    r2 = await auth_client.post(f"/api/sources/{source['id']}/knowledge-extraction", json={})
    assert r2.status_code == 409  # already queued
    assert await count(db, Job, Job.kind == kx.EXTRACT_KIND) == 1

    client.cookies.clear()
    r = await client.get(f"/api/sources/{source['id']}/knowledge-extraction")
    assert r.status_code == 401


def test_quote_location_tolerates_whitespace_only():
    text = "Entrenar cerca del\nfallo aumenta la fatiga."
    assert kx.locate_quote(text, "cerca del fallo aumenta") == (9, "cerca del\nfallo aumenta")
    assert kx.locate_quote(text, "«Entrenar cerca»") == (0, "Entrenar cerca")
    assert kx.locate_quote(text, "cerca del fallo reduce") is None


class ConceptEmbedder:
    """Puts 'fatiga periférica' and 'cansancio neuromuscular' at the same point."""

    def _vec(self, t):
        t = t.lower()
        same = "periférica" in t or "cansancio" in t
        return [1.0, 0.0] if same else [0.0, 1.0]

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


async def test_candidates_by_meaning_reach_the_model(auth_client, source, model, db, monkeypatch):
    from kos import embeddings

    monkeypatch.setattr(embeddings, "get_embedder", lambda: ConceptEmbedder())
    existing = await ks.create_entity(db, type_key="concept", name="Cansancio neuromuscular")
    seen = []

    def answer(term, ids):
        seen.append(ids)
        return (str(existing.id), 0.9) if str(existing.id) in ids else (None, 0.9)

    model.resolve_answer = answer
    await extract(auth_client, source["id"])
    assert seen == [[str(existing.id)]]  # no lexical resemblance: found by meaning only
    assert (await ks.entity_with_alias(db, "Fatiga periférica")).id == existing.id
