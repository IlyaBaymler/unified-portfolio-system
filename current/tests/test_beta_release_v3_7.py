from __future__ import annotations

import json
from pathlib import Path

import trading_robot
from trading_robot.portfolio_model import PORTFOLIO_STATE_SCHEMA_VERSION
from trading_robot.risk import RiskPolicy
from tools.release_cleanup import CURRENT_FILES, find_legacy_files


ROOT = Path(__file__).resolve().parents[1]


def manifest() -> dict:
    return json.loads((ROOT / "build_manifest.json").read_text(encoding="utf-8"))


def test_beta_version_and_manifest_are_consistent():
    data = manifest()
    assert trading_robot.__version__ == "0.3.7b1"
    assert data["software_version"] == trading_robot.__version__
    assert data["display_version"] == "v3.7-beta1"
    assert data["release_channel"] == "beta"
    assert data["build_date"] == "2026-08-11"
    assert data["sandbox_only"] is True
    assert data["real_account_execution"] is False
    assert data["release_basis"] == "accepted v3.7-alpha3 plus beta1 stabilization"
    assert data["alpha3_user_acceptance"] is True


def test_beta1_stabilization_protocols_are_explicit():
    data = manifest()
    assert data["portfolio_warning_protocol"] == "observation-recompute-v1"
    assert data["secret_provider_probe"] == "metadata-only-v1"
    assert data["credential_probe_statuses"] == [
        "provider_unavailable",
        "credential_absent",
        "credential_present",
        ".env_fallback",
    ]
    assert data["credential_probe_plaintext_forbidden"] is True
    assert data["api_failure_classification_protocol"] == "transient-recovery-v1"
    assert data["compatibility_shadow_statuses"] == ["OK", "DEGRADED", "DISABLED"]
    assert data["compatibility_shadow_blocks_canonical_readiness"] is False


def test_beta1_portfolio_protocol_remains_canonical_only_schema2():
    data = manifest()
    assert data["portfolio_state_schema"] == PORTFOLIO_STATE_SCHEMA_VERSION == 2
    assert data["portfolio_manager_protocol"] == "canonical-portfolio-v2"
    assert data["portfolio_manager_mode"] == "canonical-only"
    assert data["canonical_portfolio_preflight"] is True
    assert data["canonical_only_preflight"] is True
    assert data["portfolio_preflight_protocol"] == "canonical-only-preflight-v2"
    assert data["portfolio_snapshot_lease_protocol"] == "revision-checksum-v1"
    assert data["portfolio_dual_read_protocol"] == "disabled-after-schema2-cutover"
    assert data["legacy_state_dual_read_enabled"] is False
    assert data["legacy_state_cutover_complete"] is True
    assert data["portfolio_transaction_protocol"] == "canonical-single-writer-v1"
    assert data["portfolio_cutover_protocol"] == "schema1-to-schema2-v1"
    assert data["portfolio_legacy_shadow_mode"] == "write-only"
    assert data["portfolio_migration_confirmation"] == "CUTOVER PORTFOLIO STATE 2"
    assert data["automatic_portfolio_cutover"] is False
    assert data["portfolio_preflight_shared_by_risk_execution"] is True
    assert data["portfolio_revision_recheck_before_post"] is True
    assert data["post_fill_canonical_reconciliation"] is True
    assert data["stable_execution_path_unchanged"] is True
    assert data["multi_asset_execution"] is False
    assert data["automatic_position_adoption"] is False


def test_previous_alpha_features_remain_enabled():
    data = manifest()
    assert data["position_origin_protocol"] == "position-origin-v1"
    assert data["external_close_ack_protocol"] == "external-close-ack-v1"
    assert data["risk_profile_gui_editor"] is True
    assert data["risk_profile_gui_sandbox_min_orders"] == 1
    assert data["risk_profile_gui_sandbox_max_orders"] == 100
    assert data["export_filename_protocol"] == "version-timestamp-collision-v1"
    assert data["transient_transport_error_protocol"] == "incomplete-read-retry-v1"
    assert data["incomplete_read_transient"] is True
    assert data["unsafe_post_replay_forbidden"] is True
    assert data["diagnostic_fill_feedback_protocol"] == "diagnostic-fill-feedback-v1"
    assert data["diagnostic_fill_modal_for_manual_actions"] is True
    assert data["diagnostic_post_fill_canonical_refresh"] is True
    assert data["report_export_default_protocol"] == "current-runtime-reports-v1"
    assert data["report_export_default_directory"] == "reports"
    assert data["report_export_remembers_old_version_path"] is False
    assert data["alpha2_user_acceptance"] is True


def test_stable_safety_defaults_remain_active_in_beta1():
    policy = RiskPolicy()
    assert policy.max_position_lots == 1
    assert policy.max_orders_per_day == 4
    data = manifest()
    assert data["default_max_position_lots"] == 1
    assert data["default_max_orders_per_day"] == 4


def test_beta_source_modules_and_cli_are_present():
    required = {
        "trading_robot/portfolio_model.py",
        "trading_robot/portfolio_adapters.py",
        "trading_robot/portfolio_repository.py",
        "trading_robot/portfolio_reconciler.py",
        "trading_robot/portfolio_manager.py",
        "trading_robot/portfolio_snapshot.py",
        "trading_robot/portfolio_preflight.py",
        "trading_robot/post_fill_portfolio.py",
        "trading_robot/portfolio_transactions.py",
        "trading_robot/portfolio_cutover.py",
        "trading_robot/secret_provider.py",
        "trading_robot/risk_reporting.py",
        "portfolio_tool.py",
        "portfolio_cutover_tool.py",
        "run_portfolio_tool.bat",
        "run_portfolio_cutover.bat",
    }
    assert all((ROOT / name).is_file() for name in required)


def test_beta_public_release_files_are_current_and_alpha_files_are_absent():
    required = {
        "CHANGELOG_V3_7_BETA1_RU.md",
        "MASTER_UPDATE_2026-08-11_V3_7_BETA1_RU.md",
        "RELEASE_MANIFEST_V3_7_BETA1.txt",
        "UPDATE_TO_V3_7_BETA1.md",
        "V3_7_BETA1_ARCHITECTURE_RU.md",
        "V3_7_BETA1_RECOVERY_RUNBOOK_RU.md",
        "V3_7_BETA1_TEST_PLAN_RU.md",
        "VERIFY_V3_7_BETA1.bat",
        "install_and_verify_v3_7_beta1.bat",
    }
    assert required <= CURRENT_FILES
    assert all((ROOT / name).is_file() for name in required)
    assert find_legacy_files(ROOT) == []
    assert not list(ROOT.glob("*V3_7_ALPHA3*"))


def test_gui_uses_canonical_manager_for_view_and_operator_repairs():
    source = (ROOT / "desktop_gui.py").read_text(encoding="utf-8")
    assert 'self.title(f"MOEX Research Robot {DISPLAY_VERSION}")' in source
    assert "CanonicalPortfolioManager" in source
    assert "PortfolioSnapshotBuilder" in source
    assert "LegacyPortfolioManager" not in source
    refresh = source[source.index("def _refresh_portfolio"):source.index("def _format_money")]
    assert "api.get_portfolio" not in refresh
    assert "self._run_background" in refresh
    assert "manager.recover_ownership(request)" in source
    assert "manager.acknowledge_external_close(request)" in source
