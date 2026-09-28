"""STEP8: cumulative partial and zero terminal recovery, synthetic provider only.

Tests call the shipped factory/controller/refresher, never economic owner methods
to finish a fill. Active partials keep the full reservation and Central blocker.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest
from test_q7a_source_desktop_flow import desktop_case, _desktop_cycle
from test_q7a_source_provider_refresh import refresh_case, _manager, money, position, order
from test_q7a_source_fill_recovery import _pending, _fill, _reconcile, _multilot, _snapshot
from test_q7a_source_natural_cycle import ACCOUNT, UID


def _terminal(x, intent, executed, status="CANCELLED", *, stages=None):
    c = intent.candidate
    x.advance()
    raw = order(intent.intent_id, intent.broker_order_id or "uncertain-exchange",
                direction=c.direction, requested=c.requested_lots,
                executed=executed, status=status)
    raw["stages"] = (deepcopy(stages) if stages is not None else
        [{"tradeId": "t-" + intent.intent_id, "quantity": str(executed),
          "price": money(105), "executionTime": x.p.clock_at.isoformat()}] if executed else [])
    x.p.receipts[intent.intent_id] = raw
    x.p.receipts[raw["orderId"]] = raw
    x.p.orders = [raw] if status == "PARTIALLYFILL" else []
    actual = c.current_lots + (executed if c.direction == "BUY" else -executed)
    x.p.payload["positions"] = [position(actual)] if actual else []
    x.p.wallet_rub = 1_000_000 - 1050 * actual
    x.p.payload["totalAmountCurrencies"] = money(x.p.wallet_rub)
    x.p.payload["totalAmountShares"] = money(1050 * actual)
    return raw


def _risk(x):
    return x.c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT)


def _central(x):
    return x.c.central_order_coordinator.manager


def _final(x, intent):
    return next(i for i in _central(x).state().intents if i.intent_id == intent.intent_id)


def _recompose(x, desktop_case, monkeypatch):
    import desktop_gui
    monkeypatch.setattr(desktop_gui, "_PRODUCTION_COMPOSITION", None)
    x.c = desktop_case.compose()
    x.manager = _manager(x.c)


def _sell(x, desktop_case, monkeypatch):
    _multilot(x, desktop_case, monkeypatch)
    buy = _pending(x)
    _fill(x, buy)
    _reconcile(x)
    x.p.target = 0
    x.p.clock_at += timedelta(hours=1)
    return _pending(x)


def _progress_file(x):
    files = list((x.root / "desktop_partial_progress").glob("*.json"))
    assert len(files) == 1
    return files[0]


@pytest.mark.parametrize("status", ["CANCELLED", "REJECTED"])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("uncertain", [False, True])
def test_zero_terminal_closes_without_execution_and_without_second_post(
        refresh_case, desktop_case, monkeypatch, status, direction, uncertain):
    x = refresh_case
    if direction == "SELL":
        intent = _sell(x, desktop_case, monkeypatch)
        if uncertain:
            # Exercise already persisted UNCERTAIN without creating a new order.
            intent = _central(x).mark_uncertain(intent.intent_id, reason="synthetic ambiguity")
    else:
        intent = _pending(x, uncertain=uncertain)
    prior = _risk(x)
    posts = x.p.order_calls
    _terminal(x, intent, 0, status)
    state = _reconcile(x)
    final = _final(x, intent)
    assert final.status == "RECONCILED" and final.outcome == status
    assert final.executed_lots == 0 and final.risk_execution_status == "NOT_REQUIRED"
    assert final.risk_execution_id is None
    assert _central(x).state().blocking_intent is None
    assert _central(x).state().reserved_cash_kopecks == 0
    assert final.reserved_cash_kopecks == intent.reserved_cash_kopecks  # history not erased
    assert state.position(UID).actual_lots == state.position(UID).target_lots == intent.candidate.current_lots
    assert not state.blocking
    if direction == "SELL": assert state.position(UID).ownership is not None
    else: assert state.position(UID).ownership is None
    after = _risk(x)
    assert after.recorded_execution_ids == prior.recorded_execution_ids
    assert after.daily_turnover_rub == prior.daily_turnover_rub
    assert after.daily_order_count == prior.daily_order_count
    _reconcile(x)
    assert x.p.order_calls == posts
    assert _risk(x).recorded_execution_ids == prior.recorded_execution_ids


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("executed", [1, 2])
@pytest.mark.parametrize("observed", [False, True], ids=["fast-terminal", "cached-partial"])
def test_partial_terminal_updates_actual_target_and_records_only_executed_lots(
        refresh_case, desktop_case, monkeypatch, direction, executed, observed):
    x = refresh_case
    if direction == "BUY":
        _multilot(x, desktop_case, monkeypatch)
        intent = _pending(x)
    else:
        intent = _sell(x, desktop_case, monkeypatch)
    baseline = _risk(x)
    posts = x.p.order_calls
    reserve = _central(x).state().reserved_cash_kopecks
    raw = _terminal(x, intent, executed, "PARTIALLYFILL" if observed else "CANCELLED")
    if observed:
        stages = deepcopy(raw["stages"])
        state = _reconcile(x)
        assert state.blocking
        assert _central(x).state().blocking_intent == intent
        assert _central(x).state().reserved_cash_kopecks == reserve
        assert _risk(x).recorded_execution_ids == baseline.recorded_execution_ids
        assert state.position(UID).target_lots != state.position(UID).actual_lots
        _terminal(x, intent, executed, "CANCELLED", stages=stages)
    state = _reconcile(x)
    final = _final(x, intent)
    assert final.status == "RECONCILED" and final.outcome == "PARTIALLY_FILLED"
    assert final.executed_lots == executed and final.risk_execution_id == intent.intent_id
    assert final.candidate == intent.candidate  # submitted request is immutable
    expected = intent.candidate.current_lots + (executed if direction == "BUY" else -executed)
    assert state.position(UID).actual_lots == state.position(UID).target_lots == expected
    assert state.position(UID).ownership.strategy_id == intent.candidate.strategy_id
    assert not state.blocking and _central(x).state().blocking_intent is None
    assert _central(x).state().reserved_cash_kopecks == 0
    assert final.reserved_cash_kopecks == intent.reserved_cash_kopecks
    after = _risk(x)
    assert after.daily_turnover_rub == baseline.daily_turnover_rub + executed * 1050
    assert after.daily_order_count == baseline.daily_order_count + 1
    assert after.recorded_execution_ids == (*baseline.recorded_execution_ids, intent.intent_id)
    _reconcile(x); _reconcile(x)
    assert _risk(x).daily_order_count == after.daily_order_count
    assert x.p.order_calls == posts


@pytest.mark.parametrize("final_status,executed", [("FILL", 3), ("CANCELLED", 2)])
@pytest.mark.parametrize("restart", [False, True])
def test_cumulative_partial_progress_can_finish_without_rewriting_earlier_trades(
        refresh_case, desktop_case, monkeypatch, final_status, executed, restart):
    x = refresh_case
    _multilot(x, desktop_case, monkeypatch)
    intent = _pending(x)
    first = _terminal(x, intent, 1, "PARTIALLYFILL")
    stages = deepcopy(first["stages"])
    partial = _reconcile(x)
    assert partial.blocking and partial.position(UID).ownership is None
    assert _risk(x).daily_order_count == 0
    _reconcile(x)  # identical cumulative repeat is not a second execution
    if restart: _recompose(x, desktop_case, monkeypatch)
    x.advance()
    stages.append({"tradeId": "second", "quantity": str(executed - 1),
                   "price": money(105), "executionTime": x.p.clock_at.isoformat()})
    _terminal(x, intent, executed, final_status, stages=stages)
    state = _reconcile(x)
    assert not state.blocking
    assert state.position(UID).actual_lots == state.position(UID).target_lots == executed
    assert _final(x, intent).outcome == ("FILLED" if final_status == "FILL" else "PARTIALLY_FILLED")
    assert _risk(x).daily_turnover_rub == executed * 1050
    assert _risk(x).daily_order_count == 1 and x.p.order_calls == 1


@pytest.mark.parametrize("damage", ["drop_trade", "rewrite_price", "rewrite_quantity", "rewrite_time",
                                   "regress", "zero_cancel", "zero_reject", "wrong_exchange"])
def test_terminal_cannot_erase_or_rewrite_persisted_partial_evidence(
        refresh_case, desktop_case, monkeypatch, damage):
    x = refresh_case
    _multilot(x, desktop_case, monkeypatch)
    intent = _pending(x)
    partial = _terminal(x, intent, 2, "PARTIALLYFILL")
    saved_stages = deepcopy(partial["stages"])
    _reconcile(x)
    raw = _terminal(x, intent, 2, stages=saved_stages)
    if damage == "drop_trade": raw["stages"][0]["tradeId"] = "different"
    elif damage == "rewrite_price":
        raw["stages"][0]["price"] = money(106); raw["averagePositionPrice"] = money(106)
    elif damage == "rewrite_quantity":
        raw["stages"][0]["quantity"] = "1"
        raw["stages"].append({**deepcopy(raw["stages"][0]), "tradeId": "split"})
    elif damage == "rewrite_time": raw["stages"][0]["executionTime"] = x.p.clock_at.isoformat()
    elif damage == "regress":
        raw["lotsExecuted"] = "1"; raw["stages"][0]["quantity"] = "1"
    elif damage in {"zero_cancel", "zero_reject"}:
        raw["lotsExecuted"] = "0"; raw["stages"] = []
        if damage == "zero_reject": raw["executionReportStatus"] = "EXECUTION_REPORT_STATUS_REJECTED"
    elif damage == "wrong_exchange": raw["orderId"] = "other"
    before = _snapshot(x)
    checkpoint = _progress_file(x).read_bytes()
    with pytest.raises(RuntimeError): _reconcile(x)
    assert _snapshot(x) == before and _progress_file(x).read_bytes() == checkpoint
    assert _central(x).state().blocking_intent is not None
    assert _risk(x).recorded_execution_ids == ()


@pytest.mark.parametrize("damage", ["checksum", "duplicate_key", "delete", "lost_canonical_marker", "position_drift", "symlink"])
def test_partial_progress_corruption_does_not_unlock_recovery(
        refresh_case, desktop_case, monkeypatch, damage):
    x = refresh_case
    _multilot(x, desktop_case, monkeypatch)
    intent = _pending(x)
    raw = _terminal(x, intent, 1, "PARTIALLYFILL")
    stages = deepcopy(raw["stages"])
    _reconcile(x)
    path = _progress_file(x)
    if damage == "checksum": path.write_text('{"payload":{},"sha256":"wrong"}')
    elif damage == "duplicate_key": path.write_text('{"payload":{},"payload":{},"sha256":"wrong"}')
    elif damage == "delete": path.unlink()
    elif damage == "symlink":
        data = path.read_bytes(); path.unlink()
        target = x.root / "foreign.json"; target.write_bytes(data); path.symlink_to(target)
    else:
        state = x.manager.repository.load(expected_account_id=ACCOUNT)
        if damage == "lost_canonical_marker": state = replace(state, last_transaction_id="foreign-refresh")
        else:
            pos = state.position(UID)
            state = replace(state, positions=(replace(pos, last_candle_time="2026-08-13T00:00:00Z"),))
        x.manager.repository.save(state)
    _terminal(x, intent, 1, stages=stages)
    before = _snapshot(x)
    with pytest.raises(RuntimeError): _reconcile(x)
    assert _snapshot(x) == before
    assert _central(x).state().blocking_intent.intent_id == intent.intent_id


@pytest.mark.parametrize("damage", ["over_requested", "rejected_partial", "full_cancel", "zero_with_trade",
    "stage_short", "duplicate", "price_currency", "future", "bool_executed", "missing_price",
    "wrong_account", "wrong_instrument", "wrong_direction", "actual_mismatch", "foreign_pending"])
def test_bad_terminal_receipt_keeps_account_blocked(refresh_case, desktop_case, monkeypatch, damage):
    x = refresh_case
    _multilot(x, desktop_case, monkeypatch)
    intent = _pending(x)
    raw = _terminal(x, intent, 1)
    if damage == "over_requested": raw["lotsExecuted"] = "4"
    elif damage == "rejected_partial": raw["executionReportStatus"] = "EXECUTION_REPORT_STATUS_REJECTED"
    elif damage == "full_cancel": raw["lotsExecuted"] = "3"; raw["stages"][0]["quantity"] = "3"
    elif damage == "zero_with_trade": raw["lotsExecuted"] = "0"
    elif damage == "stage_short": raw["lotsExecuted"] = "2"
    elif damage == "duplicate": raw["stages"] *= 2
    elif damage == "price_currency": raw["stages"][0]["price"] = money(105,"usd")
    elif damage == "future": raw["stages"][0]["executionTime"] = (x.p.clock_at + timedelta(hours=1)).isoformat()
    elif damage == "bool_executed": raw["lotsExecuted"] = True
    elif damage == "missing_price": raw["stages"][0].pop("price")
    elif damage == "wrong_account": raw["accountId"] = "PRIVATE_ACCOUNT"
    elif damage == "wrong_instrument": raw["instrumentUid"] = "uid-lkoh"
    elif damage == "wrong_direction": raw["direction"] = "ORDER_DIRECTION_SELL"
    elif damage == "actual_mismatch": x.p.payload["positions"] = [position(2)]
    elif damage == "foreign_pending": x.p.orders = [order("foreign", "exchange-foreign")]
    before = _snapshot(x)
    with pytest.raises(RuntimeError) as error: _reconcile(x)
    assert "PRIVATE" not in str(error.value)
    assert _snapshot(x) == before
    assert _central(x).state().blocking_intent.intent_id == intent.intent_id


@pytest.mark.parametrize("point", ["before_partial_commit", "after_canonical", "after_risk", "canonical_journal"])
@pytest.mark.parametrize("restart", [False, True])
def test_partial_terminal_crash_boundaries_do_not_double_count(
        refresh_case, desktop_case, monkeypatch, point, restart):
    x = refresh_case
    _multilot(x, desktop_case, monkeypatch)
    intent = _pending(x)
    raw = _terminal(x, intent, 1, "PARTIALLYFILL")
    stages = deepcopy(raw["stages"])
    if point != "before_partial_commit":
        _reconcile(x)
        _terminal(x, intent, 1, stages=stages)
    central = _central(x)
    if point == "before_partial_commit": owner, name = x.manager.transaction_coordinator, "commit"
    elif point == "after_canonical": owner, name = central, "mark_reconciled"
    elif point == "after_risk": owner, name = central.store, "mutate"
    else: owner, name = x.manager.transaction_coordinator, "_record"
    original = getattr(owner, name)
    def fail(*args, **kwargs):
        if point == "canonical_journal" and args[0] != "CANONICAL_TRANSACTION_COMMITTED":
            return original(*args, **kwargs)
        raise OSError("SYNTHETIC_STOP")
    monkeypatch.setattr(owner, name, fail)
    with pytest.raises(RuntimeError): _reconcile(x)
    monkeypatch.setattr(owner, name, original)
    assert _central(x).state().blocking_intent is not None
    if restart: _recompose(x, desktop_case, monkeypatch)
    if point == "before_partial_commit":
        _reconcile(x)
        _terminal(x, intent, 1, stages=stages)
    state = _reconcile(x)
    assert not state.blocking
    assert _risk(x).daily_turnover_rub == 1050 and _risk(x).daily_order_count == 1
    assert _risk(x).recorded_execution_ids == (intent.intent_id,)
    assert x.p.order_calls == 1 and _central(x).state().reserved_cash_kopecks == 0


def test_controller_partial_terminal_ticks_are_not_full_fill_or_dispatch(refresh_case, desktop_case, monkeypatch):
    x = refresh_case
    _multilot(x, desktop_case, monkeypatch)
    intent = _pending(x)
    raw = _terminal(x, intent, 1, "PARTIALLYFILL")
    stages = deepcopy(raw["stages"])
    counts = (x.p.candle_calls,x.p.order_calls)
    x.advance()
    tick = x.c.run_cycle()
    assert tick.actions[0].action == "ORDER_RECOVERY"
    assert tick.actions[0].status == "AWAITING_TERMINAL_PARTIAL"
    assert _risk(x).daily_order_count == 0
    _terminal(x, intent, 1, stages=stages)
    x.advance(); tick = x.c.run_cycle()
    assert tick.actions[0].action == "ORDER_RECOVERY"
    assert tick.actions[0].status == "RECONCILED_PARTIAL_FILL"
    assert (x.p.candle_calls,x.p.order_calls) == counts
    events = x.c.journal.recent(event_type="GUI_FILL_RECOVERY")
    encoded = json.dumps([e["payload"] for e in events])
    assert intent.intent_id not in encoded and intent.broker_order_id not in encoded and ACCOUNT not in encoded
    assert all(e["payload"]["provider_post_attempts"] == 0 for e in events)
    assert all(e["payload"]["strategy_evaluated"] is False for e in events)


def test_partial_buy_then_sell_remaining_returns_flat_without_replaying_cancelled_remainder(
        refresh_case, desktop_case, monkeypatch):
    x=refresh_case
    _multilot(x,desktop_case,monkeypatch)
    intent=_pending(x)
    _terminal(x,intent,1)
    _reconcile(x)
    x.p.target=0; x.p.clock_at+=timedelta(hours=1); x.advance()
    result=_desktop_cycle(x.c)
    sell=_central(x).state().blocking_intent
    assert result.status=="QUEUED" and sell.candidate.requested_lots==1
    assert sell.intent_id!=intent.intent_id
    _fill(x,sell); state=_reconcile(x)
    assert state.position(UID).actual_lots==state.position(UID).target_lots==0
    assert state.position(UID).ownership is None
    assert _risk(x).daily_turnover_rub==2100 and _risk(x).daily_order_count==2
    assert x.p.order_calls==2
    assert _desktop_cycle(x.c).status=="NO_POSITION_CHANGE"


def test_partial_checkpoint_write_failure_is_fail_closed(refresh_case, desktop_case, monkeypatch):
    x=refresh_case
    _multilot(x,desktop_case,monkeypatch)
    intent=_pending(x)
    _terminal(x,intent,1,"PARTIALLYFILL")
    import trading_robot.desktop_partial_progress as module
    def failed(*args,**kwargs): raise OSError("PRIVATE_DISK")
    monkeypatch.setattr(module,"atomic_write_json",failed)
    before=_snapshot(x)
    with pytest.raises(RuntimeError) as error: _reconcile(x)
    assert "PRIVATE" not in str(error.value)
    assert _snapshot(x)==before and _central(x).state().blocking_intent==intent


@pytest.mark.parametrize("point",["after_canonical","after_journal"])
@pytest.mark.parametrize("restart",[False,True])
def test_zero_terminal_resume_never_adds_execution_record(refresh_case,desktop_case,monkeypatch,point,restart):
    x=refresh_case
    intent=_pending(x)
    _terminal(x,intent,0)
    central=_central(x)
    owner,name=(central,"mark_reconciled") if point=="after_canonical" else (x.manager.transaction_coordinator,"_record")
    original=getattr(owner,name)
    def failing(*args,**kwargs):
        if point=="after_journal" and args[0]!="CANONICAL_TRANSACTION_COMMITTED": return original(*args,**kwargs)
        raise OSError("SYNTHETIC_STOP")
    monkeypatch.setattr(owner,name,failing)
    with pytest.raises(RuntimeError): _reconcile(x)
    monkeypatch.setattr(owner,name,original)
    if restart: _recompose(x,desktop_case,monkeypatch)
    state=_reconcile(x)
    assert not state.blocking and _central(x).state().blocking_intent is None
    assert _risk(x).daily_order_count==0 and _risk(x).recorded_execution_ids==()
    assert x.p.order_calls==1


@pytest.mark.parametrize("status",["CANCELLED","REJECTED"])
def test_zero_terminal_same_candle_does_not_retry_the_rejected_order(refresh_case,status):
    x=refresh_case
    intent=_pending(x)
    _terminal(x,intent,0,status)
    _reconcile(x)
    result=_desktop_cycle(x.c)
    assert result.status=="ALREADY_PROCESSED"
    assert x.p.order_calls==1
    assert len(_central(x).state().intents)==1


def test_partial_terminal_uses_vwap_of_executed_lots_only(refresh_case,desktop_case,monkeypatch):
    x=refresh_case
    _multilot(x,desktop_case,monkeypatch)
    intent=_pending(x)
    raw=_terminal(x,intent,2)
    raw["stages"]=[{"tradeId":"a","quantity":"1","price":money(101),"executionTime":x.p.clock_at.isoformat()},
                   {"tradeId":"b","quantity":"1","price":money(109),"executionTime":x.p.clock_at.isoformat()}]
    raw["executedOrderPrice"]=money(2100)  # never interpreted as one-security price
    _reconcile(x)
    assert _risk(x).daily_turnover_rub==2100 and _risk(x).daily_order_count==1
    assert _final(x,intent).executed_lots==2


def test_partial_growth_repeats_keep_reservation_until_terminal(refresh_case,desktop_case,monkeypatch):
    x=refresh_case
    _multilot(x,desktop_case,monkeypatch)
    intent=_pending(x)
    reserve=_central(x).state().reserved_cash_kopecks
    first=_terminal(x,intent,1,"PARTIALLYFILL")
    stages=deepcopy(first["stages"])
    _reconcile(x)
    x.advance()
    stages.append({"tradeId":"second","quantity":"1","price":money(105),"executionTime":x.p.clock_at.isoformat()})
    _terminal(x,intent,2,"PARTIALLYFILL",stages=list(reversed(stages)))
    for _ in range(2):
        state=_reconcile(x)
        assert state.position(UID).actual_lots==2 and state.position(UID).ownership is None
        assert _central(x).state().blocking_intent==intent
        assert _central(x).state().reserved_cash_kopecks==reserve
        assert _risk(x).daily_order_count==0
    _terminal(x,intent,2,stages=stages)
    _reconcile(x)
    assert _risk(x).daily_order_count==1 and _risk(x).daily_turnover_rub==2100
    assert _central(x).state().reserved_cash_kopecks==0 and x.p.order_calls==1


@pytest.mark.parametrize("missing",["omitted","empty"])
def test_incomplete_partial_receipt_does_not_destroy_existing_checkpoint(refresh_case,desktop_case,monkeypatch,missing):
    x=refresh_case
    _multilot(x,desktop_case,monkeypatch)
    intent=_pending(x)
    first=_terminal(x,intent,1,"PARTIALLYFILL"); stages=deepcopy(first["stages"])
    _reconcile(x)
    raw=_terminal(x,intent,1)
    if missing=="omitted": raw.pop("stages")
    else: raw["stages"]=[]
    before=_snapshot(x); evidence=_progress_file(x).read_bytes()
    _reconcile(x)
    assert _snapshot(x)==before and _progress_file(x).read_bytes()==evidence
    assert _central(x).state().blocking_intent==intent
    raw["stages"]=stages
    _reconcile(x)
    assert _final(x,intent).outcome=="PARTIALLY_FILLED"


@pytest.mark.parametrize("phase",["order_state","portfolio","cash"])
def test_concurrent_partial_evidence_change_is_detected_before_publication(refresh_case,desktop_case,monkeypatch,phase):
    x=refresh_case
    _multilot(x,desktop_case,monkeypatch)
    intent=_pending(x)
    raw=_terminal(x,intent,1,"PARTIALLYFILL"); stages=deepcopy(raw["stages"])
    _reconcile(x)
    path=_progress_file(x)
    _terminal(x,intent,1,stages=stages)
    def corrupt(): path.write_text("{}");
    if phase=="order_state": x.p.on_state=corrupt
    elif phase=="portfolio": x.p.on_portfolio=corrupt
    else: x.p.on_cash_positions=corrupt
    before=_snapshot(x)
    with pytest.raises(RuntimeError): _reconcile(x)
    assert _snapshot(x)==before and _central(x).state().blocking_intent==intent


@pytest.mark.parametrize("status",["CANCELLED","REJECTED"])
def test_omitted_zero_stage_array_is_not_mistaken_for_a_trade(refresh_case,status):
    x=refresh_case
    intent=_pending(x)
    raw=_terminal(x,intent,0,status); raw.pop("stages"); raw.pop("averagePositionPrice")
    _reconcile(x)
    assert _final(x,intent).risk_execution_status=="NOT_REQUIRED"
    assert _risk(x).daily_turnover_rub==0 and x.p.order_calls==1


def test_no_cancel_or_post_or_strategy_in_partial_recovery_ticks(refresh_case,desktop_case,monkeypatch):
    x=refresh_case
    _multilot(x,desktop_case,monkeypatch)
    intent=_pending(x)
    raw=_terminal(x,intent,1,"PARTIALLYFILL"); stages=deepcopy(raw["stages"])
    def forbidden(*args,**kwargs): raise AssertionError("Recovery must not submit or cancel or evaluate strategy")
    monkeypatch.setattr(x.p,"post_order",forbidden)
    monkeypatch.setattr(x.p,"cancel_order",forbidden,raising=False)
    monkeypatch.setattr(x.p,"get_candles",forbidden)
    monkeypatch.setattr(x.p,"get_last_prices",forbidden)
    monkeypatch.setattr(x.c.execution_adapter,"dispatch_next",forbidden)
    x.advance(); assert x.c.run_cycle().actions[0].status=="AWAITING_TERMINAL_PARTIAL"
    _terminal(x,intent,1,stages=stages)
    x.advance(); assert x.c.run_cycle().actions[0].status=="RECONCILED_PARTIAL_FILL"
    assert x.p.order_calls==1


def test_stale_active_list_cannot_override_terminal_receipt(refresh_case,desktop_case,monkeypatch):
    x=refresh_case
    _multilot(x,desktop_case,monkeypatch)
    intent=_pending(x)
    partial=_terminal(x,intent,1,"PARTIALLYFILL"); stages=deepcopy(partial["stages"])
    _reconcile(x)
    _terminal(x,intent,1,stages=stages)
    x.p.orders=[partial]
    before=_snapshot(x)
    with pytest.raises(RuntimeError): _reconcile(x)
    assert _snapshot(x)==before and _central(x).state().blocking_intent==intent
