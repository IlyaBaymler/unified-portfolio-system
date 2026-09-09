"""Provider-exact Money and balanced CashLedger pure-domain values for CL1."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

MONEY_DOMAIN_VERSION = 1
SOURCE_IDENTITY_VERSION = 1
LEDGER_POSTING_VERSION = 1
LEDGER_TRANSACTION_VERSION = 1
LEDGER_ECONOMIC_VERSION = 1
LEDGER_CORRECTION_BUNDLE_VERSION = 1
LEDGER_CHART_VERSION = 1
LEDGER_CLASSIFICATION_VERSION = 1
CROSS_LANGUAGE_FIXTURE_VERSION = 1

NANO_FACTOR = 1_000_000_000
WIRE_UNITS_MIN = -9_223_372_036_854_775_808
WIRE_UNITS_MAX = 9_223_372_036_854_775_807
WIRE_NANO_MIN = -999_999_999
WIRE_NANO_MAX = 999_999_999
MONEY_MIN_MINOR_UNITS = WIRE_UNITS_MIN * NANO_FACTOR + WIRE_NANO_MIN
MONEY_MAX_MINOR_UNITS = WIRE_UNITS_MAX * NANO_FACTOR + WIRE_NANO_MAX
LEDGER_POSTING_MIN_MINOR_UNITS = -MONEY_MAX_MINOR_UNITS
LEDGER_POSTING_MAX_MINOR_UNITS = MONEY_MAX_MINOR_UNITS
MAX_LINE_NUMBER = 2_147_483_647

_MONEY_KEYS = frozenset(
    {"amount", "currency", "domain", "minor_units", "scale", "version"}
)
_SOURCE_KEYS = frozenset(
    {
        "account_scope_sha256",
        "domain",
        "source_content_sha256",
        "source_kind",
        "source_scope_sha256",
        "version",
    }
)
_POSTING_KEYS = frozenset({"account", "domain", "line_no", "money", "version"})
_TRANSACTION_KEYS = frozenset(
    {
        "chart_version",
        "classification",
        "classification_version",
        "corrects_sha256",
        "domain",
        "effective_at",
        "postings",
        "reversal_of_sha256",
        "source",
        "version",
    }
)
_BUNDLE_KEYS = frozenset(
    {
        "correction_sha256",
        "domain",
        "original_sha256",
        "reversal_sha256",
        "version",
    }
)
_CANONICAL_INTEGER_RE = re.compile(r"0|-?[1-9][0-9]*", re.ASCII)
_CANONICAL_LINE_RE = re.compile(r"[1-9][0-9]*", re.ASCII)
_CANONICAL_AMOUNT_RE = re.compile(r"(?:0|-[0-9]+|[1-9][0-9]*)\.[0-9]{9}", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_SOURCE_KIND_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{9})Z",
    re.ASCII,
)


class MoneyReason(StrEnum):
    TYPE_INVALID = "TYPE_INVALID"
    CURRENCY_UNSUPPORTED = "CURRENCY_UNSUPPORTED"
    SCALE_INVALID = "SCALE_INVALID"
    WIRE_UNITS_OUT_OF_RANGE = "WIRE_UNITS_OUT_OF_RANGE"
    WIRE_NANO_OUT_OF_RANGE = "WIRE_NANO_OUT_OF_RANGE"
    WIRE_SIGN_NON_CANONICAL = "WIRE_SIGN_NON_CANONICAL"
    MINOR_UNITS_OUT_OF_RANGE = "MINOR_UNITS_OUT_OF_RANGE"
    CANONICAL_FORMAT_INVALID = "CANONICAL_FORMAT_INVALID"
    INCOMPATIBLE_OPERAND = "INCOMPATIBLE_OPERAND"
    ARITHMETIC_OVERFLOW = "ARITHMETIC_OVERFLOW"


class LedgerReason(StrEnum):
    TYPE_INVALID = "TYPE_INVALID"
    HASH_INVALID = "HASH_INVALID"
    ACCOUNT_UNSUPPORTED = "ACCOUNT_UNSUPPORTED"
    CLASSIFICATION_UNSUPPORTED = "CLASSIFICATION_UNSUPPORTED"
    TIMESTAMP_INVALID = "TIMESTAMP_INVALID"
    LINE_NUMBER_INVALID = "LINE_NUMBER_INVALID"
    ZERO_POSTING = "ZERO_POSTING"
    POSTING_OUT_OF_REVERSIBLE_RANGE = "POSTING_OUT_OF_REVERSIBLE_RANGE"
    DUPLICATE_LINE = "DUPLICATE_LINE"
    UNBALANCED = "UNBALANCED"
    SOURCE_INVALID = "SOURCE_INVALID"
    CANONICAL_FORMAT_INVALID = "CANONICAL_FORMAT_INVALID"
    ACCOUNTING_PATTERN_INVALID = "ACCOUNTING_PATTERN_INVALID"
    LINEAGE_INVALID = "LINEAGE_INVALID"
    LINEAGE_CONFLICT = "LINEAGE_CONFLICT"


class MoneyError(ValueError):
    """Fail-closed Money validation error with a stable typed reason."""

    def __init__(self, reason: MoneyReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class LedgerError(ValueError):
    """Fail-closed ledger validation error with a stable typed reason."""

    def __init__(self, reason: LedgerReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class LedgerAccount(StrEnum):
    ASSET_BROKER_CASH = "ASSET_BROKER_CASH"
    ASSET_TRADE_CLEARING = "ASSET_TRADE_CLEARING"
    EQUITY_OPENING_BALANCE = "EQUITY_OPENING_BALANCE"
    EQUITY_EXTERNAL_FLOW = "EQUITY_EXTERNAL_FLOW"
    EQUITY_MANUAL_ADJUSTMENT = "EQUITY_MANUAL_ADJUSTMENT"
    INCOME_DIVIDEND = "INCOME_DIVIDEND"
    INCOME_COUPON = "INCOME_COUPON"
    INCOME_INTEREST = "INCOME_INTEREST"
    EXPENSE_COMMISSION = "EXPENSE_COMMISSION"
    EXPENSE_TAX = "EXPENSE_TAX"


class LedgerClassification(StrEnum):
    OPENING_BALANCE = "OPENING_BALANCE"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    DIVIDEND = "DIVIDEND"
    COUPON = "COUPON"
    INTEREST = "INTEREST"
    COMMISSION = "COMMISSION"
    TAX = "TAX"
    TRADE_SETTLEMENT = "TRADE_SETTLEMENT"
    REFUND = "REFUND"
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
    REVERSAL = "REVERSAL"


class IdentityRelation(StrEnum):
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    ECONOMIC_MATCH = "ECONOMIC_MATCH"
    DISTINCT = "DISTINCT"


def _is_plain_int(value: object) -> bool:
    return type(value) is int


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _canonical_amount(minor_units: int) -> str:
    sign = "-" if minor_units < 0 else ""
    whole, fraction = divmod(abs(minor_units), NANO_FACTOR)
    return f"{sign}{whole}.{fraction:09d}"


@dataclass(frozen=True, slots=True)
class Money:
    currency: str
    minor_units: int
    scale: int = 9

    def __post_init__(self) -> None:
        if self.currency != "RUB" or not isinstance(self.currency, str):
            raise MoneyError(MoneyReason.CURRENCY_UNSUPPORTED)
        if not _is_plain_int(self.scale) or self.scale != 9:
            raise MoneyError(MoneyReason.SCALE_INVALID)
        if not _is_plain_int(self.minor_units):
            raise MoneyError(MoneyReason.TYPE_INVALID)
        if not MONEY_MIN_MINOR_UNITS <= self.minor_units <= MONEY_MAX_MINOR_UNITS:
            raise MoneyError(MoneyReason.MINOR_UNITS_OUT_OF_RANGE)

    @classmethod
    def from_units_nano(
        cls,
        units: int,
        nano: int,
        currency: str = "RUB",
    ) -> Money:
        if currency != "RUB" or not isinstance(currency, str):
            raise MoneyError(MoneyReason.CURRENCY_UNSUPPORTED)
        if not _is_plain_int(units):
            raise MoneyError(MoneyReason.TYPE_INVALID)
        if not _is_plain_int(nano):
            raise MoneyError(MoneyReason.TYPE_INVALID)
        if not WIRE_UNITS_MIN <= units <= WIRE_UNITS_MAX:
            raise MoneyError(MoneyReason.WIRE_UNITS_OUT_OF_RANGE)
        if not WIRE_NANO_MIN <= nano <= WIRE_NANO_MAX:
            raise MoneyError(MoneyReason.WIRE_NANO_OUT_OF_RANGE)
        if (units > 0 and nano < 0) or (units < 0 and nano > 0):
            raise MoneyError(MoneyReason.WIRE_SIGN_NON_CANONICAL)
        minor_units = units * NANO_FACTOR + nano
        if not MONEY_MIN_MINOR_UNITS <= minor_units <= MONEY_MAX_MINOR_UNITS:
            raise MoneyError(MoneyReason.MINOR_UNITS_OUT_OF_RANGE)
        return cls(currency=currency, minor_units=minor_units, scale=9)

    @classmethod
    def from_canonical_dict(cls, value: object) -> Money:
        if not isinstance(value, Mapping) or frozenset(value) != _MONEY_KEYS:
            raise MoneyError(MoneyReason.CANONICAL_FORMAT_INVALID)
        if (
            value["domain"] != "v3.10-money"
            or not _is_plain_int(value["version"])
            or value["version"] != MONEY_DOMAIN_VERSION
        ):
            raise MoneyError(MoneyReason.CANONICAL_FORMAT_INVALID)
        currency = value["currency"]
        if currency != "RUB" or not isinstance(currency, str):
            raise MoneyError(MoneyReason.CURRENCY_UNSUPPORTED)
        scale = value["scale"]
        if not _is_plain_int(scale) or scale != 9:
            raise MoneyError(MoneyReason.SCALE_INVALID)
        encoded_minor_units = value["minor_units"]
        if (
            not isinstance(encoded_minor_units, str)
            or _CANONICAL_INTEGER_RE.fullmatch(encoded_minor_units) is None
        ):
            raise MoneyError(MoneyReason.CANONICAL_FORMAT_INVALID)
        minor_units = int(encoded_minor_units)
        if not MONEY_MIN_MINOR_UNITS <= minor_units <= MONEY_MAX_MINOR_UNITS:
            raise MoneyError(MoneyReason.MINOR_UNITS_OUT_OF_RANGE)
        encoded_amount = value["amount"]
        if (
            not isinstance(encoded_amount, str)
            or _CANONICAL_AMOUNT_RE.fullmatch(encoded_amount) is None
        ):
            raise MoneyError(MoneyReason.CANONICAL_FORMAT_INVALID)
        if encoded_amount != _canonical_amount(minor_units):
            raise MoneyError(MoneyReason.CANONICAL_FORMAT_INVALID)
        return cls(currency=currency, minor_units=minor_units, scale=scale)

    @property
    def canonical_amount(self) -> str:
        return _canonical_amount(self.minor_units)

    @property
    def amount(self) -> Decimal:
        return Decimal(self.canonical_amount)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "amount": self.canonical_amount,
            "currency": self.currency,
            "domain": "v3.10-money",
            "minor_units": str(self.minor_units),
            "scale": self.scale,
            "version": MONEY_DOMAIN_VERSION,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)

    def _binary_result(self, other: object, *, subtract: bool) -> Money:
        if not isinstance(other, Money) or (
            other.currency != self.currency or other.scale != self.scale
        ):
            raise MoneyError(MoneyReason.INCOMPATIBLE_OPERAND)
        result = (
            self.minor_units - other.minor_units
            if subtract
            else self.minor_units + other.minor_units
        )
        if not MONEY_MIN_MINOR_UNITS <= result <= MONEY_MAX_MINOR_UNITS:
            raise MoneyError(MoneyReason.ARITHMETIC_OVERFLOW)
        return Money(currency=self.currency, minor_units=result, scale=self.scale)

    def __add__(self, other: object) -> Money:
        return self._binary_result(other, subtract=False)

    def __sub__(self, other: object) -> Money:
        return self._binary_result(other, subtract=True)

    def __neg__(self) -> Money:
        result = -self.minor_units
        if not MONEY_MIN_MINOR_UNITS <= result <= MONEY_MAX_MINOR_UNITS:
            raise MoneyError(MoneyReason.ARITHMETIC_OVERFLOW)
        return Money(currency=self.currency, minor_units=result, scale=self.scale)


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    account_scope_sha256: str
    source_kind: str
    source_scope_sha256: str
    source_content_sha256: str

    def __post_init__(self) -> None:
        if not _is_sha256(self.account_scope_sha256):
            raise LedgerError(LedgerReason.SOURCE_INVALID)
        if (
            not isinstance(self.source_kind, str)
            or _SOURCE_KIND_RE.fullmatch(self.source_kind) is None
        ):
            raise LedgerError(LedgerReason.SOURCE_INVALID)
        if not _is_sha256(self.source_scope_sha256):
            raise LedgerError(LedgerReason.SOURCE_INVALID)
        if not _is_sha256(self.source_content_sha256):
            raise LedgerError(LedgerReason.SOURCE_INVALID)

    @classmethod
    def from_canonical_dict(cls, value: object) -> SourceIdentity:
        if not isinstance(value, Mapping) or frozenset(value) != _SOURCE_KEYS:
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        if (
            value["domain"] != "v3.10-cash-ledger-source"
            or not _is_plain_int(value["version"])
            or value["version"] != SOURCE_IDENTITY_VERSION
        ):
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        return cls(
            account_scope_sha256=value["account_scope_sha256"],
            source_kind=value["source_kind"],
            source_scope_sha256=value["source_scope_sha256"],
            source_content_sha256=value["source_content_sha256"],
        )

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "domain": "v3.10-cash-ledger-source",
            "source_content_sha256": self.source_content_sha256,
            "source_kind": self.source_kind,
            "source_scope_sha256": self.source_scope_sha256,
            "version": SOURCE_IDENTITY_VERSION,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@dataclass(frozen=True, slots=True)
class LedgerPosting:
    line_no: int
    account: LedgerAccount
    money: Money

    def __post_init__(self) -> None:
        if not _is_plain_int(self.line_no) or not 1 <= self.line_no <= MAX_LINE_NUMBER:
            raise LedgerError(LedgerReason.LINE_NUMBER_INVALID)
        if not isinstance(self.account, LedgerAccount):
            raise LedgerError(LedgerReason.ACCOUNT_UNSUPPORTED)
        if not isinstance(self.money, Money):
            raise LedgerError(LedgerReason.TYPE_INVALID)
        Money(
            currency=self.money.currency,
            minor_units=self.money.minor_units,
            scale=self.money.scale,
        )
        if not (
            LEDGER_POSTING_MIN_MINOR_UNITS
            <= self.money.minor_units
            <= LEDGER_POSTING_MAX_MINOR_UNITS
        ):
            raise LedgerError(LedgerReason.POSTING_OUT_OF_REVERSIBLE_RANGE)
        if self.money.minor_units == 0:
            raise LedgerError(LedgerReason.ZERO_POSTING)

    @classmethod
    def from_canonical_dict(cls, value: object) -> LedgerPosting:
        if not isinstance(value, Mapping) or frozenset(value) != _POSTING_KEYS:
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        if (
            value["domain"] != "v3.10-cash-ledger-posting"
            or not _is_plain_int(value["version"])
            or value["version"] != LEDGER_POSTING_VERSION
        ):
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        encoded_line_no = value["line_no"]
        if (
            not isinstance(encoded_line_no, str)
            or _CANONICAL_LINE_RE.fullmatch(encoded_line_no) is None
        ):
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        line_no = int(encoded_line_no)
        if line_no > MAX_LINE_NUMBER:
            raise LedgerError(LedgerReason.LINE_NUMBER_INVALID)
        try:
            account = LedgerAccount(value["account"])
        except (TypeError, ValueError) as exc:
            raise LedgerError(LedgerReason.ACCOUNT_UNSUPPORTED) from exc
        money = Money.from_canonical_dict(value["money"])
        return cls(line_no=line_no, account=account, money=money)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account": self.account.value,
            "domain": "v3.10-cash-ledger-posting",
            "line_no": str(self.line_no),
            "money": self.money.to_canonical_dict(),
            "version": LEDGER_POSTING_VERSION,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


def _timestamp_is_valid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    match = _TIMESTAMP_RE.fullmatch(value)
    if match is None:
        return False
    year, month, day_value, hour, minute, second, _ = map(int, match.groups())
    try:
        date(year, month, day_value)
    except ValueError:
        return False
    return hour <= 23 and minute <= 59 and second <= 59


def _accounting_pattern_is_valid(
    classification: LedgerClassification,
    postings: Sequence[LedgerPosting],
) -> bool:
    if len(postings) != 2:
        return False
    by_account = {posting.account: posting.money.minor_units for posting in postings}
    if len(by_account) != 2 or LedgerAccount.ASSET_BROKER_CASH not in by_account:
        return False
    cash = by_account[LedgerAccount.ASSET_BROKER_CASH]
    if cash == 0:
        return False
    counterpart = next(
        account for account in by_account if account != LedgerAccount.ASSET_BROKER_CASH
    )
    if by_account[counterpart] != -cash:
        return False

    fixed: dict[LedgerClassification, tuple[LedgerAccount, int]] = {
        LedgerClassification.OPENING_BALANCE: (
            LedgerAccount.EQUITY_OPENING_BALANCE,
            1,
        ),
        LedgerClassification.DEPOSIT: (LedgerAccount.EQUITY_EXTERNAL_FLOW, 1),
        LedgerClassification.WITHDRAWAL: (LedgerAccount.EQUITY_EXTERNAL_FLOW, -1),
        LedgerClassification.DIVIDEND: (LedgerAccount.INCOME_DIVIDEND, 1),
        LedgerClassification.COUPON: (LedgerAccount.INCOME_COUPON, 1),
        LedgerClassification.INTEREST: (LedgerAccount.INCOME_INTEREST, 1),
        LedgerClassification.COMMISSION: (LedgerAccount.EXPENSE_COMMISSION, -1),
        LedgerClassification.TAX: (LedgerAccount.EXPENSE_TAX, -1),
    }
    expected = fixed.get(classification)
    if expected is not None:
        expected_account, cash_sign = expected
        return counterpart is expected_account and (cash > 0) == (cash_sign > 0)
    if classification is LedgerClassification.TRADE_SETTLEMENT:
        return counterpart is LedgerAccount.ASSET_TRADE_CLEARING
    if classification is LedgerClassification.REFUND:
        return cash > 0 and counterpart in {
            LedgerAccount.EXPENSE_COMMISSION,
            LedgerAccount.EXPENSE_TAX,
        }
    if classification is LedgerClassification.MANUAL_ADJUSTMENT:
        return counterpart is LedgerAccount.EQUITY_MANUAL_ADJUSTMENT
    return False


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    classification: LedgerClassification
    effective_at: str
    source: SourceIdentity
    postings: tuple[LedgerPosting, ...]
    classification_version: int = LEDGER_CLASSIFICATION_VERSION
    chart_version: int = LEDGER_CHART_VERSION
    reversal_of_sha256: str | None = None
    corrects_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.classification, LedgerClassification):
            raise LedgerError(LedgerReason.CLASSIFICATION_UNSUPPORTED)
        if (
            not _is_plain_int(self.classification_version)
            or self.classification_version != LEDGER_CLASSIFICATION_VERSION
        ):
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        if (
            not _is_plain_int(self.chart_version)
            or self.chart_version != LEDGER_CHART_VERSION
        ):
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        if not _timestamp_is_valid(self.effective_at):
            raise LedgerError(LedgerReason.TIMESTAMP_INVALID)
        if not isinstance(self.source, SourceIdentity):
            raise LedgerError(LedgerReason.SOURCE_INVALID)
        SourceIdentity(
            account_scope_sha256=self.source.account_scope_sha256,
            source_kind=self.source.source_kind,
            source_scope_sha256=self.source.source_scope_sha256,
            source_content_sha256=self.source.source_content_sha256,
        )
        if isinstance(self.postings, (str, bytes)) or not isinstance(
            self.postings, Sequence
        ):
            raise LedgerError(LedgerReason.TYPE_INVALID)
        postings = tuple(self.postings)
        if any(not isinstance(posting, LedgerPosting) for posting in postings):
            raise LedgerError(LedgerReason.TYPE_INVALID)
        for posting in postings:
            LedgerPosting(
                line_no=posting.line_no,
                account=posting.account,
                money=posting.money,
            )
        if len(postings) < 2:
            raise LedgerError(LedgerReason.ACCOUNTING_PATTERN_INVALID)
        line_numbers = [posting.line_no for posting in postings]
        if len(line_numbers) != len(set(line_numbers)):
            raise LedgerError(LedgerReason.DUPLICATE_LINE)
        canonical_postings = tuple(
            sorted(postings, key=lambda posting: posting.line_no)
        )
        totals: dict[str, int] = {}
        for posting in canonical_postings:
            totals[posting.money.currency] = (
                totals.get(posting.money.currency, 0) + posting.money.minor_units
            )
        if any(total != 0 for total in totals.values()):
            raise LedgerError(LedgerReason.UNBALANCED)
        self._validate_lineage_fields()
        if self.classification is not LedgerClassification.REVERSAL and not (
            len(canonical_postings) == 2
            and _accounting_pattern_is_valid(self.classification, canonical_postings)
        ):
            raise LedgerError(LedgerReason.ACCOUNTING_PATTERN_INVALID)
        object.__setattr__(self, "postings", canonical_postings)

    def _validate_lineage_fields(self) -> None:
        if self.reversal_of_sha256 is not None and not _is_sha256(
            self.reversal_of_sha256
        ):
            raise LedgerError(LedgerReason.HASH_INVALID)
        if self.corrects_sha256 is not None and not _is_sha256(self.corrects_sha256):
            raise LedgerError(LedgerReason.HASH_INVALID)
        if self.reversal_of_sha256 is not None and self.corrects_sha256 is not None:
            raise LedgerError(LedgerReason.LINEAGE_INVALID)
        if self.classification is LedgerClassification.REVERSAL:
            if self.reversal_of_sha256 is None or self.corrects_sha256 is not None:
                raise LedgerError(LedgerReason.LINEAGE_INVALID)
        elif self.reversal_of_sha256 is not None:
            raise LedgerError(LedgerReason.LINEAGE_INVALID)

    @classmethod
    def from_canonical_dict(cls, value: object) -> LedgerTransaction:
        if not isinstance(value, Mapping) or frozenset(value) != _TRANSACTION_KEYS:
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        if (
            value["domain"] != "v3.10-cash-ledger-transaction"
            or not _is_plain_int(value["version"])
            or value["version"] != LEDGER_TRANSACTION_VERSION
        ):
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        try:
            classification = LedgerClassification(value["classification"])
        except (TypeError, ValueError) as exc:
            raise LedgerError(LedgerReason.CLASSIFICATION_UNSUPPORTED) from exc
        classification_version = value["classification_version"]
        if (
            not _is_plain_int(classification_version)
            or classification_version != LEDGER_CLASSIFICATION_VERSION
        ):
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        chart_version = value["chart_version"]
        if not _is_plain_int(chart_version) or chart_version != LEDGER_CHART_VERSION:
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        effective_at = value["effective_at"]
        if not _timestamp_is_valid(effective_at):
            raise LedgerError(LedgerReason.TIMESTAMP_INVALID)
        source = SourceIdentity.from_canonical_dict(value["source"])
        raw_postings = value["postings"]
        if not isinstance(raw_postings, list):
            raise LedgerError(LedgerReason.TYPE_INVALID)
        postings = tuple(
            LedgerPosting.from_canonical_dict(posting) for posting in raw_postings
        )
        return cls(
            classification=classification,
            effective_at=effective_at,
            source=source,
            postings=postings,
            classification_version=classification_version,
            chart_version=chart_version,
            reversal_of_sha256=value["reversal_of_sha256"],
            corrects_sha256=value["corrects_sha256"],
        )

    @classmethod
    def reversing(
        cls,
        original: LedgerTransaction,
        *,
        effective_at: str,
        source: SourceIdentity,
    ) -> LedgerTransaction:
        if not isinstance(original, LedgerTransaction):
            raise LedgerError(LedgerReason.TYPE_INVALID)
        if (
            original.classification is LedgerClassification.REVERSAL
            or original.reversal_of_sha256 is not None
            or original.corrects_sha256 is not None
        ):
            raise LedgerError(LedgerReason.LINEAGE_INVALID)
        postings = tuple(
            LedgerPosting(
                line_no=posting.line_no,
                account=posting.account,
                money=-posting.money,
            )
            for posting in original.postings
        )
        return cls(
            classification=LedgerClassification.REVERSAL,
            effective_at=effective_at,
            source=source,
            postings=postings,
            reversal_of_sha256=original.sha256,
        )

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "chart_version": self.chart_version,
            "classification": self.classification.value,
            "classification_version": self.classification_version,
            "corrects_sha256": self.corrects_sha256,
            "domain": "v3.10-cash-ledger-transaction",
            "effective_at": self.effective_at,
            "postings": [posting.to_canonical_dict() for posting in self.postings],
            "reversal_of_sha256": self.reversal_of_sha256,
            "source": self.source.to_canonical_dict(),
            "version": LEDGER_TRANSACTION_VERSION,
        }

    def to_economic_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.source.account_scope_sha256,
            "chart_version": self.chart_version,
            "classification": self.classification.value,
            "classification_version": self.classification_version,
            "domain": "v3.10-cash-ledger-economic",
            "effective_at": self.effective_at,
            "postings": [posting.to_canonical_dict() for posting in self.postings],
            "version": LEDGER_ECONOMIC_VERSION,
        }

    @property
    def source_sha256(self) -> str:
        return self.source.sha256

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)

    @property
    def economic_bytes(self) -> bytes:
        return _canonical_bytes(self.to_economic_dict())

    @property
    def economic_sha256(self) -> str:
        return _sha256(self.economic_bytes)

    def relation_to(self, other: LedgerTransaction) -> IdentityRelation:
        return compare_transaction_identities(self, other)


def compare_transaction_identities(
    left: LedgerTransaction,
    right: LedgerTransaction,
) -> IdentityRelation:
    if not isinstance(left, LedgerTransaction) or not isinstance(
        right, LedgerTransaction
    ):
        raise LedgerError(LedgerReason.TYPE_INVALID)
    if left.sha256 == right.sha256:
        return IdentityRelation.EXACT_DUPLICATE
    if left.source_sha256 == right.source_sha256:
        return IdentityRelation.SOURCE_CONFLICT
    if left.economic_sha256 == right.economic_sha256:
        return IdentityRelation.ECONOMIC_MATCH
    return IdentityRelation.DISTINCT


def _postings_are_exact_negation(
    original: Sequence[LedgerPosting],
    reversal: Sequence[LedgerPosting],
) -> bool:
    if len(original) != len(reversal):
        return False
    for source_posting, reversing_posting in zip(original, reversal, strict=True):
        if (
            source_posting.line_no != reversing_posting.line_no
            or source_posting.account is not reversing_posting.account
            or source_posting.money.currency != reversing_posting.money.currency
            or source_posting.money.scale != reversing_posting.money.scale
            or source_posting.money.minor_units != -reversing_posting.money.minor_units
        ):
            return False
    return True


@dataclass(frozen=True, slots=True)
class LedgerCorrectionBundle:
    original: LedgerTransaction
    reversal: LedgerTransaction
    correction: LedgerTransaction

    def __post_init__(self) -> None:
        if not all(
            isinstance(transaction, LedgerTransaction)
            for transaction in (self.original, self.reversal, self.correction)
        ):
            raise LedgerError(LedgerReason.TYPE_INVALID)
        if (
            self.original.classification is LedgerClassification.REVERSAL
            or self.original.reversal_of_sha256 is not None
            or self.original.corrects_sha256 is not None
        ):
            raise LedgerError(LedgerReason.LINEAGE_INVALID)
        if (
            self.reversal.classification is not LedgerClassification.REVERSAL
            or self.reversal.reversal_of_sha256 != self.original.sha256
            or self.reversal.corrects_sha256 is not None
        ):
            raise LedgerError(LedgerReason.LINEAGE_INVALID)
        if (
            self.reversal.source.account_scope_sha256
            != self.original.source.account_scope_sha256
        ):
            raise LedgerError(LedgerReason.LINEAGE_INVALID)
        if not _postings_are_exact_negation(
            self.original.postings,
            self.reversal.postings,
        ):
            raise LedgerError(LedgerReason.LINEAGE_INVALID)
        if (
            self.correction.classification is LedgerClassification.REVERSAL
            or self.correction.corrects_sha256 != self.original.sha256
            or self.correction.reversal_of_sha256 is not None
            or self.correction.source.account_scope_sha256
            != self.original.source.account_scope_sha256
        ):
            raise LedgerError(LedgerReason.LINEAGE_INVALID)
        if not _accounting_pattern_is_valid(
            self.correction.classification,
            self.correction.postings,
        ):
            raise LedgerError(LedgerReason.ACCOUNTING_PATTERN_INVALID)
        if self.correction.economic_sha256 == self.original.economic_sha256:
            raise LedgerError(LedgerReason.LINEAGE_INVALID)

    @classmethod
    def from_canonical_dict(
        cls,
        value: object,
        *,
        original: LedgerTransaction,
        reversal: LedgerTransaction,
        correction: LedgerTransaction,
    ) -> LedgerCorrectionBundle:
        if not isinstance(value, Mapping) or frozenset(value) != _BUNDLE_KEYS:
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        if (
            value["domain"] != "v3.10-cash-ledger-correction-bundle"
            or not _is_plain_int(value["version"])
            or value["version"] != LEDGER_CORRECTION_BUNDLE_VERSION
        ):
            raise LedgerError(LedgerReason.CANONICAL_FORMAT_INVALID)
        for key in ("original_sha256", "reversal_sha256", "correction_sha256"):
            if not _is_sha256(value[key]):
                raise LedgerError(LedgerReason.HASH_INVALID)
        if not all(
            isinstance(transaction, LedgerTransaction)
            for transaction in (original, reversal, correction)
        ):
            raise LedgerError(LedgerReason.TYPE_INVALID)
        if (
            value["original_sha256"] != original.sha256
            or value["reversal_sha256"] != reversal.sha256
            or value["correction_sha256"] != correction.sha256
        ):
            raise LedgerError(LedgerReason.LINEAGE_INVALID)
        return cls(original=original, reversal=reversal, correction=correction)

    @property
    def original_sha256(self) -> str:
        return self.original.sha256

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "correction_sha256": self.correction.sha256,
            "domain": "v3.10-cash-ledger-correction-bundle",
            "original_sha256": self.original.sha256,
            "reversal_sha256": self.reversal.sha256,
            "version": LEDGER_CORRECTION_BUNDLE_VERSION,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


@dataclass(frozen=True, slots=True)
class CorrectionBundleSetEntry:
    original_sha256: str
    bundle_sha256: str
    relation: IdentityRelation


def validate_correction_bundle_set(
    values: Iterable[LedgerCorrectionBundle],
) -> tuple[CorrectionBundleSetEntry, ...]:
    if isinstance(values, (str, bytes)):
        raise LedgerError(LedgerReason.TYPE_INVALID)
    try:
        supplied = tuple(values)
    except TypeError as exc:
        raise LedgerError(LedgerReason.TYPE_INVALID) from exc

    validated: list[LedgerCorrectionBundle] = []
    for bundle in supplied:
        if not isinstance(bundle, LedgerCorrectionBundle):
            raise LedgerError(LedgerReason.TYPE_INVALID)
        validated.append(
            LedgerCorrectionBundle(
                original=bundle.original,
                reversal=bundle.reversal,
                correction=bundle.correction,
            )
        )

    grouped: dict[str, dict[str, int]] = {}
    for bundle in sorted(
        validated,
        key=lambda item: (item.original_sha256, item.sha256),
    ):
        hashes = grouped.setdefault(bundle.original_sha256, {})
        hashes[bundle.sha256] = hashes.get(bundle.sha256, 0) + 1
    if any(len(hashes) > 1 for hashes in grouped.values()):
        raise LedgerError(LedgerReason.LINEAGE_CONFLICT)

    return tuple(
        CorrectionBundleSetEntry(
            original_sha256=original_sha256,
            bundle_sha256=bundle_sha256,
            relation=(
                IdentityRelation.EXACT_DUPLICATE
                if count > 1
                else IdentityRelation.DISTINCT
            ),
        )
        for original_sha256, hashes in sorted(grouped.items())
        for bundle_sha256, count in sorted(hashes.items())
    )


__all__ = [
    "CROSS_LANGUAGE_FIXTURE_VERSION",
    "LEDGER_CHART_VERSION",
    "LEDGER_CLASSIFICATION_VERSION",
    "LEDGER_CORRECTION_BUNDLE_VERSION",
    "LEDGER_ECONOMIC_VERSION",
    "LEDGER_POSTING_MAX_MINOR_UNITS",
    "LEDGER_POSTING_MIN_MINOR_UNITS",
    "LEDGER_POSTING_VERSION",
    "LEDGER_TRANSACTION_VERSION",
    "MAX_LINE_NUMBER",
    "MONEY_DOMAIN_VERSION",
    "MONEY_MAX_MINOR_UNITS",
    "MONEY_MIN_MINOR_UNITS",
    "NANO_FACTOR",
    "SOURCE_IDENTITY_VERSION",
    "WIRE_NANO_MAX",
    "WIRE_NANO_MIN",
    "WIRE_UNITS_MAX",
    "WIRE_UNITS_MIN",
    "CorrectionBundleSetEntry",
    "IdentityRelation",
    "LedgerAccount",
    "LedgerClassification",
    "LedgerCorrectionBundle",
    "LedgerError",
    "LedgerPosting",
    "LedgerReason",
    "LedgerTransaction",
    "Money",
    "MoneyError",
    "MoneyReason",
    "SourceIdentity",
    "compare_transaction_identities",
    "validate_correction_bundle_set",
]
