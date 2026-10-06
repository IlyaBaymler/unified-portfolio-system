"""Diagnostic pytest plugin: report actual phases without changing selection/outcomes.

Used in child processes by q7a_ci_diagnostics.py. Evidence is flushed per event,
so a crash cannot erase completed test identities. Network denial is in-process
only and intentionally reported as a separate diagnostic execution profile.
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any


def _emit(kind: str, **fields: Any) -> None:
    value = {"kind": kind, "monotonic": time.monotonic(), **fields}
    path = Path(os.environ["Q7A_CI_EVENTS"])
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def pytest_configure(config: Any) -> None:
    def no_network(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Q7A diagnostic profile forbids in-process network")

    socket.create_connection = no_network
    socket.getaddrinfo = no_network
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    _emit("configured", profile="OFFLINE_IN_PROCESS_V1", args=list(config.invocation_params.args))


def pytest_collection_finish(session: Any) -> None:
    _emit("collection", nodeids=[item.nodeid for item in session.items])


def pytest_deselected(items: Any) -> None:
    _emit("deselected", nodeids=[item.nodeid for item in items])


def pytest_collectreport(report: Any) -> None:
    if report.failed:
        _emit("collection_error", nodeid=report.nodeid, detail=str(report.longrepr))


def pytest_runtest_logstart(nodeid: str, location: Any) -> None:
    _emit("start", nodeid=nodeid)


def pytest_runtest_logreport(report: Any) -> None:
    detail = getattr(report, "longreprtext", "")
    _emit(
        "phase", nodeid=report.nodeid, when=report.when,
        outcome=report.outcome, duration=report.duration,
        wasxfail=str(report.wasxfail) if hasattr(report, "wasxfail") else None,
        detail=detail[:32000], detail_truncated=len(detail) > 32000,
    )


def pytest_runtest_logfinish(nodeid: str, location: Any) -> None:
    _emit("finish", nodeid=nodeid)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    _emit("session_finish", exitstatus=int(exitstatus))
