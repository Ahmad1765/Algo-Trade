# file: tests/test_auth_static.py
"""Auth must gate HTML pages but allow public static assets."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.api_server.server import create_app


def _app():
    return create_app(risk_manager=MagicMock(), signal_store=[], position_store=None)


@pytest.fixture
def secured(tmp_path, monkeypatch):
    (tmp_path / "dashboard").mkdir()
    (tmp_path / "dashboard" / "index.html").write_text("<!doctype html><title>Dashboard</title>")
    (tmp_path / "_next").mkdir()
    (tmp_path / "_next" / "app.js").write_text("x")
    monkeypatch.setenv("WEB_DIR", str(tmp_path))
    monkeypatch.delenv("DEV_MODE", raising=False)
    monkeypatch.setenv("DASHBOARD_PASSWORD", "secret")
    monkeypatch.setenv("SESSION_SECRET", "unit-test-secret")
    return tmp_path


async def test_unauthenticated_html_redirects_to_login(secured):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get(
            "/dashboard/", headers={"Accept": "text/html"}, allow_redirects=False
        )
        assert resp.status == 302
        assert resp.headers["Location"] == "/login"


async def test_next_assets_are_public(secured):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/_next/app.js", allow_redirects=False)
        assert resp.status == 200


async def test_health_still_public(secured):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/health")
        assert resp.status == 200


async def test_spoofed_next_path_is_still_gated(secured):
    # A path containing "_next" but not rooted at /_next/ must NOT be exempt.
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get(
            "/evil/_next/app.js",
            headers={"Accept": "text/html"},
            allow_redirects=False,
        )
        assert resp.status == 302
        assert resp.headers["Location"] == "/login"


async def test_named_public_files_bypass_auth(secured):
    # Exempt static files must bypass auth. With no such file present they
    # fall through to the 404 handler (404), never the /login redirect.
    async with TestClient(TestServer(_app())) as client:
        for path in ("/icon.svg", "/favicon.ico", "/robots.txt"):
            resp = await client.get(
                path, headers={"Accept": "text/html"}, allow_redirects=False
            )
            assert resp.status != 302, f"{path} was redirected (gated), expected bypass"
            assert resp.status in (200, 404), f"{path} unexpected status {resp.status}"
