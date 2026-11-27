# ruff: noqa: E501  (realistic bibliography lines)
"""Bibliographic references found in the sources: detection (no AI), grouping, the
counter of mentions and sources, links to Estudios, merging and dismissing."""

import httpx
import pytest

from kos.evidence import doi as doi_mod
from kos.references.parse import find

from .test_knowledge import run_jobs

SLIDES = """# Diapositiva 3: proximidad al fallo

Entrenar al fallo no es necesario (Schoenfeld et al., 2017; Grgic & Schoenfeld, 2019).
Refalo et al. (2023a) encontraron hipertrofia similar a 1-3 RIR.
Curso de Madrid, 2019. Enero 2020. Versión 2021.
"""

BOOK = """# Capítulo 2

Como explicó Helms (2014), la escala RIR ayuda a ajustar la carga. Schoenfeld y col. (2017)
lo confirman (ACSM, 2009).

# Bibliografía

1. Schoenfeld, B. J., Ogborn, D., & Krieger, J. W. (2017). Dose-response relationship between weekly resistance training volume and increases in muscle mass. Journal of Sports Sciences, 35(11), 1073–1082. https://doi.org/10.1080/02640414.2016.1210197
2. Helms ER, Cronin J, Storey A, Zourdos MC. Application of the repetitions in reserve-based rating of perceived exertion scale for resistance training. Strength Cond J. 2016;38(4):42-49.
3. GONZÁLEZ BADILLO, Juan José (2002). Fundamentos del entrenamiento de la fuerza. Barcelona: Inde.
"""


def test_detection_shapes_and_false_positives():
    keys = [f.key for f in find(SLIDES)]
    assert keys == ["schoenfeld 2017", "grgic 2019", "refalo 2023a"]  # no Madrid/Enero/Versión
    bib = [f for f in find(BOOK) if f.kind == "bibliography"]
    assert [f.key for f in bib] == ["schoenfeld 2017", "helms 2016", "gonzalez 2002"]
    assert bib[0].doi == "10.1080/02640414.2016.1210197"  # the DOI inside is not counted twice
    assert bib[0].title.startswith("Dose-response relationship")
    for f in find(BOOK):
        assert BOOK[f.start : f.end] == f.raw  # offsets point at the exact text
    assert [
        f.key for f in find("González-Badillo (2002) y (Organización Mundial de la Salud, 2020)")
    ] == [
        "gonzalez 2002",
        "organizacion mundial de la salud 2020",
    ]
    assert [f.key for f in find("ver 10.1519/JSC.0000000000001234.")] == [
        "doi:10.1519/jsc.0000000000001234"
    ]


async def upload(client, name, text, section=None):
    r = await client.post(
        "/api/sources",
        files={"file": (name, text.encode())},
        data={"section": section} if section else None,
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["source"]


@pytest.fixture
async def library(auth_client):
    a = await upload(auth_client, "PRUEBA diapositivas.md", SLIDES, section="cursos")
    b = await upload(auth_client, "PRUEBA libro.md", BOOK, section="libros")
    await run_jobs()
    return {"slides": a, "book": b}


async def test_counter_groups_the_same_work_across_documents(auth_client, library):
    r = (await auth_client.get("/api/references")).json()
    by_key = {x["key"]: x for x in r["items"]}
    s17 = by_key["schoenfeld 2017"]
    # slides (1) + book in-text (1) + book bibliography (1) = 3 mentions in 2 documents
    assert (s17["mentions"], s17["sources"], s17["in_bibliography"]) == (3, 2, 1)
    assert s17["title"].startswith("Dose-response") and s17["doi"].startswith("10.1080/")
    assert r["items"][0]["key"] == "schoenfeld 2017"  # most cited first
    assert {"helms 2014", "helms 2016", "acsm 2009", "gonzalez 2002"} <= set(by_key)

    detail = (await auth_client.get(f"/api/references/{s17['id']}")).json()
    titles = {s["source_title"] for s in detail["by_source"]}
    assert titles == {"PRUEBA diapositivas", "PRUEBA libro"}
    m = detail["by_source"][0]["mentions"][0]
    assert m["raw"] in m["context"] and m["segment_id"]

    # filters: by section and by document
    cursos = (await auth_client.get("/api/references?section=cursos")).json()["items"]
    assert {x["key"] for x in cursos} == {"schoenfeld 2017", "grgic 2019", "refalo 2023a"}
    assert next(x for x in cursos if x["key"] == "schoenfeld 2017")["mentions"] == 1
    per_source = (await auth_client.get(f"/api/sources/{library['book']['id']}/references")).json()
    assert per_source["total"] == 5
    assert (await auth_client.get("/api/references?q=dose")).json()["total"] == 1


async def test_reprocessing_does_not_double_count(auth_client, library):
    before = (await auth_client.get("/api/references")).json()["items"][0]
    await auth_client.post(f"/api/sources/{library['book']['id']}/reprocess")
    await run_jobs()
    after = (await auth_client.get("/api/references")).json()["items"][0]
    assert (after["key"], after["mentions"]) == (before["key"], before["mentions"])


async def test_add_to_studies_and_link(auth_client, library, monkeypatch):
    def crossref(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "DOI": "10.1080/02640414.2016.1210197",
                    "title": [
                        "Dose-response relationship between weekly resistance training volume"
                    ],
                    "issued": {"date-parts": [[2017]]},
                    "type": "journal-article",
                }
            },
        )

    monkeypatch.setattr(doi_mod, "TRANSPORT", httpx.MockTransport(crossref))
    items = {x["key"]: x for x in (await auth_client.get("/api/references")).json()["items"]}
    r = await auth_client.post(f"/api/references/{items['schoenfeld 2017']['id']}/publication")
    assert r.status_code == 201, r.text
    assert r.json()["publication"]["methodological_quality"] == "not_assessed"
    linked = (await auth_client.get("/api/references?linked=true")).json()["items"]
    assert [x["key"] for x in linked] == ["schoenfeld 2017"]

    # without DOI or title it explains what is missing
    r = await auth_client.post(f"/api/references/{items['grgic 2019']['id']}/publication")
    assert r.status_code == 400 and "título" in r.json()["detail"]
    # a study already in Estudios with the same author and year is offered
    pub = await auth_client.post(
        "/api/publications",
        json={"title": "Effect of RIR", "year": 2023, "authors_text": "Refalo MC, Helms ER"},
    )
    d = (await auth_client.get(f"/api/references/{items['refalo 2023a']['id']}")).json()
    assert [c["id"] for c in d["candidates"]] == [pub.json()["id"]]
    r = await auth_client.put(
        f"/api/references/{items['refalo 2023a']['id']}/publication",
        json={"publication_id": pub.json()["id"]},
    )
    assert r.json()["publication"]["title"] == "Effect of RIR"


async def test_dismiss_and_merge_survive_a_rescan(auth_client, library):
    items = {x["key"]: x for x in (await auth_client.get("/api/references")).json()["items"]}
    r = await auth_client.patch(
        f"/api/references/{items['acsm 2009']['id']}", json={"status": "dismissed"}
    )
    assert r.status_code == 200
    # «Helms 2014» and «Helms 2016» are the same work cited with two years
    r = await auth_client.post(
        f"/api/references/{items['helms 2014']['id']}/merge",
        json={"into_id": items["helms 2016"]["id"]},
    )
    assert r.json()["mentions"] == 2

    assert (await auth_client.post("/api/references/rescan")).json()["queued"] == 2
    await run_jobs()
    keys = {x["key"]: x for x in (await auth_client.get("/api/references")).json()["items"]}
    assert "acsm 2009" not in keys and "helms 2014" not in keys
    assert keys["helms 2016"]["mentions"] == 2
    gone = (await auth_client.get("/api/references?status=dismissed")).json()["items"]
    assert {x["key"] for x in gone} == {"acsm 2009", "helms 2014"}


async def test_auth_required(client):
    client.cookies.clear()
    assert (await client.get("/api/references")).status_code == 401
