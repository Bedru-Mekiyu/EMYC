from functools import lru_cache
from typing import List, Literal, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    PROJECT_NAME: str = "Telegram Competitive Exam Platform"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # Database (Supabase PostgreSQL / asyncpg)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/competition_db"
    DATABASE_SYNC_URL: Optional[str] = None
    TEST_DATABASE_URL: str = "postgresql+asyncpg://postgres@localhost:5433/competition_test_db"

    # Competition Policies
    MANUAL_EARLY_CLOSURE_POLICY: Literal["truncate_to_close_time", "allow_in_progress_to_finish"] = "truncate_to_close_time"

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = "mock_token_for_tests"
    BOT_MODE: Literal["polling", "webhook", "disabled"] = "polling"
    WEBHOOK_URL: Optional[str] = None
    WEBHOOK_SECRET: Optional[str] = None

    # Administrative Telegram user IDs
    ADMIN_TELEGRAM_IDS: str = ""

    # Membership Verification Adapter
    MEMBERSHIP_ADAPTER_TYPE: Literal["mock", "http"] = "mock"
    MEMBERSHIP_API_URL: Optional[str] = None
    MEMBERSHIP_API_KEY: Optional[str] = None
    MEMBERSHIP_REGEX: str = r"^EMYC/\d{5,10}/\d{4}$"

    # Localization
    DEFAULT_LANGUAGE: str = "en"
    SUPPORTED_LANGUAGES: List[str] = ["en", "am", "om", "ar"]

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def detect_render_environment(cls, v: Optional[str]) -> str:
        import os
        if os.getenv("RENDER") == "true" or os.getenv("RENDER_SERVICE_ID"):
            return v if (v and v != "development") else "production"
        return v or "development"

    @field_validator("BOT_MODE", mode="before")
    @classmethod
    def detect_render_bot_mode(cls, v: Optional[str]) -> str:
        import os
        if os.getenv("RENDER") == "true" or os.getenv("RENDER_SERVICE_ID"):
            return v if (v and v != "polling") else "webhook"
        return v or "polling"

    @field_validator("DATABASE_URL", "TEST_DATABASE_URL", mode="before")
    @classmethod
    def assemble_async_db_url(cls, v: Optional[str]) -> Optional[str]:
        """Automatically normalizes postgresql:// and postgres:// to postgresql+asyncpg:// for Supabase/Render."""
        if not v or not isinstance(v, str):
            return v
        v = v.strip().strip("'\"")
        if "sslmode=" in v:
            v = (
                v.replace("sslmode=require", "ssl=require")
                .replace("sslmode=prefer", "ssl=prefer")
                .replace("sslmode=disable", "ssl=disable")
            )
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @property
    def admin_ids(self) -> List[int]:
        """Parsed list of admin telegram user IDs."""
        if not self.ADMIN_TELEGRAM_IDS:
            return []
        ids = []
        for raw in self.ADMIN_TELEGRAM_IDS.split(","):
            raw_stripped = raw.strip()
            if raw_stripped.isdigit():
                ids.append(int(raw_stripped))
        return ids

    def is_admin(self, telegram_user_id: int) -> bool:
        """Determines if a given Telegram user ID is authorized as an administrator."""
        return telegram_user_id in self.admin_ids


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
