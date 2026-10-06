from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tools.build_release import (
    build_zip,
    collect_release_files,
    verify_deterministic_pair,
    zip_identity,
)
from tools.release_cleanup import PRIVATE_RUNTIME_DIRECTORIES, RUNTIME_NAMES
from tools.v3_9_source_artifact_qualification import (
    ARCHIVE_ROOT,
    inspect_archive,
    verify_artifacts,
)
from trading_robot import __version__

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def artifact_pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    output = tmp_path_factory.mktemp("v3-9-source-artifacts")
    first = output / "candidate-a.zip"
    second = output / "candidate-b.zip"
    build_zip(ROOT, first, ARCHIVE_ROOT)
    build_zip(ROOT, second, ARCHIVE_ROOT)
    return first, second


@pytest.fixture(scope="module")
def current_artifact_pair(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path, dict]:
    output = tmp_path_factory.mktemp("v3-10-source-artifacts")
    # Audit hooks stay in a disposable child. Only artifact-output writes are
    # permitted; provider POST, network, database and subprocess access fail closed.
    script = r'''
import json, os, sys
from pathlib import Path
root, output = (Path(value).resolve() for value in sys.argv[1:])
sys.path.insert(0, str(root))
from tools.build_release import build_zip
from trading_robot.tbank_sandbox import TBankSandboxClient
observed = {"provider_post_attempts": 0, "network_attempts": 0,
            "runtime_write_attempts": 0, "database_attempts": 0,
            "subprocess_attempts": 0}
def no_post(*args, **kwargs):
    observed["provider_post_attempts"] += 1
    raise AssertionError("Current artifact qualification forbids provider POST")
TBankSandboxClient._post = no_post
def output_only(path):
    if isinstance(path, int):
        return  # fdopen wraps the already checked mkstemp output descriptor.
    if not Path(os.fsdecode(path)).resolve().is_relative_to(output):
        observed["runtime_write_attempts"] += 1
        raise AssertionError("Current artifact qualification forbids non-artifact writes")
def policy(event, args):
    if event == "open":
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(char in mode for char in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        ):
            output_only(args[0])
    elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate", "os.utime"}:
        output_only(args[0])
    elif event in {"os.rename", "os.link", "os.symlink"}:
        output_only(args[0]); output_only(args[1])
    elif event in {"socket.connect", "socket.getaddrinfo"}:
        observed["network_attempts"] += 1
        raise AssertionError("Current artifact qualification forbids network")
    elif event == "sqlite3.connect":
        observed["database_attempts"] += 1
        raise AssertionError("Current artifact qualification forbids database access")
    elif event in {"subprocess.Popen", "os.system"}:
        observed["subprocess_attempts"] += 1
        raise AssertionError("Current artifact qualification forbids subprocesses")
sys.addaudithook(policy)
for name in ("candidate-a.zip", "candidate-b.zip"):
    build_zip(root, output / name, "moex_trading_robot_research_v3_10_0",
              secret_canaries=("ABSENT" + "-" + "CANARY",))
print(json.dumps(observed))
'''
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(ROOT), str(output)],
        check=True, capture_output=True, text=True, timeout=120,
    )
    return output / "candidate-a.zip", output / "candidate-b.zip", json.loads(completed.stdout)


def test_valid_source_artifacts_are_byte_identical_and_sandbox_only(
    current_artifact_pair: tuple[Path, Path, dict],
) -> None:
    first, second, observed = current_artifact_pair
    assert first != second
    report = verify_deterministic_pair(first, second)
    assert report["status"] == "PASS"
    assert report["first"] == report["second"]
    assert report["first"]["sha256"] == report["second"]["sha256"]
    assert observed == {
        "provider_post_attempts": 0, "network_attempts": 0,
        "runtime_write_attempts": 0, "database_attempts": 0, "subprocess_attempts": 0,
    }
    assert {path.name for path in first.parent.iterdir()} == {first.name, second.name}
    archive_root = "moex_trading_robot_research_v3_10_0"
    expected_members = [
        f"{archive_root}/{path.relative_to(ROOT).as_posix()}"
        for path in collect_release_files(ROOT)
    ] + [f"{archive_root}/ZIP_CONTENTS.txt"]
    assert len(expected_members) > 200
    absent_canary = "ABSENT" + "-" + "CANARY"
    for path, identity in ((first, report["first"]), (second, report["second"])):
        assert identity["members"] == expected_members
        with zipfile.ZipFile(path) as archive:
            for name in expected_members[:-1]:
                relative = Path(name).relative_to(archive_root)
                assert archive.read(name) == (ROOT / relative).read_bytes()
                assert not any(part.casefold() in PRIVATE_RUNTIME_DIRECTORIES for part in relative.parts[:-1])
                assert relative.name.casefold() not in {value.casefold() for value in RUNTIME_NAMES}
                assert not relative.name.casefold().endswith((".pyc", ".pyo", ".lock", "-wal", "-shm"))
                assert absent_canary.encode("utf-8") not in archive.read(name)
            manifest = json.loads(archive.read(f"{archive_root}/build_manifest.json"))
        assert manifest["software_version"] == __version__ == "0.3.10"
        assert manifest["display_version"] == "v3.10.0"
        assert manifest["release_channel"] == "stable"
        assert manifest["sandbox_only"] is True
        assert manifest["real_account_execution"] is False


def test_artifact_verifier_rejects_path_traversal(
    artifact_pair: tuple[Path, Path], tmp_path: Path
) -> None:
    unsafe = tmp_path / "unsafe.zip"
    shutil.copy2(artifact_pair[0], unsafe)
    with zipfile.ZipFile(unsafe, mode="a") as archive:
        archive.writestr(f"{ARCHIVE_ROOT}/../private.txt", "unsafe")

    report = inspect_archive(unsafe)

    assert any("unsafe or unexpected member path" in item for item in report["failures"])


def test_artifact_verifier_rejects_known_secret_canary(
    artifact_pair: tuple[Path, Path],
) -> None:
    report = verify_artifacts(
        *artifact_pair,
        secret_canaries=["MOEX Research Robot v3.9.0 Stable Candidate"],
    )

    assert report["status"] == "FAIL"
    assert any("known secret canary" in item for item in report["failures"])


def test_artifact_manifest_and_zip_contents_are_covered(
    tmp_path: Path,
) -> None:
    # The v3.9 inspector remains frozen for historical artifacts. This assertion
    # covers the current release, including every sanitized source member.
    archive_root = "moex_trading_robot_research_v3_10_0"
    archive_path = tmp_path / "v3-10-source.zip"
    build_zip(ROOT, archive_path, archive_root)
    identity = zip_identity(archive_path)
    source_files = collect_release_files(ROOT)
    expected_hashes = {
        f"{archive_root}/{path.relative_to(ROOT).as_posix()}":
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in source_files
    }
    contents_member = f"{archive_root}/ZIP_CONTENTS.txt"
    expected_members = [*sorted(expected_hashes), contents_member]
    expected_hashes[contents_member] = hashlib.sha256(
        ("\n".join(expected_members) + "\n").encode("utf-8")
    ).hexdigest()
    assert identity["members"] == expected_members
    assert identity["member_sha256"] == expected_hashes

    required = {
        "build_manifest.json", "ZIP_CONTENTS.txt", "BUILD_RELEASE.bat",
        "BUILD_STANDALONE.bat", "README.md", "trading_robot/__init__.py",
        "RELEASE_MANIFEST_V3_10_0_STABLE.txt",
        "V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md", "VERIFY_V3_10_0_STABLE.bat",
        "tools/build_release.py", "tools/release_cleanup.py",
        "tools/verify_standalone_layout.py", "tools/v3_10_stable_qualification.py",
        "tools/v3_9_source_artifact_qualification.py",
        "tools/v3_9_stable_preflight.py", "tests/test_stable_release_v3_9.py",
        "tests/test_v3_9_source_artifact_qualification.py",
        "tests/test_v3_9_stable_preflight.py",
    }
    assert {f"{archive_root}/{name}" for name in required} <= set(identity["members"])
    assert not any(
        f"{archive_root}/{name}" in identity["members"]
        for name in (
            "RELEASE_MANIFEST_V3_9_0_STABLE.txt",
            "V3_9_0_STABLE_RECOVERY_RUNBOOK_RU.md", "VERIFY_V3_9_0_STABLE.bat",
        )
    )
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read(f"{archive_root}/build_manifest.json"))
    assert manifest["software_version"] == __version__ == "0.3.10"
    assert manifest["display_version"] == "v3.10.0"
    assert manifest["release_channel"] == "stable"
    assert manifest["sandbox_only"] is True
    assert manifest["real_account_execution"] is False
    assert manifest["risk_state_schema"] == 4
    assert manifest["implementation_baseline_commit"] == (
        "7a569eadfb2a99c5314ae43d24da0dee47819d6c"
    )
    qualification = manifest["stable_qualification"]
    assert qualification["scope"] == "v3.10.0-release-cut-candidate"
    assert qualification["status"] == "candidate"
    for key in (
        "automated_source_preflight_complete", "deterministic_source_artifacts_complete",
        "user_acceptance", "final_burn_in_complete",
    ):
        assert qualification[key] is False
