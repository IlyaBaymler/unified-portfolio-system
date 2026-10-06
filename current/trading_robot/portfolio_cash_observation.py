"""Desktop RUB cash observation, never valuation/withdraw/margin buying power.

The canonical available value is a conservative common own-money ceiling for
all configured RUB securities, not a universal exact-cash or execution proof.
Positions.money is a currency-specific upper bound; own buyLimits supply the
spendable ceiling. Broker blocked money is reported, NOT subtracted again.
Central remains the sole owner of local queued reservations.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType
from typing import Any

from .portfolio_adapters import BrokerPortfolioRecord
from .portfolio_model import CashBalance

_INT = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_CURRENCY = re.compile(r"[a-z]{3}\Z")
_MAX_MONEY = Decimal("1000000000000")  # explicit bounded desktop policy: 1e12 RUB


class PortfolioCashObservationError(RuntimeError):
    """Finite privacy-safe failure; no provider text or identifiers."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PortfolioCashObservationError(reason)


def _amount(raw: Any) -> Decimal:
    _require(isinstance(raw, Mapping), "PORTFOLIO_CASH_NUMBER_INVALID")
    # Protobuf JSON can omit zero scalar fields inside a present Quotation.
    units, nano = raw.get("units", "0"), raw.get("nano", 0)
    _require(type(units) is int or (type(units) is str and len(units) <= 20
                                  and bool(_INT.fullmatch(units))),
             "PORTFOLIO_CASH_NUMBER_INVALID")
    _require(type(nano) is int and abs(nano) < 1_000_000_000,
             "PORTFOLIO_CASH_NUMBER_INVALID")
    integer = int(units)
    _require(abs(integer) <= 2**63 - 1 and integer * nano >= 0,
             "PORTFOLIO_CASH_NUMBER_INVALID")
    amount = Decimal(integer) + Decimal(nano) / Decimal(1_000_000_000)
    _require(0 <= amount <= _MAX_MONEY, "PORTFOLIO_CASH_AMOUNT_OUT_OF_SCOPE")
    return amount


def _money_list(raw: Any) -> dict[str, Decimal]:
    _require(type(raw) is list and len(raw) <= 32, "PORTFOLIO_CASH_BALANCES_INVALID")
    result: dict[str, Decimal] = {}
    for row in raw:
        _require(isinstance(row, Mapping), "PORTFOLIO_CASH_BALANCES_INVALID")
        currency = row.get("currency")
        _require(type(currency) is str and bool(_CURRENCY.fullmatch(currency)),
                 "PORTFOLIO_CASH_CURRENCY_INVALID")
        _require(currency not in result, "PORTFOLIO_CASH_DUPLICATE_CURRENCY")
        result[currency] = _amount(row)
    return result


def _digest(raw: Any) -> str:
    try:
        data = json.dumps(raw, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise PortfolioCashObservationError("PORTFOLIO_CASH_PAYLOAD_INVALID") from None
    _require(len(data) <= 1_048_576, "PORTFOLIO_CASH_PAYLOAD_INVALID")
    return sha256(data).hexdigest()


def _float_floor(value: Decimal) -> float:
    """Round down at the canonical float boundary, never manufacture cash."""
    # Central reserves kopecks. Keep a cash ceiling no greater than observed
    # cash even if float conversion would round the floored decimal upward.
    floored = value.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    result = float(floored)
    if Decimal.from_float(result) > floored:
        result = math.nextafter(result, -math.inf)
    return result


@dataclass(frozen=True, slots=True)
class PortfolioCashObservation:
    account_id: str
    instrument_ids: tuple[str, ...]
    rub_position: Decimal
    rub_blocked: Decimal
    own_buy_amounts: tuple[Decimal, ...]
    positions_sha256: str
    limits_sha256: tuple[str, ...]

    def __post_init__(self) -> None:
        _require(type(self.account_id) is str and bool(self.account_id),
                 "PORTFOLIO_CASH_OBSERVATION_INVALID")
        _require(type(self.instrument_ids) is tuple and 2 <= len(self.instrument_ids) <= 3
                 and all(type(uid) is str and bool(uid) for uid in self.instrument_ids)
                 and tuple(sorted(set(self.instrument_ids))) == self.instrument_ids,
                 "PORTFOLIO_CASH_OBSERVATION_INVALID")
        _require(type(self.own_buy_amounts) is tuple
                 and len(self.own_buy_amounts) == len(self.instrument_ids),
                 "PORTFOLIO_CASH_OBSERVATION_INVALID")
        amounts = (self.rub_position, self.rub_blocked, *self.own_buy_amounts)
        _require(all(type(n) is Decimal and n.is_finite() and 0 <= n <= _MAX_MONEY for n in amounts)
                 and all(n <= self.rub_position for n in self.own_buy_amounts),
                 "PORTFOLIO_CASH_OBSERVATION_INVALID")
        _require(type(self.limits_sha256) is tuple
                 and len(self.limits_sha256) == len(self.instrument_ids)
                 and all(type(h) is str and bool(re.fullmatch(r"[0-9a-f]{64}", h))
                         for h in (self.positions_sha256, *self.limits_sha256)),
                 "PORTFOLIO_CASH_OBSERVATION_INVALID")

    @property
    def available_rub(self) -> Decimal:
        return min(self.rub_position, *self.own_buy_amounts)

    def apply(self, broker: BrokerPortfolioRecord) -> BrokerPortfolioRecord:
        _require(broker.account.account_id == self.account_id,
                 "PORTFOLIO_CASH_ACCOUNT_MISMATCH")
        # Existing AccountState remains RUB-only here. Non-RUB positions are
        # validated but never FX-converted into a RUB purchase budget.
        return replace(broker, account=replace(broker.account, cash_balances=(
            CashBalance(currency="rub", available=_float_floor(self.available_rub),
                        blocked=float(self.rub_blocked)),
        )))


@dataclass(frozen=True)
class DesktopOwnCashPolicy:
    """One Positions + one own-limits read for each of 2-3 configured securities.

    Calls are made with the exact configured account and instrument IDs through
    the existing Sandbox client. GetMaxLots does not normally echo these IDs;
    the binding is the request/response call, not an invented response field or
    a cryptographic broker attestation. Optional identity echoes must match.
    """
    account_id: str
    instruments: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require(type(self.account_id) is str and 0 < len(self.account_id) <= 128
                 and self.account_id == self.account_id.strip(),
                 "PORTFOLIO_CASH_ACCOUNT_INVALID")
        _require(isinstance(self.instruments, Mapping) and 2 <= len(self.instruments) <= 3,
                 "PORTFOLIO_CASH_SCOPE_INVALID")
        copied = dict(self.instruments)
        for uid, metadata in copied.items():
            _require(type(uid) is str and 0 < len(uid) <= 128 and uid == uid.strip()
                     and getattr(metadata, "instrument_id", None) == uid
                     and str(getattr(metadata, "currency", "")).lower() == "rub"
                     and str(getattr(metadata, "asset_class", "")).lower() in {"share", "etf"}
                     and type(getattr(metadata, "lot_size", None)) is int
                     and metadata.lot_size > 0, "PORTFOLIO_CASH_SCOPE_INVALID")
        object.__setattr__(self, "instruments", MappingProxyType(copied))

    def acquire(self, api: Any, checkpoint: Callable[[str], None]) -> PortfolioCashObservation:
        checkpoint("PROVIDER_CASH_POSITIONS")
        try:
            raw = api.get_positions(self.account_id)
        except Exception:
            raise PortfolioCashObservationError("PORTFOLIO_CASH_POSITIONS_UNAVAILABLE") from None
        checkpoint("CASH_POSITIONS_RECEIVED")
        _require(isinstance(raw, Mapping), "PORTFOLIO_CASH_PAYLOAD_INVALID")
        _require(raw.get("accountId") == self.account_id, "PORTFOLIO_CASH_ACCOUNT_MISMATCH")
        _require(raw.get("limitsLoadingInProgress", False) is False,
                 "PORTFOLIO_CASH_LIMITS_LOADING")
        # Empty repeated fields / false scalar may be omitted by protobuf JSON.
        # Missing money therefore gives ZERO, never the valuation fallback.
        money = _money_list(raw.get("money", []))
        blocked = _money_list(raw.get("blocked", []))
        for key in ("futures", "options"):
            _require(type(raw.get(key, [])) is list and not raw.get(key, []),
                     "PORTFOLIO_CASH_DERIVATIVES_UNSUPPORTED")
        positions_hash = _digest(raw)
        amounts, hashes = [], []
        ids = tuple(sorted(self.instruments))
        for uid in ids:
            checkpoint("PROVIDER_OWN_BUY_LIMITS")
            try:
                raw_limit = api.get_max_lots(self.account_id, uid)
            except Exception:
                raise PortfolioCashObservationError("PORTFOLIO_CASH_LIMITS_UNAVAILABLE") from None
            checkpoint("OWN_BUY_LIMITS_RECEIVED")
            _require(isinstance(raw_limit, Mapping), "PORTFOLIO_CASH_LIMITS_INVALID")
            for key, expected in (("accountId", self.account_id), ("instrumentId", uid),
                                  ("instrumentUid", uid)):
                if key in raw_limit:
                    _require(raw_limit[key] == expected, "PORTFOLIO_CASH_LIMITS_BINDING_MISMATCH")
            _require(raw_limit.get("currency") == "rub", "PORTFOLIO_CASH_CURRENCY_INVALID")
            own = raw_limit.get("buyLimits")
            _require(isinstance(own, Mapping) and "buyMoneyAmount" in own,
                     "PORTFOLIO_CASH_OWN_LIMITS_REQUIRED")
            amount_raw = own["buyMoneyAmount"]
            amount = _amount(amount_raw)
            if "currency" in amount_raw:
                _require(amount_raw["currency"] == "rub", "PORTFOLIO_CASH_CURRENCY_INVALID")
            _require(amount <= money.get("rub", Decimal(0)),
                     "PORTFOLIO_CASH_BALANCE_LIMIT_MISMATCH")
            amounts.append(amount)
            hashes.append(_digest(raw_limit))
        checkpoint("CASH_OBSERVATION_COMPLETE")
        return PortfolioCashObservation(self.account_id, ids, money.get("rub", Decimal(0)),
            blocked.get("rub", Decimal(0)), tuple(amounts), positions_hash, tuple(hashes))
