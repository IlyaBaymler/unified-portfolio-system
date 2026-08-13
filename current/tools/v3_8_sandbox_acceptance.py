from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trading_robot.central_order_coordinator import CentralOrderCoordinator
from trading_robot.central_order_manager import (
    CentralOrderManager,
    CentralOrderStore,
)
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.multi_instrument_strategy import (
    StrategyCandleLoader,
    build_strategy_proposal,
)
from trading_robot.portfolio_manager import CanonicalPortfolioManager
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk_runtime import RiskRuntimeAdapter
from trading_robot.sandbox_execution_adapter import (
    SANDBOX_EXECUTION_CONFIRMATION,
    SandboxExecutionAdapter,
    SandboxExecutionPolicy,
)
from trading_robot.secret_provider import preferred_secret_provider
from trading_robot.tbank_sandbox import TBankSandboxClient

ARM_ENV_NAME = "ARM_V3_8_SANDBOX_EXECUTION"
TOKEN_KEY = "TBANK_SANDBOX_TOKEN"
RECONCILIATION_CONFIRMATION = "CONFIRM V3.8 CANONICAL RECONCILIATION"
PREPARE_CONFIRMATION = "PREPARE V3.8 SANDBOX INTENT"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only v3.8 Sandbox intent preparation, queue inspection, "
            "one-intent dispatch and canonical reconciliation."
        )
    )
    parser.add_argument(
        "action",
        choices=(
            "status",
            "prepare-one",
            "inspect",
            "dispatch-one",
            "reconcile",
        ),
    )
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--intent-id")
    parser.add_argument("--instrument-id")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--connect-timeout", type=float, default=8.0)
    parser.add_argument("--read-timeout", type=float, default=25.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--ca-bundle")
    return parser.parse_args(argv)


def run(
    args: argparse.Namespace,
    *,
    environ: dict[str, str] | None = None,
    client_factory: Any = TBankSandboxClient,
) -> dict[str, Any]:
    environment = os.environ if environ is None else environ
    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    account_id = str(args.account_id or "").strip()
    if not account_id:
        raise RuntimeError("--account-id must not be empty.")
    if args.connect_timeout <= 0 or args.read_timeout <= 0:
        raise RuntimeError("Acceptance timeouts must be positive.")
    if not 0 <= args.max_retries <= 5:
        raise RuntimeError("--max-retries must be between 0 and 5.")

    central_path = runtime_dir / "central_order_state.json"
    portfolio_path = runtime_dir / "portfolio_state.json"
    for path in (central_path, portfolio_path):
        if not path.is_file() or not path.with_name(path.name + ".sha256").is_file():
            raise RuntimeError(
                f"Checksummed runtime prerequisite is missing: {path.name}"
            )

    central_manager = CentralOrderManager(
        CentralOrderStore(central_path),
        account_id=account_id,
    )
    central_manager.recover_after_restart()
    repository = PortfolioRepository(portfolio_path)
    portfolio_state = repository.load(expected_account_id=account_id)
    state = central_manager.state()

    if args.action == "status":
        return _status_payload(state)

    if args.action == "inspect" and state.blocking_intent is None:
        return {"action": "inspect", "status": "IDLE"}

    if args.action == "reconcile":
        blocker = state.blocking_intent
        if blocker is None:
            raise RuntimeError("There is no account-wide order to reconcile.")
        selected = str(args.intent_id or "").strip()
        if not selected or selected != blocker.intent_id:
            raise RuntimeError(
                "reconcile requires --intent-id matching the account blocker."
            )
        if str(args.confirm or "").strip() != RECONCILIATION_CONFIRMATION:
            raise RuntimeError(
                "reconcile requires the exact canonical reconciliation confirmation."
            )

    if args.action == "prepare-one":
        if state.blocking_intent is not None:
            raise RuntimeError(
                "Account-wide blocker must be reconciled before prepare-one."
            )
        if not str(args.instrument_id or "").strip():
            raise RuntimeError("prepare-one requires --instrument-id.")
        if str(args.confirm or "").strip() != PREPARE_CONFIRMATION:
            raise RuntimeError(
                "prepare-one requires the exact intent preparation confirmation."
            )

    if args.action in {"prepare-one", "dispatch-one", "reconcile"}:
        for name in (
            "multi_instrument_profiles.json",
            "instrument_runtimes.json",
        ):
            path = runtime_dir / name
            if not path.is_file() or not path.with_name(
                path.name + ".sha256"
            ).is_file():
                raise RuntimeError(
                    f"Checksummed v3.8 runtime prerequisite is missing: {name}"
                )

    if args.action == "dispatch-one":
        if state.blocking_intent is not None:
            raise RuntimeError(
                "Account-wide blocker must be reconciled before dispatch-one."
            )
        selected = str(args.intent_id or "").strip()
        if not selected:
            raise RuntimeError("dispatch-one requires --intent-id.")
        queue_head = state.queued[0] if state.queued else None
        if queue_head is None:
            raise RuntimeError("Central order queue is empty.")
        if selected != queue_head.intent_id:
            raise RuntimeError(
                "--intent-id is not the current account-wide queue head."
            )
        _assert_runtime_ready_for_dispatch(
            runtime_dir,
            account_id=account_id,
            queue_head=queue_head,
            portfolio_state=portfolio_state,
            central_state=state,
        )
        if str(environment.get(ARM_ENV_NAME) or "").strip().upper() != "YES":
            raise RuntimeError(f"Set {ARM_ENV_NAME}=YES for dispatch-one.")
        if str(args.confirm or "").strip() != SANDBOX_EXECUTION_CONFIRMATION:
            raise RuntimeError(
                "dispatch-one requires the exact Sandbox execution confirmation."
            )

    token = str(environment.get(TOKEN_KEY) or "").strip()
    if not token:
        token = str(
            preferred_secret_provider(runtime_dir).get(TOKEN_KEY) or ""
        ).strip()
    if not token:
        raise RuntimeError(
            "T-Invest Sandbox credential is unavailable in the configured "
            "secret provider."
        )
    ca_bundle = str(
        args.ca_bundle or environment.get("TBANK_CA_BUNDLE") or ""
    ).strip() or None
    with client_factory(
        token,
        connect_timeout_seconds=float(args.connect_timeout),
        read_timeout_seconds=float(args.read_timeout),
        max_retries=int(args.max_retries),
        ca_bundle_path=ca_bundle,
    ) as client:
        if args.action == "prepare-one":
            return _prepare_one(
                runtime_dir=runtime_dir,
                account_id=account_id,
                instrument_id=str(args.instrument_id).strip(),
                client=client,
                central_manager=central_manager,
                portfolio_repository=repository,
            )
        if args.action in {"inspect", "reconcile"}:
            policy = SandboxExecutionPolicy(account_id=account_id)
            inspection = SandboxExecutionAdapter(
                client,
                central_manager,
                policy,
            ).inspect_blocking_order()
            if args.action == "inspect":
                result = inspection
            else:
                if (
                    inspection.status != "ORDER_OBSERVED"
                    or not inspection.terminal
                    or inspection.suggested_reconciliation_outcome is None
                ):
                    raise RuntimeError(
                        "Provider order is not in a terminal reconciliable state."
                    )
                if (
                    inspection.executed_lots
                    and inspection.execution_price_rub is None
                ):
                    raise RuntimeError(
                        "Confirmed fill has no provider execution price."
                    )
                blocker = central_manager.state().blocking_intent
                if blocker is None or blocker.intent_id != args.intent_id:
                    raise RuntimeError(
                        "Account blocker changed before canonical reconciliation."
                    )
                expected_lots = blocker.candidate.current_lots + (
                    inspection.executed_lots
                    if blocker.candidate.direction == "BUY"
                    else -inspection.executed_lots
                )
                canonical = CanonicalPortfolioManager(
                    client,
                    account_id,
                    robot_state_file=runtime_dir / "robot_state.json",
                    portfolio_state_file=runtime_dir / "portfolio_state.json",
                    journal_file=runtime_dir / "trading_events.db",
                )
                canonical.stage_confirmed_target(
                    instrument_id=blocker.candidate.instrument_id,
                    target_lots=expected_lots,
                    strategy_id=blocker.candidate.strategy_id,
                    config_hash=blocker.candidate.strategy_profile_hash,
                    candle_interval=blocker.candidate.candle_interval,
                    ticker=blocker.candidate.ticker,
                    candle_time=blocker.candidate.candle_time,
                    transaction_id=(
                        f"v3.8-postfill-target:{blocker.intent_id}:"
                        f"{expected_lots}"
                    ),
                )
                canonical_state = canonical.refresh(record_event=True)
                risk_runtime = None
                if inspection.executed_lots:
                    risk_runtime = RiskRuntimeAdapter.from_directory(
                        runtime_dir,
                        account_id=account_id,
                        mode="SANDBOX_EXECUTION",
                        auto_create_dry_run_profile=False,
                    )
                reconciled = central_manager.mark_reconciled(
                    args.intent_id,
                    portfolio_repository=repository,
                    outcome=inspection.suggested_reconciliation_outcome,
                    executed_lots=inspection.executed_lots,
                    risk_runtime=risk_runtime,
                    execution_price_rub=inspection.execution_price_rub,
                    execution_price_source=inspection.execution_price_source,
                )
                runtime_sync_warning = None
                try:
                    _sync_runtime_execution_state(
                        runtime_dir,
                        account_id=account_id,
                        instrument_id=blocker.candidate.instrument_id,
                        actual_lots=expected_lots,
                        central_manager=central_manager,
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    runtime_sync_warning = str(exc)
                return {
                    "action": "reconcile",
                    "status": reconciled.status,
                    "intent_id": reconciled.intent_id,
                    "outcome": reconciled.outcome,
                    "executed_lots": reconciled.executed_lots,
                    "canonical_revision": (
                        reconciled.reconciled_portfolio_revision
                    ),
                    "canonical_snapshot_at": (
                        reconciled.reconciled_portfolio_snapshot_at
                    ),
                    "risk_execution_status": reconciled.risk_execution_status,
                    "risk_execution_id": reconciled.risk_execution_id,
                    "canonical_state_status": canonical_state.state_status,
                    "runtime_sync_warning": runtime_sync_warning,
                }
        else:
            policy = SandboxExecutionPolicy(
                account_id=account_id,
                enabled=True,
                confirmation=args.confirm,
            )
            risk_runtime = RiskRuntimeAdapter.from_directory(
                runtime_dir,
                account_id=account_id,
                mode="SANDBOX_EXECUTION",
                auto_create_dry_run_profile=False,
            )
            result = SandboxExecutionAdapter(
                client,
                central_manager,
                policy,
                risk_runtime=risk_runtime,
            ).dispatch_next(
                repository,
                expected_intent_id=args.intent_id,
            )
    return {"action": args.action, **asdict(result)}


def _prepare_one(
    *,
    runtime_dir: Path,
    account_id: str,
    instrument_id: str,
    client: Any,
    central_manager: CentralOrderManager,
    portfolio_repository: PortfolioRepository,
) -> dict[str, Any]:
    canonical = CanonicalPortfolioManager(
        client,
        account_id,
        robot_state_file=runtime_dir / "robot_state.json",
        portfolio_state_file=runtime_dir / "portfolio_state.json",
        journal_file=runtime_dir / "trading_events.db",
    )
    canonical_state = canonical.refresh(record_event=True)
    profiles = MultiInstrumentProfileStore(
        runtime_dir / "multi_instrument_profiles.json"
    ).load_mode("SANDBOX_EXECUTION")
    selected_profiles = [
        item for item in profiles if item.instrument_id == instrument_id
    ]
    if len(selected_profiles) != 1:
        raise RuntimeError(
            "prepare-one instrument must match exactly one SANDBOX_EXECUTION profile."
        )
    profile = selected_profiles[0]

    runtime_store = InstrumentRuntimeStore(
        runtime_dir / "instrument_runtimes.json"
    )
    runtimes = runtime_store.load(expected_account_id=account_id)
    selected_runtimes = [
        item for item in runtimes if item.config.instrument_id == instrument_id
    ]
    if len(selected_runtimes) != 1:
        raise RuntimeError(
            "prepare-one instrument must match exactly one InstrumentRuntime."
        )
    runtime = selected_runtimes[0]
    if runtime.status == "BLOCKED":
        raise RuntimeError("Selected InstrumentRuntime is BLOCKED.")
    if runtime.status == "STOPPED":
        runtime = runtime.start()
        runtimes = tuple(
            runtime if item.runtime_key == runtime.runtime_key else item
            for item in runtimes
        )
        runtime_store.save(runtimes)

    instrument = client.find_instrument(profile.ticker, profile.class_code)
    observed_id = client.instrument_id(instrument)
    if observed_id != instrument_id:
        raise RuntimeError(
            "Broker instrument identity does not match the v3.8 profile."
        )
    try:
        lot_size = int(instrument.get("lot") or 0)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("Broker instrument has no usable lot size.") from exc
    if lot_size < 1:
        raise RuntimeError("Broker instrument has no usable lot size.")

    now = datetime.now(timezone.utc)
    complete = StrategyCandleLoader(client).load(
        runtime,
        profile,
        now=now,
    )
    proposal = build_strategy_proposal(
        runtime,
        profile,
        complete,
        now=now,
    )
    risk_runtime = RiskRuntimeAdapter.from_directory(
        runtime_dir,
        account_id=account_id,
        mode="SANDBOX_EXECUTION",
        auto_create_dry_run_profile=False,
    )
    result = CentralOrderCoordinator(
        central_manager,
        portfolio_repository,
        risk_runtime,
    ).coordinate(
        proposal,
        runtime,
        profile,
        candles=complete,
        lot_size=lot_size,
        now=now,
    )
    position = canonical_state.position(instrument_id)
    runtime_sync_warning = None
    try:
        _sync_runtime_execution_state(
            runtime_dir,
            account_id=account_id,
            instrument_id=instrument_id,
            actual_lots=(int(position.actual_lots) if position is not None else 0),
            central_manager=central_manager,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        runtime_sync_warning = str(exc)
    return {
        "action": "prepare-one",
        **result.to_dict(),
        "runtime_sync_warning": runtime_sync_warning,
    }


def _sync_runtime_execution_state(
    runtime_dir: Path,
    *,
    account_id: str,
    instrument_id: str,
    actual_lots: int,
    central_manager: CentralOrderManager,
) -> None:
    store = InstrumentRuntimeStore(runtime_dir / "instrument_runtimes.json")
    runtimes = store.load(expected_account_id=account_id)
    central = central_manager.state()
    live_ids = tuple(
        item.intent_id
        for item in central.intents
        if item.candidate.instrument_id == instrument_id
        and item.status in {"QUEUED", "IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}
    )
    found = False
    updated = []
    for runtime in runtimes:
        if runtime.config.instrument_id != instrument_id:
            updated.append(runtime)
            continue
        found = True
        updated.append(
            runtime.with_execution_state(
                current_lots=int(actual_lots),
                pending_order_ids=live_ids,
            )
        )
    if not found:
        raise RuntimeError(
            "Cannot synchronize missing InstrumentRuntime after central transition."
        )
    store.save(tuple(updated))


def _assert_runtime_ready_for_dispatch(
    runtime_dir: Path,
    *,
    account_id: str,
    queue_head: Any,
    portfolio_state: Any,
    central_state: Any,
) -> None:
    runtimes = InstrumentRuntimeStore(
        runtime_dir / "instrument_runtimes.json"
    ).load(expected_account_id=account_id)
    matches = [
        item
        for item in runtimes
        if item.config.instrument_id == queue_head.candidate.instrument_id
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "Queue head does not match exactly one persisted InstrumentRuntime."
        )
    runtime = matches[0]
    if runtime.status != "ACTIVE":
        raise RuntimeError("Queue-head InstrumentRuntime is not ACTIVE.")
    if (
        runtime.runtime_key != queue_head.candidate.runtime_key
        or runtime.config.runtime_config_hash
        != queue_head.candidate.runtime_config_hash
        or runtime.config.strategy_config_hash
        != queue_head.candidate.strategy_profile_hash
    ):
        raise RuntimeError("Queue-head InstrumentRuntime identity mismatch.")
    position = portfolio_state.position(queue_head.candidate.instrument_id)
    actual_lots = int(position.actual_lots) if position is not None else 0
    if runtime.current_lots != actual_lots:
        raise RuntimeError(
            "Queue-head InstrumentRuntime lots do not match canonical portfolio."
        )
    expected_pending = tuple(
        item.intent_id
        for item in central_state.intents
        if item.candidate.instrument_id == queue_head.candidate.instrument_id
        and item.status in {"QUEUED", "IN_FLIGHT", "SUBMITTED", "UNCERTAIN"}
    )
    if runtime.pending_order_ids != expected_pending:
        raise RuntimeError(
            "Queue-head InstrumentRuntime pending IDs do not match central state."
        )


def _status_payload(state: Any) -> dict[str, Any]:
    blocker = state.blocking_intent
    return {
        "action": "status",
        "status": "ACCOUNT_BLOCKED" if blocker is not None else "READY",
        "account_fingerprint": _fingerprint(state.account_id),
        "state_revision": state.revision,
        "reserved_cash_kopecks": state.reserved_cash_kopecks,
        "blocking_intent": (
            None
            if blocker is None
            else {
                "intent_id": blocker.intent_id,
                "status": blocker.status,
                "ticker": blocker.candidate.ticker,
                "instrument_id": blocker.candidate.instrument_id,
            }
        ),
        "queued": [
            {
                "intent_id": item.intent_id,
                "sequence": item.queue_sequence,
                "ticker": item.candidate.ticker,
                "instrument_id": item.candidate.instrument_id,
                "direction": item.candidate.direction,
                "requested_lots": item.candidate.requested_lots,
            }
            for item in state.queued
        ],
    }


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(parse_args(argv))
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
