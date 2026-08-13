from __future__ import annotations

from dataclasses import dataclass

from .global_scheduler import SchedulerAuditEvent
from .journal import EventJournal, JournalEvent


@dataclass(slots=True)
class EventJournalSchedulerSink:
    """Translate scheduler audit records into the existing SQLite journal."""

    journal: EventJournal
    mode: str | None = None

    def record_scheduler_event(self, event: SchedulerAuditEvent) -> None:
        category = (
            "scheduler"
            if event.event_type.startswith("SCHEDULER_")
            else "runtime"
        )
        self.journal.record(
            JournalEvent(
                category=category,
                event_type=event.event_type,
                severity=event.severity,
                session_id=event.session_id,
                account_id=event.account_id,
                instrument_id=event.instrument_id,
                ticker=event.ticker,
                candle_time=event.candle_time,
                mode=self.mode,
                status=event.status,
                action=event.action,
                config_hash=event.config_hash,
                payload={
                    "runtime_key": event.runtime_key,
                    **dict(event.payload),
                },
                timestamp_utc=event.occurred_at.isoformat(),
            )
        )


__all__ = ["EventJournalSchedulerSink"]
