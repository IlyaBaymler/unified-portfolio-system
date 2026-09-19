from __future__ import annotations

import ast
import ctypes
import inspect
import json
import subprocess
import threading
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values

import desktop_gui
from tools import v3_10_q7_prepare_runtime as q7
from tools import v3_10_runtime_cash_cutover as cutover
from trading_robot import broker_read_adapters as cl3
from trading_robot import cash_availability as cl5
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot import runtime_cash_authority as cl7
from trading_robot import secret_provider as secret_provider_module
from trading_robot.bot import BotConfig
from trading_robot.broker_read_adapters import BrokerReadReason
from trading_robot.cash_ledger_domain import Money
from trading_robot.config_persistence import bot_config_to_profile
from trading_robot.gui_runtime_controller import (
    CL4MoneyNormalizingTransport,
    GuiRuntimeController,
    normalize_cl4_portfolio_response,
)
from trading_robot.instrument_runtime import (
    InstrumentRuntimeStateError,
    InstrumentRuntimeStore,
)
from trading_robot.multi_instrument_config import (
    MultiInstrumentProfile,
    MultiInstrumentProfileStore,
)
from trading_robot.portfolio_model import PortfolioState
from trading_robot.reporting_risk_cash_context import RiskCashContextReason
from trading_robot.runtime_cash_authority import (
    CL7RuntimeError,
    CL7RuntimeReason,
    RuntimeCashAuthorityState,
)
from trading_robot.secret_provider import (
    Q7_IDENTITY_CONFIRMATION,
    Q7SecretError,
    provision_q7_identity,
    resolve_q7_protected_secrets,
)
from trading_robot.tbank_sandbox import TBankSandboxClient

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / "current"
FIXTURE = CURRENT / "tests" / "fixtures" / "v3_10_q7_preparation_vectors.json"
VECTORS = json.loads(FIXTURE.read_text(encoding="utf-8"))
ACCOUNT = VECTORS["synthetic_secrets"]["TBANK_SANDBOX_ACCOUNT_ID"]
COMMIT = VECTORS["candidate"]["commit"]
TREE = VECTORS["candidate"]["tree"]


def _withdraw_limits_transport_observation(
    response: dict[str, object],
    *,
    account_id: str,
) -> cl5.WithdrawLimitsTransportObservation:
    class StaticTransport:
        @staticmethod
        def _post(
            service: str,
            method: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            assert service == "SandboxService"
            assert method == "GetSandboxWithdrawLimits"
            assert payload == {"accountId": account_id}
            return response

    return TBankSandboxClient.get_withdraw_limits(StaticTransport(), account_id)


def _synthetic_source_verifier(commit: str, tree: str) -> None:
    assert commit == COMMIT
    assert tree == TREE


REAL_SOURCE_VERIFIER = q7._verify_source_candidate


@pytest.fixture(autouse=True)
def _use_synthetic_source_custody(monkeypatch):
    monkeypatch.setattr(q7, "_verify_source_candidate", _synthetic_source_verifier)


class FakeSecretProvider:
    name = "Synthetic Credential Manager"
    secure = True

    def __init__(
        self,
        values=None,
        *,
        fail_set_at=None,
        mismatch_key=None,
        fail_delete=False,
    ) -> None:
        self.values = dict(values or {})
        self.writes: list[tuple[str, str]] = []
        self.deletes: list[str] = []
        self.fail_set_at = fail_set_at
        self.mismatch_key = mismatch_key
        self.fail_delete = fail_delete

    def get(self, key: str):
        value = self.values.get(key)
        if key == self.mismatch_key and value is not None:
            return value + "0"
        return value

    def set(self, key: str, value: str) -> None:
        if self.fail_set_at == len(self.writes) + 1:
            raise OSError("synthetic write failure")
        self.values[key] = value
        self.writes.append((key, value))

    def delete(self, key: str) -> None:
        if self.fail_delete:
            raise OSError("synthetic delete failure")
        self.values.pop(key, None)
        self.deletes.append(key)


class FailedMutex:
    def __enter__(self):
        raise Q7SecretError("IDENTITY_PROVISIONING_LOCK_FAILED")

    def __exit__(self, *_args):
        return False


def _provider(**overrides) -> FakeSecretProvider:
    values = dict(VECTORS["synthetic_secrets"])
    values.update(overrides)
    return FakeSecretProvider(values)


def _profile(ticker: str, interval: str) -> MultiInstrumentProfile:
    config = BotConfig(
        ticker=ticker,
        class_code="TQBR",
        candle_interval=interval,
        primary_strategy="sma",
        shadow_strategies=(),
        fast_window=2,
        slow_window=5,
        volatility_window=5,
        lookback_days=5,
        max_order_lots=1,
    )
    return MultiInstrumentProfile(
        instrument_id=f"q7r-{ticker.lower()}",
        strategy_profile=bot_config_to_profile(
            config,
            connect_timeout_seconds=8,
            read_timeout_seconds=25,
        ),
        scheduler_cadence_seconds=1,
        decision_cadence_seconds=1,
        risk_refresh_cadence_seconds=1,
        reconciliation_cadence_seconds=1,
        market_status_cadence_seconds=1,
    )


def _profiles(count: int = 2) -> tuple[MultiInstrumentProfile, ...]:
    return (
        _profile("SBER", "CANDLE_INTERVAL_HOUR"),
        _profile("LKOH", "CANDLE_INTERVAL_30_MIN"),
        _profile("YDEX", "CANDLE_INTERVAL_15_MIN"),
    )[:count]


def _save_profiles(root: Path, count: int = 2) -> None:
    MultiInstrumentProfileStore(root / "multi_instrument_profiles.json").save_mode(
        "SANDBOX_EXECUTION", _profiles(count)
    )


def _materialize(root: Path, provider=None):
    _save_profiles(root)
    evidence = root.parent / f"{root.name}-stage-a.json"
    backup = root.parent / f"{root.name}-b0.zip"
    result = q7.materialize_stage_a(
        runtime_dir=root,
        output_record=evidence,
        backup_output=backup,
        candidate_commit=COMMIT,
        candidate_tree=TREE,
        provider=provider or _provider(),
        generated_at="2026-09-13T00:00:00+00:00",
    )
    return result, evidence, backup


def test_q7r_01_03_shipped_composition_is_once_and_cached(monkeypatch):
    source = (CURRENT / "desktop_gui.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    compose_helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_compose_production_gui_runtime"
    )
    calls = [
        node
        for node in ast.walk(compose_helper)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compose"
    ]
    assert len(calls) == 1
    assert "SandboxTradingBot" not in source
    cached = GuiRuntimeController.blocked("SYNTHETIC")
    monkeypatch.setattr(desktop_gui, "_PRODUCTION_COMPOSITION", (Path.cwd(), cached))
    assert desktop_gui._compose_production_gui_runtime(Path.cwd()) is cached
    assert desktop_gui._compose_production_gui_runtime(Path.cwd()) is cached


def test_q7r_01_03_production_composition_success_missing_and_duplication(
    tmp_path, monkeypatch
):
    runtime = tmp_path / "runtime"
    provider = _provider()
    _materialize(runtime, provider)
    transports: list[object] = []

    class Transport:
        def __init__(self, *, token, max_retries):
            assert token == VECTORS["synthetic_secrets"]["TBANK_SANDBOX_TOKEN"]
            assert max_retries == 0
            transports.append(self)

        def get_candles(self, *_args, **_kwargs):
            raise AssertionError("composition must not perform provider reads")

    monkeypatch.setattr(desktop_gui, "_PRODUCTION_COMPOSITION", None)
    first = desktop_gui._compose_production_gui_runtime(
        runtime,
        secret_provider=provider,
        transport_factory=Transport,
    )
    second = desktop_gui._compose_production_gui_runtime(
        runtime,
        secret_provider=provider,
        transport_factory=Transport,
    )
    assert first is second
    assert first.service_ready is True
    assert len(transports) == 1
    assert isinstance(
        first.execution_adapter.transport,
        CL4MoneyNormalizingTransport,
    )

    monkeypatch.setattr(desktop_gui, "_PRODUCTION_COMPOSITION", None)
    incomplete = _provider(V310_CL_IDENTITY_KEY_HEX="")
    with pytest.raises(Q7SecretError, match="IDENTITY_KEY_INVALID"):
        desktop_gui._compose_production_gui_runtime(
            runtime,
            secret_provider=incomplete,
            transport_factory=Transport,
        )
    assert len(transports) == 1


def test_q7r_04_confirmed_provisioning_is_metadata_only_and_idempotent():
    provider = FakeSecretProvider()
    first = provision_q7_identity(
        identity_key_id="Q7R_SYNTHETIC_V1",
        confirmation=Q7_IDENTITY_CONFIRMATION,
        provider=provider,
        random_bytes=lambda count: b"\x42" * count,
        mutex_factory=threading.Lock,
    )
    second = provision_q7_identity(
        identity_key_id="Q7R_SYNTHETIC_V1",
        confirmation=Q7_IDENTITY_CONFIRMATION,
        provider=provider,
        random_bytes=lambda count: b"\xff" * count,
        mutex_factory=threading.Lock,
    )
    assert first.status == "PROVISIONED"
    assert second.status == "ALREADY_PROVISIONED"
    assert len(provider.writes) == 2
    serialized = json.dumps(first.to_dict(), sort_keys=True)
    assert "42" * 32 not in serialized


def test_q7r_04_credential_manager_distinguishes_absence_from_read_failure():
    class Credential(ctypes.Structure):
        _fields_ = [("unused", ctypes.c_int)]

    class Api:
        @staticmethod
        def CredReadW(*_args):
            ctypes.set_last_error(5)
            return 0

    provider = object.__new__(secret_provider_module.WindowsCredentialManagerProvider)
    provider.namespace = "synthetic"
    provider._ctypes = ctypes
    provider._credential_type = Credential
    provider._advapi = Api()
    with pytest.raises(OSError):
        provider.get("V310_CL_IDENTITY_KEY_HEX")

    provider._advapi.CredReadW = lambda *_args: ctypes.set_last_error(1168) or 0
    assert provider.get("V310_CL_IDENTITY_KEY_HEX") is None


def test_q7r_04_credential_manager_preserves_present_empty_record():
    class Credential(ctypes.Structure):
        _fields_ = [
            ("CredentialBlobSize", ctypes.c_ulong),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    credential = Credential(0, ctypes.POINTER(ctypes.c_ubyte)())

    class Api:
        @staticmethod
        def CredReadW(_target, _kind, _flags, destination):
            typed = ctypes.cast(destination, ctypes.POINTER(ctypes.POINTER(Credential)))
            typed[0] = ctypes.pointer(credential)
            return 1

        @staticmethod
        def CredFree(_pointer):
            return None

    provider = object.__new__(secret_provider_module.WindowsCredentialManagerProvider)
    provider.namespace = "synthetic"
    provider._ctypes = ctypes
    provider._credential_type = Credential
    provider._advapi = Api()
    assert provider.get("V310_CL_IDENTITY_KEY_HEX") == ""


def test_q7r_04_present_empty_identity_custody_is_never_overwritten():
    provider = FakeSecretProvider(
        {
            "V310_CL_IDENTITY_KEY_HEX": "",
            "V310_CL_IDENTITY_KEY_ID": "Q7R_SYNTHETIC_V1",
        }
    )
    with pytest.raises(Q7SecretError, match="IDENTITY_KEY_INVALID"):
        provision_q7_identity(
            identity_key_id="Q7R_SYNTHETIC_V1",
            confirmation=Q7_IDENTITY_CONFIRMATION,
            provider=provider,
            mutex_factory=threading.Lock,
        )
    assert provider.writes == []


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({"V310_CL_IDENTITY_KEY_HEX": "11" * 32}, "IDENTITY_CUSTODY_PARTIAL"),
        (
            {
                "V310_CL_IDENTITY_KEY_HEX": "not-hex",
                "V310_CL_IDENTITY_KEY_ID": "Q7R_SYNTHETIC_V1",
            },
            "IDENTITY_KEY_INVALID",
        ),
        (
            {
                "V310_CL_IDENTITY_KEY_HEX": "11" * 32,
                "V310_CL_IDENTITY_KEY_ID": "OTHER_ID",
            },
            "IDENTITY_KEY_MISMATCH",
        ),
    ],
)
def test_q7r_04_08_existing_custody_never_overwritten(values, reason):
    provider = FakeSecretProvider(values)
    with pytest.raises(Q7SecretError, match=reason):
        provision_q7_identity(
            identity_key_id="Q7R_SYNTHETIC_V1",
            confirmation=Q7_IDENTITY_CONFIRMATION,
            provider=provider,
            mutex_factory=threading.Lock,
        )
    assert provider.writes == []
    assert provider.deletes == []


def test_q7r_04_lock_failure_and_second_write_compensation():
    locked = FakeSecretProvider()
    with pytest.raises(Q7SecretError, match="IDENTITY_PROVISIONING_LOCK_FAILED"):
        provision_q7_identity(
            identity_key_id="Q7R_SYNTHETIC_V1",
            confirmation=Q7_IDENTITY_CONFIRMATION,
            provider=locked,
            mutex_factory=FailedMutex,
        )
    assert locked.writes == []

    failing = FakeSecretProvider(fail_set_at=2)
    with pytest.raises(Q7SecretError, match="IDENTITY_PROVISIONING_FAILED"):
        provision_q7_identity(
            identity_key_id="Q7R_SYNTHETIC_V1",
            confirmation=Q7_IDENTITY_CONFIRMATION,
            provider=failing,
            random_bytes=lambda count: b"\x44" * count,
            mutex_factory=threading.Lock,
        )
    assert failing.values == {}


@pytest.mark.parametrize(
    ("mismatch_key", "write_count"),
    [
        ("V310_CL_IDENTITY_KEY_HEX", 1),
        ("V310_CL_IDENTITY_KEY_ID", 2),
    ],
)
def test_q7r_04_readback_mismatch_requires_manual_recovery(mismatch_key, write_count):
    provider = FakeSecretProvider(mismatch_key=mismatch_key)
    with pytest.raises(Q7SecretError, match="IDENTITY_CUSTODY_PARTIAL"):
        provision_q7_identity(
            identity_key_id="Q7R_SYNTHETIC_V1",
            confirmation=Q7_IDENTITY_CONFIRMATION,
            provider=provider,
            random_bytes=lambda count: b"\x33" * count,
            mutex_factory=threading.Lock,
        )
    assert len(provider.writes) == write_count


def test_q7r_04_failed_compensation_requires_manual_recovery():
    provider = FakeSecretProvider(fail_set_at=2, fail_delete=True)
    with pytest.raises(Q7SecretError, match="IDENTITY_CUSTODY_PARTIAL"):
        provision_q7_identity(
            identity_key_id="Q7R_SYNTHETIC_V1",
            confirmation=Q7_IDENTITY_CONFIRMATION,
            provider=provider,
            random_bytes=lambda count: b"\x22" * count,
            mutex_factory=threading.Lock,
        )
    assert provider.values["V310_CL_IDENTITY_KEY_HEX"] == "22" * 32


def test_q7r_04_concurrent_writers_create_one_pair():
    provider = FakeSecretProvider()
    gate = threading.Lock()
    results: list[str] = []

    def run() -> None:
        result = provision_q7_identity(
            identity_key_id="Q7R_SYNTHETIC_V1",
            confirmation=Q7_IDENTITY_CONFIRMATION,
            provider=provider,
            random_bytes=lambda count: b"\x55" * count,
            mutex_factory=lambda: gate,
        )
        results.append(result.status)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["ALREADY_PROVISIONED", "PROVISIONED"]
    assert len(provider.writes) == 2


@pytest.mark.parametrize(
    ("missing", "reason"),
    [
        ("V310_CL_IDENTITY_KEY_HEX", "IDENTITY_CUSTODY_PARTIAL"),
        ("TBANK_SANDBOX_TOKEN", "SANDBOX_TOKEN_REQUIRED"),
        ("TBANK_SANDBOX_ACCOUNT_ID", "SANDBOX_ACCOUNT_REQUIRED"),
    ],
)
def test_q7r_05_07_resolver_fails_closed_without_writes(missing, reason):
    values = dict(VECTORS["synthetic_secrets"])
    values.pop(missing)
    provider = FakeSecretProvider(values)
    with pytest.raises(Q7SecretError, match=reason):
        resolve_q7_protected_secrets(provider=provider)
    assert provider.writes == []


def test_q7r_07_identity_id_mismatch_is_explicit():
    provider = _provider()
    with pytest.raises(Q7SecretError, match="IDENTITY_KEY_MISMATCH"):
        resolve_q7_protected_secrets(
            provider=provider,
            expected_identity_key_id="OTHER_ID",
        )
    assert provider.writes == []


def test_blocked_controller_renders_selected_account_scope_without_raw_id():
    class Variable:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    blocked = GuiRuntimeController.blocked("GUI_RUNTIME_COMPOSITION_REQUIRED")
    assert blocked.account_id == ""
    assert blocked.account_scope_sha256 == ""

    host = SimpleNamespace(
        gui_runtime_controller=blocked,
        account_records={},
        account_combo={},
        sb_account=Variable(),
        sb_account_id_display=Variable(),
        _preferred_account_id="",
    )
    host._selected_account_id = lambda optional=False: (
        desktop_gui.TradingRobotGUI._selected_account_id(host, optional)
    )
    host._account_scope_display = lambda account_id: (
        desktop_gui.TradingRobotGUI._account_scope_display(host, account_id)
    )
    host._set_account_id_display = lambda account_id=None: (
        desktop_gui.TradingRobotGUI._set_account_id_display(host, account_id)
    )

    desktop_gui.TradingRobotGUI._set_account_records(
        host,
        [{"id": ACCOUNT, "name": "Synthetic Sandbox"}],
    )

    rendered = host.sb_account_id_display.get()
    assert host.sb_account.get() == "Synthetic Sandbox — account 1"
    assert rendered == sha256(ACCOUNT.encode("utf-8")).hexdigest()
    assert ACCOUNT not in host.sb_account.get()
    assert ACCOUNT not in rendered


def test_multi_account_selection_is_explicit_and_provider_order_independent(
    tmp_path, monkeypatch
):
    class Variable:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    account_a = "synthetic-account-a"
    account_b = "synthetic-account-b"
    blocked = GuiRuntimeController.blocked("GUI_RUNTIME_COMPOSITION_REQUIRED")
    host = SimpleNamespace(
        gui_runtime_controller=blocked,
        account_records={},
        account_combo={},
        sb_account=Variable(),
        sb_account_id_display=Variable(),
        _preferred_account_id="",
    )
    host._selected_account_id = lambda optional=False: (
        desktop_gui.TradingRobotGUI._selected_account_id(host, optional)
    )
    host._account_scope_display = lambda account_id: (
        desktop_gui.TradingRobotGUI._account_scope_display(host, account_id)
    )
    host._set_account_id_display = lambda account_id=None: (
        desktop_gui.TradingRobotGUI._set_account_id_display(host, account_id)
    )

    ordered = [
        {"id": account_a, "name": "Sandbox A"},
        {"id": account_b, "name": "Sandbox B"},
    ]
    desktop_gui.TradingRobotGUI._set_account_records(host, ordered)
    assert host.sb_account.get() == ""
    assert host.sb_account_id_display.get() == "—"
    assert host._preferred_account_id == ""

    desktop_gui.TradingRobotGUI._set_account_records(host, list(reversed(ordered)))
    assert host.sb_account.get() == ""
    assert host.sb_account_id_display.get() == "—"
    assert host._preferred_account_id == ""

    provider = FakeSecretProvider()
    errors: list[tuple[str, str]] = []
    host.secret_provider = provider
    host.sb_token = Variable("synthetic-token")
    host.sb_ca_bundle = Variable("")
    host.sb_initial_rub = Variable("1000000")
    host.sb_connect_timeout = Variable("8")
    host.sb_read_timeout = Variable("25")
    host.sb_status = Variable()
    host.robot_thread = None
    host._connection_restart_required = False
    host._read_client_settings = lambda: (8.0, 25.0)
    host._persist_connection_credentials = lambda token, account_id: (
        desktop_gui.TradingRobotGUI._persist_connection_credentials(
            host,
            token,
            account_id,
        )
    )
    host.logger = SimpleNamespace(
        info=lambda *_args: None,
        exception=lambda *_args: None,
    )
    host._refresh_readiness = lambda: None
    monkeypatch.setattr(desktop_gui, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(
        desktop_gui.messagebox,
        "showinfo",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        desktop_gui.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, message)),
    )

    desktop_gui.TradingRobotGUI._save_settings_to_env(host)

    assert errors and errors[0][0] == "Нет счёта"
    assert provider.writes == []
    assert host._connection_restart_required is False

    host._preferred_account_id = "missing-protected-account"
    desktop_gui.TradingRobotGUI._set_account_records(host, ordered)
    assert host.sb_account.get() == ""
    assert host._preferred_account_id == "missing-protected-account"

    host._preferred_account_id = ""
    selected_label = next(
        label
        for label, account in host.account_records.items()
        if account["id"] == account_a
    )
    host.sb_account.set(selected_label)
    host._set_account_id_display()
    assert host._preferred_account_id == account_a

    desktop_gui.TradingRobotGUI._set_account_records(host, list(reversed(ordered)))
    assert host._selected_account_id() == account_a

    errors.clear()
    desktop_gui.TradingRobotGUI._save_settings_to_env(host)

    assert errors == []
    assert provider.get("TBANK_SANDBOX_ACCOUNT_ID") == account_a


def test_secure_account_load_keeps_raw_id_out_of_visible_combobox(
    tmp_path, monkeypatch
):
    class Variable:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    provider = _provider()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TBANK_SANDBOX_TOKEN=legacy-token\n"
        f"TBANK_SANDBOX_ACCOUNT_ID={ACCOUNT}-legacy\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(desktop_gui, "ENV_PATH", env_path)
    host = SimpleNamespace(
        secret_provider=provider,
        _preferred_account_id="",
        sb_token=Variable(),
        sb_account=Variable(),
        sb_ca_bundle=Variable(),
        sb_initial_rub=Variable(),
        sb_connect_timeout=Variable(),
        sb_read_timeout=Variable(),
        sb_ticker=Variable("SBER"),
        sb_class_code=Variable("TQBR"),
        diag_ticker=Variable(),
        diag_class_code=Variable(),
    )

    desktop_gui.TradingRobotGUI._load_settings_from_env(host, show_message=False)

    assert host._preferred_account_id == ACCOUNT
    assert host.sb_account.get() == ""
    assert ACCOUNT not in host.sb_account.get()
    env_text = env_path.read_text(encoding="utf-8")
    assert "TBANK_SANDBOX_TOKEN" not in env_text
    assert "TBANK_SANDBOX_ACCOUNT_ID" not in env_text


@pytest.mark.parametrize(
    "legacy_line",
    [
        f"export TBANK_SANDBOX_ACCOUNT_ID={ACCOUNT}\n",
        f"TBANK_SANDBOX_ACCOUNT_ID = {ACCOUNT}\n",
    ],
)
def test_secure_account_cleanup_handles_all_supported_dotenv_forms(
    tmp_path, legacy_line
):
    env_path = tmp_path / ".env"
    env_path.write_text(legacy_line, encoding="utf-8")
    assert dotenv_values(env_path)["TBANK_SANDBOX_ACCOUNT_ID"] == ACCOUNT

    desktop_gui._delete_dotenv_secret_exact(env_path, "TBANK_SANDBOX_ACCOUNT_ID")

    assert "TBANK_SANDBOX_ACCOUNT_ID" not in dotenv_values(env_path)
    assert ACCOUNT not in env_path.read_text(encoding="utf-8")


def test_secure_account_cleanup_requires_exact_absence(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"export TBANK_SANDBOX_ACCOUNT_ID={ACCOUNT}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(desktop_gui, "unset_key", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="LEGACY_ENV_SECRET_CLEANUP_FAILED"):
        desktop_gui._delete_dotenv_secret_exact(env_path, "TBANK_SANDBOX_ACCOUNT_ID")


def test_legacy_token_migration_requires_exact_protected_readback(
    tmp_path, monkeypatch
):
    class DroppedWriteProvider(FakeSecretProvider):
        def set(self, key: str, value: str) -> None:
            self.writes.append((key, value))

    provider = DroppedWriteProvider()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TBANK_SANDBOX_TOKEN=legacy-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(desktop_gui, "ENV_PATH", env_path)
    host = SimpleNamespace(secret_provider=provider)

    with pytest.raises(RuntimeError, match="PROTECTED_SANDBOX_TOKEN_READBACK_FAILED"):
        desktop_gui.TradingRobotGUI._load_settings_from_env(
            host,
            show_message=False,
        )

    assert provider.get("TBANK_SANDBOX_TOKEN") is None
    assert dotenv_values(env_path)["TBANK_SANDBOX_TOKEN"] == "legacy-token"


def test_secure_connection_save_is_rejected_while_runtime_is_active(monkeypatch):
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr(
        desktop_gui.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, message)),
    )
    host = SimpleNamespace(
        robot_thread=SimpleNamespace(is_alive=lambda: True),
    )

    desktop_gui.TradingRobotGUI._save_settings_to_env(host)

    assert errors == [
        (
            "Sandbox активен",
            (
                "Сначала остановите account-level Sandbox runtime, затем сохраните "
                "подключение и перезапустите приложение."
            ),
        )
    ]


def test_secure_connection_save_custodies_token_and_account_and_requires_restart(
    tmp_path, monkeypatch
):
    class Variable:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    provider = FakeSecretProvider()
    messages: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    env_path = tmp_path / ".env"
    monkeypatch.setattr(desktop_gui, "ENV_PATH", env_path)
    monkeypatch.setattr(
        desktop_gui.messagebox,
        "showinfo",
        lambda title, message, **_kwargs: messages.append((title, message)),
    )
    monkeypatch.setattr(
        desktop_gui.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, message)),
    )
    host = SimpleNamespace(
        secret_provider=provider,
        sb_token=Variable("synthetic-token"),
        sb_ca_bundle=Variable(""),
        sb_initial_rub=Variable("1000000"),
        sb_connect_timeout=Variable("8"),
        sb_read_timeout=Variable("25"),
        sb_status=Variable(),
        _connection_restart_required=False,
        robot_thread=None,
        gui_runtime_controller=SimpleNamespace(service_ready=True),
        _selected_account_id=lambda optional=False: ACCOUNT,
        _read_client_settings=lambda: (8.0, 25.0),
        logger=SimpleNamespace(
            info=lambda *_args: None,
            exception=lambda *_args: None,
        ),
        _refresh_readiness=lambda: None,
        _confirm_execution=lambda: pytest.fail(
            "stale composed controller reached execution confirmation"
        ),
    )
    host._persist_connection_credentials = lambda token, account_id: (
        desktop_gui.TradingRobotGUI._persist_connection_credentials(
            host, token, account_id
        )
    )

    desktop_gui.TradingRobotGUI._save_settings_to_env(host)

    assert errors == []
    assert provider.values["TBANK_SANDBOX_TOKEN"] == "synthetic-token"
    assert provider.values["TBANK_SANDBOX_ACCOUNT_ID"] == ACCOUNT
    assert provider.get("TBANK_SANDBOX_TOKEN") == "synthetic-token"
    assert provider.get("TBANK_SANDBOX_ACCOUNT_ID") == ACCOUNT
    env_text = env_path.read_text(encoding="utf-8")
    assert "TBANK_SANDBOX_TOKEN" not in env_text
    assert "TBANK_SANDBOX_ACCOUNT_ID" not in env_text
    assert ACCOUNT not in env_text
    assert host._connection_restart_required is True
    assert "перезапустите приложение" in host.sb_status.get().lower()
    assert len(messages) == 1
    assert "production runtime должен быть собран заново" in messages[0][1]

    desktop_gui.TradingRobotGUI._start_robot_loop(host, execute=True)

    assert host.sb_status.get().startswith("BLOCKED:")


def test_secure_connection_write_readback_failure_restores_previous_pair():
    class MismatchAfterAccountWrite(FakeSecretProvider):
        mismatch_enabled = False

        def get(self, key: str):
            value = super().get(key)
            if (
                self.mismatch_enabled
                and key == "TBANK_SANDBOX_ACCOUNT_ID"
                and value is not None
            ):
                return value + "-mismatch"
            return value

        def set(self, key: str, value: str) -> None:
            super().set(key, value)
            if key == "TBANK_SANDBOX_ACCOUNT_ID" and value == ACCOUNT:
                self.mismatch_enabled = True

    provider = MismatchAfterAccountWrite(
        {
            "TBANK_SANDBOX_TOKEN": "old-token",
            "TBANK_SANDBOX_ACCOUNT_ID": "old-account",
        }
    )
    host = SimpleNamespace(secret_provider=provider)

    with pytest.raises(RuntimeError, match="PROTECTED_CONNECTION_WRITE_FAILED"):
        desktop_gui.TradingRobotGUI._persist_connection_credentials(
            host, "new-token", ACCOUNT
        )

    assert provider.values == {
        "TBANK_SANDBOX_TOKEN": "old-token",
        "TBANK_SANDBOX_ACCOUNT_ID": "old-account",
    }


def test_failed_credential_rollback_blocks_stale_composed_controller(monkeypatch):
    class Variable:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class PartialWriteRollbackFailure(FakeSecretProvider):
        def __init__(self) -> None:
            super().__init__(
                {
                    "TBANK_SANDBOX_TOKEN": "old-token",
                    "TBANK_SANDBOX_ACCOUNT_ID": "old-account",
                }
            )
            self.set_calls = 0

        def set(self, key: str, value: str) -> None:
            self.set_calls += 1
            if self.set_calls == 1:
                self.values[key] = value
                return
            raise OSError("synthetic credential write/rollback failure")

    provider = PartialWriteRollbackFailure()
    errors: list[tuple[str, str]] = []
    confirmation_reached: list[bool] = []
    monkeypatch.setattr(
        desktop_gui.messagebox,
        "showerror",
        lambda title, message, **_kwargs: errors.append((title, message)),
    )
    host = SimpleNamespace(
        secret_provider=provider,
        sb_token=Variable("new-token"),
        sb_status=Variable(),
        robot_thread=None,
        _connection_restart_required=False,
        _read_client_settings=lambda: (8.0, 25.0),
        _selected_account_id=lambda optional=False: "new-account",
        logger=SimpleNamespace(exception=lambda *_args: None),
        gui_runtime_controller=SimpleNamespace(service_ready=True),
        _confirm_execution=lambda: confirmation_reached.append(True) or False,
    )
    host._persist_connection_credentials = lambda token, account_id: (
        desktop_gui.TradingRobotGUI._persist_connection_credentials(
            host,
            token,
            account_id,
        )
    )

    desktop_gui.TradingRobotGUI._save_settings_to_env(host)

    assert provider.values == {
        "TBANK_SANDBOX_TOKEN": "new-token",
        "TBANK_SANDBOX_ACCOUNT_ID": "old-account",
    }
    assert host._connection_restart_required is True
    assert host.sb_status.get().startswith("BLOCKED:")
    assert errors and "перезапустите приложение" in errors[0][1]

    desktop_gui.TradingRobotGUI._start_robot_loop(host, execute=True)

    assert confirmation_reached == []
    assert host.sb_status.get().startswith("BLOCKED:")


def test_q7r_09_12_secret_boundary_and_tools_do_not_export_or_call_provider():
    q7_source = (CURRENT / "tools/v3_10_q7_prepare_runtime.py").read_text(
        encoding="utf-8"
    )
    cutover_source = (CURRENT / "tools/v3_10_runtime_cash_cutover.py").read_text(
        encoding="utf-8"
    )
    assert "resolve_q7_protected_secrets" in cutover_source
    assert "os.getenv" not in cutover_source
    assert "TBankSandboxClient" not in q7_source
    command_choices = set(q7._parser()._subparsers._group_actions[0].choices)
    assert command_choices == {
        "provision-identity",
        "materialize",
        "prepare-activation",
        "finalize",
    }
    assert command_choices.isdisjoint(
        {"prepare", "confirm", "activate", "arm", "dispatch"}
    )


def test_q7r_13_18_stage_a_materializes_two_instruments_and_verified_b0(tmp_path):
    runtime = tmp_path / "private-runtime"
    result, evidence, backup = _materialize(runtime)
    record = q7.verify_record_bytes(
        evidence.read_bytes(),
        expected_domain="v3.10-cl8-q7-offline-materialization",
    )
    assert result["status"] == "ACTIVATION_REQUIRED"
    assert record["configured_instrument_count"] == 2
    assert record["authority_state"] == "LEGACY_ACTIVE"
    assert record["provider_calls_performed"] is False
    assert record["provider_mutations_performed"] is False
    assert record["b0_verification_status"] == "VERIFIED"
    assert record["b0_backup_sha256"] == q7._sha256_file(backup)
    raw = backup.read_bytes()
    for secret in VECTORS["synthetic_secrets"].values():
        assert secret.encode() not in raw


def test_q7r_13_requires_two_or_three_instruments(tmp_path):
    root = tmp_path / "runtime"
    _save_profiles(root, 1)
    with pytest.raises(
        q7.Q7PreparationError, match="CONFIGURED_INSTRUMENT_COUNT_INVALID"
    ):
        q7.materialize_stage_a(
            runtime_dir=root,
            output_record=tmp_path / "record.json",
            backup_output=tmp_path / "b0.zip",
            candidate_commit=COMMIT,
            candidate_tree=TREE,
            provider=_provider(),
        )


def test_q7r_13_source_candidate_must_be_exact_and_clean(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "q7@example.invalid")
    git("config", "user.name", "Q7 Synthetic")
    (repository / "candidate.txt").write_text("exact\n", encoding="utf-8")
    git("add", "candidate.txt")
    git("commit", "-q", "-m", "candidate")
    commit = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    REAL_SOURCE_VERIFIER(commit, tree, repository=repository)
    with pytest.raises(q7.Q7PreparationError, match="CANDIDATE_COMMIT_MISMATCH"):
        REAL_SOURCE_VERIFIER("a" * 40, tree, repository=repository)
    (repository / "candidate.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(q7.Q7PreparationError, match="SOURCE_TREE_DIRTY"):
        REAL_SOURCE_VERIFIER(commit, tree, repository=repository)


def test_q7r_13_profile_runtime_account_and_orphan_checks(tmp_path):
    root = tmp_path / "runtime"
    _save_profiles(root, 2)
    store = MultiInstrumentProfileStore(root / "multi_instrument_profiles.json")
    runtime_store = InstrumentRuntimeStore(root / "instrument_runtimes.json")
    store.bootstrap_runtime_registry(
        mode="SANDBOX_EXECUTION", account_id=ACCOUNT, runtime_store=runtime_store
    )
    with pytest.raises(InstrumentRuntimeStateError):
        q7._configured_set(
            root,
            account_id="different-account",
            account_scope_sha256="a" * 64,
            bootstrap_missing=False,
        )


def test_q7r_16_17_legacy_state_never_transitions_during_stage_a(tmp_path):
    runtime = tmp_path / "runtime"
    _result, evidence, _backup = _materialize(runtime)
    record = q7.verify_record_bytes(evidence.read_bytes())
    assert record["authority_state"] == RuntimeCashAuthorityState.LEGACY_ACTIVE.value
    assert record["authority_revision"] == 0
    assert record["overall_status"] == "ACTIVATION_REQUIRED"


def test_q7r_16_stage_a_rejects_nonlegacy_or_previously_activated_authority(
    tmp_path, monkeypatch
):
    invalid = SimpleNamespace(
        state=RuntimeCashAuthorityState.EXACT_CASH_DISARMED,
        record_revision=4,
        ever_exact_activated=True,
        identity_key_id=None,
        account_scope_sha256=None,
        sha256="a" * 64,
    )
    monkeypatch.setattr(
        q7,
        "RuntimeCashAuthorityStore",
        lambda _root: SimpleNamespace(bootstrap=lambda **_kwargs: invalid),
    )
    root = tmp_path / "runtime"
    _save_profiles(root)
    with pytest.raises(q7.Q7PreparationError, match="STAGE_A_AUTHORITY_STATE_INVALID"):
        q7.materialize_stage_a(
            runtime_dir=root,
            output_record=tmp_path / "stage-a.json",
            backup_output=tmp_path / "b0.zip",
            candidate_commit=COMMIT,
            candidate_tree=TREE,
            provider=_provider(),
        )
    assert not (tmp_path / "b0.zip").exists()


def test_q7r_19_23_activation_preparation_is_separate_and_exact(tmp_path):
    runtime = tmp_path / "runtime"
    _result, stage_a, backup = _materialize(runtime)
    output = tmp_path / "activation.json"
    result = q7.prepare_activation(
        runtime_dir=runtime,
        stage_a_record=stage_a,
        b0_backup=backup,
        output_record=output,
        candidate_commit=COMMIT,
        candidate_tree=TREE,
        provider=_provider(),
        generated_at="2026-09-13T00:01:00+00:00",
    )
    record = q7.verify_record_bytes(output.read_bytes())
    assert result["experiment_id"] == q7.ACTIVATION_EXPERIMENT_ID
    assert result["burnin_authorized"] is False
    assert record["provider_calls_before_authorization"] == 0
    assert record["provider_mutations_before_authorization"] == 0
    assert [item["confirmation"] for item in record["planned_commands"]] == [
        "PREPARE V3.10 CL7 EXACT CASH CUTOVER",
        "CONFIRM V3.10 CL7 EXACT CASH CUTOVER",
        "ACTIVATE V3.10 CL7 EXACT CASH AUTHORITY",
        "ARM V3.10 CL7 SANDBOX EXACT CASH EXECUTION",
    ]
    assert (
        record["stage_a_record_sha256"]
        == q7.verify_record_bytes(stage_a.read_bytes())["record_sha256"]
    )
    assert record["stage_a_canonical_summary_sha256"] == q7._sha256_file(stage_a)
    substituted = dict(record)
    substituted.pop("record_sha256")
    substituted["ignored_extra_field"] = True
    with pytest.raises(q7.Q7PreparationError, match="EVIDENCE_SCHEMA_INVALID"):
        q7.verify_record_bytes(q7.build_record(substituted))


def test_q7r_23_known_evidence_domains_use_closed_schemas():
    minimal = q7.build_record(
        {
            "version": 1,
            "domain": "v3.10-cl8-q7-cl7-activation-preparation",
            "candidate_commit": COMMIT,
            "candidate_tree": TREE,
            "runtime_instance_id": "1" * 64,
            "configured_set_sha256": "2" * 64,
        }
    )
    with pytest.raises(q7.Q7PreparationError, match="EVIDENCE_SCHEMA_INVALID"):
        q7.verify_record_bytes(minimal)


def test_q7r_20_22_activation_authorization_never_implies_burnin(tmp_path):
    runtime = tmp_path / "runtime"
    _result, stage_a, backup = _materialize(runtime)
    output = tmp_path / "activation.json"
    result = q7.prepare_activation(
        runtime_dir=runtime,
        stage_a_record=stage_a,
        b0_backup=backup,
        output_record=output,
        candidate_commit=COMMIT,
        candidate_tree=TREE,
        provider=_provider(),
    )
    serialized = output.read_text(encoding="utf-8")
    assert "dispatch" not in serialized.lower()
    assert q7.BURNIN_EXPERIMENT_ID not in serialized
    assert result["provider_calls_before_authorization"] == 0


def test_q7r_18_30_b0_and_stage_a_substitution_fail_closed(tmp_path):
    runtime = tmp_path / "runtime"
    _result, stage_a, backup = _materialize(runtime)
    tampered_backup = tmp_path / "tampered-b0.zip"
    tampered_backup.write_bytes(backup.read_bytes() + b"tamper")
    with pytest.raises(
        q7.Q7PreparationError,
        match="BACKUP_VERIFICATION_FAILED|B0_BACKUP_SUBSTITUTION",
    ):
        q7.prepare_activation(
            runtime_dir=runtime,
            stage_a_record=stage_a,
            b0_backup=tampered_backup,
            output_record=tmp_path / "activation-a.json",
            candidate_commit=COMMIT,
            candidate_tree=TREE,
            provider=_provider(),
        )

    substituted = json.loads(stage_a.read_bytes())
    substituted.pop("record_sha256")
    substituted["candidate_commit"] = "c" * 40
    substituted_path = tmp_path / "substituted-stage-a.json"
    substituted_path.write_bytes(q7.build_record(substituted))
    with pytest.raises(q7.Q7PreparationError, match="CANDIDATE_SUBSTITUTION"):
        q7.prepare_activation(
            runtime_dir=runtime,
            stage_a_record=substituted_path,
            b0_backup=backup,
            output_record=tmp_path / "activation-b.json",
            candidate_commit=COMMIT,
            candidate_tree=TREE,
            provider=_provider(),
        )


def test_q7r_24_29_finalization_rejects_unarmed_runtime(tmp_path):
    runtime = tmp_path / "runtime"
    _result, stage_a, backup = _materialize(runtime)
    activation = tmp_path / "activation.json"
    q7.prepare_activation(
        runtime_dir=runtime,
        stage_a_record=stage_a,
        b0_backup=backup,
        output_record=activation,
        candidate_commit=COMMIT,
        candidate_tree=TREE,
        provider=_provider(),
    )
    with pytest.raises(
        q7.Q7PreparationError, match="EXACT_CASH_ARMED_PREDICATE_FAILED"
    ):
        q7.finalize_preparation(
            runtime_dir=runtime,
            activation_record=activation,
            output_record=tmp_path / "final.json",
            backup_output=tmp_path / "b1.zip",
            candidate_commit=COMMIT,
            candidate_tree=TREE,
            q4_artifact_identity_sha256="1" * 64,
            q5_privacy_summary_sha256="2" * 64,
            provider=_provider(),
        )
    assert not (tmp_path / "b1.zip").exists()


def test_q7r_24_29_exact_armed_state_creates_b1_binding_without_burnin(
    tmp_path, monkeypatch
):
    provider = _provider()
    secrets = resolve_q7_protected_secrets(provider=provider)
    account_scope = q7.derive_account_scope(
        secrets.account_id,
        identity_key=secrets.identity_key,
        identity_key_id=secrets.identity_key_id,
    )
    runtime = tmp_path / "runtime"
    _result, stage_a, b0 = _materialize(runtime, provider)
    activation = tmp_path / "activation.json"
    q7.prepare_activation(
        runtime_dir=runtime,
        stage_a_record=stage_a,
        b0_backup=b0,
        output_record=activation,
        candidate_commit=COMMIT,
        candidate_tree=TREE,
        provider=provider,
    )
    activation_value = q7.verify_record_bytes(activation.read_bytes())
    authority = SimpleNamespace(
        state=RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        post_attempt_count=0,
        pending_dispatch_proof_sha256=None,
        cutover_generation=1,
        environment="SANDBOX",
        ever_exact_activated=True,
        opening_cutoff="2026-09-19T00:00:00.000000000Z",
        opening_record_sha256="d" * 64,
        version=1,
        identity_key_id=secrets.identity_key_id,
        account_scope_sha256=account_scope,
        record_revision=7,
        sha256="3" * 64,
        activation_context_sha256="a" * 64,
        ledger_revision=6,
        ledger_head_sha256="5" * 64,
    )
    monkeypatch.setattr(
        q7,
        "RuntimeCashAuthorityStore",
        lambda _root: SimpleNamespace(
            load=lambda **_kwargs: authority,
        ),
    )
    monkeypatch.setattr(
        q7,
        "_configured_set",
        lambda *_args, **_kwargs: SimpleNamespace(
            identity_sha256=activation_value["configured_set_sha256"],
            bindings=(object(), object()),
        ),
    )
    monkeypatch.setattr(
        q7,
        "_initialize_and_validate_local_owners",
        lambda *_args, **_kwargs: (
            SimpleNamespace(
                account_id=ACCOUNT,
                freshness=q7.SnapshotFreshness.FRESH,
                blocking=False,
                revision=8,
            ),
            SimpleNamespace(blocking_intent=None, revision=4),
            {"policy_hash": "4" * 64},
            SimpleNamespace(revision=5),
        ),
    )
    ledger = SimpleNamespace(
        validate=lambda: SimpleNamespace(
            ledger_revision=6,
            ledger_head_sha256="5" * 64,
        ),
        close=lambda: None,
    )
    monkeypatch.setattr(q7, "_open_ledger", lambda *_args, **_kwargs: ledger)
    fresh_evidence = SimpleNamespace(
        reconciliation=SimpleNamespace(
            status=SimpleNamespace(value="MATCHED"),
            discrepancy_kind=SimpleNamespace(value="NONE"),
        ),
        availability=SimpleNamespace(status=SimpleNamespace(value="READY")),
        context=SimpleNamespace(
            status=SimpleNamespace(value="READY_FOR_LOCKED_REVALIDATION"),
            sha256="b" * 64,
            ledger_revision=6,
            ledger_head_sha256="5" * 64,
            account_scope_sha256=account_scope,
            identity_key_id=secrets.identity_key_id,
            central_order_revision=4,
            portfolio_revision=8,
            risk_policy_hash="4" * 64,
            risk_state_guard_hash="c" * 64,
        ),
    )
    monkeypatch.setattr(q7, "risk_state_guard_hash", lambda _state: "c" * 64)
    monkeypatch.setattr(
        q7, "_fresh_runtime_context", lambda _root: (authority, fresh_evidence)
    )
    monkeypatch.setattr(
        q7,
        "_backup_binding",
        lambda *_args, **_kwargs: {
            "sha256": "6" * 64,
            "size_bytes": 1234,
            "manifest_sha256": "7" * 64,
            "status": "VERIFIED",
        },
    )
    result = q7.finalize_preparation(
        runtime_dir=runtime,
        activation_record=activation,
        output_record=tmp_path / "final.json",
        backup_output=tmp_path / "b1.zip",
        candidate_commit=COMMIT,
        candidate_tree=TREE,
        q4_artifact_identity_sha256="8" * 64,
        q5_privacy_summary_sha256="9" * 64,
        provider=provider,
        generated_at="2026-09-13T00:02:00+00:00",
    )
    record = q7.verify_record_bytes((tmp_path / "final.json").read_bytes())
    assert result["status"] == "PASS"
    assert result["burnin_authorized"] is False
    assert record["authority_state"] == "EXACT_CASH_ARMED"
    assert record["post_attempt_count"] == 0
    assert record["pending_dispatch_proof_sha256"] is None
    assert record["preparation_provider_order_mutations"] == 0
    assert record["reconciliation_status"] == "MATCHED"
    assert record["availability_status"] == "READY"
    assert record["cash_context_status"] == "READY_FOR_LOCKED_REVALIDATION"

    fresh_evidence.availability.status.value = "MANUAL_REVIEW_REQUIRED"
    with pytest.raises(q7.Q7PreparationError, match="FRESH_CASH_CONTEXT_NOT_READY"):
        q7.finalize_preparation(
            runtime_dir=runtime,
            activation_record=activation,
            output_record=tmp_path / "final-blocked.json",
            backup_output=tmp_path / "b1-blocked.zip",
            candidate_commit=COMMIT,
            candidate_tree=TREE,
            q4_artifact_identity_sha256="8" * 64,
            q5_privacy_summary_sha256="9" * 64,
            provider=provider,
        )
    assert not (tmp_path / "b1-blocked.zip").exists()

    fresh_evidence.availability.status.value = "READY"
    fresh_evidence.context.ledger_revision = 7
    with pytest.raises(
        q7.Q7PreparationError, match="LEDGER_AUTHORITY_BINDING_MISMATCH"
    ):
        q7.finalize_preparation(
            runtime_dir=runtime,
            activation_record=activation,
            output_record=tmp_path / "final-ledger-drift.json",
            backup_output=tmp_path / "b1-ledger-drift.zip",
            candidate_commit=COMMIT,
            candidate_tree=TREE,
            q4_artifact_identity_sha256="8" * 64,
            q5_privacy_summary_sha256="9" * 64,
            provider=provider,
        )
    assert not (tmp_path / "b1-ledger-drift.zip").exists()

    # A fresh CL7 sync may legitimately advance the watermark while armed.
    # Final preparation must bind the successor authority, not the pre-sync SHA.
    ledger_snapshots = iter(
        (
            SimpleNamespace(ledger_revision=6, ledger_head_sha256="5" * 64),
            SimpleNamespace(ledger_revision=7, ledger_head_sha256="6" * 64),
        )
    )
    ledger.validate = lambda: next(ledger_snapshots)
    fresh_evidence.context.ledger_revision = 7
    fresh_evidence.context.ledger_head_sha256 = "6" * 64
    synced = SimpleNamespace(
        **{
            **vars(authority),
            "sha256": "e" * 64,
            "record_revision": 8,
            "previous_record_sha256": authority.sha256,
            "transition_kind": "SYNC_ADVANCED",
            "operations_complete_through": "2026-09-19T00:01:00.000000000Z",
            "ledger_revision": 7,
            "ledger_head_sha256": "6" * 64,
        }
    )
    loads = iter((authority, synced, synced))
    monkeypatch.setattr(
        q7,
        "RuntimeCashAuthorityStore",
        lambda _root: SimpleNamespace(load=lambda **_kwargs: next(loads)),
    )
    monkeypatch.setattr(
        q7, "_fresh_runtime_context", lambda _root: (synced, fresh_evidence)
    )
    q7.finalize_preparation(
        runtime_dir=runtime,
        activation_record=activation,
        output_record=tmp_path / "final-synced.json",
        backup_output=tmp_path / "b1-synced.zip",
        candidate_commit=COMMIT,
        candidate_tree=TREE,
        q4_artifact_identity_sha256="8" * 64,
        q5_privacy_summary_sha256="9" * 64,
        provider=provider,
    )
    synced_record = q7.verify_record_bytes((tmp_path / "final-synced.json").read_bytes())
    assert synced_record["authority_revision"] == 8
    assert synced_record["authority_record_sha256"] == synced.sha256
    assert synced_record["ledger_revision"] == 7
    assert synced_record["ledger_head_sha256"] == "6" * 64

    ledger_snapshots = iter(
        (
            SimpleNamespace(ledger_revision=6, ledger_head_sha256="5" * 64),
            SimpleNamespace(ledger_revision=7, ledger_head_sha256="6" * 64),
        )
    )
    ledger.validate = lambda: next(ledger_snapshots)
    loads = iter((authority, synced, SimpleNamespace(sha256="f" * 64)))
    with pytest.raises(q7.Q7PreparationError, match="FRESH_AUTHORITY_SUBSTITUTION"):
        q7.finalize_preparation(
            runtime_dir=runtime,
            activation_record=activation,
            output_record=tmp_path / "final-post-backup-drift.json",
            backup_output=tmp_path / "b1-post-backup-drift.zip",
            candidate_commit=COMMIT,
            candidate_tree=TREE,
            q4_artifact_identity_sha256="8" * 64,
            q5_privacy_summary_sha256="9" * 64,
            provider=provider,
        )
    assert not (tmp_path / "final-post-backup-drift.json").exists()


@pytest.mark.parametrize(
    ("change", "readback_sha"),
    [
        ({"previous_record_sha256": "f" * 64}, "2" * 64),
        ({"record_revision": 9}, "2" * 64),
        ({"transition_kind": "ARM_EXACT"}, "2" * 64),
        ({"account_scope_sha256": "f" * 64}, "2" * 64),
        ({"activation_context_sha256": "f" * 64}, "2" * 64),
        ({"cutover_generation": 2}, "2" * 64),
        ({"state": RuntimeCashAuthorityState.EXACT_CASH_DISARMED}, "2" * 64),
        ({"post_attempt_count": 1}, "2" * 64),
        ({"pending_dispatch_proof_sha256": "f" * 64}, "2" * 64),
        ({}, "f" * 64),
    ],
)
def test_finalization_rejects_foreign_fresh_authority(change, readback_sha):
    before = SimpleNamespace(
        sha256="1" * 64,
        record_revision=7,
        state=RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        post_attempt_count=0,
        pending_dispatch_proof_sha256=None,
        cutover_generation=1,
        environment="SANDBOX",
        ever_exact_activated=True,
        account_scope_sha256="a" * 64,
        identity_key_id="SYNTHETIC_V1",
        activation_context_sha256="b" * 64,
        opening_cutoff="2026-09-19T00:00:00.000000000Z",
        opening_record_sha256="c" * 64,
        version=1,
    )
    after = SimpleNamespace(
        **{
            **vars(before),
            "sha256": "2" * 64,
            "record_revision": 8,
            "previous_record_sha256": before.sha256,
            "transition_kind": "SYNC_ADVANCED",
            **change,
        }
    )
    readback = SimpleNamespace(sha256=readback_sha)
    with pytest.raises(q7.Q7PreparationError, match="FRESH_AUTHORITY_SUBSTITUTION"):
        q7._require_fresh_authority(before, after, readback)


def test_finalization_rejects_missing_fresh_authority_binding():
    before = SimpleNamespace(
        sha256="1" * 64,
        record_revision=7,
        state=RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        post_attempt_count=0,
        pending_dispatch_proof_sha256=None,
        account_scope_sha256="a" * 64,
    )
    after = SimpleNamespace(
        sha256="2" * 64,
        record_revision=8,
        state=RuntimeCashAuthorityState.EXACT_CASH_ARMED,
        post_attempt_count=0,
        pending_dispatch_proof_sha256=None,
        previous_record_sha256=before.sha256,
        transition_kind="SYNC_ADVANCED",
        account_scope_sha256="a" * 64,
    )
    with pytest.raises(q7.Q7PreparationError, match="FRESH_AUTHORITY_SUBSTITUTION"):
        q7._require_fresh_authority(before, after, after)


@pytest.mark.parametrize("field", VECTORS["evidence_tamper_fields"])
def test_q7r_30_record_tamper_is_rejected(field):
    payload = {
        "version": 1,
        "domain": "synthetic",
        "candidate_commit": COMMIT,
        "candidate_tree": TREE,
        "runtime_instance_id": "1" * 64,
        "configured_set_sha256": "2" * 64,
        "b0_backup_sha256": "3" * 64,
        "identity_key_id": "Q7R_SYNTHETIC_V1",
    }
    raw = q7.build_record(payload)
    value = json.loads(raw)
    value[field] = "tampered"
    tampered = q7._canonical_bytes(value)
    with pytest.raises(q7.Q7PreparationError, match="SHA256_MISMATCH"):
        q7.verify_record_bytes(tampered)


def test_closed_q7r_case_set_has_behavioral_nodes():
    assert VECTORS["case_ids"] == [f"Q7R-{index:02d}" for index in range(1, 31)]
    source = Path(__file__).read_text(encoding="utf-8")
    names = {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_q7r_")
    }
    assert len(names) >= 13
    assert "results" not in VECTORS


def test_cl4_opening_dependency_reason_is_finite_and_privacy_safe():
    visible = cutover._blocked_payload(
        CL7RuntimeError(
            CL7RuntimeReason.OPENING_INVALID,
            "MONEY_INVALID",
            stage="CL4_OPENING",
        )
    )
    assert visible == {
        "reason": "OPENING_INVALID",
        "dependency_reason": "MONEY_INVALID",
        "retryable": False,
        "stage": "CL4_OPENING",
        "status": "BLOCKED",
    }

    unsafe = cutover._blocked_payload(
        CL7RuntimeError(
            CL7RuntimeReason.OPENING_INVALID,
            "PRIVATE_CANARY",
            stage="CL4_OPENING",
        )
    )
    assert "dependency_reason" not in unsafe

    wrong_stage = cutover._blocked_payload(
        CL7RuntimeError(
            CL7RuntimeReason.OPENING_INVALID,
            "MONEY_INVALID",
            stage="CL3_SYNC",
        )
    )
    assert "dependency_reason" not in wrong_stage


def test_cl6_context_dependency_reason_is_finite_and_privacy_safe():
    for reason in RiskCashContextReason:
        payload = cutover._blocked_payload(
            CL7RuntimeError(
                CL7RuntimeReason.CONTEXT_BLOCKED,
                reason.value,
                stage="CL6_CONTEXT",
            )
        )
        if reason is RiskCashContextReason.READY:
            assert "dependency_reason" not in payload
        else:
            assert payload["dependency_reason"] == reason.value

    for dependency_reason, stage in (
        ("PRIVATE_ACCOUNT_CANARY", "CL6_CONTEXT"),
        (RiskCashContextReason.PORTFOLIO_NOT_READY.value, "CL4_OPENING"),
    ):
        payload = cutover._blocked_payload(
            CL7RuntimeError(
                CL7RuntimeReason.CONTEXT_BLOCKED,
                dependency_reason,
                stage=stage,
            )
        )
        assert "dependency_reason" not in payload


@pytest.mark.parametrize(
    "status,reason",
    [
        (cl5.AvailabilityStatus.BLOCKED, cl5.AvailabilityReason.CL4_NOT_READY),
        (
            cl5.AvailabilityStatus.BLOCKED,
            cl5.AvailabilityReason.BROKER_PROOF_STALE,
        ),
        (
            cl5.AvailabilityStatus.BLOCKED,
            cl5.AvailabilityReason.CENTRAL_PROJECTION_STALE,
        ),
        (
            cl5.AvailabilityStatus.BLOCKED,
            cl5.AvailabilityReason.MIXED_EVIDENCE_SNAPSHOT,
        ),
        (
            cl5.AvailabilityStatus.BLOCKED,
            cl5.AvailabilityReason.BROKER_VIEW_MISMATCH,
        ),
        (
            cl5.AvailabilityStatus.BLOCKED,
            cl5.AvailabilityReason.FOREIGN_CASH_PRESENT,
        ),
        (
            cl5.AvailabilityStatus.BLOCKED,
            cl5.AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS,
        ),
        (
            cl5.AvailabilityStatus.MANUAL_REVIEW_REQUIRED,
            cl5.AvailabilityReason.CENTRAL_PROVIDER_OVERLAP_UNKNOWN,
        ),
    ],
)
def test_cl5_availability_observability_is_finite_and_pair_bound(status, reason):
    payload = cutover._blocked_payload(
        CL7RuntimeError(
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            stage="CL6_CONTEXT",
            availability_status=status.value,
            availability_reason=reason.value,
        )
    )
    assert payload["availability_status"] == status.value
    assert payload["availability_reason"] == reason.value


@pytest.mark.parametrize(
    "outer_reason,dependency_reason,stage,status,reason",
    [
        (
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            "CL6_CONTEXT",
            cl5.AvailabilityStatus.READY.value,
            cl5.AvailabilityReason.READY.value,
        ),
        (
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            "CL6_CONTEXT",
            cl5.AvailabilityStatus.BLOCKED.value,
            cl5.AvailabilityReason.CENTRAL_PROVIDER_OVERLAP_UNKNOWN.value,
        ),
        (
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.PORTFOLIO_NOT_READY.value,
            "CL6_CONTEXT",
            cl5.AvailabilityStatus.BLOCKED.value,
            cl5.AvailabilityReason.CL4_NOT_READY.value,
        ),
        (
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            "CL5_AVAILABILITY",
            cl5.AvailabilityStatus.BLOCKED.value,
            cl5.AvailabilityReason.CL4_NOT_READY.value,
        ),
        (
            CL7RuntimeReason.INTERNAL_BOUNDARY_FAILED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            "CL6_CONTEXT",
            cl5.AvailabilityStatus.BLOCKED.value,
            cl5.AvailabilityReason.CL4_NOT_READY.value,
        ),
        (
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            "CL6_CONTEXT",
            "PRIVATE_STATUS_CANARY",
            "PRIVATE_REASON_CANARY",
        ),
    ],
)
def test_cl5_availability_observability_fails_closed_outside_exact_boundary(
    outer_reason, dependency_reason, stage, status, reason
):
    payload = cutover._blocked_payload(
        CL7RuntimeError(
            outer_reason,
            dependency_reason,
            stage=stage,
            availability_status=status,
            availability_reason=reason,
        )
    )
    assert "availability_status" not in payload
    assert "availability_reason" not in payload
    assert "PRIVATE" not in json.dumps(payload, sort_keys=True)


def test_cl5_availability_observability_rejects_non_exact_string_types():
    class StringSubclass(str):
        pass

    class EqualToBlocked:
        def __eq__(self, other):
            return other == cl5.AvailabilityStatus.BLOCKED.value

        def __str__(self):
            return cl5.AvailabilityStatus.BLOCKED.value

    invalid_values = (StringSubclass("BLOCKED"), EqualToBlocked())
    invalid_pairs = [
        (value, cl5.AvailabilityReason.CL4_NOT_READY.value) for value in invalid_values
    ] + [(cl5.AvailabilityStatus.BLOCKED.value, value) for value in invalid_values]
    for status, reason in invalid_pairs:
        error = CL7RuntimeError(
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            stage="CL6_CONTEXT",
            availability_status=cl5.AvailabilityStatus.BLOCKED.value,
            availability_reason=cl5.AvailabilityReason.CL4_NOT_READY.value,
        )
        error.availability_status = status
        error.availability_reason = reason
        payload = cutover._blocked_payload(error)
        assert "availability_status" not in payload
        assert "availability_reason" not in payload


def test_cl7_not_ready_context_preserves_cl5_observability_at_error_boundary():
    context = SimpleNamespace(
        reason=RiskCashContextReason.CASH_AVAILABILITY_NOT_READY,
        availability_status=cl5.AvailabilityStatus.BLOCKED.value,
        availability_reason=cl5.AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS.value,
    )
    with pytest.raises(CL7RuntimeError) as caught:
        cl7._fail_context_not_ready(context)

    assert caught.value.reason is CL7RuntimeReason.CONTEXT_BLOCKED
    assert caught.value.stage == "CL6_CONTEXT"
    assert (
        caught.value.dependency_reason
        == RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value
    )
    assert caught.value.availability_status == cl5.AvailabilityStatus.BLOCKED.value
    assert (
        caught.value.availability_reason
        == cl5.AvailabilityReason.INSUFFICIENT_AFTER_RESERVATIONS.value
    )


def _broker_view_observability(
    broker_total: int,
    positions_money: int,
    blocked: int,
    *,
    key: bytes = b"k" * 32,
):
    return cl7._build_broker_view_observability(
        broker_total_cash=Money(currency="RUB", minor_units=broker_total),
        positions_money_rub=Money(currency="RUB", minor_units=positions_money),
        blocked_rub=Money(currency="RUB", minor_units=blocked),
        identity_key=key,
    )


def test_broker_view_observability_has_frozen_domain_separated_hmac_vector():
    observed = _broker_view_observability(
        100_000_000_000,
        70_000_000_000,
        30_000_000_000,
    )

    assert observed == {
        "broker_total_cash_hmac_sha256": (
            "f00df5bae4caca988cad8b57ccc03005e5d2c6d282a8e7db42d1964ffe3b1d89"
        ),
        "positions_money_rub_hmac_sha256": (
            "be010e1869c9e84304d229c6e2318f67de2d968c99ef510df53ff41f8ee792a6"
        ),
        "blocked_rub_hmac_sha256": (
            "62fc4fc9c1efdefc3de63736ac02683e6e694addb6415d8e666f5c82dd823a2e"
        ),
        "positions_plus_blocked_rub_hmac_sha256": (
            "73fb7debd0f6e7551f7ae38b8ffd6eed37d48a391bb53d813e12965369268410"
        ),
        "broker_total_eq_positions_money": False,
        "broker_total_eq_blocked": False,
        "positions_money_eq_blocked": False,
        "broker_total_eq_positions_plus_blocked": True,
        "broker_total_is_zero": False,
        "positions_money_is_zero": False,
        "blocked_is_zero": False,
    }
    assert observed == _broker_view_observability(
        100_000_000_000,
        70_000_000_000,
        30_000_000_000,
    )


@pytest.mark.parametrize(
    "broker_total,positions_money,blocked,expected",
    [
        (
            0,
            0,
            0,
            (True, True, True, True, True, True, True),
        ),
        (
            100_000_000_000,
            100_000_000_000,
            0,
            (True, False, False, True, False, False, True),
        ),
        (
            100_000_000_000,
            0,
            100_000_000_000,
            (False, True, False, True, False, True, False),
        ),
        (
            100_000_000_000,
            60_000_000_000,
            30_000_000_000,
            (False, False, False, False, False, False, False),
        ),
    ],
)
def test_broker_view_observability_exact_canonical_equality_truth_table(
    broker_total,
    positions_money,
    blocked,
    expected,
):
    value = _broker_view_observability(broker_total, positions_money, blocked)
    actual = (
        value["broker_total_eq_positions_money"],
        value["broker_total_eq_blocked"],
        value["positions_money_eq_blocked"],
        value["broker_total_eq_positions_plus_blocked"],
        value["broker_total_is_zero"],
        value["positions_money_is_zero"],
        value["blocked_is_zero"],
    )
    assert actual == expected
    if broker_total == positions_money == blocked == 0:
        hashes = {
            value["broker_total_cash_hmac_sha256"],
            value["positions_money_rub_hmac_sha256"],
            value["blocked_rub_hmac_sha256"],
            value["positions_plus_blocked_rub_hmac_sha256"],
        }
        assert len(hashes) == 4


def test_broker_view_observability_detects_one_kopeck_operand_mutations():
    baseline = _broker_view_observability(
        100_000_000_000,
        70_000_000_000,
        30_000_000_000,
    )
    one_kopeck = 10_000_000
    broker_changed = _broker_view_observability(
        100_000_000_000 + one_kopeck,
        70_000_000_000,
        30_000_000_000,
    )
    positions_changed = _broker_view_observability(
        100_000_000_000,
        70_000_000_000 + one_kopeck,
        30_000_000_000,
    )
    blocked_changed = _broker_view_observability(
        100_000_000_000,
        70_000_000_000,
        30_000_000_000 + one_kopeck,
    )

    assert (
        broker_changed["broker_total_cash_hmac_sha256"]
        != baseline["broker_total_cash_hmac_sha256"]
    )
    assert (
        positions_changed["positions_money_rub_hmac_sha256"]
        != baseline["positions_money_rub_hmac_sha256"]
    )
    assert (
        blocked_changed["blocked_rub_hmac_sha256"]
        != baseline["blocked_rub_hmac_sha256"]
    )
    assert (
        positions_changed["positions_plus_blocked_rub_hmac_sha256"]
        != baseline["positions_plus_blocked_rub_hmac_sha256"]
    )
    assert (
        blocked_changed["positions_plus_blocked_rub_hmac_sha256"]
        != baseline["positions_plus_blocked_rub_hmac_sha256"]
    )


def test_broker_view_observability_is_atomic_and_privacy_safe_at_cli_boundary():
    observability = _broker_view_observability(
        100_123_456_789,
        70_111_111_111,
        30_022_345_678,
        key=b"PRIVATE_IDENTITY_KEY_CANARY_123456",
    )
    context = SimpleNamespace(
        reason=RiskCashContextReason.CASH_AVAILABILITY_NOT_READY,
        availability_status=cl5.AvailabilityStatus.BLOCKED.value,
        availability_reason=cl5.AvailabilityReason.BROKER_VIEW_MISMATCH.value,
    )
    with pytest.raises(CL7RuntimeError) as caught:
        cl7._fail_context_not_ready(
            context,
            broker_view_observability=observability,
        )

    payload = cutover._blocked_payload(caught.value)
    assert payload["reason"] == "CONTEXT_BLOCKED"
    assert payload["dependency_reason"] == "CASH_AVAILABILITY_NOT_READY"
    assert payload["availability_status"] == "BLOCKED"
    assert payload["availability_reason"] == "BROKER_VIEW_MISMATCH"
    assert payload["stage"] == "CL6_CONTEXT"
    assert payload["retryable"] is False
    assert payload["broker_view_observability"] == observability
    serialized = json.dumps(payload, sort_keys=True)
    for private_value in (
        "100123456789",
        "70111111111",
        "30022345678",
        "PRIVATE_IDENTITY_KEY_CANARY_123456",
        "PRIVATE_ACCOUNT_CANARY",
        "Authorization",
    ):
        assert private_value not in serialized


def test_withdraw_limits_runtime_rebuild_has_no_positions_ready_oracle():
    source = inspect.getsource(cl7.RuntimeCashAuthorityManager.build_runtime_context)
    assert "build_broker_withdraw_limits_cash_proof" in source
    assert "build_broker_positions_cash_proof" not in source
    assert "positions_money_rub" not in source
    assert "BROKER_VIEW_MISMATCH" not in source


def test_final_locked_revalidation_reads_withdraw_limits_exactly_once():
    from trading_robot.sandbox_execution_adapter import SandboxExecutionAdapter

    initial = inspect.getsource(
        cl7.RuntimeCashAuthorityManager._sync_and_rebuild_locked
    )
    final = inspect.getsource(SandboxExecutionAdapter._dispatch_exact)
    for source in (initial, final):
        assert source.count(".get_withdraw_limits(") == 1
        assert ".get_positions(" not in source
        assert "provider_as_of" not in source
        assert source.index("get_portfolio(") < source.index("broker_cash_as_of")
        assert source.index("broker_cash_as_of") < source.index(
            "get_withdraw_limits("
        )
        assert source.index("get_withdraw_limits(") < source.index(
            "broker_withdraw_limits_as_of"
        )
    assert final.count(".post_order_once(") == 1


def test_tbank_withdraw_limits_observation_binds_request_in_same_call_frame(
    monkeypatch,
):
    client = TBankSandboxClient("dummy-token", max_retries=0)
    response = {
        "money": [{"currency": "RUB", "units": "80", "nano": 0}],
        "blocked": [{"currency": "RUB", "units": "20", "nano": 0}],
        "blockedGuarantee": [],
    }
    calls = []

    def post(_self, service, method, payload, **_kwargs):
        calls.append((service, method, dict(payload)))
        return response

    monkeypatch.setattr(TBankSandboxClient, "_post", post)
    observation = client.get_withdraw_limits("synthetic-account")
    assert type(observation) is cl5.WithdrawLimitsTransportObservation
    assert observation.raw_request_account_id == "synthetic-account"
    assert observation.service == "SandboxService"
    assert observation.method == "GetSandboxWithdrawLimits"
    assert calls == [(
        "SandboxService", "GetSandboxWithdrawLimits",
        {"accountId": "synthetic-account"},
    )]
    response["money"].clear()
    assert observation.response["money"] != []
    client.close()

def test_broker_view_observability_rejects_non_atomic_and_adversarial_values():
    class DictSubclass(dict):
        pass

    class StringSubclass(str):
        pass

    class EqualitySpoof:
        def __eq__(self, other):
            return other in (True, "0" * 64)

    class KeyEqualitySpoof:
        def __hash__(self):
            return hash("blocked_is_zero")

        def __eq__(self, other):
            return other == "blocked_is_zero"

    valid = _broker_view_observability(
        100_000_000_000,
        70_000_000_000,
        30_000_000_000,
    )
    invalid = []
    missing = dict(valid)
    missing.pop("blocked_is_zero")
    invalid.append(missing)
    invalid.append({**valid, "extra": False})
    invalid.append(DictSubclass(valid))
    invalid.append({**valid, "broker_total_cash_hmac_sha256": "A" * 64})
    invalid.append(
        {
            **valid,
            "broker_total_cash_hmac_sha256": StringSubclass("0" * 64),
        }
    )
    invalid.append({**valid, "broker_total_is_zero": 0})
    invalid.append({**valid, "broker_total_is_zero": EqualitySpoof()})
    subclass_key = dict(valid)
    subclass_key[StringSubclass("blocked_is_zero")] = subclass_key.pop(
        "blocked_is_zero"
    )
    invalid.append(subclass_key)
    equality_key = dict(valid)
    equality_key[KeyEqualitySpoof()] = equality_key.pop("blocked_is_zero")
    invalid.append(equality_key)

    for adversarial in invalid:
        error = CL7RuntimeError(
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            stage="CL6_CONTEXT",
            availability_status=cl5.AvailabilityStatus.BLOCKED.value,
            availability_reason=cl5.AvailabilityReason.BROKER_VIEW_MISMATCH.value,
            broker_view_observability=valid,
        )
        error.broker_view_observability = adversarial
        payload = cutover._blocked_payload(error)
        assert "broker_view_observability" not in payload


def test_broker_view_observability_rejects_operand_and_key_substitution():
    class StringSubclass(str):
        pass

    class BytesSubclass(bytes):
        pass

    class EqualitySpoof:
        def __eq__(self, other):
            return other == "RUB"

    poisoned_currency = Money(
        currency=StringSubclass("RUB"),
        minor_units=100_000_000_000,
    )
    equality_currency = Money(currency="RUB", minor_units=100_000_000_000)
    object.__setattr__(equality_currency, "currency", EqualitySpoof())
    valid_positions = Money(currency="RUB", minor_units=70_000_000_000)
    valid_blocked = Money(currency="RUB", minor_units=30_000_000_000)

    for broker_total, key in (
        (poisoned_currency, b"k" * 32),
        (equality_currency, b"k" * 32),
        (
            Money(currency="RUB", minor_units=100_000_000_000),
            BytesSubclass(b"k" * 32),
        ),
    ):
        with pytest.raises(CL7RuntimeError) as caught:
            cl7._build_broker_view_observability(
                broker_total_cash=broker_total,
                positions_money_rub=valid_positions,
                blocked_rub=valid_blocked,
                identity_key=key,
            )
        assert caught.value.reason in {
            CL7RuntimeReason.TYPE_INVALID,
            CL7RuntimeReason.IDENTITY_KEY_INVALID,
        }


@pytest.mark.parametrize(
    "outer_reason,dependency_reason,stage,status,reason",
    [
        (
            CL7RuntimeReason.INTERNAL_BOUNDARY_FAILED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            "CL6_CONTEXT",
            cl5.AvailabilityStatus.BLOCKED.value,
            cl5.AvailabilityReason.BROKER_VIEW_MISMATCH.value,
        ),
        (
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.PORTFOLIO_NOT_READY.value,
            "CL6_CONTEXT",
            cl5.AvailabilityStatus.BLOCKED.value,
            cl5.AvailabilityReason.BROKER_VIEW_MISMATCH.value,
        ),
        (
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            "CL5_AVAILABILITY",
            cl5.AvailabilityStatus.BLOCKED.value,
            cl5.AvailabilityReason.BROKER_VIEW_MISMATCH.value,
        ),
        (
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            "CL6_CONTEXT",
            cl5.AvailabilityStatus.MANUAL_REVIEW_REQUIRED.value,
            cl5.AvailabilityReason.CENTRAL_PROVIDER_OVERLAP_UNKNOWN.value,
        ),
        (
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            "CL6_CONTEXT",
            cl5.AvailabilityStatus.BLOCKED.value,
            cl5.AvailabilityReason.CL4_NOT_READY.value,
        ),
    ],
)
def test_broker_view_observability_is_absent_outside_exact_gate(
    outer_reason,
    dependency_reason,
    stage,
    status,
    reason,
):
    observability = _broker_view_observability(
        100_000_000_000,
        70_000_000_000,
        30_000_000_000,
    )
    payload = cutover._blocked_payload(
        CL7RuntimeError(
            outer_reason,
            dependency_reason,
            stage=stage,
            availability_status=status,
            availability_reason=reason,
            broker_view_observability=observability,
        )
    )
    assert "broker_view_observability" not in payload


def test_broker_view_observability_exact_gate_rejects_mutated_string_types():
    class StringSubclass(str):
        pass

    class EqualitySpoof:
        def __hash__(self):
            return hash("CL6_CONTEXT")

        def __eq__(self, other):
            return other in (
                "CL6_CONTEXT",
                RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            )

    observability = _broker_view_observability(
        100_000_000_000,
        70_000_000_000,
        30_000_000_000,
    )
    for attribute, adversarial in (
        ("stage", StringSubclass("CL6_CONTEXT")),
        (
            "dependency_reason",
            StringSubclass(RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value),
        ),
        ("stage", EqualitySpoof()),
        ("dependency_reason", EqualitySpoof()),
    ):
        error = CL7RuntimeError(
            CL7RuntimeReason.CONTEXT_BLOCKED,
            RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
            stage="CL6_CONTEXT",
            availability_status=cl5.AvailabilityStatus.BLOCKED.value,
            availability_reason=cl5.AvailabilityReason.BROKER_VIEW_MISMATCH.value,
            broker_view_observability=observability,
        )
        setattr(error, attribute, adversarial)
        payload = cutover._blocked_payload(error)
        assert "broker_view_observability" not in payload


def test_cl6_context_dependency_reason_is_wired_to_cli_evidence(
    monkeypatch, tmp_path, capsys
):
    class FailingAuthority:
        def prepare_runtime(self, **_kwargs):
            raise CL7RuntimeError(
                CL7RuntimeReason.CONTEXT_BLOCKED,
                RiskCashContextReason.PORTFOLIO_STALE.value,
                stage="CL6_CONTEXT",
            )

    runtime = SimpleNamespace(
        authority=FailingAuthority(),
        ledger=SimpleNamespace(close=lambda: None),
        provider=SimpleNamespace(last_response_meta={"private": "PRIVATE_CANARY"}),
        inputs=dict,
    )
    monkeypatch.setattr(cutover, "_open_runtime", lambda *_args, **_kwargs: runtime)

    assert cutover.main(["prepare", "--runtime-dir", str(tmp_path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "reason": "CONTEXT_BLOCKED",
        "dependency_reason": "PORTFOLIO_STALE",
        "retryable": False,
        "stage": "CL6_CONTEXT",
        "status": "BLOCKED",
    }
    assert "PRIVATE_CANARY" not in json.dumps(payload, sort_keys=True)


def test_cl5_availability_observability_is_wired_to_cli_evidence(
    monkeypatch, tmp_path, capsys
):
    class FailingAuthority:
        def prepare_runtime(self, **_kwargs):
            raise CL7RuntimeError(
                CL7RuntimeReason.CONTEXT_BLOCKED,
                RiskCashContextReason.CASH_AVAILABILITY_NOT_READY.value,
                stage="CL6_CONTEXT",
                availability_status=cl5.AvailabilityStatus.BLOCKED.value,
                availability_reason=cl5.AvailabilityReason.BROKER_VIEW_MISMATCH.value,
            )

    runtime = SimpleNamespace(
        authority=FailingAuthority(),
        ledger=SimpleNamespace(close=lambda: None),
        provider=SimpleNamespace(last_response_meta={"private": "PRIVATE_CANARY"}),
        inputs=dict,
    )
    monkeypatch.setattr(cutover, "_open_runtime", lambda *_args, **_kwargs: runtime)

    assert cutover.main(["prepare", "--runtime-dir", str(tmp_path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "reason": "CONTEXT_BLOCKED",
        "dependency_reason": "CASH_AVAILABILITY_NOT_READY",
        "availability_status": "BLOCKED",
        "availability_reason": "BROKER_VIEW_MISMATCH",
        "retryable": False,
        "stage": "CL6_CONTEXT",
        "status": "BLOCKED",
    }
    assert "PRIVATE_CANARY" not in json.dumps(payload, sort_keys=True)


def test_cutover_inputs_refresh_canonical_portfolio_before_exact_read_back():
    events = []
    state = PortfolioState.empty(
        account_id=ACCOUNT,
        now="2026-09-16T00:00:00+00:00",
    )

    class Manager:
        def refresh(self, *, record_event):
            events.append(("refresh", record_event))
            return state

    class Repository:
        def load(self, *, expected_account_id):
            events.append(("read_back", expected_account_id))
            return state

    repository = Repository()
    runtime = cutover._Runtime(
        root=Path("synthetic"),
        authority=SimpleNamespace(),
        ledger=SimpleNamespace(),
        portfolio=repository,
        profiles=SimpleNamespace(),
        risk_state=SimpleNamespace(),
        central=SimpleNamespace(),
        provider=SimpleNamespace(),
        portfolio_manager=Manager(),
        raw_account=ACCOUNT,
        identity_key=b"k" * 32,
        identity_key_id="Q7R_TEST_V1",
    )

    inputs = runtime.inputs()

    assert events == [("refresh", False), ("read_back", ACCOUNT)]
    assert inputs["portfolio_repository"] is repository


@pytest.mark.parametrize("failure", ["refresh", "read_back", "mismatch"])
def test_cutover_inputs_fail_closed_when_canonical_refresh_is_not_exact(failure):
    published = PortfolioState.empty(
        account_id=ACCOUNT,
        now="2026-09-16T00:00:00+00:00",
    )
    different = PortfolioState.empty(
        account_id=ACCOUNT,
        now="2026-09-16T00:00:01+00:00",
    )

    class Manager:
        def refresh(self, *, record_event):
            assert record_event is False
            if failure == "refresh":
                raise RuntimeError("PRIVATE_REFRESH_CANARY")
            return published

    class Repository:
        def load(self, *, expected_account_id):
            assert expected_account_id == ACCOUNT
            if failure == "read_back":
                raise RuntimeError("PRIVATE_READ_BACK_CANARY")
            return different if failure == "mismatch" else published

    runtime = cutover._Runtime(
        root=Path("synthetic"),
        authority=SimpleNamespace(),
        ledger=SimpleNamespace(),
        portfolio=Repository(),
        profiles=SimpleNamespace(),
        risk_state=SimpleNamespace(),
        central=SimpleNamespace(),
        provider=SimpleNamespace(),
        portfolio_manager=Manager(),
        raw_account=ACCOUNT,
        identity_key=b"k" * 32,
        identity_key_id="Q7R_TEST_V1",
    )

    with pytest.raises(CL7RuntimeError) as caught:
        runtime.inputs()

    assert caught.value.reason is CL7RuntimeReason.CONTEXT_BLOCKED
    assert caught.value.stage == "CL6_CONTEXT"
    assert (
        caught.value.dependency_reason
        == RiskCashContextReason.PORTFOLIO_NOT_READY.value
    )
    assert "PRIVATE" not in str(caught.value)


def test_open_runtime_composes_existing_canonical_portfolio_owner(
    monkeypatch, tmp_path
):
    captured = {}
    provider = SimpleNamespace()
    repository = SimpleNamespace()

    class Manager:
        def __init__(
            self,
            selected_provider,
            account_id,
            *,
            robot_state_file,
            portfolio_state_file,
            journal_file,
        ):
            captured.update(
                provider=selected_provider,
                account_id=account_id,
                robot_state_file=robot_state_file,
                portfolio_state_file=portfolio_state_file,
                journal_file=journal_file,
            )
            self.repository = repository

    monkeypatch.setattr(cutover, "CanonicalPortfolioManager", Manager)
    monkeypatch.setattr(cutover, "_provider", lambda token: provider)
    monkeypatch.setattr(
        cutover,
        "resolve_q7_protected_secrets",
        lambda **_kwargs: SimpleNamespace(
            token="PRIVATE_TOKEN",
            account_id=ACCOUNT,
            identity_key=b"k" * 32,
            identity_key_id="Q7R_TEST_V1",
        ),
    )

    runtime = cutover._open_runtime(
        tmp_path,
        create_ledger=True,
        require_provider=True,
    )
    try:
        assert runtime.provider is provider
        assert runtime.portfolio_manager.__class__ is Manager
        assert runtime.portfolio is repository
        assert captured == {
            "provider": provider,
            "account_id": ACCOUNT,
            "robot_state_file": tmp_path / "robot_state.json",
            "portfolio_state_file": tmp_path / "portfolio_state.json",
            "journal_file": tmp_path / "trading_events.db",
        }
    finally:
        runtime.ledger.close()


def test_cl3_sync_observability_is_finite_privacy_safe_and_wired(
    monkeypatch, tmp_path, capsys
):
    tracking_id = "tracking-123"
    provider_meta = {
        "service": "SandboxService",
        "method": "GetSandboxOperationsByCursor",
        "status_code": 400,
        "transient": False,
        "attempt_count": 1,
        "tracking_id": tracking_id,
        "provider_error_code": "30014",
        "provider_error_category": "REQUEST_REJECTED",
        "request_from_inclusive": "2026-09-16T16:39:11.459527001Z",
        "request_to_exclusive": "2026-09-16T16:39:11.545931000Z",
        "error": "PRIVATE ERROR TEXT",
        "authorization": "Bearer PRIVATE_TOKEN",
        "account_id": "PRIVATE_ACCOUNT_ID",
        "response_headers": {"private": "value"},
    }

    class FailingAuthority:
        def prepare_runtime(self, **_kwargs):
            raise CL7RuntimeError(
                CL7RuntimeReason.BROKER_READ_FAILED,
                BrokerReadReason.TRANSPORT_HTTP_PERMANENT.value,
                stage="CL3_SYNC",
                retryable=True,
            )

    runtime = SimpleNamespace(
        authority=FailingAuthority(),
        ledger=SimpleNamespace(close=lambda: None),
        provider=SimpleNamespace(last_response_meta=provider_meta),
        inputs=dict,
    )
    monkeypatch.setattr(cutover, "_open_runtime", lambda *_args, **_kwargs: runtime)

    assert cutover.main(["prepare", "--runtime-dir", str(tmp_path)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "reason": "BROKER_READ_FAILED",
        "dependency_reason": "TRANSPORT_HTTP_PERMANENT",
        "retryable": True,
        "stage": "CL3_SYNC",
        "status": "BLOCKED",
        "provider_observability": {
            "service": "SandboxService",
            "method": "GetSandboxOperationsByCursor",
            "status_code": 400,
            "transient": False,
            "attempt_count": 1,
            "tracking_id_sha256": sha256(tracking_id.encode("utf-8")).hexdigest(),
            "provider_error_code": "30014",
            "provider_error_category": "REQUEST_REJECTED",
            "request_from_inclusive": "2026-09-16T16:39:11.459527001Z",
            "request_to_exclusive": "2026-09-16T16:39:11.545931000Z",
        },
    }
    serialized = json.dumps(payload, sort_keys=True)
    for private_value in (
        "PRIVATE ERROR TEXT",
        "PRIVATE_TOKEN",
        "PRIVATE_ACCOUNT_ID",
        tracking_id,
    ):
        assert private_value not in serialized


def test_cl3_sync_observability_rejects_unknown_leaf_and_adversarial_types():
    class StringSubclass(str):
        pass

    class IntegerSubclass(int):
        pass

    class DictSubclass(dict):
        pass

    finite = CL7RuntimeError(
        CL7RuntimeReason.BROKER_READ_FAILED,
        BrokerReadReason.TRANSPORT_CONNECTION_INTERRUPTED.value,
        stage="CL3_SYNC",
        retryable=True,
    )
    adversarial = cutover._blocked_payload(
        finite,
        provider_meta={
            "service": StringSubclass("SandboxService"),
            "method": StringSubclass("GetSandboxOperationsByCursor"),
            "status_code": True,
            "error_class": StringSubclass("ConnectTimeout"),
            "transient": IntegerSubclass(1),
            "attempt_count": True,
            "tracking_id": StringSubclass("private-tracking-id"),
        },
    )
    assert adversarial["dependency_reason"] == "TRANSPORT_CONNECTION_INTERRUPTED"
    assert "provider_observability" not in adversarial

    subclass_mapping = cutover._blocked_payload(
        finite,
        provider_meta=DictSubclass(
            service="SandboxService",
            method="GetSandboxOperationsByCursor",
        ),
    )
    assert "provider_observability" not in subclass_mapping

    unknown = cutover._blocked_payload(
        CL7RuntimeError(
            CL7RuntimeReason.BROKER_READ_FAILED,
            "PRIVATE_CANARY",
            stage="CL3_SYNC",
            retryable=True,
        ),
        provider_meta={
            "service": "SandboxService",
            "method": "GetSandboxOperationsByCursor",
        },
    )
    assert "dependency_reason" not in unknown
    assert "provider_observability" not in unknown

    wrong_stage = cutover._blocked_payload(
        CL7RuntimeError(
            CL7RuntimeReason.BROKER_READ_FAILED,
            BrokerReadReason.TRANSPORT_TIMEOUT.value,
            stage="CL4_OPENING",
            retryable=True,
        ),
        provider_meta={
            "service": "SandboxService",
            "method": "GetSandboxOperationsByCursor",
        },
    )
    assert "dependency_reason" not in wrong_stage
    assert "provider_observability" not in wrong_stage


@pytest.mark.parametrize(
    "unsafe_meta",
    [
        {
            "provider_error_code": "private prose",
            "provider_error_category": "REQUEST_REJECTED",
        },
        {
            "provider_error_code": "30014",
            "provider_error_category": "PRIVATE_CATEGORY",
        },
        {
            "provider_error_code": "30014",
            "provider_error_category": "REQUEST_REJECTED",
            "request_from_inclusive": "PRIVATE_FROM",
            "request_to_exclusive": "PRIVATE_TO",
        },
        {
            "provider_error_code": "30014",
            "provider_error_category": "REQUEST_REJECTED",
            "request_from_inclusive": "2026-09-16T16:39:12.000000000Z",
            "request_to_exclusive": "2026-09-16T16:39:11.000000000Z",
        },
        {
            "status_code": 500,
            "provider_error_code": "HTTP_400",
            "provider_error_category": "SERVER_REJECTED",
        },
        {
            "status_code": 500,
            "provider_error_code": "30014",
            "provider_error_category": "REQUEST_REJECTED",
        },
        {
            "provider_error_code": "30014",
            "provider_error_category": "REQUEST_REJECTED",
            "request_from_inclusive": "2026-02-30T00:00:00.000000000Z",
            "request_to_exclusive": "2026-03-01T00:00:00.000000000Z",
        },
    ],
)
def test_cl3_sync_observability_rejects_unsafe_error_and_boundary_fields(unsafe_meta):
    base = {
        "service": "SandboxService",
        "method": "GetSandboxOperationsByCursor",
        "status_code": 400,
        "transient": False,
        "attempt_count": 1,
    }
    payload = cutover._safe_cl3_provider_observability({**base, **unsafe_meta})
    assert (
        "provider_error_code" not in payload
        or payload["provider_error_code"] == "30014"
    )
    assert (
        "provider_error_category" not in payload
        or payload["provider_error_category"] == "REQUEST_REJECTED"
    )
    assert "request_from_inclusive" not in payload
    assert "request_to_exclusive" not in payload
    serialized = json.dumps(payload, sort_keys=True)
    assert "private prose" not in serialized
    assert "PRIVATE_" not in serialized


@pytest.mark.parametrize(
    "provider_meta",
    [
        {
            "service": "SandboxService",
            "method": "GetSandboxPortfolio",
            "status_code": 200,
            "error_class": "HTTPResponse",
            "transient": False,
            "attempt_count": 1,
            "tracking_id": "stale-opening-tracking-id",
        },
        {
            "service": "SandboxService",
            "status_code": 504,
            "error_class": "ReadTimeout",
            "transient": True,
            "attempt_count": 3,
            "tracking_id": "missing-method-tracking-id",
        },
        {
            "service": "OtherService",
            "method": "GetSandboxOperationsByCursor",
            "status_code": 503,
            "error_class": "ConnectTimeout",
            "transient": True,
            "attempt_count": 3,
            "tracking_id": "wrong-service-tracking-id",
        },
    ],
    ids=("stale-portfolio", "missing-method", "wrong-service"),
)
def test_cl3_sync_observability_requires_exact_service_method_pair(provider_meta):
    payload = cutover._blocked_payload(
        CL7RuntimeError(
            CL7RuntimeReason.BROKER_READ_FAILED,
            BrokerReadReason.CLOCK_FAILURE.value,
            stage="CL3_SYNC",
            retryable=True,
        ),
        provider_meta=provider_meta,
    )

    assert payload["dependency_reason"] == "CLOCK_FAILURE"
    assert "provider_observability" not in payload
    serialized = json.dumps(payload, sort_keys=True)
    assert "tracking-id" not in serialized


@pytest.mark.parametrize("exhausted", [False, True], ids=("recovers", "exhausted"))
def test_stage_b_cl3_transport_retries_only_exact_30070_without_effects(
    monkeypatch,
    exhausted,
):
    class Response:
        def __init__(self, *, ok, status_code, payload):
            self.ok = ok
            self.status_code = status_code
            self.payload = payload
            self.headers = {}
            self.history = ()
            self.text = "PRIVATE_PROVIDER_TEXT"

        def json(self):
            return self.payload

    failures = 3 if exhausted else 1
    outcomes = [
        Response(
            ok=False,
            status_code=400,
            payload={"message": "30070", "description": "PRIVATE_DESCRIPTION"},
        )
        for _ in range(failures)
    ]
    if not exhausted:
        outcomes.append(
            Response(
                ok=True,
                status_code=200,
                payload={"hasNext": False, "items": [], "nextCursor": ""},
            )
        )
    calls = []

    def post(url, **options):
        calls.append((url, json.loads(json.dumps(options["json"]))))
        return outcomes.pop(0)

    client = TBankSandboxClient("dummy-token", max_retries=0)
    monkeypatch.setattr(client._session, "post", post)
    monotonic_values = iter([0, 1, 2, 3, 4])
    waits = []
    effects = []
    request = cl3.BrokerReadRequest(
        environment=cl3.BrokerEnvironment.SANDBOX,
        raw_account_id=ACCOUNT,
        identity_key=b"k" * 32,
        identity_key_id="CL8_Q7_30070_TEST",
        from_inclusive="2026-09-16T19:39:41.218912001Z",
        to_exclusive="2026-09-16T19:39:41.295448000Z",
        limit=1000,
        max_pages=1,
        max_items=1,
        absolute_deadline_ns=60_000_000_000,
        retry_policy=cl3.RetryPolicy(
            max_attempts=3,
            per_attempt_timeout_ns=10_000_000_000,
            backoff_ns=(100_000_000, 500_000_000),
        ),
        transport=client.get_operations_by_cursor_once,
        monotonic_ns=lambda: next(monotonic_values),
        wait_ns=waits.append,
    )
    try:
        if exhausted:
            with pytest.raises(cl3.BrokerReadError) as captured:
                cl3.collect_tbank_operations(request)
            assert (
                captured.value.reason
                is cl3.BrokerReadReason.TRANSPORT_HTTP_RETRY_EXHAUSTED
            )
            assert effects == []
            assert waits == [100_000_000, 500_000_000]
        else:
            batch = cl3.collect_tbank_operations(request)
            effects.append(batch.watermark.to_exclusive)
            assert effects == ["2026-09-16T19:39:41.295448000Z"]
            assert waits == [100_000_000]
        expected_calls = 3 if exhausted else 2
        assert len(calls) == expected_calls
        assert all(call[1] == calls[0][1] for call in calls)
        assert all(call[0].endswith("/GetSandboxOperationsByCursor") for call in calls)
        assert all("PostSandboxOrder" not in call[0] for call in calls)
    finally:
        client.close()


def test_provider_to_cl4_money_normalization_is_exact_and_non_mutating():
    raw_cash = {"currency": "rub", "units": "49998", "nano": 383235000}
    untouched = {"currency": "rub", "units": "7", "nano": 0}
    raw = {
        "totalAmountCurrencies": raw_cash,
        "totalAmountPortfolio": untouched,
    }

    normalized = normalize_cl4_portfolio_response(raw)

    assert normalized is not raw
    assert normalized["totalAmountCurrencies"] == {
        "currency": "RUB",
        "units": "49998",
        "nano": 383235000,
    }
    assert normalized["totalAmountCurrencies"] is not raw_cash
    assert normalized["totalAmountPortfolio"] is untouched
    assert raw["totalAmountCurrencies"]["currency"] == "rub"

    proof = cl4.build_broker_cash_proof(
        normalized,
        account_scope_sha256="1" * 64,
        environment=cl4._BrokerEnvironment.SANDBOX,
        as_of="2026-09-16T00:00:00.000000000Z",
        evaluated_at="2026-09-16T00:00:00.000000000Z",
        response_complete=True,
        identity_key=b"synthetic-q7-money-normalization-key",
        identity_key_id="CL8_Q7_MONEY_TEST",
    )
    assert proof.cash.currency == "RUB"
    assert proof.cash.minor_units == 49_998_383_235_000


@pytest.mark.parametrize(
    ("cash", "normalizes"),
    [
        ({"currency": "RUB", "units": "1", "nano": 0}, False),
        ({"currency": "Rub", "units": "1", "nano": 0}, False),
        ({"currency": "rUB", "units": "1", "nano": 0}, False),
        ({"currency": "usd", "units": "1", "nano": 0}, False),
        ({"currency": 643, "units": "1", "nano": 0}, False),
        ({"units": "1", "nano": 0}, False),
        ({"currency": "rub", "units": 1, "nano": 0}, True),
        ({"currency": "rub", "units": "1", "nano": "0"}, True),
        ({"currency": "rub", "units": "1", "nano": 1_000_000_000}, True),
        ({"currency": "rub", "units": "1", "nano": 0, "extra": 0}, False),
    ],
)
def test_provider_to_cl4_money_normalization_does_not_over_accept(cash, normalizes):
    response = {"totalAmountCurrencies": cash}
    normalized = normalize_cl4_portfolio_response(response)
    if cash == {"currency": "RUB", "units": "1", "nano": 0}:
        assert normalized is response
        assert (
            cl4.build_broker_cash_proof(
                normalized,
                account_scope_sha256="2" * 64,
                environment=cl4._BrokerEnvironment.SANDBOX,
                as_of="2026-09-16T00:00:00.000000000Z",
                evaluated_at="2026-09-16T00:00:00.000000000Z",
                response_complete=True,
                identity_key=b"synthetic-q7-money-rejection-key",
                identity_key_id="CL8_Q7_MONEY_REJECTION",
            ).cash.currency
            == "RUB"
        )
        return
    if normalizes:
        assert normalized is not response
        assert normalized["totalAmountCurrencies"]["currency"] == "RUB"
    else:
        assert normalized is response
    with pytest.raises(cl4.CL4Error):
        cl4.build_broker_cash_proof(
            normalized,
            account_scope_sha256="2" * 64,
            environment=cl4._BrokerEnvironment.SANDBOX,
            as_of="2026-09-16T00:00:00.000000000Z",
            evaluated_at="2026-09-16T00:00:00.000000000Z",
            response_complete=True,
            identity_key=b"synthetic-q7-money-rejection-key",
            identity_key_id="CL8_Q7_MONEY_REJECTION",
        )


class _RubSubclass(str):
    pass


class _RubEqualitySpoof:
    def __eq__(self, other):
        return other == "rub"


@pytest.mark.parametrize(
    "currency",
    (_RubSubclass("rub"), _RubEqualitySpoof()),
    ids=("str-subclass", "custom-equality"),
)
def test_provider_to_cl4_money_normalization_requires_exact_string(currency):
    response = {
        "totalAmountCurrencies": {
            "currency": currency,
            "units": "1",
            "nano": 0,
        }
    }

    assert normalize_cl4_portfolio_response(response) is response
    with pytest.raises(cl4.CL4Error):
        cl4.build_broker_cash_proof(
            response,
            account_scope_sha256="4" * 64,
            environment=cl4._BrokerEnvironment.SANDBOX,
            as_of="2026-09-16T00:00:00.000000000Z",
            evaluated_at="2026-09-16T00:00:00.000000000Z",
            response_complete=True,
            identity_key=b"synthetic-q7-money-rejection-key",
            identity_key_id="CL8_Q7_MONEY_REJECTION",
        )


def test_provider_to_cl4_money_normalization_rejects_container_substitution():
    class DictSubclass(dict):
        pass

    values = (
        None,
        [],
        DictSubclass(
            totalAmountCurrencies={"currency": "rub", "units": "1", "nano": 0}
        ),
        {"totalAmountCurrencies": []},
        {
            "totalAmountCurrencies": DictSubclass(
                currency="rub",
                units="1",
                nano=0,
            )
        },
    )
    for value in values:
        assert normalize_cl4_portfolio_response(value) is value


def test_cl4_money_normalizing_provider_delegates_all_other_calls():
    class Delegate:
        def __init__(self):
            self.response = {
                "totalAmountCurrencies": {
                    "currency": "rub",
                    "units": "5",
                    "nano": 0,
                }
            }
            self.last_response_meta = {"method": "GetSandboxPortfolio"}
            self.calls = []

        def get_portfolio(self, account_id):
            self.calls.append(("get_portfolio", account_id))
            return self.response

        def get_withdraw_limits(self, account_id):
            self.calls.append(("get_withdraw_limits", account_id))
            return _withdraw_limits_transport_observation(
                {"money": [], "blocked": [], "blockedGuarantee": []},
                account_id=account_id,
            )

    delegate = Delegate()
    provider = CL4MoneyNormalizingTransport(delegate)

    assert (
        provider.get_portfolio("synthetic-account")["totalAmountCurrencies"]["currency"]
        == "RUB"
    )
    observation = provider.get_withdraw_limits("synthetic-account")
    assert type(observation) is cl5.WithdrawLimitsTransportObservation
    assert observation.raw_request_account_id == "synthetic-account"
    assert provider.last_response_meta == {"method": "GetSandboxPortfolio"}
    assert delegate.response["totalAmountCurrencies"]["currency"] == "rub"
    assert delegate.calls == [
        ("get_portfolio", "synthetic-account"),
        ("get_withdraw_limits", "synthetic-account"),
    ]


def test_cutover_provider_uses_the_same_normalizing_transport(monkeypatch):
    class Delegate:
        pass

    delegate = Delegate()

    def factory(*, token, max_retries):
        assert token == "synthetic-token"
        assert max_retries == 0
        return delegate

    monkeypatch.setattr(cutover, "TBankSandboxClient", factory)
    provider = cutover._provider("synthetic-token")

    assert isinstance(provider, CL4MoneyNormalizingTransport)
    assert provider._delegate is delegate
