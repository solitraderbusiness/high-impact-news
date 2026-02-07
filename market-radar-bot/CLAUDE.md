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
│   ├── models.py                  # 14 ORM models
│   ├── schemas.py                 # Pydantic request/response schemas
│   ├── storage.py                 # CRUD operations
│   ├── auth.py                    # Session-based authentication
│   │
│   ├── admin/                     # Web admin panel (Modern Dark Theme)
│   │   ├── __init__.py
│   │   ├── routes.py              # All admin routes (1200+ lines)
│   │   ├── templates/             # Jinja2 HTML templates
│   │   │   ├── base.html          # Dark theme base with sidebar nav
│   │   │   ├── login.html         # Glassmorphism login page
│   │   │   ├── watch_items.html
│   │   │   ├── watch_item_edit.html
│   │   │   ├── sources.html
│   │   │   ├── source_edit.html
│   │   │   ├── events.html
│   │   │   ├── alerts.html
│   │   │   ├── settings.html      # Includes sentiment schedule
│   │   │   ├── costs.html         # API cost tracking
│   │   │   └── learning.html
│   │   └── static/
│   │       └── styles.css         # (CSS now embedded in base.html)
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
│   │   ├── llm_openrouter.py      # OpenRouterClient - LLM analysis + market relevance
│   │   └── market_relevance.py    # (Legacy - now integrated into llm_openrouter.py)
│   │
│   ├── notify/                    # Notifications
│   │   ├── __init__.py
│   │   ├── telegram.py            # TelegramNotifier - send alerts
│   │   ├── daily_summary.py       # DailySummaryGenerator - LLM summaries
│   │   ├── sentiment_analyzer.py  # MarketSentimentAnalyzer - periodic sentiment
│   │   └── translator.py          # PersianTranslator
│   │
│   ├── services/                  # Background services
│   │   ├── __init__.py
│   │   ├── pipeline.py            # Pipeline - main orchestration + Twitter/Nitter
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
├── scripts/                        # Utility scripts
│   └── add_twitter_sources.py     # Pre-configured X/Twitter accounts
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
SourceType = rss | web | telegram | twitter  # Twitter uses Nitter RSS
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
POST /admin/settings/save-sentiment-schedule → Save sentiment schedule (1H/4H/Daily)
POST /admin/settings/send-sentiment        → Send sentiment report now
```

**API Costs:**
```
GET  /admin/costs                          → API cost tracking page
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

**Production Server User:** `radarbot`

The production server runs as the `radarbot` user. All file operations and service management should be done as this user.

```bash
# Switch to radarbot user
sudo su - radarbot

# Or run commands as radarbot
sudo -u radarbot <command>
```

**Systemd Service Configuration:**

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
Group=radarbot
WorkingDirectory=/home/radarbot/high-impact-news/market-radar-bot
Environment=PATH=/home/radarbot/high-impact-news/market-radar-bot/venv/bin
ExecStart=/home/radarbot/high-impact-news/market-radar-bot/venv/bin/radar run
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

**Web Admin Service (optional - if running separately):**

```ini
[Unit]
Description=Market Radar Admin Panel
After=network.target

[Service]
Type=simple
User=radarbot
Group=radarbot
WorkingDirectory=/home/radarbot/high-impact-news/market-radar-bot
Environment=PATH=/home/radarbot/high-impact-news/market-radar-bot/venv/bin
ExecStart=/home/radarbot/high-impact-news/market-radar-bot/venv/bin/radar serve
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
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

### 4. Market Relevance Filter (Integrated into LLM Analysis)

**Problem:** Entity matching (e.g., "Donald Trump") flagged all related news as market-relevant, even political gossip and social issues that don't affect prices.

**Solution:** Market relevance check is now **combined into the single LLM analysis call** to save API costs (~30-40% reduction). The LLM evaluates both relevance AND entity matching in one call.

**Categories (High Relevance - 80-100):**
- `central_bank`: Fed/ECB/BOJ decisions and statements
- `monetary_policy`: Rate decisions, QE/QT, tapering
- `economic_data`: GDP, CPI, NFP, PMI releases
- `trade_policy`: Tariffs, sanctions, trade deals
- `market_event`: Crashes, circuit breakers, bank failures

**Categories (Low Relevance - 0-30):**
- `political_noise`: Scandals, investigations, gossip
- `social_issues`: HR disputes, discrimination cases
- `entertainment`: Celebrity news, sports
- `crime`: Non-financial crimes

**How It Works:**
1. News with relevance score < 30 → Skipped entirely
2. News with relevance score 30-69 → Score multiplied by relevance %
3. News with relevance score 70+ → Normal processing

**Example:**
- "ECB raises rates by 25bp" → central_bank (95%) → Full score
- "Nike investigated for workplace discrimination" → social_issues (10%) → Skipped
- "Trump announces new tariffs on China" → trade_policy (90%) → Full score
- "Epstein investigation update" → political_noise (15%) → Skipped

**Code:** Market relevance is now in `radar/detect/llm_openrouter.py` (combined prompt)

### 5. Trading-Focused Alert Format

**Old Format (verbose, academic):**
```
🟠 IMPORTANT
█████████████░░░░ 77%
[Persian title]
[English title duplicate]
📊 Entity • category
💯 Match: 100%
💹 Affected Assets: [list]
💡 Analysis: [Long paragraph in Persian]
[Long paragraph in English]
```

**New Format (compact, actionable):**
```
🟠 IMPORTANT | 77%
📉 BEARISH ITB

[Persian title only]

📈 XLE
📉 ITB, XHB, LEN, DHI

📊 Entity | category

⚡ SETUP:
[What happened and why it matters - 1 sentence]

📍 LEVELS: Support $X, Resistance $Y
⏱ SWING

👁 WATCH: [Catalyst for confirmation]
⚠️ RISK: [What invalidates the trade]

🔗 Bloomberg
🕐 21:13 Tehran
```

**LLM Prompt Changes:** The LLM now generates:
- `trade_bias`: BULLISH, BEARISH, or NEUTRAL
- `primary_asset`: Main asset to trade
- `setup`: 1-sentence summary
- `key_levels`: Price levels to watch
- `timeframe`: INTRADAY, SWING, POSITION
- `catalyst`: What to watch for confirmation
- `risk`: What could make this trade wrong

**Files Changed:**
- `radar/detect/llm_openrouter.py` - New prompt structure
- `radar/notify/telegram.py` - New format function
- `radar/services/pipeline.py` - Pass trading fields through

### 6. Settings in Database

**Why:** Change thresholds without restarting service.

**How:** `AppSettings` table stores key-value pairs. `get_settings_with_db_overrides()` merges .env with DB.

### 6. Learning System

**Purpose:** Improve over time by tracking:
- Actual price movements after alerts
- Source reliability (accuracy rate)
- False positives

**Usage:** Adjusts severity scores based on source track record.

### 7. X/Twitter Source Integration via Nitter

**Problem:** X/Twitter has valuable real-time financial news but requires paid API.

**Solution:** Use Nitter RSS feeds - free, no API key required.

**How It Works:**
1. Add source with `SourceType.TWITTER` and URL as `@username`
2. Pipeline converts `@username` to Nitter RSS: `https://nitter.privacydev.net/username/rss`
3. Standard RSS collection handles the rest

**Code Location:** `radar/services/pipeline.py` - `_get_nitter_url()` method

**Nitter Instances (fallback order):**
1. `nitter.privacydev.net`
2. `nitter.poast.org`
3. `nitter.net`

**Pre-configured Accounts:** Run `scripts/add_twitter_sources.py` to add 25+ accounts:
- **Breaking News:** @Reuters, @business, @WSJ, @FT, @CNBC, @MarketWatch
- **Speed/Headlines:** @DeItaone (Walter Bloomberg), @WatcherGuru
- **Central Banks:** @federalreserve, @ecb, @bankofengland, @NickTimiraos
- **Energy/Oil:** @JavierBlas, @OilPriceX
- **Geopolitics:** @AFP, @AP, @BBCBreaking, @spectatorindex, @IntelCrab
- **Iran-US/Middle East:** @IranIntl_En, @BarakRavid, @AliVaez, @farnaz_fassihi
- **Crypto:** @BitcoinMagazine, @zerohedge

**Adding Twitter Sources Manually:**
```python
from radar.schemas import SourceCreate
from radar.models import SourceType, SourceTier
from radar import storage
from radar.db import get_db_context

with get_db_context() as db:
    source = SourceCreate(
        name="Reuters",
        url="@Reuters",  # Just the @username
        source_type=SourceType.TWITTER,
        tier=SourceTier.PRIMARY,
        is_global=True,
        is_active=True,
    )
    storage.create_source(db, source)
```

### 8. Market Sentiment Analyzer

**Purpose:** Scheduled market sentiment reports (1H, 4H, Daily) summarizing recent events.

**Features:**
- Configurable schedules in admin panel
- Groups events by asset category
- Provides overall market sentiment (Bullish/Bearish/Neutral)
- Key events summary with trading implications

**Settings:**
```
sentiment_1h_enabled    # "1" or "0"
sentiment_4h_enabled    # "1" or "0"
sentiment_daily_enabled # "1" or "0"
```

**Code Location:** `radar/notify/sentiment_analyzer.py`

### 9. Modern Admin UI Design

**Design System:** Vision UI inspired dark theme with glassmorphism effects.

**Features:**
- Fixed sidebar navigation with icons
- Dark gradient background (#0f0c29 → #302b63 → #24243e)
- Glassmorphism cards (frosted glass effect)
- Responsive mobile design with hamburger menu
- Modern form styling with focus states
- Color-coded badges and stat cards

**CSS Variables:**
```css
--bg-primary: #1a1a2e
--bg-secondary: #16213e
--bg-card: rgba(255, 255, 255, 0.05)
--text-primary: #ffffff
--text-secondary: rgba(255, 255, 255, 0.7)
--accent: #4f46e5
--success: #10b981
--warning: #f59e0b
--danger: #ef4444
```

**Template Structure:** All CSS is embedded in `base.html` for simplicity.

---

## Key Files to Know

### Most Important Files

| File | What It Does | When to Edit |
|------|--------------|--------------|
| `radar/services/pipeline.py` | Main orchestration + Twitter/Nitter handling | Adding new pipeline steps, new source types |
| `radar/detect/rules.py` | Keyword/entity matching | Changing match logic |
| `radar/detect/scoring.py` | Severity calculation | Adjusting scoring |
| `radar/detect/llm_openrouter.py` | LLM analysis + market relevance + trading setup | Changing prompts, adding new analysis fields |
| `radar/notify/telegram.py` | Alert formatting (compact trading format) | Changing alert format, adding new sections |
| `radar/notify/sentiment_analyzer.py` | Market sentiment reports | Changing sentiment analysis |
| `radar/notify/translator.py` | Persian translation | Changing translation prompt |
| `radar/admin/routes.py` | All admin endpoints | Adding admin features |
| `radar/admin/templates/base.html` | Modern dark theme UI, sidebar nav | Changing admin styling |
| `radar/models.py` | Database schema | Adding new tables/fields |
| `radar/config.py` | Environment config | Adding new settings |
| `scripts/add_twitter_sources.py` | Pre-configured X accounts | Adding new Twitter sources |

### Template Files

| Template | Purpose |
|----------|---------|
| `templates/base.html` | Dark theme base, sidebar navigation, all CSS embedded |
| `templates/login.html` | Glassmorphism login with animated background |
| `templates/settings.html` | Config UI, daily summary, sentiment schedule |
| `templates/costs.html` | API cost tracking and breakdown |
| `templates/events.html` | Event list with test-send |
| `templates/watch_item_edit.html` | Complex form with JSON |
| `templates/learning.html` | ML learning system, source reliability |

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

### Recent Changes

1. **Daily Summary Feature** - LLM-powered end-of-day market wrap
2. **Scheduled Summary** - Auto-send at configured time (Tehran)
3. **Professional Formatting** - Analyst-style with confidence indicators
4. **Logical Consistency** - USD bearish → EURUSD bullish
5. **Duplicate Prevention** - USD/DXY combined entries
6. **Market Relevance Filter** - Filters political noise, social issues from alerts
7. **Combined LLM Analysis** - Relevance check + entity matching in single API call (~30-40% cost savings)
8. **Separate Translation Model** - Choose different model for Persian translation
9. **New Models Added** - GPT-5.2, GPT-5-mini, Gemini 3 Flash, DeepSeek V3.2, Grok 4.1 Fast
10. **Trading-Focused Alert Format** - Completely redesigned for professional traders:
    - Trade bias (📈 BULLISH / 📉 BEARISH / ➖ NEUTRAL)
    - Key price levels to watch
    - Timeframe (INTRADAY / SWING / POSITION)
    - Catalyst for confirmation
    - Risk factors that invalidate the trade
    - Compact format (Persian title only, no duplication)
    - Removed visual noise (progress bars, match percentages)
11. **API Cost Tracking** - Monitor OpenRouter API spending:
    - New `/admin/costs` page with daily/weekly/monthly costs
    - Cost breakdown by purpose (analysis, translation, summary)
    - Cost breakdown by model
    - Recent API calls table with token counts
    - All API calls (analysis, translation, summary) are logged automatically
12. **X/Twitter Source Support** - Add X accounts as news sources:
    - New `SourceType.TWITTER` enum value
    - Automatic Nitter RSS conversion (no API key needed)
    - Pre-configured with 25+ financial/geopolitical accounts
    - Run `scripts/add_twitter_sources.py` to populate
13. **Market Sentiment Analyzer** - Scheduled sentiment reports:
    - 1-hour, 4-hour, and daily sentiment analysis
    - Configurable in admin settings panel
    - Groups events by asset category
    - Overall market mood indicator
14. **Modern Admin UI Redesign** - Complete visual overhaul:
    - Vision UI inspired dark theme
    - Glassmorphism effects (frosted glass cards)
    - Fixed sidebar navigation with icons
    - Animated login page
    - Mobile-responsive with hamburger menu
    - All functionality preserved from previous design

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
openrouter_model          # string (for analysis)
translation_model         # string (for Persian translation)
openrouter_api_key        # string (sensitive)
telegram_bot_token        # string (sensitive)
telegram_chat_id          # string
daily_summary_enabled     # "1" or "0"
daily_summary_time        # "HH:MM" format
daily_summary_hours       # "12", "24", or "48"
daily_summary_last_sent   # "YYYY-MM-DD" (auto-set)
sentiment_1h_enabled      # "1" or "0" - hourly sentiment
sentiment_4h_enabled      # "1" or "0" - 4-hour sentiment
sentiment_daily_enabled   # "1" or "0" - daily sentiment
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

# View last 100 lines
sudo journalctl -u radarbot -n 100

# Check database
sqlite3 market_radar.db ".tables"
sqlite3 market_radar.db "SELECT * FROM app_settings"
sqlite3 market_radar.db "SELECT name, source_type, is_active FROM sources"

# Quick test
radar run --once

# Python shell with context (development)
cd /home/user/high-impact-news/market-radar-bot
source venv/bin/activate
python
>>> from radar.db import get_db_context
>>> from radar import storage
>>> with get_db_context() as db:
...     print(storage.get_stats(db))

# Python shell (production - as radarbot user)
sudo su - radarbot
cd /home/radarbot/high-impact-news/market-radar-bot
source venv/bin/activate
python

# Add Twitter sources (run on server after deployment)
cd /home/radarbot/high-impact-news/market-radar-bot
source venv/bin/activate
python scripts/add_twitter_sources.py

# Restart service after changes
sudo systemctl restart radarbot
```

---

## Scripts Directory

### `scripts/add_twitter_sources.py`

Populates the database with 25+ pre-configured X/Twitter accounts for financial news.

**Categories:**
- Financial Markets (Reuters, Bloomberg, WSJ, FT, CNBC, MarketWatch)
- Speed/Headlines (DeItaone, WatcherGuru)
- Central Banks (Fed, ECB, BOE, Nick Timiraos)
- Commodities/Energy (Javier Blas, OilPrice)
- Geopolitics (AFP, AP, BBC Breaking, Intel Crab)
- Iran-US/Middle East (Iran International, Barak Ravid, etc.)
- Crypto (Bitcoin Magazine, Zerohedge)

**Usage:**
```bash
# On production server
sudo su - radarbot
cd /home/radarbot/high-impact-news/market-radar-bot
source venv/bin/activate
python scripts/add_twitter_sources.py
```

**Output:**
```
ADD: Reuters (@Reuters) - ID: 45
ADD: Bloomberg (@business) - ID: 46
SKIP: Wall Street Journal - already exists
...
Done! Added 23 sources, skipped 2 duplicates.
```

---

## Deployment Notes

### Server Access

```bash
# SSH to server
ssh radarbot@<server-ip>

# Or switch to radarbot after SSH
sudo su - radarbot
```

### Project Location

- **Development:** `/home/user/high-impact-news/market-radar-bot`
- **Production:** `/home/radarbot/high-impact-news/market-radar-bot`

### Git Backup Tags

Before major changes, backup tags are created:

```bash
# List backup tags
git tag -l "backup-*"

# Restore to backup if needed
git checkout backup-before-ui-redesign

# Current backups:
# - backup-before-ui-redesign (before Vision UI dark theme)
```

### Updating Production

```bash
# On server as radarbot user
cd /home/radarbot/high-impact-news/market-radar-bot
git pull origin main

# Restart service
sudo systemctl restart radarbot

# Check logs
sudo journalctl -u radarbot -f
```

---

## Contact & Resources

- **Admin Panel:** http://localhost:8000/admin/
- **Production Admin:** http://<server-ip>:8000/admin/
- **Health Check:** http://localhost:8000/health
- **Logs:** `journalctl -u radarbot -f`
- **Database:** `market_radar.db` (SQLite)
- **OpenRouter Dashboard:** https://openrouter.ai/activity (for API costs)
