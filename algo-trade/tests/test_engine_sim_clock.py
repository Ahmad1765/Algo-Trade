# file: tests/test_engine_sim_clock.py
"""Replay-mode trading-hours gating: the engine must follow the sim clock."""
import asyncio
from datetime import datetime

from src.sim.clock import SimClock, ET
from src.strategy_engine.multi_engine import MultiStrategyEngine


def _engine(sim_clock=None):
    cfg = {
        "indicators": {"lookback_bars": 50, "signal_cooldown_minutes": 60},
        "confirmation": {"wait_bars": 2, "expire_minutes": 10},
        "trading_hours": {"start": "09:45", "end": "15:30"},
    }
    q: asyncio.Queue = asyncio.Queue()
    return MultiStrategyEngine(
        market_adapter=None,
        chain_queue=q,
        signal_queue=q,
        config=cfg,
        sim_clock=sim_clock,
    )


class _FakeTime:
    def __init__(self) -> None:
        self.t = 0.0
    def __call__(self) -> float:
        return self.t
    def advance(self, secs: float) -> None:
        self.t += secs


def test_trading_hours_follows_sim_clock_when_present():
    ft = _FakeTime()
    # Sim starts 09:30 ET — before the 09:45 trading window opens.
    clock = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0, time_fn=ft)
    eng = _engine(sim_clock=clock)
    assert eng._is_trading_hours() is False  # 09:30 < 09:45

    ft.advance(20)  # +20 real sec * 60 = +20 sim min -> 09:50, inside window
    assert eng._is_trading_hours() is True


def test_trading_hours_without_sim_clock_uses_real_time():
    # No sim clock -> falls back to real now_et(); just assert it returns a bool
    # and does not raise (behaviour unchanged for live/paper mode).
    eng = _engine(sim_clock=None)
    assert isinstance(eng._is_trading_hours(), bool)
