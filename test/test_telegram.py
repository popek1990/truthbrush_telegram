import re

from truthbrush.formatter import MEDIA_IMAGE, MEDIA_VIDEO, MediaItem
from truthbrush.telegram import (
    MAX_CAPTION_LENGTH,
    TelegramSender,
    TelegramSendError,
    utf16_len,
)


def test_truncate_counts_utf16_units_not_python_chars():
    # 1000 emoji: 1000 Python chars, 2000 UTF-16 units.
    text = "😀" * 1000
    assert len(text) == 1000
    assert utf16_len(text) == 2000

    out = TelegramSender._truncate(text, 1500)
    # Must actually truncate (UTF-16 length exceeded the limit) and stay within it.
    assert utf16_len(out) <= 1500
    assert out.endswith("...")
    # A short string under the limit is returned unchanged.
    assert TelegramSender._truncate("hello", 4096) == "hello"


def test_truncate_does_not_cut_inside_an_entity():
    text = "a" * 20 + "&amp;" * 200  # lots of HTML entities
    for limit in range(30, 120):
        out = TelegramSender._truncate(text, limit)
        body = out[:-3] if out.endswith("...") else out
        # No dangling partial entity like "&am" or a bare "&" at the end.
        assert not re.search(r"&[#0-9A-Za-z]*$", body), (limit, repr(out[-8:]))


def test_truncate_does_not_leave_a_partial_tag():
    text = "hi " + "<b>bold</b> " * 100
    out = TelegramSender._truncate(text, 40)
    body = out[:-3] if out.endswith("...") else out
    assert not re.search(r"<[^>]*$", body)


def test_error_classification_channel_vs_media_vs_post():
    assert TelegramSendError("x", code=403).is_channel_level()
    assert TelegramSendError("x", code=401).is_channel_level()
    assert TelegramSendError(
        "x", code=400, description="Bad Request: chat not found"
    ).is_channel_level()
    assert TelegramSendError(
        "x", code=403, description="Forbidden: bot was kicked from the channel chat"
    ).is_channel_level()

    parse = TelegramSendError(
        "x", code=400, description="Bad Request: can't parse entities"
    )
    assert not parse.is_channel_level()
    assert not parse.is_media_level()

    media = TelegramSendError(
        "x", code=400, description="Bad Request: failed to get HTTP URL content"
    )
    assert media.is_media_level()
    assert not media.is_channel_level()


def test_media_group_maps_image_and_video_types():
    sender = TelegramSender("bot-token", "@chan")
    captured = {}
    sender._request = lambda method, payload: captured.update(
        method=method, payload=payload
    ) or {"ok": True}

    sender.send_media_group(
        [
            MediaItem("http://i/1.jpg", MEDIA_IMAGE),
            MediaItem("http://v/1.mp4", MEDIA_VIDEO),
        ],
        caption="cap",
    )

    assert captured["method"] == "sendMediaGroup"
    media = captured["payload"]["media"]
    assert media[0]["type"] == "photo"
    assert media[1]["type"] == "video"
    assert media[0]["caption"] == "cap"  # caption on the first item only
    assert "caption" not in media[1]


def test_caption_fits_uses_utf16():
    assert TelegramSender.caption_fits("a" * MAX_CAPTION_LENGTH)
    assert not TelegramSender.caption_fits("a" * (MAX_CAPTION_LENGTH + 1))
    # Emoji count double, so half the cap + 1 of them overflows.
    assert not TelegramSender.caption_fits("😀" * (MAX_CAPTION_LENGTH // 2 + 1))
