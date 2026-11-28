"""The user's highlights and notes on PDFs (made in the in-app reader). They live here,
never in the original file. The reader sends the whole list of a file version on each
change (debounced): the list replaces what was stored."""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select

from kos.api.deps import DbDep, UserDep
from kos.knowledge import service as ks
from kos.knowledge.extraction import locate_quote
from kos.models import (
    Claim,
    ClaimEntity,
    Entity,
    Flashcard,
    PdfAnnotation,
    Source,
    SourceSegment,
    SourceVersion,
)

router = APIRouter(prefix="/api", tags=["annotations"])

MAX_ITEMS = 3000
MAX_ITEM_BYTES = 200_000  # a long freehand drawing is the biggest kind
MAX_TEXT = 5000


class AnnotationIn(BaseModel):
    data: dict[str, Any]
    text: str | None = Field(default=None, max_length=MAX_TEXT)


class AnnotationsIn(BaseModel):
    annotations: list[AnnotationIn] = Field(max_length=MAX_ITEMS)


async def _version(db, source_id: uuid.UUID, version_id: uuid.UUID) -> SourceVersion:
    v = await db.get(SourceVersion, version_id)
    if v is None or v.source_id != source_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "archivo no encontrado")
    return v


def _out(a: PdfAnnotation) -> dict[str, Any]:
    return {
        "id": a.ink_id,
        "type": a.type,
        "page": a.page + 1,
        "text": a.text,
        "comment": a.comment,
        "reply_to": a.reply_to,
        "claim_id": str(a.claim_id) if a.claim_id else None,
        "card_id": str(a.card_id) if a.card_id else None,
        "updated_at": a.updated_at or a.created_at,
    }


@router.get("/sources/{source_id}/versions/{version_id}/annotations")
async def get_annotations(source_id: uuid.UUID, version_id: uuid.UUID, db: DbDep, _: UserDep):
    await _version(db, source_id, version_id)
    rows = (
        await db.scalars(
            select(PdfAnnotation)
            .where(PdfAnnotation.version_id == version_id)
            .order_by(PdfAnnotation.page, PdfAnnotation.created_at)
        )
    ).all()
    return {"annotations": [a.data for a in rows], "items": [_out(a) for a in rows]}


def _check(item: AnnotationIn) -> tuple[str, str, int]:
    d = item.data
    ink_id, kind, page = d.get("id"), d.get("type"), d.get("page")
    if not isinstance(ink_id, str) or not 0 < len(ink_id) <= 80:
        raise ValueError("anotación sin id válido")
    if not isinstance(kind, str) or not 0 < len(kind) <= 30:
        raise ValueError("anotación sin tipo válido")
    if not isinstance(page, int) or isinstance(page, bool) or not 0 <= page < 100_000:
        raise ValueError("anotación sin página válida")
    if len(json.dumps(d)) > MAX_ITEM_BYTES:
        raise ValueError("anotación demasiado grande")
    return ink_id, kind, page


@router.put("/sources/{source_id}/versions/{version_id}/annotations")
async def put_annotations(
    source_id: uuid.UUID, version_id: uuid.UUID, body: AnnotationsIn, db: DbDep, _: UserDep
):
    """Replace this file's annotations with the list sent. The selected text of one
    already stored is kept when the reader no longer has it (after a reload)."""
    await _version(db, source_id, version_id)
    try:
        checked = [(_check(it), it) for it in body.annotations]
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    ids = [c[0][0] for c in checked]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "anotaciones repetidas")
    existing = {
        a.ink_id: a
        for a in (
            await db.scalars(select(PdfAnnotation).where(PdfAnnotation.version_id == version_id))
        ).all()
    }
    gone = [k for k in existing if k not in set(ids)]
    if gone:
        await db.execute(
            delete(PdfAnnotation).where(
                PdfAnnotation.version_id == version_id, PdfAnnotation.ink_id.in_(gone)
            )
        )
    now = datetime.now(UTC)
    for (ink_id, kind, page), it in checked:
        d = it.data
        comment = d.get("contents") if isinstance(d.get("contents"), str) else None
        reply = d.get("inReplyTo") if isinstance(d.get("inReplyTo"), str) else None
        text = (it.text or "").strip() or None
        a = existing.get(ink_id)
        if a is None:
            db.add(
                PdfAnnotation(
                    source_id=source_id,
                    version_id=version_id,
                    ink_id=ink_id,
                    type=kind,
                    page=page,
                    text=text,
                    comment=comment,
                    reply_to=reply,
                    data=d,
                )
            )
        elif a.data != d or (text and text != a.text):
            a.type, a.page, a.comment, a.reply_to, a.data = kind, page, comment, reply, d
            a.text = text or a.text
            a.updated_at = now
    await db.commit()
    return {"saved": len(ids), "removed": len(gone)}


@router.get("/annotations")
async def list_annotations(
    db: DbDep,
    _: UserDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    source_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Your highlights and notes across the library (replies left out), newest first,
    optionally matching ``q`` in the highlighted text or the comment."""
    where = [PdfAnnotation.reply_to.is_(None)]
    if source_id:
        where.append(PdfAnnotation.source_id == source_id)
    if q and q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append(
            or_(
                PdfAnnotation.text.ilike(like, escape="\\"),
                PdfAnnotation.comment.ilike(like, escape="\\"),
            )
        )
    total = await db.scalar(select(func.count()).select_from(PdfAnnotation).where(*where))
    rows = (
        await db.execute(
            select(PdfAnnotation, Source.title)
            .join(Source, Source.id == PdfAnnotation.source_id)
            .where(*where)
            .order_by(func.coalesce(PdfAnnotation.updated_at, PdfAnnotation.created_at).desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    concepts = await _concepts_of(db, [a for a, _ in rows])
    return {
        "total": total or 0,
        "items": [
            {
                **_out(a),
                "source_id": str(a.source_id),
                "version_id": str(a.version_id),
                "source_title": title,
                "concept": concepts.get(a.id),
            }
            for a, title in rows
        ],
    }


async def _concepts_of(db, anns: list[PdfAnnotation]) -> dict[uuid.UUID, dict[str, str]]:
    """The concept of the claim or card made from each annotation, for a link."""
    out: dict[uuid.UUID, dict[str, str]] = {}
    for a in anns:
        eid = None
        if a.claim_id:
            eid = await db.scalar(
                select(ClaimEntity.entity_id).where(ClaimEntity.claim_id == a.claim_id).limit(1)
            )
        if eid is None and a.card_id:
            eid = await db.scalar(select(Flashcard.entity_id).where(Flashcard.id == a.card_id))
        if eid and (e := await db.get(Entity, eid)):
            out[a.id] = {"id": str(e.id), "slug": e.slug, "name": e.canonical_name}
    return out


# ---- turning a highlight into knowledge ------------------------------------------------


class EntityRef(BaseModel):
    id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=300)


def _concept(e: Entity) -> dict[str, str]:
    return {"id": str(e.id), "slug": e.slug, "name": e.canonical_name}


async def _annotation(db, version_id: uuid.UUID, ink_id: str) -> PdfAnnotation:
    a = await db.scalar(
        select(PdfAnnotation).where(
            PdfAnnotation.version_id == version_id, PdfAnnotation.ink_id == ink_id
        )
    )
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subrayado no encontrado")
    return a


async def _entity(db, ref: EntityRef) -> Entity:
    """An existing concept by id, or by name: the one with that name or alias, or a
    new concept."""
    if ref.id:
        e = await db.get(Entity, ref.id)
        if e is None or e.status == "merged":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "concepto no encontrado")
        return e
    name = (ref.name or "").strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "elige o escribe un concepto")
    if e := await ks.entity_with_alias(db, name):
        return e
    return await ks.create_entity(db, type_key="concept", name=name, origin="user")


async def _passage(db, a: PdfAnnotation) -> tuple[uuid.UUID, str]:
    """The library fragment that holds the highlighted text, and the exact quote. A
    highlight across two fragments is matched by its first words."""
    text = (a.text or "").strip()
    if not text:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "este subrayado no tiene texto (¿es una nota o una imagen?): no se puede citar",
        )
    segs = (
        await db.scalars(
            select(SourceSegment)
            .where(SourceSegment.source_version_id == a.version_id)
            .order_by(SourceSegment.ordinal)
        )
    ).all()
    page = a.page + 1
    near = [
        s
        for s in segs
        if s.loc_page_start is None
        or s.loc_page_start <= page <= (s.loc_page_end or s.loc_page_start)
    ]
    words = text.split()
    for candidate in (text, " ".join(words[:12]), " ".join(words[:6])):
        if candidate != text and len(candidate.split()) < 3:
            continue
        for group in (near, segs):
            for s in group:
                if found := locate_quote(s.text, candidate):
                    return s.id, found[1]
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        "el texto subrayado no aparece tal cual en los fragmentos de la fuente "
        "(¿está procesada? ¿es una página escaneada?)",
    )


class ToClaimIn(BaseModel):
    entity: EntityRef
    text: str | None = Field(default=None, max_length=2000)  # the claim; default: the quote
    claim_type: Literal[
        "definition",
        "reported_fact",
        "hypothesis",
        "interpretation",
        "opinion",
        "recommendation",
        "personal_conclusion",
    ] = "reported_fact"


@router.post("/annotations/{version_id}/{ink_id}/claim", status_code=status.HTTP_201_CREATED)
async def to_claim(version_id: uuid.UUID, ink_id: str, body: ToClaimIn, db: DbDep, _: UserDep):
    """A claim about a concept that cites the highlighted passage. Written by the user,
    so it counts as reviewed. From a study, it is the study's; from a source with a
    single author, it is attributed to that person."""
    a = await _annotation(db, version_id, ink_id)
    if a.claim_id and await db.get(Claim, a.claim_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "este subrayado ya es una afirmación")
    segment_id, quote = await _passage(db, a)
    entity = await _entity(db, body.entity)
    source = await db.get(Source, a.source_id)
    authors = source.author_rows if source else []
    try:
        claim = await ks.add_claim(
            db,
            text_=(body.text or "").strip() or quote,
            claim_type=body.claim_type,
            entities=[(entity.id, "subject")],
            evidence=[(segment_id, quote)],
            person_id=authors[0].person_id if len(authors) == 1 else None,
            publication_id=source.publication_id if source else None,
            context=a.comment or None,
            origin="user",
        )
    except ks.KnowledgeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    a.claim_id = claim.id
    await db.commit()
    return {"claim_id": str(claim.id), "concept": _concept(entity)}


class ToCardIn(BaseModel):
    entity: EntityRef
    front: str = Field(min_length=1, max_length=1000)
    back: str | None = Field(default=None, max_length=3000)  # default: the quote


@router.post("/annotations/{version_id}/{ink_id}/card", status_code=status.HTTP_201_CREATED)
async def to_card(version_id: uuid.UUID, ink_id: str, body: ToCardIn, db: DbDep, _: UserDep):
    """A study card on a concept whose answer is (or rests on) the highlighted passage,
    with the passage as its source. Active at once: the user wrote it."""
    from kos.study import cards

    a = await _annotation(db, version_id, ink_id)
    if a.card_id and await db.get(Flashcard, a.card_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "este subrayado ya es una tarjeta")
    segment_id, quote = await _passage(db, a)
    entity = await _entity(db, body.entity)
    card = cards.add_card(
        db,
        entity_id=entity.id,
        front=body.front,
        back=(body.back or "").strip() or quote,
        kind="qa",
        origin="user",
        claim_id=a.claim_id,
        segment_id=segment_id,
        quote=quote,
    )
    await db.flush()
    a.card_id = card.id
    await db.commit()
    return {"card_id": str(card.id), "concept": _concept(entity)}
