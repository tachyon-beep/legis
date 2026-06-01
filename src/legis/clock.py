"""Injectable time source.

Production code never calls ``datetime.now()`` directly; it takes a ``Clock``.
Tests inject ``FixedClock`` for determinism (the same discipline as elspeth's
``clock.py``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now_iso(self) -> str: ...


class SystemClock:
    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()


class FixedClock:
    """Deterministic clock for tests."""

    def __init__(self, value: str) -> None:
        self._value = value

    def now_iso(self) -> str:
        return self._value
