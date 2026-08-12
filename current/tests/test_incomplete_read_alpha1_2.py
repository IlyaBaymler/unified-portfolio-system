from __future__ import annotations

from http.client import IncompleteRead

import pytest
import requests
from urllib3.exceptions import ProtocolError

from trading_robot.tbank_sandbox import (
    TBankAPIError,
    TBankSandboxClient,
    is_transient_transport_exception,
)


class _OkResponse:
    ok = True
    status_code = 200
    headers = {"x-tracking-id": "alpha1-2-recovered"}

    @staticmethod
    def json():
        return {"accounts": []}


class _UnauthorizedResponse:
    ok = False
    status_code = 401
    headers = {"x-tracking-id": "auth-failed"}
    text = "unauthorized"

    @staticmethod
    def json():
        return {"error": "unauthorized"}


def _client(*, retries: int = 1, events=None) -> TBankSandboxClient:
    return TBankSandboxClient(
        "dummy-token",
        max_retries=retries,
        retry_backoff_seconds=0,
        retry_jitter_seconds=0,
        telemetry_callback=None if events is None else events.append,
    )


def test_direct_incomplete_read_is_transient_and_recovers(monkeypatch):
    events: list[dict] = []
    calls = 0
    client = _client(events=events)

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise IncompleteRead(b"abc", 7)
        return _OkResponse()

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr("trading_robot.tbank_sandbox.time.sleep", lambda _x: None)
    try:
        assert client.get_accounts() == []
    finally:
        client.close()

    assert calls == 2
    assert [event["event_type"] for event in events] == [
        "API_RETRY_SCHEDULED",
        "API_RETRY_RECOVERED",
    ]
    assert client.last_response_meta["attempt_count"] == 2
    assert client.last_response_meta["retry_count"] == 1


def test_protocol_error_with_nested_incomplete_read_is_transient():
    exc = ProtocolError("Connection broken", IncompleteRead(b"abc", 7))
    assert is_transient_transport_exception(exc) is True


def test_chunked_encoding_error_is_transient():
    exc = requests.exceptions.ChunkedEncodingError(
        ProtocolError("Connection broken", IncompleteRead(b"abc", 7))
    )
    assert is_transient_transport_exception(exc) is True


def test_incomplete_read_retries_exhausted_is_degraded_capable(monkeypatch):
    events: list[dict] = []
    calls = 0
    client = _client(retries=2, events=events)

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise requests.exceptions.ChunkedEncodingError(
            ProtocolError("Connection broken", IncompleteRead(b"abc", 7))
        )

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr("trading_robot.tbank_sandbox.time.sleep", lambda _x: None)
    try:
        with pytest.raises(TBankAPIError) as captured:
            client.get_accounts()
    finally:
        client.close()

    assert calls == 3
    assert captured.value.transient is True
    assert captured.value.error_class == "ChunkedEncodingError"
    assert client.last_response_meta["event_type"] == "API_REQUEST_FAILED"
    assert client.last_response_meta["transient"] is True
    assert client.last_response_meta["attempt_count"] == 3
    assert client.last_response_meta["retry_count"] == 2
    assert client.last_response_meta["error_class"] == "ChunkedEncodingError"
    assert [event["event_type"] for event in events] == [
        "API_RETRY_SCHEDULED",
        "API_RETRY_SCHEDULED",
        "API_REQUEST_FAILED",
    ]


def test_unsafe_post_is_not_replayed_after_incomplete_read(monkeypatch):
    calls = 0
    client = _client(retries=3)

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise IncompleteRead(b"accepted-but-truncated", 10)

    monkeypatch.setattr(client._session, "post", fake_post)
    try:
        with pytest.raises(TBankAPIError) as captured:
            client._post(
                "SandboxService",
                "PostSandboxOrder",
                {"orderId": "idempotent-request-id"},
                retry_safe=False,
            )
    finally:
        client.close()

    assert calls == 1
    assert captured.value.transient is True
    assert client.last_response_meta["attempt_count"] == 1
    assert client.last_response_meta["retry_count"] == 0


def test_non_transient_401_remains_non_transient(monkeypatch):
    client = _client(retries=3)
    calls = 0

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _UnauthorizedResponse()

    monkeypatch.setattr(client._session, "post", fake_post)
    try:
        with pytest.raises(TBankAPIError) as captured:
            client.get_accounts()
    finally:
        client.close()

    assert calls == 1
    assert captured.value.status_code == 401
    assert captured.value.transient is False
    assert client.last_response_meta["transient"] is False


def test_generic_protocol_error_without_truncated_read_is_not_transient():
    assert is_transient_transport_exception(
        ProtocolError("invalid response framing")
    ) is False
