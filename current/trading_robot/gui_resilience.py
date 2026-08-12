from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .tbank_sandbox import TBankAPIError


@dataclass(frozen=True, slots=True)
class BackgroundErrorInfo:
    """Safe, presentation-oriented metadata for a background task failure."""

    transient: bool
    service: str | None
    method: str | None
    status_code: int | None
    request_id: str | None
    retry_after_seconds: float | None
    summary: str
    dedup_key: str


def _request_id_from_details(details: Any) -> str | None:
    if not isinstance(details, dict):
        return None
    for key in ("requestId", "request_id", "trackingId", "tracking_id"):
        value = details.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def describe_background_error(exc: BaseException) -> BackgroundErrorInfo:
    """Classify an exception without exposing raw response bodies or secrets."""

    if isinstance(exc, TBankAPIError):
        status_code = exc.status_code
        transient = bool(exc.transient or status_code is None or status_code == 429)
        if status_code is not None and 500 <= status_code <= 599:
            transient = True
        service = exc.service
        method = exc.method
        request_id = _request_id_from_details(exc.details) or exc.tracking_id

        endpoint = "/".join(part for part in (service, method) if part)
        if status_code is None:
            reason = "ошибка соединения"
        else:
            reason = f"HTTP {status_code}"
        summary = "T-Invest Sandbox временно недоступен" if transient else "T-Invest отклонил запрос"
        if endpoint:
            summary += f" ({reason}, {endpoint})"
        else:
            summary += f" ({reason})"
        dedup_key = f"tbank:{status_code}:{service or '-'}:{method or '-'}"
        return BackgroundErrorInfo(
            transient=transient,
            service=service,
            method=method,
            status_code=status_code,
            request_id=request_id,
            retry_after_seconds=exc.retry_after_seconds,
            summary=summary,
            dedup_key=dedup_key,
        )

    name = type(exc).__name__
    return BackgroundErrorInfo(
        transient=False,
        service=None,
        method=None,
        status_code=None,
        request_id=None,
        retry_after_seconds=None,
        summary=f"Фоновая операция завершилась ошибкой: {name}",
        dedup_key=f"local:{name}",
    )


@dataclass(slots=True)
class TransientBackoff:
    """Small deterministic UI backoff independent from the trading circuit breaker."""

    base_seconds: int = 60
    max_seconds: int = 600
    failures: int = 0

    def record_failure(self, retry_after_seconds: float | None = None) -> int:
        self.failures += 1
        exponential = self.base_seconds * (2 ** max(0, self.failures - 1))
        delay = min(self.max_seconds, exponential)
        if retry_after_seconds is not None:
            delay = max(delay, int(max(0.0, retry_after_seconds)))
        return max(1, delay)

    def reset(self) -> None:
        self.failures = 0
