"""add books.converted_path column

Revision ID: 0003_converted_path
Revises: 0002_books_library
Create Date: 2026-06-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_converted_path"
down_revision: str | Sequence[str] | None = "0002_books_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.add_column(sa.Column("converted_path", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.drop_column("converted_path")
