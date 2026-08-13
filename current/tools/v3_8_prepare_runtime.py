from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.config_persistence import PROFILE_MODES, StrategyProfileStore
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk import RiskState
from trading_robot.risk_persistence import (
    RISK_MODES,
    RiskProfileStore,
    RiskStateStore,
)
from trading_robot.state_persistence import atomic_write_json

APPLY_CONFIRMATION = "PREPARE ISOLATED V3.8 SANDBOX RUNTIME"
MANIFEST_NAME = "v3_8_runtime_seed_manifest.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare an isolated v3.8 acceptance runtime from a verified v3.7 "
            "canonical baseline. Secrets, logs and order history are not copied."
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


def _load_profiles(
    source: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    str,
    str,
]:
    strategy_store = StrategyProfileStore(source / "strategy_profiles.json")
    strategy_document = strategy_store.load_document()
    strategies = {
        mode: profile
        for mode in PROFILE_MODES
        if (profile := strategy_store.load_profile(mode)) is not None
    }
    if "SANDBOX_EXECUTION" not in strategies:
        raise RuntimeError("Saved SANDBOX_EXECUTION strategy profile is missing.")

    risk_store = RiskProfileStore(source / "risk_profiles.json")
    risk_document = risk_store.load_document()
    risks = {
        mode: profile
        for mode in RISK_MODES
        if (profile := risk_store.load_profile(mode)) is not None
    }
    sandbox_risk = risks.get("SANDBOX_EXECUTION")
    if sandbox_risk is None:
        raise RuntimeError("Saved SANDBOX_EXECUTION Risk profile is missing.")
    if not sandbox_risk["policy"].enabled:
        raise RuntimeError("SANDBOX_EXECUTION Risk profile is disabled.")
    return (
        strategies,
        risks,
        str(strategy_document["last_selected_mode"]),
        str(risk_document["last_selected_mode"]),
    )


def _write_runtime(
    staging: Path,
    *,
    source: Path,
    target: Path,
    account_id: str,
    portfolio_state: Any,
    strategies: dict[str, dict[str, Any]],
    risks: dict[str, dict[str, Any]],
    selected_strategy_mode: str,
    selected_risk_mode: str,
) -> None:
    PortfolioRepository(staging / "portfolio_state.json").save(portfolio_state)

    strategy_store = StrategyProfileStore(staging / "strategy_profiles.json")
    for mode, profile in strategies.items():
        strategy_store.save_profile(
            mode,
            profile["config"],
            select=mode == selected_strategy_mode,
        )

    risk_store = RiskProfileStore(staging / "risk_profiles.json")
    for mode, profile in risks.items():
        risk_store.save_profile(
            mode,
            profile["policy"],
            select=mode == selected_risk_mode,
            account_scope=account_id,
            source="V3_8_ISOLATED_RUNTIME_BOOTSTRAP",
        )

    RiskStateStore(staging / "risk_state.json").save_account(
        account_id,
        RiskState(),
    )
    CentralOrderStore(staging / "central_order_state.json").initialize(account_id)
    sandbox_strategy = strategies["SANDBOX_EXECUTION"]
    sandbox_risk = risks["SANDBOX_EXECUTION"]
    atomic_write_json(
        staging / MANIFEST_NAME,
        {
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_runtime_dir": str(source),
            "runtime_dir": str(target),
            "account_fingerprint": _fingerprint(account_id),
            "portfolio_revision": int(portfolio_state.revision),
            "strategy_config_hash": sandbox_strategy["config_hash"],
            "risk_policy_hash": sandbox_risk["policy_hash"],
            "risk_account_scoped": True,
            "central_order_history_empty": True,
            "risk_counters_empty": True,
            "secrets_copied": False,
            "logs_copied": False,
            "broker_order_history_copied": False,
        },
        write_checksum=True,
    )


def _validate_runtime(target: Path, *, account_id: str) -> None:
    PortfolioRepository(target / "portfolio_state.json").load(
        expected_account_id=account_id
    )
    strategy = StrategyProfileStore(target / "strategy_profiles.json").load_profile(
        "SANDBOX_EXECUTION"
    )
    if strategy is None:
        raise RuntimeError("Prepared runtime lost its Sandbox strategy profile.")
    risk = RiskProfileStore(target / "risk_profiles.json").require_profile(
        "SANDBOX_EXECUTION"
    )
    if risk.get("account_scope") != account_id:
        raise RuntimeError("Prepared runtime Risk account scope mismatch.")
    risk_state = RiskStateStore(target / "risk_state.json").load_account(account_id)
    if risk_state != RiskState():
        raise RuntimeError("Prepared runtime Risk counters are not empty.")
    central = CentralOrderStore(target / "central_order_state.json").load(
        expected_account_id=account_id
    )
    if central.intents:
        raise RuntimeError("Prepared runtime central-order history is not empty.")
    if (target / ".env").exists():
        raise RuntimeError("Prepared runtime must not contain a copied .env file.")


def run(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source_runtime_dir).expanduser().resolve()
    target = Path(args.runtime_dir).expanduser().resolve()
    if _paths_overlap(source, target):
        raise RuntimeError("Source and isolated runtime directories must not overlap.")
    if not source.is_dir():
        raise RuntimeError("Source runtime directory does not exist.")
    for name in (
        "portfolio_state.json",
        "portfolio_state.json.sha256",
        "strategy_profiles.json",
        "risk_profiles.json",
    ):
        if not (source / name).is_file():
            raise RuntimeError(f"Source runtime prerequisite is missing: {name}")

    portfolio_state = PortfolioRepository(source / "portfolio_state.json").load()
    account_id = str(portfolio_state.account_id or "").strip()
    if not account_id:
        raise RuntimeError("Source canonical portfolio has no account scope.")
    strategies, risks, strategy_mode, risk_mode = _load_profiles(source)
    source_scope = str(
        risks["SANDBOX_EXECUTION"].get("account_scope") or ""
    ).strip()
    if source_scope and source_scope != account_id:
        raise RuntimeError(
            "Source SANDBOX_EXECUTION Risk profile belongs to another account."
        )

    target_existed_before = target.exists()
    result = {
        "action": args.action,
        "status": "PREVIEW" if args.action == "preview" else "PREPARED",
        "source_runtime_dir": str(source),
        "runtime_dir": str(target),
        "target_existed_before": target_existed_before,
        "target_exists": target_existed_before,
        "account_fingerprint": _fingerprint(account_id),
        "portfolio_revision": int(portfolio_state.revision),
        "source_risk_scope": "MATCHING" if source_scope else "UNSCOPED",
        "risk_policy_hash": risks["SANDBOX_EXECUTION"]["policy_hash"],
        "strategy_config_hash": strategies["SANDBOX_EXECUTION"]["config_hash"],
        "planned_files": [
            "portfolio_state.json",
            "strategy_profiles.json",
            "risk_profiles.json",
            "risk_state.json",
            "central_order_state.json",
            MANIFEST_NAME,
        ],
        "secrets_copied": False,
        "writes_performed": args.action == "apply",
    }
    if args.action == "preview":
        return result
    if str(args.confirm or "").strip() != APPLY_CONFIRMATION:
        raise RuntimeError("apply requires the exact isolated-runtime confirmation.")
    if target.exists():
        raise RuntimeError("Isolated runtime target already exists; overwrite refused.")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent)
    ).resolve()
    try:
        _write_runtime(
            staging,
            source=source,
            target=target,
            account_id=account_id,
            portfolio_state=portfolio_state,
            strategies=strategies,
            risks=risks,
            selected_strategy_mode=strategy_mode,
            selected_risk_mode=risk_mode,
        )
        _validate_runtime(staging, account_id=account_id)
        os.replace(staging, target)
        _validate_runtime(target, account_id=account_id)
    except Exception:
        if staging.exists() and staging.parent == target.parent:
            shutil.rmtree(staging)
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
    sys.exit(main())
