"""
SQLAlchemy ORM models for Market Radar Bot.

Tables:
- watch_items: People, institutions, events, megatrends to monitor
- sources: RSS feeds and web pages to collect from
- watch_item_sources: Many-to-many linking watch items to sources
- events: Normalized collected items (articles, posts, etc.)
- detections: Match results linking events to watch items
- alerts_sent: Log of sent Telegram alerts
- source_state: Tracking fetch state (etags, timestamps)
- settings: Application settings (stored in DB for admin panel)
"""

from datetime import datetime
from typing import Optional, List
import enum

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime,
    ForeignKey, Enum, JSON, UniqueConstraint, Index, Table
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from radar.db import Base


class WatchItemCategory(str, enum.Enum):
    """Categories for watch items."""
    PERSON = "person"
    INSTITUTION = "institution"
    CENTRAL_BANK = "central_bank"
    EVENT_TYPE = "event_type"
    MACRO_RELEASE = "macro_release"
    MEGATREND = "megatrend"


class SourceType(str, enum.Enum):
    """Types of sources."""
    RSS = "rss"
    WEB = "web"
    TELEGRAM = "telegram"


class SourceTier(str, enum.Enum):
    """Source tier for scoring."""
    PRIMARY = "primary"      # Official sources, major wire services
    SECONDARY = "secondary"  # Major news outlets
    SOCIAL = "social"        # Social media, blogs


# Many-to-many association table for watch_items <-> sources
watch_item_sources = Table(
    "watch_item_sources",
    Base.metadata,
    Column("watch_item_id", Integer, ForeignKey("watch_items.id", ondelete="CASCADE"), primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True),
)


class WatchItem(Base):
    """
    A person, institution, event type, or megatrend to monitor.
    """
    __tablename__ = "watch_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[WatchItemCategory] = mapped_column(
        Enum(WatchItemCategory),
        nullable=False,
        index=True
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Keywords and entities for rule-based matching (JSON array)
    keywords: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, default=list)
    # Entities to look for (aliases, variations)
    entities: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, default=list)

    # Severity rules: list of {pattern: str, score_bump: int}
    severity_rules: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, default=list)

    # Assets potentially affected by this watch item (JSON array)
    assets_affected: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, default=list)

    # Alert configuration
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=15)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # Relationships
    sources: Mapped[List["Source"]] = relationship(
        "Source",
        secondary=watch_item_sources,
        back_populates="watch_items"
    )
    detections: Mapped[List["Detection"]] = relationship(
        "Detection",
        back_populates="watch_item",
        cascade="all, delete-orphan"
    )


class Source(Base):
    """
    A data source: RSS feed or web page.
    Can be linked to specific watch items or be global.
    """
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), nullable=False)
    tier: Mapped[SourceTier] = mapped_column(
        Enum(SourceTier),
        default=SourceTier.SECONDARY
    )

    # Whether this is a global source (scanned for all watch items)
    is_global: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    # Polling configuration
    poll_interval_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Web scraping hints (for web sources)
    content_selector: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # Relationships
    watch_items: Mapped[List["WatchItem"]] = relationship(
        "WatchItem",
        secondary=watch_item_sources,
        back_populates="sources"
    )
    events: Mapped[List["Event"]] = relationship(
        "Event",
        back_populates="source",
        cascade="all, delete-orphan"
    )
    state: Mapped[Optional["SourceState"]] = relationship(
        "SourceState",
        back_populates="source",
        uselist=False,
        cascade="all, delete-orphan"
    )


class SourceState(Base):
    """
    Tracks the fetch state for each source (etag, last-modified, timestamps).
    """
    __tablename__ = "source_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sources.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # HTTP caching headers
    etag: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_modified: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Timestamps
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_item_published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Error tracking
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    source: Mapped["Source"] = relationship("Source", back_populates="state")


class Event(Base):
    """
    A normalized collected item (article, news piece, etc.).
    """
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    excerpt: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Metadata
    author: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Hashes for deduplication
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Processing status
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Unique constraint on content hash
    __table_args__ = (
        UniqueConstraint("content_hash", name="uq_event_content_hash"),
        Index("ix_events_processed_fetched", "is_processed", "fetched_at"),
    )

    # Relationships
    source: Mapped["Source"] = relationship("Source", back_populates="events")
    detections: Mapped[List["Detection"]] = relationship(
        "Detection",
        back_populates="event",
        cascade="all, delete-orphan"
    )


class Detection(Base):
    """
    A match result linking an event to a watch item.
    """
    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    watch_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("watch_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Match details
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    match_method: Mapped[str] = mapped_column(String(50), nullable=False)  # "rules" or "llm"

    # Trigger spans: exact quotes that triggered the match (JSON array)
    trigger_spans: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, default=list)

    # LLM-specific fields
    llm_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assets_affected: Mapped[Optional[str]] = mapped_column(JSON, nullable=True, default=list)

    # Scoring
    severity_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    severity_breakdown: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)

    # Status
    is_alerted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    suppression_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("event_id", "watch_item_id", name="uq_detection_event_watch"),
    )

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="detections")
    watch_item: Mapped["WatchItem"] = relationship("WatchItem", back_populates="detections")
    alerts: Mapped[List["AlertSent"]] = relationship(
        "AlertSent",
        back_populates="detection",
        cascade="all, delete-orphan"
    )


class AlertSent(Base):
    """
    Log of sent Telegram alerts.
    """
    __tablename__ = "alerts_sent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detection_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("detections.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Telegram message details
    telegram_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Message content (for audit)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Status
    is_success: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    detection: Mapped["Detection"] = relationship("Detection", back_populates="alerts")


class AppSettings(Base):
    """
    Application settings stored in database for admin panel access.
    """
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )


# =============================================================================
# LEARNING SYSTEM MODELS
# =============================================================================

class ImpactStatus(str, enum.Enum):
    """Status of market impact measurement."""
    PENDING = "pending"          # Waiting for price data
    MEASURED = "measured"        # Impact calculated
    NO_DATA = "no_data"          # Could not get price data
    INSUFFICIENT = "insufficient"  # Not enough data points


class MarketImpact(Base):
    """
    Tracks actual market impact after an alert is sent.
    Used to learn which sources/alerts actually move markets.
    """
    __tablename__ = "market_impacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detection_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("detections.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # The asset being tracked (e.g., "EURUSD", "SPX", "GOLD")
    asset_symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Price at alert time (T+0)
    price_at_alert: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    alert_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Price changes at various intervals (percentage)
    change_5min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_15min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_1hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_4hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_24hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Absolute prices at intervals
    price_5min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_15min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_1hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_4hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_24hr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Volatility metrics
    max_move_up: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # Max % up in 24hr
    max_move_down: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # Max % down in 24hr

    # Computed impact score (0-100, based on actual price movement)
    actual_impact_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Did this alert predict correctly? (high severity = significant move)
    was_accurate: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Status
    status: Mapped[ImpactStatus] = mapped_column(
        Enum(ImpactStatus),
        default=ImpactStatus.PENDING
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    measured_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_market_impact_detection_asset", "detection_id", "asset_symbol"),
    )

    # Relationships
    detection: Mapped["Detection"] = relationship("Detection", backref="market_impacts")


class SourceReliability(Base):
    """
    Tracks reliability metrics for each source over time.
    Used to dynamically adjust source tier/scoring.
    """
    __tablename__ = "source_reliability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("sources.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # Alert statistics
    total_alerts: Mapped[int] = mapped_column(Integer, default=0)
    high_severity_alerts: Mapped[int] = mapped_column(Integer, default=0)  # Score >= 70

    # Accuracy metrics
    accurate_predictions: Mapped[int] = mapped_column(Integer, default=0)
    inaccurate_predictions: Mapped[int] = mapped_column(Integer, default=0)
    pending_measurements: Mapped[int] = mapped_column(Integer, default=0)

    # Computed reliability score (0.0 - 1.0)
    # = accurate_predictions / (accurate + inaccurate) with smoothing
    reliability_score: Mapped[float] = mapped_column(Float, default=0.5)

    # Dynamic tier adjustment (-20 to +20 points added to severity)
    tier_adjustment: Mapped[int] = mapped_column(Integer, default=0)

    # Average actual impact of alerts from this source
    avg_actual_impact: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # False positive rate (high score but no impact)
    false_positive_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Statistics by time of day (JSON: {hour: {alerts: N, accuracy: X}})
    hourly_stats: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)

    # Last time metrics were recalculated
    last_calculated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Minimum alerts needed before adjusting tier
    min_alerts_for_adjustment: Mapped[int] = mapped_column(Integer, default=10)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # Relationships
    source: Mapped["Source"] = relationship("Source", backref="reliability")


class WatchItemHistory(Base):
    """
    Historical record of all news and their effects for a watch item.
    Designed for future ML training on price prediction.
    """
    __tablename__ = "watch_item_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watch_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("watch_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    detection_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("detections.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Snapshot of the news at the time
    event_title: Mapped[str] = mapped_column(String(1024), nullable=False)
    event_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_tier: Mapped[str] = mapped_column(String(50), nullable=False)

    # Detection details
    severity_score: Mapped[int] = mapped_column(Integer, nullable=False)
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    trigger_quotes: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)  # The citation quotes
    matched_keywords: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)

    # Timing
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Market impact summary (JSON with all assets and their impacts)
    # Format: {"EURUSD": {"5min": 0.05, "1hr": 0.12, ...}, "GOLD": {...}}
    market_impacts: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)

    # Overall impact assessment
    had_significant_impact: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    impact_direction: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # "up", "down", "mixed", "none"

    # For ML features
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Monday
    hour_of_day: Mapped[int] = mapped_column(Integer, nullable=False)
    is_market_hours: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_history_watch_item_date", "watch_item_id", "detected_at"),
    )

    # Relationships
    watch_item: Mapped["WatchItem"] = relationship("WatchItem", backref="history")
    detection: Mapped["Detection"] = relationship("Detection", backref="history_entry")


class SourcePoolStatus(str, enum.Enum):
    """Status of a source in the pool."""
    CANDIDATE = "candidate"      # Discovered, not yet validated
    VALIDATING = "validating"    # Currently being tested
    APPROVED = "approved"        # Validated, can be activated
    REJECTED = "rejected"        # Tested and found unsuitable
    ACTIVE = "active"            # Currently in use (promoted to sources table)
    RETIRED = "retired"          # Was active, now removed


class SourcePool(Base):
    """
    Pool of potential sources that can be activated.
    Used for automatic source discovery and replacement.
    """
    __tablename__ = "source_pool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Source info
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), nullable=False)
    suggested_tier: Mapped[SourceTier] = mapped_column(
        Enum(SourceTier),
        default=SourceTier.SECONDARY
    )

    # Status
    status: Mapped[SourcePoolStatus] = mapped_column(
        Enum(SourcePoolStatus),
        default=SourcePoolStatus.CANDIDATE,
        index=True
    )

    # Discovery info
    discovered_via: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # "manual", "web_search", "link_extraction"
    discovered_from_source_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # If found via another source

    # Related topics (JSON array of watch item names/categories this might cover)
    related_topics: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)

    # Validation results
    validation_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    validation_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    validation_articles_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_matches_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # If promoted to active source
    promoted_source_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True
    )
    promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # If retired/rejected
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retired_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )


class PriceData(Base):
    """
    Historical price data for assets.
    Used to calculate market impact.
    """
    __tablename__ = "price_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Asset identifier (e.g., "EURUSD", "SPX", "GOLD", "BTC")
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Price and time
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)

    # Optional OHLCV data
    open: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Data source
    data_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint("symbol", "timestamp", name="uq_price_symbol_time"),
        Index("ix_price_symbol_timestamp", "symbol", "timestamp"),
    )


class LearningConfig(Base):
    """
    Configuration for the learning system.
    """
    __tablename__ = "learning_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )


class APIUsageLog(Base):
    """
    Tracks API usage and costs for OpenRouter/LLM calls.
    Used for monitoring spending on API services.
    """
    __tablename__ = "api_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # API call details
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # "openrouter"
    model: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # "analysis", "translation", "summary"

    # Token counts
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Cost in USD (from OpenRouter response)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Response time in milliseconds
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Success/failure
    is_success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_api_usage_created_at", "created_at"),
        Index("ix_api_usage_provider_model", "provider", "model"),
        Index("ix_api_usage_purpose_date", "purpose", "created_at"),
    )
