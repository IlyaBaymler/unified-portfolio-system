from __future__ import annotations

import argparse
from dataclasses import replace
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from trading_robot.bot import BotConfig, SandboxTradingBot
from trading_robot.config_persistence import (
    StrategyProfileError,
    StrategyProfileStore,
    bot_config_to_profile,
    profile_to_bot_config,
)
from trading_robot.logging_setup import configure_file_logging
from trading_robot.risk_runtime import RiskRuntimeAdapter
from trading_robot.tbank_sandbox import TBankSandboxClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Long-only trading robot for T-Invest Sandbox."
    )
    parser.add_argument("--once", action="store_true", help="Run one iteration.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Allow Sandbox orders. Also requires "
            "ARM_SANDBOX_TRADING=YES in .env."
        ),
    )
    parser.add_argument(
        "--init-account",
        action="store_true",
        help="Create and fund a Sandbox account if none exists.",
    )
    return parser.parse_args()


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)).replace(",", "."))


def env_optional_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip().replace(",", ".")
    if not raw or raw.upper() in {"NONE", "OFF", "NO", "0"}:
        return None
    return float(raw)


def env_strategy_list(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().upper() in {"1", "TRUE", "YES", "ON"}


def _legacy_bot_config_from_env(
    *,
    state_file: str,
    journal_file: str,
    heartbeat_log_seconds: int,
    slow_cycle_seconds: float,
) -> BotConfig:
    """Build the one-time migration profile from legacy ROBOT_* settings."""
    target_volatility_percent = env_optional_float(
        "ROBOT_TARGET_VOLATILITY_PERCENT"
    )
    return BotConfig(
        ticker=os.getenv("ROBOT_TICKER", "SBER"),
        class_code=os.getenv("ROBOT_CLASS_CODE", "TQBR"),
        candle_interval=os.getenv(
            "ROBOT_CANDLE_INTERVAL", "CANDLE_INTERVAL_HOUR"
        ),
        primary_strategy=os.getenv("ROBOT_PRIMARY_STRATEGY", "sma"),
        shadow_strategies=env_strategy_list("ROBOT_SHADOW_STRATEGIES"),
        fast_window=env_int("ROBOT_FAST_WINDOW", 20),
        slow_window=env_int("ROBOT_SLOW_WINDOW", 50),
        sma_hysteresis_percent=(
            env_float("ROBOT_SMA_HYSTERESIS_PERCENT", 0.2) / 100.0
        ),
        donchian_entry_window=env_int("ROBOT_DONCHIAN_ENTRY_WINDOW", 55),
        donchian_exit_window=env_int("ROBOT_DONCHIAN_EXIT_WINDOW", 20),
        donchian_atr_window=env_int("ROBOT_DONCHIAN_ATR_WINDOW", 20),
        donchian_trailing_stop_atr=env_float(
            "ROBOT_DONCHIAN_TRAILING_STOP_ATR", 3.0
        ),
        ensemble_sma_fast=env_int("ROBOT_ENSEMBLE_SMA_FAST", 50),
        ensemble_sma_slow=env_int("ROBOT_ENSEMBLE_SMA_SLOW", 200),
        ensemble_momentum_window=env_int(
            "ROBOT_ENSEMBLE_MOMENTUM_WINDOW", 126
        ),
        ensemble_breakout_window=env_int(
            "ROBOT_ENSEMBLE_BREAKOUT_WINDOW", 100
        ),
        ensemble_vote_threshold=env_int(
            "ROBOT_ENSEMBLE_VOTE_THRESHOLD", 3
        ),
        annual_target_volatility=(
            None
            if target_volatility_percent is None
            else target_volatility_percent / 100.0
        ),
        volatility_window=env_int("ROBOT_VOLATILITY_WINDOW", 20),
        max_strategy_weight=(
            env_float("ROBOT_MAX_STRATEGY_WEIGHT_PERCENT", 100.0) / 100.0
        ),
        lookback_days=env_int("ROBOT_LOOKBACK_DAYS", 30),
        poll_seconds=env_int("ROBOT_POLL_SECONDS", 300),
        max_order_lots=env_int("ROBOT_MAX_ORDER_LOTS", 1),
        order_type=os.getenv("ROBOT_ORDER_TYPE", "BESTPRICE"),
        time_in_force=os.getenv("ROBOT_TIME_IN_FORCE", "FILL_AND_KILL"),
        check_trading_status=True,
        failure_threshold=env_int("ROBOT_FAILURE_THRESHOLD", 3),
        circuit_open_seconds=env_int("ROBOT_CIRCUIT_OPEN_SECONDS", 60),
        circuit_max_open_seconds=env_int(
            "ROBOT_CIRCUIT_MAX_OPEN_SECONDS", 900
        ),
        max_signal_age_seconds=env_int("ROBOT_MAX_SIGNAL_AGE_SECONDS", 0),
        reconcile_attempts=env_int("ROBOT_RECONCILE_ATTEMPTS", 3),
        reconcile_delay_seconds=env_float(
            "ROBOT_RECONCILE_DELAY_SECONDS", 0.5
        ),
        portfolio_reconcile_interval_seconds=env_int(
            "ROBOT_PORTFOLIO_RECONCILE_INTERVAL_SECONDS", 900
        ),
        market_idle_enabled=env_bool("ROBOT_MARKET_IDLE_ENABLED", True),
        market_status_check_seconds=env_int(
            "ROBOT_MARKET_STATUS_CHECK_SECONDS", 300
        ),
        market_idle_poll_seconds=env_int(
            "ROBOT_MARKET_IDLE_POLL_SECONDS", 300
        ),
        market_idle_reconcile_seconds=env_int(
            "ROBOT_MARKET_IDLE_RECONCILE_SECONDS", 1800
        ),
        market_idle_heartbeat_seconds=env_int(
            "ROBOT_MARKET_IDLE_HEARTBEAT_SECONDS", 1800
        ),
        dry_run=True,
        state_file=state_file,
        journal_file=journal_file,
        heartbeat_log_seconds=heartbeat_log_seconds,
        slow_cycle_seconds=slow_cycle_seconds,
    )


def load_cli_bot_config(
    *,
    execute: bool,
) -> tuple[BotConfig, float, float, str]:
    """Load the selected checksummed profile for CLI dry-run/execution.

    Legacy ROBOT_* values are used only to create the first DRY_RUN profile.
    Sandbox execution never silently falls back to .env defaults.
    """
    state_file = os.getenv("ROBOT_STATE_FILE", "robot_state.json")
    journal_file = os.getenv("ROBOT_JOURNAL_FILE", "trading_events.db")
    profile_file = os.getenv(
        "ROBOT_STRATEGY_PROFILE_FILE", "strategy_profiles.json"
    )
    heartbeat = env_int("ROBOT_HEARTBEAT_LOG_SECONDS", 1800)
    slow_cycle = env_float("ROBOT_SLOW_CYCLE_SECONDS", 10.0)
    connect_default = env_float("TBANK_CONNECT_TIMEOUT_SECONDS", 8.0)
    read_default = env_float("TBANK_READ_TIMEOUT_SECONDS", 25.0)
    mode = "SANDBOX_EXECUTION" if execute else "DRY_RUN"
    store = StrategyProfileStore(profile_file)

    try:
        if not Path(profile_file).exists():
            if execute:
                raise SystemExit(
                    "strategy_profiles.json is required for Sandbox execution. "
                    "Open the GUI, verify SANDBOX_EXECUTION and save the profile."
                )
            legacy = _legacy_bot_config_from_env(
                state_file=state_file,
                journal_file=journal_file,
                heartbeat_log_seconds=heartbeat,
                slow_cycle_seconds=slow_cycle,
            )
            store.save_profile(
                "DRY_RUN",
                bot_config_to_profile(
                    legacy,
                    connect_timeout_seconds=connect_default,
                    read_timeout_seconds=read_default,
                ),
                select=True,
            )
        loaded = store.load_profile(mode)
    except StrategyProfileError as exc:
        raise SystemExit(str(exc)) from exc

    if loaded is None:
        raise SystemExit(
            f"Profile {mode} is not saved. Open the GUI, verify the mode and "
            "save its PRIMARY/SHADOW configuration first."
        )
    profile = loaded["config"]
    config = profile_to_bot_config(
        profile,
        mode=mode,
        state_file=state_file,
        journal_file=journal_file,
        heartbeat_log_seconds=heartbeat,
        slow_cycle_seconds=slow_cycle,
    )
    config = replace(
        config,
        market_idle_enabled=env_bool("ROBOT_MARKET_IDLE_ENABLED", True),
        market_status_check_seconds=env_int(
            "ROBOT_MARKET_STATUS_CHECK_SECONDS", 300
        ),
        market_idle_poll_seconds=env_int(
            "ROBOT_MARKET_IDLE_POLL_SECONDS", 300
        ),
        market_idle_reconcile_seconds=env_int(
            "ROBOT_MARKET_IDLE_RECONCILE_SECONDS", 1800
        ),
        market_idle_heartbeat_seconds=env_int(
            "ROBOT_MARKET_IDLE_HEARTBEAT_SECONDS", 1800
        ),
    )
    connect_timeout = float(
        profile.get("connect_timeout_seconds", connect_default)
    )
    read_timeout = float(profile.get("read_timeout_seconds", read_default))
    return config, connect_timeout, read_timeout, str(loaded["config_hash"])


def main() -> None:
    load_dotenv()
    args = parse_args()
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    configure_file_logging(
        ".",
        compact_level=getattr(logging, level_name, logging.INFO),
        debug_enabled=True,
        console=True,
    )

    token = os.getenv("TBANK_SANDBOX_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Set TBANK_SANDBOX_TOKEN in .env. Never commit the token."
        )

    double_armed = (
        args.execute
        and os.getenv("ARM_SANDBOX_TRADING", "").upper() == "YES"
    )
    config, connect_timeout, read_timeout, profile_hash = load_cli_bot_config(
        execute=double_armed
    )
    logging.getLogger(__name__).info(
        "CLI strategy profile loaded: mode=%s hash=%s primary=%s shadows=%s",
        "SANDBOX_EXECUTION" if double_armed else "DRY_RUN",
        profile_hash[:16],
        config.primary_strategy,
        ",".join(config.shadow_strategies) or "—",
    )

    ca_bundle = os.getenv("TBANK_CA_BUNDLE", "").strip() or None
    with TBankSandboxClient(
        token,
        ca_bundle_path=ca_bundle,
        connect_timeout_seconds=connect_timeout,
        read_timeout_seconds=read_timeout,
    ) as api:
        accounts = api.get_accounts()
        if not accounts:
            if not args.init_account:
                raise SystemExit(
                    "No Sandbox account. Run once with --init-account."
                )
            account_id = api.open_account()
            api.pay_in(
                account_id,
                float(os.getenv("SANDBOX_INITIAL_RUB", "1000000")),
            )
        else:
            requested = os.getenv("TBANK_SANDBOX_ACCOUNT_ID", "").strip()
            account_id = requested or str(accounts[0]["id"])

        if double_armed and config.max_order_lots != 1:
            raise SystemExit(
                "v3.7.0 Sandbox Execution is limited to exactly 1 lot."
            )
        risk_runtime = RiskRuntimeAdapter.from_directory(
            Path(__file__).resolve().parent,
            account_id=account_id,
            mode=(
                "SANDBOX_EXECUTION" if double_armed else "DRY_RUN"
            ),
            auto_create_dry_run_profile=not double_armed,
        )
        robot = SandboxTradingBot(
            api,
            account_id,
            config,
            allow_execution=double_armed,
            risk_runtime=risk_runtime,
            require_risk_runtime_for_execution=double_armed,
        )
        if args.once:
            try:
                print(json.dumps(robot.run_once(), ensure_ascii=False, indent=2))
            finally:
                robot.end_session("single_cycle_completed")
        else:
            robot.run_forever()


if __name__ == "__main__":
    main()
