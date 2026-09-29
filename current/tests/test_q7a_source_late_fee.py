"""STEP20: explicit supplemental fee, synthetic IO and genuine CL2/CL7 stores."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import sqlite3

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money, position
from test_q7a_source_natural_cycle import ACCOUNT, UID
from test_q7a_source_cash_components import _input, _record, _export, _cash
from test_q7a_source_settlement_closure import _close, _snapshot, _recompose
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState


def _setup(x, *, days=1, initial_fee='0.123456789', delta='0.010000001'):
    intent, raw, trade = _input(x, fee=initial_fee)
    x.p.payload['positions'] = [position(intent.candidate.target_lots)] if intent.candidate.target_lots else []
    cash = _record(x)
    _close(x, cash.proof_sha256)
    x.closed_export = _export(x)
    x.closed_owners = _snapshot(x)[0]
    x.p.clock_at += timedelta(days=days, seconds=1)
    raw['executedCommission'] = money(Decimal(initial_fee) + Decimal(delta))
    fee = {'id':'PRIVATE_ADDITIONAL_FEE','cursor':'PRIVATE_CURSOR_LATE','brokerAccountId':ACCOUNT,
        'date':stamp(x.p.clock_at),'type':'OPERATION_TYPE_BROKER_FEE','state':'OPERATION_STATE_EXECUTED',
        'quantity':'0','quantityDone':'0','quantityRest':'0','payment':money(-Decimal(delta)),
        'commission':money(0),'parentOperationId':trade['id'],'instrumentUid':UID,'childOperations':[]}
    x.operations.append(fee)
    x.p.wallet_rub -= Decimal(delta)
    x.advance()
    return intent, raw, trade, fee, cash.proof_sha256


def _late(x, proof):
    method = getattr(x.c.execution_adapter, 'reconcile_late_fee', None)
    assert callable(method), 'STEP20 late fee reconciliation is missing'
    return method(recovery=x.c.cycle_source.portfolio_recovery, proof_sha256=proof)


@pytest.mark.parametrize('days',[0,1,3])
@pytest.mark.parametrize('initial_fee',['0','0.123456789'])
def test_late_additive_fee_once_with_no_second_execution(exact_case, days, initial_fee, request):
    x=exact_case
    intent,raw,trade,fee,proof=_setup(x,days=days,initial_fee=initial_fee)
    a=x.c.execution_adapter
    before_cash=_cash(_export(x))
    r=_late(x,proof)
    assert r.fee_delta_nano==10_000_001 and r.appended_transactions==1 and not r.replay
    assert _cash(_export(x))==before_cash-10_000_001
    assert len(json.loads(_export(x))['transactions'])==len(json.loads(x.closed_export)['transactions'])+1
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert a.cash_authority_manager.status().post_attempt_count==1
    state=_snapshot(x)[0]
    assert {k:state[k] for k in ('portfolio','risk','central')} == {
        k:x.closed_owners[k] for k in ('portfolio','risk','central')}
    assert len(a.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids)==1
    assert x.p.order_calls==1
    saved=_snapshot(x)
    again=_late(x,proof)
    assert again.replay and again.appended_transactions==0 and _snapshot(x)==saved
    assert r.to_canonical_dict()['automatic_rearm_allowed'] is False
    request.node.user_properties.extend([('late_delta_nano',r.fee_delta_nano),('cash_nano',_cash(_export(x))),
        ('posts',x.p.order_calls),('risk_executions',1),('replay_appends',again.appended_transactions)])


@pytest.mark.parametrize('damage',[
    'missing_fee','no_increase','decrease','unknown_fee','unknown_service','service_fee','exchange',
    'request','trade_id','trade_price','old_operation_missing','old_operation_alias','old_fee_alias',
    'old_summary_changed','extra_fee','unrelated_cash','new_alias_duplicate','parent','instrument',
    'currency','positive','amount','quantity','children','trades','pending_operation','fee_summary',
    'cash_mismatch','cash_blocked','incomplete','timeout','window','future_fee','early_fee',
])
def test_bad_late_evidence_retains_quarantine_and_no_new_money(exact_case,damage):
    from trading_robot.exact_late_fee import ExactLateFeeError
    x=exact_case;intent,raw,trade,fee,proof=_setup(x);a=x.c.execution_adapter
    if damage=='missing_fee':x.operations.pop()
    elif damage=='no_increase':raw['executedCommission']=money('0.123456789')
    elif damage=='decrease':raw['executedCommission']=money('0.1')
    elif damage=='unknown_fee':raw.pop('executedCommission')
    elif damage=='unknown_service':raw.pop('serviceCommission')
    elif damage=='service_fee':raw['serviceCommission']=money('0.1')
    elif damage=='exchange':raw['orderId']='OTHER_EXCHANGE'
    elif damage=='request':raw['orderRequestId']='OTHER_REQUEST'
    elif damage=='trade_id':raw['stages'][0]['tradeId']='OTHER_TRADE'
    elif damage=='trade_price':raw['stages'][0]['price']=raw['averagePositionPrice']=money(106)
    elif damage=='old_operation_missing':x.operations.remove(trade)
    elif damage=='old_operation_alias':trade['id']='RENAMED_TRADE'
    elif damage=='old_fee_alias':x.operations[1]['id']='RENAMED_OLD_FEE'
    elif damage=='old_summary_changed':trade['commission']=raw['executedCommission']
    elif damage=='extra_fee':extra=deepcopy(fee);extra['id']='ADDITIONAL_2';x.operations.append(extra)
    elif damage=='unrelated_cash':fee.update(type='OPERATION_TYPE_OUTPUT',parentOperationId='')
    elif damage=='new_alias_duplicate':extra=deepcopy(fee);extra['id']='RENAMED_DUPLICATE';x.operations.append(extra)
    elif damage=='parent':fee['parentOperationId']='OTHER_PARENT'
    elif damage=='instrument':fee['instrumentUid']='OTHER_INSTRUMENT'
    elif damage=='currency':fee['payment']['currency']='USD'
    elif damage=='positive':fee['payment']=money('0.010000001')
    elif damage=='amount':fee['payment']=money('-0.02')
    elif damage=='quantity':fee['quantity']='1'
    elif damage=='children':fee['childOperations']=[{'payment':money(-1),'instrumentUid':UID}]
    elif damage=='trades':fee['tradesInfo']={'trades':deepcopy(trade['tradesInfo']['trades'])}
    elif damage=='pending_operation':fee['state']='OPERATION_STATE_PROGRESS'
    elif damage=='fee_summary':fee['commission']=money('0.001')
    elif damage=='cash_mismatch':x.p.wallet_rub-=Decimal('0.01')
    elif damage=='cash_blocked':x.p.cash_blocked_rub=1
    elif damage=='incomplete':x.p.get_operations_by_cursor_once=lambda *_:{'hasNext':True,'items':x.operations,'nextCursor':''}
    elif damage=='timeout':
        def fail(*_):raise TimeoutError('PRIVATE')
        x.p.get_operations_by_cursor_once=fail
    elif damage=='window':x.p.clock_at+=timedelta(days=8)
    elif damage=='future_fee':fee['date']=stamp(x.p.clock_at+timedelta(days=1))
    elif damage=='early_fee':fee['date']='2000-01-01T00:00:00.000000000Z'
    before=_snapshot(x)
    with pytest.raises(ExactLateFeeError):_late(x,proof)
    after=_snapshot(x)
    assert after[1:]==before[1:]
    assert {k:after[0][k] for k in ('portfolio','risk','central')} == {
        k:before[0][k] for k in ('portfolio','risk','central')}
    held=a.cash_authority_manager.status()
    assert held.state.value=='EXACT_CASH_FEE_ADJUSTMENT_PENDING'
    assert held.post_attempt_count==1 and held.pending_dispatch_proof_sha256==proof


@pytest.mark.parametrize('cut',['hold','plan','observation','transaction','audit','authority'])
@pytest.mark.parametrize('recompose',[False,True])
def test_committed_cut_restores_same_fee_once(exact_case,desktop_case,monkeypatch,cut,recompose):
    from trading_robot import exact_late_fee as mod
    x=exact_case;intent,raw,trade,fee,proof=_setup(x);a=x.c.execution_adapter
    fired=[]
    with monkeypatch.context() as mp:
        if cut=='plan':target,name=mod._LateFeeStore,'create'
        elif cut in {'hold','authority'}:target,name=a.cash_authority_manager.store,'_commit_unlocked'
        elif cut=='audit':target,name=x.manager.transaction_coordinator,'_record'
        elif cut=='observation':target,name=a.cl7_ledger_store,'append_observation'
        else:target,name=a.cl7_ledger_store,'append_transaction'
        fn=getattr(target,name)
        def interrupted(*args,**kwargs):
            result=fn(*args,**kwargs)
            wanted=(cut not in {'hold','authority'} or args[0].transition_kind==(
                'LATE_FEE_ADJUSTMENT_HELD' if cut=='hold' else 'LATE_FEE_ADJUSTMENT_CLOSED_DISARMED'))
            if wanted and not fired:fired.append(1);raise RuntimeError('AFTER_COMMIT')
            return result
        mp.setattr(target,name,interrupted)
        with pytest.raises(mod.ExactLateFeeError):_late(x,proof)
    assert fired
    assert a.cash_authority_manager.status().state.value == (
        'EXACT_CASH_DISARMED' if cut=='authority' else 'EXACT_CASH_FEE_ADJUSTMENT_PENDING')
    if recompose:_recompose(x,desktop_case)
    result=_late(x,proof)
    assert _cash(_export(x))==_cash(x.closed_export)-10_000_001
    assert len(json.loads(_export(x))['transactions'])==4
    assert x.p.order_calls==1
    state=_snapshot(x)[0]
    assert all(state[k]==x.closed_owners[k] for k in ('portfolio','risk','central'))
    saved=_snapshot(x)
    assert _late(x,proof).replay and _snapshot(x)==saved


def test_missing_fee_can_arrive_later_under_existing_hold(exact_case):
    from trading_robot.exact_late_fee import ExactLateFeeError
    x=exact_case;intent,raw,trade,fee,proof=_setup(x)
    x.operations.pop()
    with pytest.raises(ExactLateFeeError):_late(x,proof)
    held=x.c.execution_adapter.cash_authority_manager.status()
    x.operations.append(fee);x.advance()
    result=_late(x,proof)
    assert result.appended_transactions==1
    assert x.c.execution_adapter.cash_authority_manager.status().record_revision==held.record_revision+1
    assert x.p.order_calls==1


@pytest.mark.parametrize('damage',['alias','receipt','plan_delete','plan_tamper','second_fee'])
def test_replay_never_rebooks_renamed_or_changed_fee(exact_case,damage):
    from trading_robot.exact_late_fee import ExactLateFeeError
    x=exact_case;intent,raw,trade,fee,proof=_setup(x)
    _late(x,proof)
    saved=_snapshot(x)
    if damage=='alias':fee['id']='CHANGED_ALIAS'
    elif damage=='receipt':raw['executedCommission']=money('0.2')
    elif damage=='second_fee':
        extra=deepcopy(fee);extra['id']='LATE_SECOND';x.operations.append(extra)
        raw['executedCommission']=money('0.143456791');x.p.wallet_rub-=Decimal('0.010000001')
    else:
        path=x.c.execution_adapter.manager.store.path.parent/'exact_late_fee'/f'{proof}.json'
        if damage=='plan_delete':path.unlink()
        else:
            doc=json.loads(path.read_bytes());doc['payload']['match']['delta_nano']='1';path.write_text(json.dumps(doc))
    with pytest.raises(ExactLateFeeError):_late(x,proof)
    assert _snapshot(x)==saved


@pytest.mark.parametrize('damage',['wrong_proof','armed','risk','portfolio','closure_missing','config'])
def test_invalid_initial_scope_does_not_create_hold(exact_case,damage):
    from trading_robot.exact_late_fee import ExactLateFeeError
    x=exact_case;intent,raw,trade,fee,proof=_setup(x);a=x.c.execution_adapter
    if damage=='wrong_proof':proof='f'*64
    elif damage=='armed':
        m=a.cash_authority_manager;m.arm(raw_account_id=ACCOUNT,identity_key=a.cl7_identity_key,
            identity_key_id=a.cl7_identity_key_id,confirmation=m.ARM_PHRASE,transition_at=a.cl7_clock())
    elif damage=='risk':
        store=a.risk_runtime.state_store;s=store.load_account(ACCOUNT);store.save_account(ACCOUNT,replace(s,daily_order_count=s.daily_order_count+1))
    elif damage=='portfolio':
        p=x.manager.repository.load(expected_account_id=ACCOUNT)
        x.manager.repository.save(replace(p,last_transaction_id='foreign'),expected_revision=p.revision,allow_equal_revision=True)
    elif damage=='closure_missing':(a.manager.store.path.parent/'exact_settlement_closure'/f'{proof}.json').unlink()
    elif damage=='config':a.cl7_own_funds_policy=None
    saved=_snapshot(x)
    with pytest.raises(ExactLateFeeError):_late(x,proof)
    assert _snapshot(x)==saved


def test_held_authority_blocks_arm_sync_dispatch_and_legacy_recovery(exact_case):
    from trading_robot.exact_late_fee import ExactLateFeeError
    from trading_robot.runtime_cash_authority import CL7RuntimeError
    x=exact_case;intent,raw,trade,fee,proof=_setup(x);a=x.c.execution_adapter;m=a.cash_authority_manager
    x.operations.pop()
    with pytest.raises(ExactLateFeeError):_late(x,proof)
    before=_snapshot(x)
    with pytest.raises(CL7RuntimeError):m.arm(raw_account_id=ACCOUNT,identity_key=a.cl7_identity_key,
        identity_key_id=a.cl7_identity_key_id,confirmation=m.ARM_PHRASE,transition_at=a.cl7_clock())
    with pytest.raises(CL7RuntimeError):m.sync_runtime(**x.inputs)
    with pytest.raises(CL7RuntimeError):m.recover_runtime(central_manager=a.manager,raw_account_id=ACCOUNT,
        identity_key=a.cl7_identity_key,identity_key_id=a.cl7_identity_key_id,transition_at=a.cl7_clock())
    result=a.dispatch_next(x.manager.repository)
    assert result.status!='SUBMITTED' and x.p.order_calls==1
    assert _snapshot(x)==before


def test_fee_reads_happen_after_hold_and_final_audit_has_locks(exact_case,monkeypatch):
    x=exact_case;intent,raw,trade,fee,proof=_setup(x);a=x.c.execution_adapter
    reads=[];audit=[]
    for name in ['get_order_state','get_operations_by_cursor_once','get_positions']:
        fn=getattr(x.p,name)
        def checked(*args,_fn=fn,_name=name,**kwargs):
            assert a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False).state.value=='EXACT_CASH_FEE_ADJUSTMENT_PENDING'
            reads.append(_name);return _fn(*args,**kwargs)
        monkeypatch.setattr(x.p,name,checked)
    fn=x.manager.transaction_coordinator._record
    def audited(*args,**kwargs):
        con=sqlite3.connect(a.cl7_ledger_store.root/'store.sqlite3',timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError,match='locked'):con.execute('BEGIN IMMEDIATE')
        finally:con.close()
        audit.append(1);return fn(*args,**kwargs)
    monkeypatch.setattr(x.manager.transaction_coordinator,'_record',audited)
    _late(x,proof)
    assert set(reads)=={'get_order_state','get_operations_by_cursor_once','get_positions'} and audit


@pytest.mark.parametrize('which',['utc','monotonic','owner','metadata','store'])
def test_change_during_read_retains_hold_and_no_fee(exact_case,monkeypatch,which):
    from trading_robot.exact_late_fee import ExactLateFeeError
    x=exact_case;intent,raw,trade,fee,proof=_setup(x);a=x.c.execution_adapter
    fn=x.p.get_order_state
    def changed(*args,**kwargs):
        v=fn(*args,**kwargs)
        if which=='utc':x.p.clock_at+=timedelta(seconds=6)
        elif which=='monotonic':a.cl7_monotonic_ns=lambda:10**12
        elif which=='owner':
            store=a.risk_runtime.state_store;s=store.load_account(ACCOUNT);store.save_account(ACCOUNT,replace(s,daily_order_count=s.daily_order_count+1))
        elif which=='metadata':a.cl7_own_funds_policy=None
        else:a.cl7_ledger_store=object()
        return v
    monkeypatch.setattr(x.p,'get_order_state',changed)
    store=a.cl7_ledger_store;before=store.export_bytes()
    with pytest.raises(ExactLateFeeError):_late(x,proof)
    assert store.export_bytes()==before and a.cash_authority_manager.status().state.value=='EXACT_CASH_FEE_ADJUSTMENT_PENDING'
    a.cl7_ledger_store=store  # Restore the deliberately damaged fixture for teardown.


def test_backdated_fee_is_found_by_full_overlap_not_old_watermark(exact_case,monkeypatch):
    x=exact_case;intent,raw,trade,fee,proof=_setup(x)
    fee['date']=trade['date']
    assert fee['date'] < x.closed_owners['authority']['operations_complete_through']
    requests=[];fn=x.p.get_operations_by_cursor_once
    def observed(payload,timeout):requests.append(deepcopy(payload));return fn(payload,timeout)
    monkeypatch.setattr(x.p,'get_operations_by_cursor_once',observed)
    result=_late(x,proof)
    assert result.appended_transactions==1
    assert requests and requests[0]['from']<=fee['date']<requests[0]['to']


@pytest.mark.parametrize('status',['CANCELLED','REJECTED'])
@pytest.mark.parametrize('initial_fee',['0','0.123456789'])
def test_late_fee_after_zero_terminal_does_not_invent_execution(exact_case,status,initial_fee):
    from test_q7a_source_exact_zero_terminal import _zero
    x=exact_case;intent,raw=_zero(x,status=status,fee=initial_fee)
    cash=_record(x);_close(x,cash.proof_sha256)
    old=_snapshot(x);risk=x.c.execution_adapter.risk_runtime.state_store.path.read_bytes()
    x.p.clock_at+=timedelta(days=1)
    raw['executedCommission']=money(Decimal(initial_fee)+Decimal('0.010000001'))
    x.operations.append({'id':'ZERO_LATE_FEE','cursor':'ZERO_LATE_CURSOR','brokerAccountId':ACCOUNT,
        'date':stamp(x.p.clock_at),'type':'OPERATION_TYPE_BROKER_FEE','state':'OPERATION_STATE_EXECUTED',
        'quantity':'0','quantityDone':'0','quantityRest':'0','payment':money('-0.010000001'),
        'commission':money(0),'parentOperationId':raw['orderId'],'instrumentUid':UID,'childOperations':[]})
    x.p.wallet_rub-=Decimal('0.010000001');x.advance()
    result=_late(x,cash.proof_sha256)
    assert result.appended_transactions==1
    assert _cash(_export(x))==_cash(old[1])-10_000_001
    assert x.c.execution_adapter.risk_runtime.state_store.path.read_bytes()==risk
    final=x.c.execution_adapter.manager.state().intents[-1]
    assert final.executed_lots==0 and final.outcome==status and final.risk_execution_status=='NOT_REQUIRED'
    assert final.risk_execution_id is None and x.p.order_calls==1


def test_late_fee_after_independent_sell_keeps_flat_and_single_execution(exact_case):
    from test_q7a_source_provider_refresh import stage
    x=exact_case
    x.p.payload['positions']=[position()]
    x.p.payload['totalAmountShares']=money(1050)
    x.p.payload['totalAmountPortfolio']=money(1_001_050)
    stage(x,1,'synthetic-preowned-long');x.refresh();x.p.target=0
    intent,raw,trade,fee,proof=_setup(x)
    assert intent.candidate.direction=='SELL'
    r=_late(x,proof)
    p=x.manager.repository.load(expected_account_id=ACCOUNT).position(UID)
    assert p.actual_lots==p.target_lots==0 and p.ownership is None
    assert r.fee_delta_nano==10_000_001
    assert len(x.c.execution_adapter.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids)==1
    assert _cash(_export(x))==1_001_051_101_111_100


@pytest.mark.parametrize('point',['append_observation.after_observation','append_transaction.after_transaction',
                                 'append_transaction.after_provenance','append_transaction.after_meta'])
def test_cl2_internal_rollback_and_resume_do_not_duplicate(exact_case,point):
    from trading_robot.exact_late_fee import ExactLateFeeError
    from trading_robot.cash_ledger_persistence import InjectedFault
    x=exact_case;intent,raw,trade,fee,proof=_setup(x);store=x.c.execution_adapter.cl7_ledger_store
    fired=[]
    def fault(name):
        if name==point and not fired:fired.append(1);raise InjectedFault(name)
    store._fault_injector=fault
    with pytest.raises(ExactLateFeeError):_late(x,proof)
    store._fault_injector=None
    assert fired and x.c.execution_adapter.cash_authority_manager.status().state.value=='EXACT_CASH_FEE_ADJUSTMENT_PENDING'
    _late(x,proof)
    assert _cash(_export(x))==_cash(x.closed_export)-10_000_001
    assert len(json.loads(_export(x))['transactions'])==4
    assert x.p.order_calls==1


@pytest.mark.parametrize('damage',['arm','reset_attempt','opening','skip_revision','wrong_head','watermark','change_account'])
def test_new_authority_transition_cannot_enable_trading_or_reset_custody(exact_case,damage):
    from trading_robot.runtime_cash_authority import CL7RuntimeError,_transition_pair
    from trading_robot.exact_late_fee import ExactLateFeeError
    x=exact_case;intent,raw,trade,fee,proof=_setup(x);a=x.c.execution_adapter;m=a.cash_authority_manager
    x.operations.pop()
    with pytest.raises(ExactLateFeeError):_late(x,proof)
    held=m.status();x.advance()
    changes=dict(state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED,pending_dispatch_proof_sha256=None,
        ledger_head_sha256='a'*64,ledger_revision=held.ledger_revision+1,operations_complete_through=a.cl7_clock())
    if damage=='arm':changes['state']=RuntimeCashAuthorityState.EXACT_CASH_ARMED
    elif damage=='reset_attempt':changes['post_attempt_count']=0
    elif damage=='opening':changes['opening_record_sha256']='a'*64
    elif damage=='skip_revision':changes['ledger_revision']=held.ledger_revision+2
    elif damage=='wrong_head':changes['ledger_head_sha256']=held.ledger_head_sha256
    elif damage=='watermark':changes['operations_complete_through']=held.operations_complete_through
    elif damage=='change_account':changes['account_scope_sha256']='a'*64
    with pytest.raises(CL7RuntimeError):
        candidate=m._change(held,at=a.cl7_clock(),kind='LATE_FEE_ADJUSTMENT_CLOSED_DISARMED',**changes)
        _transition_pair(held,candidate)
    assert m.status()==held


def test_privacy_safe_result_omits_money_and_raw_ids(exact_case):
    x=exact_case;intent,raw,trade,fee,proof=_setup(x)
    r=_late(x,proof)
    text=json.dumps(r.to_canonical_dict())
    for secret in (ACCOUNT,intent.intent_id,raw['orderId'],trade['id'],fee['id'],'10000001'):
        assert secret not in text
    assert r.to_canonical_dict()['risk_execution_written'] is False


def test_gui_tick_cannot_refresh_trade_or_mutate_during_fee_hold(exact_case):
    from trading_robot.exact_late_fee import ExactLateFeeError
    from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
    x=exact_case;intent,raw,trade,fee,proof=_setup(x);a=x.c.execution_adapter
    x.operations.pop()
    with pytest.raises(ExactLateFeeError):_late(x,proof)
    x.c.set_connected(True);x.c.set_market_state('OPEN')
    saved=_snapshot(x);calls=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    with pytest.raises(GuiRuntimeBlockedError,match='RECOVERY_REQUIRED'):x.c.run_cycle()
    assert _snapshot(x)==saved and calls==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
