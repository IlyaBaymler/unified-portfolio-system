from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time
import traceback
from typing import Any, Callable
import webbrowser

import pandas as pd
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from dotenv import dotenv_values, set_key
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from trading_robot import __version__
from trading_robot.backtest import BacktestConfig, BacktestResult, run_backtest
from trading_robot.bot import BotConfig, SandboxTradingBot
from trading_robot.config_persistence import (
    PROFILE_MODES,
    StrategyProfileError,
    StrategyProfileStore,
    bot_config_to_profile,
    canonical_profile_hash,
)
from trading_robot.diagnostics import DiagnosticConfig, SandboxOrderDiagnostics
from trading_robot.diagnostic_feedback import build_diagnostic_feedback
from trading_robot.dashboard_view import build_kill_switch_banner, full_account_id
from trading_robot.gui_clipboard import normalize_pasted_text, resolve_clipboard_action
from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.locking import InterProcessFileLock, LockUnavailableError
from trading_robot.logging_setup import RedactingFormatter, configure_file_logging
from trading_robot.moex_iss import MoexISSClient
from trading_robot.risk_persistence import (
    RiskPersistenceError,
    RiskProfileStore,
)
from trading_robot.risk_runtime import RiskRuntimeAdapter
from trading_robot.risk_profile_editor import (
    RiskProfileEditContext,
    RiskProfileEditError,
    RiskProfileEditor,
)
from trading_robot.runtime_bootstrap import RuntimeSetupReport, bootstrap_runtime
from trading_robot.runtime_backup import (
    RuntimeBackupError,
    RuntimeBackupManager,
    format_backup_verification_summary,
)
from trading_robot.readiness import (
    ProductionReadinessEvaluator,
    ProductionReadinessReport,
)
from trading_robot.support_bundle import SupportBundleBuilder, SupportBundleError
from trading_robot.secret_provider import (
    EnvFileSecretProvider,
    preferred_secret_provider,
    probe_secret_provider,
)
from trading_robot.risk_reporting import (
    RiskDashboardSnapshot,
    RiskReportError,
    load_risk_burn_in_report,
    load_risk_dashboard_snapshot,
    write_burn_in_report,
    write_dashboard_snapshot,
)
from trading_robot.risk_persistence import RiskStateStore
from trading_robot.portfolio import (
    ExternalCloseAcknowledgementRequest,
    OwnershipRecoveryRequest,
)
from trading_robot.portfolio_manager import CanonicalPortfolioManager
from trading_robot.portfolio_snapshot import PortfolioSnapshotBuilder
from trading_robot.paths import resolve_app_paths
from trading_robot.strategy import SmaCrossoverConfig, generate_sma_signals
from trading_robot.strategy_runtime import (
    STRATEGY_TITLES_RU,
    VALID_STRATEGIES,
    StrategySuiteConfig,
)
from trading_robot.tbank_sandbox import TBankSandboxClient
from trading_robot.gui_resilience import (
    TransientBackoff,
    describe_background_error,
)
from trading_robot.export_naming import (
    build_export_filename,
    collision_safe_path,
    display_version_from_manifest,
    ensure_export_directory,
)


APP_PATHS = resolve_app_paths(__file__)
APP_PATHS.ensure_directories()
APP_DIR = APP_PATHS.app_dir
RUNTIME_DIR = APP_PATHS.runtime_dir
ENV_PATH = RUNTIME_DIR / ".env"
LOG_PATH = RUNTIME_DIR / "robot_gui.log"
DEBUG_LOG_PATH = RUNTIME_DIR / "robot_debug.log"
EVENT_DB_PATH = RUNTIME_DIR / "trading_events.db"
DIAGNOSTIC_STATE_PATH = RUNTIME_DIR / "sandbox_diagnostic_state.json"
ROBOT_STATE_PATH = RUNTIME_DIR / "robot_state.json"
PORTFOLIO_STATE_PATH = RUNTIME_DIR / "portfolio_state.json"
STRATEGY_PROFILE_PATH = RUNTIME_DIR / "strategy_profiles.json"
RISK_PROFILE_PATH = RUNTIME_DIR / "risk_profiles.json"
RISK_STATE_PATH = RUNTIME_DIR / "risk_state.json"
GUI_LOCK_PATH = RUNTIME_DIR / "moex_robot_gui.lock"
BACKUPS_DIR = APP_PATHS.backups_dir
REPORTS_DIR = APP_PATHS.reports_dir
DISPLAY_VERSION = display_version_from_manifest(
    APP_DIR / "build_manifest.json",
    package_version=__version__,
)


def _reports_initial_dir() -> str:
    """Return the reports directory for this exact source/portable runtime."""

    return str(ensure_export_directory(REPORTS_DIR))


def _safe_file_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


class HoverTooltip:
    """Tiny tooltip helper for complete account identifiers and safety status."""

    def __init__(self, widget: tk.Widget, text_getter: Callable[[], str]) -> None:
        self.widget = widget
        self.text_getter = text_getter
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event: tk.Event | None = None) -> None:
        text = str(self.text_getter() or "").strip()
        if not text or self.window is not None:
            return
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        try:
            x = self.widget.winfo_rootx()
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            window.wm_geometry(f"+{x}+{y}")
        except tk.TclError:
            pass
        tk.Label(
            window,
            text=text,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=4,
            font=("Segoe UI", 9),
            wraplength=640,
        ).pack()
        self.window = window

    def _hide(self, _event: tk.Event | None = None) -> None:
        if self.window is None:
            return
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        self.window = None


class QueueLogHandler(logging.Handler):
    """Send formatted log records to the Tk event loop."""

    def __init__(self, output_queue: queue.Queue[tuple[str, Any]]) -> None:
        super().__init__()
        self.output_queue = output_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.output_queue.put(("log", self.format(record)))
        except Exception:
            self.handleError(record)


class TradingRobotGUI(tk.Tk):
    """Native desktop GUI for backtesting and T-Invest Sandbox control."""

    def __init__(
        self,
        bootstrap_report: RuntimeSetupReport | None = None,
    ) -> None:
        super().__init__()
        self.title(f"MOEX Research Robot {DISPLAY_VERSION}")
        self.geometry("1380x900")
        self.minsize(1120, 760)
        self.runtime_bootstrap_report = bootstrap_report

        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event = threading.Event()
        self.robot_thread: threading.Thread | None = None
        self.sandbox_task_active = False
        self.sandbox_task_name = ""
        self.backtest_result: BacktestResult | None = None
        self.account_records: dict[str, dict[str, Any]] = {}
        self._scroll_canvases: list[tk.Canvas] = []
        self.event_journal = EventJournal(EVENT_DB_PATH)
        self.strategy_profile_store = StrategyProfileStore(STRATEGY_PROFILE_PATH)
        self.strategy_profile_error: str | None = None
        self._strategy_profile_loading = False
        self._strategy_profile_dirty = False
        self._active_strategy_profile_mode: str | None = None
        self._active_strategy_profile_hash: str | None = None
        self._active_risk_policy_hash: str | None = None
        self._event_rows: dict[str, dict[str, Any]] = {}
        self.sb_config_widgets: list[tuple[tk.Widget, str]] = []
        self.sb_config_locked = False
        self.sb_shadow_checkbuttons: dict[str, ttk.Checkbutton] = {}
        self.portfolio_task_active = False
        self.last_portfolio_snapshot: dict[str, Any] | None = None
        self._portfolio_auto_after_id: str | None = None
        self._portfolio_refresh_backoff = TransientBackoff(
            base_seconds=60,
            max_seconds=600,
        )
        self._background_error_notice_at: dict[str, float] = {}
        self.last_risk_dashboard_snapshot: RiskDashboardSnapshot | None = None
        self.last_readiness_report: ProductionReadinessReport | None = None
        self._risk_dashboard_event_rows: dict[str, dict[str, Any]] = {}
        self._risk_dashboard_poll_after_id: str | None = None
        self._risk_dashboard_watch_signature: tuple[Any, ...] | None = None
        self._hover_tooltips: list[HoverTooltip] = []
        self.runtime_backup_manager = RuntimeBackupManager(
            RUNTIME_DIR,
            app_version=__version__,
        )
        self.secret_provider = preferred_secret_provider(RUNTIME_DIR)
        self.readiness_evaluator = ProductionReadinessEvaluator(
            RUNTIME_DIR,
            app_version=__version__,
            backups_dir=BACKUPS_DIR,
            build_manifest_path=APP_DIR / "build_manifest.json",
        )

        self._configure_logging()
        self._configure_styles()
        self._create_variables()
        self._build_ui()
        self._load_settings_from_env(
            show_message=False,
            include_strategy=not STRATEGY_PROFILE_PATH.exists(),
        )
        self._initialize_strategy_profiles()
        self._bind_strategy_profile_traces()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._process_ui_queue)
        self.after(750, self._poll_risk_dashboard_files)
        if bootstrap_report is not None and (
            bootstrap_report.first_run
            or bootstrap_report.changed
            or bootstrap_report.has_errors
        ):
            self.after(350, self._show_runtime_setup_report)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _configure_logging(self) -> None:
        paths = configure_file_logging(RUNTIME_DIR, debug_enabled=True)
        root_logger = logging.getLogger()
        formatter = RedactingFormatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        existing_queue_handler = next(
            (h for h in root_logger.handlers if isinstance(h, QueueLogHandler)),
            None,
        )
        if existing_queue_handler is None:
            queue_handler = QueueLogHandler(self.ui_queue)
            queue_handler.name = "moex_gui_queue"
            queue_handler.setLevel(logging.INFO)
            queue_handler.setFormatter(formatter)
            root_logger.addHandler(queue_handler)
        else:
            existing_queue_handler.output_queue = self.ui_queue
            existing_queue_handler.setFormatter(formatter)

        self.logger = logging.getLogger("moex_gui")
        self.logger.debug(
            "Logging configured: compact=%s debug=%s",
            paths.compact,
            paths.debug,
        )

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("MetricTitle.TLabel", font=("Segoe UI", 9))
        style.configure("MetricValue.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"))

    # ------------------------------------------------------------------
    # First-run bootstrap and operator-facing identity/status helpers
    # ------------------------------------------------------------------
    def _show_runtime_setup_report(self) -> None:
        report = self.runtime_bootstrap_report
        if report is None:
            return
        self._open_runtime_setup_report(report)

    def _open_runtime_setup_report(self, report: RuntimeSetupReport) -> None:
        window = tk.Toplevel(self)
        window.title("Первичная настройка MOEX Research Robot")
        window.transient(self)
        window.geometry("820x590")
        window.minsize(680, 460)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)

        title = (
            "Первичная настройка требует внимания"
            if report.has_errors
            else "Первичная настройка завершена"
        )
        ttk.Label(
            window,
            text=title,
            style="Title.TLabel",
            padding=(16, 14, 16, 4),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            window,
            text=(
                "Отсутствующие runtime-файлы создаются одним безопасным шагом. "
                "Существующие профили, состояния и журнал не перезаписываются. "
                "Каноническое имя риск-профилей — risk_profiles.json."
            ),
            wraplength=770,
            justify="left",
            padding=(16, 0, 16, 10),
        ).grid(row=1, column=0, sticky="ew")

        body = tk.Text(window, wrap="word", font=("Consolas", 10))
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 10))
        body.insert("1.0", report.format_text())
        body.configure(state="disabled")

        buttons = ttk.Frame(window, padding=(16, 0, 16, 14))
        buttons.grid(row=3, column=0, sticky="ew")

        def open_sandbox() -> None:
            self.notebook.select(self.sandbox_tab)
            window.destroy()

        ttk.Button(
            buttons,
            text="Перейти к настройкам Sandbox",
            command=open_sandbox,
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="Повторить проверку",
            command=lambda: self._rerun_runtime_setup(window),
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            buttons,
            text="Продолжить",
            command=window.destroy,
        ).pack(side="right")
        window.grab_set()
        window.focus_set()

    def _rerun_runtime_setup(self, current_window: tk.Toplevel | None = None) -> None:
        if current_window is not None:
            current_window.destroy()
        report = bootstrap_runtime(RUNTIME_DIR, write_report=True)
        self.runtime_bootstrap_report = report
        self._open_runtime_setup_report(report)

    def _copy_text(self, value: str, *, status: str | None = None) -> None:
        text = str(value or "").strip()
        if not text:
            messagebox.showinfo("Копирование", "Нет значения для копирования.", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        if status:
            self.risk_dashboard_status.set(status)

    def _set_account_id_display(self, account_id: str | None = None) -> None:
        value = full_account_id(
            account_id or self._selected_account_id(optional=True)
        )
        self.sb_account_id_display.set(value or "—")

    def _copy_selected_account_id(self) -> None:
        account_id = self._selected_account_id(optional=True)
        self._copy_text(account_id, status="Полный Account ID скопирован")

    def _apply_kill_switch_banner(
        self,
        summary: dict[str, Any] | None,
        recent_events: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    ) -> None:
        banner = build_kill_switch_banner(summary, recent_events)
        self.risk_kill_switch_title.set(banner.title)
        self.risk_kill_switch_detail.set(banner.detail)
        if hasattr(self, "risk_kill_switch_banner"):
            self.risk_kill_switch_banner.configure(background=banner.background)
            self.risk_kill_switch_title_label.configure(
                background=banner.background,
                foreground=banner.foreground,
            )
            self.risk_kill_switch_detail_label.configure(
                background=banner.background,
                foreground=banner.foreground,
            )

    def _copy_kill_switch_diagnostics(self) -> None:
        snapshot = self.last_risk_dashboard_snapshot
        if snapshot is None:
            self._refresh_risk_dashboard()
            snapshot = self.last_risk_dashboard_snapshot
        if snapshot is None:
            return
        payload = {
            "account_id": snapshot.account_id,
            "mode": snapshot.mode,
            "policy_hash": snapshot.policy_hash,
            "kill_switch_active": snapshot.summary.get("kill_switch_active"),
            "kill_switch_reason": snapshot.summary.get("kill_switch_reason"),
            "kill_switch_set_at": snapshot.summary.get("kill_switch_set_at"),
            "engine_status": snapshot.engine_status,
            "persistent_gate": self.risk_dashboard_summary_vars[
                "persistent_gate"
            ].get(),
        }
        self._copy_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            status="Диагностика kill switch скопирована",
        )

    def _poll_risk_dashboard_files(self) -> None:
        try:
            account_id = self._selected_account_id(optional=True)
            signature = (
                account_id,
                self.risk_dashboard_mode.get(),
                _safe_file_mtime_ns(RISK_STATE_PATH),
                _safe_file_mtime_ns(RISK_PROFILE_PATH),
                _safe_file_mtime_ns(EVENT_DB_PATH),
                _safe_file_mtime_ns(EVENT_DB_PATH.with_name(EVENT_DB_PATH.name + "-wal")),
            )
            if (
                account_id
                and signature != self._risk_dashboard_watch_signature
                and hasattr(self, "risk_limits_tree")
            ):
                self._risk_dashboard_watch_signature = signature
                self._refresh_risk_dashboard()
        except (tk.TclError, ValueError):
            pass
        finally:
            if self.winfo_exists():
                self._risk_dashboard_poll_after_id = self.after(
                    1000,
                    self._poll_risk_dashboard_files,
                )

    def _create_variables(self) -> None:
        # Backtest variables
        today = date.today()
        self.bt_ticker = tk.StringVar(value="SBER")
        self.bt_board = tk.StringVar(value="TQBR")
        self.bt_start = tk.StringVar(value=f"{today.year - 3}-{today.month:02d}-{today.day:02d}")
        self.bt_end = tk.StringVar(value=today.isoformat())
        self.bt_interval_label = tk.StringVar(value="День")
        self.bt_fast = tk.StringVar(value="20")
        self.bt_slow = tk.StringVar(value="50")
        self.bt_capital = tk.StringVar(value="1000000")
        self.bt_position_fraction = tk.StringVar(value="10")
        self.bt_commission = tk.StringVar(value="0.05")
        self.bt_spread = tk.StringVar(value="0.02")
        self.bt_slippage = tk.StringVar(value="0.03")
        self.bt_status = tk.StringVar(value="Готов к запуску бэктеста")

        # Sandbox variables
        self.sb_token = tk.StringVar()
        self.sb_show_token = tk.BooleanVar(value=False)
        self.sb_ca_bundle = tk.StringVar()
        self.sb_account = tk.StringVar()
        self.sb_account_id_display = tk.StringVar(value="—")
        self.sb_initial_rub = tk.StringVar(value="1000000")
        self.sb_ticker = tk.StringVar(value="SBER")
        self.sb_class_code = tk.StringVar(value="TQBR")
        self.sb_interval = tk.StringVar(value="CANDLE_INTERVAL_HOUR")
        self.sb_primary_strategy = tk.StringVar(
            value=STRATEGY_TITLES_RU["sma"]
        )
        self.sb_shadow_vars = {
            strategy: tk.BooleanVar(value=False)
            for strategy in VALID_STRATEGIES
        }
        self.sb_fast = tk.StringVar(value="20")
        self.sb_slow = tk.StringVar(value="50")
        self.sb_sma_hysteresis = tk.StringVar(value="0.2")
        self.sb_donchian_entry = tk.StringVar(value="55")
        self.sb_donchian_exit = tk.StringVar(value="20")
        self.sb_donchian_atr = tk.StringVar(value="20")
        self.sb_donchian_stop = tk.StringVar(value="3.0")
        self.sb_ensemble_fast = tk.StringVar(value="50")
        self.sb_ensemble_slow = tk.StringVar(value="200")
        self.sb_ensemble_momentum = tk.StringVar(value="126")
        self.sb_ensemble_breakout = tk.StringVar(value="100")
        self.sb_ensemble_votes = tk.StringVar(value="3")
        self.sb_target_volatility = tk.StringVar(value="")
        self.sb_volatility_window = tk.StringVar(value="20")
        self.sb_max_strategy_weight = tk.StringVar(value="100")
        self.sb_lookback = tk.StringVar(value="30")
        self.sb_poll = tk.StringVar(value="300")
        self.sb_max_lots = tk.StringVar(value="1")
        self.sb_connect_timeout = tk.StringVar(value="8")
        self.sb_read_timeout = tk.StringVar(value="25")
        self.sb_failure_threshold = tk.StringVar(value="3")
        self.sb_circuit_seconds = tk.StringVar(value="60")
        self.sb_circuit_max_seconds = tk.StringVar(value="900")
        self.sb_max_signal_age = tk.StringVar(value="0")
        self.sb_reconcile_attempts = tk.StringVar(value="3")
        self.sb_reconcile_delay = tk.StringVar(value="0.5")
        self.sb_portfolio_reconcile_interval = tk.StringVar(value="900")
        self.sb_arm_checkbox = tk.BooleanVar(value=False)
        self.sb_confirm_text = tk.StringVar()
        self.sb_status = tk.StringVar(value="Песочница не подключена")
        self.sb_profile_mode = tk.StringVar(value="DRY_RUN")
        self.sb_profile_status = tk.StringVar(value="Профиль не загружен")
        self.sb_profile_hash = tk.StringVar(value="—")

        self.portfolio_status = tk.StringVar(value="Портфель не загружен")
        self.portfolio_auto_refresh = tk.BooleanVar(value=False)
        self.portfolio_refresh_seconds = tk.StringVar(value="60")
        self.portfolio_recovery_confirm = tk.StringVar()
        self.portfolio_recovery_arm = tk.BooleanVar(value=False)
        self.portfolio_close_confirm = tk.StringVar()
        self.portfolio_close_arm = tk.BooleanVar(value=False)
        self.portfolio_ack_confirm = tk.StringVar()
        self.portfolio_ack_arm = tk.BooleanVar(value=False)
        self.portfolio_summary_vars = {
            "total": tk.StringVar(value="—"),
            "cash": tk.StringVar(value="—"),
            "securities": tk.StringVar(value="—"),
            "yield": tk.StringVar(value="—"),
            "reconciliation": tk.StringVar(value="—"),
            "checked_at": tk.StringVar(value="—"),
        }

        self.diag_ticker = tk.StringVar(value="SBER")
        self.diag_class_code = tk.StringVar(value="TQBR")
        self.diag_arm_checkbox = tk.BooleanVar(value=False)
        self.diag_confirm_text = tk.StringVar()
        self.diag_status = tk.StringVar(value="Диагностика не запускалась")
        self.event_filter = tk.StringVar(value="Все")
        self.event_severity_filter = tk.StringVar(value="Все")
        self.event_session_filter = tk.StringVar(value="Все")
        self.event_search = tk.StringVar()
        self.event_stats = tk.StringVar(value="События ещё не загружены")

        self.sb_dashboard_vars = {
            "api": tk.StringVar(value="Не проверен"),
            "market": tk.StringVar(value="Не проверен"),
            "last_check": tk.StringVar(value="—"),
            "last_candle": tk.StringVar(value="—"),
            "data_age": tk.StringVar(value="—"),
            "mode": tk.StringVar(value="—"),
            "profile": tk.StringVar(value="—"),
            "config_hash": tk.StringVar(value="—"),
            "primary": tk.StringVar(value="—"),
            "signal": tk.StringVar(value="—"),
            "risk": tk.StringVar(value="—"),
            "reason": tk.StringVar(value="—"),
            "position": tk.StringVar(value="—"),
            "pending": tk.StringVar(value="нет"),
            "failures": tk.StringVar(value="0"),
            "next_retry": tk.StringVar(value="—"),
        }

        self.risk_dashboard_mode = tk.StringVar(value="SANDBOX_EXECUTION")
        self.risk_dashboard_status = tk.StringVar(
            value="Risk Dashboard не загружен"
        )
        self.risk_dashboard_hours = tk.StringVar(value="24")
        self.risk_dashboard_account_id = tk.StringVar(value="—")
        self.risk_kill_switch_title = tk.StringVar(value="KILL SWITCH: UNKNOWN")
        self.risk_kill_switch_detail = tk.StringVar(
            value="RiskState ещё не загружен."
        )
        self.risk_order_limit_preset = tk.StringVar(value="Stable default (4)")
        self.risk_order_limit_custom = tk.StringVar(value="4")
        self.risk_order_limit_status = tk.StringVar(value="Лимит не загружен")
        self.risk_dashboard_summary_vars = {
            "engine": tk.StringVar(value="—"),
            "account": tk.StringVar(value="—"),
            "policy": tk.StringVar(value="—"),
            "decision": tk.StringVar(value="—"),
            "orders": tk.StringVar(value="—"),
            "turnover": tk.StringVar(value="—"),
            "daily_pnl": tk.StringVar(value="—"),
            "drawdown": tk.StringVar(value="—"),
            "cash": tk.StringVar(value="—"),
            "snapshot": tk.StringVar(value="—"),
            "round_trip": tk.StringVar(value="—"),
            "persistent_gate": tk.StringVar(value="—"),
        }

        self.readiness_status = tk.StringVar(value="Готовность ещё не проверена")
        self.readiness_account_id = tk.StringVar(value="—")
        self.readiness_backup_status = tk.StringVar(value="Backup не создан")
        self.readiness_support_status = tk.StringVar(value="Диагностический пакет не создан")

        self.metric_vars = {
            "capital": tk.StringVar(value="—"),
            "return": tk.StringVar(value="—"),
            "cagr": tk.StringVar(value="—"),
            "drawdown": tk.StringVar(value="—"),
            "sharpe": tk.StringVar(value="—"),
            "trades": tk.StringVar(value="—"),
        }

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(16, 12, 16, 8))
        header.pack(fill="x")
        ttk.Label(header, text="MOEX Research Robot", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Исследовательский GUI: исторический бэктест и управление "
                "только виртуальным счётом T-Invest Sandbox"
            ),
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        self.backtest_tab = ttk.Frame(self.notebook)
        self.sandbox_tab = ttk.Frame(self.notebook)
        self.portfolio_tab = ttk.Frame(self.notebook)
        self.risk_tab = ttk.Frame(self.notebook)
        self.readiness_tab = ttk.Frame(self.notebook)
        self.diagnostics_tab = ttk.Frame(self.notebook)
        self.events_tab = ttk.Frame(self.notebook)
        self.logs_tab = ttk.Frame(self.notebook)
        self.help_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.backtest_tab, text="Бэктест")
        self.notebook.add(self.sandbox_tab, text="T-Invest Sandbox")
        self.notebook.add(self.portfolio_tab, text="Виртуальный портфель")
        self.notebook.add(self.risk_tab, text="Risk Dashboard")
        self.notebook.add(self.readiness_tab, text="Готовность RC")
        self.notebook.add(self.diagnostics_tab, text="Диагностика заявок")
        self.notebook.add(self.events_tab, text="События v3.7-beta1")
        self.notebook.add(self.logs_tab, text="Технический журнал")
        self.notebook.add(self.help_tab, text="Как пользоваться")

        self._build_backtest_tab()
        self._build_sandbox_tab()
        self._build_portfolio_tab()
        self._build_risk_dashboard_tab()
        self._build_readiness_tab()
        self._build_diagnostics_tab()
        self._build_events_tab()
        self._build_logs_tab()
        self._build_help_tab()
        self._refresh_events()
        self.after(600, self._refresh_readiness)

        status = ttk.Frame(self, padding=(12, 4, 12, 8))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.bt_status).pack(side="left")
        ttk.Separator(status, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(status, textvariable=self.sb_status).pack(side="left")
        ttk.Separator(status, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(status, textvariable=self.portfolio_status).pack(side="left")
        ttk.Separator(status, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(status, textvariable=self.diag_status).pack(side="left")

    # ------------------------------------------------------------------
    # Backtest tab
    # ------------------------------------------------------------------
    def _build_backtest_tab(self) -> None:
        self.backtest_tab.columnconfigure(1, weight=1)
        self.backtest_tab.rowconfigure(0, weight=1)

        controls_host = ttk.Frame(self.backtest_tab)
        controls_host.grid(row=0, column=0, sticky="nsew")
        controls_host.rowconfigure(0, weight=1)
        controls_host.columnconfigure(0, weight=1)
        controls = self._create_scrollable_controls(controls_host, width=350)
        output = ttk.Frame(self.backtest_tab, padding=(0, 12, 12, 12))
        output.grid(row=0, column=1, sticky="nsew")
        output.rowconfigure(2, weight=1)
        output.columnconfigure(0, weight=1)

        data_box = ttk.LabelFrame(controls, text="Рыночные данные", padding=10)
        data_box.pack(fill="x", pady=(0, 8))
        self._labeled_entry(data_box, "Тикер", self.bt_ticker)
        self._labeled_entry(data_box, "Режим торгов", self.bt_board)
        self._labeled_entry(data_box, "Начало (ГГГГ-ММ-ДД)", self.bt_start)
        self._labeled_entry(data_box, "Конец (ГГГГ-ММ-ДД)", self.bt_end)
        self._labeled_combo(
            data_box,
            "Интервал",
            self.bt_interval_label,
            ["День", "1 час", "10 минут"],
        )

        strategy_box = ttk.LabelFrame(controls, text="Стратегия SMA", padding=10)
        strategy_box.pack(fill="x", pady=(0, 8))
        self._labeled_entry(strategy_box, "Быстрая SMA", self.bt_fast)
        self._labeled_entry(strategy_box, "Медленная SMA", self.bt_slow)

        risk_box = ttk.LabelFrame(controls, text="Капитал и издержки", padding=10)
        risk_box.pack(fill="x", pady=(0, 8))
        self._labeled_entry(risk_box, "Начальный капитал, ₽", self.bt_capital)
        self._labeled_entry(risk_box, "Доля позиции, %", self.bt_position_fraction)
        self._labeled_entry(risk_box, "Комиссия, %", self.bt_commission)
        self._labeled_entry(risk_box, "Половина спреда, %", self.bt_spread)
        self._labeled_entry(risk_box, "Проскальзывание, %", self.bt_slippage)

        self.bt_run_button = ttk.Button(
            controls,
            text="Запустить бэктест",
            command=self._start_backtest,
        )
        self.bt_run_button.pack(fill="x", pady=(4, 4), ipady=5)
        ttk.Button(
            controls,
            text="Экспорт сделок в CSV",
            command=self._export_trades,
        ).pack(fill="x", pady=4)
        ttk.Button(
            controls,
            text="Экспорт расчёта в CSV",
            command=self._export_curve,
        ).pack(fill="x", pady=4)
        ttk.Button(
            controls,
            text="Сохранить график PNG",
            command=self._save_chart,
        ).pack(fill="x", pady=4)

        metrics = ttk.Frame(output)
        metrics.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for index, (title, key) in enumerate(
            [
                ("Итоговый капитал", "capital"),
                ("Доходность", "return"),
                ("CAGR", "cagr"),
                ("Макс. просадка", "drawdown"),
                ("Sharpe", "sharpe"),
                ("Сделки", "trades"),
            ]
        ):
            card = ttk.LabelFrame(metrics, padding=(10, 6))
            card.grid(row=0, column=index, padx=3, sticky="ew")
            metrics.columnconfigure(index, weight=1)
            ttk.Label(card, text=title, style="MetricTitle.TLabel").pack()
            ttk.Label(
                card,
                textvariable=self.metric_vars[key],
                style="MetricValue.TLabel",
            ).pack()

        self.chart_notebook = ttk.Notebook(output)
        self.chart_notebook.grid(row=1, column=0, sticky="nsew")
        output.rowconfigure(1, weight=3)
        self.chart_frames: dict[str, ttk.Frame] = {}
        self.figures: dict[str, Figure] = {}
        self.canvases: dict[str, FigureCanvasTkAgg] = {}
        for key, title in (
            ("price", "Цена и SMA"),
            ("equity", "Кривая капитала"),
            ("drawdown", "Просадка"),
        ):
            frame = ttk.Frame(self.chart_notebook)
            self.chart_notebook.add(frame, text=title)
            self.chart_frames[key] = frame
            figure = Figure(figsize=(9, 4.5), dpi=100, constrained_layout=True)
            canvas = FigureCanvasTkAgg(figure, master=frame)
            canvas.draw()
            toolbar = NavigationToolbar2Tk(canvas, frame, pack_toolbar=False)
            toolbar.update()
            toolbar.pack(side="top", fill="x")
            canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
            self.figures[key] = figure
            self.canvases[key] = canvas

        trades_box = ttk.LabelFrame(output, text="Журнал сделок", padding=6)
        trades_box.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        trades_box.rowconfigure(0, weight=1)
        trades_box.columnconfigure(0, weight=1)
        columns = ("time", "action", "price", "position", "trade_return")
        self.trades_tree = ttk.Treeview(
            trades_box,
            columns=columns,
            show="headings",
            height=7,
        )
        headings = {
            "time": "Время",
            "action": "Действие",
            "price": "Цена",
            "position": "Доля позиции",
            "trade_return": "Доходность сделки",
        }
        widths = {"time": 170, "action": 100, "price": 100, "position": 120, "trade_return": 150}
        for column in columns:
            self.trades_tree.heading(column, text=headings[column])
            self.trades_tree.column(column, width=widths[column], anchor="center")
        scroll = ttk.Scrollbar(trades_box, orient="vertical", command=self.trades_tree.yview)
        self.trades_tree.configure(yscrollcommand=scroll.set)
        self.trades_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

    def _start_backtest(self) -> None:
        try:
            params = self._read_backtest_parameters()
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc), parent=self)
            return

        self.bt_run_button.configure(state="disabled")
        self.bt_status.set("Загружаю данные MOEX и выполняю расчёт…")
        self.logger.info(
            "Backtest started: ticker=%s board=%s interval=%s period=%s..%s SMA=%s/%s",
            params["ticker"],
            params["board"],
            params["interval"],
            params["start"],
            params["end"],
            params["fast"],
            params["slow"],
        )
        self.logger.debug("Backtest parameters: %s", params)

        def work() -> BacktestResult:
            client = MoexISSClient(board=params["board"])
            candles = client.get_candles(
                params["ticker"],
                params["start"],
                params["end"],
                params["interval"],
            )
            signals = generate_sma_signals(
                candles,
                SmaCrossoverConfig(
                    fast_window=params["fast"],
                    slow_window=params["slow"],
                ),
            )
            return run_backtest(
                signals,
                BacktestConfig(
                    initial_capital=params["capital"],
                    commission_rate=params["commission"] / 100,
                    half_spread_rate=params["spread"] / 100,
                    slippage_rate=params["slippage"] / 100,
                    position_fraction=params["position_fraction"] / 100,
                    annual_periods=None,
                ),
            )

        self._run_background(
            work,
            lambda result: self._display_backtest_result(result, params),
            on_finally=lambda: self.bt_run_button.configure(state="normal"),
        )

    def _read_backtest_parameters(self) -> dict[str, Any]:
        ticker = self.bt_ticker.get().strip().upper()
        board = self.bt_board.get().strip().upper()
        if not ticker or not board:
            raise ValueError("Тикер и режим торгов не должны быть пустыми.")
        try:
            start = datetime.strptime(self.bt_start.get().strip(), "%Y-%m-%d").date()
            end = datetime.strptime(self.bt_end.get().strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("Дата должна иметь формат ГГГГ-ММ-ДД.") from exc
        if start >= end:
            raise ValueError("Дата начала должна быть раньше даты окончания.")

        mapping = {
            "День": 24,
            "1 час": 60,
            "10 минут": 10,
        }
        if self.bt_interval_label.get() not in mapping:
            raise ValueError("Выберите поддерживаемый интервал.")
        interval = mapping[self.bt_interval_label.get()]

        try:
            fast = int(self.bt_fast.get())
            slow = int(self.bt_slow.get())
            capital = float(self.bt_capital.get().replace(" ", "").replace(",", "."))
            position_fraction = float(self.bt_position_fraction.get().replace(",", "."))
            commission = float(self.bt_commission.get().replace(",", "."))
            spread = float(self.bt_spread.get().replace(",", "."))
            slippage = float(self.bt_slippage.get().replace(",", "."))
        except ValueError as exc:
            raise ValueError("Числовые параметры содержат неверное значение.") from exc

        if fast < 2 or slow <= fast:
            raise ValueError("Медленная SMA должна быть больше быстрой, а быстрая — не меньше 2.")
        if capital <= 0:
            raise ValueError("Начальный капитал должен быть положительным.")
        if not 0 < position_fraction <= 100:
            raise ValueError("Доля позиции должна находиться в диапазоне 0–100%.")
        if commission < 0 or spread < 0 or slippage < 0:
            raise ValueError(
                "Комиссия, спред и проскальзывание не могут быть отрицательными."
            )

        return {
            "ticker": ticker,
            "board": board,
            "start": start,
            "end": end,
            "interval": interval,
            "fast": fast,
            "slow": slow,
            "capital": capital,
            "position_fraction": position_fraction,
            "commission": commission,
            "spread": spread,
            "slippage": slippage,
        }

    def _display_backtest_result(
        self,
        result: BacktestResult,
        params: dict[str, Any],
    ) -> None:
        self.backtest_result = result
        metrics = result.metrics
        self.metric_vars["capital"].set(f"{metrics['ending_capital']:,.0f} ₽".replace(",", " "))
        self.metric_vars["return"].set(f"{metrics['total_return']:.1%}")
        self.metric_vars["cagr"].set(f"{metrics['cagr']:.1%}")
        self.metric_vars["drawdown"].set(f"{metrics['max_drawdown']:.1%}")
        self.metric_vars["sharpe"].set(f"{metrics['sharpe']:.2f}")
        self.metric_vars["trades"].set(str(int(metrics["completed_trades"])))

        for item in self.trades_tree.get_children():
            self.trades_tree.delete(item)
        for _, row in result.trades.iterrows():
            trade_return = row.get("trade_return")
            trade_text = "" if pd.isna(trade_return) else f"{float(trade_return):.2%}"
            timestamp = pd.Timestamp(row["time"]).strftime("%Y-%m-%d %H:%M")
            self.trades_tree.insert(
                "",
                "end",
                values=(
                    timestamp,
                    row["action"],
                    f"{float(row['price']):.4f}",
                    f"{float(row['position_fraction']):.1%}",
                    trade_text,
                ),
            )

        self._plot_backtest(result, params["ticker"])
        self.bt_status.set(
            f"Бэктест завершён: {params['ticker']}, {len(result.curve)} свечей"
        )
        self.logger.info(
            "Backtest completed: ticker=%s bars=%s return=%.2f%% CAGR=%.2f%% maxDD=%.2f%% trades=%s",
            params["ticker"],
            len(result.curve),
            float(metrics["total_return"]) * 100.0,
            float(metrics["cagr"]) * 100.0,
            float(metrics["max_drawdown"]) * 100.0,
            int(metrics["completed_trades"]),
        )
        self.logger.debug("Backtest metrics: %s", metrics)

    def _plot_backtest(self, result: BacktestResult, ticker: str) -> None:
        figure = self.figures["price"]
        figure.clear()
        axis = figure.add_subplot(111)
        frame = result.curve[["close", "sma_fast", "sma_slow"]].dropna()
        axis.plot(frame.index, frame["close"], label=f"{ticker} close", linewidth=1.1)
        axis.plot(frame.index, frame["sma_fast"], label="SMA fast", linewidth=1.0)
        axis.plot(frame.index, frame["sma_slow"], label="SMA slow", linewidth=1.0)
        buys = result.trades[result.trades["action"] == "BUY"] if not result.trades.empty else pd.DataFrame()
        sells = result.trades[result.trades["action"] == "SELL"] if not result.trades.empty else pd.DataFrame()
        if not buys.empty:
            axis.scatter(pd.to_datetime(buys["time"]), buys["price"], marker="^", label="BUY", zorder=3)
        if not sells.empty:
            axis.scatter(pd.to_datetime(sells["time"]), sells["price"], marker="v", label="SELL", zorder=3)
        axis.set_title("Цена, скользящие средние и точки сделок")
        axis.set_ylabel("Цена")
        axis.grid(True, alpha=0.25)
        axis.legend(loc="best")
        self.canvases["price"].draw_idle()

        figure = self.figures["equity"]
        figure.clear()
        axis = figure.add_subplot(111)
        axis.plot(result.curve.index, result.curve["equity"], label="Стратегия")
        axis.plot(result.curve.index, result.curve["buy_hold_equity"], label="Buy & hold")
        axis.set_title("Изменение капитала")
        axis.set_ylabel("Капитал, ₽")
        axis.grid(True, alpha=0.25)
        axis.legend(loc="best")
        self.canvases["equity"].draw_idle()

        figure = self.figures["drawdown"]
        figure.clear()
        axis = figure.add_subplot(111)
        axis.fill_between(
            result.curve.index,
            result.curve["drawdown"].to_numpy(),
            0,
            alpha=0.5,
        )
        axis.plot(result.curve.index, result.curve["drawdown"], linewidth=0.8)
        axis.set_title("Просадка стратегии")
        axis.set_ylabel("Просадка")
        axis.yaxis.set_major_formatter(lambda value, _: f"{value:.0%}")
        axis.grid(True, alpha=0.25)
        self.canvases["drawdown"].draw_idle()

    def _export_trades(self) -> None:
        if self.backtest_result is None:
            messagebox.showinfo("Нет данных", "Сначала выполните бэктест.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Сохранить журнал сделок",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialdir=_reports_initial_dir(),
            initialfile=f"{self.bt_ticker.get().upper()}_trades.csv",
        )
        if path:
            self.backtest_result.trades.to_csv(path, index=False, encoding="utf-8-sig")
            self.bt_status.set(f"Сделки сохранены: {path}")

    def _export_curve(self) -> None:
        if self.backtest_result is None:
            messagebox.showinfo("Нет данных", "Сначала выполните бэктест.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Сохранить полный расчёт",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialdir=_reports_initial_dir(),
            initialfile=f"{self.bt_ticker.get().upper()}_backtest.csv",
        )
        if path:
            self.backtest_result.curve.to_csv(path, encoding="utf-8-sig")
            self.bt_status.set(f"Расчёт сохранён: {path}")

    def _save_chart(self) -> None:
        key = ("price", "equity", "drawdown")[self.chart_notebook.index("current")]
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Сохранить активный график",
            defaultextension=".png",
            filetypes=[("PNG", "*.png")],
            initialdir=_reports_initial_dir(),
            initialfile=f"{self.bt_ticker.get().upper()}_{key}.png",
        )
        if path:
            self.figures[key].savefig(path, dpi=180, bbox_inches="tight")
            self.bt_status.set(f"График сохранён: {path}")

    # ------------------------------------------------------------------
    # Sandbox tab
    # ------------------------------------------------------------------
    def _build_sandbox_tab(self) -> None:
        self.sandbox_tab.columnconfigure(1, weight=1)
        self.sandbox_tab.rowconfigure(0, weight=1)

        controls_host = ttk.Frame(self.sandbox_tab)
        controls_host.grid(row=0, column=0, sticky="nsew")
        controls_host.rowconfigure(0, weight=1)
        controls_host.columnconfigure(0, weight=1)
        controls = self._create_scrollable_controls(controls_host, width=350)
        info = ttk.Frame(self.sandbox_tab, padding=(0, 12, 12, 12))
        info.grid(row=0, column=1, sticky="nsew")
        info.columnconfigure(0, weight=1)
        info.rowconfigure(3, weight=1)

        auth_box = ttk.LabelFrame(controls, text="Подключение", padding=10)
        auth_box.pack(fill="x", pady=(0, 8))
        ttk.Label(auth_box, text="Sandbox API-токен").pack(anchor="w")
        token_row = ttk.Frame(auth_box)
        token_row.pack(fill="x", pady=(2, 6))
        self.token_entry = ttk.Entry(
            token_row,
            textvariable=self.sb_token,
            show="•",
            width=24,
        )
        self.token_entry.pack(side="left", fill="x", expand=True)
        self._enable_entry_clipboard(
            self.token_entry,
            strip_paste=True,
        )
        ttk.Button(
            token_row,
            text="Вставить",
            width=9,
            command=lambda: self._paste_into_entry(
                self.token_entry,
                strip_outer_whitespace=True,
            ),
        ).pack(side="left", padx=(4, 0))
        ttk.Checkbutton(
            auth_box,
            text="Показать токен",
            variable=self.sb_show_token,
            command=self._toggle_token_visibility,
        ).pack(anchor="w")
        ttk.Label(
            auth_box,
            text=(
                "Ctrl+V работает при русской и английской раскладке. "
                "Также доступны кнопка «Вставить» и меню по правому клику."
            ),
            wraplength=310,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(auth_box, text="CA bundle PEM — необязательно").pack(
            anchor="w", pady=(7, 0)
        )
        ca_row = ttk.Frame(auth_box)
        ca_row.pack(fill="x", pady=(2, 2))
        ttk.Entry(ca_row, textvariable=self.sb_ca_bundle).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(
            ca_row, text="…", width=3, command=self._browse_ca_bundle
        ).pack(side="left", padx=(4, 0))
        ttk.Label(
            auth_box,
            text=(
                "Обычно оставьте пустым: используется системное хранилище "
                "сертификатов Windows."
            ),
            wraplength=310,
            justify="left",
        ).pack(anchor="w", pady=(0, 4))
        ttk.Button(
            auth_box,
            text="Открыть установку сертификатов НУЦ",
            command=self._open_certificate_page,
        ).pack(fill="x", pady=2)
        ttk.Button(
            auth_box,
            text="Загрузить подключение из .env",
            command=lambda: self._load_settings_from_env(show_message=True),
        ).pack(fill="x", pady=(6, 2))
        ttk.Button(
            auth_box,
            text="Сохранить подключение в .env",
            command=self._save_settings_to_env,
        ).pack(fill="x", pady=2)
        ttk.Button(
            auth_box,
            text="Проверить подключение",
            command=self._check_sandbox_connection,
        ).pack(fill="x", pady=2)

        account_box = ttk.LabelFrame(controls, text="Виртуальный счёт", padding=10)
        account_box.pack(fill="x", pady=(0, 8))
        ttk.Label(account_box, text="Счёт").pack(anchor="w")
        self.account_combo = ttk.Combobox(
            account_box,
            textvariable=self.sb_account,
            state="readonly",
            width=33,
        )
        self.account_combo.pack(fill="x", pady=(2, 4))
        self.account_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._set_account_id_display(),
            add="+",
        )
        ttk.Label(account_box, text="Полный Account ID").pack(anchor="w")
        account_id_row = ttk.Frame(account_box)
        account_id_row.pack(fill="x", pady=(2, 5))
        self.account_id_entry = ttk.Entry(
            account_id_row,
            textvariable=self.sb_account_id_display,
            state="readonly",
            width=38,
            font=("Consolas", 8),
        )
        self.account_id_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(
            account_id_row,
            text="Копировать полный ID",
            command=self._copy_selected_account_id,
        ).pack(side="left", padx=(4, 0))
        self._hover_tooltips.append(
            HoverTooltip(
                self.account_id_entry,
                lambda: self.sb_account_id_display.get(),
            )
        )
        ttk.Button(
            account_box,
            text="Обновить список счетов",
            command=self._load_sandbox_accounts,
        ).pack(fill="x", pady=2)
        self._labeled_entry(account_box, "Пополнение, ₽", self.sb_initial_rub)
        ttk.Button(
            account_box,
            text="Создать и пополнить счёт",
            command=self._create_sandbox_account,
        ).pack(fill="x", pady=2)

        profile_box = ttk.LabelFrame(
            controls, text="Профили стратегии v3.7-beta1", padding=10
        )
        profile_box.pack(fill="x", pady=(0, 8))
        self.profile_mode_combo = self._labeled_combo(
            profile_box,
            "Редактируемый профиль",
            self.sb_profile_mode,
            list(PROFILE_MODES),
        )
        self.profile_mode_combo.bind(
            "<<ComboboxSelected>>",
            self._on_strategy_profile_selected,
            add="+",
        )
        self._register_sb_config_widget(self.profile_mode_combo, "readonly")
        profile_buttons = ttk.Frame(profile_box)
        profile_buttons.pack(fill="x", pady=(3, 2))
        self.profile_load_button = ttk.Button(
            profile_buttons,
            text="Загрузить",
            command=lambda: self._load_strategy_profile(
                self.sb_profile_mode.get(), show_message=True
            ),
        )
        self.profile_load_button.pack(
            side="left", fill="x", expand=True, padx=(0, 2)
        )
        self._register_sb_config_widget(self.profile_load_button)
        self.profile_save_button = ttk.Button(
            profile_buttons,
            text="Сохранить",
            command=self._save_selected_strategy_profile,
        )
        self.profile_save_button.pack(
            side="left", fill="x", expand=True, padx=2
        )
        self._register_sb_config_widget(self.profile_save_button)
        self.profile_reset_button = ttk.Button(
            profile_buttons,
            text="Сбросить",
            command=self._reset_selected_strategy_profile,
        )
        self.profile_reset_button.pack(
            side="left", fill="x", expand=True, padx=(2, 0)
        )
        self._register_sb_config_widget(self.profile_reset_button)
        ttk.Label(
            profile_box,
            textvariable=self.sb_profile_status,
            wraplength=310,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            profile_box,
            textvariable=self.sb_profile_hash,
            font=("Consolas", 8),
            wraplength=310,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(
            profile_box,
            text=(
                "DRY_RUN и SANDBOX_EXECUTION хранятся отдельно. Перед запуском "
                "программа применяет профиль соответствующего режима и проверяет "
                "его контрольную сумму."
            ),
            wraplength=310,
            justify="left",
        ).pack(anchor="w", pady=(5, 0))

        params_box = ttk.LabelFrame(
            controls, text="Инструмент и цикл", padding=10
        )
        params_box.pack(fill="x", pady=(0, 8))
        for label, variable in (
            ("Тикер", self.sb_ticker),
            ("Class code", self.sb_class_code),
        ):
            self._register_sb_config_widget(
                self._labeled_entry(params_box, label, variable)
            )
        self._register_sb_config_widget(
            self._labeled_combo(
                params_box,
                "Свечи",
                self.sb_interval,
                [
                    "CANDLE_INTERVAL_10_MIN",
                    "CANDLE_INTERVAL_HOUR",
                    "CANDLE_INTERVAL_DAY",
                ],
            ),
            "readonly",
        )
        for label, variable in (
            (
                "История, дней (расширяется автоматически)",
                self.sb_lookback,
            ),
            ("Пауза цикла, секунд", self.sb_poll),
            ("Максимум лотов PRIMARY", self.sb_max_lots),
        ):
            self._register_sb_config_widget(
                self._labeled_entry(params_box, label, variable)
            )

        role_box = ttk.LabelFrame(
            controls, text="PRIMARY и SHADOW", padding=10
        )
        role_box.pack(fill="x", pady=(0, 8))
        primary_combo = self._labeled_combo(
            role_box,
            "PRIMARY — единственная исполняемая стратегия",
            self.sb_primary_strategy,
            [STRATEGY_TITLES_RU[item] for item in VALID_STRATEGIES],
        )
        primary_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._sync_strategy_roles(),
            add="+",
        )
        self._register_sb_config_widget(primary_combo, "readonly")
        ttk.Label(role_box, text="SHADOW — только расчёт и журнал:").pack(
            anchor="w", pady=(2, 2)
        )
        for strategy in VALID_STRATEGIES:
            check = ttk.Checkbutton(
                role_box,
                text=STRATEGY_TITLES_RU[strategy],
                variable=self.sb_shadow_vars[strategy],
            )
            check.pack(anchor="w")
            self.sb_shadow_checkbuttons[strategy] = check
            self._register_sb_config_widget(check)
        ttk.Label(
            role_box,
            text=(
                "SHADOW-стратегии используют те же завершённые свечи, но "
                "никогда не передают заявку брокеру."
            ),
            wraplength=310,
            justify="left",
        ).pack(anchor="w", pady=(5, 0))

        sma_box = ttk.LabelFrame(
            controls, text="SMA с гистерезисом", padding=10
        )
        sma_box.pack(fill="x", pady=(0, 8))
        for label, variable in (
            ("Быстрая SMA", self.sb_fast),
            ("Медленная SMA", self.sb_slow),
            ("Гистерезис, %", self.sb_sma_hysteresis),
        ):
            self._register_sb_config_widget(
                self._labeled_entry(sma_box, label, variable)
            )

        donchian_box = ttk.LabelFrame(
            controls, text="Donchian + ATR", padding=10
        )
        donchian_box.pack(fill="x", pady=(0, 8))
        for label, variable in (
            ("Канал входа", self.sb_donchian_entry),
            ("Канал выхода", self.sb_donchian_exit),
            ("Окно ATR", self.sb_donchian_atr),
            ("ATR-множитель trailing stop", self.sb_donchian_stop),
        ):
            self._register_sb_config_widget(
                self._labeled_entry(donchian_box, label, variable)
            )

        ensemble_box = ttk.LabelFrame(
            controls, text="Ансамбль трендов", padding=10
        )
        ensemble_box.pack(fill="x", pady=(0, 8))
        for label, variable in (
            ("Быстрая SMA", self.sb_ensemble_fast),
            ("Медленная SMA / EMA", self.sb_ensemble_slow),
            ("Окно momentum", self.sb_ensemble_momentum),
            ("Окно breakout", self.sb_ensemble_breakout),
            ("Голосов для LONG (1–4)", self.sb_ensemble_votes),
        ):
            self._register_sb_config_widget(
                self._labeled_entry(ensemble_box, label, variable)
            )

        sizing_box = ttk.LabelFrame(
            controls, text="Размер позиции", padding=10
        )
        sizing_box.pack(fill="x", pady=(0, 8))
        for label, variable in (
            (
                "Целевая годовая волатильность, % (пусто = выкл.)",
                self.sb_target_volatility,
            ),
            ("Окно волатильности", self.sb_volatility_window),
            ("Максимальный вес стратегии, %", self.sb_max_strategy_weight),
        ):
            self._register_sb_config_widget(
                self._labeled_entry(sizing_box, label, variable)
            )
        ttk.Label(
            sizing_box,
            text=(
                "В v3.5 вес масштабирует максимум лотов. Это ещё не "
                "портфельный риск-движок v3.6."
            ),
            wraplength=310,
            justify="left",
        ).pack(anchor="w")

        resilience_box = ttk.LabelFrame(
            controls, text="Устойчивость v3.7-beta1", padding=10
        )
        resilience_box.pack(fill="x", pady=(0, 8))
        for label, variable in (
            ("Connect timeout, секунд", self.sb_connect_timeout),
            ("Read timeout, секунд", self.sb_read_timeout),
            ("Ошибок до circuit breaker", self.sb_failure_threshold),
            ("Начальная пауза circuit breaker, секунд", self.sb_circuit_seconds),
            ("Макс. пауза circuit breaker, секунд", self.sb_circuit_max_seconds),
            ("Макс. возраст сигнала, секунд (0 = авто)", self.sb_max_signal_age),
            ("Попыток сверки позиции", self.sb_reconcile_attempts),
            ("Пауза между сверками, секунд", self.sb_reconcile_delay),
            (
                "Периодическая сверка портфеля, секунд",
                self.sb_portfolio_reconcile_interval,
            ),
        ):
            self._register_sb_config_widget(
                self._labeled_entry(resilience_box, label, variable)
            )
        ttk.Label(
            resilience_box,
            text=(
                "При серии сетевых ошибок новые заявки блокируются. После "
                "восстановления сначала проверяются pending-order и портфель. "
                "Вне исполнимой торговой фазы MARKET_IDLE реже проверяет статус "
                "рынка и портфель, не пересчитывая последнюю свечу."
            ),
            wraplength=310,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        actions_box = ttk.LabelFrame(controls, text="Запуск", padding=10)
        actions_box.pack(fill="x", pady=(0, 8))
        ttk.Button(
            actions_box,
            text="Один цикл — только dry-run",
            command=lambda: self._run_sandbox_once(execute=False),
        ).pack(fill="x", pady=2)
        ttk.Button(
            actions_box,
            text="Запустить мониторинг — dry-run",
            command=lambda: self._start_robot_loop(execute=False),
        ).pack(fill="x", pady=2)
        ttk.Separator(actions_box).pack(fill="x", pady=7)
        ttk.Checkbutton(
            actions_box,
            text="Разрешить тестовые заявки",
            variable=self.sb_arm_checkbox,
        ).pack(anchor="w")
        ttk.Label(actions_box, text="Введите SANDBOX:").pack(anchor="w", pady=(5, 0))
        ttk.Entry(actions_box, textvariable=self.sb_confirm_text).pack(fill="x", pady=(2, 5))
        ttk.Button(
            actions_box,
            text="Один цикл с тестовой заявкой",
            command=lambda: self._run_sandbox_once(execute=True),
            style="Danger.TButton",
        ).pack(fill="x", pady=2)
        ttk.Button(
            actions_box,
            text="Запустить Sandbox-робота",
            command=lambda: self._start_robot_loop(execute=True),
            style="Danger.TButton",
        ).pack(fill="x", pady=2)
        ttk.Button(
            actions_box,
            text="Остановить робота",
            command=self._stop_robot,
        ).pack(fill="x", pady=(8, 2))

        dashboard = ttk.LabelFrame(info, text="Состояние робота v3.7-beta1", padding=10)
        dashboard.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        dashboard.columnconfigure(1, weight=1)
        dashboard.columnconfigure(3, weight=1)
        dashboard_items = [
            ("API", "api"),
            ("Рынок", "market"),
            ("Последняя проверка", "last_check"),
            ("Последняя свеча", "last_candle"),
            ("Возраст данных", "data_age"),
            ("Режим", "mode"),
            ("Профиль", "profile"),
            ("Config hash", "config_hash"),
            ("PRIMARY", "primary"),
            ("Сигнал", "signal"),
            ("Risk Engine", "risk"),
            ("Причина", "reason"),
            ("Позиция", "position"),
            ("Pending order", "pending"),
            ("Ошибок подряд", "failures"),
            ("Следующая попытка", "next_retry"),
        ]
        for index, (label, key) in enumerate(dashboard_items):
            row = index // 2
            column = (index % 2) * 2
            ttk.Label(dashboard, text=f"{label}:").grid(
                row=row, column=column, sticky="w", padx=(0, 5), pady=2
            )
            ttk.Label(
                dashboard, textvariable=self.sb_dashboard_vars[key]
            ).grid(row=row, column=column + 1, sticky="w", pady=2)

        warning = ttk.LabelFrame(info, text="Важно", padding=12)
        warning.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(
            warning,
            text=(
                "Этот интерфейс использует только методы T-Invest Sandbox. "
                "В проекте нет метода выставления заявок на реальном счёте.\n"
                "Dry-run рассчитывает решение, но не отправляет даже виртуальную заявку. "
                "В v3.7-beta1 Risk Engine проверяет PRIMARY и в Dry-run, и перед "
                "созданием Sandbox-intent. Подтверждённый fill учитывается в risk_state.json "
                "только после portfolio reconciliation.\n"
                "Режим исполнения требует сохранённого Sandbox risk-профиля, флажка, "
                "слова SANDBOX и дополнительного подтверждения."
            ),
            wraplength=850,
            justify="left",
        ).pack(anchor="w")

        strategy_box = ttk.LabelFrame(
            info, text="PRIMARY / SHADOW — последнее сравнение", padding=8
        )
        strategy_box.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        strategy_columns = ("role", "strategy", "signal", "weight", "lots", "reason")
        self.strategy_tree = ttk.Treeview(
            strategy_box,
            columns=strategy_columns,
            show="headings",
            height=4,
        )
        strategy_headings = {
            "role": "Роль",
            "strategy": "Стратегия",
            "signal": "Сигнал",
            "weight": "Вес",
            "lots": "Цель, лоты",
            "reason": "Основание",
        }
        strategy_widths = {
            "role": 75,
            "strategy": 150,
            "signal": 60,
            "weight": 75,
            "lots": 80,
            "reason": 430,
        }
        for column in strategy_columns:
            self.strategy_tree.heading(column, text=strategy_headings[column])
            self.strategy_tree.column(
                column,
                width=strategy_widths[column],
                stretch=(column == "reason"),
            )
        strategy_scroll = ttk.Scrollbar(
            strategy_box, orient="vertical", command=self.strategy_tree.yview
        )
        self.strategy_tree.configure(yscrollcommand=strategy_scroll.set)
        self.strategy_tree.pack(side="left", fill="x", expand=True)
        strategy_scroll.pack(side="right", fill="y")

        result_box = ttk.LabelFrame(info, text="Последний результат", padding=8)
        result_box.grid(row=3, column=0, sticky="nsew")
        result_box.rowconfigure(0, weight=1)
        result_box.columnconfigure(0, weight=1)
        self.sandbox_result_text = tk.Text(
            result_box,
            wrap="word",
            font=("Consolas", 10),
            state="disabled",
        )
        sb_scroll = ttk.Scrollbar(
            result_box,
            orient="vertical",
            command=self.sandbox_result_text.yview,
        )
        self.sandbox_result_text.configure(yscrollcommand=sb_scroll.set)
        self.sandbox_result_text.grid(row=0, column=0, sticky="nsew")
        sb_scroll.grid(row=0, column=1, sticky="ns")
        self._sync_strategy_roles()

    def _register_sb_config_widget(
        self,
        widget: tk.Widget,
        normal_state: str = "normal",
    ) -> tk.Widget:
        self.sb_config_widgets.append((widget, normal_state))
        return widget

    def _selected_primary_strategy(self) -> str:
        raw = self.sb_primary_strategy.get().strip()
        for strategy, title in STRATEGY_TITLES_RU.items():
            if raw == title or raw.lower() == strategy:
                return strategy
        raise ValueError("Выберите корректную PRIMARY-стратегию.")

    def _sync_strategy_roles(self) -> None:
        try:
            primary = self._selected_primary_strategy()
        except ValueError:
            return
        for strategy, check in self.sb_shadow_checkbuttons.items():
            if self.sb_config_locked:
                check.configure(state="disabled")
            elif strategy == primary:
                self.sb_shadow_vars[strategy].set(False)
                check.configure(state="disabled")
            else:
                check.configure(state="normal")

    def _set_sb_config_locked(self, locked: bool) -> None:
        self.sb_config_locked = bool(locked)
        for widget, normal_state in self.sb_config_widgets:
            try:
                widget.configure(state="disabled" if locked else normal_state)
            except tk.TclError:
                pass
        self._sync_strategy_roles()

    def _toggle_token_visibility(self) -> None:
        self.token_entry.configure(show="" if self.sb_show_token.get() else "•")

    def _enable_entry_clipboard(
        self,
        entry: ttk.Entry,
        *,
        strip_paste: bool = False,
    ) -> None:
        """Add robust copy/paste bindings and a right-click menu to an entry.

        The explicit keycode handling makes Ctrl+V work even when Windows uses
        a Russian keyboard layout and Tk reports a Cyrillic keysym.
        """
        entry._moex_strip_paste = bool(strip_paste)  # type: ignore[attr-defined]
        entry.bind(
            "<Control-KeyPress>",
            self._on_entry_control_shortcut,
            add="+",
        )
        entry.bind(
            "<Shift-Insert>",
            lambda event, widget=entry: self._paste_into_entry(
                widget,
                strip_outer_whitespace=bool(
                    getattr(widget, "_moex_strip_paste", False)
                ),
            ),
            add="+",
        )
        entry.bind(
            "<Control-Insert>",
            lambda event, widget=entry: self._copy_from_entry(widget),
            add="+",
        )
        entry.bind(
            "<Button-3>",
            lambda event, widget=entry: self._show_entry_context_menu(
                event, widget
            ),
            add="+",
        )

    def _on_entry_control_shortcut(self, event: tk.Event) -> str | None:
        action = resolve_clipboard_action(
            keysym=getattr(event, "keysym", ""),
            keycode=getattr(event, "keycode", -1),
            state=getattr(event, "state", 0),
        )
        widget = getattr(event, "widget", None)
        if not isinstance(widget, (ttk.Entry, tk.Entry)):
            return None

        if action == "paste":
            return self._paste_into_entry(
                widget,
                strip_outer_whitespace=bool(
                    getattr(widget, "_moex_strip_paste", False)
                ),
            )
        if action == "copy":
            return self._copy_from_entry(widget)
        if action == "cut":
            return self._cut_from_entry(widget)
        if action == "select_all":
            return self._select_all_in_entry(widget)
        return None

    @staticmethod
    def _entry_selection(entry: ttk.Entry | tk.Entry) -> tuple[int, int] | None:
        try:
            return int(entry.index("sel.first")), int(entry.index("sel.last"))
        except tk.TclError:
            return None

    def _copy_from_entry(self, entry: ttk.Entry | tk.Entry) -> str:
        selection = self._entry_selection(entry)
        if selection is None:
            self.bell()
            return "break"
        start, end = selection
        value = entry.get()[start:end]
        self.clipboard_clear()
        self.clipboard_append(value)
        # Keep the clipboard content after the menu/command callback returns.
        self.update_idletasks()
        return "break"

    def _cut_from_entry(self, entry: ttk.Entry | tk.Entry) -> str:
        selection = self._entry_selection(entry)
        if selection is None:
            self.bell()
            return "break"
        start, end = selection
        value = entry.get()[start:end]
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update_idletasks()
        entry.delete(start, end)
        entry.icursor(start)
        return "break"

    def _paste_into_entry(
        self,
        entry: ttk.Entry | tk.Entry,
        *,
        strip_outer_whitespace: bool = False,
    ) -> str:
        try:
            value = self.clipboard_get()
        except tk.TclError:
            self.bell()
            return "break"

        value = normalize_pasted_text(
            str(value),
            strip_outer_whitespace=strip_outer_whitespace,
        )
        selection = self._entry_selection(entry)
        if selection is not None:
            start, end = selection
            entry.delete(start, end)
            insert_at = start
        else:
            insert_at = int(entry.index("insert"))

        entry.insert(insert_at, value)
        entry.icursor(insert_at + len(value))
        entry.focus_set()
        return "break"

    @staticmethod
    def _select_all_in_entry(entry: ttk.Entry | tk.Entry) -> str:
        entry.selection_range(0, "end")
        entry.icursor("end")
        entry.focus_set()
        return "break"

    @staticmethod
    def _clear_entry(entry: ttk.Entry | tk.Entry) -> None:
        entry.delete(0, "end")
        entry.focus_set()

    def _show_entry_context_menu(
        self,
        event: tk.Event,
        entry: ttk.Entry | tk.Entry,
    ) -> str:
        entry.focus_set()
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(
            label="Вырезать",
            command=lambda: self._cut_from_entry(entry),
        )
        menu.add_command(
            label="Копировать",
            command=lambda: self._copy_from_entry(entry),
        )
        menu.add_command(
            label="Вставить",
            command=lambda: self._paste_into_entry(
                entry,
                strip_outer_whitespace=bool(
                    getattr(entry, "_moex_strip_paste", False)
                ),
            ),
        )
        menu.add_separator()
        menu.add_command(
            label="Выделить всё",
            command=lambda: self._select_all_in_entry(entry),
        )
        menu.add_command(
            label="Очистить",
            command=lambda: self._clear_entry(entry),
        )
        try:
            menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            menu.grab_release()
        return "break"

    def _load_settings_from_env(
        self,
        show_message: bool,
        *,
        include_strategy: bool = False,
    ) -> None:
        values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
        token = self.secret_provider.get("TBANK_SANDBOX_TOKEN")
        if not token and self.secret_provider.secure:
            legacy_token = str(values.get("TBANK_SANDBOX_TOKEN") or "").strip()
            if legacy_token:
                self.secret_provider.set("TBANK_SANDBOX_TOKEN", legacy_token)
                EnvFileSecretProvider(ENV_PATH).delete("TBANK_SANDBOX_TOKEN")
                token = legacy_token
        if token:
            self.sb_token.set(token)
        connection_mapping: list[tuple[tk.StringVar, str, str]] = [
            (self.sb_ca_bundle, "TBANK_CA_BUNDLE", ""),
            (self.sb_account, "TBANK_SANDBOX_ACCOUNT_ID", ""),
            (self.sb_initial_rub, "SANDBOX_INITIAL_RUB", "1000000"),
            (self.sb_connect_timeout, "TBANK_CONNECT_TIMEOUT_SECONDS", "8"),
            (self.sb_read_timeout, "TBANK_READ_TIMEOUT_SECONDS", "25"),
        ]
        for variable, key, default in connection_mapping:
            value = values.get(key)
            if value is not None:
                variable.set(str(value))
            elif not variable.get():
                variable.set(default)

        if include_strategy:
            strategy_mapping: list[tuple[tk.StringVar, str, str]] = [
                (self.sb_ticker, "ROBOT_TICKER", "SBER"),
                (self.sb_class_code, "ROBOT_CLASS_CODE", "TQBR"),
                (
                    self.sb_interval,
                    "ROBOT_CANDLE_INTERVAL",
                    "CANDLE_INTERVAL_HOUR",
                ),
                (self.sb_fast, "ROBOT_FAST_WINDOW", "20"),
                (self.sb_slow, "ROBOT_SLOW_WINDOW", "50"),
                (
                    self.sb_sma_hysteresis,
                    "ROBOT_SMA_HYSTERESIS_PERCENT",
                    "0.2",
                ),
                (self.sb_donchian_entry, "ROBOT_DONCHIAN_ENTRY_WINDOW", "55"),
                (self.sb_donchian_exit, "ROBOT_DONCHIAN_EXIT_WINDOW", "20"),
                (self.sb_donchian_atr, "ROBOT_DONCHIAN_ATR_WINDOW", "20"),
                (
                    self.sb_donchian_stop,
                    "ROBOT_DONCHIAN_TRAILING_STOP_ATR",
                    "3.0",
                ),
                (self.sb_ensemble_fast, "ROBOT_ENSEMBLE_SMA_FAST", "50"),
                (self.sb_ensemble_slow, "ROBOT_ENSEMBLE_SMA_SLOW", "200"),
                (
                    self.sb_ensemble_momentum,
                    "ROBOT_ENSEMBLE_MOMENTUM_WINDOW",
                    "126",
                ),
                (
                    self.sb_ensemble_breakout,
                    "ROBOT_ENSEMBLE_BREAKOUT_WINDOW",
                    "100",
                ),
                (
                    self.sb_ensemble_votes,
                    "ROBOT_ENSEMBLE_VOTE_THRESHOLD",
                    "3",
                ),
                (
                    self.sb_target_volatility,
                    "ROBOT_TARGET_VOLATILITY_PERCENT",
                    "",
                ),
                (self.sb_volatility_window, "ROBOT_VOLATILITY_WINDOW", "20"),
                (
                    self.sb_max_strategy_weight,
                    "ROBOT_MAX_STRATEGY_WEIGHT_PERCENT",
                    "100",
                ),
                (self.sb_lookback, "ROBOT_LOOKBACK_DAYS", "30"),
                (self.sb_poll, "ROBOT_POLL_SECONDS", "300"),
                (self.sb_max_lots, "ROBOT_MAX_ORDER_LOTS", "1"),
                (self.sb_failure_threshold, "ROBOT_FAILURE_THRESHOLD", "3"),
                (self.sb_circuit_seconds, "ROBOT_CIRCUIT_OPEN_SECONDS", "60"),
                (
                    self.sb_circuit_max_seconds,
                    "ROBOT_CIRCUIT_MAX_OPEN_SECONDS",
                    "900",
                ),
                (self.sb_max_signal_age, "ROBOT_MAX_SIGNAL_AGE_SECONDS", "0"),
                (self.sb_reconcile_attempts, "ROBOT_RECONCILE_ATTEMPTS", "3"),
                (
                    self.sb_reconcile_delay,
                    "ROBOT_RECONCILE_DELAY_SECONDS",
                    "0.5",
                ),
                (
                    self.sb_portfolio_reconcile_interval,
                    "ROBOT_PORTFOLIO_RECONCILE_INTERVAL_SECONDS",
                    "900",
                ),
            ]
            self._strategy_profile_loading = True
            try:
                for variable, key, default in strategy_mapping:
                    value = values.get(key)
                    if value is not None:
                        variable.set(str(value))
                    elif not variable.get():
                        variable.set(default)
                primary_raw = str(
                    values.get("ROBOT_PRIMARY_STRATEGY") or "sma"
                ).strip().lower()
                primary_title = STRATEGY_TITLES_RU.get(
                    primary_raw,
                    STRATEGY_TITLES_RU["sma"],
                )
                self.sb_primary_strategy.set(primary_title)
                shadow_raw = str(values.get("ROBOT_SHADOW_STRATEGIES") or "")
                requested_shadows = {
                    item.strip().lower()
                    for item in shadow_raw.split(",")
                    if item.strip()
                }
                for strategy, variable in self.sb_shadow_vars.items():
                    variable.set(
                        strategy in requested_shadows and strategy != primary_raw
                    )
                self._sync_strategy_roles()
            finally:
                self._strategy_profile_loading = False

        self.diag_ticker.set(self.sb_ticker.get().strip().upper() or "SBER")
        self.diag_class_code.set(
            self.sb_class_code.get().strip().upper() or "TQBR"
        )
        if show_message:
            if ENV_PATH.exists():
                note = (
                    "Токен, счёт и сетевые параметры загружены из .env. "
                    "PRIMARY/SHADOW загружаются из отдельного профиля режима."
                )
                messagebox.showinfo("Настройки", note, parent=self)
            else:
                messagebox.showinfo(
                    "Настройки",
                    ".env пока не существует. Введите токен и сохраните подключение.",
                    parent=self,
                )

    def _save_settings_to_env(self) -> None:
        try:
            connect_timeout, read_timeout = self._read_client_settings()
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc), parent=self)
            return
        token = self.sb_token.get().strip()
        if not token:
            messagebox.showerror("Нет токена", "Введите Sandbox API-токен.", parent=self)
            return

        self.secret_provider.set("TBANK_SANDBOX_TOKEN", token)
        ENV_PATH.touch(exist_ok=True)
        values = {
            "TBANK_CA_BUNDLE": self.sb_ca_bundle.get().strip(),
            "TBANK_SANDBOX_ACCOUNT_ID": self._selected_account_id(optional=True),
            "SANDBOX_INITIAL_RUB": self.sb_initial_rub.get().strip(),
            "TBANK_CONNECT_TIMEOUT_SECONDS": str(connect_timeout),
            "TBANK_READ_TIMEOUT_SECONDS": str(read_timeout),
            "ARM_SANDBOX_TRADING": "NO",
        }
        for key, value in values.items():
            set_key(str(ENV_PATH), key, value or "", quote_mode="auto")
        if self.secret_provider.secure:
            EnvFileSecretProvider(ENV_PATH).delete("TBANK_SANDBOX_TOKEN")
        self.logger.info("Settings saved to %s", ENV_PATH)
        storage_note = (
            "Файл .env не содержит токен."
            if self.secret_provider.secure
            else "Не публикуйте .env: используется совместимый token fallback."
        )
        messagebox.showinfo(
            "Подключение сохранено",
            f"Токен сохранён через: {self.secret_provider.name}.\n"
            f"Счёт и сетевые параметры сохранены локально в {ENV_PATH.name}.\n"
            "PRIMARY/SHADOW и торговые параметры сохраняются отдельно в "
            f"{STRATEGY_PROFILE_PATH.name}.\n"
            f"{storage_note}",
            parent=self,
        )
        self._refresh_readiness()

    def _strategy_profile_variables(self) -> list[tk.Variable]:
        return [
            self.sb_ticker,
            self.sb_class_code,
            self.sb_interval,
            self.sb_primary_strategy,
            *self.sb_shadow_vars.values(),
            self.sb_fast,
            self.sb_slow,
            self.sb_sma_hysteresis,
            self.sb_donchian_entry,
            self.sb_donchian_exit,
            self.sb_donchian_atr,
            self.sb_donchian_stop,
            self.sb_ensemble_fast,
            self.sb_ensemble_slow,
            self.sb_ensemble_momentum,
            self.sb_ensemble_breakout,
            self.sb_ensemble_votes,
            self.sb_target_volatility,
            self.sb_volatility_window,
            self.sb_max_strategy_weight,
            self.sb_lookback,
            self.sb_poll,
            self.sb_max_lots,
            self.sb_connect_timeout,
            self.sb_read_timeout,
            self.sb_failure_threshold,
            self.sb_circuit_seconds,
            self.sb_circuit_max_seconds,
            self.sb_max_signal_age,
            self.sb_reconcile_attempts,
            self.sb_reconcile_delay,
            self.sb_portfolio_reconcile_interval,
        ]

    def _bind_strategy_profile_traces(self) -> None:
        for variable in self._strategy_profile_variables():
            variable.trace_add("write", self._mark_strategy_profile_dirty)

    def _mark_strategy_profile_dirty(self, *_args: Any) -> None:
        if self._strategy_profile_loading or self.sb_config_locked:
            return
        self._strategy_profile_dirty = True
        mode = self.sb_profile_mode.get().strip().upper() or "DRY_RUN"
        self.sb_profile_status.set(
            f"{mode}: есть несохранённые изменения"
        )

    def _initialize_strategy_profiles(self) -> None:
        try:
            document = self.strategy_profile_store.load_document()
            profiles = document.get("profiles", {})
            if not profiles:
                connect_timeout, read_timeout = self._read_client_settings()
                for mode in PROFILE_MODES:
                    config = self._read_bot_config(dry_run=(mode == "DRY_RUN"))
                    profile = bot_config_to_profile(
                        config,
                        connect_timeout_seconds=connect_timeout,
                        read_timeout_seconds=read_timeout,
                    )
                    self.strategy_profile_store.save_profile(
                        mode,
                        profile,
                        select=(mode == "DRY_RUN"),
                    )
                self._journal_config_event(
                    "CONFIG_LOADED",
                    mode="DRY_RUN",
                    payload={"source": "legacy_env_or_defaults", "migrated": True},
                )
            selected = self.strategy_profile_store.last_selected_mode()
            self.sb_profile_mode.set(selected)
            self._load_strategy_profile(selected, show_message=False)
            self.strategy_profile_error = None
        except (StrategyProfileError, ValueError) as exc:
            self.strategy_profile_error = str(exc)
            self.sb_profile_status.set(
                "Ошибка профилей: Sandbox Execution заблокирован"
            )
            self.sb_profile_hash.set("CONFIG_MISMATCH")
            self.logger.error("Strategy profile initialization failed: %s", exc)
            self._journal_config_event(
                "CONFIG_MISMATCH",
                mode=self.sb_profile_mode.get() or "DRY_RUN",
                severity="ERROR",
                payload={"error": str(exc), "phase": "initialization"},
            )

    def _capture_strategy_profile(self, mode: str) -> dict[str, Any]:
        normalized = mode.strip().upper()
        if normalized not in PROFILE_MODES:
            raise ValueError(f"Неизвестный профиль: {mode}")
        config = self._read_bot_config(dry_run=(normalized == "DRY_RUN"))
        connect_timeout, read_timeout = self._read_client_settings()
        return bot_config_to_profile(
            config,
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
        )

    def _apply_strategy_profile_to_ui(self, profile: dict[str, Any]) -> None:
        self._strategy_profile_loading = True
        try:
            self.sb_ticker.set(str(profile.get("ticker") or "SBER"))
            self.sb_class_code.set(str(profile.get("class_code") or "TQBR"))
            self.sb_interval.set(
                str(profile.get("candle_interval") or "CANDLE_INTERVAL_HOUR")
            )
            primary = str(profile.get("primary_strategy") or "sma")
            self.sb_primary_strategy.set(
                STRATEGY_TITLES_RU.get(primary, STRATEGY_TITLES_RU["sma"])
            )
            shadows = {str(item) for item in profile.get("shadow_strategies") or []}
            for strategy, variable in self.sb_shadow_vars.items():
                variable.set(strategy in shadows and strategy != primary)
            self.sb_fast.set(str(profile.get("fast_window", 20)))
            self.sb_slow.set(str(profile.get("slow_window", 50)))
            self.sb_sma_hysteresis.set(
                str(float(profile.get("sma_hysteresis_percent", 0.002)) * 100.0)
            )
            self.sb_donchian_entry.set(
                str(profile.get("donchian_entry_window", 55))
            )
            self.sb_donchian_exit.set(
                str(profile.get("donchian_exit_window", 20))
            )
            self.sb_donchian_atr.set(
                str(profile.get("donchian_atr_window", 20))
            )
            self.sb_donchian_stop.set(
                str(profile.get("donchian_trailing_stop_atr", 3.0))
            )
            self.sb_ensemble_fast.set(str(profile.get("ensemble_sma_fast", 50)))
            self.sb_ensemble_slow.set(str(profile.get("ensemble_sma_slow", 200)))
            self.sb_ensemble_momentum.set(
                str(profile.get("ensemble_momentum_window", 126))
            )
            self.sb_ensemble_breakout.set(
                str(profile.get("ensemble_breakout_window", 100))
            )
            self.sb_ensemble_votes.set(
                str(profile.get("ensemble_vote_threshold", 3))
            )
            target = profile.get("annual_target_volatility")
            self.sb_target_volatility.set(
                "" if target in (None, "") else str(float(target) * 100.0)
            )
            self.sb_volatility_window.set(
                str(profile.get("volatility_window", 20))
            )
            self.sb_max_strategy_weight.set(
                str(float(profile.get("max_strategy_weight", 1.0)) * 100.0)
            )
            self.sb_lookback.set(str(profile.get("lookback_days", 30)))
            self.sb_poll.set(str(profile.get("poll_seconds", 300)))
            self.sb_max_lots.set(str(profile.get("max_order_lots", 1)))
            self.sb_connect_timeout.set(
                str(profile.get("connect_timeout_seconds", 8))
            )
            self.sb_read_timeout.set(str(profile.get("read_timeout_seconds", 25)))
            self.sb_failure_threshold.set(
                str(profile.get("failure_threshold", 3))
            )
            self.sb_circuit_seconds.set(
                str(profile.get("circuit_open_seconds", 60))
            )
            self.sb_circuit_max_seconds.set(
                str(profile.get("circuit_max_open_seconds", 900))
            )
            self.sb_max_signal_age.set(
                str(profile.get("max_signal_age_seconds", 0))
            )
            self.sb_reconcile_attempts.set(
                str(profile.get("reconcile_attempts", 3))
            )
            self.sb_reconcile_delay.set(
                str(profile.get("reconcile_delay_seconds", 0.5))
            )
            self.sb_portfolio_reconcile_interval.set(
                str(profile.get("portfolio_reconcile_interval_seconds", 900))
            )
            self._sync_strategy_roles()
        finally:
            self._strategy_profile_loading = False

    def _journal_config_event(
        self,
        event_type: str,
        *,
        mode: str,
        payload: dict[str, Any],
        severity: str = "INFO",
        config_hash: str | None = None,
    ) -> None:
        try:
            self.event_journal.record(
                JournalEvent(
                    category="configuration",
                    event_type=event_type,
                    severity=severity,
                    account_id=self._selected_account_id(optional=True) or None,
                    ticker=self.sb_ticker.get().strip().upper() or None,
                    mode=mode,
                    status=event_type.lower(),
                    strategy_id=(
                        self._selected_primary_strategy()
                        if self.sb_primary_strategy.get().strip()
                        else None
                    ),
                    config_hash=config_hash,
                    payload=payload,
                )
            )
        except Exception:
            self.logger.exception("Failed to write configuration event")

    def _load_strategy_profile(self, mode: str, *, show_message: bool) -> bool:
        if self.sb_config_locked and show_message:
            messagebox.showwarning(
                "Конфигурация заблокирована",
                "Остановите активную сессию перед загрузкой профиля.",
                parent=self,
            )
            return False
        normalized = mode.strip().upper()
        try:
            loaded = self.strategy_profile_store.load_profile(normalized)
            if loaded is None:
                raise StrategyProfileError(
                    f"Профиль {normalized} ещё не сохранён."
                )
            self._apply_strategy_profile_to_ui(dict(loaded["config"]))
            self.strategy_profile_store.set_last_selected_mode(normalized)
            self._strategy_profile_dirty = False
            self._loaded_profile_mode = normalized
            self.sb_profile_mode.set(normalized)
            short_hash = str(loaded["config_hash"])
            self.sb_profile_hash.set(f"hash: {short_hash[:16]}")
            self.sb_profile_status.set(
                f"{normalized}: загружен {loaded.get('updated_at') or 'без даты'}"
            )
            self.strategy_profile_error = None
            self._journal_config_event(
                "CONFIG_LOADED",
                mode=normalized,
                config_hash=short_hash,
                payload={"updated_at": loaded.get("updated_at")},
            )
            if show_message:
                messagebox.showinfo(
                    "Профиль загружен",
                    f"Применён профиль {normalized}.\nHash: {short_hash[:16]}",
                    parent=self,
                )
            return True
        except (StrategyProfileError, ValueError) as exc:
            self.strategy_profile_error = str(exc)
            self.sb_profile_status.set(f"Ошибка профиля {normalized}: {exc}")
            self.sb_profile_hash.set("CONFIG_MISMATCH")
            self._journal_config_event(
                "CONFIG_MISMATCH",
                mode=normalized,
                severity="ERROR",
                payload={"error": str(exc), "phase": "load"},
            )
            if show_message:
                messagebox.showerror("Ошибка профиля", str(exc), parent=self)
            return False

    def _save_selected_strategy_profile(self) -> bool:
        if self.sb_config_locked:
            messagebox.showwarning(
                "Конфигурация заблокирована",
                "Остановите активную сессию перед сохранением профиля.",
                parent=self,
            )
            return False
        normalized = self.sb_profile_mode.get().strip().upper()
        try:
            profile = self._capture_strategy_profile(normalized)
            saved = self.strategy_profile_store.save_profile(
                normalized,
                profile,
                select=True,
            )
            self._strategy_profile_dirty = False
            self._loaded_profile_mode = normalized
            self.strategy_profile_error = None
            config_hash = str(saved["config_hash"])
            self.sb_profile_hash.set(f"hash: {config_hash[:16]}")
            self.sb_profile_status.set(
                f"{normalized}: сохранён {saved['updated_at']}"
            )
            self._journal_config_event(
                "CONFIG_CHANGED",
                mode=normalized,
                config_hash=config_hash,
                payload={"updated_at": saved["updated_at"]},
            )
            return True
        except (ValueError, StrategyProfileError) as exc:
            messagebox.showerror("Ошибка профиля", str(exc), parent=self)
            return False

    def _reset_selected_strategy_profile(self) -> None:
        if self.sb_config_locked:
            messagebox.showwarning(
                "Конфигурация заблокирована",
                "Остановите активную сессию перед сбросом профиля.",
                parent=self,
            )
            return
        normalized = self.sb_profile_mode.get().strip().upper()
        reset_note = (
            "\n\nФайл профилей повреждён: явный сброс пересоздаст весь "
            "strategy_profiles.json, включая настройки второго режима."
            if self.strategy_profile_error
            else ""
        )
        if not messagebox.askyesno(
            "Сброс профиля",
            f"Сбросить {normalized} к безопасным значениям по умолчанию?"
            f"{reset_note}",
            parent=self,
        ):
            return
        try:
            self.strategy_profile_store.reset_profile(
                normalized,
                force_recreate_document=bool(self.strategy_profile_error),
            )
            config = BotConfig(dry_run=(normalized == "DRY_RUN"))
            connect_timeout, read_timeout = self._read_client_settings()
            profile = bot_config_to_profile(
                config,
                connect_timeout_seconds=connect_timeout,
                read_timeout_seconds=read_timeout,
            )
            saved = self.strategy_profile_store.save_profile(
                normalized,
                profile,
                select=True,
            )
            self._apply_strategy_profile_to_ui(profile)
            self._strategy_profile_dirty = False
            self._loaded_profile_mode = normalized
            self.strategy_profile_error = None
            self.sb_profile_hash.set(
                f"hash: {str(saved['config_hash'])[:16]}"
            )
            self.sb_profile_status.set(f"{normalized}: сброшен")
            self._journal_config_event(
                "CONFIG_RESET",
                mode=normalized,
                config_hash=str(saved["config_hash"]),
                payload={"source": "BotConfig defaults"},
                severity="WARNING",
            )
        except (ValueError, StrategyProfileError) as exc:
            messagebox.showerror("Ошибка сброса", str(exc), parent=self)

    def _on_strategy_profile_selected(self, _event: tk.Event | None = None) -> None:
        requested = self.sb_profile_mode.get().strip().upper()
        previous = getattr(self, "_loaded_profile_mode", None)
        if self._strategy_profile_dirty and previous and requested != previous:
            discard = messagebox.askyesno(
                "Несохранённые изменения",
                f"Отбросить изменения профиля {previous} и загрузить {requested}?",
                parent=self,
            )
            if not discard:
                self._strategy_profile_loading = True
                try:
                    self.sb_profile_mode.set(previous)
                finally:
                    self._strategy_profile_loading = False
                return
        self._load_strategy_profile(requested, show_message=False)

    def _prepare_strategy_profile_for_mode(self, mode: str) -> bool:
        normalized = mode.strip().upper()
        if normalized not in PROFILE_MODES:
            messagebox.showerror("Ошибка профиля", normalized, parent=self)
            return False
        loaded_mode = getattr(self, "_loaded_profile_mode", None)
        if loaded_mode != normalized:
            if self._strategy_profile_dirty and loaded_mode:
                save = messagebox.askyesno(
                    "Сохранить профиль",
                    f"Сохранить изменения {loaded_mode} перед переходом к {normalized}?",
                    parent=self,
                )
                if not save or not self._save_selected_strategy_profile():
                    return False
            self.sb_profile_mode.set(normalized)
            if not self._load_strategy_profile(normalized, show_message=False):
                messagebox.showerror(
                    "Профиль режима отсутствует",
                    f"Сначала сохраните профиль {normalized}.",
                    parent=self,
                )
                return False
        if self._strategy_profile_dirty:
            if not messagebox.askyesno(
                "Применение конфигурации",
                f"Сохранить изменения и применить профиль {normalized}?",
                parent=self,
            ):
                return False
            if not self._save_selected_strategy_profile():
                return False
        try:
            stored = self.strategy_profile_store.load_profile(normalized)
            if stored is None:
                raise StrategyProfileError(f"Профиль {normalized} отсутствует.")
            current = self._capture_strategy_profile(normalized)
            current_hash = canonical_profile_hash(current)
            if current_hash != stored["config_hash"]:
                self._journal_config_event(
                    "CONFIG_MISMATCH",
                    mode=normalized,
                    severity="ERROR",
                    config_hash=current_hash,
                    payload={
                        "stored_hash": stored["config_hash"],
                        "current_hash": current_hash,
                    },
                )
                raise StrategyProfileError(
                    "GUI и сохранённый профиль расходятся. Повторно загрузите профиль."
                )
            self._active_strategy_profile_mode = normalized
            self._active_strategy_profile_hash = current_hash
            self.sb_dashboard_vars["profile"].set(normalized)
            self.sb_dashboard_vars["config_hash"].set(current_hash[:12])
            self._journal_config_event(
                "CONFIG_APPLIED",
                mode=normalized,
                config_hash=current_hash,
                payload={
                    "primary_strategy": current.get("primary_strategy"),
                    "shadow_strategies": current.get("shadow_strategies", []),
                    "candle_interval": current.get("candle_interval"),
                },
            )
            return True
        except (StrategyProfileError, ValueError) as exc:
            self.strategy_profile_error = str(exc)
            messagebox.showerror("Конфигурация заблокирована", str(exc), parent=self)
            return False

    def _get_token(self) -> str:
        token = self.sb_token.get().strip()
        if not token:
            raise ValueError("Введите Sandbox API-токен или загрузите его из .env.")
        return token

    def _get_ca_bundle(self) -> str | None:
        raw = self.sb_ca_bundle.get().strip()
        if not raw:
            return None
        path = Path(os.path.expandvars(raw)).expanduser()
        if not path.is_file():
            raise ValueError(
                f"CA bundle не найден: {path}. Выберите существующий PEM-файл "
                "или очистите поле."
            )
        return str(path.resolve())

    def _read_client_settings(self) -> tuple[float, float]:
        try:
            connect_timeout = float(self.sb_connect_timeout.get().replace(",", "."))
            read_timeout = float(self.sb_read_timeout.get().replace(",", "."))
        except ValueError as exc:
            raise ValueError("Connect/read timeout должны быть числами.") from exc
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("Connect/read timeout должны быть положительными.")
        return connect_timeout, read_timeout

    def _make_tbank_client(
        self,
        token: str,
        ca_bundle: str | None,
    ) -> TBankSandboxClient:
        connect_timeout, read_timeout = self._read_client_settings()
        return TBankSandboxClient(
            token,
            ca_bundle_path=ca_bundle,
            connect_timeout_seconds=connect_timeout,
            read_timeout_seconds=read_timeout,
        )

    def _browse_ca_bundle(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="Выберите CA bundle / корневой сертификат в PEM",
            filetypes=[
                ("PEM certificates", "*.pem *.crt *.cer"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            self.sb_ca_bundle.set(selected)

    @staticmethod
    def _open_certificate_page() -> None:
        webbrowser.open("https://www.gosuslugi.ru/crt")

    def _check_sandbox_connection(self) -> None:
        try:
            token = self._get_token()
            ca_bundle = self._get_ca_bundle()
        except ValueError as exc:
            messagebox.showerror("Ошибка подключения", str(exc), parent=self)
            return
        if not self._begin_sandbox_operation("проверка подключения"):
            return
        self.sb_status.set("Проверяю подключение…")

        def work() -> list[dict[str, Any]]:
            with self._make_tbank_client(token, ca_bundle) as api:
                return api.get_accounts()

        def done(accounts: list[dict[str, Any]]) -> None:
            self._set_account_records(accounts)
            self.sb_status.set(f"Подключено: найдено счетов — {len(accounts)}")
            self._show_sandbox_result({"connection": "ok", "accounts": accounts})
            messagebox.showinfo(
                "Подключение успешно",
                f"API-токен принят. Открытых виртуальных счетов: {len(accounts)}.",
                parent=self,
            )

        self._run_background(
            work,
            done,
            on_finally=self._end_sandbox_operation,
        )

    def _load_sandbox_accounts(self) -> None:
        try:
            token = self._get_token()
            ca_bundle = self._get_ca_bundle()
        except ValueError as exc:
            messagebox.showerror("Ошибка подключения", str(exc), parent=self)
            return
        if not self._begin_sandbox_operation("обновление списка счетов"):
            return
        self.sb_status.set("Обновляю список счетов…")

        def work() -> list[dict[str, Any]]:
            with self._make_tbank_client(token, ca_bundle) as api:
                return api.get_accounts()

        def done(accounts: list[dict[str, Any]]) -> None:
            self._set_account_records(accounts)
            self.sb_status.set(f"Список обновлён: {len(accounts)} счетов")

        self._run_background(
            work,
            done,
            on_finally=self._end_sandbox_operation,
        )

    def _set_account_records(self, accounts: list[dict[str, Any]]) -> None:
        self.account_records.clear()
        labels: list[str] = []
        for account in accounts:
            account_id = str(account.get("id", ""))
            name = str(account.get("name") or "Sandbox")
            label = f"{name} — {account_id}"
            self.account_records[label] = account
            labels.append(label)
        self.account_combo["values"] = labels
        if hasattr(self, "diag_account_combo"):
            self.diag_account_combo["values"] = labels
        requested = self.sb_account.get().strip()
        selected = ""
        if requested:
            for label, account in self.account_records.items():
                if str(account.get("id")) == requested or label == requested:
                    selected = label
                    break
        if not selected and labels:
            selected = labels[0]
        self.sb_account.set(selected)
        self._set_account_id_display()
        if selected and hasattr(self, "risk_limits_tree"):
            self._refresh_risk_dashboard()

    def _create_sandbox_account(self) -> None:
        try:
            token = self._get_token()
            ca_bundle = self._get_ca_bundle()
            amount = float(self.sb_initial_rub.get().replace(" ", "").replace(",", "."))
            if amount <= 0:
                raise ValueError("Сумма пополнения должна быть положительной.")
            if amount > 30_000_000:
                raise ValueError("Сумма пополнения Sandbox не должна превышать 30 млн ₽.")
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self)
            return
        if not self._begin_sandbox_operation("создание Sandbox-счёта"):
            return
        self.sb_status.set("Создаю виртуальный счёт…")

        def work() -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
            with self._make_tbank_client(token, ca_bundle) as api:
                account_id = api.open_account("MOEX Research Robot")
                payment = api.pay_in(account_id, amount)
                accounts = api.get_accounts()
                return account_id, payment, accounts

        def done(result: tuple[str, dict[str, Any], list[dict[str, Any]]]) -> None:
            account_id, payment, accounts = result
            self._set_account_records(accounts)
            for label, account in self.account_records.items():
                if str(account.get("id")) == account_id:
                    self.sb_account.set(label)
                    self._set_account_id_display(account_id)
                    break
            self.sb_status.set(f"Создан Sandbox-счёт {account_id}")
            self._show_sandbox_result(
                {"account_id": account_id, "pay_in": payment}
            )
            messagebox.showinfo(
                "Счёт создан",
                f"Виртуальный счёт создан и пополнен на {amount:,.0f} ₽.".replace(",", " "),
                parent=self,
            )

        self._run_background(
            work,
            done,
            on_finally=self._end_sandbox_operation,
        )

    def _read_bot_config(self, dry_run: bool) -> BotConfig:
        def as_float(variable: tk.StringVar) -> float:
            return float(variable.get().strip().replace(",", "."))

        try:
            primary = self._selected_primary_strategy()
            shadows = tuple(
                strategy
                for strategy, variable in self.sb_shadow_vars.items()
                if variable.get() and strategy != primary
            )
            fast = int(self.sb_fast.get())
            slow = int(self.sb_slow.get())
            sma_hysteresis = as_float(self.sb_sma_hysteresis) / 100.0
            donchian_entry = int(self.sb_donchian_entry.get())
            donchian_exit = int(self.sb_donchian_exit.get())
            donchian_atr = int(self.sb_donchian_atr.get())
            donchian_stop = as_float(self.sb_donchian_stop)
            ensemble_fast = int(self.sb_ensemble_fast.get())
            ensemble_slow = int(self.sb_ensemble_slow.get())
            ensemble_momentum = int(self.sb_ensemble_momentum.get())
            ensemble_breakout = int(self.sb_ensemble_breakout.get())
            ensemble_votes = int(self.sb_ensemble_votes.get())
            target_raw = self.sb_target_volatility.get().strip()
            target_volatility = (
                None
                if not target_raw
                else float(target_raw.replace(",", ".")) / 100.0
            )
            volatility_window = int(self.sb_volatility_window.get())
            max_strategy_weight = as_float(self.sb_max_strategy_weight) / 100.0
            lookback = int(self.sb_lookback.get())
            poll = int(self.sb_poll.get())
            max_lots = int(self.sb_max_lots.get())
            failure_threshold = int(self.sb_failure_threshold.get())
            circuit_seconds = int(self.sb_circuit_seconds.get())
            circuit_max_seconds = int(self.sb_circuit_max_seconds.get())
            max_signal_age = int(self.sb_max_signal_age.get())
            reconcile_attempts = int(self.sb_reconcile_attempts.get())
            reconcile_delay = as_float(self.sb_reconcile_delay)
            portfolio_reconcile_interval = int(
                self.sb_portfolio_reconcile_interval.get()
            )
        except ValueError as exc:
            raise ValueError(
                "Параметры стратегий, истории, лотов и устойчивости должны "
                "быть корректными числами."
            ) from exc

        ticker = self.sb_ticker.get().strip().upper()
        class_code = self.sb_class_code.get().strip().upper()
        if not ticker or not class_code:
            raise ValueError("Тикер и class code не должны быть пустыми.")

        env_values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
        try:
            heartbeat_log_seconds = int(
                str(env_values.get("ROBOT_HEARTBEAT_LOG_SECONDS") or "1800")
            )
            slow_cycle_seconds = float(
                str(env_values.get("ROBOT_SLOW_CYCLE_SECONDS") or "10")
                .replace(",", ".")
            )
            market_idle_enabled = str(
                env_values.get("ROBOT_MARKET_IDLE_ENABLED") or "YES"
            ).strip().upper() in {"1", "TRUE", "YES", "ON"}
            market_status_check_seconds = int(
                str(env_values.get("ROBOT_MARKET_STATUS_CHECK_SECONDS") or "300")
            )
            market_idle_poll_seconds = int(
                str(env_values.get("ROBOT_MARKET_IDLE_POLL_SECONDS") or "300")
            )
            market_idle_reconcile_seconds = int(
                str(
                    env_values.get("ROBOT_MARKET_IDLE_RECONCILE_SECONDS")
                    or "1800"
                )
            )
            market_idle_heartbeat_seconds = int(
                str(
                    env_values.get("ROBOT_MARKET_IDLE_HEARTBEAT_SECONDS")
                    or "1800"
                )
            )
        except ValueError as exc:
            raise ValueError(
                "Параметры heartbeat, MARKET_IDLE и slow cycle в .env "
                "должны быть корректными числами."
            ) from exc

        try:
            return BotConfig(
                ticker=ticker,
                class_code=class_code,
                candle_interval=self.sb_interval.get().strip(),
                primary_strategy=primary,
                shadow_strategies=shadows,
                fast_window=fast,
                slow_window=slow,
                sma_hysteresis_percent=sma_hysteresis,
                donchian_entry_window=donchian_entry,
                donchian_exit_window=donchian_exit,
                donchian_atr_window=donchian_atr,
                donchian_trailing_stop_atr=donchian_stop,
                ensemble_sma_fast=ensemble_fast,
                ensemble_sma_slow=ensemble_slow,
                ensemble_momentum_window=ensemble_momentum,
                ensemble_breakout_window=ensemble_breakout,
                ensemble_vote_threshold=ensemble_votes,
                annual_target_volatility=target_volatility,
                volatility_window=volatility_window,
                max_strategy_weight=max_strategy_weight,
                lookback_days=lookback,
                poll_seconds=poll,
                max_order_lots=max_lots,
                dry_run=dry_run,
                state_file=str(ROBOT_STATE_PATH),
                journal_file=str(EVENT_DB_PATH),
                failure_threshold=failure_threshold,
                circuit_open_seconds=circuit_seconds,
                circuit_max_open_seconds=circuit_max_seconds,
                max_signal_age_seconds=max_signal_age,
                reconcile_attempts=reconcile_attempts,
                reconcile_delay_seconds=reconcile_delay,
                portfolio_reconcile_interval_seconds=portfolio_reconcile_interval,
                market_idle_enabled=market_idle_enabled,
                market_status_check_seconds=market_status_check_seconds,
                market_idle_poll_seconds=market_idle_poll_seconds,
                market_idle_reconcile_seconds=market_idle_reconcile_seconds,
                market_idle_heartbeat_seconds=market_idle_heartbeat_seconds,
                heartbeat_log_seconds=heartbeat_log_seconds,
                slow_cycle_seconds=slow_cycle_seconds,
            )
        except ValueError as exc:
            raise ValueError(f"Некорректная конфигурация стратегии: {exc}") from exc

    def _selected_account_id(self, optional: bool = False) -> str:
        value = self.sb_account.get().strip()
        if value in self.account_records:
            account_id = str(self.account_records[value].get("id", ""))
        else:
            account_id = value
        if not account_id and not optional:
            raise ValueError("Выберите Sandbox-счёт или сначала создайте его.")
        return account_id

    def _confirm_execution(self) -> bool:
        if not self.sb_arm_checkbox.get() or self.sb_confirm_text.get().strip().upper() != "SANDBOX":
            messagebox.showwarning(
                "Исполнение заблокировано",
                "Для тестовых заявок установите флажок и введите слово SANDBOX.",
                parent=self,
            )
            return False
        try:
            configured_max_lots = int(self.sb_max_lots.get())
        except (TypeError, ValueError):
            configured_max_lots = 0
        if configured_max_lots != 1:
            messagebox.showwarning(
                "Beta1.1: лимит Sandbox",
                "В v3.7-beta1 Sandbox Execution разрешён только при "
                "максимуме 1 лот.",
                parent=self,
            )
            return False
        try:
            risk_profile = RiskProfileStore(RISK_PROFILE_PATH).require_profile(
                "SANDBOX_EXECUTION"
            )
        except RiskPersistenceError as exc:
            messagebox.showerror(
                "Risk Engine заблокировал запуск",
                "Сохраните и проверьте профиль SANDBOX_EXECUTION в "
                "risk_profiles.json.\n\n" + str(exc),
                parent=self,
            )
            return False
        self._active_risk_policy_hash = str(risk_profile["policy_hash"])
        preflight_text = "недоступен"
        try:
            account_id = self._selected_account_id()
            risk_snapshot = load_risk_dashboard_snapshot(
                profile_store=RiskProfileStore(RISK_PROFILE_PATH),
                state_store=RiskStateStore(RISK_STATE_PATH),
                journal=self.event_journal,
                account_id=account_id,
                mode="SANDBOX_EXECUTION",
            )
            self._apply_risk_dashboard_snapshot(risk_snapshot)
            risk_summary = risk_snapshot.summary
            if risk_summary.get("round_trip_ready"):
                preflight_text = "ГОТОВ: есть ресурс минимум на BUY→SELL"
            else:
                blockers = risk_summary.get("round_trip_blockers") or []
                preflight_text = "ВНИМАНИЕ: " + "; ".join(
                    str(item) for item in blockers
                )
        except (ValueError, RiskReportError):
            # Core RiskRuntime remains authoritative. Dashboard preflight is
            # an operator aid and must not replace fail-closed evaluation.
            preflight_text = "нет достаточных локальных данных"
        primary = self._selected_primary_strategy()
        shadows = [
            strategy
            for strategy, variable in self.sb_shadow_vars.items()
            if variable.get() and strategy != primary
        ]
        profile_hash = (self._active_strategy_profile_hash or "").strip()
        return messagebox.askyesno(
            "Подтверждение Sandbox",
            "Разрешить отправку ВИРТУАЛЬНЫХ заявок в T-Invest Sandbox?\n\n"
            f"Профиль: {self.sb_profile_mode.get()}\n"
            f"Profile hash: {profile_hash[:16] or '—'}\n"
            f"Инструмент: {self.sb_ticker.get().strip().upper()} / "
            f"{self.sb_class_code.get().strip().upper()}\n"
            f"Интервал: {self.sb_interval.get()}\n"
            f"PRIMARY: {primary}\n"
            f"SHADOW: {', '.join(shadows) or '—'}\n"
            f"Максимум лотов: {self.sb_max_lots.get()}\n"
            f"Risk policy hash: {self._active_risk_policy_hash[:16]}\n"
            f"BUY→SELL preflight: {preflight_text}\n\n"
            "Risk Engine beta1 применяется до INTENT_SAVED и может "
            "разрешить, уменьшить или заблокировать цель PRIMARY.\n\n"
            "Реальный счёт не используется.",
            icon="warning",
            parent=self,
        )

    def _run_sandbox_once(self, execute: bool) -> None:
        profile_mode = "SANDBOX_EXECUTION" if execute else "DRY_RUN"
        if not self._prepare_strategy_profile_for_mode(profile_mode):
            return
        if execute and not self._confirm_execution():
            return
        try:
            token = self._get_token()
            ca_bundle = self._get_ca_bundle()
            account_id = self._selected_account_id()
            config = self._read_bot_config(dry_run=not execute)
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc), parent=self)
            return

        operation_name = (
            "одиночная тестовая заявка" if execute else "одиночный dry-run"
        )
        if not self._begin_sandbox_operation(operation_name):
            return
        self._set_sb_config_locked(True)

        self.sb_status.set("Выполняю один цикл робота…")
        self.logger.info(
            "Single Sandbox cycle: execute=%s ticker=%s interval=%s primary=%s",
            execute, config.ticker, config.candle_interval, config.primary_strategy,
        )
        self.logger.debug("Single-cycle configuration: %s", asdict(config))

        def work() -> dict[str, Any]:
            with self._make_tbank_client(token, ca_bundle) as api:
                risk_runtime = RiskRuntimeAdapter.from_directory(
                    RUNTIME_DIR,
                    account_id=account_id,
                    mode=(
                        "SANDBOX_EXECUTION" if execute else "DRY_RUN"
                    ),
                    auto_create_dry_run_profile=not execute,
                )
                robot = SandboxTradingBot(
                    api,
                    account_id,
                    config,
                    allow_execution=execute,
                    risk_runtime=risk_runtime,
                    require_risk_runtime_for_execution=execute,
                )
                try:
                    return robot.run_once()
                finally:
                    robot.end_session("single_cycle_completed")

        def done(result: dict[str, Any]) -> None:
            self._show_sandbox_result(result)
            action = result.get("action", "HOLD")
            status = result.get("status", "processed")
            executed_lots = int(result.get("executed_lots", 0) or 0)
            self.sb_status.set(
                f"Цикл: {status}; действие {action}; исполнено лотов: {executed_lots}"
            )

        def finished() -> None:
            self._set_sb_config_locked(False)
            self._end_sandbox_operation()

        self._run_background(
            work,
            done,
            on_finally=finished,
        )

    def _start_robot_loop(self, execute: bool) -> None:
        if self.robot_thread and self.robot_thread.is_alive():
            messagebox.showinfo("Робот уже работает", "Сначала остановите текущий цикл.", parent=self)
            return
        if self.sandbox_task_active:
            messagebox.showinfo(
                "Операция уже выполняется",
                "Дождитесь завершения операции: "
                f"{self.sandbox_task_name or 'Sandbox-запрос'}.",
                parent=self,
            )
            return
        profile_mode = "SANDBOX_EXECUTION" if execute else "DRY_RUN"
        if not self._prepare_strategy_profile_for_mode(profile_mode):
            return
        if execute and not self._confirm_execution():
            return
        try:
            token = self._get_token()
            ca_bundle = self._get_ca_bundle()
            account_id = self._selected_account_id()
            config = self._read_bot_config(dry_run=not execute)
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc), parent=self)
            return

        self._set_sb_config_locked(True)
        self.stop_event.clear()
        mode = "SANDBOX EXECUTION" if execute else "DRY-RUN"
        self.sb_status.set(f"Робот запущен: {mode}")
        self.logger.warning(
            "Robot loop started: mode=%s ticker=%s interval=%s primary=%s shadows=%s",
            mode, config.ticker, config.candle_interval,
            config.primary_strategy, ",".join(config.shadow_strategies) or "—",
        )
        self.logger.debug("Robot loop configuration: %s", asdict(config))

        def loop() -> None:
            robot: SandboxTradingBot | None = None
            try:
                with self._make_tbank_client(token, ca_bundle) as api:
                    risk_runtime = RiskRuntimeAdapter.from_directory(
                        RUNTIME_DIR,
                        account_id=account_id,
                        mode=(
                            "SANDBOX_EXECUTION" if execute else "DRY_RUN"
                        ),
                        auto_create_dry_run_profile=not execute,
                    )
                    robot = SandboxTradingBot(
                        api,
                        account_id,
                        config,
                        allow_execution=execute,
                        risk_runtime=risk_runtime,
                        require_risk_runtime_for_execution=execute,
                    )
                    while not self.stop_event.is_set():
                        wait_seconds = config.poll_seconds
                        try:
                            result = robot.run_once()
                            wait_seconds = int(
                                result.get(
                                    "recommended_wait_seconds",
                                    config.poll_seconds,
                                )
                                or config.poll_seconds
                            )
                            self.ui_queue.put(("sandbox_result", result))
                            if result.get("api_state") == "DEGRADED":
                                api_note = "; API временно недоступен"
                            elif result.get("status") == "state_save_failed":
                                api_note = "; локальное состояние не сохранено"
                            else:
                                api_note = ""
                            if result.get("status") == "market_idle":
                                status_text = (
                                    "Робот работает: "
                                    f"{mode}; MARKET_IDLE — торговля недоступна; "
                                    "стратегический расчёт приостановлен; "
                                    "следующая проверка примерно через "
                                    f"{wait_seconds} с"
                                )
                            elif result.get("market_idle_transition") == "EXITED":
                                status_text = (
                                    "Робот работает: "
                                    f"{mode}; рынок снова доступен; "
                                    f"статус — {result.get('status', 'processed')}; "
                                    f"действие — {result.get('action', 'HOLD')}"
                                    f"{api_note}"
                                )
                            else:
                                status_text = (
                                    "Робот работает: "
                                    f"{mode}; статус — {result.get('status', 'processed')}; "
                                    f"действие — {result.get('action', 'HOLD')}"
                                    f"{api_note}"
                                )
                            self.ui_queue.put(
                                (
                                    "sandbox_status",
                                    status_text,
                                )
                            )
                        except Exception as exc:
                            self.logger.exception("Robot iteration failed")
                            self.ui_queue.put(("sandbox_status", f"Ошибка цикла: {exc}"))
                            wait_seconds = min(config.poll_seconds, 30)
                        self.stop_event.wait(max(5, wait_seconds))
            finally:
                if robot is not None:
                    robot.end_session("operator_stop" if self.stop_event.is_set() else "loop_finished")
                self.ui_queue.put(("sandbox_status", "Робот остановлен"))
                self.ui_queue.put(("sandbox_config_unlock", None))
                self.logger.warning("Robot loop stopped")

        self.robot_thread = threading.Thread(target=loop, daemon=True, name="sandbox-robot")
        self.robot_thread.start()

    def _stop_robot(self) -> None:
        if not self.robot_thread or not self.robot_thread.is_alive():
            self.sb_status.set("Робот не запущен")
            return
        self.stop_event.set()
        self.sb_status.set("Останавливаю робота после текущего запроса…")
        self.logger.info("Stop requested")

    def _show_sandbox_result(self, result: Any) -> None:
        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        self.sandbox_result_text.configure(state="normal")
        self.sandbox_result_text.delete("1.0", "end")
        self.sandbox_result_text.insert("1.0", text)
        self.sandbox_result_text.configure(state="disabled")
        if isinstance(result, dict):
            self._update_sandbox_dashboard(result)
            self._update_strategy_tree(result)
        self._refresh_events()
        self._refresh_risk_dashboard()

    def _update_strategy_tree(self, result: dict[str, Any]) -> None:
        if not hasattr(self, "strategy_tree"):
            return
        decisions = (
            result.get("strategy_decisions")
            or result.get("last_known_strategy_decisions")
            or result.get("last_strategy_decisions")
        )
        if not isinstance(decisions, dict):
            return
        self.strategy_tree.delete(*self.strategy_tree.get_children())
        primary = str(result.get("primary_strategy") or "sma")
        ordered = sorted(
            decisions.items(),
            key=lambda item: (item[0] != primary, str(item[0])),
        )
        for strategy, raw in ordered:
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or ("PRIMARY" if strategy == primary else "SHADOW"))
            weight = raw.get("target_weight")
            try:
                weight_text = f"{float(weight):.1%}"
            except (TypeError, ValueError):
                weight_text = "—"
            self.strategy_tree.insert(
                "",
                "end",
                values=(
                    role,
                    STRATEGY_TITLES_RU.get(strategy, str(strategy)),
                    raw.get("signal", "—"),
                    weight_text,
                    raw.get("target_lots", "—"),
                    raw.get("reason", ""),
                ),
            )

    def _update_sandbox_dashboard(self, result: dict[str, Any]) -> None:
        api_state = str(result.get("api_state", "")).upper()
        if result.get("connection") == "ok":
            api_text = "Доступен"
        elif api_state == "AVAILABLE":
            api_text = "Доступен"
        elif api_state == "DEGRADED":
            api_text = "Временно недоступен"
        elif result.get("status") == "circuit_open":
            api_text = "Circuit breaker открыт"
        elif result.get("status") == "state_locked":
            api_text = "Занят другим процессом"
        elif result.get("status") == "state_save_failed":
            api_text = "Ошибка локального состояния"
        else:
            api_text = self.sb_dashboard_vars["api"].get()
        self.sb_dashboard_vars["api"].set(api_text)

        market_state = str(result.get("market_state") or "").upper()
        if market_state == "IDLE" or result.get("status") == "market_idle":
            reason = str(result.get("market_idle_reason") or "UNAVAILABLE")
            wait_seconds = result.get("recommended_wait_seconds")
            suffix = f"; проверка через {wait_seconds} с" if wait_seconds else ""
            self.sb_dashboard_vars["market"].set(
                f"MARKET_IDLE ({reason}){suffix}"
            )
        elif market_state == "OPEN":
            transition = result.get("market_idle_transition")
            self.sb_dashboard_vars["market"].set(
                "Открыт; выход из MARKET_IDLE"
                if transition == "EXITED"
                else "Открыт"
            )
        elif market_state == "UNKNOWN":
            self.sb_dashboard_vars["market"].set("Статус не подтверждён")

        last_check = result.get("last_check_time") or result.get("checked_at")
        if last_check:
            self.sb_dashboard_vars["last_check"].set(str(last_check))

        candle = (
            result.get("candle_time")
            or result.get("last_seen_candle")
            or result.get("last_consumed_candle")
        )
        if candle:
            self.sb_dashboard_vars["last_candle"].set(str(candle))

        age = result.get("data_age_seconds")
        if age is not None:
            try:
                seconds = max(0, int(float(age)))
                if seconds < 120:
                    age_text = f"{seconds} с"
                elif seconds < 7200:
                    age_text = f"{seconds / 60:.1f} мин"
                else:
                    age_text = f"{seconds / 3600:.1f} ч"
                self.sb_dashboard_vars["data_age"].set(age_text)
            except (TypeError, ValueError):
                self.sb_dashboard_vars["data_age"].set(str(age))

        if result.get("mode"):
            self.sb_dashboard_vars["mode"].set(str(result["mode"]))
            self.sb_dashboard_vars["profile"].set(str(result["mode"]))
        elif self._active_strategy_profile_mode:
            self.sb_dashboard_vars["profile"].set(
                self._active_strategy_profile_mode
            )
        applied_hash = (
            self._active_strategy_profile_hash
            or result.get("strategy_profile_hash")
        )
        if applied_hash:
            self.sb_dashboard_vars["config_hash"].set(str(applied_hash)[:12])
        primary = result.get("primary_strategy")
        if primary:
            label = STRATEGY_TITLES_RU.get(str(primary), str(primary))
            config_hash = str(result.get("primary_config_hash") or "")[:8]
            self.sb_dashboard_vars["primary"].set(
                f"{label} [{config_hash}]" if config_hash else label
            )
        risk_status = result.get("risk_status")
        if risk_status:
            risk_hash = str(result.get("risk_policy_hash") or "")[:8]
            risk_text = str(risk_status)
            if risk_hash:
                risk_text += f" [{risk_hash}]"
            if risk_status == "NOT_ENFORCED" and result.get("mode") == "SANDBOX_EXECUTION":
                risk_text = "ОШИБКА: Risk Engine не применён"
            self.sb_dashboard_vars["risk"].set(risk_text)
        if result.get("reason"):
            self.sb_dashboard_vars["reason"].set(str(result["reason"]))
        if "signal" in result:
            signal = result.get("signal")
            action = result.get("action")
            self.sb_dashboard_vars["signal"].set(
                f"{signal} / {action or '—'}"
            )
        if "current_lots" in result or "actual_lots_after" in result:
            current = result.get("actual_lots_after")
            if current is None:
                current = result.get("current_lots", "—")
            target = result.get("target_lots")
            position_text = f"{current} лот(ов)"
            if target is not None:
                position_text += f" → цель {target}"
            self.sb_dashboard_vars["position"].set(position_text)

        pending = result.get("pending_order")
        if pending:
            state = pending.get("lifecycle_state", "неизвестно")
            order_id = str(pending.get("order_id", ""))[:8]
            self.sb_dashboard_vars["pending"].set(
                f"да: {state} {order_id}".strip()
            )
        else:
            self.sb_dashboard_vars["pending"].set("нет")

        if "consecutive_failures" in result:
            self.sb_dashboard_vars["failures"].set(
                str(result.get("consecutive_failures", 0))
            )
        next_retry = result.get("next_retry_at")
        if next_retry:
            self.sb_dashboard_vars["next_retry"].set(str(next_retry))
        elif result.get("api_state") == "AVAILABLE":
            self.sb_dashboard_vars["next_retry"].set("—")

    def _begin_sandbox_operation(self, name: str) -> bool:
        """Prevent overlapping one-shot API operations from the GUI.

        Several simultaneous clicks used to start independent request threads.
        Apart from confusing the result panel, that could create a burst of
        connections and make transient timeouts much more likely.
        """
        if self.robot_thread and self.robot_thread.is_alive():
            messagebox.showinfo(
                "Sandbox-робот уже работает",
                "Сначала остановите непрерывный цикл робота.",
                parent=self,
            )
            return False
        if self.sandbox_task_active:
            messagebox.showinfo(
                "Операция уже выполняется",
                "Дождитесь завершения операции: "
                f"{self.sandbox_task_name or 'Sandbox-запрос'}.",
                parent=self,
            )
            return False
        self.sandbox_task_active = True
        self.sandbox_task_name = name
        return True

    def _end_sandbox_operation(self) -> None:
        self.sandbox_task_active = False
        self.sandbox_task_name = ""

    # ------------------------------------------------------------------
    # Virtual portfolio and ownership recovery
    # ------------------------------------------------------------------
    def _build_portfolio_tab(self) -> None:
        self.portfolio_tab.columnconfigure(0, weight=1)
        self.portfolio_tab.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self.portfolio_tab, padding=(12, 10, 12, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            toolbar,
            text="Обновить портфель",
            command=lambda: self._refresh_portfolio(manual=True),
        ).pack(side="left")
        ttk.Checkbutton(
            toolbar,
            text="Автообновление",
            variable=self.portfolio_auto_refresh,
            command=self._toggle_portfolio_auto_refresh,
        ).pack(side="left", padx=(12, 4))
        ttk.Label(toolbar, text="период, секунд:").pack(side="left")
        ttk.Entry(
            toolbar,
            textvariable=self.portfolio_refresh_seconds,
            width=7,
        ).pack(side="left", padx=(4, 12))
        ttk.Label(toolbar, textvariable=self.portfolio_status).pack(
            side="left", fill="x", expand=True
        )

        summary = ttk.Frame(self.portfolio_tab, padding=(12, 0, 12, 6))
        summary.grid(row=1, column=0, sticky="ew")
        summary_items = [
            ("Стоимость", "total"),
            ("Кэш RUB", "cash"),
            ("Ценные бумаги", "securities"),
            ("P&L", "yield"),
            ("Reconciliation", "reconciliation"),
            ("Проверено", "checked_at"),
        ]
        for index, (title, key) in enumerate(summary_items):
            card = ttk.LabelFrame(summary, text=title, padding=(8, 5))
            card.grid(row=0, column=index, sticky="ew", padx=3)
            summary.columnconfigure(index, weight=1)
            ttk.Label(
                card,
                textvariable=self.portfolio_summary_vars[key],
                style="MetricValue.TLabel",
            ).pack()

        content = ttk.Panedwindow(self.portfolio_tab, orient="vertical")
        content.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))

        positions_box = ttk.LabelFrame(
            content,
            text="Позиции, цели и ownership",
            padding=6,
        )
        content.add(positions_box, weight=4)
        positions_box.rowconfigure(0, weight=1)
        positions_box.columnconfigure(0, weight=1)
        columns = (
            "ticker",
            "asset_type",
            "origin",
            "lots",
            "target",
            "avg",
            "current",
            "value",
            "pnl",
            "owner",
            "interval",
            "reconciliation",
            "pending",
        )
        self.portfolio_tree = ttk.Treeview(
            positions_box,
            columns=columns,
            show="headings",
            height=12,
        )
        headings = {
            "ticker": "Тикер",
            "asset_type": "Тип",
            "origin": "Источник",
            "lots": "Лоты",
            "target": "Цель",
            "avg": "Средняя",
            "current": "Текущая",
            "value": "Стоимость",
            "pnl": "P&L",
            "owner": "Владелец",
            "interval": "Интервал",
            "reconciliation": "Сверка",
            "pending": "Pending",
        }
        widths = {
            "ticker": 75,
            "asset_type": 90,
            "origin": 95,
            "lots": 65,
            "target": 65,
            "avg": 90,
            "current": 90,
            "value": 100,
            "pnl": 90,
            "owner": 145,
            "interval": 120,
            "reconciliation": 105,
            "pending": 80,
        }
        for column in columns:
            self.portfolio_tree.heading(column, text=headings[column])
            self.portfolio_tree.column(
                column,
                width=widths[column],
                anchor="center",
                stretch=column in {"owner", "interval", "origin"},
            )
        yscroll = ttk.Scrollbar(
            positions_box,
            orient="vertical",
            command=self.portfolio_tree.yview,
        )
        xscroll = ttk.Scrollbar(
            positions_box,
            orient="horizontal",
            command=self.portfolio_tree.xview,
        )
        self.portfolio_tree.configure(
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set,
        )
        self.portfolio_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.portfolio_tree.tag_configure("warning", background="#fff4d6")
        self.portfolio_tree.tag_configure("error", background="#ffd9d9")
        self._portfolio_position_rows: dict[str, dict[str, Any]] = {}

        actions = ttk.Frame(content, padding=(0, 8, 0, 0))
        content.add(actions, weight=2)
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        actions.rowconfigure(2, weight=1)

        recovery_box = ttk.LabelFrame(
            actions,
            text="Восстановление ownership",
            padding=10,
        )
        recovery_box.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        ttk.Label(
            recovery_box,
            text=(
                "Допускается только при совпадении счёта, инструмента, "
                "таймфрейма, config hash, текущих лотов и сохранённого решения "
                "PRIMARY. Выберите строку UNATTRIBUTED."
            ),
            wraplength=520,
            justify="left",
        ).pack(anchor="w")
        ttk.Checkbutton(
            recovery_box,
            text="Я проверил происхождение позиции",
            variable=self.portfolio_recovery_arm,
        ).pack(anchor="w", pady=(7, 2))
        ttk.Label(
            recovery_box,
            text="Введите ADOPT <ТИКЕР> <ЛОТЫ>:",
        ).pack(anchor="w")
        ttk.Entry(
            recovery_box,
            textvariable=self.portfolio_recovery_confirm,
        ).pack(fill="x", pady=(2, 5))
        ttk.Button(
            recovery_box,
            text="Восстановить ownership",
            command=self._recover_portfolio_ownership,
            style="Danger.TButton",
        ).pack(fill="x")

        close_box = ttk.LabelFrame(
            actions,
            text="Безопасное закрытие непривязанной позиции",
            padding=10,
        )
        close_box.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        ttk.Label(
            close_box,
            text=(
                "Используйте, если доказательств для восстановления ownership "
                "нет. Будет продано точное текущее количество лотов выбранной "
                "позиции с последующей сверкой."
            ),
            wraplength=520,
            justify="left",
        ).pack(anchor="w")
        ttk.Checkbutton(
            close_box,
            text="Разрешить виртуальную продажу",
            variable=self.portfolio_close_arm,
        ).pack(anchor="w", pady=(7, 2))
        ttk.Label(close_box, text="Введите CLOSE <ТИКЕР> <ЛОТЫ>:").pack(
            anchor="w"
        )
        ttk.Entry(
            close_box,
            textvariable=self.portfolio_close_confirm,
        ).pack(fill="x", pady=(2, 5))
        ttk.Button(
            close_box,
            text="Закрыть непривязанную позицию",
            command=self._close_unattributed_position,
            style="Danger.TButton",
        ).pack(fill="x")

        acknowledgement_box = ttk.LabelFrame(
            actions,
            text="Подтверждение доказанного внешнего закрытия",
            padding=10,
        )
        acknowledgement_box.grid(
            row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0)
        )
        ttk.Label(
            acknowledgement_box,
            text=(
                "Используйте только для TARGET_MISMATCH, когда свежий broker "
                "snapshot подтверждает actual=0, локальная цель остаётся ненулевой, "
                "pending-order отсутствует, а последнее исполнение полностью "
                "reconciled и risk-accounted. Команда не отправляет заявку и не "
                "меняет RiskState counters."
            ),
            wraplength=1120,
            justify="left",
        ).pack(anchor="w")
        ttk.Checkbutton(
            acknowledgement_box,
            text="Я подтверждаю, что позиция закрыта вне Strategy Engine",
            variable=self.portfolio_ack_arm,
        ).pack(anchor="w", pady=(7, 2))
        ttk.Label(
            acknowledgement_box,
            text="Введите ACK EXTERNAL CLOSE <ТИКЕР> 0:",
        ).pack(anchor="w")
        ack_row = ttk.Frame(acknowledgement_box)
        ack_row.pack(fill="x", pady=(2, 0))
        ttk.Entry(
            ack_row,
            textvariable=self.portfolio_ack_confirm,
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            ack_row,
            text="Признать внешнее закрытие и очистить stale target",
            command=self._acknowledge_external_close,
            style="Danger.TButton",
        ).pack(side="left", padx=(8, 0))

        self.portfolio_details_text = tk.Text(
            actions,
            height=6,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
        )
        self.portfolio_details_text.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="nsew",
            pady=(8, 0),
        )

    def _toggle_portfolio_auto_refresh(self) -> None:
        if self._portfolio_auto_after_id:
            self.after_cancel(self._portfolio_auto_after_id)
            self._portfolio_auto_after_id = None
        if self.portfolio_auto_refresh.get():
            self._refresh_portfolio(manual=False)

    def _schedule_portfolio_auto_refresh(
        self,
        *,
        delay_seconds: int | None = None,
    ) -> None:
        if not self.portfolio_auto_refresh.get():
            return
        if delay_seconds is None:
            try:
                seconds = max(30, int(self.portfolio_refresh_seconds.get()))
            except ValueError:
                seconds = 60
                self.portfolio_refresh_seconds.set("60")
        else:
            seconds = max(1, int(delay_seconds))
        self._portfolio_auto_after_id = self.after(
            seconds * 1000,
            lambda: self._refresh_portfolio(manual=False),
        )

    def _refresh_portfolio(self, *, manual: bool) -> None:
        if self.portfolio_task_active:
            return
        try:
            token = self._get_token()
            ca_bundle = self._get_ca_bundle()
            account_id = self._selected_account_id()
        except ValueError as exc:
            if manual:
                messagebox.showerror("Портфель", str(exc), parent=self)
            self._schedule_portfolio_auto_refresh()
            return
        self.portfolio_task_active = True
        self.portfolio_status.set("Обновляю виртуальный портфель…")

        def work() -> dict[str, Any]:
            with self._make_tbank_client(token, ca_bundle) as api:
                manager = CanonicalPortfolioManager(
                    api,
                    account_id,
                    robot_state_file=ROBOT_STATE_PATH,
                    portfolio_state_file=PORTFOLIO_STATE_PATH,
                    journal_file=EVENT_DB_PATH,
                )
                state = manager.refresh()
                return PortfolioSnapshotBuilder(state).to_dict()

        refresh_delay: dict[str, int | None] = {"seconds": None}

        def done(snapshot: dict[str, Any]) -> None:
            self._portfolio_refresh_backoff.reset()
            self._display_portfolio_snapshot(snapshot)

        def failed(exc: BaseException) -> None:
            info = describe_background_error(exc)
            if info.transient:
                delay = self._portfolio_refresh_backoff.record_failure(
                    info.retry_after_seconds
                )
                refresh_delay["seconds"] = delay
                stale_note = ""
                if self.last_portfolio_snapshot is not None:
                    checked_at = self.last_portfolio_snapshot.get("checked_at") or "—"
                    stale_note = f" Последний снимок сохранён ({checked_at})."
                request_note = (
                    f" Request ID: {info.request_id}." if info.request_id else ""
                )
                self.portfolio_status.set(
                    f"{info.summary}. Автоповтор через {delay} с."
                    f"{stale_note}{request_note}"
                )
                self.sb_status.set(
                    "T-Invest Sandbox временно недоступен; "
                    "торговый цикл работает fail-closed"
                )
                return
            self.portfolio_status.set(info.summary)

        def finished() -> None:
            self.portfolio_task_active = False
            self._schedule_portfolio_auto_refresh(
                delay_seconds=refresh_delay["seconds"]
            )

        self._run_background(
            work,
            done,
            on_finally=finished,
            on_error=failed,
            show_modal_error=manual,
            error_context="portfolio_snapshot",
        )

    @staticmethod
    def _format_money(value: Any) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):,.2f} ₽".replace(",", " ")
        except (TypeError, ValueError):
            return str(value)

    def _display_portfolio_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.last_portfolio_snapshot = snapshot
        self.portfolio_summary_vars["total"].set(
            self._format_money(snapshot.get("total_value"))
        )
        self.portfolio_summary_vars["cash"].set(
            self._format_money(snapshot.get("cash_rub"))
        )
        self.portfolio_summary_vars["securities"].set(
            self._format_money(snapshot.get("securities_value"))
        )
        self.portfolio_summary_vars["yield"].set(
            self._format_money(snapshot.get("expected_yield"))
        )
        unattributed = int(snapshot.get("unattributed_positions", 0) or 0)
        mismatches = int(snapshot.get("reconciliation_mismatches", 0) or 0)
        revision = snapshot.get("revision")
        revision_text = f"rev={revision}; " if revision is not None else ""
        self.portfolio_summary_vars["reconciliation"].set(
            f"{revision_text}unattributed={unattributed}; mismatch={mismatches}"
        )
        self.portfolio_summary_vars["checked_at"].set(
            str(snapshot.get("checked_at") or "—")
        )

        for item in self.portfolio_tree.get_children():
            self.portfolio_tree.delete(item)
        self._portfolio_position_rows.clear()
        for index, row in enumerate(snapshot.get("positions", [])):
            iid = f"position-{index}"
            self._portfolio_position_rows[iid] = dict(row)
            pending_count = len(row.get("local_pending_order_ids") or []) + len(
                row.get("broker_pending_order_ids") or []
            )
            owner = row.get("owner_strategy") or row.get("ownership_status") or "—"
            tag = ""
            reconciliation_status = str(row.get("reconciliation_status") or "")
            if row.get("ownership_status") == "UNATTRIBUTED" or reconciliation_status in {
                "UNATTRIBUTED_OPEN_POSITION",
                "OWNERSHIP_MISSING",
                "EXTERNAL_ACTIVITY_DETECTED",
                "ACCOUNT_MISMATCH",
                "MANUAL_REVIEW_REQUIRED",
            }:
                tag = "error"
            elif reconciliation_status not in {"", "MATCHED"}:
                tag = "warning"
            self.portfolio_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row.get("ticker") or row.get("instrument_id") or "—",
                    row.get("asset_type") or "—",
                    row.get("origin") or "UNKNOWN",
                    row.get("quantity_lots") if row.get("quantity_lots") is not None else "—",
                    row.get("target_lots") if row.get("target_lots") is not None else "—",
                    self._format_money(row.get("average_price")),
                    self._format_money(row.get("current_price")),
                    self._format_money(row.get("market_value")),
                    self._format_money(row.get("expected_yield")),
                    owner,
                    row.get("owner_interval") or "—",
                    row.get("reconciliation_status") or "UNKNOWN",
                    pending_count,
                ),
                tags=((tag,) if tag else ()),
            )
        warnings = snapshot.get("warnings") or []
        shadow_status = str(
            snapshot.get("compatibility_shadow_status") or "DISABLED"
        ).upper()
        if shadow_status == "NOT_CONFIGURED":
            shadow_status = "DISABLED"
        details = {
            "schema_version": snapshot.get("schema_version"),
            "canonical": snapshot.get("canonical"),
            "revision": snapshot.get("revision"),
            "decision_checksum": snapshot.get("decision_checksum"),
            "account_id": snapshot.get("account_id"),
            "freshness": snapshot.get("freshness"),
            "state_status": snapshot.get("state_status"),
            "blocking": snapshot.get("blocking"),
            "compatibility_shadow_status": shadow_status,
            "warnings": warnings,
            "reconciliation_results": snapshot.get("reconciliation_results"),
            "local_pending_orders": snapshot.get("local_pending_orders"),
            "broker_orders": snapshot.get("broker_orders"),
        }
        self.portfolio_details_text.configure(state="normal")
        self.portfolio_details_text.delete("1.0", "end")
        self.portfolio_details_text.insert(
            "1.0",
            json.dumps(details, ensure_ascii=False, indent=2, default=str),
        )
        self.portfolio_details_text.configure(state="disabled")
        canonical_status = (
            "Портфель обновлён"
            if not warnings
            else "Портфель обновлён; требуется внимание"
        )
        self.portfolio_status.set(f"{canonical_status}; shadow={shadow_status}")
        self._refresh_risk_dashboard()
        self._refresh_events()

    def _selected_portfolio_row(self) -> dict[str, Any]:
        selected = self.portfolio_tree.selection()
        if not selected:
            raise ValueError("Выберите позицию в таблице портфеля.")
        row = self._portfolio_position_rows.get(selected[0])
        if not row:
            raise ValueError("Не удалось прочитать выбранную позицию.")
        return row

    def _recover_portfolio_ownership(self) -> None:
        if not self.portfolio_recovery_arm.get():
            messagebox.showwarning(
                "Ownership recovery",
                "Установите флажок подтверждения.",
                parent=self,
            )
            return
        if not self._prepare_strategy_profile_for_mode("SANDBOX_EXECUTION"):
            return
        try:
            row = self._selected_portfolio_row()
            if str(row.get("asset_type") or "").strip().lower() == "currency":
                raise ValueError(
                    "Денежный остаток не является торговой позицией и не может "
                    "получать ownership или закрываться заявкой."
                )
            if row.get("ownership_status") != "UNATTRIBUTED":
                raise ValueError("Выбранная позиция не имеет статуса UNATTRIBUTED.")
            lots = int(row.get("quantity_lots") or 0)
            if lots < 1:
                raise ValueError("У позиции нет положительного количества лотов.")
            token = self._get_token()
            ca_bundle = self._get_ca_bundle()
            account_id = self._selected_account_id()
            config = self._read_bot_config(dry_run=False)
            suite = StrategySuiteConfig(
                primary_strategy=config.primary_strategy,
                shadow_strategies=config.shadow_strategies,
                sma_fast_window=config.fast_window,
                sma_slow_window=config.slow_window,
                sma_hysteresis_percent=config.sma_hysteresis_percent,
                donchian_entry_window=config.donchian_entry_window,
                donchian_exit_window=config.donchian_exit_window,
                donchian_atr_window=config.donchian_atr_window,
                donchian_trailing_stop_atr=config.donchian_trailing_stop_atr,
                ensemble_sma_fast=config.ensemble_sma_fast,
                ensemble_sma_slow=config.ensemble_sma_slow,
                ensemble_momentum_window=config.ensemble_momentum_window,
                ensemble_breakout_window=config.ensemble_breakout_window,
                ensemble_vote_threshold=config.ensemble_vote_threshold,
                annual_target_volatility=config.annual_target_volatility,
                volatility_window=config.volatility_window,
                max_weight=config.max_strategy_weight,
                position_limit_lots=config.max_order_lots,
            )
            selected_ticker = str(row.get("ticker") or config.ticker).upper()
            expected_phrase = f"ADOPT {selected_ticker} {lots}"
            entered_phrase = self.portfolio_recovery_confirm.get().strip().upper()
            if entered_phrase != expected_phrase:
                raise ValueError(
                    f"Подтверждение не совпадает с выбранной строкой. "
                    f"Введите точно: {expected_phrase}"
                )
            request = OwnershipRecoveryRequest(
                account_id=account_id,
                ticker=selected_ticker,
                class_code=str(row.get("class_code") or config.class_code),
                candle_interval=config.candle_interval,
                primary_strategy=config.primary_strategy,
                primary_config_hash=suite.config_hash(config.primary_strategy),
                strategy_suite_hash=suite.suite_hash(),
                shadow_strategies=config.shadow_strategies,
                expected_lots=lots,
                confirmation_text=self.portfolio_recovery_confirm.get(),
            )
        except ValueError as exc:
            messagebox.showerror("Ownership recovery", str(exc), parent=self)
            return
        if not messagebox.askyesno(
            "Последнее подтверждение",
            "Восстановить владельца существующей виртуальной позиции?\n"
            "Операция изменит локальное состояние, но не отправит заявку.",
            icon="warning",
            parent=self,
        ):
            return
        if not self._begin_sandbox_operation("ownership recovery"):
            return

        def work() -> dict[str, Any]:
            with self._make_tbank_client(token, ca_bundle) as api:
                manager = CanonicalPortfolioManager(
                    api,
                    account_id,
                    robot_state_file=ROBOT_STATE_PATH,
                    portfolio_state_file=PORTFOLIO_STATE_PATH,
                    journal_file=EVENT_DB_PATH,
                )
                return manager.recover_ownership(request)

        def done(result: dict[str, Any]) -> None:
            messagebox.showinfo(
                "Ownership восстановлен",
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                parent=self,
            )
            self.portfolio_recovery_arm.set(False)
            self.portfolio_recovery_confirm.set("")
            self.after(200, lambda: self._refresh_portfolio(manual=False))

        self._run_background(
            work,
            done,
            on_finally=self._end_sandbox_operation,
        )

    def _close_unattributed_position(self) -> None:
        if not self.portfolio_close_arm.get():
            messagebox.showwarning(
                "Закрытие позиции",
                "Установите флажок разрешения виртуальной продажи.",
                parent=self,
            )
            return
        try:
            row = self._selected_portfolio_row()
            if str(row.get("asset_type") or "").strip().lower() == "currency":
                raise ValueError(
                    "Денежный остаток не является торговой позицией и не может "
                    "получать ownership или закрываться заявкой."
                )
            if row.get("ownership_status") != "UNATTRIBUTED":
                raise ValueError("Выбранная позиция не имеет статуса UNATTRIBUTED.")
            lots = int(row.get("quantity_lots") or 0)
            if lots < 1:
                raise ValueError("У позиции нет положительного количества лотов.")
            token = self._get_token()
            ca_bundle = self._get_ca_bundle()
            account_id = self._selected_account_id()
            ticker = str(row.get("ticker") or "").upper()
            class_code = str(row.get("class_code") or "TQBR").upper()
            confirmation = self.portfolio_close_confirm.get()
            expected_phrase = f"CLOSE {ticker} {lots}"
            if confirmation.strip().upper() != expected_phrase:
                raise ValueError(
                    f"Подтверждение не совпадает с выбранной строкой. "
                    f"Введите точно: {expected_phrase}"
                )
        except ValueError as exc:
            messagebox.showerror("Закрытие позиции", str(exc), parent=self)
            return
        if not messagebox.askyesno(
            "Подтверждение продажи",
            f"Продать {lots} лот(а) {ticker} в Sandbox и сверить позицию?",
            icon="warning",
            parent=self,
        ):
            return
        if not self._begin_sandbox_operation("close unattributed position"):
            return

        def work() -> dict[str, Any]:
            with self._make_tbank_client(token, ca_bundle) as api:
                diagnostic = SandboxOrderDiagnostics(
                    api,
                    account_id,
                    DiagnosticConfig(
                        ticker=ticker,
                        class_code=class_code,
                        lots=1,
                        state_file=str(DIAGNOSTIC_STATE_PATH),
                        journal_file=str(EVENT_DB_PATH),
                        risk_profile_file=str(RISK_PROFILE_PATH),
                        risk_state_file=str(RISK_STATE_PATH),
                        reconcile_delay_seconds=0.5,
                    ),
                )
                return diagnostic.close_unattributed_position(
                    expected_lots=lots,
                    confirmation_text=confirmation,
                )

        def done(result: dict[str, Any]) -> None:
            self._show_diagnostic_result(result)
            if result.get("position_reconciled"):
                messagebox.showinfo(
                    "Позиция закрыта",
                    f"Фактическая позиция после: {result.get('actual_lots_after')}",
                    parent=self,
                )
            else:
                messagebox.showwarning(
                    "Требуется проверка",
                    "Поручение не завершило reconciliation. Проверьте pending-order.",
                    parent=self,
                )
            self.portfolio_close_arm.set(False)
            self.portfolio_close_confirm.set("")
            self.after(200, lambda: self._refresh_portfolio(manual=False))

        self._run_background(
            work,
            done,
            on_finally=self._end_sandbox_operation,
        )

    def _acknowledge_external_close(self) -> None:
        if not self.portfolio_ack_arm.get():
            messagebox.showwarning(
                "External close acknowledgement",
                "Установите флажок подтверждения.",
                parent=self,
            )
            return
        try:
            row = self._selected_portfolio_row()
            if str(row.get("asset_type") or "").strip().lower() == "currency":
                raise ValueError("Денежный остаток не имеет strategy target.")
            status = str(row.get("reconciliation_status") or "").upper()
            if status != "TARGET_MISMATCH":
                raise ValueError(
                    "Команда разрешена только для TARGET_MISMATCH."
                )
            actual_lots = int(row.get("quantity_lots") or 0)
            if actual_lots != 0:
                raise ValueError(
                    "Свежий canonical snapshot должен показывать actual_lots = 0."
                )
            target_raw = row.get("target_lots")
            if target_raw is None:
                raise ValueError("У выбранной строки нет stale target для очистки.")
            target_lots = int(target_raw)
            if target_lots == 0:
                raise ValueError("Локальный target уже равен нулю.")
            ticker = str(row.get("ticker") or "").strip().upper()
            class_code = str(row.get("class_code") or "TQBR").strip().upper()
            if not ticker:
                raise ValueError("Не удалось определить тикер выбранной позиции.")
            expected_phrase = f"ACK EXTERNAL CLOSE {ticker} 0"
            confirmation = self.portfolio_ack_confirm.get().strip().upper()
            if confirmation != expected_phrase:
                raise ValueError(
                    f"Введите точную фразу подтверждения: {expected_phrase}"
                )
            token = self._get_token()
            ca_bundle = self._get_ca_bundle()
            account_id = self._selected_account_id()
            request = ExternalCloseAcknowledgementRequest(
                account_id=account_id,
                ticker=ticker,
                class_code=class_code,
                expected_target_lots=target_lots,
                confirmation_text=confirmation,
            )
        except (TypeError, ValueError) as exc:
            messagebox.showerror(
                "External close acknowledgement",
                str(exc),
                parent=self,
            )
            return
        if not messagebox.askyesno(
            "Последнее подтверждение",
            f"Подтвердить внешнее закрытие {ticker}: actual=0, stale target={target_lots}?\n\n"
            "Новая заявка не будет отправлена. Локальная цель и ownership будут "
            "очищены с записью в EventJournal.",
            icon="warning",
            parent=self,
        ):
            return
        if not self._begin_sandbox_operation("external close acknowledgement"):
            return

        def work() -> dict[str, Any]:
            with self._make_tbank_client(token, ca_bundle) as api:
                manager = CanonicalPortfolioManager(
                    api,
                    account_id,
                    robot_state_file=ROBOT_STATE_PATH,
                    portfolio_state_file=PORTFOLIO_STATE_PATH,
                    journal_file=EVENT_DB_PATH,
                )
                return manager.acknowledge_external_close(request)

        def done(result: dict[str, Any]) -> None:
            messagebox.showinfo(
                "Внешнее закрытие подтверждено",
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                parent=self,
            )
            self.portfolio_ack_arm.set(False)
            self.portfolio_ack_confirm.set("")
            self.after(200, lambda: self._refresh_portfolio(manual=False))

        self._run_background(
            work,
            done,
            on_finally=self._end_sandbox_operation,
        )

    # ------------------------------------------------------------------
    # Risk Dashboard and burn-in reporting
    # ------------------------------------------------------------------
    def _build_risk_dashboard_tab(self) -> None:
        self.risk_tab.columnconfigure(0, weight=1)
        self.risk_tab.rowconfigure(5, weight=1)

        toolbar = ttk.Frame(self.risk_tab, padding=(12, 10, 12, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Label(toolbar, text="Профиль:").pack(side="left")
        mode_combo = ttk.Combobox(
            toolbar,
            textvariable=self.risk_dashboard_mode,
            values=["SANDBOX_EXECUTION", "DRY_RUN"],
            state="readonly",
            width=20,
        )
        mode_combo.pack(side="left", padx=(4, 8))
        mode_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh_risk_dashboard(),
            add="+",
        )
        ttk.Button(
            toolbar,
            text="Обновить",
            command=self._refresh_risk_dashboard,
        ).pack(side="left")
        ttk.Button(
            toolbar,
            text="Экспорт snapshot JSON",
            command=self._export_risk_dashboard_snapshot,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(toolbar, text="Burn-in, часов:").pack(side="left", padx=(18, 4))
        ttk.Entry(
            toolbar,
            textvariable=self.risk_dashboard_hours,
            width=7,
        ).pack(side="left")
        ttk.Button(
            toolbar,
            text="Сформировать burn-in отчёт",
            command=self._export_risk_burn_in_report,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            toolbar,
            text="Открыть risk-события",
            command=self._open_risk_events,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            toolbar,
            text="Настройка runtime",
            command=self._rerun_runtime_setup,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            toolbar,
            textvariable=self.risk_dashboard_status,
        ).pack(side="right")

        identity_and_gate = ttk.Frame(self.risk_tab, padding=(12, 0, 12, 6))
        identity_and_gate.grid(row=1, column=0, sticky="ew")
        identity_and_gate.columnconfigure(0, weight=2)
        identity_and_gate.columnconfigure(1, weight=5)

        account_box = ttk.LabelFrame(
            identity_and_gate,
            text="Полный Sandbox Account ID",
            padding=8,
        )
        account_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        account_row = ttk.Frame(account_box)
        account_row.pack(fill="x")
        self.risk_account_id_entry = ttk.Entry(
            account_row,
            textvariable=self.risk_dashboard_account_id,
            state="readonly",
            font=("Consolas", 9),
        )
        self.risk_account_id_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(
            account_row,
            text="Копировать полный ID",
            command=lambda: self._copy_text(
                self.risk_dashboard_account_id.get(),
                status="Полный Account ID скопирован",
            ),
        ).pack(side="left", padx=(6, 0))
        self._hover_tooltips.append(
            HoverTooltip(
                self.risk_account_id_entry,
                lambda: self.risk_dashboard_account_id.get(),
            )
        )

        self.risk_kill_switch_banner = tk.Frame(
            identity_and_gate,
            background="#eeeeee",
            borderwidth=1,
            relief="solid",
            padx=10,
            pady=7,
        )
        self.risk_kill_switch_banner.grid(row=0, column=1, sticky="nsew")
        self.risk_kill_switch_banner.columnconfigure(0, weight=1)
        self.risk_kill_switch_title_label = tk.Label(
            self.risk_kill_switch_banner,
            textvariable=self.risk_kill_switch_title,
            background="#eeeeee",
            foreground="#333333",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        )
        self.risk_kill_switch_title_label.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            self.risk_kill_switch_banner,
            text="Копировать диагностику",
            command=self._copy_kill_switch_diagnostics,
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))
        self.risk_kill_switch_detail_label = tk.Label(
            self.risk_kill_switch_banner,
            textvariable=self.risk_kill_switch_detail,
            background="#eeeeee",
            foreground="#333333",
            justify="left",
            anchor="w",
            wraplength=770,
        )
        self.risk_kill_switch_detail_label.grid(row=1, column=0, sticky="ew", pady=(3, 0))
        self._apply_kill_switch_banner(None)

        summary = ttk.Frame(self.risk_tab, padding=(12, 0, 12, 6))
        summary.grid(row=2, column=0, sticky="ew")
        summary_items = [
            ("Risk Engine", "engine"),
            ("Счёт / режим", "account"),
            ("Policy", "policy"),
            ("Последнее решение", "decision"),
            ("Заявки", "orders"),
            ("Оборот", "turnover"),
            ("Дневной P&L", "daily_pnl"),
            ("Просадка", "drawdown"),
            ("Кэш / резерв", "cash"),
            ("Возраст снимка", "snapshot"),
            ("BUY→SELL preflight", "round_trip"),
            ("Persistent gate", "persistent_gate"),
        ]
        for index, (title, key) in enumerate(summary_items):
            row, column = divmod(index, 4)
            card = ttk.LabelFrame(summary, text=title, padding=(8, 5))
            card.grid(row=row, column=column, sticky="nsew", padx=3, pady=3)
            summary.columnconfigure(column, weight=1)
            ttk.Label(
                card,
                textvariable=self.risk_dashboard_summary_vars[key],
                wraplength=270,
                justify="left",
            ).pack(anchor="w")

        risk_editor = ttk.LabelFrame(
            self.risk_tab,
            text="Редактор дневного лимита заявок (Sandbox)",
            padding=8,
        )
        risk_editor.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 6))
        ttk.Label(risk_editor, text="Профиль лимита:").grid(
            row=0, column=0, sticky="w"
        )
        self.risk_order_limit_preset_combo = ttk.Combobox(
            risk_editor,
            textvariable=self.risk_order_limit_preset,
            values=[
                "Stable default (4)",
                "Sandbox burn-in (32)",
                "Custom",
            ],
            state="readonly",
            width=24,
        )
        self.risk_order_limit_preset_combo.grid(
            row=0, column=1, sticky="w", padx=(6, 12)
        )
        self.risk_order_limit_preset_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._sync_risk_profile_editor(),
            add="+",
        )
        ttk.Label(risk_editor, text="Custom, 1…100:").grid(
            row=0, column=2, sticky="w"
        )
        self.risk_order_limit_custom_entry = ttk.Entry(
            risk_editor,
            textvariable=self.risk_order_limit_custom,
            width=8,
        )
        self.risk_order_limit_custom_entry.grid(
            row=0, column=3, sticky="w", padx=(6, 12)
        )
        self.risk_order_limit_apply_button = ttk.Button(
            risk_editor,
            text="Применить лимит",
            command=self._apply_risk_order_limit,
        )
        self.risk_order_limit_apply_button.grid(row=0, column=4, sticky="w")
        ttk.Label(
            risk_editor,
            textvariable=self.risk_order_limit_status,
            wraplength=520,
            justify="left",
        ).grid(row=0, column=5, sticky="ew", padx=(12, 0))
        risk_editor.columnconfigure(5, weight=1)
        ttk.Label(
            risk_editor,
            text=(
                "Изменение доступно только при остановленных Dry-run/Sandbox, "
                "без pending/uncertain order и без блокирующего reconciliation. "
                "Дневной счётчик, оборот и история исполнений не сбрасываются."
            ),
            wraplength=1250,
            justify="left",
        ).grid(row=1, column=0, columnspan=6, sticky="w", pady=(5, 0))

        gates = ttk.LabelFrame(
            self.risk_tab,
            text="Инженерные правила beta1.1",
            padding=8,
        )
        gates.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 6))
        ttk.Label(
            gates,
            text=(
                "Dashboard читает только локальные checksummed risk_profiles.json, "
                "risk_state.json и SQLite-журнал; он не отправляет запросы брокеру. "
                "Preflight полного цикла заранее проверяет минимум две свободные "
                "заявки и достаточный дневной оборот. Явный kill switch и risk resync "
                "имеют приоритет над последним PASS-решением."
            ),
            wraplength=1260,
            justify="left",
        ).pack(anchor="w")

        content = ttk.Panedwindow(self.risk_tab, orient="vertical")
        content.grid(row=5, column=0, sticky="nsew", padx=12, pady=(0, 12))

        limits_box = ttk.LabelFrame(content, text="Использование лимитов", padding=6)
        content.add(limits_box, weight=3)
        limits_box.rowconfigure(0, weight=1)
        limits_box.columnconfigure(0, weight=1)
        columns = (
            "status", "limit", "current", "remaining", "utilization", "note"
        )
        self.risk_limits_tree = ttk.Treeview(
            limits_box,
            columns=columns,
            show="tree headings",
            height=11,
        )
        self.risk_limits_tree.heading("#0", text="Лимит")
        self.risk_limits_tree.column("#0", width=270, stretch=True)
        headings = {
            "status": "Статус",
            "limit": "Лимит",
            "current": "Текущее",
            "remaining": "Остаток",
            "utilization": "Использовано",
            "note": "Комментарий",
        }
        widths = {
            "status": 90,
            "limit": 105,
            "current": 105,
            "remaining": 105,
            "utilization": 95,
            "note": 410,
        }
        for column in columns:
            self.risk_limits_tree.heading(column, text=headings[column])
            self.risk_limits_tree.column(
                column,
                width=widths[column],
                stretch=(column == "note"),
                anchor="w" if column == "note" else "center",
            )
        self.risk_limits_tree.tag_configure("BLOCKED", background="#ffd9d9")
        self.risk_limits_tree.tag_configure("WARN", background="#fff1cc")
        self.risk_limits_tree.tag_configure("UNKNOWN", background="#eeeeee")
        limit_y = ttk.Scrollbar(
            limits_box,
            orient="vertical",
            command=self.risk_limits_tree.yview,
        )
        limit_x = ttk.Scrollbar(
            limits_box,
            orient="horizontal",
            command=self.risk_limits_tree.xview,
        )
        self.risk_limits_tree.configure(
            yscrollcommand=limit_y.set,
            xscrollcommand=limit_x.set,
        )
        self.risk_limits_tree.grid(row=0, column=0, sticky="nsew")
        limit_y.grid(row=0, column=1, sticky="ns")
        limit_x.grid(row=1, column=0, sticky="ew")

        events_box = ttk.LabelFrame(content, text="Последние risk-события", padding=6)
        content.add(events_box, weight=2)
        events_box.rowconfigure(0, weight=1)
        events_box.columnconfigure(0, weight=1)
        risk_columns = ("time", "event", "severity", "status", "action", "summary")
        self.risk_events_tree = ttk.Treeview(
            events_box,
            columns=risk_columns,
            show="headings",
            height=8,
            selectmode="browse",
        )
        risk_headings = {
            "time": "UTC",
            "event": "Событие",
            "severity": "Уровень",
            "status": "Статус",
            "action": "Действие",
            "summary": "Кратко",
        }
        risk_widths = {
            "time": 170,
            "event": 230,
            "severity": 75,
            "status": 110,
            "action": 80,
            "summary": 520,
        }
        for column in risk_columns:
            self.risk_events_tree.heading(column, text=risk_headings[column])
            self.risk_events_tree.column(
                column,
                width=risk_widths[column],
                stretch=(column == "summary"),
            )
        self.risk_events_tree.tag_configure("ERROR", background="#ffd9d9")
        self.risk_events_tree.tag_configure("WARNING", background="#fff1cc")
        risk_y = ttk.Scrollbar(
            events_box,
            orient="vertical",
            command=self.risk_events_tree.yview,
        )
        self.risk_events_tree.configure(yscrollcommand=risk_y.set)
        self.risk_events_tree.grid(row=0, column=0, sticky="nsew")
        risk_y.grid(row=0, column=1, sticky="ns")
        self.risk_events_tree.bind(
            "<<TreeviewSelect>>",
            self._show_selected_risk_event,
        )

        detail_box = ttk.LabelFrame(content, text="Пояснения и полная запись", padding=6)
        content.add(detail_box, weight=2)
        detail_box.rowconfigure(0, weight=1)
        detail_box.columnconfigure(0, weight=1)
        self.risk_dashboard_detail_text = tk.Text(
            detail_box,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
            height=8,
        )
        detail_y = ttk.Scrollbar(
            detail_box,
            orient="vertical",
            command=self.risk_dashboard_detail_text.yview,
        )
        self.risk_dashboard_detail_text.configure(yscrollcommand=detail_y.set)
        self.risk_dashboard_detail_text.grid(row=0, column=0, sticky="nsew")
        detail_y.grid(row=0, column=1, sticky="ns")

    def _selected_risk_order_limit(self) -> int:
        preset = self.risk_order_limit_preset.get().strip()
        if preset == "Stable default (4)":
            return 4
        if preset == "Sandbox burn-in (32)":
            return 32
        return RiskProfileEditor.validate_daily_order_limit(
            self.risk_order_limit_custom.get()
        )

    def _risk_profile_edit_context(self) -> RiskProfileEditContext:
        account_id = self._selected_account_id(optional=True) or ""
        portfolio = self.last_portfolio_snapshot or {}
        orders = list(portfolio.get("local_pending_orders") or []) + list(
            portfolio.get("broker_orders") or []
        )
        uncertain_tokens = {
            "UNKNOWN",
            "UNCERTAIN",
            "ORDER_SUBMITTED",
            "SUBMIT_UNKNOWN",
            "PENDING_ORDER_UNCERTAIN",
        }
        uncertain = False
        for order in orders:
            if not isinstance(order, dict):
                continue
            status_text = " ".join(
                str(order.get(key) or "").upper()
                for key in (
                    "status",
                    "lifecycle_state",
                    "execution_report_status",
                )
            )
            if any(token in status_text for token in uncertain_tokens):
                uncertain = True
                break
        return RiskProfileEditContext(
            account_id=account_id,
            mode=self.risk_dashboard_mode.get(),
            execution_active=bool(
                self.robot_thread and self.robot_thread.is_alive()
            ),
            operation_active=bool(self.sandbox_task_active),
            pending_order=bool(orders),
            uncertain_order=uncertain,
            reconciliation_blocking=bool(portfolio.get("blocking", False)),
            maintenance_active=bool(self.portfolio_task_active),
        )

    def _sync_risk_profile_editor(
        self,
        snapshot: RiskDashboardSnapshot | None = None,
    ) -> None:
        if not hasattr(self, "risk_order_limit_apply_button"):
            return
        selected = self.risk_order_limit_preset.get().strip()
        if selected == "Stable default (4)":
            self.risk_order_limit_custom.set("4")
        elif selected == "Sandbox burn-in (32)":
            self.risk_order_limit_custom.set("32")
        custom_state = "normal" if selected == "Custom" else "disabled"
        self.risk_order_limit_custom_entry.configure(state=custom_state)

        current_snapshot = snapshot or self.last_risk_dashboard_snapshot
        current_count: int | None = None
        current_limit: int | None = None
        if current_snapshot is not None:
            summary = current_snapshot.summary
            try:
                current_count = int(summary.get("daily_order_count") or 0)
            except (TypeError, ValueError):
                current_count = None
            try:
                raw_limit = summary.get("max_orders_per_day")
                current_limit = int(raw_limit) if raw_limit is not None else None
            except (TypeError, ValueError):
                current_limit = None
        context = self._risk_profile_edit_context()
        reasons = context.block_reasons()
        if reasons:
            self.risk_order_limit_preset_combo.configure(state="disabled")
            self.risk_order_limit_custom_entry.configure(state="disabled")
            self.risk_order_limit_apply_button.configure(state="disabled")
            prefix = (
                f"Текущее: {current_count}/{current_limit}. "
                if current_count is not None and current_limit is not None
                else ""
            )
            self.risk_order_limit_status.set(
                prefix + "Редактирование заблокировано: " + " ".join(reasons)
            )
            return
        self.risk_order_limit_preset_combo.configure(state="readonly")
        self.risk_order_limit_custom_entry.configure(state=custom_state)
        self.risk_order_limit_apply_button.configure(state="normal")
        if current_count is not None and current_limit is not None:
            state = "HALTED" if current_count >= current_limit else "ACTIVE"
            self.risk_order_limit_status.set(
                f"Текущее: {current_count}/{current_limit}; {state}."
            )
        else:
            self.risk_order_limit_status.set("Лимит ещё не загружен.")

    def _apply_risk_order_limit(self) -> None:
        try:
            context = self._risk_profile_edit_context()
            new_limit = self._selected_risk_order_limit()
            reasons = context.block_reasons()
            if reasons:
                raise RiskProfileEditError(" ".join(reasons))
            profile_store = RiskProfileStore(RISK_PROFILE_PATH)
            loaded = profile_store.require_profile(context.normalized_mode)
            old_limit = loaded["policy"].max_orders_per_day
            current_count = RiskStateStore(RISK_STATE_PATH).load_account(
                context.account_id
            ).daily_order_count
        except (RiskProfileEditError, RiskPersistenceError, ValueError) as exc:
            messagebox.showerror("Risk Profile", str(exc), parent=self)
            self._sync_risk_profile_editor()
            return
        if not messagebox.askyesno(
            "Изменение Risk Profile",
            "Применить новый дневной лимит заявок?\n\n"
            f"Счёт: {context.account_id}\n"
            f"Режим: {context.normalized_mode}\n"
            f"Текущее использование: {current_count}/{old_limit}\n"
            f"Новый лимит: {new_limit}\n\n"
            "Счётчик, оборот и история исполнений не будут сброшены.",
            icon="warning",
            parent=self,
        ):
            return
        editor = RiskProfileEditor(
            profile_store=profile_store,
            state_store=RiskStateStore(RISK_STATE_PATH),
            journal=self.event_journal,
        )
        try:
            result = editor.apply_max_orders_per_day(context, new_limit)
        except (RiskProfileEditError, RiskPersistenceError, OSError) as exc:
            messagebox.showerror("Risk Profile", str(exc), parent=self)
            self._sync_risk_profile_editor()
            return
        status = (
            "HALTED: лимит не превышает уже использованный счётчик."
            if result.halted_by_limit
            else "Новый лимит применяется со следующей Risk evaluation."
        )
        self.risk_order_limit_status.set(
            f"{result.current_count}/{result.new_limit}. {status}"
        )
        result_payload = result.to_dict()
        messagebox.showinfo(
            "Risk Profile обновлён",
            json.dumps(result_payload, ensure_ascii=False, indent=2),
            parent=self,
        )
        self._refresh_risk_dashboard()
        self._refresh_events()

    @staticmethod
    def _format_risk_value(value: Any, unit: str = "") -> str:
        if value is None:
            return "—"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if unit == "%":
            return f"{number * 100:.2f}%"
        if unit == "₽":
            return f"{number:,.2f} ₽".replace(",", " ")
        if unit in {"лоты", "шт."}:
            return f"{int(round(number))}"
        if unit == "с":
            return f"{number:.1f} с"
        return f"{number:.3f}".rstrip("0").rstrip(".")

    def _refresh_risk_dashboard(self) -> None:
        if not hasattr(self, "risk_limits_tree"):
            return
        try:
            account_id = self._selected_account_id()
            self.risk_dashboard_account_id.set(full_account_id(account_id) or "—")
            snapshot = load_risk_dashboard_snapshot(
                profile_store=RiskProfileStore(RISK_PROFILE_PATH),
                state_store=RiskStateStore(RISK_STATE_PATH),
                journal=self.event_journal,
                account_id=account_id,
                mode=self.risk_dashboard_mode.get(),
            )
        except (ValueError, RiskReportError) as exc:
            self.risk_dashboard_status.set(f"Ошибка: {exc}")
            self.last_risk_dashboard_snapshot = None
            for variable in self.risk_dashboard_summary_vars.values():
                variable.set("—")
            self.risk_limits_tree.delete(*self.risk_limits_tree.get_children())
            self.risk_events_tree.delete(*self.risk_events_tree.get_children())
            self.risk_dashboard_detail_text.configure(state="normal")
            self.risk_dashboard_detail_text.delete("1.0", "end")
            self.risk_dashboard_detail_text.insert("1.0", str(exc))
            self.risk_dashboard_detail_text.configure(state="disabled")
            self._apply_kill_switch_banner(None)
            self._sync_risk_profile_editor(None)
            return
        self._apply_risk_dashboard_snapshot(snapshot)

    def _apply_risk_dashboard_snapshot(
        self,
        snapshot: RiskDashboardSnapshot,
    ) -> None:
        self.last_risk_dashboard_snapshot = snapshot
        summary = snapshot.summary
        policy_short = snapshot.policy_hash[:16]
        self.risk_dashboard_summary_vars["engine"].set(
            f"{snapshot.engine_status_title} ({snapshot.engine_status})"
        )
        self.risk_dashboard_summary_vars["account"].set(
            f"{snapshot.account_id}\n{snapshot.mode}"
        )
        self.sb_account_id_display.set(snapshot.account_id)
        self.risk_dashboard_account_id.set(snapshot.account_id)
        self.risk_dashboard_summary_vars["policy"].set(policy_short)
        decision_status = summary.get("latest_decision_status_title") or "—"
        requested = summary.get("requested_target_lots")
        approved = summary.get("approved_target_lots")
        self.risk_dashboard_summary_vars["decision"].set(
            f"{decision_status}: {requested if requested is not None else '—'} → "
            f"{approved if approved is not None else '—'}"
        )
        used_orders = summary.get("daily_order_count")
        order_limit = summary.get("max_orders_per_day")
        remaining_orders = summary.get("orders_remaining")
        self.risk_dashboard_summary_vars["orders"].set(
            f"{used_orders}/{order_limit if order_limit is not None else '∞'}; "
            f"свободно {remaining_orders if remaining_orders is not None else '∞'}"
        )
        turnover = self._format_risk_value(summary.get("daily_turnover_rub"), "₽")
        turnover_limit = self._format_risk_value(
            summary.get("daily_turnover_limit_rub"), "₽"
        )
        turnover_remaining = self._format_risk_value(
            summary.get("daily_turnover_remaining_rub"), "₽"
        )
        self.risk_dashboard_summary_vars["turnover"].set(
            f"{turnover} / {turnover_limit}; остаток {turnover_remaining}"
        )
        self.risk_dashboard_summary_vars["daily_pnl"].set(
            self._format_risk_value(summary.get("daily_pnl_rub"), "₽")
        )
        self.risk_dashboard_summary_vars["drawdown"].set(
            self._format_risk_value(summary.get("drawdown"), "%")
        )
        cash = self._format_risk_value(summary.get("cash_rub"), "₽")
        reserve = self._format_risk_value(summary.get("cash_reserve_rub"), "₽")
        surplus = self._format_risk_value(
            summary.get("cash_reserve_surplus_rub"), "₽"
        )
        self.risk_dashboard_summary_vars["cash"].set(
            f"{cash}; резерв {reserve}; запас {surplus}"
        )
        self.risk_dashboard_summary_vars["snapshot"].set(
            self._format_risk_value(summary.get("snapshot_age_seconds"), "с")
        )
        blockers = summary.get("round_trip_blockers") or []
        self.risk_dashboard_summary_vars["round_trip"].set(
            "ГОТОВ: ≥2 свободных исполнения"
            if summary.get("round_trip_ready")
            else "БЛОК: " + "; ".join(str(item) for item in blockers)
        )
        gate_parts = []
        if summary.get("kill_switch_active"):
            gate_parts.append("KILL SWITCH")
        if summary.get("risk_resync_required"):
            gate_parts.append("RISK RESYNC")
        self.risk_dashboard_summary_vars["persistent_gate"].set(
            ", ".join(gate_parts) if gate_parts else "нет"
        )
        self._apply_kill_switch_banner(summary, snapshot.recent_events)
        self._sync_risk_profile_editor(snapshot)

        self.risk_limits_tree.delete(*self.risk_limits_tree.get_children())
        for item in snapshot.limits:
            utilization = (
                "—"
                if item.utilization is None
                else f"{item.utilization * 100:.1f}%"
            )
            self.risk_limits_tree.insert(
                "",
                "end",
                iid=item.code,
                text=item.title,
                tags=(item.status,),
                values=(
                    item.status,
                    self._format_risk_value(item.limit, item.unit),
                    self._format_risk_value(item.current, item.unit),
                    self._format_risk_value(item.remaining, item.unit),
                    utilization,
                    item.note,
                ),
            )

        self.risk_events_tree.delete(*self.risk_events_tree.get_children())
        self._risk_dashboard_event_rows.clear()
        for row in snapshot.recent_events:
            iid = str(row.get("id") or len(self._risk_dashboard_event_rows) + 1)
            self._risk_dashboard_event_rows[iid] = row
            payload = row.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            summary_parts = []
            for key in (
                "breaches",
                "reason",
                "execution_source",
                "risk_decision_id",
                "error",
            ):
                value = payload.get(key)
                if value not in (None, "", []):
                    summary_parts.append(f"{key}={value}")
            severity = str(row.get("severity") or "")
            self.risk_events_tree.insert(
                "",
                "end",
                iid=iid,
                tags=(severity,),
                values=(
                    row.get("timestamp_utc") or "",
                    row.get("event_type") or "",
                    severity,
                    row.get("status") or payload.get("status") or "",
                    row.get("action") or "",
                    "; ".join(summary_parts)[:600],
                ),
            )

        detail = {
            "warnings": list(snapshot.warnings),
            "latest_decision": snapshot.latest_decision,
            "preflight": {
                "round_trip_ready": summary.get("round_trip_ready"),
                "required_orders": summary.get("required_round_trip_orders"),
                "orders_remaining": summary.get("orders_remaining"),
                "estimated_turnover_rub": summary.get(
                    "estimated_round_trip_turnover_rub"
                ),
                "turnover_remaining_rub": summary.get(
                    "daily_turnover_remaining_rub"
                ),
                "blockers": blockers,
            },
        }
        self.risk_dashboard_detail_text.configure(state="normal")
        self.risk_dashboard_detail_text.delete("1.0", "end")
        self.risk_dashboard_detail_text.insert(
            "1.0",
            json.dumps(detail, ensure_ascii=False, indent=2, default=str),
        )
        self.risk_dashboard_detail_text.configure(state="disabled")
        self.risk_dashboard_status.set(
            f"Обновлено {snapshot.generated_at[:19]} UTC"
        )

    def _show_selected_risk_event(self, _event: tk.Event | None = None) -> None:
        selection = self.risk_events_tree.selection()
        if not selection:
            return
        row = self._risk_dashboard_event_rows.get(selection[0])
        if row is None:
            return
        self.risk_dashboard_detail_text.configure(state="normal")
        self.risk_dashboard_detail_text.delete("1.0", "end")
        self.risk_dashboard_detail_text.insert(
            "1.0",
            json.dumps(row, ensure_ascii=False, indent=2, default=str),
        )
        self.risk_dashboard_detail_text.configure(state="disabled")

    def _export_risk_dashboard_snapshot(self) -> None:
        if self.last_risk_dashboard_snapshot is None:
            self._refresh_risk_dashboard()
        snapshot = self.last_risk_dashboard_snapshot
        if snapshot is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Экспорт Risk Dashboard snapshot",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialdir=_reports_initial_dir(),
            initialfile=build_export_filename(
                "risk_dashboard_snapshot", DISPLAY_VERSION, ".json"
            ),
        )
        if not path:
            return
        target = collision_safe_path(path)
        write_dashboard_snapshot(snapshot, target)
        self.risk_dashboard_status.set(f"Snapshot экспортирован: {target}")

    def _export_risk_burn_in_report(self) -> None:
        try:
            account_id = self._selected_account_id()
            raw_hours = self.risk_dashboard_hours.get().strip()
            hours = float(raw_hours) if raw_hours else None
            if hours is not None and hours <= 0:
                raise ValueError("Период burn-in должен быть положительным.")
        except ValueError as exc:
            messagebox.showerror("Risk burn-in", str(exc), parent=self)
            return
        directory = filedialog.askdirectory(
            parent=self,
            title="Папка для Risk burn-in отчёта",
            initialdir=_reports_initial_dir(),
            mustexist=False,
        )
        if not directory:
            return
        report = load_risk_burn_in_report(
            self.event_journal,
            account_id=account_id,
            hours=hours,
            software_version=__version__,
        )
        paths = write_burn_in_report(report, directory)
        self.risk_dashboard_status.set(
            f"Burn-in: {report.overall_status}; отчёт сохранён"
        )
        messagebox.showinfo(
            "Risk burn-in отчёт",
            "Статус: "
            f"{report.overall_status}\n\n"
            + "\n".join(str(path) for path in paths.values()),
            parent=self,
        )

    def _open_risk_events(self) -> None:
        self.event_filter.set("risk")
        self.event_severity_filter.set("Все")
        self.event_search.set("")
        self.notebook.select(self.events_tab)
        self._refresh_events()

    # ------------------------------------------------------------------
    # Production readiness, backup and secure diagnostics
    # ------------------------------------------------------------------
    def _build_readiness_tab(self) -> None:
        self.readiness_tab.columnconfigure(0, weight=1)
        self.readiness_tab.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self.readiness_tab, padding=(12, 10, 12, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            toolbar,
            text="Проверить готовность",
            command=self._refresh_readiness,
        ).pack(side="left")
        ttk.Button(
            toolbar,
            text="Создать backup",
            command=self._create_runtime_backup,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            toolbar,
            text="Проверить backup",
            command=self._verify_runtime_backup,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            toolbar,
            text="Предпросмотр/восстановить",
            command=self._restore_runtime_backup,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            toolbar,
            text="Диагностический пакет",
            command=self._create_support_bundle,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            toolbar,
            textvariable=self.readiness_status,
            style="Subtitle.TLabel",
        ).pack(side="right")

        summary = ttk.LabelFrame(
            self.readiness_tab,
            text="Production Readiness Gate",
            padding=10,
        )
        summary.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        summary.columnconfigure(1, weight=1)
        ttk.Label(summary, text="Account ID:").grid(row=0, column=0, sticky="w")
        account_entry = ttk.Entry(
            summary,
            textvariable=self.readiness_account_id,
            state="readonly",
        )
        account_entry.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(
            summary,
            text="Копировать ID",
            command=lambda: self._copy_text(
                self.readiness_account_id.get(),
                status="Полный Account ID скопирован",
            ),
        ).grid(row=0, column=2, sticky="e")
        ttk.Label(summary, text="Backup:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(summary, textvariable=self.readiness_backup_status).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )
        ttk.Label(summary, text="Support bundle:").grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Label(summary, textvariable=self.readiness_support_status).grid(
            row=2, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        content = ttk.Panedwindow(self.readiness_tab, orient="vertical")
        content.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        checks_box = ttk.LabelFrame(content, text="Проверки", padding=8)
        detail_box = ttk.LabelFrame(content, text="Отчёт / recovery context", padding=8)
        content.add(checks_box, weight=3)
        content.add(detail_box, weight=2)

        checks_box.columnconfigure(0, weight=1)
        checks_box.rowconfigure(0, weight=1)
        self.readiness_tree = ttk.Treeview(
            checks_box,
            columns=("status", "detail"),
            show="tree headings",
            height=12,
        )
        self.readiness_tree.heading("#0", text="Проверка")
        self.readiness_tree.heading("status", text="Статус")
        self.readiness_tree.heading("detail", text="Подробности")
        self.readiness_tree.column("#0", width=270, stretch=False)
        self.readiness_tree.column("status", width=90, anchor="center", stretch=False)
        self.readiness_tree.column("detail", width=850, stretch=True)
        self.readiness_tree.tag_configure("PASS", foreground="#187b2f")
        self.readiness_tree.tag_configure("WARN", foreground="#9a6500")
        self.readiness_tree.tag_configure("FAIL", foreground="#b00020")
        scroll = ttk.Scrollbar(
            checks_box, orient="vertical", command=self.readiness_tree.yview
        )
        self.readiness_tree.configure(yscrollcommand=scroll.set)
        self.readiness_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        detail_box.columnconfigure(0, weight=1)
        detail_box.rowconfigure(0, weight=1)
        self.readiness_detail_text = tk.Text(
            detail_box,
            wrap="word",
            height=12,
            state="disabled",
            font=("Consolas", 9),
        )
        self.readiness_detail_text.grid(row=0, column=0, sticky="nsew")
        detail_scroll = ttk.Scrollbar(
            detail_box, orient="vertical", command=self.readiness_detail_text.yview
        )
        self.readiness_detail_text.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.grid(row=0, column=1, sticky="ns")

    def _refresh_readiness(self) -> None:
        try:
            account_id = self._selected_account_id()
        except ValueError:
            account_id = None
        api_text = self.sb_dashboard_vars["api"].get().strip()
        probe = probe_secret_provider(
            RUNTIME_DIR,
            provider=self.secret_provider,
        )
        api_status = probe.to_dict()
        if api_text and api_text != "Не проверен":
            api_status.update(
                {
                    "authenticated": api_text == "Доступен",
                    "available": api_text == "Доступен",
                    "detail": api_text,
                }
            )
        else:
            api_status.update(
                {
                    "authenticated": None,
                    "available": None,
                    "detail": "API ещё не проверен в текущем GUI-сеансе.",
                }
            )
        report = self.readiness_evaluator.evaluate(
            account_id=account_id,
            api_status=api_status,
        )
        self.last_readiness_report = report
        self.readiness_account_id.set(report.account_id or "—")
        self.readiness_status.set(str(report.status))
        self.readiness_tree.delete(*self.readiness_tree.get_children())
        for check in report.checks:
            self.readiness_tree.insert(
                "",
                "end",
                iid=check.code,
                text=check.title,
                tags=(check.status,),
                values=(check.status, check.detail),
            )
        payload = report.to_dict()
        self.readiness_detail_text.configure(state="normal")
        self.readiness_detail_text.delete("1.0", "end")
        self.readiness_detail_text.insert(
            "1.0", json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )
        self.readiness_detail_text.configure(state="disabled")

    def _create_runtime_backup(self) -> None:
        if self.robot_thread and self.robot_thread.is_alive():
            messagebox.showwarning(
                "Runtime backup",
                "Остановите торговый цикл перед созданием резервной копии.",
                parent=self,
            )
            return
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        default_name = build_export_filename(
            "runtime_backup", DISPLAY_VERSION, ".zip"
        )
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Создать резервную копию runtime",
            initialdir=str(BACKUPS_DIR),
            initialfile=default_name,
            defaultextension=".zip",
            filetypes=[("ZIP", "*.zip")],
        )
        if not path:
            return
        try:
            created = self.runtime_backup_manager.create_backup(
                collision_safe_path(path)
            )
        except (RuntimeBackupError, OSError) as exc:
            messagebox.showerror("Runtime backup", str(exc), parent=self)
            return
        self.readiness_backup_status.set(f"Создан: {created}")
        self._refresh_readiness()
        messagebox.showinfo("Runtime backup", f"Backup создан:\n{created}", parent=self)

    def _verify_runtime_backup(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Проверить runtime backup",
            initialdir=str(BACKUPS_DIR),
            filetypes=[("ZIP", "*.zip")],
        )
        if not path:
            return
        result = self.runtime_backup_manager.verify_backup(path)
        self.readiness_backup_status.set(
            f"{'VALID' if result.valid else 'INVALID'}: {path}"
        )
        self._show_backup_verification_dialog(result)
        self._refresh_readiness()

    def _show_backup_verification_dialog(self, result: Any) -> None:
        """Show a readable backup report instead of raw JSON in a messagebox."""

        dialog = tk.Toplevel(self)
        dialog.title("Проверка backup")
        dialog.transient(self)
        dialog.minsize(820, 520)
        dialog.geometry("940x620")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)

        status_text = (
            "BACKUP ДЕЙСТВИТЕЛЕН — восстановление разрешено"
            if result.valid
            else "BACKUP НЕДЕЙСТВИТЕЛЕН — восстановление запрещено"
        )
        status = ttk.Label(
            dialog,
            text=status_text,
            padding=(14, 12),
            font=("Segoe UI", 11, "bold"),
        )
        status.grid(row=0, column=0, sticky="ew")

        manifest = result.manifest if isinstance(result.manifest, dict) else {}
        metadata = ttk.Frame(dialog, padding=(14, 4, 14, 8))
        metadata.grid(row=1, column=0, sticky="ew")
        metadata.columnconfigure(1, weight=1)
        fields = (
            ("Архив", result.path),
            ("Создан", manifest.get("created_at") or "—"),
            ("Версия", manifest.get("app_version") or "—"),
            ("Исходный runtime", manifest.get("source_directory") or "—"),
            ("Токен включён", "ДА" if manifest.get("token_included") else "нет"),
        )
        for row, (title, value) in enumerate(fields):
            ttk.Label(metadata, text=title + ":").grid(
                row=row, column=0, sticky="nw", padx=(0, 10), pady=2
            )
            value_label = ttk.Label(metadata, text=str(value), wraplength=760)
            value_label.grid(row=row, column=1, sticky="w", pady=2)

        content = ttk.Panedwindow(dialog, orient="vertical")
        content.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 10))

        files_box = ttk.LabelFrame(content, text="Содержимое backup", padding=6)
        files_box.columnconfigure(0, weight=1)
        files_box.rowconfigure(0, weight=1)
        files_tree = ttk.Treeview(
            files_box,
            columns=("kind", "schema", "size", "sha"),
            show="tree headings",
            height=8,
        )
        files_tree.heading("#0", text="Файл")
        files_tree.heading("kind", text="Тип")
        files_tree.heading("schema", text="Schema")
        files_tree.heading("size", text="Размер")
        files_tree.heading("sha", text="SHA-256")
        files_tree.column("#0", width=240, stretch=True)
        files_tree.column("kind", width=90, stretch=False)
        files_tree.column("schema", width=80, stretch=False, anchor="center")
        files_tree.column("size", width=100, stretch=False, anchor="e")
        files_tree.column("sha", width=220, stretch=True)
        entries = manifest.get("entries")
        entries = entries if isinstance(entries, list) else []
        for index, raw in enumerate(entries):
            if not isinstance(raw, dict):
                continue
            size = int(raw.get("size_bytes") or 0)
            size_text = f"{size / 1024:.1f} KiB" if size >= 1024 else f"{size} B"
            files_tree.insert(
                "",
                "end",
                iid=f"entry-{index}",
                text=str(raw.get("name") or "—"),
                values=(
                    raw.get("kind") or "—",
                    raw.get("schema_version") if raw.get("schema_version") is not None else "—",
                    size_text,
                    raw.get("sha256") or "—",
                ),
            )
        files_tree.grid(row=0, column=0, sticky="nsew")
        files_scroll = ttk.Scrollbar(files_box, orient="vertical", command=files_tree.yview)
        files_scroll.grid(row=0, column=1, sticky="ns")
        files_tree.configure(yscrollcommand=files_scroll.set)
        content.add(files_box, weight=3)

        messages_box = ttk.LabelFrame(content, text="Ошибки и предупреждения", padding=6)
        messages_box.columnconfigure(0, weight=1)
        messages_box.rowconfigure(0, weight=1)
        messages = tk.Text(messages_box, height=8, wrap="word", font=("Segoe UI", 9))
        if result.errors:
            messages.insert("end", "Ошибки:\n")
            messages.insert("end", "\n".join(f"• {item}" for item in result.errors))
            messages.insert("end", "\n")
        if result.warnings:
            messages.insert("end", "Предупреждения:\n")
            messages.insert("end", "\n".join(f"• {item}" for item in result.warnings))
            messages.insert("end", "\n")
        if not result.errors and not result.warnings:
            messages.insert("end", "Ошибок и предупреждений нет. Все SHA-256 и схемы проверены.")
        messages.configure(state="disabled")
        messages.grid(row=0, column=0, sticky="nsew")
        message_scroll = ttk.Scrollbar(messages_box, orient="vertical", command=messages.yview)
        message_scroll.grid(row=0, column=1, sticky="ns")
        messages.configure(yscrollcommand=message_scroll.set)
        content.add(messages_box, weight=2)

        buttons = ttk.Frame(dialog, padding=(14, 0, 14, 12))
        buttons.grid(row=3, column=0, sticky="e")
        ttk.Button(
            buttons,
            text="Копировать отчёт",
            command=lambda: self._copy_text(
                format_backup_verification_summary(result),
                status="Отчёт проверки backup скопирован",
            ),
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(buttons, text="Закрыть", command=dialog.destroy).grid(
            row=0, column=1
        )
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        dialog.focus_set()

    def _restore_runtime_backup(self) -> None:
        if self.robot_thread and self.robot_thread.is_alive():
            messagebox.showwarning(
                "Восстановление runtime",
                "Остановите торговый цикл перед восстановлением.",
                parent=self,
            )
            return
        path = filedialog.askopenfilename(
            parent=self,
            title="Выберите runtime backup",
            initialdir=str(BACKUPS_DIR),
            filetypes=[("ZIP", "*.zip")],
        )
        if not path:
            return
        try:
            preview = self.runtime_backup_manager.preview_restore(path)
        except (RuntimeBackupError, OSError) as exc:
            messagebox.showerror("Восстановление runtime", str(exc), parent=self)
            return
        lines = [f"{item.name}: {item.action}" for item in preview]
        confirmation = simpledialog.askstring(
            "Восстановление runtime",
            "Предпросмотр:\n"
            + "\n".join(lines)
            + "\n\nВведите RESTORE RUNTIME для продолжения.",
            parent=self,
        )
        if confirmation is None:
            return
        try:
            self.runtime_backup_manager.restore_backup(
                path,
                confirmation=confirmation,
            )
            # Reset the lightweight journal facade after its SQLite contents
            # were restored through the online backup API.
            self.event_journal = EventJournal(EVENT_DB_PATH)
        except (RuntimeBackupError, OSError) as exc:
            messagebox.showerror(
                "Восстановление runtime",
                str(exc)
                + "\n\nУбедитесь, что торговый цикл остановлен и другая копия "
                "программы не использует этот runtime. Для каталога OneDrive "
                "при повторной ошибке временно приостановите синхронизацию.",
                parent=self,
            )
            return
        messagebox.showinfo(
            "Восстановление runtime",
            "Файлы восстановлены. Перезапустите GUI перед продолжением работы.",
            parent=self,
        )
        self._refresh_readiness()

    def _create_support_bundle(self) -> None:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        default_name = build_export_filename(
            "support_bundle", DISPLAY_VERSION, ".zip"
        )
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Собрать диагностический пакет",
            initialdir=_reports_initial_dir(),
            initialfile=default_name,
            defaultextension=".zip",
            filetypes=[("ZIP", "*.zip")],
        )
        if not path:
            return
        try:
            account_id = self._selected_account_id()
        except ValueError:
            account_id = None
        known_secret = self.sb_token.get().strip()
        try:
            result = SupportBundleBuilder(
                RUNTIME_DIR,
                app_version=__version__,
            ).build(
                collision_safe_path(path),
                account_id=account_id,
                known_secrets=([known_secret] if known_secret else []),
            )
        except (SupportBundleError, OSError) as exc:
            messagebox.showerror("Диагностический пакет", str(exc), parent=self)
            return
        self.readiness_support_status.set(f"Создан: {result.path}")
        messagebox.showinfo(
            "Диагностический пакет",
            f"Пакет создан и прошёл secret scan:\n{result.path}",
            parent=self,
        )

    # ------------------------------------------------------------------
    # Controlled Sandbox order diagnostics
    # ------------------------------------------------------------------
    def _build_diagnostics_tab(self) -> None:
        self.diagnostics_tab.columnconfigure(1, weight=1)
        self.diagnostics_tab.rowconfigure(0, weight=1)

        controls_host = ttk.Frame(self.diagnostics_tab)
        controls_host.grid(row=0, column=0, sticky="nsew")
        controls_host.rowconfigure(0, weight=1)
        controls_host.columnconfigure(0, weight=1)
        controls = self._create_scrollable_controls(controls_host, width=350)

        output = ttk.Frame(self.diagnostics_tab, padding=(0, 12, 12, 12))
        output.grid(row=0, column=1, sticky="nsew")
        output.rowconfigure(1, weight=1)
        output.columnconfigure(0, weight=1)

        connection_box = ttk.LabelFrame(
            controls, text="Источник настроек", padding=10
        )
        connection_box.pack(fill="x", pady=(0, 8))
        ttk.Label(
            connection_box,
            text=(
                "Используются токен, CA bundle, тайм-ауты и выбранный счёт "
                "с вкладки «T-Invest Sandbox»."
            ),
            wraplength=310,
            justify="left",
        ).pack(anchor="w")
        ttk.Label(connection_box, text="Выбранный счёт").pack(
            anchor="w", pady=(7, 0)
        )
        self.diag_account_combo = ttk.Combobox(
            connection_box,
            textvariable=self.sb_account,
            state="readonly",
            width=33,
        )
        self.diag_account_combo.pack(fill="x", pady=(2, 5))
        ttk.Button(
            connection_box,
            text="Обновить список счетов",
            command=self._load_sandbox_accounts,
        ).pack(fill="x", pady=2)

        instrument_box = ttk.LabelFrame(
            controls, text="Контрольный инструмент", padding=10
        )
        instrument_box.pack(fill="x", pady=(0, 8))
        self._labeled_entry(instrument_box, "Тикер", self.diag_ticker)
        self._labeled_entry(instrument_box, "Class code", self.diag_class_code)
        ttk.Label(
            instrument_box,
            text=(
                "Диагностический размер жёстко ограничен одним лотом. "
                "Стратегия SMA в этом тесте не участвует."
            ),
            wraplength=310,
            justify="left",
        ).pack(anchor="w")

        inspect_box = ttk.LabelFrame(
            controls, text="Проверка и восстановление", padding=10
        )
        inspect_box.pack(fill="x", pady=(0, 8))
        ttk.Button(
            inspect_box,
            text="Проверить готовность и позицию",
            command=self._run_diagnostic_snapshot,
        ).pack(fill="x", pady=2)
        ttk.Button(
            inspect_box,
            text="Проверить / восстановить pending-order",
            command=self._recover_diagnostic_order,
        ).pack(fill="x", pady=2)

        execution_box = ttk.LabelFrame(
            controls, text="Контрольная заявка — только Sandbox", padding=10
        )
        execution_box.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(
            execution_box,
            text="Разрешить диагностическую заявку в Sandbox",
            variable=self.diag_arm_checkbox,
        ).pack(anchor="w")
        ttk.Label(execution_box, text="Введите DIAGNOSTIC:").pack(
            anchor="w", pady=(5, 0)
        )
        ttk.Entry(
            execution_box, textvariable=self.diag_confirm_text
        ).pack(fill="x", pady=(2, 6))
        ttk.Button(
            execution_box,
            text="Купить 1 лот",
            command=lambda: self._run_diagnostic_order("BUY"),
            style="Danger.TButton",
        ).pack(fill="x", pady=2)
        ttk.Button(
            execution_box,
            text="Продать 1 лот",
            command=lambda: self._run_diagnostic_order("SELL"),
            style="Danger.TButton",
        ).pack(fill="x", pady=2)
        ttk.Label(
            execution_box,
            text=(
                "Продажа разрешена только при наличии минимум одного длинного "
                "лота. Новая заявка блокируется, пока предыдущая не сверена "
                "с портфелем."
            ),
            wraplength=310,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        explanation = ttk.LabelFrame(
            output, text="Что проверяет диагностика", padding=10
        )
        explanation.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(
            explanation,
            text=(
                "Цепочка: торговый статус → доступные лоты → сохранение intent "
                "→ PostSandboxOrder → GetSandboxOrderState → сверка фактической "
                "позиции. Диагностическое состояние хранится отдельно от торгового робота."
            ),
            wraplength=900,
            justify="left",
        ).pack(anchor="w")

        result_box = ttk.LabelFrame(
            output, text="Результат диагностики", padding=8
        )
        result_box.grid(row=1, column=0, sticky="nsew")
        result_box.rowconfigure(0, weight=1)
        result_box.columnconfigure(0, weight=1)
        self.diagnostic_result_text = tk.Text(
            result_box,
            wrap="word",
            font=("Consolas", 10),
            state="disabled",
        )
        scroll = ttk.Scrollbar(
            result_box,
            orient="vertical",
            command=self.diagnostic_result_text.yview,
        )
        self.diagnostic_result_text.configure(yscrollcommand=scroll.set)
        self.diagnostic_result_text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

    def _diagnostic_config(self) -> DiagnosticConfig:
        ticker = self.diag_ticker.get().strip().upper()
        class_code = self.diag_class_code.get().strip().upper()
        if not ticker or not class_code:
            raise ValueError("Укажите тикер и class code для диагностики.")
        return DiagnosticConfig(
            ticker=ticker,
            class_code=class_code,
            lots=1,
            state_file=str(DIAGNOSTIC_STATE_PATH),
            journal_file=str(EVENT_DB_PATH),
            risk_profile_file=str(RISK_PROFILE_PATH),
            risk_state_file=str(RISK_STATE_PATH),
        )

    def _confirm_diagnostic(self, direction: str) -> bool:
        if (
            not self.diag_arm_checkbox.get()
            or self.diag_confirm_text.get().strip().upper() != "DIAGNOSTIC"
        ):
            messagebox.showwarning(
                "Диагностическая заявка заблокирована",
                "Установите флажок и введите слово DIAGNOSTIC.",
                parent=self,
            )
            return False
        verb = "КУПИТЬ" if direction == "BUY" else "ПРОДАТЬ"
        return messagebox.askyesno(
            "Подтверждение диагностической заявки",
            f"{verb} ровно 1 лот на выбранном ВИРТУАЛЬНОМ счёте?\n\n"
            "Это независимый тест исполнительного контура. Реальный счёт не используется.",
            icon="warning",
            parent=self,
        )

    def _prepare_diagnostic_call(
        self,
    ) -> tuple[str, str | None, str, DiagnosticConfig] | None:
        try:
            token = self._get_token()
            ca_bundle = self._get_ca_bundle()
            account_id = self._selected_account_id()
            config = self._diagnostic_config()
            self._read_client_settings()
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc), parent=self)
            return None
        return token, ca_bundle, account_id, config

    def _run_diagnostic_snapshot(self) -> None:
        prepared = self._prepare_diagnostic_call()
        if prepared is None:
            return
        token, ca_bundle, account_id, config = prepared
        if not self._begin_sandbox_operation("диагностика готовности"):
            return
        self.diag_status.set("Проверяю готовность исполнительного контура…")

        def work() -> dict[str, Any]:
            with self._make_tbank_client(token, ca_bundle) as api:
                return SandboxOrderDiagnostics(
                    api, account_id, config
                ).snapshot()

        def done(result: dict[str, Any]) -> None:
            self._show_diagnostic_result(result)
            self.diag_status.set(
                "Диагностика: позиция "
                f"{result.get('current_lots', '—')} лот(ов), API доступен"
            )
            self._refresh_events()

        self._run_background(
            work,
            done,
            on_finally=self._end_sandbox_operation,
        )

    def _run_diagnostic_order(self, direction: str) -> None:
        if not self._confirm_diagnostic(direction):
            return
        prepared = self._prepare_diagnostic_call()
        if prepared is None:
            return
        token, ca_bundle, account_id, config = prepared
        if not self._begin_sandbox_operation(
            f"диагностическая заявка {direction}"
        ):
            return
        self.diag_status.set(f"Отправляю диагностическую заявку {direction}…")

        def work() -> dict[str, Any]:
            with self._make_tbank_client(token, ca_bundle) as api:
                result = SandboxOrderDiagnostics(
                    api, account_id, config
                ).execute(direction)
                if result.get("status") == "processed":
                    try:
                        state = CanonicalPortfolioManager(
                            api,
                            account_id,
                            robot_state_file=ROBOT_STATE_PATH,
                            portfolio_state_file=PORTFOLIO_STATE_PATH,
                            journal_file=EVENT_DB_PATH,
                        ).refresh()
                        result["canonical_portfolio_snapshot"] = (
                            PortfolioSnapshotBuilder(state).to_dict()
                        )
                    except Exception as exc:  # fill remains valid; refresh is secondary
                        result["canonical_portfolio_refresh_error"] = type(exc).__name__
                return result

        def done(result: dict[str, Any]) -> None:
            self._show_diagnostic_result(result)
            self.diag_status.set(
                "Диагностика: "
                f"{result.get('status')}; {direction}; "
                f"позиция {result.get('actual_lots_after', result.get('current_lots', '—'))}"
            )
            snapshot = result.get("canonical_portfolio_snapshot")
            if isinstance(snapshot, dict):
                self._display_portfolio_snapshot(snapshot)
            else:
                self.after(150, lambda: self._refresh_portfolio(manual=False))
            feedback = build_diagnostic_feedback(
                result,
                direction=direction,
                ticker=config.ticker,
            )
            if feedback.requires_attention:
                messagebox.showwarning(
                    feedback.title, feedback.message, parent=self
                )
            else:
                messagebox.showinfo(
                    feedback.title, feedback.message, parent=self
                )
            self._refresh_events()
            self._refresh_risk_dashboard()
            self._refresh_readiness()

        self._run_background(
            work,
            done,
            on_finally=self._end_sandbox_operation,
        )

    def _recover_diagnostic_order(self) -> None:
        prepared = self._prepare_diagnostic_call()
        if prepared is None:
            return
        token, ca_bundle, account_id, config = prepared
        if not self._begin_sandbox_operation("восстановление diagnostic pending-order"):
            return
        self.diag_status.set("Проверяю diagnostic pending-order…")

        def work() -> dict[str, Any]:
            with self._make_tbank_client(token, ca_bundle) as api:
                return SandboxOrderDiagnostics(
                    api, account_id, config
                ).recover_pending()

        def done(result: dict[str, Any]) -> None:
            self._show_diagnostic_result(result)
            self.diag_status.set(f"Диагностика восстановления: {result.get('status')}")
            self._refresh_events()

        self._run_background(
            work,
            done,
            on_finally=self._end_sandbox_operation,
        )

    def _show_diagnostic_result(self, result: Any) -> None:
        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        self.diagnostic_result_text.configure(state="normal")
        self.diagnostic_result_text.delete("1.0", "end")
        self.diagnostic_result_text.insert("1.0", text)
        self.diagnostic_result_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Structured event journal
    # ------------------------------------------------------------------
    def _build_events_tab(self) -> None:
        frame = ttk.Frame(self.events_tab, padding=10)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(2, weight=3)
        frame.rowconfigure(4, weight=2)
        frame.columnconfigure(0, weight=1)

        controls = ttk.Frame(frame)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(controls, text="Категория:").pack(side="left")
        ttk.Combobox(
            controls,
            textvariable=self.event_filter,
            values=[
                "Все", "session", "cycle", "decision", "order", "api",
                "performance", "strategy", "strategy_comparison",
                "strategy_config", "state", "incident", "diagnostic",
                "diagnostic_order", "risk", "risk_control",
            ],
            state="readonly",
            width=20,
        ).pack(side="left", padx=(4, 8))
        ttk.Label(controls, text="Уровень:").pack(side="left")
        ttk.Combobox(
            controls,
            textvariable=self.event_severity_filter,
            values=["Все", "INFO", "WARNING", "ERROR"],
            state="readonly",
            width=10,
        ).pack(side="left", padx=(4, 8))
        ttk.Label(controls, text="Сессия:").pack(side="left")
        self.event_session_combo = ttk.Combobox(
            controls,
            textvariable=self.event_session_filter,
            values=["Все"],
            state="readonly",
            width=14,
        )
        self.event_session_combo.pack(side="left", padx=(4, 8))
        ttk.Label(controls, text="Поиск:").pack(side="left")
        search_entry = ttk.Entry(
            controls, textvariable=self.event_search, width=20
        )
        search_entry.pack(side="left", padx=(4, 8), fill="x", expand=True)
        search_entry.bind("<Return>", lambda _e: self._refresh_events())
        ttk.Button(controls, text="Обновить", command=self._refresh_events).pack(side="left")
        ttk.Button(controls, text="Экспорт CSV", command=self._export_events).pack(side="left", padx=6)

        info = ttk.Frame(frame)
        info.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(info, textvariable=self.event_stats).pack(side="left")
        ttk.Label(
            info,
            text=f"SQLite: {EVENT_DB_PATH.name}  |  компактный лог: {LOG_PATH.name}  |  debug: {DEBUG_LOG_PATH.name}",
        ).pack(side="right")

        columns = (
            "time", "category", "event", "severity", "session", "mode",
            "ticker", "action", "duration", "summary",
        )
        self.events_tree = ttk.Treeview(
            frame, columns=columns, show="headings", selectmode="browse"
        )
        headings = {
            "time": "UTC", "category": "Категория", "event": "Событие",
            "severity": "Уровень", "session": "Сессия", "mode": "Режим",
            "ticker": "Тикер", "action": "Действие",
            "duration": "Время, с", "summary": "Кратко",
        }
        widths = {
            "time": 165, "category": 105, "event": 180, "severity": 72,
            "session": 78, "mode": 125, "ticker": 58, "action": 65,
            "duration": 70, "summary": 420,
        }
        for column in columns:
            self.events_tree.heading(column, text=headings[column])
            self.events_tree.column(
                column, width=widths[column], minwidth=50,
                stretch=(column == "summary"),
            )
        self.events_tree.tag_configure("ERROR", background="#ffd9d9")
        self.events_tree.tag_configure("WARNING", background="#fff1cc")
        tree_y = ttk.Scrollbar(frame, orient="vertical", command=self.events_tree.yview)
        tree_x = ttk.Scrollbar(frame, orient="horizontal", command=self.events_tree.xview)
        self.events_tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)
        self.events_tree.grid(row=2, column=0, sticky="nsew")
        tree_y.grid(row=2, column=1, sticky="ns")
        tree_x.grid(row=3, column=0, sticky="ew")
        self.events_tree.bind("<<TreeviewSelect>>", self._show_selected_event)

        payload_box = ttk.LabelFrame(frame, text="Полная запись события", padding=6)
        payload_box.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        payload_box.rowconfigure(0, weight=1)
        payload_box.columnconfigure(0, weight=1)
        self.event_payload_text = tk.Text(
            payload_box, wrap="word", font=("Consolas", 9), state="disabled"
        )
        payload_scroll = ttk.Scrollbar(
            payload_box, orient="vertical", command=self.event_payload_text.yview
        )
        self.event_payload_text.configure(yscrollcommand=payload_scroll.set)
        self.event_payload_text.grid(row=0, column=0, sticky="nsew")
        payload_scroll.grid(row=0, column=1, sticky="ns")

    def _refresh_events(self) -> None:
        if not hasattr(self, "events_tree"):
            return
        category = self.event_filter.get().strip()
        category = None if category == "Все" else category
        severity = self.event_severity_filter.get().strip()
        severity = None if severity == "Все" else severity
        session_label = self.event_session_filter.get().strip()
        session_id = None if session_label == "Все" else session_label
        search = self.event_search.get().strip() or None

        session_ids = self.event_journal.session_ids(limit=50)
        values = ["Все", *session_ids]
        self.event_session_combo.configure(values=values)
        if session_id and session_id not in session_ids:
            self.event_session_filter.set("Все")
            session_id = None

        rows = self.event_journal.recent(
            limit=1000,
            category=category,
            severity=severity,
            session_id=session_id,
            search=search,
        )
        self.events_tree.delete(*self.events_tree.get_children())
        self._event_rows.clear()
        for row in rows:
            iid = str(row["id"])
            self._event_rows[iid] = row
            payload = row.get("payload", {})
            summary_parts: list[str] = []
            for key, value in (
                ("status", row.get("status") or payload.get("status")),
                ("signal", payload.get("signal")),
                ("position", (
                    f"{payload.get('current_lots')}→{payload.get('target_lots')}"
                    if payload.get("current_lots") is not None
                    and payload.get("target_lots") is not None else None
                )),
                ("strategy", row.get("strategy_id") or payload.get("strategy_id")),
                ("retries", payload.get("api_retry_count", payload.get("retry_count"))),
                ("error", payload.get("error")),
            ):
                if value not in (None, ""):
                    summary_parts.append(f"{key}={value}")
            summary = "; ".join(summary_parts)[:500]
            severity_value = str(row.get("severity", ""))
            duration = row.get("duration_seconds")
            duration_text = "" if duration is None else f"{float(duration):.3f}"
            self.events_tree.insert(
                "", "end", iid=iid, tags=(severity_value,),
                values=(
                    row.get("timestamp_utc", ""), row.get("category", ""),
                    row.get("event_type", ""), severity_value,
                    str(row.get("session_id") or "")[:8],
                    row.get("mode", "") or "", row.get("ticker", "") or "",
                    row.get("action", "") or "", duration_text, summary,
                ),
            )

        total = self.event_journal.count()
        warnings = self.event_journal.count(severity="WARNING")
        errors = self.event_journal.count(severity="ERROR")
        self.event_stats.set(
            f"Показано: {len(rows)} | всего: {total} | предупреждений: {warnings} | ошибок: {errors}"
        )

    def _show_selected_event(self, _event: tk.Event | None = None) -> None:
        selection = self.events_tree.selection()
        if not selection:
            return
        row = self._event_rows.get(selection[0])
        if not row:
            return
        text = json.dumps(row, ensure_ascii=False, indent=2, default=str)
        self.event_payload_text.configure(state="normal")
        self.event_payload_text.delete("1.0", "end")
        self.event_payload_text.insert("1.0", text)
        self.event_payload_text.configure(state="disabled")

    def _export_events(self) -> None:
        category = self.event_filter.get().strip()
        category = None if category == "Все" else category
        severity = self.event_severity_filter.get().strip()
        severity = None if severity == "Все" else severity
        session_label = self.event_session_filter.get().strip()
        session_id = None if session_label == "Все" else session_label
        search = self.event_search.get().strip() or None
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Экспорт структурированного журнала",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialdir=_reports_initial_dir(),
            initialfile=build_export_filename(
                "trading_events", DISPLAY_VERSION, ".csv"
            ),
        )
        if not path:
            return
        target = collision_safe_path(path)
        self.event_journal.export_csv(
            target,
            category=category,
            severity=severity,
            session_id=session_id,
            search=search,
            limit=100_000,
        )
        self.diag_status.set(f"События экспортированы: {target}")

    # ------------------------------------------------------------------
    # Logs and help
    # ------------------------------------------------------------------
    def _build_logs_tab(self) -> None:
        frame = ttk.Frame(self.logs_tab, padding=10)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(frame, wrap="none", font=("Consolas", 9), state="disabled")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="Очистить окно", command=self._clear_logs).pack(side="left")
        ttk.Button(buttons, text="Открыть папку проекта", command=self._open_project_folder).pack(side="left", padx=6)
        ttk.Label(
            buttons,
            text=f"Компактный: {LOG_PATH.name} | Подробный: {DEBUG_LOG_PATH.name}",
        ).pack(side="right")

    def _build_help_tab(self) -> None:
        frame = ttk.Frame(self.help_tab, padding=14)
        frame.pack(fill="both", expand=True)
        text = tk.Text(frame, wrap="word", font=("Segoe UI", 10), padx=14, pady=12)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        instructions = """
БЫСТРЫЙ СТАРТ v3.7-beta1

1. Сначала откройте «T-Invest Sandbox», введите токен и нажмите «Проверить подключение».
2. Выберите виртуальный счёт. Для первичной проверки запустите один dry-run.
3. Если API временно недоступен, робот не отправляет заявку: он фиксирует инцидент, увеличивает счётчик ошибок и при достижении порога открывает circuit breaker.
4. После восстановления связи робот сначала проверяет pending-order и фактическую позицию, затем возвращается к стратегии.
5. Перед автоматическим Sandbox-режимом откройте «Диагностика заявок» и выполните контролируемый цикл: купить 1 лот → проверить позицию → продать 1 лот.
6. Для диагностической заявки нужны флажок, слово DIAGNOSTIC и отдельное подтверждение. Для робота — флажок и слово SANDBOX.
7. DRY_RUN и SANDBOX_EXECUTION имеют отдельные профили PRIMARY/SHADOW. Перед запуском проверьте имя режима и hash активного профиля.
8. Вкладка «Виртуальный портфель» показывает позиции, кэш, цели, ownership, pending-order и результат reconciliation.
9. Вкладка «События v3.7-beta1» показывает структурированный журнал циклов, конфигураций, заявок, переходов состояния и инцидентов. Его можно экспортировать в CSV.
10. При закрытом или явно недоступном рынке робот переходит в MARKET_IDLE: свечи и стратегии не пересчитываются, но status-check и редкая portfolio reconciliation продолжаются.

ЧТО ПРОИСХОДИТ В ОДНОМ ЦИКЛЕ

• Загружаются только завершённые свечи.
• PRIMARY и выбранные SHADOW рассчитываются через единый StrategyDecision.
• Только PRIMARY определяет запрошенное число лотов; SHADOW не имеет пути к Risk Engine или заявке.
• Risk Engine формирует одобренную цель до INTENT_SAVED и может разрешить, уменьшить либо заблокировать операцию.
• Проверяются возраст данных, владелец позиции, торговый статус и доступный объём покупки.
• До отправки заявки одобренное риск-решение и намерение атомарно сохраняются в robot_state.json.
• Для решения создаётся детерминированный orderId.
• После ответа API проверяются статус и число исполненных лотов.
• Фактическая позиция повторно читается из портфеля и сверяется с ожидаемой.
• Только после подтверждённого fill и успешной reconciliation исполнение идемпотентно записывается в risk_state.json.
• Если риск-учёт не сохранился, pending-order остаётся и новые заявки блокируются до восстановления.
• Даже без новой свечи Sandbox-цикл периодически перепроверяет фактическую позицию.
• При расхождении новая заявка блокируется до восстановления состояния.
• Pending-order recovery выполняется раньше MARKET_IDLE.

MARKET_IDLE

• MARKET_IDLE_ENTERED — выбранный order route явно недоступен.
• MARKET_IDLE_HEARTBEAT — редкое подтверждение ожидания без минутного журнального шума.
• MARKET_IDLE_EXITED — API явно подтвердил доступность, обычный цикл возобновлён.
• Неоднозначный статус не выводит уже остановленный робот из MARKET_IDLE.

СОСТОЯНИЯ ЗАЯВКИ

RISK_EVALUATED → DECISION_CREATED → PRECHECK_PASSED → INTENT_SAVED → ORDER_SUBMITTED →
ORDER_ACCEPTED → NEW / PARTIALLY_FILLED / FILLED / REJECTED / CANCELLED →
PORTFOLIO_RECONCILED → RISK_ACCOUNTED.

При ошибке сохранения риск-учёта используется состояние RISK_ACCOUNTING_REQUIRED; оно блокирует новый intent, но позволяет повторно завершить тот же учёт без дублирования исполнения.

Если ответ потерян после отправки, pending-order не удаляется. Следующий цикл запрашивает состояние по тому же orderId. Это защищает от дублирующей заявки.

УСТОЙЧИВОСТЬ

• Connect timeout — сколько ждать установления соединения.
• Read timeout — сколько ждать ответ уже подключённого сервера.
• Ошибок до breaker — число последовательных временных отказов до блокировки API-вызовов.
• Пауза breaker — базовое время безопасной остановки; при повторных отказах пауза растёт.
• Максимальный возраст сигнала: 0 означает автоматический предел по таймфрейму. Устаревшее внутридневное решение не исполняется после долгого сбоя.

ЖУРНАЛЫ И ФАЙЛЫ СОСТОЯНИЯ

• robot_gui.log — компактный операторский журнал с ротацией.
• robot_debug.log — подробные DEBUG-payload и диагностика.
• trading_events.db — структурированный SQLite-журнал schema v2.
• robot_state.json — состояние торгового робота и pending-order.
• sandbox_diagnostic_state.json — отдельное состояние контролируемых заявок.
• strategy_profiles.json — отдельные профили DRY_RUN и SANDBOX_EXECUTION без токена и account ID.

Не удаляйте state-файл, пока не убедились у брокера, что незавершённых заявок нет. Повреждённый state-файл приводит к безопасной остановке, а не к созданию пустого состояния.

СТРАТЕГИИ И SHADOW MODE

• SMA использует быстрый/медленный диапазон и гистерезис против частых переключений.
• Donchian входит при пробое канала и выходит по нижнему каналу или ATR trailing stop.
• Ансамбль суммирует четыре трендовых голоса.
• PRIMARY — единственная исполняемая стратегия.
• SHADOW рассчитываются на той же новой свече, сравниваются и журналируются, но не отправляют заявку.
• Dry-run рассчитывает все выбранные решения без виртуальной заявки.
• Смена PRIMARY или её параметров при открытой позиции блокируется.
• Повреждённый профиль или несовпадение его hash приводит к fail-closed, а не к тихому возврату к стратегии по умолчанию.

OWNERSHIP И ВИРТУАЛЬНЫЙ ПОРТФЕЛЬ

• Открытая позиция без подтверждённого владельца отмечается UNATTRIBUTED и блокирует автоматическое исполнение.
• Восстановление ownership разрешено только при совпадении счёта, инструмента, интервала, strategy/config hash, количества лотов и сохранённого PRIMARY-решения.
• Если доказательств владения нет, непривязанную Sandbox-позицию можно закрыть отдельной контролируемой операцией с повторной проверкой количества.

БЕЗОПАСНОСТЬ

• Клиент использует только официальный Sandbox-контур; production-метода выставления заявок в проекте нет.
• .env содержит секретный токен — не публикуйте его.
• Диагностика ограничена ровно одним лотом.
• Успешный Sandbox-тест проверяет программную цепочку, но не доказывает прибыльность и не воспроизводит реальное исполнение полностью.
""".strip()
        text.insert("1.0", instructions)
        text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _create_scrollable_controls(
        self,
        parent: ttk.Widget,
        *,
        width: int = 350,
    ) -> ttk.Frame:
        """Create a vertically scrollable sidebar for tall control panels.

        The canvas keeps the embedded ttk.Frame at the current sidebar width,
        while the scrollbar and mouse wheel move the content vertically.
        """
        canvas = tk.Canvas(
            parent,
            width=width,
            highlightthickness=0,
            borderwidth=0,
        )
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        content = ttk.Frame(canvas, padding=12)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def update_scroll_region(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_content_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=max(1, event.width))

        content.bind("<Configure>", update_scroll_region, add="+")
        canvas.bind("<Configure>", fit_content_width, add="+")
        self._scroll_canvases.append(canvas)

        # Bind once per root. The handler determines which sidebar is under
        # the mouse pointer, so wheel scrolling also works over entries/buttons.
        if len(self._scroll_canvases) == 1:
            self.bind_all("<MouseWheel>", self._on_sidebar_mousewheel, add="+")
            self.bind_all("<Button-4>", self._on_sidebar_mousewheel, add="+")
            self.bind_all("<Button-5>", self._on_sidebar_mousewheel, add="+")

        return content

    def _on_sidebar_mousewheel(self, event: tk.Event) -> None:
        """Scroll only the sidebar currently under the mouse pointer."""
        x_root = int(getattr(event, "x_root", self.winfo_pointerx()))
        y_root = int(getattr(event, "y_root", self.winfo_pointery()))

        for canvas in self._scroll_canvases:
            if not canvas.winfo_exists() or not canvas.winfo_ismapped():
                continue
            left = canvas.winfo_rootx()
            top = canvas.winfo_rooty()
            right = left + canvas.winfo_width()
            bottom = top + canvas.winfo_height()
            if not (left <= x_root <= right and top <= y_root <= bottom):
                continue

            if getattr(event, "num", None) == 4:
                units = -3
            elif getattr(event, "num", None) == 5:
                units = 3
            else:
                delta = int(getattr(event, "delta", 0))
                if delta == 0:
                    return
                units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
                units *= 3
            canvas.yview_scroll(units, "units")
            return

    @staticmethod
    def _labeled_entry(parent: ttk.Widget, label: str, variable: tk.StringVar) -> ttk.Entry:
        ttk.Label(parent, text=label).pack(anchor="w")
        entry = ttk.Entry(parent, textvariable=variable)
        entry.pack(fill="x", pady=(2, 6))
        return entry

    @staticmethod
    def _labeled_combo(
        parent: ttk.Widget,
        label: str,
        variable: tk.StringVar,
        values: list[str],
    ) -> ttk.Combobox:
        ttk.Label(parent, text=label).pack(anchor="w")
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.pack(fill="x", pady=(2, 6))
        return combo

    def _run_background(
        self,
        function: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_finally: Callable[[], None] | None = None,
        *,
        on_error: Callable[[BaseException], None] | None = None,
        show_modal_error: bool = True,
        error_context: str = "background",
    ) -> None:
        def worker() -> None:
            try:
                result = function()
                self.ui_queue.put(("callback", (on_success, result)))
            except Exception as exc:
                info = describe_background_error(exc)
                log_method = self.logger.warning if info.transient else self.logger.error
                log_method(
                    "Background task failed context=%s transient=%s: %s",
                    error_context,
                    info.transient,
                    info.summary,
                )
                self.logger.debug("%s", traceback.format_exc())
                self.ui_queue.put(
                    (
                        "background_error",
                        (exc, on_error, show_modal_error, error_context),
                    )
                )
            finally:
                if on_finally is not None:
                    self.ui_queue.put(("finally", on_finally))

        threading.Thread(target=worker, daemon=True).start()

    def _process_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "callback":
                    callback, result = payload
                    callback(result)
                elif kind == "finally":
                    payload()
                elif kind == "background_error":
                    exc, on_error, show_modal_error, error_context = payload
                    info = describe_background_error(exc)
                    if on_error is not None:
                        try:
                            on_error(exc)
                        except Exception:
                            self.logger.error(
                                "Background error callback failed context=%s",
                                error_context,
                            )
                            self.logger.debug("%s", traceback.format_exc())
                    else:
                        self.bt_status.set("Операция завершилась ошибкой")
                        self.sb_status.set("Операция завершилась ошибкой")
                        self.diag_status.set("Операция завершилась ошибкой")
                    self._refresh_events()
                    if show_modal_error:
                        if info.transient:
                            now = time.monotonic()
                            last_notice = self._background_error_notice_at.get(
                                info.dedup_key,
                                0.0,
                            )
                            if now - last_notice >= 120.0:
                                self._background_error_notice_at[info.dedup_key] = now
                                request_note = (
                                    f"\nRequest ID: {info.request_id}"
                                    if info.request_id
                                    else ""
                                )
                                messagebox.showwarning(
                                    "T-Invest временно недоступен",
                                    f"{info.summary}.\n"
                                    "Новые заявки не отправляются без актуального "
                                    "снимка портфеля. Повторите операцию позже."
                                    f"{request_note}",
                                    parent=self,
                                )
                        else:
                            messagebox.showerror(
                                "Ошибка",
                                str(exc),
                                parent=self,
                            )
                elif kind == "sandbox_result":
                    self._show_sandbox_result(payload)
                elif kind == "sandbox_status":
                    self.sb_status.set(str(payload))
                elif kind == "sandbox_config_unlock":
                    self._set_sb_config_locked(False)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._process_ui_queue)

    def _append_log(self, line: str) -> None:
        if not hasattr(self, "log_text"):
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _open_project_folder(self) -> None:
        try:
            os.startfile(APP_DIR)  # type: ignore[attr-defined]
        except AttributeError:
            import subprocess

            subprocess.Popen(["xdg-open", str(APP_DIR)])
        except OSError as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self)

    def _on_close(self) -> None:
        snapshot = self.last_portfolio_snapshot or {}
        open_positions = [
            row
            for row in snapshot.get("positions", [])
            if int(row.get("quantity_lots") or 0) != 0
        ]
        if open_positions:
            summary = ", ".join(
                f"{row.get('ticker') or row.get('instrument_id')}: "
                f"{row.get('quantity_lots')} лот(а)"
                for row in open_positions[:5]
            )
            if not messagebox.askyesno(
                "Есть открытая Sandbox-позиция",
                "Последний снимок виртуального портфеля содержит открытые "
                f"позиции: {summary}.\n\nЗакрытие GUI не продаёт активы. "
                "Продолжить выход?",
                icon="warning",
                parent=self,
            ):
                return
        if self.robot_thread and self.robot_thread.is_alive():
            if not messagebox.askyesno(
                "Робот работает",
                "Остановить робота и закрыть приложение?",
                parent=self,
            ):
                return
            self.stop_event.set()
        if self._portfolio_auto_after_id:
            try:
                self.after_cancel(self._portfolio_auto_after_id)
            except tk.TclError:
                pass
            self._portfolio_auto_after_id = None
        if self._risk_dashboard_poll_after_id:
            try:
                self.after_cancel(self._risk_dashboard_poll_after_id)
            except tk.TclError:
                pass
            self._risk_dashboard_poll_after_id = None
        self.destroy()


def main() -> None:
    instance_lock = InterProcessFileLock(GUI_LOCK_PATH, timeout_seconds=0.0)
    try:
        instance_lock.acquire()
    except LockUnavailableError:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "MOEX Research Robot уже запущен",
            "Обнаружен другой экземпляр GUI. Используйте уже открытое окно "
            "или завершите его перед повторным запуском.",
            parent=root,
        )
        root.destroy()
        return

    try:
        bootstrap_report = bootstrap_runtime(RUNTIME_DIR)
        app = TradingRobotGUI(bootstrap_report=bootstrap_report)
        app.mainloop()
    finally:
        instance_lock.release()


if __name__ == "__main__":
    main()
