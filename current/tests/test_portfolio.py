from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_robot.portfolio import (
    OwnershipRecoveryRequest,
    PortfolioManager,
)


class FakePortfolioAPI:
    def __init__(self, lots: int = 1) -> None:
        self.current_lots = lots
        self.orders: list[dict] = []

    def find_instrument(self, ticker: str, class_code: str):
        return {
            "ticker": ticker,
            "classCode": class_code,
            "uid": "uid-sber",
            "figi": "figi-sber",
        }

    @staticmethod
    def instrument_id(instrument):
        return instrument["uid"]

    def get_portfolio(self, account_id: str):
        positions = []
        if self.current_lots:
            positions.append(
                {
                    "instrumentUid": "uid-sber",
                    "figi": "figi-sber",
                    "ticker": "SBER",
                    "instrumentType": "share",
                    "quantity": {"units": str(self.current_lots * 10), "nano": 0},
                    "quantityLots": {"units": str(self.current_lots), "nano": 0},
                    "averagePositionPrice": {
                        "currency": "rub",
                        "units": "250",
                        "nano": 0,
                    },
                    "currentPrice": {
                        "currency": "rub",
                        "units": "260",
                        "nano": 0,
                    },
                    "expectedYield": {"units": "100", "nano": 0},
                }
            )
        return {
            "totalAmountPortfolio": {
                "currency": "rub",
                "units": "102600",
                "nano": 0,
            },
            "totalAmountShares": {
                "currency": "rub",
                "units": "2600",
                "nano": 0,
            },
            "expectedYield": {"units": "100", "nano": 0},
            "positions": positions,
        }

    def get_positions(self, account_id: str):
        return {
            "money": [
                {"currency": "rub", "units": "100000", "nano": 0}
            ]
        }

    def get_orders(self, account_id: str):
        return list(self.orders)

    @staticmethod
    def position_lots(portfolio, instrument):
        positions = portfolio.get("positions", [])
        if not positions:
            return 0
        return int(positions[0]["quantityLots"]["units"])


def state_with_decision(
    *,
    config_hash: str,
    active_owner: bool,
    target_lots: int = 1,
) -> dict:
    scope = "account-1|SBER_TQBR"
    state_key = (
        f"{scope}|CANDLE_INTERVAL_HOUR|primary:sma:{config_hash[:16]}"
    )
    state = {
        "version": 5,
        "bots": {
            state_key: {
                "instrument_id": "uid-sber",
                "last_seen_candle": "2026-07-22T10:00:00+00:00",
                "last_strategy_decisions": {
                    "sma": {
                        "strategy_id": "sma",
                        "strategy_version": "1.0",
                        "config_hash": config_hash,
                        "candle_time": "2026-07-22T10:00:00+00:00",
                        "target_lots": target_lots,
                    }
                },
            }
        },
        "execution_scopes": {scope: {}},
    }
    if active_owner:
        state["execution_scopes"][scope]["active_primary"] = {
            "strategy_id": "sma",
            "config_hash": config_hash,
            "candle_interval": "CANDLE_INTERVAL_HOUR",
        }
    return state


def manager(tmp_path: Path, api: FakePortfolioAPI, state: dict) -> PortfolioManager:
    state_path = tmp_path / "robot_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return PortfolioManager(
        api,
        "account-1",
        state_file=state_path,
        journal_file=tmp_path / "events.db",
    )


def test_portfolio_snapshot_shows_owner_target_cash_and_pnl(tmp_path: Path):
    config_hash = "a" * 64
    api = FakePortfolioAPI(lots=1)
    service = manager(
        tmp_path,
        api,
        state_with_decision(config_hash=config_hash, active_owner=True),
    )
    snapshot = service.snapshot()
    assert snapshot.cash_rub == 100000
    assert snapshot.total_value == 102600
    assert len(snapshot.positions) == 1
    position = snapshot.positions[0]
    assert position.ticker == "SBER"
    assert position.quantity_lots == 1
    assert position.target_lots == 1
    assert position.owner_strategy == "sma"
    assert position.ownership_status == "ATTRIBUTED"
    assert position.reconciliation_status == "MATCH"
    assert position.expected_yield == 100


def test_portfolio_snapshot_marks_unattributed_position(tmp_path: Path):
    api = FakePortfolioAPI(lots=1)
    service = manager(
        tmp_path,
        api,
        state_with_decision(config_hash="b" * 64, active_owner=False),
    )
    snapshot = service.snapshot()
    assert snapshot.unattributed_positions == 1
    assert snapshot.positions[0].ownership_status == "UNATTRIBUTED"
    assert snapshot.positions[0].reconciliation_status == "UNATTRIBUTED"


def test_ownership_recovery_requires_matching_decision_evidence(tmp_path: Path):
    config_hash = "c" * 64
    api = FakePortfolioAPI(lots=1)
    service = manager(
        tmp_path,
        api,
        state_with_decision(
            config_hash=config_hash,
            active_owner=False,
            target_lots=0,
        ),
    )
    request = OwnershipRecoveryRequest(
        account_id="account-1",
        ticker="SBER",
        class_code="TQBR",
        candle_interval="CANDLE_INTERVAL_HOUR",
        primary_strategy="sma",
        primary_config_hash=config_hash,
        strategy_suite_hash="suite",
        shadow_strategies=("donchian",),
        expected_lots=1,
        confirmation_text="ADOPT SBER 1",
    )
    with pytest.raises(RuntimeError, match="No matching persisted PRIMARY decision"):
        service.recover_ownership(request)


def test_ownership_recovery_writes_audited_owner(tmp_path: Path):
    config_hash = "d" * 64
    api = FakePortfolioAPI(lots=1)
    service = manager(
        tmp_path,
        api,
        state_with_decision(config_hash=config_hash, active_owner=False),
    )
    request = OwnershipRecoveryRequest(
        account_id="account-1",
        ticker="SBER",
        class_code="TQBR",
        candle_interval="CANDLE_INTERVAL_HOUR",
        primary_strategy="sma",
        primary_config_hash=config_hash,
        strategy_suite_hash="suite",
        shadow_strategies=("donchian", "ensemble"),
        expected_lots=1,
        confirmation_text="ADOPT SBER 1",
    )
    result = service.recover_ownership(request)
    state = json.loads((tmp_path / "robot_state.json").read_text(encoding="utf-8"))
    owner = state["execution_scopes"]["account-1|SBER_TQBR"]["active_primary"]
    assert result["status"] == "ownership_recovered"
    assert owner["strategy_id"] == "sma"
    assert owner["ownership_recovered"] is True
    assert owner["confirmed_lots"] == 1


def test_portfolio_snapshot_handles_empty_portfolio(tmp_path: Path):
    api = FakePortfolioAPI(lots=0)
    service = manager(
        tmp_path,
        api,
        {"version": 5, "bots": {}, "execution_scopes": {}},
    )
    snapshot = service.snapshot()
    assert snapshot.positions == ()
    assert snapshot.cash_rub == 100000
    assert snapshot.unattributed_positions == 0
    assert snapshot.reconciliation_mismatches == 0


def test_portfolio_snapshot_handles_multiple_instruments(tmp_path: Path):
    class MultiAssetAPI(FakePortfolioAPI):
        def get_portfolio(self, account_id: str):
            payload = super().get_portfolio(account_id)
            payload["positions"].append(
                {
                    "instrumentUid": "uid-bond",
                    "figi": "figi-bond",
                    "ticker": "BOND1",
                    "classCode": "TQOB",
                    "instrumentType": "bond",
                    "quantity": {"units": "2", "nano": 0},
                    "quantityLots": {"units": "2", "nano": 0},
                    "averagePositionPrice": {
                        "currency": "rub",
                        "units": "980",
                        "nano": 0,
                    },
                    "currentPrice": {
                        "currency": "rub",
                        "units": "990",
                        "nano": 0,
                    },
                    "expectedYield": {"units": "20", "nano": 0},
                }
            )
            return payload

    config_hash = "e" * 64
    service = manager(
        tmp_path,
        MultiAssetAPI(lots=1),
        state_with_decision(config_hash=config_hash, active_owner=True),
    )
    snapshot = service.snapshot()
    rows = {row.ticker: row for row in snapshot.positions}
    assert set(rows) == {"SBER", "BOND1"}
    assert rows["SBER"].ownership_status == "ATTRIBUTED"
    assert rows["BOND1"].ownership_status == "UNATTRIBUTED"
    assert rows["BOND1"].reconciliation_status == "UNATTRIBUTED"
    assert snapshot.unattributed_positions == 1


def test_portfolio_target_comes_from_active_owner_not_dict_order(tmp_path: Path):
    owner_hash = "f" * 64
    other_hash = "1" * 64
    state = state_with_decision(config_hash=owner_hash, active_owner=True)
    scope = "account-1|SBER_TQBR"
    other_key = (
        f"{scope}|CANDLE_INTERVAL_10_MIN|primary:donchian:{other_hash[:16]}"
    )
    # Insert a later, incompatible state for the same instrument. Portfolio View
    # must still use the owner-matching hourly SMA target.
    state["bots"][other_key] = {
        "instrument_id": "uid-sber",
        "last_strategy_decisions": {
            "donchian": {
                "strategy_id": "donchian",
                "config_hash": other_hash,
                "candle_time": "2026-07-22T11:00:00+00:00",
                "target_lots": 0,
            }
        },
    }
    service = manager(tmp_path, FakePortfolioAPI(lots=1), state)
    position = service.snapshot().positions[0]
    assert position.owner_strategy == "sma"
    assert position.owner_interval == "CANDLE_INTERVAL_HOUR"
    assert position.target_lots == 1
    assert position.reconciliation_status == "MATCH"


def test_ownership_recovery_rejects_already_attributed_position(tmp_path: Path):
    config_hash = "e" * 64
    api = FakePortfolioAPI(lots=1)
    service = manager(
        tmp_path,
        api,
        state_with_decision(config_hash=config_hash, active_owner=True),
    )
    request = OwnershipRecoveryRequest(
        account_id="account-1",
        ticker="SBER",
        class_code="TQBR",
        candle_interval="CANDLE_INTERVAL_HOUR",
        primary_strategy="sma",
        primary_config_hash=config_hash,
        strategy_suite_hash="suite",
        shadow_strategies=(),
        expected_lots=1,
        confirmation_text="ADOPT SBER 1",
    )
    with pytest.raises(RuntimeError, match="already has an active PRIMARY owner"):
        service.recover_ownership(request)


def test_ownership_recovery_rejects_instrument_uid_mismatch(tmp_path: Path):
    config_hash = "f" * 64
    api = FakePortfolioAPI(lots=1)
    state = state_with_decision(config_hash=config_hash, active_owner=False)
    bot_state = next(iter(state["bots"].values()))
    bot_state["instrument_id"] = "different-uid"
    service = manager(tmp_path, api, state)
    request = OwnershipRecoveryRequest(
        account_id="account-1",
        ticker="SBER",
        class_code="TQBR",
        candle_interval="CANDLE_INTERVAL_HOUR",
        primary_strategy="sma",
        primary_config_hash=config_hash,
        strategy_suite_hash="suite",
        shadow_strategies=(),
        expected_lots=1,
        confirmation_text="ADOPT SBER 1",
    )
    with pytest.raises(RuntimeError, match="instrument UID differs"):
        service.recover_ownership(request)


def test_portfolio_resolves_missing_ticker_from_instrument_uid(tmp_path: Path):
    class MetadataAPI(FakePortfolioAPI):
        def __init__(self) -> None:
            super().__init__(lots=1)
            self.metadata_calls: list[tuple[str, str]] = []

        def get_portfolio(self, account_id: str):
            payload = super().get_portfolio(account_id)
            position = payload["positions"][0]
            position.pop("ticker", None)
            position.pop("classCode", None)
            position.pop("figi", None)
            return payload

        def get_instrument_by_id(self, identifier: str, *, id_type: str):
            self.metadata_calls.append((identifier, id_type))
            return {
                "uid": "uid-sber",
                "figi": "figi-sber",
                "ticker": "SBER",
                "classCode": "TQBR",
                "instrumentType": "share",
                "currency": "rub",
            }

    api = MetadataAPI()
    service = manager(
        tmp_path,
        api,
        {"version": 5, "bots": {}, "execution_scopes": {}},
    )
    position = service.snapshot().positions[0]
    assert position.ticker == "SBER"
    assert position.class_code == "TQBR"
    assert position.figi == "figi-sber"
    assert position.asset_type == "share"
    assert api.metadata_calls == [
        ("uid-sber", "INSTRUMENT_ID_TYPE_UID")
    ]


def test_portfolio_snapshot_excludes_cash_currency_from_ownership(tmp_path: Path):
    class CurrencyPortfolioAPI(FakePortfolioAPI):
        def __init__(self) -> None:
            super().__init__(lots=0)

        def get_portfolio(self, account_id: str):
            return {
                "totalAmountPortfolio": {
                    "currency": "rub",
                    "units": "49998",
                    "nano": 383235000,
                },
                "totalAmountCurrencies": {
                    "currency": "rub",
                    "units": "49998",
                    "nano": 383235000,
                },
                "totalAmountShares": {
                    "currency": "rub",
                    "units": "0",
                    "nano": 0,
                },
                "expectedYield": {
                    "currency": "rub",
                    "units": "0",
                    "nano": 0,
                },
                "positions": [
                    {
                        "instrumentUid": "uid-rub",
                        "positionUid": "uid-rub-position",
                        "figi": "RUB000UTSTOM",
                        "ticker": "RUB000UTSTOM",
                        "classCode": "CETS",
                        "instrumentType": "currency",
                        "quantity": {"units": "49998", "nano": 383235000},
                        "quantityLots": {"units": "49998", "nano": 0},
                        "averagePositionPrice": {
                            "currency": "rub",
                            "units": "1",
                            "nano": 0,
                        },
                        "currentPrice": {
                            "currency": "rub",
                            "units": "1",
                            "nano": 0,
                        },
                    }
                ],
            }

        def get_positions(self, account_id: str):
            return {
                "money": [
                    {"currency": "rub", "units": "49998", "nano": 383235000}
                ]
            }

    service = manager(
        tmp_path,
        CurrencyPortfolioAPI(),
        {"version": 5, "bots": {}, "execution_scopes": {}},
    )
    snapshot = service.snapshot()
    assert snapshot.cash_rub == pytest.approx(49998.383235)
    assert snapshot.positions == ()
    assert snapshot.unattributed_positions == 0
    assert snapshot.reconciliation_mismatches == 0
    assert not any("unattributed" in warning.lower() for warning in snapshot.warnings)


def test_ownership_recovery_validates_confirmation_before_instrument_lookup(
    tmp_path: Path,
):
    class NoLookupAPI(FakePortfolioAPI):
        def find_instrument(self, ticker: str, class_code: str):
            raise AssertionError("Instrument lookup must not run for invalid confirmation")

    service = manager(
        tmp_path,
        NoLookupAPI(lots=1),
        state_with_decision(config_hash="9" * 64, active_owner=False),
    )
    request = OwnershipRecoveryRequest(
        account_id="account-1",
        ticker="RUB000UTSTOM",
        class_code="CETS",
        candle_interval="CANDLE_INTERVAL_HOUR",
        primary_strategy="sma",
        primary_config_hash="9" * 64,
        strategy_suite_hash="suite",
        shadow_strategies=(),
        expected_lots=49998,
        confirmation_text="ADOPT SBER 1",
    )
    with pytest.raises(ValueError, match="Confirmation mismatch"):
        service.recover_ownership(request)
