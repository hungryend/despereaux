from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.models import ApiToken, User


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def list_users_with_token_counts(session: AsyncSession) -> list[tuple[User, int]]:
    result = await session.execute(
        select(User, func.count(ApiToken.id))
        .outerjoin(ApiToken, ApiToken.user_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at)
    )
    return [(user, count) for user, count in result.all()]


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password_hash: str | None = None,
    is_admin: bool = False,
) -> User:
    user = User(
        username=username,
        email=None,
        authentik_groups=[],
        password_hash=password_hash,
        is_admin=is_admin,
    )
    session.add(user)
    await session.flush()
    return user


async def any_native_admin_exists(session: AsyncSession) -> bool:
    """An admin who can actually log in natively (has a password)."""
    result = await session.execute(
        select(User.id).where(User.is_admin.is_(True), User.password_hash.is_not(None)).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def get_or_create_user(
    session: AsyncSession,
    *,
    username: str,
    email: str | None,
    groups: list[str],
) -> User:
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if user is None:
        user = User(username=username, email=email, authentik_groups=groups)
        session.add(user)
        await session.flush()
        return user
    user.email = email or user.email
    user.authentik_groups = groups
    user.last_seen_at = now
    await session.flush()
    return user
