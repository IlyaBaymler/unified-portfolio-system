from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from .central_order_manager import CentralOrderStore
from .portfolio_repository import PortfolioRepository
from .portfolio_risk_adapter import (
    PortfolioRiskInputAdapter,
    PortfolioRiskInstrumentMetadata,
    PortfolioRiskReadOnlyReport,
    build_portfolio_risk_read_only_report,
)
from .risk_persistence import RiskProfileStore, RiskStateStore, normalize_risk_mode
from .state_persistence import read_json_verified


class PortfolioRiskReadServiceError(RuntimeError):
    """Raised when a checksummed runtime cannot produce a trusted report."""


def load_portfolio_risk_metadata(
    path: str | Path,
) -> dict[str, PortfolioRiskInstrumentMetadata]:
    selected = Path(path)
    checksum = selected.with_name(selected.name + ".sha256")
    if not checksum.is_file():
        raise PortfolioRiskReadServiceError(
            f"Portfolio Risk metadata checksum is missing: {checksum}"
        )
    try:
        raw = read_json_verified(selected, supported_versions={1})
    except Exception as exc:
        raise PortfolioRiskReadServiceError(
            f"Portfolio Risk metadata cannot be verified: {selected}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise PortfolioRiskReadServiceError(
            "Portfolio Risk metadata root must be an object."
        )
    root_unknown_fields = sorted(set(raw) - {"version", "instruments"})
    if root_unknown_fields:
        raise PortfolioRiskReadServiceError(
            "Portfolio Risk metadata root contains unsupported fields: "
            + ", ".join(str(item) for item in root_unknown_fields)
        )
    rows = raw.get("instruments")
    if not isinstance(rows, list) or any(not isinstance(item, Mapping) for item in rows):
        raise PortfolioRiskReadServiceError(
            "Portfolio Risk metadata instruments must be an array of objects."
        )
    result: dict[str, PortfolioRiskInstrumentMetadata] = {}
    for row in rows:
        unknown_fields = sorted(
            set(row) - {"instrument_id", "lot_size", "asset_class", "currency"}
        )
        if unknown_fields:
            raise PortfolioRiskReadServiceError(
                "Portfolio Risk metadata contains unsupported fields: "
                + ", ".join(str(item) for item in unknown_fields)
            )
        item = PortfolioRiskInstrumentMetadata(
            instrument_id=row.get("instrument_id", ""),
            lot_size=row.get("lot_size", 0),
            asset_class=row.get("asset_class"),
            currency=row.get("currency"),
        )
        if item.instrument_id in result:
            raise PortfolioRiskReadServiceError(
                f"Duplicate Portfolio Risk metadata: {item.instrument_id}"
            )
        result[item.instrument_id] = item
    return result


class PortfolioRiskReadService:
    """Read checksummed v3.8 stores and build a side-effect-free M2 report."""

    def __init__(self, runtime_directory: str | Path) -> None:
        self.root = Path(runtime_directory)

    def build_report(
        self,
        *,
        account_id: str,
        mode: str = "DRY_RUN",
        metadata: Mapping[str, PortfolioRiskInstrumentMetadata] | None = None,
        evaluated_at: datetime | None = None,
    ) -> PortfolioRiskReadOnlyReport:
        selected_account = str(account_id or "").strip()
        if not selected_account:
            raise PortfolioRiskReadServiceError("account_id must not be empty.")
        normalized_mode = normalize_risk_mode(mode)
        try:
            portfolio = PortfolioRepository(
                self.root / "portfolio_state.json"
            ).load(expected_account_id=selected_account)
            central_path = self.root / "central_order_state.json"
            if not central_path.is_file():
                raise PortfolioRiskReadServiceError(
                    "central_order_state.json is missing; reservation projection "
                    "cannot be trusted."
                )
            central = CentralOrderStore(central_path).load(
                expected_account_id=selected_account
            )
            loaded_profile = RiskProfileStore(
                self.root / "risk_profiles.json"
            ).require_profile(normalized_mode)
            profile_scope = str(loaded_profile.get("account_scope") or "").strip()
            if profile_scope and profile_scope != selected_account:
                raise PortfolioRiskReadServiceError(
                    "Risk profile account scope mismatch: "
                    f"{profile_scope} != {selected_account}."
                )
            risk_state = RiskStateStore(self.root / "risk_state.json").load_account(
                selected_account
            )
            risk_input = PortfolioRiskInputAdapter().build(
                portfolio=portfolio,
                central_orders=central,
                risk_state=risk_state,
                evaluated_at=evaluated_at or datetime.now(timezone.utc),
                instrument_metadata=metadata,
            )
            return build_portfolio_risk_read_only_report(
                risk_input=risk_input,
                policy=loaded_profile["policy"],
                mode=normalized_mode,
            )
        except PortfolioRiskReadServiceError:
            raise
        except Exception as exc:
            raise PortfolioRiskReadServiceError(
                f"Portfolio Risk read-only report is unavailable: {exc}"
            ) from exc
