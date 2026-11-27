# ruff: noqa: E501  (test data on one line)
"""Deleting a source for good: only from the archive, with a preview of what goes.
Files and derived data go; claims backed only by it go (with their cards); claims
with other evidence stay; concepts, studies and the audit log stay."""

import uuid

from sqlalchemy import func, select

from kos.config import get_settings
from kos.knowledge import service as ks
from kos.library.blobstore import get_store
from kos.models import (
    AuditLog,
    Claim,
    Entity,
    EntityAlias,
    Evidence,
    Flashcard,
    Relationship,
    SourceSegment,
    SourceVersion,
)

from .test_knowledge import entity, run_jobs


async def upload(client, name, text):
    r = await client.post("/api/sources", files={"file": (name, text.encode())})
    return r.json()["source"]["id"]


async def test_delete_from_the_archive(auth_client, db):
    a = await upload(
        auth_client, "PRUEBA a.md", "# A\n\nEl fallo aumenta la fatiga (Schoenfeld et al., 2017)."
    )
    b = await upload(
        auth_client, "PRUEBA b.md", "# B\n\nEl fallo también aumenta la fatiga, según otro curso."
    )
    await run_jobs()
    fallo = uuid.UUID((await entity(auth_client, "Proximidad al fallo"))["id"])
    fatiga = uuid.UUID((await entity(auth_client, "Fatiga"))["id"])
    seg_a = await db.scalar(select(SourceSegment).where(SourceSegment.text.contains("Schoenfeld")))
    seg_b = await db.scalar(select(SourceSegment).where(SourceSegment.text.contains("otro curso")))
    only_a = await ks.add_claim(
        db,
        text_="Solo A",
        claim_type="opinion",
        entities=[(fallo, "subject")],
        evidence=[(seg_a.id, "El fallo aumenta la fatiga")],
    )
    both = await ks.add_claim(
        db,
        text_="A y B",
        claim_type="opinion",
        entities=[(fallo, "subject")],
        evidence=[(seg_a.id, "aumenta la fatiga"), (seg_b.id, "también aumenta la fatiga")],
    )
    rel = await ks.add_relationship(
        db,
        source_id=fallo,
        target_id=fatiga,
        type_key="influences",
        evidence=[{"claim_id": only_a.id}, {"segment_id": seg_b.id}],
    )
    r = await auth_client.post(f"/api/entities/{fallo}/flashcards/generate", json={"ai": False})
    assert r.json()["created"] == 2
    db.add(
        EntityAlias(
            entity_id=fatiga,
            alias="cansancio",
            alias_normalized="cansancio",
            source_segment_id=seg_a.id,
        )
    )
    await auth_client.post(f"/api/sources/{a}/links", json={"other_id": b, "note": "relacionadas"})
    await db.commit()
    path = get_store().path_for(
        (
            await db.scalar(select(SourceVersion).where(SourceVersion.source_id == uuid.UUID(a)))
        ).storage_key
    )
    assert path.exists()

    pre = (await auth_client.get(f"/api/sources/{a}/delete-preview")).json()
    assert pre == {
        **pre,
        "archived": False,
        "files": 1,
        "claims_deleted": 1,
        "claims_losing_a_quote": 1,
        "references_mentions": 1,
    }
    assert pre["fragments"] >= 1

    # not archived: refused
    r = await auth_client.delete(f"/api/sources/{a}")
    assert r.status_code == 409 and "archív" in r.json()["detail"]
    await auth_client.post(f"/api/sources/{a}/archive")
    r = await auth_client.delete(f"/api/sources/{a}")
    assert r.status_code == 200 and r.json()["claims_deleted"] == 1

    only_a_id, both_id, rel_id = only_a.id, both.id, rel.id
    db.expire_all()
    assert (await auth_client.get(f"/api/sources/{a}")).status_code == 404
    assert not path.exists()  # the file is gone from storage
    assert await db.get(Claim, only_a_id) is None
    kept = await db.get(Claim, both_id)
    assert kept is not None
    assert (
        await db.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.claim_id == both_id)
        )
        == 1
    )
    assert await db.get(Relationship, rel_id) is not None  # still backed by B's fragment
    assert await db.get(Entity, fallo) is not None
    alias = await db.scalar(select(EntityAlias).where(EntityAlias.alias == "cansancio"))
    assert alias is not None and alias.source_segment_id is None
    assert all(c.claim_id != only_a_id for c in (await db.scalars(select(Flashcard))).all())
    log = await db.scalar(select(AuditLog).where(AuditLog.action == "source.deleted"))
    assert log.before["title"] == "PRUEBA a" and len(log.before["file_sha256"]) == 1
    # B and the library still work
    assert (await auth_client.get(f"/api/sources/{b}")).status_code == 200
    refs = (await auth_client.get("/api/references")).json()
    assert refs["total"] == 0  # the only mention was in A
    assert get_settings() is not None
