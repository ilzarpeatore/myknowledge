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
from kos.models import Source, SourcePage, SourceStatus, SourceVersion

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
    created_at: datetime
    versions: list[VersionOut]


class UploadOut(BaseModel):
    source: SourceOut
    duplicate: bool


class SourceListOut(BaseModel):
    items: list[SourceOut]
    total: int


def _out(source: Source) -> SourceOut:
    return SourceOut.model_validate(source, from_attributes=True)


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
):
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
        result = await service.add_upload(db, store, staged, file.filename, title)
    except service.UnsupportedFile as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from None
    return UploadOut(source=_out(result.source), duplicate=result.duplicate)


@router.get("", response_model=SourceListOut)
async def list_sources(
    db: DbDep,
    _: UserDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: SourceStatus | None = None,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    where = []
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
    return SourceListOut(items=[_out(r) for r in rows], total=total or 0)


@router.get("/{source_id}", response_model=SourceOut)
async def get_source(source_id: uuid.UUID, db: DbDep, _: UserDep):
    return _out(await _get_or_404(db, source_id))


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
    return FileResponse(
        path,
        media_type="application/pdf" if is_pdf else "text/plain; charset=utf-8",
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
    except ValueError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "restaura la fuente antes de reprocesarla"
        ) from None
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
