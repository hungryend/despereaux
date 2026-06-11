from __future__ import annotations

import bcrypt

# bcrypt truncates at 72 bytes; reject longer instead of silently truncating.
_MAX_PASSWORD_BYTES = 72
MIN_PASSWORD_LENGTH = 8


def validate_password(password: str) -> str | None:
    """Returns an error message, or None if the password is acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        return "password too long (max 72 bytes)"
    return None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False
