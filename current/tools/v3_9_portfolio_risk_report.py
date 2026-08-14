from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_robot.portfolio_risk_read_service import (
    PortfolioRiskReadService,
    load_portfolio_risk_metadata,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a side-effect-free v3.9 Portfolio Risk report."
    )
    parser.add_argument("runtime_directory", type=Path)
    parser.add_argument("--account-id", required=True)
    parser.add_argument(
        "--mode",
        choices=("DRY_RUN", "SANDBOX_EXECUTION"),
        default="DRY_RUN",
    )
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any]:
    metadata = (
        load_portfolio_risk_metadata(args.metadata) if args.metadata else None
    )
    report = PortfolioRiskReadService(args.runtime_directory).build_report(
        account_id=args.account_id,
        mode=args.mode,
        metadata=metadata,
    )
    return report.to_dict()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = run(args)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
