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


def test_duration_lpt_reduces_concentration_and_preserves_every_id():
    nodes = [f"tests/test_weighted.py::test_{i:02d}" for i in range(32)]
    weights = {node: 100000 if index % 8 == 0 else 1000 for index, node in enumerate(nodes)}
    old = [nodes[index::8] for index in range(8)]
    new = diag.weighted_partition(nodes, 8, weights)
    cost = lambda group: sum(weights[node] for node in group)
    assert max(map(cost, new)) < max(map(cost, old)) / 3
    assert sorted(node for group in new for node in group) == nodes
    assert new == diag.weighted_partition(nodes[::-1], 8, dict(reversed(list(weights.items()))))


@pytest.mark.parametrize("damage", ["zero", "negative", "float", "bool", "missing", "extra", "duplicate_id"])
def test_duration_weights_fail_closed(damage):
    nodes = ["test_weighted.py::test_a", "test_weighted.py::test_b"]
    weights = dict.fromkeys(nodes, 1000)
    if damage in {"zero", "negative", "float", "bool"}:
        weights[nodes[0]] = {"zero": 0, "negative": -1, "float": 1.0, "bool": True}[damage]
    elif damage == "missing":
        del weights[nodes[0]]
    elif damage == "extra":
        weights["test_other.py::test_extra"] = 1000
    else:
        nodes.append(nodes[0])
    with pytest.raises(ValueError):
        diag.weighted_partition(nodes, 2, weights)


@pytest.mark.parametrize("damage", ["source", "run", "attempt", "schema", "record", "zero_weight"])
def test_frozen_profile_rejects_mutation_and_mixed_evidence(damage):
    profile = diag.timing_profile()
    if damage == "source": profile["source"]["head"] = "0" * 40
    if damage == "run": profile["source"]["run_id"] = "another-run"
    if damage == "attempt": profile["source"]["run_attempt"] = "2"
    if damage == "schema": profile["schema"] = "MUTABLE_CACHE"
    if damage == "record": profile["records"][0] = ["malformed"]
    if damage == "zero_weight": profile["records"][0][1] = 0
    with pytest.raises(ValueError, match="timing profile integrity"):
        diag.validate_timing_profile(profile)


def test_unknown_duration_uses_conservative_bound_and_not_an_outcome():
    node = "tests/test_unseen_module.py::test_unknown"
    profile = diag.timing_profile()
    values = sorted(row[1] for row in profile["records"])
    percentile = values[(99 * len(values) + 99) // 100 - 1]
    assert diag.planning_weights([node])[node] == (3 * percentile + 1) // 2 + 2000
    assert profile["outcomes_used"] is False
    assert not any("passed" in row or "failed" in row for row in profile["records"])


def test_identical_input_has_byte_identical_canonical_plan(sample):
    root, evidence = sample
    (root / "test_sample.py").write_text("def test_a(): pass\ndef test_b(): pass\n")
    first = diag.collect(root, evidence / "first", 2, source_only=True)
    second = diag.collect(root, evidence / "second", 2, source_only=True)
    assert first == second
    assert (evidence / "first/plan.json").read_bytes() == (evidence / "second/plan.json").read_bytes()


@pytest.mark.parametrize("damage", ["duplicate", "extra", "missing_phase", "junit_disagreement", "interrupt"])
def test_rehashed_execution_evidence_damage_is_not_green(sample, damage):
    plan = make_plan(sample, "def test_a(): pass\n", shards=1)
    assert run_all(sample, plan)["success"]
    folder = sample[1] / "shard-0/batch-0000"
    rows = diag.events(folder)
    receipt = diag.read_json(folder / "exit.json")
    if damage == "duplicate":
        rows.append(next(row for row in rows if row["kind"] == "start"))
    elif damage == "extra":
        rows.append({"kind": "start", "nodeid": "test_other.py::test_extra"})
    elif damage == "missing_phase":
        rows = [row for row in rows if not (row["kind"] == "phase" and row["when"] == "teardown")]
    elif damage == "junit_disagreement":
        xml = folder / "junit.xml"
        tree = diag.ET.parse(xml)
        case = next(tree.iter("testcase"))
        case.append(diag.ET.Element("failure", {"message": "synthetic mismatch"}))
        tree.write(xml, encoding="utf-8")
        receipt["junit.xml_sha256"] = diag.hashlib.sha256(xml.read_bytes()).hexdigest()
    else:
        rows = [row for row in rows if row["kind"] != "session_finish"]
        receipt["returncode"] = 2
    (folder / "events.jsonl").write_bytes(b"".join(diag.canonical(row) + b"\n" for row in rows))
    receipt["events.jsonl_sha256"] = diag.hashlib.sha256((folder / "events.jsonl").read_bytes()).hexdigest()
    diag.write_json(folder / "exit.json", receipt)
    result = diag.aggregate(plan, sample[1])
    assert not result["success"]
    if damage == "junit_disagreement":
        assert "JUnit/event identity or outcome disagreement" in diag.assess(folder, plan["nodeids"])["issues"]


@pytest.mark.parametrize("budget,timeout", [(2101, 1200), (2100, 1201), (float("nan"), 1200)])
def test_existing_execution_bounds_cannot_be_enlarged(sample, budget, timeout):
    plan = make_plan(sample, "def test_a(): pass\n", shards=1)
    with pytest.raises(ValueError, match="unchanged 2100/1200"):
        diag.run_shard(sample[0], plan, 0, sample[1] / "forbidden", budget=budget, batch_timeout=timeout)
    assert not (sample[1] / "forbidden").exists()
