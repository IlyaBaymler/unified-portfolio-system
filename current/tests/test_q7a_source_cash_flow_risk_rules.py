"""Pure financial-rule matrix and strict non-authorizing authority transitions."""
from dataclasses import replace
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest

from test_v3_10_cash_ledger_domain import _transaction,_source
from test_q7a_source_selected_sync_transitions import _records
from trading_robot.cash_ledger_domain import LedgerClassification as C, LedgerAccount as A
from trading_robot.risk import RiskEngine,RiskPolicy,RiskSnapshot,RiskState
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as S,_transition_pair

AT='2026-09-29T10:02:00.000000000Z'
START='2026-09-29T10:00:00+00:00'


def case(flow=100, fee=2, loss=80, **risk_kw):
    # Pure domain fixture: external observations are tested by integration, not here.
    from trading_robot.versioned_risk_resync import _adjust_risk
    anchor=RiskState(daily_date='2026-09-29',weekly_key='2026-W40',daily_start_equity_rub=1000,
        weekly_start_equity_rub=1100,high_watermark_equity_rub=1200,last_cash_rub=500,
        last_snapshot_at=START,daily_order_count=2,daily_turnover_rub=220,recorded_execution_ids=('first','second'))
    equity=1000-loss+flow-fee; cash=500+flow-fee
    current=replace(anchor,risk_resync_required=True,risk_resync_source='EXTERNAL_CASH_CHANGE',
        risk_resync_cash_before_rub=500,risk_resync_cash_observed_rub=cash,
        last_equity_rub=equity,last_snapshot_at=AT,**risk_kw)
    portfolio=SimpleNamespace(snapshot_at=AT,account=SimpleNamespace(total_value=equity,
        cash=lambda currency:SimpleNamespace(available=cash)))
    entries=[]
    if flow:
        tx=_transaction(C.DEPOSIT if flow>0 else C.WITHDRAWAL,A.EQUITY_EXTERNAL_FLOW,int(flow*10**9),
                        effective_at='2026-09-29T10:01:00.000000000Z')
        entries.append({'transaction_json_ascii':tx.canonical_bytes.decode(),'transaction_sha256':tx.sha256})
    if fee:
        tx=_transaction(C.COMMISSION,A.EXPENSE_COMMISSION,-int(fee*10**9),source=_source(content='4'),
                        effective_at='2026-09-29T10:01:10.000000000Z')
        entries.append({'transaction_json_ascii':tx.canonical_bytes.decode(),'transaction_sha256':tx.sha256})
    events=[{'kind':'BATCH','at':AT,'body':{'entries':entries}},
            {'kind':'SNAPSHOT','at':AT,'risk':current.to_dict(),'portfolio':{'account':{'total_value':equity}}}]
    policy=RiskPolicy(daily_loss_limit_rub=50,daily_loss_limit_fraction=None,
                      weekly_loss_limit_rub=None,max_drawdown_fraction=None)
    return _adjust_risk,anchor,current,portfolio,events,policy


@pytest.mark.parametrize('flow',[100,0,-100,250])
def test_only_external_flow_shifts_baselines_and_fees_remain_loss(flow):
    derive,anchor,before,p,events,policy=case(flow)
    after,explanation=derive(anchor,before,p,events,policy,AT)
    assert after.daily_start_equity_rub==1000+flow
    assert after.weekly_start_equity_rub==1100+flow
    assert after.high_watermark_equity_rub==1200+flow
    assert after.last_equity_rub-after.daily_start_equity_rub==-82
    assert not after.risk_resync_required
    assert explanation['account_risk_decision']['risk_halted']
    assert 'DAILY_LOSS_LIMIT_RUB' in explanation['account_risk_decision']['breaches']
    assert after.daily_order_count==2 and after.daily_turnover_rub==220
    assert after.recorded_execution_ids==('first','second')
    assert explanation['external_flow_nano']==str(flow*10**9)
    assert explanation['ordinary_fee_effect_nano']=='-2000000000'


def test_raw_nav_high_watermark_is_not_shifted_twice():
    derive,anchor,before,p,events,policy=case(500,fee=0,loss=0)
    before=replace(before,high_watermark_equity_rub=1500)
    result,_=derive(anchor,before,p,events,policy,AT)
    assert result.high_watermark_equity_rub==1700 # pre-flow 1200 + flow 500, not 2000


def test_legacy_reset_is_not_used_as_flow_neutral_result():
    derive,anchor,before,p,events,policy=case()
    old,_=RiskEngine(policy).complete_external_cash_resync(before,now=datetime(2026,9,29,10,2,tzinfo=timezone.utc),
        equity_rub=p.account.total_value,cash_rub=p.account.cash('rub').available,
        snapshot_at=datetime(2026,9,29,10,2,tzinfo=timezone.utc))
    new,_=derive(anchor,before,p,events,policy,AT)
    assert p.account.total_value-old.daily_start_equity_rub==0
    assert p.account.total_value-new.daily_start_equity_rub==-82


def test_existing_operator_kill_switch_kept_exactly():
    derive,anchor,before,p,events,policy=case(kill_switch_active=True,kill_switch_reason='operator',
        kill_switch_source='MANUAL',kill_switch_set_at=START,kill_switch_operator_ref='operator-ref')
    after,_=derive(anchor,before,p,events,policy,AT)
    for name in ['kill_switch_active','kill_switch_reason','kill_switch_source','kill_switch_set_at','kill_switch_operator_ref']:
        assert getattr(after,name)==getattr(before,name)


@pytest.mark.parametrize('damage',['other_resync','already_clear','anchor_blocked','period','cash_anchor','execution',
                                  'nonpositive','duplicate','subkopeck','pre_anchor','trade'])
def test_refuse_unexplained_or_unsupported_resync(damage):
    from trading_robot.versioned_risk_resync import VerifiedCashFlowError
    derive,anchor,before,p,events,policy=case()
    if damage=='other_resync':before=replace(before,risk_resync_source='EXTERNAL_ACTIVITY')
    elif damage=='already_clear':before=replace(before,risk_resync_required=False)
    elif damage=='anchor_blocked':anchor=replace(anchor,risk_resync_required=True)
    elif damage=='period':anchor=replace(anchor,daily_date='2026-09-28')
    elif damage=='cash_anchor':before=replace(before,last_cash_rub=501)
    elif damage=='execution':before=replace(before,daily_order_count=3)
    elif damage=='nonpositive':anchor=replace(anchor,daily_start_equity_rub=-1)
    elif damage=='duplicate':events[0]['body']['entries']*=2
    elif damage in {'subkopeck','pre_anchor','trade'}:
        amount=1 if damage=='subkopeck' else 10**9
        tx=_transaction(C.TRADE_SETTLEMENT if damage=='trade' else C.DEPOSIT,
            A.ASSET_TRADE_CLEARING if damage=='trade' else A.EQUITY_EXTERNAL_FLOW,amount,
            effective_at='2026-09-29T09:59:00.000000000Z' if damage=='pre_anchor' else '2026-09-29T10:01:00.000000000Z')
        events[0]['body']['entries']=[{'transaction_json_ascii':tx.canonical_bytes.decode(),'transaction_sha256':tx.sha256}]
    with pytest.raises(VerifiedCashFlowError):derive(anchor,before,p,events,policy,AT)


def pair():
    before,_,m=_records()
    held=m._change(before,at=AT,kind='VERSIONED_CASH_FLOW_RESYNC_HELD',
        state=S.EXACT_CASH_VERSIONED_RISK_RESYNC_PENDING,pending_dispatch_proof_sha256='f'*64)
    after=m._change(held,at='2026-09-29T10:02:01.000000000Z',kind='VERSIONED_CASH_FLOW_RESYNC_COMMITTED',
        state=S.EXACT_CASH_VERSIONED_DISARMED,pending_dispatch_proof_sha256=None,activation_context_sha256='1'*64)
    return before,held,after


def test_transition_only_risk_context_changes():
    before,held,after=pair();_transition_pair(before,held);_transition_pair(held,after)
    assert before.ledger_revision==after.ledger_revision and before.post_attempt_count==after.post_attempt_count


@pytest.mark.parametrize('phase',['hold','commit'])
@pytest.mark.parametrize('field,value',[('ledger_revision',5),('ledger_head_sha256','9'*64),('post_attempt_count',2),
    ('opening_record_sha256','9'*64),('identity_key_id','OTHER'),('account_scope_sha256','8'*64),
    ('operations_complete_through',AT),('state',S.EXACT_CASH_ARMED)])
def test_transition_rejects_financial_mutation(phase,field,value):
    before,held,after=pair();left,right=(before,held) if phase=='hold' else (held,after)
    with pytest.raises(Exception):_transition_pair(left,replace(right,**{field:value}))


def test_backdated_flow_behind_an_observed_snapshot_is_not_applied_twice():
    from trading_robot.versioned_risk_resync import VerifiedCashFlowError
    derive,anchor,before,p,events,policy=case()
    earlier=dict(events[1],at='2026-09-29T10:01:30.000000000Z')
    events.insert(0,earlier)
    with pytest.raises(VerifiedCashFlowError,match='FLOW_OUTSIDE_ANCHOR_WINDOW'):
        derive(anchor,before,p,events,policy,AT)
