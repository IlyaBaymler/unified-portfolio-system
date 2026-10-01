"""STEP35: real v4/registry + native dispatch; external provider is synthetic."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import importlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from test_q7a_source_versioned_dispatch import (
    base_desktop_case, desktop_case, exact_case, refresh_case, read_case, op_case,
    cut_case, selected_case, owner_case, admission_case, admit, refresh, _snapshot,
    money, _arm, _send, _wire, _v4,
)
from test_q7a_source_exact_dispatch import stamp
from test_q7a_source_settlement_closure import _recompose
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State
from trading_robot.versioned_operational_store import VersionedOperationalStore
from trading_robot.versioned_fee_evidence import _canonical, _parse, _sealed, _sha
from trading_robot.locking import InterProcessFileLock, LockUnavailableError


def module():
    return importlib.import_module('trading_robot.versioned_fill_cash')


def record(c, **kwargs):
    return module().record_selected_fill_cash(c.a, recovery=c.r, target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,
        expected_dispatch_plan_sha256=c.sent.plan_sha256, **kwargs)


def recover(c, **kwargs):
    return module().recover_selected_fill_cash(c.a, recovery=c.r, target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,
        expected_dispatch_plan_sha256=c.sent.plan_sha256, **kwargs)


def state(c):
    with VersionedOperationalStore.open(c.root/'ledger', **module().cut._common(c.a)) as s:
        return s.snapshot(), s.export_bytes()


def setup_fill(c, monkeypatch, *, direction='BUY', fee='0.123456789', ack='SUBMITTED'):
    if direction == 'SELL':
        from test_q7a_source_selected_sync import _run
        c.provider.target = 0
        c.provider.clock_at += timedelta(hours=1)
        c.provider.quote_at = c.provider.clock_at
        _run(c); c.provider.clock_at += timedelta(seconds=1); refresh(c)
    admit(c, 'uid-lkoh' if direction == 'BUY' else 'uid-sber')
    _wire(c, monkeypatch)
    arm = _arm(c)
    if ack == 'UNCERTAIN':
        posted = c.provider.post_order_once
        def lost(*args, **kwargs):
            posted(*args, **kwargs)
            raise TimeoutError('synthetic lost acknowledgement')
        monkeypatch.setattr(c.provider, 'post_order_once', lost)
        c.sent = _send(c, arm)
    elif ack == 'IN_FLIGHT':
        seen = []
        def cut(point):
            if point == 'dispatch.after_post':
                seen.append(point)
                raise RuntimeError('synthetic lost acknowledgement')
        with pytest.raises(RuntimeError, match='synthetic lost acknowledgement'):
            _send(c, arm, fault_injector=cut)
        assert seen == ['dispatch.after_post']
        c.sent = SimpleNamespace(plan_sha256=c.a.cash_authority_manager.status().pending_dispatch_proof_sha256)
    else:
        c.sent = _send(c, arm)
    assert c.a.manager.state().blocking_intent.status == ack
    c.money_before, _ = state(c)
    intent = c.a.manager.state().blocking_intent
    c.provider.clock_at += timedelta(seconds=1)
    at = stamp(c.provider.clock_at)
    quantity = intent.candidate.requested_lots
    units = quantity * intent.candidate.lot_size
    gross = Decimal('105.123456789') * units
    fee = Decimal(fee)
    c.fill = {**c.posts[0], 'lotsExecuted': str(quantity),
        'executionReportStatus': 'EXECUTION_REPORT_STATUS_FILL',
        'executedOrderPrice': money('9999.999999999'),
        'executedCommission': money(fee), 'serviceCommission': money(0),
        'averagePositionPrice': money('105.123456789'),
        'stages': [{'tradeId': 'NEW-V4-TRADE-1', 'executionTime': at,
                    'price': money('105.123456789'), 'quantity': str(quantity)}]}
    c.trade = {'id': 'NEW-V4-OPERATION', 'brokerAccountId': c.a.policy.account_id,
        'cursor': 'NEW-V4-CURSOR', 'date': at, 'type': 'OPERATION_TYPE_'+direction,
        'state': 'OPERATION_STATE_EXECUTED', 'quantity': str(units), 'quantityDone': str(units),
        'quantityRest': '0', 'payment': money(-gross if direction == 'BUY' else gross),
        'commission': money(fee), 'childOperations': [], 'instrumentUid': intent.candidate.instrument_id,
        'instrumentType': 'share', 'tradesInfo': {'trades': [{'num': 'NEW-V4-TRADE-1',
            'date': at, 'quantity': str(units), 'price': money('105.123456789')}]}}
    c.fee = {'id': 'NEW-V4-FEE', 'brokerAccountId': c.a.policy.account_id, 'cursor': 'NEW-V4-FEE-CURSOR',
        'date': at, 'type': 'OPERATION_TYPE_BROKER_FEE', 'state': 'OPERATION_STATE_EXECUTED',
        'quantity': '0', 'quantityDone': '0', 'quantityRest': '0', 'payment': money(-fee),
        'commission': money(0), 'childOperations': [], 'parentOperationId': c.trade['id'],
        'instrumentUid': intent.candidate.instrument_id}
    c.cash_delta = int(((-gross if direction == 'BUY' else gross)-fee)*10**9)
    c.fill_rows = deepcopy(c.rows) + [c.trade] + ([c.fee] if fee else [])
    c.reads = {'receipt': 0, 'operations': 0, 'cash': 0}
    def order_state(account, order_id, *, by_request_id=False):
        assert by_request_id and account == c.a.policy.account_id and order_id == intent.intent_id
        c.reads['receipt'] += 1
        return deepcopy(c.fill)
    def operations(payload, timeout):
        c.reads['operations'] += 1
        return {'items': deepcopy(c.fill_rows), 'hasNext': False, 'nextCursor': ''}
    previous = c.provider.get_positions
    def positions(account):
        c.reads['cash'] += 1
        raw = deepcopy(previous(account))
        raw['money'] = [money(Decimal(c.money_before.cash_nano + c.cash_delta)/10**9)]
        return raw
    monkeypatch.setattr(c.provider, 'get_order_state', order_state)
    monkeypatch.setattr(c.provider, 'get_operations_by_cursor_once', operations)
    monkeypatch.setattr(c.provider, 'get_positions', positions)
    c.provider.clock_at += timedelta(seconds=1)
    return c


@pytest.mark.parametrize('direction,fee', [('BUY','0.123456789'), ('SELL','0')])
def test_core_fill_cash_and_registry_once_pending_owners(admission_case, monkeypatch, request, direction, fee):
    c = setup_fill(admission_case, monkeypatch, direction=direction, fee=fee)
    before = _snapshot(c.x.c.y.x)
    result = record(c)
    snap, raw = state(c)
    expected_count = 2 if Decimal(fee) else 1
    assert result.outcome == 'COMMITTED' and result.appended_transactions == expected_count
    assert result.cash_delta_nano == c.cash_delta
    assert result.gross_nano == 1051234567890 and result.commission_nano == int(Decimal(fee)*10**9)
    assert snap.cash_nano == c.money_before.cash_nano + c.cash_delta
    assert snap.transaction_count == c.money_before.transaction_count + expected_count
    assert snap.pins.ledger_revision == c.money_before.pins.ledger_revision + expected_count
    assert _snapshot(c.x.c.y.x) == before
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING
    assert c.a.manager.state().blocking_intent.executed_lots == 0
    reads = dict(c.reads)
    again = record(c)
    assert again.replay and again.appended_transactions == 0 and state(c)[1] == raw
    assert c.reads == reads and _snapshot(c.x.c.y.x) == before
    assert c.provider.order_calls == 2 and len(c.posts) == 1
    assert result.public_summary()['cash_components_verified'] is True
    assert result.public_summary()['authority_clear_allowed'] is False
    with pytest.raises(Exception, match='EXACT_SETTLEMENT_REQUIRED'):
        c.a.manager.mark_reconciled(c.a.manager.state().blocking_intent.intent_id,
            portfolio_repository=c.r.manager.repository, outcome='FILLED', executed_lots=1, execution_price_rub=105.123456789)
    request.node.user_properties.extend([('direction',direction),('fee_nano',result.commission_nano),
        ('gross_nano',result.gross_nano),('cash_delta_nano',result.cash_delta_nano),
        ('cash_after_nano',snap.cash_nano),('new_transactions',expected_count),
        ('attempt_count',c.a.cash_authority_manager.status().post_attempt_count),
        ('new_posts_in_cash_step',0),('replay_additional_transactions',0)])


def test_receipt_and_components_bad_evidence_no_writes(admission_case, monkeypatch):
    c = setup_fill(admission_case, monkeypatch)
    before = _snapshot(c.x.c.y.x), state(c)[1]
    saved_fill, saved_rows = deepcopy(c.fill), deepcopy(c.fill_rows)
    for damage in ['request','exchange','stage_price','stage_quantity','missing_fee','fee_parent',
                   'fee_payment','trade_id','units','service_fee','unknown_fee','new_input','not_full']:
        c.fill, c.fill_rows = deepcopy(saved_fill), deepcopy(saved_rows)
        trade = c.fill_rows[-2]; fee = c.fill_rows[-1]
        if damage == 'request': c.fill['orderRequestId'] = 'other'
        elif damage == 'exchange': c.fill['orderId'] = 'other'
        elif damage == 'stage_price': c.fill['stages'][0]['price'] = money('106')
        elif damage == 'stage_quantity': c.fill['stages'][0]['quantity'] = '0'
        elif damage == 'missing_fee': c.fill_rows.pop()
        elif damage == 'fee_parent': fee['parentOperationId'] = 'other'
        elif damage == 'fee_payment': fee['payment'] = money('-0.123456788')
        elif damage == 'trade_id': trade['tradesInfo']['trades'][0]['num'] = 'other'
        elif damage == 'units': trade['quantityDone'] = '1'
        elif damage == 'service_fee': c.fill['serviceCommission'] = money('0.01')
        elif damage == 'unknown_fee': c.fill.pop('executedCommission')
        elif damage == 'new_input':
            extra=deepcopy(fee);extra.update(id='EXTRA',parentOperationId='',type='OPERATION_TYPE_INPUT',payment=money('1'))
            c.fill_rows.append(extra)
        else: c.fill.update(executionReportStatus='EXECUTION_REPORT_STATUS_NEW',lotsExecuted='0',stages=[])
        with pytest.raises(Exception) as caught: record(c)
        assert not isinstance(caught.value, (AttributeError,TypeError,KeyError)), (damage,caught.value)
        assert (_snapshot(c.x.c.y.x),state(c)[1]) == before, damage
        assert not list((c.root/'versioned_fill_cash').rglob('plan.json')), damage
    assert c.provider.order_calls == 2


@pytest.mark.parametrize('point', ['cash.after_plan','sync.after_prepare','source.append.after_commit',
                                  'resolution.after_record','cash.after_audit','cash.after_result'])
def test_cash_cuts_recover_no_repost_no_rebooking(admission_case, desktop_case, monkeypatch, point):
    c=setup_fill(admission_case,monkeypatch)
    before=_snapshot(c.x.c.y.x)
    seen=[]
    def fault(name):
        if name==point:
            seen.append(name)
            raise RuntimeError('synthetic cut')
    with pytest.raises(RuntimeError): record(c,fault_injector=fault)
    assert seen==[point]
    if point=='source.append.after_commit':
        _recompose(c.x.c.y.x,desktop_case)
        c.a=c.x.c.y.x.c.execution_adapter;c.r=c.x.c.y.x.c.cycle_source.portfolio_recovery
    calls=dict(c.reads)
    result=recover(c)
    assert c.reads==calls and _snapshot(c.x.c.y.x)==before and c.provider.order_calls==2
    assert result.appended_transactions==0
    if point in ('cash.after_plan','sync.after_prepare'):
        assert result.outcome=='ABORTED' and state(c)[0].cash_nano==c.money_before.cash_nano
        c.provider.clock_at += timedelta(seconds=1)
        result=record(c)
        assert result.outcome=='COMMITTED' and result.appended_transactions==2
    else: assert result.outcome=='COMMITTED'
    assert state(c)[0].cash_nano==c.money_before.cash_nano+c.cash_delta
    assert record(c).replay and c.provider.order_calls==2


def test_final_locks_tampered_export_and_conflicting_source(admission_case,monkeypatch):
    c=setup_fill(admission_case,monkeypatch)
    tested=[]
    def fault(point):
        if point=='source.append.after_insert':
            for root in (c.root/'ledger',c.a.cl7_ledger_store.root):
                connection=sqlite3.connect(root/'store.sqlite3',timeout=0)
                try:
                    with pytest.raises(sqlite3.OperationalError): connection.execute('BEGIN IMMEDIATE')
                finally:connection.close()
            for lock in (c.r.manager.repository.lock_path,c.r.risk.state_store.lock_path,c.a.manager.store.lock_path):
                with pytest.raises(LockUnavailableError):
                    with InterProcessFileLock(lock,timeout_seconds=0): pass
            tested.append(point)
    result=record(c,fault_injector=fault)
    assert len(tested)==1
    snap,raw=state(c)
    # A correctly re-signed economically false v4 batch must fail a full read.
    value=json.loads(raw);row=value['batches'][-1];body=json.loads(row['canonical_json_ascii'])['payload']
    capture=json.loads(body['capture_json_ascii'])
    capture['bound_full_fill']['order_state']['executedCommission']=money('0.2')
    body['capture_json_ascii']=_canonical(capture).decode();body['capture_sha256']=_sha(_canonical(capture))
    new=_sealed(body,c.a.cl7_identity_key);row.update(canonical_json_ascii=new.decode(),sha256=_sha(new))
    with pytest.raises(Exception):module().sync._graph(c.a,_canonical(value))
    # No independent writer can add an unexplained suffix and have it adopted.
    with VersionedOperationalStore.open(c.root/'ledger',**module().cut._common(c.a)) as s:
        from test_q7a_source_versioned_operational_store import _request
        rows=deepcopy(c.fill_rows);extra=deepcopy(c.fee)
        extra.update(id='UNEXPECTED-OP',cursor='UNEXPECTED-CURSOR',parentOperationId='',type='OPERATION_TYPE_INPUT',payment=money('1'),commission=money(0))
        rows.append(extra)
        original=c.provider.get_positions
        positions=original(c.a.policy.account_id)
        positions['money']=[money(Decimal(snap.cash_nano+10**9)/10**9)]
        end=stamp(c.provider.clock_at+timedelta(seconds=1))
        s.sync_tbank_operations(_request(c.x,rows,end=end),expected_pins=s.snapshot().pins,
            recorded_at=end,read_rub_positions=lambda _:positions)
    with pytest.raises(Exception):recover(c)
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING
    assert c.provider.order_calls==2


@pytest.mark.parametrize('ack',['IN_FLIGHT','UNCERTAIN'])
def test_lost_ack_fill_is_request_bound_without_central_rewrite(admission_case,monkeypatch,ack):
    c=setup_fill(admission_case,monkeypatch,ack=ack)
    before=_snapshot(c.x.c.y.x)
    result=record(c)
    assert result.outcome=='COMMITTED' and result.appended_transactions==2
    assert _snapshot(c.x.c.y.x)==before and c.a.manager.state().blocking_intent.status==ack
    assert c.a.manager.state().blocking_intent.broker_order_id is None
    assert c.provider.order_calls==2 and record(c).replay


def test_expiry_missing_lookup_and_nanodrift_cannot_prepare(admission_case,monkeypatch):
    c=setup_fill(admission_case,monkeypatch)
    before=_snapshot(c.x.c.y.x),state(c)[1]
    original=c.provider.get_order_state
    def unavailable(*a,**k):raise TimeoutError('not evidence of absence')
    monkeypatch.setattr(c.provider,'get_order_state',unavailable)
    with pytest.raises(RuntimeError,match='LOOKUP_UNAVAILABLE_NO_RETRY'):record(c)
    monkeypatch.setattr(c.provider,'get_order_state',original)
    c.cash_delta += 1
    with pytest.raises(RuntimeError,match='CASH_MISMATCH'):record(c)
    c.cash_delta -= 1
    positions=c.provider.get_positions
    def slow(account):
        value=positions(account)
        c.provider.clock_at += timedelta(seconds=6)
        return value
    monkeypatch.setattr(c.provider,'get_positions',slow)
    with pytest.raises(RuntimeError,match='EXPIRED'):record(c)
    assert (_snapshot(c.x.c.y.x),state(c)[1])==before
    assert not list((c.root/'versioned_fill_cash').rglob('plan.json'))
    assert c.provider.order_calls==2
