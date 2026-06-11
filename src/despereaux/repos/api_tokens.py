from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.models import ApiToken, User

TOKEN_PREFIX = "desp_"

# Throttle last_used_at writes so a reading session doesn't write on every request.
_TOUCH_INTERVAL = timedelta(minutes=15)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(dt: datetime) -> datetime:
    # SQLite hands back naive datetimes; they are stored in UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def create_api_token(
    session: AsyncSession, *, user_id: str, name: str
) -> tuple[ApiToken, str]:
    """Create a token for `user_id`. Returns (row, plaintext) — the plaintext is
    shown to the caller once and only its hash is persisted."""
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(user_id=user_id, name=name, token_hash=_hash_token(plaintext))
    session.add(row)
    await session.flush()
    return row, plaintext


async def resolve_api_token(session: AsyncSession, token: str) -> User | None:
    """Return the owning user for a presented plaintext token, or None."""
    result = await session.execute(
        select(ApiToken).where(ApiToken.token_hash == _hash_token(token))
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    user = await session.get(User, row.user_id)
    if user is None:
        return None
    now = datetime.now(UTC)
    if row.last_used_at is None or now - _as_utc(row.last_used_at) > _TOUCH_INTERVAL:
        row.last_used_at = now
        user.last_seen_at = now
        await session.flush()
    return user


async def get_api_token(session: AsyncSession, token_id: str) -> ApiToken | None:
    return await session.get(ApiToken, token_id)


async def list_api_tokens_for_user(session: AsyncSession, user_id: str) -> list[ApiToken]:
    """The user's ADDITIONAL tokens — the default API key has its own endpoints."""
    result = await session.execute(
        select(ApiToken)
        .where(ApiToken.user_id == user_id, ApiToken.is_default.is_(False))
        .order_by(ApiToken.created_at.desc())
    )
    return list(result.scalars().all())


async def get_or_create_default_token(session: AsyncSession, user_id: str) -> ApiToken:
    """Each user's auto-created, revealable API key. Stored retrievably (unlike
    additional tokens) so the Account page can re-show it on demand."""
    result = await session.execute(
        select(ApiToken).where(ApiToken.user_id == user_id, ApiToken.is_default.is_(True))
    )
    row = result.scalars().first()
    if row is not None and row.stored_plaintext:
        return row
    if row is not None:
        # Shouldn't happen (defaults always store plaintext), but heal anyway.
        await session.delete(row)
        await session.flush()
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        user_id=user_id,
        name="API key",
        token_hash=_hash_token(plaintext),
        is_default=True,
        stored_plaintext=plaintext,
    )
    session.add(row)
    await session.flush()
    return row


async def rotate_default_token(session: AsyncSession, user_id: str) -> ApiToken:
    """Replace the default API key — the old one stops working immediately."""
    result = await session.execute(
        select(ApiToken).where(ApiToken.user_id == user_id, ApiToken.is_default.is_(True))
    )
    for old in result.scalars().all():
        await session.delete(old)
    await session.flush()
    return await get_or_create_default_token(session, user_id)


async def list_api_tokens(session: AsyncSession) -> list[tuple[ApiToken, str]]:
    """All tokens with their owner's username, newest first."""
    result = await session.execute(
        select(ApiToken, User.username)
        .join(User, User.id == ApiToken.user_id)
        .order_by(ApiToken.created_at.desc())
    )
    return [(row, username) for row, username in result.all()]


async def delete_api_token(session: AsyncSession, token_id: str) -> bool:
    row = await session.get(ApiToken, token_id)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True
