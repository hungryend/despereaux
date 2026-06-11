from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from despereaux.models.base import Base, new_id


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    authentik_groups: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # Native-auth mode: bcrypt hash; None for header/token-only identities.
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    # Admin in native mode (authentik mode derives admin from group membership;
    # require_admin honours either).
    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
