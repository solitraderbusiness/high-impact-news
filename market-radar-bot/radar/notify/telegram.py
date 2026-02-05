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
        """Format alert data into a Telegram message using HTML with Persian translation."""
        # Severity emoji and header based on score
        if data.severity_score >= 90:
            severity_emoji = "🚨"
            header = "🔴 HIGH IMPACT"
            bar = "█████████████████"
        elif data.severity_score >= 70:
            severity_emoji = "⚠️"
            header = "🟠 IMPORTANT"
            bar = "█████████████░░░░"
        elif data.severity_score >= 50:
            severity_emoji = "📢"
            header = "🟡 NOTABLE"
            bar = "█████████░░░░░░░░"
        else:
            severity_emoji = "📌"
            header = "🔵 INFO"
            bar = "█████░░░░░░░░░░░░"

        # Format assets with direction arrows
        assets_text = self._format_assets_with_direction(data)

        # Try to translate title and market impact to Persian
        translator = PersianTranslator()

        # Include market impact in translation if available
        texts_to_translate = []
        if data.market_impact:
            texts_to_translate.append(data.market_impact)

        translated = translator.translate(data.title, texts_to_translate)

        # Use translated content if available
        if translated:
            title_text = translated["title"]
            persian_texts = translated.get("quotes", [])
            persian_impact = persian_texts[0] if persian_texts else None
        else:
            title_text = data.title[:200]
            persian_impact = None

        # Format market impact section (the main info section)
        info_text = ""
        if data.market_impact:
            if persian_impact:
                escaped_fa_impact = self._escape_html(persian_impact)
                info_text = f"\n{escaped_fa_impact}"
            escaped_impact = self._escape_html(data.market_impact)
            info_text += f"\n\n<i>{escaped_impact}</i>"
        else:
            info_text = "\n<i>No detailed analysis available</i>"

        # Format timestamp
        timestamp = self._format_timestamp(data.published_at or datetime.utcnow())

        # Build message with cleaner visual structure
        message = f"""{header}
{bar} {data.severity_score}%

{severity_emoji} <b>{self._escape_html(title_text)}</b>

<i>{self._escape_html(data.title[:200])}</i>

📊 <b>{self._escape_html(data.watch_item_name)}</b> • {self._escape_html(data.watch_item_category)}
💯 Match: {int(data.match_confidence * 100)}%

💹 <b>Affected Assets:</b>
{assets_text}

💡 <b>Analysis:</b>{info_text}

🔗 <a href="{data.source_url}">Read Full Article</a>
🕐 {timestamp}"""

        return message

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
