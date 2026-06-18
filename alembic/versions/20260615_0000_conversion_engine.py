"""add conversions.engine (which PDF->markdown engine ran: heuristic | ocr)

Revision ID: 0009_conversion_engine
Revises: 0008_conversions
Create Date: 2026-06-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_conversion_engine"
down_revision: str | Sequence[str] | None = "0008_conversions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversions", sa.Column("engine", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversions", "engine")
