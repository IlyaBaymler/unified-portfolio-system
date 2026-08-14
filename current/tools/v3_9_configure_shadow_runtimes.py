from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.journal import EventJournal
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.portfolio_risk_read_service import load_portfolio_risk_metadata
from trading_robot.risk import RiskState
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.secret_provider import SecretProviderProbe, probe_secret_provider
from trading_robot.state_persistence import atomic_write_json, read_json_verified

APPLY_CONFIRMATION = "CONFIGURE V3.9 SHADOW RUNTIMES"
MANIFEST_NAME = "v3_9_shadow_runtime_config_manifest.json"
SEED_MANIFEST_NAME = "v3_9_shadow_runtime_seed_manifest.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and seal existing isolated v3.9 SHADOW runtimes. "
            "This command never creates a proposal, intent or provider POST."
        )
    )
    parser.add_argument("action", choices=("preview", "apply"))
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--confirm", default="")
    return parser.parse_args(argv)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


def _baseline_status(state: RiskState) -> str:
    identity = (
        state.daily_date,
        state.weekly_key,
        state.daily_start_equity_rub,
        state.weekly_start_equity_rub,
        state.high_watermark_equity_rub,
    )
    if all(value is None for value in identity):
        if (
            state.daily_turnover_rub != 0
            or state.daily_order_count != 0
            or state.last_equity_rub is not None
            or state.last_cash_rub is not None
            or state.last_evaluated_at is not None
            or state.last_execution_at is not None
            or state.recorded_execution_ids
        ):
            raise RuntimeError("Risk baseline state is partially initialized.")
        return "UNINITIALIZED"
    if any(value is None for value in identity):
        raise RuntimeError("Risk baseline state is partially initialized.")
    return "READY"


def _load_runtime(
    root: Path,
    *,
    secret_probe_factory: Callable[[str | Path], SecretProviderProbe],
) -> dict[str, Any]:
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
        SEED_MANIFEST_NAME,
        SEED_MANIFEST_NAME + ".sha256",
        "trading_events.db",
    )
    for name in required:
        if not (root / name).is_file():
            raise RuntimeError(f"v3.9 SHADOW prerequisite is missing: {name}")

    portfolio = PortfolioRepository(root / "portfolio_state.json").load()
    account_id = str(portfolio.account_id or "").strip()
    if not account_id:
        raise RuntimeError("Canonical portfolio has no account scope.")

    seed = read_json_verified(root / SEED_MANIFEST_NAME, supported_versions={1})
    if Path(str(seed.get("runtime_dir") or "")).resolve() != root:
        raise RuntimeError("Seed manifest belongs to another runtime directory.")
    if int(seed.get("portfolio_revision", -1)) != int(portfolio.revision):
        raise RuntimeError("Canonical portfolio changed after the v3.9 seed.")

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
        raise RuntimeError("Configure gate requires all InstrumentRuntimes STOPPED.")
    if any(item.pending_order_ids for item in runtimes):
        raise RuntimeError("Configure gate refuses pending runtime orders.")
    for profile in profiles:
        runtime = runtime_by_instrument[profile.instrument_id]
        if runtime.config.to_dict() != profile.to_runtime_config(account_id).to_dict():
            raise RuntimeError(f"Runtime configuration drift: {profile.ticker}.")
        position = portfolio.position(profile.instrument_id)
        actual_lots = int(position.actual_lots) if position is not None else 0
        if runtime.current_lots != actual_lots:
            raise RuntimeError(f"Runtime/canonical lots differ: {profile.ticker}.")

    metadata = load_portfolio_risk_metadata(root / "portfolio_risk_metadata.json")
    if set(metadata) != {item.instrument_id for item in profiles}:
        raise RuntimeError("Portfolio Risk metadata scope differs from profiles.")
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

    central = CentralOrderStore(root / "central_order_state.json").load(
        expected_account_id=account_id
    )
    if central.intents or central.blocking_intent is not None:
        raise RuntimeError("Configure gate requires empty Central order history.")
    if central.reserved_cash_kopecks:
        raise RuntimeError("Configure gate refuses Central cash reservations.")

    risk_store = RiskProfileStore(root / "risk_profiles.json")
    try:
        risk = risk_store.require_portfolio_policy("SANDBOX_EXECUTION")
    except RuntimeError as exc:
        raise RuntimeError("Portfolio Risk policy is not READY.") from exc
    if str(risk.get("account_scope") or "").strip() != account_id:
        raise RuntimeError("Portfolio Risk policy account scope mismatch.")
    if risk["portfolio_policy_status"] != "READY":
        raise RuntimeError("Portfolio Risk policy is not READY.")
    if risk["policy"].portfolio_policy_mode != "OBSERVE_ONLY":
        raise RuntimeError("Portfolio Risk policy must remain OBSERVE_ONLY.")
    if not risk["policy"].enabled:
        raise RuntimeError("Sandbox Risk policy is disabled.")

    risk_state = RiskStateStore(root / "risk_state.json").load_account(account_id)
    if risk_state.kill_switch_active or risk_state.instrument_kill_switches:
        raise RuntimeError("Risk kill switch is active.")
    if risk_state.risk_resync_required:
        raise RuntimeError("Risk resynchronization is required.")
    baseline_status = _baseline_status(risk_state)

    if EventJournal(root / "trading_events.db", read_only=True).count() != 0:
        raise RuntimeError("Configure gate requires an empty v3.9 EventJournal.")
    probe = secret_probe_factory(root)
    if not probe.available or not probe.credential_present:
        raise RuntimeError("T-Invest Sandbox credential is unavailable.")
    if not probe.secure:
        raise RuntimeError("v3.9 SHADOW requires a secure secret provider.")

    instruments = [
        {
            "instrument_id": profile.instrument_id,
            "ticker": profile.ticker,
            "class_code": profile.class_code,
            "candle_interval": profile.candle_interval,
            "lot_size": metadata[profile.instrument_id].lot_size,
            "current_lots": runtime_by_instrument[profile.instrument_id].current_lots,
            "runtime_key_fingerprint": _fingerprint(
                runtime_by_instrument[profile.instrument_id].runtime_key
            ),
            "profile_identity_hash": profile.profile_identity_hash,
            "runtime_status": "STOPPED",
        }
        for profile in sorted(profiles, key=lambda item: item.ticker)
    ]
    return {
        "account_id": account_id,
        "portfolio_revision": int(portfolio.revision),
        "policy_hash": str(risk["policy_hash"]),
        "baseline_status": baseline_status,
        "secret_provider": probe.provider,
        "instruments": instruments,
    }


def _manifest_payload(root: Path, loaded: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "configured_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(root),
        "account_fingerprint": _fingerprint(loaded["account_id"]),
        "portfolio_revision": loaded["portfolio_revision"],
        "policy_hash": loaded["policy_hash"],
        "portfolio_policy_status": "READY",
        "portfolio_policy_mode": "OBSERVE_ONLY",
        "risk_baseline_status": loaded["baseline_status"],
        "risk_baseline_initialization": "FIRST_FRESH_RISK_EVALUATION",
        "secret_provider": loaded["secret_provider"],
        "secret_provider_secure": True,
        "instruments": loaded["instruments"],
        "runtime_status": "STOPPED",
        "central_order_history_empty": True,
        "shadow_journal_empty": True,
        "strategy_proposal_created": False,
        "central_intent_created": False,
        "provider_post_authorized": False,
    }


def run(
    args: argparse.Namespace,
    *,
    secret_probe_factory: Callable[[str | Path], SecretProviderProbe] = (
        probe_secret_provider
    ),
) -> dict[str, Any]:
    root = Path(args.runtime_dir).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError("v3.9 SHADOW runtime directory does not exist.")
    loaded = _load_runtime(root, secret_probe_factory=secret_probe_factory)
    manifest_path = root / MANIFEST_NAME
    existing_manifest = None
    if manifest_path.exists():
        existing_manifest = read_json_verified(
            manifest_path,
            supported_versions={1},
        )
        expected = _manifest_payload(root, loaded)
        ignored = {"configured_at"}
        if {
            key: value for key, value in existing_manifest.items() if key not in ignored
        } != {key: value for key, value in expected.items() if key not in ignored}:
            raise RuntimeError("Existing v3.9 configure manifest no longer matches runtime.")

    result = {
        "action": args.action,
        "status": (
            "ALREADY_CONFIGURED"
            if existing_manifest is not None
            else ("PREVIEW" if args.action == "preview" else "CONFIGURED")
        ),
        "runtime_dir": str(root),
        "account_fingerprint": _fingerprint(loaded["account_id"]),
        "portfolio_revision": loaded["portfolio_revision"],
        "portfolio_policy_status": "READY",
        "portfolio_policy_mode": "OBSERVE_ONLY",
        "risk_baseline_status": loaded["baseline_status"],
        "risk_baseline_initialization": "FIRST_FRESH_RISK_EVALUATION",
        "secret_provider": loaded["secret_provider"],
        "secret_provider_secure": True,
        "instruments": loaded["instruments"],
        "runtime_status": "STOPPED",
        "strategy_proposal_created": False,
        "central_intent_created": False,
        "provider_post_authorized": False,
        "writes_performed": False,
    }
    if args.action == "preview" or existing_manifest is not None:
        return result
    if str(args.confirm or "").strip() != APPLY_CONFIRMATION:
        raise RuntimeError("apply requires the exact v3.9 runtime confirmation.")
    atomic_write_json(
        manifest_path,
        _manifest_payload(root, loaded),
        write_checksum=True,
    )
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
