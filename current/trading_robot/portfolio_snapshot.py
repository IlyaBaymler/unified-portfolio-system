from __future__ import annotations

"""Read-only GUI/export projection for canonical PortfolioState."""

import csv
import json
from pathlib import Path
from typing import Any

from .portfolio_model import PortfolioState, ReconciliationStatus


class PortfolioSnapshotBuilder:
    def __init__(self, state: PortfolioState) -> None:
        if not isinstance(state, PortfolioState):
            raise TypeError("state must be PortfolioState")
        self.state = state

    def to_dict(self) -> dict[str, Any]:
        cash = {item.currency: item.available for item in self.state.account.cash_balances}
        blocked = {item.currency: item.blocked for item in self.state.account.cash_balances}
        positions = [self._position_dict(item) for item in self.state.positions]
        unattributed = sum(
            item.ownership_status.value == "UNATTRIBUTED"
            for item in self.state.positions
        )
        mismatches = sum(
            item.reconciliation.status is not ReconciliationStatus.MATCHED
            for item in self.state.positions
        )
        local_pending = [
            order.to_dict()
            for position in self.state.positions
            for order in position.pending_orders
            if order.source == "LOCAL"
        ]
        broker_orders = [
            order.to_dict()
            for position in self.state.positions
            for order in position.pending_orders
            if order.source == "BROKER"
        ]
        return {
            "schema_version": self.state.version,
            "canonical": True,
            "portfolio_source": self.state.portfolio_source,
            "migration_status": self.state.migration.status.value,
            "legacy_read_path_enabled": self.state.migration.legacy_read_path_enabled,
            "compatibility_shadow_status": self.state.migration.compatibility_shadow_status.value,
            "last_transaction_id": self.state.last_transaction_id,
            "last_transaction_status": self.state.last_transaction_status,
            "revision": self.state.revision,
            "decision_checksum": self.state.decision_sha256,
            "account_id": self.state.account_id,
            "checked_at": self.state.snapshot_at,
            "generated_at": self.state.generated_at,
            "freshness": self.state.freshness.value,
            "total_value": self.state.account.total_value,
            "cash_rub": float(cash.get("rub", 0.0)),
            "securities_value": self.state.account.securities_value,
            "expected_yield": self.state.account.expected_yield,
            "cash_by_currency": cash,
            "blocked_by_currency": blocked,
            "positions": positions,
            "broker_orders": broker_orders,
            "local_pending_orders": local_pending,
            "state_status": self.state.state_status,
            "blocking": self.state.blocking,
            "warnings": list(self.state.warnings),
            "unattributed_positions": int(unattributed),
            "reconciliation_mismatches": int(mismatches),
            "reconciliation_results": [
                item.reconciliation.to_dict() for item in self.state.positions
            ],
            "raw_totals": {
                "totalAmountPortfolio": self.state.account.total_value,
                "securitiesValue": self.state.account.securities_value,
                "expectedYield": self.state.account.expected_yield,
            },
        }

    def export_json(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    def export_csv(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = [
            "instrument_id",
            "figi",
            "ticker",
            "class_code",
            "asset_type",
            "currency",
            "quantity",
            "actual_lots",
            "target_lots",
            "average_price",
            "current_price",
            "market_value",
            "expected_yield",
            "owner_strategy",
            "owner_config_hash",
            "owner_interval",
            "ownership_status",
            "origin",
            "reconciliation_status",
            "reconciliation_blocking",
            "reconciliation_reasons",
            "pending_order_ids",
            "last_candle_time",
        ]
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for position in self.state.positions:
                owner = position.ownership
                writer.writerow(
                    {
                        "instrument_id": position.instrument_id,
                        "figi": position.figi,
                        "ticker": position.ticker,
                        "class_code": position.class_code,
                        "asset_type": position.asset_type,
                        "currency": position.currency,
                        "quantity": position.quantity,
                        "actual_lots": position.actual_lots,
                        "target_lots": position.target_lots,
                        "average_price": position.average_price,
                        "current_price": position.current_price,
                        "market_value": position.market_value,
                        "expected_yield": position.expected_yield,
                        "owner_strategy": owner.strategy_id if owner else None,
                        "owner_config_hash": owner.config_hash if owner else None,
                        "owner_interval": owner.candle_interval if owner else None,
                        "ownership_status": position.ownership_status.value,
                        "origin": position.origin.value,
                        "reconciliation_status": position.reconciliation.status.value,
                        "reconciliation_blocking": position.reconciliation.blocking,
                        "reconciliation_reasons": " | ".join(position.reconciliation.reasons),
                        "pending_order_ids": " | ".join(
                            item.order_request_id for item in position.pending_orders
                        ),
                        "last_candle_time": position.last_candle_time,
                    }
                )
        return target

    @staticmethod
    def _position_dict(position: Any) -> dict[str, Any]:
        owner = position.ownership
        local_pending = tuple(
            item.order_request_id
            for item in position.pending_orders
            if item.source == "LOCAL"
        )
        broker_pending = tuple(
            item.order_request_id
            for item in position.pending_orders
            if item.source == "BROKER"
        )
        return {
            "instrument_id": position.instrument_id,
            "figi": position.figi,
            "ticker": position.ticker,
            "class_code": position.class_code,
            "asset_type": position.asset_type,
            "currency": position.currency,
            "quantity": position.quantity,
            "quantity_lots": position.actual_lots,
            "current_lots": position.actual_lots,
            "average_price": position.average_price,
            "current_price": position.current_price,
            "market_value": position.market_value,
            "expected_yield": position.expected_yield,
            "target_lots": position.target_lots,
            "owner_strategy": owner.strategy_id if owner else None,
            "owner_config_hash": owner.config_hash if owner else None,
            "owner_interval": owner.candle_interval if owner else None,
            "ownership_status": position.ownership_status.value,
            "origin": position.origin.value,
            "reconciliation_status": position.reconciliation.status.value,
            "reconciliation_blocking": position.reconciliation.blocking,
            "reconciliation_reasons": list(position.reconciliation.reasons),
            "local_pending_order_ids": list(local_pending),
            "broker_pending_order_ids": list(broker_pending),
            "last_candle_time": position.last_candle_time,
        }
