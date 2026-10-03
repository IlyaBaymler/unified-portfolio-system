"""Shared, non-mutating privacy and cross-platform archive-name policy."""
from __future__ import annotations

import json
import stat
import unicodedata
from collections.abc import Iterable
from pathlib import Path

# Actual output namespaces of the current exact/versioned financial protocols.
# Match whole components, at any depth and case, not source module prefixes.
FINANCIAL_CAPTURE_DIRECTORIES = frozenset({
    "exact_cash_components", "exact_settlement_closure", "exact_fee_alias",
    "exact_fee_replacement", "exact_late_fee", "versioned_dispatch",
    "versioned_fill_cash", "versioned_fill_closure", "selected_sync",
    "owner_refresh", "cash_flow_resync", "risk_admission",
})
_FINANCIAL_DOMAIN_PREFIXES = ("CL7_", "V4_", "CL2_VERSION", "CL2_SAME_ID_FEE_",
                              "CL4_CL6_VERSIONED_", "VERSIONED_ORDER_ADMISSION_")
_DEVICES = {"con", "prn", "aux", "nul", "clock$", "conin$", "conout$",
            *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def has_financial_capture_path(parts: Iterable[str]) -> bool:
    return any(_key(part) in FINANCIAL_CAPTURE_DIRECTORIES for part in parts)


def has_financial_capture_body(raw: bytes) -> bool:
    """Recognize top-level protocol documents, including renamed signed plans.

    This is a protocol denylist, not a general secret/PII detector. Synthetic
    vector containers keep their own domain, and source code is not scanned as
    JSON. Existing explicit canary scanning remains independently enforced.
    """
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return False
    if not isinstance(value, dict):
        return False
    for body in (value, value.get("payload")):
        if isinstance(body, dict):
            domain = body.get("domain")
            if isinstance(domain, str) and domain.startswith(_FINANCIAL_DOMAIN_PREFIXES):
                return True
    return False


def private_capture_file(path: Path) -> bool:
    # Only JSON protocol captures; do not rewrite/filter legitimate Python code.
    return path.suffix.casefold() == ".json" and has_financial_capture_body(path.read_bytes())


def safe_parts(value: str, *, directory: bool = False, allow_empty: bool = False) -> tuple[str, ...]:
    """Validate, NEVER normalize, an untrusted ZIP name or archive root."""
    if not isinstance(value, str) or (not value and not allow_empty):
        raise RuntimeError("Unsafe archive path")
    if not value:
        return ()
    if directory:
        if not value.endswith("/"):
            raise RuntimeError("Archive directory must end in one slash")
        value = value[:-1]
    parts = tuple(value.split("/"))
    for part in parts:
        normalized = _key(part)
        if (not part or part in {".", ".."} or part != part.strip()
                or part.endswith((".", " ")) or "~" in part
                or unicodedata.normalize("NFC", part) != part
                or any(c in '\\:<>"|?*' or unicodedata.category(c).startswith("C") for c in part)
                or normalized.split(".", 1)[0].rstrip(" ") in _DEVICES
                or normalized in {".", ".."} or "~" in normalized
                or any(c in '/\\:<>"|?*' for c in normalized)
                or normalized.endswith((".", " "))):
            raise RuntimeError("Unsafe archive path: " + repr(value))
    return parts


def validate_member_inventory(members: Iterable[str]) -> None:
    """Reject exact duplicates, aliases and file/directory prefix collisions."""
    explicit: set[tuple[str, ...]] = set()
    nodes: dict[tuple[str, ...], tuple[tuple[str, ...], bool]] = {}
    for name in members:
        directory = name.endswith("/")
        parts = safe_parts(name, directory=directory)
        key = tuple(_key(part) for part in parts)
        if key in explicit:
            raise RuntimeError("Duplicate or normalized-colliding archive member")
        explicit.add(key)
        for i in range(1, len(parts) + 1):
            is_directory = i < len(parts) or directory
            value = (parts[:i], is_directory)
            previous = nodes.setdefault(key[:i], value)
            if previous != value:
                raise RuntimeError("Archive component alias or file/directory collision")


def validate_zip_metadata(info: object) -> None:
    # ZipInfo.orig_filename retains NUL bytes that .filename would truncate.
    if info.orig_filename != info.filename:
        raise RuntimeError("Noncanonical archive member name")
    safe_parts(info.filename, directory=info.is_dir())
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if kind not in ({0, stat.S_IFDIR} if info.is_dir() else {0, stat.S_IFREG}):
        raise RuntimeError("Symlink or special-file archive member")
    if info.flag_bits & 1:
        raise RuntimeError("Encrypted release member is not permitted")
