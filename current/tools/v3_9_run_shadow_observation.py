from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_robot.central_order_coordinator import CentralOrderCoordinator
from trading_robot.central_order_manager import CentralOrderManager, CentralOrderStore
from trading_robot.global_scheduler import GlobalScheduler
from trading_robot.instrument_runtime import InstrumentRuntime, InstrumentRuntimeStore
from trading_robot.journal import EventJournal
from trading_robot.multi_instrument_config import (
    MultiInstrumentProfile,
    MultiInstrumentProfileStore,
)
from trading_robot.multi_instrument_strategy import (
    MultiInstrumentStrategyAdapter,
    StrategyCandleLoader,
)
from trading_robot.portfolio_adapters import BrokerPortfolioAdapter
from trading_robot.portfolio_manager import CanonicalPortfolioManager
from trading_robot.portfolio_model import SnapshotFreshness
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_read_service import load_portfolio_risk_metadata
from trading_robot.portfolio_risk_shadow import (
    PortfolioRiskCandidateQuote,
    PortfolioRiskShadowError,
    PortfolioRiskShadowObserver,
    build_portfolio_risk_shadow_report,
)
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_runtime import RiskRuntimeAdapter
from trading_robot.scheduler_journal import EventJournalSchedulerSink
from trading_robot.secret_provider import preferred_secret_provider
from trading_robot.state_persistence import read_json_verified
from trading_robot.tbank_sandbox import TBankSandboxClient, quotation_to_float

APPLY_CONFIRMATION = "RUN V3.9 SHADOW OBSERVATION"
START_MANIFEST_NAME = "v3_9_shadow_runtime_start_manifest.json"
TOKEN_KEY = "TBANK_SANDBOX_TOKEN"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or run one natural closed-candle v3.9 M3 observation. "
            "The tool has no Central mutation or broker-order path."
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


def _operator_scheduler(tick: Any) -> dict[str, Any]:
    return {
        "serviced_at": tick.serviced_at.isoformat(),
        "actions": [
            {
                "ticker": item.ticker,
                "action": item.action,
                "status": item.status,
                "detail": item.detail,
                "candle_time": item.candle_time,
            }
            for item in tick.actions
        ],
        "failure_count": len(tick.failures),
    }


def _operator_shadow_report(report: Mapping[str, Any]) -> dict[str, Any]:
    safe = {key: value for key, value in report.items() if key != "latest"}
    latest = report.get("latest")
    if isinstance(latest, Mapping):
        safe["latest"] = {
            key: latest.get(key)
            for key in (
                "event_id",
                "timestamp_utc",
                "ticker",
                "candle_time",
                "status",
                "action",
                "severity",
            )
        }
    else:
        safe["latest"] = None
    return safe


def _utc_timestamp(value: Any) -> datetime:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        parsed = parsed.tz_localize("UTC")
    else:
        parsed = parsed.tz_convert("UTC")
    return parsed.to_pydatetime()


def _load_runtime(root: Path) -> dict[str, Any]:
    required = (
        START_MANIFEST_NAME,
        START_MANIFEST_NAME + ".sha256",
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
        "trading_events.db",
    )
    for name in required:
        if not (root / name).is_file():
            raise RuntimeError(f"v3.9 observation prerequisite is missing: {name}")

    manifest = read_json_verified(
        root / START_MANIFEST_NAME,
        supported_versions={1},
    )
    if Path(str(manifest.get("runtime_dir") or "")).resolve() != root:
        raise RuntimeError("Start manifest belongs to another runtime directory.")
    if manifest.get("runtime_status") != "ACTIVE":
        raise RuntimeError("Start manifest did not seal ACTIVE runtimes.")
    if manifest.get("broker_mutation_authorized") is not False:
        raise RuntimeError("Start manifest broker boundary is unsafe.")

    portfolio = PortfolioRepository(root / "portfolio_state.json").load()
    account_id = str(portfolio.account_id or "").strip()
    if not account_id:
        raise RuntimeError("Canonical portfolio has no account scope.")
    if _fingerprint(account_id) != str(manifest.get("account_fingerprint") or ""):
        raise RuntimeError("Start manifest account fingerprint mismatch.")
    if portfolio.freshness is not SnapshotFreshness.FRESH or portfolio.blocking:
        raise RuntimeError("Canonical portfolio is not FRESH/non-blocking.")

    profiles = MultiInstrumentProfileStore(
        root / "multi_instrument_profiles.json"
    ).load_mode("SANDBOX_EXECUTION")
    runtimes = InstrumentRuntimeStore(root / "instrument_runtimes.json").load(
        expected_account_id=account_id
    )
    if not 2 <= len(profiles) <= 3 or len(runtimes) != len(profiles):
        raise RuntimeError("v3.9 observation requires two or three matched runtimes.")
    runtime_by_instrument = {item.config.instrument_id: item for item in runtimes}
    if set(runtime_by_instrument) != {item.instrument_id for item in profiles}:
        raise RuntimeError("Instrument profile/runtime scopes differ.")
    if any(item.status != "ACTIVE" for item in runtimes):
        raise RuntimeError("Every InstrumentRuntime must be ACTIVE.")
    if any(item.pending_order_ids for item in runtimes):
        raise RuntimeError("Observation refuses pending runtime orders.")
    for profile in profiles:
        runtime = runtime_by_instrument[profile.instrument_id]
        if runtime.config.to_dict() != profile.to_runtime_config(account_id).to_dict():
            raise RuntimeError(f"Runtime configuration drift: {profile.ticker}.")

    central = CentralOrderStore(root / "central_order_state.json").load(
        expected_account_id=account_id
    )
    if central.intents or central.blocking_intent is not None:
        raise RuntimeError("Observation requires empty Central order history.")
    if central.reserved_cash_kopecks:
        raise RuntimeError("Observation refuses Central cash reservations.")

    risk = RiskProfileStore(root / "risk_profiles.json").require_portfolio_policy(
        "SANDBOX_EXECUTION"
    )
    if str(risk.get("account_scope") or "").strip() != account_id:
        raise RuntimeError("Portfolio Risk policy account scope mismatch.")
    if risk["portfolio_policy_status"] != "READY":
        raise RuntimeError("Portfolio Risk policy is not READY.")
    if risk["policy"].portfolio_policy_mode != "OBSERVE_ONLY":
        raise RuntimeError("Portfolio Risk policy must remain OBSERVE_ONLY.")
    risk_state = RiskStateStore(root / "risk_state.json").load_account(account_id)
    baseline = (
        risk_state.daily_date,
        risk_state.weekly_key,
        risk_state.daily_start_equity_rub,
        risk_state.weekly_start_equity_rub,
        risk_state.high_watermark_equity_rub,
    )
    if any(value is None for value in baseline):
        raise RuntimeError("Risk baselines are not initialized.")
    if risk_state.kill_switch_active or risk_state.risk_resync_required:
        raise RuntimeError("Risk kill/resync gate blocks observation.")

    metadata = load_portfolio_risk_metadata(root / "portfolio_risk_metadata.json")
    if set(metadata) != set(runtime_by_instrument):
        raise RuntimeError("Portfolio Risk metadata scope differs from runtimes.")
    return {
        "account_id": account_id,
        "portfolio": portfolio,
        "profiles": tuple(profiles),
        "runtimes": tuple(runtimes),
        "runtime_by_instrument": runtime_by_instrument,
        "central": central,
        "metadata": metadata,
    }


def _provider_preflight(
    client: Any,
    loaded: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    account_id = loaded["account_id"]
    accounts = client.get_accounts()
    account_ids = {
        str(item.get("id") or item.get("accountId") or "").strip()
        for item in accounts
        if isinstance(item, Mapping)
    }
    if account_id not in account_ids:
        raise RuntimeError("Configured Sandbox account is not open/available.")

    frames: dict[str, pd.DataFrame] = {}
    statuses: dict[str, Mapping[str, Any]] = {}
    instruments: list[dict[str, Any]] = []
    loader = StrategyCandleLoader(client)
    for profile in sorted(loaded["profiles"], key=lambda item: item.ticker):
        runtime = loaded["runtime_by_instrument"][profile.instrument_id]
        instrument = client.find_instrument(profile.ticker, profile.class_code)
        instrument_id = str(client.instrument_id(instrument)).strip()
        if instrument_id != profile.instrument_id:
            raise RuntimeError(f"Provider UID changed for {profile.ticker}.")
        try:
            lot_size = int(instrument.get("lot"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Provider lot size is invalid: {profile.ticker}."
            ) from exc
        if lot_size != loaded["metadata"][profile.instrument_id].lot_size:
            raise RuntimeError(f"Provider lot size changed for {profile.ticker}.")
        status = client.get_trading_status(profile.instrument_id)
        if not isinstance(status, Mapping):
            raise TypeError(f"Trading status is invalid: {profile.ticker}.")
        frame = loader.load(runtime, profile, now=now)
        frames[runtime.runtime_key] = frame
        statuses[runtime.runtime_key] = status
        instruments.append(
            {
                "ticker": profile.ticker,
                "lot_size": lot_size,
                "latest_closed_candle": _utc_timestamp(frame.index[-1]).isoformat(),
                "complete_candles": len(frame),
                "previously_processed_candle": (
                    runtime.last_processed_candle.isoformat()
                    if runtime.last_processed_candle is not None
                    else None
                ),
            }
        )

    raw_last_prices = client.get_last_prices(
        [profile.instrument_id for profile in loaded["profiles"]]
    )
    prices_by_instrument: dict[str, list[Mapping[str, Any]]] = {}
    for raw in raw_last_prices:
        if not isinstance(raw, Mapping):
            continue
        instrument_id = str(
            raw.get("instrumentUid") or raw.get("instrumentId") or ""
        ).strip()
        if instrument_id:
            prices_by_instrument.setdefault(instrument_id, []).append(raw)
    candidate_quotes: dict[str, PortfolioRiskCandidateQuote | None] = {}
    for profile in loaded["profiles"]:
        runtime = loaded["runtime_by_instrument"][profile.instrument_id]
        matches = prices_by_instrument.get(profile.instrument_id, [])
        quote = None
        if len(matches) == 1:
            raw = matches[0]
            try:
                quote = PortfolioRiskCandidateQuote(
                    unit_price_rub=quotation_to_float(raw.get("price")),
                    price_at=str(raw.get("time") or ""),
                    source="TBANK_LAST_PRICE_EXCHANGE",
                )
            except (TypeError, ValueError, PortfolioRiskShadowError):
                quote = None
        candidate_quotes[runtime.runtime_key] = quote
        instrument = next(
            item for item in instruments if item["ticker"] == profile.ticker
        )
        instrument["candidate_price_at"] = (
            quote.price_at.isoformat() if quote is not None else None
        )
        instrument["candidate_price_source"] = (
            quote.source if quote is not None else None
        )

    raw_portfolio = client.get_portfolio(account_id)
    raw_orders = client.get_orders(account_id)
    snapshot_at = datetime.now(timezone.utc)
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
    for item in instruments:
        profile = next(
            candidate
            for candidate in loaded["profiles"]
            if candidate.ticker == item["ticker"]
        )
        position = broker_by_instrument.get(profile.instrument_id)
        actual_lots = int(position.actual_lots) if position is not None else 0
        runtime = loaded["runtime_by_instrument"][profile.instrument_id]
        if actual_lots != runtime.current_lots:
            raise RuntimeError(
                "Fresh provider lots differ from active runtime: "
                f"{profile.ticker} provider={actual_lots}, "
                f"runtime={runtime.current_lots}."
            )
        item["current_lots"] = actual_lots
    return {
        "broker": broker,
        "snapshot_at": snapshot_at,
        "frames": frames,
        "statuses": statuses,
        "candidate_quotes": candidate_quotes,
        "instruments": instruments,
    }


class _CachedCandleLoader:
    def __init__(self, frames: Mapping[str, pd.DataFrame]) -> None:
        self.frames = dict(frames)

    def load(
        self,
        runtime: InstrumentRuntime,
        _profile: MultiInstrumentProfile,
        *,
        now: datetime,
    ) -> pd.DataFrame:
        del now
        return self.frames[runtime.runtime_key].copy()


class _ReadOnlyRuntimeServices:
    def __init__(
        self,
        *,
        loaded: Mapping[str, Any],
        statuses: Mapping[str, Mapping[str, Any]],
        portfolio_repository: PortfolioRepository,
        risk_state_store: RiskStateStore,
    ) -> None:
        self.loaded = loaded
        self.statuses = statuses
        self.portfolio_repository = portfolio_repository
        self.risk_state_store = risk_state_store

    def refresh_market_status(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        del now
        return self.statuses[runtime.runtime_key]

    def refresh_risk(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        del runtime, now
        state = self.risk_state_store.load_account(self.loaded["account_id"])
        if state.kill_switch_active or state.risk_resync_required:
            raise RuntimeError("Risk kill/resync gate became active.")
        return state

    def reconcile_portfolio(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        del now
        portfolio = self.portfolio_repository.load(
            expected_account_id=self.loaded["account_id"]
        )
        if portfolio.freshness is not SnapshotFreshness.FRESH or portfolio.blocking:
            raise RuntimeError("Canonical portfolio became unsafe.")
        position = portfolio.position(runtime.config.instrument_id)
        actual_lots = int(position.actual_lots) if position is not None else 0
        if actual_lots != runtime.current_lots:
            raise RuntimeError("Runtime/canonical lots changed before observation.")
        return portfolio


class _ShadowObservationHooks:
    def __init__(
        self,
        *,
        strategy: MultiInstrumentStrategyAdapter,
        coordinator: CentralOrderCoordinator,
        profiles: tuple[MultiInstrumentProfile, ...],
        frames: Mapping[str, pd.DataFrame],
        lot_sizes: Mapping[str, int],
        candidate_quotes: Mapping[str, PortfolioRiskCandidateQuote | None],
    ) -> None:
        self.strategy = strategy
        self.coordinator = coordinator
        self.profiles = {item.instrument_id: item for item in profiles}
        self.frames = dict(frames)
        self.lot_sizes = dict(lot_sizes)
        self.candidate_quotes = dict(candidate_quotes)
        self.results: dict[str, Any] = {}

    def refresh_market_status(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        return self.strategy.refresh_market_status(runtime, now)

    def refresh_risk(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        return self.strategy.refresh_risk(runtime, now)

    def reconcile_portfolio(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        return self.strategy.reconcile_portfolio(runtime, now)

    def evaluate_closed_candle(
        self,
        runtime: InstrumentRuntime,
        candle_time: datetime,
        now: datetime,
    ) -> Any:
        proposal = self.strategy.evaluate_closed_candle(runtime, candle_time, now)
        result = self.coordinator.coordinate(
            proposal,
            runtime,
            self.profiles[runtime.config.instrument_id],
            candles=self.frames[runtime.runtime_key],
            lot_size=self.lot_sizes[runtime.config.instrument_id],
            now=now,
            observe_only=True,
            portfolio_risk_candidate_quote=self.candidate_quotes.get(
                runtime.runtime_key
            ),
        )
        shadow = result.portfolio_risk_shadow
        if (
            result.status != "SHADOW_OBSERVED"
            or shadow is None
            or shadow.status != "EVALUATED"
            or shadow.journal_event_id is None
        ):
            raise RuntimeError(result.reason or "M3 shadow observation failed.")
        self.results[runtime.runtime_key] = result
        return result


def _preview_proposals(
    loaded: Mapping[str, Any],
    provider: Mapping[str, Any],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    services = _ReadOnlyRuntimeServices(
        loaded=loaded,
        statuses=provider["statuses"],
        portfolio_repository=PortfolioRepository(
            Path(loaded["runtime_dir"]) / "portfolio_state.json"
        ),
        risk_state_store=RiskStateStore(
            Path(loaded["runtime_dir"]) / "risk_state.json"
        ),
    )
    adapter = MultiInstrumentStrategyAdapter(
        loaded["profiles"],
        candle_loader=_CachedCandleLoader(provider["frames"]),
        services=services,
    )
    result: list[dict[str, Any]] = []
    for runtime in sorted(loaded["runtimes"], key=lambda item: item.config.ticker):
        candle = _utc_timestamp(provider["frames"][runtime.runtime_key].index[-1])
        proposal = adapter.evaluate_closed_candle(runtime, candle, now)
        primary = proposal.decisions[proposal.primary_strategy]
        result.append(
            {
                "ticker": proposal.ticker,
                "candle_time": proposal.candle_time,
                "candidate_price_at": (
                    provider["candidate_quotes"][runtime.runtime_key]
                    .price_at.isoformat()
                    if provider["candidate_quotes"][runtime.runtime_key] is not None
                    else None
                ),
                "candidate_price_source": (
                    provider["candidate_quotes"][runtime.runtime_key].source
                    if provider["candidate_quotes"][runtime.runtime_key] is not None
                    else None
                ),
                "signal": int(primary.signal),
                "requested_target_lots": proposal.primary_target_lots,
                "execution_authorized": False,
                "persisted": False,
            }
        )
    return result


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
    loaded = _load_runtime(root)
    loaded["runtime_dir"] = str(root)
    selected = secret_provider_factory(root)
    if not bool(getattr(selected, "secure", False)):
        raise RuntimeError("v3.9 SHADOW requires a secure secret provider.")
    token = str(selected.get(TOKEN_KEY) or "").strip()
    if not token:
        raise RuntimeError("T-Invest Sandbox credential is unavailable.")

    provider_query_at = datetime.now(timezone.utc)
    with client_factory(
        token,
        connect_timeout_seconds=float(args.connect_timeout),
        read_timeout_seconds=float(args.read_timeout),
        max_retries=int(args.max_retries),
        ca_bundle_path=str(args.ca_bundle or "").strip() or None,
    ) as client:
        provider = _provider_preflight(client, loaded, now=provider_query_at)
        cycle_now = max(datetime.now(timezone.utc), provider["snapshot_at"])
        proposal_preview = _preview_proposals(loaded, provider, now=cycle_now)
        base = {
            "action": args.action,
            "status": "PREVIEW" if args.action == "preview" else "OBSERVED",
            "runtime_dir": str(root),
            "account_fingerprint": _fingerprint(loaded["account_id"]),
            "runtime_status": "ACTIVE",
            "instruments": provider["instruments"],
            "natural_proposals": proposal_preview,
            "execution_authorized": False,
            "central_mutation_authorized": False,
            "broker_mutation_authorized": False,
            "broker_order_submit_called": False,
            "writes_performed": False,
        }
        if args.action == "preview":
            return base
        if str(args.confirm or "").strip() != APPLY_CONFIRMATION:
            raise RuntimeError("apply requires the exact v3.9 observation confirmation.")
        latest_by_runtime = {
            runtime.runtime_key: _utc_timestamp(
                provider["frames"][runtime.runtime_key].index[-1]
            )
            for runtime in loaded["runtimes"]
        }
        if all(
            runtime.last_processed_candle is not None
            and latest_by_runtime[runtime.runtime_key]
            <= runtime.last_processed_candle
            for runtime in loaded["runtimes"]
        ):
            base.update(
                {
                    "status": "ALREADY_OBSERVED",
                    "shadow_report": _operator_shadow_report(
                        build_portfolio_risk_shadow_report(
                            EventJournal(root / "trading_events.db", read_only=True),
                            account_id=loaded["account_id"],
                        )
                    ),
                }
            )
            return base

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
        cycle_now = max(
            datetime.now(timezone.utc),
            provider["snapshot_at"],
            _utc_timestamp(canonical.snapshot_at),
        )

        central_manager = CentralOrderManager(
            CentralOrderStore(root / "central_order_state.json"),
            account_id=loaded["account_id"],
        )
        risk_runtime = RiskRuntimeAdapter.from_directory(
            root,
            account_id=loaded["account_id"],
            mode="SANDBOX_EXECUTION",
            auto_create_dry_run_profile=False,
        )
        observer = PortfolioRiskShadowObserver.from_directory(
            root,
            account_id=loaded["account_id"],
            mode="SANDBOX_EXECUTION",
        )
        coordinator = CentralOrderCoordinator(
            central_manager,
            PortfolioRepository(root / "portfolio_state.json"),
            risk_runtime,
            portfolio_risk_shadow=observer,
        )
        services = _ReadOnlyRuntimeServices(
            loaded=loaded,
            statuses=provider["statuses"],
            portfolio_repository=PortfolioRepository(root / "portfolio_state.json"),
            risk_state_store=RiskStateStore(root / "risk_state.json"),
        )
        strategy = MultiInstrumentStrategyAdapter(
            loaded["profiles"],
            candle_loader=_CachedCandleLoader(provider["frames"]),
            services=services,
        )
        lot_sizes = {
            profile.instrument_id: loaded["metadata"][profile.instrument_id].lot_size
            for profile in loaded["profiles"]
        }
        hooks = _ShadowObservationHooks(
            strategy=strategy,
            coordinator=coordinator,
            profiles=loaded["profiles"],
            frames=provider["frames"],
            lot_sizes=lot_sizes,
            candidate_quotes=provider["candidate_quotes"],
        )
        journal = EventJournal(root / "trading_events.db")
        scheduler = GlobalScheduler.restore(
            InstrumentRuntimeStore(root / "instrument_runtimes.json"),
            expected_account_id=loaded["account_id"],
            event_sink=EventJournalSchedulerSink(
                journal,
                mode="SANDBOX_EXECUTION",
            ),
        )
        tick = scheduler.tick(
            now=cycle_now,
            latest_closed_candles=latest_by_runtime,
            hooks=hooks,
        )
        if tick.failures:
            raise RuntimeError(
                "Shadow scheduler failed: "
                + "; ".join(
                    f"{item.ticker}/{item.action}: {item.detail}"
                    for item in tick.failures
                )
            )
        central_after = central_manager.state()
        if central_after.intents or central_after.reserved_cash_kopecks:
            raise RuntimeError("Central state changed during observe-only cycle.")
        report = _operator_shadow_report(
            build_portfolio_risk_shadow_report(
                EventJournal(root / "trading_events.db", read_only=True),
                account_id=loaded["account_id"],
            )
        )
        base.update(
            {
                "canonical_revision": canonical.revision,
                "scheduler": _operator_scheduler(tick),
                "observations": [
                    hooks.results[key].portfolio_risk_shadow.to_dict()
                    for key in sorted(hooks.results)
                ],
                "shadow_report": report,
                "writes_performed": True,
            }
        )
        return base


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
