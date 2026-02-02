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
