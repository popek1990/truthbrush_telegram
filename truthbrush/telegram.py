"""Telegram Bot API client for sending messages to channels."""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from time import sleep
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from truthbrush.formatter import MediaItem


TELEGRAM_API_BASE = "https://api.telegram.org/bot"
MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024
MAX_MEDIA_GROUP_SIZE = 10

# Substrings in a Telegram error description that mean the problem is with the
# whole channel/bot (wrong bot token, bot removed, chat not found, no rights).
# These must NOT cause a post to be skipped — the monitor retries with backoff.
_CHANNEL_LEVEL_MARKERS = (
    "chat not found",
    "bot was blocked",
    "bot was kicked",
    "bot is not a member",
    "not enough rights",
    "have no rights",
    "need administrator rights",
    "chat_write_forbidden",
    "user is deactivated",
    "chat_admin_required",
)

# Substrings that mean a specific media URL could not be fetched/sent (wrong type,
# too big, bad URL). The monitor falls back to linking the media in the text.
_MEDIA_LEVEL_MARKERS = (
    "wrong type of the web page content",
    "failed to get http url",
    "wrong file identifier",
    "webpage_media_empty",
    "image_process_failed",
    "photo_invalid_dimensions",
    "file is too big",
    "is too big",
    "wrong remote file",
)


class TelegramSendError(Exception):
    """Raised when a Telegram API call fails after all retries.

    Carries the HTTP status ``code`` and Telegram's ``description`` so callers can
    tell a post-specific problem (bad entities, too long, bad media) apart from a
    channel-wide one (bad bot token, bot kicked, chat not found).
    """

    def __init__(
        self,
        message: str,
        code: Optional[int] = None,
        description: Optional[str] = None,
    ):
        super().__init__(message)
        self.code = code
        self.description = description

    def _haystack(self) -> str:
        return (self.description or str(self)).lower()

    def is_channel_level(self) -> bool:
        """True when the failure is about the whole channel/bot, not one post."""
        if self.code in (401, 403):
            return True
        return any(marker in self._haystack() for marker in _CHANNEL_LEVEL_MARKERS)

    def is_media_level(self) -> bool:
        """True when a specific media URL could not be sent (fall back to a link)."""
        return any(marker in self._haystack() for marker in _MEDIA_LEVEL_MARKERS)


def utf16_len(text: str) -> int:
    """Length of ``text`` in UTF-16 code units — how Telegram counts limits."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


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
                description = self._parse_description(body)

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
                if 400 <= e.code < 500 and e.code != 429:
                    raise TelegramSendError(
                        f"Telegram client error {e.code}: {description or body}",
                        code=e.code,
                        description=description,
                    ) from e

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
    def _parse_description(body: str) -> Optional[str]:
        """Pull Telegram's ``description`` field out of an error body."""
        try:
            return json.loads(body).get("description")
        except (json.JSONDecodeError, AttributeError):
            return None

    @classmethod
    def caption_fits(cls, text: str) -> bool:
        """True when ``text`` fits inside a Telegram media caption."""
        return utf16_len(text) <= MAX_CAPTION_LENGTH

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        """Truncate ``text`` to ``limit`` Telegram units (UTF-16), HTML-safely.

        Telegram counts limits in UTF-16 code units (an emoji counts as 2), so we
        measure the same way and never split a surrogate pair. Truncation also
        avoids leaving a half-written HTML tag or entity (e.g. ``&amp;``) at the
        end, and closes any tag we opened.
        """
        if utf16_len(text) <= limit:
            return text

        budget = max(0, limit - 3)  # reserve room for the "..." marker
        out: list[str] = []
        used = 0
        for ch in text:
            width = 2 if ord(ch) > 0xFFFF else 1
            if used + width > budget:
                break
            out.append(ch)
            used += width
        truncated = "".join(out)

        # Drop a trailing partial tag ("<b", "<b attr") and partial entity ("&am").
        truncated = re.sub(r"<[^>]*$", "", truncated)
        truncated = re.sub(r"&[#0-9A-Za-z]*$", "", truncated)

        # Close any unclosed tags (we only emit <b> ourselves, but be general).
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

    def send_message(self, text: str, parse_mode: Optional[str] = "HTML") -> dict:
        """Send a text message to the configured chat.

        ``parse_mode=None`` sends the text verbatim (used for the plain-text
        fallback when HTML parsing is rejected).
        """
        text = self._truncate(text, MAX_MESSAGE_LENGTH)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": False,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        result = self._request("sendMessage", payload)
        logger.debug(f"Message sent to {self.chat_id}")
        return result

    def send_photo(self, photo_url: str, caption: str = "", parse_mode: str = "HTML") -> dict:
        """Send a single photo with optional caption."""
        return self._send_single("sendPhoto", "photo", photo_url, caption, parse_mode)

    def send_video(self, video_url: str, caption: str = "", parse_mode: str = "HTML") -> dict:
        """Send a single video with optional caption."""
        return self._send_single("sendVideo", "video", video_url, caption, parse_mode)

    def send_animation(self, animation_url: str, caption: str = "", parse_mode: str = "HTML") -> dict:
        """Send a single animation (used for ``gifv`` attachments)."""
        return self._send_single("sendAnimation", "animation", animation_url, caption, parse_mode)

    def _send_single(
        self, method: str, field: str, url: str, caption: str, parse_mode: str
    ) -> dict:
        caption = self._truncate(caption, MAX_CAPTION_LENGTH)
        payload = {"chat_id": self.chat_id, field: url, "caption": caption, "parse_mode": parse_mode}
        result = self._request(method, payload)
        logger.debug(f"{field.capitalize()} sent to {self.chat_id}")
        return result

    def send_media_group(
        self, media: list["MediaItem"], caption: str = "", parse_mode: str = "HTML"
    ) -> dict:
        """Send several attachments as an album (mixed photos + videos allowed).

        Caption goes on the first item. ``gifv`` must not be in an album, so the
        caller is expected to pass only image/video items here.
        """
        from truthbrush.formatter import MEDIA_VIDEO

        if not media:
            return self.send_message(caption, parse_mode)

        if len(media) == 1:
            item = media[0]
            if item.kind == MEDIA_VIDEO:
                return self.send_video(item.url, caption, parse_mode)
            return self.send_photo(item.url, caption, parse_mode)

        # Telegram allows max 10 items in a media group
        if len(media) > MAX_MEDIA_GROUP_SIZE:
            logger.warning(f"Trimming media group from {len(media)} to {MAX_MEDIA_GROUP_SIZE} items")
            media = media[:MAX_MEDIA_GROUP_SIZE]

        caption = self._truncate(caption, MAX_CAPTION_LENGTH)
        group = []
        for i, item in enumerate(media):
            telegram_type = "video" if item.kind == MEDIA_VIDEO else "photo"
            entry = {"type": telegram_type, "media": item.url}
            if i == 0 and caption:
                entry["caption"] = caption
                entry["parse_mode"] = parse_mode
            group.append(entry)

        payload = {"chat_id": self.chat_id, "media": group}
        result = self._request("sendMediaGroup", payload)
        logger.debug(f"Media group ({len(media)} items) sent to {self.chat_id}")
        return result
