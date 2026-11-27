import hashlib
import os
import stat

from sqlalchemy import select

from kos.library import originals
from kos.library.blobstore import get_store
from kos.models import AuditLog, SourceVersion

# Minimal well-formed PDF (synthetic test fixture, not user content).
PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)


async def upload(client, name, data, title=None):
    form = {"title": title} if title else None
    return await client.post("/api/sources", files={"file": (name, data)}, data=form)


async def test_upload_requires_session(client):
    assert (await upload(client, "a.pdf", PDF)).status_code == 401


async def test_upload_pdf_stores_immutable_original(auth_client, db, storage_root):
    r = await upload(auth_client, "Fatiga muscular.pdf", PDF)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["duplicate"] is False
    src = body["source"]
    assert src["title"] == "Fatiga muscular" and src["status"] == "uploaded"
    v = src["versions"][0]
    assert v["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert v["mime"] == "application/pdf" and v["size_bytes"] == len(PDF)
    assert v["original_filename"] == "Fatiga muscular.pdf"

    stored = await db.scalar(select(SourceVersion))
    path = storage_root / stored.storage_key
    assert path.read_bytes() == PDF
    assert not os.stat(path).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert not list((storage_root / "tmp").iterdir())


async def test_explicit_title_wins(auth_client):
    r = await upload(auth_client, "x.md", b"# Hola", title="Apuntes módulo 1")
    assert r.json()["source"]["title"] == "Apuntes módulo 1"


async def test_markdown_and_text_accepted(auth_client):
    md = await upload(auth_client, "notas.md", b"# Proximidad al fallo\n")
    txt = await upload(auth_client, "notas.txt", b"Repeticiones en reserva")
    assert md.json()["source"]["versions"][0]["mime"] == "text/markdown"
    assert txt.json()["source"]["versions"][0]["mime"] == "text/plain"


async def test_same_content_is_not_stored_twice(auth_client, db):
    first = (await upload(auth_client, "a.pdf", PDF)).json()
    second = (await upload(auth_client, "otro-nombre.pdf", PDF)).json()
    assert second["duplicate"] is True
    assert second["source"]["id"] == first["source"]["id"]
    assert len((await db.scalars(select(SourceVersion))).all()) == 1


async def test_rejects_fake_pdf_and_unknown_formats(auth_client):
    assert (await upload(auth_client, "x.pdf", b"<html>not a pdf")).status_code == 415
    assert (await upload(auth_client, "x.exe", b"MZ\x90\x00")).status_code == 415
    assert (await upload(auth_client, "empty.txt", b"")).status_code == 415


async def test_rejects_non_utf8_and_binary_text(auth_client):
    assert (await upload(auth_client, "latin1.txt", "año".encode("latin-1"))).status_code == 415
    assert (await upload(auth_client, "bin.md", b"abc\x00def")).status_code == 415


async def test_rejects_files_over_limit(auth_client, storage_root):
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 1)
    assert (await upload(auth_client, "big.pdf", big)).status_code == 413
    assert not list((storage_root / "tmp").iterdir())


async def test_filename_path_components_are_stripped(auth_client):
    r = await upload(auth_client, "../../etc/notas.md", b"# x")
    assert r.json()["source"]["versions"][0]["original_filename"] == "notas.md"


async def test_download_returns_exact_original(auth_client):
    src = (await upload(auth_client, "a.pdf", PDF)).json()["source"]
    v = src["versions"][0]
    r = await auth_client.get(f"/api/sources/{src['id']}/versions/{v['id']}/file")
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"] == "application/pdf"


async def test_text_download_cannot_render_as_html(auth_client):
    src = (await upload(auth_client, "x.md", b"<script>alert(1)</script>")).json()["source"]
    v = src["versions"][0]
    r = await auth_client.get(f"/api/sources/{src['id']}/versions/{v['id']}/file")
    assert r.headers["content-type"].startswith("text/plain")
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["content-disposition"].startswith("attachment")


async def test_list_search_and_archive(auth_client):
    a = (await upload(auth_client, "Hipertrofia.md", b"# a")).json()["source"]
    await upload(auth_client, "Fatiga.md", b"# b")
    listed = (await auth_client.get("/api/sources", params={"q": "hiper"})).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == a["id"]

    assert (await auth_client.post(f"/api/sources/{a['id']}/archive")).json()[
        "status"
    ] == "archived"
    assert (await auth_client.get("/api/sources")).json()["total"] == 1
    assert (await auth_client.get("/api/sources?include_archived=true")).json()["total"] == 2
    restored = (await auth_client.post(f"/api/sources/{a['id']}/restore")).json()
    assert restored["status"] == "uploaded"


async def test_search_treats_wildcards_literally(auth_client):
    await upload(auth_client, "abc.md", b"# a")
    assert (await auth_client.get("/api/sources", params={"q": "%"})).json()["total"] == 0


async def test_metadata_edit_is_audited(auth_client, db):
    src = (await upload(auth_client, "a.md", b"# a")).json()["source"]
    r = await auth_client.patch(
        f"/api/sources/{src['id']}",
        json={
            "title": "Curso de fuerza",
            "authors_text": "Profesor X",
            "url": "https://example.org/c",
        },
    )
    assert r.status_code == 200
    assert r.json()["title"] == "Curso de fuerza" and r.json()["authors_text"] == "Profesor X"
    log = await db.scalar(select(AuditLog).where(AuditLog.action == "source.updated"))
    assert log.before["title"] == "a" and log.after["title"] == "Curso de fuerza"


async def test_rejects_invalid_url(auth_client):
    src = (await upload(auth_client, "a.md", b"# a")).json()["source"]
    r = await auth_client.patch(f"/api/sources/{src['id']}", json={"url": "javascript:alert(1)"})
    assert r.status_code == 422


async def test_unknown_source_is_404(auth_client):
    r = await auth_client.get("/api/sources/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_backups_leave_out_uploads_and_uploading_again_restores_them(
    auth_client, db, storage_root
):
    src = (await upload(auth_client, "Clase 1.pdf", PDF)).json()["source"]
    v = src["versions"][0]
    assert v["origin"] == "upload" and v["stored"] is True
    left_out = await originals.excluded(db)
    assert [(r["filename"], r["title"]) for r in left_out] == [("Clase 1.pdf", "Clase 1")]
    line = originals.tsv(left_out)
    assert line == f"{left_out[0]['key']}\t{len(PDF)}\tClase 1.pdf\tClase 1\n"

    # the server is lost and a backup restored: the database knows it, the file is gone
    path = storage_root / left_out[0]["key"]
    path.chmod(0o644)
    path.unlink()
    assert [r["filename"] for r in await originals.missing(db, get_store())] == ["Clase 1.pdf"]
    got = (await auth_client.get(f"/api/sources/{src['id']}")).json()
    assert got["versions"][0]["stored"] is False
    r = await auth_client.get(f"/api/sources/{src['id']}/versions/{v['id']}/file")
    assert r.status_code == 404 and "vuelve a subir" in r.json()["detail"]

    again = (await upload(auth_client, "renombrado.pdf", PDF)).json()
    assert again["duplicate"] is True and again["restored"] is True
    assert again["source"]["id"] == src["id"] and again["source"]["versions"][0]["stored"]
    assert path.read_bytes() == PDF and not await originals.missing(db, get_store())
    assert await db.scalar(select(AuditLog).where(AuditLog.action == "source.original_restored"))
    once_more = (await upload(auth_client, "a.pdf", PDF)).json()
    assert once_more["duplicate"] is True and once_more["restored"] is False
