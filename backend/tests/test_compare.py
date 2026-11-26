"""Comparison of explanations, contradictions and AI proposals (ROADMAP Fase 2,
criteria 1 and 3). The model is replaced by a fake; no network."""

import uuid

import pytest
from sqlalchemy import func, select

from kos.ai import providers
from kos.ai.providers import ProviderResult, Usage
from kos.config import get_settings
from kos.db import get_sessionmaker
from kos.jobs import worker
from kos.knowledge import ai_jobs
from kos.knowledge import service as ks
from kos.models import (
    AIRun,
    Claim,
    ClaimEntity,
    Contradiction,
    Course,
    CourseMaterial,
    Evidence,
    Job,
    JobStatus,
    Person,
    Relationship,
    ReviewTask,
    Source,
    SourceSegment,
    SourceVersion,
)


class FakeProvider:
    def __init__(self):
        self.result = None
        self.calls = []

    async def complete_structured(self, **kw):
        self.calls.append(kw)
        return ProviderResult(
            parsed=self.result,
            stop_reason="end_turn",
            model_served=kw["model"],
            usage=Usage(input_tokens=500, output_tokens=200),
        )


@pytest.fixture
def fake(monkeypatch):
    f = FakeProvider()
    monkeypatch.setattr(providers, "get_provider", lambda: f)
    return f


async def run_jobs():
    while await worker.run_once(get_sessionmaker(), get_settings(), "test"):
        pass


async def source_with_segment(db, title, text, n):
    src = Source(title=title, kind="document")
    db.add(src)
    await db.flush()
    ver = SourceVersion(
        source_id=src.id,
        sha256=f"{n:064d}",
        storage_key=f"raw/x/{n}",
        mime="application/pdf",
        size_bytes=1,
    )
    db.add(ver)
    await db.flush()
    seg = SourceSegment(
        source_version_id=ver.id, ordinal=0, text=text, token_count=10, loc_page_start=n
    )
    db.add(seg)
    await db.flush()
    return src, seg


async def claim(db, entity_ids, text, seg, quote, person=None, ctype="definition"):
    c = Claim(
        text=text,
        claim_type=ctype,
        attributed_person_id=person.id if person else None,
        review_status="accepted",
    )
    db.add(c)
    await db.flush()
    for eid in entity_ids:
        db.add(ClaimEntity(claim_id=c.id, entity_id=eid))
    pos = seg.text.index(quote)
    db.add(
        Evidence(
            claim_id=c.id,
            segment_id=seg.id,
            quote=quote,
            quote_char_start=pos,
            quote_char_end=pos + len(quote),
        )
    )
    await db.commit()
    return c


@pytest.fixture
async def principle(db):
    """'Proximidad al fallo' explained by three teachers and one course without a teacher."""
    e = await ks.create_entity(db, type_key="principle", name="Proximidad al fallo")
    fat = await ks.create_entity(db, type_key="concept", name="Fatiga")
    hip = await ks.create_entity(db, type_key="concept", name="Hipertrofia")
    teachers = []
    for name in ("Profesora A", "Profesor B", "Profesora C"):
        p = Person(name=name, role="teacher")
        db.add(p)
        teachers.append(p)
    await db.flush()
    _, s1 = await source_with_segment(
        db, "Curso A", "Entrenar a 0-3 RIR maximiza la hipertrofia.", 1
    )
    _, s2 = await source_with_segment(
        db, "Curso B", "Las repeticiones en reserva no importan si se iguala el volumen.", 2
    )
    _, s3 = await source_with_segment(db, "Curso C", "Llegar al fallo aumenta mucho la fatiga.", 3)
    src4, s4 = await source_with_segment(db, "Apuntes D", "El RIR es la cercanía al fallo.", 4)
    course = Course(title="Curso D (sin profesor)")
    db.add(course)
    await db.flush()
    db.add(CourseMaterial(course_id=course.id, source_id=src4.id))
    c1 = await claim(
        db,
        [e.id, hip.id],
        "0-3 RIR maximiza la hipertrofia",
        s1,
        "0-3 RIR maximiza la hipertrofia",
        teachers[0],
        "recommendation",
    )
    c2 = await claim(
        db,
        [e.id],
        "La proximidad al fallo no importa a igual volumen",
        s2,
        "no importan si se iguala el volumen",
        teachers[1],
        "interpretation",
    )
    c3 = await claim(
        db,
        [e.id, fat.id],
        "El fallo aumenta la fatiga",
        s3,
        "aumenta mucho la fatiga",
        teachers[2],
        "reported_fact",
    )
    c4 = await claim(
        db, [e.id], "El RIR mide la cercanía al fallo", s4, "El RIR es la cercanía al fallo"
    )
    return {"entity": e, "fat": fat, "hip": hip, "claims": [c1, c2, c3, c4]}


async def test_comparison_groups_claims_by_perspective(auth_client, principle):
    r = await auth_client.get(f"/api/entities/{principle['entity'].id}/comparison")
    data = r.json()
    labels = sorted(p["label"] for p in data["perspectives"])
    assert labels == ["Curso D (sin profesor)", "Profesor B", "Profesora A", "Profesora C"]
    course = next(p for p in data["perspectives"] if p["kind"] == "course")
    ref = course["claims"][0]["ref"]
    assert ref["quote"] == "El RIR es la cercanía al fallo" and ref["page"] == 4
    assert data["analysis"] is None and data["contradictions"] == []


async def test_manual_contradiction_is_classified_never_deleted(auth_client, principle, db):
    c1, c2 = principle["claims"][:2]
    r = await auth_client.post(
        "/api/contradictions", json={"claim_a_id": str(c2.id), "claim_b_id": str(c1.id)}
    )
    assert r.status_code == 201 and r.json()["status"] == "real"
    again = await auth_client.post(
        "/api/contradictions", json={"claim_a_id": str(c1.id), "claim_b_id": str(c2.id)}
    )
    assert again.json()["id"] == r.json()["id"]
    p = await auth_client.patch(
        f"/api/contradictions/{r.json()['id']}",
        json={"status": "context_difference", "analysis": "Hablan de volúmenes distintos"},
    )
    assert p.json()["status"] == "context_difference"
    assert await db.scalar(select(func.count()).select_from(Claim)) == 4
    same = await auth_client.post(
        "/api/contradictions", json={"claim_a_id": str(c1.id), "claim_b_id": str(c1.id)}
    )
    assert same.status_code == 400


async def test_phase2_criterion1_ai_comparison_linked_to_claims(auth_client, principle, fake, db):
    """A principle explained by >= 3 teachers: agreements, terminology and conceptual
    differences and contradictions, each linked to claims (and so to fragments)."""
    c1, c2, c3, c4 = [str(c.id) for c in principle["claims"]]
    fake.result = ai_jobs.ComparisonResult(
        summary="Coinciden en medir la cercanía al fallo; discrepan en su importancia.",
        agreements=[
            ai_jobs.Finding(
                description="El RIR mide la cercanía al fallo", claim_ids=[c1, f"claim:{c4}"]
            )
        ],
        terminology_differences=[
            ai_jobs.Finding(description="RIR frente a proximidad al fallo", claim_ids=[c2, c4])
        ],
        conceptual_differences=[
            ai_jobs.Finding(description="Hipertrofia frente a fatiga", claim_ids=[c1, c3])
        ],
        context_differences=[
            ai_jobs.Finding(description="inventado", claim_ids=[str(uuid.uuid4())])
        ],
        possible_contradictions=[
            ai_jobs.PossibleContradiction(
                claim_a=c1, claim_b=c2, explanation="Una dice que importa y otra que no"
            ),
            ai_jobs.PossibleContradiction(
                claim_a=c1, claim_b=str(uuid.uuid4()), explanation="inventada"
            ),
        ],
        open_questions=["¿Influye el nivel del deportista?"],
    )
    r = await auth_client.post(f"/api/entities/{principle['entity'].id}/comparison/analyze")
    assert r.status_code == 202
    assert (
        await auth_client.post(f"/api/entities/{principle['entity'].id}/comparison/analyze")
    ).status_code == 409
    await run_jobs()
    job = await db.scalar(select(Job).where(Job.kind == ai_jobs.COMPARE_KIND))
    assert job.status == JobStatus.completed, job.last_error
    assert job.result == {"findings": 5, "contradictions": 1, "dropped": 2}

    # what the model received: one delimited document per claim, labelled by perspective
    sent = fake.calls[0]["user_content"][1]["text"]
    assert sent.count("<source_document ref=") == 4 and "Profesora C" in sent
    assert fake.calls[0]["system"].count("Proximidad al fallo") == 1

    data = (await auth_client.get(f"/api/entities/{principle['entity'].id}/comparison")).json()
    res = data["analysis"]["result"]
    assert res["agreements"][0]["claim_ids"] == [c1, c4]  # "claim:" prefix normalised
    assert res["context_differences"] == []  # finding citing an unknown claim dropped
    assert data["analysis"]["stale"] is False
    assert [(x["claim_a_id"], x["claim_b_id"]) for x in data["contradictions"]] == [
        tuple(sorted((c1, c2)))
    ]
    assert data["contradictions"][0]["status"] == "candidate"
    task = await db.scalar(select(ReviewTask).where(ReviewTask.kind == "contradiction"))
    assert task.status == "open"
    run = await db.scalar(select(AIRun))
    assert run.task == "compare" and run.status == "ok" and run.job_id == job.id

    # classifying the candidate closes its review task
    cid = data["contradictions"][0]["id"]
    await auth_client.patch(f"/api/contradictions/{cid}", json={"status": "real"})
    await db.refresh(task)
    assert task.status == "accepted"

    # a new claim makes the stored analysis stale
    _, s5 = await source_with_segment(db, "Curso E", "El fallo es opcional.", 5)
    await claim(db, [principle["entity"].id], "El fallo es opcional", s5, "El fallo es opcional")
    assert (await auth_client.get(f"/api/entities/{principle['entity'].id}/comparison")).json()[
        "analysis"
    ]["stale"]


async def test_comparison_needs_two_perspectives(auth_client, db, fake):
    e = await ks.create_entity(db, type_key="concept", name="Solo uno")
    _, s = await source_with_segment(db, "Curso único", "Un único texto.", 9)
    await claim(db, [e.id], "Única", s, "Un único texto")
    await auth_client.post(f"/api/entities/{e.id}/comparison/analyze")
    await run_jobs()
    job = await db.scalar(select(Job).where(Job.kind == ai_jobs.COMPARE_KIND))
    assert job.status == JobStatus.failed and "al menos dos" in job.last_error
    assert fake.calls == []  # no model call, no cost


async def test_analysis_without_credentials_fails_cleanly(auth_client, principle, db, monkeypatch):
    for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", "/nonexistent-config")
    monkeypatch.setattr(providers, "get_provider", providers.AnthropicProvider)
    await auth_client.post(f"/api/entities/{principle['entity'].id}/comparison/analyze")
    await run_jobs()
    job = await db.scalar(select(Job).where(Job.kind == ai_jobs.COMPARE_KIND))
    assert job.status == JobStatus.failed and job.attempts == 1
    assert "credentials" in job.last_error.lower()


async def test_proposed_relationships_are_validated_and_go_to_review(
    auth_client, principle, fake, db
):
    e, fat, hip = principle["entity"], principle["fat"], principle["hip"]
    c1, _, c3, _ = [str(c.id) for c in principle["claims"]]
    fake.result = ai_jobs.RelationshipProposals(
        proposals=[
            ai_jobs.ProposedRelationship(
                source_id=str(e.id),
                target_id=str(fat.id),
                type_key="influences",
                claim_ids=[c3],
                condition="cerca del fallo",
                confidence=0.8,
            ),
            # hip is not mentioned by c3: rejected
            ai_jobs.ProposedRelationship(
                source_id=str(e.id),
                target_id=str(hip.id),
                type_key="contributes_to",
                claim_ids=[c3],
                confidence=0.6,
            ),
            # unknown relationship type: rejected
            ai_jobs.ProposedRelationship(
                source_id=str(e.id),
                target_id=str(hip.id),
                type_key="magic",
                claim_ids=[c1],
                confidence=0.9,
            ),
        ]
    )
    r = await auth_client.post(f"/api/entities/{e.id}/relationships/propose")
    assert r.status_code == 202
    await run_jobs()
    job = await db.scalar(select(Job).where(Job.kind == ai_jobs.PROPOSE_KIND))
    assert job.status == JobStatus.completed and job.result == {"proposed": 1, "discarded": 2}
    rel = await db.scalar(select(Relationship))
    assert (rel.source_entity_id, rel.target_entity_id, rel.status, rel.origin) == (
        e.id,
        fat.id,
        "proposed",
        "ai",
    )
    assert rel.conditions == "cerca del fallo" and rel.ai_run_id is not None
    assert (
        await db.scalar(
            select(func.count()).select_from(ReviewTask).where(ReviewTask.kind == "relationship")
        )
        == 1
    )
    # only claims mentioning >= 2 concepts were sent
    assert fake.calls[0]["user_content"][1]["text"].count("<source_document ref=") == 2


async def test_contradictions_in_review_queue_can_be_listed(auth_client, principle, fake, db):
    c1, c2 = principle["claims"][:2]
    from kos.knowledge import compare

    await compare.add_contradiction(db, c1.id, c2.id, "IA: incompatibles", origin="ai")
    tasks = (await auth_client.get("/api/review-tasks")).json()
    assert (
        tasks[0]["kind"] == "contradiction"
        and tasks[0]["payload"]["analysis"] == "IA: incompatibles"
    )
    claim = (await auth_client.get(f"/api/claims/{c1.id}")).json()
    assert {e["name"] for e in claim["entities"]} == {"Proximidad al fallo", "Hipertrofia"}
    assert await db.scalar(select(func.count()).select_from(Contradiction)) == 1
