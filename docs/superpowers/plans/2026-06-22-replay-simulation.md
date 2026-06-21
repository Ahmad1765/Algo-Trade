# Historical Replay Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replay a real past trading day (default 2026-06-17) through the live trading pipeline on a simulated clock, so the dashboard shows prices ticking, signals firing, and paper trades executing as if live — with a user-controllable speed/pause control.

**Architecture:** A `SimClock` exposes a simulated "now" that advances at a configurable speed (pausable). A `ReplayMarketAdapter` implements the existing `MarketDataAdapter` interface but serves cached 2026-06-17 bars sliced at `sim_clock.now()` (no look-ahead), ranking real gainers/losers from the S&P 500 universe. A new `scripts/simulate.py` wires the clock + adapter into the existing pipeline (injected, paper mode). The aiohttp server, given the same `SimClock`, reports simulated time/market-open and exposes `/sim/status` + `/sim/control`. The Next.js topbar renders a control when sim mode is active.

**Tech Stack:** Python 3.11+ (asyncio, aiohttp, zoneinfo, stdlib only), pytest, Next.js/React/TypeScript (frontend), Yahoo Finance JSON endpoints (via existing `YahooFinanceAdapter`).

## Global Constraints

- Python: stdlib + existing deps only (`aiohttp`, `pyyaml`, `sqlalchemy`, `pytest`). No new Python packages.
- No pandas. Timezones via `zoneinfo` only (matches `src/market_hours.py`).
- Keep files under 500 lines (CLAUDE.md).
- Bar dict schema everywhere: `{datetime (ISO str), open, high, low, close, volume}`.
- `MarketQuote(symbol: str, price: float, change_pct: float, volume: int, timestamp: datetime)`.
- No behaviour change when sim mode is off — all existing tests must stay green.
- Do NOT commit `.env`, secrets, or the `sim_data/` cache (gitignore it).
- Replay date default: `2026-06-17` (Wednesday, valid NYSE day).

---

### Task 1: SimClock

**Files:**
- Create: `algo-trade/src/sim/__init__.py`
- Create: `algo-trade/src/sim/clock.py`
- Test: `algo-trade/tests/test_sim_clock.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SimClock(sim_date: datetime, speed: float = 60.0, *, time_fn=time.monotonic)` with methods `now() -> datetime`, `is_open() -> bool`, `day_complete() -> bool`, `set_speed(speed: float)`, `pause()`, `resume()`, `status() -> dict`, and properties `speed: float`, `paused: bool`. Also module constant `ET = ZoneInfo("America/New_York")`. `status()` dict keys: `active, sim_time, sim_time_iso, speed, paused, sim_date, day_complete, market_open`.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_sim_clock.py`:

```python
# file: tests/test_sim_clock.py
from datetime import datetime

from src.sim.clock import SimClock, ET


class FakeTime:
    """Controllable monotonic clock for deterministic tests."""
    def __init__(self) -> None:
        self.t = 1000.0
    def __call__(self) -> float:
        return self.t
    def advance(self, secs: float) -> None:
        self.t += secs


def _june17() -> datetime:
    return datetime(2026, 6, 17, tzinfo=ET)


def test_starts_at_market_open():
    clk = SimClock(_june17(), speed=60.0, time_fn=FakeTime())
    now = clk.now()
    assert now.hour == 9 and now.minute == 30
    assert clk.is_open() is True
    assert clk.day_complete() is False


def test_speed_scales_elapsed_time():
    ft = FakeTime()
    clk = SimClock(_june17(), speed=60.0, time_fn=ft)
    ft.advance(10)  # 10 real seconds * 60 = 600 sim seconds = 10 sim minutes
    assert clk.now().strftime("%H:%M") == "09:40"


def test_pause_freezes_sim_time():
    ft = FakeTime()
    clk = SimClock(_june17(), speed=60.0, time_fn=ft)
    ft.advance(5)            # -> 09:35
    clk.pause()
    frozen = clk.now()
    ft.advance(100)          # time passes but paused
    assert clk.now() == frozen
    assert clk.paused is True


def test_resume_continues_from_frozen_point():
    ft = FakeTime()
    clk = SimClock(_june17(), speed=60.0, time_fn=ft)
    ft.advance(5)            # 09:35
    clk.pause()
    ft.advance(100)          # ignored
    clk.resume()
    ft.advance(1)            # +1 real sec * 60 = +1 sim min -> 09:36
    assert clk.now().strftime("%H:%M") == "09:36"


def test_set_speed_reanchors_without_jump():
    ft = FakeTime()
    clk = SimClock(_june17(), speed=60.0, time_fn=ft)
    ft.advance(10)           # 09:40
    clk.set_speed(600.0)     # no time jump at the moment of change
    assert clk.now().strftime("%H:%M") == "09:40"
    ft.advance(1)            # +1 real * 600 = +10 sim min -> 09:50
    assert clk.now().strftime("%H:%M") == "09:50"


def test_clamps_at_market_close():
    ft = FakeTime()
    clk = SimClock(_june17(), speed=60.0, time_fn=ft)
    ft.advance(100_000)      # far past close
    now = clk.now()
    assert now.hour == 16 and now.minute == 0
    assert clk.is_open() is False
    assert clk.day_complete() is True


def test_set_speed_rejects_non_positive():
    clk = SimClock(_june17(), time_fn=FakeTime())
    try:
        clk.set_speed(0)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_status_shape():
    clk = SimClock(_june17(), speed=60.0, time_fn=FakeTime())
    s = clk.status()
    assert s["active"] is True
    assert s["sim_date"] == "2026-06-17"
    assert s["speed"] == 60.0
    assert s["paused"] is False
    assert s["market_open"] is True
    assert "sim_time" in s and "sim_time_iso" in s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_sim_clock.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.sim'`.

- [ ] **Step 3: Write minimal implementation**

Create `algo-trade/src/sim/__init__.py` (empty file).

Create `algo-trade/src/sim/clock.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd algo-trade && python -m pytest tests/test_sim_clock.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add algo-trade/src/sim/__init__.py algo-trade/src/sim/clock.py algo-trade/tests/test_sim_clock.py
git commit -m "feat(sim): add SimClock for replay mode"
```

---

### Task 2: S&P 500 universe list

**Files:**
- Create: `algo-trade/src/sim/sp500.py`
- Test: `algo-trade/tests/test_sp500_universe.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SP500_SYMBOLS: list[str]` — uppercase ticker strings, no duplicates, length ≥ 400.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_sp500_universe.py`:

```python
# file: tests/test_sp500_universe.py
from src.sim.sp500 import SP500_SYMBOLS


def test_universe_is_sizable():
    assert len(SP500_SYMBOLS) >= 400


def test_no_duplicates():
    assert len(SP500_SYMBOLS) == len(set(SP500_SYMBOLS))


def test_all_uppercase_nonempty():
    for s in SP500_SYMBOLS:
        assert s and s == s.upper()


def test_contains_known_megacaps():
    for sym in ("AAPL", "MSFT", "NVDA", "AMZN", "TSLA"):
        assert sym in SP500_SYMBOLS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_sp500_universe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.sim.sp500'`.

- [ ] **Step 3: Write minimal implementation**

Create `algo-trade/src/sim/sp500.py`. Populate `SP500_SYMBOLS` with the current S&P 500 constituents (Yahoo-compatible tickers; use `-` for class shares, e.g. `BRK-B`, `BF-B`). Source the list from a current public S&P 500 constituents reference. The list is long; below is the structure plus the head — fill in the full ~500 entries:

```python
# file: src/sim/sp500.py
"""
S&P 500 constituent universe for replay-mode mover ranking.

Tickers use Yahoo Finance conventions (class shares use '-', e.g. BRK-B).
This is reference data only — no logic. Update when constituents change.
"""

SP500_SYMBOLS: list[str] = [
    "A", "AAPL", "ABBV", "ABNB", "ABT", "ACGL", "ACN", "ADBE", "ADI", "ADM",
    "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG", "AIZ", "AJG", "AKAM",
    "ALB", "ALGN", "ALL", "ALLE", "AMAT", "AMCR", "AMD", "AME", "AMGN", "AMP",
    "AMT", "AMZN", "ANET", "ANSS", "AON", "AOS", "APA", "APD", "APH", "APTV",
    "ARE", "ATO", "AVB", "AVGO", "AVY", "AWK", "AXON", "AXP", "AZO", "BA",
    "BAC", "BALL", "BAX", "BBY", "BDX", "BEN", "BF-B", "BG", "BIIB", "BK",
    "BKNG", "BKR", "BLK", "BMY", "BR", "BRK-B", "BRO", "BSX", "BX", "C",
    "CAG", "CAH", "CARR", "CAT", "CB", "CBOE", "CBRE", "CCI", "CCL", "CDNS",
    "CDW", "CE", "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR", "CI", "CINF",
    "CL", "CLX", "CMCSA", "CME", "CMG", "CMI", "CMS", "CNC", "CNP", "COF",
    # ... continue with the remaining S&P 500 tickers through to ZTS ...
    "ZBH", "ZBRA", "ZTS",
]
```

NOTE TO IMPLEMENTER: do not ship the truncated list — populate all ~500 current constituents. If a complete list is unavailable at implementation time, a curated ≥400-name liquid large-cap universe satisfies the tests and the design; document the source in a comment.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd algo-trade && python -m pytest tests/test_sp500_universe.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add algo-trade/src/sim/sp500.py algo-trade/tests/test_sp500_universe.py
git commit -m "feat(sim): add S&P 500 universe for mover ranking"
```

---

### Task 3: Data loader + cache

**Files:**
- Create: `algo-trade/src/sim/data_loader.py`
- Modify: `algo-trade/.gitignore` (add `sim_data/`)
- Test: `algo-trade/tests/test_sim_data_loader.py`

**Interfaces:**
- Consumes: `SP500_SYMBOLS` (Task 2); `YahooFinanceAdapter` (`get_historical_bars(symbol, range_str, interval)`).
- Produces: `async load_day(sim_date: date, *, concurrency: int = 10, force_refresh: bool = False, cache_dir: Path = Path("sim_data"), universe: list[str] | None = None, adapter=None) -> dict[str, list[dict]]` returning `{symbol: [bar, ...]}` filtered to `sim_date` (ET), and writing a cache JSON. Also `CACHE_DIR: Path` default.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_sim_data_loader.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_sim_data_loader.py -v`
Expected: FAIL — `AttributeError: module 'src.sim.data_loader' has no attribute 'load_day'`.

- [ ] **Step 3: Write minimal implementation**

Create `algo-trade/src/sim/data_loader.py`:

```python
# file: src/sim/data_loader.py
"""
Fetch and cache one trading day of intraday bars for the replay universe.

Yahoo keeps 1-minute history for ~7 days; for older dates the loader falls
back to 5-minute bars (~60-day window). Results are cached to disk keyed by
date + interval so a restart loads instantly (and survives Yahoo dropping
1m history for the date).
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.logger import get_logger
from src.sim.sp500 import SP500_SYMBOLS

log = get_logger(__name__)
ET = ZoneInfo("America/New_York")
CACHE_DIR = Path("sim_data")


def _cache_path(cache_dir: Path, sim_date: date, interval: str) -> Path:
    return cache_dir / f"{sim_date.isoformat()}_{interval}.json"


def _bar_in_day(bar: Dict[str, Any], sim_date: date) -> bool:
    try:
        dt = datetime.fromisoformat(bar["datetime"]).astimezone(ET)
    except (KeyError, ValueError, TypeError):
        return False
    return dt.date() == sim_date


async def _fetch_symbol(adapter, symbol, sim_date, range_str, interval, sem) -> List[Dict[str, Any]]:
    async with sem:
        try:
            bars = await adapter.get_historical_bars(symbol, range_str=range_str, interval=interval)
        except Exception as exc:  # noqa: BLE001 — skip failed symbol, keep going
            log.debug("sim fetch failed", symbol=symbol, error=str(exc))
            return []
    return [b for b in bars if _bar_in_day(b, sim_date)]


async def load_day(
    sim_date: date,
    *,
    concurrency: int = 10,
    force_refresh: bool = False,
    cache_dir: Path = CACHE_DIR,
    universe: Optional[List[str]] = None,
    adapter: Any = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return {symbol: [bars]} for sim_date, using the disk cache when present."""
    symbols_universe = universe if universe is not None else SP500_SYMBOLS

    age_days = (datetime.now(ET).date() - sim_date).days
    interval = "1m" if age_days <= 7 else "5m"
    range_str = "7d" if interval == "1m" else "60d"
    cache = _cache_path(cache_dir, sim_date, interval)

    if cache.exists() and not force_refresh:
        log.info("sim data cache hit", path=str(cache))
        return json.loads(cache.read_text())["symbols"]

    owns_adapter = adapter is None
    if adapter is None:
        from src.market_adapter.yahoo_adapter import YahooFinanceAdapter
        adapter = YahooFinanceAdapter({})

    log.info("sim data fetch start", symbols=len(symbols_universe), interval=interval)
    sem = asyncio.Semaphore(concurrency)
    try:
        results = await asyncio.gather(*[
            _fetch_symbol(adapter, s, sim_date, range_str, interval, sem)
            for s in symbols_universe
        ])
    finally:
        if owns_adapter:
            await adapter.close()

    symbols = {s: bars for s, bars in zip(symbols_universe, results) if bars}
    if not symbols:
        raise RuntimeError(
            f"No bars fetched for {sim_date}. Check it is a trading day within "
            f"Yahoo's history window (1m: ~7 days, 5m: ~60 days)."
        )
    log.info("sim data fetch done", loaded=len(symbols),
             skipped=len(symbols_universe) - len(symbols), interval=interval)

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "date": sim_date.isoformat(),
        "interval": interval,
        "fetched_at": datetime.now(ET).isoformat(),
        "symbols": symbols,
    }))
    return symbols
```

- [ ] **Step 4: Add cache dir to gitignore**

Append to `algo-trade/.gitignore`:

```
# Replay simulation cached market data
sim_data/
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd algo-trade && python -m pytest tests/test_sim_data_loader.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add algo-trade/src/sim/data_loader.py algo-trade/tests/test_sim_data_loader.py algo-trade/.gitignore
git commit -m "feat(sim): add cached S&P 500 day data loader"
```

---

### Task 4: ReplayMarketAdapter

**Files:**
- Create: `algo-trade/src/market_adapter/replay_adapter.py`
- Test: `algo-trade/tests/test_replay_adapter.py`

**Interfaces:**
- Consumes: `SimClock` (Task 1, `now()`); `MarketDataAdapter` base; `MarketQuote`.
- Produces: `ReplayMarketAdapter(data: dict[str, list[dict]], clock: SimClock)` implementing `get_top_gainers`, `get_top_losers`, `get_quote`, `get_intraday_bars`, `get_historical_bars`, `close` — all async, all sliced at `clock.now()` with no look-ahead.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_replay_adapter.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_replay_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.market_adapter.replay_adapter'`.

- [ ] **Step 3: Write minimal implementation**

Create `algo-trade/src/market_adapter/replay_adapter.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd algo-trade && python -m pytest tests/test_replay_adapter.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add algo-trade/src/market_adapter/replay_adapter.py algo-trade/tests/test_replay_adapter.py
git commit -m "feat(sim): add ReplayMarketAdapter serving cached day on sim clock"
```

---

### Task 5: Server sim-clock integration + endpoints

**Files:**
- Modify: `algo-trade/src/api_server/server.py`
- Test: `algo-trade/tests/test_sim_endpoints.py`

**Interfaces:**
- Consumes: `SimClock` (Task 1).
- Produces: `create_app(...)` gains a trailing keyword param `sim_clock=None`. When set: `/health`, `/status`, `/stream` report `sim_clock.is_open()` / `sim_clock.now()`; new routes `GET /sim/status` and `POST /sim/control` (and `/api/...` aliases). `POST /sim/control` body: `{"action": "pause"|"resume"|"set_speed", "speed"?: number}`.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_sim_endpoints.py`:

```python
# file: tests/test_sim_endpoints.py
from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.api_server.server import create_app
from src.sim.clock import SimClock, ET


class _Risk:
    pass


@pytest.fixture
async def client_with_sim(aiohttp_client):
    clock = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0)
    app = create_app(_Risk(), [], None, None, [], None, sim_clock=clock)
    return await aiohttp_client(app), clock


async def test_sim_status_active(aiohttp_client):
    clock = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0)
    app = create_app(_Risk(), [], None, None, [], None, sim_clock=clock)
    client = await aiohttp_client(app)
    resp = await client.get("/sim/status")
    assert resp.status == 200
    body = await resp.json()
    assert body["active"] is True
    assert body["sim_date"] == "2026-06-17"


async def test_sim_status_inactive_without_clock(aiohttp_client):
    app = create_app(_Risk(), [], None, None, [], None)
    client = await aiohttp_client(app)
    resp = await client.get("/sim/status")
    body = await resp.json()
    assert body["active"] is False


async def test_sim_control_pause_resume_and_speed(aiohttp_client):
    clock = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0)
    app = create_app(_Risk(), [], None, None, [], None, sim_clock=clock)
    client = await aiohttp_client(app)

    r = await client.post("/sim/control", json={"action": "pause"})
    assert r.status == 200
    assert (await r.json())["paused"] is True

    r = await client.post("/sim/control", json={"action": "resume"})
    assert (await r.json())["paused"] is False

    r = await client.post("/sim/control", json={"action": "set_speed", "speed": 10})
    assert (await r.json())["speed"] == 10.0

    r = await client.post("/sim/control", json={"action": "set_speed", "speed": -1})
    assert r.status == 422

    r = await client.post("/sim/control", json={"action": "bogus"})
    assert r.status == 422


async def test_health_reports_sim_market_open(aiohttp_client):
    clock = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0)
    app = create_app(_Risk(), [], None, None, [], None, sim_clock=clock)
    client = await aiohttp_client(app)
    body = await (await client.get("/health")).json()
    assert body["market_open"] is True
    assert body["market_time_et"].startswith("2026-06-17")
```

NOTE: this suite needs `pytest-aiohttp`'s `aiohttp_client` fixture. If it is not already a dev dependency, the existing e2e tests reveal the project's aiohttp test pattern — match whichever is already in use (check `tests/e2e/`); adapt the fixture style accordingly rather than adding a new dependency.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_sim_endpoints.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'sim_clock'`.

- [ ] **Step 3: Implement — add the `sim_clock` param**

In `algo-trade/src/api_server/server.py`, change the `create_app` signature (around line 78-86) to add a trailing keyword:

```python
def create_app(
    risk_manager: Any,
    signal_store: List[Dict],
    position_store: Optional[Any] = None,
    market_adapter: Optional[Any] = None,
    action_store: Optional[List[Dict]] = None,
    broker_adapter: Optional[Any] = None,
    strategy_engine: Optional[Any] = None,
    sim_clock: Optional[Any] = None,
) -> web.Application:
```

- [ ] **Step 4: Implement — sim-aware time helpers**

Immediately after the `_action_store = ...` line inside `create_app` (around line 87), add two helpers:

```python
    def _market_open_now() -> bool:
        return sim_clock.is_open() if sim_clock is not None else is_market_open()

    def _market_time_str() -> str:
        src_dt = sim_clock.now() if sim_clock is not None else now_et()
        return src_dt.strftime("%Y-%m-%d %H:%M:%S ET")
```

Then replace each `is_market_open()` call inside `health`, `get_metrics`, `get_status`, and `sse_stream` with `_market_open_now()`, and each `now_et().strftime("%Y-%m-%d %H:%M:%S ET")` with `_market_time_str()`. (Exact spots: `health` line ~95-96, `get_metrics` line ~136, `get_status` lines ~162-163, `sse_stream` lines ~210-211.) Leave the module-level `is_market_open` / `now_et` imports in place — they are still the fallback.

- [ ] **Step 5: Implement — the two sim endpoints**

Add these handlers inside `create_app` (e.g. just before the `# ── Middlewares ──` section, ~line 891):

```python
    async def sim_status(request: web.Request) -> web.Response:
        if sim_clock is None:
            return web.json_response({"active": False})
        return web.json_response(sim_clock.status())

    async def sim_control(request: web.Request) -> web.Response:
        if sim_clock is None:
            return web.json_response({"error": "sim mode not active"}, status=409)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        action = body.get("action")
        if action == "pause":
            sim_clock.pause()
        elif action == "resume":
            sim_clock.resume()
        elif action == "set_speed":
            try:
                sim_clock.set_speed(float(body.get("speed")))
            except (TypeError, ValueError):
                return web.json_response({"error": "speed must be a positive number"}, status=422)
        else:
            return web.json_response({"error": "action must be pause|resume|set_speed"}, status=422)
        return web.json_response(sim_clock.status())
```

Then register them in the `api_routes` list (around line 1003-1022) by adding:

```python
        ("GET",  "/sim/status",         sim_status),
        ("POST", "/sim/control",        sim_control),
```

(The existing loop already mounts every entry at both the root path and the `/api` prefix.)

- [ ] **Step 6: Run test to verify it passes**

Run: `cd algo-trade && python -m pytest tests/test_sim_endpoints.py -v`
Expected: PASS. Then run the existing server tests to confirm no regression:
Run: `cd algo-trade && python -m pytest tests/e2e -k "health or metrics or status" -v`
Expected: PASS (sim_clock defaults to None → unchanged behaviour).

- [ ] **Step 7: Commit**

```bash
git add algo-trade/src/api_server/server.py algo-trade/tests/test_sim_endpoints.py
git commit -m "feat(sim): server reports sim time + /sim/status,/sim/control endpoints"
```

---

### Task 6: Pipeline injection (cli/main.py)

**Files:**
- Modify: `algo-trade/src/cli/main.py:60-186` (`_run_pipeline`)
- Test: `algo-trade/tests/test_pipeline_injection.py`

**Interfaces:**
- Consumes: `create_app(..., sim_clock=...)` (Task 5).
- Produces: `_run_pipeline(config, mode, *, market_adapter=None, sim_clock=None)` — when `market_adapter` is provided it is used instead of `create_market_adapter(config)`; `sim_clock` is forwarded to `create_app`.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_pipeline_injection.py`:

```python
# file: tests/test_pipeline_injection.py
import inspect

from src.cli import main


def test_run_pipeline_accepts_injection_kwargs():
    sig = inspect.signature(main._run_pipeline)
    assert "market_adapter" in sig.parameters
    assert "sim_clock" in sig.parameters
    # both must be keyword-only with default None
    assert sig.parameters["market_adapter"].default is None
    assert sig.parameters["sim_clock"].default is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_pipeline_injection.py -v`
Expected: FAIL — `assert 'market_adapter' in {...}` (param absent).

- [ ] **Step 3: Implement the injection**

In `algo-trade/src/cli/main.py`, change the `_run_pipeline` signature (line 60):

```python
async def _run_pipeline(
    config: Dict[str, Any],
    mode: str,
    *,
    market_adapter: Any = None,
    sim_clock: Any = None,
) -> None:
```

Change line 61 from `market_adapter = create_market_adapter(config)` to:

```python
    if market_adapter is None:
        market_adapter = create_market_adapter(config)
```

Change the `create_app(...)` call (line 154) to forward the clock:

```python
    app = create_app(risk_manager, _signal_store, position_store, market_adapter, _action_store, broker_adapter, strategy_engine=engine, sim_clock=sim_clock)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd algo-trade && python -m pytest tests/test_pipeline_injection.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add algo-trade/src/cli/main.py algo-trade/tests/test_pipeline_injection.py
git commit -m "feat(sim): allow injecting market adapter + sim clock into pipeline"
```

---

### Task 7: `scripts/simulate.py` entry point

**Files:**
- Create: `algo-trade/scripts/simulate.py`
- Test: `algo-trade/tests/test_simulate_script.py`

**Interfaces:**
- Consumes: `load_day` (Task 3), `SimClock` (Task 1), `ReplayMarketAdapter` (Task 4), `_run_pipeline` (Task 6), `market_hours._HOLIDAYS`.
- Produces: a runnable script + helper `validate_sim_date(s: str) -> datetime.date` raising `SystemExit` for weekends/holidays.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_simulate_script.py`:

```python
# file: tests/test_simulate_script.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import simulate  # noqa: E402


def test_validate_accepts_trading_day():
    d = simulate.validate_sim_date("2026-06-17")  # Wednesday, not a holiday
    assert (d.year, d.month, d.day) == (2026, 6, 17)


def test_validate_rejects_weekend():
    with pytest.raises(SystemExit):
        simulate.validate_sim_date("2026-06-20")  # Saturday


def test_validate_rejects_holiday():
    with pytest.raises(SystemExit):
        simulate.validate_sim_date("2026-06-19")  # Juneteenth (NYSE holiday)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_simulate_script.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'simulate'`.

- [ ] **Step 3: Write minimal implementation**

Create `algo-trade/scripts/simulate.py`:

```python
#!/usr/bin/env python3
# file: scripts/simulate.py
"""
Replay a past trading day through the live pipeline (paper mode) on a
simulated clock. The dashboard shows the day as if live; speed/pause are
controllable from the dashboard topbar.

Usage:
    python scripts/simulate.py                       # 2026-06-17 at 60x
    python scripts/simulate.py --date 2026-06-17 --speed 60
    python scripts/simulate.py --refresh             # re-fetch, ignore cache

Then open the dashboard (http://localhost:8181/) — it will show the
simulated day ticking. Speed/pause controls appear in the topbar.
"""

import argparse
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

# Ensure src is importable when running from project root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cli.main import _run_pipeline
from src.config import load_config
from src.market_adapter.replay_adapter import ReplayMarketAdapter
from src.market_hours import _HOLIDAYS
from src.sim.clock import ET, SimClock
from src.sim.data_loader import load_day


def validate_sim_date(s: str) -> date:
    d = date.fromisoformat(s)
    if d.weekday() >= 5:
        raise SystemExit(f"{s} is a weekend — pick a trading day.")
    if d in _HOLIDAYS:
        raise SystemExit(f"{s} is a NYSE holiday — pick a trading day.")
    return d


async def _run(args: argparse.Namespace) -> None:
    sim_date = validate_sim_date(args.date)
    config_path = Path(args.config)
    config = load_config(config_path if config_path.exists() else None)
    config["mode"] = "paper"
    config.setdefault("screener", {})["market_hours_only"] = False

    print(f"Loading {sim_date} market data (this may take a minute on first run)...")
    data = await load_day(sim_date, force_refresh=args.refresh)
    print(f"Loaded {len(data)} symbols. Starting simulation at {args.speed}x.")

    clock = SimClock(
        datetime(sim_date.year, sim_date.month, sim_date.day, tzinfo=ET),
        speed=args.speed,
    )
    adapter = ReplayMarketAdapter(data, clock)
    await _run_pipeline(config, "paper", market_adapter=adapter, sim_clock=clock)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a past trading day through the live pipeline.")
    parser.add_argument("--date", default="2026-06-17", help="Trading day to replay (YYYY-MM-DD).")
    parser.add_argument("--speed", type=float, default=60.0, help="Initial replay speed multiplier.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument("--refresh", action="store_true", help="Ignore cache and re-fetch from Yahoo.")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nSimulation stopped.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd algo-trade && python -m pytest tests/test_simulate_script.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add algo-trade/scripts/simulate.py algo-trade/tests/test_simulate_script.py
git commit -m "feat(sim): add simulate.py entry point for replay mode"
```

---

### Task 8: Dashboard topbar control

**Files:**
- Modify: `algo-trade/frontend/lib/api.ts`
- Create: `algo-trade/frontend/components/layout/sim-controls.tsx`
- Modify: `algo-trade/frontend/components/layout/topbar.tsx`
- Build: regenerate `algo-trade/web/` export

**Interfaces:**
- Consumes: `GET /sim/status`, `POST /sim/control` (Task 5).
- Produces: `api.simStatus()`, `api.simControl(action, speed?)`, `SimStatus` type; `<SimControls />` rendered in topbar.

- [ ] **Step 1: Add API client methods + type**

In `algo-trade/frontend/lib/api.ts`, add the interface (near the other interfaces):

```ts
export interface SimStatus {
  active: boolean;
  sim_time?: string;
  sim_time_iso?: string;
  speed?: number;
  paused?: boolean;
  sim_date?: string;
  day_complete?: boolean;
  market_open?: boolean;
}
```

Add to the `api` object (after `placeOrder`):

```ts
  simStatus:    ()                        => fetchJSON<SimStatus>("/sim/status"),
  simControl:   (action: "pause" | "resume" | "set_speed", speed?: number) =>
    fetch(`${API_BASE}/sim/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, speed }),
    }).then((r) => r.json() as Promise<SimStatus>),
```

- [ ] **Step 2: Create the SimControls component**

Create `algo-trade/frontend/components/layout/sim-controls.tsx`:

```tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Pause, Play } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type SimStatus } from "@/lib/api";

const SPEEDS: { label: string; value: number }[] = [
  { label: "1x", value: 1 },
  { label: "10x", value: 10 },
  { label: "60x", value: 60 },
  { label: "Max", value: 600 },
];

export function SimControls() {
  const [sim, setSim] = useState<SimStatus | null>(null);

  const poll = useCallback(async () => {
    try {
      const s = await api.simStatus();
      setSim(s.active ? s : null);
    } catch {
      setSim(null);
    }
  }, []);

  useEffect(() => {
    poll();
    const t = setInterval(poll, 1000);
    return () => clearInterval(t);
  }, [poll]);

  if (!sim || !sim.active) return null;

  async function control(action: "pause" | "resume" | "set_speed", speed?: number) {
    try {
      const s = await api.simControl(action, speed);
      setSim(s);
    } catch {
      /* backend unreachable — next poll recovers */
    }
  }

  return (
    <div className="hidden md:flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-indigo-500/10 border border-indigo-500/25">
      <span className="text-[11px] font-medium text-indigo-300 tabular-nums">
        SIM {sim.sim_time ?? ""}
      </span>
      <button
        onClick={() => control(sim.paused ? "resume" : "pause")}
        className="flex items-center justify-center w-6 h-6 rounded-md text-indigo-200 hover:bg-indigo-500/20 transition-colors"
        aria-label={sim.paused ? "Resume" : "Pause"}
      >
        {sim.paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
      </button>
      {SPEEDS.map((s) => (
        <button
          key={s.value}
          onClick={() => control("set_speed", s.value)}
          className={cn(
            "px-1.5 py-0.5 rounded text-[11px] font-medium transition-colors",
            sim.speed === s.value
              ? "bg-indigo-500/30 text-indigo-100"
              : "text-indigo-300/70 hover:bg-indigo-500/20"
          )}
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Render it in the topbar**

In `algo-trade/frontend/components/layout/topbar.tsx`:

Add the import near the top (after line 8):

```tsx
import { SimControls } from "@/components/layout/sim-controls";
```

Render it as the first item in the right-hand controls group — insert `<SimControls />` immediately after the opening `<div className="flex items-center gap-2 shrink-0">` (line 154), before the Demo-mode badge block.

- [ ] **Step 4: Typecheck + build the export**

Run: `cd algo-trade/frontend && npm run build`
Expected: build succeeds, regenerating the static export (the Next config writes the export consumed by the Python server). If the project copies `frontend/out` to `web/`, mirror whatever the existing Docker/build flow does (see `build(docker)` history). Verify there are no TypeScript errors.

- [ ] **Step 5: Manual smoke test**

In one terminal: `cd algo-trade && python scripts/simulate.py --speed 60`
In a browser: open the dashboard. Confirm: topbar shows "Market Open" + a `SIM 2026-06-17 HH:MM:SS ET` chip with Pause + 1x/10x/60x/Max; clicking Pause freezes the sim clock; signals/positions begin to populate as the clock advances.

- [ ] **Step 6: Commit**

```bash
git add algo-trade/frontend/lib/api.ts algo-trade/frontend/components/layout/sim-controls.tsx algo-trade/frontend/components/layout/topbar.tsx algo-trade/web
git commit -m "feat(sim): add live replay speed/pause control to dashboard topbar"
```

---

## Final Verification

- [ ] Run the full Python test suite: `cd algo-trade && python -m pytest -q`
  Expected: all green (new sim tests + existing tests unchanged).
- [ ] Confirm `sim_data/` is gitignored and not tracked: `git status --porcelain sim_data` returns nothing.
- [ ] End-to-end: `python scripts/simulate.py --speed 60` → dashboard shows the June 17 day replaying live with working speed/pause control.

## Self-Review Notes (coverage vs spec)

- SimClock (speed/pause/clamp) → Task 1. ✓
- S&P 500 universe → Task 2. ✓
- Data loader + disk cache + 1m→5m fallback → Task 3. ✓
- ReplayMarketAdapter (no look-ahead, mover ranking) → Task 4. ✓
- Server sim-time display + `/sim/status` + `/sim/control` → Task 5. ✓
- Pipeline injection (replaces spec's factory `provider=="replay"` branch — injection shares the one SimClock instance, which the factory could not) → Task 6. ✓ (intentional, simpler deviation from spec §Components #5)
- `scripts/simulate.py` + date validation → Task 7. ✓
- Frontend topbar control + rebuild → Task 8. ✓
- Edge cases (empty fetch, holiday/weekend, clamp at close) → Tasks 1/3/7. ✓
```
