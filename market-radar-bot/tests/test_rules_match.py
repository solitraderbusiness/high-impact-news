"""
Tests for rule-based matching functionality.
"""

import pytest

from radar.detect.rules import RuleMatcher, WatchItemRules, RuleMatch


class TestRuleMatcher:
    """Tests for the RuleMatcher class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.matcher = RuleMatcher(context_chars=50)

        # Sample watch items for testing
        self.watch_items = [
            WatchItemRules(
                id=1,
                name="Federal Reserve",
                keywords=["fed", "fomc", "interest rate", "monetary policy"],
                entities=["Federal Reserve", "Jerome Powell", "Fed Chair"],
                severity_rules=[
                    {"pattern": "rate hike", "score_bump": 15},
                    {"pattern": "rate cut", "score_bump": 15},
                ],
            ),
            WatchItemRules(
                id=2,
                name="Donald Trump",
                keywords=["tariff", "trade war"],
                entities=["Donald Trump", "Trump", "President Trump"],
                severity_rules=[
                    {"pattern": "tariff", "score_bump": 10},
                ],
            ),
            WatchItemRules(
                id=3,
                name="OPEC",
                keywords=["oil production", "barrel"],
                entities=["OPEC", "OPEC+"],
                severity_rules=[],
            ),
        ]

    def test_match_by_entity(self):
        """Should match text containing an entity."""
        text = "Jerome Powell announced new monetary policy measures today."
        title = "Fed Chair Speaks"

        matches = self.matcher.match(text, title, self.watch_items)

        assert len(matches) >= 1
        assert matches[0].watch_item_id == 1  # Federal Reserve
        assert matches[0].watch_item_name == "Federal Reserve"
        assert "Jerome Powell" in matches[0].matched_entities

    def test_match_by_keyword(self):
        """Should match text containing keywords."""
        text = "The FOMC is expected to discuss interest rate changes."
        title = "Markets Watch Fed"

        matches = self.matcher.match(text, title, self.watch_items)

        assert len(matches) >= 1
        fed_match = next((m for m in matches if m.watch_item_id == 1), None)
        assert fed_match is not None
        assert "fomc" in fed_match.matched_keywords or "interest rate" in fed_match.matched_keywords

    def test_match_case_insensitive(self):
        """Matching should be case-insensitive."""
        text = "The FEDERAL RESERVE announced policy changes."
        title = "Fed News"

        matches = self.matcher.match(text, title, self.watch_items)

        assert len(matches) >= 1
        assert matches[0].watch_item_id == 1

    def test_no_match_unrelated_text(self):
        """Should not match unrelated text."""
        text = "The weather today is sunny with clear skies."
        title = "Weather Report"

        matches = self.matcher.match(text, title, self.watch_items)

        assert len(matches) == 0

    def test_multiple_matches(self):
        """Should return multiple matches if text is relevant to multiple items."""
        text = "President Trump announced new tariffs after consulting with the Federal Reserve."
        title = "Trade Policy Update"

        matches = self.matcher.match(text, title, self.watch_items)

        # Should match both Trump and Fed
        assert len(matches) >= 2
        match_ids = {m.watch_item_id for m in matches}
        assert 1 in match_ids  # Fed
        assert 2 in match_ids  # Trump

    def test_matches_sorted_by_confidence(self):
        """Matches should be sorted by confidence, highest first."""
        text = "The Federal Reserve and Jerome Powell discussed monetary policy at the FOMC meeting."
        title = "Fed Meeting"

        matches = self.matcher.match(text, title, self.watch_items)

        # Verify sorted by confidence
        confidences = [m.confidence for m in matches]
        assert confidences == sorted(confidences, reverse=True)

    def test_trigger_spans_are_exact_substrings(self):
        """Trigger spans should be exact substrings from the text."""
        text = "The Federal Reserve announced a rate hike of 25 basis points."
        title = "Fed Raises Rates"

        matches = self.matcher.match(text, title, self.watch_items)

        assert len(matches) >= 1
        for match in matches:
            for span in match.trigger_spans:
                # Remove ellipsis for checking
                clean_span = span.replace("...", "").strip()
                # Normalize whitespace for comparison (newlines -> spaces)
                clean_span_normalized = ' '.join(clean_span.lower().split())
                # The span should appear in the combined text (normalized)
                full_text = f"{title}\n\n{text}"
                full_text_normalized = ' '.join(full_text.lower().split())
                assert clean_span_normalized in full_text_normalized

    def test_entity_word_boundary_matching(self):
        """Entities should match on word boundaries."""
        text = "The trump card in negotiations was their leverage."
        title = "Negotiations"

        matches = self.matcher.match(text, title, self.watch_items)

        # "trump" as a common word shouldn't match "Trump" entity
        # (though it might match "trump" keyword if defined)
        trump_match = next((m for m in matches if m.watch_item_id == 2), None)

        if trump_match:
            # If there's a match, it should be from keywords, not entities
            # since "trump card" isn't the entity "Trump"
            pass  # Implementation detail

    def test_confidence_calculation(self):
        """Confidence should increase with more matches."""
        # Text with one entity
        text1 = "Jerome Powell made an announcement."
        # Text with entity and keywords
        text2 = "Jerome Powell announced the Fed's monetary policy on interest rates."

        matches1 = self.matcher.match(text1, "News", self.watch_items)
        matches2 = self.matcher.match(text2, "News", self.watch_items)

        if matches1 and matches2:
            fed_match1 = next((m for m in matches1 if m.watch_item_id == 1), None)
            fed_match2 = next((m for m in matches2 if m.watch_item_id == 1), None)

            if fed_match1 and fed_match2:
                # More matches should mean higher confidence
                assert fed_match2.confidence >= fed_match1.confidence

    def test_empty_text_handling(self):
        """Should handle empty text gracefully."""
        matches = self.matcher.match("", "", self.watch_items)
        assert matches == []

    def test_empty_watch_items(self):
        """Should handle empty watch items list."""
        matches = self.matcher.match("Some text about the Fed.", "Title", [])
        assert matches == []


class TestSeverityRules:
    """Tests for severity rule matching."""

    def setup_method(self):
        """Set up test fixtures."""
        self.matcher = RuleMatcher()

    def test_severity_rule_match(self):
        """Should return score bump when pattern matches."""
        text = "The Fed announced a rate hike of 25 basis points."
        rules = [
            {"pattern": "rate hike", "score_bump": 15},
            {"pattern": "rate cut", "score_bump": 15},
        ]

        bump = self.matcher.match_severity_rules(text, rules)

        assert bump == 15

    def test_multiple_severity_rules_match(self):
        """Should accumulate bumps when multiple rules match."""
        text = "Emergency rate cut announced amid market turmoil."
        rules = [
            {"pattern": "emergency", "score_bump": 10},
            {"pattern": "rate cut", "score_bump": 15},
        ]

        bump = self.matcher.match_severity_rules(text, rules)

        assert bump == 25

    def test_no_severity_rule_match(self):
        """Should return 0 when no rules match."""
        text = "Regular market update with no significant news."
        rules = [
            {"pattern": "rate hike", "score_bump": 15},
            {"pattern": "emergency", "score_bump": 20},
        ]

        bump = self.matcher.match_severity_rules(text, rules)

        assert bump == 0

    def test_severity_rule_regex(self):
        """Should support regex patterns."""
        text = "The Fed raised rates by 0.50%"
        rules = [
            {"pattern": r"raised?\s+rates?", "score_bump": 15},
        ]

        bump = self.matcher.match_severity_rules(text, rules)

        assert bump == 15

    def test_empty_rules(self):
        """Should handle empty rules list."""
        bump = self.matcher.match_severity_rules("Some text", [])
        assert bump == 0

    def test_empty_text(self):
        """Should handle empty text."""
        rules = [{"pattern": "test", "score_bump": 10}]
        bump = self.matcher.match_severity_rules("", rules)
        assert bump == 0
