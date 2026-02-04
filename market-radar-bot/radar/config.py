"""
Configuration management using Pydantic Settings.
Loads from environment variables and .env file.
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="sqlite:///./market_radar.db",
        description="Database connection URL"
    )

    # Admin Authentication
    admin_username: str = Field(default="admin")
    admin_password: str = Field(default="changeme")
    secret_key: str = Field(
        default="change_this_to_a_random_secret_key_at_least_32_chars",
        description="Secret key for session signing"
    )

    # Telegram
    telegram_bot_token: Optional[str] = Field(
        default=None,
        description="Telegram bot token from @BotFather"
    )
    telegram_chat_id: Optional[str] = Field(
        default=None,
        description="Telegram chat ID to send alerts to"
    )

    # OpenRouter LLM (Optional)
    openrouter_api_key: Optional[str] = Field(
        default=None,
        description="OpenRouter API key"
    )
    openrouter_model: str = Field(
        default="anthropic/claude-3-haiku",
        description="OpenRouter model to use"
    )
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter API base URL"
    )

    # Detection Thresholds
    alert_threshold: int = Field(
        default=70,
        ge=0,
        le=100,
        description="Severity score above which immediate alert is sent"
    )
    digest_threshold: int = Field(
        default=30,
        ge=0,
        le=100,
        description="Severity score above which events are included in digest"
    )
    llm_confidence_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Minimum LLM confidence for valid match"
    )

    # Polling & Cooldowns
    poll_interval_seconds: int = Field(
        default=60,
        ge=10,
        description="How often to poll sources (seconds)"
    )
    default_cooldown_minutes: int = Field(
        default=15,
        ge=1,
        description="Default cooldown between alerts for same watch item"
    )
    max_text_length: int = Field(
        default=50000,
        ge=1000,
        description="Max text length to store (characters)"
    )

    # Logging
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # Timezone
    timezone: str = Field(
        default="Asia/Tehran",
        description="Timezone for displaying timestamps (e.g., Asia/Tehran, Europe/Berlin)"
    )

    @property
    def has_telegram(self) -> bool:
        """Check if Telegram credentials are configured."""
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def has_openrouter(self) -> bool:
        """Check if OpenRouter API key is configured."""
        return bool(self.openrouter_api_key)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
