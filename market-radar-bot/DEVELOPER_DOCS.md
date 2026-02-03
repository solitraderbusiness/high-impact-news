# Market Radar Bot - Developer Documentation

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Data Model](#data-model)
4. [Core Components](#core-components)
5. [Configuration](#configuration)
6. [API Reference](#api-reference)
7. [Extending the System](#extending-the-system)
8. [Testing](#testing)
9. [Deployment](#deployment)
10. [Troubleshooting](#troubleshooting)

---

## Architecture Overview

### System Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           MARKET RADAR BOT                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                   │
│  │   SOURCES    │    │   SOURCES    │    │   SOURCES    │                   │
│  │  (RSS Feeds) │    │ (Web Pages)  │    │   (Future)   │                   │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘                   │
│         │                   │                   │                            │
│         └───────────────────┴───────────────────┘                            │
│                             │                                                │
│                             ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        COLLECTORS                                     │   │
│  │  • RSS Collector (feedparser, ETag/Last-Modified)                    │   │
│  │  • Web Collector (httpx, BeautifulSoup)                              │   │
│  │  • Normalize to: url, title, raw_text, published_at, hashes          │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                             │                                                │
│                             ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      DEDUPLICATION                                    │   │
│  │  • Content hash (SHA256 of url+title+date)                           │   │
│  │  • Title hash (normalized, catches reformatted stories)              │   │
│  │  • Skip if already in database                                       │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                             │                                                │
│                             ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        DETECTION                                      │   │
│  │                                                                       │   │
│  │  Phase A: RULE-BASED MATCHING                                        │   │
│  │  ├── Match keywords (case-insensitive substring)                     │   │
│  │  ├── Match entities (word-boundary regex)                            │   │
│  │  ├── Extract trigger spans (exact quotes with context)               │   │
│  │  └── Calculate confidence (0.0-1.0)                                  │   │
│  │                                                                       │   │
│  │  Phase B: LLM FALLBACK (if confidence < threshold && API key exists) │   │
│  │  ├── Send to OpenRouter API                                          │   │
│  │  ├── Get match + citations + reasoning                               │   │
│  │  ├── VALIDATE citations are exact substrings                         │   │
│  │  └── Reject if citations can't be verified                           │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                             │                                                │
│                             ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      SEVERITY SCORING                                 │   │
│  │                                                                       │   │
│  │  Score = base_confidence + source_tier + recency + keywords + rules  │   │
│  │                                                                       │   │
│  │  Components:                                                          │   │
│  │  ├── Base confidence: 0-40 pts (from match confidence × 40)          │   │
│  │  ├── Source tier: 0-20 pts (PRIMARY=20, SECONDARY=10, SOCIAL=0)      │   │
│  │  ├── Recency: 0-15 pts (<1h=15, <6h=12, <24h=8, <7d=4)               │   │
│  │  ├── Keywords: 0-25 pts (high-impact terms like "rate hike", "war")  │   │
│  │  └── Custom rules: variable (per watch-item patterns)                │   │
│  │                                                                       │   │
│  │  Total: 0-100 (capped)                                               │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                             │                                                │
│                             ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    COOLDOWN CHECK                                     │   │
│  │  • Check last alert time for this watch item                         │   │
│  │  • If within cooldown period → suppress (unless severity >= 90)      │   │
│  │  • Store suppression reason                                          │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                             │                                                │
│                             ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      NOTIFICATION                                     │   │
│  │  • Format Telegram message with:                                     │   │
│  │    - Headline, watch item, category                                  │   │
│  │    - Severity score, confidence                                      │   │
│  │    - Assets affected                                                 │   │
│  │    - Citation quotes (EXACT from source)                             │   │
│  │    - Source URL, timestamp (UTC + Berlin)                            │   │
│  │  • Send via Telegram Bot API                                         │   │
│  │  • Retry with exponential backoff (2s, 4s, 8s)                       │   │
│  │  • Log success/failure                                               │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                             │                                                │
│                             ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        STORAGE                                        │   │
│  │  • Store event in database                                           │   │
│  │  • Store detection with all metadata                                 │   │
│  │  • Store alert record (for audit)                                    │   │
│  │  • Update source state (etag, last_fetched)                          │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Design Principles

1. **Rules-First**: The system works completely without LLM. LLM is only a fallback.
2. **No Hallucinated Citations**: Every alert must include exact quotes from source text.
3. **Fail Safely**: Collectors never crash the service; errors are logged and skipped.
4. **Low Noise**: Cooldowns, dedup, and thresholds prevent alert fatigue.
5. **Auditable**: Every detection and alert is logged with full context.

---

## Project Structure

```
market-radar-bot/
├── radar/                          # Main application package
│   ├── __init__.py                 # Package init, version
│   ├── main.py                     # FastAPI application entry point
│   ├── config.py                   # Pydantic Settings (env vars)
│   ├── db.py                       # SQLAlchemy engine, session factory
│   ├── models.py                   # ORM models (tables)
│   ├── schemas.py                  # Pydantic schemas (API validation)
│   ├── auth.py                     # Session-based admin authentication
│   ├── storage.py                  # Database CRUD helpers
│   ├── cli.py                      # Click CLI commands
│   │
│   ├── collectors/                 # Data collection modules
│   │   ├── __init__.py
│   │   ├── rss.py                  # RSS/Atom feed collector
│   │   └── web.py                  # Web page scraper
│   │
│   ├── detect/                     # Detection and scoring
│   │   ├── __init__.py
│   │   ├── rules.py                # Rule-based keyword/entity matching
│   │   ├── llm_openrouter.py       # OpenRouter LLM client
│   │   ├── scoring.py              # Severity scoring algorithm
│   │   └── dedup.py                # Deduplication and cooldown
│   │
│   ├── notify/                     # Notification channels
│   │   ├── __init__.py
│   │   └── telegram.py             # Telegram Bot API client
│   │
│   ├── services/                   # Background services
│   │   ├── __init__.py
│   │   ├── scheduler.py            # APScheduler wrapper
│   │   └── pipeline.py             # End-to-end orchestration
│   │
│   └── admin/                      # Admin panel
│       ├── __init__.py
│       ├── routes.py               # FastAPI routes for admin
│       ├── templates/              # Jinja2 HTML templates
│       │   ├── base.html           # Base layout with nav
│       │   ├── login.html          # Login page
│       │   ├── watch_items.html    # Watch items list
│       │   ├── watch_item_edit.html# Watch item form
│       │   ├── sources.html        # Sources list
│       │   ├── source_edit.html    # Source form
│       │   ├── events.html         # Events log
│       │   ├── alerts.html         # Alerts log
│       │   └── settings.html       # Settings view
│       └── static/
│           └── styles.css          # Additional CSS
│
├── tests/                          # Unit tests
│   ├── __init__.py
│   ├── test_dedup.py               # Deduplication tests
│   ├── test_rules_match.py         # Rule matching tests
│   └── test_scoring.py             # Scoring tests
│
├── .env.example                    # Environment template
├── .gitignore                      # Git ignore rules
├── pyproject.toml                  # Python project config
├── Dockerfile                      # Container build
├── docker-compose.yml              # Multi-container setup
└── README.md                       # Quick start guide
```

---

## Data Model

### Entity Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DATA MODEL                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────┐         ┌─────────────────────┐                        │
│  │   watch_items   │         │  watch_item_sources │                        │
│  ├─────────────────┤         │    (many-to-many)   │                        │
│  │ id (PK)         │◄────────┤ watch_item_id (FK)  │                        │
│  │ name            │         │ source_id (FK)      │────────┐               │
│  │ category        │         └─────────────────────┘        │               │
│  │ description     │                                        │               │
│  │ keywords[]      │                                        ▼               │
│  │ entities[]      │                               ┌─────────────────┐      │
│  │ severity_rules[]│                               │     sources     │      │
│  │ assets_affected[]                               ├─────────────────┤      │
│  │ cooldown_minutes│                               │ id (PK)         │      │
│  │ is_active       │                               │ name            │      │
│  │ created_at      │                               │ url (unique)    │      │
│  │ updated_at      │                               │ source_type     │      │
│  └────────┬────────┘                               │ tier            │      │
│           │                                        │ is_global       │      │
│           │                                        │ is_active       │      │
│           │                                        │ poll_interval   │      │
│           │                                        │ content_selector│      │
│           │                                        └────────┬────────┘      │
│           │                                                 │               │
│           │                                                 │               │
│           │                                                 ▼               │
│           │                                        ┌─────────────────┐      │
│           │                                        │  source_state   │      │
│           │                                        ├─────────────────┤      │
│           │                                        │ id (PK)         │      │
│           │                                        │ source_id (FK)  │      │
│           │                                        │ etag            │      │
│           │                                        │ last_modified   │      │
│           │                                        │ last_fetched_at │      │
│           │                                        │ last_item_pub_at│      │
│           │                                        │ last_error      │      │
│           │                                        │ consecutive_err │      │
│           │                                        └─────────────────┘      │
│           │                                                 │               │
│           │                                                 │               │
│           │        ┌─────────────────┐                     │               │
│           │        │     events      │◄────────────────────┘               │
│           │        ├─────────────────┤                                      │
│           │        │ id (PK)         │                                      │
│           │        │ source_id (FK)  │                                      │
│           │        │ url             │                                      │
│           │        │ title           │                                      │
│           │        │ excerpt         │                                      │
│           │        │ raw_text        │                                      │
│           │        │ author          │                                      │
│           │        │ language        │                                      │
│           │        │ published_at    │                                      │
│           │        │ fetched_at      │                                      │
│           │        │ content_hash    │                                      │
│           │        │ title_hash      │                                      │
│           │        │ is_processed    │                                      │
│           │        └────────┬────────┘                                      │
│           │                 │                                               │
│           │                 │                                               │
│           │                 ▼                                               │
│           │        ┌─────────────────┐                                      │
│           └───────►│   detections    │                                      │
│                    ├─────────────────┤                                      │
│                    │ id (PK)         │                                      │
│                    │ event_id (FK)   │                                      │
│                    │ watch_item_id(FK│                                      │
│                    │ match_confidence│                                      │
│                    │ match_method    │ ("rules" or "llm")                   │
│                    │ trigger_spans[] │ (exact quotes)                       │
│                    │ llm_reasoning   │                                      │
│                    │ assets_affected[]                                      │
│                    │ severity_score  │ (0-100)                              │
│                    │ severity_breakdn│                                      │
│                    │ is_alerted      │                                      │
│                    │ is_suppressed   │                                      │
│                    │ suppression_reas│                                      │
│                    │ created_at      │                                      │
│                    └────────┬────────┘                                      │
│                             │                                               │
│                             ▼                                               │
│                    ┌─────────────────┐                                      │
│                    │   alerts_sent   │                                      │
│                    ├─────────────────┤                                      │
│                    │ id (PK)         │                                      │
│                    │ detection_id(FK)│                                      │
│                    │ telegram_msg_id │                                      │
│                    │ telegram_chat_id│                                      │
│                    │ message_text    │                                      │
│                    │ is_success      │                                      │
│                    │ error_message   │                                      │
│                    │ retry_count     │                                      │
│                    │ sent_at         │                                      │
│                    └─────────────────┘                                      │
│                                                                              │
│                    ┌─────────────────┐                                      │
│                    │  app_settings   │ (key-value store for UI settings)   │
│                    ├─────────────────┤                                      │
│                    │ id (PK)         │                                      │
│                    │ key (unique)    │                                      │
│                    │ value           │                                      │
│                    │ description     │                                      │
│                    │ updated_at      │                                      │
│                    └─────────────────┘                                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Model Details

#### WatchItem Categories
```python
class WatchItemCategory(str, enum.Enum):
    PERSON = "person"           # Political leaders, CEOs
    INSTITUTION = "institution" # IMF, World Bank, WTO
    CENTRAL_BANK = "central_bank"  # Fed, ECB, BoJ
    EVENT_TYPE = "event_type"   # FOMC meetings, elections
    MACRO_RELEASE = "macro_release"  # CPI, NFP, GDP
    MEGATREND = "megatrend"     # De-dollarization, decoupling
```

#### Source Types and Tiers
```python
class SourceType(str, enum.Enum):
    RSS = "rss"   # RSS/Atom feeds
    WEB = "web"   # HTML pages

class SourceTier(str, enum.Enum):
    PRIMARY = "primary"      # +20 severity: Official sources, wire services
    SECONDARY = "secondary"  # +10 severity: Major news outlets
    SOCIAL = "social"        # +0 severity: Blogs, social media
```

---

## Core Components

### 1. Collectors (`radar/collectors/`)

#### RSSCollector (`rss.py`)
- Uses `feedparser` library
- Supports conditional GET (ETag, Last-Modified)
- Extracts: title, link, content/summary, published date, author
- Generates content hash and normalized title hash
- Handles HTML content extraction via BeautifulSoup

#### WebCollector (`web.py`)
- Uses `httpx` for HTTP requests
- Uses `BeautifulSoup` for HTML parsing
- Supports custom CSS selectors for content extraction
- Falls back to heuristic-based content extraction (article, main, etc.)
- Extracts metadata: title (og:title, h1, title tag), author, language, date

### 2. Detection (`radar/detect/`)

#### RuleMatcher (`rules.py`)
```python
# Key methods:
def match(text, title, watch_items) -> List[RuleMatch]:
    """Match text against all watch items, return sorted by confidence."""

def _extract_trigger_span(text, text_lower, term_lower) -> str:
    """Extract exact quote with ~100 chars context around matched term."""

def _calculate_confidence(matched_keywords, matched_entities, ...) -> float:
    """
    Confidence scoring:
    - Entity match: 0.4 per match, max 0.8
    - Keyword match: 0.2 per match, max 0.6
    - Minimum for any match: 0.2
    - Maximum: 1.0
    """
```

#### OpenRouterClient (`llm_openrouter.py`)
```python
# Only used when:
# 1. Rule-based confidence < LLM_CONFIDENCE_THRESHOLD (default 0.6)
# 2. OPENROUTER_API_KEY is set

# Key validation:
def _is_exact_substring(citation, text) -> bool:
    """CRITICAL: Verify citation is exact substring. Reject if not."""
```

#### SeverityScorer (`scoring.py`)
```python
# HIGH_IMPACT_KEYWORDS dict includes:
"rate hike": 15, "rate cut": 15, "sanctions": 15, "war": 18,
"invasion": 18, "tariff": 15, "default": 20, "recession": 15,
"assassination": 25, "market crash": 20, "bank failure": 20, ...
```

#### Deduplicator (`dedup.py`)
```python
# Content hash: SHA256 of (url + title + published_at)
# Title hash: SHA256 of normalized title (lowercase, no punctuation)
# Cooldown: Per watch-item, bypassed if severity >= 90
```

### 3. Notification (`radar/notify/`)

#### TelegramNotifier (`telegram.py`)
```python
# Message format:
"""
🚨 *{title}*

📊 *Watch Item:* {name}
📁 *Category:* {category}
🎯 *Severity:* {score}/100
💯 *Confidence:* {confidence}%
💰 *Assets:* {assets}

📝 *Key Quote(s):*
_{citation1}_
_{citation2}_

🔗 [Source]({url})
🕐 {timestamp UTC / Berlin}
"""

# Retry logic: 2s, 4s, 8s exponential backoff
```

### 4. Pipeline (`radar/services/pipeline.py`)

```python
class Pipeline:
    def run(self) -> PipelineRunResult:
        """
        Full pipeline:
        1. Collect from all active sources
        2. For each new event:
           a. Match against watch items (rules first, LLM if needed)
           b. Calculate severity score
           c. Check cooldown
           d. Send alert if above threshold
           e. Store detection and alert records
        """
```

### 5. Scheduler (`radar/services/scheduler.py`)

Uses APScheduler with:
- `coalesce=True`: Combine missed runs
- `max_instances=1`: Only one pipeline run at a time
- `misfire_grace_time=60`: Allow 60s late execution

---

## Configuration

### Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATABASE_URL` | str | `sqlite:///./market_radar.db` | Database connection |
| `ADMIN_USERNAME` | str | `admin` | Admin panel login |
| `ADMIN_PASSWORD` | str | `changeme` | Admin panel password |
| `SECRET_KEY` | str | (required) | Session signing key |
| `TELEGRAM_BOT_TOKEN` | str | None | Telegram bot token |
| `TELEGRAM_CHAT_ID` | str | None | Telegram chat to send alerts |
| `OPENROUTER_API_KEY` | str | None | OpenRouter API key (optional) |
| `OPENROUTER_MODEL` | str | `anthropic/claude-3-haiku` | LLM model to use |
| `OPENROUTER_BASE_URL` | str | `https://openrouter.ai/api/v1` | API endpoint |
| `ALERT_THRESHOLD` | int | `70` | Minimum severity to alert |
| `DIGEST_THRESHOLD` | int | `30` | Minimum for digest inclusion |
| `LLM_CONFIDENCE_THRESHOLD` | float | `0.6` | Below this, try LLM |
| `POLL_INTERVAL_SECONDS` | int | `60` | Source polling interval |
| `DEFAULT_COOLDOWN_MINUTES` | int | `15` | Alert cooldown per item |
| `MAX_TEXT_LENGTH` | int | `50000` | Max stored text chars |
| `LOG_LEVEL` | str | `INFO` | Logging level |
| `LOG_FORMAT` | str | `json` | `json` or `console` |
| `HOST` | str | `0.0.0.0` | Server bind host |
| `PORT` | int | `8000` | Server bind port |

### Pydantic Settings (`config.py`)
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
    )

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def has_openrouter(self) -> bool:
        return bool(self.openrouter_api_key)
```

---

## API Reference

### Admin Panel Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/login` | Login page |
| POST | `/admin/login` | Process login |
| GET | `/admin/logout` | Logout |
| GET | `/admin/watch-items` | List watch items |
| GET | `/admin/watch-items/new` | New watch item form |
| POST | `/admin/watch-items/new` | Create watch item |
| GET | `/admin/watch-items/{id}` | Edit watch item form |
| POST | `/admin/watch-items/{id}` | Update watch item |
| POST | `/admin/watch-items/{id}/delete` | Delete watch item |
| GET | `/admin/sources` | List sources |
| GET | `/admin/sources/new` | New source form |
| POST | `/admin/sources/new` | Create source |
| GET | `/admin/sources/{id}` | Edit source form |
| POST | `/admin/sources/{id}` | Update source |
| POST | `/admin/sources/{id}/delete` | Delete source |
| GET | `/admin/events` | List recent events |
| GET | `/admin/alerts` | List sent alerts |
| GET | `/admin/settings` | View settings |
| POST | `/admin/settings/test-telegram` | Send test message |

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | API info |
| GET | `/health` | Health check |
| GET | `/api/stats` | Get statistics |

### CLI Commands

```bash
radar init-db              # Initialize database schema
radar seed                 # Seed with example data
radar serve [--host] [--port]  # Start web server
radar run [--once]         # Run monitoring loop
radar collect              # Run collection only
radar send-test            # Test Telegram
```

---

## Extending the System

### Adding a New Collector

1. Create `radar/collectors/mytype.py`:
```python
from dataclasses import dataclass
from typing import List, Optional, Tuple

@dataclass
class CollectedItem:
    url: str
    title: str
    excerpt: Optional[str]
    raw_text: Optional[str]
    author: Optional[str]
    language: Optional[str]
    published_at: Optional[datetime]
    content_hash: str
    title_hash: str

class MyTypeCollector:
    def collect(self, url: str, ...) -> Tuple[List[CollectedItem], ...]:
        # Fetch data
        # Normalize to CollectedItem
        # Return items and any state (etag, etc.)
        pass
```

2. Register in `radar/collectors/__init__.py`
3. Add `source_type` enum value in `radar/models.py`
4. Handle in `pipeline.py`'s `_collect_source()`

### Adding a New Notification Channel

1. Create `radar/notify/slack.py` (or similar):
```python
class SlackNotifier:
    def send_alert(self, data: AlertData) -> SendResult:
        # Format and send
        pass
```

2. Add config vars in `config.py`
3. Instantiate in `Pipeline.__init__()`
4. Call in `Pipeline._send_alert()`

### Adding Custom Severity Rules

Per watch item, add JSON to `severity_rules`:
```json
[
    {"pattern": "emergency meeting", "score_bump": 20},
    {"pattern": "unanimous", "score_bump": 5},
    {"pattern": "dissent", "score_bump": 10}
]
```

Patterns can be:
- Simple strings (case-insensitive substring match)
- Regex patterns (e.g., `"raised?\\s+rates?"`)

---

## Testing

### Running Tests
```bash
# All tests
pytest

# With coverage
pytest --cov=radar --cov-report=html

# Specific file
pytest tests/test_scoring.py -v

# Specific test
pytest tests/test_dedup.py::TestDeduplicator::test_jaccard_similarity -v
```

### Test Structure
- `test_dedup.py`: Hash generation, Jaccard similarity, near-duplicate detection
- `test_rules_match.py`: Keyword/entity matching, confidence calculation, trigger spans
- `test_scoring.py`: Component scoring, caps, edge cases

### Writing New Tests
```python
# Example: Testing a new collector
def test_my_collector_parses_items():
    collector = MyTypeCollector()
    items, _, _ = collector.collect("https://example.com/feed")

    assert len(items) > 0
    assert all(item.content_hash for item in items)
    assert all(item.title for item in items)
```

---

## Deployment

### Docker (Recommended)

```bash
# Build and start
docker compose up -d

# View logs
docker compose logs -f radar

# Seed data
docker compose run --rm setup

# Update
docker compose pull
docker compose up -d
```

### Manual (systemd)

See `DEPLOYMENT.md` for systemd unit files.

### Upgrading Database

For SQLite, the schema is auto-created. For PostgreSQL migrations:
```bash
# If using Alembic (not included in MVP)
alembic upgrade head
```

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| "Telegram not configured" | Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` |
| No events collected | Check source URLs, view `/admin/sources` for errors |
| LLM not working | System works without it; set `OPENROUTER_API_KEY` if wanted |
| Too many/few alerts | Adjust `ALERT_THRESHOLD`, per-item `cooldown_minutes` |
| 403 from sources | Some sites block automated access; try different feeds |

### Debugging

```bash
# Increase log verbosity
LOG_LEVEL=DEBUG radar run --once

# Check database
sqlite3 market_radar.db ".tables"
sqlite3 market_radar.db "SELECT * FROM events ORDER BY fetched_at DESC LIMIT 5"

# Test single source
python -c "
from radar.collectors import RSSCollector
c = RSSCollector()
items, _, _ = c.collect('https://www.federalreserve.gov/feeds/press_all.xml')
print(f'Got {len(items)} items')
"
```

### Performance

- SQLite is fine for MVP (<10k events/day)
- For higher volume, migrate to PostgreSQL
- Consider adding indexes on `events.fetched_at`, `detections.created_at`

---

## Future Enhancements

1. **Digest Mode**: Daily/weekly summary emails
2. **API Authentication**: JWT for programmatic access
3. **Webhook Notifications**: POST to custom endpoints
4. **Twitter/X Collector**: Monitor specific accounts
5. **Sentiment Analysis**: Positive/negative classification
6. **Dashboard Metrics**: Grafana integration
7. **Alert Escalation**: SMS for severity >= 95
8. **Multi-tenant**: Support multiple users/organizations
