from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

CASE_IDS = tuple(f"I72-{index:02d}" for index in range(1, 33))
IMPLEMENTATION_PATHS = frozenset(
    {
        "current/desktop_gui.py",
        "current/trading_robot/dashboard_view.py",
        "current/trading_robot/global_scheduler.py",
        "current/trading_robot/instrument_runtime.py",
        "current/trading_robot/gui_runtime_controller.py",
        "current/tools/v3_10_issue72_q0_evidence.py",
        "current/tests/test_v3_10_issue72_gui_runtime.py",
        "current/tests/fixtures/v3_10_issue72_gui_runtime_vectors.json",
        "docs/project/V3_10_ISSUE72_GUI_RUNTIME_REVIEW_RU.md",
        "docs/project/V3_10_ISSUE72_RISK_POLICY_GUI_ADR_RU.md",
        "docs/plans/V3_10_ISSUE72_MULTI_INSTRUMENT_SANDBOX_RUNBOOK_RU.md",
        "docs/plans/V3_10_ISSUE72_SANDBOX_ACCOUNT_CLEANUP_RUNBOOK_RU.md",
        "current/README.md",
        "ROADMAP.md",
    }
)
INHERITED_FAILURES = frozenset(
    {
        "tests/test_v3_10_cash_ledger_persistence.py::test_v310_cl2_28_three_path_delta_and_immutable_predecessor_files",
        "tests/test_v3_10_broker_read_adapters.py::test_v310_cl3_17_exact_three_path_delta",
        "tests/test_v3_10_cash_ledger_opening_reconciliation.py::test_v310_cl4_20_three_path_delta_and_predecessor_custody",
        "tests/test_v3_10_cash_availability.py::test_exact_successor_custody_and_three_path_delta",
        "tests/test_v3_10_reporting_risk_cash_context.py::test_v310_cl6_01_exact_contract_lineage_and_three_path_delta",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::test_exact_implementation_allowlist",
    }
)
HASH = re.compile(r"[0-9a-f]{64}\Z")
RESULT_KEYS = frozenset(
    {"assertions", "artifact_identities", "counters", "observations"}
)
RECORD_KEYS = frozenset(
    {
        "version",
        "case_id",
        "candidate_commit",
        "candidate_tree",
        "accepted_contract_commit",
        "accepted_contract_tree",
        "producer_kind",
        "producer_id",
        "producer_command_sha256",
        "input_identities",
        "started_at",
        "completed_at",
        "exit_code",
        "result_payload",
        "result_payload_sha256",
        "status",
    }
)
ALLOWED_PRODUCER_KINDS = frozenset(
    {
        "GIT_CUSTODY",
        "COMMITTED_AST_REACHABILITY",
        "PYTEST_NODE",
        "DOCUMENT_SCHEMA",
        "FULL_REGRESSION",
        "EXTERNAL_ACCOUNT_DISPOSITION",
    }
)
RESULT_ENTRY_KEYS = frozenset({"case_id", "evidence_sha256", "reason", "status"})
MANIFEST_KEYS = frozenset(
    {
        "version",
        "candidate_commit",
        "candidate_tree",
        "accepted_contract_commit",
        "accepted_contract_tree",
        "records",
        "manifest_sha256",
    }
)
MANIFEST_ENTRY_KEYS = frozenset(
    {
        "case_id",
        "evidence_sha256",
        "producer_kind",
        "producer_id",
        "result_payload_sha256",
    }
)
REPORT_KEYS = frozenset(
    {
        "version",
        "issue",
        "implementation_commit",
        "implementation_tree",
        "accepted_contract_commit",
        "accepted_contract_tree",
        "rescope_contract_sha256",
        "gui_audit_sha256",
        "risk_policy_adr_sha256",
        "multi_instrument_runbook_sha256",
        "account_cleanup_runbook_sha256",
        "acceptance_case_results",
        "acceptance_case_summary_sha256",
        "case_evidence_manifest_sha256",
        "account_disposition",
        "full_regression_result",
        "provider_calls_performed",
        "provider_mutations_performed",
        "generated_at",
        "q0_candidate_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class ProducerSpec:
    kind: str
    producer_id: str


def _specs() -> dict[str, ProducerSpec]:
    test_file = "tests/test_v3_10_issue72_gui_runtime.py"
    behavioral_nodes = {
        3: "test_controller_starts_and_stops_exact_three_runtime_set",
        4: "test_group_start_is_all_or_nothing_when_one_runtime_is_blocked",
        5: "test_stop_preserves_unresolved_custody_and_is_idempotent",
        6: "test_dashboard_binds_positions_central_risk_and_cl7_without_collapsing_rows",
        7: "test_dashboard_binds_positions_central_risk_and_cl7_without_collapsing_rows",
        8: "test_dashboard_exposes_each_central_lifecycle_state",
        9: "test_dashboard_exposes_each_central_lifecycle_state",
        10: "test_dashboard_binds_positions_central_risk_and_cl7_without_collapsing_rows",
        11: "test_dashboard_missing_owner_evidence_fails_closed",
        12: "test_dashboard_binds_positions_central_risk_and_cl7_without_collapsing_rows",
        15: "test_proposal_routes_through_central_then_execution_adapter",
        17: "test_controller_enforces_closed_cl7_start_matrix",
        18: "test_controller_prevalidation_blockers_create_no_proposal_or_dispatch",
        19: "test_restart_restores_set_without_duplicate_proposal",
        20: "test_restart_with_pending_state_is_recovery_first",
        21: "test_market_idle_and_disconnect_do_not_create_proposals",
        22: "test_open_market_idle_open_preserves_set_and_watermark",
        23: "test_transient_failures_coalesce_to_one_status_surface",
        24: "test_controller_rejects_cross_account_scope",
    }
    specs: dict[str, ProducerSpec] = {}
    for index in range(1, 33):
        case_id = f"I72-{index:02d}"
        if index <= 2:
            specs[case_id] = ProducerSpec("GIT_CUSTODY", f"issue72_git_{index:02d}")
        elif index in {13, 14, 16, 25, 29, 30}:
            specs[case_id] = ProducerSpec(
                "COMMITTED_AST_REACHABILITY", f"issue72_ast_{index:02d}"
            )
        elif index in {26, 28, 32}:
            specs[case_id] = ProducerSpec(
                "DOCUMENT_SCHEMA", f"issue72_document_{index:02d}"
            )
        elif index == 27:
            specs[case_id] = ProducerSpec(
                "EXTERNAL_ACCOUNT_DISPOSITION",
                "issue72_external_account_disposition_v1",
            )
        elif index == 31:
            specs[case_id] = ProducerSpec(
                "FULL_REGRESSION", "issue72_full_predecessor_regression"
            )
        elif index in behavioral_nodes:
            specs[case_id] = ProducerSpec(
                "PYTEST_NODE",
                f"{test_file}::{behavioral_nodes[index]}",
            )
        else:
            specs[case_id] = ProducerSpec(
                "COMMITTED_AST_REACHABILITY", f"issue72_ast_{index:02d}"
            )
    return specs


PRODUCERS = _specs()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: Any) -> str:
    data = value if isinstance(value, bytes) else canonical_bytes(value)
    return hashlib.sha256(data).hexdigest()


def _git(root: Path, *args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def _timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("generated-at must be timezone-aware")
    return value


def _materialize(root: Path, commit: str, destination: Path) -> None:
    # A local clone preserves Git custody for exact-head regression tests while
    # remaining provider-free and leaving source refs untouched.  Recreate the
    # frozen implementation branch name because the dedicated custody oracle
    # intentionally verifies both the exact commit and branch topology.
    common_git_dir = str(
        _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-checkout",
            "--local",
            common_git_dir,
            str(destination),
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={destination.as_posix()}",
            "checkout",
            "--quiet",
            "-B",
            "agent/v3-10-issue72-gui-runtime-implementation",
            commit,
        ],
        cwd=destination,
        check=True,
        capture_output=True,
    )


def _run(command: list[str], cwd: Path) -> tuple[int, str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    for actual, placeholder in (
        (str(cwd.resolve()), "<CHECKOUT_CURRENT>"),
        (str(cwd.resolve().parent), "<CHECKOUT>"),
    ):
        output = output.replace(actual, placeholder).replace(
            actual.replace("\\", "/"), placeholder
        )
    output = re.sub(r"\bin \d+(?:\.\d+)?s\b", "in <DURATION>s", output)
    return result.returncode, output


def _assertion(name: str, actual: Any, expected: Any) -> dict[str, Any]:
    return {
        "actual": actual,
        "expected": expected,
        "name": name,
        "passed": actual == expected,
    }


def _payload(
    assertions: list[dict[str, Any]],
    *,
    artifacts: list[dict[str, str]] | None = None,
    counters: dict[str, int] | None = None,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "assertions": assertions,
        "artifact_identities": artifacts or [],
        "counters": {
            "active_runtimes": 0,
            "adapter_dispatch": 0,
            "central_intent": 0,
            "popup_events": 0,
            "portfolio_risk_admission": 0,
            "proposal": 0,
            "provider_mutation": 0,
            "risk_dispatch_validation": 0,
            **(counters or {}),
        },
        "observations": observations or {},
    }
    if frozenset(result) != RESULT_KEYS:
        raise AssertionError("result payload schema drift")
    return result


def _behavior_counters(case_id: str) -> dict[str, int]:
    """Closed per-case counters asserted by the bound dedicated test node."""

    overrides = {
        "I72-03": {"active_runtimes": 3},
        "I72-05": {"active_runtimes": 0},
        "I72-06": {"active_runtimes": 3},
        "I72-15": {"proposal": 1, "central_intent": 1, "adapter_dispatch": 1},
        "I72-19": {"active_runtimes": 0},
        "I72-22": {"proposal": 1, "central_intent": 1, "adapter_dispatch": 1},
    }
    return dict(overrides.get(case_id, {}))


def _ast_payload(tree: Path, case_id: str) -> dict[str, Any]:
    gui_source = (tree / "current" / "desktop_gui.py").read_text(encoding="utf-8")
    controller_source = (
        tree / "current" / "trading_robot" / "gui_runtime_controller.py"
    ).read_text(encoding="utf-8")
    scheduler_source = (
        tree / "current" / "trading_robot" / "global_scheduler.py"
    ).read_text(encoding="utf-8")
    runtime_source = (
        tree / "current" / "trading_robot" / "instrument_runtime.py"
    ).read_text(encoding="utf-8")
    parsed = (ast.parse(gui_source), ast.parse(controller_source))
    called = {
        node.func.attr
        for module in parsed
        for node in ast.walk(module)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    forbidden = {
        "post_order",
        "post_order_once",
        "execute",
        "close_unattributed_position",
        "apply_max_orders_per_day",
    }
    checks = {
        "legacy_bot_absent": "SandboxTradingBot" not in gui_source + controller_source,
        "forbidden_callbacks_absent": not forbidden.intersection(called),
        "central_boundary_exact": controller_source.count("CentralOrderCoordinator(") == 1,
        "adapter_boundary_exact": controller_source.count("SandboxExecutionAdapter(") == 1,
        "controller_provider_constructor_absent": "TBankSandboxClient" not in controller_source,
        "gui_controller_start_present": ".start_configured_set(" in gui_source,
        "gui_controller_stop_present": ".stop_configured_set(" in gui_source,
        "gui_controller_cycle_present": ".run_cycle(" in gui_source,
        "exact_cl7_gate_present": "EXACT_CASH_ARMED" in controller_source,
        "same_risk_identity_present": " is not self.portfolio_risk_runtime" in controller_source,
        "authoritative_direct_json_write_absent": not any(
            item in gui_source
            for item in ("atomic_write_json(", ".write_text(", ".write_bytes(")
        ),
        "privacy_safe_rendering_present": "_privacy_safe_gui_value(" in gui_source,
        "scheduler_provider_free": "post_order" not in scheduler_source,
        "single_group_cas_call": scheduler_source.count("compare_and_swap_all(") == 1,
        "cas_lock_present": "InterProcessFileLock" in runtime_source,
        "cas_readback_present": "GROUP_POSTCONDITION_FAILED" in runtime_source,
        "stale_labels_absent": not any(
            item in gui_source for item in ("v3.6", "v3.7", "v3.8", "v3.9")
        ),
    }
    return _payload(
        [_assertion(name, actual, True) for name, actual in sorted(checks.items())],
        artifacts=[
            {"name": "desktop_gui.py", "sha256": digest(gui_source.encode())},
            {"name": "gui_runtime_controller.py", "sha256": digest(controller_source.encode())},
        ],
        observations={"case_id": case_id, "checked_call_attributes": sorted(called)},
    )


def _document_payload(tree: Path, case_id: str) -> dict[str, Any]:
    documents = {
        "gui_audit": tree / "docs/project/V3_10_ISSUE72_GUI_RUNTIME_REVIEW_RU.md",
        "risk_adr": tree / "docs/project/V3_10_ISSUE72_RISK_POLICY_GUI_ADR_RU.md",
        "multi_runbook": tree / "docs/plans/V3_10_ISSUE72_MULTI_INSTRUMENT_SANDBOX_RUNBOOK_RU.md",
        "cleanup_runbook": tree / "docs/plans/V3_10_ISSUE72_SANDBOX_ACCOUNT_CLEANUP_RUNBOOK_RU.md",
    }
    contents = {name: path.read_text(encoding="utf-8") for name, path in documents.items()}
    fixture = json.loads(
        (
            tree
            / "current/tests/fixtures/v3_10_issue72_gui_runtime_vectors.json"
        ).read_text(encoding="utf-8")
    )

    def contains_prefilled_outcome(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("status") == "PASS" or value.get("passed") is True:
                return True
            return any(contains_prefilled_outcome(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_prefilled_outcome(item) for item in value)
        return False

    checks = {
        "risk_option_exact": "READ_ONLY_GUI_PLUS_EXISTING_ACCEPTED_OPERATOR_TOOLS" in contents["risk_adr"],
        "cleanup_gate_exact": "START EXPERIMENT" in contents["cleanup_runbook"],
        "cleanup_preview": "masked/hash preview" in contents["cleanup_runbook"],
        "provider_free_preparation": "provider calls = 0" in contents["multi_runbook"],
        "review_map_complete": all(item in contents["gui_audit"] for item in ("KEEP", "REFACTOR", "REMOVE", "DEFER")),
        "q0_fixture_not_prefilled": not contains_prefilled_outcome(fixture),
    }
    return _payload(
        [_assertion(name, actual, True) for name, actual in sorted(checks.items())],
        artifacts=[
            {"name": name, "sha256": digest(path.read_bytes())}
            for name, path in sorted(documents.items())
        ],
        observations={"case_id": case_id, "document_count": len(documents)},
    )


def _validate_external(
    path: Path | None,
    trusted_hash: str | None,
) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    if path is None or trusted_hash is None:
        return (
            False,
            {
                "disposition": "NOT_ESTABLISHED",
                "disposition_evidence_sha256": None,
            },
            None,
        )
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    common_keys = {
        "version",
        "disposition",
        "experiment_id",
        "preparation_record_sha256",
        "start_experiment_record_sha256",
        "observed_at",
        "fresh_until",
        "account_list_evidence_sha256",
        "active_account_scope_sha256",
        "redundant_account_scope_sha256",
        "runtime_reference_scan_sha256",
        "configuration_reference_scan_sha256",
        "backup_reference_scan_sha256",
        "acceptance_reference_scan_sha256",
        "open_positions_status",
        "open_orders_status",
        "pending_uncertain_status",
        "raw_identifiers_absent",
        "variant",
        "record_sha256",
    }
    claimed = value.get("record_sha256")
    unsigned = dict(value)
    unsigned.pop("record_sha256", None)
    actual = digest(unsigned)
    disposition = value.get("disposition")
    retained = disposition == "REDUNDANT_ACCOUNT_RETAINED_WITH_REASON"
    expected_variant_keys = (
        {
            "reason_code",
            "reason_text_sha256",
            "review_record_sha256",
            "provider_mutation_performed",
        }
        if retained
        else {
            "masked_preview_sha256",
            "operator_confirmation_sha256",
            "provider_action_receipt_sha256",
            "post_action_observed_at",
            "post_account_list_evidence_sha256",
            "active_account_unchanged",
            "redundant_account_absent",
            "review_record_sha256",
        }
    )
    variant = value.get("variant")
    hashes = [
        item
        for key, item in value.items()
        if key.endswith("_sha256") and key != "record_sha256"
    ]
    if isinstance(variant, dict):
        hashes.extend(
            item for key, item in variant.items() if key.endswith("_sha256")
        )
    try:
        observed = datetime.fromisoformat(str(value["observed_at"]).replace("Z", "+00:00"))
        fresh = datetime.fromisoformat(str(value["fresh_until"]).replace("Z", "+00:00"))
        fresh_valid = (
            observed.tzinfo is not None
            and fresh.tzinfo is not None
            and 0 <= (fresh - observed).total_seconds() <= 300
        )
    except (KeyError, TypeError, ValueError):
        fresh_valid = False
    variant_valid = isinstance(variant, dict) and set(variant) == expected_variant_keys
    if retained and variant_valid:
        variant_valid = (
            variant.get("reason_code")
            in {
                "PROVIDER_CLEANUP_UNAVAILABLE",
                "CLEANUP_DEFERRED_BY_OPERATOR",
                "ACCOUNT_RETAINED_FOR_AUDIT",
            }
            and variant.get("provider_mutation_performed") is False
        )
    elif variant_valid:
        variant_valid = (
            variant.get("active_account_unchanged") is True
            and variant.get("redundant_account_absent") is True
        )
    valid = (
        set(value) == common_keys
        and value.get("version") == 1
        and
        HASH.fullmatch(trusted_hash) is not None
        and actual == trusted_hash
        and claimed == trusted_hash
        and value.get("raw_identifiers_absent") is True
        and disposition
        in {
            "REDUNDANT_ACCOUNT_RETAINED_WITH_REASON",
            "REDUNDANT_ACCOUNT_CLEANUP_COMPLETED",
        }
        and value.get("experiment_id")
        == (
            "ISSUE72-SANDBOX-ACCOUNT-DISPOSITION-V1"
            if retained
            else "ISSUE72-SANDBOX-ACCOUNT-CLEANUP-V1"
        )
        and value.get("active_account_scope_sha256")
        != value.get("redundant_account_scope_sha256")
        and hashes
        and all(isinstance(item, str) and HASH.fullmatch(item) for item in hashes)
        and fresh_valid
        and variant_valid
    )
    return (
        valid,
        {
            "disposition": disposition or "NOT_ESTABLISHED",
            "disposition_evidence_sha256": actual,
        },
        value if valid else None,
    )


def _failure_nodes(output: str) -> set[str]:
    return set(
        re.findall(r"(?:FAILED\s+)?(tests/[^\s]+::test_[^\s]+)", output.replace("\\", "/"))
    )


def _record(
    *,
    case_id: str,
    spec: ProducerSpec,
    candidate: str,
    candidate_tree: str,
    contract: str,
    contract_tree: str,
    timestamp: str,
    payload: dict[str, Any],
    exit_code: int,
    command: list[str],
    inputs: list[dict[str, str]],
) -> tuple[dict[str, Any], str]:
    status = "PASS" if exit_code == 0 and all(
        item["passed"] for item in payload["assertions"]
    ) else "FAIL"
    record = {
        "version": 1,
        "case_id": case_id,
        "candidate_commit": candidate,
        "candidate_tree": candidate_tree,
        "accepted_contract_commit": contract,
        "accepted_contract_tree": contract_tree,
        "producer_kind": spec.kind,
        "producer_id": spec.producer_id,
        "producer_command_sha256": digest(command),
        "input_identities": inputs,
        "started_at": timestamp,
        "completed_at": timestamp,
        "exit_code": exit_code,
        "result_payload": payload,
        "result_payload_sha256": digest(payload),
        "status": status,
    }
    if frozenset(record) != RECORD_KEYS:
        raise AssertionError("case evidence schema drift")
    return record, digest(record)


def generate(args: argparse.Namespace) -> dict[str, Any]:
    root = args.repo_root.resolve()
    timestamp = _timestamp(args.generated_at)
    candidate = str(_git(root, "rev-parse", f"{args.candidate}^{{commit}}"))
    candidate_tree = str(_git(root, "rev-parse", f"{candidate}^{{tree}}"))
    contract = str(_git(root, "rev-parse", f"{args.accepted_contract}^{{commit}}"))
    contract_tree = str(_git(root, "rev-parse", f"{contract}^{{tree}}"))
    if args.candidate_tree and candidate_tree != args.candidate_tree:
        raise ValueError("candidate tree mismatch")
    if args.accepted_contract_tree and contract_tree != args.accepted_contract_tree:
        raise ValueError("accepted contract tree mismatch")
    ancestry = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "merge-base", "--is-ancestor", contract, candidate],
        cwd=root,
        check=False,
    ).returncode == 0
    changed = tuple(
        line for line in str(_git(root, "diff", "--name-only", f"{contract}..{candidate}")).splitlines() if line
    )
    external_valid, account_disposition, external_record = _validate_external(
        args.account_disposition,
        args.account_disposition_sha256,
    )

    with tempfile.TemporaryDirectory(prefix="issue72-q0-") as temp_name:
        temp_root = Path(temp_name)
        materialized = temp_root / "checkout"
        _materialize(root, candidate, materialized)
        # Pytest writes must stay outside the committed-tree materialization;
        # otherwise custody tests correctly classify basetemp artifacts as an
        # unexpected implementation delta.
        (temp_root / ".q0").mkdir()
        behavioral_runs: dict[str, tuple[int, str, list[str]]] = {}
        for spec in PRODUCERS.values():
            if spec.kind != "PYTEST_NODE" or spec.producer_id in behavioral_runs:
                continue
            node_token = digest(spec.producer_id.encode("utf-8"))[:12]
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                f"--basetemp=../../.q0/pytest-{node_token}",
                spec.producer_id,
            ]
            code, output = _run(command, materialized / "current")
            behavioral_runs[spec.producer_id] = (code, output, command)
        regression_command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp=../../.q0/pytest-regression",
            "tests",
        ]
        regression_exit, regression_output = _run(regression_command, materialized / "current")
        failures = _failure_nodes(regression_output)
        collection_present = re.search(
            r"\b\d+ (?:passed|failed)\b", regression_output
        ) is not None
        regression_ok = (
            regression_exit in {0, 1}
            and collection_present
            and failures.issubset(INHERITED_FAILURES)
        )

        contract_blob = bytes(_git(root, "show", f"{contract}:docs/project/V3_10_ISSUE72_GUI_RUNTIME_RESCOPE_CONTRACT_RU.md", binary=True))
        inputs = [
            {"name": "accepted_contract_blob", "sha256": digest(contract_blob)},
            {"name": "candidate_tree_oid", "sha256": digest(candidate_tree.encode("ascii"))},
        ]
        records: list[dict[str, Any]] = []
        results: list[dict[str, str]] = []
        for case_id in CASE_IDS:
            spec = PRODUCERS[case_id]
            if spec.kind == "GIT_CUSTODY":
                checks = {
                    "accepted_contract_is_ancestor": ancestry,
                    "implementation_paths_exact": set(changed) == IMPLEMENTATION_PATHS,
                    "source_branch_exact": str(_git(root, "branch", "--show-current"))
                    == "agent/v3-10-issue72-gui-runtime-implementation",
                }
                payload = _payload(
                    [_assertion(name, actual, True) for name, actual in sorted(checks.items())],
                    observations={"changed_paths": list(changed)},
                )
                exit_code, command = 0, ["git", "diff", "--name-only", f"{contract}..{candidate}"]
            elif spec.kind == "COMMITTED_AST_REACHABILITY":
                payload = _ast_payload(materialized, case_id)
                exit_code, command = 0, ["internal", spec.producer_id]
            elif spec.kind == "DOCUMENT_SCHEMA":
                payload = _document_payload(materialized, case_id)
                exit_code, command = 0, ["internal", spec.producer_id]
            elif spec.kind == "EXTERNAL_ACCOUNT_DISPOSITION":
                payload = _payload(
                    [_assertion("trusted_external_disposition", external_valid, True)],
                    artifacts=(
                        [{"name": "account_disposition", "sha256": args.account_disposition_sha256}]
                        if external_valid
                        else []
                    ),
                    observations={
                        "disposition": account_disposition["disposition"],
                        "verified_record": external_record,
                    },
                )
                exit_code, command = (0 if external_valid else 1), ["external", spec.producer_id]
            elif spec.kind == "FULL_REGRESSION":
                payload = _payload(
                    [
                        _assertion("unexpected_failures", sorted(failures - INHERITED_FAILURES), []),
                        _assertion("collection_present", collection_present, True),
                        _assertion(
                            "pytest_exit_class",
                            regression_exit in {0, 1},
                            True,
                        ),
                    ],
                    observations={
                        "actual_failures": sorted(failures),
                        "inherited_failures": sorted(INHERITED_FAILURES),
                        "output_sha256": digest(regression_output.encode()),
                    },
                )
                exit_code = 0 if regression_ok else max(1, regression_exit)
                command = regression_command
            else:
                dedicated_exit, dedicated_output, dedicated_command = behavioral_runs[
                    spec.producer_id
                ]
                payload = _payload(
                    [_assertion("dedicated_suite_exit_code", dedicated_exit, 0)],
                    artifacts=[
                        {
                            "name": spec.producer_id,
                            "sha256": digest(dedicated_output.encode()),
                        }
                    ],
                    counters=_behavior_counters(case_id),
                    observations={
                        "output_sha256": digest(dedicated_output.encode()),
                        "pytest_node_id": spec.producer_id,
                    },
                )
                exit_code, command = dedicated_exit, dedicated_command
            record, evidence_sha = _record(
                case_id=case_id,
                spec=spec,
                candidate=candidate,
                candidate_tree=candidate_tree,
                contract=contract,
                contract_tree=contract_tree,
                timestamp=timestamp,
                payload=payload,
                exit_code=exit_code,
                command=command,
                inputs=inputs,
            )
            records.append(record)
            results.append(
                {
                    "case_id": case_id,
                    "evidence_sha256": evidence_sha,
                    "reason": "producer_completed" if record["status"] == "PASS" else "producer_failed",
                    "status": record["status"],
                }
            )

        manifest_records = [
            {
                "case_id": record["case_id"],
                "evidence_sha256": digest(record),
                "producer_kind": record["producer_kind"],
                "producer_id": record["producer_id"],
                "result_payload_sha256": record["result_payload_sha256"],
            }
            for record in records
        ]
        manifest = {
            "version": 1,
            "candidate_commit": candidate,
            "candidate_tree": candidate_tree,
            "accepted_contract_commit": contract,
            "accepted_contract_tree": contract_tree,
            "records": manifest_records,
        }
        manifest["manifest_sha256"] = digest(manifest)
        docs = {
            "rescope_contract_sha256": digest(contract_blob),
            "gui_audit_sha256": digest((materialized / "docs/project/V3_10_ISSUE72_GUI_RUNTIME_REVIEW_RU.md").read_bytes()),
            "risk_policy_adr_sha256": digest((materialized / "docs/project/V3_10_ISSUE72_RISK_POLICY_GUI_ADR_RU.md").read_bytes()),
            "multi_instrument_runbook_sha256": digest((materialized / "docs/plans/V3_10_ISSUE72_MULTI_INSTRUMENT_SANDBOX_RUNBOOK_RU.md").read_bytes()),
            "account_cleanup_runbook_sha256": digest((materialized / "docs/plans/V3_10_ISSUE72_SANDBOX_ACCOUNT_CLEANUP_RUNBOOK_RU.md").read_bytes()),
        }
        report = {
            "version": 1,
            "issue": 72,
            "implementation_commit": candidate,
            "implementation_tree": candidate_tree,
            "accepted_contract_commit": contract,
            "accepted_contract_tree": contract_tree,
            **docs,
            "acceptance_case_results": results,
            "acceptance_case_summary_sha256": digest(results),
            "case_evidence_manifest_sha256": manifest["manifest_sha256"],
            "account_disposition": account_disposition,
            "full_regression_result": {
                "actual_failures": sorted(failures),
                "inherited_failure_subset": regression_ok,
                "output_sha256": digest(regression_output.encode()),
            },
            "provider_calls_performed": False,
            "provider_mutations_performed": False,
            "generated_at": timestamp,
        }
        report["q0_candidate_sha256"] = digest(report)

        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        record_root = output / "records"
        record_root.mkdir(exist_ok=True)
        for record in records:
            (record_root / f"{record['case_id']}.json").write_bytes(canonical_bytes(record))
        (output / "manifest.json").write_bytes(canonical_bytes(manifest))
        (output / "q0_candidate.json").write_bytes(canonical_bytes(report))
        return report


def verify(args: argparse.Namespace) -> dict[str, Any]:
    evidence = args.evidence.resolve()
    report = json.loads((evidence / "q0_candidate.json").read_text(encoding="utf-8"))
    manifest = json.loads((evidence / "manifest.json").read_text(encoding="utf-8"))
    if frozenset(report) != REPORT_KEYS:
        raise ValueError("q0 candidate schema mismatch")
    if frozenset(manifest) != MANIFEST_KEYS:
        raise ValueError("manifest schema mismatch")
    report_unsigned = dict(report)
    report_hash = report_unsigned.pop("q0_candidate_sha256")
    if report_hash != digest(report_unsigned):
        raise ValueError("q0 candidate hash mismatch")
    manifest_unsigned = dict(manifest)
    manifest_hash = manifest_unsigned.pop("manifest_sha256")
    if manifest_hash != digest(manifest_unsigned):
        raise ValueError("manifest hash mismatch")
    if report["case_evidence_manifest_sha256"] != manifest_hash:
        raise ValueError("candidate/manifest binding mismatch")
    trusted = {
        "implementation_commit": str(
            _git(args.repo_root.resolve(), "rev-parse", f"{args.candidate}^{{commit}}")
        ),
        "implementation_tree": args.candidate_tree,
        "accepted_contract_commit": str(
            _git(
                args.repo_root.resolve(),
                "rev-parse",
                f"{args.accepted_contract}^{{commit}}",
            )
        ),
        "accepted_contract_tree": args.accepted_contract_tree,
    }
    for key, expected in trusted.items():
        if report[key] != expected or manifest[key.replace("implementation_", "candidate_")] != expected:
            raise ValueError(f"trusted binding mismatch: {key}")
    if report["version"] != 1 or report["issue"] != 72:
        raise ValueError("candidate constants mismatch")
    if report["provider_calls_performed"] is not False:
        raise ValueError("provider-call claim mismatch")
    if report["provider_mutations_performed"] is not False:
        raise ValueError("provider-mutation claim mismatch")
    results = report["acceptance_case_results"]
    if not isinstance(results, list) or [item.get("case_id") for item in results] != list(CASE_IDS):
        raise ValueError("candidate case set/order mismatch")
    if digest(results) != report["acceptance_case_summary_sha256"]:
        raise ValueError("candidate case summary mismatch")
    if any(
        frozenset(item) != RESULT_ENTRY_KEYS
        or item["status"] not in {"PASS", "FAIL"}
        or HASH.fullmatch(str(item["evidence_sha256"])) is None
        for item in results
    ):
        raise ValueError("candidate case result schema mismatch")
    entries = manifest["records"]
    if [item["case_id"] for item in entries] != list(CASE_IDS):
        raise ValueError("manifest case set/order mismatch")
    expected_record_names = {f"{case_id}.json" for case_id in CASE_IDS}
    actual_record_names = {
        item.name for item in (evidence / "records").iterdir() if item.is_file()
    }
    if actual_record_names != expected_record_names:
        raise ValueError("record file set mismatch")
    result_by_case = {item["case_id"]: item for item in results}
    for entry in entries:
        if frozenset(entry) != MANIFEST_ENTRY_KEYS:
            raise ValueError(f"manifest entry schema mismatch: {entry.get('case_id')}")
        record = json.loads(
            (evidence / "records" / f"{entry['case_id']}.json").read_text(encoding="utf-8")
        )
        if frozenset(record) != RECORD_KEYS or digest(record) != entry["evidence_sha256"]:
            raise ValueError(f"record identity mismatch: {entry['case_id']}")
        if record["producer_kind"] not in ALLOWED_PRODUCER_KINDS:
            raise ValueError(f"producer kind invalid: {entry['case_id']}")
        expected_spec = PRODUCERS[entry["case_id"]]
        if (
            record["producer_kind"] != expected_spec.kind
            or record["producer_id"] != expected_spec.producer_id
            or entry["producer_kind"] != expected_spec.kind
            or entry["producer_id"] != expected_spec.producer_id
        ):
            raise ValueError(f"producer binding mismatch: {entry['case_id']}")
        if result_by_case[entry["case_id"]]["evidence_sha256"] != entry["evidence_sha256"]:
            raise ValueError(f"candidate/record mismatch: {entry['case_id']}")
        if record["status"] != result_by_case[entry["case_id"]]["status"]:
            raise ValueError(f"candidate/record status mismatch: {entry['case_id']}")
        for key, expected in (
            ("candidate_commit", trusted["implementation_commit"]),
            ("candidate_tree", trusted["implementation_tree"]),
            ("accepted_contract_commit", trusted["accepted_contract_commit"]),
            ("accepted_contract_tree", trusted["accepted_contract_tree"]),
        ):
            if record[key] != expected:
                raise ValueError(f"record binding mismatch: {entry['case_id']}:{key}")
        if frozenset(record["result_payload"]) != RESULT_KEYS:
            raise ValueError(f"payload schema mismatch: {entry['case_id']}")
        if digest(record["result_payload"]) != record["result_payload_sha256"]:
            raise ValueError(f"payload identity mismatch: {entry['case_id']}")

    # Hash validation alone cannot establish execution.  Recreate the exact
    # candidate from caller-trusted bindings and independently rerun every
    # repository producer; the external disposition is revalidated against
    # its separately supplied trusted hash.
    with tempfile.TemporaryDirectory(prefix="issue72-q0-verify-") as temp_name:
        rerun_root = Path(temp_name) / "evidence"
        rerun_args = argparse.Namespace(
            repo_root=args.repo_root,
            candidate=args.candidate,
            candidate_tree=args.candidate_tree,
            accepted_contract=args.accepted_contract,
            accepted_contract_tree=args.accepted_contract_tree,
            generated_at=report["generated_at"],
            account_disposition=args.account_disposition,
            account_disposition_sha256=args.account_disposition_sha256,
            output=rerun_root,
        )
        generate(rerun_args)
        expected_files = [
            Path("q0_candidate.json"),
            Path("manifest.json"),
            *(Path("records") / f"{case_id}.json" for case_id in CASE_IDS),
        ]
        for relative in expected_files:
            if (evidence / relative).read_bytes() != (rerun_root / relative).read_bytes():
                raise ValueError(f"independent producer rerun mismatch: {relative.as_posix()}")
    return {
        "status": "VERIFIED",
        "manifest_sha256": manifest_hash,
        "q0_candidate_sha256": report_hash,
    }


def parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    result = argparse.ArgumentParser(description="Issue #72 provider-free Q0 evidence")
    commands = result.add_subparsers(dest="command", required=True)
    create = commands.add_parser("generate")
    create.add_argument("--repo-root", type=Path, default=root)
    create.add_argument("--candidate", required=True)
    create.add_argument("--candidate-tree")
    create.add_argument("--accepted-contract", required=True)
    create.add_argument("--accepted-contract-tree")
    create.add_argument("--generated-at", required=True)
    create.add_argument("--account-disposition", type=Path)
    create.add_argument("--account-disposition-sha256")
    create.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("verify")
    check.add_argument("--evidence", type=Path, required=True)
    check.add_argument("--repo-root", type=Path, default=root)
    check.add_argument("--candidate", required=True)
    check.add_argument("--candidate-tree", required=True)
    check.add_argument("--accepted-contract", required=True)
    check.add_argument("--accepted-contract-tree", required=True)
    check.add_argument("--account-disposition", type=Path, required=True)
    check.add_argument("--account-disposition-sha256", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    result = generate(args) if args.command == "generate" else verify(args)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
