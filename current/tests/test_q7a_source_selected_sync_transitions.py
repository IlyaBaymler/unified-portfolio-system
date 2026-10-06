"""STEP30 transition contracts, without provider or filesystem fixtures."""
from dataclasses import replace

import pytest

from trading_robot.runtime_cash_authority import (
    RuntimeCashAuthorityManager, RuntimeCashAuthorityRecord,
    RuntimeCashAuthorityState as State, _transition_pair,
)


def _records():
    before=RuntimeCashAuthorityRecord(account_scope_sha256='a'*64,activation_context_sha256='b'*64,
        cutover_generation=1,environment='SANDBOX',ever_exact_activated=True,identity_key_id='TEST',
        ledger_head_sha256='c'*64,ledger_revision=4,opening_cutoff='2026-09-29T10:00:00.000000000Z',
        opening_record_sha256='d'*64,operations_complete_through='2026-09-29T10:01:00.000000000Z',
        pending_dispatch_proof_sha256=None,post_attempt_count=1,previous_record_sha256='e'*64,
        record_revision=10,state=State.EXACT_CASH_VERSIONED_DISARMED,
        transition_at='2026-09-29T10:01:00.000000000Z',transition_kind='VERSIONED_SOURCE_SELECTED_DISARMED')
    m=RuntimeCashAuthorityManager.__new__(RuntimeCashAuthorityManager)
    held=m._change(before,at='2026-09-29T10:02:00.000000000Z',kind='VERSIONED_SELECTED_SYNC_HELD',
        state=State.EXACT_CASH_VERSIONED_SYNC_PENDING,pending_dispatch_proof_sha256='f'*64)
    return before,held,m


def test_real_transition_allows_no_cash_rescan_and_preserves_attempt():
    before,held,m=_records();_transition_pair(before,held)
    after=m._change(held,at='2026-09-29T10:02:01.000000000Z',kind='VERSIONED_SELECTED_SYNC_COMMITTED',
        state=State.EXACT_CASH_VERSIONED_DISARMED,pending_dispatch_proof_sha256=None,
        activation_context_sha256='1'*64,operations_complete_through='2026-09-29T10:02:00.000000000Z')
    _transition_pair(held,after)
    assert after.ledger_revision==before.ledger_revision and after.post_attempt_count==1


@pytest.mark.parametrize('damage',['attempt','opening','key','account','armed','same_activation','lost_pending'])
def test_held_transition_cannot_change_financial_authority(damage):
    before,held,m=_records()
    changes={'attempt':{'post_attempt_count':2},'opening':{'opening_record_sha256':'0'*64},
             'key':{'identity_key_id':'OTHER'},'account':{'account_scope_sha256':'0'*64},
             'armed':{'state':State.EXACT_CASH_ARMED},
             'same_activation':{'activation_context_sha256':'0'*64},
             'lost_pending':{'pending_dispatch_proof_sha256':None}}[damage]
    with pytest.raises(Exception):_transition_pair(before,replace(held,**changes))


@pytest.mark.parametrize('damage',['changed_attempt','wrong_head','backward_revision','too_many','future_window',
                                  'armed','same_activation','opening','before_kind'])
def test_committed_transition_rejects_unsupported_changes(damage):
    before,held,m=_records()
    after=m._change(held,at='2026-09-29T10:02:01.000000000Z',kind='VERSIONED_SELECTED_SYNC_COMMITTED',
        state=State.EXACT_CASH_VERSIONED_DISARMED,pending_dispatch_proof_sha256=None,
        activation_context_sha256='1'*64,ledger_head_sha256='2'*64,ledger_revision=5,
        operations_complete_through='2026-09-29T10:02:00.000000000Z')
    _transition_pair(held,after)
    change={'changed_attempt':{'post_attempt_count':2},'wrong_head':{'ledger_head_sha256':held.ledger_head_sha256},
        'backward_revision':{'ledger_revision':3},'too_many':{'ledger_revision':133},
        'future_window':{'operations_complete_through':'2026-09-29T10:03:00.000000000Z'},
        'armed':{'state':State.EXACT_CASH_ARMED},'same_activation':{'activation_context_sha256':held.activation_context_sha256},
        'opening':{'opening_record_sha256':'0'*64},'before_kind':{}}[damage]
    if damage=='before_kind':held=replace(held,transition_kind='VERSIONED_SOURCE_CUTOVER_HELD')
    with pytest.raises(Exception):_transition_pair(held,replace(after,**change))


def test_aborted_transition_cannot_adopt_money():
    _,held,m=_records()
    after=m._change(held,at='2026-09-29T10:02:01.000000000Z',kind='VERSIONED_SELECTED_SYNC_ABORTED',
        state=State.EXACT_CASH_VERSIONED_DISARMED,pending_dispatch_proof_sha256=None,
        activation_context_sha256='1'*64)
    _transition_pair(held,after)
    with pytest.raises(Exception):_transition_pair(held,replace(after,ledger_head_sha256='0'*64,ledger_revision=5))
