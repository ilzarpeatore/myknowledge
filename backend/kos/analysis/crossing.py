"""Crossing content without AI: passages that say the same in other sources, the
concept × source coverage map, and the consensus of the claims about a concept.

Evidence levels are derived only from the methodological quality YOU assessed for
each study (never deduced); the label says so, and it is not a verdict on the
science."""

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kos.analysis.vectors import claim_vectors, cosine, group
from kos.knowledge.compare import claim_rows
from kos.library.locate import label as loc_label
from kos.library.locate import location
from kos.models import (
    Claim,
    ClaimCheck,
    ClaimEntity,
    Contradiction,
    Entity,
    Evidence,
    LibrarySection,
    Source,
    SourceSection,
    SourceSegment,
    SourceVersion,
)

# Measured with multilingual-e5-small on training claims: the same statement in other
# words scores 0.97-0.98; any two claims of the same field 0.85-0.92 (D-23).
SAME_CLAIM = 0.95
SIMILAR_PASSAGE = 0.88

# ---- «Ver también»: passages that say the same elsewhere --------------------------

SIMILAR_SQL = text(
    """
    WITH t AS (SELECT embedding, model FROM segment_embeddings WHERE segment_id = :sid)
    SELECT seg.id, 1 - (e.embedding <=> t.embedding) AS sim
    FROM segment_embeddings e
    JOIN t ON e.model = t.model
    JOIN source_segments seg ON seg.id = e.segment_id
    JOIN source_versions v ON v.id = seg.source_version_id
    JOIN sources s ON s.id = v.source_id
    WHERE s.id <> :src AND s.status <> 'archived'
      AND NOT EXISTS (SELECT 1 FROM reference_mentions rm
                      WHERE rm.segment_id = seg.id AND rm.kind = 'bibliography')
    ORDER BY e.embedding <=> t.embedding
    LIMIT :k
    """
)


async def similar_segments(
    db: AsyncSession,
    segment_id: uuid.UUID,
    *,
    limit: int = 8,
    min_similarity: float = SIMILAR_PASSAGE,
) -> dict[str, Any]:
    seg = await db.get(SourceSegment, segment_id)
    if seg is None:
        raise LookupError("fragmento no encontrado")
    ver = await db.get(SourceVersion, seg.source_version_id)
    rows = (
        await db.execute(SIMILAR_SQL, {"sid": segment_id, "src": ver.source_id, "k": limit * 4})
    ).all()
    has_vector = bool(rows) or bool(
        await db.scalar(
            text("SELECT 1 FROM segment_embeddings WHERE segment_id = :s"), {"s": segment_id}
        )
    )
    items, per_source = [], defaultdict(int)
    for sid, sim in rows:
        if sim < min_similarity:
            break
        other = await db.get(SourceSegment, sid)
        over = await db.get(SourceVersion, other.source_version_id)
        if per_source[over.source_id] >= 2:  # at most two passages per source
            continue
        per_source[over.source_id] += 1
        src = await db.get(Source, over.source_id)
        items.append(
            {
                "segment_id": str(other.id),
                "similarity": round(float(sim), 3),
                "text": other.text[:600],
                "source_id": str(src.id),
                "source_title": src.title,
                "version_id": str(over.id),
                "mime": over.mime,
                "where": loc_label(other),
                **location(other),
            }
        )
        if len(items) >= limit:
            break
    sections = await sections_of(db, {uuid.UUID(i["source_id"]) for i in items})
    for i in items:
        i["sections"] = sections.get(uuid.UUID(i["source_id"]), [])
    return {"segment_id": str(segment_id), "available": has_vector, "items": items}


async def sections_of(db: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(SourceSection.source_id, LibrarySection.key, LibrarySection.name)
            .join(LibrarySection, LibrarySection.key == SourceSection.section_key)
            .where(SourceSection.source_id.in_(ids))
            .order_by(LibrarySection.position)
        )
    ).all()
    out: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for sid, key, name in rows:
        out[sid].append({"key": key, "name": name})
    return out


# ---- coverage map: concepts × sources ----------------------------------------------


async def coverage(
    db: AsyncSession,
    *,
    section: str | None = None,
    entity_ids: list[uuid.UUID] | None = None,
    max_entities: int = 40,
    max_sources: int = 30,
) -> dict[str, Any]:
    """How many claims each source has about each concept (non-rejected claims with
    evidence). Concepts covered by more sources come first."""
    stmt = (
        select(
            ClaimEntity.entity_id,
            SourceVersion.source_id,
            func.count(func.distinct(Claim.id)),
        )
        .join(Claim, Claim.id == ClaimEntity.claim_id)
        .join(Evidence, Evidence.claim_id == Claim.id)
        .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
        .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
        .join(Entity, Entity.id == ClaimEntity.entity_id)
        .where(Claim.review_status != "rejected", Entity.status != "merged")
        .group_by(ClaimEntity.entity_id, SourceVersion.source_id)
    )
    if section:
        stmt = stmt.join(SourceSection, SourceSection.source_id == SourceVersion.source_id).where(
            SourceSection.section_key == section
        )
    if entity_ids:
        stmt = stmt.where(ClaimEntity.entity_id.in_(entity_ids))
    rows = (await db.execute(stmt)).all()
    ent_sources: dict[uuid.UUID, set] = defaultdict(set)
    ent_claims: dict[uuid.UUID, int] = defaultdict(int)
    src_claims: dict[uuid.UUID, int] = defaultdict(int)
    for e, s, n in rows:
        ent_sources[e].add(s)
        ent_claims[e] += n
        src_claims[s] += n
    ents = sorted(ent_sources, key=lambda e: (-len(ent_sources[e]), -ent_claims[e]))[:max_entities]
    keep = set(ents)
    srcs_count: dict[uuid.UUID, int] = defaultdict(int)
    for e, s, n in rows:
        if e in keep:
            srcs_count[s] += n
    srcs = sorted(srcs_count, key=lambda s: -srcs_count[s])[:max_sources]
    names = {e.id: e for e in (await db.scalars(select(Entity).where(Entity.id.in_(ents)))).all()}
    sources = {s.id: s for s in (await db.scalars(select(Source).where(Source.id.in_(srcs)))).all()}
    secs = await sections_of(db, set(srcs))
    src_set = set(srcs)
    return {
        "entities": [
            {
                "id": str(e),
                "name": names[e].canonical_name,
                "slug": names[e].slug,
                "sources": len(ent_sources[e]),
                "claims": ent_claims[e],
                "single_source": len(ent_sources[e]) == 1,
            }
            for e in ents
        ],
        "sources": [
            {
                "id": str(s),
                "title": sources[s].title,
                "kind": sources[s].kind.value,
                "sections": secs.get(s, []),
                "claims": srcs_count[s],
            }
            for s in srcs
        ],
        "cells": [
            {"entity_id": str(e), "source_id": str(s), "claims": n}
            for e, s, n in rows
            if e in keep and s in src_set
        ],
        "totals": {"entities": len(ent_sources), "sources": len(src_claims)},
    }


# ---- consensus: what the claims about a concept agree on ---------------------------

QUALITY_RANK = {"high": 3, "moderate": 2, "low": 1, "not_assessed": 0}
LEVEL_TEXT = {
    "high": "Alta: varios estudios que evaluaste de calidad alta",
    "moderate": "Moderada: algún estudio de calidad alta o moderada (según tu evaluación)",
    "low": "Baja: solo estudios que evaluaste de calidad baja",
    "unassessed": "Estudios sin evaluar: evalúa su calidad en Estudios",
    "none": "Sin estudios: solo explicaciones de profesores u otras fuentes",
}


def evidence_level(qualities: list[str]) -> str:
    if not qualities:
        return "none"
    best = max(QUALITY_RANK.get(q, 0) for q in qualities)
    if best == 3 and sum(1 for q in qualities if q == "high") >= 2:
        return "high"
    if best >= 2:
        return "moderate"
    if best == 1:
        return "low"
    return "unassessed"


async def consensus(
    db: AsyncSession, entity_id: uuid.UUID, *, threshold: float = SAME_CLAIM
) -> dict[str, Any]:
    rows = await claim_rows(db, entity_id)
    ids = [uuid.UUID(r["id"]) for r in rows]
    claims = (await db.scalars(select(Claim).where(Claim.id.in_(ids)))).all()
    by_id = {c.id: c for c in claims}
    vecs = await claim_vectors(db, [by_id[i] for i in ids])
    # disagreements: registered contradictions and study checks that contradict
    contra: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for a, b in (
        await db.execute(
            select(Contradiction.claim_a_id, Contradiction.claim_b_id).where(
                Contradiction.status.in_(("candidate", "real"))
            )
        )
    ).all():
        contra[a].add(b)
        contra[b].add(a)
    for a, b in (
        await db.execute(
            select(ClaimCheck.claim_id, ClaimCheck.study_claim_id).where(
                ClaimCheck.verdict == "contradicts", ClaimCheck.status != "rejected"
            )
        )
    ).all():
        contra[a].add(b)
        contra[b].add(a)

    groups = group(ids, vecs, threshold, apart=contra)
    verdicts: dict[uuid.UUID, set[str]] = defaultdict(set)
    for cid, v in (
        await db.execute(
            select(ClaimCheck.claim_id, ClaimCheck.verdict).where(
                ClaimCheck.claim_id.in_(ids), ClaimCheck.status != "rejected"
            )
        )
    ).all():
        verdicts[cid].add(v)
    row = {uuid.UUID(r["id"]): r for r in rows}

    out = []
    for g in groups:
        members = [row[i] for i in g]
        studies = {
            m["perspective"]["id"]: m["perspective"]
            for m in members
            if m["perspective"]["kind"] == "study"
        }
        teachers = {
            m["perspective"]["id"]: m["perspective"]["label"]
            for m in members
            if m["perspective"]["kind"] == "person"
        }
        sources = {m["ref"]["source_id"] for m in members if m["ref"]}
        # the most central claim names the group
        if vecs and len(g) > 1:
            head = max(g, key=lambda i: sum(cosine(vecs[i], vecs[j]) for j in g))
        else:
            head = g[0]
        against = sorted({str(x) for i in g for x in contra[i] if x not in g})
        level = evidence_level([s["quality"] for s in studies.values()])
        out.append(
            {
                "statement": row[head]["text"],
                "claims": members,
                "count": len(g),
                "sources": len(sources),
                "studies": list(studies.values()),
                "teachers": sorted(teachers.values()),
                "contradicted_by": against,
                "disputed": bool(against),
                # checks of these (non-study) claims against study claims, if any
                "checked_against_studies": sorted(
                    {v for i in g for v in verdicts[i]} - {"unrelated"}
                ),
                "evidence_level": level,
                "evidence_text": LEVEL_TEXT[level],
            }
        )
    out.sort(key=lambda x: (-x["sources"], -x["count"]))
    return {
        "entity_id": str(entity_id),
        "grouped_by_meaning": vecs is not None,
        "threshold": threshold,
        "groups": out,
        "note": "El nivel se basa solo en la calidad que tú asignaste a cada estudio; "
        "no es una valoración de la ciencia hecha por el sistema.",
    }
