from __future__ import annotations

import csv
import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Literal

from .runtime_integrity import FileIntegrityReport, inspect_sqlite_file


@dataclass(frozen=True, slots=True)
class JournalEvent:
    """Structured event written to the local SQLite audit journal.

    Frequently queried fields are stored in dedicated columns.  Everything
    strategy/provider-specific remains in ``payload`` so the schema can evolve
    without losing details.
    """

    category: str
    event_type: str
    severity: str = "INFO"
    session_id: str | None = None
    run_id: str | None = None
    account_id: str | None = None
    instrument_id: str | None = None
    ticker: str | None = None
    order_id: str | None = None
    candle_time: str | None = None
    mode: str | None = None
    status: str | None = None
    action: str | None = None
    strategy_id: str | None = None
    config_hash: str | None = None
    duration_seconds: float | None = None
    api_attempts: int | None = None
    payload: dict[str, Any] | None = None
    timestamp_utc: str | None = None


class EventJournal:
    """Append-only local audit trail for cycles, decisions, API and orders.

    Connections are short-lived and therefore safe across Tkinter background
    threads.  Schema v2 is an in-place migration: existing v3.4/v3.5 databases
    keep all rows and simply receive additional indexed columns.
    """

    SCHEMA_VERSION = 2

    _EVENT_COLUMNS: ClassVar[dict[str, str]] = {
        "session_id": "TEXT",
        "mode": "TEXT",
        "status": "TEXT",
        "action": "TEXT",
        "strategy_id": "TEXT",
        "config_hash": "TEXT",
        "duration_seconds": "REAL",
        "api_attempts": "INTEGER",
    }

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = bool(read_only)
        self._decision_index_ready = False
        if self.read_only:
            if not self.path.is_file():
                raise FileNotFoundError(f"EventJournal does not exist: {self.path}")
            self._init_lock = threading.Lock()
            self._initialised = True
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialised = False
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        read_only_uri = self.path.resolve().as_uri() + "?mode=ro"
        wal_path = self.path.with_name(self.path.name + "-wal")
        wal_is_empty = False
        if self.read_only:
            try:
                wal_is_empty = not wal_path.exists() or wal_path.stat().st_size == 0
            except OSError:
                wal_is_empty = False
        if self.read_only and wal_is_empty:
            # A stopped, fully checkpointed journal can be opened as immutable.
            # This prevents SQLite from creating -wal/-shm sidecars on a
            # strictly read-only reporting path. An empty leftover WAL is also
            # safe to ignore. If a non-empty WAL exists we retain the normal
            # read-only URI so committed WAL frames remain visible.
            read_only_uri += "&immutable=1"
        connection = (
            sqlite3.connect(
                read_only_uri,
                timeout=10.0,
                isolation_level=None,
                uri=True,
            )
            if self.read_only
            else sqlite3.connect(
                self.path,
                timeout=10.0,
                isolation_level=None,
            )
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Yield one transaction-scoped connection and always close it."""

        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        if self._initialised:
            return
        with self._init_lock:
            if self._initialised:
                return
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp_utc TEXT NOT NULL,
                        category TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        session_id TEXT,
                        run_id TEXT,
                        account_id TEXT,
                        instrument_id TEXT,
                        ticker TEXT,
                        order_id TEXT,
                        candle_time TEXT,
                        mode TEXT,
                        status TEXT,
                        action TEXT,
                        strategy_id TEXT,
                        config_hash TEXT,
                        duration_seconds REAL,
                        api_attempts INTEGER,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                existing = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(events)"
                    ).fetchall()
                }
                for column, sql_type in self._EVENT_COLUMNS.items():
                    if column not in existing:
                        connection.execute(
                            f"ALTER TABLE events ADD COLUMN {column} {sql_type}"
                        )

                index_statements = (
                    (
                        "CREATE INDEX IF NOT EXISTS idx_events_ts "
                        "ON events(timestamp_utc DESC)"
                    ),
                    (
                        "CREATE INDEX IF NOT EXISTS idx_events_order "
                        "ON events(order_id, id)"
                    ),
                    (
                        "CREATE INDEX IF NOT EXISTS idx_events_category "
                        "ON events(category, id DESC)"
                    ),
                    (
                        "CREATE INDEX IF NOT EXISTS idx_events_session "
                        "ON events(session_id, id DESC)"
                    ),
                    (
                        "CREATE INDEX IF NOT EXISTS idx_events_run "
                        "ON events(run_id, id DESC)"
                    ),
                    (
                        "CREATE INDEX IF NOT EXISTS idx_events_severity "
                        "ON events(severity, id DESC)"
                    ),
                    (
                        "CREATE INDEX IF NOT EXISTS idx_events_strategy "
                        "ON events(strategy_id, candle_time)"
                    ),
                )
                for statement in index_statements:
                    connection.execute(statement)
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_events_portfolio_risk_shadow_run
                    ON events(category, run_id)
                    WHERE category = 'portfolio_risk_shadow'
                      AND run_id IS NOT NULL
                    """
                )
                connection.execute(
                    """
                    INSERT INTO metadata(key, value)
                    VALUES('schema_version', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (str(self.SCHEMA_VERSION),),
                )
            self._initialised = True

    def record(self, event: JournalEvent) -> int:
        if self.read_only:
            raise RuntimeError("Read-only EventJournal cannot record events.")
        self._ensure_schema()
        timestamp = event.timestamp_utc or datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(
            event.payload or {},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        with self._connection() as connection:
            if not self._decision_index_ready:
                # Opening a pre-upgrade journal must preserve its exact bytes.
                # The first append is the first point where its schema may grow.
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_events_decision_scope "
                    "ON events(session_id, instrument_id, category, event_type, id DESC)"
                )
                self._decision_index_ready = True
            cursor = connection.execute(
                """
                INSERT INTO events(
                    timestamp_utc,
                    category,
                    event_type,
                    severity,
                    session_id,
                    run_id,
                    account_id,
                    instrument_id,
                    ticker,
                    order_id,
                    candle_time,
                    mode,
                    status,
                    action,
                    strategy_id,
                    config_hash,
                    duration_seconds,
                    api_attempts,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    event.category,
                    event.event_type,
                    event.severity,
                    event.session_id,
                    event.run_id,
                    event.account_id,
                    event.instrument_id,
                    event.ticker,
                    event.order_id,
                    event.candle_time,
                    event.mode,
                    event.status,
                    event.action,
                    event.strategy_id,
                    event.config_hash,
                    event.duration_seconds,
                    event.api_attempts,
                    payload_json,
                ),
            )
            return int(cursor.lastrowid)

    def record_portfolio_risk_shadow(
        self,
        event: JournalEvent,
    ) -> tuple[int, bool]:
        """Record one M3 shadow event per run_id across restart and retries."""

        if self.read_only:
            raise RuntimeError("Read-only EventJournal cannot record events.")
        if event.category != "portfolio_risk_shadow":
            raise ValueError(
                "Shadow-idempotent journal writes require "
                "category='portfolio_risk_shadow'."
            )
        run_id = str(event.run_id or "").strip()
        if not run_id:
            raise ValueError("Idempotent shadow events require run_id.")
        try:
            return self.record(event), True
        except sqlite3.IntegrityError:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT id FROM events WHERE category = ? AND run_id = ? "
                    "ORDER BY id LIMIT 1",
                    (event.category, run_id),
                ).fetchone()
            if row is None:
                raise
            return int(row[0]), False

    def record_cycle(self, **kwargs: Any) -> int:
        return self.record(JournalEvent(category="cycle", **kwargs))

    def record_decision(self, **kwargs: Any) -> int:
        return self.record(JournalEvent(category="decision", **kwargs))

    def record_order(self, **kwargs: Any) -> int:
        return self.record(JournalEvent(category="order", **kwargs))

    def record_api(self, **kwargs: Any) -> int:
        return self.record(JournalEvent(category="api", **kwargs))

    def record_incident(self, **kwargs: Any) -> int:
        return self.record(JournalEvent(category="incident", **kwargs))

    def record_session(self, **kwargs: Any) -> int:
        return self.record(JournalEvent(category="session", **kwargs))

    def integrity_report(self) -> FileIntegrityReport:
        """Run SQLite quick/integrity checks without mutating the journal."""

        self._ensure_schema()
        return inspect_sqlite_file(self.path)

    def checkpoint_wal(self, mode: str = "PASSIVE") -> dict[str, Any]:
        """Checkpoint the WAL and return SQLite's three counters.

        The operation is explicit and never runs from the trading hot path.
        It is used by backup/readiness tooling before copying the database.
        """

        normalized = str(mode).strip().upper()
        if normalized not in {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}:
            raise ValueError("Unsupported WAL checkpoint mode.")
        self._ensure_schema()
        with self._connection() as connection:
            row = connection.execute(f"PRAGMA wal_checkpoint({normalized})").fetchone()
        busy, log_frames, checkpointed_frames = (
            (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)
        )
        return {
            "mode": normalized,
            "busy": busy,
            "log_frames": log_frames,
            "checkpointed_frames": checkpointed_frames,
        }

    def backup_to(self, destination: str | Path) -> Path:
        """Create a transactionally consistent SQLite backup."""

        self._ensure_schema()
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.unlink(missing_ok=True)
        source = self._connect()
        backup = sqlite3.connect(temporary, timeout=10.0)
        try:
            source.backup(backup)
            backup.commit()
        finally:
            backup.close()
            source.close()
        temporary.replace(target)
        report = inspect_sqlite_file(target)
        if not report.valid:
            target.unlink(missing_ok=True)
            raise sqlite3.DatabaseError(
                f"SQLite backup failed integrity check: {report.detail}"
            )
        return target

    def recent(
        self,
        limit: int = 200,
        *,
        category: str | None = None,
        order_id: str | None = None,
        account_id: str | None = None,
        severity: str | None = None,
        session_id: str | None = None,
        instrument_id: str | None = None,
        event_type: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        self._ensure_schema()
        limit = max(1, min(int(limit), 100_000))
        where: list[str] = []
        params: list[Any] = []
        if category:
            where.append("category = ?")
            params.append(category)
        if order_id:
            where.append("order_id = ?")
            params.append(order_id)
        if account_id:
            where.append("account_id = ?")
            params.append(account_id)
        if severity:
            where.append("severity = ?")
            params.append(severity.upper())
        if session_id:
            where.append("session_id = ?")
            params.append(session_id)
        if instrument_id is not None:
            if type(instrument_id) is not str or not instrument_id.strip():
                raise ValueError("instrument_id must be a non-empty exact string")
            where.append("instrument_id = ?")
            params.append(instrument_id)
        if event_type:
            where.append("event_type = ?")
            params.append(event_type)
        if search:
            token = f"%{search.strip()}%"
            where.append(
                "(event_type LIKE ? OR ticker LIKE ? OR order_id LIKE ? "
                "OR strategy_id LIKE ? OR payload_json LIKE ?)"
            )
            params.extend([token, token, token, token, token])
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT id, timestamp_utc, category, event_type, severity,
                       session_id, run_id, account_id, instrument_id, ticker,
                       order_id, candle_time, mode, status, action,
                       strategy_id, config_hash, duration_seconds,
                       api_attempts, payload_json
                FROM events
                {clause}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json"))
            except (TypeError, json.JSONDecodeError):
                item["payload"] = {"raw": item.pop("payload_json", "")}
            result.append(item)
        return result

    def export_csv(
        self,
        destination: str | Path,
        *,
        category: str | None = None,
        account_id: str | None = None,
        severity: str | None = None,
        session_id: str | None = None,
        search: str | None = None,
        limit: int = 100_000,
    ) -> Path:
        destination = Path(destination)
        rows = self.recent(
            limit=limit,
            category=category,
            account_id=account_id,
            severity=severity,
            session_id=session_id,
            search=search,
        )
        fieldnames = [
            "id",
            "timestamp_utc",
            "category",
            "event_type",
            "severity",
            "session_id",
            "run_id",
            "account_id",
            "instrument_id",
            "ticker",
            "order_id",
            "candle_time",
            "mode",
            "status",
            "action",
            "strategy_id",
            "config_hash",
            "duration_seconds",
            "api_attempts",
            "payload_json",
        ]
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in reversed(rows):
                output = {key: row.get(key) for key in fieldnames}
                output["payload_json"] = json.dumps(
                    row.get("payload", {}),
                    ensure_ascii=False,
                    default=str,
                )
                writer.writerow(output)
        return destination

    def count(
        self,
        *,
        category: str | None = None,
        account_id: str | None = None,
        severity: str | None = None,
        session_id: str | None = None,
    ) -> int:
        self._ensure_schema()
        where: list[str] = []
        params: list[Any] = []
        if category:
            where.append("category = ?")
            params.append(category)
        if account_id:
            where.append("account_id = ?")
            params.append(account_id)
        if severity:
            where.append("severity = ?")
            params.append(severity.upper())
        if session_id:
            where.append("session_id = ?")
            params.append(session_id)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM events" + clause,
                params,
            ).fetchone()
        return int(row[0]) if row else 0

    def grouped_counts(
        self,
        field: Literal["category", "severity", "event_type"],
        *,
        session_id: str | None = None,
    ) -> dict[str, int]:
        """Return compact dashboard counts for a safe, whitelisted field."""

        self._ensure_schema()
        where = "WHERE session_id = ?" if session_id else ""
        params: Iterable[Any] = (session_id,) if session_id else ()
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {field}, COUNT(*) AS n FROM events "
                f"{where} GROUP BY {field} ORDER BY n DESC",
                params,
            ).fetchall()
        return {str(row[0] or ""): int(row[1]) for row in rows}

    def session_ids(self, limit: int = 50) -> list[str]:
        self._ensure_schema()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT session_id, MAX(id) AS last_id
                FROM events
                WHERE session_id IS NOT NULL AND session_id <> ''
                GROUP BY session_id
                ORDER BY last_id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [str(row[0]) for row in rows]
