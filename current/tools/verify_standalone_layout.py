from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

try:
    from .release_safety import has_financial_capture_path, private_capture_file, validate_member_inventory
except ImportError:  # direct script execution
    from release_safety import has_financial_capture_path, private_capture_file, validate_member_inventory

REQUIRED_DIRS = ("app", "runtime", "backups", "reports", "logs", "support")
STRUCTURAL_FIXTURE_LAUNCHER = """@echo off
echo Qualification-only structural fixture: executable was not built.
exit /b 2
"""
STRUCTURAL_FIXTURE_MARKER = """STRUCTURAL_LAYOUT_FIXTURE
EXECUTABLE_NOT_BUILT
executable_launch_verified=false
"""
FORBIDDEN_RUNTIME_NAMES = {
    ".env",
    "v3_8_runtime_seed_manifest.json",
    "v3_9_shadow_runtime_seed_manifest.json",
    "v3_9_shadow_runtime_config_manifest.json",
    "v3_9_enforced_runtime_manifest.json",
    "v3_9_m5_2_runtime_seed_manifest.json",
    "v3_9_external_close_ack_manifest.json",
    "v3_9_shadow_runtime_start_manifest.json",
    "portfolio_risk_metadata.json",
    "robot_state.json",
    "portfolio_state.json",
    "risk_state.json",
    "risk_profiles.json",
    "strategy_profiles.json",
    "multi_instrument_profiles.json",
    "instrument_runtimes.json",
    "central_order_state.json",
    "sandbox_diagnostic_state.json",
    "trading_events.db",
    "robot_gui.log",
    "robot_debug.log",
    "runtime_cash_authority.json",
    "runtime_cash_authority.json.sha256",
    "runtime_cash_authority.json.lastgood",
    "cash_ledger_v3_10.sqlite3",
    "store.sqlite3",
    "store.sqlite3-wal",
    "store.sqlite3-shm",
}
FORBIDDEN_RUNTIME_DIRECTORIES = {
    "qualification_output",
    "verification_output",
}


def _private_runtime_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in FORBIDDEN_RUNTIME_NAMES
        or lowered.endswith(
            ("-wal", "-shm", ".lock", ".lastgood", ".lastgood.sha256", ".bak")
        )
        or ".pre_restore_" in lowered
        or (
            lowered.endswith(".sha256")
            and any(
                lowered.startswith(prefix)
                for prefix in (
                    "portfolio_",
                    "multi_instrument_",
                    "instrument_runtimes",
                    "central_order_",
                    "v3_8_",
                    "v3_9_",
                )
            )
        )
    )


def _normalized_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def verify_layout(
    root: str | Path,
    *,
    expected_version: str = "0.3.9",
    expected_channel: str = "stable",
    minimum_risk_state_schema: int | None = None,
    allow_structural_fixture: bool = False,
) -> list[str]:
    if Path(root).is_symlink() or (Path(root).exists() and
            getattr(Path(root).lstat(), "st_file_attributes", 0) & 0x400):
        return ["Portable root is a symbolic link"]
    base = Path(root).resolve()
    errors: list[str] = []
    if not base.is_dir():
        return [f"Portable root does not exist: {base}"]
    for name in REQUIRED_DIRS:
        if not (base / name).is_dir():
            errors.append(f"Missing directory: {name}")
    if not (base / "MOEX Research Robot.bat").is_file():
        errors.append("Missing portable launcher: MOEX Research Robot.bat")
    structural_fixture = False
    manifest_path = base / "app" / "build_manifest.json"
    if not manifest_path.is_file():
        errors.append("Missing app/build_manifest.json")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"Invalid build manifest: {exc}")
        else:
            if not isinstance(manifest, dict):
                errors.append("Build manifest root is not an object")
            else:
                declares_structural_fixture = (
                    manifest.get("artifact_kind") == "STRUCTURAL_LAYOUT_FIXTURE"
                )
                if declares_structural_fixture and not allow_structural_fixture:
                    errors.append("Structural fixture requires explicit opt-in")
                structural_fixture = (
                    allow_structural_fixture and declares_structural_fixture
                )
                if manifest.get("software_version") != expected_version:
                    errors.append(
                        "Build manifest version is not " + str(expected_version)
                    )
                if manifest.get("release_channel") != expected_channel:
                    errors.append(
                        "Build manifest channel is not " + str(expected_channel)
                    )
                if manifest.get("sandbox_only") is not True:
                    errors.append("Build manifest does not enforce sandbox_only=true")
                if manifest.get("real_account_execution") is not False:
                    errors.append(
                        "Build manifest does not enforce real_account_execution=false"
                    )
                if minimum_risk_state_schema is not None:
                    try:
                        risk_state_schema = int(manifest.get("risk_state_schema"))
                    except (TypeError, ValueError):
                        risk_state_schema = -1
                    if risk_state_schema < int(minimum_risk_state_schema):
                        errors.append(
                            "Build manifest RiskState schema is below "
                            + str(minimum_risk_state_schema)
                        )
                if structural_fixture:
                    if manifest.get("executable_built") is not False:
                        errors.append(
                            "Structural fixture manifest must set executable_built=false"
                        )
                    if manifest.get("executable_launch_verified") is not False:
                        errors.append(
                            "Structural fixture manifest must set "
                            "executable_launch_verified=false"
                        )
    executable = base / "app" / "MOEXResearchRobot.exe"
    structural_marker = base / "app" / "EXECUTABLE_NOT_BUILT.txt"
    if structural_fixture:
        if executable.exists():
            errors.append("Structural fixture must not contain an executable")
        if not structural_marker.is_file():
            errors.append("Missing structural fixture marker")
        else:
            try:
                marker_text = _normalized_text(structural_marker)
            except (OSError, UnicodeError) as exc:
                errors.append(f"Invalid structural fixture marker: {exc}")
            else:
                if marker_text != STRUCTURAL_FIXTURE_MARKER:
                    errors.append("Structural fixture marker contract mismatch")
        launcher = base / "MOEX Research Robot.bat"
        try:
            launcher_text = _normalized_text(launcher)
        except (OSError, UnicodeError) as exc:
            errors.append(f"Invalid structural fixture launcher: {exc}")
        else:
            if launcher_text != STRUCTURAL_FIXTURE_LAUNCHER:
                errors.append("Structural fixture launcher must fail closed")
    elif not executable.is_file():
        errors.append("Missing executable: app/MOEXResearchRobot.exe")
    mutable_directories = ("runtime", "backups", "reports", "logs", "support")
    for directory_name in mutable_directories:
        directory = base / directory_name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if path.is_file():
                errors.append(
                    "Mutable package directory is not empty: "
                    + path.relative_to(base).as_posix()
                )
    inventory = []
    for path in base.rglob("*"):
        relative = path.relative_to(base)
        if (path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
            errors.append("Symbolic link or reparse point in package: " + relative.as_posix())
            continue
        inventory.append(relative.as_posix() + ("/" if path.is_dir() else ""))
        if has_financial_capture_path(relative.parts):
            errors.append("Private financial capture in package: " + relative.as_posix())
        if not path.is_file():
            continue
        try:
            if private_capture_file(path):
                errors.append("Private financial protocol document in package: " + relative.as_posix())
        except OSError:
            errors.append("Unreadable package file: " + relative.as_posix())
        if _private_runtime_name(path.name) or any(
            part.lower() in FORBIDDEN_RUNTIME_DIRECTORIES
            for part in relative.parts[:-1]
        ):
            errors.append(
                "Private runtime file leaked into package: " + relative.as_posix()
            )
    try:
        validate_member_inventory(inventory)
    except RuntimeError as exc:
        errors.append(str(exc))
    return errors


def layout_identity(root: str | Path) -> dict[str, object]:
    """Return a path-independent digest of a verified standalone layout."""

    base = Path(root).resolve()
    members: list[dict[str, object]] = []
    for path in sorted(
        (item for item in base.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(base).as_posix(),
    ):
        raw = path.read_bytes()
        members.append(
            {
                "name": path.relative_to(base).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": str(len(raw)),
            }
        )
    canonical = json.dumps(
        members,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return {
        "member_count": len(members),
        "members_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a Sandbox-only portable layout."
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--expected-version", default="0.3.9")
    parser.add_argument("--expected-channel", default="stable")
    parser.add_argument("--minimum-risk-state-schema", type=int)
    parser.add_argument("--allow-structural-fixture", action="store_true")
    args = parser.parse_args(argv)
    errors = verify_layout(
        args.root,
        expected_version=args.expected_version,
        expected_channel=args.expected_channel,
        minimum_risk_state_schema=args.minimum_risk_state_schema,
        allow_structural_fixture=args.allow_structural_fixture,
    )
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return 1
    print("Standalone layout: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
