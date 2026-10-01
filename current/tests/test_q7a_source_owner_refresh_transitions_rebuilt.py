"""No-I/O authority contracts: owner refresh cannot change financial authority."""
from dataclasses import replace
import pytest
from test_q7a_source_selected_sync_transitions import _records
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState as State, _transition_pair


def pair():
    before,_,m=_records()
    held=m._change(before,at='2026-09-29T10:02:00.000000000Z',kind='VERSIONED_OWNER_REFRESH_HELD',
        state=State.EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING,pending_dispatch_proof_sha256='f'*64)
    after=m._change(held,at='2026-09-29T10:02:01.000000000Z',kind='VERSIONED_OWNER_REFRESH_COMMITTED',
        state=State.EXACT_CASH_VERSIONED_DISARMED,pending_dispatch_proof_sha256=None,activation_context_sha256='1'*64)
    return before,held,after


def test_owner_pair_keeps_cash_opening_and_attempts():
    before,held,after=pair();_transition_pair(before,held);_transition_pair(held,after)
    assert before.ledger_revision==after.ledger_revision and before.ledger_head_sha256==after.ledger_head_sha256
    assert before.post_attempt_count==after.post_attempt_count and before.opening_record_sha256==after.opening_record_sha256


@pytest.mark.parametrize('phase',['hold','commit'])
@pytest.mark.parametrize('field,value',[('ledger_revision',5),('ledger_head_sha256','9'*64),('post_attempt_count',2),
    ('opening_record_sha256','9'*64),('identity_key_id','OTHER'),('account_scope_sha256','8'*64),
    ('operations_complete_through','2026-09-29T10:02:00.000000000Z'),('state',State.EXACT_CASH_ARMED)])
def test_owner_pair_rejects_financial_mutation(phase,field,value):
    before,held,after=pair()
    left,right=(before,held) if phase=='hold' else (held,after)
    with pytest.raises(Exception):_transition_pair(left,replace(right,**{field:value}))
