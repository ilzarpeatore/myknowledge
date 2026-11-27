# ruff: noqa: E501  (test data on one line)
"""Phase 5: YouTube videos. yt-dlp is never called: ``youtube.fetch`` is replaced,
and caption selection is tested on metadata shaped like yt-dlp's (checked against a
real response in development, see D-18)."""

import pytest
from sqlalchemy import select

from kos.media import youtube
from kos.models import Source

from .docgen import VTT
from .test_knowledge import run_jobs

URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def test_video_ids_from_every_link_shape():
    for u in (
        URL,
        "https://youtu.be/jNQXAC9IVRw?si=x",
        "youtube.com/shorts/jNQXAC9IVRw",
        "https://m.youtube.com/watch?v=jNQXAC9IVRw&t=10s",
        "https://www.youtube.com/embed/jNQXAC9IVRw",
        "https://www.youtube.com/live/jNQXAC9IVRw",
    ):
        assert youtube.video_id(u) == "jNQXAC9IVRw", u
    for bad in (
        "https://vimeo.com/123",
        "https://www.youtube.com/watch?v=short",
        "https://evil.example/watch?v=jNQXAC9IVRw",
        "https://www.youtube.com/@canal",
    ):
        with pytest.raises(youtube.YouTubeError):
            youtube.video_id(bad)


def _fmt(url):
    return [{"ext": "json3", "url": url + ".json"}, {"ext": "vtt", "url": url}]


def test_caption_choice_prefers_author_subtitles_then_original_auto_never_translations():
    info = {
        "language": "es",
        "subtitles": {"en": _fmt("m-en"), "es-ES": _fmt("m-es")},
        "automatic_captions": {"es-orig": _fmt("a-es")},
    }
    assert youtube.choose_captions(info, ["es", "en"]) == ("es-ES", "manual", "m-es")
    info = {
        "language": "es",
        "subtitles": {},
        "automatic_captions": {
            "en-es": _fmt("translated"),
            "es-orig": _fmt("a-es"),
            "fr-es": _fmt("t2"),
        },
    }
    assert youtube.choose_captions(info, ["es"]) == ("es", "auto", "a-es")
    # only machine translations into other languages: not usable -> audio instead
    info = {"language": None, "subtitles": {}, "automatic_captions": {"es-en": _fmt("t")}}
    assert youtube.choose_captions(info, ["es", "en"]) is None


def _audio(fid, lang, original=False):
    note = f"{lang} original (default), low" if original else f"{lang}, low"
    return {
        "format_id": fid,
        "acodec": "mp4a",
        "language": lang,
        "language_preference": 10 if original else -1,
        "format_note": note,
        "format": f"{fid} - audio only ({note})",
    }


def test_dubbed_video_with_every_caption_marked_orig_keeps_the_original_audio_language():
    """Real shape of ECFdmVTMTxE (2026-11-27): a Spanish video auto-dubbed into four
    languages, with «-orig» captions for all five and Arabic listed first. It was
    imported twice in Arabic."""
    langs = ["ar", "en-US", "pt-BR", "ru"]
    auto = {f"{b}-orig": _fmt(f"a-{b}") for b in ("ar", "en", "pt", "ru", "es")}
    auto.update({"ar": _fmt("t-ar"), "es": _fmt("a-es")})
    info = {
        "language": "es-US",
        "subtitles": {},
        "automatic_captions": auto,
        "formats": [_audio(f"139-{i}", lang) for i, lang in enumerate(langs)]
        + [_audio("139-4", "es-US", original=True)],
    }
    assert youtube.original_language(info) == "es"
    assert youtube.choose_captions(info, ["es", "en"]) == ("es", "auto", "a-es")
    # the same, without any audio track marked as original: do not guess
    info["formats"] = [_audio(f"139-{i}", lang) for i, lang in enumerate([*langs, "es-US"])]
    assert youtube.original_language(info) is None
    assert youtube.choose_captions(info, ["es", "en"]) is None


@pytest.fixture
def fake_fetch(monkeypatch, tmp_path):
    calls = []

    def fetch(vid, workdir):
        calls.append(vid)
        p = workdir / f"youtube-{vid}.es.vtt"
        p.write_text(VTT)
        return youtube.Fetched(
            p, p.name, "PRUEBA - Clase en YouTube", "Canal de prueba", "es", "subtítulos del autor"
        )

    monkeypatch.setattr(youtube, "fetch", fetch)
    return calls


async def test_youtube_video_becomes_a_timed_source(auth_client, fake_fetch, db):
    r = await auth_client.post("/api/sources/youtube", json={"url": "https://youtu.be/jNQXAC9IVRw"})
    assert r.status_code == 200 and r.json()["duplicate"] is False
    sid = r.json()["source"]["id"]
    await run_jobs()
    src = (await auth_client.get(f"/api/sources/{sid}")).json()
    assert src["status"] == "ready" and src["kind"] == "video" and src["url"] == URL
    assert src["title"] == "PRUEBA - Clase en YouTube" and src["authors_text"] == "Canal de prueba"
    assert src["versions"][0]["mime"] == "text/vtt"
    lines = (
        await auth_client.get(f"/api/sources/{sid}/versions/{src['versions'][0]['id']}/transcript")
    ).json()
    assert lines[-1] == {**lines[-1], "t_ms": 3723250}

    again = await auth_client.post("/api/sources/youtube", json={"url": URL + "&t=5s"})
    assert again.json()["duplicate"] is True and fake_fetch == ["jNQXAC9IVRw"]


async def test_blocked_download_fails_with_the_reason_and_can_be_retried(
    auth_client, monkeypatch, db, fake_fetch
):
    def blocked(vid, workdir):
        raise youtube.YouTubeError(
            youtube._explain(Exception("Sign in to confirm you're not a bot"))
        )

    monkeypatch.setattr(youtube, "fetch", blocked)
    sid = (await auth_client.post("/api/sources/youtube", json={"url": URL})).json()["source"]["id"]
    await run_jobs()
    src = (await auth_client.get(f"/api/sources/{sid}")).json()
    assert src["status"] == "failed" and "bloquea la descarga" in src["error"]
    listed = (await auth_client.get("/api/sources")).json()["items"][0]
    assert listed["error"] == src["error"]

    monkeypatch.setattr(youtube, "fetch", fake_fetch_fn(monkeypatch))
    r = await auth_client.post(f"/api/sources/{sid}/reprocess")
    assert r.status_code == 202
    await run_jobs()
    src = (await auth_client.get(f"/api/sources/{sid}")).json()
    assert src["status"] == "ready" and src["error"] is None


def fake_fetch_fn(monkeypatch):
    def fetch(vid, workdir):
        p = workdir / f"youtube-{vid}.es.vtt"
        p.write_text(VTT.replace("proximidad", "cercanía"))
        return youtube.Fetched(p, p.name, "Otra", "Canal", "es", "subtítulos del autor")

    return fetch


async def test_not_youtube_links_are_refused(auth_client, db):
    r = await auth_client.post("/api/sources/youtube", json={"url": "https://vimeo.com/1"})
    assert r.status_code == 400
    assert await db.scalar(select(Source.id)) is None


# ---- dubbed videos and translated captions (a Spanish video arrived in Arabic) -------

DUBBED = {
    "language": "ar",  # what YouTube reported for a Spanish video with automatic dubbing
    "subtitles": {},
    "automatic_captions": {
        "ar": [{"ext": "vtt", "url": "https://x/ar"}],
        "es": [{"ext": "vtt", "url": "https://x/es"}],
        "en": [{"ext": "vtt", "url": "https://x/en"}],
    },
    "formats": [
        {"acodec": "mp4a", "language": "es", "format_note": "Spanish original (default), medium"},
        {"acodec": "mp4a", "language": "ar", "format_note": "Arabic, medium"},
        {"vcodec": "avc1", "acodec": "none"},
    ],
}


def test_dubbed_video_uses_the_original_language():
    assert youtube.original_language(DUBBED) == "es"
    assert youtube.choose_captions(DUBBED, ["es", "en"]) == ("es", "auto", "https://x/es")
    # a track YouTube marks as original wins over everything
    marked = {
        **DUBBED,
        "automatic_captions": {
            **DUBBED["automatic_captions"],
            "es-orig": [{"ext": "vtt", "url": "https://x/orig"}],
        },
    }
    assert youtube.choose_captions(marked, ["en"]) == ("es", "auto", "https://x/orig")


def test_unknown_original_language_transcribes_the_audio():
    unmarked = {
        **DUBBED,
        "formats": [
            {"acodec": "mp4a", "language": "es", "format_note": "medium"},
            {"acodec": "mp4a", "language": "ar", "format_note": "medium"},
        ],
    }
    assert youtube.original_language(unmarked) is None
    assert youtube.choose_captions(unmarked, ["es", "en"]) is None  # never a translation
    # single audio language: the reported language is trusted
    single = {**DUBBED, "language": "es", "formats": [{"acodec": "mp4a", "language": "es"}]}
    assert youtube.original_language(single) == "es"


def test_captions_in_another_script_are_rejected():
    arabic = "طبيب ومدرب. لقد ساعدت أكثر من 4000 شخص على تحقيق أقصى إمكاناتهم البدنية بشكل طبيعي"
    spanish = "Médico y entrenador. He ayudado a más de 4000 personas a alcanzar su máximo potencial físico"
    assert youtube.text_matches("es", arabic) is False
    assert youtube.text_matches("es", spanish) is True
    assert youtube.text_matches("ar", arabic) is True  # not a Latin-script language: no guard


ROLLING_VTT = """WEBVTT
Kind: captions
Language: es

00:00:00.000 --> 00:00:02.500
Médico y entrenador. He ayudado a

00:00:02.500 --> 00:00:02.510
Médico y entrenador. He ayudado a

00:00:02.510 --> 00:00:05.000
Médico y entrenador. He ayudado a
más de 4000 personas a alcanzar

00:00:05.000 --> 00:00:05.010
más de 4000 personas a alcanzar

00:00:05.010 --> 00:00:08.000
más de 4000 personas a alcanzar
su máximo potencial físico.
"""


def test_rolling_automatic_captions_keep_each_line_once():
    from kos.ingestion.subtitles import parse_cues

    cues = parse_cues(ROLLING_VTT)
    assert [c[2] for c in cues] == [
        "Médico y entrenador. He ayudado a",
        "más de 4000 personas a alcanzar",
        "su máximo potencial físico.",
    ]
    assert [c[0] for c in cues] == [0, 2510, 5010]  # each line at the time it appears


async def test_refetch_replaces_a_wrong_transcript(auth_client, monkeypatch, db):
    from kos.models import SourceSegment

    def arabic(vid, workdir):
        p = workdir / f"youtube-{vid}.ar.vtt"
        p.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nطبيب ومدرب\n", encoding="utf-8")
        return youtube.Fetched(p, p.name, "PRUEBA vídeo", "Canal", "ar", "subtítulos automáticos")

    monkeypatch.setattr(youtube, "fetch", arabic)
    sid = (await auth_client.post("/api/sources/youtube", json={"url": URL})).json()["source"]["id"]
    await run_jobs()

    def spanish(vid, workdir):
        p = workdir / f"youtube-{vid}.es.vtt"
        p.write_text(ROLLING_VTT, encoding="utf-8")
        return youtube.Fetched(p, p.name, "PRUEBA vídeo", "Canal", "es", "subtítulos automáticos")

    monkeypatch.setattr(youtube, "fetch", spanish)
    r = await auth_client.post(f"/api/sources/{sid}/refetch")
    assert r.status_code == 202, r.text
    await run_jobs()
    src = (await auth_client.get(f"/api/sources/{sid}")).json()
    assert src["status"] == "ready" and src["language"] == "es" and len(src["versions"]) == 2
    texts = (await db.scalars(select(SourceSegment.text))).all()
    assert texts and all("طبيب" not in t for t in texts)  # only the new transcript is searchable
    assert any("máximo potencial" in t for t in texts)
    # not a YouTube source: refused
    other = await auth_client.post("/api/sources", files={"file": ("PRUEBA.md", b"# x\n\ny")})
    r = await auth_client.post(f"/api/sources/{other.json()['source']['id']}/refetch")
    assert r.status_code == 409 and "YouTube" in r.json()["detail"]
