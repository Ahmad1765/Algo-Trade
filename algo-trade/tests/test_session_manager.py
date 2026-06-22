# file: tests/test_session_manager.py
import asyncio
from datetime import date

import pytest

from src.runtime.context import RuntimeContext
from src.runtime.session_manager import SessionManager


# --- Fakes -----------------------------------------------------------------

async def _idle_coro():
    try:
        while True:
            await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        return


def _fake_build(mark):
    async def _build(config, mode, *, market_adapter=None, sim_clock=None, position_store=None):
        ctx = RuntimeContext(mode="sim" if sim_clock is not None else "live",
                             market_adapter=market_adapter, sim_clock=sim_clock,
                             position_store=position_store or f"{mark}-store")
        return ctx, [_idle_coro, _idle_coro]
    return _build


class _FakeClock:
    def __init__(self, *_a, **_k):
        pass
    def now(self):
        from datetime import datetime, timezone
        return datetime(2026, 6, 17, 9, 30, tzinfo=timezone.utc)
    def status(self):
        return {"sim_time": "2026-06-17 09:30:00 ET", "speed": 60.0, "paused": False}


async def _ok_loader(sim_date, **kw):
    return {"AAPL": [{"datetime": "2026-06-17T13:30:00+00:00", "open": 1, "high": 1,
                      "low": 1, "close": 1, "volume": 1}]}


async def _failing_loader(sim_date, **kw):
    raise RuntimeError("no data")


def _mgr(loader=_ok_loader):
    ctx = RuntimeContext()
    return SessionManager(
        config={"database": {"url": "sqlite:///unused"}},
        ctx=ctx,
        build_fn=_fake_build("live"),
        load_fn=loader,
        clock_factory=_FakeClock,
        adapter_factory=lambda data, clock: ("ADAPTER", data, clock),
        store_factory=lambda: "SANDBOX-STORE",
    )


# --- Tests -----------------------------------------------------------------

def test_start_live_sets_idle_and_runs():
    async def go():
        m = _mgr()
        await m.start_live()
        assert m.state == "idle"
        assert m.ctx.mode == "live"
        await m._cancel_tasks()  # cleanup
    asyncio.run(go())


def test_start_sim_transitions_to_running():
    async def go():
        m = _mgr()
        await m.start_live()
        await m.start_sim(date(2026, 6, 17), 60.0)
        # allow the background load+build task to complete
        for _ in range(50):
            if m.state in ("running", "error"):
                break
            await asyncio.sleep(0.01)
        assert m.state == "running"
        assert m.ctx.mode == "sim"
        assert m.status()["active"] is True
        await m.stop_sim()
        assert m.state == "idle"
        assert m.ctx.mode == "live"
        await m._cancel_tasks()
    asyncio.run(go())


def test_double_start_sim_is_rejected():
    async def go():
        m = _mgr()
        await m.start_live()
        await m.start_sim(date(2026, 6, 17), 60.0)
        with pytest.raises(RuntimeError):
            await m.start_sim(date(2026, 6, 17), 60.0)  # already loading/running
        await m._cancel_tasks()
    asyncio.run(go())


def test_loader_failure_sets_error_and_restores_live():
    async def go():
        m = _mgr(loader=_failing_loader)
        await m.start_live()
        await m.start_sim(date(2026, 6, 17), 60.0)
        for _ in range(50):
            if m.state in ("running", "error", "idle"):
                if m.state != "loading":
                    break
            await asyncio.sleep(0.01)
        assert m.state == "error"
        assert m.status()["error"]
        assert m.ctx.mode == "live"  # live restored
        await m._cancel_tasks()
    asyncio.run(go())


def test_stop_when_idle_is_noop():
    async def go():
        m = _mgr()
        await m.start_live()
        await m.stop_sim()  # idle -> no-op
        assert m.state == "idle"
        await m._cancel_tasks()
    asyncio.run(go())


def test_stop_during_loading_cancels_load_and_restores_idle():
    async def go():
        async def _slow_loader(sim_date, **kw):
            await asyncio.sleep(5)
            return {"AAPL": [{"datetime": "2026-06-17T13:30:00+00:00",
                               "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]}

        m = _mgr(loader=_slow_loader)
        await m.start_live()
        await m.start_sim(date(2026, 6, 17), 60.0)
        # state must be loading before the slow loader completes
        assert m.state == "loading"
        captured_load_task = m._load_task
        # Stop while still loading — must interrupt the load and restore idle
        await m.stop_sim()
        assert m.state == "idle", f"expected idle, got {m.state!r}"
        assert m.ctx.mode == "live", f"expected ctx.mode='live', got {m.ctx.mode!r}"
        # The load task must be finished (done/cancelled), not still pending
        assert captured_load_task is not None
        assert captured_load_task.done(), "load task should be done after stop_sim"
        # Internal reference must be cleared
        assert m._load_task is None
        await m._cancel_tasks()  # cleanup live tasks
    asyncio.run(go())
