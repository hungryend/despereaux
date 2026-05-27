from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.models import ReadingProgress


async def get_progress(
    session: AsyncSession, *, user_id: str, book_id: str
) -> ReadingProgress | None:
    result = await session.execute(
        select(ReadingProgress).where(
            ReadingProgress.user_id == user_id, ReadingProgress.book_id == book_id
        )
    )
    return result.scalar_one_or_none()


async def upsert_progress(
    session: AsyncSession,
    *,
    user_id: str,
    book_id: str,
    position: str,
    percent: float,
) -> None:
    stmt = sqlite_insert(ReadingProgress).values(
        user_id=user_id,
        book_id=book_id,
        position=position,
        percent=percent,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ReadingProgress.user_id, ReadingProgress.book_id],
        set_={"position": stmt.excluded.position, "percent": stmt.excluded.percent},
    )
    await session.execute(stmt)


async def list_progress_for_user(
    session: AsyncSession, *, user_id: str
) -> list[ReadingProgress]:
    result = await session.execute(
        select(ReadingProgress).where(ReadingProgress.user_id == user_id)
    )
    return list(result.scalars().all())
