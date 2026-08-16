from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.models import ReadingProgress

# A book counts as read once progress reaches the end. The paged readers report
# page/total, which is exactly 1.0 on the last page, but epub.js can land a hair
# short at the final CFI — so the last 1% counts as finished rather than leaving
# a fully-read book pinned to the On-deck shelf forever.
FINISHED_PERCENT = 0.99


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
        # updated_at must be set explicitly: the column's `onupdate` only fires for
        # UPDATE constructs, not for an upsert's DO UPDATE clause. Without it the
        # timestamp stays frozen at the first save and the On-deck shelf's
        # "most-recently-read first" order is really first-opened order.
        set_={
            "position": stmt.excluded.position,
            "percent": stmt.excluded.percent,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def list_progress_for_user(session: AsyncSession, *, user_id: str) -> list[ReadingProgress]:
    result = await session.execute(
        select(ReadingProgress).where(ReadingProgress.user_id == user_id)
    )
    return list(result.scalars().all())


async def mark_finished(session: AsyncSession, *, user_id: str, book_id: str) -> None:
    """Mark a book read: pin this user's progress to 100% so it leaves the On-deck
    shelf. The saved position is kept, so "Resume reading" still reopens where they
    stopped; a book never opened gets an empty position (the readers treat that as
    "start from the beginning").
    """
    stmt = sqlite_insert(ReadingProgress).values(
        user_id=user_id,
        book_id=book_id,
        position="",
        percent=1.0,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ReadingProgress.user_id, ReadingProgress.book_id],
        set_={"percent": 1.0, "updated_at": func.now()},
    )
    await session.execute(stmt)


async def delete_progress(session: AsyncSession, *, user_id: str, book_id: str) -> None:
    """Drop this user's reading position for a book ("mark as unread").

    Removes it from the library's On-deck shelf. No-op if there's no row.
    """
    await session.execute(
        delete(ReadingProgress).where(
            ReadingProgress.user_id == user_id, ReadingProgress.book_id == book_id
        )
    )
