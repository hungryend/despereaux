from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from despereaux.models.base import Base, new_id


class ReadingProgress(Base):
    __tablename__ = "reading_progress"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    book_id: Mapped[str] = mapped_column(
        String, ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[str] = mapped_column(Text, nullable=False)
    percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Bookmark(Base):
    __tablename__ = "bookmarks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    book_id: Mapped[str] = mapped_column(
        String, ForeignKey("books.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_bookmarks_user_book", "user_id", "book_id"),)
