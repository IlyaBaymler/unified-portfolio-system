"""STEP38: real controller ticks; only broker responses/clocks are synthetic."""
from __future__ import annotations
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import importlib
import sqlite3
import time

import pytest
from test_q7a_source_versioned_fill_closure import (
    base_desktop_case, desktop_case, exact_case, refresh_case, read_case, op_case,
    cut_case, selected_case, owner_case, admission_case, state,
)
from test_q7a_source_settlement_closure import _snapshot, _recompose
from test_q7a_source_versioned_fill_cash import money
from test_q7a_source_exact_dispatch import stamp
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State
from trading_robot.locking import InterProcessFileLock, LockUnavailableError


def bind(c):
    c.controller=c.x.c.y.x.c
    c.a=c.controller.execution_adapter;c.r=c.controller.cycle_source.portfolio_recovery
    c.route=c.controller.bind_versioned_runtime(target_root=c.root, selection_sha256=c.prepared.plan_sha256,
        expected_authority_sha256=c.a.cash_authority_manager.status().sha256,instrument_id='uid-lkoh')
    return c.route


def tick(c, *, direct=False):
    c.provider.quote_at=c.provider.clock_at
    if direct:
        result=c.controller.service_tick(now=c.provider.clock_at,latest_closed_candles={},hooks=None)
    else:result=c.controller.run_cycle()
    assert len(result.actions)==1
    return result.actions[0].status


def step_time(c,seconds=1):
    c.provider.clock_at+=timedelta(seconds=seconds);c.provider.quote_at=c.provider.clock_at


def wire(c,mp,number,*,lost=False):
    def post(account,uid,lots,direction,*,order_id,order_type,time_in_force):
        record=c.a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        assert record.state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING
        assert record.post_attempt_count==number+1
        i=c.a.manager.store._load_unlocked(expected_account_id=account).blocking_intent
        assert i.status=='IN_FLIGHT' and i.intent_id==order_id and uid=='uid-lkoh'
        for path in (c.r.manager.repository.lock_path,c.r.risk.state_store.lock_path,c.a.manager.store.lock_path):
            with pytest.raises(LockUnavailableError):
                with InterProcessFileLock(path,timeout_seconds=0):pass
        conn=sqlite3.connect(c.root/'ledger/store.sqlite3',timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError):conn.execute('BEGIN IMMEDIATE')
        finally:conn.close()
        c.provider.order_calls+=1
        c.reply=dict(accountId=account,instrumentUid=uid,lotsRequested=str(lots),lotsExecuted='0',
            direction='ORDER_DIRECTION_'+direction,orderType='ORDER_TYPE_'+order_type,
            orderId=f'CONTROLLER-EXCHANGE-{number}',orderRequestId=order_id,
            executionReportStatus='EXECUTION_REPORT_STATUS_NEW',currency='rub')
        if lost:raise TimeoutError('synthetic acknowledgement loss')
        return deepcopy(c.reply)
    mp.setattr(c.provider,'post_order_once',post)


def fill_broker(c,mp,number):
    """Simulate execution without calling a financial write/settlement API."""
    intent=c.a.manager.state().blocking_intent
    before=state(c)[0]
    old_port=deepcopy(c.provider.get_portfolio(c.a.policy.account_id))
    old_rows=deepcopy(c.fill_rows if hasattr(c,'fill_rows') else c.rows)
    step_time(c)
    at=stamp(c.provider.clock_at);price=Decimal('105.123456789');fee=Decimal('0.123456789')
    units=intent.candidate.requested_lots*intent.candidate.lot_size;gross=price*units
    direction=intent.candidate.direction
    c.fill=dict(c.reply,lotsExecuted='1',executionReportStatus='EXECUTION_REPORT_STATUS_FILL',
        executedOrderPrice=money('9999.999999999'),executedCommission=money(fee),serviceCommission=money(0),
        averagePositionPrice=money(price),stages=[{'tradeId':f'CONTROLLER-TRADE-{number}','executionTime':at,
                                                 'price':money(price),'quantity':'1'}])
    trade=dict(id=f'CONTROLLER-OP-{number}',brokerAccountId=c.a.policy.account_id,cursor=f'CONTROLLER-CURSOR-{number}',
        date=at,type='OPERATION_TYPE_'+direction,state='OPERATION_STATE_EXECUTED',quantity=str(units),quantityDone=str(units),
        quantityRest='0',payment=money(-gross if direction=='BUY' else gross),commission=money(fee),childOperations=[],
        instrumentUid='uid-lkoh',instrumentType='share',tradesInfo={'trades':[{'num':f'CONTROLLER-TRADE-{number}',
          'date':at,'quantity':str(units),'price':money(price)}]})
    fee_row=dict(id=f'CONTROLLER-FEE-{number}',brokerAccountId=c.a.policy.account_id,cursor=f'CONTROLLER-FEE-CURSOR-{number}',
        date=at,type='OPERATION_TYPE_BROKER_FEE',state='OPERATION_STATE_EXECUTED',quantity='0',quantityDone='0',quantityRest='0',
        payment=money(-fee),commission=money(0),childOperations=[],parentOperationId=trade['id'],instrumentUid='uid-lkoh')
    c.fill_rows=old_rows+[trade,fee_row]
    c.cash=before.cash_nano+int(((-gross if direction=='BUY' else gross)-fee)*10**9)
    def order(account,order_id,*,by_request_id=False):
        assert by_request_id and order_id==intent.intent_id
        return deepcopy(c.fill)
    mp.setattr(c.provider,'get_order_state',order)
    mp.setattr(c.provider,'get_operations_by_cursor_once',lambda payload,timeout:{'items':deepcopy(c.fill_rows),'hasNext':False,'nextCursor':''})
    mp.setattr(c.provider,'get_positions',lambda account:{'accountId':account,'money':[money(Decimal(c.cash)/10**9)],'blocked':[],
        'limitsLoadingInProgress':False,'securities':[],'futures':[],'options':[]})
    rows=[r for r in old_port['positions'] if r['instrumentUid']!='uid-lkoh']
    if intent.candidate.target_lots:
        rows.append(dict(instrumentUid='uid-lkoh',figi='figi-lkoh',ticker='LKOH',classCode='TQBR',instrumentType='share',
            quantity=money(units),quantityLots=money(1),averagePositionPrice=money(price),currentPrice=money(price)))
    shares=sum((Decimal(r['quantity']['units'])+Decimal(r['quantity']['nano'])/10**9)*
               (Decimal(r['currentPrice']['units'])+Decimal(r['currentPrice']['nano'])/10**9) for r in rows)
    c.portfolio_after={**old_port,'positions':rows,'totalAmountCurrencies':money(Decimal(c.cash)/10**9),
       'totalAmountShares':money(shares),'totalAmountPortfolio':money(Decimal(c.cash)/10**9+shares)}
    mp.setattr(c.provider,'get_portfolio',lambda account:deepcopy(c.portfolio_after))
    step_time(c)


def arm(c):
    intent=c.a.manager.state().queued[0];sha=c.route.expected_authority
    out=c.route.arm(intent_id=intent.intent_id,confirmation=f'ARM VERSIONED ORDER {intent.intent_id} {sha}')
    assert out.status=='ARMED'
    return intent.intent_id


def test_controller_admission_wait_and_no_implicit_arm(admission_case,monkeypatch):
    c=admission_case;bind(c)
    def old(*a,**k):pytest.fail('legacy strategy hooks/cycle route called')
    monkeypatch.setattr(c.controller.cycle_source,'candle_loader',old,raising=False)
    assert tick(c)=='QUEUED'
    before=_snapshot(c.x.c.y.x),state(c)[1]
    assert tick(c,direct=True)=='WAITING_FOR_EXPLICIT_ARM'
    assert (_snapshot(c.x.c.y.x),state(c)[1])==before
    assert c.provider.order_calls==1


@pytest.mark.parametrize('mode',['normal','restart_sell_risk'])
def test_controller_continuous_roundtrip(admission_case,desktop_case,monkeypatch,request,mode):
    c=admission_case;bind(c);start=time.monotonic();cash0=state(c)[0];v1=c.a.cl7_ledger_store.export_bytes()
    unrelated=c.r.manager.repository.load().position('uid-sber')
    fixed=(unrelated.actual_lots,unrelated.target_lots,unrelated.ownership,unrelated.origin)
    statuses=[]
    statuses.append(tick(c));assert statuses[-1]=='QUEUED'
    wire(c,monkeypatch,1);buy_id=arm(c)
    statuses.append(tick(c,direct=True));assert statuses[-1]=='SUBMITTED'
    fill_broker(c,monkeypatch,1)
    statuses.append(tick(c));assert statuses[-1]=='CASH_COMMITTED'
    # Cash tick does not also finalize owners or admit a new order.
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISPATCH_PENDING
    assert c.r.manager.repository.load().position('uid-lkoh') is None
    step_time(c)
    statuses.append(tick(c,direct=True));assert statuses[-1]=='FULL_FILL_CLOSED_DISARMED'
    step_time(c);statuses.append(tick(c));assert statuses[-1]=='CASH_SYNCED'
    step_time(c);statuses.append(tick(c,direct=True));assert statuses[-1]=='OWNERS_REFRESHED'
    statuses.append(tick(c));assert statuses[-1]=='NO_POSITION_CHANGE'
    c.provider.target=0;step_time(c,3600)
    statuses.append(tick(c));assert statuses[-1]=='CASH_SYNCED'
    step_time(c);statuses.append(tick(c));assert statuses[-1]=='OWNERS_REFRESHED'
    statuses.append(tick(c,direct=True));assert statuses[-1]=='QUEUED'
    wire(c,monkeypatch,2,lost=(mode=='restart_sell_risk'));sell_id=arm(c)
    statuses.append(tick(c));assert statuses[-1] in {'SUBMITTED','SUBMISSION_UNCERTAIN'}
    fill_broker(c,monkeypatch,2)
    statuses.append(tick(c,direct=True));assert statuses[-1]=='CASH_COMMITTED';step_time(c)
    if mode=='restart_sell_risk':
        from trading_robot import versioned_fill_closure as mod
        old=mod.close_selected_full_fill
        def fault(point):
            if point=='closure.after_risk':raise RuntimeError('controller risk cut')
        with monkeypatch.context() as mp:
            mp.setattr(mod,'close_selected_full_fill',lambda *a,**k:old(*a,**k,fault_injector=fault))
            with pytest.raises(RuntimeError,match='controller risk cut'):tick(c)
        raw=state(c)[1]
        _recompose(c.x.c.y.x,desktop_case);bind(c)
        # Offline recovery remains possible while provider disconnected/market idle.
        c.controller.set_connected(False);c.controller.set_market_state('MARKET_IDLE')
        def no_read(*a,**k):pytest.fail('provider called by offline controller recovery')
        with monkeypatch.context() as mp:
            for name in ('get_candles','get_portfolio','get_orders','get_order_state','get_positions','get_max_lots','get_operations_by_cursor_once','post_order_once'):
                mp.setattr(c.provider,name,no_read)
            statuses.append(tick(c));assert statuses[-1]=='FULL_FILL_CLOSED_DISARMED'
        assert state(c)[1]==raw
        c.controller.set_connected(True);c.controller.set_market_state('OPEN')
    else:
        statuses.append(tick(c));assert statuses[-1]=='FULL_FILL_CLOSED_DISARMED'
    step_time(c);statuses.append(tick(c));assert statuses[-1]=='CASH_SYNCED'
    step_time(c);statuses.append(tick(c,direct=True));assert statuses[-1]=='OWNERS_REFRESHED'
    statuses.append(tick(c));assert statuses[-1]=='NO_POSITION_CHANGE'
    money_final=state(c)[0];assert money_final.cash_nano==cash0.cash_nano-246913578==999048318518532
    assert money_final.transaction_count==cash0.transaction_count+4
    assert c.a.cl7_ledger_store.export_bytes()==v1 and c.provider.order_calls==3
    central=c.a.manager.state();assert not central.queued and central.blocking_intent is None
    both=[i for i in central.intents if i.intent_id in (buy_id,sell_id)]
    assert len(both)==2 and all(i.status=='RECONCILED' and i.executed_lots==1 for i in both)
    risk=c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert risk.daily_order_count==len(risk.recorded_execution_ids)==3
    pos=c.r.manager.repository.load().position('uid-lkoh');assert pos is None or (pos.actual_lots==0 and pos.ownership is None)
    sber=c.r.manager.repository.load().position('uid-sber')
    assert (sber.actual_lots,sber.target_lots,sber.ownership,sber.origin)==fixed
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED
    assert c.a.cash_authority_manager.status().post_attempt_count==3
    request.node.user_properties.extend([('mode',mode),('cash_before_nano',cash0.cash_nano),('cash_final_nano',money_final.cash_nano),
        ('new_posts',2),('new_cash_transactions',4),('new_risk_executions',2),('tick_statuses',','.join(statuses)),
        ('wall_seconds_including_setup_and_validation',time.monotonic()-start)])


def test_route_preserves_disconnected_market_and_checkpoint_guards(admission_case):
    from trading_robot.versioned_runtime_route import VersionedRouteError
    from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
    c=admission_case;c.controller=c.x.c.y.x.c
    before=_snapshot(c.x.c.y.x)
    # No attachment means the existing early versioned guard remains intact.
    with pytest.raises(GuiRuntimeBlockedError,match='VERSIONED_CASH_ROUTE_NOT_ACTIVE'):
        c.controller.run_cycle()
    with pytest.raises(VersionedRouteError,match='CHECKPOINT'):
        c.controller.bind_versioned_runtime(target_root=c.root,selection_sha256=c.prepared.plan_sha256,
            expected_authority_sha256='0'*64,instrument_id='uid-lkoh')
    assert getattr(c.controller,'_versioned_route',None) is None
    for disconnected in (True,False):
        bind(c)
        c.controller.set_connected(not disconnected)
        c.controller.set_market_state('OPEN' if disconnected else 'MARKET_IDLE')
        with pytest.raises(VersionedRouteError,match='DISCONNECTED|MARKET_IDLE'):tick(c)
        assert _snapshot(c.x.c.y.x)==before and c.provider.order_calls==1
    c.controller.set_connected(True);c.controller.set_market_state('OPEN')
    bind(c)
    c.route._busy.acquire()
    try:
        with pytest.raises(VersionedRouteError,match='BUSY'):tick(c)
    finally:c.route._busy.release()
    assert _snapshot(c.x.c.y.x)==before


def test_gui_audit_error_latches_without_rolling_back_queue(admission_case,monkeypatch):
    from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
    c=admission_case;bind(c);original=c.controller.journal.record
    def audit(event,*a,**kw):
        if event.event_type=='GUI_VERSIONED_FINANCIAL_TICK':raise OSError('synthetic audit failure')
        return original(event,*a,**kw)
    monkeypatch.setattr(c.controller.journal,'record',audit)
    with pytest.raises(GuiRuntimeBlockedError,match='TICK_AUDIT_UNAVAILABLE'):tick(c)
    assert len(c.a.manager.state().queued)==1 and c.provider.order_calls==1
    assert c.a.cash_authority_manager.status().state is State.EXACT_CASH_VERSIONED_DISARMED
    saved=_snapshot(c.x.c.y.x)
    with pytest.raises(GuiRuntimeBlockedError,match='EXECUTION_OBSERVATION_BLOCKED'):tick(c)
    assert _snapshot(c.x.c.y.x)==saved


def test_cli_refuses_borrowed_gui_then_uses_and_closes_own_factory(admission_case,desktop_case,monkeypatch):
    import desktop_gui
    from tools.v3_10_selected_runtime import run_existing_runtime
    from trading_robot.versioned_runtime_route import VersionedRouteError
    c=admission_case;bind(c)
    runtime=c.a.cash_authority_manager.store.path.parent
    arguments=dict(selected_root=c.root,selection_sha256=c.prepared.plan_sha256,
        expected_authority_sha256=c.route.expected_authority,instrument_id='uid-lkoh',execution_order_type='MARKET')
    old_comp=desktop_gui._PRODUCTION_COMPOSITION
    with pytest.raises(VersionedRouteError,match='GUI_ALREADY_COMPOSED'):
        run_existing_runtime(runtime,**arguments)
    assert desktop_gui._PRODUCTION_COMPOSITION is old_comp and not c.a.cl7_ledger_store._closed
    # Detach the first actual factory, then let CLI compose its OWN real graph.
    clock,mono,wait=c.a.cl7_clock,c.a.cl7_monotonic_ns,c.a.cl7_wait_ns
    c.a.cl7_ledger_store.close();desktop_gui._PRODUCTION_COMPOSITION=None
    original=desktop_gui._compose_production_gui_runtime
    created=[]
    def compose(root,**kwargs):
        ctl=original(root,secret_provider=desktop_case.secret,transport_factory=desktop_case.factory,**kwargs)
        ctl.cycle_source.clock=lambda:c.provider.clock_at
        a=ctl.execution_adapter;a.cl7_clock,a.cl7_monotonic_ns,a.cl7_wait_ns=clock,mono,wait
        assert kwargs['require_new'] is True
        # The native factory refuses a second borrower while the CLI owns it.
        from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
        with pytest.raises(GuiRuntimeBlockedError,match='COMPOSITION_IN_USE'):
            original(root,secret_provider=desktop_case.secret,transport_factory=desktop_case.factory,
                     execution_order_type='MARKET')
        created.append(ctl);return ctl
    monkeypatch.setattr(desktop_gui,'_compose_production_gui_runtime',compose)
    out=run_existing_runtime(runtime,**arguments)
    assert out['result']['status']=='QUEUED'
    assert len(created)==1 and created[0].execution_adapter.cl7_ledger_store._closed
    assert desktop_gui._PRODUCTION_COMPOSITION is None
    arguments['expected_authority_sha256']=out['result']['authority_sha256']
    again=run_existing_runtime(runtime,**arguments)
    assert again['result']['status']=='WAITING_FOR_EXPLICIT_ARM'
    assert len(created)==2 and created[1].execution_adapter.cl7_ledger_store._closed
    assert desktop_gui._PRODUCTION_COMPOSITION is None and c.provider.order_calls==1


def test_cli_error_output_does_not_echo_exception_or_arm(tmp_path,monkeypatch,capsys):
    from tools import v3_10_selected_runtime as cli
    def fail(*a,**k):raise RuntimeError('PRIVATE_TOKEN_CANARY')
    monkeypatch.setattr(cli,'run_existing_runtime',fail)
    code=cli.main(['--runtime-dir',str(tmp_path),'--selected-root',str(tmp_path),
        '--selection-sha256','1'*64,'--authority-sha256','2'*64,'--instrument-id','uid-lkoh',
        '--execution-order-type','MARKET','tick'])
    output=capsys.readouterr().out
    assert code==2 and 'PRIVATE_TOKEN_CANARY' not in output and 'VERSIONED_CLI_BLOCKED' in output


def test_route_does_not_approve_resync_without_explicit_confirmation(owner_case):
    from test_q7a_source_selected_owner_refresh_rebuilt import refresh
    c=owner_case;refresh(c);c.provider=c.x.c.y.x.p;bind(c)
    risk_before=c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert risk_before.risk_resync_required
    before=_snapshot(c.x.c.y.x)
    assert tick(c)=='CASH_FLOW_CONFIRMATION_REQUIRED'
    assert _snapshot(c.x.c.y.x)==before
    plan=c.route.prepare_resync()
    assert _snapshot(c.x.c.y.x)==before
    result=c.route.confirm_resync(plan_sha256=plan.plan_sha256,confirmation=plan.confirmation)
    assert result.status=='CASH_FLOW_ACKNOWLEDGED'
    risk=c.r.risk.state_store.load_account(c.a.policy.account_id)
    assert not risk.risk_resync_required and risk.daily_start_equity_rub==1_000_100
    assert risk.recorded_execution_ids==risk_before.recorded_execution_ids and c.provider.order_calls==1


def test_route_checks_persisted_stop_not_stale_in_memory(admission_case,monkeypatch):
    from dataclasses import replace
    from trading_robot.versioned_runtime_route import VersionedRouteError
    c=admission_case;bind(c);before=_snapshot(c.x.c.y.x)
    configured=c.controller._load_configured_set()
    stopped=replace(configured,bindings=tuple(replace(b,runtime=replace(b.runtime,status='STOPPED'))
                                             for b in configured.bindings))
    # Isolate the routing read boundary. No claim of a new STOP writer.
    monkeypatch.setattr(c.controller,'_load_configured_set',lambda:stopped)
    with pytest.raises(VersionedRouteError,match='NOT_ACTIVE'):tick(c)
    assert _snapshot(c.x.c.y.x)==before and c.provider.order_calls==1
    assert c.route._needs_rebind


def test_cli_factory_atomic_gate_rejects_racing_gui(admission_case,monkeypatch):
    import desktop_gui
    from tools.v3_10_selected_runtime import run_existing_runtime
    from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
    c=admission_case;bind(c);saved=desktop_gui._PRODUCTION_COMPOSITION
    root=c.a.cash_authority_manager.store.path.parent
    original=desktop_gui._compose_production_gui_runtime
    desktop_gui._PRODUCTION_COMPOSITION=None
    def racing(*args,**kwargs):
        desktop_gui._PRODUCTION_COMPOSITION=saved
        return original(*args,**kwargs)
    monkeypatch.setattr(desktop_gui,'_compose_production_gui_runtime',racing)
    try:
        with pytest.raises(GuiRuntimeBlockedError,match='COMPOSITION_IN_USE'):
            run_existing_runtime(root,selected_root=c.root,selection_sha256=c.prepared.plan_sha256,
                expected_authority_sha256=c.route.expected_authority,instrument_id='uid-lkoh',execution_order_type='MARKET')
        assert desktop_gui._PRODUCTION_COMPOSITION is saved
        assert not c.a.cl7_ledger_store._closed and c.provider.order_calls==1
    finally:
        desktop_gui._PRODUCTION_COMPOSITION=saved
