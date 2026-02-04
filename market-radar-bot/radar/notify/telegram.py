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

logger = structlog.get_logger()


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

    def __init__(self):
        self.settings = get_settings()
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
            "🔔 *Market Radar Bot - Test Message*\n\n"
            "✅ Telegram integration is working correctly!\n\n"
            f"Timestamp: {self._format_timestamp(datetime.utcnow())}"
        )

        return self._send_with_retry(message)

    def _format_message(self, data: AlertData) -> str:
        """Format alert data into a Telegram message."""
        # Severity emoji
        if data.severity_score >= 90:
            severity_emoji = "🚨"
        elif data.severity_score >= 70:
            severity_emoji = "⚠️"
        elif data.severity_score >= 50:
            severity_emoji = "📢"
        else:
            severity_emoji = "📌"

        # Format assets
        assets_text = ", ".join(data.assets_affected) if data.assets_affected else "—"

        # Format citations (trigger spans)
        citations_text = ""
        for i, span in enumerate(data.trigger_spans[:2], 1):
            # Escape markdown characters
            escaped = self._escape_markdown(span)
            citations_text += f"\n_{escaped}_"

        # Format timestamp
        timestamp = self._format_timestamp(data.published_at or datetime.utcnow())

        # Build message
        message = f"""{severity_emoji} *{self._escape_markdown(data.title[:200])}*

📊 *Watch Item:* {self._escape_markdown(data.watch_item_name)}
📁 *Category:* {data.watch_item_category}
🎯 *Severity:* {data.severity_score}/100
💯 *Confidence:* {int(data.match_confidence * 100)}%
💰 *Assets:* {assets_text}

📝 *Key Quote(s):*{citations_text}

🔗 [Source]({data.source_url})
🕐 {timestamp}"""

        return message

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

    def _escape_markdown(self, text: str) -> str:
        """Escape Telegram MarkdownV2 special characters."""
        # Characters that need escaping in MarkdownV2
        # Using simpler Markdown mode, so escape only: _ * ` [
        escape_chars = ['_', '*', '`', '[', ']', '(', ')']
        for char in escape_chars:
            text = text.replace(char, '\\' + char)
        return text

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
            "parse_mode": "Markdown",
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
