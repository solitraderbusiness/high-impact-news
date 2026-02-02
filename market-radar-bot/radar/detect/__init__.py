"""
Detection system for matching events to watch items.
Includes rule-based matching and OpenRouter LLM fallback.
"""

from radar.detect.rules import RuleMatcher
from radar.detect.llm_openrouter import OpenRouterClient
from radar.detect.scoring import SeverityScorer
from radar.detect.dedup import Deduplicator

__all__ = ["RuleMatcher", "OpenRouterClient", "SeverityScorer", "Deduplicator"]
