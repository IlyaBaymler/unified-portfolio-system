from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trading_robot import state_persistence
from trading_robot.state_persistence import (
    StatePersistenceError,
    atomic_write_json,
)


def test_atomic_write_retries_transient_windows_style_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    destination = tmp_path / "robot_state.json"
    real_replace = os.replace
    calls: list[tuple[str, str]] = []
    events: list[tuple[str, dict]] = []

    def flaky_replace(source, target):
        calls.append((str(source), str(target)))
        if len(calls) <= 2:
            raise PermissionError(13, "file temporarily locked", str(target))
        return real_replace(source, target)

    monkeypatch.setattr(state_persistence.os, "replace", flaky_replace)
    monkeypatch.setattr(state_persistence.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(state_persistence.random, "uniform", lambda _a, _b: 0.0)

    result = atomic_write_json(
        destination,
        {"version": 4, "pending_order": None},
        retry_delays=(0.01, 0.02),
        jitter_fraction=0.0,
        event_callback=lambda kind, payload: events.append((kind, payload)),
    )

    assert result.attempt_count == 3
    assert result.retry_count == 2
    assert json.loads(destination.read_text(encoding="utf-8"))["version"] == 4
    assert [kind for kind, _ in events] == [
        "STATE_SAVE_RETRY",
        "STATE_SAVE_RETRY",
        "STATE_SAVE_RECOVERED",
    ]
    temporary_paths = {source for source, _ in calls}
    assert len(temporary_paths) == 1
    temporary_path = next(iter(temporary_paths))
    assert temporary_path.endswith(".tmp")
    assert temporary_path != str(destination) + ".tmp"
    assert not Path(temporary_path).exists()


def test_atomic_write_failure_preserves_previous_file_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    destination = tmp_path / "robot_state.json"
    destination.write_text('{"safe": true}', encoding="utf-8")
    events: list[tuple[str, dict]] = []

    def always_locked(_source, target):
        raise PermissionError(13, "locked", str(target))

    monkeypatch.setattr(state_persistence.os, "replace", always_locked)
    monkeypatch.setattr(state_persistence.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(state_persistence.random, "uniform", lambda _a, _b: 0.0)

    with pytest.raises(StatePersistenceError) as captured:
        atomic_write_json(
            destination,
            {"safe": False},
            retry_delays=(0.0, 0.0),
            jitter_fraction=0.0,
            event_callback=lambda kind, payload: events.append((kind, payload)),
        )

    assert captured.value.phase == "replace"
    assert captured.value.attempts == 3
    assert json.loads(destination.read_text(encoding="utf-8")) == {"safe": True}
    assert [kind for kind, _ in events] == [
        "STATE_SAVE_RETRY",
        "STATE_SAVE_RETRY",
        "STATE_SAVE_FAILED",
    ]
    assert list(tmp_path.glob("robot_state.json.*.tmp")) == []


def test_nontransient_replace_error_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    destination = tmp_path / "state.json"
    calls = 0

    def invalid_replace(_source, _target):
        nonlocal calls
        calls += 1
        raise FileNotFoundError("unexpected filesystem failure")

    monkeypatch.setattr(state_persistence.os, "replace", invalid_replace)

    with pytest.raises(StatePersistenceError) as captured:
        atomic_write_json(destination, {"value": 1}, retry_delays=(0.0, 0.0))

    assert calls == 1
    assert captured.value.attempts == 1
    assert captured.value.phase == "replace"
