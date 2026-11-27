"""Studies (publications): bibliographic record, methodological record, and the
link between a source and the study it contains.

Rules:
- methodological quality starts as ``not_assessed`` and only changes by an explicit
  user assessment (recorded with date and audit entry); nothing infers it;
- linking a source to a study makes the claims cited only from that source the
  study's claims; unlinking undoes exactly that. No claim is ever deleted.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kos.evidence import doi as doi_mod
from kos.models import (
    AuditLog,
    Claim,
    Evidence,
    Person,
    Publication,
    Source,
    SourceSegment,
    SourceVersion,
)

QUALITY = ("not_assessed", "low", "moderate", "high")
BIBLIO_FIELDS = (
    "title",
    "year",
    "doi",
    "journal",
    "authors_text",
    "abstract",
    "url",
    "record_type",
)
METHOD_FIELDS = (
    "study_type",
    "design",
    "population",
    "sample_size",
    "variables",
    "results",
    "limitations",
)


class EvidenceError(Exception):
    pass


class DuplicateDOI(EvidenceError):
    def __init__(self, existing: Publication):
        super().__init__("ya hay un estudio con ese DOI")
        self.existing = existing


def _json(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def _audit(db, action: str, pub_id, before=None, after=None) -> None:
    db.add(
        AuditLog(
            actor="user",
            action=action,
            subject_table="publications",
            subject_id=str(pub_id),
            before={k: _json(v) for k, v in (before or {}).items()} or None,
            after={k: _json(v) for k, v in (after or {}).items()} or None,
        )
    )


async def by_doi(db: AsyncSession, doi: str) -> Publication | None:
    return await db.scalar(select(Publication).where(Publication.doi == doi_mod.normalize_doi(doi)))


async def create(
    db: AsyncSession, data: dict[str, Any], *, metadata_source: str = "manual"
) -> Publication:
    data = {k: v for k, v in data.items() if k in BIBLIO_FIELDS + METHOD_FIELDS}
    if data.get("doi"):
        data["doi"] = doi_mod.normalize_doi(data["doi"])
        if existing := await by_doi(db, data["doi"]):
            raise DuplicateDOI(existing)
    if not (data.get("title") or "").strip():
        raise EvidenceError("el título es obligatorio")
    pub = Publication(
        **data,
        metadata_source=metadata_source,
        methodological_quality="not_assessed",
        metadata_fetched_at=datetime.now(UTC) if metadata_source != "manual" else None,
    )
    db.add(pub)
    await db.flush()
    _audit(
        db,
        "publication.created",
        pub.id,
        after={"title": pub.title, "doi": pub.doi, "metadata_source": metadata_source},
    )
    await db.commit()
    return pub


async def create_from_doi(db: AsyncSession, doi: str) -> Publication:
    doi = doi_mod.normalize_doi(doi)
    if existing := await by_doi(db, doi):
        raise DuplicateDOI(existing)
    meta = await doi_mod.fetch(doi)
    return await create(db, meta.as_dict(), metadata_source="crossref")


async def update_fields(db: AsyncSession, pub: Publication, changes: dict[str, Any]) -> Publication:
    """Edit bibliographic/methodological fields and, separately, the user's quality
    assessment (which stamps ``quality_assessed_at``)."""
    if "doi" in changes and changes["doi"]:
        changes["doi"] = doi_mod.normalize_doi(changes["doi"])
        other = await by_doi(db, changes["doi"])
        if other is not None and other.id != pub.id:
            raise DuplicateDOI(other)
    if "methodological_quality" in changes and changes["methodological_quality"] not in QUALITY:
        raise EvidenceError("calidad no válida")
    allowed = set(BIBLIO_FIELDS + METHOD_FIELDS + ("methodological_quality", "quality_notes"))
    changes = {k: v for k, v in changes.items() if k in allowed and getattr(pub, k) != v}
    if not changes:
        return pub
    before = {k: getattr(pub, k) for k in changes}
    for k, v in changes.items():
        setattr(pub, k, v)
    now = datetime.now(UTC)
    pub.updated_at = now
    if "methodological_quality" in changes or "quality_notes" in changes:
        pub.quality_assessed_at = now if pub.methodological_quality != "not_assessed" else None
    _audit(db, "publication.updated", pub.id, before, changes)
    await db.commit()
    return pub


def _single_source_claims(source_id: uuid.UUID):
    """Claims whose evidence all comes from this source."""
    from_source = (
        select(Evidence.claim_id)
        .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
        .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
        .where(SourceVersion.source_id == source_id)
    )
    elsewhere = (
        select(Evidence.claim_id)
        .join(SourceSegment, SourceSegment.id == Evidence.segment_id)
        .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
        .where(SourceVersion.source_id != source_id)
    )
    return Claim.id.in_(from_source) & Claim.id.not_in(elsewhere)


async def link_source(db: AsyncSession, source: Source, publication_id: uuid.UUID | None) -> int:
    """Set (or clear) the study a source contains. Returns how many claims changed."""
    old = source.publication_id
    if old == publication_id:
        return 0
    if publication_id is not None and await db.get(Publication, publication_id) is None:
        raise EvidenceError("estudio no encontrado")
    changed = 0
    if old is not None:
        changed += (
            await db.execute(
                update(Claim)
                .where(_single_source_claims(source.id), Claim.publication_id == old)
                .values(publication_id=None)
            )
        ).rowcount
    if publication_id is not None:
        changed += (
            await db.execute(
                update(Claim)
                .where(_single_source_claims(source.id), Claim.publication_id.is_(None))
                .values(publication_id=publication_id)
            )
        ).rowcount
    source.publication_id = publication_id
    if publication_id is not None:
        from kos.library import sections

        await sections.add(db, source.id, "estudios")
    db.add(
        AuditLog(
            actor="user",
            action="source.publication",
            subject_table="sources",
            subject_id=str(source.id),
            before={"publication_id": str(old) if old else None},
            after={
                "publication_id": str(publication_id) if publication_id else None,
                "claims_changed": changed,
            },
        )
    )
    await db.commit()
    return changed


async def summary(db: AsyncSession, pub: Publication) -> dict[str, Any]:
    claims = await db.scalar(
        select(func.count()).select_from(Claim).where(Claim.publication_id == pub.id)
    )
    sources = (
        await db.execute(select(Source.id, Source.title).where(Source.publication_id == pub.id))
    ).all()
    return {
        "id": str(pub.id),
        **{k: getattr(pub, k) for k in BIBLIO_FIELDS + METHOD_FIELDS},
        "methodological_quality": pub.methodological_quality,
        "quality_notes": pub.quality_notes,
        "quality_assessed_at": pub.quality_assessed_at,
        "metadata_source": pub.metadata_source,
        "metadata_fetched_at": pub.metadata_fetched_at,
        "created_at": pub.created_at,
        "claims": claims,
        "sources": [{"id": str(i), "title": t} for i, t in sources],
    }


# ---- provenance ----------------------------------------------------------------------

PROVENANCE_LABELS = {
    "study": "estudio",
    "teacher": "profesor",
    "author": "autor",
    "ai": "inferencia de IA",
    "user": "usuario",
    "source": "fuente sin atribuir",
}


async def provenance(db: AsyncSession, claim: Claim) -> dict[str, Any]:
    """Where a claim comes from, by precedence: a study, a named person (teacher or
    other author), an AI inference, the user's own conclusion, or just a source."""
    if claim.publication_id:
        pub = await db.get(Publication, claim.publication_id)
        return {
            "kind": "study",
            "label": PROVENANCE_LABELS["study"],
            "publication": {
                "id": str(pub.id),
                "title": pub.title,
                "year": pub.year,
                "doi": pub.doi,
                "methodological_quality": pub.methodological_quality,
            },
        }
    if claim.attributed_person_id:
        p = await db.get(Person, claim.attributed_person_id)
        kind = "teacher" if (p.role or "teacher") == "teacher" else "author"
        return {"kind": kind, "label": PROVENANCE_LABELS[kind], "person": p.name}
    if claim.claim_type == "ai_inference":
        return {"kind": "ai", "label": PROVENANCE_LABELS["ai"]}
    if claim.claim_type == "personal_conclusion":
        return {"kind": "user", "label": PROVENANCE_LABELS["user"]}
    return {"kind": "source", "label": PROVENANCE_LABELS["source"]}


# ---- quote audit -------------------------------------------------------------------------


async def audit_quotes(db: AsyncSession) -> dict[str, Any]:
    """Check every stored quote against its fragment text (criterion: 100 % exact)."""
    rows = (
        await db.execute(
            select(
                Evidence.id,
                Evidence.claim_id,
                Evidence.quote,
                Evidence.quote_char_start,
                SourceSegment.text,
            ).join(SourceSegment, SourceSegment.id == Evidence.segment_id)
        )
    ).all()
    bad = []
    for ev_id, claim_id, quote, start, text in rows:
        ok = bool(quote) and quote in text
        if ok and start is not None:
            ok = text[start : start + len(quote)] == quote
        if not ok:
            bad.append({"evidence_id": str(ev_id), "claim_id": str(claim_id)})
    return {"checked": len(rows), "mismatches": bad, "ok": not bad}
