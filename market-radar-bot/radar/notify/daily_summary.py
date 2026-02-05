"""
Daily summary generator for market news sentiment and price changes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import httpx
import structlog

from radar.config import get_settings

logger = structlog.get_logger()


@dataclass
class AssetSentiment:
    """Sentiment data for a single asset."""
    symbol: str
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    total_mentions: int = 0
    weighted_score: float = 0.0  # Weighted by severity
    top_news: List[str] = field(default_factory=list)
    price_change_pct: Optional[float] = None
    current_price: Optional[float] = None

    @property
    def sentiment(self) -> str:
        """Get overall sentiment."""
        if self.bullish_count > self.bearish_count * 1.5:
            return "BULLISH"
        elif self.bearish_count > self.bullish_count * 1.5:
            return "BEARISH"
        else:
            return "NEUTRAL"

    @property
    def sentiment_emoji(self) -> str:
        """Get emoji for sentiment."""
        s = self.sentiment
        if s == "BULLISH":
            return "📈"
        elif s == "BEARISH":
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


class DailySummaryGenerator:
    """Generates daily market sentiment summaries."""

    # Asset symbols to track (prioritized order)
    TRACKED_ASSETS = [
        "GOLD", "XAU", "XAUUSD",
        "USD", "DXY",
        "EUR", "EURUSD",
        "GBP", "GBPUSD",
        "JPY", "USDJPY",
        "OIL", "WTI", "BRENT", "CRUDE",
        "BTC", "BITCOIN",
        "SPX", "S&P 500", "SP500",
    ]

    # Map variations to canonical names
    ASSET_ALIASES = {
        "XAU": "GOLD",
        "XAUUSD": "GOLD",
        "WTI": "OIL",
        "BRENT": "OIL",
        "CRUDE": "OIL",
        "BITCOIN": "BTC",
        "SP500": "S&P 500",
        "SPX": "S&P 500",
    }

    def __init__(self):
        self.settings = get_settings()

    def generate_summary(self, db, hours: int = 24) -> Optional[DailySummary]:
        """
        Generate a daily summary from recent alerts.

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

        # Aggregate by asset
        assets: Dict[str, AssetSentiment] = defaultdict(
            lambda: AssetSentiment(symbol="")
        )

        top_stories = []

        for detection in detections:
            # Track top stories
            if detection.event and detection.watch_item:
                top_stories.append((
                    detection.event.title or "Untitled",
                    detection.severity_score,
                    detection.watch_item.name
                ))

            # Get assets with direction from the detection
            # Try to parse from llm_reasoning or use assets_affected
            assets_with_direction = self._extract_assets_with_direction(detection)

            for asset_symbol, direction in assets_with_direction:
                # Normalize asset name
                canonical = self.ASSET_ALIASES.get(asset_symbol.upper(), asset_symbol.upper())

                if assets[canonical].symbol == "":
                    assets[canonical].symbol = canonical

                assets[canonical].total_mentions += 1

                # Weight by severity score
                weight = detection.severity_score / 100.0

                if direction == "bullish":
                    assets[canonical].bullish_count += 1
                    assets[canonical].weighted_score += weight
                elif direction == "bearish":
                    assets[canonical].bearish_count += 1
                    assets[canonical].weighted_score -= weight
                else:
                    assets[canonical].neutral_count += 1

                # Add top news for this asset
                if detection.event and len(assets[canonical].top_news) < 3:
                    title = detection.event.title or "Untitled"
                    if title not in assets[canonical].top_news:
                        assets[canonical].top_news.append(title[:60])

        # Sort top stories by severity
        top_stories.sort(key=lambda x: x[1], reverse=True)
        top_stories = top_stories[:5]

        # Fetch price changes for tracked assets
        self._fetch_price_changes(assets)

        # Filter to only assets with mentions
        assets = {k: v for k, v in assets.items() if v.total_mentions > 0}

        return DailySummary(
            date=datetime.utcnow(),
            assets=assets,
            total_alerts=len(detections),
            top_stories=top_stories,
        )

    def _extract_assets_with_direction(self, detection) -> List[Tuple[str, str]]:
        """Extract assets with their direction from a detection."""
        results = []

        # First try assets_affected list
        if detection.assets_affected:
            for asset in detection.assets_affected:
                # Default to neutral if we don't know direction
                direction = "neutral"

                # Try to infer direction from llm_reasoning
                if detection.llm_reasoning:
                    direction = self._detect_asset_direction(
                        detection.llm_reasoning,
                        asset
                    )

                results.append((asset, direction))

        return results

    def _detect_asset_direction(self, text: str, asset: str) -> str:
        """
        Detect bullish/bearish direction for an asset from analysis text.
        Uses flexible pattern matching to find sentiment near asset mentions.
        """
        import re

        text_lower = text.lower()
        asset_lower = asset.lower()

        # Bullish keywords and phrases
        bullish_keywords = [
            'bullish', 'positive', 'strengthen', 'rise', 'rally', 'gain',
            'boost', 'support', 'higher', 'upward', 'increase', 'advance',
            'benefit', 'favorable', 'strong', 'robust', 'optimistic',
            'appreciation', 'soar', 'surge', 'climb', 'lift', 'upside',
        ]

        # Bearish keywords and phrases
        bearish_keywords = [
            'bearish', 'negative', 'weaken', 'fall', 'drop', 'decline',
            'pressure', 'lower', 'downward', 'decrease', 'sell-off',
            'unfavorable', 'weak', 'pessimistic', 'depreciation', 'slide',
            'sink', 'plunge', 'drag', 'downside', 'risk', 'hurt',
        ]

        # Find all positions where the asset is mentioned
        asset_positions = [m.start() for m in re.finditer(re.escape(asset_lower), text_lower)]

        if not asset_positions:
            # Asset not directly mentioned, check for general sentiment
            return "neutral"

        bullish_score = 0
        bearish_score = 0

        # Check context around each asset mention (within ~100 chars)
        for pos in asset_positions:
            # Get surrounding context
            start = max(0, pos - 100)
            end = min(len(text_lower), pos + len(asset_lower) + 100)
            context = text_lower[start:end]

            # Count sentiment keywords in context
            for keyword in bullish_keywords:
                if keyword in context:
                    bullish_score += 1

            for keyword in bearish_keywords:
                if keyword in context:
                    bearish_score += 1

        # Also check for common patterns anywhere in text
        bullish_patterns = [
            f"bullish for {asset_lower}",
            f"bullish on {asset_lower}",
            f"positive for {asset_lower}",
            f"{asset_lower} strength",
            f"{asset_lower} could rise",
            f"{asset_lower} may rise",
            f"{asset_lower} likely to rise",
            f"support {asset_lower}",
            f"supports {asset_lower}",
            f"boost {asset_lower}",
            f"boosts {asset_lower}",
            f"lift {asset_lower}",
            f"lifts {asset_lower}",
            f"{asset_lower} rally",
            f"{asset_lower} gains",
            f"{asset_lower} higher",
        ]

        bearish_patterns = [
            f"bearish for {asset_lower}",
            f"bearish on {asset_lower}",
            f"negative for {asset_lower}",
            f"{asset_lower} weakness",
            f"{asset_lower} could fall",
            f"{asset_lower} may fall",
            f"{asset_lower} likely to fall",
            f"pressure on {asset_lower}",
            f"weigh on {asset_lower}",
            f"weighs on {asset_lower}",
            f"drag on {asset_lower}",
            f"drags on {asset_lower}",
            f"{asset_lower} decline",
            f"{asset_lower} drops",
            f"{asset_lower} lower",
        ]

        for pattern in bullish_patterns:
            if pattern in text_lower:
                bullish_score += 2  # Direct patterns get more weight

        for pattern in bearish_patterns:
            if pattern in text_lower:
                bearish_score += 2

        # Determine direction based on scores
        if bullish_score > bearish_score and bullish_score >= 2:
            return "bullish"
        elif bearish_score > bullish_score and bearish_score >= 2:
            return "bearish"
        else:
            return "neutral"

    def _fetch_price_changes(self, assets: Dict[str, AssetSentiment]) -> None:
        """Fetch 24h price changes for assets."""
        # Map our asset names to Yahoo Finance symbols
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
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        # Sort assets by total mentions
        sorted_assets = sorted(
            summary.assets.values(),
            key=lambda x: x.total_mentions,
            reverse=True
        )

        for asset in sorted_assets[:8]:  # Top 8 assets
            emoji = asset.sentiment_emoji
            sentiment = asset.sentiment

            # Price change line
            price_line = ""
            if asset.price_change_pct is not None:
                price_emoji = "🟢" if asset.price_change_pct >= 0 else "🔴"
                price_sign = "+" if asset.price_change_pct >= 0 else ""
                price_line = f"\n{price_emoji} Daily: {price_sign}{asset.price_change_pct:.2f}%"
                if asset.current_price:
                    price_line += f" (${asset.current_price:,.2f})"

            lines.append(f"<b>{asset.symbol}</b>")
            lines.append(f"{emoji} Sentiment: {sentiment} ({asset.total_mentions} news)")
            lines.append(f"• {asset.bullish_count} bullish | {asset.bearish_count} bearish | {asset.neutral_count} neutral")

            if price_line:
                lines.append(price_line.strip())

            if asset.top_news:
                lines.append(f"📰 {asset.top_news[0][:50]}...")

            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

        # Top stories
        if summary.top_stories:
            lines.append("<b>📝 TOP STORIES TODAY:</b>")
            for i, (title, score, watch_item) in enumerate(summary.top_stories[:5], 1):
                lines.append(f"{i}. {title[:50]}{'...' if len(title) > 50 else ''} ({score}%)")
            lines.append("")

        lines.append(f"📊 Total alerts today: {summary.total_alerts}")
        lines.append(f"🕐 Generated: {tehran_time.strftime('%H:%M')} Tehran")

        return "\n".join(lines)
