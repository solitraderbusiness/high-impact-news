"""
Severity scoring for detected events.
Combines multiple factors to produce a 0-100 score.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import structlog

from radar.models import SourceTier

logger = structlog.get_logger()


# Event type keywords that indicate high-impact events
HIGH_IMPACT_KEYWORDS = {
    # Central bank actions
    "rate hike": 15,
    "rate cut": 15,
    "interest rate": 10,
    "quantitative easing": 12,
    "quantitative tightening": 12,
    "tapering": 10,
    "emergency meeting": 20,
    "policy decision": 10,

    # Geopolitical
    "sanctions": 15,
    "war": 18,
    "invasion": 18,
    "military": 12,
    "conflict": 12,
    "escalation": 15,
    "de-escalation": 10,
    "ceasefire": 12,
    "peace talks": 10,

    # Trade & economics
    "tariff": 15,
    "trade war": 15,
    "embargo": 15,
    "default": 20,
    "debt crisis": 18,
    "recession": 15,
    "inflation": 10,
    "deflation": 12,
    "stagflation": 15,

    # Political
    "resignation": 15,
    "impeachment": 15,
    "election": 10,
    "coup": 20,
    "assassination": 25,
    "emergency": 12,

    # Market events
    "circuit breaker": 18,
    "flash crash": 20,
    "market crash": 20,
    "bank failure": 20,
    "bailout": 18,
    "liquidity crisis": 18,

    # Commodities
    "oil production cut": 15,
    "opec": 10,
    "gold": 8,
    "strategic reserve": 12,
}


@dataclass
class ScoreBreakdown:
    """Breakdown of severity score components."""
    base_confidence: int
    source_tier_bonus: int
    recency_bonus: int
    keyword_bonus: int
    severity_rule_bonus: int
    total: int

    def to_dict(self) -> Dict[str, int]:
        return {
            "base_confidence": self.base_confidence,
            "source_tier_bonus": self.source_tier_bonus,
            "recency_bonus": self.recency_bonus,
            "keyword_bonus": self.keyword_bonus,
            "severity_rule_bonus": self.severity_rule_bonus,
            "total": self.total,
        }


class SeverityScorer:
    """
    Calculates severity scores (0-100) for detected events.

    Factors:
    - Match confidence (base score)
    - Source tier (primary > secondary > social)
    - Recency (newer = higher)
    - Event type keywords
    - Custom severity rules per watch item
    """

    # Source tier bonuses
    TIER_BONUS = {
        SourceTier.PRIMARY: 20,
        SourceTier.SECONDARY: 10,
        SourceTier.SOCIAL: 0,
    }

    def score(
        self,
        match_confidence: float,
        source_tier: SourceTier,
        published_at: Optional[datetime],
        text: str,
        title: str,
        severity_rules: Optional[List[dict]] = None,
    ) -> ScoreBreakdown:
        """
        Calculate severity score for a detection.

        Args:
            match_confidence: 0.0 to 1.0 confidence from matching
            source_tier: Tier of the source
            published_at: When the article was published
            text: Article text
            title: Article title
            severity_rules: Custom severity rules for the watch item

        Returns:
            ScoreBreakdown with component scores and total
        """
        # Base score from confidence (0-40 points)
        base_confidence = int(match_confidence * 40)

        # Source tier bonus (0-20 points)
        source_tier_bonus = self.TIER_BONUS.get(source_tier, 0)

        # Recency bonus (0-15 points)
        recency_bonus = self._calculate_recency_bonus(published_at)

        # Keyword bonus from event type detection (0-25 points)
        keyword_bonus = self._calculate_keyword_bonus(text, title)

        # Custom severity rules (variable)
        severity_rule_bonus = self._apply_severity_rules(text, title, severity_rules)

        # Calculate total (capped at 100)
        total = min(
            base_confidence + source_tier_bonus + recency_bonus +
            keyword_bonus + severity_rule_bonus,
            100
        )

        return ScoreBreakdown(
            base_confidence=base_confidence,
            source_tier_bonus=source_tier_bonus,
            recency_bonus=recency_bonus,
            keyword_bonus=keyword_bonus,
            severity_rule_bonus=severity_rule_bonus,
            total=total,
        )

    def _calculate_recency_bonus(self, published_at: Optional[datetime]) -> int:
        """
        Calculate bonus based on how recent the article is.

        - Within 1 hour: 15 points
        - Within 6 hours: 12 points
        - Within 24 hours: 8 points
        - Within 7 days: 4 points
        - Older or unknown: 0 points
        """
        if not published_at:
            return 0

        now = datetime.utcnow()
        age = now - published_at

        if age < timedelta(hours=1):
            return 15
        elif age < timedelta(hours=6):
            return 12
        elif age < timedelta(hours=24):
            return 8
        elif age < timedelta(days=7):
            return 4
        else:
            return 0

    def _calculate_keyword_bonus(self, text: str, title: str) -> int:
        """
        Calculate bonus based on high-impact keywords found.
        Title matches are weighted higher.
        """
        full_text = f"{title}\n{text}".lower()
        title_lower = title.lower()

        total_bonus = 0
        matched_keywords = set()

        for keyword, bonus in HIGH_IMPACT_KEYWORDS.items():
            if keyword in full_text and keyword not in matched_keywords:
                matched_keywords.add(keyword)
                # Double bonus if keyword is in title
                if keyword in title_lower:
                    total_bonus += bonus * 2
                else:
                    total_bonus += bonus

        # Cap at 25 points
        return min(total_bonus, 25)

    def _apply_severity_rules(
        self,
        text: str,
        title: str,
        severity_rules: Optional[List[dict]],
    ) -> int:
        """
        Apply custom severity rules from watch item configuration.

        Rules format:
        [{"pattern": "regex_or_string", "score_bump": int}]
        """
        if not severity_rules:
            return 0

        full_text = f"{title}\n{text}".lower()
        total_bump = 0

        for rule in severity_rules:
            pattern = rule.get("pattern", "")
            score_bump = rule.get("score_bump", 0)

            if not pattern:
                continue

            try:
                # Try regex match
                if re.search(pattern, full_text, re.IGNORECASE):
                    total_bump += score_bump
            except re.error:
                # Fall back to simple string match
                if pattern.lower() in full_text:
                    total_bump += score_bump

        return total_bump
