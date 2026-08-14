from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_robot.risk import RiskEngine, RiskEvent, RiskPolicy, RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore

ENGAGE_GLOBAL_CONFIRMATION = "ENGAGE GLOBAL RISK HALT"
CLEAR_GLOBAL_CONFIRMATION = "CLEAR RISK HALT"

READ_ONLY_ACTIONS = {"inspect", "explain", "review-policy"}
MUTATION_ACTIONS = {
    "engage-global",
    "clear-global",
    "engage-instrument",
    "clear-instrument",
}


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument(
        "--mode",
        choices=("DRY_RUN", "SANDBOX_EXECUTION"),
        default="SANDBOX_EXECUTION",
    )
    parser.add_argument("--output", type=Path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect v3.9 Portfolio Risk state or apply an explicitly confirmed "
            "kill-switch transition. This tool never creates or dispatches orders."
        )
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    for action in sorted(READ_ONLY_ACTIONS):
        command = subparsers.add_parser(action)
        _add_common_arguments(command)

    engage_global = subparsers.add_parser("engage-global")
    _add_common_arguments(engage_global)
    engage_global.add_argument("--reason", required=True)
    engage_global.add_argument("--operator-ref")
    engage_global.add_argument("--confirm", required=True)

    clear_global = subparsers.add_parser("clear-global")
    _add_common_arguments(clear_global)
    clear_global.add_argument("--confirm", required=True)

    for action in ("engage-instrument", "clear-instrument"):
        command = subparsers.add_parser(action)
        _add_common_arguments(command)
        command.add_argument("--instrument-id", required=True)
        command.add_argument("--confirm", required=True)
        if action == "engage-instrument":
            command.add_argument("--reason", required=True)
            command.add_argument("--operator-ref")

    return parser.parse_args(argv)


def _account_reference(account_id: str) -> dict[str, str]:
    normalized = str(account_id or "").strip()
    if not normalized:
        raise ValueError("account_id must not be empty.")
    return {
        "masked": f"<REDACTED_ACCOUNT:{normalized[-4:]}>",
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def _state_summary(state: RiskState) -> dict[str, Any]:
    return {
        "version": state.version,
        "kill_switch_active": state.kill_switch_active,
        "kill_switch_reason": state.kill_switch_reason,
        "kill_switch_set_at": state.kill_switch_set_at,
        "kill_switch_source": state.kill_switch_source,
        "kill_switch_operator_ref": state.kill_switch_operator_ref,
        "instrument_kill_switches": [
            item.to_dict() for item in state.instrument_kill_switches
        ],
        "risk_resync_required": state.risk_resync_required,
        "risk_resync_reason": state.risk_resync_reason,
        "risk_resync_set_at": state.risk_resync_set_at,
        "risk_resync_source": state.risk_resync_source,
        "risk_resync_cash_before_rub": state.risk_resync_cash_before_rub,
        "risk_resync_cash_observed_rub": state.risk_resync_cash_observed_rub,
        "risk_resync_equity_observed_rub": (
            state.risk_resync_equity_observed_rub
        ),
        "risk_resync_snapshot_at": state.risk_resync_snapshot_at,
        "daily_turnover_rub": state.daily_turnover_rub,
        "daily_order_count": state.daily_order_count,
        "last_equity_rub": state.last_equity_rub,
        "last_cash_rub": state.last_cash_rub,
        "last_snapshot_at": state.last_snapshot_at,
        "last_evaluated_at": state.last_evaluated_at,
        "last_portfolio_risk_decision_id": (
            state.last_portfolio_risk_decision_id
        ),
        "last_portfolio_risk_input_hash": state.last_portfolio_risk_input_hash,
        "last_portfolio_risk_evaluated_at": (
            state.last_portfolio_risk_evaluated_at
        ),
    }


def _load_context(args: argparse.Namespace) -> tuple[dict[str, Any] | None, RiskState]:
    root = args.runtime_dir.resolve()
    profile = RiskProfileStore(root / "risk_profiles.json").load_profile(args.mode)
    state = RiskStateStore(root / "risk_state.json").load_account(args.account_id)
    return profile, state


def _reason_codes(
    profile: dict[str, Any] | None,
    state: RiskState,
    account_id: str,
    mode: str,
) -> list[str]:
    reasons: list[str] = []
    if profile is None:
        reasons.append("POLICY_MISSING")
    else:
        policy = profile["policy"]
        if profile["portfolio_policy_status"] != "READY":
            reasons.append("PORTFOLIO_POLICY_NOT_READY")
        if mode == "SANDBOX_EXECUTION" and policy.portfolio_policy_mode != "ENFORCED":
            reasons.append("PORTFOLIO_POLICY_NOT_ENFORCED")
        if not policy.enabled:
            reasons.append("POLICY_DISABLED")
        account_scope = str(profile.get("account_scope") or "").strip()
        if mode == "SANDBOX_EXECUTION" and not account_scope:
            reasons.append("ACCOUNT_SCOPE_MISSING")
        elif account_scope and account_scope != str(account_id).strip():
            reasons.append("ACCOUNT_SCOPE_MISMATCH")
    if state.kill_switch_active:
        reasons.append("GLOBAL_KILL_SWITCH_ACTIVE")
    if state.instrument_kill_switches:
        reasons.append("INSTRUMENT_KILL_SWITCH_ACTIVE")
    if state.risk_resync_required:
        reasons.append("RISK_RESYNC_REQUIRED")
    return reasons


def _build_report(
    args: argparse.Namespace,
    profile: dict[str, Any] | None,
    state: RiskState,
) -> dict[str, Any]:
    reasons = _reason_codes(profile, state, args.account_id, args.mode)
    policy = profile["policy"] if profile is not None else None
    account_scope = (
        str(profile.get("account_scope") or "").strip() if profile else ""
    )
    account_scope_matches = account_scope == str(args.account_id).strip()
    if args.mode == "DRY_RUN" and not account_scope:
        account_scope_matches = True
    increase_admission = "READY"
    if reasons == ["INSTRUMENT_KILL_SWITCH_ACTIVE"]:
        increase_admission = "CONDITIONAL"
    elif reasons:
        increase_admission = "BLOCKED"
    return {
        "version": "v3.9-beta1-m5.1",
        "action": args.action,
        "account": _account_reference(args.account_id),
        "mode": args.mode,
        "policy": {
            "present": profile is not None,
            "status": (
                profile["portfolio_policy_status"] if profile is not None else "MISSING"
            ),
            "hash": profile["policy_hash"] if profile is not None else None,
            "source": profile.get("source") if profile is not None else None,
            "updated_at": profile.get("updated_at") if profile is not None else None,
            "account_scope_matches": account_scope_matches,
            "enabled": policy.enabled if policy is not None else None,
            "portfolio_policy_mode": (
                policy.portfolio_policy_mode if policy is not None else None
            ),
        },
        "state": _state_summary(state),
        "increase_admission": increase_admission,
        "blocked_instrument_ids": [
            item.instrument_id for item in state.instrument_kill_switches
        ],
        "reason_codes": reasons,
        "risk_reducing_orders_allowed": bool(
            policy is not None
            and policy.allow_risk_reducing_orders_during_halt
            and profile["portfolio_policy_status"] == "READY"
            and not any(
                code
                in {
                    "PORTFOLIO_POLICY_NOT_ENFORCED",
                    "POLICY_DISABLED",
                    "ACCOUNT_SCOPE_MISSING",
                    "ACCOUNT_SCOPE_MISMATCH",
                }
                for code in reasons
            )
        ),
        "writes_performed": False,
        "strategy_proposal_created": False,
        "central_intent_created": False,
        "dispatch_authorized": False,
        "provider_post_authorized": False,
    }


def _base_report(args: argparse.Namespace) -> dict[str, Any]:
    profile, state = _load_context(args)
    return _build_report(args, profile, state)


def _explanations(report: dict[str, Any]) -> list[str]:
    messages = {
        "POLICY_MISSING": "Risk policy is missing; new exposure remains fail-closed.",
        "PORTFOLIO_POLICY_NOT_READY": (
            "Portfolio Risk policy has not passed explicit configuration."
        ),
        "PORTFOLIO_POLICY_NOT_ENFORCED": (
            "Sandbox Portfolio Risk policy remains OBSERVE_ONLY."
        ),
        "POLICY_DISABLED": "Risk policy is disabled; new exposure is unavailable.",
        "ACCOUNT_SCOPE_MISSING": "Sandbox Risk policy has no account scope.",
        "ACCOUNT_SCOPE_MISMATCH": "Risk policy belongs to another account scope.",
        "GLOBAL_KILL_SWITCH_ACTIVE": "Global Risk kill switch blocks new exposure.",
        "INSTRUMENT_KILL_SWITCH_ACTIVE": (
            "One or more instruments have an active Risk kill switch."
        ),
        "RISK_RESYNC_REQUIRED": (
            "External activity requires an explicit Risk baseline resync."
        ),
    }
    reasons = report["reason_codes"]
    if not reasons:
        return ["Policy and persistent Risk state permit admission evaluation."]
    return [messages[code] for code in reasons]


def _review_policy(args: argparse.Namespace) -> dict[str, Any]:
    profile, state = _load_context(args)
    report = _build_report(args, profile, state)
    report["policy_review"] = (
        asdict(profile["policy"]) if profile is not None else None
    )
    return report


def _require_confirmation(actual: str, expected: str) -> None:
    if str(actual or "").strip().upper() != expected.upper():
        raise RuntimeError(f"Confirmation must be exactly '{expected}'.")


def _transition(
    args: argparse.Namespace,
    transition: Callable[[RiskEngine, RiskState], tuple[RiskState, RiskEvent]],
) -> dict[str, Any]:
    root = args.runtime_dir.resolve()
    profile = RiskProfileStore(root / "risk_profiles.json").load_profile(args.mode)
    store = RiskStateStore(root / "risk_state.json")
    event_holder: dict[str, RiskEvent] = {}

    def updater(state: RiskState) -> RiskState:
        updated, event = transition(RiskEngine(RiskPolicy(enabled=False)), state)
        event_holder["event"] = event
        return updated

    updated = store.update_account(args.account_id, updater)
    report = _build_report(args, profile, updated)
    report["event"] = event_holder["event"].to_dict()
    report["writes_performed"] = True
    return report


def _run_mutation(args: argparse.Namespace) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    if args.action == "engage-global":
        _require_confirmation(args.confirm, ENGAGE_GLOBAL_CONFIRMATION)
        return _transition(
            args,
            lambda engine, state: engine.engage_kill_switch(
                state,
                now=now,
                reason=args.reason,
                source="OPERATOR_CLI",
                operator_ref=args.operator_ref,
            ),
        )
    if args.action == "clear-global":
        _require_confirmation(args.confirm, CLEAR_GLOBAL_CONFIRMATION)
        return _transition(
            args,
            lambda engine, state: engine.clear_kill_switch(
                state,
                now=now,
                confirmation=args.confirm,
            ),
        )

    instrument_id = str(args.instrument_id or "").strip().upper()
    if args.action == "engage-instrument":
        expected = f"ENGAGE INSTRUMENT RISK HALT {instrument_id}".upper()
        _require_confirmation(args.confirm, expected)
        return _transition(
            args,
            lambda engine, state: engine.engage_instrument_kill_switch(
                state,
                instrument_id=instrument_id,
                now=now,
                reason=args.reason,
                source="OPERATOR_CLI",
                operator_ref=args.operator_ref,
            ),
        )
    expected = f"CLEAR INSTRUMENT RISK HALT {instrument_id}".upper()
    _require_confirmation(args.confirm, expected)
    return _transition(
        args,
        lambda engine, state: engine.clear_instrument_kill_switch(
            state,
            instrument_id=instrument_id,
            now=now,
            confirmation=args.confirm,
        ),
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.action == "inspect":
        return _base_report(args)
    if args.action == "explain":
        report = _base_report(args)
        report["explanations"] = _explanations(report)
        return report
    if args.action == "review-policy":
        return _review_policy(args)
    if args.action in MUTATION_ACTIONS:
        return _run_mutation(args)
    raise ValueError(f"Unsupported action: {args.action}")


def _error_report(args: argparse.Namespace, error: Exception) -> dict[str, Any]:
    account_id = str(args.account_id or "").strip()
    account = (
        _account_reference(account_id)
        if account_id
        else {"masked": "<REDACTED_ACCOUNT>", "sha256": None}
    )
    message = str(error) or type(error).__name__
    if account_id:
        message = message.replace(account_id, account["masked"])
    return {
        "version": "v3.9-beta1-m5.1",
        "action": args.action,
        "account": account,
        "mode": args.mode,
        "status": "ERROR",
        "error": {
            "type": type(error).__name__,
            "message": message,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        payload = run(args)
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(
            json.dumps(
                _error_report(args, error),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
