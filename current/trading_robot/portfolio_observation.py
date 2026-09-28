"""Bounded desktop broker observations; no submission or execution accounting.

A missing active order is NOT a terminal receipt. Resolve only previously
observed BROKER identities, with one state read each and no retries here.
The legacy valuation-based cash projection is unchanged by this policy.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import re
import time
from types import MappingProxyType
from typing import Any

from .portfolio_model import PendingOrderState, PortfolioState

_INT = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_STATUSES = frozenset({
    "EXECUTION_REPORT_STATUS_NEW", "EXECUTION_REPORT_STATUS_PARTIALLYFILL",
    "EXECUTION_REPORT_STATUS_FILL", "EXECUTION_REPORT_STATUS_CANCELLED",
    "EXECUTION_REPORT_STATUS_REJECTED",
})


class PortfolioObservationError(RuntimeError):
    """Finite error: never exposes provider payload, credentials or identifiers."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PortfolioObservationError(reason)


def _text(value: Any) -> str:
    _require(type(value) is str and 0 < len(value) <= 128 and value == value.strip(),
             "PORTFOLIO_OBSERVATION_ID_INVALID")
    return value


def _integer(value: Any) -> int:
    _require(type(value) is int or (type(value) is str and bool(_INT.fullmatch(value))),
             "PORTFOLIO_OBSERVATION_INTEGER_INVALID")
    result = int(value)
    _require(0 <= result <= 2**63 - 1, "PORTFOLIO_OBSERVATION_INTEGER_INVALID")
    return result


def _quotation(value: Any) -> Decimal:
    _require(isinstance(value, Mapping), "PORTFOLIO_OBSERVATION_NUMBER_INVALID")
    units = value.get("units", "0")
    nano = value.get("nano", 0)
    _require(type(units) is int or (type(units) is str and bool(_INT.fullmatch(units))),
             "PORTFOLIO_OBSERVATION_NUMBER_INVALID")
    _require(type(nano) is int and abs(nano) < 1_000_000_000,
             "PORTFOLIO_OBSERVATION_NUMBER_INVALID")
    integer = int(units)
    _require(abs(integer) <= 2**63 - 1 and not (integer * nano < 0),
             "PORTFOLIO_OBSERVATION_NUMBER_INVALID")
    return Decimal(integer) + Decimal(nano) / Decimal(1_000_000_000)


@dataclass(frozen=True)
class PortfolioObservationPolicy:
    """Strict RUB/whole-lot configured desktop scope, not a general API parser."""
    account_id: str
    instruments: Mapping[str, Any]
    max_order_state_reads: int = 3
    max_acquisition_seconds: float = 30.0
    binding_guard: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        _text(self.account_id)
        _require(type(self.max_order_state_reads) is int and 0 <= self.max_order_state_reads <= 3,
                 "PORTFOLIO_OBSERVATION_BUDGET_INVALID")
        _require(type(self.max_acquisition_seconds) in {int, float}
                 and 0 < self.max_acquisition_seconds <= 30,
                 "PORTFOLIO_OBSERVATION_BUDGET_INVALID")
        _require(bool(self.instruments), "PORTFOLIO_OBSERVATION_SCOPE_INVALID")
        object.__setattr__(self, "instruments", MappingProxyType(dict(self.instruments)))

    def _portfolio(self, raw: Any) -> dict[str, Any]:
        _require(isinstance(raw, Mapping), "PORTFOLIO_OBSERVATION_PAYLOAD_INVALID")
        _require(raw.get("accountId") == self.account_id, "PORTFOLIO_OBSERVATION_ACCOUNT_MISMATCH")
        for field in ("totalAmountPortfolio", "totalAmountCurrencies"):
            value = raw.get(field)
            _require(isinstance(value, Mapping) and value.get("currency") == "rub",
                     "PORTFOLIO_OBSERVATION_VALUATION_INVALID")
            _require(_quotation(value) >= 0, "PORTFOLIO_OBSERVATION_VALUATION_INVALID")
        raw = deepcopy(dict(raw))
        positions = raw.get("positions")
        _require(type(positions) is list, "PORTFOLIO_OBSERVATION_POSITIONS_INVALID")
        seen: set[str] = set()
        for row in positions:
            _require(isinstance(row, Mapping), "PORTFOLIO_OBSERVATION_POSITION_INVALID")
            uid = _text(row.get("instrumentUid"))
            _require(uid not in seen, "PORTFOLIO_OBSERVATION_DUPLICATE_POSITION")
            seen.add(uid)
            if row.get("instrumentType") == "currency":
                _quotation(row.get("quantity"))
                continue  # Existing currency valuation projection is unchanged.
            _require(row.get("blocked", False) is False
                     and ("blockedLots" not in row or _quotation(row["blockedLots"]) == 0),
                     "PORTFOLIO_OBSERVATION_SECURITY_BLOCKED")
            metadata = self.instruments.get(uid)
            _require(metadata is not None, "PORTFOLIO_OBSERVATION_INSTRUMENT_MISMATCH")
            _require(row.get("instrumentType") == metadata.asset_class.lower(),
                     "PORTFOLIO_OBSERVATION_ASSET_MISMATCH")
            quantity = _quotation(row.get("quantity"))
            lots = quantity / Decimal(metadata.lot_size)
            _require(0 <= lots <= 2**63 - 1 and lots == lots.to_integral_value(),
                     "PORTFOLIO_OBSERVATION_LOTS_MISMATCH")
            if "quantityLots" in row:
                _require(_quotation(row["quantityLots"]) == lots,
                         "PORTFOLIO_OBSERVATION_LOTS_MISMATCH")
            # quantityLots is deprecated. Derive only from checked quantity and
            # accepted instrument lot size; never round shares into lots.
            row["quantityLots"] = {"units": str(int(lots)), "nano": 0}
            for key in ("averagePositionPrice", "currentPrice"):
                money = row.get(key)
                _require(isinstance(money, Mapping) and money.get("currency") == metadata.currency.lower(),
                         "PORTFOLIO_OBSERVATION_CURRENCY_MISMATCH")
                _require(_quotation(money) >= 0, "PORTFOLIO_OBSERVATION_PRICE_INVALID")
        return deepcopy(dict(raw))

    def _order(self, raw: Any, previous: PendingOrderState | None = None) -> dict[str, Any]:
        _require(isinstance(raw, Mapping), "PORTFOLIO_OBSERVATION_ORDER_INVALID")
        if "accountId" in raw:
            _require(raw["accountId"] == self.account_id, "PORTFOLIO_OBSERVATION_ACCOUNT_MISMATCH")
        uid = _text(raw.get("instrumentUid"))
        _require(uid in self.instruments, "PORTFOLIO_OBSERVATION_INSTRUMENT_MISMATCH")
        broker_id = _text(raw.get("orderId"))
        request_id = raw.get("orderRequestId")
        if request_id is not None and request_id != "":
            _text(request_id)
        direction = raw.get("direction")
        _require(type(direction) is str and direction in {"ORDER_DIRECTION_BUY", "ORDER_DIRECTION_SELL"},
                 "PORTFOLIO_OBSERVATION_DIRECTION_INVALID")
        requested, executed = _integer(raw.get("lotsRequested")), _integer(raw.get("lotsExecuted"))
        _require(requested > 0 and executed <= requested, "PORTFOLIO_OBSERVATION_FILL_INVALID")
        status = raw.get("executionReportStatus")
        _require(type(status) is str and status in _STATUSES, "PORTFOLIO_OBSERVATION_STATUS_INVALID")
        _require(not (status == "EXECUTION_REPORT_STATUS_FILL" and executed != requested)
                 and not (status in {"EXECUTION_REPORT_STATUS_NEW", "EXECUTION_REPORT_STATUS_REJECTED"} and executed != 0)
                 and not (status == "EXECUTION_REPORT_STATUS_PARTIALLYFILL" and not 0 < executed < requested),
                 "PORTFOLIO_OBSERVATION_FILL_INVALID")
        if previous is not None:
            _require(previous.instrument_id == uid
                     and previous.direction == direction.removeprefix("ORDER_DIRECTION_")
                     and previous.requested_lots == requested
                     and executed >= previous.executed_lots,
                     "PORTFOLIO_OBSERVATION_ORDER_BINDING_MISMATCH")
            if previous.broker_order_id:
                _require(previous.broker_order_id == broker_id, "PORTFOLIO_OBSERVATION_ORDER_BINDING_MISMATCH")
            if previous.order_request_id != previous.broker_order_id:
                _require(request_id == previous.order_request_id, "PORTFOLIO_OBSERVATION_ORDER_BINDING_MISMATCH")
            else:
                _require(previous.order_request_id == broker_id, "PORTFOLIO_OBSERVATION_ORDER_BINDING_MISMATCH")
        detached = deepcopy(dict(raw))
        # Retain the existing canonical identity even when an external order was
        # initially identified by exchange ID and now also has a request ID.
        if previous is not None:
            detached["orderRequestId"] = previous.order_request_id
        return detached

    def acquire(
        self, api: Any, previous: PortfolioState,
        observer: Callable[[str], None] | None = None,
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
        _require(previous.account_id == self.account_id, "PORTFOLIO_OBSERVATION_ACCOUNT_MISMATCH")
        if self.binding_guard is not None:
            self.binding_guard()
        started = time.monotonic()
        def checkpoint(stage: str) -> None:
            _require(time.monotonic() - started <= self.max_acquisition_seconds,
                     "PORTFOLIO_OBSERVATION_EXPIRED")
            if observer is not None:
                observer(stage)
        checkpoint("PROVIDER_PORTFOLIO")
        try:
            raw_portfolio = api.get_portfolio(self.account_id)
        except Exception:
            raise PortfolioObservationError("PORTFOLIO_OBSERVATION_PROVIDER_UNAVAILABLE") from None
        portfolio = self._portfolio(raw_portfolio)
        checkpoint("PROVIDER_ORDERS")
        try:
            raw_orders = api.get_orders(self.account_id)
        except Exception:
            raise PortfolioObservationError("PORTFOLIO_OBSERVATION_PROVIDER_UNAVAILABLE") from None
        _require(type(raw_orders) is list, "PORTFOLIO_OBSERVATION_ORDERS_INVALID")
        cached: dict[str, PendingOrderState] = {}
        cached_broker_ids: set[str] = set()
        for position in previous.positions:
            for pending in position.pending_orders:
                if pending.source != "BROKER" or not (pending.active or pending.uncertain):
                    continue
                _text(pending.order_request_id)
                _require(pending.order_request_id not in cached,
                         "PORTFOLIO_OBSERVATION_DUPLICATE_CACHED_ORDER")
                if pending.broker_order_id:
                    _text(pending.broker_order_id)
                    _require(pending.broker_order_id not in cached_broker_ids,
                             "PORTFOLIO_OBSERVATION_DUPLICATE_CACHED_ORDER")
                    cached_broker_ids.add(pending.broker_order_id)
                cached[pending.order_request_id] = pending
        orders: list[dict[str, Any]] = []
        observed: set[str] = set()
        broker_ids: set[str] = set()
        for raw in raw_orders:
            order = self._order(raw)
            key = order.get("orderRequestId") or order["orderId"]
            prior = cached.get(key) or next((o for o in cached.values() if o.broker_order_id == order["orderId"]), None)
            order = self._order(order, prior)
            key = order.get("orderRequestId") or order["orderId"]
            _require(key not in observed and order["orderId"] not in broker_ids,
                     "PORTFOLIO_OBSERVATION_DUPLICATE_ORDER")
            observed.add(key)
            broker_ids.add(order["orderId"])
            orders.append(order)
        missing = [order for key, order in cached.items() if key not in observed]
        _require(len(missing) <= self.max_order_state_reads, "PORTFOLIO_OBSERVATION_READ_BUDGET_EXCEEDED")
        for previous_order in missing:
            checkpoint("PROVIDER_ORDER_STATE")
            exchange_id = previous_order.broker_order_id
            try:
                raw = api.get_order_state(self.account_id,
                    exchange_id or previous_order.order_request_id,
                    by_request_id=not bool(exchange_id))
            except Exception:
                raise PortfolioObservationError("PORTFOLIO_OBSERVATION_ORDER_STATE_UNAVAILABLE") from None
            orders.append(self._order(raw, previous_order))
        checkpoint("OBSERVATION_COMPLETE")
        if self.binding_guard is not None:
            self.binding_guard()
        return portfolio, tuple(orders)
