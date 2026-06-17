# file: src/strategy_engine/strategies.py
"""
10 trading strategies implementing BaseStrategy.

Each strategy independently evaluates market data and returns an Optional[TradePlan].
Helper indicator functions are defined inline to avoid extra module dependencies.
"""

from __future__ import annotations

import abc
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

import numpy as np

from src.events import OptionContract, SignalDirection, TradePlan
from src.indicators import atr, macd, rsi


# ── Cached config parsing ────────────────────────────────────────────────────


class _IndParams(NamedTuple):
    rsi_period: int
    rsi_overbought: float
    rsi_oversold: float
    macd_fast: int
    macd_slow: int
    macd_signal: int
    atr_period: int
    volume_confirm_mult: float


class _RiskParams(NamedTuple):
    sl_mult: float
    tp_mult: float


_params_cache: Dict[int, Tuple[_IndParams, _RiskParams]] = {}


def clear_params_cache() -> None:
    """Invalidate the params cache — call after config mutation."""
    _params_cache.clear()


def _get_params(config: Dict[str, Any]) -> Tuple[_IndParams, _RiskParams]:
    """Parse indicator/risk config once per config object, then serve from cache.

    All 10 strategies share the same config reference per chain event, so
    parsing only happens once instead of 10 times.
    """
    cid = id(config)
    if cid not in _params_cache:
        ind = config.get("indicators", {})
        risk = config.get("risk", {})
        _params_cache[cid] = (
            _IndParams(
                rsi_period=int(ind.get("rsi_period", 14)),
                rsi_overbought=float(ind.get("rsi_overbought", 70)),
                rsi_oversold=float(ind.get("rsi_oversold", 30)),
                macd_fast=int(ind.get("macd_fast", 12)),
                macd_slow=int(ind.get("macd_slow", 26)),
                macd_signal=int(ind.get("macd_signal", 9)),
                atr_period=int(ind.get("atr_period", 14)),
                volume_confirm_mult=float(ind.get("volume_confirm_mult", 1.2)),
            ),
            _RiskParams(
                sl_mult=float(risk.get("stop_loss_atr_mult", 1.5)),
                tp_mult=float(risk.get("take_profit_atr_mult", 3.0)),
            ),
        )
        # Evict old entries to prevent unbounded growth after config reloads.
        if len(_params_cache) > 8:
            oldest = next(iter(_params_cache))
            del _params_cache[oldest]
    return _params_cache[cid]


# ── Inline indicator helpers ─────────────────────────────────────────────────


def _sma(closes: Sequence[float], period: int) -> float:
    arr = np.asarray(closes[-period:], dtype=float)
    return float(arr.mean())


def _ema_series(arr: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1)
    out = np.empty_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
    return out


def _bollinger(closes: Sequence[float], period: int = 20, num_std: float = 2.0):
    """Return (upper, mid, lower) Bollinger Bands for the last bar."""
    arr = np.asarray(closes[-period:], dtype=float)
    mid = float(arr.mean())
    std = float(arr.std())
    return mid + num_std * std, mid, mid - num_std * std


def _vwap(bars: List[Dict]) -> float:
    """Volume-weighted average price across all provided bars."""
    num = sum((b["high"] + b["low"] + b["close"]) / 3.0 * b.get("volume", 1) for b in bars)
    den = sum(b.get("volume", 1) for b in bars)
    return num / den if den else 0.0


def _volume_confirmed(bars: List[Dict], lookback: int = 20, mult: float = 1.2) -> bool:
    """Return True if the last bar's volume is at least mult × average of prior bars.

    Skips the check when the last bar has zero volume (market closed / weekend data).
    Returns True when there is insufficient history to compute a baseline.
    """
    if len(bars) < lookback + 1:
        return True
    last_vol = bars[-1].get("volume", 0)
    if last_vol == 0:
        # Market is closed or the bar has no volume — use second-to-last bar.
        prior_bars = bars[-lookback - 2:-2]
        last_vol = bars[-2].get("volume", 0) if len(bars) >= 2 else 0
    else:
        prior_bars = bars[-lookback - 1:-1]
    prior_vols = [b.get("volume", 0) for b in prior_bars]
    avg = sum(prior_vols) / len(prior_vols) if prior_vols else 0
    if avg == 0:
        return True
    return last_vol >= mult * avg


def _select_contract(
    contracts: List[OptionContract], direction: SignalDirection
) -> Optional[OptionContract]:
    opt_type = "call" if direction == SignalDirection.CALL else "put"
    candidates = [c for c in contracts if c.option_type == opt_type]
    if not candidates:
        return None
    candidates.sort(key=lambda c: (abs(c.strike - c.underlying_price), -c.volume))
    return candidates[0]


def _build_plan(
    name: str,
    symbol: str,
    direction: SignalDirection,
    contract: OptionContract,
    atr_val: float,
    sl_mult: float,
    tp_mult: float,
    rationale: str,
) -> TradePlan:
    entry = round(contract.ask * 1.01, 2)
    # We are LONG the option (buying calls or puts).
    # Profit = option price rises. Loss = option price drops.
    # This is true for both directions — stop and TP are symmetric.
    sl_pct = 0.50  # exit if option loses 50% of entry value
    tp_pct = 1.00  # exit if option doubles (2:1 R:R)
    stop = round(entry * (1 - sl_pct), 2)
    tp   = round(entry * (1 + tp_pct), 2)
    return TradePlan(
        symbol=symbol,
        direction=direction,
        contract=contract,
        entry_limit=entry,
        stop_loss=stop,
        take_profit=tp,
        rationale=rationale,
        strategy_name=name,
    )


# ── Base class ───────────────────────────────────────────────────────────────


class BaseStrategy(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def generate_signal(
        self,
        symbol: str,
        bars: List[Dict[str, Any]],
        contracts: List[OptionContract],
        config: Dict[str, Any],
    ) -> Optional[TradePlan]:
        """Evaluate bars + contracts; return a TradePlan or None."""


# ── Strategy 1: RSI + MACD ───────────────────────────────────────────────────


class RSIMACDStrategy(BaseStrategy):
    name = "RSIMACD"

    def generate_signal(self, symbol, bars, contracts, config):
        ind, risk = _get_params(config)

        closes = [b["close"] for b in bars]
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]
        required = max(ind.rsi_period + 1, ind.macd_slow + ind.macd_signal, ind.atr_period + 1)
        if len(closes) < required:
            return None

        if not _volume_confirmed(bars, mult=ind.volume_confirm_mult):
            return None

        rsi_val  = rsi(closes, ind.rsi_period)
        macd_res = macd(closes, ind.macd_fast, ind.macd_slow, ind.macd_signal)
        atr_val  = atr(highs, lows, closes, ind.atr_period)

        if rsi_val > ind.rsi_overbought and macd_res.histogram > 0:
            direction = SignalDirection.CALL
        elif rsi_val < ind.rsi_oversold and macd_res.histogram < 0:
            direction = SignalDirection.PUT
        else:
            return None

        contract = _select_contract(contracts, direction)
        if contract is None:
            return None
        rationale = f"[{self.name}] RSI={rsi_val:.1f}, MACD_hist={macd_res.histogram:.4f}"
        return _build_plan(self.name, symbol, direction, contract, atr_val, risk.sl_mult, risk.tp_mult, rationale)


# ── Strategy 2: EMA Crossover ─────────────────────────────────────────────────


class EMACrossStrategy(BaseStrategy):
    name = "EMACross"

    def generate_signal(self, symbol, bars, contracts, config):
        ind, risk = _get_params(config)

        closes = np.asarray([b["close"] for b in bars], dtype=float)
        highs  = [b["high"] for b in bars]
        lows   = [b["low"]  for b in bars]
        if len(closes) < max(22, ind.atr_period + 1):
            return None

        if not _volume_confirmed(bars, mult=ind.volume_confirm_mult):
            return None

        ema9  = _ema_series(closes, 9)
        ema21 = _ema_series(closes, 21)

        if ema9[-2] < ema21[-2] and ema9[-1] > ema21[-1]:
            direction = SignalDirection.CALL
        elif ema9[-2] > ema21[-2] and ema9[-1] < ema21[-1]:
            direction = SignalDirection.PUT
        else:
            return None

        atr_val  = atr(highs, lows, list(closes), ind.atr_period)
        contract = _select_contract(contracts, direction)
        if contract is None:
            return None
        rationale = f"[{self.name}] EMA9={ema9[-1]:.2f} crossed EMA21={ema21[-1]:.2f}"
        return _build_plan(self.name, symbol, direction, contract, atr_val, risk.sl_mult, risk.tp_mult, rationale)


# ── Strategy 3: Bollinger Band Breakout ───────────────────────────────────────


class BollingerBandBreakoutStrategy(BaseStrategy):
    name = "BollingerBreakout"

    def generate_signal(self, symbol, bars, contracts, config):
        ind, risk = _get_params(config)

        closes = [b["close"] for b in bars]
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]
        if len(closes) < max(21, ind.atr_period + 1):
            return None

        if not _volume_confirmed(bars, mult=ind.volume_confirm_mult):
            return None

        upper, _mid, lower = _bollinger(closes, period=20)
        last_close = closes[-1]
        prev_close = closes[-2]

        # Fade the breakout: price above upper band → likely overextended → PUT
        # Price below lower band → likely oversold → CALL
        if prev_close <= upper and last_close > upper:
            direction = SignalDirection.PUT
        elif prev_close >= lower and last_close < lower:
            direction = SignalDirection.CALL
        else:
            return None

        atr_val  = atr(highs, lows, closes, ind.atr_period)
        contract = _select_contract(contracts, direction)
        if contract is None:
            return None
        rationale = (f"[{self.name}] price={last_close:.2f} "
                     f"broke band upper={upper:.2f} lower={lower:.2f}")
        return _build_plan(self.name, symbol, direction, contract, atr_val, risk.sl_mult, risk.tp_mult, rationale)


# ── Strategy 4: Momentum ──────────────────────────────────────────────────────


class MomentumStrategy(BaseStrategy):
    name = "Momentum"
    _LOOKBACK = 5
    _THRESHOLD_PCT = 0.015  # 1.5% — raised from 0.5% to reduce noise signals

    def generate_signal(self, symbol, bars, contracts, config):
        ind, risk = _get_params(config)

        closes = [b["close"] for b in bars]
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]
        if len(closes) < self._LOOKBACK + ind.atr_period + 1:
            return None

        if not _volume_confirmed(bars, mult=ind.volume_confirm_mult):
            return None

        change = (closes[-1] - closes[-self._LOOKBACK]) / closes[-self._LOOKBACK]
        if change > self._THRESHOLD_PCT:
            direction = SignalDirection.CALL
        elif change < -self._THRESHOLD_PCT:
            direction = SignalDirection.PUT
        else:
            return None

        atr_val  = atr(highs, lows, closes, ind.atr_period)
        contract = _select_contract(contracts, direction)
        if contract is None:
            return None
        rationale = f"[{self.name}] {self._LOOKBACK}-bar change={change*100:.2f}%"
        return _build_plan(self.name, symbol, direction, contract, atr_val, risk.sl_mult, risk.tp_mult, rationale)


# ── Strategy 5: Mean Reversion ────────────────────────────────────────────────


class MeanReversionStrategy(BaseStrategy):
    name = "MeanReversion"

    def generate_signal(self, symbol, bars, contracts, config):
        ind, risk = _get_params(config)

        closes = [b["close"] for b in bars]
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]
        if len(closes) < max(22, ind.atr_period + 1):
            return None

        arr = np.asarray(closes[-20:], dtype=float)
        sma = float(arr.mean())
        std = float(arr.std())
        if std == 0:
            return None

        if not _volume_confirmed(bars, mult=ind.volume_confirm_mult):
            return None

        z = (closes[-1] - sma) / std
        # Price far above mean → expect drop → PUT; far below → CALL
        if z > 2.0:
            direction = SignalDirection.PUT
        elif z < -2.0:
            direction = SignalDirection.CALL
        else:
            return None

        atr_val  = atr(highs, lows, closes, ind.atr_period)
        contract = _select_contract(contracts, direction)
        if contract is None:
            return None
        rationale = f"[{self.name}] z-score={z:.2f}, SMA20={sma:.2f}"
        return _build_plan(self.name, symbol, direction, contract, atr_val, risk.sl_mult, risk.tp_mult, rationale)


# ── Strategy 6: VWAP Deviation ────────────────────────────────────────────────


class VWAPStrategy(BaseStrategy):
    name = "VWAP"
    _THRESHOLD_PCT = 0.008  # 0.8% from VWAP — raised from 0.3% to reduce noise signals

    def generate_signal(self, symbol, bars, contracts, config):
        ind, risk = _get_params(config)

        closes = [b["close"] for b in bars]
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]
        if len(closes) < ind.atr_period + 1:
            return None

        if not _volume_confirmed(bars, mult=ind.volume_confirm_mult):
            return None

        vwap_val = _vwap(bars)
        if vwap_val == 0:
            return None
        deviation = (closes[-1] - vwap_val) / vwap_val

        if deviation > self._THRESHOLD_PCT:
            direction = SignalDirection.CALL
        elif deviation < -self._THRESHOLD_PCT:
            direction = SignalDirection.PUT
        else:
            return None

        atr_val  = atr(highs, lows, closes, ind.atr_period)
        contract = _select_contract(contracts, direction)
        if contract is None:
            return None
        rationale = f"[{self.name}] VWAP={vwap_val:.2f}, deviation={deviation*100:.2f}%"
        return _build_plan(self.name, symbol, direction, contract, atr_val, risk.sl_mult, risk.tp_mult, rationale)


# ── Strategy 7: RSI Aggressive ───────────────────────────────────────────────


class RSIAggressiveStrategy(BaseStrategy):
    name = "RSIAggressive"

    def generate_signal(self, symbol, bars, contracts, config):
        ind, risk = _get_params(config)

        closes = [b["close"] for b in bars]
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]
        if len(closes) < max(ind.rsi_period + 1, ind.atr_period + 1):
            return None

        if not _volume_confirmed(bars, mult=ind.volume_confirm_mult):
            return None

        rsi_val = rsi(closes, ind.rsi_period)
        if rsi_val > 80:
            direction = SignalDirection.CALL
        elif rsi_val < 20:
            direction = SignalDirection.PUT
        else:
            return None

        atr_val  = atr(highs, lows, closes, ind.atr_period)
        contract = _select_contract(contracts, direction)
        if contract is None:
            return None
        rationale = f"[{self.name}] RSI={rsi_val:.1f} (thresholds 80/20)"
        return _build_plan(self.name, symbol, direction, contract, atr_val, risk.sl_mult, risk.tp_mult, rationale)


# ── Strategy 8: Trend Following ───────────────────────────────────────────────


class TrendFollowingStrategy(BaseStrategy):
    name = "TrendFollowing"

    def generate_signal(self, symbol, bars, contracts, config):
        ind, risk = _get_params(config)

        closes = [b["close"] for b in bars]
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]
        if len(closes) < max(51, ind.rsi_period + 1, ind.atr_period + 1):
            return None

        if not _volume_confirmed(bars, mult=ind.volume_confirm_mult):
            return None

        sma20   = _sma(closes, 20)
        sma50   = _sma(closes, 50)
        rsi_val = rsi(closes, ind.rsi_period)

        if sma20 > sma50 and rsi_val > 50:
            direction = SignalDirection.CALL
        elif sma20 < sma50 and rsi_val < 50:
            direction = SignalDirection.PUT
        else:
            return None

        atr_val  = atr(highs, lows, closes, ind.atr_period)
        contract = _select_contract(contracts, direction)
        if contract is None:
            return None
        rationale = (f"[{self.name}] SMA20={sma20:.2f} vs SMA50={sma50:.2f}, "
                     f"RSI={rsi_val:.1f}")
        return _build_plan(self.name, symbol, direction, contract, atr_val, risk.sl_mult, risk.tp_mult, rationale)


# ── Strategy 9: Volatility Breakout ──────────────────────────────────────────


class VolatilityBreakoutStrategy(BaseStrategy):
    name = "VolatilityBreakout"
    _ATR_MULT = 2.0

    def generate_signal(self, symbol, bars, contracts, config):
        ind, risk = _get_params(config)

        closes = [b["close"] for b in bars]
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]
        if len(closes) < ind.atr_period + 2:
            return None

        if not _volume_confirmed(bars, mult=ind.volume_confirm_mult):
            return None

        atr_val    = atr(highs, lows, closes, ind.atr_period)
        last_range = highs[-1] - lows[-1]
        last_move  = closes[-1] - closes[-2]

        if last_range < self._ATR_MULT * atr_val:
            return None

        direction = SignalDirection.CALL if last_move > 0 else SignalDirection.PUT
        contract  = _select_contract(contracts, direction)
        if contract is None:
            return None
        rationale = (f"[{self.name}] range={last_range:.2f} "
                     f"> {self._ATR_MULT}×ATR={atr_val:.2f}")
        return _build_plan(self.name, symbol, direction, contract, atr_val, risk.sl_mult, risk.tp_mult, rationale)


# ── Strategy 10: MACD Line Crossover ─────────────────────────────────────────


class MACDCrossStrategy(BaseStrategy):
    name = "MACDCross"

    def generate_signal(self, symbol, bars, contracts, config):
        ind, risk = _get_params(config)

        closes = np.asarray([b["close"] for b in bars], dtype=float)
        highs  = [b["high"] for b in bars]
        lows   = [b["low"]  for b in bars]
        required = ind.macd_slow + ind.macd_signal + 1
        if len(closes) < max(required, ind.atr_period + 1):
            return None

        if not _volume_confirmed(bars, mult=ind.volume_confirm_mult):
            return None

        fast_ema  = _ema_series(closes, ind.macd_fast)
        slow_ema  = _ema_series(closes, ind.macd_slow)
        macd_line = fast_ema - slow_ema
        sig_line  = _ema_series(macd_line, ind.macd_signal)

        if macd_line[-2] < sig_line[-2] and macd_line[-1] > sig_line[-1]:
            direction = SignalDirection.CALL
        elif macd_line[-2] > sig_line[-2] and macd_line[-1] < sig_line[-1]:
            direction = SignalDirection.PUT
        else:
            return None

        atr_val  = atr(highs, lows, list(closes), ind.atr_period)
        contract = _select_contract(contracts, direction)
        if contract is None:
            return None
        rationale = (f"[{self.name}] MACD={macd_line[-1]:.4f} "
                     f"crossed signal={sig_line[-1]:.4f}")
        return _build_plan(self.name, symbol, direction, contract, atr_val, risk.sl_mult, risk.tp_mult, rationale)


# ── Registry ──────────────────────────────────────────────────────────────────

ALL_STRATEGIES: List[BaseStrategy] = [
    RSIMACDStrategy(),
    EMACrossStrategy(),
    BollingerBandBreakoutStrategy(),
    MomentumStrategy(),
    MeanReversionStrategy(),
    VWAPStrategy(),
    RSIAggressiveStrategy(),
    TrendFollowingStrategy(),
    VolatilityBreakoutStrategy(),
    MACDCrossStrategy(),
]
