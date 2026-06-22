# file: tests/e2e/sim_lifecycle.spec.py
import asyncio

from aiohttp.test_utils import TestClient, TestServer

from src.api_server.server import create_app
from src.runtime.context import RuntimeContext
from src.runtime.session_manager import SessionManager


class _Risk:
    pass


def _fake_build(_mark="live"):
    async def _idle():
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            return
    async def _build(config, mode, *, market_adapter=None, sim_clock=None, position_store=None):
        ctx = RuntimeContext(mode="sim" if sim_clock is not None else "live",
                             market_adapter=market_adapter, sim_clock=sim_clock)
        return ctx, [_idle]
    return _build


class _FakeClock:
    def __init__(self, *a, **k): pass
    def now(self):
        from datetime import datetime, timezone
        return datetime(2026, 6, 17, 9, 30, tzinfo=timezone.utc)
    def is_open(self): return True
    def status(self):
        return {"sim_time": "2026-06-17 09:30:00 ET", "speed": 60.0, "paused": False}


async def _loader(sim_date, **kw):
    return {"AAPL": [{"datetime": "2026-06-17T13:30:00+00:00", "open": 1, "high": 1,
                      "low": 1, "close": 1, "volume": 1}]}


def _make_manager():
    ctx = RuntimeContext()
    return SessionManager(
        config={"mode": "paper"}, ctx=ctx,
        build_fn=_fake_build(), load_fn=_loader, clock_factory=_FakeClock,
        adapter_factory=lambda d, c: object(),
        store_factory=lambda: ("SANDBOX", None),
    ), ctx


async def _wait_state(mgr, target, tries=100):
    for _ in range(tries):
        if mgr.state == target:
            return
        await asyncio.sleep(0.01)


async def test_start_then_stop_sim():
    mgr, ctx = _make_manager()
    await mgr.start_live()
    app = create_app(_Risk(), [], None, ctx=ctx, session_manager=mgr)
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/sim/start", json={"date": "2026-06-17", "speed": 60})
        assert r.status in (200, 202)
        await _wait_state(mgr, "running")
        st = await (await client.get("/sim/status")).json()
        assert st["state"] == "running"
        assert st["active"] is True

        r = await client.post("/sim/stop")
        assert r.status == 200
        await _wait_state(mgr, "idle")
        st = await (await client.get("/sim/status")).json()
        assert st["state"] == "idle"
    await mgr._cancel_tasks()


async def test_start_rejects_bad_date():
    mgr, ctx = _make_manager()
    await mgr.start_live()
    app = create_app(_Risk(), [], None, ctx=ctx, session_manager=mgr)
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/sim/start", json={"date": "2026-06-20", "speed": 60})  # Saturday
        assert r.status == 422
    await mgr._cancel_tasks()


async def test_control_409_when_not_running():
    mgr, ctx = _make_manager()
    await mgr.start_live()
    app = create_app(_Risk(), [], None, ctx=ctx, session_manager=mgr)
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/sim/control", json={"action": "pause"})
        assert r.status == 409
    await mgr._cancel_tasks()
