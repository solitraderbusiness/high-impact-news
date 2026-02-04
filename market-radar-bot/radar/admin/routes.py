"""
Admin panel routes with Jinja2 templates.
"""

import json
from typing import Optional
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from radar.db import get_db
from radar import storage
from radar.auth import (
    verify_credentials, set_session_cookie, clear_session_cookie,
    get_current_session, require_auth_redirect
)
from radar.config import get_settings
from radar.models import WatchItemCategory, SourceType, SourceTier
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
):
    """List all watch items."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    items = storage.get_watch_items(db, limit=500)
    stats = storage.get_stats(db)

    return templates.TemplateResponse(
        "watch_items.html",
        get_context(request, items=items, stats=stats)
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
):
    """List all sources."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    sources = storage.get_sources(db, limit=500)

    return templates.TemplateResponse(
        "sources.html",
        get_context(request, sources=sources)
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
):
    """List recent events."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    events = storage.get_events(db, limit=100)

    return templates.TemplateResponse(
        "events.html",
        get_context(request, events=events)
    )


# =============================================================================
# Alerts
# =============================================================================

@router.get("/alerts", response_class=HTMLResponse)
async def alerts_list(
    request: Request,
    db: Session = Depends(get_db),
    message: Optional[str] = None,
):
    """List sent alerts."""
    session = require_auth_redirect(request)
    if not session:
        return RedirectResponse(url="/admin/login", status_code=302)

    alerts = storage.get_alerts(db, limit=100)
    failed_count = len([a for a in alerts if not a.is_success])

    return templates.TemplateResponse(
        "alerts.html",
        get_context(request, alerts=alerts, failed_count=failed_count, message=message)
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

    return templates.TemplateResponse(
        "settings.html",
        get_context(
            request,
            current_settings=settings,
            stats=stats,
            message=message,
        )
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
