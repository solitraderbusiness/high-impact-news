# CLAUDE.md - Market Radar Bot

> This file is read by Claude at the start of every conversation. It contains everything needed to work on this project without prior context.

## Project Overview

**Market Radar Bot** is a production-grade system for monitoring market-moving news and sending timely alerts via Telegram. It watches influential people, institutions, central banks, and economic events, detects relevant news using rules + optional LLM, scores severity (0-100), and sends formatted alerts.

**Key Value Proposition:** Get market-moving news alerts before they're priced in.

**Location:** `/home/user/high-impact-news/market-radar-bot`

---

## Tech Stack

| Component | Technology | Version |
|-----------|------------|---------|
| Language | Python | 3.11+ |
| Web Framework | FastAPI | 0.109+ |
| ORM | SQLAlchemy | 2.0+ |
| Database | SQLite (dev) / PostgreSQL (prod) | - |
| Scheduler | APScheduler | 3.10+ |
| HTTP Client | httpx | 0.26+ |
| RSS Parsing | feedparser | 6.0+ |
| HTML Parsing | BeautifulSoup4 | 4.12+ |
| Validation | Pydantic | 2.5+ |
| Templates | Jinja2 | (via FastAPI) |
| CLI | Click | 8.1+ |
| Logging | structlog | 24.1+ |
| Auth | itsdangerous + passlib | - |

**Optional:**
- OpenRouter API (LLM analysis via Claude/GPT)
- Telegram Bot API (alerts)

---

## Project Structure

```
market-radar-bot/
├── radar/                          # Main application package
│   ├── __init__.py
│   ├── main.py                    # FastAPI app, lifespan, endpoints
│   ├── cli.py                     # Click CLI commands
│   ├── config.py                  # Pydantic Settings (env vars)
│   ├── db.py                      # SQLAlchemy engine, sessions
│   ├── models.py                  # 13 ORM models
│   ├── schemas.py                 # Pydantic request/response schemas
│   ├── storage.py                 # CRUD operations
│   ├── auth.py                    # Session-based authentication
│   │
│   ├── admin/                     # Web admin panel
│   │   ├── __init__.py
│   │   ├── routes.py              # All admin routes (1000+ lines)
│   │   ├── templates/             # Jinja2 HTML templates
│   │   │   ├── base.html          # Base template with nav
│   │   │   ├── login.html
│   │   │   ├── watch_items.html
│   │   │   ├── watch_item_edit.html
│   │   │   ├── sources.html
│   │   │   ├── source_edit.html
│   │   │   ├── events.html
│   │   │   ├── alerts.html
│   │   │   ├── settings.html
│   │   │   ├── learning.html
│   │   │   └── ...
│   │   └── static/
│   │       └── styles.css
│   │
│   ├── collectors/                # Data collection
│   │   ├── __init__.py
│   │   ├── rss.py                 # RSSCollector class
│   │   ├── web.py                 # WebCollector class
│   │   └── telegram.py            # TelegramChannelCollector
│   │
│   ├── detect/                    # Detection & matching
│   │   ├── __init__.py
│   │   ├── rules.py               # RuleMatcher - keyword/entity matching
│   │   ├── scoring.py             # SeverityScorer - 0-100 scoring
│   │   ├── dedup.py               # Deduplicator - hash + cooldown
│   │   └── llm_openrouter.py      # OpenRouterClient - LLM fallback
│   │
│   ├── notify/                    # Notifications
│   │   ├── __init__.py
│   │   ├── telegram.py            # TelegramNotifier - send alerts
│   │   ├── daily_summary.py       # DailySummaryGenerator - LLM summaries
│   │   └── translator.py          # PersianTranslator
│   │
│   ├── services/                  # Background services
│   │   ├── __init__.py
│   │   ├── pipeline.py            # Pipeline - main orchestration
│   │   └── scheduler.py           # Scheduler - APScheduler wrapper
│   │
│   └── learning/                  # ML/Learning system
│       ├── __init__.py
│       ├── impact_tracker.py      # ImpactTracker - price movement tracking
│       ├── reliability_scorer.py  # ReliabilityScorer - source quality
│       ├── price_fetcher.py       # PriceFetcher - get prices
│       ├── history_recorder.py    # HistoryRecorder - ML training data
│       └── source_discovery.py    # SourceDiscovery - auto-find sources
│
├── tests/
│   ├── test_scoring.py
│   ├── test_rules_match.py
│   └── test_dedup.py
│
├── pyproject.toml                 # Dependencies & project config
├── .env                           # Environment variables (not in git)
├── .env.example                   # Env template
├── market_radar.db                # SQLite database (not in git)
└── CLAUDE.md                      # This file
```

---

## Database Models (13 Tables)

### Core Models (`radar/models.py`)

```python
# Main entities
WatchItem          # What to monitor (people, institutions, events)
Source             # RSS feeds, web pages, telegram channels
Event              # Collected news articles
Detection          # Match results linking events to watch items
AlertSent          # Log of sent Telegram alerts
AppSettings        # Configuration stored in database
SourceState        # Fetch state (etags, timestamps)

# Learning system
MarketImpact       # Price movements after alerts
SourceReliability  # Source quality metrics
WatchItemHistory   # Historical record for ML
SourcePool         # Candidate sources for validation
PriceData          # Historical prices
LearningConfig     # Learning system settings

# Many-to-many
watch_item_sources # Links watch items to sources
```

### Key Enums

```python
WatchItemCategory = person | institution | central_bank | event_type | macro_release | megatrend
SourceType = rss | web | telegram
SourceTier = primary | secondary | social
```

### Important Fields

**WatchItem:**
- `name`: Display name (e.g., "ECB Interest Rate Decision")
- `category`: WatchItemCategory enum
- `keywords`: List[str] - triggers (case-insensitive)
- `entities`: List[str] - exact matches (case-sensitive)
- `severity_rules`: List[dict] - custom scoring rules
- `assets_affected`: List[str] - e.g., ["EUR", "EURUSD", "German Bunds"]
- `cooldown_minutes`: Alert cooldown (default 15)

**Detection:**
- `match_confidence`: 0.0-1.0 from rules/LLM
- `severity_score`: 0-100 final score
- `trigger_spans`: List[str] - exact quotes that triggered match
- `llm_reasoning`: str - market impact analysis from LLM
- `is_alerted`: bool - whether alert was sent

---

## API Routes

### Public Endpoints (`radar/main.py`)

```
GET  /              → {"message": "Market Radar Bot API"}
GET  /health        → {"status": "healthy"}
GET  /api/stats     → Statistics (counts, config)
```

### Admin Panel (`radar/admin/routes.py`)

**Authentication:**
```
GET  /admin/login              → Login page
POST /admin/login              → Process login (username, password)
GET  /admin/logout             → Clear session
```

**Watch Items:**
```
GET  /admin/watch-items                    → List (paginated)
GET  /admin/watch-items/new                → Create form
GET  /admin/watch-items/{id}               → Edit form
POST /admin/watch-items/new                → Create
POST /admin/watch-items/{id}               → Update
POST /admin/watch-items/{id}/delete        → Delete
```

**Sources:**
```
GET  /admin/sources                        → List (paginated)
GET  /admin/sources/new                    → Create form
GET  /admin/sources/{id}                   → Edit form
POST /admin/sources/new                    → Create
POST /admin/sources/{id}                   → Update
POST /admin/sources/{id}/delete            → Delete
```

**Events & Alerts:**
```
GET  /admin/events                         → List collected events
POST /admin/events/{id}/test-send          → Test send alert for event
GET  /admin/alerts                         → Alert history
POST /admin/alerts/retry-failed            → Retry failed alerts
POST /admin/alerts/{id}/retry              → Retry single alert
```

**Settings:**
```
GET  /admin/settings                       → Settings page
POST /admin/settings/save                  → Save all settings
POST /admin/settings/test-telegram         → Send test message
POST /admin/settings/send-daily-summary    → Generate & send summary
POST /admin/settings/save-summary-schedule → Save auto-schedule
```

**Learning:**
```
GET  /admin/learning                       → Learning dashboard
GET  /admin/learning/source/{id}           → Source reliability
GET  /admin/learning/history/{id}          → Watch item history
POST /admin/learning/discover              → Trigger source discovery
POST /admin/learning/validate-candidates   → Validate candidates
POST /admin/learning/promote/{id}          → Promote to active
GET  /admin/learning/export                → Export training data
```

---

## Environment Variables

Create `.env` from `.env.example`:

```bash
# Database
DATABASE_URL=sqlite:///./market_radar.db

# Admin Auth
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_secure_password
SECRET_KEY=random_32_char_secret_key

# Telegram (optional but needed for alerts)
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=-100123456789

# OpenRouter LLM (optional - rules work without it)
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=anthropic/claude-3-haiku
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Thresholds
ALERT_THRESHOLD=70           # Severity to send alert
DIGEST_THRESHOLD=30          # Severity for digest
LLM_CONFIDENCE_THRESHOLD=0.6 # Min LLM confidence

# Polling
POLL_INTERVAL_SECONDS=60     # How often to check sources
DEFAULT_COOLDOWN_MINUTES=15  # Between alerts per watch item

# Server
HOST=0.0.0.0
PORT=8000
TIMEZONE=Asia/Tehran
LOG_LEVEL=INFO
LOG_FORMAT=json
```

---

## Build & Run Instructions

### Installation

```bash
cd /home/user/high-impact-news/market-radar-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e .

# Copy and edit environment
cp .env.example .env
nano .env
```

### CLI Commands

```bash
# Initialize database
radar init-db

# Seed with sample data (20+ watch items, sources)
radar seed

# Run continuous monitoring
radar run

# Run once (for testing)
radar run --once

# Collection only (no detection/alerts)
radar collect

# Test Telegram
radar send-test

# Start web server (admin panel)
radar serve
```

### Running as Service

```bash
# Create systemd service
sudo nano /etc/systemd/system/radarbot.service
```

```ini
[Unit]
Description=Market Radar Bot
After=network.target

[Service]
Type=simple
User=radarbot
WorkingDirectory=/home/user/high-impact-news/market-radar-bot
Environment=PATH=/home/user/high-impact-news/market-radar-bot/venv/bin
ExecStart=/home/user/high-impact-news/market-radar-bot/venv/bin/radar run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable radarbot
sudo systemctl start radarbot
sudo systemctl status radarbot
```

### Testing

```bash
pytest tests/ -v
pytest tests/test_scoring.py -v
```

---

## Key Architectural Decisions

### 1. Rules-First Detection

**Why:** LLM calls are slow (1-5s) and cost money. Rules are instant and free.

**How:**
- `RuleMatcher` checks keywords/entities first
- Only falls back to LLM if rules match with low confidence OR for market impact analysis
- LLM is optional - system works without it

### 2. Three-Layer Deduplication

**Layers:**
1. Content hash (SHA256 of URL + title + published_at)
2. Title hash (normalized title)
3. Cooldown per watch item (default 15 min)

**Why:** Same news appears on multiple sources within minutes.

### 3. Severity Scoring (0-100)

**Breakdown:**
- Base confidence: 0-40 (from match confidence)
- Source tier: 0-20 (PRIMARY=20, SECONDARY=10, SOCIAL=0)
- Recency: 0-15 (newer = higher)
- High-impact keywords: 0-25 ("rate hike", "sanctions", "war", etc.)
- Custom rules: ±points per watch item

**Thresholds:**
- 70+: Send immediate alert
- 90+: Bypass cooldown
- 30-69: Include in digest

### 4. Settings in Database

**Why:** Change thresholds without restarting service.

**How:** `AppSettings` table stores key-value pairs. `get_settings_with_db_overrides()` merges .env with DB.

### 5. Learning System

**Purpose:** Improve over time by tracking:
- Actual price movements after alerts
- Source reliability (accuracy rate)
- False positives

**Usage:** Adjusts severity scores based on source track record.

---

## Key Files to Know

### Most Important Files

| File | What It Does | When to Edit |
|------|--------------|--------------|
| `radar/services/pipeline.py` | Main orchestration | Adding new pipeline steps |
| `radar/detect/rules.py` | Keyword/entity matching | Changing match logic |
| `radar/detect/scoring.py` | Severity calculation | Adjusting scoring |
| `radar/notify/telegram.py` | Alert formatting | Changing alert format |
| `radar/admin/routes.py` | All admin endpoints | Adding admin features |
| `radar/models.py` | Database schema | Adding new tables/fields |
| `radar/config.py` | Environment config | Adding new settings |

### Template Files

| Template | Purpose |
|----------|---------|
| `templates/base.html` | Navigation, CSS includes |
| `templates/settings.html` | Config UI, daily summary |
| `templates/events.html` | Event list with test-send |
| `templates/watch_item_edit.html` | Complex form with JSON |

---

## Conventions

### Code Style

- **Imports:** stdlib → third-party → local (separated by blank lines)
- **Classes:** PascalCase
- **Functions:** snake_case
- **Constants:** UPPER_SNAKE_CASE
- **Type hints:** Required for function signatures
- **Docstrings:** Required for classes and public methods

### Database

- **IDs:** Integer auto-increment
- **Timestamps:** `created_at`, `updated_at` (auto-managed)
- **Soft deletes:** Not used (hard delete)
- **JSON fields:** Store as JSON string, parse on read

### API Responses

- **Success:** Redirect with `?message=Success+message`
- **Error:** Redirect with `?message=Error+description`
- **JSON:** Only for `/api/*` endpoints

### Jinja2 Templates

- **Timezone filter:** `{{ dt|to_tehran }}` for Tehran time
- **Forms:** POST with redirect pattern
- **Pagination:** `?page=N&per_page=M`

---

## Things to Be Careful About

### 1. Database Settings Override

Settings from database override `.env`. If something isn't working:
```python
# Check what's actually being used
from radar.db import get_db_context
from radar import storage

with get_db_context() as db:
    print(storage.get_all_app_settings(db))
```

### 2. Telegram Configuration

Both `telegram_bot_token` AND `telegram_chat_id` must be set (in .env OR database).
- Bot token: From @BotFather
- Chat ID: Use `-100` prefix for channels, `@username` also works

### 3. LLM Reasoning Field

`Detection.llm_reasoning` can be `None` if:
- Rules matched with high confidence (LLM not called)
- LLM is not configured
- LLM call failed

Don't assume it's always populated.

### 4. Cooldown Bypass

Severity >= 90 bypasses cooldown. This is intentional for breaking news.

### 5. Combined Asset Symbols

In daily summary, "USD (DXY)" is a combined entry. Code must handle both separate (USD, DXY) and combined forms to avoid duplicates.

### 6. Service Restart

After changing settings in admin panel, restart the service:
```bash
sudo systemctl restart radarbot
```

Settings are read at startup and cached.

### 7. JSON in Forms

`severity_rules` field is JSON. Template uses hidden input. Must be valid JSON array.

---

## Current State

### What's Built (Fully Working)

- [x] RSS collection with conditional GET
- [x] Web scraping with CSS selectors
- [x] Rule-based detection (keywords + entities)
- [x] LLM fallback via OpenRouter
- [x] Severity scoring with 5 factors
- [x] Three-layer deduplication
- [x] Telegram alerts with formatting
- [x] Persian translation
- [x] Admin panel with auth
- [x] CRUD for watch items, sources
- [x] Events list with test-send button
- [x] Editable settings in admin
- [x] Mobile-responsive admin
- [x] Daily summary with LLM analysis
- [x] Scheduled daily summary
- [x] Learning system (impact tracking)

### Recent Changes (This Session)

1. **Daily Summary Feature** - LLM-powered end-of-day market wrap
2. **Scheduled Summary** - Auto-send at configured time (Tehran)
3. **Professional Formatting** - Analyst-style with confidence indicators
4. **Logical Consistency** - USD bearish → EURUSD bullish
5. **Duplicate Prevention** - USD/DXY combined entries

### Known Issues

1. **LLM Reasoning Missing** - Many detections have `llm_reasoning=None` because LLM is only called as fallback
2. **Yahoo Finance** - Price fetching can fail silently for some symbols
3. **Telegram Rate Limits** - No explicit handling (retry helps)

### Database Settings Keys

```
alert_threshold           # int (0-100)
digest_threshold          # int (0-100)
poll_interval_seconds     # int
default_cooldown_minutes  # int
llm_confidence_threshold  # float (0-1)
openrouter_model          # string
openrouter_api_key        # string (sensitive)
telegram_bot_token        # string (sensitive)
telegram_chat_id          # string
daily_summary_enabled     # "1" or "0"
daily_summary_time        # "HH:MM" format
daily_summary_hours       # "12", "24", or "48"
daily_summary_last_sent   # "YYYY-MM-DD" (auto-set)
```

---

## Quick Reference

### Adding a New Watch Item

```python
from radar.schemas import WatchItemCreate
from radar import storage
from radar.db import get_db_context

with get_db_context() as db:
    item = WatchItemCreate(
        name="Fed Interest Rate Decision",
        category="central_bank",
        keywords=["fed", "fomc", "federal reserve", "powell"],
        entities=["Jerome Powell", "Federal Reserve"],
        assets_affected=["USD", "DXY", "S&P 500", "US Treasuries"],
        cooldown_minutes=15,
        is_active=True,
    )
    storage.create_watch_item(db, item)
```

### Sending a Test Alert

```python
from radar.notify.telegram import TelegramNotifier, AlertData

notifier = TelegramNotifier()
result = notifier.send_test_message()
print(result.success, result.error)
```

### Checking Pipeline Results

```python
from radar.services.pipeline import Pipeline

pipeline = Pipeline()
result = pipeline.run()
print(f"Collected: {sum(r.items_collected for r in result.collections)}")
print(f"Detections: {len(result.detections)}")
print(f"Alerts: {result.alerts_sent}")
```

### Getting Settings

```python
from radar.config import get_settings
from radar.db import get_db_context
from radar import storage

# From .env only
settings = get_settings()

# With DB overrides
with get_db_context() as db:
    db_settings = storage.get_all_app_settings(db)
    # Merge manually or use get_settings_with_db_overrides()
```

---

## Useful Commands

```bash
# View logs
sudo journalctl -u radarbot -f

# Check database
sqlite3 market_radar.db ".tables"
sqlite3 market_radar.db "SELECT * FROM app_settings"

# Quick test
radar run --once

# Python shell with context
cd /home/user/high-impact-news/market-radar-bot
source venv/bin/activate
python
>>> from radar.db import get_db_context
>>> from radar import storage
>>> with get_db_context() as db:
...     print(storage.get_stats(db))
```

---

## Contact & Resources

- **Admin Panel:** http://localhost:8000/admin/
- **Health Check:** http://localhost:8000/health
- **Logs:** `journalctl -u radarbot -f`
- **Database:** `market_radar.db` (SQLite)
