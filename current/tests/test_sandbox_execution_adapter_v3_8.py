from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_robot.central_order_manager import (
    CentralOrderCandidate,
    CentralOrderManager,
    CentralOrderStore,
    ExecutionAuthorization,
)
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    PortfolioState,
    SnapshotFreshness,
)
from trading_robot.portfolio_preflight import PortfolioSnapshotLease
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.sandbox_execution_adapter import (
    SANDBOX_EXECUTION_CONFIRMATION,
    SandboxExecutionAdapter,
    SandboxExecutionPolicy,
)
from trading_robot.tbank_sandbox import TBankAPIError, TBankSandboxClient

ACCOUNT = "sandbox-account-1"
NOW = datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc).isoformat()


class FakeSandboxTransport:
    def __init__(self) -> None:
        self.market_open = True
        self.market_uncertain = False
        self.market_error: BaseException | None = None
        self.post_error: BaseException | None = None
        self.inspection_error: BaseException | None = None
        self.response_request_id: str | None = None
        self.provider_status = "EXECUTION_REPORT_STATUS_NEW"
        self.filled_lots = 0
        self.status_calls = 0
        self.post_calls = 0
        self.inspection_calls = 0
        self.request_ids: list[str] = []

    def get_trading_status(self, instrument_id: str):
        self.status_calls += 1
        if self.market_error is not None:
            raise self.market_error
        if self.market_uncertain:
            return {"tradingStatus": "UNKNOWN"}
        return {
            "tradingStatus": (
                "SECURITY_TRADING_STATUS_NORMAL_TRADING"
                if self.market_open
                else "SECURITY_TRADING_STATUS_NOT_AVAILABLE_FOR_TRADING"
            ),
            "apiTradeAvailableFlag": self.market_open,
            "limitOrderAvailableFlag": self.market_open,
            "bestpriceOrderAvailableFlag": self.market_open,
        }

    def post_order(
        self,
        account_id: str,
        instrument_id: str,
        lots: int,
        direction: str,
        *,
        order_id: str,
        order_type: str,
        time_in_force: str,
    ):
        self.post_calls += 1
        self.request_ids.append(order_id)
        if self.post_error is not None:
            raise self.post_error
        return {
            "orderId": "exchange-order-1",
            "orderRequestId": self.response_request_id or order_id,
            "executionReportStatus": self.provider_status,
            "lotsExecuted": str(self.filled_lots),
        }

    def get_order_state(
        self,
        account_id: str,
        order_id: str,
        *,
        by_request_id: bool = True,
    ):
        self.inspection_calls += 1
        if self.inspection_error is not None:
            raise self.inspection_error
        return {
            "orderId": "exchange-order-1",
            "orderRequestId": order_id,
            "executionReportStatus": self.provider_status,
            "lotsExecuted": str(self.filled_lots),
        }


def portfolio_state(*, revision: int = 0) -> PortfolioState:
    return PortfolioState(
        version=2,
        account=AccountState(
            account_id=ACCOUNT,
            total_value=1_000_000.0,
            securities_value=0.0,
            expected_yield=0.0,
            cash_balances=(CashBalance("rub", 1_000_000.0),),
        ),
        snapshot_at=NOW,
        generated_at=NOW,
        freshness=SnapshotFreshness.FRESH,
        source="PORTFOLIO_MANAGER",
        positions=(),
        warnings=(),
        state_status="READY",
        blocking=False,
        revision=revision,
    )


def authorization(state: PortfolioState) -> ExecutionAuthorization:
    lease = PortfolioSnapshotLease.from_state(state, leased_at=NOW)
    return ExecutionAuthorization(
        account_id=ACCOUNT,
        instrument_id="uid-sber",
        authorized_target_lots=1,
        portfolio_revision=lease.revision,
        portfolio_decision_checksum=lease.decision_checksum,
        portfolio_document_checksum=lease.document_checksum,
        available_cash_kopecks=100_000_000,
        preflight_status="PASS",
        pending_order_ids=(),
        uncertain_order_ids=(),
        risk_status="PASS",
        risk_decision_id="risk-decision-1",
        risk_policy_hash="b" * 64,
        risk_order_allowed=True,
        authorized_at=NOW,
    )


def candidate() -> CentralOrderCandidate:
    return CentralOrderCandidate(
        account_id=ACCOUNT,
        instrument_id="uid-sber",
        ticker="SBER",
        runtime_key="runtime-uid-sber",
        runtime_config_hash="a" * 64,
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_time=NOW,
        strategy_id="sma",
        strategy_profile_hash="c" * 64,
        current_lots=0,
        target_lots=1,
        estimated_price_kopecks=10_000,
        lot_size=10,
        created_at=NOW,
    )


def setup_runtime(tmp_path: Path):
    state = portfolio_state()
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(state)
    manager = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    queued = manager.enqueue(candidate(), authorization(state)).intent
    return state, repository, manager, queued


def adapter(
    transport: FakeSandboxTransport,
    manager: CentralOrderManager,
    *,
    armed: bool = True,
) -> SandboxExecutionAdapter:
    return SandboxExecutionAdapter(
        transport,
        manager,
        SandboxExecutionPolicy(
            account_id=ACCOUNT,
            enabled=armed,
            confirmation=(SANDBOX_EXECUTION_CONFIRMATION if armed else ""),
        ),
    )


def test_policy_requires_exact_explicit_arming_confirmation():
    with pytest.raises(ValueError, match="exact arming"):
        SandboxExecutionPolicy(
            account_id=ACCOUNT,
            enabled=True,
            confirmation="YES",
        )


def test_disarmed_adapter_never_calls_provider(tmp_path: Path):
    _state, repository, manager, queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()

    result = adapter(transport, manager, armed=False).dispatch_next(repository)

    assert result.status == "DISARMED"
    assert result.intent_id == queued.intent_id
    assert manager.state().queued[0].status == "QUEUED"
    assert transport.status_calls == 0
    assert transport.post_calls == 0


@pytest.mark.parametrize(
    ("market_open", "market_uncertain", "expected"),
    [
        (False, False, "MARKET_IDLE"),
        (True, True, "MARKET_STATUS_UNCERTAIN"),
    ],
)
def test_market_gate_keeps_intent_queued_without_submit(
    tmp_path: Path,
    market_open: bool,
    market_uncertain: bool,
    expected: str,
):
    _state, repository, manager, _queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()
    transport.market_open = market_open
    transport.market_uncertain = market_uncertain

    result = adapter(transport, manager).dispatch_next(repository)

    assert result.status == expected
    assert result.retryable
    assert manager.state().queued[0].status == "QUEUED"
    assert transport.post_calls == 0


def test_disconnect_during_market_precheck_never_prepares_order(tmp_path: Path):
    _state, repository, manager, _queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()
    transport.market_error = TBankAPIError("dns unavailable", transient=True)

    result = adapter(transport, manager).dispatch_next(repository)

    assert result.status == "MARKET_STATUS_UNAVAILABLE"
    assert result.retryable
    assert manager.state().queued[0].status == "QUEUED"
    assert transport.post_calls == 0


def test_successful_handoff_submits_once_and_remains_blocking(tmp_path: Path):
    _state, repository, manager, queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()
    transport.provider_status = "EXECUTION_REPORT_STATUS_FILL"
    transport.filled_lots = 1
    execution = adapter(transport, manager)

    first = execution.dispatch_next(repository)
    second = execution.dispatch_next(repository)

    assert first.status == "SUBMITTED"
    assert first.intent_id == queued.intent_id
    assert first.terminal
    assert first.executed_lots == 1
    assert transport.request_ids == [queued.intent_id]
    assert second.status == "ACCOUNT_BLOCKED"
    assert transport.post_calls == 1
    assert manager.state().blocking_intent.status == "SUBMITTED"


def test_operator_selected_intent_mismatch_never_calls_provider(tmp_path: Path):
    _state, repository, manager, queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()

    result = adapter(transport, manager).dispatch_next(
        repository,
        expected_intent_id="00000000-0000-0000-0000-000000000000",
    )

    assert result.status == "OPERATOR_INTENT_MISMATCH"
    assert result.intent_id == queued.intent_id
    assert transport.status_calls == 0
    assert transport.post_calls == 0
    assert manager.state().queued[0].status == "QUEUED"


@pytest.mark.parametrize(
    "error",
    [
        TBankAPIError("lost response", transient=True),
        TBankAPIError("duplicate request is ambiguous", status_code=409),
    ],
)
def test_ambiguous_submission_becomes_uncertain_without_resubmit(
    tmp_path: Path,
    error: TBankAPIError,
):
    _state, repository, manager, queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()
    transport.post_error = error
    execution = adapter(transport, manager)

    first = execution.dispatch_next(repository)
    second = execution.dispatch_next(repository)

    assert first.status == "SUBMISSION_UNCERTAIN"
    assert first.order_may_have_been_sent
    assert manager.state().blocking_intent.status == "UNCERTAIN"
    assert second.status == "ACCOUNT_BLOCKED"
    assert transport.request_ids == [queued.intent_id]


def test_http_success_with_invalid_response_body_is_uncertain(tmp_path: Path):
    _state, repository, manager, _queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()
    transport.post_error = TBankAPIError(
        "T-Invest returned invalid JSON",
        status_code=200,
        transient=False,
        service="SandboxService",
        method="PostSandboxOrder",
    )

    result = adapter(transport, manager).dispatch_next(repository)

    assert result.status == "SUBMISSION_UNCERTAIN"
    assert result.order_may_have_been_sent
    assert manager.state().blocking_intent.status == "UNCERTAIN"


def test_restart_and_read_only_inspection_do_not_resubmit(tmp_path: Path):
    _state, repository, manager, queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()
    transport.post_error = TBankAPIError("connection reset", transient=True)
    adapter(transport, manager).dispatch_next(repository)

    restarted = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    assert restarted.recover_after_restart() is None
    transport.post_error = None
    transport.provider_status = "EXECUTION_REPORT_STATUS_FILL"
    transport.filled_lots = 1
    inspection = adapter(transport, restarted).inspect_blocking_order()

    assert inspection.status == "ORDER_OBSERVED"
    assert inspection.intent_id == queued.intent_id
    assert inspection.terminal
    assert inspection.suggested_reconciliation_outcome == "FILLED"
    assert transport.post_calls == 1
    assert transport.inspection_calls == 1
    assert restarted.state().blocking_intent.status == "UNCERTAIN"


def test_explicit_rejection_is_terminal_and_releases_reservation(tmp_path: Path):
    _state, repository, manager, _queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()
    transport.post_error = TBankAPIError(
        "invalid order",
        status_code=400,
        transient=False,
    )

    result = adapter(transport, manager).dispatch_next(repository)

    assert result.status == "SUBMISSION_REJECTED"
    assert result.order_was_sent
    assert manager.state().intents[0].status == "FAILED"
    assert manager.state().intents[0].outcome == "SUBMISSION_REJECTED"
    assert manager.state().reserved_cash_kopecks == 0


def test_local_validation_failure_is_not_marked_as_sent(tmp_path: Path):
    _state, repository, manager, _queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()
    transport.post_error = ValueError("invalid local order payload")

    result = adapter(transport, manager).dispatch_next(repository)

    assert result.status == "LOCAL_VALIDATION_FAILED"
    assert not result.order_was_sent
    assert manager.state().intents[0].outcome == "PRE_SUBMIT_FAILED"


def test_canonical_revision_change_blocks_before_submit(tmp_path: Path):
    state, repository, manager, _queued = setup_runtime(tmp_path)
    repository.save(replace(state, revision=1), expected_revision=0)
    transport = FakeSandboxTransport()

    result = adapter(transport, manager).dispatch_next(repository)

    assert result.status == "CANONICAL_PREFLIGHT_BLOCKED"
    assert result.retryable
    assert manager.state().queued[0].status == "QUEUED"
    assert transport.post_calls == 0


def test_mismatched_response_correlation_is_uncertain(tmp_path: Path):
    _state, repository, manager, _queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()
    transport.response_request_id = "different-request-id"

    result = adapter(transport, manager).dispatch_next(repository)

    assert result.status == "SUBMISSION_UNCERTAIN"
    assert manager.state().blocking_intent.status == "UNCERTAIN"


def test_not_found_inspection_remains_uncertain_without_resubmit(tmp_path: Path):
    _state, repository, manager, _queued = setup_runtime(tmp_path)
    transport = FakeSandboxTransport()
    manager.prepare_next(repository)
    manager.mark_uncertain("" + manager.state().blocking_intent.intent_id, reason="restart")
    transport.inspection_error = TBankAPIError(
        "order not found",
        status_code=404,
        transient=False,
    )

    result = adapter(transport, manager).inspect_blocking_order()

    assert result.status == "NOT_FOUND_UNCERTAIN"
    assert result.suggested_reconciliation_outcome is None
    assert result.retryable
    assert manager.state().blocking_intent.status == "UNCERTAIN"
    assert transport.post_calls == 0


def test_real_tbank_client_matches_adapter_payload_and_retry_contract(
    tmp_path: Path,
    monkeypatch,
):
    _state, repository, manager, queued = setup_runtime(tmp_path)
    calls: list[tuple[str, str, dict, bool]] = []

    def fake_post(self, service, method, payload=None, *, retry_safe=True):
        body = dict(payload or {})
        calls.append((service, method, body, retry_safe))
        if method == "GetTradingStatus":
            return {
                "apiTradeAvailableFlag": True,
                "limitOrderAvailableFlag": True,
                "bestpriceOrderAvailableFlag": True,
            }
        if method == "PostSandboxOrder":
            return {
                "orderId": "exchange-order-1",
                "orderRequestId": body["orderId"],
                "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
                "lotsExecuted": "0",
            }
        raise AssertionError(method)

    monkeypatch.setattr(TBankSandboxClient, "_post", fake_post)
    with TBankSandboxClient("dummy-token", max_retries=0) as client:
        result = SandboxExecutionAdapter(
            client,
            manager,
            SandboxExecutionPolicy(
                account_id=ACCOUNT,
                enabled=True,
                confirmation=SANDBOX_EXECUTION_CONFIRMATION,
            ),
        ).dispatch_next(
            repository,
            expected_intent_id=queued.intent_id,
        )

    assert result.status == "SUBMITTED"
    submission = next(item for item in calls if item[1] == "PostSandboxOrder")
    assert submission[0] == "SandboxService"
    assert submission[2]["accountId"] == ACCOUNT
    assert submission[2]["instrumentId"] == "uid-sber"
    assert submission[2]["orderId"] == queued.intent_id
    assert submission[2]["quantity"] == "1"
    assert submission[2]["direction"] == "ORDER_DIRECTION_BUY"
    assert submission[2]["timeInForce"] == "TIME_IN_FORCE_FILL_AND_KILL"
    assert submission[3] is True
