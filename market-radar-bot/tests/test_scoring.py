"""
Tests for severity scoring functionality.
"""

from datetime import datetime, timedelta

import pytest

from radar.detect.scoring import SeverityScorer, HIGH_IMPACT_KEYWORDS
from radar.models import SourceTier


class TestSeverityScorer:
    """Tests for the SeverityScorer class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.scorer = SeverityScorer()

    def test_base_confidence_scoring(self):
        """Base score should be proportional to confidence."""
        # Low confidence
        result_low = self.scorer.score(
            match_confidence=0.3,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="",
            title="",
        )

        # High confidence
        result_high = self.scorer.score(
            match_confidence=0.9,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="",
            title="",
        )

        assert result_high.base_confidence > result_low.base_confidence
        assert result_low.base_confidence == int(0.3 * 40)  # 12
        assert result_high.base_confidence == int(0.9 * 40)  # 36

    def test_source_tier_bonus(self):
        """Primary sources should get higher bonus."""
        result_primary = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.PRIMARY,
            published_at=None,
            text="",
            title="",
        )

        result_secondary = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="",
            title="",
        )

        result_social = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SOCIAL,
            published_at=None,
            text="",
            title="",
        )

        assert result_primary.source_tier_bonus == 20
        assert result_secondary.source_tier_bonus == 10
        assert result_social.source_tier_bonus == 0

    def test_recency_bonus_recent(self):
        """Very recent articles should get high bonus."""
        now = datetime.utcnow()

        # Within 1 hour
        result_1h = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=now - timedelta(minutes=30),
            text="",
            title="",
        )

        # Within 6 hours
        result_6h = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=now - timedelta(hours=3),
            text="",
            title="",
        )

        assert result_1h.recency_bonus == 15
        assert result_6h.recency_bonus == 12

    def test_recency_bonus_old(self):
        """Old articles should get low or no bonus."""
        now = datetime.utcnow()

        # 3 days old
        result_3d = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=now - timedelta(days=3),
            text="",
            title="",
        )

        # 10 days old
        result_10d = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=now - timedelta(days=10),
            text="",
            title="",
        )

        assert result_3d.recency_bonus == 4
        assert result_10d.recency_bonus == 0

    def test_recency_bonus_no_date(self):
        """Missing publish date should get no bonus."""
        result = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="",
            title="",
        )

        assert result.recency_bonus == 0

    def test_keyword_bonus_in_text(self):
        """High-impact keywords should add bonus."""
        result = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="The Federal Reserve announced a rate hike today.",
            title="Fed News",
        )

        assert result.keyword_bonus > 0

    def test_keyword_bonus_in_title_doubled(self):
        """Keywords in title should get double bonus."""
        result_title = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="Some other text.",
            title="Fed Announces Rate Hike",
        )

        result_text = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="The Fed announced a rate hike.",
            title="News",
        )

        # Title match should be higher
        assert result_title.keyword_bonus >= result_text.keyword_bonus

    def test_keyword_bonus_capped(self):
        """Keyword bonus should be capped at 25."""
        # Text with many high-impact keywords
        text = """
        Emergency rate hike announced amid war escalation and sanctions.
        Default risk rises as tariffs trigger trade war concerns.
        Market crash fears as recession looms with possible bank failure.
        """

        result = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text=text,
            title="Crisis",
        )

        assert result.keyword_bonus <= 25

    def test_severity_rules_applied(self):
        """Custom severity rules should be applied."""
        rules = [
            {"pattern": "special event", "score_bump": 10},
        ]

        result = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="This is a special event that matters.",
            title="Event",
            severity_rules=rules,
        )

        assert result.severity_rule_bonus == 10

    def test_total_score_capped_at_100(self):
        """Total score should never exceed 100."""
        now = datetime.utcnow()

        result = self.scorer.score(
            match_confidence=1.0,  # 40 points
            source_tier=SourceTier.PRIMARY,  # 20 points
            published_at=now,  # 15 points
            text="Emergency rate hike amid war and sanctions threat.",  # ~25 points
            title="Rate Hike",  # Double bonus
            severity_rules=[{"pattern": "emergency", "score_bump": 50}],  # 50 points
        )

        assert result.total <= 100

    def test_score_breakdown_to_dict(self):
        """Score breakdown should be convertible to dict."""
        result = self.scorer.score(
            match_confidence=0.7,
            source_tier=SourceTier.PRIMARY,
            published_at=datetime.utcnow(),
            text="Fed rate hike",
            title="News",
        )

        breakdown = result.to_dict()

        assert "base_confidence" in breakdown
        assert "source_tier_bonus" in breakdown
        assert "recency_bonus" in breakdown
        assert "keyword_bonus" in breakdown
        assert "severity_rule_bonus" in breakdown
        assert "total" in breakdown

    def test_high_impact_keywords_defined(self):
        """High-impact keywords dictionary should be populated."""
        assert len(HIGH_IMPACT_KEYWORDS) > 0
        assert "rate hike" in HIGH_IMPACT_KEYWORDS
        assert "war" in HIGH_IMPACT_KEYWORDS
        assert "sanctions" in HIGH_IMPACT_KEYWORDS


class TestScoringEdgeCases:
    """Tests for edge cases in scoring."""

    def setup_method(self):
        """Set up test fixtures."""
        self.scorer = SeverityScorer()

    def test_empty_text_and_title(self):
        """Should handle empty text and title."""
        result = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="",
            title="",
        )

        # Should still compute base score
        assert result.base_confidence == 20  # 0.5 * 40
        assert result.keyword_bonus == 0

    def test_zero_confidence(self):
        """Should handle zero confidence."""
        result = self.scorer.score(
            match_confidence=0.0,
            source_tier=SourceTier.PRIMARY,
            published_at=None,
            text="",
            title="",
        )

        assert result.base_confidence == 0
        assert result.total >= 0

    def test_full_confidence(self):
        """Should handle full confidence."""
        result = self.scorer.score(
            match_confidence=1.0,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="",
            title="",
        )

        assert result.base_confidence == 40

    def test_invalid_severity_rule_pattern(self):
        """Should handle invalid regex patterns gracefully."""
        rules = [
            {"pattern": "[invalid(regex", "score_bump": 10},  # Invalid regex
        ]

        # Should not raise, should fall back to string match
        result = self.scorer.score(
            match_confidence=0.5,
            source_tier=SourceTier.SECONDARY,
            published_at=None,
            text="Some text with [invalid(regex pattern.",
            title="Test",
            severity_rules=rules,
        )

        # Should match as literal string
        assert result.severity_rule_bonus == 10
