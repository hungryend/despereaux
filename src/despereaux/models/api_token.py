from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from despereaux.models.base import Base, new_id


class ApiToken(Base):
    """A per-user, revocable API token (e.g. for the Furlough mobile app).

    Only the SHA-256 hash of the token is stored; the plaintext is shown once
    at creation and cannot be recovered.
    """

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)

    # Each user's auto-created "API key" (one per user). Unlike additional
    # tokens it is stored retrievably so the Account page can re-show it —
    # the same trade-off Sonarr/Radarr/Plex make for their API keys. Auth
    # still goes through token_hash; stored_plaintext exists only for reveal.
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    stored_plaintext: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
