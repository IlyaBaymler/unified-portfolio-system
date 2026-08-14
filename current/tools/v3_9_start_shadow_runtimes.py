from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.portfolio_adapters import BrokerPortfolioAdapter
from trading_robot.portfolio_manager import CanonicalPortfolioManager
from trading_robot.portfolio_model import SnapshotFreshness
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_read_service import load_portfolio_risk_metadata
from trading_robot.risk import RiskEngine, RiskState, portfolio_risk_inputs
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.secret_provider import preferred_secret_provider
from trading_robot.state_persistence import atomic_write_json, read_json_verified
from trading_robot.tbank_sandbox import TBankSandboxClient

APPLY_CONFIRMATION = "START V3.9 SHADOW RUNTIMES"
CONFIG_MANIFEST_NAME = "v3_9_shadow_runtime_config_manifest.json"
ACK_MANIFEST_NAME = "v3_9_external_close_ack_manifest.json"
MANIFEST_NAME = "v3_9_shadow_runtime_start_manifest.json"
TOKEN_KEY = "TBANK_SANDBOX_TOKEN"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Start configured v3.9 SHADOW runtimes after read-only provider "
            "preflight. No Strategy proposal, Central intent or broker mutation "
            "is created by this command."
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


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("Provider snapshot timestamp is invalid.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError("Provider snapshot timestamp must be timezone-aware.")
    return parsed.astimezone(timezone.utc)


def _is_pristine_risk_state(state: RiskState) -> bool:
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

    portfolio = PortfolioRepository(root / "portfolio_state.json").load()
    account_id = str(portfolio.account_id or "").strip()
    if not account_id:
        raise RuntimeError("Canonical portfolio has no account scope.")
    config = read_json_verified(root / CONFIG_MANIFEST_NAME, supported_versions={1})
    if Path(str(config.get("runtime_dir") or "")).resolve() != root:
        raise RuntimeError("Configure manifest belongs to another runtime directory.")
    ack = None
    if (root / ACK_MANIFEST_NAME).exists():
        ack = read_json_verified(root / ACK_MANIFEST_NAME, supported_versions={1})
        if Path(str(ack.get("runtime_dir") or "")).resolve() != root:
            raise RuntimeError("External-close ACK belongs to another runtime directory.")
        if ack.get("confirmation") != "ACK EXTERNAL CLOSE LKOH 0":
            raise RuntimeError("External-close ACK confirmation mismatch.")
        if not ack.get("start_gate_ready"):
            raise RuntimeError("External-close ACK did not authorize a START retry.")
        if int(ack.get("new_portfolio_revision", -1)) != int(portfolio.revision):
            raise RuntimeError("Canonical portfolio changed after external-close ACK.")
    elif int(config.get("portfolio_revision", -1)) != int(portfolio.revision):
        raise RuntimeError("Canonical portfolio changed after configure gate.")
    if config.get("runtime_status") != "STOPPED":
        raise RuntimeError("Configure manifest did not seal STOPPED runtimes.")
    if config.get("risk_baseline_status") != "UNINITIALIZED":
        raise RuntimeError("Configure manifest did not seal pristine Risk baselines.")
    if config.get("provider_post_authorized") is not False:
        raise RuntimeError("Configure manifest provider authorization is unsafe.")

    profiles = MultiInstrumentProfileStore(
        root / "multi_instrument_profiles.json"
    ).load_mode("SANDBOX_EXECUTION")
    if not 2 <= len(profiles) <= 3:
        raise RuntimeError("v3.9 SHADOW requires two or three instrument profiles.")
    runtimes = InstrumentRuntimeStore(root / "instrument_runtimes.json").load(
        expected_account_id=account_id
    )
    if len(runtimes) != len(profiles):
        raise RuntimeError("Instrument profile/runtime counts differ.")
    runtime_by_instrument = {item.config.instrument_id: item for item in runtimes}
    if set(runtime_by_instrument) != {item.instrument_id for item in profiles}:
        raise RuntimeError("Instrument profile/runtime scopes differ.")
    if any(item.status != "STOPPED" for item in runtimes):
        raise RuntimeError("Start gate requires every InstrumentRuntime STOPPED.")
    if any(item.pending_order_ids for item in runtimes):
        raise RuntimeError("Start gate refuses pending runtime orders.")

    metadata = load_portfolio_risk_metadata(root / "portfolio_risk_metadata.json")
    if set(metadata) != set(runtime_by_instrument):
        raise RuntimeError("Portfolio Risk metadata scope differs from runtimes.")
    missing_currencies = sorted(
        profile.ticker
        for profile in profiles
        if metadata[profile.instrument_id].currency is None
    )
    if missing_currencies:
        raise RuntimeError(
            "Portfolio Risk currency metadata is unknown for: "
            + ", ".join(missing_currencies)
        )
    for profile in profiles:
        runtime = runtime_by_instrument[profile.instrument_id]
        if runtime.config.to_dict() != profile.to_runtime_config(account_id).to_dict():
            raise RuntimeError(f"Runtime configuration drift: {profile.ticker}.")
        position = portfolio.position(profile.instrument_id)
        canonical_lots = int(position.actual_lots) if position is not None else 0
        if runtime.current_lots != canonical_lots:
            raise RuntimeError(f"Runtime/canonical lots differ: {profile.ticker}.")

    central = CentralOrderStore(root / "central_order_state.json").load(
        expected_account_id=account_id
    )
    if central.intents or central.blocking_intent is not None:
        raise RuntimeError("Start gate requires empty Central order history.")
    if central.reserved_cash_kopecks:
        raise RuntimeError("Start gate refuses Central cash reservations.")

    risk = RiskProfileStore(root / "risk_profiles.json").require_portfolio_policy(
        "SANDBOX_EXECUTION"
    )
    if str(risk.get("account_scope") or "").strip() != account_id:
        raise RuntimeError("Portfolio Risk policy account scope mismatch.")
    if risk["portfolio_policy_status"] != "READY":
        raise RuntimeError("Portfolio Risk policy is not READY.")
    if risk["policy"].portfolio_policy_mode != "OBSERVE_ONLY":
        raise RuntimeError("Portfolio Risk policy must remain OBSERVE_ONLY.")
    if str(risk["policy_hash"]) != str(config.get("policy_hash") or ""):
        raise RuntimeError("Portfolio Risk policy changed after configure gate.")
    risk_state = RiskStateStore(root / "risk_state.json").load_account(account_id)
    if not _is_pristine_risk_state(risk_state):
        raise RuntimeError("Start gate requires pristine Risk baselines and counters.")
    journal = EventJournal(root / "trading_events.db", read_only=True)
    if ack is None:
        if journal.count() != 0:
            raise RuntimeError("Start gate requires the configured empty EventJournal.")
    else:
        rows = journal.recent(limit=10_000, account_id=account_id)
        allowed_types = {
            "CANONICAL_TRANSACTION_STARTED",
            "CANONICAL_TRANSACTION_COMMITTED",
            "LEGACY_SHADOW_UPDATED",
            "PORTFOLIO_STATE_PUBLISHED",
            "PORTFOLIO_TARGET_CLEARED",
            "EXTERNAL_CLOSE_ACKNOWLEDGED",
        }
        if not rows or any(str(row.get("event_type")) not in allowed_types for row in rows):
            raise RuntimeError("EventJournal contains activity outside the sealed ACK.")
        if not any(
            row.get("event_type") == "EXTERNAL_CLOSE_ACKNOWLEDGED"
            and row.get("instrument_id") == ack.get("instrument_id")
            for row in rows
        ):
            raise RuntimeError("EventJournal has no matching external-close ACK.")
    return {
        "account_id": account_id,
        "portfolio": portfolio,
        "profiles": tuple(profiles),
        "runtimes": tuple(runtimes),
        "runtime_by_instrument": runtime_by_instrument,
        "metadata": metadata,
        "policy": risk["policy"],
        "policy_hash": str(risk["policy_hash"]),
        "risk_state": risk_state,
        "external_close_ack": ack,
    }


def _provider_preflight(client: Any, loaded: dict[str, Any]) -> dict[str, Any]:
    account_id = loaded["account_id"]
    accounts = client.get_accounts()
    account_ids = {
        str(item.get("id") or item.get("accountId") or "").strip()
        for item in accounts
        if isinstance(item, dict)
    }
    if account_id not in account_ids:
        raise RuntimeError("Configured Sandbox account is not open/available.")

    identities: list[dict[str, Any]] = []
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
        identities.append(
            {
                "instrument_id": instrument_id,
                "ticker": profile.ticker,
                "class_code": profile.class_code,
                "lot_size": lot_size,
            }
        )

    snapshot_at = datetime.now(timezone.utc)
    raw_portfolio = client.get_portfolio(account_id)
    raw_orders = client.get_orders(account_id)
    broker = BrokerPortfolioAdapter.from_api_portfolio(
        raw_portfolio,
        account_id=account_id,
        broker_orders=raw_orders,
        snapshot_at=snapshot_at.isoformat(),
    )
    all_orders = tuple(
        order for position in broker.positions for order in position.pending_orders
    )
    if any(order.active or order.uncertain for order in all_orders):
        raise RuntimeError("Provider reports an active or uncertain broker order.")
    expected_ids = set(loaded["runtime_by_instrument"])
    unexpected_positions = {
        item.instrument_id
        for item in broker.positions
        if item.instrument_id not in expected_ids and int(item.actual_lots) != 0
    }
    if unexpected_positions:
        raise RuntimeError("Provider portfolio contains an unconfigured position.")
    broker_by_instrument = {item.instrument_id: item for item in broker.positions}
    for identity in identities:
        instrument_id = identity["instrument_id"]
        actual_lots = int(broker_by_instrument.get(instrument_id).actual_lots) if instrument_id in broker_by_instrument else 0
        expected_lots = loaded["runtime_by_instrument"][instrument_id].current_lots
        if actual_lots != expected_lots:
            raise RuntimeError(
                "Fresh provider lots differ from configured runtime: "
                f"{identity['ticker']} provider={actual_lots}, "
                f"configured={expected_lots}."
            )
        identity["current_lots"] = actual_lots

    totals = portfolio_risk_inputs(raw_portfolio)
    equity = totals["equity_rub"]
    cash = totals["cash_rub"]
    if equity is None or equity < 0 or cash is None or cash < 0:
        raise RuntimeError("Fresh provider equity/cash is unavailable or invalid.")
    return {
        "broker": broker,
        "snapshot_at": snapshot_at,
        "equity_rub": float(equity),
        "cash_rub": float(cash),
        "identities": identities,
        "provider_read_calls": (
            "GetSandboxAccounts",
            "FindInstrument",
            "GetSandboxPortfolio",
            "GetSandboxOrders",
        ),
    }


def _manifest_payload(
    root: Path,
    loaded: dict[str, Any],
    provider: dict[str, Any],
    *,
    canonical_revision: int,
    started_at: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "started_at": started_at,
        "runtime_dir": str(root),
        "account_fingerprint": _fingerprint(loaded["account_id"]),
        "canonical_revision": int(canonical_revision),
        "canonical_snapshot_at": provider["snapshot_at"].isoformat(),
        "canonical_freshness": "FRESH",
        "policy_hash": loaded["policy_hash"],
        "portfolio_policy_status": "READY",
        "portfolio_policy_mode": "OBSERVE_ONLY",
        "risk_baseline_status": "READY",
        "risk_baseline_source": "FRESH_PROVIDER_PREFLIGHT",
        "runtime_status": "ACTIVE",
        "instruments": provider["identities"],
        "provider_read_calls": list(provider["provider_read_calls"]),
        "broker_mutation_authorized": False,
        "broker_order_submit_called": False,
        "strategy_proposal_created": False,
        "central_intent_created": False,
    }


def _already_started_result(root: Path, *, action: str) -> dict[str, Any]:
    manifest = read_json_verified(root / MANIFEST_NAME, supported_versions={1})
    if Path(str(manifest.get("runtime_dir") or "")).resolve() != root:
        raise RuntimeError("Start manifest belongs to another runtime directory.")
    portfolio = PortfolioRepository(root / "portfolio_state.json").load()
    account_id = str(portfolio.account_id or "").strip()
    if _fingerprint(account_id) != str(manifest.get("account_fingerprint") or ""):
        raise RuntimeError("Start manifest account fingerprint mismatch.")
    if int(portfolio.revision) < int(manifest.get("canonical_revision", -1)):
        raise RuntimeError("Canonical portfolio predates the sealed start state.")
    runtimes = InstrumentRuntimeStore(root / "instrument_runtimes.json").load(
        expected_account_id=account_id
    )
    if not runtimes or any(item.status != "ACTIVE" for item in runtimes):
        raise RuntimeError(
            "Start manifest exists but an InstrumentRuntime is not ACTIVE; "
            "use a separate restart/recovery gate."
        )
    risk_state = RiskStateStore(root / "risk_state.json").load_account(account_id)
    risk_identity = (
        risk_state.daily_date,
        risk_state.weekly_key,
        risk_state.daily_start_equity_rub,
        risk_state.weekly_start_equity_rub,
        risk_state.high_watermark_equity_rub,
    )
    if any(value is None for value in risk_identity):
        raise RuntimeError("Started runtime has incomplete Risk baselines.")
    if manifest.get("broker_mutation_authorized") is not False:
        raise RuntimeError("Start manifest broker-mutation boundary is unsafe.")
    if manifest.get("broker_order_submit_called") is not False:
        raise RuntimeError("Start manifest reports a broker order submission.")
    return {
        "action": action,
        "status": "ALREADY_STARTED",
        "runtime_dir": str(root),
        "account_fingerprint": manifest["account_fingerprint"],
        "canonical_revision": portfolio.revision,
        "canonical_freshness": portfolio.freshness.value,
        "risk_baseline_status": "READY",
        "runtime_status": "ACTIVE",
        "instruments": manifest["instruments"],
        "broker_mutation_authorized": False,
        "broker_order_submit_called": False,
        "strategy_proposal_created": False,
        "central_intent_created": False,
        "writes_performed": False,
    }


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
    if (root / MANIFEST_NAME).exists():
        return _already_started_result(root, action=args.action)
    loaded = _load_static(root)
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
            "status": "PREVIEW" if args.action == "preview" else "STARTED",
            "runtime_dir": str(root),
            "account_fingerprint": _fingerprint(loaded["account_id"]),
            "portfolio_revision_before": loaded["portfolio"].revision,
            "provider_snapshot_at": provider["snapshot_at"].isoformat(),
            "portfolio_policy_status": "READY",
            "portfolio_policy_mode": "OBSERVE_ONLY",
            "risk_baseline_status": "UNINITIALIZED" if args.action == "preview" else "READY",
            "runtime_status": "STOPPED" if args.action == "preview" else "ACTIVE",
            "instruments": provider["identities"],
            "provider_read_calls": list(provider["provider_read_calls"]),
            "broker_mutation_authorized": False,
            "broker_order_submit_called": False,
            "strategy_proposal_created": False,
            "central_intent_created": False,
            "writes_performed": False,
        }
        if args.action == "preview":
            return result
        if str(args.confirm or "").strip() != APPLY_CONFIRMATION:
            raise RuntimeError("apply requires the exact v3.9 start confirmation.")

        manager = CanonicalPortfolioManager(
            client,
            loaded["account_id"],
            robot_state_file=root / "robot_state.json",
            portfolio_state_file=root / "portfolio_state.json",
            journal_file=root / "trading_events.db",
        )
        canonical = manager.publish_from_records(provider["broker"], record_event=True)
        if canonical.freshness is not SnapshotFreshness.FRESH or canonical.blocking:
            raise RuntimeError("Fresh canonical reconciliation is blocking.")
        for identity in provider["identities"]:
            position = canonical.position(identity["instrument_id"])
            actual_lots = int(position.actual_lots) if position is not None else 0
            if actual_lots != int(identity["current_lots"]):
                raise RuntimeError(f"Canonical/provider lots differ: {identity['ticker']}.")

        risk_engine = RiskEngine(loaded["policy"])
        risk_state, risk_event = risk_engine.initialize_pristine_baselines(
            loaded["risk_state"],
            now=provider["snapshot_at"],
            equity_rub=provider["equity_rub"],
            cash_rub=provider["cash_rub"],
            snapshot_at=_parse_timestamp(canonical.snapshot_at),
        )
        RiskStateStore(root / "risk_state.json").save_account(
            loaded["account_id"], risk_state
        )
        journal = EventJournal(root / "trading_events.db")
        journal.record(
            JournalEvent(
                category="risk",
                event_type=risk_event.event_type,
                severity=risk_event.severity,
                account_id=loaded["account_id"],
                mode="SANDBOX_EXECUTION",
                status="ready",
                payload=risk_event.details,
                timestamp_utc=provider["snapshot_at"].isoformat(),
            )
        )

        active = tuple(item.start() for item in loaded["runtimes"])
        InstrumentRuntimeStore(root / "instrument_runtimes.json").save(active)
        for runtime in active:
            journal.record(
                JournalEvent(
                    category="scheduler",
                    event_type="INSTRUMENT_RUNTIME_STARTED",
                    account_id=loaded["account_id"],
                    instrument_id=runtime.config.instrument_id,
                    ticker=runtime.config.ticker,
                    mode="SANDBOX_EXECUTION",
                    status="ACTIVE",
                    payload={
                        "runtime_key_fingerprint": _fingerprint(runtime.runtime_key),
                        "runtime_revision": runtime.revision,
                        "start_gate": APPLY_CONFIRMATION,
                    },
                )
            )
        started_at = datetime.now(timezone.utc).isoformat()
        atomic_write_json(
            root / MANIFEST_NAME,
            _manifest_payload(
                root,
                loaded,
                provider,
                canonical_revision=canonical.revision,
                started_at=started_at,
            ),
            write_checksum=True,
        )
        result["canonical_revision"] = canonical.revision
        result["canonical_freshness"] = canonical.freshness.value
        result["event_count"] = journal.count(account_id=loaded["account_id"])
        checkpoint = journal.checkpoint_wal("TRUNCATE")
        if int(checkpoint.get("busy", 0)) != 0:
            raise RuntimeError("Shadow start journal WAL checkpoint remained busy.")
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
