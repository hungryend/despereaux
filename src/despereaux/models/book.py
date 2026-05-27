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
    file_path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_mtime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    file_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)

    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cover_path: Mapped[str | None] = mapped_column(String, nullable=True)

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

    series = relationship("Series", lazy="joined")
    authors = relationship("BookAuthor", cascade="all, delete-orphan", lazy="selectin")
    tags = relationship("BookTag", cascade="all, delete-orphan", lazy="selectin")

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
