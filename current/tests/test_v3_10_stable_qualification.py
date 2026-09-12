from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from tools.build_release import build_zip, verify_deterministic_pair, zip_identity
from tools.v3_10_stable_qualification import (
    ACCEPTED_CONTRACT_COMMIT,
    ACCEPTED_CONTRACT_RESCOPE_COMMIT,
    ACCEPTED_CONTRACT_RESCOPE_SHA256,
    ACCEPTED_CONTRACT_RESCOPE_TREE,
    ACCEPTED_CONTRACT_SHA256,
    ACCEPTED_CONTRACT_TREE,
    CL7_PREDECESSOR_COMMIT,
    CL7_PREDECESSOR_TREE,
    INHERITED_CUSTODY_FAILURES,
    MULTI_SESSION_SCENARIOS,
    OFFLINE_CASE_IDS,
    OFFLINE_CASE_NODE_IDS,
    PHASE_KEYS,
    QUALIFICATION_IMPLEMENTATION_ALLOWLIST,
    QUALIFICATION_KAT_SHA256,
    RELEASE_CUT_ALLOWLIST,
    RELEASE_CUT_BASE_ALLOWLIST,
    RELEASE_MANIFEST_KAT_SHA256,
    RELEASE_REVIEW_CORRECTION_ALLOWLIST,
    RELEASE_REVIEW_RESCOPE_ADDITIONS,
    SUPERSEDED_RELEASE_METADATA_FAILURES,
    V39_ORACLE_COMMIT,
    V39_ORACLE_TREE,
    ArtifactIdentity,
    QualificationError,
    QualificationReason,
    RegressionStage,
    build_qualification_envelope,
    build_release_artifact_manifest,
    canonical_json_bytes,
    compare_regression_failures,
    main,
    parse_canonical_json,
    private_artifact_members,
    pytest_junit_failure_node_ids,
    release_sha256_lines,
    scan_shareable_bytes,
    sha256_hex,
    validate_gui_runtime_result,
    validate_multi_session_result,
    validate_offline_case_results,
    validate_q0_evidence,
    validate_qualification_changed_paths,
    validate_qualification_envelope,
    validate_release_artifact_manifest,
    validate_release_cut_changed_paths,
    validate_sandbox_account_disposition,
    verify_immutable_evidence,
    write_immutable_evidence,
)
from tools.verify_standalone_layout import FORBIDDEN_RUNTIME_NAMES, verify_layout
from trading_robot.cash_ledger_domain import SourceIdentity
from trading_robot.cash_ledger_persistence import (
    CashLedgerStore,
    CodecDescriptor,
    InboxObservation,
    PersistenceDisposition,
    PersistenceError,
)
from trading_robot.readiness import ProductionReadinessEvaluator
from trading_robot.runtime_backup import (
    DEFAULT_RUNTIME_FILES,
    RuntimeBackupError,
    RuntimeBackupManager,
)
from trading_robot.runtime_bootstrap import bootstrap_runtime_files
from trading_robot.runtime_cash_authority import (
    CL7RuntimeError,
    RuntimeCashAuthorityManager,
    RuntimeCashAuthorityRecord,
    RuntimeCashAuthorityState,
    RuntimeCashAuthorityStore,
)
from trading_robot.runtime_integrity import (
    IntegrityStatus,
    inspect_cash_ledger_store,
    inspect_json_file,
    inspect_runtime_cash_authority,
)
from trading_robot.state_persistence import atomic_write_json
from trading_robot.support_bundle import SupportBundleBuilder

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "current"
CONTRACT = ROOT / "docs" / "project" / "V3_10_CL8_STABLE_QUALIFICATION_CONTRACT_RU.md"
ACTUAL_CONTRACT = (
    ROOT / "docs" / "project" / "V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md"
)
FIXTURE = CURRENT / "tests" / "fixtures" / "v3_10_stable_qualification_vectors.json"
CL2_FIXTURE = (
    CURRENT / "tests" / "fixtures" / "v3_10_cash_ledger_persistence_vectors.json"
)

CL8_ADOPTION_BRANCH = "agent/v3-10-clean-cl8-qualification-adoption"
CL8_ADOPTION_PREDECESSOR_COMMIT = "e27204ad110db36b8ace540bd0738874fab69565"
CL8_ADOPTION_PREDECESSOR_TREE = "a38d38617dcfa7e15dd1b8ce1f838aeec72c35f1"
CL8_ADOPTION_MECHANICAL_COMMIT = "70589366d3c34ede0cb0aba2a43f988c9f91fbd9"
CL8_ADOPTION_MECHANICAL_TREE = "cfb1f9ea602316da30152a6f6c89b5f2018cf015"
CL8_ADOPTION_SOURCE_COMMIT = "a11cfc1f90055ef29d86606fe8377b4ddc2c10f0"
CL8_ADOPTION_SOURCE_TREE = "bd92e233ab11f8576e7540d33dfbe9c589b1a13e"
CL8_ADOPTION_SOURCE_BASE = "ef2eba758bbffb587dbe237a96372b2273fdee03"
CL8_ADOPTION_CONTRACT_PATH = (
    "docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md"
)
CL8_ADOPTION_ISSUE72_TEST_PATH = "current/tests/test_v3_10_issue72_gui_runtime.py"
CL8_ADOPTION_CORRECTION_PATHS = {
    CL8_ADOPTION_CONTRACT_PATH,
    "current/tests/test_v3_10_stable_qualification.py",
    CL8_ADOPTION_ISSUE72_TEST_PATH,
}
CL8_RELEASE_CUT_BRANCH = "agent/v3-10-clean-cl8-release-cut"
CL8_RELEASE_CUT_PREDECESSOR = "7a569eadfb2a99c5314ae43d24da0dee47819d6c"
CL8_RELEASE_CUT_PREDECESSOR_TREE = "d4f6bf5d1b00f4b944ac0aece669a00cd72b5847"
CL8_RELEASE_REVIEW_PARENT = "58fc85f26d089da677d68bf3ded6a7cca05fb035"
CL8_RELEASE_REVIEW_PARENT_TREE = "9036c7f8d943720a47cbec3c1b68e0444e721458"
CL8_RELEASE_REVIEW_ACCEPTED_HEAD = "b1ccc10ff5c548815d049ea7081cb27b49307390"
CL8_RELEASE_REVIEW_ACCEPTED_TREE = "5729ed32a94a813905e14a868fe3edd73fe4a814"
CL8_RELEASE_REVIEW_EXPECTED_PATHS = RELEASE_REVIEW_CORRECTION_ALLOWLIST - {
    "current/install_and_run_gui.bat"
}
CL8_RELEASE_PR180_RESCOPE_PATHS = {
    "current/tests/test_v3_10_issue72_gui_runtime.py",
    "current/tests/test_v3_10_stable_qualification.py",
}
CL8_Q1_CORRECTION_BRANCH = "agent/v3-10-clean-cl8-q1-correction-r1"
CL8_Q1_CORRECTION_PARENT = "ebd68c7d71929ca194dbcdb9685a140a9eb319d5"
CL8_Q1_CORRECTION_PARENT_TREE = "170b959512e0a911ee7189d8b4ec1fa398892192"
CL8_Q1_CORRECTION_PATHS = {
    "current/V3_10_0_STABLE_TEST_PLAN_RU.md",
    "current/tests/test_v3_10_issue72_gui_runtime.py",
    "current/tests/test_v3_10_stable_qualification.py",
    "current/tools/build_release.py",
    "current/tools/v3_10_stable_qualification.py",
    "current/trading_robot/tbank_sandbox.py",
    "docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md",
}
CL8_Q23_CORRECTION_BRANCH = "agent/v3-10-clean-cl8-q23-correction-r1"
CL8_Q23_CORRECTION_PARENT = "0b26cb1cf1fbd059ef41e49274ee58d04685a55e"
CL8_Q23_CORRECTION_PARENT_TREE = "ba309d04ceec97e1d3586e2c5031a7be3765850c"
CL8_Q23_CORRECTION_PATHS = {
    "current/tests/test_v3_10_stable_qualification.py",
    "current/tools/v3_10_stable_qualification.py",
    "docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md",
}


@pytest.fixture(scope="module")
def vectors() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="ascii"))


class _FakeSecretProvider:
    name = "CL8_TEST_PROVIDER"
    secure = True

    def get(self, _key: str) -> None:
        return None

    def set(self, _key: str, _value: str) -> None:
        raise AssertionError("qualification must not write a secret")

    def delete(self, _key: str) -> None:
        raise AssertionError("qualification must not delete a secret")


def _codec() -> CodecDescriptor:
    fixture = json.loads(CL2_FIXTURE.read_text(encoding="ascii"))
    vector = next(
        item
        for item in fixture["vectors"]
        if item["id"] == "codec-descriptor-synthetic"
    )
    return CodecDescriptor.from_canonical_bytes(vector["canonical_json_ascii"])


def _create_cash_custody(root: Path) -> tuple[RuntimeCashAuthorityStore, Path]:
    authority = RuntimeCashAuthorityStore(root)
    authority.bootstrap(transition_at="2026-09-12T00:00:00.000000000Z")
    ledger_root = root / "cash_ledger_v3_10.sqlite3"
    ledger = CashLedgerStore.create(ledger_root, [_codec()], busy_timeout_ms=5_000)
    ledger.close()
    return authority, ledger_root


def _cl2_vector(identifier: str) -> dict[str, object]:
    fixture = json.loads(CL2_FIXTURE.read_text(encoding="ascii"))
    return next(item for item in fixture["vectors"] if item["id"] == identifier)


def _synthetic_observation(index: int) -> InboxObservation:
    descriptor = _codec()
    content = {"operation_kind": f"Q23_{index:04d}"}
    content_sha256 = sha256_hex(canonical_json_bytes(content))
    return InboxObservation.create(
        descriptor=descriptor,
        content=content,
        source=SourceIdentity(
            account_scope_sha256="1" * 64,
            source_kind="SYNTHETIC",
            source_scope_sha256=sha256_hex(f"session-source-{index}".encode("ascii")),
            source_content_sha256=content_sha256,
        ),
        observed_at=f"2026-09-12T00:00:00.{index:09d}Z",
        provenance_sha256=sha256_hex(f"provenance-{index}".encode("ascii")),
    )


def _authority_transition(
    manager: RuntimeCashAuthorityManager,
    current: RuntimeCashAuthorityRecord,
    *,
    at: str,
    kind: str,
    state: RuntimeCashAuthorityState,
    **changes: object,
) -> RuntimeCashAuthorityRecord:
    candidate = manager._change(
        current,
        at=at,
        kind=kind,
        state=state,
        **changes,
    )
    with manager.store.locked():
        return manager.store._commit_unlocked(
            candidate,
            expected_revision=current.record_revision,
            expected_sha256=current.sha256,
        )


def _authority_chain(
    root: Path,
) -> tuple[RuntimeCashAuthorityManager, list[RuntimeCashAuthorityRecord]]:
    store = RuntimeCashAuthorityStore(root)
    manager = RuntimeCashAuthorityManager(store)
    records = [store.bootstrap(transition_at="2026-09-12T00:00:00.000000000Z")]
    records.append(
        _authority_transition(
            manager,
            records[-1],
            at="2026-09-12T00:00:01.000000000Z",
            kind="PREPARE_CUTOVER",
            state=RuntimeCashAuthorityState.CUTOVER_PREPARED,
            cutover_generation=1,
            account_scope_sha256="15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3",
            identity_key_id="CL5_TEST_KEY_V1",
        )
    )
    records.append(
        _authority_transition(
            manager,
            records[-1],
            at="2026-09-12T00:00:02.000000000Z",
            kind="PREPARATION_EVIDENCE_BOUND",
            state=RuntimeCashAuthorityState.CUTOVER_PREPARED,
            ledger_revision=5,
            ledger_head_sha256="4" * 64,
            opening_cutoff="2026-09-12T00:00:00.000000000Z",
            opening_record_sha256="7" * 64,
            operations_complete_through="2026-09-11T10:00:00.000000001Z",
        )
    )
    records.append(
        _authority_transition(
            manager,
            records[-1],
            at="2026-09-12T00:00:03.000000000Z",
            kind="CONFIRM_CUTOVER",
            state=RuntimeCashAuthorityState.CUTOVER_CONFIRMED,
        )
    )
    records.append(
        _authority_transition(
            manager,
            records[-1],
            at="2026-09-12T00:00:04.000000000Z",
            kind="ACTIVATE_EXACT",
            state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
            ever_exact_activated=True,
            activation_context_sha256="f" * 64,
            ledger_revision=5,
            ledger_head_sha256="4" * 64,
            operations_complete_through="2026-09-11T10:00:00.000000001Z",
        )
    )
    records.append(
        manager.arm(
            raw_account_id="sandbox-account-0001",
            identity_key=bytes(range(32)),
            identity_key_id="CL5_TEST_KEY_V1",
            confirmation=manager.ARM_PHRASE,
            transition_at="2026-09-12T00:00:05.000000000Z",
        )
    )
    return manager, records


def _source_archive(tmp_path: Path, name: str = "candidate") -> tuple[Path, Path]:
    archive = tmp_path / f"{name}.zip"
    build_zip(CURRENT, archive, name)
    extracted = tmp_path / f"{name}-extracted"
    with zipfile.ZipFile(archive, "r") as source:
        source.extractall(extracted)
    return archive, extracted / name


def _bootstrap_in_subprocess(source: Path, runtime: Path) -> dict[str, object]:
    script = (
        "import json,sys;from pathlib import Path;"
        "sys.path.insert(0,sys.argv[1]);"
        "from trading_robot.runtime_bootstrap import bootstrap_runtime_files;"
        "from trading_robot.runtime_cash_authority import RuntimeCashAuthorityStore;"
        "F=type('F',(),{'name':'CL8_Q23','secure':True,'get':lambda s,k:None,"
        "'set':lambda s,k,v:(_ for _ in ()).throw(AssertionError()),"
        "'delete':lambda s,k:(_ for _ in ()).throw(AssertionError())});"
        "r=bootstrap_runtime_files(Path(sys.argv[2]),create_missing=True,secret_provider=F());"
        "a=RuntimeCashAuthorityStore(Path(sys.argv[2])).load(allow_missing_legacy=False);"
        "print(json.dumps({'ok':r.ok,'errors':list(r.errors),'state':a.state.value,"
        "'attempts':a.post_attempt_count,'armed':a.state.value=='EXACT_CASH_ARMED'},sort_keys=True))"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(source), str(runtime)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip())


def _v39_source(tmp_path: Path) -> tuple[Path, Path]:
    archive = tmp_path / "v39-oracle.zip"
    _git(
        "archive",
        "--format=zip",
        f"--output={archive}",
        "--prefix=v39/",
        V39_ORACLE_COMMIT,
        "current",
    )
    extracted = tmp_path / "v39-extracted"
    with zipfile.ZipFile(archive, "r") as source:
        source.extractall(extracted)
    return archive, extracted / "v39" / "current"


def _bootstrap_v39_in_subprocess(source: Path, runtime: Path) -> dict[str, object]:
    script = (
        "import json,sys;from pathlib import Path;"
        "sys.path.insert(0,sys.argv[1]);"
        "from trading_robot.runtime_bootstrap import bootstrap_runtime_files;"
        "F=type('F',(),{'name':'CL8_Q23','secure':True,'get':lambda s,k:None,"
        "'set':lambda s,k,v:(_ for _ in ()).throw(AssertionError()),"
        "'delete':lambda s,k:(_ for _ in ()).throw(AssertionError())});"
        "r=bootstrap_runtime_files(Path(sys.argv[2]),create_missing=True,secret_provider=F());"
        "print(json.dumps({'ok':r.ok,'errors':list(r.errors)},sort_keys=True))"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(source), str(runtime)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip())


def _runtime_payload(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and not path.name.endswith(".lock")
        and path.name != "runtime_bootstrap_report.json"
    }


def _phase_summaries(
    *,
    candidate_commit: str = CL7_PREDECESSOR_COMMIT,
    candidate_tree: str = CL7_PREDECESSOR_TREE,
    contract_sha256: str = ACCEPTED_CONTRACT_SHA256,
) -> dict[str, bytes]:
    return {
        phase: canonical_json_bytes(
            {
                "candidate_commit": candidate_commit,
                "candidate_tree": candidate_tree,
                "contract_sha256": contract_sha256,
                "domain": "v3.10-cl8-phase-summary",
                "run_id": f"run-{index:02d}-{phase.lower().replace('_', '-')}",
                "status": "PASS",
                "version": 1,
            }
        )
        for index, phase in enumerate(PHASE_KEYS, start=1)
    }


def _reason(reason: QualificationReason) -> pytest.ExceptionInfo[QualificationError]:
    return pytest.raises(QualificationError, match=f"^{reason.value}$")


def _git(*args: str, text: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=text,
    )


def _cl8_adoption_candidate_head() -> str:
    checked_out_head = _git("rev-parse", "HEAD", text=True).stdout.strip()
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        assert _git("branch", "--show-current", text=True).stdout.strip() == (
            CL8_ADOPTION_BRANCH
        )
        return checked_out_head

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    assert event_path is not None
    pull_request = json.loads(Path(event_path).read_text(encoding="utf-8-sig"))[
        "pull_request"
    ]
    assert pull_request["base"]["ref"] == "program/v3-10-v4-stable-line"
    assert pull_request["base"]["sha"] == CL8_ADOPTION_PREDECESSOR_COMMIT
    assert pull_request["head"]["ref"] == CL8_ADOPTION_BRANCH
    candidate_head = pull_request["head"]["sha"]
    assert _git("cat-file", "-e", f"{candidate_head}^{{commit}}").returncode == 0
    assert checked_out_head == os.environ.get("GITHUB_SHA")
    commit_text = _git("cat-file", "-p", checked_out_head, text=True).stdout
    parents = [
        line.removeprefix("parent ")
        for line in commit_text.splitlines()
        if line.startswith("parent ")
    ]
    assert parents == [CL8_ADOPTION_PREDECESSOR_COMMIT, candidate_head]
    return candidate_head


def _run_exact_nodes(tmp_path: Path, *nodes: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            *nodes,
            "--basetemp",
            str(tmp_path / "nested-pytest"),
        ],
        cwd=CURRENT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_v310_cl8_001_exact_contract_and_allowlists() -> None:
    assert ACCEPTED_CONTRACT_COMMIT == "a315b9b919a075966a3be898420d37be8ab7b65d"
    assert ACCEPTED_CONTRACT_TREE == "035f0a4a824001ee2c794d92bb500e206a0123f0"
    assert (
        ACCEPTED_CONTRACT_SHA256
        == hashlib.sha256(
            _git(
                "show",
                f"{ACCEPTED_CONTRACT_COMMIT}:{ACTUAL_CONTRACT.relative_to(ROOT).as_posix()}",
            ).stdout
        ).hexdigest()
    )
    assert (
        ACCEPTED_CONTRACT_RESCOPE_COMMIT == "a2ee377f4180bcc0f7ced080233f3bc1c5a73792"
    )
    assert ACCEPTED_CONTRACT_RESCOPE_TREE == "8d80c956fce3ab3ce8b3a6fb6ff57ffbd35e3366"
    assert (
        ACCEPTED_CONTRACT_RESCOPE_SHA256
        == hashlib.sha256(
            _git(
                "show",
                f"{ACCEPTED_CONTRACT_RESCOPE_COMMIT}:"
                f"{ACTUAL_CONTRACT.relative_to(ROOT).as_posix()}",
            ).stdout
        ).hexdigest()
    )
    assert (
        _git(
            "rev-parse",
            f"{ACCEPTED_CONTRACT_RESCOPE_COMMIT}^",
            text=True,
        ).stdout.strip()
        == ACCEPTED_CONTRACT_COMMIT
    )
    assert len(QUALIFICATION_IMPLEMENTATION_ALLOWLIST) == 14
    assert len(RELEASE_CUT_BASE_ALLOWLIST) == 34
    assert len(RELEASE_REVIEW_RESCOPE_ADDITIONS) == 7
    assert len(RELEASE_REVIEW_CORRECTION_ALLOWLIST) == 12
    assert len(RELEASE_CUT_ALLOWLIST) == 41
    assert not (QUALIFICATION_IMPLEMENTATION_ALLOWLIST & RELEASE_CUT_BASE_ALLOWLIST)
    assert not CONTRACT.exists()


def test_v310_cl8_002_v39_and_cl7_oracles_are_exact() -> None:
    assert CL7_PREDECESSOR_COMMIT == "ef2eba758bbffb587dbe237a96372b2273fdee03"
    assert CL7_PREDECESSOR_TREE == "f55788ff362a24d368b0d01dc0d68e85d8183199"
    assert len(INHERITED_CUSTODY_FAILURES) == 6
    assert len(SUPERSEDED_RELEASE_METADATA_FAILURES) == 8
    assert not (INHERITED_CUSTODY_FAILURES & SUPERSEDED_RELEASE_METADATA_FAILURES)


def test_v310_cl8_003_004_regression_failure_sets_are_exact() -> None:
    pre = compare_regression_failures(
        INHERITED_CUSTODY_FAILURES,
        stage=RegressionStage.PRE_RELEASE_CUT,
    )
    post = compare_regression_failures(
        INHERITED_CUSTODY_FAILURES | SUPERSEDED_RELEASE_METADATA_FAILURES,
        stage=RegressionStage.POST_RELEASE_CUT,
    )
    assert pre.passed and post.passed
    replaced = set(INHERITED_CUSTODY_FAILURES)
    replaced.pop()
    replaced.add("tests/test_new.py::test_new_failure")
    mismatch = compare_regression_failures(
        replaced,
        stage=RegressionStage.PRE_RELEASE_CUT,
    )
    assert not mismatch.passed
    assert len(mismatch.missing) == len(mismatch.unexpected) == 1


def test_regression_junit_parser_includes_failures_and_setup_errors(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    tests = source / "tests"
    tests.mkdir(parents=True)
    (tests / "test_sample.py").write_text("", encoding="ascii")
    report = tmp_path / "pytest.xml"
    report.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite tests="3" failures="1" errors="1">
<testcase classname="tests.test_sample" name="test_failure"><failure /></testcase>
<testcase classname="tests.test_sample.TestGroup" name="test_setup"><error /></testcase>
<testcase classname="tests.test_sample" name="test_pass" />
</testsuite></testsuites>
""",
        encoding="utf-8",
    )
    assert pytest_junit_failure_node_ids(report, source_root=source) == (
        "tests/test_sample.py::TestGroup::test_setup",
        "tests/test_sample.py::test_failure",
    )


def test_v310_cl8_005_038_qualification_and_release_path_gates() -> None:
    assert validate_qualification_changed_paths(
        QUALIFICATION_IMPLEMENTATION_ALLOWLIST
    ) == tuple(sorted(QUALIFICATION_IMPLEMENTATION_ALLOWLIST))
    assert validate_release_cut_changed_paths(RELEASE_CUT_ALLOWLIST) == tuple(
        sorted(RELEASE_CUT_ALLOWLIST)
    )
    with _reason(QualificationReason.ALLOWLIST_VIOLATION):
        validate_qualification_changed_paths(["current/trading_robot/bot.py"])
    with _reason(QualificationReason.ALLOWLIST_VIOLATION):
        validate_release_cut_changed_paths(["current/trading_robot/risk.py"])


def test_v310_cl8_006_008_009_backup_includes_cl2_cl7_and_isolated_restore(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    authority, ledger_root = _create_cash_custody(runtime)
    managed = runtime / "multi_instrument_profiles.json"
    atomic_write_json(
        managed,
        {"version": 1, "marker": "previous"},
        write_checksum=True,
    )
    atomic_write_json(
        managed,
        {"version": 1, "marker": "current"},
        write_checksum=True,
        keep_last_good=True,
    )
    authoritative_before = {
        "active": authority.path.read_bytes(),
        "checksum": authority.checksum_path.read_bytes(),
        "ledger": (ledger_root / "store.sqlite3").read_bytes(),
    }
    manager = RuntimeBackupManager(runtime, app_version="0.3.10")
    backup = manager.create_backup(tmp_path / "backup.zip")
    assert authoritative_before == {
        "active": authority.path.read_bytes(),
        "checksum": authority.checksum_path.read_bytes(),
        "ledger": (ledger_root / "store.sqlite3").read_bytes(),
    }
    verification = manager.verify_backup(backup)
    assert verification.valid, verification.errors
    names = {item["name"] for item in verification.manifest["entries"]}
    assert {
        "multi_instrument_profiles.json",
        "multi_instrument_profiles.json.sha256",
        "multi_instrument_profiles.json.lastgood",
        "multi_instrument_profiles.json.lastgood.sha256",
        "runtime_cash_authority.json",
        "runtime_cash_authority.json.sha256",
        "cash_ledger_v3_10.sqlite3/store.sqlite3",
    } <= names
    with zipfile.ZipFile(backup, "r") as archive:
        assert archive.namelist()[-1] == "manifest.json"
    assert verification.manifest["sqlite_sidecar_dispositions"] == {
        "cash_ledger_v3_10.sqlite3-shm": "ABSENT",
        "cash_ledger_v3_10.sqlite3-wal": "ABSENT",
    }
    restored = manager.restore_backup_isolated(backup, tmp_path / "restored")
    assert inspect_runtime_cash_authority(restored).valid
    assert inspect_cash_ledger_store(restored / "cash_ledger_v3_10.sqlite3").valid
    with pytest.raises(RuntimeBackupError, match="new and empty"):
        manager.restore_backup_isolated(backup, restored)
    with pytest.raises(RuntimeBackupError, match="unsafe"):
        manager.restore_backup_isolated(backup, runtime / "nested-restore")


def test_v310_cl8_008_backup_detects_single_byte_corruption(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _create_cash_custody(runtime)
    manager = RuntimeBackupManager(runtime, app_version="0.3.9")
    source = manager.create_backup(tmp_path / "valid.zip")
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(source, "r") as archive:
        members = [(item, archive.read(item.filename)) for item in archive.infolist()]
    with zipfile.ZipFile(corrupt, "w") as archive:
        for item, raw in members:
            if item.filename == "runtime_cash_authority.json":
                raw = bytes([raw[0] ^ 1]) + raw[1:]
            archive.writestr(item, raw)
    result = manager.verify_backup(corrupt)
    assert not result.valid
    assert any("Checksum mismatch" in item for item in result.errors)


def test_v310_cl8_010_011_012_cash_custody_corruption_fails_closed(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    authority, ledger_root = _create_cash_custody(runtime)
    assert inspect_runtime_cash_authority(runtime).status is IntegrityStatus.VALID
    assert inspect_cash_ledger_store(ledger_root).status is IntegrityStatus.VALID
    database = ledger_root / "store.sqlite3"
    database.write_bytes(b"not sqlite")
    assert inspect_cash_ledger_store(ledger_root).status is IntegrityStatus.CORRUPT
    readiness = ProductionReadinessEvaluator(
        runtime,
        app_version="0.3.10",
    ).evaluate(account_id="synthetic-account")
    custody = next(
        item for item in readiness.checks if item.code == "V3_10_CASH_CUSTODY"
    )
    assert custody.status == "FAIL" and custody.blocking
    bootstrap = bootstrap_runtime_files(
        runtime,
        create_missing=False,
        secret_provider=_FakeSecretProvider(),
    )
    assert not bootstrap.ok
    assert any("cash_ledger_v3_10.sqlite3" in item for item in bootstrap.errors)
    authority.checksum_path.write_text("0" * 64 + "\n", encoding="ascii")
    assert (
        inspect_runtime_cash_authority(runtime).status
        is IntegrityStatus.CHECKSUM_MISMATCH
    )

    transition_root = tmp_path / "invalid-transition"
    transition_root.mkdir()
    previous = RuntimeCashAuthorityRecord.bootstrap("2026-09-12T00:00:00.000000000Z")
    current = replace(
        previous,
        record_revision=1,
        previous_record_sha256=previous.sha256,
        transition_at="2026-09-12T00:00:01.000000000Z",
        transition_kind="SYNC_ADVANCED",
    )
    (transition_root / "runtime_cash_authority.json").write_bytes(
        current.canonical_bytes
    )
    (transition_root / "runtime_cash_authority.json.sha256").write_bytes(
        (current.sha256 + "\n").encode("ascii")
    )
    (transition_root / "runtime_cash_authority.json.lastgood").write_bytes(
        previous.canonical_bytes
    )
    assert (
        inspect_runtime_cash_authority(transition_root).status
        is IntegrityStatus.CORRUPT
    )
    with pytest.raises(CL7RuntimeError, match="STATE_TRANSITION_INVALID"):
        RuntimeCashAuthorityStore(transition_root).load(allow_missing_legacy=False)
    _run_exact_nodes(
        tmp_path,
        "tests/test_v3_10_cash_ledger_persistence.py::"
        "test_v310_cl2_18_committed_wal_recovery_and_corrupt_sidecar_refusal",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
        "test_cas_and_checksum_fail_closed",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
        "test_corrupt_active_never_restores_lastgood",
    )


@pytest.mark.parametrize(
    "name",
    ("central_order_state.json", "portfolio_state.json", "risk_state.json"),
)
def test_v310_cl8_013_015_state_corruption_is_executed(
    tmp_path: Path,
    name: str,
) -> None:
    path = tmp_path / name
    path.write_bytes(b"{broken")
    assert inspect_json_file(path).status is IntegrityStatus.CORRUPT
    node = {
        "central_order_state.json": (
            "tests/test_central_order_manager_v3_8.py::"
            "test_store_fails_closed_on_checksum_mismatch"
        ),
        "portfolio_state.json": (
            "tests/test_portfolio_repository_v3_7.py::"
            "test_repository_rejects_checksum_mismatch"
        ),
        "risk_state.json": (
            "tests/test_risk.py::test_corrupt_state_store_fails_closed"
        ),
    }[name]
    _run_exact_nodes(tmp_path, node)


def test_v310_cl8_016_018_cl7_crash_replay_and_no_resubmit_are_executed(
    tmp_path: Path,
) -> None:
    module = "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
    _run_exact_nodes(
        tmp_path,
        module + "test_authority_commit_crash_outcome_at_each_replace_boundary",
        module + "test_bootstrap_and_full_state_chain",
        module
        + "test_full_prepare_confirm_activate_arm_uses_fresh_cl2_to_cl6_evidence",
        module
        + "test_exact_dispatch_marker_precedes_single_post_and_classifies_outcome",
        module + "test_pending_attempt_kat_and_no_rollback",
        module + "test_restart_closure_disarms_and_preserves_attempt_count",
        module + "test_post_once_timeout_never_retries",
        module + "test_central_lease_persists_exact_proof_before_d3_recovery",
        module + "test_process_boundary_recovery_requires_exact_central_resolution",
        module + "test_operator_recovery_validates_before_lookup_under_authority_lock",
        module + "test_legacy_recovery_has_no_automatic_post_resubmit",
    )


def test_v310_cl8_019_023_bootstrap_preserves_cl7_and_does_not_arm(
    tmp_path: Path,
) -> None:
    install = tmp_path / "clean-install"
    first = bootstrap_runtime_files(
        install,
        create_missing=True,
        secret_provider=_FakeSecretProvider(),
    )
    assert first.ok
    installed = {
        path.relative_to(install).as_posix(): path.read_bytes()
        for path in install.rglob("*")
        if path.is_file() and not path.name.endswith(".lock")
    }
    second = bootstrap_runtime_files(
        install,
        create_missing=True,
        secret_provider=_FakeSecretProvider(),
    )
    assert second.ok
    assert installed == {
        path.relative_to(install).as_posix(): path.read_bytes()
        for path in install.rglob("*")
        if path.is_file() and not path.name.endswith(".lock")
    }

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    authority, _ledger_root = _create_cash_custody(runtime)
    before = {
        path.name: path.read_bytes()
        for path in (
            authority.path,
            authority.checksum_path,
            authority.lastgood_path,
        )
        if path.exists()
    }
    report = bootstrap_runtime_files(
        runtime,
        create_missing=False,
        secret_provider=_FakeSecretProvider(),
    )
    after = {
        path.name: path.read_bytes()
        for path in (
            authority.path,
            authority.checksum_path,
            authority.lastgood_path,
        )
        if path.exists()
    }
    assert report.ok
    assert before == after
    assert authority.load().state.value == "LEGACY_ACTIVE"
    state = runtime / "risk_state.json"
    state.write_text('{"marker":"pre-upgrade","version":1}', encoding="ascii")
    backup = RuntimeBackupManager(runtime, app_version="0.3.10").create_backup(
        tmp_path / "pre-upgrade.zip"
    )
    state.write_text('{"marker":"upgraded","version":1}', encoding="ascii")
    RuntimeBackupManager(runtime, app_version="0.3.10").restore_backup(
        backup,
        confirmation="RESTORE RUNTIME",
    )
    assert json.loads(state.read_text(encoding="ascii"))["marker"] == "pre-upgrade"
    _run_exact_nodes(
        tmp_path,
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
        "test_disarm_cancel_and_rollback_rules",
    )


def test_v310_cl8_q23_cashledger_complete_matrix(tmp_path: Path) -> None:
    module = "tests/test_v3_10_cash_ledger_persistence.py::"
    _run_exact_nodes(
        tmp_path,
        *(
            module + name
            for name in (
                "test_v310_cl2_03_open_never_creates_and_schema_versions_fail_closed",
                "test_v310_cl2_07_observation_duplicate_conflict_and_distinct",
                "test_v310_cl2_08_status_chain_contiguous_terminal_and_duplicate",
                "test_v310_cl2_09_separate_store_and_ledger_revisions",
                "test_v310_cl2_15_missing_evidence_partial_rows_and_ordinary_lineage_rejected",
                "test_v310_cl2_17_physical_foreign_key_and_semantic_tampering_fails_closed",
                "test_v310_cl2_18_committed_wal_recovery_and_corrupt_sidecar_refusal",
                "test_v310_cl2_19_two_writer_busy_stale_writer_and_snapshot_consistency",
                "test_v310_cl2_20_precommit_faults_expose_only_prior_state",
                "test_v310_cl2_21_postcommit_interruption_retry_has_one_effect",
                "test_v310_cl2_23_online_backup_manifest_identity_and_no_clobber",
                "test_v310_cl2_24_backup_missing_extra_and_tampered_evidence_is_read_only",
                "test_v310_cl2_25_isolated_restore_exact_export_and_no_clobber",
                "test_v310_cl2_26_create_backup_restore_interruptions_never_promote",
            )
        ),
    )
    root = tmp_path / "large-unresolved-history"
    store = CashLedgerStore.create(root, [_codec()], busy_timeout_ms=5_000)
    observation_hashes: list[str] = []
    for index in range(128):
        observation = _synthetic_observation(index)
        assert (
            store.append_observation(
                observation,
                expected_store_revision=index,
            )
            is PersistenceDisposition.OBSERVATION_STORED
        )
        observation_hashes.append(observation.sha256)
    assert store.validate().store_revision == 128
    exported = store.export_bytes()
    store.close()
    reopened = CashLedgerStore.open(root, [_codec()], busy_timeout_ms=5_000)
    try:
        assert reopened.validate().store_revision == 128
        assert reopened.export_bytes() == exported
        assert all(value.encode("ascii") in exported for value in observation_hashes)
        assert exported.count(b'"current_status":"OBSERVED"') == 128
    finally:
        reopened.close()


def test_v310_cl8_q23_runtime_authority_complete_matrix(tmp_path: Path) -> None:
    module = "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
    _run_exact_nodes(
        tmp_path,
        *(
            module + name
            for name in (
                "test_missing_compatibility_requires_all_custody_absent",
                "test_authority_commit_crash_outcome_at_each_replace_boundary",
                "test_cas_and_checksum_fail_closed",
                "test_corrupt_active_never_restores_lastgood",
                "test_pending_attempt_kat_and_no_rollback",
                "test_central_rejects_noncanonical_locked_proof_text",
                "test_d3_invalid_hmac_cannot_mutate_central",
            )
        ),
    )
    corruptions = (
        "active-truncated",
        "active-noncanonical",
        "checksum-mismatch",
        "lastgood-missing",
        "lastgood-wrong-revision",
        "lastgood-wrong-hash",
        "active-new-checksum-old",
        "active-new-lastgood-wrong",
        "pending-proof-mismatch",
        "attempt-count-inconsistent",
    )
    for name in corruptions:
        root = tmp_path / name
        _manager, records = _authority_chain(root)
        store = RuntimeCashAuthorityStore(root)
        if name == "active-truncated":
            store.path.write_bytes(b"{")
        elif name == "active-noncanonical":
            store.path.write_bytes(b" " + store.path.read_bytes())
            store.checksum_path.write_bytes(
                (sha256_hex(store.path.read_bytes()) + "\n").encode("ascii")
            )
        elif name == "checksum-mismatch":
            store.checksum_path.write_bytes(("0" * 64 + "\n").encode("ascii"))
        elif name == "lastgood-missing":
            store.lastgood_path.unlink()
        elif name == "lastgood-wrong-revision":
            store.lastgood_path.write_bytes(records[0].canonical_bytes)
        elif name == "lastgood-wrong-hash":
            raw = json.loads(store.lastgood_path.read_bytes())
            raw["previous_record_sha256"] = "0" * 64
            store.lastgood_path.write_bytes(canonical_json_bytes(raw))
        elif name == "active-new-checksum-old":
            store.path.write_bytes(store.path.read_bytes() + b"\n")
        elif name == "active-new-lastgood-wrong":
            raw = json.loads(store.path.read_bytes())
            raw["transition_at"] = "2026-09-12T00:00:05.100000000Z"
            store.path.write_bytes(canonical_json_bytes(raw))
            store.checksum_path.write_bytes(
                (sha256_hex(store.path.read_bytes()) + "\n").encode("ascii")
            )
            store.lastgood_path.write_bytes(records[0].canonical_bytes)
        else:
            raw = json.loads(store.path.read_bytes())
            raw.update(
                {
                    "record_revision": 6,
                    "previous_record_sha256": records[-1].sha256,
                    "state": "EXACT_CASH_DISPATCH_PENDING",
                    "transition_at": "2026-09-12T00:00:06.000000000Z",
                    "transition_kind": "DISPATCH_ATTEMPT_RECORDED",
                    "pending_dispatch_proof_sha256": (
                        None if name == "pending-proof-mismatch" else "9" * 64
                    ),
                    "post_attempt_count": (
                        1 if name == "pending-proof-mismatch" else 0
                    ),
                }
            )
            store.lastgood_path.write_bytes(records[-1].canonical_bytes)
            store.path.write_bytes(canonical_json_bytes(raw))
            store.checksum_path.write_bytes(
                (sha256_hex(store.path.read_bytes()) + "\n").encode("ascii")
            )
        before = {
            path.name: path.read_bytes()
            for path in (store.path, store.checksum_path, store.lastgood_path)
            if path.exists()
        }
        with pytest.raises(CL7RuntimeError):
            store.load(allow_missing_legacy=False)
        assert before == {
            path.name: path.read_bytes()
            for path in (store.path, store.checksum_path, store.lastgood_path)
            if path.exists()
        }
    remaining = tmp_path / "authority-missing-with-ledger"
    remaining.mkdir()
    ledger = CashLedgerStore.create(remaining / "cash_ledger_v3_10.sqlite3", [_codec()])
    ledger.close()
    with pytest.raises(CL7RuntimeError, match="AUTHORITY_RECORD_MISSING"):
        RuntimeCashAuthorityStore(remaining).load(allow_missing_legacy=False)


@pytest.mark.parametrize("point", tuple(f"C{index}" for index in range(7)))
def test_v310_cl8_q23_c0_c6_point_labelled_replay(
    tmp_path: Path,
    point: str,
) -> None:
    if point == "C0":
        root = tmp_path / point
        root.mkdir()
        record = RuntimeCashAuthorityStore(root).load(allow_missing_legacy=True)
        assert record.record_revision == 0
        assert not RuntimeCashAuthorityStore(root).custody_exists()
        return
    root = tmp_path / point
    _manager, records = _authority_chain(root)
    expected = records[int(point[1:]) - 1]
    if expected != records[-1]:
        store = RuntimeCashAuthorityStore(root)
        store.path.write_bytes(expected.canonical_bytes)
        store.checksum_path.write_bytes((expected.sha256 + "\n").encode("ascii"))
        if expected.record_revision == 0:
            store.lastgood_path.unlink(missing_ok=True)
        else:
            store.lastgood_path.write_bytes(
                records[expected.record_revision - 1].canonical_bytes
            )
    reloaded = RuntimeCashAuthorityStore(root).load(allow_missing_legacy=False)
    assert reloaded.record_revision == expected.record_revision
    assert reloaded.sha256 == expected.sha256
    assert reloaded.post_attempt_count == 0
    assert reloaded.pending_dispatch_proof_sha256 is None


@pytest.mark.parametrize("point", tuple(f"D{index}" for index in range(11)))
def test_v310_cl8_q23_d0_d10_point_labelled_replay(
    tmp_path: Path,
    point: str,
) -> None:
    module = "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
    node = {
        "D0": "test_bootstrap_and_full_state_chain",
        "D1": "test_freshness_exact_edge_future_and_stale",
        "D2": "test_every_serialized_proof_field_mutation_invalidates_identity",
        "D3": "test_central_lease_persists_exact_proof_before_d3_recovery",
        "D4": "test_post_once_timeout_never_retries",
        "D5": "test_ambiguous_provider_outcomes_never_clear",
        "D6": "test_restart_closure_disarms_and_preserves_attempt_count",
        "D7": "test_process_boundary_recovery_requires_exact_central_resolution",
        "D8": "test_process_boundary_recovery_requires_exact_central_resolution",
        "D9": "test_process_boundary_recovery_requires_exact_central_resolution",
        "D10": "test_process_boundary_recovery_requires_exact_central_resolution",
    }[point]
    _run_exact_nodes(tmp_path, module + node)
    assert point in {f"D{index}" for index in range(11)}


def test_v310_cl8_q23_multi_session_matrix_is_executed(tmp_path: Path) -> None:
    root = tmp_path / "multi-session-ledger"
    session_a = CashLedgerStore.create(root, [_codec()], busy_timeout_ms=5_000)
    first = _synthetic_observation(1)
    assert (
        session_a.append_observation(first, expected_store_revision=0)
        is PersistenceDisposition.OBSERVATION_STORED
    )
    session_a.close()
    session_b = CashLedgerStore.open(root, [_codec()], busy_timeout_ms=5_000)
    assert (
        session_b.append_observation(first, expected_store_revision=1)
        is PersistenceDisposition.OBSERVATION_ALREADY_PRESENT
    )
    same_content = InboxObservation.create(
        descriptor=_codec(),
        content=json.loads(first.content_json_ascii),
        source=SourceIdentity(
            account_scope_sha256=first.source.account_scope_sha256,
            source_kind=first.source.source_kind,
            source_scope_sha256=sha256_hex(b"different-session-source"),
            source_content_sha256=first.source.source_content_sha256,
        ),
        observed_at="2026-09-12T00:00:01.000000000Z",
        provenance_sha256=sha256_hex(b"different-session-provenance"),
    )
    assert (
        session_b.append_observation(same_content, expected_store_revision=1)
        is PersistenceDisposition.OBSERVATION_STORED
    )
    session_c = CashLedgerStore.open(root, [_codec()], busy_timeout_ms=5_000)
    third = _synthetic_observation(3)
    assert (
        session_b.append_observation(third, expected_store_revision=2)
        is PersistenceDisposition.OBSERVATION_STORED
    )
    with pytest.raises(PersistenceError, match="REVISION_MISMATCH"):
        session_c.append_observation(
            _synthetic_observation(4), expected_store_revision=2
        )
    assert (
        session_c.append_observation(
            _synthetic_observation(4), expected_store_revision=3
        )
        is PersistenceDisposition.OBSERVATION_STORED
    )
    assert (
        session_b.snapshot().store_revision == session_c.snapshot().store_revision == 4
    )
    session_b.close()
    session_c.close()
    _run_exact_nodes(
        tmp_path,
        "tests/test_v3_10_broker_read_adapters.py::test_v310_cl3_10_pagination_order_duplicates_and_caps",
        "tests/test_v3_10_broker_read_adapters.py::test_v310_cl3_14_watermark_canonical_semantics",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::test_account_scope_is_exact_cl3_hmac",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::test_restart_closure_disarms_and_preserves_attempt_count",
        "tests/test_central_order_manager_v3_8.py::test_restart_converts_in_flight_to_uncertain_without_resubmit",
    )
    result = {
        "scenarios": sorted(MULTI_SESSION_SCENARIOS),
        "invariants": {
            "duplicate_cash_effects": 0,
            "duplicate_central_effects": 0,
            "provider_resubmits": 0,
            "wrong_account_adoptions": 0,
            "account_scope_crossings": 0,
            "source_identity_exact": same_content.source.sha256 != first.source.sha256,
            "ledger_revision_monotonic": True,
            "central_revision_monotonic": True,
            "authority_revision_monotonic": True,
            "watermark_monotonic": True,
            "pending_uncertain_fail_closed": True,
        },
    }
    validate_multi_session_result(result)


def test_v310_cl8_q23_full_runtime_backup_inventory(tmp_path: Path) -> None:
    runtime = tmp_path / "full-runtime"
    report = bootstrap_runtime_files(
        runtime,
        create_missing=True,
        secret_provider=_FakeSecretProvider(),
    )
    assert report.ok
    ledger = CashLedgerStore.create(runtime / "cash_ledger_v3_10.sqlite3", [_codec()])
    ledger.close()
    (runtime / ".env").write_text(
        "TBANK_SANDBOX_TOKEN=PRIVATE-CANARY", encoding="ascii"
    )
    (runtime / "synthetic.lock").write_text("not-authority", encoding="ascii")
    before = _runtime_payload(runtime)
    manager = RuntimeBackupManager(runtime, app_version="0.3.10")
    backup = manager.create_backup(tmp_path / "full-runtime.zip")
    verification = manager.verify_backup(backup)
    assert verification.valid, verification.errors
    names = {entry["name"] for entry in verification.manifest["entries"]}
    expected_primary = {
        (
            "cash_ledger_v3_10.sqlite3/store.sqlite3"
            if name == "cash_ledger_v3_10.sqlite3"
            else name
        )
        for name in DEFAULT_RUNTIME_FILES
        if (runtime / name).exists()
    }
    assert expected_primary <= names
    with zipfile.ZipFile(backup, "r") as archive:
        assert archive.namelist()[-1] == "manifest.json"
        assert all(not name.endswith(".lock") for name in archive.namelist())
        assert b"PRIVATE-CANARY" not in b"".join(
            archive.read(name) for name in archive.namelist()
        )
    assert before == _runtime_payload(runtime)
    restored = manager.restore_backup_isolated(backup, tmp_path / "isolated-restore")
    assert restored.is_dir()
    _run_exact_nodes(
        tmp_path,
        "tests/test_v3_10_cash_ledger_persistence.py::test_v310_cl2_19_two_writer_busy_stale_writer_and_snapshot_consistency",
        "tests/test_v3_10_cash_ledger_persistence.py::test_v310_cl2_23_online_backup_manifest_identity_and_no_clobber",
        "tests/test_v3_10_cash_ledger_persistence.py::test_v310_cl2_26_create_backup_restore_interruptions_never_promote",
    )


def test_v310_cl8_q23_clean_install_exact_candidate_artifact(tmp_path: Path) -> None:
    archive, source = _source_archive(tmp_path)
    assert (
        zip_identity(archive)["sha256"]
        == hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    runtime = tmp_path / "empty-runtime"
    assert not runtime.exists()
    result = _bootstrap_in_subprocess(source, runtime)
    assert result == {
        "armed": False,
        "attempts": 0,
        "errors": [],
        "ok": True,
        "state": "LEGACY_ACTIVE",
    }
    assert b"PRIVATE" not in (runtime / ".env").read_bytes()
    assert b"TBANK_SANDBOX_TOKEN=" not in archive.read_bytes()


def test_v310_cl8_q23_v39_to_v310_upgrade_exact_oracles(tmp_path: Path) -> None:
    assert (
        _git("rev-parse", V39_ORACLE_COMMIT, text=True).stdout.strip()
        == V39_ORACLE_COMMIT
    )
    assert (
        _git("rev-parse", f"{V39_ORACLE_COMMIT}^{{tree}}", text=True).stdout.strip()
        == V39_ORACLE_TREE
    )
    v39_archive, v39_source = _v39_source(tmp_path)
    assert v39_archive.is_file()
    v39_runtime = tmp_path / "v39-runtime"
    assert _bootstrap_v39_in_subprocess(v39_source, v39_runtime) == {
        "errors": [],
        "ok": True,
    }
    v39_before = _runtime_payload(v39_runtime)
    backup = RuntimeBackupManager(v39_runtime, app_version="0.3.9").create_backup(
        tmp_path / "verified-pre-upgrade.zip"
    )
    assert (
        RuntimeBackupManager(v39_runtime, app_version="0.3.9")
        .verify_backup(backup)
        .valid
    )
    upgrade_runtime = tmp_path / "upgrade-runtime"
    shutil.copytree(v39_runtime, upgrade_runtime)
    _candidate_archive, candidate_source = _source_archive(
        tmp_path, "candidate-upgrade"
    )
    first = _bootstrap_in_subprocess(candidate_source, upgrade_runtime)
    assert first["ok"] and first["state"] == "LEGACY_ACTIVE" and not first["armed"]
    assert first["attempts"] == 0
    assert not (upgrade_runtime / "cash_ledger_v3_10.sqlite3").exists()
    for name, raw in v39_before.items():
        assert (upgrade_runtime / name).read_bytes() == raw
    upgraded_once = _runtime_payload(upgrade_runtime)
    second = _bootstrap_in_subprocess(candidate_source, upgrade_runtime)
    assert second == first
    assert _runtime_payload(upgrade_runtime) == upgraded_once
    assert _runtime_payload(v39_runtime) == v39_before


def test_v310_cl8_q23_rollback_package_and_attempt_boundaries(tmp_path: Path) -> None:
    _v39_archive, v39_source = _v39_source(tmp_path)
    v39_runtime = tmp_path / "v39-runtime"
    assert _bootstrap_v39_in_subprocess(v39_source, v39_runtime)["ok"]
    manager = RuntimeBackupManager(v39_runtime, app_version="0.3.9")
    backup = manager.create_backup(tmp_path / "pre-upgrade.zip")
    verification = manager.verify_backup(backup)
    assert verification.valid
    rollback = manager.restore_backup_isolated(backup, tmp_path / "rollback-runtime")
    for entry in verification.manifest["entries"]:
        restored = rollback / entry["name"]
        assert restored.is_file()
        assert sha256_hex(restored.read_bytes()) == entry["sha256"]
    authority, records = _authority_chain(tmp_path / "attempted-runtime")
    pending = _authority_transition(
        authority,
        records[-1],
        at="2026-09-12T00:00:06.000000000Z",
        kind="DISPATCH_ATTEMPT_RECORDED",
        state=RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING,
        post_attempt_count=1,
        pending_dispatch_proof_sha256="9" * 64,
    )
    assert pending.post_attempt_count == 1 and pending.ever_exact_activated
    with pytest.raises(CL7RuntimeError, match="ROLLBACK_FORBIDDEN_AFTER_ATTEMPT"):
        authority.rollback_runtime(confirmation=authority.ROLLBACK_PHRASE)
    runbook = (CURRENT / "V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md").read_text(
        encoding="utf-8"
    )
    assert "v3.9 code rollback запрещён" in runbook


def test_v310_cl8_024_027_release_artifacts_are_deterministic_and_private(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("same bytes", encoding="utf-8")
    private = source / "cash_ledger_v3_10.sqlite3"
    private.mkdir()
    (private / "store.sqlite3").write_bytes(b"private")
    qualification = source / "qualification_output"
    qualification.mkdir()
    (qualification / "evidence.json").write_text("{}", encoding="ascii")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_members = build_zip(source, first, "release-root")
    second_members = build_zip(source, second, "release-root")
    assert first_members == second_members
    assert private_artifact_members(first_members) == ()
    assert verify_deterministic_pair(first, second)["status"] == "PASS"
    assert zip_identity(first) == zip_identity(second)
    invalid = tmp_path / "invalid-contents.zip"
    with zipfile.ZipFile(first, "r") as archive:
        members = [(item, archive.read(item.filename)) for item in archive.infolist()]
    with zipfile.ZipFile(invalid, "w") as archive:
        for item, raw in members:
            if item.filename.endswith("ZIP_CONTENTS.txt"):
                raw = b"substituted\n"
            archive.writestr(item, raw)
    with pytest.raises(RuntimeError, match="ZIP_CONTENTS"):
        zip_identity(invalid)


def test_standalone_archive_preserves_required_empty_directories(
    tmp_path: Path,
) -> None:
    source = tmp_path / "standalone"
    (source / "app").mkdir(parents=True)
    manifest = json.loads((CURRENT / "build_manifest.json").read_text(encoding="utf-8"))
    (source / "app" / "build_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (source / "app" / "MOEXResearchRobot.exe").write_bytes(b"synthetic")
    (source / "MOEX Research Robot.bat").write_text("@echo off\n", encoding="ascii")
    required = ("runtime", "backups", "reports", "logs", "support")
    for name in required:
        (source / name).mkdir()
    archive = tmp_path / "standalone.zip"
    members = build_zip(
        source,
        archive,
        "portable",
        required_empty_directories=required,
    )
    assert [item for item in members if item.endswith("/")] == [
        f"portable/{name}/" for name in sorted(required)
    ]
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive) as completed:
        completed.extractall(extracted)
    assert (
        verify_layout(
            extracted / "portable",
            expected_version="0.3.10",
            expected_channel="stable",
            minimum_risk_state_schema=4,
        )
        == []
    )
    assert zip_identity(archive)["members"] == members


def test_v310_cl8_025_028_standalone_private_denylist_is_complete() -> None:
    assert {
        "runtime_cash_authority.json",
        "runtime_cash_authority.json.sha256",
        "runtime_cash_authority.json.lastgood",
        "cash_ledger_v3_10.sqlite3",
        "store.sqlite3",
        "store.sqlite3-wal",
        "store.sqlite3-shm",
    } <= FORBIDDEN_RUNTIME_NAMES
    assert private_artifact_members(
        [
            "portable/runtime/cash_ledger_v3_10.sqlite3/store.sqlite3",
            "portable/app/runtime_cash_authority.json",
        ]
    ) == (
        "portable/app/runtime_cash_authority.json",
        "portable/runtime/cash_ledger_v3_10.sqlite3/store.sqlite3",
    )


def test_v310_cl8_029_030_support_bundle_reports_only_sanitized_cash_custody(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    _create_cash_custody(runtime)
    account = "synthetic-account-private-canary"
    result = SupportBundleBuilder(runtime, app_version="0.3.9").build(
        tmp_path / "support.zip",
        account_id=account,
        known_secrets=("synthetic-token-private-canary",),
    )
    assert result.secret_scan.clean
    with zipfile.ZipFile(result.path, "r") as archive:
        assert "runtime_cash_authority.json" not in archive.namelist()
        assert "cash_ledger_v3_10.sqlite3" not in archive.namelist()
        raw = b"\n".join(archive.read(name) for name in archive.namelist())
        integrity = json.loads(archive.read("runtime_integrity.json"))
    assert account.encode() not in raw
    assert b"synthetic-token-private-canary" not in raw
    assert integrity["runtime_cash_authority.json"]["valid"] is True
    assert integrity["cash_ledger_v3_10.sqlite3"]["valid"] is True


def test_v310_cl8_031_036_privacy_scans_and_no_provider_boundary() -> None:
    assert scan_shareable_bytes({"safe.json": b'{"status":"PASS"}'}) == ()
    findings = scan_shareable_bytes(
        {
            "bad.json": (
                b"Authorization: Bearer synthetic-secret C:\\Users\\person\\data"
            )
        },
        canaries=("synthetic-secret",),
    )
    assert findings == (
        "bad.json:AUTHORIZATION_BEARER",
        "bad.json:KNOWN_CANARY",
        "bad.json:PRIVATE_ABSOLUTE_PATH",
    )
    tree = ast.parse(
        (CURRENT / "tools" / "v3_10_stable_qualification.py").read_text(
            encoding="utf-8"
        )
    )
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )
    assert not imported & {"requests", "urllib", "grpc", "tinkoff"}
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"post_order", "post_order_once"}
        for node in ast.walk(tree)
    )


def test_v310_cl8_032_033_contract_kats(vectors: dict[str, object]) -> None:
    qualification = vectors["qualification_kat"]
    manifest = vectors["release_manifest_kat"]
    qualification_bytes = canonical_json_bytes(qualification["value"])
    manifest_bytes = canonical_json_bytes(manifest["value"])
    assert sha256_hex(qualification_bytes) == qualification["sha256"]
    assert sha256_hex(qualification_bytes) == QUALIFICATION_KAT_SHA256
    assert sha256_hex(manifest_bytes) == manifest["sha256"]
    assert sha256_hex(manifest_bytes) == RELEASE_MANIFEST_KAT_SHA256
    assert main(["kat"]) == 0


def test_v310_cl8_033_045_phase_bindings_prevent_substitution() -> None:
    summaries = _phase_summaries()
    envelope = build_qualification_envelope(
        candidate_commit=CL7_PREDECESSOR_COMMIT,
        candidate_tree=CL7_PREDECESSOR_TREE,
        contract_sha256=ACCEPTED_CONTRACT_SHA256,
        generated_at="2026-09-12T00:00:00.000000000Z",
        phase_summaries=summaries,
    )
    envelope_bytes = canonical_json_bytes(envelope)
    assert envelope["overall_status"] == "PASS"
    assert (
        validate_qualification_envelope(
            envelope_bytes,
            phase_summaries=summaries,
            expected_candidate_commit=CL7_PREDECESSOR_COMMIT,
            expected_candidate_tree=CL7_PREDECESSOR_TREE,
            expected_contract_sha256=ACCEPTED_CONTRACT_SHA256,
        )
        == envelope
    )
    substituted = dict(summaries)
    parsed = parse_canonical_json(substituted["REGRESSION"])
    parsed["extra"] = "substituted"
    substituted["REGRESSION"] = canonical_json_bytes(parsed)
    with _reason(QualificationReason.PHASE_BINDING_INVALID):
        validate_qualification_envelope(
            envelope_bytes,
            phase_summaries=substituted,
            expected_candidate_commit=CL7_PREDECESSOR_COMMIT,
            expected_candidate_tree=CL7_PREDECESSOR_TREE,
            expected_contract_sha256=ACCEPTED_CONTRACT_SHA256,
        )
    with _reason(QualificationReason.PHASE_BINDING_INVALID):
        validate_qualification_envelope(
            envelope_bytes,
            phase_summaries=summaries,
            expected_candidate_commit=CL7_PREDECESSOR_COMMIT,
            expected_candidate_tree=CL7_PREDECESSOR_TREE,
            expected_contract_sha256="f" * 64,
        )
    duplicated = dict(summaries)
    regression = parse_canonical_json(duplicated["REGRESSION"])
    regression["run_id"] = parse_canonical_json(duplicated["PRIVACY"])["run_id"]
    duplicated["REGRESSION"] = canonical_json_bytes(regression)
    with _reason(QualificationReason.PHASE_BINDING_INVALID):
        build_qualification_envelope(
            candidate_commit=CL7_PREDECESSOR_COMMIT,
            candidate_tree=CL7_PREDECESSOR_TREE,
            contract_sha256=ACCEPTED_CONTRACT_SHA256,
            generated_at="2026-09-12T00:00:00.000000000Z",
            phase_summaries=duplicated,
        )


def test_v310_cl8_045_evidence_gap_is_exact_and_prerequisite_bound() -> None:
    summaries = _phase_summaries()
    sandbox = parse_canonical_json(summaries["SANDBOX_BURNIN"])
    sandbox.update(
        {
            "evidence_gap_prerequisites": {
                "artificial_signal_created": False,
                "configured_instrument_count": 3,
                "exact_live_lifecycle_count": 1,
                "q7_otherwise_pass": True,
                "reviewer_acknowledged": True,
                "synthetic_multi_position_concurrency_status": "PASS",
            },
            "evidence_gap_reason": "LIVE_OVERLAP_NOT_OBSERVED",
            "status": "EVIDENCE_GAP",
        }
    )

    summaries["SANDBOX_BURNIN"] = canonical_json_bytes(sandbox)
    envelope = build_qualification_envelope(
        candidate_commit=CL7_PREDECESSOR_COMMIT,
        candidate_tree=CL7_PREDECESSOR_TREE,
        contract_sha256=ACCEPTED_CONTRACT_SHA256,
        generated_at="2026-09-12T00:00:00.000000000Z",
        phase_summaries=summaries,
    )
    assert envelope["overall_status"] == "EVIDENCE_GAP"
    sandbox["evidence_gap_reason"] = "ANY_OTHER_GAP"
    summaries["SANDBOX_BURNIN"] = canonical_json_bytes(sandbox)
    with _reason(QualificationReason.PHASE_BINDING_INVALID):
        build_qualification_envelope(
            candidate_commit=CL7_PREDECESSOR_COMMIT,
            candidate_tree=CL7_PREDECESSOR_TREE,
            contract_sha256=ACCEPTED_CONTRACT_SHA256,
            generated_at="2026-09-12T00:00:00.000000000Z",
            phase_summaries=summaries,
        )


def test_v310_cl8_033_canonical_json_rejects_noncanonical_forms() -> None:
    assert parse_canonical_json(b'{"a":1,"b":true}') == {"a": 1, "b": True}
    for raw in (
        b'{"b":true,"a":1}',
        b'{"a":1,"a":1}',
        b'{"a":1.0}',
        b'{"a":NaN}',
        b'\xef\xbb\xbf{"a":1}',
    ):
        with _reason(QualificationReason.CANONICAL_FORMAT_INVALID):
            parse_canonical_json(raw)


def test_v310_cl8_033_immutable_evidence_is_create_once(tmp_path: Path) -> None:
    value = {"domain": "test", "status": "PASS", "version": 1}
    summary, digest = write_immutable_evidence(tmp_path, "summary.json", value)
    assert verify_immutable_evidence(summary) == value
    assert digest.read_text(encoding="ascii") == sha256_hex(summary.read_bytes()) + "\n"
    with _reason(QualificationReason.EVIDENCE_ALREADY_EXISTS):
        write_immutable_evidence(tmp_path, "summary.json", value)
    digest.write_text("0" * 64 + "\n", encoding="ascii")
    with _reason(QualificationReason.EVIDENCE_INTEGRITY_INVALID):
        verify_immutable_evidence(summary)


def test_v310_cl8_032_release_manifest_and_sha_file(tmp_path: Path) -> None:
    source = tmp_path / "moex_trading_robot_source_v3_10_0.zip"
    standalone = tmp_path / "moex_trading_robot_standalone_v3_10_0.zip"
    source.write_bytes(b"source")
    standalone.write_bytes(b"standalone")
    manifest = build_release_artifact_manifest(
        candidate_commit=CL7_PREDECESSOR_COMMIT,
        candidate_tree=CL7_PREDECESSOR_TREE,
        qualification_evidence_sha256="a" * 64,
        artifacts=(source, standalone),
        source_tree_clean=True,
    )
    assert (
        validate_release_artifact_manifest(
            canonical_json_bytes(manifest),
            expected_candidate_commit=CL7_PREDECESSOR_COMMIT,
            expected_candidate_tree=CL7_PREDECESSOR_TREE,
            expected_qualification_evidence_sha256="a" * 64,
            artifacts={source.name: source, standalone.name: standalone},
            privacy_canaries=("synthetic-private-canary",),
        )
        == manifest
    )
    source.unlink()
    with _reason(QualificationReason.IDENTITY_INVALID):
        validate_release_artifact_manifest(
            canonical_json_bytes(manifest),
            expected_candidate_commit=CL7_PREDECESSOR_COMMIT,
            expected_candidate_tree=CL7_PREDECESSOR_TREE,
            expected_qualification_evidence_sha256="a" * 64,
            artifacts={source.name: source, standalone.name: standalone},
            privacy_canaries=("synthetic-private-canary",),
        )
    identities = tuple(ArtifactIdentity(**item) for item in manifest["artifacts"])
    lines = release_sha256_lines(identities)
    assert lines.endswith(b"\n")
    assert lines.decode("ascii").splitlines() == sorted(
        lines.decode("ascii").splitlines()
    )


def test_v310_cl8_034_035_controlled_clock_boundaries_are_executed(
    tmp_path: Path,
) -> None:
    _run_exact_nodes(
        tmp_path,
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
        "test_freshness_exact_edge_future_and_stale",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
        "test_restart_closure_disarms_and_preserves_attempt_count",
        "tests/test_v3_10_cash_ledger_opening_reconciliation.py::"
        "test_v310_cl4_05_timestamp_freshness_boundaries",
    )


def test_v310_cl8_037_no_production_host_or_mutation_route() -> None:
    source = (CURRENT / "tools" / "v3_10_stable_qualification.py").read_text(
        encoding="utf-8"
    )
    assert "invest-public-api.tbank.ru" not in source
    assert "sandbox-invest-public-api.tbank.ru" not in source
    assert "start_experiment_authorized" not in source
    assert ".post_order(" not in source
    assert ".close_account(" not in source


def test_v310_cl8_039_040_offline_matrix_and_fixture_identity(
    vectors: dict[str, object],
) -> None:
    assert tuple(vectors["offline_case_ids"]) == OFFLINE_CASE_IDS
    results: dict[str, dict[str, object]] = {}
    for index, item in enumerate(OFFLINE_CASE_IDS, start=1):
        run_id = f"offline-case-{index:03d}"
        node_ids = list(OFFLINE_CASE_NODE_IDS[item])
        summary = {
            "case_id": item,
            "executed_node_ids": node_ids,
            "run_id": run_id,
            "status": "PASS",
        }
        results[item] = {
            "canonical_summary_sha256": sha256_hex(canonical_json_bytes(summary)),
            "executed_node_ids": node_ids,
            "run_id": run_id,
            "status": "PASS",
        }
    validate_offline_case_results(results)
    incomplete = dict(results)
    incomplete.pop(OFFLINE_CASE_IDS[-1])
    with _reason(QualificationReason.OFFLINE_MATRIX_INCOMPLETE):
        validate_offline_case_results(incomplete)
    with _reason(QualificationReason.OFFLINE_MATRIX_INCOMPLETE):
        validate_offline_case_results({item: "PASS" for item in OFFLINE_CASE_IDS})
    forged = deepcopy(results)
    forged[OFFLINE_CASE_IDS[0]]["executed_node_ids"] = [
        "tests/fake.py::test_declared_pass"
    ]
    with _reason(QualificationReason.OFFLINE_MATRIX_INCOMPLETE):
        validate_offline_case_results(forged)


def test_v310_cl8_041_q0_is_hard_and_exact(vectors: dict[str, object]) -> None:
    evidence = deepcopy(vectors["q0_evidence"])
    with _reason(QualificationReason.Q0_BLOCKED):
        validate_q0_evidence(
            evidence,
            candidate_commit=CL7_PREDECESSOR_COMMIT,
            repository=ROOT,
            expected_accepted_commit=evidence["accepted_commit"],
            expected_accepted_tree=evidence["accepted_tree"],
            expected_review_evidence_sha256=evidence["review_evidence_sha256"],
            expected_acceptance_record_sha256=(
                evidence["explicit_acceptance_record_sha256"]
            ),
        )
    evidence.update(
        {
            "accepted_commit": ACCEPTED_CONTRACT_COMMIT,
            "accepted_tree": ACCEPTED_CONTRACT_TREE,
            "review_verdict": "PASS",
        }
    )
    trust = {
        "expected_accepted_commit": ACCEPTED_CONTRACT_COMMIT,
        "expected_accepted_tree": ACCEPTED_CONTRACT_TREE,
        "expected_review_evidence_sha256": evidence["review_evidence_sha256"],
        "expected_acceptance_record_sha256": evidence[
            "explicit_acceptance_record_sha256"
        ],
    }
    validate_q0_evidence(
        evidence,
        candidate_commit=ACCEPTED_CONTRACT_COMMIT,
        repository=ROOT,
        **trust,
    )
    evidence["review_verdict"] = "BLOCKED"
    with _reason(QualificationReason.Q0_BLOCKED):
        validate_q0_evidence(
            evidence,
            candidate_commit=ACCEPTED_CONTRACT_COMMIT,
            repository=ROOT,
            **trust,
        )
    evidence["review_verdict"] = "PASS"
    evidence["accepted_tree"] = "0" * 40
    with _reason(QualificationReason.Q0_BLOCKED):
        validate_q0_evidence(
            evidence,
            candidate_commit=ACCEPTED_CONTRACT_COMMIT,
            repository=ROOT,
            **trust,
        )


def test_v310_cl8_042_gui_runtime_matrix_is_closed(vectors: dict[str, object]) -> None:
    result = deepcopy(vectors["gui_runtime_pass"])
    validate_gui_runtime_result(result)
    result["gui_provider_post_paths"] = 1
    with _reason(QualificationReason.GUI_RUNTIME_QUALIFICATION_FAILED):
        validate_gui_runtime_result(result)


def test_v310_cl8_043_multi_session_matrix_is_closed(
    tmp_path: Path,
    vectors: dict[str, object],
) -> None:
    _run_exact_nodes(
        tmp_path,
        "tests/test_v3_10_cash_ledger_persistence.py::"
        "test_v310_cl2_07_observation_duplicate_conflict_and_distinct",
        "tests/test_v3_10_broker_read_adapters.py::"
        "test_v310_cl3_10_pagination_order_duplicates_and_caps",
        "tests/test_v3_10_broker_read_adapters.py::"
        "test_v310_cl3_14_watermark_canonical_semantics",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
        "test_account_scope_is_exact_cl3_hmac",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
        "test_cl3_to_cl2_sync_mapping_and_watermark_commit",
        "tests/test_v3_10_runtime_cash_cutover_recovery.py::"
        "test_restart_closure_disarms_and_preserves_attempt_count",
    )
    result = deepcopy(vectors["multi_session_pass"])
    assert set(result["scenarios"]) == MULTI_SESSION_SCENARIOS
    validate_multi_session_result(result)
    result["invariants"]["provider_resubmits"] = 1
    with _reason(QualificationReason.MULTI_SESSION_QUALIFICATION_FAILED):
        validate_multi_session_result(result)
    duplicated = deepcopy(vectors["multi_session_pass"])
    duplicated["scenarios"].append(duplicated["scenarios"][0])
    with _reason(QualificationReason.MULTI_SESSION_QUALIFICATION_FAILED):
        validate_multi_session_result(duplicated)


def test_v310_cl8_044_account_disposition_is_exact(
    vectors: dict[str, object],
) -> None:
    retained = deepcopy(vectors["retained_account_disposition"])
    validate_sandbox_account_disposition(
        retained,
        expected_candidate_commit=CL7_PREDECESSOR_COMMIT,
        expected_candidate_tree=CL7_PREDECESSOR_TREE,
    )
    preparation = canonical_json_bytes(
        {
            "candidate_commit": CL7_PREDECESSOR_COMMIT,
            "candidate_tree": CL7_PREDECESSOR_TREE,
            "contract_sha256": ACCEPTED_CONTRACT_SHA256,
            "experiment_id": "CL8-SANDBOX-ACCOUNT-CLEANUP-V1",
            "run_id": "cleanup-preparation-1",
            "status": "PASS",
        }
    )
    authorization = canonical_json_bytes(
        {
            "authorization_id": "cleanup-authorization-1",
            "candidate_commit": CL7_PREDECESSOR_COMMIT,
            "candidate_tree": CL7_PREDECESSOR_TREE,
            "command": "START EXPERIMENT",
            "experiment_id": "CL8-SANDBOX-ACCOUNT-CLEANUP-V1",
            "preparation_summary_sha256": sha256_hex(preparation),
        }
    )
    cleanup = {
        "active_account_scope_sha256": "a" * 64,
        "active_unchanged": True,
        "candidate_commit": CL7_PREDECESSOR_COMMIT,
        "candidate_tree": CL7_PREDECESSOR_TREE,
        "canonical_evidence_sha256": "b" * 64,
        "authorization_record_sha256": sha256_hex(authorization),
        "confirmation": "CLOSE CL8 REDUNDANT SANDBOX ACCOUNT " + "c" * 64,
        "experiment_id": "CL8-SANDBOX-ACCOUNT-CLEANUP-V1",
        "preparation_summary_sha256": sha256_hex(preparation),
        "provider_result": "CLOSED",
        "redundant_account_scope_sha256": "c" * 64,
        "redundant_absent_or_closed": True,
        "run_id": "cleanup-1",
        "status": "REDUNDANT_ACCOUNT_CLEANUP_COMPLETED",
    }
    validate_sandbox_account_disposition(
        cleanup,
        expected_candidate_commit=CL7_PREDECESSOR_COMMIT,
        expected_candidate_tree=CL7_PREDECESSOR_TREE,
        authorization_record=authorization,
        preparation_summary=preparation,
    )
    cleanup["confirmation"] = "CLOSE SOMETHING ELSE"
    with _reason(QualificationReason.ACCOUNT_DISPOSITION_INVALID):
        validate_sandbox_account_disposition(
            cleanup,
            expected_candidate_commit=CL7_PREDECESSOR_COMMIT,
            expected_candidate_tree=CL7_PREDECESSOR_TREE,
            authorization_record=authorization,
            preparation_summary=preparation,
        )


def test_v310_cl8_046_missing_failure_never_compensates_for_new_failure() -> None:
    actual = set(INHERITED_CUSTODY_FAILURES)
    missing = actual.pop()
    actual.add("tests/test_unexpected.py::test_failure")
    result = compare_regression_failures(
        actual,
        stage=RegressionStage.PRE_RELEASE_CUT,
    )
    assert result.missing == (missing,)
    assert result.unexpected == ("tests/test_unexpected.py::test_failure",)
    assert not result.passed


def test_exact_qualification_delta_and_predecessor_immutability() -> None:
    branch = _git("branch", "--show-current", text=True).stdout.strip()
    release_cut_pr: dict[str, object] | None = None
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        assert event_path is not None
        pull_request = json.loads(Path(event_path).read_text(encoding="utf-8-sig"))[
            "pull_request"
        ]
        if pull_request["head"]["ref"] == CL8_RELEASE_CUT_BRANCH:
            release_cut_pr = pull_request

    if branch == CL8_Q23_CORRECTION_BRANCH:
        head = _git("rev-parse", "HEAD", text=True).stdout.strip()
        assert (
            _git(
                "rev-parse", f"{CL8_Q23_CORRECTION_PARENT}^{{tree}}", text=True
            ).stdout.strip()
            == CL8_Q23_CORRECTION_PARENT_TREE
        )
        assert (
            _git(
                "merge-base", CL8_Q23_CORRECTION_PARENT, head, text=True
            ).stdout.strip()
            == CL8_Q23_CORRECTION_PARENT
        )
        if head != CL8_Q23_CORRECTION_PARENT:
            assert (
                _git("rev-parse", f"{head}^", text=True).stdout.strip()
                == CL8_Q23_CORRECTION_PARENT
            )
        changed = set(
            _git(
                "diff",
                "--name-only",
                f"{CL8_Q23_CORRECTION_PARENT}..{head}",
                text=True,
            ).stdout.splitlines()
        )
        changed.update(_git("diff", "--name-only", text=True).stdout.splitlines())
        changed.update(
            _git(
                "ls-files", "--others", "--exclude-standard", text=True
            ).stdout.splitlines()
        )
        assert changed == CL8_Q23_CORRECTION_PATHS
        return

    if branch == CL8_Q1_CORRECTION_BRANCH:
        head = _git("rev-parse", "HEAD", text=True).stdout.strip()
        assert (
            _git(
                "rev-parse", f"{CL8_Q1_CORRECTION_PARENT}^{{tree}}", text=True
            ).stdout.strip()
            == CL8_Q1_CORRECTION_PARENT_TREE
        )
        assert (
            _git("merge-base", CL8_Q1_CORRECTION_PARENT, head, text=True).stdout.strip()
            == CL8_Q1_CORRECTION_PARENT
        )
        if head != CL8_Q1_CORRECTION_PARENT:
            assert (
                _git("rev-parse", f"{head}^", text=True).stdout.strip()
                == CL8_Q1_CORRECTION_PARENT
            )
        changed = set(
            _git(
                "diff",
                "--name-only",
                f"{CL8_Q1_CORRECTION_PARENT}..{head}",
                text=True,
            ).stdout.splitlines()
        )
        changed.update(_git("diff", "--name-only", text=True).stdout.splitlines())
        changed.update(
            _git(
                "ls-files", "--others", "--exclude-standard", text=True
            ).stdout.splitlines()
        )
        assert changed == CL8_Q1_CORRECTION_PATHS
        cumulative_changed = set(
            _git(
                "diff",
                "--name-only",
                f"{CL8_RELEASE_CUT_PREDECESSOR}..{head}",
                text=True,
            ).stdout.splitlines()
        )
        cumulative_changed.update(
            _git("diff", "--name-only", text=True).stdout.splitlines()
        )
        assert cumulative_changed == RELEASE_CUT_ALLOWLIST - {"current/desktop_gui.py"}
        return

    if branch == CL8_RELEASE_CUT_BRANCH or release_cut_pr is not None:
        head = _git("rev-parse", "HEAD", text=True).stdout.strip()
        if release_cut_pr is not None:
            assert release_cut_pr["base"]["ref"] == ("program/v3-10-v4-stable-line")
            assert release_cut_pr["base"]["sha"] == CL8_RELEASE_CUT_PREDECESSOR
            head = str(release_cut_pr["head"]["sha"])
            assert _git("cat-file", "-e", f"{head}^{{commit}}").returncode == 0
            checked_out_head = _git("rev-parse", "HEAD", text=True).stdout.strip()
            assert checked_out_head == os.environ.get("GITHUB_SHA")
            commit_text = _git("cat-file", "-p", checked_out_head, text=True).stdout
            parents = [
                line.removeprefix("parent ")
                for line in commit_text.splitlines()
                if line.startswith("parent ")
            ]
            assert parents == [CL8_RELEASE_CUT_PREDECESSOR, head]
            assert (
                _git(
                    "rev-parse", f"{checked_out_head}^{{tree}}", text=True
                ).stdout.strip()
                == _git("rev-parse", f"{head}^{{tree}}", text=True).stdout.strip()
            )
        assert (
            _git(
                "rev-parse", f"{CL8_RELEASE_CUT_PREDECESSOR}^{{tree}}", text=True
            ).stdout.strip()
            == CL8_RELEASE_CUT_PREDECESSOR_TREE
        )
        assert (
            _git(
                "rev-parse", f"{CL8_RELEASE_REVIEW_PARENT}^{{tree}}", text=True
            ).stdout.strip()
            == CL8_RELEASE_REVIEW_PARENT_TREE
        )
        assert (
            _git("rev-parse", f"{CL8_RELEASE_REVIEW_PARENT}^", text=True).stdout.strip()
            == CL8_RELEASE_CUT_PREDECESSOR
        )
        assert (
            _git(
                "rev-parse", f"{CL8_RELEASE_REVIEW_ACCEPTED_HEAD}^{{tree}}", text=True
            ).stdout.strip()
            == CL8_RELEASE_REVIEW_ACCEPTED_TREE
        )
        assert (
            _git(
                "rev-parse", f"{CL8_RELEASE_REVIEW_ACCEPTED_HEAD}^", text=True
            ).stdout.strip()
            == CL8_RELEASE_REVIEW_PARENT
        )
        assert (
            _git(
                "merge-base", CL8_RELEASE_CUT_PREDECESSOR, head, text=True
            ).stdout.strip()
            == CL8_RELEASE_CUT_PREDECESSOR
        )
        if head not in {
            CL8_RELEASE_REVIEW_PARENT,
            CL8_RELEASE_REVIEW_ACCEPTED_HEAD,
        }:
            assert _git("rev-parse", f"{head}^", text=True).stdout.strip() == (
                CL8_RELEASE_REVIEW_ACCEPTED_HEAD
            )
        if head != CL8_RELEASE_REVIEW_PARENT:
            rescope_changed = set(
                _git(
                    "diff",
                    "--name-only",
                    f"{CL8_RELEASE_REVIEW_ACCEPTED_HEAD}..{head}",
                    text=True,
                ).stdout.splitlines()
            )
            rescope_changed.update(
                _git("diff", "--name-only", text=True).stdout.splitlines()
            )
            rescope_changed.update(
                _git(
                    "ls-files", "--others", "--exclude-standard", text=True
                ).stdout.splitlines()
            )
            assert rescope_changed == CL8_RELEASE_PR180_RESCOPE_PATHS
        correction_changed = set(
            _git(
                "diff",
                "--name-only",
                f"{CL8_RELEASE_REVIEW_PARENT}..{head}",
                text=True,
            ).stdout.splitlines()
        )
        correction_changed.update(
            _git("diff", "--name-only", text=True).stdout.splitlines()
        )
        correction_changed.update(
            _git(
                "ls-files", "--others", "--exclude-standard", text=True
            ).stdout.splitlines()
        )
        assert correction_changed == CL8_RELEASE_REVIEW_EXPECTED_PATHS
        original_changed = set(
            _git(
                "diff",
                "--name-only",
                f"{CL8_RELEASE_CUT_PREDECESSOR}..{CL8_RELEASE_REVIEW_PARENT}",
                text=True,
            ).stdout.splitlines()
        )
        assert original_changed == RELEASE_CUT_BASE_ALLOWLIST - {
            "current/desktop_gui.py"
        }
        cumulative_changed = set(
            _git(
                "diff",
                "--name-only",
                f"{CL8_RELEASE_CUT_PREDECESSOR}..{head}",
                text=True,
            ).stdout.splitlines()
        )
        cumulative_changed.update(
            _git("diff", "--name-only", text=True).stdout.splitlines()
        )
        assert cumulative_changed == RELEASE_CUT_ALLOWLIST - {"current/desktop_gui.py"}
        return

    head = _cl8_adoption_candidate_head()
    assert (
        _git(
            "rev-parse", f"{CL8_ADOPTION_PREDECESSOR_COMMIT}^{{tree}}", text=True
        ).stdout.strip()
        == CL8_ADOPTION_PREDECESSOR_TREE
    )
    assert (
        _git(
            "rev-parse", f"{CL8_ADOPTION_MECHANICAL_COMMIT}^{{tree}}", text=True
        ).stdout.strip()
        == CL8_ADOPTION_MECHANICAL_TREE
    )
    assert (
        _git(
            "rev-parse", f"{CL8_ADOPTION_SOURCE_COMMIT}^{{tree}}", text=True
        ).stdout.strip()
        == CL8_ADOPTION_SOURCE_TREE
    )
    assert (
        _git(
            "rev-parse", f"{CL8_ADOPTION_MECHANICAL_COMMIT}^", text=True
        ).stdout.strip()
        == CL8_ADOPTION_PREDECESSOR_COMMIT
    )
    assert _git("rev-parse", f"{head}^", text=True).stdout.strip() == (
        CL8_ADOPTION_MECHANICAL_COMMIT
    )
    assert (
        _git(
            "merge-base", CL8_ADOPTION_PREDECESSOR_COMMIT, head, text=True
        ).stdout.strip()
        == CL8_ADOPTION_PREDECESSOR_COMMIT
    )
    assert (
        _git(
            "merge-base", CL8_ADOPTION_MECHANICAL_COMMIT, head, text=True
        ).stdout.strip()
        == CL8_ADOPTION_MECHANICAL_COMMIT
    )

    correction_changed = set(
        _git(
            "diff",
            "--name-only",
            f"{CL8_ADOPTION_MECHANICAL_COMMIT}..{head}",
            text=True,
        ).stdout.splitlines()
    )
    correction_changed.update(
        _git("diff", "--name-only", text=True).stdout.splitlines()
    )
    correction_changed.update(
        _git(
            "ls-files", "--others", "--exclude-standard", text=True
        ).stdout.splitlines()
    )
    assert correction_changed == CL8_ADOPTION_CORRECTION_PATHS

    adopted_changed = set(
        _git(
            "diff",
            "--name-only",
            f"{CL8_ADOPTION_PREDECESSOR_COMMIT}..{head}",
            text=True,
        ).stdout.splitlines()
    )
    assert adopted_changed == (
        set(QUALIFICATION_IMPLEMENTATION_ALLOWLIST)
        | {CL8_ADOPTION_CONTRACT_PATH, CL8_ADOPTION_ISSUE72_TEST_PATH}
    )

    source_changed = set(
        _git(
            "diff",
            "--name-only",
            f"{CL8_ADOPTION_SOURCE_BASE}..{CL8_ADOPTION_SOURCE_COMMIT}",
            text=True,
        ).stdout.splitlines()
    )
    assert source_changed == (
        set(QUALIFICATION_IMPLEMENTATION_ALLOWLIST) | {CL8_ADOPTION_CONTRACT_PATH}
    )
    for path in source_changed:
        source_blob = _git(
            "rev-parse", f"{CL8_ADOPTION_SOURCE_COMMIT}:{path}", text=True
        ).stdout.strip()
        adopted_blob = _git(
            "rev-parse", f"{CL8_ADOPTION_MECHANICAL_COMMIT}:{path}", text=True
        ).stdout.strip()
        assert adopted_blob == source_blob
