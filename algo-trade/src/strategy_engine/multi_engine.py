# file: src/strategy_engine/multi_engine.py
"""
MultiStrategyEngine — runs all 10 strategies in parallel per symbol.

Selection logic:
  1. Score each strategy: 0.6 * win_rate + 0.4 * normalised_pnl
  2. No history yet → equal scores → round-robin tiebreak
  3. Highest-scoring strategy that produced a signal wins

Signal confirmation:
  - A signal must be seen on `confirmation.wait_bars` consecutive chain events
    before it is published to the execution queue.
  - Prevents acting on single-bar noise / overconfident one-off signals.

Daily circuit breaker:
  - Halts all new signal generation for the rest of the day if either
    the daily profit target OR the daily loss limit is breached.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from src.daily_circuit_breaker import DailyCircuitBreaker
from src.events import OptionChainEvent, SignalEvent, SignalDirection, TradePlan
from src.logger import get_logger
from src.market_adapter.base import MarketDataAdapter
from src.strategy_engine.strategies import ALL_STRATEGIES, BaseStrategy

log = get_logger(__name__)

_PENDING_T = Dict[str, Any]  # symbol → pending-confirmation state


class MultiStrategyEngine:
    def __init__(
        self,
        market_adapter: MarketDataAdapter,
        chain_queue: "asyncio.Queue[OptionChainEvent]",
        signal_queue: "asyncio.Queue[SignalEvent]",
        config: Dict[str, Any],
        position_store=None,
        notifier=None,
        tap_queue: "Optional[asyncio.Queue[SignalEvent]]" = None,
        sim_clock=None,
    ) -> None:
        self._market         = market_adapter
        self._chain_queue    = chain_queue
        self._signal_queue   = signal_queue
        self._tap_queue      = tap_queue
        self._position_store = position_store
        self._notifier       = notifier
        self._config         = config
        # When running a replay simulation, trading-hours gating must follow
        # the simulated clock, not real wall-clock time (the sim runs while
        # the real market is closed). None in live/paper mode → real time.
        self._sim_clock      = sim_clock
        self._strategies: List[BaseStrategy] = ALL_STRATEGIES
        self._rr_index: int = 0

        ind = config.get("indicators", {})
        self._lookback: int     = int(ind.get("lookback_bars", 50))
        self._cooldown_min: int = int(ind.get("signal_cooldown_minutes", 60))

        conf = config.get("confirmation", {})
        self._confirm_wait_bars: int    = int(conf.get("wait_bars", 2))
        self._confirm_expire_min: float = float(conf.get("expire_minutes", 10))

        # Long bias: the only statistically robust signal in historical data is
        # the market's upward drift (see scripts/edge_research.py). When enabled,
        # prefer CALL candidates and only take PUTs when no CALL is available.
        self._long_bias: bool = bool(config.get("risk", {}).get("long_bias", True))

        # symbol → {plan, strategy_name, direction, confirmations, first_seen_at}
        self._pending: Dict[str, _PENDING_T] = {}

        self._circuit_breaker = DailyCircuitBreaker(config, position_store)

        log.info(
            "multi_strategy_engine initialised",
            strategies=[s.name for s in self._strategies],
            count=len(self._strategies),
            confirm_wait_bars=self._confirm_wait_bars,
            confirm_expire_min=self._confirm_expire_min,
        )

    def get_pending_count(self) -> int:
        """Return the number of signals currently awaiting confirmation."""
        return len(self._pending)

    # ------------------------------------------------------------------ #
    # Strategy scoring & selection                                         #
    # ------------------------------------------------------------------ #

    def _get_scores(self) -> Dict[str, float]:
        if not self._position_store:
            return {s.name: 0.0 for s in self._strategies}
        try:
            stats = self._position_store.get_strategy_scores()
        except Exception:
            return {s.name: 0.0 for s in self._strategies}
        if not stats:
            return {s.name: 0.0 for s in self._strategies}

        pnls    = [v.get("total_pnl", 0.0) for v in stats.values()]
        max_abs = max((abs(p) for p in pnls), default=1.0) or 1.0

        scores: Dict[str, float] = {}
        for s in self._strategies:
            row = stats.get(s.name)
            if row is None or row.get("trades", 0) == 0:
                scores[s.name] = 0.0
            else:
                win_rate = row.get("win_rate", 0.0)
                norm_pnl = row.get("total_pnl", 0.0) / max_abs
                scores[s.name] = 0.6 * win_rate + 0.4 * norm_pnl
        return scores

    def _pick_winner(
        self,
        candidates: List[TradePlan],
        scores: Dict[str, float],
    ) -> Optional[TradePlan]:
        if not candidates:
            return None
        cand_scores = [scores.get(c.strategy_name, 0.0) for c in candidates]
        if len(set(cand_scores)) == 1:
            idx = self._rr_index % len(candidates)
            self._rr_index += 1
            return candidates[idx]
        return max(candidates, key=lambda p: scores.get(p.strategy_name, 0.0))

    # ------------------------------------------------------------------ #
    # Filters                                                              #
    # ------------------------------------------------------------------ #

    def _is_trading_hours(self) -> bool:
        th        = self._config.get("trading_hours", {})
        start_str = th.get("start", "09:45")
        end_str   = th.get("end", "15:30")
        # Always compare against Eastern Time regardless of server timezone.
        # In replay-simulation mode, use the simulated clock so trading-hours
        # gating tracks the replayed day rather than real wall-clock time.
        try:
            if self._sim_clock is not None:
                now = self._sim_clock.now().time()
            else:
                from src.market_hours import now_et
                now = now_et().time()
        except Exception:
            now = datetime.now().time()
        try:
            start = time(*[int(p) for p in start_str.split(":")])
            end   = time(*[int(p) for p in end_str.split(":")])
        except Exception:
            return True
        return start <= now <= end

    async def _spy_trend(self) -> Optional[SignalDirection]:
        """Return CALL if SPY > SMA20, PUT if SPY < SMA20, None if unavailable."""
        try:
            bars = await self._market.get_intraday_bars("SPY", interval="1min", limit=25)
            if not bars or len(bars) < 20:
                return None
            closes = np.asarray([b["close"] for b in bars], dtype=float)
            sma20  = float(closes[-20:].mean())
            last   = float(closes[-1])
            return SignalDirection.CALL if last > sma20 else SignalDirection.PUT
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Signal confirmation helpers                                          #
    # ------------------------------------------------------------------ #

    def _is_pending_expired(self, entry: _PENDING_T) -> bool:
        elapsed = (datetime.now(timezone.utc) - entry["first_seen_at"]).total_seconds()
        return elapsed > self._confirm_expire_min * 60

    async def _evaluate_strategy(
        self,
        strategy: BaseStrategy,
        symbol: str,
        bars: List[Dict],
        contracts: List,
    ) -> Optional[TradePlan]:
        try:
            return strategy.generate_signal(symbol, bars, contracts, self._config)
        except Exception as exc:
            log.warning("strategy error", strategy=strategy.name, symbol=symbol, error=str(exc))
            return None

    # ------------------------------------------------------------------ #
    # Signal publishing                                                    #
    # ------------------------------------------------------------------ #

    async def _publish_signal(self, plan: TradePlan) -> None:
        symbol = plan.symbol
        log.info(
            "SIGNAL CONFIRMED — EXECUTING",
            symbol=symbol,
            strategy=plan.strategy_name,
            direction=plan.direction.value,
            strike=plan.contract.strike,
            entry=plan.entry_limit,
            stop=plan.stop_loss,
            target=plan.take_profit,
        )
        signal = SignalEvent(trade_plan=plan)
        await self._signal_queue.put(signal)
        if self._tap_queue is not None:
            try:
                self._tap_queue.put_nowait(signal)
            except asyncio.QueueFull:
                log.debug("tap_queue full", symbol=symbol)

        if self._position_store:
            self._position_store.set_cooldown(symbol)

        if self._notifier:
            asyncio.ensure_future(self._notifier.signal(
                symbol=symbol,
                direction=plan.direction.value,
                strike=plan.contract.strike,
                expiry=plan.contract.expiry,
                entry=plan.entry_limit,
                stop=plan.stop_loss,
                target=plan.take_profit,
                rationale=plan.rationale,
            ))

    # ------------------------------------------------------------------ #
    # Core processing — helpers                                            #
    # ------------------------------------------------------------------ #

    def _check_gates(self, symbol: str) -> Optional[str]:
        """Return a block reason string, or None if all gates pass."""
        halted, reason = self._circuit_breaker.check()
        if halted:
            log.info("circuit_breaker active — no new signals", reason=reason)
            return reason
        if not self._is_trading_hours():
            log.debug("outside trading hours, skipping", symbol=symbol)
            return "outside_hours"
        if self._position_store and self._position_store.is_on_cooldown(symbol, self._cooldown_min):
            log.debug("signal cooldown active", symbol=symbol)
            return "cooldown"
        if self._position_store and symbol in self._position_store.symbols():
            log.debug("already in position", symbol=symbol)
            return "in_position"
        return None

    async def _fetch_bars(self, symbol: str) -> List[Dict]:
        try:
            return await self._market.get_intraday_bars(
                symbol, interval="1min", limit=self._lookback + 10
            )
        except Exception as exc:
            log.debug("bars fetch failed", symbol=symbol, error=str(exc))
            return []

    async def _handle_pending_confirmation(
        self,
        symbol: str,
        bars: List[Dict],
        contracts: List,
    ) -> bool:
        """Advance confirmation state. Returns True if caller should stop processing."""
        if symbol not in self._pending:
            return False
        entry = self._pending[symbol]

        if self._is_pending_expired(entry):
            log.debug("pending signal expired — discarding", symbol=symbol,
                      strategy=entry["strategy_name"])
            del self._pending[symbol]
            return False  # fall through to re-evaluate fresh

        strategy = next((s for s in self._strategies if s.name == entry["strategy_name"]), None)
        if strategy:
            fresh_plan = await self._evaluate_strategy(strategy, symbol, bars, contracts)
            if fresh_plan is None:
                # Transient miss: the trigger (e.g. a volume spike) did not recur
                # on this bar. Keep the pending entry alive — confirmations are
                # counted cumulatively within the expiry window, so a momentary
                # gap must not throw the signal away (that was the root cause of
                # signals being stuck and never executing).
                log.debug("signal not reproduced this bar — still pending",
                          symbol=symbol, strategy=entry["strategy_name"],
                          confirmations=entry["confirmations"])
            elif fresh_plan.direction == entry["direction"]:
                entry["confirmations"] += 1
                entry["plan"] = fresh_plan
                log.debug("signal confirmation", symbol=symbol,
                          strategy=entry["strategy_name"],
                          confirmations=entry["confirmations"],
                          needed=self._confirm_wait_bars)
                if entry["confirmations"] >= self._confirm_wait_bars:
                    del self._pending[symbol]
                    await self._publish_signal(fresh_plan)
            else:
                # Opposite-direction signal — a genuine reversal invalidates the
                # pending signal, so discard it.
                log.debug("signal reversed — discarding", symbol=symbol,
                          strategy=entry["strategy_name"])
                del self._pending[symbol]
        return True

    async def _select_best_plan(
        self,
        symbol: str,
        bars: List[Dict],
        contracts: List,
    ) -> Optional[TradePlan]:
        """Run all strategies, apply market-bias filter, pick winner."""
        results = await asyncio.gather(
            *[self._evaluate_strategy(s, symbol, bars, contracts) for s in self._strategies],
            return_exceptions=False,
        )
        candidates: List[TradePlan] = [r for r in results if r is not None]
        if not candidates:
            log.debug("no signal from any strategy", symbol=symbol)
            return None

        market_bias = await self._spy_trend()
        if market_bias is not None:
            aligned = [c for c in candidates if c.direction == market_bias]
            if aligned:
                # Prefer trend-aligned signals when available.
                candidates = aligned
            # If no aligned signals, allow counter-trend signals through —
            # individual stocks can diverge strongly from SPY (e.g. earnings, sector news).

        # Long bias: only the upward drift is a proven edge, so favor CALLs.
        # Drop PUTs when at least one CALL candidate exists; keep PUTs only when
        # there is no long alternative this bar.
        if self._long_bias:
            calls = [c for c in candidates if c.direction == SignalDirection.CALL]
            if calls:
                candidates = calls

        return self._pick_winner(candidates, self._get_scores())

    def _queue_for_confirmation(self, symbol: str, plan: TradePlan) -> None:
        log.info("SIGNAL PENDING CONFIRMATION", symbol=symbol,
                 strategy=plan.strategy_name, direction=plan.direction.value,
                 confirmations_needed=self._confirm_wait_bars)
        self._pending[symbol] = {
            "plan":          plan,
            "strategy_name": plan.strategy_name,
            "direction":     plan.direction,
            "confirmations": 0,
            "first_seen_at": datetime.now(timezone.utc),
        }

    # ------------------------------------------------------------------ #
    # Core processing                                                      #
    # ------------------------------------------------------------------ #

    async def _process_chain(self, event: OptionChainEvent) -> None:
        symbol = event.symbol
        if self._check_gates(symbol) is not None:
            return
        bars = await self._fetch_bars(symbol)
        if not bars:
            return
        if await self._handle_pending_confirmation(symbol, bars, event.contracts):
            return
        plan = await self._select_best_plan(symbol, bars, event.contracts)
        if plan is None:
            return
        if self._confirm_wait_bars <= 0:
            # Confirmation disabled — execute on the first valid signal.
            await self._publish_signal(plan)
        else:
            self._queue_for_confirmation(symbol, plan)

    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        log.info("multi_strategy_engine started", strategies=len(self._strategies))
        while True:
            try:
                event: OptionChainEvent = await self._chain_queue.get()
                await self._process_chain(event)
            except asyncio.CancelledError:
                log.info("multi_strategy_engine cancelled")
                return
            except Exception as exc:
                log.error("multi_strategy_engine error", error=str(exc))
