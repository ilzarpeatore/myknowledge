"""Authors of sources and the Autores library (the ``persons`` table: teachers,
researchers, book authors).

Search is for typing: it matches the start of any word of the name, ignoring case and
accents ("schoen" finds "Brad J. Schoenfeld", "lopez" finds "López Chicharro"); when
nothing starts like that, close spellings via trigrams ("Schonfeld"). Adding an
author by name reuses the person with the same normalized name instead of creating a
duplicate; real duplicates written differently ("B. Schoenfeld" / "Brad Schoenfeld")
can be merged.
"""

import uuid
from typing import Any

from sqlalchemy import and_, case, delete, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kos.models import (
    AuditLog,
    Claim,
    Course,
    Person,
    Program,
    Source,
    SourceAuthor,
    SourceStatus,
)
from kos.models.knowledge import normalize_name

MAX_AUTHORS = 50
ROLES = ("teacher", "researcher", "author")


class AuthorError(Exception):
    pass


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search(
    db: AsyncSession,
    q: str | None = None,
    role: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """People ordered by how well they match ``q`` (or by name), with how many sources
    and claims each one has."""
    n_sources = (
        select(func.count())
        .select_from(SourceAuthor)
        .join(Source, Source.id == SourceAuthor.source_id)
        .where(SourceAuthor.person_id == Person.id, Source.status != SourceStatus.archived)
        .scalar_subquery()
    )
    n_claims = (
        select(func.count())
        .select_from(Claim)
        .where(Claim.attributed_person_id == Person.id)
        .scalar_subquery()
    )
    where = []
    order: list[Any] = []
    nq = normalize_name(q or "")
    if q and q.strip() and not nq:  # only punctuation: nothing to match
        return [], 0
    if nq:
        esc = _like(nq)
        start = Person.name_normalized.like(f"{esc}%", escape="\\")
        word = Person.name_normalized.like(f"% {esc}%", escape="\\")
        # every typed word starts a word of the name, in any order ("schoenfeld brad")
        all_words = [
            or_(
                Person.name_normalized.like(f"{_like(w)}%", escape="\\"),
                Person.name_normalized.like(f"% {_like(w)}%", escape="\\"),
            )
            for w in nq.split()
        ]
        exact = or_(start, word, *([and_(*all_words)] if len(all_words) > 1 else []))
        # close spellings ("Schonfeld") only when nothing starts like that
        if len(nq) >= 4 and not await db.scalar(select(Person.id).where(exact).limit(1)):
            exact = func.word_similarity(literal(nq), Person.name_normalized) > 0.5
        where.append(exact)
        order = [
            case((start, 0), (word, 1), else_=2),
            func.word_similarity(literal(nq), Person.name_normalized).desc(),
        ]
    if role:
        where.append(Person.role == role)
    total = await db.scalar(select(func.count()).select_from(Person).where(*where))
    rows = (
        await db.execute(
            select(Person, n_sources.label("sources"), n_claims.label("claims"))
            .where(*where)
            .order_by(*order, Person.name)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [person_out(p, sources=s, claims=c) for p, s, c in rows], total or 0


def person_out(p: Person, **extra) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "name": p.name,
        "role": p.role,
        "affiliation": p.affiliation,
        "notes": p.notes,
        **extra,
    }


async def by_name(db: AsyncSession, name: str) -> Person | None:
    n = normalize_name(name)
    if not n:
        return None
    return await db.scalar(
        select(Person).where(Person.name_normalized == n).order_by(Person.created_at).limit(1)
    )


async def create(
    db: AsyncSession, name: str, role: str | None = None, affiliation: str | None = None
) -> tuple[Person, bool]:
    """(person, created). An existing person with the same name is reused."""
    name = " ".join(name.split())
    if not normalize_name(name):
        raise AuthorError("el nombre está vacío")
    if len(name) > 300:
        raise AuthorError("el nombre admite hasta 300 caracteres")
    if role is not None and role not in ROLES:
        raise AuthorError("papel no válido")
    if existing := await by_name(db, name):
        if role and not existing.role:
            existing.role = role
        return existing, False
    p = Person(name=name, role=role, affiliation=affiliation)
    db.add(p)
    await db.flush()
    db.add(
        AuditLog(
            actor="user",
            action="person.created",
            subject_table="persons",
            subject_id=str(p.id),
            after={"name": name, "role": role},
        )
    )
    return p, True


async def resolve(
    db: AsyncSession, items: list[dict[str, Any]] | None, default_role: str | None = None
) -> list[uuid.UUID]:
    """Author list from the client: each item is {"id": ...} (an existing person) or
    {"name": ...} (found by name or created). Order kept, repeats removed."""
    out: list[uuid.UUID] = []
    for it in items or []:
        if not isinstance(it, dict):
            raise AuthorError("autor no válido")
        if it.get("id"):
            try:
                pid = uuid.UUID(str(it["id"]))
            except ValueError:
                raise AuthorError("autor no válido") from None
            if await db.get(Person, pid) is None:
                raise AuthorError("autor no encontrado (¿se fusionó o borró?)")
        elif it.get("name"):
            p, _ = await create(db, str(it["name"]), it.get("role") or default_role)
            pid = p.id
        else:
            continue
        if pid not in out:
            out.append(pid)
    if len(out) > MAX_AUTHORS:
        raise AuthorError(f"máximo {MAX_AUTHORS} autores por fuente")
    return out


async def set_authors(
    db: AsyncSession, source: Source, ids: list[uuid.UUID], *, actor: str = "user"
) -> None:
    """Replace the authors of a source (in this order). The free-text authors field
    shows their names, so lists and exports keep working."""
    before = [str(r.person_id) for r in source.author_rows]
    await db.execute(delete(SourceAuthor).where(SourceAuthor.source_id == source.id))
    db.add_all(
        SourceAuthor(source_id=source.id, person_id=p, position=i) for i, p in enumerate(ids)
    )
    await db.flush()
    await db.refresh(source, ["author_rows"])
    if ids:
        source.authors_text = "; ".join(r.person.name for r in source.author_rows)[:2000]
    db.add(
        AuditLog(
            actor=actor,
            action="source.authors",
            subject_table="sources",
            subject_id=str(source.id),
            before={"authors": before},
            after={"authors": [str(i) for i in ids]},
        )
    )


def out(source: Source) -> list[dict[str, Any]]:
    return [
        {"id": str(r.person_id), "name": r.person.name, "role": r.person.role}
        for r in source.author_rows
    ]


async def detail(db: AsyncSession, p: Person) -> dict[str, Any]:
    rows = (
        (
            await db.execute(
                select(Source)
                .join(SourceAuthor, SourceAuthor.source_id == Source.id)
                .where(SourceAuthor.person_id == p.id, Source.status != SourceStatus.archived)
                .order_by(Source.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    claims = await db.scalar(
        select(func.count()).select_from(Claim).where(Claim.attributed_person_id == p.id)
    )
    return person_out(
        p,
        claims=claims or 0,
        sources=[
            {
                "id": str(s.id),
                "title": s.title,
                "kind": s.kind.value if hasattr(s.kind, "value") else s.kind,
                "co_authors": [r.person.name for r in s.author_rows if r.person_id != p.id],
            }
            for s in rows
        ],
    )


async def update_person(db: AsyncSession, p: Person, changes: dict[str, Any]) -> None:
    if "name" in changes:
        name = " ".join((changes["name"] or "").split())
        if not normalize_name(name):
            raise AuthorError("el nombre está vacío")
        other = await by_name(db, name)
        if other is not None and other.id != p.id:
            raise AuthorError(f"ya existe «{other.name}»: fusiónalos en vez de renombrar")
        changes["name"] = name
    if changes.get("role") not in (None, *ROLES):
        raise AuthorError("papel no válido")
    before = {k: getattr(p, k) for k in changes}
    for k, v in changes.items():
        setattr(p, k, v)
    if "name" in changes:  # keep the authors text of their sources in step
        for s in (
            await db.scalars(
                select(Source).join(SourceAuthor).where(SourceAuthor.person_id == p.id)
            )
        ).all():
            s.authors_text = "; ".join(r.person.name for r in s.author_rows)[:2000]
    db.add(
        AuditLog(
            actor="user",
            action="person.updated",
            subject_table="persons",
            subject_id=str(p.id),
            before=before,
            after=changes,
        )
    )


async def merge(db: AsyncSession, dup: Person, keep: Person) -> dict[str, int]:
    """Everything of ``dup`` passes to ``keep`` (sources, claims, courses, programs)
    and ``dup`` is removed. Recorded in the audit log."""
    if dup.id == keep.id:
        raise AuthorError("es la misma persona")
    already = select(SourceAuthor.source_id).where(SourceAuthor.person_id == keep.id)
    moved_sources = (
        await db.execute(
            update(SourceAuthor)
            .where(SourceAuthor.person_id == dup.id, SourceAuthor.source_id.not_in(already))
            .values(person_id=keep.id)
        )
    ).rowcount
    await db.execute(delete(SourceAuthor).where(SourceAuthor.person_id == dup.id))
    claims = (
        await db.execute(
            update(Claim)
            .where(Claim.attributed_person_id == dup.id)
            .values(attributed_person_id=keep.id)
        )
    ).rowcount
    for model in (Course, Program):
        await db.execute(
            update(model).where(model.teacher_person_id == dup.id).values(teacher_person_id=keep.id)
        )
    for field in ("role", "affiliation", "notes"):
        if not getattr(keep, field) and getattr(dup, field):
            setattr(keep, field, getattr(dup, field))
    db.add(
        AuditLog(
            actor="user",
            action="person.merged",
            subject_table="persons",
            subject_id=str(keep.id),
            before={"merged": str(dup.id), "name": dup.name},
            after={"sources": moved_sources, "claims": claims},
        )
    )
    await db.delete(dup)
    await db.flush()
    for s in (
        await db.scalars(select(Source).join(SourceAuthor).where(SourceAuthor.person_id == keep.id))
    ).all():
        await db.refresh(s, ["author_rows"])
        s.authors_text = "; ".join(r.person.name for r in s.author_rows)[:2000]
    return {"sources": moved_sources, "claims": claims}
