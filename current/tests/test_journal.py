from pathlib import Path

from trading_robot.journal import EventJournal, JournalEvent
from trading_robot.runtime_integrity import inspect_sqlite_file


def test_event_journal_records_queries_and_exports(tmp_path: Path):
    journal = EventJournal(tmp_path / "events.db")
    first_id = journal.record(
        JournalEvent(
            category="cycle",
            event_type="processed",
            run_id="run-1",
            ticker="SBER",
            payload={"signal": 1},
        )
    )
    second_id = journal.record(
        JournalEvent(
            category="incident",
            event_type="TRANSIENT_API_ERROR",
            severity="WARNING",
            run_id="run-2",
            payload={"error": "timeout"},
        )
    )

    assert second_id > first_id
    assert journal.count() == 2
    assert journal.count(category="incident") == 1
    recent = journal.recent(limit=10)
    assert recent[0]["event_type"] == "TRANSIENT_API_ERROR"
    assert recent[0]["payload"]["error"] == "timeout"

    destination = journal.export_csv(tmp_path / "events.csv")
    text = destination.read_text(encoding="utf-8-sig")
    assert "TRANSIENT_API_ERROR" in text
    assert "processed" in text


def test_v1_database_is_migrated_in_place(tmp_path: Path):
    import sqlite3

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)
            """
        )
        connection.execute(
            """
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc TEXT NOT NULL,
                category TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                run_id TEXT,
                account_id TEXT,
                instrument_id TEXT,
                ticker TEXT,
                order_id TEXT,
                candle_time TEXT,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO events(
                timestamp_utc, category, event_type, severity, payload_json
            ) VALUES ('2026-07-20T00:00:00+00:00', 'cycle', 'processed', 'INFO', '{}')
            """
        )

    journal = EventJournal(path)
    journal.record(
        JournalEvent(
            category="cycle",
            event_type="processed",
            session_id="session-1",
            mode="DRY_RUN",
            status="processed",
            action="HOLD",
            strategy_id="sma",
            config_hash="abc",
            duration_seconds=1.25,
            api_attempts=2,
        )
    )

    rows = journal.recent(limit=10)
    assert len(rows) == 2
    assert rows[0]["session_id"] == "session-1"
    assert rows[0]["duration_seconds"] == 1.25
    assert rows[1]["event_type"] == "processed"


def test_journal_filters_sessions_severity_and_search(tmp_path: Path):
    journal = EventJournal(tmp_path / "events.db")
    journal.record(
        JournalEvent(
            category="api",
            event_type="API_RETRY_RECOVERED",
            severity="WARNING",
            session_id="session-a",
            strategy_id="sma",
            payload={"service": "MarketDataService"},
        )
    )
    journal.record(
        JournalEvent(
            category="cycle",
            event_type="processed",
            severity="INFO",
            session_id="session-b",
            payload={"ticker_note": "SBER"},
        )
    )

    rows = journal.recent(
        severity="WARNING", session_id="session-a", search="MarketData"
    )
    assert len(rows) == 1
    assert rows[0]["event_type"] == "API_RETRY_RECOVERED"
    assert journal.session_ids()[0] == "session-b"
    assert journal.grouped_counts("severity")["WARNING"] == 1


def test_recent_and_count_can_filter_by_account(tmp_path: Path):
    journal = EventJournal(tmp_path / "account-filter.db")
    journal.record(JournalEvent(category="risk", event_type="A", account_id="ACC-A"))
    journal.record(JournalEvent(category="risk", event_type="B", account_id="ACC-B"))

    rows = journal.recent(account_id="ACC-A")

    assert [row["event_type"] for row in rows] == ["A"]
    assert journal.count(account_id="ACC-A") == 1
    assert journal.count(account_id="ACC-B") == 1


def test_read_only_empty_wal_remains_side_effect_free(tmp_path: Path):
    path = tmp_path / "checkpointed.db"
    EventJournal(path).record(JournalEvent(category="test", event_type="READY"))
    wal = path.with_name(path.name + "-wal")
    shm = path.with_name(path.name + "-shm")
    wal.write_bytes(b"")
    shm.unlink(missing_ok=True)

    assert inspect_sqlite_file(path).valid
    assert EventJournal(path, read_only=True).count() == 1
    assert wal.read_bytes() == b""
    assert not shm.exists()
