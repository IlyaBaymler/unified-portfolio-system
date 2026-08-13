from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping
from uuid import uuid4

from dotenv import dotenv_values

from .bot import BotConfig
from .central_order_manager import CentralOrderStateError, CentralOrderStore
from .config_persistence import (
    PROFILE_MODES,
    StrategyProfileError,
    StrategyProfileStore,
    bot_config_to_profile,
)
from .journal import EventJournal
from .locking import InterProcessFileLock, LockUnavailableError
from .instrument_runtime import InstrumentRuntimeStateError, InstrumentRuntimeStore
from .multi_instrument_config import (
    MultiInstrumentConfigError,
    MultiInstrumentProfileStore,
)
from .risk import RiskPolicy
from .risk_persistence import (
    RISK_MODES,
    RiskPersistenceError,
    RiskProfileStore,
    RiskStateStore,
)
from .state_persistence import atomic_write_json
from .portfolio_model import PortfolioState
from .portfolio_repository import PortfolioRepository, PortfolioRepositoryError
from .secret_provider import SecretProvider, SecretProviderProbe, probe_secret_provider


CANONICAL_RISK_PROFILE_NAME = "risk_profiles.json"
LEGACY_RISK_PROFILE_NAMES: tuple[str, ...] = ("risk_profile.json",)
RUNTIME_BOOTSTRAP_LOCK_NAME = "runtime_bootstrap.lock"
RUNTIME_BOOTSTRAP_REPORT_NAME = "runtime_bootstrap_report.json"


@dataclass(frozen=True, slots=True)
class RuntimeSetupItem:
    name: str
    action: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RuntimeSetupReport:
    app_dir: str
    items: tuple[RuntimeSetupItem, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    credential_probe: SecretProviderProbe | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    @property
    def first_run(self) -> bool:
        created_names = {
            item.name
            for item in self.items
            if item.action in {"CREATED", "MIGRATED"}
        }
        return bool(
            created_names
            & {
                ".env",
                "strategy_profiles.json",
                CANONICAL_RISK_PROFILE_NAME,
                "risk_state.json",
                "robot_state.json",
                "portfolio_state.json",
                "sandbox_diagnostic_state.json",
                "trading_events.db",
            }
        )

    @property
    def changed(self) -> bool:
        return any(
            item.action in {"CREATED", "MIGRATED", "UPDATED"}
            for item in self.items
        )

    @property
    def should_notify(self) -> bool:
        return self.changed or bool(self.warnings) or bool(self.errors)

    def to_dict(self) -> dict[str, Any]:
        credential = (
            self.credential_probe.to_dict()
            if self.credential_probe is not None
            else {
                "credential_status": "not_checked",
                "secret_provider": None,
                "secret_provider_secure": None,
                "secret_provider_available": None,
                "secret_present": None,
                "secret_key": "TBANK_SANDBOX_TOKEN",
                "secret_probe_error": None,
            }
        )
        return {
            "app_dir": self.app_dir,
            "ok": self.ok,
            "has_errors": self.has_errors,
            "first_run": self.first_run,
            "changed": self.changed,
            "canonical_risk_profile": CANONICAL_RISK_PROFILE_NAME,
            "items": [item.to_dict() for item in self.items],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            **credential,
        }

    def format_text(self) -> str:
        lines = [
            "Единая первичная настройка runtime-файлов",
            f"Каталог: {self.app_dir}",
            "",
        ]
        action_titles = {
            "CREATED": "создан",
            "MIGRATED": "мигрирован",
            "UPDATED": "дополнен",
            "PRESERVED": "сохранён без изменений",
            "VALIDATED": "проверен",
            "SKIPPED": "пропущен",
            "MIGRATION_REQUIRED": "требует явной миграции",
        }
        for item in self.items:
            title = action_titles.get(item.action, item.action)
            suffix = f" — {item.detail}" if item.detail else ""
            lines.append(f"• {item.name}: {title}{suffix}")
        if self.warnings:
            lines.extend(["", "Предупреждения:"])
            lines.extend(f"• {item}" for item in self.warnings)
        if self.errors:
            lines.extend(["", "Ошибки:"])
            lines.extend(f"• {item}" for item in self.errors)
        lines.extend(
            [
                "",
                "Статус Sandbox credential: "
                + (
                    self.credential_probe.status
                    if self.credential_probe is not None
                    else "not_checked"
                ),
                "Каноническое имя риск-профилей: risk_profiles.json.",
                "Существующие пользовательские файлы не перезаписываются.",
            ]
        )
        return "\n".join(lines)


class RuntimeBootstrapError(RuntimeError):
    """Raised when the first-run bootstrap cannot complete safely."""


def _value(values: Mapping[str, Any], name: str, default: str) -> str:
    raw = values.get(name)
    if raw in (None, ""):
        return default
    return str(raw).strip()


def _int_value(values: Mapping[str, Any], name: str, default: int) -> int:
    raw = _value(values, name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeBootstrapError(f"{name} must be an integer, got {raw!r}.") from exc


def _float_value(values: Mapping[str, Any], name: str, default: float) -> float:
    raw = _value(values, name, str(default)).replace(",", ".")
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeBootstrapError(f"{name} must be numeric, got {raw!r}.") from exc


def _optional_percent(values: Mapping[str, Any], name: str) -> float | None:
    raw = _value(values, name, "").replace(",", ".")
    if not raw or raw.upper() in {"NONE", "OFF", "NO", "0"}:
        return None
    try:
        return float(raw) / 100.0
    except ValueError as exc:
        raise RuntimeBootstrapError(f"{name} must be numeric, got {raw!r}.") from exc


def _strategy_list(values: Mapping[str, Any], name: str) -> tuple[str, ...]:
    raw = _value(values, name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _strategy_profile_from_env(values: Mapping[str, Any]) -> tuple[dict[str, Any], float, float]:
    target_volatility = _optional_percent(values, "ROBOT_TARGET_VOLATILITY_PERCENT")
    config = BotConfig(
        ticker=_value(values, "ROBOT_TICKER", "SBER"),
        class_code=_value(values, "ROBOT_CLASS_CODE", "TQBR"),
        candle_interval=_value(
            values, "ROBOT_CANDLE_INTERVAL", "CANDLE_INTERVAL_HOUR"
        ),
        primary_strategy=_value(values, "ROBOT_PRIMARY_STRATEGY", "sma"),
        shadow_strategies=_strategy_list(values, "ROBOT_SHADOW_STRATEGIES"),
        fast_window=_int_value(values, "ROBOT_FAST_WINDOW", 20),
        slow_window=_int_value(values, "ROBOT_SLOW_WINDOW", 50),
        sma_hysteresis_percent=(
            _float_value(values, "ROBOT_SMA_HYSTERESIS_PERCENT", 0.2) / 100.0
        ),
        donchian_entry_window=_int_value(
            values, "ROBOT_DONCHIAN_ENTRY_WINDOW", 55
        ),
        donchian_exit_window=_int_value(
            values, "ROBOT_DONCHIAN_EXIT_WINDOW", 20
        ),
        donchian_atr_window=_int_value(values, "ROBOT_DONCHIAN_ATR_WINDOW", 20),
        donchian_trailing_stop_atr=_float_value(
            values, "ROBOT_DONCHIAN_TRAILING_STOP_ATR", 3.0
        ),
        ensemble_sma_fast=_int_value(values, "ROBOT_ENSEMBLE_SMA_FAST", 50),
        ensemble_sma_slow=_int_value(values, "ROBOT_ENSEMBLE_SMA_SLOW", 200),
        ensemble_momentum_window=_int_value(
            values, "ROBOT_ENSEMBLE_MOMENTUM_WINDOW", 126
        ),
        ensemble_breakout_window=_int_value(
            values, "ROBOT_ENSEMBLE_BREAKOUT_WINDOW", 100
        ),
        ensemble_vote_threshold=_int_value(
            values, "ROBOT_ENSEMBLE_VOTE_THRESHOLD", 3
        ),
        annual_target_volatility=target_volatility,
        volatility_window=_int_value(values, "ROBOT_VOLATILITY_WINDOW", 20),
        max_strategy_weight=(
            _float_value(values, "ROBOT_MAX_STRATEGY_WEIGHT_PERCENT", 100.0)
            / 100.0
        ),
        lookback_days=_int_value(values, "ROBOT_LOOKBACK_DAYS", 30),
        poll_seconds=_int_value(values, "ROBOT_POLL_SECONDS", 300),
        max_order_lots=_int_value(values, "ROBOT_MAX_ORDER_LOTS", 1),
        order_type=_value(values, "ROBOT_ORDER_TYPE", "BESTPRICE"),
        time_in_force=_value(values, "ROBOT_TIME_IN_FORCE", "FILL_AND_KILL"),
        failure_threshold=_int_value(values, "ROBOT_FAILURE_THRESHOLD", 3),
        circuit_open_seconds=_int_value(values, "ROBOT_CIRCUIT_OPEN_SECONDS", 60),
        circuit_max_open_seconds=_int_value(
            values, "ROBOT_CIRCUIT_MAX_OPEN_SECONDS", 900
        ),
        max_signal_age_seconds=_int_value(
            values, "ROBOT_MAX_SIGNAL_AGE_SECONDS", 0
        ),
        reconcile_attempts=_int_value(values, "ROBOT_RECONCILE_ATTEMPTS", 3),
        reconcile_delay_seconds=_float_value(
            values, "ROBOT_RECONCILE_DELAY_SECONDS", 0.5
        ),
        portfolio_reconcile_interval_seconds=_int_value(
            values, "ROBOT_PORTFOLIO_RECONCILE_INTERVAL_SECONDS", 900
        ),
    )
    connect_timeout = _float_value(values, "TBANK_CONNECT_TIMEOUT_SECONDS", 8.0)
    read_timeout = _float_value(values, "TBANK_READ_TIMEOUT_SECONDS", 25.0)
    profile = bot_config_to_profile(
        config,
        connect_timeout_seconds=connect_timeout,
        read_timeout_seconds=read_timeout,
    )
    return profile, connect_timeout, read_timeout


def _atomic_copy_text(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f"{destination.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json_object(path: Path, title: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeBootstrapError(f"{title} exists but cannot be read.") from exc
    if not isinstance(value, dict):
        raise RuntimeBootstrapError(f"{title} root must be a JSON object.")
    return value


def _validate_all_risk_profiles(store: RiskProfileStore) -> None:
    document = store.load_document()
    profiles = document.get("profiles") or {}
    for mode in profiles:
        store.require_profile(str(mode))


def _validate_all_strategy_profiles(store: StrategyProfileStore) -> None:
    document = store.load_document()
    profiles = document.get("profiles") or {}
    for mode in profiles:
        loaded = store.load_profile(str(mode))
        if loaded is None:
            raise StrategyProfileError(f"Profile {mode} disappeared during validation.")


def _migrate_legacy_risk_profile(
    app_dir: Path,
    items: list[RuntimeSetupItem],
    warnings: list[str],
    errors: list[str],
) -> Path:
    canonical = app_dir / CANONICAL_RISK_PROFILE_NAME
    legacy_paths = [app_dir / name for name in LEGACY_RISK_PROFILE_NAMES]
    existing_legacy = [path for path in legacy_paths if path.exists()]
    if not existing_legacy:
        return canonical
    if canonical.exists():
        warnings.append(
            "Обнаружены одновременно risk_profiles.json и устаревший "
            + ", ".join(path.name for path in existing_legacy)
            + ". Используется канонический risk_profiles.json; legacy-файл не удалён."
        )
        for path in existing_legacy:
            items.append(
                RuntimeSetupItem(path.name, "PRESERVED", "legacy conflict; review manually")
            )
        return canonical

    legacy = existing_legacy[0]
    try:
        _validate_all_risk_profiles(RiskProfileStore(legacy))
        os.replace(legacy, canonical)
        items.append(
            RuntimeSetupItem(
                canonical.name,
                "MIGRATED",
                f"из {legacy.name}; checksum/schema validated",
            )
        )
    except (OSError, RiskPersistenceError, ValueError, TypeError) as exc:
        errors.append(f"Не удалось безопасно мигрировать {legacy.name}: {exc}")
    return canonical


def bootstrap_runtime_files(
    app_dir: str | Path,
    *,
    create_missing: bool = True,
    secret_provider: SecretProvider | None = None,
) -> RuntimeSetupReport:
    """Create/validate the canonical local runtime set without overwriting users.

    The operation is idempotent. Existing files are validated and preserved.
    Missing profiles are added with conservative one-lot defaults. Corrupt files
    are never silently replaced because they may contain pending-order or risk
    state required for safe recovery.
    """

    root = Path(app_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    items: list[RuntimeSetupItem] = []
    warnings: list[str] = []
    errors: list[str] = []
    credential_probe: SecretProviderProbe | None = None
    lock = InterProcessFileLock(
        root / RUNTIME_BOOTSTRAP_LOCK_NAME,
        timeout_seconds=5.0,
    )
    try:
        lock.acquire()
    except LockUnavailableError as exc:
        return RuntimeSetupReport(
            app_dir=str(root),
            items=(),
            warnings=(),
            errors=(str(exc),),
        )

    try:
        env_path = root / ".env"
        env_example = root / ".env.example"
        if env_path.exists():
            items.append(RuntimeSetupItem(env_path.name, "PRESERVED"))
        elif create_missing:
            try:
                if env_example.exists():
                    _atomic_copy_text(env_example, env_path)
                    detail = "создан из .env.example; токен оставлен пустым"
                else:
                    env_path.write_text(
                        "TBANK_SANDBOX_TOKEN=\n"
                        "TBANK_SANDBOX_ACCOUNT_ID=\n"
                        "SANDBOX_INITIAL_RUB=1000000\n"
                        "TBANK_CONNECT_TIMEOUT_SECONDS=8\n"
                        "TBANK_READ_TIMEOUT_SECONDS=25\n"
                        "ARM_SANDBOX_TRADING=NO\n",
                        encoding="utf-8",
                    )
                    detail = "создан безопасный минимальный шаблон"
                items.append(RuntimeSetupItem(env_path.name, "CREATED", detail))
            except OSError as exc:
                errors.append(f"Не удалось создать .env: {exc}")
        else:
            warnings.append("Отсутствует .env.")

        env_values: Mapping[str, Any] = (
            dotenv_values(env_path) if env_path.exists() else {}
        )
        credential_probe = probe_secret_provider(root, provider=secret_provider)
        if credential_probe.status == "credential_absent":
            warnings.append(
                "Sandbox credential отсутствует и в защищённом provider, и в .env. "
                "Откройте вкладку Sandbox и сохраните подключение."
            )
        elif credential_probe.status == "provider_unavailable":
            warnings.append(
                "Защищённый Sandbox credential provider недоступен; наличие токена "
                "не подтверждено."
            )
        elif credential_probe.status == ".env_fallback":
            warnings.append(
                "Sandbox credential загружен через .env fallback; защищённый provider "
                "не используется."
            )

        strategy_path = root / "strategy_profiles.json"
        strategy_store = StrategyProfileStore(strategy_path)
        try:
            if strategy_path.exists():
                _validate_all_strategy_profiles(strategy_store)
                items.append(RuntimeSetupItem(strategy_path.name, "VALIDATED"))
            elif not create_missing:
                warnings.append("Отсутствует strategy_profiles.json.")

            if create_missing:
                profile, _connect, _read = _strategy_profile_from_env(env_values)
                created_modes: list[str] = []
                for mode in PROFILE_MODES:
                    if strategy_store.load_profile(mode) is None:
                        strategy_store.save_profile(
                            mode,
                            profile,
                            select=(mode == "DRY_RUN" and not strategy_path.exists()),
                        )
                        created_modes.append(mode)
                if created_modes:
                    action = "CREATED" if len(created_modes) == len(PROFILE_MODES) else "UPDATED"
                    items.append(
                        RuntimeSetupItem(
                            strategy_path.name,
                            action,
                            "добавлены профили " + ", ".join(created_modes),
                        )
                    )
        except (StrategyProfileError, RuntimeBootstrapError, OSError, ValueError) as exc:
            errors.append(
                "strategy_profiles.json не был перезаписан: " + str(exc)
            )

        multi_profile_path = root / "multi_instrument_profiles.json"
        instrument_runtime_path = root / "instrument_runtimes.json"
        try:
            if multi_profile_path.exists():
                multi_store = MultiInstrumentProfileStore(multi_profile_path)
                for mode in PROFILE_MODES:
                    multi_store.load_mode(mode)
                items.append(RuntimeSetupItem(multi_profile_path.name, "VALIDATED"))
            if instrument_runtime_path.exists():
                InstrumentRuntimeStore(instrument_runtime_path).load()
                items.append(
                    RuntimeSetupItem(instrument_runtime_path.name, "VALIDATED")
                )
            if multi_profile_path.exists() != instrument_runtime_path.exists():
                warnings.append(
                    "v3.8 multi-instrument profile/runtime pair is incomplete; "
                    "execution integration remains disabled."
                )
        except (
            InstrumentRuntimeStateError,
            MultiInstrumentConfigError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            errors.append("v3.8 runtime registry validation failed: " + str(exc))

        central_order_path = root / "central_order_state.json"
        try:
            if central_order_path.exists():
                CentralOrderStore(central_order_path).load()
                items.append(RuntimeSetupItem(central_order_path.name, "VALIDATED"))
        except (CentralOrderStateError, OSError, TypeError, ValueError) as exc:
            errors.append("v3.8 central order state validation failed: " + str(exc))

        risk_path = _migrate_legacy_risk_profile(root, items, warnings, errors)
        risk_store = RiskProfileStore(risk_path)
        try:
            if risk_path.exists():
                _validate_all_risk_profiles(risk_store)
                if not any(item.name == risk_path.name and item.action == "MIGRATED" for item in items):
                    items.append(RuntimeSetupItem(risk_path.name, "VALIDATED"))
            elif not create_missing:
                warnings.append(f"Отсутствует {CANONICAL_RISK_PROFILE_NAME}.")

            if create_missing:
                created_modes: list[str] = []
                for mode in RISK_MODES:
                    if risk_store.load_profile(mode) is None:
                        risk_store.save_profile(
                            mode,
                            RiskPolicy(),
                            select=(mode == "DRY_RUN" and not risk_path.exists()),
                        )
                        created_modes.append(mode)
                if created_modes:
                    action = "CREATED" if len(created_modes) == len(RISK_MODES) else "UPDATED"
                    items.append(
                        RuntimeSetupItem(
                            risk_path.name,
                            action,
                            "добавлены профили " + ", ".join(created_modes),
                        )
                    )
        except (RiskPersistenceError, OSError, ValueError, TypeError) as exc:
            errors.append(f"{risk_path.name} не был перезаписан: {exc}")

        risk_state_path = root / "risk_state.json"
        try:
            if risk_state_path.exists():
                RiskStateStore(risk_state_path).load_document()
                items.append(RuntimeSetupItem(risk_state_path.name, "VALIDATED"))
            elif create_missing:
                atomic_write_json(
                    risk_state_path,
                    {"version": RiskStateStore.SCHEMA_VERSION, "accounts": {}},
                )
                items.append(RuntimeSetupItem(risk_state_path.name, "CREATED"))
            else:
                warnings.append("Отсутствует risk_state.json.")
        except (RiskPersistenceError, OSError, ValueError, TypeError) as exc:
            errors.append(f"risk_state.json не был перезаписан: {exc}")

        robot_state_path = root / "robot_state.json"
        try:
            if robot_state_path.exists():
                _read_json_object(robot_state_path, robot_state_path.name)
                items.append(RuntimeSetupItem(robot_state_path.name, "VALIDATED"))
            elif create_missing:
                atomic_write_json(
                    robot_state_path,
                    {
                        "version": 6,
                        "bots": {},
                        "strategy_states": {},
                        "strategy_configs": {},
                        "execution_scopes": {},
                    },
                )
                items.append(RuntimeSetupItem(robot_state_path.name, "CREATED"))
            else:
                warnings.append("Отсутствует robot_state.json.")
        except (RuntimeBootstrapError, OSError, ValueError, TypeError) as exc:
            errors.append(f"robot_state.json не был перезаписан: {exc}")

        portfolio_state_path = root / "portfolio_state.json"
        try:
            portfolio_repository = PortfolioRepository(portfolio_state_path)
            if portfolio_state_path.exists():
                portfolio_repository.load()
                raw_schema = portfolio_repository.raw_schema_version()
                if raw_schema == 1:
                    items.append(
                        RuntimeSetupItem(
                            portfolio_state_path.name,
                            "MIGRATION_REQUIRED",
                            "Остановите execution и выполните preview/cutover до запуска торговли.",
                        )
                    )
                    warnings.append(
                        "portfolio_state.json использует schema 1; beta1 не выполнит cutover автоматически."
                    )
                else:
                    items.append(RuntimeSetupItem(portfolio_state_path.name, "VALIDATED"))
            elif create_missing:
                portfolio_repository.save(PortfolioState.empty())
                items.append(RuntimeSetupItem(portfolio_state_path.name, "CREATED"))
            else:
                warnings.append("Отсутствует portfolio_state.json.")
        except (PortfolioRepositoryError, OSError, ValueError, TypeError) as exc:
            errors.append(f"portfolio_state.json не был перезаписан: {exc}")

        diagnostic_path = root / "sandbox_diagnostic_state.json"
        try:
            if diagnostic_path.exists():
                _read_json_object(diagnostic_path, diagnostic_path.name)
                items.append(RuntimeSetupItem(diagnostic_path.name, "VALIDATED"))
            elif create_missing:
                atomic_write_json(
                    diagnostic_path,
                    {"version": 1, "diagnostics": {}},
                )
                items.append(RuntimeSetupItem(diagnostic_path.name, "CREATED"))
            else:
                warnings.append("Отсутствует sandbox_diagnostic_state.json.")
        except (RuntimeBootstrapError, OSError, ValueError, TypeError) as exc:
            errors.append(
                f"sandbox_diagnostic_state.json не был перезаписан: {exc}"
            )

        journal_path = root / "trading_events.db"
        try:
            existed = journal_path.exists()
            EventJournal(journal_path)
            items.append(
                RuntimeSetupItem(
                    journal_path.name,
                    "VALIDATED" if existed else "CREATED",
                    "SQLite schema ready",
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"Не удалось подготовить trading_events.db: {exc}")
    finally:
        lock.release()

    # Deduplicate warnings while preserving order.
    unique_warnings = tuple(dict.fromkeys(warnings))
    unique_errors = tuple(dict.fromkeys(errors))
    return RuntimeSetupReport(
        app_dir=str(root),
        items=tuple(items),
        warnings=unique_warnings,
        errors=unique_errors,
        credential_probe=credential_probe,
    )


def bootstrap_runtime(
    app_dir: str | Path,
    *,
    write_report: bool = True,
    secret_provider: SecretProvider | None = None,
) -> RuntimeSetupReport:
    report = bootstrap_runtime_files(
        app_dir,
        create_missing=True,
        secret_provider=secret_provider,
    )
    if write_report:
        report_path = Path(app_dir).resolve() / RUNTIME_BOOTSTRAP_REPORT_NAME
        try:
            atomic_write_json(report_path, report.to_dict())
        except OSError:
            # The setup result is still returned; GUI/CLI can report the missing log.
            pass
    return report


def validate_runtime_files(
    app_dir: str | Path,
    *,
    secret_provider: SecretProvider | None = None,
) -> RuntimeSetupReport:
    return bootstrap_runtime_files(
        app_dir,
        create_missing=False,
        secret_provider=secret_provider,
    )


__all__ = [
    "CANONICAL_RISK_PROFILE_NAME",
    "LEGACY_RISK_PROFILE_NAMES",
    "RuntimeBootstrapError",
    "RuntimeSetupItem",
    "RuntimeSetupReport",
    "RUNTIME_BOOTSTRAP_LOCK_NAME",
    "RUNTIME_BOOTSTRAP_REPORT_NAME",
    "bootstrap_runtime",
    "bootstrap_runtime_files",
    "validate_runtime_files",
]
