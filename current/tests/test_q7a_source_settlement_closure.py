from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest

from test_q7a_source_cash_components import _input, _record, _export, _cash
from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case
from test_q7a_source_provider_refresh import refresh_case, position
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState


def _ready(x):
    intent, raw, trade = _input(x)
    x.p.payload['positions'] = [position(intent.candidate.target_lots)] if intent.candidate.target_lots else []
    cash = _record(x)
    return intent, raw, trade, cash


def _close(x, proof):
    method = getattr(x.c.execution_adapter, 'finalize_exact_settlement', None)
    assert callable(method), 'STEP15 exact owner closure is missing'
    return method(recovery=x.c.cycle_source.portfolio_recovery, proof_sha256=proof)


def test_full_buy_closes_and_disarms_without_duplicate(exact_case, request):
    x=exact_case
    intent, raw, trade, cash=_ready(x)
    a=x.c.execution_adapter
    before=a.cash_authority_manager.status()
    export=_export(x)
    result=_close(x,cash.proof_sha256)
    final=a.manager.state().intents[-1]
    p=x.manager.repository.load(expected_account_id=ACCOUNT)
    risk=a.risk_runtime.state_store.load_account(ACCOUNT)
    authority=a.cash_authority_manager.status()
    assert final.status=='RECONCILED' and final.outcome=='FILLED'
    assert final.risk_execution_id==intent.intent_id and final.executed_lots==1
    assert p.position(UID).actual_lots==p.position(UID).target_lots==1
    assert p.position(UID).ownership.strategy_id==intent.candidate.strategy_id
    assert list(risk.recorded_execution_ids).count(intent.intent_id)==1
    assert authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert authority.post_attempt_count==before.post_attempt_count==1
    assert authority.ledger_head_sha256==cash.ledger_head_sha256
    assert authority.ledger_revision==cash.ledger_revision
    assert authority.operations_complete_through>before.operations_complete_through
    assert authority.pending_dispatch_proof_sha256 is None
    assert _export(x)==export and x.p.order_calls==1
    snapshots=(p.to_dict(),risk.to_dict(),a.manager.state().to_dict(),authority.canonical_bytes)
    x.advance()
    again=_close(x,cash.proof_sha256)
    assert again.replay and again.closure_plan_sha256==result.closure_plan_sha256
    assert snapshots==(x.manager.repository.load(expected_account_id=ACCOUNT).to_dict(),
        a.risk_runtime.state_store.load_account(ACCOUNT).to_dict(),a.manager.state().to_dict(),
        a.cash_authority_manager.status().canonical_bytes)
    assert _export(x)==export and x.p.order_calls==1
    request.node.user_properties.extend([('posts',1),('ledger_revision',authority.ledger_revision),
       ('risk_count',risk.daily_order_count),('authority_state',authority.state.value),('cash_nano',str(_cash(export)))])


def _snapshot(x):
    from trading_robot.exact_settlement_closure import _owners
    return _owners(x.c.execution_adapter,x.c.cycle_source.portfolio_recovery),_export(x),x.p.order_calls


def _plan(x):
    paths=list((x.c.execution_adapter.manager.store.path.parent/'exact_settlement_closure').glob('*.json'))
    assert len(paths)==1
    return paths[0]


def _recompose(x, desktop_case):
    import desktop_gui
    old=x.c.execution_adapter
    clock,mono,wait=old.cl7_clock,old.cl7_monotonic_ns,old.cl7_wait_ns
    old.cl7_ledger_store.close()
    desktop_gui._PRODUCTION_COMPOSITION=None
    x.c=desktop_case.compose()
    x.manager=x.c.cycle_source.portfolio_recovery.manager
    a=x.c.execution_adapter
    a.cl7_clock,a.cl7_monotonic_ns,a.cl7_wait_ns=clock,mono,wait


def test_no_components_means_no_owner_write(exact_case):
    from trading_robot.exact_settlement_closure import ExactSettlementClosureError
    x=exact_case
    intent,_,_=_input(x)
    x.p.payload['positions']=[position()]
    before=_snapshot(x)
    with pytest.raises(ExactSettlementClosureError):_close(x,intent.cl7_locked_dispatch_proof_sha256)
    assert _snapshot(x)==before
    assert not list((x.root/'exact_settlement_closure').glob('*.json'))


def test_independent_sell_closes_flat_with_credit_and_fee(exact_case):
    from test_q7a_source_provider_refresh import stage,money
    x=exact_case
    x.p.payload['positions']=[position()]
    x.p.payload['totalAmountShares']=money(1050)
    x.p.payload['totalAmountPortfolio']=money(1_001_050)
    stage(x,1,'synthetic-initial-owned-long')
    x.refresh();x.p.target=0
    intent,raw,trade,cash=_ready(x)
    assert intent.candidate.direction=='SELL'
    _close(x,cash.proof_sha256)
    p=x.manager.repository.load(expected_account_id=ACCOUNT).position(UID)
    assert p.actual_lots==p.target_lots==0 and p.ownership is None
    assert _cash(_export(x))==1_001_051_111_111_101
    assert x.c.execution_adapter.manager.state().blocking_intent is None
    assert x.p.order_calls==1


def test_zero_fee_closes_without_fabricated_cash_transaction(exact_case):
    x=exact_case
    intent,_,_=_input(x,fee='0')
    x.p.payload['positions']=[position()]
    cash=_record(x)
    result=_close(x,cash.proof_sha256)
    assert result.to_canonical_dict()['bounded_settlement_verified']
    assert len(json.loads(_export(x))['transactions'])==2
    assert x.p.order_calls==1


@pytest.mark.parametrize('damage',[
    'cash_plan_missing','cash_plan_tampered','receipt_missing_fee','receipt_missing_service',
    'receipt_changed','receipt_exchange','operation_alias','missing_fee_operation','new_external_operation',
    'broker_cash_changed','broker_blocked','wrong_position','foreign_holding','open_order',
    'missing_risk_audit','already_risk_recorded','different_day','wrong_proof',
    'wrong_recovery','wrong_transport','unknown_state','unkeyed_receipt_result',
])
def test_bad_evidence_cannot_complete_or_clear(exact_case,damage):
    from test_q7a_source_provider_refresh import money,order
    from trading_robot.exact_settlement_closure import ExactSettlementClosureError
    from trading_robot.exact_cash_settlement import _PlanStore
    x=exact_case;intent,raw,trade,cash=_ready(x);a=x.c.execution_adapter
    proof=cash.proof_sha256
    if damage=='cash_plan_missing':_PlanStore(a.manager.store.path.parent,proof,a.cl7_identity_key).path.unlink()
    elif damage=='cash_plan_tampered':
        path=_PlanStore(a.manager.store.path.parent,proof,a.cl7_identity_key).path
        doc=json.loads(path.read_bytes());doc['payload']['match']['cash_delta_nano']='0';path.write_text(json.dumps(doc))
    elif damage=='receipt_missing_fee':raw.pop('executedCommission')
    elif damage=='receipt_missing_service':raw.pop('serviceCommission')
    elif damage=='receipt_changed':raw['stages'][0]['price']=raw['averagePositionPrice']=money(104)
    elif damage=='receipt_exchange':raw['orderId']='OTHER_EXCHANGE'
    elif damage=='operation_alias':trade['id']='OTHER_OPERATION';x.operations[1]['parentOperationId']=trade['id']
    elif damage=='missing_fee_operation':x.operations.pop()
    elif damage=='new_external_operation':x.operations.append(deepcopy(trade))
    elif damage=='broker_cash_changed':x.p.wallet_rub-=1
    elif damage=='broker_blocked':x.p.cash_blocked_rub=1
    elif damage=='wrong_position':x.p.payload['positions']=[]
    elif damage=='foreign_holding':
        p=position();p['instrumentUid']='unknown-instrument';x.p.payload['positions'].append(p)
    elif damage=='open_order':x.p.orders=[order()]
    elif damage=='missing_risk_audit':x.manager.transaction_coordinator.journal=None
    elif damage=='already_risk_recorded':
        store=a.risk_runtime.state_store;state=store.load_account(ACCOUNT)
        store.save_account(ACCOUNT,replace(state,recorded_execution_ids=(intent.intent_id,)))
    elif damage=='different_day':x.p.clock_at+=timedelta(days=1)
    elif damage=='wrong_proof':proof='a'*64
    elif damage=='wrong_recovery':x.c.cycle_source.portfolio_recovery=None
    elif damage=='wrong_transport':a.transport=object()
    elif damage=='unknown_state':
        a.manager.mark_uncertain(intent.intent_id,reason='EXTERNAL_CUSTODY_CHANGE')
        # Even if an uncertainty transition is legitimate, a receipt known under
        # a future Central timestamp must not use an earlier portfolio snapshot.
        x.p.clock_at-=timedelta(seconds=10)
    elif damage=='unkeyed_receipt_result':
        raw.clear();raw.update(cash.to_canonical_dict())
    before=(_export(x),a.manager.state().to_dict(),a.risk_runtime.state_store.load_account(ACCOUNT).to_dict(),
            a.cash_authority_manager.status().canonical_bytes,x.manager.repository.path.read_bytes())
    with pytest.raises(ExactSettlementClosureError):_close(x,proof)
    after=(_export(x),a.manager.state().to_dict(),a.risk_runtime.state_store.load_account(ACCOUNT).to_dict(),
           a.cash_authority_manager.status().canonical_bytes,x.manager.repository.path.read_bytes())
    assert before==after and x.p.order_calls==1


@pytest.mark.parametrize('cut',['plan','portfolio','risk','central','audit','authority'])
@pytest.mark.parametrize('recompose',[False,True])
def test_committed_owner_prefix_recovers_without_reposting(exact_case,desktop_case,monkeypatch,cut,recompose):
    from trading_robot import exact_settlement_closure as mod
    x=exact_case;intent,raw,trade,cash=_ready(x);a=x.c.execution_adapter
    original_ledger=_export(x);fired=[]
    with monkeypatch.context() as mp:
        if cut=='plan':target,name=mod._ClosureStore,'create'
        elif cut=='portfolio':target,name=x.manager.transaction_coordinator,'commit'
        elif cut=='risk':target,name=a.risk_runtime.state_store,'save_account_while_locked'
        elif cut=='central':target,name=a.manager.store,'_save_unlocked'
        elif cut=='audit':target,name=x.manager.transaction_coordinator,'_record'
        else:target,name=a.cash_authority_manager.store,'_commit_unlocked'
        fn=getattr(target,name)
        def interrupted(*args,**kwargs):
            result=fn(*args,**kwargs)
            wanted=cut!='audit' or args[0]=='EXACT_SETTLEMENT_ACCOUNTED'
            if wanted and not fired:fired.append(True);raise RuntimeError('SYNTHETIC_AFTER_COMMIT')
            return result
        mp.setattr(target,name,interrupted)
        with pytest.raises(mod.ExactSettlementClosureError):_close(x,cash.proof_sha256)
    assert fired and _export(x)==original_ledger and x.p.order_calls==1
    assert a.cash_authority_manager.status().state is (
        RuntimeCashAuthorityState.EXACT_CASH_DISARMED if cut=='authority'
        else RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING)
    if recompose:_recompose(x,desktop_case)
    result=_close(x,cash.proof_sha256)
    a=x.c.execution_adapter
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert len(a.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids)==1
    assert a.manager.state().blocking_intent is None and x.p.order_calls==1 and _export(x)==original_ledger
    saved=_snapshot(x)
    assert _close(x,cash.proof_sha256).replay
    assert _snapshot(x)==saved


@pytest.mark.parametrize('owner',['portfolio','risk','central','authority'])
def test_retry_rejects_foreign_owner_changes(exact_case,monkeypatch,owner):
    from trading_robot import exact_settlement_closure as mod
    x=exact_case;intent,raw,trade,cash=_ready(x);a=x.c.execution_adapter
    with monkeypatch.context() as mp:
        orig=mod._ClosureStore.create
        def stop(self,body):orig(self,body);raise RuntimeError('AFTER_PLAN')
        mp.setattr(mod._ClosureStore,'create',stop)
        with pytest.raises(mod.ExactSettlementClosureError):_close(x,cash.proof_sha256)
    if owner=='portfolio':
        p=x.manager.repository.load(expected_account_id=ACCOUNT)
        x.manager.repository.save(replace(p,last_transaction_id='foreign',last_transaction_status='COMMITTED'),
                                  expected_revision=p.revision,allow_equal_revision=True)
    elif owner=='risk':
        store=a.risk_runtime.state_store;r=store.load_account(ACCOUNT)
        store.save_account(ACCOUNT,replace(r,daily_order_count=r.daily_order_count+1))
    elif owner=='central':a.manager.mark_uncertain(intent.intent_id,reason='FOREIGN_UPDATE')
    else:
        # A valid schema/checksum for an unexpected record is still not a prefix.
        path=a.cash_authority_manager.store.path
        data=path.read_bytes();path.write_bytes(data+b' ')
    frozen=(x.manager.repository.path.read_bytes(),a.manager.store.path.read_bytes(),
            a.risk_runtime.state_store.path.read_bytes(),a.cash_authority_manager.store.path.read_bytes(),_export(x))
    with pytest.raises(mod.ExactSettlementClosureError):_close(x,cash.proof_sha256)
    assert frozen==(x.manager.repository.path.read_bytes(),a.manager.store.path.read_bytes(),
            a.risk_runtime.state_store.path.read_bytes(),a.cash_authority_manager.store.path.read_bytes(),_export(x))
    assert x.p.order_calls==1


@pytest.mark.parametrize('damage',['hmac','delete','duplicate_key','symlink'])
def test_checkpoint_damage_never_silently_repairs(exact_case,monkeypatch,damage,tmp_path):
    from trading_robot import exact_settlement_closure as mod
    x=exact_case;_,_,_,cash=_ready(x);a=x.c.execution_adapter
    with monkeypatch.context() as mp:
        orig=a.risk_runtime.state_store.save_account_while_locked
        def stop(*args,**kwargs):orig(*args,**kwargs);raise RuntimeError('AFTER_RISK')
        mp.setattr(a.risk_runtime.state_store,'save_account_while_locked',stop)
        with pytest.raises(mod.ExactSettlementClosureError):_close(x,cash.proof_sha256)
    path=_plan(x)
    if damage=='hmac':
        doc=json.loads(path.read_bytes());doc['hmac_sha256']='0'*64;path.write_text(json.dumps(doc))
    elif damage=='delete':path.unlink()
    elif damage=='duplicate_key':path.write_text('{"payload":{},"payload":{},"hmac_sha256":"x"}')
    else:
        data=path.read_bytes();path.unlink();outside=tmp_path/'elsewhere.json';outside.write_bytes(data);path.symlink_to(outside)
    before=_snapshot(x)
    with pytest.raises(mod.ExactSettlementClosureError):_close(x,cash.proof_sha256)
    assert _snapshot(x)==before and x.p.order_calls==1


@pytest.mark.parametrize('stage',['state','portfolio','cash'])
def test_concurrent_risk_change_during_provider_read_blocks(exact_case,stage):
    from trading_robot.exact_settlement_closure import ExactSettlementClosureError
    x=exact_case;_,_,_,cash=_ready(x);a=x.c.execution_adapter
    def mutate(*_):
        store=a.risk_runtime.state_store;r=store.load_account(ACCOUNT)
        store.save_account(ACCOUNT,replace(r,daily_order_count=r.daily_order_count+1))
    setattr(x.p,{'state':'on_state','portfolio':'on_portfolio','cash':'on_cash_positions'}[stage],mutate)
    p=x.manager.repository.path.read_bytes();c=a.manager.store.path.read_bytes();auth=a.cash_authority_manager.status().canonical_bytes
    with pytest.raises(ExactSettlementClosureError):_close(x,cash.proof_sha256)
    assert x.manager.repository.path.read_bytes()==p and a.manager.store.path.read_bytes()==c
    assert a.cash_authority_manager.status().canonical_bytes==auth and x.p.order_calls==1


def test_generic_completion_and_dispatch_remain_closed(exact_case):
    x=exact_case;intent,_,_,cash=_ready(x);a=x.c.execution_adapter
    with pytest.raises(Exception,match='EXACT_SETTLEMENT_REQUIRED'):
        a.manager.mark_reconciled(intent.intent_id,portfolio_repository=x.manager.repository,
            outcome='FILLED',executed_lots=1,risk_runtime=a.risk_runtime,execution_price_rub=105)
    _close(x,cash.proof_sha256)
    before=_snapshot(x)
    result=a.dispatch_next(x.manager.repository)
    assert not result.order_was_sent and x.p.order_calls==1
    assert _snapshot(x)==before


def test_public_result_has_no_raw_identifiers(exact_case):
    x=exact_case;intent,raw,trade,cash=_ready(x)
    result=_close(x,cash.proof_sha256).to_canonical_dict()
    text=json.dumps(result)
    for token in [intent.intent_id,intent.broker_order_id,ACCOUNT,UID,trade['id'],raw['stages'][0]['tradeId']]:
        assert token not in text
    assert result['automatic_rearm_allowed'] is False and result['future_fee_finality_claimed'] is False


@pytest.mark.parametrize('cut',['canonical_save','shadow','canonical_journal'])
def test_portfolio_internal_postcommit_cuts_preserve_recovery(exact_case,monkeypatch,cut):
    from trading_robot import exact_settlement_closure as mod
    x=exact_case;_,_,_,cash=_ready(x);a=x.c.execution_adapter;fired=[]
    with monkeypatch.context() as mp:
        if cut=='canonical_save':target,name=x.manager.repository,'save'
        elif cut=='shadow':target,name=x.manager.transaction_coordinator.shadow_writer,'write'
        else:target,name=x.manager.transaction_coordinator,'_record'
        fn=getattr(target,name)
        def fail(*args,**kwargs):
            result=fn(*args,**kwargs)
            selected=cut!='canonical_journal' or args[0]=='CANONICAL_TRANSACTION_COMMITTED'
            if selected and not fired:fired.append(1);raise RuntimeError('AFTER_CANONICAL_WRITE')
            return result
        mp.setattr(target,name,fail)
        with pytest.raises(mod.ExactSettlementClosureError):_close(x,cash.proof_sha256)
    assert fired and a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    _close(x,cash.proof_sha256)
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert len(a.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids)==1
    assert x.p.order_calls==1


@pytest.mark.parametrize('mode',['wall_expired','wall_backwards','mono_expired','mono_backwards'])
def test_clock_failures_never_clear_or_write_owners(exact_case,mode):
    from trading_robot.exact_settlement_closure import ExactSettlementClosureError
    x=exact_case;_,_,_,cash=_ready(x);a=x.c.execution_adapter
    ticks=[10];a.cl7_monotonic_ns=lambda:ticks[0]
    def change():
        if mode=='wall_expired':x.p.clock_at+=timedelta(seconds=6)
        elif mode=='wall_backwards':x.p.clock_at-=timedelta(seconds=1)
        elif mode=='mono_expired':ticks[0]+=6_000_000_000
        else:ticks[0]-=1
    x.p.on_state=change
    before=_snapshot(x)
    with pytest.raises(ExactSettlementClosureError):_close(x,cash.proof_sha256)
    assert _snapshot(x)==before


def test_final_owner_writes_hold_real_locks(exact_case,monkeypatch):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock,LockUnavailableError
    x=exact_case;_,_,_,cash=_ready(x);a=x.c.execution_adapter;r=x.c.cycle_source.portfolio_recovery
    observed=[]
    def assert_locks(label):
        con=sqlite3.connect(a.cl7_ledger_store.root/'store.sqlite3',timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError,match='locked'):con.execute('BEGIN IMMEDIATE')
        finally:con.close()
        for path in [r.manager.repository.lock_path,r.risk.profile_store.lock_path,r.risk.state_store.lock_path,
                     a.manager.store.lock_path,a.cash_authority_manager.store.lock_path,r.profiles.lock_path,r.runtimes.lock_path]:
            with pytest.raises(LockUnavailableError):
                with InterProcessFileLock(path,timeout_seconds=0):pass
        observed.append(label)
    for target,name,label in [(a.risk_runtime.state_store,'save_account_while_locked','risk'),
                              (a.manager.store,'_save_unlocked','central'),
                              (a.cash_authority_manager.store,'_commit_unlocked','authority')]:
        fn=getattr(target,name)
        def wrapped(*args,_fn=fn,_label=label,**kwargs):
            assert_locks(_label);return _fn(*args,**kwargs)
        monkeypatch.setattr(target,name,wrapped)
    _close(x,cash.proof_sha256)
    assert observed==['risk','central','authority']


def test_risk_engine_accounting_preserves_halt(exact_case):
    x=exact_case;_,_,_,cash=_ready(x);a=x.c.execution_adapter
    state=a.risk_runtime.state_store.load_account(ACCOUNT)
    a.risk_runtime.state_store.save_account(ACCOUNT,replace(state,kill_switch_active=True,kill_switch_reason='OPERATOR'))
    _close(x,cash.proof_sha256)
    result=a.risk_runtime.state_store.load_account(ACCOUNT)
    assert result.kill_switch_active and result.kill_switch_reason=='OPERATOR'
    assert result.daily_order_count==state.daily_order_count+1
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED


def test_same_intent_uncertain_full_receipt_can_close_without_new_post(exact_case):
    x=exact_case;intent,_,_,cash=_ready(x)
    x.c.execution_adapter.manager.mark_uncertain(intent.intent_id,reason='TRANSPORT_OUTCOME_UNKNOWN')
    x.advance()
    _close(x,cash.proof_sha256)
    assert x.p.order_calls==1 and x.c.execution_adapter.manager.state().blocking_intent is None


def test_paused_configuration_blocks_financial_closure(exact_case):
    from trading_robot.exact_settlement_closure import ExactSettlementClosureError
    x=exact_case;_,_,_,cash=_ready(x)
    store=x.c.runtime_store
    runtimes=store.load(expected_account_id=ACCOUNT)
    # Use the production group writer; never alter an in-memory status only.
    store.save(tuple(runtime.stop() for runtime in runtimes))
    before=_snapshot(x)
    with pytest.raises(ExactSettlementClosureError):_close(x,cash.proof_sha256)
    assert _snapshot(x)==before


@pytest.mark.parametrize('damage', ['attempt_count', 'ledger_revision', 'ledger_head', 'watermark', 'opening', 'rearm'])
def test_new_transition_cannot_rearm_or_rewrite_history(exact_case, damage):
    from trading_robot.runtime_cash_authority import CL7RuntimeError, _transition_pair
    x = exact_case
    _, _, _, cash = _ready(x)
    a = x.c.execution_adapter
    before = a.cash_authority_manager.status()
    _close(x, cash.proof_sha256)
    done = a.cash_authority_manager.status()
    values = {
        'attempt_count': {'post_attempt_count': 0},
        'ledger_revision': {'ledger_revision': before.ledger_revision},
        'ledger_head': {'ledger_head_sha256': before.ledger_head_sha256},
        'watermark': {'operations_complete_through': before.operations_complete_through},
        'opening': {'opening_record_sha256': 'a' * 64},
        'rearm': {'state': RuntimeCashAuthorityState.EXACT_CASH_ARMED},
    }
    with pytest.raises(CL7RuntimeError):
        _transition_pair(before, replace(done, **values[damage]))
    assert a.cash_authority_manager.status() == done and x.p.order_calls == 1


def test_audit_unavailable_after_risk_cannot_clear(exact_case, monkeypatch):
    from trading_robot import exact_settlement_closure as mod
    x = exact_case
    _, _, _, cash = _ready(x)
    a = x.c.execution_adapter
    writer = x.manager.transaction_coordinator
    original = writer._record
    with monkeypatch.context() as mp:
        def fail(event, **kwargs):
            if event == 'EXACT_SETTLEMENT_ACCOUNTED':
                raise OSError('SYNTHETIC_AUDIT_UNAVAILABLE')
            return original(event, **kwargs)
        mp.setattr(writer, '_record', fail)
        with pytest.raises(mod.ExactSettlementClosureError):
            _close(x, cash.proof_sha256)
    assert a.manager.state().intents[-1].status == 'RECONCILED'
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    assert len(a.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids) == 1
    _close(x, cash.proof_sha256)
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert x.p.order_calls == 1
