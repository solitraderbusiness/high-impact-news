"""
Market relevance filter using LLM.
Evaluates if news is actually relevant to financial markets vs political noise.
"""

import json
import re
from dataclasses import dataclass
from typing import Optional

import httpx
import structlog

from radar.config import get_settings

logger = structlog.get_logger()


# Categories for news classification
class NewsCategory:
    # HIGH RELEVANCE - Direct market impact
    CENTRAL_BANK = "central_bank"  # Fed, ECB, BOJ decisions
    MONETARY_POLICY = "monetary_policy"  # Rate decisions, QE, QT
    ECONOMIC_DATA = "economic_data"  # GDP, CPI, NFP, PMI
    TRADE_POLICY = "trade_policy"  # Tariffs, trade deals, sanctions
    FISCAL_POLICY = "fiscal_policy"  # Government spending, taxes, debt
    CORPORATE_EARNINGS = "corporate_earnings"  # Major company results
    MARKET_EVENT = "market_event"  # Crashes, circuit breakers, IPOs
    GEOPOLITICAL_ECONOMIC = "geopolitical_economic"  # Wars affecting oil/commodities

    # MEDIUM RELEVANCE - Indirect impact
    POLITICAL_ECONOMIC = "political_economic"  # Elections with economic impact
    REGULATORY = "regulatory"  # Financial regulations, crypto rules
    COMMODITY_SUPPLY = "commodity_supply"  # OPEC, production cuts

    # LOW RELEVANCE - Usually no market impact
    POLITICAL_NOISE = "political_noise"  # Scandals, investigations
    SOCIAL_ISSUES = "social_issues"  # HR issues, discrimination cases
    ENTERTAINMENT = "entertainment"  # Celebrity news
    CRIME = "crime"  # Non-financial crimes
    HEALTH_LOCAL = "health_local"  # Local health policy
    OPINION = "opinion"  # Opinion pieces, speculation


# Relevance scores by category
CATEGORY_RELEVANCE = {
    # High relevance (80-100)
    NewsCategory.CENTRAL_BANK: 95,
    NewsCategory.MONETARY_POLICY: 95,
    NewsCategory.ECONOMIC_DATA: 90,
    NewsCategory.TRADE_POLICY: 90,
    NewsCategory.FISCAL_POLICY: 85,
    NewsCategory.MARKET_EVENT: 95,
    NewsCategory.GEOPOLITICAL_ECONOMIC: 85,

    # Medium relevance (50-79)
    NewsCategory.CORPORATE_EARNINGS: 75,
    NewsCategory.POLITICAL_ECONOMIC: 70,
    NewsCategory.REGULATORY: 65,
    NewsCategory.COMMODITY_SUPPLY: 80,

    # Low relevance (0-49)
    NewsCategory.POLITICAL_NOISE: 15,
    NewsCategory.SOCIAL_ISSUES: 10,
    NewsCategory.ENTERTAINMENT: 5,
    NewsCategory.CRIME: 10,
    NewsCategory.HEALTH_LOCAL: 20,
    NewsCategory.OPINION: 30,
}


@dataclass
class MarketRelevanceResult:
    """Result of market relevance evaluation."""
    is_relevant: bool  # Should this news be alerted?
    relevance_score: int  # 0-100 score
    category: str  # News category
    reasoning: str  # Why this classification
    suggested_assets: list  # Assets actually affected (if any)
    impact_type: str  # "direct", "indirect", or "none"


class MarketRelevanceFilter:
    """
    Filters news based on actual market relevance.
    Uses LLM to evaluate if news genuinely affects financial markets.
    """

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.timeout = httpx.Timeout(30.0)

    @property
    def is_available(self) -> bool:
        """Check if LLM API is configured."""
        return bool(self.settings.openrouter_api_key)

    def evaluate(self, title: str, text: str) -> Optional[MarketRelevanceResult]:
        """
        Evaluate if news is relevant to financial markets.

        Args:
            title: News headline
            text: News content

        Returns:
            MarketRelevanceResult with relevance assessment
        """
        if not self.is_available:
            logger.debug("market_relevance_filter_not_configured")
            return None

        try:
            prompt = self._build_prompt(title, text)
            response = self._call_api(prompt)
            if not response:
                return None

            return self._parse_response(response)

        except Exception as e:
            logger.error("market_relevance_error", error=str(e))
            return None

    def _build_prompt(self, title: str, text: str) -> str:
        """Build the evaluation prompt."""
        # Truncate text
        max_text_len = 4000
        truncated_text = text[:max_text_len] if len(text) > max_text_len else text

        prompt = f"""You are a senior financial market analyst. Your job is to evaluate if a news article is ACTUALLY RELEVANT to financial markets and would cause price movements.

NEWS TITLE:
{title}

NEWS TEXT:
{truncated_text}

TASK: Classify this news into ONE of these categories:

HIGH MARKET RELEVANCE (these move markets):
- central_bank: Central bank decisions, statements, meetings (Fed, ECB, BOJ, BOE, etc.)
- monetary_policy: Interest rate decisions, QE/QT, tapering, money supply
- economic_data: GDP, CPI, inflation, employment data, PMI, retail sales
- trade_policy: Tariffs, trade deals, sanctions affecting trade, embargoes
- fiscal_policy: Government spending, tax changes, debt ceiling, stimulus
- market_event: Market crashes, circuit breakers, bank failures, major IPOs
- geopolitical_economic: Wars/conflicts that affect oil, commodities, supply chains

MEDIUM MARKET RELEVANCE:
- corporate_earnings: Major company earnings, guidance (only for large-cap)
- political_economic: Elections/political events with clear economic implications
- regulatory: New financial regulations, crypto regulations
- commodity_supply: OPEC decisions, production cuts, supply disruptions

LOW/NO MARKET RELEVANCE (these do NOT move markets):
- political_noise: Political scandals, investigations, gossip without economic impact
- social_issues: Discrimination cases, HR issues, social controversies
- entertainment: Celebrity news, sports, pop culture
- crime: Non-financial crimes, local incidents
- health_local: Local health policies without market impact
- opinion: Opinion pieces, speculation, commentary without new information

CRITICAL RULES:
1. Just because a headline mentions a person like "Trump" does NOT mean it's market-relevant
2. Political scandals, investigations, and controversies are usually NOT market-relevant
3. Company HR/discrimination lawsuits are NOT market-relevant (except for massive impact on stock)
4. For news to be market-relevant, it must have a DIRECT or CLEAR INDIRECT effect on prices
5. Ask yourself: "Would a trader at Goldman Sachs care about this?" If no, it's not market-relevant

Respond in JSON:
{{
    "category": "<category from list above>",
    "is_market_relevant": <true/false>,
    "relevance_score": <0-100>,
    "impact_type": "<direct/indirect/none>",
    "reasoning": "<1-2 sentences explaining why this is or isn't market-relevant>",
    "affected_assets": ["<list of actually affected assets, or empty if none>"]
}}

IMPORTANT: Be strict. Most political/social news is NOT market-relevant. Only news that would cause actual trading decisions should be marked as relevant."""

        return prompt

    def _call_api(self, prompt: str) -> Optional[str]:
        """Call the LLM API."""
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
            "temperature": 0.1,
            "max_tokens": 500,
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
                "market_relevance_http_error",
                status=e.response.status_code,
                body=e.response.text[:500],
            )
            return None
        except Exception as e:
            logger.error("market_relevance_api_error", error=str(e))
            return None

    def _parse_response(self, response: str) -> Optional[MarketRelevanceResult]:
        """Parse the LLM response."""
        try:
            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', response)
            if not json_match:
                logger.warning("market_relevance_no_json")
                return None

            data = json.loads(json_match.group())

            category = data.get("category", "political_noise")
            is_relevant = data.get("is_market_relevant", False)
            relevance_score = int(data.get("relevance_score", 0))
            impact_type = data.get("impact_type", "none")
            reasoning = data.get("reasoning", "")
            affected_assets = data.get("affected_assets", [])

            # Ensure relevance_score matches category if not provided correctly
            if category in CATEGORY_RELEVANCE:
                expected_base = CATEGORY_RELEVANCE[category]
                # Allow LLM to adjust within range, but keep it reasonable
                if relevance_score > expected_base + 20:
                    relevance_score = expected_base + 10
                elif relevance_score < expected_base - 30:
                    relevance_score = max(expected_base - 20, 0)

            # Override is_relevant based on score threshold
            is_relevant = relevance_score >= 50

            return MarketRelevanceResult(
                is_relevant=is_relevant,
                relevance_score=relevance_score,
                category=category,
                reasoning=reasoning,
                suggested_assets=affected_assets,
                impact_type=impact_type,
            )

        except json.JSONDecodeError as e:
            logger.error("market_relevance_json_error", error=str(e))
            return None
        except Exception as e:
            logger.error("market_relevance_parse_error", error=str(e))
            return None
