# file: tests/e2e/dashboard.spec.py
"""
Tests for the /status API endpoint.

Covers:
  - /status returns the correct open_positions count when positions are present
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

pytestmark = pytest.mark.e2e


class TestDashboard:
    async def test_dashboard_shows_correct_position_count_with_store(
        self, make_app, position_store, signal_store
    ):
        position_store.add_position(
            "AAPL_2026-05-16_175.0_C", "AAPL", "CALL", 2.55, 1.85, 4.15, 10
        )
        position_store.add_position(
            "SPY_2026-04-18_520.0_P", "SPY", "PUT", 3.10, 3.80, 1.80, 5
        )
        async with TestClient(TestServer(
            make_app(sig_store=signal_store, pos_store=position_store)
        )) as client:
            # Position data is served via /stream (SSE) and /status — confirm /status returns correct count.
            resp = await client.get("/status")
            data = await resp.json()
            assert data["open_positions"] == 2
