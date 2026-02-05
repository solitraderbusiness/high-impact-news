# Market Radar Bot

**Proactive Market Impact Radar** - A production-grade system for monitoring influential people, institutions, and events, detecting market-moving news, and sending timely alerts via Telegram.

## Features

- **Watchlist-Driven Monitoring**: Track political leaders, central banks, institutions, macro releases, and megatrends
- **Multi-Source Collection**: RSS feeds and web page scraping with stateful polling (ETags, Last-Modified)
- **Intelligent Detection**: Rule-based matching with optional OpenRouter LLM fallback
- **Severity Scoring**: Configurable 0-100 scoring based on source tier, recency, keywords, and custom rules
- **Citation-Based Alerts**: Every alert includes exact quotes from the source text
- **Deduplication**: Content hashing and cooldown management to prevent alert fatigue
- **Admin Panel**: Web-based UI for managing watch items, sources, and viewing events/alerts
- **Docker Ready**: Complete Docker setup for easy deployment

## Quick Start

### Local Development

1. **Clone and setup:**
```bash
cd market-radar-bot
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

> **IMPORTANT:** The `pip install -e .` command requires `pyproject.toml` to exist in the current directory. This file defines the `radar` CLI command. If you get "No module named 'radar'" errors, ensure `pyproject.toml` exists and re-run `pip install -e .`. See [DEPLOYMENT.md](DEPLOYMENT.md#missing-pyprojecttoml) for troubleshooting.

2. **Configure environment:**
```bash
cp .env.example .env
# Edit .env with your settings (see Configuration section)
```

3. **Initialize and seed database:**
```bash
radar init-db
radar seed  # Seeds 20+ watch items and sample sources
```

4. **Run the admin panel:**
```bash
radar serve
# Visit http://localhost:8000/admin
# Default login: admin / changeme (from .env)
```

5. **Start monitoring (in another terminal):**
```bash
radar run
# Or run once: radar run --once
```

### Docker Deployment

1. **Configure:**
```bash
cp .env.example .env
# Edit .env with your settings
```

2. **Start services:**
```bash
# Initialize and seed (first time only)
docker compose run --rm setup

# Start web + monitoring
docker compose up -d

# View logs
docker compose logs -f radar
```

3. **Access admin panel:** http://localhost:8000/admin

## Configuration

### Environment Variables

```bash
# Database
DATABASE_URL=sqlite:///./market_radar.db  # Or postgresql://...

# Admin Panel Authentication
ADMIN_USERNAME=admin
ADMIN_PASSWORD=changeme_secure_password_123
SECRET_KEY=change_this_to_a_random_secret_key_at_least_32_chars

# Telegram (required for alerts)
TELEGRAM_BOT_TOKEN=your-bot-token-from-botfather
TELEGRAM_CHAT_ID=your-chat-id

# OpenRouter LLM (optional - system works without it)
OPENROUTER_API_KEY=your-api-key
OPENROUTER_MODEL=anthropic/claude-3-haiku
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Thresholds
ALERT_THRESHOLD=70           # Severity score to trigger alert
DIGEST_THRESHOLD=30          # Score for digest inclusion
LLM_CONFIDENCE_THRESHOLD=0.6 # Minimum LLM confidence

# Polling
POLL_INTERVAL_SECONDS=60     # How often to check sources
DEFAULT_COOLDOWN_MINUTES=15  # Time between alerts for same item

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json              # or "console" for development
```

### Getting Telegram Credentials

1. **Create a bot:** Message [@BotFather](https://t.me/BotFather) on Telegram
   - Send `/newbot` and follow instructions
   - Copy the bot token

2. **Get your chat ID:**
   - Message your bot
   - Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   - Find `"chat":{"id":YOUR_CHAT_ID}` in the response

### Getting OpenRouter API Key (Optional)

1. Visit [OpenRouter](https://openrouter.ai)
2. Create an account and generate an API key
3. Add credits to your account

The system works in **rules-only mode** without OpenRouter. LLM fallback is used only when rule-based matching has low confidence.

## Usage

### CLI Commands

```bash
# Initialize database
radar init-db

# Seed with example data
radar seed

# Start web server
radar serve --host 0.0.0.0 --port 8000

# Run continuous monitoring
radar run

# Run collection once
radar run --once
radar collect  # Collection only, no detection

# Test Telegram integration
radar send-test
```

### Admin Panel

Access at `http://localhost:8000/admin` (login required).

**Pages:**
- **Watch Items**: Manage people, institutions, events to monitor
- **Sources**: Manage RSS feeds and web pages
- **Events**: View collected articles/news
- **Alerts**: View sent alert history
- **Settings**: View configuration and test integrations

### Adding a New Watch Item

1. Go to `/admin/watch-items` → "Add Watch Item"
2. Fill in:
   - **Name**: e.g., "Bank of England"
   - **Category**: central_bank
   - **Keywords**: boe, bank of england, monetary policy, gilt
   - **Entities**: Bank of England, BoE, Andrew Bailey (exact matches)
   - **Severity Rules**: JSON array, e.g., `[{"pattern": "rate hike", "score_bump": 15}]`
   - **Assets Affected**: GBP, GBPUSD, UK Gilts
   - **Cooldown**: Minutes between alerts (default 15)
3. Optionally link specific sources
4. Save

### Adding a New Source

1. Go to `/admin/sources` → "Add Source"
2. Fill in:
   - **Name**: Descriptive name
   - **URL**: RSS feed or web page URL
   - **Type**: RSS or WEB
   - **Tier**: PRIMARY (official sources), SECONDARY (news), SOCIAL (blogs)
   - **Global**: If checked, scanned for all watch items
   - **Content Selector**: CSS selector for web pages (optional)
3. Optionally link to specific watch items
4. Save

## Scoring System

Severity scores (0-100) are calculated from:

| Component | Points | Description |
|-----------|--------|-------------|
| Base Confidence | 0-40 | From rule/LLM match confidence |
| Source Tier | 0-20 | PRIMARY: 20, SECONDARY: 10, SOCIAL: 0 |
| Recency | 0-15 | <1h: 15, <6h: 12, <24h: 8, <7d: 4 |
| Keywords | 0-25 | High-impact terms (rate hike, war, sanctions, etc.) |
| Custom Rules | Variable | Per-watch-item severity rules |

**Alert Thresholds:**
- Score ≥ 70 (default): Immediate alert
- Score ≥ 90: Bypasses cooldown
- Score ≥ 30: Included in digest (future feature)

## Deduplication

Events are deduplicated by:

1. **Content Hash**: SHA256 of (URL + title + published_at)
   - Prevents exact duplicates

2. **Title Hash**: Normalized title hash
   - Catches reformatted versions of same story

3. **Cooldown**: Per-watch-item timer
   - Prevents alert fatigue
   - Bypassed for severity ≥ 90

## Architecture

```
market-radar-bot/
├── radar/
│   ├── main.py           # FastAPI app
│   ├── config.py         # Settings management
│   ├── db.py             # Database setup
│   ├── models.py         # SQLAlchemy models
│   ├── schemas.py        # Pydantic schemas
│   ├── auth.py           # Admin authentication
│   ├── storage.py        # Database helpers
│   ├── collectors/       # RSS & web collectors
│   ├── detect/           # Matching & scoring
│   │   ├── rules.py      # Rule-based matching
│   │   ├── llm_openrouter.py  # LLM fallback
│   │   ├── scoring.py    # Severity scoring
│   │   └── dedup.py      # Deduplication
│   ├── notify/           # Telegram notifier
│   ├── services/         # Scheduler & pipeline
│   ├── admin/            # Admin panel routes & templates
│   └── cli.py            # CLI commands
├── tests/                # Unit tests
├── Dockerfile
└── docker-compose.yml
```

## Data Model

```
watch_items          sources              events
├── id               ├── id               ├── id
├── name             ├── name             ├── source_id
├── category         ├── url              ├── url
├── keywords[]       ├── source_type      ├── title
├── entities[]       ├── tier             ├── raw_text
├── severity_rules[] ├── is_global        ├── published_at
├── assets_affected[]├── is_active        ├── content_hash
└── cooldown_minutes └── content_selector └── is_processed

        watch_item_sources (M2M)         detections
        ├── watch_item_id                ├── event_id
        └── source_id                    ├── watch_item_id
                                         ├── match_confidence
                                         ├── trigger_spans[]
                                         ├── severity_score
                                         └── is_alerted

        alerts_sent                      source_state
        ├── detection_id                 ├── source_id
        ├── message_text                 ├── etag
        ├── is_success                   ├── last_modified
        └── sent_at                      └── last_fetched_at
```

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run with coverage
pytest --cov=radar --cov-report=html

# Run specific test file
pytest tests/test_scoring.py -v
```

### Adding New Collectors

Create a new collector in `radar/collectors/`:

```python
from radar.collectors.rss import CollectedItem

class MyCollector:
    def collect(self, url: str, ...) -> List[CollectedItem]:
        # Fetch and parse
        # Return list of CollectedItem
        pass
```

### Adding New Detection Logic

Extend `radar/detect/rules.py` or create new matchers:

```python
class CustomMatcher:
    def match(self, text: str, title: str, watch_items: List) -> List[Match]:
        # Your matching logic
        pass
```

## Troubleshooting

### Common Issues

**"Telegram not configured"**
- Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`
- Use `radar send-test` to verify

**"No events collected"**
- Check source URLs are accessible
- View `/admin/sources` for error messages
- Check logs: `docker compose logs radar`

**"LLM fallback not working"**
- System works without LLM (rules-only mode)
- Set `OPENROUTER_API_KEY` if you want LLM fallback
- Check API key has credits

**"Too many/few alerts"**
- Adjust `ALERT_THRESHOLD` (default 70)
- Adjust per-item `cooldown_minutes`
- Review severity rules

### Logs

```bash
# Docker logs
docker compose logs -f radar
docker compose logs -f web

# Local development
LOG_LEVEL=DEBUG radar run
```

## License

MIT License - See LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Write tests for new functionality
4. Submit a pull request

### For Developers

**Critical Files:**
- `pyproject.toml` - Defines the package, dependencies, and the `radar` CLI command. **Must exist** for the app to work.
- `radar/cli.py` - CLI entry point defined in `[project.scripts]` section
- `.env` - Environment configuration (copy from `.env.example`)

**After cloning or pulling changes:**
```bash
cd market-radar-bot
source venv/bin/activate
pip install -e .  # Always run this after pulling code changes
```

**Git Branch Workflow:**
- Feature branches should be prefixed with `claude/` or your identifier
- Always test locally before deploying
- After merging, remember to run `pip install -e .` on the server

**Common Developer Issues:**
- If `radar` command not found: Run `pip install -e .`
- If imports fail: Ensure you're in the venv (`source venv/bin/activate`)
- If pyproject.toml missing after git operations: See [DEPLOYMENT.md](DEPLOYMENT.md#missing-pyprojecttoml)

---

**Note:** Some seeded RSS feed URLs may be placeholders or may change. Update them in the admin panel as needed. Check the source's official website for current RSS feed URLs.
