from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from despereaux.models.base import Base, new_id


class ConversionStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class Conversion(Base):
    """One on-demand "convert to EPUB" job per book per request.

    Persisting status/output/error in the DB (rather than in-memory) means the
    notifications menu and the per-book status survive a server restart. The
    converted EPUB itself is cached on disk at `output_path`
    (settings.exports_dir / "{book.file_hash}.epub").
    """

    __tablename__ = "conversions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    book_id: Mapped[str] = mapped_column(
        String, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Whose notification this is — the user who clicked "Convert to EPUB".
    requested_by: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_format: Mapped[str] = mapped_column(String, nullable=False, default="epub")

    status: Mapped[ConversionStatus] = mapped_column(
        Enum(ConversionStatus, native_enum=False),
        nullable=False,
        default=ConversionStatus.queued,
    )
    # Human-readable label for the progress bar ("Converting…", "Building contents…").
    phase: Mapped[str | None] = mapped_column(String, nullable=True)

    # book.file_hash at request time — lets us detect a stale export when the
    # underlying file is re-ingested with changed content.
    source_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    output_path: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    toc_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 'calibre' | 'pdf-outline' | 'heading-detect' | 'none'
    toc_source: Mapped[str | None] = mapped_column(String, nullable=True)
    # Which PDF->markdown engine ran: 'heuristic' (text layer) | 'ocr' (Ollama vision).
    engine: Mapped[str | None] = mapped_column(String, nullable=True)
    # Images carried into the EPUB — fidelity check (figures kept between paragraphs).
    image_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Cleared from the notifications menu by the user's "Clear" button.
    dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
