from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone

import pytest

from trading_robot.central_order_manager import (
    CentralOrderCandidate,
    CentralOrderIntent,
    CentralOrderState,
    ExecutionAuthorization,
)
from trading_robot.portfolio_model import (
    AccountState,
    CashBalance,
    OwnershipStatus,
    PortfolioMigrationMetadata,
    PortfolioState,
    PositionOrigin,
    PositionOwnership,
    PositionState,
    ReconciliationResult,
    ReconciliationStatus,
    SnapshotFreshness,
)
from trading_robot.portfolio_risk_adapter import (
    PortfolioRiskAdapterError,
    PortfolioRiskInputAdapter,
    PortfolioRiskInstrumentMetadata,
    build_portfolio_risk_read_only_report,
    portfolio_policy_from_risk_policy,
)
from trading_robot.portfolio_risk_model import PortfolioRiskInputError
from trading_robot.risk import InstrumentRiskHalt, RiskEngine, RiskPolicy, RiskState
from trading_robot.risk_persistence import (
    RiskPersistenceError,
    RiskProfileStore,
    RiskStateStore,
)
from trading_robot.risk_runtime import risk_state_guard_hash

ACCOUNT = "sandbox-account"
NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def portfolio(
    *,
    account_id: str = ACCOUNT,
    current_price: float | None = 100.0,
    freshness: SnapshotFreshness = SnapshotFreshness.FRESH,
    blocking: bool = False,
) -> PortfolioState:
    reconciliation_status = (
        ReconciliationStatus.MANUAL_REVIEW_REQUIRED
        if blocking
        else ReconciliationStatus.MATCHED
    )
    position = PositionState(
        instrument_id="uid-sber",
        figi="figi-sber",
        ticker="SBER",
        class_code="TQBR",
        asset_type="share",
        currency="rub",
        quantity=10.0,
        actual_lots=1,
        average_price=99.0,
        current_price=current_price,
        market_value=1_000.0 if current_price is not None else None,
        expected_yield=0.0,
        target=None,
        ownership=PositionOwnership(
            strategy_id="sma",
            config_hash="d" * 64,
            candle_interval="CANDLE_INTERVAL_HOUR",
        ),
        ownership_status=OwnershipStatus.ATTRIBUTED,
        pending_orders=(),
        reconciliation=ReconciliationResult(
            instrument_id="uid-sber",
            status=reconciliation_status,
            blocking=blocking,
            reasons=("manual review",) if blocking else (),
            actual_lots=1,
            target_lots=1,
            checked_at=NOW.isoformat(),
        ),
        origin=PositionOrigin.STRATEGY,
        last_candle_time=NOW.isoformat(),
    )
    return PortfolioState(
        version=2,
        account=AccountState(
            account_id=account_id,
            total_value=100_000.0,
            securities_value=1_000.0,
            expected_yield=0.0,
            cash_balances=(CashBalance("rub", 50_000.0, blocked=1_000.0),),
        ),
        snapshot_at=NOW.isoformat(),
        generated_at=NOW.isoformat(),
        freshness=freshness,
        source="PORTFOLIO_MANAGER",
        positions=(position,),
        warnings=(),
        state_status="BLOCKED" if blocking else "READY",
        blocking=blocking,
        revision=7,
    )


def central_candidate() -> CentralOrderCandidate:
    return CentralOrderCandidate(
        account_id=ACCOUNT,
        instrument_id="uid-gazp",
        ticker="GAZP",
        runtime_key="runtime-gazp",
        runtime_config_hash="a" * 64,
        candle_interval="CANDLE_INTERVAL_HOUR",
        candle_time=NOW.isoformat(),
        strategy_id="sma",
        strategy_profile_hash="b" * 64,
        current_lots=0,
        target_lots=1,
        estimated_price_kopecks=20_000,
        lot_size=10,
        created_at=NOW.isoformat(),
    )


def central(*, status: str | None = None) -> CentralOrderState:
    state = CentralOrderState.empty(ACCOUNT, now=NOW.isoformat())
    if status is None:
        return state
    candidate = central_candidate()
    authorization = ExecutionAuthorization(
        account_id=ACCOUNT,
        instrument_id=candidate.instrument_id,
        authorized_target_lots=candidate.target_lots,
        portfolio_revision=7,
        portfolio_decision_checksum="a" * 64,
        portfolio_document_checksum="b" * 64,
        available_cash_kopecks=5_000_000,
        preflight_status="PASS",
        pending_order_ids=(),
        uncertain_order_ids=(),
        risk_status="PASS",
        risk_decision_id="risk-decision",
        risk_policy_hash="c" * 64,
        risk_order_allowed=True,
        authorized_at=NOW.isoformat(),
        risk_state_guard_hash="d" * 64,
    )
    intent = CentralOrderIntent.create(
        candidate,
        authorization,
        queue_sequence=1,
        reserved_cash_kopecks=205_000,
        created_at=NOW.isoformat(),
    )
    if status != "QUEUED":
        intent = intent.transition(status, detail="test", at=NOW.isoformat())
    return CentralOrderState(
        account_id=ACCOUNT,
        revision=1,
        next_sequence=2,
        intents=(intent,),
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
    )


def metadata() -> dict[str, PortfolioRiskInstrumentMetadata]:
    return {
        "uid-sber": PortfolioRiskInstrumentMetadata(
            instrument_id="uid-sber",
            lot_size=10,
            asset_class="stock",
            currency="rub",
        )
    }


def adapter_input(
    *,
    portfolio_state: PortfolioState | None = None,
    central_state: CentralOrderState | None = None,
    risk_state: RiskState | None = None,
    instrument_metadata=None,
    excluded_reservation_ids=(),
):
    return PortfolioRiskInputAdapter().build(
        portfolio=portfolio_state or portfolio(),
        central_orders=central_state or central(),
        risk_state=risk_state
        or RiskState(
            daily_start_equity_rub=100_000.0,
            weekly_start_equity_rub=100_000.0,
            high_watermark_equity_rub=100_000.0,
        ),
        evaluated_at=NOW,
        instrument_metadata=(
            metadata() if instrument_metadata is None else instrument_metadata
        ),
        excluded_reservation_ids=excluded_reservation_ids,
    )


def clean_empty_portfolio() -> PortfolioState:
    state = portfolio()
    return replace(
        state,
        account=replace(state.account, securities_value=0.0),
        positions=(),
        state_status="EMPTY",
    )


def test_clean_empty_omits_only_its_status_flag_and_has_empty_reservations() -> None:
    value = adapter_input(portfolio_state=clean_empty_portfolio())

    assert value.positions == ()
    assert value.reservations == ()
    assert value.reserved_cash_rub == 0.0
    assert value.nav_rub == 100_000.0
    assert value.cash_available_rub == 50_000.0
    assert value.data_quality_flags == ()


@pytest.mark.parametrize(
    ("change", "expected_flag"),
    [
        ("source", "PORTFOLIO_SOURCE_NOT_CANONICAL"),
        ("status", "PORTFOLIO_STATUS_UNKNOWN"),
        ("freshness", "PORTFOLIO_FRESHNESS_STALE"),
        ("migration", "CANONICAL_MIGRATION_INCOMPLETE"),
        ("blocking", "PORTFOLIO_BLOCKING"),
        ("position", None),
        ("intent", None),
        ("nav_missing", None),
        ("nav_zero", None),
        ("nav_nonfinite", None),
        ("rub_missing", None),
        ("rub_zero", None),
        ("rub_nonfinite", None),
    ],
)
def test_clean_empty_near_miss_retains_status_blocker(
    change: str, expected_flag: str | None
) -> None:
    state = clean_empty_portfolio()
    central_state = central()
    if change == "source":
        state = replace(state, portfolio_source="LEGACY")
    elif change == "status":
        state = replace(state, state_status="UNKNOWN")
    elif change == "freshness":
        state = replace(state, freshness=SnapshotFreshness.STALE)
    elif change == "migration":
        state = replace(state, migration=PortfolioMigrationMetadata.pending_from_v1())
    elif change == "blocking":
        state = replace(state, blocking=True)
    elif change == "position":
        state = replace(state, positions=portfolio().positions)
    elif change == "intent":
        central_state = central(status="QUEUED")
    elif change.startswith("nav_"):
        nav = {"nav_missing": None, "nav_zero": 0.0, "nav_nonfinite": float("inf")}[
            change
        ]
        state = replace(state, account=replace(state.account, total_value=nav))
    elif change == "rub_missing":
        state = replace(state, account=replace(state.account, cash_balances=()))
    elif change.startswith("rub_"):
        cash = 0.0 if change == "rub_zero" else float("inf")
        state = replace(
            state,
            account=replace(state.account, cash_balances=(CashBalance("rub", cash),)),
        )

    if change in {"nav_nonfinite", "rub_nonfinite"}:
        field = (
            "input.nav_rub" if change == "nav_nonfinite" else "input.cash_available_rub"
        )
        with pytest.raises(PortfolioRiskInputError, match=f"{field} must be finite"):
            adapter_input(portfolio_state=state, central_state=central_state)
        return

    value = adapter_input(portfolio_state=state, central_state=central_state)
    assert (
        "PORTFOLIO_STATUS_UNKNOWN" if change == "status" else "PORTFOLIO_STATUS_EMPTY"
    ) in value.data_quality_flags
    if expected_flag is not None:
        assert expected_flag in value.data_quality_flags


def test_clean_empty_account_scope_mismatch_fails_before_input() -> None:
    state = clean_empty_portfolio()
    state = replace(state, account=replace(state.account, account_id="wrong-account"))
    with pytest.raises(PortfolioRiskAdapterError, match="account scopes differ"):
        adapter_input(portfolio_state=state)


def test_clean_empty_rejects_tampered_derived_reservation_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_robot.portfolio_risk_adapter as adapter_module

    state = central()
    original = adapter_module.central_reservation_projection_hash

    def tampered_projection(selected, **kwargs):
        if selected is state:
            return "0" * 64
        return original(selected, **kwargs)

    monkeypatch.setattr(
        adapter_module, "central_reservation_projection_hash", tampered_projection
    )
    value = adapter_input(portfolio_state=clean_empty_portfolio(), central_state=state)
    assert "PORTFOLIO_STATUS_EMPTY" in value.data_quality_flags


def test_clean_empty_rejects_tampered_reserved_cash_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        CentralOrderState, "reserved_cash_kopecks", property(lambda _state: 1)
    )
    value = adapter_input(portfolio_state=clean_empty_portfolio())
    assert "PORTFOLIO_STATUS_EMPTY" in value.data_quality_flags


def test_adapter_builds_current_input_from_canonical_contracts() -> None:
    risk_state = RiskState(
        daily_turnover_rub=5_000.0,
        daily_start_equity_rub=100_000.0,
        weekly_start_equity_rub=95_000.0,
        high_watermark_equity_rub=110_000.0,
        instrument_kill_switches=(
            InstrumentRiskHalt(
                instrument_id="uid-sber",
                reason="review",
                source="operator",
                set_at=NOW.isoformat(),
                operator_ref="ticket-1",
            ),
        ),
    )

    value = adapter_input(
        central_state=central(status="QUEUED"),
        risk_state=risk_state,
    )

    assert value.account_id == ACCOUNT
    assert value.snapshot_revision == 7
    assert value.central_order_revision == 1
    assert value.positions[0].lot_price_rub == 1_000.0
    assert value.positions[0].price_source == "CANONICAL_PORTFOLIO_MANAGER"
    assert value.positions[0].strategy_id == "sma"
    assert value.positions[0].asset_class == "STOCK"
    assert value.cash_available_rub == 50_000.0
    assert value.reserved_cash_rub == 2_050.0
    assert value.blocking_order_ids == ()
    assert value.instrument_kill_switches == ("uid-sber",)
    assert value.risk_state_guard_hash == risk_state_guard_hash(risk_state)
    assert value.data_quality_flags == ()


def test_adapter_excludes_replaced_intent_reservation_from_shadow_projection(
) -> None:
    active = central(status="QUEUED")
    reservation_id = active.intents[0].intent_id

    included = adapter_input(central_state=active)
    excluded = adapter_input(
        central_state=active,
        excluded_reservation_ids=(reservation_id,),
    )

    assert included.reserved_cash_rub == 2_050.0
    assert excluded.reserved_cash_rub == 0.0
    assert excluded.reservation_projection_hash != (
        included.reservation_projection_hash
    )


def test_adapter_does_not_fallback_to_average_price() -> None:
    value = adapter_input(portfolio_state=portfolio(current_price=None))

    assert value.positions[0].lot_price_rub is None
    assert value.positions[0].price_at is None
    assert value.positions[0].price_source is None


def test_missing_lot_metadata_is_explicit_unknown() -> None:
    value = adapter_input(instrument_metadata={})

    assert value.positions[0].lot_size is None
    assert value.positions[0].lot_price_rub is None
    assert "LOT_SIZE_UNKNOWN:uid-sber" in value.data_quality_flags


def test_blocking_central_intent_and_canonical_degradation_are_preserved() -> None:
    value = adapter_input(
        portfolio_state=portfolio(
            freshness=SnapshotFreshness.STALE,
            blocking=True,
        ),
        central_state=central(status="IN_FLIGHT"),
    )

    assert len(value.blocking_order_ids) == 1
    assert "PORTFOLIO_BLOCKING" in value.data_quality_flags
    assert "PORTFOLIO_FRESHNESS_STALE" in value.data_quality_flags


def test_account_scope_mismatch_is_rejected() -> None:
    with pytest.raises(PortfolioRiskAdapterError, match="account scopes differ"):
        adapter_input(portfolio_state=portfolio(account_id="other-account"))


def test_metadata_key_identity_mismatch_is_rejected() -> None:
    with pytest.raises(PortfolioRiskAdapterError, match="key/identity mismatch"):
        adapter_input(
            instrument_metadata={
                "uid-sber": PortfolioRiskInstrumentMetadata(
                    instrument_id="uid-other",
                    lot_size=10,
                )
            }
        )


def test_fractional_lot_metadata_is_rejected() -> None:
    with pytest.raises(PortfolioRiskAdapterError, match="positive lot_size"):
        PortfolioRiskInstrumentMetadata(
            instrument_id="uid-sber",
            lot_size=1.5,
        )


def test_read_only_report_never_authorizes_execution() -> None:
    risk_input = adapter_input()
    policy = RiskPolicy()

    report = build_portfolio_risk_read_only_report(
        risk_input=risk_input,
        policy=policy,
        mode="SANDBOX_EXECUTION",
    )

    assert report.status == "CONFIGURATION_REQUIRED"
    assert report.portfolio_policy_status == "CONFIGURATION_REQUIRED"
    assert report.metrics.gross_exposure_rub == 1_000.0
    assert report.execution_authorized is False
    assert "PORTFOLIO_POLICY_CONFIGURATION_REQUIRED" in report.warnings


def test_existing_risk_policy_maps_to_computational_policy() -> None:
    source = RiskPolicy(
        portfolio_policy_configured=True,
        portfolio_policy_mode="enforced",
        max_gross_exposure_rub=50_000.0,
        asset_class_concentration_limits=(("stock", 0.4),),
        max_open_positions=3,
    )

    mapped = portfolio_policy_from_risk_policy(source)

    assert mapped.mode == "ENFORCED"
    assert mapped.max_gross_exposure_rub == 50_000.0
    assert mapped.asset_class_limit("STOCK") == 0.4
    assert mapped.max_open_positions == 3


def test_profile_v1_migration_preserves_legacy_and_requires_sandbox_config(
    tmp_path,
) -> None:
    path = tmp_path / "risk_profiles.json"
    legacy = asdict(RiskPolicy())
    portfolio_fields = {
        "portfolio_policy_configured",
        "portfolio_policy_mode",
        "max_gross_exposure_rub",
        "max_gross_exposure_fraction",
        "max_net_exposure_fraction",
        "max_instrument_concentration_fraction",
        "max_strategy_concentration_fraction",
        "max_asset_class_concentration_fraction",
        "asset_class_concentration_limits",
        "max_open_positions",
        "min_cash_reserve_fraction",
        "max_daily_turnover_fraction",
        "max_price_age_seconds",
        "portfolio_warning_utilization_fraction",
    }
    for field in portfolio_fields:
        legacy.pop(field)
    canonical = json.dumps(
        legacy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = __import__("hashlib").sha256(canonical.encode("utf-8")).hexdigest()
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "last_selected_mode": "SANDBOX_EXECUTION",
                "profiles": {
                    "SANDBOX_EXECUTION": {
                        "policy": legacy,
                        "policy_hash": digest,
                        "updated_at": NOW.isoformat(),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    loaded = RiskProfileStore(path).require_profile("SANDBOX_EXECUTION")

    assert loaded["policy"].max_position_lots == RiskPolicy().max_position_lots
    assert loaded["policy"].portfolio_policy_configured is False
    assert loaded["portfolio_policy_status"] == "CONFIGURATION_REQUIRED"
    assert loaded["migrated_from_schema"] == 1
    with pytest.raises(RiskPersistenceError, match="explicit Portfolio Risk"):
        RiskProfileStore(path).require_portfolio_policy("SANDBOX_EXECUTION")


def test_explicit_policy_confirmation_is_required(tmp_path) -> None:
    store = RiskProfileStore(tmp_path / "risk_profiles.json")
    policy = RiskPolicy(
        portfolio_policy_configured=True,
        max_gross_exposure_rub=50_000.0,
    )

    with pytest.raises(RiskPersistenceError, match="explicit confirmation"):
        store.save_profile("SANDBOX_EXECUTION", policy)
    with pytest.raises(RiskPersistenceError, match="confirmation"):
        store.confirm_portfolio_policy(
            "SANDBOX_EXECUTION",
            policy,
            confirmation="confirm",
        )
    saved = store.confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        policy,
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
    )

    assert saved["portfolio_policy_status"] == "READY"
    assert store.require_portfolio_policy("SANDBOX_EXECUTION")["policy"] == policy


def test_persisted_portfolio_policy_status_cannot_override_policy(tmp_path) -> None:
    path = tmp_path / "risk_profiles.json"
    policy = RiskPolicy()
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "last_selected_mode": "SANDBOX_EXECUTION",
                "profiles": {
                    "SANDBOX_EXECUTION": {
                        "policy": asdict(policy),
                        "policy_hash": policy.policy_hash,
                        "portfolio_policy_status": "READY",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RiskPersistenceError, match="inconsistent Portfolio Risk"):
        RiskProfileStore(path).load_profile("SANDBOX_EXECUTION")


def test_instrument_kill_switch_is_idempotent_and_requires_exact_clear() -> None:
    engine = RiskEngine(RiskPolicy())
    first, _ = engine.engage_instrument_kill_switch(
        RiskState(),
        instrument_id="uid-sber",
        now=NOW,
        reason="manual review",
        source="operator",
        operator_ref="ticket-1",
    )
    second, _ = engine.engage_instrument_kill_switch(
        first,
        instrument_id="uid-sber",
        now=NOW,
        reason="manual review",
        source="operator",
        operator_ref="ticket-1",
    )

    assert second == first
    assert len(second.instrument_kill_switches) == 1
    with pytest.raises(Exception, match="confirmation must be exactly"):
        engine.clear_instrument_kill_switch(
            second,
            instrument_id="uid-sber",
            now=NOW,
            confirmation="clear",
        )
    cleared, event = engine.clear_instrument_kill_switch(
        second,
        instrument_id="uid-sber",
        now=NOW,
        confirmation="CLEAR INSTRUMENT RISK HALT uid-sber",
    )
    assert cleared.instrument_kill_switches == ()
    assert event.event_type == "INSTRUMENT_KILL_SWITCH_DISABLED"


def test_risk_state_v2_migration_adds_instrument_halts_without_losing_counters() -> (
    None
):
    state = RiskState.from_dict(
        {
            "version": 2,
            "daily_turnover_rub": 1_234.0,
            "daily_order_count": 2,
            "recorded_execution_ids": ["legacy"],
        }
    )

    assert state.version == 4
    assert state.daily_turnover_rub == 1_234.0
    assert state.daily_order_count == 2
    assert state.recorded_execution_ids == ("legacy",)
    assert state.instrument_kill_switches == ()


def test_instrument_kill_switch_survives_state_store_restart(tmp_path) -> None:
    path = tmp_path / "risk_state.json"
    store = RiskStateStore(path)
    halted, _event = RiskEngine(RiskPolicy()).engage_instrument_kill_switch(
        RiskState(daily_order_count=2),
        instrument_id="uid-sber",
        now=NOW,
        reason="restart review",
        source="operator",
        operator_ref="ticket-restart",
    )
    store.save_account(ACCOUNT, halted)

    restored = RiskStateStore(path).load_account(ACCOUNT)

    assert restored == halted
    assert restored.instrument_kill_switches[0].operator_ref == "ticket-restart"
    assert risk_state_guard_hash(restored) == risk_state_guard_hash(halted)


def test_profile_schema_migration_keeps_original_rollback_backup(tmp_path) -> None:
    path = tmp_path / "risk_profiles.json"
    legacy_policy = asdict(RiskPolicy())
    portfolio_fields = {
        "portfolio_policy_configured",
        "portfolio_policy_mode",
        "max_gross_exposure_rub",
        "max_gross_exposure_fraction",
        "max_net_exposure_fraction",
        "max_instrument_concentration_fraction",
        "max_strategy_concentration_fraction",
        "max_asset_class_concentration_fraction",
        "asset_class_concentration_limits",
        "max_open_positions",
        "min_cash_reserve_fraction",
        "max_daily_turnover_fraction",
        "max_price_age_seconds",
        "portfolio_warning_utilization_fraction",
    }
    for field in portfolio_fields:
        legacy_policy.pop(field)
    legacy_hash = __import__("hashlib").sha256(
        json.dumps(
            legacy_policy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    legacy_document = {
        "version": 1,
        "last_selected_mode": "SANDBOX_EXECUTION",
        "profiles": {
            "SANDBOX_EXECUTION": {
                "policy": legacy_policy,
                "policy_hash": legacy_hash,
            }
        },
    }
    path.write_text(json.dumps(legacy_document), encoding="utf-8")

    RiskProfileStore(path).save_profile("DRY_RUN", RiskPolicy())

    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2
    assert json.loads(
        path.with_name("risk_profiles.json.bak").read_text(encoding="utf-8")
    ) == legacy_document


def test_state_schema_migration_keeps_original_rollback_backup(tmp_path) -> None:
    path = tmp_path / "risk_state.json"
    legacy_document = {
        "version": 2,
        "accounts": {
            ACCOUNT: {
                "version": 2,
                "daily_turnover_rub": 250.0,
                "daily_order_count": 1,
            }
        },
    }
    path.write_text(json.dumps(legacy_document), encoding="utf-8")

    RiskStateStore(path).save_account(
        ACCOUNT,
        RiskState(daily_turnover_rub=500.0, daily_order_count=2),
    )

    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 4
    assert json.loads(
        path.with_name("risk_state.json.bak").read_text(encoding="utf-8")
    ) == legacy_document
