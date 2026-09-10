"""Contract and adversarial tests for the bounded CL3 read adapter."""

from __future__ import annotations

import ast
import copy
import inspect
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

_CURRENT = Path(__file__).resolve().parents[1]
if str(_CURRENT) not in sys.path:
    sys.path.insert(0, str(_CURRENT))

from trading_robot import broker_read_adapters as cl3  # noqa: E402
from trading_robot import cash_ledger_domain as ledger  # noqa: E402

_FIXTURE_PATH = (
    Path(__file__).with_name("fixtures") / "v3_10_broker_read_adapters_vectors.json"
)
_VECTORS = json.loads(_FIXTURE_PATH.read_text(encoding="ascii"))
_KNOWN = _VECTORS["known_answer"]


class _Clock:
    def __init__(self, values: list[object] | None = None) -> None:
        self.values = list(values if values is not None else [0])
        self.last = self.values[-1]
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        if self.values:
            self.last = self.values.pop(0)
        return self.last


class _Transport:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[dict[str, object], int]] = []

    def __call__(self, payload: object, timeout_ns: int) -> object:
        assert isinstance(payload, dict)
        self.calls.append((copy.deepcopy(payload), timeout_ns))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return copy.deepcopy(outcome)


def _request(
    outcomes: list[object] | None = None,
    *,
    transport: object | None = None,
    clock: object | None = None,
    wait: object | None = None,
    **overrides: object,
) -> cl3.BrokerReadRequest:
    source = _KNOWN["request"]
    values: dict[str, object] = {
        "environment": cl3.BrokerEnvironment(source["environment"]),
        "raw_account_id": source["raw_account_id"],
        "identity_key": bytes.fromhex(source["identity_key_hex"]),
        "identity_key_id": source["identity_key_id"],
        "from_inclusive": source["from_inclusive"],
        "to_exclusive": source["to_exclusive"],
        "limit": source["limit"],
        "max_pages": source["max_pages"],
        "max_items": source["max_items"],
        "absolute_deadline_ns": source["absolute_deadline_ns"],
        "retry_policy": cl3.RetryPolicy(1, 100_000, ()),
        "transport": transport
        if transport is not None
        else _Transport(outcomes if outcomes is not None else [_KNOWN["response"]]),
        "monotonic_ns": clock if clock is not None else _Clock(),
        "wait_ns": wait if wait is not None else (lambda delay: None),
    }
    values.update(overrides)
    return cl3.BrokerReadRequest(**values)


def _item(**changes: object) -> dict[str, object]:
    value = copy.deepcopy(_KNOWN["response"]["items"][0])
    value.update(changes)
    return value


def _response(
    items: list[object], has_next: bool = False, next_cursor: str = ""
) -> dict[str, object]:
    return {"hasNext": has_next, "items": items, "nextCursor": next_cursor}


def _error(
    call: object, reason: cl3.BrokerReadReason, cause: object = None
) -> cl3.BrokerReadError:
    try:
        call()  # type: ignore[operator]
    except cl3.BrokerReadError as error:
        assert error.reason is reason
        if cause is not None:
            assert error.cause_reason is cause
        assert error.__cause__ is None
        assert error.__context__ is None
        return error
    raise AssertionError(f"expected {reason.value}")


def _batch_for(item: dict[str, object]) -> cl3.BrokerReadBatch:
    return cl3.collect_tbank_operations(_request([_response([item])]))


def test_v310_cl3_01_exact_exports_and_immutable_shapes() -> None:
    assert cl3.__all__ == (
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
    assert {name for name in vars(cl3) if not name.startswith("_")} == set(cl3.__all__)
    request = _request()
    assert "SYNTHETIC-ACCOUNT-01" not in repr(request)
    assert _KNOWN["request"]["identity_key_hex"] not in repr(request)
    try:
        request.limit = 2  # type: ignore[misc]
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("request must be immutable")
    assert tuple(cl3.BrokerEnvironment) == (
        cl3.BrokerEnvironment.PRODUCTION,
        cl3.BrokerEnvironment.SANDBOX,
    )
    money_signature = inspect.signature(cl3.money_value_to_money)
    timestamp_signature = inspect.signature(cl3.normalize_provider_timestamp)
    collect_signature = inspect.signature(cl3.collect_tbank_operations)
    assert list(money_signature.parameters) == ["value"]
    assert money_signature.return_annotation == "Money"
    assert list(timestamp_signature.parameters) == ["value"]
    assert timestamp_signature.return_annotation == "str"
    assert list(collect_signature.parameters) == ["request"]
    assert collect_signature.return_annotation == "BrokerReadBatch"


def test_v310_cl3_02_money_value_codec() -> None:
    cases = (
        ({"currency": "RUB", "units": "0", "nano": 0}, 0),
        ({"currency": "RUB", "units": "1", "nano": 2}, 1_000_000_002),
        ({"currency": "RUB", "units": "-1", "nano": -2}, -1_000_000_002),
        (
            {"currency": "RUB", "units": "9223372036854775807", "nano": 999999999},
            9223372036854775807999999999,
        ),
        (
            {
                "currency": "RUB",
                "units": "-9223372036854775808",
                "nano": -999999999,
            },
            -9223372036854775808999999999,
        ),
    )
    for encoded, expected in cases:
        assert cl3.money_value_to_money(encoded).minor_units == expected
    invalid = (
        None,
        {},
        {"currency": "USD", "units": "0", "nano": 0},
        {"currency": "RUB", "units": "00", "nano": 0},
        {"currency": "RUB", "units": "-0", "nano": 0},
        {"currency": "RUB", "units": "+1", "nano": 0},
        {"currency": "RUB", "units": "1", "nano": True},
        {"currency": "RUB", "units": "1", "nano": 1_000_000_000},
        {"currency": "RUB", "units": "1", "nano": -1},
        {"currency": "RUB", "units": "9223372036854775808", "nano": 0},
        {"currency": "RUB", "units": "9" * 5000, "nano": 0},
        {"currency": "RUB", "units": "0", "nano": 0, "extra": 1},
    )
    for encoded in invalid:
        _error(
            lambda encoded=encoded: cl3.money_value_to_money(encoded),
            cl3.BrokerReadReason.MONEY_INVALID,
        )


def test_v310_cl3_03_timestamp_normalization() -> None:
    for supplied, expected in _VECTORS["timestamp_vectors"]:
        assert cl3.normalize_provider_timestamp(supplied) == expected
    for invalid in (
        None,
        "2026-01-02T03:04:05.1Z",
        "2026-01-02T03:04:05.1234Z",
        "2026-01-02T03:04:05.1234567890Z",
        "2026-01-02T24:00:00Z",
        "2026-01-02T03:04:60Z",
        "2026-02-29T00:00:00Z",
        "2026-01-02T03:04:05+00:00",
        "2026-01-02T03:04:05z",
    ):
        _error(
            lambda invalid=invalid: cl3.normalize_provider_timestamp(invalid),
            cl3.BrokerReadReason.TIMESTAMP_INVALID,
        )


def test_v310_cl3_04_to_05_known_answer_identities_codec_observation() -> None:
    batch = cl3.collect_tbank_operations(_request())
    expected = _KNOWN["expected"]
    decision = batch.decisions[0]
    observation = decision.observation
    assert cl3.TBANK_OPERATION_CODEC.schema_sha256 == expected["schema_sha256"]
    assert cl3.TBANK_OPERATION_CODEC.sha256 == expected["descriptor_sha256"]
    assert observation.content_json_ascii == _KNOWN["sanitized_content_json_ascii"]
    assert observation.source.account_scope_sha256 == expected["account_scope_sha256"]
    assert observation.source.source_scope_sha256 == expected["source_scope_sha256"]
    assert observation.source.source_content_sha256 == expected["source_content_sha256"]
    assert observation.provenance_sha256 == expected["provenance_sha256"]
    assert observation.logical_source_sha256 == expected["logical_source_sha256"]
    assert observation.sha256 == expected["observation_sha256"]
    assert decision.transaction_proposal is not None
    assert decision.transaction_proposal.sha256 == expected["transaction_sha256"]
    assert (
        batch.watermark.request_fingerprint_sha256
        == expected["request_fingerprint_sha256"]
    )
    assert batch.watermark.page_chain_sha256 == expected["page_chain_sha256"]
    assert batch.watermark.sha256 == expected["watermark_sha256"]
    request = _request()
    assert (
        cl3._cursor_evidence(request, None, "REQUEST")
        == expected["initial_request_cursor_evidence_sha256"]
    )
    assert (
        cl3._cursor_evidence(request, None, "NEXT")
        == expected["terminal_next_cursor_evidence_sha256"]
    )
    assert (
        cl3._cursor_evidence(request, _KNOWN["response"]["items"][0]["cursor"], "ITEM")
        == expected["item_cursor_evidence_sha256"]
    )
    changed_cursor = _item(cursor="SYNTHETIC-CURSOR-ITEM-CHANGED")
    repeated = _batch_for(changed_cursor).decisions[0].observation
    assert repeated.canonical_bytes == observation.canonical_bytes
    assert repeated.sha256 == observation.sha256
    full_range = _batch_for(
        _item(
            type="OPERATION_TYPE_BUY_CARD",
            payment={
                "currency": "RUB",
                "units": "-9223372036854775808",
                "nano": -999999999,
            },
            commission={
                "currency": "RUB",
                "units": "-9223372036854775808",
                "nano": -999999999,
            },
        )
    ).decisions[0]
    full_range_content = json.loads(full_range.observation.content_json_ascii)
    assert len(full_range_content["payment_minor_units"]) == 29
    assert len(full_range_content["commission_minor_units"]) == 29


def test_v310_cl3_06_supported_type_sign_and_postings() -> None:
    groups = (
        (
            "INPUT INPUT_SWIFT INPUT_ACQUIRING INP_MULTI",
            1,
            ledger.LedgerClassification.DEPOSIT,
            ledger.LedgerAccount.EQUITY_EXTERNAL_FLOW,
        ),
        (
            "OUTPUT OUTPUT_SWIFT OUTPUT_ACQUIRING OUT_MULTI",
            -1,
            ledger.LedgerClassification.WITHDRAWAL,
            ledger.LedgerAccount.EQUITY_EXTERNAL_FLOW,
        ),
        (
            "DIVIDEND",
            1,
            ledger.LedgerClassification.DIVIDEND,
            ledger.LedgerAccount.INCOME_DIVIDEND,
        ),
        (
            "COUPON",
            1,
            ledger.LedgerClassification.COUPON,
            ledger.LedgerAccount.INCOME_COUPON,
        ),
        (
            "OVERNIGHT OVER_INCOME",
            1,
            ledger.LedgerClassification.INTEREST,
            ledger.LedgerAccount.INCOME_INTEREST,
        ),
        (
            "SERVICE_FEE MARGIN_FEE BROKER_FEE SUCCESS_FEE TRACK_MFEE TRACK_PFEE CASH_FEE OUT_FEE OUTPUT_PENALTY ADVICE_FEE OVER_COM",
            -1,
            ledger.LedgerClassification.COMMISSION,
            ledger.LedgerAccount.EXPENSE_COMMISSION,
        ),
        (
            "BOND_TAX TAX DIVIDEND_TAX BENEFIT_TAX TAX_PROGRESSIVE BOND_TAX_PROGRESSIVE DIVIDEND_TAX_PROGRESSIVE BENEFIT_TAX_PROGRESSIVE TAX_REPO_PROGRESSIVE TAX_REPO TAX_REPO_HOLD TAX_REPO_HOLD_PROGRESSIVE OUT_STAMP_DUTY",
            -1,
            ledger.LedgerClassification.TAX,
            ledger.LedgerAccount.EXPENSE_TAX,
        ),
        (
            "TAX_CORRECTION TAX_CORRECTION_PROGRESSIVE TAX_REPO_REFUND TAX_REPO_REFUND_PROGRESSIVE TAX_CORRECTION_COUPON",
            1,
            ledger.LedgerClassification.REFUND,
            ledger.LedgerAccount.EXPENSE_TAX,
        ),
        (
            "BUY BUY_MARGIN DELIVERY_BUY",
            -1,
            ledger.LedgerClassification.TRADE_SETTLEMENT,
            ledger.LedgerAccount.ASSET_TRADE_CLEARING,
        ),
        (
            "SELL SELL_MARGIN DELIVERY_SELL",
            1,
            ledger.LedgerClassification.TRADE_SETTLEMENT,
            ledger.LedgerAccount.ASSET_TRADE_CLEARING,
        ),
    )
    for names, sign, classification, counterpart in groups:
        for name in names.split():
            amount = "1" if sign > 0 else "-1"
            changes: dict[str, object] = {
                "type": "OPERATION_TYPE_" + name,
                "payment": {"currency": "RUB", "units": amount, "nano": 0},
            }
            if name in {
                "BUY",
                "BUY_MARGIN",
                "DELIVERY_BUY",
                "SELL",
                "SELL_MARGIN",
                "DELIVERY_SELL",
            }:
                changes.update(quantity="2", quantityDone="2", quantityRest="0")
            decision = _batch_for(_item(**changes)).decisions[0]
            assert decision.kind is cl3.BrokerDecisionKind.TRANSACTION_PROPOSED
            assert decision.reason is cl3.BrokerDecisionReason.CLASSIFIED
            transaction = decision.transaction_proposal
            assert (
                transaction is not None and transaction.classification is classification
            )
            assert (
                transaction.postings[0].account
                is ledger.LedgerAccount.ASSET_BROKER_CASH
            )
            assert transaction.postings[0].money.minor_units == sign * 1_000_000_000
            assert transaction.postings[1].account is counterpart
            assert transaction.postings[1].money.minor_units == -sign * 1_000_000_000


def test_v310_cl3_07_closed_nonproposal_outcomes() -> None:
    cases = (
        (
            {"state": "OPERATION_STATE_CANCELED"},
            cl3.BrokerDecisionKind.NOT_LEDGER_RELEVANT,
            cl3.BrokerDecisionReason.CANCELED,
        ),
        (
            {"state": "OPERATION_STATE_PROGRESS"},
            cl3.BrokerDecisionKind.REVIEW_REQUIRED,
            cl3.BrokerDecisionReason.PENDING,
        ),
        (
            {"state": "OPERATION_STATE_UNSPECIFIED"},
            cl3.BrokerDecisionKind.REVIEW_REQUIRED,
            cl3.BrokerDecisionReason.STATE_UNSPECIFIED,
        ),
        (
            {"type": "OPERATION_TYPE_FUTURE_NEW"},
            cl3.BrokerDecisionKind.REVIEW_REQUIRED,
            cl3.BrokerDecisionReason.UNKNOWN_OPERATION_TYPE,
        ),
        (
            {"type": "OPERATION_TYPE_BUY_CARD"},
            cl3.BrokerDecisionKind.REVIEW_REQUIRED,
            cl3.BrokerDecisionReason.UNSUPPORTED_OPERATION_TYPE,
        ),
    )
    for changes, kind, reason in cases:
        decision = _batch_for(_item(**changes)).decisions[0]
        assert (decision.kind, decision.reason, decision.transaction_proposal) == (
            kind,
            reason,
            None,
        )


def test_v310_cl3_08_ambiguity_priority() -> None:
    commission = {"currency": "RUB", "units": "-1", "nano": 0}
    cases = (
        ({"childOperations": [{}]}, cl3.BrokerDecisionReason.MULTI_COMPONENT_AMBIGUOUS),
        (
            {"parentOperationId": "SYNTHETIC-PARENT"},
            cl3.BrokerDecisionReason.MULTI_COMPONENT_AMBIGUOUS,
        ),
        (
            {"commission": commission},
            cl3.BrokerDecisionReason.MULTI_COMPONENT_AMBIGUOUS,
        ),
        (
            {
                "type": "OPERATION_TYPE_BUY",
                "payment": {"currency": "RUB", "units": "-1", "nano": 0},
            },
            cl3.BrokerDecisionReason.PARTIAL_EXECUTION_AMBIGUOUS,
        ),
        (
            {"payment": {"currency": "RUB", "units": "0", "nano": 0}},
            cl3.BrokerDecisionReason.ZERO_CASH_EFFECT,
        ),
        (
            {"payment": {"currency": "RUB", "units": "-1", "nano": 0}},
            cl3.BrokerDecisionReason.AMOUNT_SIGN_AMBIGUOUS,
        ),
    )
    for changes, reason in cases:
        decision = _batch_for(_item(**changes)).decisions[0]
        assert decision.reason is reason
        assert decision.transaction_proposal is None
    canceled = _batch_for(
        _item(state="OPERATION_STATE_CANCELED", commission=commission)
    ).decisions[0]
    assert canceled.reason is cl3.BrokerDecisionReason.CANCELED


def test_v310_cl3_09_response_schema_bounds_and_item_priority() -> None:
    _error(
        lambda: cl3.collect_tbank_operations(
            _request([{"items": [], "hasNext": False}])
        ),
        cl3.BrokerReadReason.RESPONSE_SCHEMA_INVALID,
    )
    aliased: list[object] = []
    _error(
        lambda: cl3.collect_tbank_operations(
            _request(
                [{"hasNext": False, "items": [aliased, aliased], "nextCursor": ""}],
                max_items=2,
            )
        ),
        cl3.BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED,
    )
    cyclic: list[object] = []
    cyclic.append(cyclic)
    _error(
        lambda: cl3.collect_tbank_operations(
            _request([{"hasNext": False, "items": cyclic, "nextCursor": ""}])
        ),
        cl3.BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED,
    )
    depth: object = None
    for _ in range(17):
        depth = [depth]
    for bounded_failure in (
        [None] * 10_001,
        {1: None},
        {"value": 1.5},
        {"value": "x" * 16_385},
        depth,
    ):
        _error(
            lambda value=bounded_failure: cl3.collect_tbank_operations(
                _request([value])
            ),
            cl3.BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED,
        )

    class _HostileDict(dict[str, object]):
        calls = 0

        def __getitem__(self, key: object) -> object:
            self.calls += 1
            raise RuntimeError("RAW-PROVIDER-SENTINEL")

    hostile_item = _HostileDict(_item())
    batch = cl3.collect_tbank_operations(
        _request(transport=lambda payload, timeout_ns: _response([hostile_item]))
    )
    assert len(batch.decisions) == 1
    assert hostile_item.calls == 0

    class _HostileList(list[object]):
        calls = 0

        def __len__(self) -> int:
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("RAW-LIST-SENTINEL")
            return list.__len__(self)

    hostile_items = _HostileList([_item()])
    batch = cl3.collect_tbank_operations(
        _request(transport=lambda payload, timeout_ns: _response(hostile_items))
    )
    assert len(batch.decisions) == 1
    assert hostile_items.calls == 0

    class _ExplodingMapping(Mapping[str, object]):
        def __getitem__(self, key: str) -> object:
            raise RuntimeError("RAW-MAPPING-SENTINEL")

        def __iter__(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("RAW-MAPPING-SENTINEL")

        def __len__(self) -> int:
            return 3

        def items(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("RAW-MAPPING-SENTINEL")

    _error(
        lambda: cl3.collect_tbank_operations(
            _request(transport=lambda payload, timeout_ns: _ExplodingMapping())
        ),
        cl3.BrokerReadReason.RESPONSE_BOUNDS_EXCEEDED,
    )
    _error(
        lambda: _batch_for(_item(extra="x")),
        cl3.BrokerReadReason.RESPONSE_SCHEMA_INVALID,
    )
    _error(
        lambda: _batch_for(_item(brokerAccountId="WRONG")),
        cl3.BrokerReadReason.ACCOUNT_MISMATCH,
    )
    _error(lambda: _batch_for(_item(id="")), cl3.BrokerReadReason.OPERATION_ID_INVALID)
    _error(lambda: _batch_for(_item(cursor="")), cl3.BrokerReadReason.CURSOR_INVALID)
    _error(
        lambda: _batch_for(_item(state="bad")), cl3.BrokerReadReason.ENUM_TOKEN_INVALID
    )
    _error(
        lambda: _batch_for(_item(date="bad")), cl3.BrokerReadReason.TIMESTAMP_INVALID
    )
    _error(
        lambda: _batch_for(_item(date="2027-01-02T00:00:00Z")),
        cl3.BrokerReadReason.ITEM_OUTSIDE_WINDOW,
    )
    _error(
        lambda: _batch_for(_item(quantity="01")), cl3.BrokerReadReason.QUANTITY_INVALID
    )
    _error(
        lambda: _batch_for(_item(quantity="9" * 5000)),
        cl3.BrokerReadReason.QUANTITY_INVALID,
    )
    _error(
        lambda: _batch_for(_item(parentOperationId=None)),
        cl3.BrokerReadReason.CHILD_OPERATIONS_INVALID,
    )
    mixed = _item(brokerAccountId="WRONG", id="")
    _error(lambda: _batch_for(mixed), cl3.BrokerReadReason.ACCOUNT_MISMATCH)


def test_v310_cl3_10_pagination_order_duplicates_and_caps() -> None:
    first = _item(id="OP-1", cursor="ITEM-1")
    second = _item(id="OP-2", cursor="ITEM-2", type="OPERATION_TYPE_COUPON")
    transport = _Transport(
        [
            _response([first], True, "PAGE-2"),
            _response([second]),
        ]
    )
    batch = cl3.collect_tbank_operations(
        _request(transport=transport, max_pages=2, max_items=2)
    )
    assert [
        item.observation.source.source_scope_sha256 for item in batch.decisions
    ] == [
        batch.decisions[0].observation.source.source_scope_sha256,
        batch.decisions[1].observation.source.source_scope_sha256,
    ]
    assert transport.calls[0][0]["cursor"] == ""
    assert transport.calls[1][0]["cursor"] == "PAGE-2"
    assert batch.watermark.page_count == 2 and batch.watermark.item_count == 2
    duplicate = _item(id="OP-1", cursor="ITEM-2")
    _error(
        lambda: cl3.collect_tbank_operations(
            _request([_response([first, duplicate])], max_items=2)
        ),
        cl3.BrokerReadReason.DUPLICATE_ITEM,
    )
    _error(
        lambda: cl3.collect_tbank_operations(
            _request([_response([first], True, "PAGE-2")], max_pages=1)
        ),
        cl3.BrokerReadReason.PAGE_LIMIT_EXCEEDED,
    )
    _error(
        lambda: cl3.collect_tbank_operations(
            _request([_response([first, second])], max_items=1, limit=2)
        ),
        cl3.BrokerReadReason.ITEM_LIMIT_EXCEEDED,
    )
    _error(
        lambda: cl3.collect_tbank_operations(_request([_response([], True, "PAGE-2")])),
        cl3.BrokerReadReason.PAGINATION_INVARIANT_VIOLATION,
    )
    _error(
        lambda: cl3.collect_tbank_operations(
            _request([_response([first], False, "PAGE-2")])
        ),
        cl3.BrokerReadReason.PAGINATION_INVARIANT_VIOLATION,
    )


def test_v310_cl3_11_retry_status_and_backoff() -> None:
    for status in _VECTORS["retryable_http_statuses"]:
        transport = _Transport(
            [
                cl3.BrokerTransportFailure(
                    cl3.BrokerTransportFailureKind.HTTP_STATUS, status
                ),
                _KNOWN["response"],
            ]
        )
        waits: list[int] = []
        request = _request(
            transport=transport,
            wait=waits.append,
            clock=_Clock([0, 1, 2]),
            retry_policy=cl3.RetryPolicy(2, 10, (3,)),
        )
        cl3.collect_tbank_operations(request)
        assert waits == [3]
        assert [call[1] for call in transport.calls] == [10, 10]
    for kind, reason in (
        (
            cl3.BrokerTransportFailureKind.TIMEOUT,
            cl3.BrokerReadReason.TRANSPORT_TIMEOUT,
        ),
        (
            cl3.BrokerTransportFailureKind.CONNECTION_INTERRUPTED,
            cl3.BrokerReadReason.TRANSPORT_CONNECTION_INTERRUPTED,
        ),
    ):
        failure = cl3.BrokerTransportFailure(kind)
        _error(
            lambda failure=failure: cl3.collect_tbank_operations(_request([failure])),
            reason,
        )
    permanent = cl3.BrokerTransportFailure(
        cl3.BrokerTransportFailureKind.HTTP_STATUS, 400
    )
    transport = _Transport([permanent, _KNOWN["response"]])
    _error(
        lambda: cl3.collect_tbank_operations(
            _request(transport=transport, retry_policy=cl3.RetryPolicy(2, 10, (0,)))
        ),
        cl3.BrokerReadReason.TRANSPORT_HTTP_PERMANENT,
    )
    assert len(transport.calls) == 1
    _error(
        lambda: cl3.collect_tbank_operations(_request([RuntimeError("secret body")])),
        cl3.BrokerReadReason.TRANSPORT_PROTOCOL_FAILURE,
    )
    exhausted = cl3.BrokerTransportFailure(
        cl3.BrokerTransportFailureKind.HTTP_STATUS, 503
    )
    _error(
        lambda: cl3.collect_tbank_operations(
            _request(
                [exhausted, exhausted],
                retry_policy=cl3.RetryPolicy(2, 10, (0,)),
            )
        ),
        cl3.BrokerReadReason.TRANSPORT_HTTP_RETRY_EXHAUSTED,
    )

    class _MutatingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, payload: dict[str, object], timeout_ns: int) -> object:
            assert payload["operationTypes"] == []
            self.calls += 1
            if self.calls == 1:
                payload["operationTypes"].append("MUTATED")  # type: ignore[union-attr]
                raise cl3.BrokerTransportFailure(cl3.BrokerTransportFailureKind.TIMEOUT)
            return copy.deepcopy(_KNOWN["response"])

    mutating = _MutatingTransport()
    cl3.collect_tbank_operations(
        _request(
            transport=mutating,
            retry_policy=cl3.RetryPolicy(2, 10, (0,)),
        )
    )
    assert mutating.calls == 2


def test_v310_cl3_12_absolute_deadline_clock_and_wait() -> None:
    transport = _Transport([_KNOWN["response"]])
    _error(
        lambda: cl3.collect_tbank_operations(
            _request(transport=transport, clock=_Clock([10]), absolute_deadline_ns=10)
        ),
        cl3.BrokerReadReason.DEADLINE_EXCEEDED,
    )
    assert transport.calls == []
    failure = cl3.BrokerTransportFailure(cl3.BrokerTransportFailureKind.TIMEOUT)
    transport = _Transport([failure, _KNOWN["response"]])
    waits: list[int] = []
    _error(
        lambda: cl3.collect_tbank_operations(
            _request(
                transport=transport,
                clock=_Clock([0, 8]),
                wait=waits.append,
                absolute_deadline_ns=10,
                retry_policy=cl3.RetryPolicy(2, 10, (2,)),
            )
        ),
        cl3.BrokerReadReason.DEADLINE_EXCEEDED,
    )
    assert waits == [] and len(transport.calls) == 1
    _error(
        lambda: cl3.collect_tbank_operations(
            _request(
                clock=_Clock([2, 1]),
                transport=_Transport([failure, _KNOWN["response"]]),
                retry_policy=cl3.RetryPolicy(2, 10, (0,)),
            )
        ),
        cl3.BrokerReadReason.CLOCK_INVALID,
    )
    _error(
        lambda: cl3.collect_tbank_operations(_request(clock=lambda: "0")),
        cl3.BrokerReadReason.CLOCK_INVALID,
    )
    _error(
        lambda: cl3.collect_tbank_operations(
            _request(clock=lambda: (_ for _ in ()).throw(RuntimeError("clock")))
        ),
        cl3.BrokerReadReason.CLOCK_FAILURE,
    )
    _error(
        lambda: cl3.collect_tbank_operations(
            _request(
                transport=_Transport([failure, _KNOWN["response"]]),
                wait=lambda delay: (_ for _ in ()).throw(RuntimeError("wait")),
                retry_policy=cl3.RetryPolicy(2, 10, (0,)),
            )
        ),
        cl3.BrokerReadReason.WAIT_FAILURE,
    )
    first = _item(id="OP-1", cursor="ITEM-1")
    second_page_transport = _Transport(
        [_response([first], True, "PAGE-2"), _response([])]
    )
    _error(
        lambda: cl3.collect_tbank_operations(
            _request(
                transport=second_page_transport,
                clock=_Clock([0, 10]),
                absolute_deadline_ns=10,
                max_pages=2,
            )
        ),
        cl3.BrokerReadReason.DEADLINE_EXCEEDED,
    )
    assert len(second_page_transport.calls) == 1


def test_v310_cl3_13_no_partial_result_after_later_failure() -> None:
    first = _item(id="OP-1", cursor="ITEM-1")
    broken = _item(
        id="OP-2", cursor="ITEM-2", payment={"currency": "USD", "units": "1", "nano": 0}
    )
    transport = _Transport([_response([first], True, "PAGE-2"), _response([broken])])
    _error(
        lambda: cl3.collect_tbank_operations(
            _request(transport=transport, max_pages=2, max_items=2)
        ),
        cl3.BrokerReadReason.MONEY_INVALID,
    )


def test_v310_cl3_14_watermark_canonical_semantics() -> None:
    batch = cl3.collect_tbank_operations(_request())
    canonical = json.loads(batch.watermark.canonical_bytes.decode("ascii"))
    assert set(canonical) == {
        "account_scope_sha256",
        "complete",
        "domain",
        "from_inclusive",
        "item_count",
        "page_chain_sha256",
        "page_count",
        "request_fingerprint_sha256",
        "to_exclusive",
        "version",
    }
    assert canonical["complete"] is True
    assert canonical["item_count"] == "1" and canonical["page_count"] == "1"
    for forbidden in ("final", "current_cash", "reconciled", "economic"):
        assert forbidden not in canonical


def test_v310_cl3_15_privacy_safe_errors_and_outputs() -> None:
    sentinels = (
        "RAW-ACCOUNT-SECRET",
        "RAW-OPERATION-SECRET",
        "RAW-CURSOR-SECRET",
        "PROVIDER-DESCRIPTION-SECRET",
        "https://secret.invalid",
        "TOKEN-SECRET",
        "BODY-SECRET",
    )
    item = _item(
        brokerAccountId=sentinels[0],
        id=sentinels[1],
        cursor=sentinels[2],
        description=sentinels[3],
    )
    request = _request([_response([item])], raw_account_id=sentinels[0])
    batch = cl3.collect_tbank_operations(request)
    rendered = repr(batch) + batch.watermark.canonical_bytes.decode("ascii")
    rendered += b"".join(
        decision.observation.canonical_bytes for decision in batch.decisions
    ).decode("ascii")
    for sentinel in sentinels:
        assert sentinel not in rendered
    error = _error(
        lambda: cl3.collect_tbank_operations(
            _request([RuntimeError(" ".join(sentinels))])
        ),
        cl3.BrokerReadReason.TRANSPORT_PROTOCOL_FAILURE,
    )
    rendered_error = str(error) + repr(error) + repr(dict(error.evidence))
    for sentinel in sentinels:
        assert sentinel not in rendered_error


def test_v310_cl3_16_static_authority_boundary() -> None:
    module_path = _CURRENT / "trading_robot" / "broker_read_adapters.py"
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden_imports = {
        "requests",
        "httpx",
        "os",
        "pathlib",
        "socket",
        "sqlite3",
        "tinkoff",
        "tbank_sandbox",
    }
    assert not any(name.split(".")[0] in forbidden_imports for name in imports)
    lowered = source.lower()
    for forbidden in (
        "getenv(",
        "environ[",
        "open(",
        "urlopen",
        "cashledgerstore",
        "tkinter",
        "subprocess",
    ):
        assert forbidden not in lowered
    assert "getoperationsbycursor" not in lowered


def test_request_payload_and_policy_validation() -> None:
    transport = _Transport([_KNOWN["response"]])
    cl3.collect_tbank_operations(_request(transport=transport))
    assert transport.calls[0][0] == {
        "accountId": "SYNTHETIC-ACCOUNT-01",
        "cursor": "",
        "from": "2026-01-01T00:00:00.000000000Z",
        "limit": 1000,
        "operationTypes": [],
        "state": "OPERATION_STATE_UNSPECIFIED",
        "to": "2026-01-03T00:00:00.000000000Z",
        "withoutCommissions": False,
        "withoutOvernights": False,
        "withoutTrades": False,
    }
    _error(lambda: cl3.RetryPolicy(True, 1, ()), cl3.BrokerReadReason.TYPE_INVALID)
    _error(
        lambda: cl3.RetryPolicy(3, 1, (2, 1)),
        cl3.BrokerReadReason.CONFIGURATION_INVALID,
    )
    _error(lambda: _request(limit=True), cl3.BrokerReadReason.TYPE_INVALID)
    _error(
        lambda: _request(identity_key=b"x"), cl3.BrokerReadReason.CONFIGURATION_INVALID
    )


def test_transport_failure_shape() -> None:
    timeout = cl3.BrokerTransportFailure(cl3.BrokerTransportFailureKind.TIMEOUT)
    status = cl3.BrokerTransportFailure(cl3.BrokerTransportFailureKind.HTTP_STATUS, 503)
    assert str(timeout) == "TIMEOUT" and "503" not in repr(timeout)
    assert (
        str(status) == "HTTP_STATUS:503"
        and repr(status)
        == "BrokerTransportFailure(kind='HTTP_STATUS', http_status=503)"
    )
    _error(
        lambda: cl3.BrokerTransportFailure(cl3.BrokerTransportFailureKind.TIMEOUT, 500),
        cl3.BrokerReadReason.CONFIGURATION_INVALID,
    )
    _error(
        lambda: cl3.BrokerTransportFailure(
            cl3.BrokerTransportFailureKind.HTTP_STATUS, 200
        ),
        cl3.BrokerReadReason.CONFIGURATION_INVALID,
    )


def _assert_shallow_pull_request_custody(
    repository: Path,
    accepted_head: str,
    allowed: set[str],
) -> None:
    assert os.environ.get("GITHUB_EVENT_NAME") == "pull_request"
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    assert event_path is not None
    event = json.loads(Path(event_path).read_text(encoding="utf-8-sig"))
    pull_request = event["pull_request"]
    expected_topology = {
        "agent/v3-10-clean-cl3-contract-freeze": (accepted_head, 4, len(allowed)),
        "program/v3-10-v4-stable-line": (
            _INTEGRATED_CL2_HEAD,
            7,
            len(allowed) + 1,
        ),
    }.get(pull_request["base"]["ref"])
    assert expected_topology is not None
    expected_base, expected_commits, expected_files = expected_topology
    assert pull_request["base"]["sha"] == expected_base
    assert pull_request["base"]["repo"]["full_name"] == (
        "baimleriv/unified-portfolio-system"
    )
    assert pull_request["head"]["ref"] == "agent/v3-10-clean-cl3-implementation"
    assert pull_request["head"]["repo"]["full_name"] == (
        "baimleriv/unified-portfolio-system"
    )
    assert pull_request["commits"] == expected_commits
    assert pull_request["changed_files"] == expected_files
    current_head = subprocess.run(
        ["git", "-c", f"safe.directory={repository}", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    commit_text = subprocess.run(
        ["git", "-c", f"safe.directory={repository}", "cat-file", "-p", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert current_head == os.environ.get("GITHUB_SHA")
    parents = [
        line.removeprefix("parent ")
        for line in commit_text.splitlines()
        if line.startswith("parent ")
    ]
    assert parents == [expected_base, pull_request["head"]["sha"]]
    assert all((repository / path).is_file() for path in allowed)


_ACCEPTED_CL3_CONTRACT_HEAD = "80fe47eba1f7f625f77290fce4a816884ad0ccd2"
_INTEGRATED_CL2_HEAD = "d684186c0628d27ed452ce4f11311155fdf7a44e"
_CL3_IMPLEMENTATION_PATHS = {
    "current/trading_robot/broker_read_adapters.py",
    "current/tests/test_v3_10_broker_read_adapters.py",
    "current/tests/fixtures/v3_10_broker_read_adapters_vectors.json",
}


def test_v310_cl3_17_exact_three_path_delta() -> None:
    repository = _CURRENT.parent
    base_object = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repository}",
            "cat-file",
            "-e",
            f"{_ACCEPTED_CL3_CONTRACT_HEAD}^{{commit}}",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    if base_object.returncode != 0:
        _assert_shallow_pull_request_custody(
            repository,
            _ACCEPTED_CL3_CONTRACT_HEAD,
            _CL3_IMPLEMENTATION_PATHS,
        )
        return
    committed = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repository}",
            "diff",
            "--name-only",
            f"{_ACCEPTED_CL3_CONTRACT_HEAD}..HEAD",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repository}",
            "ls-files",
            "--others",
            "--exclude-standard",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    changed = {path.replace("\\", "/") for path in (*committed, *untracked)}
    assert changed == _CL3_IMPLEMENTATION_PATHS


def test_v310_cl3_18_cl1_cl2_sources_unchanged() -> None:
    repository = _CURRENT.parent
    base_object = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repository}",
            "cat-file",
            "-e",
            f"{_ACCEPTED_CL3_CONTRACT_HEAD}^{{commit}}",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    if base_object.returncode != 0:
        _assert_shallow_pull_request_custody(
            repository,
            _ACCEPTED_CL3_CONTRACT_HEAD,
            _CL3_IMPLEMENTATION_PATHS,
        )
        return
    protected = (
        "current/trading_robot/cash_ledger_domain.py",
        "current/trading_robot/cash_ledger_persistence.py",
        "current/tests/test_v3_10_cash_ledger_domain.py",
        "current/tests/test_v3_10_cash_ledger_persistence.py",
        "current/tests/fixtures/v3_10_cash_ledger_domain_vectors.json",
        "current/tests/fixtures/v3_10_cash_ledger_persistence_vectors.json",
    )
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repository}",
            "diff",
            "--exit-code",
            _ACCEPTED_CL3_CONTRACT_HEAD,
            "--",
            *protected,
        ],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0


if __name__ == "__main__":
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"{len(tests)} passed")
