from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from http.client import IncompleteRead
from typing import Any, Callable, ClassVar

from .tls_support import enable_system_trust_store, resolve_ca_bundle

# Must run before Requests/urllib3 create SSL contexts. On Windows this lets
# Python validate T-Bank certificates through the native certificate store.
enable_system_trust_store()

import pandas as pd
import requests
from urllib3.exceptions import ProtocolError

logger = logging.getLogger(__name__)

_CL1_REQUEST_TIMESTAMP_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T"
    r"([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{9})Z",
    re.ASCII,
)
_PROVIDER_ERROR_CODE_RE = re.compile(r"[0-9]{1,10}", re.ASCII)
_PROVIDER_ERROR_CATEGORIES = {
    400: "REQUEST_REJECTED",
    401: "AUTHENTICATION_REJECTED",
    403: "AUTHORIZATION_REJECTED",
    404: "RESOURCE_NOT_FOUND",
    408: "REQUEST_TIMEOUT",
    409: "REQUEST_CONFLICT",
    429: "RATE_LIMITED",
}


def _safe_provider_error_identity(
    status_code: object,
    details: object,
) -> dict[str, str]:
    """Return a finite error identity without copying provider prose."""

    if type(status_code) is not int or not 100 <= status_code <= 599:
        return {}
    provider_error_code: str | None = None
    if type(details) is dict:
        for key in ("message", "description"):
            value = details.get(key)
            if type(value) is str and _PROVIDER_ERROR_CODE_RE.fullmatch(value):
                provider_error_code = value
                break
        if provider_error_code is None:
            value = details.get("code")
            if type(value) is int and 0 <= value <= 999_999_999:
                provider_error_code = str(value)
            elif type(value) is str and _PROVIDER_ERROR_CODE_RE.fullmatch(value):
                provider_error_code = value
    if provider_error_code is None:
        provider_error_code = f"HTTP_{status_code}"
    if status_code >= 500:
        category = "SERVER_REJECTED"
    else:
        category = _PROVIDER_ERROR_CATEGORIES.get(status_code, "HTTP_REJECTED")
    return {
        "provider_error_code": provider_error_code,
        "provider_error_category": category,
    }


def _safe_cursor_request_boundary(payload: object) -> dict[str, str]:
    """Extract only the exact non-secret CL1 cursor time interval."""

    if type(payload) is not dict:
        return {}
    from_inclusive = payload.get("from")
    to_exclusive = payload.get("to")
    from_match = (
        _CL1_REQUEST_TIMESTAMP_RE.fullmatch(from_inclusive)
        if type(from_inclusive) is str
        else None
    )
    to_match = (
        _CL1_REQUEST_TIMESTAMP_RE.fullmatch(to_exclusive)
        if type(to_exclusive) is str
        else None
    )
    if not (
        type(from_inclusive) is str
        and type(to_exclusive) is str
        and from_match is not None
        and to_match is not None
        and from_inclusive < to_exclusive
    ):
        return {}
    try:
        datetime(*map(int, from_match.groups()[:6]), tzinfo=timezone.utc)
        datetime(*map(int, to_match.groups()[:6]), tzinfo=timezone.utc)
    except ValueError:
        return {}
    return {
        "request_from_inclusive": from_inclusive,
        "request_to_exclusive": to_exclusive,
    }


class TBankAPIError(RuntimeError):
    """Raised on an unsuccessful T-Invest REST call."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        details: Any = None,
        transient: bool = False,
        service: str | None = None,
        method: str | None = None,
        tracking_id: str | None = None,
        retry_after_seconds: float | None = None,
        error_class: str | None = None,
        direct_response: bool = False,
        redirect_followed: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details
        self.transient = bool(transient)
        self.service = service
        self.method = method
        self.tracking_id = tracking_id
        self.retry_after_seconds = retry_after_seconds
        self.error_class = str(error_class) if error_class else None
        self.direct_response = bool(direct_response)
        self.redirect_followed = bool(redirect_followed)


def _iter_nested_exceptions(exc: BaseException):
    """Yield an exception and nested transport exceptions without recursion loops."""

    stack: list[BaseException] = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        yield current

        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                stack.append(linked)

        pending = list(getattr(current, "args", ()))
        while pending:
            item = pending.pop()
            if isinstance(item, BaseException):
                stack.append(item)
            elif isinstance(item, (tuple, list)):
                pending.extend(item)


def is_transient_transport_exception(exc: BaseException) -> bool:
    """Return True for retryable HTTP transport interruptions.

    ``requests`` normally wraps a truncated response in
    ``ChunkedEncodingError``/``ProtocolError``, but adapters and tests can also
    expose ``http.client.IncompleteRead`` directly.  TLS verification errors are
    deliberately handled earlier and remain non-transient.
    """

    if isinstance(
        exc,
        (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
            IncompleteRead,
        ),
    ):
        return True

    nested = tuple(_iter_nested_exceptions(exc))
    if any(isinstance(item, IncompleteRead) for item in nested):
        return True

    # A direct urllib3 ProtocolError is retryable only when its nested cause is
    # an incomplete/chunked read.  Generic protocol/schema failures must not be
    # silently converted into transient outages.
    if isinstance(exc, ProtocolError):
        text = " ".join(str(item) for item in nested).lower()
        return "incompleteread" in text or "response ended prematurely" in text
    return False


def quotation_to_float(value: dict[str, Any] | None) -> float:
    if not value:
        return 0.0
    units = Decimal(str(value.get("units", "0")))
    nano = Decimal(str(value.get("nano", 0))) / Decimal("1000000000")
    return float(units + nano)


def decimal_to_quotation(value: Decimal | float | int) -> dict[str, Any]:
    amount = Decimal(str(value))
    units = int(amount.to_integral_value(rounding=ROUND_DOWN))
    nano = int((amount - Decimal(units)) * Decimal("1000000000"))
    return {"units": str(units), "nano": nano}


@dataclass(slots=True)
class TBankSandboxClient:
    """REST client limited to T-Invest Sandbox order methods.

    There is deliberately no production order endpoint in this class.
    """

    token: str
    timeout_seconds: float | None = None
    connect_timeout_seconds: float = 8.0
    read_timeout_seconds: float = 25.0
    app_name: str = "moex-research-robot-v3.10.0"
    max_retries: int = 3
    retry_backoff_seconds: float = 0.5
    retry_jitter_seconds: float = 0.25
    ca_bundle_path: str | None = None
    telemetry_callback: Callable[[dict[str, Any]], None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _session: requests.Session = field(init=False, repr=False, compare=False)
    _verify: bool | str = field(init=False, repr=False, compare=False)
    _last_response_meta: dict[str, Any] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    # Official dedicated Sandbox host. Every service call in this client stays
    # in the test contour; there is no route for production order execution.
    BASE_URL = "https://sandbox-invest-public-api.tbank.ru/rest"
    TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}

    # T-Invest limits the maximum requested time span by candle interval.
    # Long ranges are split into several valid requests and de-duplicated.
    CANDLE_MAX_SPAN: ClassVar[dict[str, timedelta]] = {
        "CANDLE_INTERVAL_10_MIN": timedelta(days=7),
        "CANDLE_INTERVAL_15_MIN": timedelta(days=21),
        "CANDLE_INTERVAL_30_MIN": timedelta(days=21),
        "CANDLE_INTERVAL_HOUR": timedelta(days=90),
        "CANDLE_INTERVAL_DAY": timedelta(days=365 * 6),
    }
    CANDLE_MAX_LIMITS: ClassVar[dict[str, int]] = {
        "CANDLE_INTERVAL_1_MIN": 2400,
        "CANDLE_INTERVAL_5_MIN": 2400,
        "CANDLE_INTERVAL_15_MIN": 2400,
        "CANDLE_INTERVAL_HOUR": 2400,
        "CANDLE_INTERVAL_DAY": 2400,
        "CANDLE_INTERVAL_2_MIN": 1200,
        "CANDLE_INTERVAL_3_MIN": 750,
        "CANDLE_INTERVAL_10_MIN": 1200,
        "CANDLE_INTERVAL_30_MIN": 1200,
        "CANDLE_INTERVAL_2_HOUR": 2400,
        "CANDLE_INTERVAL_4_HOUR": 700,
        "CANDLE_INTERVAL_WEEK": 300,
        "CANDLE_INTERVAL_MONTH": 120,
        "CANDLE_INTERVAL_5_SEC": 2500,
        "CANDLE_INTERVAL_10_SEC": 1250,
        "CANDLE_INTERVAL_30_SEC": 2500,
    }
    _INSTRUMENT_CACHE: ClassVar[dict[tuple[str, str], dict[str, Any]]] = {}
    _INSTRUMENT_ID_CACHE: ClassVar[dict[tuple[str, str], dict[str, Any]]] = {}

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise ValueError("T-Invest token must not be empty.")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative.")
        if self.timeout_seconds is not None:
            if self.timeout_seconds <= 0:
                raise ValueError("timeout_seconds must be positive.")
            self.connect_timeout_seconds = float(self.timeout_seconds)
            self.read_timeout_seconds = float(self.timeout_seconds)
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise ValueError("connect/read timeouts must be positive.")
        if self.retry_jitter_seconds < 0:
            raise ValueError("retry_jitter_seconds must not be negative.")
        self._verify = resolve_ca_bundle(self.ca_bundle_path)
        self._session = requests.Session()
        self._session.verify = self._verify
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-app-name": self.app_name,
            }
        )

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "TBankSandboxClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def last_response_meta(self) -> dict[str, Any]:
        return dict(self._last_response_meta)

    def set_telemetry_callback(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        """Attach a non-fatal observer for request timing and retry events.

        The callback never receives the API token or request payload.  Any
        callback error is isolated from the trading request and written only
        to the technical DEBUG log.
        """
        self.telemetry_callback = callback

    def _emit_telemetry(self, event: dict[str, Any]) -> None:
        callback = self.telemetry_callback
        if callback is None:
            return
        try:
            callback(dict(event))
        except Exception:
            logger.debug(
                "T-Invest telemetry callback failed.",
                exc_info=True,
            )

    def _complete_request_meta(
        self,
        *,
        event_type: str,
        service: str,
        method: str,
        started_at: datetime,
        started_perf: float,
        attempt_count: int,
        retry_delays: list[float],
        status_code: int | None,
        tracking_id: str | None,
        error: str | None = None,
        transient: bool | None = None,
        response_headers: dict[str, str] | None = None,
        error_class: str | None = None,
    ) -> dict[str, Any]:
        completed_at = datetime.now(timezone.utc)
        meta: dict[str, Any] = {
            "event_type": event_type,
            "service": service,
            "method": method,
            "request_started_at": started_at.isoformat(),
            "request_completed_at": completed_at.isoformat(),
            "request_duration_seconds": round(
                max(0.0, time.perf_counter() - started_perf),
                6,
            ),
            "attempt_count": int(attempt_count),
            "retry_count": max(0, int(attempt_count) - 1),
            "retry_delays_seconds": [round(float(value), 6) for value in retry_delays],
            "retry_delay_total_seconds": round(sum(retry_delays), 6),
            "recovered_after_retry": (event_type == "API_RETRY_RECOVERED"),
            "status_code": status_code,
            "tracking_id": tracking_id,
        }
        if error:
            meta["error"] = error
        if error_class:
            meta["error_class"] = str(error_class)
        if transient is not None:
            meta["transient"] = bool(transient)
        if response_headers is not None:
            meta.update(
                {
                    "rate_limit": response_headers.get("x-ratelimit-limit"),
                    "rate_remaining": response_headers.get("x-ratelimit-remaining"),
                    "rate_reset": response_headers.get("x-ratelimit-reset"),
                }
            )
        self._last_response_meta = meta
        self._emit_telemetry(meta)
        return meta

    def _post(
        self,
        service: str,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        retry_safe: bool = True,
        allow_redirects: bool = True,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        url = (
            f"{self.BASE_URL}/tinkoff.public.invest.api.contract.v1.{service}/{method}"
        )
        attempts = self.max_retries + 1 if retry_safe else 1
        last_error: TBankAPIError | None = None
        request_started_at = datetime.now(timezone.utc)
        request_started_perf = time.perf_counter()
        retry_delays: list[float] = []

        for attempt in range(attempts):
            try:
                request_options: dict[str, Any] = {
                    "json": payload or {},
                    "timeout": (
                        min(self.connect_timeout_seconds, timeout_seconds)
                        if timeout_seconds is not None
                        else self.connect_timeout_seconds,
                        timeout_seconds
                        if timeout_seconds is not None
                        else self.read_timeout_seconds,
                    ),
                }
                if not allow_redirects:
                    request_options["allow_redirects"] = False
                response = self._session.post(url, **request_options)
            except requests.exceptions.SSLError as exc:
                self._complete_request_meta(
                    event_type="API_REQUEST_FAILED",
                    service=service,
                    method=method,
                    started_at=request_started_at,
                    started_perf=request_started_perf,
                    attempt_count=attempt + 1,
                    retry_delays=retry_delays,
                    status_code=None,
                    tracking_id=None,
                    error=str(exc),
                    transient=False,
                    error_class=type(exc).__name__,
                )
                raise TBankAPIError(
                    "Не удалось проверить TLS-сертификат T-Invest Sandbox. "
                    "Программа использует официальный адрес "
                    "sandbox-invest-public-api.tbank.ru и не отключает защиту TLS. "
                    "Установите сертификаты НУЦ Минцифры РФ в хранилище "
                    "сертификатов Windows, затем полностью перезапустите программу. "
                    "Если HTTPS проверяет корпоративный прокси или антивирус, "
                    "добавьте его доверенный корневой сертификат в Windows либо "
                    "укажите PEM-файл в поле CA bundle / переменной TBANK_CA_BUNDLE. "
                    f"Техническая причина: {exc}",
                    details=str(exc),
                    transient=False,
                    service=service,
                    method=method,
                    error_class=type(exc).__name__,
                ) from exc
            except (requests.RequestException, IncompleteRead, ProtocolError) as exc:
                transient = is_transient_transport_exception(exc)
                last_error = TBankAPIError(
                    f"T-Invest request failed: {exc}",
                    details=str(exc),
                    transient=transient,
                    service=service,
                    method=method,
                    error_class=type(exc).__name__,
                )
                if transient and attempt + 1 < attempts:
                    delay = self._sleep_before_retry(
                        attempt,
                        None,
                        service=service,
                        method=method,
                        reason=type(exc).__name__,
                        max_attempts=attempts,
                    )
                    retry_delays.append(delay)
                    continue
                self._complete_request_meta(
                    event_type="API_REQUEST_FAILED",
                    service=service,
                    method=method,
                    started_at=request_started_at,
                    started_perf=request_started_perf,
                    attempt_count=attempt + 1,
                    retry_delays=retry_delays,
                    status_code=None,
                    tracking_id=None,
                    error=str(exc),
                    transient=transient,
                    error_class=type(exc).__name__,
                )
                raise last_error from exc

            tracking_id = response.headers.get("x-tracking-id")

            if response.ok:
                try:
                    parsed = response.json()
                except ValueError as exc:
                    self._complete_request_meta(
                        event_type="API_REQUEST_FAILED",
                        service=service,
                        method=method,
                        started_at=request_started_at,
                        started_perf=request_started_perf,
                        attempt_count=attempt + 1,
                        retry_delays=retry_delays,
                        status_code=response.status_code,
                        tracking_id=tracking_id,
                        error="T-Invest returned invalid JSON.",
                        transient=False,
                        response_headers=dict(response.headers),
                        error_class=type(exc).__name__,
                    )
                    raise TBankAPIError(
                        "T-Invest returned invalid JSON.",
                        status_code=response.status_code,
                        transient=False,
                        service=service,
                        method=method,
                        tracking_id=tracking_id,
                        error_class=type(exc).__name__,
                    ) from exc
                event_type = (
                    "API_RETRY_RECOVERED" if attempt > 0 else "API_REQUEST_SUCCEEDED"
                )
                self._complete_request_meta(
                    event_type=event_type,
                    service=service,
                    method=method,
                    started_at=request_started_at,
                    started_perf=request_started_perf,
                    attempt_count=attempt + 1,
                    retry_delays=retry_delays,
                    status_code=response.status_code,
                    tracking_id=tracking_id,
                    response_headers=dict(response.headers),
                )
                return parsed

            try:
                details: Any = response.json()
            except ValueError:
                details = response.text
            if isinstance(details, dict) and str(details.get("description")) == "30220":
                message = (
                    "T-Invest rejected the candle request: limit cannot be "
                    "combined with candleSourceType (error 30220). Update the "
                    "client or use date-range mode without limit."
                )
            else:
                message = (
                    f"T-Invest API returned HTTP {response.status_code}: {details}"
                )
            retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
            transient = response.status_code in self.TRANSIENT_STATUS_CODES
            last_error = TBankAPIError(
                message,
                status_code=response.status_code,
                details=details,
                transient=transient,
                service=service,
                method=method,
                tracking_id=tracking_id,
                retry_after_seconds=retry_after,
                direct_response=True,
                redirect_followed=bool(getattr(response, "history", ())),
            )
            if transient and attempt + 1 < attempts:
                delay = self._sleep_before_retry(
                    attempt,
                    response.headers.get("Retry-After"),
                    service=service,
                    method=method,
                    reason=f"HTTP {response.status_code}",
                    max_attempts=attempts,
                )
                retry_delays.append(delay)
                continue
            self._complete_request_meta(
                event_type="API_REQUEST_FAILED",
                service=service,
                method=method,
                started_at=request_started_at,
                started_perf=request_started_perf,
                attempt_count=attempt + 1,
                retry_delays=retry_delays,
                status_code=response.status_code,
                tracking_id=tracking_id,
                error=f"HTTP {response.status_code}",
                transient=transient,
                response_headers=dict(response.headers),
            )
            raise last_error

        raise last_error or TBankAPIError(
            "Unknown T-Invest request failure.",
            service=service,
            method=method,
        )

    @staticmethod
    def _parse_retry_after(retry_after: str | None) -> float | None:
        if not retry_after:
            return None
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            return None

    def _sleep_before_retry(
        self,
        attempt: int,
        retry_after: str | None,
        *,
        service: str,
        method: str,
        reason: str,
        max_attempts: int,
    ) -> float:
        delay = self.retry_backoff_seconds * (2**attempt)
        parsed_retry_after = self._parse_retry_after(retry_after)
        if parsed_retry_after is not None:
            delay = max(delay, parsed_retry_after)
        if self.retry_jitter_seconds:
            delay += random.uniform(0.0, self.retry_jitter_seconds)
        delay = min(delay, 15.0)
        logger.warning(
            "T-Invest retry %s/%s for %s/%s in %.2f s (%s)",
            attempt + 1,
            max(1, max_attempts - 1),
            service,
            method,
            delay,
            reason,
        )
        self._emit_telemetry(
            {
                "event_type": "API_RETRY_SCHEDULED",
                "service": service,
                "method": method,
                "attempt": attempt + 1,
                "max_attempts": max_attempts,
                "delay_seconds": round(delay, 6),
                "reason": reason,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        time.sleep(delay)
        return delay

    def get_accounts(self) -> list[dict[str, Any]]:
        response = self._post(
            "SandboxService",
            "GetSandboxAccounts",
            {"status": "ACCOUNT_STATUS_OPEN"},
        )
        return list(response.get("accounts", []))

    def open_account(self, name: str = "MOEX research robot") -> str:
        # Account creation has no client idempotency key; do not auto-retry.
        response = self._post(
            "SandboxService",
            "OpenSandboxAccount",
            {"name": name},
            retry_safe=False,
        )
        account_id = response.get("accountId")
        if not account_id:
            raise TBankAPIError(f"No accountId in response: {response}")
        return str(account_id)

    def pay_in(self, account_id: str, rubles: float) -> dict[str, Any]:
        if rubles <= 0:
            raise ValueError("Sandbox payment must be positive.")
        amount = decimal_to_quotation(rubles)
        # Pay-in has no client idempotency key; do not auto-retry.
        return self._post(
            "SandboxService",
            "SandboxPayIn",
            {
                "accountId": account_id,
                "amount": {
                    "currency": "rub",
                    "units": amount["units"],
                    "nano": amount["nano"],
                },
            },
            retry_safe=False,
        )

    def get_portfolio(self, account_id: str) -> dict[str, Any]:
        return self._post(
            "SandboxService",
            "GetSandboxPortfolio",
            {"accountId": account_id, "currency": "RUB"},
        )

    def get_positions(self, account_id: str) -> dict[str, Any]:
        return self._post(
            "SandboxService",
            "GetSandboxPositions",
            {"accountId": account_id},
        )

    def get_orders(self, account_id: str) -> list[dict[str, Any]]:
        response = self._post(
            "SandboxService",
            "GetSandboxOrders",
            {"accountId": account_id},
        )
        return list(response.get("orders", []))

    def find_instrument(
        self,
        query: str,
        class_code: str = "TQBR",
    ) -> dict[str, Any]:
        query_upper = query.strip().upper()
        class_upper = class_code.strip().upper()
        cache_key = (query_upper, class_upper)
        cached = self._INSTRUMENT_CACHE.get(cache_key)
        if cached is not None:
            logger.info(
                "Using cached instrument metadata for %s_%s.",
                query_upper,
                class_upper,
            )
            return dict(cached)

        response = self._post(
            "InstrumentsService",
            "FindInstrument",
            {
                "query": query,
                "apiTradeAvailableFlag": True,
            },
        )
        instruments = list(response.get("instruments", []))
        exact = [
            item
            for item in instruments
            if str(item.get("ticker", "")).upper() == query_upper
            and str(item.get("classCode", "")).upper() == class_upper
            and bool(item.get("apiTradeAvailableFlag", False))
        ]
        if len(exact) == 1:
            selected = dict(exact[0])
            self._INSTRUMENT_CACHE[cache_key] = selected
            return dict(selected)
        if len(exact) > 1:
            exact.sort(
                key=lambda item: (
                    str(item.get("instrumentKind", "")) != "INSTRUMENT_TYPE_SHARE",
                    str(item.get("currency", "")).lower() != "rub",
                )
            )
            selected = dict(exact[0])
            self._INSTRUMENT_CACHE[cache_key] = selected
            return dict(selected)
        available = [
            f"{item.get('ticker')}_{item.get('classCode')}" for item in instruments[:10]
        ]
        raise TBankAPIError(
            f"Exact instrument {query_upper}_{class_upper} not found. "
            f"Candidates: {available}"
        )

    def get_instrument_by_id(
        self,
        identifier: str,
        *,
        id_type: str = "INSTRUMENT_ID_TYPE_UID",
        class_code: str = "",
    ) -> dict[str, Any]:
        """Resolve immutable instrument metadata by UID, position UID or FIGI.

        Portfolio responses commonly omit ticker/class code. The method uses
        the official InstrumentsService/GetInstrumentBy endpoint and caches
        immutable metadata for the lifetime of the process.
        """
        identifier = str(identifier).strip()
        id_type = str(id_type).strip().upper()
        class_code = str(class_code).strip().upper()
        if not identifier:
            raise ValueError("Instrument identifier must not be empty.")
        allowed = {
            "INSTRUMENT_ID_TYPE_FIGI",
            "INSTRUMENT_ID_TYPE_TICKER",
            "INSTRUMENT_ID_TYPE_UID",
            "INSTRUMENT_ID_TYPE_POSITION_UID",
            "INSTRUMENT_ID_TYPE_ID",
        }
        if id_type not in allowed:
            raise ValueError(f"Unsupported instrument id_type: {id_type}.")
        if id_type == "INSTRUMENT_ID_TYPE_TICKER" and not class_code:
            raise ValueError("class_code is required for ticker lookup.")

        cache_key = (id_type, f"{identifier}|{class_code}")
        cached = self._INSTRUMENT_ID_CACHE.get(cache_key)
        if cached is not None:
            return dict(cached)

        payload: dict[str, Any] = {"idType": id_type, "id": identifier}
        if class_code:
            payload["classCode"] = class_code
        response = self._post(
            "InstrumentsService",
            "GetInstrumentBy",
            payload,
        )
        instrument = response.get("instrument")
        if not isinstance(instrument, dict) or not instrument:
            raise TBankAPIError(
                f"GetInstrumentBy returned no instrument for {identifier}."
            )
        selected = dict(instrument)
        self._INSTRUMENT_ID_CACHE[cache_key] = selected
        ticker = str(selected.get("ticker") or "").strip().upper()
        selected_class = str(selected.get("classCode") or "").strip().upper()
        if ticker and selected_class:
            self._INSTRUMENT_CACHE[(ticker, selected_class)] = selected
        return dict(selected)

    def get_candles(
        self,
        instrument_id: str,
        date_from: datetime,
        date_to: datetime,
        interval: str = "CANDLE_INTERVAL_HOUR",
        limit: int | None = None,
        candle_source_type: str | None = "CANDLE_SOURCE_EXCHANGE",
    ) -> pd.DataFrame:
        """Return historical candles without sending an invalid API payload.

        T-Invest error 30220 explicitly forbids combining ``limit`` with
        ``candleSourceType``.  The robot uses the date-range mode, preserves
        exchange-only candles, and splits long ranges according to the
        interval-specific API limits.  ``limit`` remains available for callers
        that intentionally want the latest N candles; in that mode the source
        field is omitted automatically.
        """
        if date_from.tzinfo is None:
            date_from = date_from.replace(tzinfo=timezone.utc)
        if date_to.tzinfo is None:
            date_to = date_to.replace(tzinfo=timezone.utc)
        date_from = date_from.astimezone(timezone.utc)
        date_to = date_to.astimezone(timezone.utc)
        interval = interval.strip().upper()

        if date_from >= date_to:
            raise ValueError("date_from must be earlier than date_to.")
        if limit is not None:
            limit = int(limit)
            if limit <= 0:
                raise ValueError("limit must be positive when specified.")
            max_limit = self.CANDLE_MAX_LIMITS.get(interval)
            if max_limit is not None and limit > max_limit:
                raise ValueError(
                    f"limit {limit} exceeds the T-Invest maximum "
                    f"{max_limit} for {interval}."
                )

        records: list[dict[str, Any]] = []
        if limit is not None:
            # API contract: candleSourceType and limit are mutually exclusive.
            effective_limit = int(limit)
            maximum_limit = self.CANDLE_MAX_LIMITS.get(interval)
            if maximum_limit is not None and effective_limit > maximum_limit:
                logger.warning(
                    "Requested candle limit %s exceeds the documented maximum "
                    "%s for %s; using %s.",
                    effective_limit,
                    maximum_limit,
                    interval,
                    maximum_limit,
                )
                effective_limit = maximum_limit
            payload = {
                "from": date_from.isoformat(),
                "to": date_to.isoformat(),
                "interval": interval,
                "instrumentId": instrument_id,
                "limit": effective_limit,
            }
            response = self._post(
                "MarketDataService",
                "GetCandles",
                payload,
            )
            self._extend_candle_records(records, response)
        else:
            for chunk_from, chunk_to in self._candle_chunks(
                date_from,
                date_to,
                interval,
            ):
                payload: dict[str, Any] = {
                    "from": chunk_from.isoformat(),
                    "to": chunk_to.isoformat(),
                    "interval": interval,
                    "instrumentId": instrument_id,
                }
                if candle_source_type:
                    payload["candleSourceType"] = candle_source_type
                response = self._post(
                    "MarketDataService",
                    "GetCandles",
                    payload,
                )
                self._extend_candle_records(records, response)

        if not records:
            raise TBankAPIError("No candles returned by T-Invest.")
        frame = (
            pd.DataFrame.from_records(records)
            .drop_duplicates(subset=["begin"], keep="last")
            .set_index("begin")
            .sort_index()
        )
        return frame

    @classmethod
    def _candle_chunks(
        cls,
        date_from: datetime,
        date_to: datetime,
        interval: str,
    ) -> list[tuple[datetime, datetime]]:
        max_span = cls.CANDLE_MAX_SPAN.get(interval)
        if max_span is None or date_to - date_from <= max_span:
            return [(date_from, date_to)]

        chunks: list[tuple[datetime, datetime]] = []
        cursor = date_from
        # A one-second margin prevents borderline "maximum period exceeded"
        # responses caused by inclusive endpoint semantics.
        safe_span = max_span - timedelta(seconds=1)
        while cursor < date_to:
            chunk_to = min(cursor + safe_span, date_to)
            chunks.append((cursor, chunk_to))
            if chunk_to >= date_to:
                break
            cursor = chunk_to
        return chunks

    @staticmethod
    def _extend_candle_records(
        records: list[dict[str, Any]],
        response: dict[str, Any],
    ) -> None:
        for candle in response.get("candles", []):
            timestamp = pd.to_datetime(candle.get("time"), utc=True)
            if pd.isna(timestamp):
                continue
            records.append(
                {
                    "begin": timestamp,
                    "open": quotation_to_float(candle.get("open")),
                    "high": quotation_to_float(candle.get("high")),
                    "low": quotation_to_float(candle.get("low")),
                    "close": quotation_to_float(candle.get("close")),
                    "volume": int(candle.get("volume", 0)),
                    "is_complete": bool(candle.get("isComplete", True)),
                }
            )

    def get_trading_status(self, instrument_id: str) -> dict[str, Any]:
        return self._post(
            "MarketDataService",
            "GetTradingStatus",
            {"instrumentId": instrument_id},
        )

    def get_last_prices(self, instrument_ids: list[str]) -> list[dict[str, Any]]:
        normalized = tuple(
            dict.fromkeys(str(item or "").strip() for item in instrument_ids)
        )
        if not normalized or any(not item for item in normalized):
            raise ValueError("instrument_ids must contain non-empty identifiers.")
        response = self._post(
            "MarketDataService",
            "GetLastPrices",
            {
                "instrumentId": list(normalized),
                "lastPriceType": "LAST_PRICE_EXCHANGE",
            },
        )
        return list(response.get("lastPrices", []))

    def get_max_lots(
        self,
        account_id: str,
        instrument_id: str,
        price: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "accountId": account_id,
            "instrumentId": instrument_id,
        }
        if price is not None:
            payload["price"] = decimal_to_quotation(price)
        return self._post(
            "SandboxService",
            "GetSandboxMaxLots",
            payload,
        )

    def get_order_state(
        self,
        account_id: str,
        order_id: str,
        *,
        by_request_id: bool = True,
    ) -> dict[str, Any]:
        return self._post(
            "SandboxService",
            "GetSandboxOrderState",
            {
                "accountId": account_id,
                "orderId": order_id,
                "orderIdType": (
                    "ORDER_ID_TYPE_REQUEST"
                    if by_request_id
                    else "ORDER_ID_TYPE_EXCHANGE"
                ),
                "priceType": "PRICE_TYPE_CURRENCY",
            },
        )

    def get_operations_by_cursor_once(
        self,
        payload: dict[str, Any],
        timeout_ns: int,
    ) -> dict[str, Any]:
        """Perform one non-replaying CL3 Sandbox cursor-read attempt."""

        if (
            not isinstance(payload, dict)
            or type(timeout_ns) is not int
            or timeout_ns <= 0
        ):
            raise ValueError("payload and timeout_ns are invalid.")
        boundary = _safe_cursor_request_boundary(payload)
        try:
            response = self._post(
                "SandboxService",
                "GetSandboxOperationsByCursor",
                dict(payload),
                retry_safe=False,
                allow_redirects=False,
                timeout_seconds=timeout_ns / 1_000_000_000,
            )
        except TBankAPIError as exc:
            meta = dict(self._last_response_meta)
            if (
                type(meta.get("service")) is str
                and meta.get("service") == "SandboxService"
                and type(meta.get("method")) is str
                and meta.get("method") == "GetSandboxOperationsByCursor"
            ):
                meta.update(boundary)
                meta.update(_safe_provider_error_identity(exc.status_code, exc.details))
                self._last_response_meta = meta
            from .broker_read_adapters import (
                BrokerTransportFailure,
                BrokerTransportFailureKind,
            )

            if exc.status_code is not None:
                raise BrokerTransportFailure(
                    BrokerTransportFailureKind.HTTP_STATUS,
                    http_status=exc.status_code,
                ) from None
            kind = (
                BrokerTransportFailureKind.TIMEOUT
                if exc.error_class and "TIMEOUT" in exc.error_class.upper()
                else BrokerTransportFailureKind.CONNECTION_INTERRUPTED
            )
            raise BrokerTransportFailure(kind) from None
        meta = dict(self._last_response_meta)
        if (
            type(meta.get("service")) is str
            and meta.get("service") == "SandboxService"
            and type(meta.get("method")) is str
            and meta.get("method") == "GetSandboxOperationsByCursor"
        ):
            meta.update(boundary)
            self._last_response_meta = meta
        return response

    def post_order(
        self,
        account_id: str,
        instrument_id: str,
        lots: int,
        direction: str,
        *,
        order_id: str,
        order_type: str = "BESTPRICE",
        time_in_force: str = "FILL_AND_KILL",
    ) -> dict[str, Any]:
        """Place an idempotent order in Sandbox only."""
        if lots <= 0:
            raise ValueError("lots must be positive.")
        direction = direction.upper()
        if direction not in {"BUY", "SELL"}:
            raise ValueError("direction must be BUY or SELL.")
        order_type = order_type.upper()
        if order_type not in {"MARKET", "BESTPRICE"}:
            raise ValueError("order_type must be MARKET or BESTPRICE.")
        time_in_force = time_in_force.upper()
        if time_in_force not in {"DAY", "FILL_AND_KILL", "FILL_OR_KILL"}:
            raise ValueError("Unsupported time_in_force.")
        if not order_id or len(order_id) > 36:
            raise ValueError("order_id must be a UID no longer than 36 characters.")

        # Retrying is safe because the same caller-provided orderId is reused.
        return self._post(
            "SandboxService",
            "PostSandboxOrder",
            {
                "quantity": str(lots),
                "direction": f"ORDER_DIRECTION_{direction}",
                "accountId": account_id,
                "orderType": f"ORDER_TYPE_{order_type}",
                "orderId": order_id,
                "instrumentId": instrument_id,
                "timeInForce": f"TIME_IN_FORCE_{time_in_force}",
                "priceType": "PRICE_TYPE_CURRENCY",
                "confirmMarginTrade": False,
            },
            retry_safe=True,
        )

    def post_order_once(
        self,
        account_id: str,
        instrument_id: str,
        lots: int,
        direction: str,
        *,
        order_id: str,
        order_type: str = "BESTPRICE",
        time_in_force: str = "FILL_AND_KILL",
    ) -> dict[str, Any]:
        """Place exactly one CL7 physical order request with no redirect replay."""

        if lots <= 0:
            raise ValueError("lots must be positive.")
        direction = direction.upper()
        order_type = order_type.upper()
        time_in_force = time_in_force.upper()
        if direction not in {"BUY", "SELL"}:
            raise ValueError("direction must be BUY or SELL.")
        if order_type not in {"MARKET", "BESTPRICE"}:
            raise ValueError("order_type must be MARKET or BESTPRICE.")
        if time_in_force not in {"DAY", "FILL_AND_KILL", "FILL_OR_KILL"}:
            raise ValueError("Unsupported time_in_force.")
        if not order_id or len(order_id) > 36:
            raise ValueError("order_id must be a UID no longer than 36 characters.")
        return self._post(
            "SandboxService",
            "PostSandboxOrder",
            {
                "quantity": str(lots),
                "direction": f"ORDER_DIRECTION_{direction}",
                "accountId": account_id,
                "orderType": f"ORDER_TYPE_{order_type}",
                "orderId": order_id,
                "instrumentId": instrument_id,
                "timeInForce": f"TIME_IN_FORCE_{time_in_force}",
                "priceType": "PRICE_TYPE_CURRENCY",
                "confirmMarginTrade": False,
            },
            retry_safe=False,
            allow_redirects=False,
        )

    def post_market_order(
        self,
        account_id: str,
        instrument_id: str,
        lots: int,
        direction: str,
        order_id: str,
    ) -> dict[str, Any]:
        """Backward-compatible Sandbox market-order wrapper."""
        return self.post_order(
            account_id,
            instrument_id,
            lots,
            direction,
            order_id=order_id,
            order_type="MARKET",
            time_in_force="FILL_AND_KILL",
        )

    @staticmethod
    def instrument_id(instrument: dict[str, Any]) -> str:
        for key in ("uid", "instrumentUid", "figi"):
            value = instrument.get(key)
            if value:
                return str(value)
        ticker = instrument.get("ticker")
        class_code = instrument.get("classCode")
        if ticker and class_code:
            return f"{ticker}_{class_code}"
        raise TBankAPIError(f"Instrument has no usable identifier: {instrument}")

    @staticmethod
    def position_lots(
        portfolio: dict[str, Any],
        instrument: dict[str, Any],
    ) -> int:
        ids = {
            str(instrument.get(key))
            for key in ("uid", "instrumentUid", "figi", "ticker")
            if instrument.get(key)
        }
        for position in portfolio.get("positions", []):
            position_ids = {
                str(position.get(key))
                for key in (
                    "instrumentUid",
                    "figi",
                    "ticker",
                    "instrumentId",
                )
                if position.get(key)
            }
            if ids.intersection(position_ids):
                quantity_lots = position.get("quantityLots")
                if quantity_lots is None:
                    raise TBankAPIError(
                        "Portfolio position has no quantityLots; refusing to "
                        "interpret quantity in pieces as lots."
                    )
                value = quotation_to_float(quantity_lots)
                if value < 0:
                    raise TBankAPIError(
                        "A short position was detected in a long-only robot."
                    )
                return int(value)
        return 0

    @staticmethod
    def max_buy_lots(response: dict[str, Any]) -> int | None:
        buy = response.get("buyLimits") or response.get("buy_limits") or {}
        for key in ("buyMaxMarketLots", "buyMaxLots", "buy_max_market_lots"):
            if key in buy:
                try:
                    return max(0, int(buy[key]))
                except (TypeError, ValueError):
                    pass
        return None

    @staticmethod
    def market_order_available(status: dict[str, Any]) -> bool:
        api_available = bool(status.get("apiTradeAvailableFlag", True))
        market_available = bool(status.get("marketOrderAvailableFlag", True))
        return api_available and market_available

    @staticmethod
    def best_price_available(status: dict[str, Any]) -> bool:
        # The public response may not expose a separate best-price flag. A
        # limit-order availability check is the conservative proxy.
        api_available = bool(status.get("apiTradeAvailableFlag", True))
        limit_available = bool(status.get("limitOrderAvailableFlag", True))
        return api_available and limit_available
