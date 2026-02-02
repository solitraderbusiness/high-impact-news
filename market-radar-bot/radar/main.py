"""
FastAPI application for Market Radar Bot.
Serves the admin panel and REST API.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from radar.config import get_settings
from radar.db import init_db
from radar.admin import admin_router
from radar.services.scheduler import Scheduler


# Configure structured logging
def configure_logging():
    settings = get_settings()

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer() if settings.log_format == "json"
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Set log level
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
    )


# Background scheduler instance
scheduler: Scheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    global scheduler

    logger = structlog.get_logger()
    settings = get_settings()

    # Initialize database
    logger.info("initializing_database")
    init_db()

    # Start background scheduler if configured
    # Note: In production, you might want to run the scheduler separately
    # For MVP, we can optionally start it with the web server
    # scheduler = Scheduler()
    # scheduler.start(blocking=False)

    logger.info("application_started", host=settings.host, port=settings.port)

    yield

    # Cleanup
    if scheduler:
        logger.info("stopping_scheduler")
        scheduler.stop()

    logger.info("application_stopped")


# Create FastAPI app
configure_logging()
app = FastAPI(
    title="Market Radar Bot",
    description="Proactive Market Impact Radar - Admin API",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files for admin panel
static_dir = Path(__file__).parent / "admin" / "static"
if static_dir.exists():
    app.mount("/admin/static", StaticFiles(directory=str(static_dir)), name="admin_static")

# Include admin routes
app.include_router(admin_router)


# API endpoints
@app.get("/")
async def root():
    """Root endpoint - redirect to admin."""
    return {"message": "Market Radar Bot API", "admin": "/admin/"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/api/stats")
async def api_stats():
    """Get current statistics."""
    from radar.db import get_db_context
    from radar import storage

    with get_db_context() as db:
        stats = storage.get_stats(db)

    settings = get_settings()
    return {
        "stats": stats,
        "config": {
            "alert_threshold": settings.alert_threshold,
            "poll_interval_seconds": settings.poll_interval_seconds,
            "has_telegram": settings.has_telegram,
            "has_openrouter": settings.has_openrouter,
        }
    }


# Error handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger = structlog.get_logger()
    logger.error("unhandled_exception", path=request.url.path, error=str(exc))

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


def run_server():
    """Run the FastAPI server."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "radar.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run_server()
