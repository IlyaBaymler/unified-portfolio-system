"""Verify a pair of deterministic v3.9.0 source release archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from .release_cleanup import RUNTIME_NAMES
except ImportError:  # direct script execution
    from release_cleanup import RUNTIME_NAMES


ARCHIVE_ROOT = "moex_trading_robot_research_v3_9_0"
FIXED_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
EXPECTED_MODE = 0o644
FORBIDDEN_DIRECTORY_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "backups",
    "build",
    "dist",
    "logs",
    "reports",
    "runtime",
    "support",
    "verification_output",
}
REQUIRED_RELATIVE_MEMBERS = {
    "BUILD_RELEASE.bat",
    "BUILD_STANDALONE.bat",
    "README.md",
    "RELEASE_MANIFEST_V3_9_0_STABLE.txt",
    "V3_9_0_STABLE_RECOVERY_RUNBOOK_RU.md",
    "VERIFY_V3_9_0_STABLE.bat",
    "build_manifest.json",
    "tests/test_stable_release_v3_9.py",
    "tests/test_v3_9_source_artifact_qualification.py",
    "tests/test_v3_9_stable_preflight.py",
    "tools/build_release.py",
    "tools/release_cleanup.py",
    "tools/v3_9_source_artifact_qualification.py",
    "tools/v3_9_stable_preflight.py",
    "tools/verify_standalone_layout.py",
    "trading_robot/__init__.py",
    "ZIP_CONTENTS.txt",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _archive_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _forbidden_member(relative: PurePosixPath) -> bool:
    lowered_parts = tuple(part.lower() for part in relative.parts)
    name = relative.name.lower()
    return (
        any(part in FORBIDDEN_DIRECTORY_NAMES for part in lowered_parts[:-1])
        or name in {item.lower() for item in RUNTIME_NAMES}
        or name in {".env", "thumbs.db", ".ds_store"}
        or name.endswith((".pyc", ".pyo", ".lock", "-wal", "-shm"))
        or ".pre_restore_" in name
    )


def _manifest_failures(document: Any) -> list[str]:
    if not isinstance(document, dict):
        return ["build_manifest.json root is not an object"]
    failures: list[str] = []
    expected = {
        "software_version": "0.3.9",
        "display_version": "v3.9.0",
        "release_channel": "stable",
        "sandbox_only": True,
        "real_account_execution": False,
        "risk_state_schema": 4,
        "implementation_baseline_commit": (
            "cddd80f3caf7191ecf2f85df9e1ccfea97af6cc4"
        ),
    }
    for key, value in expected.items():
        if document.get(key) != value:
            failures.append(f"build_manifest.{key} does not match {value!r}")
    qualification = document.get("stable_qualification")
    if not isinstance(qualification, dict):
        failures.append("stable_qualification is not an object")
        return failures
    if qualification.get("status") != "candidate":
        failures.append("stable_qualification.status is not candidate")
    if qualification.get("automated_source_preflight_complete") is not True:
        failures.append("automated source preflight is not complete")
    if qualification.get("deterministic_source_artifacts_complete") is not True:
        failures.append("deterministic source artifacts are not complete")
    for key in ("user_acceptance", "final_burn_in_complete"):
        if qualification.get(key) is not False:
            failures.append(f"stable_qualification.{key} must remain false")
    return failures


def inspect_archive(
    path: Path,
    *,
    secret_canaries: Iterable[str] = (),
) -> dict[str, Any]:
    archive_path = path.resolve()
    failures: list[str] = []
    member_hashes: dict[str, str] = {}
    canaries = [item.encode("utf-8") for item in secret_canaries if item]
    canary_findings: list[str] = []

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                failures.append("duplicate ZIP member names")
            manifest_name = f"{ARCHIVE_ROOT}/ZIP_CONTENTS.txt"
            if not names or names[-1] != manifest_name:
                failures.append("ZIP_CONTENTS.txt is not the final member")
            if names[:-1] != sorted(names[:-1]):
                failures.append("source members are not deterministically sorted")

            relative_names: set[str] = set()
            for info in infos:
                name = info.filename
                pure = PurePosixPath(name)
                if (
                    name.startswith("/")
                    or "\\" in name
                    or not pure.parts
                    or pure.parts[0] != ARCHIVE_ROOT
                    or any(part in {"", ".", ".."} for part in pure.parts)
                ):
                    failures.append(f"unsafe or unexpected member path: {name}")
                    continue
                relative = PurePosixPath(*pure.parts[1:])
                relative_names.add(relative.as_posix())
                if _forbidden_member(relative):
                    failures.append(f"forbidden private/runtime member: {name}")
                if info.date_time != FIXED_TIMESTAMP:
                    failures.append(f"non-deterministic timestamp: {name}")
                mode = (info.external_attr >> 16) & 0o777
                if info.create_system != 3 or mode != EXPECTED_MODE:
                    failures.append(f"non-deterministic file mode: {name}")
                data = archive.read(info)
                member_hashes[relative.as_posix()] = _sha256(data)
                if any(canary in data for canary in canaries):
                    canary_findings.append(relative.as_posix())

            missing = sorted(REQUIRED_RELATIVE_MEMBERS - relative_names)
            failures.extend(f"required member missing: {name}" for name in missing)

            if manifest_name in names:
                expected_contents = "\n".join(names) + "\n"
                actual_contents = archive.read(manifest_name).decode("utf-8")
                if actual_contents != expected_contents:
                    failures.append("ZIP_CONTENTS.txt does not exactly list members")

            build_manifest_name = f"{ARCHIVE_ROOT}/build_manifest.json"
            if build_manifest_name in names:
                try:
                    build_manifest = json.loads(
                        archive.read(build_manifest_name).decode("utf-8")
                    )
                except (UnicodeError, json.JSONDecodeError) as exc:
                    failures.append(f"build_manifest.json is invalid: {exc}")
                else:
                    failures.extend(_manifest_failures(build_manifest))
    except (OSError, zipfile.BadZipFile) as exc:
        failures.append(f"cannot read ZIP: {exc}")

    if canary_findings:
        failures.extend(
            f"known secret canary found in member: {name}"
            for name in sorted(set(canary_findings))
        )

    return {
        "artifact": archive_path.name,
        "sha256": _archive_sha256(archive_path) if archive_path.is_file() else None,
        "size_bytes": archive_path.stat().st_size if archive_path.is_file() else None,
        "member_count": len(member_hashes),
        "member_hashes": member_hashes,
        "known_secret_canaries_checked": len(canaries),
        "known_secret_canary_findings": sorted(set(canary_findings)),
        "failures": failures,
    }


def verify_artifacts(
    first: Path,
    second: Path,
    *,
    secret_canaries: Iterable[str] = (),
) -> dict[str, Any]:
    first_report = inspect_archive(first, secret_canaries=secret_canaries)
    second_report = inspect_archive(second, secret_canaries=secret_canaries)
    failures = [
        *(f"{first_report['artifact']}: {item}" for item in first_report["failures"]),
        *(f"{second_report['artifact']}: {item}" for item in second_report["failures"]),
    ]
    byte_identical = (
        first_report["sha256"] is not None
        and first_report["sha256"] == second_report["sha256"]
    )
    if not byte_identical:
        failures.append("source ZIP pair is not byte-identical")
    if first_report["member_hashes"] != second_report["member_hashes"]:
        failures.append("source ZIP member hashes differ")

    return {
        "schema_version": 1,
        "qualification": "v3.9.0-m6-deterministic-source-artifacts",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "archive_root": ARCHIVE_ROOT,
        "byte_identical": byte_identical,
        "artifacts": [first_report, second_report],
        "broker_calls_performed": False,
        "runtime_writes_performed": False,
        "provider_post_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify two deterministic v3.9.0 source ZIP artifacts."
    )
    parser.add_argument("--first", required=True, type=Path)
    parser.add_argument("--second", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--secret-canary", action="append", default=[])
    args = parser.parse_args(argv)

    report = verify_artifacts(
        args.first,
        args.second,
        secret_canaries=args.secret_canary,
    )
    if args.output is not None:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
