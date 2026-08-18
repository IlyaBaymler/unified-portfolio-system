from __future__ import annotations

"""CLI for PortfolioState schema-2 preview and canonical cutover."""

import argparse
import json
from pathlib import Path

from trading_robot.journal import EventJournal
from trading_robot.portfolio_cutover import PortfolioCutoverManager
from trading_robot.portfolio_repository import PortfolioRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="v3.9.0 canonical cutover tool")
    parser.add_argument("command", choices=("preview", "cutover"))
    parser.add_argument("--runtime-dir", default=".")
    parser.add_argument("--account-id", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--execution-active", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(args.runtime_dir).resolve()
    manager = PortfolioCutoverManager(
        PortfolioRepository(root / "portfolio_state.json"),
        journal=EventJournal(root / "trading_events.db"),
        report_path=root / "canonical_migration_report.json",
    )
    if args.command == "preview":
        payload = manager.preview(
            expected_account_id=args.account_id or None,
            execution_active=args.execution_active,
        ).to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["allowed"] else 2
    result = manager.execute(
        confirmation=args.confirmation,
        expected_account_id=args.account_id or None,
        execution_active=args.execution_active,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.success else 2


if __name__ == "__main__":
    raise SystemExit(main())
