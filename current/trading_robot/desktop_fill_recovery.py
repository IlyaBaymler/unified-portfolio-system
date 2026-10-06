"""Bounded desktop order recovery through existing canonical/Central owners.

No POST, cancellation, queue replacement or price fallback is available here.
Only one persisted SUBMITTED/UNCERTAIN intent, RUB SHARE/ETF, LEGACY_ACTIVE.
Active partials retain the account blocker and full local reservation. Only bound
terminal receipts may release it, with actual executed lots (possibly zero).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import time
from typing import Any

from .central_order_manager import CentralOrderIntent, CentralOrderManager
from .locking import InterProcessFileLock
from .desktop_partial_progress import PartialProgressStore
from .portfolio_adapters import BrokerPortfolioAdapter, RuntimePortfolioAdapter, RuntimePositionRecord
from .portfolio_cash_observation import DesktopOwnCashPolicy
from .portfolio_manager import CanonicalPortfolioManager
from .portfolio_model import PortfolioState, PortfolioTarget, PositionOrigin, PositionOwnership, SnapshotFreshness
from .portfolio_observation import PortfolioObservationPolicy, _integer, _quotation
from .portfolio_reconciler import ReconciliationContext
from .runtime_cash_authority import RuntimeCashAuthorityState
from .risk import MOSCOW_TZ


class DesktopFillRecoveryError(RuntimeError):
    """Finite error codes only; no exception text, provider payload or raw IDs."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise DesktopFillRecoveryError("DESKTOP_FILL_RECOVERY_" + code)


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _utc(value: Any) -> datetime:
    _require(type(value) is str and len(value) <= 40, "TIME_INVALID")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(result.tzinfo is not None and result.utcoffset() is not None, "TIME_INVALID")
        return result.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise DesktopFillRecoveryError("DESKTOP_FILL_RECOVERY_TIME_INVALID") from None


def _id(value: Any) -> str:
    _require(type(value) is str and 0 < len(value) <= 128
             and value.strip() == value and all(ord(c) >= 32 for c in value), "ID_INVALID")
    return value


@dataclass(frozen=True)
class _FullFill:
    order: dict[str, Any]
    price: Decimal | None
    sha256: str
    last_trade_at: datetime | None
    executed_lots: int
    terminal: bool
    outcome: str | None
    stages: tuple[tuple[str, int, str, str], ...]


def _receipt(raw: Any, intent: CentralOrderIntent, now: datetime,
             policy: PortfolioObservationPolicy) -> _FullFill | None:
    """Use GetOrderState stages: quantities are lots; prices are per security.

    Trade-stream quantities have different units and are not accepted here.
    executedOrderPrice is NOT used: its endpoint-dependent meaning differs.
    """
    _require(type(raw) is dict, "RECEIPT_INVALID")
    _require(len(json.dumps(raw, allow_nan=False).encode()) <= 262_144, "RECEIPT_TOO_LARGE")
    order = policy._order(raw)
    c = intent.candidate
    _require(order.get("orderRequestId") == intent.intent_id
             and order["instrumentUid"] == c.instrument_id
             and order["direction"] == "ORDER_DIRECTION_" + c.direction
             and _integer(order["lotsRequested"]) == c.requested_lots, "RECEIPT_BINDING_MISMATCH")
    exchange = _id(order.get("orderId"))
    _require(intent.broker_order_id is None or exchange == intent.broker_order_id,
             "RECEIPT_BINDING_MISMATCH")
    _require(order.get("currency") == "rub", "CURRENCY_INVALID")
    if "orderType" in order:
        _require(order["orderType"] == "ORDER_TYPE_" + c.order_type, "ORDER_TYPE_MISMATCH")
    status = order["executionReportStatus"].removeprefix("EXECUTION_REPORT_STATUS_")
    executed = _integer(order["lotsExecuted"])
    if status == "NEW":
        return None
    terminal = status in {"FILL", "CANCELLED", "REJECTED"}
    _require(status != "CANCELLED" or executed < c.requested_lots, "TERMINAL_QUANTITY_CONFLICT")
    stages = order.get("stages", [])
    if executed == 0:
        _require(status in {"CANCELLED", "REJECTED"}, "TERMINAL_QUANTITY_CONFLICT")
        _require(type(stages) is list and not stages, "ZERO_FILL_HAS_TRADES")
        attempts = [t for t in intent.transitions if t.status == "IN_FLIGHT"]
        _require(len(attempts) == 1 and _utc(attempts[0].at) <= now, "ATTEMPT_LINEAGE_INVALID")
        fingerprint = _hash({"account": c.account_id, "intent": intent.to_dict(),
            "exchange": exchange, "status": status, "lots": 0, "stages": []})
        return _FullFill(order, None, fingerprint, None, 0, True, status, ())
    if status != "FILL" and (stages is None or stages == []):
        return None  # Incomplete cumulative evidence cannot close a partial order.
    _require(type(stages) is list and 0 < len(stages) <= 128, "STAGES_REQUIRED")
    attempts = [t for t in intent.transitions if t.status == "IN_FLIGHT"]
    _require(len(attempts) == 1, "ATTEMPT_LINEAGE_INVALID")
    earliest = _utc(attempts[0].at)
    seen: set[str] = set()
    total = 0
    weighted = Decimal(0)
    normalized = []
    times = []
    for stage in stages:
        _require(type(stage) is dict, "STAGE_INVALID")
        trade = _id(stage.get("tradeId"))
        _require(trade not in seen, "DUPLICATE_TRADE")
        seen.add(trade)
        lots = _integer(stage.get("quantity"))
        _require(0 < lots <= c.requested_lots, "STAGE_QUANTITY_INVALID")
        money = stage.get("price")
        _require(type(money) is dict and money.get("currency") == "rub", "PRICE_INVALID")
        price = _quotation(money)
        _require(0 < price <= Decimal("1000000000000"), "PRICE_INVALID")
        at = _utc(stage.get("executionTime"))
        _require(earliest <= at <= now, "TRADE_TIME_INVALID")
        # Existing accounting dates executions by canonical snapshot. Until a
        # separate historical-period policy exists, do not misdate old fills.
        _require(at.astimezone(MOSCOW_TZ).date() == now.astimezone(MOSCOW_TZ).date(),
                 "ACCOUNTING_PERIOD_OUT_OF_SCOPE")
        total += lots
        weighted += price * lots
        times.append(at)
        normalized.append((trade, lots, str(price.normalize()), at.isoformat()))
    _require(total == executed, "STAGE_COVERAGE_MISMATCH")
    price = weighted / Decimal(total)
    if "averagePositionPrice" in order:
        average = order["averagePositionPrice"]
        _require(type(average) is dict and average.get("currency") == "rub", "PRICE_INVALID")
        _require(abs(_quotation(average) - price) <= Decimal("0.000000001"), "PRICE_MISMATCH")
    fingerprint = _hash({"account": c.account_id, "intent": intent.to_dict(),
        "exchange": exchange, "lots": total, "price": str(price), "stages": sorted(normalized)})
    if status != "FILL":
        fingerprint = _hash({"receipt": fingerprint, "status": status})
    outcome = ("FILLED" if status == "FILL" else
               "PARTIALLY_FILLED" if terminal else None)
    return _FullFill(order, price, fingerprint, max(times), executed,
                     terminal, outcome, tuple(sorted(normalized)))


class _ReceiptReadView:
    """Supplement active observations with one actually read bound full receipt.

    No synthetic pending ID is inferred from GetOrders absence. The receipt is
    queried using Central's exact request ID even when the broker cache is empty.
    Other active/cached orders remain visible to the existing strict policy.
    """
    def __init__(self, api: Any, fill: _FullFill) -> None:
        self.api, self.fill = api, fill

    def get_portfolio(self, account: str) -> Any:
        return self.api.get_portfolio(account)

    def get_orders(self, account: str) -> Any:
        orders = self.api.get_orders(account)
        _require(type(orders) is list, "ORDERS_INVALID")
        found = False
        for raw in orders:
            _require(type(raw) is dict, "ORDERS_INVALID")
            same_request = raw.get("orderRequestId") == self.fill.order["orderRequestId"]
            same_exchange = raw.get("orderId") == self.fill.order["orderId"]
            if same_request or same_exchange:
                _require(not found and same_request and same_exchange, "ORDERS_CONFLICT")
                for key in ("instrumentUid", "direction", "executionReportStatus"):
                    _require(raw.get(key) == self.fill.order[key], "ORDERS_CONFLICT")
                for key in ("lotsRequested", "lotsExecuted"):
                    _require(_integer(raw.get(key)) == _integer(self.fill.order[key]), "ORDERS_CONFLICT")
                found = True
        return deepcopy(orders if found else [*orders, self.fill.order])

    def get_order_state(self, account: str, key: str, *, by_request_id: bool = False) -> Any:
        # The supplemented receipt makes another lookup for our own ID needless.
        _require(key not in {self.fill.order["orderId"], self.fill.order["orderRequestId"]},
                 "UNEXPECTED_REPEAT_READ")
        return self.api.get_order_state(account, key, by_request_id=by_request_id)


class DesktopFillRecovery:
    """One synchronous reconciliation tick, not a background execution worker.

    The scheduler invokes this callback. Raw refresh remains separately callable.
    Canonical publication and Risk/Central accounting are ordered, not a distributed
    transaction. Central stays blocking until its existing final reconciliation.
    """
    def __init__(self, *, manager: CanonicalPortfolioManager, central: CentralOrderManager,
                 risk: Any, authority: Any, observation_policy: PortfolioObservationPolicy,
                 cash_policy: DesktopOwnCashPolicy, profiles: Any, runtimes: Any,
                 refresh: Callable[[], PortfolioState], clock: Callable[[], datetime]) -> None:
        self.manager, self.central, self.risk, self.authority = manager, central, risk, authority
        self.policy, self.cash_policy = observation_policy, cash_policy
        self.profiles, self.runtimes = profiles, runtimes
        self.refresh, self.clock = refresh, clock
        self.lock_path = manager.repository.path.with_name("desktop_fill_recovery.lock")
        self.last_status = "NOT_RUN"
        self.progress = PartialProgressStore(manager.repository.path)

    def __call__(self) -> PortfolioState:
        try:
            with InterProcessFileLock(self.lock_path, timeout_seconds=0.1):
                result = self._run()
            return result
        except DesktopFillRecoveryError:
            self.last_status = "RECOVERY_BLOCKED"
            raise
        except Exception:
            self.last_status = "RECOVERY_BLOCKED"
            raise DesktopFillRecoveryError("DESKTOP_FILL_RECOVERY_DEPENDENCY_FAILED") from None

    def _run(self) -> PortfolioState:
        central_before = self.central.state()
        intent = central_before.blocking_intent
        if intent is None:
            result = self.refresh()
            self.last_status = "NO_PENDING_INTENT"
            return result
        _require(intent.status in {"SUBMITTED", "UNCERTAIN"}, "ATTEMPT_NOT_TERMINAL")
        authority = self.authority.store.load()
        _require(authority.state is RuntimeCashAuthorityState.LEGACY_ACTIVE
                 and intent.cl7_locked_dispatch_proof is None, "EXACT_CASH_OUT_OF_SCOPE")
        _require(not central_before.queued, "OTHER_QUEUED_INTENT")
        c = intent.candidate
        _require(self.manager.account_id == self.central.account_id == self.risk.account_id
                 == self.policy.account_id == self.cash_policy.account_id == c.account_id
                 and self.risk.mode == "SANDBOX_EXECUTION", "ACCOUNT_SCOPE_MISMATCH")
        metadata = self.policy.instruments.get(c.instrument_id)
        _require(metadata is not None and metadata.currency == "RUB"
                 and metadata.asset_class.upper() in {"SHARE", "ETF"}
                 and metadata.lot_size == c.lot_size, "METADATA_MISMATCH")
        profiles_before = self.profiles.load_mode("SANDBOX_EXECUTION")
        runtimes_before = self.runtimes.load(expected_account_id=c.account_id)
        _require(bool(runtimes_before) and all(r.status == "ACTIVE" for r in runtimes_before),
                 "CONFIGURED_SET_NOT_ACTIVE")
        profiles = [p for p in profiles_before if p.instrument_id == c.instrument_id]
        runtimes = [r for r in runtimes_before if r.runtime_key == c.runtime_key]
        _require(len(profiles) == len(runtimes) == 1, "RUNTIME_MISMATCH")
        profile, runtime = profiles[0], runtimes[0]
        _require(profile.strategy_profile_hash == c.strategy_profile_hash
                 and runtime.config.runtime_config_hash == c.runtime_config_hash
                 and runtime.config.to_dict() == profile.to_runtime_config(c.account_id).to_dict()
                 and c.strategy_id == runtime.config.strategy_id
                 and c.candle_interval == runtime.config.candle_interval, "RUNTIME_MISMATCH")
        previous = self.manager.repository.load(expected_account_id=c.account_id)
        _require(previous.portfolio_source == "CANONICAL" and previous.migration.complete
                 and not previous.migration.legacy_read_path_enabled, "CANONICAL_INVALID")
        started = time.monotonic()
        risk_before = self.risk.state_store.load_account(c.account_id)

        def guard(stage: str = "") -> None:
            del stage
            _require(0 <= time.monotonic() - started <= 30, "READ_BUDGET_EXPIRED")
            if self.policy.binding_guard is not None:
                self.policy.binding_guard()
            _require(self.progress.load(intent_hash) == progress_expected, "PARTIAL_PROGRESS_CHANGED")
            _require(self.central.state() == central_before, "CENTRAL_CHANGED")
            _require(self.authority.store.load() == authority, "AUTHORITY_CHANGED")
            _require(self.risk.state_store.load_account(c.account_id) == risk_before, "RISK_CHANGED")
            _require(self.profiles.load_mode("SANDBOX_EXECUTION") == profiles_before
                     and self.runtimes.load(expected_account_id=c.account_id) == runtimes_before,
                     "CONFIGURED_SET_CHANGED")

        intent_hash = _hash(intent.to_dict())
        progress = self.progress.load(intent_hash)
        progress_expected = progress
        guard()
        try:
            raw = self.manager.api.get_order_state(c.account_id, intent.intent_id, by_request_id=True)
        except Exception:
            raise DesktopFillRecoveryError("DESKTOP_FILL_RECOVERY_RECEIPT_UNAVAILABLE") from None
        now = self.clock()
        _require(isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None,
                 "CLOCK_INVALID")
        fill = _receipt(raw, intent, now.astimezone(timezone.utc), self.policy)
        guard()
        if fill is None:
            self.last_status = "AWAITING_FULL_FILL"
            # Do not destroy a verified partial checkpoint with a raw refresh.
            # A later complete cumulative receipt may continue it; missing data
            # is never interpreted as rollback to zero or permission to repost.
            return previous if progress is not None else self.refresh()

        effective_target = c.current_lots + (fill.executed_lots if c.direction == "BUY" else -fill.executed_lots)
        _require(effective_target >= 0, "OBSERVED_LOTS_MISMATCH")
        kind = ("desktop-full-fill:" if fill.outcome == "FILLED" else
                "desktop-terminal:" if fill.terminal else "desktop-partial:")
        prefix = kind + _hash({"account": c.account_id, "intent": intent.intent_id}) + ":" + fill.sha256
        old = previous.position(c.instrument_id)
        resumed = fill.terminal and previous.last_transaction_status == "COMMITTED" and str(previous.last_transaction_id or "").startswith(prefix + ":")
        from_partial = False
        if progress is not None:
            _require(progress["exchange_id"] == fill.order["orderId"]
                     and fill.executed_lots >= progress["executed_lots"], "PARTIAL_PROGRESS_REGRESSION")
            stages_by_id = {t[0]: list(t) for t in fill.stages}
            _require(all(stages_by_id.get(t[0]) == t for t in progress["stages"]), "PARTIAL_STAGE_REWRITE")
            from_partial = (_hash(previous.to_dict()) == progress["before_state_sha256"] or
                previous.last_transaction_status == "COMMITTED"
                and previous.last_transaction_id == progress["after_transaction_id"]
                and _hash(old.to_dict() if old else None) == progress["after_position_sha256"])
            _require(from_partial or resumed, "PARTIAL_CHECKPOINT_REQUIRED")
        if resumed:
            _require(old is not None and old.actual_lots == old.target_lots == effective_target,
                     "PREPOSITION_MISMATCH")
        elif from_partial:
            pass  # Exact before-state or committed partial checkpoint proved above.
        elif old is None:
            _require(c.current_lots == 0, "PREPOSITION_MISMATCH")
        else:
            _require(old.actual_lots == c.current_lots and old.target_lots in {None, c.current_lots},
                     "PREPOSITION_MISMATCH")
        if old is not None and not from_partial and (old.actual_lots != 0 or resumed and effective_target != 0):
            _require(old.ownership is not None and old.ownership.strategy_id == c.strategy_id
                     and old.ownership.config_hash == c.strategy_profile_hash
                     and old.ownership.candle_interval == c.candle_interval, "OWNER_MISMATCH")
        _require(intent.intent_id not in risk_before.recorded_execution_ids or resumed,
                 "ACCOUNTING_CHECKPOINT_REQUIRED")
        # No LOCAL uncertain order, foreign pending or colliding cached identity
        # may be retired as a side effect of recovery for this one request.
        for position in previous.positions:
            for pending in position.pending_orders:
                if pending.active or pending.uncertain:
                    _require(pending.source == "BROKER" and pending.instrument_id == c.instrument_id
                             and pending.order_request_id == intent.intent_id
                             and pending.broker_order_id == fill.order["orderId"], "OTHER_PENDING_ORDER")
        portfolio, orders = self.policy.acquire(_ReceiptReadView(self.manager.api, fill), previous, guard)
        cash = self.cash_policy.acquire(self.manager.api, guard)
        guard()
        broker = cash.apply(BrokerPortfolioAdapter.from_api_portfolio(
            portfolio, account_id=c.account_id, broker_orders=orders))
        observed = next((p for p in broker.positions if p.instrument_id == c.instrument_id), None)
        actual = observed.actual_lots if observed else 0
        _require(actual == effective_target, "OBSERVED_LOTS_MISMATCH")
        _require(_utc(broker.snapshot_at) > _utc(intent.updated_at)
                 and (fill.last_trade_at is None or _utc(broker.snapshot_at) >= fill.last_trade_at),
                 "SNAPSHOT_PREDATES_FILL")
        if fill.last_trade_at is not None:
            _require(_utc(broker.snapshot_at).astimezone(MOSCOW_TZ).date()
                     == fill.last_trade_at.astimezone(MOSCOW_TZ).date(),
                     "ACCOUNTING_PERIOD_OUT_OF_SCOPE")
        base_runtime = RuntimePortfolioAdapter.from_portfolio_state(previous)
        owner = PositionOwnership(strategy_id=c.strategy_id, config_hash=c.strategy_profile_hash,
            candle_interval=c.candle_interval, source="CANONICAL_TRANSACTION", attributed_at=broker.snapshot_at)
        old_runtime = next((p for p in base_runtime.positions if p.instrument_id == c.instrument_id), None)
        updated = RuntimePositionRecord(instrument_id=c.instrument_id,
            figi=old_runtime.figi if old_runtime else "", ticker=c.ticker,
            class_code=runtime.config.class_code,
            target=PortfolioTarget(instrument_id=c.instrument_id, target_lots=effective_target,
                strategy_id=c.strategy_id, config_hash=c.strategy_profile_hash, candle_time=c.candle_time),
            ownership=(old.ownership if fill.executed_lots == 0 and old is not None else owner)
                if effective_target else None,
            pending_orders=old_runtime.pending_orders if old_runtime else (),
            last_candle_time=c.candle_time, state_key="canonical:" + c.instrument_id)
        target_runtime = (replace(base_runtime, positions=tuple(
            [p for p in base_runtime.positions if p.instrument_id != c.instrument_id] + [updated]))
            if fill.terminal else base_runtime)
        candidate = self.manager.reconciler.reconcile(broker, target_runtime, previous=previous,
            context=ReconciliationContext(expected_account_id=c.account_id,
                freshness=SnapshotFreshness.FRESH, generated_at=broker.snapshot_at,
                journal_confirmed_instruments=frozenset({c.instrument_id}) if fill.terminal else frozenset(),
                position_origins={c.instrument_id: PositionOrigin.STRATEGY} if fill.terminal else {}))
        candidate = self.manager._normalize_flat_positions(candidate)
        if fill.terminal:
            _require(not candidate.blocking, "CANONICAL_RECONCILIATION_BLOCKED")
            _require(not any(p.active or p.uncertain for pos in candidate.positions for p in pos.pending_orders),
                     "OTHER_PENDING_ORDER")
        else:
            _require(candidate.blocking, "PARTIAL_BLOCKER_REQUIRED")
            for pos in candidate.positions:
                for pending in pos.pending_orders:
                    if pending.active or pending.uncertain:
                        _require(pending.source == "BROKER" and pending.instrument_id == c.instrument_id
                                 and pending.order_request_id == intent.intent_id
                                 and pending.broker_order_id == fill.order["orderId"], "OTHER_PENDING_ORDER")
        transaction_id = prefix + ":" + _hash({"snapshot": broker.snapshot_at, "previous": previous.to_dict()})
        if not fill.terminal:
            guard()
            next_progress = {"version": 1, "intent_sha256": intent_hash,
                "exchange_id": fill.order["orderId"], "stages": [list(t) for t in fill.stages],
                "executed_lots": fill.executed_lots, "before_state_sha256": _hash(previous.to_dict()),
                "after_transaction_id": transaction_id,
                "after_position_sha256": _hash(candidate.position(c.instrument_id).to_dict())}
            self.progress.save(next_progress, expected=progress)
            progress_expected = next_progress
        elif self.progress.load(intent_hash) != progress:
            _require(False, "PARTIAL_PROGRESS_CHANGED")
        # The exact validated receipt supplies evidence ONLY for this instrument.
        # Partial observations stay blocked and preserve target/ownership.
        # Terminal attribution has no intermediate force-READY publication.
        def transform(current: PortfolioState) -> PortfolioState:
            guard()
            _require(current.to_dict() == previous.to_dict(), "CANONICAL_CHANGED")
            return replace(candidate, migration=current.migration, portfolio_source="CANONICAL")
        transaction = self.manager.transaction_coordinator.commit(
            "DESKTOP_CONFIRMED_FULL_FILL" if fill.outcome == "FILLED" else
            "DESKTOP_CONFIRMED_TERMINAL" if fill.terminal else "DESKTOP_OBSERVED_PARTIAL",
            transform, expected_revision=previous.revision, transaction_id=transaction_id,
            account_id=c.account_id, instrument_id=c.instrument_id, mode="SANDBOX_EXECUTION")
        guard()
        canonical = self.manager.repository.load(expected_account_id=c.account_id)
        _require(canonical.to_dict() == transaction.state.to_dict(), "CANONICAL_CHANGED")
        if not fill.terminal:
            self.last_status = "AWAITING_TERMINAL_PARTIAL"
            return canonical  # Full reservation and Central blocker are unchanged.
        # Existing Central method holds its canonical lease and performs Risk's
        # execution-id de-duplication BEFORE releasing the account blocker.
        final = self.central.mark_reconciled(intent.intent_id,
            portfolio_repository=self.manager.repository, outcome=fill.outcome,
            executed_lots=fill.executed_lots, risk_runtime=self.risk,
            execution_price_rub=float(fill.price) if fill.price is not None else None, execution_price_source="GET_ORDER_STATE_STAGES_VWAP",
            expected_intent=intent, expected_portfolio_state=canonical)
        _require(final.status == "RECONCILED" and final.outcome == fill.outcome
                 and final.executed_lots == fill.executed_lots, "ACCOUNTING_UNCONFIRMED")
        if fill.executed_lots:
            _require(final.risk_execution_id == intent.intent_id
                     and final.risk_execution_status in {"RECORDED", "DUPLICATE"}, "ACCOUNTING_UNCONFIRMED")
        else:
            _require(final.risk_execution_id is None and final.risk_execution_status == "NOT_REQUIRED",
                     "ACCOUNTING_UNCONFIRMED")
        self.last_status = {"FILLED": "RECONCILED_FULL_FILL", "PARTIALLY_FILLED": "RECONCILED_PARTIAL_FILL",
                            "CANCELLED": "RECONCILED_CANCELLED", "REJECTED": "RECONCILED_REJECTED"}[fill.outcome]
        return self.manager.repository.load(expected_account_id=c.account_id)
