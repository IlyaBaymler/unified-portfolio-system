from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trading_robot.central_order_manager import CentralOrderStore
from trading_robot.config_persistence import StrategyProfileStore
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.multi_instrument_config import (
    MAX_V3_8_INSTRUMENTS,
    MultiInstrumentProfile,
    MultiInstrumentProfileStore,
)
from trading_robot.risk_persistence import RiskProfileStore
from trading_robot.secret_provider import preferred_secret_provider
from trading_robot.tbank_sandbox import TBankSandboxClient

TOKEN_KEY = "TBANK_SANDBOX_TOKEN"
APPLY_CONFIRMATION = "CONFIGURE V3.8 SANDBOX RUNTIMES"
V3_8_INTERVALS = frozenset(
    {
        "CANDLE_INTERVAL_15_MIN",
        "CANDLE_INTERVAL_30_MIN",
        "CANDLE_INTERVAL_HOUR",
    }
)
MAX_V3_8_ORDER_LOTS = 100


def _fingerprint(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    ticker: str
    class_code: str
    candle_interval: str

    @classmethod
    def parse(cls, raw: str) -> InstrumentSpec:
        parts = [part.strip().upper() for part in str(raw).split(",")]
        if len(parts) != 3 or any(not part for part in parts):
            raise ValueError(
                "--instrument must be TICKER,CLASS_CODE,CANDLE_INTERVAL."
            )
        ticker, class_code, interval = parts
        if interval not in V3_8_INTERVALS:
            raise ValueError(
                "v3.8 instrument interval must be 15_MIN, 30_MIN or HOUR."
            )
        return cls(ticker, class_code, interval)

    def to_dict(self) -> dict[str, str]:
        return {
            "ticker": self.ticker,
            "class_code": self.class_code,
            "candle_interval": self.candle_interval,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "First-time v3.8 Sandbox multi-instrument profile bootstrap. "
            "It performs read-only instrument lookup and has no order method."
        )
    )
    parser.add_argument("action", choices=("preview", "apply"))
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument(
        "--instrument",
        action="append",
        required=True,
        help="TICKER,CLASS_CODE,CANDLE_INTERVAL_*; repeat 2 or 3 times",
    )
    parser.add_argument("--confirm", default="")
    parser.add_argument("--connect-timeout", type=float, default=8.0)
    parser.add_argument("--read-timeout", type=float, default=25.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--max-order-lots",
        type=int,
        help=(
            "Optional static per-profile position limit for the configured "
            "v3.8 execution set. Defaults to the saved Sandbox strategy profile."
        ),
    )
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
    specs = tuple(InstrumentSpec.parse(raw) for raw in args.instrument)
    if not 2 <= len(specs) <= MAX_V3_8_INSTRUMENTS:
        raise RuntimeError("v3.8 bootstrap requires exactly 2 or 3 instruments.")
    symbols = {(item.ticker, item.class_code) for item in specs}
    if len(symbols) != len(specs):
        raise RuntimeError("v3.8 bootstrap contains a duplicate instrument symbol.")
    if args.connect_timeout <= 0 or args.read_timeout <= 0:
        raise RuntimeError("Configuration timeouts must be positive.")
    if not 0 <= args.max_retries <= 5:
        raise RuntimeError("--max-retries must be between 0 and 5.")
    if args.max_order_lots is not None and not (
        1 <= args.max_order_lots <= MAX_V3_8_ORDER_LOTS
    ):
        raise RuntimeError(
            "--max-order-lots must be between 1 and "
            f"{MAX_V3_8_ORDER_LOTS}."
        )

    strategy_path = runtime_dir / "strategy_profiles.json"
    central_path = runtime_dir / "central_order_state.json"
    portfolio_path = runtime_dir / "portfolio_state.json"
    for path in (strategy_path, central_path, portfolio_path):
        if not path.is_file():
            raise RuntimeError(f"Runtime prerequisite is missing: {path.name}")
    for path in (central_path, portfolio_path):
        if not path.with_name(path.name + ".sha256").is_file():
            raise RuntimeError(
                f"Runtime prerequisite checksum is missing: {path.name}"
            )
    central = CentralOrderStore(central_path).load(
        expected_account_id=account_id
    )
    if central.intents:
        raise RuntimeError(
            "First-time v3.8 bootstrap requires an empty central-order history."
        )
    base = StrategyProfileStore(strategy_path).load_profile(
        "SANDBOX_EXECUTION"
    )
    if base is None:
        raise RuntimeError("Saved SANDBOX_EXECUTION strategy profile is missing.")
    risk = RiskProfileStore(runtime_dir / "risk_profiles.json").require_profile(
        "SANDBOX_EXECUTION"
    )
    if not risk["policy"].enabled:
        raise RuntimeError("SANDBOX_EXECUTION Risk profile is disabled.")
    if str(risk.get("account_scope") or "").strip() != account_id:
        raise RuntimeError(
            "SANDBOX_EXECUTION Risk profile must match the selected account."
        )

    profile_path = runtime_dir / "multi_instrument_profiles.json"
    runtime_path = runtime_dir / "instrument_runtimes.json"
    existing_profile = profile_path.exists()
    existing_runtime = runtime_path.exists()
    if args.action == "apply":
        if str(args.confirm or "").strip() != APPLY_CONFIRMATION:
            raise RuntimeError("apply requires the exact v3.8 configuration confirmation.")
        if existing_runtime:
            raise RuntimeError(
                "First-time bootstrap refuses to overwrite existing v3.8 profiles/runtimes."
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
    profiles = []
    with client_factory(
        token,
        connect_timeout_seconds=float(args.connect_timeout),
        read_timeout_seconds=float(args.read_timeout),
        max_retries=int(args.max_retries),
        ca_bundle_path=ca_bundle,
    ) as client:
        for spec in specs:
            instrument = client.find_instrument(spec.ticker, spec.class_code)
            instrument_id = str(client.instrument_id(instrument)).strip()
            if not instrument_id:
                raise RuntimeError(
                    f"Broker returned no instrument UID for {spec.ticker}."
                )
            strategy_profile = dict(base["config"])
            strategy_profile.update(spec.to_dict())
            if args.max_order_lots is not None:
                strategy_profile["max_order_lots"] = int(args.max_order_lots)
            profiles.append(
                MultiInstrumentProfile(
                    instrument_id=instrument_id,
                    strategy_profile=strategy_profile,
                )
            )

    instrument_ids = {item.instrument_id for item in profiles}
    if len(instrument_ids) != len(profiles):
        raise RuntimeError("Broker lookup resolved duplicate instrument UIDs.")
    ordered = tuple(sorted(profiles, key=lambda item: item.instrument_id))
    result = {
        "action": args.action,
        "status": "PREVIEW" if args.action == "preview" else "CONFIGURED",
        "account_fingerprint": _fingerprint(account_id),
        "instruments": [
            {
                "instrument_id": item.instrument_id,
                "ticker": item.ticker,
                "class_code": item.class_code,
                "candle_interval": item.candle_interval,
                "max_order_lots": int(
                    item.strategy_profile["max_order_lots"]
                ),
                "strategy_profile_hash": item.strategy_profile_hash,
                "profile_identity_hash": item.profile_identity_hash,
            }
            for item in ordered
        ],
        "writes_performed": args.action == "apply",
    }
    if args.action == "preview":
        return result

    store = MultiInstrumentProfileStore(profile_path)
    recovered_profile = False
    if existing_profile:
        if store.load_mode("SANDBOX_EXECUTION") != ordered:
            raise RuntimeError(
                "Existing v3.8 profile does not match this recovery request."
            )
        recovered_profile = True
    else:
        store.save_mode("SANDBOX_EXECUTION", ordered)
    runtimes = store.bootstrap_runtime_registry(
        mode="SANDBOX_EXECUTION",
        account_id=account_id,
        runtime_store=InstrumentRuntimeStore(runtime_path),
    )
    result["runtime_statuses"] = [
        {
            "instrument_id": item.config.instrument_id,
            "runtime_key_fingerprint": _fingerprint(item.runtime_key),
            "status": item.status,
        }
        for item in runtimes
    ]
    result["recovered_matching_profile"] = recovered_profile
    return result


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
