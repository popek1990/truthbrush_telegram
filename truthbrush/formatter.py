"""Formats Truth Social posts for Telegram messages."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from truthbrush.translator import PostTranslator


BASE_URL = "https://truthsocial.com"

# Media kinds we distinguish. Anything else is treated as "unknown" and linked
# in the text instead of being sent as a Telegram media attachment.
MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"
MEDIA_GIFV = "gifv"
MEDIA_UNKNOWN = "unknown"

SENDABLE_MEDIA_KINDS = (MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_GIFV)


@dataclass(frozen=True)
class MediaItem:
    """A single media attachment with its Truth Social type preserved."""

    url: str
    kind: str


@dataclass
class FormattedPost:
    """A Truth Social post formatted for Telegram.

    ``text`` is the HTML-safe body (dynamic fields escaped, our ``<b>`` tags and
    link intact). ``plain_text`` is the same content without HTML — used for the
    plain-text fallback when Telegram rejects the HTML parse. ``media`` holds only
    the attachments we can actually send (image/video/gifv); unknown attachments
    are already linked inside the text.
    """

    text: str
    plain_text: str = ""
    media: list[MediaItem] = field(default_factory=list)

    @property
    def has_media(self) -> bool:
        return len(self.media) > 0


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


def strip_html(html_text: str) -> str:
    """Strip HTML tags, converting <br> and <p> to newlines.

    Note: HTMLParser decodes entities (``&amp;`` → ``&``, ``&lt;`` → ``<``), so the
    returned text is *raw* and must be re-escaped before going into an HTML message.
    """
    if not html_text:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html_text)
    return stripper.get_text()


def _esc(text: str | None) -> str:
    """Escape text for Telegram HTML parse mode (``&`` ``<`` ``>``)."""
    return html.escape(text or "", quote=False)


def _extract_media(post: dict) -> list[MediaItem]:
    """Extract media attachments, preserving each attachment's type."""
    items: list[MediaItem] = []
    for attachment in post.get("media_attachments", []):
        url = attachment.get("url") or attachment.get("preview_url")
        if not url:
            continue
        kind = attachment.get("type") or MEDIA_UNKNOWN
        if kind not in SENDABLE_MEDIA_KINDS:
            kind = MEDIA_UNKNOWN
        items.append(MediaItem(url=url, kind=kind))
    return items


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
    Dynamic fields are HTML-escaped so a literal ``<`` or ``&`` in the content or a
    display name cannot break Telegram's HTML parser.
    """
    account = post.get("account", {})
    display_name = account.get("display_name", "")
    username = account.get("username", "")

    reblog = post.get("reblog")
    quote = post.get("quote")

    html_parts: list[str] = []
    plain_parts: list[str] = []

    def add(html_line: str = "", plain_line: str | None = None) -> None:
        html_parts.append(html_line)
        plain_parts.append(html_line if plain_line is None else plain_line)

    if reblog:
        # Retruth (repost)
        orig_account = reblog.get("account", {})
        orig_name = orig_account.get("display_name", "")
        orig_user = orig_account.get("username", "")
        add(
            f"🔁 <b>{_esc(display_name)}</b> retruthed <b>{_esc(orig_name)}</b> (@{_esc(orig_user)})",
            f"🔁 {display_name} retruthed {orig_name} (@{orig_user})",
        )
        add("")
        content = _translate_content(strip_html(reblog.get("content", "")), translator)
        if content:
            add(_esc(content), content)
        media = _extract_media(reblog)
        url = _post_url(reblog)
    else:
        add(f"<b>{_esc(display_name)}</b> (@{_esc(username)})", f"{display_name} (@{username})")
        add("")
        content = _translate_content(strip_html(post.get("content", "")), translator)
        if content:
            add(_esc(content), content)
        media = _extract_media(post)
        url = _post_url(post)

    # Quote post
    if quote:
        quote_account = quote.get("account", {})
        quote_name = quote_account.get("display_name", "")
        quote_user = quote_account.get("username", "")
        quote_content = _translate_content(strip_html(quote.get("content", "")), translator)
        add("")
        add(f"┃ <b>{_esc(quote_name)}</b> (@{_esc(quote_user)})", f"┃ {quote_name} (@{quote_user})")
        if quote_content:
            add(f"┃ {_esc(quote_content)}", f"┃ {quote_content}")
        # Include quote media if main post has none
        if not media:
            media = _extract_media(quote)

    sendable = [m for m in media if m.kind in SENDABLE_MEDIA_KINDS]
    unknown = [m for m in media if m.kind not in SENDABLE_MEDIA_KINDS]

    # Unknown attachments (e.g. audio) can't be sent as photo/video — link them.
    if unknown:
        add("")
        for item in unknown:
            add(f"📎 {_esc(item.url)}", f"📎 {item.url}")

    add("")
    add(f"🔗 {_esc(url)}", f"🔗 {url}")

    return FormattedPost(
        text="\n".join(html_parts),
        plain_text="\n".join(plain_parts),
        media=sendable,
    )
