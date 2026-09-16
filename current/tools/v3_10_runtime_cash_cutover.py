"""Private operator surface for the CL7 runtime cash-authority state machine."""

from __future__ import annotations

import argparse
import hashlib
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

from trading_robot.broker_read_adapters import TBANK_OPERATION_CODEC, BrokerReadReason
from trading_robot.cash_availability import AvailabilityReason, AvailabilityStatus
from trading_robot.cash_ledger_opening_reconciliation import (
    CL4_OPENING_CODEC,
    CL4Reason,
)
from trading_robot.cash_ledger_persistence import CashLedgerStore
from trading_robot.central_order_manager import (
    CentralOrderManager,
    CentralOrderStore,
)
from trading_robot.gui_runtime_controller import CL4MoneyNormalizingTransport
from trading_robot.portfolio_manager import CanonicalPortfolioManager
from trading_robot.portfolio_model import PortfolioState
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_runtime import PortfolioRiskRuntime
from trading_robot.reporting_risk_cash_context import RiskCashContextReason
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


def _provider(token: str) -> CL4MoneyNormalizingTransport:
    return CL4MoneyNormalizingTransport(TBankSandboxClient(token=token, max_retries=0))


def _safe_cl3_provider_observability(value: object) -> dict[str, object]:
    if type(value) is not dict:
        return {}
    service = value.get("service")
    method = value.get("method")
    if not (
        type(service) is str
        and service == "SandboxService"
        and type(method) is str
        and method == "GetSandboxOperationsByCursor"
    ):
        return {}
    result: dict[str, object] = {
        "service": service,
        "method": method,
    }
    status_code = value.get("status_code")
    if "status_code" in value:
        if status_code is None or (
            type(status_code) is int and 100 <= status_code <= 599
        ):
            result["status_code"] = status_code
    error_class = value.get("error_class")
    if (
        type(error_class) is str
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", error_class) is not None
    ):
        result["error_class"] = error_class
    transient = value.get("transient")
    if type(transient) is bool:
        result["transient"] = transient
    attempt_count = value.get("attempt_count")
    if type(attempt_count) is int and 1 <= attempt_count <= 1000:
        result["attempt_count"] = attempt_count
    if "tracking_id" in value:
        tracking_id = value.get("tracking_id")
        if tracking_id is None:
            result["tracking_id_sha256"] = None
        elif type(tracking_id) is str and 0 < len(tracking_id) <= 4096:
            try:
                tracking_id_bytes = tracking_id.encode("utf-8")
            except UnicodeEncodeError:
                pass
            else:
                result["tracking_id_sha256"] = hashlib.sha256(
                    tracking_id_bytes
                ).hexdigest()
    provider_error_code = value.get("provider_error_code")
    provider_error_category = value.get("provider_error_category")
    categories_by_status = {
        400: "REQUEST_REJECTED",
        401: "AUTHENTICATION_REJECTED",
        403: "AUTHORIZATION_REJECTED",
        404: "RESOURCE_NOT_FOUND",
        408: "REQUEST_TIMEOUT",
        409: "REQUEST_CONFLICT",
        429: "RATE_LIMITED",
    }
    expected_category = None
    if type(status_code) is int and 100 <= status_code <= 599:
        expected_category = (
            "SERVER_REJECTED"
            if status_code >= 500
            else categories_by_status.get(status_code, "HTTP_REJECTED")
        )
    if (
        type(provider_error_code) is str
        and re.fullmatch(r"(?:[0-9]{1,10}|HTTP_[1-5][0-9]{2})", provider_error_code)
        is not None
        and type(provider_error_category) is str
        and provider_error_category == expected_category
        and (
            not provider_error_code.startswith("HTTP_")
            or provider_error_code == f"HTTP_{status_code}"
        )
    ):
        result["provider_error_code"] = provider_error_code
        result["provider_error_category"] = provider_error_category
    request_from = value.get("request_from_inclusive")
    request_to = value.get("request_to_exclusive")
    timestamp_pattern = re.compile(
        r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
        r"([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{9})Z",
        re.ASCII,
    )
    request_from_match = (
        timestamp_pattern.fullmatch(request_from) if type(request_from) is str else None
    )
    request_to_match = (
        timestamp_pattern.fullmatch(request_to) if type(request_to) is str else None
    )
    if (
        type(request_from) is str
        and type(request_to) is str
        and request_from_match is not None
        and request_to_match is not None
        and request_from < request_to
    ):
        try:
            datetime(*map(int, request_from_match.groups()[:6]), tzinfo=timezone.utc)
            datetime(*map(int, request_to_match.groups()[:6]), tzinfo=timezone.utc)
        except ValueError:
            pass
        else:
            result["request_from_inclusive"] = request_from
            result["request_to_exclusive"] = request_to
    return result


def _blocked_payload(
    exc: CL7RuntimeError,
    *,
    provider_meta: object = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "reason": exc.reason.value,
        "retryable": exc.retryable,
        "stage": exc.stage,
        "status": "BLOCKED",
    }
    if (
        exc.reason is CL7RuntimeReason.OPENING_INVALID
        and exc.stage == "CL4_OPENING"
        and exc.dependency_reason in {reason.value for reason in CL4Reason}
    ):
        payload["dependency_reason"] = exc.dependency_reason
    if (
        exc.reason is CL7RuntimeReason.BROKER_READ_FAILED
        and exc.stage == "CL3_SYNC"
        and exc.dependency_reason in {reason.value for reason in BrokerReadReason}
    ):
        payload["dependency_reason"] = exc.dependency_reason
        observability = _safe_cl3_provider_observability(provider_meta)
        if observability:
            payload["provider_observability"] = observability
    if (
        exc.reason is CL7RuntimeReason.CONTEXT_BLOCKED
        and exc.stage == "CL6_CONTEXT"
        and exc.dependency_reason
        in {
            reason.value
            for reason in RiskCashContextReason
            if reason is not RiskCashContextReason.READY
        }
    ):
        payload["dependency_reason"] = exc.dependency_reason
        if (
            exc.dependency_reason
            == RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value
        ):
            blocked_reasons = {
                AvailabilityReason.CL4_NOT_READY.value,
                AvailabilityReason.BROKER_PROOF_STALE.value,
                AvailabilityReason.CENTRAL_PROJECTION_STALE.value,
                AvailabilityReason.MIXED_EVIDENCE_SNAPSHOT.value,
                AvailabilityReason.BROKER_VIEW_MISMATCH.value,
                AvailabilityReason.FOREIGN_CASH_PRESENT.value,
                AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS.value,
            }
            allowed_pairs = {
                (AvailabilityStatus.BLOCKED.value, reason) for reason in blocked_reasons
            }
            allowed_pairs.add(
                (
                    AvailabilityStatus.MANUAL_REVIEW_REQUIRED.value,
                    AvailabilityReason.CENTRAL_PROVIDER_OVERLAP_UNKNOWN.value,
                )
            )
            pair = (exc.availability_status, exc.availability_reason)
            if type(pair[0]) is str and type(pair[1]) is str and pair in allowed_pairs:
                payload["availability_status"] = pair[0]
                payload["availability_reason"] = pair[1]
    return payload


@dataclass(slots=True)
class _Runtime:
    root: Path
    authority: RuntimeCashAuthorityManager
    ledger: CashLedgerStore
    portfolio: PortfolioRepository
    profiles: RiskProfileStore
    risk_state: RiskStateStore
    central: CentralOrderManager
    provider: CL4MoneyNormalizingTransport | None
    portfolio_manager: CanonicalPortfolioManager | None
    raw_account: str
    identity_key: bytes
    identity_key_id: str

    def inputs(self) -> dict[str, Any]:
        if self.provider is None or self.portfolio_manager is None:
            raise CL7RuntimeError(
                CL7RuntimeReason.BROKER_READ_FAILED,
                stage="PROVIDER_CREDENTIALS",
            )
        try:
            published = self.portfolio_manager.refresh(record_event=False)
            read_back = self.portfolio.load(expected_account_id=self.raw_account)
        except Exception:
            raise CL7RuntimeError(
                CL7RuntimeReason.CONTEXT_BLOCKED,
                RiskCashContextReason.PORTFOLIO_NOT_READY.value,
                stage="CL6_CONTEXT",
            ) from None
        if (
            type(published) is not PortfolioState
            or type(read_back) is not PortfolioState
            or published.to_dict() != read_back.to_dict()
        ):
            raise CL7RuntimeError(
                CL7RuntimeReason.CONTEXT_BLOCKED,
                RiskCashContextReason.PORTFOLIO_NOT_READY.value,
                stage="CL6_CONTEXT",
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
    provider = _provider(resolved.token) if require_provider else None
    portfolio_manager = (
        CanonicalPortfolioManager(
            provider,
            raw_account,
            robot_state_file=selected / "robot_state.json",
            portfolio_state_file=selected / "portfolio_state.json",
            journal_file=selected / "trading_events.db",
        )
        if provider is not None
        else None
    )
    return _Runtime(
        root=selected,
        authority=authority,
        ledger=ledger,
        portfolio=(
            portfolio_manager.repository
            if portfolio_manager is not None
            else PortfolioRepository(selected / "portfolio_state.json")
        ),
        profiles=profiles,
        risk_state=risk_state,
        central=central,
        provider=provider,
        portfolio_manager=portfolio_manager,
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
                if (
                    record.state
                    is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
                ):
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
                if (
                    record.state
                    is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING
                ):
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
                            raise CL7RuntimeError(CL7RuntimeReason.RECOVERY_REQUIRED)
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
                            raise CL7RuntimeError(CL7RuntimeReason.RECOVERY_REQUIRED)
                        if blocker.status not in {"SUBMITTED", "UNCERTAIN"}:
                            raise CL7RuntimeError(CL7RuntimeReason.RECOVERY_REQUIRED)
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
        provider_meta: object = None
        if runtime is not None and runtime.provider is not None:
            try:
                provider_meta = runtime.provider.last_response_meta
            except Exception:  # noqa: BLE001 - privacy-safe observability boundary
                provider_meta = None
        _print(_blocked_payload(exc, provider_meta=provider_meta))
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
