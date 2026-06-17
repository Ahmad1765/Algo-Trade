# file: tests/e2e/auth.spec.py
"""E2E tests for the auth middleware + login flow."""
from __future__ import annotations

import os
import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

pytestmark = pytest.mark.e2e


@pytest.fixture
def auth_app(make_app, monkeypatch):
    """An app instance with auth ENABLED."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", "hunter2")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.delenv("DEV_MODE", raising=False)
    return make_app


class TestAuthGate:
    async def test_health_open_without_auth(self, auth_app):
        async with TestClient(TestServer(auth_app()), cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
            resp = await client.get("/health")
            assert resp.status == 200

    async def test_api_route_401_without_session(self, auth_app):
        async with TestClient(TestServer(auth_app()), cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
            resp = await client.get("/signals", headers={"Accept": "application/json"})
            assert resp.status == 401

    async def test_dashboard_redirects_to_login(self, auth_app):
        async with TestClient(TestServer(auth_app()), cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
            resp = await client.get("/", headers={"Accept": "text/html"},
                                    allow_redirects=False)
            assert resp.status == 302
            assert resp.headers["Location"] == "/login"

    async def test_login_page_accessible(self, auth_app):
        async with TestClient(TestServer(auth_app()), cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
            resp = await client.get("/login")
            assert resp.status == 200
            assert "password" in (await resp.text()).lower()


class TestLoginFlow:
    async def test_wrong_password_rejected(self, auth_app):
        async with TestClient(TestServer(auth_app()), cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
            resp = await client.post("/login", data={"password": "wrong"},
                                     allow_redirects=False)
            assert resp.status == 401

    async def test_correct_password_sets_cookie_and_unlocks(self, auth_app):
        async with TestClient(TestServer(auth_app()), cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
            resp = await client.post("/login", data={"password": "hunter2"},
                                     allow_redirects=False)
            assert resp.status == 302
            # Extract Set-Cookie and forward manually (Secure cookies aren't sent
            # over plain HTTP by the cookie jar even with unsafe=True).
            set_cookie = resp.headers.get("Set-Cookie", "")
            cookie_val = ""
            for part in set_cookie.split(";"):
                part = part.strip()
                if part.startswith("algo_session="):
                    cookie_val = part.split("=", 1)[1]
                    break
            assert cookie_val, "algo_session cookie not found in Set-Cookie"
            ok = await client.get("/signals", headers={
                "Accept": "application/json",
                "Cookie": f"algo_session={cookie_val}",
            })
            assert ok.status == 200

    async def test_logout_clears_session(self, auth_app):
        async with TestClient(TestServer(auth_app()), cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
            resp = await client.post("/login", data={"password": "hunter2"},
                                     allow_redirects=False)
            set_cookie = resp.headers.get("Set-Cookie", "")
            cookie_val = ""
            for part in set_cookie.split(";"):
                part = part.strip()
                if part.startswith("algo_session="):
                    cookie_val = part.split("=", 1)[1]
                    break
            # Use the session cookie to confirm we're logged in, then logout
            ok = await client.get("/signals", headers={
                "Accept": "application/json",
                "Cookie": f"algo_session={cookie_val}",
            })
            assert ok.status == 200
            # logout must tell the browser to drop the session cookie
            resp = await client.post("/logout", allow_redirects=False)
            assert resp.status == 302
            set_cookie = resp.headers.get("Set-Cookie", "")
            assert "algo_session=" in set_cookie
            assert ('Max-Age=0' in set_cookie) or ('Expires=' in set_cookie) or ('algo_session=""' in set_cookie) or ('algo_session=;' in set_cookie)


class TestAuthDisabled:
    async def test_open_when_no_password(self, make_app, monkeypatch):
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        monkeypatch.setenv("DEV_MODE", "1")
        async with TestClient(TestServer(make_app()), cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
            resp = await client.get("/signals", headers={"Accept": "application/json"})
            assert resp.status == 200
