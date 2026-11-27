"""Delete a source for good: the «papelera» is archiving, and only an archived source
can be deleted. Before deleting, ``impact`` says what will be lost.

What goes: the stored files, everything derived from them (pages, fragments, search
vectors, transcript sections, reference mentions), its place in sections, branches,
courses and links, the evidence that quotes it, the claims left with no evidence at
all (with their cards, checks and contradictions), and the cards made from it.
What stays: concepts (even if left without claims), claims that still have evidence
from other sources, studies (publications), the audit log (title and file hashes) and
any backup made before. A claim that is evidence of a relationship loses that role.
"""

import asyncio
import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kos.library.blobstore import LocalBlobStore
from kos.models import (
    AuditLog,
    Claim,
    EntityAlias,
    Evidence,
    Flashcard,
    ReferenceMention,
    RelationshipEvidence,
    Source,
    SourceSegment,
    SourceStatus,
    SourceVersion,
)


class DeletionError(Exception):
    pass


def _segments(source_id: uuid.UUID):
    return (
        select(SourceSegment.id)
        .join(SourceVersion, SourceVersion.id == SourceSegment.source_version_id)
        .where(SourceVersion.source_id == source_id)
    )


def _orphan_claims(source_id: uuid.UUID):
    """Claims whose every piece of evidence quotes this source."""
    segs = _segments(source_id)
    quoting = select(Evidence.claim_id).where(Evidence.segment_id.in_(segs))
    elsewhere = select(Evidence.claim_id).where(Evidence.segment_id.not_in(segs))
    return select(Claim.id).where(Claim.id.in_(quoting), Claim.id.not_in(elsewhere))


async def impact(db: AsyncSession, source: Source) -> dict[str, Any]:
    segs = _segments(source.id)
    count = lambda stmt: db.scalar(select(func.count()).select_from(stmt.subquery()))  # noqa: E731
    orphans = _orphan_claims(source.id)
    return {
        "title": source.title,
        "archived": source.status == SourceStatus.archived,
        "files": len(source.versions),
        "bytes": sum(v.size_bytes for v in source.versions),
        "fragments": await count(segs),
        "claims_deleted": await count(orphans),
        "claims_losing_a_quote": await count(
            select(Evidence.claim_id)
            .where(Evidence.segment_id.in_(segs), Evidence.claim_id.not_in(orphans))
            .distinct()
        ),
        "cards_deleted": await count(
            select(Flashcard.id).where(
                (Flashcard.segment_id.in_(segs)) | (Flashcard.claim_id.in_(orphans))
            )
        ),
        "references_mentions": await count(
            select(ReferenceMention.id).where(ReferenceMention.source_id == source.id)
        ),
    }


async def delete_source(db: AsyncSession, store: LocalBlobStore, source: Source) -> dict:
    if source.status != SourceStatus.archived:
        raise DeletionError("archívala primero: solo se elimina lo que está en archivados")
    summary = await impact(db, source)
    segs = _segments(source.id)
    orphans = list((await db.scalars(_orphan_claims(source.id))).all())
    keys = [(v.storage_key, v.sha256) for v in source.versions]

    # knowledge that only this source backed
    await db.execute(
        delete(Flashcard).where(
            (Flashcard.segment_id.in_(segs)) | (Flashcard.claim_id.in_(orphans))
        )
    )
    await db.execute(
        delete(RelationshipEvidence).where(
            (RelationshipEvidence.segment_id.in_(segs))
            | (RelationshipEvidence.claim_id.in_(orphans))
        )
    )
    await db.execute(delete(Evidence).where(Evidence.segment_id.in_(segs)))
    if orphans:
        await db.execute(delete(Claim).where(Claim.id.in_(orphans)))
    await db.execute(
        update(EntityAlias)
        .where(EntityAlias.source_segment_id.in_(segs))
        .values(source_segment_id=None)
    )
    # the source and everything derived from it (cascades: pages, fragments, vectors,
    # transcript sections, extractions, mentions, sections, branches, links, courses)
    await db.execute(delete(SourceVersion).where(SourceVersion.source_id == source.id))
    db.add(
        AuditLog(
            actor="user",
            action="source.deleted",
            subject_table="sources",
            subject_id=str(source.id),
            before={
                "title": source.title,
                "url": source.url,
                "file_sha256": [sha for _, sha in keys],
                **{k: v for k, v in summary.items() if k not in ("title", "archived")},
            },
        )
    )
    sid = source.id
    db.expunge(source)
    await db.execute(delete(Source).where(Source.id == sid))
    await db.commit()

    # files last: if the database step failed, nothing is lost
    for key, _ in keys:
        try:
            path = store.path_for(key)
            await asyncio.to_thread(path.unlink, True)
        except (OSError, ValueError):
            pass  # a missing file is not an error here
    return summary
