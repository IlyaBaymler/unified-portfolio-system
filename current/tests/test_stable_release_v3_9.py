from __future__ import annotations

import json
from pathlib import Path

import trading_robot
from tools.release_cleanup import CURRENT_FILES, find_legacy_files
from trading_robot.central_order_manager import CENTRAL_ORDER_SCHEMA_VERSION
from trading_robot.portfolio_model import PORTFOLIO_STATE_SCHEMA_VERSION
from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskStateStore

ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_BASELINE = "cddd80f3caf7191ecf2f85df9e1ccfea97af6cc4"
BRANCH_BASELINE = "dd3b9a35f5d2b2dfb9e8776facb81712848e2c7d"


def manifest() -> dict:
    return json.loads((ROOT / "build_manifest.json").read_text(encoding="utf-8"))


def test_stable_candidate_version_manifest_and_exact_baseline_are_consistent() -> None:
    data = manifest()

    assert trading_robot.__version__ == "0.3.9"
    assert data["software_version"] == trading_robot.__version__
    assert data["display_version"] == "v3.9.0"
    assert data["release_channel"] == "stable"
    assert data["build_date"] == "2026-08-15"
    assert data["implementation_baseline_commit"] == IMPLEMENTATION_BASELINE
    assert data["qualification_branch_base_commit"] == BRANCH_BASELINE
    assert data["sandbox_only"] is True
    assert data["real_account_execution"] is False


def test_release_candidate_does_not_claim_manual_m6_acceptance() -> None:
    qualification = manifest()["stable_qualification"]

    assert qualification == {
        "status": "candidate",
        "automated_source_preflight_complete": True,
        "automated_source_preflight_tests": 780,
        "automated_source_preflight_date": "2026-08-15",
        "deterministic_source_artifacts_complete": True,
        "deterministic_source_artifact_protocol": "deterministic-source-pair-v1",
        "user_acceptance": False,
        "final_burn_in_complete": False,
        "standalone_build_complete": False,
        "standalone_launch_complete": False,
        "upgrade_rollback_complete": False,
        "restart_disconnect_partial_fill_complete": False,
        "global_kill_switch_accepted": False,
        "instrument_kill_switch_accepted": False,
        "release_artifacts_complete": False,
    }


def test_v3_9_schema_and_execution_boundaries_are_explicit() -> None:
    data = manifest()

    assert data["portfolio_state_schema"] == PORTFOLIO_STATE_SCHEMA_VERSION == 2
    assert data["risk_state_schema"] == RiskStateStore.SCHEMA_VERSION == 4
    assert data["central_order_state_schema"] == CENTRAL_ORDER_SCHEMA_VERSION == 1
    assert data["portfolio_manager_mode"] == "canonical-only"
    assert data["multi_instrument_runtime"] is True
    assert data["multi_instrument_execution"] is True
    assert data["multi_asset_execution"] is False
    assert data["portfolio_risk_mode"] == "ENFORCED"
    assert data["automatic_position_adoption"] is False
    assert data["real_account_execution"] is False


def test_v3_9_m5_3_provenance_does_not_claim_executable_launch() -> None:
    evidence = manifest()["v3_9_m5_3_qualification"]

    assert evidence["status"] == "accepted-implementation-baseline"
    assert evidence["commit"] == IMPLEMENTATION_BASELINE
    assert evidence["post_merge_tests"] == 774
    assert evidence["persistence_final_review"] == "PASS"
    assert evidence["disposable_restore"] == "PASS"
    assert evidence["executable_launch_verified"] is False


def test_release_safety_defaults_remain_active() -> None:
    policy = RiskPolicy()
    data = manifest()

    assert policy.max_position_lots == 1
    assert policy.max_orders_per_day == 4
    assert data["default_max_position_lots"] == 1
    assert data["default_max_orders_per_day"] == 4


def test_v3_9_runtime_modules_and_operator_tools_are_present() -> None:
    required = {
        "trading_robot/central_order_coordinator.py",
        "trading_robot/central_order_manager.py",
        "trading_robot/instrument_runtime.py",
        "trading_robot/portfolio_risk_model.py",
        "trading_robot/portfolio_risk_evaluator.py",
        "trading_robot/portfolio_risk_runtime.py",
        "trading_robot/risk_persistence.py",
        "trading_robot/sandbox_execution_adapter.py",
        "tools/v3_9_risk_control.py",
        "tools/v3_9_persistence_qualification.py",
        "tools/v3_9_stable_preflight.py",
    }

    assert all((ROOT / name).is_file() for name in required)


def test_only_v3_9_root_release_documents_are_current() -> None:
    required = {
        "CHANGELOG_V3_9_0_STABLE_RU.md",
        "MASTER_UPDATE_2026-08-15_V3_9_0_STABLE_RU.md",
        "RELEASE_MANIFEST_V3_9_0_STABLE.txt",
        "UPDATE_TO_V3_9_0_STABLE.md",
        "V3_9_0_STABLE_ARCHITECTURE_RU.md",
        "V3_9_0_STABLE_RECOVERY_RUNBOOK_RU.md",
        "V3_9_0_STABLE_TEST_PLAN_RU.md",
        "VERIFY_V3_9_0_STABLE.bat",
        "install_and_verify_v3_9_0.bat",
    }

    assert required <= CURRENT_FILES
    assert all((ROOT / name).is_file() for name in required)
    assert find_legacy_files(ROOT) == []
    assert not list(ROOT.glob("*V3_7_0_STABLE*"))


def test_gui_uses_manifest_display_version_and_canonical_manager() -> None:
    source = (ROOT / "desktop_gui.py").read_text(encoding="utf-8")

    assert 'self.title(f"MOEX Research Robot {DISPLAY_VERSION}")' in source
    assert "CanonicalPortfolioManager" in source
    assert "PortfolioSnapshotBuilder" in source
    assert "LegacyPortfolioManager" not in source
