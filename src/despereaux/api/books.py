from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.db import get_db
from despereaux.middleware.auth import current_user
from despereaux.models import BookAuthor, BookTag
from despereaux.repos import books as books_repo
from despereaux.schemas.book import BookDetail, BookSummary, SeriesRead

router = APIRouter(prefix="/api/books", tags=["books"])


def _to_summary(book) -> BookSummary:
    authors = [ba.author.name for ba in (book.authors or [])]
    return BookSummary(
        id=book.id,
        title=book.title,
        sort_title=book.sort_title,
        format=book.format,
        page_count=book.page_count,
        cover_path=book.cover_path,
        rating=book.rating,
        series=SeriesRead.model_validate(book.series) if book.series else None,
        series_index=book.series_index,
        authors=authors,
    )


def _to_detail(book) -> BookDetail:
    summary = _to_summary(book).model_dump()
    tags = [bt.tag.name for bt in (book.tags or [])]
    return BookDetail(
        **summary,
        publisher=book.publisher,
        published_date=book.published_date,
        language=book.language,
        isbn=book.isbn,
        description=book.description,
        rating_count=book.rating_count,
        google_books_id=book.google_books_id,
        openlibrary_id=book.openlibrary_id,
        metadata_source=book.metadata_source.value if book.metadata_source else None,
        last_metadata_fetch_at=book.last_metadata_fetch_at,
        file_size=book.file_size,
        added_at=book.added_at,
        tags=tags,
    )


@router.get("", response_model=list[BookSummary])
async def list_books(
    session: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
):
    rows = await books_repo.list_books(session, limit=limit, offset=offset, search=search)
    return [_to_summary(b) for b in rows]


@router.get("/{book_id}", response_model=BookDetail)
async def get_book(
    book_id: str,
    session: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
):
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")
    return _to_detail(book)
