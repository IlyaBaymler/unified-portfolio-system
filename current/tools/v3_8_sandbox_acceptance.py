from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
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
from trading_robot.portfolio_adapters import BrokerPortfolioAdapter
from trading_robot.portfolio_manager import CanonicalPortfolioManager
from trading_robot.portfolio_model import SnapshotFreshness
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_read_service import load_portfolio_risk_metadata
from trading_robot.portfolio_risk_runtime import (
    portfolio_risk_runtime_from_risk_adapter,
)
from trading_robot.portfolio_risk_shadow import PortfolioRiskCandidateQuote
from trading_robot.risk_runtime import RiskRuntimeAdapter
from trading_robot.sandbox_execution_adapter import (
    SANDBOX_EXECUTION_CONFIRMATION,
    SandboxExecutionAdapter,
    SandboxExecutionPolicy,
)
from trading_robot.secret_provider import preferred_secret_provider
from trading_robot.state_persistence import read_json_verified
from trading_robot.tbank_sandbox import TBankSandboxClient, quotation_to_float

ARM_ENV_NAME = "ARM_V3_8_SANDBOX_EXECUTION"
M4_ARM_ENV_NAME = "ARM_V3_9_ENFORCED_EXECUTION"
TOKEN_KEY = "TBANK_SANDBOX_TOKEN"
RECONCILIATION_CONFIRMATION = "CONFIRM V3.8 CANONICAL RECONCILIATION"
PREPARE_CONFIRMATION = "PREPARE V3.8 SANDBOX INTENT"
M4_PREPARE_CONFIRMATION = "PREPARE V3.9 ENFORCED INTENT"
M4_REAUTHORIZE_CONFIRMATION = "REAUTHORIZE V3.9 ENFORCED INTENT"
M4_DISPATCH_CONFIRMATION = "ENABLE V3.9 ENFORCED EXECUTION"
M4_ACTIVATION_MANIFEST_NAME = "v3_9_enforced_runtime_manifest.json"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only v3.8/v3.9 Sandbox natural-signal preview, intent "
            "preparation, queue inspection, one-intent dispatch and canonical "
            "reconciliation."
        )
    )
    parser.add_argument(
        "action",
        choices=(
            "status",
            "preview-natural",
            "prepare-one",
            "reauthorize-one",
            "preflight-dispatch",
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

    repository = PortfolioRepository(portfolio_path)
    portfolio_state = repository.load(expected_account_id=account_id)

    if args.action == "status":
        state = CentralOrderStore(central_path).load(
            expected_account_id=account_id,
        )
        return _status_payload(state)

    if args.action == "preview-natural":
        central_manager = None
        state = CentralOrderStore(central_path).load(
            expected_account_id=account_id,
        )
        if state.intents or state.blocking_intent is not None:
            raise RuntimeError("Natural preview requires empty Central history.")
        if state.reserved_cash_kopecks:
            raise RuntimeError("Natural preview refuses Central reservations.")
    else:
        central_manager = CentralOrderManager(
            CentralOrderStore(central_path),
            account_id=account_id,
        )
        central_manager.recover_after_restart()
        portfolio_state = repository.load(expected_account_id=account_id)
        state = central_manager.state()

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
        if str(args.confirm or "").strip() != _prepare_confirmation(runtime_dir):
            raise RuntimeError(
                "prepare-one requires the exact intent preparation confirmation."
            )

    if args.action == "reauthorize-one":
        if not _is_m4_runtime(runtime_dir):
            raise RuntimeError("reauthorize-one is restricted to an active M4 runtime.")
        if state.blocking_intent is not None:
            raise RuntimeError(
                "Account-wide blocker must be reconciled before reauthorization."
            )
        selected = str(args.intent_id or "").strip()
        queue_head = state.queued[0] if state.queued else None
        if not selected or queue_head is None or selected != queue_head.intent_id:
            raise RuntimeError(
                "reauthorize-one requires --intent-id matching the queue head."
            )
        if str(args.confirm or "").strip() != M4_REAUTHORIZE_CONFIRMATION:
            raise RuntimeError(
                "reauthorize-one requires the exact v3.9 confirmation."
            )
        if any(
            str(environment.get(name) or "").strip().upper() == "YES"
            for name in (ARM_ENV_NAME, M4_ARM_ENV_NAME)
        ):
            raise RuntimeError(
                "Remove dispatch arming before reauthorizing an M4 intent."
            )
        _assert_runtime_ready_for_dispatch(
            runtime_dir,
            account_id=account_id,
            queue_head=queue_head,
            portfolio_state=portfolio_state,
            central_state=state,
        )

    if args.action in {
        "preview-natural",
        "prepare-one",
        "reauthorize-one",
        "preflight-dispatch",
        "dispatch-one",
        "reconcile",
    }:
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

    if args.action in {"preflight-dispatch", "dispatch-one"}:
        if state.blocking_intent is not None:
            raise RuntimeError(
                "Account-wide blocker must be reconciled before dispatch preflight."
            )
        selected = str(args.intent_id or "").strip()
        if not selected:
            raise RuntimeError("dispatch preflight requires --intent-id.")
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
        if args.action == "preflight-dispatch":
            return _preflight_dispatch(
                runtime_dir=runtime_dir,
                account_id=account_id,
                central_manager=central_manager,
                portfolio_repository=repository,
                expected_intent_id=selected,
            )
        arm_env_name = _dispatch_arm_env(runtime_dir)
        if str(environment.get(arm_env_name) or "").strip().upper() != "YES":
            raise RuntimeError(f"Set {arm_env_name}=YES for dispatch-one.")
        if str(args.confirm or "").strip() != _dispatch_confirmation(runtime_dir):
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
        if args.action == "preview-natural":
            return _preview_natural(
                runtime_dir=runtime_dir,
                account_id=account_id,
                client=client,
                portfolio_state=portfolio_state,
                central_state=state,
            )
        if args.action == "prepare-one":
            return _prepare_one(
                runtime_dir=runtime_dir,
                account_id=account_id,
                instrument_id=str(args.instrument_id).strip(),
                client=client,
                central_manager=central_manager,
                portfolio_repository=repository,
            )
        if args.action == "reauthorize-one":
            return _reauthorize_one(
                runtime_dir=runtime_dir,
                account_id=account_id,
                expected_intent_id=str(args.intent_id).strip(),
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
                post_fill_portfolio_risk = None
                post_fill_risk_runtime = risk_runtime
                if post_fill_risk_runtime is None:
                    post_fill_risk_runtime = RiskRuntimeAdapter.from_directory(
                        runtime_dir,
                        account_id=account_id,
                        mode="SANDBOX_EXECUTION",
                        auto_create_dry_run_profile=False,
                    )
                portfolio_risk_runtime = _portfolio_risk_runtime(
                    runtime_dir,
                    post_fill_risk_runtime,
                )
                if portfolio_risk_runtime is not None:
                    post_fill_portfolio_risk = (
                        portfolio_risk_runtime.recalculate_current(
                            central_manager,
                            repository,
                        ).to_dict()
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
                    "post_fill_portfolio_risk": post_fill_portfolio_risk,
                    "canonical_state_status": canonical_state.state_status,
                    "runtime_sync_warning": runtime_sync_warning,
                }
        else:
            policy = SandboxExecutionPolicy(
                account_id=account_id,
                enabled=True,
                confirmation=(
                    SANDBOX_EXECUTION_CONFIRMATION
                    if _is_m4_runtime(runtime_dir)
                    else args.confirm
                ),
            )
            risk_runtime = RiskRuntimeAdapter.from_directory(
                runtime_dir,
                account_id=account_id,
                mode="SANDBOX_EXECUTION",
                auto_create_dry_run_profile=False,
            )
            portfolio_risk_runtime = _portfolio_risk_runtime(
                runtime_dir,
                risk_runtime,
            )
            adapter_kwargs = {"risk_runtime": risk_runtime}
            if portfolio_risk_runtime is not None:
                adapter_kwargs["portfolio_risk_runtime"] = portfolio_risk_runtime
            result = SandboxExecutionAdapter(
                client,
                central_manager,
                policy,
                **adapter_kwargs,
            ).dispatch_next(
                repository,
                expected_intent_id=args.intent_id,
            )
    return {"action": args.action, **asdict(result)}


def _prepare_confirmation(runtime_dir: Path) -> str:
    if _is_m4_runtime(runtime_dir):
        return M4_PREPARE_CONFIRMATION
    return PREPARE_CONFIRMATION


def _is_m4_runtime(runtime_dir: Path) -> bool:
    return (runtime_dir / M4_ACTIVATION_MANIFEST_NAME).is_file()


def _dispatch_arm_env(runtime_dir: Path) -> str:
    return M4_ARM_ENV_NAME if _is_m4_runtime(runtime_dir) else ARM_ENV_NAME


def _dispatch_confirmation(runtime_dir: Path) -> str:
    if _is_m4_runtime(runtime_dir):
        return M4_DISPATCH_CONFIRMATION
    return SANDBOX_EXECUTION_CONFIRMATION


def _preflight_dispatch(
    *,
    runtime_dir: Path,
    account_id: str,
    central_manager: CentralOrderManager,
    portfolio_repository: PortfolioRepository,
    expected_intent_id: str,
) -> dict[str, Any]:
    risk_runtime = RiskRuntimeAdapter.from_directory(
        runtime_dir,
        account_id=account_id,
        mode="SANDBOX_EXECUTION",
        auto_create_dry_run_profile=False,
    )
    portfolio_risk_runtime = _portfolio_risk_runtime(runtime_dir, risk_runtime)
    if portfolio_risk_runtime is None:
        raise RuntimeError(
            "M4 dispatch preflight requires authoritative Portfolio Risk runtime."
        )
    initial = central_manager.state()
    initial_queue_head = initial.queued[0] if initial.queued else None
    if (
        initial_queue_head is None
        or initial_queue_head.intent_id != expected_intent_id
    ):
        raise RuntimeError("Dispatch queue head changed before preflight.")
    with (
        portfolio_repository.locked_snapshot(
            expected_account_id=account_id
        ) as portfolio,
        risk_runtime.dispatch_authorization_guard(
            expected_policy_hash=initial_queue_head.authorization.risk_policy_hash,
            expected_state_guard_hash=(
                initial_queue_head.authorization.risk_state_guard_hash
            ),
            instrument_id=initial_queue_head.candidate.instrument_id,
        ),
    ):
        def validate(central: Any) -> tuple[Any, Any]:
            queue_head = central.queued[0] if central.queued else None
            if queue_head is None or queue_head.intent_id != expected_intent_id:
                raise RuntimeError(
                    "Dispatch queue head changed during preflight revalidation."
                )
            decision = portfolio_risk_runtime.validate_dispatch(
                portfolio=portfolio,
                central_orders=central,
                intent=queue_head,
                evaluated_at=datetime.now(timezone.utc),
            )
            return queue_head, decision

        queue_head, decision = central_manager.inspect_locked(validate)
    return {
        "action": "preflight-dispatch",
        "status": "PASS",
        "ticker": queue_head.candidate.ticker,
        "direction": queue_head.candidate.direction,
        "requested_lots": queue_head.candidate.requested_lots,
        "approved_target_lots": decision.approved_target_lots,
        "portfolio_risk_status": decision.status,
        "proof_reproduced": True,
        "current_policy_revalidated": True,
        "current_risk_state_revalidated": True,
        "current_canonical_revalidated": True,
        "current_queue_revalidated": True,
        "dispatch_armed": False,
        "execution_authorized": False,
        "broker_api_called": False,
        "broker_order_submit_called": False,
        "material_writes_performed": False,
        "lock_metadata_touched": True,
        "next_arm_env": M4_ARM_ENV_NAME,
        "next_confirmation": M4_DISPATCH_CONFIRMATION,
    }


def _reauthorize_one(
    *,
    runtime_dir: Path,
    account_id: str,
    expected_intent_id: str,
    client: Any,
    central_manager: CentralOrderManager,
    portfolio_repository: PortfolioRepository,
) -> dict[str, Any]:
    before = central_manager.state()
    previous = before.queued[0] if before.queued else None
    if previous is None or previous.intent_id != expected_intent_id:
        raise RuntimeError("Queue head changed before v3.9 reauthorization.")
    prepared = _prepare_one(
        runtime_dir=runtime_dir,
        account_id=account_id,
        instrument_id=previous.candidate.instrument_id,
        client=client,
        central_manager=central_manager,
        portfolio_repository=portfolio_repository,
    )
    after = central_manager.state()
    status = str(prepared.get("status") or "").strip().upper()
    current_intent_id = str(prepared.get("intent_id") or "").strip() or None
    proof_refreshed = status in {"REAUTHORIZED", "REPLACED"}
    current = next(
        (
            item
            for item in after.queued
            if item.intent_id == current_intent_id
        ),
        None,
    )
    if proof_refreshed:
        if current is None:
            raise RuntimeError("Reauthorized intent is not the current Central queue.")
        proof = current.authorization.portfolio_risk
        if proof is None or not proof.finalized:
            raise RuntimeError("Reauthorization did not persist a finalized proof.")
        if status == "REAUTHORIZED" and current.intent_id != expected_intent_id:
            raise RuntimeError("Reauthorization unexpectedly changed intent identity.")
        if status == "REPLACED" and (
            prepared.get("cancelled_intent_id") != expected_intent_id
            or current.intent_id == expected_intent_id
        ):
            raise RuntimeError("Reprepare did not atomically replace the stale intent.")
    else:
        proof = None
    return {
        **prepared,
        "action": "reauthorize-one",
        "previous_intent_id": expected_intent_id,
        "current_intent_id": current.intent_id if current is not None else None,
        "central_revision": after.revision,
        "reserved_cash_kopecks": after.reserved_cash_kopecks,
        "proof_refreshed": proof_refreshed,
        "candidate_price_at": (
            proof.candidate_price_at if proof is not None else None
        ),
        "candidate_price_source": (
            proof.candidate_price_source if proof is not None else None
        ),
        "dispatch_armed": False,
        "execution_authorized": False,
        "broker_mutation_authorized": False,
        "broker_order_submit_called": False,
    }


def _preview_natural(
    *,
    runtime_dir: Path,
    account_id: str,
    client: Any,
    portfolio_state: Any,
    central_state: Any,
) -> dict[str, Any]:
    if portfolio_state.freshness is not SnapshotFreshness.FRESH:
        raise RuntimeError("Natural preview requires a FRESH canonical portfolio.")
    if portfolio_state.blocking:
        raise RuntimeError("Natural preview refuses a blocking canonical portfolio.")
    if central_state.intents or central_state.reserved_cash_kopecks:
        raise RuntimeError("Natural preview requires pristine Central state.")

    profiles = MultiInstrumentProfileStore(
        runtime_dir / "multi_instrument_profiles.json"
    ).load_mode("SANDBOX_EXECUTION")
    runtimes = InstrumentRuntimeStore(
        runtime_dir / "instrument_runtimes.json"
    ).load(expected_account_id=account_id)
    runtime_by_instrument = {item.config.instrument_id: item for item in runtimes}
    if not profiles or len(runtime_by_instrument) != len(profiles):
        raise RuntimeError("Natural preview profile/runtime scopes differ.")
    if set(runtime_by_instrument) != {item.instrument_id for item in profiles}:
        raise RuntimeError("Natural preview profile/runtime identities differ.")
    if any(item.status == "BLOCKED" for item in runtimes):
        raise RuntimeError("Natural preview refuses a BLOCKED runtime.")
    if any(item.pending_order_ids for item in runtimes):
        raise RuntimeError("Natural preview refuses pending runtime orders.")

    risk_runtime = RiskRuntimeAdapter.from_directory(
        runtime_dir,
        account_id=account_id,
        mode="SANDBOX_EXECUTION",
        auto_create_dry_run_profile=False,
    )
    portfolio_risk_runtime = _portfolio_risk_runtime(runtime_dir, risk_runtime)

    raw_portfolio = client.get_portfolio(account_id)
    raw_orders = client.get_orders(account_id)
    broker = BrokerPortfolioAdapter.from_api_portfolio(
        raw_portfolio,
        account_id=account_id,
        broker_orders=raw_orders,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
    )
    pending_orders = tuple(
        order for position in broker.positions for order in position.pending_orders
    )
    if any(order.active or order.uncertain for order in pending_orders):
        raise RuntimeError("Natural preview refuses active or uncertain broker orders.")
    expected_ids = set(runtime_by_instrument)
    unexpected_positions = {
        item.instrument_id
        for item in broker.positions
        if item.instrument_id not in expected_ids and int(item.actual_lots) != 0
    }
    if unexpected_positions:
        raise RuntimeError("Provider portfolio contains an unconfigured position.")
    broker_by_instrument = {item.instrument_id: item for item in broker.positions}

    raw_prices: list[Mapping[str, Any]] = []
    if portfolio_risk_runtime is not None:
        raw_prices = [
            item
            for item in client.get_last_prices(sorted(expected_ids))
            if isinstance(item, Mapping)
        ]
    prices_by_instrument: dict[str, list[Mapping[str, Any]]] = {}
    for raw in raw_prices:
        instrument_id = str(
            raw.get("instrumentUid") or raw.get("instrumentId") or ""
        ).strip()
        if instrument_id:
            prices_by_instrument.setdefault(instrument_id, []).append(raw)

    now = datetime.now(timezone.utc)
    proposals: list[dict[str, Any]] = []
    for profile in sorted(profiles, key=lambda item: item.ticker):
        runtime = runtime_by_instrument[profile.instrument_id]
        if runtime.config.to_dict() != profile.to_runtime_config(account_id).to_dict():
            raise RuntimeError(f"Runtime configuration drift: {profile.ticker}.")
        instrument = client.find_instrument(profile.ticker, profile.class_code)
        if str(client.instrument_id(instrument)).strip() != profile.instrument_id:
            raise RuntimeError(f"Provider UID changed for {profile.ticker}.")
        try:
            lot_size = int(instrument.get("lot") or 0)
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Provider lot size is invalid: {profile.ticker}."
            ) from exc
        if lot_size < 1:
            raise RuntimeError(f"Provider lot size is invalid: {profile.ticker}.")
        if portfolio_risk_runtime is not None:
            metadata = portfolio_risk_runtime.instrument_metadata.get(
                profile.instrument_id
            )
            if metadata is None or metadata.lot_size != lot_size:
                raise RuntimeError(
                    f"Portfolio Risk lot metadata changed: {profile.ticker}."
                )

        provider_position = broker_by_instrument.get(profile.instrument_id)
        provider_lots = (
            int(provider_position.actual_lots) if provider_position is not None else 0
        )
        canonical_position = portfolio_state.position(profile.instrument_id)
        canonical_lots = (
            int(canonical_position.actual_lots)
            if canonical_position is not None
            else 0
        )
        if provider_lots != canonical_lots or runtime.current_lots != canonical_lots:
            raise RuntimeError(
                f"Provider/canonical/runtime lots differ: {profile.ticker}."
            )

        complete = StrategyCandleLoader(client).load(runtime, profile, now=now)
        proposal = build_strategy_proposal(
            runtime,
            profile,
            complete,
            now=now,
        )
        primary = proposal.decisions[proposal.primary_strategy]
        quote = None
        if portfolio_risk_runtime is not None:
            matches = prices_by_instrument.get(profile.instrument_id, [])
            if len(matches) != 1:
                raise RuntimeError(
                    f"Authoritative preview requires one last price: {profile.ticker}."
                )
            raw_quote = matches[0]
            quote = PortfolioRiskCandidateQuote(
                unit_price_rub=quotation_to_float(raw_quote.get("price")),
                price_at=str(raw_quote.get("time") or ""),
                source="TBANK_LAST_PRICE_EXCHANGE",
            )
        proposals.append(
            {
                "ticker": proposal.ticker,
                "runtime_status": runtime.status,
                "candle_time": proposal.candle_time,
                "signal": int(primary.signal),
                "current_lots": canonical_lots,
                "requested_target_lots": int(proposal.primary_target_lots),
                "position_change_requested": (
                    int(proposal.primary_target_lots) != canonical_lots
                ),
                "candidate_price_at": (
                    quote.price_at.isoformat() if quote is not None else None
                ),
                "candidate_price_source": (
                    quote.source if quote is not None else None
                ),
                "execution_authorized": False,
                "persisted": False,
            }
        )

    return {
        "action": "preview-natural",
        "status": (
            "SIGNAL"
            if any(item["position_change_requested"] for item in proposals)
            else "NO_SIGNAL"
        ),
        "account_fingerprint": _fingerprint(account_id),
        "natural_proposals": proposals,
        "central_mutation_authorized": False,
        "intent_preparation_authorized": False,
        "execution_authorized": False,
        "broker_order_submit_called": False,
        "writes_performed": False,
    }


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
    portfolio_risk_runtime = _portfolio_risk_runtime(runtime_dir, risk_runtime)
    candidate_quote = None
    coordination_now = now
    if portfolio_risk_runtime is not None:
        matches = [
            item
            for item in client.get_last_prices([instrument_id])
            if str(item.get("instrumentUid") or item.get("instrumentId") or "").strip()
            == instrument_id
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "Authoritative Portfolio Risk requires exactly one last price."
            )
        raw_quote = matches[0]
        candidate_quote = PortfolioRiskCandidateQuote(
            unit_price_rub=quotation_to_float(raw_quote.get("price")),
            price_at=str(raw_quote.get("time") or ""),
            source="TBANK_LAST_PRICE_EXCHANGE",
        )
        coordination_now = datetime.now(timezone.utc)
    result = CentralOrderCoordinator(
        central_manager,
        portfolio_repository,
        risk_runtime,
        portfolio_risk_runtime=portfolio_risk_runtime,
    ).coordinate(
        proposal,
        runtime,
        profile,
        candles=complete,
        lot_size=lot_size,
        now=coordination_now,
        portfolio_risk_candidate_quote=candidate_quote,
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


def _portfolio_risk_runtime(runtime_dir: Path, risk_runtime: Any):
    metadata_path = runtime_dir / "portfolio_risk_metadata.json"
    metadata = (
        load_portfolio_risk_metadata(metadata_path)
        if metadata_path.is_file()
        else None
    )
    runtime = portfolio_risk_runtime_from_risk_adapter(
        risk_runtime,
        instrument_metadata=metadata,
    )
    if runtime is None:
        return None
    manifest_path = runtime_dir / M4_ACTIVATION_MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError(
            "ENFORCED Portfolio Risk requires a checksummed M4 activation manifest."
        )
    manifest = read_json_verified(manifest_path, supported_versions={1})
    if Path(str(manifest.get("runtime_dir") or "")).resolve() != runtime_dir.resolve():
        raise RuntimeError("M4 activation manifest belongs to another runtime.")
    if manifest.get("activation_status") != "ACTIVE":
        raise RuntimeError("M4 activation manifest is not ACTIVE.")
    if manifest.get("account_fingerprint") != _fingerprint(runtime.account_id):
        raise RuntimeError("M4 activation manifest account scope mismatch.")
    loaded = runtime.profile_store.require_portfolio_policy("SANDBOX_EXECUTION")
    if manifest.get("policy_hash") != loaded["policy_hash"]:
        raise RuntimeError("M4 activation manifest policy hash mismatch.")
    if manifest.get("portfolio_policy_mode") != "ENFORCED":
        raise RuntimeError("M4 activation manifest mode is not ENFORCED.")
    return runtime


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
