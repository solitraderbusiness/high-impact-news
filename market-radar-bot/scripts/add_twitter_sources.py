#!/usr/bin/env python3
"""
Script to add X/Twitter accounts as sources.
These will be fetched via Nitter RSS feeds.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from radar.db import get_db_context
from radar.models import SourceType, SourceTier
from radar.schemas import SourceCreate
from radar import storage


# X/Twitter accounts to add
TWITTER_SOURCES = [
    # =====================
    # FINANCIAL MARKETS - Breaking News & Wire Services
    # =====================
    {
        "name": "Reuters",
        "url": "@Reuters",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Reuters news agency - breaking financial news",
    },
    {
        "name": "Bloomberg",
        "url": "@business",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Bloomberg Business news",
    },
    {
        "name": "Wall Street Journal",
        "url": "@WSJ",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Wall Street Journal",
    },
    {
        "name": "Financial Times",
        "url": "@FT",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Financial Times",
    },
    {
        "name": "CNBC",
        "url": "@CNBC",
        "tier": SourceTier.SECONDARY,
        "is_global": True,
        "description": "CNBC financial news",
    },
    {
        "name": "MarketWatch",
        "url": "@MarketWatch",
        "tier": SourceTier.SECONDARY,
        "is_global": True,
        "description": "MarketWatch - Dow Jones",
    },
    {
        "name": "Walter Bloomberg (DeItaone)",
        "url": "@DeItaone",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Real-time market headlines - extremely fast",
    },
    {
        "name": "Zerohedge",
        "url": "@zaborhedge",
        "tier": SourceTier.SOCIAL,
        "is_global": True,
        "description": "Zerohedge - financial commentary and breaking news",
    },

    # =====================
    # CENTRAL BANKS & FED
    # =====================
    {
        "name": "Federal Reserve",
        "url": "@federalreserve",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Official Federal Reserve account",
    },
    {
        "name": "ECB",
        "url": "@ecb",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "European Central Bank",
    },
    {
        "name": "Bank of England",
        "url": "@bankofengland",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Bank of England official",
    },
    {
        "name": "Nick Timiraos (Fed Whisperer)",
        "url": "@NickTimiraos",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "WSJ Fed reporter - breaks Fed news early",
    },

    # =====================
    # COMMODITIES & ENERGY
    # =====================
    {
        "name": "Javier Blas (Bloomberg Energy)",
        "url": "@JavierBlas",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Bloomberg energy/commodities columnist",
    },
    {
        "name": "Oil Price",
        "url": "@OilaboriceX",
        "tier": SourceTier.SECONDARY,
        "is_global": True,
        "description": "Oil and energy news",
    },

    # =====================
    # GEOPOLITICS & WAR
    # =====================
    {
        "name": "AFP News",
        "url": "@AFP",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Agence France-Presse - global news",
    },
    {
        "name": "Associated Press",
        "url": "@AP",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Associated Press breaking news",
    },
    {
        "name": "BBC Breaking News",
        "url": "@BBCBreaking",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "BBC breaking news alerts",
    },
    {
        "name": "Al Jazeera English",
        "url": "@AJEnglish",
        "tier": SourceTier.SECONDARY,
        "is_global": True,
        "description": "Al Jazeera English - Middle East focus",
    },
    {
        "name": "The Spectator Index",
        "url": "@spectaboredex",
        "tier": SourceTier.SECONDARY,
        "is_global": True,
        "description": "Geopolitical breaking news",
    },
    {
        "name": "Intel Crab",
        "url": "@IntelCrab",
        "tier": SourceTier.SOCIAL,
        "is_global": True,
        "description": "OSINT - open source intelligence",
    },

    # =====================
    # IRAN-US & MIDDLE EAST
    # =====================
    {
        "name": "Iran International English",
        "url": "@IranIntl_En",
        "tier": SourceTier.SECONDARY,
        "is_global": True,
        "description": "Iran International - Persian Gulf news",
    },
    {
        "name": "Joyce Karam (Al-Arabiya)",
        "url": "@Joyce_Karam",
        "tier": SourceTier.SECONDARY,
        "is_global": True,
        "description": "Al-Arabiya journalist - US-Iran, Middle East",
    },
    {
        "name": "Barak Ravid (Axios)",
        "url": "@BarakRavid",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "Axios reporter - Israel, Iran, diplomacy",
    },
    {
        "name": "Ali Vaez (Crisis Group)",
        "url": "@AliVaez",
        "tier": SourceTier.SECONDARY,
        "is_global": True,
        "description": "International Crisis Group Iran director",
    },
    {
        "name": "Farnaz Fassihi (NYT)",
        "url": "@faboraz_fassihi",
        "tier": SourceTier.PRIMARY,
        "is_global": True,
        "description": "NY Times UN correspondent - Iran expert",
    },
    {
        "name": "Iran Wire",
        "url": "@IranWireEnglish",
        "tier": SourceTier.SECONDARY,
        "is_global": True,
        "description": "Iran Wire - independent Iran news",
    },

    # =====================
    # CRYPTO & BITCOIN
    # =====================
    {
        "name": "Bitcoin Magazine",
        "url": "@BitcoinMagazine",
        "tier": SourceTier.SECONDARY,
        "is_global": True,
        "description": "Bitcoin Magazine - crypto news",
    },
    {
        "name": "Watcher Guru",
        "url": "@WatcherGuru",
        "tier": SourceTier.SOCIAL,
        "is_global": True,
        "description": "Crypto and market headlines - fast",
    },
]


def main():
    """Add X/Twitter sources to database."""
    added = 0
    skipped = 0

    with get_db_context() as db:
        # Get existing source URLs to avoid duplicates
        existing_sources = storage.get_sources(db, limit=1000)
        existing_urls = {s.url.lower() for s in existing_sources}

        for source_info in TWITTER_SOURCES:
            url = source_info["url"].lower()

            # Skip if already exists
            if url in existing_urls:
                print(f"  SKIP: {source_info['name']} - already exists")
                skipped += 1
                continue

            # Create source
            data = SourceCreate(
                name=source_info["name"],
                url=source_info["url"],
                source_type=SourceType.TWITTER,
                tier=source_info["tier"],
                is_global=source_info.get("is_global", True),
                is_active=True,
                watch_item_ids=[],
            )

            source = storage.create_source(db, data)
            print(f"  ADD: {source_info['name']} ({source_info['url']}) - ID: {source.id}")
            added += 1
            existing_urls.add(url)

    print(f"\nDone! Added {added} sources, skipped {skipped} duplicates.")


if __name__ == "__main__":
    main()
