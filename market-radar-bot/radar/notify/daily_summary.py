"""
Daily summary generator for market news sentiment and price changes.
Uses LLM to analyze sentiment across all news for each asset.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
import structlog

from radar.config import get_settings

logger = structlog.get_logger()


@dataclass
class AssetSentiment:
    """Sentiment data for a single asset."""
    symbol: str
    sentiment: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL
    confidence: str = "MEDIUM"  # HIGH, MEDIUM, LOW
    news_count: int = 0
    key_drivers: List[str] = field(default_factory=list)
    outlook: str = ""
    price_change_pct: Optional[float] = None
    current_price: Optional[float] = None

    @property
    def sentiment_emoji(self) -> str:
        """Get emoji for sentiment."""
        if self.sentiment == "BULLISH":
            return "🟢"
        elif self.sentiment == "BEARISH":
            return "🔴"
        else:
            return "⚪"

    @property
    def confidence_indicator(self) -> str:
        """Get confidence indicator."""
        if self.confidence == "HIGH":
            return "●●●"
        elif self.confidence == "MEDIUM":
            return "●●○"
        else:
            return "●○○"


@dataclass
class DailySummary:
    """Complete daily summary."""
    date: datetime
    assets: Dict[str, AssetSentiment]
    total_alerts: int
    top_stories: List[Tuple[str, int, str]]  # (title, score, watch_item)
    market_overview: str = ""  # LLM-generated market overview


class DailySummaryGenerator:
    """Generates daily market sentiment summaries using LLM."""

    # Key assets to track
    KEY_ASSETS = [
        "GOLD", "USD", "DXY", "EUR", "EURUSD", "GBP", "JPY",
        "OIL", "BTC", "S&P 500", "US TREASURIES", "GERMAN BUNDS"
    ]

    def __init__(self):
        self.settings = get_settings()

    def generate_summary(self, db, hours: int = 24) -> Optional[DailySummary]:
        """
        Generate a daily summary from recent alerts using LLM.

        Args:
            db: Database session
            hours: Number of hours to look back

        Returns:
            DailySummary object or None if no data
        """
        from radar import storage

        cutoff = datetime.utcnow() - timedelta(hours=hours)

        # Get recent detections with alerts
        detections = storage.get_recent_detections_for_summary(db, since=cutoff)

        if not detections:
            logger.info("no_detections_for_summary", hours=hours)
            return None

        # Collect news titles and their assets
        news_items = []
        top_stories = []
        asset_news_count: Dict[str, int] = {}

        for detection in detections:
            if detection.event:
                title = detection.event.title or "Untitled"
                news_items.append({
                    "title": title,
                    "assets": detection.assets_affected or [],
                    "score": detection.severity_score,
                    "watch_item": detection.watch_item.name if detection.watch_item else "Unknown",
                })

                # Count asset mentions
                for asset in (detection.assets_affected or []):
                    asset_upper = asset.upper()
                    asset_news_count[asset_upper] = asset_news_count.get(asset_upper, 0) + 1

                # Track top stories
                top_stories.append((
                    title,
                    detection.severity_score,
                    detection.watch_item.name if detection.watch_item else "Unknown"
                ))

        # Sort top stories by severity
        top_stories.sort(key=lambda x: x[1], reverse=True)
        top_stories = top_stories[:5]

        # Use LLM to analyze sentiment
        llm_analysis = self._analyze_with_llm(news_items)

        # Build asset sentiments from LLM analysis
        assets: Dict[str, AssetSentiment] = {}

        if llm_analysis:
            for asset_data in llm_analysis.get("assets", []):
                symbol = asset_data.get("symbol", "")
                if symbol:
                    # Try to match news count with variations
                    count = asset_news_count.get(symbol.upper(), 0)
                    # Handle combined symbols like "USD (DXY)"
                    if "USD" in symbol.upper():
                        count = max(count, asset_news_count.get("USD", 0), asset_news_count.get("DXY", 0))

                    assets[symbol] = AssetSentiment(
                        symbol=symbol,
                        sentiment=asset_data.get("sentiment", "NEUTRAL").upper(),
                        confidence=asset_data.get("confidence", "MEDIUM").upper(),
                        news_count=count,
                        key_drivers=asset_data.get("key_drivers", []),
                        outlook=asset_data.get("outlook", ""),
                    )

        # Add any assets that were in news but not in LLM response
        # But skip assets that are already covered by combined entries (e.g., "USD (DXY)")
        covered_assets = set()
        for symbol in assets.keys():
            symbol_upper = symbol.upper()
            # Track what's covered by combined symbols
            if "USD" in symbol_upper:
                covered_assets.add("USD")
            if "DXY" in symbol_upper:
                covered_assets.add("DXY")
            if "EUR" in symbol_upper and "USD" in symbol_upper:
                covered_assets.add("EURUSD")
            covered_assets.add(symbol_upper)

        for asset, count in asset_news_count.items():
            asset_upper = asset.upper()
            # Skip if already covered by LLM analysis
            if asset_upper in covered_assets:
                continue
            # Skip if this is a variation already covered
            if asset_upper in ["USD", "DXY"] and any("USD" in s.upper() for s in assets.keys()):
                continue

            assets[asset] = AssetSentiment(
                symbol=asset,
                sentiment="NEUTRAL",
                news_count=count,
            )

        # Fetch price changes
        self._fetch_price_changes(assets)

        return DailySummary(
            date=datetime.utcnow(),
            assets=assets,
            total_alerts=len(detections),
            top_stories=top_stories,
            market_overview=llm_analysis.get("overview", "") if llm_analysis else "",
        )

    def _analyze_with_llm(self, news_items: List[dict]) -> Optional[dict]:
        """
        Use LLM to analyze sentiment for all news items.
        """
        if not self.settings.openrouter_api_key:
            logger.warning("openrouter_not_configured_for_summary")
            return None

        if not news_items:
            return None

        # Build news list for prompt
        news_text = "\n".join([
            f"- {item['title']} (Score: {item['score']}%, Assets: {', '.join(item['assets'])})"
            for item in news_items[:50]  # Limit to 50 news items
        ])

        prompt = f"""You are a senior financial market analyst at a major investment bank writing an end-of-day market summary for institutional clients.

TODAY'S NEWS FLOW:
{news_text}

TASK: Write a professional analyst-style daily market summary.

CRITICAL RULES:
1. LOGICAL CONSISTENCY IS MANDATORY:
   - If USD is BEARISH and EUR is NEUTRAL → EURUSD must be BULLISH (weaker dollar = higher EUR/USD)
   - If USD is BULLISH and EUR is NEUTRAL → EURUSD must be BEARISH
   - If GOLD is BULLISH and USD is BEARISH → These are consistent (inverse correlation)
   - DXY and USD must have the SAME sentiment (they measure the same thing)

2. Consolidate related instruments - don't list both USD and DXY separately with different views

3. For each asset, provide actionable insight, not just a description

4. The overview should read like a Bloomberg or Reuters market wrap

Respond in JSON format:
{{
    "overview": "<Write 3-4 sentences as a senior market strategist would. Start with the dominant theme, then key drivers, then outlook. Be specific about direction and catalysts.>",
    "assets": [
        {{
            "symbol": "GOLD",
            "sentiment": "BULLISH",
            "confidence": "HIGH",
            "key_drivers": ["Safe-haven demand on risk-off sentiment", "Real yields declining as Fed cut expectations rise"],
            "outlook": "Targeting $2,080 resistance; dips toward $2,020 support likely bought"
        }},
        {{
            "symbol": "USD (DXY)",
            "sentiment": "BEARISH",
            "confidence": "MEDIUM",
            "key_drivers": ["Fed rate cut pricing increasing", "Trade policy uncertainty weighing"],
            "outlook": "Index testing 103.50 support; break opens 102.80"
        }},
        {{
            "symbol": "EURUSD",
            "sentiment": "BULLISH",
            "confidence": "MEDIUM",
            "key_drivers": ["USD weakness dominant factor", "ECB holding steady provides relative support"],
            "outlook": "Path of least resistance higher toward 1.0950"
        }}
    ]
}}

ASSET GUIDELINES:
- Combine USD/DXY into one entry as "USD (DXY)"
- EURUSD sentiment must be logically consistent with USD sentiment
- Include: GOLD, USD (DXY), EURUSD, US TREASURIES, S&P 500, OIL if relevant
- confidence: HIGH (strong consensus), MEDIUM (mixed signals), LOW (uncertain)
- outlook: Brief 1-sentence trading view with key levels if possible

Remember: Institutional clients will act on this analysis. Be precise and logically consistent."""

        try:
            headers = {
                "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://market-radar-bot.local",
                "X-Title": "Market Radar Bot",
            }

            payload = {
                "model": self.settings.openrouter_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 2000,
            }

            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{self.settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()

            data = response.json()
            content = data["choices"][0]["message"]["content"]

            # Parse JSON from response
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())

            logger.warning("no_json_in_llm_summary_response")
            return None

        except Exception as e:
            logger.error("llm_summary_error", error=str(e))
            return None

    def _fetch_price_changes(self, assets: Dict[str, AssetSentiment]) -> None:
        """Fetch 24h price changes for assets."""
        YAHOO_SYMBOLS = {
            "GOLD": "GC=F",
            "USD": "DX-Y.NYB",
            "DXY": "DX-Y.NYB",
            "EUR": "EURUSD=X",
            "EURUSD": "EURUSD=X",
            "GBP": "GBPUSD=X",
            "GBPUSD": "GBPUSD=X",
            "JPY": "JPY=X",
            "USDJPY": "JPY=X",
            "OIL": "CL=F",
            "BTC": "BTC-USD",
            "S&P 500": "^GSPC",
        }

        for asset_name, asset_data in assets.items():
            yahoo_symbol = YAHOO_SYMBOLS.get(asset_name)
            if not yahoo_symbol:
                continue

            try:
                price_info = self._get_yahoo_price(yahoo_symbol)
                if price_info:
                    asset_data.current_price = price_info.get("price")
                    asset_data.price_change_pct = price_info.get("change_pct")
            except Exception as e:
                logger.warning("price_fetch_error", asset=asset_name, error=str(e))

    def _get_yahoo_price(self, symbol: str) -> Optional[Dict]:
        """Get price from Yahoo Finance."""
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            params = {"interval": "1d", "range": "2d"}

            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params=params)
                data = response.json()

            result = data.get("chart", {}).get("result", [])
            if not result:
                return None

            meta = result[0].get("meta", {})
            current_price = meta.get("regularMarketPrice")
            previous_close = meta.get("previousClose") or meta.get("chartPreviousClose")

            if current_price and previous_close:
                change_pct = ((current_price - previous_close) / previous_close) * 100
                return {
                    "price": current_price,
                    "change_pct": round(change_pct, 2)
                }

        except Exception as e:
            logger.debug("yahoo_price_error", symbol=symbol, error=str(e))

        return None

    def format_telegram_message(self, summary: DailySummary) -> str:
        """Format summary as professional Telegram message."""
        import pytz

        tehran_tz = pytz.timezone("Asia/Tehran")
        tehran_time = summary.date.replace(tzinfo=pytz.UTC).astimezone(tehran_tz)

        lines = [
            "📊 <b>MARKET DAILY WRAP</b>",
            f"<i>{tehran_time.strftime('%A, %B %d, %Y')}</i>",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        # Market overview from LLM
        if summary.market_overview:
            lines.append("<b>MARKET OVERVIEW</b>")
            lines.append(f"{summary.market_overview}")
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("")

        lines.append("<b>ASSET VIEWS</b>")
        lines.append("")

        # Sort assets - prioritize those with analysis, then by sentiment strength
        def sort_key(a):
            sentiment_order = {"BULLISH": 0, "BEARISH": 1, "NEUTRAL": 2}
            confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            return (
                0 if a.outlook else 1,  # Assets with outlook first
                sentiment_order.get(a.sentiment, 2),
                confidence_order.get(a.confidence, 1),
            )

        sorted_assets = sorted(summary.assets.values(), key=sort_key)

        for asset in sorted_assets[:8]:  # Top 8 assets
            emoji = asset.sentiment_emoji
            sentiment = asset.sentiment
            conf = asset.confidence_indicator

            # Header line with sentiment
            lines.append(f"{emoji} <b>{asset.symbol}</b> — {sentiment} {conf}")

            # Key drivers
            if asset.key_drivers:
                for driver in asset.key_drivers[:2]:
                    lines.append(f"   ▸ {driver[:70]}")

            # Outlook (trading view)
            if asset.outlook:
                lines.append(f"   <i>→ {asset.outlook[:80]}</i>")

            # Price change
            if asset.price_change_pct is not None:
                price_emoji = "▲" if asset.price_change_pct >= 0 else "▼"
                price_sign = "+" if asset.price_change_pct >= 0 else ""
                price_str = f"   {price_emoji} Today: {price_sign}{asset.price_change_pct:.2f}%"
                if asset.current_price:
                    price_str += f" @ ${asset.current_price:,.2f}"
                lines.append(price_str)

            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # Top headlines
        if summary.top_stories:
            lines.append("<b>KEY HEADLINES</b>")
            for i, (title, score, watch_item) in enumerate(summary.top_stories[:5], 1):
                lines.append(f"{i}. {title[:55]}{'...' if len(title) > 55 else ''}")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>Based on {summary.total_alerts} alerts • {tehran_time.strftime('%H:%M')} Tehran</i>")

        return "\n".join(lines)
