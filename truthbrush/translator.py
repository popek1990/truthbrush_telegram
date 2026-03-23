"""Optional translation of posts via OpenAI GPT or Google Translate fallback."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
USAGE_FILE = os.getenv("USAGE_FILE", "/data/usage.json")

# Pricing per 1M tokens (USD)
MODEL_PRICING = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "google-translate": {"input": 0.00, "output": 0.00},
}


class UsageTracker:
    """Tracks OpenAI API usage and costs."""

    def __init__(self, usage_file: str = USAGE_FILE):
        self.usage_file = Path(usage_file)
        self._data = self._load()

    def _load(self) -> dict:
        if self.usage_file.exists():
            try:
                with open(self.usage_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {"total": {}, "daily": {}}
        return {"total": {}, "daily": {}}

    def _save(self) -> None:
        self.usage_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.usage_file, "w") as f:
                json.dump(self._data, f, indent=2)
        except OSError as e:
            logger.warning(f"Failed to save usage data: {e}")

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0})
        cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Update totals
        if model not in self._data["total"]:
            self._data["total"][model] = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        self._data["total"][model]["requests"] += 1
        self._data["total"][model]["input_tokens"] += input_tokens
        self._data["total"][model]["output_tokens"] += output_tokens
        self._data["total"][model]["cost_usd"] = round(self._data["total"][model]["cost_usd"] + cost, 6)

        # Update daily
        if today not in self._data["daily"]:
            self._data["daily"][today] = {}
        if model not in self._data["daily"][today]:
            self._data["daily"][today][model] = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        self._data["daily"][today][model]["requests"] += 1
        self._data["daily"][today][model]["input_tokens"] += input_tokens
        self._data["daily"][today][model]["output_tokens"] += output_tokens
        self._data["daily"][today][model]["cost_usd"] = round(self._data["daily"][today][model]["cost_usd"] + cost, 6)

        self._save()
        logger.debug(f"Usage: {model} +{input_tokens}in/{output_tokens}out = ${cost:.6f} (total: ${self._data['total'][model]['cost_usd']:.4f})")


class PostTranslator:
    """Translates text to a target language.

    Uses OpenAI GPT if OPENAI_API_KEY is set, otherwise falls back to Google Translate.
    """

    def __init__(self, target_lang: str = "pl"):
        self.target_lang = target_lang
        self._openai_client = None
        self._google_translator = None
        self._model = "google-translate"
        self._usage = UsageTracker()

        if OPENAI_API_KEY:
            from openai import OpenAI
            self._openai_client = OpenAI(api_key=OPENAI_API_KEY)
            self._model = "gpt-4o-mini"
            logger.info(f"Translator: OpenAI {self._model} → {target_lang}")
        else:
            from deep_translator import GoogleTranslator
            self._google_translator = GoogleTranslator(source="auto", target=target_lang)
            logger.info(f"Translator: Google Translate → {target_lang}")

    def _translate_openai(self, text: str) -> str:
        """Translate using OpenAI GPT-4o-mini."""
        response = self._openai_client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a translator. Translate the following text to {self.target_lang}. "
                        "Keep it natural and fluent. Preserve names, @handles, hashtags, "
                        "and URLs exactly as they are. Return ONLY the translation, nothing else."
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        usage = response.usage
        self._usage.record(self._model, usage.prompt_tokens, usage.completion_tokens)
        return response.choices[0].message.content.strip()

    def _translate_google(self, text: str) -> str:
        """Translate using Google Translate (free fallback)."""
        truncated = text[:4500] if len(text) > 4500 else text
        self._usage.record("google-translate", len(truncated), len(truncated))
        return self._google_translator.translate(truncated)

    def translate(self, text: str) -> str:
        """Translate text. Returns original if translation fails."""
        if not text or not text.strip():
            return text

        try:
            if self._openai_client:
                translated = self._translate_openai(text)
            else:
                translated = self._translate_google(text)

            if translated:
                return translated
            return text
        except Exception as e:
            logger.warning(f"Translation failed, using original text: {e}")
            return text
