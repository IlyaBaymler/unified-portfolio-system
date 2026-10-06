"""STEP22: a bound pure fee alias is not a second financial event."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money, position
from test_q7a_source_natural_cycle import ACCOUNT, UID
from test_q7a_source_cash_components import _export, _cash
from test_q7a_source_settlement_closure import _snapshot, _recompose
from test_q7a_source_fee_replacement import _setup as _replacement_setup


def _setup(x, *, late=False):
    intent, raw, trade, fee, proof, target = _replacement_setup(x, late=late)
    fee['payment'] = deepcopy(x.old_fee['payment'])
    raw['executedCommission'] = deepcopy(x.old_commission)
    x.p.wallet_rub = x.old_wallet
    return intent, raw, trade, fee, proof, target


def _alias(x, proof, target):
    fn = getattr(x.c.execution_adapter, 'reconcile_fee_alias', None)
    assert callable(fn), 'STEP22 zero-money alias API is missing'
    return fn(recovery=x.c.cycle_source.portfolio_recovery, proof_sha256=proof,
              original_transaction_sha256=target)


@pytest.mark.parametrize('late', [False, True])
def test_pure_alias_keeps_all_financial_entries_and_retains_both_observations(exact_case, late, request):
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x, late=late)
    before = json.loads(x.prior_export)
    result = _alias(x, proof, target)
    after = json.loads(_export(x))
    assert not result.replay
    for key in ('transactions', 'provenance_links', 'correction_bundles', 'ledger_transitions',
                'ledger_head_sha256', 'ledger_revision', 'ledger_head_json_ascii'):
        assert after[key] == before[key], key
    assert _cash(_export(x)) == _cash(x.prior_export)
    assert int(after['store_revision']) == int(before['store_revision']) + 2
    assert len(after['observations']) == len(before['observations']) + 1
    assert len(after['inbox_status_events']) == len(before['inbox_status_events']) + 1
    added = next(v for v in after['observations'] if v['sha256'] == result.alias_observation_sha256)
    old = next(v for v in before['observations'] if v['sha256'] == result.old_observation_sha256)
    assert old in after['observations']
    assert added['current_status'] == 'REJECTED'  # no independent monetary event; not a broker reject
    assert added['logical_source_sha256'] != old['logical_source_sha256']
    assert json.loads(added['canonical_json_ascii'])['content_json_ascii'] == json.loads(old['canonical_json_ascii'])['content_json_ascii']
    plan_path = x.c.execution_adapter.manager.store.path.parent / 'exact_fee_alias' / (proof + '.json')
    plan = json.loads(plan_path.read_text())['payload']
    assert plan['match']['old_observation_sha256'] == old['sha256']
    assert plan['match']['alias_observation_sha256'] == added['sha256']
    assert plan['match']['original_transaction_sha256'] == target
    assert all(_snapshot(x)[0][k] == x.prior_owners[k] for k in ('portfolio', 'risk', 'central'))
    authority = x.c.cash_authority.status()
    assert authority.state.value == 'EXACT_CASH_DISARMED'
    assert authority.transition_kind == 'FEE_ALIAS_CLOSED_DISARMED'
    assert authority.post_attempt_count == 1 and authority.pending_dispatch_proof_sha256 is None
    assert x.p.order_calls == 1
    saved = _snapshot(x)
    replay = _alias(x, proof, target)
    assert replay.replay and _snapshot(x) == saved
    request.node.user_properties.extend([
        ('old_cash_nano', _cash(x.prior_export)), ('new_cash_nano', _cash(_export(x))),
        ('new_transactions', len(after['transactions']) - len(before['transactions'])),
        ('new_observations', 1), ('store_revision_delta', 2), ('ledger_revision_delta', 0),
        ('post_count', x.p.order_calls), ('owners_unchanged', True), ('replay_no_writes', True),
    ])


@pytest.mark.parametrize('damage', [
    'same_id', 'same_id_content_change', 'fee_amount', 'receipt_amount', 'missing_receipt_fee',
    'missing_service', 'service_nonzero', 'request', 'exchange', 'receipt_trade',
    'missing_alias', 'old_still_present', 'two_aliases', 'fee_parent', 'fee_instrument',
    'fee_type', 'fee_state', 'fee_currency', 'fee_sign', 'fee_summary', 'fee_quantity',
    'fee_trades', 'fee_children', 'fee_date', 'trade_missing', 'trade_renamed',
    'trade_instrument', 'trade_parent', 'trade_stage_id', 'trade_commission',
    'cash_mismatch', 'blocked', 'incomplete', 'timeout', 'expired_window',
])
def test_changed_content_or_ambiguous_alias_retains_hold_without_observation(exact_case, damage):
    from trading_robot.exact_fee_alias import ExactFeeAliasError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    if damage == 'same_id': fee['id'] = x.old_fee['id']
    elif damage == 'same_id_content_change':
        fee['id'] = x.old_fee['id']; fee['payment'] = money('-0.2'); raw['executedCommission'] = money('0.2')
    elif damage == 'fee_amount': fee['payment'] = money('-0.12')
    elif damage == 'receipt_amount': raw['executedCommission'] = money('0.12')
    elif damage == 'missing_receipt_fee': raw.pop('executedCommission')
    elif damage == 'missing_service': raw.pop('serviceCommission')
    elif damage == 'service_nonzero': raw['serviceCommission'] = money(1)
    elif damage == 'request': raw['orderRequestId'] = 'OTHER'
    elif damage == 'exchange': raw['orderId'] = 'OTHER'
    elif damage == 'receipt_trade': raw['stages'][0]['tradeId'] = 'OTHER'
    elif damage == 'missing_alias': x.operations.remove(fee)
    elif damage == 'old_still_present': x.operations.append(deepcopy(x.old_fee))
    elif damage == 'two_aliases':
        extra = deepcopy(fee); extra.update(id='OTHER', cursor='OTHER'); x.operations.append(extra)
    elif damage == 'fee_parent': fee['parentOperationId'] = 'OTHER'
    elif damage == 'fee_instrument': fee['instrumentUid'] = 'OTHER'
    elif damage == 'fee_type': fee['type'] = 'OPERATION_TYPE_SERVICE_FEE'
    elif damage == 'fee_state': fee['state'] = 'OPERATION_STATE_PROGRESS'
    elif damage == 'fee_currency': fee['payment']['currency'] = 'USD'
    elif damage == 'fee_sign': fee['payment'] = money('0.123456789')
    elif damage == 'fee_summary': fee['commission'] = money(1)
    elif damage == 'fee_quantity': fee['quantity'] = '1'
    elif damage == 'fee_trades': fee['tradesInfo'] = deepcopy(trade['tradesInfo'])
    elif damage == 'fee_children': fee['childOperations'] = [{'payment': money(-1)}]
    elif damage == 'fee_date': fee['date'] = stamp(x.p.clock_at)
    elif damage == 'trade_missing': x.operations.remove(trade)
    elif damage == 'trade_renamed': trade['id'] = fee['parentOperationId'] = 'OTHER'
    elif damage == 'trade_instrument': trade['instrumentUid'] = 'OTHER'
    elif damage == 'trade_parent': trade['parentOperationId'] = 'OTHER'
    elif damage == 'trade_stage_id': trade['tradesInfo']['trades'][0]['num'] = 'OTHER'
    elif damage == 'trade_commission': trade['commission'] = money('0.9')
    elif damage == 'cash_mismatch': x.p.wallet_rub -= 1
    elif damage == 'blocked': x.p.cash_blocked_rub = 1
    elif damage == 'incomplete':
        x.p.get_operations_by_cursor_once = lambda *_: {'hasNext': True, 'nextCursor': '', 'items': x.operations}
    elif damage == 'timeout':
        def fail(*_): raise TimeoutError('SYNTHETIC_PRIVATE')
        x.p.get_operations_by_cursor_once = fail
    else: x.p.clock_at += timedelta(days=8)
    before = _snapshot(x)
    with pytest.raises(ExactFeeAliasError): _alias(x, proof, target)
    after = _snapshot(x)
    assert after[1:] == before[1:]
    assert all(after[0][k] == before[0][k] for k in ('portfolio', 'risk', 'central'))
    assert x.c.cash_authority.status().transition_kind == 'FEE_ALIAS_HELD'
    assert x.p.order_calls == 1


@pytest.mark.parametrize('cut', ['hold', 'plan', 'observation', 'status', 'audit', 'authority'])
@pytest.mark.parametrize('recompose', [False, True])
def test_durable_alias_prefix_resumes_without_money(exact_case, desktop_case, monkeypatch, cut, recompose):
    from trading_robot import exact_fee_alias as mod
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    a = x.c.execution_adapter
    fired = []
    with monkeypatch.context() as mp:
        if cut == 'plan': obj, name = mod._AliasStore, 'create'
        elif cut in {'hold', 'authority'}: obj, name = a.cash_authority_manager.store, '_commit_unlocked'
        elif cut == 'observation': obj, name = a.cl7_ledger_store, 'append_observation'
        elif cut == 'status': obj, name = a.cl7_ledger_store, 'append_status_event'
        else: obj, name = x.manager.transaction_coordinator, '_record'
        old = getattr(obj, name)
        def interrupted(*args, **kwargs):
            result = old(*args, **kwargs)
            wanted = cut not in {'hold', 'authority'} or args[0].transition_kind == (
                'FEE_ALIAS_HELD' if cut == 'hold' else 'FEE_ALIAS_CLOSED_DISARMED')
            if wanted and not fired:
                fired.append(True); raise RuntimeError('SYNTHETIC_AFTER_COMMIT')
            return result
        mp.setattr(obj, name, interrupted)
        with pytest.raises(mod.ExactFeeAliasError): _alias(x, proof, target)
    assert fired and _cash(_export(x)) == _cash(x.prior_export)
    assert json.loads(_export(x))['transactions'] == json.loads(x.prior_export)['transactions']
    if recompose: _recompose(x, desktop_case)
    result = _alias(x, proof, target)
    assert x.c.cash_authority.status().transition_kind == 'FEE_ALIAS_CLOSED_DISARMED'
    after = json.loads(_export(x)); before = json.loads(x.prior_export)
    assert after['transactions'] == before['transactions']
    assert after['ledger_head_sha256'] == before['ledger_head_sha256']
    assert int(after['store_revision']) == int(before['store_revision']) + 2
    assert all(_snapshot(x)[0][k] == x.prior_owners[k] for k in ('portfolio', 'risk', 'central'))
    saved = _snapshot(x)
    assert _alias(x, proof, target).replay and _snapshot(x) == saved and x.p.order_calls == 1


@pytest.mark.parametrize('point', [
    'append_observation.after_observation', 'append_observation.after_meta',
    'append_status_event.after_event', 'append_status_event.after_meta',
])
def test_cl2_observation_status_rollback_does_not_mutate_ledger(exact_case, point):
    from trading_robot.exact_fee_alias import ExactFeeAliasError
    from trading_robot.cash_ledger_persistence import InjectedFault
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    store = x.c.execution_adapter.cl7_ledger_store
    fired = []
    def fault(name):
        if name == point and not fired:
            fired.append(True); raise InjectedFault(name)
    store._fault_injector = fault
    with pytest.raises(ExactFeeAliasError): _alias(x, proof, target)
    assert fired and json.loads(_export(x))['transactions'] == json.loads(x.prior_export)['transactions']
    store._fault_injector = None
    _alias(x, proof, target)
    assert _cash(_export(x)) == _cash(x.prior_export) and x.p.order_calls == 1


@pytest.mark.parametrize('damage', ['receipt_amount', 'alias_again', 'missing', 'timeout'])
def test_changed_replay_quarantines_completed_alias_without_new_observation(exact_case, damage):
    from trading_robot.exact_fee_alias import ExactFeeAliasError
    from trading_robot.runtime_cash_authority import CL7RuntimeError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    _alias(x, proof, target)
    saved = _snapshot(x)
    if damage == 'receipt_amount': raw['executedCommission'] = money('0.3')
    elif damage == 'alias_again': fee['id'] = 'THIRD_ALIAS'
    elif damage == 'missing': x.operations.remove(fee)
    else:
        def fail(*args, **kwargs): raise TimeoutError('SYNTHETIC_PRIVATE')
        x.p.get_order_state = fail
    with pytest.raises(ExactFeeAliasError): _alias(x, proof, target)
    after = _snapshot(x)
    assert after[1:] == saved[1:]
    assert all(after[0][k] == saved[0][k] for k in ('portfolio', 'risk', 'central'))
    assert x.c.cash_authority.status().transition_kind == 'FEE_ALIAS_REVIEW_HELD'
    m = x.c.cash_authority
    with pytest.raises(CL7RuntimeError):
        m.arm(raw_account_id=ACCOUNT, identity_key=x.c.execution_adapter.cl7_identity_key,
              identity_key_id=x.c.execution_adapter.cl7_identity_key_id,
              confirmation=m.ARM_PHRASE, transition_at=x.c.execution_adapter.cl7_clock())
    assert x.c.execution_adapter.dispatch_next(x.manager.repository).status != 'SUBMITTED'
    assert x.p.order_calls == 1


@pytest.mark.parametrize('damage', ['wrong_target', 'wrong_proof', 'armed', 'risk', 'portfolio', 'config', 'closure_missing'])
def test_wrong_local_scope_does_not_modify_or_quarantine_other_state(exact_case, damage):
    from trading_robot.exact_fee_alias import ExactFeeAliasError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    a = x.c.execution_adapter
    if damage == 'wrong_target': target = 'f' * 64
    elif damage == 'wrong_proof': proof = 'f' * 64
    elif damage == 'armed':
        m = a.cash_authority_manager
        m.arm(raw_account_id=ACCOUNT, identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
              confirmation=m.ARM_PHRASE, transition_at=a.cl7_clock())
    elif damage == 'risk':
        st = a.risk_runtime.state_store; state = st.load_account(ACCOUNT)
        st.save_account(ACCOUNT, replace(state, daily_order_count=state.daily_order_count + 1))
    elif damage == 'portfolio':
        p = x.manager.repository.load(expected_account_id=ACCOUNT)
        x.manager.repository.save(replace(p, last_transaction_id='FOREIGN'), expected_revision=p.revision, allow_equal_revision=True)
    elif damage == 'config': a.cl7_own_funds_policy = None
    else: (a.manager.store.path.parent / 'exact_settlement_closure' / (proof + '.json')).unlink()
    saved = _snapshot(x)
    with pytest.raises(ExactFeeAliasError): _alias(x, proof, target)
    assert _snapshot(x) == saved


@pytest.mark.parametrize('which', ['wall', 'monotonic', 'owner', 'metadata'])
def test_observation_races_fail_before_alias_store_writes(exact_case, monkeypatch, which):
    from trading_robot.exact_fee_alias import ExactFeeAliasError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    a = x.c.execution_adapter
    old = x.p.get_order_state
    def raced(*args, **kwargs):
        result = old(*args, **kwargs)
        if which == 'wall': x.p.clock_at += timedelta(seconds=6)
        elif which == 'monotonic': a.cl7_monotonic_ns = lambda: 10**12
        elif which == 'owner':
            st = a.risk_runtime.state_store; state = st.load_account(ACCOUNT)
            st.save_account(ACCOUNT, replace(state, daily_order_count=state.daily_order_count + 1))
        else: a.cl7_own_funds_policy = None
        return result
    monkeypatch.setattr(x.p, 'get_order_state', raced)
    with pytest.raises(ExactFeeAliasError): _alias(x, proof, target)
    assert _export(x) == x.prior_export and x.p.order_calls == 1


@pytest.mark.parametrize('damage', ['delete', 'hmac', 'symlink'])
def test_lost_plan_after_status_never_reinserts_alias(exact_case, monkeypatch, damage):
    from trading_robot.exact_fee_alias import ExactFeeAliasError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    with monkeypatch.context() as mp:
        store = x.c.execution_adapter.cl7_ledger_store; old = store.append_status_event
        def interrupted(*args, **kwargs):
            old(*args, **kwargs); raise RuntimeError('AFTER_STATUS')
        mp.setattr(store, 'append_status_event', interrupted)
        with pytest.raises(ExactFeeAliasError): _alias(x, proof, target)
    saved = _snapshot(x)
    path = x.c.execution_adapter.manager.store.path.parent / 'exact_fee_alias' / (proof + '.json')
    if damage == 'delete': path.unlink()
    elif damage == 'hmac':
        doc = json.loads(path.read_bytes()); doc['payload']['match']['original_transaction_sha256'] = 'f' * 64
        path.write_text(json.dumps(doc))
    else:
        other = path.with_suffix('.copy'); path.rename(other); path.symlink_to(other)
    with pytest.raises(ExactFeeAliasError): _alias(x, proof, target)
    assert _snapshot(x) == saved


def test_alias_calls_no_money_writers_and_holds_real_locks(exact_case, monkeypatch):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock, LockUnavailableError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    a = x.c.execution_adapter
    def forbidden(*args, **kwargs): pytest.fail('Alias must not write money or Risk')
    monkeypatch.setattr(a.cl7_ledger_store, 'append_transaction', forbidden)
    monkeypatch.setattr(a.cl7_ledger_store, 'append_correction_bundle', forbidden)
    monkeypatch.setattr(a.risk_runtime.state_store, 'save_account_while_locked', forbidden)
    reads, locks = [], []
    for name in ('get_order_state', 'get_operations_by_cursor_once', 'get_positions'):
        old = getattr(x.p, name)
        def read(*args, _old=old, _name=name, **kwargs):
            assert a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False).transition_kind == 'FEE_ALIAS_HELD'
            reads.append(_name); return _old(*args, **kwargs)
        monkeypatch.setattr(x.p, name, read)
    def locked(label):
        con = sqlite3.connect(a.cl7_ledger_store.root / 'store.sqlite3', timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError): con.execute('BEGIN IMMEDIATE')
        finally: con.close()
        locks.append(label)
    def fault(point):
        if point == 'append_status_event.after_event': locked('status')
    a.cl7_ledger_store._fault_injector = fault
    old_audit = x.manager.transaction_coordinator._record
    def audit(*args, **kwargs):
        if args[0] == 'EXACT_FEE_ALIAS_VERIFIED':
            locked('audit')
            for path in (a.manager.store.lock_path, a.risk_runtime.state_store.lock_path):
                with pytest.raises(LockUnavailableError):
                    with InterProcessFileLock(path, timeout_seconds=0): pass
        return old_audit(*args, **kwargs)
    monkeypatch.setattr(x.manager.transaction_coordinator, '_record', audit)
    _alias(x, proof, target)
    assert set(reads) == {'get_order_state', 'get_operations_by_cursor_once', 'get_positions'}
    assert locks == ['status', 'audit'] and x.p.order_calls == 1


@pytest.mark.parametrize('status', ['CANCELLED', 'REJECTED'])
def test_alias_after_zero_terminal_keeps_no_execution(exact_case, status):
    from test_q7a_source_exact_zero_terminal import _zero
    from test_q7a_source_cash_components import _record
    from test_q7a_source_settlement_closure import _close
    x = exact_case
    intent, raw = _zero(x, status=status, fee='0.123456789')
    cash = _record(x); _close(x, cash.proof_sha256)
    before = _snapshot(x)
    target = next(v['sha256'] for v in json.loads(_export(x))['transactions']
                  if json.loads(v['canonical_json_ascii'])['classification'] == 'COMMISSION')
    x.operations[0]['id'] = 'ZERO_FEE_ALIAS'
    x.p.clock_at += timedelta(days=1); x.advance()
    _alias(x, cash.proof_sha256, target)
    assert all(_snapshot(x)[0][k] == before[0][k] for k in ('portfolio', 'risk', 'central'))
    assert len(x.c.execution_adapter.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids) == 0
    assert _cash(_export(x)) == 10**15 - 123456789 and x.p.order_calls == 1


def test_alias_of_independent_sell_keeps_flat_and_cash(exact_case):
    from test_q7a_source_provider_refresh import stage
    x = exact_case
    x.p.payload['positions'] = [position()]
    x.p.payload['totalAmountShares'] = money(1050)
    x.p.payload['totalAmountPortfolio'] = money(1_001_050)
    stage(x, 1, 'synthetic-preowned-long'); x.refresh(); x.p.target = 0
    intent, raw, trade, fee, proof, target = _setup(x)
    assert intent.candidate.direction == 'SELL'
    _alias(x, proof, target)
    assert _cash(_export(x)) == _cash(x.prior_export)
    assert all(_snapshot(x)[0][k] == x.prior_owners[k] for k in ('portfolio', 'risk', 'central'))


def test_v1_cl2_uniqueness_and_alias_nonledger_disposition_cannot_be_bypassed(exact_case):
    import hashlib
    from trading_robot.cash_ledger_persistence import InboxObservation, PersistenceError, canonical_json_bytes
    from trading_robot.cash_ledger_domain import LedgerTransaction
    from trading_robot import broker_read_adapters as cl3
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    result = _alias(x, proof, target)
    store = x.c.execution_adapter.cl7_ledger_store
    saved = store.export_bytes(); data = json.loads(saved)
    row = next(v for v in data['observations'] if v['sha256'] == result.alias_observation_sha256)
    obs = InboxObservation.from_canonical_bytes(row['canonical_json_ascii'], (cl3.TBANK_OPERATION_CODEC,))
    old_tx = LedgerTransaction.from_canonical_dict(json.loads(next(v['canonical_json_ascii'] for v in data['transactions'] if v['sha256'] == target)))
    proposed = replace(old_tx, source=obs.source)
    with pytest.raises(PersistenceError):
        store.append_transaction(proposed, obs.sha256,
            expected_store_revision=int(data['store_revision']), expected_ledger_revision=int(data['ledger_revision']))
    assert store.export_bytes() == saved
    content = json.loads(obs.content_json_ascii); content['payment_minor_units'] = '-1'
    encoded = canonical_json_bytes(content)
    revised = replace(obs, content_json_ascii=encoded.decode('ascii'),
                      source=replace(obs.source, source_content_sha256=hashlib.sha256(encoded).hexdigest()))
    with pytest.raises(PersistenceError) as err:
        store.append_observation(revised, expected_store_revision=int(data['store_revision']))
    assert err.value.reason.value == 'SOURCE_CONTENT_CONFLICT' and store.export_bytes() == saved


def test_alias_done_transition_forbids_head_revision_attempt_or_opening_change(exact_case):
    from trading_robot.runtime_cash_authority import CL7RuntimeError, RuntimeCashAuthorityState, _transition_pair
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    _alias(x, proof, target)
    path = x.c.execution_adapter.manager.store.path.parent / 'exact_fee_alias' / (proof + '.json')
    body = json.loads(path.read_bytes())['payload']
    from trading_robot.runtime_cash_authority import RuntimeCashAuthorityRecord
    held = RuntimeCashAuthorityRecord.from_canonical_dict(body['held_authority'])
    manager = x.c.cash_authority
    base = dict(state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED, pending_dispatch_proof_sha256=None,
                operations_complete_through=body['watermark']['to_exclusive'])
    for damage in ({'ledger_revision': held.ledger_revision + 1}, {'ledger_head_sha256': 'f'*64},
                   {'post_attempt_count': 0}, {'state': RuntimeCashAuthorityState.EXACT_CASH_ARMED},
                   {'operations_complete_through': held.operations_complete_through}):
        with pytest.raises(CL7RuntimeError):
            candidate = manager._change(held, at=body['watermark']['to_exclusive'], kind='FEE_ALIAS_CLOSED_DISARMED', **(base | damage))
            _transition_pair(held, candidate)
