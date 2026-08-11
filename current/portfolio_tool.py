from __future__ import annotations

"""Read-only CLI for the persisted v3.7 canonical portfolio snapshot."""

import argparse
import json
from pathlib import Path
import sys

from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_snapshot import PortfolioSnapshotBuilder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect/export portfolio_state.json")
    parser.add_argument(
        "--state",
        default="portfolio_state.json",
        help="Path to canonical portfolio_state.json.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("inspect", help="Print repository integrity/account metadata.")
    subparsers.add_parser("show", help="Print canonical snapshot JSON.")

    export = subparsers.add_parser("export", help="Export snapshot to JSON or CSV.")
    export.add_argument("--format", choices=("json", "csv"), required=True)
    export.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = PortfolioRepository(Path(args.state))
    try:
        if args.command == "inspect":
            print(json.dumps(repository.status().to_dict(), ensure_ascii=False, indent=2))
            return 0 if repository.status().valid else 1
        state = repository.load()
        builder = PortfolioSnapshotBuilder(state)
        if args.command == "show":
            print(json.dumps(builder.to_dict(), ensure_ascii=False, indent=2))
            return 0
        target = Path(args.output)
        if args.format == "json":
            builder.export_json(target)
        else:
            builder.export_csv(target)
        print(target.resolve())
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
