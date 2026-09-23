from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from tools import v3_10_q7a_live_entrypoint as live
from trading_robot.multi_instrument_strategy import StrategyProposal
from trading_robot.portfolio_adapters import BrokerPortfolioAdapter
from trading_robot.strategy_runtime import StrategyDecision, compare_strategy_decisions
from trading_robot.tbank_sandbox import TBankSandboxClient

VECTORS = json.loads(
    (
        Path(__file__).parent / "fixtures" / "v3_10_q7a_live_entrypoint_vectors.json"
    ).read_text(encoding="utf-8")
)


def test_live_contract_identity_is_exact_r2_acceptance() -> None:
    assert live.LIVE_CONTRACT_COMMIT == ("61c7d32c119dd46488ac764ed887b339f7a1f53a")
    assert live.LIVE_CONTRACT_TREE == "3d1fb3e433f62da4629a47e57821d2949fa05afb"


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def preparation(**changes: object) -> tuple[bytes, str]:
    account_policy = {
        "owner": "RuntimeCashAuthorityManager.sync_runtime",
        "operations_service": "SandboxService",
        "operations_method": "GetSandboxOperationsByCursor",
        "operations_logical_sessions_per_gate": 1,
        "operations_max_pages": 100,
        "operations_max_items": 100_000,
        "operations_max_attempts_per_page": 3,
        "operations_policy_sha256": "a" * 64,
        "portfolio_service": "SandboxService",
        "portfolio_method": "GetSandboxPortfolio",
        "portfolio_physical_requests_per_gate": 1,
        "portfolio_logical_reads_gate_a": 2,
        "portfolio_logical_reads_gate_b": 1,
        "withdraw_limits_method": "GetSandboxWithdrawLimits",
        "withdraw_limits_requests_per_gate": 1,
        "positions_required": False,
        "positions_method": None,
        "orders_service": "SandboxService",
        "orders_method": "GetSandboxOrders",
        "orders_owner": "CanonicalPortfolioManager.refresh",
        "orders_requests_gate_a": 1,
        "orders_requests_gate_b": 0,
        "orders_retries": 0,
        "orders_redirect_replays": 0,
        "orders_automatic_reacquisition": 0,
        "portfolio_owner": "CanonicalPortfolioManager.refresh",
        "owner_graph_sha256": "b" * 64,
        "absolute_timeout_policy_sha256": "c" * 64,
    }
    candle = {
        "owner": "StrategyCandleLoader",
        "service": "MarketDataService",
        "method": "GetCandles",
        "target_instrument_sha256": VECTORS["target_instrument_sha256"],
        "interval": VECTORS["interval"],
        "required_bars": VECTORS["required_bars"],
        "lookback_days": VECTORS["lookback_days"],
        "maximum_span_seconds": VECTORS["maximum_span_seconds"],
        "logical_sessions": 1,
        "physical_requests": 1,
        "retries": 0,
        "redirects_followed": 0,
        "automatic_reacquisition": 0,
        "candle_source_type": "CANDLE_SOURCE_EXCHANGE",
        "maximum_age_seconds": 3900,
        "future_skew_seconds": 5,
    }
    quote = {
        "owner": "_ProductionGuiHooks",
        "service": "MarketDataService",
        "method": "GetLastPrices",
        "target_instrument_sha256": VECTORS["target_instrument_sha256"],
        "last_price_type": "LAST_PRICE_EXCHANGE",
        "request_count": 1,
        "retries": 0,
        "redirects_followed": 0,
        "source": "TBANK_LAST_PRICE_EXCHANGE",
        "maximum_age_seconds": 300,
        "future_skew_seconds": 5,
    }
    status = {
        "owner": "SandboxExecutionAdapter._market_precheck",
        "service": "MarketDataService",
        "method": "GetTradingStatus",
        "target_instrument_sha256": VECTORS["target_instrument_sha256"],
        "logical_calls": 1,
        "physical_requests": 1,
        "retries": 0,
        "redirect_replays": 0,
        "automatic_reacquisition": 0,
    }
    fields: dict[str, object] = {
        "version": 1,
        "domain": live.PREPARATION_DOMAIN,
        "experiment_id": live.EXPERIMENT_ID,
        "candidate_commit": VECTORS["candidate_commit"],
        "candidate_tree": VECTORS["candidate_tree"],
        "live_contract_commit": live.LIVE_CONTRACT_COMMIT,
        "live_contract_tree": live.LIVE_CONTRACT_TREE,
        "q7a_contract_commit": live.Q7A_CONTRACT_COMMIT,
        "q7a_contract_tree": live.Q7A_CONTRACT_TREE,
        "q7a_synthetic_implementation_commit": (
            live.Q7A_SYNTHETIC_IMPLEMENTATION_COMMIT
        ),
        "q7a_synthetic_implementation_tree": live.Q7A_SYNTHETIC_IMPLEMENTATION_TREE,
        "accepted_implementation_commit": VECTORS["candidate_commit"],
        "accepted_implementation_tree": VECTORS["candidate_tree"],
        "accepted_q1_summary_sha256": "d" * 64,
        "accepted_q4_summary_sha256": "e" * 64,
        "accepted_q5_summary_sha256": "f" * 64,
        "runtime_manifest_sha256": VECTORS["runtime_manifest_sha256"],
        "account_scope_sha256": VECTORS["account_scope_sha256"],
        "identity_key_id": VECTORS["identity_key_id"],
        "environment": "SANDBOX",
        "configured_set_sha256": VECTORS["configured_set_sha256"],
        "target_instrument_sha256": VECTORS["target_instrument_sha256"],
        "control_record_sha256": VECTORS["control_record_sha256"],
        "risk_policy_sha256": VECTORS["risk_policy_sha256"],
        "risk_policy_mode": "ENFORCED",
        "static_metadata_schema_sha256": "1" * 64,
        "static_metadata_sha256": VECTORS["static_metadata_sha256"],
        "currency_evidence_sha256": "2" * 64,
        "lot_size_evidence_sha256": "3" * 64,
        "account_cash_portfolio_policy": account_policy,
        "candle_policy": candle,
        "quote_policy": quote,
        "trading_status_policy": status,
        "cl7_final_freshness_seconds": 10,
        "max_provider_post_attempts": 1,
        "automatic_retries": 0,
        "redirect_replays": 0,
        "absolute_deadline_utc": "2026-09-23T12:00:00.000000Z",
        "evidence_root_sha256": VECTORS["evidence_root_sha256"],
        "backup_binding_sha256": VECTORS["backup_binding_sha256"],
        "pre_admission_stop_rules_sha256": "4" * 64,
        "post_admission_same_lineage_rules_sha256": "5" * 64,
        "evidence_privacy_policy_sha256": "6" * 64,
        "recovery_procedure_sha256": "7" * 64,
        "stop_conditions_sha256": "8" * 64,
        "created_at_utc": "2026-09-22T12:00:00.000000Z",
    }
    fields.update(changes)
    acquisition_policy = {
        "account_cash_portfolio_policy": fields["account_cash_portfolio_policy"],
        "candle_policy": fields["candle_policy"],
        "quote_policy": fields["quote_policy"],
        "trading_status_policy": fields["trading_status_policy"],
        "cl7_final_freshness_seconds": fields["cl7_final_freshness_seconds"],
        "max_provider_post_attempts": fields["max_provider_post_attempts"],
        "automatic_retries": fields["automatic_retries"],
        "redirect_replays": fields["redirect_replays"],
    }
    fields.setdefault(
        "live_acquisition_policy_sha256",
        hashlib.sha256(canonical(acquisition_policy)).hexdigest(),
    )
    fields["preparation_sha256"] = hashlib.sha256(canonical(fields)).hexdigest()
    raw = canonical(fields)
    return raw, hashlib.sha256(raw).hexdigest()


def reason(expected_reason: str, fn: Any, *args: object, **kwargs: object) -> None:
    with pytest.raises(live.Q7ALiveError) as captured:
        fn(*args, **kwargs)
    assert captured.value.reason == expected_reason


def valid_preparation() -> live.LivePreparation:
    raw, digest = preparation()
    return live.verify_preparation(
        raw,
        expected_sha256=digest,
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
    )


def frame() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            "2026-09-22T09:00:00Z",
            "2026-09-22T10:00:00Z",
            "2026-09-22T11:00:00Z",
        ]
    )
    return pd.DataFrame(
        {
            "open": np.array([100.0, 101.0, 102.0], dtype=np.float64),
            "high": np.array([102.0, 103.0, 104.0], dtype=np.float64),
            "low": np.array([99.0, 100.0, 101.0], dtype=np.float64),
            "close": np.array([101.0, 102.0, 103.0], dtype=np.float64),
            "volume": np.array([10, 11, 12], dtype=np.int64),
            "is_complete": np.array([True, True, True], dtype=np.bool_),
        },
        index=index,
    )


def test_preparation_exact_canonical_binding_and_no_future_observations() -> None:
    selected = valid_preparation()
    assert selected.sha256 == hashlib.sha256(selected.raw).hexdigest()
    raw, digest = preparation(current_quote_sha256="f" * 64)
    reason(
        "PREPARATION_INVALID",
        live.verify_preparation,
        raw,
        expected_sha256=digest,
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
    )


def test_preparation_sha_and_source_substitution_fail_closed() -> None:
    raw, digest = preparation()
    reason(
        "PREPARATION_SHA_MISMATCH",
        live.verify_preparation,
        raw,
        expected_sha256="f" * 64,
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
    )
    reason(
        "SOURCE_IDENTITY_MISMATCH",
        live.verify_preparation,
        raw,
        expected_sha256=digest,
        candidate_commit="c" * 40,
        candidate_tree=VECTORS["candidate_tree"],
    )


@pytest.mark.parametrize(
    ("section", "key", "value", "expected"),
    [
        ("candle_policy", "physical_requests", 2, "CANDLE_ACQUISITION_POLICY_MISMATCH"),
        ("candle_policy", "lookback_days", 91, "CANDLE_ACQUISITION_POLICY_MISMATCH"),
        ("quote_policy", "method", "GetOrderBook", "LIVE_ACQUISITION_POLICY_MISMATCH"),
        ("quote_policy", "request_count", 2, "LIVE_ACQUISITION_POLICY_MISMATCH"),
        ("trading_status_policy", "retries", 1, "LIVE_ACQUISITION_POLICY_MISMATCH"),
        (
            "trading_status_policy",
            "owner",
            "entrypoint",
            "LIVE_ACQUISITION_POLICY_MISMATCH",
        ),
        (
            "account_cash_portfolio_policy",
            "orders_requests_gate_a",
            2,
            "LIVE_ACQUISITION_POLICY_MISMATCH",
        ),
        (
            "account_cash_portfolio_policy",
            "orders_requests_gate_b",
            1,
            "LIVE_ACQUISITION_POLICY_MISMATCH",
        ),
        (
            "account_cash_portfolio_policy",
            "orders_method",
            "GetOrders",
            "LIVE_ACQUISITION_POLICY_MISMATCH",
        ),
    ],
)
def test_preparation_policy_tamper_is_rejected(
    section: str, key: str, value: object, expected: str
) -> None:
    raw, _ = preparation()
    fields = json.loads(raw)
    fields[section][key] = value
    fields["preparation_sha256"] = hashlib.sha256(
        canonical({k: v for k, v in fields.items() if k != "preparation_sha256"})
    ).hexdigest()
    altered = canonical(fields)
    reason(
        expected,
        live.verify_preparation,
        altered,
        expected_sha256=hashlib.sha256(altered).hexdigest(),
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
    )


def test_canonical_candle_frame_is_exact_and_stable() -> None:
    selected = valid_preparation()
    first = live.canonical_candle_evidence(
        frame(),
        policy=selected.candle_policy,
        now=datetime.fromisoformat(VECTORS["now"].replace("Z", "+00:00")),
    )
    second = live.canonical_candle_evidence(
        frame(),
        policy=selected.candle_policy,
        now=datetime.fromisoformat(VECTORS["now"].replace("Z", "+00:00")),
    )
    assert first == second
    assert first.row_count == 3
    assert first.latest_begin_utc == VECTORS["latest_candle"]
    assert first.latest_close_utc == "2026-09-22T12:00:00.000000Z"


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "reverse",
        "incomplete",
        "nan",
        "negative",
        "bad_ohlc",
        "bool_volume",
    ],
)
def test_candle_frame_adversarial_mutations_fail(mutation: str) -> None:
    selected = valid_preparation()
    value = frame()
    if mutation == "duplicate":
        value.index = pd.DatetimeIndex([value.index[0], value.index[0], value.index[2]])
    elif mutation == "reverse":
        value = value.iloc[::-1]
    elif mutation == "incomplete":
        value.loc[value.index[-1], "is_complete"] = False
    elif mutation == "nan":
        value.loc[value.index[-1], "close"] = np.nan
    elif mutation == "negative":
        value.loc[value.index[-1], "volume"] = -1
    elif mutation == "bad_ohlc":
        value.loc[value.index[-1], "low"] = 999.0
    else:
        value["volume"] = pd.Series([True, True, True], index=value.index, dtype=object)
    expected = (
        "CANDLE_FRAME_INCOMPLETE"
        if mutation in {"duplicate", "incomplete"}
        else "CANDLE_FRAME_INVALID"
    )
    reason(
        expected,
        live.canonical_candle_evidence,
        value,
        policy=selected.candle_policy,
        now=datetime.fromisoformat(VECTORS["now"].replace("Z", "+00:00")),
    )


def test_candle_frame_accepts_normal_exchange_session_gaps() -> None:
    selected = valid_preparation()
    value = frame()
    value.index = pd.DatetimeIndex(
        [
            "2026-09-18T15:00:00Z",
            "2026-09-21T10:00:00Z",
            "2026-09-22T11:00:00Z",
        ]
    )
    evidence = live.canonical_candle_evidence(
        value,
        policy=selected.candle_policy,
        now=datetime.fromisoformat(VECTORS["now"].replace("Z", "+00:00")),
    )
    assert evidence.row_count == 3
    assert evidence.latest_begin_utc == VECTORS["latest_candle"]


def test_candle_frame_is_bound_to_exact_authorized_request_range() -> None:
    selected = valid_preparation()
    evidence = live.canonical_candle_evidence(
        frame(),
        policy=selected.candle_policy,
        now=datetime.fromisoformat(VECTORS["now"].replace("Z", "+00:00")),
    )
    request = {
        "target_instrument_sha256": VECTORS["target_instrument_sha256"],
        "from_utc": "2026-09-22T09:00:00.000000Z",
        "to_utc": "2026-09-22T12:00:00.000000Z",
        "interval": "CANDLE_INTERVAL_HOUR",
        "limit": None,
        "candle_source_type": "CANDLE_SOURCE_EXCHANGE",
    }
    live.verify_candle_request_binding(
        evidence,
        request,
        expected_interval="CANDLE_INTERVAL_HOUR",
        expected_target_sha256=VECTORS["target_instrument_sha256"],
    )
    outside = dict(request, from_utc="2026-09-22T10:00:00.000000Z")
    reason(
        "CANDLE_FRAME_INVALID",
        live.verify_candle_request_binding,
        evidence,
        outside,
        expected_interval="CANDLE_INTERVAL_HOUR",
        expected_target_sha256=VECTORS["target_instrument_sha256"],
    )


def test_stale_and_future_candle_frames_fail() -> None:
    selected = valid_preparation()
    reason(
        "CANDLE_EVIDENCE_STALE",
        live.canonical_candle_evidence,
        frame(),
        policy=selected.candle_policy,
        now=datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc),
    )
    reason(
        "CANDLE_EVIDENCE_STALE",
        live.canonical_candle_evidence,
        frame(),
        policy=selected.candle_policy,
        now=datetime(2026, 9, 22, 11, 59, 50, tzinfo=timezone.utc),
    )


def proposal() -> StrategyProposal:
    primary = StrategyDecision(
        candle_time="2026-09-22T11:00:00+00:00",
        strategy_id="sma",
        strategy_version="1",
        role="PRIMARY",
        config_hash="a" * 64,
        signal=0,
        target_weight=0.25,
        target_lots=0,
        reason="BASE",
        indicators={"close": 103.0, "atr": 2.0},
        bars_used=3,
        required_bars=3,
        stop_level=97.0,
    )
    shadow = replace(
        primary, strategy_id="donchian", role="SHADOW", config_hash="b" * 64
    )
    decisions = {"sma": primary, "donchian": shadow}
    return StrategyProposal(
        runtime_key="runtime-key",
        instrument_id="target",
        ticker="TEST",
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_time=primary.candle_time,
        strategy_profile_hash="c" * 64,
        primary_strategy="sma",
        primary_target_lots=0,
        decisions=decisions,
        comparison=compare_strategy_decisions(decisions, "sma"),
        generated_at="2026-09-22T12:30:00+00:00",
    )


def test_controlled_derivation_changes_only_frozen_primary_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = proposal()
    monkeypatch.setattr(live, "build_strategy_proposal", lambda *a, **k: base)
    selected = valid_preparation()
    evidence = live.canonical_candle_evidence(
        frame(),
        policy=selected.candle_policy,
        now=datetime(2026, 9, 22, 12, 30, tzinfo=timezone.utc),
    )
    result = live.derive_controlled_proposal(
        runtime=object(),
        profile=object(),
        frame=frame(),
        frame_evidence=evidence,
        now=datetime(2026, 9, 22, 12, 30, tzinfo=timezone.utc),
    )
    controlled = result.controlled
    assert controlled.primary_target_lots == 1
    assert controlled.decisions["sma"].signal == 1
    assert controlled.decisions["sma"].target_lots == 1
    assert controlled.decisions["sma"].indicators == base.decisions["sma"].indicators
    assert controlled.decisions["sma"].stop_level == base.decisions["sma"].stop_level
    assert controlled.decisions["donchian"] == base.decisions["donchian"]
    assert result.base_sha256 != result.controlled_sha256
    assert result.frame_sha256 == evidence.sha256


class FakeProvider:
    def __init__(self) -> None:
        self.status = {"tradingStatus": "SECURITY_TRADING_STATUS_NORMAL_TRADING"}
        self.posts = 0

    def get_candles(self, *args: object, **kwargs: object) -> pd.DataFrame:
        return frame()

    def get_last_prices(self, values: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "instrumentUid": values[0],
                "time": "2026-09-22T12:29:59Z",
                "price": {"units": "103", "nano": 250000000},
            }
        ]

    def get_trading_status(self, instrument: str) -> dict[str, Any]:
        return dict(self.status)

    def post_order_once(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.posts += 1
        return {"ok": True}


class TelemetryProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.max_retries = 0
        self._session = SimpleNamespace(max_redirects=30)
        self.telemetry_callback: Any = None
        self.portfolio_reads = 0
        self.order_reads = 0
        self.orders = [
            {
                "orderId": "target-order",
                "instrumentUid": "target",
                "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
            },
            {
                "orderId": "other-order",
                "instrumentUid": "other",
                "executionReportStatus": "EXECUTION_REPORT_STATUS_UNSPECIFIED",
            },
        ]

    def set_telemetry_callback(self, callback: Any) -> None:
        self.telemetry_callback = callback

    def emit(
        self,
        service: str,
        method: str,
        *,
        attempt_count: int = 1,
    ) -> None:
        self.telemetry_callback(
            {
                "service": service,
                "method": method,
                "attempt_count": attempt_count,
                "retry_count": attempt_count - 1,
                "status_code": 200,
                "transient": False,
                "tracking_id": "private-tracking-id",
            }
        )

    def get_operations_by_cursor_once(self, *args: object, **kwargs: object) -> dict:
        self.emit("SandboxService", "GetSandboxOperationsByCursor")
        return {"items": []}

    def get_portfolio(self, account_id: str) -> dict[str, object]:
        self.portfolio_reads += 1
        self.emit("SandboxService", "GetSandboxPortfolio")
        return {"account": account_id, "positions": []}

    def get_withdraw_limits(self, account_id: str) -> dict[str, object]:
        self.emit("SandboxService", "GetSandboxWithdrawLimits")
        return {"account": account_id}

    def get_orders(self, account_id: str) -> list[dict[str, object]]:
        self.order_reads += 1
        self.emit("SandboxService", "GetSandboxOrders")
        return json.loads(json.dumps(self.orders))

    def get_candles(self, *args: object, **kwargs: object) -> pd.DataFrame:
        self.emit("MarketDataService", "GetCandles")
        return frame()

    def get_last_prices(self, values: list[str]) -> list[dict[str, Any]]:
        self.emit("MarketDataService", "GetLastPrices")
        return super().get_last_prices(values)

    def get_trading_status(self, instrument: str) -> dict[str, Any]:
        self.emit("MarketDataService", "GetTradingStatus")
        return super().get_trading_status(instrument)

    def post_order_once(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.emit("SandboxService", "PostSandboxOrder")
        return super().post_order_once(*args, **kwargs)


def test_provider_adapter_enforces_target_phase_and_one_call_budgets() -> None:
    adapter = live.ProviderEvidenceAdapter(
        FakeProvider(), target_instrument_id="target"
    )
    from_time = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    to_time = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    adapter.get_candles(
        "target",
        from_time,
        to_time,
        interval="CANDLE_INTERVAL_HOUR",
        limit=None,
    )
    reason(
        "PROVIDER_READ_SCOPE_INVALID",
        adapter.get_candles,
        "target",
        from_time,
        to_time,
        interval="CANDLE_INTERVAL_HOUR",
        limit=None,
    )
    quote = adapter.get_last_prices(["target"])
    assert quote[0]["instrumentUid"] == "target"
    assert quote is not adapter.quote_raw
    reason("PROVIDER_READ_SCOPE_INVALID", adapter.get_last_prices, ["target"])
    adapter.begin_gate_b()
    assert adapter.get_trading_status("target")["tradingStatus"].endswith(
        "NORMAL_TRADING"
    )
    reason("PROVIDER_READ_SCOPE_INVALID", adapter.get_trading_status, "target")
    adapter.post_order_once()
    reason("ATTEMPT_BUDGET_EXHAUSTED", adapter.post_order_once)


def test_provider_adapter_rejects_non_target_before_io() -> None:
    provider = FakeProvider()
    adapter = live.ProviderEvidenceAdapter(provider, target_instrument_id="target")
    reason("PROVIDER_READ_SCOPE_INVALID", adapter.get_last_prices, ["other"])
    assert adapter.quote_calls == 0


def test_provider_adapter_enforces_complete_two_gate_receipt_budget() -> None:
    selected = valid_preparation()
    provider = TelemetryProvider()
    adapter = live.ProviderEvidenceAdapter(
        provider,
        target_instrument_id="target",
        account_id="account",
        account_policy=selected.fields["account_cash_portfolio_policy"],
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_lookback_days=2,
    )
    assert provider._session.max_redirects == 0
    assert hasattr(adapter, "get_orders")
    assert not hasattr(adapter, "get_positions")
    first = adapter.get_portfolio("account")
    second = adapter.get_portfolio("account")
    assert first == second and first is not second
    assert provider.portfolio_reads == 1
    adapter.get_operations_by_cursor_once()
    adapter.get_withdraw_limits("account")
    orders = adapter.get_orders("account")
    assert orders == provider.orders and orders is not provider.orders
    assert {item["instrumentUid"] for item in orders} == {"target", "other"}
    assert (
        adapter.orders_response_canonical_sha256
        == hashlib.sha256(canonical(orders)).hexdigest()
    )
    assert provider.order_reads == 1
    adapter.get_candles(
        "target",
        datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
        datetime(2026, 9, 22, 12, tzinfo=timezone.utc),
        interval="CANDLE_INTERVAL_HOUR",
        limit=None,
    )
    adapter.get_last_prices(["target"])
    adapter.verify_gate_a_receipts()
    adapter.begin_gate_b()
    adapter.get_trading_status("target")
    adapter.get_operations_by_cursor_once()
    adapter.get_portfolio("account")
    adapter.get_withdraw_limits("account")
    adapter.post_order_once()
    adapter.verify_gate_b_receipts(dispatch_status="SUBMITTED")
    assert adapter.operation_calls == {"A": 1, "B": 1}
    assert adapter.portfolio_physical_calls == {"A": 1, "B": 1}
    assert adapter.order_calls == {"A": 1, "B": 0}
    assert all("tracking_id" not in item for item in adapter.telemetry)
    assert all("tracking_id_sha256" in item for item in adapter.telemetry)


def test_provider_adapter_accepts_exact_market_precheck_terminal_prefix() -> None:
    selected = valid_preparation()
    provider = TelemetryProvider()
    adapter = live.ProviderEvidenceAdapter(
        provider,
        target_instrument_id="target",
        account_id="account",
        account_policy=selected.fields["account_cash_portfolio_policy"],
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_lookback_days=2,
    )
    adapter.get_portfolio("account")
    adapter.get_portfolio("account")
    adapter.get_operations_by_cursor_once()
    adapter.get_withdraw_limits("account")
    adapter.get_orders("account")
    adapter.get_candles(
        "target",
        datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
        datetime(2026, 9, 22, 12, tzinfo=timezone.utc),
        interval="CANDLE_INTERVAL_HOUR",
        limit=None,
    )
    adapter.get_last_prices(["target"])
    adapter.begin_gate_b()
    adapter.get_trading_status("target")
    adapter.verify_gate_b_receipts(dispatch_status="MARKET_IDLE")
    assert adapter.post_calls == 0
    assert adapter.operation_calls["B"] == 0
    assert adapter.portfolio_physical_calls["B"] == 0
    assert adapter.gate_b_precheck_evidence == {
        "dispatch_status": "MARKET_IDLE",
        "trading_status_response_sha256": adapter.trading_status_response_sha256,
    }


def test_provider_adapter_order_read_is_gate_a_exactly_once_and_account_wide() -> None:
    selected = valid_preparation()
    provider = TelemetryProvider()
    adapter = live.ProviderEvidenceAdapter(
        provider,
        target_instrument_id="target",
        account_id="account",
        account_policy=selected.fields["account_cash_portfolio_policy"],
    )
    reason("PROVIDER_READ_SCOPE_INVALID", adapter.get_orders, "other-account")
    assert provider.order_reads == 0
    orders = adapter.get_orders("account")
    assert [item["instrumentUid"] for item in orders] == ["target", "other"]
    reason("PROVIDER_READ_SCOPE_INVALID", adapter.get_orders, "account")
    assert provider.order_reads == 1

    class GateBProvider(FakeProvider):
        def get_orders(self, account_id: str) -> list[dict[str, object]]:
            return []

    gate_b_provider = GateBProvider()
    gate_b_adapter = live.ProviderEvidenceAdapter(
        gate_b_provider,
        target_instrument_id="target",
        account_id="account",
        account_policy=selected.fields["account_cash_portfolio_policy"],
    )
    gate_b_adapter.get_orders("account")
    gate_b_adapter.get_candles(
        "target",
        datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
        datetime(2026, 9, 22, 12, tzinfo=timezone.utc),
        interval="CANDLE_INTERVAL_HOUR",
        limit=None,
    )
    gate_b_adapter.get_last_prices(["target"])
    gate_b_adapter.begin_gate_b()
    reason("PROVIDER_READ_SCOPE_INVALID", gate_b_adapter.get_orders, "account")


def test_production_order_validation_binds_account_wide_owner_input(
    monkeypatch,
) -> None:
    selected = valid_preparation()
    raw_response: object = {
        "orders": [
            {
                "instrumentUid": "target",
                "orderRequestId": "target-request",
                "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
            },
            {
                "instrumentUid": "other",
                "orderId": "other-order",
                "executionReportStatus": "EXECUTION_REPORT_STATUS_UNSPECIFIED",
            },
        ]
    }

    def fake_post(self, service, method, payload=None, *, retry_safe=True):
        assert (service, method, payload) == (
            "SandboxService",
            "GetSandboxOrders",
            {"accountId": "account"},
        )
        return raw_response

    monkeypatch.setattr(TBankSandboxClient, "_post", fake_post)
    client = TBankSandboxClient("dummy-token", max_retries=0)
    try:
        provider = live.ProviderEvidenceAdapter(
            client,
            target_instrument_id="target",
            account_id="account",
            account_policy=selected.fields["account_cash_portfolio_policy"],
        )
        orders = provider.get_orders("account")
    finally:
        client.close()

    record = BrokerPortfolioAdapter.from_api_portfolio(
        {"positions": []},
        account_id="account",
        broker_orders=orders,
        snapshot_at="2026-09-22T12:00:00+00:00",
    )
    positions = {item.instrument_id: item for item in record.positions}
    assert set(positions) == {"target", "other"}
    assert positions["target"].pending_orders[0].uncertain is False
    assert positions["other"].pending_orders[0].uncertain is True

    raw_response = {"orders": [{}]}
    client = TBankSandboxClient("dummy-token", max_retries=0)
    try:
        malformed = live.ProviderEvidenceAdapter(
            client,
            target_instrument_id="target",
            account_id="account",
            account_policy=selected.fields["account_cash_portfolio_policy"],
        )
        reason("PROVIDER_READ_FAILED", malformed.get_orders, "account")
        assert malformed.order_calls == {"A": 1, "B": 0}
    finally:
        client.close()


@pytest.mark.parametrize("orders", [None, {}, ["not-a-mapping"]])
def test_provider_adapter_rejects_malformed_order_response(orders: object) -> None:
    selected = valid_preparation()
    provider = TelemetryProvider()
    provider.orders = orders  # type: ignore[assignment]
    adapter = live.ProviderEvidenceAdapter(
        provider,
        target_instrument_id="target",
        account_id="account",
        account_policy=selected.fields["account_cash_portfolio_policy"],
    )
    reason("PROVIDER_READ_INCOMPLETE", adapter.get_orders, "account")


def test_provider_receipt_rejects_stale_operations_identity_and_order_retry() -> None:
    selected = valid_preparation()
    provider = TelemetryProvider()
    adapter = live.ProviderEvidenceAdapter(
        provider,
        target_instrument_id="target",
        account_id="account",
        account_policy=selected.fields["account_cash_portfolio_policy"],
    )
    provider.emit("OperationsService", "GetOperationsByCursor")
    reason("PROVIDER_READ_SCOPE_INVALID", adapter.verify_gate_a_receipts)

    provider = TelemetryProvider()
    adapter = live.ProviderEvidenceAdapter(
        provider,
        target_instrument_id="target",
        account_id="account",
        account_policy=selected.fields["account_cash_portfolio_policy"],
    )
    provider.emit("SandboxService", "GetSandboxOrders", attempt_count=2)
    reason("LIVE_ACQUISITION_BUDGET_EXHAUSTED", adapter.verify_gate_a_receipts)


def test_provider_receipt_retry_or_extra_method_fails_closed() -> None:
    selected = valid_preparation()
    provider = TelemetryProvider()
    adapter = live.ProviderEvidenceAdapter(
        provider,
        target_instrument_id="target",
        account_id="account",
        account_policy=selected.fields["account_cash_portfolio_policy"],
    )
    provider.emit("MarketDataService", "GetOrderBook")
    reason("PROVIDER_READ_SCOPE_INVALID", adapter.verify_gate_a_receipts)

    provider = TelemetryProvider()
    adapter = live.ProviderEvidenceAdapter(
        provider,
        target_instrument_id="target",
        account_id="account",
        account_policy=selected.fields["account_cash_portfolio_policy"],
    )
    provider.emit("MarketDataService", "GetCandles", attempt_count=2)
    reason("LIVE_ACQUISITION_BUDGET_EXHAUSTED", adapter.verify_gate_a_receipts)


@dataclass
class FakeState:
    revision: int
    decision_sha256: str
    current: int

    def position(self, instrument: str) -> Any:
        return None if self.current == 0 else SimpleNamespace(actual_lots=self.current)

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "decision": self.decision_sha256,
            "current": self.current,
        }


class FakeRepository:
    def __init__(self, values: list[FakeState]) -> None:
        self.values = values

    def load(self, *, expected_account_id: str) -> FakeState:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def test_flat_position_zero_is_bound_and_nonzero_is_rejected() -> None:
    zero = FakeState(5, "a" * 64, 0)
    binding = live.bind_flat_position(
        FakeRepository([zero]), account_id="account", target_instrument_id="target"
    )
    assert binding.current_lots == 0
    for current in (1, 2):
        reason(
            "PORTFOLIO_FLAT_POSITION_DRIFT",
            live.bind_flat_position,
            FakeRepository([FakeState(5, "a" * 64, current)]),
            account_id="account",
            target_instrument_id="target",
        )


def test_flat_position_revision_or_position_drift_is_rejected() -> None:
    initial = FakeState(5, "a" * 64, 0)
    repository = FakeRepository([initial, FakeState(6, "b" * 64, 0)])
    binding = live.bind_flat_position(
        repository, account_id="account", target_instrument_id="target"
    )
    reason(
        "PORTFOLIO_FLAT_POSITION_DRIFT",
        live.recheck_flat_position,
        repository,
        account_id="account",
        target_instrument_id="target",
        expected=binding,
    )


def test_exact_admission_requires_one_queued_buy_zero_to_one() -> None:
    candidate = SimpleNamespace(
        current_lots=0,
        target_lots=1,
        requested_lots=1,
        direction="BUY",
    )
    intent = SimpleNamespace(intent_id="intent-private", candidate=candidate)
    before = SimpleNamespace(intents=(), revision=2)
    after = SimpleNamespace(intents=(intent,), queued=(intent,), revision=3)
    coordinator = SimpleNamespace(manager=SimpleNamespace(state=lambda: after))
    result = SimpleNamespace(
        status="QUEUED",
        current_lots=0,
        proposed_target_lots=1,
        approved_target_lots=1,
        intent_id="intent-private",
        cancelled_intent_id=None,
    )
    assert (
        live.validate_admission(result, coordinator=coordinator, before_state=before)
        is intent
    )
    for changed in (
        replace(SimpleResult(), current_lots=1),
        replace(SimpleResult(), approved_target_lots=0),
    ):
        reason(
            "PROPOSAL_ADMISSION_BINDING_INVALID",
            live.validate_admission,
            changed,
            coordinator=coordinator,
            before_state=before,
        )


@dataclass(frozen=True)
class SimpleResult:
    status: str = "QUEUED"
    current_lots: int = 0
    proposed_target_lots: int = 1
    approved_target_lots: int = 1
    intent_id: str = "intent-private"
    cancelled_intent_id: str | None = None


def test_quote_evidence_binds_exact_wire_value_without_raw_target() -> None:
    adapter = live.ProviderEvidenceAdapter(
        FakeProvider(), target_instrument_id="target"
    )
    adapter.get_last_prices(["target"])
    quote = SimpleNamespace(
        source="TBANK_LAST_PRICE_EXCHANGE",
        price_at=datetime(2026, 9, 22, 12, 29, 59, tzinfo=timezone.utc),
        unit_price_rub=103.25,
    )
    result = live.quote_evidence(
        adapter,
        SimpleNamespace(portfolio_risk_candidate_quote=quote),
        now=datetime(2026, 9, 22, 12, 30, tzinfo=timezone.utc),
    )
    assert result["units"] == "103"
    assert result["nano"] == 250000000
    assert ':"target"' not in canonical(result).decode()


@pytest.mark.parametrize(
    "scenario", ["stable", "ledger-drift", "quote-blocked", "synthetic-invalid"]
)
def test_execute_smoke_binds_existing_owners_and_stops_safely_at_market_idle(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    selected = valid_preparation()
    base = proposal()
    monkeypatch.setattr(live, "build_strategy_proposal", lambda *a, **k: base)
    monkeypatch.setattr(
        live,
        "verify_candle_policy_for_profile",
        lambda **kwargs: None,
    )

    class Loader:
        def __init__(self, api: live.ProviderEvidenceAdapter) -> None:
            self.api = api

        def load(self, runtime: Any, profile: Any, *, now: datetime) -> pd.DataFrame:
            return self.api.get_candles(
                "target",
                now.replace(day=20),
                now,
                interval="CANDLE_INTERVAL_HOUR",
                limit=None,
            )

    monkeypatch.setattr(live, "StrategyCandleLoader", Loader)
    provider = live.ProviderEvidenceAdapter(
        FakeProvider(), target_instrument_id="target"
    )
    now = datetime(2026, 9, 22, 12, 30, tzinfo=timezone.utc)
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            account_id="account",
            instrument_id="target",
            candle_interval="CANDLE_INTERVAL_HOUR",
        )
    )
    repository = FakeRepository([FakeState(5, "a" * 64, 0), FakeState(5, "a" * 64, 0)])
    risk_state = SimpleNamespace(
        kill_switch_active=False,
        risk_resync_required=False,
        to_dict=lambda: {"revision": 7, "guard": "READY"},
    )
    risk_runtime = SimpleNamespace(
        mode="SANDBOX_EXECUTION",
        account_id="account",
        current_policy_hash=lambda: VECTORS["risk_policy_sha256"],
        state_store=SimpleNamespace(load_account=lambda account: risk_state),
    )
    authority = SimpleNamespace(
        state=live.RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        post_attempt_count=0,
        pending_dispatch_proof_sha256=None,
        operations_complete_through="2026-09-22T12:29:20.000000000Z",
        ledger_revision=3,
        ledger_head_sha256="1" * 64,
        canonical_bytes=b"authority",
        sha256="9" * 64,
        record_revision=8,
    )
    authority_manager = SimpleNamespace(status=lambda: authority)
    candidate = SimpleNamespace(
        current_lots=0,
        target_lots=1,
        requested_lots=1,
        direction="BUY",
    )
    intent = SimpleNamespace(intent_id="intent-private", candidate=candidate)
    before = SimpleNamespace(intents=(), queued=(), revision=2, reserved_cash_kopecks=0)
    after = SimpleNamespace(
        intents=(intent,), queued=(intent,), revision=3, reserved_cash_kopecks=100
    )

    class Manager:
        active = before

        def state(self) -> Any:
            return self.active

    manager = Manager()
    coordinator = SimpleNamespace(manager=manager)
    profile = object()
    gui_hooks = SimpleNamespace(frames={}, lot_sizes={"target": 10})

    class Hooks:
        admission_binding_raw: bytes | None = None

        def evaluate_closed_candle(
            self, runtime: Any, candle_time: datetime, observed_at: datetime
        ) -> StrategyProposal:
            return proposal_box["proposal"]

        def coordination_request(
            self,
            runtime: Any,
            issued: StrategyProposal,
            candle_time: datetime,
            observed_at: datetime,
        ) -> Any:
            if scenario == "quote-blocked":
                raise live.GuiRuntimeBlockedError("CANDIDATE_QUOTE_READ_FAILED")
            if scenario == "synthetic-invalid":
                raise live.Q7ASyntheticError("COORDINATION_REQUEST_INVALID")
            provider.get_last_prices(["target"])
            if scenario == "ledger-drift":
                ledger.mutate()
            quote = SimpleNamespace(
                source="TBANK_LAST_PRICE_EXCHANGE",
                price_at=datetime(2026, 9, 22, 12, 29, 59, tzinfo=timezone.utc),
                unit_price_rub=103.25,
            )
            return SimpleNamespace(
                candles=gui_hooks.frames["target"],
                proposal=issued,
                profile=profile,
                lot_size=10,
                cash_buffer_bps=100,
                portfolio_risk_candidate_quote=quote,
                evaluated_at=datetime(2026, 9, 22, 12, 30, tzinfo=timezone.utc),
            )

        def coordinate_marked(self, **kwargs: Any) -> SimpleResult:
            manager.active = after
            self.admission_binding_raw = b"admission-binding"
            return SimpleResult()

    hooks = Hooks()
    proposal_box: dict[str, StrategyProposal] = {}

    class FakeLedger:
        def __init__(self) -> None:
            self.store_revision = 5
            self.revision = 3
            self.head = "1" * 64
            self.export = b"ledger-export"

        def snapshot(self) -> Any:
            return SimpleNamespace(
                store_revision=self.store_revision,
                ledger_revision=self.revision,
                ledger_head_sha256=self.head,
            )

        def export_bytes(self) -> bytes:
            return self.export

        def mutate(self) -> None:
            self.store_revision = 6
            self.revision = 4
            self.head = "2" * 64
            self.export = b"ledger-export-drift"

    ledger = FakeLedger()

    class Adapter:
        def __init__(self) -> None:
            self.risk_runtime = risk_runtime
            self.cash_authority_manager = authority_manager
            self.cl7_ledger_store = ledger

        def dispatch_next(self, repository: Any, *, expected_intent_id: str) -> Any:
            assert expected_intent_id == "intent-private"
            provider.get_trading_status("target")
            return SimpleNamespace(status="MARKET_IDLE", order_may_have_been_sent=False)

    context = SimpleNamespace(
        status=SimpleNamespace(value="READY_FOR_LOCKED_REVALIDATION"),
        reason=SimpleNamespace(value="READY"),
        evaluated_at="2026-09-22T12:29:30.000000000Z",
        sha256="8" * 64,
        ledger_export_sha256=hashlib.sha256(b"ledger-export").hexdigest(),
        ledger_revision=3,
        ledger_head_sha256="1" * 64,
        reconciliation_sha256="4" * 64,
        availability_sha256="6" * 64,
        portfolio_evidence_sha256="7" * 64,
        risk_guard_evidence_sha256="a" * 64,
        risk_policy_hash=VECTORS["risk_policy_sha256"],
        risk_state_guard_hash="b" * 64,
    )
    sync_evidence = SimpleNamespace(
        ledger_export_bytes=b"ledger-export",
        broker_cash_proof=SimpleNamespace(
            sha256="2" * 64, response_canonical_sha256="3" * 64
        ),
        broker_withdraw_limits_proof=SimpleNamespace(
            sha256="c" * 64, response_canonical_sha256="d" * 64
        ),
        reconciliation=SimpleNamespace(
            status=SimpleNamespace(value="MATCHED"),
            discrepancy_kind=SimpleNamespace(value="NONE"),
            sha256="4" * 64,
        ),
        reservations=SimpleNamespace(
            sha256="5" * 64,
            central_order_revision=2,
            queued_count=0,
            ambiguous_count=0,
        ),
        availability=SimpleNamespace(
            status=SimpleNamespace(value="READY"),
            availability_reason=SimpleNamespace(value="READY"),
            sha256="6" * 64,
        ),
        portfolio=SimpleNamespace(
            sha256="7" * 64,
            portfolio_revision=5,
            portfolio_decision_checksum="e" * 64,
            portfolio_document_checksum="f" * 64,
            portfolio_snapshot_at="2026-09-22T12:29:25.000000000Z",
        ),
        risk_guard=SimpleNamespace(
            sha256="a" * 64,
            risk_policy_hash=VECTORS["risk_policy_sha256"],
            risk_state_guard_hash="b" * 64,
        ),
        context=context,
    )
    owners = live.LiveOwners(
        preparation=selected,
        configured=SimpleNamespace(identity_sha256=VECTORS["configured_set_sha256"]),
        runtime=runtime,
        profile=profile,
        provider=provider,
        portfolio_repository=repository,
        coordinator=coordinator,
        execution_adapter=Adapter(),
        q7a_hooks=hooks,
        gui_hooks=gui_hooks,
        sync_gate_a=lambda: (authority, sync_evidence),
        clock=lambda: now,
        controlled_proposal_box=proposal_box,
    )
    if scenario == "ledger-drift":
        reason("CL6_CONTEXT_NOT_READY", live.execute_economic_smoke, owners)
        assert manager.active is before
        assert provider.post_calls == 0
        return
    if scenario == "quote-blocked":
        with pytest.raises(live.Q7ALiveError) as caught:
            live.execute_economic_smoke(owners)
        assert caught.value.reason == "PROVIDER_READ_FAILED"
        assert caught.value.dependency_reason == "CANDIDATE_QUOTE_READ_FAILED"
        assert manager.active is before
        assert provider.post_calls == 0
        return
    if scenario == "synthetic-invalid":
        with pytest.raises(live.Q7ASyntheticError) as caught:
            live.execute_economic_smoke(owners)
        assert caught.value.reason == "COORDINATION_REQUEST_INVALID"
        assert manager.active is before
        assert provider.post_calls == 0
        return
    result = live.execute_economic_smoke(owners)
    assert result["outcome_class"] == "PROVIDER_SAFE_REJECTED"
    assert result["provider_read_counts"] == {
        "GetCandles": 1,
        "GetLastPrices": 1,
        "GetSandboxOrders": 0,
        "GetTradingStatus": 1,
    }
    assert result["provider_post_attempts"] == 0
    assert result["pre_admission_ledger_readback"] == {
        "store_revision": 5,
        "ledger_revision": 3,
        "ledger_head_sha256": "1" * 64,
        "ledger_export_sha256": hashlib.sha256(b"ledger-export").hexdigest(),
    }
    assert result["gate_a_owner_evidence"]["cl6_status"] == (
        "READY_FOR_LOCKED_REVALIDATION"
    )
    assert result["gate_a_owner_evidence"]["post_attempt_count"] == 0
    assert (
        result["request_binding_sha256"]
        == hashlib.sha256(
            canonical(
                {
                    "account_scope_sha256": VECTORS["account_scope_sha256"],
                    "configured_set_sha256": VECTORS["configured_set_sha256"],
                    "target_instrument_sha256": VECTORS["target_instrument_sha256"],
                    "candle_frame_sha256": result["candle_frame_sha256"],
                    "controlled_proposal_sha256": result["controlled_proposal_sha256"],
                    "quote_canonical_sha256": result["quote_evidence"][
                        "canonical_sha256"
                    ],
                    "risk_policy_sha256": VECTORS["risk_policy_sha256"],
                    "lot_size": 10,
                    "cash_buffer_bps": 100,
                    "evaluated_at_utc": "2026-09-22T12:30:00.000000Z",
                }
            )
        ).hexdigest()
    )
    assert manager.active.intents == (intent,)


def test_evidence_is_canonical_create_once_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "evidence" / "result.json"
    digest = live.write_evidence_once(path, {"b": 2, "a": 1})
    assert path.read_bytes() == b'{"a":1,"b":2}'
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = json.loads(path.with_name("result.json.manifest.json").read_bytes())
    assert manifest["file_name"] == "result.json"
    assert manifest["size"] == len(path.read_bytes())
    assert manifest["sha256"] == digest
    reason("EVIDENCE_WRITE_FAILED", live.write_evidence_once, path, {"a": 1})


def test_evidence_root_binding_and_preparation_consumption_are_exact(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "evidence").resolve()
    digest = live.evidence_root_identity(root)
    live._verify_evidence_root(root, digest)
    reason("EVIDENCE_WRITE_FAILED", live._verify_evidence_root, root, "f" * 64)
    selected = valid_preparation()
    path = root / "preparation-consumed.json"
    live.consume_preparation_once(path, selected)
    reason(
        "PREPARATION_ALREADY_CONSUMED",
        live.consume_preparation_once,
        path,
        selected,
    )


@pytest.mark.parametrize(
    ("status", "posts", "may_have_been_sent", "expected"),
    [
        ("SUBMITTED", 1, False, "POSTED_AWAITING_TERMINAL_RECONCILIATION"),
        ("SUBMISSION_REJECTED", 1, False, "PROVIDER_SAFE_REJECTED"),
        ("MARKET_IDLE", 0, False, "PROVIDER_SAFE_REJECTED"),
        ("SUBMISSION_UNCERTAIN", 1, True, "PROVIDER_OUTCOME_AMBIGUOUS"),
        (
            "CL7_LOCKED_REVALIDATION_BLOCKED",
            0,
            False,
            "POST_ADMISSION_RECOVERY_REQUIRED",
        ),
    ],
)
def test_dispatch_outcome_classification_is_finite(
    status: str, posts: int, may_have_been_sent: bool, expected: str
) -> None:
    dispatch = SimpleNamespace(
        status=status, order_may_have_been_sent=may_have_been_sent
    )
    assert (
        live.classify_dispatch_outcome(dispatch, provider_post_attempts=posts)
        == expected
    )


def test_validate_preparation_cli_is_offline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw, digest = preparation()
    path = tmp_path / "preparation.json"
    path.write_bytes(raw)
    monkeypatch.setattr(
        live, "_compose_live_owners", lambda *_: pytest.fail("live composition reached")
    )
    assert (
        live.main(
            [
                "VALIDATE_PREPARATION",
                "--preparation",
                str(path),
                "--expected-preparation-sha256",
                digest,
                "--candidate-commit",
                VECTORS["candidate_commit"],
                "--candidate-tree",
                VECTORS["candidate_tree"],
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


@pytest.mark.parametrize(
    ("blocker", "expected"),
    [
        ("CANDIDATE_QUOTE_READ_FAILED", "PROVIDER_READ_FAILED"),
        ("CANDIDATE_QUOTE_INVALID", "QUOTE_OR_METADATA_INVALID"),
        ("CANDIDATE_QUOTE_NOT_FRESH", "QUOTE_OR_METADATA_INVALID"),
        ("CYCLE_CLOCK_INVALID", "QUOTE_OR_METADATA_INVALID"),
        ("DECISION_AUDIT_UNAVAILABLE", "PROPOSAL_ADMISSION_BINDING_INVALID"),
        ("PRIVATE_PROVIDER_CANARY", "POSTCONDITION_FAILED"),
    ],
)
def test_gui_blocker_mapping_is_finite_and_privacy_safe(
    blocker: str, expected: str
) -> None:
    error = live.GuiRuntimeBlockedError(blocker)
    assert live._map_gui_blocker(error) == expected
    assert "PRIVATE" not in live._map_gui_blocker(error)


def test_main_terminalizes_unexpected_exception_without_raw_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = (tmp_path / "runtime").resolve()
    evidence = (tmp_path / "evidence").resolve()
    runtime.mkdir()
    evidence.mkdir()
    raw, digest = preparation(
        evidence_root_sha256=live.evidence_root_identity(evidence)
    )
    prep_path = tmp_path / "preparation.json"
    prep_path.write_bytes(raw)
    monkeypatch.setattr(live, "_compose_live_owners", lambda *_: object())

    def fail_unexpected(_owners: object) -> dict[str, Any]:
        raise RuntimeError("PRIVATE_PROVIDER_CANARY")

    monkeypatch.setattr(live, "execute_economic_smoke", fail_unexpected)
    result = live.main(
        [
            live.LIVE_MODE,
            "--preparation",
            str(prep_path),
            "--expected-preparation-sha256",
            digest,
            "--candidate-commit",
            VECTORS["candidate_commit"],
            "--candidate-tree",
            VECTORS["candidate_tree"],
            "--runtime-dir",
            str(runtime),
            "--evidence-dir",
            str(evidence),
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert "PRIVATE_PROVIDER_CANARY" not in captured.out
    assert json.loads(captured.out) == {
        "reason": "POSTCONDITION_FAILED",
        "status": "BLOCKED",
    }
    terminal = json.loads((evidence / "terminal-blocked.json").read_bytes())
    assert terminal["reason"] == "POSTCONDITION_FAILED"
    assert "PRIVATE_PROVIDER_CANARY" not in json.dumps(terminal)


@pytest.mark.parametrize(
    ("dependency_reason", "primary_reason", "subclass"),
    [
        ("COORDINATION_REQUEST_INVALID", "PROPOSAL_ADMISSION_BINDING_INVALID", False),
        ("COORDINATION_REQUEST_STALE", "QUOTE_OR_METADATA_INVALID", False),
        ("QUOTE_NOT_FRESH", "QUOTE_OR_METADATA_INVALID", False),
        ("PROPOSAL_MARKER_INVALID", "PROPOSAL_ADMISSION_BINDING_INVALID", False),
        ("PRIVATE_ACCOUNT_ID_CANARY", "POSTCONDITION_FAILED", False),
        ("COORDINATION_REQUEST_INVALID", "PROPOSAL_ADMISSION_BINDING_INVALID", True),
        ("PRIVATE_ACCOUNT_ID_CANARY", "POSTCONDITION_FAILED", True),
    ],
)
def test_post_marker_synthetic_failure_records_only_finite_dependency_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    dependency_reason: str,
    primary_reason: str,
    subclass: bool,
) -> None:
    runtime = (tmp_path / "runtime").resolve()
    evidence = (tmp_path / "evidence").resolve()
    runtime.mkdir()
    evidence.mkdir()
    (evidence / "proposal-marker.json").write_bytes(b"offline-marker")
    raw, digest = preparation(
        evidence_root_sha256=live.evidence_root_identity(evidence)
    )
    prep_path = tmp_path / "preparation.json"
    prep_path.write_bytes(raw)
    monkeypatch.setattr(live, "_compose_live_owners", lambda *_: object())

    class SyntheticSubclass(live.Q7ASyntheticError):
        pass

    def fail_after_marker(_owners: object) -> dict[str, Any]:
        error_type = SyntheticSubclass if subclass else live.Q7ASyntheticError
        raise error_type(dependency_reason)

    monkeypatch.setattr(live, "execute_economic_smoke", fail_after_marker)
    result = live.main(
        [
            live.LIVE_MODE,
            "--preparation",
            str(prep_path),
            "--expected-preparation-sha256",
            digest,
            "--candidate-commit",
            VECTORS["candidate_commit"],
            "--candidate-tree",
            VECTORS["candidate_tree"],
            "--runtime-dir",
            str(runtime),
            "--evidence-dir",
            str(evidence),
        ]
    )
    assert result == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "reason": primary_reason,
        "status": "BLOCKED",
    }
    assert captured.err == ""
    assert "PRIVATE_ACCOUNT_ID_CANARY" not in captured.out
    terminal = json.loads((evidence / "terminal-blocked.json").read_bytes())
    assert terminal["reason"] == primary_reason
    assert terminal.get("dependency_reason") == (
        dependency_reason
        if dependency_reason in live._POST_MARKER_SYNTHETIC_REASONS
        else None
    )
    assert "PRIVATE_ACCOUNT_ID_CANARY" not in json.dumps(terminal)
    assert not (evidence / "live-result.json").exists()


def test_post_admission_terminal_preserves_hashed_existing_lineage(
    tmp_path: Path,
) -> None:
    selected = valid_preparation()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    intent = SimpleNamespace(intent_id="PRIVATE-INTENT")
    central = SimpleNamespace(revision=7, intents=(intent,))
    authority = SimpleNamespace(
        sha256="a" * 64,
        record_revision=9,
        post_attempt_count=0,
        pending_dispatch_proof_sha256=None,
    )
    owners = SimpleNamespace(
        coordinator=SimpleNamespace(manager=SimpleNamespace(state=lambda: central)),
        execution_adapter=SimpleNamespace(
            cash_authority_manager=SimpleNamespace(status=lambda: authority)
        ),
    )
    args = SimpleNamespace(evidence_dir=evidence)
    reason_value = live._write_terminal_blocked(
        args=args,
        prep=selected,
        reason="POSTCONDITION_FAILED",
        owners=owners,
    )
    assert reason_value == "EXISTING_INTENT_REQUIRES_RECOVERY"
    terminal = json.loads((evidence / "terminal-blocked.json").read_bytes())
    assert terminal["status"] == "RECOVERY_REQUIRED"
    assert terminal["central_intent_count"] == 1
    assert terminal["pending_dispatch_proof"] is False
    assert "PRIVATE-INTENT" not in json.dumps(terminal)


def test_fixture_finite_reasons_are_closed_taxonomy_members() -> None:
    assert set(VECTORS["finite_reasons"]) <= live._PRIMARY_REASONS
