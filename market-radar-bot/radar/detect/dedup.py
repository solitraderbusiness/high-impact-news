"""
Deduplication and cooldown management for events and alerts.
"""

import hashlib
import re
from datetime import datetime, timedelta
from typing import Optional, Set, List

import structlog
from sqlalchemy.orm import Session

from radar import storage

logger = structlog.get_logger()


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

        # For MVP, we rely primarily on content hash
        # Title hash can be checked for near-duplicates later
        return False

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
