"""Private operator surface for the CL7 runtime cash-authority state machine."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CURRENT = Path(__file__).resolve().parents[1]
if str(CURRENT) not in sys.path:
    sys.path.insert(0, str(CURRENT))

from trading_robot.broker_read_adapters import TBANK_OPERATION_CODEC
from trading_robot.cash_ledger_opening_reconciliation import (
    CL4_OPENING_CODEC,
)
from trading_robot.cash_ledger_persistence import CashLedgerStore
from trading_robot.central_order_manager import (
    CentralOrderManager,
    CentralOrderStore,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_runtime import PortfolioRiskRuntime
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_runtime import RiskRuntimeAdapter
from trading_robot.runtime_cash_authority import (
    CL7RuntimeError,
    CL7RuntimeReason,
    RuntimeCashAuthorityManager,
    RuntimeCashAuthorityState,
    RuntimeCashAuthorityStore,
)
from trading_robot.sandbox_execution_adapter import (
    SandboxDispatchResult,
    SandboxExecutionAdapter,
    SandboxExecutionPolicy,
    SandboxInspectionResult,
)
from trading_robot.secret_provider import (
    Q7SecretError,
    resolve_q7_protected_secrets,
)
from trading_robot.tbank_sandbox import TBankSandboxClient

COMMANDS = (
    "status",
    "prepare",
    "confirm",
    "activate",
    "cancel",
    "arm",
    "disarm",
    "rollback",
    "sync",
    "recover",
    "inspect",
    "dispatch",
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CL7 exact cash authority operator tool"
    )
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--runtime-dir", type=Path, default=Path.cwd())
    parser.add_argument("--confirmation", default="")
    parser.add_argument(
        "--expected-intent-id",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--allow-environment-secrets",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def _provider(token: str) -> TBankSandboxClient:
    return TBankSandboxClient(token=token, max_retries=0)


@dataclass(slots=True)
class _Runtime:
    root: Path
    authority: RuntimeCashAuthorityManager
    ledger: CashLedgerStore
    portfolio: PortfolioRepository
    profiles: RiskProfileStore
    risk_state: RiskStateStore
    central: CentralOrderManager
    provider: TBankSandboxClient | None
    raw_account: str
    identity_key: bytes
    identity_key_id: str

    def inputs(self) -> dict[str, Any]:
        if self.provider is None:
            raise CL7RuntimeError(
                CL7RuntimeReason.BROKER_READ_FAILED,
                stage="PROVIDER_CREDENTIALS",
            )
        return {
            "ledger_store": self.ledger,
            "portfolio_repository": self.portfolio,
            "risk_profile_store": self.profiles,
            "risk_state_store": self.risk_state,
            "central_manager": self.central,
            "provider": self.provider,
            "raw_account_id": self.raw_account,
            "identity_key": self.identity_key,
            "identity_key_id": self.identity_key_id,
            "clock": _timestamp,
            "monotonic_ns": time.monotonic_ns,
            "wait_ns": lambda duration: time.sleep(duration / 1_000_000_000),
        }

    def adapter(self) -> SandboxExecutionAdapter:
        if self.provider is None:
            raise CL7RuntimeError(
                CL7RuntimeReason.BROKER_READ_FAILED,
                stage="PROVIDER_CREDENTIALS",
            )
        risk = RiskRuntimeAdapter(
            account_id=self.raw_account,
            mode="SANDBOX_EXECUTION",
            profile_store=self.profiles,
            state_store=self.risk_state,
            auto_create_dry_run_profile=False,
        )
        portfolio_risk = PortfolioRiskRuntime(
            account_id=self.raw_account,
            profile_store=self.profiles,
            state_store=self.risk_state,
        )
        return SandboxExecutionAdapter(
            self.provider,
            self.central,
            SandboxExecutionPolicy(account_id=self.raw_account),
            risk_runtime=risk,
            portfolio_risk_runtime=portfolio_risk,
            cash_authority_manager=self.authority,
            cl7_identity_key=self.identity_key,
            cl7_identity_key_id=self.identity_key_id,
            cl7_ledger_store=self.ledger,
            cl7_clock=_timestamp,
            cl7_monotonic_ns=time.monotonic_ns,
            cl7_wait_ns=lambda duration: time.sleep(duration / 1_000_000_000),
        )


def _open_runtime(
    root: Path,
    *,
    create_ledger: bool,
    require_provider: bool,
    allow_environment_secrets: bool = False,
) -> _Runtime:
    selected = root.resolve()
    selected.mkdir(parents=True, exist_ok=True)
    resolved = resolve_q7_protected_secrets(
        allow_environment=allow_environment_secrets,
    )
    raw_account = resolved.account_id
    key = resolved.identity_key
    key_id = resolved.identity_key_id
    authority = RuntimeCashAuthorityManager(RuntimeCashAuthorityStore(selected))
    ledger_root = selected / "cash_ledger_v3_10.sqlite3"
    codecs = (CL4_OPENING_CODEC, TBANK_OPERATION_CODEC)
    if ledger_root.exists():
        ledger = CashLedgerStore.open(ledger_root, codecs, busy_timeout_ms=5_000)
    elif create_ledger:
        ledger = CashLedgerStore.create(ledger_root, codecs, busy_timeout_ms=5_000)
    else:
        raise CL7RuntimeError(CL7RuntimeReason.LEDGER_UNAVAILABLE)
    profiles = RiskProfileStore(selected / "risk_profiles.json")
    risk_state = RiskStateStore(selected / "risk_state.json")
    central = CentralOrderManager(
        CentralOrderStore(selected / "central_order_state.json"),
        account_id=raw_account,
    )
    return _Runtime(
        root=selected,
        authority=authority,
        ledger=ledger,
        portfolio=PortfolioRepository(selected / "portfolio_state.json"),
        profiles=profiles,
        risk_state=risk_state,
        central=central,
        provider=_provider(resolved.token) if require_provider else None,
        raw_account=raw_account,
        identity_key=key,
        identity_key_id=key_id,
    )


def _safe(record: Any) -> dict[str, object]:
    return {
        "account_scope_sha256": record.account_scope_sha256,
        "authority_record_sha256": record.sha256,
        "cutover_generation": record.cutover_generation,
        "execution_armed": record.state is RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        "identity_key_id": record.identity_key_id,
        "ledger_head_sha256": record.ledger_head_sha256,
        "ledger_revision": record.ledger_revision,
        "operations_complete_through": record.operations_complete_through,
        "owner": record.owner.value,
        "pending_dispatch_proof_sha256": record.pending_dispatch_proof_sha256,
        "post_attempt_count": record.post_attempt_count,
        "record_revision": record.record_revision,
        "recovery_required": (
            record.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
        ),
        "state": record.state.value,
        "transition_at": record.transition_at,
        "transition_kind": record.transition_kind,
    }


def _operator_token(value: str | None) -> str | None:
    if value is None:
        return None
    if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) is None:
        return CL7RuntimeReason.INTERNAL_BOUNDARY_FAILED.value
    return value


def _safe_dispatch(result: SandboxDispatchResult) -> dict[str, object]:
    return {
        "error": _operator_token(result.error),
        "order_may_have_been_sent": result.order_may_have_been_sent,
        "order_was_sent": result.order_was_sent,
        "provider_status": _operator_token(result.provider_status),
        "retryable": result.retryable,
        "status": result.status,
        "terminal": result.terminal,
    }


def _safe_inspection(result: SandboxInspectionResult) -> dict[str, object]:
    return {
        "executed_lots": result.executed_lots,
        "provider_status": _operator_token(result.provider_status),
        "retryable": result.retryable,
        "status": result.status,
        "terminal": result.terminal,
    }


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    authority = RuntimeCashAuthorityManager(RuntimeCashAuthorityStore(args.runtime_dir))
    runtime: _Runtime | None = None
    try:
        if args.command == "status":
            _print(_safe(authority.status()))
            return 0
        if args.command == "cancel":
            _print(_safe(authority.cancel(transition_at=_timestamp())))
            return 0
        if args.command == "disarm":
            _print(_safe(authority.disarm(transition_at=_timestamp())))
            return 0

        require_provider = args.command != "arm"
        if args.command == "recover":
            require_provider = (
                authority.status().state
                is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
            )
        runtime = _open_runtime(
            args.runtime_dir,
            create_ledger=args.command == "prepare",
            require_provider=require_provider,
            allow_environment_secrets=args.allow_environment_secrets,
        )
        if args.command == "prepare":
            inputs = runtime.inputs()
            record, evidence = runtime.authority.prepare_runtime(
                confirmation=args.confirmation,
                **inputs,
            )
            payload = _safe(record)
            payload["context_sha256"] = evidence.context.sha256
            payload["context_status"] = evidence.context.status.value
        elif args.command == "confirm":
            inputs = runtime.inputs()
            record = runtime.authority.confirm_runtime(
                confirmation=args.confirmation,
                runtime_dir=runtime.root,
                **inputs,
            )
            payload = _safe(record)
        elif args.command == "activate":
            inputs = runtime.inputs()
            record = runtime.authority.activate_runtime(
                confirmation=args.confirmation,
                runtime_dir=runtime.root,
                **inputs,
            )
            payload = _safe(record)
        elif args.command == "arm":
            record = runtime.authority.arm(
                raw_account_id=runtime.raw_account,
                identity_key=runtime.identity_key,
                identity_key_id=runtime.identity_key_id,
                confirmation=args.confirmation,
                transition_at=_timestamp(),
            )
            payload = _safe(record)
        elif args.command == "rollback":
            inputs = runtime.inputs()
            record = runtime.authority.rollback_runtime(
                confirmation=args.confirmation,
                runtime_dir=runtime.root,
                **inputs,
            )
            payload = _safe(record)
        elif args.command == "sync":
            inputs = runtime.inputs()
            record, evidence = runtime.authority.sync_runtime(**inputs)
            payload = _safe(record)
            payload["context_sha256"] = evidence.context.sha256
            payload["context_status"] = evidence.context.status.value
        elif args.command == "inspect":
            with runtime.authority.store.locked():
                record = runtime.authority.store._load_unlocked(
                    allow_missing_legacy=False
                )
                adapter = runtime.adapter()
                if record.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING:
                    intent = runtime.authority._recovery_intent_locked(
                        record,
                        central_manager=runtime.central,
                        identity_key=runtime.identity_key,
                    )
                    inspection = adapter._inspect_order(intent)
                elif record.state is RuntimeCashAuthorityState.EXACT_CASH_ARMED:
                    d3_intent = runtime.authority._recovery_intent_locked(
                        record,
                        central_manager=runtime.central,
                        identity_key=runtime.identity_key,
                        allow_absent_d3=True,
                    )
                    inspection = SandboxInspectionResult(
                        status=("D3_PRE_SUBMIT" if d3_intent is not None else "IDLE")
                    )
                elif record.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED:
                    inspection = SandboxInspectionResult(status="IDLE")
                else:
                    inspection = adapter.inspect_blocking_order()
                payload = _safe(record)
                payload["central_blocking_count"] = sum(
                    item.status in {"IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}
                    for item in runtime.central.state().intents
                )
                payload["inspection"] = _safe_inspection(inspection)
        elif args.command == "recover":
            with runtime.authority.store.locked():
                record = runtime.authority.store._load_unlocked(
                    allow_missing_legacy=False
                )
                if record.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING:
                    blocker = runtime.authority._recovery_intent_locked(
                        record,
                        central_manager=runtime.central,
                        identity_key=runtime.identity_key,
                    )
                    fully_resolved = (
                        blocker.status == "FAILED"
                        and blocker.outcome == "SUBMISSION_REJECTED"
                    ) or blocker.status == "RECONCILED"
                    if not fully_resolved:
                        adapter = runtime.adapter()
                        inspection = adapter._inspect_order(blocker)
                        if inspection.status != "ORDER_OBSERVED":
                            raise CL7RuntimeError(
                                CL7RuntimeReason.RECOVERY_REQUIRED
                            )
                        if blocker.status == "IN_FLIGHT":
                            if inspection.broker_order_id is None:
                                raise CL7RuntimeError(
                                    CL7RuntimeReason.RECOVERY_REQUIRED
                                )
                            blocker = runtime.central.mark_submitted(
                                blocker.intent_id,
                                broker_order_id=inspection.broker_order_id,
                            )
                        if not inspection.terminal:
                            raise CL7RuntimeError(
                                CL7RuntimeReason.RECOVERY_REQUIRED
                            )
                        if blocker.status not in {"SUBMITTED", "UNCERTAIN"}:
                            raise CL7RuntimeError(
                                CL7RuntimeReason.RECOVERY_REQUIRED
                            )
                        runtime.central.mark_reconciled(
                            blocker.intent_id,
                            portfolio_repository=runtime.portfolio,
                            outcome=inspection.suggested_reconciliation_outcome,
                            executed_lots=inspection.executed_lots,
                            risk_runtime=adapter.risk_runtime,
                            execution_price_rub=inspection.execution_price_rub,
                            execution_price_source=inspection.execution_price_source,
                        )
                record, disposition = runtime.authority._recover_runtime_locked(
                    record,
                    central_manager=runtime.central,
                    raw_account_id=runtime.raw_account,
                    identity_key=runtime.identity_key,
                    identity_key_id=runtime.identity_key_id,
                    transition_at=_timestamp(),
                )
            payload = _safe(record)
            payload["recovery_disposition"] = disposition
        else:
            expected = args.expected_intent_id.strip()
            if not expected:
                raise CL7RuntimeError(
                    CL7RuntimeReason.CENTRAL_CHANGED,
                    stage="EXPECTED_INTENT_REQUIRED",
                )
            result = runtime.adapter().dispatch_next(
                runtime.portfolio,
                expected_intent_id=expected,
            )
            payload = _safe_dispatch(result)
        _print(payload)
        return 0
    except CL7RuntimeError as exc:
        _print(
            {
                "reason": exc.reason.value,
                "retryable": exc.retryable,
                "stage": exc.stage,
                "status": "BLOCKED",
            }
        )
        return 2
    except Q7SecretError as exc:
        _print(
            {
                "reason": exc.reason,
                "retryable": False,
                "stage": "PROTECTED_SECRET_RESOLUTION",
                "status": "BLOCKED",
            }
        )
        return 2
    except Exception:  # noqa: BLE001 - privacy-safe operator boundary
        _print(
            {
                "reason": CL7RuntimeReason.INTERNAL_BOUNDARY_FAILED.value,
                "retryable": False,
                "stage": "OPERATOR_BOUNDARY",
                "status": "BLOCKED",
            }
        )
        return 2
    finally:
        if runtime is not None:
            runtime.ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
