"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str
    telegram_allowed_user_ids: str = Field(description="Comma-separated Telegram user IDs")

    llm_base_url: str = "http://ollama:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str
    # Name exactly as it appears on your bank/e-wallet accounts — lets the
    # extraction prompt tell Pemasukan/Pengeluaran apart on transfer
    # receipts via "Rekening Sumber"/"Nama Penerima". Optional.
    account_holder_name: str = ""

    google_credentials_dir: str = "/app/credentials"
    google_spreadsheet_id: str
    google_drive_folder_id: str

    timezone: str = "Asia/Jakarta"
    log_level: str = "INFO"

    @property
    def allowed_user_ids(self) -> set[int]:
        return {
            int(raw.strip()) for raw in self.telegram_allowed_user_ids.split(",") if raw.strip()
        }


def load_settings() -> Settings:
    return Settings()
