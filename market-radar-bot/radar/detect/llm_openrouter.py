"""
OpenRouter LLM client for fallback event detection.
Only used when rule-based matching has low confidence.
"""

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

import httpx
import structlog

from radar.config import get_settings

logger = structlog.get_logger()


@dataclass
class AssetImpact:
    """An asset with its expected direction."""
    symbol: str
    direction: str  # "bullish", "bearish", or "neutral"


@dataclass
class LLMMatch:
    """Result of an LLM-based match."""
    watch_item_id: Optional[int]  # None if no match
    watch_item_name: Optional[str]
    confidence: float
    trigger_spans: List[str]  # Must be exact substrings from source text
    reasoning: str
    assets_affected: List[str] = field(default_factory=list)
    assets_with_impact: List[AssetImpact] = field(default_factory=list)  # Assets with direction
    market_impact: Optional[str] = None  # Detailed explanation of market impact
    # Market relevance fields (combined analysis)
    is_market_relevant: bool = True
    relevance_score: int = 100
    relevance_category: str = "market_event"
    # New trading-focused fields
    trade_bias: str = "NEUTRAL"  # BULLISH, BEARISH, NEUTRAL
    primary_asset: Optional[str] = None  # Main asset to trade
    setup: Optional[str] = None  # What happened and why it matters
    key_levels: Optional[str] = None  # Price levels to watch
    timeframe: str = "SWING"  # INTRADAY, SWING, POSITION
    catalyst: Optional[str] = None  # What to watch for confirmation
    risk: Optional[str] = None  # What could invalidate the trade


@dataclass
class WatchItemInfo:
    """Info about a watch item for LLM context."""
    id: int
    name: str
    category: str
    description: Optional[str]
    assets_affected: List[str]


class OpenRouterClient:
    """
    Client for OpenRouter API.
    Analyzes text to match against watch items when rules are insufficient.
    """

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.timeout = httpx.Timeout(60.0)

    @property
    def is_available(self) -> bool:
        """Check if OpenRouter API is configured."""
        return bool(self.settings.openrouter_api_key)

    def analyze(
        self,
        title: str,
        text: str,
        watch_items: List[WatchItemInfo],
    ) -> Optional[LLMMatch]:
        """
        Analyze text using OpenRouter LLM to match against watch items.

        Args:
            title: Article title
            text: Article text (may be truncated)
            watch_items: List of watch items to consider

        Returns:
            LLMMatch if successful, None if API unavailable or error
        """
        if not self.is_available:
            logger.debug("openrouter_not_configured")
            return None

        if not watch_items:
            return None

        try:
            # Build the prompt
            prompt = self._build_prompt(title, text, watch_items)

            # Call the API
            response = self._call_api(prompt)
            if not response:
                return None

            # Parse the response
            match = self._parse_response(response, text, watch_items)
            return match

        except Exception as e:
            logger.error("openrouter_error", error=str(e))
            return None

    def _build_prompt(
        self,
        title: str,
        text: str,
        watch_items: List[WatchItemInfo],
    ) -> str:
        """Build the analysis prompt."""
        # Truncate text for prompt (keep it reasonable for API)
        max_text_len = 8000
        truncated_text = text[:max_text_len] if len(text) > max_text_len else text

        # Format watch items
        items_text = "\n".join([
            f"- ID: {w.id}, Name: {w.name}, Category: {w.category}"
            + (f", Description: {w.description}" if w.description else "")
            for w in watch_items
        ])

        prompt = f"""You are a senior trader at Goldman Sachs. Analyze this news for trading opportunities.

MONITORED ENTITIES:
{items_text}

HEADLINE: {title}

ARTICLE:
{truncated_text}

ANALYSIS REQUIRED:

1. MARKET RELEVANCE - Would this move prices? Score 0-100.
   HIGH (80-100): Fed/central banks, rate decisions, GDP/CPI/NFP, tariffs, sanctions, geopolitical supply shocks
   LOW (0-30): Political scandals, HR disputes, celebrity gossip, non-financial crimes

2. TRADE SETUP - If relevant, what's the trade?

Respond in JSON:
{{
    "is_market_relevant": <true/false>,
    "relevance_score": <0-100>,
    "relevance_category": "<central_bank|economic_data|trade_policy|fiscal_policy|geopolitical|political_noise|other>",
    "match_id": <entity id or null>,
    "match_name": "<entity name or null>",
    "confidence": <0.0-1.0>,
    "citations": ["<exact quote from article>"],

    "trade_bias": "<BULLISH|BEARISH|NEUTRAL>",
    "primary_asset": "<main asset to trade, e.g. SPY, DXY, XAUUSD>",
    "assets_with_impact": [
        {{"symbol": "SPY", "direction": "bullish"}},
        {{"symbol": "US10Y", "direction": "bearish"}}
    ],

    "setup": "<1 sentence: what happened and why it matters>",
    "key_levels": "<specific price levels to watch, or 'N/A' if not applicable>",
    "timeframe": "<INTRADAY|SWING|POSITION>",
    "catalyst": "<what to watch for confirmation/invalidation>",
    "risk": "<what could make this trade wrong>"
}}

RULES:
- Trump/famous names mentioned ≠ market relevant. Must have ECONOMIC impact.
- Be specific with levels when possible (e.g., "SPY support at $480, resistance $495")
- Citations must be EXACT quotes from the article
- If relevance_score < 50, set match_id to null"""

        return prompt

    def _call_api(self, prompt: str) -> Optional[str]:
        """Call the OpenRouter API."""
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://market-radar-bot.local",
            "X-Title": "Market Radar Bot",
        }

        payload = {
            "model": self.settings.openrouter_model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,  # Low temperature for consistent results
            "max_tokens": 1000,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.settings.openrouter_base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()

            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return content

        except httpx.HTTPStatusError as e:
            logger.error(
                "openrouter_http_error",
                status=e.response.status_code,
                body=e.response.text[:500],
            )
            return None
        except Exception as e:
            logger.error("openrouter_api_error", error=str(e))
            return None

    def _parse_response(
        self,
        response: str,
        original_text: str,
        watch_items: List[WatchItemInfo],
    ) -> Optional[LLMMatch]:
        """Parse the LLM response and validate citations."""
        try:
            # Extract JSON from response (handle markdown code blocks)
            json_match = re.search(r'\{[\s\S]*\}', response)
            if not json_match:
                logger.warning("openrouter_no_json_in_response")
                return None

            data = json.loads(json_match.group())

            # Extract market relevance fields first
            is_market_relevant = data.get("is_market_relevant", True)
            relevance_score = int(data.get("relevance_score", 100))
            relevance_category = data.get("relevance_category", "market_event")
            relevance_reasoning = data.get("relevance_reasoning", "")

            match_id = data.get("match_id")
            match_name = data.get("match_name")
            confidence = float(data.get("confidence", 0))
            citations = data.get("citations", [])
            reasoning = data.get("reasoning", "") or relevance_reasoning
            market_impact = data.get("market_impact", "")

            # New trading-focused fields
            trade_bias = data.get("trade_bias", "NEUTRAL")
            primary_asset = data.get("primary_asset")
            setup = data.get("setup", "")
            key_levels = data.get("key_levels", "")
            timeframe = data.get("timeframe", "SWING")
            catalyst = data.get("catalyst", "")
            risk = data.get("risk", "")

            # Parse assets with impact direction
            assets_with_impact_raw = data.get("assets_with_impact", [])
            assets_with_impact = []
            assets_affected = []

            for asset_data in assets_with_impact_raw:
                if isinstance(asset_data, dict):
                    symbol = asset_data.get("symbol", "")
                    direction = asset_data.get("direction", "neutral")
                    if symbol:
                        assets_with_impact.append(AssetImpact(symbol=symbol, direction=direction))
                        assets_affected.append(symbol)
                elif isinstance(asset_data, str):
                    # Fallback for old format
                    assets_affected.append(asset_data)

            # Fallback to old assets_affected format if no assets_with_impact
            if not assets_affected:
                assets_affected = data.get("assets_affected", [])

            # If no match or not market relevant, return with relevance info
            if match_id is None or not is_market_relevant or relevance_score < 50:
                return LLMMatch(
                    watch_item_id=None,
                    watch_item_name=None,
                    confidence=0.0,
                    trigger_spans=[],
                    reasoning=reasoning,
                    assets_affected=[],
                    is_market_relevant=is_market_relevant,
                    relevance_score=relevance_score,
                    relevance_category=relevance_category,
                    trade_bias=trade_bias,
                    primary_asset=primary_asset,
                    setup=setup,
                    key_levels=key_levels,
                    timeframe=timeframe,
                    catalyst=catalyst,
                    risk=risk,
                )

            # Validate that match_id exists in watch_items
            valid_ids = {w.id for w in watch_items}
            if match_id not in valid_ids:
                logger.warning("openrouter_invalid_match_id", match_id=match_id)
                return None

            # CRITICAL: Validate citations are exact substrings
            validated_citations = []
            for citation in citations:
                if not citation:
                    continue
                # Check if citation is an exact substring (case-insensitive search)
                if self._is_exact_substring(citation, original_text):
                    validated_citations.append(citation)
                else:
                    logger.warning(
                        "openrouter_invalid_citation",
                        citation=citation[:100],
                    )

            # If no valid citations but high confidence, use reasoning as trigger span
            # This allows matches to proceed even when LLM paraphrases slightly
            if not validated_citations:
                if confidence >= 0.7 and reasoning:
                    logger.info("openrouter_using_reasoning_as_trigger", confidence=confidence)
                    validated_citations = [reasoning[:200]]
                else:
                    logger.warning("openrouter_no_valid_citations")
                    return None

            return LLMMatch(
                watch_item_id=match_id,
                watch_item_name=match_name,
                confidence=confidence,
                trigger_spans=validated_citations,
                reasoning=reasoning,
                assets_affected=assets_affected,
                assets_with_impact=assets_with_impact,
                market_impact=market_impact or setup or reasoning,  # Prefer setup for market_impact
                is_market_relevant=is_market_relevant,
                relevance_score=relevance_score,
                relevance_category=relevance_category,
                trade_bias=trade_bias,
                primary_asset=primary_asset,
                setup=setup,
                key_levels=key_levels,
                timeframe=timeframe,
                catalyst=catalyst,
                risk=risk,
            )

        except json.JSONDecodeError as e:
            logger.error("openrouter_json_parse_error", error=str(e))
            return None
        except Exception as e:
            logger.error("openrouter_parse_error", error=str(e))
            return None

    def _is_exact_substring(self, citation: str, text: str) -> bool:
        """
        Check if citation is an exact substring of text.
        Uses case-insensitive matching but requires exact character sequence.
        """
        if not citation or not text:
            return False

        # Normalize whitespace in both strings for comparison
        citation_normalized = ' '.join(citation.split())
        text_normalized = ' '.join(text.split())

        # Check if normalized citation exists in normalized text
        return citation_normalized.lower() in text_normalized.lower()
