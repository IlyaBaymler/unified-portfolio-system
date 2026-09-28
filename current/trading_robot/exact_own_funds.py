"""Additional locked own-funds gate, not a replacement for CL4/CL5 evidence.

MARKET only. Observations are sequential, request-bound and not a broker
attestation or a guarantee of execution price/fees. No retries or provider writes.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import Any

DOMAIN = "CL7_OWN_FUNDS_EVIDENCE_V1"
_MAX_NANO = 10**21  # 1e12 RUB, intentionally bounded non-margin scope.
_INT = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}Z\Z")
MAX_AGE_NS = 5_000_000_000


class OwnFundsError(RuntimeError):
    """Finite code only: never include provider payload or exception text."""


def require(value: bool, reason: str) -> None:
    if not value:
        raise OwnFundsError(reason)


def _uint(value: Any) -> int:
    require(type(value) is int or (type(value) is str and bool(_INT.fullmatch(value))),
            "OWN_FUNDS_NUMBER_INVALID")
    number = int(value)
    require(0 <= number <= 2**63 - 1, "OWN_FUNDS_NUMBER_INVALID")
    return number


def _amount(value: Any) -> int:
    require(isinstance(value, Mapping), "OWN_FUNDS_MONEY_INVALID")
    units = _uint(value.get("units", "0"))
    nano = value.get("nano", 0)
    require(type(nano) is int and 0 <= nano < 10**9, "OWN_FUNDS_MONEY_INVALID")
    require("currency" not in value or value["currency"] in ("rub", "RUB"),
            "OWN_FUNDS_CURRENCY_INVALID")
    amount = units * 10**9 + nano
    require(amount <= _MAX_NANO, "OWN_FUNDS_MONEY_INVALID")
    return amount


def _balances(rows: Any) -> dict[str, int]:
    require(type(rows) is list and len(rows) <= 32, "OWN_FUNDS_BALANCES_INVALID")
    result = {}
    for row in rows:
        require(isinstance(row, Mapping), "OWN_FUNDS_BALANCES_INVALID")
        cur = row.get("currency")
        require(type(cur) is str and bool(re.fullmatch(r"[a-z]{3}|[A-Z]{3}", cur)),
                "OWN_FUNDS_CURRENCY_INVALID")
        key = cur.upper()
        require(key not in result, "OWN_FUNDS_DUPLICATE_CURRENCY")
        value = dict(row)
        value.pop("currency")  # Preserve the raw response; do not FX-convert.
        result[key] = _amount(value)
    return result


def canonical(value: Any) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, OverflowError):
        raise OwnFundsError("OWN_FUNDS_PAYLOAD_INVALID") from None
    require(len(raw) <= 1_048_576, "OWN_FUNDS_PAYLOAD_INVALID")
    return raw


def digest(value: Any) -> str:
    return sha256(canonical(value)).hexdigest()


def timestamp_ns(value: Any) -> int:
    require(type(value) is str and bool(_TIME.fullmatch(value)), "OWN_FUNDS_CLOCK_INVALID")
    try:
        base = datetime.fromisoformat(value[:19] + "+00:00")
        delta = base - datetime(1970, 1, 1, tzinfo=timezone.utc)
    except ValueError:
        raise OwnFundsError("OWN_FUNDS_CLOCK_INVALID") from None
    return (delta.days * 86400 + delta.seconds) * 10**9 + int(value[20:29])


@dataclass(frozen=True, slots=True)
class OwnFundsEvidence:
    request_sha256: str
    metadata_sha256: str
    positions_sha256: str
    limits_sha256: str
    direction: str
    order_type: str
    time_in_force: str
    requested_lots: int
    lot_size: int
    estimated_price_kopecks: int
    rub_position_nano: int
    broker_blocked_nano: int
    own_money_nano: int
    own_max_lots: int
    all_local_reservations_nano: int
    own_reservation_nano: int
    started_at: str
    completed_at: str

    def __post_init__(self) -> None:
        for name in ("request_sha256", "metadata_sha256", "positions_sha256", "limits_sha256"):
            value = getattr(self, name)
            require(type(value) is str and bool(_HASH.fullmatch(value)), "OWN_FUNDS_EVIDENCE_INVALID")
        for field in fields(self):
            if field.name in _INTEGER_FIELDS:
                value = getattr(self, field.name)
                require(type(value) is int and 0 <= value <= _MAX_NANO,
                        "OWN_FUNDS_EVIDENCE_INVALID")
        require(type(self.direction) is str and self.direction in {"BUY", "SELL"}
                and type(self.order_type) is str and self.order_type == "MARKET"
                and type(self.time_in_force) is str and self.time_in_force in {"FILL_AND_KILL", "FILL_OR_KILL"}
                and self.requested_lots > 0 and self.lot_size > 0
                and self.estimated_price_kopecks > 0
                and self.requested_lots <= self.own_max_lots,
                "OWN_FUNDS_EVIDENCE_INVALID")
        require(all(getattr(self, k) <= 2**63-1 for k in
                    ("requested_lots", "lot_size", "estimated_price_kopecks", "own_max_lots")),
                "OWN_FUNDS_EVIDENCE_INVALID")
        require(0 <= timestamp_ns(self.completed_at) - timestamp_ns(self.started_at) <= MAX_AGE_NS,
                "OWN_FUNDS_STALE")
        require(self.own_money_nano <= self.rub_position_nano
                and self.own_reservation_nano <= self.all_local_reservations_nano,
                "OWN_FUNDS_EVIDENCE_INVALID")
        if self.direction == "BUY":
            require(self.own_reservation_nano > 0
                    and self.all_local_reservations_nano <= self.own_money_nano,
                    "OWN_FUNDS_INSUFFICIENT")
        else:
            require(self.own_reservation_nano == 0, "OWN_FUNDS_EVIDENCE_INVALID")

    @property
    def free_after_reservations_nano(self) -> int:
        return max(0, self.own_money_nano - self.all_local_reservations_nano)

    def to_canonical_dict(self) -> dict[str, Any]:
        result = {f.name: (str(getattr(self, f.name)) if f.name in _INTEGER_FIELDS
                           else getattr(self, f.name)) for f in fields(self)}
        return {"domain": DOMAIN, "version": 1, **result}

    @classmethod
    def from_canonical_dict(cls, value: Any) -> OwnFundsEvidence:
        require(isinstance(value, Mapping) and set(value) == {f.name for f in fields(cls)} | {"domain", "version"}
                and value["domain"] == DOMAIN and type(value["version"]) is int and value["version"] == 1,
                "OWN_FUNDS_EVIDENCE_INVALID")
        args = {f.name: value[f.name] for f in fields(cls)}
        for key in _INTEGER_FIELDS:
            require(type(args[key]) is str and bool(re.fullmatch(r"0|[1-9][0-9]{0,21}", args[key])),
                    "OWN_FUNDS_EVIDENCE_INVALID")
            args[key] = int(args[key])
        return cls(**args)

    def check_age(self, now: str) -> None:
        require(timestamp_ns(self.completed_at) <= timestamp_ns(now)
                and 0 <= timestamp_ns(now) - timestamp_ns(self.started_at) <= MAX_AGE_NS,
                "OWN_FUNDS_STALE")


_INTEGER_FIELDS = frozenset({"requested_lots", "lot_size", "estimated_price_kopecks", "rub_position_nano",
    "broker_blocked_nano", "own_money_nano", "own_max_lots", "all_local_reservations_nano", "own_reservation_nano"})


@dataclass(frozen=True)
class LockedOwnFundsPolicy:
    account_id: str
    instruments: Mapping[str, Any]
    binding_guard: Callable[[], None]

    def __post_init__(self) -> None:
        require(type(self.account_id) is str and bool(self.account_id)
                and isinstance(self.instruments, Mapping) and 2 <= len(self.instruments) <= 3
                and callable(self.binding_guard), "OWN_FUNDS_POLICY_INVALID")
        for uid, row in self.instruments.items():
            require(type(uid) is str and bool(uid) and row.instrument_id == uid
                    and row.currency in {"RUB", "rub"} and row.asset_class.lower() in {"share", "etf"}
                    and type(row.lot_size) is int and row.lot_size > 0, "OWN_FUNDS_POLICY_INVALID")
        object.__setattr__(self, "instruments", MappingProxyType(dict(self.instruments)))

    def acquire(self, provider: Any, intent: Any, central: Any, *, clock: Callable[[], str],
                monotonic_ns: Callable[[], int]) -> OwnFundsEvidence:
        """Called only under the existing authority/Portfolio/Risk/ledger/Central locks."""
        self.binding_guard()
        c = intent.candidate
        require(c.account_id == self.account_id and central.account_id == self.account_id,
                "OWN_FUNDS_ACCOUNT_MISMATCH")
        require(c.order_type == "MARKET", "OWN_FUNDS_ORDER_TYPE_UNSUPPORTED")
        row = self.instruments.get(c.instrument_id)
        require(row is not None and row.lot_size == c.lot_size, "OWN_FUNDS_METADATA_MISMATCH")
        require(intent.status == "QUEUED" and central.queued and central.queued[0] == intent
                and central.blocking_intent is None, "OWN_FUNDS_CENTRAL_MISMATCH")
        request_hash = digest({"domain": "CL7_OWN_FUNDS_REQUEST_V1", "intent_id": intent.intent_id,
                               "candidate": c.to_dict(), "get_max_lots_price": None})
        metadata_hash = digest({"instrument_id": c.instrument_id, "currency": row.currency,
                                "asset_class": row.asset_class, "lot_size": row.lot_size})
        started_at, start = clock(), monotonic_ns()
        require(type(start) is int and start >= 0, "OWN_FUNDS_CLOCK_INVALID")

        def checkpoint() -> None:
            tick = monotonic_ns()
            require(type(tick) is int and 0 <= tick - start <= MAX_AGE_NS, "OWN_FUNDS_STALE")
            require(0 <= timestamp_ns(clock()) - timestamp_ns(started_at) <= MAX_AGE_NS, "OWN_FUNDS_STALE")
            self.binding_guard()

        try:
            positions = provider.get_positions(self.account_id)
        except Exception:
            raise OwnFundsError("OWN_FUNDS_POSITIONS_UNAVAILABLE") from None
        checkpoint()
        require(isinstance(positions, Mapping) and positions.get("accountId") == self.account_id,
                "OWN_FUNDS_ACCOUNT_MISMATCH")
        require(positions.get("limitsLoadingInProgress", False) is False, "OWN_FUNDS_LIMITS_LOADING")
        require(all(type(positions.get(k, [])) is list and not positions.get(k, []) for k in ("futures", "options")),
                "OWN_FUNDS_DERIVATIVES_UNSUPPORTED")
        money = _balances(positions.get("money", []))
        blocked = _balances(positions.get("blocked", []))
        positions_hash = digest(positions)
        try:
            limits = provider.get_max_lots(self.account_id, c.instrument_id, price=None)
        except Exception:
            raise OwnFundsError("OWN_FUNDS_LIMITS_UNAVAILABLE") from None
        checkpoint()
        require(isinstance(limits, Mapping), "OWN_FUNDS_LIMITS_INVALID")
        for key, expected in (("accountId", self.account_id), ("instrumentId", c.instrument_id), ("instrumentUid", c.instrument_id)):
            require(key not in limits or limits[key] == expected, "OWN_FUNDS_LIMITS_BINDING_MISMATCH")
        require(limits.get("currency") in {"rub", "RUB"}, "OWN_FUNDS_CURRENCY_INVALID")
        own = limits.get("buyLimits" if c.direction == "BUY" else "sellLimits")
        require(isinstance(own, Mapping), "OWN_FUNDS_LIMITS_REQUIRED")
        cap = _uint(own.get("buyMaxMarketLots" if c.direction == "BUY" else "sellMaxLots", "0"))
        require(c.requested_lots <= cap, "OWN_FUNDS_LOTS_INSUFFICIENT")
        cash = _amount(own.get("buyMoneyAmount")) if c.direction == "BUY" else 0
        require(cash <= money.get("RUB", 0), "OWN_FUNDS_POSITION_LIMIT_MISMATCH")
        # All Central local queued reserves, including this intent, exactly once.
        total = sum(item.reserved_cash_kopecks * 10**7 for item in central.queued)
        own_reserved = intent.reserved_cash_kopecks * 10**7
        require(c.direction != "SELL" or c.requested_lots <= c.current_lots, "OWN_FUNDS_SHORT_FORBIDDEN")
        evidence = OwnFundsEvidence(request_hash, metadata_hash, positions_hash, digest(limits),
            c.direction, c.order_type, c.time_in_force, c.requested_lots, c.lot_size,
            c.estimated_price_kopecks, money.get("RUB", 0), blocked.get("RUB", 0), cash, cap,
            total, own_reserved, started_at, clock())
        checkpoint()
        evidence.check_age(clock())
        return evidence
