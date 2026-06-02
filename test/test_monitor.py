from datetime import datetime, timedelta, timezone

from truthbrush.monitor import TruthMonitor
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
        self.calls = []
        self.yielded = 0

    def pull_statuses(self, username: str, since_id=None):
        self.calls.append({"username": username, "since_id": since_id})
        for post in self.posts:
            self.yielded += 1
            yield post


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
