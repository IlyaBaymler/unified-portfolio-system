"""STEP34 fast contracts; no provider network, no user runtime or custody waiver."""
from dataclasses import replace
from types import SimpleNamespace
import pytest

from test_q7a_source_admission_contracts import _pair
from test_central_order_manager_v3_8 import candidate,authorization,portfolio_state,manager,save_portfolio,NOW
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State,RuntimeCashAuthorityManager,_transition_pair
from trading_robot import versioned_dispatch as dispatch

T='2026-09-29T10:02:02.000000000Z'


def records():
    before=_pair()[2];m=RuntimeCashAuthorityManager.__new__(RuntimeCashAuthorityManager)
    armed=m._change(before,at=T,kind=dispatch.ARM,state=State.EXACT_CASH_VERSIONED_ARMED,activation_context_sha256='2'*64)
    pending=m._change(armed,at=T,kind=dispatch.ATTEMPT,state=State.EXACT_CASH_VERSIONED_DISPATCH_PENDING,
        pending_dispatch_proof_sha256='3'*64,post_attempt_count=armed.post_attempt_count+1)
    disarmed=m._change(armed,at=T,kind=dispatch.DISARM,state=State.EXACT_CASH_VERSIONED_DISARMED)
    return before,armed,pending,disarmed


def test_one_attempt_does_not_change_ledger_or_reset_counts():
    before,armed,pending,revoked=records()
    _transition_pair(before,armed);_transition_pair(armed,pending);_transition_pair(armed,revoked)
    for field in ('ledger_head_sha256','ledger_revision','opening_record_sha256','operations_complete_through',
                  'account_scope_sha256','identity_key_id','cutover_generation'):
        assert getattr(before,field)==getattr(pending,field)
    assert pending.post_attempt_count==before.post_attempt_count+1 and revoked.post_attempt_count==before.post_attempt_count
    with pytest.raises(Exception):_transition_pair(pending,armed)
    with pytest.raises(Exception):_transition_pair(revoked,armed)


@pytest.mark.parametrize('phase',['arm','attempt','disarm'])
@pytest.mark.parametrize('field,value',[('ledger_revision',500),('ledger_head_sha256','9'*64),
    ('opening_record_sha256','8'*64),('post_attempt_count',0),('identity_key_id','OTHER'),
    ('account_scope_sha256','9'*64),('operations_complete_through','2026-09-29T10:03:00.000000000Z'),
    ('state',State.EXACT_CASH_ARMED),('transition_at','2026-09-29T09:59:59.000000000Z')])
def test_transition_cannot_mint_or_clear_unrelated_authority(phase,field,value):
    before,armed,pending,revoked=records();a,b={'arm':(before,armed),'attempt':(armed,pending),'disarm':(armed,revoked)}[phase]
    with pytest.raises(Exception):_transition_pair(a,replace(b,**{field:value}))


def queued(tmp_path):
    m=manager(tmp_path);c=replace(candidate(),order_type='MARKET')
    m.enqueue(c,authorization(portfolio_state()))
    return m,m.state()


def response(intent):
    c=intent.candidate
    return dict(orderRequestId=intent.intent_id,orderId='exchange-1',instrumentUid=c.instrument_id,
        accountId=c.account_id,orderType='ORDER_TYPE_MARKET',direction='ORDER_DIRECTION_'+c.direction,
        lotsRequested=str(c.requested_lots),lotsExecuted='0',executionReportStatus='EXECUTION_REPORT_STATUS_NEW',currency='rub')


@pytest.mark.parametrize('field,value',[('orderRequestId','other'),('orderId',''),('orderId','x\n'),
    ('orderId','я'),('orderId','x'*129),('accountId','foreign'),('instrumentUid','foreign'),
    ('direction','ORDER_DIRECTION_SELL'),('orderType','ORDER_TYPE_LIMIT'),('currency','USD'),
    ('lotsRequested',True),('lotsRequested','01'),('lotsRequested','2'),('lotsExecuted','2'),
    ('executionReportStatus','EXECUTION_REPORT_STATUS_UNSPECIFIED')])
def test_lookup_identity_and_quantity_are_not_inferred(tmp_path,field,value):
    _,central=queued(tmp_path);intent=central.queued[0];raw=response(intent);raw[field]=value
    with pytest.raises(Exception):dispatch._exchange(raw,intent,lookup=True)


@pytest.mark.parametrize('status,executed',[('NEW','0'),('FILL','1'),('CANCELLED','0'),('REJECTED','0')])
def test_lookup_identity_is_not_fill_or_settlement_permission(tmp_path,status,executed):
    _,central=queued(tmp_path);intent=central.queued[0];raw=response(intent)
    raw.update(executionReportStatus='EXECUTION_REPORT_STATUS_'+status,lotsExecuted=executed)
    assert dispatch._exchange(raw,intent,lookup=True)=='exchange-1'
    assert central.queued[0].executed_lots==0


@pytest.mark.parametrize('damage',['revision','next_sequence','time','quantity','risk','status'])
def test_full_central_prefix_rejects_unplanned_edits(tmp_path,damage):
    _,before=queued(tmp_path);at=dispatch.cl6._normalize_portfolio_timestamp(before.queued[0].created_at)[0]
    inflight=dispatch._inflight(before,'a'*64,at)
    good=dispatch._outcome(inflight,'SUBMITTED',at,'exchange-1')
    dispatch._central_prefix(before,good,'a'*64,at)
    with pytest.raises(Exception):
        if damage=='revision':bad=replace(good,revision=good.revision+1)
        elif damage=='next_sequence':bad=replace(good,next_sequence=good.next_sequence+1)
        elif damage=='time':bad=replace(good,updated_at='2026-08-13T12:00:01+00:00')
        elif damage=='quantity':bad=good.replace_intent(replace(good.blocking_intent,executed_lots=1))
        elif damage=='risk':bad=good.replace_intent(replace(good.blocking_intent,risk_execution_id='fake'))
        else:bad=replace(good,account_id='foreign')
        dispatch._central_prefix(before,bad,'a'*64,at)


def test_generic_reconciliation_blocks_v4_before_any_risk_record(tmp_path):
    m,before=queued(tmp_path);at=dispatch.cl6._normalize_portfolio_timestamp(before.queued[0].created_at)[0]
    state=dispatch._inflight(before,'a'*64,at)
    state=dispatch._outcome(state,'SUBMITTED',at,'exchange-1')
    m.store._save_unlocked(state);repo=save_portfolio(tmp_path)
    risk=SimpleNamespace(record_execution=lambda **k:pytest.fail('unexpected Risk write'))
    with pytest.raises(Exception,match='EXACT_SETTLEMENT_REQUIRED'):
        m.mark_reconciled(state.blocking_intent.intent_id,portfolio_repository=repo,outcome='FILLED',executed_lots=1,risk_runtime=risk)
    assert m.state()==state


def test_native_single_request_uses_market_no_margin_no_retry_no_redirect(monkeypatch):
    from trading_robot.tbank_sandbox import TBankSandboxClient
    c=TBankSandboxClient.__new__(TBankSandboxClient);calls=[]
    def post(self,*args,**kwargs):calls.append((args,kwargs));return {'orderId':'exchange'}
    monkeypatch.setattr(TBankSandboxClient,'_post',post)
    assert c.post_order_once('account','uid',1,'BUY',order_id='01234567-89ab-cdef-0123-456789abcdef',order_type='MARKET')=={'orderId':'exchange'}
    assert len(calls)==1
    args,kwargs=calls[0]
    assert args[:2]==('SandboxService','PostSandboxOrder') and args[2]['confirmMarginTrade'] is False
    assert args[2]['orderType']=='ORDER_TYPE_MARKET' and args[2]['quantity']=='1' and 'price' not in args[2]
    assert kwargs=={'retry_safe':False,'allow_redirects':False}


def test_deadline_preserves_nanosecond_boundary_and_rejects_backward_clock():
    now=['2026-09-29T10:02:02.000000001Z'];tick=[10]
    a=SimpleNamespace(cl7_clock=lambda:now[0],cl7_monotonic_ns=lambda:tick[0])
    d=dispatch._Deadline(a);now[0]='2026-09-29T10:02:07.000000001Z';tick[0]+=5_000_000_000;d.check()
    now[0]='2026-09-29T10:02:07.000000002Z'
    with pytest.raises(dispatch.VersionedDispatchError,match='EXPIRED'):d.check()
    now[0]='2026-09-29T10:02:02.000000001Z';tick[0]=10;d=dispatch._Deadline(a);tick[0]=9
    with pytest.raises(dispatch.VersionedDispatchError,match='EXPIRED'):d.check()
