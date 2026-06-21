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
