"""Pure CL6 reporting and Portfolio/Risk cash-context proofs.

The module is intentionally read-only.  It validates accepted CL1-CL5 evidence,
builds immutable reporting artifacts, and prepares a cash context that still
requires a future locked revalidation before any economic mutation.
"""

from __future__ import annotations

import hashlib as _hashlib
import hmac as _hmac
import json as _json
import math as _math
import re as _re
from collections.abc import Mapping as _Mapping
from dataclasses import asdict as _asdict
from dataclasses import dataclass as _dataclass
from dataclasses import fields as _fields
from dataclasses import replace as _replace
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from datetime import timezone as _timezone
from decimal import ROUND_HALF_EVEN as _ROUND_HALF_EVEN
from decimal import Decimal as _Decimal
from decimal import DecimalException as _DecimalException
from decimal import localcontext as _localcontext
from enum import StrEnum as _StrEnum
from fractions import Fraction as _Fraction
from itertools import pairwise as _pairwise
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

from trading_robot import broker_read_adapters as _broker
from trading_robot import cash_availability as _cl5
from trading_robot import cash_ledger_domain as _ledger
from trading_robot import cash_ledger_opening_reconciliation as _cl4
from trading_robot import cash_ledger_persistence as _persistence
from trading_robot import portfolio_model as _portfolio
from trading_robot import portfolio_preflight as _portfolio_preflight
from trading_robot import risk as _risk
from trading_robot import risk_runtime as _risk_runtime

if _TYPE_CHECKING:
    from trading_robot.broker_read_adapters import BrokerEnvironment
    from trading_robot.cash_availability import (
        BrokerPositionsCashProof,
        CashAvailabilitySnapshot,
        CentralReservationProjection,
    )
    from trading_robot.cash_ledger_domain import Money
    from trading_robot.cash_ledger_opening_reconciliation import CashReconciliation
    from trading_robot.portfolio_preflight import PortfolioSnapshotLease
    from trading_robot.risk import RiskPolicy, RiskState

del annotations

__all__ = (
    "CL6Error",
    "CL6Reason",
    "CashFlowSummary",
    "MetricReason",
    "MetricStatus",
    "PerformanceReport",
    "PortfolioIdentityEvidence",
    "PortfolioRiskCashContext",
    "PortfolioValuationPoint",
    "ReportStatus",
    "ReportingCategory",
    "RiskCashContextReason",
    "RiskCashContextStatus",
    "RiskGuardEvidence",
    "TWRResult",
    "ValuationPhase",
    "XIRRResult",
    "build_performance_report",
    "build_portfolio_identity_evidence",
    "build_portfolio_risk_cash_context",
    "build_portfolio_valuation_point",
    "build_risk_guard_evidence",
)

CL6_CONTRACT_VERSION = 1
PORTFOLIO_VALUATION_POINT_VERSION = 1
CASH_FLOW_SUMMARY_VERSION = 1
TWR_RESULT_VERSION = 1
XIRR_RESULT_VERSION = 1
PERFORMANCE_REPORT_VERSION = 1
PORTFOLIO_IDENTITY_EVIDENCE_VERSION = 1
RISK_GUARD_EVIDENCE_VERSION = 1
PORTFOLIO_RISK_CASH_CONTEXT_VERSION = 1
CL6_CROSS_LANGUAGE_FIXTURE_VERSION = 1

MAX_LEDGER_EXPORT_BYTES = 16_777_216
MAX_LEDGER_TRANSACTIONS = 100_000
MAX_VALUATION_POINTS = 10_000
MAX_EXTERNAL_FLOW_TIMESTAMPS = 10_000
MAX_RATIONAL_DECIMAL_DIGITS = 4_096
MAX_PORTFOLIO_POSITIONS = 100_000
MAX_PORTFOLIO_PENDING_ORDERS_TOTAL = 100_000
MAX_PORTFOLIO_GRAPH_ITEMS_TOTAL = 500_000
MAX_PORTFOLIO_CANONICAL_BYTES = 16_777_216
MAX_RISK_HALTS = 10_000
MAX_RECORDED_EXECUTION_IDS = 512
MAX_RISK_ASSET_CLASS_LIMITS = 10_000
MAX_RISK_CANONICAL_BYTES = 1_048_576
MAX_STRING_SCALARS = 4_096
MAX_REVISION = 9_223_372_036_854_775_807
MAX_CONTEXT_AGE_NS = 120_000_000_000
MAX_CONTEXT_SKEW_NS = 10_000_000_000
XIRR_DECIMAL_PRECISION = 80
XIRR_MAX_ITERATIONS = 256
XIRR_ROOT_INTERVAL_EPSILON = _Decimal("1E-24")
XIRR_MIN_RATE = _Decimal("-0.999999999")
XIRR_MAX_RATE = _Decimal(1000)
XIRR_OUTPUT_DECIMALS = 12

_HASH_RE = _re.compile(r"[0-9a-f]{64}", _re.ASCII)
_TOKEN_RE = _re.compile(r"[A-Z][A-Z0-9_]{0,63}", _re.ASCII)
_TIMESTAMP_RE = _re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{9})Z",
    _re.ASCII,
)
_PORTFOLIO_TIMESTAMP_RE = _re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]{1,6}))?(Z|[+-][0-9]{2}:[0-9]{2})",
    _re.ASCII,
)
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_NONE_TYPE = type(None)


class ValuationPhase(_StrEnum):
    PERIOD_START = "PERIOD_START"
    PRE_EXTERNAL_FLOW = "PRE_EXTERNAL_FLOW"
    PERIOD_END = "PERIOD_END"


class ReportingCategory(_StrEnum):
    OPENING = "OPENING"
    EXTERNAL_FLOW = "EXTERNAL_FLOW"
    INVESTMENT_INCOME = "INVESTMENT_INCOME"
    EXPENSE = "EXPENSE"
    INTERNAL_SETTLEMENT = "INTERNAL_SETTLEMENT"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"


class MetricStatus(_StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    AMBIGUOUS = "AMBIGUOUS"


class MetricReason(_StrEnum):
    AVAILABLE = "AVAILABLE"
    LEDGER_INCOMPLETE = "LEDGER_INCOMPLETE"
    VALUATION_MISSING = "VALUATION_MISSING"
    NON_POSITIVE_SUBPERIOD_BASE = "NON_POSITIVE_SUBPERIOD_BASE"
    END_FLOW_VALUATION_AMBIGUOUS = "END_FLOW_VALUATION_AMBIGUOUS"
    MANUAL_ADJUSTMENT_PRESENT = "MANUAL_ADJUSTMENT_PRESENT"
    OPENING_INSIDE_PERIOD = "OPENING_INSIDE_PERIOD"
    RATIONAL_LIMIT_EXCEEDED = "RATIONAL_LIMIT_EXCEEDED"
    XIRR_NO_SIGN_CHANGE = "XIRR_NO_SIGN_CHANGE"
    XIRR_MULTIPLE_SIGN_CHANGES = "XIRR_MULTIPLE_SIGN_CHANGES"
    XIRR_ROOT_OUT_OF_RANGE = "XIRR_ROOT_OUT_OF_RANGE"
    XIRR_NUMERIC_FAILURE = "XIRR_NUMERIC_FAILURE"


class ReportStatus(_StrEnum):
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"


class RiskCashContextStatus(_StrEnum):
    READY_FOR_LOCKED_REVALIDATION = "READY_FOR_LOCKED_REVALIDATION"
    BLOCKED = "BLOCKED"


class RiskCashContextReason(_StrEnum):
    READY = "READY"
    CASH_AVAILABILITY_NOT_READY = "CASH_AVAILABILITY_NOT_READY"
    CASH_AVAILABILITY_STALE = "CASH_AVAILABILITY_STALE"
    PORTFOLIO_NOT_READY = "PORTFOLIO_NOT_READY"
    PORTFOLIO_STALE = "PORTFOLIO_STALE"
    RISK_GUARD_STALE = "RISK_GUARD_STALE"
    MIXED_EVIDENCE_SNAPSHOT = "MIXED_EVIDENCE_SNAPSHOT"


class CL6Reason(_StrEnum):
    TYPE_INVALID = "TYPE_INVALID"
    VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
    ENVIRONMENT_UNSUPPORTED = "ENVIRONMENT_UNSUPPORTED"
    CURRENCY_UNSUPPORTED = "CURRENCY_UNSUPPORTED"
    ACCOUNT_SCOPE_INVALID = "ACCOUNT_SCOPE_INVALID"
    IDENTITY_KEY_INVALID = "IDENTITY_KEY_INVALID"
    TIMESTAMP_INVALID = "TIMESTAMP_INVALID"
    PERIOD_INVALID = "PERIOD_INVALID"
    LEDGER_EXPORT_INVALID = "LEDGER_EXPORT_INVALID"
    LEDGER_GRAPH_INVALID = "LEDGER_GRAPH_INVALID"
    LEDGER_IDENTITY_MISMATCH = "LEDGER_IDENTITY_MISMATCH"
    VALUATION_INVALID = "VALUATION_INVALID"
    VALUATION_IDENTITY_INVALID = "VALUATION_IDENTITY_INVALID"
    VALUATION_SET_INVALID = "VALUATION_SET_INVALID"
    PORTFOLIO_EVIDENCE_INVALID = "PORTFOLIO_EVIDENCE_INVALID"
    RISK_EVIDENCE_INVALID = "RISK_EVIDENCE_INVALID"
    CASH_AVAILABILITY_INVALID = "CASH_AVAILABILITY_INVALID"
    EVIDENCE_CORRELATION_INVALID = "EVIDENCE_CORRELATION_INVALID"
    DEPENDENCY_FROM_FUTURE = "DEPENDENCY_FROM_FUTURE"
    ARITHMETIC_OVERFLOW = "ARITHMETIC_OVERFLOW"
    NUMERIC_BOUND_EXCEEDED = "NUMERIC_BOUND_EXCEEDED"
    CANONICAL_FORMAT_INVALID = "CANONICAL_FORMAT_INVALID"
    INTERNAL_BOUNDARY_FAILED = "INTERNAL_BOUNDARY_FAILED"


_CAUSE_TYPES = (
    _ledger.MoneyReason,
    _ledger.LedgerReason,
    _persistence.PersistenceReason,
    _cl4.CL4Reason,
    _cl5.CL5Reason,
)


class CL6Error(RuntimeError):
    """Closed CL6 failure with finite privacy-safe evidence."""

    def __init__(
        self,
        reason: CL6Reason,
        cause_reason: object | None = None,
        evidence: _Mapping[str, str] | None = None,
    ) -> None:
        if type(reason) is not CL6Reason:
            reason = CL6Reason.INTERNAL_BOUNDARY_FAILED
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
                    key.endswith("_sha256")
                    and type(value) is str
                    and _HASH_RE.fullmatch(value) is not None
                ) or (
                    key in {"revision", "stage"}
                    and type(value) is str
                    and len(value) <= 64
                    and not _contains_surrogate(value)
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
        return f"CL6Error(reason={self.reason.value}{suffix})"


def _fail(
    reason: CL6Reason,
    cause_reason: object | None = None,
    **evidence: str,
) -> None:
    raise CL6Error(reason, cause_reason, evidence) from None


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _canonical_bytes(value: object) -> bytes:
    try:
        return _json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail(CL6Reason.CANONICAL_FORMAT_INVALID)


def _sha256(value: bytes) -> str:
    return _hashlib.sha256(value).hexdigest()


def _hmac_sha256(identity_key: bytes, value: object) -> str:
    return _hmac.new(identity_key, _canonical_bytes(value), _hashlib.sha256).hexdigest()


def _is_hash(value: object) -> bool:
    return type(value) is str and _HASH_RE.fullmatch(value) is not None


def _require_hash(value: object, reason: CL6Reason) -> str:
    if not _is_hash(value):
        _fail(reason)
    return value


def _require_string(value: object, reason: CL6Reason) -> str:
    if (
        type(value) is not str
        or len(value) > MAX_STRING_SCALARS
        or _contains_surrogate(value)
    ):
        _fail(reason)
    return value


def _require_key(value: object) -> bytes:
    if type(value) is not bytes or not 32 <= len(value) <= 64:
        _fail(CL6Reason.IDENTITY_KEY_INVALID)
    return value


def _require_key_id(value: object) -> str:
    if (
        type(value) is not str
        or _contains_surrogate(value)
        or _TOKEN_RE.fullmatch(value) is None
    ):
        _fail(CL6Reason.IDENTITY_KEY_INVALID)
    return value


def _require_environment(value: object) -> _broker.BrokerEnvironment:
    if (
        type(value) is not _broker.BrokerEnvironment
        or value is not _broker.BrokerEnvironment.SANDBOX
    ):
        _fail(CL6Reason.ENVIRONMENT_UNSUPPORTED)
    return value


def _timestamp_ns(
    value: object, reason: CL6Reason = CL6Reason.TIMESTAMP_INVALID
) -> int:
    if type(value) is not str:
        _fail(reason)
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        _fail(reason)
    year, month, day, hour, minute, second, fraction = map(int, match.groups())
    try:
        ordinal = _date(year, month, day).toordinal()
    except ValueError:
        _fail(reason)
    if hour > 23 or minute > 59 or second > 59:
        _fail(reason)
    seconds = (((ordinal * 24) + hour) * 60 + minute) * 60 + second
    return seconds * 1_000_000_000 + fraction


def _normalize_portfolio_timestamp(value: object) -> tuple[str, int]:
    if type(value) is not str:
        _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
    match = _PORTFOLIO_TIMESTAMP_RE.fullmatch(value)
    if match is None:
        _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    fraction = match.group(7) or ""
    offset = match.group(8)
    microsecond = int(fraction.ljust(6, "0")) if fraction else 0
    if hour > 23 or minute > 59 or second > 59:
        _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
    if offset == "Z":
        zone = _timezone.utc
    else:
        offset_hour = int(offset[1:3])
        offset_minute = int(offset[4:6])
        if offset_hour > 23 or offset_minute > 59:
            _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        delta = _timedelta(hours=offset_hour, minutes=offset_minute)
        if offset[0] == "-":
            delta = -delta
        try:
            zone = _timezone(delta)
        except ValueError:
            _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
    try:
        parsed = _datetime(year, month, day, hour, minute, second, microsecond, zone)
    except ValueError:
        _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
    utc = parsed.astimezone(_timezone.utc)
    normalized = utc.strftime("%Y-%m-%dT%H:%M:%S")
    normalized += f".{utc.microsecond * 1000:09d}Z"
    return normalized, _timestamp_ns(normalized)


def _require_revision(value: object, reason: CL6Reason) -> int:
    if type(value) is not int or not 0 <= value <= MAX_REVISION:
        _fail(reason)
    return value


def _money(value: object, reason: CL6Reason) -> _ledger.Money:
    if type(value) is not _ledger.Money:
        _fail(reason)
    try:
        checked = _ledger.Money(
            currency=object.__getattribute__(value, "currency"),
            minor_units=object.__getattribute__(value, "minor_units"),
            scale=object.__getattribute__(value, "scale"),
        )
    except _ledger.MoneyError as error:
        failure = CL6Error(reason, error.reason)
    except Exception:  # noqa: BLE001 - forged Money boundary
        failure = CL6Error(reason)
    else:
        return checked
    raise failure from None


def _money_dict(value: _ledger.Money) -> dict[str, object]:
    return value.to_canonical_dict()


def _money_from_minor(value: int) -> _ledger.Money:
    try:
        return _ledger.Money(currency="RUB", minor_units=value)
    except _ledger.MoneyError as error:
        failure = CL6Error(CL6Reason.ARITHMETIC_OVERFLOW, error.reason)
    raise failure from None


def _checked_minor_add(left: int, right: int) -> int:
    result = left + right
    if not _ledger.MONEY_MIN_MINOR_UNITS <= result <= _ledger.MONEY_MAX_MINOR_UNITS:
        _fail(CL6Reason.ARITHMETIC_OVERFLOW)
    return result


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


def _canonical_property(value: object) -> bytes:
    return _canonical_bytes(value.to_canonical_dict())


@_dataclass(frozen=True, slots=True)
class PortfolioValuationPoint:
    account_scope_sha256: str
    as_of: str
    environment: BrokerEnvironment
    identity_key_id: str
    phase: ValuationPhase
    portfolio_decision_checksum: str
    portfolio_document_checksum: str
    portfolio_revision: int
    source_sha256: str
    total_value: Money
    valuation_identity_sha256: str
    version: int = PORTFOLIO_VALUATION_POINT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != PORTFOLIO_VALUATION_POINT_VERSION
        ):
            _fail(CL6Reason.VERSION_UNSUPPORTED)
        _require_hash(self.account_scope_sha256, CL6Reason.ACCOUNT_SCOPE_INVALID)
        _timestamp_ns(self.as_of)
        _require_environment(self.environment)
        _require_key_id(self.identity_key_id)
        if type(self.phase) is not ValuationPhase:
            _fail(CL6Reason.VALUATION_INVALID)
        _require_hash(self.portfolio_decision_checksum, CL6Reason.VALUATION_INVALID)
        _require_hash(self.portfolio_document_checksum, CL6Reason.VALUATION_INVALID)
        _require_revision(self.portfolio_revision, CL6Reason.VALUATION_INVALID)
        _require_hash(self.source_sha256, CL6Reason.VALUATION_INVALID)
        checked = _money(self.total_value, CL6Reason.VALUATION_INVALID)
        if checked.currency != "RUB" or checked.minor_units < 0:
            _fail(CL6Reason.VALUATION_INVALID)
        _require_hash(
            self.valuation_identity_sha256, CL6Reason.VALUATION_IDENTITY_INVALID
        )
        object.__setattr__(self, "total_value", checked)

    def _identity_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "as_of": self.as_of,
            "domain": "v3.10-cl6-portfolio-valuation-point-identity",
            "environment": self.environment.value,
            "identity_key_id": self.identity_key_id,
            "phase": self.phase.value,
            "portfolio_decision_checksum": self.portfolio_decision_checksum,
            "portfolio_document_checksum": self.portfolio_document_checksum,
            "portfolio_revision": str(self.portfolio_revision),
            "source_sha256": self.source_sha256,
            "total_value": _money_dict(self.total_value),
            "version": self.version,
        }

    def to_canonical_dict(self) -> dict[str, object]:
        result = self._identity_dict()
        result["domain"] = "v3.10-cl6-portfolio-valuation-point"
        result["valuation_identity_sha256"] = self.valuation_identity_sha256
        return result

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_property(self)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


def _summary_values(value: CashFlowSummary) -> tuple[tuple[int, _ledger.Money], ...]:
    return (
        (value.opening_count, value.opening_cash_effect),
        (value.external_flow_count, value.external_flow_cash_effect),
        (value.investment_income_count, value.investment_income_cash_effect),
        (value.expense_count, value.expense_cash_effect),
        (value.internal_settlement_count, value.internal_settlement_cash_effect),
        (value.manual_adjustment_count, value.manual_adjustment_cash_effect),
    )


@_dataclass(frozen=True, slots=True)
class CashFlowSummary:
    transaction_count: int
    opening_count: int
    opening_cash_effect: Money
    external_flow_count: int
    external_flow_cash_effect: Money
    investment_income_count: int
    investment_income_cash_effect: Money
    expense_count: int
    expense_cash_effect: Money
    internal_settlement_count: int
    internal_settlement_cash_effect: Money
    manual_adjustment_count: int
    manual_adjustment_cash_effect: Money
    version: int = CASH_FLOW_SUMMARY_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != CASH_FLOW_SUMMARY_VERSION:
            _fail(CL6Reason.VERSION_UNSUPPORTED)
        if (
            type(self.transaction_count) is not int
            or not 0 <= self.transaction_count <= MAX_LEDGER_TRANSACTIONS
        ):
            _fail(CL6Reason.TYPE_INVALID)
        checked: list[tuple[int, _ledger.Money]] = []
        for count, money in _summary_values(self):
            if type(count) is not int or count < 0:
                _fail(CL6Reason.TYPE_INVALID)
            checked.append((count, _money(money, CL6Reason.TYPE_INVALID)))
        if sum(count for count, _ in checked) != self.transaction_count:
            _fail(CL6Reason.LEDGER_GRAPH_INVALID)
        if any(count == 0 and money.minor_units != 0 for count, money in checked):
            _fail(CL6Reason.LEDGER_GRAPH_INVALID)
        for name, (_, money) in zip(
            (
                "opening_cash_effect",
                "external_flow_cash_effect",
                "investment_income_cash_effect",
                "expense_cash_effect",
                "internal_settlement_cash_effect",
                "manual_adjustment_cash_effect",
            ),
            checked,
            strict=True,
        ):
            object.__setattr__(self, name, money)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "domain": "v3.10-cl6-cash-flow-summary",
            "expense_cash_effect": _money_dict(self.expense_cash_effect),
            "expense_count": self.expense_count,
            "external_flow_cash_effect": _money_dict(self.external_flow_cash_effect),
            "external_flow_count": self.external_flow_count,
            "internal_settlement_cash_effect": _money_dict(
                self.internal_settlement_cash_effect
            ),
            "internal_settlement_count": self.internal_settlement_count,
            "investment_income_cash_effect": _money_dict(
                self.investment_income_cash_effect
            ),
            "investment_income_count": self.investment_income_count,
            "manual_adjustment_cash_effect": _money_dict(
                self.manual_adjustment_cash_effect
            ),
            "manual_adjustment_count": self.manual_adjustment_count,
            "opening_cash_effect": _money_dict(self.opening_cash_effect),
            "opening_count": self.opening_count,
            "transaction_count": self.transaction_count,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_property(self)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class TWRResult:
    status: MetricStatus
    reason: MetricReason
    growth_numerator: int | None
    growth_denominator: int | None
    return_numerator: int | None
    return_denominator: int | None
    rate_decimal: str | None
    subperiod_count: int
    version: int = TWR_RESULT_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != TWR_RESULT_VERSION:
            _fail(CL6Reason.VERSION_UNSUPPORTED)
        if (
            type(self.status) is not MetricStatus
            or type(self.reason) is not MetricReason
        ):
            _fail(CL6Reason.TYPE_INVALID)
        if type(self.subperiod_count) is not int or self.subperiod_count < 0:
            _fail(CL6Reason.TYPE_INVALID)
        rational = (
            self.growth_numerator,
            self.growth_denominator,
            self.return_numerator,
            self.return_denominator,
        )
        if self.status is MetricStatus.AVAILABLE:
            if self.reason is not MetricReason.AVAILABLE:
                _fail(CL6Reason.TYPE_INVALID)
            if any(type(item) is not int for item in rational):
                _fail(CL6Reason.TYPE_INVALID)
            if self.growth_denominator <= 0 or self.return_denominator <= 0:
                _fail(CL6Reason.TYPE_INVALID)
            if (
                type(self.rate_decimal) is not str
                or _re.fullmatch(r"-?[0-9]+\.[0-9]{12}", self.rate_decimal, _re.ASCII)
                is None
            ):
                _fail(CL6Reason.TYPE_INVALID)
            growth = _Fraction(self.growth_numerator, self.growth_denominator)
            result = _Fraction(self.return_numerator, self.return_denominator)
            if (
                growth.numerator != self.growth_numerator
                or growth.denominator != self.growth_denominator
                or result.numerator != self.return_numerator
                or result.denominator != self.return_denominator
                or result != growth - 1
                or self.rate_decimal != _rate_string(result)
            ):
                _fail(CL6Reason.TYPE_INVALID)
        elif (
            self.status is not MetricStatus.UNAVAILABLE
            or self.reason is MetricReason.AVAILABLE
            or any(item is not None for item in rational)
            or self.rate_decimal is not None
        ):
            _fail(CL6Reason.TYPE_INVALID)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "domain": "v3.10-cl6-twr-result",
            "growth_denominator": (
                None
                if self.growth_denominator is None
                else str(self.growth_denominator)
            ),
            "growth_numerator": (
                None if self.growth_numerator is None else str(self.growth_numerator)
            ),
            "rate_decimal": self.rate_decimal,
            "reason": self.reason.value,
            "return_denominator": (
                None
                if self.return_denominator is None
                else str(self.return_denominator)
            ),
            "return_numerator": (
                None if self.return_numerator is None else str(self.return_numerator)
            ),
            "status": self.status.value,
            "subperiod_count": self.subperiod_count,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_property(self)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class XIRRResult:
    status: MetricStatus
    reason: MetricReason
    rate_decimal: str | None
    sign_change_count: int
    cash_flow_count: int
    day_count_basis: str = "ACT_365_FIXED"
    algorithm: str = "DECIMAL_BISECTION_V1"
    version: int = XIRR_RESULT_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != XIRR_RESULT_VERSION:
            _fail(CL6Reason.VERSION_UNSUPPORTED)
        if (
            type(self.status) is not MetricStatus
            or type(self.reason) is not MetricReason
        ):
            _fail(CL6Reason.TYPE_INVALID)
        if any(
            type(item) is not int or item < 0
            for item in (self.sign_change_count, self.cash_flow_count)
        ):
            _fail(CL6Reason.TYPE_INVALID)
        if (
            self.day_count_basis != "ACT_365_FIXED"
            or self.algorithm != "DECIMAL_BISECTION_V1"
        ):
            _fail(CL6Reason.VERSION_UNSUPPORTED)
        if self.status is MetricStatus.AVAILABLE:
            if self.reason is not MetricReason.AVAILABLE or self.sign_change_count != 1:
                _fail(CL6Reason.TYPE_INVALID)
            if (
                type(self.rate_decimal) is not str
                or _re.fullmatch(r"-?[0-9]+\.[0-9]{12}", self.rate_decimal, _re.ASCII)
                is None
            ):
                _fail(CL6Reason.TYPE_INVALID)
        elif self.status is MetricStatus.AMBIGUOUS:
            if (
                self.reason is not MetricReason.XIRR_MULTIPLE_SIGN_CHANGES
                or self.sign_change_count <= 1
                or self.rate_decimal is not None
            ):
                _fail(CL6Reason.TYPE_INVALID)
        elif (
            self.reason
            in {
                MetricReason.AVAILABLE,
                MetricReason.XIRR_MULTIPLE_SIGN_CHANGES,
            }
            or self.rate_decimal is not None
        ):
            _fail(CL6Reason.TYPE_INVALID)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "algorithm": self.algorithm,
            "cash_flow_count": self.cash_flow_count,
            "day_count_basis": self.day_count_basis,
            "domain": "v3.10-cl6-xirr-result",
            "rate_decimal": self.rate_decimal,
            "reason": self.reason.value,
            "sign_change_count": self.sign_change_count,
            "status": self.status.value,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_property(self)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class PerformanceReport:
    account_scope_sha256: str
    environment: BrokerEnvironment
    period_start: str
    period_end: str
    generated_at: str
    ledger_export_sha256: str
    ledger_revision: int
    ledger_head_sha256: str
    ledger_projection_sha256: str
    ledger_complete: bool
    ledger_incompleteness_kinds: tuple[str, ...]
    valuation_set_sha256: str
    cash_flow_summary: CashFlowSummary
    twr: TWRResult
    xirr: XIRRResult
    report_status: ReportStatus
    identity_key_id: str
    report_identity_sha256: str
    version: int = PERFORMANCE_REPORT_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != PERFORMANCE_REPORT_VERSION:
            _fail(CL6Reason.VERSION_UNSUPPORTED)
        _require_hash(self.account_scope_sha256, CL6Reason.ACCOUNT_SCOPE_INVALID)
        _require_environment(self.environment)
        period_start_ns = _timestamp_ns(self.period_start)
        period_end_ns = _timestamp_ns(self.period_end)
        generated_ns = _timestamp_ns(self.generated_at)
        if not period_start_ns < period_end_ns <= generated_ns:
            _fail(CL6Reason.PERIOD_INVALID)
        for value in (
            self.ledger_export_sha256,
            self.ledger_head_sha256,
            self.ledger_projection_sha256,
            self.valuation_set_sha256,
        ):
            _require_hash(value, CL6Reason.LEDGER_IDENTITY_MISMATCH)
        _require_revision(self.ledger_revision, CL6Reason.LEDGER_IDENTITY_MISMATCH)
        if type(self.ledger_complete) is not bool:
            _fail(CL6Reason.TYPE_INVALID)
        if type(self.ledger_incompleteness_kinds) is not tuple:
            _fail(CL6Reason.TYPE_INVALID)
        if (
            any(
                type(item) is not str
                or _TOKEN_RE.fullmatch(item) is None
                or _contains_surrogate(item)
                for item in self.ledger_incompleteness_kinds
            )
            or tuple(sorted(set(self.ledger_incompleteness_kinds)))
            != self.ledger_incompleteness_kinds
        ):
            _fail(CL6Reason.TYPE_INVALID)
        if type(self.cash_flow_summary) is not CashFlowSummary:
            _fail(CL6Reason.TYPE_INVALID)
        if type(self.twr) is not TWRResult or type(self.xirr) is not XIRRResult:
            _fail(CL6Reason.TYPE_INVALID)
        if type(self.report_status) is not ReportStatus:
            _fail(CL6Reason.TYPE_INVALID)
        complete = (
            self.ledger_complete
            and self.twr.status is MetricStatus.AVAILABLE
            and self.xirr.status is MetricStatus.AVAILABLE
        )
        expected = ReportStatus.COMPLETE if complete else ReportStatus.DEGRADED
        if self.report_status is not expected:
            _fail(CL6Reason.TYPE_INVALID)
        _require_key_id(self.identity_key_id)
        _require_hash(self.report_identity_sha256, CL6Reason.CANONICAL_FORMAT_INVALID)

    def _identity_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "cash_flow_summary": self.cash_flow_summary.to_canonical_dict(),
            "domain": "v3.10-cl6-performance-report-identity",
            "environment": self.environment.value,
            "generated_at": self.generated_at,
            "identity_key_id": self.identity_key_id,
            "ledger_complete": self.ledger_complete,
            "ledger_export_sha256": self.ledger_export_sha256,
            "ledger_head_sha256": self.ledger_head_sha256,
            "ledger_incompleteness_kinds": list(self.ledger_incompleteness_kinds),
            "ledger_projection_sha256": self.ledger_projection_sha256,
            "ledger_revision": str(self.ledger_revision),
            "period_end": self.period_end,
            "period_start": self.period_start,
            "report_status": self.report_status.value,
            "twr": self.twr.to_canonical_dict(),
            "valuation_set_sha256": self.valuation_set_sha256,
            "version": self.version,
            "xirr": self.xirr.to_canonical_dict(),
        }

    def to_canonical_dict(self) -> dict[str, object]:
        result = self._identity_dict()
        result["domain"] = "v3.10-cl6-performance-report"
        result["report_identity_sha256"] = self.report_identity_sha256
        return result

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_property(self)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


def _valuation_identity(
    *,
    account_scope_sha256: str,
    as_of: str,
    environment: _broker.BrokerEnvironment,
    identity_key_id: str,
    phase: ValuationPhase,
    portfolio_decision_checksum: str,
    portfolio_document_checksum: str,
    portfolio_revision: int,
    source_sha256: str,
    total_value: _ledger.Money,
    identity_key: bytes,
) -> str:
    return _hmac_sha256(
        identity_key,
        {
            "account_scope_sha256": account_scope_sha256,
            "as_of": as_of,
            "domain": "v3.10-cl6-portfolio-valuation-point-identity",
            "environment": environment.value,
            "identity_key_id": identity_key_id,
            "phase": phase.value,
            "portfolio_decision_checksum": portfolio_decision_checksum,
            "portfolio_document_checksum": portfolio_document_checksum,
            "portfolio_revision": str(portfolio_revision),
            "source_sha256": source_sha256,
            "total_value": _money_dict(total_value),
            "version": PORTFOLIO_VALUATION_POINT_VERSION,
        },
    )


def build_portfolio_valuation_point(
    total_value: Money,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    as_of: str,
    phase: ValuationPhase,
    portfolio_revision: int,
    portfolio_decision_checksum: str,
    portfolio_document_checksum: str,
    source_sha256: str,
    identity_key: bytes,
    identity_key_id: str,
) -> PortfolioValuationPoint:
    if (
        type(total_value) is not _ledger.Money
        or type(account_scope_sha256) is not str
        or type(as_of) is not str
        or type(portfolio_revision) is not int
        or type(portfolio_decision_checksum) is not str
        or type(portfolio_document_checksum) is not str
        or type(source_sha256) is not str
        or type(identity_key) is not bytes
        or type(identity_key_id) is not str
    ):
        _fail(CL6Reason.TYPE_INVALID)
    checked_environment = _require_environment(environment)
    account = _require_hash(account_scope_sha256, CL6Reason.ACCOUNT_SCOPE_INVALID)
    key = _require_key(identity_key)
    key_id = _require_key_id(identity_key_id)
    _timestamp_ns(as_of)
    if type(phase) is not ValuationPhase:
        _fail(CL6Reason.VALUATION_INVALID)
    revision = _require_revision(portfolio_revision, CL6Reason.VALUATION_INVALID)
    decision = _require_hash(portfolio_decision_checksum, CL6Reason.VALUATION_INVALID)
    document = _require_hash(portfolio_document_checksum, CL6Reason.VALUATION_INVALID)
    source = _require_hash(source_sha256, CL6Reason.VALUATION_INVALID)
    checked_money = _money(total_value, CL6Reason.VALUATION_INVALID)
    if checked_money.currency != "RUB" or checked_money.minor_units < 0:
        _fail(CL6Reason.VALUATION_INVALID)
    identity = _valuation_identity(
        account_scope_sha256=account,
        as_of=as_of,
        environment=checked_environment,
        identity_key_id=key_id,
        phase=phase,
        portfolio_decision_checksum=decision,
        portfolio_document_checksum=document,
        portfolio_revision=revision,
        source_sha256=source,
        total_value=checked_money,
        identity_key=key,
    )
    return PortfolioValuationPoint(
        account_scope_sha256=account,
        as_of=as_of,
        environment=checked_environment,
        identity_key_id=key_id,
        phase=phase,
        portfolio_decision_checksum=decision,
        portfolio_document_checksum=document,
        portfolio_revision=revision,
        source_sha256=source,
        total_value=checked_money,
        valuation_identity_sha256=identity,
    )


def _translate_cl4(error: _cl4.CL4Error) -> CL6Error:
    reason = error.reason
    if reason is _cl4.CL4Reason.LEDGER_EXPORT_INVALID:
        primary = CL6Reason.LEDGER_EXPORT_INVALID
    elif reason in {
        _cl4.CL4Reason.LEDGER_GRAPH_INVALID,
        _cl4.CL4Reason.LEDGER_REVISION_INVALID,
        _cl4.CL4Reason.OPENING_MISSING,
        _cl4.CL4Reason.OPENING_CONFLICT,
        _cl4.CL4Reason.BASELINE_STALE,
    }:
        primary = CL6Reason.LEDGER_GRAPH_INVALID
    elif reason is _cl4.CL4Reason.ACCOUNT_SCOPE_INVALID:
        primary = CL6Reason.ACCOUNT_SCOPE_INVALID
    elif reason is _cl4.CL4Reason.ENVIRONMENT_UNSUPPORTED:
        primary = CL6Reason.ENVIRONMENT_UNSUPPORTED
    elif reason is _cl4.CL4Reason.IDENTITY_KEY_INVALID:
        primary = CL6Reason.IDENTITY_KEY_INVALID
    elif reason is _cl4.CL4Reason.VERSION_UNSUPPORTED:
        primary = CL6Reason.VERSION_UNSUPPORTED
    elif reason is _cl4.CL4Reason.ARITHMETIC_OVERFLOW:
        primary = CL6Reason.ARITHMETIC_OVERFLOW
    else:
        primary = CL6Reason.INTERNAL_BOUNDARY_FAILED
    return CL6Error(primary, reason)


def _ledger_projection(
    ledger_export_bytes: bytes,
    *,
    account_scope_sha256: str,
    environment: _broker.BrokerEnvironment,
    generated_at: str,
    identity_key: bytes,
) -> _cl4.LedgerCashProjection:
    try:
        projection = _cl4.project_shadow_cash(
            ledger_export_bytes,
            account_scope_sha256=account_scope_sha256,
            environment=environment,
            as_of=generated_at,
            identity_key=identity_key,
        )
    except _cl4.CL4Error as error:
        failure = _translate_cl4(error)
    except Exception:  # noqa: BLE001 - accepted CL4 boundary
        failure = CL6Error(CL6Reason.INTERNAL_BOUNDARY_FAILED)
    else:
        if type(projection) is _cl4.LedgerCashProjection:
            return projection
        failure = CL6Error(CL6Reason.INTERNAL_BOUNDARY_FAILED)
    raise failure from None


def _parse_export_transactions(
    value: bytes,
) -> tuple[dict[str, object], tuple[_ledger.LedgerTransaction, ...]]:
    try:
        parsed = _persistence.parse_canonical_json(value)
    except _persistence.PersistenceError as error:
        failure = CL6Error(CL6Reason.LEDGER_EXPORT_INVALID, error.reason)
    except Exception:  # noqa: BLE001 - accepted parser boundary
        failure = CL6Error(CL6Reason.INTERNAL_BOUNDARY_FAILED)
    else:
        if type(parsed) is not dict:
            _fail(CL6Reason.LEDGER_EXPORT_INVALID)
        rows = parsed.get("transactions")
        if type(rows) is not list:
            _fail(CL6Reason.LEDGER_EXPORT_INVALID)
        if len(rows) > MAX_LEDGER_TRANSACTIONS:
            _fail(CL6Reason.NUMERIC_BOUND_EXCEEDED)
        transactions: list[_ledger.LedgerTransaction] = []
        for row in rows:
            if type(row) is not dict:
                _fail(CL6Reason.LEDGER_GRAPH_INVALID)
            encoded = row.get("canonical_json_ascii")
            if type(encoded) is not str or _contains_surrogate(encoded):
                _fail(CL6Reason.LEDGER_GRAPH_INVALID)
            try:
                raw = encoded.encode("ascii")
                material = _persistence.parse_canonical_json(raw)
                transaction = _ledger.LedgerTransaction.from_canonical_dict(material)
            except _persistence.PersistenceError as error:
                failure = CL6Error(CL6Reason.LEDGER_GRAPH_INVALID, error.reason)
                raise failure from None
            except _ledger.LedgerError as error:
                failure = CL6Error(CL6Reason.LEDGER_GRAPH_INVALID, error.reason)
                raise failure from None
            except (TypeError, ValueError, UnicodeError):
                _fail(CL6Reason.LEDGER_GRAPH_INVALID)
            if (
                raw != transaction.canonical_bytes
                or row.get("sha256") != transaction.sha256
                or row.get("source_sha256") != transaction.source_sha256
                or row.get("economic_sha256") != transaction.economic_sha256
            ):
                _fail(CL6Reason.LEDGER_IDENTITY_MISMATCH)
            transactions.append(transaction)
        return parsed, tuple(transactions)
    raise failure from None


def _validated_valuation(
    value: PortfolioValuationPoint,
    identity_key: bytes,
) -> PortfolioValuationPoint:
    try:
        checked = PortfolioValuationPoint(
            account_scope_sha256=object.__getattribute__(value, "account_scope_sha256"),
            as_of=object.__getattribute__(value, "as_of"),
            environment=object.__getattribute__(value, "environment"),
            identity_key_id=object.__getattribute__(value, "identity_key_id"),
            phase=object.__getattribute__(value, "phase"),
            portfolio_decision_checksum=object.__getattribute__(
                value, "portfolio_decision_checksum"
            ),
            portfolio_document_checksum=object.__getattribute__(
                value, "portfolio_document_checksum"
            ),
            portfolio_revision=object.__getattribute__(value, "portfolio_revision"),
            source_sha256=object.__getattribute__(value, "source_sha256"),
            total_value=object.__getattribute__(value, "total_value"),
            valuation_identity_sha256=object.__getattribute__(
                value, "valuation_identity_sha256"
            ),
            version=object.__getattribute__(value, "version"),
        )
        expected = _valuation_identity(
            account_scope_sha256=checked.account_scope_sha256,
            as_of=checked.as_of,
            environment=checked.environment,
            identity_key_id=checked.identity_key_id,
            phase=checked.phase,
            portfolio_decision_checksum=checked.portfolio_decision_checksum,
            portfolio_document_checksum=checked.portfolio_document_checksum,
            portfolio_revision=checked.portfolio_revision,
            source_sha256=checked.source_sha256,
            total_value=checked.total_value,
            identity_key=identity_key,
        )
        if not _hmac.compare_digest(expected, checked.valuation_identity_sha256):
            _fail(CL6Reason.VALUATION_IDENTITY_INVALID)
        if checked.canonical_bytes != value.canonical_bytes:
            _fail(CL6Reason.VALUATION_IDENTITY_INVALID)
        return checked
    except CL6Error:
        raise
    except Exception:  # noqa: BLE001 - forged valuation boundary
        failure = CL6Error(CL6Reason.VALUATION_IDENTITY_INVALID)
    raise failure from None


_CATEGORY_BY_CLASSIFICATION = {
    _ledger.LedgerClassification.OPENING_BALANCE: ReportingCategory.OPENING,
    _ledger.LedgerClassification.DEPOSIT: ReportingCategory.EXTERNAL_FLOW,
    _ledger.LedgerClassification.WITHDRAWAL: ReportingCategory.EXTERNAL_FLOW,
    _ledger.LedgerClassification.DIVIDEND: ReportingCategory.INVESTMENT_INCOME,
    _ledger.LedgerClassification.COUPON: ReportingCategory.INVESTMENT_INCOME,
    _ledger.LedgerClassification.INTEREST: ReportingCategory.INVESTMENT_INCOME,
    _ledger.LedgerClassification.COMMISSION: ReportingCategory.EXPENSE,
    _ledger.LedgerClassification.TAX: ReportingCategory.EXPENSE,
    _ledger.LedgerClassification.REFUND: ReportingCategory.EXPENSE,
    _ledger.LedgerClassification.TRADE_SETTLEMENT: (
        ReportingCategory.INTERNAL_SETTLEMENT
    ),
    _ledger.LedgerClassification.MANUAL_ADJUSTMENT: (
        ReportingCategory.MANUAL_ADJUSTMENT
    ),
}


def _reporting_category(
    transaction: _ledger.LedgerTransaction,
    by_hash: dict[str, _ledger.LedgerTransaction],
) -> ReportingCategory:
    if transaction.classification is not _ledger.LedgerClassification.REVERSAL:
        category = _CATEGORY_BY_CLASSIFICATION.get(transaction.classification)
        if category is None:
            _fail(CL6Reason.LEDGER_GRAPH_INVALID)
        return category
    target_hash = transaction.reversal_of_sha256
    target = by_hash.get(target_hash or "")
    if target is None or target.classification is _ledger.LedgerClassification.REVERSAL:
        _fail(CL6Reason.LEDGER_GRAPH_INVALID)
    if target.source.account_scope_sha256 != transaction.source.account_scope_sha256:
        _fail(CL6Reason.ACCOUNT_SCOPE_INVALID)
    category = _CATEGORY_BY_CLASSIFICATION.get(target.classification)
    if category is None:
        _fail(CL6Reason.LEDGER_GRAPH_INVALID)
    return category


def _cash_effect(transaction: _ledger.LedgerTransaction) -> int:
    matches = tuple(
        posting
        for posting in transaction.postings
        if posting.account is _ledger.LedgerAccount.ASSET_BROKER_CASH
    )
    if len(matches) != 1:
        _fail(CL6Reason.LEDGER_GRAPH_INVALID)
    checked = _money(matches[0].money, CL6Reason.LEDGER_GRAPH_INVALID)
    return checked.minor_units


def _valuation_set_sha(
    points: tuple[PortfolioValuationPoint, ...],
    *,
    account_scope_sha256: str,
    environment: _broker.BrokerEnvironment,
    identity_key_id: str,
    period_start: str,
    period_end: str,
) -> str:
    phase_order = {
        ValuationPhase.PERIOD_START: 0,
        ValuationPhase.PRE_EXTERNAL_FLOW: 1,
        ValuationPhase.PERIOD_END: 2,
    }
    ordered = sorted(points, key=lambda item: (phase_order[item.phase], item.as_of))
    return _sha256(
        _canonical_bytes(
            {
                "account_scope_sha256": account_scope_sha256,
                "domain": "v3.10-cl6-valuation-set",
                "environment": environment.value,
                "identity_key_id": identity_key_id,
                "period_end": period_end,
                "period_start": period_start,
                "point_sha256": [item.sha256 for item in ordered],
                "version": 1,
            }
        )
    )


def _summary(
    rows: tuple[tuple[_ledger.LedgerTransaction, ReportingCategory, int], ...],
) -> CashFlowSummary:
    counts = {category: 0 for category in ReportingCategory}
    amounts = {category: 0 for category in ReportingCategory}
    for _, category, effect in rows:
        counts[category] += 1
        amounts[category] = _checked_minor_add(amounts[category], effect)
    return CashFlowSummary(
        transaction_count=len(rows),
        opening_count=counts[ReportingCategory.OPENING],
        opening_cash_effect=_money_from_minor(amounts[ReportingCategory.OPENING]),
        external_flow_count=counts[ReportingCategory.EXTERNAL_FLOW],
        external_flow_cash_effect=_money_from_minor(
            amounts[ReportingCategory.EXTERNAL_FLOW]
        ),
        investment_income_count=counts[ReportingCategory.INVESTMENT_INCOME],
        investment_income_cash_effect=_money_from_minor(
            amounts[ReportingCategory.INVESTMENT_INCOME]
        ),
        expense_count=counts[ReportingCategory.EXPENSE],
        expense_cash_effect=_money_from_minor(amounts[ReportingCategory.EXPENSE]),
        internal_settlement_count=counts[ReportingCategory.INTERNAL_SETTLEMENT],
        internal_settlement_cash_effect=_money_from_minor(
            amounts[ReportingCategory.INTERNAL_SETTLEMENT]
        ),
        manual_adjustment_count=counts[ReportingCategory.MANUAL_ADJUSTMENT],
        manual_adjustment_cash_effect=_money_from_minor(
            amounts[ReportingCategory.MANUAL_ADJUSTMENT]
        ),
    )


def _unavailable_twr(reason: MetricReason, subperiod_count: int) -> TWRResult:
    return TWRResult(
        status=MetricStatus.UNAVAILABLE,
        reason=reason,
        growth_numerator=None,
        growth_denominator=None,
        return_numerator=None,
        return_denominator=None,
        rate_decimal=None,
        subperiod_count=subperiod_count,
    )


def _too_many_digits(value: int) -> bool:
    magnitude = abs(value)
    if magnitude.bit_length() > 13_610:
        return True
    return len(str(magnitude)) > MAX_RATIONAL_DECIMAL_DIGITS


def _fraction_bounded(value: _Fraction) -> bool:
    return not (
        _too_many_digits(value.numerator) or _too_many_digits(value.denominator)
    )


def _rate_string(value: _Fraction) -> str:
    with _localcontext() as context:
        context.prec = max(
            XIRR_DECIMAL_PRECISION,
            len(str(abs(value.numerator))) + len(str(value.denominator)) + 20,
        )
        context.rounding = _ROUND_HALF_EVEN
        decimal_value = _Decimal(value.numerator) / _Decimal(value.denominator)
        quantized = decimal_value.quantize(_Decimal("0.000000000001"))
    return format(quantized, ".12f")


def _twr(
    *,
    initial_reason: MetricReason | None,
    start: PortfolioValuationPoint | None,
    end: PortfolioValuationPoint | None,
    pre: dict[str, PortfolioValuationPoint],
    external: dict[str, int],
) -> TWRResult:
    subperiod_count = len(external) + 1
    if initial_reason is not None:
        return _unavailable_twr(initial_reason, subperiod_count)
    if start is None or end is None or set(pre) != set(external):
        return _unavailable_twr(MetricReason.VALUATION_MISSING, subperiod_count)
    if end.as_of in external:
        expected = _checked_minor_add(
            pre[end.as_of].total_value.minor_units,
            external[end.as_of],
        )
        if end.total_value.minor_units != expected:
            return _unavailable_twr(
                MetricReason.END_FLOW_VALUATION_AMBIGUOUS, subperiod_count
            )
    base = start.total_value.minor_units
    if base <= 0:
        return _unavailable_twr(
            MetricReason.NON_POSITIVE_SUBPERIOD_BASE, subperiod_count
        )
    growth = _Fraction(1, 1)
    for timestamp in sorted(external):
        before = pre[timestamp].total_value.minor_units
        growth *= _Fraction(before, base)
        if not _fraction_bounded(growth):
            return _unavailable_twr(
                MetricReason.RATIONAL_LIMIT_EXCEEDED, subperiod_count
            )
        base = _checked_minor_add(before, external[timestamp])
        if base <= 0:
            return _unavailable_twr(
                MetricReason.NON_POSITIVE_SUBPERIOD_BASE, subperiod_count
            )
    growth *= _Fraction(end.total_value.minor_units, base)
    if not _fraction_bounded(growth):
        return _unavailable_twr(MetricReason.RATIONAL_LIMIT_EXCEEDED, subperiod_count)
    result = growth - 1
    if not _fraction_bounded(result):
        return _unavailable_twr(MetricReason.RATIONAL_LIMIT_EXCEEDED, subperiod_count)
    return TWRResult(
        status=MetricStatus.AVAILABLE,
        reason=MetricReason.AVAILABLE,
        growth_numerator=growth.numerator,
        growth_denominator=growth.denominator,
        return_numerator=result.numerator,
        return_denominator=result.denominator,
        rate_decimal=_rate_string(result),
        subperiod_count=subperiod_count,
    )


def _sign_changes(values: tuple[int, ...]) -> int:
    signs = tuple(1 if value > 0 else -1 for value in values if value != 0)
    return sum(left != right for left, right in _pairwise(signs))


def _unavailable_xirr(
    reason: MetricReason,
    *,
    sign_change_count: int,
    cash_flow_count: int,
    ambiguous: bool = False,
) -> XIRRResult:
    return XIRRResult(
        status=MetricStatus.AMBIGUOUS if ambiguous else MetricStatus.UNAVAILABLE,
        reason=reason,
        rate_decimal=None,
        sign_change_count=sign_change_count,
        cash_flow_count=cash_flow_count,
    )


def _xirr_npv(
    rate: _Decimal,
    flows: tuple[tuple[int, int], ...],
    start_ns: int,
) -> _Decimal:
    one_plus = _Decimal(1) + rate
    if one_plus <= 0:
        raise _DecimalException
    logarithm = one_plus.ln()
    total = _Decimal(0)
    year_ns = _Decimal(31_536_000_000_000_000)
    nano = _Decimal(1_000_000_000)
    for timestamp_ns, amount in flows:
        years = _Decimal(timestamp_ns - start_ns) / year_ns
        discount = (years * logarithm).exp()
        total += (_Decimal(amount) / nano) / discount
    if not total.is_finite():
        raise _DecimalException
    return total


def _xirr(
    *,
    initial_reason: MetricReason | None,
    start: PortfolioValuationPoint | None,
    end: PortfolioValuationPoint | None,
    external: dict[str, int],
) -> XIRRResult:
    if start is None or end is None:
        return _unavailable_xirr(
            initial_reason or MetricReason.VALUATION_MISSING,
            sign_change_count=0,
            cash_flow_count=0,
        )
    grouped: dict[str, int] = {
        start.as_of: -start.total_value.minor_units,
        end.as_of: end.total_value.minor_units,
    }
    if start.as_of == end.as_of:
        grouped[start.as_of] = _checked_minor_add(
            -start.total_value.minor_units, end.total_value.minor_units
        )
    for timestamp, effect in external.items():
        grouped[timestamp] = _checked_minor_add(grouped.get(timestamp, 0), -effect)
    ordered = tuple(
        (_timestamp_ns(timestamp), amount)
        for timestamp, amount in sorted(grouped.items())
        if amount != 0
    )
    changes = _sign_changes(tuple(amount for _, amount in ordered))
    count = len(ordered)
    if initial_reason is not None:
        return _unavailable_xirr(
            initial_reason,
            sign_change_count=changes,
            cash_flow_count=count,
        )
    if changes == 0:
        return _unavailable_xirr(
            MetricReason.XIRR_NO_SIGN_CHANGE,
            sign_change_count=changes,
            cash_flow_count=count,
        )
    if changes > 1:
        return _unavailable_xirr(
            MetricReason.XIRR_MULTIPLE_SIGN_CHANGES,
            sign_change_count=changes,
            cash_flow_count=count,
            ambiguous=True,
        )
    try:
        with _localcontext() as context:
            context.prec = XIRR_DECIMAL_PRECISION
            context.rounding = _ROUND_HALF_EVEN
            start_ns = _timestamp_ns(start.as_of)
            low = XIRR_MIN_RATE
            high = XIRR_MAX_RATE
            low_value = _xirr_npv(low, ordered, start_ns)
            high_value = _xirr_npv(high, ordered, start_ns)
            if low_value == 0:
                root = low
            elif high_value == 0:
                root = high
            elif (low_value > 0) == (high_value > 0):
                return _unavailable_xirr(
                    MetricReason.XIRR_ROOT_OUT_OF_RANGE,
                    sign_change_count=changes,
                    cash_flow_count=count,
                )
            else:
                root = (low + high) / 2
                for _ in range(XIRR_MAX_ITERATIONS):
                    root = (low + high) / 2
                    value = _xirr_npv(root, ordered, start_ns)
                    if value == 0 or high - low <= XIRR_ROOT_INTERVAL_EPSILON:
                        break
                    if (value > 0) == (low_value > 0):
                        low = root
                        low_value = value
                    else:
                        high = root
                else:
                    return _unavailable_xirr(
                        MetricReason.XIRR_NUMERIC_FAILURE,
                        sign_change_count=changes,
                        cash_flow_count=count,
                    )
            rate = root.quantize(_Decimal("0.000000000001"))
            if not rate.is_finite():
                raise _DecimalException
    except (_DecimalException, ArithmeticError, ValueError):
        return _unavailable_xirr(
            MetricReason.XIRR_NUMERIC_FAILURE,
            sign_change_count=changes,
            cash_flow_count=count,
        )
    return XIRRResult(
        status=MetricStatus.AVAILABLE,
        reason=MetricReason.AVAILABLE,
        rate_decimal=format(rate, ".12f"),
        sign_change_count=changes,
        cash_flow_count=count,
    )


def _report_identity(
    report: PerformanceReport,
    identity_key: bytes,
) -> str:
    return _hmac_sha256(identity_key, report._identity_dict())


def build_performance_report(
    ledger_export_bytes: bytes,
    valuations: tuple[PortfolioValuationPoint, ...],
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    period_start: str,
    period_end: str,
    generated_at: str,
    identity_key: bytes,
    identity_key_id: str,
) -> PerformanceReport:
    if (
        type(ledger_export_bytes) is not bytes
        or type(valuations) is not tuple
        or type(account_scope_sha256) is not str
        or type(period_start) is not str
        or type(period_end) is not str
        or type(generated_at) is not str
        or type(identity_key) is not bytes
        or type(identity_key_id) is not str
    ):
        _fail(CL6Reason.TYPE_INVALID)
    checked_environment = _require_environment(environment)
    account = _require_hash(account_scope_sha256, CL6Reason.ACCOUNT_SCOPE_INVALID)
    key = _require_key(identity_key)
    key_id = _require_key_id(identity_key_id)
    start_ns = _timestamp_ns(period_start)
    end_ns = _timestamp_ns(period_end)
    generated_ns = _timestamp_ns(generated_at)
    if not start_ns < end_ns <= generated_ns:
        _fail(CL6Reason.PERIOD_INVALID)
    if not 1 <= len(ledger_export_bytes) <= MAX_LEDGER_EXPORT_BYTES:
        _fail(CL6Reason.LEDGER_EXPORT_INVALID)
    projection = _ledger_projection(
        ledger_export_bytes,
        account_scope_sha256=account,
        environment=checked_environment,
        generated_at=generated_at,
        identity_key=key,
    )
    parsed, transactions = _parse_export_transactions(ledger_export_bytes)
    if (
        projection.account_scope_sha256 != account
        or projection.environment is not checked_environment
        or projection.as_of != generated_at
        or projection.ledger_export_sha256 != _sha256(ledger_export_bytes)
        or str(parsed.get("ledger_revision")) != str(projection.ledger_revision)
        or parsed.get("ledger_head_sha256") != projection.ledger_head_sha256
    ):
        _fail(CL6Reason.LEDGER_IDENTITY_MISMATCH)
    by_hash = {transaction.sha256: transaction for transaction in transactions}
    if len(by_hash) != len(transactions):
        _fail(CL6Reason.LEDGER_GRAPH_INVALID)

    if len(valuations) > MAX_VALUATION_POINTS:
        _fail(CL6Reason.NUMERIC_BOUND_EXCEEDED)
    checked_valuations: list[PortfolioValuationPoint] = []
    seen_valuations: set[tuple[ValuationPhase, str]] = set()
    for value in valuations:
        if type(value) is not PortfolioValuationPoint:
            _fail(CL6Reason.TYPE_INVALID)
        checked = _validated_valuation(value, key)
        if (
            checked.account_scope_sha256 != account
            or checked.environment is not checked_environment
            or checked.identity_key_id != key_id
        ):
            _fail(CL6Reason.EVIDENCE_CORRELATION_INVALID)
        marker = (checked.phase, checked.as_of)
        if marker in seen_valuations:
            _fail(CL6Reason.VALUATION_SET_INVALID)
        seen_valuations.add(marker)
        if (
            (
                checked.phase is ValuationPhase.PERIOD_START
                and checked.as_of != period_start
            )
            or (
                checked.phase is ValuationPhase.PERIOD_END
                and checked.as_of != period_end
            )
            or (
                checked.phase is ValuationPhase.PRE_EXTERNAL_FLOW
                and not start_ns < _timestamp_ns(checked.as_of) <= end_ns
            )
        ):
            _fail(CL6Reason.VALUATION_SET_INVALID)
        checked_valuations.append(checked)

    reporting_rows: list[tuple[_ledger.LedgerTransaction, ReportingCategory, int]] = []
    external: dict[str, int] = {}
    for transaction in transactions:
        if transaction.source.account_scope_sha256 != account:
            continue
        for reference in (
            transaction.reversal_of_sha256,
            transaction.corrects_sha256,
        ):
            if reference is not None:
                target = by_hash.get(reference)
                if target is None:
                    _fail(CL6Reason.LEDGER_GRAPH_INVALID)
                if target.source.account_scope_sha256 != account:
                    _fail(CL6Reason.ACCOUNT_SCOPE_INVALID)
        timestamp_ns = _timestamp_ns(transaction.effective_at)
        if not start_ns < timestamp_ns <= end_ns:
            continue
        category = _reporting_category(transaction, by_hash)
        effect = _cash_effect(transaction)
        reporting_rows.append((transaction, category, effect))
        if category is ReportingCategory.EXTERNAL_FLOW:
            external[transaction.effective_at] = _checked_minor_add(
                external.get(transaction.effective_at, 0), effect
            )
    if len(external) > MAX_EXTERNAL_FLOW_TIMESTAMPS:
        _fail(CL6Reason.NUMERIC_BOUND_EXCEEDED)

    pre: dict[str, PortfolioValuationPoint] = {}
    start: PortfolioValuationPoint | None = None
    end: PortfolioValuationPoint | None = None
    for point in checked_valuations:
        if point.phase is ValuationPhase.PERIOD_START:
            start = point
        elif point.phase is ValuationPhase.PERIOD_END:
            end = point
        elif point.as_of not in external:
            _fail(CL6Reason.VALUATION_SET_INVALID)
        else:
            pre[point.as_of] = point

    summary = _summary(tuple(reporting_rows))
    shared_reason: MetricReason | None = None
    if not projection.complete:
        shared_reason = MetricReason.LEDGER_INCOMPLETE
    elif summary.manual_adjustment_count:
        shared_reason = MetricReason.MANUAL_ADJUSTMENT_PRESENT
    elif summary.opening_count:
        shared_reason = MetricReason.OPENING_INSIDE_PERIOD
    elif start is None or end is None or set(pre) != set(external):
        shared_reason = MetricReason.VALUATION_MISSING
    twr = _twr(
        initial_reason=shared_reason,
        start=start,
        end=end,
        pre=pre,
        external=external,
    )
    xirr = _xirr(
        initial_reason=shared_reason,
        start=start,
        end=end,
        external=external,
    )
    status = (
        ReportStatus.COMPLETE
        if projection.complete
        and twr.status is MetricStatus.AVAILABLE
        and xirr.status is MetricStatus.AVAILABLE
        else ReportStatus.DEGRADED
    )
    kinds = tuple(sorted({item.value for item in projection.incompleteness_kinds}))
    valuation_sha = _valuation_set_sha(
        tuple(checked_valuations),
        account_scope_sha256=account,
        environment=checked_environment,
        identity_key_id=key_id,
        period_start=period_start,
        period_end=period_end,
    )
    placeholder = PerformanceReport(
        account_scope_sha256=account,
        environment=checked_environment,
        period_start=period_start,
        period_end=period_end,
        generated_at=generated_at,
        ledger_export_sha256=projection.ledger_export_sha256,
        ledger_revision=projection.ledger_revision,
        ledger_head_sha256=projection.ledger_head_sha256,
        ledger_projection_sha256=projection.sha256,
        ledger_complete=projection.complete,
        ledger_incompleteness_kinds=kinds,
        valuation_set_sha256=valuation_sha,
        cash_flow_summary=summary,
        twr=twr,
        xirr=xirr,
        report_status=status,
        identity_key_id=key_id,
        report_identity_sha256="0" * 64,
    )
    identity = _report_identity(placeholder, key)
    return PerformanceReport(
        account_scope_sha256=placeholder.account_scope_sha256,
        environment=placeholder.environment,
        period_start=placeholder.period_start,
        period_end=placeholder.period_end,
        generated_at=placeholder.generated_at,
        ledger_export_sha256=placeholder.ledger_export_sha256,
        ledger_revision=placeholder.ledger_revision,
        ledger_head_sha256=placeholder.ledger_head_sha256,
        ledger_projection_sha256=placeholder.ledger_projection_sha256,
        ledger_complete=placeholder.ledger_complete,
        ledger_incompleteness_kinds=placeholder.ledger_incompleteness_kinds,
        valuation_set_sha256=placeholder.valuation_set_sha256,
        cash_flow_summary=placeholder.cash_flow_summary,
        twr=placeholder.twr,
        xirr=placeholder.xirr,
        report_status=placeholder.report_status,
        identity_key_id=placeholder.identity_key_id,
        report_identity_sha256=identity,
    )


@_dataclass(frozen=True, slots=True)
class PortfolioIdentityEvidence:
    account_scope_sha256: str
    environment: BrokerEnvironment
    captured_at: str
    portfolio_snapshot_at: str
    portfolio_revision: int
    portfolio_decision_checksum: str
    portfolio_document_checksum: str
    portfolio_schema_version: int
    portfolio_source: str
    migration_status: str
    legacy_read_path_enabled: bool
    freshness: str
    state_status: str
    blocking: bool
    identity_key_id: str
    evidence_identity_sha256: str
    version: int = PORTFOLIO_IDENTITY_EVIDENCE_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != PORTFOLIO_IDENTITY_EVIDENCE_VERSION
        ):
            _fail(CL6Reason.VERSION_UNSUPPORTED)
        _require_hash(self.account_scope_sha256, CL6Reason.ACCOUNT_SCOPE_INVALID)
        _require_environment(self.environment)
        _timestamp_ns(self.captured_at)
        _timestamp_ns(self.portfolio_snapshot_at)
        _require_revision(self.portfolio_revision, CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        _require_hash(
            self.portfolio_decision_checksum, CL6Reason.PORTFOLIO_EVIDENCE_INVALID
        )
        _require_hash(
            self.portfolio_document_checksum, CL6Reason.PORTFOLIO_EVIDENCE_INVALID
        )
        if type(self.portfolio_schema_version) is not int:
            _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        for value in (
            self.portfolio_source,
            self.migration_status,
            self.freshness,
            self.state_status,
        ):
            _require_string(value, CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        if (
            type(self.legacy_read_path_enabled) is not bool
            or type(self.blocking) is not bool
        ):
            _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        _require_key_id(self.identity_key_id)
        _require_hash(
            self.evidence_identity_sha256, CL6Reason.PORTFOLIO_EVIDENCE_INVALID
        )

    def _identity_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "blocking": self.blocking,
            "captured_at": self.captured_at,
            "domain": "v3.10-cl6-portfolio-identity-evidence-identity",
            "environment": self.environment.value,
            "freshness": self.freshness,
            "identity_key_id": self.identity_key_id,
            "legacy_read_path_enabled": self.legacy_read_path_enabled,
            "migration_status": self.migration_status,
            "portfolio_decision_checksum": self.portfolio_decision_checksum,
            "portfolio_document_checksum": self.portfolio_document_checksum,
            "portfolio_revision": str(self.portfolio_revision),
            "portfolio_schema_version": self.portfolio_schema_version,
            "portfolio_snapshot_at": self.portfolio_snapshot_at,
            "portfolio_source": self.portfolio_source,
            "state_status": self.state_status,
            "version": self.version,
        }

    def to_canonical_dict(self) -> dict[str, object]:
        result = self._identity_dict()
        result["domain"] = "v3.10-cl6-portfolio-identity-evidence"
        result["evidence_identity_sha256"] = self.evidence_identity_sha256
        return result

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_property(self)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class RiskGuardEvidence:
    account_scope_sha256: str
    environment: BrokerEnvironment
    captured_at: str
    risk_policy_hash: str
    risk_state_guard_hash: str
    risk_state_version: int
    identity_key_id: str
    evidence_identity_sha256: str
    version: int = RISK_GUARD_EVIDENCE_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != RISK_GUARD_EVIDENCE_VERSION:
            _fail(CL6Reason.VERSION_UNSUPPORTED)
        _require_hash(self.account_scope_sha256, CL6Reason.ACCOUNT_SCOPE_INVALID)
        _require_environment(self.environment)
        _timestamp_ns(self.captured_at)
        _require_hash(self.risk_policy_hash, CL6Reason.RISK_EVIDENCE_INVALID)
        _require_hash(self.risk_state_guard_hash, CL6Reason.RISK_EVIDENCE_INVALID)
        if (
            type(self.risk_state_version) is not int
            or self.risk_state_version != _risk.RISK_STATE_VERSION
        ):
            _fail(CL6Reason.RISK_EVIDENCE_INVALID)
        _require_key_id(self.identity_key_id)
        _require_hash(self.evidence_identity_sha256, CL6Reason.RISK_EVIDENCE_INVALID)

    def _identity_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "captured_at": self.captured_at,
            "domain": "v3.10-cl6-risk-guard-evidence-identity",
            "environment": self.environment.value,
            "identity_key_id": self.identity_key_id,
            "risk_policy_hash": self.risk_policy_hash,
            "risk_state_guard_hash": self.risk_state_guard_hash,
            "risk_state_version": self.risk_state_version,
            "version": self.version,
        }

    def to_canonical_dict(self) -> dict[str, object]:
        result = self._identity_dict()
        result["domain"] = "v3.10-cl6-risk-guard-evidence"
        result["evidence_identity_sha256"] = self.evidence_identity_sha256
        return result

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_property(self)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class PortfolioRiskCashContext:
    account_scope_sha256: str
    environment: BrokerEnvironment
    evaluated_at: str
    status: RiskCashContextStatus
    reason: RiskCashContextReason
    availability_sha256: str
    availability_status: str
    availability_reason: str
    availability_evaluated_at: str
    broker_cash_as_of: str
    broker_positions_as_of: str
    free_investable_cash: Money | None
    ledger_export_sha256: str
    ledger_revision: int
    ledger_head_sha256: str
    reconciliation_sha256: str
    central_order_revision: int
    reservation_projection_hash: str
    central_projection_evaluated_at: str
    portfolio_evidence_sha256: str
    portfolio_revision: int
    portfolio_decision_checksum: str
    portfolio_document_checksum: str
    portfolio_snapshot_at: str
    portfolio_captured_at: str
    risk_guard_evidence_sha256: str
    risk_guard_captured_at: str
    risk_policy_hash: str
    risk_state_guard_hash: str
    identity_key_id: str
    context_identity_sha256: str
    version: int = PORTFOLIO_RISK_CASH_CONTEXT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != PORTFOLIO_RISK_CASH_CONTEXT_VERSION
        ):
            _fail(CL6Reason.VERSION_UNSUPPORTED)
        _require_hash(self.account_scope_sha256, CL6Reason.ACCOUNT_SCOPE_INVALID)
        _require_environment(self.environment)
        for value in (
            self.evaluated_at,
            self.availability_evaluated_at,
            self.broker_cash_as_of,
            self.broker_positions_as_of,
            self.central_projection_evaluated_at,
            self.portfolio_snapshot_at,
            self.portfolio_captured_at,
            self.risk_guard_captured_at,
        ):
            _timestamp_ns(value)
        if (
            type(self.status) is not RiskCashContextStatus
            or type(self.reason) is not RiskCashContextReason
        ):
            _fail(CL6Reason.TYPE_INVALID)
        for value in (self.availability_status, self.availability_reason):
            _require_string(value, CL6Reason.CASH_AVAILABILITY_INVALID)
        for value in (
            self.availability_sha256,
            self.ledger_export_sha256,
            self.ledger_head_sha256,
            self.reconciliation_sha256,
            self.reservation_projection_hash,
            self.portfolio_evidence_sha256,
            self.portfolio_decision_checksum,
            self.portfolio_document_checksum,
            self.risk_guard_evidence_sha256,
            self.risk_policy_hash,
            self.risk_state_guard_hash,
            self.context_identity_sha256,
        ):
            _require_hash(value, CL6Reason.EVIDENCE_CORRELATION_INVALID)
        _require_revision(self.ledger_revision, CL6Reason.EVIDENCE_CORRELATION_INVALID)
        _require_revision(
            self.central_order_revision, CL6Reason.EVIDENCE_CORRELATION_INVALID
        )
        _require_revision(
            self.portfolio_revision, CL6Reason.EVIDENCE_CORRELATION_INVALID
        )
        if self.status is RiskCashContextStatus.READY_FOR_LOCKED_REVALIDATION:
            if self.reason is not RiskCashContextReason.READY:
                _fail(CL6Reason.EVIDENCE_CORRELATION_INVALID)
            checked = _money(
                self.free_investable_cash, CL6Reason.EVIDENCE_CORRELATION_INVALID
            )
            object.__setattr__(self, "free_investable_cash", checked)
        elif (
            self.free_investable_cash is not None
            or self.reason is RiskCashContextReason.READY
        ):
            _fail(CL6Reason.EVIDENCE_CORRELATION_INVALID)
        _require_key_id(self.identity_key_id)

    def _identity_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "availability_evaluated_at": self.availability_evaluated_at,
            "availability_reason": self.availability_reason,
            "availability_sha256": self.availability_sha256,
            "availability_status": self.availability_status,
            "broker_cash_as_of": self.broker_cash_as_of,
            "broker_positions_as_of": self.broker_positions_as_of,
            "central_order_revision": str(self.central_order_revision),
            "central_projection_evaluated_at": self.central_projection_evaluated_at,
            "domain": "v3.10-cl6-portfolio-risk-cash-context-identity",
            "environment": self.environment.value,
            "evaluated_at": self.evaluated_at,
            "free_investable_cash": (
                None
                if self.free_investable_cash is None
                else _money_dict(self.free_investable_cash)
            ),
            "identity_key_id": self.identity_key_id,
            "ledger_export_sha256": self.ledger_export_sha256,
            "ledger_head_sha256": self.ledger_head_sha256,
            "ledger_revision": str(self.ledger_revision),
            "portfolio_captured_at": self.portfolio_captured_at,
            "portfolio_decision_checksum": self.portfolio_decision_checksum,
            "portfolio_document_checksum": self.portfolio_document_checksum,
            "portfolio_evidence_sha256": self.portfolio_evidence_sha256,
            "portfolio_revision": str(self.portfolio_revision),
            "portfolio_snapshot_at": self.portfolio_snapshot_at,
            "reason": self.reason.value,
            "reconciliation_sha256": self.reconciliation_sha256,
            "reservation_projection_hash": self.reservation_projection_hash,
            "risk_guard_captured_at": self.risk_guard_captured_at,
            "risk_guard_evidence_sha256": self.risk_guard_evidence_sha256,
            "risk_policy_hash": self.risk_policy_hash,
            "risk_state_guard_hash": self.risk_state_guard_hash,
            "status": self.status.value,
            "version": self.version,
        }

    def to_canonical_dict(self) -> dict[str, object]:
        result = self._identity_dict()
        result["domain"] = "v3.10-cl6-portfolio-risk-cash-context"
        result["context_identity_sha256"] = self.context_identity_sha256
        return result

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_property(self)

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


_P = _portfolio
_PORTFOLIO_SPECS: dict[
    type[object], tuple[tuple[str, tuple[type[object], ...], object], ...]
] = {
    _portfolio_preflight.PortfolioSnapshotLease: (
        ("state", (_P.PortfolioState,), None),
        ("revision", (int,), None),
        ("decision_checksum", (str,), None),
        ("document_checksum", (str,), None),
        ("leased_at", (str,), None),
    ),
    _P.PortfolioState: (
        ("version", (int,), None),
        ("account", (_P.AccountState,), None),
        ("snapshot_at", (str,), None),
        ("generated_at", (str,), None),
        ("freshness", (_P.SnapshotFreshness,), None),
        ("source", (str,), None),
        ("positions", (tuple,), _P.PositionState),
        ("warnings", (tuple,), str),
        ("state_status", (str,), None),
        ("blocking", (bool,), None),
        ("revision", (int,), None),
        ("portfolio_source", (str,), None),
        ("migration", (_P.PortfolioMigrationMetadata,), None),
        ("last_transaction_id", (str, _NONE_TYPE), None),
        ("last_transaction_status", (str,), None),
    ),
    _P.AccountState: (
        ("account_id", (str,), None),
        ("total_value", (float, _NONE_TYPE), None),
        ("securities_value", (float, _NONE_TYPE), None),
        ("expected_yield", (float, _NONE_TYPE), None),
        ("cash_balances", (tuple,), _P.CashBalance),
    ),
    _P.CashBalance: (
        ("currency", (str,), None),
        ("available", (float,), None),
        ("blocked", (float,), None),
    ),
    _P.PositionState: (
        ("instrument_id", (str,), None),
        ("figi", (str,), None),
        ("ticker", (str,), None),
        ("class_code", (str,), None),
        ("asset_type", (str,), None),
        ("currency", (str,), None),
        ("quantity", (float,), None),
        ("actual_lots", (int,), None),
        ("average_price", (float, _NONE_TYPE), None),
        ("current_price", (float, _NONE_TYPE), None),
        ("market_value", (float, _NONE_TYPE), None),
        ("expected_yield", (float, _NONE_TYPE), None),
        ("target", (_P.PortfolioTarget, _NONE_TYPE), None),
        ("ownership", (_P.PositionOwnership, _NONE_TYPE), None),
        ("ownership_status", (_P.OwnershipStatus,), None),
        ("pending_orders", (tuple,), _P.PendingOrderState),
        ("reconciliation", (_P.ReconciliationResult,), None),
        ("origin", (_P.PositionOrigin,), None),
        ("last_candle_time", (str, _NONE_TYPE), None),
    ),
    _P.PortfolioTarget: (
        ("instrument_id", (str,), None),
        ("target_lots", (int,), None),
        ("strategy_id", (str, _NONE_TYPE), None),
        ("config_hash", (str, _NONE_TYPE), None),
        ("candle_time", (str, _NONE_TYPE), None),
    ),
    _P.PositionOwnership: (
        ("strategy_id", (str,), None),
        ("config_hash", (str,), None),
        ("candle_interval", (str,), None),
        ("source", (str,), None),
        ("attributed_at", (str, _NONE_TYPE), None),
    ),
    _P.PendingOrderState: (
        ("order_request_id", (str,), None),
        ("instrument_id", (str,), None),
        ("direction", (str,), None),
        ("requested_lots", (int,), None),
        ("executed_lots", (int,), None),
        ("status", (_P.PendingOrderStatus,), None),
        ("broker_order_id", (str, _NONE_TYPE), None),
        ("uncertain", (bool,), None),
        ("source", (str,), None),
    ),
    _P.ReconciliationResult: (
        ("instrument_id", (str,), None),
        ("status", (_P.ReconciliationStatus,), None),
        ("blocking", (bool,), None),
        ("reasons", (tuple,), str),
        ("actual_lots", (int,), None),
        ("target_lots", (int, _NONE_TYPE), None),
        ("pending_order_ids", (tuple,), str),
        ("checked_at", (str, _NONE_TYPE), None),
    ),
    _P.PortfolioMigrationMetadata: (
        ("status", (_P.PortfolioMigrationStatus,), None),
        ("source_schema", (int,), None),
        ("target_schema", (int,), None),
        ("migration_id", (str, _NONE_TYPE), None),
        ("migrated_at", (str, _NONE_TYPE), None),
        ("legacy_read_path_enabled", (bool,), None),
        ("compatibility_shadow_status", (_P.CompatibilityShadowStatus,), None),
        ("detail", (str,), None),
    ),
}


def _scalar_preflight(value: object, reason: CL6Reason) -> None:
    value_type = type(value)
    if value_type is str:
        _require_string(value, reason)
    elif value_type is int:
        if not _INT64_MIN <= value <= _INT64_MAX:
            _fail(reason)
    elif value_type is float and not _math.isfinite(value):
        _fail(reason)


def _preflight_portfolio_lease(
    value: _portfolio_preflight.PortfolioSnapshotLease,
) -> None:
    counts = {"graph_items": 0, "pending_orders": 0}

    def visit(candidate: object, expected: tuple[type[object], ...]) -> None:
        candidate_type = type(candidate)
        if candidate_type not in expected:
            _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        _scalar_preflight(candidate, CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        spec = _PORTFOLIO_SPECS.get(candidate_type)
        if spec is None:
            return
        if tuple(field.name for field in _fields(candidate_type)) != tuple(
            field[0] for field in spec
        ):
            _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        for name, allowed, item_type in spec:
            try:
                child = object.__getattribute__(candidate, name)
            except Exception:  # noqa: BLE001 - forged Portfolio slots
                _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
            if type(child) not in allowed:
                _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
            _scalar_preflight(child, CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
            if type(child) in _PORTFOLIO_SPECS:
                visit(child, allowed)
            if type(child) is tuple:
                counts["graph_items"] += len(child)
                if counts["graph_items"] > MAX_PORTFOLIO_GRAPH_ITEMS_TOTAL:
                    _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
                if name == "positions" and len(child) > MAX_PORTFOLIO_POSITIONS:
                    _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
                if name == "pending_orders":
                    counts["pending_orders"] += len(child)
                    if counts["pending_orders"] > MAX_PORTFOLIO_PENDING_ORDERS_TOTAL:
                        _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
                for item in child:
                    visit(item, (item_type,))

    visit(value, (_portfolio_preflight.PortfolioSnapshotLease,))
    state = object.__getattribute__(value, "state")
    revision = object.__getattribute__(state, "revision")
    if not 0 <= revision <= MAX_REVISION:
        _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)


_RISK_SPECS: dict[
    type[object], tuple[tuple[str, tuple[type[object], ...], object], ...]
] = {
    _risk.RiskPolicy: (
        ("enabled", (bool,), None),
        ("max_position_lots", (int,), None),
        ("max_position_value_rub", (float, _NONE_TYPE), None),
        ("max_position_share_of_equity", (float, _NONE_TYPE), None),
        ("max_order_value_rub", (float, _NONE_TYPE), None),
        ("cash_reserve_rub", (float,), None),
        ("max_cash_usage_fraction", (float,), None),
        ("commission_buffer_fraction", (float,), None),
        ("risk_per_trade_rub", (float, _NONE_TYPE), None),
        ("risk_per_trade_fraction", (float, _NONE_TYPE), None),
        ("atr_multiplier", (float,), None),
        ("daily_loss_limit_rub", (float, _NONE_TYPE), None),
        ("daily_loss_limit_fraction", (float, _NONE_TYPE), None),
        ("weekly_loss_limit_rub", (float, _NONE_TYPE), None),
        ("weekly_loss_limit_fraction", (float, _NONE_TYPE), None),
        ("max_drawdown_fraction", (float, _NONE_TYPE), None),
        ("max_daily_turnover_rub", (float, _NONE_TYPE), None),
        ("max_orders_per_day", (int, _NONE_TYPE), None),
        ("max_snapshot_age_seconds", (int, _NONE_TYPE), None),
        ("block_on_unknown_equity", (bool,), None),
        ("block_on_unknown_cash", (bool,), None),
        ("block_on_unknown_price", (bool,), None),
        ("block_on_unknown_risk_distance", (bool,), None),
        ("block_on_stale_snapshot", (bool,), None),
        ("block_on_unreconciled_position", (bool,), None),
        ("block_on_pending_order", (bool,), None),
        ("allow_risk_reducing_orders_during_halt", (bool,), None),
        ("portfolio_policy_configured", (bool,), None),
        ("portfolio_policy_mode", (str,), None),
        ("max_gross_exposure_rub", (float, _NONE_TYPE), None),
        ("max_gross_exposure_fraction", (float, _NONE_TYPE), None),
        ("max_net_exposure_fraction", (float, _NONE_TYPE), None),
        ("max_instrument_concentration_fraction", (float, _NONE_TYPE), None),
        ("max_strategy_concentration_fraction", (float, _NONE_TYPE), None),
        ("max_asset_class_concentration_fraction", (float, _NONE_TYPE), None),
        ("asset_class_concentration_limits", (tuple,), "pair"),
        ("max_open_positions", (int, _NONE_TYPE), None),
        ("min_cash_reserve_fraction", (float, _NONE_TYPE), None),
        ("max_daily_turnover_fraction", (float, _NONE_TYPE), None),
        ("max_price_age_seconds", (int, _NONE_TYPE), None),
        ("portfolio_warning_utilization_fraction", (float,), None),
    ),
    _risk.InstrumentRiskHalt: (
        ("instrument_id", (str,), None),
        ("reason", (str,), None),
        ("source", (str,), None),
        ("set_at", (str,), None),
        ("operator_ref", (str, _NONE_TYPE), None),
    ),
    _risk.RiskState: (
        ("version", (int,), None),
        ("daily_date", (str, _NONE_TYPE), None),
        ("weekly_key", (str, _NONE_TYPE), None),
        ("daily_start_equity_rub", (float, _NONE_TYPE), None),
        ("weekly_start_equity_rub", (float, _NONE_TYPE), None),
        ("high_watermark_equity_rub", (float, _NONE_TYPE), None),
        ("daily_turnover_rub", (float,), None),
        ("daily_order_count", (int,), None),
        ("kill_switch_active", (bool,), None),
        ("kill_switch_reason", (str, _NONE_TYPE), None),
        ("kill_switch_set_at", (str, _NONE_TYPE), None),
        ("kill_switch_source", (str, _NONE_TYPE), None),
        ("kill_switch_operator_ref", (str, _NONE_TYPE), None),
        ("instrument_kill_switches", (tuple,), _risk.InstrumentRiskHalt),
        ("risk_resync_required", (bool,), None),
        ("risk_resync_reason", (str, _NONE_TYPE), None),
        ("risk_resync_set_at", (str, _NONE_TYPE), None),
        ("risk_resync_source", (str, _NONE_TYPE), None),
        ("risk_resync_cash_before_rub", (float, _NONE_TYPE), None),
        ("risk_resync_cash_observed_rub", (float, _NONE_TYPE), None),
        ("risk_resync_equity_observed_rub", (float, _NONE_TYPE), None),
        ("risk_resync_snapshot_at", (str, _NONE_TYPE), None),
        ("last_equity_rub", (float, _NONE_TYPE), None),
        ("last_cash_rub", (float, _NONE_TYPE), None),
        ("last_snapshot_at", (str, _NONE_TYPE), None),
        ("last_evaluated_at", (str, _NONE_TYPE), None),
        ("last_execution_at", (str, _NONE_TYPE), None),
        ("recorded_execution_ids", (tuple,), str),
        ("last_portfolio_risk_decision_id", (str, _NONE_TYPE), None),
        ("last_portfolio_risk_input_hash", (str, _NONE_TYPE), None),
        ("last_portfolio_risk_evaluated_at", (str, _NONE_TYPE), None),
    ),
}


def _risk_preflight(policy: _risk.RiskPolicy, state: _risk.RiskState) -> None:
    def visit(candidate: object, expected: tuple[type[object], ...]) -> None:
        candidate_type = type(candidate)
        if candidate_type not in expected:
            _fail(CL6Reason.RISK_EVIDENCE_INVALID)
        _scalar_preflight(candidate, CL6Reason.RISK_EVIDENCE_INVALID)
        spec = _RISK_SPECS.get(candidate_type)
        if spec is None:
            return
        if tuple(field.name for field in _fields(candidate_type)) != tuple(
            field[0] for field in spec
        ):
            _fail(CL6Reason.RISK_EVIDENCE_INVALID)
        for name, allowed, item_type in spec:
            try:
                child = object.__getattribute__(candidate, name)
            except Exception:  # noqa: BLE001 - forged Risk slots
                _fail(CL6Reason.RISK_EVIDENCE_INVALID)
            if type(child) not in allowed:
                _fail(CL6Reason.RISK_EVIDENCE_INVALID)
            _scalar_preflight(child, CL6Reason.RISK_EVIDENCE_INVALID)
            if type(child) in _RISK_SPECS:
                visit(child, allowed)
            if type(child) is tuple:
                if name == "instrument_kill_switches" and len(child) > MAX_RISK_HALTS:
                    _fail(CL6Reason.RISK_EVIDENCE_INVALID)
                if (
                    name == "recorded_execution_ids"
                    and len(child) > MAX_RECORDED_EXECUTION_IDS
                ):
                    _fail(CL6Reason.RISK_EVIDENCE_INVALID)
                if name == "asset_class_concentration_limits":
                    if len(child) > MAX_RISK_ASSET_CLASS_LIMITS:
                        _fail(CL6Reason.RISK_EVIDENCE_INVALID)
                    for pair in child:
                        if type(pair) is not tuple or len(pair) != 2:
                            _fail(CL6Reason.RISK_EVIDENCE_INVALID)
                        visit(pair[0], (str,))
                        visit(pair[1], (float,))
                else:
                    for item in child:
                        visit(item, (item_type,))

    visit(policy, (_risk.RiskPolicy,))
    visit(state, (_risk.RiskState,))


def _portfolio_evidence_identity(
    evidence: PortfolioIdentityEvidence, identity_key: bytes
) -> str:
    return _hmac_sha256(identity_key, evidence._identity_dict())


def _risk_evidence_identity(evidence: RiskGuardEvidence, identity_key: bytes) -> str:
    return _hmac_sha256(identity_key, evidence._identity_dict())


def build_portfolio_identity_evidence(
    lease: PortfolioSnapshotLease,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    evaluated_at: str,
    identity_key: bytes,
    identity_key_id: str,
) -> PortfolioIdentityEvidence:
    if (
        type(lease) is not _portfolio_preflight.PortfolioSnapshotLease
        or type(account_scope_sha256) is not str
        or type(evaluated_at) is not str
        or type(identity_key) is not bytes
        or type(identity_key_id) is not str
    ):
        _fail(CL6Reason.TYPE_INVALID)
    checked_environment = _require_environment(environment)
    account = _require_hash(account_scope_sha256, CL6Reason.ACCOUNT_SCOPE_INVALID)
    key = _require_key(identity_key)
    key_id = _require_key_id(identity_key_id)
    evaluated_ns = _timestamp_ns(evaluated_at)
    _preflight_portfolio_lease(lease)
    state = object.__getattribute__(lease, "state")
    try:
        raw = _portfolio.PortfolioState.to_dict(state)
        canonical = _json.dumps(
            raw,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(canonical) > MAX_PORTFOLIO_CANONICAL_BYTES:
            _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        checked_state = _portfolio.PortfolioState.from_dict(raw)
        if (
            checked_state != state
            or _portfolio.PortfolioState.to_dict(checked_state) != raw
        ):
            _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        decision = checked_state.decision_sha256
        document = _sha256(canonical)
    except CL6Error:
        raise
    except Exception:  # noqa: BLE001 - accepted Portfolio boundary
        failure = CL6Error(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        raise failure from None
    lease_revision = object.__getattribute__(lease, "revision")
    lease_decision = object.__getattribute__(lease, "decision_checksum")
    lease_document = object.__getattribute__(lease, "document_checksum")
    if (
        lease_revision != checked_state.revision
        or lease_decision != decision
        or lease_document != document
    ):
        _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
    raw_account = object.__getattribute__(checked_state.account, "account_id")
    expected_scope = _account_scope(raw_account, checked_environment, key_id, key)
    if not _hmac.compare_digest(expected_scope, account):
        _fail(CL6Reason.ACCOUNT_SCOPE_INVALID)
    snapshot_at, snapshot_ns = _normalize_portfolio_timestamp(
        object.__getattribute__(checked_state, "snapshot_at")
    )
    captured_at, captured_ns = _normalize_portfolio_timestamp(
        object.__getattribute__(lease, "leased_at")
    )
    if snapshot_ns > evaluated_ns or captured_ns > evaluated_ns:
        _fail(CL6Reason.DEPENDENCY_FROM_FUTURE)
    migration = object.__getattribute__(checked_state, "migration")
    placeholder = PortfolioIdentityEvidence(
        account_scope_sha256=account,
        environment=checked_environment,
        captured_at=captured_at,
        portfolio_snapshot_at=snapshot_at,
        portfolio_revision=checked_state.revision,
        portfolio_decision_checksum=decision,
        portfolio_document_checksum=document,
        portfolio_schema_version=checked_state.version,
        portfolio_source=checked_state.portfolio_source,
        migration_status=migration.status.value,
        legacy_read_path_enabled=migration.legacy_read_path_enabled,
        freshness=checked_state.freshness.value,
        state_status=checked_state.state_status,
        blocking=checked_state.blocking,
        identity_key_id=key_id,
        evidence_identity_sha256="0" * 64,
    )
    identity = _portfolio_evidence_identity(placeholder, key)
    return _replace(placeholder, evidence_identity_sha256=identity)


def build_risk_guard_evidence(
    policy: RiskPolicy,
    state: RiskState,
    *,
    raw_account_id: str,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    captured_at: str,
    evaluated_at: str,
    identity_key: bytes,
    identity_key_id: str,
) -> RiskGuardEvidence:
    if (
        type(policy) is not _risk.RiskPolicy
        or type(state) is not _risk.RiskState
        or type(raw_account_id) is not str
        or type(account_scope_sha256) is not str
        or type(captured_at) is not str
        or type(evaluated_at) is not str
        or type(identity_key) is not bytes
        or type(identity_key_id) is not str
    ):
        _fail(CL6Reason.TYPE_INVALID)
    checked_environment = _require_environment(environment)
    raw_account = _require_string(raw_account_id, CL6Reason.ACCOUNT_SCOPE_INVALID)
    if not raw_account:
        _fail(CL6Reason.ACCOUNT_SCOPE_INVALID)
    account = _require_hash(account_scope_sha256, CL6Reason.ACCOUNT_SCOPE_INVALID)
    key = _require_key(identity_key)
    key_id = _require_key_id(identity_key_id)
    captured_ns = _timestamp_ns(captured_at)
    evaluated_ns = _timestamp_ns(evaluated_at)
    _risk_preflight(policy, state)
    if state.version != _risk.RISK_STATE_VERSION:
        _fail(CL6Reason.RISK_EVIDENCE_INVALID)
    expected_scope = _account_scope(raw_account, checked_environment, key_id, key)
    if not _hmac.compare_digest(expected_scope, account):
        _fail(CL6Reason.ACCOUNT_SCOPE_INVALID)
    try:
        source = _canonical_bytes(
            {
                "policy": _asdict(policy),
                "state": _risk.RiskState.to_dict(state),
            }
        )
        if len(source) > MAX_RISK_CANONICAL_BYTES:
            _fail(CL6Reason.RISK_EVIDENCE_INVALID)
        policy_hash = policy.policy_hash
        state_hash = _risk_runtime.risk_state_guard_hash(state)
    except CL6Error:
        raise
    except Exception:  # noqa: BLE001 - accepted Risk boundary
        failure = CL6Error(CL6Reason.RISK_EVIDENCE_INVALID)
        raise failure from None
    _require_hash(policy_hash, CL6Reason.RISK_EVIDENCE_INVALID)
    _require_hash(state_hash, CL6Reason.RISK_EVIDENCE_INVALID)
    if captured_ns > evaluated_ns:
        _fail(CL6Reason.DEPENDENCY_FROM_FUTURE)
    placeholder = RiskGuardEvidence(
        account_scope_sha256=account,
        environment=checked_environment,
        captured_at=captured_at,
        risk_policy_hash=policy_hash,
        risk_state_guard_hash=state_hash,
        risk_state_version=state.version,
        identity_key_id=key_id,
        evidence_identity_sha256="0" * 64,
    )
    identity = _risk_evidence_identity(placeholder, key)
    return _replace(placeholder, evidence_identity_sha256=identity)


def _validated_portfolio_evidence(
    value: PortfolioIdentityEvidence,
    identity_key: bytes,
) -> PortfolioIdentityEvidence:
    try:
        checked = PortfolioIdentityEvidence(
            **{
                field.name: object.__getattribute__(value, field.name)
                for field in _fields(PortfolioIdentityEvidence)
            }
        )
        expected = _portfolio_evidence_identity(checked, identity_key)
        if not _hmac.compare_digest(expected, checked.evidence_identity_sha256):
            _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        if checked.canonical_bytes != value.canonical_bytes:
            _fail(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
        return checked
    except CL6Error:
        raise
    except Exception:  # noqa: BLE001 - forged Portfolio evidence
        failure = CL6Error(CL6Reason.PORTFOLIO_EVIDENCE_INVALID)
    raise failure from None


def _validated_risk_evidence(
    value: RiskGuardEvidence,
    identity_key: bytes,
) -> RiskGuardEvidence:
    try:
        checked = RiskGuardEvidence(
            **{
                field.name: object.__getattribute__(value, field.name)
                for field in _fields(RiskGuardEvidence)
            }
        )
        expected = _risk_evidence_identity(checked, identity_key)
        if not _hmac.compare_digest(expected, checked.evidence_identity_sha256):
            _fail(CL6Reason.RISK_EVIDENCE_INVALID)
        if checked.canonical_bytes != value.canonical_bytes:
            _fail(CL6Reason.RISK_EVIDENCE_INVALID)
        return checked
    except CL6Error:
        raise
    except Exception:  # noqa: BLE001 - forged Risk evidence
        failure = CL6Error(CL6Reason.RISK_EVIDENCE_INVALID)
    raise failure from None


def _rebuild_availability(
    ledger_export_bytes: bytes,
    reconciliation: _cl4.CashReconciliation,
    positions: _cl5.BrokerPositionsCashProof,
    reservations: _cl5.CentralReservationProjection,
    availability: _cl5.CashAvailabilitySnapshot,
    identity_key: bytes,
) -> _cl5.CashAvailabilitySnapshot:
    try:
        rebuilt = _cl5.build_cash_availability(
            ledger_export_bytes,
            reconciliation,
            positions,
            reservations,
            evaluated_at=availability.evaluated_at,
            identity_key=identity_key,
        )
    except _cl5.CL5Error as error:
        primary = (
            CL6Reason.INTERNAL_BOUNDARY_FAILED
            if error.reason is _cl5.CL5Reason.INTERNAL_BOUNDARY_FAILED
            else CL6Reason.CASH_AVAILABILITY_INVALID
        )
        failure = CL6Error(primary, error.reason)
    except Exception:  # noqa: BLE001 - accepted CL5 boundary
        failure = CL6Error(CL6Reason.INTERNAL_BOUNDARY_FAILED)
    else:
        if type(rebuilt) is not _cl5.CashAvailabilitySnapshot:
            failure = CL6Error(CL6Reason.INTERNAL_BOUNDARY_FAILED)
        else:
            try:
                if rebuilt.canonical_bytes == availability.canonical_bytes:
                    return rebuilt
            except Exception:  # noqa: BLE001, S110 - forged snapshot boundary
                pass
            failure = CL6Error(CL6Reason.CASH_AVAILABILITY_INVALID)
    raise failure from None


def _context_identity(
    context: PortfolioRiskCashContext,
    identity_key: bytes,
) -> str:
    return _hmac_sha256(identity_key, context._identity_dict())


def build_portfolio_risk_cash_context(
    ledger_export_bytes: bytes,
    reconciliation: CashReconciliation,
    positions: BrokerPositionsCashProof,
    reservations: CentralReservationProjection,
    availability: CashAvailabilitySnapshot,
    portfolio: PortfolioIdentityEvidence,
    risk_guard: RiskGuardEvidence,
    *,
    evaluated_at: str,
    identity_key: bytes,
    identity_key_id: str,
) -> PortfolioRiskCashContext:
    if (
        type(ledger_export_bytes) is not bytes
        or type(reconciliation) is not _cl4.CashReconciliation
        or type(positions) is not _cl5.BrokerPositionsCashProof
        or type(reservations) is not _cl5.CentralReservationProjection
        or type(availability) is not _cl5.CashAvailabilitySnapshot
        or type(portfolio) is not PortfolioIdentityEvidence
        or type(risk_guard) is not RiskGuardEvidence
        or type(evaluated_at) is not str
        or type(identity_key) is not bytes
        or type(identity_key_id) is not str
    ):
        _fail(CL6Reason.TYPE_INVALID)
    if not 1 <= len(ledger_export_bytes) <= MAX_LEDGER_EXPORT_BYTES:
        _fail(CL6Reason.LEDGER_EXPORT_INVALID)
    evaluated_ns = _timestamp_ns(evaluated_at)
    key = _require_key(identity_key)
    key_id = _require_key_id(identity_key_id)
    rebuilt = _rebuild_availability(
        ledger_export_bytes,
        reconciliation,
        positions,
        reservations,
        availability,
        key,
    )
    checked_portfolio = _validated_portfolio_evidence(portfolio, key)
    checked_risk = _validated_risk_evidence(risk_guard, key)
    account = rebuilt.account_scope_sha256
    environment = rebuilt.environment
    if (
        rebuilt.currency != "RUB"
        or checked_portfolio.account_scope_sha256 != account
        or checked_risk.account_scope_sha256 != account
        or positions.account_scope_sha256 != account
        or reservations.account_scope_sha256 != account
        or reconciliation.proof.account_scope_sha256 != account
    ):
        _fail(CL6Reason.ACCOUNT_SCOPE_INVALID)
    if (
        environment is not _broker.BrokerEnvironment.SANDBOX
        or checked_portfolio.environment is not environment
        or checked_risk.environment is not environment
        or positions.environment is not environment
        or reservations.environment is not environment
        or reconciliation.proof.environment is not environment
        or positions.identity_key_id != key_id
        or reservations.identity_key_id != key_id
        or reconciliation.proof.identity_key_id != key_id
        or checked_portfolio.identity_key_id != key_id
        or checked_risk.identity_key_id != key_id
    ):
        _fail(CL6Reason.EVIDENCE_CORRELATION_INVALID)
    if (
        rebuilt.ledger_export_sha256 != _sha256(ledger_export_bytes)
        or rebuilt.ledger_revision != reconciliation.projection.ledger_revision
        or rebuilt.ledger_head_sha256 != reconciliation.projection.ledger_head_sha256
        or rebuilt.reconciliation_sha256 != reconciliation.sha256
        or rebuilt.central_order_revision != reservations.central_order_revision
        or rebuilt.central_reservation_projection_hash
        != reservations.central_reservation_projection_hash
    ):
        _fail(CL6Reason.EVIDENCE_CORRELATION_INVALID)
    timestamps = (
        rebuilt.evaluated_at,
        rebuilt.broker_cash_as_of,
        rebuilt.broker_positions_as_of,
        rebuilt.central_projection_evaluated_at,
        checked_portfolio.portfolio_snapshot_at,
        checked_portfolio.captured_at,
        checked_risk.captured_at,
    )
    timestamp_values = tuple(_timestamp_ns(value) for value in timestamps)
    if any(value > evaluated_ns for value in timestamp_values):
        _fail(CL6Reason.DEPENDENCY_FROM_FUTURE)
    availability_times = timestamp_values[:4]
    portfolio_times = timestamp_values[4:6]
    risk_time = timestamp_values[6]
    availability_ready = (
        rebuilt.status is _cl5.AvailabilityStatus.READY
        and rebuilt.availability_reason is _cl5.AvailabilityReason.READY
        and rebuilt.free_investable_cash is not None
    )
    portfolio_ready = (
        checked_portfolio.portfolio_schema_version == 2
        and checked_portfolio.portfolio_source == "CANONICAL"
        and checked_portfolio.migration_status == "COMPLETED"
        and checked_portfolio.legacy_read_path_enabled is False
        and checked_portfolio.freshness == "FRESH"
        and checked_portfolio.blocking is False
        and checked_portfolio.state_status not in {"BLOCKED", "MANUAL_REVIEW_REQUIRED"}
    )
    if not availability_ready:
        status = RiskCashContextStatus.BLOCKED
        reason = RiskCashContextReason.CASH_AVAILABILITY_NOT_READY
    elif any(evaluated_ns - value > MAX_CONTEXT_AGE_NS for value in availability_times):
        status = RiskCashContextStatus.BLOCKED
        reason = RiskCashContextReason.CASH_AVAILABILITY_STALE
    elif not portfolio_ready:
        status = RiskCashContextStatus.BLOCKED
        reason = RiskCashContextReason.PORTFOLIO_NOT_READY
    elif any(evaluated_ns - value > MAX_CONTEXT_AGE_NS for value in portfolio_times):
        status = RiskCashContextStatus.BLOCKED
        reason = RiskCashContextReason.PORTFOLIO_STALE
    elif evaluated_ns - risk_time > MAX_CONTEXT_AGE_NS:
        status = RiskCashContextStatus.BLOCKED
        reason = RiskCashContextReason.RISK_GUARD_STALE
    elif max(timestamp_values) - min(timestamp_values) > MAX_CONTEXT_SKEW_NS:
        status = RiskCashContextStatus.BLOCKED
        reason = RiskCashContextReason.MIXED_EVIDENCE_SNAPSHOT
    else:
        status = RiskCashContextStatus.READY_FOR_LOCKED_REVALIDATION
        reason = RiskCashContextReason.READY
    free = (
        _money(rebuilt.free_investable_cash, CL6Reason.CASH_AVAILABILITY_INVALID)
        if status is RiskCashContextStatus.READY_FOR_LOCKED_REVALIDATION
        else None
    )
    placeholder = PortfolioRiskCashContext(
        account_scope_sha256=account,
        environment=environment,
        evaluated_at=evaluated_at,
        status=status,
        reason=reason,
        availability_sha256=rebuilt.sha256,
        availability_status=rebuilt.status.value,
        availability_reason=rebuilt.availability_reason.value,
        availability_evaluated_at=rebuilt.evaluated_at,
        broker_cash_as_of=rebuilt.broker_cash_as_of,
        broker_positions_as_of=rebuilt.broker_positions_as_of,
        free_investable_cash=free,
        ledger_export_sha256=rebuilt.ledger_export_sha256,
        ledger_revision=rebuilt.ledger_revision,
        ledger_head_sha256=rebuilt.ledger_head_sha256,
        reconciliation_sha256=rebuilt.reconciliation_sha256,
        central_order_revision=rebuilt.central_order_revision,
        reservation_projection_hash=rebuilt.central_reservation_projection_hash,
        central_projection_evaluated_at=rebuilt.central_projection_evaluated_at,
        portfolio_evidence_sha256=checked_portfolio.sha256,
        portfolio_revision=checked_portfolio.portfolio_revision,
        portfolio_decision_checksum=checked_portfolio.portfolio_decision_checksum,
        portfolio_document_checksum=checked_portfolio.portfolio_document_checksum,
        portfolio_snapshot_at=checked_portfolio.portfolio_snapshot_at,
        portfolio_captured_at=checked_portfolio.captured_at,
        risk_guard_evidence_sha256=checked_risk.sha256,
        risk_guard_captured_at=checked_risk.captured_at,
        risk_policy_hash=checked_risk.risk_policy_hash,
        risk_state_guard_hash=checked_risk.risk_state_guard_hash,
        identity_key_id=key_id,
        context_identity_sha256="0" * 64,
    )
    identity = _context_identity(placeholder, key)
    return _replace(placeholder, context_identity_sha256=identity)
