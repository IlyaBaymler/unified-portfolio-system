from __future__ import annotations

from pathlib import Path

from trading_robot.dashboard_view import build_kill_switch_banner, full_account_id


def test_kill_switch_banner_is_explicit_for_all_states():
    unknown = build_kill_switch_banner(None)
    assert unknown.state == "UNKNOWN"
    assert "UNKNOWN" in unknown.title

    off = build_kill_switch_banner({"kill_switch_active": False})
    assert off.state == "OFF"
    assert off.title == "KILL SWITCH: OFF"

    on = build_kill_switch_banner(
        {
            "kill_switch_active": True,
            "kill_switch_reason": "acceptance",
            "kill_switch_set_at": "2026-07-25T10:00:00Z",
        },
        [
            {
                "event_type": "KILL_SWITCH_ENGAGED",
                "timestamp_utc": "2026-07-25T10:00:00Z",
                "payload": {"actor": "operator", "source": "risk_control_tool"},
            }
        ],
    )
    assert on.state == "ON"
    assert "НОВЫЕ ВХОДЫ ЗАБЛОКИРОВАНЫ" in on.title
    assert "acceptance" in on.detail
    assert "operator" in on.detail
    assert "risk_control_tool" in on.detail
    assert "Reduce-only" in on.detail


def test_full_account_id_is_never_abridged():
    account_id = "00000000-0000-4000-8000-000000000042"
    assert full_account_id(account_id) == account_id


def test_gui_source_contains_explicit_kill_switch_and_account_copy_controls():
    root = Path(__file__).resolve().parents[1]
    source = (root / "desktop_gui.py").read_text(encoding="utf-8")
    banner_source = (root / "trading_robot" / "dashboard_view.py").read_text(
        encoding="utf-8"
    )
    assert 'self.title(f"MOEX Research Robot {DISPLAY_VERSION}")' in source
    assert "KILL SWITCH: ON — НОВЫЕ ВХОДЫ ЗАБЛОКИРОВАНЫ" in banner_source
    assert "Копировать полный ID" in source
    assert "Настройка runtime" in source
    assert "bootstrap_runtime(RUNTIME_DIR" in source
    assert "probe_secret_provider" in source
    assert "probe.to_dict()" in source
    assert 'snapshot.get("compatibility_shadow_status")' in source


def test_backup_verification_gui_is_structured_not_raw_json():
    root = Path(__file__).resolve().parents[1]
    source = (root / "desktop_gui.py").read_text(encoding="utf-8")
    assert "BACKUP ДЕЙСТВИТЕЛЕН — восстановление разрешено" in source
    assert "Копировать отчёт" in source
    assert "format_backup_verification_summary" in source
    assert "json.dumps(result.to_dict()" not in source
