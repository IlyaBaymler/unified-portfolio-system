from __future__ import annotations

"""Safe operator edits for selected RiskPolicy fields.

The service contains no GUI code.  It validates the operational context,
persists one checksummed profile update, preserves RiskState counters and writes
one auditable EventJournal row for each accepted change.
"""

from dataclasses import dataclass, replace
from typing import Any

from .journal import EventJournal, JournalEvent
from .risk_persistence import (
    RiskMode,
    RiskPersistenceError,
    RiskProfileStore,
    RiskStateStore,
    normalize_risk_mode,
)


class RiskProfileEditError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RiskProfileEditContext:
    account_id: str
    mode: str
    execution_active: bool = False
    operation_active: bool = False
    pending_order: bool = False
    uncertain_order: bool = False
    reconciliation_blocking: bool = False
    maintenance_active: bool = False

    @property
    def normalized_mode(self) -> RiskMode:
        return normalize_risk_mode(self.mode)

    def block_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.execution_active:
            reasons.append("Sandbox Execution или Dry-run активен.")
        if self.operation_active:
            reasons.append("Фоновая Sandbox-операция ещё выполняется.")
        if self.pending_order:
            reasons.append("Есть активная pending-order.")
        if self.uncertain_order:
            reasons.append("Есть uncertain order; сначала завершите recovery.")
        if self.reconciliation_blocking:
            reasons.append("Portfolio reconciliation требует ручной проверки.")
        if self.maintenance_active:
            reasons.append("Выполняется backup/restore/update операция.")
        if not str(self.account_id).strip():
            reasons.append("Sandbox Account ID не выбран.")
        return tuple(reasons)


@dataclass(frozen=True, slots=True)
class RiskProfileEditResult:
    changed: bool
    account_id: str
    mode: str
    old_limit: int | None
    new_limit: int
    current_count: int
    halted_by_limit: bool
    old_policy_hash: str
    new_policy_hash: str
    updated_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": self.changed,
            "account_id": self.account_id,
            "mode": self.mode,
            "old_limit": self.old_limit,
            "new_limit": self.new_limit,
            "current_count": self.current_count,
            "halted_by_limit": self.halted_by_limit,
            "old_policy_hash": self.old_policy_hash,
            "new_policy_hash": self.new_policy_hash,
            "updated_at": self.updated_at,
        }


class RiskProfileEditor:
    MIN_SANDBOX_ORDERS = 1
    MAX_SANDBOX_ORDERS = 100

    def __init__(
        self,
        *,
        profile_store: RiskProfileStore,
        state_store: RiskStateStore,
        journal: EventJournal,
    ) -> None:
        self.profile_store = profile_store
        self.state_store = state_store
        self.journal = journal

    @classmethod
    def validate_daily_order_limit(cls, value: Any) -> int:
        if isinstance(value, bool):
            raise RiskProfileEditError("Лимит должен быть целым числом.")
        text = str(value).strip()
        if not text:
            raise RiskProfileEditError("Лимит заявок не задан.")
        if any(token in text for token in (".", ",")):
            raise RiskProfileEditError("Дробный лимит заявок недопустим.")
        try:
            limit = int(text)
        except (TypeError, ValueError) as exc:
            raise RiskProfileEditError("Лимит должен быть целым числом.") from exc
        if not cls.MIN_SANDBOX_ORDERS <= limit <= cls.MAX_SANDBOX_ORDERS:
            raise RiskProfileEditError(
                f"Допустимый Sandbox-диапазон: "
                f"{cls.MIN_SANDBOX_ORDERS}…{cls.MAX_SANDBOX_ORDERS}."
            )
        return limit

    def apply_max_orders_per_day(
        self,
        context: RiskProfileEditContext,
        new_limit: Any,
        *,
        source: str = "GUI_OPERATOR",
    ) -> RiskProfileEditResult:
        reasons = context.block_reasons()
        if reasons:
            raise RiskProfileEditError(" ".join(reasons))
        account_id = str(context.account_id).strip()
        mode = context.normalized_mode
        limit = self.validate_daily_order_limit(new_limit)
        try:
            loaded = self.profile_store.require_profile(mode)
            state = self.state_store.load_account(account_id)
        except RiskPersistenceError as exc:
            raise RiskProfileEditError(str(exc)) from exc

        old_policy = loaded["policy"]
        old_limit = old_policy.max_orders_per_day
        current_count = int(state.daily_order_count)
        old_hash = old_policy.policy_hash
        existing_scope = str(loaded.get("account_scope") or "").strip() or None
        if old_limit == limit and existing_scope == account_id:
            return RiskProfileEditResult(
                changed=False,
                account_id=account_id,
                mode=mode,
                old_limit=old_limit,
                new_limit=limit,
                current_count=current_count,
                halted_by_limit=current_count >= limit,
                old_policy_hash=old_hash,
                new_policy_hash=old_hash,
                updated_at=loaded.get("updated_at"),
            )

        new_policy = replace(old_policy, max_orders_per_day=limit)
        try:
            saved = self.profile_store.save_profile(
                mode,
                new_policy,
                select=True,
                account_scope=account_id,
                source=source,
            )
        except RiskPersistenceError as exc:
            raise RiskProfileEditError(str(exc)) from exc

        self.journal.record(
            JournalEvent(
                category="risk",
                event_type="RISK_PROFILE_UPDATED",
                severity="WARNING" if current_count >= limit else "INFO",
                account_id=account_id,
                mode=mode,
                status="HALTED" if current_count >= limit else "UPDATED",
                config_hash=saved["policy_hash"],
                payload={
                    "source": source,
                    "field": "max_orders_per_day",
                    "old_value": old_limit,
                    "new_value": limit,
                    "daily_order_count": current_count,
                    "halted_by_limit": current_count >= limit,
                    "old_policy_hash": old_hash,
                    "new_policy_hash": saved["policy_hash"],
                    "account_scope": account_id,
                    "mode": mode,
                },
            )
        )
        return RiskProfileEditResult(
            changed=True,
            account_id=account_id,
            mode=mode,
            old_limit=old_limit,
            new_limit=limit,
            current_count=current_count,
            halted_by_limit=current_count >= limit,
            old_policy_hash=old_hash,
            new_policy_hash=saved["policy_hash"],
            updated_at=saved.get("updated_at"),
        )
