"""STEP38: exactly checked cash projection reuse within ONE live SQLite lease."""
from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
import threading

import pytest
from test_q7a_source_versioned_runtime_adapter import (
    base_desktop_case, desktop_case, exact_case, refresh_case, read_case, op_case,
    adapter, queued_inputs,
)
from trading_robot import versioned_operational_store as v4
from trading_robot import versioned_runtime_adapter as binding
from trading_robot import versioned_financial_readers as readers


def test_live_binding_reuses_one_fully_validated_graph(op_case, monkeypatch, request):
    a = adapter(op_case)
    inputs = queued_inputs(op_case)
    count = []
    original = v4._validate
    def counted(*args, **kwargs):
        count.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(v4, '_validate', counted)
    with a.locked_snapshot(monotonic_ns=lambda: 0) as view:
        before = len(count)
        result = a.build_request_binding(view, **inputs)
        a.validate_request_binding(result, view, now=inputs['evaluated_at'], **inputs)
        assert len(count) == before == 1
        assert view.project_cash().expected_cash_nano == view.snapshot().cash_nano
    request.node.user_properties.extend([('full_graph_validations_per_live_lease',len(count)),
                                         ('underlying_evidence_revalidated',True)])
    # A new view never inherits the old lease/cached cash projection.
    with a.locked_snapshot(monotonic_ns=lambda: 0) as new_view:
        assert len(count) == 2
        with pytest.raises(binding.VersionedRuntimeBindingError):
            a.validate_request_binding(result, new_view, now=inputs['evaluated_at'], **inputs)


def test_repeated_derivation_no_longer_consumes_lease(op_case, monkeypatch, request):
    a = adapter(op_case); inputs = queued_inputs(op_case); ticks = [0]; calls = []
    old = v4._validate
    def expensive(*args, **kwargs):
        out = old(*args, **kwargs)
        ticks[0] += 2_000_000_000
        calls.append(1)
        return out
    monkeypatch.setattr(v4, '_validate', expensive)
    # Deterministic model of two seconds per complete graph validation, not a
    # claim about live latency. Original repeated validation exhausts 5 seconds.
    with a.locked_snapshot(monotonic_ns=lambda: ticks[0]) as view:
        result = a.build_request_binding(view, **inputs)
        a.validate_request_binding(result, view, now=inputs['evaluated_at'], **inputs)
        assert ticks[0] == 2_000_000_000 and len(calls) == 1
    request.node.user_properties.extend([('modeled_validation_cost_ns',ticks[0]),('full_derivations',len(calls))])


@pytest.mark.parametrize('damage',['expiry','thread','rollback','restart','restart_semicolon','bytes','write_restore','schema','raw','pins','public_injection'])
def test_projection_reuse_does_not_skip_live_guards(op_case, damage):
    a = adapter(op_case); inputs = queued_inputs(op_case); ticks=[0]
    # Some invalidations are intentionally detected again on context exit.
    with pytest.raises(Exception) as caught:
        with a.locked_snapshot(monotonic_ns=lambda:ticks[0]) as view:
            result=a.build_request_binding(view,**inputs)
            if damage=='expiry':ticks[0]=5_000_000_001
            elif damage=='thread':
                errors=[]
                def other():
                    try:view.project_cash()
                    except BaseException as error:errors.append(error)
                t=threading.Thread(target=other);t.start();t.join()
                assert len(errors)==1
                raise errors[0]
            elif damage=='rollback':view._connection.execute('ROLLBACK')
            elif damage in ('restart','restart_semicolon'):
                statement='/* end lease */ ROLLBACK' if damage=='restart' else '; ROLLBACK'
                view._connection.execute(statement);view._connection.execute('BEGIN IMMEDIATE')
            elif damage in ('bytes','write_restore'):
                row=view._connection.execute('SELECT seed FROM cl2_v4_base WHERE singleton=1').fetchone()[0]
                view._connection.execute("UPDATE cl2_v4_base SET seed=? WHERE singleton=1", (row+' ',))
                if damage=='write_restore':view._connection.execute('UPDATE cl2_v4_base SET seed=? WHERE singleton=1',(row,))
            elif damage=='schema':view._connection.execute('CREATE TABLE extra(a)')
            elif damage in ('raw','pins'):
                from trading_robot.cash_availability import project_central_reservations
                from trading_robot.broker_read_adapters import BrokerEnvironment
                args=dict(inputs)
                args.pop('intent');central=args.pop('central_state');args.pop('expected_intent_sha256')
                args.pop('own_policy');args.pop('own_funds')
                args.update(codec_registry=tuple(a.store._registry.values()), identity_key=a.store._key,
                    identity_key_id=a.store._key_id,account_scope_sha256=a.store._account,
                    reservations=project_central_reservations(central,evaluated_at=inputs['evaluated_at'],
                        environment=BrokerEnvironment.SANDBOX,identity_key=a.store._key,
                        identity_key_id=a.store._key_id,account_scope_sha256=a.store._account))
                raw=view.export_bytes();pins=a.pins
                if damage=='raw':raw+=b' '
                else:pins=replace(pins,ledger_revision=pins.ledger_revision+1)
                readers._build(raw,pins=pins,_locked_view=view,**args)
            elif damage=='public_injection':
                readers.build_versioned_cash_context(view.export_bytes(),_locked_view=view)
            a.validate_request_binding(result,view,now=inputs['evaluated_at'],**inputs)
    assert not isinstance(caught.value,(NameError,AttributeError,KeyError)),repr(caught.value)


def test_cached_cash_does_not_cache_owner_or_freshness_decisions(op_case):
    from test_q7a_source_versioned_operational_store import _later
    a=adapter(op_case);inputs=queued_inputs(op_case)
    with a.locked_snapshot(monotonic_ns=lambda:0) as view:
        proof=a.build_request_binding(view,**inputs)
        with pytest.raises(binding.VersionedRuntimeBindingError):
            a.validate_request_binding(proof,view,now=_later(inputs['evaluated_at'],6),**inputs)
        wrong=dict(inputs,portfolio=replace(inputs['portfolio'],portfolio_revision=inputs['portfolio'].portfolio_revision+1))
        with pytest.raises(binding.VersionedRuntimeBindingError):a.build_request_binding(view,**wrong)
        conn=sqlite3.connect(a.root/'store.sqlite3',timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError):conn.execute('BEGIN IMMEDIATE')
        finally:conn.close()
    with pytest.raises(v4.OperationalStoreError):view.project_cash()


def test_initial_graph_validation_must_still_fit_lease(op_case,monkeypatch):
    a=adapter(op_case);ticks=[0];old=v4._validate
    def slow(*args,**kwargs):
        graph=old(*args,**kwargs);ticks[0]+=5_000_000_001;return graph
    monkeypatch.setattr(v4,'_validate',slow)
    with pytest.raises(v4.OperationalStoreError,match='EXPIRED'):
        with a.locked_snapshot(monotonic_ns=lambda:ticks[0]):pytest.fail('expired view was issued')


def test_raw_comparison_cost_cannot_extend_lease(op_case,monkeypatch):
    a=adapter(op_case);ticks=[0]
    with pytest.raises(v4.OperationalStoreError,match='EXPIRED'):
        with a.locked_snapshot(monotonic_ns=lambda:ticks[0]) as view:
            old=v4._export
            def slow(conn):
                raw=old(conn);ticks[0]+=5_000_000_001;return raw
            monkeypatch.setattr(v4,'_export',slow)
            view.project_cash()
