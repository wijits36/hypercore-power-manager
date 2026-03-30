"""Tests for state persistence."""

import json
from unittest.mock import patch

import pytest

from hypercore_power_manager.state import (
    delete_state,
    load_state,
    save_state,
)


@pytest.fixture
def state_file(tmp_path):
    """Redirect STATE_FILE to a temp directory for testing."""
    path = tmp_path / "state.json"
    with patch("hypercore_power_manager.state.STATE_FILE", path):
        yield path


def test_save_state_writes_json(state_file):
    """save_state writes valid JSON to the state file."""
    data = {
        "timestamp": "2026-03-30T12:00:00+00:00",
        "clusters": {
            "192.168.1.201": [
                {"uuid": "abc-123", "name": "pihole"},
            ],
        },
    }

    result = save_state(data)

    assert result is True
    assert state_file.exists()

    written = json.loads(state_file.read_text())
    assert written["timestamp"] == "2026-03-30T12:00:00+00:00"
    assert len(written["clusters"]["192.168.1.201"]) == 1
    assert written["clusters"]["192.168.1.201"][0]["uuid"] == "abc-123"


def test_save_state_overwrites_existing(state_file):
    """save_state replaces existing file content."""
    save_state({"timestamp": "old", "clusters": {}})
    save_state({"timestamp": "new", "clusters": {}})

    written = json.loads(state_file.read_text())
    assert written["timestamp"] == "new"


def test_save_state_returns_false_on_failure(tmp_path):
    """save_state returns False and logs warning if write fails."""
    # Point STATE_FILE at a path inside a non-existent read-only directory.
    # mkdir will fail because the parent doesn't allow writes.
    bad_path = tmp_path / "readonly" / "state.json"
    (tmp_path / "readonly").mkdir()
    (tmp_path / "readonly").chmod(0o444)

    with patch("hypercore_power_manager.state.STATE_FILE", bad_path):
        result = save_state({"test": True})

    assert result is False

    # Restore permissions so pytest can clean up tmp_path
    (tmp_path / "readonly").chmod(0o755)


def test_load_state_returns_none_when_missing(state_file):
    """load_state returns None if no state file exists."""
    assert not state_file.exists()
    assert load_state() is None


def test_load_state_reads_valid_json(state_file):
    """load_state returns the parsed dict from a valid state file."""
    data = {
        "timestamp": "2026-03-30T12:00:00+00:00",
        "clusters": {
            "192.168.1.201": [{"uuid": "abc-123", "name": "pihole"}],
        },
    }
    state_file.write_text(json.dumps(data))

    result = load_state()

    assert result is not None
    assert result["timestamp"] == "2026-03-30T12:00:00+00:00"
    assert result["clusters"]["192.168.1.201"][0]["name"] == "pihole"


def test_load_state_returns_none_on_corrupted_json(state_file):
    """load_state returns None if the file contains invalid JSON."""
    state_file.write_text("{this is not valid json")

    assert load_state() is None


def test_load_state_returns_none_on_non_dict(state_file):
    """load_state returns None if JSON is valid but not a dict."""
    state_file.write_text(json.dumps([1, 2, 3]))

    assert load_state() is None


def test_delete_state_removes_file(state_file):
    """delete_state removes an existing state file."""
    state_file.write_text("{}")
    assert state_file.exists()

    delete_state()

    assert not state_file.exists()


def test_delete_state_handles_missing_file(state_file):
    """delete_state does nothing if file doesn't exist."""
    assert not state_file.exists()

    # Should not raise
    delete_state()
