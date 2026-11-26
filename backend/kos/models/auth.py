"""Single-user authentication: one account, server-side sessions."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kos.models.base import Base, created_at_col, uuid_pk


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 of the cookie token; the raw token is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ip: Mapped[str | None] = mapped_column(INET, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = created_at_col()


class ApiToken(Base):
    """Personal access token for programmatic clients (MCP). Only its SHA-256 is
    stored; the token is shown once when created. ``scopes``: read, propose."""

    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)  # to recognise it
    scopes: Mapped[str] = mapped_column(String(100), nullable=False, default="read")
    created_at: Mapped[datetime] = created_at_col()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
