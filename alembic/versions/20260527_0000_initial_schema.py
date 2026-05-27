"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-27

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("authentik_groups", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "authors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sort_name", sa.String(), nullable=False),
        sa.UniqueConstraint("sort_name"),
    )
    op.create_index("ix_authors_sort_name", "authors", ["sort_name"])

    op.create_table(
        "series",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sort_name", sa.String(), nullable=False),
        sa.UniqueConstraint("sort_name"),
    )
    op.create_index("ix_series_sort_name", "series", ["sort_name"])

    op.create_table(
        "tags",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_tags_name", "tags", ["name"])

    op.create_table(
        "books",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("sort_title", sa.String(), nullable=False),
        sa.Column(
            "series_id", sa.String(), sa.ForeignKey("series.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("series_index", sa.Float(), nullable=True),
        sa.Column("publisher", sa.String(), nullable=True),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("isbn", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("format", sa.String(), nullable=False),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_mtime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_hash", sa.String(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("cover_path", sa.String(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("rating_count", sa.Integer(), nullable=True),
        sa.Column("google_books_id", sa.String(), nullable=True),
        sa.Column("openlibrary_id", sa.String(), nullable=True),
        sa.Column("metadata_source", sa.String(), nullable=True),
        sa.Column("last_metadata_fetch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("file_path"),
    )
    op.create_index("ix_books_sort_title", "books", ["sort_title"])
    op.create_index("ix_books_isbn", "books", ["isbn"])
    op.create_index("ix_books_file_hash", "books", ["file_hash"])
    op.create_index("ix_books_series", "books", ["series_id", "series_index"])
    op.create_index("ix_books_mtime", "books", ["file_mtime"])

    op.create_table(
        "book_authors",
        sa.Column(
            "book_id", sa.String(), sa.ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "author_id",
            sa.String(),
            sa.ForeignKey("authors.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(), primary_key=True),
    )

    op.create_table(
        "book_tags",
        sa.Column(
            "book_id", sa.String(), sa.ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "tag_id", sa.String(), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
        ),
    )

    op.create_table(
        "reading_progress",
        sa.Column(
            "user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "book_id", sa.String(), sa.ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("position", sa.Text(), nullable=False),
        sa.Column("percent", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "bookmarks",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "book_id", sa.String(), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("position", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_bookmarks_user_book", "bookmarks", ["user_id", "book_id"])

    op.create_table(
        "downloads",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "book_id", sa.String(), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("user_agent", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("downloads")
    op.drop_index("ix_bookmarks_user_book", table_name="bookmarks")
    op.drop_table("bookmarks")
    op.drop_table("reading_progress")
    op.drop_table("book_tags")
    op.drop_table("book_authors")
    op.drop_index("ix_books_mtime", table_name="books")
    op.drop_index("ix_books_series", table_name="books")
    op.drop_index("ix_books_file_hash", table_name="books")
    op.drop_index("ix_books_isbn", table_name="books")
    op.drop_index("ix_books_sort_title", table_name="books")
    op.drop_table("books")
    op.drop_index("ix_tags_name", table_name="tags")
    op.drop_table("tags")
    op.drop_index("ix_series_sort_name", table_name="series")
    op.drop_table("series")
    op.drop_index("ix_authors_sort_name", table_name="authors")
    op.drop_table("authors")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
