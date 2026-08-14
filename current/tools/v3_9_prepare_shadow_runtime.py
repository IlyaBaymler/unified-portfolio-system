from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.config_persistence import PROFILE_MODES, StrategyProfileStore
from trading_robot.instrument_runtime import InstrumentRuntime, InstrumentRuntimeStore
from trading_robot.journal import EventJournal
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_adapter import PortfolioRiskInstrumentMetadata
from trading_robot.portfolio_risk_read_service import load_portfolio_risk_metadata
from trading_robot.risk import RiskState
from trading_robot.risk_persistence import RISK_MODES, RiskProfileStore, RiskStateStore
from trading_robot.state_persistence import atomic_write_json

APPLY_CONFIRMATION = "PREPARE ISOLATED V3.9 SHADOW RUNTIME"
MANIFEST_NAME = "v3_9_shadow_runtime_seed_manifest.json"
METADATA_NAME = "portfolio_risk_metadata.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a token-free isolated v3.9 M3 shadow runtime from a "
            "verified v3.8 multi-instrument runtime."
        )
    )
    parser.add_argument("action", choices=("preview", "apply"))
    parser.add_argument("--source-runtime-dir", required=True)
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--confirm", default="")
    return parser.parse_args(argv)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _paths_overlap(source: Path, target: Path) -> bool:
    return source == target or source in target.parents or target in source.parents


def _promote_staging(staging: Path, target: Path) -> None:
    last_error: OSError | None = None
    for delay in (0.0, 0.10, 0.25, 0.50, 1.00, 2.00):
        if delay:
            time.sleep(delay)
        try:
            os.replace(staging, target)
            return
        except PermissionError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _remove_staging(staging: Path) -> None:
    last_error: OSError | None = None
    for delay in (0.0, 0.10, 0.25, 0.50, 1.00):
        if delay:
            time.sleep(delay)
        try:
            shutil.rmtree(staging)
            return
        except PermissionError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _verified_portfolio_risk_metadata(
    source: Path,
) -> dict[str, PortfolioRiskInstrumentMetadata]:
    metadata_path = source / METADATA_NAME
    checksum_path = metadata_path.with_name(metadata_path.name + ".sha256")
    if not metadata_path.is_file() and not checksum_path.is_file():
        return {}
    if not metadata_path.is_file() or not checksum_path.is_file():
        raise RuntimeError(
            "Source Portfolio Risk metadata/checksum pair is incomplete."
        )
    return load_portfolio_risk_metadata(metadata_path)


def _lot_sizes(
    source: Path,
    metadata: dict[str, PortfolioRiskInstrumentMetadata],
) -> dict[str, int]:
    central = CentralOrderStore(source / "central_order_state.json").load()
    sizes: dict[str, int] = {}
    for intent in central.intents:
        instrument_id = intent.candidate.instrument_id
        lot_size = int(intent.candidate.lot_size)
        existing = sizes.get(instrument_id)
        if existing is not None and existing != lot_size:
            raise RuntimeError(
                f"Conflicting lot-size evidence for {instrument_id}."
            )
        sizes[instrument_id] = lot_size
    for instrument_id, item in metadata.items():
        lot_size = int(item.lot_size)
        existing = sizes.get(instrument_id)
        if existing is not None and existing != lot_size:
            raise RuntimeError(
                f"Conflicting lot-size evidence for {instrument_id}."
            )
        sizes[instrument_id] = lot_size
    return sizes


def _load_source(source: Path) -> dict[str, Any]:
    required = (
        "portfolio_state.json",
        "portfolio_state.json.sha256",
        "strategy_profiles.json",
        "risk_profiles.json",
        "multi_instrument_profiles.json",
        "multi_instrument_profiles.json.sha256",
        "instrument_runtimes.json",
        "instrument_runtimes.json.sha256",
        "central_order_state.json",
        "central_order_state.json.sha256",
    )
    for name in required:
        if not (source / name).is_file():
            raise RuntimeError(f"Source runtime prerequisite is missing: {name}")

    portfolio = PortfolioRepository(source / "portfolio_state.json").load()
    account_id = str(portfolio.account_id or "").strip()
    if not account_id:
        raise RuntimeError("Source canonical portfolio has no account scope.")

    strategy_store = StrategyProfileStore(source / "strategy_profiles.json")
    strategy_document = strategy_store.load_document()
    strategies = {
        mode: profile
        for mode in PROFILE_MODES
        if (profile := strategy_store.load_profile(mode)) is not None
    }
    if "SANDBOX_EXECUTION" not in strategies:
        raise RuntimeError("Source Sandbox Strategy profile is missing.")

    risk_store = RiskProfileStore(source / "risk_profiles.json")
    risk_document = risk_store.load_document()
    risks = {
        mode: profile
        for mode in RISK_MODES
        if (profile := risk_store.load_profile(mode)) is not None
    }
    sandbox_risk = risks.get("SANDBOX_EXECUTION")
    if sandbox_risk is None or not sandbox_risk["policy"].enabled:
        raise RuntimeError("Source Sandbox Risk profile is missing or disabled.")
    risk_scope = str(sandbox_risk.get("account_scope") or "").strip()
    if risk_scope != account_id:
        raise RuntimeError("Source Sandbox Risk profile account scope mismatch.")

    profile_store = MultiInstrumentProfileStore(
        source / "multi_instrument_profiles.json"
    )
    profiles = profile_store.load_mode("SANDBOX_EXECUTION")
    if len(profiles) < 2:
        raise RuntimeError("Source must contain at least two Sandbox instruments.")
    runtimes = InstrumentRuntimeStore(source / "instrument_runtimes.json").load(
        expected_account_id=account_id
    )
    runtime_by_instrument = {item.config.instrument_id: item for item in runtimes}
    if {item.instrument_id for item in profiles} != set(runtime_by_instrument):
        raise RuntimeError("Source profile/runtime instrument scopes differ.")
    if any(item.pending_order_ids for item in runtimes):
        raise RuntimeError("Source instrument runtime contains pending orders.")
    for item in profiles:
        position = portfolio.position(item.instrument_id)
        actual_lots = int(position.actual_lots) if position is not None else 0
        if runtime_by_instrument[item.instrument_id].current_lots != actual_lots:
            raise RuntimeError("Source runtime lots do not match canonical portfolio.")

    central = CentralOrderStore(source / "central_order_state.json").load(
        expected_account_id=account_id
    )
    if central.blocking_intent is not None or central.reserved_cash_kopecks:
        raise RuntimeError("Source Central state is blocking or reserves cash.")

    source_metadata = _verified_portfolio_risk_metadata(source)
    sizes = _lot_sizes(source, source_metadata)
    missing_sizes = sorted(
        item.instrument_id for item in profiles if item.instrument_id not in sizes
    )
    if missing_sizes:
        raise RuntimeError(
            "Source has no verified lot-size evidence for: "
            + ", ".join(missing_sizes)
        )
    metadata = []
    for item in profiles:
        position = portfolio.position(item.instrument_id)
        configured = source_metadata.get(item.instrument_id)
        asset_class = (
            position.asset_type
            if position is not None and position.asset_type.upper() != "UNKNOWN"
            else (configured.asset_class if configured is not None else None)
        )
        currency = (
            position.currency
            if position is not None and position.currency
            else (configured.currency if configured is not None else None)
        )
        metadata.append(
            {
                "instrument_id": item.instrument_id,
                "lot_size": sizes[item.instrument_id],
                "asset_class": asset_class,
                "currency": currency,
            }
        )
    return {
        "portfolio": portfolio,
        "account_id": account_id,
        "strategies": strategies,
        "strategy_mode": str(strategy_document["last_selected_mode"]),
        "risks": risks,
        "risk_mode": str(risk_document["last_selected_mode"]),
        "profiles": profiles,
        "runtimes": runtimes,
        "metadata": metadata,
    }


def _write_runtime(
    staging: Path,
    *,
    source: Path,
    target: Path,
    loaded: dict[str, Any],
) -> None:
    account_id = loaded["account_id"]
    PortfolioRepository(staging / "portfolio_state.json").save(loaded["portfolio"])

    strategy_store = StrategyProfileStore(staging / "strategy_profiles.json")
    for mode, profile in loaded["strategies"].items():
        strategy_store.save_profile(
            mode,
            profile["config"],
            select=mode == "SANDBOX_EXECUTION",
        )

    risk_store = RiskProfileStore(staging / "risk_profiles.json")
    for mode, profile in loaded["risks"].items():
        policy = replace(
            profile["policy"],
            portfolio_policy_configured=False,
            portfolio_policy_mode="OBSERVE_ONLY",
        )
        risk_store.save_profile(
            mode,
            policy,
            select=mode == "SANDBOX_EXECUTION",
            account_scope=account_id,
            source="V3_9_M3_ISOLATED_RUNTIME_BOOTSTRAP",
        )

    profile_store = MultiInstrumentProfileStore(
        staging / "multi_instrument_profiles.json"
    )
    profile_store.save_mode("SANDBOX_EXECUTION", loaded["profiles"])
    clean_runtimes = tuple(
        InstrumentRuntime(
            config=item.config,
            status="STOPPED",
            current_lots=(
                int(position.actual_lots)
                if (
                    position := loaded["portfolio"].position(
                        item.config.instrument_id
                    )
                ) is not None
                else 0
            ),
        )
        for item in loaded["runtimes"]
    )
    InstrumentRuntimeStore(staging / "instrument_runtimes.json").save(
        clean_runtimes
    )
    RiskStateStore(staging / "risk_state.json").save_account(
        account_id,
        RiskState(),
    )
    CentralOrderStore(staging / "central_order_state.json").initialize(account_id)
    journal = EventJournal(staging / "trading_events.db")
    del journal
    gc.collect()
    atomic_write_json(
        staging / METADATA_NAME,
        {"version": 1, "instruments": loaded["metadata"]},
        write_checksum=True,
    )
    atomic_write_json(
        staging / MANIFEST_NAME,
        {
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_runtime_dir": str(source),
            "runtime_dir": str(target),
            "account_fingerprint": _fingerprint(account_id),
            "portfolio_revision": int(loaded["portfolio"].revision),
            "instrument_count": len(loaded["profiles"]),
            "instruments": sorted(item.ticker for item in loaded["profiles"]),
            "portfolio_policy_status": "CONFIGURATION_REQUIRED",
            "portfolio_policy_mode": "OBSERVE_ONLY",
            "central_order_history_empty": True,
            "risk_counters_empty": True,
            "shadow_journal_empty": True,
            "runtimes_stopped": True,
            "secrets_copied": False,
            "logs_copied": False,
            "broker_order_history_copied": False,
        },
        write_checksum=True,
    )


def _validate_runtime(target: Path, *, account_id: str) -> None:
    portfolio = PortfolioRepository(target / "portfolio_state.json").load(
        expected_account_id=account_id
    )
    profiles = MultiInstrumentProfileStore(
        target / "multi_instrument_profiles.json"
    ).load_mode("SANDBOX_EXECUTION")
    runtimes = InstrumentRuntimeStore(target / "instrument_runtimes.json").load(
        expected_account_id=account_id
    )
    if len(profiles) != len(runtimes) or any(item.status != "STOPPED" for item in runtimes):
        raise RuntimeError("Prepared instrument runtimes are incomplete or active.")
    if any(item.pending_order_ids for item in runtimes):
        raise RuntimeError("Prepared instrument runtime contains pending orders.")
    for runtime in runtimes:
        position = portfolio.position(runtime.config.instrument_id)
        actual = int(position.actual_lots) if position is not None else 0
        if runtime.current_lots != actual:
            raise RuntimeError("Prepared runtime lots do not match canonical state.")
    risk = RiskProfileStore(target / "risk_profiles.json").require_profile(
        "SANDBOX_EXECUTION"
    )
    if risk.get("account_scope") != account_id:
        raise RuntimeError("Prepared Risk account scope mismatch.")
    if risk["portfolio_policy_status"] != "CONFIGURATION_REQUIRED":
        raise RuntimeError("Prepared Portfolio Policy was activated implicitly.")
    if RiskStateStore(target / "risk_state.json").load_account(account_id) != RiskState():
        raise RuntimeError("Prepared Risk counters are not empty.")
    central = CentralOrderStore(target / "central_order_state.json").load(
        expected_account_id=account_id
    )
    if central.intents:
        raise RuntimeError("Prepared Central history is not empty.")
    journal = EventJournal(target / "trading_events.db", read_only=True)
    journal_count = journal.count()
    del journal
    gc.collect()
    if journal_count != 0:
        raise RuntimeError("Prepared shadow journal is not empty.")
    metadata = load_portfolio_risk_metadata(target / METADATA_NAME)
    if set(metadata) != {item.instrument_id for item in profiles}:
        raise RuntimeError("Prepared Portfolio Risk metadata scope mismatch.")
    forbidden = (".env", "robot_gui.log", "robot_debug.log")
    if any((target / name).exists() for name in forbidden):
        raise RuntimeError("Prepared runtime contains forbidden secret/log files.")


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source_runtime_dir).expanduser().resolve()
    target = Path(args.runtime_dir).expanduser().resolve()
    if _paths_overlap(source, target):
        raise RuntimeError("Source and isolated runtime directories must not overlap.")
    if not source.is_dir():
        raise RuntimeError("Source runtime directory does not exist.")
    loaded = _load_source(source)
    target_existed_before = target.exists()
    result = {
        "action": args.action,
        "status": "PREVIEW" if args.action == "preview" else "PREPARED",
        "source_runtime_dir": str(source),
        "runtime_dir": str(target),
        "target_existed_before": target_existed_before,
        "target_exists": target_existed_before,
        "account_fingerprint": _fingerprint(loaded["account_id"]),
        "portfolio_revision": int(loaded["portfolio"].revision),
        "instrument_count": len(loaded["profiles"]),
        "instruments": sorted(item.ticker for item in loaded["profiles"]),
        "portfolio_policy_status": "CONFIGURATION_REQUIRED",
        "shadow_journal_empty": True,
        "runtimes_stopped": True,
        "secrets_copied": False,
        "writes_performed": args.action == "apply",
    }
    if args.action == "preview":
        return result
    if str(args.confirm or "").strip() != APPLY_CONFIRMATION:
        raise RuntimeError("apply requires the exact v3.9 shadow confirmation.")
    if target.exists():
        raise RuntimeError("Isolated runtime target already exists; overwrite refused.")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent)
    ).resolve()
    try:
        _write_runtime(staging, source=source, target=target, loaded=loaded)
        _validate_runtime(staging, account_id=loaded["account_id"])
        _promote_staging(staging, target)
        _validate_runtime(target, account_id=loaded["account_id"])
    except Exception:
        if staging.exists() and staging.parent == target.parent:
            try:
                _remove_staging(staging)
            except OSError:
                pass
        raise
    result["target_exists"] = target.exists()
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
