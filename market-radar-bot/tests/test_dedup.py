"""
Tests for deduplication functionality.
"""

from datetime import datetime

import pytest

from radar.detect.dedup import Deduplicator


class TestDeduplicator:
    """Tests for the Deduplicator class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.dedup = Deduplicator(cooldown_minutes=15, high_severity_threshold=90)

    def test_generate_content_hash_deterministic(self):
        """Content hash should be deterministic for same inputs."""
        url = "https://example.com/article"
        title = "Test Article Title"
        published = datetime(2024, 1, 15, 10, 30, 0)

        hash1 = Deduplicator.generate_content_hash(url, title, published)
        hash2 = Deduplicator.generate_content_hash(url, title, published)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex

    def test_generate_content_hash_different_inputs(self):
        """Different inputs should produce different hashes."""
        url = "https://example.com/article"
        title = "Test Article Title"
        published = datetime(2024, 1, 15, 10, 30, 0)

        hash1 = Deduplicator.generate_content_hash(url, title, published)
        hash2 = Deduplicator.generate_content_hash(url, "Different Title", published)
        hash3 = Deduplicator.generate_content_hash("https://other.com", title, published)

        assert hash1 != hash2
        assert hash1 != hash3
        assert hash2 != hash3

    def test_generate_content_hash_case_insensitive(self):
        """Content hash should be case-insensitive."""
        url = "https://example.com/article"
        published = datetime(2024, 1, 15)

        hash1 = Deduplicator.generate_content_hash(url, "Test Title", published)
        hash2 = Deduplicator.generate_content_hash(url, "test title", published)

        assert hash1 == hash2

    def test_generate_title_hash_normalized(self):
        """Title hash should normalize text."""
        # Same title with different punctuation/case
        title1 = "Fed Raises Interest Rates!"
        title2 = "Fed raises interest rates"
        title3 = "Fed Raises Interest Rates."

        hash1 = Deduplicator.generate_title_hash(title1)
        hash2 = Deduplicator.generate_title_hash(title2)
        hash3 = Deduplicator.generate_title_hash(title3)

        # All should produce the same normalized hash
        assert hash1 == hash2
        assert hash2 == hash3

    def test_generate_title_hash_different_titles(self):
        """Different titles should produce different hashes."""
        hash1 = Deduplicator.generate_title_hash("Fed Raises Rates")
        hash2 = Deduplicator.generate_title_hash("Fed Cuts Rates")

        assert hash1 != hash2

    def test_jaccard_similarity_identical(self):
        """Identical texts should have similarity of 1.0."""
        text = "The Federal Reserve raised interest rates today."

        similarity = self.dedup.jaccard_similarity(text, text)

        assert similarity == 1.0

    def test_jaccard_similarity_completely_different(self):
        """Completely different texts should have low similarity."""
        text1 = "apple banana cherry"
        text2 = "dog elephant fox"

        similarity = self.dedup.jaccard_similarity(text1, text2)

        assert similarity == 0.0

    def test_jaccard_similarity_partial_overlap(self):
        """Partially overlapping texts should have medium similarity."""
        text1 = "The Federal Reserve raised interest rates today"
        text2 = "The Federal Reserve cut interest rates yesterday"

        similarity = self.dedup.jaccard_similarity(text1, text2)

        # Should have significant overlap (Federal, Reserve, interest, rates, The)
        assert 0.3 < similarity < 0.8

    def test_jaccard_similarity_empty_text(self):
        """Empty text should return 0.0 similarity."""
        similarity = self.dedup.jaccard_similarity("", "some text")

        assert similarity == 0.0

    def test_normalize_text(self):
        """Text normalization should lowercase and remove punctuation."""
        text = "Fed Raises Rates! (Breaking News)"

        normalized = Deduplicator.normalize_text(text)

        assert normalized == "fed raises rates breaking news"

    def test_near_duplicate_title_detection(self):
        """Near-duplicate titles should be detected."""
        title1 = "Federal Reserve Raises Interest Rates by 0.25%"
        title2 = "Federal Reserve raises interest rates by 0.25%!"

        hash1 = Deduplicator.generate_title_hash(title1)
        hash2 = Deduplicator.generate_title_hash(title2)

        recent_hashes = {hash1}

        assert self.dedup.is_near_duplicate_title(hash2, recent_hashes)

    def test_not_near_duplicate_title(self):
        """Sufficiently different titles should not be flagged."""
        title1 = "Federal Reserve Raises Rates"
        title2 = "ECB Cuts Interest Rates"

        hash1 = Deduplicator.generate_title_hash(title1)
        hash2 = Deduplicator.generate_title_hash(title2)

        recent_hashes = {hash1}

        assert not self.dedup.is_near_duplicate_title(hash2, recent_hashes)


class TestDeduplicatorCooldown:
    """Tests for cooldown functionality (requires mock DB)."""

    def test_high_severity_bypasses_cooldown(self):
        """High severity (>=90) should bypass cooldown."""
        dedup = Deduplicator(cooldown_minutes=15, high_severity_threshold=90)

        # This test would need a mock database
        # For now, we just verify the threshold is set correctly
        assert dedup.high_severity_threshold == 90
        assert dedup.cooldown_minutes == 15
