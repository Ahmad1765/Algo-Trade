# file: tests/e2e/market_hours.spec.py
"""
E2E tests for src/market_hours.py

Covers:
  - now_et() returns a timezone-aware datetime in the US/Eastern timezone
  - is_market_open() returns False for times outside 09:30–16:00 ET
  - is_market_open() returns False on weekends
  - is_market_open() returns False on known NYSE holidays
  - is_market_open() returns True during regular trading hours on a weekday
  - is_market_open() returns False exactly at 16:00 ET (market closed at close)
  - is_market_open() returns True at 09:30 ET (market opens at open)
  - wait_for_market_open() returns immediately when market is already open
  - wait_for_market_open() waits when market is closed (mocked sleep)
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

import src.market_hours as mh

pytestmark = pytest.mark.e2e

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Helpers — build datetime in ET timezone
# ---------------------------------------------------------------------------

def _et(year, month, day, hour, minute, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=ET)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNowEt:
    def test_now_et_returns_datetime(self):
        result = mh.now_et()
        assert isinstance(result, datetime)

    def test_now_et_is_timezone_aware(self):
        result = mh.now_et()
        assert result.tzinfo is not None

    def test_now_et_timezone_is_eastern(self):
        result = mh.now_et()
        # The UTC offset for Eastern time should be -4 or -5 hours (DST/standard)
        offset_hours = result.utcoffset().total_seconds() / 3600
        assert -5 <= offset_hours <= -4


class TestIsMarketOpen:
    def test_returns_false_before_market_open(self):
        """08:59 ET on a trading day → closed."""
        dt = _et(2026, 4, 8, 8, 59)  # Wednesday — normal trading day
        assert mh.is_market_open(dt) is False

    def test_returns_true_during_regular_hours(self):
        """10:30 ET on a normal Wednesday → open."""
        dt = _et(2026, 4, 8, 10, 30)
        assert mh.is_market_open(dt) is True

    def test_returns_false_after_market_close(self):
        """16:01 ET on a trading day → closed."""
        dt = _et(2026, 4, 8, 16, 1)
        assert mh.is_market_open(dt) is False

    def test_returns_false_at_exactly_market_close(self):
        """16:00 ET — market is closed at the close time (exclusive upper bound)."""
        dt = _et(2026, 4, 8, 16, 0, 0)
        assert mh.is_market_open(dt) is False

    def test_returns_true_at_market_open(self):
        """09:30 ET — market is open at the open time (inclusive lower bound)."""
        dt = _et(2026, 4, 8, 9, 30, 0)
        assert mh.is_market_open(dt) is True

    def test_returns_false_on_saturday(self):
        """Saturday → market closed regardless of time."""
        # 2026-04-11 is a Saturday
        dt = _et(2026, 4, 11, 11, 0)
        assert mh.is_market_open(dt) is False

    def test_returns_false_on_sunday(self):
        """Sunday → market closed."""
        # 2026-04-12 is a Sunday
        dt = _et(2026, 4, 12, 11, 0)
        assert mh.is_market_open(dt) is False

    def test_returns_false_on_new_years_day_2026(self):
        """New Year's Day 2026 (2026-01-01) is a NYSE holiday."""
        dt = _et(2026, 1, 1, 11, 0)
        assert mh.is_market_open(dt) is False

    def test_returns_false_on_christmas_2026(self):
        """Christmas 2026 (2026-12-25) is a NYSE holiday."""
        dt = _et(2026, 12, 25, 11, 0)
        assert mh.is_market_open(dt) is False

    def test_returns_false_on_mlk_day_2026(self):
        """MLK Day 2026 (2026-01-19) is a NYSE holiday."""
        dt = _et(2026, 1, 19, 11, 0)
        assert mh.is_market_open(dt) is False

    def test_returns_false_on_good_friday_2026(self):
        """Good Friday 2026 (2026-04-03) is a NYSE holiday."""
        dt = _et(2026, 4, 3, 11, 0)
        assert mh.is_market_open(dt) is False

    def test_returns_false_on_thanksgiving_2026(self):
        """Thanksgiving 2026 (2026-11-26) is a NYSE holiday."""
        dt = _et(2026, 11, 26, 11, 0)
        assert mh.is_market_open(dt) is False

    def test_returns_false_on_independence_day_2026(self):
        """Independence Day observed 2026 (2026-07-03) is a NYSE holiday."""
        dt = _et(2026, 7, 3, 11, 0)
        assert mh.is_market_open(dt) is False

    def test_returns_true_on_regular_monday(self):
        """2026-04-06 is a regular Monday (no holiday) — should be open mid-day."""
        dt = _et(2026, 4, 6, 12, 0)
        assert mh.is_market_open(dt) is True

    def test_just_before_open_returns_false(self):
        """09:29 ET → one minute before open → closed."""
        dt = _et(2026, 4, 8, 9, 29)
        assert mh.is_market_open(dt) is False

    def test_just_before_close_returns_true(self):
        """15:59 ET → one minute before close → still open."""
        dt = _et(2026, 4, 8, 15, 59)
        assert mh.is_market_open(dt) is True


class TestWaitForMarketOpen:
    async def test_wait_returns_immediately_when_market_open(self):
        """If seconds_until_open() returns 0, no sleep is called."""
        sleep_calls = []
        async def _fake_sleep(s):
            sleep_calls.append(s)
        with patch.object(mh, "seconds_until_open", return_value=0.0):
            with patch("src.market_hours.asyncio.sleep", new=_fake_sleep):
                await mh.wait_for_market_open()
        assert len(sleep_calls) == 0

    async def test_wait_sleeps_for_seconds_until_open_duration(self):
        """wait_for_market_open sleeps for the duration returned by seconds_until_open."""
        sleep_calls = []
        async def _fast_sleep(s):
            sleep_calls.append(s)
        with patch.object(mh, "seconds_until_open", return_value=3600.0):
            with patch("src.market_hours.asyncio.sleep", new=_fast_sleep):
                await mh.wait_for_market_open(log=None)
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == pytest.approx(3600.0)

    async def test_wait_accepts_log_argument_without_error(self):
        """wait_for_market_open accepts a logger argument and does not raise."""
        import logging
        log = logging.getLogger("test")
        with patch.object(mh, "seconds_until_open", return_value=0.0):
            async def _fast_sleep(s):
                pass
            with patch("src.market_hours.asyncio.sleep", new=_fast_sleep):
                await mh.wait_for_market_open(log=log)
