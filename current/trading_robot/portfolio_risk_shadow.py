from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from .central_order_manager import CentralOrderState
from .journal import EventJournal, JournalEvent
from .multi_instrument_strategy import StrategyProposal
from .portfolio_model import PortfolioState
from .portfolio_risk_adapter import (
    PortfolioRiskInputAdapter,
    PortfolioRiskInstrumentMetadata,
    portfolio_policy_from_risk_policy,
)
from .portfolio_risk_evaluator import PortfolioRiskEvaluator
from .portfolio_risk_model import (
    PortfolioRiskDecision,
    ProposedPortfolioChange,
    canonical_sha256,
)
from .portfolio_risk_read_service import load_portfolio_risk_metadata
from .risk import RiskState
from .risk_persistence import (
    RiskProfileStore,
    normalize_risk_mode,
)


class PortfolioRiskShadowError(RuntimeError):
    """Raised when an M3 shadow observation contract is malformed."""


@dataclass(frozen=True, slots=True)
class PortfolioRiskCandidateQuote:
    unit_price_rub: float
    price_at: datetime | str
    source: str

    def __post_init__(self) -> None:
        try:
            price = float(self.unit_price_rub)
        except (TypeError, ValueError) as exc:
            raise PortfolioRiskShadowError(
                "Candidate quote price must be positive."
            ) from exc
        if not isfinite(price) or price <= 0:
            raise PortfolioRiskShadowError("Candidate quote price must be positive.")
        source = str(self.source or "").strip().upper()
        if not source:
            raise PortfolioRiskShadowError("Candidate quote source must not be empty.")
        object.__setattr__(self, "unit_price_rub", price)
        object.__setattr__(self, "price_at", _aware(self.price_at))
        object.__setattr__(self, "source", source)


def _aware(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise PortfolioRiskShadowError(
                "Shadow timestamp must be ISO-8601."
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PortfolioRiskShadowError("Shadow timestamp must be timezone-aware.")
    return parsed.astimezone(timezone.utc)


def _target(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PortfolioRiskShadowError(f"{field} must be a non-negative integer.")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioRiskShadowError(
            f"{field} must be a non-negative integer."
        ) from exc
    if normalized != value or normalized < 0:
        raise PortfolioRiskShadowError(f"{field} must be a non-negative integer.")
    return normalized


def classify_shadow_drift(
    *,
    current_lots: int,
    requested_target_lots: int,
    actual_approved_target_lots: int,
    shadow_approved_target_lots: int,
) -> tuple[str, bool]:
    """Classify target drift without treating an expected new policy as a defect."""

    current = _target(current_lots, "current_lots")
    requested = _target(requested_target_lots, "requested_target_lots")
    actual = _target(actual_approved_target_lots, "actual_approved_target_lots")
    shadow = _target(shadow_approved_target_lots, "shadow_approved_target_lots")
    lower, upper = sorted((current, requested))
    if not lower <= actual <= upper:
        return "UNEXPLAINED_ACTUAL_TARGET_OUTSIDE_PROPOSAL", True
    if not lower <= shadow <= upper:
        return "UNEXPLAINED_SHADOW_TARGET_OUTSIDE_PROPOSAL", True
    if actual == shadow:
        return "MATCH", False
    actual_progress = abs(actual - current)
    shadow_progress = abs(shadow - current)
    if shadow_progress < actual_progress:
        return "EXPLAINED_SHADOW_MORE_RESTRICTIVE", False
    if shadow_progress > actual_progress:
        return "EXPLAINED_V3_8_MORE_RESTRICTIVE", False
    return "UNEXPLAINED_DIRECTIONAL_DRIFT", True


@dataclass(frozen=True, slots=True)
class PortfolioRiskShadowResult:
    shadow_key: str
    status: str
    portfolio_policy_status: str
    actual_approved_target_lots: int
    actual_risk_status: str | None
    actual_risk_decision_id: str
    drift_classification: str
    unexplained_drift: bool
    profile_policy_hash: str | None = None
    actual_reason_codes: tuple[str, ...] = ()
    candidate_price_at: str | None = None
    candidate_price_source: str | None = None
    decision: PortfolioRiskDecision | None = None
    reason_codes: tuple[str, ...] = ()
    journal_event_id: int | None = None
    journal_inserted: bool = False
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        normalized_status = str(self.status or "").strip().upper()
        if normalized_status not in {"EVALUATED", "UNAVAILABLE"}:
            raise PortfolioRiskShadowError("Unsupported shadow result status.")
        object.__setattr__(self, "status", normalized_status)
        if self.execution_authorized is not False:
            raise PortfolioRiskShadowError(
                "Portfolio Risk shadow cannot authorize execution."
            )
        if len(self.shadow_key) != 64 or any(
            item not in "0123456789abcdef" for item in self.shadow_key
        ):
            raise PortfolioRiskShadowError("shadow_key must be SHA-256.")
        object.__setattr__(
            self,
            "actual_approved_target_lots",
            _target(
                self.actual_approved_target_lots,
                "actual_approved_target_lots",
            ),
        )
        normalized_actual_status = str(self.actual_risk_status or "").strip().upper()
        object.__setattr__(
            self,
            "actual_risk_status",
            normalized_actual_status or None,
        )
        normalized_actual_id = str(self.actual_risk_decision_id or "").strip()
        if not normalized_actual_id:
            raise PortfolioRiskShadowError(
                "actual_risk_decision_id must not be empty."
            )
        object.__setattr__(
            self,
            "actual_risk_decision_id",
            normalized_actual_id,
        )
        if self.profile_policy_hash is not None:
            normalized_hash = str(self.profile_policy_hash).strip().lower()
            if len(normalized_hash) != 64 or any(
                item not in "0123456789abcdef" for item in normalized_hash
            ):
                raise PortfolioRiskShadowError(
                    "profile_policy_hash must be SHA-256 or None."
                )
            object.__setattr__(self, "profile_policy_hash", normalized_hash)
        actual_codes = tuple(
            sorted({str(item or "").strip() for item in self.actual_reason_codes})
        )
        if any(not item for item in actual_codes):
            raise PortfolioRiskShadowError(
                "Actual Risk reason code must not be empty."
            )
        object.__setattr__(self, "actual_reason_codes", actual_codes)
        if self.candidate_price_at is not None:
            object.__setattr__(
                self,
                "candidate_price_at",
                _aware(self.candidate_price_at).isoformat(),
            )
        if self.candidate_price_source is not None:
            source = str(self.candidate_price_source).strip().upper()
            if not source:
                raise PortfolioRiskShadowError(
                    "candidate_price_source must not be empty."
                )
            object.__setattr__(self, "candidate_price_source", source)
        codes = tuple(sorted({str(item or "").strip() for item in self.reason_codes}))
        if any(not item for item in codes):
            raise PortfolioRiskShadowError("Shadow reason code must not be empty.")
        object.__setattr__(self, "reason_codes", codes)
        if normalized_status == "EVALUATED" and self.decision is None:
            raise PortfolioRiskShadowError("Evaluated shadow must contain a decision.")
        if normalized_status == "UNAVAILABLE" and self.decision is not None:
            raise PortfolioRiskShadowError(
                "Unavailable shadow must not contain a decision."
            )

    @property
    def decision_id(self) -> str | None:
        return self.decision.decision_id if self.decision is not None else None

    @property
    def shadow_approved_target_lots(self) -> int | None:
        return (
            self.decision.approved_target_lots
            if self.decision is not None
            else None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "shadow_key": self.shadow_key,
            "status": self.status,
            "portfolio_policy_status": self.portfolio_policy_status,
            "actual_approved_target_lots": self.actual_approved_target_lots,
            "actual_risk_status": self.actual_risk_status,
            "actual_risk_decision_id": self.actual_risk_decision_id,
            "actual_reason_codes": list(self.actual_reason_codes),
            "candidate_price_at": self.candidate_price_at,
            "candidate_price_source": self.candidate_price_source,
            "shadow_approved_target_lots": self.shadow_approved_target_lots,
            "drift_classification": self.drift_classification,
            "unexplained_drift": self.unexplained_drift,
            "reason_codes": list(self.reason_codes),
            "profile_policy_hash": self.profile_policy_hash,
            "decision": self.decision.to_dict() if self.decision is not None else None,
            "journal_event_id": self.journal_event_id,
            "journal_inserted": self.journal_inserted,
            "execution_authorized": False,
        }


class PortfolioRiskShadowObserver:
    """Evaluate and journal M3 shadow without changing v3.8 authorization."""

    def __init__(
        self,
        *,
        account_id: str,
        mode: str,
        profile_store: RiskProfileStore,
        journal: EventJournal,
        instrument_metadata: Mapping[
            str,
            PortfolioRiskInstrumentMetadata,
        ]
        | None = None,
    ) -> None:
        self.account_id = str(account_id or "").strip()
        if not self.account_id:
            raise PortfolioRiskShadowError("account_id must not be empty.")
        self.mode = normalize_risk_mode(mode)
        self.profile_store = profile_store
        self.journal = journal
        self.instrument_metadata = dict(instrument_metadata or {})

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        *,
        account_id: str,
        mode: str = "SANDBOX_EXECUTION",
    ) -> PortfolioRiskShadowObserver:
        root = Path(directory)
        metadata_path = root / "portfolio_risk_metadata.json"
        return cls(
            account_id=account_id,
            mode=mode,
            profile_store=RiskProfileStore(root / "risk_profiles.json"),
            journal=EventJournal(root / "trading_events.db"),
            instrument_metadata=(
                load_portfolio_risk_metadata(metadata_path)
                if metadata_path.is_file()
                else None
            ),
        )

    def observe(
        self,
        *,
        proposal: StrategyProposal,
        portfolio: PortfolioState,
        central_orders: CentralOrderState,
        risk_state: RiskState,
        actual_approved_target_lots: int,
        actual_risk_decision_id: str,
        actual_risk_status: str | None = None,
        actual_reason_codes: tuple[str, ...] = (),
        actual_risk_policy_hash: str | None = None,
        lot_size: int,
        evaluated_at: datetime,
        candidate_quote: PortfolioRiskCandidateQuote | None = None,
        cash_buffer_bps: int = 100,
        excluded_reservation_ids: tuple[str, ...] = (),
    ) -> PortfolioRiskShadowResult:
        actual = _target(
            actual_approved_target_lots,
            "actual_approved_target_lots",
        )
        actual_decision_id = str(actual_risk_decision_id or "").strip()
        if not actual_decision_id:
            raise PortfolioRiskShadowError(
                "actual_risk_decision_id must not be empty."
            )
        expected_policy_hash = str(actual_risk_policy_hash or "").strip().lower()
        eligible_identity = {
            "account_id": self.account_id,
            "runtime_key": proposal.runtime_key,
            "instrument_id": proposal.instrument_id,
            "candle_time": proposal.candle_time,
            "candidate_quote": (
                {
                    "unit_price_rub": candidate_quote.unit_price_rub,
                    "price_at": candidate_quote.price_at.isoformat(),
                    "source": candidate_quote.source,
                }
                if candidate_quote is not None
                else None
            ),
            "strategy_profile_hash": proposal.strategy_profile_hash,
            "requested_target_lots": proposal.primary_target_lots,
            "snapshot_revision": portfolio.revision,
            "snapshot_checksum": portfolio.decision_sha256,
            "central_order_revision": central_orders.revision,
            "actual_risk_decision_id": actual_decision_id,
        }
        eligible_key = canonical_sha256(eligible_identity)
        policy_status = "UNAVAILABLE"
        profile_policy_hash = None
        try:
            loaded = self.profile_store.require_portfolio_policy(self.mode)
            profile_policy_hash = str(loaded["policy_hash"])
            if (
                expected_policy_hash
                and profile_policy_hash != expected_policy_hash
            ):
                raise PortfolioRiskShadowError(
                    "Risk policy changed between v3.8 and shadow evaluation."
                )
            policy_scope = str(loaded.get("account_scope") or "").strip()
            if policy_scope and policy_scope != self.account_id:
                raise PortfolioRiskShadowError(
                    "Risk profile account scope mismatch."
                )
            policy_status = str(loaded["portfolio_policy_status"])
            policy = loaded["policy"]
            current_position = portfolio.position(proposal.instrument_id)
            metadata = dict(self.instrument_metadata)
            configured_candidate = metadata.get(proposal.instrument_id)
            if (
                configured_candidate is not None
                and configured_candidate.lot_size != int(lot_size)
            ):
                raise PortfolioRiskShadowError(
                    "Candidate lot size differs from Portfolio Risk metadata."
                )
            metadata[proposal.instrument_id] = PortfolioRiskInstrumentMetadata(
                instrument_id=proposal.instrument_id,
                lot_size=lot_size,
                asset_class=(
                    current_position.asset_type
                    if current_position is not None
                    and current_position.asset_type.upper() != "UNKNOWN"
                    else (
                        configured_candidate.asset_class
                        if configured_candidate is not None
                        else None
                    )
                ),
                currency=(
                    current_position.currency
                    if current_position is not None and current_position.currency
                    else (
                        configured_candidate.currency
                        if configured_candidate is not None
                        and configured_candidate.currency
                        else None
                    )
                ),
            )
            risk_input = PortfolioRiskInputAdapter().build(
                portfolio=portfolio,
                central_orders=central_orders,
                risk_state=risk_state,
                evaluated_at=evaluated_at,
                instrument_metadata=metadata,
                excluded_reservation_ids=excluded_reservation_ids,
            )
            quote = candidate_quote
            if quote is None:
                raise PortfolioRiskShadowError(
                    "Portfolio Risk candidate quote is unavailable."
                )
            if not 0 <= int(cash_buffer_bps) <= 5_000:
                raise PortfolioRiskShadowError(
                    "cash_buffer_bps must be in [0, 5000]."
                )
            current_lots = (
                int(current_position.actual_lots)
                if current_position is not None
                else 0
            )
            candidate_currency = metadata[proposal.instrument_id].currency
            if candidate_currency is None:
                raise PortfolioRiskShadowError(
                    "Portfolio Risk candidate currency is unavailable."
                )
            change = ProposedPortfolioChange(
                instrument_id=proposal.instrument_id,
                ticker=proposal.ticker,
                strategy_id=proposal.primary_strategy,
                asset_class=metadata[proposal.instrument_id].asset_class,
                current_lots=current_lots,
                requested_target_lots=int(proposal.primary_target_lots),
                lot_size=int(lot_size),
                currency=candidate_currency,
                price_per_lot_rub=quote.unit_price_rub * int(lot_size),
                reservation_per_lot_rub=(
                    quote.unit_price_rub
                    * int(lot_size)
                    * (10_000 + int(cash_buffer_bps))
                    / 10_000
                ),
                price_at=quote.price_at,
                price_source=quote.source,
            )
            computational_policy = replace(
                portfolio_policy_from_risk_policy(policy),
                mode="OBSERVE_ONLY",
            )
            decision = PortfolioRiskEvaluator(computational_policy).evaluate(
                risk_input,
                change,
            ).decision
            drift, unexplained = classify_shadow_drift(
                current_lots=current_lots,
                requested_target_lots=proposal.primary_target_lots,
                actual_approved_target_lots=actual,
                shadow_approved_target_lots=decision.approved_target_lots,
            )
            shadow_key = eligible_key
            result = PortfolioRiskShadowResult(
                shadow_key=shadow_key,
                status="EVALUATED",
                portfolio_policy_status=policy_status,
                actual_approved_target_lots=actual,
                actual_risk_status=actual_risk_status,
                actual_risk_decision_id=actual_decision_id,
                drift_classification=drift,
                unexplained_drift=unexplained,
                profile_policy_hash=profile_policy_hash,
                actual_reason_codes=actual_reason_codes,
                candidate_price_at=quote.price_at.isoformat(),
                candidate_price_source=(
                    quote.source
                ),
                decision=decision,
                reason_codes=(
                    *decision.hard_blocks,
                    *decision.policy_halts,
                    *decision.adjustments,
                ),
            )
        except (
            ArithmeticError,
            LookupError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            reason_code = f"SHADOW_UNAVAILABLE:{type(exc).__name__.upper()}"
            shadow_key = eligible_key
            result = PortfolioRiskShadowResult(
                shadow_key=shadow_key,
                status="UNAVAILABLE",
                portfolio_policy_status=policy_status,
                actual_approved_target_lots=actual,
                actual_risk_status=actual_risk_status,
                actual_risk_decision_id=actual_decision_id,
                drift_classification="NOT_EVALUATED",
                unexplained_drift=False,
                profile_policy_hash=profile_policy_hash,
                actual_reason_codes=actual_reason_codes,
                candidate_price_at=(
                    candidate_quote.price_at.isoformat()
                    if candidate_quote is not None
                    else None
                ),
                candidate_price_source=(
                    candidate_quote.source
                    if candidate_quote is not None
                    else None
                ),
                reason_codes=(reason_code,),
            )
        try:
            event_id, inserted = self.journal.record_portfolio_risk_shadow(
                JournalEvent(
                category="portfolio_risk_shadow",
                event_type="PORTFOLIO_RISK_SHADOW_DECISION",
                severity=(
                    "ERROR"
                    if result.status == "UNAVAILABLE" or result.unexplained_drift
                    else ("WARNING" if result.drift_classification != "MATCH" else "INFO")
                ),
                run_id=result.shadow_key,
                account_id=self.account_id,
                instrument_id=proposal.instrument_id,
                ticker=proposal.ticker,
                candle_time=proposal.candle_time,
                mode=self.mode,
                status=result.status,
                action="SHADOW_ONLY",
                strategy_id=proposal.primary_strategy,
                config_hash=proposal.strategy_profile_hash,
                payload=result.to_dict(),
                timestamp_utc=_aware(evaluated_at).isoformat(),
                )
            )
        except (OSError, RuntimeError, sqlite3.Error):
            return replace(
                result,
                reason_codes=(*result.reason_codes, "SHADOW_JOURNAL_WRITE_FAILED"),
            )
        return replace(
            result,
            journal_event_id=event_id,
            journal_inserted=inserted,
        )


def build_portfolio_risk_shadow_report(
    journal: EventJournal,
    *,
    account_id: str | None = None,
    limit: int = 100_000,
) -> dict[str, Any]:
    rows = journal.recent(
        limit=limit,
        category="portfolio_risk_shadow",
        account_id=account_id,
        event_type="PORTFOLIO_RISK_SHADOW_DECISION",
    )
    total = len(rows)
    evaluated = sum(item.get("status") == "EVALUATED" for item in rows)
    unavailable = total - evaluated
    drift_counts: dict[str, int] = {}
    unexplained = 0
    for row in rows:
        payload = row.get("payload") or {}
        drift = str(payload.get("drift_classification") or "UNKNOWN")
        drift_counts[drift] = drift_counts.get(drift, 0) + 1
        unexplained += int(bool(payload.get("unexplained_drift")))
    return {
        "status": (
            "PASS"
            if total > 0 and evaluated == total and unexplained == 0
            else "INCOMPLETE"
        ),
        "total_eligible_observations": total,
        "evaluated": evaluated,
        "unavailable": unavailable,
        "coverage_fraction": (evaluated / total if total else 0.0),
        "unexplained_drift": unexplained,
        "drift_counts": dict(sorted(drift_counts.items())),
        "execution_authorized": False,
        "latest": rows[0] if rows else None,
    }
