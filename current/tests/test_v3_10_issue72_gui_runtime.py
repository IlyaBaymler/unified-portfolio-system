from __future__ import annotations

import ast
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.v3_10_issue72_q0_evidence as q0
from desktop_gui import _privacy_safe_gui_value
from tools.v3_10_stable_qualification import (
    RELEASE_CUT_ALLOWLIST,
    RELEASE_REVIEW_CORRECTION_ALLOWLIST,
)
from trading_robot.bot import BotConfig
from trading_robot.config_persistence import bot_config_to_profile
from trading_robot.dashboard_view import build_multi_instrument_dashboard
from trading_robot.global_scheduler import GlobalScheduler
from trading_robot.gui_runtime_controller import (
    GuiCoordinationRequest,
    GuiRuntimeBlockedError,
    GuiRuntimeController,
)
from trading_robot.instrument_runtime import (
    InstrumentRuntimeConflictError,
    InstrumentRuntimeStateError,
    InstrumentRuntimeStore,
)
from trading_robot.multi_instrument_config import (
    MultiInstrumentProfile,
    MultiInstrumentProfileStore,
)
from trading_robot.runtime_cash_authority import (
    RuntimeCashAuthorityRecord,
    RuntimeCashAuthorityState,
)

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "current"
FIXTURE = CURRENT / "tests" / "fixtures" / "v3_10_issue72_gui_runtime_vectors.json"
CONTRACT = (
    ROOT / "docs" / "project" / "V3_10_ISSUE72_GUI_RUNTIME_RESCOPE_CONTRACT_RU.md"
)
ACCOUNT = "sandbox-account-synthetic"
SCOPE = "15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3"
T0 = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
ACCEPTED_CONTRACT = "5bb7569992a93817fad939a7fc8919444001b8c0"
ACCEPTED_CONTRACT_TREE = "6e843c504ea91033c1226a0223ea0578256b80b4"
ACCEPTED_IMPLEMENTATION = "e27204ad110db36b8ace540bd0738874fab69565"
ACCEPTED_IMPLEMENTATION_TREE = "a38d38617dcfa7e15dd1b8ce1f838aeec72c35f1"
CL8_ADOPTION_BRANCH = "agent/v3-10-clean-cl8-qualification-adoption"
CL8_ADOPTION_MECHANICAL_COMMIT = "70589366d3c34ede0cb0aba2a43f988c9f91fbd9"
CL8_ADOPTION_MECHANICAL_TREE = "cfb1f9ea602316da30152a6f6c89b5f2018cf015"
CL8_RELEASE_CUT_BRANCH = "agent/v3-10-clean-cl8-release-cut"
CL8_RELEASE_CUT_PREDECESSOR = "7a569eadfb2a99c5314ae43d24da0dee47819d6c"
CL8_RELEASE_CUT_PREDECESSOR_TREE = "d4f6bf5d1b00f4b944ac0aece669a00cd72b5847"
CL8_RELEASE_REVIEW_PARENT = "58fc85f26d089da677d68bf3ded6a7cca05fb035"
CL8_RELEASE_REVIEW_PARENT_TREE = "9036c7f8d943720a47cbec3c1b68e0444e721458"
CL8_RELEASE_REVIEW_ACCEPTED_HEAD = "b1ccc10ff5c548815d049ea7081cb27b49307390"
CL8_RELEASE_REVIEW_ACCEPTED_TREE = "5729ed32a94a813905e14a868fe3edd73fe4a814"
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
CL8_Q7R_IMPLEMENTATION_BRANCH = (
    "agent/v3-10-clean-cl8-q7-preparation-rescope-implementation"
)
CL8_Q7R_ACCEPTED_CONTRACT = "1b87316a310e094c8c1c0d2bd3790221b49f60b4"
CL8_Q7R_ACCEPTED_CONTRACT_TREE = "610e544f802cea599fbfce56ede0a72a7e14c945"
CL8_Q7R_CONTRACT_BRANCH = "agent/v3-10-clean-cl8-q7-preparation-rescope-contract-freeze"
CL8_Q7R_INTEGRATION_BRANCH = "program/v3-10-v4-stable-line"
CL8_Q7R_INTEGRATION_BASE = "ebd68c7d71929ca194dbcdb9685a140a9eb319d5"
CL8_Q7R_INTEGRATION_BASE_TREE = "170b959512e0a911ee7189d8b4ec1fa398892192"
CL8_Q7R_REVIEWED_HEAD = "1bdcb7bbb383d2640cd942bff41993fb4975fddb"
CL8_Q7R_REVIEWED_TREE = "c4ebc10a3b4d148b566c005c00bb8d11d1cd201b"
CL8_Q7R_ACCEPTED_IMPLEMENTATION = "0a896154e9b8419151181767811de0e1682d0485"
CL8_Q7R_ACCEPTED_IMPLEMENTATION_TREE = "a7d8aabeeeec2f58d42633e071078426d407b599"
CL8_Q7R_IMPLEMENTATION_PATHS = {
    "current/desktop_gui.py",
    "current/trading_robot/gui_runtime_controller.py",
    "current/trading_robot/secret_provider.py",
    "current/tools/v3_10_runtime_cash_cutover.py",
    "current/tools/v3_10_q7_prepare_runtime.py",
    "current/tests/test_v3_10_q7_preparation_runtime.py",
    "current/tests/fixtures/v3_10_q7_preparation_vectors.json",
    "current/tests/test_v3_10_issue72_gui_runtime.py",
    "current/tests/test_v3_10_stable_qualification.py",
    "docs/plans/V3_10_CL8_Q7_PREPARATION_RUNBOOK_RU.md",
}
IMPLEMENTATION_PATHS = {
    "ROADMAP.md",
    "current/README.md",
    "current/desktop_gui.py",
    "current/tests/fixtures/v3_10_issue72_gui_runtime_vectors.json",
    "current/tests/test_v3_10_issue72_gui_runtime.py",
    "current/tools/v3_10_issue72_q0_evidence.py",
    "current/trading_robot/dashboard_view.py",
    "current/trading_robot/global_scheduler.py",
    "current/trading_robot/gui_runtime_controller.py",
    "current/trading_robot/instrument_runtime.py",
    "docs/plans/V3_10_ISSUE72_MULTI_INSTRUMENT_SANDBOX_RUNBOOK_RU.md",
    "docs/plans/V3_10_ISSUE72_SANDBOX_ACCOUNT_CLEANUP_RUNBOOK_RU.md",
    "docs/project/V3_10_ISSUE72_GUI_RUNTIME_REVIEW_RU.md",
    "docs/project/V3_10_ISSUE72_RISK_POLICY_GUI_ADR_RU.md",
}
CL8_ADOPTION_PATHS = {
    ".github/workflows/ci.yml",
    "current/rc_tool.py",
    "current/runtime_tool.py",
    "current/tests/fixtures/v3_10_stable_qualification_vectors.json",
    "current/tests/test_v3_10_issue72_gui_runtime.py",
    "current/tests/test_v3_10_stable_qualification.py",
    "current/tools/build_release.py",
    "current/tools/release_cleanup.py",
    "current/tools/v3_10_stable_qualification.py",
    "current/tools/verify_standalone_layout.py",
    "current/trading_robot/readiness.py",
    "current/trading_robot/runtime_backup.py",
    "current/trading_robot/runtime_bootstrap.py",
    "current/trading_robot/runtime_integrity.py",
    "current/trading_robot/support_bundle.py",
    "docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md",
}


def _emit_behavior_counters(**values: int) -> None:
    counters = {key: 0 for key in q0.COUNTER_KEYS}
    counters.update(values)
    assert frozenset(counters) == q0.COUNTER_KEYS
    assert all(type(item) is int and item >= 0 for item in counters.values())
    print(
        "ISSUE72_COUNTERS="
        + json.dumps(counters, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.rstrip()


def test_exact_contract_branch_and_fourteen_path_custody():
    checked_out_head = _git("rev-parse", "HEAD")
    head = checked_out_head
    assert _git("rev-parse", f"{ACCEPTED_CONTRACT}^{{tree}}") == ACCEPTED_CONTRACT_TREE
    assert _git("merge-base", ACCEPTED_CONTRACT, head) == ACCEPTED_CONTRACT
    branch = _git("branch", "--show-current")
    q7r_pr = None
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        assert event_path is not None
        pull_request = json.loads(Path(event_path).read_text(encoding="utf-8-sig"))[
            "pull_request"
        ]
        if pull_request["head"]["ref"] == CL8_Q7R_IMPLEMENTATION_BRANCH:
            q7r_pr = pull_request
            base = (
                pull_request["base"]["ref"],
                pull_request["base"]["sha"],
            )
            assert base in {
                (CL8_Q7R_CONTRACT_BRANCH, CL8_Q7R_ACCEPTED_CONTRACT),
                (CL8_Q7R_INTEGRATION_BRANCH, CL8_Q7R_INTEGRATION_BASE),
            }
            if base[0] == CL8_Q7R_INTEGRATION_BRANCH:
                assert _git("rev-parse", f"{base[1]}^{{tree}}") == (
                    CL8_Q7R_INTEGRATION_BASE_TREE
                )
            head = pull_request["head"]["sha"]
            parents = _git("show", "-s", "--format=%P", checked_out_head).split()
            assert parents == [base[1], head]
            assert _git("rev-parse", f"{checked_out_head}^{{tree}}") == _git(
                "rev-parse", f"{head}^{{tree}}"
            )
    if branch == CL8_Q7R_IMPLEMENTATION_BRANCH or q7r_pr is not None:
        assert _git(
            "rev-parse", f"{CL8_Q7R_ACCEPTED_CONTRACT}^{{tree}}"
        ) == CL8_Q7R_ACCEPTED_CONTRACT_TREE
        assert _git("merge-base", CL8_Q7R_ACCEPTED_CONTRACT, head) == (
            CL8_Q7R_ACCEPTED_CONTRACT
        )
        if head != CL8_Q7R_ACCEPTED_CONTRACT:
            parent = _git("rev-parse", f"{head}^")
            assert parent in {
                CL8_Q7R_ACCEPTED_CONTRACT,
                CL8_Q7R_REVIEWED_HEAD,
                CL8_Q7R_ACCEPTED_IMPLEMENTATION,
            }
            if parent == CL8_Q7R_REVIEWED_HEAD:
                assert _git("rev-parse", f"{parent}^{{tree}}") == CL8_Q7R_REVIEWED_TREE
            elif parent == CL8_Q7R_ACCEPTED_IMPLEMENTATION:
                assert _git("rev-parse", f"{parent}^{{tree}}") == (
                    CL8_Q7R_ACCEPTED_IMPLEMENTATION_TREE
                )
        changed = set(
            _git(
                "diff", "--name-only", f"{CL8_Q7R_ACCEPTED_CONTRACT}..{head}"
            ).splitlines()
        )
        if q7r_pr is None:
            changed.update(_git("diff", "--name-only").splitlines())
            changed.update(_git("ls-files", "--others", "--exclude-standard").splitlines())
        assert changed == CL8_Q7R_IMPLEMENTATION_PATHS
        return
    release_cut_pr = None
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        assert event_path is not None
        pull_request = json.loads(Path(event_path).read_text(encoding="utf-8-sig"))[
            "pull_request"
        ]
        if pull_request["head"]["ref"] == CL8_RELEASE_CUT_BRANCH:
            release_cut_pr = pull_request
    if branch == "agent/v3-10-issue72-gui-runtime-implementation":
        if head == ACCEPTED_CONTRACT:
            status = _git("status", "--porcelain=v1").splitlines()
            changed = {line[3:].replace("\\", "/") for line in status}
        else:
            changed = set(
                _git("diff", "--name-only", f"{ACCEPTED_CONTRACT}..{head}").splitlines()
            )
            changed.update(
                _git("ls-files", "--others", "--exclude-standard").splitlines()
            )
        assert changed == IMPLEMENTATION_PATHS
        return

    if branch == CL8_Q1_CORRECTION_BRANCH:
        assert _git("rev-parse", f"{CL8_Q1_CORRECTION_PARENT}^{{tree}}") == (
            CL8_Q1_CORRECTION_PARENT_TREE
        )
        assert _git("merge-base", CL8_Q1_CORRECTION_PARENT, head) == (
            CL8_Q1_CORRECTION_PARENT
        )
        if head != CL8_Q1_CORRECTION_PARENT:
            assert _git("rev-parse", f"{head}^") == CL8_Q1_CORRECTION_PARENT
        changed = set(
            _git(
                "diff", "--name-only", f"{CL8_Q1_CORRECTION_PARENT}..{head}"
            ).splitlines()
        )
        changed.update(_git("diff", "--name-only").splitlines())
        changed.update(_git("ls-files", "--others", "--exclude-standard").splitlines())
        assert changed == CL8_Q1_CORRECTION_PATHS
        for path in IMPLEMENTATION_PATHS - {
            "current/README.md",
            "current/tests/test_v3_10_issue72_gui_runtime.py",
        }:
            assert _git("rev-parse", f"{ACCEPTED_IMPLEMENTATION}:{path}") == _git(
                "rev-parse", f"{head}:{path}"
            )
        return

    if branch == CL8_RELEASE_CUT_BRANCH or release_cut_pr is not None:
        if release_cut_pr is not None:
            assert release_cut_pr["base"]["ref"] == ("program/v3-10-v4-stable-line")
            assert release_cut_pr["base"]["sha"] == CL8_RELEASE_CUT_PREDECESSOR
            head = release_cut_pr["head"]["sha"]
            assert _git("cat-file", "-e", f"{head}^{{commit}}") == ""
            assert checked_out_head == os.environ.get("GITHUB_SHA")
            commit_text = _git("cat-file", "-p", checked_out_head)
            parents = [
                line.removeprefix("parent ")
                for line in commit_text.splitlines()
                if line.startswith("parent ")
            ]
            assert parents == [CL8_RELEASE_CUT_PREDECESSOR, head]
            assert _git("rev-parse", f"{checked_out_head}^{{tree}}") == _git(
                "rev-parse", f"{head}^{{tree}}"
            )
        assert _git("rev-parse", f"{CL8_RELEASE_CUT_PREDECESSOR}^{{tree}}") == (
            CL8_RELEASE_CUT_PREDECESSOR_TREE
        )
        assert _git("rev-parse", f"{CL8_RELEASE_REVIEW_PARENT}^{{tree}}") == (
            CL8_RELEASE_REVIEW_PARENT_TREE
        )
        assert _git("rev-parse", f"{CL8_RELEASE_REVIEW_PARENT}^") == (
            CL8_RELEASE_CUT_PREDECESSOR
        )
        assert (
            _git("rev-parse", f"{CL8_RELEASE_REVIEW_ACCEPTED_HEAD}^{{tree}}")
            == CL8_RELEASE_REVIEW_ACCEPTED_TREE
        )
        assert _git("rev-parse", f"{CL8_RELEASE_REVIEW_ACCEPTED_HEAD}^") == (
            CL8_RELEASE_REVIEW_PARENT
        )
        assert _git("merge-base", ACCEPTED_IMPLEMENTATION, head) == (
            ACCEPTED_IMPLEMENTATION
        )
        if head not in {
            CL8_RELEASE_REVIEW_PARENT,
            CL8_RELEASE_REVIEW_ACCEPTED_HEAD,
        }:
            assert _git("rev-parse", f"{head}^") == CL8_RELEASE_REVIEW_ACCEPTED_HEAD
        if head != CL8_RELEASE_REVIEW_PARENT:
            rescope_changed = set(
                _git(
                    "diff",
                    "--name-only",
                    f"{CL8_RELEASE_REVIEW_ACCEPTED_HEAD}..{head}",
                ).splitlines()
            )
            rescope_changed.update(_git("diff", "--name-only").splitlines())
            rescope_changed.update(
                _git("ls-files", "--others", "--exclude-standard").splitlines()
            )
            assert rescope_changed == CL8_RELEASE_PR180_RESCOPE_PATHS
        correction_changed = set(
            _git(
                "diff", "--name-only", f"{CL8_RELEASE_REVIEW_PARENT}..{head}"
            ).splitlines()
        )
        correction_changed.update(_git("diff", "--name-only").splitlines())
        correction_changed.update(
            _git("ls-files", "--others", "--exclude-standard").splitlines()
        )
        assert correction_changed == RELEASE_REVIEW_CORRECTION_ALLOWLIST - {
            "current/install_and_run_gui.bat"
        }
        cumulative_changed = set(
            _git(
                "diff", "--name-only", f"{CL8_RELEASE_CUT_PREDECESSOR}..{head}"
            ).splitlines()
        )
        cumulative_changed.update(_git("diff", "--name-only").splitlines())
        assert cumulative_changed == RELEASE_CUT_ALLOWLIST - {"current/desktop_gui.py"}
        for path in IMPLEMENTATION_PATHS - {
            "current/README.md",
            "current/tests/test_v3_10_issue72_gui_runtime.py",
        }:
            assert _git("rev-parse", f"{ACCEPTED_IMPLEMENTATION}:{path}") == _git(
                "rev-parse", f"{head}:{path}"
            )
        return

    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        assert event_path is not None
        pull_request = json.loads(Path(event_path).read_text(encoding="utf-8-sig"))[
            "pull_request"
        ]
        assert pull_request["base"]["ref"] == "program/v3-10-v4-stable-line"
        assert pull_request["base"]["sha"] == ACCEPTED_IMPLEMENTATION
        assert pull_request["head"]["ref"] == CL8_ADOPTION_BRANCH
        head = pull_request["head"]["sha"]
        assert checked_out_head == os.environ.get("GITHUB_SHA")
        commit_text = _git("cat-file", "-p", checked_out_head)
        parents = [
            line.removeprefix("parent ")
            for line in commit_text.splitlines()
            if line.startswith("parent ")
        ]
        assert parents == [ACCEPTED_IMPLEMENTATION, head]
    else:
        assert branch == CL8_ADOPTION_BRANCH
    assert _git("rev-parse", f"{ACCEPTED_IMPLEMENTATION}^{{tree}}") == (
        ACCEPTED_IMPLEMENTATION_TREE
    )
    assert _git("rev-parse", f"{CL8_ADOPTION_MECHANICAL_COMMIT}^{{tree}}") == (
        CL8_ADOPTION_MECHANICAL_TREE
    )
    assert _git("rev-parse", f"{CL8_ADOPTION_MECHANICAL_COMMIT}^") == (
        ACCEPTED_IMPLEMENTATION
    )
    assert _git("rev-parse", f"{head}^") == CL8_ADOPTION_MECHANICAL_COMMIT
    assert _git("merge-base", ACCEPTED_IMPLEMENTATION, head) == (
        ACCEPTED_IMPLEMENTATION
    )
    changed = set(
        _git("diff", "--name-only", f"{ACCEPTED_IMPLEMENTATION}..{head}").splitlines()
    )
    changed.update(_git("diff", "--name-only").splitlines())
    changed.update(_git("ls-files", "--others", "--exclude-standard").splitlines())
    assert changed == CL8_ADOPTION_PATHS
    for path in IMPLEMENTATION_PATHS - {
        "current/tests/test_v3_10_issue72_gui_runtime.py"
    }:
        assert _git("rev-parse", f"{ACCEPTED_IMPLEMENTATION}:{path}") == _git(
            "rev-parse", f"{head}:{path}"
        )


def _profile(ticker: str, interval: str) -> MultiInstrumentProfile:
    config = BotConfig(
        ticker=ticker,
        class_code="TQBR",
        candle_interval=interval,
        primary_strategy="sma",
        shadow_strategies=(),
        fast_window=2,
        slow_window=5,
        volatility_window=5,
        lookback_days=5,
        max_order_lots=1,
    )
    return MultiInstrumentProfile(
        instrument_id=f"uid-{ticker.lower()}",
        strategy_profile=bot_config_to_profile(
            config,
            connect_timeout_seconds=8,
            read_timeout_seconds=25,
        ),
        scheduler_cadence_seconds=1,
        decision_cadence_seconds=1,
        risk_refresh_cadence_seconds=1,
        reconciliation_cadence_seconds=1,
        market_status_cadence_seconds=1,
    )


def _profiles() -> tuple[MultiInstrumentProfile, ...]:
    return (
        _profile("SBER", "CANDLE_INTERVAL_HOUR"),
        _profile("LKOH", "CANDLE_INTERVAL_30_MIN"),
        _profile("YDEX", "CANDLE_INTERVAL_15_MIN"),
    )


def _stores(root: Path):
    profile_store = MultiInstrumentProfileStore(root / "multi_instrument_profiles.json")
    profiles = profile_store.save_mode("SANDBOX_EXECUTION", _profiles())
    runtime_store = InstrumentRuntimeStore(root / "instrument_runtimes.json")
    runtimes = profile_store.bootstrap_runtime_registry(
        mode="SANDBOX_EXECUTION",
        account_id=ACCOUNT,
        runtime_store=runtime_store,
    )
    return profile_store, runtime_store, profiles, runtimes


def _authority(state: RuntimeCashAuthorityState) -> RuntimeCashAuthorityRecord:
    # A narrow test double keeps offline Issue #72 tests independent from the
    # separately accepted CL7 transition ceremony while preserving type checks.
    record = object.__new__(RuntimeCashAuthorityRecord)
    object.__setattr__(record, "state", state)
    object.__setattr__(record, "account_scope_sha256", SCOPE)
    object.__setattr__(
        record,
        "pending_dispatch_proof_sha256",
        "f" * 64
        if state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
        else None,
    )
    object.__setattr__(record, "record_revision", 9)
    return record


class _Authority:
    def __init__(self, state: RuntimeCashAuthorityState) -> None:
        self.record = _authority(state)

    def status(self) -> RuntimeCashAuthorityRecord:
        return self.record


class _Portfolio:
    revision = 12
    freshness = "FRESH"
    blocking = False

    def __init__(self, profiles: tuple[MultiInstrumentProfile, ...]) -> None:
        self.positions = {
            item.instrument_id: SimpleNamespace(
                instrument_id=item.instrument_id,
                actual_lots=index + 1,
                target_lots=index + 2,
                target=SimpleNamespace(target_lots=index + 2),
                ownership_status="OWNED",
                reconciliation=SimpleNamespace(status="MATCHED", blocking=False),
            )
            for index, item in enumerate(profiles)
        }

    def load(self, *, expected_account_id: str):
        assert expected_account_id == ACCOUNT
        return self

    def position(self, instrument_id: str):
        return self.positions.get(instrument_id)


class _RiskStateStore:
    def load_account(self, account_id: str):
        assert account_id == ACCOUNT
        return SimpleNamespace(
            revision=7,
            kill_switch_active=False,
            risk_resync_required=False,
            instrument_kill_switches=(),
        )


class _RiskRuntime:
    account_id = ACCOUNT
    mode = "SANDBOX_EXECUTION"
    state_store = _RiskStateStore()

    @staticmethod
    def current_policy_hash() -> str:
        return "a" * 64


class _PortfolioRiskRuntime:
    account_id = ACCOUNT

    @staticmethod
    def recalculate_current(manager, portfolio_repository):
        assert manager.account_id == ACCOUNT
        assert portfolio_repository is not None
        return SimpleNamespace(account_id=ACCOUNT, status="READY")


@dataclass
class _CentralState:
    account_id: str = ACCOUNT
    revision: int = 4
    intents: tuple[object, ...] = ()

    @property
    def blocking_intent(self):
        return next(
            (
                item
                for item in self.intents
                if item.status in {"IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}
            ),
            None,
        )


class _Manager:
    account_id = ACCOUNT

    def __init__(self, state: _CentralState | None = None) -> None:
        self.current = state or _CentralState()

    def state(self):
        return self.current


class _Coordinator:
    def __init__(self, manager, portfolio, portfolio_risk) -> None:
        self.manager = manager
        self.portfolio_repository = portfolio
        self.risk_runtime = _RiskRuntime()
        self.portfolio_risk_runtime = portfolio_risk
        self.calls: list[object] = []

    def coordinate(self, proposal, *_args, **_kwargs):
        self.calls.append(proposal)
        return SimpleNamespace(status="QUEUED")


class _Adapter:
    def __init__(self, manager, portfolio_risk, risk_runtime, cash_authority) -> None:
        self.manager = manager
        self.policy = SimpleNamespace(account_id=ACCOUNT, armed=True)
        self.portfolio_risk_runtime = portfolio_risk
        self.risk_runtime = risk_runtime
        self.cash_authority_manager = cash_authority
        self.dispatches = 0

    def dispatch_next(self, repository):
        self.dispatches += 1
        return SimpleNamespace(status="IDLE")


def _controller(root: Path, state=RuntimeCashAuthorityState.EXACT_CASH_ARMED):
    profile_store, runtime_store, profiles, runtimes = _stores(root)
    portfolio = _Portfolio(profiles)
    manager = _Manager()
    portfolio_risk = _PortfolioRiskRuntime()
    coordinator = _Coordinator(manager, portfolio, portfolio_risk)
    authority = _Authority(state)
    adapter = _Adapter(
        manager,
        portfolio_risk,
        coordinator.risk_runtime,
        authority,
    )
    controller = GuiRuntimeController(
        profile_store=profile_store,
        runtime_store=runtime_store,
        portfolio_repository=portfolio,
        central_order_coordinator=coordinator,
        execution_adapter=adapter,
        portfolio_risk_runtime=portfolio_risk,
        cash_authority=authority,
        account_id=ACCOUNT,
        account_scope_sha256=SCOPE,
    )
    return controller, runtime_store, profiles, runtimes, coordinator, adapter


def test_group_store_cas_commits_complete_registry_and_exact_readback(tmp_path: Path):
    _, store, _, before = _stores(tmp_path)
    successor = tuple(item.start() for item in before)

    committed = store.compare_and_swap_all(
        expected=before,
        successor=successor,
        expected_account_id=ACCOUNT,
    )

    assert committed == successor
    assert store.load(expected_account_id=ACCOUNT) == successor
    assert {item.status for item in committed} == {"ACTIVE"}


def test_group_store_cas_mismatch_performs_zero_writes(tmp_path: Path):
    _, store, _, before = _stores(tmp_path)
    managed = (store.path, store.path.with_name(store.path.name + ".sha256"))
    prior = {item: item.read_bytes() for item in managed}
    stale = tuple(item.start() for item in before)

    with pytest.raises(InstrumentRuntimeConflictError, match="GROUP_CAS_MISMATCH"):
        store.compare_and_swap_all(
            expected=stale,
            successor=before,
            expected_account_id=ACCOUNT,
        )

    assert prior == {item: item.read_bytes() for item in managed}


def test_group_store_rejects_nonexact_committed_readback(tmp_path: Path, monkeypatch):
    _, store, _, before = _stores(tmp_path)
    successor = tuple(item.start() for item in before)
    reads = iter((before, before))
    monkeypatch.setattr(
        store,
        "_load_unlocked",
        lambda *, expected_account_id: next(reads),
    )

    with pytest.raises(InstrumentRuntimeStateError, match="GROUP_POSTCONDITION_FAILED"):
        store.compare_and_swap_all(
            expected=before,
            successor=successor,
            expected_account_id=ACCOUNT,
        )


def test_group_store_maps_unreadable_committed_readback_to_postcondition(
    tmp_path: Path,
    monkeypatch,
):
    _, store, _, before = _stores(tmp_path)
    successor = tuple(item.start() for item in before)
    reads = 0

    def load(*, expected_account_id):
        nonlocal reads
        reads += 1
        if reads == 1:
            return before
        raise InstrumentRuntimeStateError("committed checksum invalid")

    monkeypatch.setattr(store, "_load_unlocked", load)

    with pytest.raises(InstrumentRuntimeStateError, match="GROUP_POSTCONDITION_FAILED"):
        store.compare_and_swap_all(
            expected=before,
            successor=successor,
            expected_account_id=ACCOUNT,
        )


def test_group_start_is_all_or_nothing_when_one_runtime_is_blocked(tmp_path: Path):
    _, store, _, before = _stores(tmp_path)
    blocked = (*before[:-1], before[-1].block())
    store.save(blocked)
    scheduler = GlobalScheduler(blocked, store=store)

    with pytest.raises(InstrumentRuntimeConflictError, match="cannot start"):
        scheduler.start_configured_set(
            tuple(item.runtime_key for item in blocked),
            expected_account_id=ACCOUNT,
        )

    persisted = store.load(expected_account_id=ACCOUNT)
    assert persisted == blocked
    assert scheduler.runtimes == tuple(
        sorted(blocked, key=lambda item: item.runtime_key)
    )
    _emit_behavior_counters(
        active_runtimes=sum(item.status == "ACTIVE" for item in persisted)
    )


def test_controller_rejects_distinct_portfolio_risk_instances(tmp_path: Path):
    profile_store, runtime_store, profiles, _ = _stores(tmp_path)
    portfolio = _Portfolio(profiles)
    manager = _Manager()
    first = SimpleNamespace(account_id=ACCOUNT)
    second = SimpleNamespace(account_id=ACCOUNT)

    with pytest.raises(GuiRuntimeBlockedError, match="PORTFOLIO_RISK_RUNTIME_MISMATCH"):
        GuiRuntimeController(
            profile_store=profile_store,
            runtime_store=runtime_store,
            portfolio_repository=portfolio,
            central_order_coordinator=_Coordinator(manager, portfolio, first),
            execution_adapter=_Adapter(
                manager,
                second,
                _RiskRuntime(),
                _Authority(RuntimeCashAuthorityState.EXACT_CASH_ARMED),
            ),
            portfolio_risk_runtime=first,
            cash_authority=_Authority(RuntimeCashAuthorityState.EXACT_CASH_ARMED),
            account_id=ACCOUNT,
            account_scope_sha256=SCOPE,
        )


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (RuntimeCashAuthorityState.LEGACY_ACTIVE, "CL7_EXACT_AUTHORITY_REQUIRED"),
        (RuntimeCashAuthorityState.CUTOVER_PREPARED, "CL7_CUTOVER_INCOMPLETE"),
        (RuntimeCashAuthorityState.CUTOVER_CONFIRMED, "CL7_CUTOVER_INCOMPLETE"),
        (RuntimeCashAuthorityState.EXACT_CASH_DISARMED, "CL7_EXACT_AUTHORITY_DISARMED"),
        (
            RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING,
            "CL7_RECOVERY_REQUIRED",
        ),
    ],
)
def test_controller_enforces_closed_cl7_start_matrix(tmp_path: Path, state, reason):
    controller, store, *_ = _controller(tmp_path, state)

    with pytest.raises(GuiRuntimeBlockedError, match=reason):
        controller.start_configured_set()
    _emit_behavior_counters(
        active_runtimes=sum(
            item.status == "ACTIVE" for item in store.load(expected_account_id=ACCOUNT)
        )
    )


def test_controller_starts_and_stops_exact_three_runtime_set(tmp_path: Path):
    controller, store, *_ = _controller(tmp_path)

    started = controller.start_configured_set()
    stopped = controller.stop_configured_set()

    assert started.status == "ACTIVE"
    assert {item[1] for item in started.runtime_statuses} == {"ACTIVE"}
    assert stopped.status == "STOPPED"
    assert {item.status for item in store.load(expected_account_id=ACCOUNT)} == {
        "STOPPED"
    }
    _emit_behavior_counters(
        active_runtimes=sum(
            status == "ACTIVE" for _, status in started.runtime_statuses
        )
    )


def test_stop_preserves_unresolved_custody_and_is_idempotent(tmp_path: Path):
    controller, store, *_ = _controller(tmp_path)
    controller.start_configured_set()
    pending = SimpleNamespace(status="UNCERTAIN", custody="provider-unknown")
    controller.central_order_coordinator.manager.current = _CentralState(
        intents=(pending,)
    )

    first = controller.stop_configured_set()
    second = controller.stop_configured_set()

    assert first.status == second.status == "STOPPED"
    assert first.recovery_required is second.recovery_required is True
    assert controller.central_order_coordinator.manager.state().intents == (pending,)
    persisted = store.load(expected_account_id=ACCOUNT)
    assert {item.status for item in persisted} == {"STOPPED"}
    _emit_behavior_counters(
        active_runtimes=sum(item.status == "ACTIVE" for item in persisted)
    )


def test_restart_with_pending_state_is_recovery_first(tmp_path: Path):
    controller, store, *_ = _controller(tmp_path)
    controller.central_order_coordinator.manager.current = _CentralState(
        intents=(SimpleNamespace(status="UNCERTAIN"),)
    )

    with pytest.raises(GuiRuntimeBlockedError, match="RECOVERY_REQUIRED"):
        controller.start_configured_set()

    assert {item.status for item in store.load(expected_account_id=ACCOUNT)} == {
        "STOPPED"
    }
    _emit_behavior_counters()


def test_restart_restores_set_without_duplicate_proposal(tmp_path: Path):
    controller, store, profiles, _, coordinator, adapter = _controller(tmp_path)
    first = controller.restore()
    restarted = GuiRuntimeController(
        profile_store=controller.profile_store,
        runtime_store=store,
        portfolio_repository=controller.portfolio_repository,
        central_order_coordinator=coordinator,
        execution_adapter=adapter,
        portfolio_risk_runtime=controller.portfolio_risk_runtime,
        cash_authority=controller.cash_authority,
        account_id=ACCOUNT,
        account_scope_sha256=SCOPE,
        session_id="session-b",
    )
    second = restarted.restore()

    assert first.identity_sha256 == second.identity_sha256
    assert len(second.bindings) == len(profiles) == 3
    assert coordinator.calls == []
    assert adapter.dispatches == 0
    _emit_behavior_counters(
        active_runtimes=sum(
            item.runtime.status == "ACTIVE" for item in second.bindings
        ),
        proposal=len(coordinator.calls),
        central_intent=len(coordinator.calls),
        adapter_dispatch=adapter.dispatches,
    )


@pytest.mark.parametrize(
    "blocker",
    ["portfolio", "central", "risk_kill", "risk_resync", "portfolio_risk"],
)
def test_controller_prevalidation_blockers_create_no_proposal_or_dispatch(
    tmp_path: Path,
    blocker: str,
):
    controller, store, profiles, _, coordinator, adapter = _controller(tmp_path)
    if blocker == "portfolio":
        controller.portfolio_repository.positions[
            profiles[0].instrument_id
        ].target = None
    elif blocker == "central":
        coordinator.manager.current = _CentralState(
            intents=(SimpleNamespace(status="SUBMITTED"),)
        )
    elif blocker in {"risk_kill", "risk_resync"}:
        state = SimpleNamespace(
            revision=8,
            kill_switch_active=blocker == "risk_kill",
            risk_resync_required=blocker == "risk_resync",
            instrument_kill_switches=(),
        )
        coordinator.risk_runtime.state_store = SimpleNamespace(
            load_account=lambda _account_id: state
        )
    else:
        coordinator.portfolio_risk_runtime = SimpleNamespace(account_id=ACCOUNT)

    with pytest.raises(GuiRuntimeBlockedError):
        controller.start_configured_set()

    persisted = store.load(expected_account_id=ACCOUNT)
    assert {item.status for item in persisted} == {"STOPPED"}
    assert coordinator.calls == []
    assert adapter.dispatches == 0
    _emit_behavior_counters(
        active_runtimes=sum(item.status == "ACTIVE" for item in persisted),
        proposal=len(coordinator.calls),
        central_intent=len(coordinator.calls),
        adapter_dispatch=adapter.dispatches,
    )


def test_controller_rejects_cross_account_scope(tmp_path: Path):
    controller, *_ = _controller(tmp_path)
    controller.central_order_coordinator.manager.account_id = "another-account"

    with pytest.raises(
        GuiRuntimeBlockedError, match="CENTRAL_OWNER_MISMATCH|ACCOUNT_SCOPE_MISMATCH"
    ):
        controller.start_configured_set()
    _emit_behavior_counters()


def test_account_disposition_validator_binds_closed_enum_and_trusted_hash(
    tmp_path: Path,
):
    value = {
        "version": 1,
        "disposition": "REDUNDANT_ACCOUNT_RETAINED_WITH_REASON",
        "experiment_id": "ISSUE72-SANDBOX-ACCOUNT-DISPOSITION-V1",
        "preparation_record_sha256": "1" * 64,
        "start_experiment_record_sha256": "2" * 64,
        "observed_at": "2026-09-12T10:00:00+00:00",
        "fresh_until": "2026-09-12T10:05:00+00:00",
        "account_list_evidence_sha256": "3" * 64,
        "active_account_scope_sha256": "4" * 64,
        "redundant_account_scope_sha256": "5" * 64,
        "runtime_reference_scan_sha256": "6" * 64,
        "configuration_reference_scan_sha256": "7" * 64,
        "backup_reference_scan_sha256": "8" * 64,
        "acceptance_reference_scan_sha256": "9" * 64,
        "open_positions_status": "CLEAR",
        "open_orders_status": "CLEAR",
        "pending_uncertain_status": "CLEAR",
        "raw_identifiers_absent": True,
        "variant": {
            "reason_code": "ACCOUNT_RETAINED_FOR_AUDIT",
            "reason_text_sha256": "a" * 64,
            "review_record_sha256": "b" * 64,
            "provider_mutation_performed": False,
        },
    }
    value["record_sha256"] = q0.digest(value)
    path = tmp_path / "disposition.json"
    path.write_bytes(q0.canonical_bytes(value))

    valid, summary, record = q0._validate_external(
        path,
        value["record_sha256"],
        validated_at="2026-09-12T10:02:00+00:00",
    )
    assert valid is True
    assert summary == {
        "disposition": "REDUNDANT_ACCOUNT_RETAINED_WITH_REASON",
        "disposition_evidence_sha256": value["record_sha256"],
    }
    assert record == value

    value["disposition"] = "PREFILLED_PASS"
    path.write_bytes(q0.canonical_bytes(value))
    assert (
        q0._validate_external(
            path,
            value["record_sha256"],
            validated_at="2026-09-12T10:02:00+00:00",
        )[0]
        is False
    )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("open_positions_status", "OPEN"),
        ("open_orders_status", "UNKNOWN"),
        ("pending_uncertain_status", "PRESENT"),
    ],
)
def test_account_disposition_rejects_unsafe_statuses(tmp_path: Path, field, invalid):
    value = {
        "version": 1,
        "disposition": "REDUNDANT_ACCOUNT_RETAINED_WITH_REASON",
        "experiment_id": "ISSUE72-SANDBOX-ACCOUNT-DISPOSITION-V1",
        "preparation_record_sha256": "1" * 64,
        "start_experiment_record_sha256": "2" * 64,
        "observed_at": "2026-09-12T10:00:00+00:00",
        "fresh_until": "2026-09-12T10:05:00+00:00",
        "account_list_evidence_sha256": "3" * 64,
        "active_account_scope_sha256": "4" * 64,
        "redundant_account_scope_sha256": "5" * 64,
        "runtime_reference_scan_sha256": "6" * 64,
        "configuration_reference_scan_sha256": "7" * 64,
        "backup_reference_scan_sha256": "8" * 64,
        "acceptance_reference_scan_sha256": "9" * 64,
        "open_positions_status": "CLEAR",
        "open_orders_status": "CLEAR",
        "pending_uncertain_status": "CLEAR",
        "raw_identifiers_absent": True,
        "variant": {
            "reason_code": "ACCOUNT_RETAINED_FOR_AUDIT",
            "reason_text_sha256": "a" * 64,
            "review_record_sha256": "b" * 64,
            "provider_mutation_performed": False,
        },
    }
    value[field] = invalid
    value["record_sha256"] = q0.digest(value)
    path = tmp_path / "disposition.json"
    path.write_bytes(q0.canonical_bytes(value))

    assert (
        q0._validate_external(
            path,
            value["record_sha256"],
            validated_at="2026-09-12T10:02:00+00:00",
        )[0]
        is False
    )
    assert (
        q0._validate_external(
            path,
            value["record_sha256"],
            validated_at="2026-09-12T10:06:00+00:00",
        )[0]
        is False
    )


def test_q0_producer_table_and_verify_mode_are_closed_and_rerun_bound():
    assert tuple(q0.PRODUCERS) == q0.CASE_IDS
    assert len(q0.PRODUCERS) == 32
    assert all(spec.kind in q0.ALLOWED_PRODUCER_KINDS for spec in q0.PRODUCERS.values())
    assert all(
        "*" not in spec.producer_id and "?" not in spec.producer_id
        for spec in q0.PRODUCERS.values()
    )
    source = (CURRENT / "tools/v3_10_issue72_q0_evidence.py").read_text(
        encoding="utf-8"
    )
    verify_node = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "verify"
    )
    called_names = {
        node.func.id
        for node in ast.walk(verify_node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "generate" in called_names
    assert "def _behavior_counters(" not in source
    for required in (
        "--candidate",
        "--candidate-tree",
        "--accepted-contract",
        "--accepted-contract-tree",
        "--account-disposition-sha256",
    ):
        assert required in source


def test_q0_behavior_counters_are_parsed_only_from_producer_output():
    rendered = {key: 0 for key in q0.COUNTER_KEYS}
    rendered.update(proposal=1, central_intent=1, adapter_dispatch=1)
    marker = "ISSUE72_COUNTERS=" + json.dumps(
        rendered, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )

    assert q0._parse_behavior_counters(marker) == rendered
    assert q0._parse_behavior_counters("1 passed") is None


class _Hooks:
    def __init__(self, profile: MultiInstrumentProfile) -> None:
        self.profile = profile

    def refresh_market_status(self, runtime, now):
        return "OPEN"

    def refresh_risk(self, runtime, now):
        return "READY"

    def reconcile_portfolio(self, runtime, now):
        return "MATCHED"

    def evaluate_closed_candle(self, runtime, candle_time, now):
        return {"runtime_key": runtime.runtime_key}

    def coordination_request(self, runtime, proposal, candle_time, now):
        return GuiCoordinationRequest(
            proposal=proposal,
            profile=self.profile,
            candles=SimpleNamespace(),
            lot_size=1,
        )


def test_proposal_routes_through_central_then_execution_adapter(tmp_path: Path):
    controller, _, profiles, _, coordinator, adapter = _controller(tmp_path)
    controller.start_configured_set()
    runtime = controller.scheduler.runtimes[0]

    result = controller.service_tick(
        now=T0,
        latest_closed_candles={runtime.runtime_key: T0},
        hooks=_Hooks(
            next(
                item
                for item in profiles
                if item.instrument_id == runtime.config.instrument_id
            )
        ),
    )

    assert not result.failures
    assert coordinator.calls
    assert adapter.dispatches == 1
    _emit_behavior_counters(
        proposal=len(coordinator.calls),
        central_intent=len(coordinator.calls),
        adapter_dispatch=adapter.dispatches,
    )


def test_nonnull_proposal_without_central_request_fails_before_watermark(
    tmp_path: Path,
):
    controller, store, profiles, _, coordinator, adapter = _controller(tmp_path)
    controller.start_configured_set()
    runtime = controller.scheduler.runtimes[0]

    class MissingRequestHooks(_Hooks):
        def coordination_request(self, runtime, proposal, candle_time, now):
            return None

    result = controller.service_tick(
        now=T0,
        latest_closed_candles={runtime.runtime_key: T0},
        hooks=MissingRequestHooks(
            next(
                item
                for item in profiles
                if item.instrument_id == runtime.config.instrument_id
            )
        ),
    )

    assert result.failures
    assert coordinator.calls == []
    assert adapter.dispatches == 0
    persisted = store.load(expected_account_id=ACCOUNT)
    assert (
        next(
            item for item in persisted if item.runtime_key == runtime.runtime_key
        ).last_processed_candle
        is None
    )


def test_market_idle_and_disconnect_do_not_create_proposals(tmp_path: Path):
    controller, _, profiles, _, coordinator, adapter = _controller(tmp_path)
    controller.start_configured_set()
    runtime = controller.scheduler.runtimes[0]
    hooks = _Hooks(
        next(
            item
            for item in profiles
            if item.instrument_id == runtime.config.instrument_id
        )
    )
    controller.set_market_state("MARKET_IDLE")
    with pytest.raises(GuiRuntimeBlockedError, match="MARKET_IDLE"):
        controller.service_tick(now=T0, latest_closed_candles={}, hooks=hooks)
    controller.set_market_state("OPEN")
    controller.set_connected(False)
    with pytest.raises(GuiRuntimeBlockedError, match="PROVIDER_DISCONNECTED"):
        controller.service_tick(now=T0, latest_closed_candles={}, hooks=hooks)
    assert coordinator.calls == []
    assert adapter.dispatches == 0
    _emit_behavior_counters(
        proposal=len(coordinator.calls),
        central_intent=len(coordinator.calls),
        adapter_dispatch=adapter.dispatches,
    )


def test_open_market_idle_open_preserves_set_and_watermark(tmp_path: Path):
    controller, store, profiles, _, coordinator, adapter = _controller(tmp_path)
    started = controller.start_configured_set()
    runtime = controller.scheduler.runtimes[0]
    hooks = _Hooks(
        next(
            item
            for item in profiles
            if item.instrument_id == runtime.config.instrument_id
        )
    )
    first = controller.service_tick(
        now=T0,
        latest_closed_candles={runtime.runtime_key: T0},
        hooks=hooks,
    )
    controller.set_market_state("MARKET_IDLE")
    with pytest.raises(GuiRuntimeBlockedError, match="MARKET_IDLE"):
        controller.service_tick(now=T0, latest_closed_candles={}, hooks=hooks)
    controller.set_market_state("OPEN")
    second = controller.service_tick(
        now=T0,
        latest_closed_candles={runtime.runtime_key: T0},
        hooks=hooks,
    )
    stopped = controller.stop_configured_set()

    assert not first.failures and not second.failures
    assert len(coordinator.calls) == 1
    assert adapter.dispatches == 1
    assert stopped.status == "STOPPED"
    persisted = store.load(expected_account_id=ACCOUNT)
    assert (
        next(
            item for item in persisted if item.runtime_key == runtime.runtime_key
        ).last_processed_candle
        == T0
    )
    _emit_behavior_counters(
        active_runtimes=sum(
            status == "ACTIVE" for _, status in started.runtime_statuses
        ),
        proposal=len(coordinator.calls),
        central_intent=len(coordinator.calls),
        adapter_dispatch=adapter.dispatches,
    )


def test_dashboard_binds_positions_central_risk_and_cl7_without_collapsing_rows(
    tmp_path: Path,
):
    controller, _, profiles, runtimes, *_ = _controller(tmp_path)
    portfolio = controller.portfolio_repository
    first = profiles[0].instrument_id
    intent = SimpleNamespace(
        status="QUEUED",
        reserved_cash_kopecks=12345,
        candidate=SimpleNamespace(instrument_id=first),
    )
    central = _CentralState(intents=(intent,))
    snapshot = build_multi_instrument_dashboard(
        profiles,
        runtimes,
        mode="SANDBOX_EXECUTION",
        portfolio_state=portfolio,
        central_state=central,
        risk_snapshot={
            "risk_policy_hash": "a" * 64,
            "risk_state_revision": 7,
            "risk_readiness": "READY",
            "kill_switch_active": False,
            "risk_resync_required": False,
        },
        portfolio_risk_snapshot={item.instrument_id: "PASS" for item in profiles},
        authority_record=_authority(RuntimeCashAuthorityState.EXACT_CASH_ARMED),
        cash_actionability_status="READY",
        account_scope_sha256=SCOPE,
    )

    assert len(snapshot.rows) == 3
    assert sum(int(row.actual_lots) > 0 for row in snapshot.rows) >= 2
    selected = next(row for row in snapshot.rows if row.instrument_id == first)
    assert selected.actual_lots != selected.target_lots
    assert selected.queued_reserved_cash == 12345
    assert selected.pending_status == "PRESENT"
    assert selected.reconciliation_status == "MATCHED"
    assert selected.cl7_authority_mode == "EXACT_CASH_ARMED"
    _emit_behavior_counters()


def test_dashboard_exposes_each_central_lifecycle_state(tmp_path: Path):
    controller, _, profiles, runtimes, *_ = _controller(tmp_path)
    first = profiles[0].instrument_id
    intents = tuple(
        SimpleNamespace(
            status=status,
            reserved_cash_kopecks=100 if status == "QUEUED" else 0,
            candidate=SimpleNamespace(instrument_id=first),
        )
        for status in ("QUEUED", "IN_FLIGHT", "SUBMITTED", "UNCERTAIN")
    )
    dashboard = build_multi_instrument_dashboard(
        profiles,
        runtimes,
        mode="SANDBOX_EXECUTION",
        portfolio_state=controller.portfolio_repository,
        central_state=_CentralState(intents=intents),
        risk_snapshot={
            "risk_policy_hash": "a" * 64,
            "risk_state_revision": 7,
            "risk_readiness": "READY",
            "kill_switch_active": False,
            "risk_resync_required": False,
        },
        portfolio_risk_snapshot={item.instrument_id: "PASS" for item in profiles},
        authority_record=_authority(RuntimeCashAuthorityState.EXACT_CASH_ARMED),
        cash_actionability_status="READY",
        account_scope_sha256=SCOPE,
    )
    row = next(item for item in dashboard.rows if item.instrument_id == first)

    assert row.pending_status == "PRESENT"
    assert row.in_flight_status == "PRESENT"
    assert row.submitted_status == "PRESENT"
    assert row.uncertain_status == "PRESENT"
    assert row.queued_reserved_cash == 100
    _emit_behavior_counters()


def test_dashboard_missing_owner_evidence_fails_closed(tmp_path: Path):
    _, _, profiles, runtimes = _stores(tmp_path)
    dashboard = build_multi_instrument_dashboard(
        profiles,
        runtimes,
        mode="SANDBOX_EXECUTION",
    )

    assert dashboard.rows
    for row in dashboard.rows:
        assert row.source_status == "UNKNOWN"
        assert row.actual_lots == "UNKNOWN"
        assert row.portfolio_risk_status == "UNKNOWN"
        assert row.cl7_authority_mode == "UNKNOWN"
    _emit_behavior_counters()


def test_controller_dashboard_reads_mandatory_owner_statuses(tmp_path: Path):
    controller, *_ = _controller(tmp_path)

    dashboard = controller.dashboard()

    assert dashboard.state == "READY"
    assert {row.portfolio_risk_status for row in dashboard.rows} == {"READY"}
    assert {row.cash_actionability_status for row in dashboard.rows} == {
        "LOCKED_REVALIDATION_REQUIRED"
    }
    assert {row.source_status for row in dashboard.rows} == {"READY"}


def test_transient_failures_coalesce_to_one_status_surface():
    source = (CURRENT / "desktop_gui.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    start = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_start_robot_loop"
    )
    calls = {
        node.func.attr
        for node in ast.walk(start)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "showerror" not in calls
    assert "showwarning" not in calls
    assert "showinfo" not in calls
    assert "put" in calls
    _emit_behavior_counters(
        popup_events=sum(
            item in calls for item in ("showerror", "showwarning", "showinfo")
        )
    )


def test_gui_process_always_receives_one_controller_and_raw_ids_are_sanitized():
    source = (CURRENT / "desktop_gui.py").read_text(encoding="utf-8")
    main_node = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    calls = [node for node in ast.walk(main_node) if isinstance(node, ast.Call)]
    assert any(
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "GuiRuntimeController"
        and node.func.attr == "blocked"
        for node in calls
    )
    gui_call = next(
        node
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "TradingRobotGUI"
    )
    assert any(keyword.arg == "gui_runtime_controller" for keyword in gui_call.keywords)
    blocked = GuiRuntimeController.blocked("GUI_RUNTIME_COMPOSITION_REQUIRED")
    assert isinstance(blocked, GuiRuntimeController)
    assert blocked.service_ready is False

    rendered = _privacy_safe_gui_value(
        {"accounts": [{"id": "RAW-ACCOUNT-ID", "status": "OPEN"}]}
    )
    assert "RAW-ACCOUNT-ID" not in json.dumps(rendered)
    assert rendered["accounts"][0]["id_sha256"] == q0.digest(b"RAW-ACCOUNT-ID")


def test_active_gui_has_no_legacy_bot_provider_mutation_or_risk_write_callback():
    source = (CURRENT / "desktop_gui.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_attributes = {
        "post_order",
        "post_order_once",
        "execute",
        "close_unattributed_position",
        "apply_max_orders_per_day",
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "SandboxTradingBot" not in source
    assert not forbidden_attributes.intersection(calls)
    assert all(label not in source for label in ("v3.6", "v3.7", "v3.8", "v3.9"))
    json_dumps = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "json"
        and node.func.attr == "dumps"
    ]
    assert json_dumps
    assert all(
        node.args
        and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Name)
        and node.args[0].func.id == "_privacy_safe_gui_value"
        for node in json_dumps
    )


def test_contract_fixture_and_governance_documents_are_synthetic_and_provider_free():
    vector = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert vector["provider_calls_per_offline_case"] == 0
    assert vector["provider_mutations_per_offline_case"] == 0
    assert len(vector["configured_instruments"]) == 3
    document_payload = q0._document_payload(ROOT, "I72-32")
    assert all(item["passed"] for item in document_payload["assertions"])
    assert (
        "tests/test_v3_10_reporting_risk_cash_context.py::test_v310_cl6_01_exact_contract_lineage_and_three_path_delta"
        in q0.INHERITED_FAILURES
    )
    assert CONTRACT.is_file()
    risk_adr = (
        ROOT / "docs/project/V3_10_ISSUE72_RISK_POLICY_GUI_ADR_RU.md"
    ).read_text(encoding="utf-8")
    assert "OPTION A = READ_ONLY_GUI_PLUS_EXISTING_ACCEPTED_OPERATOR_TOOLS" in risk_adr
    for relative in (
        "docs/project/V3_10_ISSUE72_GUI_RUNTIME_REVIEW_RU.md",
        "docs/project/V3_10_ISSUE72_RISK_POLICY_GUI_ADR_RU.md",
        "docs/plans/V3_10_ISSUE72_MULTI_INSTRUMENT_SANDBOX_RUNBOOK_RU.md",
        "docs/plans/V3_10_ISSUE72_SANDBOX_ACCOUNT_CLEANUP_RUNBOOK_RU.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert (
            "START EXPERIMENT" in text
            or "READ_ONLY_GUI_PLUS_EXISTING_ACCEPTED_OPERATOR_TOOLS" in text
        )
