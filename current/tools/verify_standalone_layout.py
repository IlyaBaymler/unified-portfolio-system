from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REQUIRED_DIRS = ("app", "runtime", "backups", "reports", "logs", "support")
FORBIDDEN_RUNTIME_NAMES = {
    ".env",
    "robot_state.json",
    "portfolio_state.json",
    "risk_state.json",
    "risk_profiles.json",
    "strategy_profiles.json",
    "sandbox_diagnostic_state.json",
    "trading_events.db",
    "robot_gui.log",
    "robot_debug.log",
}


def verify_layout(root: str | Path) -> list[str]:
    base = Path(root).resolve()
    errors: list[str] = []
    if not base.is_dir():
        return [f"Portable root does not exist: {base}"]
    for name in REQUIRED_DIRS:
        if not (base / name).is_dir():
            errors.append(f"Missing directory: {name}")
    if not (base / "MOEX Research Robot.bat").is_file():
        errors.append("Missing portable launcher: MOEX Research Robot.bat")
    if not (base / "app" / "MOEXResearchRobot.exe").is_file():
        errors.append("Missing executable: app/MOEXResearchRobot.exe")
    manifest_path = base / "app" / "build_manifest.json"
    if not manifest_path.is_file():
        errors.append("Missing app/build_manifest.json")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"Invalid build manifest: {exc}")
        else:
            if manifest.get("software_version") != "0.3.7a3":
                errors.append("Build manifest version is not 0.3.7a3")
    for name in FORBIDDEN_RUNTIME_NAMES:
        if (base / name).exists() or (base / "app" / name).exists():
            errors.append(f"Private runtime file leaked into package: {name}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify v3.7-alpha3 portable layout.")
    parser.add_argument("--root", required=True)
    args = parser.parse_args(argv)
    errors = verify_layout(args.root)
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return 1
    print("Standalone layout: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
