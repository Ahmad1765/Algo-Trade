# file: src/strategy_engine/engine.py
"""
CEP-style strategy engine.

Production features added:
  - Per-ticker signal cooldown (configurable, default 30 min)
  - Notifier integration for signal alerts
  - Persistence integration to record cooldowns
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from src.events import (
    OptionChainEvent,
    OptionContract,
    SignalDirection,
    SignalEvent,
    TradePlan,
)
from src.indicators import atr, macd, rsi
from src.logger import get_logger
from src.market_adapter.base import MarketDataAdapter

log = get_logger(__name__)


def _select_contract(
    contracts: List[OptionContract],
    direction: SignalDirection,
) -> Optional[OptionContract]:
    opt_type = "call" if direction == SignalDirection.CALL else "put"
    candidates = [c for c in contracts if c.option_type == opt_type]
    if not candidates:
        return None
    candidates.sort(key=lambda c: (abs(c.strike - c.underlying_price), -c.volume))
    return candidates[0]


class StrategyEngine:
    def __init__(
        self,
        market_adapter: MarketDataAdapter,
        chain_queue: "asyncio.Queue[OptionChainEvent]",
        signal_queue: "asyncio.Queue[SignalEvent]",
        config: Dict[str, Any],
        position_store=None,
        notifier=None,
        tap_queue: "Optional[asyncio.Queue[SignalEvent]]" = None,
    ) -> None:
        self._market = market_adapter
        self._chain_queue = chain_queue
        self._signal_queue = signal_queue
        self._tap_queue = tap_queue
        self._position_store = position_store
        self._notifier = notifier

        ind = config.get("indicators", {})
        self._rsi_period: int = int(ind.get("rsi_period", 14))
        self._rsi_ob: float = float(ind.get("rsi_overbought", 70))
        self._rsi_os: float = float(ind.get("rsi_oversold", 30))
        self._macd_fast: int = int(ind.get("macd_fast", 12))
        self._macd_slow: int = int(ind.get("macd_slow", 26))
        self._macd_sig: int = int(ind.get("macd_signal", 9))
        self._atr_period: int = int(ind.get("atr_period", 14))
        self._lookback: int = int(ind.get("lookback_bars", 50))
        self._cooldown_min: int = int(ind.get("signal_cooldown_minutes", 30))

        risk = config.get("risk", {})
        self._sl_mult: float = float(risk.get("stop_loss_atr_mult", 1.5))
        self._tp_mult: float = float(risk.get("take_profit_atr_mult", 1.5))
        # Option-premium exit thresholds (fraction of entry). Symmetric by default.
        self._opt_sl_pct: float = float(risk.get("option_stop_loss_pct", 0.30))
        self._opt_tp_pct: float = float(risk.get("option_take_profit_pct", 0.30))

    async def _compute_indicators(self, symbol: str):
        bars = await self._market.get_intraday_bars(
            symbol, interval="1min", limit=self._lookback + 10
        )
        required = max(
            self._rsi_period + 1,
            self._macd_slow + self._macd_sig,
            self._atr_period + 1,
        )
        if len(bars) < required:
            raise ValueError(f"Insufficient bars for {symbol}: {len(bars)} < {required}")

        closes = [b["close"] for b in bars]
        highs  = [b["high"]  for b in bars]
        lows   = [b["low"]   for b in bars]

        rsi_val  = rsi(closes, self._rsi_period)
        macd_res = macd(closes, self._macd_fast, self._macd_slow, self._macd_sig)
        atr_val  = atr(highs, lows, closes, self._atr_period)
        return rsi_val, macd_res, atr_val

    def _determine_direction(self, rsi_val: float, macd_hist: float) -> Optional[SignalDirection]:
        if rsi_val > self._rsi_ob and macd_hist > 0:
            return SignalDirection.CALL
        if rsi_val < self._rsi_os and macd_hist < 0:
            return SignalDirection.PUT
        return None

    def _build_trade_plan(
        self,
        symbol: str,
        direction: SignalDirection,
        contract: OptionContract,
        atr_val: float,
        rsi_val: float,
        macd_hist: float,
    ) -> TradePlan:
        entry = round(contract.ask * 1.01, 2)
        # We are LONG the option. Profit/loss is measured on option price, not
        # the underlying. ATR of the underlying cannot be applied to option premium —
        # use a fixed % of entry price instead (same for both directions).
        stop = round(entry * (1 - self._opt_sl_pct), 2)
        tp   = round(entry * (1 + self._opt_tp_pct), 2)

        rationale = (
            f"RSI={rsi_val:.1f}, MACD_hist={macd_hist:.4f}, "
            f"ATR={atr_val:.2f}, strike={contract.strike}, expiry={contract.expiry}"
        )
        return TradePlan(
            symbol=symbol,
            direction=direction,
            contract=contract,
            entry_limit=entry,
            stop_loss=stop,
            take_profit=tp,
            rsi=rsi_val,
            macd_hist=macd_hist,
            rationale=rationale,
        )

    async def _process_chain(self, event: OptionChainEvent) -> None:
        symbol = event.symbol

        # Skip if ticker is on cooldown.
        if self._position_store and self._position_store.is_on_cooldown(symbol, self._cooldown_min):
            log.debug("signal cooldown active", symbol=symbol)
            return

        # Skip if already holding a position in this ticker.
        if self._position_store and symbol in self._position_store.symbols():
            log.debug("already in position", symbol=symbol)
            return

        try:
            rsi_val, macd_res, atr_val = await self._compute_indicators(symbol)
        except ValueError as exc:
            log.debug("indicator skip", symbol=symbol, reason=str(exc))
            return
        except Exception as exc:
            log.error("indicator error", symbol=symbol, error=str(exc))
            return

        direction = self._determine_direction(rsi_val, macd_res.histogram)
        if direction is None:
            log.debug("no signal", symbol=symbol, rsi=round(rsi_val, 1))
            return

        contract = _select_contract(event.contracts, direction)
        if contract is None:
            return

        plan = self._build_trade_plan(symbol, direction, contract, atr_val, rsi_val, macd_res.histogram)
        signal = SignalEvent(trade_plan=plan)
        await self._signal_queue.put(signal)
        if self._tap_queue is not None:
            try:
                self._tap_queue.put_nowait(signal)
            except asyncio.QueueFull:
                log.debug("tap_queue full — signal observation skipped", symbol=symbol)

        # Record cooldown and send alert.
        if self._position_store:
            self._position_store.set_cooldown(symbol)
        if self._notifier:
            asyncio.ensure_future(self._notifier.signal(
                symbol=symbol,
                direction=direction.value,
                strike=contract.strike,
                expiry=contract.expiry,
                entry=plan.entry_limit,
                stop=plan.stop_loss,
                target=plan.take_profit,
                rationale=plan.rationale,
            ))

        log.info(
            "SIGNAL GENERATED",
            symbol=symbol,
            direction=direction.value,
            strike=contract.strike,
            expiry=contract.expiry,
            entry=plan.entry_limit,
            stop=plan.stop_loss,
            target=plan.take_profit,
            rsi=round(rsi_val, 1),
            macd_hist=round(macd_res.histogram, 4),
        )

    async def run(self) -> None:
        log.info("strategy_engine started")
        while True:
            try:
                event: OptionChainEvent = await self._chain_queue.get()
                await self._process_chain(event)
            except asyncio.CancelledError:
                log.info("strategy_engine cancelled")
                return
            except Exception as exc:
                log.error("strategy_engine error", error=str(exc))
