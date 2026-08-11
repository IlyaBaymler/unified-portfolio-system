from pathlib import Path

import pandas as pd
import pytest
import requests

from trading_robot.tbank_sandbox import TBankAPIError, TBankSandboxClient


def test_position_lots_uses_quantity_lots_not_piece_quantity():
    instrument = {"uid": "u1", "ticker": "SBER"}
    portfolio = {
        "positions": [
            {
                "instrumentUid": "u1",
                "quantityLots": {"units": "3", "nano": 0},
                "quantity": {"units": "30", "nano": 0},
            }
        ]
    }
    assert TBankSandboxClient.position_lots(portfolio, instrument) == 3


def test_position_lots_refuses_ambiguous_piece_quantity():
    instrument = {"uid": "u1", "ticker": "SBER"}
    portfolio = {
        "positions": [
            {
                "instrumentUid": "u1",
                "quantity": {"units": "30", "nano": 0},
            }
        ]
    }
    with pytest.raises(TBankAPIError):
        TBankSandboxClient.position_lots(portfolio, instrument)


def test_client_initializes_private_session_with_slots():
    client = TBankSandboxClient("dummy-token", max_retries=0)
    try:
        assert client._session is not None
        assert client._session.headers["Authorization"] == "Bearer dummy-token"
    finally:
        client.close()


def test_client_uses_official_sandbox_host():
    assert TBankSandboxClient.BASE_URL == (
        "https://sandbox-invest-public-api.tbank.ru/rest"
    )


def test_client_accepts_explicit_ca_bundle(tmp_path: Path):
    bundle = tmp_path / "root.pem"
    bundle.write_text("dummy", encoding="utf-8")
    client = TBankSandboxClient(
        "dummy-token", max_retries=0, ca_bundle_path=str(bundle)
    )
    try:
        assert client._session.verify == str(bundle.resolve())
    finally:
        client.close()


def test_ssl_error_is_actionable_and_not_retried(monkeypatch):
    client = TBankSandboxClient("dummy-token", max_retries=3)
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise requests.exceptions.SSLError("certificate verify failed")

    monkeypatch.setattr(client._session, "post", fail)
    try:
        with pytest.raises(TBankAPIError, match="НУЦ Минцифры"):
            client.get_accounts()
        assert calls == 1
    finally:
        client.close()


def _sample_candle_response():
    quotation = {"units": "100", "nano": 0}
    return {
        "candles": [
            {
                "time": "2026-07-18T10:00:00Z",
                "open": quotation,
                "high": quotation,
                "low": quotation,
                "close": quotation,
                "volume": "1000",
                "isComplete": True,
            }
        ]
    }


def test_get_candles_never_combines_limit_and_candle_source(monkeypatch):
    captured = {}

    def fake_post(self, service, method, payload=None, *, retry_safe=True):
        captured["service"] = service
        captured["method"] = method
        captured["payload"] = dict(payload or {})
        return _sample_candle_response()

    monkeypatch.setattr(TBankSandboxClient, "_post", fake_post)
    client = TBankSandboxClient("dummy-token", max_retries=0)
    try:
        frame = client.get_candles(
            "instrument-uid",
            pd.Timestamp("2026-07-01", tz="UTC").to_pydatetime(),
            pd.Timestamp("2026-07-20", tz="UTC").to_pydatetime(),
            interval="CANDLE_INTERVAL_HOUR",
            limit=500,
        )
        assert len(frame) == 1
        assert captured["payload"]["limit"] == 500
        assert "candleSourceType" not in captured["payload"]
    finally:
        client.close()


def test_get_candles_uses_exchange_source_without_limit(monkeypatch):
    captured = {}

    def fake_post(self, service, method, payload=None, *, retry_safe=True):
        captured["payload"] = dict(payload or {})
        return _sample_candle_response()

    monkeypatch.setattr(TBankSandboxClient, "_post", fake_post)
    client = TBankSandboxClient("dummy-token", max_retries=0)
    try:
        client.get_candles(
            "instrument-uid",
            pd.Timestamp("2026-07-01", tz="UTC").to_pydatetime(),
            pd.Timestamp("2026-07-20", tz="UTC").to_pydatetime(),
            interval="CANDLE_INTERVAL_HOUR",
            limit=None,
        )
        assert "limit" not in captured["payload"]
        assert captured["payload"]["candleSourceType"] == (
            "CANDLE_SOURCE_EXCHANGE"
        )
    finally:
        client.close()


def test_get_candles_splits_long_ten_minute_range(monkeypatch):
    payloads = []

    def fake_post(self, service, method, payload=None, *, retry_safe=True):
        payloads.append(dict(payload or {}))
        return _sample_candle_response()

    monkeypatch.setattr(TBankSandboxClient, "_post", fake_post)
    client = TBankSandboxClient("dummy-token", max_retries=0)
    try:
        frame = client.get_candles(
            "instrument-uid",
            pd.Timestamp("2026-06-01", tz="UTC").to_pydatetime(),
            pd.Timestamp("2026-07-01", tz="UTC").to_pydatetime(),
            interval="CANDLE_INTERVAL_10_MIN",
            limit=None,
        )
        assert len(payloads) >= 5
        assert len(frame) == 1  # duplicate fake timestamps are de-duplicated
        assert all("limit" not in payload for payload in payloads)
        assert all(
            payload.get("candleSourceType") == "CANDLE_SOURCE_EXCHANGE"
            for payload in payloads
        )
    finally:
        client.close()


def test_find_instrument_uses_process_cache(monkeypatch):
    TBankSandboxClient._INSTRUMENT_CACHE.clear()
    calls = 0

    def fake_post(self, service, method, payload=None, *, retry_safe=True):
        nonlocal calls
        calls += 1
        return {
            "instruments": [
                {
                    "ticker": "SBER",
                    "classCode": "TQBR",
                    "uid": "uid-sber",
                    "apiTradeAvailableFlag": True,
                    "instrumentKind": "INSTRUMENT_TYPE_SHARE",
                    "currency": "rub",
                }
            ]
        }

    monkeypatch.setattr(TBankSandboxClient, "_post", fake_post)
    client = TBankSandboxClient("dummy-token", max_retries=0)
    try:
        first = client.find_instrument("SBER", "TQBR")
        second = client.find_instrument("sber", "tqbr")
        assert first["uid"] == "uid-sber"
        assert second["uid"] == "uid-sber"
        assert calls == 1
    finally:
        client.close()
        TBankSandboxClient._INSTRUMENT_CACHE.clear()


def test_client_uses_separate_connect_and_read_timeouts(monkeypatch):
    captured = {}

    class Response:
        ok = True
        status_code = 200
        headers = {"x-tracking-id": "tracking-123"}

        @staticmethod
        def json():
            return {"accounts": []}

    client = TBankSandboxClient(
        "dummy-token",
        max_retries=0,
        connect_timeout_seconds=3.0,
        read_timeout_seconds=11.0,
    )

    def fake_post(*args, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return Response()

    monkeypatch.setattr(client._session, "post", fake_post)
    try:
        assert client.get_accounts() == []
        assert captured["timeout"] == (3.0, 11.0)
        assert client.last_response_meta["tracking_id"] == "tracking-123"
    finally:
        client.close()


def test_connect_timeout_is_marked_transient(monkeypatch):
    client = TBankSandboxClient("dummy-token", max_retries=0)

    def fail(*args, **kwargs):
        raise requests.exceptions.ConnectTimeout("network unavailable")

    monkeypatch.setattr(client._session, "post", fail)
    try:
        with pytest.raises(TBankAPIError) as captured:
            client.get_accounts()
        assert captured.value.transient is True
        assert captured.value.service == "SandboxService"
        assert captured.value.method == "GetSandboxAccounts"
    finally:
        client.close()


def test_retry_telemetry_reports_recovery(monkeypatch):
    events = []
    calls = 0

    class Response:
        ok = True
        status_code = 200
        headers = {"x-tracking-id": "tracking-recovered"}

        @staticmethod
        def json():
            return {"accounts": []}

    client = TBankSandboxClient(
        "dummy-token",
        max_retries=1,
        retry_backoff_seconds=0,
        retry_jitter_seconds=0,
        telemetry_callback=events.append,
    )

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.exceptions.ConnectTimeout("temporary")
        return Response()

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr("trading_robot.tbank_sandbox.time.sleep", lambda _x: None)
    try:
        assert client.get_accounts() == []
        event_types = [event["event_type"] for event in events]
        assert "API_RETRY_SCHEDULED" in event_types
        assert "API_RETRY_RECOVERED" in event_types
        assert client.last_response_meta["attempt_count"] == 2
        assert client.last_response_meta["retry_count"] == 1
        assert client.last_response_meta["tracking_id"] == "tracking-recovered"
    finally:
        client.close()


def test_retry_telemetry_reports_recovery_and_precise_timing(monkeypatch):
    events = []
    calls = 0

    class Response:
        ok = True
        status_code = 200
        headers = {
            "x-tracking-id": "retry-tracking-id",
            "x-ratelimit-limit": "100",
            "x-ratelimit-remaining": "99",
        }

        @staticmethod
        def json():
            return {"accounts": []}

    client = TBankSandboxClient(
        "dummy-token",
        max_retries=1,
        retry_backoff_seconds=0.0,
        retry_jitter_seconds=0.0,
        telemetry_callback=events.append,
    )

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.exceptions.ConnectTimeout("temporary outage")
        return Response()

    monkeypatch.setattr(client._session, "post", fake_post)
    monkeypatch.setattr("trading_robot.tbank_sandbox.time.sleep", lambda _value: None)
    try:
        assert client.get_accounts() == []
    finally:
        client.close()

    assert calls == 2
    assert [event["event_type"] for event in events] == [
        "API_RETRY_SCHEDULED",
        "API_RETRY_RECOVERED",
    ]
    recovered = events[-1]
    assert recovered["attempt_count"] == 2
    assert recovered["retry_count"] == 1
    assert recovered["recovered_after_retry"] is True
    assert recovered["request_duration_seconds"] >= 0
    assert recovered["request_started_at"] <= recovered["request_completed_at"]
    assert recovered["tracking_id"] == "retry-tracking-id"
    assert client.last_response_meta["event_type"] == "API_RETRY_RECOVERED"

def test_get_instrument_by_id_uses_official_method_and_cache(monkeypatch):
    TBankSandboxClient._INSTRUMENT_ID_CACHE.clear()
    calls = []

    def fake_post(self, service, method, payload=None, *, retry_safe=True):
        calls.append((service, method, dict(payload or {})))
        return {
            "instrument": {
                "ticker": "SBER",
                "classCode": "TQBR",
                "uid": "uid-sber",
                "positionUid": "position-sber",
            }
        }

    monkeypatch.setattr(TBankSandboxClient, "_post", fake_post)
    client = TBankSandboxClient("dummy-token", max_retries=0)
    try:
        first = client.get_instrument_by_id(
            "position-sber",
            id_type="INSTRUMENT_ID_TYPE_POSITION_UID",
        )
        second = client.get_instrument_by_id(
            "position-sber",
            id_type="INSTRUMENT_ID_TYPE_POSITION_UID",
        )
        assert first["uid"] == "uid-sber"
        assert second["ticker"] == "SBER"
        assert calls == [
            (
                "InstrumentsService",
                "GetInstrumentBy",
                {
                    "idType": "INSTRUMENT_ID_TYPE_POSITION_UID",
                    "id": "position-sber",
                },
            )
        ]
    finally:
        client.close()
        TBankSandboxClient._INSTRUMENT_ID_CACHE.clear()

