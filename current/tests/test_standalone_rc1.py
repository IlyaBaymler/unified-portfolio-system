from __future__ import annotations

import json
from pathlib import Path

from tools.verify_standalone_layout import verify_layout
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


def test_standalone_layout_verifier_accepts_expected_tree(tmp_path: Path):
    for name in ("app", "runtime", "backups", "reports", "logs", "support"):
        (tmp_path / name).mkdir()
    (tmp_path / "MOEX Research Robot.bat").write_text("launcher", encoding="utf-8")
    (tmp_path / "app" / "MOEXResearchRobot.exe").write_bytes(b"placeholder")
    (tmp_path / "app" / "build_manifest.json").write_text(
        json.dumps({"software_version": "0.3.7b1"}), encoding="utf-8"
    )
    assert verify_layout(tmp_path) == []


def test_standalone_sources_are_present_and_use_portable_environment():
    root = Path(__file__).resolve().parents[1]
    spec = (root / "MOEXResearchRobot.spec").read_text(encoding="utf-8")
    builder = (root / "BUILD_STANDALONE.bat").read_text(encoding="utf-8")
    launcher = (root / "portable_launcher.bat").read_text(encoding="utf-8")
    assert "desktop_gui.py" in spec
    assert "MOEXResearchRobot" in spec
    assert "V3_7_BETA1_RECOVERY_RUNBOOK_RU.md" in spec
    assert "V3_7_ALPHA3_RECOVERY_RUNBOOK_RU.md" not in spec
    assert "PyInstaller" in builder
    assert "verify_standalone_layout.py" in builder
    assert "MOEX_ROBOT_PORTABLE_LAYOUT=1" in launcher
    assert "MOEX_ROBOT_RUNTIME_DIR" in launcher
