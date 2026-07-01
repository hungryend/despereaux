"""Unit tests for the auth primitives: bcrypt password handling and the
HMAC-signed session tokens. Pins behavior across bcrypt majors (5.0 raises
ValueError on malformed hashes where 4.x sometimes returned False)."""

from __future__ import annotations

from despereaux.services import sessions
from despereaux.services.passwords import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    validate_password,
    verify_password,
)

# ---------- passwords ----------


def test_hash_verify_roundtrip() -> None:
    h = hash_password("correct horse battery staple")
    assert h.startswith("$2")
    assert verify_password("correct horse battery staple", h) is True
    assert verify_password("wrong horse", h) is False


def test_verify_handles_missing_and_malformed_hash() -> None:
    assert verify_password("anything", None) is False
    assert verify_password("anything", "") is False
    # bcrypt 5 raises ValueError on garbage salts; verify_password must swallow it.
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_validate_password_length_floor() -> None:
    assert validate_password("a" * (MIN_PASSWORD_LENGTH - 1)) is not None
    assert validate_password("a" * MIN_PASSWORD_LENGTH) is None


def test_validate_password_72_byte_cap_is_byte_based() -> None:
    assert validate_password("a" * 72) is None
    assert validate_password("a" * 73) is not None
    # 40 chars but 80 UTF-8 bytes — the cap must count bytes, not characters,
    # because bcrypt truncates at 72 BYTES.
    assert validate_password("é" * 40) is not None


def test_hash_verify_multibyte_password() -> None:
    pw = "pässwörd-ñ-你好-8chars"
    assert validate_password(pw) is None
    assert verify_password(pw, hash_password(pw)) is True


# ---------- sessions ----------


def test_session_token_roundtrip() -> None:
    token = sessions.make_session_token("user-abc")
    assert token.startswith("v1.")
    assert sessions.verify_session_token(token) == "user-abc"


def test_session_token_tamper_and_garbage_rejected() -> None:
    token = sessions.make_session_token("user-abc")
    head, payload, sig = token.split(".")
    flipped = "0" if sig[0] != "0" else "1"
    assert sessions.verify_session_token(f"{head}.{payload}.{flipped}{sig[1:]}") is None
    assert sessions.verify_session_token(f"v2.{payload}.{sig}") is None
    assert sessions.verify_session_token("not-even-a-token") is None
    assert sessions.verify_session_token(None) is None
    assert sessions.verify_session_token("") is None


def test_session_token_expiry(monkeypatch) -> None:
    monkeypatch.setattr(sessions, "SESSION_TTL_SECONDS", -1)
    token = sessions.make_session_token("user-abc")  # already expired at mint time
    assert sessions.verify_session_token(token) is None


def test_session_secret_persisted_in_data_dir(tmp_path, monkeypatch) -> None:
    from despereaux.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "session_secret", None)
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(sessions, "_cached_secret", None)

    secret_file = tmp_path / "session-secret"
    token = sessions.make_session_token("user-persist")
    assert secret_file.exists()  # generated + persisted on first use

    # A "restarted process" (cache cleared) reads the same secret back, so the
    # token stays valid across restarts.
    monkeypatch.setattr(sessions, "_cached_secret", None)
    assert sessions.verify_session_token(token) == "user-persist"
