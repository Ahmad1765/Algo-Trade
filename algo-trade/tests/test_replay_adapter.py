# file: tests/test_replay_adapter.py
import asyncio
from datetime import datetime

from src.market_adapter.replay_adapter import ReplayMarketAdapter
from src.sim.clock import SimClock, ET


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0
    def __call__(self) -> float:
        return self.t
    def advance(self, secs: float) -> None:
        self.t += secs


def _bars(symbol_open, closes):
    """Build June 17 1m bars starting 09:30 ET (13:30 UTC)."""
    out = []
    price = symbol_open
    for i, c in enumerate(closes):
        minute = 30 + i
        out.append({
            "datetime": f"2026-06-17T13:{minute:02d}:00+00:00",
            "open": price, "high": max(price, c), "low": min(price, c),
            "close": c, "volume": 1000 + i,
        })
        price = c
    return out


def _adapter(ft):
    data = {
        # UP ~ +5% by end
        "UP":   _bars(100.0, [101, 102, 103, 104, 105]),
        # DOWN ~ -5%
        "DOWN": _bars(100.0, [99, 98, 97, 96, 95]),
        # FLAT
        "FLAT": _bars(100.0, [100, 100, 100, 100, 100]),
    }
    clk = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0, time_fn=ft)
    return ReplayMarketAdapter(data, clk), ft


def test_no_lookahead_only_bars_up_to_sim_now():
    ft = FakeTime()
    adapter, ft = _adapter(ft)
    # At t=0 sim is 09:30 -> only the first bar (09:30) is visible.
    bars = asyncio.run(adapter.get_intraday_bars("UP"))
    assert len(bars) == 1
    assert bars[-1]["datetime"].endswith("13:30:00+00:00")
    # Advance 2 real sec * 60 = 2 sim min -> 09:32 -> 3 bars visible.
    ft.advance(2)
    bars = asyncio.run(adapter.get_intraday_bars("UP"))
    assert len(bars) == 3


def test_quote_change_pct_relative_to_day_open():
    ft = FakeTime()
    adapter, ft = _adapter(ft)
    ft.advance(4)  # 09:34 -> all 5 bars visible, close=105
    q = asyncio.run(adapter.get_quote("UP"))
    assert q.symbol == "UP"
    assert q.price == 105.0
    assert round(q.change_pct, 1) == 5.0


def test_gainers_and_losers_ranking():
    ft = FakeTime()
    adapter, ft = _adapter(ft)
    ft.advance(4)
    gainers = asyncio.run(adapter.get_top_gainers(limit=1))
    losers = asyncio.run(adapter.get_top_losers(limit=1))
    assert gainers[0].symbol == "UP"
    assert losers[0].symbol == "DOWN"


def test_intraday_bars_respects_limit():
    ft = FakeTime()
    adapter, ft = _adapter(ft)
    ft.advance(4)  # all 5 visible
    bars = asyncio.run(adapter.get_intraday_bars("UP", limit=2))
    assert len(bars) == 2
    assert bars[-1]["close"] == 105


def test_unknown_symbol_returns_zero_quote():
    ft = FakeTime()
    adapter, ft = _adapter(ft)
    q = asyncio.run(adapter.get_quote("NOPE"))
    assert q.symbol == "NOPE" and q.price == 0.0
