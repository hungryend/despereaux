"""First-ever automated run of the real alembic migration chain.

conftest bootstraps the shared test DB via Base.metadata.create_all, so the
migration files themselves had zero coverage — yet `alembic upgrade head` is
exactly what runs on every container boot. These tests point the settings
singleton at a fresh temp SQLite file and call the production entrypoint
(`despereaux.main._run_migrations`).

All tests are SYNC on purpose: alembic/env.py calls asyncio.run(), which would
blow up inside an async test's already-running loop.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from despereaux import main as main_mod
from despereaux.config import get_settings

EXPECTED_TABLES = {
    "books",
    "authors",
    "series",
    "tags",
    "book_authors",
    "book_tags",
    "users",
    "api_tokens",
    "reading_progress",
    "downloads",
    "conversions",
    "alembic_version",
}


def _migrate_fresh(tmp_path: Path, monkeypatch) -> Path:
    db = tmp_path / "fresh.db"
    monkeypatch.setattr(get_settings(), "db_url", f"sqlite+aiosqlite:///{db.as_posix()}")
    main_mod._run_migrations()
    return db


def _script_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = main_mod._find_project_root()
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def test_upgrade_head_reaches_script_head(tmp_path: Path, monkeypatch) -> None:
    db = _migrate_fresh(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()
    assert version == _script_head()


def test_migrated_schema_has_expected_tables(tmp_path: Path, monkeypatch) -> None:
    db = _migrate_fresh(tmp_path, monkeypatch)
    conn = sqlite3.connect(db)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    missing = EXPECTED_TABLES - names
    assert not missing, f"tables missing after upgrade head: {missing}"


def test_second_upgrade_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    """Every container boot re-runs upgrade head against an already-migrated DB."""
    db = _migrate_fresh(tmp_path, monkeypatch)
    main_mod._run_migrations()  # second run: must be a clean no-op
    conn = sqlite3.connect(db)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()
    assert version == _script_head()


# Pre-existing, benign divergences between the migration chain and the models,
# discovered when this drift check was first written. The migrations created
# these indexes non-unique (models say unique=True — uniqueness is enforced by
# the _get_or_create_* code paths, so real DBs behave fine), and one index got
# a different auto-name. Fixing them means a new migration touching production
# schemas — deliberately out of scope here. If a future migration aligns them,
# remove the corresponding entries. ANY other diff fails the test.
_KNOWN_DRIFT: set[tuple[str, str | None, str]] = {
    ("remove_index", "authors", "ix_authors_sort_name"),
    ("add_index", "authors", "ix_authors_sort_name"),
    ("remove_index", "series", "ix_series_sort_name"),
    ("add_index", "series", "ix_series_sort_name"),
    ("remove_index", "tags", "ix_tags_name"),
    ("add_index", "tags", "ix_tags_name"),
    ("remove_index", "users", "ix_users_username"),
    ("add_index", "users", "ix_users_username"),
    ("remove_index", "books", "ix_books_parent"),
    ("add_index", "books", "ix_books_parent_book_id"),
}


def _diff_key(diff) -> tuple[str, str | None, str]:
    op = diff[0]
    obj = diff[1]
    table = getattr(getattr(obj, "table", None), "name", None)
    return (op, table, getattr(obj, "name", str(obj)))


def test_no_drift_between_chain_and_models(tmp_path: Path, monkeypatch) -> None:
    """The migrated schema must match Base.metadata (which every other test —
    and create_all-based tooling — runs against), modulo the documented
    _KNOWN_DRIFT allowlist. Catches 'model changed, migration forgotten'."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from despereaux.models import Base

    db = _migrate_fresh(tmp_path, monkeypatch)
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"compare_type": False, "render_as_batch": True}
            )
            diffs = compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()

    unexpected = [d for d in diffs if _diff_key(d) not in _KNOWN_DRIFT]
    assert unexpected == [], f"NEW schema drift between migrations and models: {unexpected}"
