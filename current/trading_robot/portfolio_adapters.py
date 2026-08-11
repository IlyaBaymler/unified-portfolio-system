from __future__ import annotations

"""Broker and canonical runtime adapters for v3.7-alpha3."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .portfolio import (
    PortfolioSnapshot as LegacyPortfolioSnapshot,
    _select_primary_decision,
    _state_matches_active_owner,
)
from .tbank_sandbox import quotation_to_float

from .portfolio_model import (
    AccountState,
    CashBalance,
    PendingOrderState,
    PendingOrderStatus,
    PortfolioTarget,
    PositionOwnership,
    PortfolioState,
)


class PortfolioAdapterError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BrokerPositionRecord:
    instrument_id: str
    figi: str
    ticker: str
    class_code: str
    asset_type: str
    currency: str
    quantity: float
    actual_lots: int
    average_price: float | None
    current_price: float | None
    market_value: float | None
    expected_yield: float | None
    pending_orders: tuple[PendingOrderState, ...]


@dataclass(frozen=True, slots=True)
class BrokerPortfolioRecord:
    account: AccountState
    snapshot_at: str
    positions: tuple[BrokerPositionRecord, ...]
    source_status: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimePositionRecord:
    instrument_id: str
    figi: str
    ticker: str
    class_code: str
    target: PortfolioTarget | None
    ownership: PositionOwnership | None
    pending_orders: tuple[PendingOrderState, ...]
    last_candle_time: str | None
    state_key: str


@dataclass(frozen=True, slots=True)
class RuntimePortfolioRecord:
    account_id: str
    positions: tuple[RuntimePositionRecord, ...]
    warnings: tuple[str, ...]
    state_status: str


class BrokerPortfolioAdapter:
    """Convert the accepted v3.6 broker projection into domain input.

    v3.7-alpha3 intentionally keeps the stable T-Invest request path inside the
    legacy projection.  This adapter is the only transitional boundary that
    knows its shape; PortfolioState and the GUI never receive raw API payloads.
    """

    @staticmethod
    def from_legacy_snapshot(snapshot: LegacyPortfolioSnapshot) -> BrokerPortfolioRecord:
        cash_balances: list[CashBalance] = []
        currencies = set(snapshot.cash_by_currency) | set(snapshot.blocked_by_currency)
        for currency in sorted(currencies):
            cash_balances.append(
                CashBalance(
                    currency=currency,
                    available=float(snapshot.cash_by_currency.get(currency, 0.0)),
                    blocked=float(snapshot.blocked_by_currency.get(currency, 0.0)),
                )
            )

        order_map: dict[str, list[PendingOrderState]] = {}
        for raw in snapshot.broker_orders:
            if not isinstance(raw, Mapping):
                continue
            instrument_id = str(
                raw.get("instrumentUid")
                or raw.get("instrumentId")
                or raw.get("figi")
                or ""
            ).strip()
            request_id = str(
                raw.get("orderRequestId") or raw.get("orderId") or ""
            ).strip()
            if not instrument_id or not request_id:
                continue
            requested = _int_value(raw.get("lotsRequested"), raw.get("quantity"), default=0)
            executed = _int_value(raw.get("lotsExecuted"), raw.get("executedLots"), default=0)
            status = _pending_status(raw.get("executionReportStatus") or raw.get("status"))
            direction = _direction(raw.get("direction"))
            order_map.setdefault(instrument_id, []).append(
                PendingOrderState(
                    order_request_id=request_id,
                    broker_order_id=str(raw.get("orderId") or "") or None,
                    instrument_id=instrument_id,
                    direction=direction,
                    requested_lots=max(requested, executed),
                    executed_lots=executed,
                    status=status,
                    uncertain=(status is PendingOrderStatus.UNKNOWN),
                    source="BROKER",
                )
            )

        positions: list[BrokerPositionRecord] = []
        for row in snapshot.positions:
            pending = list(order_map.get(row.instrument_id, ()))
            if row.figi and row.figi != row.instrument_id:
                pending.extend(order_map.get(row.figi, ()))
            # If the API projection exposed only IDs, retain them as uncertain
            # broker orders rather than silently discarding the information.
            existing_ids = {item.order_request_id for item in pending}
            for order_id in row.broker_pending_order_ids:
                if order_id in existing_ids:
                    continue
                pending.append(
                    PendingOrderState(
                        order_request_id=order_id,
                        instrument_id=row.instrument_id,
                        direction="UNKNOWN",
                        requested_lots=0,
                        executed_lots=0,
                        status=PendingOrderStatus.UNKNOWN,
                        uncertain=True,
                        source="BROKER",
                    )
                )
            positions.append(
                BrokerPositionRecord(
                    instrument_id=row.instrument_id,
                    figi=row.figi,
                    ticker=row.ticker,
                    class_code=row.class_code,
                    asset_type=row.asset_type,
                    currency=row.currency,
                    quantity=row.quantity,
                    actual_lots=row.quantity_lots,
                    average_price=row.average_price,
                    current_price=row.current_price,
                    market_value=row.market_value,
                    expected_yield=row.expected_yield,
                    pending_orders=tuple(_dedupe_orders(pending)),
                )
            )
        represented = {item.instrument_id for item in positions}
        for instrument_id, pending in order_map.items():
            if instrument_id in represented:
                continue
            positions.append(
                BrokerPositionRecord(
                    instrument_id=instrument_id,
                    figi="",
                    ticker=instrument_id[:12],
                    class_code="",
                    asset_type="unknown",
                    currency="",
                    quantity=0.0,
                    actual_lots=0,
                    average_price=None,
                    current_price=None,
                    market_value=None,
                    expected_yield=None,
                    pending_orders=tuple(_dedupe_orders(pending)),
                )
            )
        return BrokerPortfolioRecord(
            account=AccountState(
                account_id=snapshot.account_id,
                total_value=snapshot.total_value,
                securities_value=snapshot.securities_value,
                expected_yield=snapshot.expected_yield,
                cash_balances=tuple(cash_balances),
            ),
            snapshot_at=snapshot.checked_at,
            positions=tuple(sorted(positions, key=lambda item: (item.ticker, item.instrument_id))),
            source_status=snapshot.state_status,
            warnings=tuple(snapshot.warnings),
        )



    @staticmethod
    def from_api_portfolio(
        portfolio: Mapping[str, Any],
        *,
        account_id: str,
        instrument_metadata: Mapping[str, Any] | None = None,
        broker_orders: Iterable[Mapping[str, Any]] = (),
        snapshot_at: str | None = None,
    ) -> BrokerPortfolioRecord:
        """Convert a current GetPortfolio payload without a second API round-trip.

        Alpha2 uses this adapter inside the execution cycle so Risk and
        Execution are authorized against the same broker observation.  Missing
        display metadata is acceptable; instrument IDs, lots and pending-order
        state remain authoritative.
        """

        metadata = dict(instrument_metadata or {})
        known_id = str(
            metadata.get("uid")
            or metadata.get("instrumentUid")
            or metadata.get("instrumentId")
            or metadata.get("figi")
            or ""
        ).strip()
        order_map: dict[str, list[PendingOrderState]] = {}
        for raw in broker_orders:
            if not isinstance(raw, Mapping):
                continue
            instrument_id = str(
                raw.get("instrumentUid")
                or raw.get("instrumentId")
                or raw.get("figi")
                or ""
            ).strip()
            request_id = str(
                raw.get("orderRequestId") or raw.get("orderId") or ""
            ).strip()
            if not instrument_id or not request_id:
                continue
            requested = _int_value(
                raw.get("lotsRequested"), raw.get("quantity"), default=0
            )
            executed = _int_value(
                raw.get("lotsExecuted"), raw.get("executedLots"), default=0
            )
            status = _pending_status(
                raw.get("executionReportStatus") or raw.get("status")
            )
            order_map.setdefault(instrument_id, []).append(
                PendingOrderState(
                    order_request_id=request_id,
                    broker_order_id=str(raw.get("orderId") or "") or None,
                    instrument_id=instrument_id,
                    direction=_direction(raw.get("direction")),
                    requested_lots=max(requested, executed),
                    executed_lots=executed,
                    status=status,
                    uncertain=(status is PendingOrderStatus.UNKNOWN),
                    source="BROKER",
                )
            )

        positions: list[BrokerPositionRecord] = []
        represented: set[str] = set()
        raw_positions = portfolio.get("positions")
        raw_positions = raw_positions if isinstance(raw_positions, list) else []
        for raw in raw_positions:
            if not isinstance(raw, Mapping):
                continue
            asset_type = str(
                raw.get("instrumentType") or raw.get("assetType") or "unknown"
            ).strip().lower()
            if asset_type == "currency":
                continue
            instrument_id = str(
                raw.get("instrumentUid")
                or raw.get("instrumentId")
                or raw.get("figi")
                or raw.get("positionUid")
                or ""
            ).strip()
            if not instrument_id:
                continue
            row_metadata = metadata if instrument_id == known_id else {}
            quantity = quotation_to_float(raw.get("quantity"))
            lots_value = raw.get("quantityLots")
            if isinstance(lots_value, Mapping):
                actual_lots = int(round(quotation_to_float(lots_value)))
            elif lots_value is not None:
                actual_lots = _int_value(lots_value, default=0)
            else:
                actual_lots = int(round(quantity))
            pending = list(order_map.get(instrument_id, ()))
            figi = str(row_metadata.get("figi") or raw.get("figi") or "")
            if figi and figi != instrument_id:
                pending.extend(order_map.get(figi, ()))
            average_price = _quotation_optional(
                raw.get("averagePositionPrice") or raw.get("averagePrice")
            )
            current_price = _quotation_optional(
                raw.get("currentPrice") or raw.get("lastPrice")
            )
            market_value = _quotation_optional(raw.get("marketValue"))
            if market_value is None and current_price is not None:
                market_value = float(current_price) * float(quantity)
            positions.append(
                BrokerPositionRecord(
                    instrument_id=instrument_id,
                    figi=figi,
                    ticker=str(
                        row_metadata.get("ticker")
                        or raw.get("ticker")
                        or figi
                        or instrument_id[:12]
                    ),
                    class_code=str(
                        row_metadata.get("classCode")
                        or raw.get("classCode")
                        or ""
                    ),
                    asset_type=asset_type or "unknown",
                    currency=str(
                        (raw.get("averagePositionPrice") or {}).get("currency")
                        if isinstance(raw.get("averagePositionPrice"), Mapping)
                        else raw.get("currency") or ""
                    ).lower(),
                    quantity=float(quantity),
                    actual_lots=int(actual_lots),
                    average_price=average_price,
                    current_price=current_price,
                    market_value=market_value,
                    expected_yield=_quotation_optional(raw.get("expectedYield")),
                    pending_orders=tuple(_dedupe_orders(pending)),
                )
            )
            represented.add(instrument_id)

        for instrument_id, pending in order_map.items():
            if instrument_id in represented:
                continue
            row_metadata = metadata if instrument_id == known_id else {}
            positions.append(
                BrokerPositionRecord(
                    instrument_id=instrument_id,
                    figi=str(row_metadata.get("figi") or ""),
                    ticker=str(row_metadata.get("ticker") or instrument_id[:12]),
                    class_code=str(row_metadata.get("classCode") or ""),
                    asset_type="unknown",
                    currency="",
                    quantity=0.0,
                    actual_lots=0,
                    average_price=None,
                    current_price=None,
                    market_value=None,
                    expected_yield=None,
                    pending_orders=tuple(_dedupe_orders(list(pending))),
                )
            )

        cash_rub = _quotation_optional(portfolio.get("totalAmountCurrencies"))
        cash_balances = (
            (CashBalance(currency="rub", available=float(cash_rub)),)
            if cash_rub is not None
            else ()
        )
        return BrokerPortfolioRecord(
            account=AccountState(
                account_id=str(account_id),
                total_value=_quotation_optional(portfolio.get("totalAmountPortfolio")),
                securities_value=_sum_optional_totals(
                    portfolio,
                    (
                        "totalAmountShares",
                        "totalAmountBonds",
                        "totalAmountEtf",
                        "totalAmountFutures",
                        "totalAmountOptions",
                        "totalAmountSp",
                    ),
                ),
                expected_yield=_quotation_optional(portfolio.get("expectedYield")),
                cash_balances=cash_balances,
            ),
            snapshot_at=snapshot_at or datetime.now(timezone.utc).isoformat(),
            positions=tuple(
                sorted(positions, key=lambda item: (item.ticker, item.instrument_id))
            ),
            source_status="BROKER_API",
            warnings=(),
        )


class RuntimePortfolioAdapter:
    """Read target/ownership/pending state without depending on the GUI."""

    @staticmethod
    def from_robot_state(
        path: str | Path,
        *,
        account_id: str,
    ) -> RuntimePortfolioRecord:
        target = Path(path)
        if not target.exists():
            return RuntimePortfolioRecord(
                account_id=str(account_id),
                positions=(),
                warnings=("robot_state.json is missing.",),
                state_status="MISSING",
            )
        try:
            root = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PortfolioAdapterError(f"Cannot read robot state: {exc}") from exc
        if not isinstance(root, Mapping):
            raise PortfolioAdapterError("robot_state.json root must be an object.")
        return RuntimePortfolioAdapter.from_mapping(
            root,
            account_id=account_id,
        )

    @staticmethod
    def from_mapping(
        root: Mapping[str, Any],
        *,
        account_id: str,
    ) -> RuntimePortfolioRecord:
        """Build a runtime projection from an already locked in-memory state."""

        if not isinstance(root, Mapping):
            raise PortfolioAdapterError("robot state root must be an object.")
        bots = root.get("bots")
        scopes = root.get("execution_scopes")
        bots = bots if isinstance(bots, Mapping) else {}
        scopes = scopes if isinstance(scopes, Mapping) else {}

        selected: dict[str, tuple[tuple[int, str], RuntimePositionRecord]] = {}
        prefix = str(account_id) + "|"
        for state_key, raw_state in bots.items():
            if not str(state_key).startswith(prefix) or not isinstance(raw_state, Mapping):
                continue
            parts = str(state_key).split("|")
            ticker_scope = parts[1] if len(parts) > 1 else ""
            ticker, _, class_code = ticker_scope.partition("_")
            interval = parts[2] if len(parts) > 2 else ""
            instrument_id = str(
                raw_state.get("instrument_id") or raw_state.get("figi") or ""
            ).strip()
            if not instrument_id:
                continue
            figi = str(raw_state.get("figi") or "").strip()
            scope_key = "|".join([str(account_id), ticker_scope])
            scope = scopes.get(scope_key)
            scope = scope if isinstance(scope, Mapping) else {}
            active_primary = scope.get("active_primary")
            decisions = raw_state.get("last_strategy_decisions")
            decision = _select_primary_decision(
                decisions,
                active_primary=active_primary,
                state_key=str(state_key),
            )
            acknowledgement = raw_state.get("external_close_acknowledgement")
            acknowledgement = (
                acknowledgement if isinstance(acknowledgement, Mapping) else None
            )
            acknowledgement_suppresses_runtime = False
            if acknowledgement is not None:
                effective_through = str(
                    acknowledgement.get("effective_through_candle") or ""
                )
                decision_candle = (
                    str(decision.get("candle_time") or "")
                    if isinstance(decision, Mapping)
                    else ""
                )
                if effective_through and (
                    not decision_candle or decision_candle <= effective_through
                ):
                    acknowledgement_suppresses_runtime = True
                    decision = None
                    active_primary = None
            owner_match = _state_matches_active_owner(
                state_key=str(state_key),
                candle_interval=interval,
                decision=decision,
                active_primary=active_primary,
            )
            if isinstance(active_primary, Mapping) and not owner_match:
                decision = None

            # Canonical state represents the last broker-confirmed target, not
            # the newest unexecuted StrategyDecision.  Older alpha1/runtime
            # fixtures may not contain the confirmed field yet, so the latest
            # accepted PRIMARY decision remains a migration fallback.
            confirmed_target = raw_state.get("last_confirmed_target_lots")
            target_state: PortfolioTarget | None = None
            if confirmed_target is not None:
                target_state = PortfolioTarget(
                    instrument_id=instrument_id,
                    target_lots=int(confirmed_target),
                    strategy_id=(
                        str((active_primary or {}).get("strategy_id") or "") or None
                        if isinstance(active_primary, Mapping)
                        else None
                    ),
                    config_hash=(
                        str((active_primary or {}).get("config_hash") or "") or None
                        if isinstance(active_primary, Mapping)
                        else None
                    ),
                    candle_time=(
                        str(raw_state.get("last_consumed_candle") or "") or None
                    ),
                )
            elif isinstance(decision, Mapping) and decision.get("target_lots") is not None:
                target_state = PortfolioTarget(
                    instrument_id=instrument_id,
                    target_lots=int(decision.get("target_lots") or 0),
                    strategy_id=str(decision.get("strategy_id") or "") or None,
                    config_hash=str(decision.get("config_hash") or "") or None,
                    candle_time=str(decision.get("candle_time") or "") or None,
                )

            ownership: PositionOwnership | None = None
            if isinstance(active_primary, Mapping):
                strategy_id = str(active_primary.get("strategy_id") or "").strip()
                config_hash = str(active_primary.get("config_hash") or "").strip()
                owner_interval = str(
                    active_primary.get("candle_interval") or interval
                ).strip()
                if strategy_id and config_hash and owner_interval:
                    ownership = PositionOwnership(
                        strategy_id=strategy_id,
                        config_hash=config_hash,
                        candle_interval=owner_interval,
                        source="RUNTIME",
                        attributed_at=str(active_primary.get("attributed_at") or "") or None,
                    )

            pending: list[PendingOrderState] = []
            raw_pending = raw_state.get("pending_order")
            if isinstance(raw_pending, Mapping):
                request_id = str(
                    raw_pending.get("order_id")
                    or raw_pending.get("order_request_id")
                    or raw_pending.get("orderRequestId")
                    or ""
                ).strip()
                if request_id:
                    requested = _int_value(
                        raw_pending.get("requested_lots"),
                        raw_pending.get("lots"),
                        default=0,
                    )
                    executed = _int_value(
                        raw_pending.get("executed_lots"),
                        raw_pending.get("lots_executed"),
                        default=0,
                    )
                    lifecycle = str(raw_pending.get("lifecycle_state") or "UNKNOWN")
                    pending_status = _pending_status(lifecycle)
                    time_in_force = str(
                        raw_pending.get("time_in_force")
                        or raw_pending.get("timeInForce")
                        or ""
                    ).upper()
                    # A partial fill under fill-and-kill / immediate-or-cancel is
                    # terminal: the unfilled remainder no longer exists at the
                    # broker.  Keep the original requested/executed counters for
                    # audit, but do not expose it as an active pending order.
                    if (
                        pending_status is PendingOrderStatus.PARTIALLY_FILLED
                        and executed > 0
                        and time_in_force
                        in {
                            "FILL_AND_KILL",
                            "FILL_OR_KILL",
                            "IMMEDIATE_OR_CANCEL",
                            "IOC",
                            "FOK",
                        }
                    ):
                        pending_status = PendingOrderStatus.FILLED
                    pending.append(
                        PendingOrderState(
                            order_request_id=request_id,
                            broker_order_id=str(raw_pending.get("broker_order_id") or "") or None,
                            instrument_id=instrument_id,
                            direction=_direction(
                                raw_pending.get("direction") or raw_pending.get("action")
                            ),
                            requested_lots=max(requested, executed),
                            executed_lots=executed,
                            status=pending_status,
                            uncertain=lifecycle.upper()
                            in {
                                "ORDER_SUBMITTED",
                                "UNKNOWN_SUBMIT_STATE",
                                "SUBMISSION_UNCERTAIN",
                                "UNKNOWN",
                            },
                            source="LOCAL",
                        )
                    )

            record = RuntimePositionRecord(
                instrument_id=instrument_id,
                figi=figi,
                ticker=ticker,
                class_code=class_code,
                target=target_state,
                ownership=ownership,
                pending_orders=tuple(pending),
                last_candle_time=(
                    str((decision or {}).get("candle_time") or "") or None
                    if isinstance(decision, Mapping)
                    else None
                ),
                state_key=str(state_key),
            )
            priority = 100 if owner_match else (50 if decision is not None else 10)
            candle_sort = record.last_candle_time or ""
            score = (priority, candle_sort)
            existing = selected.get(instrument_id)
            if existing is None or score > existing[0]:
                selected[instrument_id] = (score, record)

        return RuntimePortfolioRecord(
            account_id=str(account_id),
            positions=tuple(
                sorted(
                    (item[1] for item in selected.values()),
                    key=lambda item: (item.ticker, item.instrument_id),
                )
            ),
            warnings=(),
            state_status="OK",
        )


    @staticmethod
    def from_portfolio_state(state: PortfolioState) -> RuntimePortfolioRecord:
        """Build the authorization/runtime projection from canonical state only."""

        return RuntimePortfolioRecord(
            account_id=state.account_id,
            positions=tuple(
                RuntimePositionRecord(
                    instrument_id=item.instrument_id,
                    figi=item.figi,
                    ticker=item.ticker,
                    class_code=item.class_code,
                    target=item.target,
                    ownership=item.ownership,
                    pending_orders=item.pending_orders,
                    last_candle_time=item.last_candle_time,
                    state_key=f"canonical:{item.instrument_id}",
                )
                for item in state.positions
            ),
            warnings=tuple(state.warnings),
            state_status=state.state_status,
        )


def _dedupe_orders(items: list[PendingOrderState]) -> list[PendingOrderState]:
    result: list[PendingOrderState] = []
    seen: set[str] = set()
    for item in items:
        if item.order_request_id in seen:
            continue
        seen.add(item.order_request_id)
        result.append(item)
    return result


def _direction(value: Any) -> str:
    token = str(value or "UNKNOWN").upper()
    if "BUY" in token:
        return "BUY"
    if "SELL" in token:
        return "SELL"
    return "UNKNOWN"


def _pending_status(value: Any) -> PendingOrderStatus:
    token = str(value or "UNKNOWN").upper()
    if "PART" in token:
        return PendingOrderStatus.PARTIALLY_FILLED
    if (
        "FILL" in token
        or token in {
            "PORTFOLIO_RECONCILED",
            "RISK_ACCOUNTING_REQUIRED",
            "RISK_ACCOUNTED",
            "EXECUTION_RECORDED",
        }
    ) and "PART" not in token:
        return PendingOrderStatus.FILLED
    if "CANCEL" in token:
        return PendingOrderStatus.CANCELLED
    if "REJECT" in token or "FAILED" in token:
        return PendingOrderStatus.REJECTED
    if "NEW" in token or "SUBMITTED" in token or "ACCEPTED" in token:
        return PendingOrderStatus.NEW
    return PendingOrderStatus.UNKNOWN



def _quotation_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(quotation_to_float(value))
    except (TypeError, ValueError):
        return None


def _sum_optional_totals(
    portfolio: Mapping[str, Any],
    keys: tuple[str, ...],
) -> float | None:
    values = [
        item
        for item in (_quotation_optional(portfolio.get(key)) for key in keys)
        if item is not None
    ]
    return sum(values) if values else None


def _int_value(*values: Any, default: int) -> int:
    for value in values:
        if value is None:
            continue
        if isinstance(value, Mapping):
            value = value.get("units", 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return int(default)
