# file: src/market_adapter/replay_adapter.py
"""
Replay market-data adapter.

Serves a single cached trading day of bars, sliced at the SimClock's current
simulated time. Never returns a bar dated after sim-now (no look-ahead), so
the pipeline sees exactly the information available at that simulated moment.
Gainers/losers are ranked from each symbol's change since the day's open.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.events import MarketQuote
from src.market_adapter.base import MarketDataAdapter
from src.sim.clock import SimClock

ET = ZoneInfo("America/New_York")


class ReplayMarketAdapter(MarketDataAdapter):
    def __init__(self, data: Dict[str, List[Dict[str, Any]]], clock: SimClock) -> None:
        self._data = data
        self._clock = clock

    def _visible(self, symbol: str) -> List[Dict[str, Any]]:
        """Bars for `symbol` with datetime <= sim-now (assumes ascending order)."""
        now = self._clock.now()
        out: List[Dict[str, Any]] = []
        for b in self._data.get(symbol, []):
            try:
                dt = datetime.fromisoformat(b["datetime"]).astimezone(ET)
            except (KeyError, ValueError, TypeError):
                continue
            if dt <= now:
                out.append(b)
            else:
                break
        return out

    def _quote_for(self, symbol: str) -> Optional[MarketQuote]:
        all_bars = self._data.get(symbol, [])
        vis = self._visible(symbol)
        if not all_bars or not vis:
            return None
        day_open = all_bars[0]["open"]
        last = vis[-1]
        price = last["close"]
        change_pct = ((price - day_open) / day_open * 100) if day_open else 0.0
        return MarketQuote(
            symbol=symbol,
            price=round(float(price), 4),
            change_pct=round(float(change_pct), 4),
            volume=int(last.get("volume", 0) or 0),
            timestamp=self._clock.now(),
        )

    async def get_top_gainers(self, limit: int = 10) -> List[MarketQuote]:
        quotes = [q for q in (self._quote_for(s) for s in self._data) if q is not None]
        quotes.sort(key=lambda q: q.change_pct, reverse=True)
        return quotes[:limit]

    async def get_top_losers(self, limit: int = 10) -> List[MarketQuote]:
        quotes = [q for q in (self._quote_for(s) for s in self._data) if q is not None]
        quotes.sort(key=lambda q: q.change_pct)
        return quotes[:limit]

    async def get_quote(self, symbol: str) -> MarketQuote:
        q = self._quote_for(symbol)
        if q is None:
            return MarketQuote(symbol=symbol, price=0.0, change_pct=0.0,
                               volume=0, timestamp=self._clock.now())
        return q

    async def get_intraday_bars(
        self, symbol: str, interval: str = "1min", limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return self._visible(symbol)[-limit:]

    async def get_historical_bars(
        self, symbol: str, range_str: str = "1d", interval: str = "1m",
    ) -> List[Dict[str, Any]]:
        return self._visible(symbol)

    async def close(self) -> None:
        return None
