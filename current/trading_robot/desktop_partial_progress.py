"""Small checksummed recovery checkpoint, not an execution or cash authority.

Called only while DesktopFillRecovery holds its account recovery lock. The
write-ahead record links one complete cumulative stage set to the before-state
and intended canonical transaction. Missing/conflicting evidence never repairs
Portfolio or releases Central. No network, order submission or cancellation.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .state_persistence import atomic_write_json

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = frozenset({"version", "intent_sha256", "exchange_id", "stages", "executed_lots",
                    "before_state_sha256", "after_transaction_id", "after_position_sha256"})


class PartialProgressError(RuntimeError):
    """Finite checkpoint error without local paths or private identifiers."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise PartialProgressError("PARTIAL_PROGRESS_INVALID")
        result[key] = value
    return result


class PartialProgressStore:
    """Per-intent, bounded, single-file checksum with atomic replacement.

    Atomic replacement uses the existing persistence owner. It is not a
    transaction spanning this checkpoint, canonical Portfolio, Risk and Central.
    Retained files are private local evidence, not broker-signed attestations.
    """

    def __init__(self, portfolio_path: Path) -> None:
        self.root = portfolio_path.parent / "desktop_partial_progress"

    def path(self, intent_sha256: str) -> Path:
        if not _HEX.fullmatch(intent_sha256) or self.root.is_symlink():
            raise PartialProgressError("PARTIAL_PROGRESS_SCOPE_INVALID")
        path = self.root / (intent_sha256 + ".json")
        if path.is_symlink():
            raise PartialProgressError("PARTIAL_PROGRESS_LINK_FORBIDDEN")
        return path

    def load(self, intent_sha256: str) -> dict[str, Any] | None:
        path = self.path(intent_sha256)
        if not path.exists():
            return None
        try:
            if not path.is_file() or path.stat().st_size > 131_072:
                raise PartialProgressError("PARTIAL_PROGRESS_INVALID")
            with path.open("rb") as stream:
                raw = stream.read(131_073)
            if len(raw) > 131_072:
                raise PartialProgressError("PARTIAL_PROGRESS_INVALID")
            document = json.loads(raw, object_pairs_hook=_pairs)
            if (type(document) is not dict or set(document) != {"payload", "sha256"}
                    or type(document["payload"]) is not dict
                    or document["sha256"] != digest(document["payload"])):
                raise PartialProgressError("PARTIAL_PROGRESS_CHECKSUM_INVALID")
            payload = document["payload"]
            self._validate(payload, intent_sha256)
            return payload
        except PartialProgressError:
            raise
        except Exception:
            raise PartialProgressError("PARTIAL_PROGRESS_INVALID") from None

    @staticmethod
    def _validate(payload: dict[str, Any], key: str) -> None:
        valid = (set(payload) == _FIELDS and type(payload.get("version")) is int
                 and payload["version"] == 1 and payload["intent_sha256"] == key
                 and type(payload["executed_lots"]) is int and payload["executed_lots"] > 0
                 and type(payload["stages"]) is list and 0 < len(payload["stages"]) <= 128
                 and type(payload["exchange_id"]) is str and 0 < len(payload["exchange_id"]) <= 128
                 and type(payload["after_transaction_id"]) is str
                 and payload["after_transaction_id"].startswith("desktop-partial:"))
        for field in ("before_state_sha256", "after_position_sha256"):
            valid = valid and type(payload[field]) is str and bool(_HEX.fullmatch(payload[field]))
        seen: set[str] = set()
        total = 0
        if valid:
            for item in payload["stages"]:
                if (type(item) is not list or len(item) != 4 or type(item[0]) is not str
                        or not item[0] or item[0] in seen or type(item[1]) is not int
                        or item[1] <= 0 or type(item[2]) is not str or type(item[3]) is not str):
                    valid = False
                    break
                seen.add(item[0]); total += item[1]
            valid = valid and total == payload["executed_lots"]
        if not valid:
            raise PartialProgressError("PARTIAL_PROGRESS_INVALID")

    def save(self, payload: dict[str, Any], *, expected: dict[str, Any] | None) -> None:
        key = payload["intent_sha256"]
        self._validate(payload, key)
        if self.load(key) != expected:
            raise PartialProgressError("PARTIAL_PROGRESS_CHANGED")
        document = {"payload": payload, "sha256": digest(payload)}
        if len(json.dumps(document, allow_nan=False).encode()) > 131_072:
            raise PartialProgressError("PARTIAL_PROGRESS_INVALID")
        try:
            atomic_write_json(self.path(key), document, retry_delays=(), jitter_fraction=0,
                              write_checksum=False, keep_last_good=False)
        except Exception:
            raise PartialProgressError("PARTIAL_PROGRESS_WRITE_FAILED") from None
        if self.load(key) != payload:
            raise PartialProgressError("PARTIAL_PROGRESS_READBACK_FAILED")
