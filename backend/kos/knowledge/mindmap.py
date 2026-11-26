"""Generated mind map of a concept, built only from stored knowledge.

The centre is the concept; branches group what is related to it by kind
(mechanisms, variables, principles, theories and models, applications, studies,
authors, open questions, related concepts) plus its key claims. Every node links
to its page and, when known, to the source that backs it. Exports: Markdown
(markmap / Obsidian mind-map plugins) and OPML (XMind, outliners).
"""

import uuid
import xml.etree.ElementTree as ET
from collections import OrderedDict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from kos.library.locate import location
from kos.models import (
    Claim,
    ClaimEntity,
    Entity,
    Evidence,
    Person,
    Publication,
    Relationship,
    RelationshipEvidence,
    RelationshipType,
    Source,
    SourceSegment,
    SourceVersion,
)

BRANCHES: "OrderedDict[str, tuple[str, tuple[str, ...]]]" = OrderedDict(
    [
        ("mechanisms", ("Mecanismos", ("mechanism",))),
        ("variables", ("Variables", ("variable",))),
        ("principles", ("Principios", ("principle",))),
        ("theories", ("Teorías y modelos", ("theory", "model"))),
        ("applications", ("Aplicaciones y metodologías", ("application", "methodology", "case"))),
        ("studies", ("Estudios", ("study",))),
        ("authors", ("Autores e instituciones", ("author", "institution"))),
        ("questions", ("Preguntas abiertas", ("open_question",))),
        ("related", ("Conceptos relacionados", ())),  # everything else
    ]
)
TYPE_TO_BRANCH = {t: key for key, (_, types) in BRANCHES.items() for t in types}
KEY_CLAIM_TYPES = ("definition", "recommendation", "reported_fact", "hypothesis")
MAX_KEY_CLAIMS = 6


def _ref(seg: SourceSegment, ver: SourceVersion, src: Source) -> dict[str, Any]:
    return {
        "source_id": str(src.id),
        "version_id": str(ver.id),
        "source_title": src.title,
        **location(seg),
        "mime": ver.mime,
    }


async def _segment_ref(db: AsyncSession, segment_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await db.execute(
            select(SourceSegment, SourceVersion, Source)
            .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
            .join(Source, Source.id == SourceVersion.source_id)
            .where(SourceSegment.id == segment_id)
        )
    ).first()
    return _ref(*row) if row else None


async def _claim_ref(db: AsyncSession, claim_id: uuid.UUID) -> dict[str, Any] | None:
    seg_id = await db.scalar(
        select(Evidence.segment_id).where(Evidence.claim_id == claim_id).limit(1)
    )
    return await _segment_ref(db, seg_id) if seg_id else None


async def build(db: AsyncSession, entity_id: uuid.UUID) -> dict[str, Any] | None:
    center = await db.get(Entity, entity_id)
    if center is None:
        return None
    branches: dict[str, list[dict[str, Any]]] = {k: [] for k in BRANCHES}
    other = aliased(Entity)
    rtypes = {t.key: t for t in (await db.scalars(select(RelationshipType))).all()}

    for mine, theirs, outgoing in (
        (Relationship.source_entity_id, Relationship.target_entity_id, True),
        (Relationship.target_entity_id, Relationship.source_entity_id, False),
    ):
        rows = (
            await db.execute(
                select(Relationship, other)
                .join(other, other.id == theirs)
                .where(
                    mine == entity_id,
                    Relationship.status != "rejected",
                    other.status.in_(("active", "proposed")),
                )
                .order_by(other.canonical_name)
            )
        ).all()
        for rel, oth in rows:
            rt = rtypes[rel.type_key]
            label = rt.label if outgoing or rt.is_symmetric else rt.inverse_label
            ev = await db.scalar(
                select(RelationshipEvidence)
                .where(RelationshipEvidence.relationship_id == rel.id)
                .limit(1)
            )
            ref = None
            if ev is not None:
                ref = await (
                    _claim_ref(db, ev.claim_id) if ev.claim_id else _segment_ref(db, ev.segment_id)
                )
            branches[TYPE_TO_BRANCH.get(oth.type_key, "related")].append(
                {
                    "kind": "entity",
                    "id": str(oth.id),
                    "slug": oth.slug,
                    "name": oth.canonical_name,
                    "relation": f"{center.canonical_name} {label} {oth.canonical_name}",
                    "status": rel.status,
                    "origin": rel.origin,
                    "ref": ref,
                }
            )

    claims = (
        (
            await db.execute(
                select(Claim)
                .join(ClaimEntity, ClaimEntity.claim_id == Claim.id)
                .where(ClaimEntity.entity_id == entity_id, Claim.review_status != "rejected")
                .order_by(Claim.created_at)
            )
        )
        .scalars()
        .all()
    )
    persons: dict[uuid.UUID, int] = {}
    pubs: dict[uuid.UUID, int] = {}
    key_claims = []
    for c in claims:
        if c.attributed_person_id:
            persons[c.attributed_person_id] = persons.get(c.attributed_person_id, 0) + 1
        if c.publication_id:
            pubs[c.publication_id] = pubs.get(c.publication_id, 0) + 1
        if c.claim_type in KEY_CLAIM_TYPES and len(key_claims) < MAX_KEY_CLAIMS:
            key_claims.append(
                {
                    "kind": "claim",
                    "id": str(c.id),
                    "name": c.text,
                    "claim_type": c.claim_type,
                    "ref": await _claim_ref(db, c.id),
                }
            )
    for pid, n in persons.items():
        p = await db.get(Person, pid)
        branches["authors"].append(
            {
                "kind": "person",
                "id": str(pid),
                "name": p.name,
                "relation": f"{n} afirmación(es) sobre {center.canonical_name}",
            }
        )
    for pid, n in pubs.items():
        p = await db.get(Publication, pid)
        branches["studies"].append(
            {
                "kind": "publication",
                "id": str(pid),
                "name": f"{p.title}{f' ({p.year})' if p.year else ''}",
                "relation": f"{n} afirmación(es)",
            }
        )

    out = [{"key": k, "label": BRANCHES[k][0], "items": v} for k, v in branches.items() if v]
    if key_claims:
        out.insert(0, {"key": "claims", "label": "Afirmaciones clave", "items": key_claims})
    return {
        "center": {
            "id": str(center.id),
            "slug": center.slug,
            "name": center.canonical_name,
            "definition": center.short_definition,
            "type_key": center.type_key,
        },
        "branches": out,
    }


# ---- exports --------------------------------------------------------------------------


def _url(base: str, item: dict[str, Any]) -> str | None:
    if item.get("kind") == "entity" and item.get("slug"):
        return f"{base}/concepts/{item['slug']}"
    return None


def _source_note(ref: dict[str, Any] | None) -> str:
    if not ref:
        return ""
    where = f", p. {ref['page']}" if ref.get("page") else ""
    return f"{ref['source_title']}{where}"


def _md_escape(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def to_markdown(m: dict[str, Any], base_url: str) -> str:
    c = m["center"]
    lines = [f"# [{_md_escape(c['name'])}]({base_url}/concepts/{c['slug']})", ""]
    if c.get("definition"):
        lines += [f"> {_md_escape(c['definition'])}", ""]
    for b in m["branches"]:
        lines.append(f"## {b['label']}")
        for it in b["items"]:
            name = _md_escape(it["name"])
            url = _url(base_url, it)
            text = f"[{name}]({url})" if url else name
            extra = [
                x
                for x in (
                    _source_note(it.get("ref")),
                    "propuesta" if it.get("status") == "proposed" else "",
                )
                if x
            ]
            lines.append(f"- {text}" + (f" — {'; '.join(extra)}" if extra else ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_opml(m: dict[str, Any], base_url: str) -> bytes:
    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = m["center"]["name"]
    body = ET.SubElement(root, "body")
    c = m["center"]
    center = ET.SubElement(body, "outline", text=c["name"], url=f"{base_url}/concepts/{c['slug']}")
    if c.get("definition"):
        center.set("_note", c["definition"])
    for b in m["branches"]:
        branch = ET.SubElement(center, "outline", text=b["label"])
        for it in b["items"]:
            node = ET.SubElement(branch, "outline", text=it["name"])
            if url := _url(base_url, it):
                node.set("url", url)
            note = "; ".join(x for x in (it.get("relation", ""), _source_note(it.get("ref"))) if x)
            if note:
                node.set("_note", note)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
