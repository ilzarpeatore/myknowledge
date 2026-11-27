"""Reports computed from what is stored (no AI at read time): what a source adds,
where each non-study claim stands against the studies, and a teacher's profile."""

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.analysis.ai_jobs import ANALYZE_KIND, claim_sources, neighbours, source_claims
from kos.analysis.vectors import claim_vectors, cosine
from kos.models import (
    Claim,
    ClaimCheck,
    ClaimEntity,
    Contradiction,
    Entity,
    Evidence,
    Job,
    Person,
    Publication,
    ReferenceMention,
    Source,
    SourceLink,
    SourceSegment,
    SourceVersion,
)

CONFIRM_SIMILARITY = 0.95  # same statement (D-23)

STATUS_TEXT = {
    "supported": "Respaldada por estudios",
    "contradicted": "Contradicha por estudios",
    "partial": "Respaldada en parte",
    "mixed": "Estudios a favor y en contra",
    "unchecked": "Hay estudios sobre el tema, sin comprobar",
    "no_studies": "Sin estudios sobre el tema en tu biblioteca",
    "study": "Afirmación de un estudio",
}


async def _contradicted(db: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, set[uuid.UUID]]:
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    if not ids:
        return out
    for a, b in (
        await db.execute(
            select(Contradiction.claim_a_id, Contradiction.claim_b_id).where(
                Contradiction.status.in_(("candidate", "real")),
                (Contradiction.claim_a_id.in_(ids)) | (Contradiction.claim_b_id.in_(ids)),
            )
        )
    ).all():
        out[a].add(b)
        out[b].add(a)
    return out


async def study_status(db: AsyncSession, claims: list[Claim]) -> dict[uuid.UUID, dict[str, Any]]:
    """For each claim: its standing against the studies, with the checks behind it.
    Rejected checks do not count; proposed ones count but are marked as such."""
    ids = [c.id for c in claims]
    checks = (
        await db.scalars(
            select(ClaimCheck).where(
                ClaimCheck.claim_id.in_(ids),
                ClaimCheck.status != "rejected",
                ClaimCheck.verdict != "unrelated",
            )
        )
    ).all()
    by_claim: dict[uuid.UUID, list[ClaimCheck]] = defaultdict(list)
    for ck in checks:
        by_claim[ck.claim_id].append(ck)
    near = await neighbours(db, [c for c in claims if not c.publication_id])
    out = {}
    for c in claims:
        if c.publication_id:
            out[c.id] = {"status": "study", "text": STATUS_TEXT["study"], "checks": []}
            continue
        cks = by_claim.get(c.id, [])
        verdicts = {ck.verdict for ck in cks}
        if "supports" in verdicts and "contradicts" in verdicts:
            st = "mixed"
        elif "contradicts" in verdicts:
            st = "contradicted"
        elif "supports" in verdicts:
            st = "supported"
        elif "partial" in verdicts:
            st = "partial"
        elif any(o.publication_id for o in near.get(c.id, [])):
            st = "unchecked"
        else:
            st = "no_studies"
        out[c.id] = {
            "status": st,
            "text": STATUS_TEXT[st],
            "pending_review": any(ck.status == "proposed" for ck in cks),
            "checks": [await check_out(db, ck) for ck in cks],
        }
    return out


async def check_out(db: AsyncSession, ck: ClaimCheck) -> dict[str, Any]:
    study = await db.get(Claim, ck.study_claim_id)
    pub = await db.get(Publication, study.publication_id) if study.publication_id else None
    return {
        "id": str(ck.id),
        "verdict": ck.verdict,
        "explanation": ck.explanation,
        "confidence": ck.confidence,
        "status": ck.status,
        "origin": ck.origin,
        "study_claim": {"id": str(study.id), "text": study.text},
        "publication": (
            {
                "id": str(pub.id),
                "title": pub.title,
                "year": pub.year,
                "quality": pub.methodological_quality,
            }
            if pub
            else None
        ),
    }


async def contribution(db: AsyncSession, source_id: uuid.UUID) -> dict[str, Any]:
    """What this source adds to the library: new concepts (nobody else talks about
    them), claims confirmed elsewhere, claims in contradiction, references."""
    mine = await source_claims(db, source_id)
    ids = [c.id for c in mine]
    ents = (
        await db.execute(
            select(ClaimEntity.entity_id, Entity.canonical_name, Entity.slug)
            .join(Entity, Entity.id == ClaimEntity.entity_id)
            .where(ClaimEntity.claim_id.in_(ids))
            .distinct()
        )
    ).all()
    ent_ids = [e for e, _, _ in ents]
    elsewhere = set(
        (
            await db.scalars(
                select(ClaimEntity.entity_id)
                .join(Evidence, Evidence.claim_id == ClaimEntity.claim_id)
                .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
                .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
                .join(Claim, Claim.id == ClaimEntity.claim_id)
                .where(
                    ClaimEntity.entity_id.in_(ent_ids),
                    SourceVersion.source_id != source_id,
                    Claim.review_status != "rejected",
                )
                .distinct()
            )
        ).all()
    )
    near = await neighbours(db, mine, exclude_source=source_id)
    vecs = await claim_vectors(db, mine + [o for v in near.values() for o in v])
    confirmed = []
    if vecs:
        for c in mine:
            best = max(
                (cosine(vecs[c.id], vecs[o.id]) for o in near.get(c.id, []) if o.id in vecs),
                default=0.0,
            )
            if best >= CONFIRM_SIMILARITY:
                confirmed.append(c)
    contra = await _contradicted(db, ids)
    other_src = await claim_sources(db, [x for s in contra.values() for x in s])
    in_conflict = [
        c
        for c in mine
        if any(source_id not in other_src.get(o, set()) for o in contra.get(c.id, ()))
    ]
    status = await study_status(db, mine)
    by_status: dict[str, int] = defaultdict(int)
    for s in status.values():
        by_status[s["status"]] += 1
    refs = await db.scalar(
        select(func.count(func.distinct(ReferenceMention.reference_id))).where(
            ReferenceMention.source_id == source_id
        )
    )
    links = (
        await db.execute(
            select(SourceLink.status, func.count())
            .where((SourceLink.a_id == source_id) | (SourceLink.b_id == source_id))
            .group_by(SourceLink.status)
        )
    ).all()
    last = await db.scalar(
        select(Job)
        .where(Job.kind == ANALYZE_KIND, Job.payload["source_id"].astext == str(source_id))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    new = [{"id": str(e), "name": n, "slug": s} for e, n, s in ents if e not in elsewhere]
    shared = [{"id": str(e), "name": n, "slug": s} for e, n, s in ents if e in elsewhere]
    return {
        "source_id": str(source_id),
        "claims": len(mine),
        "concepts": {"new": new, "shared": shared},
        "confirmed": [{"id": str(c.id), "text": c.text} for c in confirmed],
        "in_conflict": [{"id": str(c.id), "text": c.text} for c in in_conflict],
        "against_studies": dict(by_status),
        "similarity_available": vecs is not None,
        "references": refs or 0,
        "links": {s: n for s, n in links},
        "analysis": (
            {
                "status": last.status.value if hasattr(last.status, "value") else last.status,
                "result": last.result,
                "error": last.last_error,
                "at": last.created_at.isoformat(),
            }
            if last
            else None
        ),
    }


async def person_profile(db: AsyncSession, person: Person) -> dict[str, Any]:
    """A teacher's claims by concept, how they stand against the studies, and how they
    agree or disagree with the other people in the library."""
    claims = (
        await db.scalars(
            select(Claim).where(
                Claim.attributed_person_id == person.id, Claim.review_status != "rejected"
            )
        )
    ).all()
    status = await study_status(db, list(claims))
    ents = (
        await db.execute(
            select(ClaimEntity.claim_id, Entity.id, Entity.canonical_name, Entity.slug)
            .join(Entity, Entity.id == ClaimEntity.entity_id)
            .where(ClaimEntity.claim_id.in_([c.id for c in claims]))
        )
    ).all()
    by_entity: dict[uuid.UUID, dict[str, Any]] = {}
    for cid, eid, name, slug in ents:
        e = by_entity.setdefault(eid, {"id": str(eid), "name": name, "slug": slug, "claims": []})
        c = next(x for x in claims if x.id == cid)
        e["claims"].append({"id": str(c.id), "text": c.text, "study": status[c.id]})
    summary: dict[str, int] = defaultdict(int)
    for s in status.values():
        summary[s["status"]] += 1

    # agreement with other people: similar claims (agree) and contradictions (disagree)
    near = await neighbours(db, list(claims))
    others = {
        o.id: o for v in near.values() for o in v if o.attributed_person_id not in (None, person.id)
    }
    vecs = await claim_vectors(db, list(claims) + list(others.values()))
    contra = await _contradicted(db, [c.id for c in claims])
    peers: dict[uuid.UUID, dict[str, Any]] = {}
    for c in claims:
        for o in near.get(c.id, []):
            if o.id not in others:
                continue
            p = peers.setdefault(
                o.attributed_person_id, {"agree": 0, "disagree": 0, "examples": []}
            )
            if o.id in contra.get(c.id, ()):
                p["disagree"] += 1
                p["examples"].append({"kind": "disagree", "mine": c.text, "theirs": o.text})
            elif vecs and cosine(vecs[c.id], vecs[o.id]) >= CONFIRM_SIMILARITY:
                p["agree"] += 1
                p["examples"].append({"kind": "agree", "mine": c.text, "theirs": o.text})
    names = {
        p.id: p.name
        for p in (await db.scalars(select(Person).where(Person.id.in_(list(peers))))).all()
    }
    sources = (
        await db.execute(
            select(Source.id, Source.title, func.count(func.distinct(Claim.id)))
            .join(SourceVersion, SourceVersion.source_id == Source.id)
            .join(SourceSegment, SourceSegment.source_version_id == SourceVersion.id)
            .join(Evidence, Evidence.segment_id == SourceSegment.id)
            .join(Claim, Claim.id == Evidence.claim_id)
            .where(Claim.attributed_person_id == person.id, Claim.review_status != "rejected")
            .group_by(Source.id, Source.title)
        )
    ).all()
    return {
        "person": {
            "id": str(person.id),
            "name": person.name,
            "role": person.role,
            "affiliation": person.affiliation,
        },
        "claims": len(claims),
        "summary": dict(summary),
        "summary_text": {k: STATUS_TEXT[k] for k in summary},
        "concepts": sorted(by_entity.values(), key=lambda e: -len(e["claims"])),
        "peers": sorted(
            (
                {"id": str(pid), "name": names.get(pid, "?"), **v, "examples": v["examples"][:4]}
                for pid, v in peers.items()
                if v["agree"] or v["disagree"]
            ),
            key=lambda p: -(p["agree"] + p["disagree"]),
        ),
        "sources": [{"id": str(i), "title": t, "claims": n} for i, t, n in sources],
    }
