from __future__ import annotations

import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.portfolio_model import (
    CompatibilityShadowStatus,
    PortfolioMigrationMetadata,
    PortfolioState,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.readiness import ProductionReadinessEvaluator, ReadinessStatus
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.secret_provider import (
    EnvFileSecretProvider,
    preferred_secret_provider,
    probe_secret_provider,
)
from trading_robot.state_persistence import atomic_write_json
from trading_robot.support_bundle import (
    SupportBundleBuilder,
    redact_object,
    scan_text_for_secrets,
)


def create_runtime(
    root: Path,
    *,
    account_id: str = "account-123",
    shadow_status: CompatibilityShadowStatus = CompatibilityShadowStatus.DISABLED,
) -> None:
    atomic_write_json(root / "strategy_profiles.json", {"version": 2, "profiles": {}})
    atomic_write_json(root / "risk_profiles.json", {"version": 1, "profiles": {}})
    atomic_write_json(
        root / "risk_state.json",
        {
            "version": 2,
            "accounts": {
                account_id: {
                    "kill_switch_active": False,
                    "risk_resync_required": False,
                    "last_snapshot_at": "2099-01-01T00:00:00+00:00",
                }
            },
        },
    )
    atomic_write_json(root / "robot_state.json", {"version": 5, "bots": {}})
    state = PortfolioState.empty(account_id=account_id)
    state = replace(
        state,
        migration=PortfolioMigrationMetadata.completed(shadow_status=shadow_status),
    )
    PortfolioRepository(root / "portfolio_state.json").save(state)
    atomic_write_json(
        root / "sandbox_diagnostic_state.json", {"version": 4, "accounts": {}}
    )
    EventJournal(root / "trading_events.db").record(
        JournalEvent(
            category="session",
            event_type="STARTED",
            account_id=account_id,
            payload={"authorization": "Bearer secret-value"},
        )
    )


def test_env_secret_provider_roundtrip(tmp_path: Path):
    provider = EnvFileSecretProvider(tmp_path / ".env")
    provider.set("TBANK_SANDBOX_TOKEN", "token-value")
    assert provider.get("TBANK_SANDBOX_TOKEN") == "token-value"
    provider.delete("TBANK_SANDBOX_TOKEN")
    assert provider.get("TBANK_SANDBOX_TOKEN") is None
    assert provider.secure is False


def test_preferred_secret_provider_falls_back_when_windows_backend_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class UnavailableWindowsProvider:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Credential Manager is unavailable for this test.")

    monkeypatch.setattr(
        "trading_robot.secret_provider.WindowsCredentialManagerProvider",
        UnavailableWindowsProvider,
    )
    provider = preferred_secret_provider(tmp_path)
    assert isinstance(provider, EnvFileSecretProvider)


def test_offline_qualification_never_probes_windows_credential_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class ForbiddenWindowsProvider:
        def __init__(self, *args, **kwargs):
            raise AssertionError("offline qualification probed Credential Manager")

    monkeypatch.setenv("MOEX_ROBOT_OFFLINE_QUALIFICATION", "1")
    monkeypatch.setattr(
        "trading_robot.secret_provider.WindowsCredentialManagerProvider",
        ForbiddenWindowsProvider,
    )

    provider = preferred_secret_provider(tmp_path)

    assert isinstance(provider, EnvFileSecretProvider)
    assert provider.path == tmp_path / ".env"


def test_recursive_redaction_and_secret_scan():
    account_id = "account-123456789"
    value = {
        "token": "very-secret",
        "nested": {"Authorization": "Bearer abcdefghijklmnopqrstuvwxyz"},
        "transaction_id": f"external-close-{account_id}-10",
        "safe": "ok",
    }
    redacted = redact_object(value, known_values=[account_id])
    assert redacted["token"] == "<REDACTED>"
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted["nested"]["Authorization"]
    assert account_id not in redacted["transaction_id"]
    assert "<REDACTED>" in redacted["transaction_id"]
    assert scan_text_for_secrets("Bearer abcdefghijklmnop").clean is False
    assert scan_text_for_secrets("hello", known_secrets=["canary"]).clean


def test_support_bundle_excludes_env_and_redacts_logs_and_events(tmp_path: Path):
    create_runtime(tmp_path)
    secret = "CANARY-SECRET-123456789"
    (tmp_path / ".env").write_text(
        f"TBANK_SANDBOX_TOKEN={secret}\n", encoding="utf-8"
    )
    (tmp_path / "robot_gui.log").write_text(
        "Authorization" + f": Bearer {secret}\n", encoding="utf-8"
    )
    result = SupportBundleBuilder(
        tmp_path,
        app_version="0.3.6",
    ).build(
        tmp_path / "support.zip",
        account_id="account-123",
        known_secrets=[secret],
    )
    assert result.secret_scan.clean
    with zipfile.ZipFile(result.path) as archive:
        names = archive.namelist()
        assert ".env" not in names
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        combined = "\n".join(
            archive.read(name).decode("utf-8", errors="replace") for name in names
        )
    assert secret not in combined
    assert "<REDACTED>" in combined
    assert (
        manifest["portfolio_observability"]["compatibility_shadow_status"]
        == "DISABLED"
    )


def test_support_bundle_auto_redacts_account_id_inside_transaction_id(
    tmp_path: Path,
):
    account_id = "00000000-0000-4000-8000-000000000042"
    create_runtime(tmp_path, account_id=account_id)
    EventJournal(tmp_path / "trading_events.db").record(
        JournalEvent(
            category="portfolio",
            event_type="EXTERNAL_CLOSE_ACKNOWLEDGED",
            account_id=account_id,
            payload={
                "transaction_id": f"external-close-{account_id}-revision-10"
            },
        )
    )
    (tmp_path / "robot_debug.log").write_text(
        f"reconcile-{account_id}-revision-10\n", encoding="utf-8"
    )

    result = SupportBundleBuilder(tmp_path, app_version="0.3.7").build(
        tmp_path / "support.zip",
        account_id=None,
    )

    assert result.secret_scan.clean
    with zipfile.ZipFile(result.path) as archive:
        combined = "\n".join(
            archive.read(name).decode("utf-8", errors="replace")
            for name in archive.namelist()
        )
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    assert account_id not in combined
    assert "<REDACTED>" in combined
    assert manifest["account_id"] == "<REDACTED_ACCOUNT:0042>"

    explicit_account_id = "operator-selected-account"
    explicit_result = SupportBundleBuilder(tmp_path, app_version="0.3.7").build(
        tmp_path / "support-explicit.zip",
        account_id=explicit_account_id,
    )
    with zipfile.ZipFile(explicit_result.path) as archive:
        explicit_combined = "\n".join(
            archive.read(name).decode("utf-8", errors="replace")
            for name in archive.namelist()
        )
    assert account_id not in explicit_combined
    assert explicit_account_id not in explicit_combined


def test_support_bundle_covers_v3_9_integrity_and_generated_risk_snapshot(
    tmp_path: Path,
):
    account_id = "m5-3-account-00000042"
    create_runtime(tmp_path, account_id=account_id)
    for name in (
        "v3_9_enforced_runtime_manifest.json",
        "v3_9_m5_2_runtime_seed_manifest.json",
        "portfolio_risk_metadata.json",
        "multi_instrument_profiles.json",
        "instrument_runtimes.json",
        "central_order_state.json",
    ):
        atomic_write_json(
            tmp_path / name,
            {"version": 1, "name": name},
            write_checksum=True,
        )

    result = SupportBundleBuilder(tmp_path, app_version="0.3.9b1").build(
        tmp_path / "support-v3-9.zip",
        account_id=account_id,
    )

    with zipfile.ZipFile(result.path) as archive:
        integrity = json.loads(
            archive.read("runtime_integrity.json").decode("utf-8")
        )
        dashboard = json.loads(
            archive.read("risk_dashboard_snapshot.json").decode("utf-8")
        )
        combined = b"\n".join(archive.read(name) for name in archive.namelist())
    assert integrity["v3_9_enforced_runtime_manifest.json"]["valid"] is True
    assert integrity["v3_9_m5_2_runtime_seed_manifest.json"]["valid"] is True
    assert integrity["portfolio_risk_metadata.json"]["valid"] is True
    assert all(report["path"] == name for name, report in integrity.items())
    assert dashboard["status"] == "UNAVAILABLE"
    assert account_id.encode("utf-8") not in combined


def test_support_bundle_does_not_serialize_unknown_dashboard_exception(
    tmp_path: Path,
    monkeypatch,
):
    account_id = "m5-3-account-00000042"
    unknown_account = "persisted-foreign-account-991122"
    create_runtime(tmp_path, account_id=account_id)

    def fail_dashboard(**_kwargs):
        raise ValueError(f"persisted account mismatch: {unknown_account}")

    monkeypatch.setattr(
        "trading_robot.support_bundle.load_risk_dashboard_snapshot",
        fail_dashboard,
    )

    result = SupportBundleBuilder(tmp_path, app_version="0.3.9b1").build(
        tmp_path / "support-error-redaction.zip",
        account_id=account_id,
    )

    with zipfile.ZipFile(result.path) as archive:
        dashboard = json.loads(
            archive.read("risk_dashboard_snapshot.json").decode("utf-8")
        )
        combined = b"\n".join(archive.read(name) for name in archive.namelist())
    assert dashboard == {
        "status": "UNAVAILABLE",
        "error_type": "ValueError",
        "detail": "Risk dashboard generation failed safely.",
    }
    assert unknown_account.encode("utf-8") not in combined


def test_support_bundle_reports_v3_9_checksum_mismatch(tmp_path: Path):
    create_runtime(tmp_path)
    metadata = tmp_path / "portfolio_risk_metadata.json"
    atomic_write_json(
        metadata,
        {"version": 1, "instruments": []},
        write_checksum=True,
    )
    metadata.write_text(
        json.dumps({"version": 1, "instruments": [], "tampered": True}),
        encoding="utf-8",
    )

    result = SupportBundleBuilder(tmp_path, app_version="0.3.9b1").build(
        tmp_path / "support-v3-9-tampered.zip"
    )

    with zipfile.ZipFile(result.path) as archive:
        integrity = json.loads(
            archive.read("runtime_integrity.json").decode("utf-8")
        )
    assert (
        integrity["portfolio_risk_metadata.json"]["status"]
        == "CHECKSUM_MISMATCH"
    )


def test_readiness_ready_with_valid_runtime_api_and_backup(tmp_path: Path):
    account_id = "account-123"
    create_runtime(tmp_path, account_id=account_id)
    backups = tmp_path / "backups"
    RuntimeBackupManager(tmp_path, app_version="0.3.6").create_backup(
        backups / "runtime.zip"
    )
    (tmp_path / "build_manifest.json").write_text(
        json.dumps({"software_version": "0.3.6"}), encoding="utf-8"
    )
    report = ProductionReadinessEvaluator(
        tmp_path,
        app_version="0.3.6",
        backups_dir=backups,
    ).evaluate(
        account_id=account_id,
        api_status={
            "authenticated": True,
            "available": True,
            "secret_provider": "Windows Credential Manager",
            "secret_provider_secure": True,
        },
    )
    assert report.status == ReadinessStatus.READY_FOR_SANDBOX
    assert all(check.status == "PASS" for check in report.checks)


def test_degraded_shadow_is_reported_without_changing_canonical_readiness(
    tmp_path: Path,
):
    account_id = "account-123"
    create_runtime(
        tmp_path,
        account_id=account_id,
        shadow_status=CompatibilityShadowStatus.DEGRADED,
    )
    backups = tmp_path / "backups"
    RuntimeBackupManager(tmp_path, app_version="0.3.6").create_backup(
        backups / "runtime.zip"
    )
    (tmp_path / "build_manifest.json").write_text(
        json.dumps({"software_version": "0.3.6"}), encoding="utf-8"
    )

    report = ProductionReadinessEvaluator(
        tmp_path,
        app_version="0.3.6",
        backups_dir=backups,
    ).evaluate(
        account_id=account_id,
        api_status={
            "authenticated": True,
            "available": True,
            "secret_provider": "Windows Credential Manager",
            "secret_provider_secure": True,
        },
    )

    assert report.status == ReadinessStatus.READY_FOR_SANDBOX
    assert report.compatibility_shadow_status == "DEGRADED"
    canonical = next(
        check for check in report.checks if check.code == "CANONICAL_PORTFOLIO_STATE"
    )
    assert canonical.status == "PASS"
    assert canonical.blocking is False


def test_readiness_blocks_on_pending_and_kill_switch(tmp_path: Path):
    account_id = "account-123"
    create_runtime(tmp_path, account_id=account_id)
    robot = json.loads((tmp_path / "robot_state.json").read_text(encoding="utf-8"))
    robot["bots"] = {
        "scope": {
            "pending_order": {
                "order_id": "order-1",
                "lifecycle_state": "ORDER_SUBMITTED",
            }
        }
    }
    atomic_write_json(tmp_path / "robot_state.json", robot, backup_existing=True)
    risk = json.loads((tmp_path / "risk_state.json").read_text(encoding="utf-8"))
    risk["accounts"][account_id]["kill_switch_active"] = True
    atomic_write_json(tmp_path / "risk_state.json", risk, backup_existing=True)
    report = ProductionReadinessEvaluator(
        tmp_path, app_version="0.3.6"
    ).evaluate(
        account_id=account_id,
        api_status={"authenticated": True, "available": True},
    )
    assert report.status == ReadinessStatus.BLOCKED
    codes = {check.code: check for check in report.checks}
    assert codes["PENDING_ORDER"].status == "FAIL"
    assert codes["RISK_PERSISTENT_GATE"].status == "FAIL"


def test_readiness_is_degraded_without_backup_or_api_probe(tmp_path: Path):
    create_runtime(tmp_path)
    report = ProductionReadinessEvaluator(
        tmp_path, app_version="0.3.6"
    ).evaluate(account_id="account-123")
    assert report.status == ReadinessStatus.DEGRADED
    assert any(check.code == "VALID_BACKUP" and check.status == "WARN" for check in report.checks)


class FakeSecretProvider:
    def __init__(self, *, name: str, secure: bool, value: str | None, error: Exception | None = None):
        self.name = name
        self.secure = secure
        self.value = value
        self.error = error

    def get(self, key: str) -> str | None:
        if self.error is not None:
            raise self.error
        return self.value

    def set(self, key: str, value: str) -> None:
        self.value = value

    def delete(self, key: str) -> None:
        self.value = None


def test_secret_provider_probe_never_returns_plaintext(tmp_path: Path):
    provider = FakeSecretProvider(
        name="Windows Credential Manager",
        secure=True,
        value="TOP-SECRET-CANARY",
    )
    result = probe_secret_provider(tmp_path, provider=provider)
    payload = result.to_dict()
    assert payload["secret_provider"] == "Windows Credential Manager"
    assert payload["secret_provider_secure"] is True
    assert payload["secret_provider_available"] is True
    assert payload["secret_present"] is True
    assert "TOP-SECRET-CANARY" not in json.dumps(payload)


def test_readiness_marks_secure_present_provider_pass_when_api_authenticated(tmp_path: Path):
    account_id = "account-123"
    create_runtime(tmp_path, account_id=account_id)
    backups = tmp_path / "backups"
    RuntimeBackupManager(tmp_path, app_version="0.3.6").create_backup(
        backups / "runtime.zip"
    )
    (tmp_path / "build_manifest.json").write_text(
        json.dumps({"software_version": "0.3.6"}), encoding="utf-8"
    )
    report = ProductionReadinessEvaluator(
        tmp_path,
        app_version="0.3.6",
        backups_dir=backups,
    ).evaluate(
        account_id=account_id,
        api_status={
            "authenticated": True,
            "available": True,
            "secret_provider": "Windows Credential Manager",
            "secret_provider_secure": True,
            "secret_provider_available": True,
            "secret_present": True,
        },
    )
    check = {item.code: item for item in report.checks}["SECRET_PROVIDER"]
    assert check.status == "PASS"
    assert "запись найдена" in check.detail
    assert "успешно использован" in check.detail


def test_readiness_warns_for_env_fallback_even_when_api_works(tmp_path: Path):
    create_runtime(tmp_path)
    report = ProductionReadinessEvaluator(
        tmp_path, app_version="0.3.6"
    ).evaluate(
        account_id="account-123",
        api_status={
            "authenticated": True,
            "available": True,
            "secret_provider": ".env fallback",
            "secret_provider_secure": False,
            "secret_provider_available": True,
            "secret_present": True,
        },
    )
    check = {item.code: item for item in report.checks}["SECRET_PROVIDER"]
    assert check.status == "WARN"
    assert "fallback" in check.detail


def test_readiness_blocks_when_secret_missing_and_authentication_failed(tmp_path: Path):
    create_runtime(tmp_path)
    report = ProductionReadinessEvaluator(
        tmp_path, app_version="0.3.6"
    ).evaluate(
        account_id="account-123",
        api_status={
            "authenticated": False,
            "available": False,
            "secret_provider": "Windows Credential Manager",
            "secret_provider_secure": True,
            "secret_provider_available": True,
            "secret_present": False,
            "detail": "Authentication failed",
        },
    )
    check = {item.code: item for item in report.checks}["SECRET_PROVIDER"]
    assert check.status == "FAIL"
    assert check.blocking is True
    assert report.status == ReadinessStatus.BLOCKED
