#!/usr/bin/env python3
# file: scripts/agreement_test.py
"""
Agreement-filter validation: does requiring multiple strategies to agree on
direction actually improve accuracy AND expectancy?

Runs the REAL ALL_STRATEGIES code (via dummy option contracts so each strategy
returns a TradePlan with a direction) over cached daily bars, then buckets the
forward H-day outcome by how many strategies voted the same way on that bar.

Reports, per agreement level:
  - n            : number of bar-signals at that agreement level
  - dir_acc%     : % where the majority direction matched the realised move
  - expectancy%  : average forward return *in the voted direction* (the number
                   that actually decides profitability; > 0 = edge after drift)

If higher agreement does not raise expectancy, the filter does not help and we
will say so instead of shipping it.

Usage:
    python scripts/agreement_test.py --horizon 3
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.events import OptionContract, SignalDirection
from src.strategy_engine.strategies import ALL_STRATEGIES, clear_params_cache

CACHE = Path(__file__).parent.parent / "data" / "history"
WARMUP = 60


def _load(path: Path) -> List[dict]:
    out = []
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                out.append({"datetime": r["datetime"], "open": float(r["open"]),
                            "high": float(r["high"]), "low": float(r["low"]),
                            "close": float(r["close"]), "volume": float(r["volume"])})
            except (KeyError, ValueError):
                pass
    return out


def _dummy_contracts(spot: float) -> List[OptionContract]:
    """One ATM call + put so generate_signal can build a plan and expose direction."""
    mk = lambda t: OptionContract(symbol="X", expiry="2099-01-01", strike=round(spot, 0),
                                  option_type=t, bid=1.0, ask=1.05, volume=1000,
                                  open_interest=5000, implied_volatility=0.3,
                                  delta=0.5, underlying_price=spot)
    return [mk("call"), mk("put")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--max-bars", type=int, default=900, help="cap recent bars/symbol for speed")
    args = ap.parse_args()

    config = load_config(Path("config.yaml"))
    clear_params_cache()

    files = sorted(CACHE.glob("*_1d.csv"))
    if not files:
        print("No cached data. Run scripts/edge_research.py first.")
        return

    # rows: (agreement_count, voted_dir 'CALL'/'PUT', forward_return)
    rows = []
    for f in files:
        bars = _load(f)
        if len(bars) > args.max_bars:
            bars = bars[-args.max_bars:]
        if len(bars) < WARMUP + args.horizon + 1:
            continue
        closes = [b["close"] for b in bars]
        n = len(bars)
        for i in range(WARMUP, n - args.horizon):
            sl = bars[: i + 1]
            contracts = _dummy_contracts(closes[i])
            calls = puts = 0
            for strat in ALL_STRATEGIES:
                try:
                    plan = strat.generate_signal("X", sl, contracts, config)
                except Exception:
                    plan = None
                if plan is None:
                    continue
                if plan.direction == SignalDirection.CALL:
                    calls += 1
                else:
                    puts += 1
            if calls == 0 and puts == 0:
                continue
            if calls == puts:
                continue  # no majority
            voted = "CALL" if calls > puts else "PUT"
            agree = max(calls, puts)
            fwd = closes[i + args.horizon] / closes[i] - 1.0
            rows.append((agree, voted, fwd))

    if not rows:
        print("No signals produced.")
        return

    print(f"\nDaily bars, horizon={args.horizon}d, {len(files)} symbols, {len(rows):,} bar-signals\n")
    print(f"  {'agreement':>10} {'n':>7} {'dir_acc%':>9} {'expectancy%':>12} {'call_share%':>11}")
    print(f"  {'-'*10:>10} {'-'*7:>7} {'-'*9:>9} {'-'*12:>12} {'-'*11:>11}")

    def report(label, subset):
        if not subset:
            print(f"  {label:>10} {0:>7}")
            return
        n = len(subset)
        acc = sum(1 for a, d, fwd in subset
                  if (d == "CALL" and fwd > 0) or (d == "PUT" and fwd < 0)) / n
        exp = sum((fwd if d == "CALL" else -fwd) for a, d, fwd in subset) / n
        call_share = sum(1 for a, d, fwd in subset if d == "CALL") / n
        print(f"  {label:>10} {n:>7} {acc*100:>9.2f} {exp*100:>12.3f} {call_share*100:>11.1f}")

    for k in range(1, 8):
        report(f">={k}", [r for r in rows if r[0] >= k])
    print("\n  (expectancy% = avg forward return in the voted direction; the drift means")
    print("   ~+0.05%/3d is the 'always-CALL' baseline. Beating it = real selection value.)")


if __name__ == "__main__":
    main()
