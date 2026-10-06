# ruff: noqa: BLE001
"""CL7 durable runtime cash authority and exact dispatch evidence.

The module is deliberately independent from provider sessions.  It owns the
single durable cash-authority record, its CAS chain, privacy-safe identities,
and the state transitions used by the CL7 orchestration boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from .cash_ledger_domain import Money
from .exact_own_funds import OwnFundsEvidence, timestamp_ns as _cash_timestamp_ns
from .locking import InterProcessFileLock, LockUnavailableError

_VERSION = 1
_DOMAIN = "v3.10-cl7-runtime-cash-authority"
_PROOF_DOMAIN = "v3.10-cl7-locked-dispatch-proof"
_PROOF_IDENTITY_DOMAIN = "v3.10-cl7-locked-dispatch-proof-identity"
_INTENT_SCOPE_DOMAIN = "v3.10-cl7-intent-scope"
_BROKER_VIEW_MISMATCH_OPERAND_DOMAIN = "v3.10-cl8-q7-broker-view-mismatch-operand-v1"
_BROKER_VIEW_MISMATCH_HASH_FIELDS = (
    "broker_total_cash_hmac_sha256",
    "positions_money_rub_hmac_sha256",
    "blocked_rub_hmac_sha256",
    "positions_plus_blocked_rub_hmac_sha256",
)
_BROKER_VIEW_MISMATCH_BOOL_FIELDS = (
    "broker_total_eq_positions_money",
    "broker_total_eq_blocked",
    "positions_money_eq_blocked",
    "broker_total_eq_positions_plus_blocked",
    "broker_total_is_zero",
    "positions_money_is_zero",
    "blocked_is_zero",
)
_BROKER_VIEW_MISMATCH_FIELDS = frozenset(
    _BROKER_VIEW_MISMATCH_HASH_FIELDS + _BROKER_VIEW_MISMATCH_BOOL_FIELDS
)
_INT64_MAX = 9_223_372_036_854_775_807
_MAX_RECORD_BYTES = 64 * 1024
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_KEY_ID_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_UINT_RE = re.compile(r"0|[1-9][0-9]{0,18}")
_TIMESTAMP_RE = re.compile(
    r"(?:[0-9]{4})-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{9}Z"
)


class RuntimeCashAuthorityState(StrEnum):
    LEGACY_ACTIVE = "LEGACY_ACTIVE"
    CUTOVER_PREPARED = "CUTOVER_PREPARED"
    CUTOVER_CONFIRMED = "CUTOVER_CONFIRMED"
    EXACT_CASH_DISARMED = "EXACT_CASH_DISARMED"
    EXACT_CASH_ARMED = "EXACT_CASH_ARMED"
    EXACT_CASH_DISPATCH_PENDING = "EXACT_CASH_DISPATCH_PENDING"
    EXACT_CASH_FEE_ADJUSTMENT_PENDING = "EXACT_CASH_FEE_ADJUSTMENT_PENDING"
    EXACT_CASH_SOURCE_CUTOVER_PENDING = "EXACT_CASH_SOURCE_CUTOVER_PENDING"
    EXACT_CASH_VERSIONED_DISARMED = "EXACT_CASH_VERSIONED_DISARMED"
    EXACT_CASH_VERSIONED_SYNC_PENDING = "EXACT_CASH_VERSIONED_SYNC_PENDING"
    EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING = "EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING"
    EXACT_CASH_VERSIONED_RISK_RESYNC_PENDING = "EXACT_CASH_VERSIONED_RISK_RESYNC_PENDING"
    EXACT_CASH_VERSIONED_ADMISSION_PENDING = "EXACT_CASH_VERSIONED_ADMISSION_PENDING"
    EXACT_CASH_VERSIONED_ARMED = "EXACT_CASH_VERSIONED_ARMED"
    EXACT_CASH_VERSIONED_DISPATCH_PENDING = "EXACT_CASH_VERSIONED_DISPATCH_PENDING"


class RuntimeCashAuthorityOwner(StrEnum):
    LEGACY_CASH_AUTHORITY = "LEGACY_CASH_AUTHORITY"
    CL7_EXACT_CASH_AUTHORITY = "CL7_EXACT_CASH_AUTHORITY"


class CL7RuntimeReason(StrEnum):
    TYPE_INVALID = "TYPE_INVALID"
    VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
    ENVIRONMENT_UNSUPPORTED = "ENVIRONMENT_UNSUPPORTED"
    ACCOUNT_SCOPE_INVALID = "ACCOUNT_SCOPE_INVALID"
    IDENTITY_KEY_INVALID = "IDENTITY_KEY_INVALID"
    TIMESTAMP_INVALID = "TIMESTAMP_INVALID"
    CONFIRMATION_INVALID = "CONFIRMATION_INVALID"
    STATE_INVALID = "STATE_INVALID"
    STATE_TRANSITION_INVALID = "STATE_TRANSITION_INVALID"
    AUTHORITY_RECORD_MISSING = "AUTHORITY_RECORD_MISSING"
    AUTHORITY_RECORD_CORRUPT = "AUTHORITY_RECORD_CORRUPT"
    AUTHORITY_CHECKSUM_INVALID = "AUTHORITY_CHECKSUM_INVALID"
    CAS_CONFLICT = "CAS_CONFLICT"
    CUTOVER_NOT_QUIESCENT = "CUTOVER_NOT_QUIESCENT"
    LEGACY_PENDING_OPERATION = "LEGACY_PENDING_OPERATION"
    LEDGER_UNAVAILABLE = "LEDGER_UNAVAILABLE"
    LEDGER_SYNC_INCOMPLETE = "LEDGER_SYNC_INCOMPLETE"
    BROKER_READ_FAILED = "BROKER_READ_FAILED"
    OPENING_INVALID = "OPENING_INVALID"
    RECONCILIATION_BLOCKED = "RECONCILIATION_BLOCKED"
    AVAILABILITY_BLOCKED = "AVAILABILITY_BLOCKED"
    CONTEXT_BLOCKED = "CONTEXT_BLOCKED"
    CONTEXT_STALE = "CONTEXT_STALE"
    LOCK_UNAVAILABLE = "LOCK_UNAVAILABLE"
    LOCK_ORDER_VIOLATION = "LOCK_ORDER_VIOLATION"
    PORTFOLIO_CHANGED = "PORTFOLIO_CHANGED"
    RISK_CHANGED = "RISK_CHANGED"
    LEDGER_CHANGED = "LEDGER_CHANGED"
    CENTRAL_CHANGED = "CENTRAL_CHANGED"
    DISPATCH_NOT_ARMED = "DISPATCH_NOT_ARMED"
    DISPATCH_PENDING = "DISPATCH_PENDING"
    DISPATCH_PROOF_INVALID = "DISPATCH_PROOF_INVALID"
    ATTEMPT_RECORD_FAILED = "ATTEMPT_RECORD_FAILED"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    PROVIDER_OUTCOME_UNCERTAIN = "PROVIDER_OUTCOME_UNCERTAIN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    ROLLBACK_FORBIDDEN_AFTER_ATTEMPT = "ROLLBACK_FORBIDDEN_AFTER_ATTEMPT"
    PRIVACY_BOUNDARY_FAILED = "PRIVACY_BOUNDARY_FAILED"
    INTERNAL_BOUNDARY_FAILED = "INTERNAL_BOUNDARY_FAILED"
    OWN_FUNDS_BLOCKED = "OWN_FUNDS_BLOCKED"


class CL7RuntimeError(RuntimeError):
    """Finite, privacy-safe CL7 failure."""

    def __init__(
        self,
        reason: CL7RuntimeReason,
        dependency_reason: str | None = None,
        *,
        stage: str | None = None,
        retryable: bool = False,
        availability_status: str | None = None,
        availability_reason: str | None = None,
        broker_view_observability: object = None,
    ) -> None:
        if type(reason) is not CL7RuntimeReason:
            raise TypeError("reason must be CL7RuntimeReason")
        self.reason = reason
        self.dependency_reason = _safe_token(dependency_reason)
        self.stage = _safe_token(stage)
        self.retryable = bool(retryable)
        self.availability_status = _safe_exact_token(availability_status)
        self.availability_reason = _safe_exact_token(availability_reason)
        self.broker_view_observability = _safe_broker_view_observability(
            broker_view_observability
        )
        super().__init__(reason.value)

    def __str__(self) -> str:
        parts = [self.reason.value]
        if self.dependency_reason:
            parts.append(self.dependency_reason)
        if self.stage:
            parts.append(self.stage)
        return ":".join(parts)


def _fail(
    reason: CL7RuntimeReason,
    dependency_reason: str | None = None,
    *,
    stage: str | None = None,
    retryable: bool = False,
    availability_status: str | None = None,
    availability_reason: str | None = None,
    broker_view_observability: object = None,
) -> None:
    raise CL7RuntimeError(
        reason,
        dependency_reason,
        stage=stage,
        retryable=retryable,
        availability_status=availability_status,
        availability_reason=availability_reason,
        broker_view_observability=broker_view_observability,
    ) from None


def _safe_token(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    return text if re.fullmatch(r"[A-Z0-9_]{1,96}", text) else None


def _safe_exact_token(value: object) -> str | None:
    if type(value) is not str:
        return None
    return value if re.fullmatch(r"[A-Z0-9_]{1,96}", value) else None


def _safe_broker_view_observability(value: object) -> dict[str, object] | None:
    if (
        type(value) is not dict
        or any(type(field) is not str for field in value)
        or frozenset(value) != _BROKER_VIEW_MISMATCH_FIELDS
    ):
        return None
    if any(
        type(value[field]) is not str or _HASH_RE.fullmatch(value[field]) is None
        for field in _BROKER_VIEW_MISMATCH_HASH_FIELDS
    ):
        return None
    if any(
        type(value[field]) is not bool for field in _BROKER_VIEW_MISMATCH_BOOL_FIELDS
    ):
        return None
    return {
        field: value[field]
        for field in _BROKER_VIEW_MISMATCH_HASH_FIELDS
        + _BROKER_VIEW_MISMATCH_BOOL_FIELDS
    }


def _fail_context_not_ready(
    context: object,
    *,
    broker_view_observability: object = None,
) -> None:
    _fail(
        CL7RuntimeReason.CONTEXT_BLOCKED,
        getattr(getattr(context, "reason", None), "value", None),
        stage="CL6_CONTEXT",
        availability_status=getattr(context, "availability_status", None),
        availability_reason=getattr(context, "availability_reason", None),
        broker_view_observability=broker_view_observability,
    )


def _plain_int(value: object, reason: CL7RuntimeReason) -> int:
    if type(value) is not int or not 0 <= value <= _INT64_MAX:
        _fail(reason)
    return value


def _parse_uint(value: object, reason: CL7RuntimeReason) -> int:
    if type(value) is not str or _UINT_RE.fullmatch(value) is None:
        _fail(reason)
    number = int(value)
    if number > _INT64_MAX:
        _fail(reason)
    return number


def _hash(value: object, reason: CL7RuntimeReason) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        _fail(reason)
    return value


def _optional_hash(value: object, reason: CL7RuntimeReason) -> str | None:
    return None if value is None else _hash(value, reason)


def _timestamp(value: object) -> str:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(CL7RuntimeReason.TIMESTAMP_INVALID)
    try:
        # Calendar validation; the regex already freezes nanosecond formatting.
        from datetime import datetime

        datetime.fromisoformat(value[:26] + "+00:00")
    except ValueError:
        _fail(CL7RuntimeReason.TIMESTAMP_INVALID)
    return value


def _timestamp_to_iso(value: object) -> str:
    selected = _timestamp(value)
    return selected[:26] + "+00:00"


def _successor_timestamp(value: object) -> str:
    selected = _timestamp(value)
    from datetime import datetime, timezone

    seconds = int(
        datetime.fromisoformat(selected[:19] + "+00:00")
        .astimezone(timezone.utc)
        .timestamp()
    )
    nanoseconds = seconds * 1_000_000_000 + int(selected[20:29])
    if nanoseconds >= 253402300799999999999:
        _fail(CL7RuntimeReason.TIMESTAMP_INVALID)
    next_value = nanoseconds + 1
    next_seconds, fraction = divmod(next_value, 1_000_000_000)
    prefix = datetime.fromtimestamp(next_seconds, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    return f"{prefix}.{fraction:09d}Z"


def _key(identity_key: object, identity_key_id: object) -> tuple[bytes, str]:
    if (
        type(identity_key) is not bytes
        or not 32 <= len(identity_key) <= 64
        or type(identity_key_id) is not str
        or _KEY_ID_RE.fullmatch(identity_key_id) is None
    ):
        _fail(CL7RuntimeReason.IDENTITY_KEY_INVALID)
    return identity_key, identity_key_id


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if any(0xD800 <= ord(char) <= 0xDFFF for char in encoded):
            raise ValueError("surrogate")
        return encoded.encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail(CL7RuntimeReason.TYPE_INVALID)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hmac_sha256(key: bytes, value: object) -> str:
    return hmac.new(key, _canonical_bytes(value), hashlib.sha256).hexdigest()


def _broker_view_operand_hmac(
    identity_key: bytes,
    *,
    role: str,
    money: Money,
) -> str:
    if type(identity_key) is not bytes or not 32 <= len(identity_key) <= 64:
        _fail(CL7RuntimeReason.IDENTITY_KEY_INVALID)
    if type(role) is not str or role not in {
        "BROKER_TOTAL_CASH",
        "POSITIONS_MONEY_RUB",
        "BLOCKED_RUB",
        "POSITIONS_PLUS_BLOCKED_RUB",
    }:
        _fail(CL7RuntimeReason.TYPE_INVALID)
    if not _is_exact_broker_view_money(money):
        _fail(CL7RuntimeReason.TYPE_INVALID)
    return _hmac_sha256(
        identity_key,
        {
            "domain": _BROKER_VIEW_MISMATCH_OPERAND_DOMAIN,
            "money": money.to_canonical_dict(),
            "role": role,
            "version": 1,
        },
    )


def _is_exact_broker_view_money(value: object) -> bool:
    if (
        type(value) is not Money
        or type(value.currency) is not str
        or value.currency != "RUB"
        or type(value.minor_units) is not int
        or type(value.scale) is not int
        or value.scale != 9
    ):
        return False
    try:
        rebuilt = Money(
            currency=value.currency,
            minor_units=value.minor_units,
            scale=value.scale,
        )
    except Exception:
        return False
    return hmac.compare_digest(value.canonical_bytes, rebuilt.canonical_bytes)


def _build_broker_view_observability(
    *,
    broker_total_cash: Money,
    positions_money_rub: Money,
    blocked_rub: Money,
    identity_key: bytes,
) -> dict[str, object]:
    if any(
        not _is_exact_broker_view_money(value)
        for value in (broker_total_cash, positions_money_rub, blocked_rub)
    ):
        _fail(CL7RuntimeReason.TYPE_INVALID)
    positions_plus_blocked = positions_money_rub + blocked_rub
    zero = Money(currency="RUB", minor_units=0)
    broker_bytes = broker_total_cash.canonical_bytes
    positions_bytes = positions_money_rub.canonical_bytes
    blocked_bytes = blocked_rub.canonical_bytes
    combined_bytes = positions_plus_blocked.canonical_bytes
    zero_bytes = zero.canonical_bytes
    value: dict[str, object] = {
        "broker_total_cash_hmac_sha256": _broker_view_operand_hmac(
            identity_key,
            role="BROKER_TOTAL_CASH",
            money=broker_total_cash,
        ),
        "positions_money_rub_hmac_sha256": _broker_view_operand_hmac(
            identity_key,
            role="POSITIONS_MONEY_RUB",
            money=positions_money_rub,
        ),
        "blocked_rub_hmac_sha256": _broker_view_operand_hmac(
            identity_key,
            role="BLOCKED_RUB",
            money=blocked_rub,
        ),
        "positions_plus_blocked_rub_hmac_sha256": _broker_view_operand_hmac(
            identity_key,
            role="POSITIONS_PLUS_BLOCKED_RUB",
            money=positions_plus_blocked,
        ),
        "broker_total_eq_positions_money": hmac.compare_digest(
            broker_bytes, positions_bytes
        ),
        "broker_total_eq_blocked": hmac.compare_digest(broker_bytes, blocked_bytes),
        "positions_money_eq_blocked": hmac.compare_digest(
            positions_bytes, blocked_bytes
        ),
        "broker_total_eq_positions_plus_blocked": hmac.compare_digest(
            broker_bytes, combined_bytes
        ),
        "broker_total_is_zero": hmac.compare_digest(broker_bytes, zero_bytes),
        "positions_money_is_zero": hmac.compare_digest(positions_bytes, zero_bytes),
        "blocked_is_zero": hmac.compare_digest(blocked_bytes, zero_bytes),
    }
    checked = _safe_broker_view_observability(value)
    if checked is None:
        _fail(CL7RuntimeReason.INTERNAL_BOUNDARY_FAILED)
    return checked


def derive_account_scope(
    raw_account_id: str,
    *,
    identity_key: bytes,
    identity_key_id: str,
) -> str:
    """Reproduce the accepted CL3 account-scope HMAC."""

    key, key_id = _key(identity_key, identity_key_id)
    if type(raw_account_id) is not str or not 1 <= len(raw_account_id) <= 256:
        _fail(CL7RuntimeReason.ACCOUNT_SCOPE_INVALID)
    if any(0xD800 <= ord(char) <= 0xDFFF for char in raw_account_id):
        _fail(CL7RuntimeReason.ACCOUNT_SCOPE_INVALID)
    return _hmac_sha256(
        key,
        {
            "account_id": raw_account_id,
            "domain": "v3.10-cl3-account-scope",
            "environment": "SANDBOX",
            "identity_key_id": key_id,
            "provider": "TBANK",
            "version": 1,
        },
    )


_RECORD_FIELDS = frozenset(
    {
        "account_scope_sha256",
        "activation_context_sha256",
        "cutover_generation",
        "domain",
        "environment",
        "ever_exact_activated",
        "identity_key_id",
        "ledger_head_sha256",
        "ledger_revision",
        "opening_cutoff",
        "opening_record_sha256",
        "operations_complete_through",
        "pending_dispatch_proof_sha256",
        "post_attempt_count",
        "previous_record_sha256",
        "record_revision",
        "state",
        "transition_at",
        "transition_kind",
        "version",
    }
)

_TRANSITION_KINDS = frozenset(
    {
        "BOOTSTRAP_LEGACY",
        "PREPARE_CUTOVER",
        "PREPARATION_EVIDENCE_BOUND",
        "CONFIRM_CUTOVER",
        "CANCEL_CUTOVER",
        "ACTIVATE_EXACT",
        "ARM_EXACT",
        "DISARM_EXACT",
        "SYNC_ADVANCED",
        "DISPATCH_ATTEMPT_RECORDED",
        "DISPATCH_REJECTED_REARMED",
        "DISPATCH_ACCOUNTED_REARMED",
        "RECOVERY_CLOSED_DISARMED",
        "EXACT_SETTLEMENT_CLOSED_DISARMED",
        "EXACT_ZERO_TERMINAL_CLOSED_DISARMED",
        "FEE_ALIAS_HELD",
        "FEE_ALIAS_CLOSED_DISARMED",
        "FEE_ALIAS_REVIEW_HELD",
        "FEE_REPLACEMENT_HELD",
        "FEE_REPLACEMENT_CLOSED_DISARMED",
        "FEE_REPLACEMENT_REVIEW_HELD",
        "LATE_FEE_ADJUSTMENT_HELD",
        "LATE_FEE_ADJUSTMENT_CLOSED_DISARMED",
        "LATE_FEE_REVIEW_HELD",
        "VERSIONED_SOURCE_CUTOVER_HELD",
        "VERSIONED_SOURCE_SELECTED_DISARMED",
        "VERSIONED_SELECTED_SYNC_HELD",
        "VERSIONED_SELECTED_SYNC_COMMITTED",
        "VERSIONED_SELECTED_SYNC_ABORTED",
        "VERSIONED_OWNER_REFRESH_HELD",
        "VERSIONED_OWNER_REFRESH_COMMITTED",
        "VERSIONED_CASH_FLOW_RESYNC_HELD",
        "VERSIONED_CASH_FLOW_RESYNC_COMMITTED",
        "VERSIONED_ORDER_ADMISSION_HELD",
        "VERSIONED_ORDER_ADMISSION_COMMITTED",
        "VERSIONED_ORDER_ARMED",
        "VERSIONED_ORDER_DISARMED",
        "VERSIONED_ORDER_ATTEMPT_RECORDED",
        "VERSIONED_FULL_FILL_CLOSED_DISARMED",
        "ROLLBACK_TO_LEGACY",
    }
)


@dataclass(frozen=True, slots=True)
class RuntimeCashAuthorityRecord:
    account_scope_sha256: str | None
    activation_context_sha256: str | None
    cutover_generation: int
    environment: str
    ever_exact_activated: bool
    identity_key_id: str | None
    ledger_head_sha256: str | None
    ledger_revision: int | None
    opening_cutoff: str | None
    opening_record_sha256: str | None
    operations_complete_through: str | None
    pending_dispatch_proof_sha256: str | None
    post_attempt_count: int
    previous_record_sha256: str | None
    record_revision: int
    state: RuntimeCashAuthorityState
    transition_at: str
    transition_kind: str
    version: int = _VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != _VERSION:
            _fail(CL7RuntimeReason.VERSION_UNSUPPORTED)
        if type(self.environment) is not str or self.environment != "SANDBOX":
            _fail(CL7RuntimeReason.ENVIRONMENT_UNSUPPORTED)
        if type(self.state) is not RuntimeCashAuthorityState:
            _fail(CL7RuntimeReason.STATE_INVALID)
        if type(self.ever_exact_activated) is not bool:
            _fail(CL7RuntimeReason.TYPE_INVALID)
        _plain_int(self.cutover_generation, CL7RuntimeReason.STATE_INVALID)
        _plain_int(self.record_revision, CL7RuntimeReason.STATE_INVALID)
        _plain_int(self.post_attempt_count, CL7RuntimeReason.STATE_INVALID)
        if self.ledger_revision is not None:
            _plain_int(self.ledger_revision, CL7RuntimeReason.STATE_INVALID)
        for value in (
            self.account_scope_sha256,
            self.activation_context_sha256,
            self.ledger_head_sha256,
            self.opening_record_sha256,
            self.pending_dispatch_proof_sha256,
            self.previous_record_sha256,
        ):
            _optional_hash(value, CL7RuntimeReason.STATE_INVALID)
        if self.identity_key_id is not None and (
            type(self.identity_key_id) is not str
            or _KEY_ID_RE.fullmatch(self.identity_key_id) is None
        ):
            _fail(CL7RuntimeReason.STATE_INVALID)
        if self.opening_cutoff is not None:
            _timestamp(self.opening_cutoff)
        if self.operations_complete_through is not None:
            _timestamp(self.operations_complete_through)
        _timestamp(self.transition_at)
        if (
            type(self.transition_kind) is not str
            or self.transition_kind not in _TRANSITION_KINDS
        ):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        if self.record_revision == 0:
            if (
                self.state is not RuntimeCashAuthorityState.LEGACY_ACTIVE
                or self.transition_kind != "BOOTSTRAP_LEGACY"
                or self.previous_record_sha256 is not None
                or self.cutover_generation != 0
                or self.ever_exact_activated
                or self.post_attempt_count != 0
                or any(
                    value is not None
                    for value in (
                        self.account_scope_sha256,
                        self.activation_context_sha256,
                        self.identity_key_id,
                        self.ledger_head_sha256,
                        self.ledger_revision,
                        self.opening_cutoff,
                        self.opening_record_sha256,
                        self.operations_complete_through,
                        self.pending_dispatch_proof_sha256,
                    )
                )
            ):
                _fail(CL7RuntimeReason.STATE_INVALID)
        elif self.previous_record_sha256 is None:
            _fail(CL7RuntimeReason.STATE_INVALID)
        exact = self.state.value.startswith("EXACT_CASH_")
        evidence = (
            self.account_scope_sha256,
            self.identity_key_id,
            self.ledger_head_sha256,
            self.ledger_revision,
            self.opening_cutoff,
            self.opening_record_sha256,
            self.operations_complete_through,
        )
        if self.state is RuntimeCashAuthorityState.CUTOVER_PREPARED:
            bound = self.transition_kind in {
                "PREPARATION_EVIDENCE_BOUND",
                "SYNC_ADVANCED",
            }
            if self.account_scope_sha256 is None or self.identity_key_id is None:
                _fail(CL7RuntimeReason.STATE_INVALID)
            if bound and any(item is None for item in evidence):
                _fail(CL7RuntimeReason.STATE_INVALID)
            if not bound and any(item is not None for item in evidence[2:]):
                _fail(CL7RuntimeReason.STATE_INVALID)
            if self.activation_context_sha256 is not None:
                _fail(CL7RuntimeReason.STATE_INVALID)
        elif self.state is RuntimeCashAuthorityState.CUTOVER_CONFIRMED:
            if (
                any(item is None for item in evidence)
                or self.activation_context_sha256 is not None
            ):
                _fail(CL7RuntimeReason.STATE_INVALID)
        elif exact:
            if (
                any(item is None for item in evidence)
                or self.activation_context_sha256 is None
            ):
                _fail(CL7RuntimeReason.STATE_INVALID)
            if not self.ever_exact_activated:
                _fail(CL7RuntimeReason.STATE_INVALID)
        if (self.pending_dispatch_proof_sha256 is not None) != (
            self.state in {RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING,
                           RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
                           RuntimeCashAuthorityState.EXACT_CASH_SOURCE_CUTOVER_PENDING,
                           RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_SYNC_PENDING,
                           RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING,
                           RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_RISK_RESYNC_PENDING,
                           RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_ADMISSION_PENDING,
                           RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISPATCH_PENDING}
        ):
            _fail(CL7RuntimeReason.STATE_INVALID)

    @classmethod
    def bootstrap(cls, transition_at: str) -> RuntimeCashAuthorityRecord:
        return cls(
            account_scope_sha256=None,
            activation_context_sha256=None,
            cutover_generation=0,
            environment="SANDBOX",
            ever_exact_activated=False,
            identity_key_id=None,
            ledger_head_sha256=None,
            ledger_revision=None,
            opening_cutoff=None,
            opening_record_sha256=None,
            operations_complete_through=None,
            pending_dispatch_proof_sha256=None,
            post_attempt_count=0,
            previous_record_sha256=None,
            record_revision=0,
            state=RuntimeCashAuthorityState.LEGACY_ACTIVE,
            transition_at=_timestamp(transition_at),
            transition_kind="BOOTSTRAP_LEGACY",
        )

    @property
    def owner(self) -> RuntimeCashAuthorityOwner:
        if self.state.value.startswith("EXACT_CASH_"):
            return RuntimeCashAuthorityOwner.CL7_EXACT_CASH_AUTHORITY
        return RuntimeCashAuthorityOwner.LEGACY_CASH_AUTHORITY

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "activation_context_sha256": self.activation_context_sha256,
            "cutover_generation": str(self.cutover_generation),
            "domain": _DOMAIN,
            "environment": self.environment,
            "ever_exact_activated": self.ever_exact_activated,
            "identity_key_id": self.identity_key_id,
            "ledger_head_sha256": self.ledger_head_sha256,
            "ledger_revision": None
            if self.ledger_revision is None
            else str(self.ledger_revision),
            "opening_cutoff": self.opening_cutoff,
            "opening_record_sha256": self.opening_record_sha256,
            "operations_complete_through": self.operations_complete_through,
            "pending_dispatch_proof_sha256": self.pending_dispatch_proof_sha256,
            "post_attempt_count": str(self.post_attempt_count),
            "previous_record_sha256": self.previous_record_sha256,
            "record_revision": str(self.record_revision),
            "state": self.state.value,
            "transition_at": self.transition_at,
            "transition_kind": self.transition_kind,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)

    @classmethod
    def from_canonical_dict(cls, value: object) -> RuntimeCashAuthorityRecord:
        if not isinstance(value, Mapping) or frozenset(value) != _RECORD_FIELDS:
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
        if value["domain"] != _DOMAIN:
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
        try:
            state = RuntimeCashAuthorityState(value["state"])
        except (TypeError, ValueError):
            _fail(CL7RuntimeReason.STATE_INVALID)
        return cls(
            account_scope_sha256=_optional_hash(
                value["account_scope_sha256"], CL7RuntimeReason.STATE_INVALID
            ),
            activation_context_sha256=_optional_hash(
                value["activation_context_sha256"], CL7RuntimeReason.STATE_INVALID
            ),
            cutover_generation=_parse_uint(
                value["cutover_generation"], CL7RuntimeReason.STATE_INVALID
            ),
            environment=value["environment"],
            ever_exact_activated=value["ever_exact_activated"],
            identity_key_id=value["identity_key_id"],
            ledger_head_sha256=_optional_hash(
                value["ledger_head_sha256"], CL7RuntimeReason.STATE_INVALID
            ),
            ledger_revision=(
                None
                if value["ledger_revision"] is None
                else _parse_uint(
                    value["ledger_revision"], CL7RuntimeReason.STATE_INVALID
                )
            ),
            opening_cutoff=value["opening_cutoff"],
            opening_record_sha256=_optional_hash(
                value["opening_record_sha256"], CL7RuntimeReason.STATE_INVALID
            ),
            operations_complete_through=value["operations_complete_through"],
            pending_dispatch_proof_sha256=_optional_hash(
                value["pending_dispatch_proof_sha256"], CL7RuntimeReason.STATE_INVALID
            ),
            post_attempt_count=_parse_uint(
                value["post_attempt_count"], CL7RuntimeReason.STATE_INVALID
            ),
            previous_record_sha256=_optional_hash(
                value["previous_record_sha256"], CL7RuntimeReason.STATE_INVALID
            ),
            record_revision=_parse_uint(
                value["record_revision"], CL7RuntimeReason.STATE_INVALID
            ),
            state=state,
            transition_at=value["transition_at"],
            transition_kind=value["transition_kind"],
            version=value["version"],
        )

    @classmethod
    def from_canonical_bytes(cls, value: bytes) -> RuntimeCashAuthorityRecord:
        if type(value) is not bytes or not value or len(value) > _MAX_RECORD_BYTES:
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)

        def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in items:
                if key in result:
                    raise ValueError("duplicate")
                result[key] = item
            return result

        try:
            parsed = json.loads(
                value.decode("ascii"),
                object_pairs_hook=pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ValueError("constant")
                ),
            )
        except (UnicodeError, ValueError, json.JSONDecodeError):
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
        record = cls.from_canonical_dict(parsed)
        if not hmac.compare_digest(record.canonical_bytes, value):
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
        return record


_PROOF_FIELDS = frozenset(
    {
        "account_scope_sha256",
        "authority_record_revision",
        "authority_record_sha256",
        "availability_sha256",
        "central_order_revision",
        "central_reservation_projection_hash",
        "cl6_context_identity_sha256",
        "cl6_context_sha256",
        "current_lots",
        "direction",
        "domain",
        "evaluated_at",
        "free_investable_cash",
        "identity_key_id",
        "intent_scope_sha256",
        "ledger_head_sha256",
        "ledger_revision",
        "portfolio_decision_checksum",
        "portfolio_document_checksum",
        "portfolio_revision",
        "proof_identity_sha256",
        "reconciliation_sha256",
        "reserved_cash",
        "risk_policy_hash",
        "risk_state_guard_hash",
        "target_lots",
        "version",
    }
)


@dataclass(frozen=True, slots=True)
class LockedDispatchProof:
    account_scope_sha256: str
    authority_record_revision: int
    authority_record_sha256: str
    availability_sha256: str
    central_order_revision: int
    central_reservation_projection_hash: str
    cl6_context_identity_sha256: str
    cl6_context_sha256: str
    current_lots: int
    direction: str
    evaluated_at: str
    free_investable_cash: Money
    identity_key_id: str
    intent_scope_sha256: str
    ledger_head_sha256: str
    ledger_revision: int
    portfolio_decision_checksum: str
    portfolio_document_checksum: str
    portfolio_revision: int
    reconciliation_sha256: str
    reserved_cash: Money
    risk_policy_hash: str
    risk_state_guard_hash: str
    target_lots: int
    proof_identity_sha256: str
    version: int = _VERSION
    own_funds_evidence: OwnFundsEvidence | None = None

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version not in {1, 2}:
            _fail(CL7RuntimeReason.VERSION_UNSUPPORTED)
        if (self.version == 1 and self.own_funds_evidence is not None) or (
            self.version == 2 and type(self.own_funds_evidence) is not OwnFundsEvidence
        ):
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        for value in (
            self.account_scope_sha256,
            self.authority_record_sha256,
            self.availability_sha256,
            self.central_reservation_projection_hash,
            self.cl6_context_identity_sha256,
            self.cl6_context_sha256,
            self.intent_scope_sha256,
            self.ledger_head_sha256,
            self.portfolio_decision_checksum,
            self.portfolio_document_checksum,
            self.reconciliation_sha256,
            self.risk_policy_hash,
            self.risk_state_guard_hash,
            self.proof_identity_sha256,
        ):
            _hash(value, CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        for value in (
            self.authority_record_revision,
            self.central_order_revision,
            self.current_lots,
            self.ledger_revision,
            self.portfolio_revision,
            self.target_lots,
        ):
            _plain_int(value, CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        if type(self.direction) is not str or self.direction not in {"BUY", "SELL"}:
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        _timestamp(self.evaluated_at)
        if (
            type(self.identity_key_id) is not str
            or _KEY_ID_RE.fullmatch(self.identity_key_id) is None
        ):
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        if (
            type(self.free_investable_cash) is not Money
            or type(self.reserved_cash) is not Money
        ):
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        if (
            self.free_investable_cash.currency != "RUB"
            or self.reserved_cash.currency != "RUB"
        ):
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)

        if self.version == 2:
            own = self.own_funds_evidence
            if (own.direction != self.direction
                or own.requested_lots != abs(self.target_lots - self.current_lots)
                or own.own_reservation_nano != self.reserved_cash.minor_units
                or (self.direction == "BUY" and
                    self.free_investable_cash.minor_units > own.free_after_reservations_nano)):
                _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)

    def _base_dict(self, *, domain: str) -> dict[str, object]:
        result = {
            "account_scope_sha256": self.account_scope_sha256,
            "authority_record_revision": str(self.authority_record_revision),
            "authority_record_sha256": self.authority_record_sha256,
            "availability_sha256": self.availability_sha256,
            "central_order_revision": str(self.central_order_revision),
            "central_reservation_projection_hash": self.central_reservation_projection_hash,
            "cl6_context_identity_sha256": self.cl6_context_identity_sha256,
            "cl6_context_sha256": self.cl6_context_sha256,
            "current_lots": self.current_lots,
            "direction": self.direction,
            "domain": domain,
            "evaluated_at": self.evaluated_at,
            "free_investable_cash": self.free_investable_cash.to_canonical_dict(),
            "identity_key_id": self.identity_key_id,
            "intent_scope_sha256": self.intent_scope_sha256,
            "ledger_head_sha256": self.ledger_head_sha256,
            "ledger_revision": str(self.ledger_revision),
            "portfolio_decision_checksum": self.portfolio_decision_checksum,
            "portfolio_document_checksum": self.portfolio_document_checksum,
            "portfolio_revision": str(self.portfolio_revision),
            "reconciliation_sha256": self.reconciliation_sha256,
            "reserved_cash": self.reserved_cash.to_canonical_dict(),
            "risk_policy_hash": self.risk_policy_hash,
            "risk_state_guard_hash": self.risk_state_guard_hash,
            "target_lots": self.target_lots,
            "version": self.version,
        }
        if self.version == 2:
            result["domain"] = domain + "-own-funds-v2"
            result["own_funds_evidence"] = self.own_funds_evidence.to_canonical_dict()
        return result

    def to_canonical_dict(self) -> dict[str, object]:
        result = self._base_dict(domain=_PROOF_DOMAIN)
        result["proof_identity_sha256"] = self.proof_identity_sha256
        return result

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)

    @classmethod
    def build(
        cls,
        *,
        raw_intent_id: str,
        identity_key: bytes,
        **values: object,
    ) -> LockedDispatchProof:
        values = dict(values)
        key_id = values.pop("identity_key_id", None)
        key, checked_key_id = _key(identity_key, key_id)
        if (
            type(raw_intent_id) is not str
            or not raw_intent_id
            or len(raw_intent_id) > 256
        ):
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        account = _hash(
            values.get("account_scope_sha256"), CL7RuntimeReason.DISPATCH_PROOF_INVALID
        )
        intent_scope = _hmac_sha256(
            key,
            {
                "account_scope_sha256": account,
                "domain": _INTENT_SCOPE_DOMAIN,
                "intent_id": raw_intent_id,
                "version": 1,
            },
        )
        placeholder = cls(
            **values,
            identity_key_id=checked_key_id,
            intent_scope_sha256=intent_scope,
            proof_identity_sha256="0" * 64,
        )
        identity = _hmac_sha256(
            key, placeholder._base_dict(domain=_PROOF_IDENTITY_DOMAIN)
        )
        return replace(placeholder, proof_identity_sha256=identity)

    @classmethod
    def from_canonical_dict(cls, value: object) -> LockedDispatchProof:
        if not isinstance(value, Mapping) or type(value.get("version")) is not int:
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        v2 = value.get("version") == 2
        expected_fields = _PROOF_FIELDS | {"own_funds_evidence"} if v2 else _PROOF_FIELDS
        expected_domain = _PROOF_DOMAIN + "-own-funds-v2" if v2 else _PROOF_DOMAIN
        if frozenset(value) != expected_fields or value.get("domain") != expected_domain:
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        try:
            own = OwnFundsEvidence.from_canonical_dict(value["own_funds_evidence"]) if v2 else None
            free = Money.from_canonical_dict(value["free_investable_cash"])
            reserved = Money.from_canonical_dict(value["reserved_cash"])
        except Exception:
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        return cls(
            account_scope_sha256=value["account_scope_sha256"],
            authority_record_revision=_parse_uint(
                value["authority_record_revision"],
                CL7RuntimeReason.DISPATCH_PROOF_INVALID,
            ),
            authority_record_sha256=value["authority_record_sha256"],
            availability_sha256=value["availability_sha256"],
            central_order_revision=_parse_uint(
                value["central_order_revision"], CL7RuntimeReason.DISPATCH_PROOF_INVALID
            ),
            central_reservation_projection_hash=value[
                "central_reservation_projection_hash"
            ],
            cl6_context_identity_sha256=value["cl6_context_identity_sha256"],
            cl6_context_sha256=value["cl6_context_sha256"],
            current_lots=_plain_int(
                value["current_lots"], CL7RuntimeReason.DISPATCH_PROOF_INVALID
            ),
            direction=value["direction"],
            evaluated_at=value["evaluated_at"],
            free_investable_cash=free,
            identity_key_id=value["identity_key_id"],
            intent_scope_sha256=value["intent_scope_sha256"],
            ledger_head_sha256=value["ledger_head_sha256"],
            ledger_revision=_parse_uint(
                value["ledger_revision"], CL7RuntimeReason.DISPATCH_PROOF_INVALID
            ),
            portfolio_decision_checksum=value["portfolio_decision_checksum"],
            portfolio_document_checksum=value["portfolio_document_checksum"],
            portfolio_revision=_parse_uint(
                value["portfolio_revision"], CL7RuntimeReason.DISPATCH_PROOF_INVALID
            ),
            reconciliation_sha256=value["reconciliation_sha256"],
            reserved_cash=reserved,
            risk_policy_hash=value["risk_policy_hash"],
            risk_state_guard_hash=value["risk_state_guard_hash"],
            target_lots=_plain_int(
                value["target_lots"], CL7RuntimeReason.DISPATCH_PROOF_INVALID
            ),
            proof_identity_sha256=value["proof_identity_sha256"],
            version=value["version"],
            own_funds_evidence=own,
        )

    def verify_identity(self, *, raw_intent_id: str, identity_key: bytes) -> None:
        rebuilt = type(self).build(
            raw_intent_id=raw_intent_id,
            identity_key=identity_key,
            **{
                item.name: getattr(self, item.name)
                for item in fields(self)
                if item.name not in {"intent_scope_sha256", "proof_identity_sha256"}
            },
        )
        if not hmac.compare_digest(rebuilt.canonical_bytes, self.canonical_bytes):
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)


def _transition_pair(
    previous: RuntimeCashAuthorityRecord, current: RuntimeCashAuthorityRecord
) -> None:
    allowed: dict[
        str, tuple[set[RuntimeCashAuthorityState], RuntimeCashAuthorityState]
    ] = {
        "PREPARE_CUTOVER": (
            {RuntimeCashAuthorityState.LEGACY_ACTIVE},
            RuntimeCashAuthorityState.CUTOVER_PREPARED,
        ),
        "PREPARATION_EVIDENCE_BOUND": (
            {RuntimeCashAuthorityState.CUTOVER_PREPARED},
            RuntimeCashAuthorityState.CUTOVER_PREPARED,
        ),
        "CONFIRM_CUTOVER": (
            {RuntimeCashAuthorityState.CUTOVER_PREPARED},
            RuntimeCashAuthorityState.CUTOVER_CONFIRMED,
        ),
        "CANCEL_CUTOVER": (
            {
                RuntimeCashAuthorityState.CUTOVER_PREPARED,
                RuntimeCashAuthorityState.CUTOVER_CONFIRMED,
            },
            RuntimeCashAuthorityState.LEGACY_ACTIVE,
        ),
        "ACTIVATE_EXACT": (
            {RuntimeCashAuthorityState.CUTOVER_CONFIRMED},
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        ),
        "ARM_EXACT": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        ),
        "DISARM_EXACT": (
            {RuntimeCashAuthorityState.EXACT_CASH_ARMED},
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        ),
        "SYNC_ADVANCED": (
            {
                RuntimeCashAuthorityState.CUTOVER_PREPARED,
                RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
                RuntimeCashAuthorityState.EXACT_CASH_ARMED,
            },
            current.state,
        ),
        "DISPATCH_ATTEMPT_RECORDED": (
            {RuntimeCashAuthorityState.EXACT_CASH_ARMED},
            RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING,
        ),
        "DISPATCH_REJECTED_REARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        ),
        "DISPATCH_ACCOUNTED_REARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        ),
        "EXACT_SETTLEMENT_CLOSED_DISARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        ),
        "EXACT_ZERO_TERMINAL_CLOSED_DISARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        ),
        "FEE_ALIAS_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
        ),
        "FEE_ALIAS_REVIEW_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
        ),
        "FEE_ALIAS_CLOSED_DISARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        ),
        "FEE_REPLACEMENT_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
        ),
        "FEE_REPLACEMENT_REVIEW_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
        ),
        "FEE_REPLACEMENT_CLOSED_DISARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        ),
        "LATE_FEE_ADJUSTMENT_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
        ),
        "LATE_FEE_REVIEW_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING,
        ),
        "LATE_FEE_ADJUSTMENT_CLOSED_DISARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        ),
        "RECOVERY_CLOSED_DISARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        ),
        "VERSIONED_SOURCE_CUTOVER_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_SOURCE_CUTOVER_PENDING,
        ),
        "VERSIONED_SOURCE_SELECTED_DISARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_SOURCE_CUTOVER_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED,
        ),
        "VERSIONED_SELECTED_SYNC_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_SYNC_PENDING,
        ),
        "VERSIONED_SELECTED_SYNC_COMMITTED": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_SYNC_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED,
        ),
        "VERSIONED_SELECTED_SYNC_ABORTED": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_SYNC_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED,
        ),
        "VERSIONED_OWNER_REFRESH_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING,
        ),
        "VERSIONED_OWNER_REFRESH_COMMITTED": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_OWNER_REFRESH_PENDING,
                           RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_RISK_RESYNC_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED,
        ),
        "VERSIONED_CASH_FLOW_RESYNC_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_RISK_RESYNC_PENDING,
        ),
        "VERSIONED_CASH_FLOW_RESYNC_COMMITTED": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_RISK_RESYNC_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED,
        ),
        "VERSIONED_ORDER_ADMISSION_HELD": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_ADMISSION_PENDING,
        ),
        "VERSIONED_ORDER_ADMISSION_COMMITTED": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_ADMISSION_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED,
        ),
        "VERSIONED_ORDER_ARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_ARMED,
        ),
        "VERSIONED_ORDER_DISARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_ARMED},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED,
        ),
        "VERSIONED_FULL_FILL_CLOSED_DISARMED": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISPATCH_PENDING},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED,
        ),
        "VERSIONED_ORDER_ATTEMPT_RECORDED": (
            {RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_ARMED},
            RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISPATCH_PENDING,
        ),
        "ROLLBACK_TO_LEGACY": (
            {RuntimeCashAuthorityState.EXACT_CASH_DISARMED},
            RuntimeCashAuthorityState.LEGACY_ACTIVE,
        ),
    }
    pair = allowed.get(current.transition_kind)
    if pair is None or previous.state not in pair[0] or current.state is not pair[1]:
        _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.record_revision != previous.record_revision + 1:
        _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.previous_record_sha256 != previous.sha256:
        _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    expected_generation = previous.cutover_generation + (
        current.transition_kind == "PREPARE_CUTOVER"
    )
    if current.cutover_generation != expected_generation:
        _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.post_attempt_count != previous.post_attempt_count + (
        current.transition_kind in {"DISPATCH_ATTEMPT_RECORDED", "VERSIONED_ORDER_ATTEMPT_RECORDED"}
    ):
        _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if previous.ever_exact_activated and not current.ever_exact_activated:
        _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    mutable_by_kind = {
        "PREPARE_CUTOVER": {
            "cutover_generation",
            "account_scope_sha256",
            "identity_key_id",
            "activation_context_sha256",
            "ledger_head_sha256",
            "ledger_revision",
            "opening_cutoff",
            "opening_record_sha256",
            "operations_complete_through",
            "pending_dispatch_proof_sha256",
        },
        "PREPARATION_EVIDENCE_BOUND": {
            "ledger_head_sha256",
            "ledger_revision",
            "opening_cutoff",
            "opening_record_sha256",
            "operations_complete_through",
        },
        "CONFIRM_CUTOVER": set(),
        "CANCEL_CUTOVER": set(),
        "ACTIVATE_EXACT": {
            "activation_context_sha256",
            "ever_exact_activated",
            "ledger_head_sha256",
            "ledger_revision",
            "operations_complete_through",
        },
        "ARM_EXACT": set(),
        "DISARM_EXACT": set(),
        "SYNC_ADVANCED": {
            "ledger_head_sha256",
            "ledger_revision",
            "operations_complete_through",
        },
        "DISPATCH_ATTEMPT_RECORDED": {
            "pending_dispatch_proof_sha256",
            "post_attempt_count",
        },
        "DISPATCH_REJECTED_REARMED": {"pending_dispatch_proof_sha256"},
        "DISPATCH_ACCOUNTED_REARMED": {"pending_dispatch_proof_sha256"},
        "RECOVERY_CLOSED_DISARMED": {"pending_dispatch_proof_sha256"},
        "EXACT_SETTLEMENT_CLOSED_DISARMED": {
            "pending_dispatch_proof_sha256", "ledger_head_sha256",
            "ledger_revision", "operations_complete_through",
        },
        "EXACT_ZERO_TERMINAL_CLOSED_DISARMED": {
            "pending_dispatch_proof_sha256", "ledger_head_sha256",
            "ledger_revision", "operations_complete_through",
        },
        "FEE_ALIAS_HELD": {"pending_dispatch_proof_sha256"},
        "FEE_ALIAS_REVIEW_HELD": {"pending_dispatch_proof_sha256"},
        "FEE_ALIAS_CLOSED_DISARMED": {
            "pending_dispatch_proof_sha256", "operations_complete_through",
        },
        "FEE_REPLACEMENT_HELD": {"pending_dispatch_proof_sha256"},
        "FEE_REPLACEMENT_REVIEW_HELD": {"pending_dispatch_proof_sha256"},
        "FEE_REPLACEMENT_CLOSED_DISARMED": {
            "pending_dispatch_proof_sha256", "ledger_head_sha256",
            "ledger_revision", "operations_complete_through",
        },
        "LATE_FEE_ADJUSTMENT_HELD": {"pending_dispatch_proof_sha256"},
        "LATE_FEE_REVIEW_HELD": {"pending_dispatch_proof_sha256"},
        "LATE_FEE_ADJUSTMENT_CLOSED_DISARMED": {
            "pending_dispatch_proof_sha256", "ledger_head_sha256",
            "ledger_revision", "operations_complete_through",
        },
        "VERSIONED_SOURCE_CUTOVER_HELD": {"pending_dispatch_proof_sha256"},
        "VERSIONED_SOURCE_SELECTED_DISARMED": {
            "pending_dispatch_proof_sha256", "activation_context_sha256",
            "ledger_head_sha256", "ledger_revision", "operations_complete_through",
        },
        "VERSIONED_SELECTED_SYNC_HELD": {"pending_dispatch_proof_sha256"},
        "VERSIONED_SELECTED_SYNC_COMMITTED": {
            "pending_dispatch_proof_sha256", "activation_context_sha256",
            "ledger_head_sha256", "ledger_revision", "operations_complete_through",
        },
        "VERSIONED_SELECTED_SYNC_ABORTED": {
            "pending_dispatch_proof_sha256", "activation_context_sha256",
        },
        "VERSIONED_OWNER_REFRESH_HELD": {"pending_dispatch_proof_sha256"},
        "VERSIONED_OWNER_REFRESH_COMMITTED": {
            "pending_dispatch_proof_sha256", "activation_context_sha256",
        },
        "VERSIONED_CASH_FLOW_RESYNC_HELD": {"pending_dispatch_proof_sha256"},
        "VERSIONED_CASH_FLOW_RESYNC_COMMITTED": {
            "pending_dispatch_proof_sha256", "activation_context_sha256",
        },
        "VERSIONED_ORDER_ADMISSION_HELD": {"pending_dispatch_proof_sha256"},
        "VERSIONED_ORDER_ADMISSION_COMMITTED": {
            "pending_dispatch_proof_sha256", "activation_context_sha256",
        },
        "VERSIONED_ORDER_ARMED": {"activation_context_sha256"},
        "VERSIONED_ORDER_DISARMED": set(),
        "VERSIONED_ORDER_ATTEMPT_RECORDED": {"pending_dispatch_proof_sha256", "post_attempt_count"},
        "VERSIONED_FULL_FILL_CLOSED_DISARMED": {
            "pending_dispatch_proof_sha256", "activation_context_sha256",
            "ledger_head_sha256", "ledger_revision", "operations_complete_through",
        },
        "ROLLBACK_TO_LEGACY": set(),
    }
    if current.transition_kind == "VERSIONED_FULL_FILL_CLOSED_DISARMED":
        # This is positive full-FILL only: one trade and zero/one fee already
        # committed to v4. No zero terminal, re-arm or arbitrary pin update.
        if (previous.transition_kind != "VERSIONED_ORDER_ATTEMPT_RECORDED"
                or previous.post_attempt_count < 1
                or previous.pending_dispatch_proof_sha256 is None
                or current.pending_dispatch_proof_sha256 is not None
                or current.activation_context_sha256 is None
                or current.activation_context_sha256 == previous.activation_context_sha256
                or current.ledger_revision is None or previous.ledger_revision is None
                or current.ledger_revision - previous.ledger_revision not in {1, 2}
                or current.ledger_head_sha256 == previous.ledger_head_sha256
                or previous.operations_complete_through is None
                or current.operations_complete_through is None
                or not previous.operations_complete_through < current.operations_complete_through <= current.transition_at
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "VERSIONED_ORDER_ARMED":
        if (previous.transition_kind != "VERSIONED_ORDER_ADMISSION_COMMITTED"
                or current.activation_context_sha256 == previous.activation_context_sha256
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind in {"VERSIONED_ORDER_DISARMED", "VERSIONED_ORDER_ATTEMPT_RECORDED"}:
        if (previous.transition_kind != "VERSIONED_ORDER_ARMED"
                or current.transition_at < previous.transition_at
                or (current.transition_kind == "VERSIONED_ORDER_ATTEMPT_RECORDED"
                    and current.pending_dispatch_proof_sha256 is None)):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "EXACT_SETTLEMENT_CLOSED_DISARMED":
        if (current.ledger_revision is None or previous.ledger_revision is None
                or current.ledger_revision <= previous.ledger_revision
                or current.ledger_head_sha256 == previous.ledger_head_sha256
                or current.operations_complete_through is None
                or previous.operations_complete_through is None
                or not previous.operations_complete_through < current.operations_complete_through <= current.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "EXACT_ZERO_TERMINAL_CLOSED_DISARMED":
        # Dedicated zero-effect / one-fee transition. Never relax the positive
        # settlement rule, invent a zero transaction, or reset the spent attempt.
        if (current.ledger_revision is None or previous.ledger_revision is None
                or current.ledger_revision - previous.ledger_revision not in {0, 1}
                or (current.ledger_head_sha256 == previous.ledger_head_sha256)
                   != (current.ledger_revision == previous.ledger_revision)
                or current.operations_complete_through is None
                or previous.operations_complete_through is None
                or not previous.operations_complete_through < current.operations_complete_through <= current.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind in {"FEE_ALIAS_HELD", "FEE_ALIAS_REVIEW_HELD"}:
        if (previous.post_attempt_count < 1 or current.pending_dispatch_proof_sha256 is None
                or current.transition_at < previous.transition_at
                or (current.transition_kind == "FEE_ALIAS_REVIEW_HELD"
                    and previous.transition_kind != "FEE_ALIAS_CLOSED_DISARMED")):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "FEE_ALIAS_CLOSED_DISARMED":
        # An observation alias is never a ledger mutation or a dispatch attempt.
        if (previous.transition_kind != "FEE_ALIAS_HELD"
                or current.ledger_revision is None or current.ledger_head_sha256 is None
                or current.ledger_revision != previous.ledger_revision
                or current.ledger_head_sha256 != previous.ledger_head_sha256
                or current.pending_dispatch_proof_sha256 is not None
                or current.operations_complete_through is None
                or previous.operations_complete_through is None
                or not previous.operations_complete_through < current.operations_complete_through <= current.transition_at
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind in {"FEE_REPLACEMENT_HELD", "FEE_REPLACEMENT_REVIEW_HELD"}:
        if (previous.post_attempt_count < 1 or current.pending_dispatch_proof_sha256 is None
                or current.transition_at < previous.transition_at
                or (current.transition_kind == "FEE_REPLACEMENT_REVIEW_HELD"
                    and previous.transition_kind != "FEE_REPLACEMENT_CLOSED_DISARMED")):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "FEE_REPLACEMENT_CLOSED_DISARMED":
        # CL2 correction bundle is one ledger transition with two transactions.
        if (previous.transition_kind != "FEE_REPLACEMENT_HELD"
                or current.ledger_revision is None or previous.ledger_revision is None
                or current.ledger_revision != previous.ledger_revision + 1
                or current.ledger_head_sha256 == previous.ledger_head_sha256
                or current.operations_complete_through is None
                or previous.operations_complete_through is None
                or not previous.operations_complete_through < current.operations_complete_through <= current.transition_at
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "LATE_FEE_ADJUSTMENT_HELD":
        # A separate quarantine, not a spent POST attempt or an auto-arm.
        if (previous.post_attempt_count < 1
                or current.pending_dispatch_proof_sha256 is None
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "LATE_FEE_ADJUSTMENT_CLOSED_DISARMED":
        if (previous.transition_kind != "LATE_FEE_ADJUSTMENT_HELD"
                or current.ledger_revision is None or previous.ledger_revision is None
                or current.ledger_revision != previous.ledger_revision + 1
                or current.ledger_head_sha256 == previous.ledger_head_sha256
                or current.operations_complete_through is None
                or previous.operations_complete_through is None
                or not previous.operations_complete_through < current.operations_complete_through <= current.transition_at
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "LATE_FEE_REVIEW_HELD" and (
                previous.transition_kind != "LATE_FEE_ADJUSTMENT_CLOSED_DISARMED"
                or previous.post_attempt_count < 1
                or previous.pending_dispatch_proof_sha256 is not None
                or current.pending_dispatch_proof_sha256 is None
                or current.transition_at < previous.transition_at):
        _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "VERSIONED_SOURCE_CUTOVER_HELD":
        if (current.pending_dispatch_proof_sha256 is None
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "VERSIONED_SOURCE_SELECTED_DISARMED":
        # Source selection is NOT arm. Only the first verified same-ID fee
        # correction (one ledger revision) may enter this parked v4 state.
        if (previous.transition_kind != "VERSIONED_SOURCE_CUTOVER_HELD"
                or current.activation_context_sha256 != previous.pending_dispatch_proof_sha256
                or current.pending_dispatch_proof_sha256 is not None
                or previous.ledger_revision is None or current.ledger_revision != previous.ledger_revision + 1
                or current.ledger_head_sha256 == previous.ledger_head_sha256
                or current.operations_complete_through is None
                or previous.operations_complete_through is None
                or not previous.operations_complete_through <= current.operations_complete_through <= current.transition_at
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "VERSIONED_SELECTED_SYNC_HELD":
        if (current.pending_dispatch_proof_sha256 is None
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind in {
        "VERSIONED_SELECTED_SYNC_COMMITTED", "VERSIONED_SELECTED_SYNC_ABORTED",
    }:
        if (previous.transition_kind != "VERSIONED_SELECTED_SYNC_HELD"
                or current.pending_dispatch_proof_sha256 is not None
                or current.activation_context_sha256 == previous.activation_context_sha256
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        if current.transition_kind == "VERSIONED_SELECTED_SYNC_COMMITTED":
            if (previous.ledger_revision is None or current.ledger_revision is None
                    or not 0 <= current.ledger_revision - previous.ledger_revision <= 128
                    or (current.ledger_head_sha256 == previous.ledger_head_sha256)
                       != (current.ledger_revision == previous.ledger_revision)
                    or previous.operations_complete_through is None
                    or current.operations_complete_through is None
                    or not previous.operations_complete_through <= current.operations_complete_through <= current.transition_at):
                _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "VERSIONED_OWNER_REFRESH_HELD":
        if current.pending_dispatch_proof_sha256 is None or current.transition_at < previous.transition_at:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "VERSIONED_OWNER_REFRESH_COMMITTED":
        if (previous.transition_kind != "VERSIONED_OWNER_REFRESH_HELD"
                or current.pending_dispatch_proof_sha256 is not None
                or current.activation_context_sha256 == previous.activation_context_sha256
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "VERSIONED_CASH_FLOW_RESYNC_HELD":
        if current.pending_dispatch_proof_sha256 is None or current.transition_at < previous.transition_at:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "VERSIONED_CASH_FLOW_RESYNC_COMMITTED":
        if (previous.transition_kind != "VERSIONED_CASH_FLOW_RESYNC_HELD"
                or current.pending_dispatch_proof_sha256 is not None
                or current.activation_context_sha256 == previous.activation_context_sha256
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "VERSIONED_ORDER_ADMISSION_HELD":
        if current.pending_dispatch_proof_sha256 is None or current.transition_at < previous.transition_at:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    if current.transition_kind == "VERSIONED_ORDER_ADMISSION_COMMITTED":
        if (previous.transition_kind != "VERSIONED_ORDER_ADMISSION_HELD"
                or current.pending_dispatch_proof_sha256 is not None
                or current.activation_context_sha256 == previous.activation_context_sha256
                or current.transition_at < previous.transition_at):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
    always = {
        "record_revision",
        "previous_record_sha256",
        "transition_at",
        "transition_kind",
        "state",
    }
    allowed_changes = always | mutable_by_kind[current.transition_kind]
    for item in fields(RuntimeCashAuthorityRecord):
        if item.name not in allowed_changes and getattr(previous, item.name) != getattr(
            current, item.name
        ):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)


class RuntimeCashAuthorityStore:
    """Exact CL7 active/checksum/lastgood custody with revision+SHA CAS."""

    def __init__(self, path: str | Path, *, lock_timeout_seconds: float = 5.0) -> None:
        selected = Path(path)
        self.path = (
            selected / "runtime_cash_authority.json"
            if selected.suffix.lower() != ".json"
            else selected
        )
        self.checksum_path = self.path.with_name(self.path.name + ".sha256")
        self.lastgood_path = self.path.with_name(self.path.name + ".lastgood")
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.ledger_path = self.path.with_name("cash_ledger_v3_10.sqlite3")
        self.lock_timeout_seconds = max(0.0, float(lock_timeout_seconds))

    @contextmanager
    def locked(self) -> Iterator[RuntimeCashAuthorityStore]:
        try:
            with InterProcessFileLock(
                self.lock_path, timeout_seconds=self.lock_timeout_seconds
            ):
                yield self
        except LockUnavailableError:
            _fail(CL7RuntimeReason.LOCK_UNAVAILABLE, retryable=True)

    def custody_exists(self) -> bool:
        return any(
            path.exists()
            for path in (
                self.path,
                self.checksum_path,
                self.lastgood_path,
                self.ledger_path,
            )
        )

    def load(self, *, allow_missing_legacy: bool = True) -> RuntimeCashAuthorityRecord:
        with self.locked():
            return self._load_unlocked(allow_missing_legacy=allow_missing_legacy)

    def _load_unlocked(
        self, *, allow_missing_legacy: bool = True
    ) -> RuntimeCashAuthorityRecord:
        custody = (
            self.path.exists(),
            self.checksum_path.exists(),
            self.lastgood_path.exists(),
            self.ledger_path.exists(),
        )
        if not custody[0]:
            if allow_missing_legacy and not any(custody):
                return RuntimeCashAuthorityRecord.bootstrap(
                    "1970-01-01T00:00:00.000000000Z"
                )
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_MISSING)
        if not custody[1]:
            _fail(CL7RuntimeReason.AUTHORITY_CHECKSUM_INVALID)
        try:
            active_bytes = self.path.read_bytes()
            checksum_bytes = self.checksum_path.read_bytes()
        except OSError:
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
        active = RuntimeCashAuthorityRecord.from_canonical_bytes(active_bytes)
        expected_checksum = (active.sha256 + "\n").encode("ascii")
        if not hmac.compare_digest(checksum_bytes, expected_checksum):
            _fail(CL7RuntimeReason.AUTHORITY_CHECKSUM_INVALID)
        if active.record_revision == 0:
            if self.lastgood_path.exists():
                _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
            return active
        if not self.lastgood_path.exists():
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
        try:
            previous_bytes = self.lastgood_path.read_bytes()
        except OSError:
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
        previous = RuntimeCashAuthorityRecord.from_canonical_bytes(previous_bytes)
        if (
            previous.record_revision != active.record_revision - 1
            or active.previous_record_sha256 != previous.sha256
        ):
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
        _transition_pair(previous, active)
        return active

    def bootstrap(self, *, transition_at: str) -> RuntimeCashAuthorityRecord:
        record = RuntimeCashAuthorityRecord.bootstrap(transition_at)
        with self.locked():
            if self.custody_exists():
                return self._load_unlocked(allow_missing_legacy=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            active_temp = self._write_temp(self.path, record.canonical_bytes)
            checksum_temp: Path | None = None
            try:
                checksum_temp = self._write_temp(
                    self.checksum_path,
                    (record.sha256 + "\n").encode("ascii"),
                )
                self._replace_prepared(active_temp, self.path)
                self._sync_parent()
                self._replace_prepared(checksum_temp, self.checksum_path)
                self._sync_parent()
            finally:
                active_temp.unlink(missing_ok=True)
                if checksum_temp is not None:
                    checksum_temp.unlink(missing_ok=True)
            return self._load_unlocked(allow_missing_legacy=False)

    def _commit_unlocked(
        self,
        candidate: RuntimeCashAuthorityRecord,
        *,
        expected_revision: int,
        expected_sha256: str,
    ) -> RuntimeCashAuthorityRecord:
        current = self._load_unlocked(allow_missing_legacy=False)
        if current.record_revision != expected_revision or not hmac.compare_digest(
            current.sha256, expected_sha256
        ):
            _fail(CL7RuntimeReason.CAS_CONFLICT)
        _transition_pair(current, candidate)
        if candidate.transition_kind == "LATE_FEE_REVIEW_HELD":
            # The completed record's retained predecessor carries its exact
            # consumed proof. A structurally valid unrelated hash cannot be
            # committed as a review hold, even through this internal store API.
            origin = RuntimeCashAuthorityRecord.from_canonical_bytes(
                self.lastgood_path.read_bytes()
            )
            if (origin.transition_kind != "LATE_FEE_ADJUSTMENT_HELD"
                    or origin.state is not RuntimeCashAuthorityState.EXACT_CASH_FEE_ADJUSTMENT_PENDING
                    or current.previous_record_sha256 != origin.sha256
                    or candidate.pending_dispatch_proof_sha256 != origin.pending_dispatch_proof_sha256):
                _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        next_temp = self._write_temp(self.path, candidate.canonical_bytes)
        checksum_temp: Path | None = None
        lastgood_temp: Path | None = None
        try:
            prepared = RuntimeCashAuthorityRecord.from_canonical_bytes(
                next_temp.read_bytes()
            )
            if not hmac.compare_digest(
                prepared.canonical_bytes,
                candidate.canonical_bytes,
            ):
                _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
            checksum_temp = self._write_temp(
                self.checksum_path,
                (candidate.sha256 + "\n").encode("ascii"),
            )
            lastgood_temp = self._write_temp(
                self.lastgood_path,
                current.canonical_bytes,
            )
            self._replace_prepared(lastgood_temp, self.lastgood_path)
            self._sync_parent()
            self._replace_prepared(next_temp, self.path)
            self._sync_parent()
            self._replace_prepared(checksum_temp, self.checksum_path)
            self._sync_parent()
        finally:
            next_temp.unlink(missing_ok=True)
            if checksum_temp is not None:
                checksum_temp.unlink(missing_ok=True)
            if lastgood_temp is not None:
                lastgood_temp.unlink(missing_ok=True)
        readback = self._load_unlocked(allow_missing_legacy=False)
        if not hmac.compare_digest(readback.canonical_bytes, candidate.canonical_bytes):
            _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
        return readback

    @staticmethod
    def _write_temp(path: Path, value: bytes) -> Path:
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            if temporary.read_bytes() != value:
                _fail(CL7RuntimeReason.AUTHORITY_RECORD_CORRUPT)
            return temporary
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _replace_prepared(temporary: Path, path: Path) -> None:
        os.replace(temporary, path)

    def _sync_parent(self) -> None:
        try:
            flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
            descriptor = os.open(self.path.parent, flags)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass


@dataclass(frozen=True, slots=True)
class _RuntimeEvidenceSet:
    ledger_export_bytes: bytes
    broker_cash_proof: Any
    broker_withdraw_limits_proof: Any
    reconciliation: Any
    reservations: Any
    availability: Any
    portfolio: Any
    risk_guard: Any
    context: Any
    broker_own_buying_cash_proof: Any = None


class RuntimeCashAuthorityManager:
    """CAS state machine and exact proof/attempt boundary; transport-free."""

    PREPARE_PHRASE = "PREPARE V3.10 CL7 EXACT CASH CUTOVER"
    CONFIRM_PHRASE = "CONFIRM V3.10 CL7 EXACT CASH CUTOVER"
    ACTIVATE_PHRASE = "ACTIVATE V3.10 CL7 EXACT CASH AUTHORITY"
    ARM_PHRASE = "ARM V3.10 CL7 SANDBOX EXACT CASH EXECUTION"
    ROLLBACK_PHRASE = "ROLLBACK V3.10 CL7 TO LEGACY CASH AUTHORITY"

    def __init__(self, store: RuntimeCashAuthorityStore, *, cash_source_version: int = 2,
                 buying_budget_policy: Any = None) -> None:
        if (type(store) is not RuntimeCashAuthorityStore
            or type(cash_source_version) is not int or cash_source_version not in (2, 3)):
            _fail(CL7RuntimeReason.TYPE_INVALID)
        self.store = store
        self._cash_source_version = cash_source_version
        from .cash_buying_availability import OwnBuyingBudgetPolicy
        if buying_budget_policy is not None and (type(buying_budget_policy) is not OwnBuyingBudgetPolicy
                                                 or cash_source_version != 3):
            _fail(CL7RuntimeReason.TYPE_INVALID)
        self._buying_budget_policy = buying_budget_policy

    @property
    def cash_source_version(self) -> int:
        """CL4 source version. The separate buying policy selects CL5 semantics."""
        if type(self._cash_source_version) is not int or self._cash_source_version not in (2, 3):
            _fail(CL7RuntimeReason.TYPE_INVALID)
        return self._cash_source_version

    @property
    def buying_budget_policy(self) -> Any:
        from .cash_buying_availability import OwnBuyingBudgetPolicy
        policy = getattr(self, "_buying_budget_policy", None)
        if policy is not None and (type(policy) is not OwnBuyingBudgetPolicy or self.cash_source_version != 3):
            _fail(CL7RuntimeReason.TYPE_INVALID)
        return policy

    def read_availability_cash(
        self, provider: Any, raw_account_id: str, *, account_scope_sha256: str,
        identity_key: bytes, identity_key_id: str, clock: Any, monotonic_ns: Any,
    ) -> tuple[Any, str | None, Any]:
        """Legacy observation/time or V3 own proof. Never relabel withdrawal data."""
        from .cash_availability import CL5Error
        policy = self.buying_budget_policy
        if policy is None:
            observation = provider.get_withdraw_limits(raw_account_id)
            return observation, _timestamp(clock()), None
        if policy.account_id != raw_account_id:
            _fail(CL7RuntimeReason.ACCOUNT_SCOPE_INVALID)
        try:
            proof = policy.acquire(provider, account_scope_sha256=account_scope_sha256,
                identity_key=identity_key, identity_key_id=identity_key_id,
                clock=lambda: _timestamp(clock()), monotonic_ns=monotonic_ns)
        except CL5Error as exc:
            _fail(CL7RuntimeReason.AVAILABILITY_BLOCKED, exc.reason.value, stage="CL5_OWN_BUYING")
        return None, None, proof

    def read_accounting_cash(
        self, provider: Any, raw_account_id: str, *, clock: Any = None, monotonic_ns: Any = None,
    ) -> object:
        """Read the selected raw source without fallback or field rewriting.

        V3 bounds a single synchronous read by both clocks (5s); this is an
        elapsed check, not HTTP cancellation or a broker-atomic snapshot.
        """
        if self.cash_source_version == 3:
            if not callable(clock) or not callable(monotonic_ns):
                _fail(CL7RuntimeReason.TYPE_INVALID)
            begin = _cash_timestamp_ns(_timestamp(clock()))
            tick = monotonic_ns()
            if type(tick) is not int or tick < 0:
                _fail(CL7RuntimeReason.CONTEXT_STALE)
            response = provider.get_positions(raw_account_id)
            end = _cash_timestamp_ns(_timestamp(clock()))
            finish = monotonic_ns()
            if (type(finish) is not int or not 0 <= finish - tick <= 5_000_000_000
                or not 0 <= end - begin <= 5_000_000_000):
                _fail(CL7RuntimeReason.CONTEXT_STALE)
            return response
        if self.cash_source_version != 2:
            _fail(CL7RuntimeReason.TYPE_INVALID)
        return provider.get_portfolio(raw_account_id)

    def _build_accounting_cash_proof(self, response: object, *, raw_account_id: str, **kwargs: Any) -> Any:
        from . import cash_ledger_opening_reconciliation as cl4
        if self.cash_source_version == 3:
            return cl4.build_broker_rub_position_cash_proof(
                response, raw_account_id=raw_account_id, **kwargs,
            )
        if self.cash_source_version != 2:
            _fail(CL7RuntimeReason.TYPE_INVALID)
        return cl4.build_broker_cash_proof(response, **kwargs)

    def status(self) -> RuntimeCashAuthorityRecord:
        return self.store.load()

    @contextmanager
    def ledger_guard(self, ledger_store: Any) -> Iterator[Any]:
        """Freeze CL2 against every SQLite writer during final revalidation."""

        # Explicit v4 pin/lease path. This only freezes a complete cash graph;
        # it does not turn the view into a v1 store or an accepted CL7 context.
        from .versioned_operational_store import VersionedOperationalStore
        from .versioned_runtime_adapter import VersionedRuntimeStoreAdapter

        if type(ledger_store) is VersionedRuntimeStoreAdapter:
            try:
                with ledger_store.locked_snapshot() as view:
                    yield view
            except CL7RuntimeError:
                raise
            except Exception:
                _fail(CL7RuntimeReason.LEDGER_UNAVAILABLE, stage="VERSIONED_LEDGER")
            return
        if type(ledger_store) is VersionedOperationalStore:
            _fail(CL7RuntimeReason.LEDGER_UNAVAILABLE,
                  "VERSIONED_PINNED_ADAPTER_REQUIRED", stage="VERSIONED_LEDGER")

        root = getattr(ledger_store, "root", None)
        if not isinstance(root, Path):
            _fail(CL7RuntimeReason.LEDGER_UNAVAILABLE)
        database = root / "store.sqlite3"
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                database,
                timeout=self.store.lock_timeout_seconds,
                isolation_level=None,
            )
            connection.execute(
                f"PRAGMA busy_timeout={int(self.store.lock_timeout_seconds * 1000)}"
            )
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            if connection is not None:
                connection.close()
            _fail(
                CL7RuntimeReason.LOCK_UNAVAILABLE,
                stage="CASH_LEDGER",
                retryable=True,
            )
        except (OSError, sqlite3.Error):
            if connection is not None:
                connection.close()
            _fail(CL7RuntimeReason.LEDGER_UNAVAILABLE, stage="CASH_LEDGER")
        try:
            yield ledger_store
        finally:
            if connection is not None:
                try:
                    connection.execute("ROLLBACK")
                finally:
                    connection.close()

    def ensure_opening_locked(
        self,
        current: RuntimeCashAuthorityRecord,
        *,
        ledger_store: Any,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
        portfolio_response: object,
        as_of: str,
        evaluated_at: str,
    ) -> tuple[Any, Any]:
        """Create or reuse the one accepted CL4 FROM_NOW opening."""

        from . import broker_read_adapters as broker
        from . import cash_ledger_opening_reconciliation as cl4

        if type(current) is not RuntimeCashAuthorityRecord:
            _fail(CL7RuntimeReason.TYPE_INVALID)
        if current.state is not RuntimeCashAuthorityState.CUTOVER_PREPARED:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        self._account(current, raw_account_id, identity_key, identity_key_id)
        try:
            proof = self._build_accounting_cash_proof(
                portfolio_response, raw_account_id=raw_account_id,
                account_scope_sha256=current.account_scope_sha256,
                environment=broker.BrokerEnvironment.SANDBOX,
                as_of=_timestamp(as_of),
                evaluated_at=_timestamp(evaluated_at),
                response_complete=True,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
            )
            plan = cl4.prepare_from_now_opening(
                ledger_store.export_bytes(),
                proof,
                evaluated_at=evaluated_at,
                identity_key=identity_key,
            )
            acceptance = cl4.accept_from_now_opening(
                ledger_store,
                plan,
                confirmation=f"ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}",
                evaluated_at=evaluated_at,
                identity_key=identity_key,
            )
            return acceptance, proof
        except cl4.CL4Error as exc:
            _fail(
                CL7RuntimeReason.OPENING_INVALID,
                exc.reason.value,
                stage="CL4_OPENING",
            )
        except CL7RuntimeError:
            raise
        except Exception:
            _fail(CL7RuntimeReason.INTERNAL_BOUNDARY_FAILED, stage="CL4_OPENING")

    def build_runtime_context(
        self,
        *,
        current: RuntimeCashAuthorityRecord,
        ledger_store: Any,
        portfolio_response: object,
        withdraw_limits_observation: object,
        broker_cash_as_of: str,
        broker_withdraw_limits_as_of: str | None,
        central_state: Any,
        portfolio_lease: Any,
        risk_policy: Any,
        risk_state: Any,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
        evaluated_at: str,
        require_ready: bool = True,
        own_buying_cash_proof: Any = None,
    ) -> Any:
        """Rebuild current CL4 -> CL5 -> CL6 evidence from exact inputs."""

        from . import broker_read_adapters as broker
        from . import cash_availability as cl5
        from . import cash_ledger_opening_reconciliation as cl4
        from . import reporting_risk_cash_context as cl6

        if type(require_ready) is not bool:
            _fail(CL7RuntimeReason.TYPE_INVALID)
        self._account(current, raw_account_id, identity_key, identity_key_id)
        try:
            ledger_export = ledger_store.export_bytes()
            cash = self._build_accounting_cash_proof(
                portfolio_response, raw_account_id=raw_account_id,
                account_scope_sha256=current.account_scope_sha256,
                environment=broker.BrokerEnvironment.SANDBOX,
                as_of=_timestamp(broker_cash_as_of),
                evaluated_at=_timestamp(evaluated_at),
                response_complete=True,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
            )
            from . import cash_buying_availability as buying
            policy = self.buying_budget_policy
            if policy is None:
                if own_buying_cash_proof is not None:
                    _fail(CL7RuntimeReason.AVAILABILITY_BLOCKED, stage="CL5_SOURCE_MISMATCH")
                withdraw_limits = cl5.build_broker_withdraw_limits_cash_proof(
                    withdraw_limits_observation,
                    account_scope_sha256=current.account_scope_sha256,
                    environment=broker.BrokerEnvironment.SANDBOX,
                    as_of=_timestamp(broker_withdraw_limits_as_of),
                    evaluated_at=evaluated_at, response_complete=True,
                    identity_key=identity_key, identity_key_id=identity_key_id,
                )
            else:
                if withdraw_limits_observation is not None or broker_withdraw_limits_as_of is not None:
                    _fail(CL7RuntimeReason.AVAILABILITY_BLOCKED, stage="CL5_SOURCE_MISMATCH")
                policy.binding_guard()
                withdraw_limits = buying.validate_own_buying_proof(own_buying_cash_proof, identity_key,
                    buying_scope_sha256=policy.scope_sha256)
            availability_builder = buying.build_own_cash_availability if policy is not None else cl5.build_cash_availability
            reconciliation = cl4.reconcile_shadow_cash(
                ledger_export,
                cash,
                evaluated_at=evaluated_at,
                identity_key=identity_key,
            )
            reservations = cl5.project_central_reservations(
                central_state,
                account_scope_sha256=current.account_scope_sha256,
                environment=broker.BrokerEnvironment.SANDBOX,
                evaluated_at=evaluated_at,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
            )
            availability = availability_builder(
                ledger_export,
                reconciliation,
                withdraw_limits,
                reservations,
                evaluated_at=evaluated_at,
                identity_key=identity_key,
            )
            portfolio = cl6.build_portfolio_identity_evidence(
                portfolio_lease,
                account_scope_sha256=current.account_scope_sha256,
                environment=broker.BrokerEnvironment.SANDBOX,
                evaluated_at=evaluated_at,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
            )
            risk_guard = cl6.build_risk_guard_evidence(
                risk_policy,
                risk_state,
                raw_account_id=raw_account_id,
                account_scope_sha256=current.account_scope_sha256,
                environment=broker.BrokerEnvironment.SANDBOX,
                captured_at=evaluated_at,
                evaluated_at=evaluated_at,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
            )
            context = cl6.build_portfolio_risk_cash_context(
                ledger_export,
                reconciliation,
                withdraw_limits,
                reservations,
                availability,
                portfolio,
                risk_guard,
                evaluated_at=evaluated_at,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
            )
        except cl4.CL4Error as exc:
            _fail(
                CL7RuntimeReason.RECONCILIATION_BLOCKED,
                exc.reason.value,
                stage="CL4_RECONCILIATION",
            )
        except cl5.CL5Error as exc:
            _fail(
                CL7RuntimeReason.AVAILABILITY_BLOCKED,
                exc.reason.value,
                stage="CL5_AVAILABILITY",
            )
        except cl6.CL6Error as exc:
            _fail(
                CL7RuntimeReason.CONTEXT_BLOCKED,
                exc.reason.value,
                stage="CL6_CONTEXT",
            )
        except CL7RuntimeError:
            raise
        except Exception:
            _fail(CL7RuntimeReason.INTERNAL_BOUNDARY_FAILED, stage="EVIDENCE_BUILD")
        if (
            require_ready
            and getattr(getattr(context, "status", None), "value", None)
            != "READY_FOR_LOCKED_REVALIDATION"
        ):
            _fail_context_not_ready(context)
        snapshot = ledger_store.snapshot()
        if (
            context.ledger_revision != snapshot.ledger_revision
            or context.ledger_head_sha256 != snapshot.ledger_head_sha256
        ):
            _fail(CL7RuntimeReason.LEDGER_CHANGED)
        return _RuntimeEvidenceSet(
            ledger_export_bytes=ledger_export,
            broker_cash_proof=cash,
            broker_withdraw_limits_proof=withdraw_limits if policy is None else None,
            broker_own_buying_cash_proof=withdraw_limits if policy is not None else None,
            reconciliation=reconciliation,
            reservations=reservations,
            availability=availability,
            portfolio=portfolio,
            risk_guard=risk_guard,
            context=context,
        )

    def rebuild_context_with_locks(
        self,
        *,
        current: RuntimeCashAuthorityRecord,
        ledger_store: Any,
        portfolio_repository: Any,
        risk_profile_store: Any,
        risk_state_store: Any,
        central_manager: Any,
        portfolio_response: object,
        withdraw_limits_observation: object,
        broker_cash_as_of: str,
        broker_withdraw_limits_as_of: str | None,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
        evaluated_at: str,
        require_ready: bool = True,
        own_buying_cash_proof: Any = None,
        finalizer: Any | None = None,
    ) -> _RuntimeEvidenceSet:
        """Freeze Portfolio/Risk/ledger/Central in the contract lock order."""

        from .portfolio_preflight import PortfolioSnapshotLease

        try:
            with (
                portfolio_repository.locked_snapshot(
                    expected_account_id=raw_account_id
                ) as portfolio_state,
                InterProcessFileLock(
                    risk_profile_store.lock_path,
                    timeout_seconds=self.store.lock_timeout_seconds,
                ),
            ):
                loaded = risk_profile_store.require_profile("SANDBOX_EXECUTION")
                policy = loaded["policy"]
                with InterProcessFileLock(
                    risk_state_store.lock_path,
                    timeout_seconds=self.store.lock_timeout_seconds,
                ):
                    risk_state = risk_state_store.load_account(raw_account_id)
                    lease = PortfolioSnapshotLease.from_state(
                        portfolio_state,
                        leased_at=_timestamp_to_iso(evaluated_at),
                    )
                    with self.ledger_guard(ledger_store):

                        def build(central: Any) -> Any:
                            evidence = self.build_runtime_context(
                                current=current,
                                ledger_store=ledger_store,
                                portfolio_response=portfolio_response,
                                withdraw_limits_observation=(
                                    withdraw_limits_observation
                                ),
                                broker_cash_as_of=broker_cash_as_of,
                                broker_withdraw_limits_as_of=(
                                    broker_withdraw_limits_as_of
                                ),
                                central_state=central,
                                portfolio_lease=lease,
                                risk_policy=policy,
                                risk_state=risk_state,
                                raw_account_id=raw_account_id,
                                identity_key=identity_key,
                                identity_key_id=identity_key_id,
                                evaluated_at=evaluated_at,
                                require_ready=require_ready,
                                own_buying_cash_proof=own_buying_cash_proof,
                            )
                            if finalizer is None:
                                return evidence
                            if not callable(finalizer):
                                _fail(CL7RuntimeReason.TYPE_INVALID)
                            return finalizer(evidence, central)

                        return central_manager.inspect_locked(build)
        except CL7RuntimeError:
            raise
        except LockUnavailableError:
            _fail(CL7RuntimeReason.LOCK_UNAVAILABLE, retryable=True)
        except Exception:
            _fail(CL7RuntimeReason.INTERNAL_BOUNDARY_FAILED, stage="LOCAL_SNAPSHOT")

    def require_quiescent(
        self,
        *,
        current: RuntimeCashAuthorityRecord,
        central_state: Any,
        runtime_dir: str | Path,
        evidence: _RuntimeEvidenceSet,
    ) -> None:
        """Apply the bounded Central/legacy/exact-evidence quiescence gate."""

        if current.pending_dispatch_proof_sha256 is not None:
            _fail(CL7RuntimeReason.CUTOVER_NOT_QUIESCENT)
        try:
            if any(
                item.status in {"QUEUED", "IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}
                for item in central_state.intents
            ):
                _fail(CL7RuntimeReason.CUTOVER_NOT_QUIESCENT)
        except CL7RuntimeError:
            raise
        except Exception:
            _fail(CL7RuntimeReason.CENTRAL_CHANGED)
        root = Path(runtime_dir)
        for name in ("robot_state.json", "sandbox_diagnostic_state.json"):
            path = root / name
            if not path.exists():
                continue
            try:
                raw = path.read_bytes()
                if not 1 <= len(raw) <= _MAX_RECORD_BYTES:
                    raise ValueError
                document = json.loads(raw.decode("utf-8-sig"))
                if type(document) is not dict:
                    raise ValueError
                pending = document.get("pending_order")
            except Exception:
                _fail(CL7RuntimeReason.LEGACY_PENDING_OPERATION)
            if pending is not None:
                _fail(CL7RuntimeReason.LEGACY_PENDING_OPERATION)
        if (
            getattr(getattr(evidence.reconciliation, "status", None), "value", None)
            != "MATCHED"
            or getattr(
                getattr(evidence.reconciliation, "discrepancy_kind", None),
                "value",
                None,
            )
            != "NONE"
            or getattr(getattr(evidence.availability, "status", None), "value", None)
            != "READY"
            or getattr(getattr(evidence.context, "status", None), "value", None)
            != "READY_FOR_LOCKED_REVALIDATION"
        ):
            _fail(CL7RuntimeReason.CUTOVER_NOT_QUIESCENT)

    def _sync_and_rebuild_locked(
        self,
        current: RuntimeCashAuthorityRecord,
        *,
        ledger_store: Any,
        portfolio_repository: Any,
        risk_profile_store: Any,
        risk_state_store: Any,
        central_manager: Any,
        provider: Any,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
        clock: Any,
        monotonic_ns: Any,
        wait_ns: Any,
        commit_sync: bool,
        require_ready: bool,
        finalizer: Any | None = None,
    ) -> tuple[RuntimeCashAuthorityRecord, Any, Any]:
        from .broker_read_adapters import RetryPolicy

        if not callable(clock) or not callable(monotonic_ns) or not callable(wait_ns):
            _fail(CL7RuntimeReason.TYPE_INVALID)
        sync_to = _timestamp(clock())
        if current.operations_complete_through is None:
            _fail(CL7RuntimeReason.OPENING_INVALID)
        if sync_to <= current.operations_complete_through:
            sync_to = _successor_timestamp(current.operations_complete_through)
        current, batch = self.synchronize_operations_locked(
            current,
            ledger_store=ledger_store,
            raw_account_id=raw_account_id,
            identity_key=identity_key,
            identity_key_id=identity_key_id,
            sync_to_exclusive=sync_to,
            transport=provider.get_operations_by_cursor_once,
            monotonic_ns=monotonic_ns,
            wait_ns=wait_ns,
            absolute_deadline_ns=monotonic_ns() + 60_000_000_000,
            retry_policy=RetryPolicy(
                max_attempts=3,
                per_attempt_timeout_ns=10_000_000_000,
                backoff_ns=(100_000_000, 500_000_000),
            ),
            transition_at=_timestamp(clock()),
            commit_authority=commit_sync,
        )
        try:
            portfolio_response = self.read_accounting_cash(
                provider, raw_account_id, clock=clock, monotonic_ns=monotonic_ns,
            )
            broker_cash_as_of = _timestamp(clock())
            if self.buying_budget_policy is None:
                # Preserve the historical call sequence and independently sampled time.
                withdraw_limits_observation = provider.get_withdraw_limits(raw_account_id)
                broker_withdraw_limits_as_of = _timestamp(clock())
                own_buying_cash_proof = None
            else:
                withdraw_limits_observation, broker_withdraw_limits_as_of, own_buying_cash_proof = self.read_availability_cash(
                    provider, raw_account_id, account_scope_sha256=current.account_scope_sha256,
                    identity_key=identity_key, identity_key_id=identity_key_id,
                    clock=clock, monotonic_ns=monotonic_ns,
                )
        except CL7RuntimeError:
            raise
        except Exception:
            _fail(
                CL7RuntimeReason.BROKER_READ_FAILED,
                stage="CURRENT_CASH_WITHDRAW_LIMITS",
                retryable=True,
            )
        evaluated_at = _timestamp(clock())
        locked_finalizer = None
        if finalizer is not None:
            if not callable(finalizer):
                _fail(CL7RuntimeReason.TYPE_INVALID)

            def locked_finalizer(evidence: _RuntimeEvidenceSet, central: Any) -> Any:
                return finalizer(current, batch, evidence, central)

        evidence_or_result = self.rebuild_context_with_locks(
            current=current,
            ledger_store=ledger_store,
            portfolio_repository=portfolio_repository,
            risk_profile_store=risk_profile_store,
            risk_state_store=risk_state_store,
            central_manager=central_manager,
            portfolio_response=portfolio_response,
            withdraw_limits_observation=withdraw_limits_observation,
            broker_cash_as_of=broker_cash_as_of,
            broker_withdraw_limits_as_of=broker_withdraw_limits_as_of,
            own_buying_cash_proof=own_buying_cash_proof,
            raw_account_id=raw_account_id,
            identity_key=identity_key,
            identity_key_id=identity_key_id,
            evaluated_at=evaluated_at,
            require_ready=require_ready,
            finalizer=locked_finalizer,
        )
        return current, batch, evidence_or_result

    def prepare_runtime(
        self,
        *,
        ledger_store: Any,
        portfolio_repository: Any,
        risk_profile_store: Any,
        risk_state_store: Any,
        central_manager: Any,
        provider: Any,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
        confirmation: str,
        clock: Any,
        monotonic_ns: Any,
        wait_ns: Any,
    ) -> tuple[RuntimeCashAuthorityRecord, _RuntimeEvidenceSet]:
        """Freeze legacy creation, create/reuse opening, sync, and preview."""

        if not all(
            (
                type(raw_account_id) is str,
                type(identity_key) is bytes,
                type(identity_key_id) is str,
                type(confirmation) is str,
                callable(clock),
                callable(monotonic_ns),
                callable(wait_ns),
            )
        ):
            _fail(CL7RuntimeReason.TYPE_INVALID)
        observed = self.store.load(allow_missing_legacy=False)
        if observed.state is not RuntimeCashAuthorityState.LEGACY_ACTIVE:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        self._phrase(confirmation, self.PREPARE_PHRASE)
        account = derive_account_scope(
            raw_account_id,
            identity_key=identity_key,
            identity_key_id=identity_key_id,
        )
        with self.store.locked():
            current = self.store._load_unlocked(allow_missing_legacy=False)
            if current.canonical_bytes != observed.canonical_bytes:
                _fail(CL7RuntimeReason.CAS_CONFLICT)
            if current.cutover_generation >= _INT64_MAX:
                _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
            current = self.store._commit_unlocked(
                self._change(
                    current,
                    at=_timestamp(clock()),
                    kind="PREPARE_CUTOVER",
                    state=RuntimeCashAuthorityState.CUTOVER_PREPARED,
                    cutover_generation=current.cutover_generation + 1,
                    account_scope_sha256=account,
                    identity_key_id=identity_key_id,
                    activation_context_sha256=None,
                    ledger_head_sha256=None,
                    ledger_revision=None,
                    opening_cutoff=None,
                    opening_record_sha256=None,
                    operations_complete_through=None,
                    pending_dispatch_proof_sha256=None,
                ),
                expected_revision=current.record_revision,
                expected_sha256=current.sha256,
            )
            try:
                opening_response = self.read_accounting_cash(
                provider, raw_account_id, clock=clock, monotonic_ns=monotonic_ns,
            )
                opening_as_of = _timestamp(clock())
            except CL7RuntimeError:
                raise
            except Exception:
                _fail(
                    CL7RuntimeReason.BROKER_READ_FAILED,
                    stage="OPENING_CASH_READ",
                    retryable=True,
                )
            acceptance, _proof = self.ensure_opening_locked(
                current,
                ledger_store=ledger_store,
                raw_account_id=raw_account_id,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
                portfolio_response=opening_response,
                as_of=opening_as_of,
                evaluated_at=_timestamp(clock()),
            )
            snapshot = ledger_store.snapshot()
            current = self.store._commit_unlocked(
                self._change(
                    current,
                    at=_timestamp(clock()),
                    kind="PREPARATION_EVIDENCE_BOUND",
                    state=current.state,
                    ledger_revision=snapshot.ledger_revision,
                    ledger_head_sha256=snapshot.ledger_head_sha256,
                    opening_cutoff=acceptance.record.cutoff,
                    opening_record_sha256=acceptance.record.sha256,
                    operations_complete_through=_successor_timestamp(
                        acceptance.record.cutoff
                    ),
                ),
                expected_revision=current.record_revision,
                expected_sha256=current.sha256,
            )
            current, _batch, evidence = self._sync_and_rebuild_locked(
                current,
                ledger_store=ledger_store,
                portfolio_repository=portfolio_repository,
                risk_profile_store=risk_profile_store,
                risk_state_store=risk_state_store,
                central_manager=central_manager,
                provider=provider,
                raw_account_id=raw_account_id,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
                clock=clock,
                monotonic_ns=monotonic_ns,
                wait_ns=wait_ns,
                commit_sync=True,
                require_ready=False,
            )
            return current, evidence

    def confirm_runtime(self, **inputs: Any) -> RuntimeCashAuthorityRecord:
        """Rebuild quiescence under all locks and commit CONFIRMED."""

        confirmation = inputs.pop("confirmation", None)
        clock = inputs.get("clock")
        observed = self.store.load(allow_missing_legacy=False)
        if observed.state is not RuntimeCashAuthorityState.CUTOVER_PREPARED:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        self._phrase(confirmation, self.CONFIRM_PHRASE)
        self._account(
            observed,
            inputs.get("raw_account_id"),
            inputs.get("identity_key"),
            inputs.get("identity_key_id"),
        )
        with self.store.locked():
            current = self.store._load_unlocked(allow_missing_legacy=False)
            if current.canonical_bytes != observed.canonical_bytes:
                _fail(CL7RuntimeReason.CAS_CONFLICT)

            def finalize(
                synced: RuntimeCashAuthorityRecord,
                _batch: Any,
                evidence: _RuntimeEvidenceSet,
                central: Any,
            ) -> RuntimeCashAuthorityRecord:
                self.require_quiescent(
                    current=synced,
                    central_state=central,
                    runtime_dir=inputs["runtime_dir"],
                    evidence=evidence,
                )
                candidate = self._change(
                    synced,
                    at=_timestamp(clock()),
                    kind="CONFIRM_CUTOVER",
                    state=RuntimeCashAuthorityState.CUTOVER_CONFIRMED,
                )
                return self.store._commit_unlocked(
                    candidate,
                    expected_revision=synced.record_revision,
                    expected_sha256=synced.sha256,
                )

            _synced, _batch, result = self._sync_and_rebuild_locked(
                current,
                finalizer=finalize,
                commit_sync=True,
                require_ready=True,
                **{key: value for key, value in inputs.items() if key != "runtime_dir"},
            )
            return result

    def activate_runtime(self, **inputs: Any) -> RuntimeCashAuthorityRecord:
        """Perform the fresh evidence run and atomically switch to exact/disarmed."""

        confirmation = inputs.pop("confirmation", None)
        clock = inputs.get("clock")
        observed = self.store.load(allow_missing_legacy=False)
        if observed.state is not RuntimeCashAuthorityState.CUTOVER_CONFIRMED:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        self._phrase(confirmation, self.ACTIVATE_PHRASE)
        self._account(
            observed,
            inputs.get("raw_account_id"),
            inputs.get("identity_key"),
            inputs.get("identity_key_id"),
        )
        with self.store.locked():
            current = self.store._load_unlocked(allow_missing_legacy=False)
            if current.canonical_bytes != observed.canonical_bytes:
                _fail(CL7RuntimeReason.CAS_CONFLICT)

            def finalize(
                synced: RuntimeCashAuthorityRecord,
                batch: Any,
                evidence: _RuntimeEvidenceSet,
                central: Any,
            ) -> RuntimeCashAuthorityRecord:
                self.require_quiescent(
                    current=synced,
                    central_state=central,
                    runtime_dir=inputs["runtime_dir"],
                    evidence=evidence,
                )
                snapshot = inputs["ledger_store"].snapshot()
                candidate = self._change(
                    synced,
                    at=_timestamp(clock()),
                    kind="ACTIVATE_EXACT",
                    state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
                    ever_exact_activated=True,
                    activation_context_sha256=evidence.context.sha256,
                    ledger_revision=snapshot.ledger_revision,
                    ledger_head_sha256=snapshot.ledger_head_sha256,
                    operations_complete_through=(batch.watermark.to_exclusive),
                )
                return self.store._commit_unlocked(
                    candidate,
                    expected_revision=synced.record_revision,
                    expected_sha256=synced.sha256,
                )

            # The activation sync cannot emit SYNC_ADVANCED from CONFIRMED;
            # its watermark is committed only by ACTIVATE_EXACT.
            _synced, _batch, result = self._sync_and_rebuild_locked(
                current,
                finalizer=finalize,
                commit_sync=False,
                require_ready=True,
                **{key: value for key, value in inputs.items() if key != "runtime_dir"},
            )
            return result

    def sync_runtime(self, **inputs: Any) -> tuple[RuntimeCashAuthorityRecord, Any]:
        """Synchronize CL3 -> CL2 and rebuild a current READY context."""

        observed = self.store.load(allow_missing_legacy=False)
        if observed.state not in {
            RuntimeCashAuthorityState.CUTOVER_PREPARED,
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
            RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        }:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        self._account(
            observed,
            inputs.get("raw_account_id"),
            inputs.get("identity_key"),
            inputs.get("identity_key_id"),
        )
        with self.store.locked():
            current = self.store._load_unlocked(allow_missing_legacy=False)
            if current.canonical_bytes != observed.canonical_bytes:
                _fail(CL7RuntimeReason.CAS_CONFLICT)
            current, _batch, evidence = self._sync_and_rebuild_locked(
                current,
                commit_sync=True,
                require_ready=True,
                **inputs,
            )
            return current, evidence

    def rollback_runtime(self, **inputs: Any) -> RuntimeCashAuthorityRecord:
        """Revalidate all evidence and atomically return ownership to legacy."""

        confirmation = inputs.pop("confirmation", None)
        runtime_dir = inputs.pop("runtime_dir", None)
        clock = inputs.get("clock")
        observed = self.store.load(allow_missing_legacy=False)
        if observed.post_attempt_count != 0:
            _fail(CL7RuntimeReason.ROLLBACK_FORBIDDEN_AFTER_ATTEMPT)
        if observed.pending_dispatch_proof_sha256 is not None:
            _fail(CL7RuntimeReason.DISPATCH_PENDING)
        if observed.state is not RuntimeCashAuthorityState.EXACT_CASH_DISARMED:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        self._phrase(confirmation, self.ROLLBACK_PHRASE)
        self._account(
            observed,
            inputs.get("raw_account_id"),
            inputs.get("identity_key"),
            inputs.get("identity_key_id"),
        )
        with self.store.locked():
            current = self.store._load_unlocked(allow_missing_legacy=False)
            if current.canonical_bytes != observed.canonical_bytes:
                _fail(CL7RuntimeReason.CAS_CONFLICT)

            def finalize(
                synced: RuntimeCashAuthorityRecord,
                _batch: Any,
                evidence: _RuntimeEvidenceSet,
                central: Any,
            ) -> RuntimeCashAuthorityRecord:
                if synced.post_attempt_count != 0:
                    _fail(CL7RuntimeReason.ROLLBACK_FORBIDDEN_AFTER_ATTEMPT)
                self.require_quiescent(
                    current=synced,
                    central_state=central,
                    runtime_dir=runtime_dir,
                    evidence=evidence,
                )
                candidate = self._change(
                    synced,
                    at=_timestamp(clock()),
                    kind="ROLLBACK_TO_LEGACY",
                    state=RuntimeCashAuthorityState.LEGACY_ACTIVE,
                )
                return self.store._commit_unlocked(
                    candidate,
                    expected_revision=synced.record_revision,
                    expected_sha256=synced.sha256,
                )

            _synced, _batch, result = self._sync_and_rebuild_locked(
                current,
                finalizer=finalize,
                commit_sync=True,
                require_ready=True,
                **inputs,
            )
            return result

    def recover_runtime(
        self,
        *,
        central_manager: Any,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
        transition_at: str,
    ) -> tuple[RuntimeCashAuthorityRecord, str]:
        """Close only a fully proven D3 or process-boundary pending state."""

        with self.store.locked():
            current = self.store._load_unlocked(allow_missing_legacy=False)
            return self._recover_runtime_locked(
                current,
                central_manager=central_manager,
                raw_account_id=raw_account_id,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
                transition_at=transition_at,
            )

    def _recovery_intent_from_state(
        self,
        current: RuntimeCashAuthorityRecord,
        central: Any,
        *,
        identity_key: bytes,
        allow_absent_d3: bool = False,
    ) -> Any | None:
        matches: list[tuple[Any, LockedDispatchProof]] = []
        for item in central.intents:
            proof_raw = item.cl7_locked_dispatch_proof
            if proof_raw is None:
                continue
            proof = LockedDispatchProof.from_canonical_dict(proof_raw)
            pending_match = (
                current.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
                and proof.sha256 == current.pending_dispatch_proof_sha256
                and proof.authority_record_revision + 1 == current.record_revision
                and proof.authority_record_sha256 == current.previous_record_sha256
            )
            d3_match = (
                current.state is RuntimeCashAuthorityState.EXACT_CASH_ARMED
                and item.status == "IN_FLIGHT"
                and proof.authority_record_revision == current.record_revision
                and proof.authority_record_sha256 == current.sha256
                and central.revision == proof.central_order_revision + 1
            )
            if pending_match or d3_match:
                proof.verify_identity(
                    raw_intent_id=item.intent_id,
                    identity_key=identity_key,
                )
                if (
                    proof.account_scope_sha256 != current.account_scope_sha256
                    or proof.identity_key_id != current.identity_key_id
                    or proof.ledger_revision != current.ledger_revision
                    or proof.ledger_head_sha256 != current.ledger_head_sha256
                    or central.revision < proof.central_order_revision + 1
                ):
                    _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
                matches.append((item, proof))
        if (
            not matches
            and allow_absent_d3
            and (current.state is RuntimeCashAuthorityState.EXACT_CASH_ARMED)
        ):
            return None
        if len(matches) != 1:
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        return matches[0][0]

    def _recovery_intent_locked(
        self,
        current: RuntimeCashAuthorityRecord,
        *,
        central_manager: Any,
        identity_key: bytes,
        allow_absent_d3: bool = False,
    ) -> Any | None:
        try:
            return central_manager.inspect_locked(
                lambda central: self._recovery_intent_from_state(
                    current,
                    central,
                    identity_key=identity_key,
                    allow_absent_d3=allow_absent_d3,
                )
            )
        except CL7RuntimeError:
            raise
        except Exception:
            _fail(CL7RuntimeReason.RECOVERY_REQUIRED, stage="CENTRAL_CUSTODY")

    def _recover_runtime_locked(
        self,
        current: RuntimeCashAuthorityRecord,
        *,
        central_manager: Any,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
        transition_at: str,
    ) -> tuple[RuntimeCashAuthorityRecord, str]:
        self._account(
            current,
            raw_account_id,
            identity_key,
            identity_key_id,
        )
        if current.state is RuntimeCashAuthorityState.EXACT_CASH_ARMED:
            intent = self._recovery_intent_locked(
                current,
                central_manager=central_manager,
                identity_key=identity_key,
            )
            proof = LockedDispatchProof.from_canonical_dict(
                intent.cl7_locked_dispatch_proof
            )
            try:
                central_manager.resolve_cl7_pre_submit(
                    expected_intent_id=intent.intent_id,
                    expected_proof_sha256=proof.sha256,
                    authority_record_revision=current.record_revision,
                    authority_record_sha256=current.sha256,
                    account_scope_sha256=current.account_scope_sha256,
                    identity_key_id=current.identity_key_id,
                    identity_key=identity_key,
                    ledger_revision=current.ledger_revision,
                    ledger_head_sha256=current.ledger_head_sha256,
                )
            except CL7RuntimeError:
                raise
            except Exception:
                _fail(CL7RuntimeReason.RECOVERY_REQUIRED, stage="D3")
            return current, "D3_PRE_SUBMIT_FAILED"
        if current.state is not RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)

        def resolve(central: Any) -> RuntimeCashAuthorityRecord:
            intent = self._recovery_intent_from_state(
                current,
                central,
                identity_key=identity_key,
            )
            # Canonical position/Risk reconciliation does not attest to CL2
            # debit/credit/fee completeness. The generic clear API accepts no
            # settlement evidence and therefore cannot close any executed path.
            if intent.status == "RECONCILED":
                _fail(CL7RuntimeReason.RECOVERY_REQUIRED,
                      "EXACT_SETTLEMENT_REQUIRED", stage="POST_FILL")
            fully_resolved = (
                intent.status == "FAILED" and intent.outcome == "SUBMISSION_REJECTED"
            )
            if not fully_resolved:
                _fail(CL7RuntimeReason.RECOVERY_REQUIRED)
            candidate = self._change(
                current,
                at=_timestamp(transition_at),
                kind="RECOVERY_CLOSED_DISARMED",
                state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
                pending_dispatch_proof_sha256=None,
            )
            return self.store._commit_unlocked(
                candidate,
                expected_revision=current.record_revision,
                expected_sha256=current.sha256,
            )

        try:
            result = central_manager.inspect_locked(resolve)
        except CL7RuntimeError:
            raise
        except Exception:
            _fail(CL7RuntimeReason.RECOVERY_REQUIRED, stage="CENTRAL_CUSTODY")
        return result, "RECOVERY_CLOSED_DISARMED"

    @staticmethod
    def _phrase(value: object, expected: str) -> None:
        if type(value) is not str or value.strip() != expected:
            _fail(CL7RuntimeReason.CONFIRMATION_INVALID)

    def _change(
        self,
        current: RuntimeCashAuthorityRecord,
        *,
        at: str,
        kind: str,
        state: RuntimeCashAuthorityState,
        **changes: object,
    ) -> RuntimeCashAuthorityRecord:
        if current.record_revision >= _INT64_MAX:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        return replace(
            current,
            record_revision=current.record_revision + 1,
            previous_record_sha256=current.sha256,
            transition_at=_timestamp(at),
            transition_kind=kind,
            state=state,
            **changes,
        )

    def _locked_change(self, builder: Any) -> RuntimeCashAuthorityRecord:
        with self.store.locked():
            current = self.store._load_unlocked(allow_missing_legacy=False)
            candidate = builder(current)
            return self.store._commit_unlocked(
                candidate,
                expected_revision=current.record_revision,
                expected_sha256=current.sha256,
            )

    def _commit_after_precheck(
        self,
        observed: RuntimeCashAuthorityRecord,
        builder: Any,
    ) -> RuntimeCashAuthorityRecord:
        with self.store.locked():
            current = self.store._load_unlocked(allow_missing_legacy=False)
            if current.canonical_bytes != observed.canonical_bytes:
                _fail(CL7RuntimeReason.CAS_CONFLICT)
            candidate = builder(current)
            return self.store._commit_unlocked(
                candidate,
                expected_revision=current.record_revision,
                expected_sha256=current.sha256,
            )

    def _account(
        self,
        current: RuntimeCashAuthorityRecord,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
    ) -> None:
        account = derive_account_scope(
            raw_account_id, identity_key=identity_key, identity_key_id=identity_key_id
        )
        if (
            current.account_scope_sha256 != account
            or current.identity_key_id != identity_key_id
        ):
            _fail(CL7RuntimeReason.ACCOUNT_SCOPE_INVALID)

    def arm(
        self,
        *,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
        confirmation: str,
        transition_at: str,
    ) -> RuntimeCashAuthorityRecord:
        observed = self.store.load(allow_missing_legacy=False)
        if observed.state is not RuntimeCashAuthorityState.EXACT_CASH_DISARMED:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        if observed.pending_dispatch_proof_sha256 is not None:
            _fail(CL7RuntimeReason.DISPATCH_PENDING)
        self._phrase(confirmation, self.ARM_PHRASE)
        self._account(observed, raw_account_id, identity_key, identity_key_id)

        def build(current: RuntimeCashAuthorityRecord) -> RuntimeCashAuthorityRecord:
            if (
                current.state is not RuntimeCashAuthorityState.EXACT_CASH_DISARMED
                or current.pending_dispatch_proof_sha256 is not None
            ):
                _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
            self._account(current, raw_account_id, identity_key, identity_key_id)
            return self._change(
                current,
                at=transition_at,
                kind="ARM_EXACT",
                state=RuntimeCashAuthorityState.EXACT_CASH_ARMED,
            )

        return self._commit_after_precheck(observed, build)

    def disarm(self, *, transition_at: str) -> RuntimeCashAuthorityRecord:
        return self._locked_change(
            lambda current: (
                self._change(
                    current,
                    at=transition_at,
                    kind="DISARM_EXACT",
                    state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
                )
                if current.state is RuntimeCashAuthorityState.EXACT_CASH_ARMED
                else (
                    _fail(CL7RuntimeReason.DISPATCH_PENDING)
                    if current.state
                    is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
                    else _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
                )
            )
        )

    def cancel(self, *, transition_at: str) -> RuntimeCashAuthorityRecord:
        def build(current: RuntimeCashAuthorityRecord) -> RuntimeCashAuthorityRecord:
            if current.state not in {
                RuntimeCashAuthorityState.CUTOVER_PREPARED,
                RuntimeCashAuthorityState.CUTOVER_CONFIRMED,
            }:
                _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
            return self._change(
                current,
                at=transition_at,
                kind="CANCEL_CUTOVER",
                state=RuntimeCashAuthorityState.LEGACY_ACTIVE,
            )

        return self._locked_change(build)

    def synchronize_operations(
        self,
        *,
        ledger_store: Any,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
        sync_to_exclusive: str,
        transport: Any,
        monotonic_ns: Any,
        wait_ns: Any,
        absolute_deadline_ns: int,
        retry_policy: Any,
        transition_at: str,
        commit_authority: bool = True,
    ) -> tuple[RuntimeCashAuthorityRecord, Any]:
        """Run one bounded contiguous CL3 -> CL2 synchronization interval."""

        with self.store.locked():
            current = self.store._load_unlocked(allow_missing_legacy=False)
            return self.synchronize_operations_locked(
                current,
                ledger_store=ledger_store,
                raw_account_id=raw_account_id,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
                sync_to_exclusive=sync_to_exclusive,
                transport=transport,
                monotonic_ns=monotonic_ns,
                wait_ns=wait_ns,
                absolute_deadline_ns=absolute_deadline_ns,
                retry_policy=retry_policy,
                transition_at=transition_at,
                commit_authority=commit_authority,
            )

    def synchronize_operations_locked(
        self,
        current: RuntimeCashAuthorityRecord,
        *,
        ledger_store: Any,
        raw_account_id: str,
        identity_key: bytes,
        identity_key_id: str,
        sync_to_exclusive: str,
        transport: Any,
        monotonic_ns: Any,
        wait_ns: Any,
        absolute_deadline_ns: int,
        retry_policy: Any,
        transition_at: str,
        commit_authority: bool = True,
    ) -> tuple[RuntimeCashAuthorityRecord, Any]:
        """Locked form used by prepare, activation, and exact dispatch.

        The authority lock must already be held.  Each CL2 append remains its
        own accepted SQLite transaction; the durable watermark changes only
        after every decision in the complete CL3 batch has converged.
        """

        from . import broker_read_adapters as broker
        from . import cash_ledger_persistence as persistence

        if (
            type(current) is not RuntimeCashAuthorityRecord
            or type(commit_authority) is not bool
        ):
            _fail(CL7RuntimeReason.TYPE_INVALID)
        if current.state not in {
            RuntimeCashAuthorityState.CUTOVER_PREPARED,
            RuntimeCashAuthorityState.CUTOVER_CONFIRMED,
            RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
            RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        }:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        if current.pending_dispatch_proof_sha256 is not None:
            _fail(CL7RuntimeReason.DISPATCH_PENDING)
        self._account(current, raw_account_id, identity_key, identity_key_id)
        if current.operations_complete_through is None:
            _fail(CL7RuntimeReason.OPENING_INVALID)
        if (
            not callable(transport)
            or not callable(monotonic_ns)
            or not callable(wait_ns)
        ):
            _fail(CL7RuntimeReason.TYPE_INVALID)
        try:
            request = broker.BrokerReadRequest(
                environment=broker.BrokerEnvironment.SANDBOX,
                raw_account_id=raw_account_id,
                identity_key=identity_key,
                identity_key_id=identity_key_id,
                from_inclusive=current.operations_complete_through,
                to_exclusive=_timestamp(sync_to_exclusive),
                limit=1000,
                max_pages=100,
                max_items=100_000,
                absolute_deadline_ns=absolute_deadline_ns,
                retry_policy=retry_policy,
                transport=transport,
                monotonic_ns=monotonic_ns,
                wait_ns=wait_ns,
            )
            batch = broker.collect_tbank_operations(request)
        except broker.BrokerReadError as exc:
            _fail(
                CL7RuntimeReason.BROKER_READ_FAILED,
                exc.reason.value,
                stage="CL3_SYNC",
                retryable=True,
            )
        except CL7RuntimeError:
            raise
        except Exception:
            _fail(CL7RuntimeReason.INTERNAL_BOUNDARY_FAILED, stage="CL3_SYNC")
        if (
            batch.watermark.account_scope_sha256 != current.account_scope_sha256
            or batch.watermark.from_inclusive != current.operations_complete_through
            or batch.watermark.to_exclusive != sync_to_exclusive
        ):
            _fail(CL7RuntimeReason.LEDGER_SYNC_INCOMPLETE, stage="WATERMARK")
        try:
            for decision in batch.decisions:
                snapshot = ledger_store.snapshot()
                ledger_store.append_observation(
                    decision.observation,
                    expected_store_revision=snapshot.store_revision,
                )
                snapshot = ledger_store.snapshot()
                if decision.kind is broker.BrokerDecisionKind.TRANSACTION_PROPOSED:
                    if decision.transaction_proposal is None:
                        _fail(CL7RuntimeReason.LEDGER_SYNC_INCOMPLETE)
                    ledger_store.append_transaction(
                        decision.transaction_proposal,
                        decision.observation.sha256,
                        expected_store_revision=snapshot.store_revision,
                        expected_ledger_revision=snapshot.ledger_revision,
                    )
                else:
                    review = decision.kind is broker.BrokerDecisionKind.REVIEW_REQUIRED
                    event = persistence.InboxStatusEvent(
                        observation_sha256=decision.observation.sha256,
                        event_no=1,
                        from_status="OBSERVED",
                        to_status="REVIEW_REQUIRED" if review else "REJECTED",
                        reason="REVIEW_REQUIRED" if review else "NOT_LEDGER_RELEVANT",
                    )
                    try:
                        ledger_store.append_status_event(
                            event,
                            expected_store_revision=snapshot.store_revision,
                        )
                    except persistence.PersistenceError as exc:
                        # A replay may find the observation already advanced by
                        # the identical event.  Exact event identity is checked
                        # by the accepted CL2 idempotency path before returning.
                        if (
                            exc.reason
                            is not persistence.PersistenceReason.STATUS_TRANSITION_INVALID
                        ):
                            raise
                        ledger_store.append_status_event(
                            event,
                            expected_store_revision=snapshot.store_revision,
                        )
            snapshot = ledger_store.snapshot()
        except CL7RuntimeError:
            raise
        except persistence.PersistenceError as exc:
            _fail(
                CL7RuntimeReason.LEDGER_SYNC_INCOMPLETE,
                exc.reason.value,
                stage="CL2_APPEND",
                retryable=exc.reason
                in {
                    persistence.PersistenceReason.REVISION_MISMATCH,
                    persistence.PersistenceReason.STORE_BUSY,
                    persistence.PersistenceReason.INTERRUPTED_TRANSACTION,
                },
            )
        except Exception:
            _fail(CL7RuntimeReason.INTERNAL_BOUNDARY_FAILED, stage="CL2_APPEND")
        if (
            current.ledger_revision == snapshot.ledger_revision
            and current.ledger_head_sha256 == snapshot.ledger_head_sha256
            and current.operations_complete_through == batch.watermark.to_exclusive
        ):
            return current, batch
        if not commit_authority:
            return current, batch
        candidate = self._change(
            current,
            at=transition_at,
            kind="SYNC_ADVANCED",
            state=current.state,
            ledger_revision=snapshot.ledger_revision,
            ledger_head_sha256=snapshot.ledger_head_sha256,
            operations_complete_through=batch.watermark.to_exclusive,
        )
        committed = self.store._commit_unlocked(
            candidate,
            expected_revision=current.record_revision,
            expected_sha256=current.sha256,
        )
        return committed, batch

    def _record_dispatch_attempt_locked(
        self,
        current: RuntimeCashAuthorityRecord,
        proof: LockedDispatchProof,
        *,
        transition_at: str,
    ) -> RuntimeCashAuthorityRecord:
        """Persist step V while the caller holds the outer authority lock."""

        if current.state is not RuntimeCashAuthorityState.EXACT_CASH_ARMED:
            _fail(CL7RuntimeReason.DISPATCH_NOT_ARMED)
        if (
            proof.authority_record_revision != current.record_revision
            or proof.authority_record_sha256 != current.sha256
            or proof.account_scope_sha256 != current.account_scope_sha256
            or proof.ledger_revision != current.ledger_revision
            or proof.ledger_head_sha256 != current.ledger_head_sha256
        ):
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        if current.post_attempt_count >= _INT64_MAX:
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        candidate = self._change(
            current,
            at=transition_at,
            kind="DISPATCH_ATTEMPT_RECORDED",
            state=RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING,
            post_attempt_count=current.post_attempt_count + 1,
            pending_dispatch_proof_sha256=proof.sha256,
        )
        try:
            return self.store._commit_unlocked(
                candidate,
                expected_revision=current.record_revision,
                expected_sha256=current.sha256,
            )
        except Exception:
            # A normal returned write failure may prove zero POST only when the
            # old ARMED record is still the exact active tuple.  If the marker
            # reached durable custody, the outcome is D4 and recovery is
            # mandatory even though transport has not yet been invoked here.
            try:
                readback = self.store._load_unlocked(allow_missing_legacy=False)
            except Exception:
                _fail(CL7RuntimeReason.RECOVERY_REQUIRED, stage="ATTEMPT_RECORD")
            if (
                readback.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
                and readback.pending_dispatch_proof_sha256 == proof.sha256
            ):
                _fail(CL7RuntimeReason.RECOVERY_REQUIRED, stage="D4")
            if readback.canonical_bytes == current.canonical_bytes:
                _fail(CL7RuntimeReason.ATTEMPT_RECORD_FAILED, stage="PRE_POST")
            _fail(CL7RuntimeReason.RECOVERY_REQUIRED, stage="ATTEMPT_RECORD")

    def _clear_dispatch_locked(
        self,
        current: RuntimeCashAuthorityRecord,
        *,
        proof: LockedDispatchProof,
        central_intent: Any,
        transition_at: str,
    ) -> RuntimeCashAuthorityRecord:
        """Clear a fully resolved attempt in the uninterrupted dispatch process."""

        if type(proof) is not LockedDispatchProof:
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        if (
            getattr(central_intent, "cl7_locked_dispatch_proof_sha256", None)
            != proof.sha256
        ):
            _fail(CL7RuntimeReason.DISPATCH_PROOF_INVALID)
        if (
            getattr(central_intent, "status", None) == "FAILED"
            and getattr(central_intent, "outcome", None) == "SUBMISSION_REJECTED"
        ):
            kind = "DISPATCH_REJECTED_REARMED"
        elif getattr(central_intent, "status", None) == "RECONCILED":
            _fail(CL7RuntimeReason.RECOVERY_REQUIRED,
                  "EXACT_SETTLEMENT_REQUIRED", stage="POST_FILL")
        else:
            kind = None
        if (
            kind is None
            or current.state
            is not RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
            or current.pending_dispatch_proof_sha256 != proof.sha256
        ):
            _fail(CL7RuntimeReason.STATE_TRANSITION_INVALID)
        candidate = self._change(
            current,
            at=transition_at,
            kind=kind,
            state=RuntimeCashAuthorityState.EXACT_CASH_ARMED,
            pending_dispatch_proof_sha256=None,
        )
        return self.store._commit_unlocked(
            candidate,
            expected_revision=current.record_revision,
            expected_sha256=current.sha256,
        )

    @staticmethod
    def build_locked_dispatch_proof(
        *,
        context: Any,
        authority_record: RuntimeCashAuthorityRecord,
        raw_intent_id: str,
        identity_key: bytes,
        reserved_cash: Money,
        current_lots: int,
        target_lots: int,
        direction: str,
        evaluated_at: str,
        own_funds_evidence: OwnFundsEvidence | None = None,
    ) -> LockedDispatchProof:
        context_status = getattr(context, "status", None)
        if getattr(context_status, "value", None) != "READY_FOR_LOCKED_REVALIDATION":
            _fail(CL7RuntimeReason.CONTEXT_BLOCKED)
        return LockedDispatchProof.build(
            raw_intent_id=raw_intent_id,
            identity_key=identity_key,
            account_scope_sha256=context.account_scope_sha256,
            authority_record_revision=authority_record.record_revision,
            authority_record_sha256=authority_record.sha256,
            availability_sha256=context.availability_sha256,
            central_order_revision=context.central_order_revision,
            central_reservation_projection_hash=context.reservation_projection_hash,
            cl6_context_identity_sha256=context.context_identity_sha256,
            cl6_context_sha256=context.sha256,
            current_lots=current_lots,
            direction=direction,
            evaluated_at=evaluated_at,
            free_investable_cash=(
                Money("RUB", min(context.free_investable_cash.minor_units, own_funds_evidence.free_after_reservations_nano))
                if own_funds_evidence is not None and direction == "BUY" else context.free_investable_cash
            ),
            identity_key_id=context.identity_key_id,
            ledger_head_sha256=context.ledger_head_sha256,
            ledger_revision=context.ledger_revision,
            portfolio_decision_checksum=context.portfolio_decision_checksum,
            portfolio_document_checksum=context.portfolio_document_checksum,
            portfolio_revision=context.portfolio_revision,
            reconciliation_sha256=context.reconciliation_sha256,
            reserved_cash=reserved_cash,
            risk_policy_hash=context.risk_policy_hash,
            risk_state_guard_hash=context.risk_state_guard_hash,
            target_lots=target_lots,
            version=2 if own_funds_evidence is not None else 1,
            own_funds_evidence=own_funds_evidence,
        )


@contextmanager
def legacy_execution_guard(
    store: RuntimeCashAuthorityStore,
) -> Iterator[RuntimeCashAuthorityRecord]:
    """Hold the outer authority lock across one legacy economic mutation."""

    if type(store) is not RuntimeCashAuthorityStore:
        _fail(CL7RuntimeReason.TYPE_INVALID)
    with store.locked():
        record = store._load_unlocked(allow_missing_legacy=True)
        if record.state is not RuntimeCashAuthorityState.LEGACY_ACTIVE:
            _fail(CL7RuntimeReason.DISPATCH_NOT_ARMED)
        yield record


__all__ = (  # noqa: RUF022 - public order is frozen by the CL7 contract
    "RuntimeCashAuthorityState",
    "RuntimeCashAuthorityOwner",
    "CL7RuntimeReason",
    "CL7RuntimeError",
    "RuntimeCashAuthorityRecord",
    "LockedDispatchProof",
    "RuntimeCashAuthorityStore",
    "RuntimeCashAuthorityManager",
    "legacy_execution_guard",
)
