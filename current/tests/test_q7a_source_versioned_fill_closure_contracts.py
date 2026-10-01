"""Finite closure transitions and prefix contracts. No trading or provider IO."""
from dataclasses import replace
from itertools import product
from types import SimpleNamespace

import pytest

from test_q7a_source_versioned_dispatch_contracts import records
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityManager, RuntimeCashAuthorityState as State, _transition_pair
from trading_robot import versioned_fill_closure as closure
from trading_robot.versioned_fee_evidence import _canonical, _sealed

AT='2026-09-29T10:02:03.000000000Z'
END='2026-09-29T10:02:04.000000000Z'


def pair(delta=2):
    pending=records()[2]
    m=RuntimeCashAuthorityManager.__new__(RuntimeCashAuthorityManager)
    after=m._change(pending,at=END,kind=closure.DONE,state=State.EXACT_CASH_VERSIONED_DISARMED,
        activation_context_sha256='4'*64,pending_dispatch_proof_sha256=None,
        ledger_revision=pending.ledger_revision+delta,ledger_head_sha256='5'*64,
        operations_complete_through=AT)
    return pending,after


@pytest.mark.parametrize('delta',[1,2])
def test_only_preposted_trade_and_optional_fee_head_advancement(delta):
    before,after=pair(delta);_transition_pair(before,after)
    for field in ('account_scope_sha256','identity_key_id','opening_cutoff','opening_record_sha256',
                  'post_attempt_count','cutover_generation','ever_exact_activated'):
        assert getattr(after,field)==getattr(before,field)
    assert after.state is State.EXACT_CASH_VERSIONED_DISARMED


@pytest.mark.parametrize('field,value',[
    ('state',State.EXACT_CASH_ARMED),('state',State.EXACT_CASH_VERSIONED_ARMED),('state',State.LEGACY_ACTIVE),
    ('post_attempt_count',0),('post_attempt_count',3),('account_scope_sha256','c'*64),
    ('identity_key_id','OTHER'),('opening_record_sha256','b'*64),('opening_cutoff','2026-09-28T00:00:00.000000000Z'),
    ('cutover_generation',99),('pending_dispatch_proof_sha256','9'*64),('activation_context_sha256',None),
    ('ledger_head_sha256',None),('ledger_revision',999),('previous_record_sha256','9'*64),
    ('record_revision',99),('operations_complete_through','2026-09-29T10:02:05.000000000Z'),
    ('transition_at','2026-09-29T09:59:59.000000000Z'),
])
def test_terminal_transition_cannot_rearm_reset_attempt_or_change_identity(field,value):
    before,after=pair()
    with pytest.raises(Exception):_transition_pair(before,replace(after,**{field:value}))


@pytest.mark.parametrize('delta',[0,3,8])
def test_unsupported_ledger_delta_does_not_close_authority(delta):
    with pytest.raises(Exception):
        before,after=pair(delta);_transition_pair(before,after)


@pytest.mark.parametrize('bits',list(product([False,True],repeat=3)))
def test_only_ordered_owner_prefixes(bits):
    names=('portfolio','risk','central')
    before={k:{'old':k} for k in names};after={k:{'new':k} for k in names}
    observed={k:(after[k] if b else before[k]) for k,b in zip(names,bits)}
    observed['authority']={'pending':True}
    allowed={(False,False,False):0,(True,False,False):1,(True,True,False):2,(True,True,True):3}
    if bits in allowed:
        assert closure._owner_phase(before,after,observed,{'pending':True})==allowed[bits]
    else:
        with pytest.raises(closure.VersionedFillClosureError):closure._owner_phase(before,after,observed,{'pending':True})


@pytest.mark.parametrize('phase',[0,1,2,3])
def test_terminal_authority_cannot_precede_any_owner(phase):
    names=('portfolio','risk','central');before={k:0 for k in names};after={k:1 for k in names}
    obs={k:int(i<phase) for i,k in enumerate(names)};obs['authority']={'closed':True}
    if phase==3:assert closure._owner_phase(before,after,obs,{'pending':True},{'closed':True})==4
    else:
        with pytest.raises(closure.VersionedFillClosureError):
            closure._owner_phase(before,after,obs,{'pending':True},{'closed':True})


@pytest.mark.parametrize('damage',['signature','field','domain','version','duplicate','path'])
def test_record_reader_refuses_untrusted_envelopes(tmp_path,damage):
    body={'domain':closure.DOMAIN,'version':1,'kind':'COMMITTED','plan_sha256':'1'*64,
          'dispatch_plan_sha256':'2'*64,'completed_at':END}
    key=bytes(32);path=tmp_path/'result.json'
    if damage=='field':body['extra']=True
    if damage=='domain':body['domain']='other'
    if damage=='version':body['version']=True
    raw=_sealed(body,key)
    if damage=='signature':raw=raw.replace(b'COMMITTED',b'ABANDONED')
    if damage=='duplicate':raw=raw.replace(b'"payload":{',b'"payload":{},"payload":{',1)
    path.write_bytes(raw)
    if damage=='path':
        link=tmp_path/'link.json';link.symlink_to(path);path=link
    with pytest.raises(Exception):closure._read(path,key,closure.RESULT_FIELDS)


def test_public_summary_never_grants_new_trading_or_fee_finality():
    x=closure.VersionedClosureResult('1'*64,'2'*64,'3'*64,'4'*64,7,False)
    d=x.public_summary()
    assert d['new_money_transactions']==0 and not d['post_order_called']
    assert not d['runtime_authority_granted'] and not d['automatic_rearm_allowed']
    assert not d['future_fee_finality_claimed']


@pytest.mark.parametrize('historical_file',['plan.json','result.json','index'])
def test_evidence_fence_includes_preceding_closure_records(tmp_path,historical_file):
    old=tmp_path/'versioned_fill_closure'/('1'*64)
    old.mkdir(parents=True)
    path=old/historical_file
    if historical_file=='index':
        path=tmp_path/'versioned_fill_closure/results'/('3'*64+'.json');path.parent.mkdir()
    path.write_text('{}')
    guard=closure._evidence_fence(tmp_path,'2'*64)
    path.write_text('{"changed":true}')
    with pytest.raises(closure.VersionedFillClosureError,match='HISTORICAL_EVIDENCE_CHANGED'):guard()


def test_evidence_fence_allows_only_current_result_index(tmp_path):
    root=tmp_path/'versioned_fill_closure';(root/'results').mkdir(parents=True)
    guard=closure._evidence_fence(tmp_path,'2'*64)
    own=root/('2'*64);own.mkdir();(own/'plan.json').write_text('{}')
    guard()  # Own plan is separately re-derived and checked by its exact digest.
    guard('3'*64)  # Durable result may precede its index after interruption.
    (root/'results'/('3'*64+'.json')).write_text('{}')
    with pytest.raises(closure.VersionedFillClosureError):guard()
    guard('3'*64)
    (root/'results'/('4'*64+'.json')).write_text('{}')
    with pytest.raises(closure.VersionedFillClosureError):guard('3'*64)
