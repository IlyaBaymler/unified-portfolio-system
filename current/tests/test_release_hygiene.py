from __future__ import annotations

from pathlib import Path

from tools.release_cleanup import CURRENT_FILES, clean, find_legacy_files


def test_cleanup_removes_legacy_and_preserves_current_files(tmp_path: Path):
    legacy = tmp_path / "README_HOTFIX_V3_4.txt"
    legacy.write_text("old", encoding="utf-8")
    old_update = tmp_path / "UPDATE_TO_V3_6_0.md"
    old_update.write_text("old", encoding="utf-8")
    old_installer = tmp_path / "install_and_verify_alpha3.bat"
    old_installer.write_text("old", encoding="utf-8")
    previous_beta = tmp_path / "install_and_verify_beta0.bat"
    previous_beta.write_text("old", encoding="utf-8")
    previous_rc = tmp_path / "install_and_verify_rc1.bat"
    previous_rc.write_text("old", encoding="utf-8")
    previous_stable = tmp_path / "install_and_verify_v3_6_0.bat"
    previous_stable.write_text("old", encoding="utf-8")
    old_recovery = tmp_path / "V3_6_0_RECOVERY_RUNBOOK_RU.md"
    old_recovery.write_text("old", encoding="utf-8")
    old_verifier_note = tmp_path / "README_WINDOWS_VERIFIER_FIX_V2_RU.txt"
    old_verifier_note.write_text("old", encoding="utf-8")
    old_console_note = tmp_path / "README_WINDOWS_CONSOLE_FIX_V3_RU.txt"
    old_console_note.write_text("old", encoding="utf-8")
    old_nested_test = tmp_path / "tests" / "test_runtime_backup_rc1_1.py"
    old_nested_test.parent.mkdir(parents=True, exist_ok=True)
    old_nested_test.write_text("old", encoding="utf-8")
    old_stable_test = tmp_path / "tests" / "test_stable_release_v3_6_0.py"
    old_stable_test.write_text("old", encoding="utf-8")
    previous_alpha = tmp_path / "UPDATE_TO_V3_7_ALPHA3.md"
    previous_alpha.write_text("old", encoding="utf-8")
    previous_beta1 = tmp_path / "UPDATE_TO_V3_7_BETA1.md"
    previous_beta1.write_text("old", encoding="utf-8")
    current = tmp_path / "UPDATE_TO_V3_7_0_STABLE.md"
    current.write_text("current", encoding="utf-8")

    removed = clean(tmp_path)

    assert {item.name for item in removed} == {
        legacy.name,
        old_update.name,
        old_installer.name,
        previous_beta.name,
        previous_rc.name,
        previous_stable.name,
        old_recovery.name,
        old_verifier_note.name,
        old_console_note.name,
        old_nested_test.name,
        old_stable_test.name,
        previous_alpha.name,
        previous_beta1.name,
    }
    assert current.exists()
    assert find_legacy_files(tmp_path) == []


def test_current_source_tree_has_no_legacy_release_files():
    root = Path(__file__).resolve().parents[1]
    remaining = [path.name for path in find_legacy_files(root)]
    assert remaining == [], remaining
    assert "VERIFY_V3_7_0_STABLE.bat" in CURRENT_FILES
    assert "install_and_verify_v3_7_0.bat" in CURRENT_FILES
    assert "UPDATE_TO_V3_7_0_STABLE.md" in CURRENT_FILES
    assert "V3_7_0_STABLE_ARCHITECTURE_RU.md" in CURRENT_FILES
    assert "MASTER_UPDATE_2026-08-12_V3_7_0_STABLE_RU.md" in CURRENT_FILES


def test_cleanup_removes_stale_python_bytecode(tmp_path: Path):
    cache = tmp_path / "trading_robot" / "__pycache__"
    cache.mkdir(parents=True)
    stale = cache / "__init__.cpython-313.pyc"
    stale.write_bytes(b"old bytecode")
    removed = clean(tmp_path)
    assert cache in removed
    assert not cache.exists()
