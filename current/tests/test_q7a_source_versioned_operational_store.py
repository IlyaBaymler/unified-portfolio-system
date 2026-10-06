"""STEP26 ordinary writers over the complete versioned cash prefix. Synthetic IO."""
from __future__ import annotations

import importlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_versioned_financial_readers import read_case, _pins, _args
from test_q7a_source_settlement_closure import _snapshot
from trading_robot import broker_read_adapters as cl3
from trading_robot import cash_ledger_persistence as cl2
from trading_robot import versioned_financial_readers as readers


def _module():
    return importlib.import_module('trading_robot.versioned_operational_store')


def _later(value, seconds=1):
    return stamp(datetime.fromisoformat(value.replace('Z', '+00:00')) + timedelta(seconds=seconds))


@pytest.fixture
def op_case(read_case):
    c = read_case; m = _module()
    root = c.y.root.with_name('operational-v4')
    seed = c.store.export_bytes()
    cap = json.loads(json.loads(json.loads(seed)['correction_record_json_ascii'])['payload']['capture_json_ascii'])
    m.create_operational_store(seed, root, seed_pins=_pins(c), created_at=cap['captured_at'], **c.y.common)
    store = m.VersionedOperationalStore.open(root, **c.y.common)
    try:
        yield SimpleNamespace(c=c, m=m, root=root, store=store, seed=seed,
                              start=cap['operation_requests'][0]['from'], end=cap['captured_at'],
                              old_rows=[deepcopy(r) for page in cap['operation_responses'] for r in page['items']])
    finally:
        store.close()


def _operation(x, name='ordinary-1', kind='INPUT', amount='100', *, qty='0', at=None):
    return {'id':name, 'brokerAccountId':x.c.y.a.policy.account_id, 'cursor':'cursor-'+name,
        'date':at or x.end, 'type':'OPERATION_TYPE_'+kind, 'state':'OPERATION_STATE_EXECUTED',
        'quantity':qty, 'quantityDone':qty, 'quantityRest':'0', 'payment':money(amount),
        'commission':money(0), 'childOperations':[]}


def _request(x, rows, *, start=None, end=None, page_size=128, transform=None, clock=None):
    pages=[]
    for n in range(0, len(rows) or 1, page_size):
        subset=deepcopy(rows[n:n+page_size]); more=n+page_size<len(rows)
        pages.append({'items':subset,'hasNext':more,'nextCursor':f'PAGE-{len(pages)+1}' if more else ''})
    index=0
    def transport(payload, timeout):
        nonlocal index
        page=deepcopy(pages[index]); index+=1
        if transform is not None: page=transform(payload,page,index)
        return page
    return cl3.BrokerReadRequest(cl3.BrokerEnvironment.SANDBOX,x.c.y.a.policy.account_id,
        x.c.y.a.cl7_identity_key,x.c.y.a.cl7_identity_key_id,start or x.start,end or _later(x.end),
        page_size,4,128,5_000_000_000,cl3.RetryPolicy(1,5_000_000_000,()),transport,
        clock or (lambda:0),lambda _:None)


def _sync(x, rows, **kwargs):
    end=kwargs.get('end') or _later(x.end)
    cash_rows = kwargs.pop('cash_rows', rows)
    cash_override = kwargs.pop('cash_override', None)
    def read_cash(account):
        raw=x.c.y.x.p.get_positions(account)
        old_ids={r['id'] for r in x.old_rows}
        amount=x.c.store.snapshot().cash_nano+sum(cl3.money_value_to_money(r['payment']).minor_units
            for r in cash_rows if r['id'] not in old_ids)
        raw['money']=[money(Decimal(amount if cash_override is None else cash_override)/10**9)]
        return raw
    return x.store.sync_tbank_operations(_request(x,rows,**kwargs),expected_pins=x.store.snapshot().pins,
        recorded_at=end,read_rub_positions=read_cash)


@pytest.mark.parametrize('kind,amount,qty',[('INPUT','100','0'),('OUTPUT','-25','0'),
    ('BUY','-1050','10'),('SELL','1050','10'),('BROKER_FEE','-2.1','0')])
def test_ordinary_operation_uses_corrected_cash_once(op_case,kind,amount,qty,request):
    x=op_case; before=_snapshot(x.c.y.x),x.c.store.export_bytes(),x.c.y.review.export_bytes()
    start=x.store.snapshot(); op=_operation(x,kind=kind,amount=amount,qty=qty)
    result=_sync(x,x.old_rows+[op]); after=x.store.snapshot()
    assert after.cash_nano==998948565432110+int(Decimal(amount)*10**9)
    assert result.appended_observations==result.appended_transactions==1
    assert after.pins.ledger_revision==start.pins.ledger_revision+1
    assert after.pins.store_revision==start.pins.store_revision+1
    assert after.transaction_count==6 and after.pins.ledger_head_sha256!=start.pins.ledger_head_sha256
    final=x.store.export_bytes();again=_sync(x,x.old_rows+[op])
    assert again.replay and again.appended_transactions==0 and x.store.export_bytes()==final
    assert (_snapshot(x.c.y.x),x.c.store.export_bytes(),x.c.y.review.export_bytes())==before
    assert x.c.y.x.p.order_calls==1 and after.financial_ready is False
    request.node.user_properties.extend([('operation',kind),('cash_after_nano',after.cash_nano),
        ('cash_delta_nano',result.cash_delta_nano),('appended_transactions',1),
        ('source_runtime_unchanged',True),('fake_posts',1),('replay_unchanged',True)])


def test_five_operations_one_atomic_batch_and_context(op_case,request):
    x=op_case
    ops=[_operation(x,'deposit','INPUT','1000'),_operation(x,'withdrawal','OUTPUT','-250'),
         _operation(x,'buy','BUY','-1050',qty='10'),_operation(x,'sell','SELL','1050',qty='10'),
         _operation(x,'fee','BROKER_FEE','-2.1')]
    result=_sync(x,x.old_rows+ops)
    s=x.store.snapshot()
    assert result.appended_transactions==5 and result.appended_observations==5
    assert result.cash_delta_nano==747900000000
    assert s.cash_nano==999696465432110 and s.transaction_count==10
    assert s.pins.ledger_revision==9 and s.batch_count==1
    projection=x.m.project_operational_cash(x.store.export_bytes(),pins=s.pins,**x.c.y.common)
    assert projection.expected_cash_nano==s.cash_nano and projection.ordinary_delta_nano==747900000000
    positions=x.c.y.x.p.get_positions(x.c.y.a.policy.account_id)
    positions['money']=[money(Decimal(s.cash_nano)/10**9)]
    args=_args(x.c,positions=positions,budgets=(s.cash_nano,s.cash_nano),time=_later(x.end))
    args['pins']=s.pins
    context=readers.build_versioned_cash_context(x.store.export_bytes(),**args)
    body=json.loads(context.payload_bytes)
    assert body['source_export_version']==4 and body['status']=='CONSISTENT_REVIEW_ONLY',body
    assert body['expected_cash_nano']==str(s.cash_nano) and body['free_cash_nano']==str(s.cash_nano)
    assert body['cl7_status']=='BLOCKED_CUTOVER_REQUIRED' and body['runtime_authority_granted'] is False
    assert readers.validate_versioned_cash_context(context,x.store.export_bytes(),**args)==context
    with pytest.raises(readers.VersionedFinancialReadError,match='CUTOVER'):
        readers.require_versioned_runtime_authority(context)
    request.node.user_properties.extend([('five_operation_cash_nano',s.cash_nano),
        ('net_ordinary_delta_nano',result.cash_delta_nano),('transaction_count',s.transaction_count),
        ('ledger_revision',s.pins.ledger_revision),('batch_count',s.batch_count),('cutover_performed',False)])


def test_multiple_batches_overlap_and_empty_rescan_preserve_money(op_case):
    x=op_case; deposit=_operation(x,'deposit','INPUT','100')
    first=_sync(x,x.old_rows+[deposit]); s1=x.store.snapshot()
    withdrawal=_operation(x,'withdrawal','OUTPUT','-25',at=_later(x.end))
    rows=x.old_rows+[deposit,withdrawal]
    second=_sync(x,rows,end=_later(x.end,2));s2=x.store.snapshot()
    assert second.appended_transactions==1 and s2.cash_nano==s1.cash_nano-25*10**9
    assert s2.pins.ledger_revision==s1.pins.ledger_revision+1
    assert s2.pins.store_revision==s1.pins.store_revision+1
    third=_sync(x,rows,end=_later(x.end,3));s3=x.store.snapshot()
    assert third.appended_observations==third.appended_transactions==0
    assert s3.cash_nano==s2.cash_nano and s3.pins.ledger_head_sha256==s2.pins.ledger_head_sha256
    assert s3.pins.store_revision==s2.pins.store_revision+1
    exported=x.store.export_bytes()
    assert _sync(x,rows,end=_later(x.end,3)).replay
    assert x.store.export_bytes()==exported and first.pins.seed_export_sha256==s3.pins.seed_export_sha256


def test_multipage_capture_and_cursor_change_do_not_duplicate(op_case):
    x=op_case; rows=x.old_rows+[_operation(x,'input-a')]
    first=_sync(x,rows,page_size=1)
    assert first.appended_transactions==1
    prior=x.store.snapshot()
    for row in rows: row['cursor']='new-'+row['cursor']
    result=_sync(x,rows,page_size=1,end=_later(x.end,2))
    assert result.appended_transactions==result.appended_observations==0
    assert x.store.snapshot().pins.ledger_head_sha256==prior.pins.ledger_head_sha256


@pytest.mark.parametrize('damage',['old_version','same_id_new_amount','state_change','raw_instrument',
    'alias','missing_previous','duplicate','pre_seed','partial','parent_fee','embedded_fee','unknown_type',
    'foreign_currency','wrong_account','cancel_with_fill','cancel_with_payment','negative_cash'])
def test_bad_or_ambiguous_operation_never_partially_writes(op_case,damage):
    x=op_case; rows=deepcopy(x.old_rows)+[_operation(x)]
    if damage=='old_version': rows[1]['payment']=money('-0.123456789')
    elif damage=='same_id_new_amount': rows[1]['payment']=money('-0.3')
    elif damage=='state_change': rows[1]['state']='OPERATION_STATE_CANCELED'
    elif damage=='raw_instrument': rows[0]['instrumentUid']='wrong-uid'
    elif damage=='alias':
        alias=deepcopy(rows[1]);alias['id']='alias-fee';alias['cursor']='alias-cursor';rows.append(alias)
    elif damage=='missing_previous':rows.pop(0)
    elif damage=='duplicate':rows.append(deepcopy(rows[-1]))
    elif damage=='pre_seed': rows[-1]['date']=_later(x.end,-0.000001)
    elif damage=='partial': rows[-1]=_operation(x,kind='BUY',amount='-100',qty='10');rows[-1]['quantityDone']='5';rows[-1]['quantityRest']='5'
    elif damage=='parent_fee':rows[-1]=_operation(x,kind='BROKER_FEE',amount='-1');rows[-1]['parentOperationId']='p'
    elif damage=='embedded_fee':rows[-1]['commission']=money('1')
    elif damage=='unknown_type':rows[-1]['type']='OPERATION_TYPE_DIVIDEND'
    elif damage=='foreign_currency': rows[-1]['payment']['currency']='USD'
    elif damage=='wrong_account': rows[-1]['brokerAccountId']='OTHER-PRIVATE-ACCOUNT'
    elif damage=='cancel_with_fill': rows[-1].update(state='OPERATION_STATE_CANCELED',payment=money(0),quantityDone='1',quantity='1')
    elif damage=='cancel_with_payment':rows[-1]['state']='OPERATION_STATE_CANCELED'
    else:rows[-1]=_operation(x,kind='OUTPUT',amount='-2000000')
    before=x.store.export_bytes(),_snapshot(x.c.y.x)
    with pytest.raises(x.m.OperationalStoreError):_sync(x,rows)
    assert (x.store.export_bytes(),_snapshot(x.c.y.x))==before


def test_zero_canceled_observation_is_nonledger_not_a_trade(op_case):
    x=op_case; row=_operation(x,amount='0');row['state']='OPERATION_STATE_CANCELED'
    before=x.store.snapshot()
    result=_sync(x,x.old_rows+[row]);after=x.store.snapshot()
    assert result.appended_observations==1 and result.appended_transactions==0
    assert after.cash_nano==before.cash_nano and after.transaction_count==before.transaction_count
    assert after.pins.ledger_head_sha256==before.pins.ledger_head_sha256
    body=json.loads(json.loads(x.store.export_bytes())['batches'][0]['canonical_json_ascii'])['payload']
    assert body['entries'][0]['decision_kind']=='NOT_LEDGER_RELEVANT'
    assert body['entries'][0]['transaction_json_ascii'] is None


@pytest.mark.parametrize('delta',[-1,1])
def test_one_nanoruble_balance_mismatch_blocks_entire_batch(op_case,delta):
    x=op_case;before=x.store.export_bytes()
    with pytest.raises(x.m.OperationalStoreError,match='BROKER_CASH_MISMATCH'):
        _sync(x,x.old_rows+[_operation(x)],cash_override=x.store.snapshot().cash_nano+100*10**9+delta)
    assert x.store.export_bytes()==before


@pytest.mark.parametrize('fault',['gap','regression','incomplete','empty_last_page','deadline','cash_io'])
def test_failed_collection_does_not_move_any_watermark(op_case,fault):
    x=op_case;before=x.store.export_bytes();kwargs={}
    if fault=='gap':kwargs['start']=_later(x.end,0.5)
    elif fault=='regression':kwargs['end']=_later(x.end,-0.5)
    elif fault=='incomplete':kwargs.update(page_size=1,transform=lambda payload,page,index:dict(page,hasNext=True,nextCursor='LOOP'))
    elif fault=='empty_last_page':kwargs.update(transform=lambda payload,page,index:dict(page,items=[],hasNext=True,nextCursor='MORE'))
    elif fault=='deadline':
        ticks=[0]
        kwargs['clock']=lambda:ticks[0]
        def expire(payload,page,index):
            ticks[0]=6_000_000_000
            return page
        kwargs['transform']=expire
    else:
        original=x.c.y.x.p.get_positions
        x.c.y.x.p.get_positions=lambda *_:(_ for _ in ()).throw(RuntimeError('PRIVATE provider error'))
    try:
        with pytest.raises(x.m.OperationalStoreError) as exc:_sync(x,x.old_rows+[_operation(x)],**kwargs)
        assert 'PRIVATE' not in str(exc.value)
        assert x.store.export_bytes()==before
    finally:
        if fault=='cash_io':x.c.y.x.p.get_positions=original


@pytest.mark.parametrize('field',['export_sha256','seed_export_sha256','batch_head_sha256','store_revision','ledger_revision','ledger_head_sha256'])
def test_external_pins_are_checked_before_provider_io(op_case,field):
    x=op_case;p=x.store.snapshot().pins
    wrong=replace(p,**{field:'a'*64 if field.endswith('sha256') else getattr(p,field)+1})
    def no_read(*_):pytest.fail('provider called with wrong pins')
    request=replace(_request(x,x.old_rows),transport=no_read)
    before=x.store.export_bytes()
    with pytest.raises(x.m.OperationalStoreError):
        x.store.sync_tbank_operations(request,expected_pins=wrong,recorded_at=_later(x.end),read_rub_positions=no_read)
    assert x.store.export_bytes()==before


@pytest.mark.parametrize('cut',['append.before_insert','append.after_insert','append.before_commit','append.after_commit'])
@pytest.mark.parametrize('reopen',[False,True])
def test_atomic_batch_cuts_never_leave_half_the_money(op_case,cut,reopen):
    x=op_case;rows=x.old_rows+[_operation(x,'input','INPUT','100'),_operation(x,'out','OUTPUT','-25')]
    before=x.store.export_bytes();s=x.store.snapshot()
    def fail(point):
        if point==cut:raise RuntimeError('injected')
    x.store._injector=fail
    with pytest.raises(x.m.OperationalStoreError):_sync(x,rows)
    x.store._injector=None
    if reopen:
        x.store.close();x.store=x.m.VersionedOperationalStore.open(x.root,**x.c.y.common)
    try:
        interim=x.store.snapshot()
        if cut=='append.after_commit':
            assert interim.cash_nano==s.cash_nano+75*10**9 and interim.transaction_count==s.transaction_count+2
        else:assert x.store.export_bytes()==before
        result=_sync(x,rows)
        assert result.replay==(cut=='append.after_commit')
        assert x.store.snapshot().cash_nano==s.cash_nano+75*10**9
        assert x.store.snapshot().transaction_count==s.transaction_count+2
        assert _sync(x,rows).replay
    finally:
        if reopen:x.store.close()


def test_real_writer_lock_and_no_provider_call_under_final_lock(op_case):
    x=op_case;db=x.root/'store.sqlite3';seen=[]
    def probe(point):
        if point=='append.after_insert':
            second=sqlite3.connect(db,timeout=0,isolation_level=None)
            try:
                with pytest.raises(sqlite3.OperationalError):second.execute('BEGIN IMMEDIATE')
                seen.append(point)
            finally:second.close()
    def during_read(payload,page,index):
        second=sqlite3.connect(db,timeout=0,isolation_level=None)
        second.execute('BEGIN IMMEDIATE');second.execute('ROLLBACK');second.close()
        return page
    x.store._injector=probe
    try:_sync(x,x.old_rows+[_operation(x)],transform=during_read)
    finally:x.store._injector=None
    assert seen==['append.after_insert']


def test_concurrent_cas_does_not_overwrite_other_valid_batch(op_case):
    x=op_case;outer_rows=x.old_rows+[_operation(x,'outer','INPUT','200')]
    inner_rows=x.old_rows+[_operation(x,'inner','INPUT','100')];ran=[]
    def race(payload,page,index):
        if not ran:
            ran.append(True)
            with x.m.VersionedOperationalStore.open(x.root,**x.c.y.common) as store:
                other=SimpleNamespace(**vars(x));other.store=store
                _sync(other,inner_rows)
        return page
    with pytest.raises(x.m.OperationalStoreError,match='PIN_MISMATCH'):_sync(x,outer_rows,transform=race)
    assert x.store.snapshot().cash_nano==998948565432110+100*10**9
    assert x.store.snapshot().batch_count==1


@pytest.mark.parametrize('damage',['money','transaction','provenance','parent','sequence','raw_capture','duplicate_batch','unknown_field'])
def test_resigned_false_export_is_rejected_semantically(op_case,damage):
    x=op_case;_sync(x,x.old_rows+[_operation(x)])
    from trading_robot.versioned_fee_evidence import _canonical,_sealed,_sha
    d=json.loads(x.store.export_bytes());row=d['batches'][0];b=json.loads(row['canonical_json_ascii'])['payload']
    if damage=='money':b['cash_after_nano']=str(int(b['cash_after_nano'])+1)
    elif damage=='transaction':b['entries'][0]['transaction_sha256']='1'*64
    elif damage=='provenance':b['entries'][0]['provenance']['observation_sha256']='2'*64
    elif damage=='parent':b['parent_sha256']='3'*64
    elif damage=='sequence':b['sequence']=True
    elif damage=='raw_capture':
        cap=json.loads(b['capture_json_ascii']);cap['responses'][0]['items'][-1]['payment']=money('101')
        b['capture_json_ascii']=_canonical(cap).decode();b['capture_sha256']=_sha(_canonical(cap))
    elif damage=='unknown_field':b['authority_granted']=True
    if damage=='duplicate_batch':d['batches'].append(deepcopy(row))
    else:
        raw=_sealed(b,x.c.y.a.cl7_identity_key);row['canonical_json_ascii']=raw.decode();row['sha256']=_sha(raw)
    raw=_canonical(d)
    with pytest.raises(x.m.OperationalStoreError):
        x.m.validate_operational_export(raw,pins=replace(x.store.snapshot().pins,export_sha256=_sha(raw)),**x.c.y.common)


@pytest.mark.parametrize('cut',['copy.before_commit','copy.after_commit','copy.before_promote','copy.after_promote'])
def test_backup_restore_publication_cuts(op_case,cut):
    x=op_case;_sync(x,x.old_rows+[_operation(x)])
    raw=x.store.export_bytes();p=x.store.snapshot().pins;target=x.root.with_name('restored-v4')
    def fail(point):
        if point==cut:raise RuntimeError('copy cut')
    with pytest.raises(RuntimeError):x.m.restore_operational_export(raw,target,pins=p,fault_injector=fail,**x.c.y.common)
    if cut!='copy.after_promote':
        assert not target.exists()
        x.m.restore_operational_export(raw,target,pins=p,**x.c.y.common)
    with x.m.VersionedOperationalStore.open(target,pins=p,**x.c.y.common) as restored:
        assert restored.export_bytes()==raw
    assert x.store.export_bytes()==raw


def test_old_snapshot_needs_new_external_pin_to_detect_rollback(op_case):
    x=op_case;old=x.store.export_bytes();oldpins=x.store.snapshot().pins
    _sync(x,x.old_rows+[_operation(x)]);current=x.store.snapshot().pins
    assert x.m.validate_operational_export(old,pins=oldpins,**x.c.y.common).cash_nano==998948565432110
    with pytest.raises(x.m.OperationalStoreError,match='PIN_MISMATCH'):
        x.m.validate_operational_export(old,pins=current,**x.c.y.common)


def test_immutable_rows_and_schema_checks(op_case):
    x=op_case;_sync(x,x.old_rows+[_operation(x)])
    with sqlite3.connect(x.root/'store.sqlite3') as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM cl2_v4_batch')
        with pytest.raises(sqlite3.IntegrityError):conn.execute('UPDATE cl2_v4_base SET seed=seed')
        conn.execute('DROP TRIGGER freeze_v4_batch_update')
    with pytest.raises(x.m.OperationalStoreError,match='SCHEMA_INVALID'):x.store.snapshot()


def test_old_consumers_and_direct_writers_do_not_grant_authority(op_case):
    x=op_case;raw=x.store.export_bytes()
    for method in ('append_transaction','append_observation','append_correction_bundle','require_runtime_authority'):
        with pytest.raises(x.m.OperationalStoreError):getattr(x.store,method)()
    with pytest.raises(Exception):cl2.CashLedgerStore.open(x.root,codec_registry=x.c.y.common['codec_registry'])
    from trading_robot import cash_ledger_opening_reconciliation as cl4
    with pytest.raises(Exception):cl4.project_shadow_cash(raw,account_scope_sha256=x.c.y.common['account_scope_sha256'],
        environment=cl3.BrokerEnvironment.SANDBOX,as_of=x.end,identity_key=x.c.y.common['identity_key'])
    with pytest.raises(readers.VersionedFinancialReadError):readers.project_versioned_cash(raw,pins=_pins(x.c),**x.c.y.common)
    assert x.store.export_bytes()==raw and x.c.y.x.p.order_calls==1


def test_v4_context_refuses_old_corrected_cash_without_new_postings(op_case):
    x=op_case;_sync(x,x.old_rows+[_operation(x)])
    args=_args(x.c,time=_later(x.end),budgets=(100*10**9,100*10**9));args['pins']=x.store.snapshot().pins
    result=readers.build_versioned_cash_context(x.store.export_bytes(),**args)
    b=json.loads(result.payload_bytes)
    assert b['status']=='BLOCKED' and b['reason']=='VERSIONED_CASH_MISMATCH' and b['free_cash_nano'] is None
    assert b['source_export_version']==4 and b['cl7_status']=='BLOCKED_CUTOVER_REQUIRED'


def test_alias_of_later_ordinary_operation_is_not_new_money(op_case):
    x=op_case;deposit=_operation(x,'first','INPUT','100')
    _sync(x,x.old_rows+[deposit]);before=x.store.export_bytes()
    alias=deepcopy(deposit);alias['id']='second-id';alias['cursor']='second-cursor'
    with pytest.raises(x.m.OperationalStoreError,match='POSSIBLE_ALIAS'):
        _sync(x,x.old_rows+[alias],end=_later(x.end,2))
    assert x.store.export_bytes()==before


def test_append_capacity_is_explicit_not_silent_history_truncation(op_case,monkeypatch):
    x=op_case;monkeypatch.setattr(x.m,'MAX_BATCHES',1)
    rows=x.old_rows+[_operation(x)];_sync(x,rows);before=x.store.export_bytes()
    with pytest.raises(x.m.OperationalStoreError,match='CAPACITY'):
        _sync(x,rows,end=_later(x.end,2))
    assert x.store.export_bytes()==before


def test_bank_cash_with_blocked_amount_is_not_accepted(op_case):
    x=op_case;before=x.store.export_bytes()
    def positions(account):
        raw=x.c.y.x.p.get_positions(account);raw['blocked']=[money('1')]
        return raw
    with pytest.raises(x.m.OperationalStoreError):
        x.store.sync_tbank_operations(_request(x,x.old_rows),expected_pins=x.store.snapshot().pins,
            recorded_at=_later(x.end),read_rub_positions=positions)
    assert x.store.export_bytes()==before


def test_invalid_registration_time_is_not_hidden_by_replay(op_case):
    x=op_case;rows=x.old_rows+[_operation(x)];_sync(x,rows);before=x.store.export_bytes()
    def forbidden(*_):pytest.fail('read before time validation')
    with pytest.raises(x.m.OperationalStoreError):
        x.store.sync_tbank_operations(replace(_request(x,rows),transport=forbidden),
            expected_pins=x.store.snapshot().pins,recorded_at='invalid',read_rub_positions=forbidden)
    assert x.store.export_bytes()==before
