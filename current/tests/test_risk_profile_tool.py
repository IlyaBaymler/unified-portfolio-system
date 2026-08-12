from __future__ import annotations

import pytest

from risk_profile_tool import policy_for_preset


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
