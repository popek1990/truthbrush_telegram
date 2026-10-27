from datetime import datetime, timedelta, timezone

import pytest

from truthbrush.api import (
    FetchError,
    LoginErrorException,
    RateLimitException,
    TokenInvalidException,
)
from truthbrush.monitor import BACKOFF_SCHEDULE_SECONDS, TruthMonitor
from truthbrush.state import StateManager
from truthbrush.telegram import TelegramSendError


USERNAME = "realDonaldTrump"


def make_post(
    post_id: int,
    content: str | None = None,
    media: list[dict] | None = None,
) -> dict:
    return {
        "id": str(post_id),
        "content": f"<p>{content or f'post {post_id}'}</p>",
        "account": {
            "username": USERNAME,
            "display_name": "Donald J. Trump",
        },
        "media_attachments": media or [],
        "quote": None,
        "reblog": None,
    }


class FakeApi:
    def __init__(self, posts: list[dict]):
        self.posts = posts
        self.auth_id = "auth"
        self.has_password_auth = False
        self.calls = []
        self.yielded = 0

    def pull_statuses(self, username: str, since_id=None, raise_on_error=False):
        self.calls.append({"username": username, "since_id": since_id})
        for post in self.posts:
            self.yielded += 1
            yield post


class AuthFailingApi:
    """Raises ``exc`` from pull_statuses when set, otherwise yields ``posts``."""

    def __init__(self, exc=None, posts=None, has_password_auth: bool = False):
        self.exc = exc
        self.posts = posts or []
        self.auth_id = "token"
        self.has_password_auth = has_password_auth
        self.calls = 0

    def pull_statuses(self, username: str, since_id=None, raise_on_error=False):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        for post in self.posts:
            yield post


class FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None, json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data

    def json(self):
        return self._json


class FakeSession:
    def __init__(self, response: FakeResponse):
        self._response = response

    def get(self, *args, **kwargs):
        return self._response


class FakeSender:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.messages = []  # text of every send_message (incl. captions kept separately)
        self.parse_modes = []  # parse_mode of each send_message call
        self.media_groups = []  # (media_items, caption)
        self.photos = []  # (url, caption)
        self.videos = []  # (url, caption)
        self.animations = []  # (url, caption)
        # Optional queue of exceptions (or None) to raise per send_message call.
        self.message_errors: list = []

    def send_message(self, text: str, parse_mode="HTML") -> dict:
        if self.message_errors:
            exc = self.message_errors.pop(0)
            if exc is not None:
                raise exc
        if self.fail:
            raise TelegramSendError("send failed")
        self.messages.append(text)
        self.parse_modes.append(parse_mode)
        return {"ok": True}

    def send_media_group(self, media, caption: str = "", parse_mode: str = "HTML"):
        if self.fail:
            raise TelegramSendError("send failed")
        self.media_groups.append((media, caption))
        return {"ok": True}

    def send_photo(self, url, caption: str = "", parse_mode: str = "HTML"):
        if self.fail:
            raise TelegramSendError("send failed")
        self.photos.append((url, caption))
        return {"ok": True}

    def send_video(self, url, caption: str = "", parse_mode: str = "HTML"):
        if self.fail:
            raise TelegramSendError("send failed")
        self.videos.append((url, caption))
        return {"ok": True}

    def send_animation(self, url, caption: str = "", parse_mode: str = "HTML"):
        if self.fail:
            raise TelegramSendError("send failed")
        self.animations.append((url, caption))
        return {"ok": True}


class FakeTranslator:
    def __init__(self):
        self.calls = []

    def translate(self, text: str) -> str:
        self.calls.append(text)
        return f"translated: {text}"


def make_state(tmp_path, last_seen_id: str | None = None, last_check: str | None = None):
    state = StateManager(str(tmp_path / "state.json"))
    if last_seen_id is not None:
        state._state[USERNAME] = {"last_seen_id": last_seen_id}
        if last_check is not None:
            state._state[USERNAME]["last_check"] = last_check
        state._save()
    return state


def test_initialize_without_state_sets_latest_without_sending_or_translating(tmp_path):
    api = FakeApi([make_post(105), make_post(104)])
    sender = FakeSender()
    translator = FakeTranslator()
    state = make_state(tmp_path)
    monitor = TruthMonitor(
        USERNAME,
        api,
        sender,
        state,
        translator=translator,
    )

    monitor._initialize()

    assert state.get_last_seen_id(USERNAME) == "105"
    assert sender.messages == []
    assert translator.calls == []
    assert api.calls == [{"username": USERNAME, "since_id": None}]


def test_initialize_with_stale_state_skips_backlog_without_translation(tmp_path):
    old_check = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    api = FakeApi([make_post(205), make_post(204)])
    sender = FakeSender()
    translator = FakeTranslator()
    state = make_state(tmp_path, last_seen_id="100", last_check=old_check)
    monitor = TruthMonitor(
        USERNAME,
        api,
        sender,
        state,
        translator=translator,
        max_backfill_age_seconds=3600,
    )

    monitor._initialize()

    assert state.get_last_seen_id(USERNAME) == "205"
    assert sender.messages == []
    assert translator.calls == []
    assert api.calls == [{"username": USERNAME, "since_id": None}]


def test_initialize_with_invalid_last_check_resets_to_latest(tmp_path):
    api = FakeApi([make_post(305)])
    state = make_state(tmp_path, last_seen_id="100", last_check="not-a-date")
    monitor = TruthMonitor(USERNAME, api, FakeSender(), state)

    monitor._initialize()

    assert state.get_last_seen_id(USERNAME) == "305"
    assert api.calls == [{"username": USERNAME, "since_id": None}]


def test_poll_with_fresh_state_sends_new_posts_oldest_first(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    api = FakeApi([make_post(102), make_post(101)])
    sender = FakeSender()
    translator = FakeTranslator()
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(
        USERNAME,
        api,
        sender,
        state,
        translator=translator,
    )

    monitor._poll()

    assert len(sender.messages) == 2
    assert "translated: post 101" in sender.messages[0]
    assert "translated: post 102" in sender.messages[1]
    assert state.get_last_seen_id(USERNAME) == "102"
    assert translator.calls == ["post 101", "post 102"]
    assert api.calls == [{"username": USERNAME, "since_id": "100"}]


def test_poll_skips_large_batch_before_translation_or_send(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    posts = [make_post(post_id) for post_id in range(130, 99, -1)]
    api = FakeApi(posts)
    sender = FakeSender()
    translator = FakeTranslator()
    state = make_state(tmp_path, last_seen_id="99", last_check=now)
    monitor = TruthMonitor(
        USERNAME,
        api,
        sender,
        state,
        translator=translator,
        max_posts_per_poll=20,
    )

    monitor._poll()

    assert state.get_last_seen_id(USERNAME) == "130"
    assert sender.messages == []
    assert translator.calls == []
    assert api.yielded == 21


def test_failed_telegram_send_does_not_advance_last_seen_id(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    api = FakeApi([make_post(101)])
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(USERNAME, api, FakeSender(fail=True), state)

    monitor._poll()

    assert state.get_last_seen_id(USERNAME) == "100"


def test_backoff_grows_and_caps_at_thirty_minutes(tmp_path):
    monitor = TruthMonitor(USERNAME, FakeApi([]), FakeSender(), make_state(tmp_path))

    delays = []
    for failures in range(1, len(BACKOFF_SCHEDULE_SECONDS) + 3):
        monitor._auth_failures = failures
        delays.append(monitor._backoff_seconds())

    assert delays[: len(BACKOFF_SCHEDULE_SECONDS)] == BACKOFF_SCHEDULE_SECONDS
    # Beyond the schedule, the delay stays capped at the last (30 min) value.
    assert delays[len(BACKOFF_SCHEDULE_SECONDS):] == [30 * 60, 30 * 60]
    assert max(delays) == 30 * 60


def test_next_delay_respects_retry_after(tmp_path):
    monitor = TruthMonitor(USERNAME, FakeApi([]), FakeSender(), make_state(tmp_path))
    monitor._auth_failures = 1  # backoff = 60s

    monitor._retry_after = 200
    assert monitor._next_delay() == 200  # max(retry_after, backoff)

    monitor._retry_after = 10
    assert monitor._next_delay() == 60  # backoff wins when it is larger


def test_success_resets_failure_counter(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    api = AuthFailingApi(exc=TokenInvalidException("bad token"), posts=[make_post(101)])
    sender = FakeSender()
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(USERNAME, api, sender, state)

    monitor._execute(monitor._poll)
    assert monitor._auth_failures == 1

    api.exc = None  # next poll succeeds
    monitor._execute(monitor._poll)

    assert monitor._auth_failures == 0
    assert len(sender.messages) == 1
    assert state.get_last_seen_id(USERNAME) == "101"


def test_token_only_auth_failure_keeps_token_and_skips_login(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    api = AuthFailingApi(
        exc=TokenInvalidException("HTTP 401"), has_password_auth=False
    )
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(USERNAME, api, FakeSender(), state)

    monitor._execute(monitor._poll)

    assert monitor._auth_failures == 1
    # Token-only mode must not clear the token (there is nothing to log in with).
    assert monitor.api.auth_id == "token"


def test_password_mode_auth_failure_clears_token_for_relogin(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    api = AuthFailingApi(exc=TokenInvalidException("HTTP 401"), has_password_auth=True)
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(USERNAME, api, FakeSender(), state)

    monitor._execute(monitor._poll)

    assert monitor._auth_failures == 1
    # Password mode clears the token so the next (backed-off) poll re-logs in.
    assert monitor.api.auth_id is None


def test_pause_after_max_failures_sends_single_alert_and_stops(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    api = AuthFailingApi(exc=TokenInvalidException("HTTP 401"), has_password_auth=False)
    sender = FakeSender()
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(USERNAME, api, sender, state, max_auth_failures=3)

    # Make waits instant and make the idle (paused) loop terminate the run.
    monkeypatch.setattr(monitor, "_interruptible_sleep", lambda seconds: None)
    monkeypatch.setattr(
        monitor, "_idle_until_shutdown", lambda: setattr(monitor, "_running", False)
    )

    monitor.run()

    assert monitor._paused is True
    assert monitor._auth_failures == 3
    # _initialize resumes without an API call; exactly max_auth_failures polls run.
    assert api.calls == 3
    # Exactly one alert, and no post messages.
    assert len(sender.messages) == 1
    assert "Bot wstrzymany." in sender.messages[0]
    assert "(3 prób)" in sender.messages[0]


def test_pause_alert_suppressed_in_dry_run(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    api = AuthFailingApi(exc=TokenInvalidException("HTTP 401"))
    sender = FakeSender()
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(
        USERNAME, api, sender, state, dry_run=True, max_auth_failures=2
    )

    for _ in range(monitor.max_auth_failures):
        monitor._execute(monitor._poll)

    assert monitor._paused is True
    assert sender.messages == []  # no alert while dry_run


def test_rate_limit_failure_does_not_clear_token(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    api = AuthFailingApi(
        exc=RateLimitException("429", retry_after=120), has_password_auth=True
    )
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(USERNAME, api, FakeSender(), state)

    monitor._execute(monitor._poll)

    assert monitor._auth_failures == 1
    assert monitor._retry_after == 120
    # A 429 is not an auth problem — keep the token even in password mode.
    assert monitor.api.auth_id == "token"


def test_get_raises_rate_limit_exception_on_429(monkeypatch):
    from truthbrush.api import Api

    api = Api(token="token")
    response = FakeResponse(429, headers={"Retry-After": "42"})
    monkeypatch.setattr(api, "_make_session", lambda: FakeSession(response))

    with pytest.raises(RateLimitException) as exc_info:
        api._get("/v1/accounts/lookup")

    assert exc_info.value.retry_after == 42


def test_get_raises_token_invalid_on_401(monkeypatch):
    from truthbrush.api import Api

    api = Api(token="token")
    response = FakeResponse(401)
    monkeypatch.setattr(api, "_make_session", lambda: FakeSession(response))

    with pytest.raises(LoginErrorException):
        api._get("/v1/accounts/lookup")


# ----------------------------------------------------------------------
# Delivery resilience: bad posts, channel errors, retries, media
# ----------------------------------------------------------------------


def test_post_rejected_three_times_falls_back_then_queue_continues(tmp_path):
    """A post rejected 3x gets a plain-text fallback; later posts still go out."""
    now = datetime.now(timezone.utc).isoformat()
    api = FakeApi([make_post(102), make_post(101)])  # 101 is bad, 102 is good
    sender = FakeSender()
    parse_err = TelegramSendError(
        "parse", code=400, description="Bad Request: can't parse entities"
    )
    # First three HTML text sends (post 101, three cycles) fail; the plain-text
    # fallback and post 102 then succeed.
    sender.message_errors = [parse_err, parse_err, parse_err]
    translator = FakeTranslator()
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(USERNAME, api, sender, state, translator=translator)

    monitor._poll()  # attempt 1 → fail, stop before post 102
    assert state.get_last_seen_id(USERNAME) == "100"
    monitor._poll()  # attempt 2 → fail
    assert state.get_last_seen_id(USERNAME) == "100"
    monitor._poll()  # attempt 3 → plain fallback for 101, then send 102

    # Post 101 given up on, 102 delivered → queue advanced to the newest.
    assert state.get_last_seen_id(USERNAME) == "102"
    # The plain-text fallback was sent with no HTML parse mode.
    assert None in sender.parse_modes
    fallback = sender.messages[sender.parse_modes.index(None)]
    assert "translated: post 101" in fallback
    # Each post translated exactly once despite the retries (cache).
    assert translator.calls == ["post 101", "post 102"]


def test_channel_level_error_does_not_advance_or_count_attempts(tmp_path):
    """A channel-wide error (chat not found) must not skip the post."""
    now = datetime.now(timezone.utc).isoformat()
    api = FakeApi([make_post(101)])
    sender = FakeSender()
    sender.message_errors = [
        TelegramSendError("chat", code=400, description="Bad Request: chat not found")
    ]
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(USERNAME, api, sender, state)

    monitor._poll()

    # Not advanced, and not counted against the post's 3-attempt budget.
    assert state.get_last_seen_id(USERNAME) == "100"
    assert monitor._send_progress["101"]["attempts"] == 0


def test_retry_does_not_retranslate(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    api = FakeApi([make_post(101)])
    translator = FakeTranslator()
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(
        USERNAME, api, FakeSender(fail=True), state, translator=translator
    )

    monitor._poll()  # formats + translates, send fails
    monitor._poll()  # retries from cache, must not translate again

    assert translator.calls == ["post 101"]
    assert state.get_last_seen_id(USERNAME) == "100"


def test_fetch_error_does_not_update_last_check_or_reset(tmp_path):
    api = AuthFailingApi(exc=FetchError("cloudflare 503"))
    state = make_state(tmp_path, last_seen_id="100")
    monitor = TruthMonitor(USERNAME, api, FakeSender(), state)

    monitor._execute(monitor._poll)

    assert monitor._fetch_failures == 1
    assert monitor._auth_failures == 0  # not an auth failure → no pause path
    assert not monitor._paused
    # A failed fetch must not look like a successful poll.
    assert state.get_last_check(USERNAME) is None


def test_no_success_alert_sent_once_and_recovery_once(tmp_path):
    api = AuthFailingApi(exc=FetchError("down"), posts=[])
    sender = FakeSender()
    state = make_state(tmp_path)
    monitor = TruthMonitor(
        USERNAME, api, sender, state, alert_after_no_success=10
    )
    monitor._last_fetch_success = datetime.now(timezone.utc) - timedelta(seconds=100)

    monitor._execute(monitor._poll)  # gap > 10s → one alert
    assert len(sender.messages) == 1
    assert "nie udaje się pobrać" in sender.messages[0]

    monitor._execute(monitor._poll)  # still failing → no second alert
    assert len(sender.messages) == 1

    api.exc = None  # fetch recovers (yields nothing, but succeeds)
    monitor._execute(monitor._poll)
    assert len(sender.messages) == 2
    assert "znów działa" in sender.messages[1]
    assert monitor._no_success_alerted is False


def test_long_post_with_media_sends_media_then_text_without_duplicate(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    long_text = "x" * 1100  # longer than a 1024-char caption
    post = make_post(101, content=long_text, media=[{"type": "image", "url": "http://img/1.jpg"}])
    api = FakeApi([post])
    sender = FakeSender()
    # The first text send fails (media already went out) → partial progress.
    sender.message_errors = [
        TelegramSendError("flaky", code=400, description="Bad Request: message is too long")
    ]
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(USERNAME, api, sender, state)

    monitor._poll()  # photo sent, text fails → not advanced
    assert state.get_last_seen_id(USERNAME) == "100"
    assert len(sender.photos) == 1

    monitor._poll()  # retry: text only, photo NOT resent
    assert state.get_last_seen_id(USERNAME) == "101"
    assert len(sender.photos) == 1  # no duplicate media
    assert any("x" * 1100 in m for m in sender.messages)


def test_video_is_sent_as_video_not_photo(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    post = make_post(101, content="short", media=[{"type": "video", "url": "http://v/1.mp4"}])
    api = FakeApi([post])
    sender = FakeSender()
    state = make_state(tmp_path, last_seen_id="100", last_check=now)
    monitor = TruthMonitor(USERNAME, api, sender, state)

    monitor._poll()

    assert len(sender.videos) == 1
    assert sender.videos[0][0] == "http://v/1.mp4"
    assert sender.photos == []
    assert state.get_last_seen_id(USERNAME) == "101"
