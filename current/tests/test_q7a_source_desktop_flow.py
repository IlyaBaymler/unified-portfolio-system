"""STEP4: real desktop factory, synthetic credentials/transport, no live IO.

Portfolio states are seeded locally, not produced by a broker refresh.  The
factory builds its own real Risk/Central/adapter graph; the seed helper's owners
are never injected into it.  Economic submission tests are LEGACY_ACTIVE only.
"""
from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

import desktop_gui
from test_q7a_source_natural_cycle import (
    ACCOUNT, NOW, UID, DeterministicProvider, _case, _cycle,
)
from test_v3_10_q7_preparation_runtime import _provider
from trading_robot.dashboard_view import latest_sandbox_decisions
from trading_robot.gui_runtime_controller import GuiRuntimeBlockedError, _CoordinatingHooks
from trading_robot.portfolio_risk_runtime import PortfolioRiskRuntime
from trading_robot.sandbox_execution_adapter import SandboxDispatchResult
from trading_robot.tbank_sandbox import TBankAPIError


class DesktopProvider(DeterministicProvider):
    def __init__(self, target=1):
        super().__init__(target)
        self.candle_calls = 0
        self.instrument_calls = 0
        self.closed = False

    def get_candles(self, *args, **kwargs):
        self.candle_calls += 1
        return super().get_candles(*args, **kwargs)

    def get_instrument_by_id(self, instrument_id):
        self.instrument_calls += 1
        return super().get_instrument_by_id(instrument_id)

    def close(self):
        self.closed = True


def _write_metadata(root, rows=None, raw=None):
    p = root / "portfolio_risk_metadata.json"
    if raw is None:
        if rows is None:
            rows = [{"instrument_id": uid, "lot_size": 10, "asset_class": "share", "currency": "rub"}
                    for uid in ("uid-sber", "uid-lkoh")]
        raw = json.dumps({"version": 1, "instruments": rows}).encode()
    p.write_bytes(raw)
    p.with_name(p.name + ".sha256").write_text(sha256(raw).hexdigest() + "\n")
    return p


@pytest.fixture
def desktop_case(tmp_path, monkeypatch):
    # Only seed disposable on-disk stores. All returned helper owners are discarded.
    _case(tmp_path, current=0, target=1)
    from trading_robot.cash_ledger_persistence import CashLedgerStore
    from trading_robot.cash_ledger_opening_reconciliation import CL4_OPENING_CODEC
    from trading_robot.broker_read_adapters import TBANK_OPERATION_CODEC
    # Fresh synthetic store only: preserve explicit LEGACY_ACTIVE authority.
    from trading_robot.runtime_cash_authority import RuntimeCashAuthorityStore
    RuntimeCashAuthorityStore(tmp_path).bootstrap(transition_at="2026-08-13T12:00:00.000000000Z")
    ledger = CashLedgerStore.create(tmp_path / "cash_ledger_v3_10.sqlite3",
                                   (CL4_OPENING_CODEC, TBANK_OPERATION_CODEC))
    ledger.close()
    path = _write_metadata(tmp_path)
    provider = DesktopProvider()
    transports = []
    secret = _provider(TBANK_SANDBOX_ACCOUNT_ID=ACCOUNT)
    monkeypatch.setattr(desktop_gui, "_PRODUCTION_COMPOSITION", None)

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["token"] == secret.get("TBANK_SANDBOX_TOKEN")
        transports.append(provider)
        return provider

    controllers = []

    def compose():
        # Tests opting into MARKET do so explicitly, before factory construction.
        selected = getattr(case, "execution_order_type", "BESTPRICE")
        options = {} if selected == "BESTPRICE" else {"execution_order_type": selected}
        c = desktop_gui._compose_production_gui_runtime(
            tmp_path, secret_provider=secret, transport_factory=factory, **options,
        )
        c.cycle_source.clock = lambda: provider.clock_at
        controllers.append(c)
        return c

    case = SimpleNamespace(root=tmp_path, path=path, provider=provider, transports=transports,
                           secret=secret, compose=compose, factory=factory)
    yield case
    seen = set()
    for c in controllers:
        ledger = c.execution_adapter.cl7_ledger_store
        if id(ledger) not in seen:
            ledger.close()
            seen.add(id(ledger))
    desktop_gui._PRODUCTION_COMPOSITION = None


def _desktop_cycle(controller):
    now, latest, hooks = controller.cycle_source()
    runtime = next(r for r in controller.runtime_store.load(expected_account_id=ACCOUNT)
                   if r.config.instrument_id == UID)
    return _CoordinatingHooks(controller, hooks).evaluate_closed_candle(
        runtime, latest[runtime.runtime_key], now,
    )


def _observed(controller):
    return controller.latest_cycle_outcomes()[UID]


def _display(controller):
    return latest_sandbox_decisions(
        controller.journal.recent(limit=50), session_id=controller.session_id,
        account_scope_sha256=controller.account_scope_sha256, instrument_ids=[UID],
    )[UID]


def test_factory_binds_one_real_risk_owner_before_any_provider_read(desktop_case):
    c = desktop_case.compose()
    assert type(c.portfolio_risk_runtime) is PortfolioRiskRuntime
    assert set(c.portfolio_risk_runtime.instrument_metadata) == {"uid-sber", "uid-lkoh"}
    assert c.central_order_coordinator.portfolio_risk_runtime is c.portfolio_risk_runtime
    assert c.execution_adapter.portfolio_risk_runtime is c.portfolio_risk_runtime
    assert c.cycle_source.risk_runtime is c.central_order_coordinator.risk_runtime
    assert desktop_case.provider.candle_calls == desktop_case.provider.quote_calls == 0
    assert desktop_case.provider.order_calls == 0
    assert len(desktop_case.transports) == 1
    assert desktop_case.compose() is c
    assert len(desktop_case.transports) == 1


def test_factory_to_empty_buy_fake_submission_no_ready_override(desktop_case):
    c = desktop_case.compose()
    provider = desktop_case.provider
    provider.market_open = True
    provider.post_behavior = "submitted"
    assert c.portfolio_repository.load(expected_account_id=ACCOUNT).state_status == "EMPTY"
    result = _desktop_cycle(c)
    assert result.status == "QUEUED", result.to_dict()
    assert provider.order_calls == 1
    assert provider.instrument_calls == 0  # Same verified lot source as Risk.
    observed = _observed(c)
    assert observed.coordination_status == "QUEUED"
    assert observed.execution_status == "SUBMITTED"
    assert observed.intent_binding == "MATCHED"
    assert observed.order_was_sent is True
    assert observed.order_may_have_been_sent is False
    assert observed.audit_persisted is True
    assert observed.automatic_retry is False
    assert _display(c).status == "QUEUED / SUBMITTED"
    assert c.portfolio_repository.load(expected_account_id=ACCOUNT).state_status == "EMPTY"
    assert result.intent_id not in json.dumps(observed.to_dict())
    again = _desktop_cycle(c)
    assert again.status == "ACCOUNT_BLOCKED"
    assert provider.order_calls == 1


def test_factory_hold_no_quote_no_intent_no_post(desktop_case):
    desktop_case.provider.target = 0
    desktop_case.provider.fail_quote = True
    c = desktop_case.compose()
    result = _desktop_cycle(c)
    assert result.status == "NO_POSITION_CHANGE"
    o = _observed(c)
    assert o.execution_status == "NOT_DISPATCHED"
    assert o.dispatch_invoked is False
    assert o.action == "HOLD"
    assert desktop_case.provider.quote_calls == desktop_case.provider.order_calls == 0
    assert c.central_order_coordinator.manager.state().intents == ()
    assert _display(c).status == "NO_POSITION_CHANGE / NOT_DISPATCHED"


@pytest.mark.parametrize("damage", [
    "missing", "checksum_missing", "checksum_bad", "currency_missing", "currency_usd",
    "lot_bool", "lot_string", "lot_float", "lot_zero", "lot_negative", "duplicate_id",
    "wrong_id", "extra_id", "missing_id", "duplicate_json_key", "version_bool",
    "unknown_field", "root_list", "blank", "symlink", "sidecar_symlink",
])
def test_factory_rejects_invalid_metadata_before_transport(desktop_case, damage):
    path = desktop_case.path
    doc = json.loads(path.read_bytes())
    row = doc["instruments"][0]
    if damage == "missing":
        path.unlink()
    elif damage == "checksum_missing":
        path.with_name(path.name + ".sha256").unlink()
    elif damage == "checksum_bad":
        path.with_name(path.name + ".sha256").write_text("0" * 64)
    elif damage == "currency_missing":
        del row["currency"]
    elif damage == "currency_usd":
        row["currency"] = "USD"
    elif damage.startswith("lot_"):
        row["lot_size"] = {"bool": True, "string": "10", "float": 10.0, "zero": 0, "negative": -1}[damage[4:]]
    elif damage == "duplicate_id":
        doc["instruments"].append(dict(row))
    elif damage == "wrong_id":
        row["instrument_id"] = "OTHER_INSTRUMENT"
    elif damage == "extra_id":
        doc["instruments"].append({**row, "instrument_id": "OTHER_INSTRUMENT"})
    elif damage == "missing_id":
        doc["instruments"].pop()
    elif damage == "version_bool":
        doc["version"] = True
    elif damage == "unknown_field":
        row["secret"] = "PRIVATE_CANARY"
    elif damage == "root_list":
        doc = []
    elif damage == "blank":
        _write_metadata(desktop_case.root, raw=b" ")
    elif damage in {"symlink", "sidecar_symlink"}:
        target = path if damage == "symlink" else path.with_name(path.name + ".sha256")
        other = target.with_name(target.name + ".real")
        target.rename(other)
        target.symlink_to(other)
    elif damage == "duplicate_json_key":
        _write_metadata(desktop_case.root, raw=b'{"version":1,"version":1,"instruments":[]}')
    if damage not in {"missing", "checksum_missing", "checksum_bad", "blank", "symlink",
                      "sidecar_symlink", "duplicate_json_key"}:
        _write_metadata(desktop_case.root, raw=json.dumps(doc).encode())
    with pytest.raises(GuiRuntimeBlockedError, match="GUI_RISK_METADATA") as caught:
        desktop_case.compose()
    assert "PRIVATE" not in str(caught.value)
    assert str(desktop_case.root) not in str(caught.value)
    assert desktop_case.transports == []
    assert desktop_gui._PRODUCTION_COMPOSITION is None


@pytest.mark.parametrize("damage", ["profile_runtime", "missing_runtime", "runtime_account", "missing_profiles"])
def test_factory_rejects_scope_mismatch_before_transport(desktop_case, damage):
    from trading_robot.instrument_runtime import InstrumentRuntimeStore
    from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
    store = InstrumentRuntimeStore(desktop_case.root / "instrument_runtimes.json")
    rows = store.load(expected_account_id=ACCOUNT)
    if damage == "missing_runtime":
        store.save(rows[:1])
    elif damage == "profile_runtime":
        store.save((replace(rows[0], config=replace(rows[0].config, instrument_id="other")), rows[1]))
    elif damage == "runtime_account":
        store.save(tuple(replace(r, config=replace(r.config, account_id="other-account")) for r in rows))
    else:
        MultiInstrumentProfileStore(desktop_case.root / "multi_instrument_profiles.json").save_mode("SANDBOX_EXECUTION", ())
    with pytest.raises(GuiRuntimeBlockedError, match="GUI_RISK_METADATA_SCOPE_INVALID"):
        desktop_case.compose()
    assert desktop_case.transports == []


@pytest.mark.parametrize("target", ["disk", "checksum", "risk_mapping", "profile"])
def test_changed_binding_blocks_next_provider_cycle(desktop_case, target):
    c = desktop_case.compose()
    if target == "disk":
        rows = json.loads(desktop_case.path.read_bytes())["instruments"]
        rows[0]["lot_size"] = 20
        _write_metadata(desktop_case.root, rows)
    elif target == "checksum":
        desktop_case.path.with_name(desktop_case.path.name + ".sha256").write_text("0" * 64)
    elif target == "risk_mapping":
        c.portfolio_risk_runtime.instrument_metadata.clear()
    else:
        c.profile_store.save_mode("SANDBOX_EXECUTION", ())
    with pytest.raises(GuiRuntimeBlockedError, match="GUI_RISK_METADATA"):
        c.cycle_source()
    assert desktop_case.provider.candle_calls == desktop_case.provider.quote_calls == 0
    assert desktop_case.provider.order_calls == 0


def test_cached_factory_rechecks_metadata_without_second_transport(desktop_case):
    desktop_case.compose()
    desktop_case.path.unlink()
    with pytest.raises(GuiRuntimeBlockedError, match="GUI_RISK_METADATA"):
        desktop_case.compose()
    assert len(desktop_case.transports) == 1


def test_metadata_change_during_quote_blocks_admission_and_dispatch(desktop_case):
    c = desktop_case.compose()
    desktop_case.provider.on_quote = lambda: desktop_case.path.unlink()
    with pytest.raises(GuiRuntimeBlockedError, match="GUI_RISK_METADATA"):
        _desktop_cycle(c)
    assert desktop_case.provider.order_calls == 0
    assert c.central_order_coordinator.manager.state().intents == ()


def test_metadata_change_after_admission_blocks_dispatch(desktop_case, monkeypatch):
    c = desktop_case.compose()
    record = c.journal.record
    def changed(event):
        if event.event_type == "CENTRAL_COORDINATION_RESULT":
            desktop_case.path.unlink()
        return record(event)
    monkeypatch.setattr(c.journal, "record", changed)
    with pytest.raises(GuiRuntimeBlockedError, match="GUI_RISK_METADATA"):
        _desktop_cycle(c)
    assert desktop_case.provider.order_calls == 0
    assert len(c.central_order_coordinator.manager.state().queued) == 1
    assert _observed(c).execution_status == "METADATA_BINDING_BLOCKED"


def test_semantic_loader_substitution_rejected_before_transport(desktop_case, monkeypatch):
    from trading_robot import gui_risk_metadata as gm
    original = gm.load_portfolio_risk_metadata
    def altered(path):
        mapping = original(path)
        mapping[UID] = replace(mapping[UID], lot_size=20)
        return mapping
    monkeypatch.setattr(gm, "load_portfolio_risk_metadata", altered)
    with pytest.raises(GuiRuntimeBlockedError, match="GUI_RISK_METADATA_INVALID"):
        desktop_case.compose()
    assert desktop_case.transports == []


def test_post_composition_validation_failure_closes_transport_and_ledger(desktop_case, monkeypatch):
    from trading_robot import gui_risk_metadata as gm
    original = gm.GuiRiskMetadataBinding.verify
    def changed(self, *args):
        raise gm.GuiRiskMetadataError("GUI_RISK_METADATA_CHANGED")
    monkeypatch.setattr(gm.GuiRiskMetadataBinding, "verify", changed)
    with pytest.raises(GuiRuntimeBlockedError, match="GUI_RISK_METADATA_CHANGED"):
        desktop_case.compose()
    assert desktop_case.provider.closed is True
    assert desktop_gui._PRODUCTION_COMPOSITION is None
    monkeypatch.setattr(gm.GuiRiskMetadataBinding, "verify", original)
    desktop_case.provider.closed = False
    c = desktop_case.compose()
    assert c.service_ready is True


@pytest.mark.parametrize("behavior,expected", [
    ("closed", "MARKET_IDLE"), ("submitted", "SUBMITTED"),
    ("ambiguous", "SUBMISSION_UNCERTAIN"), ("rejected", "SUBMISSION_REJECTED"),
])
def test_real_adapter_result_reaches_read_api_journal_and_display(desktop_case, monkeypatch, behavior, expected):
    c = desktop_case.compose()
    p = desktop_case.provider
    p.market_open = behavior != "closed"
    p.post_behavior = behavior
    if behavior == "rejected":
        def rejected(*args, **kwargs):
            p.order_calls += 1
            raise TBankAPIError("PRIVATE_PROVIDER_CANARY", status_code=400)
        monkeypatch.setattr(p, "post_order", rejected)
    central_result = _desktop_cycle(c)
    assert central_result.status == "QUEUED"
    o = _observed(c)
    assert o.execution_status == expected
    assert o.order_was_sent is (behavior != "closed")
    assert o.order_may_have_been_sent is (behavior == "ambiguous")
    assert o.intent_binding == "MATCHED"
    assert o.audit_persisted is True
    assert _display(c).status == "QUEUED / " + expected
    events = c.journal.recent(event_type="GUI_EXECUTION_OUTCOME")
    serialized = json.dumps(events)
    assert "PRIVATE_PROVIDER_CANARY" not in serialized
    assert ACCOUNT not in serialized
    assert central_result.intent_id not in serialized
    assert "synthetic-exchange-order" not in serialized
    assert "error" not in events[0]["payload"]


@pytest.mark.parametrize("failure", ["exception", "wrong_type", "wrong_intent", "unknown_status", "audit_failure"])
def test_unobserved_dispatch_fail_closed_without_automatic_retry(desktop_case, monkeypatch, failure):
    c = desktop_case.compose()
    calls = []
    def dispatch(repository, *, expected_intent_id):
        calls.append(expected_intent_id)
        if failure == "exception":
            raise RuntimeError("PRIVATE_POSSIBLE_POST_CANARY")
        if failure == "wrong_type":
            return SimpleNamespace(status="SUBMITTED", error="PRIVATE_CANARY")
        return SandboxDispatchResult(
            status="PRIVATE_STATUS_CANARY" if failure == "unknown_status" else "SUBMITTED",
            intent_id="OTHER_PRIVATE_INTENT" if failure == "wrong_intent" else expected_intent_id,
            order_was_sent=True,
        )
    monkeypatch.setattr(c.execution_adapter, "dispatch_next", dispatch)
    if failure == "audit_failure":
        record = c.journal.record
        def broken(event):
            if event.event_type == "GUI_EXECUTION_OUTCOME":
                raise RuntimeError("PRIVATE_AUDIT_CANARY")
            return record(event)
        monkeypatch.setattr(c.journal, "record", broken)
    _desktop_cycle(c)
    o = _observed(c)
    assert o.recovery_required is True
    if failure == "audit_failure":
        assert o.execution_status == "SUBMITTED"
        assert o.audit_persisted is False
    else:
        assert o.execution_status in {"DISPATCH_EXCEPTION", "DISPATCH_RESULT_INVALID"}
        assert o.order_was_sent is None
        assert o.order_may_have_been_sent is True
    assert "PRIVATE" not in json.dumps(o.to_dict())
    # Verify before any new source read, not merely before a second POST.
    reads = desktop_case.provider.candle_calls
    with pytest.raises(GuiRuntimeBlockedError, match="EXECUTION_OBSERVATION_BLOCKED"):
        c.run_cycle()
    assert len(calls) == 1
    assert desktop_case.provider.candle_calls == reads


def test_outcome_values_immutable_and_external_map_copy(desktop_case):
    c = desktop_case.compose()
    _desktop_cycle(c)
    mapping = c.latest_cycle_outcomes()
    mapping.clear()
    assert UID in c.latest_cycle_outcomes()
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        _observed(c).execution_status = "SUBMITTED"


@pytest.mark.parametrize("status", ["CL7_DISPATCH_PENDING", "CL7_CONTEXT_UNAVAILABLE", "CL7_CENTRAL_CHANGED", "DISARMED"])
def test_finite_no_post_results_are_not_labeled_sent(desktop_case, monkeypatch, status):
    c = desktop_case.compose()
    monkeypatch.setattr(c.execution_adapter, "dispatch_next", lambda *a, **k: SandboxDispatchResult(status=status))
    _desktop_cycle(c)
    o = _observed(c)
    assert o.execution_status == status
    assert o.order_was_sent is False
    assert o.intent_binding == "NOT_REPORTED"
    assert desktop_case.provider.order_calls == 0


@pytest.mark.parametrize("field,value", [
    ("coordination_status", "PRIVATE_CANARY"), ("execution_status", "PRIVATE_CANARY"),
    ("intent_binding", []), ("audit_persisted", False), ("dispatch_invoked", 1),
    ("automatic_retry", True),
])
def test_bad_gui_execution_event_is_not_trusted(desktop_case, field, value):
    c = desktop_case.compose()
    _desktop_cycle(c)
    events = c.journal.recent(event_type="GUI_EXECUTION_OUTCOME")
    events[0]["payload"][field] = value
    result = latest_sandbox_decisions(events, session_id=c.session_id,
        account_scope_sha256=c.account_scope_sha256, instrument_ids=[UID])
    assert result[UID].status == "AUDIT_INVALID"


def test_factory_synthetic_buy_hold_sell_flat_reentry(desktop_case, monkeypatch):
    """Factory owners, explicit synthetic snapshots; NOT production refresh/CL7."""
    from test_q7a_source_empty_lifecycle import _snapshot, _choose_market
    import trading_robot.central_order_manager as central_module
    c = desktop_case.compose()
    p = desktop_case.provider
    p.market_open, p.post_behavior = True, "submitted"
    original = p.post_order
    def correlated(*args, **kwargs):
        return dict(original(*args, **kwargs), orderId="synthetic-" + kwargs["order_id"])
    monkeypatch.setattr(p, "post_order", correlated)
    now, latest, hooks = c.cycle_source()
    runtime = next(r for r in c.runtime_store.load(expected_account_id=ACCOUNT) if r.config.instrument_id == UID)
    profile = next(r for r in c.profile_store.load_mode("SANDBOX_EXECUTION") if r.instrument_id == UID)
    case = SimpleNamespace(controller=c, provider=p, central=c.central_order_coordinator.manager,
        repository=c.portfolio_repository, risk=c.central_order_coordinator.risk_runtime,
        portfolio_risk=c.portfolio_risk_runtime, runtime=runtime, profile=profile,
        hooks=hooks, now=now, candle=latest[runtime.runtime_key])
    monkeypatch.setattr(central_module, "_now", lambda: case.now.isoformat())
    buy = _cycle(case)
    assert buy.status == "QUEUED"
    assert _observed(c).execution_status == "SUBMITTED"
    _snapshot(case, 1, 1_000_000.0 - 1050.0)
    for_result = case.central.mark_reconciled(
        buy.intent_id, portfolio_repository=case.repository, outcome="FILLED", executed_lots=1,
        risk_runtime=case.risk, execution_price_rub=105.0, execution_price_source="SYNTHETIC_EXECUTION_PRICE")
    assert for_result.risk_execution_status == "RECORDED"
    assert _cycle(case).status == "NO_POSITION_CHANGE"
    assert _observed(c).execution_status == "NOT_DISPATCHED"
    assert p.order_calls == 1
    _choose_market(case, 0)
    sell = _cycle(case)
    assert sell.status == "QUEUED"
    assert _observed(c).action == "SELL"
    assert _observed(c).execution_status == "SUBMITTED"
    _snapshot(case, 0, 1_000_000.0)
    terminal = case.central.mark_reconciled(
        sell.intent_id, portfolio_repository=case.repository, outcome="FILLED", executed_lots=1,
        risk_runtime=case.risk, execution_price_rub=105.0, execution_price_source="SYNTHETIC_EXECUTION_PRICE")
    assert terminal.risk_execution_status == "RECORDED"
    assert _cycle(case).status == "NO_POSITION_CHANGE"
    assert _observed(c).execution_status == "NOT_DISPATCHED"
    _choose_market(case, 1)
    new_buy = _cycle(case)
    assert new_buy.status == "QUEUED"
    assert _observed(c).execution_status == "SUBMITTED"
    assert p.sent_ids == [buy.intent_id, sell.intent_id, new_buy.intent_id]
    assert len(set(p.sent_ids)) == 3
    assert case.repository.load(expected_account_id=ACCOUNT).state_status == "EMPTY"
    assert case.portfolio_risk.state_store.load_account(ACCOUNT).daily_turnover_rub == 2100.0
    assert _cycle(case).status == "ACCOUNT_BLOCKED"
    assert p.order_calls == 3


@pytest.mark.parametrize("status,was_sent,may_sent", [
    ("SUBMITTED", False, False), ("SUBMISSION_REJECTED", False, False),
    ("SUBMISSION_UNCERTAIN", True, False), ("MARKET_IDLE", True, False),
    ("SUBMITTED", 1, False), ("SUBMITTED", True, 0),
])
def test_contradictory_adapter_flags_are_not_used_as_delivery_evidence(desktop_case, monkeypatch, status, was_sent, may_sent):
    c = desktop_case.compose()
    monkeypatch.setattr(c.execution_adapter, "dispatch_next", lambda *a, **k: SandboxDispatchResult(
        status=status, intent_id=k["expected_intent_id"], order_was_sent=was_sent,
        order_may_have_been_sent=may_sent))
    _desktop_cycle(c)
    assert _observed(c).execution_status == "DISPATCH_RESULT_INVALID"
    assert _observed(c).order_was_sent is None
    assert _observed(c).order_may_have_been_sent is True
    assert desktop_case.provider.order_calls == 0


@pytest.mark.parametrize("audit_failure", [False, True])
def test_desktop_render_consumes_dispatch_event_and_surfaces_audit_failure(desktop_case, monkeypatch, audit_failure):
    from trading_robot.dashboard_view import InstrumentRuntimeView, MultiInstrumentDashboard
    c = desktop_case.compose()
    p = desktop_case.provider
    p.market_open, p.post_behavior = True, "submitted"
    if audit_failure:
        record = c.journal.record
        def failed(event):
            if event.event_type == "GUI_EXECUTION_OUTCOME":
                raise RuntimeError("PRIVATE_AUDIT_CANARY")
            return record(event)
        monkeypatch.setattr(c.journal, "record", failed)
    _desktop_cycle(c)
    # Real desktop render method with inert widgets and an explicit read projection.
    row = InstrumentRuntimeView(UID, "SBER", "CANDLE_INTERVAL_HOUR", "sma", "ACTIVE",
        "MATCHED", 0, 0, "", "synthetic-key", "synthetic-hash", "synthetic projection")
    monkeypatch.setattr(c, "dashboard", lambda: MultiInstrumentDashboard(
        "SANDBOX_EXECUTION", "READY", ACCOUNT, "synthetic view", (row,)))
    class Variable:
        def __init__(self, value=""):
            self.value = value
        def get(self):
            return self.value
        def set(self, value):
            self.value = value
    class Tree:
        def __init__(self):
            self.values = []
        def get_children(self):
            return ()
        def delete(self, *args):
            pass
        def insert(self, *args, **kwargs):
            self.values.append(kwargs["values"])
    gui = SimpleNamespace(multi_instrument_tree=Tree(), multi_instrument_status=Variable(),
        sb_profile_mode=Variable("SANDBOX_EXECUTION"), gui_runtime_controller=c, event_journal=c.journal)
    desktop_gui.TradingRobotGUI._refresh_multi_instrument_dashboard(gui)
    expected = "AUDIT_UNAVAILABLE" if audit_failure else "BUY / QUEUED / SUBMITTED"
    assert gui.multi_instrument_tree.values[0][5] == expected
    assert p.order_calls == 1


def test_missing_authority_with_existing_ledger_is_reported_not_bypassed(desktop_case):
    c = desktop_case.compose()
    store = c.cash_authority.store
    store.path.unlink()
    # Deliberately corrupt a synthetic fixture, never create substitute authority.
    _desktop_cycle(c)
    o = _observed(c)
    assert o.coordination_status == "QUEUED"
    assert o.execution_status == "CL7_RECOVERY_BLOCKED"
    assert o.recovery_required is True
    assert o.order_was_sent is False
    assert desktop_case.provider.order_calls == 0


def test_finite_execution_catalog_covers_existing_owner_literal_statuses():
    import ast
    from trading_robot.gui_execution_outcome import EXECUTION_STATUSES
    root = Path(__file__).resolve().parents[1]
    for file, constructor in (
        ("risk_runtime.py", "RiskDispatchAuthorizationError"),
        ("portfolio_risk_runtime.py", "PortfolioRiskAuthorizationError"),
        ("sandbox_execution_adapter.py", "SandboxDispatchResult"),
    ):
        tree = ast.parse((root / "trading_robot" / file).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == constructor:
                status = next((kw.value for kw in node.keywords if kw.arg == "status"),
                              node.args[0] if node.args else None)
                if isinstance(status, ast.Constant) and type(status.value) is str:
                    assert status.value in EXECUTION_STATUSES, (file, status.value)
