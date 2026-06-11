"""add api_tokens.is_default + api_tokens.stored_plaintext (revealable API key)

Revision ID: 0007_default_tokens
Revises: 0006_native_auth
Create Date: 2026-06-11

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_default_tokens"
down_revision: str | Sequence[str] | None = "0006_native_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_tokens",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column("api_tokens", sa.Column("stored_plaintext", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("api_tokens", "stored_plaintext")
    op.drop_column("api_tokens", "is_default")
