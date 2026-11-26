from sqlalchemy import func, select

import kos.ingestion.pipeline  # noqa: F401  (registers the job handler)
from kos.config import get_settings
from kos.db import get_sessionmaker
from kos.jobs import worker
from kos.models import Job, JobStatus, SourcePage, SourceSegment
from tests.pdfgen import make_pdf

PAGES = [
    ["Fatiga muscular", "La fatiga periferica reduce la fuerza."],
    ["Proximidad al fallo", "Las repeticiones en reserva miden la proximidad al fallo."],
    [],  # no text layer, like a scanned page
]


async def run_jobs(n=1):
    for _ in range(n):
        await worker.run_once(get_sessionmaker(), get_settings(), "test")


async def upload(client, name, data):
    r = await client.post("/api/sources", files={"file": (name, data)})
    assert r.status_code == 200, r.text
    return r.json()["source"]


async def test_upload_queues_extraction_and_pdf_keeps_page_numbers(auth_client, db):
    src = await upload(auth_client, "curso.pdf", make_pdf(PAGES))
    assert src["status"] == "uploaded"
    job = await db.scalar(select(Job).where(Job.kind == "source.extract"))
    assert job.status == JobStatus.pending

    await run_jobs()
    await db.refresh(job)
    assert job.status == JobStatus.completed, job.last_error
    assert job.result == {"pages": 3, "segments": 2, "pages_without_text": [3]}

    detail = (await auth_client.get(f"/api/sources/{src['id']}")).json()
    assert detail["status"] == "ready"
    v = detail["versions"][0]
    assert v["page_count"] == 3 and v["extractor"] == "pdfium"
    assert v["extraction_info"] == {"pages_without_text": [3]}

    segs = (await db.scalars(select(SourceSegment).order_by(SourceSegment.ordinal))).all()
    assert [s.loc_page_start for s in segs] == [1, 2]
    assert "repeticiones en reserva" in segs[1].text
    page2 = (await auth_client.get(f"/api/sources/{src['id']}/versions/{v['id']}/pages/2")).json()[
        "text"
    ]
    assert page2[segs[1].loc_char_start : segs[1].loc_char_end] == segs[1].text


async def test_markdown_sections_are_recorded(auth_client, db):
    md = "# Hipertrofia\n\nTensión mecánica.\n\n## Volumen\n\nSeries por semana.".encode()
    await upload(auth_client, "apuntes.md", md)
    await run_jobs()
    segs = (await db.scalars(select(SourceSegment).order_by(SourceSegment.ordinal))).all()
    assert [s.loc_section for s in segs] == ["Hipertrofia", "Hipertrofia > Volumen"]
    assert all(s.loc_page_start is None for s in segs)


async def test_corrupt_pdf_fails_permanently_and_marks_source(auth_client, db):
    src = await upload(auth_client, "roto.pdf", b"%PDF-1.4\nthis is not really a pdf")
    await run_jobs()
    job = await db.scalar(select(Job))
    assert job.status == JobStatus.failed and job.attempts == 1
    assert "PDF" in job.last_error
    assert (await auth_client.get(f"/api/sources/{src['id']}")).json()["status"] == "failed"


async def test_reprocess_replaces_instead_of_duplicating(auth_client, db):
    src = await upload(auth_client, "curso.pdf", make_pdf(PAGES))
    await run_jobs()
    r = await auth_client.post(f"/api/sources/{src['id']}/reprocess")
    assert r.status_code == 202
    assert (await auth_client.post(f"/api/sources/{src['id']}/reprocess")).status_code == 409
    await run_jobs()
    assert await db.scalar(select(func.count()).select_from(SourceSegment)) == 2
    assert await db.scalar(select(func.count()).select_from(SourcePage)) == 3


async def test_search_finds_segments_with_page_and_highlight(auth_client):
    await upload(auth_client, "curso.pdf", make_pdf(PAGES))
    await upload(auth_client, "otro.md", b"# Nutricion\n\nProteina y recuperacion.")
    await run_jobs(2)
    hits = (await auth_client.get("/api/search", params={"q": "repeticiones fallo"})).json()
    assert len(hits) == 1
    hit = hits[0]
    assert hit["source_title"] == "curso" and hit["page"] == 2
    assert "\x02" in hit["snippet"] and "\x03" in hit["snippet"]
    # Spanish stemming: singular query matches plural text.
    assert (await auth_client.get("/api/search", params={"q": "repeticion"})).json()


async def test_search_excludes_archived_sources(auth_client):
    src = await upload(auth_client, "curso.pdf", make_pdf(PAGES))
    await run_jobs()
    await auth_client.post(f"/api/sources/{src['id']}/archive")
    assert (await auth_client.get("/api/search", params={"q": "fatiga"})).json() == []
    # Processing keeps working on archived sources without un-archiving them.
    detail = (await auth_client.get(f"/api/sources/{src['id']}")).json()
    assert detail["status"] == "archived"


async def test_search_requires_session(client):
    assert (await client.get("/api/search", params={"q": "x"})).status_code == 401


def test_worker_cli_entry_point_registers_pipeline_handlers():
    """Regression: `kos worker` (the Docker command) must run with the pipeline
    handlers registered. Running the worker module as __main__ silently lost them."""
    import subprocess
    import sys

    code = (
        "import sys, asyncio, kos.jobs.worker as w\n"
        "async def fake():\n"
        "    print('KINDS', w.registered_kinds())\n"
        "w.run_forever = fake\n"
        "sys.argv = ['kos', 'worker']\n"
        "from kos.cli import main\n"
        "main()\n"
    )
    # Fixed, test-authored code; no external input reaches the subprocess.
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert (
        "KINDS ['entity.compare', 'entity.propose_relationships', 'source.embed', 'source.extract']"
        in out.stdout
    )
