from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from test_central_order_coordinator_v3_8 import (
    ACCOUNT,
    NOW_DT,
    FakeRiskRuntime,
    candles,
    portfolio_state,
    profile,
    proposal,
    runtime,
)

from tools import v3_9_portfolio_risk_shadow_report as report_tool
from trading_robot.central_order_coordinator import CentralOrderCoordinator
from trading_robot.central_order_manager import CentralOrderManager, CentralOrderStore
from trading_robot.journal import EventJournal
from trading_robot.portfolio_model import SnapshotFreshness
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_adapter import PortfolioRiskInstrumentMetadata
from trading_robot.portfolio_risk_shadow import (
    PortfolioRiskCandidateQuote,
    PortfolioRiskShadowObserver,
    build_portfolio_risk_shadow_report,
    classify_shadow_drift,
)
from trading_robot.risk import RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore
from trading_robot.state_persistence import atomic_write_json


def shadow_policy(
    *,
    max_gross: float | None = None,
    mode: str = "OBSERVE_ONLY",
) -> RiskPolicy:
    return RiskPolicy(
        cash_reserve_rub=0.0,
        daily_loss_limit_rub=None,
        daily_loss_limit_fraction=None,
        weekly_loss_limit_rub=None,
        weekly_loss_limit_fraction=None,
        max_drawdown_fraction=None,
        max_daily_turnover_rub=None,
        max_orders_per_day=None,
        portfolio_policy_configured=True,
        portfolio_policy_mode=mode,
        max_gross_exposure_rub=max_gross,
    )


def observer(
    root,
    *,
    max_gross: float | None = None,
    mode: str = "OBSERVE_ONLY",
    currency: str | None = "RUB",
) -> PortfolioRiskShadowObserver:
    profiles = RiskProfileStore(root / "risk_profiles.json")
    profiles.confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        shadow_policy(max_gross=max_gross, mode=mode),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
    )
    return PortfolioRiskShadowObserver(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=profiles,
        journal=EventJournal(root / "trading_events.db"),
        instrument_metadata={
            "uid-sber": PortfolioRiskInstrumentMetadata(
                instrument_id="uid-sber",
                lot_size=10,
                asset_class="SHARE",
                currency=currency,
            )
        },
    )


def quote(
    *,
    price: float = 100.0,
    price_at=NOW_DT,
    source: str = "TBANK_LAST_PRICE_EXCHANGE",
) -> PortfolioRiskCandidateQuote:
    return PortfolioRiskCandidateQuote(
        unit_price_rub=price,
        price_at=price_at,
        source=source,
    )


def observe(root, *, target: int = 1, actual: int = 1, max_gross=None):
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)
    return observer(root, max_gross=max_gross).observe(
        proposal=proposal(selected, selected_runtime, target_lots=target),
        portfolio=portfolio_state(),
        central_orders=CentralOrderStore(
            root / "central_order_state.json"
        ).initialize(ACCOUNT),
        risk_state=RiskState(),
        actual_approved_target_lots=actual,
        actual_risk_decision_id="risk-decision-1",
        lot_size=10,
        candidate_quote=quote(),
        evaluated_at=NOW_DT,
    )


def test_shadow_records_exactly_one_deterministic_decision_across_restart(
    tmp_path,
) -> None:
    first = observe(tmp_path)
    second = observe(tmp_path)
    rows = EventJournal(tmp_path / "trading_events.db").recent(
        category="portfolio_risk_shadow"
    )

    assert first.status == "EVALUATED"
    assert first.drift_classification == "MATCH"
    assert first.execution_authorized is False
    assert first.candidate_price_at == NOW_DT.isoformat()
    assert first.candidate_price_source == "TBANK_LAST_PRICE_EXCHANGE"
    assert first.journal_inserted is True
    assert second.shadow_key == first.shadow_key
    assert second.decision_id == first.decision_id
    assert second.journal_inserted is False
    assert len(rows) == 1
    assert rows[0]["payload"]["decision"]["execution_authorized"] is False


def test_shadow_missing_price_timestamp_is_structured_unavailable(tmp_path) -> None:
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)
    result = observer(tmp_path).observe(
        proposal=proposal(selected, selected_runtime),
        portfolio=portfolio_state(),
        central_orders=CentralOrderStore(
            tmp_path / "central_order_state.json"
        ).initialize(ACCOUNT),
        risk_state=RiskState(),
        actual_approved_target_lots=1,
        actual_risk_decision_id="risk-decision-1",
        lot_size=10,
        candidate_quote=None,
        evaluated_at=NOW_DT,
    )

    assert result.status == "UNAVAILABLE"
    assert result.candidate_price_at is None
    assert result.candidate_price_source is None
    assert result.reason_codes == (
        "SHADOW_UNAVAILABLE:PORTFOLIORISKSHADOWERROR",
    )
    assert result.execution_authorized is False


def test_shadow_unknown_currency_is_structured_unavailable(tmp_path) -> None:
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)

    result = observer(tmp_path, currency=None).observe(
        proposal=proposal(selected, selected_runtime),
        portfolio=portfolio_state(),
        central_orders=CentralOrderStore(
            tmp_path / "central_order_state.json"
        ).initialize(ACCOUNT),
        risk_state=RiskState(),
        actual_approved_target_lots=1,
        actual_risk_decision_id="risk-decision-unknown-currency",
        lot_size=10,
        candidate_quote=quote(),
        evaluated_at=NOW_DT,
    )

    assert result.status == "UNAVAILABLE"
    assert result.reason_codes == (
        "SHADOW_UNAVAILABLE:PORTFOLIORISKSHADOWERROR",
    )
    assert result.execution_authorized is False


def test_shadow_uses_independent_exchange_quote_timestamp(tmp_path) -> None:
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)
    quote_at = NOW_DT.replace(minute=59)
    result = observer(tmp_path).observe(
        proposal=proposal(selected, selected_runtime),
        portfolio=portfolio_state(),
        central_orders=CentralOrderStore(
            tmp_path / "central_order_state.json"
        ).initialize(ACCOUNT),
        risk_state=RiskState(),
        actual_approved_target_lots=1,
        actual_risk_decision_id="risk-decision-quote",
        lot_size=10,
        candidate_quote=quote(price=101.5, price_at=quote_at),
        evaluated_at=quote_at,
    )

    assert result.status == "EVALUATED"
    assert result.candidate_price_at == quote_at.isoformat()
    assert result.candidate_price_source == "TBANK_LAST_PRICE_EXCHANGE"
    assert "CANDIDATE_PRICE_STALE" not in result.reason_codes


def test_shadow_event_is_idempotent_under_concurrent_duplicate_observation(
    tmp_path,
) -> None:
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)
    selected_observer = observer(tmp_path)
    central = CentralOrderStore(
        tmp_path / "central_order_state.json"
    ).initialize(ACCOUNT)

    def run_once(_index: int):
        return selected_observer.observe(
            proposal=proposal(selected, selected_runtime),
            portfolio=portfolio_state(),
            central_orders=central,
            risk_state=RiskState(),
            actual_approved_target_lots=1,
            actual_risk_decision_id="risk-decision-1",
            lot_size=10,
            candidate_quote=quote(),
            evaluated_at=NOW_DT,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = tuple(pool.map(run_once, range(4)))

    assert len({item.shadow_key for item in results}) == 1
    assert sum(item.journal_inserted for item in results) == 1
    assert EventJournal(tmp_path / "trading_events.db").count(
        category="portfolio_risk_shadow"
    ) == 1


def test_same_eligible_proposal_keeps_first_shadow_when_actual_result_drifts(
    tmp_path,
) -> None:
    first = observe(tmp_path, actual=1)
    duplicate = observe(tmp_path, actual=0)

    assert first.journal_inserted is True
    assert duplicate.shadow_key == first.shadow_key
    assert duplicate.journal_inserted is False
    assert duplicate.journal_event_id == first.journal_event_id
    assert EventJournal(tmp_path / "trading_events.db").count(
        category="portfolio_risk_shadow"
    ) == 1


def test_candidate_quote_provenance_changes_shadow_identity(tmp_path) -> None:
    first = observe(tmp_path)
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)
    second = observer(tmp_path).observe(
        proposal=proposal(selected, selected_runtime),
        portfolio=portfolio_state(),
        central_orders=CentralOrderStore(
            tmp_path / "central_order_state.json"
        ).initialize(ACCOUNT),
        risk_state=RiskState(),
        actual_approved_target_lots=1,
        actual_risk_decision_id="risk-decision-1",
        lot_size=10,
        candidate_quote=quote(price=100.5),
        evaluated_at=NOW_DT,
    )

    assert second.shadow_key != first.shadow_key
    assert second.journal_inserted is True


def test_shadow_classifies_new_portfolio_limit_as_explained_drift(tmp_path) -> None:
    result = observe(tmp_path, target=2, actual=2, max_gross=1_500.0)

    assert result.status == "EVALUATED"
    assert result.shadow_approved_target_lots == 1
    assert result.drift_classification == "EXPLAINED_SHADOW_MORE_RESTRICTIVE"
    assert result.unexplained_drift is False
    assert "MAX_GROSS_EXPOSURE_RUB" in result.reason_codes


def test_shadow_forces_observe_only_even_if_persisted_policy_is_enforced(
    tmp_path,
) -> None:
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)
    result = observer(tmp_path, mode="ENFORCED").observe(
        proposal=proposal(selected, selected_runtime),
        portfolio=portfolio_state(),
        central_orders=CentralOrderStore(
            tmp_path / "central_order_state.json"
        ).initialize(ACCOUNT),
        risk_state=RiskState(),
        actual_approved_target_lots=1,
        actual_risk_decision_id="risk-decision-1",
        lot_size=10,
        candidate_quote=quote(),
        evaluated_at=NOW_DT,
    )

    assert result.status == "EVALUATED"
    assert result.decision is not None
    assert result.decision.mode == "OBSERVE_ONLY"
    assert result.execution_authorized is False


def test_policy_change_after_v3_8_decision_is_shadow_unavailable(tmp_path) -> None:
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)
    result = observer(tmp_path).observe(
        proposal=proposal(selected, selected_runtime),
        portfolio=portfolio_state(),
        central_orders=CentralOrderStore(
            tmp_path / "central_order_state.json"
        ).initialize(ACCOUNT),
        risk_state=RiskState(),
        actual_approved_target_lots=1,
        actual_risk_decision_id="risk-decision-1",
        actual_risk_policy_hash="a" * 64,
        lot_size=10,
        candidate_quote=quote(),
        evaluated_at=NOW_DT,
    )

    assert result.status == "UNAVAILABLE"
    assert result.reason_codes == (
        "SHADOW_UNAVAILABLE:PORTFOLIORISKSHADOWERROR",
    )


def test_unavailable_shadow_is_a_structured_coverage_event(tmp_path) -> None:
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)
    result = PortfolioRiskShadowObserver(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=RiskProfileStore(tmp_path / "risk_profiles.json"),
        journal=EventJournal(tmp_path / "trading_events.db"),
    ).observe(
        proposal=proposal(selected, selected_runtime),
        portfolio=portfolio_state(),
        central_orders=CentralOrderStore(
            tmp_path / "central_order_state.json"
        ).initialize(ACCOUNT),
        risk_state=RiskState(),
        actual_approved_target_lots=1,
        actual_risk_decision_id="risk-decision-1",
        lot_size=10,
        candidate_quote=quote(),
        evaluated_at=NOW_DT,
    )
    report = build_portfolio_risk_shadow_report(
        EventJournal(tmp_path / "trading_events.db"),
        account_id=ACCOUNT,
    )

    assert result.status == "UNAVAILABLE"
    assert result.reason_codes == ("SHADOW_UNAVAILABLE:RISKPERSISTENCEERROR",)
    assert report["total_eligible_observations"] == 1
    assert report["evaluated"] == 0
    assert report["coverage_fraction"] == 0.0
    assert report["status"] == "INCOMPLETE"


def test_shadow_journal_failure_does_not_raise_or_authorize(tmp_path) -> None:
    class BrokenJournal:
        def record_portfolio_risk_shadow(self, _event):
            raise sqlite3.OperationalError("disk busy")

    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)
    selected_observer = observer(tmp_path)
    selected_observer.journal = BrokenJournal()

    result = selected_observer.observe(
        proposal=proposal(selected, selected_runtime),
        portfolio=portfolio_state(),
        central_orders=CentralOrderStore(
            tmp_path / "central_order_state.json"
        ).initialize(ACCOUNT),
        risk_state=RiskState(),
        actual_approved_target_lots=1,
        actual_risk_decision_id="risk-decision-1",
        lot_size=10,
        candidate_quote=quote(),
        evaluated_at=NOW_DT,
    )

    assert result.status == "EVALUATED"
    assert result.execution_authorized is False
    assert "SHADOW_JOURNAL_WRITE_FAILED" in result.reason_codes
    assert result.journal_event_id is None


def test_shadow_report_cli_exposes_coverage_and_zero_unexplained_drift(
    tmp_path,
) -> None:
    observe(tmp_path)

    payload = report_tool.run(
        report_tool.parse_args(
            [str(tmp_path), "--account-id", ACCOUNT]
        )
    )

    assert payload["status"] == "PASS"
    assert payload["coverage_fraction"] == 1.0
    assert payload["unexplained_drift"] == 0
    assert payload["execution_authorized"] is False


def test_shadow_report_cli_is_strictly_read_only(tmp_path) -> None:
    observe(tmp_path)
    database = tmp_path / "trading_events.db"
    before = database.read_bytes()
    before_names = sorted(item.name for item in tmp_path.iterdir())

    report_tool.run(
        report_tool.parse_args(
            [str(tmp_path), "--account-id", ACCOUNT]
        )
    )

    assert database.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == before_names


def test_shadow_report_direct_script_entrypoint_is_read_only(tmp_path) -> None:
    observe(tmp_path)
    database = tmp_path / "trading_events.db"
    before = database.read_bytes()
    script = (
        Path(__file__).parents[1]
        / "tools"
        / "v3_9_portfolio_risk_shadow_report.py"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            str(tmp_path),
            "--account-id",
            ACCOUNT,
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout)["execution_authorized"] is False
    assert database.read_bytes() == before


def test_coordinator_shadow_failure_does_not_change_v3_8_queue(tmp_path) -> None:
    class BrokenObserver:
        def observe(self, **_kwargs):
            raise RuntimeError("shadow unavailable")

    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(portfolio_state())
    central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    risk = FakeRiskRuntime()
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)

    result = CentralOrderCoordinator(
        central,
        repository,
        risk,
        portfolio_risk_shadow=BrokenObserver(),
    ).coordinate(
        proposal(selected, selected_runtime),
        selected_runtime,
        selected,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    assert result.status == "QUEUED"
    assert result.approved_target_lots == 1
    assert result.portfolio_risk_shadow is None
    assert central.state().queued[0].candidate.target_lots == 1
    assert result.broker_execution_authorized is False


def test_coordinator_records_shadow_but_keeps_v3_8_approved_target(tmp_path) -> None:
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(portfolio_state())
    central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    selected_observer = observer(tmp_path, max_gross=1_500.0)
    expected_policy_hash = selected_observer.profile_store.require_profile(
        "SANDBOX_EXECUTION"
    )["policy_hash"]

    class PolicyAlignedRiskRuntime(FakeRiskRuntime):
        def evaluate(self, **kwargs):
            outcome = super().evaluate(**kwargs)
            outcome.assessment.decision.policy_hash = expected_policy_hash
            return outcome

    risk = PolicyAlignedRiskRuntime(approved_target_lots=2)
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)

    result = CentralOrderCoordinator(
        central,
        repository,
        risk,
        portfolio_risk_shadow=selected_observer,
    ).coordinate(
        proposal(selected, selected_runtime, target_lots=2),
        selected_runtime,
        selected,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    assert result.status == "QUEUED"
    assert result.approved_target_lots == 2
    assert central.state().queued[0].candidate.target_lots == 2
    assert result.portfolio_risk_shadow is not None
    assert result.portfolio_risk_shadow.shadow_approved_target_lots == 1
    assert (
        result.portfolio_risk_shadow.drift_classification
        == "EXPLAINED_SHADOW_MORE_RESTRICTIVE"
    )
    assert result.portfolio_risk_shadow.execution_authorized is False


def test_coordinator_observe_only_records_shadow_without_central_mutation(
    tmp_path,
) -> None:
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(portfolio_state())
    central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    selected_observer = observer(tmp_path, max_gross=1_500.0)
    expected_policy_hash = selected_observer.profile_store.require_profile(
        "SANDBOX_EXECUTION"
    )["policy_hash"]

    class PolicyAlignedRiskRuntime(FakeRiskRuntime):
        def evaluate(self, **kwargs):
            outcome = super().evaluate(**kwargs)
            outcome.assessment.decision.policy_hash = expected_policy_hash
            return outcome

    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)
    result = CentralOrderCoordinator(
        central,
        repository,
        PolicyAlignedRiskRuntime(approved_target_lots=1),
        portfolio_risk_shadow=selected_observer,
    ).coordinate(
        proposal(selected, selected_runtime, target_lots=1),
        selected_runtime,
        selected,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
        observe_only=True,
        portfolio_risk_candidate_quote=quote(),
    )

    assert result.status == "SHADOW_OBSERVED"
    assert result.portfolio_risk_shadow is not None
    assert result.portfolio_risk_shadow.status == "EVALUATED"
    assert result.broker_execution_authorized is False
    assert central.state().intents == ()
    assert central.state().reserved_cash_kopecks == 0


def test_preflight_blocked_proposal_is_not_counted_as_shadow_eligible(
    tmp_path,
) -> None:
    stale = portfolio_state(freshness=SnapshotFreshness.STALE)
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(stale)
    central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)

    result = CentralOrderCoordinator(
        central,
        repository,
        FakeRiskRuntime(),
        portfolio_risk_shadow=observer(tmp_path),
    ).coordinate(
        proposal(selected, selected_runtime),
        selected_runtime,
        selected,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    assert result.status == "PREFLIGHT_BLOCKED"
    assert result.portfolio_risk_shadow is None
    assert EventJournal(tmp_path / "trading_events.db").count(
        category="portfolio_risk_shadow"
    ) == 0


def test_real_runtime_auto_wires_unconfigured_shadow_without_changing_queue(
    tmp_path,
) -> None:
    from trading_robot.risk_persistence import RiskStateStore
    from trading_robot.risk_runtime import RiskRuntimeAdapter

    profiles = RiskProfileStore(tmp_path / "risk_profiles.json")
    profiles.save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(
            cash_reserve_rub=0.0,
            daily_loss_limit_rub=None,
            daily_loss_limit_fraction=None,
            weekly_loss_limit_rub=None,
            weekly_loss_limit_fraction=None,
            max_drawdown_fraction=None,
            max_daily_turnover_rub=None,
            max_orders_per_day=None,
            risk_per_trade_rub=None,
            risk_per_trade_fraction=None,
            max_position_share_of_equity=1.0,
            max_position_value_rub=1_000_000.0,
            max_order_value_rub=1_000_000.0,
            commission_buffer_fraction=0.0,
        ),
        account_scope=ACCOUNT,
    )
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(portfolio_state())
    central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    risk = RiskRuntimeAdapter(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=profiles,
        state_store=RiskStateStore(tmp_path / "risk_state.json"),
    )
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)

    result = CentralOrderCoordinator(central, repository, risk).coordinate(
        proposal(selected, selected_runtime),
        selected_runtime,
        selected,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    assert result.status == "QUEUED"
    assert result.portfolio_risk_shadow is not None
    assert result.portfolio_risk_shadow.status == "UNAVAILABLE"
    assert "SHADOW_UNAVAILABLE:RISKPERSISTENCEERROR" in (
        result.portfolio_risk_shadow.reason_codes
    )
    assert central.state().queued[0].candidate.target_lots == 1


def test_real_runtime_auto_wires_confirmed_shadow_as_observe_only(tmp_path) -> None:
    from trading_robot.risk_persistence import RiskStateStore
    from trading_robot.risk_runtime import RiskRuntimeAdapter

    profiles = RiskProfileStore(tmp_path / "risk_profiles.json")
    profiles.confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        RiskPolicy(
            cash_reserve_rub=0.0,
            daily_loss_limit_rub=None,
            daily_loss_limit_fraction=None,
            weekly_loss_limit_rub=None,
            weekly_loss_limit_fraction=None,
            max_drawdown_fraction=None,
            max_daily_turnover_rub=None,
            max_orders_per_day=None,
            risk_per_trade_rub=None,
            risk_per_trade_fraction=None,
            max_position_share_of_equity=1.0,
            max_position_value_rub=1_000_000.0,
            max_order_value_rub=1_000_000.0,
            commission_buffer_fraction=0.0,
            portfolio_policy_configured=True,
            portfolio_policy_mode="ENFORCED",
            max_gross_exposure_rub=10_000.0,
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
    )
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(portfolio_state())
    central = CentralOrderManager(
        CentralOrderStore(tmp_path / "central_order_state.json"),
        account_id=ACCOUNT,
    )
    risk = RiskRuntimeAdapter(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=profiles,
        state_store=RiskStateStore(tmp_path / "risk_state.json"),
    )
    atomic_write_json(
        tmp_path / "portfolio_risk_metadata.json",
        {
            "version": 1,
            "instruments": [
                {
                    "instrument_id": "uid-sber",
                    "lot_size": 10,
                    "asset_class": "SHARE",
                    "currency": "RUB",
                }
            ],
        },
        write_checksum=True,
    )
    selected = profile("SBER", "CANDLE_INTERVAL_HOUR")
    selected_runtime = runtime(selected)

    result = CentralOrderCoordinator(central, repository, risk).coordinate(
        proposal(selected, selected_runtime),
        selected_runtime,
        selected,
        candles=candles(),
        lot_size=10,
        now=NOW_DT,
    )

    assert result.status == "QUEUED"
    assert result.portfolio_risk_shadow is not None
    assert result.portfolio_risk_shadow.status == "EVALUATED"
    assert result.portfolio_risk_shadow.decision is not None
    assert result.portfolio_risk_shadow.decision.mode == "OBSERVE_ONLY"
    assert result.portfolio_risk_shadow.execution_authorized is False
    assert central.state().queued[0].candidate.target_lots == 1


def test_unexplained_drift_classifier_is_reserved_for_invalid_targets() -> None:
    drift, unexplained = classify_shadow_drift(
        current_lots=0,
        requested_target_lots=1,
        actual_approved_target_lots=2,
        shadow_approved_target_lots=1,
    )

    assert drift == "UNEXPLAINED_ACTUAL_TARGET_OUTSIDE_PROPOSAL"
    assert unexplained is True
