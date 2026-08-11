from datetime import datetime, timezone
from pathlib import Path

from trading_robot.diagnostics import DiagnosticConfig, SandboxOrderDiagnostics
from trading_robot.journal import EventJournal
from trading_robot.risk import RiskEngine, RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore


def q(value: float):
    units = int(value)
    nano = int(round((value - units) * 1_000_000_000))
    return {"units": str(units), "nano": nano}


class FakeDiagnosticAPI:
    def __init__(self) -> None:
        self.current_lots = 0
        self.post_count = 0
        self.orders = {}
        self.executed_price = 264.5
        self.last_response_meta = {"tracking_id": "track-1"}

    def find_instrument(self, ticker, class_code):
        return {
            "ticker": ticker,
            "classCode": class_code,
            "uid": "uid-sber",
            "lot": 1,
            "apiTradeAvailableFlag": True,
        }

    @staticmethod
    def instrument_id(instrument):
        return instrument["uid"]

    def get_portfolio(self, account_id):
        positions = []
        if self.current_lots:
            positions.append(
                {
                    "instrumentUid": "uid-sber",
                    "quantityLots": {
                        "units": str(self.current_lots),
                        "nano": 0,
                    },
                }
            )
        securities = self.current_lots * self.executed_price
        return {
            "positions": positions,
            "totalAmountPortfolio": q(50_000.0),
            "totalAmountCurrencies": q(50_000.0 - securities),
            "totalAmountShares": q(securities),
            "totalAmountBonds": q(0.0),
            "totalAmountEtf": q(0.0),
            "totalAmountFutures": q(0.0),
            "totalAmountOptions": q(0.0),
            "totalAmountSp": q(0.0),
        }

    @staticmethod
    def position_lots(portfolio, instrument):
        positions = portfolio.get("positions", [])
        return int(positions[0]["quantityLots"]["units"]) if positions else 0

    def get_trading_status(self, instrument_id):
        return {
            "apiTradeAvailableFlag": True,
            "limitOrderAvailableFlag": True,
            "marketOrderAvailableFlag": True,
        }

    @staticmethod
    def best_price_available(status):
        return True

    @staticmethod
    def market_order_available(status):
        return True

    def get_max_lots(self, account_id, instrument_id):
        return {"buyLimits": {"buyMaxMarketLots": "100"}}

    @staticmethod
    def max_buy_lots(response):
        return int(response["buyLimits"]["buyMaxMarketLots"])

    def get_orders(self, account_id):
        return []

    def post_order(
        self,
        account_id,
        instrument_id,
        lots,
        direction,
        *,
        order_id,
        order_type,
        time_in_force,
    ):
        self.post_count += 1
        if order_id not in self.orders:
            delta = lots if direction == "BUY" else -lots
            self.current_lots += delta
            self.orders[order_id] = {
                "orderId": order_id,
                "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                "lotsExecuted": str(lots),
                "executedOrderPrice": q(self.executed_price),
            }
        return self.orders[order_id]

    def get_order_state(self, account_id, order_id, by_request_id=True):
        return self.orders[order_id]


def config(tmp_path: Path) -> DiagnosticConfig:
    RiskProfileStore(tmp_path / "risk_profiles.json").save_profile(
        "SANDBOX_EXECUTION",
        RiskPolicy(),
    )
    return DiagnosticConfig(
        state_file=str(tmp_path / "diagnostic.json"),
        journal_file=str(tmp_path / "events.db"),
        risk_profile_file=str(tmp_path / "risk_profiles.json"),
        risk_state_file=str(tmp_path / "risk_state.json"),
        reconcile_delay_seconds=0,
    )


def test_controlled_buy_and_sell_round_trip(tmp_path: Path):
    api = FakeDiagnosticAPI()
    service = SandboxOrderDiagnostics(api, "account-1", config(tmp_path))

    buy = service.execute("BUY")
    assert buy["status"] == "processed"
    assert buy["position_reconciled"] is True
    assert buy["actual_lots_after"] == 1

    sell = service.execute("SELL")
    assert sell["status"] == "processed"
    assert sell["position_reconciled"] is True
    assert sell["actual_lots_after"] == 0
    assert api.post_count == 2


def test_sell_is_blocked_without_a_long_position(tmp_path: Path):
    api = FakeDiagnosticAPI()
    service = SandboxOrderDiagnostics(api, "account-1", config(tmp_path))
    result = service.execute("SELL")
    assert result["status"] == "execution_blocked"
    assert api.post_count == 0


def test_diagnostic_nontransient_rejection_clears_pending(tmp_path: Path):
    from trading_robot.tbank_sandbox import TBankAPIError

    class RejectingDiagnosticAPI(FakeDiagnosticAPI):
        def post_order(self, *args, **kwargs):
            self.post_count += 1
            raise TBankAPIError(
                "invalid diagnostic order",
                status_code=400,
                transient=False,
                service="SandboxService",
                method="PostSandboxOrder",
            )

    api = RejectingDiagnosticAPI()
    service = SandboxOrderDiagnostics(api, "account-1", config(tmp_path))

    result = service.execute("BUY")
    snapshot = service.snapshot()

    assert result["status"] == "submission_failed"
    assert result["pending_order"] is None
    assert snapshot["pending_order"] is None
    assert api.post_count == 1


def test_diagnostic_lost_response_recovers_without_duplicate(tmp_path: Path):
    import pytest
    from trading_robot.tbank_sandbox import TBankAPIError

    class LostResponseDiagnosticAPI(FakeDiagnosticAPI):
        def post_order(
            self,
            account_id,
            instrument_id,
            lots,
            direction,
            *,
            order_id,
            order_type,
            time_in_force,
        ):
            self.post_count += 1
            if order_id not in self.orders:
                self.current_lots += lots if direction == "BUY" else -lots
                self.orders[order_id] = {
                    "orderId": order_id,
                    "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                    "lotsExecuted": str(lots),
                    "executedOrderPrice": q(self.executed_price),
                }
            raise TBankAPIError(
                "response lost",
                transient=True,
                service="SandboxService",
                method="PostSandboxOrder",
            )

    api = LostResponseDiagnosticAPI()
    service = SandboxOrderDiagnostics(api, "account-1", config(tmp_path))

    with pytest.raises(TBankAPIError):
        service.execute("BUY")
    recovered = service.recover_pending()

    assert recovered["status"] == "processed"
    assert recovered["position_reconciled"] is True
    assert api.post_count == 1


def test_independent_diagnostic_actions_use_unique_request_ids(tmp_path: Path):
    api = FakeDiagnosticAPI()
    service = SandboxOrderDiagnostics(api, "account-1", config(tmp_path))
    first = service.execute("BUY")
    second = service.execute("SELL")
    third = service.execute("BUY")
    assert len({first["order_id"], second["order_id"], third["order_id"]}) == 3


def test_close_unattributed_position_sells_exact_quantity(tmp_path: Path):
    api = FakeDiagnosticAPI()
    api.current_lots = 3
    service = SandboxOrderDiagnostics(api, "account-1", config(tmp_path))
    result = service.close_unattributed_position(
        expected_lots=3,
        confirmation_text="CLOSE SBER 3",
    )
    assert result["status"] == "processed"
    assert result["requested_lots"] == 3
    assert result["actual_lots_after"] == 0
    assert result["position_reconciled"] is True
    assert result["purpose"] == "unattributed-position-close"


def test_close_unattributed_position_rechecks_quantity(tmp_path: Path):
    api = FakeDiagnosticAPI()
    api.current_lots = 2
    service = SandboxOrderDiagnostics(api, "account-1", config(tmp_path))
    result = service.close_unattributed_position(
        expected_lots=3,
        confirmation_text="CLOSE SBER 3",
    )
    assert result["status"] == "execution_blocked"
    assert api.post_count == 0


def test_diagnostic_executions_are_counted_in_risk_state(tmp_path: Path):
    api = FakeDiagnosticAPI()
    service = SandboxOrderDiagnostics(api, "account-1", config(tmp_path))

    buy = service.execute("BUY")
    sell = service.execute("SELL")

    assert buy["risk_execution_status"] == "RECORDED"
    assert sell["risk_execution_status"] == "RECORDED"
    state = RiskStateStore(tmp_path / "risk_state.json").load_account(
        "account-1"
    )
    assert state.daily_order_count == 2
    assert state.daily_turnover_rub == 2 * api.executed_price
    assert len(state.recorded_execution_ids) == 2



def test_unattributed_position_close_is_accounted_with_explicit_source(tmp_path: Path):
    api = FakeDiagnosticAPI()
    api.current_lots = 2
    service = SandboxOrderDiagnostics(api, "account-1", config(tmp_path))

    result = service.close_unattributed_position(
        expected_lots=2,
        confirmation_text="CLOSE SBER 2",
    )

    assert result["status"] == "processed"
    assert result["execution_source"] == "UNATTRIBUTED_POSITION_CLOSE"
    state = RiskStateStore(tmp_path / "risk_state.json").load_account(
        "account-1"
    )
    assert state.daily_order_count == 1
    assert state.daily_turnover_rub == 2 * api.executed_price
    risk_rows = EventJournal(tmp_path / "events.db").recent(
        limit=20,
        category="risk",
        event_type="EXECUTION_RECORDED",
    )
    assert len(risk_rows) == 1
    assert (
        risk_rows[0]["payload"]["execution_source"]
        == "UNATTRIBUTED_POSITION_CLOSE"
    )


def test_diagnostic_buy_is_blocked_by_persistent_kill_switch(tmp_path: Path):
    api = FakeDiagnosticAPI()
    cfg = config(tmp_path)
    state_store = RiskStateStore(tmp_path / "risk_state.json")
    halted, _event = RiskEngine(RiskPolicy()).engage_kill_switch(
        state_store.load_account("account-1"),
        now=datetime.now(timezone.utc),
        reason="maintenance",
    )
    state_store.save_account("account-1", halted)
    service = SandboxOrderDiagnostics(api, "account-1", cfg)

    result = service.execute("BUY")

    assert result["status"] == "execution_blocked"
    assert "kill switch" in str(result["reason"]).lower()
    assert api.post_count == 0
    assert RiskStateStore(tmp_path / "risk_state.json").load_account(
        "account-1"
    ).daily_order_count == 0

def test_diagnostic_audit_order_is_causal(tmp_path: Path):
    api = FakeDiagnosticAPI()
    service = SandboxOrderDiagnostics(api, "account-1", config(tmp_path))
    result = service.execute("BUY")

    rows = list(
        reversed(
            EventJournal(tmp_path / "events.db").recent(
                limit=100, order_id=result["order_id"]
            )
        )
    )
    names = [(row["category"], row["event_type"]) for row in rows]
    filled_index = names.index(("diagnostic_order", "FILLED"))
    reconciled_index = names.index(
        ("diagnostic_order", "PORTFOLIO_RECONCILED")
    )
    execution_index = names.index(("risk", "EXECUTION_RECORDED"))
    accounted_index = names.index(("diagnostic_order", "RISK_ACCOUNTED"))
    assert filled_index < reconciled_index < execution_index < accounted_index
    proof = rows[execution_index]["payload"]["reconciliation_proof"]
    assert proof["position_reconciled"] is True
    assert proof["expected_lots_after"] == proof["actual_lots_after"] == 1
    assert rows[execution_index]["payload"]["execution_source"] == "DIAGNOSTIC"
