from __future__ import annotations

import pytest

from risk_profile_tool import confirm_portfolio_shadow, main, policy_for_preset
from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore


def test_risk_profile_presets_are_valid_and_distinct():
    default = policy_for_preset("default")
    max_one = policy_for_preset("max-one")
    block = policy_for_preset("block-new")
    permissive = policy_for_preset("permissive")
    sandbox = policy_for_preset("sandbox-alpha3")
    sandbox_current = policy_for_preset("sandbox-beta1")

    assert default.max_position_lots == 1
    assert sandbox.enabled is True
    assert sandbox.max_position_lots == 1
    assert sandbox_current.policy_hash == sandbox.policy_hash
    assert max_one.max_position_lots == 1
    assert block.max_position_lots == 0
    assert permissive.max_position_lots == 100
    assert len({default.policy_hash, block.policy_hash, permissive.policy_hash}) == 3


def test_unknown_risk_profile_preset_is_rejected():
    with pytest.raises(ValueError):
        policy_for_preset("mystery")


def test_confirm_portfolio_shadow_preserves_limits_and_requires_account_scope(
    tmp_path,
):
    store = RiskProfileStore(tmp_path / "risk_profiles.json")
    original = RiskPolicy(max_position_lots=3, cash_reserve_rub=12_345.0)
    store.save_profile(
        "SANDBOX_EXECUTION",
        original,
        account_scope="account-1",
    )

    saved = confirm_portfolio_shadow(
        store,
        mode="SANDBOX_EXECUTION",
        account_id="account-1",
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
    )

    assert saved["portfolio_policy_status"] == "READY"
    assert saved["policy"].portfolio_policy_configured is True
    assert saved["policy"].portfolio_policy_mode == "OBSERVE_ONLY"
    assert saved["policy"].max_position_lots == original.max_position_lots
    assert saved["policy"].cash_reserve_rub == original.cash_reserve_rub
    assert saved["source"] == "V3_9_M3_OPERATOR"
    with pytest.raises(ValueError, match="account scope"):
        confirm_portfolio_shadow(
            store,
            mode="SANDBOX_EXECUTION",
            account_id="account-2",
            confirmation="CONFIRM PORTFOLIO RISK POLICY",
        )


def test_confirm_portfolio_shadow_cli_reports_ready(tmp_path, capsys):
    path = tmp_path / "risk_profiles.json"
    RiskProfileStore(path).save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
        account_scope="account-1",
    )

    result = main(
        [
            "confirm-portfolio-shadow",
            "--file",
            str(path),
            "--mode",
            "SANDBOX_EXECUTION",
            "--account-id",
            "account-1",
            "--confirmation",
            "CONFIRM PORTFOLIO RISK POLICY",
        ]
    )

    assert result == 0
    assert '"portfolio_policy_status": "READY"' in capsys.readouterr().out
