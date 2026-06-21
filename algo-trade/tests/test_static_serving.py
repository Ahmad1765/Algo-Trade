# file: tests/test_static_serving.py
"""Static-export serving behaviour for the aiohttp server."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.api_server.server import create_app


def _app():
    return create_app(risk_manager=MagicMock(), signal_store=[], position_store=None)


@pytest.fixture
def web_dir(tmp_path, monkeypatch):
    """A fake exported site, wired in via the WEB_DIR env var."""
    (tmp_path / "dashboard").mkdir()
    (tmp_path / "dashboard" / "index.html").write_text("<!doctype html><title>Dashboard</title>")
    (tmp_path / "_next" / "static").mkdir(parents=True)
    (tmp_path / "_next" / "static" / "app.js").write_text("console.log('app')")
    (tmp_path / "404.html").write_text("<!doctype html><title>Not found</title>")
    (tmp_path / "icon.svg").write_text("<svg/>")
    monkeypatch.setenv("WEB_DIR", str(tmp_path))
    monkeypatch.setenv("DEV_MODE", "1")  # auth off for these tests
    return tmp_path


async def test_root_redirects_to_dashboard(web_dir):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/", allow_redirects=False)
        assert resp.status == 302
        assert resp.headers["Location"] == "/dashboard/"


async def test_serves_dashboard_index(web_dir):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/dashboard/")
        assert resp.status == 200
        assert "Dashboard" in await resp.text()


async def test_serves_next_asset(web_dir):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/_next/static/app.js")
        assert resp.status == 200
        assert "console.log" in await resp.text()


async def test_unknown_route_falls_back_to_404_page(web_dir):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/does-not-exist/")
        assert resp.status == 404
        assert "Not found" in await resp.text()


async def test_api_alias_reaches_health(web_dir):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/health")
        assert resp.status == 200
        assert (await resp.json())["status"] == "ok"


async def test_health_still_served_at_root(web_dir):
    # The catch-all must not shadow the explicit root /health route.
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/health")
        assert resp.status == 200
