"""STEP34: native admitted intent, explicit arm, one synthetic POST and no repeats."""
from __future__ import annotations
from copy import deepcopy
from datetime import timedelta
from dataclasses import replace
import importlib
import json
import sqlite3
import pytest

from test_q7a_source_versioned_risk_admission import (
    base_desktop_case, desktop_case, exact_case, refresh_case, read_case, op_case,
    cut_case, selected_case, owner_case, admission_case, admit, refresh, _snapshot, money,
)
from test_q7a_source_settlement_closure import _recompose
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State
from trading_robot.locking import InterProcessFileLock, LockUnavailableError


def module():
    return importlib.import_module('trading_robot.versioned_dispatch')


def _arm(c, **kwargs):
    intent = c.a.manager.state().queued[0]
    h = c.a.cash_authority_manager.status().sha256
    return module().arm_selected_order(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,expected_authority_sha256=h,
        expected_intent_id=intent.intent_id,confirmation=f'ARM VERSIONED ORDER {intent.intent_id} {h}',**kwargs)


def _send(c, arm, **kwargs):
    return module().dispatch_selected_order(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,expected_arm_sha256=arm.arm_sha256,
        expected_authority_sha256=arm.authority_sha256,expected_intent_id=arm.intent_id,**kwargs)


def _recover(c, digest):
    return module().recover_selected_dispatch_identity(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,expected_plan_sha256=digest)


def _v4(c):
    from trading_robot.versioned_operational_store import VersionedOperationalStore
    with VersionedOperationalStore.open(c.root/'ledger',**module().cut._common(c.a)) as store:
        return store.export_bytes()


def _wire(c, monkeypatch):
    c.posts=[]
    def post(account,uid,lots,direction,*,order_id,order_type,time_in_force):
        record=c.a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        assert record.state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING
        intent=c.a.manager.store._load_unlocked(expected_account_id=account).blocking_intent
        assert intent.status=='IN_FLIGHT' and intent.intent_id==order_id
        assert intent.versioned_dispatch_plan_sha256==record.pending_dispatch_proof_sha256
        assert record.post_attempt_count==2
        for path in (c.r.manager.repository.lock_path,c.r.risk.state_store.lock_path,c.a.manager.store.lock_path):
            with pytest.raises(LockUnavailableError):
                with InterProcessFileLock(path,timeout_seconds=0):pass
        for root in (c.root/'ledger',c.a.cl7_ledger_store.root):
            connection=sqlite3.connect(root/'store.sqlite3',timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError):connection.execute('BEGIN IMMEDIATE')
            finally:connection.close()
        c.provider.order_calls+=1
        c.posts.append(dict(accountId=account,instrumentUid=uid,lotsRequested=str(lots),lotsExecuted='0',
            direction='ORDER_DIRECTION_'+direction,orderType='ORDER_TYPE_'+order_type,orderId='synthetic-v4-exchange',
            orderRequestId=order_id,executionReportStatus='EXECUTION_REPORT_STATUS_NEW',currency='rub'))
        return deepcopy(c.posts[-1])
    monkeypatch.setattr(c.provider,'post_order_once',post)


@pytest.mark.parametrize('direction',['BUY','SELL'])
def test_core_native_admission_arm_and_one_post(admission_case,monkeypatch,request,direction):
    c=admission_case
    uid='uid-lkoh'
    if direction=='SELL':
        from test_q7a_source_selected_sync import _run
        c.provider.target=0;c.provider.clock_at+=timedelta(hours=1);c.provider.quote_at=c.provider.clock_at
        _run(c);c.provider.clock_at+=timedelta(seconds=1);refresh(c);uid='uid-sber'
    admitted=admit(c,uid);before=_snapshot(c.x.c.y.x);v4_before=_v4(c)
    _wire(c,monkeypatch)
    arm=_arm(c)
    assert c.provider.order_calls==1
    assert c.a.manager.state().queued[0].intent_id==admitted.intent_id
    result=_send(c,arm)
    assert result.status=='SUBMITTED' and _v4(c)==v4_before
    final=_snapshot(c.x.c.y.x)
    for k in ('portfolio','risk'):assert final[0][k]==before[0][k]
    assert final[1]==before[1]
    assert final[2]==before[2]+1
    intent=c.a.manager.state().blocking_intent
    assert intent.status=='SUBMITTED' and intent.intent_id==admitted.intent_id
    assert intent.candidate.direction==direction and intent.reserved_cash_kopecks==(106050 if direction=='BUY' else 0)
    assert c.provider.order_calls==2 and len(c.posts)==1
    assert c.a.cash_authority_manager.status().post_attempt_count==2
    with pytest.raises(module().VersionedDispatchError,match='NOT_ARMED_OR_ALREADY_ATTEMPTED'):_send(c,arm)
    assert c.provider.order_calls==2
    request.node.user_properties.extend([('direction',direction),('new_fake_post',len(c.posts)),
        ('fake_post_total',c.provider.order_calls),('attempt_count',2),('reserved_cash_kopecks',intent.reserved_cash_kopecks),
        ('money_or_risk_execution_writes',0),('authority_state',c.a.cash_authority_manager.status().state.value)])


def _plan(c):
    paths=list((c.root/'versioned_dispatch/plans').glob('*.json'))
    assert len(paths)==1
    return paths[0].stem


def _lookup(c,monkeypatch,raw):
    calls=[]
    def read(account,order_id,*,by_request_id=False):
        assert account==c.a.policy.account_id and order_id==raw['orderRequestId'] and by_request_id is True
        calls.append(order_id);return deepcopy(raw)
    monkeypatch.setattr(c.provider,'get_order_state',read)
    return calls


@pytest.mark.parametrize('point',['dispatch.after_plan','dispatch.after_attempt','dispatch.after_inflight',
    'dispatch.after_post','dispatch.after_submitted'])
def test_cut_never_reposts_and_lost_ack_identity_is_recovered(admission_case,desktop_case,monkeypatch,point,request):
    c=admission_case;admit(c);_wire(c,monkeypatch);arm=_arm(c)
    before=_snapshot(c.x.c.y.x)
    def fault(at):
        if at==point:raise RuntimeError('synthetic cut')
    with pytest.raises(RuntimeError,match='synthetic cut'):_send(c,arm,fault_injector=fault)
    plan=_plan(c)
    state=c.a.cash_authority_manager.status()
    if point=='dispatch.after_plan':
        assert state.state is State.EXACT_CASH_VERSIONED_ARMED and state.post_attempt_count==1
        assert c.posts==[]
        with pytest.raises(module().VersionedDispatchError,match='CONFIRMED_ATTEMPT_REQUIRED'):_recover(c,plan)
        # No attempt was spent, so a fresh checked invocation is permitted.
        result=_send(c,arm);assert result.status=='SUBMITTED' and len(c.posts)==1
    else:
        assert state.state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING and state.post_attempt_count==2
        with pytest.raises(module().VersionedDispatchError,match='ALREADY_ATTEMPTED'):_send(c,arm)
        if not c.posts:
            saved=_snapshot(c.x.c.y.x)
            monkeypatch.setattr(c.provider,'get_order_state',lambda *a,**k:(_ for _ in ()).throw(TimeoutError('unavailable')))
            with pytest.raises(module().VersionedDispatchError,match='LOOKUP_UNAVAILABLE_NO_RETRY'):_recover(c,plan)
            assert _snapshot(c.x.c.y.x)==saved
            assert c.provider.order_calls==1
        else:
            if point=='dispatch.after_post':
                _recompose(c.x.c.y.x,desktop_case)
                c.a=c.x.c.y.x.c.execution_adapter;c.r=c.x.c.y.x.c.cycle_source.portfolio_recovery
            calls=_lookup(c,monkeypatch,c.posts[0])
            result=_recover(c,plan)
            assert result.status=='SUBMITTED_SETTLEMENT_REQUIRED' and len(calls)==1
            assert c.a.manager.state().blocking_intent.status=='SUBMITTED'
            final=_snapshot(c.x.c.y.x)
            assert _recover(c,plan).status=='SUBMITTED_SETTLEMENT_REQUIRED'
            assert _snapshot(c.x.c.y.x)==final and len(c.posts)==1
    final=_snapshot(c.x.c.y.x)
    assert final[1]==before[1]
    for k in ('portfolio','risk'):assert final[0][k]==before[0][k]
    request.node.user_properties.extend([('cut',point),('new_fake_post',len(c.posts)),
        ('attempt_count',c.a.cash_authority_manager.status().post_attempt_count),('new_cash_or_risk_records',0)])


@pytest.mark.parametrize('response',['timeout','bad_request_id','filled'])
def test_provider_outcome_keeps_pending_and_forbids_legacy_completion(admission_case,monkeypatch,response):
    from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
    c=admission_case;admit(c);_wire(c,monkeypatch);arm=_arm(c);post=c.provider.post_order_once
    def injected(*args,**kwargs):
        raw=post(*args,**kwargs)
        if response=='timeout':raise TimeoutError('private token should not be echoed')
        if response=='bad_request_id':raw['orderRequestId']='other'
        if response=='filled':raw.update(lotsExecuted=raw['lotsRequested'],executionReportStatus='EXECUTION_REPORT_STATUS_FILL')
        return raw
    monkeypatch.setattr(c.provider,'post_order_once',injected)
    before=_snapshot(c.x.c.y.x);result=_send(c,arm)
    assert result.status==('SUBMITTED' if response=='filled' else 'SUBMISSION_UNCERTAIN')
    assert len(c.posts)==1 and c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING
    intent=c.a.manager.state().blocking_intent
    assert intent.executed_lots==0 and intent.risk_execution_id is None
    with pytest.raises(Exception,match='EXACT_SETTLEMENT_REQUIRED'):
        c.a.manager.mark_reconciled(intent.intent_id,portfolio_repository=c.r.manager.repository,
            outcome='FILLED',executed_lots=1,risk_runtime=c.r.risk)
    controller=c.x.c.y.x.c;controller.set_connected(True);controller.set_market_state('OPEN')
    with pytest.raises(GuiRuntimeBlockedError,match='VERSIONED_CASH_ROUTE_NOT_ACTIVE'):controller.run_cycle()
    with pytest.raises(Exception,match='STATE_TRANSITION_INVALID'):
        c.a.cash_authority_manager.arm(raw_account_id=c.a.policy.account_id,identity_key=c.a.cl7_identity_key,
            identity_key_id=c.a.cl7_identity_key_id,confirmation=c.a.cash_authority_manager.ARM_PHRASE,
            transition_at=c.a.cl7_clock())
    final=_snapshot(c.x.c.y.x)
    assert final[1]==before[1]
    for name in ('portfolio','risk'):assert final[0][name]==before[0][name]
    _lookup(c,monkeypatch,c.posts[0]);found=_recover(c,result.plan_sha256)
    assert found.status==('SUBMITTED_SETTLEMENT_REQUIRED' if response=='filled' else 'UNCERTAIN_IDENTITY_OBSERVED')
    assert _snapshot(c.x.c.y.x)==final and c.provider.order_calls==2


def test_unconfirmed_arm_and_pre_attempt_vetoes_leave_reserve(admission_case,monkeypatch):
    from decimal import Decimal
    c=admission_case;admit(c);m=module();intent=c.a.manager.state().queued[0]
    saved=_snapshot(c.x.c.y.x);h=c.a.cash_authority_manager.status().sha256
    with pytest.raises(m.VersionedDispatchError,match='CONFIRMATION_INVALID'):
        m.arm_selected_order(c.a,recovery=c.r,target_root=c.root,expected_selection_sha256=c.prepared.plan_sha256,
            expected_authority_sha256=h,expected_intent_id=intent.intent_id,confirmation='ARM')
    assert _snapshot(c.x.c.y.x)==saved
    arm=_arm(c);_wire(c,monkeypatch);saved=_snapshot(c.x.c.y.x)
    originals={name:getattr(c.provider,name) for name in ('get_trading_status','get_positions','get_max_lots','get_last_prices','get_orders')}
    for damage in ('market','quote_uid','cash','external_order','final_money','final_lots','stale'):
        with monkeypatch.context() as mp:
            positions=[]
            if damage=='market':mp.setattr(c.provider,'get_trading_status',lambda *a:{'apiTradeAvailableFlag':False})
            elif damage=='quote_uid':
                def quote(*a,**k):
                    raw=deepcopy(originals['get_last_prices'](*a,**k));raw[0]['instrumentUid']='other';return raw
                mp.setattr(c.provider,'get_last_prices',quote)
            elif damage=='cash':
                def cash(*a,**k):
                    raw=deepcopy(originals['get_positions'](*a,**k));raw['money'][0]['nano']+=1;return raw
                mp.setattr(c.provider,'get_positions',cash)
            elif damage=='external_order':mp.setattr(c.provider,'get_orders',lambda *a,**k:[{'orderId':'foreign'}])
            elif damage in ('final_money','final_lots'):
                def cash(*a,**k):
                    positions.append(1);return originals['get_positions'](*a,**k)
                def limits(*a,**k):
                    raw=deepcopy(originals['get_max_lots'](*a,**k))
                    if len(positions)>=2:
                        if damage=='final_money':raw['buyLimits']['buyMoneyAmount']=money(Decimal('1'))
                        else:raw['buyLimits']['buyMaxMarketLots']='0'
                    return raw
                mp.setattr(c.provider,'get_positions',cash);mp.setattr(c.provider,'get_max_lots',limits)
            else:mp.setattr(c.a,'cl7_clock',lambda:'2027-01-01T14:00:00.000000000Z')
            with pytest.raises(Exception) as caught:_send(c,arm)
            assert not isinstance(caught.value,(TypeError,KeyError,AttributeError,UnboundLocalError)),repr(caught.value)
            assert _snapshot(c.x.c.y.x)==saved and not c.posts
            if damage.startswith('final_'):assert len(positions)==2
    revoked=m.disarm_selected_order(c.a,recovery=c.r,target_root=c.root,expected_selection_sha256=c.prepared.plan_sha256,
        expected_arm_sha256=arm.arm_sha256,expected_authority_sha256=arm.authority_sha256)
    assert revoked.state is State.EXACT_CASH_VERSIONED_DISARMED and revoked.post_attempt_count==1
    with pytest.raises(m.VersionedDispatchError,match='ALREADY_ATTEMPTED'):_send(c,arm)
    assert c.a.manager.state().queued[0]==intent


def test_recovery_rejects_resigned_false_final_evidence_and_owner_drift(admission_case,monkeypatch):
    from trading_robot.versioned_fee_evidence import _sealed,_sha
    c=admission_case;admit(c);_wire(c,monkeypatch);arm=_arm(c);result=_send(c,arm)
    m=module();plan_file=c.root/'versioned_dispatch/plans'/(result.plan_sha256+'.json')
    plan=json.loads(plan_file.read_text())['payload']
    # Validate semantics directly, keeping the real pending identity unchanged.
    armed,p,ap,owners=m._load_arm(c.a,c.r,c.root,c.prepared.plan_sha256,arm.arm_sha256)
    saved=_snapshot(c.x.c.y.x)
    for damage in ('request_reserve','funds_amount','risk','extra_read'):
        bad=deepcopy(plan)
        if damage=='request_reserve':bad['request_binding']['payload']['reserved_cash_nano']='1'
        elif damage=='funds_amount':bad['locked_reads'][1]['response']['buyLimits']['buyMoneyAmount']=money(0)
        elif damage=='risk':bad['evaluation']['available_cash_kopecks']+=1
        else:bad['locked_reads'].append(deepcopy(bad['locked_reads'][0]))
        if damage=='request_reserve':
            bad['request_binding']=json.loads(_sealed(bad['request_binding']['payload'],c.a.cl7_identity_key))
        with c.a.cash_authority_manager.store.locked(),m._locked_source(c.a,c.r,p,ap) as (adapter,view):
            with pytest.raises(Exception) as caught:m._historical_plan(c.a,c.r,armed,ap,owners,bad,adapter,view)
            assert not isinstance(caught.value,(KeyError,AttributeError,TypeError)),repr(caught.value)
        assert _snapshot(c.x.c.y.x)==saved
    _lookup(c,monkeypatch,c.posts[0])
    original=c.r.risk.state_store.load_account(c.a.policy.account_id)
    c.r.risk.state_store.save_account(c.a.policy.account_id,replace(original,daily_order_count=original.daily_order_count+1))
    drift=_snapshot(c.x.c.y.x)
    with pytest.raises(m.VersionedDispatchError,match='OWNER_CHANGED'):_recover(c,result.plan_sha256)
    assert _snapshot(c.x.c.y.x)==drift
