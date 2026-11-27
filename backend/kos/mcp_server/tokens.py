"""Personal access tokens for MCP clients. Only a SHA-256 is stored; the token
itself is shown once. Scopes: ``read`` (always), ``propose`` (proposals, notes) and
``decide`` (accept or reject pending items; implies propose)."""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.auth.service import hash_token
from kos.models import ApiToken, AuditLog

PREFIX = "kos_"
SCOPES = ("read", "propose", "decide")


async def create(
    db: AsyncSession, name: str, propose: bool, decide: bool = False
) -> tuple[ApiToken, str]:
    raw = PREFIX + secrets.token_urlsafe(32)
    scopes = "read,propose,decide" if decide else ("read,propose" if propose else "read")
    tok = ApiToken(
        name=name.strip()[:100], token_hash=hash_token(raw), prefix=raw[:10], scopes=scopes
    )
    db.add(tok)
    await db.flush()
    db.add(
        AuditLog(
            actor="user",
            action="api_token.created",
            subject_table="api_tokens",
            subject_id=str(tok.id),
            after={"name": tok.name, "scopes": scopes},
        )
    )
    await db.commit()
    return tok, raw


async def verify(db: AsyncSession, raw: str) -> ApiToken | None:
    if not raw.startswith(PREFIX) or len(raw) > 200:
        return None
    tok = await db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(raw)))
    if tok is None or tok.revoked_at is not None:
        return None
    now = datetime.now(UTC)
    if tok.last_used_at is None or now - tok.last_used_at > timedelta(minutes=1):
        tok.last_used_at = now
        await db.commit()
    return tok


async def revoke(db: AsyncSession, tok: ApiToken) -> None:
    if tok.revoked_at is None:
        tok.revoked_at = datetime.now(UTC)
        db.add(
            AuditLog(
                actor="user",
                action="api_token.revoked",
                subject_table="api_tokens",
                subject_id=str(tok.id),
            )
        )
        await db.commit()
