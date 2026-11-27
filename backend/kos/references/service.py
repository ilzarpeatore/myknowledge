"""Bibliographic references: scan a source version, group by key, count mentions,
link to Estudios (publications). The scan is deterministic and free (no AI); it is
re-run when a source is reprocessed and replaces that source's mentions."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kos.evidence import service as ev
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.knowledge.service import normalize
from kos.models import (
    AuditLog,
    Job,
    JobStatus,
    LibrarySection,
    Publication,
    Reference,
    ReferenceMention,
    Source,
    SourceSection,
    SourceSegment,
    SourceVersion,
)
from kos.references.parse import Found, find, fold

REFS_KIND = "source.references"


class ReferenceError_(Exception):
    pass


def refs_job(version_id: uuid.UUID) -> Job:
    return Job(
        kind=REFS_KIND,
        payload={"source_version_id": str(version_id)},
        status=JobStatus.pending,
        max_attempts=3,
    )


def _same_title(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return True
    return fold(a)[:60] == fold(b)[:60]


async def _reference_for(db: AsyncSession, f: Found) -> Reference:
    key = f.key
    assert key
    ref = await db.scalar(select(Reference).where(Reference.key == key))
    if ref is None and f.doi and not key.startswith("doi:"):
        # a DOI seen alone earlier now has its author and year: adopt that reference
        ref = await db.scalar(select(Reference).where(Reference.key == f"doi:{f.doi}"))
        if ref is not None:
            ref.key = key
    if ref is None and key.startswith("doi:"):
        ref = await db.scalar(select(Reference).where(Reference.doi == f.doi))
    if ref is None:
        ref = Reference(key=key, status="active", ambiguous=False)
        db.add(ref)
    seen_ids = set()
    while ref.merged_into_id and ref.merged_into_id not in seen_ids:
        seen_ids.add(ref.merged_into_id)
        ref = await db.get(Reference, ref.merged_into_id)
    if f.author and not ref.first_author:
        ref.first_author = f.author.strip()[:300]
    if f.year and not ref.year:
        ref.year = f.year
    if f.title:
        if ref.title and not _same_title(ref.title, f.title):
            ref.ambiguous = True
        ref.title = ref.title or f.title
    if f.authors_text and not ref.authors_text:
        ref.authors_text = f.authors_text
    if f.doi and not ref.doi:
        ref.doi = f.doi
    if ref.doi and ref.publication_id is None:
        pub = await db.scalar(select(Publication.id).where(Publication.doi == ref.doi))
        ref.publication_id = pub
    await db.flush()
    return ref


async def scan_version(db: AsyncSession, version_id: uuid.UUID) -> dict[str, int]:
    version = await db.get(SourceVersion, version_id)
    if version is None:
        raise ReferenceError_(f"no existe la versión {version_id}")
    segs = (
        await db.scalars(
            select(SourceSegment)
            .where(SourceSegment.source_version_id == version_id)
            .order_by(SourceSegment.ordinal)
        )
    ).all()
    # this version replaces whatever was counted for the source before
    await db.execute(
        delete(ReferenceMention).where(ReferenceMention.source_id == version.source_id)
    )
    found = refs = 0
    seen: set[uuid.UUID] = set()
    for seg in segs:
        for f in find(seg.text):
            if not f.key:
                continue
            ref = await _reference_for(db, f)
            await db.execute(
                insert(ReferenceMention)
                .values(
                    reference_id=ref.id,
                    segment_id=seg.id,
                    source_id=version.source_id,
                    kind=f.kind,
                    raw=f.raw[:2000],
                    char_start=f.start,
                    char_end=f.end,
                )
                .on_conflict_do_nothing()
            )
            found += 1
            if ref.id not in seen:
                seen.add(ref.id)
                refs += 1
    await db.commit()
    return {"mentions": found, "references": refs}


@handler(REFS_KIND)
async def scan_job(ctx: JobContext) -> dict[str, Any]:
    try:
        version_id = uuid.UUID(ctx.payload["source_version_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload sin source_version_id válido") from None
    async with ctx.sessionmaker() as db:
        try:
            return await scan_version(db, version_id)
        except ReferenceError_ as exc:
            raise PermanentJobError(str(exc)) from None


async def rescan_all(db: AsyncSession) -> int:
    """Queue a scan of the latest version of every source (after an upgrade)."""
    latest = (
        select(SourceVersion.id)
        .distinct(SourceVersion.source_id)
        .order_by(SourceVersion.source_id, SourceVersion.created_at.desc())
    )
    ids = (await db.scalars(latest)).all()
    db.add_all(refs_job(i) for i in ids)
    await db.commit()
    return len(ids)


# ---- reading ---------------------------------------------------------------------


def _counts() -> Select:
    return select(
        ReferenceMention.reference_id.label("rid"),
        func.count().label("mentions"),
        func.count(func.distinct(ReferenceMention.source_id)).label("sources"),
        func.count().filter(ReferenceMention.kind == "bibliography").label("in_bibliography"),
    ).group_by(ReferenceMention.reference_id)


def ref_out(r: Reference, counts: dict[str, int] | None = None, pub: Publication | None = None):
    label = r.title or (
        f"{r.first_author}{' et al.' if r.authors_text and ',' in r.authors_text else ''}, {r.year}"
        if r.first_author
        else r.doi
    )
    return {
        "id": str(r.id),
        "key": r.key,
        "label": label,
        "first_author": r.first_author,
        "year": r.year,
        "authors_text": r.authors_text,
        "title": r.title,
        "doi": r.doi,
        "ambiguous": r.ambiguous,
        "status": r.status,
        "notes": r.notes,
        "publication": (
            {
                "id": str(pub.id),
                "title": pub.title,
                "methodological_quality": pub.methodological_quality,
            }
            if pub
            else None
        ),
        **(counts or {"mentions": 0, "sources": 0, "in_bibliography": 0}),
    }


async def listing(
    db: AsyncSession,
    *,
    q: str | None = None,
    status: str = "active",
    linked: bool | None = None,
    section: str | None = None,
    source_id: uuid.UUID | None = None,
    sort: str = "mentions",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    counts = _counts()
    if section:
        counts = counts.join(
            SourceSection, SourceSection.source_id == ReferenceMention.source_id
        ).where(SourceSection.section_key == section)
    if source_id:
        counts = counts.where(ReferenceMention.source_id == source_id)
    c = counts.subquery()
    stmt = (
        select(
            Reference,
            func.coalesce(c.c.mentions, 0),
            func.coalesce(c.c.sources, 0),
            func.coalesce(c.c.in_bibliography, 0),
            Publication,
        )
        # active: only what is cited somewhere; dismissed: also merged ones (now empty)
        .join(
            c, c.c.rid == Reference.id, isouter=status == "dismissed" and not (section or source_id)
        )
        .outerjoin(Publication, Publication.id == Reference.publication_id)
        .where(Reference.status == status)
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Reference.first_author.ilike(like),
                Reference.title.ilike(like),
                Reference.authors_text.ilike(like),
                Reference.doi.ilike(like),
                Reference.key.ilike(f"%{normalize(q)}%"),
            )
        )
    if linked is True:
        stmt = stmt.where(Reference.publication_id.is_not(None))
    elif linked is False:
        stmt = stmt.where(Reference.publication_id.is_(None))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    order = {
        "mentions": [c.c.mentions.desc().nulls_last(), c.c.sources.desc()],
        "sources": [c.c.sources.desc().nulls_last(), c.c.mentions.desc()],
        "year": [Reference.year.desc().nulls_last()],
        "author": [Reference.first_author.asc().nulls_last(), Reference.year.asc()],
    }.get(sort, [c.c.mentions.desc()])
    rows = (
        await db.execute(stmt.order_by(*order, Reference.key).limit(limit).offset(offset))
    ).all()
    return {
        "total": total or 0,
        "items": [
            ref_out(r, {"mentions": m, "sources": s, "in_bibliography": b}, pub)
            for r, m, s, b, pub in rows
        ],
    }


async def detail(db: AsyncSession, ref: Reference) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(ReferenceMention, SourceSegment, Source, SourceVersion.mime)
            .join(SourceSegment, SourceSegment.id == ReferenceMention.segment_id)
            .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
            .join(Source, Source.id == ReferenceMention.source_id)
            .where(ReferenceMention.reference_id == ref.id)
            .order_by(Source.title, SourceSegment.ordinal, ReferenceMention.char_start)
        )
    ).all()
    sections = await _sections_of(db, {s.id for _, _, s, _ in rows})
    pub = await db.get(Publication, ref.publication_id) if ref.publication_id else None
    by_source: dict[uuid.UUID, dict[str, Any]] = {}
    for m, seg, src, mime in rows:
        entry = by_source.setdefault(
            src.id,
            {
                "source_id": str(src.id),
                "source_title": src.title,
                "kind": src.kind.value,
                "sections": sections.get(src.id, []),
                "mentions": [],
            },
        )
        a, b = max(m.char_start - 160, 0), min(m.char_end + 160, len(seg.text))
        entry["mentions"].append(
            {
                "id": str(m.id),
                "kind": m.kind,
                "raw": m.raw,
                "segment_id": str(seg.id),
                "version_id": str(seg.source_version_id),
                "page": seg.loc_page_start,
                "slide": seg.loc_slide,
                "t_ms": seg.loc_t_start_ms,
                "section": seg.loc_section,
                "context": ("…" if a else "") + seg.text[a:b] + ("…" if b < len(seg.text) else ""),
                "mime": mime,
            }
        )
    counts = {
        "mentions": len(rows),
        "sources": len(by_source),
        "in_bibliography": sum(1 for m, *_ in rows if m.kind == "bibliography"),
    }
    return {
        **ref_out(ref, counts, pub),
        "by_source": list(by_source.values()),
        "candidates": [] if pub else await publication_candidates(db, ref),
    }


async def _sections_of(db: AsyncSession, source_ids: set[uuid.UUID]) -> dict[uuid.UUID, list]:
    if not source_ids:
        return {}
    rows = (
        await db.execute(
            select(SourceSection.source_id, LibrarySection.key, LibrarySection.name)
            .join(LibrarySection, LibrarySection.key == SourceSection.section_key)
            .where(SourceSection.source_id.in_(source_ids))
            .order_by(LibrarySection.position)
        )
    ).all()
    out: dict[uuid.UUID, list] = {}
    for sid, key, name in rows:
        out.setdefault(sid, []).append({"key": key, "name": name})
    return out


async def publication_candidates(db: AsyncSession, ref: Reference) -> list[dict[str, Any]]:
    """Studies already in Estudios that could be this reference: same DOI, or same
    year and the first author's surname in their authors."""
    conds = []
    if ref.doi:
        conds.append(Publication.doi == ref.doi)
    if ref.year and ref.first_author:
        surname = ref.first_author.split()[-1]
        conds.append(
            (Publication.year == ref.year) & Publication.authors_text.ilike(f"%{surname}%")
        )
    if not conds:
        return []
    pubs = (await db.scalars(select(Publication).where(or_(*conds)).limit(5))).all()
    return [{"id": str(p.id), "title": p.title, "year": p.year, "doi": p.doi} for p in pubs]


async def sources_counts(db: AsyncSession, source_id: uuid.UUID) -> int:
    return (
        await db.scalar(
            select(func.count(func.distinct(ReferenceMention.reference_id))).where(
                ReferenceMention.source_id == source_id
            )
        )
        or 0
    )


# ---- changing ----------------------------------------------------------------------


def _audit(db, action: str, ref: Reference, after: dict[str, Any]) -> None:
    db.add(
        AuditLog(
            actor="user",
            action=action,
            subject_table="bib_references",
            subject_id=str(ref.id),
            after=after,
        )
    )


EDITABLE = ("title", "authors_text", "doi", "year", "first_author", "notes", "status")


async def update_fields(db: AsyncSession, ref: Reference, changes: dict[str, Any]) -> Reference:
    for k, v in changes.items():
        if k not in EDITABLE:
            continue
        if k == "status" and v not in ("active", "dismissed"):
            raise ReferenceError_("estado no válido")
        if k == "doi" and v:
            from kos.evidence.doi import normalize_doi

            v = normalize_doi(v)
        setattr(ref, k, v)
    ref.updated_at = datetime.now(UTC)
    _audit(db, "reference.updated", ref, {k: v for k, v in changes.items() if k in EDITABLE})
    await db.commit()
    return ref


async def link(db: AsyncSession, ref: Reference, publication_id: uuid.UUID | None) -> Reference:
    if publication_id and await db.get(Publication, publication_id) is None:
        raise ReferenceError_("no existe ese estudio")
    ref.publication_id = publication_id
    ref.updated_at = datetime.now(UTC)
    _audit(
        db,
        "reference.linked",
        ref,
        {"publication_id": str(publication_id) if publication_id else None},
    )
    await db.commit()
    return ref


async def create_publication(db: AsyncSession, ref: Reference) -> Publication:
    """Add the reference to Estudios: from Crossref when it has a DOI, else with the
    title, authors and year found (quality «no evaluada», as always)."""
    if ref.doi:
        if existing := await ev.by_doi(db, ref.doi):
            pub = existing
        else:
            pub = await ev.create_from_doi(db, ref.doi)
    else:
        if not ref.title:
            raise ReferenceError_(
                "falta el título: complétalo (o el DOI) antes de añadirla a Estudios"
            )
        pub = await ev.create(
            db, {"title": ref.title, "year": ref.year, "authors_text": ref.authors_text}
        )
    await link(db, ref, pub.id)
    return pub


async def merge(db: AsyncSession, src: Reference, into: Reference) -> Reference:
    """«Son la misma»: mentions move, the empty reference is kept as dismissed."""
    if src.id == into.id:
        raise ReferenceError_("es la misma referencia")
    await db.execute(
        update(ReferenceMention)
        .where(ReferenceMention.reference_id == src.id)
        .values(reference_id=into.id)
    )
    for k in ("title", "authors_text", "doi", "first_author", "year", "publication_id"):
        if getattr(into, k) is None and getattr(src, k) is not None:
            setattr(into, k, getattr(src, k))
    src.status = "dismissed"
    src.merged_into_id = into.id
    src.notes = f"Unida a {into.key}"
    _audit(db, "reference.merged", src, {"into": str(into.id)})
    await db.commit()
    return into
