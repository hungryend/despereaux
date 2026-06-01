"""add books.parent_book_id + books.asset_label

Revision ID: 0004_book_assets
Revises: 0003_converted_path
Create Date: 2026-06-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_book_assets"
down_revision: str | Sequence[str] | None = "0003_converted_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.add_column(sa.Column("parent_book_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("asset_label", sa.String(), nullable=True))
        batch.create_foreign_key(
            "fk_books_parent",
            "books",
            ["parent_book_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_books_parent", "books", ["parent_book_id"])


def downgrade() -> None:
    op.drop_index("ix_books_parent", table_name="books")
    with op.batch_alter_table("books") as batch:
        batch.drop_constraint("fk_books_parent", type_="foreignkey")
        batch.drop_column("asset_label")
        batch.drop_column("parent_book_id")
