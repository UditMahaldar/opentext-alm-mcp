from __future__ import annotations

import functools

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    base_url: str
    username: str
    password: str
    domain: str
    project: str
    # Seconds to wait between API calls — ALM servers can be slow to process
    request_delay: float = 2.0

    model_config = SettingsConfigDict(
        env_prefix="ALM_",
        env_file=".env",
        env_file_encoding="utf-8",
    )


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (loaded on first call)."""
    return Settings()
