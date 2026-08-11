from trading_robot.gui_resilience import (
    TransientBackoff,
    describe_background_error,
)
from trading_robot.tbank_sandbox import TBankAPIError


def test_describe_http_500_is_transient_and_safe() -> None:
    exc = TBankAPIError(
        "raw server payload that must not be shown",
        status_code=500,
        details={"requestId": "req-123", "secret": "do-not-show"},
        transient=True,
        service="SandboxService",
        method="GetSandboxPortfolio",
    )

    info = describe_background_error(exc)

    assert info.transient is True
    assert info.status_code == 500
    assert info.request_id == "req-123"
    assert info.dedup_key == "tbank:500:SandboxService:GetSandboxPortfolio"
    assert "do-not-show" not in info.summary
    assert "raw server payload" not in info.summary
    assert "GetSandboxPortfolio" in info.summary


def test_describe_connection_failure_is_transient() -> None:
    exc = TBankAPIError(
        "DNS failure",
        transient=True,
        service="MarketDataService",
        method="GetTradingStatus",
    )

    info = describe_background_error(exc)

    assert info.transient is True
    assert info.status_code is None
    assert "ошибка соединения" in info.summary


def test_describe_non_transient_api_error_remains_operator_visible() -> None:
    exc = TBankAPIError(
        "bad request",
        status_code=400,
        details={"requestId": "req-400"},
        transient=False,
        service="SandboxService",
        method="PostSandboxOrder",
    )

    info = describe_background_error(exc)

    assert info.transient is False
    assert "отклонил запрос" in info.summary


def test_transient_backoff_grows_and_caps() -> None:
    backoff = TransientBackoff(base_seconds=30, max_seconds=120)

    assert [backoff.record_failure() for _ in range(5)] == [30, 60, 120, 120, 120]


def test_transient_backoff_honours_retry_after_and_resets() -> None:
    backoff = TransientBackoff(base_seconds=30, max_seconds=600)

    assert backoff.record_failure(retry_after_seconds=90) == 90
    assert backoff.record_failure() == 60
    backoff.reset()
    assert backoff.record_failure() == 30
