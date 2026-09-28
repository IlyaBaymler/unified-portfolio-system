"""STEP6 real desktop refresh + Risk/Central; synthetic provider responses only.

No real account, credentials, HTTP or exact-cash authority. Assertions below
separate currency valuation from independent cash and own-money limits.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import time

import pytest
from test_q7a_source_desktop_flow import desktop_case, _desktop_cycle, _observed
from test_q7a_source_provider_refresh import refresh_case, money, position, stage
from test_q7a_source_natural_cycle import ACCOUNT, UID


def balances(rub=1000, blocked=0):
    return {"accountId": ACCOUNT, "money": [money(rub)], "blocked": [money(blocked)],
            "limitsLoadingInProgress": False, "securities": [], "futures": [], "options": []}


def limits(amount=1000, currency="rub"):
    return {"currency": currency, "buyLimits": {"buyMoneyAmount": money(amount)},
            "buyMarginLimits": {"buyMoneyAmount": money(9_000_000)}}


def set_cash(x, *, rub=1000, blocked=0, own=1000):
    x.p.cash_positions_override = balances(rub, blocked)
    x.p.cash_limits_override = limits(own)


def test_usd_valuation_is_not_spendable_rub_through_desktop_refresh(refresh_case):
    x = refresh_case
    x.p.payload["positions"] = [{"instrumentUid": "synthetic-usd", "instrumentType": "currency",
                                  "quantity": money(10)}]
    x.p.payload["totalAmountCurrencies"] = money(1000)
    x.p.payload["totalAmountPortfolio"] = money(1000)
    x.p.cash_positions_override = balances(0)
    x.p.cash_positions_override["money"] = [money(10, "usd")]
    x.p.cash_limits_override = limits(0)
    state = x.refresh()
    assert state.account.total_value == 1000
    assert state.account.cash("rub").available == 0
    assert x.p.order_calls == 0
    assert x.p.cash_read_calls == [("positions", ACCOUNT),
        ("max_lots", ACCOUNT, "uid-lkoh"), ("max_lots", ACCOUNT, UID)]


def test_fake_buy_not_sent_when_valuation_positive_but_own_cash_zero(refresh_case):
    x = refresh_case
    set_cash(x, rub=0, own=0)
    state = x.refresh()
    assert state.account.total_value == 1_000_000
    result = _desktop_cycle(x.c)
    assert result.status not in {"QUEUED", "REAUTHORIZED", "REPLACED"}
    assert x.p.order_calls == 0
    assert x.c.central_order_coordinator.manager.state().intents == ()


def test_real_cash_survives_unrelated_currency_valuation(refresh_case):
    x = refresh_case
    x.p.payload["totalAmountCurrencies"] = money(999_999)
    set_cash(x, rub=2000, blocked=300, own=1700)
    state = x.refresh()
    assert state.account.cash("rub").available == 1700
    assert state.account.cash("rub").blocked == 300
    assert state.account.total_value == 1_000_000
    result = _desktop_cycle(x.c)
    assert result.status == "QUEUED", result.to_dict()
    assert _observed(x.c).execution_status == "SUBMITTED"
    assert x.p.order_calls == 1
    assert result.intent_id == x.p.sent_ids[0]


def test_blocked_is_not_subtracted_twice_from_provider_own_limit(refresh_case):
    x = refresh_case
    set_cash(x, rub=1800, blocked=700, own=1100)
    state = x.refresh()
    assert state.account.cash("rub").available == 1100
    # 1100 covers the real 1050 RUB test BUY. Subtracting blocked a second
    # time would incorrectly cut this budget to 400 RUB.
    assert _desktop_cycle(x.c).status == "QUEUED"
    assert x.p.order_calls == 1


def test_local_queued_reserve_stays_once_and_reauthorization_reuses_intent(refresh_case):
    x = refresh_case
    set_cash(x, rub=1800, blocked=700, own=1100)
    x.p.market_open = False
    x.refresh()
    result = _desktop_cycle(x.c)
    central = x.c.central_order_coordinator.manager
    assert result.status == "QUEUED"
    first = central.state().queued[0]
    assert 105000 <= first.reserved_cash_kopecks <= 110000
    state = x.refresh()
    assert state.account.cash("rub").available == 1100  # no local reserve subtraction here
    again = _desktop_cycle(x.c)
    assert again.status == "REAUTHORIZED", again.to_dict()
    assert again.intent_id == result.intent_id
    assert len(central.state().intents) == 1
    assert central.state().reserved_cash_kopecks == first.reserved_cash_kopecks
    assert x.p.order_calls == 0


def test_smaller_limit_for_another_configured_instrument_caps_common_cash(refresh_case, monkeypatch):
    x = refresh_case
    set_cash(x, rub=5000, own=5000)
    def get_limits(account, uid, price=None):
        assert account == ACCOUNT and price is None
        return limits(2000 if uid == UID else 1500)
    monkeypatch.setattr(x.p, "get_max_lots", get_limits)
    assert x.refresh().account.cash("rub").available == 1500


def test_sell_with_zero_own_cash_and_hold_do_not_require_positive_cash(refresh_case):
    x = refresh_case
    x.p.payload["positions"] = [position(1)]
    x.p.payload["totalAmountCurrencies"] = money(0)
    set_cash(x, rub=0, own=0)
    stage(x, 1)
    state = x.refresh()
    assert not state.blocking and state.account.cash("rub").available == 0
    x.p.target = 1
    assert _desktop_cycle(x.c).status == "NO_POSITION_CHANGE"
    assert x.p.quote_calls == x.p.order_calls == 0
    x.p.target = 0
    result = _desktop_cycle(x.c)
    assert result.status == "QUEUED", result.to_dict()
    assert x.p.order_calls == 1
    assert x.c.central_order_coordinator.manager.state().intents[0].candidate.direction == "SELL"


@pytest.mark.parametrize("damage", ["missing_account", "wrong_account", "loading", "loading_number", "loading_string",
    "root_list", "money_null", "money_object", "money_row_null", "blocked_null", "duplicate_rub",
    "duplicate_usd", "duplicate_blocked", "currency_missing", "currency_upper", "currency_long",
    "units_bool", "units_float", "units_nan", "nano_bool", "nano_overflow", "negative_rub", "negative_usd",
    "units_overflow", "scope_overflow", "mixed_sign", "foreign_field_nan", "futures", "options", "futures_bad_type"])
def test_invalid_positions_cannot_replace_canonical_cash(refresh_case, damage):
    x = refresh_case
    set_cash(x)
    raw = x.p.cash_positions_override
    row = raw["money"][0]
    if damage == "missing_account": raw.pop("accountId")
    elif damage == "wrong_account": raw["accountId"] = "PRIVATE_OTHER_ACCOUNT"
    elif damage == "loading": raw["limitsLoadingInProgress"] = True
    elif damage == "loading_number": raw["limitsLoadingInProgress"] = 0
    elif damage == "loading_string": raw["limitsLoadingInProgress"] = "false"
    elif damage == "root_list": x.p.cash_positions_override = []
    elif damage == "money_null": raw["money"] = None
    elif damage == "money_object": raw["money"] = {}
    elif damage == "money_row_null": raw["money"] = [None]
    elif damage == "blocked_null": raw["blocked"] = None
    elif damage == "duplicate_rub": raw["money"].append(money(3))
    elif damage == "duplicate_usd": raw["money"].extend([money(1,"usd"), money(2,"usd")])
    elif damage == "duplicate_blocked": raw["blocked"].append(money(0))
    elif damage == "currency_missing": row.pop("currency")
    elif damage == "currency_upper": row["currency"] = "RUB"
    elif damage == "currency_long": row["currency"] = "PRIVATE_CURRENCY"
    elif damage == "units_bool": row["units"] = True
    elif damage == "units_float": row["units"] = 1.0
    elif damage == "units_nan": row["units"] = "NaN"
    elif damage == "nano_bool": row["nano"] = False
    elif damage == "nano_overflow": row["nano"] = 10**9
    elif damage == "negative_rub": raw["money"][0] = money(-1)
    elif damage == "negative_usd": raw["money"].append(money(-1,"usd"))
    elif damage == "units_overflow": row["units"] = str(2**63)
    elif damage == "scope_overflow": row["units"] = str(10**12+1)
    elif damage == "mixed_sign": row["nano"] = -1
    elif damage == "foreign_field_nan": raw["unrelated"] = float("nan")
    elif damage == "futures": raw["futures"] = [{"balance":"1"}]
    elif damage == "options": raw["options"] = [{"balance":"1"}]
    elif damage == "futures_bad_type": raw["futures"] = {}
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_CASH_") as exc:
        x.refresh()
    assert "PRIVATE" not in str(exc.value)
    assert x.manager.repository.path.read_bytes() == before
    assert x.p.order_calls == 0
    assert not any(call[0] == "max_lots" for call in x.p.cash_read_calls)


@pytest.mark.parametrize("damage", ["currency_missing", "currency_usd", "own_missing", "margin_only",
    "own_null", "amount_missing", "amount_null", "amount_nan", "units_bool", "units_float", "units_overflow",
    "nano_bool", "nano_overflow", "negative", "more_than_rub", "wrong_account", "wrong_uid", "wrong_instrument",
    "root_list", "unknown_nan"])
def test_invalid_own_limits_not_replaced_with_margin_valuation_or_withdraw(refresh_case, damage):
    x = refresh_case
    set_cash(x)
    raw = x.p.cash_limits_override
    if damage == "currency_missing": raw.pop("currency")
    elif damage == "currency_usd": raw["currency"] = "usd"
    elif damage in {"own_missing","margin_only"}: raw.pop("buyLimits")
    elif damage == "own_null": raw["buyLimits"] = None
    elif damage == "amount_missing": raw["buyLimits"].pop("buyMoneyAmount")
    elif damage == "amount_null": raw["buyLimits"]["buyMoneyAmount"] = None
    elif damage == "amount_nan": raw["buyLimits"]["buyMoneyAmount"]["units"] = "NaN"
    elif damage == "units_bool": raw["buyLimits"]["buyMoneyAmount"]["units"] = True
    elif damage == "units_float": raw["buyLimits"]["buyMoneyAmount"]["units"] = 1.0
    elif damage == "units_overflow": raw["buyLimits"]["buyMoneyAmount"]["units"] = str(2**63)
    elif damage == "nano_bool": raw["buyLimits"]["buyMoneyAmount"]["nano"] = True
    elif damage == "nano_overflow": raw["buyLimits"]["buyMoneyAmount"]["nano"] = 10**9
    elif damage == "negative": raw["buyLimits"]["buyMoneyAmount"] = money(-1)
    elif damage == "more_than_rub": raw["buyLimits"]["buyMoneyAmount"] = money(1001)
    elif damage == "wrong_account": raw["accountId"] = "PRIVATE_OTHER_ACCOUNT"
    elif damage == "wrong_uid": raw["instrumentUid"] = "PRIVATE_OTHER_UID"
    elif damage == "wrong_instrument": raw["instrumentId"] = "PRIVATE_OTHER_ID"
    elif damage == "root_list": x.p.cash_limits_override = []
    elif damage == "unknown_nan": raw["unrelated"] = float("nan")
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_CASH_") as exc: x.refresh()
    assert "PRIVATE" not in str(exc.value)
    assert x.manager.repository.path.read_bytes() == before
    assert x.p.order_calls == 0


@pytest.mark.parametrize("method", ["get_positions", "get_max_lots"])
def test_cash_read_failure_not_published_and_not_leaked(refresh_case, monkeypatch, method):
    x = refresh_case
    def fail(*args, **kwargs): raise TimeoutError("PRIVATE_PAYLOAD_TOKEN_ACCOUNT")
    monkeypatch.setattr(x.p, method, fail)
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_CASH_.*UNAVAILABLE") as exc: x.refresh()
    assert "PRIVATE" not in str(exc.value)
    assert x.manager.repository.path.read_bytes() == before and x.p.order_calls == 0


@pytest.mark.parametrize("at", ["positions", "first_limit", "last_limit"])
def test_whole_refresh_deadline_includes_cash_reads(refresh_case, monkeypatch, at):
    x = refresh_case
    wall = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: wall[0])
    if at == "positions": x.p.on_cash_positions = lambda: wall.__setitem__(0, 131.0)
    elif at == "first_limit": x.p.on_cash_limits = lambda uid: wall.__setitem__(0, 131.0)
    else: x.p.on_cash_limits = lambda uid: wall.__setitem__(0, 131.0) if uid == UID else None
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="PORTFOLIO_CASH_OBSERVATION_EXPIRED"): x.refresh()
    assert x.manager.repository.path.read_bytes() == before and x.p.order_calls == 0


def test_metadata_changed_during_limits_is_not_published(refresh_case):
    x = refresh_case
    x.p.on_cash_limits = lambda uid: (x.root / "portfolio_risk_metadata.json").write_bytes(b"changed")
    before = x.manager.repository.path.read_bytes()
    with pytest.raises(RuntimeError, match="GUI_RISK_METADATA"): x.refresh()
    assert x.manager.repository.path.read_bytes() == before and x.p.order_calls == 0


def test_concurrent_target_change_during_cash_reads_keeps_new_revision(refresh_case):
    x = refresh_case
    before = x.manager.repository.load(expected_account_id=ACCOUNT)
    x.p.on_cash_positions = lambda: x.manager.stage_confirmed_target(instrument_id=UID,
        target_lots=1, strategy_id="sma", config_hash="a"*64, candle_interval="CANDLE_INTERVAL_HOUR")
    from trading_robot.portfolio_repository import PortfolioRevisionConflictError
    with pytest.raises(PortfolioRevisionConflictError): x.refresh()
    after = x.manager.repository.load(expected_account_id=ACCOUNT)
    assert after.revision == before.revision + 1 and after.position(UID).target_lots == 1
    assert x.p.order_calls == 0


def test_fresh_cash_drop_invalidates_saved_authorization_no_post(refresh_case):
    x = refresh_case
    set_cash(x, rub=2000, own=2000)
    x.p.market_open = False
    x.refresh()
    result = _desktop_cycle(x.c)
    assert result.status == "QUEUED"
    set_cash(x, rub=10, own=10)
    x.refresh()
    x.p.market_open = True
    dispatch = x.c.execution_adapter.dispatch_next(x.manager.repository, expected_intent_id=result.intent_id)
    assert dispatch.status != "SUBMITTED" and not dispatch.order_was_sent
    assert x.p.order_calls == 0


@pytest.mark.parametrize("cash", ["0", "0.009999999", "10.019999999", "999999.999999999"])
def test_money_is_rounded_down_not_increased_at_float_boundary(refresh_case, cash):
    x = refresh_case
    set_cash(x, rub=Decimal(cash), own=Decimal(cash))
    available = x.refresh().account.cash("rub").available
    exact = Decimal(cash)
    assert Decimal.from_float(available) <= exact
    assert exact - Decimal.from_float(available) < Decimal("0.011")


@pytest.mark.parametrize("omitted", ["false_flag", "empty_fields", "zero_scalar"])
def test_proto_default_zero_never_falls_back_to_valuation(refresh_case, omitted):
    x = refresh_case
    set_cash(x, rub=0, own=0)
    if omitted == "false_flag": x.p.cash_positions_override.pop("limitsLoadingInProgress")
    elif omitted == "empty_fields":
        for name in ("money", "blocked", "securities", "futures", "options"):
            x.p.cash_positions_override.pop(name)
    else: x.p.cash_limits_override["buyLimits"]["buyMoneyAmount"] = {}
    assert x.refresh().account.cash("rub").available == 0


def test_legacy_no_policy_request_sequence_remains_unchanged(refresh_case):
    x = refresh_case
    state = x.manager.refresh()
    assert state.account.cash("rub").available == 1_000_000
    assert not x.p.cash_read_calls  # legacy path deliberately NOT cash-qualified
    assert x.p.order_calls == 0


def test_production_client_cash_calls_use_sandbox_account_and_instrument(monkeypatch):
    from trading_robot.tbank_sandbox import TBankSandboxClient
    client = object.__new__(TBankSandboxClient)
    calls = []
    def post(self, service, method, payload, **kwargs):
        calls.append((service, method, deepcopy(payload)))
        return {"synthetic": True}
    monkeypatch.setattr(TBankSandboxClient, "_post", post)
    client.get_positions(ACCOUNT)
    client.get_max_lots(ACCOUNT, UID)
    assert calls == [("SandboxService", "GetSandboxPositions", {"accountId":ACCOUNT}),
        ("SandboxService", "GetSandboxMaxLots", {"accountId":ACCOUNT, "instrumentId":UID})]
