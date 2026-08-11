from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

CURRENT_FILES = {
    "CHANGELOG_V3_7_ALPHA3_RU.md",
    "MASTER_UPDATE_2026-08-10_V3_7_ALPHA3_RU.md",
    "RELEASE_MANIFEST_V3_7_ALPHA3.txt",
    "UPDATE_TO_V3_7_ALPHA3.md",
    "V3_7_ALPHA3_ARCHITECTURE_RU.md",
    "V3_7_ALPHA3_RECOVERY_RUNBOOK_RU.md",
    "V3_7_ALPHA3_TEST_PLAN_RU.md",
    "VERIFY_V3_7_ALPHA3.bat",
    "install_and_verify_v3_7_alpha3.bat",
    "restore_stable_default_risk_profile.bat",
}



LEGACY_RELATIVE_PATHS = {
    "tests/test_runtime_backup_rc1_1.py",
    "tests/test_stable_release_v3_6_0.py",
}

LEGACY_EXACT = {
    "AUDIT_AND_ROADMAP_RU.md",
    "DEVELOPMENT_PLAN_V3_5_RU.md",
    "LOG_REVIEW_2026-07-20_RU.md",
    "RESEARCH_PROTOCOL_RU.md",
    "TLS_FIX_V3_2_RU.md",
    "UPDATE_FROM_V3.md",
    "README_TESTFIX_RU.txt",
    "README_WINDOWS_VERIFIER_FIX_V2_RU.txt",
    "README_WINDOWS_CONSOLE_FIX_V3_RU.txt",
    "V3_6_ALPHA1_1_WINDOWS_TEST_GUIDE_RU.md",
    "run_risk_alpha_tests.bat",
}

LEGACY_GLOBS = (
    "README_HOTFIX_*",
    "BUILD_INFO_V*",
    "CHANGELOG_V*",
    "UPDATE_TO_V*",
    "VERIFY_V*",
    "install_and_verify_alpha*.bat",
    "install_and_verify_beta*.bat",
    "install_and_verify_rc*.bat",
    "install_and_verify_v*.bat",
    "MASTER_UPDATE_*",
    "RELEASE_MANIFEST_V*",
    "V3_*_TEST_PLAN_RU.md",
    "V3_*_ARCHITECTURE_RU.md",
    "V3_*_RECOVERY_RUNBOOK_RU.md",
    "V3_*_ACCEPTANCE_REPORT_RU.md",
    "V3_*_SANDBOX_TEST_PLAN_RU.md",
    "V3_*_OBSERVABILITY_TEST_PLAN_RU.md",
    "V3_*_STATE_TEST_PLAN_RU.md",
    "V3_*_SHADOW_TEST_PLAN_RU.md",
)

RUNTIME_NAMES = {
    ".env",
    "robot_state.json",
    "portfolio_state.json",
    "sandbox_diagnostic_state.json",
    "strategy_profiles.json",
    "risk_profiles.json",
    "risk_state.json",
    "trading_events.db",
    "robot_gui.log",
    "robot_debug.log",
    "runtime_bootstrap_report.json",
    "runtime_bootstrap.lock",
    "moex_robot_gui.lock",
    "risk_profile.json",
    "trading_events.db-wal",
    "trading_events.db-shm",
    "risk_profiles.json.lock",
    "strategy_profiles.json.lock",
    "risk_state.json.lock",
    "robot_state.json.lock",
    "portfolio_state.json.lock",
    "portfolio_state.json.sha256",
    "portfolio_state.json.lastgood",
    "portfolio_state.json.lastgood.sha256",
    "canonical_migration_report.json",
    "canonical_migration_report.json.sha256",
    "canonical_migration_report.json.lastgood",
    "canonical_migration_report.json.lastgood.sha256",
    "portfolio_legacy_shadow.json",
    "portfolio_legacy_shadow.json.sha256",
    "portfolio_legacy_shadow.json.lastgood",
    "portfolio_legacy_shadow.json.lastgood.sha256",
    "sandbox_diagnostic_state.json.lock",
    "runtime_backup.lock",
    "build_manifest.runtime.json",
}


def find_legacy_files(root: Path) -> list[Path]:
    found: dict[str, Path] = {}
    for relative in LEGACY_RELATIVE_PATHS:
        path = root / relative
        if path.is_file():
            found[relative] = path
    for name in LEGACY_EXACT:
        path = root / name
        if path.is_file() and name not in CURRENT_FILES:
            found[name] = path
    for pattern in LEGACY_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file() or path.name in CURRENT_FILES:
                continue
            key = path.relative_to(root).as_posix()
            found[key] = path
    return [found[key] for key in sorted(found)]


def clear_python_caches(root: Path) -> list[Path]:
    """Remove stale bytecode that can survive deterministic ZIP overlays.

    Release archives use fixed timestamps for reproducibility.  Without this
    cleanup, an old same-size ``.pyc`` may appear valid after a hotfix overlay
    and Python can execute the previous version until the cache is deleted.
    """

    removed: list[Path] = []
    for directory in sorted(
        (path for path in root.rglob("__pycache__") if path.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        shutil.rmtree(directory, ignore_errors=False)
        removed.append(directory)
    for pattern in ("*.pyc", "*.pyo"):
        for path in root.rglob(pattern):
            if path.is_file():
                path.unlink()
                removed.append(path)
    return removed


def clean(root: Path) -> list[Path]:
    removed: list[Path] = []
    for path in find_legacy_files(root):
        path.unlink()
        removed.append(path)
    removed.extend(clear_python_caches(root))
    return removed


def check_runtime_absent(root: Path) -> list[Path]:
    found = {root / name for name in RUNTIME_NAMES if (root / name).exists()}
    found.update(path for path in root.glob("*.lock") if path.is_file())
    found.update(
        path
        for pattern in (
            "portfolio_state.schema1.*.backup.json",
            "portfolio_state.schema1.*.backup.json.sha256",
        )
        for path in root.glob(pattern)
        if path.is_file()
    )
    return sorted(found, key=lambda item: item.name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean/check release tree.")
    parser.add_argument("--root", default=".", help="Release root directory.")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete legacy release files from the root.",
    )
    parser.add_argument(
        "--check-runtime",
        action="store_true",
        help="Also fail if runtime/private files are present.",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"[ERROR] Release root is not a directory: {root}")
        return 2

    if args.clean:
        removed = clean(root)
        for path in removed:
            print(f"REMOVED {path.name}")

    legacy = find_legacy_files(root)
    runtime = check_runtime_absent(root) if args.check_runtime else []
    if legacy:
        print("[ERROR] Legacy release files remain:")
        for path in legacy:
            print(f"  - {path.name}")
    if runtime:
        print("[ERROR] Runtime/private files are present:")
        for path in runtime:
            print(f"  - {path.name}")
    if legacy or runtime:
        return 1
    print("Release hygiene: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
