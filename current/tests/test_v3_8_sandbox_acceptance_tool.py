from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from tools import v3_8_sandbox_acceptance as acceptance
from trading_robot.sandbox_execution_adapter import (
    SandboxDispatchResult,
    SandboxInspectionResult,
)

ACCOUNT = "sandbox-account-1"
INTENT = "11111111-1111-1111-1111-111111111111"


class FakeManager:
    def __init__(self, _store, *, account_id: str) -> None:
        assert account_id == ACCOUNT
        self.account_id = account_id
        self._state = SimpleNamespace(
            account_id=account_id,
            revision=3,
            reserved_cash_kopecks=101_000,
            blocking_intent=None,
            queued=(
                SimpleNamespace(
                    intent_id=INTENT,
                    queue_sequence=1,
                    candidate=SimpleNamespace(
                        ticker="SBER",
                        instrument_id="uid-sber",
                        direction="BUY",
                        requested_lots=1,
                        current_lots=0,
                        strategy_id="sma",
                        strategy_profile_hash="c" * 64,
                        candle_interval="CANDLE_INTERVAL_HOUR",
                        candle_time="2026-08-13T12:00:00+00:00",
                    ),
                ),
            ),
        )

    def state(self):
        return self._state

    def recover_after_restart(self):
        return None


class FakeRepository:
    def __init__(self, _path) -> None:
        pass

    def load(self, *, expected_account_id: str):
        assert expected_account_id == ACCOUNT
        return SimpleNamespace(account_id=ACCOUNT)


def prepare_tool(monkeypatch, tmp_path):
    for name in (
        "central_order_state.json",
        "portfolio_state.json",
        "multi_instrument_profiles.json",
        "instrument_runtimes.json",
    ):
        (tmp_path / name).write_text("{}", encoding="utf-8")
        (tmp_path / f"{name}.sha256").write_text("0" * 64, encoding="ascii")
    monkeypatch.setattr(acceptance, "CentralOrderStore", lambda path: path)
    monkeypatch.setattr(acceptance, "CentralOrderManager", FakeManager)
    monkeypatch.setattr(acceptance, "PortfolioRepository", FakeRepository)
    monkeypatch.setattr(
        acceptance,
        "_assert_runtime_ready_for_dispatch",
        lambda *a, **k: None,
    )


def args(tmp_path, action: str, *extra: str):
    return acceptance.parse_args(
        [
            action,
            "--runtime-dir",
            str(tmp_path),
            "--account-id",
            ACCOUNT,
            *extra,
        ]
    )


def test_status_is_offline_and_never_reads_secret(monkeypatch, tmp_path):
    prepare_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(
        acceptance,
        "preferred_secret_provider",
        lambda _path: pytest.fail("secret provider must not be read"),
    )

    result = acceptance.run(
        args(tmp_path, "status"),
        environ={},
        client_factory=lambda *a, **k: pytest.fail("network client created"),
    )

    assert result["status"] == "READY"
    assert result["queued"][0]["intent_id"] == INTENT
    assert result["account_fingerprint"] == acceptance._fingerprint(ACCOUNT)
    assert ACCOUNT not in json.dumps(result)


def test_idle_inspection_is_offline(monkeypatch, tmp_path):
    prepare_tool(monkeypatch, tmp_path)
    original = acceptance.CentralOrderManager

    class IdleManager(original):
        def __init__(self, store, *, account_id):
            super().__init__(store, account_id=account_id)
            self._state.blocking_intent = None
            self._state.queued = ()

    monkeypatch.setattr(acceptance, "CentralOrderManager", IdleManager)

    result = acceptance.run(
        args(tmp_path, "inspect"),
        environ={},
        client_factory=lambda *a, **k: pytest.fail("network client created"),
    )

    assert result == {"action": "inspect", "status": "IDLE"}


def test_dispatch_requires_environment_arm_before_secret_or_network(
    monkeypatch,
    tmp_path,
):
    prepare_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(
        acceptance,
        "preferred_secret_provider",
        lambda _path: pytest.fail("secret provider must not be read"),
    )

    with pytest.raises(RuntimeError, match=acceptance.ARM_ENV_NAME):
        acceptance.run(
            args(
                tmp_path,
                "dispatch-one",
                "--intent-id",
                INTENT,
                "--confirm",
                "ENABLE V3.8 SANDBOX EXECUTION",
            ),
            environ={},
            client_factory=lambda *a, **k: pytest.fail("network client created"),
        )


def test_prepare_requires_exact_confirmation_before_secret_or_network(
    monkeypatch,
    tmp_path,
):
    prepare_tool(monkeypatch, tmp_path)
    monkeypatch.setattr(
        acceptance,
        "preferred_secret_provider",
        lambda _path: pytest.fail("secret provider must not be read"),
    )

    with pytest.raises(RuntimeError, match="exact intent preparation"):
        acceptance.run(
            args(
                tmp_path,
                "prepare-one",
                "--instrument-id",
                "uid-sber",
                "--confirm",
                "WRONG",
            ),
            environ={},
            client_factory=lambda *a, **k: pytest.fail("network client created"),
        )


def test_confirmed_prepare_routes_one_instrument_without_dispatch_arm(
    monkeypatch,
    tmp_path,
):
    prepare_tool(monkeypatch, tmp_path)
    captured = {}

    class FakeClient:
        def __init__(self, token, **kwargs):
            captured["token"] = token

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def fake_prepare(**kwargs):
        captured["prepare"] = kwargs
        return {
            "action": "prepare-one",
            "status": "QUEUED",
            "intent_id": INTENT,
        }

    monkeypatch.setattr(acceptance, "_prepare_one", fake_prepare)
    result = acceptance.run(
        args(
            tmp_path,
            "prepare-one",
            "--instrument-id",
            "uid-sber",
            "--confirm",
            acceptance.PREPARE_CONFIRMATION,
        ),
        environ={acceptance.TOKEN_KEY: "secret-canary"},
        client_factory=FakeClient,
    )

    assert result["status"] == "QUEUED"
    assert captured["prepare"]["instrument_id"] == "uid-sber"
    assert captured["token"] == "secret-canary"
    assert "secret-canary" not in str(result)


def test_dispatch_rejects_non_head_intent_before_network(monkeypatch, tmp_path):
    prepare_tool(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="queue head"):
        acceptance.run(
            args(
                tmp_path,
                "dispatch-one",
                "--intent-id",
                "22222222-2222-2222-2222-222222222222",
                "--confirm",
                "ENABLE V3.8 SANDBOX EXECUTION",
            ),
            environ={acceptance.ARM_ENV_NAME: "YES"},
            client_factory=lambda *a, **k: pytest.fail("network client created"),
        )


def test_fully_armed_dispatch_passes_exact_intent_without_exposing_token(
    monkeypatch,
    tmp_path,
):
    prepare_tool(monkeypatch, tmp_path)
    captured = {}

    class FakeClient:
        def __init__(self, token, **kwargs):
            captured["token"] = token
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    class FakeAdapter:
        def __init__(self, client, manager, policy, *, risk_runtime=None):
            captured["policy"] = policy
            captured["risk_runtime"] = risk_runtime

        def dispatch_next(self, repository, *, expected_intent_id=None):
            captured["intent_id"] = expected_intent_id
            return SandboxDispatchResult(
                status="SUBMITTED",
                intent_id=expected_intent_id,
                order_was_sent=True,
            )

    monkeypatch.setattr(acceptance, "SandboxExecutionAdapter", FakeAdapter)
    result = acceptance.run(
        args(
            tmp_path,
            "dispatch-one",
            "--intent-id",
            INTENT,
            "--confirm",
            "ENABLE V3.8 SANDBOX EXECUTION",
        ),
        environ={
            acceptance.ARM_ENV_NAME: "YES",
            acceptance.TOKEN_KEY: "secret-canary",
        },
        client_factory=FakeClient,
    )

    assert result["status"] == "SUBMITTED"
    assert captured["intent_id"] == INTENT
    assert captured["policy"].armed
    assert captured["risk_runtime"].mode == "SANDBOX_EXECUTION"
    assert captured["token"] == "secret-canary"
    assert "secret-canary" not in str(result)


def test_reconcile_requires_terminal_broker_proof_and_records_risk(
    monkeypatch,
    tmp_path,
):
    prepare_tool(monkeypatch, tmp_path)
    captured = {}

    class ReconcileManager(FakeManager):
        def __init__(self, store, *, account_id):
            super().__init__(store, account_id=account_id)
            self._state.blocking_intent = self._state.queued[0]
            self._state.blocking_intent.status = "SUBMITTED"
            self._state.queued = ()

        def mark_reconciled(self, intent_id, **kwargs):
            captured["reconcile"] = (intent_id, kwargs)
            return SimpleNamespace(
                status="RECONCILED",
                intent_id=intent_id,
                outcome=kwargs["outcome"],
                executed_lots=kwargs["executed_lots"],
                reconciled_portfolio_revision=4,
                reconciled_portfolio_snapshot_at="2026-08-13T18:00:00+00:00",
                risk_execution_status="RECORDED",
                risk_execution_id=intent_id,
            )

    class FakeClient:
        def __init__(self, token, **kwargs):
            captured["token"] = token

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    class FakeAdapter:
        def __init__(self, client, manager, policy):
            pass

        def inspect_blocking_order(self):
            return SandboxInspectionResult(
                status="ORDER_OBSERVED",
                intent_id=INTENT,
                provider_status="FILL",
                executed_lots=1,
                terminal=True,
                suggested_reconciliation_outcome="FILLED",
                execution_price_rub=100.25,
                execution_price_source="executedOrderPrice",
            )

    class FakeCanonicalManager:
        def __init__(self, client, account_id, **kwargs):
            assert account_id == ACCOUNT

        def stage_confirmed_target(self, **kwargs):
            captured["staged_target"] = kwargs

        def refresh(self, *, record_event):
            captured["canonical_refreshed"] = record_event
            return SimpleNamespace(state_status="READY")

    fake_risk = SimpleNamespace(account_id=ACCOUNT, mode="SANDBOX_EXECUTION")
    monkeypatch.setattr(acceptance, "CentralOrderManager", ReconcileManager)
    monkeypatch.setattr(acceptance, "SandboxExecutionAdapter", FakeAdapter)
    monkeypatch.setattr(
        acceptance,
        "CanonicalPortfolioManager",
        FakeCanonicalManager,
    )
    monkeypatch.setattr(
        acceptance,
        "_sync_runtime_execution_state",
        lambda *a, **k: captured.setdefault("runtime_synced", k),
    )
    monkeypatch.setattr(
        acceptance.RiskRuntimeAdapter,
        "from_directory",
        lambda *a, **k: fake_risk,
    )

    result = acceptance.run(
        args(
            tmp_path,
            "reconcile",
            "--intent-id",
            INTENT,
            "--confirm",
            acceptance.RECONCILIATION_CONFIRMATION,
        ),
        environ={acceptance.TOKEN_KEY: "secret-canary"},
        client_factory=FakeClient,
    )

    assert result["status"] == "RECONCILED"
    assert result["risk_execution_status"] == "RECORDED"
    reconcile_kwargs = captured["reconcile"][1]
    assert reconcile_kwargs["risk_runtime"] is fake_risk
    assert reconcile_kwargs["execution_price_rub"] == 100.25
    assert captured["staged_target"]["target_lots"] == 1
    assert captured["canonical_refreshed"] is True
    assert captured["runtime_synced"]["actual_lots"] == 1
    assert "secret-canary" not in str(result)
