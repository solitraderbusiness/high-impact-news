"""
Rule-based matching for detecting events related to watch items.
Uses keywords and entities configured for each watch item.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import structlog

logger = structlog.get_logger()


@dataclass
class RuleMatch:
    """Result of a rule-based match."""
    watch_item_id: int
    watch_item_name: str
    confidence: float  # 0.0 to 1.0
    trigger_spans: List[str]  # Exact quotes that triggered the match
    matched_keywords: List[str]
    matched_entities: List[str]


@dataclass
class WatchItemRules:
    """Rules for a watch item."""
    id: int
    name: str
    keywords: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    severity_rules: List[dict] = field(default_factory=list)


class RuleMatcher:
    """
    Matches text against watch item rules (keywords and entities).
    Returns confidence scores and exact trigger spans.
    """

    def __init__(self, context_chars: int = 100):
        """
        Initialize the matcher.

        Args:
            context_chars: Number of characters to include around matched terms
        """
        self.context_chars = context_chars

    def match(
        self,
        text: str,
        title: str,
        watch_items: List[WatchItemRules],
    ) -> List[RuleMatch]:
        """
        Match text against all watch items.

        Args:
            text: The main text to search (raw_text)
            title: The title/headline
            watch_items: List of watch items with their rules

        Returns:
            List of matches, sorted by confidence (highest first)
        """
        if not text and not title:
            return []

        # Combine title and text for searching
        full_text = f"{title}\n\n{text}" if text else title
        full_text_lower = full_text.lower()

        matches: List[RuleMatch] = []

        for item in watch_items:
            match = self._match_single(full_text, full_text_lower, item)
            if match:
                matches.append(match)

        # Sort by confidence, highest first
        matches.sort(key=lambda m: m.confidence, reverse=True)

        return matches

    def _match_single(
        self,
        text: str,
        text_lower: str,
        item: WatchItemRules,
    ) -> Optional[RuleMatch]:
        """Match text against a single watch item's rules."""
        matched_keywords: List[str] = []
        matched_entities: List[str] = []
        trigger_spans: List[str] = []

        # Match keywords (case-insensitive)
        for keyword in item.keywords:
            if not keyword:
                continue

            keyword_lower = keyword.lower()
            if keyword_lower in text_lower:
                matched_keywords.append(keyword)
                # Extract trigger span
                span = self._extract_trigger_span(text, text_lower, keyword_lower)
                if span and span not in trigger_spans:
                    trigger_spans.append(span)

        # Match entities (case-insensitive, but try to preserve original case)
        for entity in item.entities:
            if not entity:
                continue

            entity_lower = entity.lower()
            # Use word boundaries for entities to avoid partial matches
            pattern = r'\b' + re.escape(entity_lower) + r'\b'
            if re.search(pattern, text_lower):
                matched_entities.append(entity)
                span = self._extract_trigger_span(text, text_lower, entity_lower)
                if span and span not in trigger_spans:
                    trigger_spans.append(span)

        # Calculate confidence
        if not matched_keywords and not matched_entities:
            return None

        confidence = self._calculate_confidence(
            matched_keywords,
            matched_entities,
            item.keywords,
            item.entities,
        )

        # Limit trigger spans to 3 most relevant
        trigger_spans = trigger_spans[:3]

        return RuleMatch(
            watch_item_id=item.id,
            watch_item_name=item.name,
            confidence=confidence,
            trigger_spans=trigger_spans,
            matched_keywords=matched_keywords,
            matched_entities=matched_entities,
        )

    def _extract_trigger_span(
        self,
        text: str,
        text_lower: str,
        term_lower: str,
    ) -> Optional[str]:
        """
        Extract a trigger span (exact quote) around a matched term.
        Returns the exact substring from the original text.
        """
        # Find the position of the term
        pos = text_lower.find(term_lower)
        if pos == -1:
            return None

        # Calculate span boundaries
        start = max(0, pos - self.context_chars)
        end = min(len(text), pos + len(term_lower) + self.context_chars)

        # Adjust to word boundaries
        if start > 0:
            # Find the next space after start
            space_pos = text.find(" ", start)
            if space_pos != -1 and space_pos < pos:
                start = space_pos + 1

        if end < len(text):
            # Find the previous space before end
            space_pos = text.rfind(" ", pos + len(term_lower), end)
            if space_pos != -1:
                end = space_pos

        # Extract span
        span = text[start:end].strip()

        # Add ellipsis if truncated
        if start > 0:
            span = "..." + span
        if end < len(text):
            span = span + "..."

        # Clean up any excessive whitespace
        span = re.sub(r'\s+', ' ', span)

        return span

    def _calculate_confidence(
        self,
        matched_keywords: List[str],
        matched_entities: List[str],
        all_keywords: List[str],
        all_entities: List[str],
    ) -> float:
        """
        Calculate match confidence based on matched terms.

        Scoring logic:
        - Entity matches are weighted higher (0.4 per match, max 0.8)
        - Keyword matches contribute (0.2 per match, max 0.6)
        - Minimum confidence for any match is 0.2
        - Maximum confidence is 1.0
        """
        confidence = 0.0

        # Entity matches (higher weight)
        if matched_entities:
            entity_score = min(len(matched_entities) * 0.4, 0.8)
            confidence += entity_score

        # Keyword matches
        if matched_keywords:
            keyword_score = min(len(matched_keywords) * 0.2, 0.6)
            confidence += keyword_score

        # Apply ceiling
        confidence = min(confidence, 1.0)

        # Ensure minimum threshold for any match
        if matched_keywords or matched_entities:
            confidence = max(confidence, 0.2)

        return round(confidence, 2)

    def match_severity_rules(
        self,
        text: str,
        severity_rules: List[dict],
    ) -> int:
        """
        Check severity rules against text and return score bump.

        Each rule should have:
        - pattern: regex or simple string to match
        - score_bump: int to add to severity

        Returns:
            Total score bump from matching rules
        """
        if not text or not severity_rules:
            return 0

        text_lower = text.lower()
        total_bump = 0

        for rule in severity_rules:
            pattern = rule.get("pattern", "")
            score_bump = rule.get("score_bump", 0)

            if not pattern:
                continue

            try:
                # Try as regex first
                if re.search(pattern, text_lower, re.IGNORECASE):
                    total_bump += score_bump
            except re.error:
                # Fall back to simple string matching
                if pattern.lower() in text_lower:
                    total_bump += score_bump

        return total_bump
