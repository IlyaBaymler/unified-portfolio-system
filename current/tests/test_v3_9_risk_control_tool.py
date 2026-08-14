from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import v3_9_risk_control as control
from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore

ACCOUNT = "sandbox-account-12345678"


def ready_runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir()
    RiskProfileStore(root / "risk_profiles.json").confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        RiskPolicy(
            enabled=True,
            portfolio_policy_configured=True,
            portfolio_policy_mode="ENFORCED",
            max_gross_exposure_rub=100_000.0,
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
        source="TEST",
    )
    return root


def args(root: Path, action: str, *extra: str):
    return control.parse_args(
        [
            action,
            "--runtime-dir",
            str(root),
            "--account-id",
            ACCOUNT,
            *extra,
        ]
    )


def test_inspect_explain_and_policy_review_are_read_only(tmp_path: Path) -> None:
    root = ready_runtime(tmp_path)
    profile_path = root / "risk_profiles.json"
    profile_before = profile_path.read_bytes()

    inspected = control.run(args(root, "inspect"))
    explained = control.run(args(root, "explain"))
    reviewed = control.run(args(root, "review-policy"))

    assert inspected["increase_admission"] == "READY"
    assert inspected["reason_codes"] == []
    assert explained["explanations"] == [
        "Policy and persistent Risk state permit admission evaluation."
    ]
    assert reviewed["policy_review"]["portfolio_policy_mode"] == "ENFORCED"
    assert reviewed["policy_review"]["max_gross_exposure_rub"] == 100_000.0
    assert profile_path.read_bytes() == profile_before
    assert not (root / "risk_state.json").exists()
    assert all(
        result["writes_performed"] is False
        and result["central_intent_created"] is False
        and result["dispatch_authorized"] is False
        and result["provider_post_authorized"] is False
        for result in (inspected, explained, reviewed)
    )
    assert ACCOUNT not in json.dumps(
        [inspected, explained, reviewed], ensure_ascii=False
    )


def test_missing_policy_explains_fail_closed_state_without_writes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()

    result = control.run(args(root, "explain"))

    assert result["increase_admission"] == "BLOCKED"
    assert result["reason_codes"] == ["POLICY_MISSING"]
    assert "fail-closed" in result["explanations"][0]
    assert list(root.iterdir()) == []


def test_sandbox_observe_only_or_missing_account_scope_is_not_admission_ready(
    tmp_path: Path,
) -> None:
    observe_root = tmp_path / "observe-runtime"
    observe_root.mkdir()
    RiskProfileStore(observe_root / "risk_profiles.json").confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        RiskPolicy(
            portfolio_policy_configured=True,
            portfolio_policy_mode="OBSERVE_ONLY",
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        account_scope=ACCOUNT,
    )

    observed = control.run(args(observe_root, "explain"))

    assert observed["increase_admission"] == "BLOCKED"
    assert observed["reason_codes"] == ["PORTFOLIO_POLICY_NOT_ENFORCED"]
    assert observed["risk_reducing_orders_allowed"] is False

    missing_scope_root = tmp_path / "missing-scope-runtime"
    missing_scope_root.mkdir()
    RiskProfileStore(
        missing_scope_root / "risk_profiles.json"
    ).confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        RiskPolicy(
            portfolio_policy_configured=True,
            portfolio_policy_mode="ENFORCED",
        ),
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
    )

    missing_scope = control.run(args(missing_scope_root, "inspect"))

    assert missing_scope["increase_admission"] == "BLOCKED"
    assert missing_scope["reason_codes"] == ["ACCOUNT_SCOPE_MISSING"]
    assert missing_scope["policy"]["account_scope_matches"] is False


def test_global_kill_switch_requires_exact_confirmation_and_is_persistent(
    tmp_path: Path,
) -> None:
    root = ready_runtime(tmp_path)

    with pytest.raises(RuntimeError, match="Confirmation must be exactly"):
        control.run(
            args(
                root,
                "engage-global",
                "--reason",
                "operator test",
                "--confirm",
                "YES",
            )
        )
    assert not (root / "risk_state.json").exists()

    engaged = control.run(
        args(
            root,
            "engage-global",
            "--reason",
            "operator test",
            "--operator-ref",
            "M5.1-TEST",
            "--confirm",
            control.ENGAGE_GLOBAL_CONFIRMATION,
        )
    )
    persisted = RiskStateStore(root / "risk_state.json").load_account(ACCOUNT)

    assert engaged["event"]["event_type"] == "KILL_SWITCH_ENABLED"
    assert engaged["writes_performed"] is True
    assert engaged["central_intent_created"] is False
    assert engaged["dispatch_authorized"] is False
    assert engaged["provider_post_authorized"] is False
    assert engaged["increase_admission"] == "BLOCKED"
    assert persisted.kill_switch_active is True
    assert persisted.kill_switch_source == "OPERATOR_CLI"

    with pytest.raises(RuntimeError, match="Confirmation must be exactly"):
        control.run(args(root, "clear-global", "--confirm", "CLEAR"))
    assert RiskStateStore(root / "risk_state.json").load_account(
        ACCOUNT
    ).kill_switch_active

    cleared = control.run(
        args(
            root,
            "clear-global",
            "--confirm",
            control.CLEAR_GLOBAL_CONFIRMATION,
        )
    )
    assert cleared["event"]["event_type"] == "KILL_SWITCH_DISABLED"
    assert cleared["increase_admission"] == "READY"
    assert not RiskStateStore(root / "risk_state.json").load_account(
        ACCOUNT
    ).kill_switch_active


def test_instrument_kill_switch_uses_instrument_bound_confirmation(
    tmp_path: Path,
) -> None:
    root = ready_runtime(tmp_path)
    engage_confirmation = "ENGAGE INSTRUMENT RISK HALT SBER"
    clear_confirmation = "CLEAR INSTRUMENT RISK HALT SBER"

    with pytest.raises(RuntimeError, match="Confirmation must be exactly"):
        control.run(
            args(
                root,
                "engage-instrument",
                "--instrument-id",
                "sber",
                "--reason",
                "instrument review",
                "--confirm",
                "ENGAGE INSTRUMENT RISK HALT LKOH",
            )
        )

    engaged = control.run(
        args(
            root,
            "engage-instrument",
            "--instrument-id",
            "sber",
            "--reason",
            "instrument review",
            "--confirm",
            engage_confirmation,
        )
    )
    assert engaged["event"]["event_type"] == "INSTRUMENT_KILL_SWITCH_ENABLED"
    assert engaged["reason_codes"] == ["INSTRUMENT_KILL_SWITCH_ACTIVE"]
    assert engaged["increase_admission"] == "CONDITIONAL"
    assert engaged["blocked_instrument_ids"] == ["SBER"]
    assert [
        item.instrument_id
        for item in RiskStateStore(root / "risk_state.json")
        .load_account(ACCOUNT)
        .instrument_kill_switches
    ] == ["SBER"]

    cleared = control.run(
        args(
            root,
            "clear-instrument",
            "--instrument-id",
            "sber",
            "--confirm",
            clear_confirmation,
        )
    )
    assert cleared["event"]["event_type"] == "INSTRUMENT_KILL_SWITCH_DISABLED"
    assert cleared["reason_codes"] == []


def test_corrupt_risk_state_fails_closed_without_replacement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = ready_runtime(tmp_path)
    state_path = root / "risk_state.json"
    state_path.write_text("{not-json", encoding="utf-8")
    before = state_path.read_bytes()

    with pytest.raises(Exception, match="must remain blocked"):
        control.run(args(root, "inspect"))
    with pytest.raises(Exception, match="must remain blocked"):
        control.run(
            args(
                root,
                "engage-global",
                "--reason",
                "corrupt-state test",
                "--confirm",
                control.ENGAGE_GLOBAL_CONFIRMATION,
            )
        )

    assert state_path.read_bytes() == before

    account_root = tmp_path / "corrupt-account"
    account_root.mkdir()
    account_state_path = account_root / "risk_state.json"
    account_state_path.write_text(
        json.dumps({"version": 3, "accounts": {ACCOUNT: []}}),
        encoding="utf-8",
    )
    account_state_before = account_state_path.read_bytes()

    exit_code = control.main(
        [
            "inspect",
            "--runtime-dir",
            str(account_root),
            "--account-id",
            ACCOUNT,
        ]
    )
    captured = capsys.readouterr()
    error_report = json.loads(captured.err)

    assert exit_code == 1
    assert captured.out == ""
    assert ACCOUNT not in captured.err
    assert error_report["status"] == "ERROR"
    assert error_report["account"]["masked"] == "<REDACTED_ACCOUNT:5678>"
    assert error_report["error"]["type"] == "RiskPersistenceError"
    assert "Risk state for account <REDACTED_ACCOUNT:5678>" in error_report[
        "error"
    ]["message"]
    assert account_state_path.read_bytes() == account_state_before


def test_corrupt_policy_is_detected_before_kill_switch_mutation(tmp_path: Path) -> None:
    root = ready_runtime(tmp_path)
    profile_path = root / "risk_profiles.json"
    state_path = root / "risk_state.json"
    profile_path.write_text("{not-json", encoding="utf-8")
    before = profile_path.read_bytes()

    with pytest.raises(Exception, match="risk execution must remain blocked"):
        control.run(
            args(
                root,
                "engage-global",
                "--reason",
                "corrupt-policy test",
                "--confirm",
                control.ENGAGE_GLOBAL_CONFIRMATION,
            )
        )

    assert profile_path.read_bytes() == before
    assert not state_path.exists()
