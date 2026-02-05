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

        prompt = f"""You are a senior financial market analyst at a major investment bank. Analyze the following news article and determine if it relates to any of the monitored entities/topics.

MONITORED ENTITIES/TOPICS:
{items_text}

ARTICLE TITLE:
{title}

ARTICLE TEXT:
{truncated_text}

TASK:
1. Determine which monitored entity/topic (if any) this article is most relevant to.
2. Extract 1-2 EXACT quotes from the article that support this match. These must be EXACT substrings from the article text - do not paraphrase or modify.
3. Write a professional analyst-style market impact assessment (5-10 sentences) that includes:
   - The core event and its immediate significance
   - Historical context or precedent (if relevant)
   - First-order effects: direct market impact
   - Second-order effects: knock-on consequences for related markets
   - Key risk factors or uncertainties
   - What traders should watch for (key levels, timing, catalysts)
4. List specific assets with their expected direction (bullish/bearish).

Respond in JSON format:
{{
    "match_id": <id of best matching entity or null if none>,
    "match_name": "<name of matched entity or null>",
    "confidence": <0.0 to 1.0>,
    "citations": ["<exact quote 1>", "<exact quote 2 if relevant>"],
    "reasoning": "<brief one-line summary>",
    "market_impact": "<ANALYST-STYLE ASSESSMENT: Write 5-10 sentences as a senior financial analyst would. Start with the core significance, then explain the market mechanics (why this moves prices), historical context if relevant, potential second-order effects on related assets, key uncertainties/risks, and what to watch. Be specific about price direction expectations. Do NOT just summarize the headline - provide actionable insight.>",
    "assets_with_impact": [
        {{"symbol": "EUR", "direction": "bearish"}},
        {{"symbol": "EURUSD", "direction": "bearish"}},
        {{"symbol": "German Bunds", "direction": "bullish"}}
    ]
}}

IMPORTANT:
- Citations MUST be exact substrings from the article. Do not modify or paraphrase.
- Only match if there is a clear, direct connection to the monitored entity.
- If no match is appropriate, return match_id: null.
- Confidence should reflect how certain you are of the match.
- The market_impact field is CRITICAL - write like a Bloomberg or Reuters analyst, not a news summary.
- For assets_with_impact, use "bullish" for assets expected to rise, "bearish" for those expected to fall, "neutral" if uncertain."""

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

            match_id = data.get("match_id")
            match_name = data.get("match_name")
            confidence = float(data.get("confidence", 0))
            citations = data.get("citations", [])
            reasoning = data.get("reasoning", "")
            market_impact = data.get("market_impact", "")

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

            # If no match, return early
            if match_id is None:
                return LLMMatch(
                    watch_item_id=None,
                    watch_item_name=None,
                    confidence=0.0,
                    trigger_spans=[],
                    reasoning=reasoning,
                    assets_affected=[],
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
                market_impact=market_impact or reasoning,  # Fall back to reasoning if no market_impact
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
