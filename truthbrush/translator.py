"""Optional translation of posts using Google Translate (free, no API key)."""

from deep_translator import GoogleTranslator
from loguru import logger


class PostTranslator:
    """Translates text to a target language."""

    def __init__(self, target_lang: str = "pl"):
        self.target_lang = target_lang
        self._translator = GoogleTranslator(source="auto", target=target_lang)

    def translate(self, text: str) -> str:
        """Translate text, preserving HTML tags and links.

        Returns original text if translation fails.
        """
        if not text:
            return text

        try:
            translated = self._translator.translate(text)
            if translated:
                return translated
            return text
        except Exception as e:
            logger.warning(f"Translation failed, using original text: {e}")
            return text
