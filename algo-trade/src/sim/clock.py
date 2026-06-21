# file: src/sim/clock.py
"""
Simulated market clock for replay mode.

Advances a simulated "now" from the replay day's 09:30 ET open at a
configurable speed multiplier. Supports pause/resume and live speed
changes. Clamps at 16:00 ET (market close).

The wall-clock source is injectable (`time_fn`) so the clock is
deterministically testable.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


class SimClock:
    def __init__(
        self,
        sim_date: datetime,
        speed: float = 60.0,
        *,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be > 0")
        base = sim_date.astimezone(ET)
        self._market_open = base.replace(hour=9, minute=30, second=0, microsecond=0)
        self._market_close = base.replace(hour=16, minute=0, second=0, microsecond=0)
        self._time_fn = time_fn
        self._speed = float(speed)
        self._paused = False
        self._sim_anchor = self._market_open
        self._real_anchor = self._time_fn()

    def _reanchor(self) -> None:
        """Pin the current sim time so the next speed/pause change is seamless."""
        self._sim_anchor = self.now()
        self._real_anchor = self._time_fn()

    def now(self) -> datetime:
        if self._paused:
            sim = self._sim_anchor
        else:
            elapsed = self._time_fn() - self._real_anchor
            sim = self._sim_anchor + timedelta(seconds=elapsed * self._speed)
        return min(sim, self._market_close)

    def is_open(self) -> bool:
        return self._market_open <= self.now() < self._market_close

    def day_complete(self) -> bool:
        return self.now() >= self._market_close

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("speed must be > 0")
        self._reanchor()
        self._speed = float(speed)

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        if not self._paused:
            self._reanchor()
            self._paused = True

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            self._real_anchor = self._time_fn()

    def status(self) -> Dict[str, Any]:
        n = self.now()
        return {
            "active": True,
            "sim_time": n.strftime("%Y-%m-%d %H:%M:%S ET"),
            "sim_time_iso": n.isoformat(),
            "speed": self._speed,
            "paused": self._paused,
            "sim_date": self._market_open.strftime("%Y-%m-%d"),
            "day_complete": self.day_complete(),
            "market_open": self.is_open(),
        }
