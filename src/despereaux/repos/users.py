from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.models import User


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
