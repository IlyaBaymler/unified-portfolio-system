from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_robot.portfolio_risk_recovery import ExternalCashResyncService


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare or apply a v3.9 external RUB cash baseline resync. "
            "This tool cannot create, dispatch or resubmit an order."
        )
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("prepare", "apply"):
        command = subparsers.add_parser(action)
        command.add_argument("--runtime-dir", type=Path, required=True)
        command.add_argument("--account-id", required=True)
        command.add_argument("--output", type=Path)
        if action == "apply":
            command.add_argument("--proof-sha256", required=True)
            command.add_argument("--confirm", required=True)
    return parser.parse_args(argv)


def _absolute_without_symlink_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_within(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _validate_output_boundary(args: argparse.Namespace) -> None:
    output = getattr(args, "output", None)
    if output is None:
        return
    runtime_dir = Path(args.runtime_dir)
    output_path = Path(output)
    lexical_runtime = _absolute_without_symlink_resolution(runtime_dir)
    lexical_output = _absolute_without_symlink_resolution(output_path)
    resolved_runtime = runtime_dir.resolve(strict=False)
    resolved_output = output_path.resolve(strict=False)
    if _is_within(lexical_output, lexical_runtime) or _is_within(
        resolved_output,
        resolved_runtime,
    ):
        raise ValueError(
            "--output must be outside --runtime-dir; runtime state paths are protected."
        )


def _account_reference(account_id: str) -> dict[str, str]:
    normalized = str(account_id or "").strip()
    if not normalized:
        raise ValueError("account_id must not be empty.")
    return {
        "masked": f"<REDACTED_ACCOUNT:{normalized[-4:]}>",
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def _safety_fields(*, writes_performed: bool) -> dict[str, bool]:
    return {
        "writes_performed": writes_performed,
        "strategy_proposal_created": False,
        "central_intent_created": False,
        "dispatch_authorized": False,
        "resubmit_authorized": False,
        "provider_post_authorized": False,
    }


def run(
    args: argparse.Namespace,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    service = ExternalCashResyncService.from_directory(
        args.runtime_dir.resolve(),
        account_id=args.account_id,
    )
    account = _account_reference(args.account_id)
    if args.action == "prepare":
        proof = service.prepare(now=now)
        return {
            "version": "v3.9-beta1-m5.2",
            "action": "prepare",
            "status": "PREPARED",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "account": account,
            "proof": proof.to_dict(),
            "required_confirmation": proof.confirmation,
            **_safety_fields(writes_performed=False),
        }

    result = service.apply(
        expected_proof_sha256=args.proof_sha256,
        confirmation=args.confirm,
        now=now,
    )
    return {
        "version": "v3.9-beta1-m5.2",
        "action": "apply",
        "status": "APPLIED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "account": account,
        "proof": result.proof.to_dict(),
        "risk_state": {
            "risk_resync_required": result.state.risk_resync_required,
            "daily_start_equity_rub": result.state.daily_start_equity_rub,
            "weekly_start_equity_rub": result.state.weekly_start_equity_rub,
            "high_watermark_equity_rub": result.state.high_watermark_equity_rub,
            "last_equity_rub": result.state.last_equity_rub,
            "last_cash_rub": result.state.last_cash_rub,
            "last_snapshot_at": result.state.last_snapshot_at,
            "daily_turnover_rub": result.state.daily_turnover_rub,
            "daily_order_count": result.state.daily_order_count,
            "recorded_execution_count": len(result.state.recorded_execution_ids),
        },
        "event": result.event.to_dict(),
        **_safety_fields(writes_performed=True),
    }


def _error_report(args: argparse.Namespace, error: Exception) -> dict[str, Any]:
    account_id = str(getattr(args, "account_id", "") or "").strip()
    account = (
        _account_reference(account_id)
        if account_id
        else {"masked": "<REDACTED_ACCOUNT>", "sha256": None}
    )
    message = str(error) or type(error).__name__
    if account_id:
        message = message.replace(account_id, account["masked"])
    return {
        "version": "v3.9-beta1-m5.2",
        "action": getattr(args, "action", None),
        "status": "ERROR",
        "account": account,
        "error": {
            "type": type(error).__name__,
            "message": message,
        },
        **_safety_fields(writes_performed=False),
    }


def _output_error_report(
    args: argparse.Namespace,
    error: Exception,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    account_id = str(getattr(args, "account_id", "") or "").strip()
    account = payload.get("account") or (
        _account_reference(account_id)
        if account_id
        else {"masked": "<REDACTED_ACCOUNT>", "sha256": None}
    )
    message = str(error) or type(error).__name__
    if account_id:
        message = message.replace(account_id, account["masked"])
    writes_performed = bool(payload.get("writes_performed", False))
    return {
        "version": payload.get("version", "v3.9-beta1-m5.2"),
        "action": payload.get("action", getattr(args, "action", None)),
        "status": "APPLIED_OUTPUT_ERROR" if writes_performed else "OUTPUT_ERROR",
        "operation_status": payload.get("status"),
        "account": account,
        "error": {
            "type": type(error).__name__,
            "message": message,
        },
        **_safety_fields(writes_performed=writes_performed),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _validate_output_boundary(args)
        payload = run(args)
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

    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            args.output.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        print(
            json.dumps(
                _output_error_report(args, error, payload=payload),
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
