from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class CircuitBreakerConfig:
    failure_threshold: int = 3
    open_seconds: int = 60
    max_open_seconds: int = 900

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be positive.")
        if self.open_seconds < 1:
            raise ValueError("open_seconds must be positive.")
        if self.max_open_seconds < self.open_seconds:
            raise ValueError("max_open_seconds must be >= open_seconds.")


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class PersistentCircuitBreaker:
    """Small state-dict backed circuit breaker.

    The caller owns persistence. Every mutating method updates the supplied
    dictionary in place, so the same state can be written atomically with the
    rest of the robot state.
    """

    def __init__(
        self,
        state: dict[str, Any],
        config: CircuitBreakerConfig,
    ) -> None:
        self.state = state
        self.config = config
        self.state.setdefault("consecutive_failures", 0)
        self.state.setdefault("state", "CLOSED")

    def can_attempt(
        self,
        now: datetime | None = None,
    ) -> tuple[bool, int]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        open_until = _parse_utc(self.state.get("open_until"))
        if open_until and now < open_until:
            remaining = max(1, int((open_until - now).total_seconds()))
            self.state["state"] = "OPEN"
            return False, remaining
        if open_until:
            self.state["state"] = "HALF_OPEN"
        return True, 0

    def record_success(self, now: datetime | None = None) -> None:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.state.update(
            {
                "state": "CLOSED",
                "consecutive_failures": 0,
                "open_until": None,
                "last_success_at": now.isoformat(),
                "last_error": None,
            }
        )

    def record_failure(
        self,
        error: str,
        now: datetime | None = None,
    ) -> int:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        failures = int(self.state.get("consecutive_failures", 0)) + 1
        self.state["consecutive_failures"] = failures
        self.state["last_failure_at"] = now.isoformat()
        self.state["last_error"] = str(error)

        retry_after = 0
        if failures >= self.config.failure_threshold:
            exponent = failures - self.config.failure_threshold
            retry_after = min(
                self.config.max_open_seconds,
                self.config.open_seconds * (2**exponent),
            )
            self.state["state"] = "OPEN"
            self.state["open_until"] = (
                now + timedelta(seconds=retry_after)
            ).isoformat()
        else:
            self.state["state"] = "CLOSED"
            self.state["open_until"] = None
        return int(retry_after)

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        allowed, retry_after = self.can_attempt(now)
        return {
            "circuit_state": self.state.get("state", "CLOSED"),
            "consecutive_failures": int(
                self.state.get("consecutive_failures", 0)
            ),
            "next_retry_at": self.state.get("open_until"),
            "retry_after_seconds": retry_after,
            "can_attempt": allowed,
            "last_success_at": self.state.get("last_success_at"),
            "last_failure_at": self.state.get("last_failure_at"),
            "last_error": self.state.get("last_error"),
        }
