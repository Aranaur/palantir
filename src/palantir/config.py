from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram Userbot (Telethon) ---
    tg_api_id: int
    tg_api_hash: str
    tg_session_name: str = "palantir_scraper"
    tg_channels: list[str] = Field(
        default_factory=list,
        description="List of Telegram channel usernames or IDs to monitor",
    )

    # --- RSS ---
    rss_feeds: list[str] = Field(
        default_factory=list,
        description="List of RSS feed URLs to monitor",
    )

    # --- Google Gemini ---
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash"

    # --- Telegram Bot (aiogram) ---
    bot_token: str
    admin_id: int

    # --- Pipeline ---
    score_threshold: int = 6
    poll_interval_seconds: int = 300
    scrape_limit: int = 50

    # --- Database ---
    db_path: str = str(BASE_DIR / "data" / "palantir.db")


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
