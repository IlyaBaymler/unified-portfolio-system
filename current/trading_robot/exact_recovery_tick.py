"""One exact full-fill recovery tick; never a trading or re-arm entrypoint.

Cash is written only before an authenticated closure plan exists. A later tick
resumes that plan, including the Central-complete / authority-pending cut. The
STEP14/15 owners revalidate all evidence; result flags are not authorizations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .desktop_fill_recovery import DesktopFillRecovery
from .exact_inflight_recovery import bind_inflight_order_locked
from .exact_settlement_closure import _ClosureStore, _config, _owners, _prefix
from .locking import InterProcessFileLock
from .runtime_cash_authority import RuntimeCashAuthorityState


class ExactRecoveryTickError(RuntimeError):
    """Finite, privacy-safe failure; committed prefixes are not rolled back."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ExactRecoveryTickError("EXACT_RECOVERY_" + code)


@dataclass(frozen=True, slots=True)
class ExactRecoveryTickResult:
    runtime_key: str
    ticker: str
    proof_sha256: str
    closure_plan_sha256: str
    ledger_head_sha256: str
    authority_record_sha256: str
    resumed_closure: bool
    status: str = "EXACT_SETTLEMENT_CLOSED_DISARMED"

    def to_canonical_dict(self) -> dict[str, Any]:
        # Runtime identities / account / request / monetary amounts stay private.
        return {
            "domain": "CL7_EXACT_RECOVERY_TICK_V1", "version": 1,
            "status": self.status, "proof_sha256": self.proof_sha256,
            "closure_plan_sha256": self.closure_plan_sha256,
            "ledger_head_sha256": self.ledger_head_sha256,
            "authority_record_sha256": self.authority_record_sha256,
            "resumed_closure": self.resumed_closure,
            "provider_post_calls": 0, "strategy_evaluated": False,
            "automatic_rearm_allowed": False,
        }


def run_exact_recovery_tick(adapter: Any, *, recovery: DesktopFillRecovery) -> ExactRecoveryTickResult:
    """Record/verify cash, then close exactly the persisted pending full FILL.

    Existing financial guards remain authoritative. No raw refresh, fallback to
    LEGACY, discovery by position, automatic cancellation or resubmission exists.
    A failure may leave a committed prefix; only its pinned plan can resume it.
    """
    try:
        _require(type(recovery) is DesktopFillRecovery, "OWNER_GRAPH_MISMATCH")
        with InterProcessFileLock(
            recovery.manager.repository.path.with_name("exact_recovery_tick.lock"),
            timeout_seconds=0.1,
        ):
            authority = adapter.cash_authority_manager
            with authority.store.locked():
                _config(adapter, recovery)  # Check graph/config before cash writes.
                record = authority.store._load_unlocked(allow_missing_legacy=False)
                _require(record.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING,
                         "NOT_PENDING")
                proof = record.pending_dispatch_proof_sha256
                central = adapter.manager.state()
                intent = authority._recovery_intent_from_state(
                    record, central, identity_key=adapter.cl7_identity_key,
                )
                plan = _ClosureStore(adapter.manager.store.path.parent, proof,
                                     adapter.cl7_identity_key).load()
                if plan is None and intent.status == "IN_FLIGHT":
                    # Bind only a checked exchange identity. The existing cash
                    # and closure protocols still re-read all financial evidence.
                    intent = bind_inflight_order_locked(
                        adapter, recovery=recovery, expected_proof_sha256=proof,
                    )
                    central = adapter.manager.state()
                if plan is None:
                    _require(central.blocking_intent == intent and not central.queued
                             and intent.status in {"SUBMITTED", "UNCERTAIN"},
                             "INTENT_OUT_OF_SCOPE")
                else:
                    _require(plan["proof_sha256"] == proof, "PLAN_BINDING_MISMATCH")
                    _prefix(plan, _owners(adapter, recovery))
            if plan is None:
                # Identity is rechecked under the cash writer's authority lock;
                # a changed request cannot become the target of a stale tick.
                adapter.record_exact_cash_components(expected_proof_sha256=proof)
            closed = adapter.finalize_exact_settlement(recovery=recovery, proof_sha256=proof)
            _require(closed.proof_sha256 == proof, "RESULT_BINDING_MISMATCH")
            return ExactRecoveryTickResult(
                intent.candidate.runtime_key, intent.candidate.ticker, proof,
                closed.closure_plan_sha256, closed.ledger_head_sha256,
                closed.authority_record_sha256, plan is not None,
            )
    except ExactRecoveryTickError:
        raise
    except Exception:
        # Do not publish raw provider/local exceptions or retry a failed write.
        raise ExactRecoveryTickError("EXACT_RECOVERY_BLOCKED") from None
