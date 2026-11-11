"""Time source.

Real runs need wall-clock time, but gate policies are expressed in minutes and
some adversarial cases wait 26 hours. Tests and dry runs use ``FakeClock`` so a
whole case finishes instantly and deterministically.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current UTC time."""

    def sleep(self, seconds: float) -> None:
        """Block (or advance fake time) by ``seconds``."""

    def advance(self, seconds: float) -> None:
        """Advance time without sleeping. Only fake clocks implement this."""


class RealClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def advance(self, seconds: float) -> None:
        raise RuntimeError("RealClock cannot advance time; use FakeClock")


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=max(0.0, seconds))

    def advance(self, seconds: float) -> None:
        self.sleep(seconds)
