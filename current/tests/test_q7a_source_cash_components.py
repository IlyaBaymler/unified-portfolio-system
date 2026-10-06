"""STEP14 full-fill cash-component journal subgate; all provider IO is synthetic."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case, _desktop_cycle
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money, order
from test_q7a_source_exact_receipt import _pending, _raw, _snapshot
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot.cash_ledger_domain import LedgerAccount, LedgerTransaction
from trading_robot.cash_ledger_persistence import CashLedgerStore, InjectedFault
from trading_robot.runtime_cash_authority import CL7RuntimeError, RuntimeCashAuthorityState


def _record(x):
    method = getattr(x.c.execution_adapter, "record_exact_cash_components", None)
    assert callable(method), 'STEP14 cash-component bridge is missing'
    return method()


def _input(x, *, fee='0.123456789'):
    intent = _pending(x)
    raw = _raw(x, intent, commission=fee)
    qty = intent.candidate.requested_lots * intent.candidate.lot_size
    gross = Decimal('105.123456789') * qty
    trade = {
        'id': 'PRIVATE_OPERATION_1', 'brokerAccountId': ACCOUNT, 'cursor': 'PRIVATE_CURSOR_1',
        'date': stamp(x.p.clock_at), 'type': 'OPERATION_TYPE_' + intent.candidate.direction,
        'state': 'OPERATION_STATE_EXECUTED', 'quantity': str(qty), 'quantityDone': str(qty),
        'quantityRest': '0', 'payment': money(-gross if intent.candidate.direction == 'BUY' else gross),
        'commission': money(fee), 'childOperations': [], 'instrumentUid': UID, 'instrumentType': 'share',
        'tradesInfo': {'trades': [{'num': raw['stages'][0]['tradeId'], 'quantity': str(qty),
            'price': money('105.123456789'), 'date': stamp(x.p.clock_at)}]},
    }
    x.operations[:] = [trade]
    if Decimal(fee):
        x.operations.append({
            'id': 'PRIVATE_FEE_1', 'brokerAccountId': ACCOUNT, 'cursor': 'PRIVATE_CURSOR_2',
            'date': stamp(x.p.clock_at), 'type': 'OPERATION_TYPE_BROKER_FEE',
            'state': 'OPERATION_STATE_EXECUTED', 'quantity': '0', 'quantityDone': '0', 'quantityRest': '0',
            'payment': money(-Decimal(fee)), 'commission': money(0), 'childOperations': [],
            'parentOperationId': trade['id'], 'instrumentUid': UID,
        })
    delta = (-gross if intent.candidate.direction == 'BUY' else gross) - Decimal(fee)
    x.p.wallet_rub = Decimal('1000000') + delta
    x.advance()
    return intent, raw, trade


def _export(x):
    return x.c.execution_adapter.cl7_ledger_store.export_bytes()


def _owners(x):
    return _snapshot(x)[:4] + (_snapshot(x)[5],)


def _cash(export):
    return sum(p.money.minor_units for row in json.loads(export)['transactions']
               for p in LedgerTransaction.from_canonical_dict(json.loads(row['canonical_json_ascii'])).postings
               if p.account is LedgerAccount.ASSET_BROKER_CASH)


def _plan_path(x):
    files = list((x.c.central_order_coordinator.manager.store.path.parent / 'exact_cash_components').glob('*.json'))
    assert len(files) == 1
    return files[0]


def test_full_buy_records_gross_and_fee_once_and_does_not_close(exact_case, request):
    x = exact_case
    intent, raw, trade = _input(x)
    baseline, owners = _export(x), _owners(x)
    result = _record(x)
    after = _export(x)
    assert result.gross_nano == 1_051_234_567_890
    assert result.commission_nano == 123_456_789
    assert result.cash_delta_nano == -1_051_358_024_679
    assert _cash(after) == 1_000_000_000_000_000 - 1_051_358_024_679
    assert result.appended_transactions == 2 and result.replay is False
    assert len(json.loads(after)['transactions']) == len(json.loads(baseline)['transactions']) + 2
    assert _owners(x) == owners
    assert x.c.execution_adapter.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    assert x.c.central_order_coordinator.manager.state().blocking_intent == intent
    assert result.to_canonical_dict()['cash_components_verified'] is True
    assert result.to_canonical_dict()['settlement_verified'] is False
    assert result.to_canonical_dict()['authority_clear_allowed'] is False
    assert x.p.order_calls == 1
    plan = _plan_path(x).read_bytes()
    x.advance()
    repeated = _record(x)
    assert repeated.replay and repeated.appended_transactions == 0
    assert repeated.transaction_sha256s == result.transaction_sha256s
    assert repeated.ledger_head_sha256 == result.ledger_head_sha256
    assert repeated.plan_sha256 == result.plan_sha256
    assert _export(x) == after and _plan_path(x).read_bytes() == plan and _owners(x) == owners
    request.node.user_properties.extend([('gross_nano', str(result.gross_nano)),
        ('commission_nano', str(result.commission_nano)), ('cash_delta_nano', str(result.cash_delta_nano)),
        ('fake_posts', 1), ('appended_transactions', 2), ('replay_appended', 0)])


def test_zero_fee_creates_no_zero_or_duplicate_fee_posting(exact_case):
    x = exact_case
    _input(x, fee='0')
    baseline = _export(x)
    result = _record(x)
    assert result.appended_transactions == 1 and result.commission_nano == 0
    assert len(json.loads(_export(x))['transactions']) == len(json.loads(baseline)['transactions']) + 1
    assert _record(x).replay


def test_old_generic_cl3_still_refuses_fee_components(exact_case):
    x = exact_case
    _input(x)
    from trading_robot import broker_read_adapters as cl3
    a = x.c.execution_adapter
    r = cl3.BrokerReadRequest(environment=cl3.BrokerEnvironment.SANDBOX, raw_account_id=ACCOUNT,
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        from_inclusive=a.cash_authority_manager.status().operations_complete_through,
        to_exclusive=stamp(x.p.clock_at), limit=100, max_pages=2, max_items=4, absolute_deadline_ns=10**9,
        retry_policy=cl3.RetryPolicy(1,10**9,()), transport=x.p.get_operations_by_cursor_once,
        monotonic_ns=lambda:1,wait_ns=lambda _:None)
    batch=cl3.collect_tbank_operations(r)
    assert [d.reason.value for d in batch.decisions] == ['MULTI_COMPONENT_AMBIGUOUS'] * 2
    assert all(d.transaction_proposal is None for d in batch.decisions)


@pytest.mark.parametrize('damage', [
    'no_operations','missing_fee','extra_fee','unrelated_cash','parent','fee_currency','fee_amount',
    'fee_positive','fee_summary','fee_children','fee_quantity','fee_trades','fee_instrument','fee_state',
    'trade_id','trade_units','trade_price','trade_time','duplicate_trade','missing_trades','quantity_lots',
    'quantity_rest','operation_state','direction','instrument','asset_type','payment_net','payment_sign',
    'payment_amount','summary_amount','trade_children','trade_parent','account','duplicate_operation',
    'operation_future','operation_early','operation_float','page_missing','pagination_broken',
    'receipt_missing_fee','receipt_missing_service','receipt_service','cash_mismatch','cash_blocked',
])
def test_bad_evidence_never_books_any_component(exact_case, damage):
    x = exact_case
    intent, raw, trade = _input(x)
    fee = x.operations[1]
    if damage == 'no_operations': x.operations.clear()
    elif damage == 'missing_fee': x.operations.pop()
    elif damage == 'extra_fee':
        extra=deepcopy(fee);extra.update(id='PRIVATE_FEE_2',cursor='PRIVATE_CURSOR_3');x.operations.append(extra)
    elif damage == 'unrelated_cash': fee.update(type='OPERATION_TYPE_INPUT',payment=money(1))
    elif damage == 'parent': fee['parentOperationId']='PRIVATE_OTHER'
    elif damage == 'fee_currency': fee['payment']['currency']='usd'
    elif damage == 'fee_amount': fee['payment']=money('-0.2')
    elif damage == 'fee_positive': fee['payment']=money('0.123456789')
    elif damage == 'fee_summary': fee['commission']=money('0.1')
    elif damage == 'fee_children': fee['childOperations']=[{'payment':money(1),'instrumentUid':UID}]
    elif damage == 'fee_quantity': fee['quantity']='1'
    elif damage == 'fee_trades': fee['tradesInfo']={'trades':[deepcopy(trade['tradesInfo']['trades'][0])]}
    elif damage == 'fee_instrument': fee['instrumentUid']='PRIVATE_OTHER'
    elif damage == 'fee_state': fee['state']='OPERATION_STATE_PROGRESS'
    elif damage == 'trade_id': trade['tradesInfo']['trades'][0]['num']='PRIVATE_OTHER'
    elif damage == 'trade_units': trade['tradesInfo']['trades'][0]['quantity']='1'
    elif damage == 'trade_price': trade['tradesInfo']['trades'][0]['price']=money(105)
    elif damage == 'trade_time': trade['tradesInfo']['trades'][0]['date']=stamp(x.p.clock_at)
    elif damage == 'duplicate_trade': trade['tradesInfo']['trades'] *= 2
    elif damage == 'missing_trades': trade.pop('tradesInfo')
    elif damage == 'quantity_lots': trade['quantity']=trade['quantityDone']='1'
    elif damage == 'quantity_rest': trade['quantityRest']='1'
    elif damage == 'operation_state': trade['state']='OPERATION_STATE_CANCELED'
    elif damage == 'direction': trade['type']='OPERATION_TYPE_SELL'
    elif damage == 'instrument': trade['instrumentUid']='PRIVATE_OTHER'
    elif damage == 'asset_type': trade['instrumentType']='bond'
    elif damage == 'payment_net': trade['payment']=money('-1051.358024679')
    elif damage == 'payment_sign': trade['payment']=money('1051.23456789')
    elif damage == 'payment_amount': trade['payment']=money('-1051')
    elif damage == 'summary_amount': trade['commission']=money(0)
    elif damage == 'trade_children': trade['childOperations']=[{'payment':money(-1),'instrumentUid':UID}]
    elif damage == 'trade_parent': trade['parentOperationId']='PRIVATE_OTHER'
    elif damage == 'account': trade['brokerAccountId']='PRIVATE_OTHER'
    elif damage == 'duplicate_operation': x.operations.append(deepcopy(trade))
    elif damage == 'operation_future': trade['date']='2028-01-01T00:00:00.000000000Z'
    elif damage == 'operation_early': trade['date']='2020-01-01T00:00:00.000000000Z'
    elif damage == 'operation_float': trade['quantity']=10.0
    elif damage == 'page_missing': x.operations_damage='invalid'
    elif damage == 'pagination_broken':
        x.p.get_operations_by_cursor_once=lambda *_:{'hasNext':True,'items':x.operations,'nextCursor':''}
    elif damage == 'receipt_missing_fee': raw.pop('executedCommission')
    elif damage == 'receipt_missing_service': raw.pop('serviceCommission')
    elif damage == 'receipt_service': raw['serviceCommission']=money('0.1')
    elif damage == 'cash_mismatch': x.p.wallet_rub -= 1
    elif damage == 'cash_blocked': x.p.cash_blocked_rub=1
    before, owners=_export(x),_owners(x)
    from trading_robot.exact_cash_settlement import ExactCashSettlementError
    with pytest.raises(ExactCashSettlementError): _record(x)
    assert _export(x)==before and _owners(x)==owners
    assert not list((x.c.central_order_coordinator.manager.store.path.parent/'exact_cash_components').glob('*.json'))


@pytest.mark.parametrize('point', ['append_observation.after_observation', 'append_transaction.after_transaction',
                                  'append_transaction.after_provenance', 'append_transaction.after_meta'])
def test_cl2_internal_fault_rolls_back_then_replays(exact_case, point):
    x=exact_case;_input(x);owners=_owners(x)
    a=x.c.execution_adapter; fired=[]
    def fault(name):
        if name == point and not fired:
            fired.append(name);raise InjectedFault(name)
    a.cl7_ledger_store._fault_injector=fault
    from trading_robot.exact_cash_settlement import ExactCashSettlementError
    with pytest.raises(ExactCashSettlementError): _record(x)
    assert fired and _owners(x)==owners
    a.cl7_ledger_store._fault_injector=None
    result=_record(x)
    assert result.to_canonical_dict()['cash_components_verified']
    assert len(json.loads(_export(x))['transactions'])==3
    assert _owners(x)==owners


@pytest.mark.parametrize('cut', ['plan','trade_observation','trade_transaction','fee_observation','fee_transaction'])
@pytest.mark.parametrize('reopen', [False,True])
def test_committed_prefix_resumes_without_duplicate_or_clear(exact_case,monkeypatch,cut,reopen):
    x=exact_case;_input(x);a=x.c.execution_adapter;owners=_owners(x)
    from trading_robot import exact_cash_settlement as mod
    store=a.cl7_ledger_store;fired=[]
    if cut=='plan':
        original=mod._PlanStore.create
        def create(self,payload):
            original(self,payload)
            if not fired:fired.append(1);raise RuntimeError('PRIVATE_INJECTED')
        monkeypatch.setattr(mod._PlanStore,'create',create)
    else:
        name='append_observation' if 'observation' in cut else 'append_transaction'
        original=getattr(store,name); count=[0]
        target=1 if cut.startswith('trade') else 2
        def append(*args,**kwargs):
            result=original(*args,**kwargs);count[0]+=1
            if count[0]==target and not fired:fired.append(1);raise RuntimeError('PRIVATE_INJECTED')
            return result
        monkeypatch.setattr(store,name,append)
    with pytest.raises(mod.ExactCashSettlementError): _record(x)
    assert fired and _owners(x)==owners
    plan=_plan_path(x).read_bytes()
    if cut=='plan':monkeypatch.setattr(mod._PlanStore,'create',original)
    else:monkeypatch.setattr(store,name,original)
    if reopen:
        root=store.root;registry=tuple(store._registry.values());store.close()
        a.cl7_ledger_store=CashLedgerStore.open(root,registry)
    try:
        result=_record(x);after=_export(x)
        assert len(json.loads(after)['transactions'])==3 and _owners(x)==owners
        assert _plan_path(x).read_bytes()==plan
        assert _record(x).replay and _export(x)==after
    finally:
        if reopen:a.cl7_ledger_store.close()


@pytest.mark.parametrize('damage',['operation_alias','changed_fee_alias','receipt_price','plan_tamper','plan_delete'])
def test_replay_rejects_changed_evidence_and_never_books_again(exact_case,damage):
    x=exact_case;_,raw,trade=_input(x);_record(x)
    plan=_plan_path(x)
    if damage=='operation_alias':trade['id']='PRIVATE_ALIAS';x.operations[1]['parentOperationId']=trade['id']
    elif damage=='changed_fee_alias':x.operations[1]['id']='PRIVATE_FEE_ALIAS'
    elif damage=='receipt_price':raw['stages'][0]['price']=money(104)
    elif damage=='plan_tamper':
        data=json.loads(plan.read_bytes());data['payload']['match']['gross_nano']='1';plan.write_text(json.dumps(data))
    elif damage=='plan_delete':plan.unlink()
    before,owners=_export(x),_owners(x)
    from trading_robot.exact_cash_settlement import ExactCashSettlementError
    with pytest.raises(ExactCashSettlementError):_record(x)
    assert _export(x)==before and _owners(x)==owners


def test_valid_components_are_not_a_clear_or_generic_reconciliation_token(exact_case):
    x=exact_case;intent,_,_=_input(x);_record(x)
    a=x.c.execution_adapter;before,owners=_export(x),_owners(x)
    with pytest.raises(Exception,match='EXACT_SETTLEMENT_REQUIRED'):
        a.manager.mark_reconciled(intent.intent_id,portfolio_repository=x.c.portfolio_repository,
            outcome='FILLED',executed_lots=1,risk_runtime=x.c.central_order_coordinator.risk_runtime,
            execution_price_rub=105.123456789,execution_price_source='GET_ORDER_STATE_STAGES')
    with pytest.raises(CL7RuntimeError):
        a.cash_authority_manager.recover_runtime(central_manager=a.manager,raw_account_id=ACCOUNT,
            identity_key=a.cl7_identity_key,identity_key_id=a.cl7_identity_key_id,transition_at=stamp(x.p.clock_at))
    result=a.dispatch_next(x.c.portfolio_repository)
    assert result.status=='CL7_DISPATCH_PENDING'
    assert _export(x)==before and _owners(x)==owners


def test_plan_and_result_do_not_publish_raw_provider_identifiers(exact_case):
    x=exact_case;intent,raw,trade=_input(x);result=_record(x)
    raw_out=_plan_path(x).read_text()+json.dumps(result.to_canonical_dict())
    for private in [intent.intent_id,intent.broker_order_id,raw['stages'][0]['tradeId'],
                    trade['id'],trade['cursor'],ACCOUNT,UID,'PRIVATE_FEE_1']:
        assert private not in raw_out


def test_independent_preowned_sell_books_credit_and_fee(exact_case):
    # A separate synthetic initial long, not an exact BUY -> SELL closed cycle.
    from test_q7a_source_provider_refresh import position, stage
    x=exact_case
    x.p.payload['positions']=[position()]
    x.p.payload['totalAmountShares']=money(1050)
    x.p.payload['totalAmountPortfolio']=money(1_001_050)
    stage(x,1,'synthetic-preowned-long-for-sell')
    x.refresh();x.p.target=0
    intent,_,_=_input(x)
    assert intent.candidate.direction=='SELL'
    owners=_owners(x);result=_record(x)
    assert result.cash_delta_nano==1_051_111_111_101
    assert _cash(_export(x))==1_001_051_111_111_101
    assert result.appended_transactions==2 and _owners(x)==owners
    assert _record(x).replay


@pytest.mark.parametrize('fee_first',[False,True])
def test_complete_pagination_and_reordered_replay_use_identical_components(exact_case,fee_first):
    x=exact_case;_input(x)
    calls=[]
    def pages(payload,timeout):
        assert payload['withoutTrades'] is payload['withoutCommissions'] is False
        assert timeout>0;calls.append(payload['cursor'])
        rows=list(reversed(x.operations)) if fee_first else x.operations
        if payload['cursor']=='':return {'hasNext':True,'nextCursor':'NEXT','items':[deepcopy(rows[0])]}
        assert payload['cursor']=='NEXT'
        return {'hasNext':False,'nextCursor':'','items':[deepcopy(rows[1])]}
    x.p.get_operations_by_cursor_once=pages
    first=_record(x);saved=_export(x);plan=_plan_path(x).read_bytes()
    x.operations.reverse()
    second=_record(x)
    assert calls==['','NEXT','','NEXT'] and second.replay
    assert first.transaction_sha256s==second.transaction_sha256s
    assert _export(x)==saved and _plan_path(x).read_bytes()==plan


@pytest.mark.parametrize('damage',['last_page_error','last_page_omits_fee','monotonic_regression','wall_stale','metadata','central'])
def test_incomplete_or_changed_reads_do_not_write_ledger(exact_case,damage):
    from datetime import timedelta
    x=exact_case;intent,_,_=_input(x);a=x.c.execution_adapter
    before=_export(x);base_owners=_owners(x)
    if damage.startswith('last_page'):
        def pages(payload,timeout):
            if not payload['cursor']:return {'hasNext':True,'nextCursor':'NEXT','items':[deepcopy(x.operations[0])]}
            if damage=='last_page_error':raise TimeoutError('PRIVATE_PROVIDER')
            return {'hasNext':False,'nextCursor':'','items':[]}
        x.p.get_operations_by_cursor_once=pages
    else:
        old=x.p.get_operations_by_cursor_once
        def changed(payload,timeout):
            data=old(payload,timeout)
            if damage=='monotonic_regression':a.cl7_monotonic_ns=lambda:0
            elif damage=='wall_stale':x.p.clock_at+=timedelta(seconds=6)
            elif damage=='metadata':(x.root/'portfolio_risk_metadata.json').write_bytes(b'changed')
            elif damage=='central':
                newer=replace(intent,last_error='PRIVATE_CONCURRENT_CHANGE')
                a.manager.store.mutate(ACCOUNT,lambda state:(state.replace_intent(newer),newer))
            return data
        x.p.get_operations_by_cursor_once=changed
    from trading_robot.exact_cash_settlement import ExactCashSettlementError
    with pytest.raises(ExactCashSettlementError):_record(x)
    assert _export(x)==before and x.p.order_calls==1
    assert a.cash_authority_manager.status().canonical_bytes==base_owners[3]


def _unrelated_append(x):
    from test_q7a_source_exact_dispatch import operation
    from trading_robot import broker_read_adapters as cl3
    a=x.c.execution_adapter; row=operation(x,'INPUT',1)
    request=cl3.BrokerReadRequest(environment=cl3.BrokerEnvironment.SANDBOX,raw_account_id=ACCOUNT,
        identity_key=a.cl7_identity_key,identity_key_id=a.cl7_identity_key_id,
        from_inclusive='2027-01-01T00:00:00.000000000Z',to_exclusive='2027-01-02T00:00:00.000000000Z',
        limit=100,max_pages=1,max_items=1,absolute_deadline_ns=10**9,retry_policy=cl3.RetryPolicy(1,10**9,()),
        transport=lambda *_:{'hasNext':False,'nextCursor':'','items':[row]},monotonic_ns=lambda:1,wait_ns=lambda _:None)
    d=cl3.collect_tbank_operations(request).decisions[0];store=a.cl7_ledger_store
    store.append_observation(d.observation,expected_store_revision=store.snapshot().store_revision)
    store.append_transaction(d.transaction_proposal,d.observation.sha256,
        expected_store_revision=store.snapshot().store_revision,expected_ledger_revision=store.snapshot().ledger_revision)


@pytest.mark.parametrize('phase',['before_first_plan','during_cash_read','after_completed_plan'])
def test_unrelated_ledger_movement_is_not_adopted_as_settlement_prefix(exact_case,phase):
    x=exact_case;_input(x)
    if phase=='after_completed_plan':_record(x)
    if phase=='during_cash_read':x.p.on_cash_positions=lambda:_unrelated_append(x)
    else:_unrelated_append(x)
    owners=_owners(x)
    from trading_robot.exact_cash_settlement import ExactCashSettlementError
    with pytest.raises(ExactCashSettlementError):_record(x)
    assert _owners(x)==owners
    # Only the deliberately injected foreign transaction was added.
    assert len(json.loads(_export(x))['transactions'])==(4 if phase=='after_completed_plan' else 2)


def test_final_readback_failure_is_replayable_not_double_booked(exact_case,monkeypatch):
    x=exact_case;_input(x)
    from trading_robot import exact_cash_settlement as mod
    original=mod.cl4.reconcile_shadow_cash;fired=[]
    def fail(*args,**kwargs):
        if not fired:fired.append(1);raise RuntimeError('PRIVATE_READBACK_CUT')
        return original(*args,**kwargs)
    monkeypatch.setattr(mod.cl4,'reconcile_shadow_cash',fail)
    with pytest.raises(mod.ExactCashSettlementError):_record(x)
    after=_export(x);owners=_owners(x)
    result=_record(x)
    assert result.replay and _export(x)==after and _owners(x)==owners
