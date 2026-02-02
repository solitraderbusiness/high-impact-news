"""
RSS feed collector using feedparser.
Supports etag/last-modified for efficient polling.
"""

import hashlib
import re
from datetime import datetime
from typing import Optional, List, Tuple
from dataclasses import dataclass

import feedparser
import structlog
from bs4 import BeautifulSoup

from radar.config import get_settings

logger = structlog.get_logger()


@dataclass
class CollectedItem:
    """A normalized item collected from a source."""
    url: str
    title: str
    excerpt: Optional[str]
    raw_text: Optional[str]
    author: Optional[str]
    language: Optional[str]
    published_at: Optional[datetime]
    content_hash: str
    title_hash: str


class RSSCollector:
    """
    Collects items from RSS/Atom feeds.
    """

    def __init__(self):
        self.settings = get_settings()

    def collect(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        last_published_at: Optional[datetime] = None,
    ) -> Tuple[List[CollectedItem], Optional[str], Optional[str]]:
        """
        Collect items from an RSS feed.

        Args:
            url: The feed URL
            etag: Previous ETag for conditional GET
            last_modified: Previous Last-Modified for conditional GET
            last_published_at: Only return items newer than this

        Returns:
            Tuple of (items, new_etag, new_last_modified)
        """
        logger.info("collecting_rss", url=url)

        try:
            # Parse the feed with conditional GET
            feed = feedparser.parse(
                url,
                etag=etag,
                modified=last_modified,
            )

            # Check for HTTP status
            status = feed.get("status", 200)
            if status == 304:
                # Not modified
                logger.debug("rss_not_modified", url=url)
                return [], etag, last_modified

            if status >= 400:
                logger.warning("rss_fetch_error", url=url, status=status)
                return [], etag, last_modified

            # Get new etag/last-modified
            new_etag = feed.get("etag", etag)
            new_modified = feed.get("modified", last_modified)

            # Parse entries
            items: List[CollectedItem] = []
            for entry in feed.entries:
                try:
                    item = self._parse_entry(entry, url, last_published_at)
                    if item:
                        items.append(item)
                except Exception as e:
                    logger.error("rss_entry_parse_error", url=url, error=str(e))

            logger.info("rss_collected", url=url, items_count=len(items))
            return items, new_etag, new_modified

        except Exception as e:
            logger.error("rss_collect_error", url=url, error=str(e))
            raise

    def _parse_entry(
        self,
        entry: dict,
        feed_url: str,
        last_published_at: Optional[datetime] = None,
    ) -> Optional[CollectedItem]:
        """Parse a single feed entry into a CollectedItem."""
        # Get the URL
        url = entry.get("link", "")
        if not url:
            return None

        # Get the title
        title = entry.get("title", "")
        if not title:
            title = "Untitled"
        title = self._clean_text(title)

        # Parse published date
        published_at = None
        if entry.get("published_parsed"):
            try:
                published_at = datetime(*entry.published_parsed[:6])
            except (TypeError, ValueError):
                pass
        elif entry.get("updated_parsed"):
            try:
                published_at = datetime(*entry.updated_parsed[:6])
            except (TypeError, ValueError):
                pass

        # Skip old items if we have a cutoff
        if last_published_at and published_at and published_at <= last_published_at:
            return None

        # Get content/summary
        raw_text = ""
        excerpt = ""

        # Try to get full content first
        if entry.get("content"):
            for content in entry.content:
                if content.get("value"):
                    raw_text = self._extract_text(content.value)
                    break

        # Fall back to summary
        if not raw_text and entry.get("summary"):
            raw_text = self._extract_text(entry.summary)

        # Truncate raw text
        if raw_text:
            raw_text = raw_text[:self.settings.max_text_length]
            excerpt = raw_text[:500] if len(raw_text) > 500 else raw_text

        # Get author
        author = entry.get("author", None)

        # Get language from feed
        language = entry.get("language", None)

        # Generate hashes
        content_hash = self._generate_content_hash(url, title, published_at)
        title_hash = self._generate_title_hash(title)

        return CollectedItem(
            url=url,
            title=title,
            excerpt=excerpt,
            raw_text=raw_text,
            author=author,
            language=language,
            published_at=published_at,
            content_hash=content_hash,
            title_hash=title_hash,
        )

    def _extract_text(self, html: str) -> str:
        """Extract plain text from HTML content."""
        if not html:
            return ""

        try:
            soup = BeautifulSoup(html, "lxml")

            # Remove script and style elements
            for element in soup(["script", "style", "nav", "header", "footer"]):
                element.decompose()

            # Get text
            text = soup.get_text(separator=" ", strip=True)

            # Clean up whitespace
            text = re.sub(r"\s+", " ", text)

            return text.strip()
        except Exception:
            # If HTML parsing fails, try basic cleanup
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text)
            return text.strip()

    def _clean_text(self, text: str) -> str:
        """Clean text of HTML entities and extra whitespace."""
        if not text:
            return ""

        # Decode HTML entities
        try:
            soup = BeautifulSoup(text, "lxml")
            text = soup.get_text()
        except Exception:
            pass

        # Clean whitespace
        text = re.sub(r"\s+", " ", text)
        return text.strip()

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
        # Normalize: lowercase, remove punctuation, collapse whitespace
        normalized = title.lower()
        normalized = re.sub(r"[^\w\s]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return hashlib.sha256(normalized.encode()).hexdigest()
