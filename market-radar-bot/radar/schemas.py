"""
Pydantic schemas for API request/response validation.
"""

from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel, Field, ConfigDict

from radar.models import WatchItemCategory, SourceType, SourceTier


# =============================================================================
# Watch Item Schemas
# =============================================================================

class WatchItemBase(BaseModel):
    """Base schema for watch items."""
    name: str = Field(..., min_length=1, max_length=255)
    category: WatchItemCategory
    description: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    entities: List[str] = Field(default_factory=list)
    severity_rules: List[dict] = Field(default_factory=list)
    assets_affected: List[str] = Field(default_factory=list)
    cooldown_minutes: int = Field(default=15, ge=1)
    is_active: bool = True


class WatchItemCreate(WatchItemBase):
    """Schema for creating a watch item."""
    source_ids: List[int] = Field(default_factory=list)


class WatchItemUpdate(BaseModel):
    """Schema for updating a watch item."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    category: Optional[WatchItemCategory] = None
    description: Optional[str] = None
    keywords: Optional[List[str]] = None
    entities: Optional[List[str]] = None
    severity_rules: Optional[List[dict]] = None
    assets_affected: Optional[List[str]] = None
    cooldown_minutes: Optional[int] = Field(None, ge=1)
    is_active: Optional[bool] = None
    source_ids: Optional[List[int]] = None


class WatchItemResponse(WatchItemBase):
    """Schema for watch item response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class WatchItemDetailResponse(WatchItemResponse):
    """Detailed watch item response with related sources."""
    sources: List["SourceResponse"] = Field(default_factory=list)


# =============================================================================
# Source Schemas
# =============================================================================

class SourceBase(BaseModel):
    """Base schema for sources."""
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1, max_length=2048)
    source_type: SourceType
    tier: SourceTier = SourceTier.SECONDARY
    is_global: bool = False
    is_active: bool = True
    poll_interval_override: Optional[int] = None
    content_selector: Optional[str] = None


class SourceCreate(SourceBase):
    """Schema for creating a source."""
    watch_item_ids: List[int] = Field(default_factory=list)


class SourceUpdate(BaseModel):
    """Schema for updating a source."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    url: Optional[str] = Field(None, min_length=1, max_length=2048)
    source_type: Optional[SourceType] = None
    tier: Optional[SourceTier] = None
    is_global: Optional[bool] = None
    is_active: Optional[bool] = None
    poll_interval_override: Optional[int] = None
    content_selector: Optional[str] = None
    watch_item_ids: Optional[List[int]] = None


class SourceResponse(SourceBase):
    """Schema for source response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class SourceDetailResponse(SourceResponse):
    """Detailed source response with state info."""
    last_fetched_at: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_errors: int = 0
    watch_items: List[WatchItemResponse] = Field(default_factory=list)


# =============================================================================
# Event Schemas
# =============================================================================

class EventResponse(BaseModel):
    """Schema for event response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    url: str
    title: str
    excerpt: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    published_at: Optional[datetime] = None
    fetched_at: datetime
    is_processed: bool


class EventDetailResponse(EventResponse):
    """Detailed event response with source info."""
    source_name: Optional[str] = None
    raw_text: Optional[str] = None
    detections: List["DetectionResponse"] = Field(default_factory=list)


# =============================================================================
# Detection Schemas
# =============================================================================

class DetectionResponse(BaseModel):
    """Schema for detection response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: int
    watch_item_id: int
    match_confidence: float
    match_method: str
    trigger_spans: List[str] = Field(default_factory=list)
    llm_reasoning: Optional[str] = None
    assets_affected: List[str] = Field(default_factory=list)
    severity_score: int
    severity_breakdown: Optional[dict] = None
    is_alerted: bool
    is_suppressed: bool
    suppression_reason: Optional[str] = None
    created_at: datetime


class DetectionDetailResponse(DetectionResponse):
    """Detailed detection with event and watch item info."""
    event_title: Optional[str] = None
    event_url: Optional[str] = None
    watch_item_name: Optional[str] = None
    watch_item_category: Optional[str] = None


# =============================================================================
# Alert Schemas
# =============================================================================

class AlertResponse(BaseModel):
    """Schema for alert response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    detection_id: int
    telegram_message_id: Optional[int] = None
    telegram_chat_id: Optional[str] = None
    message_text: str
    is_success: bool
    error_message: Optional[str] = None
    retry_count: int
    sent_at: datetime


class AlertDetailResponse(AlertResponse):
    """Detailed alert with detection info."""
    event_title: Optional[str] = None
    watch_item_name: Optional[str] = None
    severity_score: Optional[int] = None


# =============================================================================
# Settings Schemas
# =============================================================================

class SettingsResponse(BaseModel):
    """Schema for settings response."""
    alert_threshold: int
    digest_threshold: int
    poll_interval_seconds: int
    default_cooldown_minutes: int
    llm_confidence_threshold: float
    has_telegram: bool
    has_openrouter: bool


class SettingsUpdate(BaseModel):
    """Schema for updating settings."""
    alert_threshold: Optional[int] = Field(None, ge=0, le=100)
    digest_threshold: Optional[int] = Field(None, ge=0, le=100)
    poll_interval_seconds: Optional[int] = Field(None, ge=10)
    default_cooldown_minutes: Optional[int] = Field(None, ge=1)
    llm_confidence_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)


# =============================================================================
# Authentication Schemas
# =============================================================================

class LoginRequest(BaseModel):
    """Schema for login request."""
    username: str
    password: str


class LoginResponse(BaseModel):
    """Schema for login response."""
    success: bool
    message: str


# =============================================================================
# Pipeline Schemas
# =============================================================================

class CollectionResult(BaseModel):
    """Result of a collection run."""
    source_id: int
    source_name: str
    items_collected: int
    errors: List[str] = Field(default_factory=list)


class DetectionResult(BaseModel):
    """Result of detection on an event."""
    event_id: int
    watch_item_id: int
    match_confidence: float
    severity_score: int
    trigger_spans: List[str]
    is_alerted: bool


class PipelineRunResult(BaseModel):
    """Result of a full pipeline run."""
    started_at: datetime
    completed_at: datetime
    collections: List[CollectionResult]
    detections: List[DetectionResult]
    alerts_sent: int
    errors: List[str]


# Update forward references
WatchItemDetailResponse.model_rebuild()
SourceDetailResponse.model_rebuild()
EventDetailResponse.model_rebuild()
