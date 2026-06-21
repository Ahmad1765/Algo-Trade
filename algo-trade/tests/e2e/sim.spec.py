# file: tests/e2e/sim.spec.py
"""
E2E tests for sim-clock integration endpoints:
  GET  /sim/status   — reports active/inactive and sim date
  POST /sim/control  — pause / resume / set_speed
  GET  /health       — reports sim market_open + sim market_time_et when clock active
"""
from __future__ import annotations

import os
os.environ.setdefault("DEV_MODE", "1")  # disable auth for tests

from datetime import datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.api_server.server import create_app
from src.sim.clock import SimClock, ET

pytestmark = pytest.mark.e2e


class _Risk:
    """Minimal stand-in for RiskManager — create_app only needs the object to exist."""
    pass


# ---------------------------------------------------------------------------
# Helper: build app with sim clock
# ---------------------------------------------------------------------------

def _app_with_clock(clock: SimClock):
    return create_app(_Risk(), [], None, None, [], None, sim_clock=clock)


def _app_without_clock():
    return create_app(_Risk(), [], None, None, [], None)


# ---------------------------------------------------------------------------
# /sim/status
# ---------------------------------------------------------------------------

class TestSimStatus:
    async def test_sim_status_active(self):
        clock = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0)
        app = _app_with_clock(clock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/sim/status")
            assert resp.status == 200
            body = await resp.json()
            assert body["active"] is True
            assert body["sim_date"] == "2026-06-17"

    async def test_sim_status_inactive_without_clock(self):
        app = _app_without_clock()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/sim/status")
            assert resp.status == 200
            body = await resp.json()
            assert body["active"] is False

    async def test_sim_status_api_alias(self):
        """The /api/sim/status alias must also work."""
        clock = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0)
        app = _app_with_clock(clock)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/sim/status")
            assert resp.status == 200
            body = await resp.json()
            assert body["active"] is True


# ---------------------------------------------------------------------------
# /sim/control
# ---------------------------------------------------------------------------

class TestSimControl:
    async def test_sim_control_pause_resume_and_speed(self):
        clock = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0)
        app = _app_with_clock(clock)
        async with TestClient(TestServer(app)) as client:
            # pause
            r = await client.post("/sim/control", json={"action": "pause"})
            assert r.status == 200
            assert (await r.json())["paused"] is True

            # resume
            r = await client.post("/sim/control", json={"action": "resume"})
            assert r.status == 200
            assert (await r.json())["paused"] is False

            # set_speed to valid value
            r = await client.post("/sim/control", json={"action": "set_speed", "speed": 10})
            assert r.status == 200
            assert (await r.json())["speed"] == 10.0

            # set_speed to invalid value → 422
            r = await client.post("/sim/control", json={"action": "set_speed", "speed": -1})
            assert r.status == 422

            # bogus action → 422
            r = await client.post("/sim/control", json={"action": "bogus"})
            assert r.status == 422

    async def test_sim_control_without_clock_returns_409(self):
        app = _app_without_clock()
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/sim/control", json={"action": "pause"})
            assert r.status == 409

    async def test_sim_control_api_alias(self):
        """/api/sim/control alias works."""
        clock = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0)
        app = _app_with_clock(clock)
        async with TestClient(TestServer(app)) as client:
            r = await client.post("/api/sim/control", json={"action": "pause"})
            assert r.status == 200
            assert (await r.json())["paused"] is True


# ---------------------------------------------------------------------------
# /health with sim clock
# ---------------------------------------------------------------------------

class TestHealthWithSimClock:
    async def test_health_reports_sim_market_open_and_date(self):
        clock = SimClock(datetime(2026, 6, 17, tzinfo=ET), speed=60.0)
        app = _app_with_clock(clock)
        async with TestClient(TestServer(app)) as client:
            body = await (await client.get("/health")).json()
            # 2026-06-17 is a Tuesday — market is open at 09:30 ET (clock starts there)
            assert body["market_open"] is True
            assert body["market_time_et"].startswith("2026-06-17")

    async def test_health_without_clock_still_works(self):
        """Ensure non-sim path is unchanged (market_open is a bool, time has ET suffix)."""
        app = _app_without_clock()
        async with TestClient(TestServer(app)) as client:
            body = await (await client.get("/health")).json()
            assert isinstance(body["market_open"], bool)
            assert "ET" in body["market_time_et"]
