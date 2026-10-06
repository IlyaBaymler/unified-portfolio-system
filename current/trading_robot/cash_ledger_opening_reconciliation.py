"""Bounded FROM_NOW opening proof and shadow cash reconciliation for CL4."""

from __future__ import annotations

import hashlib as _hashlib
import hmac as _hmac
import json as _json
import re as _re
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from datetime import date as _date
from enum import StrEnum as _StrEnum
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

from trading_robot import broker_read_adapters as _broker
from trading_robot import cash_ledger_domain as _ledger
from trading_robot import cash_ledger_persistence as _persistence

if _TYPE_CHECKING:
    from trading_robot.broker_read_adapters import BrokerEnvironment
    from trading_robot.cash_ledger_persistence import CashLedgerStore

del annotations

__all__ = (  # noqa: RUF022 - frozen contract order
    "OpeningMode",
    "ReconciliationStatus",
    "DiscrepancyKind",
    "AdoptionDisposition",
    "CL4Reason",
    "CL4Error",
    "BrokerCashProof",
    "OpeningPlan",
    "OpeningRecord",
    "LedgerCashProjection",
    "CashReconciliation",
    "AdoptionCandidate",
    "OpeningAcceptance",
    "CL4_OPENING_CODEC",
    "CL4_RUB_POSITION_OPENING_CODEC",
    "build_broker_rub_position_cash_proof",
    "build_broker_cash_proof",
    "prepare_from_now_opening",
    "accept_from_now_opening",
    "project_shadow_cash",
    "reconcile_shadow_cash",
    "build_adoption_candidate",
)


_CL4_CONTRACT_VERSION = 2
_BROKER_CASH_PROOF_VERSION = 2
_CASH_VERSIONS = (2, 3)
_RUB_POSITION_RPC = "tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxPositions"
_OPENING_PLAN_VERSION = 2
_OPENING_RECORD_VERSION = 2
_LEDGER_CASH_PROJECTION_VERSION = 2
_CASH_RECONCILIATION_VERSION = 2
_ADOPTION_CANDIDATE_VERSION = 2
_MAX_PROOF_AGE_NS = 120_000_000_000
_MAX_RESPONSE_DEPTH = 16
_MAX_RESPONSE_NODES = 100_000
_MAX_RESPONSE_CANONICAL_BYTES = 1_048_576
_MAX_MAPPING_KEYS = 4_096
_MAX_STRING_SCALARS = 4_096
_MAX_KEY_SCALARS = 128
_MAX_LEDGER_EXPORT_BYTES = 16_777_216
_MAX_BASELINE_WITNESS_BYTES = 16_777_216
_MAX_LEDGER_OBJECTS = 100_000
_MAX_REVISION = 9_223_372_036_854_775_807
_OPENING_STORE_REVISION_RESERVE = 2
_OPENING_LEDGER_REVISION_RESERVE = 1
_RECONCILIATION_DELTA_MAX_ABS_MINOR_UNITS = 18_446_744_073_709_551_616_999_999_998
_RPC = "tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio"
_HASH_RE = _re.compile(r"[0-9a-f]{64}", _re.ASCII)
_TOKEN_RE = _re.compile(r"[A-Z][A-Z0-9_]{0,63}", _re.ASCII)
_DECIMAL_RE = _re.compile(r"0|-?[1-9][0-9]*", _re.ASCII)
_REVISION_RE = _re.compile(r"0|[1-9][0-9]*", _re.ASCII)
_TIMESTAMP_RE = _re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{9})Z",
    _re.ASCII,
)
_SAFE_EVIDENCE_KEYS = frozenset(
    {
        "reason",
        "cause_reason",
        "stage",
        "account_scope_sha256",
        "broker_cash_proof_sha256",
        "opening_plan_sha256",
        "baseline_export_sha256",
        "ledger_export_sha256",
        "ledger_head_sha256",
        "reconciliation_sha256",
    }
)
_EXPORT_KEYS = frozenset(
    {
        "codec_registry",
        "correction_bundles",
        "domain",
        "inbox_status_events",
        "ledger_head_json_ascii",
        "ledger_head_sha256",
        "ledger_revision",
        "ledger_transitions",
        "observations",
        "provenance_links",
        "schema_version",
        "store_revision",
        "transactions",
        "version",
    }
)
_CODEC_WRAPPER_KEYS = frozenset({"canonical_json_ascii", "sha256"})
_OBSERVATION_WRAPPER_KEYS = frozenset(
    {
        "canonical_json_ascii",
        "current_status",
        "logical_source_sha256",
        "sha256",
        "source_sha256",
    }
)
_EVENT_WRAPPER_KEYS = frozenset({"canonical_json_ascii", "sha256"})
_TRANSACTION_WRAPPER_KEYS = frozenset(
    {"canonical_json_ascii", "economic_sha256", "sha256", "source_sha256"}
)
_LINK_KEYS = frozenset({"observation_sha256", "transaction_sha256"})
_BUNDLE_WRAPPER_KEYS = frozenset({"canonical_json_ascii", "sha256"})
_TRANSITION_WRAPPER_KEYS = frozenset({"head_json_ascii", "head_sha256"})
_EXPORT_ARRAY_NAMES = (
    "codec_registry",
    "observations",
    "inbox_status_events",
    "transactions",
    "provenance_links",
    "correction_bundles",
    "ledger_transitions",
)
_CONTENT_KEYS = frozenset(
    {
        "account_scope_sha256",
        "broker_cash_proof_sha256",
        "cutoff",
        "environment",
        "generation",
        "identity_key_id",
        "baseline_export_sha256",
        "ledger_head_sha256",
        "ledger_revision",
        "mode",
        "opening_currency",
        "opening_minor_units",
        "proof_identity_sha256",
        "store_revision",
        "contract_version",
    }
)
_INCOMPLETE_PRECEDENCE = (
    "LATE_PRE_CUTOFF_LEDGER_EFFECT",
    "PROOF_BEFORE_LEDGER_EFFECT",
    "PROOF_BEFORE_LEDGER_EVIDENCE",
    "UNRESOLVED_OBSERVATION",
)


class OpeningMode(_StrEnum):
    FROM_NOW = "FROM_NOW"


class ReconciliationStatus(_StrEnum):
    MATCHED = "MATCHED"
    DISCREPANCY = "DISCREPANCY"
    INCOMPLETE = "INCOMPLETE"


class DiscrepancyKind(_StrEnum):
    LATE_PRE_CUTOFF_LEDGER_EFFECT = "LATE_PRE_CUTOFF_LEDGER_EFFECT"
    PROOF_BEFORE_LEDGER_EFFECT = "PROOF_BEFORE_LEDGER_EFFECT"
    PROOF_BEFORE_LEDGER_EVIDENCE = "PROOF_BEFORE_LEDGER_EVIDENCE"
    UNRESOLVED_OBSERVATION = "UNRESOLVED_OBSERVATION"
    BROKER_ABOVE_EXPECTED = "BROKER_ABOVE_EXPECTED"
    BROKER_BELOW_EXPECTED = "BROKER_BELOW_EXPECTED"
    NONE = "NONE"


class AdoptionDisposition(_StrEnum):
    SEPARATE_LOCKED_REVIEW_REQUIRED = "SEPARATE_LOCKED_REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


class CL4Reason(_StrEnum):
    TYPE_INVALID = "TYPE_INVALID"
    VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
    ENVIRONMENT_UNSUPPORTED = "ENVIRONMENT_UNSUPPORTED"
    MODE_UNSUPPORTED = "MODE_UNSUPPORTED"
    ACCOUNT_SCOPE_INVALID = "ACCOUNT_SCOPE_INVALID"
    IDENTITY_KEY_INVALID = "IDENTITY_KEY_INVALID"
    TIMESTAMP_INVALID = "TIMESTAMP_INVALID"
    PROOF_FROM_FUTURE = "PROOF_FROM_FUTURE"
    PROOF_STALE = "PROOF_STALE"
    PROOF_INCOMPLETE = "PROOF_INCOMPLETE"
    RESPONSE_BOUNDS_EXCEEDED = "RESPONSE_BOUNDS_EXCEEDED"
    RESPONSE_SCHEMA_INVALID = "RESPONSE_SCHEMA_INVALID"
    MONEY_INVALID = "MONEY_INVALID"
    PROOF_IDENTITY_INVALID = "PROOF_IDENTITY_INVALID"
    CASH_SOURCE_MISMATCH = "CASH_SOURCE_MISMATCH"
    POSITIONS_BLOCKED_UNSUPPORTED = "POSITIONS_BLOCKED_UNSUPPORTED"
    POSITIONS_LOADING = "POSITIONS_LOADING"
    LEDGER_EXPORT_INVALID = "LEDGER_EXPORT_INVALID"
    LEDGER_GRAPH_INVALID = "LEDGER_GRAPH_INVALID"
    LEDGER_REVISION_INVALID = "LEDGER_REVISION_INVALID"
    RECONCILIATION_INVALID = "RECONCILIATION_INVALID"
    INBOX_INCOMPLETE = "INBOX_INCOMPLETE"
    OPENING_AMOUNT_UNSUPPORTED = "OPENING_AMOUNT_UNSUPPORTED"
    OPENING_MISSING = "OPENING_MISSING"
    OPENING_CONFLICT = "OPENING_CONFLICT"
    BASELINE_STALE = "BASELINE_STALE"
    OPENING_CAPACITY_EXHAUSTED = "OPENING_CAPACITY_EXHAUSTED"
    OPENING_PLAN_STALE = "OPENING_PLAN_STALE"
    CONFIRMATION_INVALID = "CONFIRMATION_INVALID"
    PERSISTENCE_FAILURE = "PERSISTENCE_FAILURE"
    ARITHMETIC_OVERFLOW = "ARITHMETIC_OVERFLOW"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    INTERNAL_BOUNDARY_FAILED = "INTERNAL_BOUNDARY_FAILED"


class CL4Error(RuntimeError):
    """Closed CL4 failure carrying only finite, privacy-safe evidence."""

    def __init__(
        self,
        reason: CL4Reason,
        cause_reason: object | None = None,
        evidence: _Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(reason, CL4Reason):
            reason = CL4Reason.INTERNAL_BOUNDARY_FAILED
        allowed_causes = (
            _ledger.MoneyReason,
            _ledger.LedgerReason,
            _persistence.PersistenceReason,
            _broker.BrokerReadReason,
        )
        if cause_reason is not None and not isinstance(cause_reason, allowed_causes):
            cause_reason = None
        self.reason = reason
        self.cause_reason = cause_reason
        supplied = {} if evidence is None else dict(evidence)
        if any(key not in _SAFE_EVIDENCE_KEYS for key in supplied):
            supplied = {}
        safe: dict[str, str] = {"reason": reason.value}
        if cause_reason is not None:
            value = getattr(cause_reason, "value", None)
            if isinstance(value, str) and _TOKEN_RE.fullmatch(value):
                safe["cause_reason"] = value
        for key, value in supplied.items():
            if key in {"reason", "cause_reason"}:
                continue
            if not isinstance(value, str):
                continue
            if key == "stage":
                if _TOKEN_RE.fullmatch(value):
                    safe[key] = value
            elif _HASH_RE.fullmatch(value):
                safe[key] = value
        self.evidence = _MappingProxyType(safe)
        RuntimeError.__init__(self, reason.value)

    def __str__(self) -> str:
        return self.reason.value

    def __repr__(self) -> str:
        cause = getattr(self.cause_reason, "value", None)
        suffix = "" if cause is None else f", cause_reason={cause}"
        return f"CL4Error(reason={self.reason.value}{suffix})"


_BrokerEnvironment = _broker.BrokerEnvironment


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


def _hmac_sha256(key: bytes, value: object) -> str:
    return _hmac.new(key, _canonical_bytes(value), _hashlib.sha256).hexdigest()


def _fail(
    reason: CL4Reason,
    cause_reason: object | None = None,
    **evidence: str,
) -> None:
    raise CL4Error(reason, cause_reason, evidence) from None


def _is_hash(value: object) -> bool:
    return type(value) is str and _HASH_RE.fullmatch(value) is not None


def _require_hash(value: object, reason: CL4Reason) -> str:
    if not _is_hash(value):
        _fail(reason)
    return value


def _require_key(value: object) -> bytes:
    if type(value) is not bytes or not 32 <= len(value) <= 64:
        _fail(CL4Reason.IDENTITY_KEY_INVALID)
    return value


def _require_key_id(value: object) -> str:
    if type(value) is not str or _TOKEN_RE.fullmatch(value) is None:
        _fail(CL4Reason.IDENTITY_KEY_INVALID)
    return value


def _timestamp_ns(value: object) -> int:
    if type(value) is not str:
        _fail(CL4Reason.TIMESTAMP_INVALID)
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        _fail(CL4Reason.TIMESTAMP_INVALID)
    year, month, day, hour, minute, second, fraction = map(int, match.groups())
    try:
        ordinal = _date(year, month, day).toordinal()
    except ValueError:
        failure = CL4Error(CL4Reason.TIMESTAMP_INVALID)
    else:
        if hour > 23 or minute > 59 or second > 59:
            _fail(CL4Reason.TIMESTAMP_INVALID)
        seconds = (((ordinal * 24) + hour) * 60 + minute) * 60 + second
        return seconds * 1_000_000_000 + fraction
    raise failure from None


def _require_fresh(as_of: str, evaluated_at: str) -> None:
    as_of_ns = _timestamp_ns(as_of)
    evaluated_ns = _timestamp_ns(evaluated_at)
    if as_of_ns > evaluated_ns:
        _fail(CL4Reason.PROOF_FROM_FUTURE)
    if evaluated_ns - as_of_ns > _MAX_PROOF_AGE_NS:
        _fail(CL4Reason.PROOF_STALE)


def _parse_revision(value: object) -> int:
    if type(value) is not str or _REVISION_RE.fullmatch(value) is None:
        _fail(CL4Reason.LEDGER_REVISION_INVALID)
    if len(value) > 19 or (len(value) == 19 and value > str(_MAX_REVISION)):
        _fail(CL4Reason.LEDGER_REVISION_INVALID)
    return int(value)


def _money_dict(value: _ledger.Money) -> dict[str, object]:
    return value.to_canonical_dict()


def _checked_money(value: object) -> _ledger.Money:
    if type(value) is not _ledger.Money:
        _fail(CL4Reason.TYPE_INVALID)
    try:
        checked = _ledger.Money.from_canonical_dict(value.to_canonical_dict())
    except _ledger.MoneyError as error:
        failure = CL4Error(CL4Reason.MONEY_INVALID, error.reason)
    except Exception:  # noqa: BLE001 - closed public boundary
        failure = CL4Error(CL4Reason.MONEY_INVALID)
    else:
        if checked.canonical_bytes == value.canonical_bytes:
            return checked
        failure = CL4Error(CL4Reason.MONEY_INVALID)
    raise failure from None


def _proof_identity(
    *,
    account_scope_sha256: str,
    environment: _BrokerEnvironment,
    identity_key_id: str,
    response_canonical_sha256: str,
    cash: _ledger.Money,
    as_of: str,
    identity_key: bytes,
    version: int = 2,
) -> str:
    return _hmac_sha256(
        identity_key,
        {
            "account_scope_sha256": account_scope_sha256,
            "as_of": as_of,
            "cash": _money_dict(cash),
            "domain": "v3.10-cl4-broker-cash-proof-identity",
            "environment": environment.value,
            "identity_key_id": identity_key_id,
            "provider": "TBANK",
            "response_canonical_sha256": response_canonical_sha256,
            "rpc": _RPC if version == 2 else _RUB_POSITION_RPC,
            "version": version,
        },
    )


@_dataclass(frozen=True, slots=True)
class BrokerCashProof:
    account_scope_sha256: str
    environment: BrokerEnvironment
    as_of: str
    cash: _ledger.Money
    response_canonical_sha256: str
    proof_identity_sha256: str
    identity_key_id: str
    response_complete: bool = True
    version: int = _BROKER_CASH_PROOF_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version not in _CASH_VERSIONS:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        _require_hash(self.account_scope_sha256, CL4Reason.ACCOUNT_SCOPE_INVALID)
        if type(self.environment) is not _BrokerEnvironment:
            _fail(CL4Reason.ENVIRONMENT_UNSUPPORTED)
        if self.environment is not _BrokerEnvironment.SANDBOX:
            _fail(CL4Reason.ENVIRONMENT_UNSUPPORTED)
        _timestamp_ns(self.as_of)
        _checked_money(self.cash)
        _require_hash(self.response_canonical_sha256, CL4Reason.PROOF_IDENTITY_INVALID)
        _require_hash(self.proof_identity_sha256, CL4Reason.PROOF_IDENTITY_INVALID)
        _require_key_id(self.identity_key_id)
        if self.response_complete is not True:
            _fail(CL4Reason.PROOF_INCOMPLETE)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "as_of": self.as_of,
            "cash": _money_dict(self.cash),
            "cash_field": ("totalAmountCurrencies" if self.version == 2
                           else "money[RUB]; all blocked=0; no derivatives"),
            "domain": "v3.10-cl4-broker-cash-proof",
            "environment": self.environment.value,
            "identity_key_id": self.identity_key_id,
            "provider": "TBANK",
            "response_complete": self.response_complete,
            "response_canonical_sha256": self.response_canonical_sha256,
            "proof_identity_sha256": self.proof_identity_sha256,
            "rpc": _RPC if self.version == 2 else _RUB_POSITION_RPC,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class OpeningPlan:
    proof: BrokerCashProof
    observation: _persistence.InboxObservation
    transaction: _ledger.LedgerTransaction
    mode: OpeningMode
    generation: int
    pre_store_revision: int
    pre_ledger_revision: int
    pre_ledger_head_sha256: str
    baseline_export_sha256: str
    baseline_export_bytes: bytes = _field(repr=False)
    version: int = _OPENING_PLAN_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version not in _CASH_VERSIONS:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        if type(self.proof) is not BrokerCashProof:
            _fail(CL4Reason.TYPE_INVALID)
        if type(self.proof.version) is not int or self.proof.version not in _CASH_VERSIONS:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        if self.version != self.proof.version:
            _fail(CL4Reason.CASH_SOURCE_MISMATCH)
        if type(self.observation) is not _persistence.InboxObservation:
            _fail(CL4Reason.TYPE_INVALID)
        if type(self.transaction) is not _ledger.LedgerTransaction:
            _fail(CL4Reason.TYPE_INVALID)
        if self.mode is not OpeningMode.FROM_NOW:
            _fail(CL4Reason.MODE_UNSUPPORTED)
        if type(self.generation) is not int or self.generation != 1:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        for value in (self.pre_store_revision, self.pre_ledger_revision):
            if type(value) is not int or not 0 <= value <= _MAX_REVISION:
                _fail(CL4Reason.LEDGER_REVISION_INVALID)
        if self.pre_store_revision < self.pre_ledger_revision:
            _fail(CL4Reason.LEDGER_REVISION_INVALID)
        _require_hash(self.pre_ledger_head_sha256, CL4Reason.LEDGER_GRAPH_INVALID)
        _require_hash(self.baseline_export_sha256, CL4Reason.LEDGER_EXPORT_INVALID)
        if (
            type(self.baseline_export_bytes) is not bytes
            or not 1 <= len(self.baseline_export_bytes) <= _MAX_BASELINE_WITNESS_BYTES
            or _sha256(self.baseline_export_bytes) != self.baseline_export_sha256
        ):
            _fail(CL4Reason.LEDGER_EXPORT_INVALID)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.proof.account_scope_sha256,
            "broker_cash_proof_sha256": self.proof.sha256,
            "domain": "v3.10-cl4-opening-plan",
            "environment": self.proof.environment.value,
            "generation": self.generation,
            "mode": self.mode.value,
            "observation_sha256": self.observation.sha256,
            "opening_money": _money_dict(self.proof.cash),
            "baseline_export_sha256": self.baseline_export_sha256,
            "pre_ledger_head_sha256": self.pre_ledger_head_sha256,
            "pre_ledger_revision": str(self.pre_ledger_revision),
            "pre_store_revision": str(self.pre_store_revision),
            "transaction_sha256": self.transaction.sha256,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class OpeningRecord:
    accepted_ledger_revision: int
    account_scope_sha256: str
    baseline_ledger_export_sha256: str
    baseline_ledger_head_sha256: str
    baseline_ledger_revision: int
    baseline_store_revision: int
    broker_cash_proof_sha256: str
    cutoff: str
    environment: BrokerEnvironment
    generation: int
    mode: OpeningMode
    observation_sha256: str
    opening_money: _ledger.Money
    transaction_sha256: str
    version: int = _OPENING_RECORD_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version not in _CASH_VERSIONS:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        for value in (
            self.accepted_ledger_revision,
            self.baseline_ledger_revision,
            self.baseline_store_revision,
        ):
            if type(value) is not int or not 0 <= value <= _MAX_REVISION:
                _fail(CL4Reason.LEDGER_REVISION_INVALID)
        if self.accepted_ledger_revision <= 0:
            _fail(CL4Reason.LEDGER_REVISION_INVALID)
        if (
            self.baseline_ledger_revision >= self.accepted_ledger_revision
            or self.baseline_store_revision < self.baseline_ledger_revision
        ):
            _fail(CL4Reason.LEDGER_REVISION_INVALID)
        _require_hash(self.account_scope_sha256, CL4Reason.ACCOUNT_SCOPE_INVALID)
        for value in (
            self.baseline_ledger_export_sha256,
            self.baseline_ledger_head_sha256,
            self.broker_cash_proof_sha256,
            self.observation_sha256,
            self.transaction_sha256,
        ):
            _require_hash(value, CL4Reason.LEDGER_GRAPH_INVALID)
        _timestamp_ns(self.cutoff)
        if self.environment is not _BrokerEnvironment.SANDBOX:
            _fail(CL4Reason.ENVIRONMENT_UNSUPPORTED)
        if self.generation != 1 or type(self.generation) is not int:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        if self.mode is not OpeningMode.FROM_NOW:
            _fail(CL4Reason.MODE_UNSUPPORTED)
        _checked_money(self.opening_money)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "accepted_ledger_revision": str(self.accepted_ledger_revision),
            "account_scope_sha256": self.account_scope_sha256,
            "baseline_ledger_export_sha256": self.baseline_ledger_export_sha256,
            "baseline_ledger_head_sha256": self.baseline_ledger_head_sha256,
            "baseline_ledger_revision": str(self.baseline_ledger_revision),
            "baseline_store_revision": str(self.baseline_store_revision),
            "broker_cash_proof_sha256": self.broker_cash_proof_sha256,
            "cutoff": self.cutoff,
            "domain": "v3.10-cl4-opening-record",
            "environment": self.environment.value,
            "generation": self.generation,
            "mode": self.mode.value,
            "observation_sha256": self.observation_sha256,
            "opening_money": _money_dict(self.opening_money),
            "transaction_sha256": self.transaction_sha256,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class LedgerCashProjection:
    account_scope_sha256: str
    environment: BrokerEnvironment
    currency: str
    as_of: str
    opening_record_sha256: str
    ledger_export_sha256: str
    ledger_revision: int
    ledger_head_sha256: str
    expected_cash: _ledger.Money
    complete: bool
    incompleteness_kinds: tuple[DiscrepancyKind, ...]
    unresolved_observation_sha256: tuple[str, ...]
    version: int = _LEDGER_CASH_PROJECTION_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version not in _CASH_VERSIONS:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        _require_hash(self.account_scope_sha256, CL4Reason.ACCOUNT_SCOPE_INVALID)
        if self.environment is not _BrokerEnvironment.SANDBOX:
            _fail(CL4Reason.ENVIRONMENT_UNSUPPORTED)
        if self.currency != "RUB" or type(self.currency) is not str:
            _fail(CL4Reason.MONEY_INVALID)
        _timestamp_ns(self.as_of)
        for value in (
            self.opening_record_sha256,
            self.ledger_export_sha256,
            self.ledger_head_sha256,
        ):
            _require_hash(value, CL4Reason.LEDGER_GRAPH_INVALID)
        if type(self.ledger_revision) is not int or not 0 <= self.ledger_revision <= _MAX_REVISION:
            _fail(CL4Reason.LEDGER_REVISION_INVALID)
        _checked_money(self.expected_cash)
        if type(self.complete) is not bool:
            _fail(CL4Reason.TYPE_INVALID)
        if type(self.incompleteness_kinds) is not tuple or any(
            not isinstance(value, DiscrepancyKind)
            for value in self.incompleteness_kinds
        ):
            _fail(CL4Reason.TYPE_INVALID)
        expected_kinds = tuple(
            DiscrepancyKind(value)
            for value in _INCOMPLETE_PRECEDENCE
            if DiscrepancyKind(value) in set(self.incompleteness_kinds)
        )
        if self.incompleteness_kinds != expected_kinds:
            _fail(CL4Reason.RECONCILIATION_INVALID)
        if type(self.unresolved_observation_sha256) is not tuple or any(
            not _is_hash(value) for value in self.unresolved_observation_sha256
        ):
            _fail(CL4Reason.TYPE_INVALID)
        if self.unresolved_observation_sha256 != tuple(
            sorted(set(self.unresolved_observation_sha256))
        ):
            _fail(CL4Reason.RECONCILIATION_INVALID)
        has_unresolved = bool(self.unresolved_observation_sha256)
        if (
            (DiscrepancyKind.UNRESOLVED_OBSERVATION in self.incompleteness_kinds)
            != has_unresolved
            or self.complete != (not self.incompleteness_kinds and not has_unresolved)
        ):
            _fail(CL4Reason.RECONCILIATION_INVALID)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "as_of": self.as_of,
            "complete": self.complete,
            "currency": self.currency,
            "domain": "v3.10-cl4-ledger-cash-projection",
            "environment": self.environment.value,
            "expected_cash": _money_dict(self.expected_cash),
            "incompleteness_kinds": [value.value for value in self.incompleteness_kinds],
            "ledger_export_sha256": self.ledger_export_sha256,
            "ledger_head_sha256": self.ledger_head_sha256,
            "ledger_revision": str(self.ledger_revision),
            "opening_record_sha256": self.opening_record_sha256,
            "unresolved_observation_sha256": list(self.unresolved_observation_sha256),
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class CashReconciliation:
    proof: BrokerCashProof
    projection: LedgerCashProjection
    evaluated_at: str
    broker_cash: _ledger.Money
    expected_cash: _ledger.Money
    delta_minor_units: int
    status: ReconciliationStatus
    discrepancy_kind: DiscrepancyKind
    version: int = _CASH_RECONCILIATION_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version not in _CASH_VERSIONS:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        if (
            type(self.proof) is not BrokerCashProof
            or type(self.projection) is not LedgerCashProjection
        ):
            _fail(CL4Reason.TYPE_INVALID)
        if (
            type(self.proof.version) is not int
            or self.proof.version not in _CASH_VERSIONS
            or type(self.projection.version) is not int
            or self.projection.version not in _CASH_VERSIONS
        ):
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        if self.version != self.proof.version or self.version != self.projection.version:
            _fail(CL4Reason.CASH_SOURCE_MISMATCH)
        _timestamp_ns(self.evaluated_at)
        _checked_money(self.broker_cash)
        _checked_money(self.expected_cash)
        if type(self.delta_minor_units) is not int or not (
            -_RECONCILIATION_DELTA_MAX_ABS_MINOR_UNITS
            <= self.delta_minor_units
            <= _RECONCILIATION_DELTA_MAX_ABS_MINOR_UNITS
        ):
            _fail(CL4Reason.ARITHMETIC_OVERFLOW)
        if (
            type(self.status) is not ReconciliationStatus
            or type(self.discrepancy_kind) is not DiscrepancyKind
        ):
            _fail(CL4Reason.RECONCILIATION_INVALID)
        if (
            self.proof.account_scope_sha256 != self.projection.account_scope_sha256
            or self.proof.environment is not self.projection.environment
            or self.proof.as_of != self.projection.as_of
            or self.broker_cash.canonical_bytes != self.proof.cash.canonical_bytes
            or self.expected_cash.canonical_bytes
            != self.projection.expected_cash.canonical_bytes
            or self.delta_minor_units
            != self.broker_cash.minor_units - self.expected_cash.minor_units
        ):
            _fail(CL4Reason.RECONCILIATION_INVALID)
        if not self.projection.complete:
            expected_status = ReconciliationStatus.INCOMPLETE
            expected_kind = self.projection.incompleteness_kinds[0]
        elif self.delta_minor_units > 0:
            expected_status = ReconciliationStatus.DISCREPANCY
            expected_kind = DiscrepancyKind.BROKER_ABOVE_EXPECTED
        elif self.delta_minor_units < 0:
            expected_status = ReconciliationStatus.DISCREPANCY
            expected_kind = DiscrepancyKind.BROKER_BELOW_EXPECTED
        else:
            expected_status = ReconciliationStatus.MATCHED
            expected_kind = DiscrepancyKind.NONE
        if (self.status, self.discrepancy_kind) != (expected_status, expected_kind):
            _fail(CL4Reason.RECONCILIATION_INVALID)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.proof.account_scope_sha256,
            "broker_cash": _money_dict(self.broker_cash),
            "broker_cash_proof_sha256": self.proof.sha256,
            "currency": self.broker_cash.currency,
            "delta_minor_units": str(self.delta_minor_units),
            "discrepancy_kind": self.discrepancy_kind.value,
            "domain": "v3.10-cl4-cash-reconciliation",
            "environment": self.proof.environment.value,
            "evaluated_at": self.evaluated_at,
            "expected_cash": _money_dict(self.expected_cash),
            "ledger_cash_projection_sha256": self.projection.sha256,
            "status": self.status.value,
            "tolerance_minor_units": "0",
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class AdoptionCandidate:
    disposition: AdoptionDisposition
    ledger_head_sha256: str
    reconciliation_sha256: str
    automatic_adoption: bool = False
    requires_locked_revalidation: bool = True
    runtime_cash_owner_changed: bool = False
    version: int = _ADOPTION_CANDIDATE_VERSION

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != _ADOPTION_CANDIDATE_VERSION:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        if type(self.disposition) is not AdoptionDisposition:
            _fail(CL4Reason.RECONCILIATION_INVALID)
        _require_hash(self.ledger_head_sha256, CL4Reason.LEDGER_GRAPH_INVALID)
        _require_hash(self.reconciliation_sha256, CL4Reason.RECONCILIATION_INVALID)
        if (
            self.automatic_adoption is not False
            or self.requires_locked_revalidation is not True
            or self.runtime_cash_owner_changed is not False
        ):
            _fail(CL4Reason.RECONCILIATION_INVALID)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "automatic_adoption": self.automatic_adoption,
            "disposition": self.disposition.value,
            "domain": "v3.10-cl4-adoption-candidate",
            "ledger_head_sha256": self.ledger_head_sha256,
            "reconciliation_sha256": self.reconciliation_sha256,
            "requires_locked_revalidation": self.requires_locked_revalidation,
            "runtime_cash_owner_changed": self.runtime_cash_owner_changed,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@_dataclass(frozen=True, slots=True)
class OpeningAcceptance:
    disposition: str
    record: OpeningRecord
    store_revision: int
    ledger_revision: int
    ledger_head_sha256: str

    def __post_init__(self) -> None:
        if type(self.disposition) is not str or self.disposition not in {
            "OPENING_APPENDED",
            "OPENING_ALREADY_PRESENT",
        }:
            _fail(CL4Reason.POSTCONDITION_FAILED)
        if type(self.record) is not OpeningRecord:
            _fail(CL4Reason.TYPE_INVALID)
        if type(self.record.version) is not int or self.record.version not in _CASH_VERSIONS:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        for value in (self.store_revision, self.ledger_revision):
            if type(value) is not int or not 0 <= value <= _MAX_REVISION:
                _fail(CL4Reason.LEDGER_REVISION_INVALID)
        _require_hash(self.ledger_head_sha256, CL4Reason.LEDGER_GRAPH_INVALID)


_SCHEMA_JSON_ASCII = '{"domain":"v3.10-operation-inbox-codec-schema","fields":[{"allowed_values":null,"key":"account_scope_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"baseline_export_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"broker_cash_proof_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"contract_version","kind":"INTEGER","max_scalars":null,"maximum":"2","minimum":"2","required":true},{"allowed_values":null,"key":"cutoff","kind":"STRING","max_scalars":"30","maximum":null,"minimum":null,"required":true},{"allowed_values":["SANDBOX"],"key":"environment","kind":"STRING","max_scalars":"16","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"generation","kind":"INTEGER","max_scalars":null,"maximum":"1","minimum":"1","required":true},{"allowed_values":null,"key":"identity_key_id","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"ledger_head_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"ledger_revision","kind":"STRING","max_scalars":"19","maximum":null,"minimum":null,"required":true},{"allowed_values":["FROM_NOW"],"key":"mode","kind":"STRING","max_scalars":"16","maximum":null,"minimum":null,"required":true},{"allowed_values":["RUB"],"key":"opening_currency","kind":"STRING","max_scalars":"3","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"opening_minor_units","kind":"STRING","max_scalars":"29","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"proof_identity_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"store_revision","kind":"STRING","max_scalars":"19","maximum":null,"minimum":null,"required":true}],"version":1}'

CL4_OPENING_CODEC = _persistence.CodecDescriptor(
    codec_id="CL4_FROM_NOW_OPENING_V2",
    schema_json_ascii=_SCHEMA_JSON_ASCII,
    schema_sha256="1f15c6486dd348bdcf8f2b725111b01e583404efe5717ab5df0b7968d87bbfe3",
)


# V2 descriptor remains byte-identical. V3 pins a currency-specific, unblocked
# positions basis; legacy openings are never automatically relabelled.
_rub_schema = _json.loads(_SCHEMA_JSON_ASCII)
for _entry in _rub_schema["fields"]:
    if _entry["key"] == "contract_version":
        _entry["minimum"] = _entry["maximum"] = "3"
_rub_schema_ascii = _canonical_bytes(_rub_schema).decode("ascii")
CL4_RUB_POSITION_OPENING_CODEC = _persistence.CodecDescriptor(
    codec_id="CL4_FROM_NOW_RUB_POSITION_OPENING_V3",
    schema_json_ascii=_rub_schema_ascii,
    schema_sha256=_sha256(_rub_schema_ascii.encode("ascii")),
)
del _rub_schema, _entry, _rub_schema_ascii


def _opening_codec(version: int) -> _persistence.CodecDescriptor:
    if type(version) is not int or version not in _CASH_VERSIONS:
        _fail(CL4Reason.VERSION_UNSUPPORTED)
    return CL4_OPENING_CODEC if version == 2 else CL4_RUB_POSITION_OPENING_CODEC


def _bounded_response(value: object) -> tuple[dict[str, object], bytes]:
    if type(value) is not dict:
        _fail(CL4Reason.TYPE_INVALID)
    seen: set[int] = set()
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_RESPONSE_NODES or depth > _MAX_RESPONSE_DEPTH:
            _fail(CL4Reason.RESPONSE_BOUNDS_EXCEEDED)
        if type(item) is dict:
            identity = id(item)
            if identity in seen:
                _fail(CL4Reason.RESPONSE_BOUNDS_EXCEEDED)
            seen.add(identity)
            if len(item) > _MAX_MAPPING_KEYS:
                _fail(CL4Reason.RESPONSE_BOUNDS_EXCEEDED)
            for key, child in item.items():
                if type(key) is not str:
                    _fail(CL4Reason.RESPONSE_SCHEMA_INVALID)
                if len(key) > _MAX_KEY_SCALARS or any(
                    0xD800 <= ord(char) <= 0xDFFF for char in key
                ):
                    _fail(CL4Reason.RESPONSE_BOUNDS_EXCEEDED)
                visit(child, depth + 1)
            return
        if type(item) is list:
            identity = id(item)
            if identity in seen:
                _fail(CL4Reason.RESPONSE_BOUNDS_EXCEEDED)
            seen.add(identity)
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is str:
            if len(item) > _MAX_STRING_SCALARS or any(
                0xD800 <= ord(char) <= 0xDFFF for char in item
            ):
                _fail(CL4Reason.RESPONSE_BOUNDS_EXCEEDED)
            return
        if type(item) is int:
            if not -(2**63) <= item <= 2**63 - 1:
                _fail(CL4Reason.RESPONSE_BOUNDS_EXCEEDED)
            return
        if item is None or type(item) is bool:
            return
        _fail(CL4Reason.RESPONSE_SCHEMA_INVALID)

    visit(value, 1)
    try:
        encoded = _canonical_bytes(value)
    except (TypeError, ValueError, UnicodeError):
        failure = CL4Error(CL4Reason.RESPONSE_SCHEMA_INVALID)
    else:
        if len(encoded) > _MAX_RESPONSE_CANONICAL_BYTES:
            _fail(CL4Reason.RESPONSE_BOUNDS_EXCEEDED)
        try:
            parsed = _persistence.parse_canonical_json(encoded)
        except _persistence.PersistenceError:
            failure = CL4Error(CL4Reason.RESPONSE_SCHEMA_INVALID)
        else:
            if type(parsed) is dict and _canonical_bytes(parsed) == encoded:
                return parsed, encoded
            failure = CL4Error(CL4Reason.RESPONSE_SCHEMA_INVALID)
    raise failure from None


def _validate_proof(
    proof: object,
    identity_key: object,
    *,
    evaluated_at: str | None,
) -> BrokerCashProof:
    if type(proof) is not BrokerCashProof:
        _fail(CL4Reason.TYPE_INVALID)
    if type(proof.version) is not int or proof.version not in _CASH_VERSIONS:
        _fail(CL4Reason.VERSION_UNSUPPORTED)
    try:
        checked = BrokerCashProof(
            account_scope_sha256=proof.account_scope_sha256,
            environment=proof.environment,
            as_of=proof.as_of,
            cash=proof.cash,
            response_canonical_sha256=proof.response_canonical_sha256,
            proof_identity_sha256=proof.proof_identity_sha256,
            identity_key_id=proof.identity_key_id,
            response_complete=proof.response_complete,
            version=proof.version,
        )
        same = checked.canonical_bytes == proof.canonical_bytes
    except CL4Error:
        raise
    except Exception:  # noqa: BLE001 - forged DTO must not leak internals
        failure = CL4Error(CL4Reason.PROOF_IDENTITY_INVALID)
    else:
        if not same:
            failure = CL4Error(CL4Reason.PROOF_IDENTITY_INVALID)
        else:
            if evaluated_at is not None:
                _require_fresh(checked.as_of, evaluated_at)
            key = _require_key(identity_key)
            expected = _proof_identity(
                account_scope_sha256=checked.account_scope_sha256,
                environment=checked.environment,
                identity_key_id=checked.identity_key_id,
                response_canonical_sha256=checked.response_canonical_sha256,
                cash=checked.cash,
                as_of=checked.as_of,
                identity_key=key,
                version=checked.version,
            )
            if not _hmac.compare_digest(expected, checked.proof_identity_sha256):
                _fail(
                    CL4Reason.PROOF_IDENTITY_INVALID,
                    broker_cash_proof_sha256=checked.sha256,
                )
            return checked
    raise failure from None


def build_broker_cash_proof(
    response: object,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    as_of: str,
    evaluated_at: str,
    response_complete: bool,
    identity_key: bytes,
    identity_key_id: str,
) -> BrokerCashProof:
    if type(response) is not dict:
        _fail(CL4Reason.TYPE_INVALID)
    if type(environment) is not _BrokerEnvironment or environment is not _BrokerEnvironment.SANDBOX:
        _fail(CL4Reason.ENVIRONMENT_UNSUPPORTED)
    account = _require_hash(account_scope_sha256, CL4Reason.ACCOUNT_SCOPE_INVALID)
    key_id = _require_key_id(identity_key_id)
    _timestamp_ns(as_of)
    _timestamp_ns(evaluated_at)
    if response_complete is not True:
        _fail(CL4Reason.PROOF_INCOMPLETE)
    _require_fresh(as_of, evaluated_at)
    key = _require_key(identity_key)
    snapshot, encoded = _bounded_response(response)
    if "totalAmountCurrencies" not in snapshot:
        _fail(CL4Reason.RESPONSE_SCHEMA_INVALID)
    cash_value = snapshot["totalAmountCurrencies"]
    if type(cash_value) is not dict or frozenset(cash_value) != {
        "currency",
        "nano",
        "units",
    }:
        _fail(CL4Reason.RESPONSE_SCHEMA_INVALID)
    try:
        cash = _broker.money_value_to_money(cash_value)
    except _broker.BrokerReadError as error:
        failure = CL4Error(CL4Reason.MONEY_INVALID, error.cause_reason)
    except Exception:  # noqa: BLE001 - closed dependency boundary
        failure = CL4Error(CL4Reason.INTERNAL_BOUNDARY_FAILED)
    else:
        response_hash = _sha256(encoded)
        identity = _proof_identity(
            account_scope_sha256=account,
            environment=environment,
            identity_key_id=key_id,
            response_canonical_sha256=response_hash,
            cash=cash,
            as_of=as_of,
            identity_key=key,
        )
        return BrokerCashProof(
            account_scope_sha256=account,
            environment=environment,
            as_of=as_of,
            cash=cash,
            response_canonical_sha256=response_hash,
            proof_identity_sha256=identity,
            identity_key_id=key_id,
        )
    raise failure from None


def build_broker_rub_position_cash_proof(
    response: object,
    *,
    raw_account_id: str,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    as_of: str,
    evaluated_at: str,
    response_complete: bool,
    identity_key: bytes,
    identity_key_id: str,
) -> BrokerCashProof:
    """V3 CL4 accounting basis, not available cash or an order authorization.

    Uses a fresh exact-account Sandbox GetPositions response. Foreign positive
    positions are checked but not converted; nonzero broker blocked money and
    derivatives are outside this initial accounting basis. Raw bytes are hashed
    before any numeric decoding. The response is never mutated or re-labelled.
    """
    if type(environment) is not _BrokerEnvironment or environment is not _BrokerEnvironment.SANDBOX:
        _fail(CL4Reason.ENVIRONMENT_UNSUPPORTED)
    key = _require_key(identity_key)
    key_id = _require_key_id(identity_key_id)
    account = _require_hash(account_scope_sha256, CL4Reason.ACCOUNT_SCOPE_INVALID)
    if (type(raw_account_id) is not str or not 1 <= len(raw_account_id) <= 256
        or raw_account_id != raw_account_id.strip() or any(0xD800 <= ord(c) <= 0xDFFF for c in raw_account_id)):
        _fail(CL4Reason.ACCOUNT_SCOPE_INVALID)
    bound_account = _hmac_sha256(key, {
        "account_id": raw_account_id, "domain": "v3.10-cl3-account-scope",
        "environment": "SANDBOX", "identity_key_id": key_id,
        "provider": "TBANK", "version": 1,
    })
    if not _hmac.compare_digest(account, bound_account):
        _fail(CL4Reason.ACCOUNT_SCOPE_INVALID)
    if response_complete is not True:
        _fail(CL4Reason.PROOF_INCOMPLETE)
    _require_fresh(as_of, evaluated_at)
    snapshot, encoded = _bounded_response(response)
    if snapshot.get("accountId") != raw_account_id or type(snapshot.get("accountId")) is not str:
        _fail(CL4Reason.ACCOUNT_SCOPE_INVALID)
    loading = snapshot.get("limitsLoadingInProgress", False)
    if type(loading) is not bool:
        _fail(CL4Reason.RESPONSE_SCHEMA_INVALID)
    if loading:
        _fail(CL4Reason.POSITIONS_LOADING)
    for name in ("futures", "options"):
        if type(snapshot.get(name, [])) is not list or snapshot.get(name, []):
            _fail(CL4Reason.RESPONSE_SCHEMA_INVALID)
    if type(snapshot.get("securities", [])) is not list:
        _fail(CL4Reason.RESPONSE_SCHEMA_INVALID)

    def balances(name: str) -> dict[str, int]:
        values = snapshot.get(name, [])
        if type(values) is not list or len(values) > 32:
            _fail(CL4Reason.RESPONSE_SCHEMA_INVALID)
        result: dict[str, int] = {}
        for row in values:
            if type(row) is not dict or not frozenset(row) <= {"currency", "units", "nano"}:
                _fail(CL4Reason.RESPONSE_SCHEMA_INVALID)
            currency = row.get("currency")
            if type(currency) is not str or _re.fullmatch(r"(?:[A-Z]{3}|[a-z]{3})", currency) is None:
                _fail(CL4Reason.MONEY_INVALID)
            canonical_currency = currency.upper()
            if canonical_currency in result:
                _fail(CL4Reason.RESPONSE_SCHEMA_INVALID)
            # Decode sign/range in the existing strict integer Money codec only.
            numeric = None
            try:
                numeric = _broker.money_value_to_money({
                    "currency": "RUB", "units": row.get("units", "0"),
                    "nano": row.get("nano", 0),
                })
            except _broker.BrokerReadError:
                pass
            if numeric is None:
                _fail(CL4Reason.MONEY_INVALID)
            if not 0 <= numeric.minor_units <= 10**21:  # 1e12 currency units
                _fail(CL4Reason.MONEY_INVALID)
            result[canonical_currency] = numeric.minor_units
        return result

    money = balances("money")
    blocked = balances("blocked")
    if any(blocked.values()):
        _fail(CL4Reason.POSITIONS_BLOCKED_UNSUPPORTED)
    cash = _ledger.Money(currency="RUB", minor_units=money.get("RUB", 0))
    response_hash = _sha256(encoded)
    return BrokerCashProof(
        account_scope_sha256=account, environment=environment,
        as_of=as_of, cash=cash, response_canonical_sha256=response_hash,
        proof_identity_sha256=_proof_identity(
            account_scope_sha256=account, environment=environment,
            identity_key_id=key_id, response_canonical_sha256=response_hash,
            cash=cash, as_of=as_of, identity_key=key, version=3,
        ),
        identity_key_id=key_id, version=3,
    )


@_dataclass(frozen=True, slots=True)
class _OpeningContent:
    account_scope_sha256: str
    baseline_export_sha256: str
    broker_cash_proof_sha256: str
    contract_version: int
    cutoff: str
    environment: BrokerEnvironment
    identity_key_id: str
    ledger_head_sha256: str
    ledger_revision: int
    opening_minor_units: int
    proof_identity_sha256: str
    store_revision: int


@_dataclass(frozen=True, slots=True)
class _LedgerView:
    export_sha256: str
    store_revision: int
    ledger_revision: int
    ledger_head_sha256: str
    descriptors: dict[str, _persistence.CodecDescriptor]
    observations: dict[str, _persistence.InboxObservation]
    current_status: dict[str, str]
    events: dict[str, tuple[_persistence.InboxStatusEvent, ...]]
    transactions: dict[str, _ledger.LedgerTransaction]
    links: dict[str, str]
    bundles: dict[str, _ledger.LedgerCorrectionBundle]
    transitions: tuple[_persistence.LedgerHead, ...]
    cl4_content: dict[str, _OpeningContent]


@_dataclass(frozen=True, slots=True)
class _ProspectiveStates:
    staged_bytes: bytes
    committed_bytes: bytes
    staged_view: _LedgerView
    committed_view: _LedgerView


def _ascii_bytes(value: object, reason: CL4Reason) -> bytes:
    if type(value) is not str:
        _fail(reason)
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        _fail(reason)


def _parse_content(value: object) -> _OpeningContent:
    try:
        raw = _ascii_bytes(value, CL4Reason.LEDGER_GRAPH_INVALID)
        parsed = _persistence.parse_canonical_json(raw)
    except CL4Error:
        raise
    except _persistence.PersistenceError:
        failure = CL4Error(CL4Reason.LEDGER_GRAPH_INVALID)
    else:
        if not isinstance(parsed, _Mapping) or frozenset(parsed) != _CONTENT_KEYS:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        try:
            if (
                parsed["environment"] != "SANDBOX"
                or parsed["mode"] != "FROM_NOW"
                or type(parsed["contract_version"]) is not int
                or parsed["contract_version"] not in _CASH_VERSIONS
                or type(parsed["generation"]) is not int
                or parsed["generation"] != 1
                or parsed["opening_currency"] != "RUB"
            ):
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
            account = _require_hash(parsed["account_scope_sha256"], CL4Reason.LEDGER_GRAPH_INVALID)
            proof_hash = _require_hash(parsed["broker_cash_proof_sha256"], CL4Reason.LEDGER_GRAPH_INVALID)
            proof_identity_hash = _require_hash(parsed["proof_identity_sha256"], CL4Reason.LEDGER_GRAPH_INVALID)
            baseline_hash = _require_hash(parsed["baseline_export_sha256"], CL4Reason.LEDGER_GRAPH_INVALID)
            head_hash = _require_hash(parsed["ledger_head_sha256"], CL4Reason.LEDGER_GRAPH_INVALID)
            key_id = _require_key_id(parsed["identity_key_id"])
            _timestamp_ns(parsed["cutoff"])
            ledger_revision = _parse_revision(parsed["ledger_revision"])
            store_revision = _parse_revision(parsed["store_revision"])
            if store_revision < ledger_revision:
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
            amount_text = parsed["opening_minor_units"]
            if type(amount_text) is not str or _DECIMAL_RE.fullmatch(amount_text) is None:
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
            amount = int(amount_text)
            money = _ledger.Money(currency="RUB", minor_units=amount)
            if money.minor_units <= 0 or money.minor_units > _ledger.LEDGER_POSTING_MAX_MINOR_UNITS:
                _fail(CL4Reason.OPENING_AMOUNT_UNSUPPORTED)
            return _OpeningContent(
                account_scope_sha256=account,
                baseline_export_sha256=baseline_hash,
                broker_cash_proof_sha256=proof_hash,
                contract_version=parsed["contract_version"],
                cutoff=parsed["cutoff"],
                environment=_BrokerEnvironment.SANDBOX,
                identity_key_id=key_id,
                ledger_head_sha256=head_hash,
                ledger_revision=ledger_revision,
                opening_minor_units=amount,
                proof_identity_sha256=proof_identity_hash,
                store_revision=store_revision,
            )
        except CL4Error:
            failure = CL4Error(CL4Reason.LEDGER_GRAPH_INVALID)
        except (_ledger.MoneyError, ValueError, TypeError):
            failure = CL4Error(CL4Reason.LEDGER_GRAPH_INVALID)
    raise failure from None


def _source_scope(content: _OpeningContent, key: bytes) -> str:
    return _hmac_sha256(
        key,
        {
            "account_scope_sha256": content.account_scope_sha256,
            "currency": "RUB",
            "domain": "v3.10-cl4-opening-source-scope",
            "environment": "SANDBOX",
            "generation": 1,
            "identity_key_id": content.identity_key_id,
            "mode": "FROM_NOW",
            "version": content.contract_version,
        },
    )


def _provenance(content: _OpeningContent, source_content_sha256: str, key: bytes) -> str:
    return _hmac_sha256(
        key,
        {
            "account_scope_sha256": content.account_scope_sha256,
            "baseline_export_sha256": content.baseline_export_sha256,
            "broker_cash_proof_sha256": content.broker_cash_proof_sha256,
            "domain": "v3.10-cl4-opening-provenance",
            "identity_key_id": content.identity_key_id,
            "proof_identity_sha256": content.proof_identity_sha256,
            "source_content_sha256": source_content_sha256,
            "version": content.contract_version,
        },
    )


def _content_dict(content: _OpeningContent) -> dict[str, object]:
    return {
        "account_scope_sha256": content.account_scope_sha256,
        "baseline_export_sha256": content.baseline_export_sha256,
        "broker_cash_proof_sha256": content.broker_cash_proof_sha256,
        "contract_version": content.contract_version,
        "cutoff": content.cutoff,
        "environment": content.environment.value,
        "generation": 1,
        "identity_key_id": content.identity_key_id,
        "ledger_head_sha256": content.ledger_head_sha256,
        "ledger_revision": str(content.ledger_revision),
        "mode": "FROM_NOW",
        "opening_currency": "RUB",
        "opening_minor_units": str(content.opening_minor_units),
        "proof_identity_sha256": content.proof_identity_sha256,
        "store_revision": str(content.store_revision),
    }


def _opening_graph(
    content: _OpeningContent,
    key: bytes,
) -> tuple[_persistence.InboxObservation, _ledger.LedgerTransaction]:
    content_bytes = _canonical_bytes(_content_dict(content))
    content_hash = _sha256(content_bytes)
    source = _ledger.SourceIdentity(
        account_scope_sha256=content.account_scope_sha256,
        source_kind="CL4_FROM_NOW_OPENING",
        source_scope_sha256=_source_scope(content, key),
        source_content_sha256=content_hash,
    )
    observation = _persistence.InboxObservation.create(
        descriptor=_opening_codec(content.contract_version),
        content=_content_dict(content),
        source=source,
        observed_at=content.cutoff,
        provenance_sha256=_provenance(content, content_hash, key),
    )
    money = _ledger.Money(currency="RUB", minor_units=content.opening_minor_units)
    transaction = _ledger.LedgerTransaction(
        classification=_ledger.LedgerClassification.OPENING_BALANCE,
        effective_at=content.cutoff,
        source=source,
        postings=(
            _ledger.LedgerPosting(
                line_no=1,
                account=_ledger.LedgerAccount.ASSET_BROKER_CASH,
                money=money,
            ),
            _ledger.LedgerPosting(
                line_no=2,
                account=_ledger.LedgerAccount.EQUITY_OPENING_BALANCE,
                money=-money,
            ),
        ),
    )
    return observation, transaction


def _export_value(value: bytes) -> dict[str, object]:
    try:
        parsed = _persistence.parse_canonical_json(value)
    except _persistence.PersistenceError:
        _fail(CL4Reason.OPENING_CAPACITY_EXHAUSTED)
    if type(parsed) is not dict:
        _fail(CL4Reason.OPENING_CAPACITY_EXHAUSTED)
    return parsed


def _event_order(row: object) -> tuple[str, int]:
    if type(row) is not dict or frozenset(row) != _EVENT_WRAPPER_KEYS:
        _fail(CL4Reason.OPENING_CAPACITY_EXHAUSTED)
    try:
        event = _persistence.InboxStatusEvent.from_canonical_bytes(
            _ascii_bytes(row["canonical_json_ascii"], CL4Reason.OPENING_CAPACITY_EXHAUSTED)
        )
    except (_persistence.PersistenceError, CL4Error):
        _fail(CL4Reason.OPENING_CAPACITY_EXHAUSTED)
    return event.observation_sha256, event.event_no


def _assemble_prospective_exports(plan: OpeningPlan) -> tuple[bytes, bytes]:
    staged = _export_value(plan.baseline_export_bytes)
    codec = _opening_codec(plan.proof.version)
    descriptor_row = {
        "canonical_json_ascii": codec.canonical_bytes.decode("ascii"),
        "sha256": codec.sha256,
    }
    if not any(
        type(row) is dict and row.get("sha256") == codec.sha256
        for row in staged["codec_registry"]
    ):
        staged["codec_registry"].append(descriptor_row)
        staged["codec_registry"].sort(key=lambda row: row["sha256"])
    observation_row = {
        "canonical_json_ascii": plan.observation.canonical_bytes.decode("ascii"),
        "current_status": "OBSERVED",
        "logical_source_sha256": plan.observation.logical_source_sha256,
        "sha256": plan.observation.sha256,
        "source_sha256": plan.observation.source.sha256,
    }
    staged["observations"].append(observation_row)
    staged["observations"].sort(
        key=lambda row: (row["logical_source_sha256"], row["sha256"])
    )
    staged["store_revision"] = str(plan.pre_store_revision + 1)
    staged_bytes = _canonical_bytes(staged)

    committed = _export_value(staged_bytes)
    for row in committed["observations"]:
        if row["sha256"] == plan.observation.sha256:
            row["current_status"] = "LEDGER_LINKED"
            break
    event = _persistence.InboxStatusEvent(
        observation_sha256=plan.observation.sha256,
        event_no=1,
        from_status="OBSERVED",
        to_status="LEDGER_LINKED",
        reason="LEDGER_TRANSACTION_ACCEPTED",
        related_transaction_sha256=plan.transaction.sha256,
    )
    committed["inbox_status_events"].append(
        {
            "canonical_json_ascii": event.canonical_bytes.decode("ascii"),
            "sha256": event.sha256,
        }
    )
    committed["inbox_status_events"].sort(key=_event_order)
    committed["transactions"].append(
        {
            "canonical_json_ascii": plan.transaction.canonical_bytes.decode("ascii"),
            "economic_sha256": plan.transaction.economic_sha256,
            "sha256": plan.transaction.sha256,
            "source_sha256": plan.transaction.source_sha256,
        }
    )
    committed["transactions"].sort(key=lambda row: row["sha256"])
    committed["provenance_links"].append(
        {
            "observation_sha256": plan.observation.sha256,
            "transaction_sha256": plan.transaction.sha256,
        }
    )
    committed["provenance_links"].sort(
        key=lambda row: (row["transaction_sha256"], row["observation_sha256"])
    )
    head_bytes = _canonical_bytes(
        {
            "domain": "v3.10-cash-ledger-head",
            "ledger_revision": str(plan.pre_ledger_revision + 1),
            "previous_head_sha256": plan.pre_ledger_head_sha256,
            "transition_kind": "TRANSACTION",
            "transition_sha256": plan.transaction.sha256,
            "version": 1,
        }
    )
    head_sha256 = _sha256(head_bytes)
    committed["ledger_transitions"].append(
        {
            "head_json_ascii": head_bytes.decode("ascii"),
            "head_sha256": head_sha256,
        }
    )
    committed["ledger_head_json_ascii"] = head_bytes.decode("ascii")
    committed["ledger_head_sha256"] = head_sha256
    committed["ledger_revision"] = str(plan.pre_ledger_revision + 1)
    committed["store_revision"] = str(plan.pre_store_revision + 2)
    return staged_bytes, _canonical_bytes(committed)


def _prospective_states(plan: OpeningPlan, key: bytes) -> _ProspectiveStates:
    if (
        plan.pre_store_revision
        > _MAX_REVISION - _OPENING_STORE_REVISION_RESERVE
        or plan.pre_ledger_revision
        > _MAX_REVISION - _OPENING_LEDGER_REVISION_RESERVE
    ):
        _fail(CL4Reason.OPENING_CAPACITY_EXHAUSTED)
    try:
        staged_bytes, committed_bytes = _assemble_prospective_exports(plan)
        for encoded in (staged_bytes, committed_bytes):
            parsed = _export_value(encoded)
            arrays = [parsed[name] for name in _EXPORT_ARRAY_NAMES]
            if (
                any(type(array) is not list or len(array) > _MAX_LEDGER_OBJECTS for array in arrays)
                or sum(map(len, arrays)) > _MAX_LEDGER_OBJECTS
                or not 1 <= len(encoded) <= _MAX_LEDGER_EXPORT_BYTES
            ):
                _fail(CL4Reason.OPENING_CAPACITY_EXHAUSTED)
        staged_view = _parse_ledger_export(
            staged_bytes,
            target_account=plan.proof.account_scope_sha256,
            identity_key=key,
        )
        committed_view = _parse_ledger_export(
            committed_bytes,
            target_account=plan.proof.account_scope_sha256,
            identity_key=key,
        )
        if (
            staged_view.store_revision != plan.pre_store_revision + 1
            or staged_view.ledger_revision != plan.pre_ledger_revision
            or staged_view.observations.get(plan.observation.sha256) is None
            or committed_view.store_revision != plan.pre_store_revision + 2
            or committed_view.ledger_revision != plan.pre_ledger_revision + 1
            or committed_view.transactions.get(plan.transaction.sha256) is None
            or committed_view.links.get(plan.transaction.sha256)
            != plan.observation.sha256
        ):
            _fail(CL4Reason.OPENING_CAPACITY_EXHAUSTED)
    except CL4Error:
        failure = CL4Error(CL4Reason.OPENING_CAPACITY_EXHAUSTED)
    except (_persistence.PersistenceError, _ledger.LedgerError, KeyError, TypeError, ValueError):
        failure = CL4Error(CL4Reason.OPENING_CAPACITY_EXHAUSTED)
    else:
        return _ProspectiveStates(
            staged_bytes=staged_bytes,
            committed_bytes=committed_bytes,
            staged_view=staged_view,
            committed_view=committed_view,
        )
    raise failure from None


def _valid_event(event: _persistence.InboxStatusEvent, status: str, number: int) -> bool:
    if event.event_no != number + 1 or event.from_status != status:
        return False
    null_refs = event.related_transaction_sha256 is None and event.related_bundle_sha256 is None
    if event.reason == "REVIEW_REQUIRED":
        return status == "OBSERVED" and event.to_status == "REVIEW_REQUIRED" and null_refs
    if event.reason in {"NOT_LEDGER_RELEVANT", "INVALID_OBSERVATION"}:
        return status in {"OBSERVED", "REVIEW_REQUIRED"} and event.to_status == "REJECTED" and null_refs
    if event.reason == "LEDGER_TRANSACTION_ACCEPTED":
        return (
            status in {"OBSERVED", "REVIEW_REQUIRED"}
            and event.to_status == "LEDGER_LINKED"
            and event.related_transaction_sha256 is not None
            and event.related_bundle_sha256 is None
        )
    if event.reason == "LEDGER_CORRECTION_ACCEPTED":
        return (
            status in {"OBSERVED", "REVIEW_REQUIRED"}
            and event.to_status == "LEDGER_LINKED"
            and event.related_transaction_sha256 is None
            and event.related_bundle_sha256 is not None
        )
    return False


def _transaction_kind(transaction: _ledger.LedgerTransaction) -> str:
    if transaction.classification is _ledger.LedgerClassification.REVERSAL:
        return "REVERSAL"
    if transaction.corrects_sha256 is not None:
        return "CORRECTION"
    return "ORDINARY"


def _parse_ledger_export(
    value: object,
    *,
    target_account: str,
    identity_key: bytes | None,
) -> _LedgerView:
    if type(value) is not bytes:
        _fail(CL4Reason.TYPE_INVALID)
    if not 1 <= len(value) <= _MAX_LEDGER_EXPORT_BYTES:
        _fail(CL4Reason.LEDGER_EXPORT_INVALID)
    try:
        parsed = _persistence.parse_canonical_json(value)
    except _persistence.PersistenceError as error:
        failure = CL4Error(CL4Reason.LEDGER_EXPORT_INVALID, error.reason)
    else:
        if not isinstance(parsed, _Mapping) or frozenset(parsed) != _EXPORT_KEYS:
            _fail(CL4Reason.LEDGER_EXPORT_INVALID)
        if (
            parsed["domain"] != "v3.10-cash-ledger-export"
            or type(parsed["schema_version"]) is not int
            or type(parsed["version"]) is not int
            or parsed["schema_version"] != 1
            or parsed["version"] != 1
        ):
            _fail(CL4Reason.LEDGER_EXPORT_INVALID)
        arrays = []
        for name in (
            "codec_registry",
            "observations",
            "inbox_status_events",
            "transactions",
            "provenance_links",
            "correction_bundles",
            "ledger_transitions",
        ):
            candidate = parsed[name]
            if type(candidate) is not list or len(candidate) > _MAX_LEDGER_OBJECTS:
                _fail(CL4Reason.LEDGER_EXPORT_INVALID)
            arrays.append(candidate)
        if sum(map(len, arrays)) > _MAX_LEDGER_OBJECTS:
            _fail(CL4Reason.LEDGER_EXPORT_INVALID)
        try:
            view = _validate_export_graph(parsed, target_account, identity_key, _sha256(value))
        except CL4Error:
            raise
        except (_persistence.PersistenceError, _ledger.LedgerError, ValueError, TypeError, UnicodeError):
            failure = CL4Error(CL4Reason.LEDGER_GRAPH_INVALID)
        else:
            return view
    raise failure from None


def _validate_export_graph(
    parsed: _Mapping[str, object],
    target_account: str,
    identity_key: bytes | None,
    export_sha256: str,
) -> _LedgerView:
    codec_rows = parsed["codec_registry"]
    descriptors: dict[str, _persistence.CodecDescriptor] = {}
    codec_order: list[str] = []
    for row in codec_rows:
        if type(row) is not dict or frozenset(row) != _CODEC_WRAPPER_KEYS:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        descriptor = _persistence.CodecDescriptor.from_canonical_bytes(
            _ascii_bytes(row["canonical_json_ascii"], CL4Reason.LEDGER_GRAPH_INVALID)
        )
        if row["sha256"] != descriptor.sha256 or descriptor.sha256 in descriptors:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        for frozen_codec in (CL4_OPENING_CODEC, CL4_RUB_POSITION_OPENING_CODEC):
            if descriptor.codec_id == frozen_codec.codec_id and descriptor.canonical_bytes != frozen_codec.canonical_bytes:
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        descriptors[descriptor.sha256] = descriptor
        codec_order.append(descriptor.sha256)
    if codec_order != sorted(codec_order):
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)

    registry = tuple(descriptors.values())
    observations: dict[str, _persistence.InboxObservation] = {}
    current_status: dict[str, str] = {}
    observation_order: list[tuple[str, str]] = []
    cl4_content: dict[str, _OpeningContent] = {}
    logical_sources: set[str] = set()
    for row in parsed["observations"]:
        if type(row) is not dict or frozenset(row) != _OBSERVATION_WRAPPER_KEYS:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        observation = _persistence.InboxObservation.from_canonical_bytes(
            _ascii_bytes(row["canonical_json_ascii"], CL4Reason.LEDGER_GRAPH_INVALID),
            registry,
        )
        if (
            row["sha256"] != observation.sha256
            or row["logical_source_sha256"] != observation.logical_source_sha256
            or row["source_sha256"] != observation.source.sha256
            or observation.sha256 in observations
            or observation.logical_source_sha256 in logical_sources
            or row["current_status"] not in {"OBSERVED", "REVIEW_REQUIRED", "REJECTED", "LEDGER_LINKED"}
        ):
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        observations[observation.sha256] = observation
        logical_sources.add(observation.logical_source_sha256)
        current_status[observation.sha256] = row["current_status"]
        observation_order.append((observation.logical_source_sha256, observation.sha256))
        is_cl4 = (
            observation.descriptor.codec_id in {CL4_OPENING_CODEC.codec_id, CL4_RUB_POSITION_OPENING_CODEC.codec_id}
            or observation.source.source_kind == "CL4_FROM_NOW_OPENING"
        )
        if is_cl4:
            content = _parse_content(observation.content_json_ascii)
            if (
                observation.descriptor.canonical_bytes != _opening_codec(content.contract_version).canonical_bytes
                or observation.source.source_kind != "CL4_FROM_NOW_OPENING"
            ):
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
            if (
                observation.source.account_scope_sha256 != content.account_scope_sha256
                or observation.observed_at != content.cutoff
                or observation.source.source_content_sha256
                != _sha256(_canonical_bytes(_content_dict(content)))
            ):
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
            if content.account_scope_sha256 == target_account and identity_key is not None:
                expected_observation, _ = _opening_graph(content, identity_key)
                if expected_observation.canonical_bytes != observation.canonical_bytes:
                    _fail(CL4Reason.LEDGER_GRAPH_INVALID)
            cl4_content[observation.sha256] = content
    if observation_order != sorted(observation_order):
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)
    if set(descriptors) != {
        observation.descriptor.sha256 for observation in observations.values()
    }:
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)

    events_by_observation: dict[str, list[_persistence.InboxStatusEvent]] = {
        key: [] for key in observations
    }
    event_order: list[tuple[str, int]] = []
    event_hashes: set[str] = set()
    for row in parsed["inbox_status_events"]:
        if type(row) is not dict or frozenset(row) != _EVENT_WRAPPER_KEYS:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        event = _persistence.InboxStatusEvent.from_canonical_bytes(
            _ascii_bytes(row["canonical_json_ascii"], CL4Reason.LEDGER_GRAPH_INVALID)
        )
        if (
            row["sha256"] != event.sha256
            or event.sha256 in event_hashes
            or event.observation_sha256 not in observations
        ):
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        event_hashes.add(event.sha256)
        events_by_observation[event.observation_sha256].append(event)
        event_order.append((event.observation_sha256, event.event_no))
    if event_order != sorted(event_order):
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)
    for observation_hash, values in events_by_observation.items():
        status = "OBSERVED"
        number = 0
        for event in values:
            if not _valid_event(event, status, number):
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
            status, number = event.to_status, event.event_no
        if status != current_status[observation_hash]:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)

    transactions: dict[str, _ledger.LedgerTransaction] = {}
    transaction_order: list[str] = []
    for row in parsed["transactions"]:
        if type(row) is not dict or frozenset(row) != _TRANSACTION_WRAPPER_KEYS:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        raw = _ascii_bytes(row["canonical_json_ascii"], CL4Reason.LEDGER_GRAPH_INVALID)
        transaction = _ledger.LedgerTransaction.from_canonical_dict(
            _persistence.parse_canonical_json(raw)
        )
        if (
            row["sha256"] != transaction.sha256
            or row["source_sha256"] != transaction.source_sha256
            or row["economic_sha256"] != transaction.economic_sha256
            or transaction.sha256 in transactions
        ):
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        transactions[transaction.sha256] = transaction
        transaction_order.append(transaction.sha256)
    if transaction_order != sorted(transaction_order):
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)

    links: dict[str, str] = {}
    link_order: list[tuple[str, str]] = []
    for row in parsed["provenance_links"]:
        if type(row) is not dict or frozenset(row) != _LINK_KEYS:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        transaction_hash = _require_hash(row["transaction_sha256"], CL4Reason.LEDGER_GRAPH_INVALID)
        observation_hash = _require_hash(row["observation_sha256"], CL4Reason.LEDGER_GRAPH_INVALID)
        if (
            transaction_hash in links
            or transaction_hash not in transactions
            or observation_hash not in observations
            or transactions[transaction_hash].source.canonical_bytes
            != observations[observation_hash].source.canonical_bytes
        ):
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        links[transaction_hash] = observation_hash
        link_order.append((transaction_hash, observation_hash))
    if link_order != sorted(link_order) or set(links) != set(transactions):
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)

    bundles: dict[str, _ledger.LedgerCorrectionBundle] = {}
    bundle_order: list[tuple[str, str]] = []
    for row in parsed["correction_bundles"]:
        if type(row) is not dict or frozenset(row) != _BUNDLE_WRAPPER_KEYS:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        raw = _ascii_bytes(row["canonical_json_ascii"], CL4Reason.LEDGER_GRAPH_INVALID)
        bundle_dict = _persistence.parse_canonical_json(raw)
        if not isinstance(bundle_dict, _Mapping):
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        original_hash = bundle_dict.get("original_sha256")
        reversal_hash = bundle_dict.get("reversal_sha256")
        correction_hash = bundle_dict.get("correction_sha256")
        if any(value not in transactions for value in (original_hash, reversal_hash, correction_hash)):
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        bundle = _ledger.LedgerCorrectionBundle.from_canonical_dict(
            bundle_dict,
            original=transactions[original_hash],
            reversal=transactions[reversal_hash],
            correction=transactions[correction_hash],
        )
        if row["sha256"] != bundle.sha256 or bundle.sha256 in bundles:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        bundles[bundle.sha256] = bundle
        bundle_order.append((bundle.original_sha256, bundle.sha256))
    if bundle_order != sorted(bundle_order):
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)
    _ledger.validate_correction_bundle_set(tuple(bundles.values()))

    correction_members: set[str] = set()
    permitted_pairs: set[frozenset[str]] = set()
    for bundle in bundles.values():
        if (
            _transaction_kind(bundle.original) != "ORDINARY"
            or _transaction_kind(bundle.reversal) != "REVERSAL"
            or _transaction_kind(bundle.correction) != "CORRECTION"
        ):
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        correction_members.update((bundle.reversal.sha256, bundle.correction.sha256))
        members = (bundle.original.sha256, bundle.reversal.sha256, bundle.correction.sha256)
        permitted_pairs.update(
            frozenset((members[left], members[right]))
            for left, right in ((0, 1), (0, 2), (1, 2))
        )
        original_observation = links[bundle.original.sha256]
        component_observations = {links[bundle.reversal.sha256], links[bundle.correction.sha256]}
        for observation_hash in component_observations:
            matches = [event for event in events_by_observation[observation_hash] if event.related_bundle_sha256 == bundle.sha256]
            if len(matches) != (0 if observation_hash == original_observation else 1):
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)

    source_groups: dict[str, list[str]] = {}
    economic_groups: dict[str, list[str]] = {}
    for transaction in transactions.values():
        source_groups.setdefault(transaction.source_sha256, []).append(transaction.sha256)
        economic_groups.setdefault(transaction.economic_sha256, []).append(transaction.sha256)
    for group in (*source_groups.values(), *economic_groups.values()):
        if len(group) > 3:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                if frozenset((left, right)) not in permitted_pairs:
                    _fail(CL4Reason.LEDGER_GRAPH_INVALID)

    transaction_events: dict[str, list[_persistence.InboxStatusEvent]] = {}
    for values in events_by_observation.values():
        for event in values:
            if event.related_transaction_sha256 is not None:
                transaction_events.setdefault(
                    event.related_transaction_sha256,
                    [],
                ).append(event)
    for transaction_hash, transaction in transactions.items():
        related = transaction_events.get(transaction_hash, [])
        if _transaction_kind(transaction) == "ORDINARY":
            if len(related) != 1:
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        elif transaction_hash not in correction_members or related:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
    for observation_hash, values in events_by_observation.items():
        for event in values:
            if event.related_transaction_sha256 is not None and (
                event.related_transaction_sha256 not in transactions
                or links[event.related_transaction_sha256] != observation_hash
            ):
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
            if event.related_bundle_sha256 is not None:
                bundle = bundles.get(event.related_bundle_sha256)
                if bundle is None:
                    _fail(CL4Reason.LEDGER_GRAPH_INVALID)
                expected = {links[bundle.reversal.sha256], links[bundle.correction.sha256]} - {links[bundle.original.sha256]}
                if observation_hash not in expected:
                    _fail(CL4Reason.LEDGER_GRAPH_INVALID)

    ledger_revision = _parse_revision(parsed["ledger_revision"])
    store_revision = _parse_revision(parsed["store_revision"])
    transitions: list[_persistence.LedgerHead] = []
    previous = _persistence.GENESIS_HEAD_SHA256
    transitioned_transactions: set[str] = set()
    transitioned_bundles: set[str] = set()
    for expected_revision, row in enumerate(parsed["ledger_transitions"], start=1):
        if type(row) is not dict or frozenset(row) != _TRANSITION_WRAPPER_KEYS:
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        head = _persistence.LedgerHead.from_canonical_bytes(
            _ascii_bytes(row["head_json_ascii"], CL4Reason.LEDGER_GRAPH_INVALID)
        )
        if (
            row["head_sha256"] != head.sha256
            or head.ledger_revision != expected_revision
            or head.previous_head_sha256 != previous
        ):
            _fail(CL4Reason.LEDGER_GRAPH_INVALID)
        if head.transition_kind == "TRANSACTION":
            transaction = transactions.get(head.transition_sha256)
            if (
                transaction is None
                or _transaction_kind(transaction) != "ORDINARY"
                or head.transition_sha256 in transitioned_transactions
            ):
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
            transitioned_transactions.add(head.transition_sha256)
        else:
            if (
                head.transition_sha256 not in bundles
                or head.transition_sha256 in transitioned_bundles
            ):
                _fail(CL4Reason.LEDGER_GRAPH_INVALID)
            transitioned_bundles.add(head.transition_sha256)
        transitions.append(head)
        previous = head.sha256
    if len(transitions) != ledger_revision:
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)
    final_head_bytes = (
        _persistence.GENESIS_HEAD_JSON_ASCII.encode("ascii")
        if not transitions
        else transitions[-1].canonical_bytes
    )
    if (
        parsed["ledger_head_sha256"] != previous
        or _ascii_bytes(parsed["ledger_head_json_ascii"], CL4Reason.LEDGER_GRAPH_INVALID) != final_head_bytes
        or transitioned_transactions != {key for key, value in transactions.items() if _transaction_kind(value) == "ORDINARY"}
        or transitioned_bundles != set(bundles)
    ):
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)
    nonledger = sum(
        event.reason in {"REVIEW_REQUIRED", "NOT_LEDGER_RELEVANT", "INVALID_OBSERVATION"}
        for values in events_by_observation.values()
        for event in values
    )
    if store_revision != len(observations) + nonledger + ledger_revision:
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)
    return _LedgerView(
        export_sha256=export_sha256,
        store_revision=store_revision,
        ledger_revision=ledger_revision,
        ledger_head_sha256=previous,
        descriptors=descriptors,
        observations=observations,
        current_status=current_status,
        events={key: tuple(value) for key, value in events_by_observation.items()},
        transactions=transactions,
        links=links,
        bundles=bundles,
        transitions=tuple(transitions),
        cl4_content=cl4_content,
    )


def _head_at(view: _LedgerView, revision: int) -> str | None:
    if revision == 0:
        return _persistence.GENESIS_HEAD_SHA256
    if 1 <= revision <= len(view.transitions):
        return view.transitions[revision - 1].sha256
    return None


def _opening_record(
    view: _LedgerView,
    account: str,
    key: bytes,
) -> OpeningRecord | None:
    target_openings = [
        transaction
        for transaction in view.transactions.values()
        if transaction.source.account_scope_sha256 == account
        and transaction.classification is _ledger.LedgerClassification.OPENING_BALANCE
    ]
    target_cl4_observations = [
        observation_hash
        for observation_hash, content in view.cl4_content.items()
        if content.account_scope_sha256 == account
    ]
    if len(target_openings) > 1 or len(target_cl4_observations) > 1:
        _fail(CL4Reason.OPENING_CONFLICT)
    for bundle in view.bundles.values():
        if (
            bundle.original.source.account_scope_sha256 == account
            and bundle.original.classification is _ledger.LedgerClassification.OPENING_BALANCE
        ):
            _fail(CL4Reason.OPENING_CONFLICT)
    if not target_openings:
        return None
    transaction = target_openings[0]
    observation_hash = view.links.get(transaction.sha256)
    if observation_hash not in view.cl4_content or view.current_status.get(observation_hash) != "LEDGER_LINKED":
        _fail(CL4Reason.OPENING_CONFLICT)
    content = view.cl4_content[observation_hash]
    if content.account_scope_sha256 != account:
        _fail(CL4Reason.OPENING_CONFLICT)
    _, expected_transaction = _opening_graph(content, key)
    if transaction.canonical_bytes != expected_transaction.canonical_bytes:
        _fail(CL4Reason.OPENING_CONFLICT)
    accepted_revision = next(
        (
            head.ledger_revision
            for head in view.transitions
            if head.transition_kind == "TRANSACTION" and head.transition_sha256 == transaction.sha256
        ),
        None,
    )
    if (
        accepted_revision is None
        or content.ledger_revision + 1 != accepted_revision
        or content.store_revision < content.ledger_revision
        or _head_at(view, content.ledger_revision) != content.ledger_head_sha256
    ):
        _fail(CL4Reason.OPENING_CONFLICT)
    return OpeningRecord(
        accepted_ledger_revision=accepted_revision,
        account_scope_sha256=account,
        baseline_ledger_export_sha256=content.baseline_export_sha256,
        baseline_ledger_head_sha256=content.ledger_head_sha256,
        baseline_ledger_revision=content.ledger_revision,
        baseline_store_revision=content.store_revision,
        broker_cash_proof_sha256=content.broker_cash_proof_sha256,
        cutoff=content.cutoff,
        environment=content.environment,
        generation=1,
        mode=OpeningMode.FROM_NOW,
        observation_sha256=observation_hash,
        opening_money=_ledger.Money(currency="RUB", minor_units=content.opening_minor_units),
        transaction_sha256=transaction.sha256,
        version=content.contract_version,
    )


def _validate_target_inbox_before(view: _LedgerView, account: str, cutoff: str) -> None:
    for observation_hash, observation in view.observations.items():
        if observation.source.account_scope_sha256 != account:
            continue
        if observation.observed_at > cutoff or view.current_status[observation_hash] not in {"LEDGER_LINKED", "REJECTED"}:
            _fail(CL4Reason.INBOX_INCOMPLETE)


def prepare_from_now_opening(
    ledger_export_bytes: bytes,
    proof: BrokerCashProof,
    *,
    evaluated_at: str,
    identity_key: bytes,
) -> OpeningPlan:
    if type(ledger_export_bytes) is not bytes:
        _fail(CL4Reason.TYPE_INVALID)
    checked_proof = _validate_proof(proof, identity_key, evaluated_at=evaluated_at)
    view = _parse_ledger_export(
        ledger_export_bytes,
        target_account=checked_proof.account_scope_sha256,
        identity_key=identity_key,
    )
    if _opening_record(view, checked_proof.account_scope_sha256, identity_key) is not None:
        _fail(CL4Reason.OPENING_CONFLICT)
    if any(
        content.account_scope_sha256 == checked_proof.account_scope_sha256
        for content in view.cl4_content.values()
    ):
        _fail(CL4Reason.OPENING_CONFLICT)
    _validate_target_inbox_before(view, checked_proof.account_scope_sha256, checked_proof.as_of)
    if checked_proof.cash.minor_units <= 0 or checked_proof.cash.minor_units > _ledger.LEDGER_POSTING_MAX_MINOR_UNITS:
        _fail(CL4Reason.OPENING_AMOUNT_UNSUPPORTED)
    content = _OpeningContent(
        account_scope_sha256=checked_proof.account_scope_sha256,
        baseline_export_sha256=view.export_sha256,
        broker_cash_proof_sha256=checked_proof.sha256,
        contract_version=checked_proof.version,
        cutoff=checked_proof.as_of,
        environment=checked_proof.environment,
        identity_key_id=checked_proof.identity_key_id,
        ledger_head_sha256=view.ledger_head_sha256,
        ledger_revision=view.ledger_revision,
        opening_minor_units=checked_proof.cash.minor_units,
        proof_identity_sha256=checked_proof.proof_identity_sha256,
        store_revision=view.store_revision,
    )
    observation, transaction = _opening_graph(content, identity_key)
    plan = OpeningPlan(
        proof=checked_proof,
        observation=observation,
        transaction=transaction,
        mode=OpeningMode.FROM_NOW,
        generation=1,
        pre_store_revision=view.store_revision,
        pre_ledger_revision=view.ledger_revision,
        pre_ledger_head_sha256=view.ledger_head_sha256,
        baseline_export_sha256=view.export_sha256,
        baseline_export_bytes=ledger_export_bytes,
        version=checked_proof.version,
    )
    _prospective_states(plan, _require_key(identity_key))
    return plan


def _validate_plan_structure(plan: object) -> OpeningPlan:
    if type(plan) is not OpeningPlan:
        _fail(CL4Reason.TYPE_INVALID)
    if type(plan.version) is not int or plan.version not in _CASH_VERSIONS:
        _fail(CL4Reason.VERSION_UNSUPPORTED)
    try:
        raw_proof = plan.proof
        if type(raw_proof) is not BrokerCashProof:
            _fail(CL4Reason.TYPE_INVALID)
        if type(raw_proof.version) is not int or raw_proof.version not in _CASH_VERSIONS:
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        proof = BrokerCashProof(
            account_scope_sha256=raw_proof.account_scope_sha256,
            environment=raw_proof.environment,
            as_of=raw_proof.as_of,
            cash=raw_proof.cash,
            response_canonical_sha256=raw_proof.response_canonical_sha256,
            proof_identity_sha256=raw_proof.proof_identity_sha256,
            identity_key_id=raw_proof.identity_key_id,
            response_complete=raw_proof.response_complete,
            version=raw_proof.version,
        )
        observation = _persistence.InboxObservation.from_canonical_bytes(
            plan.observation.canonical_bytes,
            (CL4_OPENING_CODEC, CL4_RUB_POSITION_OPENING_CODEC),
        )
        transaction = _ledger.LedgerTransaction.from_canonical_dict(
            _persistence.parse_canonical_json(plan.transaction.canonical_bytes)
        )
        checked = OpeningPlan(
            proof=proof,
            observation=observation,
            transaction=transaction,
            mode=plan.mode,
            generation=plan.generation,
            pre_store_revision=plan.pre_store_revision,
            pre_ledger_revision=plan.pre_ledger_revision,
            pre_ledger_head_sha256=plan.pre_ledger_head_sha256,
            baseline_export_sha256=plan.baseline_export_sha256,
            baseline_export_bytes=plan.baseline_export_bytes,
            version=plan.version,
        )
        same = (
            proof.canonical_bytes == raw_proof.canonical_bytes
            and observation.canonical_bytes == plan.observation.canonical_bytes
            and transaction.canonical_bytes == plan.transaction.canonical_bytes
            and checked.canonical_bytes == plan.canonical_bytes
        )
    except CL4Error:
        raise
    except Exception:  # noqa: BLE001 - forged DTO must not leak internals
        failure = CL4Error(CL4Reason.OPENING_CONFLICT)
    else:
        if not same:
            _fail(CL4Reason.OPENING_CONFLICT)
        return checked
    raise failure from None


def _validate_plan(plan: object, identity_key: object) -> OpeningPlan:
    checked = _validate_plan_structure(plan)
    try:
        proof = _validate_proof(checked.proof, identity_key, evaluated_at=None)
        key = _require_key(identity_key)
        content = _OpeningContent(
            account_scope_sha256=proof.account_scope_sha256,
            baseline_export_sha256=checked.baseline_export_sha256,
            broker_cash_proof_sha256=proof.sha256,
            contract_version=proof.version,
            cutoff=proof.as_of,
            environment=proof.environment,
            identity_key_id=proof.identity_key_id,
            ledger_head_sha256=checked.pre_ledger_head_sha256,
            ledger_revision=checked.pre_ledger_revision,
            opening_minor_units=proof.cash.minor_units,
            proof_identity_sha256=proof.proof_identity_sha256,
            store_revision=checked.pre_store_revision,
        )
        observation, transaction = _opening_graph(content, key)
        same = (
            observation.canonical_bytes == checked.observation.canonical_bytes
            and transaction.canonical_bytes == checked.transaction.canonical_bytes
        )
        if not same:
            _fail(CL4Reason.OPENING_CONFLICT)
        baseline = _parse_ledger_export(
            checked.baseline_export_bytes,
            target_account=proof.account_scope_sha256,
            identity_key=key,
        )
        if (
            baseline.export_sha256 != checked.baseline_export_sha256
            or baseline.store_revision != checked.pre_store_revision
            or baseline.ledger_revision != checked.pre_ledger_revision
            or baseline.ledger_head_sha256 != checked.pre_ledger_head_sha256
        ):
            _fail(CL4Reason.OPENING_CONFLICT)
        if _opening_record(baseline, proof.account_scope_sha256, key) is not None or any(
            item.account_scope_sha256 == proof.account_scope_sha256
            for item in baseline.cl4_content.values()
        ):
            _fail(CL4Reason.OPENING_CONFLICT)
        _validate_target_inbox_before(
            baseline,
            proof.account_scope_sha256,
            proof.as_of,
        )
        if proof.cash.minor_units <= 0 or proof.cash.minor_units > _ledger.LEDGER_POSTING_MAX_MINOR_UNITS:
            _fail(CL4Reason.OPENING_AMOUNT_UNSUPPORTED)
    except CL4Error:
        raise
    except Exception:  # noqa: BLE001 - forged DTO must not leak internals
        failure = CL4Error(CL4Reason.OPENING_CONFLICT)
    else:
        return checked
    raise failure from None


def _acceptance(view: _LedgerView, record: OpeningRecord, disposition: str) -> OpeningAcceptance:
    return OpeningAcceptance(
        disposition=disposition,
        record=record,
        store_revision=view.store_revision,
        ledger_revision=view.ledger_revision,
        ledger_head_sha256=view.ledger_head_sha256,
    )


def _export_store(store: object) -> bytes:
    if type(store) is not _persistence.CashLedgerStore:
        _fail(CL4Reason.TYPE_INVALID)
    try:
        return store.export_bytes()
    except _persistence.PersistenceError as error:
        failure = CL4Error(CL4Reason.PERSISTENCE_FAILURE, error.reason)
    except Exception:  # noqa: BLE001 - closed public boundary
        failure = CL4Error(CL4Reason.PERSISTENCE_FAILURE)
    raise failure from None


def _opening_persistence_failure(
    error: _persistence.PersistenceError,
    plan: OpeningPlan,
) -> CL4Error:
    if error.reason is _persistence.PersistenceReason.REVISION_MISMATCH:
        reason = CL4Reason.OPENING_PLAN_STALE
    elif error.reason in {
        _persistence.PersistenceReason.SOURCE_CONTENT_CONFLICT,
        _persistence.PersistenceReason.SOURCE_CONFLICT,
        _persistence.PersistenceReason.STATUS_TRANSITION_INVALID,
        _persistence.PersistenceReason.LINEAGE_CONFLICT,
        _persistence.PersistenceReason.ECONOMIC_MATCH_REVIEW_REQUIRED,
    }:
        reason = CL4Reason.OPENING_CONFLICT
    else:
        reason = CL4Reason.PERSISTENCE_FAILURE
    return CL4Error(
        reason,
        error.reason,
        {"opening_plan_sha256": plan.sha256},
    )


def _classify_accept_state(
    export_bytes: bytes,
    view: _LedgerView,
    plan: OpeningPlan,
    key: bytes | None,
    staged_bytes: bytes,
    committed_bytes: bytes,
) -> tuple[str, OpeningRecord | None]:
    account = plan.proof.account_scope_sha256
    if export_bytes == plan.baseline_export_bytes:
        return "ABSENT", None
    if export_bytes == staged_bytes:
        return "STAGED", None
    if export_bytes == committed_bytes:
        if key is None:
            return "COMMITTED", None
        record = _opening_record(view, account, key)
        if (
            record is not None
            and record.broker_cash_proof_sha256 == plan.proof.sha256
            and record.observation_sha256 == plan.observation.sha256
            and record.transaction_sha256 == plan.transaction.sha256
            and record.baseline_ledger_export_sha256 == plan.baseline_export_sha256
            and record.baseline_ledger_head_sha256 == plan.pre_ledger_head_sha256
            and record.baseline_ledger_revision == plan.pre_ledger_revision
            and record.baseline_store_revision == plan.pre_store_revision
        ):
            return "COMMITTED", record
        _fail(CL4Reason.POSTCONDITION_FAILED)
    target_cl4 = [
        observation_hash
        for observation_hash, content in view.cl4_content.items()
        if content.account_scope_sha256 == account
    ]
    target_openings = [
        transaction
        for transaction in view.transactions.values()
        if transaction.source.account_scope_sha256 == account
        and transaction.classification is _ledger.LedgerClassification.OPENING_BALANCE
    ]
    same_observation = (
        target_cl4 == [plan.observation.sha256]
        and view.observations[plan.observation.sha256].canonical_bytes
        == plan.observation.canonical_bytes
    )
    same_transaction = (
        len(target_openings) == 1
        and target_openings[0].canonical_bytes == plan.transaction.canonical_bytes
    )
    if target_cl4 or target_openings:
        if same_observation and (not target_openings or same_transaction):
            _fail(CL4Reason.BASELINE_STALE)
        _fail(CL4Reason.OPENING_CONFLICT)
    _fail(CL4Reason.BASELINE_STALE)


def accept_from_now_opening(
    store: CashLedgerStore,
    plan: OpeningPlan,
    *,
    confirmation: str,
    evaluated_at: str,
    identity_key: bytes,
) -> OpeningAcceptance:
    if type(confirmation) is not str:
        _fail(CL4Reason.CONFIRMATION_INVALID)
    try:
        expected_confirmation = f"ACCEPT V3.10 CL4 FROM_NOW OPENING {plan.sha256}"
    except Exception:  # noqa: BLE001 - forged DTO must not leak internals
        expected_confirmation = ""
    if confirmation != expected_confirmation:
        _fail(CL4Reason.CONFIRMATION_INVALID)
    if type(plan) is not OpeningPlan:
        _fail(CL4Reason.TYPE_INVALID)
    structural_plan = _validate_plan_structure(plan)
    _timestamp_ns(evaluated_at)
    if type(store) is not _persistence.CashLedgerStore:
        _fail(CL4Reason.TYPE_INVALID)
    before_bytes = _export_store(store)
    before = _parse_ledger_export(
        before_bytes,
        target_account=structural_plan.proof.account_scope_sha256,
        identity_key=None,
    )
    if before_bytes == structural_plan.baseline_export_bytes:
        structural_state = "ABSENT"
        staged_bytes = b""
        committed_bytes = b""
    else:
        try:
            staged_bytes, committed_bytes = _assemble_prospective_exports(
                structural_plan
            )
        except (
            CL4Error,
            _persistence.PersistenceError,
            _ledger.LedgerError,
            AttributeError,
            KeyError,
            TypeError,
            UnicodeError,
            ValueError,
        ):
            assembly_failed = True
        else:
            assembly_failed = False
        if assembly_failed:
            _fail(CL4Reason.BASELINE_STALE)
        structural_state, _ = _classify_accept_state(
            before_bytes,
            before,
            structural_plan,
            None,
            staged_bytes,
            committed_bytes,
        )
    if structural_state == "ABSENT":
        _require_fresh(structural_plan.proof.as_of, evaluated_at)
    key = _require_key(identity_key)
    checked_plan = _validate_plan(structural_plan, key)
    before = _parse_ledger_export(
        before_bytes,
        target_account=checked_plan.proof.account_scope_sha256,
        identity_key=key,
    )
    if structural_state == "ABSENT":
        state, record = "ABSENT", None
    else:
        state, record = _classify_accept_state(
            before_bytes,
            before,
            checked_plan,
            key,
            staged_bytes,
            committed_bytes,
        )
    if state != structural_state:
        _fail(CL4Reason.POSTCONDITION_FAILED)
    prospective = _prospective_states(checked_plan, key)
    if state == "COMMITTED":
        assert record is not None
        return _acceptance(before, record, "OPENING_ALREADY_PRESENT")
    if state == "ABSENT":
        try:
            store.append_observation(
                checked_plan.observation,
                expected_store_revision=before.store_revision,
            )
        except _persistence.PersistenceError as error:
            failure = _opening_persistence_failure(error, checked_plan)
        except Exception:  # noqa: BLE001 - closed persistence boundary
            failure = CL4Error(CL4Reason.PERSISTENCE_FAILURE)
        else:
            after_observation_bytes = _export_store(store)
            after_observation = _parse_ledger_export(
                after_observation_bytes,
                target_account=checked_plan.proof.account_scope_sha256,
                identity_key=key,
            )
            state, record = _classify_accept_state(
                after_observation_bytes,
                after_observation,
                checked_plan,
                key,
                prospective.staged_bytes,
                prospective.committed_bytes,
            )
            if state == "COMMITTED":
                assert record is not None
                return _acceptance(after_observation, record, "OPENING_ALREADY_PRESENT")
            if state != "STAGED":
                _fail(CL4Reason.POSTCONDITION_FAILED)
            before = after_observation
        if 'failure' in locals():
            raise failure from None
    try:
        transaction_disposition = store.append_transaction(
            checked_plan.transaction,
            checked_plan.observation.sha256,
            expected_store_revision=before.store_revision,
            expected_ledger_revision=before.ledger_revision,
        )
    except _persistence.PersistenceError as error:
        failure = _opening_persistence_failure(error, checked_plan)
    except Exception:  # noqa: BLE001 - closed persistence boundary
        failure = CL4Error(CL4Reason.PERSISTENCE_FAILURE)
    else:
        after_bytes = _export_store(store)
        if after_bytes != prospective.committed_bytes:
            _fail(CL4Reason.POSTCONDITION_FAILED)
        after = _parse_ledger_export(
            after_bytes,
            target_account=checked_plan.proof.account_scope_sha256,
            identity_key=key,
        )
        state, record = _classify_accept_state(
            after_bytes,
            after,
            checked_plan,
            key,
            prospective.staged_bytes,
            prospective.committed_bytes,
        )
        if state != "COMMITTED" or record is None:
            _fail(CL4Reason.POSTCONDITION_FAILED)
        disposition = (
            "OPENING_ALREADY_PRESENT"
            if transaction_disposition
            is _persistence.PersistenceDisposition.TRANSACTION_ALREADY_PRESENT
            else "OPENING_APPENDED"
        )
        return _acceptance(after, record, disposition)
    raise failure from None


def _cash_effect(transaction: _ledger.LedgerTransaction) -> int:
    matches = [
        posting.money.minor_units
        for posting in transaction.postings
        if posting.account is _ledger.LedgerAccount.ASSET_BROKER_CASH
    ]
    if len(matches) != 1:
        _fail(CL4Reason.LEDGER_GRAPH_INVALID)
    return matches[0]


def project_shadow_cash(
    ledger_export_bytes: bytes,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    as_of: str,
    identity_key: bytes,
) -> LedgerCashProjection:
    if type(ledger_export_bytes) is not bytes:
        _fail(CL4Reason.TYPE_INVALID)
    account = _require_hash(account_scope_sha256, CL4Reason.ACCOUNT_SCOPE_INVALID)
    if type(environment) is not _BrokerEnvironment or environment is not _BrokerEnvironment.SANDBOX:
        _fail(CL4Reason.ENVIRONMENT_UNSUPPORTED)
    _timestamp_ns(as_of)
    key = _require_key(identity_key)
    view = _parse_ledger_export(ledger_export_bytes, target_account=account, identity_key=key)
    record = _opening_record(view, account, key)
    if record is None:
        _fail(CL4Reason.OPENING_MISSING)
    amount = record.opening_money.minor_units
    kinds: set[DiscrepancyKind] = set()
    if record.cutoff > as_of:
        kinds.add(DiscrepancyKind.PROOF_BEFORE_LEDGER_EFFECT)
    for head in view.transitions:
        if head.ledger_revision <= record.baseline_ledger_revision:
            continue
        if head.transition_kind == "TRANSACTION":
            transaction = view.transactions[head.transition_sha256]
            if transaction.sha256 == record.transaction_sha256:
                continue
            candidates = (transaction,)
        else:
            bundle = view.bundles[head.transition_sha256]
            candidates = (bundle.reversal, bundle.correction)
        for transaction in candidates:
            if transaction.source.account_scope_sha256 != account:
                continue
            effect = _cash_effect(transaction)
            if transaction.effective_at <= record.cutoff:
                kinds.add(DiscrepancyKind.LATE_PRE_CUTOFF_LEDGER_EFFECT)
            if transaction.effective_at > as_of:
                kinds.add(DiscrepancyKind.PROOF_BEFORE_LEDGER_EFFECT)
            amount += effect
            if not _ledger.MONEY_MIN_MINOR_UNITS <= amount <= _ledger.MONEY_MAX_MINOR_UNITS:
                _fail(CL4Reason.ARITHMETIC_OVERFLOW)
    unresolved: list[str] = []
    for observation_hash, observation in view.observations.items():
        if observation.source.account_scope_sha256 != account:
            continue
        if observation.observed_at > as_of:
            kinds.add(DiscrepancyKind.PROOF_BEFORE_LEDGER_EVIDENCE)
        if view.current_status[observation_hash] in {"OBSERVED", "REVIEW_REQUIRED"}:
            unresolved.append(observation_hash)
    if unresolved:
        kinds.add(DiscrepancyKind.UNRESOLVED_OBSERVATION)
    ordered = tuple(
        DiscrepancyKind(value)
        for value in _INCOMPLETE_PRECEDENCE
        if DiscrepancyKind(value) in kinds
    )
    return LedgerCashProjection(
        account_scope_sha256=account,
        environment=environment,
        currency="RUB",
        as_of=as_of,
        opening_record_sha256=record.sha256,
        ledger_export_sha256=view.export_sha256,
        ledger_revision=view.ledger_revision,
        ledger_head_sha256=view.ledger_head_sha256,
        expected_cash=_ledger.Money(currency="RUB", minor_units=amount),
        complete=not ordered and not unresolved,
        incompleteness_kinds=ordered,
        unresolved_observation_sha256=tuple(sorted(set(unresolved))),
        version=record.version,
    )


def reconcile_shadow_cash(
    ledger_export_bytes: bytes,
    proof: BrokerCashProof,
    *,
    evaluated_at: str,
    identity_key: bytes,
) -> CashReconciliation:
    if type(ledger_export_bytes) is not bytes or type(proof) is not BrokerCashProof:
        _fail(CL4Reason.TYPE_INVALID)
    checked_proof = _validate_proof(proof, identity_key, evaluated_at=evaluated_at)
    projection = project_shadow_cash(
        ledger_export_bytes,
        account_scope_sha256=checked_proof.account_scope_sha256,
        environment=checked_proof.environment,
        as_of=checked_proof.as_of,
        identity_key=identity_key,
    )
    view = _parse_ledger_export(
        ledger_export_bytes,
        target_account=checked_proof.account_scope_sha256,
        identity_key=identity_key,
    )
    record = _opening_record(view, checked_proof.account_scope_sha256, identity_key)
    if record is None:
        _fail(CL4Reason.OPENING_MISSING)
    opening_content = view.cl4_content[record.observation_sha256]
    if opening_content.contract_version != checked_proof.version:
        _fail(CL4Reason.CASH_SOURCE_MISMATCH)
    if opening_content.identity_key_id != checked_proof.identity_key_id:
        _fail(CL4Reason.PROOF_IDENTITY_INVALID)
    if (
        projection.account_scope_sha256 != checked_proof.account_scope_sha256
        or projection.environment is not checked_proof.environment
        or projection.currency != checked_proof.cash.currency
    ):
        _fail(CL4Reason.RECONCILIATION_INVALID)
    delta = checked_proof.cash.minor_units - projection.expected_cash.minor_units
    if not -_RECONCILIATION_DELTA_MAX_ABS_MINOR_UNITS <= delta <= _RECONCILIATION_DELTA_MAX_ABS_MINOR_UNITS:
        _fail(CL4Reason.ARITHMETIC_OVERFLOW)
    if not projection.complete:
        status = ReconciliationStatus.INCOMPLETE
        kind = projection.incompleteness_kinds[0]
    elif delta > 0:
        status = ReconciliationStatus.DISCREPANCY
        kind = DiscrepancyKind.BROKER_ABOVE_EXPECTED
    elif delta < 0:
        status = ReconciliationStatus.DISCREPANCY
        kind = DiscrepancyKind.BROKER_BELOW_EXPECTED
    else:
        status = ReconciliationStatus.MATCHED
        kind = DiscrepancyKind.NONE
    return CashReconciliation(
        proof=checked_proof,
        projection=projection,
        evaluated_at=evaluated_at,
        broker_cash=checked_proof.cash,
        expected_cash=projection.expected_cash,
        delta_minor_units=delta,
        status=status,
        discrepancy_kind=kind,
        version=checked_proof.version,
    )


def _validate_reconciliation(value: object) -> CashReconciliation:
    if type(value) is not CashReconciliation:
        _fail(CL4Reason.TYPE_INVALID)
    if type(value.version) is not int or value.version not in _CASH_VERSIONS:
        _fail(CL4Reason.VERSION_UNSUPPORTED)
    try:
        raw_proof = value.proof
        raw_projection = value.projection
        if (
            type(raw_proof) is not BrokerCashProof
            or type(raw_projection) is not LedgerCashProjection
        ):
            _fail(CL4Reason.TYPE_INVALID)
        if (
            type(raw_proof.version) is not int
            or raw_proof.version not in _CASH_VERSIONS
            or type(raw_projection.version) is not int
            or raw_projection.version not in _CASH_VERSIONS
        ):
            _fail(CL4Reason.VERSION_UNSUPPORTED)
        proof = BrokerCashProof(
            account_scope_sha256=raw_proof.account_scope_sha256,
            environment=raw_proof.environment,
            as_of=raw_proof.as_of,
            cash=raw_proof.cash,
            response_canonical_sha256=raw_proof.response_canonical_sha256,
            proof_identity_sha256=raw_proof.proof_identity_sha256,
            identity_key_id=raw_proof.identity_key_id,
            response_complete=raw_proof.response_complete,
            version=raw_proof.version,
        )
        projection = LedgerCashProjection(
            account_scope_sha256=raw_projection.account_scope_sha256,
            environment=raw_projection.environment,
            currency=raw_projection.currency,
            as_of=raw_projection.as_of,
            opening_record_sha256=raw_projection.opening_record_sha256,
            ledger_export_sha256=raw_projection.ledger_export_sha256,
            ledger_revision=raw_projection.ledger_revision,
            ledger_head_sha256=raw_projection.ledger_head_sha256,
            expected_cash=raw_projection.expected_cash,
            complete=raw_projection.complete,
            incompleteness_kinds=raw_projection.incompleteness_kinds,
            unresolved_observation_sha256=raw_projection.unresolved_observation_sha256,
            version=raw_projection.version,
        )
        checked = CashReconciliation(
            proof=proof,
            projection=projection,
            evaluated_at=value.evaluated_at,
            broker_cash=value.broker_cash,
            expected_cash=value.expected_cash,
            delta_minor_units=value.delta_minor_units,
            status=value.status,
            discrepancy_kind=value.discrepancy_kind,
            version=value.version,
        )
        same = (
            proof.canonical_bytes == raw_proof.canonical_bytes
            and projection.canonical_bytes == raw_projection.canonical_bytes
            and checked.canonical_bytes == value.canonical_bytes
        )
        correlated = (
            checked.broker_cash.canonical_bytes == checked.proof.cash.canonical_bytes
            and checked.expected_cash.canonical_bytes
            == checked.projection.expected_cash.canonical_bytes
            and checked.delta_minor_units
            == checked.broker_cash.minor_units - checked.expected_cash.minor_units
        )
        if not checked.projection.complete:
            expected = (
                ReconciliationStatus.INCOMPLETE,
                checked.projection.incompleteness_kinds[0],
            )
        elif checked.delta_minor_units > 0:
            expected = (
                ReconciliationStatus.DISCREPANCY,
                DiscrepancyKind.BROKER_ABOVE_EXPECTED,
            )
        elif checked.delta_minor_units < 0:
            expected = (
                ReconciliationStatus.DISCREPANCY,
                DiscrepancyKind.BROKER_BELOW_EXPECTED,
            )
        else:
            expected = (ReconciliationStatus.MATCHED, DiscrepancyKind.NONE)
    except CL4Error:
        raise
    except Exception:  # noqa: BLE001 - forged DTO must not leak internals
        failure = CL4Error(CL4Reason.RECONCILIATION_INVALID)
    else:
        if same and correlated and (checked.status, checked.discrepancy_kind) == expected:
            return checked
        failure = CL4Error(CL4Reason.RECONCILIATION_INVALID)
    raise failure from None


def build_adoption_candidate(
    reconciliation: CashReconciliation,
    *,
    ledger_export_bytes: bytes,
    identity_key: bytes,
) -> AdoptionCandidate:
    if type(reconciliation) is not CashReconciliation or type(ledger_export_bytes) is not bytes:
        _fail(CL4Reason.TYPE_INVALID)
    checked = _validate_reconciliation(reconciliation)
    recomputed = reconcile_shadow_cash(
        ledger_export_bytes,
        checked.proof,
        evaluated_at=checked.evaluated_at,
        identity_key=identity_key,
    )
    if recomputed.canonical_bytes != checked.canonical_bytes:
        _fail(
            CL4Reason.RECONCILIATION_INVALID,
            reconciliation_sha256=checked.sha256,
        )
    disposition = (
        AdoptionDisposition.SEPARATE_LOCKED_REVIEW_REQUIRED
        if checked.status is ReconciliationStatus.MATCHED
        and checked.discrepancy_kind is DiscrepancyKind.NONE
        and checked.projection.complete
        else AdoptionDisposition.BLOCKED
    )
    return AdoptionCandidate(
        disposition=disposition,
        ledger_head_sha256=checked.projection.ledger_head_sha256,
        reconciliation_sha256=checked.sha256,
    )
