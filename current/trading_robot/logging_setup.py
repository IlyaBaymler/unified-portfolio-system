from __future__ import annotations

"""Logging helpers shared by the desktop GUI and command-line runner.

The compact log is intended for operators.  The debug log keeps full payloads
for post-mortem analysis.  Both formatters redact common token representations
before text reaches disk or the GUI.
"""

from dataclasses import dataclass
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
from typing import Any


_BEARER_RE = re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+\-/=]{12,}")
_TOKEN_ASSIGNMENT_RE = re.compile(
    r"(?i)(TBANK_(?:SANDBOX_)?TOKEN\s*[=:]\s*)[^\s,;'}\"]+"
)
_JSON_TOKEN_RE = re.compile(
    r'(?i)(["\'](?:token|authorization)["\']\s*:\s*["\'])[^"\']+(["\'])'
)


def redact_sensitive_text(value: str) -> str:
    """Redact common API-token forms from a formatted log line."""

    text = _BEARER_RE.sub(r"\1<REDACTED>", str(value))
    text = _TOKEN_ASSIGNMENT_RE.sub(r"\1<REDACTED>", text)
    text = _JSON_TOKEN_RE.sub(r"\1<REDACTED>\2", text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts secrets after regular interpolation."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))


@dataclass(frozen=True, slots=True)
class LoggingPaths:
    compact: Path
    debug: Path


def configure_file_logging(
    directory: str | Path,
    *,
    compact_name: str = "robot_gui.log",
    debug_name: str = "robot_debug.log",
    compact_level: int = logging.INFO,
    debug_enabled: bool = True,
    console: bool = False,
) -> LoggingPaths:
    """Configure rotating compact/debug logs without duplicating handlers.

    The root logger is kept at DEBUG so handler levels can decide which records
    are written.  Third-party libraries are restricted to WARNING to avoid
    flooding the research logs with matplotlib/urllib3 internals.
    """

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    compact_path = directory / compact_name
    debug_path = directory / debug_name

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    formatter = RedactingFormatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    existing_names = {getattr(handler, "name", None) for handler in root.handlers}

    if "moex_compact_file" not in existing_names:
        compact_handler = RotatingFileHandler(
            compact_path,
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        compact_handler.name = "moex_compact_file"
        compact_handler.setLevel(compact_level)
        compact_handler.setFormatter(formatter)
        root.addHandler(compact_handler)

    if debug_enabled and "moex_debug_file" not in existing_names:
        debug_handler = RotatingFileHandler(
            debug_path,
            maxBytes=10_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        debug_handler.name = "moex_debug_file"
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(formatter)
        root.addHandler(debug_handler)

    if console and "moex_console" not in existing_names:
        console_handler = logging.StreamHandler()
        console_handler.name = "moex_console"
        console_handler.setLevel(compact_level)
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

    for noisy_name in (
        "matplotlib",
        "PIL",
        "urllib3.connectionpool",
        "asyncio",
    ):
        logging.getLogger(noisy_name).setLevel(logging.WARNING)

    return LoggingPaths(compact=compact_path, debug=debug_path)


def concise_value(value: Any, *, limit: int = 160) -> str:
    """Return a one-line, bounded representation for operator-facing logs."""

    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
