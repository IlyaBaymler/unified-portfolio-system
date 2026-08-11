from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.risk import RiskEngine, RiskPolicy, RiskState
from trading_robot.risk_persistence import (
    RiskPersistenceError,
    RiskProfileStore,
    RiskStateStore,
)


def _account_id(raw: str | None) -> str:
    value = str(raw or os.getenv("TBANK_SANDBOX_ACCOUNT_ID", "")).strip()
    if not value:
        raise ValueError(
            "Sandbox account id is required. Set TBANK_SANDBOX_ACCOUNT_ID "
            "in .env or pass --account-id."
        )
    return value


def _policy_hash(path: Path) -> str | None:
    try:
        loaded = RiskProfileStore(path).load_profile("SANDBOX_EXECUTION")
    except (RiskPersistenceError, OSError, ValueError):
        return None
    if loaded is None:
        return None
    return str(loaded.get("policy_hash") or "").strip() or None


def _journal_event_type(core_event_type: str) -> str:
    return {
        "KILL_SWITCH_ENABLED": "KILL_SWITCH_ENGAGED",
        "KILL_SWITCH_DISABLED": "KILL_SWITCH_CLEARED",
    }.get(core_event_type, core_event_type)


def _record_control_event(
    *,
    journal_path: Path,
    account_id: str,
    command: str,
    actor: str,
    now: datetime,
    previous: RiskState,
    updated: RiskState,
    core_event_type: str,
    details: dict,
    policy_hash: str | None,
    reason: str | None,
    confirmation: str,
) -> int:
    event_type = _journal_event_type(core_event_type)
    expected_confirmation = {
        "clear": "CLEAR RISK HALT",
        "reset-baselines": "RESET RISK BASELINES",
    }.get(command)
    confirmation_validated = (
        True
        if expected_confirmation is None
        else confirmation.strip().upper() == expected_confirmation
    )
    payload = {
        **details,
        "actor": actor,
        "source": "risk_control_tool",
        "command": command,
        "reason": reason,
        "previous_state": {
            "kill_switch_active": previous.kill_switch_active,
            "kill_switch_reason": previous.kill_switch_reason,
            "kill_switch_set_at": previous.kill_switch_set_at,
            "risk_resync_required": previous.risk_resync_required,
            "risk_resync_reason": previous.risk_resync_reason,
            "risk_resync_set_at": previous.risk_resync_set_at,
            "daily_date": previous.daily_date,
            "weekly_key": previous.weekly_key,
            "daily_start_equity_rub": previous.daily_start_equity_rub,
            "weekly_start_equity_rub": previous.weekly_start_equity_rub,
            "high_watermark_equity_rub": previous.high_watermark_equity_rub,
            "daily_turnover_rub": previous.daily_turnover_rub,
            "daily_order_count": previous.daily_order_count,
        },
        "new_state": {
            "kill_switch_active": updated.kill_switch_active,
            "kill_switch_reason": updated.kill_switch_reason,
            "kill_switch_set_at": updated.kill_switch_set_at,
            "risk_resync_required": updated.risk_resync_required,
            "risk_resync_reason": updated.risk_resync_reason,
            "risk_resync_set_at": updated.risk_resync_set_at,
            "daily_date": updated.daily_date,
            "weekly_key": updated.weekly_key,
            "daily_start_equity_rub": updated.daily_start_equity_rub,
            "weekly_start_equity_rub": updated.weekly_start_equity_rub,
            "high_watermark_equity_rub": updated.high_watermark_equity_rub,
            "daily_turnover_rub": updated.daily_turnover_rub,
            "daily_order_count": updated.daily_order_count,
        },
        "policy_hash": policy_hash,
        "confirmation_validated": confirmation_validated,
        "changed_at": now.astimezone(timezone.utc).isoformat(),
    }
    return EventJournal(journal_path).record(
        JournalEvent(
            category="risk_control",
            event_type=event_type,
            severity=(
                "WARNING"
                if event_type in {
                    "KILL_SWITCH_ENGAGED",
                    "RISK_BASELINES_RESET",
                    "RISK_RESYNC_COMPLETED",
                }
                else "INFO"
            ),
            account_id=account_id,
            mode="ACCOUNT",
            status="HALTED" if updated.kill_switch_active else "ACTIVE",
            action=command.upper(),
            config_hash=policy_hash,
            payload=payload,
            timestamp_utc=now.astimezone(timezone.utc).isoformat(),
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect and control persistent Risk Engine state."
    )
    parser.add_argument(
        "command",
        choices=("show", "engage", "clear", "reset-baselines"),
    )
    parser.add_argument("--file", default="risk_state.json")
    parser.add_argument("--profile-file", default="risk_profiles.json")
    parser.add_argument("--journal-file", default="trading_events.db")
    parser.add_argument("--account-id")
    parser.add_argument("--reason", default="Manual operator halt")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--equity-rub", type=float)
    parser.add_argument(
        "--actor",
        default=(getpass.getuser() or "operator"),
        help="Operator identifier written to the local audit journal.",
    )
    args = parser.parse_args(argv)

    app_dir = Path(__file__).resolve().parent
    load_dotenv(app_dir / ".env")
    account_id = _account_id(args.account_id)
    state_path = Path(args.file)
    profile_path = Path(args.profile_file)
    journal_path = Path(args.journal_file)
    if not state_path.is_absolute():
        state_path = app_dir / state_path
    if not profile_path.is_absolute():
        profile_path = app_dir / profile_path
    if not journal_path.is_absolute():
        journal_path = app_dir / journal_path

    store = RiskStateStore(state_path)
    engine = RiskEngine(RiskPolicy())
    now = datetime.now(timezone.utc)

    if args.command == "show":
        state = store.load_account(account_id)
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        return 0

    previous = store.load_account(account_id)
    event_holder: dict[str, object] = {}

    if args.command == "engage":

        def updater(state):
            updated, event = engine.engage_kill_switch(
                state,
                now=now,
                reason=args.reason,
            )
            event_holder["events"] = [event]
            return updated

    elif args.command == "clear":

        def updater(state):
            updated, event = engine.clear_kill_switch(
                state,
                now=now,
                confirmation=args.confirmation,
            )
            event_holder["events"] = [event]
            return updated

    else:

        def updater(state):
            updated, events = engine.reset_baselines(
                state,
                now=now,
                equity_rub=args.equity_rub,
                confirmation=args.confirmation,
            )
            event_holder["events"] = list(events)
            return updated

    updated = store.update_account(account_id, updater)
    events = list(event_holder.get("events") or [])
    policy_hash = _policy_hash(profile_path)
    journal_ids: list[int] = []
    for event in events:
        core_event_type = str(getattr(event, "event_type"))
        event_details = dict(getattr(event, "details"))
        journal_ids.append(
            _record_control_event(
                journal_path=journal_path,
                account_id=account_id,
                command=args.command,
                actor=str(args.actor).strip() or "operator",
                now=now,
                previous=previous,
                updated=updated,
                core_event_type=core_event_type,
                details=event_details,
                policy_hash=policy_hash,
                reason=(args.reason if args.command == "engage" else None),
                confirmation=args.confirmation,
            )
        )
        print(_journal_event_type(core_event_type))
        print(json.dumps(event_details, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                "kill_switch_active": updated.kill_switch_active,
                "daily_date": updated.daily_date,
                "daily_turnover_rub": updated.daily_turnover_rub,
                "daily_order_count": updated.daily_order_count,
                "risk_resync_required": updated.risk_resync_required,
                "journal_event_ids": journal_ids,
                "journal_file": str(journal_path),
                "policy_hash": policy_hash,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
