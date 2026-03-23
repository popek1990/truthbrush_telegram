"""Truth Social monitor — polls for new posts and forwards them to Telegram."""

import signal
import sys
from time import sleep

from loguru import logger

from truthbrush.api import Api, LoginErrorException
from truthbrush.formatter import format_post
from truthbrush.state import StateManager
from truthbrush.telegram import TelegramSender, TelegramSendError


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
    ):
        self.username = username
        self.api = api
        self.sender = sender
        self.state = state
        self.interval = interval
        self.dry_run = dry_run
        self._running = True

    def _handle_signal(self, signum: int, frame) -> None:
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, shutting down gracefully...")
        self._running = False

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _initialize(self) -> None:
        """First run: save the latest post ID without sending to Telegram."""
        last_seen_id = self.state.get_last_seen_id(self.username)
        if last_seen_id is not None:
            logger.info(f"Resuming from post ID {last_seen_id}")
            return

        logger.info(f"First run for @{self.username}, fetching latest post ID...")
        try:
            # Only grab the first few posts (one API page), not the entire history
            posts = []
            for post in self.api.pull_statuses(self.username):
                posts.append(post)
                if len(posts) >= 5:
                    break
        except Exception as e:
            logger.error(f"Failed to fetch initial posts: {e}")
            return

        if posts:
            newest_id = max(posts, key=lambda p: int(p["id"]))["id"]
            self.state.save_last_seen_id(self.username, newest_id)
            logger.info(f"Initialized: will monitor new posts after ID {newest_id}")
        else:
            logger.warning(f"No posts found for @{self.username}")

    def _poll(self) -> None:
        """Single poll cycle: fetch new posts, format, and send."""
        last_seen_id = self.state.get_last_seen_id(self.username)

        try:
            posts = list(self.api.pull_statuses(self.username, since_id=last_seen_id))
        except LoginErrorException:
            logger.warning("Auth failed, forcing re-authentication...")
            self.api.auth_id = None
            return
        except Exception as e:
            logger.error(f"Error fetching posts: {e}")
            return

        self.state.update_last_check(self.username)

        if not posts:
            logger.debug(f"No new posts for @{self.username}")
            return

        # Sort chronologically (oldest first) so Telegram receives them in order
        posts.sort(key=lambda p: int(p["id"]))
        logger.info(f"Found {len(posts)} new post(s) for @{self.username}")

        for post in posts:
            formatted = format_post(post)

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
                        if "wrong type" in str(e).lower():
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

    def run(self) -> None:
        """Main loop: initialize, then poll on interval until shutdown."""
        self._setup_signals()
        logger.info(f"Starting monitor for @{self.username} (interval={self.interval}s, dry_run={self.dry_run})")

        self._initialize()

        while self._running:
            self._poll()

            # Interruptible sleep
            for _ in range(self.interval):
                if not self._running:
                    break
                sleep(1)

        logger.info("Monitor stopped")
