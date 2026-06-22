#!/usr/bin/env python3
# file: scripts/fetch_history.py
"""
Fetch historical bars from Yahoo Finance and backtest on them.

Works while the market is closed — Yahoo serves past trading days for free,
no API key required.

Usage:
    # Backtest 5 days of 1-minute AAPL bars
    python scripts/fetch_history.py AAPL --range 5d --interval 1m

    # Backtest 6 months of daily TSLA bars
    python scripts/fetch_history.py TSLA --range 6mo --interval 1d

    # Save the fetched bars to CSV as well (reusable with backtest.py)
    python scripts/fetch_history.py NVDA --range 5d --interval 1m --save-csv nvda_5d.csv

Yahoo Finance range/interval limits:
    1m              : last ~7 days only
    2m/5m/15m/30m   : last ~60 days
    60m/1h          : last ~730 days
    1d/1wk/1mo      : years

Valid ranges:    1d 5d 1mo 3mo 6mo 1y 2y 5y 10y ytd max
Valid intervals: 1m 2m 5m 15m 30m 60m 1h 1d 1wk 1mo
"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

# Ensure src is importable when running from project root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtester import Backtester
from src.config import load_config
from src.market_adapter.yahoo_adapter import YahooFinanceAdapter


async def _fetch_bars(config, symbol, range_str, interval):
    """Fetch historical bars from Yahoo; always closes the HTTP session."""
    adapter = YahooFinanceAdapter(config)
    try:
        return await adapter.get_historical_bars(symbol, range_str=range_str, interval=interval)
    finally:
        await adapter.close()


def _save_csv(bars, path):
    """Write bars to a CSV the standard backtester can re-read."""
    with Path(path).open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["datetime", "open", "high", "low", "close", "volume"])
        for b in bars:
            writer.writerow([b["datetime"], b["open"], b["high"], b["low"], b["close"], b["volume"]])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch historical bars from Yahoo Finance and backtest on them.",
    )
    parser.add_argument("symbol", help="Ticker symbol, e.g. AAPL.")
    parser.add_argument("--range", default="5d", dest="range_str",
                        help="History range: 1d 5d 1mo 3mo 6mo 1y 2y 5y (default: 5d).")
    parser.add_argument("--interval", default="1m",
                        help="Bar interval: 1m 5m 15m 30m 60m 1d 1wk (default: 1m).")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument("--save-csv", default=None, dest="save_csv",
                        help="Optional path to also write the fetched bars as CSV.")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_config(config_path if config_path.exists() else None)

    print(f"\nFetching {args.symbol} | range={args.range_str} | interval={args.interval} from Yahoo Finance...")
    bars = asyncio.run(_fetch_bars(config, args.symbol.upper(), args.range_str, args.interval))

    if not bars:
        print("Error: no bars returned. Check the symbol, or the range/interval limits "
              "(e.g. 1m history is only available for ~7 days).")
        sys.exit(1)

    print(f"Fetched {len(bars)} bars: {bars[0]['datetime']} -> {bars[-1]['datetime']}")

    if args.save_csv:
        _save_csv(bars, args.save_csv)
        print(f"Saved bars to: {args.save_csv}")

    bt = Backtester(config)
    try:
        result = bt.run_from_bars(bars)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    result.print_report()


if __name__ == "__main__":
    main()
