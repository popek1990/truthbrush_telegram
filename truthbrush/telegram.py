"""Telegram Bot API client for sending messages to channels."""

import json
import re
import urllib.request
import urllib.error
from time import sleep
from typing import Optional

from loguru import logger


TELEGRAM_API_BASE = "https://api.telegram.org/bot"
MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024
MAX_MEDIA_GROUP_SIZE = 10


class TelegramSendError(Exception):
    """Raised when a Telegram API call fails after all retries."""


class TelegramSender:
    """Sends messages to a Telegram channel via Bot API."""

    def __init__(self, bot_token: str, chat_id: str, max_retries: int = 3):
        self._bot_token = bot_token
        self.chat_id = chat_id
        self.max_retries = max_retries
        self._base_url = f"{TELEGRAM_API_BASE}{bot_token}"

    def __repr__(self) -> str:
        return f"TelegramSender(chat_id={self.chat_id!r})"

    def _request(self, method: str, payload: dict) -> dict:
        """Make a POST request to the Telegram Bot API with retry + backoff."""
        url = f"{self._base_url}/{method}"
        data = json.dumps(payload).encode("utf-8")

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                last_error = e

                # Rate limited — respect retry_after
                if e.code == 429:
                    try:
                        retry_after = json.loads(body).get("parameters", {}).get("retry_after", 5)
                    except (json.JSONDecodeError, AttributeError):
                        retry_after = 5
                    logger.warning(f"Telegram rate limited, retry after {retry_after}s")
                    sleep(retry_after)
                    continue

                logger.error(f"Telegram API error {e.code}: {body}")
                if e.code >= 400 and e.code < 500 and e.code != 429:
                    raise TelegramSendError(f"Telegram client error {e.code}: {body}") from e

            except (urllib.error.URLError, OSError) as e:
                last_error = e
                logger.warning(f"Telegram request failed (attempt {attempt}/{self.max_retries}): {e}")

            # Exponential backoff
            if attempt < self.max_retries:
                wait = 2 ** attempt
                logger.info(f"Retrying in {wait}s...")
                sleep(wait)

        raise TelegramSendError(f"Failed after {self.max_retries} retries") from last_error

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        """Truncate text to fit Telegram's character limits.

        Handles HTML safely — if truncation cuts inside a tag, removes the
        partial tag and closes any unclosed tags.
        """
        if len(text) <= limit:
            return text
        truncated = text[: limit - 3]
        # Remove any partial HTML tag at the end (e.g. "<b" or "<b attr")
        truncated = re.sub(r"<[^>]*$", "", truncated)
        # Close any unclosed tags (we only use <b> in our formatter)
        open_tags = re.findall(r"<(b|i|a|code|pre)\b[^>]*>", truncated)
        close_tags = re.findall(r"</(b|i|a|code|pre)>", truncated)
        unclosed = []
        for tag in open_tags:
            if tag in close_tags:
                close_tags.remove(tag)
            else:
                unclosed.append(tag)
        closing = "".join(f"</{tag}>" for tag in reversed(unclosed))
        return truncated + "..." + closing

    def send_message(self, text: str, parse_mode: str = "HTML") -> dict:
        """Send a text message to the configured chat."""
        text = self._truncate(text, MAX_MESSAGE_LENGTH)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False,
        }
        result = self._request("sendMessage", payload)
        logger.debug(f"Message sent to {self.chat_id}")
        return result

    def send_photo(self, photo_url: str, caption: str = "", parse_mode: str = "HTML") -> dict:
        """Send a single photo with optional caption."""
        caption = self._truncate(caption, MAX_CAPTION_LENGTH)
        payload = {
            "chat_id": self.chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": parse_mode,
        }
        result = self._request("sendPhoto", payload)
        logger.debug(f"Photo sent to {self.chat_id}")
        return result

    def send_media_group(self, media_urls: list[str], caption: str = "", parse_mode: str = "HTML") -> dict:
        """Send multiple photos as an album. Caption goes on the first item."""
        if not media_urls:
            return self.send_message(caption, parse_mode)

        if len(media_urls) == 1:
            return self.send_photo(media_urls[0], caption, parse_mode)

        # Telegram allows max 10 items in a media group
        if len(media_urls) > MAX_MEDIA_GROUP_SIZE:
            logger.warning(f"Trimming media group from {len(media_urls)} to {MAX_MEDIA_GROUP_SIZE} items")
            media_urls = media_urls[:MAX_MEDIA_GROUP_SIZE]

        caption = self._truncate(caption, MAX_CAPTION_LENGTH)
        media = []
        for i, url in enumerate(media_urls):
            item = {"type": "photo", "media": url}
            if i == 0 and caption:
                item["caption"] = caption
                item["parse_mode"] = parse_mode
            media.append(item)

        payload = {
            "chat_id": self.chat_id,
            "media": media,
        }
        result = self._request("sendMediaGroup", payload)
        logger.debug(f"Media group ({len(media_urls)} items) sent to {self.chat_id}")
        return result
