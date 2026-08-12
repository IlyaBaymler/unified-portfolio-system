from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import csv
import json
from math import isfinite
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .journal import EventJournal
from .risk import RiskPolicy, RiskState
from .risk_persistence import (
    RiskPersistenceError,
    RiskProfileStore,
    RiskStateStore,
    normalize_risk_mode,
)


class RiskReportError(RuntimeError):
    """Raised when a trustworthy risk dashboard/report cannot be produced."""


BREACH_TITLES_RU: dict[str, str] = {
    "KILL_SWITCH": "Включён kill switch",
    "RISK_RESYNC_REQUIRED": "Требуется risk resync",
    "MAX_POSITION_LOTS": "Лимит количества лотов",
    "MAX_POSITION_VALUE": "Лимит стоимости позиции",
    "MAX_POSITION_SHARE": "Лимит доли позиции",
    "MAX_ORDER_VALUE": "Лимит стоимости заявки",
    "CASH_RESERVE": "Минимальный денежный резерв",
    "RISK_PER_TRADE": "Лимит риска сделки",
    "DAILY_LOSS_LIMIT_RUB": "Дневной лимит убытка, ₽",
    "DAILY_LOSS_LIMIT_FRACTION": "Дневной лимит убытка, %",
    "WEEKLY_LOSS_LIMIT_RUB": "Недельный лимит убытка, ₽",
    "WEEKLY_LOSS_LIMIT_FRACTION": "Недельный лимит убытка, %",
    "MAX_DRAWDOWN": "Максимальная просадка",
    "MAX_DAILY_TURNOVER": "Дневной лимит оборота",
    "MAX_ORDERS_PER_DAY": "Дневной лимит заявок",
    "STALE_PORTFOLIO_SNAPSHOT": "Устаревший снимок портфеля",
    "SNAPSHOT_TIME_UNKNOWN": "Неизвестно время снимка",
    "SNAPSHOT_FROM_FUTURE": "Некорректное время снимка",
    "POSITION_NOT_RECONCILED": "Позиция не сверена",
    "PENDING_ORDER_ACTIVE": "Есть незавершённая заявка",
    "PENDING_ORDER_STATE_UNKNOWN": "Неизвестно состояние заявок",
    "UNKNOWN_PRICE": "Неизвестна цена",
    "UNKNOWN_EQUITY": "Неизвестна стоимость портфеля",
    "UNKNOWN_CASH": "Неизвестен денежный остаток",
    "UNKNOWN_RISK_DISTANCE": "Неизвестна стоп-дистанция/ATR",
}

LIMIT_TITLES_RU: dict[str, str] = {
    "MAX_POSITION_LOTS": "Позиция, лоты",
    "MAX_POSITION_VALUE": "Стоимость позиции",
    "MAX_POSITION_SHARE": "Доля позиции в портфеле",
    "MAX_ORDER_VALUE": "Стоимость последней одобренной заявки",
    "CASH_RESERVE": "Денежный резерв",
    "RISK_PER_TRADE": "Расчётный риск позиции",
    "DAILY_LOSS_LIMIT_RUB": "Дневной убыток",
    "DAILY_LOSS_LIMIT_FRACTION": "Дневная доходность",
    "WEEKLY_LOSS_LIMIT_RUB": "Недельный убыток",
    "WEEKLY_LOSS_LIMIT_FRACTION": "Недельная доходность",
    "MAX_DRAWDOWN": "Просадка от high-watermark",
    "MAX_DAILY_TURNOVER": "Дневной оборот",
    "MAX_ORDERS_PER_DAY": "Заявки за день",
    "MAX_SNAPSHOT_AGE": "Возраст снимка портфеля",
}

STATUS_TITLES_RU: dict[str, str] = {
    "ACTIVE": "Активен",
    "READY": "Готов",
    "PASS": "Разрешено",
    "ADJUSTED": "Размер уменьшен",
    "REDUCTION_ALLOWED": "Разрешено уменьшение риска",
    "BLOCKED": "Заблокировано",
    "HALTED": "Остановлено",
    "RESYNC_REQUIRED": "Требуется пересинхронизация",
    "RUNTIME_ERROR": "Ошибка Risk Engine",
    "NOT_ENFORCED": "Risk Engine не применён",
    "DISABLED": "Отключён",
    "UNKNOWN": "Неизвестно",
}


@dataclass(frozen=True, slots=True)
class RiskLimitUsage:
    code: str
    title: str
    status: str
    unit: str
    current: float | int | None
    limit: float | int | None
    remaining: float | int | None
    utilization: float | None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RiskDashboardSnapshot:
    generated_at: str
    account_id: str
    mode: str
    engine_status: str
    engine_status_title: str
    policy_hash: str
    profile_updated_at: str | None
    summary: dict[str, Any]
    limits: tuple[RiskLimitUsage, ...]
    latest_decision: dict[str, Any] | None
    recent_events: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "account_id": self.account_id,
            "mode": self.mode,
            "engine_status": self.engine_status,
            "engine_status_title": self.engine_status_title,
            "policy_hash": self.policy_hash,
            "profile_updated_at": self.profile_updated_at,
            "summary": dict(self.summary),
            "limits": [item.to_dict() for item in self.limits],
            "latest_decision": (
                dict(self.latest_decision)
                if self.latest_decision is not None
                else None
            ),
            "recent_events": [dict(item) for item in self.recent_events],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class BurnInCheck:
    code: str
    status: str
    title: str
    details: str
    affected_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "title": self.title,
            "details": self.details,
            "affected_ids": list(self.affected_ids),
        }


@dataclass(frozen=True, slots=True)
class RiskBurnInReport:
    generated_at: str
    account_id: str | None
    period_start: str | None
    period_end: str | None
    overall_status: str
    metrics: dict[str, Any]
    checks: tuple[BurnInCheck, ...]
    recommendations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "account_id": self.account_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "overall_status": self.overall_status,
            "metrics": dict(self.metrics),
            "checks": [item.to_dict() for item in self.checks],
            "recommendations": list(self.recommendations),
        }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _max_limit_status(current: float | None, limit: float | None) -> tuple[str, float | None, float | None]:
    if limit is None:
        return "NOT_SET", None, None
    if current is None:
        return "UNKNOWN", None, None
    utilization = current / limit if limit > 0 else None
    remaining = max(0.0, limit - current)
    if current >= limit:
        return "BLOCKED", remaining, utilization
    if utilization is not None and utilization >= 0.80:
        return "WARN", remaining, utilization
    return "OK", remaining, utilization


def _min_limit_status(current: float | None, minimum: float | None) -> tuple[str, float | None, float | None]:
    if minimum is None:
        return "NOT_SET", None, None
    if current is None:
        return "UNKNOWN", None, None
    surplus = current - minimum
    utilization = minimum / current if current > 0 else None
    if current < minimum:
        return "BLOCKED", surplus, utilization
    if minimum > 0 and current < minimum * 1.20:
        return "WARN", surplus, utilization
    return "OK", surplus, utilization


def _latest_decision(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        decision = payload.get("risk_decision")
        if isinstance(decision, Mapping):
            return dict(decision)
        nested = payload.get("risk_execution")
        if isinstance(nested, Mapping):
            continue
        if str(row.get("event_type") or "") == "RISK_RUNTIME_ERROR":
            return {
                "status": "RUNTIME_ERROR",
                "reasons": [str(payload.get("error") or "Risk runtime error")],
                "breaches": [],
                "metrics": {},
                "lot_caps": {},
            }
    return None


def _recent_risk_events(rows: Sequence[Mapping[str, Any]], limit: int = 50) -> tuple[dict[str, Any], ...]:
    selected: list[dict[str, Any]] = []
    risk_event_types = {
        "EXTERNAL_ACTIVITY_DETECTED",
        "EXTERNAL_ACTIVITY_CLEARED",
        "EXTERNAL_POSITION_RECONCILED",
        "RISK_RESYNC_COMPLETED",
        "RISK_BASELINES_RESET",
        "KILL_SWITCH_ENGAGED",
        "KILL_SWITCH_CLEARED",
        "RISK_RUNTIME_ERROR",
        "RISK_EXECUTION_RUNTIME_ERROR",
        "RISK_EXECUTION_ACCOUNTING_FAILED",
        "RISK_AUTHORIZATION_MISSING",
        "RISK_ACCOUNTING_REQUIRED",
        "RISK_ACCOUNTED",
        "EXECUTION_RECORDED",
        "RISK_EVALUATED",
    }
    for row in rows:
        category = str(row.get("category") or "")
        event_type = str(row.get("event_type") or "")
        if category in {"risk", "risk_control"} or event_type in risk_event_types:
            selected.append(dict(row))
            if len(selected) >= limit:
                break
    return tuple(selected)


def _latest_kill_switch_metadata(
    rows: Sequence[Mapping[str, Any]],
    *,
    active: bool,
) -> dict[str, Any]:
    expected = "KILL_SWITCH_ENGAGED" if active else "KILL_SWITCH_CLEARED"

    def sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
        try:
            event_id = int(row.get("id") or 0)
        except (TypeError, ValueError):
            event_id = 0
        return event_id, str(row.get("timestamp_utc") or "")

    for row in sorted(rows, key=sort_key, reverse=True):
        if str(row.get("event_type") or "") != expected:
            continue
        payload = row.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        return {
            "actor": payload.get("actor"),
            "source": payload.get("source"),
            "changed_at": payload.get("changed_at") or row.get("timestamp_utc"),
        }
    return {"actor": None, "source": None, "changed_at": None}


def _decision_metric(decision: Mapping[str, Any] | None, key: str) -> float | None:
    if not decision:
        return None
    metrics = decision.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    return _finite(metrics.get(key))


def _decision_int(decision: Mapping[str, Any] | None, key: str) -> int | None:
    if not decision:
        return None
    value = decision.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_risk_dashboard_snapshot(
    *,
    account_id: str,
    mode: str,
    policy: RiskPolicy,
    state: RiskState,
    recent_rows: Sequence[Mapping[str, Any]] = (),
    profile_updated_at: str | None = None,
    now: datetime | None = None,
    required_round_trip_orders: int = 2,
) -> RiskDashboardSnapshot:
    """Build a deterministic, broker-independent dashboard snapshot."""

    normalized_account = str(account_id).strip()
    if not normalized_account:
        raise RiskReportError("Sandbox account id is required for Risk Dashboard.")
    normalized_mode = normalize_risk_mode(mode)
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = [dict(item) for item in recent_rows]
    latest = _latest_decision(rows)
    latest_status = str((latest or {}).get("status") or "READY").upper()

    if state.risk_resync_required:
        engine_status = "RESYNC_REQUIRED"
    elif state.kill_switch_active:
        engine_status = "HALTED"
    elif latest_status in {"RUNTIME_ERROR", "NOT_ENFORCED"}:
        engine_status = latest_status
    else:
        engine_status = "ACTIVE"

    equity = _finite(state.last_equity_rub)
    cash = _finite(state.last_cash_rub)
    daily_start = _finite(state.daily_start_equity_rub)
    weekly_start = _finite(state.weekly_start_equity_rub)
    high_watermark = _finite(state.high_watermark_equity_rub)
    daily_pnl = equity - daily_start if equity is not None and daily_start is not None else None
    weekly_pnl = equity - weekly_start if equity is not None and weekly_start is not None else None
    daily_return = _safe_ratio(daily_pnl, daily_start)
    weekly_return = _safe_ratio(weekly_pnl, weekly_start)
    drawdown = (
        (equity - high_watermark) / high_watermark
        if equity is not None and high_watermark is not None and high_watermark > 0
        else None
    )
    snapshot_at = _parse_datetime(state.last_snapshot_at)
    snapshot_age = (
        max(0.0, (current_time - snapshot_at).total_seconds())
        if snapshot_at is not None
        else None
    )

    current_lots = _decision_int(latest, "current_lots")
    approved_target_lots = _decision_int(latest, "approved_target_lots")
    requested_target_lots = _decision_int(latest, "requested_target_lots")
    approved_delta_lots = _decision_int(latest, "approved_delta_lots")
    securities_value = _decision_metric(latest, "securities_value_rub")
    price_per_lot = _decision_metric(latest, "price_per_lot_rub")
    risk_per_lot = _decision_metric(latest, "risk_per_lot_rub")
    position_share = _safe_ratio(securities_value, equity)
    latest_order_value = (
        abs(approved_delta_lots) * price_per_lot
        if approved_delta_lots is not None and price_per_lot is not None
        else None
    )
    position_risk = (
        max(0, current_lots or approved_target_lots or 0) * risk_per_lot
        if risk_per_lot is not None
        else None
    )

    orders_limit = policy.max_orders_per_day
    orders_used = int(state.daily_order_count)
    orders_remaining = (
        max(0, int(orders_limit) - orders_used)
        if orders_limit is not None
        else None
    )
    turnover_used = float(state.daily_turnover_rub)
    turnover_limit = _finite(policy.max_daily_turnover_rub)
    turnover_remaining = (
        max(0.0, turnover_limit - turnover_used)
        if turnover_limit is not None
        else None
    )
    cash_reserve_surplus = (
        cash - policy.cash_reserve_rub if cash is not None else None
    )

    round_trip_blockers: list[str] = []
    if state.kill_switch_active:
        round_trip_blockers.append("Включён kill switch.")
    if state.risk_resync_required:
        round_trip_blockers.append("Требуется risk resync.")
    if orders_remaining is not None and orders_remaining < required_round_trip_orders:
        round_trip_blockers.append(
            f"Недостаточно свободных заявок: {orders_remaining}; требуется "
            f"{required_round_trip_orders}."
        )
    estimated_round_trip_turnover = (
        price_per_lot * required_round_trip_orders
        if price_per_lot is not None
        else None
    )
    if turnover_remaining is not None:
        if estimated_round_trip_turnover is None:
            round_trip_blockers.append(
                "Неизвестна цена лота для проверки дневного оборота."
            )
        elif turnover_remaining < estimated_round_trip_turnover:
            round_trip_blockers.append(
                "Недостаточно свободного дневного оборота для полного цикла."
            )
    round_trip_ready = not round_trip_blockers

    latest_breaches = [
        str(item)
        for item in ((latest or {}).get("breaches") or [])
    ]
    latest_reasons = [
        str(item)
        for item in ((latest or {}).get("reasons") or [])
    ]
    kill_switch_meta = _latest_kill_switch_metadata(
        rows,
        active=bool(state.kill_switch_active),
    )

    summary = {
        "equity_rub": equity,
        "cash_rub": cash,
        "cash_reserve_rub": policy.cash_reserve_rub,
        "cash_reserve_surplus_rub": cash_reserve_surplus,
        "daily_pnl_rub": daily_pnl,
        "weekly_pnl_rub": weekly_pnl,
        "daily_return": daily_return,
        "weekly_return": weekly_return,
        "drawdown": drawdown,
        "daily_turnover_rub": turnover_used,
        "daily_turnover_limit_rub": turnover_limit,
        "daily_turnover_remaining_rub": turnover_remaining,
        "daily_order_count": orders_used,
        "max_orders_per_day": orders_limit,
        "orders_remaining": orders_remaining,
        "snapshot_at": state.last_snapshot_at,
        "snapshot_age_seconds": snapshot_age,
        "last_evaluated_at": state.last_evaluated_at,
        "last_execution_at": state.last_execution_at,
        "kill_switch_active": state.kill_switch_active,
        "kill_switch_reason": state.kill_switch_reason,
        "kill_switch_set_at": (
            state.kill_switch_set_at or kill_switch_meta.get("changed_at")
        ),
        "kill_switch_actor": kill_switch_meta.get("actor"),
        "kill_switch_source": kill_switch_meta.get("source"),
        "kill_switch_reduce_only_allowed": (
            policy.allow_risk_reducing_orders_during_halt
        ),
        "risk_resync_required": state.risk_resync_required,
        "risk_resync_reason": state.risk_resync_reason,
        "latest_decision_status": latest_status,
        "latest_decision_status_title": STATUS_TITLES_RU.get(
            latest_status, latest_status
        ),
        "requested_target_lots": requested_target_lots,
        "approved_target_lots": approved_target_lots,
        "current_lots": current_lots,
        "latest_breaches": latest_breaches,
        "latest_breach_titles": [
            BREACH_TITLES_RU.get(code, code) for code in latest_breaches
        ],
        "latest_reasons": latest_reasons,
        "price_per_lot_rub": price_per_lot,
        "estimated_round_trip_turnover_rub": estimated_round_trip_turnover,
        "required_round_trip_orders": required_round_trip_orders,
        "round_trip_ready": round_trip_ready,
        "round_trip_blockers": round_trip_blockers,
    }

    limit_rows: list[RiskLimitUsage] = []

    def append_max(
        code: str,
        current: float | int | None,
        limit: float | int | None,
        unit: str,
        note: str = "",
    ) -> None:
        current_value = _finite(current)
        limit_value = _finite(limit)
        status, remaining, utilization = _max_limit_status(
            current_value, limit_value
        )
        limit_rows.append(
            RiskLimitUsage(
                code=code,
                title=LIMIT_TITLES_RU.get(code, code),
                status=status,
                unit=unit,
                current=current,
                limit=limit,
                remaining=remaining,
                utilization=utilization,
                note=note,
            )
        )

    append_max(
        "MAX_POSITION_LOTS",
        current_lots,
        policy.max_position_lots,
        "лоты",
    )
    append_max(
        "MAX_POSITION_VALUE",
        securities_value,
        policy.max_position_value_rub,
        "₽",
    )
    append_max(
        "MAX_POSITION_SHARE",
        position_share,
        policy.max_position_share_of_equity,
        "%",
    )
    append_max(
        "MAX_ORDER_VALUE",
        latest_order_value,
        policy.max_order_value_rub,
        "₽",
    )

    cash_status, cash_remaining, cash_utilization = _min_limit_status(
        cash, float(policy.cash_reserve_rub)
    )
    limit_rows.append(
        RiskLimitUsage(
            code="CASH_RESERVE",
            title=LIMIT_TITLES_RU["CASH_RESERVE"],
            status=cash_status,
            unit="₽",
            current=cash,
            limit=policy.cash_reserve_rub,
            remaining=cash_remaining,
            utilization=cash_utilization,
            note="remaining = кэш минус обязательный резерв",
        )
    )

    risk_budget = _decision_metric(latest, "risk_budget_rub")
    if risk_budget is None:
        risk_budget = _finite(policy.risk_per_trade_rub)
    append_max(
        "RISK_PER_TRADE",
        position_risk,
        risk_budget,
        "₽",
        "По последней доступной ATR/стоп-дистанции.",
    )
    append_max(
        "DAILY_LOSS_LIMIT_RUB",
        max(0.0, -(daily_pnl or 0.0)) if daily_pnl is not None else None,
        policy.daily_loss_limit_rub,
        "₽",
    )
    append_max(
        "DAILY_LOSS_LIMIT_FRACTION",
        max(0.0, -(daily_return or 0.0)) if daily_return is not None else None,
        policy.daily_loss_limit_fraction,
        "%",
    )
    append_max(
        "WEEKLY_LOSS_LIMIT_RUB",
        max(0.0, -(weekly_pnl or 0.0)) if weekly_pnl is not None else None,
        policy.weekly_loss_limit_rub,
        "₽",
    )
    append_max(
        "WEEKLY_LOSS_LIMIT_FRACTION",
        max(0.0, -(weekly_return or 0.0)) if weekly_return is not None else None,
        policy.weekly_loss_limit_fraction,
        "%",
    )
    append_max(
        "MAX_DRAWDOWN",
        max(0.0, -(drawdown or 0.0)) if drawdown is not None else None,
        policy.max_drawdown_fraction,
        "%",
    )
    append_max(
        "MAX_DAILY_TURNOVER",
        turnover_used,
        policy.max_daily_turnover_rub,
        "₽",
    )
    append_max(
        "MAX_ORDERS_PER_DAY",
        orders_used,
        orders_limit,
        "шт.",
        f"Для полного BUY→SELL требуется минимум {required_round_trip_orders}.",
    )
    append_max(
        "MAX_SNAPSHOT_AGE",
        snapshot_age,
        policy.max_snapshot_age_seconds,
        "с",
    )

    warnings: list[str] = []
    if orders_limit is not None and orders_used >= int(orders_limit):
        warnings.append(
            "Дневной лимит заявок достигнут: "
            f"{orders_used}/{int(orders_limit)}; новые входы заблокированы "
            "до смены торгового дня."
        )
    if (
        turnover_limit is not None
        and turnover_used >= turnover_limit
    ):
        warnings.append(
            "Дневной лимит оборота достигнут: "
            f"{turnover_used:.2f}/{turnover_limit:.2f} ₽; новые входы "
            "заблокированы до смены торгового дня."
        )
    if not state.last_snapshot_at:
        warnings.append("RiskState ещё не содержит подтверждённый снимок портфеля.")
    if latest is None:
        warnings.append("В журнале ещё нет RiskDecision для выбранного счёта.")
    if state.kill_switch_active:
        warnings.append(
            "Kill switch активен: новые входы заблокированы, reduce-only может "
            "оставаться разрешённым при однозначном состоянии."
        )
    if state.risk_resync_required:
        warnings.append(
            "Обнаружена внешняя активность: новые заявки заблокированы до "
            "reconciliation и явного reset baselines."
        )
    warnings.extend(round_trip_blockers)

    return RiskDashboardSnapshot(
        generated_at=current_time.isoformat(),
        account_id=normalized_account,
        mode=normalized_mode,
        engine_status=engine_status,
        engine_status_title=STATUS_TITLES_RU.get(engine_status, engine_status),
        policy_hash=policy.policy_hash,
        profile_updated_at=profile_updated_at,
        summary=summary,
        limits=tuple(limit_rows),
        latest_decision=latest,
        recent_events=_recent_risk_events(rows),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def load_risk_dashboard_snapshot(
    *,
    profile_store: RiskProfileStore,
    state_store: RiskStateStore,
    journal: EventJournal,
    account_id: str,
    mode: str = "SANDBOX_EXECUTION",
    now: datetime | None = None,
    recent_limit: int = 500,
) -> RiskDashboardSnapshot:
    """Load checksummed policy/state and build a read-only dashboard snapshot."""

    try:
        loaded = profile_store.require_profile(normalize_risk_mode(mode))
        profile_scope = str(loaded.get("account_scope") or "").strip()
        if profile_scope and profile_scope != str(account_id).strip():
            raise RiskPersistenceError(
                "Risk profile account scope mismatch: "
                f"{profile_scope} != {str(account_id).strip()}."
            )
        state = state_store.load_account(account_id)
        rows = journal.recent(
            limit=max(50, int(recent_limit)),
            account_id=account_id,
        )
    except (RiskPersistenceError, OSError, ValueError, TypeError) as exc:
        raise RiskReportError(str(exc)) from exc
    return build_risk_dashboard_snapshot(
        account_id=account_id,
        mode=mode,
        policy=loaded["policy"],
        state=state,
        recent_rows=rows,
        profile_updated_at=loaded.get("updated_at"),
        now=now,
    )


def write_dashboard_snapshot(
    snapshot: RiskDashboardSnapshot,
    destination: str | Path,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return destination


def _filter_rows_since(
    rows: Iterable[Mapping[str, Any]],
    since: datetime | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    normalized_since = (
        since.astimezone(timezone.utc)
        if since is not None and since.tzinfo is not None
        else since.replace(tzinfo=timezone.utc)
        if since is not None
        else None
    )
    for row in rows:
        parsed = _parse_datetime(row.get("timestamp_utc"))
        if normalized_since is not None and (
            parsed is None or parsed < normalized_since
        ):
            continue
        result.append(dict(row))
    return result


def build_risk_burn_in_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    account_id: str | None = None,
    now: datetime | None = None,
    since: datetime | None = None,
    software_version: str | None = None,
) -> RiskBurnInReport:
    """Audit causal order, idempotency and fail-closed behaviour in a journal slice."""

    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    filtered = _filter_rows_since(rows, since)
    if account_id:
        filtered = [
            row
            for row in filtered
            if str(row.get("account_id") or "") == str(account_id)
        ]
    ordered = sorted(filtered, key=lambda item: int(item.get("id") or 0))
    version_marker_found = False
    version_marker_event_id: int | None = None
    normalized_version = str(software_version or "").strip()
    if normalized_version:
        for row in ordered:
            if (
                str(row.get("category") or "") == "session"
                and str(row.get("event_type") or "") == "STARTED"
            ):
                payload = row.get("payload")
                payload = payload if isinstance(payload, Mapping) else {}
                if str(payload.get("software_version") or "") == normalized_version:
                    version_marker_found = True
                    version_marker_event_id = int(row.get("id") or 0)
                    break
        if version_marker_found and version_marker_event_id is not None:
            ordered = [
                row
                for row in ordered
                if int(row.get("id") or 0) >= version_marker_event_id
            ]

    event_types = Counter(str(row.get("event_type") or "") for row in ordered)
    categories = Counter(str(row.get("category") or "") for row in ordered)
    severities = Counter(str(row.get("severity") or "") for row in ordered)
    cycle_statuses = Counter(
        str(row.get("status") or row.get("event_type") or "")
        for row in ordered
        if str(row.get("category") or "") == "cycle"
    )
    risk_statuses = Counter()
    execution_sources = Counter()
    for row in ordered:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if str(row.get("event_type") or "") == "RISK_EVALUATED":
            decision = payload.get("risk_decision")
            status = (
                decision.get("status")
                if isinstance(decision, Mapping)
                else row.get("status")
            )
            risk_statuses[str(status or "UNKNOWN")] += 1
        source = payload.get("execution_source")
        if source:
            execution_sources[str(source).upper()] += 1

    by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ordered:
        order_id = str(row.get("order_id") or "").strip()
        if order_id:
            by_order[order_id].append(row)

    checks: list[BurnInCheck] = []
    if normalized_version:
        checks.append(
            BurnInCheck(
                code="SOFTWARE_VERSION_SCOPE",
                status="PASS" if version_marker_found else "WARN",
                title="Граница burn-in по версии программы",
                details=(
                    f"Анализ начинается с первого STARTED для {normalized_version} "
                    f"(event id {version_marker_event_id})."
                    if version_marker_found
                    else f"STARTED с software_version={normalized_version} не найден; "
                    "использован только временной/account-фильтр."
                ),
            )
        )

    duplicate_submissions = tuple(
        order_id
        for order_id, events in by_order.items()
        if sum(1 for event in events if event.get("event_type") == "ORDER_SUBMITTED") > 1
    )
    checks.append(
        BurnInCheck(
            code="DUPLICATE_ORDER_SUBMISSION",
            status="FAIL" if duplicate_submissions else "PASS",
            title="Повторная отправка одной экономической заявки",
            details=(
                f"Обнаружено: {len(duplicate_submissions)}."
                if duplicate_submissions
                else "Повторных ORDER_SUBMITTED для одного order_id нет."
            ),
            affected_ids=duplicate_submissions,
        )
    )

    filled_without_reconciliation: list[str] = []
    reconciled_without_accounting: list[str] = []
    causal_order_errors: list[str] = []
    missing_risk_authorization: list[str] = []
    for order_id, events in by_order.items():
        event_names = [str(event.get("event_type") or "") for event in events]
        positions: dict[str, int] = {}
        for index, name in enumerate(event_names):
            positions.setdefault(name, index)
        if "ORDER_SUBMITTED" in positions:
            submitted = events[positions["ORDER_SUBMITTED"]]
            payload = submitted.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            # Only PRIMARY strategy orders require a preceding RiskDecision.
            # Diagnostic and safe-close operations use their own explicit
            # operator confirmations and are accounted separately in RiskState.
            if str(submitted.get("category") or "") == "order" and (
                not payload.get("risk_decision_id")
                or not payload.get("risk_policy_hash")
            ):
                missing_risk_authorization.append(order_id)
        if "FILLED" in positions and "PORTFOLIO_RECONCILED" not in positions:
            filled_without_reconciliation.append(order_id)
        if "PORTFOLIO_RECONCILED" in positions:
            if "EXECUTION_RECORDED" not in positions or "RISK_ACCOUNTED" not in positions:
                reconciled_without_accounting.append(order_id)
            elif not (
                positions.get("FILLED", -1)
                < positions["PORTFOLIO_RECONCILED"]
                < positions["EXECUTION_RECORDED"]
                < positions["RISK_ACCOUNTED"]
            ):
                causal_order_errors.append(order_id)

    checks.extend(
        [
            BurnInCheck(
                code="FILLED_WITHOUT_RECONCILIATION",
                status="FAIL" if filled_without_reconciliation else "PASS",
                title="Исполнение без portfolio reconciliation",
                details=(
                    f"Обнаружено: {len(filled_without_reconciliation)}."
                    if filled_without_reconciliation
                    else "Все FILLED имеют PORTFOLIO_RECONCILED."
                ),
                affected_ids=tuple(filled_without_reconciliation),
            ),
            BurnInCheck(
                code="RECONCILIATION_WITHOUT_RISK_ACCOUNTING",
                status="FAIL" if reconciled_without_accounting else "PASS",
                title="Сверка без RiskState accounting",
                details=(
                    f"Обнаружено: {len(reconciled_without_accounting)}."
                    if reconciled_without_accounting
                    else "Все reconciled fills учтены в RiskState."
                ),
                affected_ids=tuple(reconciled_without_accounting),
            ),
            BurnInCheck(
                code="CAUSAL_EVENT_ORDER",
                status="FAIL" if causal_order_errors else "PASS",
                title="Причинный порядок post-trade событий",
                details=(
                    f"Нарушений: {len(causal_order_errors)}."
                    if causal_order_errors
                    else "FILLED → PORTFOLIO_RECONCILED → EXECUTION_RECORDED → RISK_ACCOUNTED."
                ),
                affected_ids=tuple(causal_order_errors),
            ),
            BurnInCheck(
                code="RISK_AUTHORIZATION_PRESENT",
                status="FAIL" if missing_risk_authorization else "PASS",
                title="RiskDecision присутствует до отправки",
                details=(
                    f"Заявок без risk_decision_id/policy_hash: {len(missing_risk_authorization)}."
                    if missing_risk_authorization
                    else "Все ORDER_SUBMITTED связаны с RiskDecision."
                ),
                affected_ids=tuple(missing_risk_authorization),
            ),
        ]
    )

    runtime_error_types = {
        "RISK_RUNTIME_ERROR",
        "RISK_EXECUTION_RUNTIME_ERROR",
        "RISK_EXECUTION_ACCOUNTING_FAILED",
        "RISK_AUTHORIZATION_MISSING",
        "STATE_SAVE_FAILED",
    }
    runtime_errors = tuple(
        str(row.get("id") or "")
        for row in ordered
        if str(row.get("event_type") or "") in runtime_error_types
    )
    checks.append(
        BurnInCheck(
            code="RISK_RUNTIME_ERRORS",
            status="FAIL" if runtime_errors else "PASS",
            title="Ошибки runtime/persistence Risk Engine",
            details=(
                f"Обнаружено: {len(runtime_errors)}."
                if runtime_errors
                else "Критических runtime/persistence ошибок нет."
            ),
            affected_ids=runtime_errors,
        )
    )

    safe_canonical_snapshot_indices: list[int] = []
    for index, row in enumerate(ordered):
        if str(row.get("event_type") or "") != "PORTFOLIO_STATE_PUBLISHED":
            continue
        payload = row.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        reconciliation_counts = payload.get("reconciliation_counts")
        reconciliation_counts = (
            reconciliation_counts
            if isinstance(reconciliation_counts, Mapping)
            else {}
        )
        non_matched = sum(
            int(count or 0)
            for status, count in reconciliation_counts.items()
            if str(status).upper() != "MATCHED"
        )
        if (
            str(payload.get("portfolio_source") or "").upper() == "CANONICAL"
            and str(row.get("status") or "").upper() in {"READY", "EMPTY"}
            and str(payload.get("freshness") or "").upper() == "FRESH"
            and not bool(payload.get("blocking"))
            and non_matched == 0
        ):
            safe_canonical_snapshot_indices.append(index)

    recovered_transient_api_failures: list[str] = []
    unresolved_transient_api_failures: list[str] = []
    non_transient_api_failures: list[str] = []
    for index, row in enumerate(ordered):
        if str(row.get("event_type") or "") != "API_REQUEST_FAILED":
            continue
        payload = row.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        event_id = str(row.get("id") or "")
        if bool(payload.get("transient")):
            recovered = any(item > index for item in safe_canonical_snapshot_indices)
            if recovered:
                recovered_transient_api_failures.append(event_id)
            else:
                unresolved_transient_api_failures.append(event_id)
        else:
            non_transient_api_failures.append(event_id)

    api_failure_status = (
        "FAIL"
        if unresolved_transient_api_failures or non_transient_api_failures
        else "WARN"
        if recovered_transient_api_failures
        else "PASS"
    )
    checks.append(
        BurnInCheck(
            code="API_FAILURE_CLASSIFICATION",
            status=api_failure_status,
            title="Классификация отказов T-Invest API",
            details=(
                "Recovered transient outages: "
                f"{len(recovered_transient_api_failures)}; unresolved transient: "
                f"{len(unresolved_transient_api_failures)}; non-transient: "
                f"{len(non_transient_api_failures)}."
                if (
                    recovered_transient_api_failures
                    or unresolved_transient_api_failures
                    or non_transient_api_failures
                )
                else "API_REQUEST_FAILED за период отсутствуют."
            ),
            affected_ids=tuple(
                unresolved_transient_api_failures
                + non_transient_api_failures
                + recovered_transient_api_failures
            ),
        )
    )

    expected_policy_blocks: list[str] = []
    policy_block_breaches: Counter[str] = Counter()
    for row in ordered:
        if str(row.get("event_type") or "") != "RISK_EVALUATED":
            continue
        payload = row.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        decision = payload.get("risk_decision")
        decision = decision if isinstance(decision, Mapping) else {}
        status = str(decision.get("status") or row.get("status") or "")
        expected = bool(payload.get("expected_policy_block"))
        if status != "BLOCKED" or not expected:
            continue
        expected_policy_blocks.append(str(row.get("id") or ""))
        for breach in decision.get("breaches") or payload.get("breaches") or []:
            policy_block_breaches[str(breach)] += 1
    checks.append(
        BurnInCheck(
            code="EXPECTED_POLICY_BLOCKS",
            status="WARN" if expected_policy_blocks else "PASS",
            title="Штатные блокировки риск-политикой",
            details=(
                "Risk Engine штатно блокировал новые входы: "
                + ", ".join(
                    f"{code}={count}"
                    for code, count in sorted(policy_block_breaches.items())
                )
                if expected_policy_blocks
                else "Штатных policy-limit блокировок за период нет."
            ),
            affected_ids=tuple(expected_policy_blocks),
        )
    )

    resync_depth = 0
    for row in ordered:
        event_type = str(row.get("event_type") or "")
        if event_type == "EXTERNAL_ACTIVITY_DETECTED":
            resync_depth += 1
        elif event_type in {"EXTERNAL_ACTIVITY_CLEARED", "RISK_RESYNC_COMPLETED"}:
            resync_depth = max(0, resync_depth - 1)
    checks.append(
        BurnInCheck(
            code="EXTERNAL_ACTIVITY_RESOLVED",
            status="FAIL" if resync_depth else "PASS",
            title="Внешняя активность завершена risk resync",
            details=(
                "Последний внешний drift остаётся незакрытым."
                if resync_depth
                else "Незавершённого external-activity gate нет."
            ),
        )
    )

    started_sessions = Counter(
        str(row.get("session_id") or "")
        for row in ordered
        if str(row.get("category") or "") == "session"
        and str(row.get("event_type") or "") == "STARTED"
        and row.get("session_id")
    )
    stopped_sessions = Counter(
        str(row.get("session_id") or "")
        for row in ordered
        if str(row.get("category") or "") == "session"
        and str(row.get("event_type") or "") == "STOPPED"
        and row.get("session_id")
    )
    open_sessions = tuple(
        session_id
        for session_id, count in started_sessions.items()
        if count > stopped_sessions.get(session_id, 0)
    )
    checks.append(
        BurnInCheck(
            code="SESSION_CLOSURE",
            status="WARN" if open_sessions else "PASS",
            title="Штатное завершение сессий",
            details=(
                f"Незавершённых сессий в срезе: {len(open_sessions)}."
                if open_sessions
                else "Все начатые сессии имеют STOPPED."
            ),
            affected_ids=open_sessions,
        )
    )

    api_degraded = int(cycle_statuses.get("api_degraded", 0))
    checks.append(
        BurnInCheck(
            code="API_DEGRADED_EPISODES",
            status="WARN" if api_degraded else "PASS",
            title="Сетевые деградации",
            details=(
                f"За период зарегистрировано {api_degraded} api_degraded циклов."
                if api_degraded
                else "Полностью неуспешных API-циклов нет."
            ),
        )
    )

    market_idle_entered = int(event_types.get("MARKET_IDLE_ENTERED", 0))
    market_idle_exited = int(event_types.get("MARKET_IDLE_EXITED", 0))
    market_idle_heartbeats = int(event_types.get("MARKET_IDLE_HEARTBEAT", 0))
    if market_idle_entered:
        complete_transitions = min(market_idle_entered, market_idle_exited)
        checks.append(
            BurnInCheck(
                code="MARKET_IDLE_TRANSITIONS",
                status=(
                    "PASS"
                    if complete_transitions >= 1
                    else "WARN"
                ),
                title="Переход рынка через MARKET_IDLE",
                details=(
                    f"ENTERED={market_idle_entered}, EXITED={market_idle_exited}, "
                    f"HEARTBEAT={market_idle_heartbeats}; подтверждённых пар: "
                    f"{complete_transitions}."
                ),
            )
        )

    duplicate_execution_errors: list[str] = []
    execution_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in ordered:
        if str(row.get("event_type") or "") != "EXECUTION_RECORDED":
            continue
        payload = row.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        execution_id = str(
            payload.get("risk_execution_id")
            or payload.get("execution_id")
            or row.get("order_id")
            or ""
        )
        if execution_id:
            execution_rows[execution_id].append(row)
    for execution_id, events in execution_rows.items():
        non_duplicate = 0
        for event in events:
            payload = event.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            if not bool(payload.get("duplicate")):
                non_duplicate += 1
        if non_duplicate > 1:
            duplicate_execution_errors.append(execution_id)
    checks.append(
        BurnInCheck(
            code="EXECUTION_IDEMPOTENCY",
            status="FAIL" if duplicate_execution_errors else "PASS",
            title="Идемпотентность учёта исполнений",
            details=(
                f"Повторно учтённых execution_id: {len(duplicate_execution_errors)}."
                if duplicate_execution_errors
                else "Каждый execution_id учтён не более одного раза."
            ),
            affected_ids=tuple(duplicate_execution_errors),
        )
    )

    overall_status = (
        "FAIL"
        if any(check.status == "FAIL" for check in checks)
        else "WARN"
        if any(check.status == "WARN" for check in checks)
        else "PASS"
    )

    recommendations: list[str] = []
    for check in checks:
        if check.status == "PASS":
            continue
        if check.code == "API_DEGRADED_EPISODES":
            recommendations.append(
                "Проверить длительность API-циклов и отсутствие заявок во время DEGRADED."
            )
        elif check.code == "SESSION_CLOSURE":
            recommendations.append(
                "Убедиться, что незавершённая сессия не является активным процессом."
            )
        elif check.code == "EXTERNAL_ACTIVITY_RESOLVED":
            recommendations.append(
                "Завершить reconciliation и явный reset baselines перед новыми входами."
            )
        elif check.code == "EXPECTED_POLICY_BLOCKS":
            recommendations.append(
                "Штатная блокировка не является сбоем; проверьте лимит и "
                "дождитесь смены торгового дня либо примените отдельный "
                "аудируемый burn-in профиль."
            )
        elif check.code == "MARKET_IDLE_TRANSITIONS":
            recommendations.append(
                "Оставить процесс до следующего явного открытия рынка и "
                "подтвердить MARKET_IDLE_EXITED без перезапуска GUI."
            )
        else:
            recommendations.append(
                f"Разобрать проверку {check.code} до следующего Sandbox burn-in."
            )

    timestamps = [
        parsed
        for parsed in (_parse_datetime(row.get("timestamp_utc")) for row in ordered)
        if parsed is not None
    ]
    metrics = {
        "events_total": len(ordered),
        "categories": dict(categories),
        "severities": dict(severities),
        "event_types": dict(event_types),
        "cycle_statuses": dict(cycle_statuses),
        "risk_statuses": dict(risk_statuses),
        "execution_sources": dict(execution_sources),
        "unique_orders": len(by_order),
        "orders_submitted": int(event_types.get("ORDER_SUBMITTED", 0)),
        "orders_filled": int(event_types.get("FILLED", 0)),
        "orders_reconciled": int(event_types.get("PORTFOLIO_RECONCILED", 0)),
        "executions_recorded": int(event_types.get("EXECUTION_RECORDED", 0)),
        "risk_accounted": int(event_types.get("RISK_ACCOUNTED", 0)),
        "expected_policy_blocks": len(expected_policy_blocks),
        "policy_block_breaches": dict(policy_block_breaches),
        "external_activity_detected": int(
            event_types.get("EXTERNAL_ACTIVITY_DETECTED", 0)
        ),
        "kill_switch_engaged": int(event_types.get("KILL_SWITCH_ENGAGED", 0)),
        "kill_switch_cleared": int(event_types.get("KILL_SWITCH_CLEARED", 0)),
        "api_degraded_cycles": api_degraded,
        "api_transient_recovered": len(recovered_transient_api_failures),
        "api_transient_unresolved": len(unresolved_transient_api_failures),
        "api_non_transient_failures": len(non_transient_api_failures),
        "market_idle_entered": market_idle_entered,
        "market_idle_exited": market_idle_exited,
        "market_idle_heartbeats": market_idle_heartbeats,
        "market_idle_complete_transitions": min(
            market_idle_entered,
            market_idle_exited,
        ),
        "software_version_filter": normalized_version or None,
        "software_version_marker_found": version_marker_found,
        "software_version_marker_event_id": version_marker_event_id,
    }
    return RiskBurnInReport(
        generated_at=current_time.isoformat(),
        account_id=str(account_id) if account_id else None,
        period_start=min(timestamps).isoformat() if timestamps else None,
        period_end=max(timestamps).isoformat() if timestamps else None,
        overall_status=overall_status,
        metrics=metrics,
        checks=tuple(checks),
        recommendations=tuple(dict.fromkeys(recommendations)),
    )


def load_risk_burn_in_report(
    journal: EventJournal,
    *,
    account_id: str | None = None,
    hours: float | None = None,
    limit: int = 100_000,
    now: datetime | None = None,
    software_version: str | None = None,
) -> RiskBurnInReport:
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = (
        current_time - timedelta(hours=float(hours))
        if hours is not None
        else None
    )
    rows = journal.recent(
        limit=limit,
        account_id=account_id,
    )
    return build_risk_burn_in_report(
        rows,
        account_id=account_id,
        now=current_time,
        since=since,
        software_version=software_version,
    )


def write_burn_in_report(
    report: RiskBurnInReport,
    output_dir: str | Path,
) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "risk_burn_in_report.json"
    checks_path = output / "risk_burn_in_checks.csv"
    metrics_path = output / "risk_burn_in_metrics.csv"
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    with checks_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("code", "status", "title", "details", "affected_ids"),
        )
        writer.writeheader()
        for check in report.checks:
            row = check.to_dict()
            row["affected_ids"] = ";".join(row["affected_ids"])
            writer.writerow(row)
    with metrics_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("metric", "value"))
        for key, value in sorted(report.metrics.items()):
            writer.writerow(
                (
                    key,
                    json.dumps(value, ensure_ascii=False, default=str)
                    if isinstance(value, (dict, list, tuple))
                    else value,
                )
            )
    return {
        "json": json_path,
        "checks_csv": checks_path,
        "metrics_csv": metrics_path,
    }
