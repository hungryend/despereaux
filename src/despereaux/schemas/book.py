from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class AuthorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    sort_name: str


class SeriesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class BookSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    sort_title: str
    format: str
    library: str
    page_count: int | None = None
    cover_path: str | None = None
    rating: float | None = None
    series: SeriesRead | None = None
    series_index: float | None = None
    authors: list[str] = []


class LibraryRead(BaseModel):
    """A configured library and its current book count."""

    name: str
    path: str
    book_count: int = 0


class BookDetail(BookSummary):
    publisher: str | None = None
    published_date: date | None = None
    language: str | None = None
    isbn: str | None = None
    description: str | None = None
    rating_count: int | None = None
    google_books_id: str | None = None
    openlibrary_id: str | None = None
    metadata_source: str | None = None
    last_metadata_fetch_at: datetime | None = None
    file_size: int
    added_at: datetime
    tags: list[str] = []


class ProgressRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    book_id: str
    position: str
    percent: float
    updated_at: datetime


class ProgressUpdate(BaseModel):
    position: str
    percent: float
