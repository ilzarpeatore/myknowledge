"""Generated mind map (ROADMAP Fase 2, criterion 6)."""

import xml.etree.ElementTree as ET

from kos.knowledge import service as ks
from kos.models import (
    Claim,
    ClaimEntity,
    Evidence,
    Person,
    Relationship,
    RelationshipEvidence,
    Source,
    SourceSegment,
    SourceVersion,
)


async def setup_concept(db):
    center = await ks.create_entity(
        db,
        type_key="concept",
        name="Proximidad al fallo",
        definition="Repeticiones que quedan antes del fallo",
    )
    mech = await ks.create_entity(
        db, type_key="mechanism", name="Reclutamiento de unidades motoras"
    )
    var = await ks.create_entity(db, type_key="variable", name="Volumen <semanal> & series")
    rel_c = await ks.create_entity(db, type_key="concept", name="Fatiga")
    gone = await ks.create_entity(db, type_key="concept", name="Rechazado")
    q = await ks.create_entity(
        db, type_key="open_question", name="¿Importa el fallo en principiantes?"
    )

    src = Source(title="Curso A", kind="document")
    db.add(src)
    await db.flush()
    ver = SourceVersion(
        source_id=src.id,
        sha256="1" * 64,
        storage_key="raw/11/x",
        mime="application/pdf",
        size_bytes=1,
    )
    db.add(ver)
    await db.flush()
    seg = SourceSegment(
        source_version_id=ver.id,
        ordinal=0,
        text="Cerca del fallo se reclutan más",
        token_count=5,
        loc_page_start=12,
    )
    teacher = Person(name="Profesora A")
    db.add_all([seg, teacher])
    await db.flush()

    def r(a, b, t, status="verified"):
        x = Relationship(source_entity_id=a.id, target_entity_id=b.id, type_key=t, status=status)
        db.add(x)
        return x

    r1 = r(center, mech, "influences")
    r(var, center, "influences", status="proposed")  # incoming -> inverse label
    r(center, rel_c, "associated_with")
    r(center, gone, "causes", status="rejected")
    r(q, center, "associated_with")
    await db.flush()
    db.add(RelationshipEvidence(relationship_id=r1.id, segment_id=seg.id))
    claim = Claim(
        text="Entrenar a 0-3 RIR basta",
        claim_type="recommendation",
        attributed_person_id=teacher.id,
        review_status="accepted",
    )
    db.add(claim)
    await db.flush()
    db.add_all(
        [
            ClaimEntity(claim_id=claim.id, entity_id=center.id),
            Evidence(
                claim_id=claim.id,
                segment_id=seg.id,
                quote="Cerca del fallo",
                quote_char_start=0,
                quote_char_end=15,
            ),
        ]
    )
    await db.commit()
    return center


async def test_branches_grouped_by_type_with_sources(auth_client, db):
    center = await setup_concept(db)
    m = (await auth_client.get(f"/api/entities/{center.id}/mindmap")).json()
    branches = {b["key"]: b for b in m["branches"]}
    assert list(branches) == [
        "claims",
        "mechanisms",
        "variables",
        "authors",
        "questions",
        "related",
    ]
    mech = branches["mechanisms"]["items"][0]
    assert mech["relation"] == "Proximidad al fallo influye en Reclutamiento de unidades motoras"
    assert mech["ref"]["source_title"] == "Curso A" and mech["ref"]["page"] == 12
    var = branches["variables"]["items"][0]
    assert (
        var["relation"].startswith("Proximidad al fallo está influido por")
        and var["status"] == "proposed"
    )
    assert branches["authors"]["items"][0]["name"] == "Profesora A"
    assert branches["claims"]["items"][0]["ref"]["page"] == 12
    assert [i["name"] for i in branches["related"]["items"]] == ["Fatiga"]  # rejected excluded
    assert m["center"]["definition"] == "Repeticiones que quedan antes del fallo"


async def test_markdown_export_for_markmap(auth_client, db):
    center = await setup_concept(db)
    r = await auth_client.get(f"/api/entities/{center.id}/mindmap.md")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    md = r.text
    assert md.startswith("# [Proximidad al fallo](http://testserver/concepts/proximidad-al-fallo)")
    assert "## Mecanismos" in md and "## Preguntas abiertas" in md
    assert "Curso A, p. 12" in md and "— propuesta" in md


async def test_opml_export_is_well_formed_and_escaped(auth_client, db):
    center = await setup_concept(db)
    r = await auth_client.get(f"/api/entities/{center.id}/mindmap.opml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/x-opml")
    root = ET.fromstring(r.content)  # noqa: S314 - our own output, not external data
    assert root.tag == "opml" and root.get("version") == "2.0"
    top = root.find("body/outline")
    assert top.get("text") == "Proximidad al fallo" and top.get("url").endswith(
        "/concepts/proximidad-al-fallo"
    )
    branch_names = [o.get("text") for o in top.findall("outline")]
    assert "Variables" in branch_names
    var = next(o for o in top.findall("outline") if o.get("text") == "Variables").find("outline")
    assert var.get("text") == "Volumen <semanal> & series"  # survived XML escaping intact
    mech = next(o for o in top.findall("outline") if o.get("text") == "Mecanismos").find("outline")
    assert "Curso A, p. 12" in mech.get("_note")


async def test_unknown_concept_404(auth_client):
    r = await auth_client.get("/api/entities/00000000-0000-0000-0000-000000000000/mindmap")
    assert r.status_code == 404
