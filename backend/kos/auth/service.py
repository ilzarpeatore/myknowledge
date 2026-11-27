"""Password hashing, login throttling and server-side sessions."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from kos.config import Settings
from kos.models import AuditLog, LoginAttempt, User, UserSession

_hasher = PasswordHasher()
# Verified against when the username does not exist, so both paths cost the same.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")

MIN_PASSWORD_LENGTH = 12


class AuthError(Exception):
    pass


class TooManyAttempts(AuthError):
    pass


class InvalidCredentials(AuthError):
    pass


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class NewSession:
    token: str
    session: UserSession


async def _too_many_failures(db: AsyncSession, s: Settings, username: str, ip: str | None) -> bool:
    since = datetime.now(UTC) - timedelta(minutes=s.login_window_minutes)
    who = [LoginAttempt.username == username]
    if ip:
        who.append(LoginAttempt.ip == ip)
    stmt = select(func.count()).where(
        LoginAttempt.success.is_(False), LoginAttempt.created_at >= since, or_(*who)
    )
    return (await db.scalar(stmt) or 0) >= s.login_max_failures


async def login(
    db: AsyncSession,
    s: Settings,
    username: str,
    password: str,
    ip: str | None,
    user_agent: str | None,
) -> NewSession:
    """Validate credentials and open a session. Commits the transaction."""
    username = username.strip().lower()
    if await _too_many_failures(db, s, username, ip):
        raise TooManyAttempts()

    user = await db.scalar(select(User).where(User.username == username))
    ok = _verify(user.password_hash if user else _DUMMY_HASH, password) and user is not None
    db.add(LoginAttempt(username=username, ip=ip, success=ok))

    if not ok or user is None:
        db.add(AuditLog(actor="user", action="auth.login_failed", after={"username": username}))
        await db.commit()
        raise InvalidCredentials()

    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)

    now = datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=now + timedelta(hours=s.session_ttl_hours),
        last_seen_at=now,
        user_agent=(user_agent or "")[:500] or None,
        ip=ip,
    )
    user.last_login_at = now
    db.add(session)
    db.add(
        AuditLog(actor="user", action="auth.login", subject_table="users", subject_id=str(user.id))
    )
    await db.commit()
    return NewSession(token=token, session=session)


async def resolve_session(db: AsyncSession, token: str) -> User | None:
    """Return the user for a valid, unexpired session token."""
    now = datetime.now(UTC)
    row = (
        await db.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == hash_token(token), UserSession.expires_at > now)
        )
    ).first()
    if row is None:
        return None
    session, user = row
    # Avoid a write on every request: refresh at most once a minute.
    if session.last_seen_at is None or now - session.last_seen_at > timedelta(minutes=1):
        session.last_seen_at = now
        await db.commit()
    return user


async def logout(db: AsyncSession, token: str) -> None:
    await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
    db.add(AuditLog(actor="user", action="auth.logout"))
    await db.commit()


async def create_single_user(db: AsyncSession, username: str, password: str) -> User:
    """Create the only account. Refuses if any account already exists."""
    if await db.scalar(select(func.count()).select_from(User)):
        raise AuthError("an account already exists; use reset-password instead")
    user = User(username=username.strip().lower(), password_hash=hash_password(password))
    db.add(user)
    db.add(AuditLog(actor="system", action="auth.user_created", after={"username": user.username}))
    await db.commit()
    return user


async def reset_password(db: AsyncSession, username: str, password: str) -> None:
    """Change the password and revoke every open session."""
    user = await db.scalar(select(User).where(User.username == username.strip().lower()))
    if user is None:
        raise AuthError("no such user")
    user.password_hash = hash_password(password)
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.add(AuditLog(actor="system", action="auth.password_reset", subject_id=str(user.id)))
    await db.commit()
