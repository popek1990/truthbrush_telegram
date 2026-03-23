"""Optional translation of posts using Google Translate (free, no API key)."""

from deep_translator import GoogleTranslator
from loguru import logger

# Google Translate limit per request
MAX_TRANSLATE_LENGTH = 4500


class PostTranslator:
    """Translates text to a target language."""

    def __init__(self, target_lang: str = "pl"):
        self.target_lang = target_lang
        self._translator = GoogleTranslator(source="auto", target=target_lang)

    def translate(self, text: str) -> str:
        """Translate plain text content (no HTML, no URLs).

        Returns original text if translation fails or text is empty.
        """
        if not text or not text.strip():
            return text

        try:
            # Truncate to stay within Google Translate limits
            truncated = text[:MAX_TRANSLATE_LENGTH] if len(text) > MAX_TRANSLATE_LENGTH else text
            translated = self._translator.translate(truncated)
            if translated:
                return translated
            return text
        except Exception as e:
            logger.warning(f"Translation failed, using original text: {e}")
            return text
