from __future__ import annotations

"""Application/runtime path resolution for source and portable Windows builds.

The source distribution keeps the historical single-directory layout unless
``MOEX_ROBOT_RUNTIME_DIR`` is supplied.  The standalone launcher sets that
variable to a sibling ``runtime`` directory so immutable binaries and mutable
state are separated without breaking upgrades from v3.6-beta1.1 to v3.6.0.
"""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import sys
from typing import Any


@dataclass(frozen=True, slots=True)
class AppPaths:
    app_dir: Path
    runtime_dir: Path
    logs_dir: Path
    backups_dir: Path
    reports_dir: Path
    support_dir: Path

    def ensure_directories(self) -> None:
        for directory in (
            self.runtime_dir,
            self.logs_dir,
            self.backups_dir,
            self.reports_dir,
            self.support_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        return {key: str(value) for key, value in asdict(self).items()}


def executable_app_dir(source_file: str | Path | None = None) -> Path:
    """Return the directory that contains the executable application files."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if source_file is not None:
        return Path(source_file).resolve().parent
    return Path.cwd().resolve()


def resolve_app_paths(source_file: str | Path | None = None) -> AppPaths:
    app_dir = executable_app_dir(source_file)
    raw_runtime = os.getenv("MOEX_ROBOT_RUNTIME_DIR", "").strip()
    portable = os.getenv("MOEX_ROBOT_PORTABLE_LAYOUT", "").strip().upper() in {
        "1",
        "YES",
        "TRUE",
        "ON",
    }
    if raw_runtime:
        runtime_dir = Path(os.path.expandvars(raw_runtime)).expanduser()
        if not runtime_dir.is_absolute():
            runtime_dir = app_dir / runtime_dir
        runtime_dir = runtime_dir.resolve()
    elif portable or getattr(sys, "frozen", False):
        runtime_dir = (app_dir.parent / "runtime" if app_dir.name.lower() == "app" else app_dir / "runtime").resolve()
    else:
        # Backward-compatible source/hotfix layout.
        runtime_dir = app_dir

    def _resolve_optional(env_name: str, default: Path) -> Path:
        raw = os.getenv(env_name, "").strip()
        if not raw:
            return default.resolve()
        path = Path(os.path.expandvars(raw)).expanduser()
        if not path.is_absolute():
            path = runtime_dir / path
        return path.resolve()

    return AppPaths(
        app_dir=app_dir,
        runtime_dir=runtime_dir,
        logs_dir=_resolve_optional("MOEX_ROBOT_LOGS_DIR", runtime_dir / "logs" if runtime_dir != app_dir else runtime_dir),
        backups_dir=_resolve_optional("MOEX_ROBOT_BACKUPS_DIR", runtime_dir / "backups"),
        reports_dir=_resolve_optional("MOEX_ROBOT_REPORTS_DIR", runtime_dir / "reports"),
        support_dir=_resolve_optional("MOEX_ROBOT_SUPPORT_DIR", runtime_dir / "support"),
    )
