"""
Admin panel routes with Jinja2 templates.
"""

import json
from datetime import datetime
from typing import Optional
from pathlib import Path

import pytz
from fastapi import APIRouter, Depends, Form, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from radar.db import get_db
from radar import storage
from radar.auth import (
    verify_credentials, set_session_cookie, clear_session_cookie,
    get_current_session, require_auth_redirect
)
from radar.config import get_settings
from radar.models import WatchItemCategory, SourceType, SourceTier, AlertSent, Event, WatchItem, Source
from radar.schemas import WatchItemCreate, WatchItemUpdate, SourceCreate, SourceUpdate
from radar.notify.telegram import TelegramNotifier
from radar.learning import (
    ImpactTracker,
    ReliabilityScorer,
    HistoryRecorder,
    SourceDiscovery,
)

router = APIRouter(prefix="/admin", tags=["admin"])

# Set up templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


# Add custom Jinja2 filter for timezone conversion
def to_tehran_time(dt: Optional[datetime]) -> str:
    """Convert datetime to Tehran timezone string."""
    if not dt:
        return "-"
    try:
        tehran_tz = pytz.timezone("Asia/Tehran")
        # Assume UTC if no timezone info
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        tehran_time = dt.astimezone(tehran_tz)
        return tehran_time.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M") if dt else "-"


# Register the filter with Jinja2
templates.env.filters["to_tehran"] = to_tehran_time


def get_context(request: Request, **kwargs) -> dict:
    """Build template context with common variables."""
    settings = get_settings()
    return {
        "request": request,
        "settings": settings,
        "categories": list(WatchItemCategory),
        "source_types": list(SourceType),
        "source_tiers": list(SourceTier),
        **kwargs,
    }


# =============================================================================
# Authentication
# =============================================================================

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    """Show login page."""
    session = get_current_session(request)
    if session:
        return RedirectResponse(url="/admin/watch-items", status_code=302)

    return templates.TemplateResponse(
        "login.html",
        get_context(request, error=error)
    )


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    """Process login."""
    if verify_credentials(username, password):
        response = RedirectResponse(url="/admin/watch-items", status_code=302)
        set_session_cookie(response, username)
        return response
    else:
        return RedirectResponse(
            url="/admin/login?error=Invalid+credentials",
            status_code=302
        )


@router.get("/logout")
async def logout(request: Request):
    """Log out and redirect to login."""
    response = RedirectResponse(url="/admin/login", status_code=302)
    clear_session_cookie(response)
    return response


# =============================================================================
# Watch Items
# =============================================================================

@router.get("/watch-items", response_class=HTMLResponse)
async def watch_items_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    per_page: int = 30,
):
    """List all watch items with pagination."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Ensure valid pagination
    page = max(1, page)
    per_page = min(max(10, per_page), 100)
    skip = (page - 1) * per_page

    items = storage.get_watch_items(db, skip=skip, limit=per_page)
    total_count = storage.count_watch_items(db)
    total_pages = (total_count + per_page - 1) // per_page
    stats = storage.get_stats(db)

    return templates.TemplateResponse(
        "watch_items.html",
        get_context(
            request,
            items=items,
            stats=stats,
            page=page,
            per_page=per_page,
            total_count=total_count,
            total_pages=total_pages,
        )
    )


@router.get("/watch-items/new", response_class=HTMLResponse)
async def watch_item_new(
    request: Request,
    db: Session = Depends(get_db),
):
    """Show form to create new watch item."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    sources = storage.get_sources(db, limit=500)

    return templates.TemplateResponse(
        "watch_item_edit.html",
        get_context(request, item=None, sources=sources, is_new=True)
    )


@router.get("/watch-items/{item_id}", response_class=HTMLResponse)
async def watch_item_edit(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
):
    """Show form to edit a watch item."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    item = storage.get_watch_item(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Watch item not found")

    sources = storage.get_sources(db, limit=500)
    item_source_ids = [s.id for s in item.sources]

    return templates.TemplateResponse(
        "watch_item_edit.html",
        get_context(
            request,
            item=item,
            sources=sources,
            item_source_ids=item_source_ids,
            is_new=False
        )
    )


@router.post("/watch-items/new")
async def watch_item_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    category: str = Form(...),
    description: str = Form(""),
    keywords: str = Form(""),
    entities: str = Form(""),
    severity_rules: str = Form("[]"),
    assets_affected: str = Form(""),
    cooldown_minutes: int = Form(15),
    is_active: bool = Form(True),
    source_ids: str = Form(""),
):
    """Create a new watch item."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Parse form data
    keywords_list = [k.strip() for k in keywords.split(",") if k.strip()]
    entities_list = [e.strip() for e in entities.split(",") if e.strip()]
    assets_list = [a.strip() for a in assets_affected.split(",") if a.strip()]

    try:
        severity_rules_list = json.loads(severity_rules) if severity_rules else []
    except json.JSONDecodeError:
        severity_rules_list = []

    source_ids_list = [int(s) for s in source_ids.split(",") if s.strip().isdigit()]

    data = WatchItemCreate(
        name=name,
        category=WatchItemCategory(category),
        description=description or None,
        keywords=keywords_list,
        entities=entities_list,
        severity_rules=severity_rules_list,
        assets_affected=assets_list,
        cooldown_minutes=cooldown_minutes,
        is_active=is_active,
        source_ids=source_ids_list,
    )

    storage.create_watch_item(db, data)

    return RedirectResponse(url="/admin/watch-items", status_code=302)


@router.post("/watch-items/{item_id}")
async def watch_item_update(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    category: str = Form(...),
    description: str = Form(""),
    keywords: str = Form(""),
    entities: str = Form(""),
    severity_rules: str = Form("[]"),
    assets_affected: str = Form(""),
    cooldown_minutes: int = Form(15),
    is_active: bool = Form(False),
    source_ids: str = Form(""),
):
    """Update a watch item."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Parse form data
    keywords_list = [k.strip() for k in keywords.split(",") if k.strip()]
    entities_list = [e.strip() for e in entities.split(",") if e.strip()]
    assets_list = [a.strip() for a in assets_affected.split(",") if a.strip()]

    try:
        severity_rules_list = json.loads(severity_rules) if severity_rules else []
    except json.JSONDecodeError:
        severity_rules_list = []

    source_ids_list = [int(s) for s in source_ids.split(",") if s.strip().isdigit()]

    data = WatchItemUpdate(
        name=name,
        category=WatchItemCategory(category),
        description=description or None,
        keywords=keywords_list,
        entities=entities_list,
        severity_rules=severity_rules_list,
        assets_affected=assets_list,
        cooldown_minutes=cooldown_minutes,
        is_active=is_active,
        source_ids=source_ids_list,
    )

    storage.update_watch_item(db, item_id, data)

    return RedirectResponse(url="/admin/watch-items", status_code=302)


@router.post("/watch-items/{item_id}/delete")
async def watch_item_delete(
    request: Request,
    item_id: int,
    db: Session = Depends(get_db),
):
    """Delete a watch item."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    storage.delete_watch_item(db, item_id)

    return RedirectResponse(url="/admin/watch-items", status_code=302)


# =============================================================================
# Sources
# =============================================================================

@router.get("/sources", response_class=HTMLResponse)
async def sources_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    per_page: int = 30,
):
    """List all sources with pagination."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Ensure valid pagination
    page = max(1, page)
    per_page = min(max(10, per_page), 100)
    skip = (page - 1) * per_page

    sources = storage.get_sources(db, skip=skip, limit=per_page)
    total_count = storage.count_sources(db)
    total_pages = (total_count + per_page - 1) // per_page

    return templates.TemplateResponse(
        "sources.html",
        get_context(
            request,
            sources=sources,
            page=page,
            per_page=per_page,
            total_count=total_count,
            total_pages=total_pages,
        )
    )


@router.get("/sources/new", response_class=HTMLResponse)
async def source_new(
    request: Request,
    db: Session = Depends(get_db),
):
    """Show form to create new source."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    watch_items = storage.get_watch_items(db, limit=500)

    return templates.TemplateResponse(
        "source_edit.html",
        get_context(request, source=None, watch_items=watch_items, is_new=True)
    )


@router.get("/sources/{source_id}", response_class=HTMLResponse)
async def source_edit(
    request: Request,
    source_id: int,
    db: Session = Depends(get_db),
):
    """Show form to edit a source."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    source = storage.get_source(db, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    watch_items = storage.get_watch_items(db, limit=500)
    source_watch_item_ids = [wi.id for wi in source.watch_items]

    return templates.TemplateResponse(
        "source_edit.html",
        get_context(
            request,
            source=source,
            watch_items=watch_items,
            source_watch_item_ids=source_watch_item_ids,
            is_new=False
        )
    )


@router.post("/sources/new")
async def source_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    url: str = Form(...),
    source_type: str = Form(...),
    tier: str = Form("secondary"),
    is_global: bool = Form(False),
    is_active: bool = Form(True),
    poll_interval_override: Optional[int] = Form(None),
    content_selector: str = Form(""),
    watch_item_ids: str = Form(""),
):
    """Create a new source."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    watch_item_ids_list = [int(s) for s in watch_item_ids.split(",") if s.strip().isdigit()]

    data = SourceCreate(
        name=name,
        url=url,
        source_type=SourceType(source_type),
        tier=SourceTier(tier),
        is_global=is_global,
        is_active=is_active,
        poll_interval_override=poll_interval_override,
        content_selector=content_selector or None,
        watch_item_ids=watch_item_ids_list,
    )

    storage.create_source(db, data)

    return RedirectResponse(url="/admin/sources", status_code=302)


@router.post("/sources/{source_id}")
async def source_update(
    request: Request,
    source_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    url: str = Form(...),
    source_type: str = Form(...),
    tier: str = Form("secondary"),
    is_global: bool = Form(False),
    is_active: bool = Form(False),
    poll_interval_override: Optional[int] = Form(None),
    content_selector: str = Form(""),
    watch_item_ids: str = Form(""),
):
    """Update a source."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    watch_item_ids_list = [int(s) for s in watch_item_ids.split(",") if s.strip().isdigit()]

    data = SourceUpdate(
        name=name,
        url=url,
        source_type=SourceType(source_type),
        tier=SourceTier(tier),
        is_global=is_global,
        is_active=is_active,
        poll_interval_override=poll_interval_override,
        content_selector=content_selector or None,
        watch_item_ids=watch_item_ids_list,
    )

    storage.update_source(db, source_id, data)

    return RedirectResponse(url="/admin/sources", status_code=302)


@router.post("/sources/{source_id}/delete")
async def source_delete(
    request: Request,
    source_id: int,
    db: Session = Depends(get_db),
):
    """Delete a source."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    storage.delete_source(db, source_id)

    return RedirectResponse(url="/admin/sources", status_code=302)


# =============================================================================
# Events
# =============================================================================

@router.get("/events", response_class=HTMLResponse)
async def events_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = 1,
    per_page: int = 30,
):
    """List recent events with pagination."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Ensure valid pagination
    page = max(1, page)
    per_page = min(max(10, per_page), 100)  # Between 10 and 100
    skip = (page - 1) * per_page

    events = storage.get_events(db, skip=skip, limit=per_page)
    total_count = storage.count_events(db)
    total_pages = (total_count + per_page - 1) // per_page

    return templates.TemplateResponse(
        "events.html",
        get_context(
            request,
            events=events,
            page=page,
            per_page=per_page,
            total_count=total_count,
            total_pages=total_pages,
        )
    )


@router.post("/events/{event_id}/test-send")
async def test_send_event(
    request: Request,
    event_id: int,
    db: Session = Depends(get_db),
):
    """Send a test alert for an event to Telegram."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    from radar.notify.telegram import TelegramNotifier, AlertData, AssetWithDirection

    # Get the event and its best detection
    event = storage.get_event(db, event_id)
    if not event:
        return RedirectResponse(
            url="/admin/events?message=Event+not+found",
            status_code=302
        )

    if not event.detections:
        return RedirectResponse(
            url="/admin/events?message=Event+has+no+detections",
            status_code=302
        )

    # Use the detection with highest score
    detection = max(event.detections, key=lambda d: d.severity_score)

    # Build assets with direction from detection
    assets_with_direction = None
    if detection.assets_affected:
        # Simple fallback - no direction info stored in detection
        assets_with_direction = [
            AssetWithDirection(symbol=asset, direction="neutral")
            for asset in detection.assets_affected[:6]
        ]

    # Build AlertData
    alert_data = AlertData(
        title=event.title or "Untitled",
        watch_item_name=detection.watch_item.name if detection.watch_item else "Unknown",
        watch_item_category=detection.watch_item.category.value if detection.watch_item else "unknown",
        severity_score=detection.severity_score,
        match_confidence=detection.match_confidence,
        assets_affected=detection.assets_affected or [],
        assets_with_direction=assets_with_direction,
        trigger_spans=detection.trigger_spans or [],
        source_url=event.url or "",
        published_at=event.published_at,
        market_impact=detection.llm_reasoning,  # This contains the market analysis
    )

    # Send test alert
    notifier = TelegramNotifier()
    if not notifier.is_available:
        return RedirectResponse(
            url="/admin/events?message=Telegram+not+configured",
            status_code=302
        )

    result = notifier.send_alert(alert_data)

    if result.success:
        message = f"Test+alert+sent+successfully!"
    else:
        message = f"Failed:+{result.error[:50] if result.error else 'Unknown'}"

    return RedirectResponse(
        url=f"/admin/events?message={message}",
        status_code=302
    )


# =============================================================================
# Alerts
# =============================================================================

@router.get("/alerts", response_class=HTMLResponse)
async def alerts_list(
    request: Request,
    db: Session = Depends(get_db),
    message: Optional[str] = None,
    page: int = 1,
    per_page: int = 30,
):
    """List sent alerts with pagination."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Ensure valid pagination
    page = max(1, page)
    per_page = min(max(10, per_page), 100)
    skip = (page - 1) * per_page

    alerts = storage.get_alerts(db, skip=skip, limit=per_page)
    total_count = storage.count_alerts(db)
    total_pages = (total_count + per_page - 1) // per_page
    failed_count = len([a for a in alerts if not a.is_success])

    return templates.TemplateResponse(
        "alerts.html",
        get_context(
            request,
            alerts=alerts,
            failed_count=failed_count,
            message=message,
            page=page,
            per_page=per_page,
            total_count=total_count,
            total_pages=total_pages,
        )
    )


@router.post("/alerts/retry-failed")
async def retry_failed_alerts(
    request: Request,
    db: Session = Depends(get_db),
):
    """Retry all failed alerts."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    from radar.notify.telegram import TelegramNotifier, AlertData

    notifier = TelegramNotifier()
    if not notifier.is_available:
        return RedirectResponse(
            url="/admin/alerts?message=Telegram+not+configured",
            status_code=302
        )

    failed_alerts = storage.get_failed_alerts(db)
    success_count = 0
    fail_count = 0

    for alert in failed_alerts:
        detection = alert.detection
        if not detection or not detection.event or not detection.watch_item:
            continue

        # Rebuild AlertData from detection
        alert_data = AlertData(
            title=detection.event.title or "Untitled",
            watch_item_name=detection.watch_item.name,
            watch_item_category=detection.watch_item.category.value,
            severity_score=detection.severity_score,
            match_confidence=detection.match_confidence,
            assets_affected=detection.assets_affected or [],
            trigger_spans=detection.trigger_spans or [],
            source_url=detection.event.url or "",
            published_at=detection.event.published_at,
            market_impact=detection.llm_reasoning,
        )

        result = notifier.send_alert(alert_data)

        storage.update_alert_status(
            db,
            alert.id,
            is_success=result.success,
            telegram_message_id=result.message_id,
            error_message=result.error if not result.success else None,
        )

        if result.success:
            success_count += 1
        else:
            fail_count += 1

    message = f"Retried:+{success_count}+succeeded,+{fail_count}+failed"
    return RedirectResponse(
        url=f"/admin/alerts?message={message}",
        status_code=302
    )


@router.post("/alerts/{alert_id}/retry")
async def retry_single_alert(
    request: Request,
    alert_id: int,
    db: Session = Depends(get_db),
):
    """Retry a single failed alert."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    from radar.notify.telegram import TelegramNotifier, AlertData

    notifier = TelegramNotifier()
    if not notifier.is_available:
        return RedirectResponse(
            url="/admin/alerts?message=Telegram+not+configured",
            status_code=302
        )

    # Get the specific alert
    alert = db.get(AlertSent, alert_id)
    if not alert:
        return RedirectResponse(
            url="/admin/alerts?message=Alert+not+found",
            status_code=302
        )

    detection = alert.detection
    if not detection or not detection.event or not detection.watch_item:
        return RedirectResponse(
            url="/admin/alerts?message=Alert+data+incomplete",
            status_code=302
        )

    # Rebuild AlertData from detection
    alert_data = AlertData(
        title=detection.event.title or "Untitled",
        watch_item_name=detection.watch_item.name,
        watch_item_category=detection.watch_item.category.value,
        severity_score=detection.severity_score,
        match_confidence=detection.match_confidence,
        assets_affected=detection.assets_affected or [],
        trigger_spans=detection.trigger_spans or [],
        source_url=detection.event.url or "",
        published_at=detection.event.published_at,
        market_impact=detection.llm_reasoning,
    )

    result = notifier.send_alert(alert_data)

    storage.update_alert_status(
        db,
        alert.id,
        is_success=result.success,
        telegram_message_id=result.message_id,
        error_message=result.error if not result.success else None,
    )

    if result.success:
        message = "Alert+sent+successfully"
    else:
        message = f"Failed:+{result.error[:50] if result.error else 'Unknown'}"

    return RedirectResponse(
        url=f"/admin/alerts?message={message}",
        status_code=302
    )


# =============================================================================
# Settings
# =============================================================================

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    message: Optional[str] = None,
):
    """Show settings page."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    settings = get_settings()
    stats = storage.get_stats(db)
    db_settings = storage.get_all_app_settings(db)

    return templates.TemplateResponse(
        "settings.html",
        get_context(
            request,
            current_settings=settings,
            stats=stats,
            db_settings=db_settings,
            message=message,
        )
    )


@router.post("/settings/save")
async def save_settings(
    request: Request,
    db: Session = Depends(get_db),
    alert_threshold: int = Form(...),
    digest_threshold: int = Form(...),
    poll_interval_seconds: int = Form(...),
    default_cooldown_minutes: int = Form(...),
    llm_confidence_threshold: float = Form(...),
    openrouter_model: str = Form(...),
    translation_model: str = Form(...),
    openrouter_api_key: str = Form(""),
    telegram_bot_token: str = Form(""),
    telegram_chat_id: str = Form(""),
):
    """Save settings to database."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Save detection/alert settings
    storage.set_app_setting(db, "alert_threshold", str(alert_threshold), "Severity threshold for immediate alerts")
    storage.set_app_setting(db, "digest_threshold", str(digest_threshold), "Severity threshold for digest inclusion")
    storage.set_app_setting(db, "poll_interval_seconds", str(poll_interval_seconds), "How often to poll sources")
    storage.set_app_setting(db, "default_cooldown_minutes", str(default_cooldown_minutes), "Cooldown between alerts")
    storage.set_app_setting(db, "llm_confidence_threshold", str(llm_confidence_threshold), "Min LLM confidence")

    # Save LLM settings
    storage.set_app_setting(db, "openrouter_model", openrouter_model, "OpenRouter model for analysis")
    storage.set_app_setting(db, "translation_model", translation_model, "OpenRouter model for Persian translation")
    if openrouter_api_key:  # Only update if provided
        storage.set_app_setting(db, "openrouter_api_key", openrouter_api_key, "OpenRouter API key")

    # Save Telegram settings
    if telegram_bot_token:  # Only update if provided
        storage.set_app_setting(db, "telegram_bot_token", telegram_bot_token, "Telegram bot token")
    if telegram_chat_id:
        storage.set_app_setting(db, "telegram_chat_id", telegram_chat_id, "Telegram channel/chat ID")

    return RedirectResponse(
        url="/admin/settings?message=Settings+saved+successfully!+Restart+service+to+apply.",
        status_code=302
    )


@router.post("/settings/test-telegram")
async def test_telegram(request: Request):
    """Send a test Telegram message."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    notifier = TelegramNotifier()
    result = notifier.send_test_message()

    if result.success:
        message = "Test message sent successfully!"
    else:
        message = f"Failed to send: {result.error}"

    return RedirectResponse(
        url=f"/admin/settings?message={message}",
        status_code=302
    )


@router.post("/settings/deploy")
async def deploy_updates(request: Request):
    """Pull latest code and restart service."""
    import subprocess
    import os

    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Look for deploy script in common locations
    deploy_script = None

    # Get the project root directory (parent of radar/)
    from pathlib import Path
    project_root = Path(__file__).parent.parent.parent

    possible_paths = [
        # Project directory first (most reliable)
        str(project_root / "deploy.sh"),
        # Production locations
        "/home/radarbot/high-impact-news/market-radar-bot/deploy.sh",
        "/home/radarbot/market-radar-bot/deploy.sh",
        "/home/radarbot/deploy.sh",
        # User home directory fallback
        os.path.expanduser("~/deploy.sh"),
        os.path.expanduser("~/high-impact-news/market-radar-bot/deploy.sh"),
    ]

    for path in possible_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            deploy_script = path
            break

    if not deploy_script:
        # List where we looked
        paths_checked = ", ".join([p.split("/")[-1] for p in possible_paths[:3]])
        return RedirectResponse(
            url=f"/admin/settings?message=Deploy+script+not+found.+Expected+at:{str(project_root / 'deploy.sh').replace(' ', '+')}",
            status_code=302
        )

    try:
        # Run deploy script with timeout from the script's directory
        script_dir = os.path.dirname(deploy_script)
        result = subprocess.run(
            ["/bin/bash", deploy_script],
            capture_output=True,
            text=True,
            timeout=120,  # 2 minutes for git operations
            cwd=script_dir,
            env={**os.environ, "HOME": os.path.expanduser("~")},
        )

        if result.returncode == 0:
            message = "Deploy+successful!+Service+restarting..."
        else:
            # Get meaningful error from stderr or stdout
            error_output = result.stderr.strip() or result.stdout.strip() or "Unknown error"
            # Take last line which usually has the actual error
            error_line = error_output.split("\n")[-1][:80]
            message = f"Deploy+failed:+{error_line}".replace(" ", "+").replace(":", "%3A")

    except subprocess.TimeoutExpired:
        message = "Deploy+timed+out+(60s)"
    except Exception as e:
        message = f"Deploy+error:+{str(e)[:50]}".replace(" ", "+")

    return RedirectResponse(
        url=f"/admin/settings?message={message}",
        status_code=302
    )


@router.post("/settings/send-daily-summary")
async def send_daily_summary(
    request: Request,
    db: Session = Depends(get_db),
    hours: int = Form(24),
):
    """Generate and send daily market summary to Telegram."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    from radar.notify.daily_summary import DailySummaryGenerator

    notifier = TelegramNotifier()
    if not notifier.is_available:
        return RedirectResponse(
            url="/admin/settings?message=Telegram+not+configured",
            status_code=302
        )

    # Generate summary
    generator = DailySummaryGenerator()
    summary = generator.generate_summary(db, hours=hours)

    if not summary:
        return RedirectResponse(
            url=f"/admin/settings?message=No+alerts+in+last+{hours}+hours",
            status_code=302
        )

    # Format and send
    message_text = generator.format_telegram_message(summary)
    result = notifier.send_raw_message(message_text)

    if result.success:
        message = f"Daily+summary+sent!+{summary.total_alerts}+alerts,+{len(summary.assets)}+assets"
    else:
        message = f"Failed:+{result.error[:50] if result.error else 'Unknown'}"

    return RedirectResponse(
        url=f"/admin/settings?message={message}",
        status_code=302
    )


@router.post("/settings/save-summary-schedule")
async def save_summary_schedule(
    request: Request,
    db: Session = Depends(get_db),
    daily_summary_enabled: Optional[str] = Form(None),
    daily_summary_time: str = Form("18:00"),
    daily_summary_hours: str = Form("24"),
):
    """Save daily summary schedule settings."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Save schedule settings
    enabled = "1" if daily_summary_enabled else "0"
    storage.set_app_setting(db, "daily_summary_enabled", enabled, "Enable automatic daily summary")
    storage.set_app_setting(db, "daily_summary_time", daily_summary_time, "Time to send daily summary (Tehran)")
    storage.set_app_setting(db, "daily_summary_hours", daily_summary_hours, "Hours to look back for summary")

    if enabled == "1":
        message = f"Schedule+saved!+Summary+will+be+sent+daily+at+{daily_summary_time}+Tehran"
    else:
        message = "Daily+summary+schedule+disabled"

    return RedirectResponse(
        url=f"/admin/settings?message={message}",
        status_code=302
    )


@router.post("/settings/save-sentiment-schedule")
async def save_sentiment_schedule(
    request: Request,
    db: Session = Depends(get_db),
    sentiment_1h_enabled: Optional[str] = Form(None),
    sentiment_4h_enabled: Optional[str] = Form(None),
    sentiment_daily_enabled: Optional[str] = Form(None),
    sentiment_daily_time: str = Form("08:00"),
):
    """Save sentiment report schedule settings."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Save schedule settings
    storage.set_app_setting(
        db, "sentiment_1h_enabled",
        "1" if sentiment_1h_enabled else "0",
        "Enable hourly sentiment reports"
    )
    storage.set_app_setting(
        db, "sentiment_4h_enabled",
        "1" if sentiment_4h_enabled else "0",
        "Enable 4-hour sentiment reports"
    )
    storage.set_app_setting(
        db, "sentiment_daily_enabled",
        "1" if sentiment_daily_enabled else "0",
        "Enable daily sentiment reports"
    )
    storage.set_app_setting(
        db, "sentiment_daily_time",
        sentiment_daily_time,
        "Time to send daily sentiment (Tehran)"
    )

    # Build status message
    enabled_list = []
    if sentiment_1h_enabled:
        enabled_list.append("1H")
    if sentiment_4h_enabled:
        enabled_list.append("4H")
    if sentiment_daily_enabled:
        enabled_list.append(f"Daily@{sentiment_daily_time}")

    if enabled_list:
        message = f"Sentiment+schedule+saved:+{','.join(enabled_list)}"
    else:
        message = "All+sentiment+reports+disabled"

    return RedirectResponse(
        url=f"/admin/settings?message={message}",
        status_code=302
    )


@router.post("/settings/send-sentiment")
async def send_sentiment(
    request: Request,
    db: Session = Depends(get_db),
    hours: int = Form(4),
):
    """Generate and send sentiment report to Telegram."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    from radar.notify.sentiment import SentimentAnalyzer

    notifier = TelegramNotifier()
    if not notifier.is_available:
        return RedirectResponse(
            url="/admin/settings?message=Telegram+not+configured",
            status_code=302
        )

    # Generate sentiment report
    analyzer = SentimentAnalyzer()
    report = analyzer.analyze(db, hours=hours)

    if not report:
        return RedirectResponse(
            url=f"/admin/settings?message=No+sentiment+data+in+last+{hours}+hours",
            status_code=302
        )

    if report.total_news_count < 1:
        return RedirectResponse(
            url=f"/admin/settings?message=Insufficient+data+({report.total_news_count}+news)",
            status_code=302
        )

    # Format and send
    message_text = analyzer.format_telegram_message(report)
    result = notifier.send_raw_message(message_text)

    if result.success:
        message = f"Sentiment+sent!+{report.total_news_count}+news+analyzed"
    else:
        message = f"Failed:+{result.error[:50] if result.error else 'Unknown'}"

    return RedirectResponse(
        url=f"/admin/settings?message={message}",
        status_code=302
    )


# =============================================================================
# Dashboard / Index
# =============================================================================

@router.get("/", response_class=HTMLResponse)
async def admin_index(request: Request):
    """Redirect to watch items."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    return RedirectResponse(url="/admin/watch-items", status_code=302)


# =============================================================================
# Learning System
# =============================================================================

@router.get("/learning", response_class=HTMLResponse)
async def learning_dashboard(
    request: Request,
    db: Session = Depends(get_db),
):
    """Learning system dashboard."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    reliability_scorer = ReliabilityScorer()
    history_recorder = HistoryRecorder()
    source_discovery = SourceDiscovery()

    # Get source reliability rankings
    rankings = reliability_scorer.get_all_source_rankings(db)

    # Get recent history
    recent_history = history_recorder.get_recent_history(db, limit=20)

    # Get source candidates ready for review
    candidates = source_discovery.get_candidates_for_review(db)

    return templates.TemplateResponse(
        "learning.html",
        get_context(
            request,
            rankings=rankings,
            recent_history=recent_history,
            candidates=candidates,
        )
    )


@router.get("/learning/source/{source_id}", response_class=HTMLResponse)
async def source_reliability_detail(
    request: Request,
    source_id: int,
    db: Session = Depends(get_db),
):
    """Detailed reliability stats for a source."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    reliability_scorer = ReliabilityScorer()

    stats = reliability_scorer.get_source_stats(db, source_id)
    best_hours = reliability_scorer.get_best_hours_for_source(db, source_id)
    should_demote, demote_reason = reliability_scorer.should_demote_source(db, source_id)

    source = storage.get_source(db, source_id)

    return templates.TemplateResponse(
        "source_reliability.html",
        get_context(
            request,
            source=source,
            stats=stats,
            best_hours=best_hours,
            should_demote=should_demote,
            demote_reason=demote_reason,
        )
    )


@router.get("/learning/history/{watch_item_id}", response_class=HTMLResponse)
async def watch_item_history(
    request: Request,
    watch_item_id: int,
    db: Session = Depends(get_db),
):
    """Historical analysis for a watch item."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    history_recorder = HistoryRecorder()

    stats = history_recorder.get_watch_item_stats(db, watch_item_id)
    correlation = history_recorder.get_correlation_analysis(db, watch_item_id)
    training_data = history_recorder.get_training_data(db, watch_item_id=watch_item_id)

    watch_item = storage.get_watch_item(db, watch_item_id)

    return templates.TemplateResponse(
        "watch_item_history.html",
        get_context(
            request,
            watch_item=watch_item,
            stats=stats,
            correlation=correlation,
            training_data=training_data[:50],  # Limit for display
        )
    )


@router.post("/learning/discover")
async def trigger_source_discovery(
    request: Request,
    db: Session = Depends(get_db),
):
    """Trigger automatic source discovery."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    source_discovery = SourceDiscovery()
    added = source_discovery.auto_discover(db)

    return RedirectResponse(
        url=f"/admin/learning?message=Discovered+{added}+new+candidate+sources",
        status_code=302
    )


@router.post("/learning/validate-candidates")
async def validate_all_candidates(
    request: Request,
    db: Session = Depends(get_db),
):
    """Validate all pending source candidates."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    source_discovery = SourceDiscovery()
    results = source_discovery.validate_all_candidates(db)

    message = f"Validated:{results['validated']}+Invalid:{results['invalid']}"

    return RedirectResponse(
        url=f"/admin/learning?message={message}",
        status_code=302
    )


@router.post("/learning/promote/{candidate_id}")
async def promote_candidate(
    request: Request,
    candidate_id: int,
    name: str = Form(...),
    tier: int = Form(3),
    db: Session = Depends(get_db),
):
    """Promote a validated candidate to active source."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    source_discovery = SourceDiscovery()
    source = source_discovery.promote_to_active(db, candidate_id, name, tier)

    if source:
        message = f"Promoted+to+source+{source.id}"
    else:
        message = "Failed+to+promote"

    return RedirectResponse(
        url=f"/admin/learning?message={message}",
        status_code=302
    )


@router.get("/learning/export", response_class=HTMLResponse)
async def export_training_data(
    request: Request,
    db: Session = Depends(get_db),
):
    """Export training data as JSON."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    history_recorder = HistoryRecorder()
    data = history_recorder.get_training_data(db, only_with_impact=True)

    return Response(
        content=json.dumps(data, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=training_data.json"}
    )


# =============================================================================
# API Costs
# =============================================================================

@router.get("/costs", response_class=HTMLResponse)
async def costs_page(
    request: Request,
    db: Session = Depends(get_db),
    message: Optional[str] = None,
):
    """Show API costs page with daily/weekly/monthly breakdown."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Get cost summaries
    summary = storage.get_api_costs_summary(db)
    daily_costs = storage.get_api_costs_by_day(db, days=30)
    by_model = storage.get_api_costs_by_model(db, days=30)
    by_purpose = storage.get_api_costs_by_purpose(db, days=30)
    recent_calls = storage.get_recent_api_calls(db, limit=30)

    return templates.TemplateResponse(
        "costs.html",
        get_context(
            request,
            summary=summary,
            daily_costs=daily_costs,
            by_model=by_model,
            by_purpose=by_purpose,
            recent_calls=recent_calls,
            message=message,
        )
    )


# =============================================================================
# Documentation
# =============================================================================

@router.get("/docs", response_class=HTMLResponse)
async def docs_page(
    request: Request,
    db: Session = Depends(get_db),
):
    """Show documentation page."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    # Get today's date range
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Calculate stats for docs page
    stats = {
        "watch_items": db.execute(
            select(func.count(WatchItem.id)).where(WatchItem.is_active == True)
        ).scalar() or 0,
        "sources": db.execute(
            select(func.count(Source.id)).where(Source.is_active == True)
        ).scalar() or 0,
        "events_today": db.execute(
            select(func.count(Event.id)).where(Event.published_at >= today_start)
        ).scalar() or 0,
        "alerts_today": db.execute(
            select(func.count(AlertSent.id)).where(
                AlertSent.sent_at >= today_start,
                AlertSent.is_success == True
            )
        ).scalar() or 0,
    }

    return templates.TemplateResponse(
        "docs.html",
        get_context(request, stats=stats)
    )
