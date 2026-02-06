"""
Telegram notification system.
Sends formatted alerts via Telegram Bot API.
"""

import time
from datetime import datetime
from typing import Optional, List
from dataclasses import dataclass

import httpx
import pytz
import structlog

from radar.config import get_settings
from radar.notify.translator import PersianTranslator

logger = structlog.get_logger()


@dataclass
class AssetWithDirection:
    """An asset with its expected price direction."""
    symbol: str
    direction: str  # "bullish", "bearish", or "neutral"


@dataclass
class AlertData:
    """Data for a Telegram alert."""
    title: str
    watch_item_name: str
    watch_item_category: str
    severity_score: int
    match_confidence: float
    assets_affected: List[str]
    trigger_spans: List[str]  # Citation quotes
    source_url: str
    published_at: Optional[datetime]
    market_impact: Optional[str] = None  # Detailed market impact analysis
    assets_with_direction: Optional[List[AssetWithDirection]] = None  # Assets with bullish/bearish direction
    # New trading-focused fields
    trade_bias: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL
    primary_asset: Optional[str] = None
    setup: Optional[str] = None  # What happened and why it matters
    key_levels: Optional[str] = None  # Price levels to watch
    timeframe: str = "SWING"  # INTRADAY, SWING, POSITION
    catalyst: Optional[str] = None  # What to watch for confirmation
    risk: Optional[str] = None  # What could invalidate the trade


@dataclass
class SendResult:
    """Result of sending a Telegram message."""
    success: bool
    message_id: Optional[int] = None
    error: Optional[str] = None
    retry_count: int = 0


class TelegramNotifier:
    """
    Sends alerts via Telegram Bot API with retry logic.
    """

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.timeout = httpx.Timeout(30.0)
        self.max_retries = 3
        self.retry_delays = [2, 4, 8]  # Exponential backoff

    @property
    def is_available(self) -> bool:
        """Check if Telegram is configured."""
        return self.settings.has_telegram

    def send_alert(self, data: AlertData) -> SendResult:
        """
        Send an alert to Telegram.

        Args:
            data: Alert data to send

        Returns:
            SendResult with success status and message ID
        """
        if not self.is_available:
            return SendResult(
                success=False,
                error="Telegram not configured",
            )

        # Format the message
        message = self._format_message(data)

        # Send with retry
        return self._send_with_retry(message)

    def send_test_message(self) -> SendResult:
        """Send a test message to verify configuration."""
        if not self.is_available:
            return SendResult(
                success=False,
                error="Telegram not configured",
            )

        message = (
            "🔔 <b>Market Radar Bot - Test Message</b>\n\n"
            "✅ Telegram integration is working correctly!\n\n"
            f"Timestamp: {self._format_timestamp(datetime.utcnow())}"
        )

        return self._send_with_retry(message)

    def send_raw_message(self, message: str) -> SendResult:
        """Send a pre-formatted message (for summaries, etc.)."""
        if not self.is_available:
            return SendResult(
                success=False,
                error="Telegram not configured",
            )

        return self._send_with_retry(message)

    def _format_message(self, data: AlertData) -> str:
        """Format alert data into a compact, actionable Telegram message."""
        # Severity header
        if data.severity_score >= 90:
            header = "🔴 HIGH IMPACT"
        elif data.severity_score >= 70:
            header = "🟠 IMPORTANT"
        elif data.severity_score >= 50:
            header = "🟡 NOTABLE"
        else:
            header = "🔵 INFO"

        # Trade bias emoji
        bias = data.trade_bias.upper() if data.trade_bias else "NEUTRAL"
        if bias == "BULLISH":
            bias_emoji = "📈"
            bias_text = "BULLISH"
        elif bias == "BEARISH":
            bias_emoji = "📉"
            bias_text = "BEARISH"
        else:
            bias_emoji = "➖"
            bias_text = "NEUTRAL"

        # Format assets with direction (compact)
        assets_lines = []
        if data.assets_with_direction:
            bullish = [a.symbol for a in data.assets_with_direction if a.direction == "bullish"]
            bearish = [a.symbol for a in data.assets_with_direction if a.direction == "bearish"]
            if bullish:
                assets_lines.append(f"📈 {', '.join(bullish[:4])}")
            if bearish:
                assets_lines.append(f"📉 {', '.join(bearish[:4])}")
        elif data.assets_affected:
            assets_lines.append(f"• {', '.join(data.assets_affected[:4])}")

        assets_text = "\n".join(assets_lines) if assets_lines else "• N/A"

        # Translate title to Persian
        translator = PersianTranslator()
        translated = translator.translate(data.title, [])

        if translated:
            title_fa = self._escape_html(translated["title"])
        else:
            title_fa = self._escape_html(data.title[:150])

        # Format timestamp (Tehran only)
        timestamp = self._format_timestamp_short(data.published_at or datetime.utcnow())

        # Extract source domain
        source_domain = self._extract_domain(data.source_url)

        # Build compact message
        message = f"""{header} | {data.severity_score}%
{bias_emoji} <b>{bias_text}</b> {self._escape_html(data.primary_asset or '')}

<b>{title_fa}</b>

{assets_text}

📊 {self._escape_html(data.watch_item_name)} | {self._escape_html(data.watch_item_category)}"""

        # Add setup (what happened)
        if data.setup:
            message += f"\n\n⚡ <b>SETUP:</b>\n{self._escape_html(data.setup)}"

        # Add key levels if available
        if data.key_levels and data.key_levels.lower() not in ['n/a', 'none', '']:
            message += f"\n\n📍 <b>LEVELS:</b> {self._escape_html(data.key_levels)}"

        # Add timeframe
        if data.timeframe:
            message += f"\n⏱ {self._escape_html(data.timeframe)}"

        # Add catalyst/what to watch
        if data.catalyst:
            message += f"\n\n👁 <b>WATCH:</b> {self._escape_html(data.catalyst)}"

        # Add risk
        if data.risk:
            message += f"\n⚠️ <b>RISK:</b> {self._escape_html(data.risk)}"

        # Footer
        message += f"""

🔗 <a href="{data.source_url}">{source_domain}</a>
🕐 {timestamp}"""

        return message

    def _format_timestamp_short(self, dt: datetime) -> str:
        """Format timestamp with Tehran time only."""
        try:
            local_tz = pytz.timezone(self.settings.timezone)
            local_time = dt.replace(tzinfo=pytz.UTC).astimezone(local_tz)
            return local_time.strftime("%H:%M Tehran")
        except Exception:
            return dt.strftime("%H:%M UTC")

    def _extract_domain(self, url: str) -> str:
        """Extract domain name from URL."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.replace("www.", "")
            # Shorten common domains
            if "bloomberg" in domain:
                return "Bloomberg"
            elif "reuters" in domain:
                return "Reuters"
            elif "cnbc" in domain:
                return "CNBC"
            elif "zerohedge" in domain:
                return "ZeroHedge"
            elif "wsj" in domain:
                return "WSJ"
            elif "ft.com" in domain:
                return "FT"
            elif "t.me" in domain:
                return "Telegram"
            return domain[:20]
        except Exception:
            return "Source"

    def _format_assets_with_direction(self, data: AlertData) -> str:
        """Format assets with direction arrows (↑ bullish, ↓ bearish)."""
        if data.assets_with_direction:
            lines = []
            for asset in data.assets_with_direction[:6]:
                if asset.direction == "bullish":
                    arrow = "📈"
                elif asset.direction == "bearish":
                    arrow = "📉"
                else:
                    arrow = "➖"
                lines.append(f"{arrow} {self._escape_html(asset.symbol)}")
            return "\n".join(lines)
        elif data.assets_affected:
            # Fallback to old format without direction
            assets_list = [f"• {self._escape_html(asset)}" for asset in data.assets_affected[:6]]
            return "\n".join(assets_list)
        else:
            return "• Not specified"

    def _format_timestamp(self, dt: datetime) -> str:
        """Format timestamp with UTC and configured local timezone."""
        utc_time = dt.strftime("%Y-%m-%d %H:%M UTC")

        try:
            local_tz = pytz.timezone(self.settings.timezone)
            local_time = dt.replace(tzinfo=pytz.UTC).astimezone(local_tz)
            # Get a short name for the timezone
            tz_name = self.settings.timezone.split("/")[-1].replace("_", " ")
            local_str = local_time.strftime(f"%H:%M {tz_name}")
            return f"{utc_time} / {local_str}"
        except Exception:
            return utc_time

    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters for Telegram."""
        if not text:
            return ""
        return (
            text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _send_with_retry(self, message: str) -> SendResult:
        """Send message with exponential backoff retry."""
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                result = self._send_message(message)
                if result.success:
                    result.retry_count = attempt
                    return result
                last_error = result.error
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "telegram_send_attempt_failed",
                    attempt=attempt + 1,
                    error=last_error,
                )

            # Wait before retry (except on last attempt)
            if attempt < self.max_retries:
                delay = self.retry_delays[min(attempt, len(self.retry_delays) - 1)]
                time.sleep(delay)

        return SendResult(
            success=False,
            error=f"Failed after {self.max_retries + 1} attempts: {last_error}",
            retry_count=self.max_retries,
        )

    def _send_message(self, message: str) -> SendResult:
        """Send a single message to Telegram."""
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"

        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload)

            data = response.json()

            if data.get("ok"):
                message_id = data.get("result", {}).get("message_id")
                logger.info("telegram_message_sent", message_id=message_id)
                return SendResult(success=True, message_id=message_id)
            else:
                error = data.get("description", "Unknown error")
                logger.error("telegram_api_error", error=error)
                return SendResult(success=False, error=error)

        except httpx.HTTPStatusError as e:
            error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error("telegram_http_error", error=error)
            return SendResult(success=False, error=error)
        except Exception as e:
            logger.error("telegram_error", error=str(e))
            return SendResult(success=False, error=str(e))
