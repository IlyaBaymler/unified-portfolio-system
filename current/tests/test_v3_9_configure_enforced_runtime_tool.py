from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_central_order_coordinator_v3_8 import ACCOUNT
from test_v3_9_configure_shadow_runtimes_tool import (
    args as shadow_args,
)
from test_v3_9_configure_shadow_runtimes_tool import (
    prepare_ready_runtime,
    secure_probe,
)

from tools import v3_8_sandbox_acceptance as acceptance
from tools import v3_9_configure_enforced_runtime as configure
from tools import v3_9_configure_shadow_runtimes as shadow_configure
from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.journal import EventJournal
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_runtime import RiskRuntimeAdapter
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.state_persistence import atomic_write_json


def ready_runtime(tmp_path: Path) -> Path:
    root = prepare_ready_runtime(tmp_path)
    shadow_configure.run(
        shadow_args(
            root,
            "apply",
            confirm=shadow_configure.APPLY_CONFIRMATION,
        ),
        secret_probe_factory=secure_probe,
    )
    return root


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


def test_preview_is_read_only_and_discloses_exact_enforced_limits(
    tmp_path: Path,
) -> None:
    root = ready_runtime(tmp_path)
    profile_path = root / "risk_profiles.json"
    before = profile_path.read_bytes()

    result = configure.run(
        args(root, "preview"),
        secret_probe_factory=secure_probe,
    )

    assert result["status"] == "PREVIEW"
    assert result["portfolio_policy_mode"] == "ENFORCED"
    assert result["portfolio_limits"]["max_price_age_seconds"] == 300
    assert result["runtime_status"] == "STOPPED"
    assert result["strategy_proposal_created"] is False
    assert result["central_intent_created"] is False
    assert result["provider_post_authorized"] is False
    assert result["writes_performed"] is False
    assert ACCOUNT not in json.dumps(result)
    assert profile_path.read_bytes() == before
    assert not (root / configure.MANIFEST_NAME).exists()


def test_apply_requires_exact_confirmation_without_partial_activation(
    tmp_path: Path,
) -> None:
    root = ready_runtime(tmp_path)
    profile_path = root / "risk_profiles.json"
    before = profile_path.read_bytes()

    with pytest.raises(RuntimeError, match="exact v3.9 M4 confirmation"):
        configure.run(
            args(root, "apply", confirm="WRONG"),
            secret_probe_factory=secure_probe,
        )

    assert profile_path.read_bytes() == before
    assert not (root / configure.MANIFEST_NAME).exists()


def test_apply_activates_checksummed_manifest_and_is_idempotent(
    tmp_path: Path,
) -> None:
    root = ready_runtime(tmp_path)

    result = configure.run(
        args(root, "apply", confirm=configure.APPLY_CONFIRMATION),
        secret_probe_factory=secure_probe,
    )
    repeated = configure.run(
        args(root, "apply", confirm=configure.APPLY_CONFIRMATION),
        secret_probe_factory=secure_probe,
    )

    profile = RiskProfileStore(root / "risk_profiles.json").require_portfolio_policy(
        "SANDBOX_EXECUTION"
    )
    manifest = json.loads(
        (root / configure.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    assert result["status"] == "CONFIGURED"
    assert result["writes_performed"] is True
    assert repeated["status"] == "ALREADY_CONFIGURED"
    assert repeated["writes_performed"] is False
    assert profile["policy"].portfolio_policy_mode == "ENFORCED"
    assert profile["source"] == "V3_9_M4_OPERATOR"
    assert manifest["activation_status"] == "ACTIVE"
    assert manifest["policy_hash"] == profile["policy_hash"]
    assert manifest["provider_post_authorized"] is False
    assert result["pre_activation_backup"]
    backup_path = Path(result["pre_activation_backup"])
    assert backup_path.is_file()
    assert RuntimeBackupManager(
        root, app_version="v3.9-m4-pre-activation"
    ).verify_backup(backup_path).valid
    assert repeated["pre_activation_backup"] == str(backup_path)
    assert (root / f"{configure.MANIFEST_NAME}.sha256").is_file()
    assert CentralOrderStore(root / "central_order_state.json").load(
        expected_account_id=ACCOUNT
    ).intents == ()
    assert EventJournal(root / "trading_events.db", read_only=True).count() == 0


def test_enforced_profile_without_active_manifest_fails_closed(
    tmp_path: Path,
) -> None:
    root = ready_runtime(tmp_path)
    configure.run(
        args(root, "apply", confirm=configure.APPLY_CONFIRMATION),
        secret_probe_factory=secure_probe,
    )
    manifest_path = root / configure.MANIFEST_NAME
    manifest_path.unlink()
    manifest_path.with_name(manifest_path.name + ".sha256").unlink()
    risk_runtime = RiskRuntimeAdapter(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=RiskProfileStore(root / "risk_profiles.json"),
        state_store=RiskStateStore(root / "risk_state.json"),
    )

    with pytest.raises(RuntimeError, match="activation manifest"):
        acceptance._portfolio_risk_runtime(root, risk_runtime)


def test_acceptance_autowires_only_matching_active_manifest(tmp_path: Path) -> None:
    root = ready_runtime(tmp_path)
    configure.run(
        args(root, "apply", confirm=configure.APPLY_CONFIRMATION),
        secret_probe_factory=secure_probe,
    )
    risk_runtime = RiskRuntimeAdapter(
        account_id=ACCOUNT,
        mode="SANDBOX_EXECUTION",
        profile_store=RiskProfileStore(root / "risk_profiles.json"),
        state_store=RiskStateStore(root / "risk_state.json"),
    )

    runtime = acceptance._portfolio_risk_runtime(root, risk_runtime)
    assert runtime is not None
    assert runtime.account_id == ACCOUNT

    manifest_path = root / configure.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["policy_hash"] = "0" * 64
    atomic_write_json(manifest_path, manifest, write_checksum=True)
    with pytest.raises(RuntimeError, match="policy hash mismatch"):
        acceptance._portfolio_risk_runtime(root, risk_runtime)


def test_runtime_backup_contains_activation_manifest(tmp_path: Path) -> None:
    root = ready_runtime(tmp_path)
    configure.run(
        args(root, "apply", confirm=configure.APPLY_CONFIRMATION),
        secret_probe_factory=secure_probe,
    )

    backup = RuntimeBackupManager(root, app_version="v3.9-m4").create_backup(
        tmp_path / "m4-runtime.zip"
    )
    verification = RuntimeBackupManager(root, app_version="v3.9-m4").verify_backup(
        backup
    )

    assert verification.valid
    assert verification.manifest is not None
    names = {item["name"] for item in verification.manifest["entries"]}
    assert configure.MANIFEST_NAME in names


def test_configure_refuses_started_shadow_runtime(tmp_path: Path) -> None:
    root = ready_runtime(tmp_path)
    atomic_write_json(
        root / configure.SHADOW_START_MANIFEST_NAME,
        {"version": 1, "runtime_dir": str(root)},
        write_checksum=True,
    )

    with pytest.raises(RuntimeError, match="has not passed START"):
        configure.run(
            args(root, "preview"),
            secret_probe_factory=secure_probe,
        )
