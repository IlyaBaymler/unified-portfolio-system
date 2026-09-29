"""Bind a checked exchange identity after a lost exact dispatch acknowledgement.

This changes only Central IN_FLIGHT -> SUBMITTED. It does not infer execution,
settle cash, release a reservation, reset an attempt, arm, post or cancel. The
caller holds the authority lock; the cooperative recovery tick serializes calls.
A committed Central record is the restart marker, so no new multi-store plan is
needed for this single-owner transition. Subsequent settlement re-reads evidence.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
from typing import Any

from .central_order_manager import CentralOrderIntent
from .desktop_fill_recovery import DesktopFillRecovery
from .exact_cash_settlement import _sha
from .exact_order_receipt import ExactOrderReceipt
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .exact_settlement_closure import _config, _owners, _utc
from .locking import InterProcessFileLock
from .runtime_cash_authority import RuntimeCashAuthorityState


class ExactInflightRecoveryError(RuntimeError):
    """Finite diagnostic, never raw provider payload or order identity."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ExactInflightRecoveryError("EXACT_INFLIGHT_" + code)


def bind_inflight_order_locked(
    adapter: Any, *, recovery: DesktopFillRecovery, expected_proof_sha256: str,
) -> CentralOrderIntent:
    """Caller must hold the account authority lock. One checked Central write.

    A not-found response, timeout, mismatched receipt, expired observation or
    changed local state leaves IN_FLIGHT untouched. An exception after the one
    durable write leaves SUBMITTED; a later tick re-reads the bound receipt. A
    readback error never rolls back the record or authorizes another POST.
    """
    authority = adapter.cash_authority_manager
    record = authority.store._load_unlocked(allow_missing_legacy=False)
    _require(record.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
             and record.pending_dispatch_proof_sha256 == expected_proof_sha256,
             "AUTHORITY_MISMATCH")
    config = _config(adapter, recovery)
    central = adapter.manager.state()
    intent = authority._recovery_intent_from_state(
        record, central, identity_key=adapter.cl7_identity_key,
    )
    _require(intent.status == "IN_FLIGHT" and intent.broker_order_id is None
             and central.blocking_intent == intent and not central.queued,
             "INTENT_OUT_OF_SCOPE")
    expected = _owners(adapter, recovery)
    ledger_hash = _sha(adapter.cl7_ledger_store.export_bytes())
    policy = adapter.cl7_own_funds_policy
    started = last_tick = adapter.cl7_monotonic_ns()
    started_wall = last_wall = timestamp_ns(adapter.cl7_clock())
    _require(type(started) is int and started >= 0, "CLOCK_INVALID")

    def guard() -> None:
        nonlocal last_tick, last_wall
        tick, wall = adapter.cl7_monotonic_ns(), timestamp_ns(adapter.cl7_clock())
        _require(type(tick) is int and last_tick <= tick <= started + MAX_AGE_NS
                 and last_wall <= wall <= started_wall + MAX_AGE_NS,
                 "OBSERVATION_STALE")
        last_tick, last_wall = tick, wall
        _require(adapter.cl7_own_funds_policy is policy
                 and _config(adapter, recovery) == config
                 and _owners(adapter, recovery) == expected
                 and _sha(adapter.cl7_ledger_store.export_bytes()) == ledger_hash,
                 "CUSTODY_CHANGED")

    guard()
    inspection = adapter._inspect_exact_order(intent)
    receipt = inspection.exact_receipt
    _require(inspection.status == "ORDER_OBSERVED"
             and type(receipt) is ExactOrderReceipt
             and receipt.proof_sha256 == expected_proof_sha256
             and inspection.intent_id == intent.intent_id
             and type(inspection.broker_order_id) is str
             and bool(inspection.broker_order_id), "RECEIPT_UNAVAILABLE")
    guard()
    # Persist the identity, not any claimed execution quantity/outcome. The
    # existing receipt decoder checked request/account/instrument/direction,
    # stages, saved HMAC proof and metadata before this write is considered.
    at = _utc(receipt.observed_at).isoformat()
    bound = intent.transition(
        "SUBMITTED", at=at, broker_order_id=inspection.broker_order_id,
        detail="exact recovery identity observed; receipt=" + receipt.receipt_identity_sha256,
    )
    updated = replace(central.replace_intent(bound), revision=central.revision + 1,
                      updated_at=at)
    # Same final cooperative lock order as exact closure. No provider calls
    # occur inside this final group. Audit must succeed before the state write.
    with ExitStack() as locks:
        for path in (recovery.profiles.lock_path, recovery.runtimes.lock_path):
            locks.enter_context(InterProcessFileLock(path, timeout_seconds=0.1))
        locks.enter_context(recovery.manager.repository.locked_snapshot(
            expected_account_id=adapter.policy.account_id))
        for path in (recovery.risk.profile_store.lock_path, recovery.risk.state_store.lock_path):
            locks.enter_context(InterProcessFileLock(path, timeout_seconds=0.1))
        locks.enter_context(authority.ledger_guard(adapter.cl7_ledger_store))
        locks.enter_context(InterProcessFileLock(adapter.manager.store.lock_path, timeout_seconds=0.1))
        guard()
        recovery.manager.transaction_coordinator._record(
            "EXACT_INFLIGHT_IDENTITY_OBSERVED", account_id=adapter.policy.account_id,
            instrument_id=intent.candidate.instrument_id, mode="SANDBOX_EXECUTION",
            status="verified_pending_binding", payload={
                "proof_sha256": expected_proof_sha256,
                "receipt_sha256": receipt.receipt_identity_sha256,
                "provider_status": receipt.provider_status,
            },
        )
        guard()
        adapter.manager.store._save_unlocked(updated)
        expected = {**expected, "central": updated.to_dict()}
        guard()
    _require(adapter.manager.state() == updated, "CENTRAL_READBACK_FAILED")
    return bound
