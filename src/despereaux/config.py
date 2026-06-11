from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LibraryConfig(BaseModel):
    """A named library and its filesystem root inside the container."""

    name: str
    path: Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DESPEREAUX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # === Libraries (multi-library support) ===
    # Set DESPEREAUX_LIBRARIES as JSON, e.g.:
    #   DESPEREAUX_LIBRARIES='[{"name":"Fiction","path":"/libraries/fiction"},{"name":"D&D","path":"/libraries/dnd"}]'
    # If unset, falls back to a single library named "Default" pointing at
    # `library_path` (the legacy single-library env var).
    libraries: list[LibraryConfig] = []
    library_path: Path = Path("/ebooks")

    data_dir: Path = Path("/config")
    db_url: str = "sqlite+aiosqlite:////config/despereaux.db"

    dev_mode: bool = False
    admin_group: str = "ebook-admin"

    # === Authentication mode ===
    # "authentik": trust X-authentik-* identity headers injected by a reverse
    #   proxy running forward-auth (Authentik/Authelia/Keycloak). The app has
    #   no login page; the proxy is the gate. DEFAULT — existing deployments
    #   keep working unchanged.
    # "native": despereaux's own login page + bcrypt passwords + signed session
    #   cookie. X-authentik-* headers are IGNORED (they'd be spoofable without
    #   a trusted proxy). First run redirects to /setup to create the admin.
    # Per-user API tokens (Authorization: Bearer / despereaux_token cookie)
    # work identically in BOTH modes.
    auth_mode: Literal["authentik", "native"] = "authentik"

    # Secret for signing native-mode session cookies. If unset, one is
    # generated and persisted at {data_dir}/session-secret on first use.
    session_secret: str | None = None

    # Shared token for /api/admin/sync (chaptarr or other internal callers).
    webhook_token: str | None = None

    google_books_api_key: str | None = None
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _resolve_libraries(self) -> Settings:
        if not self.libraries:
            # Legacy: derive single "Default" library from library_path
            self.libraries = [LibraryConfig(name="Default", path=self.library_path)]
        # Deduplicate by name (last wins).
        seen: dict[str, LibraryConfig] = {}
        for lib in self.libraries:
            seen[lib.name] = lib
        self.libraries = list(seen.values())
        return self

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def metadata_cache_dir(self) -> Path:
        return self.data_dir / "metadata-cache"

    @property
    def converted_dir(self) -> Path:
        """Where MOBI/AZW (etc.) converted EPUBs live, keyed by content hash."""
        return self.data_dir / "converted"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        for d in (
            _settings.data_dir,
            _settings.covers_dir,
            _settings.cache_dir,
            _settings.metadata_cache_dir,
            _settings.converted_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
    return _settings
