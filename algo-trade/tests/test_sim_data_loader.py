# file: tests/test_sim_data_loader.py
import asyncio
import json
from datetime import date

import pytest

from src.sim import data_loader


class FakeYahoo:
    """Stub adapter returning two days of bars; loader must keep only sim_date."""
    def __init__(self, *_args, **_kwargs) -> None:
        self.calls = 0

    async def get_historical_bars(self, symbol, range_str="1d", interval="1m"):
        self.calls += 1
        return [
            {"datetime": "2026-06-16T14:00:00+00:00", "open": 10, "high": 10,
             "low": 10, "close": 10, "volume": 1},   # wrong day -> dropped
            {"datetime": "2026-06-17T13:30:00+00:00", "open": 100, "high": 101,
             "low": 99, "close": 100.5, "volume": 5000},  # 09:30 ET June 17
            {"datetime": "2026-06-17T13:31:00+00:00", "open": 100.5, "high": 102,
             "low": 100, "close": 101.2, "volume": 4200},
        ]

    async def close(self):
        return None


def test_load_day_filters_to_sim_date_and_caches(tmp_path):
    fake = FakeYahoo()
    result = asyncio.run(data_loader.load_day(
        date(2026, 6, 17), cache_dir=tmp_path,
        universe=["AAPL", "MSFT"], adapter=fake,
    ))
    # Only June 17 bars survive, both symbols loaded.
    assert set(result.keys()) == {"AAPL", "MSFT"}
    assert len(result["AAPL"]) == 2
    assert all(b["datetime"].startswith("2026-06-17") for b in result["AAPL"])
    # Cache file written.
    files = list(tmp_path.glob("2026-06-17_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload["date"] == "2026-06-17"
    assert "AAPL" in payload["symbols"]


def test_load_day_uses_cache_on_second_call(tmp_path):
    fake = FakeYahoo()
    asyncio.run(data_loader.load_day(
        date(2026, 6, 17), cache_dir=tmp_path, universe=["AAPL"], adapter=fake,
    ))
    first_calls = fake.calls
    # Second call: cache hit -> adapter not touched again.
    fake2 = FakeYahoo()
    result = asyncio.run(data_loader.load_day(
        date(2026, 6, 17), cache_dir=tmp_path, universe=["AAPL"], adapter=fake2,
    ))
    assert fake2.calls == 0
    assert "AAPL" in result
    assert first_calls > 0


def test_load_day_raises_when_nothing_loaded(tmp_path):
    class EmptyYahoo:
        async def get_historical_bars(self, *a, **k):
            return []
        async def close(self):
            return None
    with pytest.raises(RuntimeError):
        asyncio.run(data_loader.load_day(
            date(2026, 6, 17), cache_dir=tmp_path,
            universe=["AAPL"], adapter=EmptyYahoo(),
        ))
