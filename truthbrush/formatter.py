"""Formats Truth Social posts for Telegram messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from truthbrush.translator import PostTranslator


BASE_URL = "https://truthsocial.com"


@dataclass
class FormattedPost:
    """A Truth Social post formatted for Telegram."""

    text: str
    media_urls: list[str] = field(default_factory=list)

    @property
    def has_media(self) -> bool:
        return len(self.media_urls) > 0

    @property
    def is_album(self) -> bool:
        return len(self.media_urls) > 1


class _HTMLStripper(HTMLParser):
    """Minimal HTML tag stripper using stdlib."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._in_link = False
        self._link_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br":
            self._parts.append("\n")
        elif tag == "p":
            if self._parts:
                self._parts.append("\n\n")
        elif tag == "a":
            self._in_link = True
            self._link_href = None
            for attr_name, attr_value in attrs:
                if attr_name == "href" and attr_value:
                    self._link_href = attr_value
                    break

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_link = False
            self._link_href = None

    def handle_data(self, data: str) -> None:
        if self._in_link:
            # Use link text (e.g. "@user") instead of href to avoid duplication
            self._parts.append(data)
        else:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def strip_html(html: str) -> str:
    """Strip HTML tags, converting <br> and <p> to newlines."""
    if not html:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


def _extract_media(post: dict) -> list[str]:
    """Extract media URLs from a post's attachments."""
    urls = []
    for attachment in post.get("media_attachments", []):
        url = attachment.get("url") or attachment.get("preview_url")
        if url:
            urls.append(url)
    return urls


def _post_url(post: dict) -> str:
    """Build the Truth Social URL for a post."""
    username = post.get("account", {}).get("username", "")
    post_id = post.get("id", "")
    return f"{BASE_URL}/@{username}/posts/{post_id}"


def _translate_content(text: str, translator: PostTranslator | None) -> str:
    """Translate content if translator is available."""
    if translator and text:
        return translator.translate(text)
    return text


def format_post(post: dict, translator: PostTranslator | None = None) -> FormattedPost:
    """Convert a Truth Social post dict into a FormattedPost for Telegram.

    Handles regular posts, reblogs (retruths), and quote posts.
    Only post content is translated — usernames, links, and HTML tags are preserved.
    """
    account = post.get("account", {})
    display_name = account.get("display_name", "")
    username = account.get("username", "")

    reblog = post.get("reblog")
    quote = post.get("quote")

    parts: list[str] = []

    if reblog:
        # Retruth (repost)
        orig_account = reblog.get("account", {})
        orig_name = orig_account.get("display_name", "")
        orig_user = orig_account.get("username", "")
        parts.append(f"🔁 <b>{display_name}</b> retruthed <b>{orig_name}</b> (@{orig_user})")
        parts.append("")
        content = strip_html(reblog.get("content", ""))
        content = _translate_content(content, translator)
        if content:
            parts.append(content)
        media_urls = _extract_media(reblog)
        url = _post_url(reblog)
    else:
        parts.append(f"<b>{display_name}</b> (@{username})")
        parts.append("")
        content = strip_html(post.get("content", ""))
        content = _translate_content(content, translator)
        if content:
            parts.append(content)
        media_urls = _extract_media(post)
        url = _post_url(post)

    # Quote post
    if quote:
        quote_account = quote.get("account", {})
        quote_name = quote_account.get("display_name", "")
        quote_user = quote_account.get("username", "")
        quote_content = strip_html(quote.get("content", ""))
        quote_content = _translate_content(quote_content, translator)
        parts.append("")
        parts.append(f"┃ <b>{quote_name}</b> (@{quote_user})")
        if quote_content:
            parts.append(f"┃ {quote_content}")
        # Include quote media if main post has none
        if not media_urls:
            media_urls = _extract_media(quote)

    parts.append("")
    parts.append(f"🔗 {url}")

    text = "\n".join(parts)
    return FormattedPost(text=text, media_urls=media_urls)
