"""Q7A offline control and real Central/CL7 handoff with a fake provider."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from tools import v3_10_q7a_e2e_smoke as q7a
from trading_robot import broker_read_adapters as broker
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot import cash_ledger_persistence as persistence
from trading_robot import central_order_manager as central_module
from trading_robot import runtime_cash_authority as cl7
from trading_robot import tbank_sandbox
from trading_robot.bot import BotConfig
from trading_robot.cash_ledger_domain import Money
from trading_robot.central_order_coordinator import CentralOrderCoordinator
from trading_robot.central_order_manager import (
    CentralOrderCandidate,
    CentralOrderConflictError,
    CentralOrderManager,
    CentralOrderStore,
    ExecutionAuthorization,
    central_reservation_projection_hash,
)
from trading_robot.config_persistence import bot_config_to_profile
from trading_robot.gui_runtime_controller import (
    ConfiguredExecutionSet,
    ConfiguredRuntimeBinding,
    GuiCoordinationRequest,
)
from trading_robot.instrument_runtime import InstrumentRuntime
from trading_robot.multi_instrument_config import MultiInstrumentProfile
from trading_robot.multi_instrument_strategy import StrategyProposal
from trading_robot.portfolio_manager import CanonicalPortfolioManager
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    PortfolioState,
    ReconciliationStatus,
    SnapshotFreshness,
)
from trading_robot.portfolio_preflight import PortfolioSnapshotLease
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_read_service import load_portfolio_risk_metadata
from trading_robot.portfolio_risk_runtime import PortfolioRiskRuntime
from trading_robot.portfolio_risk_shadow import PortfolioRiskCandidateQuote
from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_runtime import (
    RiskDispatchAuthorizationError,
    RiskRuntimeAdapter,
)
from trading_robot.sandbox_execution_adapter import (
    SandboxExecutionAdapter,
    SandboxExecutionPolicy,
)
from trading_robot.state_persistence import atomic_write_json
from trading_robot.strategy_runtime import StrategyDecision

ROOT = Path(__file__).resolve().parents[2]
VECTORS = json.loads(
    (ROOT / "current/tests/fixtures/v3_10_q7a_e2e_smoke_vectors.json").read_text(
        encoding="utf-8"
    )
)
ACCOUNT = "sandbox-account-0001"  # synthetic only; never shareable evidence
KEY = bytes(range(32))
KEY_ID = "CL5_TEST_KEY_V1"
T0 = "2026-09-11T10:00:00.000000000Z"
T1 = "2026-09-11T10:00:01.000000000Z"
T2 = "2026-09-11T10:00:02.000000000Z"
T3 = "2026-09-11T10:00:03.000000000Z"
T4 = "2026-09-11T10:00:04.000000000Z"
T5 = "2026-09-11T10:00:05.000000000Z"
T6 = "2026-09-11T10:00:06.000000000Z"
T7 = "2026-09-11T10:00:07.000000000Z"
T8 = "2026-09-11T10:00:08.000000000Z"
T9 = "2026-09-11T10:00:09.000000000Z"


def _profile(ticker: str, interval: str) -> MultiInstrumentProfile:
    config = BotConfig(
        ticker=ticker,
        class_code="TQBR",
        candle_interval=interval,
        primary_strategy="sma",
        shadow_strategies=(),
        fast_window=2,
        slow_window=5,
        volatility_window=5,
        lookback_days=5,
        max_order_lots=1,
    )
    return MultiInstrumentProfile(
        instrument_id=f"uid-{ticker.lower()}",
        strategy_profile=bot_config_to_profile(
            config, connect_timeout_seconds=8, read_timeout_seconds=25
        ),
    )


def _configured(
    scope: str | None = None, *, active: bool = False
) -> ConfiguredExecutionSet:
    profiles = (
        _profile("SBER", "CANDLE_INTERVAL_HOUR"),
        _profile("LKOH", "CANDLE_INTERVAL_30_MIN"),
    )
    return ConfiguredExecutionSet(
        account_scope_sha256=scope or VECTORS["account_scope_sha256"],
        bindings=tuple(
            ConfiguredRuntimeBinding(
                profile,
                InstrumentRuntime(
                    profile.to_runtime_config(ACCOUNT),
                    status="ACTIVE" if active else "STOPPED",
                ),
            )
            for profile in profiles
        ),
    )


def _metadata(root: Path, *, currency: str | None = "RUB", lot_size: int = 10) -> Path:
    path = root / "portfolio_risk_metadata.json"
    atomic_write_json(
        path,
        {
            "version": 1,
            "instruments": [
                {
                    "instrument_id": "uid-sber",
                    "lot_size": lot_size,
                    "asset_class": "EQUITY",
                    "currency": currency,
                },
                {
                    "instrument_id": "uid-lkoh",
                    "lot_size": 1,
                    "asset_class": "EQUITY",
                    "currency": "RUB",
                },
            ],
        },
        write_checksum=True,
    )
    return path


def _record(configured: ConfiguredExecutionSet, metadata_path: Path):
    return q7a.build_control_record(
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        configured=configured,
        target_instrument_id=VECTORS["target_instrument_id"],
        metadata_path=metadata_path,
        created_at=VECTORS["created_at"],
    )


def _verify(raw: bytes, configured: ConfiguredExecutionSet, path: Path):
    return q7a.verify_control_record(
        raw,
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        configured=configured,
        metadata_path=path,
    )


def _reason(expected: str, call, *args, **kwargs) -> None:
    with pytest.raises(q7a.Q7ASyntheticError) as captured:
        call(*args, **kwargs)
    assert captured.value.reason == expected
    assert ACCOUNT not in str(captured.value)


def test_contract_fixture_and_canonical_control_custody(tmp_path: Path) -> None:
    assert VECTORS["accepted_contract_commit"] == q7a.CONTRACT_COMMIT
    assert VECTORS["accepted_contract_tree"] == q7a.CONTRACT_TREE
    assert VECTORS["configured_instruments"] == ["uid-sber", "uid-lkoh"]
    configured = _configured()
    path = _metadata(tmp_path)
    record = _record(configured, path)
    fields = record.fields
    assert (
        fields["record_sha256"]
        == hashlib.sha256(
            json.dumps(
                {key: value for key, value in fields.items() if key != "record_sha256"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    )
    assert _verify(record.raw, configured, path).raw == record.raw
    assert fields["configured_set_sha256"] == configured.identity_sha256
    assert fields["target_lot_size"] == VECTORS["target_lot_size"]
    assert (
        fields["target_runtime_key_sha256"]
        == hashlib.sha256(
            configured.bindings[0].runtime.runtime_key.encode("utf-8")
        ).hexdigest()
    )
    assert ACCOUNT.encode() not in record.raw
    assert b"CONTROLLED_Q7A_ONLY" in record.raw
    assert b"execution_authorized" not in record.raw
    output = tmp_path / "private" / "q7a-control.json"
    assert (
        q7a.write_control_record_once(output, record)
        == hashlib.sha256(record.raw).hexdigest()
    )
    _reason("CONTROL_ALREADY_EXISTS", q7a.write_control_record_once, output, record)


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"requested_target_lots": True}, "CONTROL_BUDGET_INVALID"),
        ({"requested_target_lots": 2}, "CONTROL_BUDGET_INVALID"),
        ({"max_provider_post_attempts": 2}, "CONTROL_BUDGET_INVALID"),
        ({"automatic_retries": 1}, "CONTROL_BUDGET_INVALID"),
        ({"candidate_commit": "f" * 40}, "CONTROL_IDENTITY_MISMATCH"),
        ({"target_instrument_id": "uid-other"}, "TARGET_NOT_CONFIGURED"),
        ({"account_scope_sha256": "f" * 64}, "CONTROL_BINDING_MISMATCH"),
        ({"configured_set_sha256": "f" * 64}, "CONTROL_BINDING_MISMATCH"),
        ({"target_runtime_key_sha256": "f" * 64}, "CONTROL_BINDING_MISMATCH"),
        ({"metadata_sha256": "f" * 64}, "CONTROL_BINDING_MISMATCH"),
    ],
)
def test_control_record_rejects_rehashed_substitution(
    tmp_path: Path, change: dict[str, object], expected: str
) -> None:
    configured = _configured()
    path = _metadata(tmp_path)
    fields = _record(configured, path).fields
    fields.update(change)
    preimage = {key: value for key, value in fields.items() if key != "record_sha256"}
    fields["record_sha256"] = hashlib.sha256(
        json.dumps(preimage, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    _reason(expected, _verify, raw, configured, path)


def test_control_rejects_noncanonical_tamper_and_metadata_drift(tmp_path: Path) -> None:
    configured = _configured()
    path = _metadata(tmp_path)
    record = _record(configured, path)
    _reason("CONTROL_NOT_CANONICAL", _verify, record.raw + b" ", configured, path)
    _reason("CONTROL_ENCODING_INVALID", _verify, record.raw + b"\n", configured, path)
    _reason(
        "CONTROL_DUPLICATE_FIELD",
        _verify,
        record.raw.replace(b'"version":1', b'"version":1,"version":1'),
        configured,
        path,
    )
    _reason(
        "CONTROL_HASH_MISMATCH",
        _verify,
        record.raw.replace(b'"automatic_retries":0', b'"automatic_retries":1'),
        configured,
        path,
    )
    _reason(
        "CONTROL_BINDING_MISMATCH",
        _verify,
        record.raw,
        _configured("f" * 64),
        path,
    )
    _metadata(tmp_path, lot_size=11)
    _reason("CONTROL_BINDING_MISMATCH", _verify, record.raw, configured, path)
    _metadata(tmp_path, currency="USD")
    _reason("TARGET_CURRENCY_NOT_RUB", _verify, record.raw, configured, path)
    _metadata(tmp_path, currency="")
    _reason("TARGET_CURRENCY_NOT_RUB", _verify, record.raw, configured, path)
    _metadata(tmp_path, currency=None)
    _reason("TARGET_CURRENCY_NOT_RUB", _verify, record.raw, configured, path)
    path.with_name(path.name + ".sha256").unlink()
    _reason("METADATA_UNVERIFIED", _verify, record.raw, configured, path)


def test_metadata_race_between_verified_load_and_hash_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = _configured()
    path = _metadata(tmp_path)
    original = q7a.load_portfolio_risk_metadata

    def racing_load(selected_path: Path):
        result = original(selected_path)
        selected_path.write_bytes(selected_path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(q7a, "load_portfolio_risk_metadata", racing_load)
    _reason("METADATA_UNVERIFIED", _record, configured, path)


def _proposal(runtime: InstrumentRuntime) -> StrategyProposal:
    return StrategyProposal(
        runtime_key=runtime.runtime_key,
        instrument_id=runtime.config.instrument_id,
        ticker=runtime.config.ticker,
        candle_interval=runtime.config.candle_interval,
        candle_time=T6,
        strategy_profile_hash=runtime.config.strategy_config_hash,
        primary_strategy=runtime.config.strategy_id,
        primary_target_lots=1,
        decisions={},
        comparison={},
        generated_at=T6,
    )


class _Hooks:
    def __init__(
        self,
        profile: MultiInstrumentProfile,
        *,
        quote: object = ...,
        evaluated_at: datetime = datetime(2026, 9, 20, tzinfo=timezone.utc),
    ):
        self.profile = profile
        self.evaluated_at = evaluated_at
        self.quote = (
            PortfolioRiskCandidateQuote(
                unit_price_rub=100.0,
                price_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
                source="SYNTHETIC_QUOTE",
            )
            if quote is ...
            else quote
        )
        self.coordination_calls = 0
        self.evaluation_calls = 0

    def refresh_market_status(self, *_args):
        return "OPEN"

    def refresh_risk(self, *_args):
        return "READY"

    def reconcile_portfolio(self, *_args):
        return "MATCHED"

    def evaluate_closed_candle(self, runtime, *_args):
        self.evaluation_calls += 1
        return _proposal(runtime)

    def coordination_request(self, runtime, proposal, *_args):
        self.coordination_calls += 1
        return GuiCoordinationRequest(
            proposal=proposal,
            profile=self.profile,
            candles=None,
            lot_size=10,
            portfolio_risk_candidate_quote=self.quote,
            evaluated_at=self.evaluated_at,
        )


def test_controlled_hooks_preserve_normal_request_and_block_non_target(
    tmp_path: Path,
) -> None:
    configured = _configured()
    path = _metadata(tmp_path)
    record = _record(configured, path)
    target, other = (binding.runtime for binding in configured.bindings)
    delegate = _Hooks(configured.bindings[0].profile)
    hooks = q7a.Q7AControlledHooks(
        delegate=delegate,
        configured=configured,
        record=record,
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        metadata_path=path,
        proposal_marker_path=tmp_path / "private" / "proposal-marker.json",
        controlled_proposal=lambda runtime, *_args: _proposal(runtime),
    )
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    other_proposal = hooks.evaluate_closed_candle(other, now, now)
    assert delegate.evaluation_calls == 1
    _reason(
        "NON_TARGET_COORDINATION_FORBIDDEN",
        hooks.coordination_request,
        other,
        other_proposal,
        now,
        now,
    )
    assert delegate.coordination_calls == 0
    target_proposal = hooks.evaluate_closed_candle(target, now, now)
    marker = (tmp_path / "private" / "proposal-marker.json").read_bytes()
    assert ACCOUNT.encode() not in marker
    assert record.record_sha256.encode() in marker
    assert (
        hashlib.sha256(
            json.dumps(
                target_proposal.to_dict(), sort_keys=True, separators=(",", ":")
            ).encode()
        )
        .hexdigest()
        .encode()
        in marker
    )
    request = hooks.coordination_request(target, target_proposal, now, now)
    assert request.proposal is target_proposal
    assert delegate.coordination_calls == 1
    assert (
        hooks.proposal_sha256
        == hashlib.sha256(
            json.dumps(
                target_proposal.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert "control_mode" not in target_proposal.to_dict()
    _reason(
        "SECOND_LIFECYCLE_FORBIDDEN", hooks.evaluate_closed_candle, target, now, now
    )
    _reason(
        "SECOND_LIFECYCLE_FORBIDDEN",
        hooks.coordination_request,
        target,
        target_proposal,
        now,
        now,
    )
    restarted = q7a.Q7AControlledHooks(
        delegate=delegate,
        configured=configured,
        record=record,
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        metadata_path=path,
        proposal_marker_path=tmp_path / "private" / "proposal-marker.json",
        controlled_proposal=lambda runtime, *_args: _proposal(runtime),
    )
    _reason(
        "PROPOSAL_ALREADY_ISSUED", restarted.evaluate_closed_candle, target, now, now
    )


def test_missing_quote_and_tampered_record_fail_before_central(tmp_path: Path) -> None:
    configured = _configured()
    path = _metadata(tmp_path)
    record = _record(configured, path)
    target = configured.bindings[0].runtime
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    delegate = _Hooks(configured.bindings[0].profile, quote=None)
    hooks = q7a.Q7AControlledHooks(
        delegate=delegate,
        configured=configured,
        record=record,
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        metadata_path=path,
        proposal_marker_path=tmp_path / "private" / "proposal-marker.json",
        controlled_proposal=lambda runtime, *_args: _proposal(runtime),
    )
    _reason(
        "CONTROLLED_PROPOSAL_INVALID",
        hooks.coordination_request,
        target,
        _proposal(target),
        now,
        now,
    )
    issued = hooks.evaluate_closed_candle(target, now, now)
    _reason(
        "COORDINATION_REQUEST_INVALID",
        hooks.coordination_request,
        target,
        issued,
        now,
        now,
    )
    assert hooks.proposal_sha256 is None
    delegate.quote = PortfolioRiskCandidateQuote(
        unit_price_rub=100.0,
        price_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        source="SYNTHETIC_QUOTE",
    )
    delegate.evaluated_at = datetime(2026, 9, 19, tzinfo=timezone.utc)
    _reason(
        "COORDINATION_REQUEST_STALE",
        hooks.coordination_request,
        target,
        issued,
        now,
        now,
    )
    delegate.evaluated_at = now
    delegate.quote = PortfolioRiskCandidateQuote(
        unit_price_rub=100.0,
        price_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        source="SYNTHETIC_QUOTE",
    )
    _reason(
        "QUOTE_NOT_FRESH",
        hooks.coordination_request,
        target,
        issued,
        now,
        now,
    )
    assert hooks.proposal_sha256 is None
    _reason(
        "CONTROL_HASH_MISMATCH",
        q7a.Q7AControlledHooks,
        delegate=delegate,
        configured=configured,
        record=q7a.Q7AControlRecord(
            record.raw.replace(b'"automatic_retries":0', b'"automatic_retries":1')
        ),
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        metadata_path=path,
        proposal_marker_path=tmp_path / "private" / "proposal-marker.json",
        controlled_proposal=lambda runtime, *_args: _proposal(runtime),
    )


def test_proposal_marker_tamper_blocks_coordination(tmp_path: Path) -> None:
    configured = _configured()
    path = _metadata(tmp_path)
    marker_path = tmp_path / "private" / "proposal-marker.json"
    target = configured.bindings[0].runtime
    delegate = _Hooks(configured.bindings[0].profile)
    hooks = q7a.Q7AControlledHooks(
        delegate=delegate,
        configured=configured,
        record=_record(configured, path),
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        metadata_path=path,
        proposal_marker_path=marker_path,
        controlled_proposal=lambda runtime, *_args: _proposal(runtime),
    )
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    earlier = datetime(2026, 9, 19, tzinfo=timezone.utc)
    _reason(
        "CONTROL_TIME_INVALID", hooks.evaluate_closed_candle, target, earlier, earlier
    )
    assert not marker_path.exists()
    issued = hooks.evaluate_closed_candle(target, now, now)
    marker_path.write_bytes(marker_path.read_bytes() + b" ")
    _reason(
        "PROPOSAL_MARKER_INVALID", hooks.coordination_request, target, issued, now, now
    )
    assert delegate.coordination_calls == 0
    assert hooks.proposal_sha256 is None


def test_nested_proposal_mutation_cannot_escape_marker_binding(tmp_path: Path) -> None:
    configured = _configured()
    metadata_path = _metadata(tmp_path)
    target = configured.bindings[0].runtime
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    delegate = _Hooks(configured.bindings[0].profile)
    hooks = q7a.Q7AControlledHooks(
        delegate=delegate,
        configured=configured,
        record=_record(configured, metadata_path),
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        metadata_path=metadata_path,
        proposal_marker_path=tmp_path / "private" / "proposal-marker.json",
        controlled_proposal=lambda runtime, *_args: _proposal(runtime),
    )
    proposal = hooks.evaluate_closed_candle(target, now, now)
    _reason(
        "PROPOSAL_MARKER_INVALID",
        proposal.comparison.__setitem__,
        "changed_after_marker",
        "untrusted",
    )
    _reason(
        "PROPOSAL_MARKER_INVALID",
        proposal.decisions.__setitem__,
        "changed_after_marker",
        1,
    )
    assert delegate.coordination_calls == 0
    assert hooks.proposal_sha256 is None
    request = hooks.coordination_request(target, proposal, now, now)
    assert request.proposal is proposal
    assert (
        hooks.proposal_sha256
        == hashlib.sha256(q7a._canonical(proposal.to_dict())).hexdigest()
    )


def test_delegate_cannot_mutate_proposal_during_coordination(tmp_path: Path) -> None:
    class MutatingHooks(_Hooks):
        def coordination_request(self, runtime, proposal, *args):
            request = super().coordination_request(runtime, proposal, *args)
            proposal.comparison["changed_during_coordination"] = "untrusted"
            return request

    configured = _configured()
    metadata_path = _metadata(tmp_path)
    target = configured.bindings[0].runtime
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    delegate = MutatingHooks(configured.bindings[0].profile)
    hooks = q7a.Q7AControlledHooks(
        delegate=delegate,
        configured=configured,
        record=_record(configured, metadata_path),
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        metadata_path=metadata_path,
        proposal_marker_path=tmp_path / "private" / "proposal-marker.json",
        controlled_proposal=lambda runtime, *_args: _proposal(runtime),
    )
    proposal = hooks.evaluate_closed_candle(target, now, now)
    _reason(
        "PROPOSAL_MARKER_INVALID",
        hooks.coordination_request,
        target,
        proposal,
        now,
        now,
    )
    assert delegate.coordination_calls == 1
    assert hooks.proposal_sha256 is None


def _armed_chain(
    root: Path,
    *,
    ledger_revision: int,
    ledger_head_sha256: str,
    opening_record_sha256: str = "7" * 64,
):
    store = cl7.RuntimeCashAuthorityStore(root)
    manager = cl7.RuntimeCashAuthorityManager(store)
    current = store.bootstrap(transition_at=T0)
    transitions = (
        (
            T1,
            "PREPARE_CUTOVER",
            cl7.RuntimeCashAuthorityState.CUTOVER_PREPARED,
            {
                "cutover_generation": 1,
                "account_scope_sha256": VECTORS["account_scope_sha256"],
                "identity_key_id": KEY_ID,
            },
        ),
        (
            T2,
            "PREPARATION_EVIDENCE_BOUND",
            cl7.RuntimeCashAuthorityState.CUTOVER_PREPARED,
            {
                "ledger_revision": ledger_revision,
                "ledger_head_sha256": ledger_head_sha256,
                "opening_cutoff": T0,
                "opening_record_sha256": opening_record_sha256,
                "operations_complete_through": "2026-09-11T10:00:00.000000001Z",
            },
        ),
        (T3, "CONFIRM_CUTOVER", cl7.RuntimeCashAuthorityState.CUTOVER_CONFIRMED, {}),
        (
            T4,
            "ACTIVATE_EXACT",
            cl7.RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
            {
                "ever_exact_activated": True,
                "activation_context_sha256": "f" * 64,
            },
        ),
    )
    for at, kind, state, changes in transitions:
        candidate = manager._change(current, at=at, kind=kind, state=state, **changes)
        with store.locked():
            current = store._commit_unlocked(
                candidate,
                expected_revision=current.record_revision,
                expected_sha256=current.sha256,
            )
    current = manager.arm(
        raw_account_id=ACCOUNT,
        identity_key=KEY,
        identity_key_id=KEY_ID,
        confirmation=manager.ARM_PHRASE,
        transition_at=T5,
    )
    return manager, current


def _central(
    root: Path,
    *,
    risk_policy_hash: str = "d" * 64,
    risk_guard_hash: str = "e" * 64,
    enqueue: bool = True,
    canonical_time: bool = False,
):
    snapshot_at = (
        datetime.fromisoformat(T5.replace("Z", "+00:00")).isoformat()
        if canonical_time
        else T5
    )
    state = PortfolioState(
        version=2,
        account=AccountState(
            account_id=ACCOUNT,
            total_value=1_000_000.0,
            securities_value=0.0,
            expected_yield=0.0,
            cash_balances=(CashBalance("rub", 1_000_000.0),),
        ),
        snapshot_at=snapshot_at,
        generated_at=snapshot_at,
        freshness=SnapshotFreshness.FRESH,
        source="PORTFOLIO_MANAGER",
        positions=(),
        warnings=(),
        state_status="READY",
        blocking=False,
        revision=9,
    )
    repository = PortfolioRepository(root / "portfolio_state.json")
    repository.save(state)
    central = CentralOrderManager(
        CentralOrderStore(root / "central_order_state.json"), account_id=ACCOUNT
    )
    if not enqueue:
        return repository, central, None
    lease = PortfolioSnapshotLease.from_state(state, leased_at=T5)
    authorization = ExecutionAuthorization(
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
        risk_decision_id="synthetic-risk-decision",
        risk_policy_hash=risk_policy_hash,
        risk_order_allowed=True,
        authorized_at=T5,
        risk_state_guard_hash=risk_guard_hash,
    )
    candidate = CentralOrderCandidate(
        account_id=ACCOUNT,
        instrument_id="uid-sber",
        ticker="SBER",
        runtime_key="synthetic-runtime-sber",
        runtime_config_hash="a" * 64,
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_time=T5,
        strategy_id="sma",
        strategy_profile_hash="c" * 64,
        current_lots=0,
        target_lots=1,
        estimated_price_kopecks=10_000,
        lot_size=10,
        created_at=T5,
    )
    intent = central.enqueue(candidate, authorization).intent
    return repository, central, intent


def _owner_admission_setup(root: Path):
    """Prepare the real Risk/Portfolio Risk/Central owners before admission."""

    configured = _configured(active=True)
    metadata_path = _metadata(root)
    repository, central, _ = _central(root, enqueue=False, canonical_time=True)
    profile_store = RiskProfileStore(root / "risk_profiles.json")
    profile_store.confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        RiskPolicy(
            cash_reserve_rub=0.0,
            daily_loss_limit_rub=None,
            daily_loss_limit_fraction=None,
            weekly_loss_limit_rub=None,
            weekly_loss_limit_fraction=None,
            max_drawdown_fraction=None,
            max_daily_turnover_rub=None,
            max_daily_turnover_fraction=None,
            max_orders_per_day=None,
            risk_per_trade_rub=None,
            risk_per_trade_fraction=None,
            max_position_share_of_equity=1.0,
            max_position_value_rub=1_000_000.0,
            max_order_value_rub=1_000_000.0,
            commission_buffer_fraction=0.0,
            portfolio_policy_configured=True,
            portfolio_policy_mode="ENFORCED",
            max_snapshot_age_seconds=None,
            max_price_age_seconds=None,
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
    )
    state_store = RiskStateStore(root / "risk_state.json")
    risk = RiskRuntimeAdapter(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=profile_store,
        state_store=state_store,
    )
    portfolio_risk = PortfolioRiskRuntime(
        account_id=ACCOUNT,
        profile_store=profile_store,
        state_store=state_store,
        instrument_metadata=load_portfolio_risk_metadata(metadata_path),
    )
    target = configured.bindings[0].runtime
    profile = configured.bindings[0].profile
    now = datetime.fromisoformat(T6.replace("Z", "+00:00"))
    decision = StrategyDecision(
        candle_time=T6,
        strategy_id="sma",
        strategy_version="1",
        role="PRIMARY",
        config_hash=profile.strategy_profile_hash,
        signal=1,
        target_weight=1.0,
        target_lots=1,
        reason="synthetic controlled candidate",
        indicators={"close": 100.0},
        bars_used=20,
        required_bars=5,
    )

    def controlled(runtime, *_args):
        return StrategyProposal(
            runtime_key=runtime.runtime_key,
            instrument_id=runtime.config.instrument_id,
            ticker=runtime.config.ticker,
            candle_interval=runtime.config.candle_interval,
            candle_time=T6,
            strategy_profile_hash=runtime.config.strategy_config_hash,
            primary_strategy="sma",
            primary_target_lots=1,
            decisions={"sma": decision},
            comparison={
                "signals": {"sma": 1},
                "target_lots": {"sma": 1},
                "disagreeing_strategies": [],
            },
            generated_at=T6,
        )

    closes = [100.0 + step for step in range(20)]
    candles = pd.DataFrame(
        {
            "open": closes,
            "high": [value + 1.0 for value in closes],
            "low": [value - 1.0 for value in closes],
            "close": closes,
        },
        index=pd.date_range(end=now, periods=20, freq="h"),
    )

    class EconomicHooks(_Hooks):
        def coordination_request(self, runtime, proposal, *args):
            return replace(
                super().coordination_request(runtime, proposal, *args),
                candles=candles,
            )

    record = q7a.build_control_record(
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        configured=configured,
        target_instrument_id=target.config.instrument_id,
        metadata_path=metadata_path,
        created_at="2026-09-11T10:00:05.000000Z",
    )
    hooks = q7a.Q7AControlledHooks(
        delegate=EconomicHooks(
            profile,
            quote=PortfolioRiskCandidateQuote(
                unit_price_rub=100.0,
                price_at=now,
                source="SYNTHETIC_QUOTE",
            ),
            evaluated_at=now,
        ),
        configured=configured,
        record=record,
        candidate_commit=VECTORS["candidate_commit"],
        candidate_tree=VECTORS["candidate_tree"],
        metadata_path=metadata_path,
        proposal_marker_path=root / "q7a-proposal-marker.json",
        controlled_proposal=controlled,
    )
    proposal = hooks.evaluate_closed_candle(target, now, now)
    assert q7a._canonical(proposal.to_dict()) == q7a._canonical(
        controlled(target, now, now).to_dict()
    )
    request = hooks.coordination_request(target, proposal, now, now)
    return (
        record,
        hooks,
        proposal,
        risk,
        portfolio_risk,
        repository,
        central,
        target,
        request,
    )


def _connected_owner_admission(
    root: Path, *, probe_post_hook_mutation: bool = False, via_bridge: bool = True
):
    """Use the accepted Risk/Portfolio Risk coordinator for the control proposal."""

    (
        record,
        hooks,
        proposal,
        risk,
        portfolio_risk,
        repository,
        central,
        target,
        request,
    ) = _owner_admission_setup(root)
    if probe_post_hook_mutation:
        marked_sha256 = hooks.proposal_sha256
        assert marked_sha256 is not None
        for target_map in (
            proposal.decisions,
            proposal.decisions["sma"].indicators,
            proposal.comparison,
            proposal.comparison["signals"],
        ):
            assert not isinstance(target_map, dict)
            with pytest.raises(TypeError):
                dict.__setitem__(target_map, "close", 101.0)
            with pytest.raises((AttributeError, TypeError)):
                object.__setattr__(target_map, "_items", (("close", 101.0),))
        assert hashlib.sha256(q7a._canonical(proposal.to_dict())).hexdigest() == (
            marked_sha256
        )
    coordinator = CentralOrderCoordinator(
        central, repository, risk, portfolio_risk_runtime=portfolio_risk
    )
    if via_bridge:
        result = hooks.coordinate_marked(
            coordinator=coordinator, runtime=target, request=request
        )
    else:
        result = coordinator.coordinate(
            request.proposal,
            target,
            request.profile,
            candles=request.candles,
            lot_size=request.lot_size,
            now=request.evaluated_at,
            portfolio_risk_candidate_quote=request.portfolio_risk_candidate_quote,
        )
    assert result.status == "QUEUED"
    intent = central.state().queued[0]
    assert result.intent_id == intent.intent_id
    assert intent.candidate.runtime_key == proposal.runtime_key
    assert intent.candidate.strategy_profile_hash == proposal.strategy_profile_hash
    assert datetime.fromisoformat(
        intent.candidate.candle_time
    ) == datetime.fromisoformat(proposal.candle_time)
    assert intent.authorization.portfolio_risk is not None
    assert intent.authorization.portfolio_risk.finalized is True
    assert hooks.proposal_sha256 is not None
    return record, hooks, proposal, risk, portfolio_risk, repository, central, intent


def test_post_hook_dict_base_mutation_cannot_change_central_admission(
    tmp_path: Path,
) -> None:
    _, hooks, proposal, _, _, _, central, intent = _connected_owner_admission(
        tmp_path, probe_post_hook_mutation=True
    )
    assert intent.status == "QUEUED"
    assert central.state().queued[0].intent_id == intent.intent_id
    assert (
        hooks.proposal_sha256
        == hashlib.sha256(q7a._canonical(proposal.to_dict())).hexdigest()
    )


@pytest.mark.parametrize("mutation", ["target", "nested_price", "authority_bit"])
def test_post_marker_public_mutation_rejected_before_owner_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    (
        _,
        hooks,
        proposal,
        risk,
        portfolio_risk,
        repository,
        central,
        target,
        request,
    ) = _owner_admission_setup(tmp_path)
    before_central = central.state()
    before_risk = risk.state_store.load_account(ACCOUNT)
    if mutation == "nested_price":
        object.__setattr__(proposal.decisions["sma"], "indicators", {"close": 101.0})
    elif mutation == "target":
        object.__setattr__(proposal, "primary_target_lots", 0)
    else:
        # Production to_dict masks this field, but Central reads the live bit.
        object.__setattr__(proposal, "execution_authorized", True)
    coordinator = CentralOrderCoordinator(
        central, repository, risk, portfolio_risk_runtime=portfolio_risk
    )
    calls = 0
    original = CentralOrderCoordinator.coordinate

    def counted(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CentralOrderCoordinator, "coordinate", counted)
    _reason(
        "Q7A_PROPOSAL_DRIFT",
        hooks.coordinate_marked,
        coordinator=coordinator,
        runtime=target,
        request=request,
    )
    assert calls == 0
    assert central.state() == before_central
    assert central.state().queued == ()
    assert risk.state_store.load_account(ACCOUNT) == before_risk
    assert hooks.admission_binding_raw is None
    assert not (tmp_path / "q7a-synthetic-lineage.json").exists()
    assert not (tmp_path / "runtime_cash_authority.json").exists()


def test_late_public_mutation_cannot_change_real_central_risk_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        _,
        hooks,
        public,
        risk,
        portfolio_risk,
        repository,
        central,
        target,
        request,
    ) = _owner_admission_setup(tmp_path)
    coordinator = CentralOrderCoordinator(
        central, repository, risk, portfolio_risk_runtime=portfolio_risk
    )
    observed_prices: list[float] = []
    observed_private: list[StrategyProposal] = []
    actual_coordinate = CentralOrderCoordinator.coordinate
    actual_evaluate = risk.evaluate

    def evaluate_with_price(**kwargs):
        observed_prices.append(kwargs["price_rub"])
        return actual_evaluate(**kwargs)

    def coordinate_after_public_mutation(self, private, *args, **kwargs):
        observed_private.append(private)
        assert private is not public
        assert private.decisions is not public.decisions
        assert private.decisions["sma"] is not public.decisions["sma"]
        assert (
            private.decisions["sma"].indicators
            is not public.decisions["sma"].indicators
        )
        assert private.comparison is not public.comparison
        assert private.comparison["signals"] is not public.comparison["signals"]
        object.__setattr__(public.decisions["sma"], "indicators", {"close": 101.0})
        object.__setattr__(public, "primary_target_lots", 0)
        assert private.decisions["sma"].indicators["close"] == 100.0
        assert private.primary_target_lots == 1
        return actual_coordinate(self, private, *args, **kwargs)

    monkeypatch.setattr(risk, "evaluate", evaluate_with_price)
    monkeypatch.setattr(
        CentralOrderCoordinator, "coordinate", coordinate_after_public_mutation
    )
    result = hooks.coordinate_marked(
        coordinator=coordinator, runtime=target, request=request
    )
    assert result.status == "QUEUED"
    assert len(observed_private) == 1
    assert observed_prices == [100.0]
    assert public.decisions["sma"].indicators["close"] == 101.0
    assert public.primary_target_lots == 0
    queued = central.state().queued[0]
    assert queued.candidate.target_lots == 1
    assert queued.candidate.estimated_price_kopecks == 10_000
    assert queued.candidate.strategy_id == "sma"
    assert queued.authorization.risk_order_allowed is True
    binding = json.loads(hooks.admission_binding_raw)
    assert binding["private_proposal_pre_sha256"] == hooks.proposal_sha256
    assert binding["private_proposal_post_sha256"] == hooks.proposal_sha256
    assert (
        binding["central_candidate_sha256"]
        == hashlib.sha256(q7a._canonical(queued.candidate.to_dict())).hexdigest()
    )
    before_repeat = central.state()
    _reason(
        "Q7A_ADMISSION_REQUEST_INVALID",
        hooks.coordinate_marked,
        coordinator=coordinator,
        runtime=target,
        request=request,
    )
    assert central.state() == before_repeat


@pytest.mark.parametrize(
    "tamper",
    [
        "wrong_sha",
        "missing_field",
        "extra_field",
        "invalid_decision",
        "wrong_type",
        "invalid_enum",
    ],
)
def test_private_snapshot_tampering_fails_before_central(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    (
        _,
        hooks,
        _,
        risk,
        portfolio_risk,
        repository,
        central,
        target,
        request,
    ) = _owner_admission_setup(tmp_path)
    coordinator = CentralOrderCoordinator(
        central, repository, risk, portfolio_risk_runtime=portfolio_risk
    )
    calls = 0
    actual_coordinate = CentralOrderCoordinator.coordinate

    def counted(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return actual_coordinate(self, *args, **kwargs)

    monkeypatch.setattr(CentralOrderCoordinator, "coordinate", counted)
    snapshot = json.loads(hooks._issued_proposal_canonical)
    if tamper == "wrong_sha":
        snapshot["primary_target_lots"] = 0
    elif tamper == "missing_field":
        del snapshot["ticker"]
    elif tamper == "extra_field":
        snapshot["unexpected"] = "value"
    elif tamper == "invalid_decision":
        snapshot["decisions"]["sma"]["signal"] = "BUY"
    elif tamper == "wrong_type":
        snapshot["primary_target_lots"] = "1"
    else:
        snapshot["primary_strategy"] = "not-a-strategy"
    altered = q7a._canonical(snapshot)
    if tamper != "wrong_sha":
        _reason("PROPOSAL_SNAPSHOT_INVALID", q7a._proposal_from_snapshot, altered)
    hooks._issued_proposal_canonical = altered
    _reason(
        "PROPOSAL_SNAPSHOT_INVALID",
        hooks.coordinate_marked,
        coordinator=coordinator,
        runtime=target,
        request=request,
    )
    assert calls == 0
    assert central.state().queued == ()
    assert hooks.admission_binding_raw is None


def test_private_snapshot_rejects_noncanonical_bytes(tmp_path: Path) -> None:
    _, hooks, _, _, _, _, _, _, _ = _owner_admission_setup(tmp_path)
    snapshot = hooks._issued_proposal_canonical
    assert snapshot is not None
    assert q7a._proposal_canonical(q7a._proposal_from_snapshot(snapshot)) == snapshot
    _reason("PROPOSAL_SNAPSHOT_INVALID", q7a._proposal_from_snapshot, snapshot + b"\n")


def test_private_proposal_mutation_during_owner_call_invalidates_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        _,
        hooks,
        _,
        risk,
        portfolio_risk,
        repository,
        central,
        target,
        request,
    ) = _owner_admission_setup(tmp_path)
    coordinator = CentralOrderCoordinator(
        central, repository, risk, portfolio_risk_runtime=portfolio_risk
    )
    actual_coordinate = CentralOrderCoordinator.coordinate

    def mutate_after_owner(self, private, *args, **kwargs):
        result = actual_coordinate(self, private, *args, **kwargs)
        object.__setattr__(private.decisions["sma"], "indicators", {"close": 101.0})
        return result

    monkeypatch.setattr(CentralOrderCoordinator, "coordinate", mutate_after_owner)
    _reason(
        "Q7A_PRIVATE_PROPOSAL_DRIFT",
        hooks.coordinate_marked,
        coordinator=coordinator,
        runtime=target,
        request=request,
    )
    assert len(central.state().queued) == 1
    assert hooks.admission_binding_raw is None


class _ExactRiskGate:
    account_id = ACCOUNT
    mode = "SANDBOX_EXECUTION"

    def __init__(self) -> None:
        self.state_store = SimpleNamespace(
            load_account=lambda _account: SimpleNamespace()
        )

    def _load_policy(self):
        return SimpleNamespace(), False

    def dispatch_authorization_guard(self, **_kwargs):
        from contextlib import nullcontext

        return nullcontext()


class _CompleteFakeSandboxTransport(q7a.Q7AFakeSandboxTransport):
    """One fake broker account with consistent cash, position and CL3 reads."""

    def __init__(self) -> None:
        super().__init__(
            scenario="filled",
            account_id=ACCOUNT,
            target_instrument_id="uid-sber",
        )
        self.fill_visible = False
        self.operation_reads = 0
        self.operation_id: str | None = None

    def post_order_once(self, *args, **kwargs):
        response = super().post_order_once(*args, **kwargs)
        assert self.request_id is not None
        self.operation_id = f"synthetic-operation-for-{self.request_id}"
        self.fill_visible = True
        return response

    def get_operations_by_cursor_once(self, _payload, _timeout):
        self.operation_reads += 1
        items = []
        if self.fill_visible:
            assert self.operation_id is not None
            items.append(
                {
                    "brokerAccountId": ACCOUNT,
                    "childOperations": [],
                    "commission": {"currency": "RUB", "units": "0", "nano": 0},
                    "cursor": "synthetic-filled-cursor",
                    "date": "2026-09-11T10:00:07.000Z",
                    "id": self.operation_id,
                    "payment": {"currency": "RUB", "units": "-1000", "nano": 0},
                    "quantity": "1",
                    "quantityDone": "1",
                    "quantityRest": "0",
                    "state": "OPERATION_STATE_EXECUTED",
                    "type": "OPERATION_TYPE_BUY",
                }
            )
        return {"items": items, "hasNext": False, "nextCursor": ""}

    def get_portfolio(self, account_id):
        assert account_id == ACCOUNT
        cash = "999000" if self.fill_visible else "1000000"
        shares = "1000" if self.fill_visible else "0"
        positions = []
        if self.fill_visible:
            positions.append(
                {
                    "instrumentUid": "uid-sber",
                    "figi": "synthetic-figi-sber",
                    "ticker": "SBER",
                    "classCode": "TQBR",
                    "instrumentType": "share",
                    "quantity": {"units": "10", "nano": 0},
                    "quantityLots": {"units": "1", "nano": 0},
                    "averagePositionPrice": {
                        "currency": "rub",
                        "units": "100",
                        "nano": 0,
                    },
                    "currentPrice": {"currency": "rub", "units": "100", "nano": 0},
                    "expectedYield": {"units": "0", "nano": 0},
                }
            )
        return {
            "totalAmountCurrencies": {"currency": "RUB", "units": cash, "nano": 0},
            "totalAmountPortfolio": {"currency": "rub", "units": "1000000", "nano": 0},
            "totalAmountShares": {"currency": "rub", "units": shares, "nano": 0},
            "expectedYield": {"units": "0", "nano": 0},
            "positions": positions,
        }

    def get_withdraw_limits(self, account_id):
        assert account_id == ACCOUNT
        cash = "999000" if self.fill_visible else "1000000"

        class StaticTransport:
            @staticmethod
            def _post(service, method, payload):
                assert (service, method, payload) == (
                    "SandboxService",
                    "GetSandboxWithdrawLimits",
                    {"accountId": ACCOUNT},
                )
                return {
                    "blocked": [],
                    "blockedGuarantee": [],
                    "money": [{"currency": "RUB", "units": cash, "nano": 0}],
                }

        return tbank_sandbox.TBankSandboxClient.get_withdraw_limits(
            StaticTransport(), ACCOUNT
        )


def _proof(current, state, queued, **changes):
    values = {
        "account_scope_sha256": VECTORS["account_scope_sha256"],
        "authority_record_revision": current.record_revision,
        "authority_record_sha256": current.sha256,
        "availability_sha256": "1" * 64,
        "central_order_revision": state.revision,
        "central_reservation_projection_hash": central_reservation_projection_hash(
            state
        ),
        "cl6_context_identity_sha256": "2" * 64,
        "cl6_context_sha256": "3" * 64,
        "current_lots": 0,
        "direction": "BUY",
        "evaluated_at": T6,
        "free_investable_cash": Money("RUB", 50_000_000_000),
        "identity_key_id": KEY_ID,
        "ledger_head_sha256": current.ledger_head_sha256,
        "ledger_revision": current.ledger_revision,
        "portfolio_decision_checksum": "a" * 64,
        "portfolio_document_checksum": "b" * 64,
        "portfolio_revision": 9,
        "reconciliation_sha256": "5" * 64,
        "reserved_cash": Money("RUB", 10_000_000_000),
        "risk_policy_hash": queued.authorization.risk_policy_hash,
        "risk_state_guard_hash": queued.authorization.risk_state_guard_hash,
        "target_lots": 1,
    }
    values.update(changes)
    return cl7.LockedDispatchProof.build(
        raw_intent_id=queued.intent_id, identity_key=KEY, **values
    )


def _dispatch_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ledger_store: persistence.CashLedgerStore,
    scenario: str = "filled",
    proof_changes: dict[str, object] | None = None,
    risk_drift: bool = False,
    portfolio_drift: bool = False,
    risk_policy_hash: str = "d" * 64,
    risk_guard_hash: str = "e" * 64,
    risk_runtime: RiskRuntimeAdapter | None = None,
    portfolio_risk_runtime: PortfolioRiskRuntime | None = None,
    admitted: tuple[PortfolioRepository, CentralOrderManager, object] | None = None,
    exact_context: bool = False,
    opening_record_sha256: str = "7" * 64,
):
    ledger_snapshot = ledger_store.snapshot()
    authority_manager, _ = _armed_chain(
        tmp_path,
        ledger_revision=ledger_snapshot.ledger_revision,
        ledger_head_sha256=ledger_snapshot.ledger_head_sha256,
        opening_record_sha256=opening_record_sha256,
    )
    repository, central, intent = (
        admitted
        if admitted is not None
        else _central(
            tmp_path,
            risk_policy_hash=risk_policy_hash,
            risk_guard_hash=risk_guard_hash,
        )
    )
    if portfolio_drift:
        old = repository.load(expected_account_id=ACCOUNT)
        repository.save(
            replace(old, revision=old.revision + 1), expected_revision=old.revision
        )
    at_post: list[tuple[str, int, bool]] = []

    def observe_attempt_marker() -> None:
        current = authority_manager.store._load_unlocked(allow_missing_legacy=False)
        at_post.append(
            (
                current.state.value,
                current.post_attempt_count,
                current.pending_dispatch_proof_sha256 is not None,
            )
        )

    if exact_context:
        transport = _CompleteFakeSandboxTransport()
        transport.before_post = observe_attempt_marker
    else:
        transport = q7a.Q7AFakeSandboxTransport(
            scenario=scenario,
            account_id=ACCOUNT,
            target_instrument_id="uid-sber",
            before_post=observe_attempt_marker,
        )
        monkeypatch.setattr(
            cl7.RuntimeCashAuthorityManager,
            "synchronize_operations_locked",
            lambda self, current, **_kwargs: (current, SimpleNamespace()),
        )
        monkeypatch.setattr(
            cl7.RuntimeCashAuthorityManager,
            "build_runtime_context",
            lambda *_args, **_kwargs: SimpleNamespace(context=SimpleNamespace()),
        )
        monkeypatch.setattr(
            transport, "get_portfolio", lambda _account: {"synthetic": "portfolio"}
        )
        monkeypatch.setattr(transport, "get_withdraw_limits", lambda _account: {})

    class _DriftRiskGate(_ExactRiskGate):
        def dispatch_authorization_guard(self, **_kwargs):
            raise RiskDispatchAuthorizationError("RISK_CHANGED", "synthetic drift")

    adapter = SandboxExecutionAdapter(
        transport,
        central,
        SandboxExecutionPolicy(account_id=ACCOUNT),
        risk_runtime=(
            _DriftRiskGate()
            if risk_drift
            else risk_runtime
            if risk_runtime is not None
            else _ExactRiskGate()
        ),
        portfolio_risk_runtime=portfolio_risk_runtime,
        cash_authority_manager=authority_manager,
        cl7_identity_key=KEY,
        cl7_identity_key_id=KEY_ID,
        cl7_ledger_store=ledger_store,
        cl7_proof_builder=(
            None
            if exact_context
            else lambda current, state, queued: _proof(
                current, state, queued, **(proof_changes or {})
            )
        ),
        cl7_clock=lambda: T6,
        cl7_monotonic_ns=lambda: 1,
        cl7_wait_ns=lambda _duration: None,
    )
    return authority_manager, repository, central, intent, transport, adapter, at_post


@pytest.mark.parametrize(
    "case", VECTORS["dispatch_cases"], ids=lambda item: item["scenario"]
)
def test_isolated_provider_outcomes_cannot_fabricate_economic_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: dict[str, object]
) -> None:
    # These injected-proofs cases test failure classifications only. The
    # connected owner-to-owner success and lifecycle is asserted separately.
    ledger, opening, _descriptor = _ledger_with_opening(tmp_path)
    authority_manager, repository, central, intent, transport, adapter, at_post = (
        _dispatch_setup(
            tmp_path,
            monkeypatch,
            scenario=case["scenario"],
            ledger_store=ledger,
        )
    )
    first = adapter.dispatch_next(repository, expected_intent_id=intent.intent_id)
    assert first.status == case["dispatch_status"]
    assert transport.post_calls == case["post_calls"]
    assert at_post == [("EXACT_CASH_DISPATCH_PENDING", 1, True)]
    assert central.state().intents[0].status == case["central_status"]
    assert central.state().intents[0].cl7_locked_dispatch_proof_sha256 is not None
    authority = authority_manager.status()
    assert authority.state.value == case["cl7_state"]
    assert authority.post_attempt_count == case["post_attempts"]
    assert (authority.pending_dispatch_proof_sha256 is not None) is case[
        "pending_proof"
    ]
    assert (
        len(repository.load(expected_account_id=ACCOUNT).positions)
        == case["portfolio_effects"]
    )
    # CL3/CL2 and Risk accounting must not be fabricated from a POST response.
    assert (
        ledger.snapshot().ledger_revision - opening.ledger_revision
        == case["ledger_effects"]
    )
    assert (
        len(
            RiskStateStore(tmp_path / "risk_state.json")
            .load_account(ACCOUNT)
            .recorded_execution_ids
        )
        == case["risk_effects"]
    )
    assert (
        case["classification"]
        == {
            "SUBMITTED": "INCOMPLETE",
            "SUBMISSION_REJECTED": "SAFE_REJECTED",
            "SUBMISSION_UNCERTAIN": "INDETERMINATE",
        }[first.status]
    )
    second = adapter.dispatch_next(repository, expected_intent_id=intent.intent_id)
    assert second.order_was_sent is False
    assert transport.post_calls == 1
    ledger.close()


def _ledger_with_opening(
    root: Path,
) -> tuple[
    persistence.CashLedgerStore, cl4.OpeningAcceptance, persistence.CodecDescriptor
]:
    descriptor = broker.TBANK_OPERATION_CODEC
    ledger = persistence.CashLedgerStore.create(
        root / "ledger", (cl4.CL4_OPENING_CODEC, descriptor)
    )
    proof = cl4.build_broker_cash_proof(
        {"totalAmountCurrencies": {"currency": "RUB", "units": "1000000", "nano": 0}},
        account_scope_sha256=VECTORS["account_scope_sha256"],
        environment=broker.BrokerEnvironment.SANDBOX,
        as_of=T0,
        evaluated_at=T1,
        response_complete=True,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    plan = cl4.prepare_from_now_opening(
        ledger.export_bytes(), proof, evaluated_at=T1, identity_key=KEY
    )
    opening = cl4.accept_from_now_opening(
        ledger,
        plan,
        confirmation=f"ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}",
        evaluated_at=T2,
        identity_key=KEY,
    )
    assert opening.disposition == "OPENING_APPENDED"
    return ledger, opening, descriptor


@pytest.mark.parametrize("via_bridge", [True, False])
def test_filled_fake_provider_closes_exact_owner_lineage_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, via_bridge: bool
) -> None:
    # No success-side authority is fabricated: control -> owner admission ->
    # Central -> real CL7 pre-POST rebuild -> fake provider -> CL3/CL2/Portfolio/Risk.
    clock = [T6]
    monkeypatch.setattr(central_module, "_now", lambda: clock[0])
    record, hooks, proposal, risk, portfolio_risk, repository, central, intent = (
        _connected_owner_admission(tmp_path, via_bridge=via_bridge)
    )
    _reason(
        "PROPOSAL_MARKER_INVALID",
        proposal.decisions["sma"].indicators.__setitem__,
        "changed_after_coordination",
        1,
    )
    assert (
        hooks.proposal_sha256
        == hashlib.sha256(q7a._canonical(proposal.to_dict())).hexdigest()
    )
    assert intent.authorization.portfolio_risk is not None
    assert intent.authorization.risk_decision_id
    assert intent.candidate.runtime_key == proposal.runtime_key
    assert record.fields["target_instrument_id"] == intent.candidate.instrument_id
    risk_states = risk.state_store
    ledger, opening, descriptor = _ledger_with_opening(tmp_path)
    authority, repository, central, intent, transport, adapter, at_post = (
        _dispatch_setup(
            tmp_path,
            monkeypatch,
            ledger_store=ledger,
            risk_runtime=risk,
            portfolio_risk_runtime=portfolio_risk,
            admitted=(repository, central, intent),
            exact_context=True,
            opening_record_sha256=opening.record.sha256,
        )
    )
    dispatched = adapter.dispatch_next(repository, expected_intent_id=intent.intent_id)
    assert dispatched.status == "SUBMITTED"
    assert at_post == [("EXACT_CASH_DISPATCH_PENDING", 1, True)]
    assert transport.post_calls == 1
    # A fake broker order is read back; broker observations flow through the
    # accepted CL3 classification and CL2 append owners, never hand-built.
    order = transport.get_order_state(ACCOUNT, intent.intent_id)
    assert order["orderRequestId"] == intent.intent_id
    assert order["orderId"] == transport.order_id
    assert order["executionReportStatus"] == "EXECUTION_REPORT_STATUS_FILL"
    assert order["lotsExecuted"] == "1"
    assert transport.request_id == intent.intent_id
    assert transport.operation_id == f"synthetic-operation-for-{intent.intent_id}"
    pending_proof_sha256 = authority.status().pending_dispatch_proof_sha256
    assert pending_proof_sha256 is not None
    assert (
        central.state().intents[0].cl7_locked_dispatch_proof_sha256
        == pending_proof_sha256
    )
    batch = broker.collect_tbank_operations(
        broker.BrokerReadRequest(
            environment=broker.BrokerEnvironment.SANDBOX,
            raw_account_id=ACCOUNT,
            identity_key=KEY,
            identity_key_id=KEY_ID,
            from_inclusive=T6,
            to_exclusive=T8,
            limit=100,
            max_pages=1,
            max_items=100,
            absolute_deadline_ns=10_000_000,
            retry_policy=broker.RetryPolicy(1, 1_000_000, ()),
            transport=transport.get_operations_by_cursor_once,
            monotonic_ns=lambda: 0,
            wait_ns=lambda _duration: None,
        )
    )
    assert batch.watermark.item_count == 1
    assert len(batch.decisions) == 1
    decision = batch.decisions[0]
    assert decision.kind is broker.BrokerDecisionKind.TRANSACTION_PROPOSED
    observation = decision.observation
    transaction = decision.transaction_proposal
    assert transaction is not None
    assert transaction.source == observation.source
    assert observation.source.account_scope_sha256 == VECTORS["account_scope_sha256"]
    assert observation.source.source_kind == "TBANK_OPERATION"
    assert (
        observation.source.source_scope_sha256
        == hmac.new(
            KEY,
            persistence.canonical_json_bytes(
                {
                    "account_scope_sha256": VECTORS["account_scope_sha256"],
                    "component": "PAYMENT",
                    "domain": "v3.10-cl3-source-scope",
                    "identity_key_id": KEY_ID,
                    "operation_id": transport.operation_id,
                    "operation_state": "OPERATION_STATE_EXECUTED",
                    "provider": "TBANK",
                    "version": 1,
                }
            ),
            hashlib.sha256,
        ).hexdigest()
    )
    pre = ledger.snapshot()
    assert pre.ledger_revision == opening.ledger_revision
    assert pre.ledger_head_sha256 == opening.ledger_head_sha256
    assert (
        ledger.append_observation(
            observation, expected_store_revision=pre.store_revision
        )
        is persistence.PersistenceDisposition.OBSERVATION_STORED
    )
    observed = ledger.snapshot()
    assert (
        ledger.append_transaction(
            transaction,
            observation.sha256,
            expected_store_revision=observed.store_revision,
            expected_ledger_revision=observed.ledger_revision,
        )
        is persistence.PersistenceDisposition.TRANSACTION_APPENDED
    )
    posted = ledger.snapshot()
    assert posted.ledger_revision == opening.ledger_revision + 1
    assert posted.ledger_head_sha256 != opening.ledger_head_sha256
    manager = CanonicalPortfolioManager(
        transport,
        ACCOUNT,
        robot_state_file=tmp_path / "robot_state.json",
        portfolio_state_file=tmp_path / "portfolio_state.json",
        journal_file=tmp_path / "portfolio_events.db",
    )
    manager.stage_confirmed_target(
        instrument_id=intent.candidate.instrument_id,
        target_lots=intent.candidate.target_lots,
        strategy_id=intent.candidate.strategy_id,
        config_hash=intent.candidate.strategy_profile_hash,
        candle_interval=intent.candidate.candle_interval,
        ticker=intent.candidate.ticker,
        figi="synthetic-figi-sber",
        class_code="TQBR",
        candle_time=intent.candidate.candle_time,
        transaction_id=intent.intent_id,
    )
    refreshed = manager.refresh_from_api_portfolio(
        transport.get_portfolio(ACCOUNT),
        broker_orders=(),
        record_event=False,
        snapshot_at="2026-09-11T10:00:08+00:00",
    )
    assert repository.load(expected_account_id=ACCOUNT) == refreshed
    assert len(refreshed.positions) == 1
    assert refreshed.positions[0].actual_lots == 1
    assert refreshed.positions[0].reconciliation.status is ReconciliationStatus.MATCHED
    cash_proof = cl4.build_broker_cash_proof(
        transport.get_portfolio(ACCOUNT),
        account_scope_sha256=VECTORS["account_scope_sha256"],
        environment=broker.BrokerEnvironment.SANDBOX,
        as_of=T8,
        evaluated_at=T9,
        response_complete=True,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    reconciliation = cl4.reconcile_shadow_cash(
        ledger.export_bytes(), cash_proof, evaluated_at=T9, identity_key=KEY
    )
    assert reconciliation.status is cl4.ReconciliationStatus.MATCHED
    assert reconciliation.expected_cash.minor_units == 999_000_000_000_000
    assert reconciliation.projection.opening_record_sha256 == opening.record.sha256
    assert intent.authorization.risk_policy_hash == risk.current_policy_hash()
    clock[0] = T9
    terminal = central.mark_reconciled(
        intent.intent_id,
        portfolio_repository=repository,
        outcome="FILLED",
        executed_lots=1,
        risk_runtime=risk,
        execution_price_rub=100.0,
        execution_price_source="synthetic_broker_order_state",
    )
    assert terminal.status == "RECONCILED"
    assert terminal.broker_order_id == order["orderId"]
    assert terminal.risk_execution_status == "RECORDED"
    assert terminal.risk_execution_id == intent.intent_id
    assert central.state().reserved_cash_kopecks == 0
    assert risk_states.load_account(ACCOUNT).recorded_execution_ids == (
        intent.intent_id,
    )
    recovered, disposition = authority.recover_runtime(
        central_manager=central,
        raw_account_id=ACCOUNT,
        identity_key=KEY,
        identity_key_id=KEY_ID,
        transition_at=T9,
    )
    assert disposition == "RECOVERY_CLOSED_DISARMED"
    assert recovered.post_attempt_count == 1
    assert recovered.pending_dispatch_proof_sha256 is None
    assert pending_proof_sha256 != recovered.pending_dispatch_proof_sha256
    assert recovered.state is cl7.RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert transport.get_order_state(ACCOUNT, intent.intent_id) == order
    assert transport.post_calls == 1
    assert central.state().intents == (terminal,)
    assert ledger.snapshot().ledger_revision == posted.ledger_revision
    assert risk_states.load_account(ACCOUNT).recorded_execution_ids == (
        intent.intent_id,
    )
    lineage_inputs = {
        "record": record,
        "hooks": hooks,
        "proposal": proposal,
        "queued": intent,
        "terminal": terminal,
        "provider_order": order,
        "operation_id": transport.operation_id,
        "observation": observation,
        "transaction": transaction,
        "opening_ledger_revision": opening.ledger_revision,
        "opening_ledger_head_sha256": opening.ledger_head_sha256,
        "ledger_snapshot": posted,
        "reconciliation": reconciliation,
        "portfolio": refreshed,
        "risk_execution_ids": risk_states.load_account(ACCOUNT).recorded_execution_ids,
        "authority": recovered,
        "locked_proof_sha256": pending_proof_sha256,
        "identity_key": KEY,
        "identity_key_id": KEY_ID,
    }
    if not via_bridge:
        assert hooks.admission_binding_raw is None
        _reason(
            "ADMISSION_BINDING_INVALID",
            q7a.build_synthetic_lineage_evidence,
            **lineage_inputs,
        )
        return
    lineage = q7a.build_synthetic_lineage_evidence(**lineage_inputs)
    with (tmp_path / "q7a-synthetic-lineage.json").open("xb") as stream:
        stream.write(lineage)
    assert (tmp_path / "q7a-synthetic-lineage.json").read_bytes() == lineage
    assert ACCOUNT.encode() not in lineage
    assert intent.intent_id.encode() not in lineage
    assert order["orderId"].encode() not in lineage
    fields = json.loads(lineage)
    assert fields["control_record_sha256"] == record.record_sha256
    assert fields["proposal_sha256"] == hooks.proposal_sha256
    assert (
        fields["admission_binding_sha256"]
        == hashlib.sha256(hooks.admission_binding_raw).hexdigest()
    )
    assert fields["admission_binding_status"] == "VALID"
    assert (
        fields["operation_source_scope_sha256"]
        == observation.source.source_scope_sha256
    )
    assert fields["ledger_head_sha256"] == posted.ledger_head_sha256
    assert fields["reconciliation_sha256"] == reconciliation.sha256
    assert fields["locked_dispatch_proof_sha256"] == pending_proof_sha256
    assert fields["authority_record_sha256"] == recovered.sha256
    binding_raw = hooks.admission_binding_raw
    assert binding_raw is not None
    binding = json.loads(binding_raw)
    assert binding["private_proposal_pre_sha256"] == hooks.proposal_sha256
    assert binding["private_proposal_post_sha256"] == hooks.proposal_sha256
    assert ACCOUNT.encode() not in binding_raw
    assert intent.intent_id.encode() not in binding_raw
    binding["central_intent_id_sha256"] = "0" * 64
    binding["record_sha256"] = hashlib.sha256(
        q7a._canonical(
            {key: value for key, value in binding.items() if key != "record_sha256"}
        )
    ).hexdigest()
    hooks._admission_binding_raw = q7a._canonical(binding)
    _reason(
        "ADMISSION_BINDING_INVALID",
        q7a.build_synthetic_lineage_evidence,
        **lineage_inputs,
    )
    hooks._admission_binding_raw = binding_raw
    _reason(
        "LINEAGE_MISMATCH",
        q7a.build_synthetic_lineage_evidence,
        **{
            **lineage_inputs,
            "provider_order": {**order, "orderRequestId": "different-order"},
        },
    )
    _reason(
        "LINEAGE_MISMATCH",
        q7a.build_synthetic_lineage_evidence,
        **{**lineage_inputs, "operation_id": "unrelated-operation"},
    )
    _reason(
        "LINEAGE_MISMATCH",
        q7a.build_synthetic_lineage_evidence,
        **{**lineage_inputs, "risk_execution_ids": ()},
    )
    _reason(
        "LINEAGE_MISMATCH",
        q7a.build_synthetic_lineage_evidence,
        **{**lineage_inputs, "opening_ledger_head_sha256": "0" * 64},
    )
    _reason(
        "LINEAGE_MISMATCH",
        q7a.build_synthetic_lineage_evidence,
        **{**lineage_inputs, "locked_proof_sha256": "0" * 64},
    )
    assert central.enqueue(intent.candidate, intent.authorization).idempotent is True
    with pytest.raises(CentralOrderConflictError):
        central.mark_reconciled(
            intent.intent_id,
            portfolio_repository=repository,
            outcome="FILLED",
            executed_lots=1,
            risk_runtime=risk,
            execution_price_rub=100.0,
        )
    assert (
        ledger.append_observation(
            observation, expected_store_revision=posted.store_revision
        )
        is persistence.PersistenceDisposition.OBSERVATION_ALREADY_PRESENT
    )
    assert (
        ledger.append_transaction(
            transaction,
            observation.sha256,
            expected_store_revision=posted.store_revision,
            expected_ledger_revision=posted.ledger_revision,
        )
        is persistence.PersistenceDisposition.TRANSACTION_ALREADY_PRESENT
    )
    assert ledger.snapshot() == posted
    ledger.close()
    reopened = persistence.CashLedgerStore.open(
        tmp_path / "ledger", (cl4.CL4_OPENING_CODEC, descriptor)
    )
    reopened.validate()
    assert reopened.snapshot() == posted
    reopened.close()
    restarted_central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"), account_id=ACCOUNT
    )
    assert restarted_central.recover_after_restart() is None
    assert restarted_central.state().intents == (terminal,)
    assert (
        cl7.RuntimeCashAuthorityManager(
            cl7.RuntimeCashAuthorityStore(tmp_path)
        ).status()
        == recovered
    )
    assert risk_states.load_account(ACCOUNT).recorded_execution_ids == (
        intent.intent_id,
    )


@pytest.mark.parametrize(
    "drift,proof_changes,risk_drift,portfolio_drift,expected_result,expected_central",
    [
        (
            "stale_proof",
            {"evaluated_at": "2026-09-11T09:59:55.000000000Z"},
            False,
            False,
            "CL7_CONTEXT_STALE",
            "QUEUED",
        ),
        (
            "central_revision",
            {"central_order_revision": 99},
            False,
            False,
            "CL7_INTERNAL_BOUNDARY_FAILED",
            "QUEUED",
        ),
        (
            "ledger_revision",
            {"ledger_revision": 99},
            False,
            False,
            "CL7_DISPATCH_PROOF_INVALID",
            "IN_FLIGHT",
        ),
        ("risk_state", {}, True, False, "CL7_LOCKED_REVALIDATION_BLOCKED", "QUEUED"),
        (
            "portfolio_revision",
            {},
            False,
            True,
            "CL7_INTERNAL_BOUNDARY_FAILED",
            "QUEUED",
        ),
    ],
)
def test_freshness_and_owner_drift_fail_before_fake_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    proof_changes: dict[str, object],
    risk_drift: bool,
    portfolio_drift: bool,
    expected_result: str,
    expected_central: str,
) -> None:
    ledger, opening, _descriptor = _ledger_with_opening(tmp_path)
    authority, repository, central, intent, transport, adapter, at_post = (
        _dispatch_setup(
            tmp_path,
            monkeypatch,
            proof_changes=proof_changes,
            risk_drift=risk_drift,
            portfolio_drift=portfolio_drift,
            ledger_store=ledger,
        )
    )
    result = adapter.dispatch_next(repository, expected_intent_id=intent.intent_id)
    assert result.status == expected_result, drift
    assert transport.post_calls == 0
    assert at_post == []
    assert authority.status().post_attempt_count == 0
    assert authority.status().pending_dispatch_proof_sha256 is None
    assert central.state().intents[0].status == expected_central
    assert repository.load(expected_account_id=ACCOUNT).positions == ()
    assert ledger.snapshot().ledger_revision == opening.ledger_revision
    assert (
        RiskStateStore(tmp_path / "risk_state.json")
        .load_account(ACCOUNT)
        .recorded_execution_ids
        == ()
    )
    ledger.close()


def test_restart_and_duplicate_readback_never_resubmit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, opening, _descriptor = _ledger_with_opening(tmp_path)
    authority, repository, _central_manager, intent, transport, adapter, _ = (
        _dispatch_setup(
            tmp_path, monkeypatch, scenario="ambiguous_success", ledger_store=ledger
        )
    )
    first = adapter.dispatch_next(repository, expected_intent_id=intent.intent_id)
    assert first.status == "SUBMISSION_UNCERTAIN"
    restarted_central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"), account_id=ACCOUNT
    )
    restarted_authority = cl7.RuntimeCashAuthorityManager(
        cl7.RuntimeCashAuthorityStore(tmp_path)
    )
    assert restarted_central.recover_after_restart() is None
    assert restarted_authority.status().state is (
        cl7.RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    )
    assert transport.get_order_state(
        ACCOUNT, intent.intent_id
    ) == transport.get_order_state(ACCOUNT, intent.intent_id)
    assert transport.lookup_calls == 2
    assert transport.post_calls == 1
    assert restarted_central.state().intents[0].status == "UNCERTAIN"
    assert authority.status().post_attempt_count == 1
    assert repository.load(expected_account_id=ACCOUNT).positions == ()
    assert ledger.snapshot().ledger_revision == opening.ledger_revision
    assert (
        RiskStateStore(tmp_path / "risk_state.json")
        .load_account(ACCOUNT)
        .recorded_execution_ids
        == ()
    )
    ledger.close()


def test_fake_transport_rejects_wrong_scope_legacy_and_second_post() -> None:
    fake = q7a.Q7AFakeSandboxTransport(
        scenario="filled", account_id=ACCOUNT, target_instrument_id="uid-sber"
    )
    kwargs = {
        "order_id": "synthetic-intent",
        "order_type": "MARKET",
        "time_in_force": "FOK",
    }
    _reason(
        "LEGACY_POST_FORBIDDEN",
        fake.post_order,
        ACCOUNT,
        "uid-sber",
        1,
        "BUY",
        **kwargs,
    )
    _reason(
        "FAKE_POST_SCOPE_INVALID",
        fake.post_order_once,
        ACCOUNT,
        "uid-lkoh",
        1,
        "BUY",
        **kwargs,
    )
    _reason(
        "FAKE_POST_SCOPE_INVALID",
        fake.post_order_once,
        ACCOUNT,
        "uid-sber",
        2,
        "BUY",
        **kwargs,
    )
    _reason(
        "FAKE_POST_SCOPE_INVALID",
        fake.post_order_once,
        ACCOUNT,
        "uid-sber",
        1,
        "SELL",
        **kwargs,
    )
    assert fake.post_calls == 0
    assert (
        fake.post_order_once(ACCOUNT, "uid-sber", 1, "BUY", **kwargs)["lotsExecuted"]
        == "1"
    )
    _reason(
        "SECOND_POST_FORBIDDEN",
        fake.post_order_once,
        ACCOUNT,
        "uid-sber",
        1,
        "BUY",
        **kwargs,
    )
    assert fake.post_calls == 1
