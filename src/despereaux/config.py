from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DESPEREAUX_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    library_path: Path = Path("/ebooks")
    data_dir: Path = Path("/config")
    db_url: str = "sqlite+aiosqlite:////config/despereaux.db"

    dev_mode: bool = False
    admin_group: str = "ebook-admin"

    google_books_api_key: str | None = None
    log_level: str = "INFO"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def metadata_cache_dir(self) -> Path:
        return self.data_dir / "metadata-cache"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        for d in (_settings.data_dir, _settings.covers_dir, _settings.cache_dir, _settings.metadata_cache_dir):
            d.mkdir(parents=True, exist_ok=True)
    return _settings
