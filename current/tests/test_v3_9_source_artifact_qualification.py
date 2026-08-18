from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from tools.build_release import build_zip
from tools.v3_9_source_artifact_qualification import (
    ARCHIVE_ROOT,
    inspect_archive,
    verify_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def artifact_pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    output = tmp_path_factory.mktemp("v3-9-source-artifacts")
    first = output / "candidate-a.zip"
    second = output / "candidate-b.zip"
    build_zip(ROOT, first, ARCHIVE_ROOT)
    build_zip(ROOT, second, ARCHIVE_ROOT)
    return first, second


def test_valid_source_artifacts_are_byte_identical_and_sandbox_only(
    artifact_pair: tuple[Path, Path],
) -> None:
    absent_canary = "ABSENT" + "-" + "CANARY"
    report = verify_artifacts(*artifact_pair, secret_canaries=[absent_canary])

    assert report["status"] == "PASS", report["failures"]
    assert report["byte_identical"] is True
    assert report["broker_calls_performed"] is False
    assert report["runtime_writes_performed"] is False
    assert report["provider_post_performed"] is False
    assert report["artifacts"][0]["member_count"] > 200


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
    artifact_pair: tuple[Path, Path],
) -> None:
    report = inspect_archive(artifact_pair[0])

    assert report["failures"] == []
    assert "build_manifest.json" in report["member_hashes"]
    assert "ZIP_CONTENTS.txt" in report["member_hashes"]
    assert "tools/v3_9_source_artifact_qualification.py" in report["member_hashes"]
