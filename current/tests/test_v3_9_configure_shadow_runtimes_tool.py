from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_central_order_coordinator_v3_8 import ACCOUNT
from test_v3_9_prepare_shadow_runtime_tool import args as prepare_args
from test_v3_9_prepare_shadow_runtime_tool import make_source

from tools import v3_9_configure_shadow_runtimes as configure
from tools import v3_9_prepare_shadow_runtime as prepare
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.risk import RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.secret_provider import SecretProviderProbe


def secure_probe(_root: str | Path) -> SecretProviderProbe:
    return SecretProviderProbe(
        provider="TEST SECURE STORE",
        secure=True,
        available=True,
        credential_present=True,
        key="TBANK_SANDBOX_TOKEN",
    )


def prepare_ready_runtime(tmp_path: Path) -> Path:
    source = tmp_path / "accepted-v3-8"
    target = tmp_path / "isolated-v3-9"
    make_source(source, explicit_currency_metadata=True)
    prepare.run(
        prepare_args(
            source,
            target,
            "apply",
            confirm=prepare.APPLY_CONFIRMATION,
        )
    )
    store = RiskProfileStore(target / "risk_profiles.json")
    loaded = store.require_profile("SANDBOX_EXECUTION")
    store.confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        replace(
            loaded["policy"],
            portfolio_policy_configured=True,
            portfolio_policy_mode="OBSERVE_ONLY",
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
        source="V3_9_M3_OPERATOR",
    )
    return target


def args(root: Path, action: str, *, confirm: str = ""):
    return configure.parse_args(
        [
            action,
            "--runtime-dir",
            str(root),
            "--confirm",
            confirm,
        ]
    )


def test_preview_validates_stopped_runtimes_without_writes(tmp_path: Path) -> None:
    root = prepare_ready_runtime(tmp_path)

    result = configure.run(
        args(root, "preview"),
        secret_probe_factory=secure_probe,
    )

    assert result["status"] == "PREVIEW"
    assert result["account_fingerprint"]
    assert ACCOUNT not in json.dumps(result)
    assert result["portfolio_policy_status"] == "READY"
    assert result["portfolio_policy_mode"] == "OBSERVE_ONLY"
    assert result["risk_baseline_status"] == "UNINITIALIZED"
    assert result["risk_baseline_initialization"] == "FIRST_FRESH_RISK_EVALUATION"
    assert result["runtime_status"] == "STOPPED"
    assert {item["ticker"] for item in result["instruments"]} == {"SBER", "LKOH"}
    assert all(item["runtime_status"] == "STOPPED" for item in result["instruments"])
    assert result["strategy_proposal_created"] is False
    assert result["central_intent_created"] is False
    assert result["provider_post_authorized"] is False
    assert result["writes_performed"] is False
    assert not (root / configure.MANIFEST_NAME).exists()


def test_apply_requires_exact_confirmation_without_writes(tmp_path: Path) -> None:
    root = prepare_ready_runtime(tmp_path)

    with pytest.raises(RuntimeError, match="exact v3.9 runtime confirmation"):
        configure.run(
            args(root, "apply", confirm="WRONG"),
            secret_probe_factory=secure_probe,
        )

    assert not (root / configure.MANIFEST_NAME).exists()


def test_apply_seals_configuration_and_is_idempotent(tmp_path: Path) -> None:
    root = prepare_ready_runtime(tmp_path)
    runtime_path = root / "instrument_runtimes.json"
    before_runtime = runtime_path.read_bytes()

    result = configure.run(
        args(root, "apply", confirm=configure.APPLY_CONFIRMATION),
        secret_probe_factory=secure_probe,
    )
    repeated = configure.run(
        args(root, "apply", confirm=configure.APPLY_CONFIRMATION),
        secret_probe_factory=secure_probe,
    )

    assert result["status"] == "CONFIGURED"
    assert result["writes_performed"] is True
    assert repeated["status"] == "ALREADY_CONFIGURED"
    assert repeated["writes_performed"] is False
    assert runtime_path.read_bytes() == before_runtime
    assert (root / f"{configure.MANIFEST_NAME}.sha256").is_file()
    manifest = json.loads(
        (root / configure.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert manifest["risk_baseline_status"] == "UNINITIALIZED"
    assert manifest["runtime_status"] == "STOPPED"
    assert manifest["strategy_proposal_created"] is False
    assert manifest["central_intent_created"] is False
    assert manifest["provider_post_authorized"] is False


def test_configure_refuses_unconfirmed_policy_and_insecure_secret(tmp_path: Path) -> None:
    root = prepare_ready_runtime(tmp_path)
    store = RiskProfileStore(root / "risk_profiles.json")
    loaded = store.require_profile("SANDBOX_EXECUTION")
    store.save_profile(
        "SANDBOX_EXECUTION",
        replace(loaded["policy"], portfolio_policy_configured=False),
        account_scope=ACCOUNT,
    )

    with pytest.raises(RuntimeError, match="policy is not READY"):
        configure.run(
            args(root, "preview"),
            secret_probe_factory=secure_probe,
        )

    store.confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        replace(
            loaded["policy"],
            portfolio_policy_configured=True,
            portfolio_policy_mode="OBSERVE_ONLY",
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
    )

    def insecure_probe(_root: str | Path) -> SecretProviderProbe:
        return SecretProviderProbe(
            provider=".env fallback",
            secure=False,
            available=True,
            credential_present=True,
            key="TBANK_SANDBOX_TOKEN",
        )

    with pytest.raises(RuntimeError, match="secure secret provider"):
        configure.run(
            args(root, "preview"),
            secret_probe_factory=insecure_probe,
        )


def test_configure_refuses_unknown_currency_metadata(tmp_path: Path) -> None:
    source = tmp_path / "accepted-v3-8"
    root = tmp_path / "isolated-v3-9"
    make_source(source)
    prepare.run(
        prepare_args(
            source,
            root,
            "apply",
            confirm=prepare.APPLY_CONFIRMATION,
        )
    )
    store = RiskProfileStore(root / "risk_profiles.json")
    loaded = store.require_profile("SANDBOX_EXECUTION")
    store.confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        replace(
            loaded["policy"],
            portfolio_policy_configured=True,
            portfolio_policy_mode="OBSERVE_ONLY",
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
        source="V3_9_M3_OPERATOR",
    )

    with pytest.raises(RuntimeError, match="currency metadata is unknown"):
        configure.run(
            args(root, "preview"),
            secret_probe_factory=secure_probe,
        )


def test_configure_refuses_active_runtime_and_partial_baseline(tmp_path: Path) -> None:
    root = prepare_ready_runtime(tmp_path)
    runtime_store = InstrumentRuntimeStore(root / "instrument_runtimes.json")
    runtimes = runtime_store.load(expected_account_id=ACCOUNT)
    runtime_store.save((runtimes[0].start(), *runtimes[1:]))

    with pytest.raises(RuntimeError, match="all InstrumentRuntimes STOPPED"):
        configure.run(
            args(root, "preview"),
            secret_probe_factory=secure_probe,
        )

    runtime_store.save(runtimes)
    RiskStateStore(root / "risk_state.json").save_account(
        ACCOUNT,
        RiskState(daily_date="2026-08-13"),
    )
    with pytest.raises(RuntimeError, match="partially initialized"):
        configure.run(
            args(root, "preview"),
            secret_probe_factory=secure_probe,
        )


def test_backup_restores_config_manifest_with_checksum(tmp_path: Path) -> None:
    root = prepare_ready_runtime(tmp_path)
    configure.run(
        args(root, "apply", confirm=configure.APPLY_CONFIRMATION),
        secret_probe_factory=secure_probe,
    )
    manager = RuntimeBackupManager(root, app_version="v3.9-m3")
    backup = manager.create_backup(tmp_path / "configured-runtime.zip")
    verification = manager.verify_backup(backup)
    restored = tmp_path / "restored"
    RuntimeBackupManager(restored, app_version="v3.9-m3").restore_backup(
        backup,
        confirmation="RESTORE RUNTIME",
    )

    assert verification.valid
    assert verification.manifest is not None
    names = {entry["name"] for entry in verification.manifest["entries"]}
    assert configure.MANIFEST_NAME in names
    assert (restored / configure.MANIFEST_NAME).is_file()
    assert (restored / f"{configure.MANIFEST_NAME}.sha256").is_file()
