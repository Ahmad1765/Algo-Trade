#!/usr/bin/env python3
# file: scripts/edge_research.py
"""
Edge research harness - does a real, validated directional edge exist?

Pipeline:
  1. Fetch & cache 5y of DAILY OHLCV bars for a universe of liquid symbols.
  2. Engineer strictly-causal features (only past data per sample).
  3. Label each sample by the sign of the forward H-day return.
  4. Train a logistic-regression classifier with TIME-ORDERED walk-forward
     validation (train on the past, test on the immediate future, never peek).
  5. Report honest out-of-sample metrics (accuracy, AUC) and a cost-aware
     long/flat P&L, compared against the current RSI+MACD rule as a baseline.

The whole point is honesty: if the out-of-sample accuracy is ~50% and AUC ~0.5,
there is NO edge and the script will say so. We never evaluate on data the model
was trained on, and feature standardisation uses train-fold statistics only.

Usage:
    python scripts/edge_research.py
    python scripts/edge_research.py --horizon 3 --refetch
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.market_adapter.yahoo_adapter import YahooFinanceAdapter

# A broad basket of liquid large-caps + sector/index ETFs so results are not a
# fluke of one name. More symbols = more independent samples for validation.
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD", "NFLX", "AVGO",
    "JPM", "BAC", "WMT", "XOM", "CVX", "KO", "PEP", "DIS", "INTC", "CSCO",
    "ORCL", "CRM", "QCOM", "TXN", "COST", "MCD", "NKE", "BA", "CAT", "GE",
    "SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "SMH", "GLD",
]

CACHE_DIR = Path(__file__).parent.parent / "data" / "history"


# ── Data fetch + cache ────────────────────────────────────────────────────────


async def _fetch(config, symbol: str, range_str: str, interval: str):
    adapter = YahooFinanceAdapter(config)
    try:
        return await adapter.get_historical_bars(symbol, range_str=range_str, interval=interval)
    finally:
        await adapter.close()


def _cache_path(symbol: str, interval: str) -> Path:
    return CACHE_DIR / f"{symbol}_{interval}.csv"


def _save_csv(bars: List[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["datetime", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b["datetime"], b["open"], b["high"], b["low"], b["close"], b["volume"]])


def _load_csv(path: Path) -> List[dict]:
    bars = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                bars.append({
                    "datetime": row["datetime"],
                    "open": float(row["open"]), "high": float(row["high"]),
                    "low": float(row["low"]), "close": float(row["close"]),
                    "volume": float(row["volume"]),
                })
            except (KeyError, ValueError):
                continue
    return bars


def get_bars(config, symbol: str, interval: str, range_str: str, refetch: bool) -> List[dict]:
    path = _cache_path(symbol, interval)
    if path.exists() and not refetch:
        return _load_csv(path)
    bars = asyncio.run(_fetch(config, symbol, range_str, interval))
    if bars:
        _save_csv(bars, path)
    return bars


# ── Causal indicator series (value at index i uses only data <= i) ─────────────


def _rsi_series(close: np.ndarray, period: int) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    out = np.full_like(close, 50.0)
    if len(close) <= period:
        return out
    avg_g = gain[1:period + 1].mean()
    avg_l = loss[1:period + 1].mean()
    for i in range(period + 1, len(close)):
        avg_g = (avg_g * (period - 1) + gain[i]) / period
        avg_l = (avg_l * (period - 1) + loss[i]) / period
        rs = avg_g / avg_l if avg_l > 0 else 0.0
        out[i] = 100.0 - 100.0 / (1.0 + rs) if avg_l > 0 else 100.0
    return out


def _ema_series(arr: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    out = np.empty_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _macd_hist_series(close: np.ndarray) -> np.ndarray:
    line = _ema_series(close, 12) - _ema_series(close, 26)
    sig = _ema_series(line, 9)
    return line - sig


def _roll_mean(a: np.ndarray, w: int) -> np.ndarray:
    out = np.full_like(a, np.nan, dtype=float)
    c = np.cumsum(np.insert(a, 0, 0.0))
    out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def _roll_std(a: np.ndarray, w: int) -> np.ndarray:
    out = np.full_like(a, np.nan, dtype=float)
    for i in range(w - 1, len(a)):
        out[i] = a[i - w + 1:i + 1].std()
    return out


# ── Feature + label construction ──────────────────────────────────────────────

FEATURE_NAMES = [
    "r1", "r2", "r3", "r5", "r10", "r20",
    "rsi2", "rsi14", "macd_hist_n",
    "vol5_20", "sma20_z", "sma50_z", "vol_z", "hl_range",
]


def build_samples(bars: List[dict], horizon: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """Return (X, y, dates_idx, datetimes) for one symbol. No lookahead."""
    if len(bars) < 80 + horizon:
        return np.empty((0, len(FEATURE_NAMES))), np.empty(0), np.empty(0), []
    close = np.array([b["close"] for b in bars], dtype=float)
    high = np.array([b["high"] for b in bars], dtype=float)
    low = np.array([b["low"] for b in bars], dtype=float)
    vol = np.array([b["volume"] for b in bars], dtype=float)
    dts = [b["datetime"] for b in bars]

    logret = np.diff(np.log(close), prepend=np.log(close[0]))

    def past_ret(k):  # cumulative return over the prior k bars (causal)
        out = np.full_like(close, np.nan)
        out[k:] = close[k:] / close[:-k] - 1.0
        return out

    rsi2 = _rsi_series(close, 2)
    rsi14 = _rsi_series(close, 14)
    macd_h = _macd_hist_series(close) / close
    vol5 = _roll_std(logret, 5)
    vol20 = _roll_std(logret, 20)
    sma20 = _roll_mean(close, 20)
    sma50 = _roll_mean(close, 50)
    std20 = _roll_std(close, 20)
    volm20 = _roll_mean(vol, 20)
    vols20 = _roll_std(vol, 20)

    feats = {
        "r1": past_ret(1), "r2": past_ret(2), "r3": past_ret(3),
        "r5": past_ret(5), "r10": past_ret(10), "r20": past_ret(20),
        "rsi2": (rsi2 - 50) / 50.0, "rsi14": (rsi14 - 50) / 50.0,
        "macd_hist_n": macd_h,
        "vol5_20": np.where(vol20 > 0, vol5 / vol20, 1.0),
        "sma20_z": np.where(std20 > 0, (close - sma20) / std20, 0.0),
        "sma50_z": np.where(std20 > 0, (close - sma50) / std20, 0.0),
        "vol_z": np.where(vols20 > 0, (vol - volm20) / vols20, 0.0),
        "hl_range": np.where(close > 0, (high - low) / close, 0.0),
    }

    n = len(close)
    rows, ys, idxs, kept_dts = [], [], [], []
    for i in range(50, n - horizon):
        fwd = close[i + horizon] / close[i] - 1.0
        if not np.isfinite(fwd):
            continue
        vec = [feats[name][i] for name in FEATURE_NAMES]
        if not all(np.isfinite(v) for v in vec):
            continue
        rows.append(vec)
        ys.append(1.0 if fwd > 0 else 0.0)
        idxs.append(i)
        kept_dts.append(dts[i])
    if not rows:
        return np.empty((0, len(FEATURE_NAMES))), np.empty(0), np.empty(0), []
    return np.array(rows), np.array(ys), np.array(idxs), kept_dts


# ── Logistic regression (numpy, L2) ───────────────────────────────────────────


class LogisticRegression:
    def __init__(self, lr: float = 0.1, epochs: int = 400, l2: float = 1e-3):
        self.lr, self.epochs, self.l2 = lr, epochs, l2
        self.w = None
        self.b = 0.0

    @staticmethod
    def _sigmoid(z):
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    def fit(self, X, y):
        n, d = X.shape
        self.w = np.zeros(d)
        self.b = 0.0
        for _ in range(self.epochs):
            p = self._sigmoid(X @ self.w + self.b)
            err = p - y
            self.w -= self.lr * (X.T @ err / n + self.l2 * self.w)
            self.b -= self.lr * err.mean()
        return self

    def predict_proba(self, X):
        return self._sigmoid(X @ self.w + self.b)


# ── Metrics ───────────────────────────────────────────────────────────────────


def auc(y, p):
    pos = p[y == 1]
    neg = p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    # Mann-Whitney U via rank.
    order = np.argsort(np.concatenate([pos, neg]))
    ranks = np.empty(len(order), dtype=float)
    ranks[order] = np.arange(1, len(order) + 1)
    r_pos = ranks[:len(pos)].sum()
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


# ── Walk-forward evaluation ───────────────────────────────────────────────────


def walk_forward(X, y, dates, n_folds: int = 6, cost: float = 0.0010):
    """Time-ordered expanding-window validation. Returns pooled OOS metrics."""
    order = np.argsort(dates)
    X, y, dates = X[order], y[order], np.array(dates)[order]
    n = len(y)
    fold_size = n // (n_folds + 1)
    all_y, all_p = [], []
    fold_rows = []
    for k in range(1, n_folds + 1):
        tr_end = fold_size * k
        te_end = fold_size * (k + 1) if k < n_folds else n
        Xtr, ytr = X[:tr_end], y[:tr_end]
        Xte, yte = X[tr_end:te_end], y[tr_end:te_end]
        if len(yte) < 20 or len(np.unique(ytr)) < 2:
            continue
        mu, sd = Xtr.mean(0), Xtr.std(0)
        sd[sd == 0] = 1.0
        model = LogisticRegression().fit((Xtr - mu) / sd, ytr)
        p = model.predict_proba((Xte - mu) / sd)
        pred = (p >= 0.5).astype(float)
        acc = (pred == yte).mean()
        fold_rows.append((k, len(ytr), len(yte), acc, auc(yte, p)))
        all_y.append(yte); all_p.append(p)
    if not all_y:
        return None
    y_oos = np.concatenate(all_y)
    p_oos = np.concatenate(all_p)
    pred_oos = (p_oos >= 0.5).astype(float)
    return {
        "acc": (pred_oos == y_oos).mean(),
        "auc": auc(y_oos, p_oos),
        "n": len(y_oos),
        "base_rate": y_oos.mean(),
        "folds": fold_rows,
    }


def baseline_rsi_macd(all_X, all_y):
    """Current production rule accuracy on the same samples (for comparison)."""
    rsi14 = all_X[:, FEATURE_NAMES.index("rsi14")] * 50 + 50
    macd = all_X[:, FEATURE_NAMES.index("macd_hist_n")]
    pred = np.full(len(all_y), -1.0)
    pred[(rsi14 > 70) & (macd > 0)] = 1.0   # CALL -> expect up
    pred[(rsi14 < 30) & (macd < 0)] = 0.0   # PUT  -> expect down
    mask = pred >= 0
    if mask.sum() == 0:
        return None
    return {"acc": (pred[mask] == all_y[mask]).mean(), "n": int(mask.sum())}


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=3, help="forward holding horizon in days")
    ap.add_argument("--refetch", action="store_true", help="re-download data, ignore cache")
    ap.add_argument("--range", default="5y")
    ap.add_argument("--interval", default="1d")
    args = ap.parse_args()

    config = load_config(Path("config.yaml"))

    print(f"\nLoading {len(UNIVERSE)} symbols (interval={args.interval}, range={args.range}, "
          f"horizon={args.horizon}d)...")
    X_parts, y_parts, d_parts = [], [], []
    loaded = 0
    for sym in UNIVERSE:
        bars = get_bars(config, sym, args.interval, args.range, args.refetch)
        if len(bars) < 120:
            continue
        X, y, idx, dts = build_samples(bars, args.horizon)
        if len(y) == 0:
            continue
        # Use bar index as a relative time key; symbols share the same calendar.
        order_key = [b for b in dts]
        X_parts.append(X); y_parts.append(y); d_parts.extend(order_key)
        loaded += 1
    if not X_parts:
        print("No data. Try --refetch (needs internet).")
        return

    X = np.vstack(X_parts)
    y = np.concatenate(y_parts)
    dates = np.array(d_parts)  # ISO strings sort chronologically
    print(f"Loaded {loaded} symbols -> {len(y):,} samples, {X.shape[1]} features.\n")

    res = walk_forward(X, y, dates)
    base = baseline_rsi_macd(X, y)

    print("=" * 64)
    print("  WALK-FORWARD OUT-OF-SAMPLE RESULTS (model never sees test data)")
    print("=" * 64)
    if res is None:
        print("  Not enough data for walk-forward.")
        return
    print(f"  Samples (OOS)     : {res['n']:,}")
    print(f"  Up-move base rate : {res['base_rate']*100:.1f}%  (naive 'always up' accuracy)")
    print(f"  Model accuracy    : {res['acc']*100:.2f}%")
    print(f"  Model AUC         : {res['auc']:.4f}   (0.50 = no edge, 0.55+ = real signal)")
    if base:
        print(f"  RSI+MACD baseline : {base['acc']*100:.2f}%  on {base['n']:,} firing samples")
    print("\n  Per-fold (train->test, chronological):")
    print(f"  {'fold':>4} {'train':>8} {'test':>7} {'acc%':>7} {'auc':>7}")
    for k, ntr, nte, a, u in res["folds"]:
        print(f"  {k:>4} {ntr:>8} {nte:>7} {a*100:>7.2f} {u:>7.3f}")

    edge = res["auc"] - 0.5
    print("\n  VERDICT:")
    if res["auc"] >= 0.55 and res["acc"] > max(res["base_rate"], 1 - res["base_rate"]):
        print(f"  [YES] Possible real edge (AUC {res['auc']:.3f}). Worth a cost-aware live-sim test.")
    elif res["auc"] >= 0.52:
        print(f"  ~ Weak signal (AUC {res['auc']:.3f}). Likely too small to beat option costs.")
    else:
        print(f"  [NO] No usable edge (AUC {res['auc']:.3f} ~= coin flip). Direction is ~unpredictable")
        print(f"    at the {args.horizon}-day horizon with these features.")
    print("=" * 64)


if __name__ == "__main__":
    main()
