import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from kos.api.deps import DbDep, SettingsDep, UserDep
from kos.library import service
from kos.library.blobstore import CHUNK, TooLarge, get_store
from kos.models import (
    AuditLog,
    Job,
    JobStatus,
    Source,
    SourceDiscipline,
    SourceKind,
    SourcePage,
    SourceSegment,
    SourceStatus,
    SourceVersion,
)

router = APIRouter(prefix="/api/sources", tags=["library"])


class VersionOut(BaseModel):
    id: uuid.UUID
    sha256: str
    original_filename: str | None
    mime: str
    size_bytes: int
    page_count: int | None
    extractor: str | None
    extraction_info: dict | None
    extracted_at: datetime | None
    created_at: datetime


class SourceOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: str
    status: SourceStatus
    language: str | None
    authors_text: str | None
    url: str | None
    notes: str | None
    publication_id: uuid.UUID | None = None
    sections: list[str] = []
    disciplines: list[dict[str, str]] = []  # branches: Nutrición, Biomecánica...
    authors: list[dict[str, str | None]] = []  # people of the Autores library, in order
    # Why processing failed (from the last failed job), for failed sources only.
    error: str | None = None
    created_at: datetime
    versions: list[VersionOut]


class UploadOut(BaseModel):
    source: SourceOut
    duplicate: bool


class SourceListOut(BaseModel):
    items: list[SourceOut]
    total: int


def _out(source: Source, error: str | None = None) -> SourceOut:
    out = SourceOut.model_validate(source, from_attributes=True)
    out.error = error
    out.sections = [r.section_key for r in source.section_rows]
    from kos.library.disciplines import out as discipline_out

    out.disciplines = discipline_out(source)
    from kos.library.authors import out as authors_out

    out.authors = authors_out(source)
    return out


async def _errors(db, sources) -> dict[uuid.UUID, str]:
    """Last failed job message for each failed source (by source or version id)."""
    failed = [s for s in sources if s.status == SourceStatus.failed]
    if not failed:
        return {}
    owner = {str(s.id): s.id for s in failed}
    owner |= {str(v.id): s.id for s in failed for v in s.versions}
    jobs = await db.scalars(
        select(Job)
        .where(
            Job.status == JobStatus.failed,
            Job.payload["source_id"].astext.in_(owner)
            | Job.payload["source_version_id"].astext.in_(owner),
        )
        .order_by(Job.created_at)
    )
    out: dict[uuid.UUID, str] = {}
    for j in jobs:
        key = j.payload.get("source_id") or j.payload.get("source_version_id")
        if key in owner and j.last_error:
            out[owner[key]] = j.last_error.removeprefix("PermanentJobError: ")[:500]
    return out


async def _get_or_404(db, source_id: uuid.UUID) -> Source:
    source = await service.get_source(db, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "source not found")
    return source


@router.post("", response_model=UploadOut)
async def upload(
    file: UploadFile,
    db: DbDep,
    s: SettingsDep,
    _: UserDep,
    title: Annotated[str | None, Form(max_length=500)] = None,
    section: Annotated[str | None, Form(max_length=40)] = None,
    disciplines: Annotated[list[str] | None, Form()] = None,
    authors: Annotated[str | None, Form(max_length=20000)] = None,
):
    """Upload a file. ``disciplines``: branch ids (repeat the field or comma-separate).
    ``authors``: JSON list of {"id": person id} or {"name": new or existing name}."""
    from kos.library import authors as auth
    from kos.library import disciplines as disc

    try:
        branch_ids = await disc.parse_ids(db, disciplines)
        author_items = json.loads(authors) if authors else []
        if not isinstance(author_items, list):
            raise ValueError
    except disc.DisciplineError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "lista de autores no válida") from None

    async def chunks():
        while data := await file.read(CHUNK):
            yield data

    store = get_store()
    try:
        staged = await store.stage(chunks(), s.max_upload_mb * 1024 * 1024)
    except TooLarge:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"supera el límite de {s.max_upload_mb} MB"
        ) from None
    try:
        result = await service.add_upload(db, store, staged, file.filename, title, section)
    except service.UnsupportedFile as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from None
    if branch_ids:  # a duplicate keeps its branches and gains the new ones
        current = [r.discipline_id for r in result.source.discipline_rows]
        await disc.set_disciplines(db, result.source, list(dict.fromkeys(current + branch_ids)))
        await db.commit()
    if author_items:  # likewise, a duplicate keeps its authors and gains the new ones
        try:
            ids = await auth.resolve(db, author_items, _role_for(section))
        except auth.AuthorError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
        current = [r.person_id for r in result.source.author_rows]
        await auth.set_authors(db, result.source, list(dict.fromkeys(current + ids)))
        await db.commit()
    return UploadOut(source=_out(result.source), duplicate=result.duplicate)


def _role_for(section: str | None) -> str | None:
    """Role given to an author created while uploading, from the section."""
    return {
        "estudios": "researcher",
        "articulos": "researcher",
        "cursos": "teacher",
        "videos": "teacher",
        "audios": "teacher",
        "libros": "author",
        "manuales": "author",
    }.get(section or "")


class AuthorRef(BaseModel):
    id: str | None = None
    name: str | None = Field(default=None, max_length=300)


class YouTubeIn(BaseModel):
    url: str = Field(min_length=5, max_length=500)
    disciplines: list[str] = Field(default=[], max_length=20)
    authors: list[AuthorRef] = Field(default=[], max_length=50)


@router.post("/youtube", response_model=UploadOut)
async def add_youtube(body: YouTubeIn, db: DbDep, _: UserDep):
    """Register a YouTube video; the worker fetches its subtitles (or audio)."""
    from kos.library import disciplines as disc
    from kos.media import youtube

    try:
        branch_ids = await disc.parse_ids(db, body.disciplines)
    except disc.DisciplineError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    try:
        vid = youtube.video_id(body.url)
    except youtube.YouTubeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    url = youtube.canonical_url(vid)
    existing = await db.scalar(select(Source).where(Source.url == url))
    if existing is not None:
        return UploadOut(source=_out(await _get_or_404(db, existing.id)), duplicate=True)
    source = Source(
        title=f"YouTube {vid}", kind=SourceKind.video, status=SourceStatus.uploaded, url=url
    )
    db.add(source)
    await db.flush()
    from kos.library import sections

    await sections.add(db, source.id, "videos")
    if branch_ids:
        db.add_all(SourceDiscipline(source_id=source.id, discipline_id=d) for d in branch_ids)
    if body.authors:
        from kos.library import authors as auth

        try:
            ids = await auth.resolve(db, [a.model_dump() for a in body.authors], "teacher")
        except auth.AuthorError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
        await db.refresh(source, ["author_rows"])
        await auth.set_authors(db, source, ids)
    db.add(youtube.job(source.id))
    db.add(
        AuditLog(
            actor="user",
            action="source.added",
            subject_table="sources",
            subject_id=str(source.id),
            after={"youtube": vid},
        )
    )
    await db.commit()
    return UploadOut(source=_out(await _get_or_404(db, source.id)), duplicate=False)


@router.get("", response_model=SourceListOut)
async def list_sources(
    db: DbDep,
    _: UserDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: SourceStatus | None = None,
    include_archived: bool = False,
    section: Annotated[str | None, Query(max_length=40)] = None,
    discipline: uuid.UUID | None = None,
    author: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    where = []
    if author:
        from kos.models import SourceAuthor

        where.append(
            Source.id.in_(select(SourceAuthor.source_id).where(SourceAuthor.person_id == author))
        )
    if discipline:
        from kos.library.disciplines import discipline_sources

        where.append(Source.id.in_(await discipline_sources(db, discipline)))
    if section:
        from kos.models import SourceSection

        where.append(
            Source.id.in_(
                select(SourceSection.source_id).where(SourceSection.section_key == section)
            )
        )
    if status_filter is not None:
        where.append(Source.status == status_filter)
    elif not include_archived:
        where.append(Source.status != SourceStatus.archived)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append(Source.title.ilike(f"%{escaped}%", escape="\\"))
    total = await db.scalar(select(func.count()).select_from(Source).where(*where))
    rows = await db.scalars(
        select(Source)
        .where(*where)
        .options(selectinload(Source.versions))
        .order_by(Source.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list(rows)
    errors = await _errors(db, rows)
    return SourceListOut(items=[_out(r, errors.get(r.id)) for r in rows], total=total or 0)


@router.get("/{source_id}", response_model=SourceOut)
async def get_source(source_id: uuid.UUID, db: DbDep, _: UserDep):
    source = await _get_or_404(db, source_id)
    return _out(source, (await _errors(db, [source])).get(source.id))


class SourcePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    authors_text: str | None = Field(default=None, max_length=2000)
    language: str | None = Field(default=None, max_length=16)
    url: HttpUrl | None = None
    notes: str | None = Field(default=None, max_length=20000)


@router.patch("/{source_id}", response_model=SourceOut)
async def patch_source(source_id: uuid.UUID, body: SourcePatch, db: DbDep, _: UserDep):
    source = await _get_or_404(db, source_id)
    changes = body.model_dump(exclude_unset=True)
    if "url" in changes and changes["url"] is not None:
        changes["url"] = str(changes["url"])
    if changes.get("title") is None:
        changes.pop("title", None)
    await service.update_metadata(db, source, changes)
    return _out(await _get_or_404(db, source_id))


@router.post("/{source_id}/archive", response_model=SourceOut)
async def archive(source_id: uuid.UUID, db: DbDep, _: UserDep):
    await service.set_archived(db, await _get_or_404(db, source_id), True)
    return _out(await _get_or_404(db, source_id))


@router.get("/{source_id}/delete-preview")
async def delete_preview(source_id: uuid.UUID, db: DbDep, _: UserDep):
    """What deleting this source would remove (see kos.library.deletion)."""
    from kos.library.deletion import impact

    return await impact(db, await _get_or_404(db, source_id))


@router.delete("/{source_id}")
async def delete_source(source_id: uuid.UUID, db: DbDep, _: UserDep):
    """Delete an archived source for good (files and derived data)."""
    from kos.library.deletion import DeletionError, delete_source

    source = await _get_or_404(db, source_id)
    try:
        return {"deleted": True, **(await delete_source(db, get_store(), source))}
    except DeletionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None


@router.post("/{source_id}/restore", response_model=SourceOut)
async def restore(source_id: uuid.UUID, db: DbDep, _: UserDep):
    await service.set_archived(db, await _get_or_404(db, source_id), False)
    return _out(await _get_or_404(db, source_id))


@router.get("/{source_id}/versions/{version_id}/file")
async def download(source_id: uuid.UUID, version_id: uuid.UUID, db: DbDep, _: UserDep):
    """Serve the untouched original. PDFs are shown inline in a sandbox; text is
    always served as plain text so it can never run as HTML."""
    source = await _get_or_404(db, source_id)
    version = next((v for v in source.versions if v.id == version_id), None)
    if version is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    path = get_store().path_for(version.storage_key)
    if not path.is_file():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "stored file is missing")
    is_pdf = version.mime == "application/pdf"
    is_text = version.mime.startswith("text/") or version.mime == "application/x-subrip"
    if version.mime.startswith(("audio/", "video/")):
        # Played in the app's own <audio>/<video> element; byte ranges allow seeking.
        return FileResponse(
            path,
            media_type=version.mime,
            content_disposition_type="inline",
            filename=version.original_filename or version.sha256,
        )
    return FileResponse(
        path,
        media_type="application/pdf"
        if is_pdf
        else ("text/plain; charset=utf-8" if is_text else version.mime),
        filename=version.original_filename or f"{version.sha256}",
        content_disposition_type="inline" if is_pdf else "attachment",
        # Browsers refuse to render PDFs under a sandbox CSP, so only text gets it.
        headers=None if is_pdf else {"Content-Security-Policy": "sandbox; default-src 'none'"},
    )


@router.post("/{source_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
async def reprocess(source_id: uuid.UUID, db: DbDep, _: UserDep):
    source = await _get_or_404(db, source_id)
    try:
        await service.reprocess(db, source)
    except service.AlreadyProcessing:
        raise HTTPException(status.HTTP_409_CONFLICT, "ya se está procesando") from None
    except service.CitedSegments:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "la fuente tiene fragmentos citados como evidencia; reprocesarla borraría esas citas",
        ) from None
    except ValueError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "restaura la fuente antes de reprocesarla"
        ) from None
    return {"queued": True}


@router.post("/{source_id}/refetch", status_code=status.HTTP_202_ACCEPTED)
async def refetch(source_id: uuid.UUID, db: DbDep, _: UserDep):
    """YouTube: download the video's captions (or audio) again."""
    source = await _get_or_404(db, source_id)
    try:
        await service.refetch_youtube(db, source)
    except service.AlreadyProcessing:
        raise HTTPException(status.HTTP_409_CONFLICT, "ya se está procesando") from None
    except service.CitedSegments:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "hay conocimiento que cita esta transcripción; volver a descargarla borraría esas "
            "citas (rechaza antes esas afirmaciones si son de la transcripción errónea)",
        ) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    return {"queued": True}


class PageOut(BaseModel):
    page_no: int
    text: str


@router.get("/{source_id}/versions/{version_id}/pages/{page_no}", response_model=PageOut)
async def get_page(
    source_id: uuid.UUID, version_id: uuid.UUID, page_no: int, db: DbDep, _: UserDep
):
    """Extracted text of one page, as stored (segment offsets point into it)."""
    page = await db.scalar(
        select(SourcePage)
        .join(SourceVersion, SourceVersion.id == SourcePage.source_version_id)
        .where(
            SourceVersion.source_id == source_id,
            SourcePage.source_version_id == version_id,
            SourcePage.page_no == page_no,
        )
    )
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")
    return PageOut(page_no=page.page_no, text=page.text)


@router.get("/{source_id}/versions/{version_id}/transcript")
async def transcript(source_id: uuid.UUID, version_id: uuid.UUID, db: DbDep, _: UserDep):
    """Timed lines of an audio/video/subtitle version, for the player."""
    source = await _get_or_404(db, source_id)
    if not any(v.id == version_id for v in source.versions):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    segs = (
        await db.scalars(
            select(SourceSegment)
            .where(SourceSegment.source_version_id == version_id)
            .order_by(SourceSegment.ordinal)
        )
    ).all()
    lines = []
    for seg in segs:
        tl = seg.loc_timeline or []
        for i, (off, ms) in enumerate(tl):
            end = tl[i + 1][0] if i + 1 < len(tl) else len(seg.text)
            text = seg.text[off:end].strip()
            if text and not (lines and lines[-1]["t_ms"] == ms and lines[-1]["text"] == text):
                lines.append({"t_ms": ms, "text": text, "segment_id": str(seg.id)})
    return lines


class DisciplinesIn(BaseModel):
    discipline_ids: list[str] = Field(max_length=20)


@router.put("/{source_id}/disciplines", response_model=SourceOut)
async def set_source_disciplines(source_id: uuid.UUID, body: DisciplinesIn, db: DbDep, _: UserDep):
    """Branches of a source (Nutrición, Biomecánica...): replaces the current ones."""
    from kos.library import disciplines as disc

    source = await _get_or_404(db, source_id)
    try:
        ids = await disc.parse_ids(db, body.discipline_ids)
    except disc.DisciplineError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    await disc.set_disciplines(db, source, ids)
    await db.commit()
    return _out(await _get_or_404(db, source_id))


class AuthorsIn(BaseModel):
    authors: list[AuthorRef] = Field(max_length=50)


@router.put("/{source_id}/authors", response_model=SourceOut)
async def set_source_authors(source_id: uuid.UUID, body: AuthorsIn, db: DbDep, _: UserDep):
    """Authors of a source, in order: existing people by id, new ones by name (a name
    that already exists is reused). Replaces the current ones."""
    from kos.library import authors as auth

    source = await _get_or_404(db, source_id)
    try:
        ids = await auth.resolve(
            db,
            [a.model_dump() for a in body.authors],
            _role_for(source.section_rows[0].section_key if source.section_rows else None),
        )
    except auth.AuthorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    await auth.set_authors(db, source, ids)
    await db.commit()
    return _out(await _get_or_404(db, source_id))
