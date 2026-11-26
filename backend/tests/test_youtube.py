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
