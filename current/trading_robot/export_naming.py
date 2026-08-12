from __future__ import annotations

"""Version-aware, collision-safe export file names.

The helper is intentionally independent from Tkinter so naming behaviour can be
covered by deterministic unit tests and reused by CLI/standalone builds.
"""

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

_WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_UNDERSCORE = re.compile(r"_+")


def sanitize_component(value: str, *, fallback: str = "export") -> str:
    text = _WINDOWS_INVALID.sub("_", str(value or "").strip())
    text = re.sub(r"\s+", "_", text)
    text = text.replace(".", "_").replace("-", "_")
    text = _MULTI_UNDERSCORE.sub("_", text).strip(" ._")
    return text or fallback


def display_version_from_manifest(
    manifest_path: str | Path,
    *,
    package_version: str,
) -> str:
    path = Path(manifest_path)
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        display = str(payload.get("display_version") or "").strip()
        if display:
            return display
    return f"v{package_version}"


def build_export_filename(
    prefix: str,
    display_version: str,
    extension: str,
    *,
    now: datetime | None = None,
) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    suffix = str(extension or "").strip()
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    return (
        f"{sanitize_component(prefix)}_"
        f"{sanitize_component(display_version, fallback='version')}_"
        f"{stamp}{suffix}"
    )


def collision_safe_path(path: str | Path) -> Path:
    """Return ``path`` or a numbered sibling without overwriting silently."""

    target = Path(path)
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(2, 10_000):
        candidate = target.with_name(f"{stem}_{index:02d}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Cannot allocate a collision-safe export name for {target}")


def ensure_export_directory(path: str | Path) -> Path:
    """Create and return the current runtime export directory."""

    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return target
