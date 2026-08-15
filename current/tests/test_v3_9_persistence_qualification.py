from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from test_central_order_coordinator_v3_8 import ACCOUNT
from test_v3_9_configure_enforced_runtime_tool import (
    args as enforced_args,
)
from test_v3_9_configure_enforced_runtime_tool import (
    ready_runtime,
    secure_probe,
)

from tools import v3_9_configure_enforced_runtime as configure
from tools import v3_9_persistence_qualification as qualification
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.support_bundle import SupportBundleBuilder


def _standalone(root: Path) -> Path:
    for name in ("app", "runtime", "backups", "reports", "logs", "support"):
        (root / name).mkdir(parents=True)
    (root / "MOEX Research Robot.bat").write_text("launcher", encoding="utf-8")
    (root / "app" / "MOEXResearchRobot.exe").write_bytes(b"placeholder")
    (root / "app" / "build_manifest.json").write_text(
        json.dumps(
            {
                "software_version": "0.3.9b1",
                "release_channel": "beta",
                "sandbox_only": True,
                "real_account_execution": False,
                "risk_state_schema": 4,
            }
        ),
        encoding="utf-8",
    )
    return root


def _qualified_runtime(tmp_path: Path) -> Path:
    root = ready_runtime(tmp_path)
    configure.run(
        enforced_args(
            root,
            "apply",
            confirm=configure.APPLY_CONFIRMATION,
        ),
        secret_probe_factory=secure_probe,
    )
    return root


def test_final_review_is_read_only_and_verifies_all_m5_3_artifacts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / ACCOUNT
    root = _qualified_runtime(workspace)
    backup = RuntimeBackupManager(root, app_version="0.3.9b1").create_backup(
        workspace / "artifacts" / "runtime.zip"
    )
    support = (
        SupportBundleBuilder(root, app_version="0.3.9b1")
        .build(
            workspace / "artifacts" / "support.zip",
            account_id=ACCOUNT,
        )
        .path
    )
    standalone = _standalone(workspace / "portable")
    material_before = {
        path.name: path.read_bytes() for path in root.iterdir() if path.is_file()
    }
    args = qualification.parse_args(
        [
            "final-review",
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
            "--backup",
            str(backup),
            "--support-bundle",
            str(support),
            "--standalone-root",
            str(standalone),
        ]
    )

    result = qualification.run(args)

    assert result["failures"] == [], result["failures"]
    assert result["status"] == "PASS", result
    assert result["backup"]["valid"] is True
    assert result["support_bundle"]["account_id_leaks"] == []
    assert result["support_bundle"]["runtime_integrity_failures"] == []
    assert result["standalone"]["valid"] is True
    assert result["writes_performed"] is False
    assert result["dispatch_authorized"] is False
    assert result["provider_post_authorized"] is False
    assert ACCOUNT not in json.dumps(result)
    assert {
        path.name: path.read_bytes() for path in root.iterdir() if path.is_file()
    } == material_before


def test_final_review_requires_all_external_artifacts(tmp_path: Path) -> None:
    root = _qualified_runtime(tmp_path)
    args = qualification.parse_args(
        [
            "final-review",
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
        ]
    )

    result = qualification.run(args)

    assert result["status"] == "FAIL"
    assert "FINAL_REVIEW_ARTIFACTS_INCOMPLETE" in result["failures"]
    assert result["writes_performed"] is False


def test_non_empty_wal_fails_without_opening_or_creating_shm(tmp_path: Path) -> None:
    root = _qualified_runtime(tmp_path)
    journal = root / "trading_events.db"
    wal = journal.with_name(journal.name + "-wal")
    shm = journal.with_name(journal.name + "-shm")
    wal.write_bytes(b"uncheckpointed-test-wal")
    shm.unlink(missing_ok=True)
    args = qualification.parse_args(
        [
            "inspect",
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
        ]
    )

    result = qualification.run(args)

    assert result["status"] == "FAIL"
    assert "EVENT_JOURNAL_WAL_NOT_EMPTY" in result["failures"]
    assert result["runtime"]["integrity"]["trading_events.db"]["status"] == (
        "WAL_NOT_EMPTY"
    )
    assert result["writes_performed"] is False
    assert wal.read_bytes() == b"uncheckpointed-test-wal"
    assert not shm.exists()


def test_output_must_be_outside_runtime_and_standalone_roots(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    standalone = tmp_path / "portable"
    args = qualification.parse_args(
        [
            "inspect",
            "--runtime-dir",
            str(runtime),
            "--account-id",
            ACCOUNT,
            "--standalone-root",
            str(standalone),
            "--output",
            str(runtime / "qualification.json"),
        ]
    )
    with pytest.raises(ValueError, match="outside"):
        qualification._validate_output_boundary(args)

    args.output = standalone / "qualification.json"
    with pytest.raises(ValueError, match="outside"):
        qualification._validate_output_boundary(args)


def test_wrong_account_failure_never_serializes_runtime_account(tmp_path: Path) -> None:
    root = _qualified_runtime(tmp_path)
    args = qualification.parse_args(
        [
            "inspect",
            "--runtime-dir",
            str(root),
            "--account-id",
            "caller-supplied-wrong-account",
        ]
    )

    result = qualification.run(args)
    serialized = json.dumps(result)

    assert result["status"] == "FAIL"
    assert ACCOUNT not in serialized
    assert "caller-supplied-wrong-account" not in serialized


def test_error_payload_does_not_echo_unknown_exception_identifiers(
    tmp_path: Path,
) -> None:
    args = qualification.parse_args(
        [
            "inspect",
            "--runtime-dir",
            str(tmp_path),
            "--account-id",
            "caller-supplied-wrong-account",
        ]
    )

    result = qualification._redacted_error(
        args,
        ValueError(f"persisted account mismatch: {ACCOUNT}"),
    )

    assert ACCOUNT not in json.dumps(result)
    assert result["error"]["type"] == "ValueError"


def test_backup_app_version_must_match_qualification_contract(tmp_path: Path) -> None:
    root = _qualified_runtime(tmp_path)
    backup = RuntimeBackupManager(root, app_version="wrong-version").create_backup(
        tmp_path / "artifacts" / "runtime.zip"
    )
    args = qualification.parse_args(
        [
            "inspect",
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
            "--backup",
            str(backup),
        ]
    )

    result = qualification.run(args)

    assert result["status"] == "FAIL"
    assert "BACKUP_APP_VERSION_MISMATCH" in result["failures"]
    assert result["backup"]["valid"] is False


def test_support_bundle_rejects_unhashed_archive_member(tmp_path: Path) -> None:
    root = _qualified_runtime(tmp_path)
    support = (
        SupportBundleBuilder(root, app_version="0.3.9b1")
        .build(
            tmp_path / "artifacts" / "support.zip",
            account_id=ACCOUNT,
        )
        .path
    )
    with zipfile.ZipFile(support, "a") as archive:
        archive.writestr("untracked.txt", "unhashed qualification probe")
    args = qualification.parse_args(
        [
            "inspect",
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
            "--support-bundle",
            str(support),
        ]
    )

    result = qualification.run(args)

    assert result["status"] == "FAIL"
    assert "SUPPORT_BUNDLE_CHECKSUM_COVERAGE_INVALID" in result["failures"]
    assert result["support_bundle"]["checksum_coverage_errors"] == [
        "UNHASHED:untracked.txt"
    ]


def test_support_report_does_not_serialize_archive_exception(
    tmp_path: Path,
    monkeypatch,
) -> None:
    unknown_account = "persisted-foreign-account-991122"

    def fail_archive(*_args, **_kwargs):
        raise OSError(f"unreadable archive for {unknown_account}")

    monkeypatch.setattr(qualification.zipfile, "ZipFile", fail_archive)

    report, failures = qualification._support_report(
        tmp_path / "support.zip",
        account_id=ACCOUNT,
    )
    serialized = json.dumps({"report": report, "failures": failures})

    assert unknown_account not in serialized
    assert failures == ["SUPPORT_BUNDLE_INVALID:OSError"]
