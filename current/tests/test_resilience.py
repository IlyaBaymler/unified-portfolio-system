from datetime import datetime, timedelta, timezone

from trading_robot.resilience import (
    CircuitBreakerConfig,
    PersistentCircuitBreaker,
)


def test_circuit_breaker_opens_and_recovers():
    state = {}
    config = CircuitBreakerConfig(
        failure_threshold=2,
        open_seconds=30,
        max_open_seconds=120,
    )
    breaker = PersistentCircuitBreaker(state, config)
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    assert breaker.can_attempt(now) == (True, 0)
    assert breaker.record_failure("timeout-1", now) == 0
    assert breaker.can_attempt(now) == (True, 0)
    assert breaker.record_failure("timeout-2", now) == 30

    allowed, retry_after = breaker.can_attempt(now + timedelta(seconds=5))
    assert allowed is False
    assert 24 <= retry_after <= 25

    allowed, retry_after = breaker.can_attempt(now + timedelta(seconds=31))
    assert allowed is True
    assert retry_after == 0
    assert state["state"] == "HALF_OPEN"

    breaker.record_success(now + timedelta(seconds=31))
    assert state["state"] == "CLOSED"
    assert state["consecutive_failures"] == 0
