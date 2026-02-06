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
        description="OpenRouter model to use for analysis"
    )
    translation_model: str = Field(
        default="anthropic/claude-3-haiku",
        description="OpenRouter model to use for Persian translation"
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


def get_settings_with_db_overrides(db_settings: dict) -> Settings:
    """
    Get settings with database overrides applied.

    Args:
        db_settings: Dictionary of settings from database (key -> value strings)

    Returns:
        Settings instance with database values overriding env values
    """
    base = get_settings()

    # Create a copy of settings as dict
    settings_dict = {
        "database_url": base.database_url,
        "admin_username": base.admin_username,
        "admin_password": base.admin_password,
        "secret_key": base.secret_key,
        "telegram_bot_token": db_settings.get("telegram_bot_token") or base.telegram_bot_token,
        "telegram_chat_id": db_settings.get("telegram_chat_id") or base.telegram_chat_id,
        "openrouter_api_key": db_settings.get("openrouter_api_key") or base.openrouter_api_key,
        "openrouter_model": db_settings.get("openrouter_model", base.openrouter_model),
        "translation_model": db_settings.get("translation_model", base.translation_model),
        "openrouter_base_url": base.openrouter_base_url,
        "alert_threshold": int(db_settings.get("alert_threshold", base.alert_threshold)),
        "digest_threshold": int(db_settings.get("digest_threshold", base.digest_threshold)),
        "llm_confidence_threshold": float(db_settings.get("llm_confidence_threshold", base.llm_confidence_threshold)),
        "poll_interval_seconds": int(db_settings.get("poll_interval_seconds", base.poll_interval_seconds)),
        "default_cooldown_minutes": int(db_settings.get("default_cooldown_minutes", base.default_cooldown_minutes)),
        "max_text_length": base.max_text_length,
        "log_level": base.log_level,
        "log_format": base.log_format,
        "host": base.host,
        "port": base.port,
        "timezone": base.timezone,
    }

    return Settings(**settings_dict)


def get_db_setting(db, key: str, default=None):
    """
    Get a single setting value, checking database first, then env.

    Args:
        db: Database session
        key: Setting key
        default: Default value if not found

    Returns:
        Setting value
    """
    from radar.storage import get_app_setting

    db_value = get_app_setting(db, key)
    if db_value is not None:
        return db_value

    base = get_settings()
    return getattr(base, key, default)
