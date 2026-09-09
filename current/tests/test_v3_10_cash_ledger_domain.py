from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from trading_robot.cash_ledger_domain import (
    CROSS_LANGUAGE_FIXTURE_VERSION,
    LEDGER_CHART_VERSION,
    LEDGER_CLASSIFICATION_VERSION,
    LEDGER_CORRECTION_BUNDLE_VERSION,
    LEDGER_ECONOMIC_VERSION,
    LEDGER_POSTING_MAX_MINOR_UNITS,
    LEDGER_POSTING_MIN_MINOR_UNITS,
    LEDGER_POSTING_VERSION,
    LEDGER_TRANSACTION_VERSION,
    MONEY_DOMAIN_VERSION,
    MONEY_MAX_MINOR_UNITS,
    MONEY_MIN_MINOR_UNITS,
    SOURCE_IDENTITY_VERSION,
    WIRE_UNITS_MAX,
    WIRE_UNITS_MIN,
    IdentityRelation,
    LedgerAccount,
    LedgerClassification,
    LedgerCorrectionBundle,
    LedgerError,
    LedgerPosting,
    LedgerReason,
    LedgerTransaction,
    Money,
    MoneyError,
    MoneyReason,
    SourceIdentity,
    compare_transaction_identities,
    validate_correction_bundle_set,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v3_10_cash_ledger_vectors.json"
MODULE_PATH = Path(__file__).parents[1] / "trading_robot" / "cash_ledger_domain.py"
TIMESTAMP = "2026-01-02T03:04:05.123456789Z"
ORIGINAL_SHA = "bb14525732cba2e2050c2743c30c07a7cbbb705ffc3ded42fa8f58368c6a7b53"
REVERSAL_SHA = "8207bbda82b045350235f78e0a2e132f142b98c006d254ff08d25ceb39d8c86b"
CORRECTION_SHA = "77b098c218c50faa4f2d205bf903ce216e6d3c45935c00805477b839d9738004"
BUNDLE_SHA = "73a8a1776c5f6e6262bed3a92628fc29f62b11a75d17334d47a12a702351799f"


def _source(
    *,
    account: str = "1",
    scope: str = "2",
    content: str = "3",
    kind: str = "SYNTHETIC",
) -> SourceIdentity:
    return SourceIdentity(
        account_scope_sha256=account * 64,
        source_kind=kind,
        source_scope_sha256=scope * 64,
        source_content_sha256=content * 64,
    )


def _posting(line_no: int, account: LedgerAccount, minor_units: int) -> LedgerPosting:
    return LedgerPosting(line_no, account, Money("RUB", minor_units))


def _transaction(
    classification: LedgerClassification,
    counterpart: LedgerAccount,
    cash_minor_units: int,
    *,
    source: SourceIdentity | None = None,
    effective_at: str = TIMESTAMP,
    reverse_order: bool = False,
    reversal_of_sha256: str | None = None,
    corrects_sha256: str | None = None,
) -> LedgerTransaction:
    postings = (
        _posting(1, LedgerAccount.ASSET_BROKER_CASH, cash_minor_units),
        _posting(2, counterpart, -cash_minor_units),
    )
    if reverse_order:
        postings = postings[::-1]
    return LedgerTransaction(
        classification=classification,
        effective_at=effective_at,
        source=source or _source(),
        postings=postings,
        reversal_of_sha256=reversal_of_sha256,
        corrects_sha256=corrects_sha256,
    )


def _original() -> LedgerTransaction:
    return _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        1_000_000_000,
    )


def _lineage(
    *, correction_amount: int = 2_000_000_000
) -> tuple[
    LedgerTransaction,
    LedgerTransaction,
    LedgerTransaction,
    LedgerCorrectionBundle,
]:
    original = _original()
    reversal = LedgerTransaction.reversing(
        original,
        effective_at="2026-01-02T03:05:00.000000000Z",
        source=_source(content="4", kind="SYNTHETIC_REVERSAL"),
    )
    correction = _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        correction_amount,
        source=_source(content="5", kind="SYNTHETIC_CORRECTION"),
        corrects_sha256=original.sha256,
    )
    return (
        original,
        reversal,
        correction,
        LedgerCorrectionBundle(
            original,
            reversal,
            correction,
        ),
    )


def _assert_money_reason(reason: MoneyReason, action: Any) -> None:
    with pytest.raises(MoneyError) as caught:
        action()
    assert caught.value.reason is reason


def _assert_ledger_reason(reason: LedgerReason, action: Any) -> None:
    with pytest.raises(LedgerError) as caught:
        action()
    assert caught.value.reason is reason


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def test_v310_cl1_01_units_nano_exact_scale_nine_money() -> None:
    assert Money.from_units_nano(7, 0).minor_units == 7_000_000_000
    assert Money.from_units_nano(0, 10_000_000).canonical_amount == "0.010000000"
    assert Money.from_units_nano(0, -1).canonical_amount == "-0.000000001"
    assert Money.from_units_nano(-2, -3).minor_units == -2_000_000_003
    value = Money.from_units_nano(0, 1)
    assert value.currency == "RUB"
    assert value.scale == 9
    assert not isinstance(value.amount, float)
    with pytest.raises(FrozenInstanceError):
        value.minor_units = 2  # type: ignore[misc]


def test_v310_cl1_02_asymmetric_money_and_symmetric_posting_bounds() -> None:
    minimum = Money.from_units_nano(WIRE_UNITS_MIN, -999_999_999)
    maximum = Money.from_units_nano(WIRE_UNITS_MAX, 999_999_999)
    assert minimum.minor_units == MONEY_MIN_MINOR_UNITS
    assert maximum.minor_units == MONEY_MAX_MINOR_UNITS
    assert MONEY_MIN_MINOR_UNITS == -MONEY_MAX_MINOR_UNITS - 1_000_000_000
    _assert_ledger_reason(
        LedgerReason.POSTING_OUT_OF_REVERSIBLE_RANGE,
        lambda: LedgerPosting(1, LedgerAccount.ASSET_BROKER_CASH, minimum),
    )
    for amount in (LEDGER_POSTING_MIN_MINOR_UNITS, LEDGER_POSTING_MAX_MINOR_UNITS):
        posting = _posting(1, LedgerAccount.ASSET_BROKER_CASH, amount)
        assert (-posting.money).minor_units == -amount


def test_v310_cl1_03_closed_money_reasons_and_ordered_failures() -> None:
    assert {reason.value for reason in MoneyReason} == {
        "TYPE_INVALID",
        "CURRENCY_UNSUPPORTED",
        "SCALE_INVALID",
        "WIRE_UNITS_OUT_OF_RANGE",
        "WIRE_NANO_OUT_OF_RANGE",
        "WIRE_SIGN_NON_CANONICAL",
        "MINOR_UNITS_OUT_OF_RANGE",
        "CANONICAL_FORMAT_INVALID",
        "INCOMPATIBLE_OPERAND",
        "ARITHMETIC_OVERFLOW",
    }
    _assert_money_reason(
        MoneyReason.CURRENCY_UNSUPPORTED,
        lambda: Money("usd", "bad", scale=2),
    )
    _assert_money_reason(MoneyReason.SCALE_INVALID, lambda: Money("RUB", "bad", 2))
    _assert_money_reason(MoneyReason.TYPE_INVALID, lambda: Money("RUB", True))
    _assert_money_reason(
        MoneyReason.CURRENCY_UNSUPPORTED,
        lambda: Money.from_units_nano("bad", "bad", currency="rub"),
    )
    _assert_money_reason(
        MoneyReason.TYPE_INVALID,
        lambda: Money.from_units_nano(True, "bad"),
    )
    _assert_money_reason(
        MoneyReason.WIRE_UNITS_OUT_OF_RANGE,
        lambda: Money.from_units_nano(WIRE_UNITS_MAX + 1, 1_000_000_000),
    )
    _assert_money_reason(
        MoneyReason.WIRE_NANO_OUT_OF_RANGE,
        lambda: Money.from_units_nano(1, -1_000_000_000),
    )
    _assert_money_reason(
        MoneyReason.WIRE_SIGN_NON_CANONICAL,
        lambda: Money.from_units_nano(1, -1),
    )
    _assert_money_reason(
        MoneyReason.MINOR_UNITS_OUT_OF_RANGE,
        lambda: Money("RUB", MONEY_MAX_MINOR_UNITS + 1),
    )


def test_v310_cl1_04_money_canonical_form_and_round_trip() -> None:
    money = Money("RUB", 1_000_000_000)
    expected = (
        b'{"amount":"1.000000000","currency":"RUB","domain":"v3.10-money",'
        b'"minor_units":"1000000000","scale":9,"version":1}'
    )
    assert money.canonical_bytes == expected
    assert Money.from_canonical_dict(json.loads(expected)).canonical_bytes == expected
    valid = money.to_canonical_dict()
    invalid_values = []
    for key in valid:
        candidate = dict(valid)
        del candidate[key]
        invalid_values.append(candidate)
    invalid_values.extend(
        [
            {**valid, "extra": None},
            {**valid, "domain": "v3.10-money-other", "currency": "usd"},
            {**valid, "version": True},
            {**valid, "minor_units": "01"},
            {**valid, "minor_units": "-0"},
            {**valid, "minor_units": 1_000_000_000},
            {**valid, "amount": "1.0"},
            {**valid, "amount": "1.000000001"},
        ]
    )
    for candidate in invalid_values:
        _assert_money_reason(
            MoneyReason.CANONICAL_FORMAT_INVALID,
            lambda candidate=candidate: Money.from_canonical_dict(candidate),
        )
    for oversized_minor_units in ("1" * 5_000, "-" + "1" * 5_000):
        candidate = {
            **valid,
            "minor_units": oversized_minor_units,
            "amount": "not-reached",
        }
        _assert_money_reason(
            MoneyReason.MINOR_UNITS_OUT_OF_RANGE,
            lambda candidate=candidate: Money.from_canonical_dict(candidate),
        )


def test_v310_cl1_05_source_exact_identity_privacy_and_failure_order() -> None:
    source = _source()
    expected = (
        b'{"account_scope_sha256":"1111111111111111111111111111111111111111111111111111111111111111",'
        b'"domain":"v3.10-cash-ledger-source","source_content_sha256":"3333333333333333333333333333333333333333333333333333333333333333",'
        b'"source_kind":"SYNTHETIC","source_scope_sha256":"2222222222222222222222222222222222222222222222222222222222222222","version":1}'
    )
    assert source.canonical_bytes == expected
    assert (
        source.sha256
        == "37b37643d08b5e591bf184f95542dfe20c0d627427cc4757c7e5255158d7ee20"
    )
    assert SourceIdentity.from_canonical_dict(json.loads(expected)) == source
    lowered = expected.lower()
    for forbidden in (b"account_id", b"token", b"payload", b"cursor", b"operation_id"):
        assert forbidden not in lowered
    _assert_ledger_reason(
        LedgerReason.SOURCE_INVALID,
        lambda: SourceIdentity("X", "bad", "Y", "Z"),
    )
    invalid_container = {**source.to_canonical_dict(), "extra": "raw"}
    invalid_container["account_scope_sha256"] = "X"
    _assert_ledger_reason(
        LedgerReason.CANONICAL_FORMAT_INVALID,
        lambda: SourceIdentity.from_canonical_dict(invalid_container),
    )


def test_v310_cl1_06_posting_canonical_line_and_reversible_range() -> None:
    posting = _posting(1, LedgerAccount.ASSET_BROKER_CASH, 1_000_000_000)
    assert (
        posting.sha256
        == "5764735e2fbb39adb0118772020e5cde8ce978497058270ecafd0613ff77fa20"
    )
    encoded = posting.to_canonical_dict()
    assert set(encoded) == {"account", "domain", "line_no", "money", "version"}
    assert encoded["line_no"] == "1"
    assert LedgerPosting.from_canonical_dict(encoded) == posting
    for line_no in (1, "01", "+1", " 1", 1):
        candidate = dict(encoded)
        candidate["line_no"] = line_no
        if line_no == "1":
            continue
        _assert_ledger_reason(
            LedgerReason.CANONICAL_FORMAT_INVALID,
            lambda candidate=candidate: LedgerPosting.from_canonical_dict(candidate),
        )
    candidate = dict(encoded)
    candidate["line_no"] = "2147483648"
    _assert_ledger_reason(
        LedgerReason.LINE_NUMBER_INVALID,
        lambda: LedgerPosting.from_canonical_dict(candidate),
    )
    oversized_line = {
        **encoded,
        "line_no": "1" * 5_000,
        "account": "UNKNOWN",
        "money": None,
    }
    _assert_ledger_reason(
        LedgerReason.LINE_NUMBER_INVALID,
        lambda: LedgerPosting.from_canonical_dict(oversized_line),
    )
    unknown_account = dict(encoded)
    unknown_account["account"] = "UNKNOWN"
    _assert_ledger_reason(
        LedgerReason.ACCOUNT_UNSUPPORTED,
        lambda: LedgerPosting.from_canonical_dict(unknown_account),
    )
    invalid_nested_money = dict(encoded)
    invalid_nested_money["money"] = {**encoded["money"], "currency": "USD"}  # type: ignore[dict-item]
    _assert_money_reason(
        MoneyReason.CURRENCY_UNSUPPORTED,
        lambda: LedgerPosting.from_canonical_dict(invalid_nested_money),
    )
    _assert_ledger_reason(
        LedgerReason.POSTING_OUT_OF_REVERSIBLE_RANGE,
        lambda: LedgerPosting(
            1,
            LedgerAccount.ASSET_BROKER_CASH,
            Money("RUB", MONEY_MIN_MINOR_UNITS),
        ),
    )


@pytest.mark.parametrize(
    "timestamp",
    [
        "0001-01-01T00:00:00.000000000Z",
        "2000-02-29T23:59:59.999999999Z",
        "9999-12-31T23:59:59.999999999Z",
    ],
)
def test_v310_cl1_07_timestamp_exact_grammar_and_calendar(timestamp: str) -> None:
    transaction = _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        1,
        effective_at=timestamp,
    )
    assert transaction.effective_at == timestamp
    for invalid in (
        "0000-01-01T00:00:00.000000000Z",
        "1900-02-29T00:00:00.000000000Z",
        "2026-02-30T00:00:00.000000000Z",
        "2026-01-01T24:00:00.000000000Z",
        "2026-01-01T23:59:60.000000000Z",
        "2026-01-01T00:00:00.00000000Z",
        "2026-01-01T00:00:00.000000000+00:00",
    ):
        _assert_ledger_reason(
            LedgerReason.TIMESTAMP_INVALID,
            lambda invalid=invalid: _transaction(
                LedgerClassification.DEPOSIT,
                LedgerAccount.EQUITY_EXTERNAL_FLOW,
                1,
                effective_at=invalid,
            ),
        )


def test_v310_cl1_08_full_transaction_known_answer_and_null_policy() -> None:
    transaction = _original()
    encoded = transaction.to_canonical_dict()
    assert set(encoded) == {
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
    assert encoded["corrects_sha256"] is None
    assert encoded["reversal_of_sha256"] is None
    assert transaction.sha256 == ORIGINAL_SHA
    assert (
        LedgerTransaction.from_canonical_dict(encoded).canonical_bytes
        == transaction.canonical_bytes
    )
    missing_lineage = dict(encoded)
    del missing_lineage["corrects_sha256"]
    _assert_ledger_reason(
        LedgerReason.CANONICAL_FORMAT_INVALID,
        lambda: LedgerTransaction.from_canonical_dict(missing_lineage),
    )


def test_v310_cl1_09_economic_form_and_three_identity_axes() -> None:
    transaction = _original()
    economic = transaction.to_economic_dict()
    assert set(economic) == {
        "account_scope_sha256",
        "chart_version",
        "classification",
        "classification_version",
        "domain",
        "effective_at",
        "postings",
        "version",
    }
    assert "source" not in economic
    assert "corrects_sha256" not in economic
    assert "reversal_of_sha256" not in economic
    assert transaction.source_sha256 == transaction.source.sha256
    assert (
        transaction.economic_sha256
        == "02f37175436a6e25fefdfc0b3750713572a486e056bb6b32cd13be959b666788"
    )
    assert transaction.sha256 != transaction.economic_sha256


def test_v310_cl1_10_exact_arithmetic_and_no_float_authority() -> None:
    one = Money("RUB", 1)
    two = Money("RUB", 2)
    assert (one + two).minor_units == 3
    assert (two - one).minor_units == 1
    assert (-one).minor_units == -1
    assert not isinstance((one + two).amount, float)
    _assert_money_reason(MoneyReason.INCOMPATIBLE_OPERAND, lambda: one + 1.0)
    _assert_money_reason(
        MoneyReason.ARITHMETIC_OVERFLOW,
        lambda: Money("RUB", MONEY_MAX_MINOR_UNITS) + one,
    )
    _assert_money_reason(
        MoneyReason.ARITHMETIC_OVERFLOW,
        lambda: -Money("RUB", MONEY_MIN_MINOR_UNITS),
    )


def test_v310_cl1_11_deposit_withdrawal_polarity() -> None:
    _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        1,
    )
    _transaction(
        LedgerClassification.WITHDRAWAL,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        -1,
    )
    for classification, sign in (
        (LedgerClassification.DEPOSIT, -1),
        (LedgerClassification.WITHDRAWAL, 1),
    ):
        _assert_ledger_reason(
            LedgerReason.ACCOUNTING_PATTERN_INVALID,
            lambda classification=classification, sign=sign: _transaction(
                classification,
                LedgerAccount.EQUITY_EXTERNAL_FLOW,
                sign,
            ),
        )


@pytest.mark.parametrize(
    ("classification", "counterpart", "cash_sign"),
    [
        (LedgerClassification.DIVIDEND, LedgerAccount.INCOME_DIVIDEND, 1),
        (LedgerClassification.COUPON, LedgerAccount.INCOME_COUPON, 1),
        (LedgerClassification.INTEREST, LedgerAccount.INCOME_INTEREST, 1),
        (LedgerClassification.COMMISSION, LedgerAccount.EXPENSE_COMMISSION, -1),
        (LedgerClassification.TAX, LedgerAccount.EXPENSE_TAX, -1),
    ],
)
def test_v310_cl1_12_income_expense_accounting_patterns(
    classification: LedgerClassification,
    counterpart: LedgerAccount,
    cash_sign: int,
) -> None:
    _transaction(classification, counterpart, cash_sign)
    _assert_ledger_reason(
        LedgerReason.ACCOUNTING_PATTERN_INVALID,
        lambda: _transaction(classification, counterpart, -cash_sign),
    )
    _assert_ledger_reason(
        LedgerReason.ACCOUNTING_PATTERN_INVALID,
        lambda: _transaction(
            classification, LedgerAccount.EQUITY_EXTERNAL_FLOW, cash_sign
        ),
    )


def test_v310_cl1_13_trade_settlement_uses_only_trade_clearing() -> None:
    for cash_sign in (-1, 1):
        _transaction(
            LedgerClassification.TRADE_SETTLEMENT,
            LedgerAccount.ASSET_TRADE_CLEARING,
            cash_sign,
        )
    for forbidden in (
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        LedgerAccount.INCOME_DIVIDEND,
        LedgerAccount.EXPENSE_COMMISSION,
    ):
        _assert_ledger_reason(
            LedgerReason.ACCOUNTING_PATTERN_INVALID,
            lambda forbidden=forbidden: _transaction(
                LedgerClassification.TRADE_SETTLEMENT,
                forbidden,
                1,
            ),
        )


def test_v310_cl1_14_opening_refund_and_manual_patterns() -> None:
    _transaction(
        LedgerClassification.OPENING_BALANCE,
        LedgerAccount.EQUITY_OPENING_BALANCE,
        1,
    )
    for account in (LedgerAccount.EXPENSE_COMMISSION, LedgerAccount.EXPENSE_TAX):
        _transaction(LedgerClassification.REFUND, account, 1)
    for cash_sign in (-1, 1):
        _transaction(
            LedgerClassification.MANUAL_ADJUSTMENT,
            LedgerAccount.EQUITY_MANUAL_ADJUSTMENT,
            cash_sign,
        )
    for classification, wrong_account in (
        (LedgerClassification.OPENING_BALANCE, LedgerAccount.EQUITY_EXTERNAL_FLOW),
        (LedgerClassification.REFUND, LedgerAccount.INCOME_DIVIDEND),
        (LedgerClassification.MANUAL_ADJUSTMENT, LedgerAccount.EQUITY_OPENING_BALANCE),
    ):
        _assert_ledger_reason(
            LedgerReason.ACCOUNTING_PATTERN_INVALID,
            lambda classification=classification, wrong_account=wrong_account: (
                _transaction(
                    classification,
                    wrong_account,
                    1,
                )
            ),
        )


def test_v310_cl1_15_structural_transaction_rejections() -> None:
    _assert_ledger_reason(
        LedgerReason.ZERO_POSTING,
        lambda: _posting(1, LedgerAccount.ASSET_BROKER_CASH, 0),
    )
    for line_no in (True, 0, 2_147_483_648):
        _assert_ledger_reason(
            LedgerReason.LINE_NUMBER_INVALID,
            lambda line_no=line_no: _posting(
                line_no,  # type: ignore[arg-type]
                LedgerAccount.ASSET_BROKER_CASH,
                1,
            ),
        )
    valid = _original()
    duplicate = (
        valid.postings[0],
        _posting(1, LedgerAccount.EQUITY_EXTERNAL_FLOW, -1_000_000_000),
    )
    _assert_ledger_reason(
        LedgerReason.DUPLICATE_LINE,
        lambda: LedgerTransaction(
            LedgerClassification.DEPOSIT,
            TIMESTAMP,
            _source(),
            duplicate,
        ),
    )
    unbalanced = (
        valid.postings[0],
        _posting(2, LedgerAccount.EQUITY_EXTERNAL_FLOW, -1),
    )
    _assert_ledger_reason(
        LedgerReason.UNBALANCED,
        lambda: LedgerTransaction(
            LedgerClassification.DEPOSIT,
            TIMESTAMP,
            _source(),
            unbalanced,
        ),
    )
    _assert_ledger_reason(
        LedgerReason.TYPE_INVALID,
        lambda: LedgerTransaction(
            LedgerClassification.DEPOSIT,
            TIMESTAMP,
            _source(),
            (valid.postings[0], object()),  # type: ignore[arg-type]
        ),
    )
    balanced_three_leg = (
        _posting(1, LedgerAccount.ASSET_BROKER_CASH, 2),
        _posting(2, LedgerAccount.EQUITY_EXTERNAL_FLOW, -1),
        _posting(3, LedgerAccount.EQUITY_MANUAL_ADJUSTMENT, -1),
    )
    _assert_ledger_reason(
        LedgerReason.ACCOUNTING_PATTERN_INVALID,
        lambda: LedgerTransaction(
            LedgerClassification.DEPOSIT,
            TIMESTAMP,
            _source(),
            balanced_three_leg,
        ),
    )


def test_v310_cl1_16_numeric_order_normalization_is_deterministic() -> None:
    transaction = _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        1_000_000_000,
        reverse_order=True,
    )
    assert [posting.line_no for posting in transaction.postings] == [1, 2]
    first_bytes = transaction.canonical_bytes
    first_hash = transaction.sha256
    for _ in range(5):
        assert transaction.canonical_bytes == first_bytes
        assert transaction.sha256 == first_hash


def test_v310_cl1_17_source_economic_full_identity_relation_matrix() -> None:
    original = _original()
    source_conflict = _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        2_000_000_000,
    )
    economic_match = _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        1_000_000_000,
        source=_source(content="4", kind="SYNTHETIC_SECOND_OBSERVATION"),
    )
    distinct = _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        1_000_000_000,
        effective_at="2026-01-02T03:04:06.123456789Z",
        source=_source(content="4", kind="SYNTHETIC_SECOND_OBSERVATION"),
    )
    assert (
        compare_transaction_identities(original, original)
        is IdentityRelation.EXACT_DUPLICATE
    )
    assert original.relation_to(source_conflict) is IdentityRelation.SOURCE_CONFLICT
    assert original.relation_to(economic_match) is IdentityRelation.ECONOMIC_MATCH
    assert original.relation_to(distinct) is IdentityRelation.DISTINCT


def test_v310_cl1_18_reversal_exact_negation_and_representability() -> None:
    original = _transaction(
        LedgerClassification.MANUAL_ADJUSTMENT,
        LedgerAccount.EQUITY_MANUAL_ADJUSTMENT,
        LEDGER_POSTING_MAX_MINOR_UNITS,
    )
    reversal = LedgerTransaction.reversing(
        original,
        effective_at="2026-01-02T03:05:00.000000000Z",
        source=_source(content="4", kind="SYNTHETIC_REVERSAL"),
    )
    assert reversal.classification is LedgerClassification.REVERSAL
    assert reversal.reversal_of_sha256 == original.sha256
    assert reversal.source.account_scope_sha256 == original.source.account_scope_sha256
    for before, after in zip(original.postings, reversal.postings, strict=True):
        assert after.line_no == before.line_no
        assert after.account is before.account
        assert after.money.minor_units == -before.money.minor_units


def test_v310_cl1_19_lineage_valid_and_invalid_targets_rejected() -> None:
    original, reversal, correction, bundle = _lineage()
    assert bundle.original is original
    assert bundle.reversal is reversal
    assert bundle.correction is correction
    _assert_ledger_reason(
        LedgerReason.LINEAGE_INVALID,
        lambda: LedgerTransaction.reversing(
            reversal,
            effective_at=TIMESTAMP,
            source=_source(content="6", kind="SYNTHETIC_REVERSAL_SECOND"),
        ),
    )
    wrong_target = _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        2_000_000_000,
        source=_source(content="5", kind="SYNTHETIC_CORRECTION"),
        corrects_sha256="a" * 64,
    )
    _assert_ledger_reason(
        LedgerReason.LINEAGE_INVALID,
        lambda: LedgerCorrectionBundle(original, reversal, wrong_target),
    )
    correction_of_reversal = _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        2_000_000_000,
        source=_source(content="5", kind="SYNTHETIC_CORRECTION"),
        corrects_sha256=reversal.sha256,
    )
    _assert_ledger_reason(
        LedgerReason.LINEAGE_INVALID,
        lambda: LedgerCorrectionBundle(original, reversal, correction_of_reversal),
    )
    no_op = _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        1_000_000_000,
        source=_source(content="6", kind="SYNTHETIC_CORRECTION"),
        corrects_sha256=original.sha256,
    )
    _assert_ledger_reason(
        LedgerReason.LINEAGE_INVALID,
        lambda: LedgerCorrectionBundle(original, reversal, no_op),
    )


def test_v310_cl1_20_correction_bundle_known_answer() -> None:
    original, reversal, correction, bundle = _lineage()
    assert original.sha256 == ORIGINAL_SHA
    assert reversal.sha256 == REVERSAL_SHA
    assert correction.sha256 == CORRECTION_SHA
    expected = (
        b'{"correction_sha256":"77b098c218c50faa4f2d205bf903ce216e6d3c45935c00805477b839d9738004",'
        b'"domain":"v3.10-cash-ledger-correction-bundle",'
        b'"original_sha256":"bb14525732cba2e2050c2743c30c07a7cbbb705ffc3ded42fa8f58368c6a7b53",'
        b'"reversal_sha256":"8207bbda82b045350235f78e0a2e132f142b98c006d254ff08d25ceb39d8c86b","version":1}'
    )
    assert bundle.canonical_bytes == expected
    assert bundle.sha256 == BUNDLE_SHA
    assert (
        LedgerCorrectionBundle.from_canonical_dict(
            json.loads(expected),
            original=original,
            reversal=reversal,
            correction=correction,
        )
        == bundle
    )


def test_v310_cl1_21_bundle_set_order_duplicate_and_conflict() -> None:
    original, reversal, _, bundle = _lineage()
    first = validate_correction_bundle_set([bundle, bundle])
    second = validate_correction_bundle_set([bundle, bundle][::-1])
    assert first == second
    assert len(first) == 1
    assert first[0].relation is IdentityRelation.EXACT_DUPLICATE
    alternate_correction = _transaction(
        LedgerClassification.DEPOSIT,
        LedgerAccount.EQUITY_EXTERNAL_FLOW,
        3_000_000_000,
        source=_source(content="6", kind="SYNTHETIC_CORRECTION_BRANCH"),
        corrects_sha256=original.sha256,
    )
    alternate = LedgerCorrectionBundle(original, reversal, alternate_correction)
    for values in ([bundle, alternate], [alternate, bundle]):
        _assert_ledger_reason(
            LedgerReason.LINEAGE_CONFLICT,
            lambda values=values: validate_correction_bundle_set(values),
        )


def test_v310_cl1_22_closed_ledger_reasons_and_ordered_failures() -> None:
    assert (
        MONEY_DOMAIN_VERSION,
        SOURCE_IDENTITY_VERSION,
        LEDGER_POSTING_VERSION,
        LEDGER_TRANSACTION_VERSION,
        LEDGER_ECONOMIC_VERSION,
        LEDGER_CORRECTION_BUNDLE_VERSION,
        LEDGER_CHART_VERSION,
        LEDGER_CLASSIFICATION_VERSION,
        CROSS_LANGUAGE_FIXTURE_VERSION,
    ) == (1,) * 9
    assert {account.value for account in LedgerAccount} == {
        "ASSET_BROKER_CASH",
        "ASSET_TRADE_CLEARING",
        "EQUITY_OPENING_BALANCE",
        "EQUITY_EXTERNAL_FLOW",
        "EQUITY_MANUAL_ADJUSTMENT",
        "INCOME_DIVIDEND",
        "INCOME_COUPON",
        "INCOME_INTEREST",
        "EXPENSE_COMMISSION",
        "EXPENSE_TAX",
    }
    assert {classification.value for classification in LedgerClassification} == {
        "OPENING_BALANCE",
        "DEPOSIT",
        "WITHDRAWAL",
        "DIVIDEND",
        "COUPON",
        "INTEREST",
        "COMMISSION",
        "TAX",
        "TRADE_SETTLEMENT",
        "REFUND",
        "MANUAL_ADJUSTMENT",
        "REVERSAL",
    }
    assert {reason.value for reason in LedgerReason} == {
        "TYPE_INVALID",
        "HASH_INVALID",
        "ACCOUNT_UNSUPPORTED",
        "CLASSIFICATION_UNSUPPORTED",
        "TIMESTAMP_INVALID",
        "LINE_NUMBER_INVALID",
        "ZERO_POSTING",
        "POSTING_OUT_OF_REVERSIBLE_RANGE",
        "DUPLICATE_LINE",
        "UNBALANCED",
        "SOURCE_INVALID",
        "CANONICAL_FORMAT_INVALID",
        "ACCOUNTING_PATTERN_INVALID",
        "LINEAGE_INVALID",
        "LINEAGE_CONFLICT",
    }
    _assert_ledger_reason(
        LedgerReason.LINE_NUMBER_INVALID,
        lambda: LedgerPosting(0, "UNKNOWN", object()),  # type: ignore[arg-type]
    )
    _assert_ledger_reason(
        LedgerReason.ACCOUNT_UNSUPPORTED,
        lambda: LedgerPosting(1, "UNKNOWN", object()),  # type: ignore[arg-type]
    )
    valid = _original()
    _assert_ledger_reason(
        LedgerReason.CLASSIFICATION_UNSUPPORTED,
        lambda: LedgerTransaction(
            "UNKNOWN",  # type: ignore[arg-type]
            "invalid",
            object(),  # type: ignore[arg-type]
            "invalid",  # type: ignore[arg-type]
        ),
    )
    _assert_ledger_reason(
        LedgerReason.CANONICAL_FORMAT_INVALID,
        lambda: LedgerTransaction(
            LedgerClassification.DEPOSIT,
            "invalid",
            object(),  # type: ignore[arg-type]
            "invalid",  # type: ignore[arg-type]
            classification_version=2,
            chart_version=2,
        ),
    )
    _assert_ledger_reason(
        LedgerReason.TIMESTAMP_INVALID,
        lambda: LedgerTransaction(
            LedgerClassification.DEPOSIT,
            "invalid",
            object(),  # type: ignore[arg-type]
            "invalid",  # type: ignore[arg-type]
        ),
    )
    _assert_ledger_reason(
        LedgerReason.HASH_INVALID,
        lambda: LedgerTransaction(
            valid.classification,
            valid.effective_at,
            valid.source,
            valid.postings,
            reversal_of_sha256="BAD",
            corrects_sha256="BAD",
        ),
    )
    _assert_ledger_reason(
        LedgerReason.TYPE_INVALID,
        lambda: LedgerCorrectionBundle(object(), object(), object()),  # type: ignore[arg-type]
    )
    original, reversal, correction, bundle = _lineage()
    malformed_bundle = {**bundle.to_canonical_dict(), "extra": "forbidden"}
    malformed_bundle["original_sha256"] = "BAD"
    _assert_ledger_reason(
        LedgerReason.CANONICAL_FORMAT_INVALID,
        lambda: LedgerCorrectionBundle.from_canonical_dict(
            malformed_bundle,
            original=object(),  # type: ignore[arg-type]
            reversal=object(),  # type: ignore[arg-type]
            correction=object(),  # type: ignore[arg-type]
        ),
    )
    invalid_hash_bundle = bundle.to_canonical_dict()
    invalid_hash_bundle["original_sha256"] = "BAD"
    _assert_ledger_reason(
        LedgerReason.HASH_INVALID,
        lambda: LedgerCorrectionBundle.from_canonical_dict(
            invalid_hash_bundle,
            original=original,
            reversal=reversal,
            correction=correction,
        ),
    )
    _assert_ledger_reason(
        LedgerReason.TYPE_INVALID,
        lambda: validate_correction_bundle_set("not-a-bundle-set"),
    )


def test_v310_cl1_23_ast_import_and_side_effect_boundary() -> None:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert imported_roots <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "hashlib",
        "json",
        "re",
        "typing",
    }
    assert imported_roots.isdisjoint(
        {
            "asyncio",
            "logging",
            "os",
            "pathlib",
            "requests",
            "socket",
            "sqlite3",
            "subprocess",
            "time",
            "tkinter",
            "uuid",
        }
    )
    top_level_calls = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    assert top_level_calls == []


def test_v310_cl1_24_fixture_schema_bytes_hashes_and_frozen_samples() -> None:
    fixture_bytes = FIXTURE_PATH.read_bytes()
    assert fixture_bytes.decode("ascii").encode("ascii") == fixture_bytes
    fixture = json.loads(fixture_bytes)
    assert set(fixture) == {"domain", "version", "vectors"}
    assert fixture["domain"] == "v3.10-cash-ledger-fixture"
    assert type(fixture["version"]) is int
    assert fixture["version"] == CROSS_LANGUAGE_FIXTURE_VERSION
    assert isinstance(fixture["vectors"], list)

    observed_ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    for vector in fixture["vectors"]:
        common = {"id", "kind", "canonical_json_ascii", "sha256"}
        extra = set()
        if vector["kind"] == "transaction":
            extra = {"economic_json_ascii", "economic_sha256", "source_sha256"}
        elif vector["kind"] == "bundle":
            extra = {"original_sha256", "reversal_sha256", "correction_sha256"}
        assert set(vector) == common | extra
        assert vector["id"] not in observed_ids
        observed_ids.add(vector["id"])
        by_id[vector["id"]] = vector

        canonical_ascii = vector["canonical_json_ascii"]
        canonical_bytes = canonical_ascii.encode("ascii")
        parsed = json.loads(canonical_ascii)
        assert _canonical_json(parsed) == canonical_ascii
        assert hashlib.sha256(canonical_bytes).hexdigest() == vector["sha256"]
        if vector["kind"] == "money":
            assert Money.from_canonical_dict(parsed).canonical_bytes == canonical_bytes
        elif vector["kind"] == "source":
            assert (
                SourceIdentity.from_canonical_dict(parsed).canonical_bytes
                == canonical_bytes
            )
        elif vector["kind"] == "posting":
            assert (
                LedgerPosting.from_canonical_dict(parsed).canonical_bytes
                == canonical_bytes
            )
        elif vector["kind"] == "transaction":
            transaction = LedgerTransaction.from_canonical_dict(parsed)
            assert transaction.canonical_bytes == canonical_bytes
            economic_ascii = vector["economic_json_ascii"]
            assert _canonical_json(json.loads(economic_ascii)) == economic_ascii
            assert transaction.economic_bytes == economic_ascii.encode("ascii")
            assert transaction.economic_sha256 == vector["economic_sha256"]
            assert transaction.source_sha256 == vector["source_sha256"]
        elif vector["kind"] == "bundle":
            assert set(parsed) == {
                "correction_sha256",
                "domain",
                "original_sha256",
                "reversal_sha256",
                "version",
            }
            assert parsed["original_sha256"] == vector["original_sha256"]
            assert parsed["reversal_sha256"] == vector["reversal_sha256"]
            assert parsed["correction_sha256"] == vector["correction_sha256"]
        else:
            pytest.fail(f"unknown fixture kind: {vector['kind']}")

    assert (
        by_id["money-one-rub"]["sha256"]
        == "84f2a0a835a9925f376b0f8deb78df59665c4f7cb66bad5b46ee7d88044050ed"
    )
    assert (
        by_id["source-synthetic"]["sha256"]
        == "37b37643d08b5e591bf184f95542dfe20c0d627427cc4757c7e5255158d7ee20"
    )
    assert (
        by_id["posting-cash-positive"]["sha256"]
        == "5764735e2fbb39adb0118772020e5cde8ce978497058270ecafd0613ff77fa20"
    )
    assert by_id["transaction-deposit-original"]["sha256"] == ORIGINAL_SHA
    assert (
        by_id["transaction-deposit-original"]["economic_sha256"]
        == "02f37175436a6e25fefdfc0b3750713572a486e056bb6b32cd13be959b666788"
    )
    assert by_id["transaction-reversal"]["sha256"] == REVERSAL_SHA
    assert by_id["transaction-correction"]["sha256"] == CORRECTION_SHA
    assert by_id["bundle-known-answer"]["sha256"] == BUNDLE_SHA
    assert by_id["bundle-exact-duplicate"]["sha256"] == BUNDLE_SHA
    assert by_id["bundle-conflicting-branch"]["sha256"] != BUNDLE_SHA
