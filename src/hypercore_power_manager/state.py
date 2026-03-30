"""Persistent state for crash recovery during power events."""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path("/var/lib/hypercore-power-manager/state.json")


def save_state(data: dict) -> bool:
    """Atomically write state to disk. Returns True on success."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(
            dir=STATE_FILE.parent,
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, STATE_FILE)
            return True
        except Exception:
            # Clean up temp file if rename didn't happen
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    except Exception as e:
        logger.warning("Failed to write state file: %s", e)
        return False


def load_state() -> dict | None:
    """Load state from disk. Returns None if missing or corrupted."""
    if not STATE_FILE.exists():
        return None

    try:
        with open(STATE_FILE) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            logger.warning("State file has unexpected format, ignoring")
            return None

        return data

    except json.JSONDecodeError as e:
        logger.warning("State file is corrupted, ignoring: %s", e)
        return None
    except Exception as e:
        logger.warning("Failed to read state file: %s", e)
        return None


def delete_state() -> None:
    """Remove the state file after successful recovery."""
    try:
        STATE_FILE.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("Failed to delete state file: %s", e)
