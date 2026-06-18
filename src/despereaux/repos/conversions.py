"""Data access for on-demand EPUB conversion jobs (the "Convert to EPUB" button)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.models import Book, Conversion, ConversionStatus
from despereaux.models.base import new_id

_ACTIVE = (ConversionStatus.queued, ConversionStatus.running)
_FINISHED = (ConversionStatus.done, ConversionStatus.failed)


async def create(
    session: AsyncSession,
    *,
    book_id: str,
    requested_by: str,
    source_hash: str,
    target_format: str = "epub",
) -> Conversion:
    row = Conversion(
        id=new_id(),
        book_id=book_id,
        requested_by=requested_by,
        source_hash=source_hash,
        target_format=target_format,
        status=ConversionStatus.queued,
    )
    session.add(row)
    await session.flush()
    return row


async def get(session: AsyncSession, conversion_id: str) -> Conversion | None:
    result = await session.execute(select(Conversion).where(Conversion.id == conversion_id))
    return result.scalar_one_or_none()


async def get_latest_for_book(session: AsyncSession, book_id: str) -> Conversion | None:
    result = await session.execute(
        select(Conversion)
        .where(Conversion.book_id == book_id)
        .order_by(Conversion.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_active_for_book(session: AsyncSession, book_id: str) -> Conversion | None:
    """Latest queued/running conversion for a book, if any (dedup double-clicks)."""
    result = await session.execute(
        select(Conversion)
        .where(Conversion.book_id == book_id)
        .where(Conversion.status.in_(_ACTIVE))
        .order_by(Conversion.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_done_for_hash(
    session: AsyncSession, book_id: str, source_hash: str
) -> Conversion | None:
    """Most recent completed conversion for this exact file content, if any.
    Used to safely reuse a cached export (vs. trusting a bare leftover file)."""
    result = await session.execute(
        select(Conversion)
        .where(Conversion.book_id == book_id)
        .where(Conversion.source_hash == source_hash)
        .where(Conversion.status == ConversionStatus.done)
        .order_by(Conversion.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def delete_for_book(session: AsyncSession, book_id: str) -> int:
    """Remove all conversion rows for a book (used when the user removes the
    converted EPUB). Returns the number of rows deleted."""
    result = await session.execute(delete(Conversion).where(Conversion.book_id == book_id))
    return result.rowcount or 0


async def set_status(session: AsyncSession, conversion_id: str, **fields: Any) -> None:
    """Patch a conversion row in place. `status` accepts a ConversionStatus."""
    row = await get(session, conversion_id)
    if row is None:
        return
    for k, v in fields.items():
        setattr(row, k, v)


async def list_for_user(
    session: AsyncSession,
    user_id: str,
    *,
    include_dismissed: bool = False,
    limit: int = 30,
) -> list[tuple[Conversion, str]]:
    """Recent conversions for the notifications menu, newest first.

    Returns (conversion, book_title) pairs so the menu can show titles without
    a second round-trip.
    """
    stmt = (
        select(Conversion, Book.title)
        .join(Book, Book.id == Conversion.book_id)
        .where(Conversion.requested_by == user_id)
        .order_by(Conversion.created_at.desc())
        .limit(limit)
    )
    if not include_dismissed:
        stmt = stmt.where(Conversion.dismissed.is_(False))
    result = await session.execute(stmt)
    return [(row, title) for row, title in result.all()]


async def fail_orphaned(session: AsyncSession) -> int:
    """Mark every queued/running conversion as failed.

    Conversions run as in-process background tasks that don't survive a restart,
    so on startup any still-active row is orphaned — failing them stops the UI
    showing a forever-spinner for work that will never finish. Returns the count.
    """
    result = await session.execute(select(Conversion).where(Conversion.status.in_(_ACTIVE)))
    rows = list(result.scalars().all())
    for row in rows:
        row.status = ConversionStatus.failed
        row.phase = None
        row.error = "interrupted by a server restart — please convert again"
    return len(rows)


async def dismiss_all_for_user(session: AsyncSession, user_id: str) -> int:
    """Hide the user's finished (done/failed) conversions from the menu.

    Active jobs are left visible — the conversion keeps running regardless.
    Returns the number of rows dismissed.
    """
    result = await session.execute(
        select(Conversion)
        .where(Conversion.requested_by == user_id)
        .where(Conversion.dismissed.is_(False))
        .where(Conversion.status.in_(_FINISHED))
    )
    rows = list(result.scalars().all())
    for row in rows:
        row.dismissed = True
    return len(rows)
