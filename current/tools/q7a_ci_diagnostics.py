"""Independent collection, bounded subprocess shards and exact-coverage evidence.

This is a companion diagnostic runner, not a trading entrypoint or a replacement
for the existing CI. No retries, xfail additions, failure allowlists or synthetic
GitHub PR context. Ordinary pytest assertions and deadlines are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import importlib.metadata
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

PROFILE = "Q7A_OFFLINE_DIAGNOSTICS_V1"
PLAN_SCHEMA = "Q7A_CI_PLAN_V1"
HERE = Path(__file__).resolve().parent


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True,
                                   stderr=subprocess.PIPE, timeout=20).strip()


def source_identity(cwd: Path, source_only: bool = False) -> dict[str, str]:
    if source_only:
        # Only permitted explicitly for isolated runner tests/source-archive checks.
        # Never presented as Git provenance. Outputs must live outside this tree.
        rows = []
        for path in sorted(cwd.rglob("*")):
            if any(part in {"__pycache__", ".pytest_cache", ".git"} for part in path.parts):
                continue
            if path.is_symlink():
                raise ValueError("source symlink is unsupported")
            if path.is_file():
                rows.append([path.relative_to(cwd).as_posix(),
                             hashlib.sha256(path.read_bytes()).hexdigest()])
        return {"mode": "SOURCE_ONLY", "files_sha256": digest(rows)}
    # Full checkout and unchanged tracked source are required in GitHub CI.
    if _git(cwd, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("tracked source changed during diagnostics")
    return {"mode": "GIT", "head": _git(cwd, "rev-parse", "HEAD"),
            "tree": _git(cwd, "rev-parse", "HEAD^{tree}")}


def child_env(folder: Path) -> dict[str, str]:
    # Preserve genuine GitHub identity, never borrow credential/token env values.
    allowed = {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "HOME", "USERPROFILE",
        "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "LANG", "LC_ALL", "LOGNAME", "USER", "LNAME", "USERNAME", "VIRTUAL_ENV",
        "PYTHONHOME", "GIT_EXEC_PATH", "CI",
        "GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH", "GITHUB_SHA",
        "GITHUB_HEAD_REF", "GITHUB_BASE_REF", "GITHUB_REPOSITORY", "GITHUB_WORKSPACE",
        "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "RUNNER_OS", "RUNNER_ARCH",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    temporary = folder / "scratch"
    temporary.mkdir()
    env.update({
        "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "MPLBACKEND": "Agg",
        "PYTHONPATH": str(HERE), "MOEX_ROBOT_RUNTIME_DIR": str(temporary / "runtime"),
        "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary),
        "Q7A_CI_EVENTS": str(folder / "events.jsonl"),
    })
    return env


def _kill_tree(proc: subprocess.Popen[Any]) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=20, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=20)


def invoke(cwd: Path, folder: Path, args: list[str], timeout: float) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=False)
    env = child_env(folder)
    command = [sys.executable, "-u", "-B", "-m", "pytest", "-p", "q7a_ci_events",
               "-p", "no:cacheprovider", "--tb=short", "-ra", "-vv",
               "--basetemp", str(folder / "scratch" / "pytest"),
               "--junitxml", str(folder / "junit.xml"), *args]
    start = time.monotonic()
    receipt: dict[str, Any] = {"command": command, "profile": PROFILE,
                               "timeout_seconds": timeout, "timed_out": False,
                               "environment": {"python": platform.python_version(),
                                "platform": platform.system(), "sqlite": sqlite3.sqlite_version,
                                "pytest": importlib.metadata.version("pytest")}}
    write_json(folder / "started.json", receipt)
    with (folder / "pytest.log").open("wb") as output:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=output,
                                stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            receipt["timed_out"] = True
            _kill_tree(proc)
            code = proc.returncode
        except BaseException:
            _kill_tree(proc)
            receipt.update(returncode=proc.returncode, interrupted=True,
                           seconds=time.monotonic() - start)
            write_json(folder / "exit.json", receipt)
            raise
    receipt.update(returncode=code, seconds=time.monotonic() - start)
    for name in ("events.jsonl", "junit.xml", "pytest.log"):
        path = folder / name
        receipt[name + "_sha256"] = (hashlib.sha256(path.read_bytes()).hexdigest()
                                     if path.exists() else None)
    write_json(folder / "exit.json", receipt)
    return receipt


def events(folder: Path) -> list[dict[str, Any]]:
    path = folder / "events.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        # A truncated event on crash is not silently treated as a complete stream.
        rows.append(json.loads(line))
    return rows


def valid_node(node: Any) -> bool:
    if not isinstance(node, str) or "::" not in node or "\n" in node or "\r" in node:
        return False
    filename = node.split("::", 1)[0]
    return (not filename.startswith(("-", "/", "\\")) and "\\" not in filename
            and ":" not in filename and ".." not in Path(filename).parts
            and filename.endswith(".py"))


def partition(nodes: list[str], count: int) -> list[list[str]]:
    if type(count) is not int or not 1 <= count <= 16:
        raise ValueError("shard count must be 1..16")
    if not nodes or len(nodes) != len(set(nodes)) or not all(map(valid_node, nodes)):
        raise ValueError("invalid or duplicate collected test IDs")
    return [sorted(nodes)[index::count] for index in range(count)]


def collect(cwd: Path, out: Path, shards: int, source_only: bool = False) -> dict[str, Any]:
    identity = source_identity(cwd, source_only)
    receipt = invoke(cwd, out / "collection", ["--collect-only"], 180)
    rows = events(out / "collection")
    collected = [row for row in rows if row["kind"] == "collection"]
    if (receipt["returncode"] != 0 or receipt["timed_out"] or len(collected) != 1
            or any(row["kind"] in {"collection_error", "deselected"} for row in rows)):
        raise ValueError("full collection failed; inspect collection evidence")
    if source_identity(cwd, source_only) != identity:
        raise ValueError("source changed during collection")
    nodes = sorted(collected[0]["nodeids"])
    payload = {"schema": PLAN_SCHEMA, "profile": PROFILE, "identity": identity,
               "run_id": os.environ.get("GITHUB_RUN_ID"),
               "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
               "nodeids": nodes, "partitions": partition(nodes, shards)}
    plan = {**payload, "sha256": digest(payload)}
    write_json(out / "plan.json", plan)
    return plan


def validate_plan(plan: dict[str, Any]) -> None:
    payload = {key: value for key, value in plan.items() if key != "sha256"}
    if plan.get("sha256") != digest(payload) or plan.get("schema") != PLAN_SCHEMA:
        raise ValueError("plan integrity/schema mismatch")
    if plan.get("profile") != PROFILE:
        raise ValueError("diagnostic profile mismatch")
    if plan["partitions"] != partition(plan["nodeids"], len(plan["partitions"])):
        raise ValueError("noncanonical partition or omitted tests")


def batches(nodes: list[str]) -> list[list[str]]:
    result: list[list[str]] = []
    for node in nodes:
        module = node.split("::", 1)[0]
        if (not result or len(result[-1]) >= 16
                or result[-1][0].split("::", 1)[0] != module
                or sum(len(n) + 3 for n in result[-1]) + len(node) > 10000):
            result.append([])
        result[-1].append(node)
    return result


def assess(folder: Path, expected: list[str]) -> dict[str, Any]:
    issues: list[str] = []
    outcomes: dict[str, str] = {}
    receipt: dict[str, Any] = {"returncode": None, "timed_out": False}
    try:
        receipt = read_json(folder / "exit.json")
        rows = events(folder)
        for name in ("events.jsonl", "junit.xml", "pytest.log"):
            path = folder / name
            observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if receipt.get(name + "_sha256") != observed:
                issues.append("changed evidence: " + name)
        collected = [row["nodeids"] for row in rows if row["kind"] == "collection"]
        if collected != [expected]:
            issues.append("executed collection != assigned selection")
        if any(row["kind"] in {"collection_error", "deselected"} for row in rows):
            issues.append("collection error or deselection")
        for kind in ("start", "finish"):
            ids = [row["nodeid"] for row in rows if row["kind"] == kind]
            if Counter(ids) != Counter(expected):
                issues.append("missing/duplicate/extra " + kind)
        extra = {row["nodeid"] for row in rows if "nodeid" in row} - set(expected)
        if extra:
            issues.append("unassigned report IDs")
        for node in expected:
            phases = [row for row in rows if row["kind"] == "phase" and row["nodeid"] == node]
            by_phase = {row["when"]: row for row in phases}
            if (len(by_phase) != len(phases) or "setup" not in by_phase
                    or "teardown" not in by_phase
                    or (by_phase["setup"]["outcome"] == "passed" and "call" not in by_phase)
                    or any(p not in {"setup", "call", "teardown"} for p in by_phase)
                    or any(row["outcome"] not in {"passed", "failed", "skipped"} for row in phases)):
                outcomes[node] = "incomplete"
                continue
            if any(row["outcome"] == "failed" for row in phases):
                outcomes[node] = "error" if any(row["outcome"] == "failed" and row["when"] != "call"
                                                for row in phases) else "failed"
            elif any(row["outcome"] == "skipped" for row in phases):
                outcomes[node] = "xfail" if any(row["wasxfail"] is not None for row in phases) else "skipped"
            elif by_phase.get("call", {}).get("wasxfail") is not None:
                outcomes[node] = "xpass"
            else:
                outcomes[node] = "passed"
        finish = [row["exitstatus"] for row in rows if row["kind"] == "session_finish"]
        if finish != [receipt["returncode"]]:
            issues.append("pytest session did not finish with recorded exit code")
        if receipt["timed_out"] or receipt["returncode"] not in (0, 1):
            issues.append("timeout/crash/interruption/invalid pytest exit")
        cases = list(ET.parse(folder / "junit.xml").iter("testcase"))
        # A setup+teardown error can produce more than one testcase for one node.
        # JSONL is authoritative for identity; XML must contain every selected case name.
        observed_names = Counter(case.attrib.get("name", "") for case in cases)
        # Pytest's name contains the function, class components live in classname.
        expected_leaf = Counter(node.split("::", 1)[1].split("[", 1)[0].split("::")[-1]
                                + ("[" + node.split("[", 1)[1] if "[" in node else "")
                                for node in expected)
        if any(observed_names[name] < amount for name, amount in expected_leaf.items()):
            issues.append("JUnit missing selected case")
        junit_bad = any(case.find("failure") is not None or case.find("error") is not None
                        for case in cases)
        if receipt["returncode"] == 0 and (junit_bad or any(
                outcome in {"failed", "error", "incomplete"} for outcome in outcomes.values())):
            issues.append("exit zero contradicts evidence")
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        issues.append(f"unreadable/incomplete evidence: {type(exc).__name__}")
    for node in expected:
        outcomes.setdefault(node, "incomplete")
    success = not issues and receipt["returncode"] == 0 and all(
        outcome not in {"failed", "error", "incomplete", "xpass"} for outcome in outcomes.values())
    return {"success": success, "issues": issues, "outcomes": outcomes,
            "returncode": receipt["returncode"], "timed_out": receipt["timed_out"]}


def run_shard(cwd: Path, plan: dict[str, Any], index: int, out: Path,
              budget: float = 2100, batch_timeout: float = 1200) -> dict[str, Any]:
    validate_plan(plan)
    if type(index) is not int or not 0 <= index < len(plan["partitions"]):
        raise ValueError("invalid shard index")
    if budget <= 0 or batch_timeout <= 0:
        raise ValueError("timeouts must be positive")
    source_only = plan["identity"]["mode"] == "SOURCE_ONLY"
    if source_identity(cwd, source_only) != plan["identity"]:
        raise ValueError("checkout differs from independent collection")
    for key, name in (("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT")):
        if plan[key] != os.environ.get(name):
            raise ValueError("mixed workflow run/attempt")
    out.mkdir(parents=True, exist_ok=False)
    assigned = plan["partitions"][index]
    write_json(out / "selection.json", {"plan_sha256": plan["sha256"], "index": index,
                                        "nodeids": assigned})
    start = time.monotonic()
    results = []
    for number, selection in enumerate(batches(assigned)):
        remaining = budget - (time.monotonic() - start)
        if remaining <= 0:
            break
        name = f"batch-{number:04d}"
        print(f"START shard={index} {name} nodes={len(selection)} {selection[0]}", flush=True)
        invoke(cwd, out / name, ["--", *selection], min(batch_timeout, remaining))
        status = assess(out / name, selection)
        results.append({"name": name, "nodeids": selection, "status": status})
        write_json(out / "progress.json", {"index": index, "batches": results})
    all_outcomes = {node: outcome for result in results
                    for node, outcome in result["status"]["outcomes"].items()}
    for node in assigned:
        all_outcomes.setdefault(node, "incomplete")
    unchanged = source_identity(cwd, source_only) == plan["identity"]
    report = {"schema": "Q7A_CI_SHARD_V1", "plan_sha256": plan["sha256"], "index": index,
              "identity": plan["identity"], "source_unchanged": unchanged,
              "batches": results, "outcomes": all_outcomes,
              "success": unchanged and all(result["status"]["success"] for result in results)
              and len(results) == len(batches(assigned)), "seconds": time.monotonic() - start}
    write_json(out / "report.json", report)
    return report


def aggregate(plan: dict[str, Any], artifacts: Path) -> dict[str, Any]:
    validate_plan(plan)
    issues: list[str] = []
    all_outcomes: dict[str, str] = {}
    found: dict[int, Path] = {}
    for path in artifacts.rglob("report.json"):
        report = read_json(path)
        if report.get("schema") != "Q7A_CI_SHARD_V1":
            continue
        index = report["index"]
        if type(index) is not int or not 0 <= index < len(plan["partitions"]) or index in found:
            issues.append("duplicate or invalid shard")
            continue
        found[index] = path
        if report["plan_sha256"] != plan["sha256"] or report["identity"] != plan["identity"]:
            issues.append("mixed plan/commit/tree")
            continue
        if not report.get("success"):
            issues.append("shard reported unsuccessful")
        if not report.get("source_unchanged"):
            issues.append("shard source changed")
        assigned_batches = batches(plan["partitions"][index])
        expected_names = [f"batch-{i:04d}" for i in range(len(assigned_batches))]
        if [item["name"] for item in report["batches"]] != expected_names:
            issues.append("missing/extra batches")
        checked: dict[str, str] = {}
        for i, selection in enumerate(assigned_batches):
            result = assess(path.parent / f"batch-{i:04d}", selection)
            if not result["success"]:
                issues.append(f"shard {index} batch {i} unsuccessful")
            checked.update(result["outcomes"])
        if checked != report.get("outcomes"):
            issues.append("summary disagrees with original phase evidence")
        for node, outcome in checked.items():
            if node in all_outcomes:
                issues.append("duplicate executed test")
            all_outcomes[node] = outcome
    if set(found) != set(range(len(plan["partitions"]))):
        issues.append("missing shard artifacts")
    if set(all_outcomes) != set(plan["nodeids"]):
        issues.append("full collection != executed union")
    for node in plan["nodeids"]:
        all_outcomes.setdefault(node, "incomplete")
    return {"schema": "Q7A_CI_AGGREGATE_V1", "plan_sha256": plan["sha256"],
            "identity": plan["identity"], "success": not issues,
            "expected_count": len(plan["nodeids"]), "counts": dict(Counter(all_outcomes.values())),
            "issues": issues, "outcomes": all_outcomes,
            "qualification": "DIAGNOSTIC_ONLY_NOT_RELEASE_OR_TRADE_AUTHORITY"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    coll = subs.add_parser("collect")
    coll.add_argument("--cwd", type=Path, default=Path.cwd())
    coll.add_argument("--out", type=Path, required=True)
    coll.add_argument("--shards", type=int, default=8)
    coll.add_argument("--source-only", action="store_true")
    run = subs.add_parser("run")
    run.add_argument("--cwd", type=Path, default=Path.cwd())
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--index", type=int, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--budget-seconds", type=float, default=2100)
    run.add_argument("--batch-timeout", type=float, default=1200)
    agg = subs.add_parser("aggregate")
    agg.add_argument("--plan", type=Path, required=True)
    agg.add_argument("--artifacts", type=Path, required=True)
    agg.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "collect":
            plan = collect(args.cwd.resolve(), args.out.resolve(), args.shards, args.source_only)
            print(f"Collected {len(plan['nodeids'])} IDs; plan {plan['sha256']}")
            return 0
        if args.action == "run":
            result = run_shard(args.cwd.resolve(), read_json(args.plan), args.index,
                               args.out.resolve(), args.budget_seconds, args.batch_timeout)
        else:
            result = aggregate(read_json(args.plan), args.artifacts.resolve())
            write_json(args.out, result)
        print(json.dumps({key: result[key] for key in ("success",) if key in result}))
        return 0 if result["success"] else 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"DIAGNOSTIC INFRASTRUCTURE FAILURE: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
