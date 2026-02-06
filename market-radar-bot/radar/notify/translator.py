"""
Persian translator using OpenRouter LLM.
Translates news titles and quotes for Telegram alerts.
"""

import json
import re
from typing import Optional

import httpx
import structlog

from radar.config import get_settings

logger = structlog.get_logger()


class PersianTranslator:
    """
    Translates text to Persian using OpenRouter LLM.
    """

    def __init__(self):
        self.settings = get_settings()
        self.timeout = httpx.Timeout(30.0)

    @property
    def is_available(self) -> bool:
        """Check if OpenRouter API is configured."""
        return bool(self.settings.openrouter_api_key)

    def translate(self, title: str, quotes: list[str]) -> Optional[dict]:
        """
        Translate title and quotes to Persian.

        Args:
            title: News title to translate
            quotes: List of quotes to translate

        Returns:
            Dict with 'title' and 'quotes' in Persian, or None if failed
        """
        if not self.is_available:
            return None

        try:
            prompt = self._build_prompt(title, quotes)
            response = self._call_api(prompt)
            if not response:
                return None

            return self._parse_response(response)

        except Exception as e:
            logger.error("translator_error", error=str(e))
            return None

    def _build_prompt(self, title: str, quotes: list[str]) -> str:
        """Build the translation prompt."""
        quotes_text = "\n".join([f"{i+1}. {q}" for i, q in enumerate(quotes)])

        prompt = f"""Translate the following news title and quotes to Persian (Farsi).
Keep the translation natural and professional, suitable for financial news.

TITLE:
{title}

QUOTES:
{quotes_text}

Respond in JSON format only:
{{
    "title": "<Persian translation of title>",
    "quotes": ["<Persian translation of quote 1>", "<Persian translation of quote 2>", ...]
}}

IMPORTANT:
- Translate naturally, not word-for-word
- Keep financial/economic terms accurate
- Do not add any extra text outside the JSON"""

        return prompt

    def _call_api(self, prompt: str) -> Optional[str]:
        """Call the OpenRouter API."""
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://market-radar-bot.local",
            "X-Title": "Market Radar Bot",
        }

        # Use translation_model if set, otherwise fall back to openrouter_model
        model = getattr(self.settings, 'translation_model', None) or self.settings.openrouter_model

        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
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
                "translator_http_error",
                status=e.response.status_code,
                body=e.response.text[:200],
            )
            return None
        except Exception as e:
            logger.error("translator_api_error", error=str(e))
            return None

    def _parse_response(self, response: str) -> Optional[dict]:
        """Parse the translation response."""
        try:
            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', response)
            if not json_match:
                logger.warning("translator_no_json_in_response")
                return None

            data = json.loads(json_match.group())

            title = data.get("title", "")
            quotes = data.get("quotes", [])

            if not title:
                return None

            return {
                "title": title,
                "quotes": quotes,
            }

        except json.JSONDecodeError as e:
            logger.error("translator_json_parse_error", error=str(e))
            return None
        except Exception as e:
            logger.error("translator_parse_error", error=str(e))
            return None
