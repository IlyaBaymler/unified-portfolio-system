"""STEP33: numeric clock boundary, native Central prepare CAS and authority limits."""
from dataclasses import replace
import pytest

from test_q7a_source_selected_sync_transitions import _records
from trading_robot import reporting_risk_cash_context as cl6
from trading_robot.central_order_manager import CentralOrderStore, CentralOrderState, CentralOrderConflictError
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State, _transition_pair


@pytest.mark.parametrize('raw,expected', [
 ('2026-09-29T12:00:00Z','2026-09-29T12:00:00.000000000Z'),
 ('2026-09-29T12:00:00.1Z','2026-09-29T12:00:00.100000000Z'),
 ('2026-09-29T12:00:00.123456Z','2026-09-29T12:00:00.123456000Z'),
 ('2026-09-29T12:00:00.123456789Z','2026-09-29T12:00:00.123456789Z'),
 ('2026-09-29T15:00:00.000000001+03:00','2026-09-29T12:00:00.000000001Z'),
 ('2026-09-29T00:00:00.999999999+03:00','2026-09-28T21:00:00.999999999Z'),
])
def test_portfolio_timestamp_exact_nanosecond_not_truncated(raw,expected):
    text,nano=cl6._normalize_portfolio_timestamp(raw)
    assert text==expected and nano==cl6._timestamp_ns(expected)


def test_one_nanosecond_outside_age_is_not_rounded_back_inside():
    start=cl6._normalize_portfolio_timestamp('2026-09-29T12:00:00.000000001Z')[1]
    end=cl6._normalize_portfolio_timestamp('2026-09-29T12:00:05.000000002Z')[1]
    assert end-start==5_000_000_001


@pytest.mark.parametrize('bad',['2026-09-29T12:00:00.1234567890Z','2026-09-29T12:00:00',
 '2026-09-29T12:00:00.-1Z','2026-09-29T12:00:00.123456789+24:00',
 '2026-09-29T12:00:60.000000001Z',True,None])
def test_nano_extension_still_rejects_invalid_timestamp(bad):
    with pytest.raises(cl6.CL6Error):cl6._normalize_portfolio_timestamp(bad)


def _pair():
    before,_,m=_records()
    held=m._change(before,at='2026-09-29T10:02:00.000000000Z',kind='VERSIONED_ORDER_ADMISSION_HELD',
        state=State.EXACT_CASH_VERSIONED_ADMISSION_PENDING,pending_dispatch_proof_sha256='f'*64)
    after=m._change(held,at='2026-09-29T10:02:01.000000000Z',kind='VERSIONED_ORDER_ADMISSION_COMMITTED',
        state=State.EXACT_CASH_VERSIONED_DISARMED,pending_dispatch_proof_sha256=None,activation_context_sha256='1'*64)
    return before,held,after


def test_admission_never_changes_cash_or_grants_armed_state():
    before,held,after=_pair();_transition_pair(before,held);_transition_pair(held,after)
    for field in ('ledger_head_sha256','ledger_revision','post_attempt_count','opening_record_sha256','operations_complete_through'):
        assert getattr(before,field)==getattr(after,field)
    assert after.state is State.EXACT_CASH_VERSIONED_DISARMED


@pytest.mark.parametrize('phase',[0,1])
@pytest.mark.parametrize('field,value',[('ledger_revision',5),('ledger_head_sha256','9'*64),
 ('post_attempt_count',2),('opening_record_sha256','9'*64),('identity_key_id','OTHER'),
 ('account_scope_sha256','8'*64),('state',State.EXACT_CASH_ARMED)])
def test_admission_transition_cannot_mint_financial_authority(phase,field,value):
    records=_pair()
    with pytest.raises(Exception):_transition_pair(records[phase],replace(records[phase+1],**{field:value}))


def test_native_prepare_callback_cannot_overwrite_concurrent_central(tmp_path):
    store=CentralOrderStore(tmp_path/'central.json'); initial=store.initialize('sandbox-account')
    observed=[]
    def transform(state):return replace(state,next_sequence=state.next_sequence+1), None
    def prepare(before,after):
        observed.append((before,after))
        store._save_unlocked(replace(before,revision=before.revision+1,next_sequence=10))
    with pytest.raises(CentralOrderConflictError,match='changed in admission prepare'):
        store.mutate('sandbox-account',transform,before_commit=prepare)
    assert len(observed)==1 and store.load().next_sequence==10


def test_native_prepare_failure_does_not_commit_central(tmp_path):
    store=CentralOrderStore(tmp_path/'central.json'); initial=store.initialize('sandbox-account')
    def fail(before,after):raise RuntimeError('prepare failed')
    with pytest.raises(RuntimeError,match='prepare failed'):
        store.mutate('sandbox-account',lambda s:(replace(s,next_sequence=s.next_sequence+1), None),before_commit=fail)
    assert store.load() == initial


@pytest.mark.parametrize('when',['2026-09-29T12:00:00.000000001Z','2026-09-29T11:59:54.999999999Z'])
def test_quote_future_or_stale_nanosecond_fails(when):
    from trading_robot.versioned_risk_admission import _quote,VersionedAdmissionError
    with pytest.raises(VersionedAdmissionError,match='QUOTE_STALE'):
        _quote([{'instrumentUid':'uid','time':when,'price':{'units':'105','nano':1}}],
            'uid','2026-09-29T12:00:00.000000000Z')


def test_quote_rounds_estimate_up_never_spends_on_truncated_price():
    from trading_robot.versioned_risk_admission import _quote
    value,stamp=_quote([{'instrumentUid':'uid','time':'2026-09-29T12:00:00Z',
        'price':{'units':'105','nano':1}}],'uid','2026-09-29T12:00:00.000000000Z')
    assert value==10501
