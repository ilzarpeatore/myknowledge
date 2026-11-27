"""Phase 5: audio/video transcription. The speech model is replaced by a fake whose
output depends on the section it is asked for, so the tests check sectioning,
saving and resuming; real transcription quality is measured separately (D-18)."""

import uuid

import numpy as np
import pytest
from sqlalchemy import func, select

from kos.ingestion import pipeline
from kos.knowledge import service as ks
from kos.media import transcribe as tr
from kos.media import youtube
from kos.models import Job, SourceSegment, SourceVersion, TranscriptChunk

from .test_knowledge import entity, run_jobs

MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 200  # only the signature matters here
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 200


class FakeSpeech:
    """10-minute sections; one sentence every 30 s, saying its own minute."""

    def __init__(self, total_ms=25 * 60_000, crash_at_call=None):
        self.total_ms, self.calls, self.crash_at_call = total_ms, [], crash_at_call
        self.languages = []

    def section(self, path, start_ms, total_ms, language=None):
        self.calls.append(start_ms)
        self.languages.append(language)
        if self.crash_at_call is not None and len(self.calls) == self.crash_at_call:
            raise RuntimeError("worker killed")
        end = min(start_ms + 600_000 - 3_000, total_ms)  # cut a bit early, as at a pause
        if total_ms - end < 60_000:
            end = total_ms
        cues = [
            [t, t + 4000, f"En el segundo {t // 1000} se habla de fatiga."]
            for t in range(start_ms + 1000, end - 4000, 30_000)
        ]
        return tr.Section(start_ms, end, cues, "es")


@pytest.fixture
def speech(monkeypatch):
    fake = FakeSpeech()
    monkeypatch.setattr(tr, "available", lambda: True)
    monkeypatch.setattr(tr, "probe_duration_ms", lambda path: fake.total_ms)
    monkeypatch.setattr(tr, "transcribe_section", lambda *a: fake.section(*a))
    return fake


async def upload(client, name, data):
    r = await client.post("/api/sources", files={"file": (name, data)})
    assert r.status_code in (200, 201), r.text
    return r.json()["source"]


def test_audio_and_video_are_recognised_by_content():
    from kos.library.service import _detect_media

    assert _detect_media(MP3).mime == "audio/mpeg"
    assert _detect_media(MP4).mime == "video/mp4" and _detect_media(MP4).kind == "video"
    assert _detect_media(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 8).mime == "audio/mp4"
    assert _detect_media(b"%PDF-1.7") is None


def test_sections_are_cut_at_the_quietest_moment():
    sr = tr.SAMPLE_RATE
    audio = np.random.default_rng(0).uniform(-0.5, 0.5, 60 * sr).astype(np.float32)
    audio[50 * sr : int(50.6 * sr)] = 0  # a pause 10 s before the nominal end
    cut = tr.quiet_cut(audio, 60)
    assert 50 * sr <= cut <= int(50.6 * sr)


async def test_transcription_is_saved_by_section_and_resumes(auth_client, speech, db):
    speech.crash_at_call = 3  # dies while transcribing the third section
    src = await upload(auth_client, "clase.mp3", MP3)
    assert src["kind"] == "audio"
    await run_jobs()  # first attempt fails (retryable), the retry resumes
    assert speech.calls == [0, 597_000, 1_194_000, 1_194_000]  # third section redone once
    assert await db.scalar(select(func.count()).select_from(TranscriptChunk)) == 3

    detail = (await auth_client.get(f"/api/sources/{src['id']}")).json()
    assert detail["status"] == "ready"
    info = detail["versions"][0]["extraction_info"]
    assert info["sections"] == 3 and info["resumed_sections"] == 2
    assert detail["versions"][0]["extractor"] == "faster-whisper"

    # a quote is located to its own minute
    seg = await db.scalar(
        select(SourceSegment).where(SourceSegment.text.contains("En el segundo 1225 se habla"))
    )
    rir = await entity(auth_client, "Fatiga")
    await ks.add_claim(
        db,
        text_="Se habla de fatiga",
        claim_type="reported_fact",
        entities=[(uuid.UUID(rir["id"]), "subject")],
        evidence=[(seg.id, "En el segundo 1225 se habla de fatiga.")],
    )
    ev = (await auth_client.get(f"/api/entities/{rir['slug']}")).json()["claims"][0]["evidence"][0]
    assert ev["t_ms"] == 1_225_000 and ev["mime"] == "audio/mpeg"


async def test_reprocessing_reuses_finished_sections(auth_client, speech, db):
    src = await upload(auth_client, "clase.mp4", MP4)
    await run_jobs()
    first = len(speech.calls)
    r = await auth_client.post(f"/api/sources/{src['id']}/reprocess")
    assert r.status_code == 202
    await run_jobs()
    assert len(speech.calls) == first  # nothing transcribed again


async def test_without_the_speech_model_the_source_fails_with_a_clear_reason(
    auth_client, monkeypatch, db
):
    monkeypatch.setattr(tr, "available", lambda: False)
    src = await upload(auth_client, "clase.mp3", MP3)
    await run_jobs()
    assert (await auth_client.get(f"/api/sources/{src['id']}")).json()["status"] == "failed"
    job = await db.scalar(select(Job).where(Job.kind == pipeline.EXTRACT_KIND))
    assert "imagen worker" in job.last_error


async def test_media_is_served_inline_with_byte_ranges(auth_client, speech, db):
    src = await upload(auth_client, "clase.mp4", MP4)
    vid = await db.scalar(select(SourceVersion.id))
    r = await auth_client.get(
        f"/api/sources/{src['id']}/versions/{vid}/file", headers={"Range": "bytes=0-9"}
    )
    assert r.status_code == 206 and len(r.content) == 10
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["content-disposition"].startswith("inline")


def test_language_codes_for_the_speech_model():
    assert tr.whisper_code("en-US") == "en" and tr.whisper_code("es-419") == "es"
    assert tr.whisper_code("EN") == "en" and tr.whisper_code("") is None
    assert tr.whisper_code(None) is None and tr.whisper_code("xx-YY") is None


async def test_youtube_audio_is_transcribed_in_the_videos_own_language(
    auth_client, speech, monkeypatch
):
    """No subtitles: the audio of an English video is transcribed as English, whatever
    the configured language; an uploaded file without a language is detected."""

    def fetch(vid, workdir):
        p = workdir / f"youtube-{vid}.m4a"
        p.write_bytes(MP3)
        return youtube.Fetched(p, p.name, "PRUEBA - English lecture", "Canal", "en-US", "audio")

    monkeypatch.setattr(youtube, "fetch", fetch)
    r = await auth_client.post("/api/sources/youtube", json={"url": "https://youtu.be/jNQXAC9IVRw"})
    assert r.status_code == 200, r.text
    await run_jobs()
    assert set(speech.languages) == {"en-US"}  # passed on; whisper_code makes it "en"

    speech.languages.clear()
    await upload(auth_client, "PRUEBA clase.mp3", MP3 + b"\x01")  # not a duplicate
    await run_jobs()
    assert set(speech.languages) == {None}  # nothing known: the model detects it
