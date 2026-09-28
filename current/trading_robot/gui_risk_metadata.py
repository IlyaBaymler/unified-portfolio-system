"""Offline, immutable metadata binding for the desktop composition root.

This is not an admission/dispatch authority.  The existing Risk and execution
owners remain responsible for all economic checks.  A changed runtime requires
new composition rather than an in-place metadata reload during trading.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

from .instrument_runtime import InstrumentRuntimeStore
from .multi_instrument_config import MultiInstrumentProfileStore
from .portfolio_risk_adapter import PortfolioRiskInstrumentMetadata
from .portfolio_risk_read_service import load_portfolio_risk_metadata


class GuiRiskMetadataError(RuntimeError):
    """Finite, non-sensitive failure at the composition boundary."""

    def __init__(self, reason: str = "GUI_RISK_METADATA_INVALID") -> None:
        self.reason = reason
        super().__init__(reason)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GuiRiskMetadataError()
        result[key] = value
    return result


def _read_regular(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise GuiRiskMetadataError()
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise GuiRiskMetadataError()
    return raw


def _snapshot(path: Path) -> tuple[str, dict[str, PortfolioRiskInstrumentMetadata]]:
    """Verify one pinned payload and compare the existing loader's result to it."""
    try:
        checksum_path = path.with_name(path.name + ".sha256")
        raw = _read_regular(path, 1_048_576)
        checksum = _read_regular(checksum_path, 128)
        digest = sha256(raw).hexdigest()
        if checksum.decode("ascii").strip().lower() != digest:
            raise GuiRiskMetadataError()
        fields = json.loads(raw, object_pairs_hook=_unique_object)
        if (
            type(fields) is not dict
            or set(fields) != {"version", "instruments"}
            or type(fields["version"]) is not int
            or fields["version"] != 1
            or type(fields["instruments"]) is not list
        ):
            raise GuiRiskMetadataError()
        pinned: dict[str, PortfolioRiskInstrumentMetadata] = {}
        for row in fields["instruments"]:
            if (
                type(row) is not dict
                or set(row) - {"instrument_id", "lot_size", "asset_class", "currency"}
                or type(row.get("instrument_id")) is not str
                or not row["instrument_id"]
                or row["instrument_id"] != row["instrument_id"].strip()
                or type(row.get("lot_size")) is not int
                or row["lot_size"] < 1
                or type(row.get("currency")) is not str
                or row["currency"].strip().upper() != "RUB"
                or (row.get("asset_class") is not None
                    and type(row["asset_class"]) is not str)
            ):
                raise GuiRiskMetadataError()
            item = PortfolioRiskInstrumentMetadata(**row)
            if item.instrument_id in pinned:
                raise GuiRiskMetadataError()
            pinned[item.instrument_id] = item
        loaded = load_portfolio_risk_metadata(path)
        if not pinned or loaded != pinned:
            raise GuiRiskMetadataError()
        # Both payload and sidecar must still be the bytes that were verified.
        # Comparing the parsed mapping above also catches a loader A/B/A race.
        if (_read_regular(path, 1_048_576) != raw
                or _read_regular(checksum_path, 128) != checksum):
            raise GuiRiskMetadataError()
        return digest, pinned
    except GuiRiskMetadataError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise GuiRiskMetadataError() from None


def _configured_scope(
    profiles: MultiInstrumentProfileStore,
    runtimes: InstrumentRuntimeStore,
    account_id: str,
) -> tuple[tuple[str, str], ...]:
    try:
        selected = profiles.load_mode("SANDBOX_EXECUTION")
        active = runtimes.load(expected_account_id=account_id)
        by_id = {item.config.instrument_id: item for item in active}
        if (len(selected) not in {2, 3} or len(by_id) != len(active)
                or len(selected) != len(active)):
            raise GuiRiskMetadataError("GUI_RISK_METADATA_SCOPE_INVALID")
        result = []
        for profile in selected:
            runtime = by_id.pop(profile.instrument_id, None)
            if runtime is None or runtime.config != profile.to_runtime_config(account_id):
                raise GuiRiskMetadataError("GUI_RISK_METADATA_SCOPE_INVALID")
            result.append((profile.instrument_id, runtime.config.runtime_config_hash))
        if by_id:
            raise GuiRiskMetadataError("GUI_RISK_METADATA_SCOPE_INVALID")
        return tuple(sorted(result))
    except GuiRiskMetadataError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise GuiRiskMetadataError("GUI_RISK_METADATA_SCOPE_INVALID") from None


@dataclass(frozen=True, slots=True)
class GuiRiskMetadataBinding:
    path: Path
    payload_sha256: str
    account_id: str
    configured_scope: tuple[tuple[str, str], ...]
    metadata: Mapping[str, PortfolioRiskInstrumentMetadata]

    def verify(
        self,
        profiles: MultiInstrumentProfileStore,
        runtimes: InstrumentRuntimeStore,
        authoritative_metadata: Mapping[str, PortfolioRiskInstrumentMetadata],
    ) -> None:
        if not isinstance(authoritative_metadata, Mapping):
            raise GuiRiskMetadataError("GUI_RISK_METADATA_CHANGED")
        digest, loaded = _snapshot(self.path)
        scope = _configured_scope(profiles, runtimes, self.account_id)
        if (digest != self.payload_sha256 or loaded != self.metadata
                or scope != self.configured_scope
                or dict(authoritative_metadata) != self.metadata):
            raise GuiRiskMetadataError("GUI_RISK_METADATA_CHANGED")


def bind_gui_risk_metadata(
    path: Path,
    *,
    profiles: MultiInstrumentProfileStore,
    runtimes: InstrumentRuntimeStore,
    account_id: str,
) -> GuiRiskMetadataBinding:
    scope = _configured_scope(profiles, runtimes, account_id)
    digest, loaded = _snapshot(path)
    if set(loaded) != {instrument_id for instrument_id, _ in scope}:
        raise GuiRiskMetadataError("GUI_RISK_METADATA_SCOPE_INVALID")
    return GuiRiskMetadataBinding(path, digest, account_id, scope, MappingProxyType(loaded))
