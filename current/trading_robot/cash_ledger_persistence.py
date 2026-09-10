"""Deterministic append-only SQLite custody for the accepted v3.10 CashLedger."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from trading_robot.cash_ledger_domain import (
    IdentityRelation,
    LedgerClassification,
    LedgerCorrectionBundle,
    LedgerError,
    LedgerTransaction,
    SourceIdentity,
    compare_transaction_identities,
    validate_correction_bundle_set,
)

CASH_LEDGER_STORE_SCHEMA_VERSION = 1
OPERATION_INBOX_VERSION = 1
OPERATION_INBOX_CODEC_VERSION = 1
OPERATION_INBOX_STATUS_EVENT_VERSION = 1
LEDGER_HEAD_VERSION = 1
CASH_LEDGER_EXPORT_VERSION = 1
CASH_LEDGER_BACKUP_VERSION = 1
CL2_CROSS_LANGUAGE_FIXTURE_VERSION = 1

SQLITE_APPLICATION_ID = 0x434C3201
MAX_REVISION = 9_223_372_036_854_775_807
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
MAX_CANONICAL_CONTENT_BYTES = 65_536

GENESIS_HEAD_JSON_ASCII = (
    '{"domain":"v3.10-cash-ledger-head","ledger_revision":"0",'
    '"previous_head_sha256":null,"transition_kind":"GENESIS",'
    '"transition_sha256":null,"version":1}'
)
GENESIS_HEAD_SHA256 = "6ee5e86309122771bcaca40bb57771c2378c30d79b079e5431b227300a673d37"

_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}", re.ASCII)
_FIELD_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_DECIMAL_RE = re.compile(r"0|-?[1-9][0-9]*", re.ASCII)
_POSITIVE_DECIMAL_RE = re.compile(r"[1-9][0-9]*", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{9})Z",
    re.ASCII,
)
_FORBIDDEN_CONTENT_KEYS = frozenset(
    {
        "account_id",
        "broker_account_id",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "set_cookie",
        "headers",
        "raw_payload",
        "provider_payload",
        "secret",
        "password",
        "api_key",
        "credential",
    }
)
_OBSERVATION_KEYS = frozenset(
    {
        "codec_id",
        "codec_schema_sha256",
        "codec_version",
        "content_json_ascii",
        "domain",
        "initial_status",
        "logical_source_sha256",
        "observed_at",
        "provenance_sha256",
        "source",
        "version",
    }
)
_STATUS_EVENT_KEYS = frozenset(
    {
        "domain",
        "event_no",
        "from_status",
        "observation_sha256",
        "reason",
        "related_bundle_sha256",
        "related_transaction_sha256",
        "to_status",
        "version",
    }
)
_DESCRIPTOR_KEYS = frozenset(
    {
        "codec_id",
        "codec_version",
        "domain",
        "schema_json_ascii",
        "schema_sha256",
        "version",
    }
)
_SCHEMA_KEYS = frozenset({"domain", "fields", "version"})
_SCHEMA_FIELD_KEYS = frozenset(
    {
        "allowed_values",
        "key",
        "kind",
        "maximum",
        "max_scalars",
        "minimum",
        "required",
    }
)


class PersistenceDisposition(StrEnum):
    OBSERVATION_STORED = "OBSERVATION_STORED"
    OBSERVATION_ALREADY_PRESENT = "OBSERVATION_ALREADY_PRESENT"
    STATUS_EVENT_APPENDED = "STATUS_EVENT_APPENDED"
    STATUS_EVENT_ALREADY_PRESENT = "STATUS_EVENT_ALREADY_PRESENT"
    TRANSACTION_APPENDED = "TRANSACTION_APPENDED"
    TRANSACTION_ALREADY_PRESENT = "TRANSACTION_ALREADY_PRESENT"
    CORRECTION_BUNDLE_APPENDED = "CORRECTION_BUNDLE_APPENDED"
    CORRECTION_BUNDLE_ALREADY_PRESENT = "CORRECTION_BUNDLE_ALREADY_PRESENT"


class PersistenceReason(StrEnum):
    TYPE_INVALID = "TYPE_INVALID"
    CANONICAL_FORMAT_INVALID = "CANONICAL_FORMAT_INVALID"
    HASH_INVALID = "HASH_INVALID"
    VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
    CODEC_UNSUPPORTED = "CODEC_UNSUPPORTED"
    SANITIZED_CONTENT_INVALID = "SANITIZED_CONTENT_INVALID"
    SENSITIVE_CONTENT_FORBIDDEN = "SENSITIVE_CONTENT_FORBIDDEN"
    PATH_INVALID = "PATH_INVALID"
    PATH_COLLISION = "PATH_COLLISION"
    STORE_MISSING = "STORE_MISSING"
    STORE_BUSY = "STORE_BUSY"
    STORE_CLOSED = "STORE_CLOSED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    SEMANTIC_INTEGRITY_FAILURE = "SEMANTIC_INTEGRITY_FAILURE"
    REVISION_MISMATCH = "REVISION_MISMATCH"
    REVISION_EXHAUSTED = "REVISION_EXHAUSTED"
    OBSERVATION_NOT_FOUND = "OBSERVATION_NOT_FOUND"
    SOURCE_CONTENT_CONFLICT = "SOURCE_CONTENT_CONFLICT"
    STATUS_TRANSITION_INVALID = "STATUS_TRANSITION_INVALID"
    TRANSACTION_NOT_FOUND = "TRANSACTION_NOT_FOUND"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    ECONOMIC_MATCH_REVIEW_REQUIRED = "ECONOMIC_MATCH_REVIEW_REQUIRED"
    LINEAGE_CONFLICT = "LINEAGE_CONFLICT"
    INTERRUPTED_TRANSACTION = "INTERRUPTED_TRANSACTION"
    WAL_SIDECAR_INCONSISTENT = "WAL_SIDECAR_INCONSISTENT"
    BACKUP_MANIFEST_INVALID = "BACKUP_MANIFEST_INVALID"
    RESTORE_VERIFICATION_FAILED = "RESTORE_VERIFICATION_FAILED"
    IO_FAILURE = "IO_FAILURE"


class PersistenceError(RuntimeError):
    """Fail-closed persistence error with a stable reason."""

    def __init__(self, reason: PersistenceReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class InjectedFault(RuntimeError):
    """Test-only interruption raised by a caller-supplied fault injector."""


class _DuplicateKey(ValueError):
    pass


def _fail(reason: PersistenceReason) -> None:
    raise PersistenceError(reason)


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_revision(value: object) -> int:
    if not _is_plain_int(value):
        _fail(PersistenceReason.TYPE_INVALID)
    if not 0 <= value <= MAX_REVISION:
        _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
    return value


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _require_sha256(value: object) -> str:
    if not _is_sha256(value):
        _fail(PersistenceReason.HASH_INVALID)
    return value


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _reject_float_or_surrogate(value: object) -> None:
    if isinstance(value, float):
        _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
    if isinstance(value, str) and _contains_surrogate(value):
        _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or _contains_surrogate(key):
                _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
            _reject_float_or_surrogate(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _reject_float_or_surrogate(item)


def canonical_json_bytes(value: object) -> bytes:
    try:
        _reject_float_or_surrogate(value)
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except PersistenceError:
        raise
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise PersistenceError(PersistenceReason.CANONICAL_FORMAT_INVALID) from exc


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def parse_canonical_json(value: object) -> object:
    if isinstance(value, bytes):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise PersistenceError(PersistenceReason.CANONICAL_FORMAT_INVALID) from exc
        supplied = value
    elif isinstance(value, str):
        if _contains_surrogate(value):
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        try:
            supplied = value.encode("ascii")
        except UnicodeEncodeError as exc:
            raise PersistenceError(PersistenceReason.CANONICAL_FORMAT_INVALID) from exc
        text = value
    else:
        _fail(PersistenceReason.TYPE_INVALID)
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (json.JSONDecodeError, _DuplicateKey, RecursionError, ValueError) as exc:
        raise PersistenceError(PersistenceReason.CANONICAL_FORMAT_INVALID) from exc
    if canonical_json_bytes(parsed) != supplied:
        _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
    return parsed


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _timestamp_is_valid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        return False
    year, month, day_value, hour, minute, second, _ = map(int, match.groups())
    if hour > 23 or minute > 59 or second > 59:
        return False
    try:
        date(year, month, day_value)
    except ValueError:
        return False
    return True


def _parse_decimal(value: object, *, positive: bool = False) -> int:
    if not isinstance(value, str):
        _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
    pattern = _POSITIVE_DECIMAL_RE if positive else _DECIMAL_RE
    if pattern.fullmatch(value) is None:
        _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
    try:
        return int(value)
    except ValueError as exc:
        raise PersistenceError(PersistenceReason.CANONICAL_FORMAT_INVALID) from exc


def _validate_schema(schema_json_ascii: str) -> tuple[dict[str, object], ...]:
    try:
        encoded = schema_json_ascii.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise PersistenceError(PersistenceReason.CANONICAL_FORMAT_INVALID) from exc
    if len(encoded) > MAX_CANONICAL_CONTENT_BYTES:
        _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
    value = parse_canonical_json(encoded)
    if not isinstance(value, Mapping) or frozenset(value) != _SCHEMA_KEYS:
        _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
    if value["domain"] != "v3.10-operation-inbox-codec-schema":
        _fail(PersistenceReason.VERSION_UNSUPPORTED)
    version = value["version"]
    if not _is_plain_int(version) or version != OPERATION_INBOX_CODEC_VERSION:
        _fail(PersistenceReason.VERSION_UNSUPPORTED)
    fields = value["fields"]
    if not isinstance(fields, list) or not 1 <= len(fields) <= 256:
        _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
    validated: list[dict[str, object]] = []
    previous_key: str | None = None
    for field in fields:
        if not isinstance(field, Mapping) or frozenset(field) != _SCHEMA_FIELD_KEYS:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        key = field["key"]
        if not isinstance(key, str) or _FIELD_KEY_RE.fullmatch(key) is None:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        if previous_key is not None and key <= previous_key:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        previous_key = key
        if not isinstance(field["required"], bool):
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        kind = field["kind"]
        if kind not in {"STRING", "INTEGER", "BOOLEAN", "NULL"}:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        allowed = field["allowed_values"]
        minimum = field["minimum"]
        maximum = field["maximum"]
        max_scalars = field["max_scalars"]
        if kind == "STRING":
            scalar_limit = _parse_decimal(max_scalars, positive=True)
            if scalar_limit > 4096 or minimum is not None or maximum is not None:
                _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
            if allowed is not None:
                if not isinstance(allowed, list) or not 1 <= len(allowed) <= 256:
                    _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
                if any(
                    not isinstance(item, str)
                    or _contains_surrogate(item)
                    or len(item) > scalar_limit
                    for item in allowed
                ):
                    _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
                ordered = sorted(allowed, key=lambda item: canonical_json_bytes(item))
                if allowed != ordered or len(set(allowed)) != len(allowed):
                    _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        elif kind == "INTEGER":
            lower = _parse_decimal(minimum)
            upper = _parse_decimal(maximum)
            if (
                lower < -MAX_SAFE_JSON_INTEGER
                or upper > MAX_SAFE_JSON_INTEGER
                or lower > upper
                or max_scalars is not None
                or allowed is not None
            ):
                _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        elif any(item is not None for item in (allowed, minimum, maximum, max_scalars)):
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        validated.append(dict(field))
    return tuple(validated)


def _reject_forbidden_schema_fields(
    fields: tuple[dict[str, object], ...],
) -> None:
    if any(field["key"].lower() in _FORBIDDEN_CONTENT_KEYS for field in fields):
        _fail(PersistenceReason.SENSITIVE_CONTENT_FORBIDDEN)


@dataclass(frozen=True, slots=True)
class CodecDescriptor:
    codec_id: str
    schema_json_ascii: str
    schema_sha256: str
    codec_version: int = OPERATION_INBOX_CODEC_VERSION
    version: int = OPERATION_INBOX_CODEC_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.codec_id, str) or _TOKEN_RE.fullmatch(self.codec_id) is None:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        if not _is_plain_int(self.codec_version) or not _is_plain_int(self.version):
            _fail(PersistenceReason.TYPE_INVALID)
        if (
            self.codec_version != OPERATION_INBOX_CODEC_VERSION
            or self.version != OPERATION_INBOX_CODEC_VERSION
        ):
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        _require_sha256(self.schema_sha256)
        try:
            schema_bytes = self.schema_json_ascii.encode("ascii")
        except (AttributeError, UnicodeEncodeError) as exc:
            raise PersistenceError(PersistenceReason.CANONICAL_FORMAT_INVALID) from exc
        fields = _validate_schema(self.schema_json_ascii)
        if sha256_hex(schema_bytes) != self.schema_sha256:
            _fail(PersistenceReason.HASH_INVALID)
        _reject_forbidden_schema_fields(fields)

    @classmethod
    def from_canonical_bytes(cls, value: object) -> CodecDescriptor:
        parsed = parse_canonical_json(value)
        if not isinstance(parsed, Mapping) or frozenset(parsed) != _DESCRIPTOR_KEYS:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        if parsed["domain"] != "v3.10-operation-inbox-codec":
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        descriptor = cls(
            codec_id=parsed["codec_id"],
            codec_version=parsed["codec_version"],
            schema_json_ascii=parsed["schema_json_ascii"],
            schema_sha256=parsed["schema_sha256"],
            version=parsed["version"],
        )
        if descriptor.canonical_bytes != (
            value if isinstance(value, bytes) else value.encode("ascii")
        ):
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        return descriptor

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "codec_id": self.codec_id,
            "codec_version": self.codec_version,
            "domain": "v3.10-operation-inbox-codec",
            "schema_json_ascii": self.schema_json_ascii,
            "schema_sha256": self.schema_sha256,
            "version": self.version,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return sha256_hex(self.canonical_bytes)

    @property
    def fields(self) -> tuple[dict[str, object], ...]:
        fields = _validate_schema(self.schema_json_ascii)
        _reject_forbidden_schema_fields(fields)
        return fields

    def validate_content(self, content_json_ascii: object) -> bytes:
        if not isinstance(content_json_ascii, str):
            _fail(PersistenceReason.TYPE_INVALID)
        try:
            encoded = content_json_ascii.encode("ascii")
        except UnicodeEncodeError as exc:
            raise PersistenceError(PersistenceReason.CANONICAL_FORMAT_INVALID) from exc
        if len(encoded) > MAX_CANONICAL_CONTENT_BYTES:
            _fail(PersistenceReason.SANITIZED_CONTENT_INVALID)
        content = parse_canonical_json(encoded)
        if not isinstance(content, Mapping):
            _fail(PersistenceReason.SANITIZED_CONTENT_INVALID)
        field_map = {field["key"]: field for field in self.fields}
        if any(key.lower() in _FORBIDDEN_CONTENT_KEYS for key in content):
            _fail(PersistenceReason.SENSITIVE_CONTENT_FORBIDDEN)
        required = {key for key, field in field_map.items() if field["required"]}
        if not required.issubset(content) or not set(content).issubset(field_map):
            _fail(PersistenceReason.SANITIZED_CONTENT_INVALID)
        for key, item in content.items():
            field = field_map[key]
            kind = field["kind"]
            valid = False
            if kind == "STRING":
                valid = (
                    isinstance(item, str)
                    and not _contains_surrogate(item)
                    and len(item) <= int(field["max_scalars"])
                    and (
                        field["allowed_values"] is None
                        or item in field["allowed_values"]
                    )
                )
            elif kind == "INTEGER":
                valid = (
                    _is_plain_int(item)
                    and int(field["minimum"]) <= item <= int(field["maximum"])
                )
            elif kind == "BOOLEAN":
                valid = isinstance(item, bool)
            elif kind == "NULL":
                valid = item is None
            if not valid:
                _fail(PersistenceReason.SANITIZED_CONTENT_INVALID)
        return encoded


def normalize_codec_registry(
    values: object,
) -> dict[tuple[str, int], CodecDescriptor]:
    if isinstance(values, Mapping):
        supplied = tuple(values.values())
    elif isinstance(values, Iterable) and not isinstance(values, (str, bytes)):
        supplied = tuple(values)
    else:
        _fail(PersistenceReason.TYPE_INVALID)
    result: dict[tuple[str, int], CodecDescriptor] = {}
    for descriptor in supplied:
        if not isinstance(descriptor, CodecDescriptor):
            _fail(PersistenceReason.TYPE_INVALID)
        checked = CodecDescriptor.from_canonical_bytes(descriptor.canonical_bytes)
        key = (checked.codec_id, checked.codec_version)
        existing = result.get(key)
        if existing is not None and existing.canonical_bytes != checked.canonical_bytes:
            _fail(PersistenceReason.CODEC_UNSUPPORTED)
        result[key] = checked
    return result


def _logical_source_dict(source: SourceIdentity) -> dict[str, object]:
    return {
        "account_scope_sha256": source.account_scope_sha256,
        "domain": "v3.10-operation-inbox-logical-source",
        "source_kind": source.source_kind,
        "source_scope_sha256": source.source_scope_sha256,
        "version": OPERATION_INBOX_VERSION,
    }


@dataclass(frozen=True, slots=True)
class InboxObservation:
    descriptor: CodecDescriptor
    content_json_ascii: str
    source: SourceIdentity
    observed_at: str
    provenance_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, CodecDescriptor):
            _fail(PersistenceReason.TYPE_INVALID)
        if not isinstance(self.source, SourceIdentity):
            _fail(PersistenceReason.TYPE_INVALID)
        checked_source = SourceIdentity.from_canonical_dict(self.source.to_canonical_dict())
        if checked_source.canonical_bytes != self.source.canonical_bytes:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        if not _timestamp_is_valid(self.observed_at):
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        _require_sha256(self.provenance_sha256)
        content = self.descriptor.validate_content(self.content_json_ascii)
        if sha256_hex(content) != self.source.source_content_sha256:
            _fail(PersistenceReason.HASH_INVALID)

    @classmethod
    def create(
        cls,
        *,
        descriptor: CodecDescriptor,
        content: Mapping[str, object],
        source: SourceIdentity,
        observed_at: str,
        provenance_sha256: str,
    ) -> InboxObservation:
        if not isinstance(content, Mapping):
            _fail(PersistenceReason.TYPE_INVALID)
        content_json_ascii = canonical_json_bytes(content).decode("ascii")
        return cls(
            descriptor=descriptor,
            content_json_ascii=content_json_ascii,
            source=source,
            observed_at=observed_at,
            provenance_sha256=provenance_sha256,
        )

    @classmethod
    def from_canonical_bytes(
        cls,
        value: object,
        codec_registry: object,
    ) -> InboxObservation:
        parsed = parse_canonical_json(value)
        if not isinstance(parsed, Mapping) or frozenset(parsed) != _OBSERVATION_KEYS:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        if parsed["domain"] != "v3.10-operation-inbox-observation":
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        version = parsed["version"]
        codec_version = parsed["codec_version"]
        if not _is_plain_int(version) or not _is_plain_int(codec_version):
            _fail(PersistenceReason.TYPE_INVALID)
        if version != OPERATION_INBOX_VERSION or codec_version != OPERATION_INBOX_CODEC_VERSION:
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        for key in (
            "codec_schema_sha256",
            "logical_source_sha256",
            "provenance_sha256",
        ):
            _require_sha256(parsed[key])
        source = SourceIdentity.from_canonical_dict(parsed["source"])
        registry = normalize_codec_registry(codec_registry)
        codec_id = parsed["codec_id"]
        if not isinstance(codec_id, str) or _TOKEN_RE.fullmatch(codec_id) is None:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        descriptor = registry.get((codec_id, codec_version))
        if descriptor is None or descriptor.schema_sha256 != parsed["codec_schema_sha256"]:
            _fail(PersistenceReason.CODEC_UNSUPPORTED)
        observation = cls(
            descriptor=descriptor,
            content_json_ascii=parsed["content_json_ascii"],
            source=source,
            observed_at=parsed["observed_at"],
            provenance_sha256=parsed["provenance_sha256"],
        )
        if parsed["initial_status"] != "OBSERVED":
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        if parsed["logical_source_sha256"] != observation.logical_source_sha256:
            _fail(PersistenceReason.HASH_INVALID)
        supplied = value if isinstance(value, bytes) else value.encode("ascii")
        if observation.canonical_bytes != supplied:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        return observation

    @property
    def logical_source_bytes(self) -> bytes:
        return canonical_json_bytes(_logical_source_dict(self.source))

    @property
    def logical_source_sha256(self) -> str:
        return sha256_hex(self.logical_source_bytes)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "codec_id": self.descriptor.codec_id,
            "codec_schema_sha256": self.descriptor.schema_sha256,
            "codec_version": self.descriptor.codec_version,
            "content_json_ascii": self.content_json_ascii,
            "domain": "v3.10-operation-inbox-observation",
            "initial_status": "OBSERVED",
            "logical_source_sha256": self.logical_source_sha256,
            "observed_at": self.observed_at,
            "provenance_sha256": self.provenance_sha256,
            "source": self.source.to_canonical_dict(),
            "version": OPERATION_INBOX_VERSION,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return sha256_hex(self.canonical_bytes)


@dataclass(frozen=True, slots=True)
class InboxStatusEvent:
    observation_sha256: str
    event_no: int
    from_status: str
    to_status: str
    reason: str
    related_transaction_sha256: str | None = None
    related_bundle_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_sha256(self.observation_sha256)
        if not _is_plain_int(self.event_no) or not 1 <= self.event_no <= MAX_REVISION:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        for value in (self.from_status, self.to_status, self.reason):
            if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
                _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        for value in (
            self.related_transaction_sha256,
            self.related_bundle_sha256,
        ):
            if value is not None:
                _require_sha256(value)

    @classmethod
    def from_canonical_bytes(cls, value: object) -> InboxStatusEvent:
        parsed = parse_canonical_json(value)
        if not isinstance(parsed, Mapping) or frozenset(parsed) != _STATUS_EVENT_KEYS:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        if parsed["domain"] != "v3.10-operation-inbox-status-event":
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        if not _is_plain_int(parsed["version"]):
            _fail(PersistenceReason.TYPE_INVALID)
        if parsed["version"] != OPERATION_INBOX_STATUS_EVENT_VERSION:
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        event = cls(
            observation_sha256=parsed["observation_sha256"],
            event_no=_parse_decimal(parsed["event_no"], positive=True),
            from_status=parsed["from_status"],
            to_status=parsed["to_status"],
            reason=parsed["reason"],
            related_transaction_sha256=parsed["related_transaction_sha256"],
            related_bundle_sha256=parsed["related_bundle_sha256"],
        )
        supplied = value if isinstance(value, bytes) else value.encode("ascii")
        if event.canonical_bytes != supplied:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        return event

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "domain": "v3.10-operation-inbox-status-event",
            "event_no": str(self.event_no),
            "from_status": self.from_status,
            "observation_sha256": self.observation_sha256,
            "reason": self.reason,
            "related_bundle_sha256": self.related_bundle_sha256,
            "related_transaction_sha256": self.related_transaction_sha256,
            "to_status": self.to_status,
            "version": OPERATION_INBOX_STATUS_EVENT_VERSION,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return sha256_hex(self.canonical_bytes)


@dataclass(frozen=True, slots=True)
class LedgerHead:
    ledger_revision: int
    previous_head_sha256: str | None
    transition_kind: str
    transition_sha256: str | None

    def __post_init__(self) -> None:
        _require_revision(self.ledger_revision)
        if self.ledger_revision == 0:
            if (
                self.previous_head_sha256 is not None
                or self.transition_kind != "GENESIS"
                or self.transition_sha256 is not None
            ):
                _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        else:
            if self.transition_kind not in {"TRANSACTION", "CORRECTION_BUNDLE"}:
                _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
            _require_sha256(self.previous_head_sha256)
            _require_sha256(self.transition_sha256)

    @classmethod
    def from_canonical_bytes(cls, value: object) -> LedgerHead:
        parsed = parse_canonical_json(value)
        keys = {
            "domain",
            "ledger_revision",
            "previous_head_sha256",
            "transition_kind",
            "transition_sha256",
            "version",
        }
        if not isinstance(parsed, Mapping) or set(parsed) != keys:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        if parsed["domain"] != "v3.10-cash-ledger-head":
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        if not _is_plain_int(parsed["version"]):
            _fail(PersistenceReason.TYPE_INVALID)
        if parsed["version"] != LEDGER_HEAD_VERSION:
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        head = cls(
            ledger_revision=_parse_decimal(parsed["ledger_revision"]),
            previous_head_sha256=parsed["previous_head_sha256"],
            transition_kind=parsed["transition_kind"],
            transition_sha256=parsed["transition_sha256"],
        )
        supplied = value if isinstance(value, bytes) else value.encode("ascii")
        if head.canonical_bytes != supplied:
            _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
        return head

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "domain": "v3.10-cash-ledger-head",
            "ledger_revision": str(self.ledger_revision),
            "previous_head_sha256": self.previous_head_sha256,
            "transition_kind": self.transition_kind,
            "transition_sha256": self.transition_sha256,
            "version": LEDGER_HEAD_VERSION,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return sha256_hex(self.canonical_bytes)


@dataclass(frozen=True, slots=True)
class StoreSnapshot:
    store_revision: int
    ledger_revision: int
    ledger_head_json_ascii: str
    ledger_head_sha256: str


@dataclass(frozen=True, slots=True)
class BackupVerification:
    path: Path
    store_revision: int
    ledger_revision: int
    ledger_head_sha256: str
    export_sha256: str


_SCHEMA_STATEMENTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "table",
        "cl2_meta",
        "cl2_meta",
        "CREATE TABLE cl2_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), schema_version INTEGER NOT NULL, schema_fingerprint TEXT NOT NULL, store_revision INTEGER NOT NULL CHECK(store_revision>=0), ledger_revision INTEGER NOT NULL CHECK(ledger_revision>=0), ledger_head_json BLOB NOT NULL, ledger_head_sha256 TEXT NOT NULL)",
    ),
    (
        "table",
        "cl2_codec",
        "cl2_codec",
        "CREATE TABLE cl2_codec (sha256 TEXT PRIMARY KEY, codec_id TEXT NOT NULL, codec_version INTEGER NOT NULL, schema_sha256 TEXT NOT NULL, canonical BLOB NOT NULL, UNIQUE(codec_id,codec_version))",
    ),
    (
        "table",
        "cl2_observation",
        "cl2_observation",
        "CREATE TABLE cl2_observation (sha256 TEXT PRIMARY KEY, logical_source_sha256 TEXT NOT NULL UNIQUE, source_sha256 TEXT NOT NULL, codec_sha256 TEXT NOT NULL REFERENCES cl2_codec(sha256), canonical BLOB NOT NULL, initial_status TEXT NOT NULL)",
    ),
    (
        "table",
        "cl2_inbox_status_event",
        "cl2_inbox_status_event",
        "CREATE TABLE cl2_inbox_status_event (observation_sha256 TEXT NOT NULL REFERENCES cl2_observation(sha256), event_no INTEGER NOT NULL, sha256 TEXT NOT NULL UNIQUE, reason TEXT NOT NULL, from_status TEXT NOT NULL, to_status TEXT NOT NULL, related_transaction_sha256 TEXT, related_bundle_sha256 TEXT, canonical BLOB NOT NULL, PRIMARY KEY(observation_sha256,event_no))",
    ),
    (
        "table",
        "cl2_transaction",
        "cl2_transaction",
        "CREATE TABLE cl2_transaction (sha256 TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, economic_sha256 TEXT NOT NULL, transaction_kind TEXT NOT NULL, canonical BLOB NOT NULL)",
    ),
    (
        "table",
        "cl2_posting",
        "cl2_posting",
        "CREATE TABLE cl2_posting (transaction_sha256 TEXT NOT NULL REFERENCES cl2_transaction(sha256), line_no INTEGER NOT NULL, sha256 TEXT NOT NULL, canonical BLOB NOT NULL, PRIMARY KEY(transaction_sha256,line_no))",
    ),
    (
        "table",
        "cl2_transaction_observation",
        "cl2_transaction_observation",
        "CREATE TABLE cl2_transaction_observation (transaction_sha256 TEXT PRIMARY KEY REFERENCES cl2_transaction(sha256), observation_sha256 TEXT NOT NULL REFERENCES cl2_observation(sha256))",
    ),
    (
        "table",
        "cl2_correction_bundle",
        "cl2_correction_bundle",
        "CREATE TABLE cl2_correction_bundle (sha256 TEXT PRIMARY KEY, original_sha256 TEXT NOT NULL UNIQUE REFERENCES cl2_transaction(sha256), reversal_sha256 TEXT NOT NULL UNIQUE REFERENCES cl2_transaction(sha256), correction_sha256 TEXT NOT NULL UNIQUE REFERENCES cl2_transaction(sha256), canonical BLOB NOT NULL)",
    ),
    (
        "table",
        "cl2_ledger_transition",
        "cl2_ledger_transition",
        "CREATE TABLE cl2_ledger_transition (ledger_revision INTEGER PRIMARY KEY, transition_kind TEXT NOT NULL, transition_sha256 TEXT NOT NULL UNIQUE, head_json BLOB NOT NULL, head_sha256 TEXT NOT NULL UNIQUE)",
    ),
    (
        "index",
        "cl2_idx_observation_source",
        "cl2_observation",
        "CREATE INDEX cl2_idx_observation_source ON cl2_observation(logical_source_sha256)",
    ),
    (
        "index",
        "cl2_idx_status_observation",
        "cl2_inbox_status_event",
        "CREATE INDEX cl2_idx_status_observation ON cl2_inbox_status_event(observation_sha256,event_no)",
    ),
    (
        "index",
        "cl2_idx_transaction_source",
        "cl2_transaction",
        "CREATE INDEX cl2_idx_transaction_source ON cl2_transaction(source_sha256)",
    ),
    (
        "index",
        "cl2_idx_transaction_economic",
        "cl2_transaction",
        "CREATE INDEX cl2_idx_transaction_economic ON cl2_transaction(economic_sha256)",
    ),
    (
        "index",
        "cl2_idx_posting_transaction",
        "cl2_posting",
        "CREATE INDEX cl2_idx_posting_transaction ON cl2_posting(transaction_sha256,line_no)",
    ),
    (
        "index",
        "cl2_idx_link_observation",
        "cl2_transaction_observation",
        "CREATE INDEX cl2_idx_link_observation ON cl2_transaction_observation(observation_sha256,transaction_sha256)",
    ),
    (
        "index",
        "cl2_idx_bundle_original",
        "cl2_correction_bundle",
        "CREATE INDEX cl2_idx_bundle_original ON cl2_correction_bundle(original_sha256)",
    ),
)

_EXPECTED_SCHEMA_ROWS = tuple(
    {
        "name": name,
        "sql": sql,
        "tbl_name": table_name,
        "type": object_type,
    }
    for object_type, name, table_name, sql in sorted(_SCHEMA_STATEMENTS)
)
SCHEMA_FINGERPRINT_SHA256 = sha256_hex(canonical_json_bytes(_EXPECTED_SCHEMA_ROWS))


def _path_from(value: object) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        _fail(PersistenceReason.PATH_INVALID)
    try:
        path = Path(value)
    except (TypeError, ValueError) as exc:
        raise PersistenceError(PersistenceReason.PATH_INVALID) from exc
    text = str(path)
    if (
        not path.is_absolute()
        or "\x00" in text
        or text.startswith(("\\\\", "//"))
        or "://" in text
        or any(":" in part for part in path.parts[1:])
    ):
        _fail(PersistenceReason.PATH_INVALID)
    return path


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(
        getattr(details, "st_file_attributes", 0) & 0x400
    )


def _validate_existing_components(path: Path) -> None:
    current = path
    while True:
        if _is_reparse_or_symlink(current):
            _fail(PersistenceReason.PATH_INVALID)
        if current.parent == current:
            break
        current = current.parent


def _validate_target_parent(path: Path) -> None:
    _validate_existing_components(path.parent)
    if not path.parent.exists() or not path.parent.is_dir():
        _fail(PersistenceReason.PATH_INVALID)


def _require_absent_target(path: Path, staging: Path) -> None:
    for candidate in (path, staging):
        if _is_reparse_or_symlink(candidate):
            _fail(PersistenceReason.PATH_INVALID)
    if path.exists() or staging.exists():
        _fail(PersistenceReason.PATH_COLLISION)


def _reject_overlapping_roots(source: Path, *destinations: Path) -> None:
    try:
        source_resolved = source.resolve(strict=False)
        destination_roots = tuple(
            destination.resolve(strict=False) for destination in destinations
        )
    except OSError as exc:
        raise PersistenceError(PersistenceReason.PATH_INVALID) from exc
    for destination in destination_roots:
        if (
            destination == source_resolved
            or source_resolved in destination.parents
            or destination in source_resolved.parents
        ):
            _fail(PersistenceReason.PATH_INVALID)


def _promote_staging(staging: Path, target: Path) -> None:
    _validate_target_parent(target)
    if _is_reparse_or_symlink(target):
        _fail(PersistenceReason.PATH_INVALID)
    if target.exists():
        _fail(PersistenceReason.PATH_COLLISION)
    if _is_reparse_or_symlink(staging) or not staging.is_dir():
        _fail(PersistenceReason.PATH_INVALID)
    try:
        os.rename(staging, target)
    except OSError as exc:
        if _is_reparse_or_symlink(target):
            raise PersistenceError(PersistenceReason.PATH_INVALID) from exc
        if target.exists():
            raise PersistenceError(PersistenceReason.PATH_COLLISION) from exc
        raise


def _validate_database_file(path: Path) -> None:
    if _is_reparse_or_symlink(path) or not path.is_file():
        _fail(PersistenceReason.PATH_INVALID)
    try:
        if path.stat().st_nlink != 1:
            _fail(PersistenceReason.PATH_INVALID)
    except OSError as exc:
        raise PersistenceError(PersistenceReason.PATH_INVALID) from exc


def _wal_checksum(
    data: bytes,
    *,
    little_endian: bool,
    seed: tuple[int, int] = (0, 0),
) -> tuple[int, int]:
    if len(data) % 8:
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    byte_order = "<" if little_endian else ">"
    words = struct.unpack(f"{byte_order}{len(data) // 4}I", data)
    first, second = seed
    for index in range(0, len(words), 2):
        first = (first + words[index] + second) & 0xFFFFFFFF
        second = (second + words[index + 1] + first) & 0xFFFFFFFF
    return first, second


@dataclass(frozen=True, slots=True)
class _WalMetadata:
    data: bytes
    page_size: int
    magic: int
    page_numbers: tuple[int, ...]
    frame_checksums: tuple[tuple[int, int], ...]
    commit_sizes: tuple[int, ...]


def _validate_wal(database: Path, wal: Path) -> _WalMetadata | None:
    try:
        with database.open("rb") as handle:
            database_header = handle.read(100)
        data = wal.read_bytes()
    except OSError as exc:
        raise PersistenceError(PersistenceReason.WAL_SIDECAR_INCONSISTENT) from exc
    if not data:
        return None
    if len(data) < 32:
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    magic = int.from_bytes(data[0:4], "big")
    if magic not in {0x377F0682, 0x377F0683}:
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    if int.from_bytes(data[4:8], "big") != 3_007_000:
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    page_size = int.from_bytes(data[8:12], "big") or 65_536
    database_page_size = int.from_bytes(database_header[16:18], "big")
    if database_page_size == 1:
        database_page_size = 65_536
    if (
        page_size != database_page_size
        or page_size < 512
        or page_size > 65_536
        or page_size & (page_size - 1)
        or (len(data) - 32) % (page_size + 24)
    ):
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    little_endian = magic == 0x377F0682
    checksum = _wal_checksum(data[:24], little_endian=little_endian)
    if checksum != struct.unpack(">II", data[24:32]):
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    salts = data[16:24]
    offset = 32
    page_numbers: list[int] = []
    frame_checksums: list[tuple[int, int]] = []
    commit_sizes: list[int] = []
    while offset < len(data):
        frame_header = data[offset : offset + 24]
        page = data[offset + 24 : offset + 24 + page_size]
        page_number = int.from_bytes(frame_header[:4], "big")
        commit_size = int.from_bytes(frame_header[4:8], "big")
        if (
            page_number == 0
            or frame_header[8:16] != salts
            or (commit_size != 0 and page_number > commit_size)
        ):
            _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
        checksum = _wal_checksum(
            frame_header[:8] + page,
            little_endian=little_endian,
            seed=checksum,
        )
        if checksum != struct.unpack(">II", frame_header[16:24]):
            _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
        page_numbers.append(page_number)
        frame_checksums.append(checksum)
        commit_sizes.append(commit_size)
        offset += page_size + 24
    if not any(commit_sizes):
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    return _WalMetadata(
        data=data,
        page_size=page_size,
        magic=magic,
        page_numbers=tuple(page_numbers),
        frame_checksums=tuple(frame_checksums),
        commit_sizes=tuple(commit_sizes),
    )


def _validate_shm(metadata: _WalMetadata, shared_memory: Path) -> None:
    try:
        data = shared_memory.read_bytes()
    except OSError as exc:
        raise PersistenceError(PersistenceReason.WAL_SIDECAR_INCONSISTENT) from exc
    if len(data) < 32_768 or len(data) % 32_768:
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    header = data[:48]
    if header != data[48:96]:
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    try:
        (
            version,
            unused,
            _change,
            is_initialized,
            big_endian_checksum,
            encoded_page_size,
            max_frame,
            database_pages,
            frame_checksum_1,
            frame_checksum_2,
            _salt_1,
            _salt_2,
            header_checksum_1,
            header_checksum_2,
        ) = struct.unpack("=IIIBBHIIIIIIII", header)
    except struct.error as exc:
        raise PersistenceError(PersistenceReason.WAL_SIDECAR_INCONSISTENT) from exc
    native_little_endian = struct.pack("=I", 1)[0] == 1
    decoded_page_size = 65_536 if encoded_page_size == 1 else encoded_page_size
    if (
        version != 3_007_000
        or unused != 0
        or is_initialized != 1
        or big_endian_checksum != (metadata.magic & 1)
        or decoded_page_size != metadata.page_size
        or max_frame == 0
        or max_frame > len(metadata.page_numbers)
        or database_pages == 0
        or data[32:40] != metadata.data[16:24]
        or _wal_checksum(
            header[:40], little_endian=native_little_endian
        )
        != (header_checksum_1, header_checksum_2)
        or metadata.frame_checksums[max_frame - 1]
        != (frame_checksum_1, frame_checksum_2)
        or metadata.commit_sizes[max_frame - 1] != database_pages
    ):
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    try:
        checkpoint = struct.unpack("=I5I8sII", data[96:136])
    except struct.error as exc:
        raise PersistenceError(PersistenceReason.WAL_SIDECAR_INCONSISTENT) from exc
    n_backfill, *checkpoint_tail = checkpoint
    read_marks = checkpoint_tail[:5]
    lock_bytes = checkpoint_tail[5]
    n_backfill_attempted = checkpoint_tail[6]
    unused_checkpoint = checkpoint_tail[7]
    if (
        n_backfill > max_frame
        or any(mark != 0xFFFFFFFF and mark > max_frame for mark in read_marks)
        or lock_bytes != b"\x00" * 8
        or n_backfill_attempted > max_frame
        or unused_checkpoint != 0
    ):
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)

    block_count = 1 + max(0, max_frame - 4_062 + 4_095) // 4_096
    if len(data) < block_count * 32_768:
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    for block in range(block_count):
        first_frame = 1 if block == 0 else 4_063 + (block - 1) * 4_096
        frame_limit = min(max_frame, 4_062 if block == 0 else first_frame + 4_095)
        frame_zero = 0 if block == 0 else first_frame - 1
        block_offset = block * 32_768
        page_array_offset = 136 if block == 0 else block_offset
        page_capacity = 4_062 if block == 0 else 4_096
        hash_offset = block_offset + 16_384
        expected_hash = [0] * 8_192
        for frame_number in range(first_frame, frame_limit + 1):
            page_number = metadata.page_numbers[frame_number - 1]
            page_index = frame_number - first_frame
            actual_page = struct.unpack_from(
                "=I", data, page_array_offset + page_index * 4
            )[0]
            if actual_page != page_number:
                _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
            slot = (page_number * 383) & 8_191
            while expected_hash[slot]:
                slot = (slot + 1) & 8_191
            expected_hash[slot] = frame_number - frame_zero
        actual_hash = struct.unpack_from("=8192H", data, hash_offset)
        if tuple(expected_hash) != actual_hash:
            _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
        if frame_limit - first_frame + 1 > page_capacity:
            _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)


def _validate_empty_shm(database: Path, shared_memory: Path) -> None:
    try:
        data = shared_memory.read_bytes()
        with database.open("rb") as handle:
            database_header = handle.read(100)
    except OSError as exc:
        raise PersistenceError(PersistenceReason.WAL_SIDECAR_INCONSISTENT) from exc
    if len(data) < 32_768 or len(data) % 32_768 or data[:48] != data[48:96]:
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    header = data[:48]
    try:
        fields = struct.unpack("=IIIBBHIIIIIIII", header)
        checkpoint = struct.unpack("=I5I8sII", data[96:136])
    except struct.error as exc:
        raise PersistenceError(PersistenceReason.WAL_SIDECAR_INCONSISTENT) from exc
    database_page_size = int.from_bytes(database_header[16:18], "big")
    if database_page_size == 1:
        database_page_size = 65_536
    encoded_page_size = fields[5]
    decoded_page_size = 65_536 if encoded_page_size == 1 else encoded_page_size
    native_little_endian = struct.pack("=I", 1)[0] == 1
    read_marks = checkpoint[1:6]
    if (
        fields[0] != 3_007_000
        or fields[1] != 0
        or fields[3] != 1
        or decoded_page_size not in {0, database_page_size}
        or fields[6] != 0
        or _wal_checksum(header[:40], little_endian=native_little_endian)
        != fields[12:14]
        or checkpoint[0] != 0
        or any(mark not in {0, 0xFFFFFFFF} for mark in read_marks)
        or checkpoint[6] != b"\x00" * 8
        or checkpoint[7] != 0
        or checkpoint[8] != 0
    ):
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    for block in range(len(data) // 32_768):
        block_offset = block * 32_768
        page_array_offset = 136 if block == 0 else block_offset
        page_capacity = 4_062 if block == 0 else 4_096
        actual_hash = struct.unpack_from(
            "=8192H", data, block_offset + 16_384
        )
        occupied = sorted(value for value in actual_hash if value)
        if not occupied:
            continue
        entry_count = occupied[-1]
        if entry_count > page_capacity or occupied != list(
            range(1, entry_count + 1)
        ):
            _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
        page_numbers = struct.unpack_from(
            f"={entry_count}I", data, page_array_offset
        )
        if any(page_number == 0 for page_number in page_numbers):
            _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
        expected_hash = [0] * 8_192
        for entry, page_number in enumerate(page_numbers, 1):
            slot = (page_number * 383) & 8_191
            while expected_hash[slot]:
                slot = (slot + 1) & 8_191
            expected_hash[slot] = entry
        if tuple(expected_hash) != actual_hash:
            _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)


def _validate_busy_timeout(value: object) -> int:
    if not _is_plain_int(value):
        _fail(PersistenceReason.TYPE_INVALID)
    if not 0 <= value <= 60_000:
        _fail(PersistenceReason.CANONICAL_FORMAT_INVALID)
    return value


def _validate_live_root(root: Path) -> Path:
    _validate_existing_components(root)
    if not root.exists():
        _fail(PersistenceReason.STORE_MISSING)
    if not root.is_dir() or _is_reparse_or_symlink(root):
        _fail(PersistenceReason.PATH_INVALID)
    database = root / "store.sqlite3"
    if not database.exists():
        _fail(PersistenceReason.STORE_MISSING)
    _validate_database_file(database)
    allowed = {
        "store.sqlite3",
        "store.sqlite3-wal",
        "store.sqlite3-shm",
        "store.sqlite3-journal",
    }
    try:
        children = {child.name for child in root.iterdir()}
    except OSError as exc:
        raise PersistenceError(PersistenceReason.IO_FAILURE) from exc
    if not children.issubset(allowed):
        _fail(PersistenceReason.PATH_INVALID)
    wal = root / "store.sqlite3-wal"
    shared_memory = root / "store.sqlite3-shm"
    journal = root / "store.sqlite3-journal"
    if journal.exists() or (shared_memory.exists() != wal.exists()):
        _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
    for sidecar in (wal, shared_memory):
        if sidecar.exists():
            if _is_reparse_or_symlink(sidecar) or not sidecar.is_file():
                _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
            try:
                if sidecar.stat().st_nlink != 1:
                    _fail(PersistenceReason.WAL_SIDECAR_INCONSISTENT)
            except OSError as exc:
                raise PersistenceError(
                    PersistenceReason.WAL_SIDECAR_INCONSISTENT
                ) from exc
    if wal.exists():
        metadata = _validate_wal(database, wal)
        if metadata is None:
            _validate_empty_shm(database, shared_memory)
        else:
            _validate_shm(metadata, shared_memory)
    return database


def _validate_sqlite_header(database: Path) -> None:
    try:
        with database.open("rb") as handle:
            header = handle.read(100)
    except OSError as exc:
        raise PersistenceError(PersistenceReason.IO_FAILURE) from exc
    if len(header) < 100 or header[:16] != b"SQLite format 3\x00":
        _fail(PersistenceReason.INTEGRITY_FAILURE)


def _connect(database: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            database,
            timeout=busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        trusted = connection.execute("PRAGMA trusted_schema").fetchone()
        if trusted is None or trusted[0] != 0:
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        return connection
    except PersistenceError:
        try:
            connection.close()
        except (NameError, sqlite3.Error):
            pass
        raise
    except sqlite3.Error as exc:
        raise PersistenceError(PersistenceReason.IO_FAILURE) from exc


def _schema_rows(connection: sqlite3.Connection) -> tuple[dict[str, object], ...]:
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema "
        "ORDER BY type,name,tbl_name"
    ).fetchall()
    allowed_tables = {
        name for object_type, name, _, _ in _SCHEMA_STATEMENTS if object_type == "table"
    }
    application_rows: list[dict[str, object]] = []
    for row in rows:
        name = row["name"]
        if name.startswith("sqlite_"):
            if (
                row["type"] != "index"
                or row["sql"] is not None
                or row["tbl_name"] not in allowed_tables
                or not name.startswith(f"sqlite_autoindex_{row['tbl_name']}_")
            ):
                _fail(PersistenceReason.SCHEMA_INVALID)
            continue
        if row["sql"] is None:
            _fail(PersistenceReason.SCHEMA_INVALID)
        application_rows.append(
            {
                "name": name,
                "sql": row["sql"],
                "tbl_name": row["tbl_name"],
                "type": row["type"],
            }
        )
    return tuple(application_rows)


def _validate_schema_objects(connection: sqlite3.Connection) -> None:
    try:
        rows = _schema_rows(connection)
    except sqlite3.Error as exc:
        raise PersistenceError(PersistenceReason.SCHEMA_INVALID) from exc
    if rows != _EXPECTED_SCHEMA_ROWS:
        _fail(PersistenceReason.SCHEMA_INVALID)
    if sha256_hex(canonical_json_bytes(rows)) != SCHEMA_FINGERPRINT_SHA256:
        _fail(PersistenceReason.SCHEMA_INVALID)


def _blob(value: object) -> bytes:
    if not isinstance(value, bytes):
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
    return value


def _parse_transaction(value: object) -> LedgerTransaction:
    raw = _blob(value)
    parsed = parse_canonical_json(raw)
    transaction = LedgerTransaction.from_canonical_dict(parsed)
    if transaction.canonical_bytes != raw:
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
    return transaction


def _transaction_kind(transaction: LedgerTransaction) -> str:
    if transaction.classification is LedgerClassification.REVERSAL:
        return "REVERSAL"
    if transaction.corrects_sha256 is not None:
        return "CORRECTION"
    return "ORDINARY"


def _sqlite_reason(exc: sqlite3.Error) -> PersistenceReason:
    error_code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(error_code, int) and error_code & 0xFF in {
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
    }:
        return PersistenceReason.STORE_BUSY
    return PersistenceReason.IO_FAILURE


class CashLedgerStore:
    """Explicit local handle for one validated CL2 store."""

    def __init__(
        self,
        *,
        root: Path,
        connection: sqlite3.Connection,
        registry: dict[tuple[str, int], CodecDescriptor],
        busy_timeout_ms: int,
        fault_injector: Callable[[str], None] | None,
    ) -> None:
        self._root = root
        self._connection = connection
        self._registry = registry
        self._busy_timeout_ms = busy_timeout_ms
        self._fault_injector = fault_injector
        self._closed = False

    @classmethod
    def create(
        cls,
        root: object,
        codec_registry: object,
        *,
        busy_timeout_ms: int = 0,
        fault_injector: Callable[[str], None] | None = None,
    ) -> CashLedgerStore:
        path = _path_from(root)
        timeout = _validate_busy_timeout(busy_timeout_ms)
        registry = normalize_codec_registry(codec_registry)
        _validate_target_parent(path)
        staging = path.with_name(f"{path.name}.cl2-create-staging")
        _require_absent_target(path, staging)
        try:
            staging.mkdir()
            if fault_injector is not None:
                fault_injector("create.after_staging")
            database = staging / "store.sqlite3"
            connection = _connect(database, timeout)
            try:
                mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if str(mode).lower() != "wal":
                    _fail(PersistenceReason.VERSION_UNSUPPORTED)
                connection.execute(f"PRAGMA application_id={SQLITE_APPLICATION_ID}")
                connection.execute(
                    f"PRAGMA user_version={CASH_LEDGER_STORE_SCHEMA_VERSION}"
                )
                connection.execute("BEGIN IMMEDIATE")
                for _, _, _, statement in _SCHEMA_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO cl2_meta VALUES (1,?,?,?,?,?,?)",
                    (
                        CASH_LEDGER_STORE_SCHEMA_VERSION,
                        SCHEMA_FINGERPRINT_SHA256,
                        0,
                        0,
                        GENESIS_HEAD_JSON_ASCII.encode("ascii"),
                        GENESIS_HEAD_SHA256,
                    ),
                )
                if fault_injector is not None:
                    fault_injector("create.before_commit")
                connection.execute("COMMIT")
                _validate_connection(connection, registry)
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
            if fault_injector is not None:
                fault_injector("create.before_promote")
            _promote_staging(staging, path)
        except InjectedFault as exc:
            raise PersistenceError(PersistenceReason.INTERRUPTED_TRANSACTION) from exc
        except PersistenceError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PersistenceError(PersistenceReason.IO_FAILURE) from exc
        return cls.open(
            path,
            registry.values(),
            busy_timeout_ms=timeout,
            fault_injector=fault_injector,
        )

    @classmethod
    def open(
        cls,
        root: object,
        codec_registry: object,
        *,
        busy_timeout_ms: int = 0,
        fault_injector: Callable[[str], None] | None = None,
    ) -> CashLedgerStore:
        path = _path_from(root)
        timeout = _validate_busy_timeout(busy_timeout_ms)
        registry = normalize_codec_registry(codec_registry)
        database = _validate_live_root(path)
        _validate_sqlite_header(database)
        connection = _connect(database, timeout)
        try:
            if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                _fail(PersistenceReason.VERSION_UNSUPPORTED)
            _validate_connection(connection, registry)
        except PersistenceError:
            connection.close()
            raise
        except sqlite3.DatabaseError as exc:
            connection.close()
            reason = (
                PersistenceReason.WAL_SIDECAR_INCONSISTENT
                if (path / "store.sqlite3-wal").exists()
                else PersistenceReason.INTEGRITY_FAILURE
            )
            raise PersistenceError(reason) from exc
        return cls(
            root=path,
            connection=connection,
            registry=registry,
            busy_timeout_ms=timeout,
            fault_injector=fault_injector,
        )

    @property
    def root(self) -> Path:
        return self._root

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> Self:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self._closed:
            _fail(PersistenceReason.STORE_CLOSED)

    def _fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._connection.close()
        finally:
            self._closed = True

    def validate(self) -> StoreSnapshot:
        self._ensure_open()
        _validate_live_root(self._root)
        _validate_connection(self._connection, self._registry)
        return self.snapshot()

    def snapshot(self) -> StoreSnapshot:
        self._ensure_open()
        try:
            self._connection.execute("BEGIN")
            row = self._connection.execute(
                "SELECT store_revision,ledger_revision,ledger_head_json,"
                "ledger_head_sha256 FROM cl2_meta WHERE singleton=1"
            ).fetchone()
            if row is None:
                _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
            result = StoreSnapshot(
                store_revision=row["store_revision"],
                ledger_revision=row["ledger_revision"],
                ledger_head_json_ascii=_blob(row["ledger_head_json"]).decode("ascii"),
                ledger_head_sha256=row["ledger_head_sha256"],
            )
            self._connection.execute("COMMIT")
            return result
        except PersistenceError:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise PersistenceError(_sqlite_reason(exc)) from exc

    def _begin_mutation(self, operation: str) -> None:
        self._ensure_open()
        try:
            self._fault(f"{operation}.before_transaction")
            self._connection.execute("BEGIN IMMEDIATE")
        except InjectedFault as exc:
            raise PersistenceError(PersistenceReason.INTERRUPTED_TRANSACTION) from exc
        except sqlite3.Error as exc:
            raise PersistenceError(_sqlite_reason(exc)) from exc

    def _abort_mutation(self) -> None:
        if self._connection.in_transaction:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass

    def _finish_mutation(self, operation: str) -> None:
        try:
            self._fault(f"{operation}.before_commit")
            self._connection.execute("COMMIT")
        except InjectedFault as exc:
            self._abort_mutation()
            raise PersistenceError(PersistenceReason.INTERRUPTED_TRANSACTION) from exc
        except sqlite3.Error as exc:
            self._abort_mutation()
            raise PersistenceError(_sqlite_reason(exc)) from exc
        try:
            self._fault(f"{operation}.after_commit")
        except InjectedFault as exc:
            raise PersistenceError(PersistenceReason.INTERRUPTED_TRANSACTION) from exc

    def _meta_revisions(self) -> tuple[int, int]:
        row = self._connection.execute(
            "SELECT store_revision,ledger_revision FROM cl2_meta WHERE singleton=1"
        ).fetchone()
        if row is None:
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        return row["store_revision"], row["ledger_revision"]

    def _current_status(self, observation_sha256: str) -> tuple[str, int]:
        row = self._connection.execute(
            "SELECT to_status,event_no FROM cl2_inbox_status_event "
            "WHERE observation_sha256=? ORDER BY event_no DESC LIMIT 1",
            (observation_sha256,),
        ).fetchone()
        if row is None:
            return "OBSERVED", 0
        return row["to_status"], row["event_no"]

    def append_observation(
        self,
        observation: InboxObservation,
        *,
        expected_store_revision: int,
    ) -> PersistenceDisposition:
        if not isinstance(observation, InboxObservation):
            _fail(PersistenceReason.TYPE_INVALID)
        checked = InboxObservation.from_canonical_bytes(
            observation.canonical_bytes,
            self._registry.values(),
        )
        expected = _require_revision(expected_store_revision)
        self._begin_mutation("append_observation")
        try:
            existing = self._connection.execute(
                "SELECT canonical,logical_source_sha256 FROM cl2_observation WHERE sha256=?",
                (checked.sha256,),
            ).fetchone()
            if existing is not None:
                if (
                    _blob(existing["canonical"]) == checked.canonical_bytes
                    and existing["logical_source_sha256"]
                    == checked.logical_source_sha256
                ):
                    self._connection.execute("ROLLBACK")
                    return PersistenceDisposition.OBSERVATION_ALREADY_PRESENT
                _fail(PersistenceReason.SOURCE_CONTENT_CONFLICT)
            store_revision, _ = self._meta_revisions()
            if store_revision != expected:
                _fail(PersistenceReason.REVISION_MISMATCH)
            source_row = self._connection.execute(
                "SELECT canonical FROM cl2_observation WHERE logical_source_sha256=?",
                (checked.logical_source_sha256,),
            ).fetchone()
            if source_row is not None:
                _fail(PersistenceReason.SOURCE_CONTENT_CONFLICT)
            if store_revision == MAX_REVISION:
                _fail(PersistenceReason.REVISION_EXHAUSTED)
            descriptor = checked.descriptor
            codec_row = self._connection.execute(
                "SELECT canonical FROM cl2_codec WHERE sha256=?", (descriptor.sha256,)
            ).fetchone()
            if codec_row is None:
                self._connection.execute(
                    "INSERT INTO cl2_codec VALUES (?,?,?,?,?)",
                    (
                        descriptor.sha256,
                        descriptor.codec_id,
                        descriptor.codec_version,
                        descriptor.schema_sha256,
                        descriptor.canonical_bytes,
                    ),
                )
            elif _blob(codec_row["canonical"]) != descriptor.canonical_bytes:
                _fail(PersistenceReason.CODEC_UNSUPPORTED)
            self._fault("append_observation.after_codec")
            self._connection.execute(
                "INSERT INTO cl2_observation VALUES (?,?,?,?,?,?)",
                (
                    checked.sha256,
                    checked.logical_source_sha256,
                    checked.source.sha256,
                    descriptor.sha256,
                    checked.canonical_bytes,
                    "OBSERVED",
                ),
            )
            self._fault("append_observation.after_observation")
            self._connection.execute(
                "UPDATE cl2_meta SET store_revision=? WHERE singleton=1",
                (store_revision + 1,),
            )
            self._fault("append_observation.after_meta")
            self._finish_mutation("append_observation")
            return PersistenceDisposition.OBSERVATION_STORED
        except PersistenceError:
            self._abort_mutation()
            raise
        except InjectedFault as exc:
            self._abort_mutation()
            raise PersistenceError(PersistenceReason.INTERRUPTED_TRANSACTION) from exc
        except sqlite3.Error as exc:
            self._abort_mutation()
            raise PersistenceError(_sqlite_reason(exc)) from exc

    def append_status_event(
        self,
        event: InboxStatusEvent,
        *,
        expected_store_revision: int,
    ) -> PersistenceDisposition:
        if not isinstance(event, InboxStatusEvent):
            _fail(PersistenceReason.TYPE_INVALID)
        checked = InboxStatusEvent.from_canonical_bytes(event.canonical_bytes)
        expected = _require_revision(expected_store_revision)
        self._begin_mutation("append_status_event")
        try:
            existing = self._connection.execute(
                "SELECT canonical FROM cl2_inbox_status_event "
                "WHERE observation_sha256=? AND event_no=?",
                (checked.observation_sha256, checked.event_no),
            ).fetchone()
            if existing is not None:
                if _blob(existing["canonical"]) == checked.canonical_bytes:
                    self._connection.execute("ROLLBACK")
                    return PersistenceDisposition.STATUS_EVENT_ALREADY_PRESENT
                _fail(PersistenceReason.STATUS_TRANSITION_INVALID)
            store_revision, _ = self._meta_revisions()
            if store_revision != expected:
                _fail(PersistenceReason.REVISION_MISMATCH)
            observation = self._connection.execute(
                "SELECT 1 FROM cl2_observation WHERE sha256=?",
                (checked.observation_sha256,),
            ).fetchone()
            if observation is None:
                _fail(PersistenceReason.OBSERVATION_NOT_FOUND)
            current_status, last_event = self._current_status(checked.observation_sha256)
            if not _valid_nonledger_status_event(checked, current_status, last_event):
                _fail(PersistenceReason.STATUS_TRANSITION_INVALID)
            if store_revision == MAX_REVISION:
                _fail(PersistenceReason.REVISION_EXHAUSTED)
            self._insert_status_event(checked)
            self._fault("append_status_event.after_event")
            self._connection.execute(
                "UPDATE cl2_meta SET store_revision=? WHERE singleton=1",
                (store_revision + 1,),
            )
            self._fault("append_status_event.after_meta")
            self._finish_mutation("append_status_event")
            return PersistenceDisposition.STATUS_EVENT_APPENDED
        except PersistenceError:
            self._abort_mutation()
            raise
        except InjectedFault as exc:
            self._abort_mutation()
            raise PersistenceError(PersistenceReason.INTERRUPTED_TRANSACTION) from exc
        except sqlite3.Error as exc:
            self._abort_mutation()
            raise PersistenceError(_sqlite_reason(exc)) from exc

    def _insert_status_event(self, event: InboxStatusEvent) -> None:
        self._connection.execute(
            "INSERT INTO cl2_inbox_status_event VALUES (?,?,?,?,?,?,?,?,?)",
            (
                event.observation_sha256,
                event.event_no,
                event.sha256,
                event.reason,
                event.from_status,
                event.to_status,
                event.related_transaction_sha256,
                event.related_bundle_sha256,
                event.canonical_bytes,
            ),
        )

    def _insert_transaction(self, transaction: LedgerTransaction, kind: str) -> None:
        self._connection.execute(
            "INSERT INTO cl2_transaction VALUES (?,?,?,?,?)",
            (
                transaction.sha256,
                transaction.source_sha256,
                transaction.economic_sha256,
                kind,
                transaction.canonical_bytes,
            ),
        )
        for posting in transaction.postings:
            self._connection.execute(
                "INSERT INTO cl2_posting VALUES (?,?,?,?)",
                (
                    transaction.sha256,
                    posting.line_no,
                    posting.sha256,
                    posting.canonical_bytes,
                ),
            )

    def _advance_head(
        self,
        *,
        store_revision: int,
        ledger_revision: int,
        transition_kind: str,
        transition_sha256: str,
    ) -> LedgerHead:
        row = self._connection.execute(
            "SELECT ledger_head_sha256 FROM cl2_meta WHERE singleton=1"
        ).fetchone()
        if row is None or not _is_sha256(row["ledger_head_sha256"]):
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        head = LedgerHead(
            ledger_revision=ledger_revision + 1,
            previous_head_sha256=row["ledger_head_sha256"],
            transition_kind=transition_kind,
            transition_sha256=transition_sha256,
        )
        self._connection.execute(
            "INSERT INTO cl2_ledger_transition VALUES (?,?,?,?,?)",
            (
                head.ledger_revision,
                transition_kind,
                transition_sha256,
                head.canonical_bytes,
                head.sha256,
            ),
        )
        self._connection.execute(
            "UPDATE cl2_meta SET store_revision=?,ledger_revision=?,"
            "ledger_head_json=?,ledger_head_sha256=? WHERE singleton=1",
            (
                store_revision + 1,
                ledger_revision + 1,
                head.canonical_bytes,
                head.sha256,
            ),
        )
        return head

    def append_transaction(
        self,
        transaction: LedgerTransaction,
        observation_sha256: str,
        *,
        expected_store_revision: int,
        expected_ledger_revision: int,
    ) -> PersistenceDisposition:
        if not isinstance(transaction, LedgerTransaction):
            _fail(PersistenceReason.TYPE_INVALID)
        checked = LedgerTransaction.from_canonical_dict(transaction.to_canonical_dict())
        observation_hash = _require_sha256(observation_sha256)
        expected_store = _require_revision(expected_store_revision)
        expected_ledger = _require_revision(expected_ledger_revision)
        self._begin_mutation("append_transaction")
        try:
            existing = self._connection.execute(
                "SELECT canonical FROM cl2_transaction WHERE sha256=?",
                (checked.sha256,),
            ).fetchone()
            if existing is not None:
                link = self._connection.execute(
                    "SELECT observation_sha256 FROM cl2_transaction_observation "
                    "WHERE transaction_sha256=?",
                    (checked.sha256,),
                ).fetchone()
                if (
                    _blob(existing["canonical"]) == checked.canonical_bytes
                    and link is not None
                    and link["observation_sha256"] == observation_hash
                ):
                    self._connection.execute("ROLLBACK")
                    return PersistenceDisposition.TRANSACTION_ALREADY_PRESENT
                _fail(PersistenceReason.SOURCE_CONFLICT)
            store_revision, ledger_revision = self._meta_revisions()
            if store_revision != expected_store or ledger_revision != expected_ledger:
                _fail(PersistenceReason.REVISION_MISMATCH)
            observation_row = self._connection.execute(
                "SELECT canonical FROM cl2_observation WHERE sha256=?",
                (observation_hash,),
            ).fetchone()
            if observation_row is None:
                _fail(PersistenceReason.OBSERVATION_NOT_FOUND)
            if _transaction_kind(checked) != "ORDINARY":
                _fail(PersistenceReason.LINEAGE_CONFLICT)
            observation = InboxObservation.from_canonical_bytes(
                _blob(observation_row["canonical"]), self._registry.values()
            )
            if observation.source.canonical_bytes != checked.source.canonical_bytes:
                _fail(PersistenceReason.SOURCE_CONFLICT)
            for row in self._connection.execute(
                "SELECT canonical FROM cl2_transaction ORDER BY sha256"
            ):
                trusted = _parse_transaction(row["canonical"])
                relation = compare_transaction_identities(checked, trusted)
                if relation is IdentityRelation.SOURCE_CONFLICT:
                    _fail(PersistenceReason.SOURCE_CONFLICT)
                if relation is IdentityRelation.ECONOMIC_MATCH:
                    _fail(PersistenceReason.ECONOMIC_MATCH_REVIEW_REQUIRED)
            current_status, last_event = self._current_status(observation_hash)
            if current_status not in {"OBSERVED", "REVIEW_REQUIRED"}:
                _fail(PersistenceReason.STATUS_TRANSITION_INVALID)
            if store_revision == MAX_REVISION or ledger_revision == MAX_REVISION:
                _fail(PersistenceReason.REVISION_EXHAUSTED)
            self._insert_transaction(checked, "ORDINARY")
            self._fault("append_transaction.after_transaction")
            self._connection.execute(
                "INSERT INTO cl2_transaction_observation VALUES (?,?)",
                (checked.sha256, observation_hash),
            )
            event = InboxStatusEvent(
                observation_sha256=observation_hash,
                event_no=last_event + 1,
                from_status=current_status,
                to_status="LEDGER_LINKED",
                reason="LEDGER_TRANSACTION_ACCEPTED",
                related_transaction_sha256=checked.sha256,
            )
            self._insert_status_event(event)
            self._fault("append_transaction.after_provenance")
            self._advance_head(
                store_revision=store_revision,
                ledger_revision=ledger_revision,
                transition_kind="TRANSACTION",
                transition_sha256=checked.sha256,
            )
            self._fault("append_transaction.after_meta")
            self._finish_mutation("append_transaction")
            return PersistenceDisposition.TRANSACTION_APPENDED
        except PersistenceError:
            self._abort_mutation()
            raise
        except InjectedFault as exc:
            self._abort_mutation()
            raise PersistenceError(PersistenceReason.INTERRUPTED_TRANSACTION) from exc
        except (sqlite3.Error, LedgerError) as exc:
            self._abort_mutation()
            if isinstance(exc, LedgerError):
                raise
            raise PersistenceError(_sqlite_reason(exc)) from exc

    def append_correction_bundle(
        self,
        bundle: LedgerCorrectionBundle,
        reversal_observation_sha256: str,
        correction_observation_sha256: str,
        *,
        expected_store_revision: int,
        expected_ledger_revision: int,
    ) -> PersistenceDisposition:
        if not isinstance(bundle, LedgerCorrectionBundle):
            _fail(PersistenceReason.TYPE_INVALID)
        checked = LedgerCorrectionBundle(
            original=bundle.original,
            reversal=bundle.reversal,
            correction=bundle.correction,
        )
        reversal_observation = _require_sha256(reversal_observation_sha256)
        correction_observation = _require_sha256(correction_observation_sha256)
        expected_store = _require_revision(expected_store_revision)
        expected_ledger = _require_revision(expected_ledger_revision)
        self._begin_mutation("append_correction_bundle")
        try:
            existing_bundle = self._connection.execute(
                "SELECT canonical,reversal_sha256,correction_sha256 "
                "FROM cl2_correction_bundle WHERE sha256=?",
                (checked.sha256,),
            ).fetchone()
            if existing_bundle is not None:
                reversal_link = self._connection.execute(
                    "SELECT observation_sha256 FROM cl2_transaction_observation "
                    "WHERE transaction_sha256=?",
                    (checked.reversal.sha256,),
                ).fetchone()
                correction_link = self._connection.execute(
                    "SELECT observation_sha256 FROM cl2_transaction_observation "
                    "WHERE transaction_sha256=?",
                    (checked.correction.sha256,),
                ).fetchone()
                if (
                    _blob(existing_bundle["canonical"]) == checked.canonical_bytes
                    and reversal_link is not None
                    and correction_link is not None
                    and reversal_link["observation_sha256"] == reversal_observation
                    and correction_link["observation_sha256"] == correction_observation
                ):
                    self._connection.execute("ROLLBACK")
                    return PersistenceDisposition.CORRECTION_BUNDLE_ALREADY_PRESENT
                _fail(PersistenceReason.LINEAGE_CONFLICT)
            store_revision, ledger_revision = self._meta_revisions()
            if store_revision != expected_store or ledger_revision != expected_ledger:
                _fail(PersistenceReason.REVISION_MISMATCH)
            original = self._connection.execute(
                "SELECT canonical,transaction_kind FROM cl2_transaction WHERE sha256=?",
                (checked.original.sha256,),
            ).fetchone()
            if original is None:
                _fail(PersistenceReason.TRANSACTION_NOT_FOUND)
            observation_rows: dict[str, sqlite3.Row] = {}
            for observation_hash in (reversal_observation, correction_observation):
                row = self._connection.execute(
                    "SELECT canonical FROM cl2_observation WHERE sha256=?",
                    (observation_hash,),
                ).fetchone()
                if row is None:
                    _fail(PersistenceReason.OBSERVATION_NOT_FOUND)
                observation_rows[observation_hash] = row
            if (
                original["transaction_kind"] != "ORDINARY"
                or _blob(original["canonical"]) != checked.original.canonical_bytes
                or self._connection.execute(
                    "SELECT 1 FROM cl2_ledger_transition "
                    "WHERE transition_kind='TRANSACTION' AND transition_sha256=?",
                    (checked.original.sha256,),
                ).fetchone()
                is None
                or self._connection.execute(
                    "SELECT 1 FROM cl2_correction_bundle WHERE original_sha256=?",
                    (checked.original.sha256,),
                ).fetchone()
                is not None
            ):
                _fail(PersistenceReason.LINEAGE_CONFLICT)
            if any(
                self._connection.execute(
                    "SELECT 1 FROM cl2_transaction WHERE sha256=?", (transaction.sha256,)
                ).fetchone()
                is not None
                for transaction in (checked.reversal, checked.correction)
            ):
                _fail(PersistenceReason.LINEAGE_CONFLICT)
            try:
                validate_correction_bundle_set(
                    (*_load_bundles(self._connection), checked)
                )
            except LedgerError:
                _fail(PersistenceReason.LINEAGE_CONFLICT)
            reversal_value = InboxObservation.from_canonical_bytes(
                _blob(observation_rows[reversal_observation]["canonical"]),
                self._registry.values(),
            )
            correction_value = InboxObservation.from_canonical_bytes(
                _blob(observation_rows[correction_observation]["canonical"]),
                self._registry.values(),
            )
            if (
                reversal_value.source.canonical_bytes
                != checked.reversal.source.canonical_bytes
                or correction_value.source.canonical_bytes
                != checked.correction.source.canonical_bytes
            ):
                _fail(PersistenceReason.SOURCE_CONFLICT)
            same_component_source = (
                checked.reversal.source.canonical_bytes
                == checked.correction.source.canonical_bytes
            )
            if same_component_source != (
                reversal_observation == correction_observation
            ):
                _fail(PersistenceReason.SOURCE_CONFLICT)
            trusted_rows = self._connection.execute(
                "SELECT canonical FROM cl2_transaction WHERE sha256<>? ORDER BY sha256",
                (checked.original.sha256,),
            ).fetchall()
            for candidate in (checked.reversal, checked.correction):
                for row in trusted_rows:
                    relation = compare_transaction_identities(
                        candidate, _parse_transaction(row["canonical"])
                    )
                    if relation in {
                        IdentityRelation.EXACT_DUPLICATE,
                        IdentityRelation.SOURCE_CONFLICT,
                    }:
                        _fail(PersistenceReason.SOURCE_CONFLICT)
                    if relation is IdentityRelation.ECONOMIC_MATCH:
                        _fail(PersistenceReason.ECONOMIC_MATCH_REVIEW_REQUIRED)
            observation_state: dict[str, tuple[str, int, bool]] = {}
            for observation_hash in dict.fromkeys(
                (reversal_observation, correction_observation)
            ):
                status, last_event = self._current_status(observation_hash)
                links = [
                    row["transaction_sha256"]
                    for row in self._connection.execute(
                        "SELECT transaction_sha256 FROM cl2_transaction_observation "
                        "WHERE observation_sha256=? ORDER BY transaction_sha256",
                        (observation_hash,),
                    )
                ]
                reused_original = status == "LEDGER_LINKED" and links == [
                    checked.original.sha256
                ]
                if status not in {"OBSERVED", "REVIEW_REQUIRED"} and not reused_original:
                    _fail(PersistenceReason.STATUS_TRANSITION_INVALID)
                observation_state[observation_hash] = (
                    status,
                    last_event,
                    reused_original,
                )
            if store_revision == MAX_REVISION or ledger_revision == MAX_REVISION:
                _fail(PersistenceReason.REVISION_EXHAUSTED)
            self._insert_transaction(checked.reversal, "REVERSAL")
            self._insert_transaction(checked.correction, "CORRECTION")
            self._fault("append_correction_bundle.after_transactions")
            self._connection.execute(
                "INSERT INTO cl2_transaction_observation VALUES (?,?)",
                (checked.reversal.sha256, reversal_observation),
            )
            self._connection.execute(
                "INSERT INTO cl2_transaction_observation VALUES (?,?)",
                (checked.correction.sha256, correction_observation),
            )
            for observation_hash, (status, last_event, reused_original) in (
                observation_state.items()
            ):
                if reused_original:
                    continue
                self._insert_status_event(
                    InboxStatusEvent(
                        observation_sha256=observation_hash,
                        event_no=last_event + 1,
                        from_status=status,
                        to_status="LEDGER_LINKED",
                        reason="LEDGER_CORRECTION_ACCEPTED",
                        related_bundle_sha256=checked.sha256,
                    )
                )
            self._fault("append_correction_bundle.after_provenance")
            self._connection.execute(
                "INSERT INTO cl2_correction_bundle VALUES (?,?,?,?,?)",
                (
                    checked.sha256,
                    checked.original.sha256,
                    checked.reversal.sha256,
                    checked.correction.sha256,
                    checked.canonical_bytes,
                ),
            )
            self._fault("append_correction_bundle.after_bundle")
            self._advance_head(
                store_revision=store_revision,
                ledger_revision=ledger_revision,
                transition_kind="CORRECTION_BUNDLE",
                transition_sha256=checked.sha256,
            )
            self._fault("append_correction_bundle.after_meta")
            self._finish_mutation("append_correction_bundle")
            return PersistenceDisposition.CORRECTION_BUNDLE_APPENDED
        except PersistenceError:
            self._abort_mutation()
            raise
        except InjectedFault as exc:
            self._abort_mutation()
            raise PersistenceError(PersistenceReason.INTERRUPTED_TRANSACTION) from exc
        except sqlite3.Error as exc:
            self._abort_mutation()
            raise PersistenceError(_sqlite_reason(exc)) from exc

    def export_bytes(self) -> bytes:
        self._ensure_open()
        try:
            self._connection.execute("BEGIN")
            _validate_connection(self._connection, self._registry)
            result = _export_from_connection(self._connection)
            self._connection.execute("COMMIT")
            return result
        except PersistenceError:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise PersistenceError(_sqlite_reason(exc)) from exc

    def backup(self, destination: object) -> BackupVerification:
        self._ensure_open()
        _validate_live_root(self._root)
        _validate_connection(self._connection, self._registry)
        target = _path_from(destination)
        _validate_target_parent(target)
        staging = target.with_name(f"{target.name}.cl2-backup-staging")
        _require_absent_target(target, staging)
        _reject_overlapping_roots(self._root, target, staging)
        try:
            staging.mkdir()
            self._fault("backup.after_staging")
            database = staging / "store.sqlite3"
            destination_connection = _connect(database, self._busy_timeout_ms)
            try:
                self._connection.backup(destination_connection)
                destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                destination_connection.close()
            self._fault("backup.after_database")
            _validate_backup_database(database, self._registry, self._busy_timeout_ms)
            export = _export_backup_database(
                database, self._registry, self._busy_timeout_ms
            )
            export_path = staging / "export.json"
            _write_bytes_fsync(export_path, export)
            self._fault("backup.after_export")
            snapshot = _snapshot_backup_database(
                database, self._registry, self._busy_timeout_ms
            )
            manifest = _manifest_bytes(database, export_path, snapshot)
            _write_bytes_fsync(staging / "manifest.json", manifest)
            self._fault("backup.after_manifest")
            verification = verify_backup(
                staging,
                self._registry.values(),
                busy_timeout_ms=self._busy_timeout_ms,
            )
            self._fault("backup.before_promote")
            _validate_live_root(self._root)
            _validate_connection(self._connection, self._registry)
            _promote_staging(staging, target)
            return BackupVerification(
                path=target,
                store_revision=verification.store_revision,
                ledger_revision=verification.ledger_revision,
                ledger_head_sha256=verification.ledger_head_sha256,
                export_sha256=verification.export_sha256,
            )
        except InjectedFault as exc:
            raise PersistenceError(PersistenceReason.INTERRUPTED_TRANSACTION) from exc
        except PersistenceError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PersistenceError(PersistenceReason.IO_FAILURE) from exc


def _valid_status_event(
    event: InboxStatusEvent,
    current_status: str,
    last_event_no: int,
) -> bool:
    if event.event_no != last_event_no + 1 or event.from_status != current_status:
        return False
    null_references = (
        event.related_transaction_sha256 is None
        and event.related_bundle_sha256 is None
    )
    if event.reason == "REVIEW_REQUIRED":
        return (
            current_status == "OBSERVED"
            and event.to_status == "REVIEW_REQUIRED"
            and null_references
        )
    if event.reason in {"NOT_LEDGER_RELEVANT", "INVALID_OBSERVATION"}:
        return (
            current_status in {"OBSERVED", "REVIEW_REQUIRED"}
            and event.to_status == "REJECTED"
            and null_references
        )
    if event.reason == "LEDGER_TRANSACTION_ACCEPTED":
        return (
            current_status in {"OBSERVED", "REVIEW_REQUIRED"}
            and event.to_status == "LEDGER_LINKED"
            and event.related_transaction_sha256 is not None
            and event.related_bundle_sha256 is None
        )
    if event.reason == "LEDGER_CORRECTION_ACCEPTED":
        return (
            current_status in {"OBSERVED", "REVIEW_REQUIRED"}
            and event.to_status == "LEDGER_LINKED"
            and event.related_transaction_sha256 is None
            and event.related_bundle_sha256 is not None
        )
    return False


def _valid_nonledger_status_event(
    event: InboxStatusEvent,
    current_status: str,
    last_event_no: int,
) -> bool:
    return event.reason in {
        "REVIEW_REQUIRED",
        "NOT_LEDGER_RELEVANT",
        "INVALID_OBSERVATION",
    } and _valid_status_event(event, current_status, last_event_no)


def _load_bundles(connection: sqlite3.Connection) -> tuple[LedgerCorrectionBundle, ...]:
    bundles: list[LedgerCorrectionBundle] = []
    for row in connection.execute(
        "SELECT sha256,original_sha256,reversal_sha256,correction_sha256,canonical "
        "FROM cl2_correction_bundle ORDER BY original_sha256,sha256"
    ):
        raw = _blob(row["canonical"])
        parsed = parse_canonical_json(raw)
        transaction_rows = []
        for column in ("original_sha256", "reversal_sha256", "correction_sha256"):
            transaction_row = connection.execute(
                "SELECT canonical FROM cl2_transaction WHERE sha256=?", (row[column],)
            ).fetchone()
            if transaction_row is None:
                _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
            transaction_rows.append(_parse_transaction(transaction_row["canonical"]))
        try:
            bundle = LedgerCorrectionBundle.from_canonical_dict(
                parsed,
                original=transaction_rows[0],
                reversal=transaction_rows[1],
                correction=transaction_rows[2],
            )
        except LedgerError as exc:
            raise PersistenceError(
                PersistenceReason.SEMANTIC_INTEGRITY_FAILURE
            ) from exc
        if (
            bundle.canonical_bytes != raw
            or bundle.sha256 != row["sha256"]
            or bundle.original.sha256 != row["original_sha256"]
            or bundle.reversal.sha256 != row["reversal_sha256"]
            or bundle.correction.sha256 != row["correction_sha256"]
        ):
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        bundles.append(bundle)
    return tuple(bundles)


def _validate_connection(
    connection: sqlite3.Connection,
    registry: dict[tuple[str, int], CodecDescriptor],
) -> None:
    try:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise PersistenceError(PersistenceReason.INTEGRITY_FAILURE) from exc
    if (
        application_id != SQLITE_APPLICATION_ID
        or user_version != CASH_LEDGER_STORE_SCHEMA_VERSION
    ):
        _fail(PersistenceReason.VERSION_UNSUPPORTED)
    _validate_schema_objects(connection)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
    except sqlite3.DatabaseError as exc:
        raise PersistenceError(PersistenceReason.INTEGRITY_FAILURE) from exc
    if len(integrity) != 1 or integrity[0][0] != "ok":
        _fail(PersistenceReason.INTEGRITY_FAILURE)
    try:
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        _validate_semantics(connection, registry)
    except PersistenceError:
        raise
    except (sqlite3.Error, LedgerError, UnicodeError, ValueError) as exc:
        raise PersistenceError(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE) from exc


def _validate_semantics(
    connection: sqlite3.Connection,
    registry: dict[tuple[str, int], CodecDescriptor],
) -> None:
    meta_rows = connection.execute("SELECT * FROM cl2_meta").fetchall()
    if len(meta_rows) != 1:
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
    meta = meta_rows[0]
    if (
        meta["singleton"] != 1
        or meta["schema_version"] != CASH_LEDGER_STORE_SCHEMA_VERSION
        or meta["schema_fingerprint"] != SCHEMA_FINGERPRINT_SHA256
        or not _is_plain_int(meta["store_revision"])
        or not _is_plain_int(meta["ledger_revision"])
        or not 0 <= meta["store_revision"] <= MAX_REVISION
        or not 0 <= meta["ledger_revision"] <= MAX_REVISION
        or meta["store_revision"] < meta["ledger_revision"]
        or not _is_sha256(meta["ledger_head_sha256"])
    ):
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)

    stored_codecs: dict[str, CodecDescriptor] = {}
    for row in connection.execute("SELECT * FROM cl2_codec ORDER BY sha256"):
        try:
            descriptor = CodecDescriptor.from_canonical_bytes(_blob(row["canonical"]))
        except PersistenceError as exc:
            raise PersistenceError(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE) from exc
        supplied = registry.get((descriptor.codec_id, descriptor.codec_version))
        if supplied is None or supplied.canonical_bytes != descriptor.canonical_bytes:
            _fail(PersistenceReason.CODEC_UNSUPPORTED)
        if (
            row["sha256"] != descriptor.sha256
            or row["codec_id"] != descriptor.codec_id
            or row["codec_version"] != descriptor.codec_version
            or row["schema_sha256"] != descriptor.schema_sha256
        ):
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        stored_codecs[descriptor.sha256] = descriptor

    observations: dict[str, InboxObservation] = {}
    for row in connection.execute("SELECT * FROM cl2_observation ORDER BY sha256"):
        descriptor = stored_codecs.get(row["codec_sha256"])
        if descriptor is None:
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        try:
            observation = InboxObservation.from_canonical_bytes(
                _blob(row["canonical"]), registry.values()
            )
        except PersistenceError as exc:
            raise PersistenceError(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE) from exc
        if (
            row["sha256"] != observation.sha256
            or row["logical_source_sha256"] != observation.logical_source_sha256
            or row["source_sha256"] != observation.source.sha256
            or row["initial_status"] != "OBSERVED"
            or descriptor.canonical_bytes != observation.descriptor.canonical_bytes
        ):
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        observations[observation.sha256] = observation

    transactions: dict[str, LedgerTransaction] = {}
    transaction_kinds: dict[str, str] = {}
    for row in connection.execute("SELECT * FROM cl2_transaction ORDER BY sha256"):
        try:
            transaction = _parse_transaction(row["canonical"])
        except (PersistenceError, LedgerError) as exc:
            raise PersistenceError(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE) from exc
        kind = _transaction_kind(transaction)
        if (
            row["sha256"] != transaction.sha256
            or row["source_sha256"] != transaction.source_sha256
            or row["economic_sha256"] != transaction.economic_sha256
            or row["transaction_kind"] != kind
        ):
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        posting_rows = connection.execute(
            "SELECT line_no,sha256,canonical FROM cl2_posting "
            "WHERE transaction_sha256=? ORDER BY line_no",
            (transaction.sha256,),
        ).fetchall()
        if len(posting_rows) != len(transaction.postings):
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        for row_value, posting in zip(posting_rows, transaction.postings, strict=True):
            if (
                row_value["line_no"] != posting.line_no
                or row_value["sha256"] != posting.sha256
                or _blob(row_value["canonical"]) != posting.canonical_bytes
            ):
                _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        transactions[transaction.sha256] = transaction
        transaction_kinds[transaction.sha256] = kind

    links: dict[str, str] = {}
    for row in connection.execute(
        "SELECT transaction_sha256,observation_sha256 "
        "FROM cl2_transaction_observation ORDER BY transaction_sha256"
    ):
        transaction = transactions.get(row["transaction_sha256"])
        observation = observations.get(row["observation_sha256"])
        if (
            transaction is None
            or observation is None
            or transaction.source.canonical_bytes != observation.source.canonical_bytes
        ):
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        links[transaction.sha256] = observation.sha256
    if set(links) != set(transactions):
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)

    events_by_observation: dict[str, list[InboxStatusEvent]] = {
        value: [] for value in observations
    }
    for row in connection.execute(
        "SELECT * FROM cl2_inbox_status_event ORDER BY observation_sha256,event_no"
    ):
        try:
            event = InboxStatusEvent.from_canonical_bytes(_blob(row["canonical"]))
        except PersistenceError as exc:
            raise PersistenceError(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE) from exc
        if (
            event.observation_sha256 not in observations
            or row["observation_sha256"] != event.observation_sha256
            or row["event_no"] != event.event_no
            or row["sha256"] != event.sha256
            or row["reason"] != event.reason
            or row["from_status"] != event.from_status
            or row["to_status"] != event.to_status
            or row["related_transaction_sha256"]
            != event.related_transaction_sha256
            or row["related_bundle_sha256"] != event.related_bundle_sha256
        ):
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        events_by_observation[event.observation_sha256].append(event)
    current_status: dict[str, str] = {}
    for observation_hash, events in events_by_observation.items():
        status = "OBSERVED"
        last_event = 0
        for event in events:
            if not _valid_status_event(event, status, last_event):
                _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
            status = event.to_status
            last_event = event.event_no
        current_status[observation_hash] = status

    try:
        bundles = _load_bundles(connection)
        validate_correction_bundle_set(bundles)
    except (PersistenceError, LedgerError) as exc:
        raise PersistenceError(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE) from exc
    bundle_by_hash = {bundle.sha256: bundle for bundle in bundles}
    correction_members: set[str] = set()
    permitted_bundle_pairs: set[frozenset[str]] = set()
    for bundle in bundles:
        if (
            transaction_kinds.get(bundle.original.sha256) != "ORDINARY"
            or transaction_kinds.get(bundle.reversal.sha256) != "REVERSAL"
            or transaction_kinds.get(bundle.correction.sha256) != "CORRECTION"
        ):
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        correction_members.update(
            {bundle.reversal.sha256, bundle.correction.sha256}
        )
        permitted_bundle_pairs.update(
            {
                frozenset((bundle.original.sha256, bundle.reversal.sha256)),
                frozenset((bundle.original.sha256, bundle.correction.sha256)),
                frozenset((bundle.reversal.sha256, bundle.correction.sha256)),
            }
        )
        original_observation = links[bundle.original.sha256]
        component_observations = {
            links[bundle.reversal.sha256],
            links[bundle.correction.sha256],
        }
        for observation_hash in component_observations:
            matching = [
                event
                for event in events_by_observation[observation_hash]
                if event.related_bundle_sha256 == bundle.sha256
            ]
            expected = 0 if observation_hash == original_observation else 1
            if len(matching) != expected:
                _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)

    ordered_transactions = sorted(transactions.values(), key=lambda item: item.sha256)
    for index, left in enumerate(ordered_transactions):
        for right in ordered_transactions[index + 1 :]:
            relation = compare_transaction_identities(left, right)
            if (
                relation
                in {IdentityRelation.SOURCE_CONFLICT, IdentityRelation.ECONOMIC_MATCH}
                and frozenset((left.sha256, right.sha256))
                not in permitted_bundle_pairs
            ):
                _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)

    for transaction_hash, kind in transaction_kinds.items():
        transaction_events = [
            event
            for events in events_by_observation.values()
            for event in events
            if event.related_transaction_sha256 == transaction_hash
        ]
        if kind == "ORDINARY":
            if len(transaction_events) != 1:
                _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        elif transaction_hash not in correction_members or transaction_events:
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
    for events in events_by_observation.values():
        for event in events:
            if (
                event.related_transaction_sha256 is not None
                and event.related_transaction_sha256 not in transactions
            ) or (
                event.related_bundle_sha256 is not None
                and event.related_bundle_sha256 not in bundle_by_hash
            ):
                _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
            if (
                event.related_transaction_sha256 is not None
                and links[event.related_transaction_sha256]
                != event.observation_sha256
            ):
                _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
            if event.related_bundle_sha256 is not None:
                bundle = bundle_by_hash[event.related_bundle_sha256]
                expected_observations = {
                    links[bundle.reversal.sha256],
                    links[bundle.correction.sha256],
                } - {links[bundle.original.sha256]}
                if event.observation_sha256 not in expected_observations:
                    _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)

    transitions = connection.execute(
        "SELECT * FROM cl2_ledger_transition ORDER BY ledger_revision"
    ).fetchall()
    if len(transitions) != meta["ledger_revision"]:
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
    previous_head = GENESIS_HEAD_SHA256
    current_head_bytes = GENESIS_HEAD_JSON_ASCII.encode("ascii")
    transitioned_transactions: set[str] = set()
    transitioned_bundles: set[str] = set()
    for expected_revision, row in enumerate(transitions, start=1):
        try:
            head = LedgerHead.from_canonical_bytes(_blob(row["head_json"]))
        except PersistenceError as exc:
            raise PersistenceError(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE) from exc
        if (
            row["ledger_revision"] != expected_revision
            or head.ledger_revision != expected_revision
            or row["transition_kind"] != head.transition_kind
            or row["transition_sha256"] != head.transition_sha256
            or row["head_sha256"] != head.sha256
            or head.previous_head_sha256 != previous_head
        ):
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        if head.transition_kind == "TRANSACTION":
            if transaction_kinds.get(head.transition_sha256) != "ORDINARY":
                _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
            transitioned_transactions.add(head.transition_sha256)
        elif head.transition_sha256 not in bundle_by_hash:
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        else:
            transitioned_bundles.add(head.transition_sha256)
        previous_head = head.sha256
        current_head_bytes = head.canonical_bytes
    if (
        meta["ledger_head_sha256"] != previous_head
        or _blob(meta["ledger_head_json"]) != current_head_bytes
        or transitioned_transactions
        != {
            transaction_hash
            for transaction_hash, kind in transaction_kinds.items()
            if kind == "ORDINARY"
        }
        or transitioned_bundles != set(bundle_by_hash)
    ):
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)

    nonledger_reasons = {
        "REVIEW_REQUIRED",
        "NOT_LEDGER_RELEVANT",
        "INVALID_OBSERVATION",
    }
    nonledger_events = sum(
        event.reason in nonledger_reasons
        for events in events_by_observation.values()
        for event in events
    )
    expected_store_revision = (
        len(observations) + nonledger_events + meta["ledger_revision"]
    )
    if meta["store_revision"] != expected_store_revision:
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)


def _export_from_connection(connection: sqlite3.Connection) -> bytes:
    meta = connection.execute(
        "SELECT store_revision,ledger_revision,ledger_head_json,ledger_head_sha256 "
        "FROM cl2_meta WHERE singleton=1"
    ).fetchone()
    if meta is None:
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
    codec_registry = [
        {
            "canonical_json_ascii": _blob(row["canonical"]).decode("ascii"),
            "sha256": row["sha256"],
        }
        for row in connection.execute(
            "SELECT sha256,canonical FROM cl2_codec ORDER BY sha256"
        )
    ]
    observations = []
    for row in connection.execute(
        "SELECT sha256,logical_source_sha256,source_sha256,canonical "
        "FROM cl2_observation ORDER BY logical_source_sha256,sha256"
    ):
        status_row = connection.execute(
            "SELECT to_status FROM cl2_inbox_status_event "
            "WHERE observation_sha256=? ORDER BY event_no DESC LIMIT 1",
            (row["sha256"],),
        ).fetchone()
        observations.append(
            {
                "canonical_json_ascii": _blob(row["canonical"]).decode("ascii"),
                "current_status": (
                    "OBSERVED" if status_row is None else status_row["to_status"]
                ),
                "logical_source_sha256": row["logical_source_sha256"],
                "sha256": row["sha256"],
                "source_sha256": row["source_sha256"],
            }
        )
    status_events = [
        {
            "canonical_json_ascii": _blob(row["canonical"]).decode("ascii"),
            "sha256": row["sha256"],
        }
        for row in connection.execute(
            "SELECT sha256,canonical FROM cl2_inbox_status_event "
            "ORDER BY observation_sha256,event_no"
        )
    ]
    transactions = [
        {
            "canonical_json_ascii": _blob(row["canonical"]).decode("ascii"),
            "economic_sha256": row["economic_sha256"],
            "sha256": row["sha256"],
            "source_sha256": row["source_sha256"],
        }
        for row in connection.execute(
            "SELECT sha256,source_sha256,economic_sha256,canonical "
            "FROM cl2_transaction ORDER BY sha256"
        )
    ]
    provenance_links = [
        {
            "observation_sha256": row["observation_sha256"],
            "transaction_sha256": row["transaction_sha256"],
        }
        for row in connection.execute(
            "SELECT transaction_sha256,observation_sha256 "
            "FROM cl2_transaction_observation "
            "ORDER BY transaction_sha256,observation_sha256"
        )
    ]
    correction_bundles = [
        {
            "canonical_json_ascii": _blob(row["canonical"]).decode("ascii"),
            "sha256": row["sha256"],
        }
        for row in connection.execute(
            "SELECT sha256,canonical FROM cl2_correction_bundle "
            "ORDER BY original_sha256,sha256"
        )
    ]
    ledger_transitions = [
        {
            "head_json_ascii": _blob(row["head_json"]).decode("ascii"),
            "head_sha256": row["head_sha256"],
        }
        for row in connection.execute(
            "SELECT head_json,head_sha256 FROM cl2_ledger_transition "
            "ORDER BY ledger_revision"
        )
    ]
    return canonical_json_bytes(
        {
            "codec_registry": codec_registry,
            "correction_bundles": correction_bundles,
            "domain": "v3.10-cash-ledger-export",
            "inbox_status_events": status_events,
            "ledger_head_json_ascii": _blob(meta["ledger_head_json"]).decode("ascii"),
            "ledger_head_sha256": meta["ledger_head_sha256"],
            "ledger_revision": str(meta["ledger_revision"]),
            "ledger_transitions": ledger_transitions,
            "observations": observations,
            "provenance_links": provenance_links,
            "schema_version": CASH_LEDGER_STORE_SCHEMA_VERSION,
            "store_revision": str(meta["store_revision"]),
            "transactions": transactions,
            "version": CASH_LEDGER_EXPORT_VERSION,
        }
    )


def _connect_readonly(database: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    try:
        uri = f"{database.resolve().as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA query_only=ON")
        if (
            connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1
            or connection.execute("PRAGMA trusted_schema").fetchone()[0] != 0
            or connection.execute("PRAGMA query_only").fetchone()[0] != 1
        ):
            _fail(PersistenceReason.VERSION_UNSUPPORTED)
        return connection
    except sqlite3.Error as exc:
        raise PersistenceError(PersistenceReason.INTEGRITY_FAILURE) from exc


def _validate_backup_database(
    database: Path,
    registry: dict[tuple[str, int], CodecDescriptor],
    busy_timeout_ms: int,
) -> None:
    _validate_database_file(database)
    _validate_sqlite_header(database)
    connection = _connect_readonly(database, busy_timeout_ms)
    try:
        _validate_connection(connection, registry)
    finally:
        connection.close()


def _export_backup_database(
    database: Path,
    registry: dict[tuple[str, int], CodecDescriptor],
    busy_timeout_ms: int,
) -> bytes:
    connection = _connect_readonly(database, busy_timeout_ms)
    try:
        connection.execute("BEGIN")
        _validate_connection(connection, registry)
        result = _export_from_connection(connection)
        connection.execute("COMMIT")
        return result
    finally:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()


def _snapshot_backup_database(
    database: Path,
    registry: dict[tuple[str, int], CodecDescriptor],
    busy_timeout_ms: int,
) -> StoreSnapshot:
    connection = _connect_readonly(database, busy_timeout_ms)
    try:
        _validate_connection(connection, registry)
        row = connection.execute(
            "SELECT store_revision,ledger_revision,ledger_head_json,ledger_head_sha256 "
            "FROM cl2_meta WHERE singleton=1"
        ).fetchone()
        if row is None:
            _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
        return StoreSnapshot(
            store_revision=row["store_revision"],
            ledger_revision=row["ledger_revision"],
            ledger_head_json_ascii=_blob(row["ledger_head_json"]).decode("ascii"),
            ledger_head_sha256=row["ledger_head_sha256"],
        )
    finally:
        connection.close()


def _write_bytes_fsync(path: Path, value: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _file_identity(path: Path) -> dict[str, str]:
    data = path.read_bytes()
    return {
        "path": path.name,
        "sha256": sha256_hex(data),
        "size": str(len(data)),
    }


def _manifest_bytes(
    database: Path,
    export_path: Path,
    snapshot: StoreSnapshot,
) -> bytes:
    files = sorted(
        (_file_identity(export_path), _file_identity(database)),
        key=lambda item: item["path"],
    )
    export_identity = next(item for item in files if item["path"] == "export.json")
    return canonical_json_bytes(
        {
            "domain": "v3.10-cash-ledger-backup-manifest",
            "export_sha256": export_identity["sha256"],
            "files": files,
            "ledger_head_sha256": snapshot.ledger_head_sha256,
            "ledger_revision": str(snapshot.ledger_revision),
            "schema_version": CASH_LEDGER_STORE_SCHEMA_VERSION,
            "store_revision": str(snapshot.store_revision),
            "version": CASH_LEDGER_BACKUP_VERSION,
        }
    )


def _parse_manifest(value: bytes) -> dict[str, object]:
    parsed = parse_canonical_json(value)
    keys = {
        "domain",
        "export_sha256",
        "files",
        "ledger_head_sha256",
        "ledger_revision",
        "schema_version",
        "store_revision",
        "version",
    }
    if not isinstance(parsed, dict) or set(parsed) != keys:
        _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
    if (
        parsed["domain"] != "v3.10-cash-ledger-backup-manifest"
        or not _is_plain_int(parsed["schema_version"])
        or not _is_plain_int(parsed["version"])
        or parsed["schema_version"] != CASH_LEDGER_STORE_SCHEMA_VERSION
        or parsed["version"] != CASH_LEDGER_BACKUP_VERSION
        or not _is_sha256(parsed["export_sha256"])
        or not _is_sha256(parsed["ledger_head_sha256"])
    ):
        _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
    for field in ("store_revision", "ledger_revision"):
        try:
            revision = _parse_decimal(parsed[field])
        except PersistenceError as exc:
            raise PersistenceError(PersistenceReason.BACKUP_MANIFEST_INVALID) from exc
        if not 0 <= revision <= MAX_REVISION:
            _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
    files = parsed["files"]
    if not isinstance(files, list) or len(files) != 2:
        _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
    expected_paths = ["export.json", "store.sqlite3"]
    for item, expected_path in zip(files, expected_paths, strict=True):
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256", "size"}
            or item["path"] != expected_path
            or not _is_sha256(item["sha256"])
        ):
            _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
        try:
            size = _parse_decimal(item["size"])
        except PersistenceError as exc:
            raise PersistenceError(PersistenceReason.BACKUP_MANIFEST_INVALID) from exc
        if size < 0:
            _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
    if files[0]["sha256"] != parsed["export_sha256"]:
        _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
    return parsed


def verify_backup(
    path: object,
    codec_registry: object,
    *,
    busy_timeout_ms: int = 0,
) -> BackupVerification:
    root = _path_from(path)
    timeout = _validate_busy_timeout(busy_timeout_ms)
    registry = normalize_codec_registry(codec_registry)
    _validate_existing_components(root)
    if not root.exists():
        _fail(PersistenceReason.STORE_MISSING)
    if not root.is_dir() or _is_reparse_or_symlink(root):
        _fail(PersistenceReason.PATH_INVALID)
    try:
        children = sorted(child.name for child in root.iterdir())
    except OSError as exc:
        raise PersistenceError(PersistenceReason.IO_FAILURE) from exc
    if children != ["export.json", "manifest.json", "store.sqlite3"]:
        _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
    database = root / "store.sqlite3"
    export_path = root / "export.json"
    manifest_path = root / "manifest.json"
    for child in (database, export_path, manifest_path):
        if _is_reparse_or_symlink(child) or not child.is_file():
            _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
        try:
            if child.stat().st_nlink != 1:
                _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
        except OSError as exc:
            raise PersistenceError(PersistenceReason.BACKUP_MANIFEST_INVALID) from exc
    _validate_database_file(database)
    try:
        manifest = _parse_manifest(manifest_path.read_bytes())
        for expected in manifest["files"]:
            actual = _file_identity(root / expected["path"])
            if actual != expected:
                _fail(PersistenceReason.BACKUP_MANIFEST_INVALID)
    except PersistenceError as exc:
        if exc.reason in {
            PersistenceReason.PATH_INVALID,
            PersistenceReason.CANONICAL_FORMAT_INVALID,
            PersistenceReason.HASH_INVALID,
            PersistenceReason.VERSION_UNSUPPORTED,
        }:
            raise PersistenceError(PersistenceReason.BACKUP_MANIFEST_INVALID) from exc
        raise
    except OSError as exc:
        raise PersistenceError(PersistenceReason.BACKUP_MANIFEST_INVALID) from exc
    _validate_backup_database(database, registry, timeout)
    regenerated = _export_backup_database(database, registry, timeout)
    supplied_export = export_path.read_bytes()
    if regenerated != supplied_export:
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
    snapshot = _snapshot_backup_database(database, registry, timeout)
    export_value = parse_canonical_json(supplied_export)
    if (
        not isinstance(export_value, dict)
        or export_value.get("domain") != "v3.10-cash-ledger-export"
        or export_value.get("version") != CASH_LEDGER_EXPORT_VERSION
        or export_value.get("schema_version") != CASH_LEDGER_STORE_SCHEMA_VERSION
        or export_value.get("store_revision") != str(snapshot.store_revision)
        or export_value.get("ledger_revision") != str(snapshot.ledger_revision)
        or export_value.get("ledger_head_sha256") != snapshot.ledger_head_sha256
        or manifest["store_revision"] != str(snapshot.store_revision)
        or manifest["ledger_revision"] != str(snapshot.ledger_revision)
        or manifest["ledger_head_sha256"] != snapshot.ledger_head_sha256
    ):
        _fail(PersistenceReason.SEMANTIC_INTEGRITY_FAILURE)
    return BackupVerification(
        path=root,
        store_revision=snapshot.store_revision,
        ledger_revision=snapshot.ledger_revision,
        ledger_head_sha256=snapshot.ledger_head_sha256,
        export_sha256=sha256_hex(supplied_export),
    )


def restore_backup(
    backup_path: object,
    target_root: object,
    codec_registry: object,
    *,
    busy_timeout_ms: int = 0,
    fault_injector: Callable[[str], None] | None = None,
) -> CashLedgerStore:
    source = _path_from(backup_path)
    timeout = _validate_busy_timeout(busy_timeout_ms)
    registry = normalize_codec_registry(codec_registry)
    verification = verify_backup(
        source, registry.values(), busy_timeout_ms=timeout
    )
    target = _path_from(target_root)
    _validate_target_parent(target)
    staging = target.with_name(f"{target.name}.cl2-restore-staging")
    _require_absent_target(target, staging)
    _reject_overlapping_roots(source, target, staging)
    try:
        source_export = (source / "export.json").read_bytes()
        source_manifest_sha256 = sha256_hex(
            (source / "manifest.json").read_bytes()
        )
        source_database_identity = _file_identity(source / "store.sqlite3")
    except OSError as exc:
        raise PersistenceError(PersistenceReason.IO_FAILURE) from exc
    try:
        staging.mkdir()
        if fault_injector is not None:
            fault_injector("restore.after_staging")
        destination_database = staging / "store.sqlite3"
        with (
            (source / "store.sqlite3").open("rb") as reader,
            destination_database.open("xb") as writer,
        ):
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        if fault_injector is not None:
            fault_injector("restore.after_copy")
        if _file_identity(destination_database) != source_database_identity:
            _fail(PersistenceReason.RESTORE_VERIFICATION_FAILED)
        repeated_verification = verify_backup(
            source, registry.values(), busy_timeout_ms=timeout
        )
        if (
            repeated_verification != verification
            or (source / "export.json").read_bytes() != source_export
            or sha256_hex((source / "manifest.json").read_bytes())
            != source_manifest_sha256
            or _file_identity(source / "store.sqlite3") != source_database_identity
        ):
            _fail(PersistenceReason.RESTORE_VERIFICATION_FAILED)
        restored: CashLedgerStore | None = None
        try:
            restored = CashLedgerStore.open(
                staging,
                registry.values(),
                busy_timeout_ms=timeout,
            )
            if restored.export_bytes() != source_export:
                _fail(PersistenceReason.RESTORE_VERIFICATION_FAILED)
            snapshot = restored.snapshot()
            if (
                snapshot.store_revision != verification.store_revision
                or snapshot.ledger_revision != verification.ledger_revision
                or snapshot.ledger_head_sha256 != verification.ledger_head_sha256
            ):
                _fail(PersistenceReason.RESTORE_VERIFICATION_FAILED)
        except PersistenceError as exc:
            if exc.reason is PersistenceReason.RESTORE_VERIFICATION_FAILED:
                raise
            raise PersistenceError(PersistenceReason.RESTORE_VERIFICATION_FAILED) from exc
        finally:
            if restored is not None:
                restored.close()
        if _file_identity(destination_database) != source_database_identity:
            _fail(PersistenceReason.RESTORE_VERIFICATION_FAILED)
        if fault_injector is not None:
            fault_injector("restore.before_promote")
        final_verification = verify_backup(
            source, registry.values(), busy_timeout_ms=timeout
        )
        if (
            final_verification != verification
            or (source / "export.json").read_bytes() != source_export
            or sha256_hex((source / "manifest.json").read_bytes())
            != source_manifest_sha256
            or _file_identity(source / "store.sqlite3") != source_database_identity
            or _file_identity(destination_database) != source_database_identity
        ):
            _fail(PersistenceReason.RESTORE_VERIFICATION_FAILED)
        _promote_staging(staging, target)
        promoted_database = target / "store.sqlite3"
        if _file_identity(promoted_database) != source_database_identity:
            _fail(PersistenceReason.RESTORE_VERIFICATION_FAILED)
        promoted: CashLedgerStore | None = None
        try:
            promoted = CashLedgerStore.open(
                target,
                registry.values(),
                busy_timeout_ms=timeout,
                fault_injector=fault_injector,
            )
            if promoted.export_bytes() != source_export:
                _fail(PersistenceReason.RESTORE_VERIFICATION_FAILED)
            if _file_identity(promoted_database) != source_database_identity:
                _fail(PersistenceReason.RESTORE_VERIFICATION_FAILED)
        except PersistenceError as exc:
            if promoted is not None:
                promoted.close()
            if exc.reason is PersistenceReason.RESTORE_VERIFICATION_FAILED:
                raise
            raise PersistenceError(PersistenceReason.RESTORE_VERIFICATION_FAILED) from exc
        assert promoted is not None
        return promoted
    except InjectedFault as exc:
        raise PersistenceError(PersistenceReason.INTERRUPTED_TRANSACTION) from exc
    except PersistenceError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise PersistenceError(PersistenceReason.IO_FAILURE) from exc


__all__ = [
    "CASH_LEDGER_BACKUP_VERSION",
    "CASH_LEDGER_EXPORT_VERSION",
    "CASH_LEDGER_STORE_SCHEMA_VERSION",
    "CL2_CROSS_LANGUAGE_FIXTURE_VERSION",
    "GENESIS_HEAD_JSON_ASCII",
    "GENESIS_HEAD_SHA256",
    "LEDGER_HEAD_VERSION",
    "OPERATION_INBOX_CODEC_VERSION",
    "OPERATION_INBOX_STATUS_EVENT_VERSION",
    "OPERATION_INBOX_VERSION",
    "SCHEMA_FINGERPRINT_SHA256",
    "SQLITE_APPLICATION_ID",
    "BackupVerification",
    "CashLedgerStore",
    "CodecDescriptor",
    "InboxObservation",
    "InboxStatusEvent",
    "InjectedFault",
    "LedgerHead",
    "PersistenceDisposition",
    "PersistenceError",
    "PersistenceReason",
    "StoreSnapshot",
    "canonical_json_bytes",
    "normalize_codec_registry",
    "parse_canonical_json",
    "restore_backup",
    "sha256_hex",
    "verify_backup",
]
