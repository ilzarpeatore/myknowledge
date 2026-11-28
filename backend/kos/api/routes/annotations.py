"""The user's highlights and notes on PDFs (made in the in-app reader). They live here,
never in the original file. The reader sends the whole list of a file version on each
change (debounced): the list replaces what was stored."""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select

from kos.api.deps import DbDep, UserDep
from kos.models import PdfAnnotation, Source, SourceVersion

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
    return {
        "total": total or 0,
        "items": [
            {
                **_out(a),
                "source_id": str(a.source_id),
                "version_id": str(a.version_id),
                "source_title": title,
            }
            for a, title in rows
        ],
    }
