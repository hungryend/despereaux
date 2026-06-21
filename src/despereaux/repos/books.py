from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from despereaux.models import Author, Book, BookAuthor, BookTag, Series, Tag
from despereaux.models.base import new_id


def primary_epub_path(book: Book) -> str | None:
    """The EPUB the reader should serve as the book's primary form, if any.

    Prefers a user-requested high-quality export (`epub_export_path`) over the
    MOBI/AZW auto-conversion (`converted_path`). The export path is existence-
    checked so a cleared/missing file falls back gracefully. Returns None when
    the original format should be served (e.g. a PDF with no export yet)."""
    if book.epub_export_path and Path(book.epub_export_path).exists():
        return book.epub_export_path
    if book.converted_path:
        return book.converted_path
    return None


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


# Re-export the helpers used by metadata_apply (kept internal-ish via underscore).
__all__ = ["_get_or_create_author", "_get_or_create_tag", "_sort_name"]


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
    library: str | None = None,
    include_children: bool = False,
    order: str = "title",
) -> list[Book]:
    """List top-level books (parent_book_id IS NULL). Children/assets are
    hidden from the library grid by default; set include_children=True to
    include them (used by the attach picker).

    order="title" (default) sorts by sort_title; order="added" sorts by most
    recently added first. (Author grouping is done by the caller in Python since
    a multi-author book lands in several groups.)"""
    stmt = (
        select(Book)
        .options(selectinload(Book.authors).selectinload(BookAuthor.author))
        .order_by(Book.added_at.desc() if order == "added" else Book.sort_title)
        .limit(limit)
        .offset(offset)
    )
    if not include_children:
        stmt = stmt.where(Book.parent_book_id.is_(None))
    if library:
        stmt = stmt.where(Book.library == library)
    if search:
        like = f"%{search.lower()}%"
        stmt = stmt.where(Book.sort_title.ilike(like) | Book.title.ilike(like))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_books_by_ids(session: AsyncSession, ids: list[str]) -> list[Book]:
    """Fetch books by id (authors eager-loaded), returned in the order of `ids`.

    Ids with no matching book are skipped. Used by the library On-deck shelf,
    which needs the books behind a user's reading-progress rows in
    most-recently-read order.
    """
    if not ids:
        return []
    result = await session.execute(
        select(Book)
        .where(Book.id.in_(ids))
        .options(selectinload(Book.authors).selectinload(BookAuthor.author))
    )
    by_id = {b.id: b for b in result.scalars().all()}
    return [by_id[i] for i in ids if i in by_id]


async def get_children(session: AsyncSession, parent_id: str) -> list[Book]:
    """Books attached as assets to the given parent book."""
    stmt = (
        select(Book)
        .where(Book.parent_book_id == parent_id)
        .options(selectinload(Book.authors).selectinload(BookAuthor.author))
        .order_by(Book.asset_label.nulls_last(), Book.sort_title)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def attach_to_parent(
    session: AsyncSession,
    *,
    child_id: str,
    parent_id: str,
    label: str | None,
) -> bool:
    """Make `child_id` an asset of `parent_id`. Returns False if either book
    is missing OR if you'd be creating a parent cycle (child == parent OR
    parent already a descendant of child)."""
    if child_id == parent_id:
        return False
    child = await get_book(session, child_id)
    parent = await get_book(session, parent_id)
    if child is None or parent is None:
        return False
    # Don't allow attaching to a child of yourself (one-level deep cycle).
    if parent.parent_book_id == child_id:
        return False
    child.parent_book_id = parent_id
    child.asset_label = (label or "").strip() or None
    return True


async def detach_from_parent(session: AsyncSession, child_id: str) -> bool:
    child = await get_book(session, child_id)
    if child is None:
        return False
    child.parent_book_id = None
    child.asset_label = None
    return True


async def find_duplicates(session: AsyncSession, book: Book) -> list[Book]:
    """Find other books that look like duplicates of `book`. Match on (in order):
    same file_hash, same openlibrary_id, or same google_books_id + isbn pair."""
    conditions = []
    if book.file_hash:
        conditions.append(Book.file_hash == book.file_hash)
    if book.openlibrary_id:
        conditions.append(Book.openlibrary_id == book.openlibrary_id)
    if book.google_books_id:
        conditions.append(Book.google_books_id == book.google_books_id)
    if book.isbn:
        conditions.append(Book.isbn == book.isbn)
    if not conditions:
        return []
    from sqlalchemy import or_

    stmt = (
        select(Book)
        .where(or_(*conditions))
        .where(Book.id != book.id)
        .options(selectinload(Book.authors).selectinload(BookAuthor.author))
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def count_books_by_library(session: AsyncSession) -> dict[str, int]:
    """Returns {library_name: count} for every library that has books. Only
    counts top-level books (children/assets aren't shown in the grid)."""
    from sqlalchemy import func

    stmt = (
        select(Book.library, func.count(Book.id))
        .where(Book.parent_book_id.is_(None))
        .group_by(Book.library)
    )
    result = await session.execute(stmt)
    return {name: count for name, count in result.all()}


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
