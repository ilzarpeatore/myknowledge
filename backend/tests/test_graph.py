"""Graph neighbourhood, filters, hierarchy tree (ROADMAP Fase 2, criteria 4)."""

import time
import uuid

from sqlalchemy import insert

from kos.knowledge import service as ks
from kos.models import (
    Claim,
    ClaimEntity,
    Discipline,
    Entity,
    EntityDiscipline,
    Evidence,
    Person,
    Relationship,
    Source,
    SourceSegment,
    SourceVersion,
)


async def ent(db, name, type_key="concept"):
    return await ks.create_entity(db, type_key=type_key, name=name)


async def rel(db, a, b, type_key="influences", status="verified"):
    r = Relationship(source_entity_id=a.id, target_entity_id=b.id, type_key=type_key, status=status)
    db.add(r)
    await db.commit()
    return r


async def get(client, **params):
    r = await client.get("/api/graph/neighbourhood", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def names(g):
    return sorted(n["name"] for n in g["nodes"])


async def test_neighbourhood_depth_and_labels(auth_client, db):
    a, b, c, d = [await ent(db, n) for n in ("Fatiga", "Volumen", "Hipertrofia", "Proteína")]
    await rel(db, b, a)  # Volumen influye en Fatiga
    await rel(db, b, c, "contributes_to")
    await rel(db, d, c, "contributes_to")
    g1 = await get(auth_client, entity=str(a.id))
    assert names(g1) == ["Fatiga", "Volumen"]
    assert g1["edges"][0]["label"] == "influye en"
    center = next(n for n in g1["nodes"] if n["center"])
    assert center["name"] == "Fatiga"
    vol = next(n for n in g1["nodes"] if n["name"] == "Volumen")
    assert vol["degree"] == 2  # tells the UI it can be expanded
    g2 = await get(auth_client, entity=str(a.id), depth=2)
    assert names(g2) == ["Fatiga", "Hipertrofia", "Volumen"]
    g3 = await get(auth_client, entity=str(a.id), depth=3)
    assert names(g3) == ["Fatiga", "Hipertrofia", "Proteína", "Volumen"]


async def test_type_and_status_filters(auth_client, db):
    a, b, c, d = [await ent(db, n) for n in ("A1", "B1", "C1", "D1")]
    await rel(db, a, b, "causes")
    await rel(db, a, c, "influences", status="proposed")
    await rel(db, a, d, "influences", status="rejected")
    assert names(await get(auth_client, entity=str(a.id))) == ["A1", "B1", "C1"]
    assert names(await get(auth_client, entity=str(a.id), include_proposed=False)) == ["A1", "B1"]
    assert names(await get(auth_client, entity=str(a.id), types=["causes"])) == ["A1", "B1"]


async def test_discipline_source_and_author_filters(auth_client, db):
    a, b, c = [await ent(db, n) for n in ("Centro", "Fisio", "Biomec")]
    await rel(db, a, b)
    await rel(db, a, c)
    fis = Discipline(name="Fisiología", slug="fisiologia")
    db.add(fis)
    await db.commit()
    r = await auth_client.put(
        f"/api/entities/{b.id}/disciplines", json={"discipline_ids": [str(fis.id)]}
    )
    assert r.status_code == 200
    assert names(await get(auth_client, entity=str(a.id), discipline=[str(fis.id)])) == [
        "Centro",
        "Fisio",
    ]

    # source + author: a claim about "Biomec" citing a segment of one source, by one teacher
    src = Source(title="Curso X", kind="document")
    db.add(src)
    await db.flush()
    ver = SourceVersion(
        source_id=src.id, sha256="0" * 64, storage_key="raw/00/x", mime="text/plain", size_bytes=1
    )
    db.add(ver)
    await db.flush()
    seg = SourceSegment(source_version_id=ver.id, ordinal=0, text="texto", token_count=1)
    teacher = Person(name="Profesor X")
    db.add_all([seg, teacher])
    await db.flush()
    claim = Claim(text="t", claim_type="opinion", attributed_person_id=teacher.id)
    db.add(claim)
    await db.flush()
    db.add_all(
        [
            ClaimEntity(claim_id=claim.id, entity_id=c.id),
            Evidence(
                claim_id=claim.id,
                segment_id=seg.id,
                quote="texto",
                quote_char_start=0,
                quote_char_end=5,
            ),
        ]
    )
    await db.commit()
    assert names(await get(auth_client, entity=str(a.id), source=[str(src.id)])) == [
        "Biomec",
        "Centro",
    ]
    assert names(await get(auth_client, entity=str(a.id), person=[str(teacher.id)])) == [
        "Biomec",
        "Centro",
    ]

    bad = await auth_client.put(
        f"/api/entities/{b.id}/disciplines", json={"discipline_ids": [str(uuid.uuid4())]}
    )
    assert bad.status_code == 400


async def test_200_neighbours_under_two_seconds(auth_client, db):
    """ROADMAP Fase 2, criterion 4: a concept with 200 neighbours renders in < 2 s."""
    center = await ent(db, "Centro grande")
    ids = [uuid.uuid4() for _ in range(200)]
    await db.execute(
        insert(Entity),
        [
            {
                "id": i,
                "type_key": "concept",
                "canonical_name": f"Vecino {n:03d}",
                "slug": f"vecino-{n:03d}",
                "status": "active",
                "origin": "user",
            }
            for n, i in enumerate(ids)
        ],
    )
    await db.execute(
        insert(Relationship),
        [
            {
                "id": uuid.uuid4(),
                "source_entity_id": center.id,
                "target_entity_id": i,
                "type_key": "associated_with",
                "status": "verified",
                "origin": "user",
            }
            for i in ids
        ],
    )
    # second ring so depth 2 has real work: each neighbour links to the next one
    await db.execute(
        insert(Relationship),
        [
            {
                "id": uuid.uuid4(),
                "source_entity_id": ids[n],
                "target_entity_id": ids[n + 1],
                "type_key": "influences",
                "status": "verified",
                "origin": "user",
            }
            for n in range(199)
        ],
    )
    await db.commit()
    t = time.perf_counter()
    g = await get(auth_client, entity=str(center.id), depth=2)
    elapsed = time.perf_counter() - t
    assert len(g["nodes"]) == 201 and len(g["edges"]) == 399 and not g["truncated"]
    assert elapsed < 2.0, f"{elapsed:.2f}s"


async def test_max_nodes_truncates(auth_client, db):
    center = await ent(db, "Centro")
    for n in range(15):
        await rel(db, center, await ent(db, f"Hoja {n}"))
    g = await get(auth_client, entity=str(center.id), max_nodes=10)
    assert g["truncated"] and len(g["nodes"]) == 10


async def test_tree_hierarchy_four_levels_and_no_duplicates(auth_client, db):
    root = Discipline(name="Ciencias del entrenamiento", slug="cde")
    db.add(root)
    await db.flush()
    fis = Discipline(name="Fisiología", slug="fis", parent_id=root.id)
    db.add(fis)
    await db.commit()
    adapt, musc, hip, sarc = [
        await ent(db, n)
        for n in (
            "Adaptaciones",
            "Adaptaciones musculares",
            "Hipertrofia",
            "Hipertrofia sarcoplasmática",
        )
    ]
    fatigue, central = await ent(db, "Fatiga"), await ent(db, "Fatiga central")
    await rel(db, musc, adapt, "is_a")
    await rel(db, hip, musc, "part_of")
    await rel(db, sarc, hip, "is_a")
    await rel(db, central, fatigue, "is_a")
    await rel(db, adapt, sarc, "influences")  # non-hierarchical: ignored by the tree
    db.add_all(
        [
            EntityDiscipline(entity_id=adapt.id, discipline_id=fis.id),
            EntityDiscipline(entity_id=musc.id, discipline_id=fis.id),
        ]
    )
    await db.commit()

    tree = (await auth_client.get("/api/graph/tree")).json()
    top = tree[0]
    assert top["name"] == "Ciencias del entrenamiento"
    fis_node = top["children"][0]
    assert fis_node["name"] == "Fisiología"
    # musc is assigned too, but its parent is shown in the same discipline: one root only
    assert [c["name"] for c in fis_node["children"]] == ["Adaptaciones"]
    lvl = fis_node["children"][0]
    chain = []
    while lvl["children"]:
        lvl = lvl["children"][0]
        chain.append((lvl["name"], lvl["via"]))
    assert chain == [
        ("Adaptaciones musculares", "is_a"),
        ("Hipertrofia", "part_of"),
        ("Hipertrofia sarcoplasmática", "is_a"),
    ]
    loose = next(n for n in tree if n["name"] == "Sin disciplina")
    assert [c["name"] for c in loose["children"]] == ["Fatiga"]  # hip/sarc not repeated here
    assert loose["children"][0]["children"][0]["name"] == "Fatiga central"


async def test_tree_survives_cycles(auth_client, db):
    a, b = await ent(db, "Ciclo A"), await ent(db, "Ciclo B")
    await rel(db, a, b, "part_of")
    await rel(db, b, a, "part_of")
    r = await auth_client.get("/api/graph/tree")
    assert r.status_code == 200
    loose = next(n for n in r.json() if n["name"] == "Sin disciplina")
    assert [c["name"] for c in loose["children"]] == ["Ciclo A"]
    assert loose["children"][0]["children"][0]["name"] == "Ciclo B"
    assert loose["children"][0]["children"][0]["children"] == []  # the cycle is cut
