import queue
from types import SimpleNamespace
from unittest.mock import Mock

import desktop_gui
from desktop_gui import TradingRobotGUI
from trading_robot.gui_resilience import (
    TransientBackoff,
    describe_background_error,
)
from trading_robot.tbank_sandbox import TBankAPIError


class _Variable:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def _queue_host(*, queue_alive: bool = False) -> SimpleNamespace:
    scheduled: list[tuple[int, object]] = []
    host = SimpleNamespace(
        ui_queue=queue.Queue(),
        logger=Mock(),
        bt_status=_Variable(),
        sb_status=_Variable(),
        diag_status=_Variable(),
        _background_error_notice_at={},
        _refresh_events=lambda: None,
        _append_log=lambda line: None,
        _show_sandbox_result=lambda result: None,
        _set_sb_config_locked=lambda locked: None,
        winfo_exists=lambda: queue_alive,
        after=lambda delay, callback: scheduled.append((delay, callback)),
        scheduled=scheduled,
    )
    host._process_ui_queue = lambda: TradingRobotGUI._process_ui_queue(host)
    return host


def test_describe_http_500_is_transient_and_safe() -> None:
    exc = TBankAPIError(
        "raw server payload that must not be shown",
        status_code=500,
        details={"requestId": "req-123", "secret": "do-not-show"},
        transient=True,
        service="SandboxService",
        method="GetSandboxPortfolio",
    )

    info = describe_background_error(exc)

    assert info.transient is True
    assert info.status_code == 500
    assert info.request_id == "req-123"
    assert info.dedup_key == "tbank:500:SandboxService:GetSandboxPortfolio"
    assert "do-not-show" not in info.summary
    assert "raw server payload" not in info.summary
    assert "GetSandboxPortfolio" in info.summary


def test_describe_connection_failure_is_transient() -> None:
    exc = TBankAPIError(
        "DNS failure",
        transient=True,
        service="MarketDataService",
        method="GetTradingStatus",
    )

    info = describe_background_error(exc)

    assert info.transient is True
    assert info.status_code is None
    assert "ошибка соединения" in info.summary


def test_describe_non_transient_api_error_remains_operator_visible() -> None:
    exc = TBankAPIError(
        "bad request",
        status_code=400,
        details={"requestId": "req-400"},
        transient=False,
        service="SandboxService",
        method="PostSandboxOrder",
    )

    info = describe_background_error(exc)

    assert info.transient is False
    assert "отклонил запрос" in info.summary


def test_transient_backoff_grows_and_caps() -> None:
    backoff = TransientBackoff(base_seconds=30, max_seconds=120)

    assert [backoff.record_failure() for _ in range(5)] == [30, 60, 120, 120, 120]


def test_transient_backoff_honours_retry_after_and_resets() -> None:
    backoff = TransientBackoff(base_seconds=30, max_seconds=600)

    assert backoff.record_failure(retry_after_seconds=90) == 90
    assert backoff.record_failure() == 60
    backoff.reset()
    assert backoff.record_failure() == 30


def test_gui_conn_01_success_notice_precedes_failing_secondary_refresh(
    monkeypatch,
) -> None:
    events: list[str] = []

    def fail_secondary_refresh(result) -> None:
        del result
        events.append("secondary-refresh")
        raise RuntimeError("render failed")

    host = SimpleNamespace(
        _get_token=lambda: "synthetic-token",
        _get_ca_bundle=lambda: None,
        _begin_sandbox_operation=lambda name: True,
        sb_status=SimpleNamespace(
            set=lambda value: events.append(f"status:{value}")
        ),
        _set_account_records=lambda accounts: events.append("accounts"),
        _show_sandbox_result=fail_secondary_refresh,
        logger=Mock(),
        _end_sandbox_operation=lambda: events.append("finally"),
    )

    def run_background(function, on_success, on_finally=None, **kwargs) -> None:
        del function, kwargs
        on_success([{"id": "synthetic-account"}])
        if on_finally is not None:
            on_finally()

    host._run_background = run_background
    monkeypatch.setattr(
        desktop_gui.messagebox,
        "showinfo",
        lambda title, message, parent: events.append(f"modal:{title}"),
    )

    TradingRobotGUI._check_sandbox_connection(host)

    assert events[:4] == [
        "status:Проверяю подключение…",
        "status:Подключено: найдено счетов — 1",
        "modal:Подключение успешно",
        "accounts",
    ]
    assert events[-2:] == ["secondary-refresh", "finally"]
    host.logger.exception.assert_called_once_with(
        "Connection succeeded but secondary GUI refresh failed"
    )


def test_gui_conn_02_error_notice_precedes_failing_event_refresh(
    monkeypatch,
) -> None:
    events: list[str] = []
    host = _queue_host()

    def failing_refresh() -> None:
        events.append("event-refresh")
        raise RuntimeError("journal unavailable")

    host._refresh_events = failing_refresh
    monkeypatch.setattr(
        desktop_gui.messagebox,
        "showwarning",
        lambda title, message, parent: events.append(f"modal:{title}"),
    )
    error = TBankAPIError(
        "synthetic connection failure",
        transient=True,
        service="SandboxService",
        method="GetSandboxAccounts",
    )
    host.ui_queue.put(("background_error", (error, None, True, "connection")))

    TradingRobotGUI._process_ui_queue(host)

    assert events == ["modal:T-Invest временно недоступен", "event-refresh"]
    host.logger.exception.assert_called_once_with(
        "Event refresh failed after background error context=%s",
        "connection",
    )


def test_gui_conn_03_callback_failure_keeps_dispatcher_alive() -> None:
    host = _queue_host(queue_alive=True)
    processed: list[str] = []

    def failing_callback(result) -> None:
        del result
        raise RuntimeError("callback failed")

    host.ui_queue.put(("callback", (failing_callback, object())))
    host.ui_queue.put(
        ("callback", (lambda result: processed.append(result), "next-item"))
    )

    TradingRobotGUI._process_ui_queue(host)

    assert processed == ["next-item"]
    host.logger.exception.assert_called_once_with(
        "UI queue item failed kind=%s",
        "callback",
    )
    assert host.scheduled == [(100, host._process_ui_queue)]


def test_gui_conn_04_finally_remains_reachable_after_callback_failure() -> None:
    host = _queue_host()
    finally_called: list[bool] = []

    def failing_callback(result) -> None:
        del result
        raise RuntimeError("callback failed")

    host.ui_queue.put(("callback", (failing_callback, object())))
    host.ui_queue.put(("finally", lambda: finally_called.append(True)))

    TradingRobotGUI._process_ui_queue(host)

    assert finally_called == [True]
    host.logger.exception.assert_called_once_with(
        "UI queue item failed kind=%s",
        "callback",
    )


def test_gui_conn_05_two_checks_possible_after_render_failure(monkeypatch) -> None:
    host = _queue_host()
    host.robot_thread = None
    host.sandbox_task_active = False
    host.sandbox_task_name = ""
    host._get_token = lambda: "synthetic-token"
    host._get_ca_bundle = lambda: None
    host._begin_sandbox_operation = lambda name: (
        TradingRobotGUI._begin_sandbox_operation(host, name)
    )
    host._end_sandbox_operation = lambda: TradingRobotGUI._end_sandbox_operation(
        host
    )
    host._set_account_records = lambda accounts: None
    render_attempts: list[int] = []

    def render(result) -> None:
        del result
        render_attempts.append(len(render_attempts) + 1)
        if len(render_attempts) == 1:
            raise RuntimeError("first render failed")

    host._show_sandbox_result = render

    def run_background(function, on_success, on_finally=None, **kwargs) -> None:
        del function, kwargs
        host.ui_queue.put(("callback", (on_success, [{"id": "synthetic"}])))
        if on_finally is not None:
            host.ui_queue.put(("finally", on_finally))

    host._run_background = run_background
    notices: list[str] = []
    monkeypatch.setattr(
        desktop_gui.messagebox,
        "showinfo",
        lambda title, message, parent: notices.append(title),
    )

    TradingRobotGUI._check_sandbox_connection(host)
    assert host.sandbox_task_active is True
    TradingRobotGUI._process_ui_queue(host)
    assert host.sandbox_task_active is False
    assert host.sandbox_task_name == ""

    TradingRobotGUI._check_sandbox_connection(host)
    assert host.sandbox_task_active is True
    TradingRobotGUI._process_ui_queue(host)

    assert host.sandbox_task_active is False
    assert host.sandbox_task_name == ""
    assert notices == ["Подключение успешно", "Подключение успешно"]
    assert render_attempts == [1, 2]
