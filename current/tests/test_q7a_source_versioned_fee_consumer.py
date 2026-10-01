"""STEP24 detached monetary consumer; all provider IO is synthetic."""
from __future__ import annotations

import hashlib
import importlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_fee_replacement import _setup
from test_q7a_source_cash_components import _export
from test_q7a_source_settlement_closure import _snapshot
from trading_robot import cash_observation_versions as versions
from trading_robot import cash_ledger_persistence as cl2
from trading_robot import broker_read_adapters as cl3


def _module():
    return importlib.import_module('trading_robot.versioned_fee_corrections')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _prepare(x, *, amount='0.2'):
    # A real closed exact BUY and known fee. Only provider contents then change.
    intent, raw, trade, fee, proof, target = _setup(x, amount=amount)
    fee['id'] = x.old_fee['id']
    fee['cursor'] = x.old_fee['cursor']
    adapter = x.c.execution_adapter
    common = dict(codec_registry=tuple(adapter.cl7_ledger_store._registry.values()),
        identity_key=adapter.cl7_identity_key, identity_key_id=adapter.cl7_identity_key_id,
        account_scope_sha256=adapter.cash_authority_manager.status().account_scope_sha256)
    root = x.temp/'version-review' if hasattr(x, 'temp') else adapter.manager.store.path.parent/'version-review'
    versions.copy_v1_for_revision_review(adapter.cl7_ledger_store.root, root, **common,
        expected_source_export_sha256=_sha(_export(x)), created_at=stamp(x.p.clock_at-timedelta(seconds=1)))
    review = versions.ObservationVersionStore.open(root, **common)
    return SimpleNamespace(x=x, a=adapter, intent=intent, raw=raw, trade=trade, fee=fee,
        proof=proof, target=target, common=common, review=review, root=root,
        recovery=x.c.cycle_source.portfolio_recovery)


def _capture(y):
    evidence = importlib.import_module('trading_robot.versioned_fee_evidence')
    return evidence.capture_same_id_fee_revision(y.a, recovery=y.recovery, review_store=y.review,
        proof_sha256=y.proof, original_transaction_sha256=y.target)


@pytest.mark.parametrize('amount', ['0.2','0.001'])
def test_capture_core(exact_case, amount):
    y = _prepare(exact_case, amount=amount)
    try:
        before = _snapshot(y.x)
        captured = _capture(y)
        assert captured.evaluation.cash_delta_nano == 123456789-int(Decimal(amount)*10**9)
        assert captured.evaluation.observation.logical_source_sha256 == y.review._graph().tips[captured.evaluation.observation.logical_source_sha256][0].logical_source_sha256
        assert _snapshot(y.x) == before
    finally:
        y.review.close()


def _register_and_create(y, captured):
    e = captured.evaluation
    result = y.review.append_observed_version(e.observation,
        expected_previous_observation_sha256=e.revision_evidence.previous_observation_sha256,
        expected_store_revision=captured.expected_store_revision,
        expected_version_head_sha256=captured.expected_version_head_sha256,
        evidence=e.revision_evidence)
    frozen = y.review.export_bytes()
    target = y.root.with_name('detached-correction')
    mod = _module()
    mod.create_fee_correction_review(frozen, target, **y.common,
        expected_review_export_sha256=_sha(frozen), expected_version_head_sha256=result.record_sha256,
        created_at=e.revision_evidence.recorded_at)
    store = mod.VersionedFeeCorrectionStore.open(target, **y.common)
    snapshot = store.snapshot()
    args = dict(original_transaction_sha256=y.target,
        expected_store_revision=snapshot.store_revision,
        expected_review_export_sha256=snapshot.review_export_sha256,
        expected_correction_head_sha256=snapshot.correction_head_sha256)
    return store, args


@pytest.mark.parametrize('amount', ['0.2','0.001'])
def test_same_id_cash_correction_core(exact_case, amount, request):
    y = _prepare(exact_case, amount=amount)
    try:
        captured = _capture(y)
        with _register_and_create(y, captured)[0] as store:
            initial = store.snapshot()
            args = dict(original_transaction_sha256=y.target, expected_store_revision=initial.store_revision,
                expected_review_export_sha256=initial.review_export_sha256,
                expected_correction_head_sha256=initial.correction_head_sha256)
            source, review = _snapshot(y.x), y.review.export_bytes()
            result = store.append_verified_fee_revision(captured.capture, **args)
            final = store.snapshot()
            assert final.cash_nano == 998948641975321 + 123456789 - int(Decimal(amount)*10**9)
            assert result.appended_transactions == 2 and not result.replay
            assert final.transaction_count == 5 and final.ledger_revision == initial.ledger_revision+1
            assert final.store_revision == initial.store_revision+1 and final.correction_recorded
            assert final.financial_ready is False and final.runtime_cutover_performed is False
            assert _snapshot(y.x) == source and y.review.export_bytes() == review
            exported = store.export_bytes()
            payload = json.loads(json.loads(exported)['correction_record_json_ascii'])['payload']
            assert payload['version_record_sha256'] == y.review.snapshot().version_head_sha256
            reversal = json.loads(payload['reversal_json_ascii'])
            corrected = json.loads(payload['correction_json_ascii'])
            assert reversal['reversal_of_sha256'] == corrected['corrects_sha256'] == y.target
            assert reversal['source'] == corrected['source'] == captured.evaluation.observation.source.to_canonical_dict()
            assert payload['provenance']['observation_sha256'] == captured.evaluation.observation.sha256
            assert store.append_verified_fee_revision(captured.capture, **args).replay
            assert store.export_bytes() == exported
            request.node.user_properties.extend([
                ('same_provider_id', True), ('new_cash_nano', final.cash_nano),
                ('cash_delta_nano', result.cash_delta_nano), ('appended_transactions', 2),
                ('source_runtime_unchanged', True), ('version_review_unchanged', True),
                ('financial_ready', False), ('risk_execution_count', 1),
                ('fake_posts', y.x.p.order_calls), ('replay_bytes_unchanged', True),
            ])
    finally:
        y.review.close()


# These corrupt captured primary bytes after a valid capture, proving the
# economic consumer reinterprets them instead of trusting the report hash.
@pytest.mark.parametrize('damage', [
    'amount', 'fee_parent', 'fee_instrument', 'fee_id', 'fee_time', 'fee_quantity',
    'fee_trades', 'fee_children', 'fee_currency', 'fee_sign', 'fee_type', 'fee_state',
    'fee_summary', 'trade_id', 'trade_price', 'trade_instrument', 'trade_commission',
    'receipt_fee', 'receipt_service', 'receipt_trade', 'receipt_instrument', 'receipt_request',
    'receipt_exchange', 'cash', 'blocked', 'missing_fee', 'extra_operation', 'incomplete',
    'request_account', 'unused_page', 'account', 'instrument', 'expired_window',
    'closure_signature', 'cash_plan_signature', 'unknown_field',
])
def test_changed_primary_evidence_does_not_post(exact_case, damage):
    from trading_robot.versioned_fee_evidence import _canonical, evaluate_captured_fee_revision, VersionFeeEvidenceError
    y = _prepare(exact_case)
    try:
        c = _capture(y)
        with _register_and_create(y,c)[0] as store:
            before, original = store.export_bytes(), _snapshot(y.x)
            d = json.loads(c.capture)
            trade, fee = d['operation_responses'][0]['items']
            if damage == 'amount': fee['payment'] = money('-0.21')
            elif damage == 'fee_parent': fee['parentOperationId'] = 'SYNTHETIC_OTHER'
            elif damage == 'fee_instrument': fee['instrumentUid'] = 'SYNTHETIC_OTHER'
            elif damage == 'fee_id': fee['id'] = 'SYNTHETIC_ALIAS'
            elif damage == 'fee_time': fee['date'] = d['captured_at']
            elif damage == 'fee_quantity': fee['quantity'] = '1'
            elif damage == 'fee_trades': fee['tradesInfo'] = deepcopy(trade['tradesInfo'])
            elif damage == 'fee_children': fee['childOperations'] = [{'payment':money(-1)}]
            elif damage == 'fee_currency': fee['payment']['currency'] = 'USD'
            elif damage == 'fee_sign': fee['payment'] = money('0.2')
            elif damage == 'fee_type': fee['type'] = 'OPERATION_TYPE_SERVICE_FEE'
            elif damage == 'fee_state': fee['state'] = 'OPERATION_STATE_PROGRESS'
            elif damage == 'fee_summary': fee['commission'] = money(1)
            elif damage == 'trade_id': trade['id'] = fee['parentOperationId'] = 'SYNTHETIC_OTHER'
            elif damage == 'trade_price': trade['tradesInfo']['trades'][0]['price'] = money(1)
            elif damage == 'trade_instrument': trade['instrumentUid'] = 'SYNTHETIC_OTHER'
            elif damage == 'trade_commission': trade['commission'] = money('0.2')
            elif damage == 'receipt_fee': d['order_state']['executedCommission'] = money('0.21')
            elif damage == 'receipt_service': d['order_state']['serviceCommission'] = money(1)
            elif damage == 'receipt_trade': d['order_state']['stages'][0]['tradeId'] = 'SYNTHETIC_OTHER'
            elif damage == 'receipt_instrument': d['order_state']['instrumentUid'] = 'SYNTHETIC_OTHER'
            elif damage == 'receipt_request': d['order_state']['orderRequestId'] = 'SYNTHETIC_OTHER'
            elif damage == 'receipt_exchange': d['order_state']['orderId'] = 'SYNTHETIC_OTHER'
            elif damage == 'cash': d['rub_positions']['money'][0] = money(1)
            elif damage == 'blocked': d['rub_positions']['blocked'] = [money(1)]
            elif damage == 'missing_fee': d['operation_responses'][0]['items'].remove(fee)
            elif damage == 'extra_operation':
                other=deepcopy(fee); other.update(id='SYNTHETIC_EXTRA',cursor='SYNTHETIC_EXTRA'); d['operation_responses'][0]['items'].append(other)
            elif damage == 'incomplete': d['operation_responses'][0]['hasNext'] = True
            elif damage == 'request_account': d['operation_requests'][0]['accountId'] = 'SYNTHETIC_OTHER'
            elif damage == 'unused_page': d['operation_responses'].append(deepcopy(d['operation_responses'][0])); d['operation_requests'].append(deepcopy(d['operation_requests'][0]))
            elif damage == 'account': d['raw_account_id'] = 'SYNTHETIC_OTHER'
            elif damage == 'instrument': d['instrument']['lot_size'] = 1
            elif damage == 'expired_window': d['captured_at'] = '2026-10-31T10:00:00.000000000Z'
            elif damage in ('closure_signature','cash_plan_signature'):
                field = 'closure_json_ascii' if damage=='closure_signature' else 'cash_plan_json_ascii'
                plan=json.loads(d[field]); plan['hmac_sha256']='0'*64; d[field]=_canonical(plan).decode()
            elif damage == 'unknown_field': d['approved_by_caller'] = True
            bad = _canonical(d)
            # Also exercise the pure economic join, independently of the version evidence hash.
            with pytest.raises(VersionFeeEvidenceError):
                evaluate_captured_fee_revision(y.x.prior_export, bad,
                    original_transaction_sha256=y.target, **y.common)
            s=store.snapshot()
            with pytest.raises((VersionFeeEvidenceError, _module().VersionFeeCorrectionError)):
                store.append_verified_fee_revision(bad, original_transaction_sha256=y.target,
                    expected_store_revision=s.store_revision, expected_review_export_sha256=s.review_export_sha256,
                    expected_correction_head_sha256=s.correction_head_sha256)
            assert store.export_bytes()==before and _snapshot(y.x)==original
    finally:
        y.review.close()


@pytest.mark.parametrize('field', ['request_sha256','response_sha256','binding_report_sha256'])
def test_opaque_version_evidence_is_not_a_monetary_permission(exact_case, field):
    y=_prepare(exact_case)
    try:
        c=_capture(y)
        changed=replace(c.evaluation.revision_evidence, **{field:'a'*64})
        y.review.append_observed_version(c.evaluation.observation,
            expected_previous_observation_sha256=changed.previous_observation_sha256,
            expected_store_revision=c.expected_store_revision,expected_version_head_sha256=c.expected_version_head_sha256,
            evidence=changed)
        mod=_module(); frozen=y.review.export_bytes(); target=y.root.with_name('wrong-report')
        mod.create_fee_correction_review(frozen,target,**y.common,expected_review_export_sha256=_sha(frozen),
            expected_version_head_sha256=y.review.snapshot().version_head_sha256,created_at=changed.recorded_at)
        with mod.VersionedFeeCorrectionStore.open(target,**y.common) as store:
            s=store.snapshot(); before=store.export_bytes()
            with pytest.raises(mod.VersionFeeCorrectionError,match='VERSION_ECONOMIC_BINDING_INVALID'):
                store.append_verified_fee_revision(c.capture,original_transaction_sha256=y.target,
                    expected_store_revision=s.store_revision,expected_review_export_sha256=s.review_export_sha256,
                    expected_correction_head_sha256=s.correction_head_sha256)
            assert store.export_bytes()==before
    finally:
        y.review.close()


@pytest.mark.parametrize('damage',['revision','head','review_hash','bool_revision','target'])
def test_compare_and_swap_or_wrong_target_is_noop(exact_case,damage):
    y=_prepare(exact_case)
    try:
        c=_capture(y); store,args=_register_and_create(y,c)
        with store:
            before=store.export_bytes()
            if damage=='revision': args['expected_store_revision']+=1
            elif damage=='bool_revision': args['expected_store_revision']=True
            elif damage=='head': args['expected_correction_head_sha256']='0'*64
            elif damage=='review_hash': args['expected_review_export_sha256']='0'*64
            elif damage=='target': args['original_transaction_sha256']='0'*64
            with pytest.raises(Exception): store.append_verified_fee_revision(c.capture,**args)
            assert store.export_bytes()==before
    finally: y.review.close()


@pytest.mark.parametrize('point',['correction.before_insert','correction.after_insert','correction.before_commit','correction.after_commit'])
@pytest.mark.parametrize('reopen',[False,True])
def test_atomic_bundle_cut_and_replay(exact_case,point,reopen):
    y=_prepare(exact_case)
    try:
        c=_capture(y); store,args=_register_and_create(y,c)
        before=store.export_bytes(); source=_snapshot(y.x)
        def fail(where):
            if where==point: raise RuntimeError('SYNTHETIC_CUT')
        store._injector=fail
        with pytest.raises(RuntimeError,match='SYNTHETIC_CUT'): store.append_verified_fee_revision(c.capture,**args)
        store._injector=None
        if point!='correction.after_commit': assert store.export_bytes()==before
        else: assert store.snapshot().transaction_count==5
        if reopen:
            root=store.root; store.close(); store=_module().VersionedFeeCorrectionStore.open(root,**y.common)
        try:
            result=store.append_verified_fee_revision(c.capture,**args)
            assert result.replay == (point=='correction.after_commit')
            assert store.snapshot().transaction_count==5
            final=store.export_bytes()
            assert store.append_verified_fee_revision(c.capture,**args).replay and store.export_bytes()==final
            assert _snapshot(y.x)==source
        finally: store.close()
    finally: y.review.close()


def test_writer_lock_and_second_connection_replay(exact_case):
    import sqlite3
    y=_prepare(exact_case)
    try:
        c=_capture(y); store,args=_register_and_create(y,c)
        second=_module().VersionedFeeCorrectionStore.open(store.root,**y.common)
        writer=sqlite3.connect(store.root/'store.sqlite3',timeout=0,isolation_level=None)
        try:
            writer.execute('BEGIN IMMEDIATE')
            with pytest.raises(_module().VersionFeeCorrectionError,match='STORE_BUSY'): store.append_verified_fee_revision(c.capture,**args)
            writer.execute('ROLLBACK')
            hits=[]
            def locked(where):
                if where=='correction.after_insert':
                    with pytest.raises(sqlite3.OperationalError): writer.execute('BEGIN IMMEDIATE')
                    hits.append(where)
            store._injector=locked
            store.append_verified_fee_revision(c.capture,**args)
            assert hits==['correction.after_insert']
            assert second.append_verified_fee_revision(c.capture,**args).replay
            assert second.export_bytes()==store.export_bytes()
        finally:
            writer.close(); second.close(); store.close()
    finally: y.review.close()


@pytest.mark.parametrize('damage',['version','head','observation','bundle','reversal','correction','cash_report','capture','ready','base','row_sha','schema'])
def test_complete_export_or_database_corruption_is_rejected(exact_case,damage):
    from trading_robot.versioned_fee_evidence import _canonical, _sealed
    import sqlite3
    y=_prepare(exact_case)
    try:
        c=_capture(y); store,args=_register_and_create(y,c)
        with store:
            store.append_verified_fee_revision(c.capture,**args)
            raw=store.export_bytes(); d=json.loads(raw); doc=json.loads(d['correction_record_json_ascii']);p=doc['payload']
            if damage=='version': p['version_record_sha256']='0'*64
            elif damage=='head': p['parent_sha256']='0'*64
            elif damage=='observation': p['provenance']['observation_sha256']='0'*64
            elif damage=='bundle':
                b=json.loads(p['bundle_json_ascii']);b['original_sha256']='0'*64;p['bundle_json_ascii']=_canonical(b).decode()
            elif damage=='reversal': p['reversal_json_ascii']=p['correction_json_ascii']
            elif damage=='correction': p['correction_json_ascii']=p['reversal_json_ascii']
            elif damage=='cash_report':
                report=json.loads(p['report_json_ascii']); report['cash_after_nano']='1'; p['report_json_ascii']=_canonical(report).decode()
            elif damage=='capture':
                capture=json.loads(p['capture_json_ascii']);capture['order_state']['executedCommission']=money('0.21');p['capture_json_ascii']=_canonical(capture).decode()
            elif damage=='ready': d['financial_ready']=True
            elif damage=='base':
                base=json.loads(d['frozen_review_export_ascii']);base['version_head_sha256']='0'*64;d['frozen_review_export_ascii']=_canonical(base).decode()
            elif damage in ('row_sha','schema'):
                other=sqlite3.connect(store.root/'store.sqlite3',isolation_level=None)
                if damage=='schema': other.execute('CREATE TABLE unexpected(value BLOB)')
                else:
                    other.execute('DROP TRIGGER freeze_correction_update')
                    other.execute("UPDATE cl2_version_cash_correction SET sha256=?",('0'*64,))
                    other.execute("CREATE TRIGGER freeze_correction_update BEFORE UPDATE ON cl2_version_cash_correction BEGIN SELECT RAISE(ABORT,'IMMUTABLE_CORRECTION'); END")
                other.close()
                with pytest.raises(Exception): store.export_bytes()
                return
            # Re-sign malicious semantics: HMAC alone must NOT suffice.
            d['correction_record_json_ascii']=_sealed(p,y.common['identity_key']).decode()
            with pytest.raises(_module().VersionFeeCorrectionError):
                _module().validate_fee_correction_export(_canonical(d),**y.common)
            assert store.export_bytes()==raw
    finally: y.review.close()


def test_backup_restore_all_evidence_and_external_rollback_pin(exact_case):
    y=_prepare(exact_case)
    try:
        c=_capture(y); store,args=_register_and_create(y,c)
        with store:
            old=store.export_bytes()
            store.append_verified_fee_revision(c.capture,**args)
            final=store.export_bytes(); s=store.snapshot();mod=_module()
            restored=y.root.with_name('restored-correction')
            mod.restore_fee_correction_export(final,restored,**y.common,expected_export_sha256=_sha(final))
            with mod.VersionedFeeCorrectionStore.open(restored,**y.common,expected_correction_head_sha256=s.correction_head_sha256) as reopened:
                assert reopened.export_bytes()==final
                assert reopened.append_verified_fee_revision(c.capture,**args).replay
            assert mod.validate_fee_correction_export(old,**y.common).correction_recorded is False
            with pytest.raises(mod.VersionFeeCorrectionError,match='CORRECTION_PIN_MISMATCH'):
                mod.validate_fee_correction_export(old,**y.common,expected_correction_head_sha256=s.correction_head_sha256)
    finally: y.review.close()


@pytest.mark.parametrize('point',['copy.before_commit','copy.after_commit','copy.before_promote','copy.after_promote'])
def test_copy_failures_do_not_overwrite_source(exact_case,point):
    y=_prepare(exact_case)
    try:
        c=_capture(y); store,args=_register_and_create(y,c)
        with store:
            store.append_verified_fee_revision(c.capture,**args)
            raw=store.export_bytes();source=_snapshot(y.x);target=y.root.with_name('copy-cut');mod=_module()
            def fail(where):
                if where==point: raise RuntimeError('SYNTHETIC_CUT')
            with pytest.raises(RuntimeError,match='SYNTHETIC_CUT'):
                mod.restore_fee_correction_export(raw,target,**y.common,expected_export_sha256=_sha(raw),fault_injector=fail)
            assert target.exists()==(point=='copy.after_promote')
            if target.exists():
                with mod.VersionedFeeCorrectionStore.open(target,**y.common) as reopened: assert reopened.export_bytes()==raw
            assert store.export_bytes()==raw and _snapshot(y.x)==source
    finally: y.review.close()


def test_old_readers_and_generic_writers_reject_new_format(exact_case):
    from trading_robot import cash_ledger_opening_reconciliation as cl4
    from trading_robot import cash_availability as cl5
    from trading_robot import reporting_risk_cash_context as cl6
    y=_prepare(exact_case)
    try:
        c=_capture(y);store,args=_register_and_create(y,c)
        with store:
            store.append_verified_fee_revision(c.capture,**args)
            raw=store.export_bytes()
            with pytest.raises(cl2.PersistenceError):cl2.CashLedgerStore.open(store.root,y.common['codec_registry'])
            with pytest.raises(versions.ObservationVersionError):versions.ObservationVersionStore.open(store.root,**y.common)
            with pytest.raises(cl4.CL4Error):cl4._parse_ledger_export(raw,target_account=y.common['account_scope_sha256'],identity_key=y.common['identity_key'])
            for method in ('append_transaction','append_correction_bundle'):
                with pytest.raises(_module().VersionFeeCorrectionError):getattr(store,method)(object())
            assert store.snapshot().financial_ready is False
    finally:y.review.close()


@pytest.mark.parametrize('damage',['stale','owner_drift','wrong_proof','missing_receipt','pure_alias','zero','same_amount'])
def test_capture_refuses_unbound_or_changed_source_without_runtime_writes(exact_case,damage):
    from trading_robot.versioned_fee_evidence import VersionFeeEvidenceError, capture_same_id_fee_revision
    y=_prepare(exact_case)
    try:
        before=_snapshot(y.x);review=y.review.export_bytes();a=y.a
        proof=y.proof
        if damage=='wrong_proof': proof='0'*64
        elif damage=='missing_receipt': y.raw.pop('executedCommission')
        elif damage=='pure_alias': y.fee['id']='SYNTHETIC_ALIAS'
        elif damage=='zero': y.raw['executedCommission']=money(0);y.fee['payment']=money(0)
        elif damage=='same_amount':
            y.raw['executedCommission']=deepcopy(y.x.old_commission);y.fee['payment']=deepcopy(y.x.old_fee['payment']);y.x.p.wallet_rub=y.x.old_wallet
        elif damage in ('stale','owner_drift'):
            real=a.transport.get_order_state
            def changed(*args,**kwargs):
                answer=real(*args,**kwargs)
                if damage=='stale': y.x.p.clock_at+=timedelta(seconds=6)
                else: a.cl7_identity_key=b'Z'*32
                return answer
            a.transport.get_order_state=changed
        with pytest.raises(VersionFeeEvidenceError):
            capture_same_id_fee_revision(a,recovery=y.recovery,review_store=y.review,
                proof_sha256=proof,original_transaction_sha256=y.target)
        if damage=='owner_drift': a.cl7_identity_key=y.common['identity_key']
        assert _snapshot(y.x)==before and y.review.export_bytes()==review
    finally:y.review.close()


def test_additional_observed_version_does_not_extend_money_scope_implicitly(exact_case):
    y=_prepare(exact_case)
    try:
        c=_capture(y);store,args=_register_and_create(y,c)
        store.close()
        old=c.evaluation.observation
        base=json.loads(y.review.export_bytes())['base_export_json_ascii']
        previous=next(cl2.InboxObservation.from_canonical_bytes(row['canonical_json_ascii'],y.common['codec_registry'])
            for row in json.loads(base)['observations'] if row['sha256']==c.evaluation.revision_evidence.previous_observation_sha256)
        snap=y.review.snapshot()
        evidence=versions.RevisionEvidence(old.sha256,previous.sha256,'a'*64,'b'*64,'c'*64,stamp(y.x.p.clock_at+timedelta(seconds=1)))
        y.review.append_observed_version(previous,expected_previous_observation_sha256=old.sha256,
            expected_store_revision=snap.store_revision,expected_version_head_sha256=snap.version_head_sha256,evidence=evidence)
        exp=y.review.export_bytes();snap=y.review.snapshot()
        with pytest.raises(_module().VersionFeeCorrectionError,match='FIRST_VERSION_ONLY'):
            _module().create_fee_correction_review(exp,y.root.with_name('second-version-money'),**y.common,
                expected_review_export_sha256=_sha(exp),expected_version_head_sha256=snap.version_head_sha256,created_at=evidence.recorded_at)
    finally:y.review.close()


@pytest.mark.parametrize('consumer',['CL5','CL6'])
def test_old_budget_context_readers_do_not_unwrap_v3(exact_case,consumer):
    from pathlib import Path
    import test_v3_10_reporting_risk_cash_context as fixture
    from trading_robot import cash_availability as cl5
    from trading_robot import reporting_risk_cash_context as cl6
    vector=json.loads((Path(__file__).parent/'fixtures/v3_10_reporting_risk_cash_context_vectors.json').read_text())
    reconciliation,withdraw,reservations,availability=fixture._cash_inputs(vector)
    assert fixture._context(vector) is not None
    y=_prepare(exact_case)
    try:
        c=_capture(y);store,args=_register_and_create(y,c)
        with store:
            store.append_verified_fee_revision(c.capture,**args);export=store.export_bytes()
            if consumer=='CL5':
                with pytest.raises(cl5.CL5Error):cl5.build_cash_availability(export,reconciliation,withdraw,reservations,evaluated_at=fixture.END,identity_key=fixture.KEY)
            else:
                with pytest.raises(cl6.CL6Error):cl6.build_portfolio_risk_cash_context(export,reconciliation,withdraw,reservations,availability,
                    fixture._portfolio_evidence(),fixture._risk_evidence(),evaluated_at=fixture.END,identity_key=fixture.KEY,identity_key_id=fixture.KEY_ID)
    finally:y.review.close()


def test_open_fee_store_validation_never_raw_reads_live_shm(exact_case, monkeypatch):
    y = _prepare(exact_case)
    try:
        captured = _capture(y)
        store, _ = _register_and_create(y, captured)
        shared_memory = store.root / "store.sqlite3-shm"
        original_read_bytes = Path.read_bytes
        attempted_shm_reads = []

        def reject_live_shm(path):
            if path == shared_memory and path.exists():
                attempted_shm_reads.append(path)
                raise PermissionError("simulated Windows live SHM denial")
            return original_read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", reject_live_shm)
        with store:
            snapshot = store.snapshot()
            assert store.snapshot() == snapshot
            assert store.export_bytes()
            assert attempted_shm_reads == []
            with pytest.raises(
                cl2.PersistenceError,
                match=f"^{cl2.PersistenceReason.WAL_SIDECAR_INCONSISTENT.value}$",
            ):
                cl2._validate_live_root(store.root)
            assert attempted_shm_reads == [shared_memory]
    finally:
        y.review.close()
