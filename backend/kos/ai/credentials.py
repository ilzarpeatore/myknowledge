"""The Anthropic API key: from the server's environment or set from the «IA» page.

Order: ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` in the environment (the .env)
win; otherwise the key saved from the web is used. That key is stored encrypted
(Fernet, AES-128 + HMAC) with a key derived from ``KOS_SECRET_KEY``, which lives only
in the server's .env: a database dump or backup alone does not reveal it. The key is
never returned by the API, written to logs or to the audit log: only its last four
characters (``hint``).
"""

import base64
import contextvars
import hashlib
import os
import re
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from kos.config import get_settings
from kos.models import AppSecret, AuditLog

NAME = "anthropic_api_key"
KEY_FORMAT = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,300}$")

# The key for the AI call running in this task (read by the provider).
current_key: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "anthropic_api_key", default=None
)


class CredentialsError(Exception):
    pass


def _fernet() -> Fernet:
    secret = get_settings().secret_key
    if not secret or len(secret) < 32:
        raise CredentialsError(
            "el servidor no tiene KOS_SECRET_KEY (32 caracteres o más) en su .env: "
            "sin ella no se puede guardar la clave desde la web"
        )
    raw = hashlib.sha256(b"kos-app-secrets:" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def from_environment() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


async def stored_key(db: AsyncSession) -> str | None:
    row = await db.get(AppSecret, NAME)
    if row is None:
        return None
    try:
        return _fernet().decrypt(row.ciphertext.encode()).decode()
    except (CredentialsError, InvalidToken):
        return None  # master key missing or changed: the stored key is unreadable


async def key_for_call(db: AsyncSession) -> str | None:
    """None means: let the SDK read the environment."""
    if from_environment():
        return None
    return await stored_key(db)


async def status(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(AppSecret, NAME)
    readable = row is not None and await stored_key(db) is not None
    try:
        _fernet()
        can_store = True
    except CredentialsError:
        can_store = False
    source = "environment" if from_environment() else ("app" if readable else None)
    return {
        "configured": source is not None,
        "source": source,
        "hint": row.hint if row else None,
        "updated_at": row.updated_at.isoformat() if row else None,
        "stored_unreadable": row is not None and not readable,
        "can_store": can_store,
    }


def _audit(db: AsyncSession, action: str, hint: str | None) -> None:
    db.add(
        AuditLog(
            actor="user",
            action=action,
            subject_table="app_secrets",
            subject_id=NAME,
            after={"hint": hint},  # never the key
        )
    )


async def set_key(db: AsyncSession, key: str) -> dict[str, Any]:
    key = key.strip()
    if not KEY_FORMAT.match(key):
        raise CredentialsError("no parece una clave de Anthropic (empieza por «sk-ant-»)")
    token = _fernet().encrypt(key.encode()).decode()
    row = await db.get(AppSecret, NAME)
    if row is None:
        row = AppSecret(name=NAME, ciphertext=token, hint=key[-4:], updated_at=datetime.now(UTC))
        db.add(row)
    else:
        row.ciphertext, row.hint, row.updated_at = token, key[-4:], datetime.now(UTC)
    _audit(db, "ai.key_set", key[-4:])
    await db.commit()
    return await status(db)


async def delete_key(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(AppSecret, NAME)
    if row is not None:
        await db.delete(row)
        _audit(db, "ai.key_removed", row.hint)
        await db.commit()
    return await status(db)
