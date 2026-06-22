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

Then open the dashboard (http://localhost:8181/) -- it will show the
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
from src.sim.calendar import validate_sim_date as _validate
from src.sim.clock import ET, SimClock
from src.sim.data_loader import load_day


def validate_sim_date(s: str) -> date:
    try:
        return _validate(s)
    except ValueError as exc:
        raise SystemExit(f"{exc} — pick a trading day.")


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
