from __future__ import annotations

import json
import re
from pathlib import Path

from tools.verify_standalone_layout import (
    STRUCTURAL_FIXTURE_LAUNCHER,
    STRUCTURAL_FIXTURE_MARKER,
    verify_layout,
)
from trading_robot.paths import resolve_app_paths


def test_portable_path_resolution_uses_sibling_runtime(monkeypatch, tmp_path: Path):
    app_dir = tmp_path / "portable" / "app"
    app_dir.mkdir(parents=True)
    source = app_dir / "desktop_gui.py"
    source.write_text("", encoding="utf-8")
    monkeypatch.setenv("MOEX_ROBOT_PORTABLE_LAYOUT", "1")
    monkeypatch.delenv("MOEX_ROBOT_RUNTIME_DIR", raising=False)
    paths = resolve_app_paths(source)
    assert paths.app_dir == app_dir.resolve()
    assert paths.runtime_dir == (tmp_path / "portable" / "runtime").resolve()
    assert paths.backups_dir == paths.runtime_dir / "backups"


def test_standalone_layout_verifier_accepts_v3_9_stable_tree(tmp_path: Path):
    for name in ("app", "runtime", "backups", "reports", "logs", "support"):
        (tmp_path / name).mkdir()
    (tmp_path / "MOEX Research Robot.bat").write_text("launcher", encoding="utf-8")
    (tmp_path / "app" / "MOEXResearchRobot.exe").write_bytes(b"placeholder")
    (tmp_path / "app" / "build_manifest.json").write_text(
        json.dumps(
            {
                "software_version": "0.3.9",
                "release_channel": "stable",
                "sandbox_only": True,
                "real_account_execution": False,
            }
        ),
        encoding="utf-8",
    )
    assert verify_layout(tmp_path) == []


def test_standalone_layout_verifier_accepts_v3_9_beta_contract(tmp_path: Path):
    for name in ("app", "runtime", "backups", "reports", "logs", "support"):
        (tmp_path / name).mkdir()
    (tmp_path / "MOEX Research Robot.bat").write_text("launcher", encoding="utf-8")
    (tmp_path / "app" / "MOEXResearchRobot.exe").write_bytes(b"placeholder")
    (tmp_path / "app" / "build_manifest.json").write_text(
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

    assert verify_layout(
        tmp_path,
        expected_version="0.3.9b1",
        expected_channel="beta",
        minimum_risk_state_schema=4,
    ) == []


def test_standalone_layout_rejects_private_state_inside_runtime(tmp_path: Path):
    for name in ("app", "runtime", "backups", "reports", "logs", "support"):
        (tmp_path / name).mkdir()
    (tmp_path / "MOEX Research Robot.bat").write_text("launcher", encoding="utf-8")
    (tmp_path / "app" / "MOEXResearchRobot.exe").write_bytes(b"placeholder")
    (tmp_path / "app" / "build_manifest.json").write_text(
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
    (tmp_path / "runtime" / "risk_state.json").write_text(
        '{"version": 4, "accounts": {}}',
        encoding="utf-8",
    )

    errors = verify_layout(
        tmp_path,
        expected_version="0.3.9b1",
        expected_channel="beta",
        minimum_risk_state_schema=4,
    )
    assert any("runtime/risk_state.json" in error for error in errors)


def test_standalone_layout_rejects_non_object_manifest_and_backup_state(
    tmp_path: Path,
):
    for name in ("app", "runtime", "backups", "reports", "logs", "support"):
        (tmp_path / name).mkdir()
    (tmp_path / "MOEX Research Robot.bat").write_text("launcher", encoding="utf-8")
    (tmp_path / "app" / "MOEXResearchRobot.exe").write_bytes(b"placeholder")
    (tmp_path / "app" / "build_manifest.json").write_text("[]", encoding="utf-8")
    (tmp_path / "app" / "risk_state.json.bak").write_text("{}", encoding="utf-8")

    errors = verify_layout(tmp_path)

    assert "Build manifest root is not an object" in errors
    assert any("app/risk_state.json.bak" in error for error in errors)


def test_structural_fixture_requires_explicit_opt_in_and_no_executable(
    tmp_path: Path,
):
    for name in ("app", "runtime", "backups", "reports", "logs", "support"):
        (tmp_path / name).mkdir()
    (tmp_path / "MOEX Research Robot.bat").write_text(
        STRUCTURAL_FIXTURE_LAUNCHER,
        encoding="utf-8",
    )
    (tmp_path / "app" / "EXECUTABLE_NOT_BUILT.txt").write_text(
        STRUCTURAL_FIXTURE_MARKER,
        encoding="utf-8",
    )
    (tmp_path / "app" / "build_manifest.json").write_text(
        json.dumps(
            {
                "software_version": "0.3.9b1",
                "release_channel": "beta",
                "sandbox_only": True,
                "real_account_execution": False,
                "risk_state_schema": 4,
                "artifact_kind": "STRUCTURAL_LAYOUT_FIXTURE",
                "executable_built": False,
                "executable_launch_verified": False,
            }
        ),
        encoding="utf-8",
    )

    without_opt_in = verify_layout(
        tmp_path,
        expected_version="0.3.9b1",
        expected_channel="beta",
        minimum_risk_state_schema=4,
    )
    with_opt_in = verify_layout(
        tmp_path,
        expected_version="0.3.9b1",
        expected_channel="beta",
        minimum_risk_state_schema=4,
        allow_structural_fixture=True,
    )

    assert "Missing executable: app/MOEXResearchRobot.exe" in without_opt_in
    assert "Structural fixture requires explicit opt-in" in without_opt_in
    assert with_opt_in == []


def test_structural_fixture_rejects_launcher_and_manifest_contradictions(
    tmp_path: Path,
):
    for name in ("app", "runtime", "backups", "reports", "logs", "support"):
        (tmp_path / name).mkdir()
    (tmp_path / "MOEX Research Robot.bat").write_text(
        "@echo off\necho unsafe launcher\nexit /b 0\n",
        encoding="utf-8",
    )
    (tmp_path / "app" / "EXECUTABLE_NOT_BUILT.txt").write_text(
        "ambiguous marker",
        encoding="utf-8",
    )
    (tmp_path / "app" / "build_manifest.json").write_text(
        json.dumps(
            {
                "software_version": "0.3.9b1",
                "release_channel": "beta",
                "sandbox_only": True,
                "real_account_execution": False,
                "risk_state_schema": 4,
                "artifact_kind": "STRUCTURAL_LAYOUT_FIXTURE",
                "executable_built": True,
                "executable_launch_verified": True,
            }
        ),
        encoding="utf-8",
    )

    errors = verify_layout(
        tmp_path,
        expected_version="0.3.9b1",
        expected_channel="beta",
        minimum_risk_state_schema=4,
        allow_structural_fixture=True,
    )

    assert "Structural fixture manifest must set executable_built=false" in errors
    assert any("executable_launch_verified=false" in error for error in errors)
    assert "Structural fixture marker contract mismatch" in errors
    assert "Structural fixture launcher must fail closed" in errors


def test_standalone_sources_are_present_and_use_portable_environment():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "MOEXResearchRobot.spec").read_text(encoding="utf-8")
    builder = (root / "BUILD_STANDALONE.bat").read_text(encoding="utf-8")
    launcher = (root / "portable_launcher.bat").read_text(encoding="utf-8")
    assert "desktop_gui.py" in spec
    assert "MOEXResearchRobot" in spec
    assert "V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md" in spec
    assert "V3_9_0_STABLE_RECOVERY_RUNBOOK_RU.md" not in spec
    assert "V3_7_0_STABLE_RECOVERY_RUNBOOK_RU.md" not in spec
    assert "V3_7_BETA1_RECOVERY_RUNBOOK_RU.md" not in spec
    assert "V3_7_ALPHA3_RECOVERY_RUNBOOK_RU.md" not in spec
    assert "PyInstaller" in builder
    assert "verify_standalone_layout.py" in builder
    assert "MOEX_ROBOT_PORTABLE_LAYOUT=1" in launcher
    assert "MOEX_ROBOT_RUNTIME_DIR" in launcher


def test_gui_robot_loop_uses_resolved_runtime_for_all_mutable_state():
    root = Path(__file__).resolve().parents[1]
    source = (root / "desktop_gui.py").read_text(encoding="utf-8")

    assert "state_file=str(ROBOT_STATE_PATH)" in source
    assert len(
        re.findall(
            r"RiskRuntimeAdapter\.from_directory\(\s*RUNTIME_DIR,",
            source,
        )
    ) == 2
    assert not re.search(
        r"RiskRuntimeAdapter\.from_directory\(\s*APP_DIR,",
        source,
    )
