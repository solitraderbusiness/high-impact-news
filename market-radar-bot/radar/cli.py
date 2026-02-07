"""
CLI commands for Market Radar Bot.

Commands:
- radar init-db: Initialize the database
- radar run: Run the continuous monitoring loop
- radar collect --once: Run collection once
- radar send-test: Send a test Telegram message
- radar seed: Seed the database with example data
"""

import click
import structlog

from radar.config import get_settings


def configure_logging():
    """Configure logging for CLI."""
    import logging

    settings = get_settings()

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", level=log_level)


@click.group()
def cli():
    """Market Radar Bot - Proactive Market Impact Radar"""
    configure_logging()


@cli.command()
def init_db():
    """Initialize the database schema."""
    from radar.db import init_db as do_init_db

    click.echo("Initializing database...")
    do_init_db()
    click.echo("Database initialized successfully!")


@cli.command()
def seed():
    """Seed the database with example watch items and sources."""
    from radar.db import init_db as do_init_db, get_db_context
    from radar import storage
    from radar.models import WatchItemCategory, SourceType, SourceTier
    from radar.schemas import WatchItemCreate, SourceCreate

    click.echo("Initializing database...")
    do_init_db()

    click.echo("Seeding watch items and sources...")

    with get_db_context() as db:
        # Check if already seeded
        existing = storage.get_watch_items(db, limit=1)
        if existing:
            click.echo("Database already has data. Skipping seed.")
            return

        # =================================================================
        # SEED WATCH ITEMS
        # =================================================================

        watch_items_data = [
            # Political Leaders
            {
                "name": "Donald Trump",
                "category": WatchItemCategory.PERSON,
                "description": "45th & 47th US President - major market mover on trade, tariffs, and policy",
                "keywords": ["trump", "truth social", "maga"],
                "entities": ["Donald Trump", "Trump", "President Trump"],
                "assets_affected": ["USD", "S&P 500", "DXY", "US Treasuries"],
                "severity_rules": [
                    {"pattern": "tariff", "score_bump": 15},
                    {"pattern": "executive order", "score_bump": 10},
                    {"pattern": "trade war", "score_bump": 15},
                ],
            },
            {
                "name": "Xi Jinping",
                "category": WatchItemCategory.PERSON,
                "description": "President of China - key figure in US-China relations",
                "keywords": ["china", "beijing", "ccp"],
                "entities": ["Xi Jinping", "Xi", "President Xi"],
                "assets_affected": ["CNY", "CNH", "Hang Seng", "Shanghai Composite", "Copper"],
                "severity_rules": [
                    {"pattern": "taiwan", "score_bump": 20},
                    {"pattern": "sanctions", "score_bump": 15},
                ],
            },
            {
                "name": "Vladimir Putin",
                "category": WatchItemCategory.PERSON,
                "description": "President of Russia - geopolitical risk factor",
                "keywords": ["russia", "kremlin", "moscow"],
                "entities": ["Vladimir Putin", "Putin"],
                "assets_affected": ["RUB", "Brent Oil", "Natural Gas", "Wheat"],
                "severity_rules": [
                    {"pattern": "nuclear", "score_bump": 25},
                    {"pattern": "ukraine", "score_bump": 15},
                    {"pattern": "nato", "score_bump": 10},
                ],
            },
            {
                "name": "Mohammed bin Salman (MBS)",
                "category": WatchItemCategory.PERSON,
                "description": "Crown Prince of Saudi Arabia - OPEC+ leader, oil policy",
                "keywords": ["saudi", "riyadh", "opec"],
                "entities": ["Mohammed bin Salman", "MBS", "Crown Prince"],
                "assets_affected": ["WTI Oil", "Brent Oil", "Saudi Aramco"],
                "severity_rules": [
                    {"pattern": "oil production", "score_bump": 15},
                    {"pattern": "opec cut", "score_bump": 20},
                ],
            },

            # Central Banks
            {
                "name": "Federal Reserve",
                "category": WatchItemCategory.CENTRAL_BANK,
                "description": "US central bank - most important monetary policy institution",
                "keywords": ["fed", "fomc", "monetary policy", "interest rate", "quantitative"],
                "entities": ["Federal Reserve", "Fed", "Jerome Powell", "FOMC"],
                "assets_affected": ["USD", "DXY", "S&P 500", "US Treasuries", "Gold"],
                "severity_rules": [
                    {"pattern": "rate hike", "score_bump": 20},
                    {"pattern": "rate cut", "score_bump": 20},
                    {"pattern": "emergency", "score_bump": 25},
                    {"pattern": "balance sheet", "score_bump": 10},
                ],
            },
            {
                "name": "European Central Bank",
                "category": WatchItemCategory.CENTRAL_BANK,
                "description": "ECB - Eurozone monetary policy",
                "keywords": ["ecb", "eurozone", "frankfurt"],
                "entities": ["ECB", "European Central Bank", "Christine Lagarde", "Lagarde"],
                "assets_affected": ["EUR", "EURUSD", "Euro Stoxx 50", "German Bunds"],
                "severity_rules": [
                    {"pattern": "rate", "score_bump": 15},
                    {"pattern": "fragmentation", "score_bump": 15},
                ],
            },
            {
                "name": "Bank of Japan",
                "category": WatchItemCategory.CENTRAL_BANK,
                "description": "BoJ - Japanese monetary policy, yield curve control",
                "keywords": ["boj", "japan", "yen"],
                "entities": ["Bank of Japan", "BoJ", "Kazuo Ueda", "Ueda"],
                "assets_affected": ["JPY", "USDJPY", "Nikkei 225", "JGBs"],
                "severity_rules": [
                    {"pattern": "yield curve", "score_bump": 20},
                    {"pattern": "ycc", "score_bump": 20},
                    {"pattern": "intervention", "score_bump": 15},
                ],
            },
            {
                "name": "People's Bank of China",
                "category": WatchItemCategory.CENTRAL_BANK,
                "description": "PBOC - Chinese monetary policy",
                "keywords": ["pboc", "yuan", "rmb"],
                "entities": ["PBOC", "People's Bank of China", "Pan Gongsheng"],
                "assets_affected": ["CNY", "CNH", "Hang Seng", "Copper"],
                "severity_rules": [
                    {"pattern": "devaluation", "score_bump": 20},
                    {"pattern": "rrr cut", "score_bump": 15},
                ],
            },

            # Institutions
            {
                "name": "IMF",
                "category": WatchItemCategory.INSTITUTION,
                "description": "International Monetary Fund - global financial stability",
                "keywords": ["imf", "special drawing rights", "sdr"],
                "entities": ["IMF", "International Monetary Fund", "Kristalina Georgieva"],
                "assets_affected": ["EM Currencies", "Gold"],
                "severity_rules": [
                    {"pattern": "bailout", "score_bump": 15},
                    {"pattern": "default", "score_bump": 20},
                ],
            },
            {
                "name": "World Bank",
                "category": WatchItemCategory.INSTITUTION,
                "description": "World Bank - development and economic outlook",
                "keywords": ["world bank", "ibrd"],
                "entities": ["World Bank", "Ajay Banga"],
                "assets_affected": ["EM Currencies", "Commodities"],
            },
            {
                "name": "WTO",
                "category": WatchItemCategory.INSTITUTION,
                "description": "World Trade Organization - global trade rules",
                "keywords": ["wto", "trade dispute", "tariff ruling"],
                "entities": ["WTO", "World Trade Organization"],
                "assets_affected": ["Trade-sensitive stocks", "DXY"],
                "severity_rules": [
                    {"pattern": "ruling", "score_bump": 10},
                    {"pattern": "dispute", "score_bump": 10},
                ],
            },
            {
                "name": "OPEC+",
                "category": WatchItemCategory.INSTITUTION,
                "description": "OPEC+ - oil production cartel",
                "keywords": ["opec", "oil production", "barrel"],
                "entities": ["OPEC", "OPEC+"],
                "assets_affected": ["WTI Oil", "Brent Oil", "Energy Stocks"],
                "severity_rules": [
                    {"pattern": "production cut", "score_bump": 20},
                    {"pattern": "quota", "score_bump": 15},
                ],
            },
            {
                "name": "G20",
                "category": WatchItemCategory.INSTITUTION,
                "description": "G20 - major economy coordination",
                "keywords": ["g20", "g-20", "summit"],
                "entities": ["G20", "G-20"],
                "assets_affected": ["Global indices"],
            },

            # Macro Releases
            {
                "name": "US CPI",
                "category": WatchItemCategory.MACRO_RELEASE,
                "description": "US Consumer Price Index - key inflation indicator",
                "keywords": ["cpi", "inflation", "consumer price"],
                "entities": ["CPI", "Consumer Price Index"],
                "assets_affected": ["USD", "US Treasuries", "Gold", "S&P 500"],
                "severity_rules": [
                    {"pattern": "higher than expected", "score_bump": 15},
                    {"pattern": "surprise", "score_bump": 15},
                ],
            },
            {
                "name": "Non-Farm Payrolls",
                "category": WatchItemCategory.MACRO_RELEASE,
                "description": "NFP - US employment report",
                "keywords": ["nfp", "payrolls", "jobs report", "employment"],
                "entities": ["NFP", "Non-Farm Payrolls", "Nonfarm Payrolls"],
                "assets_affected": ["USD", "S&P 500", "US Treasuries"],
                "severity_rules": [
                    {"pattern": "beat", "score_bump": 10},
                    {"pattern": "miss", "score_bump": 10},
                ],
            },
            {
                "name": "GDP Releases",
                "category": WatchItemCategory.MACRO_RELEASE,
                "description": "Gross Domestic Product data",
                "keywords": ["gdp", "economic growth", "recession"],
                "entities": ["GDP"],
                "assets_affected": ["Respective currency", "Equity indices"],
                "severity_rules": [
                    {"pattern": "recession", "score_bump": 20},
                    {"pattern": "contraction", "score_bump": 15},
                ],
            },
            {
                "name": "FOMC Decisions",
                "category": WatchItemCategory.EVENT_TYPE,
                "description": "Federal Reserve policy decisions",
                "keywords": ["fomc", "fed decision", "rate decision"],
                "entities": ["FOMC"],
                "assets_affected": ["USD", "US Treasuries", "S&P 500", "Gold"],
                "severity_rules": [
                    {"pattern": "unanimous", "score_bump": 5},
                    {"pattern": "dissent", "score_bump": 10},
                    {"pattern": "surprise", "score_bump": 20},
                ],
            },

            # Megatrends
            {
                "name": "Central Bank Gold Buying",
                "category": WatchItemCategory.MEGATREND,
                "description": "De-dollarization trend - central banks accumulating gold",
                "keywords": ["gold reserves", "gold buying", "de-dollarization"],
                "entities": [],
                "assets_affected": ["Gold", "USD", "Bitcoin"],
                "severity_rules": [
                    {"pattern": "record", "score_bump": 15},
                ],
            },
            {
                "name": "Geoeconomic Confrontation",
                "category": WatchItemCategory.MEGATREND,
                "description": "US-China decoupling, sanctions, tech war",
                "keywords": ["decoupling", "sanctions", "chip war", "tech war", "export controls"],
                "entities": [],
                "assets_affected": ["Semiconductors", "Tech stocks", "CNY", "USD"],
                "severity_rules": [
                    {"pattern": "ban", "score_bump": 15},
                    {"pattern": "restrict", "score_bump": 10},
                ],
            },
            {
                "name": "US Political Instability",
                "category": WatchItemCategory.MEGATREND,
                "description": "Domestic political risk in the United States",
                "keywords": ["government shutdown", "debt ceiling", "impeachment", "election"],
                "entities": [],
                "assets_affected": ["USD", "S&P 500", "VIX"],
                "severity_rules": [
                    {"pattern": "shutdown", "score_bump": 20},
                    {"pattern": "debt ceiling", "score_bump": 20},
                    {"pattern": "default", "score_bump": 25},
                ],
            },
        ]

        # Create watch items
        for item_data in watch_items_data:
            data = WatchItemCreate(**item_data)
            storage.create_watch_item(db, data)
            click.echo(f"  Created: {item_data['name']}")

        # =================================================================
        # SEED SOURCES
        # =================================================================

        sources_data = [
            # Primary sources - Official
            {
                "name": "Federal Reserve Press Releases",
                "url": "https://www.federalreserve.gov/feeds/press_all.xml",
                "source_type": SourceType.RSS,
                "tier": SourceTier.PRIMARY,
                "is_global": True,
            },
            {
                "name": "ECB Press Releases",
                "url": "https://www.ecb.europa.eu/rss/press.html",
                "source_type": SourceType.RSS,
                "tier": SourceTier.PRIMARY,
                "is_global": True,
            },
            {
                "name": "Bank of Japan Announcements",
                "url": "https://www.boj.or.jp/en/rss/whatsnew.xml",
                "source_type": SourceType.RSS,
                "tier": SourceTier.PRIMARY,
                "is_global": True,
            },

            # Secondary sources - Major news
            {
                "name": "Reuters Top News",
                "url": "https://www.reutersagency.com/feed/",
                "source_type": SourceType.RSS,
                "tier": SourceTier.PRIMARY,
                "is_global": True,
            },
            {
                "name": "Bloomberg Markets",
                "url": "https://feeds.bloomberg.com/markets/news.rss",
                "source_type": SourceType.RSS,
                "tier": SourceTier.PRIMARY,
                "is_global": True,
            },
            {
                "name": "Financial Times",
                "url": "https://www.ft.com/rss/home",
                "source_type": SourceType.RSS,
                "tier": SourceTier.SECONDARY,
                "is_global": True,
            },
            {
                "name": "Wall Street Journal Markets",
                "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
                "source_type": SourceType.RSS,
                "tier": SourceTier.SECONDARY,
                "is_global": True,
            },
            {
                "name": "CNBC Top News",
                "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
                "source_type": SourceType.RSS,
                "tier": SourceTier.SECONDARY,
                "is_global": True,
            },

            # Specialized sources
            {
                "name": "ZeroHedge",
                "url": "https://feeds.feedburner.com/zerohedge/feed",
                "source_type": SourceType.RSS,
                "tier": SourceTier.SOCIAL,
                "is_global": True,
            },
            {
                "name": "Politico",
                "url": "https://rss.politico.com/politics-news.xml",
                "source_type": SourceType.RSS,
                "tier": SourceTier.SECONDARY,
                "is_global": True,
            },

            # Placeholder sources (update URLs as needed)
            {
                "name": "IMF News",
                "url": "https://www.imf.org/en/News/rss",
                "source_type": SourceType.RSS,
                "tier": SourceTier.PRIMARY,
                "is_global": True,
            },
        ]

        # Create sources
        for source_data in sources_data:
            try:
                existing = storage.get_source_by_url(db, source_data["url"])
                if not existing:
                    data = SourceCreate(**source_data)
                    storage.create_source(db, data)
                    click.echo(f"  Created source: {source_data['name']}")
            except Exception as e:
                click.echo(f"  Warning: Could not create {source_data['name']}: {e}")

    click.echo(f"\nSeeded {len(watch_items_data)} watch items and {len(sources_data)} sources.")
    click.echo("Run 'radar run' to start monitoring!")


@cli.command()
@click.option("--once", is_flag=True, help="Run collection once and exit")
@click.option("--web", is_flag=True, help="Also start the web server")
@click.option("--host", default=None, help="Web server host (with --web)")
@click.option("--port", default=None, type=int, help="Web server port (with --web)")
def run(once: bool, web: bool, host: str, port: int):
    """Run the monitoring pipeline."""
    if once:
        click.echo("Running collection once...")
        from radar.services.pipeline import Pipeline

        pipeline = Pipeline()
        result = pipeline.run()

        click.echo(f"\nCollection Results:")
        click.echo(f"  Events collected: {sum(r.items_collected for r in result.collections)}")
        click.echo(f"  Detections: {len(result.detections)}")
        click.echo(f"  Alerts sent: {result.alerts_sent}")
        if result.errors:
            click.echo(f"  Errors: {len(result.errors)}")
            for err in result.errors[:5]:
                click.echo(f"    - {err}")
    else:
        click.echo("Starting continuous monitoring...")

        if web:
            # Start web server in a separate thread
            import threading
            import uvicorn
            from radar.db import init_db

            settings = get_settings()
            init_db()

            web_host = host or settings.host
            web_port = port or settings.port

            click.echo(f"Starting web server on {web_host}:{web_port}...")

            # Create uvicorn config and server
            config = uvicorn.Config(
                "radar.main:app",
                host=web_host,
                port=web_port,
                reload=False,
                log_level="info",
            )
            server = uvicorn.Server(config)

            # Run web server in background thread
            web_thread = threading.Thread(target=server.run, daemon=True)
            web_thread.start()

            click.echo(f"Web server started at http://{web_host}:{web_port}")

        click.echo("Press Ctrl+C to stop.\n")

        from radar.services.scheduler import run_scheduler
        run_scheduler(blocking=True)


@cli.command()
def collect():
    """Run collection only (no detection/alerting)."""
    click.echo("Running collection...")
    from radar.db import init_db
    from radar.services.pipeline import Pipeline

    init_db()
    pipeline = Pipeline()
    results = pipeline.collect_once()

    total = sum(r.items_collected for r in results)
    click.echo(f"\nCollected {total} new events from {len(results)} sources.")

    for r in results:
        if r.items_collected > 0:
            click.echo(f"  {r.source_name}: {r.items_collected} items")


@cli.command("send-test")
def send_test():
    """Send a test Telegram message."""
    from radar.notify.telegram import TelegramNotifier

    click.echo("Sending test message...")
    notifier = TelegramNotifier()

    if not notifier.is_available:
        click.echo("Error: Telegram is not configured.")
        click.echo("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return

    result = notifier.send_test_message()

    if result.success:
        click.echo(f"Test message sent successfully! (Message ID: {result.message_id})")
    else:
        click.echo(f"Failed to send: {result.error}")


@cli.command()
@click.option("--host", default=None, help="Host to bind to")
@click.option("--port", default=None, type=int, help="Port to bind to")
def serve(host: str, port: int):
    """Start the web server."""
    import uvicorn
    from radar.db import init_db

    settings = get_settings()

    init_db()

    uvicorn.run(
        "radar.main:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=False,
    )


if __name__ == "__main__":
    cli()
