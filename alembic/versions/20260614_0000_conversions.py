"""add conversions table (on-demand EPUB export jobs) + books.epub_export_path

Revision ID: 0008_conversions
Revises: 0007_default_tokens
Create Date: 2026-06-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_conversions"
down_revision: str | Sequence[str] | None = "0007_default_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "book_id",
            sa.String(),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_format", sa.String(), nullable=False, server_default="epub"),
        # ConversionStatus is a non-native enum (stored as VARCHAR).
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("phase", sa.String(), nullable=True),
        sa.Column("source_hash", sa.String(), nullable=False),
        sa.Column("output_path", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("toc_count", sa.Integer(), nullable=True),
        sa.Column("toc_source", sa.String(), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=True),
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_conversions_book_id", "conversions", ["book_id"])
    op.create_index("ix_conversions_requested_by", "conversions", ["requested_by"])
    op.create_index("ix_conversions_source_hash", "conversions", ["source_hash"])

    op.add_column("books", sa.Column("epub_export_path", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("books", "epub_export_path")
    op.drop_index("ix_conversions_source_hash", table_name="conversions")
    op.drop_index("ix_conversions_requested_by", table_name="conversions")
    op.drop_index("ix_conversions_book_id", table_name="conversions")
    op.drop_table("conversions")
