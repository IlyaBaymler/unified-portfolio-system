"""Offline EMPTY lifecycle regressions; no broker, live authority or CL7 qualification.

Real Strategy/Risk/Central/dispatch are used. Portfolio post-fill snapshots are
explicit synthetic fixtures, not observations or a production refresh run.
"""
from dataclasses import replace
from datetime import timedelta

import pytest

from test_q7a_source_natural_cycle import ACCOUNT, NOW, UID, _case, _cycle
from test_portfolio_risk_adapter_v3_9 import (
    adapter_input, central, clean_empty_portfolio,
)
from trading_robot.central_order_manager import CentralOrderState, central_reservation_projection_hash


def _shift(case):
    frame = case.hooks.frames[UID].copy()
    frame.index += timedelta(minutes=5)
    case.hooks.frames[UID] = frame
    case.candle += timedelta(minutes=5)
    case.now += timedelta(minutes=5)
    case.provider.clock_at = case.provider.quote_at = case.now


@pytest.mark.parametrize("replace_proposal", [False, True])
def test_empty_reauthorization_or_replacement_reaches_single_fake_post(tmp_path, replace_proposal):
    case = _case(tmp_path, current=0, target=1)
    first = _cycle(case)
    assert first.status == "QUEUED"
    reserved = case.central.state().reserved_cash_kopecks
    if replace_proposal:
        _shift(case)
    case.provider.market_open = True
    case.provider.post_behavior = "submitted"
    second = _cycle(case)
    assert second.status == ("REPLACED" if replace_proposal else "REAUTHORIZED"), second.to_dict()
    assert (second.intent_id != first.intent_id) is replace_proposal
    assert case.provider.order_calls == 1
    assert case.provider.sent_ids == [second.intent_id]
    assert case.central.state().blocking_intent.status == "SUBMITTED"
    assert case.central.state().reserved_cash_kopecks == reserved
    quotes = case.provider.quote_calls
    assert _cycle(case).status == "ACCOUNT_BLOCKED"
    assert case.provider.order_calls == 1
    assert case.provider.quote_calls == quotes


def test_first_empty_buy_can_dispatch_without_ready_override(tmp_path):
    case = _case(tmp_path, current=0, target=1)
    case.provider.market_open = True
    case.provider.post_behavior = "submitted"
    result = _cycle(case)
    assert result.status == "QUEUED"
    assert case.repository.load(expected_account_id=ACCOUNT).state_status == "EMPTY"
    assert case.provider.order_calls == 1
    assert case.central.state().blocking_intent.intent_id == result.intent_id


@pytest.mark.parametrize("ending", ["CANCELLED", "FAILED"])
def test_terminal_unsent_history_allows_new_buy_not_replay(tmp_path, ending):
    case = _case(tmp_path, current=0, target=1)
    first = _cycle(case)
    if ending == "CANCELLED":
        case.central.cancel_queued(first.intent_id, reason="synthetic operator cancel")
    else:
        case.central.mark_pre_submit_failed(first.intent_id, reason="synthetic local failure")
    assert case.central.state().reserved_cash_kopecks == 0
    old = case.central.state().intents[0]
    _shift(case)
    case.provider.market_open = True
    case.provider.post_behavior = "submitted"
    second = _cycle(case)
    assert second.status == "QUEUED", second.to_dict()
    assert second.intent_id != first.intent_id
    assert case.central.state().intents[0] == old
    assert case.provider.sent_ids == [second.intent_id]


def test_terminal_history_is_not_active_reservation():
    selected = central(status="QUEUED")
    original = selected.intents[0]
    retired = original.transition("CANCELLED", detail="synthetic", outcome="CANCELLED", at=NOW.isoformat())
    selected = replace(selected, intents=(retired,))
    result = adapter_input(portfolio_state=clean_empty_portfolio(), central_state=selected)
    assert result.data_quality_flags == ()
    assert result.reservations == ()
    assert retired.reserved_cash_kopecks > 0  # Retained evidence must not be erased.
    assert selected.reserved_cash_kopecks == 0


def test_same_excluded_queued_reservation_no_double_count():
    selected = central(status="QUEUED")
    excluded = (selected.intents[0].intent_id,)
    before = selected.to_dict()
    result = adapter_input(portfolio_state=clean_empty_portfolio(), central_state=selected,
                           excluded_reservation_ids=excluded)
    assert result.data_quality_flags == ()
    assert result.reservations == ()
    assert result.reservation_projection_hash == central_reservation_projection_hash(
        selected, excluded_reservation_ids=excluded)
    assert selected.to_dict() == before
    assert selected.reserved_cash_kopecks > 0


def _retire(selected, status="CANCELLED"):
    intent = selected.intents[0]
    retired = intent.transition(status, detail="synthetic terminal", outcome="CANCELLED",
                                at=NOW.isoformat())
    return replace(selected, intents=(retired,))


def _snapshot(case, current, cash):
    """Inject explicit synthetic canonical evidence, NOT a broker refresh."""
    from trading_robot.portfolio_model import (
        CashBalance, PositionState, PositionOwnership, PortfolioTarget,
        OwnershipStatus, ReconciliationResult, ReconciliationStatus, PositionOrigin,
    )
    old = case.repository.load(expected_account_id=ACCOUNT)
    case.now += timedelta(minutes=1)
    case.provider.clock_at = case.provider.quote_at = case.now
    positions = ()
    if current:
        positions = (PositionState(
            instrument_id=UID, figi="", ticker="SBER", class_code="TQBR",
            asset_type="share", currency="rub", quantity=current * 10,
            actual_lots=current, average_price=105.0, current_price=105.0,
            market_value=current * 1050.0, expected_yield=0.0,
            target=PortfolioTarget(UID, current, "sma", case.profile.strategy_profile_hash),
            ownership=PositionOwnership("sma", case.profile.strategy_profile_hash,
                                        "CANDLE_INTERVAL_HOUR"),
            ownership_status=OwnershipStatus.ATTRIBUTED, pending_orders=(),
            reconciliation=ReconciliationResult(
                UID, ReconciliationStatus.MATCHED, False, (), current, current,
            ), origin=PositionOrigin.STRATEGY,
        ),)
    saved = replace(
        old, account=replace(old.account, total_value=cash + current * 1050.0,
                             securities_value=current * 1050.0,
                             cash_balances=(CashBalance("rub", cash),)),
        positions=positions, state_status="READY" if current else "EMPTY",
        revision=old.revision + 1, snapshot_at=case.now.isoformat(),
        generated_at=case.now.isoformat(),
    )
    case.repository.save(saved, expected_revision=old.revision, allow_equal_revision=False)
    return saved


def _choose_market(case, target):
    case.provider.target = target
    old_index = case.hooks.frames[UID].index.copy()
    frame = case.provider.get_candles(UID, case.now, case.now, interval="CANDLE_INTERVAL_HOUR", limit=80)
    frame.index = old_index
    case.hooks.frames[UID] = frame
    _shift(case)


def test_synthetic_buy_fill_hold_sell_fill_flat_and_new_buy(tmp_path, monkeypatch):
    """Real risk accounting/reconciliation on synthetic snapshots, LEGACY only."""
    import trading_robot.central_order_manager as central_module
    case = _case(tmp_path, current=0, target=1)
    monkeypatch.setattr(central_module, "_now", lambda: case.now.isoformat())
    case.provider.market_open = True
    case.provider.post_behavior = "submitted"
    original_post = case.provider.post_order

    def correlated_fake_post(*args, **kwargs):
        response = original_post(*args, **kwargs)
        return dict(response, orderId="synthetic-" + kwargs["order_id"])

    monkeypatch.setattr(case.provider, "post_order", correlated_fake_post)
    buy = _cycle(case)
    assert buy.status == "QUEUED"
    assert case.central.state().blocking_intent.status == "SUBMITTED"
    assert case.provider.sent_ids == [buy.intent_id]
    assert _cycle(case).status == "ACCOUNT_BLOCKED"
    _snapshot(case, 1, 1_000_000.0 - 1050.0)
    buy_terminal = case.central.mark_reconciled(
        buy.intent_id, portfolio_repository=case.repository, outcome="FILLED", executed_lots=1,
        risk_runtime=case.risk, execution_price_rub=105.0,
        execution_price_source="SYNTHETIC_EXECUTION_PRICE",
    )
    assert buy_terminal.risk_execution_status == "RECORDED"
    assert case.central.state().reserved_cash_kopecks == 0
    q = case.provider.quote_calls
    hold = _cycle(case)
    assert hold.status == "NO_POSITION_CHANGE", hold.to_dict()
    assert case.provider.quote_calls == q
    assert case.provider.sent_ids == [buy.intent_id]
    _choose_market(case, 0)
    sell = _cycle(case)
    assert sell.status == "QUEUED", sell.to_dict()
    assert case.central.state().blocking_intent.candidate.direction == "SELL"
    assert case.provider.sent_ids == [buy.intent_id, sell.intent_id]
    _snapshot(case, 0, 1_000_000.0)
    sell_terminal = case.central.mark_reconciled(
        sell.intent_id, portfolio_repository=case.repository, outcome="FILLED", executed_lots=1,
        risk_runtime=case.risk, execution_price_rub=105.0,
        execution_price_source="SYNTHETIC_EXECUTION_PRICE",
    )
    assert sell_terminal.risk_execution_status == "RECORDED"
    assert case.central.state().reserved_cash_kopecks == 0
    history = case.central.state().intents
    assert {i.status for i in history} == {"RECONCILED"}
    q = case.provider.quote_calls
    assert _cycle(case).status == "NO_POSITION_CHANGE"
    assert case.provider.quote_calls == q
    report = case.portfolio_risk.recalculate_current(
        case.central, case.repository, evaluated_at=case.now)
    assert "PORTFOLIO_STATUS_EMPTY" not in report.warnings
    assert report.metrics.cash_reserved_rub == 0.0
    _choose_market(case, 1)
    new_buy = _cycle(case)
    assert new_buy.status == "QUEUED", new_buy.to_dict()
    assert new_buy.intent_id not in {buy.intent_id, sell.intent_id}
    assert case.provider.sent_ids == [buy.intent_id, sell.intent_id, new_buy.intent_id]
    assert case.central.state().intents[:2] == history
    assert case.repository.load(expected_account_id=ACCOUNT).state_status == "EMPTY"
    assert case.portfolio_risk.state_store.load_account(ACCOUNT).daily_turnover_rub == 2100.0
    assert _cycle(case).status == "ACCOUNT_BLOCKED"
    assert case.provider.order_calls == 3


@pytest.mark.parametrize("status", ["IN_FLIGHT", "SUBMITTED", "UNCERTAIN"])
@pytest.mark.parametrize("exclude", [False, True])
def test_account_blockers_are_not_suppressed_by_exclusion(status, exclude):
    selected = central(status="QUEUED")
    intent = selected.intents[0].transition("IN_FLIGHT", detail="synthetic", at=NOW.isoformat())
    if status == "SUBMITTED":
        intent = intent.transition(status, detail="synthetic", broker_order_id="synthetic-id", at=NOW.isoformat())
    elif status == "UNCERTAIN":
        intent = intent.transition(status, detail="synthetic", uncertainty_reason="synthetic timeout", at=NOW.isoformat())
    selected = replace(selected, intents=(intent,))
    result = adapter_input(portfolio_state=clean_empty_portfolio(), central_state=selected,
                           excluded_reservation_ids=(intent.intent_id,) if exclude else ())
    assert "PORTFOLIO_STATUS_EMPTY" in result.data_quality_flags
    assert result.blocking_order_ids == (intent.intent_id,)


@pytest.mark.parametrize("context", ["terminal", "own_queued"])
@pytest.mark.parametrize("change", [
    "source", "freshness", "migration", "blocking", "positions", "nav_missing",
    "nav_zero", "nav_negative", "rub_missing", "rub_zero", "rub_negative",
])
def test_original_financial_guards_survive_lifecycle_extension(context, change):
    from trading_robot.portfolio_model import CashBalance, SnapshotFreshness, PortfolioMigrationMetadata
    from test_portfolio_risk_adapter_v3_9 import portfolio
    state = clean_empty_portfolio()
    selected = central(status="QUEUED")
    excluded = (selected.intents[0].intent_id,)
    if context == "terminal":
        selected, excluded = _retire(selected), ()
    if change == "source":
        state = replace(state, portfolio_source="LEGACY")
    elif change == "freshness":
        state = replace(state, freshness=SnapshotFreshness.STALE)
    elif change == "migration":
        state = replace(state, migration=PortfolioMigrationMetadata.pending_from_v1())
    elif change == "blocking":
        state = replace(state, blocking=True)
    elif change == "positions":
        state = replace(state, positions=portfolio().positions)
    elif change.startswith("nav"):
        state = replace(state, account=replace(state.account, total_value={
            "nav_missing": None, "nav_zero": 0.0, "nav_negative": -1.0}[change]))
    elif change == "rub_missing":
        state = replace(state, account=replace(state.account, cash_balances=()))
    else:
        state = replace(state, account=replace(state.account, cash_balances=(
            CashBalance("rub", 0.0 if change == "rub_zero" else -1.0),)))
    if change in {"nav_negative", "rub_negative"}:
        from trading_robot.portfolio_risk_model import PortfolioRiskInputError
        with pytest.raises(PortfolioRiskInputError, match="non-negative"):
            adapter_input(portfolio_state=state, central_state=selected,
                          excluded_reservation_ids=excluded)
        return
    result = adapter_input(portfolio_state=state, central_state=selected,
                           excluded_reservation_ids=excluded)
    assert "PORTFOLIO_STATUS_EMPTY" in result.data_quality_flags


@pytest.mark.parametrize("context", ["terminal", "own_queued"])
@pytest.mark.parametrize("field", ["nav", "cash"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_financial_values_are_rejected(context, field, value):
    from trading_robot.portfolio_model import CashBalance
    from trading_robot.portfolio_risk_model import PortfolioRiskInputError
    state = clean_empty_portfolio()
    selected = central(status="QUEUED")
    excluded = (selected.intents[0].intent_id,)
    if context == "terminal":
        selected, excluded = _retire(selected), ()
    account = (replace(state.account, total_value=value) if field == "nav" else
               replace(state.account, cash_balances=(CashBalance("rub", value),)))
    with pytest.raises(PortfolioRiskInputError):
        adapter_input(portfolio_state=replace(state, account=account), central_state=selected,
                      excluded_reservation_ids=excluded)


@pytest.mark.parametrize("context", ["terminal", "own_queued"])
@pytest.mark.parametrize("tamper", ["aggregate", "projection"])
def test_derived_reservation_corruption_fails_closed(context, tamper, monkeypatch):
    import trading_robot.portfolio_risk_adapter as adapter_module
    selected = central(status="QUEUED")
    excluded = (selected.intents[0].intent_id,)
    if context == "terminal":
        selected, excluded = _retire(selected), ()
    if tamper == "aggregate":
        expected = selected.reserved_cash_kopecks
        monkeypatch.setattr(CentralOrderState, "reserved_cash_kopecks", property(lambda _s: expected + 1))
    else:
        original = adapter_module.central_reservation_projection_hash
        monkeypatch.setattr(adapter_module, "central_reservation_projection_hash",
                            lambda s, **kw: "0" * 64 if s is selected else original(s, **kw))
    result = adapter_input(portfolio_state=clean_empty_portfolio(), central_state=selected,
                           excluded_reservation_ids=excluded)
    assert "PORTFOLIO_STATUS_EMPTY" in result.data_quality_flags


def test_unexcluded_queued_reservation_is_retained_and_blocks_empty():
    selected = central(status="QUEUED")
    result = adapter_input(portfolio_state=clean_empty_portfolio(), central_state=selected)
    assert "PORTFOLIO_STATUS_EMPTY" in result.data_quality_flags
    assert result.reserved_cash_rub == selected.reserved_cash_kopecks / 100
    assert result.reservations[0].reservation_id == selected.intents[0].intent_id


def test_unknown_exclusion_is_rejected_before_input():
    from trading_robot.portfolio_risk_adapter import PortfolioRiskAdapterError
    with pytest.raises(PortfolioRiskAdapterError, match="does not exist"):
        adapter_input(portfolio_state=clean_empty_portfolio(), excluded_reservation_ids=("unknown",))


@pytest.mark.parametrize("context", ["terminal", "own_queued"])
def test_cross_account_lifecycle_is_rejected(context):
    from trading_robot.portfolio_risk_adapter import PortfolioRiskAdapterError
    state = clean_empty_portfolio()
    selected = central(status="QUEUED")
    excluded = (selected.intents[0].intent_id,)
    if context == "terminal":
        selected, excluded = _retire(selected), ()
    state = replace(state, account=replace(state.account, account_id="different-account"))
    with pytest.raises(PortfolioRiskAdapterError, match="account scopes differ"):
        adapter_input(portfolio_state=state, central_state=selected, excluded_reservation_ids=excluded)


@pytest.mark.parametrize("change", ["queue", "reserve", "risk_state", "portfolio"])
def test_empty_reauthorized_dispatch_still_revalidates_custody(tmp_path, change):
    from trading_robot.portfolio_risk_runtime import PortfolioRiskAuthorizationError
    from trading_robot.risk import RiskState
    case = _case(tmp_path, current=0, target=1)
    _cycle(case)
    repeated = _cycle(case)
    assert repeated.status == "REAUTHORIZED"
    selected = case.central.state()
    intent = selected.queued[0]
    state = case.repository.load(expected_account_id=ACCOUNT)
    expected = {"queue": "PORTFOLIO_RISK_QUEUE_CHANGED", "reserve": "PORTFOLIO_RISK_RESERVATION_CHANGED",
                "risk_state": "PORTFOLIO_RISK_STATE_CHANGED", "portfolio": "PORTFOLIO_RISK_CANONICAL_CHANGED"}[change]
    if change == "queue":
        selected = replace(selected, revision=selected.revision + 1)
    elif change == "reserve":
        selected = selected.replace_intent(replace(intent, reserved_cash_kopecks=intent.reserved_cash_kopecks + 1))
    elif change == "risk_state":
        case.portfolio_risk.state_store.save_account(ACCOUNT, RiskState(kill_switch_active=True))
    else:
        state = replace(state, revision=state.revision + 1)
    with pytest.raises(PortfolioRiskAuthorizationError) as error:
        case.portfolio_risk.validate_dispatch(portfolio=state, central_orders=selected, intent=intent,
                                              evaluated_at=case.now)
    assert error.value.status == expected
    assert case.provider.order_calls == 0


def _two_queued():
    from trading_robot.central_order_manager import CentralOrderIntent
    selected = central(status="QUEUED")
    first = selected.intents[0]
    candidate = replace(first.candidate, instrument_id="second-instrument", runtime_key="second-runtime")
    auth = replace(first.authorization, instrument_id=candidate.instrument_id)
    second = CentralOrderIntent.create(candidate, auth, queue_sequence=2, reserved_cash_kopecks=100,
                                       created_at=NOW.isoformat())
    return replace(selected, intents=(first, second), next_sequence=3)


@pytest.mark.parametrize("exclude_both", [False, True])
def test_other_queued_reserve_cannot_be_erased_to_admit_empty(exclude_both):
    selected = _two_queued()
    excluded = tuple(i.intent_id for i in selected.intents) if exclude_both else (selected.intents[0].intent_id,)
    result = adapter_input(portfolio_state=clean_empty_portfolio(), central_state=selected,
                           excluded_reservation_ids=excluded)
    assert "PORTFOLIO_STATUS_EMPTY" in result.data_quality_flags
    if not exclude_both:
        assert result.reserved_cash_rub == 1.0
        assert result.reservations[0].reservation_id == selected.intents[1].intent_id
    assert selected.reserved_cash_kopecks == 205_100


def test_excluding_retired_history_does_not_exclude_other_live_cash():
    selected = _two_queued()
    first, second = selected.intents
    retired = first.transition("CANCELLED", detail="synthetic", outcome="CANCELLED", at=NOW.isoformat())
    selected = replace(selected, intents=(retired, second))
    result = adapter_input(portfolio_state=clean_empty_portfolio(), central_state=selected,
                           excluded_reservation_ids=(first.intent_id,))
    assert "PORTFOLIO_STATUS_EMPTY" in result.data_quality_flags
    assert result.reserved_cash_rub == 1.0


@pytest.mark.parametrize("current,target", [(1, 2), (1, 0)])
def test_queued_nonflat_candidate_cannot_use_empty_exception(current, target):
    from trading_robot.central_order_manager import CentralOrderIntent
    selected = central(status="QUEUED")
    first = selected.intents[0]
    candidate = replace(first.candidate, current_lots=current, target_lots=target)
    auth = replace(first.authorization, authorized_target_lots=target)
    intent = CentralOrderIntent.create(candidate, auth, queue_sequence=1,
                                       reserved_cash_kopecks=0 if target == 0 else 205_000,
                                       created_at=NOW.isoformat())
    selected = replace(selected, intents=(intent,))
    result = adapter_input(portfolio_state=clean_empty_portfolio(), central_state=selected,
                           excluded_reservation_ids=(intent.intent_id,))
    assert "PORTFOLIO_STATUS_EMPTY" in result.data_quality_flags


def test_central_rejects_exclusion_of_other_instrument_even_after_adapter_fix(tmp_path):
    from trading_robot.central_order_manager import CentralOrderConflictError
    case = _case(tmp_path, current=0, target=1)
    _cycle(case)
    selected = case.central.state()
    intent = selected.queued[0]
    proof = intent.authorization.portfolio_risk
    candidate = replace(intent.candidate, instrument_id="uid-lkoh", ticker="LKOH")
    forged = replace(proof, central_order_revision=selected.revision,
                     excluded_reservation_ids=(intent.intent_id,),
                     reservation_projection_hash=central_reservation_projection_hash(
                         selected, excluded_reservation_ids=(intent.intent_id,)),
                     admission_central_revision=None, admission_reservation_projection_hash=None)
    auth = replace(intent.authorization, instrument_id="uid-lkoh", portfolio_risk=forged)
    with pytest.raises(CentralOrderConflictError, match="replaceable reservation scope"):
        case.central.admit_portfolio(lambda state: (candidate, auth))
    assert case.central.state() == selected
    assert case.provider.order_calls == 0


@pytest.mark.parametrize("replacement", [False, True])
def test_ambiguous_reauthorized_post_never_replayed_on_next_cycle(tmp_path, replacement):
    case = _case(tmp_path, current=0, target=1)
    first = _cycle(case)
    if replacement:
        _shift(case)
    case.provider.market_open = True
    case.provider.post_behavior = "ambiguous"
    second = _cycle(case)
    assert second.status == ("REPLACED" if replacement else "REAUTHORIZED")
    assert case.central.state().blocking_intent.status == "UNCERTAIN"
    assert case.provider.sent_ids == [second.intent_id]
    assert (first.intent_id != second.intent_id) is replacement
    before = case.central.state()
    q = case.provider.quote_calls
    assert _cycle(case).status == "ACCOUNT_BLOCKED"
    assert case.central.state() == before
    assert case.provider.quote_calls == q
    assert case.provider.order_calls == 1


def test_hold_cancels_queued_buy_then_new_candle_can_buy_from_same_empty(tmp_path, monkeypatch):
    import trading_robot.central_order_manager as central_module
    case = _case(tmp_path, current=0, target=1)
    monkeypatch.setattr(central_module, "_now", lambda: case.now.isoformat())
    first = _cycle(case)
    q = case.provider.quote_calls
    _choose_market(case, 0)
    hold = _cycle(case)
    assert hold.status == "CANCELLED_NO_POSITION_CHANGE"
    assert hold.cancelled_intent_id == first.intent_id
    assert case.provider.quote_calls == q
    assert case.central.state().reserved_cash_kopecks == 0
    assert case.provider.order_calls == 0
    _choose_market(case, 1)
    case.provider.market_open = True
    case.provider.post_behavior = "submitted"
    result = _cycle(case)
    assert result.status == "QUEUED"
    assert case.provider.sent_ids == [result.intent_id]
    assert result.intent_id != first.intent_id


def test_reauthorization_uses_new_cash_not_the_excluded_old_budget(tmp_path):
    from trading_robot.portfolio_model import CashBalance
    case = _case(tmp_path, current=0, target=1)
    _cycle(case)
    state = case.repository.load(expected_account_id=ACCOUNT)
    case.repository.save(replace(state, revision=state.revision + 1,
                                  account=replace(state.account, total_value=1.0,
                                                  cash_balances=(CashBalance("rub", 1.0),))))
    case.provider.market_open = True
    case.provider.post_behavior = "submitted"
    result = _cycle(case)
    assert result.status not in {"QUEUED", "REAUTHORIZED", "REPLACED"}, result.to_dict()
    assert case.provider.order_calls == 0


def test_proof_hash_tamper_is_not_excused_by_empty_lifecycle(tmp_path):
    from trading_robot.portfolio_risk_runtime import PortfolioRiskAuthorizationError
    case = _case(tmp_path, current=0, target=1)
    _cycle(case)
    assert _cycle(case).status == "REAUTHORIZED"
    selected = case.central.state()
    intent = selected.queued[0]
    proof = replace(intent.authorization.portfolio_risk, input_hash="0" * 64)
    altered = replace(intent, authorization=replace(intent.authorization, portfolio_risk=proof))
    with pytest.raises(PortfolioRiskAuthorizationError) as error:
        case.portfolio_risk.validate_dispatch(
            portfolio=case.repository.load(expected_account_id=ACCOUNT), central_orders=selected,
            intent=altered, evaluated_at=case.now)
    assert error.value.status == "PORTFOLIO_RISK_PROOF_MISMATCH"
    assert case.provider.order_calls == 0
