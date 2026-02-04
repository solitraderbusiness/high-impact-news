"""
Telegram channel collector.
Scrapes posts from public Telegram channels via the web preview.
"""

import hashlib
import re
from datetime import datetime
from typing import Optional, List, Tuple
from dataclasses import dataclass

import httpx
import structlog
from bs4 import BeautifulSoup

from radar.config import get_settings
from radar.collectors.rss import CollectedItem

logger = structlog.get_logger()


class TelegramChannelCollector:
    """
    Collects posts from public Telegram channels.
    Uses the web preview at t.me/s/channelname
    """

    def __init__(self):
        self.settings = get_settings()
        self.timeout = httpx.Timeout(30.0)

    def collect(
        self,
        channel_url: str,
        last_published_at: Optional[datetime] = None,
    ) -> Tuple[List[CollectedItem], Optional[str], Optional[str]]:
        """
        Collect posts from a Telegram channel.

        Args:
            channel_url: The channel URL (e.g., https://t.me/channelname or @channelname)
            last_published_at: Only return items newer than this

        Returns:
            Tuple of (items, None, None) - no etag support for Telegram
        """
        # Extract channel username from URL
        channel_name = self._extract_channel_name(channel_url)
        if not channel_name:
            logger.error("telegram_invalid_url", url=channel_url)
            return [], None, None

        # Build the web preview URL
        preview_url = f"https://t.me/s/{channel_name}"
        logger.info("collecting_telegram", channel=channel_name, url=preview_url)

        try:
            # Fetch the page
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    preview_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    follow_redirects=True,
                )
                response.raise_for_status()

            # Parse the HTML
            soup = BeautifulSoup(response.text, "lxml")

            # Find all message bubbles
            messages = soup.find_all("div", class_="tgme_widget_message_wrap")
            if not messages:
                # Try alternative selector
                messages = soup.find_all("div", class_="tgme_widget_message")

            items: List[CollectedItem] = []
            for msg in messages:
                try:
                    item = self._parse_message(msg, channel_name, last_published_at)
                    if item:
                        items.append(item)
                except Exception as e:
                    logger.error("telegram_message_parse_error", error=str(e))

            logger.info("telegram_collected", channel=channel_name, items_count=len(items))
            return items, None, None

        except httpx.HTTPStatusError as e:
            logger.error("telegram_http_error", channel=channel_name, status=e.response.status_code)
            return [], None, None
        except Exception as e:
            logger.error("telegram_collect_error", channel=channel_name, error=str(e))
            return [], None, None

    def _extract_channel_name(self, url: str) -> Optional[str]:
        """Extract the channel username from various URL formats."""
        url = url.strip()

        # Handle @username format
        if url.startswith("@"):
            return url[1:]

        # Handle t.me/username or t.me/s/username formats
        match = re.search(r"t\.me/(?:s/)?([a-zA-Z0-9_]+)", url)
        if match:
            return match.group(1)

        # Handle just the username
        if re.match(r"^[a-zA-Z0-9_]+$", url):
            return url

        return None

    def _parse_message(
        self,
        msg_elem,
        channel_name: str,
        last_published_at: Optional[datetime] = None,
    ) -> Optional[CollectedItem]:
        """Parse a single Telegram message into a CollectedItem."""
        # Get the message link
        link_elem = msg_elem.find("a", class_="tgme_widget_message_date")
        if not link_elem:
            return None

        url = link_elem.get("href", "")
        if not url:
            return None

        # Extract message ID from URL
        msg_id_match = re.search(r"/(\d+)$", url)
        msg_id = msg_id_match.group(1) if msg_id_match else ""

        # Get the message text
        text_elem = msg_elem.find("div", class_="tgme_widget_message_text")
        if not text_elem:
            # Message might be media-only
            return None

        raw_text = text_elem.get_text(separator=" ", strip=True)
        if not raw_text:
            return None

        # Truncate raw text
        raw_text = raw_text[:self.settings.max_text_length]

        # Create a title from the first line or first 100 chars
        title = self._create_title(raw_text)
        excerpt = raw_text[:500] if len(raw_text) > 500 else raw_text

        # Get the timestamp
        time_elem = msg_elem.find("time")
        published_at = None
        if time_elem and time_elem.get("datetime"):
            try:
                published_at = datetime.fromisoformat(
                    time_elem["datetime"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        # Skip old items if we have a cutoff
        if last_published_at and published_at and published_at <= last_published_at:
            return None

        # Get author (channel name)
        author_elem = msg_elem.find("a", class_="tgme_widget_message_owner_name")
        author = author_elem.get_text(strip=True) if author_elem else channel_name

        # Generate hashes
        content_hash = self._generate_content_hash(url, title, published_at)
        title_hash = self._generate_title_hash(title)

        return CollectedItem(
            url=url,
            title=title,
            excerpt=excerpt,
            raw_text=raw_text,
            author=author,
            language=None,  # Could detect later
            published_at=published_at,
            content_hash=content_hash,
            title_hash=title_hash,
        )

    def _create_title(self, text: str) -> str:
        """Create a title from message text."""
        # Take first line or first 100 characters
        first_line = text.split("\n")[0].strip()
        if len(first_line) <= 100:
            return first_line
        return first_line[:97] + "..."

    def _generate_content_hash(
        self,
        url: str,
        title: str,
        published_at: Optional[datetime],
    ) -> str:
        """Generate a deterministic hash for deduplication."""
        components = [
            url.lower().strip(),
            title.lower().strip(),
            published_at.isoformat() if published_at else "",
        ]
        content = "|".join(components)
        return hashlib.sha256(content.encode()).hexdigest()

    def _generate_title_hash(self, title: str) -> str:
        """Generate a normalized title hash for near-duplicate detection."""
        normalized = title.lower()
        normalized = re.sub(r"[^\w\s]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return hashlib.sha256(normalized.encode()).hexdigest()
