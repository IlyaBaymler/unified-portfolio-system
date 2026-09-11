"""Immutable CL5 cash-availability proofs over accepted CL1-CL4 evidence."""

from __future__ import annotations

import hashlib as _hashlib
import hmac as _hmac
import json as _json
import re as _re
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from dataclasses import fields as _fields
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timezone as _timezone
from enum import StrEnum as _StrEnum
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

from trading_robot import broker_read_adapters as _broker
from trading_robot import cash_ledger_domain as _ledger
from trading_robot import cash_ledger_opening_reconciliation as _cl4
from trading_robot import central_order_manager as _central

if _TYPE_CHECKING:
    from trading_robot.broker_read_adapters import BrokerEnvironment
    from trading_robot.cash_ledger_domain import Money
    from trading_robot.cash_ledger_opening_reconciliation import CashReconciliation
    from trading_robot.central_order_manager import CentralOrderState

del annotations

__all__ = (
    "AvailabilityReason",
    "AvailabilityStatus",
    "BrokerPositionsCashProof",
    "CL5Error",
    "CL5Reason",
    "CashAvailabilitySnapshot",
    "CentralReservationProjection",
    "OverlapDisposition",
    "build_broker_positions_cash_proof",
    "build_cash_availability",
    "project_central_reservations",
)


_BROKER_POSITIONS_CASH_PROOF_VERSION = 1
_CENTRAL_RESERVATION_PROJECTION_VERSION = 1
_CASH_AVAILABILITY_SNAPSHOT_VERSION = 1
_KOPECK_TO_NANO = 10_000_000
_MAX_PROOF_AGE_NS = 120_000_000_000
_MAX_CROSS_PROOF_SKEW_NS = 10_000_000_000
_MAX_RESPONSE_DEPTH = 16
_MAX_RESPONSE_NODES = 100_000
_MAX_RESPONSE_CANONICAL_BYTES = 1_048_576
_MAX_MAPPING_KEYS = 4_096
_MAX_STRING_SCALARS = 4_096
_MAX_KEY_SCALARS = 128
_MAX_CENTRAL_INTENTS = 100_000
_MAX_CENTRAL_STATE_DEPTH = 32
_MAX_CENTRAL_STATE_NODES = 2_000_000
_MAX_CENTRAL_STATE_CANONICAL_BYTES = 16_777_216
_MAX_CENTRAL_TRANSITIONS_PER_INTENT = 64
_MAX_CENTRAL_STRING_SCALARS = 4_096
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_RPC = "tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxPositions"
_HASH_RE = _re.compile(r"[0-9a-f]{64}", _re.ASCII)
_TOKEN_RE = _re.compile(r"[A-Z][A-Z0-9_]{0,63}", _re.ASCII)
_TIMESTAMP_RE = _re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{9})Z",
    _re.ASCII,
)
_REQUIRED_RESPONSE_KEYS = frozenset(
    {"accountId", "money", "blocked", "limitsLoadingInProgress"}
)
_IGNORED_RESPONSE_KEYS = frozenset({"securities", "futures", "options"})
_MONEY_VALUE_KEYS = frozenset({"currency", "nano", "units"})
_NONE_TYPE = type(None)


class AvailabilityStatus(_StrEnum):
    READY = "READY"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


class AvailabilityReason(_StrEnum):
    READY = "READY"
    CL4_NOT_READY = "CL4_NOT_READY"
    BROKER_PROOF_STALE = "BROKER_PROOF_STALE"
    CENTRAL_PROJECTION_STALE = "CENTRAL_PROJECTION_STALE"
    MIXED_EVIDENCE_SNAPSHOT = "MIXED_EVIDENCE_SNAPSHOT"
    BROKER_VIEW_MISMATCH = "BROKER_VIEW_MISMATCH"
    FOREIGN_CASH_PRESENT = "FOREIGN_CASH_PRESENT"
    CENTRAL_PROVIDER_OVERLAP_UNKNOWN = "CENTRAL_PROVIDER_OVERLAP_UNKNOWN"
    INSUFFICIENT_AFTER_RESERVATIONS = "INSUFFICIENT_AFTER_RESERVATIONS"


class OverlapDisposition(_StrEnum):
    NO_LOCAL_RESERVATION = "NO_LOCAL_RESERVATION"
    QUEUED_DISJOINT = "QUEUED_DISJOINT"
    AMBIGUOUS_PROVIDER_OVERLAP = "AMBIGUOUS_PROVIDER_OVERLAP"


class CL5Reason(_StrEnum):
    TYPE_INVALID = "TYPE_INVALID"
    VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
    ENVIRONMENT_UNSUPPORTED = "ENVIRONMENT_UNSUPPORTED"
    CURRENCY_UNSUPPORTED = "CURRENCY_UNSUPPORTED"
    ACCOUNT_SCOPE_INVALID = "ACCOUNT_SCOPE_INVALID"
    IDENTITY_KEY_INVALID = "IDENTITY_KEY_INVALID"
    TIMESTAMP_INVALID = "TIMESTAMP_INVALID"
    PROOF_FROM_FUTURE = "PROOF_FROM_FUTURE"
    PROOF_STALE = "PROOF_STALE"
    DEPENDENCY_FROM_FUTURE = "DEPENDENCY_FROM_FUTURE"
    PROOF_INCOMPLETE = "PROOF_INCOMPLETE"
    RESPONSE_BOUNDS_EXCEEDED = "RESPONSE_BOUNDS_EXCEEDED"
    RESPONSE_SCHEMA_INVALID = "RESPONSE_SCHEMA_INVALID"
    POSITIONS_LOADING_IN_PROGRESS = "POSITIONS_LOADING_IN_PROGRESS"
    MONEY_INVALID = "MONEY_INVALID"
    PROOF_IDENTITY_INVALID = "PROOF_IDENTITY_INVALID"
    CENTRAL_STATE_INVALID = "CENTRAL_STATE_INVALID"
    CENTRAL_ACCOUNT_MISMATCH = "CENTRAL_ACCOUNT_MISMATCH"
    CENTRAL_PROJECTION_INVALID = "CENTRAL_PROJECTION_INVALID"
    CL4_EVIDENCE_INVALID = "CL4_EVIDENCE_INVALID"
    ARITHMETIC_OVERFLOW = "ARITHMETIC_OVERFLOW"
    CANONICAL_FORMAT_INVALID = "CANONICAL_FORMAT_INVALID"
    INTERNAL_BOUNDARY_FAILED = "INTERNAL_BOUNDARY_FAILED"


_CAUSE_TYPES = (
    _ledger.MoneyReason,
    _broker.BrokerReadReason,
    _cl4.CL4Reason,
)


class CL5Error(RuntimeError):
    """Closed CL5 failure carrying finite privacy-safe evidence only."""

    def __init__(
        self,
        reason: CL5Reason,
        cause_reason: object | None = None,
        evidence: _Mapping[str, str] | None = None,
    ) -> None:
        if type(reason) is not CL5Reason:
            reason = CL5Reason.INTERNAL_BOUNDARY_FAILED
        if cause_reason is not None and not isinstance(cause_reason, _CAUSE_TYPES):
            cause_reason = None
        safe: dict[str, str] = {"reason": reason.value}
        if cause_reason is not None:
            value = getattr(cause_reason, "value", None)
            if type(value) is str and _TOKEN_RE.fullmatch(value) is not None:
                safe["cause_reason"] = value
        if evidence is not None:
            for key, value in dict(evidence).items():
                if (
                    key in {"account_scope_sha256", "proof_sha256", "projection_sha256"}
                    and type(value) is str
                    and _HASH_RE.fullmatch(value) is not None
                ):
                    safe[key] = value
        self.reason = reason
        self.cause_reason = cause_reason
        self.evidence = _MappingProxyType(safe)
        RuntimeError.__init__(self, reason.value)

    def __str__(self) -> str:
        return self.reason.value

    def __repr__(self) -> str:
        cause = getattr(self.cause_reason, "value", None)
        suffix = "" if cause is None else f", cause_reason={cause}"
        return f"CL5Error(reason={self.reason.value}{suffix})"


def _fail(
    reason: CL5Reason,
    cause_reason: object | None = None,
    **evidence: str,
) -> None:
    raise CL5Error(reason, cause_reason, evidence) from None


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _canonical_bytes(value: object) -> bytes:
    return _json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _sha256(value: bytes) -> str:
    return _hashlib.sha256(value).hexdigest()


def _hmac_sha256(identity_key: bytes, value: object) -> str:
    return _hmac.new(identity_key, _canonical_bytes(value), _hashlib.sha256).hexdigest()


def _is_hash(value: object) -> bool:
    return type(value) is str and _HASH_RE.fullmatch(value) is not None


def _require_hash(value: object, reason: CL5Reason) -> str:
    if not _is_hash(value):
        _fail(reason)
    return value


def _require_key(value: object) -> bytes:
    if type(value) is not bytes or not 32 <= len(value) <= 64:
        _fail(CL5Reason.IDENTITY_KEY_INVALID)
    return value


def _require_key_id(value: object) -> str:
    if (
        type(value) is not str
        or _contains_surrogate(value)
        or _TOKEN_RE.fullmatch(value) is None
    ):
        _fail(CL5Reason.IDENTITY_KEY_INVALID)
    return value


def _require_environment(value: object) -> _broker.BrokerEnvironment:
    if type(value) is not _broker.BrokerEnvironment:
        _fail(CL5Reason.ENVIRONMENT_UNSUPPORTED)
    if value is not _broker.BrokerEnvironment.SANDBOX:
        _fail(CL5Reason.ENVIRONMENT_UNSUPPORTED)
    return value


def _timestamp_ns(value: object) -> int:
    if type(value) is not str:
        _fail(CL5Reason.TIMESTAMP_INVALID)
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        _fail(CL5Reason.TIMESTAMP_INVALID)
    year, month, day, hour, minute, second, fraction = map(int, match.groups())
    try:
        ordinal = _date(year, month, day).toordinal()
    except ValueError:
        _fail(CL5Reason.TIMESTAMP_INVALID)
    if hour > 23 or minute > 59 or second > 59:
        _fail(CL5Reason.TIMESTAMP_INVALID)
    seconds = (((ordinal * 24) + hour) * 60 + minute) * 60 + second
    return seconds * 1_000_000_000 + fraction


def _central_timestamp_ns(value: object) -> int:
    if type(value) is not str:
        _fail(CL5Reason.CENTRAL_STATE_INVALID)
    try:
        parsed = _datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(CL5Reason.CENTRAL_STATE_INVALID)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(CL5Reason.CENTRAL_STATE_INVALID)
    utc = parsed.astimezone(_timezone.utc)
    seconds = (((utc.toordinal() * 24) + utc.hour) * 60 + utc.minute) * 60 + utc.second
    return seconds * 1_000_000_000 + utc.microsecond * 1_000


def _require_fresh(as_of_ns: int, evaluated_ns: int) -> None:
    if as_of_ns > evaluated_ns:
        _fail(CL5Reason.PROOF_FROM_FUTURE)
    if evaluated_ns - as_of_ns > _MAX_PROOF_AGE_NS:
        _fail(CL5Reason.PROOF_STALE)


def _checked_money(value: object) -> _ledger.Money:
    if type(value) is not _ledger.Money:
        _fail(CL5Reason.MONEY_INVALID)
    try:
        checked = _ledger.Money.from_canonical_dict(value.to_canonical_dict())
    except _ledger.MoneyError as error:
        failure = CL5Error(CL5Reason.MONEY_INVALID, error.reason)
    except Exception:  # noqa: BLE001 - forged DTO must not expose internals
        failure = CL5Error(CL5Reason.MONEY_INVALID)
    else:
        if checked.canonical_bytes == value.canonical_bytes:
            return checked
        failure = CL5Error(CL5Reason.MONEY_INVALID)
    raise failure from None


def _money_from_minor_units(minor_units: int) -> _ledger.Money:
    try:
        return _ledger.Money(currency="RUB", minor_units=minor_units)
    except _ledger.MoneyError as error:
        failure = CL5Error(CL5Reason.ARITHMETIC_OVERFLOW, error.reason)
    raise failure from None


def _money_dict(value: _ledger.Money) -> dict[str, object]:
    return value.to_canonical_dict()


def _account_scope(
    raw_account_id: str,
    environment: _broker.BrokerEnvironment,
    identity_key_id: str,
    identity_key: bytes,
) -> str:
    return _hmac_sha256(
        identity_key,
        {
            "account_id": raw_account_id,
            "domain": "v3.10-cl3-account-scope",
            "environment": environment.value,
            "identity_key_id": identity_key_id,
            "provider": "TBANK",
            "version": 1,
        },
    )


def _positions_identity(
    *,
    account_scope_sha256: str,
    as_of: str,
    blocked_rub: _ledger.Money,
    environment: _broker.BrokerEnvironment,
    foreign_cash_present: bool,
    identity_key_id: str,
    positions_money_rub: _ledger.Money,
    response_canonical_sha256: str,
    response_complete: bool,
    identity_key: bytes,
) -> str:
    return _hmac_sha256(
        identity_key,
        {
            "account_scope_sha256": account_scope_sha256,
            "as_of": as_of,
            "blocked_rub": _money_dict(blocked_rub),
            "domain": "v3.10-cl5-broker-positions-cash-proof-identity",
            "environment": environment.value,
            "foreign_cash_present": foreign_cash_present,
            "identity_key_id": identity_key_id,
            "positions_money_rub": _money_dict(positions_money_rub),
            "provider": "TBANK",
            "response_canonical_sha256": response_canonical_sha256,
            "response_complete": response_complete,
            "rpc": _RPC,
            "version": _BROKER_POSITIONS_CASH_PROOF_VERSION,
        },
    )


@_dataclass(frozen=True, slots=True)
class BrokerPositionsCashProof:
    account_scope_sha256: str
    environment: BrokerEnvironment
    as_of: str
    positions_money_rub: Money
    blocked_rub: Money
    foreign_cash_present: bool
    response_canonical_sha256: str
    proof_identity_sha256: str
    identity_key_id: str
    response_complete: bool = True
    version: int = _BROKER_POSITIONS_CASH_PROOF_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != _BROKER_POSITIONS_CASH_PROOF_VERSION
        ):
            _fail(CL5Reason.VERSION_UNSUPPORTED)
        _require_hash(self.account_scope_sha256, CL5Reason.ACCOUNT_SCOPE_INVALID)
        _require_environment(self.environment)
        _timestamp_ns(self.as_of)
        positions = _checked_money(self.positions_money_rub)
        blocked = _checked_money(self.blocked_rub)
        if blocked.minor_units < 0:
            _fail(CL5Reason.MONEY_INVALID)
        if type(self.foreign_cash_present) is not bool:
            _fail(CL5Reason.TYPE_INVALID)
        _require_hash(self.response_canonical_sha256, CL5Reason.PROOF_IDENTITY_INVALID)
        _require_hash(self.proof_identity_sha256, CL5Reason.PROOF_IDENTITY_INVALID)
        _require_key_id(self.identity_key_id)
        if self.response_complete is not True:
            _fail(CL5Reason.PROOF_INCOMPLETE)
        object.__setattr__(self, "positions_money_rub", positions)
        object.__setattr__(self, "blocked_rub", blocked)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "as_of": self.as_of,
            "blocked_rub": _money_dict(self.blocked_rub),
            "domain": "v3.10-cl5-broker-positions-cash-proof",
            "environment": self.environment.value,
            "foreign_cash_present": self.foreign_cash_present,
            "identity_key_id": self.identity_key_id,
            "positions_money_rub": _money_dict(self.positions_money_rub),
            "proof_identity_sha256": self.proof_identity_sha256,
            "provider": "TBANK",
            "response_canonical_sha256": self.response_canonical_sha256,
            "response_complete": self.response_complete,
            "rpc": _RPC,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


def _projection_identity(
    *,
    account_scope_sha256: str,
    ambiguous_count: int,
    ambiguous_reserved_cash: _ledger.Money,
    central_order_revision: int,
    central_reservation_projection_hash: str,
    environment: _broker.BrokerEnvironment,
    evaluated_at: str,
    identity_key_id: str,
    queued_count: int,
    queued_reserved_cash: _ledger.Money,
    total_reserved_cash: _ledger.Money,
    identity_key: bytes,
) -> str:
    return _hmac_sha256(
        identity_key,
        {
            "account_scope_sha256": account_scope_sha256,
            "ambiguous_count": ambiguous_count,
            "ambiguous_reserved_cash": _money_dict(ambiguous_reserved_cash),
            "central_order_revision": str(central_order_revision),
            "central_reservation_projection_hash": central_reservation_projection_hash,
            "domain": "v3.10-cl5-central-reservation-projection-identity",
            "environment": environment.value,
            "evaluated_at": evaluated_at,
            "identity_key_id": identity_key_id,
            "queued_count": queued_count,
            "queued_reserved_cash": _money_dict(queued_reserved_cash),
            "total_reserved_cash": _money_dict(total_reserved_cash),
            "version": _CENTRAL_RESERVATION_PROJECTION_VERSION,
        },
    )


@_dataclass(frozen=True, slots=True)
class CentralReservationProjection:
    account_scope_sha256: str
    environment: BrokerEnvironment
    central_order_revision: int
    central_reservation_projection_hash: str
    queued_reserved_cash: Money
    ambiguous_reserved_cash: Money
    total_reserved_cash: Money
    queued_count: int
    ambiguous_count: int
    evaluated_at: str
    identity_key_id: str
    projection_identity_sha256: str
    version: int = _CENTRAL_RESERVATION_PROJECTION_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != _CENTRAL_RESERVATION_PROJECTION_VERSION
        ):
            _fail(CL5Reason.VERSION_UNSUPPORTED)
        _require_hash(self.account_scope_sha256, CL5Reason.ACCOUNT_SCOPE_INVALID)
        _require_environment(self.environment)
        if (
            type(self.central_order_revision) is not int
            or not 0 <= self.central_order_revision <= _INT64_MAX
        ):
            _fail(CL5Reason.CENTRAL_PROJECTION_INVALID)
        _require_hash(
            self.central_reservation_projection_hash,
            CL5Reason.CENTRAL_PROJECTION_INVALID,
        )
        queued = _checked_money(self.queued_reserved_cash)
        ambiguous = _checked_money(self.ambiguous_reserved_cash)
        total = _checked_money(self.total_reserved_cash)
        if min(queued.minor_units, ambiguous.minor_units, total.minor_units) < 0:
            _fail(CL5Reason.CENTRAL_PROJECTION_INVALID)
        if queued.minor_units + ambiguous.minor_units != total.minor_units:
            _fail(CL5Reason.CENTRAL_PROJECTION_INVALID)
        for count in (self.queued_count, self.ambiguous_count):
            if type(count) is not int or not 0 <= count <= _INT64_MAX:
                _fail(CL5Reason.CENTRAL_PROJECTION_INVALID)
        if (self.queued_count == 0 and queued.minor_units != 0) or (
            self.ambiguous_count == 0 and ambiguous.minor_units != 0
        ):
            _fail(CL5Reason.CENTRAL_PROJECTION_INVALID)
        _timestamp_ns(self.evaluated_at)
        _require_key_id(self.identity_key_id)
        _require_hash(
            self.projection_identity_sha256,
            CL5Reason.CENTRAL_PROJECTION_INVALID,
        )
        object.__setattr__(self, "queued_reserved_cash", queued)
        object.__setattr__(self, "ambiguous_reserved_cash", ambiguous)
        object.__setattr__(self, "total_reserved_cash", total)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "ambiguous_count": self.ambiguous_count,
            "ambiguous_reserved_cash": _money_dict(self.ambiguous_reserved_cash),
            "central_order_revision": str(self.central_order_revision),
            "central_reservation_projection_hash": self.central_reservation_projection_hash,
            "domain": "v3.10-cl5-central-reservation-projection",
            "environment": self.environment.value,
            "evaluated_at": self.evaluated_at,
            "identity_key_id": self.identity_key_id,
            "projection_identity_sha256": self.projection_identity_sha256,
            "queued_count": self.queued_count,
            "queued_reserved_cash": _money_dict(self.queued_reserved_cash),
            "total_reserved_cash": _money_dict(self.total_reserved_cash),
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class CashAvailabilitySnapshot:
    account_scope_sha256: str
    environment: BrokerEnvironment
    currency: str
    evaluated_at: str
    cl4_reconciliation_evaluated_at: str
    broker_cash_as_of: str
    broker_positions_as_of: str
    central_projection_evaluated_at: str
    reconciliation_sha256: str
    cl4_adoption_candidate_sha256: str
    ledger_export_sha256: str
    ledger_revision: int
    ledger_head_sha256: str
    broker_positions_cash_proof_sha256: str
    broker_total_cash: Money
    broker_blocked_cash: Money
    broker_unblocked_cash: Money | None
    central_reservation_projection_sha256: str
    central_order_revision: int
    central_reservation_projection_hash: str
    central_queued_reserved_cash: Money
    central_ambiguous_reserved_cash: Money
    central_total_reserved_cash: Money
    overlap_disposition: OverlapDisposition
    status: AvailabilityStatus
    availability_reason: AvailabilityReason
    free_investable_cash: Money | None
    version: int = _CASH_AVAILABILITY_SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != _CASH_AVAILABILITY_SNAPSHOT_VERSION
        ):
            _fail(CL5Reason.VERSION_UNSUPPORTED)
        _require_hash(self.account_scope_sha256, CL5Reason.ACCOUNT_SCOPE_INVALID)
        _require_environment(self.environment)
        if type(self.currency) is not str or self.currency != "RUB":
            _fail(CL5Reason.CURRENCY_UNSUPPORTED)
        for timestamp in (
            self.evaluated_at,
            self.cl4_reconciliation_evaluated_at,
            self.broker_cash_as_of,
            self.broker_positions_as_of,
            self.central_projection_evaluated_at,
        ):
            _timestamp_ns(timestamp)
        for value in (
            self.reconciliation_sha256,
            self.cl4_adoption_candidate_sha256,
            self.ledger_export_sha256,
            self.ledger_head_sha256,
            self.broker_positions_cash_proof_sha256,
            self.central_reservation_projection_sha256,
            self.central_reservation_projection_hash,
        ):
            _require_hash(value, CL5Reason.CANONICAL_FORMAT_INVALID)
        for revision in (self.ledger_revision, self.central_order_revision):
            if type(revision) is not int or not 0 <= revision <= _INT64_MAX:
                _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        broker_total = _checked_money(self.broker_total_cash)
        broker_blocked = _checked_money(self.broker_blocked_cash)
        queued = _checked_money(self.central_queued_reserved_cash)
        ambiguous = _checked_money(self.central_ambiguous_reserved_cash)
        total = _checked_money(self.central_total_reserved_cash)
        if (
            min(broker_blocked.minor_units, queued.minor_units, ambiguous.minor_units)
            < 0
        ):
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        if queued.minor_units + ambiguous.minor_units != total.minor_units:
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        unblocked = (
            None
            if self.broker_unblocked_cash is None
            else _checked_money(self.broker_unblocked_cash)
        )
        free = (
            None
            if self.free_investable_cash is None
            else _checked_money(self.free_investable_cash)
        )
        if unblocked is not None and (
            broker_total.minor_units - broker_blocked.minor_units
            != unblocked.minor_units
        ):
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        if type(self.overlap_disposition) is not OverlapDisposition:
            _fail(CL5Reason.TYPE_INVALID)
        if type(self.status) is not AvailabilityStatus:
            _fail(CL5Reason.TYPE_INVALID)
        if type(self.availability_reason) is not AvailabilityReason:
            _fail(CL5Reason.TYPE_INVALID)
        expected_overlap = (
            OverlapDisposition.NO_LOCAL_RESERVATION
            if total.minor_units == 0
            else (
                OverlapDisposition.AMBIGUOUS_PROVIDER_OVERLAP
                if ambiguous.minor_units > 0
                else OverlapDisposition.QUEUED_DISJOINT
            )
        )
        if self.overlap_disposition is not expected_overlap:
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        if self.status is AvailabilityStatus.READY:
            if (
                self.availability_reason is not AvailabilityReason.READY
                or unblocked is None
                or free is None
                or ambiguous.minor_units != 0
                or free.minor_units != unblocked.minor_units - queued.minor_units
                or free.minor_units < 0
            ):
                _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        elif self.status is AvailabilityStatus.MANUAL_REVIEW_REQUIRED:
            if (
                self.availability_reason
                is not AvailabilityReason.CENTRAL_PROVIDER_OVERLAP_UNKNOWN
                or ambiguous.minor_units <= 0
                or unblocked is None
                or free is not None
            ):
                _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        elif (
            self.availability_reason
            not in {
                AvailabilityReason.CL4_NOT_READY,
                AvailabilityReason.BROKER_PROOF_STALE,
                AvailabilityReason.CENTRAL_PROJECTION_STALE,
                AvailabilityReason.MIXED_EVIDENCE_SNAPSHOT,
                AvailabilityReason.BROKER_VIEW_MISMATCH,
                AvailabilityReason.FOREIGN_CASH_PRESENT,
                AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS,
            }
            or free is not None
        ):
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        if (
            self.availability_reason is AvailabilityReason.BROKER_VIEW_MISMATCH
            and unblocked is not None
        ):
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        if (
            self.availability_reason
            in {
                AvailabilityReason.FOREIGN_CASH_PRESENT,
                AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS,
            }
            and unblocked is None
        ):
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        if (
            self.availability_reason
            is AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS
            and (
                ambiguous.minor_units != 0
                or unblocked is None
                or unblocked.minor_units - queued.minor_units >= 0
            )
        ):
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        object.__setattr__(self, "broker_total_cash", broker_total)
        object.__setattr__(self, "broker_blocked_cash", broker_blocked)
        object.__setattr__(self, "broker_unblocked_cash", unblocked)
        object.__setattr__(self, "central_queued_reserved_cash", queued)
        object.__setattr__(self, "central_ambiguous_reserved_cash", ambiguous)
        object.__setattr__(self, "central_total_reserved_cash", total)
        object.__setattr__(self, "free_investable_cash", free)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "availability_reason": self.availability_reason.value,
            "broker_blocked_cash": _money_dict(self.broker_blocked_cash),
            "broker_cash_as_of": self.broker_cash_as_of,
            "broker_positions_as_of": self.broker_positions_as_of,
            "broker_positions_cash_proof_sha256": (
                self.broker_positions_cash_proof_sha256
            ),
            "broker_total_cash": _money_dict(self.broker_total_cash),
            "broker_unblocked_cash": (
                None
                if self.broker_unblocked_cash is None
                else _money_dict(self.broker_unblocked_cash)
            ),
            "central_ambiguous_reserved_cash": _money_dict(
                self.central_ambiguous_reserved_cash
            ),
            "central_order_revision": str(self.central_order_revision),
            "central_projection_evaluated_at": self.central_projection_evaluated_at,
            "central_queued_reserved_cash": _money_dict(
                self.central_queued_reserved_cash
            ),
            "central_reservation_projection_hash": (
                self.central_reservation_projection_hash
            ),
            "central_reservation_projection_sha256": (
                self.central_reservation_projection_sha256
            ),
            "central_total_reserved_cash": _money_dict(
                self.central_total_reserved_cash
            ),
            "cl4_adoption_candidate_sha256": self.cl4_adoption_candidate_sha256,
            "cl4_reconciliation_evaluated_at": self.cl4_reconciliation_evaluated_at,
            "currency": self.currency,
            "domain": "v3.10-cl5-cash-availability",
            "environment": self.environment.value,
            "evaluated_at": self.evaluated_at,
            "free_investable_cash": (
                None
                if self.free_investable_cash is None
                else _money_dict(self.free_investable_cash)
            ),
            "ledger_export_sha256": self.ledger_export_sha256,
            "ledger_head_sha256": self.ledger_head_sha256,
            "ledger_revision": str(self.ledger_revision),
            "overlap_disposition": self.overlap_disposition.value,
            "reconciliation_sha256": self.reconciliation_sha256,
            "status": self.status.value,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _parse_canonical_bytes(value: bytes, reason: CL5Reason) -> object:
    def reject_constant(_value: str) -> None:
        raise ValueError("constant")

    try:
        parsed = _json.loads(
            value.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=reject_constant,
        )
        if _canonical_bytes(parsed) != value:
            _fail(reason)
        return parsed
    except CL5Error:
        raise
    except Exception:  # noqa: BLE001 - canonical boundary
        failure = CL5Error(reason)
    raise failure from None


def _bounded_response(value: dict[str, object]) -> tuple[dict[str, object], bytes]:
    seen: set[int] = set()
    nodes = 0

    def visit(item: object, depth: int) -> object:
        nonlocal nodes
        nodes += 1
        if depth > _MAX_RESPONSE_DEPTH or nodes > _MAX_RESPONSE_NODES:
            _fail(CL5Reason.RESPONSE_BOUNDS_EXCEEDED)
        if type(item) is dict:
            identity = id(item)
            if identity in seen:
                _fail(CL5Reason.RESPONSE_BOUNDS_EXCEEDED)
            seen.add(identity)
            if len(item) > _MAX_MAPPING_KEYS:
                _fail(CL5Reason.RESPONSE_BOUNDS_EXCEEDED)
            detached: dict[str, object] = {}
            for key, child in item.items():
                if (
                    type(key) is not str
                    or len(key) > _MAX_KEY_SCALARS
                    or _contains_surrogate(key)
                ):
                    _fail(CL5Reason.RESPONSE_BOUNDS_EXCEEDED)
                detached[key] = visit(child, depth + 1)
            return detached
        if type(item) is list:
            identity = id(item)
            if identity in seen:
                _fail(CL5Reason.RESPONSE_BOUNDS_EXCEEDED)
            seen.add(identity)
            return [visit(child, depth + 1) for child in item]
        if type(item) is str:
            if len(item) > _MAX_STRING_SCALARS or _contains_surrogate(item):
                _fail(CL5Reason.RESPONSE_BOUNDS_EXCEEDED)
            return item
        if type(item) is bool or item is None:
            return item
        if type(item) is int:
            if not _INT64_MIN <= item <= _INT64_MAX:
                _fail(CL5Reason.RESPONSE_BOUNDS_EXCEEDED)
            return item
        _fail(CL5Reason.RESPONSE_BOUNDS_EXCEEDED)

    detached = visit(value, 1)
    if type(detached) is not dict:
        _fail(CL5Reason.RESPONSE_BOUNDS_EXCEEDED)
    try:
        canonical = _canonical_bytes(detached)
    except Exception:  # noqa: BLE001 - canonical boundary
        failure = CL5Error(CL5Reason.CANONICAL_FORMAT_INVALID)
    else:
        if len(canonical) > _MAX_RESPONSE_CANONICAL_BYTES:
            _fail(CL5Reason.RESPONSE_BOUNDS_EXCEEDED)
        parsed = _parse_canonical_bytes(canonical, CL5Reason.CANONICAL_FORMAT_INVALID)
        if type(parsed) is not dict:
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        return parsed, canonical
    raise failure from None


def _response_schema(
    value: dict[str, object],
) -> tuple[str, list[dict[str, object]], list[dict[str, object]]]:
    keys = frozenset(value)
    if not _REQUIRED_RESPONSE_KEYS.issubset(keys) or not keys.issubset(
        _REQUIRED_RESPONSE_KEYS | _IGNORED_RESPONSE_KEYS
    ):
        _fail(CL5Reason.RESPONSE_SCHEMA_INVALID)
    raw_account = value["accountId"]
    money = value["money"]
    blocked = value["blocked"]
    loading = value["limitsLoadingInProgress"]
    if (
        type(raw_account) is not str
        or not raw_account
        or type(money) is not list
        or type(blocked) is not list
        or type(loading) is not bool
    ):
        _fail(CL5Reason.RESPONSE_SCHEMA_INVALID)
    for key in _IGNORED_RESPONSE_KEYS & keys:
        if type(value[key]) is not list:
            _fail(CL5Reason.RESPONSE_SCHEMA_INVALID)
    for item in (*money, *blocked):
        if (
            type(item) is not dict
            or frozenset(item) != _MONEY_VALUE_KEYS
            or type(item["currency"]) is not str
            or type(item["units"]) is not str
            or type(item["nano"]) is not int
        ):
            _fail(CL5Reason.RESPONSE_SCHEMA_INVALID)
    return raw_account, money, blocked


def _rub_money(items: list[dict[str, object]]) -> tuple[_ledger.Money, bool]:
    rub = [item for item in items if item["currency"] == "RUB"]
    if len(rub) > 1:
        _fail(CL5Reason.RESPONSE_SCHEMA_INVALID)
    foreign = any(item["currency"] != "RUB" for item in items)
    if not rub:
        return _ledger.Money(currency="RUB", minor_units=0), foreign
    try:
        return _broker.money_value_to_money(rub[0]), foreign
    except _broker.BrokerReadError as error:
        failure = CL5Error(CL5Reason.MONEY_INVALID, error.reason)
    except Exception:  # noqa: BLE001 - accepted codec boundary
        failure = CL5Error(CL5Reason.MONEY_INVALID)
    raise failure from None


def build_broker_positions_cash_proof(
    response: object,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    as_of: str,
    evaluated_at: str,
    response_complete: bool,
    identity_key: bytes,
    identity_key_id: str,
) -> BrokerPositionsCashProof:
    if (
        type(response) is not dict
        or type(account_scope_sha256) is not str
        or type(as_of) is not str
        or type(evaluated_at) is not str
        or type(response_complete) is not bool
        or type(identity_key) is not bytes
        or type(identity_key_id) is not str
    ):
        _fail(CL5Reason.TYPE_INVALID)
    checked_environment = _require_environment(environment)
    _require_hash(account_scope_sha256, CL5Reason.ACCOUNT_SCOPE_INVALID)
    checked_key = _require_key(identity_key)
    checked_key_id = _require_key_id(identity_key_id)
    as_of_ns = _timestamp_ns(as_of)
    evaluated_ns = _timestamp_ns(evaluated_at)
    if response_complete is not True:
        _fail(CL5Reason.PROOF_INCOMPLETE)
    detached, response_bytes = _bounded_response(response)
    raw_account, money_items, blocked_items = _response_schema(detached)
    if detached["limitsLoadingInProgress"] is True:
        _fail(CL5Reason.POSITIONS_LOADING_IN_PROGRESS)
    expected_scope = _account_scope(
        raw_account,
        checked_environment,
        checked_key_id,
        checked_key,
    )
    if not _hmac.compare_digest(expected_scope, account_scope_sha256):
        _fail(CL5Reason.ACCOUNT_SCOPE_INVALID)
    positions, positions_foreign = _rub_money(money_items)
    blocked, blocked_foreign = _rub_money(blocked_items)
    if blocked.minor_units < 0:
        _fail(CL5Reason.MONEY_INVALID)
    response_sha = _sha256(response_bytes)
    _require_fresh(as_of_ns, evaluated_ns)
    foreign = positions_foreign or blocked_foreign
    identity = _positions_identity(
        account_scope_sha256=account_scope_sha256,
        as_of=as_of,
        blocked_rub=blocked,
        environment=checked_environment,
        foreign_cash_present=foreign,
        identity_key_id=checked_key_id,
        positions_money_rub=positions,
        response_canonical_sha256=response_sha,
        response_complete=True,
        identity_key=checked_key,
    )
    return BrokerPositionsCashProof(
        account_scope_sha256=account_scope_sha256,
        environment=checked_environment,
        as_of=as_of,
        positions_money_rub=positions,
        blocked_rub=blocked,
        foreign_cash_present=foreign,
        response_canonical_sha256=response_sha,
        proof_identity_sha256=identity,
        identity_key_id=checked_key_id,
    )


_CENTRAL_SPECS: dict[
    type[object],
    tuple[tuple[str, tuple[type[object], ...], tuple[type[object], ...] | None], ...],
] = {
    _central.CentralOrderState: (
        ("account_id", (str,), None),
        ("revision", (int,), None),
        ("next_sequence", (int,), None),
        ("intents", (tuple,), (_central.CentralOrderIntent,)),
        ("created_at", (str,), None),
        ("updated_at", (str,), None),
        ("version", (int,), None),
    ),
    _central.CentralOrderIntent: (
        ("intent_id", (str,), None),
        ("idempotency_key", (str,), None),
        ("queue_sequence", (int,), None),
        ("candidate", (_central.CentralOrderCandidate,), None),
        ("authorization", (_central.ExecutionAuthorization,), None),
        ("status", (str,), None),
        ("reserved_cash_kopecks", (int,), None),
        ("broker_order_id", (str, _NONE_TYPE), None),
        ("uncertainty_reason", (str, _NONE_TYPE), None),
        ("outcome", (str, _NONE_TYPE), None),
        ("executed_lots", (int,), None),
        ("reconciled_portfolio_revision", (int, _NONE_TYPE), None),
        ("reconciled_portfolio_decision_checksum", (str, _NONE_TYPE), None),
        ("reconciled_portfolio_snapshot_at", (str, _NONE_TYPE), None),
        ("risk_execution_status", (str, _NONE_TYPE), None),
        ("risk_execution_id", (str, _NONE_TYPE), None),
        ("created_at", (str,), None),
        ("updated_at", (str,), None),
        ("transitions", (tuple,), (_central.OrderTransition,)),
    ),
    _central.CentralOrderCandidate: (
        ("account_id", (str,), None),
        ("instrument_id", (str,), None),
        ("ticker", (str,), None),
        ("runtime_key", (str,), None),
        ("runtime_config_hash", (str,), None),
        ("candle_interval", (str,), None),
        ("candle_time", (str,), None),
        ("strategy_id", (str,), None),
        ("strategy_profile_hash", (str,), None),
        ("current_lots", (int,), None),
        ("target_lots", (int,), None),
        ("estimated_price_kopecks", (int,), None),
        ("lot_size", (int,), None),
        ("order_type", (str,), None),
        ("time_in_force", (str,), None),
        ("created_at", (str,), None),
    ),
    _central.ExecutionAuthorization: (
        ("account_id", (str,), None),
        ("instrument_id", (str,), None),
        ("authorized_target_lots", (int,), None),
        ("portfolio_revision", (int,), None),
        ("portfolio_decision_checksum", (str,), None),
        ("portfolio_document_checksum", (str,), None),
        ("available_cash_kopecks", (int,), None),
        ("preflight_status", (str,), None),
        ("pending_order_ids", (tuple,), (str,)),
        ("uncertain_order_ids", (tuple,), (str,)),
        ("risk_status", (str,), None),
        ("risk_decision_id", (str,), None),
        ("risk_policy_hash", (str,), None),
        ("risk_order_allowed", (bool,), None),
        ("authorized_at", (str,), None),
        ("risk_state_guard_hash", (str, _NONE_TYPE), None),
        (
            "portfolio_risk",
            (_central.PortfolioRiskAuthorizationProof, _NONE_TYPE),
            None,
        ),
    ),
    _central.PortfolioRiskAuthorizationProof: (
        ("decision_id", (str,), None),
        ("input_hash", (str,), None),
        ("policy_hash", (str,), None),
        ("status", (str,), None),
        ("single_risk_approved_target_lots", (int,), None),
        ("approved_target_lots", (int,), None),
        ("snapshot_revision", (int,), None),
        ("snapshot_checksum", (str,), None),
        ("central_order_revision", (int,), None),
        ("reservation_projection_hash", (str,), None),
        ("risk_state_guard_hash", (str,), None),
        ("evaluated_at", (str,), None),
        ("candidate_price_at", (str,), None),
        ("candidate_price_source", (str,), None),
        ("cash_buffer_bps", (int,), None),
        ("excluded_reservation_ids", (tuple,), (str,)),
        ("admission_central_revision", (int, _NONE_TYPE), None),
        ("admission_reservation_projection_hash", (str, _NONE_TYPE), None),
    ),
    _central.OrderTransition: (
        ("status", (str,), None),
        ("at", (str,), None),
        ("detail", (str,), None),
    ),
}


def _central_preflight(state: _central.CentralOrderState) -> None:
    nodes = 0

    def visit(value: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > _MAX_CENTRAL_STATE_DEPTH or nodes > _MAX_CENTRAL_STATE_NODES:
            _fail(CL5Reason.CENTRAL_STATE_INVALID)
        value_type = type(value)
        if value_type in _CENTRAL_SPECS:
            spec = _CENTRAL_SPECS[value_type]
            if tuple(field.name for field in _fields(value_type)) != tuple(
                name for name, _, _ in spec
            ):
                _fail(CL5Reason.CENTRAL_STATE_INVALID)
            for name, allowed, item_types in spec:
                child = object.__getattribute__(value, name)
                if type(child) not in allowed:
                    _fail(CL5Reason.CENTRAL_STATE_INVALID)
                if item_types is not None and any(
                    type(item) not in item_types for item in child
                ):
                    _fail(CL5Reason.CENTRAL_STATE_INVALID)
                if name == "intents" and len(child) > _MAX_CENTRAL_INTENTS:
                    _fail(CL5Reason.CENTRAL_STATE_INVALID)
                if (
                    name == "transitions"
                    and len(child) > _MAX_CENTRAL_TRANSITIONS_PER_INTENT
                ):
                    _fail(CL5Reason.CENTRAL_STATE_INVALID)
                visit(child, depth + 1)
            return
        if value_type is tuple:
            for child in value:
                visit(child, depth + 1)
            return
        if value_type is str:
            if len(value) > _MAX_CENTRAL_STRING_SCALARS or _contains_surrogate(value):
                _fail(CL5Reason.CENTRAL_STATE_INVALID)
            return
        if value_type is bool or value is None:
            return
        if value_type is int:
            if not _INT64_MIN <= value <= _INT64_MAX:
                _fail(CL5Reason.CENTRAL_STATE_INVALID)
            return
        _fail(CL5Reason.CENTRAL_STATE_INVALID)

    visit(state, 1)


def _validated_central_state(
    state: _central.CentralOrderState,
) -> _central.CentralOrderState:
    _central_preflight(state)
    try:
        raw = _central.CentralOrderState.to_dict(state)
        canonical = _canonical_bytes(raw)
        if len(canonical) > _MAX_CENTRAL_STATE_CANONICAL_BYTES:
            _fail(CL5Reason.CENTRAL_STATE_INVALID)
        parsed = _parse_canonical_bytes(canonical, CL5Reason.CENTRAL_STATE_INVALID)
        if type(parsed) is not dict:
            _fail(CL5Reason.CENTRAL_STATE_INVALID)
        checked = _central.CentralOrderState.from_dict(parsed)
        checked_bytes = _canonical_bytes(_central.CentralOrderState.to_dict(checked))
        if checked != state or checked_bytes != canonical:
            _fail(CL5Reason.CENTRAL_STATE_INVALID)
        return checked
    except CL5Error:
        raise
    except Exception:  # noqa: BLE001 - closed Central boundary
        failure = CL5Error(CL5Reason.CENTRAL_STATE_INVALID)
    raise failure from None


def _central_projection_hash(state: _central.CentralOrderState) -> str:
    try:
        return _central.central_reservation_projection_hash(
            state,
            excluded_reservation_ids=(),
        )
    except Exception:  # noqa: BLE001 - closed Central boundary
        failure = CL5Error(CL5Reason.CENTRAL_PROJECTION_INVALID)
    raise failure from None


def project_central_reservations(
    state: CentralOrderState,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    evaluated_at: str,
    identity_key: bytes,
    identity_key_id: str,
) -> CentralReservationProjection:
    if (
        type(state) is not _central.CentralOrderState
        or type(account_scope_sha256) is not str
        or type(evaluated_at) is not str
        or type(identity_key) is not bytes
        or type(identity_key_id) is not str
    ):
        _fail(CL5Reason.TYPE_INVALID)
    checked_environment = _require_environment(environment)
    _require_hash(account_scope_sha256, CL5Reason.ACCOUNT_SCOPE_INVALID)
    checked_key = _require_key(identity_key)
    checked_key_id = _require_key_id(identity_key_id)
    evaluated_ns = _timestamp_ns(evaluated_at)
    checked = _validated_central_state(state)
    if _central_timestamp_ns(checked.updated_at) > evaluated_ns:
        _fail(CL5Reason.DEPENDENCY_FROM_FUTURE)
    expected_scope = _account_scope(
        checked.account_id,
        checked_environment,
        checked_key_id,
        checked_key,
    )
    if not _hmac.compare_digest(expected_scope, account_scope_sha256):
        _fail(CL5Reason.CENTRAL_ACCOUNT_MISMATCH)
    projection_hash = _central_projection_hash(checked)
    queued_kopecks = 0
    ambiguous_kopecks = 0
    queued_count = 0
    ambiguous_count = 0
    for intent in checked.intents:
        if intent.status == "QUEUED":
            queued_kopecks += intent.reserved_cash_kopecks
            queued_count += 1
        elif intent.status in {"IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}:
            ambiguous_kopecks += intent.reserved_cash_kopecks
            ambiguous_count += 1
    queued = _money_from_minor_units(queued_kopecks * _KOPECK_TO_NANO)
    ambiguous = _money_from_minor_units(ambiguous_kopecks * _KOPECK_TO_NANO)
    total = _money_from_minor_units(queued.minor_units + ambiguous.minor_units)
    identity = _projection_identity(
        account_scope_sha256=account_scope_sha256,
        ambiguous_count=ambiguous_count,
        ambiguous_reserved_cash=ambiguous,
        central_order_revision=checked.revision,
        central_reservation_projection_hash=projection_hash,
        environment=checked_environment,
        evaluated_at=evaluated_at,
        identity_key_id=checked_key_id,
        queued_count=queued_count,
        queued_reserved_cash=queued,
        total_reserved_cash=total,
        identity_key=checked_key,
    )
    return CentralReservationProjection(
        account_scope_sha256=account_scope_sha256,
        environment=checked_environment,
        central_order_revision=checked.revision,
        central_reservation_projection_hash=projection_hash,
        queued_reserved_cash=queued,
        ambiguous_reserved_cash=ambiguous,
        total_reserved_cash=total,
        queued_count=queued_count,
        ambiguous_count=ambiguous_count,
        evaluated_at=evaluated_at,
        identity_key_id=checked_key_id,
        projection_identity_sha256=identity,
    )


def _validated_positions_proof(
    value: BrokerPositionsCashProof,
    identity_key: bytes,
) -> BrokerPositionsCashProof:
    try:
        checked = BrokerPositionsCashProof(
            account_scope_sha256=value.account_scope_sha256,
            environment=value.environment,
            as_of=value.as_of,
            positions_money_rub=value.positions_money_rub,
            blocked_rub=value.blocked_rub,
            foreign_cash_present=value.foreign_cash_present,
            response_canonical_sha256=value.response_canonical_sha256,
            proof_identity_sha256=value.proof_identity_sha256,
            identity_key_id=value.identity_key_id,
            response_complete=value.response_complete,
            version=value.version,
        )
        expected = _positions_identity(
            account_scope_sha256=checked.account_scope_sha256,
            as_of=checked.as_of,
            blocked_rub=checked.blocked_rub,
            environment=checked.environment,
            foreign_cash_present=checked.foreign_cash_present,
            identity_key_id=checked.identity_key_id,
            positions_money_rub=checked.positions_money_rub,
            response_canonical_sha256=checked.response_canonical_sha256,
            response_complete=checked.response_complete,
            identity_key=identity_key,
        )
        if checked.canonical_bytes != value.canonical_bytes or not _hmac.compare_digest(
            expected, checked.proof_identity_sha256
        ):
            _fail(CL5Reason.PROOF_IDENTITY_INVALID)
        return checked
    except CL5Error:
        raise
    except Exception:  # noqa: BLE001 - forged DTO boundary
        failure = CL5Error(CL5Reason.PROOF_IDENTITY_INVALID)
    raise failure from None


def _validated_projection(
    value: CentralReservationProjection,
    identity_key: bytes,
) -> CentralReservationProjection:
    try:
        checked = CentralReservationProjection(
            account_scope_sha256=value.account_scope_sha256,
            environment=value.environment,
            central_order_revision=value.central_order_revision,
            central_reservation_projection_hash=value.central_reservation_projection_hash,
            queued_reserved_cash=value.queued_reserved_cash,
            ambiguous_reserved_cash=value.ambiguous_reserved_cash,
            total_reserved_cash=value.total_reserved_cash,
            queued_count=value.queued_count,
            ambiguous_count=value.ambiguous_count,
            evaluated_at=value.evaluated_at,
            identity_key_id=value.identity_key_id,
            projection_identity_sha256=value.projection_identity_sha256,
            version=value.version,
        )
        expected = _projection_identity(
            account_scope_sha256=checked.account_scope_sha256,
            ambiguous_count=checked.ambiguous_count,
            ambiguous_reserved_cash=checked.ambiguous_reserved_cash,
            central_order_revision=checked.central_order_revision,
            central_reservation_projection_hash=(
                checked.central_reservation_projection_hash
            ),
            environment=checked.environment,
            evaluated_at=checked.evaluated_at,
            identity_key_id=checked.identity_key_id,
            queued_count=checked.queued_count,
            queued_reserved_cash=checked.queued_reserved_cash,
            total_reserved_cash=checked.total_reserved_cash,
            identity_key=identity_key,
        )
        if checked.canonical_bytes != value.canonical_bytes or not _hmac.compare_digest(
            expected, checked.projection_identity_sha256
        ):
            _fail(CL5Reason.CENTRAL_PROJECTION_INVALID)
        return checked
    except CL5Error:
        raise
    except Exception:  # noqa: BLE001 - forged DTO boundary
        failure = CL5Error(CL5Reason.CENTRAL_PROJECTION_INVALID)
    raise failure from None


def _snapshot(
    *,
    reconciliation: _cl4.CashReconciliation,
    adoption: _cl4.AdoptionCandidate,
    positions: BrokerPositionsCashProof,
    reservations: CentralReservationProjection,
    evaluated_at: str,
    broker_unblocked_cash: _ledger.Money | None,
    overlap: OverlapDisposition,
    status: AvailabilityStatus,
    reason: AvailabilityReason,
    free: _ledger.Money | None,
) -> CashAvailabilitySnapshot:
    return CashAvailabilitySnapshot(
        account_scope_sha256=positions.account_scope_sha256,
        environment=positions.environment,
        currency="RUB",
        evaluated_at=evaluated_at,
        cl4_reconciliation_evaluated_at=reconciliation.evaluated_at,
        broker_cash_as_of=reconciliation.proof.as_of,
        broker_positions_as_of=positions.as_of,
        central_projection_evaluated_at=reservations.evaluated_at,
        reconciliation_sha256=reconciliation.sha256,
        cl4_adoption_candidate_sha256=adoption.sha256,
        ledger_export_sha256=reconciliation.projection.ledger_export_sha256,
        ledger_revision=reconciliation.projection.ledger_revision,
        ledger_head_sha256=reconciliation.projection.ledger_head_sha256,
        broker_positions_cash_proof_sha256=positions.sha256,
        broker_total_cash=reconciliation.broker_cash,
        broker_blocked_cash=positions.blocked_rub,
        broker_unblocked_cash=broker_unblocked_cash,
        central_reservation_projection_sha256=reservations.sha256,
        central_order_revision=reservations.central_order_revision,
        central_reservation_projection_hash=(
            reservations.central_reservation_projection_hash
        ),
        central_queued_reserved_cash=reservations.queued_reserved_cash,
        central_ambiguous_reserved_cash=reservations.ambiguous_reserved_cash,
        central_total_reserved_cash=reservations.total_reserved_cash,
        overlap_disposition=overlap,
        status=status,
        availability_reason=reason,
        free_investable_cash=free,
    )


def build_cash_availability(
    ledger_export_bytes: bytes,
    reconciliation: CashReconciliation,
    positions: BrokerPositionsCashProof,
    reservations: CentralReservationProjection,
    *,
    evaluated_at: str,
    identity_key: bytes,
) -> CashAvailabilitySnapshot:
    if (
        type(ledger_export_bytes) is not bytes
        or type(reconciliation) is not _cl4.CashReconciliation
        or type(positions) is not BrokerPositionsCashProof
        or type(reservations) is not CentralReservationProjection
        or type(evaluated_at) is not str
        or type(identity_key) is not bytes
    ):
        _fail(CL5Reason.TYPE_INVALID)
    evaluated_ns = _timestamp_ns(evaluated_at)
    checked_key = _require_key(identity_key)
    try:
        adoption = _cl4.build_adoption_candidate(
            reconciliation,
            ledger_export_bytes=ledger_export_bytes,
            identity_key=checked_key,
        )
    except _cl4.CL4Error as error:
        failure = CL5Error(CL5Reason.CL4_EVIDENCE_INVALID, error.reason)
    except Exception:  # noqa: BLE001 - accepted CL4 boundary
        failure = CL5Error(CL5Reason.CL4_EVIDENCE_INVALID)
    else:
        if type(adoption) is not _cl4.AdoptionCandidate:
            _fail(CL5Reason.CL4_EVIDENCE_INVALID)
        failure = None
    if failure is not None:
        raise failure from None
    checked_positions = _validated_positions_proof(positions, checked_key)
    checked_reservations = _validated_projection(reservations, checked_key)
    dependency_times = (
        _timestamp_ns(reconciliation.evaluated_at),
        _timestamp_ns(reconciliation.proof.as_of),
        _timestamp_ns(checked_positions.as_of),
        _timestamp_ns(checked_reservations.evaluated_at),
    )
    if any(value > evaluated_ns for value in dependency_times):
        _fail(CL5Reason.DEPENDENCY_FROM_FUTURE)
    if (
        reconciliation.proof.account_scope_sha256
        != checked_positions.account_scope_sha256
        or checked_reservations.account_scope_sha256
        != checked_positions.account_scope_sha256
    ):
        _fail(CL5Reason.ACCOUNT_SCOPE_INVALID)
    if (
        reconciliation.proof.environment is not checked_positions.environment
        or checked_reservations.environment is not checked_positions.environment
    ):
        _fail(CL5Reason.ENVIRONMENT_UNSUPPORTED)
    money_values = (
        reconciliation.broker_cash,
        checked_positions.positions_money_rub,
        checked_positions.blocked_rub,
        checked_reservations.queued_reserved_cash,
        checked_reservations.ambiguous_reserved_cash,
        checked_reservations.total_reserved_cash,
    )
    if any(value.currency != "RUB" for value in money_values):
        _fail(CL5Reason.CURRENCY_UNSUPPORTED)
    broker_view_total = _money_from_minor_units(
        checked_positions.positions_money_rub.minor_units
        + checked_positions.blocked_rub.minor_units
    )
    view_matches = (
        broker_view_total.canonical_bytes == reconciliation.broker_cash.canonical_bytes
    )
    broker_unblocked = None
    if view_matches:
        broker_unblocked = _money_from_minor_units(
            reconciliation.broker_cash.minor_units
            - checked_positions.blocked_rub.minor_units
        )
        if (
            broker_unblocked.canonical_bytes
            != checked_positions.positions_money_rub.canonical_bytes
        ):
            view_matches = False
            broker_unblocked = None
    broker_cash_ns = dependency_times[1]
    positions_ns = dependency_times[2]
    reservations_ns = dependency_times[3]
    broker_stale = (
        evaluated_ns - broker_cash_ns > _MAX_PROOF_AGE_NS
        or evaluated_ns - positions_ns > _MAX_PROOF_AGE_NS
    )
    reservations_stale = evaluated_ns - reservations_ns > _MAX_PROOF_AGE_NS
    mixed = (
        max(broker_cash_ns, positions_ns, reservations_ns)
        - min(broker_cash_ns, positions_ns, reservations_ns)
        > _MAX_CROSS_PROOF_SKEW_NS
    )
    if checked_reservations.total_reserved_cash.minor_units == 0:
        overlap = OverlapDisposition.NO_LOCAL_RESERVATION
    elif checked_reservations.ambiguous_reserved_cash.minor_units > 0:
        overlap = OverlapDisposition.AMBIGUOUS_PROVIDER_OVERLAP
    else:
        overlap = OverlapDisposition.QUEUED_DISJOINT
    candidate_free = None
    if (
        broker_unblocked is not None
        and checked_reservations.ambiguous_reserved_cash.minor_units == 0
    ):
        candidate_free = _money_from_minor_units(
            broker_unblocked.minor_units
            - checked_reservations.queued_reserved_cash.minor_units
        )
    if (
        adoption.disposition
        is not _cl4.AdoptionDisposition.SEPARATE_LOCKED_REVIEW_REQUIRED
    ):
        status = AvailabilityStatus.BLOCKED
        reason = AvailabilityReason.CL4_NOT_READY
    elif broker_stale:
        status = AvailabilityStatus.BLOCKED
        reason = AvailabilityReason.BROKER_PROOF_STALE
    elif reservations_stale:
        status = AvailabilityStatus.BLOCKED
        reason = AvailabilityReason.CENTRAL_PROJECTION_STALE
    elif mixed:
        status = AvailabilityStatus.BLOCKED
        reason = AvailabilityReason.MIXED_EVIDENCE_SNAPSHOT
    elif not view_matches:
        status = AvailabilityStatus.BLOCKED
        reason = AvailabilityReason.BROKER_VIEW_MISMATCH
    elif checked_positions.foreign_cash_present:
        status = AvailabilityStatus.BLOCKED
        reason = AvailabilityReason.FOREIGN_CASH_PRESENT
    elif checked_reservations.ambiguous_reserved_cash.minor_units > 0:
        status = AvailabilityStatus.MANUAL_REVIEW_REQUIRED
        reason = AvailabilityReason.CENTRAL_PROVIDER_OVERLAP_UNKNOWN
    elif candidate_free is None or candidate_free.minor_units < 0:
        status = AvailabilityStatus.BLOCKED
        reason = AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS
    else:
        status = AvailabilityStatus.READY
        reason = AvailabilityReason.READY
    free = candidate_free if status is AvailabilityStatus.READY else None
    return _snapshot(
        reconciliation=reconciliation,
        adoption=adoption,
        positions=checked_positions,
        reservations=checked_reservations,
        evaluated_at=evaluated_at,
        broker_unblocked_cash=broker_unblocked,
        overlap=overlap,
        status=status,
        reason=reason,
        free=free,
    )
