"""STEP17: lost acknowledgement on the real exact path, synthetic provider only."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case, _desktop_cycle
from test_q7a_source_exact_dispatch import desktop_case, exact_case
from test_q7a_source_provider_refresh import refresh_case, money, position
from test_q7a_source_natural_cycle import ACCOUNT, UID
from test_q7a_source_exact_recovery_tick import (
    roundtrip_case, _fill_existing, _next_hour, _state,
)
from test_q7a_source_settlement_closure import _recompose
from test_q7a_source_cash_components import _export, _cash
from trading_robot.central_order_manager import CentralOrderStateError, LockedCentralDispatch
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState
from trading_robot.tbank_sandbox import TBankAPIError


def _lost_ack(x, monkeypatch, *, gui=False):
    def cut(*args, **kwargs):
        raise CentralOrderStateError('SYNTHETIC_ACK_STORE_FAILURE')
    with monkeypatch.context() as mp:
        mp.setattr(LockedCentralDispatch, 'mark_submitted', cut)
        x.c.run_cycle() if gui else _desktop_cycle(x.c)
    a=x.c.execution_adapter
    intent=a.manager.state().blocking_intent
    assert intent.status=='IN_FLIGHT' and intent.broker_order_id is None
    assert x.p.order_calls>=1
    authority=a.cash_authority_manager.status()
    assert authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    assert authority.post_attempt_count==x.p.order_calls
    assert sum(t.status=='IN_FLIGHT' for t in intent.transitions)==1
    x.advance();x.c.set_connected(True);x.c.set_market_state('OPEN')
    return intent


def _fill(x, intent):
    raw,trade=_fill_existing(x,intent)
    raw['orderId']='exchange-'+intent.intent_id
    # A later provider snapshot is strictly newer than identity binding.
    # This explicit synthetic clock progression does not change any source guard.
    x.p.on_portfolio=lambda:setattr(x.p,'clock_at',x.p.clock_at+timedelta(microseconds=1))
    return raw,trade


def _bind(x, proof=None):
    from trading_robot.exact_inflight_recovery import bind_inflight_order_locked
    a=x.c.execution_adapter
    with a.cash_authority_manager.store.locked():
        p=proof if proof is not None else a.cash_authority_manager.store._load_unlocked(
            allow_missing_legacy=False).pending_dispatch_proof_sha256
        return bind_inflight_order_locked(a,recovery=x.c.cycle_source.portfolio_recovery,
                                         expected_proof_sha256=p)


@pytest.mark.parametrize('surface',['run_cycle','service_tick','cli'])
def test_real_lost_ack_is_settled_once_without_second_post(exact_case,monkeypatch,surface,capsys,request):
    x=exact_case;intent=_lost_ack(x,monkeypatch);raw,_=_fill(x,intent)
    a=x.c.execution_adapter;proof=intent.cl7_locked_dispatch_proof_sha256
    counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    reads=len(x.p.read_calls)
    if surface=='run_cycle':
        r=x.c.run_cycle();assert r.actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    elif surface=='service_tick':
        r=x.c.service_tick(now=x.p.clock_at,latest_closed_candles={},hooks=object())
        assert r.actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    else:
        from tools.v3_10_exact_settlement_recover import main
        assert main(['--runtime-dir',str(x.root),'--execution-order-type','MARKET'])==0
        payload=json.loads(capsys.readouterr().out)
        assert payload['status']=='EXACT_SETTLEMENT_CLOSED_DISARMED'
        for private in (ACCOUNT,intent.intent_id,raw['orderId']):
            assert private not in json.dumps(payload)
    final=a.manager.state().intents[-1]
    assert final.intent_id==intent.intent_id and final.candidate==intent.candidate
    assert final.cl7_locked_dispatch_proof==intent.cl7_locked_dispatch_proof
    assert final.broker_order_id==raw['orderId'] and final.status=='RECONCILED'
    assert [t.status for t in final.transitions]==['QUEUED','IN_FLIGHT','SUBMITTED','RECONCILED']
    assert counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    assert any(row==('state',intent.intent_id,True) for row in x.p.read_calls[reads:])
    authority=a.cash_authority_manager.status()
    assert authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert authority.post_attempt_count==1 and authority.pending_dispatch_proof_sha256 is None
    risk=a.risk_runtime.state_store.load_account(ACCOUNT)
    assert risk.daily_order_count==1 and list(risk.recorded_execution_ids)==[intent.intent_id]
    assert _cash(_export(x))==998948641975321 and len(json.loads(_export(x))['transactions'])==3
    request.node.user_properties.extend([('fake_post_calls',x.p.order_calls),('attempt_count',1),
        ('risk_executions',risk.daily_order_count),('cash_nano',str(_cash(_export(x)))),
        ('authority_state',authority.state.value),('recovered_same_proof',final.cl7_locked_dispatch_proof_sha256==proof)])


def test_identity_binding_alone_does_not_settle_or_release(exact_case,monkeypatch):
    x=exact_case;intent=_lost_ack(x,monkeypatch);raw,_=_fill(x,intent)
    old=_state(x);a=x.c.execution_adapter;before=a.manager.state()
    bound=_bind(x)
    after=_state(x)
    assert (old[0],old[1],old[3],old[4])==(after[0],after[1],after[3],after[4])
    assert bound.broker_order_id==raw['orderId'] and bound.status=='SUBMITTED'
    assert bound.candidate==intent.candidate and bound.reserved_cash_kopecks==intent.reserved_cash_kopecks
    assert bound.executed_lots==0 and bound.outcome is None and bound.risk_execution_id is None
    assert a.manager.state().revision==before.revision+1
    assert a.manager.state().blocking_intent==bound and x.p.order_calls==1
    assert a.cash_authority_manager.status().pending_dispatch_proof_sha256==intent.cl7_locked_dispatch_proof_sha256


@pytest.mark.parametrize('damage',['404','timeout','none','request','account','instrument','direction',
    'empty_exchange','requested','lots_bool','stages','fee_negative','currency','order_type',
    'unknown_status','future_trade','wall_forward','wall_backward','mono_forward','mono_backward'])
def test_bad_or_missing_observation_never_changes_owners(exact_case,monkeypatch,damage):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;intent=_lost_ack(x,monkeypatch);raw,_=_fill(x,intent)
    if damage=='404':x.p.receipts[intent.intent_id]=TBankAPIError('PRIVATE_NOT_FOUND',status_code=404,transient=False)
    elif damage=='timeout':x.p.receipts[intent.intent_id]=TimeoutError('PRIVATE_TIMEOUT')
    elif damage=='none':x.p.receipts[intent.intent_id]=None
    elif damage=='request':raw['orderRequestId']='other'
    elif damage=='account':raw['accountId']='other'
    elif damage=='instrument':raw['instrumentUid']='other'
    elif damage=='direction':raw['direction']='ORDER_DIRECTION_SELL'
    elif damage=='empty_exchange':raw['orderId']=''
    elif damage=='requested':raw['lotsRequested']='2'
    elif damage=='lots_bool':raw['lotsExecuted']=True
    elif damage=='stages':raw['stages']=[]
    elif damage=='fee_negative':raw['executedCommission']=money(-1)
    elif damage=='currency':raw['currency']='usd'
    elif damage=='order_type':raw['orderType']='ORDER_TYPE_LIMIT'
    elif damage=='unknown_status':raw['executionReportStatus']='UNKNOWN'
    elif damage=='future_trade':
        from test_q7a_source_exact_dispatch import stamp
        raw['stages'][0]['executionTime']=stamp(x.p.clock_at+timedelta(seconds=1))
    elif damage.startswith('wall'):
        x.p.on_state=lambda:setattr(x.p,'clock_at',x.p.clock_at+timedelta(seconds=6 if damage=='wall_forward' else -1))
    else:
        timer=[1_000_000_000];x.c.execution_adapter.cl7_monotonic_ns=lambda:timer[0]
        x.p.on_state=lambda:timer.__setitem__(0,timer[0]+(6_000_000_000 if damage=='mono_forward' else -1))
    before=_state(x);counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    assert _state(x)==before and counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    assert x.c.execution_adapter.manager.state().blocking_intent==intent
    assert x.c.execution_adapter.dispatch_next(x.manager.repository,expected_intent_id=intent.intent_id).status=='CL7_DISPATCH_PENDING'
    assert x.p.order_calls==1


@pytest.mark.parametrize('status',['NEW','CANCELLED','REJECTED'])
def test_zero_non_fill_observation_can_bind_but_cannot_settle(exact_case,monkeypatch,status):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    from test_q7a_source_exact_receipt import _raw
    x=exact_case;intent=_lost_ack(x,monkeypatch)
    raw=_raw(x,intent,status=status,executed=0,commission=0);raw['orderId']='exchange-'+intent.intent_id
    old=_state(x)
    with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    after=_state(x)
    assert (old[0],old[1],old[3],old[4])==(after[0],after[1],after[3],after[4])
    now=x.c.execution_adapter.manager.state().blocking_intent
    assert now.status=='SUBMITTED' and now.executed_lots==0 and now.risk_execution_id is None
    assert now.reserved_cash_kopecks==intent.reserved_cash_kopecks and x.p.order_calls==1


@pytest.mark.parametrize('cut',['audit','before_save','after_save','cash','central','authority'])
@pytest.mark.parametrize('recompose',[False,True])
def test_restart_continues_without_duplicate_binding_or_cash(
    exact_case,desktop_case,monkeypatch,cut,recompose,
):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;intent=_lost_ack(x,monkeypatch);_fill(x,intent);a=x.c.execution_adapter
    counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    called=[]
    with monkeypatch.context() as mp:
        if cut=='audit':target,name=x.manager.transaction_coordinator,'_record'
        elif cut in {'before_save','after_save','central'}:target,name=a.manager.store,'_save_unlocked'
        elif cut=='cash':target,name=a.cl7_ledger_store,'append_transaction'
        else:target,name=a.cash_authority_manager.store,'_commit_unlocked'
        original=getattr(target,name)
        def fail(*args,**kwargs):
            match=(cut!='audit' or args[0]=='EXACT_INFLIGHT_IDENTITY_OBSERVED')
            if cut=='central':match=any(i.status=='RECONCILED' for i in args[0].intents)
            if match and not called and cut in {'before_save','audit'}:
                called.append(True);raise RuntimeError('PRIVATE_BINDING_FAULT')
            out=original(*args,**kwargs)
            if match and not called:
                called.append(True);raise RuntimeError('PRIVATE_AFTER_COMMIT')
            return out
        mp.setattr(target,name,fail)
        with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    assert called and counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    if cut in {'audit','before_save'}:
        assert a.manager.state().blocking_intent.status=='IN_FLIGHT'
    elif cut in {'after_save','cash'}:
        assert a.manager.state().blocking_intent.status=='SUBMITTED'
    if recompose:
        _recompose(x,desktop_case)
        x.c.set_connected(True);x.c.set_market_state('OPEN');a=x.c.execution_adapter
    x.advance()
    if cut!='authority':
        assert x.c.run_cycle().actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    final=a.manager.state().intents[-1]
    assert final.status=='RECONCILED' and final.intent_id==intent.intent_id
    assert sum(t.status=='SUBMITTED' for t in final.transitions)==1
    assert sum(t.status=='IN_FLIGHT' for t in final.transitions)==1
    assert final.cl7_locked_dispatch_proof==intent.cl7_locked_dispatch_proof
    assert x.p.order_calls==1 and a.cash_authority_manager.status().post_attempt_count==1
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert a.risk_runtime.state_store.load_account(ACCOUNT).daily_order_count==1
    assert len(json.loads(_export(x))['transactions'])==3
    assert _cash(_export(x))==998948641975321


@pytest.mark.parametrize('delay',['not_found','timeout','new','fee_unknown'])
def test_delayed_observation_never_causes_resubmission(exact_case,monkeypatch,delay):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;intent=_lost_ack(x,monkeypatch);raw,_=_fill(x,intent)
    correct=deepcopy(raw)
    if delay=='not_found':
        x.p.receipts[intent.intent_id]=TBankAPIError('PRIVATE',status_code=404,transient=False)
    elif delay=='timeout':x.p.receipts[intent.intent_id]=TimeoutError('PRIVATE')
    elif delay=='new':
        raw.update(lotsExecuted='0',stages=[],executionReportStatus='EXECUTION_REPORT_STATUS_NEW',executedCommission=money(0))
    else:del raw['executedCommission']
    for _ in range(2):
        with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
        assert x.p.order_calls==1
        assert x.c.execution_adapter.cash_authority_manager.status().post_attempt_count==1
        x.advance()
    x.p.receipts[intent.intent_id]=correct;x.advance()
    assert x.c.run_cycle().actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert x.p.order_calls==1
    assert sum(t.status=='SUBMITTED' for t in x.c.execution_adapter.manager.state().intents[-1].transitions)==1


def test_pending_marker_without_any_post_does_not_authorize_retry(exact_case,monkeypatch):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    class Interrupted(BaseException):pass
    x=exact_case;a=x.c.execution_adapter
    def stop(*args,**kwargs):raise Interrupted()
    with monkeypatch.context() as mp:
        mp.setattr(x.p,'post_order_once',stop)
        with pytest.raises(Interrupted):_desktop_cycle(x.c)
    intent=a.manager.state().blocking_intent
    assert intent.status=='IN_FLIGHT' and x.p.order_calls==0
    assert a.cash_authority_manager.status().post_attempt_count==1
    x.c.set_connected(True);x.c.set_market_state('OPEN');before=_state(x)
    for _ in range(2):
        with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
        assert _state(x)==before
        assert a.dispatch_next(x.manager.repository,expected_intent_id=intent.intent_id).status=='CL7_DISPATCH_PENDING'
    assert x.p.order_calls==0 and a.cash_authority_manager.status().post_attempt_count==1


def test_same_frozen_clock_keeps_snapshot_freshness_guard(exact_case,monkeypatch):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;intent=_lost_ack(x,monkeypatch);_fill(x,intent);x.p.on_portfolio=None
    with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    # Identity may commit; closure's strict newer-than-binding check is intact.
    assert x.c.execution_adapter.manager.state().blocking_intent.status=='SUBMITTED'
    assert x.c.execution_adapter.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    x.advance()
    assert x.c.run_cycle().actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert x.p.order_calls==1


def test_exchange_id_cannot_change_after_binding(exact_case,monkeypatch):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;intent=_lost_ack(x,monkeypatch);raw,_=_fill(x,intent);_bind(x)
    raw['orderId']='PRIVATE_OTHER_EXCHANGE';before=_state(x)
    with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    assert _state(x)==before and x.p.order_calls==1


@pytest.mark.parametrize('damage',['central','portfolio','risk','config','metadata'])
def test_concurrent_local_change_is_not_overwritten(exact_case,monkeypatch,damage):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;intent=_lost_ack(x,monkeypatch);_fill(x,intent);a=x.c.execution_adapter
    changed=[]
    def mutate():
        if changed:return
        if damage=='central':
            state=a.manager.state();a.manager.store._save_unlocked(replace(state,revision=state.revision+1))
        elif damage=='portfolio':
            from trading_robot.portfolio_model import PortfolioState
            state=x.manager.repository.load(expected_account_id=ACCOUNT)
            x.manager.repository.save(replace(state,revision=state.revision+1))
        elif damage=='risk':
            state=a.risk_runtime.state_store.load_account(ACCOUNT)
            a.risk_runtime.state_store.save_account(ACCOUNT,replace(state,daily_order_count=state.daily_order_count+1))
        elif damage=='config':
            r=x.c.runtime_store.load(expected_account_id=ACCOUNT)
            x.c.runtime_store.save(tuple(replace(item,status='STOPPED') for item in r))
        else:
            path=x.root/'portfolio_risk_metadata.json';path.write_bytes(path.read_bytes()+b' ')
        changed.append((x.manager.repository.path.read_bytes(), a.risk_runtime.state_store.path.read_bytes(),
                        a.manager.store.path.read_bytes(),
                        a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False).canonical_bytes,
                        _export(x)))
    x.p.on_state=mutate
    with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    assert changed and _state(x)==changed[0]
    assert a.manager.state().blocking_intent.status=='IN_FLIGHT' and x.p.order_calls==1


def test_binding_is_under_existing_locks_and_never_books_cash(exact_case,monkeypatch):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock, LockUnavailableError
    x=exact_case;intent=_lost_ack(x,monkeypatch);_fill(x,intent);a=x.c.execution_adapter
    save=a.manager.store._save_unlocked;calls=[]
    def checked(state):
        calls.append(True)
        for path in (a.manager.store.lock_path,x.manager.repository.lock_path,
                     a.risk_runtime.state_store.lock_path):
            with pytest.raises(LockUnavailableError):
                with InterProcessFileLock(path,timeout_seconds=0):pass
        con=sqlite3.connect(a.cl7_ledger_store.root/'store.sqlite3',timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError,match='locked'):con.execute('BEGIN IMMEDIATE')
        finally:con.close()
        return save(state)
    monkeypatch.setattr(a.manager.store,'_save_unlocked',checked)
    old=_state(x);_bind(x)
    assert calls==[True] and x.p.order_calls==1
    assert _state(x)[0:2]==old[0:2] and _state(x)[3:]==old[3:]


def test_own_audit_latency_cannot_expire_the_read_and_still_commit(exact_case,monkeypatch):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;intent=_lost_ack(x,monkeypatch);_fill(x,intent)
    original=x.manager.transaction_coordinator._record
    def delay(event,**kw):
        r=original(event,**kw)
        if event=='EXACT_INFLIGHT_IDENTITY_OBSERVED':x.p.clock_at+=timedelta(seconds=6)
        return r
    monkeypatch.setattr(x.manager.transaction_coordinator,'_record',delay)
    before=_state(x)
    with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    assert _state(x)==before and x.p.order_calls==1


def test_wrong_expected_proof_is_refused_before_broker_read(exact_case,monkeypatch):
    x=exact_case;intent=_lost_ack(x,monkeypatch);_fill(x,intent)
    before=_state(x);reads=list(x.p.read_calls)
    with pytest.raises(Exception,match='AUTHORITY_MISMATCH'):_bind(x,'0'*64)
    assert _state(x)==before and x.p.read_calls==reads


def test_full_roundtrip_with_lost_ack_on_both_requests(roundtrip_case,monkeypatch,request):
    x=roundtrip_case;a=x.c.execution_adapter
    buy=_lost_ack(x,monkeypatch,gui=True);assert buy.candidate.direction=='BUY'
    _fill(x,buy);counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    assert x.c.run_cycle().actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    _next_hour(x);x.c.run_cycle();assert x.c.latest_cycle_outcomes()[UID].action=='HOLD'
    x.advance()
    a.cash_authority_manager.arm(raw_account_id=ACCOUNT,identity_key=a.cl7_identity_key,
        identity_key_id=a.cl7_identity_key_id,confirmation=a.cash_authority_manager.ARM_PHRASE,
        transition_at=a.cl7_clock())
    _next_hour(x);x.signal=0
    sell=_lost_ack(x,monkeypatch,gui=True);assert sell.candidate.direction=='SELL'
    _fill(x,sell);counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    assert x.c.run_cycle().actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    _next_hour(x);x.c.run_cycle();assert x.c.latest_cycle_outcomes()[UID].action=='HOLD'
    assert buy.intent_id!=sell.intent_id and x.p.order_calls==2
    p=x.manager.repository.load(expected_account_id=ACCOUNT).position(UID)
    assert p.actual_lots==p.target_lots==0 and p.ownership is None
    assert all([t.status for t in i.transitions]==['QUEUED','IN_FLIGHT','SUBMITTED','RECONCILED']
               for i in a.manager.state().intents)
    assert a.risk_runtime.state_store.load_account(ACCOUNT).daily_order_count==2
    assert _cash(_export(x))==999999753086422 and len(json.loads(_export(x))['transactions'])==5
    authority=a.cash_authority_manager.status()
    assert authority.post_attempt_count==2 and authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    request.node.user_properties.extend([('fake_posts',x.p.order_calls),('attempt_count',2),
        ('risk_executions',2),('cash_nano',str(_cash(_export(x)))),('lost_acks',2)])
