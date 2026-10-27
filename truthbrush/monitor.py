"""Truth Social monitor — polls for new posts and forwards them to Telegram."""

import signal
from datetime import datetime, timedelta, timezone
from time import sleep
from typing import Callable

from loguru import logger

from truthbrush.api import Api, LoginErrorException, RateLimitException
from truthbrush.formatter import format_post
from truthbrush.state import StateManager
from truthbrush.telegram import TelegramSender, TelegramSendError
from truthbrush.translator import PostTranslator  # noqa: F401 — used via constructor

DEFAULT_MAX_BACKFILL_AGE_SECONDS = 6 * 60 * 60
DEFAULT_MAX_POSTS_PER_POLL = 20
INITIAL_POST_FETCH_LIMIT = 5

DEFAULT_MAX_AUTH_FAILURES = 5
# Rising delay (seconds) applied after each consecutive auth/rate-limit failure:
# 1, 2, 4, 8, 16, 30 minutes. The last value is the cap.
BACKOFF_SCHEDULE_SECONDS = [60, 120, 240, 480, 960, 1800]
# How often the liveness heartbeat is refreshed while sleeping, so the Docker
# healthcheck sees the process is alive even during a long backoff or idle wait.
HEARTBEAT_INTERVAL_SECONDS = 60
# How often the idle (paused) loop logs that it is still waiting for a restart.
IDLE_LOG_INTERVAL_SECONDS = 60 * 60


class TruthMonitor:
    """Monitors a Truth Social user and sends new posts to Telegram."""

    def __init__(
        self,
        username: str,
        api: Api,
        sender: TelegramSender | None,
        state: StateManager,
        interval: int = 60,
        dry_run: bool = False,
        translator: PostTranslator | None = None,
        max_backfill_age_seconds: int | None = DEFAULT_MAX_BACKFILL_AGE_SECONDS,
        max_posts_per_poll: int | None = DEFAULT_MAX_POSTS_PER_POLL,
        max_auth_failures: int = DEFAULT_MAX_AUTH_FAILURES,
    ):
        self.username = username
        self.api = api
        self.sender = sender
        self.state = state
        self.interval = interval
        self.dry_run = dry_run
        self.translator = translator
        self.max_backfill_age_seconds = max_backfill_age_seconds
        self.max_posts_per_poll = max_posts_per_poll
        self.max_auth_failures = max_auth_failures
        self._running = True
        # Consecutive auth/rate-limit failures; reset to 0 on any clean cycle.
        self._auth_failures = 0
        # Retry delay (seconds) suggested by the last 429 response, if any.
        self._retry_after: int | None = None
        # Set once the hard failure limit is hit: polling stops, we idle.
        self._paused = False

    def _handle_signal(self, signum: int, frame) -> None:
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, shutting down gracefully...")
        self._running = False

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _backfill_protection_enabled(self) -> bool:
        return (
            self.max_backfill_age_seconds is not None
            and self.max_backfill_age_seconds > 0
        )

    def _max_posts_protection_enabled(self) -> bool:
        return self.max_posts_per_poll is not None and self.max_posts_per_poll > 0

    def _format_age(self, age: timedelta) -> str:
        seconds = max(0, int(age.total_seconds()))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds or not parts:
            parts.append(f"{seconds}s")
        return " ".join(parts)

    def _stale_state_reason(self) -> str | None:
        if not self._backfill_protection_enabled():
            return None

        last_check = self.state.get_last_check_datetime(self.username)
        if last_check is None:
            return "missing or invalid last_check"

        age = datetime.now(timezone.utc) - last_check
        max_age = timedelta(seconds=self.max_backfill_age_seconds)
        if age > max_age:
            return (
                f"last_check is {self._format_age(age)} old "
                f"(limit {self._format_age(max_age)})"
            )

        return None

    def _fetch_latest_posts(self, limit: int = INITIAL_POST_FETCH_LIMIT) -> list[dict]:
        posts = []
        for post in self.api.pull_statuses(self.username):
            posts.append(post)
            if len(posts) >= limit:
                break
        return posts

    def _newest_post_id(self, posts: list[dict]) -> str:
        return max(posts, key=lambda p: int(p["id"]))["id"]

    def _reset_to_latest(self, reason: str) -> None:
        """Move state to the latest post without sending historical posts."""
        logger.warning(f"Resetting @{self.username} to latest post: {reason}")
        try:
            posts = self._fetch_latest_posts()
        except (LoginErrorException, RateLimitException):
            # Let the run loop account for the failure and apply backoff.
            raise
        except Exception as e:
            logger.error(f"Failed to fetch latest post for reset: {e}")
            return

        if posts:
            newest_id = self._newest_post_id(posts)
            self.state.save_last_seen_id(self.username, newest_id)
            logger.warning(
                f"Skipped backlog for @{self.username}; monitoring new posts after ID {newest_id}"
            )
        else:
            self.state.update_last_check(self.username)
            logger.warning(f"No posts found for @{self.username} during reset")

    def _collect_new_posts(self, last_seen_id: str | None) -> tuple[list[dict], bool]:
        posts = []
        overflow = False
        for post in self.api.pull_statuses(self.username, since_id=last_seen_id):
            posts.append(post)
            if (
                self._max_posts_protection_enabled()
                and len(posts) > self.max_posts_per_poll
            ):
                overflow = True
                break
        return posts, overflow

    def _skip_large_batch(self, posts: list[dict]) -> None:
        newest_id = self._newest_post_id(posts)
        self.state.save_last_seen_id(self.username, newest_id)
        logger.warning(
            f"Skipped {len(posts)} posts for @{self.username}; "
            f"batch exceeded max_posts_per_poll={self.max_posts_per_poll}. "
            f"Monitoring new posts after ID {newest_id}"
        )

    def _initialize(self) -> None:
        """Initialize state and avoid replaying stale history."""
        last_seen_id = self.state.get_last_seen_id(self.username)
        if last_seen_id is None:
            self._reset_to_latest("first run")
            return

        stale_reason = self._stale_state_reason()
        if stale_reason:
            self._reset_to_latest(f"stale state ({stale_reason})")
            return

        logger.info(f"Resuming from post ID {last_seen_id}")

    def _poll(self) -> None:
        """Single poll cycle: fetch new posts, format, and send."""
        last_seen_id = self.state.get_last_seen_id(self.username)

        try:
            posts, overflow = self._collect_new_posts(last_seen_id)
        except (LoginErrorException, RateLimitException):
            # Let the run loop account for the failure and apply backoff.
            raise
        except Exception as e:
            logger.error(f"Error fetching posts: {e}")
            return

        self.state.update_last_check(self.username)

        if not posts:
            logger.debug(f"No new posts for @{self.username}")
            return

        if overflow:
            self._skip_large_batch(posts)
            return

        # Sort chronologically (oldest first) so Telegram receives them in order
        posts.sort(key=lambda p: int(p["id"]))
        logger.info(f"Found {len(posts)} new post(s) for @{self.username}")

        for post in posts:
            formatted = format_post(post, translator=self.translator)

            if self.dry_run:
                logger.info(f"[DRY RUN] Post {post['id']}:\n{formatted.text}")
                self.state.save_last_seen_id(self.username, post["id"])
                continue

            try:
                sent = False
                if formatted.has_media:
                    try:
                        self.sender.send_media_group(
                            formatted.media_urls,
                            caption=formatted.text,
                        )
                        sent = True
                    except TelegramSendError as e:
                        err = str(e).lower()
                        if "wrong type" in err or "failed to get http url" in err:
                            # Media type not supported (e.g. video URL) — fallback to text
                            logger.warning(f"Media send failed for post {post['id']}, falling back to text: {e}")
                        else:
                            raise

                if not sent:
                    self.sender.send_message(formatted.text)

                self.state.save_last_seen_id(self.username, post["id"])
                logger.info(f"Sent post {post['id']} to Telegram")

            except TelegramSendError as e:
                logger.error(f"Failed to send post {post['id']} to Telegram: {e}")
                # Don't update state — will retry this post next cycle
                break

    # ------------------------------------------------------------------
    # Backoff, hard limit and alerting
    # ------------------------------------------------------------------

    def _backoff_seconds(self) -> int:
        """Current backoff delay based on the consecutive failure count."""
        index = min(self._auth_failures - 1, len(BACKOFF_SCHEDULE_SECONDS) - 1)
        index = max(index, 0)
        return BACKOFF_SCHEDULE_SECONDS[index]

    def _next_delay(self) -> int:
        """Seconds to wait before the next poll while recovering from a failure."""
        backoff = self._backoff_seconds()
        if self._retry_after:
            return max(self._retry_after, backoff)
        return backoff

    def _on_success(self) -> None:
        """Clear the failure state after a clean cycle."""
        if self._auth_failures:
            logger.info("Auth/rate-limit recovered; resetting failure counter")
        self._auth_failures = 0
        self._retry_after = None

    def _on_auth_failure(self, exc: Exception) -> None:
        """Record an auth/rate-limit failure and decide how to recover."""
        self._auth_failures += 1
        self._retry_after = getattr(exc, "retry_after", None)
        attempt = f"{self._auth_failures}/{self.max_auth_failures}"

        if isinstance(exc, RateLimitException):
            logger.warning(
                f"Rate limited (failure {attempt}); "
                f"backing off (retry_after={self._retry_after})"
            )
        elif self.api.has_password_auth:
            # Password mode: allow a re-login, but only on the next (backed-off)
            # poll — never more often than the backoff schedule permits.
            logger.warning(
                f"Auth failed (failure {attempt}); "
                "will re-authenticate with password after backoff"
            )
            self.api.auth_id = None
        else:
            # Token-only mode: there is nothing to log in with, so keep the
            # (invalid) token in place and tell the operator to replace it.
            logger.error(
                f"Auth failed (failure {attempt}): token is invalid. "
                "Paste a fresh TRUTHSOCIAL_TOKEN into .env and restart the bot."
            )

        if self._auth_failures >= self.max_auth_failures and not self._paused:
            self._enter_paused_state()

    def _enter_paused_state(self) -> None:
        """Stop polling after too many failures and alert the Telegram channel."""
        self._paused = True
        logger.error(
            f"Reached {self.max_auth_failures} consecutive auth failures; "
            "pausing Truth Social polling until restart."
        )
        self._send_pause_alert()

    def _send_pause_alert(self) -> None:
        """Send a single alert to Telegram that the monitor has stopped."""
        if self.dry_run or self.sender is None:
            return

        message = (
            "⚠️ Monitor Truth Social: logowanie nie działa "
            f"({self.max_auth_failures} prób). Prawdopodobnie wygasł token "
            "lub konto wymaga weryfikacji. Bot wstrzymany."
        )
        try:
            self.sender.send_message(message)
        except Exception as e:
            # A failed alert must never take the monitor down.
            logger.error(f"Failed to send pause alert to Telegram: {e}")

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    def _execute(self, step: Callable[[], None]) -> None:
        """Run a poll/init step, translating auth & rate errors into backoff."""
        try:
            step()
        except (LoginErrorException, RateLimitException) as e:
            self._on_auth_failure(e)
        else:
            self._on_success()

    def _update_heartbeat(self) -> None:
        """Refresh the liveness marker read by the Docker healthcheck."""
        try:
            self.state.update_heartbeat()
        except Exception as e:  # pragma: no cover — defensive, never fatal
            logger.debug(f"Failed to update heartbeat: {e}")

    def _interruptible_sleep(self, seconds: int) -> None:
        """Sleep in 1s steps (SIGTERM-interruptible), refreshing the heartbeat."""
        for elapsed in range(int(seconds)):
            if not self._running:
                break
            if elapsed % HEARTBEAT_INTERVAL_SECONDS == 0:
                self._update_heartbeat()
            sleep(1)

    def _idle_until_shutdown(self) -> None:
        """Wait idly (no API calls) until SIGTERM once polling is paused."""
        logger.warning(
            "Monitor paused; idling until shutdown. "
            "Fix the token/credentials and restart the container."
        )
        while self._running:
            self._interruptible_sleep(IDLE_LOG_INTERVAL_SECONDS)
            if self._running:
                logger.info("Monitor still paused; waiting for a restart...")

    def run(self) -> None:
        """Main loop: initialize, then poll on interval until shutdown."""
        self._setup_signals()
        logger.info(
            f"Starting monitor for @{self.username} "
            f"(interval={self.interval}s, dry_run={self.dry_run})"
        )
        self._update_heartbeat()

        self._execute(self._initialize)

        # The first poll after a clean init runs immediately; after any failure
        # (including during init) we wait out the backoff before retrying.
        awaiting_first_poll = True
        while self._running:
            if self._paused:
                self._idle_until_shutdown()
                break

            if self._auth_failures > 0:
                self._interruptible_sleep(self._next_delay())
            elif not awaiting_first_poll:
                self._interruptible_sleep(self.interval)
            awaiting_first_poll = False

            if not self._running or self._paused:
                continue

            self._execute(self._poll)

        logger.info("Monitor stopped")
