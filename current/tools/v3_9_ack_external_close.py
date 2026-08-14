from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.portfolio_adapters import (
    BrokerPortfolioAdapter,
    BrokerPortfolioRecord,
    RuntimePortfolioAdapter,
)
from trading_robot.portfolio_manager import CanonicalPortfolioManager
from trading_robot.portfolio_model import (
    OwnershipStatus,
    PortfolioState,
    PortfolioTarget,
    PositionOrigin,
    ReconciliationStatus,
    SnapshotFreshness,
)
from trading_robot.portfolio_reconciler import ReconciliationContext
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_read_service import load_portfolio_risk_metadata
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.runtime_integrity import sha256_file
from trading_robot.secret_provider import preferred_secret_provider
from trading_robot.state_persistence import atomic_write_json, read_json_verified
from trading_robot.tbank_sandbox import TBankSandboxClient

APPLY_CONFIRMATION = "ACK EXTERNAL CLOSE LKOH 0"
CONFIG_MANIFEST_NAME = "v3_9_shadow_runtime_config_manifest.json"
MANIFEST_NAME = "v3_9_external_close_ack_manifest.json"
START_MANIFEST_NAME = "v3_9_shadow_runtime_start_manifest.json"
TOKEN_KEY = "TBANK_SANDBOX_TOKEN"
TICKER = "LKOH"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Acknowledge one verified external LKOH flat close in the isolated "
            "v3.9 SHADOW runtime. No broker mutation is available to this command."
        )
    )
    parser.add_argument("action", choices=("preview", "apply"))
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--confirm", default="")
    parser.add_argument("--connect-timeout", type=float, default=8.0)
    parser.add_argument("--read-timeout", type=float, default=25.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--ca-bundle")
    return parser.parse_args(argv)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _risk_pristine(state: Any) -> bool:
    identity = (
        state.daily_date,
        state.weekly_key,
        state.daily_start_equity_rub,
        state.weekly_start_equity_rub,
        state.high_watermark_equity_rub,
        state.last_equity_rub,
        state.last_cash_rub,
        state.last_snapshot_at,
        state.last_evaluated_at,
        state.last_execution_at,
        state.last_portfolio_risk_decision_id,
        state.last_portfolio_risk_input_hash,
        state.last_portfolio_risk_evaluated_at,
    )
    return (
        all(value is None for value in identity)
        and state.daily_turnover_rub == 0
        and state.daily_order_count == 0
        and not state.recorded_execution_ids
        and not state.kill_switch_active
        and not state.instrument_kill_switches
        and not state.risk_resync_required
    )


def _load_static(root: Path) -> dict[str, Any]:
    required = (
        "portfolio_state.json",
        "portfolio_state.json.sha256",
        "risk_profiles.json",
        "risk_state.json",
        "multi_instrument_profiles.json",
        "multi_instrument_profiles.json.sha256",
        "instrument_runtimes.json",
        "instrument_runtimes.json.sha256",
        "central_order_state.json",
        "central_order_state.json.sha256",
        "portfolio_risk_metadata.json",
        "portfolio_risk_metadata.json.sha256",
        CONFIG_MANIFEST_NAME,
        CONFIG_MANIFEST_NAME + ".sha256",
        "trading_events.db",
    )
    for name in required:
        if not (root / name).is_file():
            raise RuntimeError(f"v3.9 SHADOW prerequisite is missing: {name}")
    if (root / START_MANIFEST_NAME).exists():
        raise RuntimeError("External-close acknowledgement requires runtimes not started.")

    portfolio = PortfolioRepository(root / "portfolio_state.json").load()
    account_id = str(portfolio.account_id or "").strip()
    config = read_json_verified(root / CONFIG_MANIFEST_NAME, supported_versions={1})
    if Path(str(config.get("runtime_dir") or "")).resolve() != root:
        raise RuntimeError("Configure manifest belongs to another runtime directory.")
    if _fingerprint(account_id) != str(config.get("account_fingerprint") or ""):
        raise RuntimeError("Configure manifest account fingerprint mismatch.")
    existing_ack = None
    if (root / MANIFEST_NAME).exists():
        existing_ack = read_json_verified(root / MANIFEST_NAME, supported_versions={1})
        if Path(str(existing_ack.get("runtime_dir") or "")).resolve() != root:
            raise RuntimeError("ACK manifest belongs to another runtime directory.")
        if existing_ack.get("confirmation") != APPLY_CONFIRMATION:
            raise RuntimeError("ACK manifest confirmation mismatch.")
        if int(existing_ack.get("new_portfolio_revision", -1)) != portfolio.revision:
            raise RuntimeError("Canonical portfolio changed after external-close ACK.")

    profiles = MultiInstrumentProfileStore(
        root / "multi_instrument_profiles.json"
    ).load_mode("SANDBOX_EXECUTION")
    profile_by_ticker = {item.ticker: item for item in profiles}
    if TICKER not in profile_by_ticker or len(profile_by_ticker) != len(profiles):
        raise RuntimeError("Configured runtime has no unique LKOH profile.")
    runtimes = InstrumentRuntimeStore(root / "instrument_runtimes.json").load(
        expected_account_id=account_id
    )
    runtime_by_instrument = {item.config.instrument_id: item for item in runtimes}
    if set(runtime_by_instrument) != {item.instrument_id for item in profiles}:
        raise RuntimeError("Instrument profile/runtime scopes differ.")
    if any(item.status != "STOPPED" for item in runtimes):
        raise RuntimeError("ACK requires every InstrumentRuntime STOPPED.")
    if any(item.pending_order_ids for item in runtimes):
        raise RuntimeError("ACK refuses pending runtime orders.")
    metadata = load_portfolio_risk_metadata(root / "portfolio_risk_metadata.json")
    if set(metadata) != set(runtime_by_instrument):
        raise RuntimeError("Portfolio Risk metadata scope differs from runtimes.")

    central = CentralOrderStore(root / "central_order_state.json").load(
        expected_account_id=account_id
    )
    if central.intents or central.blocking_intent is not None:
        raise RuntimeError("ACK requires empty Central order history.")
    if central.reserved_cash_kopecks:
        raise RuntimeError("ACK refuses Central cash reservations.")
    risk = RiskProfileStore(root / "risk_profiles.json").require_portfolio_policy(
        "SANDBOX_EXECUTION"
    )
    if risk["portfolio_policy_status"] != "READY":
        raise RuntimeError("Portfolio Risk policy is not READY.")
    if risk["policy"].portfolio_policy_mode != "OBSERVE_ONLY":
        raise RuntimeError("Portfolio Risk policy must remain OBSERVE_ONLY.")
    if str(risk["policy_hash"]) != str(config.get("policy_hash") or ""):
        raise RuntimeError("Portfolio Risk policy changed after configure gate.")
    if not _risk_pristine(
        RiskStateStore(root / "risk_state.json").load_account(account_id)
    ):
        raise RuntimeError("ACK requires pristine Risk baselines and counters.")

    selected_profile = profile_by_ticker[TICKER]
    selected_runtime = runtime_by_instrument[selected_profile.instrument_id]
    position = portfolio.position(selected_profile.instrument_id)
    if position is None:
        raise RuntimeError("Canonical LKOH position is missing.")
    partial_ack = bool(
        existing_ack is None
        and int(portfolio.revision) == int(config.get("portfolio_revision", -1)) + 1
        and str(portfolio.last_transaction_id or "").startswith(
            f"v3.9-external-close:{account_id}:{selected_profile.instrument_id}:"
        )
        and position.actual_lots == 0
        and position.target_lots == 0
        and position.ownership is None
    )
    if (
        existing_ack is None
        and not partial_ack
        and int(config.get("portfolio_revision", -1)) != portfolio.revision
    ):
        raise RuntimeError("Canonical portfolio changed after configure gate.")
    if existing_ack is None and not partial_ack:
        if (
            position.actual_lots != 1
            or position.target_lots != 1
            or position.ownership is None
            or selected_runtime.current_lots != 1
        ):
            raise RuntimeError("ACK requires sealed attributed LKOH 1 state.")
        if EventJournal(root / "trading_events.db", read_only=True).count() != 0:
            raise RuntimeError("ACK requires the configured empty EventJournal.")
    elif existing_ack is not None:
        if (
            position.actual_lots != 0
            or position.target_lots != 0
            or position.ownership is not None
            or selected_runtime.current_lots != 0
        ):
            raise RuntimeError("ACK manifest no longer matches LKOH flat state.")
        if sha256_file(root / "risk_state.json") != str(
            existing_ack.get("risk_state_sha256") or ""
        ):
            raise RuntimeError("Risk state changed after external-close ACK.")
        if sha256_file(root / "central_order_state.json") != str(
            existing_ack.get("central_state_sha256") or ""
        ):
            raise RuntimeError("Central state changed after external-close ACK.")
        if existing_ack.get("broker_mutation_authorized") is not False:
            raise RuntimeError("ACK manifest broker-mutation boundary is unsafe.")
        if existing_ack.get("broker_order_submit_called") is not False:
            raise RuntimeError("ACK manifest reports a broker order submission.")
    else:
        rows = EventJournal(root / "trading_events.db", read_only=True).recent(
            limit=100,
            account_id=account_id,
        )
        if not any(
            row.get("event_type") == "CANONICAL_TRANSACTION_COMMITTED"
            for row in rows
        ):
            raise RuntimeError("Partial ACK has no committed canonical transaction.")
    return {
        "account_id": account_id,
        "config": config,
        "portfolio": portfolio,
        "profiles": tuple(profiles),
        "runtime_by_instrument": runtime_by_instrument,
        "selected_profile": selected_profile,
        "selected_runtime": selected_runtime,
        "metadata": metadata,
        "existing_ack": existing_ack,
        "partial_ack": partial_ack,
    }


def _provider_preflight(client: Any, loaded: dict[str, Any]) -> dict[str, Any]:
    account_id = loaded["account_id"]
    account_ids = {
        str(item.get("id") or item.get("accountId") or "").strip()
        for item in client.get_accounts()
        if isinstance(item, dict)
    }
    if account_id not in account_ids:
        raise RuntimeError("Configured Sandbox account is not open/available.")
    identities: dict[str, dict[str, Any]] = {}
    for profile in sorted(loaded["profiles"], key=lambda item: item.ticker):
        instrument = client.find_instrument(profile.ticker, profile.class_code)
        instrument_id = str(client.instrument_id(instrument)).strip()
        if instrument_id != profile.instrument_id:
            raise RuntimeError(f"Provider UID changed for {profile.ticker}.")
        try:
            lot_size = int(instrument.get("lot"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Provider lot size is invalid: {profile.ticker}.") from exc
        if lot_size != loaded["metadata"][profile.instrument_id].lot_size:
            raise RuntimeError(f"Provider lot size changed for {profile.ticker}.")
        identities[instrument_id] = {
            "instrument_id": instrument_id,
            "ticker": profile.ticker,
            "class_code": profile.class_code,
            "lot_size": lot_size,
        }
    snapshot_at = datetime.now(timezone.utc)
    raw_portfolio = client.get_portfolio(account_id)
    raw_orders = client.get_orders(account_id)
    broker = BrokerPortfolioAdapter.from_api_portfolio(
        raw_portfolio,
        account_id=account_id,
        broker_orders=raw_orders,
        snapshot_at=snapshot_at.isoformat(),
    )
    orders = tuple(
        order for position in broker.positions for order in position.pending_orders
    )
    if any(order.active or order.uncertain for order in orders):
        raise RuntimeError("Provider reports an active or uncertain broker order.")
    broker_by_instrument = {item.instrument_id: item for item in broker.positions}
    unexpected = {
        item.instrument_id
        for item in broker.positions
        if item.instrument_id not in identities and item.actual_lots != 0
    }
    if unexpected:
        raise RuntimeError("Provider portfolio contains an unconfigured position.")
    selected_id = loaded["selected_profile"].instrument_id
    for instrument_id, identity in identities.items():
        actual = (
            int(broker_by_instrument[instrument_id].actual_lots)
            if instrument_id in broker_by_instrument
            else 0
        )
        expected = (
            0
            if instrument_id == selected_id
            else loaded["runtime_by_instrument"][instrument_id].current_lots
        )
        if actual != expected:
            raise RuntimeError(
                f"Provider lots are not ACK-ready: {identity['ticker']} "
                f"provider={actual}, expected={expected}."
            )
        identity["provider_lots"] = actual
    return {
        "broker": broker,
        "snapshot_at": snapshot_at,
        "identities": tuple(identities.values()),
    }


def _clear_canonical_target(
    manager: CanonicalPortfolioManager,
    loaded: dict[str, Any],
    broker: BrokerPortfolioRecord,
) -> PortfolioState:
    instrument_id = loaded["selected_profile"].instrument_id
    current = manager.repository.load(expected_account_id=loaded["account_id"])
    if (
        current.position(instrument_id) is not None
        and current.position(instrument_id).actual_lots == 0
        and current.position(instrument_id).target_lots == 0
        and current.position(instrument_id).ownership is None
    ):
        return current
    now = datetime.now(timezone.utc).isoformat()

    def transform(state: PortfolioState) -> PortfolioState:
        candidate = manager.reconciler.reconcile(
            broker,
            RuntimePortfolioAdapter.from_portfolio_state(state),
            previous=state,
            context=ReconciliationContext(
                freshness=SnapshotFreshness.FRESH,
                expected_account_id=loaded["account_id"],
                generated_at=now,
            ),
        )
        positions = []
        found = False
        for item in candidate.positions:
            if item.instrument_id != instrument_id:
                positions.append(item)
                continue
            found = True
            if item.actual_lots != 0 or item.target_lots != 1 or item.ownership is None:
                raise RuntimeError("Canonical LKOH changed during acknowledgement.")
            positions.append(
                replace(
                    item,
                    target=PortfolioTarget(
                        instrument_id=instrument_id,
                        target_lots=0,
                        strategy_id=None,
                        config_hash=None,
                        candle_time=item.last_candle_time,
                    ),
                    ownership=None,
                    ownership_status=OwnershipStatus.FLAT,
                    origin=PositionOrigin.EXTERNAL,
                    pending_orders=(),
                    reconciliation=replace(
                        item.reconciliation,
                        status=ReconciliationStatus.MATCHED,
                        blocking=False,
                        reasons=("External flat close acknowledged by operator.",),
                        actual_lots=0,
                        target_lots=0,
                        pending_order_ids=(),
                        checked_at=now,
                    ),
                )
            )
        if not found:
            raise RuntimeError("Canonical LKOH disappeared during acknowledgement.")
        blocking = any(item.reconciliation.blocking for item in positions)
        warnings = tuple(
            item
            for item in candidate.warnings
            if not str(item).startswith(f"{instrument_id}:")
        )
        return replace(
            candidate,
            positions=tuple(positions),
            warnings=warnings,
            generated_at=now,
            blocking=blocking,
            state_status="BLOCKED" if blocking else "READY",
        )

    result = manager.transaction_coordinator.commit(
        "ACKNOWLEDGE_EXTERNAL_CLOSE",
        transform,
        transaction_id=(
            f"v3.9-external-close:{loaded['account_id']}:{instrument_id}:"
            f"{loaded['config']['portfolio_revision']}"
        ),
        account_id=loaded["account_id"],
        instrument_id=instrument_id,
        mode="SANDBOX_EXECUTION",
    )
    return result.state


def _record_once(journal: EventJournal, event: JournalEvent) -> None:
    existing = journal.recent(
        limit=50,
        account_id=event.account_id,
        event_type=event.event_type,
    )
    if any(row.get("instrument_id") == event.instrument_id for row in existing):
        return
    journal.record(event)


def run(
    args: argparse.Namespace,
    *,
    client_factory: Any = TBankSandboxClient,
    secret_provider_factory: Any = preferred_secret_provider,
) -> dict[str, Any]:
    root = Path(args.runtime_dir).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError("v3.9 SHADOW runtime directory does not exist.")
    if args.connect_timeout <= 0 or args.read_timeout <= 0:
        raise RuntimeError("Provider timeouts must be positive.")
    if not 0 <= args.max_retries <= 5:
        raise RuntimeError("--max-retries must be between 0 and 5.")
    loaded = _load_static(root)
    if loaded["existing_ack"] is not None:
        return {
            "action": args.action,
            "status": "ALREADY_ACKNOWLEDGED",
            "runtime_dir": str(root),
            "account_fingerprint": _fingerprint(loaded["account_id"]),
            "ticker": TICKER,
            "canonical_lots": 0,
            "runtime_lots": 0,
            "runtime_status": "STOPPED",
            "risk_baseline_status": "UNINITIALIZED",
            "broker_mutation_authorized": False,
            "broker_order_submit_called": False,
            "writes_performed": False,
        }
    selected = secret_provider_factory(root)
    if not bool(getattr(selected, "secure", False)):
        raise RuntimeError("v3.9 SHADOW requires a secure secret provider.")
    token = str(selected.get(TOKEN_KEY) or "").strip()
    if not token:
        raise RuntimeError("T-Invest Sandbox credential is unavailable.")
    with client_factory(
        token,
        connect_timeout_seconds=float(args.connect_timeout),
        read_timeout_seconds=float(args.read_timeout),
        max_retries=int(args.max_retries),
        ca_bundle_path=str(args.ca_bundle or "").strip() or None,
    ) as client:
        provider = _provider_preflight(client, loaded)
        result = {
            "action": args.action,
            "status": "PREVIEW" if args.action == "preview" else "ACKNOWLEDGED",
            "runtime_dir": str(root),
            "account_fingerprint": _fingerprint(loaded["account_id"]),
            "ticker": TICKER,
            "canonical_lots_before": 1,
            "provider_lots": 0,
            "canonical_lots_after": 0 if args.action == "apply" else 1,
            "runtime_lots_after": 0 if args.action == "apply" else 1,
            "runtime_status": "STOPPED",
            "risk_baseline_status": "UNINITIALIZED",
            "broker_order_count": 0,
            "broker_mutation_authorized": False,
            "broker_order_submit_called": False,
            "writes_performed": False,
        }
        if args.action == "preview":
            return result
        if str(args.confirm or "").strip() != APPLY_CONFIRMATION:
            raise RuntimeError("apply requires the exact external-close confirmation.")

        risk_hash_before = sha256_file(root / "risk_state.json")
        central_hash_before = sha256_file(root / "central_order_state.json")
        manager = CanonicalPortfolioManager(
            client,
            loaded["account_id"],
            robot_state_file=root / "robot_state.json",
            portfolio_state_file=root / "portfolio_state.json",
            journal_file=root / "trading_events.db",
        )
        acknowledged = _clear_canonical_target(manager, loaded, provider["broker"])
        if acknowledged.blocking:
            raise RuntimeError("Canonical portfolio remains blocking after ACK.")

        selected_id = loaded["selected_profile"].instrument_id
        updated_runtimes = tuple(
            runtime.with_execution_state(current_lots=0, pending_order_ids=())
            if runtime.config.instrument_id == selected_id
            else runtime
            for runtime in loaded["runtime_by_instrument"].values()
        )
        InstrumentRuntimeStore(root / "instrument_runtimes.json").save(
            updated_runtimes
        )
        journal = EventJournal(root / "trading_events.db")
        common = {
            "confirmation": APPLY_CONFIRMATION,
            "old_target_lots": 1,
            "new_target_lots": 0,
            "broker_actual_lots": 0,
            "canonical_revision": acknowledged.revision,
            "risk_state_changed": False,
            "execution_history_changed": False,
            "source_history_intentionally_isolated": True,
        }
        for event_type in ("PORTFOLIO_TARGET_CLEARED", "EXTERNAL_CLOSE_ACKNOWLEDGED"):
            _record_once(
                journal,
                JournalEvent(
                    category="portfolio",
                    event_type=event_type,
                    severity="WARNING",
                    account_id=loaded["account_id"],
                    instrument_id=selected_id,
                    ticker=TICKER,
                    mode="SANDBOX_EXECUTION",
                    status="completed",
                    payload=common,
                ),
            )
        risk_hash_after = sha256_file(root / "risk_state.json")
        central_hash_after = sha256_file(root / "central_order_state.json")
        if risk_hash_after != risk_hash_before or central_hash_after != central_hash_before:
            raise RuntimeError("ACK changed Risk or Central state unexpectedly.")
        atomic_write_json(
            root / MANIFEST_NAME,
            {
                "version": 1,
                "acknowledged_at": datetime.now(timezone.utc).isoformat(),
                "runtime_dir": str(root),
                "account_fingerprint": _fingerprint(loaded["account_id"]),
                "confirmation": APPLY_CONFIRMATION,
                "ticker": TICKER,
                "instrument_id": selected_id,
                "old_portfolio_revision": int(
                    loaded["config"]["portfolio_revision"]
                ),
                "new_portfolio_revision": acknowledged.revision,
                "old_canonical_lots": 1,
                "new_canonical_lots": 0,
                "old_runtime_lots": 1,
                "new_runtime_lots": 0,
                "runtime_status": "STOPPED",
                "provider_lots": 0,
                "provider_order_count": 0,
                "provider_snapshot_at": provider["snapshot_at"].isoformat(),
                "canonical_freshness": acknowledged.freshness.value,
                "canonical_blocking": acknowledged.blocking,
                "risk_baseline_status": "UNINITIALIZED",
                "risk_state_sha256": risk_hash_after,
                "central_state_sha256": central_hash_after,
                "source_history_intentionally_isolated": True,
                "broker_mutation_authorized": False,
                "broker_order_submit_called": False,
                "start_gate_ready": True,
            },
            write_checksum=True,
        )
        result["canonical_revision"] = acknowledged.revision
        result["event_count"] = journal.count(account_id=loaded["account_id"])
        result["writes_performed"] = True
        return result


def main(argv: list[str] | None = None) -> int:
    try:
        result = run(parse_args(argv))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
