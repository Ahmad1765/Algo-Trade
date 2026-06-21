# file: tests/test_sim_clock.py
from datetime import datetime

from src.sim.clock import SimClock, ET


class FakeTime:
    """Controllable monotonic clock for deterministic tests."""
    def __init__(self) -> None:
        self.t = 1000.0
    def __call__(self) -> float:
        return self.t
    def advance(self, secs: float) -> None:
        self.t += secs


def _june17() -> datetime:
    return datetime(2026, 6, 17, tzinfo=ET)


def test_starts_at_market_open():
    clk = SimClock(_june17(), speed=60.0, time_fn=FakeTime())
    now = clk.now()
    assert now.hour == 9 and now.minute == 30
    assert clk.is_open() is True
    assert clk.day_complete() is False


def test_speed_scales_elapsed_time():
    ft = FakeTime()
    clk = SimClock(_june17(), speed=60.0, time_fn=ft)
    ft.advance(10)  # 10 real seconds * 60 = 600 sim seconds = 10 sim minutes
    assert clk.now().strftime("%H:%M") == "09:40"


def test_pause_freezes_sim_time():
    ft = FakeTime()
    clk = SimClock(_june17(), speed=60.0, time_fn=ft)
    ft.advance(5)            # -> 09:35
    clk.pause()
    frozen = clk.now()
    ft.advance(100)          # time passes but paused
    assert clk.now() == frozen
    assert clk.paused is True


def test_resume_continues_from_frozen_point():
    ft = FakeTime()
    clk = SimClock(_june17(), speed=60.0, time_fn=ft)
    ft.advance(5)            # 09:35
    clk.pause()
    ft.advance(100)          # ignored
    clk.resume()
    ft.advance(1)            # +1 real sec * 60 = +1 sim min -> 09:36
    assert clk.now().strftime("%H:%M") == "09:36"


def test_set_speed_reanchors_without_jump():
    ft = FakeTime()
    clk = SimClock(_june17(), speed=60.0, time_fn=ft)
    ft.advance(10)           # 09:40
    clk.set_speed(600.0)     # no time jump at the moment of change
    assert clk.now().strftime("%H:%M") == "09:40"
    ft.advance(1)            # +1 real * 600 = +10 sim min -> 09:50
    assert clk.now().strftime("%H:%M") == "09:50"


def test_clamps_at_market_close():
    ft = FakeTime()
    clk = SimClock(_june17(), speed=60.0, time_fn=ft)
    ft.advance(100_000)      # far past close
    now = clk.now()
    assert now.hour == 16 and now.minute == 0
    assert clk.is_open() is False
    assert clk.day_complete() is True


def test_set_speed_rejects_non_positive():
    clk = SimClock(_june17(), time_fn=FakeTime())
    try:
        clk.set_speed(0)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_status_shape():
    clk = SimClock(_june17(), speed=60.0, time_fn=FakeTime())
    s = clk.status()
    assert s["active"] is True
    assert s["sim_date"] == "2026-06-17"
    assert s["speed"] == 60.0
    assert s["paused"] is False
    assert s["market_open"] is True
    assert "sim_time" in s and "sim_time_iso" in s
