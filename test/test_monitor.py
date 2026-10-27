from datetime import datetime, timedelta, timezone

import pytest

from truthbrush.api import LoginErrorException, RateLimitException, TokenInvalidException
from truthbrush.monitor import BACKOFF_SCHEDULE_SECONDS, TruthMonitor
from truthbrush.state import StateManager
from truthbrush.telegram import TelegramSendError


USERNAME = "realDonaldTrump"


def make_post(post_id: int, content: str | None = None) -> dict:
    return {
        "id": str(post_id),
        "content": f"<p>{content or f'post {post_id}'}</p>",
        "account": {
            "username": USERNAME,
            "display_name": "Donald J. Trump",
        },
        "media_attachments": [],
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

    def pull_statuses(self, username: str, since_id=None):
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

    def pull_statuses(self, username: str, since_id=None):
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
        self.messages = []
        self.media_groups = []

    def send_message(self, text: str, parse_mode: str = "HTML") -> dict:
        if self.fail:
            raise TelegramSendError("send failed")
        self.messages.append(text)
        return {"ok": True}

    def send_media_group(self, media_urls, caption: str = "", parse_mode: str = "HTML"):
        if self.fail:
            raise TelegramSendError("send failed")
        self.media_groups.append((media_urls, caption))
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
