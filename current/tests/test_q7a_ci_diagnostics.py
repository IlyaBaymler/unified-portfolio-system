"""Runner qualification: real subprocesses, no trading fixtures or broker calls."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import q7a_ci_diagnostics as diag


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    return root, tmp_path / "evidence"


def make_plan(sample, text, shards=2):
    root, evidence = sample
    (root / "test_sample.py").write_text(text, encoding="utf-8")
    return diag.collect(root, evidence / "plan", shards, source_only=True)


def run_all(sample, plan):
    root, evidence = sample
    for index in range(len(plan["partitions"])):
        diag.run_shard(root, plan, index, evidence / f"shard-{index}", budget=30, batch_timeout=10)
    return diag.aggregate(plan, evidence)


def test_pass_all_unique_ids_and_empty_shard(sample):
    plan = make_plan(sample, "def test_a(): assert 1 + 1 == 2\ndef test_b(): assert True\n", shards=3)
    result = run_all(sample, plan)
    assert result["success"] and result["counts"] == {"passed": 2}
    assert result["expected_count"] == 2


def test_failure_setup_error_skip_and_parameter_ids_preserved(sample):
    plan = make_plan(sample, '''import pytest
class TestSample:
    def test_pass(self): assert True
    def test_fail(self): assert False, "controlled assertion failure"
@pytest.fixture
def broken(): raise ValueError("controlled setup error")
def test_setup(broken): pass
@pytest.mark.skip(reason="existing skip retained")
def test_skip(): pass
@pytest.mark.parametrize("value", [1, 2], ids=["x::a", "x[b]"])
def test_params(value): assert value > 0
''')
    result = run_all(sample, plan)
    assert not result["success"]
    assert result["counts"] == {"passed": 3, "error": 1, "failed": 1, "skipped": 1}
    phases = [r for p in sample[1].glob("shard-*/batch-*") for r in diag.events(p)]
    assert any("controlled assertion failure" in r.get("detail", "") for r in phases)
    assert any("controlled setup error" in r.get("detail", "") for r in phases)
    assert all("full collection != executed union" != issue for issue in result["issues"])


def test_timeout_preserves_started_id_and_fails_missing_completion(sample):
    plan = make_plan(sample, "import time\ndef test_wait(): time.sleep(20)\n", shards=1)
    root, evidence = sample
    report = diag.run_shard(root, plan, 0, evidence / "shard-0", budget=3, batch_timeout=2)
    result = diag.aggregate(plan, evidence)
    assert not report["success"] and not result["success"]
    rows = diag.events(evidence / "shard-0" / "batch-0000")
    assert [r["nodeid"] for r in rows if r["kind"] == "start"] == plan["nodeids"]
    assert report["batches"][0]["status"]["timed_out"]
    assert result["counts"] == {"incomplete": 1}


def test_abrupt_exit_not_mistaken_for_pass(sample):
    plan = make_plan(sample, "import os\ndef test_crash(): os._exit(9)\n", shards=1)
    result = run_all(sample, plan)
    receipt = diag.read_json(sample[1] / "shard-0" / "batch-0000" / "exit.json")
    assert receipt["returncode"] == 9 and not result["success"]
    assert result["counts"] == {"incomplete": 1}


def test_missing_artifact_is_not_green(sample):
    plan = make_plan(sample, "def test_a(): pass\ndef test_b(): pass\n")
    diag.run_shard(sample[0], plan, 0, sample[1] / "shard-0", budget=20)
    result = diag.aggregate(plan, sample[1])
    assert not result["success"] and "missing shard artifacts" in result["issues"]
    assert result["counts"].get("incomplete") == 1


def test_duplicate_artifact_is_rejected(sample):
    plan = make_plan(sample, "def test_a(): pass\n", shards=1)
    assert run_all(sample, plan)["success"]
    duplicate = sample[1] / "duplicate"
    duplicate.mkdir()
    (duplicate / "report.json").write_bytes((sample[1] / "shard-0" / "report.json").read_bytes())
    assert "duplicate or invalid shard" in diag.aggregate(plan, sample[1])["issues"]


def test_tampered_summary_and_junit_are_rejected(sample):
    plan = make_plan(sample, "def test_a(): assert False\n", shards=1)
    run_all(sample, plan)
    report_path = sample[1] / "shard-0" / "report.json"
    report = diag.read_json(report_path)
    report["outcomes"][plan["nodeids"][0]] = "passed"
    report["success"] = True
    diag.write_json(report_path, report)
    result = diag.aggregate(plan, sample[1])
    assert not result["success"]
    assert "summary disagrees with original phase evidence" in result["issues"]
    junit = sample[1] / "shard-0" / "batch-0000" / "junit.xml"
    junit.write_text('<testsuite><testcase name="test_a"/></testsuite>', encoding="utf-8")
    checked = diag.assess(junit.parent, plan["nodeids"])
    assert not checked["success"] and "changed evidence: junit.xml" in checked["issues"]


def test_zero_exit_without_phase_completion_is_rejected(sample):
    plan = make_plan(sample, "import os\ndef test_crash(): os._exit(0)\n", shards=1)
    result = run_all(sample, plan)
    assert not result["success"] and result["counts"] == {"incomplete": 1}


def test_collection_error_blocks_plan(sample):
    root, evidence = sample
    (root / "test_bad.py").write_text("raise ValueError('collection failed')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="full collection failed"):
        diag.collect(root, evidence, 2, source_only=True)
    assert not (evidence / "plan.json").exists()
    assert any(r["kind"] == "collection_error" for r in diag.events(evidence / "collection"))


def test_source_drift_rejected_before_test_execution(sample):
    plan = make_plan(sample, "def test_a(): pass\n", shards=1)
    (sample[0] / "test_sample.py").write_text("def test_a(): assert False\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checkout differs"):
        diag.run_shard(sample[0], plan, 0, sample[1] / "shard-0")
    assert not (sample[1] / "shard-0").exists()


def test_mixed_workflow_attempt_rejected(sample, monkeypatch):
    plan = make_plan(sample, "def test_a(): pass\n", shards=1)
    monkeypatch.setenv("GITHUB_RUN_ID", "another-run")
    with pytest.raises(ValueError, match="mixed workflow"):
        diag.run_shard(sample[0], plan, 0, sample[1] / "shard-0")


def test_network_guard_and_secret_environment(sample, monkeypatch):
    identities = {
        "LOGNAME": "diagnostic-logname",
        "USER": "diagnostic-user",
        "LNAME": "diagnostic-lname",
        "USERNAME": "diagnostic-username",
    }
    for key, value in identities.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GITHUB_TOKEN", "SYNTHETIC_DO_NOT_FORWARD")
    monkeypatch.setenv("TBANK_TOKEN", "SYNTHETIC_DO_NOT_FORWARD")
    monkeypatch.setenv("UNRELATED_DIAGNOSTIC_INPUT", "SYNTHETIC_DO_NOT_FORWARD")
    plan = make_plan(sample, '''import os, socket, pytest
def test_isolated():
    assert {key: os.environ.get(key) for key in ("LOGNAME", "USER", "LNAME", "USERNAME")} == {
        "LOGNAME": "diagnostic-logname", "USER": "diagnostic-user",
        "LNAME": "diagnostic-lname", "USERNAME": "diagnostic-username",
    }
    assert "GITHUB_TOKEN" not in os.environ and "TBANK_TOKEN" not in os.environ
    assert "UNRELATED_DIAGNOSTIC_INPUT" not in os.environ
    assert "MOEX_ROBOT_RUNTIME_DIR" in os.environ
    with pytest.raises(AssertionError, match="in-process network"):
        socket.getaddrinfo("example.invalid", 443)
''', shards=1)
    assert run_all(sample, plan)["success"]


@pytest.mark.parametrize("damage", ["hash", "omit", "duplicate", "partition", "profile"])
def test_plan_tamper_rejected_without_subprocess(damage):
    payload = {"schema": diag.PLAN_SCHEMA, "profile": diag.PROFILE,
               "nodeids": ["test_x.py::test_a", "test_x.py::test_b"],
               "partitions": [["test_x.py::test_a"], ["test_x.py::test_b"]]}
    plan = {**payload, "sha256": diag.digest(payload)}
    if damage == "hash":
        plan["sha256"] = "0" * 64
    else:
        if damage == "omit": plan["partitions"][1] = []
        if damage == "duplicate": plan["nodeids"].append("test_x.py::test_a")
        if damage == "partition": plan["partitions"].reverse()
        if damage == "profile": plan["profile"] = "ONLINE"
        plan["sha256"] = diag.digest({k: v for k, v in plan.items() if k != "sha256"})
    with pytest.raises(ValueError):
        diag.validate_plan(plan)


@pytest.mark.parametrize("node", ["../test.py::test_a", "/test.py::test_a", "-x.py::test",
                                 "C:/test.py::test_a", "test.py::test_a\n--ignore=tests", "test.py"])
def test_unsafe_node_rejected(node):
    with pytest.raises(ValueError):
        diag.partition([node], 1)


def test_partition_is_complete_nonoverlapping_and_deterministic():
    nodes = [f"tests/test_{i // 30}.py::test_{i}" for i in range(1000)]
    first = diag.partition(nodes, 8)
    assert first == diag.partition(nodes[::-1], 8)
    assert sorted(node for group in first for node in group) == sorted(nodes)
    assert max(map(len, first)) == min(map(len, first)) == 125


def test_source_only_identity_cannot_claim_git(sample):
    assert diag.source_identity(sample[0], True)["mode"] == "SOURCE_ONLY"
    with pytest.raises(Exception):
        diag.source_identity(sample[0], False)


def test_duplicate_phase_fails_even_with_recomputed_hash(sample):
    plan = make_plan(sample, "def test_a(): pass\n", shards=1)
    assert run_all(sample, plan)["success"]
    folder = sample[1] / "shard-0" / "batch-0000"
    rows = diag.events(folder)
    phase = next(row for row in rows if row["kind"] == "phase")
    with (folder / "events.jsonl").open("ab") as stream:
        stream.write(diag.canonical(phase) + b"\n")
    receipt = diag.read_json(folder / "exit.json")
    receipt["events.jsonl_sha256"] = diag.hashlib.sha256((folder / "events.jsonl").read_bytes()).hexdigest()
    diag.write_json(folder / "exit.json", receipt)
    assert not diag.aggregate(plan, sample[1])["success"]


def test_truncated_jsonl_is_not_silently_ignored(sample):
    plan = make_plan(sample, "def test_a(): pass\n", shards=1)
    assert run_all(sample, plan)["success"]
    folder = sample[1] / "shard-0" / "batch-0000"
    with (folder / "events.jsonl").open("ab") as stream:
        stream.write(b'{"kind":')
    result = diag.assess(folder, plan["nodeids"])
    assert not result["success"] and result["outcomes"][plan["nodeids"][0]] == "incomplete"


@pytest.mark.parametrize("empty_reason", [False, True])
def test_xfail_visible_and_xpass_is_not_green(sample, empty_reason):
    text = "import pytest\n@pytest.mark.xfail(reason='known')\ndef test_a(): assert False\n@pytest.mark.xfail(reason='unexpected pass')\ndef test_b(): pass\n"
    if empty_reason:
        text = text.replace("reason='known'", "reason=''").replace("reason='unexpected pass'", "reason=''")
    plan = make_plan(sample, text, shards=1)
    result = run_all(sample, plan)
    assert result["counts"] == {"xfail": 1, "xpass": 1}
    assert not result["success"]


def test_real_git_identity_and_dirty_guard(sample):
    import subprocess
    root, evidence = sample
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (root / "test_sample.py").write_text("def test_a(): pass\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Diagnostic fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], check=True)
    plan = diag.collect(root, evidence / "plan", 1)
    assert plan["identity"]["mode"] == "GIT" and len(plan["identity"]["tree"]) == 40
    assert run_all(sample, plan)["success"]
    (root / "test_untracked.py").write_text("def test_extra(): pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        diag.source_identity(root)


def test_bad_report_outcome_is_not_green(sample):
    plan = make_plan(sample, "def test_a(): pass\n", shards=1)
    assert run_all(sample, plan)["success"]
    folder = sample[1] / "shard-0" / "batch-0000"
    rows = diag.events(folder)
    for row in rows:
        if row["kind"] == "phase": row["outcome"] = "invalid"
    (folder / "events.jsonl").write_bytes(b"".join(diag.canonical(row) + b"\n" for row in rows))
    receipt = diag.read_json(folder / "exit.json")
    receipt["events.jsonl_sha256"] = diag.hashlib.sha256((folder / "events.jsonl").read_bytes()).hexdigest()
    diag.write_json(folder / "exit.json", receipt)
    assert not diag.assess(folder, plan["nodeids"])["success"]


def test_workflow_pins_every_checkout_to_pr_head_sha():
    import re

    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "q7a-diagnostics.yml"
    body = workflow.read_text(encoding="utf-8")
    checkout = "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7"
    setup_python = "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7"
    pinned = "ref: ${{ github.event.pull_request.head.sha || github.sha }}"
    assert body.count(checkout) == 3
    assert body.count(setup_python) == 3
    assert body.count(pinned) == 3
    # Every GitHub Action dependency in this exact-custody workflow must be immutable.
    for action_ref in re.findall(r"uses:\s+actions/[^@\s]+@([^\s#]+)", body):
        assert re.fullmatch(r"[0-9a-f]{40}", action_ref), action_ref
    # A pull_request workflow must not silently execute against refs/pull/*/merge.
    for block in body.split(checkout)[1:]:
        assert pinned in block.split("- uses:", 1)[0]
