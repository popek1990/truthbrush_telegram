"""Optional translation of posts via OpenAI GPT or Google Translate fallback."""

import os

from loguru import logger

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


class PostTranslator:
    """Translates text to a target language.

    Uses OpenAI GPT if OPENAI_API_KEY is set, otherwise falls back to Google Translate.
    """

    def __init__(self, target_lang: str = "pl"):
        self.target_lang = target_lang
        self._openai_client = None
        self._google_translator = None

        if OPENAI_API_KEY:
            from openai import OpenAI
            self._openai_client = OpenAI(api_key=OPENAI_API_KEY)
            logger.info(f"Translator: OpenAI gpt-4o-mini → {target_lang}")
        else:
            from deep_translator import GoogleTranslator
            self._google_translator = GoogleTranslator(source="auto", target=target_lang)
            logger.info(f"Translator: Google Translate → {target_lang}")

    def _translate_openai(self, text: str) -> str:
        """Translate using OpenAI GPT-4o-mini."""
        response = self._openai_client.chat.completions.create(
            model="gpt-4o-mini",
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
        return response.choices[0].message.content.strip()

    def _translate_google(self, text: str) -> str:
        """Translate using Google Translate (free fallback)."""
        truncated = text[:4500] if len(text) > 4500 else text
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
