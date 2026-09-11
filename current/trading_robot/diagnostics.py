from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .journal import EventJournal, JournalEvent
from .orders import (
    OrderLifecycle,
    executed_lots,
    executed_order_price,
    is_terminal_order_status,
    lifecycle_for_status,
    normalize_execution_status,
    signed_lot_delta,
    transition_intent,
)
from .risk_persistence import RiskProfileStore, RiskStateStore
from .risk_runtime import RiskExecutionOutcome, RiskRuntimeAdapter
from .runtime_cash_authority import (
    CL7RuntimeError,
    RuntimeCashAuthorityStore,
    legacy_execution_guard,
)
from .state_persistence import atomic_write_json
from .tbank_sandbox import TBankAPIError, TBankSandboxClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiagnosticConfig:
    ticker: str = "SBER"
    class_code: str = "TQBR"
    lots: int = 1
    state_file: str = "sandbox_diagnostic_state.json"
    journal_file: str = ""
    order_type: str = "BESTPRICE"
    time_in_force: str = "FILL_AND_KILL"
    reconcile_attempts: int = 3
    reconcile_delay_seconds: float = 0.5
    risk_profile_file: str = "risk_profiles.json"
    risk_state_file: str = "risk_state.json"
    require_risk_accounting: bool = True

    def __post_init__(self) -> None:
        if self.lots != 1:
            raise ValueError(
                "The controlled diagnostic entry point is restricted to exactly 1 lot."
            )
        if self.order_type.upper() not in {"MARKET", "BESTPRICE"}:
            raise ValueError("Unsupported diagnostic order type.")
        if self.time_in_force.upper() not in {
            "DAY",
            "FILL_AND_KILL",
            "FILL_OR_KILL",
        }:
            raise ValueError("Unsupported diagnostic time in force.")
        if self.reconcile_attempts < 1:
            raise ValueError("reconcile_attempts must be positive.")
        if self.reconcile_delay_seconds < 0:
            raise ValueError("reconcile_delay_seconds must not be negative.")


class SandboxOrderDiagnostics:
    """Controlled one-lot order diagnostic independent of strategy signals."""

    STATE_VERSION = 1

    def __init__(
        self,
        api: TBankSandboxClient,
        account_id: str,
        config: DiagnosticConfig,
        *,
        risk_runtime: RiskRuntimeAdapter | None = None,
        require_risk_accounting: bool | None = None,
        authority_store: RuntimeCashAuthorityStore | None = None,
    ) -> None:
        self.api = api
        self.account_id = str(account_id)
        self.config = config
        self.require_risk_accounting = (
            config.require_risk_accounting
            if require_risk_accounting is None
            else bool(require_risk_accounting)
        )
        self.risk_runtime = risk_runtime
        self.instrument = api.find_instrument(config.ticker, config.class_code)
        self.instrument_id = api.instrument_id(self.instrument)
        self.state_path = Path(config.state_file)
        self.authority_store = authority_store or RuntimeCashAuthorityStore(
            self.state_path.parent
        )
        journal_path = (
            Path(config.journal_file)
            if config.journal_file
            else self.state_path.with_name("trading_events.db")
        )
        self.journal = EventJournal(journal_path)
        if self.risk_runtime is None and self.require_risk_accounting:
            profile_path = Path(config.risk_profile_file)
            state_path = Path(config.risk_state_file)
            if not profile_path.is_absolute():
                profile_path = self.state_path.parent / profile_path
            if not state_path.is_absolute():
                state_path = self.state_path.parent / state_path
            self.risk_runtime = RiskRuntimeAdapter(
                account_id=self.account_id,
                mode="SANDBOX_EXECUTION",
                profile_store=RiskProfileStore(profile_path),
                state_store=RiskStateStore(state_path),
                auto_create_dry_run_profile=False,
            )
        self._active_run_id: str | None = None
        self.state_key = "|".join(
            [
                self.account_id,
                self.instrument_id,
                "manual-one-lot-diagnostic",
            ]
        )

    def snapshot(self) -> dict[str, Any]:
        portfolio = self.api.get_portfolio(self.account_id)
        current_lots = self.api.position_lots(portfolio, self.instrument)
        trading_status = self.api.get_trading_status(self.instrument_id)
        max_lots = self.api.get_max_lots(self.account_id, self.instrument_id)
        active_orders = self.api.get_orders(self.account_id)
        root = self._load_state()
        state = root.setdefault("diagnostics", {}).setdefault(self.state_key, {})
        return {
            "status": "ready",
            "ticker": self.config.ticker,
            "instrument_id": self.instrument_id,
            "account_id": self.account_id,
            "current_lots": current_lots,
            "broker_max_buy_lots": self.api.max_buy_lots(max_lots),
            "trading_status": trading_status,
            "best_price_available": self.api.best_price_available(trading_status),
            "market_order_available": self.api.market_order_available(trading_status),
            "active_orders": active_orders,
            "pending_order": state.get("pending_order"),
            "last_response_meta": self.api.last_response_meta,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def execute(self, direction: str) -> dict[str, Any]:
        """Run the original explicitly confirmed one-lot diagnostic."""
        return self._execute_order(
            direction,
            lots=1,
            purpose="manual-one-lot-diagnostic",
        )

    def close_unattributed_position(
        self,
        *,
        expected_lots: int,
        confirmation_text: str,
    ) -> dict[str, Any]:
        """Close an explicitly confirmed unattributed long Sandbox position.

        The broker quantity is re-read immediately before the intent is saved.
        A random request id is persisted before submission, so this operation is
        both unique across independent manual actions and recoverable after a
        lost response.
        """
        lots = int(expected_lots)
        if lots < 1:
            raise ValueError("expected_lots must be positive.")
        phrase = f"CLOSE {self.config.ticker.upper()} {lots}"
        if confirmation_text.strip().upper() != phrase:
            raise ValueError(f"Confirmation mismatch. Enter exactly: {phrase}")
        return self._execute_order(
            "SELL",
            lots=lots,
            purpose="unattributed-position-close",
            require_exact_current_lots=lots,
        )

    def _risk_authorization(
        self,
        *,
        direction: str,
        purpose: str,
    ) -> dict[str, Any]:
        source = (
            "UNATTRIBUTED_POSITION_CLOSE"
            if purpose == "unattributed-position-close"
            else "DIAGNOSTIC"
        )
        base = {
            "execution_source": source,
            "operator_override": True,
            "actor": "GUI_OPERATOR",
            "blocked": False,
        }
        if self.risk_runtime is None:
            if self.require_risk_accounting:
                return {
                    **base,
                    "blocked": True,
                    "reason": (
                        "Risk runtime is unavailable; diagnostic execution "
                        "cannot be accounted safely."
                    ),
                    "risk_policy_hash": None,
                }
            return {
                **base,
                "risk_policy_hash": None,
                "reason": "Risk accounting is not enforced for this caller.",
            }

        try:
            state = self.risk_runtime.state_store.load_account(self.account_id)
        except Exception as exc:
            return {
                **base,
                "blocked": True,
                "reason": f"Risk state is unavailable: {exc}",
                "risk_policy_hash": None,
            }

        if direction == "BUY" and state.kill_switch_active:
            return {
                **base,
                "blocked": True,
                "reason": (
                    "Diagnostic BUY is blocked while the persistent risk "
                    "kill switch is active."
                ),
                "risk_policy_hash": None,
                "kill_switch_active": True,
            }

        try:
            policy_hash = self.risk_runtime.current_policy_hash()
        except Exception as exc:
            if direction == "BUY":
                return {
                    **base,
                    "blocked": True,
                    "reason": f"Sandbox RiskPolicy is unavailable: {exc}",
                    "risk_policy_hash": None,
                }
            policy_hash = f"OPERATOR_REDUCE_ONLY:{source}"

        return {
            **base,
            "risk_policy_hash": policy_hash,
            "kill_switch_active": bool(state.kill_switch_active),
            "reason": (
                "Explicit operator diagnostic override; execution will be "
                "accounted after portfolio reconciliation."
            ),
        }

    def _execute_order(
        self,
        direction: str,
        *,
        lots: int,
        purpose: str,
        require_exact_current_lots: int | None = None,
    ) -> dict[str, Any]:
        direction = direction.strip().upper()
        if direction not in {"BUY", "SELL"}:
            raise ValueError("direction must be BUY or SELL.")
        lots = int(lots)
        if lots < 1:
            raise ValueError("lots must be positive.")
        run_id = str(uuid4())
        self._active_run_id = run_id
        root = self._load_state()
        state = root.setdefault("diagnostics", {}).setdefault(self.state_key, {})

        if state.get("pending_order"):
            recovered = self.recover_pending(run_id=run_id)
            root = self._load_state()
            state = root.setdefault("diagnostics", {}).setdefault(
                self.state_key, {}
            )
            if state.get("pending_order"):
                return {
                    "status": "order_pending",
                    "message": (
                        "Previous diagnostic order is unresolved. New order blocked."
                    ),
                    "recovery": recovered,
                    "pending_order": state.get("pending_order"),
                    "run_id": run_id,
                }

        try:
            with legacy_execution_guard(self.authority_store):
                snapshot = self.snapshot()
                current_lots = int(snapshot["current_lots"])
                if (
                    require_exact_current_lots is not None
                    and current_lots != int(require_exact_current_lots)
                ):
                    return {
                        **snapshot,
                        "status": "execution_blocked",
                        "direction": direction,
                        "reason": (
                            "Broker position changed before the recovery close: "
                            f"expected {require_exact_current_lots}, actual {current_lots}."
                        ),
                        "run_id": run_id,
                    }
                trading_status = snapshot["trading_status"]
                if self.config.order_type.upper() == "MARKET":
                    available = self.api.market_order_available(trading_status)
                else:
                    available = self.api.best_price_available(trading_status)
                if not available:
                    return {
                        **snapshot,
                        "status": "execution_blocked",
                        "direction": direction,
                        "reason": "Instrument is not available for the selected order type.",
                        "run_id": run_id,
                    }
                if direction == "BUY":
                    maximum = snapshot.get("broker_max_buy_lots")
                    if maximum is not None and int(maximum) < lots:
                        return {
                            **snapshot,
                            "status": "execution_blocked",
                            "direction": direction,
                            "reason": "Broker reports insufficient buy capacity.",
                            "run_id": run_id,
                        }
                if direction == "SELL" and current_lots < lots:
                    return {
                        **snapshot,
                        "status": "execution_blocked",
                        "direction": direction,
                        "reason": "There are not enough long lots available to sell.",
                        "run_id": run_id,
                    }

                risk_authorization = self._risk_authorization(
                    direction=direction,
                    purpose=purpose,
                )
                if risk_authorization.get("blocked"):
                    return {
                        **snapshot,
                        "status": "execution_blocked",
                        "direction": direction,
                        "reason": risk_authorization.get("reason"),
                        "risk_authorization": risk_authorization,
                        "run_id": run_id,
                    }

                sequence = int(state.get("sequence", 0)) + 1
                order_id = self._order_id(sequence, direction)
                intent: dict[str, Any] = {
                    "order_id": order_id,
                    "run_id": run_id,
                    "sequence": sequence,
                    "action": direction,
                    "lots": lots,
                    "current_lots_before": current_lots,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "order_type": self.config.order_type,
                    "time_in_force": self.config.time_in_force,
                    "diagnostic": True,
                    "purpose": purpose,
                    "execution_source": risk_authorization.get("execution_source"),
                    "operator_override": risk_authorization.get("operator_override"),
                    "risk_policy_hash": risk_authorization.get("risk_policy_hash"),
                    "risk_authorization": risk_authorization,
                }
                transition_intent(intent, OrderLifecycle.PRECHECK_PASSED)
                transition_intent(intent, OrderLifecycle.INTENT_SAVED)
                state["pending_order"] = intent
                state["sequence"] = sequence
                self._save_state(root)
                self._record_transition(intent, run_id)

                transition_intent(
                    intent,
                    OrderLifecycle.ORDER_SUBMITTED,
                    details={"submission_attempted": True},
                )
                state["pending_order"] = intent
                self._save_state(root)
                self._record_transition(intent, run_id)

                try:
                    response = self.api.post_order(
                        self.account_id,
                        self.instrument_id,
                        lots,
                        direction,
                        order_id=order_id,
                        order_type=self.config.order_type,
                        time_in_force=self.config.time_in_force,
                    )
                except TBankAPIError as exc:
                    if exc.transient or exc.status_code is None or exc.status_code < 400:
                        raise
                    transition_intent(
                        intent,
                        OrderLifecycle.SUBMISSION_FAILED,
                        details={
                            "accepted": False,
                            "submission_error": str(exc),
                            "status_code": exc.status_code,
                            "tracking_id": exc.tracking_id,
                        },
                    )
                    self._record_transition(intent, run_id, {"api_error": exc.details})
                    state.pop("pending_order", None)
                    state["last_order_id"] = order_id
                    state["last_action"] = direction
                    state["last_failure_at"] = datetime.now(timezone.utc).isoformat()
                    self._save_state(root)
                    return {
                        "status": "submission_failed",
                        "ticker": self.config.ticker,
                        "instrument_id": self.instrument_id,
                        "account_id": self.account_id,
                        "direction": direction,
                        "requested_lots": lots,
                        "executed_lots": 0,
                        "order_id": order_id,
                        "order_status": "REJECTED",
                        "order_lifecycle_state": intent.get("lifecycle_state"),
                        "position_reconciled": True,
                        "pending_order": None,
                        "error": str(exc),
                        "api_status_code": exc.status_code,
                        "tracking_id": exc.tracking_id,
                        "run_id": run_id,
                        "purpose": purpose,
                    }
                transition_intent(
                    intent,
                    OrderLifecycle.ORDER_ACCEPTED,
                    details={"accepted": True},
                )
                state["pending_order"] = intent
                self._save_state(root)
                self._record_transition(intent, run_id, {"order_response": response})

                order_state = self.api.get_order_state(
                    self.account_id,
                    order_id,
                    by_request_id=True,
                )
                result = self._finalise_order(
                    root,
                    state,
                    intent,
                    order_state,
                    run_id=run_id,
                    response=response,
                )
                result["purpose"] = purpose
                return result

        except CL7RuntimeError as exc:
            return {
                "status": "cl7_legacy_execution_blocked",
                "direction": direction,
                "reason": exc.reason.value,
                "order_was_sent": False,
                "order_may_have_been_sent": False,
                "run_id": run_id,
            }

    def recover_pending(self, *, run_id: str | None = None) -> dict[str, Any]:
        run_id = run_id or str(uuid4())
        self._active_run_id = run_id
        root = self._load_state()
        state = root.setdefault("diagnostics", {}).setdefault(self.state_key, {})
        intent = state.get("pending_order")
        if not intent:
            return {
                "status": "no_pending_order",
                "ticker": self.config.ticker,
                "run_id": run_id,
            }
        order_id = str(intent.get("order_id", ""))
        if not order_id:
            raise RuntimeError("Diagnostic pending order has no order_id.")
        lifecycle_state = str(intent.get("lifecycle_state") or "")
        execution_already_confirmed = lifecycle_state in {
            str(OrderLifecycle.PORTFOLIO_RECONCILED),
            str(OrderLifecycle.RISK_ACCOUNTING_REQUIRED),
            str(OrderLifecycle.RISK_ACCOUNTED),
        }
        response: dict[str, Any] | None = None
        if execution_already_confirmed:
            order_state = {
                "executionReportStatus": (
                    "EXECUTION_REPORT_STATUS_"
                    + str(intent.get("last_known_status") or "FILL")
                ),
                "lotsExecuted": str(int(intent.get("executed_lots", 0) or 0)),
                "recoveredFromConfirmedIntent": True,
            }
        else:
            try:
                order_state = self.api.get_order_state(
                    self.account_id,
                    order_id,
                    by_request_id=True,
                )
            except TBankAPIError as exc:
                if exc.status_code != 404:
                    raise
                # CL7 removes every automatic provider resubmit path. A
                # missing exact lookup remains durable for operator recovery.
                return {
                    "status": "unknown_submit_state",
                    "resubmitted": False,
                    "reason": "Persisted diagnostic request was not found by exact lookup.",
                    "position_reconciled": False,
                    "run_id": run_id,
                }

        return self._finalise_order(
            root,
            state,
            intent,
            order_state,
            run_id=run_id,
            response=response,
        )

    def _finalise_order(
        self,
        root: dict[str, Any],
        state: dict[str, Any],
        intent: dict[str, Any],
        order_state: dict[str, Any],
        *,
        run_id: str,
        response: dict[str, Any] | None,
    ) -> dict[str, Any]:
        status = normalize_execution_status(order_state)
        filled = executed_lots(order_state)
        transition_intent(
            intent,
            lifecycle_for_status(status),
            details={
                "last_known_status": status,
                "executed_lots": filled,
            },
        )
        state["pending_order"] = intent
        self._save_state(root)
        self._record_transition(intent, run_id, {"order_state": order_state})

        actual_after: int | None = None
        expected_after = int(intent.get("current_lots_before", 0)) + signed_lot_delta(
            str(intent["action"]),
            filled,
        )
        reconciled = False
        attempts_used = 0
        reconciliation: dict[str, Any] = {}
        risk_execution: RiskExecutionOutcome | None = None
        result_status = "order_pending"

        if is_terminal_order_status(
            status,
            str(intent.get("time_in_force", self.config.time_in_force)),
        ):
            reconciliation = self._reconcile(expected_after)
            reconciled = bool(reconciliation.get("position_reconciled"))
            actual_after = reconciliation.get("actual_lots_after")
            attempts_used = int(
                reconciliation.get("reconcile_attempts_used", 0) or 0
            )
            if reconciled:
                transition_intent(
                    intent,
                    OrderLifecycle.PORTFOLIO_RECONCILED,
                    details={
                        "expected_lots_after": expected_after,
                        "actual_lots_after": actual_after,
                        "reconcile_attempts_used": attempts_used,
                        "reconciliation_confirmed_at": reconciliation.get(
                            "reconciliation_confirmed_at"
                        ),
                        "reconciliation_proof": reconciliation.get(
                            "reconciliation_proof"
                        ),
                    },
                )
                state["pending_order"] = intent
                self._save_state(root)
                self._record_transition(
                    intent,
                    run_id,
                    {
                        "reconciliation_proof": reconciliation.get(
                            "reconciliation_proof"
                        )
                    },
                )

                risk_execution = self._register_risk_execution(
                    intent=intent,
                    order_state=order_state,
                    reconciliation=reconciliation,
                    run_id=run_id,
                )
                if (
                    risk_execution is not None
                    and risk_execution.status == "RUNTIME_ERROR"
                    and self.require_risk_accounting
                ):
                    transition_intent(
                        intent,
                        OrderLifecycle.RISK_ACCOUNTING_REQUIRED,
                        details={
                            "expected_lots_after": expected_after,
                            "actual_lots_after": actual_after,
                            "reconciliation_confirmed_at": reconciliation.get(
                                "reconciliation_confirmed_at"
                            ),
                            "risk_execution_error": risk_execution.error,
                        },
                    )
                    state["pending_order"] = intent
                    self._save_state(root)
                    self._record_transition(intent, run_id)
                    result_status = "risk_accounting_pending"
                else:
                    if risk_execution is not None:
                        transition_intent(
                            intent,
                            OrderLifecycle.RISK_ACCOUNTED,
                            details={
                                "risk_execution_status": risk_execution.status,
                                "risk_execution_duplicate": (
                                    risk_execution.duplicate
                                ),
                                "reconciliation_confirmed_at": (
                                    risk_execution.reconciliation_confirmed_at
                                ),
                            },
                        )
                        self._record_transition(intent, run_id)
                    state.pop("pending_order", None)
                    state["last_order_id"] = intent["order_id"]
                    state["last_action"] = intent["action"]
                    state["last_reconciled_at"] = reconciliation.get(
                        "reconciliation_confirmed_at"
                    )
                    result_status = "processed"
            else:
                transition_intent(
                    intent,
                    OrderLifecycle.RECONCILIATION_REQUIRED,
                    details={
                        "expected_lots_after": expected_after,
                        "actual_lots_after": actual_after,
                        "reconcile_attempts_used": attempts_used,
                    },
                )
                state["pending_order"] = intent
                self._record_transition(intent, run_id)
        self._save_state(root)

        result = {
            "status": result_status,
            "ticker": self.config.ticker,
            "instrument_id": self.instrument_id,
            "account_id": self.account_id,
            "direction": intent["action"],
            "requested_lots": int(intent.get("lots", 1)),
            "executed_lots": filled,
            "order_id": intent["order_id"],
            "order_status": status,
            "order_lifecycle_state": intent.get("lifecycle_state"),
            "expected_lots_after": expected_after,
            "actual_lots_after": actual_after,
            "position_reconciled": reconciled,
            "reconcile_attempts_used": attempts_used,
            "reconciliation_confirmed_at": reconciliation.get(
                "reconciliation_confirmed_at"
            ),
            "reconciliation_proof": reconciliation.get(
                "reconciliation_proof"
            ),
            "risk_execution": (
                risk_execution.to_dict() if risk_execution is not None else None
            ),
            "risk_execution_status": intent.get("risk_execution_status"),
            "execution_source": intent.get("execution_source"),
            "operator_override": intent.get("operator_override"),
            "purpose": intent.get("purpose"),
            "pending_order": state.get("pending_order"),
            "order_response": response,
            "order_state": order_state,
            "run_id": run_id,
            "last_response_meta": self.api.last_response_meta,
        }
        self._record_event(
            JournalEvent(
                category="diagnostic",
                event_type=result["status"],
                severity=(
                    "INFO"
                    if result_status == "processed"
                    else "ERROR"
                    if result_status == "risk_accounting_pending"
                    else "WARNING"
                ),
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                order_id=intent["order_id"],
                status=result_status,
                action=str(intent.get("action") or "HOLD"),
                config_hash=str(intent.get("risk_policy_hash") or "") or None,
                payload={
                    key: value
                    for key, value in result.items()
                    if key not in {"order_response", "order_state"}
                },
            )
        )
        return result

    def _reconcile(self, expected_lots: int) -> dict[str, Any]:
        actual: int | None = None
        attempts = 0
        last_portfolio: dict[str, Any] = {}
        for attempts in range(1, self.config.reconcile_attempts + 1):
            last_portfolio = self.api.get_portfolio(self.account_id)
            actual = self.api.position_lots(last_portfolio, self.instrument)
            if actual == expected_lots:
                confirmed_at = datetime.now(timezone.utc).isoformat()
                proof = {
                    "position_reconciled": True,
                    "account_id": self.account_id,
                    "instrument_id": self.instrument_id,
                    "expected_lots_after": expected_lots,
                    "actual_lots_after": actual,
                    "reconcile_attempts_used": attempts,
                    "confirmed_at": confirmed_at,
                }
                return {
                    **proof,
                    "reconciliation_confirmed_at": confirmed_at,
                    "reconciliation_proof": proof,
                    "portfolio": last_portfolio,
                }
            if (
                attempts < self.config.reconcile_attempts
                and self.config.reconcile_delay_seconds > 0
            ):
                time.sleep(self.config.reconcile_delay_seconds)
        checked_at = datetime.now(timezone.utc).isoformat()
        proof = {
            "position_reconciled": False,
            "account_id": self.account_id,
            "instrument_id": self.instrument_id,
            "expected_lots_after": expected_lots,
            "actual_lots_after": actual,
            "reconcile_attempts_used": attempts,
            "confirmed_at": None,
            "checked_at": checked_at,
        }
        return {
            **proof,
            "reconciliation_confirmed_at": None,
            "reconciliation_proof": proof,
            "portfolio": last_portfolio,
        }

    def _register_risk_execution(
        self,
        *,
        intent: dict[str, Any],
        order_state: dict[str, Any],
        reconciliation: dict[str, Any],
        run_id: str,
    ) -> RiskExecutionOutcome | None:
        filled = int(intent.get("executed_lots", 0) or 0)
        source = str(
            intent.get("execution_source") or "DIAGNOSTIC"
        ).strip().upper()
        if filled <= 0:
            intent["risk_execution_status"] = "NOT_REQUIRED"
            return None
        if self.risk_runtime is None:
            if not self.require_risk_accounting:
                intent["risk_execution_status"] = "NOT_ENFORCED"
                return None
            outcome = RiskExecutionOutcome(
                enforced=True,
                mode="SANDBOX_EXECUTION",
                execution_id=str(intent.get("order_id") or ""),
                decision_id=None,
                policy_hash=str(intent.get("risk_policy_hash") or "") or None,
                registration=None,
                execution_source=source,
                reconciliation_confirmed_at=reconciliation.get(
                    "reconciliation_confirmed_at"
                ),
                reconciliation_proof=reconciliation.get(
                    "reconciliation_proof"
                ),
                error="Risk runtime is unavailable for diagnostic accounting.",
            )
            intent["risk_execution_status"] = outcome.status
            intent["risk_execution"] = outcome.to_dict()
            self._record_risk_execution(outcome, intent, run_id)
            return outcome

        fallback_price = intent.get("confirmed_execution_price_rub")
        price, price_source = executed_order_price(
            order_state,
            fallback=fallback_price,
        )
        if price is not None:
            intent["confirmed_execution_price_rub"] = price
            intent["confirmed_execution_price_source"] = price_source
        if price is None:
            outcome = RiskExecutionOutcome(
                enforced=True,
                mode="SANDBOX_EXECUTION",
                execution_id=str(intent.get("order_id") or ""),
                decision_id=None,
                policy_hash=str(intent.get("risk_policy_hash") or "") or None,
                registration=None,
                execution_source=source,
                reconciliation_confirmed_at=reconciliation.get(
                    "reconciliation_confirmed_at"
                ),
                reconciliation_proof=reconciliation.get(
                    "reconciliation_proof"
                ),
                error="Confirmed diagnostic fill has no usable execution price.",
            )
        else:
            policy_hash = str(intent.get("risk_policy_hash") or "").strip() or None
            outcome = self.risk_runtime.record_execution(
                execution_id=str(intent.get("order_id") or ""),
                executed_at=datetime.now(timezone.utc),
                signed_lots=signed_lot_delta(
                    str(intent.get("action") or ""),
                    filled,
                ),
                price_rub=price,
                lot_size=max(1, int(self.instrument.get("lot", 1) or 1)),
                portfolio=reconciliation.get("portfolio") or {},
                decision_id=None,
                expected_policy_hash=policy_hash,
                price_source=price_source,
                execution_source=source,
                reconciliation_confirmed_at=reconciliation.get(
                    "reconciliation_confirmed_at"
                ),
                reconciliation_proof=reconciliation.get(
                    "reconciliation_proof"
                ),
            )
        intent["risk_execution_status"] = outcome.status
        intent["risk_execution"] = outcome.to_dict()
        intent["risk_execution_id"] = outcome.execution_id
        intent["risk_execution_source"] = outcome.execution_source
        intent["risk_execution_price_rub"] = outcome.price_rub
        intent["risk_execution_price_source"] = outcome.price_source
        intent["reconciliation_confirmed_at"] = (
            outcome.reconciliation_confirmed_at
        )
        intent["reconciliation_proof"] = outcome.reconciliation_proof
        self._record_risk_execution(outcome, intent, run_id)
        return outcome

    def _record_risk_execution(
        self,
        outcome: RiskExecutionOutcome,
        intent: dict[str, Any],
        run_id: str,
    ) -> None:
        if outcome.registration is not None:
            for event in outcome.registration.events:
                self._record_event(
                    JournalEvent(
                        category="risk",
                        event_type=event.event_type,
                        severity=event.severity,
                        run_id=run_id,
                        account_id=self.account_id,
                        instrument_id=self.instrument_id,
                        ticker=self.config.ticker,
                        order_id=str(intent.get("order_id") or "") or None,
                        mode="SANDBOX_EXECUTION",
                        status=outcome.status,
                        action=str(intent.get("action") or "HOLD"),
                        config_hash=outcome.policy_hash,
                        payload={
                            **event.details,
                            "execution_source": outcome.execution_source,
                            "purpose": intent.get("purpose"),
                            "risk_policy_hash": outcome.policy_hash,
                            "risk_execution_id": outcome.execution_id,
                            "price_rub": outcome.price_rub,
                            "price_source": outcome.price_source,
                            "reconciliation_confirmed_at": (
                                outcome.reconciliation_confirmed_at
                            ),
                            "reconciliation_proof": (
                                outcome.reconciliation_proof
                            ),
                            "duplicate": outcome.duplicate,
                        },
                    )
                )
        else:
            self._record_event(
                JournalEvent(
                    category="risk",
                    event_type="RISK_EXECUTION_RUNTIME_ERROR",
                    severity="ERROR",
                    run_id=run_id,
                    account_id=self.account_id,
                    instrument_id=self.instrument_id,
                    ticker=self.config.ticker,
                    order_id=str(intent.get("order_id") or "") or None,
                    mode="SANDBOX_EXECUTION",
                    status=outcome.status,
                    action=str(intent.get("action") or "HOLD"),
                    config_hash=outcome.policy_hash,
                    payload={
                        "error": outcome.error,
                        "execution_source": outcome.execution_source,
                        "purpose": intent.get("purpose"),
                        "risk_execution_id": outcome.execution_id,
                        "price_rub": outcome.price_rub,
                        "price_source": outcome.price_source,
                        "reconciliation_confirmed_at": (
                            outcome.reconciliation_confirmed_at
                        ),
                        "reconciliation_proof": outcome.reconciliation_proof,
                    },
                )
            )

    def _order_id(self, sequence: int, direction: str) -> str:
        # Manual diagnostic actions are independent operator commands. A fresh
        # UUID prevents reuse after a state rollback or a copied installation.
        # The generated value is persisted before submission, so recovery still
        # uses exactly the same request id after a lost response.
        del sequence, direction
        return str(uuid4())

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"version": self.STATE_VERSION, "diagnostics": {}}
        try:
            root = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(root, dict):
                raise ValueError("Diagnostic state root must be an object.")
            root.setdefault("version", self.STATE_VERSION)
            root.setdefault("diagnostics", {})
            return root
        except (OSError, json.JSONDecodeError, ValueError):
            raise RuntimeError(
                "Diagnostic state file is unreadable. Do not delete it until "
                "the broker confirms there is no unresolved diagnostic order."
            )

    def _handle_state_persistence_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        severity = {
            "STATE_SAVE_RETRY": "WARNING",
            "STATE_SAVE_RECOVERED": "INFO",
            "STATE_SAVE_FAILED": "ERROR",
        }.get(event_type, "INFO")
        self._record_event(
            JournalEvent(
                category="state",
                event_type=event_type,
                severity=severity,
                run_id=self._active_run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                mode="DIAGNOSTIC",
                status=event_type.lower(),
                payload=payload,
            )
        )
        message = (
            f"{event_type} diagnostic run={str(self._active_run_id or '')[:8]} "
            f"attempt={payload.get('attempt_count') or payload.get('attempt')} "
            f"error={payload.get('error', '—')}"
        )
        if severity == "ERROR":
            logger.error(message)
        elif severity == "WARNING":
            logger.warning(message)
        else:
            logger.info(message)

    def _save_state(self, state: dict[str, Any]) -> None:
        state["version"] = self.STATE_VERSION
        atomic_write_json(
            self.state_path,
            state,
            event_callback=self._handle_state_persistence_event,
            backup_existing=True,
        )

    def _record_transition(
        self,
        intent: dict[str, Any],
        run_id: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        lifecycle = str(intent.get("lifecycle_state", "UNKNOWN"))
        severity = (
            "ERROR"
            if lifecycle in {
                str(OrderLifecycle.RECONCILIATION_REQUIRED),
                str(OrderLifecycle.RISK_ACCOUNTING_REQUIRED),
            }
            else "WARNING"
            if lifecycle in {
                str(OrderLifecycle.SUBMISSION_FAILED),
                str(OrderLifecycle.REJECTED),
                str(OrderLifecycle.CANCELLED),
            }
            else "INFO"
        )
        self._record_event(
            JournalEvent(
                category="diagnostic_order",
                event_type=lifecycle,
                severity=severity,
                run_id=run_id,
                account_id=self.account_id,
                instrument_id=self.instrument_id,
                ticker=self.config.ticker,
                order_id=intent.get("order_id"),
                mode="SANDBOX_EXECUTION",
                status=lifecycle,
                action=str(intent.get("action") or "HOLD"),
                config_hash=str(intent.get("risk_policy_hash") or "") or None,
                payload={
                    "action": intent.get("action"),
                    "lots": intent.get("lots"),
                    "current_lots_before": intent.get("current_lots_before"),
                    "executed_lots": intent.get("executed_lots"),
                    "expected_lots_after": intent.get("expected_lots_after"),
                    "actual_lots_after": intent.get("actual_lots_after"),
                    "last_known_status": intent.get("last_known_status"),
                    "purpose": intent.get("purpose"),
                    "execution_source": intent.get("execution_source"),
                    "operator_override": intent.get("operator_override"),
                    "risk_policy_hash": intent.get("risk_policy_hash"),
                    "risk_execution_status": intent.get(
                        "risk_execution_status"
                    ),
                    "risk_execution_id": intent.get("risk_execution_id"),
                    "risk_execution_price_rub": intent.get(
                        "risk_execution_price_rub"
                    ),
                    "risk_execution_price_source": intent.get(
                        "risk_execution_price_source"
                    ),
                    "reconciliation_confirmed_at": intent.get(
                        "reconciliation_confirmed_at"
                    ),
                    "reconciliation_proof": intent.get(
                        "reconciliation_proof"
                    ),
                    **(extra or {}),
                },
            )
        )

    def _record_event(self, event: JournalEvent) -> None:
        try:
            self.journal.record(event)
        except Exception:
            logger.exception("Failed to write diagnostic event journal.")
