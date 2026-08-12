from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .journal import EventJournal, JournalEvent
from .locking import InterProcessFileLock
from .state_persistence import atomic_write_json
from .tbank_sandbox import TBankSandboxClient, quotation_to_float


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OwnershipRecoveryRequest:
    account_id: str
    ticker: str
    class_code: str
    candle_interval: str
    primary_strategy: str
    primary_config_hash: str
    strategy_suite_hash: str
    shadow_strategies: tuple[str, ...]
    expected_lots: int
    confirmation_text: str

    @property
    def expected_phrase(self) -> str:
        return f"ADOPT {self.ticker.upper()} {int(self.expected_lots)}"


@dataclass(frozen=True, slots=True)
class ExternalCloseAcknowledgementRequest:
    account_id: str
    ticker: str
    class_code: str
    expected_target_lots: int
    confirmation_text: str

    @property
    def expected_phrase(self) -> str:
        return f"ACK EXTERNAL CLOSE {self.ticker.upper()} 0"


@dataclass(frozen=True, slots=True)
class PortfolioPositionView:
    instrument_id: str
    figi: str
    ticker: str
    class_code: str
    asset_type: str
    currency: str
    quantity: float
    quantity_lots: int
    average_price: float | None
    current_price: float | None
    market_value: float | None
    expected_yield: float | None
    target_lots: int | None
    owner_strategy: str | None
    owner_config_hash: str | None
    owner_interval: str | None
    ownership_status: str
    reconciliation_status: str
    local_pending_order_ids: tuple[str, ...]
    broker_pending_order_ids: tuple[str, ...]
    last_candle_time: str | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        # Compatibility alias used by the core robot and diagnostic exports.
        result["current_lots"] = self.quantity_lots
        return result


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    account_id: str
    checked_at: str
    total_value: float | None
    cash_rub: float
    securities_value: float | None
    expected_yield: float | None
    cash_by_currency: dict[str, float]
    blocked_by_currency: dict[str, float]
    positions: tuple[PortfolioPositionView, ...]
    broker_orders: tuple[dict[str, Any], ...]
    local_pending_orders: tuple[dict[str, Any], ...]
    state_status: str
    warnings: tuple[str, ...]
    unattributed_positions: int
    reconciliation_mismatches: int
    raw_totals: dict[str, float | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "checked_at": self.checked_at,
            "total_value": self.total_value,
            "cash_rub": self.cash_rub,
            "securities_value": self.securities_value,
            "expected_yield": self.expected_yield,
            "cash_by_currency": dict(self.cash_by_currency),
            "blocked_by_currency": dict(self.blocked_by_currency),
            "positions": [item.to_dict() for item in self.positions],
            "broker_orders": list(self.broker_orders),
            "local_pending_orders": list(self.local_pending_orders),
            "state_status": self.state_status,
            "warnings": list(self.warnings),
            "unattributed_positions": self.unattributed_positions,
            "reconciliation_mismatches": self.reconciliation_mismatches,
            "raw_totals": dict(self.raw_totals),
        }


class PortfolioManager:
    """Read-only Sandbox portfolio projection and controlled ownership repair.

    Broker quantities are authoritative. Local state contributes strategy
    ownership, model targets, pending intents and reconciliation metadata. The
    GUI consumes the resulting projection and never calculates portfolio state
    on its own.
    """

    def __init__(
        self,
        api: TBankSandboxClient,
        account_id: str,
        *,
        state_file: str | Path,
        journal_file: str | Path | None = None,
    ) -> None:
        self.api = api
        self.account_id = str(account_id)
        self.state_path = Path(state_file)
        self.journal = EventJournal(
            Path(journal_file)
            if journal_file
            else self.state_path.with_name("trading_events.db")
        )

    def snapshot(self, *, record_event: bool = True) -> PortfolioSnapshot:
        portfolio = self.api.get_portfolio(self.account_id)
        positions_payload = self.api.get_positions(self.account_id)
        broker_orders = tuple(self.api.get_orders(self.account_id))
        root, state_status, warnings = self._load_state_for_view()
        local_index = _build_local_state_index(root, self.account_id)
        broker_order_index = _index_broker_orders(broker_orders)

        position_views: list[PortfolioPositionView] = []
        for raw in portfolio.get("positions", []):
            if not isinstance(raw, dict):
                continue
            metadata = self._resolve_instrument_metadata(raw)
            instrument_id = str(
                metadata.get("uid")
                or raw.get("instrumentUid")
                or raw.get("instrumentId")
                or metadata.get("figi")
                or raw.get("figi")
                or raw.get("positionUid")
                or ""
            )
            figi = str(metadata.get("figi") or raw.get("figi") or "")
            position_uid = str(
                raw.get("positionUid") or metadata.get("positionUid") or ""
            )
            local: dict[str, Any] = {}
            for candidate in (
                instrument_id,
                str(raw.get("instrumentUid") or ""),
                str(raw.get("instrumentId") or ""),
                figi,
                position_uid,
            ):
                if candidate and candidate in local_index:
                    local = local_index[candidate]
                    break
            asset_type = str(
                raw.get("instrumentType")
                or metadata.get("instrumentType")
                or metadata.get("instrumentKind")
                or raw.get("assetType")
                or ""
            ).strip().lower()
            # T-Invest includes the cash balance as a synthetic currency row in
            # GetPortfolio.positions. Cash is already represented by
            # GetPositions.money and must never participate in position
            # ownership, reconciliation, adoption, or safe-close workflows.
            if asset_type == "currency":
                continue

            quantity = quotation_to_float(raw.get("quantity"))
            lots_raw = raw.get("quantityLots")
            quantity_lots = (
                int(round(quotation_to_float(lots_raw)))
                if isinstance(lots_raw, dict)
                else int(round(quantity))
            )
            if quantity_lots == 0 and abs(quantity) < 1e-12:
                continue

            ticker = str(
                metadata.get("ticker")
                or raw.get("ticker")
                or local.get("ticker")
                or figi
                or instrument_id[:12]
                or "—"
            )
            class_code = str(
                metadata.get("classCode")
                or raw.get("classCode")
                or local.get("class_code")
                or ""
            )
            target_lots = _as_optional_int(local.get("target_lots"))
            owner = (
                local.get("active_primary")
                if isinstance(local.get("active_primary"), dict)
                else None
            )
            local_pending = tuple(
                str(item)
                for item in local.get("local_pending_order_ids", ())
                if item
            )
            broker_pending = tuple(
                broker_order_index.get(instrument_id, ())
                or broker_order_index.get(figi, ())
            )

            if quantity_lots != 0 and owner is None:
                ownership_status = "UNATTRIBUTED"
            elif owner is not None:
                ownership_status = "ATTRIBUTED"
            else:
                ownership_status = "FLAT"

            if ownership_status == "UNATTRIBUTED":
                # Quantity equality alone is not sufficient reconciliation when
                # the local state cannot prove who owns the broker position.
                # Surface the ambiguity explicitly so the GUI cannot present a
                # green MATCH for an unmanaged open position.
                reconciliation = "UNATTRIBUTED"
            elif local_pending or broker_pending:
                reconciliation = "PENDING"
            elif target_lots is None:
                reconciliation = "UNKNOWN"
            elif target_lots == quantity_lots:
                reconciliation = "MATCH"
            else:
                reconciliation = "MISMATCH"

            current_price = _optional_quotation(raw.get("currentPrice"))
            market_value = (
                current_price * quantity
                if current_price is not None
                else None
            )
            position_views.append(
                PortfolioPositionView(
                    instrument_id=instrument_id,
                    figi=figi,
                    ticker=ticker,
                    class_code=class_code,
                    asset_type=asset_type,
                    currency=str(metadata.get("currency") or "").lower()
                    or _money_currency(
                        raw.get("currentPrice")
                        or raw.get("averagePositionPrice")
                    ),
                    quantity=quantity,
                    quantity_lots=quantity_lots,
                    average_price=_optional_quotation(
                        raw.get("averagePositionPrice")
                    ),
                    current_price=current_price,
                    market_value=market_value,
                    expected_yield=_optional_quotation(raw.get("expectedYield")),
                    target_lots=target_lots,
                    owner_strategy=(
                        str(owner.get("strategy_id")) if owner else None
                    ),
                    owner_config_hash=(
                        str(owner.get("config_hash")) if owner else None
                    ),
                    owner_interval=(
                        str(owner.get("candle_interval")) if owner else None
                    ),
                    ownership_status=ownership_status,
                    reconciliation_status=reconciliation,
                    local_pending_order_ids=local_pending,
                    broker_pending_order_ids=broker_pending,
                    last_candle_time=(
                        str(local.get("last_candle_time") or "") or None
                    ),
                )
            )

        cash = _money_list(positions_payload.get("money", []))
        blocked = _money_list(positions_payload.get("blocked", []))
        local_pending_orders = tuple(_collect_local_pending(root, self.account_id))
        raw_totals = {
            key: _optional_quotation(portfolio.get(key))
            for key in (
                "totalAmountPortfolio",
                "totalAmountShares",
                "totalAmountBonds",
                "totalAmountEtf",
                "totalAmountCurrencies",
                "totalAmountFutures",
                "expectedYield",
            )
        }
        securities_value = sum(
            value or 0.0
            for key, value in raw_totals.items()
            if key
            in {
                "totalAmountShares",
                "totalAmountBonds",
                "totalAmountEtf",
                "totalAmountFutures",
            }
        )
        unattributed = sum(
            item.ownership_status == "UNATTRIBUTED" for item in position_views
        )
        mismatches = sum(
            item.reconciliation_status in {"MISMATCH", "PENDING"}
            for item in position_views
        )
        if unattributed:
            warnings.append(
                f"Detected {unattributed} unattributed open position(s)."
            )
        if mismatches:
            warnings.append(
                f"Detected {mismatches} reconciliation mismatch/pending position(s)."
            )

        snapshot = PortfolioSnapshot(
            account_id=self.account_id,
            checked_at=datetime.now(timezone.utc).isoformat(),
            total_value=raw_totals.get("totalAmountPortfolio"),
            cash_rub=float(cash.get("rub", 0.0)),
            securities_value=securities_value,
            expected_yield=raw_totals.get("expectedYield"),
            cash_by_currency=cash,
            blocked_by_currency=blocked,
            positions=tuple(
                sorted(position_views, key=lambda item: (item.ticker, item.instrument_id))
            ),
            broker_orders=broker_orders,
            local_pending_orders=local_pending_orders,
            state_status=state_status,
            warnings=tuple(warnings),
            unattributed_positions=int(unattributed),
            reconciliation_mismatches=int(mismatches),
            raw_totals=raw_totals,
        )
        if record_event:
            self._record(
                "PORTFOLIO_SNAPSHOT",
                severity=("WARNING" if warnings else "INFO"),
                payload={
                    "state_status": state_status,
                    "positions": [item.to_dict() for item in snapshot.positions],
                    "cash_by_currency": cash,
                    "broker_order_count": len(broker_orders),
                    "local_pending_count": len(local_pending_orders),
                    "warnings": warnings,
                },
            )
        return snapshot

    def ownership_recovery_precheck(
        self,
        request: OwnershipRecoveryRequest,
    ) -> dict[str, Any]:
        if str(request.account_id) != self.account_id:
            raise ValueError("Ownership request account does not match manager account.")
        if request.expected_lots < 1:
            raise ValueError("expected_lots must be positive.")
        if request.confirmation_text.strip().upper() != request.expected_phrase:
            raise ValueError(
                f"Confirmation mismatch; expected {request.expected_phrase}."
            )
        instrument = self.api.find_instrument(request.ticker, request.class_code)
        instrument_id = self.api.instrument_id(instrument)
        portfolio = self.api.get_portfolio(self.account_id)
        current_lots = self.api.position_lots(portfolio, instrument)
        broker_orders = self.api.get_orders(self.account_id)
        root = self._load_state_strict()
        scope_key = _execution_scope_key(
            self.account_id,
            request.ticker,
            request.class_code,
        )
        scope = root.setdefault("execution_scopes", {}).setdefault(scope_key, {})
        active = scope.get("active_primary")
        desired = {
            "strategy_id": request.primary_strategy,
            "config_hash": request.primary_config_hash,
            "candle_interval": request.candle_interval,
            "strategy_suite_hash": request.strategy_suite_hash,
            "shadow_strategies": list(request.shadow_strategies),
        }
        state_key = _bot_state_key(self.account_id, request)
        bot_state = root.get("bots", {}).get(state_key, {})
        last_decisions = (
            bot_state.get("last_strategy_decisions", {})
            if isinstance(bot_state, dict)
            else {}
        )
        evidence = (
            last_decisions.get(request.primary_strategy)
            if isinstance(last_decisions, dict)
            else None
        )
        evidence_target = (
            _as_optional_int((evidence or {}).get("target_lots"))
            if isinstance(evidence, dict)
            else None
        )
        local_pending = _collect_local_pending(
            root,
            self.account_id,
            scope_prefix=scope_key,
        )

        reasons: list[str] = []
        if current_lots != int(request.expected_lots):
            reasons.append(
                "Broker position changed or does not match the request "
                f"({current_lots} != {request.expected_lots})."
            )
        if active:
            reasons.append(
                "The position already has an active PRIMARY owner; ownership "
                "recovery is only valid for UNATTRIBUTED positions."
            )
        if local_pending:
            reasons.append(
                "A local pending order exists in this account/instrument scope."
            )
        if broker_orders:
            reasons.append("Broker reports active Sandbox orders; resolve them first.")
        if not isinstance(evidence, dict):
            reasons.append("No matching persisted PRIMARY decision was found.")
        else:
            persisted_instrument_id = str(
                (bot_state or {}).get("instrument_id") or ""
            )
            if persisted_instrument_id and persisted_instrument_id != instrument_id:
                reasons.append(
                    "No matching persisted PRIMARY decision: instrument UID differs."
                )
            if str(evidence.get("strategy_id") or request.primary_strategy) != (
                request.primary_strategy
            ):
                reasons.append(
                    "No matching persisted PRIMARY decision: strategy_id differs."
                )
            if str(evidence.get("config_hash") or "") != request.primary_config_hash:
                reasons.append(
                    "No matching persisted PRIMARY decision: config_hash differs."
                )
            evidence_role = str(evidence.get("role") or "PRIMARY").upper()
            if evidence_role != "PRIMARY":
                reasons.append(
                    "No matching persisted PRIMARY decision: role is not PRIMARY."
                )
            if not str(evidence.get("candle_time") or ""):
                reasons.append(
                    "No matching persisted PRIMARY decision: candle_time is missing."
                )
            if evidence_target != current_lots:
                reasons.append(
                    "No matching persisted PRIMARY decision: target_lots does not "
                    "match the broker position "
                    f"({evidence_target!r} != {current_lots})."
                )

        return {
            "status": "ready" if not reasons else "blocked",
            "account_id": self.account_id,
            "instrument_id": instrument_id,
            "ticker": request.ticker.upper(),
            "class_code": request.class_code.upper(),
            "current_lots": current_lots,
            "scope_key": scope_key,
            "state_key": state_key,
            "active_primary": active,
            "requested_primary": desired,
            "decision_evidence": evidence,
            "evidence_target_lots": evidence_target,
            "local_pending_orders": local_pending,
            "broker_orders": broker_orders,
            "block_reasons": reasons,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def recover_ownership(
        self,
        request: OwnershipRecoveryRequest,
    ) -> dict[str, Any]:
        lock_path = Path(str(self.state_path) + ".lock")
        with InterProcessFileLock(lock_path, timeout_seconds=2.0):
            precheck = self.ownership_recovery_precheck(request)
            self._record(
                "OWNERSHIP_RECOVERY_PRECHECK",
                severity=(
                    "INFO" if precheck["status"] == "ready" else "ERROR"
                ),
                ticker=request.ticker,
                instrument_id=precheck.get("instrument_id"),
                strategy_id=request.primary_strategy,
                config_hash=request.primary_config_hash,
                payload=precheck,
            )
            if precheck["status"] != "ready":
                reasons = precheck.get("block_reasons") or [
                    "Ownership recovery precheck failed."
                ]
                raise RuntimeError("; ".join(str(item) for item in reasons))
            root = self._load_state_strict()
            scope = root.setdefault("execution_scopes", {}).setdefault(
                precheck["scope_key"], {}
            )
            desired = dict(precheck["requested_primary"])
            now = datetime.now(timezone.utc).isoformat()
            desired.update(
                {
                    "assigned_at": now,
                    "ownership_status": "RECOVERED_BY_OPERATOR",
                    "ownership_recovered": True,
                    "recovered_at": now,
                    "confirmed_lots": int(precheck["current_lots"]),
                    "recovery_id": str(uuid4()),
                    "recovery_evidence": {
                        "current_lots": precheck["current_lots"],
                        "target_lots": precheck["evidence_target_lots"],
                        "candle_time": (
                            precheck.get("decision_evidence") or {}
                        ).get("candle_time"),
                        "state_key": precheck["state_key"],
                    },
                }
            )
            scope["active_primary"] = desired
            scope.pop("migration_conflict", None)
            atomic_write_json(self.state_path, root, backup_existing=True)
            result = {
                **precheck,
                "status": "ownership_recovered",
                "active_primary": desired,
                "recovered_at": now,
            }
            self._record(
                "OWNERSHIP_RECOVERED",
                severity="WARNING",
                ticker=request.ticker,
                instrument_id=precheck.get("instrument_id"),
                strategy_id=request.primary_strategy,
                config_hash=request.primary_config_hash,
                payload=result,
            )
            return result

    def external_close_acknowledgement_precheck(
        self,
        request: ExternalCloseAcknowledgementRequest,
    ) -> dict[str, Any]:
        if str(request.account_id) != self.account_id:
            raise ValueError("Acknowledgement account does not match manager account.")
        if int(request.expected_target_lots) == 0:
            raise ValueError("expected_target_lots must describe a non-flat stale target.")
        if request.confirmation_text.strip().upper() != request.expected_phrase:
            raise ValueError(
                f"Confirmation mismatch; expected {request.expected_phrase}."
            )
        instrument = self.api.find_instrument(request.ticker, request.class_code)
        instrument_id = self.api.instrument_id(instrument)
        figi = str(instrument.get("figi") or "") if isinstance(instrument, Mapping) else ""
        portfolio = self.api.get_portfolio(self.account_id)
        current_lots = self.api.position_lots(portfolio, instrument)
        broker_orders = [
            order
            for order in self.api.get_orders(self.account_id)
            if _order_matches_instrument(order, instrument_id=instrument_id, figi=figi)
        ]
        root = self._load_state_strict()
        scope_key = _execution_scope_key(
            self.account_id,
            request.ticker,
            request.class_code,
        )
        scope = root.setdefault("execution_scopes", {}).setdefault(scope_key, {})
        active = scope.get("active_primary")
        local_pending = _collect_local_pending(
            root,
            self.account_id,
            scope_prefix=scope_key,
        )
        matching_states: list[dict[str, Any]] = []
        targets: list[int] = []
        for state_key, raw_state in root.get("bots", {}).items():
            if not str(state_key).startswith(scope_key + "|") or not isinstance(raw_state, Mapping):
                continue
            state_instrument = str(raw_state.get("instrument_id") or raw_state.get("figi") or "")
            if state_instrument not in {instrument_id, figi}:
                continue
            decisions = raw_state.get("last_strategy_decisions")
            primary = _select_primary_decision(
                decisions,
                active_primary=active,
                state_key=str(state_key),
            )
            target_lots = (
                _as_optional_int(primary.get("target_lots"))
                if isinstance(primary, Mapping)
                else None
            )
            if target_lots is not None:
                targets.append(target_lots)
            matching_states.append(
                {
                    "state_key": str(state_key),
                    "target_lots": target_lots,
                    "last_consumed_candle": raw_state.get("last_consumed_candle"),
                    "last_seen_candle": raw_state.get("last_seen_candle"),
                }
            )

        lifecycle = self._latest_execution_lifecycle(instrument_id)
        reasons: list[str] = []
        if current_lots != 0:
            reasons.append(
                f"Fresh broker snapshot is not flat ({current_lots} lot(s))."
            )
        if not isinstance(active, Mapping):
            reasons.append("No active PRIMARY ownership exists to clear.")
        if not matching_states:
            reasons.append("No matching robot state exists for the selected instrument.")
        if int(request.expected_target_lots) not in targets:
            reasons.append(
                "Persisted target does not match the operator expectation "
                f"({targets!r} vs {request.expected_target_lots})."
            )
        if local_pending:
            reasons.append("A local pending order exists in this instrument scope.")
        if broker_orders:
            reasons.append("Broker reports an active order for this instrument.")
        if not lifecycle.get("fully_accounted"):
            reasons.append(
                "Latest filled execution is not fully reconciled and risk-accounted."
            )

        return {
            "status": "ready" if not reasons else "blocked",
            "account_id": self.account_id,
            "instrument_id": instrument_id,
            "figi": figi,
            "ticker": request.ticker.upper(),
            "class_code": request.class_code.upper(),
            "current_lots": current_lots,
            "expected_target_lots": int(request.expected_target_lots),
            "scope_key": scope_key,
            "active_primary": active,
            "matching_states": matching_states,
            "local_pending_orders": local_pending,
            "broker_orders": broker_orders,
            "latest_execution_lifecycle": lifecycle,
            "block_reasons": reasons,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def acknowledge_external_close(
        self,
        request: ExternalCloseAcknowledgementRequest,
    ) -> dict[str, Any]:
        lock_path = Path(str(self.state_path) + ".lock")
        with InterProcessFileLock(lock_path, timeout_seconds=2.0):
            precheck = self.external_close_acknowledgement_precheck(request)
            self._record(
                "EXTERNAL_CLOSE_ACK_PRECHECK",
                severity="INFO" if precheck["status"] == "ready" else "ERROR",
                ticker=request.ticker,
                instrument_id=precheck.get("instrument_id"),
                payload=precheck,
            )
            if precheck["status"] != "ready":
                raise RuntimeError(
                    "; ".join(
                        str(item)
                        for item in precheck.get("block_reasons")
                        or ["External close acknowledgement precheck failed."]
                    )
                )

            root = self._load_state_strict()
            scope = root.setdefault("execution_scopes", {}).setdefault(
                precheck["scope_key"], {}
            )
            previous_owner = scope.pop("active_primary", None)
            acknowledgement_id = str(uuid4())
            now = datetime.now(timezone.utc).isoformat()
            state_changes: list[dict[str, Any]] = []
            for item in precheck["matching_states"]:
                state_key = str(item["state_key"])
                bot_state = root.setdefault("bots", {}).get(state_key)
                if not isinstance(bot_state, dict):
                    continue
                previous_decisions = bot_state.get("last_strategy_decisions")
                effective_through = (
                    bot_state.get("last_consumed_candle")
                    or bot_state.get("last_seen_candle")
                )
                bot_state["external_close_acknowledgement"] = {
                    "acknowledgement_id": acknowledgement_id,
                    "acknowledged_at": now,
                    "effective_through_candle": effective_through,
                    "previous_target_lots": item.get("target_lots"),
                    "previous_active_primary": previous_owner,
                    "source": "GUI_OPERATOR",
                }
                if isinstance(previous_decisions, Mapping):
                    bot_state["acknowledged_strategy_decisions"] = dict(previous_decisions)
                bot_state["last_strategy_decisions"] = {}
                bot_state["last_strategy_comparison"] = None
                bot_state["last_confirmed_current_lots"] = 0
                bot_state["last_confirmed_target_lots"] = 0
                bot_state["last_risk_decision"] = {
                    "status": "EXTERNAL_CLOSE_ACKNOWLEDGED",
                    "approved_target_lots": 0,
                    "strategy_target_lots": 0,
                    "acknowledgement_id": acknowledgement_id,
                    "acknowledged_at": now,
                }
                state_changes.append(
                    {
                        "state_key": state_key,
                        "effective_through_candle": effective_through,
                        "previous_target_lots": item.get("target_lots"),
                    }
                )
            scope["last_external_close_acknowledgement"] = {
                "acknowledgement_id": acknowledgement_id,
                "acknowledged_at": now,
                "instrument_id": precheck["instrument_id"],
                "previous_active_primary": previous_owner,
                "previous_target_lots": int(request.expected_target_lots),
                "broker_actual_lots": 0,
                "confirmation": request.expected_phrase,
            }
            atomic_write_json(self.state_path, root, backup_existing=True)
            result = {
                **precheck,
                "status": "external_close_acknowledged",
                "acknowledgement_id": acknowledgement_id,
                "acknowledged_at": now,
                "previous_active_primary": previous_owner,
                "state_changes": state_changes,
                "risk_state_changed": False,
                "execution_history_changed": False,
            }
            common_payload = {
                "acknowledgement_id": acknowledgement_id,
                "old_target_lots": int(request.expected_target_lots),
                "new_target_lots": 0,
                "broker_actual_lots": 0,
                "previous_active_primary": previous_owner,
                "latest_execution_lifecycle": precheck["latest_execution_lifecycle"],
                "state_changes": state_changes,
                "source": "GUI_OPERATOR",
            }
            self._record(
                "PORTFOLIO_TARGET_CLEARED",
                severity="WARNING",
                ticker=request.ticker,
                instrument_id=precheck.get("instrument_id"),
                payload=common_payload,
            )
            self._record(
                "EXTERNAL_CLOSE_ACKNOWLEDGED",
                severity="WARNING",
                ticker=request.ticker,
                instrument_id=precheck.get("instrument_id"),
                payload=common_payload,
            )
            return result

    def _latest_execution_lifecycle(self, instrument_id: str) -> dict[str, Any]:
        rows = self.journal.recent(
            limit=5_000,
            account_id=self.account_id,
        )
        filled = next(
            (
                row
                for row in rows
                if str(row.get("instrument_id") or "") == instrument_id
                and str(row.get("event_type") or "").upper() == "FILLED"
                and str(row.get("order_id") or "")
            ),
            None,
        )
        if filled is None:
            return {
                "fully_accounted": False,
                "reason": "No FILLED event found for this instrument.",
            }
        order_id = str(filled.get("order_id") or "")
        events = {
            str(row.get("event_type") or "").upper()
            for row in rows
            if str(row.get("order_id") or "") == order_id
        }
        required = {
            "FILLED",
            "PORTFOLIO_RECONCILED",
            "EXECUTION_RECORDED",
            "RISK_ACCOUNTED",
        }
        missing = sorted(required - events)
        return {
            "fully_accounted": not missing,
            "order_id": order_id,
            "filled_at": filled.get("timestamp_utc"),
            "events": sorted(events),
            "missing_events": missing,
        }

    def _resolve_instrument_metadata(
        self,
        position: Mapping[str, Any],
    ) -> dict[str, Any]:
        direct = {
            key: position.get(key)
            for key in (
                "instrumentUid",
                "positionUid",
                "figi",
                "ticker",
                "classCode",
                "instrumentType",
            )
            if position.get(key) is not None
        }
        if position.get("ticker") and position.get("classCode"):
            return direct
        resolver = getattr(self.api, "get_instrument_by_id", None)
        if callable(resolver):
            for key, id_type in (
                ("instrumentUid", "INSTRUMENT_ID_TYPE_UID"),
                ("positionUid", "INSTRUMENT_ID_TYPE_POSITION_UID"),
                ("figi", "INSTRUMENT_ID_TYPE_FIGI"),
                ("instrumentId", "INSTRUMENT_ID_TYPE_ID"),
            ):
                value = position.get(key)
                if not value:
                    continue
                try:
                    resolved = resolver(str(value), id_type=id_type)
                    if isinstance(resolved, dict) and resolved:
                        return dict(resolved)
                except Exception as exc:
                    # A metadata error must not hide the actual broker position.
                    logger.debug(
                        "Instrument metadata lookup failed: key=%s value=%s error=%s",
                        key,
                        value,
                        exc,
                    )
        return direct

    def _load_state_for_view(self) -> tuple[dict[str, Any], str, list[str]]:
        if not self.state_path.exists():
            return {
                "version": 6,
                "bots": {},
                "execution_scopes": {},
            }, "MISSING", [
                "Local robot_state.json is missing; open positions cannot be attributed."
            ]
        try:
            root = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(root, dict):
                raise ValueError("state root must be an object")
            return root, "OK", []
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return {
                "version": 6,
                "bots": {},
                "execution_scopes": {},
            }, "UNREADABLE", [f"Local state is unreadable: {exc}"]

    def _load_state_strict(self) -> dict[str, Any]:
        root, status, warnings = self._load_state_for_view()
        if status != "OK":
            raise RuntimeError("; ".join(warnings))
        root.setdefault("bots", {})
        root.setdefault("execution_scopes", {})
        return root

    def _record(
        self,
        event_type: str,
        *,
        severity: str,
        payload: dict[str, Any],
        ticker: str | None = None,
        instrument_id: str | None = None,
        order_id: str | None = None,
        strategy_id: str | None = None,
        config_hash: str | None = None,
    ) -> None:
        self.journal.record(
            JournalEvent(
                category="portfolio",
                event_type=event_type,
                severity=severity,
                account_id=self.account_id,
                instrument_id=instrument_id,
                ticker=ticker,
                order_id=order_id,
                strategy_id=strategy_id,
                config_hash=config_hash,
                payload=payload,
            )
        )


# Backward-compatible descriptive alias for tests and future service split.
PortfolioSnapshotService = PortfolioManager


def _build_local_state_index(
    root: Mapping[str, Any],
    account_id: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    priorities: dict[str, tuple[int, str]] = {}
    scopes = root.get("execution_scopes", {}) if isinstance(root, Mapping) else {}
    bots = root.get("bots", {}) if isinstance(root, Mapping) else {}
    iterable = bots.items() if isinstance(bots, Mapping) else []
    for key, bot_state in iterable:
        if (
            not isinstance(bot_state, Mapping)
            or not str(key).startswith(str(account_id) + "|")
        ):
            continue
        parts = str(key).split("|")
        ticker_scope = parts[1] if len(parts) > 1 else ""
        ticker, _, class_code = ticker_scope.partition("_")
        candle_interval = parts[2] if len(parts) > 2 else ""
        instrument_id = str(bot_state.get("instrument_id") or "")
        scope_key = "|".join([str(account_id), ticker_scope])
        scope = scopes.get(scope_key, {}) if isinstance(scopes, Mapping) else {}
        active_primary = (
            scope.get("active_primary") if isinstance(scope, Mapping) else None
        )
        decisions = bot_state.get("last_strategy_decisions", {})
        primary_decision = _select_primary_decision(
            decisions,
            active_primary=active_primary,
            state_key=str(key),
        )
        owner_match = _state_matches_active_owner(
            state_key=str(key),
            candle_interval=candle_interval,
            decision=primary_decision,
            active_primary=active_primary,
        )
        if isinstance(active_primary, Mapping) and not owner_match:
            # A stale state from another timeframe/configuration must not supply
            # the target for the currently attributed broker position.
            primary_decision = None
        pending = bot_state.get("pending_order")
        pending_ids = (
            (str(pending.get("order_id")),)
            if isinstance(pending, Mapping) and pending.get("order_id")
            else ()
        )
        local = {
            "ticker": ticker,
            "class_code": class_code,
            "target_lots": (primary_decision or {}).get("target_lots"),
            "last_candle_time": (primary_decision or {}).get("candle_time"),
            "active_primary": active_primary,
            "local_pending_order_ids": pending_ids,
            "state_key": key,
            "owner_state_match": owner_match,
        }
        # Prefer the bot state that exactly matches the active PRIMARY owner.
        # Without an owner, prefer the state with a usable decision and the
        # newest candle timestamp. Dict insertion order must not decide which
        # timeframe appears in Portfolio View.
        priority = 100 if owner_match else (50 if primary_decision else 10)
        candle_sort = str((primary_decision or {}).get("candle_time") or "")
        score = (priority, candle_sort)
        if instrument_id and score > priorities.get(instrument_id, (-1, "")):
            result[instrument_id] = local
            priorities[instrument_id] = score
        figi = str(bot_state.get("figi") or "")
        if figi and score > priorities.get(figi, (-1, "")):
            result[figi] = local
            priorities[figi] = score
    return result


def _select_primary_decision(
    decisions: Any,
    *,
    active_primary: Any,
    state_key: str,
) -> Mapping[str, Any] | None:
    """Return the persisted PRIMARY decision, including legacy v3.5 states.

    Current decision payloads contain ``role=PRIMARY``. Earlier state files and
    some recovery fixtures do not. In those cases we first use the active owner
    identity, then the ``primary:<strategy>:<hash>`` component of the state key,
    and only as a last unambiguous fallback accept a single decision entry.
    """

    if not isinstance(decisions, Mapping):
        return None

    candidates = [
        raw for raw in decisions.values() if isinstance(raw, Mapping)
    ]
    for raw in candidates:
        if str(raw.get("role") or "").upper() == "PRIMARY":
            return raw

    if isinstance(active_primary, Mapping):
        strategy_id = str(active_primary.get("strategy_id") or "")
        config_hash = str(active_primary.get("config_hash") or "")
        raw = decisions.get(strategy_id)
        if isinstance(raw, Mapping) and (
            not config_hash
            or str(raw.get("config_hash") or "") == config_hash
        ):
            return raw

    marker = "|primary:"
    if marker in state_key:
        suffix = state_key.split(marker, 1)[1]
        strategy_id, _, hash_prefix = suffix.partition(":")
        raw = decisions.get(strategy_id)
        if isinstance(raw, Mapping) and (
            not hash_prefix
            or str(raw.get("config_hash") or "").startswith(hash_prefix)
        ):
            return raw

    return candidates[0] if len(candidates) == 1 else None


def _state_matches_active_owner(
    *,
    state_key: str,
    candle_interval: str,
    decision: Mapping[str, Any] | None,
    active_primary: Any,
) -> bool:
    if not isinstance(active_primary, Mapping):
        return False
    strategy_id = str(active_primary.get("strategy_id") or "")
    config_hash = str(active_primary.get("config_hash") or "")
    owner_interval = str(active_primary.get("candle_interval") or "")
    if owner_interval and candle_interval != owner_interval:
        return False
    if decision is not None:
        if strategy_id and str(decision.get("strategy_id") or "") != strategy_id:
            return False
        if config_hash and str(decision.get("config_hash") or "") != config_hash:
            return False
        return True
    marker = "|primary:"
    if marker not in state_key:
        return False
    suffix = state_key.split(marker, 1)[1]
    state_strategy, _, hash_prefix = suffix.partition(":")
    return (
        (not strategy_id or state_strategy == strategy_id)
        and (not config_hash or config_hash.startswith(hash_prefix))
    )


def _collect_local_pending(
    root: Mapping[str, Any],
    account_id: str,
    *,
    scope_prefix: str | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    bots = root.get("bots", {}) if isinstance(root, Mapping) else {}
    if not isinstance(bots, Mapping):
        return result
    prefix = (
        scope_prefix + "|" if scope_prefix else str(account_id) + "|"
    )
    for key, state in bots.items():
        if not str(key).startswith(prefix) or not isinstance(state, Mapping):
            continue
        pending = state.get("pending_order")
        if isinstance(pending, Mapping):
            result.append({"state_key": str(key), **dict(pending)})
    return result




def _order_matches_instrument(
    order: Mapping[str, Any],
    *,
    instrument_id: str,
    figi: str,
) -> bool:
    values = {
        str(order.get("instrumentUid") or ""),
        str(order.get("instrumentId") or ""),
        str(order.get("figi") or ""),
    }
    return bool({str(instrument_id), str(figi)} & {value for value in values if value})

def _index_broker_orders(
    orders: tuple[dict[str, Any], ...],
) -> dict[str, tuple[str, ...]]:
    mutable: dict[str, list[str]] = {}
    for order in orders:
        if not isinstance(order, dict):
            continue
        instrument_ids = {
            str(order.get("instrumentUid") or ""),
            str(order.get("figi") or ""),
        }
        order_id = str(order.get("orderRequestId") or order.get("orderId") or "")
        if not order_id:
            continue
        for instrument_id in instrument_ids:
            if instrument_id:
                mutable.setdefault(instrument_id, []).append(order_id)
    return {key: tuple(values) for key, values in mutable.items()}


def _execution_scope_key(
    account_id: str,
    ticker: str,
    class_code: str,
) -> str:
    return "|".join(
        [str(account_id), f"{ticker.upper()}_{class_code.upper()}"]
    )


def _bot_state_key(
    account_id: str,
    request: OwnershipRecoveryRequest,
) -> str:
    return "|".join(
        [
            str(account_id),
            f"{request.ticker.upper()}_{request.class_code.upper()}",
            request.candle_interval,
            f"primary:{request.primary_strategy}:{request.primary_config_hash[:16]}",
        ]
    )


def _same_owner(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        str(left.get(key) or "") == str(right.get(key) or "")
        for key in ("strategy_id", "config_hash", "candle_interval")
    )


def _optional_quotation(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return quotation_to_float(dict(value))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money_currency(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("currency") or "")
    return ""


def _money_list(values: Any) -> dict[str, float]:
    result: dict[str, float] = {}
    if not isinstance(values, list):
        return result
    for raw in values:
        if not isinstance(raw, Mapping):
            continue
        currency = str(raw.get("currency") or "").lower() or "unknown"
        result[currency] = result.get(currency, 0.0) + quotation_to_float(dict(raw))
    return result


def _as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
