from __future__ import annotations

"""Deterministic crash-injection hooks used by the RC recovery matrix.

The hook is disabled by default and cannot be enabled from the GUI.  Tests may
pass an explicit injector or set MOEX_TEST_CRASH_AFTER_PHASE.  The exception
inherits BaseException so broad application ``except Exception`` blocks do not
turn a simulated hard process crash into a normal recoverable error.
"""

from dataclasses import dataclass, field
import os
from typing import Any, Mapping


class SimulatedProcessCrash(BaseException):
    def __init__(self, phase: str, context: Mapping[str, Any] | None = None) -> None:
        self.phase = str(phase)
        self.context = dict(context or {})
        super().__init__(f"Simulated hard process crash after phase {self.phase}")


@dataclass(slots=True)
class CrashInjector:
    phase: str | None = None
    once: bool = True
    enabled: bool = False
    _triggered: bool = field(default=False, init=False, repr=False)

    @classmethod
    def from_environment(cls) -> "CrashInjector":
        raw = os.getenv("MOEX_TEST_CRASH_AFTER_PHASE", "").strip().upper()
        armed = os.getenv("MOEX_ENABLE_TEST_CRASH_INJECTION", "").strip().upper() in {
            "YES", "TRUE", "1", "ON"
        }
        # A phase variable by itself must never crash an operator session.
        return cls(phase=raw or None, once=True, enabled=bool(raw) and armed)

    def checkpoint(
        self,
        phase: str,
        *,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        if not self.enabled or not self.phase:
            return
        if self.once and self._triggered:
            return
        normalized = str(phase).strip().upper()
        if normalized != str(self.phase).strip().upper():
            return
        self._triggered = True
        raise SimulatedProcessCrash(normalized, context)
