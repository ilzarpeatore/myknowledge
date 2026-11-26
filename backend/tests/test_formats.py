"""Phase 5: DOCX, PPTX, EPUB and SRT/VTT subtitles, uploaded and cited with their
location (section, slide, minute). Inputs are synthetic, generated in tests/docgen.py."""

import io
import uuid
import zipfile

import pytest
from sqlalchemy import select

from kos.ingestion import docling_extract
from kos.ingestion.segment import segment_document, time_at
from kos.ingestion.subtitles import build_transcript, parse_cues
from kos.knowledge import service as ks
from kos.models import SourceSegment, SourceVersion

from .docgen import SRT, VTT, make_docx, make_epub, make_pptx
from .test_knowledge import entity, run_jobs

needs_docling = pytest.mark.skipif(not docling_extract.available(), reason="docling not installed")


async def upload(client, name, data):
    r = await client.post("/api/sources", files={"file": (name, data)})
    assert r.status_code in (200, 201), r.text
    await run_jobs()
    src = (await client.get(f"/api/sources/{r.json()['source']['id']}")).json()
    return src


async def segments(db, source_id):
    return (
        await db.scalars(
            select(SourceSegment)
            .join(SourceVersion)
            .where(SourceVersion.source_id == uuid.UUID(source_id))
            .order_by(SourceSegment.ordinal)
        )
    ).all()


# ---- subtitles ----------------------------------------------------------------------


def test_srt_and_vtt_give_the_same_cues():
    vtt, srt = parse_cues(VTT), parse_cues(SRT)
    assert vtt == srt
    assert vtt[0] == (1000, 4500, "Hoy hablamos de la proximidad al fallo.")
    assert vtt[2] == (
        3723250,
        3727000,
        "Dejar tres repeticiones en reserva es suficiente.",
    )  # tags gone


def test_autocaption_repeats_are_merged():
    cues = parse_cues("WEBVTT\n\n00:01.000 --> 00:02.000\nhola\n\n00:02.000 --> 00:03.000\nhola\n")
    assert cues == [(1000, 3000, "hola")]


def test_every_quote_maps_to_its_own_cue_time():
    cues = [(i * 7000, i * 7000 + 6500, f"Frase número {i} del ponente.") for i in range(120)]
    unit = build_transcript(cues)
    segs = segment_document(type("D", (), {"units": [unit]})(), markdown=False)
    assert len(segs) > 1  # minute groups become separate paragraphs/segments
    for i, (start, _end, body) in enumerate(cues):
        seg = next(s for s in segs if body in s.text)
        assert time_at(seg.timeline, seg.text.index(body)) == start, i
        assert seg.t_start_ms <= start <= seg.t_end_ms


async def test_subtitle_upload_is_cited_at_the_minute(auth_client, db):
    src = await upload(auth_client, "clase.srt", SRT.encode())
    assert src["status"] == "ready" and src["versions"][0]["mime"] == "application/x-subrip"
    seg = (await segments(db, src["id"]))[-1]
    rir = await entity(auth_client, "RIR")
    quote = "Dejar tres repeticiones en reserva es suficiente."
    await ks.add_claim(
        db,
        text_="3 RIR basta",
        claim_type="recommendation",
        entities=[(uuid.UUID(rir["id"]), "subject")],
        evidence=[(seg.id, quote)],
    )
    detail = (await auth_client.get(f"/api/entities/{rir['slug']}")).json()
    ev = detail["claims"][0]["evidence"][0]
    assert ev["t_ms"] == 3723250 and ev["page"] is None  # 1:02:03, exact cue
    # search results carry the time too
    hits = (await auth_client.get("/api/search?q=repeticiones+reserva&mode=keyword")).json()
    assert hits[0]["t_ms"] is not None


async def test_subtitles_without_cues_fail_cleanly(auth_client):
    src = await upload(auth_client, "vacio.vtt", b"WEBVTT\n\nsolo texto sin tiempos\n")
    assert src["status"] == "failed"


# ---- office formats -------------------------------------------------------------------


@needs_docling
async def test_pptx_keeps_slide_numbers(auth_client, db):
    src = await upload(auth_client, "clase.pptx", make_pptx())
    assert src["status"] == "ready", src
    segs = await segments(db, src["id"])
    by_text = {s.loc_slide: s.text for s in segs}
    assert set(by_text) == {1, 2, 3}
    assert "series efectivas" in by_text[2] and by_text[1].startswith("# Proximidad al fallo")
    file = await auth_client.get(
        f"/api/sources/{src['id']}/versions/{src['versions'][0]['id']}/file"
    )
    assert file.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert file.headers["content-disposition"].startswith("attachment")


@needs_docling
async def test_docx_keeps_sections_and_tables(auth_client, db):
    src = await upload(auth_client, "apuntes.docx", make_docx())
    assert src["status"] == "ready", src
    segs = await segments(db, src["id"])
    assert any(
        s.loc_section and s.loc_section.endswith("Recuperación") and "72 horas" in s.text
        for s in segs
    )
    assert any(s.text.startswith("|") and "RIR" in s.text for s in segs)  # table as Markdown


@needs_docling
async def test_epub_keeps_chapters(auth_client, db):
    src = await upload(auth_client, "libro.epub", make_epub())
    assert src["status"] == "ready", src
    segs = await segments(db, src["id"])
    sec = {s.loc_section for s in segs if "tensión mecánica" in s.text}
    assert sec == {"Capítulo 2. Hipertrofia"}
    assert any(s.loc_section == "Capítulo 2. Hipertrofia > Volumen" for s in segs)


async def test_unknown_or_dangerous_archives_are_refused(auth_client, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hola.txt", "x")
    r = await auth_client.post("/api/sources", files={"file": ("x.zip", buf.getvalue())})
    assert r.status_code == 415 or r.status_code == 400
    assert "no reconocido" in r.text

    from kos.library import service

    monkeypatch.setattr(service, "ZIP_MAX_UNCOMPRESSED", 10)
    r = await auth_client.post("/api/sources", files={"file": ("a.docx", make_docx())})
    assert "demasiado grande" in r.text
