from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path

from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore, normalize_risk_mode


def policy_for_preset(name: str) -> RiskPolicy:
    normalized = name.strip().lower()
    if normalized in {"default", "sandbox-beta1", "sandbox-alpha3", "sandbox-alpha3.1"}:
        return RiskPolicy()
    if normalized == "max-one":
        return replace(RiskPolicy(), max_position_lots=1)
    if normalized == "block-new":
        return replace(RiskPolicy(), max_position_lots=0)
    if normalized == "permissive":
        return RiskPolicy(
            max_position_lots=100,
            max_position_value_rub=1_000_000.0,
            max_position_share_of_equity=1.0,
            max_order_value_rub=1_000_000.0,
            cash_reserve_rub=0.0,
            commission_buffer_fraction=0.0,
            risk_per_trade_rub=None,
            risk_per_trade_fraction=None,
            daily_loss_limit_rub=None,
            daily_loss_limit_fraction=None,
            weekly_loss_limit_rub=None,
            weekly_loss_limit_fraction=None,
            max_drawdown_fraction=None,
            max_daily_turnover_rub=None,
            max_orders_per_day=None,
            max_snapshot_age_seconds=300,
        )
    raise ValueError(f"Unknown preset: {name}")


def confirm_portfolio_shadow(
    store: RiskProfileStore,
    *,
    mode: str,
    account_id: str,
    confirmation: str,
) -> dict:
    normalized_mode = normalize_risk_mode(mode)
    if normalized_mode != "SANDBOX_EXECUTION":
        raise ValueError(
            "Portfolio shadow confirmation is restricted to SANDBOX_EXECUTION."
        )
    normalized_account = str(account_id or "").strip()
    if not normalized_account:
        raise ValueError("--account-id is required for Portfolio shadow.")
    loaded = store.require_profile(normalized_mode)
    existing_scope = str(loaded.get("account_scope") or "").strip()
    if existing_scope and existing_scope != normalized_account:
        raise ValueError(
            "Risk profile account scope does not match --account-id."
        )
    policy = replace(
        loaded["policy"],
        portfolio_policy_configured=True,
        portfolio_policy_mode="OBSERVE_ONLY",
    )
    return store.confirm_portfolio_policy(
        normalized_mode,
        policy,
        confirmation=confirmation,
        select=True,
        account_scope=normalized_account,
        source="V3_9_M3_OPERATOR",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create/show checksummed Risk Engine test profiles."
    )
    parser.add_argument(
        "command",
        choices=("show", "preset", "reset", "confirm-portfolio-shadow"),
    )
    parser.add_argument(
        "value",
        nargs="?",
        help=(
            "Preset: default, sandbox-beta1, max-one, block-new, permissive "
            "block-new, permissive."
        ),
    )
    parser.add_argument("--file", default="risk_profiles.json")
    parser.add_argument("--mode", default="DRY_RUN")
    parser.add_argument("--account-id")
    parser.add_argument("--confirmation")
    args = parser.parse_args(argv)

    store = RiskProfileStore(Path(args.file))
    if args.command == "show":
        loaded = store.load_profile(args.mode)
        if loaded is None:
            print(f"Profile {args.mode} is not saved.")
            return 1
        print(
            json.dumps(
                {
                    "mode": loaded["mode"],
                    "policy_hash": loaded["policy_hash"],
                    "updated_at": loaded["updated_at"],
                    "account_scope": loaded.get("account_scope"),
                    "source": loaded.get("source"),
                    "portfolio_policy_status": loaded[
                        "portfolio_policy_status"
                    ],
                    "policy": asdict(loaded["policy"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "confirm-portfolio-shadow":
        saved = confirm_portfolio_shadow(
            store,
            mode=args.mode,
            account_id=args.account_id,
            confirmation=args.confirmation,
        )
        print(
            json.dumps(
                {
                    "mode": saved["mode"],
                    "policy_hash": saved["policy_hash"],
                    "account_scope": saved["account_scope"],
                    "source": saved["source"],
                    "portfolio_policy_status": saved[
                        "portfolio_policy_status"
                    ],
                    "portfolio_policy_mode": saved[
                        "policy"
                    ].portfolio_policy_mode,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "reset":
        removed = store.reset_profile(args.mode)
        print(f"Profile removed: {removed}")
        return 0

    if not args.value:
        parser.error("preset requires a value")
    policy = policy_for_preset(args.value)
    saved = store.save_profile(args.mode, policy, select=True)
    print(f"Saved {saved['mode']} preset={args.value}")
    print(f"Policy hash: {saved['policy_hash']}")
    print(json.dumps(asdict(policy), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
