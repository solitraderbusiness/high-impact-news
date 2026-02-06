"""
Market Sentiment Analyzer.
Aggregates news sentiment over time periods and generates sentiment reports.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import json

import pytz
import structlog
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from radar.config import get_settings
from radar.models import Detection, Event

logger = structlog.get_logger()


# Target assets for sentiment tracking
SENTIMENT_ASSETS = ["BTC", "GOLD", "OIL", "DXY"]

# Asset aliases - map various names to standard symbols
ASSET_ALIASES = {
    # Bitcoin
    "BTC": "BTC", "BITCOIN": "BTC", "BTC-USD": "BTC", "BTCUSD": "BTC",
    # Gold
    "GOLD": "GOLD", "XAU": "GOLD", "XAUUSD": "GOLD", "XAU/USD": "GOLD", "GC": "GOLD",
    # Oil
    "OIL": "OIL", "WTI": "OIL", "BRENT": "OIL", "CL": "OIL", "CRUDE": "OIL", "USO": "OIL",
    # Dollar
    "DXY": "DXY", "USD": "DXY", "DOLLAR": "DXY", "US DOLLAR": "DXY",
    # Also track these but map to DXY inverse
    "EURUSD": "DXY_INVERSE", "EUR/USD": "DXY_INVERSE", "EUR": "DXY_INVERSE",
}


@dataclass
class AssetSentiment:
    """Sentiment for a single asset."""
    symbol: str
    bullish_score: float = 0.0  # Sum of bullish signals weighted by severity
    bearish_score: float = 0.0  # Sum of bearish signals weighted by severity
    news_count: int = 0
    key_drivers: List[str] = field(default_factory=list)  # Top reasons for sentiment

    @property
    def net_score(self) -> float:
        """Net sentiment score (-100 to +100)."""
        total = self.bullish_score + self.bearish_score
        if total == 0:
            return 0
        return ((self.bullish_score - self.bearish_score) / total) * 100

    @property
    def sentiment(self) -> str:
        """BULLISH, BEARISH, or NEUTRAL."""
        score = self.net_score
        if score > 20:
            return "BULLISH"
        elif score < -20:
            return "BEARISH"
        return "NEUTRAL"

    @property
    def strength(self) -> str:
        """Sentiment strength: STRONG, MODERATE, or WEAK."""
        score = abs(self.net_score)
        if score > 60:
            return "STRONG"
        elif score > 30:
            return "MODERATE"
        return "WEAK"

    @property
    def confidence(self) -> str:
        """Confidence based on news count."""
        if self.news_count >= 5:
            return "HIGH"
        elif self.news_count >= 2:
            return "MEDIUM"
        return "LOW"


@dataclass
class SentimentReport:
    """Complete sentiment report for a time period."""
    period_hours: int
    start_time: datetime
    end_time: datetime
    assets: Dict[str, AssetSentiment]
    total_news_count: int = 0
    top_headlines: List[str] = field(default_factory=list)


class SentimentAnalyzer:
    """
    Analyzes market sentiment based on aggregated news.
    """

    def __init__(self):
        self.settings = get_settings()

    def analyze(self, db: Session, hours: int = 4) -> Optional[SentimentReport]:
        """
        Analyze sentiment for the past N hours.

        Args:
            db: Database session
            hours: Number of hours to look back

        Returns:
            SentimentReport or None if no data
        """
        now = datetime.utcnow()
        start_time = now - timedelta(hours=hours)

        # Get all detections from the time period
        query = (
            select(Detection)
            .join(Event, Detection.event_id == Event.id)
            .where(
                and_(
                    Detection.created_at >= start_time,
                    Detection.severity_score >= 30,  # Only consider notable news
                )
            )
            .order_by(Detection.severity_score.desc())
        )

        detections = list(db.execute(query).scalars().all())

        if not detections:
            logger.info("sentiment_no_detections", hours=hours)
            return None

        # Initialize asset sentiments
        assets = {symbol: AssetSentiment(symbol=symbol) for symbol in SENTIMENT_ASSETS}

        # Track headlines for report
        headlines = []

        # Process each detection
        for detection in detections:
            event = detection.event
            if event:
                headlines.append(event.title[:100])

            # Get weight based on severity (higher severity = more weight)
            weight = detection.severity_score / 100.0

            # Parse assets_affected JSON
            detection_assets = detection.assets_affected or []
            if isinstance(detection_assets, str):
                try:
                    detection_assets = json.loads(detection_assets)
                except:
                    detection_assets = []

            # Get trade bias from LLM reasoning or infer from assets
            trade_bias = self._extract_trade_bias(detection)

            # Process each affected asset
            for asset_info in detection_assets:
                # Handle both string and dict formats
                if isinstance(asset_info, dict):
                    asset_symbol = asset_info.get("symbol", "").upper()
                    direction = asset_info.get("direction", "neutral")
                else:
                    asset_symbol = str(asset_info).upper()
                    direction = trade_bias.lower() if trade_bias else "neutral"

                # Map to standard symbol
                standard_symbol = ASSET_ALIASES.get(asset_symbol)
                if not standard_symbol:
                    continue

                # Handle DXY inverse (EURUSD bullish = DXY bearish)
                if standard_symbol == "DXY_INVERSE":
                    standard_symbol = "DXY"
                    direction = "bearish" if direction == "bullish" else "bullish" if direction == "bearish" else "neutral"

                if standard_symbol not in assets:
                    continue

                asset_sent = assets[standard_symbol]
                asset_sent.news_count += 1

                # Add to sentiment scores
                if direction == "bullish":
                    asset_sent.bullish_score += weight
                    if event and len(asset_sent.key_drivers) < 3:
                        asset_sent.key_drivers.append(f"📈 {event.title[:50]}")
                elif direction == "bearish":
                    asset_sent.bearish_score += weight
                    if event and len(asset_sent.key_drivers) < 3:
                        asset_sent.key_drivers.append(f"📉 {event.title[:50]}")

        # Create report
        report = SentimentReport(
            period_hours=hours,
            start_time=start_time,
            end_time=now,
            assets=assets,
            total_news_count=len(detections),
            top_headlines=headlines[:5],
        )

        return report

    def _extract_trade_bias(self, detection: Detection) -> Optional[str]:
        """Extract trade bias from detection's LLM reasoning."""
        reasoning = detection.llm_reasoning or ""

        # Look for explicit bias indicators
        reasoning_lower = reasoning.lower()
        if "bullish" in reasoning_lower:
            return "BULLISH"
        elif "bearish" in reasoning_lower:
            return "BEARISH"

        return None

    def format_telegram_message(self, report: SentimentReport) -> str:
        """Format sentiment report for Telegram."""
        # Determine period label
        if report.period_hours == 1:
            period_label = "1H"
        elif report.period_hours == 4:
            period_label = "4H"
        elif report.period_hours == 24:
            period_label = "DAILY"
        else:
            period_label = f"{report.period_hours}H"

        # Format time in Tehran
        tehran_tz = pytz.timezone(self.settings.timezone)
        end_time_tehran = report.end_time.replace(tzinfo=pytz.UTC).astimezone(tehran_tz)
        time_str = end_time_tehran.strftime("%H:%M")
        date_str = end_time_tehran.strftime("%b %d")

        # Build message
        lines = [
            f"📊 <b>MARKET SENTIMENT</b> | {period_label}",
            f"🕐 {date_str}, {time_str} Tehran",
            "",
        ]

        # Add each asset sentiment
        for symbol in SENTIMENT_ASSETS:
            asset = report.assets.get(symbol)
            if not asset:
                continue

            # Sentiment bar visualization
            score = asset.net_score
            bar = self._make_sentiment_bar(score)

            # Emoji based on sentiment
            if asset.sentiment == "BULLISH":
                emoji = "🟢"
            elif asset.sentiment == "BEARISH":
                emoji = "🔴"
            else:
                emoji = "⚪"

            # Format percentage
            pct = abs(int(score))
            sent_text = asset.sentiment

            # Confidence indicator
            conf = f"({asset.news_count} news)" if asset.news_count > 0 else "(no data)"

            lines.append(f"<b>{symbol}</b>  {bar}  {emoji} {pct}% {sent_text}")
            lines.append(f"      {conf}")

            # Add key driver if available
            if asset.key_drivers:
                driver = asset.key_drivers[0][:40]
                lines.append(f"      <i>{driver}...</i>")

            lines.append("")

        # Footer
        lines.append(f"📰 Based on {report.total_news_count} news items")

        return "\n".join(lines)

    def _make_sentiment_bar(self, score: float) -> str:
        """Create a visual sentiment bar."""
        # Score is -100 to +100, map to 0-10 blocks
        normalized = (score + 100) / 200  # 0 to 1
        filled = int(normalized * 10)
        filled = max(0, min(10, filled))

        # Use different characters for bearish vs bullish
        if score < 0:
            return "🔴" * (10 - filled) + "⬜" * filled
        else:
            return "⬜" * (10 - filled) + "🟢" * filled


def get_sentiment_report(db: Session, hours: int = 4) -> Optional[SentimentReport]:
    """Convenience function to get a sentiment report."""
    analyzer = SentimentAnalyzer()
    return analyzer.analyze(db, hours=hours)


def format_sentiment_message(report: SentimentReport) -> str:
    """Convenience function to format a sentiment report."""
    analyzer = SentimentAnalyzer()
    return analyzer.format_telegram_message(report)
