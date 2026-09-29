"""STEP21 synthetic same-closure fee correction through real CL1/CL2/CL4/CL7."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_exact_dispatch import desktop_case, exact_case, stamp
from test_q7a_source_provider_refresh import refresh_case, money, position
from test_q7a_source_natural_cycle import ACCOUNT, UID
from test_q7a_source_cash_components import _input, _record, _export, _cash
from test_q7a_source_settlement_closure import _close, _snapshot, _recompose
from test_q7a_source_late_fee import _setup as _late_setup, _late
from trading_robot.cash_ledger_domain import LedgerAccount, LedgerClassification, LedgerTransaction
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState


def _setup(x, *, amount='0.2', late=False):
    if late:
        intent, raw, trade, fee, proof = _late_setup(x)
        completed = _late(x, proof)
        target = completed.transaction_sha256
    else:
        intent, raw, trade = _input(x, fee='0.123456789')
        x.p.payload['positions'] = [position(intent.candidate.target_lots)] if intent.candidate.target_lots else []
        recorded = _record(x)
        proof = recorded.proof_sha256
        _close(x, proof)
        fee = x.operations[1]
        target = next(r['sha256'] for r in json.loads(_export(x))['transactions']
                      if json.loads(r['canonical_json_ascii'])['classification'] == 'COMMISSION')
    x.prior_export = _export(x)
    x.prior_owners = _snapshot(x)[0]
    x.old_fee = deepcopy(fee)
    x.old_commission = deepcopy(raw['executedCommission'])
    x.old_wallet = x.p.wallet_rub
    old_amount = -(Decimal(fee['payment']['units']) + Decimal(fee['payment']['nano']) / 10**9)
    new_amount = Decimal(amount)
    total = Decimal(raw['executedCommission']['units']) + Decimal(raw['executedCommission']['nano']) / 10**9
    raw['executedCommission'] = money(total + new_amount - old_amount)
    fee['payment'] = money(-new_amount)
    fee['id'] = 'PRIVATE_REPLACEMENT_FEE'
    fee['cursor'] = 'PRIVATE_REPLACEMENT_CURSOR'
    x.p.wallet_rub -= new_amount - old_amount
    x.p.clock_at += timedelta(days=1, seconds=1)
    x.advance()
    return intent, raw, trade, fee, proof, target


def _replace_fee(x, proof, target):
    method = getattr(x.c.execution_adapter, 'reconcile_fee_replacement', None)
    assert callable(method), 'STEP21 fee replacement API is missing'
    return method(recovery=x.c.cycle_source.portfolio_recovery, proof_sha256=proof,
                  original_transaction_sha256=target)


@pytest.mark.parametrize('late', [False, True])
@pytest.mark.parametrize('amount', ['0.2', '0.001'])
def test_atomic_replacement_debit_or_credit_without_second_execution(exact_case, late, amount, request):
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x, amount=amount, late=late)
    original = json.loads(x.prior_export)
    old_bytes = next(v for v in original['transactions'] if v['sha256'] == target)
    result = _replace_fee(x, proof, target)
    current = json.loads(_export(x))
    assert result.appended_transactions == 2 and not result.replay
    assert int(current['ledger_revision']) == int(original['ledger_revision']) + 1
    assert len(current['transactions']) == len(original['transactions']) + 2
    assert len(current['correction_bundles']) == 1
    assert next(v for v in current['transactions'] if v['sha256'] == target) == old_bytes
    bundle = json.loads(current['correction_bundles'][0]['canonical_json_ascii'])
    assert bundle['original_sha256'] == target and current['correction_bundles'][0]['sha256'] == result.bundle_sha256
    by_hash = {v['sha256']: LedgerTransaction.from_canonical_dict(json.loads(v['canonical_json_ascii']))
               for v in current['transactions']}
    assert by_hash[bundle['reversal_sha256']].reversal_of_sha256 == target
    assert by_hash[bundle['correction_sha256']].corrects_sha256 == target
    assert by_hash[bundle['reversal_sha256']].source == by_hash[bundle['correction_sha256']].source
    assert _cash(_export(x)) == _cash(x.prior_export) + result.cash_delta_nano
    assert Decimal(_cash(_export(x))) / 10**9 == x.p.wallet_rub
    owner = _snapshot(x)[0]
    assert all(owner[k] == x.prior_owners[k] for k in ('portfolio', 'risk', 'central'))
    authority = x.c.cash_authority.status()
    assert authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED
    assert authority.post_attempt_count == 1 and authority.pending_dispatch_proof_sha256 is None
    assert x.p.order_calls == 1
    saved = _snapshot(x)
    again = _replace_fee(x, proof, target)
    assert again.replay and again.appended_transactions == 0 and _snapshot(x) == saved
    assert again.bundle_sha256 == result.bundle_sha256
    request.node.user_properties.extend([('cash_delta_nano', result.cash_delta_nano),
        ('new_cash_nano', _cash(_export(x))), ('ledger_revision', result.ledger_revision),
        ('old_fee_nano', result.old_fee_nano), ('new_fee_nano', result.new_fee_nano),
        ('posts', x.p.order_calls), ('replay_appends', again.appended_transactions),
        ('old_transaction_preserved', True), ('owners_unchanged', True)])


@pytest.mark.parametrize('damage', [
    'same_id', 'pure_alias', 'zero_replacement', 'negative_total', 'missing_receipt_fee',
    'missing_service', 'service_nonzero', 'request', 'exchange', 'receipt_trade',
    'missing_replacement', 'old_still_present', 'two_replacements', 'fee_parent', 'fee_instrument',
    'fee_type', 'fee_state', 'fee_currency', 'fee_sign', 'fee_amount', 'fee_summary',
    'fee_quantity', 'fee_trades', 'fee_children', 'fee_date', 'trade_missing',
    'trade_renamed', 'trade_instrument', 'trade_parent', 'trade_stage_id', 'trade_stage_price',
    'trade_stage_time', 'trade_stage_quantity', 'trade_asset', 'trade_commission',
    'cash_mismatch', 'blocked', 'incomplete', 'timeout', 'expired_window',
])
def test_ambiguous_replacement_is_quarantined_without_money(exact_case, damage):
    from trading_robot.exact_fee_replacement import ExactFeeReplacementError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    if damage == 'same_id': fee['id'] = x.old_fee['id']
    elif damage == 'pure_alias':
        fee['payment'] = deepcopy(x.old_fee['payment'])
        raw['executedCommission'] = deepcopy(x.old_commission)
        x.p.wallet_rub = x.old_wallet
    elif damage == 'zero_replacement': fee['payment'] = raw['executedCommission'] = money(0)
    elif damage == 'negative_total': raw['executedCommission'] = money(-1)
    elif damage == 'missing_receipt_fee': raw.pop('executedCommission')
    elif damage == 'missing_service': raw.pop('serviceCommission')
    elif damage == 'service_nonzero': raw['serviceCommission'] = money(1)
    elif damage == 'request': raw['orderRequestId'] = 'OTHER'
    elif damage == 'exchange': raw['orderId'] = 'OTHER'
    elif damage == 'receipt_trade': raw['stages'][0]['tradeId'] = 'OTHER'
    elif damage == 'missing_replacement': x.operations.remove(fee)
    elif damage == 'old_still_present': x.operations.append(deepcopy(x.old_fee))
    elif damage == 'two_replacements':
        extra = deepcopy(fee); extra.update(id='OTHER', cursor='OTHER'); x.operations.append(extra)
    elif damage == 'fee_parent': fee['parentOperationId'] = 'OTHER'
    elif damage == 'fee_instrument': fee['instrumentUid'] = 'OTHER'
    elif damage == 'fee_type': fee['type'] = 'OPERATION_TYPE_SERVICE_FEE'
    elif damage == 'fee_state': fee['state'] = 'OPERATION_STATE_PROGRESS'
    elif damage == 'fee_currency': fee['payment']['currency'] = 'USD'
    elif damage == 'fee_sign': fee['payment'] = money('0.2')
    elif damage == 'fee_amount': fee['payment'] = money('-0.19')
    elif damage == 'fee_summary': fee['commission'] = money(1)
    elif damage == 'fee_quantity': fee['quantity'] = '1'
    elif damage == 'fee_trades': fee['tradesInfo'] = deepcopy(trade['tradesInfo'])
    elif damage == 'fee_children': fee['childOperations'] = [{'payment': money(-1), 'instrumentUid': UID}]
    elif damage == 'fee_date': fee['date'] = stamp(x.p.clock_at)
    elif damage == 'trade_missing': x.operations.remove(trade)
    elif damage == 'trade_renamed': trade['id'] = fee['parentOperationId'] = 'OTHER'
    elif damage == 'trade_instrument': trade['instrumentUid'] = 'OTHER'
    elif damage == 'trade_parent': trade['parentOperationId'] = 'OTHER'
    elif damage == 'trade_stage_id': trade['tradesInfo']['trades'][0]['num'] = 'OTHER'
    elif damage == 'trade_stage_price': trade['tradesInfo']['trades'][0]['price'] = money(1)
    elif damage == 'trade_stage_time': trade['tradesInfo']['trades'][0]['date'] = stamp(x.p.clock_at)
    elif damage == 'trade_stage_quantity': trade['tradesInfo']['trades'][0]['quantity'] = '1'
    elif damage == 'trade_asset': trade['instrumentType'] = 'bond'
    elif damage == 'trade_commission': trade['commission'] = deepcopy(raw['executedCommission'])
    elif damage == 'cash_mismatch': x.p.wallet_rub -= 1
    elif damage == 'blocked': x.p.cash_blocked_rub = 1
    elif damage == 'incomplete':
        x.p.get_operations_by_cursor_once = lambda *_: {'hasNext': True, 'nextCursor': '', 'items': x.operations}
    elif damage == 'timeout':
        def fail(*_): raise TimeoutError('PRIVATE')
        x.p.get_operations_by_cursor_once = fail
    elif damage == 'expired_window': x.p.clock_at += timedelta(days=8)
    before = _snapshot(x)
    with pytest.raises(ExactFeeReplacementError): _replace_fee(x, proof, target)
    after = _snapshot(x)
    assert after[1:] == before[1:]
    assert all(after[0][k] == before[0][k] for k in ('portfolio', 'risk', 'central'))
    authority = x.c.cash_authority.status()
    assert authority.state.value == 'EXACT_CASH_FEE_ADJUSTMENT_PENDING'
    assert authority.transition_kind == 'FEE_REPLACEMENT_HELD'
    assert authority.pending_dispatch_proof_sha256 == proof and authority.post_attempt_count == 1


@pytest.mark.parametrize('damage', ['parent', 'instrument', 'trades'])
def test_unchanged_other_fee_raw_binding_is_not_covered_only_by_cl3_hash(exact_case, damage):
    from trading_robot.exact_fee_replacement import ExactFeeReplacementError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x, late=True)
    older_fee = x.operations[1]
    if damage == 'parent': older_fee['parentOperationId'] = 'OTHER'
    elif damage == 'instrument': older_fee['instrumentUid'] = 'OTHER'
    else: older_fee['tradesInfo'] = deepcopy(trade['tradesInfo'])
    with pytest.raises(ExactFeeReplacementError): _replace_fee(x, proof, target)
    assert _export(x) == x.prior_export
    assert x.c.cash_authority.status().state.value == 'EXACT_CASH_FEE_ADJUSTMENT_PENDING'


@pytest.mark.parametrize('cut', ['hold', 'plan', 'observation', 'bundle', 'audit', 'authority'])
@pytest.mark.parametrize('recompose', [False, True])
def test_committed_prefix_resumes_same_bundle_once(exact_case, desktop_case, monkeypatch, cut, recompose):
    from trading_robot import exact_fee_replacement as mod
    x = exact_case
    intent, raw, trade, fee, proof, target_hash = _setup(x, amount='0.001')
    a = x.c.execution_adapter
    fired = []
    with monkeypatch.context() as mp:
        if cut == 'plan': obj, name = mod._ReplacementStore, 'create'
        elif cut in {'hold', 'authority'}: obj, name = a.cash_authority_manager.store, '_commit_unlocked'
        elif cut == 'observation': obj, name = a.cl7_ledger_store, 'append_observation'
        elif cut == 'bundle': obj, name = a.cl7_ledger_store, 'append_correction_bundle'
        else: obj, name = x.manager.transaction_coordinator, '_record'
        fn = getattr(obj, name)
        def interrupted(*args, **kwargs):
            result = fn(*args, **kwargs)
            wanted = cut not in {'hold', 'authority'} or args[0].transition_kind == (
                'FEE_REPLACEMENT_HELD' if cut == 'hold' else 'FEE_REPLACEMENT_CLOSED_DISARMED')
            if wanted and not fired:
                fired.append(True)
                raise RuntimeError('SYNTHETIC_AFTER_COMMIT')
            return result
        mp.setattr(obj, name, interrupted)
        with pytest.raises(mod.ExactFeeReplacementError): _replace_fee(x, proof, target_hash)
    assert fired
    assert a.cash_authority_manager.status().state.value == (
        'EXACT_CASH_DISARMED' if cut == 'authority' else 'EXACT_CASH_FEE_ADJUSTMENT_PENDING')
    if recompose: _recompose(x, desktop_case)
    result = _replace_fee(x, proof, target_hash)
    final = json.loads(_export(x))
    assert len(final['transactions']) == len(json.loads(x.prior_export)['transactions']) + 2
    assert len(final['correction_bundles']) == 1
    assert _cash(_export(x)) == _cash(x.prior_export) + 122_456_789
    assert all(_snapshot(x)[0][k] == x.prior_owners[k] for k in ('portfolio', 'risk', 'central'))
    assert x.p.order_calls == 1
    saved = _snapshot(x)
    assert _replace_fee(x, proof, target_hash).replay and _snapshot(x) == saved


@pytest.mark.parametrize('point', [
    'append_observation.after_observation',
    'append_correction_bundle.after_transactions', 'append_correction_bundle.after_provenance',
    'append_correction_bundle.after_bundle', 'append_correction_bundle.after_meta',
])
def test_cl2_atomic_rollback_has_no_half_reversal(exact_case, point):
    from trading_robot.exact_fee_replacement import ExactFeeReplacementError
    from trading_robot.cash_ledger_persistence import InjectedFault
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    store = x.c.execution_adapter.cl7_ledger_store
    fired = []
    def fault(name):
        if name == point and not fired:
            fired.append(True); raise InjectedFault(name)
    store._fault_injector = fault
    with pytest.raises(ExactFeeReplacementError): _replace_fee(x, proof, target)
    assert fired
    export = json.loads(_export(x))
    assert export['transactions'] == json.loads(x.prior_export)['transactions']
    assert export['correction_bundles'] == [] and _cash(_export(x)) == _cash(x.prior_export)
    store._fault_injector = None
    _replace_fee(x, proof, target)
    assert len(json.loads(_export(x))['correction_bundles']) == 1 and x.p.order_calls == 1


@pytest.mark.parametrize('damage', ['receipt_amount', 'alias', 'missing', 'timeout'])
def test_changed_replay_enters_review_hold_without_second_bundle(exact_case, damage):
    from trading_robot.exact_fee_replacement import ExactFeeReplacementError
    from trading_robot.runtime_cash_authority import CL7RuntimeError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    _replace_fee(x, proof, target)
    saved = _snapshot(x)
    if damage == 'receipt_amount': raw['executedCommission'] = money('0.3')
    elif damage == 'alias': fee['id'] = 'ANOTHER_ALIAS'
    elif damage == 'missing': x.operations.remove(fee)
    else:
        def fail(*args, **kwargs): raise TimeoutError('PRIVATE')
        x.p.get_order_state = fail
    with pytest.raises(ExactFeeReplacementError): _replace_fee(x, proof, target)
    after = _snapshot(x)
    assert after[1:] == saved[1:]
    assert all(after[0][k] == saved[0][k] for k in ('portfolio', 'risk', 'central'))
    authority = x.c.cash_authority.status()
    assert authority.transition_kind == 'FEE_REPLACEMENT_REVIEW_HELD'
    assert authority.state.value == 'EXACT_CASH_FEE_ADJUSTMENT_PENDING'
    m = x.c.cash_authority
    with pytest.raises(CL7RuntimeError):
        m.arm(raw_account_id=ACCOUNT, identity_key=x.c.execution_adapter.cl7_identity_key,
              identity_key_id=x.c.execution_adapter.cl7_identity_key_id,
              confirmation=m.ARM_PHRASE, transition_at=x.c.execution_adapter.cl7_clock())
    assert x.c.execution_adapter.dispatch_next(x.manager.repository).status != 'SUBMITTED'
    assert x.p.order_calls == 1


@pytest.mark.parametrize('damage', ['wrong_target', 'wrong_proof', 'armed', 'risk', 'portfolio', 'config', 'closure_missing'])
def test_invalid_initial_local_scope_cannot_quarantine_another_state(exact_case, damage):
    from trading_robot.exact_fee_replacement import ExactFeeReplacementError
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
    elif damage == 'closure_missing':
        (a.manager.store.path.parent / 'exact_settlement_closure' / (proof + '.json')).unlink()
    saved = _snapshot(x)
    with pytest.raises(ExactFeeReplacementError): _replace_fee(x, proof, target)
    assert _snapshot(x) == saved


@pytest.mark.parametrize('which', ['wall', 'monotonic', 'owner', 'metadata'])
def test_read_races_or_expiry_do_not_write_money(exact_case, monkeypatch, which):
    from trading_robot.exact_fee_replacement import ExactFeeReplacementError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    a = x.c.execution_adapter
    fn = x.p.get_order_state
    def raced(*args, **kwargs):
        result = fn(*args, **kwargs)
        if which == 'wall': x.p.clock_at += timedelta(seconds=6)
        elif which == 'monotonic': a.cl7_monotonic_ns = lambda: 10**12
        elif which == 'owner':
            st = a.risk_runtime.state_store; state = st.load_account(ACCOUNT)
            st.save_account(ACCOUNT, replace(state, daily_order_count=state.daily_order_count + 1))
        else: a.cl7_own_funds_policy = None
        return result
    monkeypatch.setattr(x.p, 'get_order_state', raced)
    with pytest.raises(ExactFeeReplacementError): _replace_fee(x, proof, target)
    assert _export(x) == x.prior_export and x.p.order_calls == 1
    assert a.cash_authority_manager.status().state.value == 'EXACT_CASH_FEE_ADJUSTMENT_PENDING'


@pytest.mark.parametrize('status', ['CANCELLED', 'REJECTED'])
def test_fee_replacement_after_zero_terminal_does_not_record_execution(exact_case, status):
    from test_q7a_source_exact_zero_terminal import _zero
    x = exact_case
    intent, raw = _zero(x, status=status, fee='0.123456789')
    cash = _record(x); _close(x, cash.proof_sha256)
    before = _snapshot(x)
    target = next(v['sha256'] for v in json.loads(_export(x))['transactions']
                  if json.loads(v['canonical_json_ascii'])['classification'] == 'COMMISSION')
    raw['executedCommission'] = money('0.1')
    x.operations[0]['id'] = 'ZERO_REPLACED_FEE'
    x.operations[0]['payment'] = money('-0.1')
    x.p.wallet_rub += Decimal('0.023456789')
    x.p.clock_at += timedelta(days=1)
    x.advance()
    result = _replace_fee(x, cash.proof_sha256, target)
    assert result.cash_delta_nano == 23_456_789
    assert _cash(_export(x)) == 10**15 - 100_000_000
    assert all(_snapshot(x)[0][k] == before[0][k] for k in ('portfolio', 'risk', 'central'))
    assert len(x.c.execution_adapter.risk_runtime.state_store.load_account(ACCOUNT).recorded_execution_ids) == 0
    assert x.p.order_calls == 1


def test_independent_sell_fee_replacement_preserves_flat(exact_case):
    from test_q7a_source_provider_refresh import stage
    x = exact_case
    x.p.payload['positions'] = [position()]
    x.p.payload['totalAmountShares'] = money(1050)
    x.p.payload['totalAmountPortfolio'] = money(1_001_050)
    stage(x, 1, 'synthetic-preowned-long'); x.refresh(); x.p.target = 0
    intent, raw, trade, fee, proof, target = _setup(x, amount='0.1')
    assert intent.candidate.direction == 'SELL'
    result = _replace_fee(x, proof, target)
    assert result.cash_delta_nano == 23_456_789
    assert _cash(_export(x)) == 1_001_051_134_567_890
    assert all(_snapshot(x)[0][k] == x.prior_owners[k] for k in ('portfolio', 'risk', 'central'))
    assert x.p.order_calls == 1


def test_provider_reads_after_hold_and_bundle_and_audit_hold_real_writer_lock(exact_case, monkeypatch):
    import sqlite3
    from trading_robot.locking import InterProcessFileLock, LockUnavailableError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    a = x.c.execution_adapter
    reads, locks = [], []
    for name in ('get_order_state', 'get_operations_by_cursor_once', 'get_positions'):
        fn = getattr(x.p, name)
        def checked(*args, _fn=fn, _name=name, **kwargs):
            assert a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False).state.value == 'EXACT_CASH_FEE_ADJUSTMENT_PENDING'
            reads.append(_name)
            return _fn(*args, **kwargs)
        monkeypatch.setattr(x.p, name, checked)
    def locked_writer(label):
        con = sqlite3.connect(a.cl7_ledger_store.root / 'store.sqlite3', timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError): con.execute('BEGIN IMMEDIATE')
        finally: con.close()
        locks.append(label)
    def fault(point):
        if point == 'append_correction_bundle.after_transactions': locked_writer('bundle')
    a.cl7_ledger_store._fault_injector = fault
    old = x.manager.transaction_coordinator._record
    def audit(*args, **kwargs):
        if args[0] == 'EXACT_FEE_REPLACEMENT_ACCOUNTED':
            locked_writer('audit')
            for path in (a.manager.store.lock_path, a.risk_runtime.state_store.lock_path):
                with pytest.raises(LockUnavailableError):
                    with InterProcessFileLock(path, timeout_seconds=0): pass
        return old(*args, **kwargs)
    monkeypatch.setattr(x.manager.transaction_coordinator, '_record', audit)
    _replace_fee(x, proof, target)
    assert set(reads) == {'get_order_state', 'get_operations_by_cursor_once', 'get_positions'}
    assert locks == ['bundle', 'audit'] and x.p.order_calls == 1


@pytest.mark.parametrize('damage', ['delete', 'hmac', 'link'])
def test_changed_or_missing_checkpoint_after_bundle_cannot_create_another_correction(exact_case, monkeypatch, damage):
    from trading_robot import exact_fee_replacement as mod
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    store = x.c.execution_adapter.cl7_ledger_store
    with monkeypatch.context() as mp:
        old = store.append_correction_bundle
        def interrupted(*args, **kwargs):
            old(*args, **kwargs); raise RuntimeError('AFTER_BUNDLE')
        mp.setattr(store, 'append_correction_bundle', interrupted)
        with pytest.raises(mod.ExactFeeReplacementError): _replace_fee(x, proof, target)
    saved = _snapshot(x)
    p = x.c.execution_adapter.manager.store.path.parent / 'exact_fee_replacement' / (proof + '.json')
    if damage == 'delete': p.unlink()
    elif damage == 'hmac':
        d = json.loads(p.read_bytes()); d['payload']['match']['new_fee_nano'] = '1'; p.write_text(json.dumps(d))
    else:
        q = p.with_suffix('.copy'); p.rename(q); p.symlink_to(q)
    with pytest.raises(mod.ExactFeeReplacementError): _replace_fee(x, proof, target)
    assert _snapshot(x) == saved
    assert len(json.loads(_export(x))['correction_bundles']) == 1


def test_audit_expiry_retains_atomic_bundle_and_hold_then_resumes(exact_case, monkeypatch):
    from trading_robot.exact_fee_replacement import ExactFeeReplacementError
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    with monkeypatch.context() as mp:
        old = x.manager.transaction_coordinator._record
        def delayed(*args, **kwargs):
            result = old(*args, **kwargs)
            if args[0] == 'EXACT_FEE_REPLACEMENT_ACCOUNTED': x.p.clock_at += timedelta(seconds=6)
            return result
        mp.setattr(x.manager.transaction_coordinator, '_record', delayed)
        with pytest.raises(ExactFeeReplacementError): _replace_fee(x, proof, target)
    assert x.c.cash_authority.status().state.value == 'EXACT_CASH_FEE_ADJUSTMENT_PENDING'
    saved = _export(x)
    result = _replace_fee(x, proof, target)
    assert result.replay and result.appended_transactions == 0 and _export(x) == saved
    assert x.c.cash_authority.status().state.value == 'EXACT_CASH_DISARMED'


def test_public_result_does_not_export_money_or_raw_identity(exact_case):
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    result = _replace_fee(x, proof, target)
    doc = result.to_canonical_dict()
    encoded = json.dumps(doc)
    for text in (ACCOUNT, UID, intent.intent_id, fee['id'], trade['id'], raw['orderId'],
                 'old_fee_nano', 'new_fee_nano', 'cash_delta_nano', '200000000'):
        assert text not in encoded
    assert doc['risk_execution_written'] is False
    assert doc['automatic_rearm_allowed'] is False and doc['future_fee_finality_claimed'] is False


@pytest.mark.parametrize('damage', ['skip_hold', 'revision', 'attempts', 'rearm', 'from_review'])
def test_new_authority_transitions_preserve_spent_attempt_and_require_real_hold(exact_case, damage):
    from trading_robot.runtime_cash_authority import CL7RuntimeError, _transition_pair
    x = exact_case
    intent, raw, trade, fee, proof, target = _setup(x)
    prior = x.c.cash_authority.status()
    _replace_fee(x, proof, target)
    final = x.c.cash_authority.status()
    from trading_robot.exact_fee_replacement import _ReplacementStore
    plan = _ReplacementStore(x.c.execution_adapter.manager.store.path.parent, proof,
                             x.c.execution_adapter.cl7_identity_key).load()
    from trading_robot.runtime_cash_authority import RuntimeCashAuthorityRecord
    held = RuntimeCashAuthorityRecord.from_canonical_dict(plan['held_authority'])
    old, new = held, final
    if damage == 'skip_hold': old = prior
    elif damage == 'revision': new = replace(final, ledger_revision=final.ledger_revision + 1)
    elif damage == 'attempts': new = replace(final, post_attempt_count=0)
    elif damage == 'rearm': new = replace(final, state=RuntimeCashAuthorityState.EXACT_CASH_ARMED)
    else: old = replace(held, transition_kind='FEE_REPLACEMENT_REVIEW_HELD')
    with pytest.raises(CL7RuntimeError): _transition_pair(old, new)
