# file: tests/test_multi_engine_confirmation.py
"""
MultiStrategyEngine signal-confirmation behaviour.

Root cause regression: confirmation used to DISCARD a pending signal the moment
one re-evaluation failed to reproduce it. Strategy triggers are transient (e.g.
volume spikes), so the signal almost never recurred on the very next event and
nothing ever executed. Confirmation must instead:
  - tolerate a transient miss (fresh signal None) — keep waiting until expiry,
  - count same-direction confirmations cumulatively within the expiry window,
  - discard only on a genuine reversal (opposite direction) or expiry,
  - publish immediately when wait_bars <= 0 (no confirmation required).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.events import SignalDirection
from src.strategy_engine.multi_engine import MultiStrategyEngine


def _config(wait_bars: int = 1):
    return {
        "indicators": {"lookback_bars": 50, "signal_cooldown_minutes": 30},
        "confirmation": {"wait_bars": wait_bars, "expire_minutes": 10},
        "trading_hours": {"start": "00:00", "end": "23:59"},
    }


def _plan(direction: SignalDirection):
    p = MagicMock()
    p.direction = direction
    p.strategy_name = "RSIMACD"  # must match a real strategy name
    p.symbol = "AAPL"
    return p


def _engine(config):
    eng = MultiStrategyEngine(
        MagicMock(), asyncio.Queue(), asyncio.Queue(), config,
        position_store=None, notifier=None,
    )
    # Isolate confirmation logic from strategy-data details / gates.
    eng._check_gates = lambda symbol: None
    eng._fetch_bars = AsyncMock(return_value=[{"close": 1}])
    return eng


def _event():
    ev = MagicMock()
    ev.symbol = "AAPL"
    ev.contracts = []
    return ev


def test_confirmation_survives_transient_miss_then_publishes():
    """event1 fires -> queue; event2 transient None -> KEEP; event3 fires -> publish."""
    async def go():
        eng = _engine(_config(wait_bars=1))
        eng._select_best_plan = AsyncMock(return_value=_plan(SignalDirection.CALL))
        # Confirmation re-eval: miss, then re-fire.
        eng._evaluate_strategy = AsyncMock(side_effect=[None, _plan(SignalDirection.CALL)])

        await eng._process_chain(_event())          # event1 -> queued
        assert "AAPL" in eng._pending
        await eng._process_chain(_event())          # event2 -> transient miss, keep
        kept = "AAPL" in eng._pending
        await eng._process_chain(_event())          # event3 -> confirm + publish
        published = not eng._signal_queue.empty()
        return kept, published

    kept, published = asyncio.run(go())
    assert kept, "transient miss must NOT discard the pending signal"
    assert published, "signal must publish once it re-fires within the window"


def test_confirmation_discards_on_reversal():
    """A genuine opposite-direction signal cancels the pending one."""
    async def go():
        eng = _engine(_config(wait_bars=2))
        eng._select_best_plan = AsyncMock(return_value=_plan(SignalDirection.CALL))
        eng._evaluate_strategy = AsyncMock(return_value=_plan(SignalDirection.PUT))
        await eng._process_chain(_event())   # queue CALL
        await eng._process_chain(_event())   # PUT reversal -> discard
        return "AAPL" in eng._pending, eng._signal_queue.empty()

    pending, empty = asyncio.run(go())
    assert not pending, "reversal must discard the pending signal"
    assert empty, "no signal should publish on a reversal"


def test_wait_bars_zero_publishes_immediately():
    """wait_bars <= 0 means no confirmation — execute on first valid signal."""
    async def go():
        eng = _engine(_config(wait_bars=0))
        eng._select_best_plan = AsyncMock(return_value=_plan(SignalDirection.CALL))
        await eng._process_chain(_event())
        return eng._signal_queue.empty(), "AAPL" in eng._pending

    empty, pending = asyncio.run(go())
    assert not empty, "wait_bars=0 must publish immediately"
    assert not pending, "wait_bars=0 must not leave a pending entry"
