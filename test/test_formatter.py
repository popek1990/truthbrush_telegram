from truthbrush.formatter import (
    MEDIA_GIFV,
    MEDIA_IMAGE,
    MEDIA_VIDEO,
    format_post,
)


def _post(
    content, media=None, display_name="Donald J. Trump", username="realDonaldTrump"
):
    return {
        "id": "1",
        "content": content,
        "account": {"username": username, "display_name": display_name},
        "media_attachments": media or [],
        "quote": None,
        "reblog": None,
    }


def test_special_chars_are_escaped_in_html_but_raw_in_plain():
    post = _post(
        "<p>5 &lt; 10 &amp; AT&amp;T &gt; 3</p>",
        display_name="A&B <Corp>",
    )
    fp = format_post(post)

    # HTML body: dynamic content and name are escaped; our <b> tag is intact.
    assert "&lt;" in fp.text and "&amp;" in fp.text and "&gt;" in fp.text
    assert "<b>" in fp.text
    # No raw special characters that would break Telegram's HTML parser.
    assert "<Corp>" not in fp.text
    assert "5 < 10" not in fp.text
    assert "AT&T" not in fp.text

    # Plain fallback keeps the human-readable raw characters.
    assert "A&B <Corp>" in fp.plain_text
    assert "5 < 10 & AT&T > 3" in fp.plain_text


def test_media_types_are_preserved_and_unknown_linked_in_text():
    post = _post(
        "<p>hi</p>",
        media=[
            {"type": "image", "url": "http://i/1.jpg"},
            {"type": "video", "url": "http://v/1.mp4"},
            {"type": "gifv", "url": "http://g/1.mp4"},
            {"type": "audio", "url": "http://a/1.mp3"},  # unsupported → linked
        ],
    )
    fp = format_post(post)

    kinds = [m.kind for m in fp.media]
    assert kinds == [MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_GIFV]  # audio excluded
    # The unsupported attachment is linked in the body instead of dropped.
    assert "http://a/1.mp3" in fp.text
    assert "http://a/1.mp3" in fp.plain_text


def test_post_with_no_special_chars_is_unchanged_round_trip():
    fp = format_post(_post("<p>Just a normal post</p>"))
    assert "Just a normal post" in fp.text
    assert "Just a normal post" in fp.plain_text
