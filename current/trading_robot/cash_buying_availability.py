"""CL5 own buying budget, distinct from accounting cash and withdrawal limits.

V3 is a conservative common own-money ceiling for 2-3 configured RUB SHARE/ETF
instruments. It never authorizes an order. The request-bound locked CL7 own-funds
check remains mandatory in the desktop composition. No runtime migration here.
"""
from __future__ import annotations

from dataclasses import dataclass as _dataclass, fields as _fields, replace as _replace
from collections.abc import Mapping, Callable
from types import MappingProxyType
from typing import Any
import hmac

from . import cash_availability as _cl5
from . import cash_ledger_opening_reconciliation as _cl4
from . import cash_ledger_domain as _ledger
from .broker_read_adapters import BrokerEnvironment
from .cash_ledger_domain import Money
from .portfolio_risk_adapter import PortfolioRiskInstrumentMetadata as _Metadata
from .cash_availability import (
    CL5Error, CL5Reason, AvailabilityStatus, AvailabilityReason, OverlapDisposition,
    _fail, _checked_money, _money_dict, _timestamp_ns, _require_hash,
    _require_environment, _canonical_bytes, _sha256, _hmac_sha256, _require_key,
    _require_key_id, _account_scope, _INT64_MAX,
)

MAX_AGE_NS = 5_000_000_000
_MAX_NANO = 10**21
_RPC = "tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxMaxLots"


def _require(condition: bool, reason: CL5Reason = CL5Reason.PROOF_IDENTITY_INVALID) -> None:
    if not condition:
        _fail(reason)


def _quotation(raw: object) -> int:
    _require(type(raw) is dict and set(raw) <= {"units", "nano", "currency"}, CL5Reason.MONEY_INVALID)
    _require("currency" not in raw or (type(raw["currency"]) is str and raw["currency"] in {"rub", "RUB"}), CL5Reason.CURRENCY_UNSUPPORTED)
    # Proto JSON may omit zero scalars inside a present Quotation, not the
    # buyMoneyAmount object itself. Reuse the strict signed wire Money decoder.
    try:
        money = _cl5._broker.money_value_to_money({
            "currency": "RUB", "units": raw.get("units", "0"), "nano": raw.get("nano", 0),
        })
    except Exception:
        money = None
    if money is None:
        _fail(CL5Reason.MONEY_INVALID)
    _require(0 <= money.minor_units <= _MAX_NANO, CL5Reason.MONEY_INVALID)
    return money.minor_units


@_dataclass(frozen=True, slots=True)
class OwnBuyingCashProof:
    account_scope_sha256: str
    environment: BrokerEnvironment
    buying_scope_sha256: str
    request_sha256: tuple[str, ...]
    response_sha256: tuple[str, ...]
    own_money_nano: tuple[int, ...]
    as_of: str
    completed_at: str
    identity_key_id: str
    proof_identity_sha256: str
    response_complete: bool = True
    version: int = 3

    def __post_init__(self) -> None:
        _require(type(self.version) is int and self.version == 3, CL5Reason.VERSION_UNSUPPORTED)
        _require_environment(self.environment)
        _require_key_id(self.identity_key_id)
        for value in (self.account_scope_sha256, self.buying_scope_sha256, self.proof_identity_sha256):
            _require_hash(value, CL5Reason.PROOF_IDENTITY_INVALID)
        _require(type(self.own_money_nano) is tuple and 2 <= len(self.own_money_nano) <= 3)
        _require(all(type(n) is int and 0 <= n <= _MAX_NANO for n in self.own_money_nano))
        for hashes in (self.request_sha256, self.response_sha256):
            _require(type(hashes) is tuple and len(hashes) == len(self.own_money_nano))
            for value in hashes:
                _require_hash(value, CL5Reason.PROOF_IDENTITY_INVALID)
        _require(len(set(self.request_sha256)) == len(self.request_sha256))
        _require(0 <= _timestamp_ns(self.completed_at) - _timestamp_ns(self.as_of) <= MAX_AGE_NS,
                 CL5Reason.PROOF_STALE)
        _require(self.response_complete is True, CL5Reason.PROOF_INCOMPLETE)

    @property
    def available_rub(self) -> Money:
        return _cl5._money_from_minor_units(min(self.own_money_nano))

    def _identity_dict(self) -> dict[str, object]:
        return {
            "domain": "v3.10-cl5-own-buying-cash-proof-identity", "version": 3,
            "account_scope_sha256": self.account_scope_sha256,
            "environment": self.environment.value, "buying_scope_sha256": self.buying_scope_sha256,
            "request_sha256": list(self.request_sha256), "response_sha256": list(self.response_sha256),
            "own_money_nano": [str(n) for n in self.own_money_nano],
            "as_of": self.as_of, "completed_at": self.completed_at,
            "identity_key_id": self.identity_key_id, "response_complete": self.response_complete,
            "source": "buyLimits.buyMoneyAmount", "rpc": _RPC,
            "aggregation": "MIN_CONFIGURED_OWN_MONEY", "price": None,
        }

    def to_canonical_dict(self) -> dict[str, object]:
        value = self._identity_dict()
        value["domain"] = "v3.10-cl5-own-buying-cash-proof"
        value["proof_identity_sha256"] = self.proof_identity_sha256
        return value

    @property
    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_canonical_dict())

    @property
    def sha256(self) -> str:
        return _sha256(self.canonical_bytes)


def validate_own_buying_proof(proof: OwnBuyingCashProof, identity_key: bytes,
                              *, buying_scope_sha256: str | None = None) -> OwnBuyingCashProof:
    try:
        _require(type(proof) is OwnBuyingCashProof, CL5Reason.TYPE_INVALID)
        checked = OwnBuyingCashProof(**{f.name: object.__getattribute__(proof, f.name) for f in _fields(proof)})
        key = _require_key(identity_key)
        expected = _hmac_sha256(key, checked._identity_dict())
        _require(hmac.compare_digest(expected, checked.proof_identity_sha256))
        _require(checked.canonical_bytes == proof.canonical_bytes)
        if buying_scope_sha256 is not None:
            _require_hash(buying_scope_sha256, CL5Reason.PROOF_IDENTITY_INVALID)
            _require(checked.buying_scope_sha256 == buying_scope_sha256)
        return checked
    except CL5Error:
        raise
    except Exception:
        _fail(CL5Reason.PROOF_IDENTITY_INVALID)


@_dataclass(frozen=True)
class OwnBuyingBudgetPolicy:
    account_id: str
    instruments: Mapping[str, Any]
    binding_guard: Callable[[], None]
    execution_order_type: str = "BESTPRICE"

    def __post_init__(self) -> None:
        _require(type(self.account_id) is str and 0 < len(self.account_id) <= 128
                 and self.account_id == self.account_id.strip(), CL5Reason.ACCOUNT_SCOPE_INVALID)
        _require(isinstance(self.instruments, Mapping) and 2 <= len(self.instruments) <= 3
                 and callable(self.binding_guard), CL5Reason.TYPE_INVALID)
        _require(type(self.execution_order_type) is str
                 and self.execution_order_type in {"MARKET", "BESTPRICE"}, CL5Reason.TYPE_INVALID)
        copied = dict(self.instruments)
        for uid, row in copied.items():
            _require(type(uid) is str and 0 < len(uid) <= 128 and uid == uid.strip()
                     and type(row) is _Metadata and row.instrument_id == uid
                     and type(row.currency) is str and row.currency in {"rub", "RUB"}
                     and type(row.asset_class) is str and row.asset_class.lower() in {"share", "etf"}
                     and type(row.lot_size) is int and 0 < row.lot_size <= _INT64_MAX,
                     CL5Reason.RESPONSE_SCHEMA_INVALID)
        object.__setattr__(self, "instruments", MappingProxyType(copied))

    @property
    def scope_sha256(self) -> str:
        # Includes exact configured identities, metadata and explicit order choice;
        # only hashes leave this private local acquisition object.
        return _sha256(_canonical_bytes({
            "domain": "v3.10-cl5-own-buying-config-scope-v3", "account_id": self.account_id,
            "execution_order_type": self.execution_order_type, "price": None,
            "source": "buyLimits.buyMoneyAmount", "aggregation": "MIN_CONFIGURED_OWN_MONEY",
            "instruments": [{"instrument_id": uid, "lot_size": row.lot_size,
                             "currency": row.currency, "asset_class": row.asset_class}
                            for uid, row in sorted(self.instruments.items())],
        }))

    def acquire(self, provider: Any, *, account_scope_sha256: str,
                identity_key: bytes, identity_key_id: str,
                clock: Callable[[], str], monotonic_ns: Callable[[], int]) -> OwnBuyingCashProof:
        _require(callable(clock) and callable(monotonic_ns), CL5Reason.TYPE_INVALID)
        key = _require_key(identity_key); key_id = _require_key_id(identity_key_id)
        _require(_account_scope(self.account_id, BrokerEnvironment.SANDBOX, key_id, key)
                 == account_scope_sha256, CL5Reason.ACCOUNT_SCOPE_INVALID)
        self.binding_guard()
        scope = self.scope_sha256
        start_time = clock(); start = monotonic_ns()
        _require(type(start) is int and start >= 0, CL5Reason.TIMESTAMP_INVALID)
        _timestamp_ns(start_time)

        def checkpoint() -> None:
            tick = monotonic_ns(); now = clock()
            _require(type(tick) is int and 0 <= tick - start <= MAX_AGE_NS, CL5Reason.PROOF_STALE)
            _require(0 <= _timestamp_ns(now) - _timestamp_ns(start_time) <= MAX_AGE_NS,
                     CL5Reason.PROOF_STALE)
            self.binding_guard()
            _require(self.scope_sha256 == scope, CL5Reason.PROOF_IDENTITY_INVALID)

        requests, responses, amounts = [], [], []
        for uid in sorted(self.instruments):
            checkpoint()
            failed = False
            try:
                raw = provider.get_max_lots(self.account_id, uid, price=None)
            except Exception:
                failed = True
            if failed:
                # Raise outside the handler: private provider exception text is
                # not retained as the new exception's implicit context.
                _fail(CL5Reason.PROOF_INCOMPLETE)
            checkpoint()
            _require(type(raw) is dict, CL5Reason.RESPONSE_SCHEMA_INVALID)
            checked, raw_bytes = _cl5._bounded_response(raw)
            for name, expected in (("accountId", self.account_id), ("instrumentId", uid), ("instrumentUid", uid)):
                _require(name not in checked or checked[name] == expected, CL5Reason.ACCOUNT_SCOPE_INVALID)
            _require(type(checked.get("currency")) is str and checked["currency"] in {"rub", "RUB"}, CL5Reason.CURRENCY_UNSUPPORTED)
            _require(checked.get("limitsLoadingInProgress", False) is False,
                     CL5Reason.POSITIONS_LOADING_IN_PROGRESS)
            own = checked.get("buyLimits")
            _require(type(own) is dict and "buyMoneyAmount" in own, CL5Reason.PROOF_INCOMPLETE)
            amounts.append(_quotation(own["buyMoneyAmount"]))
            requests.append(_sha256(_canonical_bytes({"account_scope_sha256": account_scope_sha256,
                "instrument_id": uid, "price": None, "rpc": _RPC, "buying_scope_sha256": scope})))
            responses.append(_sha256(raw_bytes))
        checkpoint()
        placeholder = OwnBuyingCashProof(account_scope_sha256, BrokerEnvironment.SANDBOX, scope,
            tuple(requests), tuple(responses), tuple(amounts), start_time, clock(), key_id, "0"*64)
        return _replace(placeholder, proof_identity_sha256=_hmac_sha256(key, placeholder._identity_dict()))


@_dataclass(frozen=True, slots=True)
class OwnCashAvailabilitySnapshot:
    account_scope_sha256: str
    environment: BrokerEnvironment
    currency: str
    evaluated_at: str
    cl4_reconciliation_evaluated_at: str
    broker_cash_as_of: str
    broker_own_buying_as_of: str
    central_projection_evaluated_at: str
    reconciliation_sha256: str
    cl4_adoption_candidate_sha256: str
    ledger_export_sha256: str
    ledger_revision: int
    ledger_head_sha256: str
    broker_own_buying_cash_proof_sha256: str
    buying_scope_sha256: str
    broker_total_cash: Money
    broker_own_buying_cash_lower_bound: Money
    central_reservation_projection_sha256: str
    central_order_revision: int
    central_reservation_projection_hash: str
    central_queued_reserved_cash: Money
    central_ambiguous_reserved_cash: Money
    central_total_reserved_cash: Money
    central_ambiguous_count: int
    overlap_disposition: OverlapDisposition
    status: AvailabilityStatus
    availability_reason: AvailabilityReason
    free_investable_cash: Money | None
    version: int = 3

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != 3
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
            self.broker_own_buying_as_of,
            self.central_projection_evaluated_at,
        ):
            _timestamp_ns(timestamp)
        for value in (
            self.reconciliation_sha256,
            self.cl4_adoption_candidate_sha256,
            self.ledger_export_sha256,
            self.ledger_head_sha256,
            self.broker_own_buying_cash_proof_sha256,
            self.buying_scope_sha256,
            self.central_reservation_projection_sha256,
            self.central_reservation_projection_hash,
        ):
            _require_hash(value, CL5Reason.CANONICAL_FORMAT_INVALID)
        for revision in (self.ledger_revision, self.central_order_revision):
            if type(revision) is not int or not 0 <= revision <= _INT64_MAX:
                _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        broker_total = _checked_money(self.broker_total_cash)
        lower_bound = _checked_money(self.broker_own_buying_cash_lower_bound)
        queued = _checked_money(self.central_queued_reserved_cash)
        ambiguous = _checked_money(self.central_ambiguous_reserved_cash)
        total = _checked_money(self.central_total_reserved_cash)
        if (
            min(
                broker_total.minor_units,
                lower_bound.minor_units,
                queued.minor_units,
                ambiguous.minor_units,
            )
            < 0
        ):
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        if lower_bound.minor_units > broker_total.minor_units:
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        if queued.minor_units + ambiguous.minor_units != total.minor_units:
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        free = (
            None
            if self.free_investable_cash is None
            else _checked_money(self.free_investable_cash)
        )
        if type(self.overlap_disposition) is not OverlapDisposition:
            _fail(CL5Reason.TYPE_INVALID)
        if type(self.status) is not AvailabilityStatus:
            _fail(CL5Reason.TYPE_INVALID)
        if type(self.availability_reason) is not AvailabilityReason:
            _fail(CL5Reason.TYPE_INVALID)
        _require(type(self.central_ambiguous_count) is int
                 and 0 <= self.central_ambiguous_count <= _INT64_MAX)
        _require(self.central_ambiguous_count > 0 or ambiguous.minor_units == 0,
                 CL5Reason.CANONICAL_FORMAT_INVALID)
        expected_overlap = (
            OverlapDisposition.NO_LOCAL_RESERVATION
            if total.minor_units == 0 and self.central_ambiguous_count == 0
            else (
                OverlapDisposition.AMBIGUOUS_PROVIDER_OVERLAP
                if self.central_ambiguous_count > 0
                else OverlapDisposition.QUEUED_DISJOINT
            )
        )
        if self.overlap_disposition is not expected_overlap:
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        if self.status is AvailabilityStatus.READY:
            if (
                self.availability_reason is not AvailabilityReason.READY
                or free is None
                or self.central_ambiguous_count != 0
                or free.minor_units != lower_bound.minor_units - queued.minor_units
                or free.minor_units < 0
            ):
                _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        elif self.status is AvailabilityStatus.MANUAL_REVIEW_REQUIRED:
            if (
                self.availability_reason
                is not AvailabilityReason.CENTRAL_PROVIDER_OVERLAP_UNKNOWN
                or self.central_ambiguous_count <= 0
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
                AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS,
            }
            or free is not None
        ):
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        if (
            self.availability_reason
            is AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS
            and (
                self.central_ambiguous_count != 0
                or lower_bound.minor_units - queued.minor_units >= 0
            )
        ):
            _fail(CL5Reason.CANONICAL_FORMAT_INVALID)
        object.__setattr__(self, "broker_total_cash", broker_total)
        object.__setattr__(self, "broker_own_buying_cash_lower_bound", lower_bound)
        object.__setattr__(self, "central_queued_reserved_cash", queued)
        object.__setattr__(self, "central_ambiguous_reserved_cash", ambiguous)
        object.__setattr__(self, "central_total_reserved_cash", total)
        object.__setattr__(self, "free_investable_cash", free)

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "account_scope_sha256": self.account_scope_sha256,
            "buying_scope_sha256": self.buying_scope_sha256,
            "availability_reason": self.availability_reason.value,
            "central_ambiguous_count": self.central_ambiguous_count,
            "broker_cash_as_of": self.broker_cash_as_of,
            "broker_own_buying_as_of": self.broker_own_buying_as_of,
            "broker_own_buying_cash_proof_sha256": (
                self.broker_own_buying_cash_proof_sha256
            ),
            "broker_own_buying_cash_lower_bound": _money_dict(
                self.broker_own_buying_cash_lower_bound
            ),
            "broker_total_cash": _money_dict(self.broker_total_cash),
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
            "domain": "v3.10-cl5-own-buying-availability",
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



def build_own_cash_availability(
    ledger_export_bytes: bytes, reconciliation: _cl4.CashReconciliation,
    buying: OwnBuyingCashProof, reservations: _cl5.CentralReservationProjection,
    *, evaluated_at: str, identity_key: bytes,
) -> OwnCashAvailabilitySnapshot:
    """Pure V3 computation; a smaller own budget is not a ledger discrepancy.

    No withdrawal observation is accepted. Accounting and buying proof retain
    separate identities. All queued reservations (including the own intent) are
    subtracted once; any possible provider overlap remains manual-review-only.
    """
    _require(type(ledger_export_bytes) is bytes
             and type(reconciliation) is _cl4.CashReconciliation
             and reconciliation.version == 3 and reconciliation.proof.version == 3,
             CL5Reason.CL4_EVIDENCE_INVALID)
    key = _require_key(identity_key)
    try:
        adoption = _cl4.build_adoption_candidate(reconciliation,
            ledger_export_bytes=ledger_export_bytes, identity_key=key)
    except _cl4.CL4Error as error:
        _fail(CL5Reason.CL4_EVIDENCE_INVALID, error.reason)
    proof = validate_own_buying_proof(buying, key)
    projection = _cl5._validated_projection(reservations, key)
    _require(proof.account_scope_sha256 == reconciliation.proof.account_scope_sha256
             == projection.account_scope_sha256, CL5Reason.ACCOUNT_SCOPE_INVALID)
    _require(proof.environment is reconciliation.proof.environment is projection.environment,
             CL5Reason.ENVIRONMENT_UNSUPPORTED)
    _require(proof.identity_key_id == reconciliation.proof.identity_key_id == projection.identity_key_id,
             CL5Reason.PROOF_IDENTITY_INVALID)
    now = _timestamp_ns(evaluated_at)
    times = tuple(_timestamp_ns(t) for t in (reconciliation.evaluated_at,
        reconciliation.proof.as_of, proof.as_of, proof.completed_at, projection.evaluated_at))
    _require(not any(t > now for t in times), CL5Reason.DEPENDENCY_FROM_FUTURE)
    accounting = _checked_money(reconciliation.broker_cash)
    lower = _cl5._money_from_minor_units(min(accounting.minor_units, proof.available_rub.minor_units))
    overlap = (OverlapDisposition.AMBIGUOUS_PROVIDER_OVERLAP if projection.ambiguous_count
               else OverlapDisposition.QUEUED_DISJOINT if projection.total_reserved_cash.minor_units
               else OverlapDisposition.NO_LOCAL_RESERVATION)
    free_nano = lower.minor_units - projection.queued_reserved_cash.minor_units
    if adoption.disposition is not _cl4.AdoptionDisposition.SEPARATE_LOCKED_REVIEW_REQUIRED:
        status, reason = AvailabilityStatus.BLOCKED, AvailabilityReason.CL4_NOT_READY
    elif now - times[1] > MAX_AGE_NS or now - times[2] > MAX_AGE_NS:
        status, reason = AvailabilityStatus.BLOCKED, AvailabilityReason.BROKER_PROOF_STALE
    elif now - times[4] > MAX_AGE_NS:
        status, reason = AvailabilityStatus.BLOCKED, AvailabilityReason.CENTRAL_PROJECTION_STALE
    elif max(times) - min(times) > MAX_AGE_NS:
        status, reason = AvailabilityStatus.BLOCKED, AvailabilityReason.MIXED_EVIDENCE_SNAPSHOT
    elif any(amount > accounting.minor_units for amount in proof.own_money_nano):
        status, reason = AvailabilityStatus.BLOCKED, AvailabilityReason.BROKER_VIEW_MISMATCH
    elif projection.ambiguous_count:
        status, reason = AvailabilityStatus.MANUAL_REVIEW_REQUIRED, AvailabilityReason.CENTRAL_PROVIDER_OVERLAP_UNKNOWN
    elif free_nano < 0:
        status, reason = AvailabilityStatus.BLOCKED, AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS
    else:
        status, reason = AvailabilityStatus.READY, AvailabilityReason.READY
    free = _cl5._money_from_minor_units(free_nano) if status is AvailabilityStatus.READY else None
    return OwnCashAvailabilitySnapshot(
        account_scope_sha256=proof.account_scope_sha256, environment=proof.environment, currency="RUB",
        evaluated_at=evaluated_at, cl4_reconciliation_evaluated_at=reconciliation.evaluated_at,
        broker_cash_as_of=reconciliation.proof.as_of, broker_own_buying_as_of=proof.as_of,
        central_projection_evaluated_at=projection.evaluated_at,
        reconciliation_sha256=reconciliation.sha256, cl4_adoption_candidate_sha256=adoption.sha256,
        ledger_export_sha256=reconciliation.projection.ledger_export_sha256,
        ledger_revision=reconciliation.projection.ledger_revision,
        ledger_head_sha256=reconciliation.projection.ledger_head_sha256,
        broker_own_buying_cash_proof_sha256=proof.sha256, buying_scope_sha256=proof.buying_scope_sha256,
        broker_total_cash=accounting, broker_own_buying_cash_lower_bound=lower,
        central_reservation_projection_sha256=projection.sha256,
        central_order_revision=projection.central_order_revision,
        central_reservation_projection_hash=projection.central_reservation_projection_hash,
        central_queued_reserved_cash=projection.queued_reserved_cash,
        central_ambiguous_reserved_cash=projection.ambiguous_reserved_cash,
        central_total_reserved_cash=projection.total_reserved_cash,
        central_ambiguous_count=projection.ambiguous_count,
        overlap_disposition=overlap, status=status, availability_reason=reason, free_investable_cash=free,
    )
