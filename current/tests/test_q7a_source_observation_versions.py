"""STEP23: explicit same-source version custody; never a monetary permission."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case as desktop_case
from test_q7a_source_exact_dispatch import exact_case as exact_case
from test_q7a_source_exact_dispatch import stamp
from test_q7a_source_provider_refresh import refresh_case as refresh_case
from test_v3_10_broker_read_adapters import _item, _request, _response
from trading_robot import broker_read_adapters as broker
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot import cash_ledger_persistence as v1

KEY = bytes(32)
KEY_ID = 'TEST_KEY_1'
CREATED = '2026-01-03T00:00:00.000000000Z'
LATER = '2026-01-04T00:00:00.000000000Z'
REGISTRY = (broker.TBANK_OPERATION_CODEC,)


def _module():
    import importlib
    return importlib.import_module('trading_robot.cash_observation_versions')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _decoded(amount_nano=123456789, *, identifier='SYNTHETIC-FEE-01', kind='OPERATION_TYPE_BROKER_FEE', account=None):
    raw = _item(id=identifier, payment={'currency': 'RUB', 'units': '0', 'nano': -amount_nano}, type=kind)
    overrides = {}
    if account:
        raw['brokerAccountId'] = account
        overrides['raw_account_id'] = account
    request = _request([_response([raw])], **overrides)
    batch = broker.collect_tbank_operations(request)
    return batch.decisions[0], raw


@pytest.fixture
def case(tmp_path):
    old, raw = _decoded()
    source = v1.CashLedgerStore.create(tmp_path / 'v1', REGISTRY)
    # A synthetic INPUT and a real CL3-decoded fee provide a non-empty monetary base.
    inp = _item(id='SYNTHETIC-INPUT-01', payment={'currency': 'RUB', 'units': '1000', 'nano': 0})
    income = broker.collect_tbank_operations(_request([_response([inp])])).decisions[0]
    for decision in (income, old):
        s = source.snapshot()
        source.append_observation(decision.observation, expected_store_revision=s.store_revision)
        source.append_transaction(decision.transaction_proposal, decision.observation.sha256,
                                  expected_store_revision=s.store_revision+1,
                                  expected_ledger_revision=s.ledger_revision)
    base = source.export_bytes()
    account = old.observation.source.account_scope_sha256
    common = dict(codec_registry=REGISTRY, identity_key=KEY, identity_key_id=KEY_ID,
                  account_scope_sha256=account)
    yield SimpleNamespace(source=source, old=old.observation, base=base, common=common,
                          raw=raw, target=tmp_path/'v2', temp=tmp_path)
    source.close()


def _open_copy(x):
    mod = _module()
    mod.copy_v1_for_revision_review(x.source.root, x.target, **x.common,
                                    expected_source_export_sha256=_sha(x.base), created_at=CREATED)
    return mod.ObservationVersionStore.open(x.target, **x.common)


def _args(store, old, new, raw, *, recorded_at=LATER):
    mod = _module()
    snapshot = store.snapshot()
    evidence = mod.RevisionEvidence(old.sha256, new.sha256, _sha(b'SYNTHETIC_REQUEST'),
                                     _sha(v1.canonical_json_bytes(raw)), _sha(b'SYNTHETIC_LOCAL_BINDING_REPORT'), recorded_at)
    return dict(expected_previous_observation_sha256=old.sha256,
                expected_store_revision=snapshot.store_revision,
                expected_version_head_sha256=snapshot.version_head_sha256, evidence=evidence)


def _append(store, old, amount=200000000, *, recorded_at=LATER):
    decision, raw = _decoded(amount)
    args = _args(store, old, decision.observation, raw, recorded_at=recorded_at)
    result = store.append_observed_version(decision.observation, **args)
    return result, decision.observation, args


def test_real_cl3_same_id_change_is_retained_without_a_money_write(case, request):
    x = case
    changed, raw = _decoded(200000000)
    assert raw['id'] == x.raw['id']
    assert changed.observation.logical_source_sha256 == x.old.logical_source_sha256
    assert changed.observation.source.source_scope_sha256 == x.old.source.source_scope_sha256
    assert changed.observation.source.source_content_sha256 != x.old.source.source_content_sha256
    assert changed.observation.observed_at == x.old.observed_at  # effective_at is not revision receipt time
    with pytest.raises(v1.PersistenceError, match='SOURCE_CONTENT_CONFLICT'):
        x.source.append_observation(changed.observation, expected_store_revision=x.source.snapshot().store_revision)
    with _open_copy(x) as target:
        before = target.snapshot()
        args = _args(target, x.old, changed.observation, raw)
        result = target.append_observed_version(changed.observation, **args)
        after = target.snapshot()
        exported = json.loads(target.export_bytes())
        assert result.version_no == 2 and result.status == 'REVIEW_REQUIRED'
        assert result.monetary_write_allowed is False and after.financial_ready is False
        assert after.store_revision == before.store_revision+1
        assert after.ledger_revision == before.ledger_revision
        assert after.ledger_head_sha256 == before.ledger_head_sha256
        assert exported['base_export_json_ascii'].encode() == x.base
        record = json.loads(exported['version_records'][0]['record_json_ascii'])['payload']
        assert record['previous_observation_sha256'] == x.old.sha256
        assert json.loads(record['observation_json_ascii'])['source'] == changed.observation.source.to_canonical_dict()
        saved = target.export_bytes()
        repeat = target.append_observed_version(changed.observation, **args)
        assert repeat.replay and repeat.record_sha256 == result.record_sha256
        assert target.export_bytes() == saved
        assert x.source.export_bytes() == x.base
        request.node.user_properties.extend([
            ('provider_id_unchanged', True), ('logical_source_unchanged', True),
            ('observed_versions', 2), ('revision_count', after.revision_count),
            ('store_revision_delta', 1), ('ledger_revision_delta', 0),
            ('new_monetary_transactions', 0), ('frozen_v1_export_unchanged', True),
            ('financial_ready', False), ('replay_bytes_unchanged', True),
        ])


def test_copy_preserves_frozen_source_and_old_readers_reject_v2(case):
    mod = _module()
    with _open_copy(case) as target:
        exp = target.export_bytes()
        assert json.loads(exp)['base_export_json_ascii'].encode() == case.base
        with pytest.raises(v1.PersistenceError):
            v1.CashLedgerStore.open(case.target, REGISTRY)
        with pytest.raises(cl4.CL4Error):
            cl4._parse_ledger_export(exp, target_account=case.common['account_scope_sha256'], identity_key=KEY)
        assert case.source.export_bytes() == case.base
    with pytest.raises(mod.ObservationVersionError):
        mod.ObservationVersionStore.open(case.source.root, **case.common)


def test_three_versions_preserve_predecessors_and_effective_time(case):
    mod = _module()
    with _open_copy(case) as target:
        a, one, _ = _append(target, case.old)
        b, two, args = _append(target, one, 1000000, recorded_at='2026-01-05T00:00:00.000000000Z')
        assert (a.version_no, b.version_no) == (2, 3)
        assert one.observed_at == two.observed_at == case.old.observed_at
        exported = json.loads(target.export_bytes())
        row = json.loads(exported['version_records'][1]['record_json_ascii'])['payload']
        assert row['previous_version_sha256'] == a.record_sha256
        assert row['previous_observation_sha256'] == one.sha256
        assert target.append_observed_version(two, **args).replay
        tip = target.snapshot().version_head_sha256
    with mod.ObservationVersionStore.open(case.target, **case.common, expected_version_head_sha256=tip) as reopened:
        assert reopened.snapshot().revision_count == 2
        assert json.loads(reopened.export_bytes())['base_export_json_ascii'].encode() == case.base


@pytest.mark.parametrize('damage', ['expected_revision','expected_head','predecessor','account','alias',
    'codec','same_content','changed_kind','evidence_previous','evidence_next','empty_report','time_equal','bad_time'])
def test_bad_versions_do_not_change_any_bytes(case, damage):
    mod = _module()
    with _open_copy(case) as target:
        dec, raw = _decoded(200000000)
        new = dec.observation
        args = _args(target, case.old, new, raw)
        if damage == 'expected_revision': args['expected_store_revision'] += 1
        elif damage == 'expected_head': args['expected_version_head_sha256'] = '0'*64
        elif damage == 'predecessor': args['expected_previous_observation_sha256'] = '0'*64
        elif damage == 'account': new = _decoded(200000000, account='SYNTHETIC-OTHER')[0].observation
        elif damage == 'alias': new = _decoded(200000000, identifier='ALIAS')[0].observation
        elif damage == 'codec':
            descriptor = replace(new.descriptor, codec_id='SYNTHETIC_OTHER_CODEC')
            new = replace(new, descriptor=descriptor)
        elif damage == 'same_content': new = replace(case.old, provenance_sha256='1'*64)
        elif damage == 'changed_kind': new = replace(new, source=replace(new.source, source_kind='OTHER'))
        elif damage == 'evidence_previous': args['evidence'] = replace(args['evidence'], previous_observation_sha256='1'*64)
        elif damage == 'evidence_next': args['evidence'] = replace(args['evidence'], observation_sha256='1'*64)
        elif damage == 'empty_report': args['evidence'] = replace(args['evidence'], binding_report_sha256='')
        elif damage == 'time_equal': args['evidence'] = replace(args['evidence'], recorded_at=CREATED)
        elif damage == 'bad_time': args['evidence'] = replace(args['evidence'], recorded_at='2026-02-31T00:00:00.000000000Z')
        saved = target.export_bytes()
        with pytest.raises(mod.ObservationVersionError): target.append_observed_version(new, **args)
        assert target.export_bytes() == saved
        assert case.source.export_bytes() == case.base


@pytest.mark.parametrize('method', ['append_transaction','append_correction_bundle','append_status_event'])
def test_revision_does_not_grant_any_monetary_writer(case, method):
    mod = _module()
    with _open_copy(case) as target:
        _append(target, case.old)
        saved = target.export_bytes()
        with pytest.raises(mod.ObservationVersionError): getattr(target, method)(object(), expected_store_revision=0)
        assert target.export_bytes() == saved


def test_generic_ingestion_still_refuses_content_conflict(case):
    with _open_copy(case) as target:
        _, obs, _ = _append(target, case.old)
        saved = target.export_bytes()
        with pytest.raises(v1.PersistenceError, match='SOURCE_CONTENT_CONFLICT'):
            target.append_observation(obs, expected_store_revision=target.snapshot().store_revision)
        assert target.export_bytes() == saved


def test_competing_cas_and_branch_from_stale_predecessor_are_rejected(case):
    mod = _module()
    with _open_copy(case) as first, mod.ObservationVersionStore.open(case.target, **case.common) as second:
        new, raw = _decoded(200000000)
        stale = _args(second, case.old, new.observation, raw)
        _append(first, case.old, 1000000)
        before = second.export_bytes()
        with pytest.raises(mod.ObservationVersionError, match='CAS_CONFLICT'):
            second.append_observed_version(new.observation, **stale)
        current = second.snapshot()
        stale.update(expected_store_revision=current.store_revision,
                     expected_version_head_sha256=current.version_head_sha256)
        with pytest.raises(mod.ObservationVersionError, match='PREDECESSOR_MISMATCH'):
            second.append_observed_version(new.observation, **stale)
        assert second.export_bytes() == before


@pytest.mark.parametrize('cut', ['before_insert','after_insert','after_meta','before_commit','after_commit'])
@pytest.mark.parametrize('reopen', [False, True])
def test_sqlite_prefix_replay_after_injected_failure(case, cut, reopen):
    mod = _module()
    target = _open_copy(case)
    new, raw = _decoded(200000000)
    args = _args(target, case.old, new.observation, raw)
    saved = target.export_bytes()
    def fail(point):
        if point == 'version.'+cut: raise v1.InjectedFault('SYNTHETIC_FAULT')
    target._injector = fail
    with pytest.raises(v1.InjectedFault): target.append_observed_version(new.observation, **args)
    target._injector = None
    if reopen:
        target.close()
        target = mod.ObservationVersionStore.open(case.target, **case.common)
    try:
        if cut != 'after_commit': assert target.export_bytes() == saved
        result = target.append_observed_version(new.observation, **args)
        assert result.replay == (cut == 'after_commit')
        assert target.snapshot().revision_count == 1
        assert json.loads(target.export_bytes())['base_export_json_ascii'].encode() == case.base
    finally: target.close()


@pytest.mark.parametrize('cut', ['before_copy','after_copy','after_schema','before_promote','after_promote'])
def test_copy_is_not_an_in_place_migration_and_failures_preserve_source(case, cut):
    mod = _module()
    before_files = {p.name: p.read_bytes() for p in case.source.root.iterdir() if p.is_file() and not p.name.endswith('-shm')}
    def fail(point):
        if point == 'copy.'+cut: raise v1.InjectedFault('SYNTHETIC_COPY_FAULT')
    with pytest.raises(v1.InjectedFault):
        mod.copy_v1_for_revision_review(case.source.root, case.target, **case.common,
           expected_source_export_sha256=_sha(case.base), created_at=CREATED, fault_injector=fail)
    assert case.source.export_bytes() == case.base
    after_files = {p.name: p.read_bytes() for p in case.source.root.iterdir() if p.is_file() and not p.name.endswith('-shm')}
    assert after_files == before_files
    assert case.target.exists() == (cut == 'after_promote')
    assert not case.target.with_name(case.target.name+'.revision-staging').exists()
    if cut == 'after_promote':
        with mod.ObservationVersionStore.open(case.target, **case.common) as target:
            assert target.snapshot().revision_count == 0


@pytest.mark.parametrize('damage', ['old_key','account','key_id','extra_registry','hash','existing_target','nested_target'])
def test_copy_rejects_wrong_identity_scope_and_target(case, damage):
    mod = _module()
    opts = dict(case.common)
    expected = _sha(case.base)
    target = case.target
    if damage == 'old_key': opts['identity_key'] = b'x'*32
    elif damage == 'account': opts['account_scope_sha256'] = '1'*64
    elif damage == 'key_id': opts['identity_key_id'] = 'bad key'
    elif damage == 'extra_registry': opts['codec_registry'] = ()
    elif damage == 'hash': expected = '0'*64
    elif damage == 'existing_target': target.mkdir()
    elif damage == 'nested_target': target = case.source.root/'nested'
    # A different HMAC key for raw v1 TBANK observations cannot be identified at
    # storage level without an external source-provenance receipt: not claimed.
    if damage == 'old_key':
        mod.copy_v1_for_revision_review(case.source.root, target, **opts,
           expected_source_export_sha256=expected, created_at=CREATED)
        with pytest.raises(mod.ObservationVersionError):
            mod.ObservationVersionStore.open(target, **case.common)
        return
    with pytest.raises((mod.ObservationVersionError, v1.PersistenceError)):
        mod.copy_v1_for_revision_review(case.source.root, target, **opts,
           expected_source_export_sha256=expected, created_at=CREATED)
    assert case.source.export_bytes() == case.base


def test_copy_backup_restore_retains_wal_and_entire_chain(case):
    mod = _module()
    with _open_copy(case) as target:
        _, one, _ = _append(target, case.old)
        _append(target, one, 1000000, recorded_at='2026-01-05T00:00:00.000000000Z')
        expected = target.export_bytes()
        backup = case.temp/'backup'
        mod.copy_versioned_snapshot(target.root, backup, **case.common, expected_export_sha256=_sha(expected))
    restored = case.temp/'restored'
    mod.copy_versioned_snapshot(backup, restored, **case.common, expected_export_sha256=_sha(expected))
    with mod.ObservationVersionStore.open(restored, **case.common) as result:
        assert result.export_bytes() == expected
        assert result.snapshot().revision_count == 2
    with pytest.raises(v1.PersistenceError): v1.CashLedgerStore.open(restored, REGISTRY)


def test_real_second_sqlite_connection_cannot_write_during_cas(case):
    with _open_copy(case) as target:
        observed = []
        def check(point):
            if point != 'version.after_insert': return
            other = sqlite3.connect(case.target/'store.sqlite3', timeout=0, isolation_level=None)
            try:
                with pytest.raises(sqlite3.OperationalError, match='locked'): other.execute('BEGIN IMMEDIATE')
                observed.append(True)
            finally: other.close()
        target._injector = check
        _append(target, case.old)
        assert observed == [True]


@pytest.mark.parametrize('field', ['sequence','version_no','previous_observation_sha256','previous_version_sha256',
    'global_parent_sha256','logical_source_sha256','status','change_kind','observation_sha256','content','codec','recorded_at'])
def test_authenticated_but_malformed_chain_is_rejected(case, field):
    mod = _module()
    with _open_copy(case) as target:
        _append(target, case.old)
        data = json.loads(target.export_bytes())
    env = json.loads(data['version_records'][0]['record_json_ascii'])
    r = env['payload']
    if field == 'sequence': r[field] = '2'
    elif field == 'version_no': r[field] = '3'
    elif field in ('previous_observation_sha256','previous_version_sha256','global_parent_sha256','logical_source_sha256','observation_sha256'):
        r[field] = '1'*64
    elif field in ('status','change_kind'): r[field] = 'POSTED'
    elif field == 'recorded_at': r['evidence']['recorded_at'] = CREATED
    else:
        obs = json.loads(r['observation_json_ascii'])
        if field == 'content': obs['content_json_ascii'] = '{}'
        else: obs['codec_id'] = 'UNKNOWN'
        r['observation_json_ascii'] = v1.canonical_json_bytes(obs).decode()
    raw = mod._signed(r, KEY)
    digest = _sha(raw)
    data['version_records'][0] = dict(record_json_ascii=raw.decode(), sha256=digest)
    data['version_head_sha256'] = digest
    with pytest.raises(mod.ObservationVersionError):
        mod.validate_versioned_export(v1.canonical_json_bytes(data), **case.common)


@pytest.mark.parametrize('damage', ['remove_relation','duplicate','reverse','wrong_version','ready','extra_field','hmac','base_mutation','missing_codec','truncate_tail'])
def test_complete_export_graph_rejects_tampering(case, damage):
    mod = _module()
    with _open_copy(case) as target:
        _, obs, _ = _append(target, case.old)
        _append(target, obs, 1000000, recorded_at='2026-01-05T00:00:00.000000000Z')
        data = json.loads(target.export_bytes())
    if damage == 'remove_relation': data['version_records'].pop(0)
    elif damage == 'duplicate': data['version_records'].append(deepcopy(data['version_records'][0]))
    elif damage == 'reverse': data['version_records'].reverse()
    elif damage == 'wrong_version': data['version'] = 1
    elif damage == 'ready': data['financial_ready'] = True
    elif damage == 'extra_field': data['transactions'] = []
    elif damage == 'truncate_tail': data['version_records'].pop()
    elif damage == 'hmac':
        env = json.loads(data['version_records'][0]['record_json_ascii']);env['hmac_sha256'] = '0'*64
        data['version_records'][0]['record_json_ascii'] = v1.canonical_json_bytes(env).decode()
    else:
        base = json.loads(data['base_export_json_ascii'])
        if damage == 'base_mutation': base['transactions'].pop()
        else: base['codec_registry'].clear()
        data['base_export_json_ascii'] = v1.canonical_json_bytes(base).decode()
    with pytest.raises(mod.ObservationVersionError): mod.validate_versioned_export(v1.canonical_json_bytes(data), **case.common)


def test_pinned_tip_detects_whole_snapshot_rollback(case):
    mod = _module()
    with _open_copy(case) as target:
        old_export = target.export_bytes()
        _append(target, case.old)
        current_head = target.snapshot().version_head_sha256
    # An old complete snapshot is internally valid, but fails a newer external pin.
    assert mod.validate_versioned_export(old_export, **case.common).revision_count == 0
    with pytest.raises(mod.ObservationVersionError, match='PINNED_HEAD_MISMATCH'):
        mod.validate_versioned_export(old_export, **case.common, expected_version_head_sha256=current_head)


@pytest.mark.parametrize('sql', [
    "DELETE FROM cl2_transaction", "UPDATE cl2_meta SET store_revision=99",
    "DELETE FROM cl2_observation", "DELETE FROM cl2_ledger_transition",
    "DELETE FROM cl2_observed_version", "UPDATE cl2_observed_version SET version_no=99",
])
def test_database_freezes_base_and_past_versions(case, sql):
    with _open_copy(case) as target:
        _append(target, case.old)
        before = target.export_bytes()
        with pytest.raises(sqlite3.IntegrityError): target._connection.execute(sql)
        assert target.export_bytes() == before


@pytest.mark.parametrize('damage', ['meta','row','extra_table','lost_trigger'])
def test_reopen_rejects_corrupt_sql_schema_or_row_bindings(case, damage):
    mod = _module()
    with _open_copy(case) as target:
        _append(target, case.old)
    conn = sqlite3.connect(case.target/'store.sqlite3')
    try:
        if damage == 'meta': conn.execute('UPDATE cl2_revision_meta SET revision_count=99')
        elif damage == 'extra_table': conn.execute('CREATE TABLE unexpected (x TEXT)')
        elif damage == 'lost_trigger': conn.execute('DROP TRIGGER v2_frozen_cl2_observation_delete')
        else:
            conn.execute('DROP TRIGGER v2_immutable_cl2_observed_version_update')
            conn.execute('UPDATE cl2_observed_version SET logical_source_sha256=?', ('0'*64,))
            conn.execute(next(s for s in mod._TRIGGER_SQL if 'v2_immutable_cl2_observed_version_update ' in s))
        conn.commit()
    finally: conn.close()
    with pytest.raises(mod.ObservationVersionError): mod.ObservationVersionStore.open(case.target, **case.common)


@pytest.mark.parametrize('damage', ['revision','previous','global_head','response_hash','recorded_time'])
def test_same_observation_replay_requires_exact_original_binding(case, damage):
    mod = _module()
    with _open_copy(case) as target:
        _, obs, args = _append(target, case.old)
        before = target.export_bytes()
        if damage == 'revision': args['expected_store_revision'] += 999
        elif damage == 'previous': args['expected_previous_observation_sha256'] = '1'*64
        elif damage == 'global_head': args['expected_version_head_sha256'] = '1'*64
        elif damage == 'response_hash': args['evidence'] = replace(args['evidence'], response_sha256='1'*64)
        else: args['evidence'] = replace(args['evidence'], recorded_at='2026-01-05T00:00:00.000000000Z')
        with pytest.raises(mod.ObservationVersionError, match='REPLAY_BINDING_CONFLICT'):
            target.append_observed_version(obs, **args)
        assert target.export_bytes() == before


def test_interleaved_sources_use_independent_version_numbers(case):
    with _open_copy(case) as target:
        first, one, _ = _append(target, case.old)
        base = json.loads(case.base)
        original = next(v1.InboxObservation.from_canonical_bytes(row['canonical_json_ascii'],REGISTRY)
                        for row in base['observations'] if row['sha256'] != case.old.sha256)
        raw = _item(id='SYNTHETIC-INPUT-01', payment={'currency':'RUB','units':'900','nano':0})
        changed = broker.collect_tbank_operations(_request([_response([raw])])).decisions[0].observation
        args = _args(target, original, changed, raw, recorded_at='2026-01-05T00:00:00.000000000Z')
        second = target.append_observed_version(changed, **args)
        third, _, _ = _append(target, one, 1000000, recorded_at='2026-01-06T00:00:00.000000000Z')
        assert [first.version_no,second.version_no,third.version_no] == [2,2,3]
        assert target.snapshot().revision_count == 3
        assert json.loads(target.export_bytes())['base_export_json_ascii'].encode() == case.base


def test_source_changes_after_copy_do_not_mutate_frozen_target(case):
    with _open_copy(case) as target:
        before = target.export_bytes()
        observation = _decoded(1000, identifier='NEW-SOURCE')[0].observation
        case.source.append_observation(observation,expected_store_revision=case.source.snapshot().store_revision)
        assert case.source.export_bytes() != case.base
        assert target.export_bytes() == before  # a snapshot, not a runtime cutover


def test_target_symlink_is_rejected_without_touching_referent(case):
    mod = _module()
    other = case.temp/'referent';other.mkdir();(other/'sentinel').write_text('UNCHANGED')
    case.target.symlink_to(other, target_is_directory=True)
    with pytest.raises(v1.PersistenceError):
        mod.copy_v1_for_revision_review(case.source.root,case.target,**case.common,
            expected_source_export_sha256=_sha(case.base),created_at=CREATED)
    assert (other/'sentinel').read_text()=='UNCHANGED'
    assert case.source.export_bytes()==case.base


def test_foreign_schema_snapshot_and_wrong_backup_pin_are_rejected(case):
    mod=_module()
    with _open_copy(case) as target:
        _append(target,case.old)
        with pytest.raises(mod.ObservationVersionError,match='SOURCE_EXPORT_MISMATCH'):
            mod.copy_versioned_snapshot(case.target,case.temp/'invalid-backup',**case.common,
                                        expected_export_sha256='1'*64)
        assert not (case.temp/'invalid-backup').exists()
        with pytest.raises(v1.PersistenceError):
            mod.copy_v1_for_revision_review(case.target,case.temp/'mixed-copy',**case.common,
                expected_source_export_sha256=_sha(target.export_bytes()),created_at=CREATED)
        assert not (case.temp/'mixed-copy').exists()


def test_version_copy_after_real_exact_closure_does_not_change_live_owners(exact_case, tmp_path, request):
    from datetime import timedelta

    from test_q7a_source_fee_replacement import _setup
    from test_q7a_source_natural_cycle import ACCOUNT
    from test_q7a_source_settlement_closure import _snapshot
    mod = _module()
    x = exact_case
    intent, raw, trade, fee, proof, transaction_sha = _setup(x)
    fee['id'] = x.old_fee['id']  # same real provider ID, changed content
    a = x.c.execution_adapter
    source = a.cl7_ledger_store
    base = source.export_bytes()
    value = json.loads(base)
    link = next(row for row in value['provenance_links'] if row['transaction_sha256'] == transaction_sha)
    old_row = next(row for row in value['observations'] if row['sha256']==link['observation_sha256'])
    registry = tuple(source._registry.values())
    old = v1.InboxObservation.from_canonical_bytes(old_row['canonical_json_ascii'], registry)
    common = dict(codec_registry=registry, identity_key=a.cl7_identity_key,
                  identity_key_id=a.cl7_identity_key_id, account_scope_sha256=old.source.account_scope_sha256)
    before = _snapshot(x)
    target = tmp_path/'exact-version-copy'
    mod.copy_v1_for_revision_review(source.root,target,**common,
        expected_source_export_sha256=_sha(base),created_at=stamp(x.p.clock_at))
    x.p.clock_at += timedelta(seconds=1)
    req = broker.BrokerReadRequest(
        environment=broker.BrokerEnvironment.SANDBOX, raw_account_id=ACCOUNT,
        identity_key=a.cl7_identity_key,identity_key_id=a.cl7_identity_key_id,
        from_inclusive='2027-01-01T00:00:00.000000000Z',to_exclusive=stamp(x.p.clock_at),
        limit=8,max_pages=4,max_items=8,absolute_deadline_ns=1_000_000_000,
        retry_policy=broker.RetryPolicy(1,100_000,()),
        transport=x.p.get_operations_by_cursor_once,monotonic_ns=lambda:1,wait_ns=lambda _:None)
    batch = broker.collect_tbank_operations(req)
    new = next(d.observation for d in batch.decisions
               if d.observation.logical_source_sha256==old.logical_source_sha256)
    evidence = mod.RevisionEvidence(old.sha256,new.sha256,batch.watermark.request_fingerprint_sha256,
        batch.watermark.page_chain_sha256,_sha(v1.canonical_json_bytes({'proof':proof,'old_fee':transaction_sha})),
        stamp(x.p.clock_at))
    with mod.ObservationVersionStore.open(target,**common) as copy:
        snap=copy.snapshot()
        result=copy.append_observed_version(new,expected_previous_observation_sha256=old.sha256,
            expected_store_revision=snap.store_revision,expected_version_head_sha256=snap.version_head_sha256,evidence=evidence)
        assert result.status=='REVIEW_REQUIRED' and not result.monetary_write_allowed
        assert copy.snapshot().ledger_head_sha256==snap.ledger_head_sha256
        assert copy.snapshot().ledger_revision==3
        assert json.loads(copy.export_bytes())['base_export_json_ascii'].encode()==base
    assert source.export_bytes()==base and _snapshot(x)==before
    assert x.p.order_calls==1
    request.node.user_properties.extend([('real_exact_closure_fixture',True),('live_owners_unchanged',True),
        ('original_post_count',1),('target_revision_count',1),('source_ledger_revision',3),
        ('monetary_revision_consumer_enabled',False)])


def test_frozen_v2_known_answer_matches_real_cl3_pipeline(case):
    from pathlib import Path
    vector = json.loads((Path(__file__).parent/'fixtures/v3_10_observation_versions_v2_vectors.json').read_text())
    assert case.base.decode()==vector['base_export_json_ascii']
    with _open_copy(case) as target:
        assert target.export_bytes().decode()==vector['genesis_export_json_ascii']
        first,one,_=_append(target,case.old)
        second,_,_=_append(target,one,1000000,recorded_at='2026-01-05T00:00:00.000000000Z')
        assert target.export_bytes().decode()==vector['versioned_export_json_ascii']
        assert target.snapshot().export_sha256==vector['versioned_export_sha256']
        assert (first.record_sha256,second.record_sha256)==(vector['first_record_sha256'],vector['second_record_sha256'])


@pytest.mark.parametrize('consumer',['CL5','CL6'])
def test_legacy_cash_consumers_reject_v2_export_with_valid_old_evidence(case, consumer):
    from pathlib import Path

    import test_v3_10_reporting_risk_cash_context as fixture
    from trading_robot import cash_availability as cl5
    from trading_robot import reporting_risk_cash_context as cl6
    vector=json.loads((Path(__file__).parent/'fixtures/v3_10_reporting_risk_cash_context_vectors.json').read_text())
    reconciliation,withdraw,reservations,availability=fixture._cash_inputs(vector)
    # First prove the known v1 context/evidence is valid, then change ONLY export.
    valid=fixture._context(vector)
    assert valid is not None
    with _open_copy(case) as target: export=target.export_bytes()
    if consumer=='CL5':
        with pytest.raises(cl5.CL5Error):
            cl5.build_cash_availability(export,reconciliation,withdraw,reservations,
                evaluated_at=fixture.END,identity_key=fixture.KEY)
    else:
        with pytest.raises(cl6.CL6Error):
            cl6.build_portfolio_risk_cash_context(export,reconciliation,withdraw,reservations,availability,
                fixture._portfolio_evidence(),fixture._risk_evidence(),evaluated_at=fixture.END,
                identity_key=fixture.KEY,identity_key_id=fixture.KEY_ID)


def test_return_to_prior_content_is_a_new_version_not_a_cycle_or_a_replay(case):
    with _open_copy(case) as target:
        first, one, first_args = _append(target, case.old)
        # A -> B -> A keeps the real SourceIdentity; only the observed version is new.
        args = _args(target, one, case.old, case.raw,
                     recorded_at='2026-01-05T00:00:00.000000000Z')
        second = target.append_observed_version(case.old, **args)
        assert second.version_no == 3 and second.replay is False
        assert second.observation_sha256 == case.old.sha256
        assert second.record_sha256 != first.record_sha256
        before = target.export_bytes()
        assert target.append_observed_version(case.old, **args).replay
        # Historical exact request retries are harmless even after later versions.
        assert target.append_observed_version(one, **first_args).replay
        assert target.export_bytes() == before
        assert target.snapshot().revision_count == 2
        assert json.loads(before)['base_export_json_ascii'].encode() == case.base


def test_repeated_content_in_longer_chain_uses_version_predecessors(case):
    with _open_copy(case) as target:
        _, one, _ = _append(target, case.old)
        args = _args(target, one, case.old, case.raw,
                     recorded_at='2026-01-05T00:00:00.000000000Z')
        target.append_observed_version(case.old, **args)
        args = _args(target, case.old, one, _decoded(200000000)[1],
                     recorded_at='2026-01-06T00:00:00.000000000Z')
        result = target.append_observed_version(one, **args)
        assert result.version_no == 4 and result.replay is False
        assert target.snapshot().revision_count == 3
        rows = json.loads(target.export_bytes())['version_records']
        record = json.loads(rows[-1]['record_json_ascii'])['payload']
        assert record['previous_version_sha256'] == rows[-2]['sha256']
        assert case.source.export_bytes() == case.base
