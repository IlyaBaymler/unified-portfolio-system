from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import v3_9_configure_shadow_runtimes as shadow_configure
from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.journal import EventJournal
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.secret_provider import SecretProviderProbe, probe_secret_provider
from trading_robot.state_persistence import atomic_write_json, read_json_verified

APPLY_CONFIRMATION = "CONFIGURE V3.9 ENFORCED RUNTIME"
MANIFEST_NAME = "v3_9_enforced_runtime_manifest.json"
SHADOW_CONFIG_MANIFEST_NAME = "v3_9_shadow_runtime_config_manifest.json"
SHADOW_START_MANIFEST_NAME = "v3_9_shadow_runtime_start_manifest.json"
PORTFOLIO_LIMIT_FIELDS = (
    "max_gross_exposure_rub",
    "max_gross_exposure_fraction",
    "max_net_exposure_fraction",
    "max_instrument_concentration_fraction",
    "max_strategy_concentration_fraction",
    "max_asset_class_concentration_fraction",
    "asset_class_concentration_limits",
    "max_open_positions",
    "min_cash_reserve_fraction",
    "max_daily_turnover_fraction",
    "max_price_age_seconds",
    "portfolio_warning_utilization_fraction",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and explicitly activate authoritative v3.9 Portfolio Risk "
            "in a fresh isolated runtime. This command never creates a Strategy "
            "proposal, Central intent or provider POST."
        )
    )
    parser.add_argument("action", choices=("preview", "apply"))
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--confirm", default="")
    return parser.parse_args(argv)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy_limits(policy: RiskPolicy) -> dict[str, Any]:
    payload = asdict(policy)
    return {name: payload[name] for name in PORTFOLIO_LIMIT_FIELDS}


def _validate_shadow_manifest(
    root: Path,
    *,
    account_id: str,
    portfolio_revision: int,
    policy_hash: str,
) -> dict[str, Any]:
    config_path = root / SHADOW_CONFIG_MANIFEST_NAME
    if not config_path.is_file() or not config_path.with_name(
        config_path.name + ".sha256"
    ).is_file():
        raise RuntimeError("Checksummed v3.9 SHADOW configure manifest is missing.")
    config = read_json_verified(config_path, supported_versions={1})
    if Path(str(config.get("runtime_dir") or "")).resolve() != root:
        raise RuntimeError("SHADOW configure manifest belongs to another runtime.")
    if config.get("account_fingerprint") != _fingerprint(account_id):
        raise RuntimeError("SHADOW configure manifest account scope mismatch.")
    if int(config.get("portfolio_revision", -1)) != int(portfolio_revision):
        raise RuntimeError("Canonical portfolio changed after SHADOW configuration.")
    if config.get("policy_hash") != policy_hash:
        raise RuntimeError("Risk policy changed after SHADOW configuration.")
    if config.get("portfolio_policy_mode") != "OBSERVE_ONLY":
        raise RuntimeError("SHADOW configure manifest mode is not OBSERVE_ONLY.")
    if config.get("runtime_status") != "STOPPED":
        raise RuntimeError("SHADOW configure manifest did not seal STOPPED runtimes.")
    if config.get("central_intent_created") is not False:
        raise RuntimeError("SHADOW configure manifest contains a Central intent.")
    if config.get("provider_post_authorized") is not False:
        raise RuntimeError("SHADOW configure manifest authorized a provider POST.")
    return config


def _load_observe_runtime(
    root: Path,
    *,
    secret_probe_factory: Callable[[str | Path], SecretProviderProbe],
) -> dict[str, Any]:
    if (root / SHADOW_START_MANIFEST_NAME).exists():
        raise RuntimeError(
            "M4 configuration requires a fresh runtime that has not passed START."
        )
    loaded = shadow_configure._load_runtime(
        root,
        secret_probe_factory=secret_probe_factory,
    )
    profile = RiskProfileStore(root / "risk_profiles.json").require_portfolio_policy(
        "SANDBOX_EXECUTION"
    )
    policy = profile["policy"]
    if policy.portfolio_policy_mode != "OBSERVE_ONLY":
        raise RuntimeError("Portfolio Risk policy must start as OBSERVE_ONLY.")
    config = _validate_shadow_manifest(
        root,
        account_id=loaded["account_id"],
        portfolio_revision=loaded["portfolio_revision"],
        policy_hash=loaded["policy_hash"],
    )
    target_policy = replace(policy, portfolio_policy_mode="ENFORCED")
    return {
        **loaded,
        "source_policy": policy,
        "target_policy": target_policy,
        "source_policy_hash": policy.policy_hash,
        "target_policy_hash": target_policy.policy_hash,
        "shadow_config_sha256": _sha256(root / SHADOW_CONFIG_MANIFEST_NAME),
        "shadow_configured_at": config.get("configured_at"),
    }


def _validate_active_runtime(
    root: Path,
    manifest: dict[str, Any],
    *,
    secret_probe_factory: Callable[[str | Path], SecretProviderProbe],
) -> dict[str, Any]:
    portfolio = PortfolioRepository(root / "portfolio_state.json").load()
    account_id = str(portfolio.account_id or "").strip()
    if not account_id:
        raise RuntimeError("Canonical portfolio has no account scope.")
    if Path(str(manifest.get("runtime_dir") or "")).resolve() != root:
        raise RuntimeError("M4 activation manifest belongs to another runtime.")
    if manifest.get("account_fingerprint") != _fingerprint(account_id):
        raise RuntimeError("M4 activation manifest account scope mismatch.")
    if int(manifest.get("portfolio_revision", -1)) != int(portfolio.revision):
        raise RuntimeError("Canonical portfolio changed after M4 configuration.")

    profile = RiskProfileStore(root / "risk_profiles.json").require_portfolio_policy(
        "SANDBOX_EXECUTION"
    )
    policy = profile["policy"]
    if str(profile.get("account_scope") or "").strip() != account_id:
        raise RuntimeError("M4 Risk profile account scope mismatch.")
    if policy.portfolio_policy_mode != "ENFORCED":
        raise RuntimeError("M4 Risk profile is not ENFORCED.")
    if manifest.get("policy_hash") != policy.policy_hash:
        raise RuntimeError("M4 activation manifest policy hash mismatch.")

    profiles = MultiInstrumentProfileStore(
        root / "multi_instrument_profiles.json"
    ).load_mode("SANDBOX_EXECUTION")
    runtimes = InstrumentRuntimeStore(root / "instrument_runtimes.json").load(
        expected_account_id=account_id
    )
    if len(profiles) != len(runtimes) or not 2 <= len(runtimes) <= 3:
        raise RuntimeError("M4 instrument profile/runtime counts differ.")
    if any(item.status != "STOPPED" for item in runtimes):
        raise RuntimeError("M4 configuration requires all InstrumentRuntimes STOPPED.")
    if any(item.pending_order_ids for item in runtimes):
        raise RuntimeError("M4 configuration refuses pending runtime orders.")
    central = CentralOrderStore(root / "central_order_state.json").load(
        expected_account_id=account_id
    )
    if central.intents or central.reserved_cash_kopecks:
        raise RuntimeError("M4 configuration requires empty Central state.")
    risk_state = RiskStateStore(root / "risk_state.json").load_account(account_id)
    if (
        risk_state.kill_switch_active
        or risk_state.instrument_kill_switches
        or risk_state.risk_resync_required
    ):
        raise RuntimeError("M4 Risk state is blocked or requires resynchronization.")
    if EventJournal(root / "trading_events.db", read_only=True).count() != 0:
        raise RuntimeError("M4 configuration requires an empty EventJournal.")
    probe = secret_probe_factory(root)
    if not probe.available or not probe.credential_present or not probe.secure:
        raise RuntimeError("M4 requires an available secure Sandbox credential.")
    return {
        "account_id": account_id,
        "portfolio_revision": int(portfolio.revision),
        "policy_hash": policy.policy_hash,
        "target_policy": policy,
        "baseline_status": manifest.get("risk_baseline_status"),
        "secret_provider": probe.provider,
        "instruments": manifest.get("instruments", []),
        "pre_activation_backup": manifest.get("pre_activation_backup"),
    }


def _manifest_payload(
    root: Path,
    loaded: dict[str, Any],
    *,
    activation_status: str,
    configured_at: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "configured_at": configured_at,
        "activation_status": activation_status,
        "runtime_dir": str(root),
        "account_fingerprint": _fingerprint(loaded["account_id"]),
        "portfolio_revision": loaded["portfolio_revision"],
        "source_policy_hash": loaded["source_policy_hash"],
        "policy_hash": loaded["target_policy_hash"],
        "portfolio_policy_status": "READY",
        "portfolio_policy_mode": "ENFORCED",
        "portfolio_limits": _policy_limits(loaded["target_policy"]),
        "risk_baseline_status": loaded["baseline_status"],
        "secret_provider": loaded["secret_provider"],
        "secret_provider_secure": True,
        "instruments": loaded["instruments"],
        "runtime_status": "STOPPED",
        "central_order_history_empty": True,
        "event_journal_empty": True,
        "strategy_proposal_created": False,
        "central_intent_created": False,
        "provider_post_authorized": False,
        "shadow_config_manifest_sha256": loaded["shadow_config_sha256"],
        "shadow_configured_at": loaded["shadow_configured_at"],
        "pre_activation_backup": loaded.get("pre_activation_backup"),
    }


def _result(
    args: argparse.Namespace,
    root: Path,
    loaded: dict[str, Any],
    *,
    status: str,
    writes_performed: bool,
) -> dict[str, Any]:
    policy = loaded["target_policy"]
    return {
        "action": args.action,
        "status": status,
        "runtime_dir": str(root),
        "account_fingerprint": _fingerprint(loaded["account_id"]),
        "portfolio_revision": loaded["portfolio_revision"],
        "policy_hash": policy.policy_hash,
        "portfolio_policy_status": "READY",
        "portfolio_policy_mode": "ENFORCED",
        "portfolio_limits": _policy_limits(policy),
        "risk_baseline_status": loaded["baseline_status"],
        "secret_provider": loaded["secret_provider"],
        "secret_provider_secure": True,
        "instruments": loaded["instruments"],
        "runtime_status": "STOPPED",
        "strategy_proposal_created": False,
        "central_intent_created": False,
        "provider_post_authorized": False,
        "pre_activation_backup": loaded.get("pre_activation_backup"),
        "writes_performed": writes_performed,
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
        raise RuntimeError("v3.9 isolated runtime directory does not exist.")
    manifest_path = root / MANIFEST_NAME
    existing = (
        read_json_verified(manifest_path, supported_versions={1})
        if manifest_path.exists()
        else None
    )
    if existing is not None and existing.get("activation_status") == "ACTIVE":
        active = _validate_active_runtime(
            root,
            existing,
            secret_probe_factory=secret_probe_factory,
        )
        return _result(
            args,
            root,
            active,
            status="ALREADY_CONFIGURED",
            writes_performed=False,
        )
    if existing is not None:
        raise RuntimeError(
            "M4 activation manifest is incomplete; restore the pre-activation "
            "runtime backup before retrying."
        )

    loaded = _load_observe_runtime(
        root,
        secret_probe_factory=secret_probe_factory,
    )
    if args.action == "preview":
        return _result(
            args,
            root,
            loaded,
            status="PREVIEW",
            writes_performed=False,
        )
    if str(args.confirm or "").strip() != APPLY_CONFIRMATION:
        raise RuntimeError("apply requires the exact v3.9 M4 confirmation.")

    configured_now = datetime.now(timezone.utc)
    configured_at = configured_now.isoformat()
    backup_path = (
        root.parent
        / "backups"
        / (
            "v3_9_m4_pre_activation_"
            + configured_now.strftime("%Y%m%dT%H%M%S%fZ")
            + ".zip"
        )
    )
    backup_manager = RuntimeBackupManager(root, app_version="v3.9-m4-pre-activation")
    created_backup = backup_manager.create_backup(backup_path)
    verification = backup_manager.verify_backup(created_backup)
    if not verification.valid:
        raise RuntimeError("Pre-activation runtime backup verification failed.")
    loaded = {**loaded, "pre_activation_backup": str(created_backup)}
    atomic_write_json(
        manifest_path,
        _manifest_payload(
            root,
            loaded,
            activation_status="PENDING",
            configured_at=configured_at,
        ),
        write_checksum=True,
    )
    saved = RiskProfileStore(root / "risk_profiles.json").confirm_portfolio_policy(
        "SANDBOX_EXECUTION",
        loaded["target_policy"],
        confirmation="CONFIRM PORTFOLIO RISK POLICY",
        select=True,
        account_scope=loaded["account_id"],
        source="V3_9_M4_OPERATOR",
    )
    if saved["policy_hash"] != loaded["target_policy_hash"]:
        raise RuntimeError("Persisted M4 policy hash differs from the preview.")
    atomic_write_json(
        manifest_path,
        _manifest_payload(
            root,
            loaded,
            activation_status="ACTIVE",
            configured_at=configured_at,
        ),
        write_checksum=True,
    )
    return _result(
        args,
        root,
        loaded,
        status="CONFIGURED",
        writes_performed=True,
    )


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
