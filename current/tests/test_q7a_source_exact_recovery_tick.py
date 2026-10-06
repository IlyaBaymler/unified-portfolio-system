"""STEP16: real GUI recovery orchestration, synthetic broker and local stores."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import sqlite3

import pytest

from test_q7a_source_cash_components import _input, _export, _cash
from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_exact_receipt import _raw
from test_q7a_source_provider_refresh import refresh_case, position, money
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState, LockedDispatchProof


def _prepare(x):
    intent, raw, trade = _input(x)
    x.p.payload['positions'] = [position()]
    x.c.set_connected(True)
    x.c.set_market_state('OPEN')
    return intent, raw, trade


def _state(x):
    a=x.c.execution_adapter
    return (x.manager.repository.path.read_bytes(), a.risk_runtime.state_store.path.read_bytes(),
            a.manager.store.path.read_bytes(), a.cash_authority_manager.status().canonical_bytes,
            _export(x))


def test_gui_full_fill_tick_records_cash_and_closes_without_candles(exact_case):
    x=exact_case; intent,raw,trade=_prepare(x)
    counts=(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    result=x.c.run_cycle()
    assert len(result.actions)==1
    assert result.actions[0].action=='EXACT_SETTLEMENT_RECOVERY'
    assert result.actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    a=x.c.execution_adapter
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert a.manager.state().blocking_intent is None
    assert a.manager.state().intents[-1].risk_execution_id==intent.intent_id
    assert a.risk_runtime.state_store.load_account(ACCOUNT).daily_order_count==1
    assert _cash(_export(x))==998948641975321
    assert len(json.loads(_export(x))['transactions'])==3
    assert (x.p.candle_calls,x.p.quote_calls,x.p.order_calls)==counts
    p=x.manager.repository.load(expected_account_id=ACCOUNT)
    assert p.position(UID).actual_lots==p.position(UID).target_lots==1


@pytest.fixture
def roundtrip_case(exact_case, monkeypatch):
    """Same real factory, but broker fixtures can issue two distinct orders."""
    x=exact_case
    x.signal=1
    original_candles=x.p.get_candles
    def candles(instrument_id,*args,**kwargs):
        old=x.p.target
        x.p.target=x.signal if instrument_id==UID else 0
        try:
            return original_candles(instrument_id,*args,**kwargs)
        finally:
            x.p.target=old
    def post(*args,**kwargs):
        a=x.c.execution_adapter
        authority=a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        intent=a.manager.store._load_unlocked(expected_account_id=ACCOUNT).blocking_intent
        assert authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
        assert intent.status=='IN_FLIGHT'
        assert kwargs['order_id']==intent.intent_id
        assert authority.post_attempt_count==x.p.order_calls+1
        proof=LockedDispatchProof.from_canonical_dict(intent.cl7_locked_dispatch_proof)
        proof.verify_identity(raw_intent_id=intent.intent_id,identity_key=a.cl7_identity_key)
        assert proof.sha256==authority.pending_dispatch_proof_sha256
        con=sqlite3.connect(a.cl7_ledger_store.root/'store.sqlite3',timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError,match='locked'):
                con.execute('BEGIN IMMEDIATE')
        finally:
            con.close()
        return x.p.post_order(*args,**kwargs)
    monkeypatch.setattr(x.p,'get_candles',candles)
    from trading_robot.multi_instrument_strategy import StrategyCandleLoader
    x.c.cycle_source.candle_loader=StrategyCandleLoader(x.p)
    monkeypatch.setattr(x.p,'post_order_once',post)
    x.c.set_connected(True)
    x.c.set_market_state('OPEN')
    return x


def _next_hour(x):
    x.p.clock_at+=timedelta(hours=1)
    x.advance()


def _fill_existing(x,intent):
    x.advance()
    fee=Decimal('0.123456789')
    price=Decimal('105.123456789')
    raw=_raw(x,intent,price=str(price),commission=str(fee))
    ident=f'SYNTHETIC_{intent.candidate.direction}_{x.p.order_calls}'
    raw['stages'][0]['tradeId']=ident+'_TRADE'
    qty=intent.candidate.requested_lots*intent.candidate.lot_size
    gross=price*qty
    direction=intent.candidate.direction
    trade={'id':ident+'_OP','brokerAccountId':ACCOUNT,'cursor':ident+'_CURSOR',
        'date':stamp(x.p.clock_at),'type':'OPERATION_TYPE_'+direction,
        'state':'OPERATION_STATE_EXECUTED','quantity':str(qty),'quantityDone':str(qty),'quantityRest':'0',
        'payment':money(-gross if direction=='BUY' else gross),'commission':money(fee),
        'childOperations':[],'instrumentUid':UID,'instrumentType':'share',
        'tradesInfo':{'trades':[{'num':raw['stages'][0]['tradeId'],'quantity':str(qty),
                               'price':money(price),'date':stamp(x.p.clock_at)}]}}
    commission={'id':ident+'_FEE','brokerAccountId':ACCOUNT,'cursor':ident+'_FEE_CURSOR',
        'date':stamp(x.p.clock_at),'type':'OPERATION_TYPE_BROKER_FEE',
        'state':'OPERATION_STATE_EXECUTED','quantity':'0','quantityDone':'0','quantityRest':'0',
        'payment':money(-fee),'commission':money(0),'childOperations':[],
        'parentOperationId':trade['id'],'instrumentUid':UID}
    x.operations.extend([trade,commission])
    x.p.wallet_rub=Decimal(str(x.p.wallet_rub))+(-gross if direction=='BUY' else gross)-fee
    lots=intent.candidate.target_lots
    x.p.payload['positions']=[position(lots)] if lots else []
    x.p.payload['totalAmountCurrencies']=money(x.p.wallet_rub)
    x.p.payload['totalAmountShares']=money(1050*lots)
    x.p.payload['totalAmountPortfolio']=money(x.p.wallet_rub+1050*lots)
    x.advance()
    return raw,trade


def test_continuous_real_gui_buy_hold_sell_requires_explicit_rearm(roundtrip_case, request):
    x=roundtrip_case;a=x.c.execution_adapter
    first=x.c.run_cycle()
    intent=a.manager.state().blocking_intent
    assert intent is not None,[(r.action,r.status,r.detail) for r in first.actions]
    assert intent.candidate.direction=='BUY' and intent.status=='SUBMITTED'
    _fill_existing(x,intent)
    counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    result=x.c.run_cycle()
    assert result.actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    # HOLD while disarmed cannot submit. It is a fresh strategy/canonical pass.
    _next_hour(x)
    hold=x.c.run_cycle()
    assert x.p.order_calls==1
    assert x.c.latest_cycle_outcomes()[UID].action=='HOLD'
    assert a.manager.state().blocking_intent is None
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    # Only the explicit operator arm permits the next order. No state rewriting.
    x.advance()
    a.cash_authority_manager.arm(raw_account_id=ACCOUNT,identity_key=a.cl7_identity_key,
        identity_key_id=a.cl7_identity_key_id,confirmation=a.cash_authority_manager.ARM_PHRASE,
        transition_at=a.cl7_clock())
    _next_hour(x);x.signal=0
    sell=x.c.run_cycle()
    second=a.manager.state().blocking_intent
    assert second is not None,[(r.action,r.status,r.detail) for r in sell.actions]
    assert second.candidate.direction=='SELL' and second.status=='SUBMITTED'
    assert second.intent_id!=intent.intent_id and x.p.order_calls==2
    _fill_existing(x,second)
    counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    result=x.c.run_cycle()
    assert result.actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    _next_hour(x)
    x.c.run_cycle()
    assert x.c.latest_cycle_outcomes()[UID].action=='HOLD'
    assert x.p.order_calls==2 and len(set(x.p.sent_ids))==2
    p=x.manager.repository.load(expected_account_id=ACCOUNT).position(UID)
    assert p.actual_lots==p.target_lots==0 and p.ownership is None
    state=a.manager.state()
    assert len(state.intents)==2 and all(i.status=='RECONCILED' for i in state.intents)
    r=a.risk_runtime.state_store.load_account(ACCOUNT)
    assert r.daily_order_count==2 and len(r.recorded_execution_ids)==2
    authority=a.cash_authority_manager.status()
    assert authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert authority.post_attempt_count==2 and authority.pending_dispatch_proof_sha256 is None
    assert len(json.loads(_export(x))['transactions'])==5
    assert _cash(_export(x))==999999753086422
    request.node.user_properties.extend([('fake_posts',x.p.order_calls),
        ('distinct_intents',len(set(x.p.sent_ids))),('risk_execution_count',r.daily_order_count),
        ('ledger_cash_nano',str(_cash(_export(x)))),('ledger_revision',authority.ledger_revision),
        ('authority_state',authority.state.value),('attempt_count',authority.post_attempt_count)])


@pytest.mark.parametrize('surface',['run_cycle','service_tick','cli'])
def test_each_surface_runs_one_recovery_tick_only(exact_case,surface,capsys):
    x=exact_case;intent,_,_=_prepare(x)
    counts=(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    if surface=='run_cycle':
        assert x.c.run_cycle().actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    elif surface=='service_tick':
        result=x.c.service_tick(now=x.p.clock_at,latest_closed_candles={},hooks=object())
        assert result.actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    else:
        from tools.v3_10_exact_settlement_recover import main
        assert main(['--runtime-dir',str(x.root),'--execution-order-type','MARKET'])==0
        payload=json.loads(capsys.readouterr().out)
        assert payload['status']=='EXACT_SETTLEMENT_CLOSED_DISARMED'
        assert payload['provider_post_calls']==0 and payload['automatic_rearm_allowed'] is False
        text=json.dumps(payload)
        for private in (ACCOUNT,intent.intent_id,intent.broker_order_id,'105.123456789'):
            assert private not in text
        assert main(['--runtime-dir',str(x.root),'--execution-order-type','MARKET'])==2
        assert json.loads(capsys.readouterr().out)['status']=='EXACT_RECOVERY_CLI_BLOCKED'
    assert counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    assert len(json.loads(_export(x))['transactions'])==3
    assert x.c.execution_adapter.risk_runtime.state_store.load_account(ACCOUNT).daily_order_count==1


@pytest.mark.parametrize('cut',['cash','plan','portfolio','risk','central','audit','authority'])
@pytest.mark.parametrize('recompose',[False,True])
def test_gui_resumes_committed_prefix_including_central_without_blocker(
    exact_case,desktop_case,monkeypatch,cut,recompose,
):
    from trading_robot import exact_settlement_closure as closure
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError,run_exact_recovery_tick
    from test_q7a_source_settlement_closure import _recompose
    x=exact_case;intent,_,_=_prepare(x);a=x.c.execution_adapter
    counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    fired=[]
    with monkeypatch.context() as mp:
        if cut=='cash': target,name=a.cl7_ledger_store,'append_transaction'
        elif cut=='plan':target,name=closure._ClosureStore,'create'
        elif cut=='portfolio':target,name=x.manager.transaction_coordinator,'commit'
        elif cut=='risk':target,name=a.risk_runtime.state_store,'save_account_while_locked'
        elif cut=='central':target,name=a.manager.store,'_save_unlocked'
        elif cut=='audit':target,name=x.manager.transaction_coordinator,'_record'
        else:target,name=a.cash_authority_manager.store,'_commit_unlocked'
        fn=getattr(target,name)
        def interrupted(*args,**kwargs):
            value=fn(*args,**kwargs)
            match=cut!='audit' or args[0]=='EXACT_SETTLEMENT_ACCOUNTED'
            if match and not fired:
                fired.append(True);raise RuntimeError('SYNTHETIC_PRIVATE_AFTER_COMMIT')
            return value
        mp.setattr(target,name,interrupted)
        with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    assert fired and counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    if cut in {'central','audit'}:
        assert a.manager.state().blocking_intent is None
        assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
    if recompose:_recompose(x,desktop_case)
    a=x.c.execution_adapter
    if cut=='authority':
        # Committed final state is already DISARMED, despite the thrown error.
        # The dispatcher must not reinterpret it as a fresh recovery or POST.
        before=_state(x)
        with pytest.raises(ExactRecoveryTickError,match='NOT_PENDING'):
            run_exact_recovery_tick(a,recovery=x.c.cycle_source.portfolio_recovery)
        assert _state(x)==before
    else:
        if cut not in {'cash'}:
            def forbidden(**kwargs):raise AssertionError('cash writer must not run after closure plan')
            with monkeypatch.context() as mp:
                mp.setattr(a,'record_exact_cash_components',forbidden)
                assert x.c.run_cycle().actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
        else:
            assert x.c.run_cycle().actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert a.cash_authority_manager.status().post_attempt_count==1
    assert counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)
    assert len(json.loads(_export(x))['transactions'])==3
    assert a.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids==(intent.intent_id,)


@pytest.mark.parametrize('damage',['disconnected','market_idle','stopped','metadata','graph','recovery'])
def test_gui_preflight_blocks_before_any_provider_read_or_cash_write(exact_case,damage):
    x=exact_case;_prepare(x);a=x.c.execution_adapter
    if damage=='disconnected':x.c.set_connected(False)
    elif damage=='market_idle':x.c.set_market_state('MARKET_IDLE')
    elif damage=='stopped':
        x.c.restore()
        runtimes=x.c.runtime_store.load(expected_account_id=ACCOUNT)
        x.c.runtime_store.save(tuple(replace(r,status='STOPPED') for r in runtimes))
    elif damage=='metadata':(x.root/'portfolio_risk_metadata.json.sha256').write_text('0'*64)
    elif damage=='graph':x.c.cycle_source.portfolio_recovery.risk=object()
    else:x.c.cycle_source.portfolio_recovery=object()
    before=_state(x)
    reads=deepcopy((x.p.read_calls,x.p.cash_read_calls,x.exact_calls))
    with pytest.raises(Exception):x.c.run_cycle()
    assert _state(x)==before
    assert reads==(x.p.read_calls,x.p.cash_read_calls,x.exact_calls)
    assert x.p.order_calls==1


@pytest.mark.parametrize('status',['NEW','CANCELLED','REJECTED','PARTIALLYFILL'])
def test_non_full_fill_keeps_exact_pending_without_refresh_or_post(exact_case,status):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;intent,raw,_=_prepare(x)
    raw['executionReportStatus']='EXECUTION_REPORT_STATUS_'+status
    raw['lotsExecuted']='0'
    raw['stages']=[]
    if status=='PARTIALLYFILL':
        # A one-lot candidate cannot have a legal fractional-lot execution;
        # this is deliberately inconsistent, not a qualified partial receipt.
        raw['lotsExecuted']='1'
    before=_state(x)
    counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    for _ in range(2):
        with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
        assert _state(x)==before
    assert counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)


def test_missing_fee_can_arrive_on_later_tick_without_reposting(exact_case):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;_prepare(x)
    fee=x.operations.pop();before=_state(x)
    with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    assert _state(x)==before
    x.advance();x.operations.append(fee)
    assert x.c.run_cycle().actions[0].status=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert x.p.order_calls==1 and len(json.loads(_export(x))['transactions'])==3


@pytest.mark.parametrize('proof',['',True,17,'a'*63,'0'*64])
def test_cash_writer_checks_expected_proof_under_authority_lock(exact_case,proof):
    from trading_robot.exact_cash_settlement import ExactCashSettlementError
    x=exact_case;_prepare(x);before=_state(x)
    reads=deepcopy((x.p.read_calls,x.exact_calls,x.p.cash_read_calls))
    with pytest.raises(ExactCashSettlementError):
        x.c.execution_adapter.record_exact_cash_components(expected_proof_sha256=proof)
    assert _state(x)==before
    assert reads==(x.p.read_calls,x.exact_calls,x.p.cash_read_calls)


def test_corrupt_closure_is_not_treated_as_permission_to_repeat_cash(exact_case,monkeypatch):
    from trading_robot import exact_settlement_closure as closure
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;_prepare(x);a=x.c.execution_adapter
    with monkeypatch.context() as mp:
        original=closure._ClosureStore.create
        def cut(self,plan):original(self,plan);raise RuntimeError('AFTER_PLAN')
        mp.setattr(closure._ClosureStore,'create',cut)
        with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    path=next((x.root/'exact_settlement_closure').glob('*.json'))
    doc=json.loads(path.read_bytes());doc['hmac_sha256']='0'*64;path.write_text(json.dumps(doc))
    before=_state(x)
    with monkeypatch.context() as mp:
        def forbidden(**kw):raise AssertionError('do not re-run cash writer on corrupt checkpoint')
        mp.setattr(a,'record_exact_cash_components',forbidden)
        with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    assert _state(x)==before and x.p.order_calls==1


@pytest.mark.parametrize('args',[[],['--execution-order-type','BESTPRICE']])
def test_cli_requires_explicit_market_before_composition(exact_case,monkeypatch,args):
    from tools.v3_10_exact_settlement_recover import main
    import desktop_gui
    x=exact_case;_prepare(x);before=_state(x)
    def forbidden(*a,**kw):raise AssertionError('composition must not happen')
    monkeypatch.setattr(desktop_gui,'_compose_production_gui_runtime',forbidden)
    with pytest.raises(SystemExit) as error:main(['--runtime-dir',str(x.root),*args])
    assert error.value.code==2 and _state(x)==before


def test_cli_failure_redacts_raw_exception_and_preserves_pending(exact_case,monkeypatch,capsys):
    from tools.v3_10_exact_settlement_recover import main
    x=exact_case;_prepare(x);before=_state(x)
    def unavailable(*a,**kw):raise RuntimeError('SYNTHETIC_PRIVATE_TOKEN_ACCOUNT_PROVIDER')
    monkeypatch.setattr(x.p,'get_order_state',unavailable)
    assert main(['--runtime-dir',str(x.root),'--execution-order-type','MARKET'])==2
    output=capsys.readouterr().out
    assert 'SYNTHETIC_PRIVATE' not in output and ACCOUNT not in output
    assert json.loads(output)=={'automatic_retry':False,'status':'EXACT_RECOVERY_CLI_BLOCKED'}
    assert _state(x)==before and x.p.order_calls==1


def test_cli_builds_real_fresh_desktop_graph_and_closes_owned_connections(
    exact_case,desktop_case,monkeypatch,capsys,
):
    import desktop_gui
    from tools.v3_10_exact_settlement_recover import main
    from trading_robot.cash_ledger_persistence import CashLedgerStore
    from trading_robot.cash_ledger_opening_reconciliation import CL4_OPENING_CODEC,CL4_RUB_POSITION_OPENING_CODEC
    from trading_robot.broker_read_adapters import TBANK_OPERATION_CODEC
    x=exact_case;_prepare(x);old=x.c.execution_adapter
    clock,mono,wait=old.cl7_clock,old.cl7_monotonic_ns,old.cl7_wait_ns
    old.cl7_ledger_store.close()
    desktop_gui._PRODUCTION_COMPOSITION=None
    real_factory=desktop_gui._compose_production_gui_runtime
    created=[]
    def compose(root,*,execution_order_type):
        # Only external provider/credentials/time are synthetic. All economic
        # owners are built afresh by the real shipped factory, not a fake graph.
        c=real_factory(root,secret_provider=desktop_case.secret,
                       transport_factory=desktop_case.factory,execution_order_type=execution_order_type)
        c.cycle_source.clock=lambda:x.p.clock_at
        c.execution_adapter.cl7_clock,c.execution_adapter.cl7_monotonic_ns,c.execution_adapter.cl7_wait_ns=clock,mono,wait
        created.append(c)
        return c
    monkeypatch.setattr(desktop_gui,'_compose_production_gui_runtime',compose)
    counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    assert main(['--runtime-dir',str(x.root),'--execution-order-type','MARKET'])==0
    payload=json.loads(capsys.readouterr().out)
    assert payload['status']=='EXACT_SETTLEMENT_CLOSED_DISARMED'
    assert len(created)==1 and created[0] is not x.c
    assert desktop_gui._PRODUCTION_COMPOSITION is None and x.p.closed
    c=created[0]
    assert c.execution_adapter.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert c.central_order_coordinator.manager.state().blocking_intent is None
    assert c.central_order_coordinator.risk_runtime.state_store.load_account(ACCOUNT).daily_order_count==1
    store=CashLedgerStore.open(x.root/'cash_ledger_v3_10.sqlite3',
        (CL4_OPENING_CODEC,CL4_RUB_POSITION_OPENING_CODEC,TBANK_OPERATION_CODEC))
    try:
        assert len(json.loads(store.export_bytes())['transactions'])==3
        assert _cash(store.export_bytes())==998948641975321
    finally:store.close()
    assert counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)


def test_gui_audit_failure_after_settlement_stays_disarmed_and_blocks_next_cycle(exact_case,monkeypatch):
    from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
    x=exact_case;_prepare(x)
    original=x.c.journal.record
    def unavailable(event,*args,**kwargs):
        if event.event_type=='GUI_FILL_RECOVERY':raise RuntimeError('SYNTHETIC_PRIVATE_AUDIT')
        return original(event,*args,**kwargs)
    monkeypatch.setattr(x.c.journal,'record',unavailable)
    with pytest.raises(GuiRuntimeBlockedError,match='RECOVERY_AUDIT_UNAVAILABLE'):x.c.run_cycle()
    a=x.c.execution_adapter
    assert a.cash_authority_manager.status().state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert a.manager.state().blocking_intent is None
    before=_state(x);counts=x.p.candle_calls,x.p.quote_calls,x.p.order_calls
    with pytest.raises(GuiRuntimeBlockedError):x.c.run_cycle()
    assert _state(x)==before and counts==(x.p.candle_calls,x.p.quote_calls,x.p.order_calls)


def test_orchestration_lock_conflict_does_not_read_or_write(exact_case):
    from trading_robot.locking import InterProcessFileLock
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;_prepare(x);before=_state(x)
    reads=deepcopy((x.p.read_calls,x.p.cash_read_calls,x.exact_calls))
    with InterProcessFileLock(x.manager.repository.path.with_name('exact_recovery_tick.lock')):
        with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    assert _state(x)==before and reads==(x.p.read_calls,x.p.cash_read_calls,x.exact_calls)


def test_cash_phase_receives_exact_selected_proof(exact_case,monkeypatch):
    from trading_robot.exact_recovery_tick import ExactRecoveryTickError
    x=exact_case;intent,_,_=_prepare(x);a=x.c.execution_adapter
    original=a.record_exact_cash_components
    calls=[]
    def changed_request(*,expected_proof_sha256):
        calls.append(expected_proof_sha256)
        # Emulate stale request identity reaching the locked writer. The writer
        # must reject this request before any receipt read or money write.
        return original(expected_proof_sha256='f'*64)
    monkeypatch.setattr(a,'record_exact_cash_components',changed_request)
    before=_state(x);reads=deepcopy((x.p.read_calls,x.p.cash_read_calls,x.exact_calls))
    with pytest.raises(ExactRecoveryTickError):x.c.run_cycle()
    assert calls==[intent.cl7_locked_dispatch_proof_sha256]
    assert _state(x)==before and reads==(x.p.read_calls,x.p.cash_read_calls,x.exact_calls)
