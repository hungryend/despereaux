from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from despereaux.config import get_settings

SESSION_COOKIE = "despereaux_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days

_cached_secret: bytes | None = None


def _session_secret() -> bytes:
    """Settings value if provided, else a generated secret persisted in data_dir
    so sessions survive restarts."""
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret
    settings = get_settings()
    if settings.session_secret:
        _cached_secret = settings.session_secret.encode("utf-8")
        return _cached_secret
    path = settings.data_dir / "session-secret"
    try:
        _cached_secret = path.read_bytes().strip()
        if _cached_secret:
            return _cached_secret
    except OSError:
        pass
    _cached_secret = secrets.token_urlsafe(48).encode("ascii")
    path.write_bytes(_cached_secret)
    return _cached_secret


def _sign(payload_b64: bytes) -> str:
    return hmac.new(_session_secret(), payload_b64, hashlib.sha256).hexdigest()


def make_session_token(user_id: str) -> str:
    payload = json.dumps(
        {"uid": user_id, "exp": int(time.time()) + SESSION_TTL_SECONDS},
        separators=(",", ":"),
    ).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload)
    return f"v1.{payload_b64.decode('ascii')}.{_sign(payload_b64)}"


def verify_session_token(token: str | None) -> str | None:
    """Returns the user_id for a valid, unexpired session token, else None."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    payload_b64 = parts[1].encode("ascii")
    if not hmac.compare_digest(_sign(payload_b64), parts[2]):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    uid = payload.get("uid")
    return uid if isinstance(uid, str) else None
