"""YouTube videos as sources.

The original kept for a YouTube source is what the video says, in this order:
1. subtitles written by its author, in the video's language (or Spanish/English);
2. YouTube's automatic captions in the video's original language (never the
   machine translations into other languages: they are not what was said);
3. otherwise the audio track only, transcribed locally like any recording.
The file is stored immutable, as any upload, and processed by the normal
pipeline. Citations link to the video at the cited second.

Network: YouTube often blocks downloads from datacenter IPs ("confirm you're not
a bot"); the error says so, and the user can then upload the subtitles or audio
downloaded on their own computer instead.
"""

import asyncio
import hashlib
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from kos.config import get_settings
from kos.jobs.worker import JobContext, PermanentJobError, handler
from kos.library.blobstore import StagedFile, get_store
from kos.models import AuditLog, Job, JobStatus, Source, SourceStatus, SourceVersion

FETCH_KIND = "source.youtube"
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtube-nocookie.com",
    "youtube-nocookie.com",
}


class YouTubeError(Exception):
    pass


def video_id(url: str) -> str:
    """The 11-character id of a YouTube video URL (watch, youtu.be, shorts, embed, live)."""
    u = urlparse(url.strip() if "://" in url else f"https://{url.strip()}")
    host = (u.hostname or "").lower()
    if host not in HOSTS:
        raise YouTubeError("no es un enlace de YouTube")
    if host == "youtu.be":
        candidate = u.path.strip("/").split("/")[0]
    elif u.path == "/watch":
        candidate = (parse_qs(u.query).get("v") or [""])[0]
    else:
        parts = u.path.strip("/").split("/")
        candidate = (
            parts[1] if len(parts) > 1 and parts[0] in ("shorts", "embed", "live", "v") else ""
        )
    if not VIDEO_ID.match(candidate):
        raise YouTubeError("no se encuentra el identificador del vídeo en el enlace")
    return candidate


def canonical_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def job(source_id: uuid.UUID) -> Job:
    return Job(
        kind=FETCH_KIND,
        status=JobStatus.pending,
        max_attempts=3,
        payload={"source_id": str(source_id)},
    )


def _base(code: str | None) -> str | None:
    return (code or "").split("-")[0].lower() or None


def original_language(info: dict[str, Any]) -> str | None:
    """The language the video was recorded in, or None if it cannot be told for sure.

    Videos with YouTube's automatic dubbing carry audio tracks in many languages and
    machine-translated captions in all of them; their top-level ``language`` can be a
    dubbed one (seen: a Spanish video reported as Arabic). Trust, in order: the
    caption track YouTube marks as original («-orig»), the audio track marked as
    original, and ``language`` only when the video has a single audio language."""
    auto = info.get("automatic_captions") or {}
    for key in auto:
        if key.endswith("-orig"):
            return _base(key.removesuffix("-orig"))
    audio = [
        f
        for f in info.get("formats") or []
        if f.get("acodec") not in (None, "none") and f.get("language")
    ]
    for f in audio:
        note = f"{f.get('format_note') or ''} {f.get('format') or ''}".lower()
        if "original" in note or (f.get("language_preference") or 0) >= 10:
            return _base(f["language"])
    languages = {_base(f["language"]) for f in audio}
    if len(languages) > 1:
        return None  # dubbed and no track marked as original: do not guess
    return _base(info.get("language")) or (languages.pop() if languages else None)


LATIN_LANGS = {"es", "en", "pt", "fr", "it", "de", "ca", "gl", "eu", "nl", "pl", "ro", "sv"}


def text_matches(lang: str | None, text: str) -> bool:
    """A cheap guard against translated captions: a language written in the Latin
    alphabet must come mostly in Latin letters (not Arabic, Cyrillic, CJK...)."""
    if not lang or _base(lang) not in LATIN_LANGS:
        return True
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 50:
        return True
    latin = sum(1 for c in letters if c.isascii() or "\u00c0" <= c <= "\u024f")
    return latin / len(letters) >= 0.8


def choose_captions(info: dict[str, Any], preferred: list[str]) -> tuple[str, str, str] | None:
    """(language, 'manual'|'auto', vtt url) or None. Automatic captions only in the
    video's original language, and only when that language is known for sure."""

    def vtt(formats):
        return next(
            (f["url"] for f in formats or [] if f.get("ext") == "vtt" and f.get("url")), None
        )

    lang = original_language(info)
    order = [x for x in [lang, *preferred] if x]
    manual = info.get("subtitles") or {}
    for code in order:
        for key in (code, *[k for k in manual if k.split("-")[0] == code]):
            if key in manual and (url := vtt(manual[key])):
                return key, "manual", url
    if lang is None:
        return None  # unknown original language: transcribe the original audio instead
    auto = info.get("automatic_captions") or {}
    originals = [k for k in auto if k.endswith("-orig") and _base(k.removesuffix("-orig")) == lang]
    if lang in auto:
        originals.append(lang)
    for key in originals:
        if url := vtt(auto[key]):
            return key.removesuffix("-orig"), "auto", url
    return None


@dataclass
class Fetched:
    path: Path
    filename: str
    title: str | None
    channel: str | None
    language: str | None
    origin: str  # manual subtitles | auto captions | audio


def _explain(exc: Exception) -> str:
    msg = str(exc)
    if "confirm you" in msg.lower() or "sign in" in msg.lower():
        return (
            "YouTube bloquea la descarga desde este servidor (pide confirmar que no es un bot). "
            "Descarga los subtítulos o el audio en tu ordenador y súbelos a la biblioteca."
        )
    if "private" in msg.lower() or "unavailable" in msg.lower():
        return "el vídeo no está disponible (privado, borrado o restringido)"
    return f"no se pudo leer el vídeo de YouTube: {msg[-300:]}"


def fetch(vid: str, workdir: Path) -> Fetched:
    """Blocking: metadata, then subtitles or audio, into ``workdir``."""
    import yt_dlp

    s = get_settings()
    preferred = [x for x in [s.whisper_language, "es", "en"] if x]
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 30,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(canonical_url(vid), download=False)
            if info.get("is_live"):
                raise YouTubeError("es una emisión en directo; impórtalo cuando termine")
            meta = {
                "title": info.get("title"),
                "channel": info.get("channel") or info.get("uploader"),
            }
            original = original_language(info)
            chosen = choose_captions(info, preferred)
            data = y.urlopen(chosen[2]).read() if chosen else b""
            if chosen and not text_matches(original or chosen[0], data.decode("utf-8", "replace")):
                chosen = None  # captions in another script: a translation, not what was said
            if chosen:
                lang, kind, url = chosen
                path = workdir / f"youtube-{vid}.{lang}.vtt"
                path.write_bytes(data)
                return Fetched(
                    path,
                    path.name,
                    meta["title"],
                    meta["channel"],
                    lang,
                    "subtítulos del autor" if kind == "manual" else "subtítulos automáticos",
                )
        audio_opts = {
            **opts,
            "skip_download": False,
            # the original audio, never a dubbed track
            "format": (
                "bestaudio[format_note*=original][ext=m4a]/bestaudio[format_note*=original]/"
                + (f"bestaudio[language^={original}][ext=m4a]/" if original else "")
                + "bestaudio[ext=m4a]/bestaudio"
            ),
            "outtmpl": str(workdir / f"youtube-{vid}.%(ext)s"),
            "max_filesize": s.max_upload_mb * 1024 * 1024,
        }
        with yt_dlp.YoutubeDL(audio_opts) as y:
            info = y.extract_info(canonical_url(vid), download=True)
        files = [p for p in workdir.iterdir() if p.name.startswith(f"youtube-{vid}.")]
        if not files:
            raise YouTubeError("el audio supera el tamaño máximo o no se pudo descargar")
        return Fetched(
            files[0],
            files[0].name,
            meta["title"],
            meta["channel"],
            original,  # None: the speech model detects it
            "audio (se transcribe)",
        )
    except YouTubeError:
        raise
    except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types; all mean "could not fetch"
        raise YouTubeError(_explain(exc)) from None


def _stage(path: Path) -> StagedFile:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as f:
        head = f.read(4096)
        f.seek(0)
        while chunk := f.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return StagedFile(path=path, sha256=digest.hexdigest(), size=size, head=head)


@handler(FETCH_KIND)
async def fetch_youtube(ctx: JobContext) -> dict[str, Any]:
    from kos.ingestion.pipeline import extract_job
    from kos.library.service import UnsupportedFile, detect_format

    try:
        source_id = uuid.UUID(ctx.payload["source_id"])
    except (KeyError, ValueError):
        raise PermanentJobError("payload no válido") from None
    async with ctx.sessionmaker() as db:
        source = await db.get(Source, source_id)
        if source is None:
            raise PermanentJobError("fuente no encontrada")
        refetch = bool(ctx.payload.get("refetch"))
        if not refetch and await db.scalar(
            select(SourceVersion.id).where(SourceVersion.source_id == source_id)
        ):
            return {"skipped": "ya importado"}
        source.status = SourceStatus.processing
        await db.commit()
        vid = video_id(source.url or "")

    store = get_store()
    tmp_root = store.root / "tmp"  # same filesystem as the store: the final move is a rename
    tmp_root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(dir=tmp_root))
    try:
        try:
            got = await asyncio.to_thread(fetch, vid, workdir)
        except YouTubeError as exc:
            await _fail(ctx, source_id, str(exc))
            raise PermanentJobError(str(exc)) from None
        staged = await asyncio.to_thread(_stage, got.path)
        try:
            fmt = await asyncio.to_thread(detect_format, staged, got.filename)
        except UnsupportedFile as exc:
            await _fail(ctx, source_id, str(exc))
            raise PermanentJobError(str(exc)) from None
        async with ctx.sessionmaker() as db:
            dup = await db.scalar(
                select(SourceVersion).where(SourceVersion.sha256 == staged.sha256)
            )
            if dup is not None and refetch and dup.source_id == source_id:
                # the same file again: just extract it again (with today's parser)
                db.add(extract_job(dup.id))
                await db.commit()
                return {"video": vid, "stored": got.origin, "same_file": True}
            if dup is not None:
                msg = "ese contenido ya está en la biblioteca"
                await _fail(ctx, source_id, msg)
                raise PermanentJobError(msg)
            key = await asyncio.to_thread(store.commit, staged)
            source = await db.get(Source, source_id)
            if source.title.startswith("YouTube ") and got.title:
                source.title = got.title[:500]
            source.authors_text = source.authors_text or got.channel
            source.language = got.language if refetch else (source.language or got.language)
            version = SourceVersion(
                source_id=source_id,
                sha256=staged.sha256,
                storage_key=key,
                original_filename=got.filename,
                mime=fmt.mime,
                size_bytes=staged.size,
            )
            db.add(version)
            await db.flush()
            db.add(extract_job(version.id))
            db.add(
                AuditLog(
                    actor="worker",
                    action="source.youtube_fetched",
                    subject_table="sources",
                    subject_id=str(source_id),
                    after={"video": vid, "stored": got.origin, "mime": fmt.mime},
                )
            )
            await db.commit()
        return {"video": vid, "stored": got.origin}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _fail(ctx: JobContext, source_id: uuid.UUID, reason: str) -> None:
    async with ctx.sessionmaker() as db:
        source = await db.get(Source, source_id)
        source.status = SourceStatus.failed
        db.add(
            AuditLog(
                actor="worker",
                action="source.youtube_failed",
                subject_table="sources",
                subject_id=str(source_id),
                after={"reason": reason},
            )
        )
        await db.commit()
