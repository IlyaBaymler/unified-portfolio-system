from __future__ import annotations

"""Offline-only CL8 qualification evidence and release-custody verifier.

This module deliberately has no provider client imports.  It validates already
produced, privacy-safe evidence and deterministic artifact identities; live
experiments remain separate operator-governed actions.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

ACCEPTED_CONTRACT_COMMIT = "a315b9b919a075966a3be898420d37be8ab7b65d"
ACCEPTED_CONTRACT_TREE = "035f0a4a824001ee2c794d92bb500e206a0123f0"
ACCEPTED_CONTRACT_SHA256 = (
    "d72b693a88c8d6c2b5f68ca9c25865aa0bf77ff82f315e3b199f2839b0eb5eee"
)
ACCEPTED_CONTRACT_RESCOPE_COMMIT = "a2ee377f4180bcc0f7ced080233f3bc1c5a73792"
ACCEPTED_CONTRACT_RESCOPE_TREE = "8d80c956fce3ab3ce8b3a6fb6ff57ffbd35e3366"
ACCEPTED_CONTRACT_RESCOPE_SHA256 = (
    "2d09cc474a433bae380b128203007a9740d315f5b147aa2405ee9737aa319072"
)
CL7_PREDECESSOR_COMMIT = "ef2eba758bbffb587dbe237a96372b2273fdee03"
CL7_PREDECESSOR_TREE = "f55788ff362a24d368b0d01dc0d68e85d8183199"
V39_ORACLE_COMMIT = "412e126166831a8bac0435d61932ab328fc61f12"
V39_ORACLE_TREE = "18c2822d2aa7f174a99d7d7dc5f5a5851dc59eba"
RELEASE_VERSION = "v3.10.0"

QUALIFICATION_IMPLEMENTATION_ALLOWLIST = frozenset(
    {
        "current/trading_robot/runtime_backup.py",
        "current/trading_robot/runtime_integrity.py",
        "current/trading_robot/support_bundle.py",
        "current/trading_robot/readiness.py",
        "current/trading_robot/runtime_bootstrap.py",
        "current/rc_tool.py",
        "current/runtime_tool.py",
        "current/tools/build_release.py",
        "current/tools/release_cleanup.py",
        "current/tools/verify_standalone_layout.py",
        "current/tools/v3_10_stable_qualification.py",
        "current/tests/test_v3_10_stable_qualification.py",
        "current/tests/fixtures/v3_10_stable_qualification_vectors.json",
        ".github/workflows/ci.yml",
    }
)

RELEASE_CUT_BASE_ALLOWLIST = frozenset(
    {
        "current/trading_robot/__init__.py",
        "current/trading_robot/tbank_sandbox.py",
        "current/desktop_gui.py",
        "current/build_manifest.json",
        "current/MOEXResearchRobot.spec",
        "current/BUILD_RELEASE.bat",
        "current/BUILD_STANDALONE.bat",
        "current/portable_launcher.bat",
        "current/initialize_runtime.bat",
        "current/install_and_run_gui.bat",
        "current/run_gui.bat",
        "current/run_risk_lab.bat",
        "current/run_risk_report.bat",
        "current/restore_stable_default_risk_profile.bat",
        "current/README.md",
        "current/START_HERE_WINDOWS.md",
        "current/CHANGELOG_V3_10_0_STABLE_RU.md",
        "current/RELEASE_MANIFEST_V3_10_0_STABLE.txt",
        "current/UPDATE_TO_V3_10_0_STABLE.md",
        "current/V3_10_0_STABLE_ARCHITECTURE_RU.md",
        "current/V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md",
        "current/V3_10_0_STABLE_TEST_PLAN_RU.md",
        "current/VERIFY_V3_10_0_STABLE.bat",
        "current/install_and_verify_v3_10_0.bat",
        "docs/releases/V3_10_0_STABLE_QUALIFICATION_RU.md",
        "current/CHANGELOG_V3_9_0_STABLE_RU.md",
        "current/MASTER_UPDATE_2026-08-15_V3_9_0_STABLE_RU.md",
        "current/RELEASE_MANIFEST_V3_9_0_STABLE.txt",
        "current/UPDATE_TO_V3_9_0_STABLE.md",
        "current/V3_9_0_STABLE_ARCHITECTURE_RU.md",
        "current/V3_9_0_STABLE_RECOVERY_RUNBOOK_RU.md",
        "current/V3_9_0_STABLE_TEST_PLAN_RU.md",
        "current/VERIFY_V3_9_0_STABLE.bat",
        "current/install_and_verify_v3_9_0.bat",
    }
)

RELEASE_REVIEW_RESCOPE_ADDITIONS = frozenset(
    {
        "docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md",
        "current/tools/release_cleanup.py",
        "current/tools/build_release.py",
        "current/tools/v3_10_stable_qualification.py",
        "current/tests/test_release_hygiene.py",
        "current/tests/test_v3_10_stable_qualification.py",
        "current/tests/test_v3_10_issue72_gui_runtime.py",
    }
)

RELEASE_REVIEW_CORRECTION_ALLOWLIST = frozenset(
    {
        *RELEASE_REVIEW_RESCOPE_ADDITIONS,
        "current/install_and_run_gui.bat",
        "current/BUILD_STANDALONE.bat",
        "current/build_manifest.json",
        "current/VERIFY_V3_10_0_STABLE.bat",
        "docs/releases/V3_10_0_STABLE_QUALIFICATION_RU.md",
    }
)

RELEASE_CUT_ALLOWLIST = frozenset(
    {*RELEASE_CUT_BASE_ALLOWLIST, *RELEASE_REVIEW_RESCOPE_ADDITIONS}
)

PHASE_KEYS = (
    "GUI_RUNTIME_PREREQUISITE",
    "REGRESSION",
    "CORRUPTION_RECOVERY",
    "INSTALL_UPGRADE_ROLLBACK",
    "STANDALONE",
    "ARTIFACTS",
    "PRIVACY",
    "CONTROLLED_CLOCK",
    "SANDBOX_BURNIN",
)

INHERITED_CUSTODY_FAILURES = frozenset(
    {
        (
            "tests/test_v3_10_cash_ledger_persistence.py::"
            "test_v310_cl2_28_three_path_delta_and_immutable_predecessor_files"
        ),
        (
            "tests/test_v3_10_broker_read_adapters.py::"
            "test_v310_cl3_17_exact_three_path_delta"
        ),
        (
            "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
            "test_exact_implementation_allowlist"
        ),
        (
            "tests/test_v3_10_cash_ledger_opening_reconciliation.py::"
            "test_v310_cl4_20_three_path_delta_and_predecessor_custody"
        ),
        (
            "tests/test_v3_10_cash_availability.py::"
            "test_exact_successor_custody_and_three_path_delta"
        ),
        (
            "tests/test_v3_10_reporting_risk_cash_context.py::"
            "test_v310_cl6_01_exact_contract_lineage_and_three_path_delta"
        ),
    }
)

SUPERSEDED_RELEASE_METADATA_FAILURES = frozenset(
    {
        (
            "tests/test_stable_release_v3_9.py::"
            "test_stable_candidate_version_manifest_and_exact_baseline_are_consistent"
        ),
        (
            "tests/test_stable_release_v3_9.py::"
            "test_only_v3_9_root_release_documents_are_current"
        ),
        (
            "tests/test_stable_release_v3_9.py::"
            "test_release_candidate_does_not_claim_manual_m6_acceptance"
        ),
        (
            "tests/test_v3_9_source_artifact_qualification.py::"
            "test_valid_source_artifacts_are_byte_identical_and_sandbox_only"
        ),
        (
            "tests/test_v3_9_source_artifact_qualification.py::"
            "test_artifact_manifest_and_zip_contents_are_covered"
        ),
        (
            "tests/test_v3_9_stable_preflight.py::"
            "test_repository_source_preflight_passes_without_claiming_manual_gates"
        ),
        (
            "tests/test_standalone_rc1.py::"
            "test_standalone_sources_are_present_and_use_portable_environment"
        ),
        (
            "tests/test_observability.py::"
            "test_cycle_has_timing_session_and_decision_is_not_an_order"
        ),
    }
)

OFFLINE_CASE_IDS = tuple(f"V310-CL8-{number:03d}" for number in range(1, 47))


def _test_nodes(*names: str) -> tuple[str, ...]:
    return tuple(names)


_CL8_TEST = "tests/test_v3_10_stable_qualification.py::"
_CL7_TEST = "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
OFFLINE_CASE_NODE_IDS: dict[str, tuple[str, ...]] = {
    "V310-CL8-001": _test_nodes(
        _CL8_TEST + "test_v310_cl8_001_exact_contract_and_allowlists"
    ),
    "V310-CL8-002": _test_nodes(
        _CL8_TEST + "test_v310_cl8_002_v39_and_cl7_oracles_are_exact"
    ),
    "V310-CL8-003": _test_nodes(
        _CL8_TEST + "test_v310_cl8_003_004_regression_failure_sets_are_exact"
    ),
    "V310-CL8-004": _test_nodes(
        _CL8_TEST + "test_v310_cl8_003_004_regression_failure_sets_are_exact"
    ),
    "V310-CL8-005": _test_nodes(
        _CL8_TEST + "test_v310_cl8_005_038_qualification_and_release_path_gates"
    ),
    "V310-CL8-006": _test_nodes(
        _CL8_TEST
        + "test_v310_cl8_006_008_009_backup_includes_cl2_cl7_and_isolated_restore"
    ),
    "V310-CL8-007": _test_nodes(
        _CL8_TEST
        + "test_v310_cl8_006_008_009_backup_includes_cl2_cl7_and_isolated_restore"
    ),
    "V310-CL8-008": _test_nodes(
        _CL8_TEST + "test_v310_cl8_008_backup_detects_single_byte_corruption"
    ),
    "V310-CL8-009": _test_nodes(
        _CL8_TEST
        + "test_v310_cl8_006_008_009_backup_includes_cl2_cl7_and_isolated_restore"
    ),
    "V310-CL8-010": _test_nodes(
        _CL8_TEST + "test_v310_cl8_q23_cashledger_complete_matrix"
    ),
    "V310-CL8-011": _test_nodes(
        "tests/test_v3_10_cash_ledger_persistence.py::test_v310_cl2_18_committed_wal_recovery_and_corrupt_sidecar_refusal"
    ),
    "V310-CL8-012": _test_nodes(
        _CL8_TEST + "test_v310_cl8_q23_runtime_authority_complete_matrix",
        _CL7_TEST + "test_cas_and_checksum_fail_closed",
        _CL7_TEST + "test_corrupt_active_never_restores_lastgood",
        _CL7_TEST + "test_authority_commit_crash_outcome_at_each_replace_boundary",
    ),
    "V310-CL8-013": _test_nodes(
        "tests/test_central_order_manager_v3_8.py::test_store_fails_closed_on_checksum_mismatch",
        "tests/test_central_order_manager_v3_8.py::test_persisted_identity_tampering_is_rejected_even_with_new_checksum",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::test_central_rejects_noncanonical_locked_proof_text",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::test_d3_invalid_hmac_cannot_mutate_central",
    ),
    "V310-CL8-014": _test_nodes(
        "tests/test_portfolio_repository_v3_7.py::test_repository_rejects_checksum_mismatch",
        "tests/test_portfolio_repository_v3_7.py::test_repository_rejects_account_scope_change",
        "tests/test_portfolio_repository_v3_7.py::test_repository_corrupt_json_is_not_silently_reset",
    ),
    "V310-CL8-015": _test_nodes(
        "tests/test_risk.py::test_profile_store_separates_modes_and_checks_checksum",
        "tests/test_risk.py::test_corrupt_state_store_fails_closed",
        "tests/test_risk.py::test_stale_snapshot_is_absolute_block",
        "tests/test_v3_10_cash_ledger_opening_reconciliation.py::test_v310_cl4_12_projection_classifications_and_account_scope",
        "tests/test_v3_10_cash_availability.py::test_forged_proofs_and_cl4_binding_fail_closed",
        "tests/test_v3_10_reporting_risk_cash_context.py::test_v310_cl6_08_valuation_hmac_and_scope_mutations_fail",
    ),
    "V310-CL8-016": _test_nodes(
        _CL8_TEST + "test_v310_cl8_q23_c0_c6_point_labelled_replay",
        _CL7_TEST + "test_bootstrap_and_full_state_chain",
        _CL7_TEST + "test_authority_commit_crash_outcome_at_each_replace_boundary",
        _CL7_TEST
        + "test_full_prepare_confirm_activate_arm_uses_fresh_cl2_to_cl6_evidence",
    ),
    "V310-CL8-017": _test_nodes(
        _CL8_TEST + "test_v310_cl8_q23_d0_d10_point_labelled_replay",
        _CL7_TEST + "test_central_lease_persists_exact_proof_before_d3_recovery",
        _CL7_TEST
        + "test_exact_dispatch_marker_precedes_single_post_and_classifies_outcome",
        _CL7_TEST + "test_process_boundary_recovery_requires_exact_central_resolution",
        _CL7_TEST
        + "test_operator_recovery_validates_before_lookup_under_authority_lock",
    ),
    "V310-CL8-018": _test_nodes(
        _CL7_TEST + "test_pending_attempt_kat_and_no_rollback",
        _CL7_TEST + "test_post_once_timeout_never_retries",
        _CL7_TEST + "test_legacy_recovery_has_no_automatic_post_resubmit",
    ),
    "V310-CL8-019": _test_nodes(
        _CL8_TEST + "test_v310_cl8_q23_clean_install_exact_candidate_artifact"
    ),
    "V310-CL8-020": _test_nodes(
        _CL8_TEST + "test_v310_cl8_q23_v39_to_v310_upgrade_exact_oracles"
    ),
    "V310-CL8-021": _test_nodes(
        _CL8_TEST + "test_v310_cl8_q23_v39_to_v310_upgrade_exact_oracles"
    ),
    "V310-CL8-022": _test_nodes(
        _CL8_TEST + "test_v310_cl8_q23_full_runtime_backup_inventory"
    ),
    "V310-CL8-023": _test_nodes(
        _CL8_TEST + "test_v310_cl8_q23_rollback_package_and_attempt_boundaries",
        _CL7_TEST + "test_pending_attempt_kat_and_no_rollback",
    ),
    "V310-CL8-024": _test_nodes(
        _CL8_TEST
        + "test_v310_cl8_024_027_release_artifacts_are_deterministic_and_private"
    ),
    "V310-CL8-025": _test_nodes(
        _CL8_TEST + "test_v310_cl8_025_028_standalone_private_denylist_is_complete"
    ),
    "V310-CL8-026": _test_nodes(
        _CL8_TEST
        + "test_v310_cl8_024_027_release_artifacts_are_deterministic_and_private"
    ),
    "V310-CL8-027": _test_nodes(
        _CL8_TEST
        + "test_v310_cl8_024_027_release_artifacts_are_deterministic_and_private"
    ),
    "V310-CL8-028": _test_nodes(
        _CL8_TEST + "test_v310_cl8_025_028_standalone_private_denylist_is_complete"
    ),
    "V310-CL8-029": _test_nodes(
        _CL8_TEST
        + "test_v310_cl8_029_030_support_bundle_reports_only_sanitized_cash_custody"
    ),
    "V310-CL8-030": _test_nodes(
        _CL8_TEST
        + "test_v310_cl8_029_030_support_bundle_reports_only_sanitized_cash_custody"
    ),
    "V310-CL8-031": _test_nodes(
        _CL8_TEST + "test_v310_cl8_031_036_privacy_scans_and_no_provider_boundary"
    ),
    "V310-CL8-032": _test_nodes(_CL8_TEST + "test_v310_cl8_032_033_contract_kats"),
    "V310-CL8-033": _test_nodes(
        _CL8_TEST + "test_v310_cl8_033_045_phase_bindings_prevent_substitution"
    ),
    "V310-CL8-034": _test_nodes(
        _CL8_TEST + "test_v310_cl8_034_035_controlled_clock_boundaries_are_executed"
    ),
    "V310-CL8-035": _test_nodes(
        _CL8_TEST + "test_v310_cl8_034_035_controlled_clock_boundaries_are_executed"
    ),
    "V310-CL8-036": _test_nodes(
        _CL8_TEST + "test_v310_cl8_031_036_privacy_scans_and_no_provider_boundary"
    ),
    "V310-CL8-037": _test_nodes(
        _CL8_TEST + "test_v310_cl8_037_no_production_host_or_mutation_route"
    ),
    "V310-CL8-038": _test_nodes(
        _CL8_TEST + "test_v310_cl8_005_038_qualification_and_release_path_gates"
    ),
    "V310-CL8-039": _test_nodes(
        _CL8_TEST + "test_exact_qualification_delta_and_predecessor_immutability"
    ),
    "V310-CL8-040": _test_nodes(
        _CL8_TEST + "test_v310_cl8_003_004_regression_failure_sets_are_exact"
    ),
    "V310-CL8-041": _test_nodes(_CL8_TEST + "test_v310_cl8_041_q0_is_hard_and_exact"),
    "V310-CL8-042": _test_nodes(
        _CL8_TEST + "test_v310_cl8_042_gui_runtime_matrix_is_closed"
    ),
    "V310-CL8-043": _test_nodes(
        _CL8_TEST + "test_v310_cl8_q23_multi_session_matrix_is_executed"
    ),
    "V310-CL8-044": _test_nodes(
        _CL8_TEST + "test_v310_cl8_044_account_disposition_is_exact"
    ),
    "V310-CL8-045": _test_nodes(
        _CL8_TEST + "test_v310_cl8_033_045_phase_bindings_prevent_substitution",
        _CL8_TEST + "test_v310_cl8_045_evidence_gap_is_exact_and_prerequisite_bound",
    ),
    "V310-CL8-046": _test_nodes(
        _CL8_TEST
        + "test_v310_cl8_046_missing_failure_never_compensates_for_new_failure"
    ),
}

QUALIFICATION_KAT_SHA256 = (
    "a16672bd9f825e73b75a51bcd4a7af93f0bafb239b63bb86d5a4d1bf96ded57d"
)
RELEASE_MANIFEST_KAT_SHA256 = (
    "8d3dda9aa4c747d69a83632758500ac4483ff1787f5ad959b4be520cbcace0fe"
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_RUN_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{9}Z"
)
_UINT_RE = re.compile(r"0|[1-9][0-9]*")
_PRIVATE_PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/](?:users|documents and settings)[\\/]|/(?:home|users)/)"
)
_AUTHORIZATION_RE = re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+")


class PhaseStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    PASS = "PASS"
    FAIL = "FAIL"
    INDETERMINATE = "INDETERMINATE"
    EVIDENCE_GAP = "EVIDENCE_GAP"


class RegressionStage(StrEnum):
    PRE_RELEASE_CUT = "PRE_RELEASE_CUT"
    POST_RELEASE_CUT = "POST_RELEASE_CUT"


class QualificationReason(StrEnum):
    TYPE_INVALID = "TYPE_INVALID"
    KEYSET_INVALID = "KEYSET_INVALID"
    CANONICAL_FORMAT_INVALID = "CANONICAL_FORMAT_INVALID"
    IDENTITY_INVALID = "IDENTITY_INVALID"
    PHASE_BINDING_INVALID = "PHASE_BINDING_INVALID"
    PHASE_SUMMARY_MISSING = "PHASE_SUMMARY_MISSING"
    PHASE_SUMMARY_MISMATCH = "PHASE_SUMMARY_MISMATCH"
    STATUS_INVALID = "STATUS_INVALID"
    PRIVACY_BOUNDARY_VIOLATION = "PRIVACY_BOUNDARY_VIOLATION"
    REGRESSION_SET_MISMATCH = "REGRESSION_SET_MISMATCH"
    ALLOWLIST_VIOLATION = "ALLOWLIST_VIOLATION"
    EVIDENCE_ALREADY_EXISTS = "EVIDENCE_ALREADY_EXISTS"
    EVIDENCE_INTEGRITY_INVALID = "EVIDENCE_INTEGRITY_INVALID"
    Q0_BLOCKED = "Q0_BLOCKED"
    GUI_RUNTIME_QUALIFICATION_FAILED = "GUI_RUNTIME_QUALIFICATION_FAILED"
    MULTI_SESSION_QUALIFICATION_FAILED = "MULTI_SESSION_QUALIFICATION_FAILED"
    ACCOUNT_DISPOSITION_INVALID = "ACCOUNT_DISPOSITION_INVALID"
    OFFLINE_MATRIX_INCOMPLETE = "OFFLINE_MATRIX_INCOMPLETE"


class QualificationError(RuntimeError):
    def __init__(self, reason: QualificationReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _fail(reason: QualificationReason) -> None:
    raise QualificationError(reason)


def _reject_float_or_surrogate(value: object) -> None:
    if isinstance(value, float):
        _fail(QualificationReason.CANONICAL_FORMAT_INVALID)
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            _fail(QualificationReason.CANONICAL_FORMAT_INVALID)
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if type(key) is not str:
                _fail(QualificationReason.CANONICAL_FORMAT_INVALID)
            _reject_float_or_surrogate(key)
            _reject_float_or_surrogate(nested)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _reject_float_or_surrogate(nested)
        return
    if value is not None and type(value) not in {bool, int}:
        _fail(QualificationReason.CANONICAL_FORMAT_INVALID)


def canonical_json_bytes(value: object) -> bytes:
    _reject_float_or_surrogate(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise QualificationError(QualificationReason.CANONICAL_FORMAT_INVALID) from exc
    return encoded


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(QualificationReason.CANONICAL_FORMAT_INVALID)
        result[key] = value
    return result


def parse_canonical_json(value: bytes | bytearray | memoryview) -> object:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        _fail(QualificationReason.TYPE_INVALID)
    raw = bytes(value)
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail(QualificationReason.CANONICAL_FORMAT_INVALID)
    try:
        text = raw.decode("ascii")
        parsed = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_float=lambda _value: _fail(
                QualificationReason.CANONICAL_FORMAT_INVALID
            ),
            parse_constant=lambda _value: _fail(
                QualificationReason.CANONICAL_FORMAT_INVALID
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationError(QualificationReason.CANONICAL_FORMAT_INVALID) from exc
    if canonical_json_bytes(parsed) != raw:
        _fail(QualificationReason.CANONICAL_FORMAT_INVALID)
    return parsed


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_exact_keys(value: Mapping[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        _fail(QualificationReason.KEYSET_INVALID)


def _require_sha256(value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(QualificationReason.IDENTITY_INVALID)
    return value


def _require_git_sha(value: object) -> str:
    if type(value) is not str or _GIT_SHA_RE.fullmatch(value) is None:
        _fail(QualificationReason.IDENTITY_INVALID)
    return value


def _require_run_id(value: object) -> str:
    if type(value) is not str or _RUN_ID_RE.fullmatch(value) is None:
        _fail(QualificationReason.IDENTITY_INVALID)
    return value


def _require_status(value: object) -> PhaseStatus:
    try:
        return PhaseStatus(value)
    except (TypeError, ValueError) as exc:
        raise QualificationError(QualificationReason.STATUS_INVALID) from exc


def _require_timestamp(value: object) -> str:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(QualificationReason.IDENTITY_INVALID)
    return value


def validate_phase_summary(
    summary_bytes: bytes,
    *,
    phase: str,
    candidate_commit: str,
    candidate_tree: str,
    contract_sha256: str,
) -> dict[str, Any]:
    parsed = parse_canonical_json(summary_bytes)
    if not isinstance(parsed, dict):
        _fail(QualificationReason.TYPE_INVALID)
    required = {
        "candidate_commit",
        "candidate_tree",
        "contract_sha256",
        "run_id",
        "status",
    }
    if not required.issubset(parsed):
        _fail(QualificationReason.KEYSET_INVALID)
    if (
        phase not in PHASE_KEYS
        or parsed["candidate_commit"] != candidate_commit
        or parsed["candidate_tree"] != candidate_tree
        or parsed["contract_sha256"] != contract_sha256
    ):
        _fail(QualificationReason.PHASE_SUMMARY_MISMATCH)
    _require_run_id(parsed["run_id"])
    status = _require_status(parsed["status"])
    gap_keys = {"evidence_gap_reason", "evidence_gap_prerequisites"}
    if status is PhaseStatus.EVIDENCE_GAP:
        if phase != "SANDBOX_BURNIN" or not gap_keys.issubset(parsed):
            _fail(QualificationReason.PHASE_BINDING_INVALID)
        prerequisites = parsed["evidence_gap_prerequisites"]
        if not isinstance(prerequisites, Mapping):
            _fail(QualificationReason.PHASE_BINDING_INVALID)
        _require_exact_keys(
            prerequisites,
            {
                "artificial_signal_created",
                "configured_instrument_count",
                "exact_live_lifecycle_count",
                "q7_otherwise_pass",
                "reviewer_acknowledged",
                "synthetic_multi_position_concurrency_status",
            },
        )
        if (
            parsed["evidence_gap_reason"] != "LIVE_OVERLAP_NOT_OBSERVED"
            or prerequisites["q7_otherwise_pass"] is not True
            or type(prerequisites["exact_live_lifecycle_count"]) is not int
            or prerequisites["exact_live_lifecycle_count"] < 1
            or prerequisites["configured_instrument_count"] not in {2, 3}
            or prerequisites["synthetic_multi_position_concurrency_status"] != "PASS"
            or prerequisites["artificial_signal_created"] is not False
            or prerequisites["reviewer_acknowledged"] is not True
        ):
            _fail(QualificationReason.PHASE_BINDING_INVALID)
    elif gap_keys & set(parsed):
        _fail(QualificationReason.PHASE_BINDING_INVALID)
    return parsed


def derive_overall_status(phase_statuses: Mapping[str, PhaseStatus]) -> PhaseStatus:
    if set(phase_statuses) != set(PHASE_KEYS):
        _fail(QualificationReason.PHASE_BINDING_INVALID)
    if phase_statuses["GUI_RUNTIME_PREREQUISITE"] is not PhaseStatus.PASS:
        return PhaseStatus.FAIL
    values = tuple(phase_statuses.values())
    if PhaseStatus.FAIL in values:
        return PhaseStatus.FAIL
    if PhaseStatus.INDETERMINATE in values:
        return PhaseStatus.INDETERMINATE
    if PhaseStatus.NOT_RUN in values:
        return PhaseStatus.NOT_RUN
    gaps = {
        key
        for key, value in phase_statuses.items()
        if value is PhaseStatus.EVIDENCE_GAP
    }
    if gaps:
        return (
            PhaseStatus.EVIDENCE_GAP if gaps == {"SANDBOX_BURNIN"} else PhaseStatus.FAIL
        )
    return PhaseStatus.PASS


def build_qualification_envelope(
    *,
    candidate_commit: str,
    candidate_tree: str,
    contract_sha256: str,
    generated_at: str,
    phase_summaries: Mapping[str, bytes],
) -> dict[str, Any]:
    candidate_commit = _require_git_sha(candidate_commit)
    candidate_tree = _require_git_sha(candidate_tree)
    contract_sha256 = _require_sha256(contract_sha256)
    generated_at = _require_timestamp(generated_at)
    if set(phase_summaries) != set(PHASE_KEYS):
        _fail(QualificationReason.PHASE_SUMMARY_MISSING)
    bindings: dict[str, dict[str, str]] = {}
    statuses: dict[str, PhaseStatus] = {}
    run_ids: set[str] = set()
    for phase in PHASE_KEYS:
        summary_bytes = phase_summaries[phase]
        summary = validate_phase_summary(
            summary_bytes,
            phase=phase,
            candidate_commit=candidate_commit,
            candidate_tree=candidate_tree,
            contract_sha256=contract_sha256,
        )
        run_id = _require_run_id(summary["run_id"])
        if run_id in run_ids:
            _fail(QualificationReason.PHASE_BINDING_INVALID)
        run_ids.add(run_id)
        status = _require_status(summary["status"])
        statuses[phase] = status
        bindings[phase] = {
            "canonical_summary_sha256": sha256_hex(summary_bytes),
            "run_id": run_id,
            "status": status.value,
        }
    return {
        "candidate_commit": candidate_commit,
        "candidate_tree": candidate_tree,
        "contract_sha256": contract_sha256,
        "domain": "v3.10-cl8-qualification-evidence",
        "generated_at": generated_at,
        "oracle_v39_commit": V39_ORACLE_COMMIT,
        "oracle_v39_tree": V39_ORACLE_TREE,
        "overall_status": derive_overall_status(statuses).value,
        "phase_results": bindings,
        "release_version": RELEASE_VERSION,
        "version": 1,
    }


def validate_qualification_envelope(
    envelope_bytes: bytes,
    *,
    phase_summaries: Mapping[str, bytes],
    expected_candidate_commit: str,
    expected_candidate_tree: str,
    expected_contract_sha256: str,
) -> dict[str, Any]:
    parsed = parse_canonical_json(envelope_bytes)
    if not isinstance(parsed, dict):
        _fail(QualificationReason.TYPE_INVALID)
    _require_exact_keys(
        parsed,
        {
            "candidate_commit",
            "candidate_tree",
            "contract_sha256",
            "domain",
            "generated_at",
            "oracle_v39_commit",
            "oracle_v39_tree",
            "overall_status",
            "phase_results",
            "release_version",
            "version",
        },
    )
    expected_candidate_commit = _require_git_sha(expected_candidate_commit)
    expected_candidate_tree = _require_git_sha(expected_candidate_tree)
    expected_contract_sha256 = _require_sha256(expected_contract_sha256)
    if (
        parsed["candidate_commit"] != expected_candidate_commit
        or parsed["candidate_tree"] != expected_candidate_tree
        or parsed["contract_sha256"] != expected_contract_sha256
    ):
        _fail(QualificationReason.PHASE_BINDING_INVALID)
    expected = build_qualification_envelope(
        candidate_commit=expected_candidate_commit,
        candidate_tree=expected_candidate_tree,
        contract_sha256=expected_contract_sha256,
        generated_at=_require_timestamp(parsed["generated_at"]),
        phase_summaries=phase_summaries,
    )
    if parsed != expected:
        _fail(QualificationReason.PHASE_BINDING_INVALID)
    return parsed


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    name: str
    sha256: str
    size_bytes: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def build_release_artifact_manifest(
    *,
    candidate_commit: str,
    candidate_tree: str,
    qualification_evidence_sha256: str,
    artifacts: Iterable[str | Path],
    source_tree_clean: bool,
) -> dict[str, Any]:
    if type(source_tree_clean) is not bool:
        _fail(QualificationReason.TYPE_INVALID)
    identities: list[ArtifactIdentity] = []
    names: set[str] = set()
    for raw_path in artifacts:
        path = Path(raw_path)
        if not path.is_file() or path.name != str(path.name):
            _fail(QualificationReason.IDENTITY_INVALID)
        if path.name in names:
            _fail(QualificationReason.IDENTITY_INVALID)
        names.add(path.name)
        identities.append(
            ArtifactIdentity(
                name=path.name,
                sha256=_sha256_file(path),
                size_bytes=str(path.stat().st_size),
            )
        )
    identities.sort(key=lambda item: item.name)
    return {
        "artifacts": [item.to_dict() for item in identities],
        "candidate_commit": _require_git_sha(candidate_commit),
        "candidate_tree": _require_git_sha(candidate_tree),
        "domain": "v3.10-cl8-release-artifact-manifest",
        "qualification_evidence_sha256": _require_sha256(qualification_evidence_sha256),
        "release_version": RELEASE_VERSION,
        "runtime_private_files_present": False,
        "source_tree_clean": source_tree_clean,
        "version": 1,
    }


def validate_release_artifact_manifest(
    manifest_bytes: bytes,
    *,
    expected_candidate_commit: str,
    expected_candidate_tree: str,
    expected_qualification_evidence_sha256: str,
    artifacts: Mapping[str, str | Path],
    privacy_canaries: Iterable[str],
) -> dict[str, Any]:
    parsed = parse_canonical_json(manifest_bytes)
    if not isinstance(parsed, dict):
        _fail(QualificationReason.TYPE_INVALID)
    _require_exact_keys(
        parsed,
        {
            "artifacts",
            "candidate_commit",
            "candidate_tree",
            "domain",
            "qualification_evidence_sha256",
            "release_version",
            "runtime_private_files_present",
            "source_tree_clean",
            "version",
        },
    )
    if (
        parsed["domain"] != "v3.10-cl8-release-artifact-manifest"
        or parsed["release_version"] != RELEASE_VERSION
        or parsed["version"] != 1
        or parsed["runtime_private_files_present"] is not False
        or parsed["source_tree_clean"] is not True
    ):
        _fail(QualificationReason.IDENTITY_INVALID)
    expected_candidate_commit = _require_git_sha(expected_candidate_commit)
    expected_candidate_tree = _require_git_sha(expected_candidate_tree)
    expected_qualification_evidence_sha256 = _require_sha256(
        expected_qualification_evidence_sha256
    )
    if (
        parsed["candidate_commit"] != expected_candidate_commit
        or parsed["candidate_tree"] != expected_candidate_tree
        or parsed["qualification_evidence_sha256"]
        != expected_qualification_evidence_sha256
    ):
        _fail(QualificationReason.IDENTITY_INVALID)
    if not isinstance(artifacts, Mapping) or not artifacts:
        _fail(QualificationReason.IDENTITY_INVALID)
    expected_artifacts = {str(name): Path(path) for name, path in artifacts.items()}
    canaries = tuple(str(value) for value in privacy_canaries if str(value))
    if not canaries:
        _fail(QualificationReason.PRIVACY_BOUNDARY_VIOLATION)
    artifacts = parsed["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        _fail(QualificationReason.TYPE_INVALID)
    names: list[str] = []
    for entry in artifacts:
        if not isinstance(entry, dict):
            _fail(QualificationReason.TYPE_INVALID)
        _require_exact_keys(entry, {"name", "sha256", "size_bytes"})
        name = entry["name"]
        if type(name) is not str or PurePosixPath(name).name != name or not name:
            _fail(QualificationReason.IDENTITY_INVALID)
        names.append(name)
        _require_sha256(entry["sha256"])
        if (
            type(entry["size_bytes"]) is not str
            or _UINT_RE.fullmatch(entry["size_bytes"]) is None
        ):
            _fail(QualificationReason.IDENTITY_INVALID)
    if names != sorted(names) or len(names) != len(set(names)):
        _fail(QualificationReason.IDENTITY_INVALID)
    if set(names) != set(expected_artifacts):
        _fail(QualificationReason.IDENTITY_INVALID)
    privacy_payloads: dict[str, bytes] = {}
    for entry in artifacts:
        name = str(entry["name"])
        path = expected_artifacts[name]
        if not path.is_file() or path.name != name:
            _fail(QualificationReason.IDENTITY_INVALID)
        raw = path.read_bytes()
        if str(len(raw)) != entry["size_bytes"] or sha256_hex(raw) != entry["sha256"]:
            _fail(QualificationReason.IDENTITY_INVALID)
        privacy_payloads[name] = raw
        if zipfile.is_zipfile(path):
            try:
                with zipfile.ZipFile(path, "r") as archive:
                    members = tuple(
                        item.filename
                        for item in archive.infolist()
                        if not item.is_dir()
                    )
                    if private_artifact_members(members):
                        _fail(QualificationReason.PRIVACY_BOUNDARY_VIOLATION)
                    for member in members:
                        privacy_payloads[f"{name}:{member}"] = archive.read(member)
            except (OSError, zipfile.BadZipFile):
                _fail(QualificationReason.IDENTITY_INVALID)
    if scan_shareable_bytes(privacy_payloads, canaries=canaries):
        _fail(QualificationReason.PRIVACY_BOUNDARY_VIOLATION)
    return parsed


@dataclass(frozen=True, slots=True)
class RegressionDisposition:
    stage: RegressionStage
    passed: bool
    expected: tuple[str, ...]
    actual: tuple[str, ...]
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["stage"] = self.stage.value
        return result


def pytest_junit_failure_node_ids(
    report_path: str | Path,
    *,
    source_root: str | Path,
) -> tuple[str, ...]:
    """Read every failed/error pytest node from a JUnit report.

    Text-summary parsing is intentionally avoided: a shared fixture setup error
    may be rendered only as an ``ERROR at setup`` heading and disappear from
    the short summary.  JUnit records the owning testcase for both outcomes.
    """

    report = Path(report_path)
    root = Path(source_root).resolve()
    try:
        document = ET.parse(report)
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(f"Invalid pytest JUnit report: {exc}") from exc
    nodes: set[str] = set()
    for testcase in document.getroot().iter("testcase"):
        if not any(child.tag in {"failure", "error"} for child in testcase):
            continue
        name = testcase.get("name", "").strip()
        classname = testcase.get("classname", "").strip()
        if not name or not classname:
            raise RuntimeError("Pytest JUnit failure has no node identity.")
        parts = classname.split(".")
        source_path: Path | None = None
        class_parts: list[str] = []
        for index in range(len(parts), 0, -1):
            candidate = root.joinpath(*parts[:index]).with_suffix(".py")
            if candidate.is_file():
                source_path = candidate
                class_parts = parts[index:]
                break
        if source_path is None:
            raise RuntimeError(f"Cannot resolve pytest JUnit classname: {classname}")
        relative = source_path.relative_to(root).as_posix()
        nodes.add("::".join([relative, *class_parts, name]))
    return tuple(sorted(nodes))


def compare_regression_failures(
    actual_failures: Iterable[str],
    *,
    stage: RegressionStage | str,
) -> RegressionDisposition:
    selected = RegressionStage(stage)
    expected_set = set(INHERITED_CUSTODY_FAILURES)
    if selected is RegressionStage.POST_RELEASE_CUT:
        expected_set.update(SUPERSEDED_RELEASE_METADATA_FAILURES)
    actual_set = {str(item).strip().replace("\\", "/") for item in actual_failures}
    actual_set.discard("")
    missing = tuple(sorted(expected_set - actual_set))
    unexpected = tuple(sorted(actual_set - expected_set))
    return RegressionDisposition(
        stage=selected,
        passed=not missing and not unexpected,
        expected=tuple(sorted(expected_set)),
        actual=tuple(sorted(actual_set)),
        missing=missing,
        unexpected=unexpected,
    )


def validate_changed_paths(
    changed_paths: Iterable[str],
    *,
    allowed_paths: frozenset[str],
) -> tuple[str, ...]:
    normalized = tuple(
        sorted({str(path).strip().replace("\\", "/") for path in changed_paths})
    )
    if not set(normalized).issubset(allowed_paths):
        _fail(QualificationReason.ALLOWLIST_VIOLATION)
    return normalized


def validate_qualification_changed_paths(
    changed_paths: Iterable[str],
) -> tuple[str, ...]:
    return validate_changed_paths(
        changed_paths,
        allowed_paths=QUALIFICATION_IMPLEMENTATION_ALLOWLIST,
    )


def validate_release_cut_changed_paths(changed_paths: Iterable[str]) -> tuple[str, ...]:
    return validate_changed_paths(changed_paths, allowed_paths=RELEASE_CUT_ALLOWLIST)


def validate_q0_evidence(
    evidence: Mapping[str, Any],
    *,
    candidate_commit: str,
    repository: str | Path,
    expected_accepted_commit: str,
    expected_accepted_tree: str,
    expected_review_evidence_sha256: str,
    expected_acceptance_record_sha256: str,
) -> None:
    _require_exact_keys(
        evidence,
        {
            "accepted_commit",
            "accepted_tree",
            "explicit_acceptance_record_sha256",
            "issue",
            "review_evidence_sha256",
            "review_verdict",
        },
    )
    if evidence["issue"] != 72 or evidence["review_verdict"] != "PASS":
        _fail(QualificationReason.Q0_BLOCKED)
    accepted_commit = _require_git_sha(evidence["accepted_commit"])
    expected_accepted_commit = _require_git_sha(expected_accepted_commit)
    expected_accepted_tree = _require_git_sha(expected_accepted_tree)
    expected_review_evidence_sha256 = _require_sha256(expected_review_evidence_sha256)
    expected_acceptance_record_sha256 = _require_sha256(
        expected_acceptance_record_sha256
    )
    if (
        accepted_commit != expected_accepted_commit
        or evidence["accepted_tree"] != expected_accepted_tree
        or evidence["review_evidence_sha256"] != expected_review_evidence_sha256
        or evidence["explicit_acceptance_record_sha256"]
        != expected_acceptance_record_sha256
    ):
        _fail(QualificationReason.Q0_BLOCKED)
    candidate_commit = _require_git_sha(candidate_commit)
    if git_commit_tree(repository, accepted_commit) != evidence[
        "accepted_tree"
    ] or not commit_is_ancestor(
        repository,
        accepted_commit,
        candidate_commit,
    ):
        _fail(QualificationReason.Q0_BLOCKED)


_GUI_REQUIRED_TRUE = frozenset(
    {
        "account_level_start_stop",
        "configured_execution_set_complete",
        "instrument_rows_canonical",
        "multiple_positions_visible",
        "central_reservations_visible",
        "pending_uncertain_visible",
        "risk_state_visible",
        "restart_disconnect_single_owner",
        "open_market_idle_open_account_wide",
        "read_model_owners_bound",
        "widgets_do_not_access_authoritative_files",
    }
)
_GUI_REQUIRED_ZERO = frozenset(
    {
        "duplicate_refresh_paths",
        "popup_storms",
        "gui_provider_post_paths",
        "widget_owned_calculations",
    }
)


def validate_gui_runtime_result(result: Mapping[str, Any]) -> None:
    expected = _GUI_REQUIRED_TRUE | _GUI_REQUIRED_ZERO
    _require_exact_keys(result, set(expected))
    if any(result[name] is not True for name in _GUI_REQUIRED_TRUE):
        _fail(QualificationReason.GUI_RUNTIME_QUALIFICATION_FAILED)
    if any(
        type(result[name]) is not int or result[name] != 0
        for name in _GUI_REQUIRED_ZERO
    ):
        _fail(QualificationReason.GUI_RUNTIME_QUALIFICATION_FAILED)


MULTI_SESSION_SCENARIOS = frozenset(
    {
        "CLEAN_RESTART_REPLAY",
        "PENDING_UNCERTAIN_CRASH_RECOVERY",
        "PARALLEL_SAME_ACCOUNT",
        "SEQUENTIAL_SAME_ACCOUNT",
        "OPERATION_REPLAY_ACROSS_SESSIONS",
        "SAME_CONTENT_DIFFERENT_SOURCE",
        "WRONG_ACCOUNT_SCOPE",
        "STALE_THEN_FRESH_WATERMARK",
    }
)
_MULTI_SESSION_ZERO = frozenset(
    {
        "duplicate_cash_effects",
        "duplicate_central_effects",
        "provider_resubmits",
        "wrong_account_adoptions",
        "account_scope_crossings",
    }
)
_MULTI_SESSION_TRUE = frozenset(
    {
        "source_identity_exact",
        "ledger_revision_monotonic",
        "central_revision_monotonic",
        "authority_revision_monotonic",
        "watermark_monotonic",
        "pending_uncertain_fail_closed",
    }
)


def validate_multi_session_result(result: Mapping[str, Any]) -> None:
    _require_exact_keys(result, {"scenarios", "invariants"})
    scenarios = result["scenarios"]
    invariants = result["invariants"]
    if (
        not isinstance(scenarios, list)
        or len(scenarios) != len(MULTI_SESSION_SCENARIOS)
        or any(type(item) is not str for item in scenarios)
        or set(scenarios) != MULTI_SESSION_SCENARIOS
    ):
        _fail(QualificationReason.MULTI_SESSION_QUALIFICATION_FAILED)
    if not isinstance(invariants, Mapping):
        _fail(QualificationReason.MULTI_SESSION_QUALIFICATION_FAILED)
    _require_exact_keys(invariants, set(_MULTI_SESSION_ZERO | _MULTI_SESSION_TRUE))
    if any(
        type(invariants[name]) is not int or invariants[name] != 0
        for name in _MULTI_SESSION_ZERO
    ):
        _fail(QualificationReason.MULTI_SESSION_QUALIFICATION_FAILED)
    if any(invariants[name] is not True for name in _MULTI_SESSION_TRUE):
        _fail(QualificationReason.MULTI_SESSION_QUALIFICATION_FAILED)


def validate_sandbox_account_disposition(
    value: Mapping[str, Any],
    *,
    expected_candidate_commit: str,
    expected_candidate_tree: str,
    authorization_record: bytes | None = None,
    preparation_summary: bytes | None = None,
) -> None:
    status = value.get("status")
    common = {
        "active_account_scope_sha256",
        "candidate_commit",
        "candidate_tree",
        "canonical_evidence_sha256",
        "redundant_account_scope_sha256",
        "run_id",
        "status",
    }
    if status == "REDUNDANT_ACCOUNT_RETAINED_WITH_REASON":
        expected = common | {"active_unchanged", "reason", "redundant_unreferenced"}
        _require_exact_keys(value, expected)
        if (
            value["active_unchanged"] is not True
            or value["redundant_unreferenced"] is not True
            or type(value["reason"]) is not str
            or not value["reason"].strip()
        ):
            _fail(QualificationReason.ACCOUNT_DISPOSITION_INVALID)
    elif status == "REDUNDANT_ACCOUNT_CLEANUP_COMPLETED":
        expected = common | {
            "active_unchanged",
            "authorization_record_sha256",
            "confirmation",
            "experiment_id",
            "preparation_summary_sha256",
            "provider_result",
            "redundant_absent_or_closed",
        }
        _require_exact_keys(value, expected)
        redundant_hash = _require_sha256(value["redundant_account_scope_sha256"])
        if (
            value["experiment_id"] != "CL8-SANDBOX-ACCOUNT-CLEANUP-V1"
            or value["confirmation"]
            != f"CLOSE CL8 REDUNDANT SANDBOX ACCOUNT {redundant_hash}"
            or value["active_unchanged"] is not True
            or value["redundant_absent_or_closed"] is not True
            or value["provider_result"] not in {"CLOSED", "ALREADY_CLOSED"}
        ):
            _fail(QualificationReason.ACCOUNT_DISPOSITION_INVALID)
        if not isinstance(authorization_record, bytes) or not isinstance(
            preparation_summary, bytes
        ):
            _fail(QualificationReason.ACCOUNT_DISPOSITION_INVALID)
        if sha256_hex(authorization_record) != _require_sha256(
            value["authorization_record_sha256"]
        ) or sha256_hex(preparation_summary) != _require_sha256(
            value["preparation_summary_sha256"]
        ):
            _fail(QualificationReason.ACCOUNT_DISPOSITION_INVALID)
        authorization = parse_canonical_json(authorization_record)
        preparation = parse_canonical_json(preparation_summary)
        if not isinstance(authorization, dict) or not isinstance(preparation, dict):
            _fail(QualificationReason.ACCOUNT_DISPOSITION_INVALID)
        _require_exact_keys(
            authorization,
            {
                "authorization_id",
                "candidate_commit",
                "candidate_tree",
                "command",
                "experiment_id",
                "preparation_summary_sha256",
            },
        )
        _require_exact_keys(
            preparation,
            {
                "candidate_commit",
                "candidate_tree",
                "contract_sha256",
                "experiment_id",
                "run_id",
                "status",
            },
        )
        if (
            authorization["command"] != "START EXPERIMENT"
            or authorization["experiment_id"] != "CL8-SANDBOX-ACCOUNT-CLEANUP-V1"
            or authorization["preparation_summary_sha256"]
            != value["preparation_summary_sha256"]
            or preparation["experiment_id"] != "CL8-SANDBOX-ACCOUNT-CLEANUP-V1"
            or preparation["contract_sha256"] != ACCEPTED_CONTRACT_SHA256
            or preparation["status"] != "PASS"
            or authorization["candidate_commit"] != expected_candidate_commit
            or authorization["candidate_tree"] != expected_candidate_tree
            or preparation["candidate_commit"] != expected_candidate_commit
            or preparation["candidate_tree"] != expected_candidate_tree
        ):
            _fail(QualificationReason.ACCOUNT_DISPOSITION_INVALID)
        _require_run_id(authorization["authorization_id"])
        _require_run_id(preparation["run_id"])
    else:
        _fail(QualificationReason.ACCOUNT_DISPOSITION_INVALID)
    expected_candidate_commit = _require_git_sha(expected_candidate_commit)
    expected_candidate_tree = _require_git_sha(expected_candidate_tree)
    if (
        value["candidate_commit"] != expected_candidate_commit
        or value["candidate_tree"] != expected_candidate_tree
    ):
        _fail(QualificationReason.ACCOUNT_DISPOSITION_INVALID)
    _require_sha256(value["active_account_scope_sha256"])
    _require_sha256(value["redundant_account_scope_sha256"])
    _require_sha256(value["canonical_evidence_sha256"])
    _require_run_id(value["run_id"])
    if value["active_account_scope_sha256"] == value["redundant_account_scope_sha256"]:
        _fail(QualificationReason.ACCOUNT_DISPOSITION_INVALID)


def validate_offline_case_results(results: Mapping[str, Any]) -> None:
    if set(results) != set(OFFLINE_CASE_IDS):
        _fail(QualificationReason.OFFLINE_MATRIX_INCOMPLETE)
    run_ids: set[str] = set()
    for case_id, value in results.items():
        if not isinstance(value, Mapping):
            _fail(QualificationReason.OFFLINE_MATRIX_INCOMPLETE)
        _require_exact_keys(
            value,
            {"canonical_summary_sha256", "executed_node_ids", "run_id", "status"},
        )
        run_id = _require_run_id(value["run_id"])
        node_ids = value["executed_node_ids"]
        if (
            value["status"] != "PASS"
            or run_id in run_ids
            or not isinstance(node_ids, list)
            or tuple(node_ids) != OFFLINE_CASE_NODE_IDS[case_id]
            or any(
                type(node_id) is not str or "::test_" not in node_id
                for node_id in node_ids
            )
            or len(node_ids) != len(set(node_ids))
        ):
            _fail(QualificationReason.OFFLINE_MATRIX_INCOMPLETE)
        run_ids.add(run_id)
        summary_sha256 = _require_sha256(value["canonical_summary_sha256"])
        expected_summary = canonical_json_bytes(
            {
                "case_id": case_id,
                "executed_node_ids": node_ids,
                "run_id": run_id,
                "status": "PASS",
            }
        )
        if summary_sha256 != sha256_hex(expected_summary):
            _fail(QualificationReason.OFFLINE_MATRIX_INCOMPLETE)


def scan_shareable_bytes(
    payloads: Mapping[str, bytes],
    *,
    canaries: Iterable[str] = (),
) -> tuple[str, ...]:
    needles = tuple(
        str(value).encode("utf-8") for value in canaries if str(value).strip()
    )
    findings: list[str] = []
    for name, raw in sorted(payloads.items()):
        if not isinstance(raw, bytes):
            _fail(QualificationReason.TYPE_INVALID)
        text = raw.decode("utf-8", errors="replace")
        reasons: list[str] = []
        if any(needle in raw for needle in needles):
            reasons.append("KNOWN_CANARY")
        if _AUTHORIZATION_RE.search(text):
            reasons.append("AUTHORIZATION_BEARER")
        if _PRIVATE_PATH_RE.search(text):
            reasons.append("PRIVATE_ABSOLUTE_PATH")
        for reason in sorted(set(reasons)):
            findings.append(f"{name}:{reason}")
    return tuple(findings)


_PRIVATE_RUNTIME_EXACT = frozenset(
    {
        ".env",
        "robot_state.json",
        "portfolio_state.json",
        "sandbox_diagnostic_state.json",
        "strategy_profiles.json",
        "multi_instrument_profiles.json",
        "instrument_runtimes.json",
        "central_order_state.json",
        "risk_profiles.json",
        "risk_state.json",
        "trading_events.db",
        "cash_ledger_v3_10.sqlite3",
        "runtime_cash_authority.json",
        "runtime_cash_authority.json.sha256",
        "runtime_cash_authority.json.lastgood",
        "runtime_bootstrap_report.json",
    }
)
_PRIVATE_DIRECTORY_NAMES = frozenset(
    {
        "backups",
        "logs",
        "reports",
        "support",
        "qualification_output",
        "verification_output",
    }
)


def private_artifact_members(members: Iterable[str]) -> tuple[str, ...]:
    findings: set[str] = set()
    for raw in members:
        normalized = str(raw).replace("\\", "/").strip("/")
        if not normalized:
            continue
        parts = PurePosixPath(normalized).parts
        lowered = tuple(part.lower() for part in parts)
        name = lowered[-1]
        if (
            name in _PRIVATE_RUNTIME_EXACT
            or any(part in _PRIVATE_DIRECTORY_NAMES for part in lowered)
            or name.endswith((".lock", ".wal", ".shm"))
            or "cash_ledger_v3_10.sqlite3" in lowered
        ):
            findings.add(normalized)
    return tuple(sorted(findings))


def write_immutable_evidence(
    evidence_root: str | Path,
    name: str,
    value: object,
) -> tuple[Path, Path]:
    if PurePosixPath(name).name != name or not name.endswith(".json"):
        _fail(QualificationReason.IDENTITY_INVALID)
    root = Path(evidence_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    digest_target = target.with_name(target.name + ".sha256")
    if target.exists() or digest_target.exists():
        _fail(QualificationReason.EVIDENCE_ALREADY_EXISTS)
    raw = canonical_json_bytes(value)
    digest = sha256_hex(raw)
    temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
    digest_temporary = digest_target.with_name(
        f"{digest_target.name}.{uuid4().hex}.tmp"
    )
    try:
        _write_exclusive_fsync(temporary, raw)
        _write_exclusive_fsync(digest_temporary, (digest + "\n").encode("ascii"))
        os.replace(temporary, target)
        os.replace(digest_temporary, digest_target)
    finally:
        temporary.unlink(missing_ok=True)
        digest_temporary.unlink(missing_ok=True)
    return target, digest_target


def verify_immutable_evidence(path: str | Path) -> object:
    target = Path(path)
    digest_path = target.with_name(target.name + ".sha256")
    try:
        raw = target.read_bytes()
        digest_bytes = digest_path.read_bytes()
    except OSError as exc:
        raise QualificationError(
            QualificationReason.EVIDENCE_INTEGRITY_INVALID
        ) from exc
    expected = (sha256_hex(raw) + "\n").encode("ascii")
    if digest_bytes != expected:
        _fail(QualificationReason.EVIDENCE_INTEGRITY_INVALID)
    return parse_canonical_json(raw)


def commit_is_ancestor(
    repository: str | Path,
    ancestor: str,
    candidate: str,
) -> bool:
    root = Path(repository).resolve()
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root.as_posix()}",
            "merge-base",
            "--is-ancestor",
            _require_git_sha(ancestor),
            _require_git_sha(candidate),
        ],
        cwd=root,
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def git_commit_tree(repository: str | Path, commit: str) -> str | None:
    root = Path(repository).resolve()
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root.as_posix()}",
            "rev-parse",
            f"{_require_git_sha(commit)}^{{tree}}",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and _GIT_SHA_RE.fullmatch(value) else None


def _write_exclusive_fsync(path: Path, value: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_sha256_lines(artifacts: Sequence[ArtifactIdentity]) -> bytes:
    ordered = sorted(artifacts, key=lambda item: item.name)
    if len({item.name for item in ordered}) != len(ordered):
        _fail(QualificationReason.IDENTITY_INVALID)
    return (
        "".join(f"{_require_sha256(item.sha256)}  {item.name}\n" for item in ordered)
    ).encode("ascii")


def _load_phase_arguments(values: Sequence[str]) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for value in values:
        phase, separator, path = value.partition("=")
        if separator != "=" or phase not in PHASE_KEYS or phase in result:
            _fail(QualificationReason.PHASE_BINDING_INVALID)
        result[phase] = Path(path).read_bytes()
    return result


def _load_artifact_arguments(values: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if separator != "=" or not name or name in result or not path:
            _fail(QualificationReason.IDENTITY_INVALID)
        result[name] = Path(path)
    if not result:
        _fail(QualificationReason.IDENTITY_INVALID)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline-only v3.10 CL8 qualification verifier."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("kat")
    envelope = commands.add_parser("verify-envelope")
    envelope.add_argument("path")
    envelope.add_argument("--phase", action="append", default=[])
    envelope.add_argument("--candidate-commit", required=True)
    envelope.add_argument("--candidate-tree", required=True)
    envelope.add_argument("--contract-sha256", required=True)
    artifact = commands.add_parser("verify-artifact-manifest")
    artifact.add_argument("path")
    artifact.add_argument("--candidate-commit", required=True)
    artifact.add_argument("--candidate-tree", required=True)
    artifact.add_argument("--qualification-evidence-sha256", required=True)
    artifact.add_argument("--artifact", action="append", default=[])
    artifact.add_argument("--privacy-canary", action="append", default=[])
    regression = commands.add_parser("compare-regression")
    regression.add_argument(
        "--stage", choices=[item.value for item in RegressionStage], required=True
    )
    regression.add_argument("--failures", required=True)
    cases = commands.add_parser("verify-offline-cases")
    cases.add_argument("path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "kat":
            print(
                json.dumps(
                    {
                        "qualification_evidence_sha256": QUALIFICATION_KAT_SHA256,
                        "release_artifact_manifest_sha256": RELEASE_MANIFEST_KAT_SHA256,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "verify-envelope":
            phase_summaries = _load_phase_arguments(args.phase)
            envelope = validate_qualification_envelope(
                Path(args.path).read_bytes(),
                phase_summaries=phase_summaries,
                expected_candidate_commit=args.candidate_commit,
                expected_candidate_tree=args.candidate_tree,
                expected_contract_sha256=args.contract_sha256,
            )
            print(sha256_hex(canonical_json_bytes(envelope)))
            return 0
        if args.command == "verify-artifact-manifest":
            manifest = validate_release_artifact_manifest(
                Path(args.path).read_bytes(),
                expected_candidate_commit=args.candidate_commit,
                expected_candidate_tree=args.candidate_tree,
                expected_qualification_evidence_sha256=(
                    args.qualification_evidence_sha256
                ),
                artifacts=_load_artifact_arguments(args.artifact),
                privacy_canaries=args.privacy_canary,
            )
            print(sha256_hex(canonical_json_bytes(manifest)))
            return 0
        if args.command == "compare-regression":
            failures = Path(args.failures).read_text(encoding="utf-8").splitlines()
            disposition = compare_regression_failures(failures, stage=args.stage)
            print(json.dumps(disposition.to_dict(), sort_keys=True))
            return 0 if disposition.passed else 1
        if args.command == "verify-offline-cases":
            raw = Path(args.path).read_bytes()
            parsed = parse_canonical_json(raw)
            if not isinstance(parsed, dict):
                _fail(QualificationReason.TYPE_INVALID)
            validate_offline_case_results(parsed)
            print("PASS")
            return 0
    except (OSError, QualificationError) as exc:
        reason = (
            exc.reason.value if isinstance(exc, QualificationError) else "IO_FAILURE"
        )
        print(f"[ERROR] {reason}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ACCEPTED_CONTRACT_COMMIT",
    "ACCEPTED_CONTRACT_RESCOPE_COMMIT",
    "ACCEPTED_CONTRACT_RESCOPE_SHA256",
    "ACCEPTED_CONTRACT_RESCOPE_TREE",
    "ACCEPTED_CONTRACT_SHA256",
    "ACCEPTED_CONTRACT_TREE",
    "CL7_PREDECESSOR_COMMIT",
    "CL7_PREDECESSOR_TREE",
    "INHERITED_CUSTODY_FAILURES",
    "MULTI_SESSION_SCENARIOS",
    "OFFLINE_CASE_IDS",
    "PHASE_KEYS",
    "QUALIFICATION_IMPLEMENTATION_ALLOWLIST",
    "QUALIFICATION_KAT_SHA256",
    "RELEASE_CUT_ALLOWLIST",
    "RELEASE_CUT_BASE_ALLOWLIST",
    "RELEASE_MANIFEST_KAT_SHA256",
    "RELEASE_REVIEW_CORRECTION_ALLOWLIST",
    "RELEASE_REVIEW_RESCOPE_ADDITIONS",
    "SUPERSEDED_RELEASE_METADATA_FAILURES",
    "V39_ORACLE_COMMIT",
    "V39_ORACLE_TREE",
    "ArtifactIdentity",
    "PhaseStatus",
    "QualificationError",
    "QualificationReason",
    "RegressionDisposition",
    "RegressionStage",
    "build_qualification_envelope",
    "build_release_artifact_manifest",
    "canonical_json_bytes",
    "commit_is_ancestor",
    "compare_regression_failures",
    "derive_overall_status",
    "git_commit_tree",
    "parse_canonical_json",
    "private_artifact_members",
    "pytest_junit_failure_node_ids",
    "release_sha256_lines",
    "scan_shareable_bytes",
    "sha256_hex",
    "validate_changed_paths",
    "validate_gui_runtime_result",
    "validate_multi_session_result",
    "validate_offline_case_results",
    "validate_phase_summary",
    "validate_q0_evidence",
    "validate_qualification_changed_paths",
    "validate_qualification_envelope",
    "validate_release_artifact_manifest",
    "validate_release_cut_changed_paths",
    "validate_sandbox_account_disposition",
    "verify_immutable_evidence",
    "write_immutable_evidence",
]
