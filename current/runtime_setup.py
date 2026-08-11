from __future__ import annotations

import argparse
from pathlib import Path

from trading_robot.runtime_bootstrap import bootstrap_runtime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create and validate all canonical MOEX Research Robot runtime "
            "files without overwriting user data."
        )
    )
    parser.add_argument("--app-dir", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)

    report = bootstrap_runtime(args.app_dir, write_report=not args.no_report)
    print(report.format_text())
    return 1 if report.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
