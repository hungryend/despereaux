"""add books.library column

Revision ID: 0002_books_library
Revises: 0001_initial
Create Date: 2026-06-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_books_library"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite can't ADD COLUMN with NOT NULL + no default in a single statement
    # unless server_default is set. Adding with server_default 'Default' so
    # existing rows backfill cleanly.
    with op.batch_alter_table("books") as batch:
        batch.add_column(
            sa.Column("library", sa.String(), nullable=False, server_default="Default")
        )
    op.create_index("ix_books_library", "books", ["library"])


def downgrade() -> None:
    op.drop_index("ix_books_library", table_name="books")
    with op.batch_alter_table("books") as batch:
        batch.drop_column("library")
