"""STEP33: natural SMA -> native Risk(s) -> actual Central; synthetic provider only."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from dataclasses import replace
import importlib
import json
import pytest

from test_q7a_source_verified_cash_resync import (
    base_desktop_case, desktop_case, exact_case, refresh_case, read_case, op_case, cut_case,
    selected_case, owner_case, refresh, prepare, confirm, _snapshot, money,
)
from test_q7a_source_selected_sync import _view
from test_q7a_source_settlement_closure import _recompose


def module():
    return importlib.import_module('trading_robot.versioned_risk_admission')


@pytest.fixture
def admission_case(owner_case):
    c=owner_case
    refresh(c); confirm(c,prepare(c))
    c.provider=c.x.c.y.x.p
    c.provider.quote_at=c.provider.clock_at
    return c


def admit(c,uid='uid-lkoh',**kwargs):
    return module().admit_selected_order(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,
        expected_authority_sha256=c.a.cash_authority_manager.status().sha256,instrument_id=uid,**kwargs)


def recover(c,**kwargs):
    plan=next((c.root/'risk_admission/plans').glob('*.json')).stem
    return module().recover_selected_admission(c.a,recovery=c.r,target_root=c.root,
        expected_selection_sha256=c.prepared.plan_sha256,expected_plan_sha256=plan,**kwargs)


def test_core_natural_buy_is_queued_with_real_risk_and_no_post(admission_case,request):
    c=admission_case
    before=_snapshot(c.x.c.y.x)
    with _view(c) as (_,view):cash=view.export_bytes()
    result=admit(c)
    intent=c.a.manager.state().queued[0]
    assert intent.intent_id==result.intent_id and intent.status=='QUEUED'
    assert intent.candidate.direction=='BUY' and intent.candidate.target_lots==1
    assert intent.authorization.portfolio_risk.finalized
    assert intent.authorization.risk_order_allowed and intent.authorization.risk_status=='PASS'
    assert result.reserved_cash_kopecks==106050
    after=_snapshot(c.x.c.y.x)
    assert before[0]['portfolio']==after[0]['portfolio'] and before[1:]==after[1:]
    assert after[0]['risk']['recorded_execution_ids']==before[0]['risk']['recorded_execution_ids']
    assert after[0]['risk']['daily_order_count']==before[0]['risk']['daily_order_count']==1
    with _view(c) as (_,view):assert view.export_bytes()==cash
    assert c.provider.order_calls==1 and c.a.cash_authority_manager.status().post_attempt_count==1
    assert not result.public_summary()['broker_execution_authorized']
    saved=_snapshot(c.x.c.y.x)
    assert recover(c).replay and _snapshot(c.x.c.y.x)==saved
    request.node.user_properties.extend([('direction','BUY'),('reserved_cash_kopecks',result.reserved_cash_kopecks),
        ('new_money_writes',0),('new_risk_execution',0),('fake_post_total',1)])


def test_core_natural_sell_is_queued_with_zero_cash_reserve(admission_case,request):
    from test_q7a_source_selected_sync import _run
    c=admission_case;c.provider.target=0
    # The seed BUY used the preceding hourly candle. Obtain a truly NEW closed
    # candle and fresh operations/owner evidence; never rewrite the seed intent.
    c.provider.clock_at+=timedelta(hours=1);c.provider.quote_at=c.provider.clock_at
    _run(c);c.provider.clock_at+=timedelta(seconds=1);refresh(c)
    result=admit(c,'uid-sber')
    intent=c.a.manager.state().queued[0]
    assert intent.candidate.direction=='SELL' and intent.candidate.target_lots==0
    assert intent.candidate.current_lots==1 and result.reserved_cash_kopecks==0
    assert intent.authorization.portfolio_risk.finalized
    assert c.provider.order_calls==1
    request.node.user_properties.extend([('direction','SELL'),('reserved_cash_kopecks',0),('fake_post_total',1)])


def test_blocked_capture_never_writes_owners_or_plan(admission_case,monkeypatch):
    from decimal import Decimal
    c=admission_case; before=_snapshot(c.x.c.y.x)
    oq,oc,ol,oo=(c.provider.get_last_prices,c.provider.get_positions,c.provider.get_max_lots,c.provider.get_orders)
    scenarios=['foreign_quote','stale_quote','cash_nano','external_order','budget_too_small','market_lots_zero']
    for damage in scenarios:
        with monkeypatch.context() as mp:
            if damage in ('foreign_quote','stale_quote'):
                def quote(*args,**kw):
                    raw=deepcopy(oq(*args,**kw))
                    if damage=='foreign_quote':raw[0]['instrumentUid']='OTHER'
                    else:raw[0]['time']=(c.provider.clock_at-timedelta(seconds=6)).isoformat()
                    return raw
                mp.setattr(c.provider,'get_last_prices',quote)
            elif damage=='cash_nano':
                def cash(*args,**kw):
                    raw=deepcopy(oc(*args,**kw));raw['money'][0]['nano']+=1;return raw
                mp.setattr(c.provider,'get_positions',cash)
            elif damage=='external_order':mp.setattr(c.provider,'get_orders',lambda *a,**k:[{'orderId':'unexpected'}])
            else:
                def limits(*args,**kw):
                    raw=deepcopy(ol(*args,**kw))
                    if damage=='budget_too_small':raw['buyLimits']['buyMoneyAmount']=money(Decimal('1'))
                    else:raw['buyLimits']['buyMaxMarketLots']='0'
                    return raw
                mp.setattr(c.provider,'get_max_lots',limits)
            with pytest.raises(Exception) as caught:admit(c)
            assert not isinstance(caught.value,(TypeError,AttributeError,KeyError)), repr(caught.value)
            assert any(s in str(caught.value).upper() for s in ('QUOTE','CASH','RISK','ORDER','LOT')), repr(caught.value)
            assert _snapshot(c.x.c.y.x)==before
            assert not list((c.root/'risk_admission/plans').glob('*.json'))
    assert c.provider.order_calls==1


def test_resync_must_be_resolved_before_admission(owner_case):
    c=owner_case;refresh(c);c.provider=c.x.c.y.x.p;c.provider.quote_at=c.provider.clock_at
    assert c.r.risk.state_store.load_account(c.a.policy.account_id).risk_resync_required
    before=_snapshot(c.x.c.y.x)
    with pytest.raises(module().VersionedAdmissionError,match='RISK_RESYNC_REQUIRED'):admit(c)
    assert _snapshot(c.x.c.y.x)==before and not c.a.manager.state().queued


@pytest.mark.parametrize('point',['admission.after_hold','admission.after_risk','admission.after_central',
    'admission.after_result','admission.after_audit','admission.after_authority'])
def test_offline_recovery_exact_prefix_and_no_repeat(admission_case,desktop_case,monkeypatch,point):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock,LockUnavailableError
    c=admission_case;original=_snapshot(c.x.c.y.x)
    def fault(at):
        if at==point:
            paths=[c.r.manager.repository.lock_path,c.r.risk.state_store.lock_path]
            if point!='admission.after_central':paths.append(c.a.manager.store.lock_path)
            for lock in paths:
                with pytest.raises(LockUnavailableError):
                    with InterProcessFileLock(lock,timeout_seconds=0):pass
            connection=sqlite3.connect(c.root/'ledger/store.sqlite3',timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError):connection.execute('BEGIN IMMEDIATE')
            finally:connection.close()
            raise RuntimeError('injected admission cut')
    with pytest.raises(RuntimeError,match='injected admission cut'):admit(c,fault_injector=fault)
    if point=='admission.after_risk':
        _recompose(c.x.c.y.x,desktop_case)
        c.a=c.x.c.y.x.c.execution_adapter;c.r=c.x.c.y.x.c.cycle_source.portfolio_recovery
    for name in ('get_candles','get_last_prices','get_positions','get_orders','get_order_state',
                 'get_operations_by_cursor_once','get_max_lots'):
        monkeypatch.setattr(c.provider,name,lambda *a,**k:pytest.fail('broker read during offline recovery'))
    result=recover(c)
    final=_snapshot(c.x.c.y.x)
    assert final[0]['portfolio']==original[0]['portfolio'] and final[1:]==original[1:]
    assert final[0]['risk']['recorded_execution_ids']==original[0]['risk']['recorded_execution_ids']
    assert len(c.a.manager.state().queued)==1 and c.a.manager.state().queued[0].intent_id==result.intent_id
    assert c.provider.order_calls==1 and c.a.cash_authority_manager.status().post_attempt_count==1
    assert recover(c).replay and _snapshot(c.x.c.y.x)==final


def test_prepared_plan_without_hold_is_not_permission(admission_case):
    c=admission_case;before=_snapshot(c.x.c.y.x)
    def stop(at):
        if at=='admission.after_plan':raise RuntimeError('plan only')
    with pytest.raises(RuntimeError,match='plan only'):admit(c,fault_injector=stop)
    assert _snapshot(c.x.c.y.x)==before
    with pytest.raises(module().VersionedAdmissionError,match='CONFIRMED_HOLD_REQUIRED'):recover(c)
    assert _snapshot(c.x.c.y.x)==before


def test_captured_risk_limits_and_resigned_false_plan(admission_case):
    from dataclasses import asdict
    from trading_robot.risk import RiskPolicy
    from trading_robot.instrument_runtime import InstrumentRuntime
    from trading_robot.multi_instrument_config import MultiInstrumentProfile
    from trading_robot.versioned_operational_store import OperationalPins
    from trading_robot.versioned_fee_evidence import _canonical,_sha,_sealed,_parse
    from trading_robot import versioned_selected_sync as sync
    from trading_robot.runtime_cash_authority import RuntimeCashAuthorityRecord
    c=admission_case;m=module();result=admit(c)
    root=c.root/'risk_admission/plans';data=json.loads((root/(result.plan_sha256+'.json')).read_text())['payload']
    p=m.cut._load_prepared(c.a,c.r,c.root,c.prepared.plan_sha256,_check_initial_owners=False)
    before_a=RuntimeCashAuthorityRecord.from_canonical_dict(data['before_authority'])
    _,_,_,raw=sync._lineage(c.a,p,c.prepared.plan_sha256,before_a)
    kwargs=dict(started_at=data['started_at'],evaluated_at=data['evaluated_at'],cash_nano=data['cash_nano'],
        pins=OperationalPins(**data['pins']),source_raw=raw)
    for kind in ('loss','resync','position_limit','portfolio_limit'):
        owners=deepcopy(data['before_owners']);policy=RiskPolicy(**data['policy'])
        if kind=='loss':policy=replace(policy,daily_loss_limit_rub=0.01)
        elif kind=='resync':owners['risk']['risk_resync_required']=True
        elif kind=='position_limit':policy=replace(policy,max_position_lots=0)
        else:policy=replace(policy,max_gross_exposure_rub=1.0)
        with pytest.raises(Exception) as caught:
            m._evaluate(c.a,c.r,owners,InstrumentRuntime.from_dict(data['runtime']),
                MultiInstrumentProfile.from_dict(data['profile']),policy,m._Tape(rows=data['reads']),**kwargs)
        assert not isinstance(caught.value,(TypeError,AttributeError,KeyError)),repr(caught.value)
        assert {'loss':'DAILY_LOSS','resync':'RISK_RESYNC','position_limit':'SINGLE_RISK_BLOCKED',
            'portfolio_limit':'GROSS_EXPOSURE_LIMIT_BREACH'}[kind] in str(caught.value).upper(),str(caught.value)
    saved=_snapshot(c.x.c.y.x)
    bad=deepcopy(data);bad['evaluation']['available_cash_kopecks']+=100
    raw_bad=_sealed(bad,c.a.cl7_identity_key);digest=_sha(raw_bad)
    (root/(digest+'.json')).write_bytes(raw_bad)
    with pytest.raises(m.VersionedAdmissionError,match='ECONOMIC_PLAN_INVALID'):
        m._plan(c.a,c.r,p,c.prepared.plan_sha256,digest)
    assert _snapshot(c.x.c.y.x)==saved


def test_pending_admission_blocks_refresh_second_order_and_dispatch(admission_case):
    from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError
    from test_q7a_source_selected_sync import _run
    c=admission_case;admit(c);before=_snapshot(c.x.c.y.x)
    with pytest.raises(module().VersionedAdmissionError,match='CENTRAL_NOT_QUIESCENT'):admit(c)
    with pytest.raises(Exception,match='ADMITTED_ORDER_REQUIRES_DISPATCH_RECOVERY'):_run(c)
    controller=c.x.c.y.x.c;controller.set_connected(True);controller.set_market_state('OPEN')
    with pytest.raises(GuiRuntimeBlockedError,match='VERSIONED_CASH_ROUTE_NOT_ACTIVE'):controller.run_cycle()
    with pytest.raises(Exception,match='STATE_TRANSITION_INVALID'):
        c.a.cash_authority_manager.arm(raw_account_id=c.a.policy.account_id, identity_key=c.a.cl7_identity_key,
            identity_key_id=c.a.cl7_identity_key_id, confirmation=c.a.cash_authority_manager.ARM_PHRASE,
            transition_at=c.a.cl7_clock())
    assert _snapshot(c.x.c.y.x)==before and c.provider.order_calls==1


def test_capture_total_deadline_not_restarted(admission_case,monkeypatch):
    c=admission_case;before=_snapshot(c.x.c.y.x)
    candles=c.provider.get_candles;quotes=c.provider.get_last_prices
    def slow_candles(*a,**k):
        raw=candles(*a,**k);c.provider.clock_at+=timedelta(seconds=4);return raw
    def slow_quote(*a,**k):
        raw=quotes(*a,**k);c.provider.clock_at+=timedelta(seconds=2);return raw
    monkeypatch.setattr(c.provider,'get_candles',slow_candles);monkeypatch.setattr(c.provider,'get_last_prices',slow_quote)
    with pytest.raises(module().VersionedAdmissionError,match='STALE'):admit(c)
    assert _snapshot(c.x.c.y.x)==before


def test_risk_drift_during_io_is_not_overwritten(admission_case,monkeypatch):
    c=admission_case;original=c.provider.get_last_prices
    old=c.r.risk.state_store.load_account(c.a.policy.account_id)
    newer=replace(old,daily_order_count=old.daily_order_count+1)
    def change(*args,**kwargs):
        raw=original(*args,**kwargs)
        c.r.risk.state_store.save_account(c.a.policy.account_id,newer)
        return raw
    monkeypatch.setattr(c.provider,'get_last_prices',change)
    authority=c.a.cash_authority_manager.status()
    with pytest.raises(module().VersionedAdmissionError,match='OWNER_CHANGED'):admit(c)
    assert c.r.risk.state_store.load_account(c.a.policy.account_id)==newer
    assert c.a.cash_authority_manager.status()==authority and not c.a.manager.state().queued


def test_selection_without_owner_refresh_cannot_enter_risk(selected_case):
    c=selected_case;c.provider=c.x.c.y.x.p
    before=_snapshot(c.x.c.y.x);reads=c.provider.candle_calls
    with pytest.raises(module().VersionedAdmissionError,match='RECENT_OWNER_REFRESH_REQUIRED'):admit(c)
    assert _snapshot(c.x.c.y.x)==before and c.provider.candle_calls==reads
