from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class MarketAvailability:
    """Metadata-only assessment of whether the configured order route is usable.

    ``executable`` is deliberately tri-state:

    - ``True``: the provider explicitly confirms the API and selected order route;
    - ``False``: the provider explicitly denies either one;
    - ``None``: the response is incomplete and must not be used to invent a
      market transition.
    """

    executable: bool | None
    reason_code: str
    selected_order_type: str
    order_availability_key: str
    trading_status_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_market_status(
    status: Mapping[str, Any],
    *,
    order_type: str,
) -> MarketAvailability:
    api_key = "apiTradeAvailableFlag"
    normalized_order_type = str(order_type).strip().upper()
    if normalized_order_type == "MARKET":
        order_key = "marketOrderAvailableFlag"
    elif "bestpriceOrderAvailableFlag" in status:
        order_key = "bestpriceOrderAvailableFlag"
    else:
        # T-Invest responses may omit a dedicated BESTPRICE flag.  Limit-order
        # availability is the same conservative proxy used by the execution
        # precheck in TBankSandboxClient.
        order_key = "limitOrderAvailableFlag"

    status_text = str(
        status.get("tradingStatus")
        or status.get("trading_status")
        or "UNKNOWN"
    )
    api_known = api_key in status
    order_known = order_key in status

    # Any explicit denial is sufficient to enter MARKET_IDLE.  Explicitly
    # resuming execution is stricter: both the API-trading flag and the flag
    # for the selected order route must be present and true.  A partial
    # response therefore remains tri-state/uncertain rather than silently
    # defaulting a missing permission to true.
    api_available = bool(status.get(api_key)) if api_known else None
    order_available = bool(status.get(order_key)) if order_known else None
    if api_known and api_available is False:
        executable: bool | None = False
        reason = "API_TRADE_UNAVAILABLE"
    elif order_known and order_available is False:
        executable = False
        reason = "SELECTED_ORDER_TYPE_UNAVAILABLE"
    elif api_known and order_known and api_available and order_available:
        executable = True
        reason = "EXECUTABLE"
    elif not api_known and not order_known:
        executable = None
        reason = "STATUS_FIELDS_MISSING"
    else:
        executable = None
        reason = "STATUS_UNCERTAIN"

    return MarketAvailability(
        executable=executable,
        reason_code=reason,
        selected_order_type=normalized_order_type,
        order_availability_key=order_key,
        trading_status_text=status_text,
    )


def parse_utc_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def seconds_since(value: Any, now: datetime) -> float | None:
    parsed = parse_utc_datetime(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())
