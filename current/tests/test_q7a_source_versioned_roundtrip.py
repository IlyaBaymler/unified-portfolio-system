"""STEP37: continuous selected-v4 lifecycle; only the broker is simulated.

An unrelated seed SBER holding remains present. LKOH is bought from flat and
sold back to flat. All persistent owners are written only by production APIs.
The native admission API reports HOLD as NO_POSITION_CHANGE, not a GUI tick.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json
import time

import pytest

from test_q7a_source_versioned_fill_closure import (
    base_desktop_case, desktop_case, exact_case, refresh_case, read_case, op_case,
    cut_case, selected_case, owner_case, admission_case,
    setup_closure, close, recover, record, refresh, admit, state,
)
from test_q7a_source_selected_sync import _run
from test_q7a_source_settlement_closure import _snapshot, _recompose
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State
from trading_robot.versioned_risk_admission import VersionedAdmissionError


def _reopen(c, desktop_case):
    _recompose(c.x.c.y.x, desktop_case)
    c.a = c.x.c.y.x.c.execution_adapter
    c.r = c.x.c.y.x.c.cycle_source.portfolio_recovery


def _advance_and_refresh(c, seconds):
    c.provider.clock_at += timedelta(seconds=seconds)
    c.provider.quote_at = c.provider.clock_at
    result = _run(c)
    c.provider.clock_at += timedelta(seconds=1)
    owner_result = refresh(c)
    assert not owner_result.risk_resync_required
    return result, owner_result


def _hold(c):
    before = _snapshot(c.x.c.y.x)
    raw = state(c)[1]
    posts = c.provider.order_calls
    with pytest.raises(VersionedAdmissionError, match="^V4_ADMISSION_NO_POSITION_CHANGE$"):
        admit(c, 'uid-lkoh')
    assert _snapshot(c.x.c.y.x) == before and state(c)[1] == raw
    assert c.provider.order_calls == posts
    assert not c.a.manager.state().queued and c.a.manager.state().blocking_intent is None


def _offline_replay(c, monkeypatch):
    before = _snapshot(c.x.c.y.x)
    raw = state(c)[1]
    def forbidden(*args, **kwargs):
        pytest.fail('broker read during completed closure replay')
    with monkeypatch.context() as mp:
        for method in ('get_portfolio', 'get_orders', 'get_order_state', 'get_positions',
                       'get_max_lots', 'get_operations_by_cursor_once', 'post_order_once'):
            mp.setattr(c.provider, method, forbidden)
        result = recover(c)
    assert result.replay and _snapshot(c.x.c.y.x) == before and state(c)[1] == raw

def sell_and_close(c, monkeypatch, *, lose_ack=False, closure_fault=None):
    """Only replace broker observations. Never assign any canonical owner."""
    from copy import deepcopy
    from decimal import Decimal
    from types import SimpleNamespace
    from test_q7a_source_versioned_dispatch import _arm, _send
    from test_q7a_source_versioned_fill_cash import money
    from test_q7a_source_exact_dispatch import stamp
    from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State
    old_cash = state(c)[0].cash_nano
    old_rows = deepcopy(c.fill_rows)
    posts = []
    def post(account, uid, lots, direction, *, order_id, order_type, time_in_force):
        a = c.a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        assert a.state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING and a.post_attempt_count == 3
        i = c.a.manager.store._load_unlocked(expected_account_id=account).blocking_intent
        assert i.intent_id == order_id and i.status == 'IN_FLIGHT'
        assert uid == 'uid-lkoh' and direction == 'SELL' and lots == 1
        c.provider.order_calls += 1
        reply = dict(accountId=account, instrumentUid=uid, lotsRequested=str(lots), lotsExecuted='0',
            direction='ORDER_DIRECTION_'+direction, orderType='ORDER_TYPE_'+order_type,
            orderId='ROUNDTRIP-SELL-EXCHANGE', orderRequestId=order_id,
            executionReportStatus='EXECUTION_REPORT_STATUS_NEW', currency='rub')
        posts.append(deepcopy(reply))
        if lose_ack: raise TimeoutError('roundtrip acknowledgement lost')
        return reply
    monkeypatch.setattr(c.provider, 'post_order_once', post)
    arm = _arm(c)
    c.sell_arm = arm
    c.sent = _send(c, arm)
    assert len(posts) == 1
    c.intent = c.a.manager.state().blocking_intent
    c.provider.clock_at += timedelta(seconds=1)
    at = stamp(c.provider.clock_at)
    gross = Decimal('105.123456789') * 10
    fee = Decimal('0.123456789')
    c.fill = dict(posts[0], lotsExecuted='1', executionReportStatus='EXECUTION_REPORT_STATUS_FILL',
        executedOrderPrice=money('9999.999999999'), executedCommission=money(fee),
        serviceCommission=money(0), averagePositionPrice=money('105.123456789'),
        stages=[{'tradeId':'ROUNDTRIP-SELL-TRADE','executionTime':at,'price':money('105.123456789'),'quantity':'1'}])
    trade = dict(id='ROUNDTRIP-SELL-OP', brokerAccountId=c.a.policy.account_id, cursor='ROUNDTRIP-SELL-CURSOR',
        date=at, type='OPERATION_TYPE_SELL', state='OPERATION_STATE_EXECUTED', quantity='10',quantityDone='10',quantityRest='0',
        payment=money(gross),commission=money(fee),childOperations=[],instrumentUid='uid-lkoh',instrumentType='share',
        tradesInfo={'trades':[{'num':'ROUNDTRIP-SELL-TRADE','date':at,'quantity':'10','price':money('105.123456789')}]})
    commission=dict(id='ROUNDTRIP-SELL-FEE',brokerAccountId=c.a.policy.account_id,cursor='ROUNDTRIP-FEE-CURSOR',date=at,
        type='OPERATION_TYPE_BROKER_FEE',state='OPERATION_STATE_EXECUTED',quantity='0',quantityDone='0',quantityRest='0',
        payment=money(-fee),commission=money(0),childOperations=[],parentOperationId=trade['id'],instrumentUid='uid-lkoh')
    c.fill_rows = old_rows+[trade,commission]
    c.cash = old_cash+int((gross-fee)*10**9)
    def order(account,request_id,*,by_request_id=False):
        assert account == c.a.policy.account_id and request_id == c.intent.intent_id and by_request_id
        return deepcopy(c.fill)
    def operations(payload,timeout):return {'items':deepcopy(c.fill_rows),'hasNext':False,'nextCursor':''}
    def positions(account):return {'accountId':account,'money':[money(Decimal(c.cash)/10**9)],'blocked':[],
        'limitsLoadingInProgress':False,'securities':[],'futures':[],'options':[]}
    monkeypatch.setattr(c.provider,'get_order_state',order)
    monkeypatch.setattr(c.provider,'get_operations_by_cursor_once',operations)
    monkeypatch.setattr(c.provider,'get_positions',positions)
    c.portfolio_after['positions']=[p for p in c.portfolio_after['positions'] if p['instrumentUid'] != 'uid-lkoh']
    shares=sum((Decimal(p['quantity']['units'])+Decimal(p['quantity']['nano'])/10**9)*
        (Decimal(p['currentPrice']['units'])+Decimal(p['currentPrice']['nano'])/10**9) for p in c.portfolio_after['positions'])
    c.portfolio_after['totalAmountCurrencies']=money(Decimal(c.cash)/10**9)
    c.portfolio_after['totalAmountShares']=money(shares)
    c.portfolio_after['totalAmountPortfolio']=money(Decimal(c.cash)/10**9+shares)
    c.provider.clock_at += timedelta(seconds=1)
    c.cash_result=record(c)
    c.provider.clock_at += timedelta(seconds=1)
    out=close(c, **({'fault_injector':closure_fault} if closure_fault else {}))
    return out


@pytest.mark.parametrize('mode', ['normal', 'restart_lost_ack', 'restart_sell_risk'])
def test_continuous_buy_fill_hold_sell_fill_hold(admission_case, desktop_case, monkeypatch, request, mode):
    started = time.perf_counter()
    c = setup_closure(admission_case, monkeypatch)
    cash_before = c.money_before.cash_nano
    unrelated = c.r.manager.repository.load().position('uid-sber')
    fixed = (unrelated.actual_lots, unrelated.target_lots, unrelated.ownership, unrelated.origin)
    original_v1 = c.a.cl7_ledger_store.export_bytes()
    first_intent = c.intent.intent_id
    close(c)
    assert c.r.manager.repository.load().position('uid-lkoh').actual_lots == 1
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED
    _offline_replay(c, monkeypatch)
    if mode == 'restart_lost_ack':
        _reopen(c, desktop_case)
    _advance_and_refresh(c, 1)
    _hold(c)
    assert c.provider.order_calls == 2
    # A later closed candle, not a rewritten target or an altered admitted intent.
    c.provider.target = 0
    _advance_and_refresh(c, 3600)
    admitted = admit(c, 'uid-lkoh')
    assert admitted.intent_id != first_intent
    intent = c.a.manager.state().queued[0]
    assert intent.candidate.direction == 'SELL' and intent.reserved_cash_kopecks == 0
    assert c.provider.order_calls == 2
    if mode == 'restart_sell_risk':
        seen = []
        def fault(point):
            if point == 'closure.after_risk':
                seen.append(point)
                raise RuntimeError('synthetic second-leg risk cut')
        with pytest.raises(RuntimeError, match='synthetic second-leg risk cut'):
            sell_and_close(c, monkeypatch, closure_fault=fault)
        assert seen == ['closure.after_risk']
        cash_once = state(c)[1]
        assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING
        _reopen(c, desktop_case)
        def forbidden(*args, **kwargs):pytest.fail('broker call during interrupted closure recovery')
        with monkeypatch.context() as mp:
            for method in ('get_portfolio','get_orders','get_order_state','get_positions',
                           'get_max_lots','get_operations_by_cursor_once','post_order_once'):
                mp.setattr(c.provider, method, forbidden)
            recover(c)
        assert state(c)[1] == cash_once
    else:
        sell_and_close(c, monkeypatch, lose_ack=(mode == 'restart_lost_ack'))
    _offline_replay(c, monkeypatch)
    money = state(c)[0]
    assert money.cash_nano == cash_before - 2 * 123456789 == 999048318518532
    assert money.transaction_count == c.money_before.transaction_count + 4
    assert money.pins.ledger_revision == c.money_before.pins.ledger_revision + 4
    assert c.a.cl7_ledger_store.export_bytes() == original_v1
    central = c.a.manager.state()
    ours = [i for i in central.intents if i.intent_id in (first_intent, admitted.intent_id)]
    assert len(ours) == 2 and {i.candidate.direction for i in ours} == {'BUY','SELL'}
    assert all(i.status == 'RECONCILED' and i.outcome == 'FILLED' and i.executed_lots == 1 for i in ours)
    assert len({i.broker_order_id for i in ours}) == 2
    assert not central.queued and central.blocking_intent is None
    assert c.provider.order_calls == 3
    risk = c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert len(risk.recorded_execution_ids) == risk.daily_order_count == 3
    assert first_intent in risk.recorded_execution_ids and admitted.intent_id in risk.recorded_execution_ids
    authority = c.a.cash_authority_manager.status()
    assert authority.state is State.EXACT_CASH_VERSIONED_DISARMED and authority.post_attempt_count == 3
    assert authority.pending_dispatch_proof_sha256 is None
    assert authority.ledger_head_sha256 == money.pins.ledger_head_sha256
    position = c.r.manager.repository.load().position('uid-lkoh')
    assert position is None or (position.actual_lots == 0 and position.ownership is None)
    unrelated = c.r.manager.repository.load().position('uid-sber')
    assert (unrelated.actual_lots,unrelated.target_lots,unrelated.ownership,unrelated.origin) == fixed
    from test_q7a_source_versioned_dispatch import _send
    from trading_robot.versioned_dispatch import VersionedDispatchError
    with pytest.raises(VersionedDispatchError, match='NOT_ARMED_OR_ALREADY_ATTEMPTED'):
        _send(c, c.sell_arm)
    assert c.provider.order_calls == 3
    # The next maintenance tick and flat HOLD must work on this same history.
    _advance_and_refresh(c, 1)
    _hold(c)
    assert state(c)[0].cash_nano == money.cash_nano
    assert c.r.risk.state_store.load_account(c.a.policy.account_id).recorded_execution_ids == risk.recorded_execution_ids
    request.node.user_properties.extend([
        ('mode',mode),('cash_before_nano',cash_before),('cash_final_nano',money.cash_nano),
        ('roundtrip_fees_nano',246913578),('new_posts',2),('post_total_including_seed',3),
        ('new_risk_executions',2),('transaction_delta',4),('final_instrument_lots',0),
        ('hold_long_no_post',True),('hold_flat_no_post',True),('unrelated_position_preserved',True),
        ('test_wall_seconds_including_fixture_calls',time.perf_counter()-started)])
