"""
Web page collector using httpx and BeautifulSoup.
Fetches HTML pages and extracts main text content.
"""

import hashlib
import re
from datetime import datetime
from typing import Optional, Tuple
from dataclasses import dataclass

import httpx
import structlog
from bs4 import BeautifulSoup

from radar.config import get_settings
from radar.collectors.rss import CollectedItem

logger = structlog.get_logger()


class WebCollector:
    """
    Collects content from web pages.
    Extracts main article text using heuristics or CSS selectors.
    """

    def __init__(self):
        self.settings = get_settings()
        self.timeout = httpx.Timeout(30.0)
        self.headers = {
            "User-Agent": "MarketRadarBot/1.0 (https://github.com/market-radar-bot)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

    def collect(
        self,
        url: str,
        content_selector: Optional[str] = None,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> Tuple[Optional[CollectedItem], Optional[str], Optional[str]]:
        """
        Collect content from a web page.

        Args:
            url: The page URL
            content_selector: Optional CSS selector for main content
            etag: Previous ETag for conditional GET
            last_modified: Previous Last-Modified for conditional GET

        Returns:
            Tuple of (item or None, new_etag, new_last_modified)
        """
        logger.info("collecting_web", url=url)

        try:
            # Build headers for conditional GET
            headers = self.headers.copy()
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified

            # Fetch the page
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(url, headers=headers)

            # Check for not modified
            if response.status_code == 304:
                logger.debug("web_not_modified", url=url)
                return None, etag, last_modified

            response.raise_for_status()

            # Get new etag/last-modified
            new_etag = response.headers.get("ETag", etag)
            new_modified = response.headers.get("Last-Modified", last_modified)

            # Parse the page
            html = response.text
            item = self._parse_page(url, html, content_selector)

            if item:
                logger.info("web_collected", url=url, title=item.title[:50])
            else:
                logger.warning("web_no_content", url=url)

            return item, new_etag, new_modified

        except httpx.HTTPStatusError as e:
            logger.error("web_http_error", url=url, status=e.response.status_code)
            raise
        except Exception as e:
            logger.error("web_collect_error", url=url, error=str(e))
            raise

    def _parse_page(
        self,
        url: str,
        html: str,
        content_selector: Optional[str] = None,
    ) -> Optional[CollectedItem]:
        """Parse an HTML page and extract content."""
        try:
            soup = BeautifulSoup(html, "lxml")

            # Extract title
            title = self._extract_title(soup)
            if not title:
                return None

            # Extract main content
            if content_selector:
                raw_text = self._extract_with_selector(soup, content_selector)
            else:
                raw_text = self._extract_main_content(soup)

            # Truncate raw text
            if raw_text:
                raw_text = raw_text[:self.settings.max_text_length]

            # Create excerpt
            excerpt = raw_text[:500] if raw_text and len(raw_text) > 500 else raw_text

            # Extract metadata
            author = self._extract_author(soup)
            language = self._extract_language(soup)
            published_at = self._extract_publish_date(soup)

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

        except Exception as e:
            logger.error("web_parse_error", url=url, error=str(e))
            return None

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract the page title."""
        # Try og:title first
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return self._clean_text(og_title["content"])

        # Try Twitter title
        twitter_title = soup.find("meta", attrs={"name": "twitter:title"})
        if twitter_title and twitter_title.get("content"):
            return self._clean_text(twitter_title["content"])

        # Try h1
        h1 = soup.find("h1")
        if h1:
            return self._clean_text(h1.get_text())

        # Fall back to <title>
        title_tag = soup.find("title")
        if title_tag:
            return self._clean_text(title_tag.get_text())

        return None

    def _extract_main_content(self, soup: BeautifulSoup) -> str:
        """Extract main article content using heuristics."""
        # Remove unwanted elements
        for element in soup(["script", "style", "nav", "header", "footer",
                           "aside", "form", "iframe", "noscript"]):
            element.decompose()

        # Try common article containers
        content_selectors = [
            "article",
            "[role='main']",
            ".post-content",
            ".article-content",
            ".entry-content",
            ".content-body",
            "#article-body",
            ".story-body",
            "main",
        ]

        for selector in content_selectors:
            element = soup.select_one(selector)
            if element:
                text = element.get_text(separator=" ", strip=True)
                if len(text) > 100:  # Reasonable minimum
                    return self._clean_whitespace(text)

        # Fall back to body
        body = soup.find("body")
        if body:
            text = body.get_text(separator=" ", strip=True)
            return self._clean_whitespace(text)

        return ""

    def _extract_with_selector(self, soup: BeautifulSoup, selector: str) -> str:
        """Extract content using a specific CSS selector."""
        element = soup.select_one(selector)
        if element:
            return self._clean_whitespace(element.get_text(separator=" ", strip=True))
        return self._extract_main_content(soup)

    def _extract_author(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract author from metadata."""
        # Try meta author
        author_meta = soup.find("meta", attrs={"name": "author"})
        if author_meta and author_meta.get("content"):
            return author_meta["content"]

        # Try schema.org author
        author_span = soup.find(attrs={"itemprop": "author"})
        if author_span:
            return self._clean_text(author_span.get_text())

        # Try common author classes
        for cls in ["author", "byline", "writer"]:
            element = soup.find(class_=re.compile(cls, re.I))
            if element:
                return self._clean_text(element.get_text())

        return None

    def _extract_language(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract language from HTML."""
        html_tag = soup.find("html")
        if html_tag:
            return html_tag.get("lang", None)
        return None

    def _extract_publish_date(self, soup: BeautifulSoup) -> Optional[datetime]:
        """Extract publish date from metadata."""
        # Try common meta tags
        date_metas = [
            ("meta", {"property": "article:published_time"}),
            ("meta", {"name": "publishdate"}),
            ("meta", {"name": "date"}),
            ("time", {"itemprop": "datePublished"}),
        ]

        for tag, attrs in date_metas:
            element = soup.find(tag, attrs)
            if element:
                date_str = element.get("content") or element.get("datetime")
                if date_str:
                    parsed = self._parse_date(date_str)
                    if parsed:
                        return parsed

        return None

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse a date string into datetime."""
        import dateutil.parser

        try:
            return dateutil.parser.parse(date_str)
        except Exception:
            return None

    def _clean_text(self, text: str) -> str:
        """Clean text of extra whitespace."""
        if not text:
            return ""
        return re.sub(r"\s+", " ", text).strip()

    def _clean_whitespace(self, text: str) -> str:
        """Clean excessive whitespace from text."""
        if not text:
            return ""
        # Collapse multiple newlines
        text = re.sub(r"\n\s*\n", "\n\n", text)
        # Collapse multiple spaces
        text = re.sub(r"[ \t]+", " ", text)
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
        normalized = title.lower()
        normalized = re.sub(r"[^\w\s]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return hashlib.sha256(normalized.encode()).hexdigest()
