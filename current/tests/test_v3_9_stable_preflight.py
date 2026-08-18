from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.v3_9_stable_preflight import (
    EXPECTED_IMPLEMENTATION_COMMIT,
    MANUAL_GATES,
    _output_path,
    review_manifest,
    review_source,
)

ROOT = Path(__file__).resolve().parents[1]


def manifest() -> dict:
    return json.loads((ROOT / "build_manifest.json").read_text(encoding="utf-8"))


def test_repository_source_preflight_passes_without_claiming_manual_gates() -> None:
    report = review_source(ROOT)

    assert report["status"] == "PASS", report["failures"]
    assert report["implementation_baseline_commit"] == EXPECTED_IMPLEMENTATION_COMMIT
    assert report["source_writes_performed"] is False
    assert report["broker_calls_performed"] is False
    assert report["provider_post_performed"] is False
    assert report["manual_gates"] == {name: "PENDING" for name in MANUAL_GATES}


def test_manifest_review_rejects_premature_acceptance_and_real_execution() -> None:
    candidate = copy.deepcopy(manifest())
    candidate["real_account_execution"] = True
    candidate["stable_qualification"]["user_acceptance"] = True

    failures = review_manifest(candidate)

    assert "build_manifest.real_account_execution must be false" in failures
    assert "stable_qualification.user_acceptance must remain false" in failures


def test_preflight_output_boundary_allows_only_excluded_verification_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()

    allowed = _output_path(source, source / "verification_output" / "report.json")
    assert allowed == (source / "verification_output" / "report.json").resolve()
    with pytest.raises(ValueError, match="verification_output"):
        _output_path(source, source / "build_manifest.json")
    assert _output_path(source, tmp_path / "external.json") == (
        tmp_path / "external.json"
    ).resolve()
