"""add users.library_sort (each profile's remembered library sort/grouping)

Revision ID: 0010_user_library_sort
Revises: 0009_conversion_engine
Create Date: 2026-10-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_user_library_sort"
down_revision: str | Sequence[str] | None = "0009_conversion_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("library_sort", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "library_sort")
