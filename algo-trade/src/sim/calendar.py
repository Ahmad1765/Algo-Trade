# file: src/sim/calendar.py
"""Trading-day validation for replay dates (shared by server + simulate.py)."""
from __future__ import annotations

from datetime import date

from src.market_hours import _HOLIDAYS


def validate_sim_date(s: str) -> date:
    """Return a date for a valid NYSE trading day, else raise ValueError."""
    d = date.fromisoformat(s)
    if d.weekday() >= 5:
        raise ValueError(f"{s} is a weekend")
    if d in _HOLIDAYS:
        raise ValueError(f"{s} is a NYSE holiday")
    return d
