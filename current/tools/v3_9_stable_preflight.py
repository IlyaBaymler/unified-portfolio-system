"""Fail-closed source preflight for the v3.9.0 M6 Stable candidate.

The tool does not build an executable, touch runtime state, contact a broker or
claim any manual M6 gate.  It verifies that the source tree is an internally
consistent candidate before expensive and operator-controlled qualification.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

try:
    from .release_cleanup import check_runtime_absent, find_legacy_files
except ImportError:  # direct script execution
    from release_cleanup import check_runtime_absent, find_legacy_files


EXPECTED_VERSION = "0.3.9"
EXPECTED_DISPLAY_VERSION = "v3.9.0"
EXPECTED_IMPLEMENTATION_COMMIT = "cddd80f3caf7191ecf2f85df9e1ccfea97af6cc4"
EXPECTED_BRANCH_BASE_COMMIT = "dd3b9a35f5d2b2dfb9e8776facb81712848e2c7d"

REQUIRED_RELEASE_FILES = (
    "CHANGELOG_V3_9_0_STABLE_RU.md",
    "MASTER_UPDATE_2026-08-15_V3_9_0_STABLE_RU.md",
    "RELEASE_MANIFEST_V3_9_0_STABLE.txt",
    "UPDATE_TO_V3_9_0_STABLE.md",
    "V3_9_0_STABLE_ARCHITECTURE_RU.md",
    "V3_9_0_STABLE_RECOVERY_RUNBOOK_RU.md",
    "V3_9_0_STABLE_TEST_PLAN_RU.md",
    "VERIFY_V3_9_0_STABLE.bat",
    "install_and_verify_v3_9_0.bat",
)

MANUAL_GATES = (
    "standalone_build",
    "standalone_launch_without_system_python",
    "clean_install",
    "upgrade_and_isolated_rollback",
    "restart_disconnect_partial_fill_matrix",
    "global_kill_switch",
    "instrument_kill_switch",
    "sandbox_burn_in_24_48h",
    "final_artifact_secret_scan",
    "explicit_user_acceptance",
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root is not an object: {path.name}")
    return value


def _package_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            value = ast.literal_eval(node.value)
            return str(value)
    raise ValueError("trading_robot.__version__ assignment is missing")


def review_manifest(manifest: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    expected_values = {
        "software_version": EXPECTED_VERSION,
        "display_version": EXPECTED_DISPLAY_VERSION,
        "release_channel": "stable",
        "implementation_baseline_commit": EXPECTED_IMPLEMENTATION_COMMIT,
        "qualification_branch_base_commit": EXPECTED_BRANCH_BASE_COMMIT,
        "portfolio_state_schema": 2,
        "risk_state_schema": 4,
        "central_order_state_schema": 1,
        "portfolio_risk_mode": "ENFORCED",
    }
    for key, expected in expected_values.items():
        if manifest.get(key) != expected:
            failures.append(
                f"build_manifest.{key}={manifest.get(key)!r}; expected {expected!r}"
            )

    required_true = ("sandbox_only", "multi_instrument_runtime", "multi_instrument_execution")
    for key in required_true:
        if manifest.get(key) is not True:
            failures.append(f"build_manifest.{key} must be true")
    required_false = (
        "real_account_execution",
        "automatic_position_adoption",
        "multi_asset_execution",
    )
    for key in required_false:
        if manifest.get(key) is not False:
            failures.append(f"build_manifest.{key} must be false")

    qualification = manifest.get("stable_qualification")
    if not isinstance(qualification, dict):
        failures.append("build_manifest.stable_qualification must be an object")
        return failures
    if qualification.get("status") != "candidate":
        failures.append("stable_qualification.status must remain candidate")
    if qualification.get("automated_source_preflight_complete") is not True:
        failures.append(
            "stable_qualification.automated_source_preflight_complete must be true"
        )
    if qualification.get("automated_source_preflight_tests") != 780:
        failures.append(
            "stable_qualification.automated_source_preflight_tests must be 780"
        )
    if qualification.get("automated_source_preflight_date") != "2026-08-15":
        failures.append(
            "stable_qualification.automated_source_preflight_date must be 2026-08-15"
        )
    if qualification.get("deterministic_source_artifacts_complete") is not True:
        failures.append(
            "stable_qualification.deterministic_source_artifacts_complete must be true"
        )
    pending_flags = (
        "user_acceptance",
        "final_burn_in_complete",
        "standalone_build_complete",
        "standalone_launch_complete",
        "upgrade_rollback_complete",
        "restart_disconnect_partial_fill_complete",
        "global_kill_switch_accepted",
        "instrument_kill_switch_accepted",
        "release_artifacts_complete",
    )
    for key in pending_flags:
        if qualification.get(key) is not False:
            failures.append(f"stable_qualification.{key} must remain false")
    return failures


def _contract_hashes(root: Path, relative_paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in sorted(relative_paths):
        path = root / relative
        if path.is_file():
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def review_source(root: Path) -> dict[str, Any]:
    source = root.resolve()
    failures: list[str] = []
    manifest: dict[str, Any] = {}
    manifest_path = source / "build_manifest.json"
    try:
        manifest = _read_json(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        failures.append(f"build_manifest.json invalid: {exc}")
    else:
        failures.extend(review_manifest(manifest))

    try:
        package_version = _package_version(source / "trading_robot" / "__init__.py")
    except (OSError, UnicodeError, SyntaxError, ValueError) as exc:
        package_version = None
        failures.append(f"package version invalid: {exc}")
    if package_version != EXPECTED_VERSION:
        failures.append(
            f"trading_robot.__version__={package_version!r}; expected {EXPECTED_VERSION!r}"
        )

    for relative in REQUIRED_RELEASE_FILES:
        if not (source / relative).is_file():
            failures.append(f"required release file missing: {relative}")

    for path in find_legacy_files(source):
        failures.append(f"legacy release file remains: {path.relative_to(source).as_posix()}")
    for path in check_runtime_absent(source):
        failures.append(f"private runtime file in source root: {path.name}")

    text_contracts = {
        "BUILD_RELEASE.bat": ("v3.9.0", "moex_trading_robot_research_v3_9_0"),
        "BUILD_STANDALONE.bat": ("v3.9.0", "MOEX_Research_Robot_v3_9_0", "0.3.9"),
        "MOEXResearchRobot.spec": ("V3_9_0_STABLE_RECOVERY_RUNBOOK_RU.md",),
        "portable_launcher.bat": ("v3.9.0", "MOEX_ROBOT_PORTABLE_LAYOUT=1"),
    }
    for relative, needles in text_contracts.items():
        path = source / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(f"cannot read {relative}: {exc}")
            continue
        for needle in needles:
            if needle not in text:
                failures.append(f"{relative} missing contract text: {needle}")

    contract_paths = [
        "build_manifest.json",
        "trading_robot/__init__.py",
        "BUILD_RELEASE.bat",
        "BUILD_STANDALONE.bat",
        "MOEXResearchRobot.spec",
        "portable_launcher.bat",
        "tools/release_cleanup.py",
        "tools/verify_standalone_layout.py",
        "tools/v3_9_source_artifact_qualification.py",
        "tools/v3_9_stable_preflight.py",
        "tests/test_stable_release_v3_9.py",
        "tests/test_standalone_rc1.py",
        "tests/test_v3_9_source_artifact_qualification.py",
        "tests/test_v3_9_stable_preflight.py",
        *REQUIRED_RELEASE_FILES,
    ]
    for relative in contract_paths:
        if not (source / relative).is_file():
            failures.append(f"candidate contract file missing: {relative}")
    hashes = _contract_hashes(source, contract_paths)
    digest_source = json.dumps(hashes, ensure_ascii=False, sort_keys=True).encode("utf-8")

    return {
        "schema_version": 1,
        "qualification": "v3.9.0-m6-source-preflight",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "source_directory": source.name,
        "software_version": manifest.get("software_version"),
        "display_version": manifest.get("display_version"),
        "release_channel": manifest.get("release_channel"),
        "implementation_baseline_commit": manifest.get("implementation_baseline_commit"),
        "qualification_branch_base_commit": manifest.get("qualification_branch_base_commit"),
        "candidate_contract_sha256": hashlib.sha256(digest_source).hexdigest(),
        "candidate_contract_files": hashes,
        "source_writes_performed": False,
        "broker_calls_performed": False,
        "provider_post_performed": False,
        "manual_gates": {name: "PENDING" for name in MANUAL_GATES},
    }


def _output_path(source: Path, value: str | Path) -> Path:
    output = Path(value)
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()
    else:
        output = output.resolve()
    try:
        relative = output.relative_to(source.resolve())
    except ValueError:
        return output
    if not relative.parts or relative.parts[0] != "verification_output":
        raise ValueError(
            "Output inside source is allowed only under excluded verification_output"
        )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only v3.9.0 M6 Stable candidate source preflight."
    )
    parser.add_argument("--source-root", default=".")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    source = Path(args.source_root).resolve()
    if not source.is_dir():
        print(f"[ERROR] Source root is not a directory: {source}")
        return 2
    try:
        output = _output_path(source, args.output) if args.output else None
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 2

    report = review_source(source)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
