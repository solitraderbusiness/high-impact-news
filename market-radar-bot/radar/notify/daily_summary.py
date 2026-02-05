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
    news_count: int = 0
    key_drivers: List[str] = field(default_factory=list)
    price_change_pct: Optional[float] = None
    current_price: Optional[float] = None

    @property
    def sentiment_emoji(self) -> str:
        """Get emoji for sentiment."""
        if self.sentiment == "BULLISH":
            return "📈"
        elif self.sentiment == "BEARISH":
            return "📉"
        else:
            return "➖"


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
                symbol = asset_data.get("symbol", "").upper()
                if symbol:
                    assets[symbol] = AssetSentiment(
                        symbol=symbol,
                        sentiment=asset_data.get("sentiment", "NEUTRAL").upper(),
                        news_count=asset_news_count.get(symbol, 0),
                        key_drivers=asset_data.get("key_drivers", []),
                    )

        # Add any assets that were in news but not in LLM response
        for asset, count in asset_news_count.items():
            if asset not in assets:
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

        prompt = f"""You are a senior financial market analyst. Analyze the following market news from the last 24 hours and provide a sentiment summary.

NEWS ITEMS:
{news_text}

TASK:
1. For each major asset mentioned (GOLD, USD, DXY, EUR, EURUSD, GBP, JPY, OIL, BTC, S&P 500, US TREASURIES, GERMAN BUNDS), determine:
   - Overall sentiment: BULLISH, BEARISH, or NEUTRAL
   - Key drivers (1-2 bullet points explaining why)

2. Write a brief 2-3 sentence market overview.

Respond in JSON format:
{{
    "overview": "<brief 2-3 sentence market summary>",
    "assets": [
        {{
            "symbol": "GOLD",
            "sentiment": "BULLISH",
            "key_drivers": ["Safe haven demand amid uncertainty", "Fed policy expectations"]
        }},
        {{
            "symbol": "USD",
            "sentiment": "BEARISH",
            "key_drivers": ["Rate cut expectations", "Trade policy uncertainty"]
        }}
    ]
}}

IMPORTANT:
- Only include assets that have relevant news
- Be specific about what's driving sentiment
- Sentiment should reflect the NET impact of all news (not just one headline)
- Consider how different news items might offset or reinforce each other"""

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
                "max_tokens": 1500,
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
        """Format summary as Telegram message."""
        import pytz

        tehran_tz = pytz.timezone("Asia/Tehran")
        tehran_time = summary.date.replace(tzinfo=pytz.UTC).astimezone(tehran_tz)

        lines = [
            "📊 <b>DAILY MARKET SENTIMENT SUMMARY</b>",
            f"📅 {tehran_time.strftime('%Y-%m-%d')}",
            "",
        ]

        # Market overview from LLM
        if summary.market_overview:
            lines.append(f"<i>{summary.market_overview}</i>")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # Sort assets by news count
        sorted_assets = sorted(
            summary.assets.values(),
            key=lambda x: x.news_count,
            reverse=True
        )

        for asset in sorted_assets[:10]:  # Top 10 assets
            emoji = asset.sentiment_emoji
            sentiment = asset.sentiment

            # Price change line
            price_line = ""
            if asset.price_change_pct is not None:
                price_emoji = "🟢" if asset.price_change_pct >= 0 else "🔴"
                price_sign = "+" if asset.price_change_pct >= 0 else ""
                price_line = f"   {price_emoji} 24h: {price_sign}{asset.price_change_pct:.2f}%"
                if asset.current_price:
                    price_line += f" (${asset.current_price:,.2f})"

            lines.append(f"<b>{asset.symbol}</b>")
            lines.append(f"{emoji} {sentiment} ({asset.news_count} news)")

            # Key drivers from LLM
            if asset.key_drivers:
                for driver in asset.key_drivers[:2]:
                    lines.append(f"   • {driver[:60]}")

            if price_line:
                lines.append(price_line)

            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # Top stories
        if summary.top_stories:
            lines.append("<b>📝 TOP STORIES:</b>")
            for i, (title, score, watch_item) in enumerate(summary.top_stories[:5], 1):
                lines.append(f"{i}. {title[:50]}{'...' if len(title) > 50 else ''}")
            lines.append("")

        lines.append(f"📊 Total alerts: {summary.total_alerts}")
        lines.append(f"🕐 {tehran_time.strftime('%H:%M')} Tehran")

        return "\n".join(lines)
