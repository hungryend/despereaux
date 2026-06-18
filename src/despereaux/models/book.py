from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from despereaux.models.base import Base, new_id


class MetadataSource(enum.StrEnum):
    local = "local"
    calibre = "calibre"
    googlebooks = "googlebooks"
    openlibrary = "openlibrary"
    manual = "manual"


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sort_name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)


class Series(Base):
    __tablename__ = "series"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sort_name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)


class Book(Base):
    __tablename__ = "books"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String, nullable=False)
    sort_title: Mapped[str] = mapped_column(String, nullable=False, index=True)

    series_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("series.id", ondelete="SET NULL"), nullable=True
    )
    series_index: Mapped[float | None] = mapped_column(Float, nullable=True)

    publisher: Mapped[str | None] = mapped_column(String, nullable=True)
    published_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    isbn: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    format: Mapped[str] = mapped_column(String, nullable=False)
    # Logical library this book belongs to (configured by user; e.g. "Fiction",
    # "D&D Rules"). Defaults to "Default" for legacy single-library installs.
    library: Mapped[str] = mapped_column(String, nullable=False, default="Default", index=True)
    file_path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_mtime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    file_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)

    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cover_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # For formats that need conversion to be readable in the browser
    # (e.g. MOBI/AZW -> EPUB via Calibre). When set, /file serves this; /download
    # still serves the original file_path so users can grab the source format.
    converted_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # User-requested high-quality EPUB export (the "Convert to EPUB" button).
    # When set, this becomes the PRIMARY thing the reader serves (preferred over
    # converted_path); the user can still open/download the original. Set ONLY by
    # the export pipeline and cleared on re-ingest with changed content — never
    # written by the ingest upsert, so a re-scan can't clobber it.
    epub_export_path: Mapped[str | None] = mapped_column(String, nullable=True)

    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    google_books_id: Mapped[str | None] = mapped_column(String, nullable=True)
    openlibrary_id: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_source: Mapped[MetadataSource | None] = mapped_column(
        Enum(MetadataSource, native_enum=False), nullable=True
    )
    last_metadata_fetch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Parent/asset relationship — children are extra files (maps, handouts,
    # supplements) attached to a main book. Children don't appear in the
    # library grid; they're listed on the parent's detail page.
    parent_book_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("books.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Free-form display label for the asset shown on the parent's page
    # (e.g. "Maps", "Death House handout", "Pre-gen characters").
    asset_label: Mapped[str | None] = mapped_column(String, nullable=True)

    series = relationship("Series", lazy="joined")
    # lazy="raise" prevents accidental sync-context lazy loads (which throw
    # MissingGreenlet in async SQLAlchemy). Endpoints that need the M2M data
    # opt in explicitly via .options(selectinload(...)) in repos/books.py.
    authors = relationship("BookAuthor", cascade="all, delete-orphan", lazy="raise")
    tags = relationship("BookTag", cascade="all, delete-orphan", lazy="raise")

    __table_args__ = (
        Index("ix_books_series", "series_id", "series_index"),
        Index("ix_books_mtime", "file_mtime"),
    )


class BookAuthor(Base):
    __tablename__ = "book_authors"

    book_id: Mapped[str] = mapped_column(
        String, ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[str] = mapped_column(
        String, ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String, primary_key=True, default="author")

    author = relationship("Author", lazy="joined")


class BookTag(Base):
    __tablename__ = "book_tags"

    book_id: Mapped[str] = mapped_column(
        String, ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    tag = relationship("Tag", lazy="joined")
