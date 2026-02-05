"""
Deduplication and cooldown management for events and alerts.
"""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Optional, Set, List, Tuple

import structlog
from sqlalchemy.orm import Session

from radar import storage

logger = structlog.get_logger()

# Threshold for Jaccard similarity to consider as near-duplicate
TITLE_SIMILARITY_THRESHOLD = 0.5  # Lowered from 0.7 to catch more duplicates

# Keywords that indicate the same type of event
EVENT_KEYWORDS = {
    'rate_decision': ['rate', 'rates', 'interest', 'holds', 'hold', 'cut', 'hike', 'unchanged', 'steady'],
    'inflation': ['inflation', 'cpi', 'prices', 'price'],
    'gdp': ['gdp', 'growth', 'economy', 'economic'],
    'employment': ['jobs', 'employment', 'unemployment', 'payroll', 'nfp'],
}


class Deduplicator:
    """
    Handles deduplication of events and cooldown management.

    Dedup strategies:
    1. Content hash: exact match on (source_url + title + published_at)
    2. Title hash: normalized title match (catches rephrased versions)
    3. Jaccard similarity: optional fuzzy matching (MVP: disabled by default)

    Cooldown:
    - Per watch item cooldown between alerts
    - Bypass for severity >= 90
    """

    def __init__(
        self,
        cooldown_minutes: int = 15,
        high_severity_threshold: int = 90,
    ):
        """
        Initialize deduplicator.

        Args:
            cooldown_minutes: Default cooldown between alerts for same watch item
            high_severity_threshold: Severity above which cooldown is bypassed
        """
        self.cooldown_minutes = cooldown_minutes
        self.high_severity_threshold = high_severity_threshold

    def is_duplicate_event(
        self,
        db: Session,
        content_hash: str,
        title_hash: Optional[str] = None,
    ) -> bool:
        """
        Check if an event is a duplicate.

        Args:
            db: Database session
            content_hash: SHA256 hash of (url + title + published_at)
            title_hash: Optional normalized title hash

        Returns:
            True if duplicate exists
        """
        # Check exact content hash
        existing = storage.get_event_by_hash(db, content_hash)
        if existing:
            logger.debug("duplicate_by_content_hash", hash=content_hash[:16])
            return True

        # Check for near-duplicates by title hash
        if title_hash:
            recent_title_hashes = storage.get_recent_title_hashes(db, hours=24)
            if title_hash in recent_title_hashes:
                logger.debug("duplicate_by_title_hash", hash=title_hash[:16])
                return True

        return False

    def is_near_duplicate_by_title(
        self,
        db: Session,
        title: str,
        watch_item_id: int,
        hours: int = 6,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if there's a near-duplicate alert for the same watch item recently.
        Uses multiple strategies:
        1. Jaccard similarity on full title
        2. Core keyword matching (e.g., "ECB holds rates" matches "ECB leaves rates unchanged")
        3. Event type detection (same type of event = likely duplicate)

        Args:
            db: Database session
            title: The title to check
            watch_item_id: The watch item ID
            hours: Look back this many hours

        Returns:
            Tuple of (is_duplicate, reason)
        """
        # Get recent successful alerts for this watch item
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        recent_alerts = storage.get_recent_alert_titles(
            db,
            watch_item_id=watch_item_id,
            since=cutoff,
            limit=20
        )

        normalized_title = self.normalize_text(title)
        title_keywords = self.extract_event_keywords(title)

        for recent_title in recent_alerts:
            normalized_recent = self.normalize_text(recent_title)

            # Strategy 1: Jaccard similarity
            similarity = self.jaccard_similarity(normalized_title, normalized_recent)
            if similarity >= TITLE_SIMILARITY_THRESHOLD:
                logger.debug(
                    "near_duplicate_detected",
                    method="jaccard",
                    similarity=similarity,
                    title=title[:50],
                    similar_to=recent_title[:50],
                )
                return True, f"Similar to recent alert ({similarity:.0%} match)"

            # Strategy 2: Core keyword matching
            recent_keywords = self.extract_event_keywords(recent_title)
            keyword_overlap = self.keyword_overlap_score(title_keywords, recent_keywords)
            if keyword_overlap >= 0.6:
                logger.debug(
                    "near_duplicate_detected",
                    method="keywords",
                    overlap=keyword_overlap,
                    title=title[:50],
                    similar_to=recent_title[:50],
                )
                return True, f"Same event type as recent alert ({keyword_overlap:.0%} keyword match)"

        return False, None

    def extract_event_keywords(self, title: str) -> Set[str]:
        """
        Extract key event-related words from a title.
        Focuses on action words and entities that identify the event.
        """
        # Normalize
        title_lower = title.lower()

        # Extract meaningful words (remove common words)
        stop_words = {
            'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'and', 'or',
            'as', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
            'this', 'that', 'these', 'those', 'it', 'its',
            'says', 'said', 'say', 'according', 'report', 'reports',
            'video', 'live', 'update', 'updates', 'breaking', 'news',
            'why', 'how', 'what', 'when', 'where', 'who',
            'here', 'heres', "here's", 'now', 'just', 'also',
        }

        # Keep important financial/event words
        words = re.findall(r'\b[a-z]+\b', title_lower)
        keywords = set()

        for word in words:
            if word not in stop_words and len(word) > 2:
                keywords.add(word)

        return keywords

    def keyword_overlap_score(self, keywords1: Set[str], keywords2: Set[str]) -> float:
        """
        Calculate overlap between two keyword sets.
        Uses a weighted approach favoring important financial terms.
        """
        if not keywords1 or not keywords2:
            return 0.0

        # Important terms get higher weight
        important_terms = {
            'ecb', 'fed', 'boe', 'boj', 'rba', 'snb',  # Central banks
            'rate', 'rates', 'interest', 'hold', 'holds', 'cut', 'cuts', 'hike', 'hikes',
            'inflation', 'gdp', 'employment', 'unemployment',
            'unchanged', 'steady', 'decision',
        }

        # Calculate weighted intersection
        intersection = keywords1 & keywords2
        important_matches = intersection & important_terms

        # Score: (regular matches + 2*important matches) / union size
        regular_matches = len(intersection - important_terms)
        weighted_score = regular_matches + (2 * len(important_matches))
        max_possible = len(keywords1 | keywords2) + len(important_terms & (keywords1 | keywords2))

        if max_possible == 0:
            return 0.0

        return min(1.0, weighted_score / max_possible)

    def is_near_duplicate_title(
        self,
        title_hash: str,
        recent_title_hashes: Set[str],
    ) -> bool:
        """
        Check if title is a near-duplicate using normalized hash.

        Args:
            title_hash: Normalized title hash
            recent_title_hashes: Set of recent title hashes to check against

        Returns:
            True if near-duplicate found
        """
        return title_hash in recent_title_hashes

    def jaccard_similarity(self, text1: str, text2: str) -> float:
        """
        Calculate Jaccard similarity between two texts.
        Uses word-level tokens.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Similarity score between 0.0 and 1.0
        """
        # Tokenize: lowercase, remove punctuation, split into words
        def tokenize(text: str) -> Set[str]:
            text = text.lower()
            text = re.sub(r'[^\w\s]', '', text)
            return set(text.split())

        tokens1 = tokenize(text1)
        tokens2 = tokenize(text2)

        if not tokens1 or not tokens2:
            return 0.0

        intersection = tokens1 & tokens2
        union = tokens1 | tokens2

        return len(intersection) / len(union)

    def should_alert(
        self,
        db: Session,
        watch_item_id: int,
        severity_score: int,
        watch_item_cooldown: Optional[int] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Determine if an alert should be sent based on cooldown.

        Args:
            db: Database session
            watch_item_id: ID of the watch item
            severity_score: Severity score of the detection
            watch_item_cooldown: Custom cooldown for this watch item (minutes)

        Returns:
            Tuple of (should_alert, suppression_reason)
        """
        # High severity bypasses cooldown
        if severity_score >= self.high_severity_threshold:
            logger.debug(
                "alert_bypassing_cooldown",
                watch_item_id=watch_item_id,
                severity=severity_score,
            )
            return True, None

        # Check last alert time
        last_alert = storage.get_last_alert_time(db, watch_item_id)
        if not last_alert:
            return True, None

        # Calculate cooldown period
        cooldown = watch_item_cooldown or self.cooldown_minutes
        cooldown_until = last_alert + timedelta(minutes=cooldown)

        now = datetime.utcnow()
        if now < cooldown_until:
            remaining = (cooldown_until - now).total_seconds() / 60
            reason = f"Cooldown active: {remaining:.1f} minutes remaining"
            logger.debug(
                "alert_in_cooldown",
                watch_item_id=watch_item_id,
                remaining_minutes=remaining,
            )
            return False, reason

        return True, None

    @staticmethod
    def generate_content_hash(
        url: str,
        title: str,
        published_at: Optional[datetime],
    ) -> str:
        """Generate deterministic content hash."""
        components = [
            url.lower().strip(),
            title.lower().strip(),
            published_at.isoformat() if published_at else "",
        ]
        content = "|".join(components)
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def generate_title_hash(title: str) -> str:
        """Generate normalized title hash for near-duplicate detection."""
        # Normalize: lowercase, remove punctuation, collapse whitespace
        normalized = title.lower()
        normalized = re.sub(r'[^\w\s]', '', normalized)
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return hashlib.sha256(normalized.encode()).hexdigest()

    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize text for comparison."""
        text = text.lower()
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
