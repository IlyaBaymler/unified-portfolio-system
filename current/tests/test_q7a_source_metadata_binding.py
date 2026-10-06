"""Offline regression for PR #238's production metadata composition gap.

Fixtures use disposable stores and an inert provider. The real composition,
Runtime.adapter, Portfolio Risk and Central are used; no broker is contacted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_v3_10_q7a_e2e_smoke import (
    ACCOUNT,
    KEY,
    KEY_ID,
    _configured,
    _owner_admission_setup,
)
from test_v3_10_q7a_live_entrypoint import valid_preparation
from tools import v3_10_q7a_e2e_smoke as q7a
from tools import v3_10_q7a_live_entrypoint as live
from tools import v3_10_runtime_cash_cutover as cutover
from trading_robot.runtime_cash_authority import derive_account_scope


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class NoBroker:
    def __getattr__(self, name: str) -> Any:
        if name.startswith(("get_", "post_")):

            def forbidden(*args: Any, **kwargs: Any) -> None:
                raise AssertionError("BROKER_CALL_FORBIDDEN")

            return forbidden
        raise AttributeError(name)


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    (
        _record,
        _hooks,
        proposal,
        risk,
        _portfolio_risk,
        repository,
        central,
        target,
        request,
    ) = _owner_admission_setup(tmp_path)
    state = repository.load(expected_account_id=ACCOUNT)
    repository.save(replace(state, state_status="EMPTY"))
    scope = derive_account_scope(ACCOUNT, identity_key=KEY, identity_key_id=KEY_ID)
    configured = _configured(scope, active=True)
    metadata = tmp_path / "portfolio_risk_metadata.json"
    prep = valid_preparation()
    fields = dict(prep.fields)
    control = q7a.build_control_record(
        candidate_commit=fields["candidate_commit"],
        candidate_tree=fields["candidate_tree"],
        configured=configured,
        target_instrument_id=target.config.instrument_id,
        metadata_path=metadata,
        created_at="2026-09-11T10:00:05.000000Z",
    )
    args = argparse.Namespace(
        runtime_dir=tmp_path,
        runtime_manifest=tmp_path / "test-runtime-manifest.json",
        control_record=tmp_path / "test-control.json",
        metadata=metadata,
        evidence_dir=tmp_path / "test-evidence",
    )
    args.control_record.write_bytes(control.raw)
    fields.update(
        account_scope_sha256=scope,
        configured_set_sha256=configured.identity_sha256,
        identity_key_id=KEY_ID,
        target_instrument_sha256=_sha(target.config.instrument_id.encode()),
        static_metadata_sha256=_sha(metadata.read_bytes()),
        control_record_sha256=_sha(control.raw),
        evidence_root_sha256=live.evidence_root_identity(args.evidence_dir),
    )
    manifest = {
        key: fields[key]
        for key in (
            "candidate_commit",
            "candidate_tree",
            "account_scope_sha256",
            "configured_set_sha256",
            "identity_key_id",
            "backup_binding_sha256",
        )
    }
    manifest["environment"] = "SANDBOX"
    args.runtime_manifest.write_bytes(live._canonical(manifest))
    fields["runtime_manifest_sha256"] = _sha(args.runtime_manifest.read_bytes())
    # This is an isolated composition test, not a valid operational Preparation.
    prep = live.LivePreparation(raw=b"SYNTHETIC_COMPOSITION_ONLY", fields=fields)
    runtime = cutover._Runtime(
        root=tmp_path,
        authority=SimpleNamespace(),
        ledger=SimpleNamespace(),
        portfolio=repository,
        profiles=risk.profile_store,
        risk_state=risk.state_store,
        central=central,
        provider=NoBroker(),
        portfolio_manager=SimpleNamespace(api=None),
        raw_account=ACCOUNT,
        identity_key=KEY,
        identity_key_id=KEY_ID,
    )
    calls: list[str] = []

    def open_runtime(*args: Any, **kwargs: Any) -> cutover._Runtime:
        calls.append("open_runtime")
        assert kwargs == {
            "create_ledger": False,
            "require_provider": True,
            "allow_environment_secrets": False,
        }
        return runtime

    monkeypatch.setattr(cutover, "_open_runtime", open_runtime)
    monkeypatch.setattr(
        live, "verify_configured_active_runtime", lambda *a, **k: configured,
    )
    return SimpleNamespace(
        args=args,
        prep=prep,
        runtime=runtime,
        calls=calls,
        configured=configured,
        proposal=proposal,
        target=target,
        request=request,
    )


def _admit(
    owners: live.LiveOwners | SimpleNamespace, case: SimpleNamespace,
) -> live.CentralOrderCoordinationResult:
    return owners.coordinator.coordinate(
        case.proposal,
        case.target,
        case.request.profile,
        candles=case.request.candles,
        lot_size=case.request.lot_size,
        now=case.request.evaluated_at,
        portfolio_risk_candidate_quote=case.request.portfolio_risk_candidate_quote,
    )


def test_production_composition_admits_empty_buy_with_bound_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _setup(tmp_path, monkeypatch)
    owners = live._compose_live_owners(case.args, case.prep)
    result = _admit(owners, case)
    assert result.status == "QUEUED", result.status
    assert (
        owners.coordinator.portfolio_risk_runtime
        is owners.execution_adapter.portfolio_risk_runtime
    )
    metadata = owners.execution_adapter.portfolio_risk_runtime.instrument_metadata
    assert set(metadata) == {"uid-sber", "uid-lkoh"}
    assert metadata["uid-sber"].currency == "RUB"
    assert metadata["uid-sber"].lot_size == 10
    assert owners.provider.post_calls == 0
    assert len(owners.coordinator.manager.state().intents) == 1


def _write_metadata(path: Path, document: object) -> bytes:
    raw = live._canonical(document)
    path.write_bytes(raw)
    path.with_name(path.name + ".sha256").write_text(_sha(raw), encoding="ascii")
    return raw


def _document() -> dict[str, Any]:
    return {
        "version": 1,
        "instruments": [{
            "instrument_id": "uid-sber", "lot_size": 10,
            "asset_class": "SHARE", "currency": "RUB",
        }],
    }


def _repin_metadata(case: SimpleNamespace, document: object) -> None:
    raw = _write_metadata(case.args.metadata, document)
    control = json.loads(case.args.control_record.read_bytes())
    control["metadata_sha256"] = _sha(raw)
    control.pop("record_sha256")
    control["record_sha256"] = _sha(live._canonical(control))
    control_raw = live._canonical(control)
    case.args.control_record.write_bytes(control_raw)
    fields = dict(case.prep.fields)
    fields.update(
        static_metadata_sha256=_sha(raw),
        control_record_sha256=_sha(control_raw),
    )
    case.prep = live.LivePreparation(raw=case.prep.raw, fields=fields)


@pytest.mark.parametrize("mutation", [
    "missing_currency", "blank_currency", "numeric_currency",
    "boolean_lot", "string_lot", "zero_lot", "negative_lot",
    "blank_id", "numeric_id", "numeric_asset_class", "unknown_row_field",
    "boolean_version", "unknown_version", "missing_version", "unknown_root_field",
    "duplicate_instrument", "empty_instruments", "not_a_list", "not_an_object",
])
def test_bound_metadata_rejects_invalid_schema(tmp_path: Path, mutation: str) -> None:
    document = _document()
    row = document["instruments"][0]
    if mutation == "missing_currency":
        row.pop("currency")
    elif mutation == "blank_currency":
        row["currency"] = " "
    elif mutation == "numeric_currency":
        row["currency"] = 643
    elif mutation == "boolean_lot":
        row["lot_size"] = True
    elif mutation == "string_lot":
        row["lot_size"] = "10"
    elif mutation == "zero_lot":
        row["lot_size"] = 0
    elif mutation == "negative_lot":
        row["lot_size"] = -1
    elif mutation == "blank_id":
        row["instrument_id"] = " "
    elif mutation == "numeric_id":
        row["instrument_id"] = 123
    elif mutation == "numeric_asset_class":
        row["asset_class"] = 123
    elif mutation == "unknown_row_field":
        row["authority"] = "SYNTHETIC_REJECT"
    elif mutation == "boolean_version":
        document["version"] = True
    elif mutation == "unknown_version":
        document["version"] = 2
    elif mutation == "missing_version":
        document.pop("version")
    elif mutation == "unknown_root_field":
        document["extra"] = 1
    elif mutation == "duplicate_instrument":
        document["instruments"].append(dict(row))
    elif mutation == "empty_instruments":
        document["instruments"] = []
    elif mutation == "not_a_list":
        document["instruments"] = {}
    elif mutation == "not_an_object":
        document["instruments"] = ["invalid"]
    path = tmp_path / "metadata.json"
    raw = _write_metadata(path, document)
    with pytest.raises(live.Q7ALiveError) as caught:
        live._load_bound_risk_metadata(path, expected_raw=raw)
    assert caught.value.reason == "QUOTE_OR_METADATA_INVALID"


@pytest.mark.parametrize("mutation", [
    "missing_checksum", "wrong_checksum", "invalid_checksum_encoding",
    "wrong_expected_bytes", "metadata_symlink", "checksum_symlink", "malformed_json",
    "duplicate_json_key",
])
def test_bound_metadata_rejects_integrity_errors(tmp_path: Path, mutation: str) -> None:
    path = tmp_path / "metadata.json"
    raw = _write_metadata(path, _document())
    expected = raw
    checksum = path.with_name(path.name + ".sha256")
    if mutation == "missing_checksum":
        checksum.unlink()
    elif mutation == "wrong_checksum":
        checksum.write_text("0" * 64, encoding="ascii")
    elif mutation == "invalid_checksum_encoding":
        checksum.write_bytes(bytes([255]))
    elif mutation == "wrong_expected_bytes":
        expected = raw + b" "
    elif mutation in {"metadata_symlink", "checksum_symlink"}:
        selected = path if mutation == "metadata_symlink" else checksum
        target = selected.with_name(selected.name + ".target")
        selected.rename(target)
        try:
            selected.symlink_to(target)
        except OSError:
            pytest.skip("Host cannot create a synthetic symlink")
    elif mutation == "malformed_json":
        expected = raw = b"{"
        path.write_bytes(raw)
        checksum.write_text(_sha(raw), encoding="ascii")
    elif mutation == "duplicate_json_key":
        expected = raw = raw.replace(b'"version":1', b'"version":1,"version":1')
        path.write_bytes(raw)
        checksum.write_text(_sha(raw), encoding="ascii")
    with pytest.raises(live.Q7ALiveError) as caught:
        live._load_bound_risk_metadata(path, expected_raw=expected)
    assert caught.value.reason == "QUOTE_OR_METADATA_INVALID"


@pytest.mark.parametrize("drift", ["metadata", "checksum", "aba_mapping"])
def test_bound_metadata_rejects_loader_time_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    path = tmp_path / "metadata.json"
    raw = _write_metadata(path, _document())
    original_loader = live.load_portfolio_risk_metadata
    calls = []

    def substituted_loader(
        selected: Path,
    ) -> dict[str, live.PortfolioRiskInstrumentMetadata]:
        calls.append(selected)
        mapping = original_loader(selected)
        if drift == "metadata":
            path.write_bytes(raw + b" ")
        elif drift == "checksum":
            path.with_name(path.name + ".sha256").write_text("0" * 64, encoding="ascii")
        else:
            # Simulate an A/B/A read: disk bytes are A before and after, but
            # the owner's returned value came from different metadata B.
            mapping["uid-sber"] = replace(mapping["uid-sber"], currency="USD")
        return mapping

    monkeypatch.setattr(live, "load_portfolio_risk_metadata", substituted_loader)
    with pytest.raises(live.Q7ALiveError) as caught:
        live._load_bound_risk_metadata(path, expected_raw=raw)
    assert caught.value.reason == "QUOTE_OR_METADATA_INVALID"
    assert calls == [path]


@pytest.mark.parametrize("mutation", [
    "missing_target", "missing_other", "extra_instrument", "target_usd",
    "target_lot_mismatch", "missing_currency",
])
def test_production_composition_rejects_unbound_scope_without_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    case = _setup(tmp_path, monkeypatch)
    document = json.loads(case.args.metadata.read_bytes())
    rows = document["instruments"]
    target = next(row for row in rows if row["instrument_id"] == "uid-sber")
    if mutation == "missing_target":
        rows.remove(target)
    elif mutation == "missing_other":
        document["instruments"] = [target]
    elif mutation == "extra_instrument":
        rows.append({**target, "instrument_id": "uid-extra"})
    elif mutation == "target_usd":
        target["currency"] = "USD"
    elif mutation == "target_lot_mismatch":
        target["lot_size"] = 100
    elif mutation == "missing_currency":
        target.pop("currency")
    _repin_metadata(case, document)
    with pytest.raises(live.Q7ALiveError) as caught:
        live._compose_live_owners(case.args, case.prep)
    assert caught.value.reason == "QUOTE_OR_METADATA_INVALID"
    assert len(case.runtime.central.state().intents) == 0
    assert isinstance(case.runtime.provider, NoBroker)


def test_bad_checksum_rejected_before_adapter_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _setup(tmp_path, monkeypatch)
    case.args.metadata.with_name(case.args.metadata.name + ".sha256").unlink()
    adapter_calls: list[bool] = []

    def forbidden_adapter(*args: Any, **kwargs: Any) -> None:
        adapter_calls.append(True)
        raise AssertionError("ADAPTER_MUST_NOT_BE_CONSTRUCTED")

    monkeypatch.setattr(cutover._Runtime, "adapter", forbidden_adapter)
    with pytest.raises(live.Q7ALiveError) as caught:
        live._compose_live_owners(case.args, case.prep)
    assert caught.value.reason == "QUOTE_OR_METADATA_INVALID"
    assert case.calls == ["open_runtime"]
    assert adapter_calls == []
    assert isinstance(case.runtime.provider, NoBroker)
    assert len(case.runtime.central.state().intents) == 0


def test_unbound_adapter_retains_currency_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _setup(tmp_path, monkeypatch)
    adapter = case.runtime.adapter()
    coordinator = live.CentralOrderCoordinator(
        case.runtime.central, case.runtime.portfolio, adapter.risk_runtime,
        portfolio_risk_runtime=adapter.portfolio_risk_runtime,
    )
    result = _admit(SimpleNamespace(coordinator=coordinator), case)
    assert result.status == "PORTFOLIO_RISK_CURRENCY_UNKNOWN"
    assert len(case.runtime.central.state().intents) == 0


def test_bound_adapter_copies_mapping_into_one_authoritative_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _setup(tmp_path, monkeypatch)
    mapping = live._load_bound_risk_metadata(
        case.args.metadata, expected_raw=case.args.metadata.read_bytes(),
    )
    adapter = case.runtime.adapter(instrument_metadata=mapping)
    mapping.clear()
    assert set(adapter.portfolio_risk_runtime.instrument_metadata) == {
        "uid-sber", "uid-lkoh",
    }
