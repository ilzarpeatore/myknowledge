"""Library sections (Cursos, Libros, Estudios...) and the links between sources.

A source can be in several sections. When it arrives it is placed automatically by
its format (video, audio, EPUB), and later when it is linked to a study or used in a
course; automatic placements are only ever *added*, never override yours. Related
content across sections comes from concepts both sources talk about (through cited
claims) and from links you make.
"""

import re
import unicodedata
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kos.models import (
    AuditLog,
    Claim,
    ClaimEntity,
    Course,
    CourseMaterial,
    Entity,
    Evidence,
    LibrarySection,
    Source,
    SourceKind,
    SourceLink,
    SourceSection,
    SourceSegment,
    SourceVersion,
)

FALLBACK = "recursos"


class SectionError(Exception):
    pass


def suggest(kind: str, mime: str | None) -> str:
    if kind == SourceKind.video:
        return "videos"
    if kind == SourceKind.audio:
        return "audios"
    if mime == "application/epub+zip":
        return "libros"
    return FALLBACK


async def add(db: AsyncSession, source_id: uuid.UUID, key: str, origin: str = "auto") -> None:
    """Put a source in a section (no-op if already there or the section is gone)."""
    if await db.get(LibrarySection, key) is None:
        return
    await db.execute(
        insert(SourceSection)
        .values(source_id=source_id, section_key=key, origin=origin)
        .on_conflict_do_nothing()
    )


async def set_sections(db: AsyncSession, source: Source, keys: list[str]) -> None:
    keys = list(dict.fromkeys(keys))
    if not keys:
        raise SectionError("una fuente debe estar al menos en una sección")
    known = set(
        (await db.scalars(select(LibrarySection.key).where(LibrarySection.key.in_(keys)))).all()
    )
    if unknown := set(keys) - known:
        raise SectionError(f"sección desconocida: {', '.join(sorted(unknown))}")
    before = sorted(r.section_key for r in source.section_rows)
    source.section_rows = [r for r in source.section_rows if r.section_key in keys]
    have = {r.section_key for r in source.section_rows}
    source.section_rows += [
        SourceSection(source_id=source.id, section_key=k, origin="user")
        for k in keys
        if k not in have
    ]
    for r in source.section_rows:
        r.origin = "user"  # confirmed by the user
    db.add(
        AuditLog(
            actor="user",
            action="source.sections",
            subject_table="sources",
            subject_id=str(source.id),
            before={"sections": before},
            after={"sections": keys},
        )
    )
    await db.commit()


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:40]


async def create_section(db: AsyncSession, name: str, description: str | None) -> LibrarySection:
    key = _slug(name)
    if not key:
        raise SectionError("nombre no válido")
    if await db.get(LibrarySection, key) is not None:
        raise SectionError("ya existe una sección con ese nombre")
    pos = (await db.scalar(select(func.max(LibrarySection.position)))) or 0
    sec = LibrarySection(
        key=key, name=name.strip(), description=description, position=pos + 1, builtin=False
    )
    db.add(sec)
    await db.commit()
    return sec


async def delete_section(db: AsyncSession, sec: LibrarySection) -> int:
    """Remove a user section; sources left without any section go to Recursos."""
    if sec.builtin:
        raise SectionError("las secciones predefinidas se pueden renombrar, no borrar")
    members = list(
        (
            await db.scalars(
                select(SourceSection.source_id).where(SourceSection.section_key == sec.key)
            )
        ).all()
    )
    await db.delete(sec)
    await db.flush()
    moved = 0
    for sid in members:
        left = await db.scalar(
            select(func.count()).select_from(SourceSection).where(SourceSection.source_id == sid)
        )
        if not left:
            await add(db, sid, FALLBACK)
            moved += 1
    await db.commit()
    return moved


async def counts(db: AsyncSession) -> dict[str, int]:
    rows = await db.execute(
        select(SourceSection.section_key, func.count())
        .join(Source, Source.id == SourceSection.source_id)
        .where(Source.status != "archived")
        .group_by(SourceSection.section_key)
    )
    return dict(rows.all())


# ---- crossing sections --------------------------------------------------------------------


def _concepts_of_sources():
    """(source_id, entity_id, claims) for every source with cited claims."""
    return (
        select(
            SourceVersion.source_id.label("source_id"),
            ClaimEntity.entity_id.label("entity_id"),
            func.count(func.distinct(Claim.id)).label("claims"),
        )
        .join(SourceSegment, SourceSegment.source_version_id == SourceVersion.id)
        .join(Evidence, Evidence.segment_id == SourceSegment.id)
        .join(Claim, Claim.id == Evidence.claim_id)
        .join(ClaimEntity, ClaimEntity.claim_id == Claim.id)
        .where(Claim.review_status != "rejected")
        .group_by(SourceVersion.source_id, ClaimEntity.entity_id)
    )


async def _sections_of(db, ids) -> dict[uuid.UUID, list[str]]:
    out: dict[uuid.UUID, list[str]] = {i: [] for i in ids}
    for sid, key in (
        await db.execute(
            select(SourceSection.source_id, SourceSection.section_key).where(
                SourceSection.source_id.in_(ids)
            )
        )
    ).all():
        out[sid].append(key)
    return out


async def related(db: AsyncSession, source: Source, limit: int = 30) -> dict[str, Any]:
    """What this source is about and what else in the library connects to it."""
    cs = _concepts_of_sources().subquery()
    mine = (
        await db.execute(
            select(cs.c.entity_id, cs.c.claims)
            .where(cs.c.source_id == source.id)
            .order_by(cs.c.claims.desc())
        )
    ).all()
    concept_ids = [e for e, _ in mine]
    concepts = []
    for eid, n in mine[:40]:
        e = await db.get(Entity, eid)
        if e and e.status != "merged":
            concepts.append(
                {"id": str(e.id), "slug": e.slug, "name": e.canonical_name, "claims": n}
            )
    shared: dict[uuid.UUID, list[uuid.UUID]] = {}
    if concept_ids:
        for sid, eid in (
            await db.execute(
                select(cs.c.source_id, cs.c.entity_id).where(
                    cs.c.entity_id.in_(concept_ids), cs.c.source_id != source.id
                )
            )
        ).all():
            shared.setdefault(sid, []).append(eid)
    links = (
        await db.scalars(
            select(SourceLink).where(
                (SourceLink.a_id == source.id) | (SourceLink.b_id == source.id)
            )
        )
    ).all()
    linked = {(lk.b_id if lk.a_id == source.id else lk.a_id): lk for lk in links}
    ids = list(dict.fromkeys([*linked, *sorted(shared, key=lambda s: -len(shared[s]))]))[:limit]
    secs = await _sections_of(db, ids)
    names = {
        e: n
        for e, n in (
            await db.execute(
                select(Entity.id, Entity.canonical_name).where(Entity.id.in_(concept_ids))
            )
        ).all()
    }
    items = []
    for sid in ids:
        s = await db.get(Source, sid)
        if s is None or s.status == "archived":
            continue
        lk = linked.get(sid)
        items.append(
            {
                "id": str(s.id),
                "title": s.title,
                "kind": s.kind,
                "sections": secs.get(sid, []),
                "shared_concepts": [names.get(e, "?") for e in shared.get(sid, [])][:8],
                "shared_count": len(shared.get(sid, [])),
                "link": {"id": str(lk.id), "note": lk.note} if lk else None,
            }
        )
    courses = (
        await db.execute(
            select(Course.id, Course.title)
            .join(CourseMaterial, CourseMaterial.course_id == Course.id)
            .where(CourseMaterial.source_id == source.id)
            .distinct()
        )
    ).all()
    return {
        "concepts": concepts,
        "related": items,
        "courses": [{"id": str(i), "title": t} for i, t in courses],
    }


async def link(db: AsyncSession, a: uuid.UUID, b: uuid.UUID, note: str | None) -> SourceLink:
    if a == b:
        raise SectionError("una fuente no se enlaza consigo misma")
    x, y = sorted((a, b), key=str)
    existing = await db.scalar(select(SourceLink).where(SourceLink.a_id == x, SourceLink.b_id == y))
    if existing:
        existing.note = note or existing.note
        await db.commit()
        return existing
    lk = SourceLink(a_id=x, b_id=y, note=note)
    db.add(lk)
    await db.commit()
    return lk


async def concept_sources(db: AsyncSession, entity_id: uuid.UUID) -> list[dict[str, Any]]:
    """Where a concept appears, grouped by section (a source can count in several)."""
    cs = _concepts_of_sources().subquery()
    rows = (
        await db.execute(
            select(cs.c.source_id, cs.c.claims)
            .where(cs.c.entity_id == entity_id)
            .order_by(cs.c.claims.desc())
        )
    ).all()
    secs = await _sections_of(db, [r[0] for r in rows])
    sections = {s.key: s for s in (await db.scalars(select(LibrarySection))).all()}
    groups: dict[str, list[dict[str, Any]]] = {}
    for sid, n in rows:
        s = await db.get(Source, sid)
        if s is None or s.status == "archived":
            continue
        for key in secs.get(sid) or [FALLBACK]:
            groups.setdefault(key, []).append({"id": str(s.id), "title": s.title, "claims": n})
    return [
        {"key": k, "name": sections[k].name if k in sections else k, "sources": v}
        for k, v in sorted(
            groups.items(), key=lambda kv: sections[kv[0]].position if kv[0] in sections else 99
        )
    ]


async def section_sources(db: AsyncSession, key: str) -> list[uuid.UUID]:
    return list(
        (
            await db.scalars(
                select(SourceSection.source_id).where(SourceSection.section_key == key)
            )
        ).all()
    )
