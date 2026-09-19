"""Offline regression for the Q7 GUI quote and decision-audit boundary."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import trading_robot.gui_runtime_controller as gui_runtime
from trading_robot.global_scheduler import GlobalScheduler
from trading_robot.gui_runtime_controller import (
    GuiCoordinationRequest,
    GuiRuntimeBlockedError,
    GuiRuntimeController,
    _CoordinatingHooks,
    _GuiSchedulerJournalSink,
    _ProductionGuiHooks,
)
from trading_robot.journal import EventJournal

NOW = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)
SCOPE = "a" * 64
INSTRUMENT = "synthetic-instrument"


def _runtime():
    return SimpleNamespace(
        config=SimpleNamespace(
            instrument_id=INSTRUMENT,
            ticker="SYN",
            runtime_config_hash="b" * 64,
        )
    )


def _proposal(target: int):
    return SimpleNamespace(
        primary_strategy="sma_crossover",
        primary_target_lots=target,
        decisions={"sma_crossover": SimpleNamespace(signal=int(target > 0))},
        candle_time=NOW.isoformat(),
        strategy_profile_hash="c" * 64,
    )


def _price(*, time: datetime = NOW):
    return {
        "instrumentUid": INSTRUMENT,
        "price": {"units": "123", "nano": 500_000_000},
        "time": time.isoformat(),
    }


def _production_hooks(provider, *, clock=lambda: NOW):
    return _ProductionGuiHooks(
        provider=provider,
        risk_runtime=object(),
        portfolio_refresher=lambda: None,
        profiles={INSTRUMENT: object()},
        frames={INSTRUMENT: object()},
        lot_sizes={INSTRUMENT: 10},
        clock=clock,
    )


def test_exact_exchange_quote_reaches_central_request_without_candle_substitution():
    calls = []

    class Provider:
        def get_last_prices(self, instrument_ids):
            calls.append(tuple(instrument_ids))
            return [_price()]

    proposal = _proposal(2)
    request = _production_hooks(Provider()).coordination_request(
        _runtime(), proposal, NOW - timedelta(hours=1), NOW
    )

    assert calls == [(INSTRUMENT,)]
    assert request.proposal is proposal
    assert request.portfolio_risk_candidate_quote.unit_price_rub == 123.5
    assert request.portfolio_risk_candidate_quote.price_at == NOW
    assert request.portfolio_risk_candidate_quote.source == "TBANK_LAST_PRICE_EXCHANGE"
    assert request.evaluated_at == NOW


def test_production_quote_and_fresh_clock_reach_central_without_dispatch(
    tmp_path, monkeypatch
):
    proposal = _proposal(2)
    monkeypatch.setattr(
        gui_runtime, "build_strategy_proposal", lambda *_args, **_kwargs: proposal
    )
    provider = SimpleNamespace(get_last_prices=lambda _ids: [_price()])
    hooks = _production_hooks(provider, clock=lambda: NOW + timedelta(seconds=1))
    journal = EventJournal(tmp_path / "trading_events.db")
    outcome = SimpleNamespace(
        status="PORTFOLIO_RISK_BLOCKED",
        current_lots=0,
        approved_target_lots=0,
        preflight_status="PASS",
        risk_status="PASS",
        portfolio_risk_status="BLOCKED",
    )
    controller, calls = _controller(journal, outcome)

    assert (
        _CoordinatingHooks(controller, hooks).evaluate_closed_candle(
            _runtime(), NOW, NOW
        )
        is outcome
    )
    assert len(calls) == 1
    assert calls[0]["now"] == NOW + timedelta(seconds=1)
    assert calls[0]["portfolio_risk_candidate_quote"].source == (
        "TBANK_LAST_PRICE_EXCHANGE"
    )
    assert journal.recent(event_type="CENTRAL_COORDINATION_RESULT")[0]["action"] == (
        "BUY"
    )


@pytest.mark.parametrize(
    "prices",
    [
        [],
        [_price(), _price()],
        [{**_price(), "instrumentUid": "another-instrument"}],
        [{**_price(), "instrumentUid": type("TextSubclass", (str,), {})(INSTRUMENT)}],
        [{**_price(), "price": {"units": "0", "nano": 0}}],
        [{**_price(), "price": {"units": "123", "nano": True}}],
        [{**_price(), "price": {"units": "123", "nano": 1_000_000_000}}],
        [{**_price(), "price": {"units": "NaN", "nano": 0}}],
        [{**_price(), "price": {"units": "9" * 1000, "nano": 0}}],
        [{**_price(), "time": "PRIVATE_NOT_A_TIME"}],
        [_price(time=NOW - timedelta(seconds=301))],
        [_price(time=NOW + timedelta(seconds=6))],
    ],
)
def test_invalid_ambiguous_or_stale_quote_fails_closed(prices):
    provider = SimpleNamespace(get_last_prices=lambda _ids: prices)
    with pytest.raises(GuiRuntimeBlockedError, match="CANDIDATE_QUOTE_") as caught:
        _production_hooks(provider).coordination_request(
            _runtime(), _proposal(1), NOW, NOW
        )
    assert "PRIVATE" not in str(caught.value)


def test_provider_read_error_is_finite_and_does_not_leak_provider_text():
    def fail(_ids):
        raise RuntimeError("PRIVATE_TOKEN_OR_ACCOUNT_CANARY")

    with pytest.raises(GuiRuntimeBlockedError) as caught:
        _production_hooks(SimpleNamespace(get_last_prices=fail)).coordination_request(
            _runtime(), _proposal(1), NOW, NOW
        )
    assert caught.value.reason == "CANDIDATE_QUOTE_READ_FAILED"
    assert "PRIVATE" not in str(caught.value)


def test_quote_read_failure_preserves_signal_audit_without_central_mutation(
    tmp_path, monkeypatch
):
    proposal = _proposal(1)
    monkeypatch.setattr(
        gui_runtime, "build_strategy_proposal", lambda *_args, **_kwargs: proposal
    )

    def fail(_ids):
        raise RuntimeError("PRIVATE_PROVIDER_CANARY")

    journal = EventJournal(tmp_path / "trading_events.db")
    controller, calls = _controller(journal, SimpleNamespace(status="QUEUED"))
    hooks = _production_hooks(SimpleNamespace(get_last_prices=fail))
    with pytest.raises(GuiRuntimeBlockedError, match="CANDIDATE_QUOTE_READ_FAILED"):
        _CoordinatingHooks(controller, hooks).evaluate_closed_candle(
            _runtime(), NOW, NOW
        )
    assert len(journal.recent(event_type="PRIMARY_STRATEGY_DECISION")) == 1
    assert journal.recent(event_type="CENTRAL_COORDINATION_RESULT") == []
    assert calls == []


class _Hooks:
    def __init__(self, proposal, outcome):
        self.proposal = proposal
        self.outcome = outcome

    def evaluate_closed_candle(self, _runtime, _candle_time, _now):
        return self.proposal

    def coordination_request(self, _runtime, proposal, _candle_time, _now):
        return GuiCoordinationRequest(
            proposal=proposal,
            profile=object(),
            candles=object(),
            lot_size=10,
            evaluated_at=NOW + timedelta(seconds=1),
        )


def _controller(journal, outcome):
    calls = []

    class Coordinator:
        def coordinate(self, *_args, **kwargs):
            calls.append(kwargs)
            return outcome

    class Adapter:
        def dispatch_next(self, _portfolio):
            calls.append("DISPATCH")

    controller = object.__new__(GuiRuntimeController)
    controller.journal = journal
    controller.session_id = "synthetic-session"
    controller.account_scope_sha256 = SCOPE
    controller.central_order_coordinator = Coordinator()
    controller.execution_adapter = Adapter()
    controller.portfolio_repository = object()
    return controller, calls


@pytest.mark.parametrize(
    ("current", "target", "action"),
    [(0, 2, "BUY"), (2, 2, "HOLD"), (2, 0, "SELL")],
)
def test_strategy_and_central_audit_bind_signal_action_and_blocker(
    tmp_path, current, target, action
):
    journal = EventJournal(tmp_path / "trading_events.db")
    outcome = SimpleNamespace(
        status="PORTFOLIO_RISK_PRICE_UNAVAILABLE",
        current_lots=current,
        approved_target_lots=current,
        preflight_status="PASS",
        risk_status="PASS",
        portfolio_risk_status=None,
        reason="PRIVATE_ACCOUNT_AND_ORDER_CANARY",
    )
    controller, calls = _controller(journal, outcome)
    hooks = _CoordinatingHooks(controller, _Hooks(_proposal(target), outcome))

    assert hooks.evaluate_closed_candle(_runtime(), NOW, NOW) is outcome
    decision = journal.recent(event_type="PRIMARY_STRATEGY_DECISION")
    central = journal.recent(event_type="CENTRAL_COORDINATION_RESULT")
    assert len(decision) == len(central) == 1
    assert decision[0]["status"] == "PROPOSED_NOT_AUTHORIZED"
    assert decision[0]["payload"]["proposed_target_lots"] == target
    assert central[0]["action"] == action
    assert central[0]["status"] == "PORTFOLIO_RISK_PRICE_UNAVAILABLE"
    assert central[0]["payload"]["execution_authorized"] is False
    assert central[0]["account_id"] is None
    assert calls[0]["now"] == NOW + timedelta(seconds=1)
    assert "DISPATCH" not in calls
    assert "PRIVATE" not in json.dumps(decision + central)


def test_decision_audit_failure_prevents_quote_read_central_and_dispatch():
    class FailingJournal:
        def record(self, _event):
            raise RuntimeError("PRIVATE_AUDIT_ERROR")

    outcome = SimpleNamespace(status="QUEUED")
    controller, calls = _controller(FailingJournal(), outcome)

    class Hooks(_Hooks):
        def coordination_request(self, *_args):
            raise AssertionError("quote read must not occur")

    with pytest.raises(GuiRuntimeBlockedError, match="DECISION_AUDIT_UNAVAILABLE"):
        _CoordinatingHooks(
            controller, Hooks(_proposal(1), outcome)
        ).evaluate_closed_candle(_runtime(), NOW, NOW)
    assert calls == []


def test_coordination_audit_failure_prevents_dispatch_after_queued_result():
    class FailOnSecondRecord:
        def __init__(self):
            self.calls = 0

        def record(self, _event):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("PRIVATE_AUDIT_ERROR")

    journal = FailOnSecondRecord()
    outcome = SimpleNamespace(
        status="QUEUED",
        current_lots=0,
        approved_target_lots=1,
    )
    controller, calls = _controller(journal, outcome)
    with pytest.raises(GuiRuntimeBlockedError, match="DECISION_AUDIT_UNAVAILABLE"):
        _CoordinatingHooks(
            controller, _Hooks(_proposal(1), outcome)
        ).evaluate_closed_candle(_runtime(), NOW, NOW)
    assert journal.calls == 2
    assert len(calls) == 1
    assert "DISPATCH" not in calls


def test_scheduler_audit_is_wired_and_strips_private_account_and_detail(
    tmp_path, monkeypatch
):
    journal = EventJournal(tmp_path / "trading_events.db")
    controller = object.__new__(GuiRuntimeController)
    controller.journal = journal
    controller.account_id = "PRIVATE_RAW_ACCOUNT"
    controller.account_scope_sha256 = SCOPE
    controller.session_id = "synthetic-session"
    controller.runtime_store = object()
    controller._load_configured_set = lambda: SimpleNamespace(runtime_keys=("key",))
    captured = {}

    def restore(_store, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(runtimes=(SimpleNamespace(runtime_key="key"),))

    monkeypatch.setattr(GlobalScheduler, "restore", staticmethod(restore))
    controller.restore()
    captured["event_sink"].record_scheduler_event(
        SimpleNamespace(
            payload={"revision": 7, "detail": "PRIVATE_TOKEN_CANARY"},
            event_type="SCHEDULER_ACTION_FAILED",
            severity="ERROR",
            session_id="synthetic-session",
            account_id="PRIVATE_RAW_ACCOUNT",
            instrument_id=INSTRUMENT,
            ticker="SYN",
            candle_time=NOW.isoformat(),
            status="FAILED",
            action="DECISION_EVALUATION",
            config_hash="b" * 64,
            occurred_at=NOW,
        )
    )

    events = journal.recent(event_type="SCHEDULER_ACTION_FAILED")
    assert len(events) == 1
    assert events[0]["account_id"] is None
    assert events[0]["payload"] == {
        "account_scope_sha256": SCOPE,
        "revision": 7,
        "blocker": None,
    }
    assert "PRIVATE" not in json.dumps(events)


def test_scheduler_audit_retains_only_known_finite_quote_blocker(tmp_path):
    journal = EventJournal(tmp_path / "trading_events.db")
    sink = _GuiSchedulerJournalSink(journal, SCOPE)
    base = dict(
        event_type="SCHEDULER_ACTION_FAILED",
        severity="ERROR",
        session_id="synthetic-session",
        account_id="PRIVATE_RAW_ACCOUNT",
        instrument_id=INSTRUMENT,
        ticker="SYN",
        candle_time=NOW.isoformat(),
        status="FAILED",
        action="DECISION_EVALUATION",
        config_hash="b" * 64,
        occurred_at=NOW,
    )
    sink.record_scheduler_event(
        SimpleNamespace(
            **base,
            payload={"detail": "GuiRuntimeBlockedError: CANDIDATE_QUOTE_READ_FAILED"},
        )
    )
    sink.record_scheduler_event(
        SimpleNamespace(
            **base,
            payload={"detail": "GuiRuntimeBlockedError: PRIVATE_CANARY"},
        )
    )
    rows = journal.recent(event_type="SCHEDULER_ACTION_FAILED")
    assert {row["payload"]["blocker"] for row in rows} == {
        "CANDIDATE_QUOTE_READ_FAILED",
        None,
    }
    assert "PRIVATE" not in json.dumps(rows)
