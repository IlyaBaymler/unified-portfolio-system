"""Pure T-Bank read adapter and deterministic CL1 proposal construction."""

from __future__ import annotations

import hashlib as _hashlib
import hmac as _hmac
import re as _re
from collections.abc import Callable as _Callable
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from datetime import date as _date
from enum import StrEnum as _StrEnum
from types import MappingProxyType as _MappingProxyType
from typing import Protocol as _Protocol

from trading_robot import cash_ledger_domain as _ledger
from trading_robot import cash_ledger_persistence as _inbox

del annotations

__all__ = (
    "BrokerEnvironment",
    "BrokerDecisionKind",
    "BrokerDecisionReason",
    "BrokerReadReason",
    "BrokerReadError",
    "RetryPolicy",
    "BrokerReadRequest",
    "BrokerReadTransport",
    "BrokerTransportFailureKind",
    "BrokerTransportFailure",
    "BrokerDecision",
    "CompletenessWatermark",
    "BrokerReadBatch",
    "TBANK_OPERATION_CODEC",
    "money_value_to_money",
    "normalize_provider_timestamp",
    "collect_tbank_operations",
)


class BrokerEnvironment(_StrEnum):
    PRODUCTION = "PRODUCTION"
    SANDBOX = "SANDBOX"


class BrokerDecisionKind(_StrEnum):
    TRANSACTION_PROPOSED = "TRANSACTION_PROPOSED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    NOT_LEDGER_RELEVANT = "NOT_LEDGER_RELEVANT"


class BrokerDecisionReason(_StrEnum):
    CLASSIFIED = "CLASSIFIED"
    CANCELED = "CANCELED"
    PENDING = "PENDING"
    STATE_UNSPECIFIED = "STATE_UNSPECIFIED"
    UNKNOWN_OPERATION_TYPE = "UNKNOWN_OPERATION_TYPE"
    UNSUPPORTED_OPERATION_TYPE = "UNSUPPORTED_OPERATION_TYPE"
    ZERO_CASH_EFFECT = "ZERO_CASH_EFFECT"
    AMOUNT_SIGN_AMBIGUOUS = "AMOUNT_SIGN_AMBIGUOUS"
    MULTI_COMPONENT_AMBIGUOUS = "MULTI_COMPONENT_AMBIGUOUS"
    PARTIAL_EXECUTION_AMBIGUOUS = "PARTIAL_EXECUTION_AMBIGUOUS"


class BrokerReadReason(_StrEnum):
    TYPE_INVALID = "TYPE_INVALID"
    CONFIGURATION_INVALID = "CONFIGURATION_INVALID"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    TRANSPORT_TIMEOUT = "TRANSPORT_TIMEOUT"
    TRANSPORT_CONNECTION_INTERRUPTED = "TRANSPORT_CONNECTION_INTERRUPTED"
    TRANSPORT_HTTP_RETRY_EXHAUSTED = "TRANSPORT_HTTP_RETRY_EXHAUSTED"
    TRANSPORT_HTTP_PERMANENT = "TRANSPORT_HTTP_PERMANENT"
    TRANSPORT_PROTOCOL_FAILURE = "TRANSPORT_PROTOCOL_FAILURE"
    CLOCK_INVALID = "CLOCK_INVALID"
    CLOCK_FAILURE = "CLOCK_FAILURE"
    WAIT_FAILURE = "WAIT_FAILURE"
    RESPONSE_BOUNDS_EXCEEDED = "RESPONSE_BOUNDS_EXCEEDED"
    RESPONSE_SCHEMA_INVALID = "RESPONSE_SCHEMA_INVALID"
    PAGE_LIMIT_EXCEEDED = "PAGE_LIMIT_EXCEEDED"
    ITEM_LIMIT_EXCEEDED = "ITEM_LIMIT_EXCEEDED"
    PAGINATION_INVARIANT_VIOLATION = "PAGINATION_INVARIANT_VIOLATION"
    DUPLICATE_ITEM = "DUPLICATE_ITEM"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    ITEM_OUTSIDE_WINDOW = "ITEM_OUTSIDE_WINDOW"
    OPERATION_ID_INVALID = "OPERATION_ID_INVALID"
    CURSOR_INVALID = "CURSOR_INVALID"
    ENUM_TOKEN_INVALID = "ENUM_TOKEN_INVALID"
    TIMESTAMP_INVALID = "TIMESTAMP_INVALID"
    MONEY_INVALID = "MONEY_INVALID"
    QUANTITY_INVALID = "QUANTITY_INVALID"
    CHILD_OPERATIONS_INVALID = "CHILD_OPERATIONS_INVALID"
    IDENTITY_INVALID = "IDENTITY_INVALID"
    INBOX_OBSERVATION_INVALID = "INBOX_OBSERVATION_INVALID"
    LEDGER_PROPOSAL_INVALID = "LEDGER_PROPOSAL_INVALID"


_CAUSE_TYPES = (_ledger.MoneyReason, _ledger.LedgerReason, _inbox.PersistenceReason)
_STAGE_RE = _re.compile(r"[A-Z][A-Z0-9_]{0,63}", _re.ASCII)
_TOKEN_RE = _re.compile(r"[A-Z][A-Z0-9_]{0,63}", _re.ASCII)
_PROVIDER_TOKEN_RE = _re.compile(r"[A-Z][A-Z0-9_]{0,95}", _re.ASCII)
_DECIMAL_RE = _re.compile(r"0|-?[1-9][0-9]*", _re.ASCII)
_CL1_TIMESTAMP_RE = _re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{9})Z",
    _re.ASCII,
)
_PROVIDER_TIMESTAMP_RE = _re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{3}|[0-9]{6}|[0-9]{9}))?Z",
    _re.ASCII,
)
_INT64_POSITIVE_MAX = "9223372036854775807"
_INT64_NEGATIVE_MAGNITUDE_MAX = "9223372036854775808"


class BrokerReadError(RuntimeError):
    """Closed, privacy-safe CL3 failure."""

    def __init__(
        self,
        reason: BrokerReadReason,
        cause_reason: object = None,
        *,
        stage: str | None = None,
        attempt_no: int | None = None,
        page_no: int | None = None,
        account_scope_sha256: str | None = None,
        request_fingerprint_sha256: str | None = None,
    ) -> None:
        if not isinstance(reason, BrokerReadReason):
            raise TypeError("reason")
        if cause_reason is not None and not isinstance(cause_reason, _CAUSE_TYPES):
            raise TypeError("cause_reason")
        evidence: dict[str, object] = {"reason": reason.value}
        if cause_reason is not None:
            evidence["cause_reason"] = cause_reason.value
        if stage is not None:
            if not isinstance(stage, str) or _STAGE_RE.fullmatch(stage) is None:
                raise ValueError("stage")
            evidence["stage"] = stage
        for key, value in (("attempt_no", attempt_no), ("page_no", page_no)):
            if value is not None:
                if type(value) is not int or value <= 0:
                    raise ValueError(key)
                evidence[key] = value
        for key, value in (
            ("account_scope_sha256", account_scope_sha256),
            ("request_fingerprint_sha256", request_fingerprint_sha256),
        ):
            if value is not None:
                if not _is_sha256(value):
                    raise ValueError(key)
                evidence[key] = value
        self.reason = reason
        self.cause_reason = cause_reason
        self.evidence = _MappingProxyType(evidence)
        message = reason.value
        if cause_reason is not None:
            message += ":" + cause_reason.value
        super().__init__(message)

    def __repr__(self) -> str:
        cause = (
            ""
            if self.cause_reason is None
            else f", cause_reason={self.cause_reason.value!r}"
        )
        return f"BrokerReadError(reason={self.reason.value!r}{cause})"


class BrokerTransportFailureKind(_StrEnum):
    TIMEOUT = "TIMEOUT"
    CONNECTION_INTERRUPTED = "CONNECTION_INTERRUPTED"
    HTTP_STATUS = "HTTP_STATUS"


class BrokerTransportFailure(RuntimeError):
    """Safe signal emitted by the caller-supplied transport."""

    def __init__(
        self,
        kind: BrokerTransportFailureKind,
        http_status: int | None = None,
    ) -> None:
        if not isinstance(kind, BrokerTransportFailureKind):
            raise BrokerReadError(BrokerReadReason.TYPE_INVALID)
        if kind is BrokerTransportFailureKind.HTTP_STATUS:
            if type(http_status) is not int:
                raise BrokerReadError(BrokerReadReason.TYPE_INVALID)
            if not 100 <= http_status <= 599 or 200 <= http_status <= 299:
                raise BrokerReadError(BrokerReadReason.CONFIGURATION_INVALID)
        elif http_status is not None:
            if type(http_status) is not int:
                raise BrokerReadError(BrokerReadReason.TYPE_INVALID)
            raise BrokerReadError(BrokerReadReason.CONFIGURATION_INVALID)
        self.kind = kind
        self.http_status = http_status
        message = kind.value if http_status is None else f"{kind.value}:{http_status}"
        super().__init__(message)

    def __repr__(self) -> str:
        if self.http_status is None:
            return f"BrokerTransportFailure(kind={self.kind.value!r})"
        return (
            "BrokerTransportFailure("
            f"kind={self.kind.value!r}, http_status={self.http_status})"
        )


class BrokerReadTransport(_Protocol):
    def __call__(
        self,
        request_payload: _Mapping[str, object],
        timeout_ns: int,
    ) -> _Mapping[str, object]: ...


def _raise(
    reason: BrokerReadReason, cause_reason: object = None, **evidence: object
) -> None:
    raise BrokerReadError(reason, cause_reason, **evidence)


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _int64_decimal_exceeds(value: str) -> bool:
    negative = value.startswith("-")
    digits = value[1:] if negative else value
    maximum = _INT64_NEGATIVE_MAGNITUDE_MAX if negative else _INT64_POSITIVE_MAX
    return len(digits) > len(maximum) or (
        len(digits) == len(maximum) and digits > maximum
    )


def _timestamp_parts(
    value: object, pattern: _re.Pattern[str]
) -> tuple[str, str] | None:
    if not isinstance(value, str) or _contains_surrogate(value):
        return None
    match = pattern.fullmatch(value)
    if match is None:
        return None
    year, month, day_value, hour, minute, second = map(int, match.groups()[:6])
    if hour > 23 or minute > 59 or second > 59:
        return None
    try:
        _date(year, month, day_value)
    except ValueError:
        return None
    fraction = match.group(7) or ""
    prefix = value[:19]
    return prefix, fraction


def _is_cl1_timestamp(value: object) -> bool:
    return _timestamp_parts(value, _CL1_TIMESTAMP_RE) is not None


@_dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    per_attempt_timeout_ns: int
    backoff_ns: tuple[int, ...]

    def __post_init__(self) -> None:
        if (
            type(self.max_attempts) is not int
            or type(self.per_attempt_timeout_ns) is not int
        ):
            _raise(BrokerReadReason.TYPE_INVALID)
        if not isinstance(self.backoff_ns, tuple) or any(
            type(value) is not int for value in self.backoff_ns
        ):
            _raise(BrokerReadReason.TYPE_INVALID)
        if not 1 <= self.max_attempts <= 4:
            _raise(BrokerReadReason.CONFIGURATION_INVALID)
        if not 1 <= self.per_attempt_timeout_ns <= 30_000_000_000:
            _raise(BrokerReadReason.CONFIGURATION_INVALID)
        if len(self.backoff_ns) != self.max_attempts - 1:
            _raise(BrokerReadReason.CONFIGURATION_INVALID)
        if any(not 0 <= value <= 10_000_000_000 for value in self.backoff_ns):
            _raise(BrokerReadReason.CONFIGURATION_INVALID)
        if any(
            left > right for left, right in zip(self.backoff_ns, self.backoff_ns[1:])
        ):
            _raise(BrokerReadReason.CONFIGURATION_INVALID)
        if sum(self.backoff_ns) > 20_000_000_000:
            _raise(BrokerReadReason.CONFIGURATION_INVALID)


@_dataclass(frozen=True, slots=True)
class BrokerReadRequest:
    environment: BrokerEnvironment
    raw_account_id: str = _field(repr=False)
    identity_key: bytes = _field(repr=False)
    identity_key_id: str
    from_inclusive: str
    to_exclusive: str
    limit: int
    max_pages: int
    max_items: int
    absolute_deadline_ns: int
    retry_policy: RetryPolicy
    transport: BrokerReadTransport = _field(repr=False)
    monotonic_ns: _Callable[[], int] = _field(repr=False)
    wait_ns: _Callable[[int], object] = _field(repr=False)

    def __post_init__(self) -> None:
        typed = (
            isinstance(self.environment, BrokerEnvironment)
            and isinstance(self.raw_account_id, str)
            and isinstance(self.identity_key, bytes)
            and isinstance(self.identity_key_id, str)
            and isinstance(self.from_inclusive, str)
            and isinstance(self.to_exclusive, str)
            and type(self.limit) is int
            and type(self.max_pages) is int
            and type(self.max_items) is int
            and type(self.absolute_deadline_ns) is int
            and isinstance(self.retry_policy, RetryPolicy)
            and callable(self.transport)
            and callable(self.monotonic_ns)
            and callable(self.wait_ns)
        )
        if not typed:
            _raise(BrokerReadReason.TYPE_INVALID)
        if (
            not 1 <= len(self.raw_account_id) <= 256
            or _contains_surrogate(self.raw_account_id)
            or not _is_cl1_timestamp(self.from_inclusive)
            or not _is_cl1_timestamp(self.to_exclusive)
            or self.from_inclusive >= self.to_exclusive
            or not 1 <= self.limit <= 1000
            or not 1 <= self.max_pages <= 100
            or not 1 <= self.max_items <= 100_000
            or self.absolute_deadline_ns <= 0
        ):
            _raise(BrokerReadReason.CONFIGURATION_INVALID)
        if (
            not 32 <= len(self.identity_key) <= 64
            or _contains_surrogate(self.identity_key_id)
            or _TOKEN_RE.fullmatch(self.identity_key_id) is None
        ):
            _raise(BrokerReadReason.CONFIGURATION_INVALID)


@_dataclass(frozen=True, slots=True)
class BrokerDecision:
    kind: BrokerDecisionKind
    reason: BrokerDecisionReason
    observation: _inbox.InboxObservation
    transaction_proposal: _ledger.LedgerTransaction | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, BrokerDecisionKind)
            or not isinstance(self.reason, BrokerDecisionReason)
            or not isinstance(self.observation, _inbox.InboxObservation)
        ):
            _raise(BrokerReadReason.TYPE_INVALID)
        valid = False
        if self.kind is BrokerDecisionKind.TRANSACTION_PROPOSED:
            valid = self.reason is BrokerDecisionReason.CLASSIFIED and isinstance(
                self.transaction_proposal, _ledger.LedgerTransaction
            )
        elif self.kind is BrokerDecisionKind.NOT_LEDGER_RELEVANT:
            valid = (
                self.reason is BrokerDecisionReason.CANCELED
                and self.transaction_proposal is None
            )
        elif self.kind is BrokerDecisionKind.REVIEW_REQUIRED:
            valid = (
                self.reason
                in {
                    BrokerDecisionReason.PENDING,
                    BrokerDecisionReason.STATE_UNSPECIFIED,
                    BrokerDecisionReason.UNKNOWN_OPERATION_TYPE,
                    BrokerDecisionReason.UNSUPPORTED_OPERATION_TYPE,
                    BrokerDecisionReason.ZERO_CASH_EFFECT,
                    BrokerDecisionReason.AMOUNT_SIGN_AMBIGUOUS,
                    BrokerDecisionReason.MULTI_COMPONENT_AMBIGUOUS,
                    BrokerDecisionReason.PARTIAL_EXECUTION_AMBIGUOUS,
                }
                and self.transaction_proposal is None
            )
        if not valid:
            _raise(BrokerReadReason.CONFIGURATION_INVALID)


@_dataclass(frozen=True, slots=True)
class CompletenessWatermark:
    account_scope_sha256: str
    from_inclusive: str
    to_exclusive: str
    page_count: int
    item_count: int
    request_fingerprint_sha256: str
    page_chain_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.account_scope_sha256, str)
            or not isinstance(self.from_inclusive, str)
            or not isinstance(self.to_exclusive, str)
            or type(self.page_count) is not int
            or type(self.item_count) is not int
            or not isinstance(self.request_fingerprint_sha256, str)
            or not isinstance(self.page_chain_sha256, str)
        ):
            _raise(BrokerReadReason.TYPE_INVALID)
        if (
            not _is_sha256(self.account_scope_sha256)
            or not _is_cl1_timestamp(self.from_inclusive)
            or not _is_cl1_timestamp(self.to_exclusive)
            or self.from_inclusive >= self.to_exclusive
            or self.page_count <= 0
            or self.item_count < 0
            or not _is_sha256(self.request_fingerprint_sha256)
            or not _is_sha256(self.page_chain_sha256)
        ):
            _raise(BrokerReadReason.CONFIGURATION_INVALID)

    def _canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "complete": True,
            "domain": "v3.10-cl3-completeness-watermark",
            "from_inclusive": self.from_inclusive,
            "item_count": str(self.item_count),
            "page_chain_sha256": self.page_chain_sha256,
            "page_count": str(self.page_count),
            "request_fingerprint_sha256": self.request_fingerprint_sha256,
            "to_exclusive": self.to_exclusive,
            "version": 1,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(
            self._canonical_dict(), BrokerReadReason.IDENTITY_INVALID
        )

    @property
    def sha256(self) -> str:
        return _hashlib.sha256(self.canonical_bytes).hexdigest()


@_dataclass(frozen=True, slots=True)
class BrokerReadBatch:
    decisions: tuple[BrokerDecision, ...]
    watermark: CompletenessWatermark

    def __post_init__(self) -> None:
        if (
            not isinstance(self.decisions, tuple)
            or any(not isinstance(value, BrokerDecision) for value in self.decisions)
            or not isinstance(self.watermark, CompletenessWatermark)
        ):
            _raise(BrokerReadReason.TYPE_INVALID)


_SCHEMA_JSON_ASCII = '{"domain":"v3.10-operation-inbox-codec-schema","fields":[{"allowed_values":null,"key":"child_operation_count","kind":"INTEGER","max_scalars":null,"maximum":"256","minimum":"0","required":true},{"allowed_values":null,"key":"commission_minor_units","kind":"STRING","max_scalars":"29","maximum":null,"minimum":null,"required":true},{"allowed_values":["PAYMENT"],"key":"component","kind":"STRING","max_scalars":"16","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"effective_at","kind":"STRING","max_scalars":"30","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"has_parent_operation","kind":"BOOLEAN","max_scalars":null,"maximum":null,"minimum":null,"required":true},{"allowed_values":["OPERATION_STATE_CANCELED","OPERATION_STATE_EXECUTED","OPERATION_STATE_PROGRESS","OPERATION_STATE_UNSPECIFIED"],"key":"operation_state","kind":"STRING","max_scalars":"32","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"operation_type","kind":"STRING","max_scalars":"96","maximum":null,"minimum":null,"required":true},{"allowed_values":["RUB"],"key":"payment_currency","kind":"STRING","max_scalars":"3","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"payment_minor_units","kind":"STRING","max_scalars":"29","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"quantity","kind":"STRING","max_scalars":"20","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"quantity_done","kind":"STRING","max_scalars":"20","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"quantity_rest","kind":"STRING","max_scalars":"20","maximum":null,"minimum":null,"required":true}],"version":1}'

TBANK_OPERATION_CODEC = _inbox.CodecDescriptor(
    codec_id="TBANK_OPERATION_V1",
    schema_json_ascii=_SCHEMA_JSON_ASCII,
    schema_sha256="5e84067595ee002c35593fe52232ef577debba4be64092711f3d4ddc96b85794",
)


def money_value_to_money(value: object) -> Money:  # noqa: F821
    try:
        if not isinstance(value, _Mapping):
            _raise(BrokerReadReason.MONEY_INVALID, _ledger.MoneyReason.TYPE_INVALID)
        if frozenset(value) != {
            "currency",
            "nano",
            "units",
        }:
            _raise(
                BrokerReadReason.MONEY_INVALID,
                _ledger.MoneyReason.CANONICAL_FORMAT_INVALID,
            )
        currency = value["currency"]
        units_text = value["units"]
        nano = value["nano"]
        if not isinstance(currency, str):
            _raise(BrokerReadReason.MONEY_INVALID, _ledger.MoneyReason.TYPE_INVALID)
        if currency != "RUB":
            _raise(
                BrokerReadReason.MONEY_INVALID, _ledger.MoneyReason.CURRENCY_UNSUPPORTED
            )
        if not isinstance(units_text, str) or _DECIMAL_RE.fullmatch(units_text) is None:
            _raise(
                BrokerReadReason.MONEY_INVALID,
                _ledger.MoneyReason.CANONICAL_FORMAT_INVALID,
            )
        if _int64_decimal_exceeds(units_text):
            _raise(
                BrokerReadReason.MONEY_INVALID,
                _ledger.MoneyReason.WIRE_UNITS_OUT_OF_RANGE,
            )
        units = int(units_text)
        if type(nano) is not int:
            _raise(BrokerReadReason.MONEY_INVALID, _ledger.MoneyReason.TYPE_INVALID)
        if not -999_999_999 <= nano <= 999_999_999:
            _raise(
                BrokerReadReason.MONEY_INVALID,
                _ledger.MoneyReason.WIRE_NANO_OUT_OF_RANGE,
            )
        return _ledger.Money.from_units_nano(units=units, nano=nano, currency=currency)
    except BrokerReadError:
        raise
    except _ledger.MoneyError as error:
        failure = BrokerReadError(BrokerReadReason.MONEY_INVALID, error.reason)
    except Exception:
        failure = BrokerReadError(
            BrokerReadReason.MONEY_INVALID,
            _ledger.MoneyReason.CANONICAL_FORMAT_INVALID,
        )
    raise failure from None


def normalize_provider_timestamp(value: object) -> str:
    parts = _timestamp_parts(value, _PROVIDER_TIMESTAMP_RE)
    if parts is None:
        _raise(BrokerReadReason.TIMESTAMP_INVALID)
    prefix, fraction = parts
    return f"{prefix}.{fraction.ljust(9, '0')}Z"


def _canonical_bytes(value: object, reason: BrokerReadReason) -> bytes:
    try:
        return _inbox.canonical_json_bytes(value)
    except _inbox.PersistenceError as error:
        failure = BrokerReadError(reason, error.reason)
    except Exception:
        failure = BrokerReadError(reason)
    raise failure from None


def _plain_sha(value: object, reason: BrokerReadReason) -> str:
    return _hashlib.sha256(_canonical_bytes(value, reason)).hexdigest()


def _keyed_sha(request: BrokerReadRequest, value: object) -> str:
    data = _canonical_bytes(value, BrokerReadReason.IDENTITY_INVALID)
    return _hmac.new(request.identity_key, data, _hashlib.sha256).hexdigest()


def _account_scope(request: BrokerReadRequest) -> str:
    return _keyed_sha(
        request,
        {
            "account_id": request.raw_account_id,
            "domain": "v3.10-cl3-account-scope",
            "environment": request.environment.value,
            "identity_key_id": request.identity_key_id,
            "provider": "TBANK",
            "version": 1,
        },
    )


def _cursor_evidence(
    request: BrokerReadRequest,
    cursor: str | None,
    role: str,
) -> str:
    return _keyed_sha(
        request,
        {
            "cursor": cursor,
            "domain": "v3.10-cl3-cursor-evidence",
            "identity_key_id": request.identity_key_id,
            "role": role,
            "version": 1,
        },
    )


def _request_fingerprint(request: BrokerReadRequest, account_scope: str) -> str:
    return _plain_sha(
        {
            "account_scope_sha256": account_scope,
            "domain": "v3.10-cl3-read-request",
            "from_inclusive": request.from_inclusive,
            "identity_key_id": request.identity_key_id,
            "limit": str(request.limit),
            "operation_types": [],
            "state": "OPERATION_STATE_UNSPECIFIED",
            "to_exclusive": request.to_exclusive,
            "version": 1,
            "without_commissions": False,
            "without_overnights": False,
            "without_trades": False,
        },
        BrokerReadReason.IDENTITY_INVALID,
    )


_REQUIRED_ITEM_KEYS = frozenset(
    {
        "brokerAccountId",
        "childOperations",
        "commission",
        "cursor",
        "date",
        "id",
        "payment",
        "quantity",
        "quantityDone",
        "quantityRest",
        "state",
        "type",
    }
)
_OPTIONAL_ITEM_KEYS = frozenset(
    {
        "accruedInt",
        "assetUid",
        "cancelDateTime",
        "cancelReason",
        "description",
        "figi",
        "classCode",
        "instrumentKind",
        "instrumentType",
        "instrumentUid",
        "name",
        "parentOperationId",
        "positionUid",
        "price",
        "ticker",
        "tradesInfo",
        "yield",
        "yieldRelative",
    }
)
_VALID_STATES = frozenset(
    {
        "OPERATION_STATE_CANCELED",
        "OPERATION_STATE_EXECUTED",
        "OPERATION_STATE_PROGRESS",
        "OPERATION_STATE_UNSPECIFIED",
    }
)
_KNOWN_TYPES = frozenset(
    """OPERATION_TYPE_UNSPECIFIED OPERATION_TYPE_INPUT OPERATION_TYPE_BOND_TAX
OPERATION_TYPE_OUTPUT_SECURITIES OPERATION_TYPE_OVERNIGHT OPERATION_TYPE_TAX
OPERATION_TYPE_BOND_REPAYMENT_FULL OPERATION_TYPE_SELL_CARD OPERATION_TYPE_DIVIDEND_TAX
OPERATION_TYPE_OUTPUT OPERATION_TYPE_BOND_REPAYMENT OPERATION_TYPE_TAX_CORRECTION
OPERATION_TYPE_SERVICE_FEE OPERATION_TYPE_BENEFIT_TAX OPERATION_TYPE_MARGIN_FEE
OPERATION_TYPE_BUY OPERATION_TYPE_BUY_CARD OPERATION_TYPE_INPUT_SECURITIES
OPERATION_TYPE_SELL_MARGIN OPERATION_TYPE_BROKER_FEE OPERATION_TYPE_BUY_MARGIN
OPERATION_TYPE_DIVIDEND OPERATION_TYPE_SELL OPERATION_TYPE_COUPON
OPERATION_TYPE_SUCCESS_FEE OPERATION_TYPE_DIVIDEND_TRANSFER OPERATION_TYPE_ACCRUING_VARMARGIN
OPERATION_TYPE_WRITING_OFF_VARMARGIN OPERATION_TYPE_DELIVERY_BUY OPERATION_TYPE_DELIVERY_SELL
OPERATION_TYPE_TRACK_MFEE OPERATION_TYPE_TRACK_PFEE OPERATION_TYPE_TAX_PROGRESSIVE
OPERATION_TYPE_BOND_TAX_PROGRESSIVE OPERATION_TYPE_DIVIDEND_TAX_PROGRESSIVE
OPERATION_TYPE_BENEFIT_TAX_PROGRESSIVE OPERATION_TYPE_TAX_CORRECTION_PROGRESSIVE
OPERATION_TYPE_TAX_REPO_PROGRESSIVE OPERATION_TYPE_TAX_REPO OPERATION_TYPE_TAX_REPO_HOLD
OPERATION_TYPE_TAX_REPO_REFUND OPERATION_TYPE_TAX_REPO_HOLD_PROGRESSIVE
OPERATION_TYPE_TAX_REPO_REFUND_PROGRESSIVE OPERATION_TYPE_DIV_EXT
OPERATION_TYPE_TAX_CORRECTION_COUPON OPERATION_TYPE_CASH_FEE OPERATION_TYPE_OUT_FEE
OPERATION_TYPE_OUT_STAMP_DUTY OPERATION_TYPE_OUTPUT_SWIFT OPERATION_TYPE_INPUT_SWIFT
OPERATION_TYPE_OUTPUT_ACQUIRING OPERATION_TYPE_INPUT_ACQUIRING OPERATION_TYPE_OUTPUT_PENALTY
OPERATION_TYPE_ADVICE_FEE OPERATION_TYPE_TRANS_IIS_BS OPERATION_TYPE_TRANS_BS_BS
OPERATION_TYPE_OUT_MULTI OPERATION_TYPE_INP_MULTI OPERATION_TYPE_OVER_PLACEMENT
OPERATION_TYPE_OVER_COM OPERATION_TYPE_OVER_INCOME OPERATION_TYPE_OPTION_EXPIRATION
OPERATION_TYPE_FUTURE_EXPIRATION""".split()
)


def _prefixed(values: str) -> frozenset[str]:
    return frozenset("OPERATION_TYPE_" + value for value in values.split())


_DEPOSITS = _prefixed("INPUT INPUT_SWIFT INPUT_ACQUIRING INP_MULTI")
_WITHDRAWALS = _prefixed("OUTPUT OUTPUT_SWIFT OUTPUT_ACQUIRING OUT_MULTI")
_DIVIDENDS = _prefixed("DIVIDEND")
_COUPONS = _prefixed("COUPON")
_INTEREST = _prefixed("OVERNIGHT OVER_INCOME")
_FEES = _prefixed(
    "SERVICE_FEE MARGIN_FEE BROKER_FEE SUCCESS_FEE TRACK_MFEE TRACK_PFEE "
    "CASH_FEE OUT_FEE OUTPUT_PENALTY ADVICE_FEE OVER_COM"
)
_TAXES = _prefixed(
    "BOND_TAX TAX DIVIDEND_TAX BENEFIT_TAX TAX_PROGRESSIVE BOND_TAX_PROGRESSIVE "
    "DIVIDEND_TAX_PROGRESSIVE BENEFIT_TAX_PROGRESSIVE TAX_REPO_PROGRESSIVE TAX_REPO "
    "TAX_REPO_HOLD TAX_REPO_HOLD_PROGRESSIVE OUT_STAMP_DUTY"
)
_REFUNDS = _prefixed(
    "TAX_CORRECTION TAX_CORRECTION_PROGRESSIVE TAX_REPO_REFUND "
    "TAX_REPO_REFUND_PROGRESSIVE TAX_CORRECTION_COUPON"
)
_BUYS = _prefixed("BUY BUY_MARGIN DELIVERY_BUY")
_SELLS = _prefixed("SELL SELL_MARGIN DELIVERY_SELL")
_TRADE_TYPES = _BUYS | _SELLS
_CLASSIFICATIONS: dict[
    str, tuple[int, _ledger.LedgerClassification, _ledger.LedgerAccount]
] = {}
for _types, _sign, _classification, _counterpart in (
    (
        _DEPOSITS,
        1,
        _ledger.LedgerClassification.DEPOSIT,
        _ledger.LedgerAccount.EQUITY_EXTERNAL_FLOW,
    ),
    (
        _WITHDRAWALS,
        -1,
        _ledger.LedgerClassification.WITHDRAWAL,
        _ledger.LedgerAccount.EQUITY_EXTERNAL_FLOW,
    ),
    (
        _DIVIDENDS,
        1,
        _ledger.LedgerClassification.DIVIDEND,
        _ledger.LedgerAccount.INCOME_DIVIDEND,
    ),
    (
        _COUPONS,
        1,
        _ledger.LedgerClassification.COUPON,
        _ledger.LedgerAccount.INCOME_COUPON,
    ),
    (
        _INTEREST,
        1,
        _ledger.LedgerClassification.INTEREST,
        _ledger.LedgerAccount.INCOME_INTEREST,
    ),
    (
        _FEES,
        -1,
        _ledger.LedgerClassification.COMMISSION,
        _ledger.LedgerAccount.EXPENSE_COMMISSION,
    ),
    (_TAXES, -1, _ledger.LedgerClassification.TAX, _ledger.LedgerAccount.EXPENSE_TAX),
    (
        _REFUNDS,
        1,
        _ledger.LedgerClassification.REFUND,
        _ledger.LedgerAccount.EXPENSE_TAX,
    ),
    (
        _BUYS | _SELLS,
        0,
        _ledger.LedgerClassification.TRADE_SETTLEMENT,
        _ledger.LedgerAccount.ASSET_TRADE_CLEARING,
    ),
):
    for _operation_type in _types:
        _CLASSIFICATIONS[_operation_type] = (_sign, _classification, _counterpart)
del _types, _sign, _classification, _counterpart, _operation_type


def _response_preflight(value: object) -> object:
    unexpected_failure = False
    try:
        root: list[object] = [None]
        stack: list[tuple[object, int, list[object] | dict[str, object], int | str]] = [
            (value, 0, root, 0)
        ]
        seen_containers: set[int] = set()
        nodes = 0
        while stack:
            current, depth, parent, slot = stack.pop()
            nodes += 1
            if nodes > 200_000 or depth > 16:
                _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
            if current is None or isinstance(current, bool) or type(current) is int:
                parent[slot] = current
                continue
            if isinstance(current, str):
                plain_string = str.__str__(current)
                if str.__len__(plain_string) > 16_384 or _contains_surrogate(
                    plain_string
                ):
                    _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
                parent[slot] = plain_string
                continue
            if isinstance(current, _Mapping):
                identity = id(current)
                if identity in seen_containers:
                    _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
                seen_containers.add(identity)
                if isinstance(current, dict):
                    size = dict.__len__(current)
                    source_items = dict.items(current)
                else:
                    size = len(current)
                    source_items = current.items()
                if size > 10_000:
                    _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
                items: list[tuple[str, object]] = []
                keys: set[str] = set()
                for index, pair in enumerate(source_items):
                    if index >= 10_000:
                        _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
                    key, item = pair
                    if not isinstance(key, str):
                        _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
                    nodes += 1
                    if nodes > 200_000 or depth + 1 > 16:
                        _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
                    plain_key = str.__str__(key)
                    if (
                        str.__len__(plain_key) > 16_384
                        or _contains_surrogate(plain_key)
                        or plain_key in keys
                    ):
                        _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
                    keys.add(plain_key)
                    items.append((plain_key, item))
                if len(items) != size:
                    _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
                snapshot: dict[str, object] = {key: None for key, _ in items}
                parent[slot] = snapshot
                for key, item in reversed(items):
                    stack.append((item, depth + 1, snapshot, key))
                continue
            if isinstance(current, list):
                identity = id(current)
                if identity in seen_containers:
                    _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
                seen_containers.add(identity)
                size = list.__len__(current)
                if size > 10_000:
                    _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
                snapshot_list: list[object] = [None] * size
                parent[slot] = snapshot_list
                for index in range(size - 1, -1, -1):
                    stack.append(
                        (
                            list.__getitem__(current, index),
                            depth + 1,
                            snapshot_list,
                            index,
                        )
                    )
                continue
            _raise(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED)
    except BrokerReadError:
        raise
    except Exception:
        unexpected_failure = True
    if unexpected_failure:
        raise BrokerReadError(BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED) from None
    return root[0]


def _require_short_string(value: object, reason: BrokerReadReason) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 4096
        or _contains_surrogate(value)
    ):
        _raise(reason)
    return value


def _require_quantity(value: object) -> tuple[str, int]:
    if not isinstance(value, str) or _DECIMAL_RE.fullmatch(value) is None:
        _raise(BrokerReadReason.QUANTITY_INVALID)
    if _int64_decimal_exceeds(value):
        _raise(BrokerReadReason.QUANTITY_INVALID)
    parsed = int(value)
    return value, parsed


def _source_and_observation(
    request: BrokerReadRequest,
    account_scope: str,
    operation_id: str,
    state: str,
    content: dict[str, object],
    effective_at: str,
) -> tuple[_ledger.SourceIdentity, _inbox.InboxObservation]:
    content_bytes = _canonical_bytes(content, BrokerReadReason.IDENTITY_INVALID)
    content_sha = _hashlib.sha256(content_bytes).hexdigest()
    source_scope = _keyed_sha(
        request,
        {
            "account_scope_sha256": account_scope,
            "component": "PAYMENT",
            "domain": "v3.10-cl3-source-scope",
            "identity_key_id": request.identity_key_id,
            "operation_id": operation_id,
            "operation_state": state,
            "provider": "TBANK",
            "version": 1,
        },
    )
    try:
        source = _ledger.SourceIdentity(
            account_scope_sha256=account_scope,
            source_kind="TBANK_OPERATION",
            source_scope_sha256=source_scope,
            source_content_sha256=content_sha,
        )
    except _ledger.LedgerError as error:
        source_failure = error.reason
    else:
        source_failure = None
    if source_failure is not None:
        failure = BrokerReadError(BrokerReadReason.IDENTITY_INVALID, source_failure)
        raise failure from None
    provenance = _keyed_sha(
        request,
        {
            "account_scope_sha256": account_scope,
            "domain": "v3.10-cl3-provenance",
            "identity_key_id": request.identity_key_id,
            "operation_id": operation_id,
            "source_content_sha256": content_sha,
            "version": 1,
        },
    )
    try:
        observation = _inbox.InboxObservation.create(
            descriptor=TBANK_OPERATION_CODEC,
            content=content,
            source=source,
            observed_at=effective_at,
            provenance_sha256=provenance,
        )
    except _inbox.PersistenceError as error:
        observation_failure = error.reason
    else:
        observation_failure = None
    if observation_failure is not None:
        failure = BrokerReadError(
            BrokerReadReason.INBOX_OBSERVATION_INVALID,
            observation_failure,
        )
        raise failure from None
    return source, observation


def _review(
    reason: BrokerDecisionReason,
    observation: _inbox.InboxObservation,
) -> BrokerDecision:
    return BrokerDecision(BrokerDecisionKind.REVIEW_REQUIRED, reason, observation, None)


def _classify(
    *,
    state: str,
    operation_type: str,
    payment: _ledger.Money,
    commission: _ledger.Money,
    quantity: int,
    quantity_done: int,
    quantity_rest: int,
    child_count: int,
    has_parent: bool,
    effective_at: str,
    source: _ledger.SourceIdentity,
    observation: _inbox.InboxObservation,
) -> BrokerDecision:
    if state == "OPERATION_STATE_CANCELED":
        return BrokerDecision(
            BrokerDecisionKind.NOT_LEDGER_RELEVANT,
            BrokerDecisionReason.CANCELED,
            observation,
            None,
        )
    if state == "OPERATION_STATE_PROGRESS":
        return _review(BrokerDecisionReason.PENDING, observation)
    if state == "OPERATION_STATE_UNSPECIFIED":
        return _review(BrokerDecisionReason.STATE_UNSPECIFIED, observation)
    if state not in _VALID_STATES:
        _raise(BrokerReadReason.ENUM_TOKEN_INVALID)
    if operation_type not in _KNOWN_TYPES:
        return _review(BrokerDecisionReason.UNKNOWN_OPERATION_TYPE, observation)
    profile = _CLASSIFICATIONS.get(operation_type)
    if profile is None:
        return _review(BrokerDecisionReason.UNSUPPORTED_OPERATION_TYPE, observation)
    if child_count != 0 or has_parent or commission.minor_units != 0:
        return _review(BrokerDecisionReason.MULTI_COMPONENT_AMBIGUOUS, observation)
    if operation_type in _TRADE_TYPES and (
        quantity <= 0 or quantity_done != quantity or quantity_rest != 0
    ):
        return _review(BrokerDecisionReason.PARTIAL_EXECUTION_AMBIGUOUS, observation)
    if payment.minor_units == 0:
        return _review(BrokerDecisionReason.ZERO_CASH_EFFECT, observation)
    required_sign, classification, counterpart = profile
    if operation_type in _BUYS:
        required_sign = -1
    elif operation_type in _SELLS:
        required_sign = 1
    if (payment.minor_units > 0) != (required_sign > 0):
        return _review(BrokerDecisionReason.AMOUNT_SIGN_AMBIGUOUS, observation)
    try:
        transaction = _ledger.LedgerTransaction(
            classification=classification,
            effective_at=effective_at,
            source=source,
            postings=(
                _ledger.LedgerPosting(
                    1, _ledger.LedgerAccount.ASSET_BROKER_CASH, payment
                ),
                _ledger.LedgerPosting(2, counterpart, -payment),
            ),
            reversal_of_sha256=None,
            corrects_sha256=None,
        )
    except _ledger.MoneyError as error:
        proposal_failure: object = error.reason
    except _ledger.LedgerError as error:
        proposal_failure = error.reason
    else:
        proposal_failure = None
    if proposal_failure is not None:
        failure = BrokerReadError(
            BrokerReadReason.LEDGER_PROPOSAL_INVALID, proposal_failure
        )
        raise failure from None
    return BrokerDecision(
        BrokerDecisionKind.TRANSACTION_PROPOSED,
        BrokerDecisionReason.CLASSIFIED,
        observation,
        transaction,
    )


def _validate_item(
    item: object,
    request: BrokerReadRequest,
    account_scope: str,
    raw_operation_ids: set[str],
    item_cursors: set[str],
    source_scopes: set[str],
) -> tuple[BrokerDecision, str]:
    if not isinstance(item, _Mapping):
        _raise(BrokerReadReason.RESPONSE_SCHEMA_INVALID)
    mapping_failure = False
    try:
        keys = frozenset(item)
    except Exception:
        mapping_failure = True
        keys = frozenset()
    if mapping_failure:
        raise BrokerReadError(BrokerReadReason.RESPONSE_SCHEMA_INVALID) from None
    if not _REQUIRED_ITEM_KEYS.issubset(keys) or not keys.issubset(
        _REQUIRED_ITEM_KEYS | _OPTIONAL_ITEM_KEYS
    ):
        _raise(BrokerReadReason.RESPONSE_SCHEMA_INVALID)

    account_id = item["brokerAccountId"]
    if (
        not isinstance(account_id, str)
        or not 1 <= len(account_id) <= 4096
        or _contains_surrogate(account_id)
        or account_id != request.raw_account_id
    ):
        _raise(BrokerReadReason.ACCOUNT_MISMATCH)

    operation_id = _require_short_string(
        item["id"], BrokerReadReason.OPERATION_ID_INVALID
    )
    item_cursor = _require_short_string(item["cursor"], BrokerReadReason.CURSOR_INVALID)
    if operation_id in raw_operation_ids or item_cursor in item_cursors:
        _raise(BrokerReadReason.DUPLICATE_ITEM)
    raw_operation_ids.add(operation_id)
    item_cursors.add(item_cursor)

    state = item["state"]
    operation_type = item["type"]
    if (
        not isinstance(state, str)
        or _PROVIDER_TOKEN_RE.fullmatch(state) is None
        or state not in _VALID_STATES
        or not isinstance(operation_type, str)
        or _PROVIDER_TOKEN_RE.fullmatch(operation_type) is None
    ):
        _raise(BrokerReadReason.ENUM_TOKEN_INVALID)

    effective_at = normalize_provider_timestamp(item["date"])
    if not request.from_inclusive <= effective_at < request.to_exclusive:
        _raise(BrokerReadReason.ITEM_OUTSIDE_WINDOW)

    payment = money_value_to_money(item["payment"])
    commission = money_value_to_money(item["commission"])

    quantity_text, quantity = _require_quantity(item["quantity"])
    quantity_done_text, quantity_done = _require_quantity(item["quantityDone"])
    quantity_rest_text, quantity_rest = _require_quantity(item["quantityRest"])

    children = item["childOperations"]
    if not isinstance(children, list) or len(children) > 256:
        _raise(BrokerReadReason.CHILD_OPERATIONS_INVALID)
    parent: object = None
    if "parentOperationId" in item:
        parent = item["parentOperationId"]
        if (
            not isinstance(parent, str)
            or len(parent) > 4096
            or _contains_surrogate(parent)
        ):
            _raise(BrokerReadReason.CHILD_OPERATIONS_INVALID)
    has_parent = isinstance(parent, str) and bool(parent)

    content: dict[str, object] = {
        "child_operation_count": len(children),
        "commission_minor_units": str(commission.minor_units),
        "component": "PAYMENT",
        "effective_at": effective_at,
        "has_parent_operation": has_parent,
        "operation_state": state,
        "operation_type": operation_type,
        "payment_currency": payment.currency,
        "payment_minor_units": str(payment.minor_units),
        "quantity": quantity_text,
        "quantity_done": quantity_done_text,
        "quantity_rest": quantity_rest_text,
    }
    source, observation = _source_and_observation(
        request,
        account_scope,
        operation_id,
        state,
        content,
        effective_at,
    )
    if source.source_scope_sha256 in source_scopes:
        _raise(BrokerReadReason.DUPLICATE_ITEM)
    source_scopes.add(source.source_scope_sha256)
    decision = _classify(
        state=state,
        operation_type=operation_type,
        payment=payment,
        commission=commission,
        quantity=quantity,
        quantity_done=quantity_done,
        quantity_rest=quantity_rest,
        child_count=len(children),
        has_parent=has_parent,
        effective_at=effective_at,
        source=source,
        observation=observation,
    )
    return decision, _cursor_evidence(request, item_cursor, "ITEM")


_RETRYABLE_HTTP = frozenset({408, 429, 500, 502, 503, 504})


def _terminal_transport_reason(failure: BrokerTransportFailure) -> BrokerReadReason:
    if failure.kind is BrokerTransportFailureKind.TIMEOUT:
        return BrokerReadReason.TRANSPORT_TIMEOUT
    if failure.kind is BrokerTransportFailureKind.CONNECTION_INTERRUPTED:
        return BrokerReadReason.TRANSPORT_CONNECTION_INTERRUPTED
    if failure.http_status in _RETRYABLE_HTTP:
        return BrokerReadReason.TRANSPORT_HTTP_RETRY_EXHAUSTED
    return BrokerReadReason.TRANSPORT_HTTP_PERMANENT


def _transport_is_retryable(failure: BrokerTransportFailure) -> bool:
    return failure.kind is not BrokerTransportFailureKind.HTTP_STATUS or (
        failure.http_status in _RETRYABLE_HTTP
    )


def collect_tbank_operations(request: BrokerReadRequest) -> BrokerReadBatch:
    if not isinstance(request, BrokerReadRequest):
        _raise(BrokerReadReason.TYPE_INVALID)

    account_scope = _account_scope(request)
    request_fingerprint = _request_fingerprint(request, account_scope)
    last_clock = -1

    def read_clock() -> int:
        nonlocal last_clock
        clock_failure = False
        try:
            value = request.monotonic_ns()
        except Exception:
            clock_failure = True
            value = None
        if clock_failure:
            raise BrokerReadError(BrokerReadReason.CLOCK_FAILURE) from None
        if type(value) is not int or value < 0 or value < last_clock:
            _raise(BrokerReadReason.CLOCK_INVALID)
        last_clock = value
        return value

    def fetch_page(cursor: str | None, page_no: int) -> _Mapping[str, object]:
        for attempt_index in range(request.retry_policy.max_attempts):
            now = read_clock()
            remaining = request.absolute_deadline_ns - now
            if remaining <= 0:
                _raise(
                    BrokerReadReason.DEADLINE_EXCEEDED,
                    stage="BEFORE_TRANSPORT",
                    attempt_no=attempt_index + 1,
                    page_no=page_no,
                    account_scope_sha256=account_scope,
                    request_fingerprint_sha256=request_fingerprint,
                )
            timeout = min(request.retry_policy.per_attempt_timeout_ns, remaining)
            failure: BrokerTransportFailure | None = None
            protocol_failure = False
            payload = {
                "accountId": request.raw_account_id,
                "cursor": "" if cursor is None else cursor,
                "from": request.from_inclusive,
                "limit": request.limit,
                "operationTypes": [],
                "state": "OPERATION_STATE_UNSPECIFIED",
                "to": request.to_exclusive,
                "withoutCommissions": False,
                "withoutOvernights": False,
                "withoutTrades": False,
            }
            try:
                response = request.transport(payload, timeout)
            except BrokerTransportFailure as error:
                failure = error
            except Exception:
                protocol_failure = True
                response = None
            if protocol_failure:
                raise BrokerReadError(
                    BrokerReadReason.TRANSPORT_PROTOCOL_FAILURE,
                    stage="TRANSPORT",
                    attempt_no=attempt_index + 1,
                    page_no=page_no,
                    account_scope_sha256=account_scope,
                    request_fingerprint_sha256=request_fingerprint,
                ) from None
            if failure is None:
                return response
            if not _transport_is_retryable(failure):
                _raise(
                    BrokerReadReason.TRANSPORT_HTTP_PERMANENT,
                    stage="TRANSPORT",
                    attempt_no=attempt_index + 1,
                    page_no=page_no,
                    account_scope_sha256=account_scope,
                    request_fingerprint_sha256=request_fingerprint,
                )
            if attempt_index + 1 >= request.retry_policy.max_attempts:
                _raise(
                    _terminal_transport_reason(failure),
                    stage="TRANSPORT",
                    attempt_no=attempt_index + 1,
                    page_no=page_no,
                    account_scope_sha256=account_scope,
                    request_fingerprint_sha256=request_fingerprint,
                )
            backoff = request.retry_policy.backoff_ns[attempt_index]
            now = read_clock()
            if request.absolute_deadline_ns - now <= backoff:
                _raise(
                    BrokerReadReason.DEADLINE_EXCEEDED,
                    stage="BEFORE_WAIT",
                    attempt_no=attempt_index + 1,
                    page_no=page_no,
                    account_scope_sha256=account_scope,
                    request_fingerprint_sha256=request_fingerprint,
                )
            wait_failure = False
            try:
                request.wait_ns(backoff)
            except Exception:
                wait_failure = True
            if wait_failure:
                raise BrokerReadError(
                    BrokerReadReason.WAIT_FAILURE,
                    stage="WAIT",
                    attempt_no=attempt_index + 1,
                    page_no=page_no,
                    account_scope_sha256=account_scope,
                    request_fingerprint_sha256=request_fingerprint,
                ) from None
        raise AssertionError("unreachable")

    decisions: list[BrokerDecision] = []
    raw_operation_ids: set[str] = set()
    item_cursors: set[str] = set()
    source_scopes: set[str] = set()
    request_cursors: set[str | None] = {None}
    cursor: str | None = None
    page_no = 0
    previous_page_chain = "0" * 64

    while True:
        page_no += 1
        response = _response_preflight(fetch_page(cursor, page_no))
        if not isinstance(response, _Mapping):
            _raise(BrokerReadReason.RESPONSE_SCHEMA_INVALID)
        response_schema_failure = False
        try:
            if frozenset(response) != {"hasNext", "items", "nextCursor"}:
                _raise(BrokerReadReason.RESPONSE_SCHEMA_INVALID)
            has_next = response["hasNext"]
            items = response["items"]
            next_cursor = response["nextCursor"]
        except BrokerReadError:
            raise
        except Exception:
            response_schema_failure = True
            has_next = None
            items = None
            next_cursor = None
        if response_schema_failure:
            raise BrokerReadError(BrokerReadReason.RESPONSE_SCHEMA_INVALID) from None
        if (
            not isinstance(has_next, bool)
            or not isinstance(items, list)
            or len(items) > request.limit
            or not isinstance(next_cursor, str)
            or len(next_cursor) > 4096
            or _contains_surrogate(next_cursor)
        ):
            _raise(BrokerReadReason.RESPONSE_SCHEMA_INVALID)
        if len(decisions) + len(items) > request.max_items:
            _raise(BrokerReadReason.ITEM_LIMIT_EXCEEDED)
        if has_next:
            if not next_cursor or not items or next_cursor in request_cursors:
                _raise(BrokerReadReason.PAGINATION_INVARIANT_VIOLATION)
            if page_no >= request.max_pages:
                _raise(BrokerReadReason.PAGE_LIMIT_EXCEEDED)
        elif next_cursor:
            _raise(BrokerReadReason.PAGINATION_INVARIANT_VIOLATION)

        page_cursor_hashes: list[str] = []
        page_observation_hashes: list[str] = []
        for item in items:
            decision, item_cursor_hash = _validate_item(
                item,
                request,
                account_scope,
                raw_operation_ids,
                item_cursors,
                source_scopes,
            )
            decisions.append(decision)
            page_cursor_hashes.append(item_cursor_hash)
            page_observation_hashes.append(decision.observation.sha256)

        previous_page_chain = _plain_sha(
            {
                "domain": "v3.10-cl3-page-chain",
                "has_next": has_next,
                "item_cursor_evidence_sha256": page_cursor_hashes,
                "item_observation_sha256": page_observation_hashes,
                "next_cursor_evidence_sha256": _cursor_evidence(
                    request,
                    next_cursor if has_next else None,
                    "NEXT",
                ),
                "page_no": str(page_no),
                "previous_page_chain_sha256": previous_page_chain,
                "request_cursor_evidence_sha256": _cursor_evidence(
                    request,
                    cursor,
                    "REQUEST",
                ),
                "request_fingerprint_sha256": request_fingerprint,
                "version": 1,
            },
            BrokerReadReason.IDENTITY_INVALID,
        )
        if not has_next:
            break
        cursor = next_cursor
        request_cursors.add(cursor)

    watermark = CompletenessWatermark(
        account_scope_sha256=account_scope,
        from_inclusive=request.from_inclusive,
        to_exclusive=request.to_exclusive,
        page_count=page_no,
        item_count=len(decisions),
        request_fingerprint_sha256=request_fingerprint,
        page_chain_sha256=previous_page_chain,
    )
    return BrokerReadBatch(tuple(decisions), watermark)
