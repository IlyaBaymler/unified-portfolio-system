from __future__ import annotations

from trading_robot.dashboard_view import build_kill_switch_banner, full_account_id


def test_full_account_id_is_never_abbreviated():
    account_id = "00000000-0000-4000-8000-000000000042"
    assert full_account_id(account_id) == account_id


def test_kill_switch_banner_unknown_off_and_on():
    unknown = build_kill_switch_banner(None)
    assert unknown.state == "UNKNOWN"
    assert "UNKNOWN" in unknown.title

    off = build_kill_switch_banner({"kill_switch_active": False})
    assert off.state == "OFF"
    assert off.title == "KILL SWITCH: OFF"

    on = build_kill_switch_banner(
        {
            "kill_switch_active": True,
            "kill_switch_reason": "operator acceptance",
            "kill_switch_set_at": "2026-07-25T10:00:00+00:00",
            "kill_switch_actor": "risk_control_tool",
            "kill_switch_source": "CLI",
            "kill_switch_reduce_only_allowed": True,
        }
    )
    assert on.state == "ON"
    assert "НОВЫЕ ВХОДЫ ЗАБЛОКИРОВАНЫ" in on.title
    assert "operator acceptance" in on.detail
    assert "risk_control_tool" in on.detail
    assert "Reduce-only" in on.detail
