from pathlib import Path

import pytest

from trading_robot.locking import InterProcessFileLock, LockUnavailableError


def test_interprocess_lock_rejects_second_owner(tmp_path: Path):
    path = tmp_path / "state.lock"
    first = InterProcessFileLock(path)
    second = InterProcessFileLock(path, timeout_seconds=0.05, poll_seconds=0.01)
    first.acquire()
    try:
        with pytest.raises(LockUnavailableError):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    assert second.acquired is True
    second.release()
