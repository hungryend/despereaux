from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from despereaux.models import Author, Book, BookAuthor, BookTag, Series, Tag
from despereaux.models.base import new_id


def _sort_name(name: str) -> str:
    """'John Smith' -> 'Smith, John'. 'The Hobbit' -> 'Hobbit, The'."""
    name = name.strip()
    parts = name.rsplit(" ", 1)
    if len(parts) == 2 and parts[0].lower() not in {"the", "a", "an"}:
        return f"{parts[1]}, {parts[0]}"
    if " " in name and name.split(" ", 1)[0].lower() in {"the", "a", "an"}:
        article, rest = name.split(" ", 1)
        return f"{rest}, {article}"
    return name


async def _get_or_create_author(session: AsyncSession, name: str) -> Author:
    sort_name = _sort_name(name)
    result = await session.execute(select(Author).where(Author.sort_name == sort_name))
    author = result.scalar_one_or_none()
    if author:
        return author
    author = Author(id=new_id(), name=name, sort_name=sort_name)
    session.add(author)
    await session.flush()
    return author


async def _get_or_create_series(session: AsyncSession, name: str) -> Series:
    sort_name = _sort_name(name)
    result = await session.execute(select(Series).where(Series.sort_name == sort_name))
    series = result.scalar_one_or_none()
    if series:
        return series
    series = Series(id=new_id(), name=name, sort_name=sort_name)
    session.add(series)
    await session.flush()
    return series


async def _get_or_create_tag(session: AsyncSession, name: str) -> Tag:
    result = await session.execute(select(Tag).where(Tag.name == name))
    tag = result.scalar_one_or_none()
    if tag:
        return tag
    tag = Tag(id=new_id(), name=name)
    session.add(tag)
    await session.flush()
    return tag


async def get_book_by_path(session: AsyncSession, file_path: str) -> Book | None:
    # No selectinload here — upsert_book uses raw DELETE + session.add for the
    # M2M rows rather than touching the relationship collection, which
    # avoids both the lazy-load MissingGreenlet trap and the identity-map
    # warning that comes from loading rows we're about to delete anyway.
    result = await session.execute(select(Book).where(Book.file_path == file_path))
    return result.scalar_one_or_none()


async def get_book(session: AsyncSession, book_id: str) -> Book | None:
    result = await session.execute(
        select(Book)
        .where(Book.id == book_id)
        .options(selectinload(Book.authors).selectinload(BookAuthor.author))
        .options(selectinload(Book.tags).selectinload(BookTag.tag))
    )
    return result.scalar_one_or_none()


async def list_books(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
) -> list[Book]:
    stmt = (
        select(Book)
        .options(selectinload(Book.authors).selectinload(BookAuthor.author))
        .order_by(Book.sort_title)
        .limit(limit)
        .offset(offset)
    )
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(Book.sort_title.ilike(like) | Book.title.ilike(like))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def upsert_book(
    session: AsyncSession,
    *,
    fields: dict[str, Any],
    author_names: list[str],
    series_name: str | None,
    series_index: float | None,
    tag_names: list[str],
) -> Book:
    existing = await get_book_by_path(session, fields["file_path"])

    series_id: str | None = None
    if series_name:
        series = await _get_or_create_series(session, series_name)
        series_id = series.id

    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        existing.series_id = series_id
        existing.series_index = series_index
        book = existing
    else:
        book = Book(
            id=new_id(),
            series_id=series_id,
            series_index=series_index,
            **fields,
        )
        session.add(book)
        await session.flush()

    # Raw SQL DELETE for the M2M rows — bypassing the ORM relationship avoids
    # the lazy-load MissingGreenlet that async-SQLAlchemy throws when
    # `lazy="selectin"` relationships are touched outside the load context.
    # session.add() of fresh rows below works because we never loaded the old
    # ones into the identity map.
    await session.execute(BookAuthor.__table__.delete().where(BookAuthor.book_id == book.id))
    await session.execute(BookTag.__table__.delete().where(BookTag.book_id == book.id))

    seen_authors: set[str] = set()
    for name in author_names:
        if not name or name in seen_authors:
            continue
        seen_authors.add(name)
        author = await _get_or_create_author(session, name)
        session.add(BookAuthor(book_id=book.id, author_id=author.id, role="author"))

    seen_tags: set[str] = set()
    for tag_name in tag_names:
        if not tag_name or tag_name in seen_tags:
            continue
        seen_tags.add(tag_name)
        tag = await _get_or_create_tag(session, tag_name)
        session.add(BookTag(book_id=book.id, tag_id=tag.id))

    await session.flush()
    return book
