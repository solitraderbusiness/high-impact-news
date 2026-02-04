"""
Database helper functions for CRUD operations.
"""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import Session, joinedload

from radar.models import (
    WatchItem, Source, SourceState, Event, Detection, AlertSent, AppSettings,
    watch_item_sources, WatchItemCategory, SourceType
)
from radar.schemas import (
    WatchItemCreate, WatchItemUpdate,
    SourceCreate, SourceUpdate,
    SettingsUpdate
)
from radar.config import get_settings


# =============================================================================
# Watch Items
# =============================================================================

def get_watch_items(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    active_only: bool = False,
    category: Optional[WatchItemCategory] = None,
) -> List[WatchItem]:
    """Get watch items with optional filtering."""
    query = select(WatchItem)

    if active_only:
        query = query.where(WatchItem.is_active == True)

    if category:
        query = query.where(WatchItem.category == category)

    query = query.order_by(WatchItem.name).offset(skip).limit(limit)
    return list(db.execute(query).scalars().all())


def get_watch_item(db: Session, watch_item_id: int) -> Optional[WatchItem]:
    """Get a single watch item by ID."""
    query = (
        select(WatchItem)
        .options(joinedload(WatchItem.sources))
        .where(WatchItem.id == watch_item_id)
    )
    return db.execute(query).scalars().first()


def create_watch_item(db: Session, data: WatchItemCreate) -> WatchItem:
    """Create a new watch item."""
    watch_item = WatchItem(
        name=data.name,
        category=data.category,
        description=data.description,
        keywords=data.keywords,
        entities=data.entities,
        severity_rules=data.severity_rules,
        assets_affected=data.assets_affected,
        cooldown_minutes=data.cooldown_minutes,
        is_active=data.is_active,
    )

    # Add sources if specified
    if data.source_ids:
        sources = db.execute(
            select(Source).where(Source.id.in_(data.source_ids))
        ).scalars().all()
        watch_item.sources = list(sources)

    db.add(watch_item)
    db.commit()
    db.refresh(watch_item)
    return watch_item


def update_watch_item(
    db: Session,
    watch_item_id: int,
    data: WatchItemUpdate
) -> Optional[WatchItem]:
    """Update a watch item."""
    watch_item = db.get(WatchItem, watch_item_id)
    if not watch_item:
        return None

    update_data = data.model_dump(exclude_unset=True, exclude={"source_ids"})
    for field, value in update_data.items():
        setattr(watch_item, field, value)

    # Update sources if specified
    if data.source_ids is not None:
        sources = db.execute(
            select(Source).where(Source.id.in_(data.source_ids))
        ).scalars().all()
        watch_item.sources = list(sources)

    db.commit()
    db.refresh(watch_item)
    return watch_item


def delete_watch_item(db: Session, watch_item_id: int) -> bool:
    """Delete a watch item."""
    watch_item = db.get(WatchItem, watch_item_id)
    if not watch_item:
        return False

    db.delete(watch_item)
    db.commit()
    return True


# =============================================================================
# Sources
# =============================================================================

def get_sources(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    active_only: bool = False,
    source_type: Optional[SourceType] = None,
    global_only: bool = False,
) -> List[Source]:
    """Get sources with optional filtering."""
    query = select(Source).options(joinedload(Source.state))

    if active_only:
        query = query.where(Source.is_active == True)

    if source_type:
        query = query.where(Source.source_type == source_type)

    if global_only:
        query = query.where(Source.is_global == True)

    query = query.order_by(Source.name).offset(skip).limit(limit)
    result = db.execute(query).unique().scalars().all()
    return list(result)


def get_source(db: Session, source_id: int) -> Optional[Source]:
    """Get a single source by ID."""
    query = (
        select(Source)
        .options(joinedload(Source.watch_items), joinedload(Source.state))
        .where(Source.id == source_id)
    )
    return db.execute(query).unique().scalars().first()


def get_source_by_url(db: Session, url: str) -> Optional[Source]:
    """Get a source by URL."""
    query = select(Source).where(Source.url == url)
    return db.execute(query).scalars().first()


def create_source(db: Session, data: SourceCreate) -> Source:
    """Create a new source."""
    source = Source(
        name=data.name,
        url=data.url,
        source_type=data.source_type,
        tier=data.tier,
        is_global=data.is_global,
        is_active=data.is_active,
        poll_interval_override=data.poll_interval_override,
        content_selector=data.content_selector,
    )

    # Add watch items if specified
    if data.watch_item_ids:
        watch_items = db.execute(
            select(WatchItem).where(WatchItem.id.in_(data.watch_item_ids))
        ).scalars().all()
        source.watch_items = list(watch_items)

    db.add(source)
    db.commit()
    db.refresh(source)

    # Create source state
    state = SourceState(source_id=source.id)
    db.add(state)
    db.commit()

    return source


def update_source(
    db: Session,
    source_id: int,
    data: SourceUpdate
) -> Optional[Source]:
    """Update a source."""
    source = db.get(Source, source_id)
    if not source:
        return None

    update_data = data.model_dump(exclude_unset=True, exclude={"watch_item_ids"})
    for field, value in update_data.items():
        setattr(source, field, value)

    # Update watch items if specified
    if data.watch_item_ids is not None:
        watch_items = db.execute(
            select(WatchItem).where(WatchItem.id.in_(data.watch_item_ids))
        ).scalars().all()
        source.watch_items = list(watch_items)

    db.commit()
    db.refresh(source)
    return source


def delete_source(db: Session, source_id: int) -> bool:
    """Delete a source."""
    source = db.get(Source, source_id)
    if not source:
        return False

    db.delete(source)
    db.commit()
    return True


def update_source_state(
    db: Session,
    source_id: int,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    last_fetched_at: Optional[datetime] = None,
    last_item_published_at: Optional[datetime] = None,
    last_error: Optional[str] = None,
    increment_errors: bool = False,
    reset_errors: bool = False,
) -> None:
    """Update the state for a source."""
    query = select(SourceState).where(SourceState.source_id == source_id)
    state = db.execute(query).scalars().first()

    if not state:
        state = SourceState(source_id=source_id)
        db.add(state)

    if etag is not None:
        state.etag = etag
    if last_modified is not None:
        state.last_modified = last_modified
    if last_fetched_at is not None:
        state.last_fetched_at = last_fetched_at
    if last_item_published_at is not None:
        state.last_item_published_at = last_item_published_at
    if last_error is not None:
        state.last_error = last_error
    if increment_errors:
        state.consecutive_errors += 1
    if reset_errors:
        state.consecutive_errors = 0
        state.last_error = None

    db.commit()


# =============================================================================
# Events
# =============================================================================

def get_events(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    processed_only: bool = False,
    unprocessed_only: bool = False,
    source_id: Optional[int] = None,
) -> List[Event]:
    """Get events with optional filtering."""
    query = select(Event).options(joinedload(Event.source))

    if processed_only:
        query = query.where(Event.is_processed == True)
    elif unprocessed_only:
        query = query.where(Event.is_processed == False)

    if source_id:
        query = query.where(Event.source_id == source_id)

    query = query.order_by(Event.fetched_at.desc()).offset(skip).limit(limit)
    return list(db.execute(query).unique().scalars().all())


def get_event(db: Session, event_id: int) -> Optional[Event]:
    """Get a single event by ID."""
    query = (
        select(Event)
        .options(joinedload(Event.source), joinedload(Event.detections))
        .where(Event.id == event_id)
    )
    return db.execute(query).unique().scalars().first()


def get_event_by_hash(db: Session, content_hash: str) -> Optional[Event]:
    """Get an event by content hash."""
    query = select(Event).where(Event.content_hash == content_hash)
    return db.execute(query).scalars().first()


def create_event(
    db: Session,
    source_id: int,
    url: str,
    title: str,
    content_hash: str,
    title_hash: str,
    excerpt: Optional[str] = None,
    raw_text: Optional[str] = None,
    author: Optional[str] = None,
    language: Optional[str] = None,
    published_at: Optional[datetime] = None,
) -> Event:
    """Create a new event."""
    event = Event(
        source_id=source_id,
        url=url,
        title=title,
        excerpt=excerpt,
        raw_text=raw_text,
        author=author,
        language=language,
        published_at=published_at,
        content_hash=content_hash,
        title_hash=title_hash,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def mark_event_processed(db: Session, event_id: int) -> None:
    """Mark an event as processed."""
    event = db.get(Event, event_id)
    if event:
        event.is_processed = True
        db.commit()


# =============================================================================
# Detections
# =============================================================================

def get_detections(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    alerted_only: bool = False,
    watch_item_id: Optional[int] = None,
) -> List[Detection]:
    """Get detections with optional filtering."""
    query = (
        select(Detection)
        .options(joinedload(Detection.event), joinedload(Detection.watch_item))
    )

    if alerted_only:
        query = query.where(Detection.is_alerted == True)

    if watch_item_id:
        query = query.where(Detection.watch_item_id == watch_item_id)

    query = query.order_by(Detection.created_at.desc()).offset(skip).limit(limit)
    return list(db.execute(query).unique().scalars().all())


def get_detection(db: Session, detection_id: int) -> Optional[Detection]:
    """Get a single detection by ID."""
    query = (
        select(Detection)
        .options(joinedload(Detection.event), joinedload(Detection.watch_item))
        .where(Detection.id == detection_id)
    )
    return db.execute(query).unique().scalars().first()


def create_detection(
    db: Session,
    event_id: int,
    watch_item_id: int,
    match_confidence: float,
    match_method: str,
    trigger_spans: List[str],
    severity_score: int,
    severity_breakdown: Optional[dict] = None,
    llm_reasoning: Optional[str] = None,
    assets_affected: Optional[List[str]] = None,
) -> Detection:
    """Create a new detection."""
    detection = Detection(
        event_id=event_id,
        watch_item_id=watch_item_id,
        match_confidence=match_confidence,
        match_method=match_method,
        trigger_spans=trigger_spans,
        severity_score=severity_score,
        severity_breakdown=severity_breakdown,
        llm_reasoning=llm_reasoning,
        assets_affected=assets_affected or [],
    )
    db.add(detection)
    db.commit()
    db.refresh(detection)
    return detection


def get_last_alert_time(db: Session, watch_item_id: int) -> Optional[datetime]:
    """Get the last time an alert was sent for a watch item."""
    query = (
        select(AlertSent.sent_at)
        .join(Detection)
        .where(
            and_(
                Detection.watch_item_id == watch_item_id,
                AlertSent.is_success == True,
            )
        )
        .order_by(AlertSent.sent_at.desc())
        .limit(1)
    )
    result = db.execute(query).scalars().first()
    return result


def mark_detection_alerted(db: Session, detection_id: int) -> None:
    """Mark a detection as alerted."""
    detection = db.get(Detection, detection_id)
    if detection:
        detection.is_alerted = True
        db.commit()


def suppress_detection(db: Session, detection_id: int, reason: str) -> None:
    """Suppress a detection with a reason."""
    detection = db.get(Detection, detection_id)
    if detection:
        detection.is_suppressed = True
        detection.suppression_reason = reason
        db.commit()


# =============================================================================
# Alerts
# =============================================================================

def get_alerts(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    success_only: bool = False,
) -> List[AlertSent]:
    """Get sent alerts."""
    query = (
        select(AlertSent)
        .options(joinedload(AlertSent.detection).joinedload(Detection.event))
        .options(joinedload(AlertSent.detection).joinedload(Detection.watch_item))
    )

    if success_only:
        query = query.where(AlertSent.is_success == True)

    query = query.order_by(AlertSent.sent_at.desc()).offset(skip).limit(limit)
    return list(db.execute(query).unique().scalars().all())


def create_alert(
    db: Session,
    detection_id: int,
    message_text: str,
    telegram_message_id: Optional[int] = None,
    telegram_chat_id: Optional[str] = None,
    is_success: bool = False,
    error_message: Optional[str] = None,
    retry_count: int = 0,
) -> AlertSent:
    """Create an alert record."""
    alert = AlertSent(
        detection_id=detection_id,
        message_text=message_text,
        telegram_message_id=telegram_message_id,
        telegram_chat_id=telegram_chat_id,
        is_success=is_success,
        error_message=error_message,
        retry_count=retry_count,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def get_failed_alerts(db: Session, limit: int = 100) -> List[AlertSent]:
    """Get failed alerts that can be retried."""
    query = (
        select(AlertSent)
        .options(joinedload(AlertSent.detection).joinedload(Detection.event))
        .options(joinedload(AlertSent.detection).joinedload(Detection.watch_item))
        .where(AlertSent.is_success == False)
        .order_by(AlertSent.sent_at.desc())
        .limit(limit)
    )
    return list(db.execute(query).unique().scalars().all())


def update_alert_status(
    db: Session,
    alert_id: int,
    is_success: bool,
    telegram_message_id: Optional[int] = None,
    error_message: Optional[str] = None,
) -> Optional[AlertSent]:
    """Update alert status after retry."""
    alert = db.get(AlertSent, alert_id)
    if alert:
        alert.is_success = is_success
        alert.retry_count += 1
        if telegram_message_id:
            alert.telegram_message_id = telegram_message_id
        if error_message:
            alert.error_message = error_message
        db.commit()
        db.refresh(alert)
    return alert


# =============================================================================
# App Settings
# =============================================================================

def get_app_setting(db: Session, key: str) -> Optional[str]:
    """Get an app setting value."""
    query = select(AppSettings).where(AppSettings.key == key)
    setting = db.execute(query).scalars().first()
    return setting.value if setting else None


def set_app_setting(db: Session, key: str, value: str, description: Optional[str] = None) -> None:
    """Set an app setting value."""
    query = select(AppSettings).where(AppSettings.key == key)
    setting = db.execute(query).scalars().first()

    if setting:
        setting.value = value
        if description:
            setting.description = description
    else:
        setting = AppSettings(key=key, value=value, description=description)
        db.add(setting)

    db.commit()


def get_all_app_settings(db: Session) -> dict:
    """Get all app settings as a dictionary."""
    query = select(AppSettings)
    settings = db.execute(query).scalars().all()
    return {s.key: s.value for s in settings}


# =============================================================================
# Statistics
# =============================================================================

def get_stats(db: Session) -> dict:
    """Get overall statistics."""
    return {
        "watch_items_count": db.execute(
            select(func.count(WatchItem.id)).where(WatchItem.is_active == True)
        ).scalar(),
        "sources_count": db.execute(
            select(func.count(Source.id)).where(Source.is_active == True)
        ).scalar(),
        "events_count": db.execute(select(func.count(Event.id))).scalar(),
        "detections_count": db.execute(select(func.count(Detection.id))).scalar(),
        "alerts_count": db.execute(
            select(func.count(AlertSent.id)).where(AlertSent.is_success == True)
        ).scalar(),
    }
