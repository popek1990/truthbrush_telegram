"""Persistent state manager for the Truth Social monitor."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

DEFAULT_STATE_FILE = os.path.expanduser("~/.truthbrush_state.json")


class StateManager:
    """Manages last_seen_id persistence using an atomic JSON file."""

    def __init__(self, state_file: str = DEFAULT_STATE_FILE):
        self.state_file = Path(state_file)
        self._state: dict = self._load()

    def _load(self) -> dict:
        """Load state from disk. Returns empty dict if file doesn't exist."""
        if not self.state_file.exists():
            logger.info(f"No state file found at {self.state_file}, starting fresh")
            return {}
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
                logger.info(f"Loaded state from {self.state_file}")
                return data
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load state from {self.state_file}: {e}")
            return {}

    def _save(self) -> None:
        """Atomically write state to disk (write tmp → os.replace)."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=self.state_file.parent,
                prefix=".truthbrush_state_",
                suffix=".tmp",
            )
            with os.fdopen(fd, "w") as f:
                json.dump(self._state, f, indent=2)
            os.replace(tmp_path, self.state_file)
        except OSError as e:
            logger.error(f"Failed to save state: {e}")
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def get_last_seen_id(self, username: str) -> Optional[str]:
        """Get the last seen post ID for a username."""
        entry = self._state.get(username)
        if entry is None:
            return None
        return entry.get("last_seen_id")

    def save_last_seen_id(self, username: str, post_id: str) -> None:
        """Update and persist the last seen post ID for a username."""
        self._state[username] = {
            "last_seen_id": post_id,
            "last_check": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        logger.debug(f"State saved: {username} → {post_id}")

    def update_last_check(self, username: str) -> None:
        """Update the last_check timestamp without changing last_seen_id."""
        if username not in self._state:
            self._state[username] = {}
        self._state[username]["last_check"] = datetime.now(timezone.utc).isoformat()
        self._save()

    def get_last_check(self, username: str) -> Optional[str]:
        """Get the last check timestamp for healthcheck purposes."""
        entry = self._state.get(username)
        if entry is None:
            return None
        return entry.get("last_check")
