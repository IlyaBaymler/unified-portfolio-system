"""STEP10: real locked sending boundary; provider observations are synthetic.

The two paired core regressions set an explicit MARKET argument at the existing
candidate constructor, identically before and after the source change. This is a
request-choice shim for the old factory, NOT a replacement Risk/proof/transport.
Other tests exercise the new desktop factory's explicit MARKET option directly.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
import sqlite3

import pytest

from test_q7a_source_exact_dispatch import exact_case, desktop_case
from test_q7a_source_desktop_flow import desktop_case as base_desktop_case
from test_q7a_source_provider_refresh import refresh_case, money
from test_q7a_source_desktop_flow import _desktop_cycle, _observed
from test_q7a_source_natural_cycle import ACCOUNT, UID
from trading_robot.central_order_manager import CentralOrderCandidate
from trading_robot.runtime_cash_authority import LockedDispatchProof, CL7RuntimeError, RuntimeCashAuthorityState


def limits(cash=1_000_000, market_lots='1000000'):
    return {'currency': 'rub', 'buyLimits': {
        'buyMoneyAmount': money(cash), 'buyMaxMarketLots': market_lots, 'buyMaxLots': '1000000'},
        'buyMarginLimits': {'buyMoneyAmount': money(10_000_000), 'buyMaxMarketLots': '10000000'},
        'sellLimits': {'sellMaxLots': '1000000'}}


def dispatch(x):
    assert _desktop_cycle(x.c).status == 'QUEUED'
    return _observed(x.c)


def proof(x):
    intent = x.c.central_order_coordinator.manager.state().blocking_intent
    return intent, LockedDispatchProof.from_canonical_dict(intent.cl7_locked_dispatch_proof)


def assert_no_attempt(x):
    a = x.c.execution_adapter.cash_authority_manager.status()
    assert x.p.order_calls == 0 and not x.at_post
    assert a.state is RuntimeCashAuthorityState.EXACT_CASH_ARMED
    assert a.post_attempt_count == 0 and a.pending_dispatch_proof_sha256 is None
    assert x.c.central_order_coordinator.manager.state().queued


def inject_at_final_own_funds_read(x, raw):
    # STEP12 adds pre-lock configured-scope reads. This test targets STEP10's
    # final request-bound veto, so leave preliminary cash valid and inject only
    # when its second Positions read starts, under the final locks.
    positions_reads = []
    def inject():
        positions_reads.append(1)
        if len(positions_reads) == 2:
            x.p.cash_limits_override = raw
    x.p.on_cash_positions = inject


def explicit_market_argument(monkeypatch):
    original = CentralOrderCandidate.from_strategy_proposal.__func__
    def selected(cls, *args, **kwargs):
        kwargs['order_type'] = 'MARKET'
        return original(cls, *args, **kwargs)
    monkeypatch.setattr(CentralOrderCandidate, 'from_strategy_proposal', classmethod(selected))


@pytest.mark.parametrize('damage', ['money_zero', 'market_lots_zero'])
def test_paired_market_own_funds_collapsed_after_admission(exact_case, monkeypatch, damage, request):
    x = exact_case
    explicit_market_argument(monkeypatch)
    # Canonical admission/ledger/withdraw remain high. Only the fresh, independent
    # account+instrument buying limit is no longer sufficient at locked dispatch.
    inject_at_final_own_funds_read(x, limits(cash=0 if damage == 'money_zero' else 1_000_000,
                                      market_lots='0' if damage == 'market_lots_zero' else '1000000'))
    outcome = dispatch(x)
    intents = x.c.central_order_coordinator.manager.state().intents
    authority = x.c.execution_adapter.cash_authority_manager.status()
    request.node.user_properties.extend([('scenario', damage),
        ('execution_status', outcome.execution_status), ('fake_post_count', x.p.order_calls),
        ('authority_state', authority.state.value), ('attempt_count', authority.post_attempt_count),
        ('order_type', intents[-1].candidate.order_type)])
    assert intents[-1].candidate.order_type == 'MARKET'
    assert outcome.execution_status == 'CL7_OWN_FUNDS_BLOCKED'
    assert_no_attempt(x)


def test_factory_market_choice_and_v2_evidence_are_real(exact_case):
    x = exact_case
    assert x.c.central_order_coordinator.execution_order_type == 'MARKET'
    pbefore = len(x.p.cash_read_calls)
    assert dispatch(x).execution_status == 'SUBMITTED'
    intent, p = proof(x)
    assert intent.candidate.order_type == 'MARKET' and p.version == 2
    own = p.own_funds_evidence
    assert own.order_type == 'MARKET' and own.direction == 'BUY'
    assert own.requested_lots == intent.candidate.requested_lots == 1
    assert own.lot_size == 10
    assert own.own_reservation_nano == intent.reserved_cash_kopecks * 10**7
    assert own.all_local_reservations_nano == own.own_reservation_nano
    assert own.own_money_nano == 1_000_000 * 10**9
    assert p.free_investable_cash.minor_units == own.free_after_reservations_nano
    # STEP11 primary CL4 read is additional; the last two calls remain locked.
    assert x.p.cash_read_calls[pbefore:] == [
        ('positions', ACCOUNT), ('max_lots', ACCOUNT, 'uid-lkoh'),
        ('max_lots', ACCOUNT, UID), ('positions', ACCOUNT), ('max_lots', ACCOUNT, UID)]
    assert p == LockedDispatchProof.from_canonical_dict(p.to_canonical_dict())
    p.verify_identity(raw_intent_id=intent.intent_id, identity_key=x.c.execution_adapter.cl7_identity_key)
    assert x.p.order_calls == 1


def test_own_limit_reads_and_fake_post_hold_actual_ledger_writer_lock(exact_case):
    x = exact_case
    hits = []
    def check(*args):
        con = sqlite3.connect(x.c.execution_adapter.cl7_ledger_store.root/'store.sqlite3', timeout=0)
        try:
            if len(hits) < 3:
                # CL4 and both configured CL5 limits are pre-lock, NOT locked evidence.
                con.execute('BEGIN IMMEDIATE')
                con.rollback()
                hits.append('primary_prelock')
            else:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    con.execute('BEGIN IMMEDIATE')
                hits.append('own_funds_locked')
        finally:
            con.close()
    x.p.on_cash_positions = check
    x.p.on_cash_limits = check
    assert dispatch(x).execution_status == 'SUBMITTED'
    assert hits == ['primary_prelock'] * 3 + ['own_funds_locked'] * 2 and x.at_post


def test_default_bestprice_is_not_silently_rewritten(exact_case):
    x = exact_case
    assert x.c.central_order_coordinator.execution_order_type == 'BESTPRICE'
    calls = len(x.p.cash_read_calls)
    assert dispatch(x).execution_status == 'CL7_OWN_FUNDS_BLOCKED'
    assert x.c.central_order_coordinator.manager.state().queued[0].candidate.order_type == 'BESTPRICE'
    assert x.p.cash_read_calls[calls:] == [('positions', ACCOUNT),
        ('max_lots', ACCOUNT, 'uid-lkoh'), ('max_lots', ACCOUNT, UID)]
    assert_no_attempt(x)


@pytest.mark.parametrize('damage', [
    'missing_limits', 'margin_only', 'missing_market_cap', 'market_cap_zero', 'market_cap_bool',
    'market_cap_float', 'market_cap_negative', 'market_cap_leading_zero', 'market_cap_overflow',
    'missing_money', 'money_bool', 'money_float', 'negative_money', 'nano_bool', 'nano_range',
    'foreign_currency', 'mixed_currency', 'unknown_account', 'wrong_instrument', 'wrong_uid',
    'money_above_position', 'non_mapping', 'nonfinite',
])
def test_bad_limits_never_use_margin_or_valuation(exact_case, damage):
    x = exact_case
    raw = limits()
    own = raw['buyLimits']
    if damage == 'missing_limits': del raw['buyLimits']
    elif damage == 'margin_only': del raw['buyLimits']
    elif damage == 'missing_market_cap': del own['buyMaxMarketLots']
    elif damage == 'market_cap_zero': own['buyMaxMarketLots'] = '0'
    elif damage == 'market_cap_bool': own['buyMaxMarketLots'] = True
    elif damage == 'market_cap_float': own['buyMaxMarketLots'] = 1.0
    elif damage == 'market_cap_negative': own['buyMaxMarketLots'] = '-1'
    elif damage == 'market_cap_leading_zero': own['buyMaxMarketLots'] = '01'
    elif damage == 'market_cap_overflow': own['buyMaxMarketLots'] = str(2**63)
    elif damage == 'missing_money': del own['buyMoneyAmount']
    elif damage == 'money_bool': own['buyMoneyAmount']['units'] = True
    elif damage == 'money_float': own['buyMoneyAmount']['units'] = 1000000.0
    elif damage == 'negative_money': own['buyMoneyAmount']['units'] = '-1'
    elif damage == 'nano_bool': own['buyMoneyAmount']['nano'] = True
    elif damage == 'nano_range': own['buyMoneyAmount']['nano'] = 10**9
    elif damage == 'foreign_currency': raw['currency'] = 'usd'
    elif damage == 'mixed_currency': raw['currency'] = 'Rub'
    elif damage == 'unknown_account': raw['accountId'] = 'other-account'
    elif damage == 'wrong_instrument': raw['instrumentId'] = 'other-instrument'
    elif damage == 'wrong_uid': raw['instrumentUid'] = 'other-instrument'
    elif damage == 'money_above_position': own['buyMoneyAmount'] = money(1_000_001)
    elif damage == 'non_mapping': raw = []
    elif damage == 'nonfinite': raw['untrusted'] = float('nan')
    inject_at_final_own_funds_read(x, raw)
    assert dispatch(x).execution_status == 'CL7_OWN_FUNDS_BLOCKED'
    assert_no_attempt(x)


@pytest.mark.parametrize('damage', [
    'account', 'loading', 'loading_integer', 'missing_rub', 'duplicate_currency',
    'negative', 'nano', 'futures', 'options', 'non_mapping',
])
def test_bad_positions_refuse_before_limits_read(exact_case, damage):
    x = exact_case
    raw = {'accountId': ACCOUNT, 'money': [money(1_000_000)], 'blocked': []}
    if damage == 'account': raw['accountId'] = 'other'
    elif damage == 'loading': raw['limitsLoadingInProgress'] = True
    elif damage == 'loading_integer': raw['limitsLoadingInProgress'] = 0
    elif damage == 'missing_rub': raw['money'] = [money(1_000_000, 'usd')]
    elif damage == 'duplicate_currency': raw['money'].append(money(1, 'RUB'))
    elif damage == 'negative': raw['money'][0]['units'] = '-1'
    elif damage == 'nano': raw['blocked'] = [money(0)]; raw['blocked'][0]['nano'] = True
    elif damage in {'futures', 'options'}: raw[damage] = [{}]
    elif damage == 'non_mapping': raw = []
    x.p.cash_positions_override = raw
    # STEP11 catches malformed cash earlier, before the own-funds gate. Missing RUB
    # is a valid zero accounting observation, which makes the CL4 context disagree.
    expected = 'CL7_CONTEXT_BLOCKED' if damage == 'missing_rub' else 'CL7_RECONCILIATION_BLOCKED'
    assert dispatch(x).execution_status == expected
    assert_no_attempt(x)


@pytest.mark.parametrize('which', ['positions', 'limits'])
def test_read_failures_are_not_retry_or_exception_text(exact_case, monkeypatch, which):
    x = exact_case
    calls = []
    def fail(*a, **k):
        calls.append(True)
        raise RuntimeError('PRIVATE_PROVIDER_RAW_CANARY')
    if which == 'positions':
        monkeypatch.setattr(x.p, 'get_positions', fail)
    else:
        original = x.p.get_max_lots
        reads = []
        def fail_final(*args, **kwargs):
            reads.append(1)
            if len(reads) > 2: return fail(*args, **kwargs)
            return original(*args, **kwargs)
        monkeypatch.setattr(x.p, 'get_max_lots', fail_final)
    outcome = dispatch(x)
    expected = 'CL7_BROKER_READ_FAILED' if which == 'positions' else 'CL7_OWN_FUNDS_BLOCKED'
    assert outcome.execution_status == expected
    assert 'PRIVATE_PROVIDER_RAW_CANARY' not in str(outcome)
    assert calls == [True]
    assert_no_attempt(x)


@pytest.mark.parametrize('kind', ['wall_slow', 'wall_backwards', 'mono_slow', 'mono_backwards'])
def test_bad_clock_prevents_marker_and_post(exact_case, kind):
    x = exact_case
    a = x.c.execution_adapter
    ticks = [1]
    a.cl7_monotonic_ns = lambda: ticks[0]
    def advance():
        if kind == 'wall_slow': x.p.clock_at += timedelta(seconds=6)
        elif kind == 'wall_backwards': x.p.clock_at -= timedelta(seconds=1)
        elif kind == 'mono_slow': ticks[0] += 6_000_000_000
        else: ticks[0] = 0
    x.p.on_cash_positions = advance
    # V3 primary accounting read now checks both clocks before subsequent reads.
    assert dispatch(x).execution_status == 'CL7_CONTEXT_STALE'
    assert_no_attempt(x)


def test_primary_accounting_rejects_nonzero_blocked_until_basis_is_qualified(exact_case):
    # STEP11 explicitly narrows the supported accounting basis. Do not pretend
    # that the previous successful blocked-balance case remains qualified.
    x = exact_case
    x.p.cash_limits_override = limits(cash=1800)
    x.p.cash_blocked_rub = 700
    assert dispatch(x).execution_status == 'CL7_RECONCILIATION_BLOCKED'
    assert_no_attempt(x)


def test_current_intent_reservation_counted_once(exact_case):
    x = exact_case
    # 105 RUB/share * 10 shares + the actual Central 100 bps buffer.
    x.p.cash_limits_override = limits(cash='1060.50', market_lots='1')
    assert dispatch(x).execution_status == 'SUBMITTED'
    _, p = proof(x)
    assert p.free_investable_cash.minor_units == 0
    assert p.own_funds_evidence.own_money_nano == p.reserved_cash.minor_units


def test_reserved_buffer_not_silently_dropped(exact_case):
    x = exact_case
    inject_at_final_own_funds_read(x, limits(cash=1050, market_lots='1'))
    assert dispatch(x).execution_status == 'CL7_OWN_FUNDS_BLOCKED'
    assert_no_attempt(x)


@pytest.mark.parametrize('field', ['own_money_nano', 'positions_sha256', 'limits_sha256', 'request_sha256', 'metadata_sha256'])
def test_proof_hmac_binds_own_evidence(exact_case, field):
    x = exact_case
    assert dispatch(x).execution_status == 'SUBMITTED'
    intent, p = proof(x)
    raw = p.to_canonical_dict()
    e = raw['own_funds_evidence']
    e[field] = str(int(e[field]) - 1) if field.endswith('_nano') else 'a'*64
    try:
        changed = LockedDispatchProof.from_canonical_dict(raw)
        with pytest.raises(CL7RuntimeError):
            changed.verify_identity(raw_intent_id=intent.intent_id, identity_key=x.c.execution_adapter.cl7_identity_key)
    except CL7RuntimeError:
        pass  # A field that violates the financial cap is rejected even before HMAC.


@pytest.mark.parametrize('mutation', ['remove_evidence', 'version_one', 'domain_old', 'extra_key', 'bool_version'])
def test_v2_cannot_be_downgraded_or_misparsed(exact_case, mutation):
    x = exact_case
    assert dispatch(x).execution_status == 'SUBMITTED'
    intent, p = proof(x)
    raw = p.to_canonical_dict()
    if mutation == 'remove_evidence': del raw['own_funds_evidence']
    elif mutation == 'version_one': raw['version'] = 1
    elif mutation == 'domain_old': raw['domain'] = 'v3.10-cl7-locked-dispatch-proof'
    elif mutation == 'extra_key': raw['additional_permission'] = True
    else: raw['version'] = True
    with pytest.raises(CL7RuntimeError): LockedDispatchProof.from_canonical_dict(raw)


def test_old_v1_proof_still_roundtrips_without_granting_v2(exact_case):
    x = exact_case
    assert dispatch(x).execution_status == 'SUBMITTED'
    intent, p = proof(x)
    from dataclasses import fields
    values = {f.name: getattr(p, f.name) for f in fields(p)
              if f.name not in {'intent_scope_sha256', 'proof_identity_sha256'}}
    values.update(version=1, own_funds_evidence=None)
    old = LockedDispatchProof.build(raw_intent_id=intent.intent_id,
                                    identity_key=x.c.execution_adapter.cl7_identity_key, **values)
    assert old.version == 1 and 'own_funds_evidence' not in old.to_canonical_dict()
    assert old.to_canonical_dict()['domain'] == 'v3.10-cl7-locked-dispatch-proof'
    assert LockedDispatchProof.from_canonical_dict(old.to_canonical_dict()) == old
    assert old.sha256 != p.sha256


def test_custom_proof_builder_cannot_bypass_desktop_policy(exact_case):
    x = exact_case
    calls = []
    x.c.execution_adapter.cl7_proof_builder = lambda *args: calls.append(True)
    assert dispatch(x).execution_status == 'CL7_DISPATCH_PROOF_INVALID'
    assert_no_attempt(x)
    assert not calls


def test_metadata_change_during_own_read_blocks(exact_case):
    x = exact_case
    x.p.on_cash_limits = lambda *_: (x.root/'portfolio_risk_metadata.json').write_bytes(b'CHANGED')
    outcome = dispatch(x)
    assert outcome.execution_status != 'SUBMITTED'
    assert_no_attempt(x)


def test_policy_removal_is_not_silent_legacy_fallback(exact_case):
    x = exact_case
    x.c.execution_adapter.cl7_own_funds_policy = None
    with pytest.raises(RuntimeError): _desktop_cycle(x.c)
    assert x.p.order_calls == 0


def test_factory_recomposition_with_different_choice_is_refused(exact_case):
    x = exact_case
    import desktop_gui
    with pytest.raises(RuntimeError): desktop_gui._compose_production_gui_runtime(x.root)
    assert x.p.order_calls == 0


def test_no_new_own_reads_after_pending(exact_case):
    x = exact_case
    assert dispatch(x).execution_status == 'SUBMITTED'
    intent, _ = proof(x)
    calls = len(x.p.cash_read_calls)
    result = x.c.execution_adapter.dispatch_next(x.c.portfolio_repository, expected_intent_id=intent.intent_id)
    assert result.status == 'CL7_DISPATCH_PENDING'
    assert len(x.p.cash_read_calls) == calls and x.p.order_calls == 1


@pytest.mark.parametrize('phase', ['before_marker', 'after_marker'])
@pytest.mark.parametrize('clock_kind', ['wall', 'monotonic_only'])
def test_expiry_during_custody_writes_never_uses_stale_budget(exact_case, monkeypatch, phase, clock_kind):
    x = exact_case
    a = x.c.execution_adapter
    ticks = [1]
    a.cl7_monotonic_ns = lambda: ticks[0]
    def expire():
        if clock_kind == 'wall': x.p.clock_at += timedelta(seconds=6)
        else: ticks[0] += 6_000_000_000
    if phase == 'before_marker':
        store = x.c.central_order_coordinator.manager.store
        original = store._save_unlocked
        def slow(state):
            result = original(state)
            if state.blocking_intent is not None and state.blocking_intent.status == 'IN_FLIGHT':
                expire()
            return result
        monkeypatch.setattr(store, '_save_unlocked', slow)
    else:
        owner = a.cash_authority_manager
        original = owner._record_dispatch_attempt_locked
        def slow(*args, **kwargs):
            result = original(*args, **kwargs)
            expire()
            return result
        monkeypatch.setattr(owner, '_record_dispatch_attempt_locked', slow)
    outcome = dispatch(x)
    assert x.p.order_calls == 0 and not x.at_post
    state = a.cash_authority_manager.status()
    central = x.c.central_order_coordinator.manager.state()
    if phase == 'before_marker':
        assert outcome.execution_status == 'CL7_CONTEXT_STALE'
        assert state.post_attempt_count == 0
        assert central.intents[-1].status == 'FAILED'
        assert central.intents[-1].outcome == 'PRE_SUBMIT_FAILED'
        assert state.state is RuntimeCashAuthorityState.EXACT_CASH_ARMED
    else:
        assert outcome.execution_status == 'CL7_RECOVERY_REQUIRED'
        assert state.post_attempt_count == 1
        assert state.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
        assert central.blocking_intent.status == 'IN_FLIGHT'
        calls = len(x.p.cash_read_calls)
        again = a.dispatch_next(x.c.portfolio_repository, expected_intent_id=central.blocking_intent.intent_id)
        assert again.status == 'CL7_DISPATCH_PENDING'
        assert len(x.p.cash_read_calls) == calls and x.p.order_calls == 0


@pytest.mark.parametrize('spelling', ['rub', 'RUB'])
def test_raw_currency_spelling_remains_hashed_not_rewritten(exact_case, spelling):
    x = exact_case
    raw = limits()
    raw['currency'] = spelling
    raw['buyLimits']['buyMoneyAmount']['currency'] = spelling
    x.p.cash_limits_override = deepcopy(raw)
    assert dispatch(x).execution_status == 'SUBMITTED'
    from trading_robot.exact_own_funds import digest
    _, p = proof(x)
    assert p.own_funds_evidence.limits_sha256 == digest(raw)
    assert x.p.cash_limits_override == raw


@pytest.mark.parametrize('choice', ['market', 'LIMIT', '', True, None])
def test_invalid_explicit_order_choice_before_transport(desktop_case, choice):
    import desktop_gui
    with pytest.raises(RuntimeError):
        desktop_gui._compose_production_gui_runtime(desktop_case.root,
            secret_provider=desktop_case.secret, transport_factory=desktop_case.factory,
            execution_order_type=choice)
    assert not desktop_case.transports and desktop_case.provider.order_calls == 0


def test_actual_client_max_lots_route_is_sandbox_market_no_estimate_price():
    from trading_robot.tbank_sandbox import TBankSandboxClient
    calls = []
    class Transport:
        def _post(self, service, method, body):
            calls.append((service, method, body))
            return limits()
    result = TBankSandboxClient.get_max_lots(Transport(), ACCOUNT, UID, price=None)
    assert result == limits()
    assert calls == [('SandboxService', 'GetSandboxMaxLots', {'accountId': ACCOUNT, 'instrumentId': UID})]


@pytest.mark.parametrize('damage', ['sell_margin_only', 'insufficient', 'valid'])
def test_sell_own_lot_ceiling_in_isolated_policy(exact_case, damage):
    """Policy-only SELL input; not exact SELL post-fill qualification."""
    x = exact_case
    x.p.market_open = False
    assert _desktop_cycle(x.c).status == 'QUEUED'
    manager = x.c.central_order_coordinator.manager
    state = manager.state()
    original = state.queued[0]
    candidate = replace(original.candidate, current_lots=3, target_lots=1)
    # Isolated structured input: the new policy does not authorize this candidate;
    # integration independently requires real canonical/Risk/CL7 validation.
    from uuid import NAMESPACE_URL, uuid5
    key = candidate.idempotency_key()
    changed = replace(original, candidate=candidate, reserved_cash_kopecks=0,
        idempotency_key=key, intent_id=str(uuid5(NAMESPACE_URL, "central-order-v1|" + key)))
    state = replace(state, intents=(changed,))
    raw = limits(cash=0)
    raw.pop('buyLimits')
    if damage == 'sell_margin_only':
        raw['sellMarginLimits'] = raw.pop('sellLimits')
    elif damage == 'insufficient': raw['sellLimits']['sellMaxLots'] = '1'
    else: raw['sellLimits']['sellMaxLots'] = '2'
    x.p.cash_limits_override = raw
    from trading_robot.exact_own_funds import OwnFundsError
    def acquire():
        return x.c.execution_adapter.cl7_own_funds_policy.acquire(x.p, changed, state,
            clock=x.c.execution_adapter.cl7_clock, monotonic_ns=lambda: 1)
    if damage != 'valid':
        with pytest.raises(OwnFundsError): acquire()
    else:
        evidence = acquire()
        assert evidence.requested_lots == 2 and evidence.own_money_nano == 0
        assert evidence.own_reservation_nano == 0 and evidence.direction == 'SELL'
    assert x.p.order_calls == 0
