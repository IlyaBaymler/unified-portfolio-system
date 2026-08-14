from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_robot.journal import EventJournal
from trading_robot.portfolio_risk_shadow import build_portfolio_risk_shadow_report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect v3.9 Portfolio Risk shadow coverage and drift."
    )
    parser.add_argument("runtime_directory", type=Path)
    parser.add_argument("--account-id")
    parser.add_argument("--limit", type=int, default=100_000)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any]:
    return build_portfolio_risk_shadow_report(
        EventJournal(
            args.runtime_directory / "trading_events.db",
            read_only=True,
        ),
        account_id=(str(args.account_id).strip() if args.account_id else None),
        limit=args.limit,
    )


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
