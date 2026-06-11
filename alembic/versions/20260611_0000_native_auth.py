"""add users.password_hash + users.is_admin (native auth mode)

Revision ID: 0006_native_auth
Revises: 0005_api_tokens
Create Date: 2026-06-11

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_native_auth"
down_revision: str | Sequence[str] | None = "0005_api_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(), nullable=True))
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
    op.drop_column("users", "password_hash")
