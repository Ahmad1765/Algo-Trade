# Tier B — Publish algo-trade as a Live Web App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the algo-trade paper-trading dashboard safe and presentable to deploy publicly on Render (auth, Supabase persistence, deploy hardening, UX polish).

**Architecture:** Add two aiohttp middlewares (error + auth) to the existing `create_app()` in `src/api_server/server.py`. Auth is a stdlib HMAC-signed session cookie with credential logic isolated in a new `src/api_server/auth.py` (Tier-C multi-user seam). Persistence is unchanged code — it already supports Postgres via `DATABASE_URL`; we point it at Supabase. UX polish edits the embedded dashboard HTML/JS. Deploy config is hardened in `render.yaml`.

**Tech Stack:** Python 3.11, aiohttp, SQLAlchemy + psycopg2-binary (already present), pytest / pytest-asyncio, Render (Docker), Supabase Postgres.

## Global Constraints

- **No new Python dependencies.** Auth uses only stdlib (`hmac`, `hashlib`, `base64`, `json`, `time`). `psycopg2-binary` already in `requirements.txt`.
- **Keep files focused.** New auth logic lives in `src/api_server/auth.py`, not inside `server.py`.
- **Auth is enabled iff `DASHBOARD_PASSWORD` is set.** Boot fails fast if neither `DASHBOARD_PASSWORD` nor `DEV_MODE` is set (prevents an accidental open public deploy). If `DASHBOARD_PASSWORD` is set, `SESSION_SECRET` is also required.
- **Exempt routes (never gated):** `/health`, `/login`, `/logout`.
- **Existing E2E suite must stay green** — it runs with `DEV_MODE=1` (auth off).
- **Mode stays `paper`.** Never enable real-money paths.
- **Banner copy (verbatim):** `📄 PAPER TRADING — simulated orders, not financial advice`
- **Cookie name (verbatim):** `algo_session`
- **Secrets are never committed.** New env vars go into `render.yaml` as `sync: false`.
- Run tests with: `cd algo-trade && python -m pytest <path> -v` (venv at `algo-trade/.venv`).

---

## File Structure

- **Create** `algo-trade/src/api_server/auth.py` — credential check + cookie sign/verify + config assertions (pure-ish functions, fully unit-testable).
- **Modify** `algo-trade/src/api_server/server.py` — add error + auth middlewares, `/login` + `/logout` routes, register middlewares on `web.Application`, inject UX (banner / about / loading-empty-error JS / responsive CSS) into the dashboard HTML.
- **Modify** `algo-trade/tests/e2e/conftest.py` — set `DEV_MODE=1` so existing specs run with auth off.
- **Create** `algo-trade/tests/e2e/auth.spec.py` — auth middleware + login flow tests.
- **Create** `algo-trade/tests/test_auth.py` — unit tests for `auth.py` pure functions.
- **Modify** `render.yaml` — branch `test`→`main`, add `DASHBOARD_PASSWORD` / `SESSION_SECRET` / `DATABASE_URL` (`sync: false`).
- **Modify** `algo-trade/README.md` — live URL, screenshot, disclaimer, keep-alive note.

---

## Task 1: Auth core module (`auth.py`)

**Files:**
- Create: `algo-trade/src/api_server/auth.py`
- Test: `algo-trade/tests/test_auth.py`

**Interfaces:**
- Consumes: env vars `DASHBOARD_PASSWORD`, `SESSION_SECRET`, `DEV_MODE`.
- Produces:
  - `auth_enabled() -> bool`
  - `assert_auth_config() -> None` (raises `RuntimeError` on misconfig)
  - `verify_credentials(username: Optional[str], password: str) -> Optional[str]` (returns subject or `None`)
  - `sign_session(subject: str, ttl: int = 604800) -> str`
  - `verify_session(token: Optional[str]) -> Optional[str]` (returns subject or `None`)
  - `COOKIE_NAME = "algo_session"`, `EXEMPT_PATHS = {"/health", "/login", "/logout"}`

- [ ] **Step 1: Write the failing unit tests**

Create `algo-trade/tests/test_auth.py`:

```python
# file: tests/test_auth.py
"""Unit tests for the stdlib auth helpers in src/api_server/auth.py."""
from __future__ import annotations

import importlib
import pytest

import src.api_server.auth as auth


@pytest.fixture
def env(monkeypatch):
    """Helper to set/clear the auth env vars and reload the module's view."""
    def _set(**kw):
        for k in ("DASHBOARD_PASSWORD", "SESSION_SECRET", "DEV_MODE"):
            monkeypatch.delenv(k, raising=False)
        for k, v in kw.items():
            monkeypatch.setenv(k, v)
    return _set


class TestAuthEnabled:
    def test_disabled_when_no_password(self, env):
        env(DEV_MODE="1")
        assert auth.auth_enabled() is False

    def test_enabled_when_password_set(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="s")
        assert auth.auth_enabled() is True


class TestAssertConfig:
    def test_raises_when_no_password_and_no_dev(self, env):
        env()  # nothing set
        with pytest.raises(RuntimeError):
            auth.assert_auth_config()

    def test_ok_in_dev_mode(self, env):
        env(DEV_MODE="1")
        auth.assert_auth_config()  # must not raise

    def test_raises_when_password_but_no_secret(self, env):
        env(DASHBOARD_PASSWORD="hunter2")
        with pytest.raises(RuntimeError):
            auth.assert_auth_config()


class TestCredentials:
    def test_correct_password_returns_subject(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="s")
        assert auth.verify_credentials(None, "hunter2") == "admin"

    def test_wrong_password_returns_none(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="s")
        assert auth.verify_credentials(None, "nope") is None


class TestSession:
    def test_roundtrip(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="topsecret")
        token = auth.sign_session("admin")
        assert auth.verify_session(token) == "admin"

    def test_tampered_token_rejected(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="topsecret")
        token = auth.sign_session("admin")
        assert auth.verify_session(token[:-1] + ("0" if token[-1] != "0" else "1")) is None

    def test_expired_token_rejected(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="topsecret")
        token = auth.sign_session("admin", ttl=-1)
        assert auth.verify_session(token) is None

    def test_wrong_secret_rejected(self, env):
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="topsecret")
        token = auth.sign_session("admin")
        env(DASHBOARD_PASSWORD="hunter2", SESSION_SECRET="different")
        assert auth.verify_session(token) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd algo-trade && python -m pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.api_server.auth'`

- [ ] **Step 3: Implement `auth.py`**

Create `algo-trade/src/api_server/auth.py`:

```python
# file: src/api_server/auth.py
"""
Stateless auth for the dashboard.

Single shared password (Tier B) with a clean seam for a multi-user table later
(Tier C): swap the body of ``verify_credentials`` to a DB lookup and nothing
else changes. Sessions are stdlib HMAC-signed cookies — no new dependency.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Optional

COOKIE_NAME = "algo_session"
EXEMPT_PATHS = {"/health", "/login", "/logout"}
_DEFAULT_TTL = 7 * 24 * 3600  # 7 days


def _truthy(val: Optional[str]) -> bool:
    return str(val or "").strip().lower() in {"1", "true", "yes", "on"}


def auth_enabled() -> bool:
    """Auth is active whenever a dashboard password is configured."""
    return bool(os.getenv("DASHBOARD_PASSWORD", ""))


def assert_auth_config() -> None:
    """Fail fast so a public deploy can never launch unauthenticated."""
    password = os.getenv("DASHBOARD_PASSWORD", "")
    dev = _truthy(os.getenv("DEV_MODE"))
    if not password and not dev:
        raise RuntimeError(
            "DASHBOARD_PASSWORD is not set and DEV_MODE is off. Refusing to start "
            "an unauthenticated public server. Set DASHBOARD_PASSWORD (and "
            "SESSION_SECRET), or set DEV_MODE=1 for local development."
        )
    if password and not os.getenv("SESSION_SECRET", ""):
        raise RuntimeError("DASHBOARD_PASSWORD is set but SESSION_SECRET is missing.")


def verify_credentials(username: Optional[str], password: str) -> Optional[str]:
    """Return a subject (user id) on success, else None.

    Tier C seam: replace this body with a users-table lookup. Callers only
    rely on the (username, password) -> subject contract.
    """
    expected = os.getenv("DASHBOARD_PASSWORD", "")
    if expected and hmac.compare_digest(password or "", expected):
        return username or "admin"
    return None


def _secret() -> bytes:
    return os.getenv("SESSION_SECRET", "").encode()


def sign_session(subject: str, ttl: int = _DEFAULT_TTL) -> str:
    now = int(time.time())
    payload = {"sub": subject, "iat": now, "exp": now + ttl}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_session(token: Optional[str]) -> Optional[str]:
    if not token or "." not in token:
        return None
    raw, _, sig = token.rpartition(".")
    expected = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload.get("sub")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd algo-trade && python -m pytest tests/test_auth.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 5: Commit**

```bash
git add algo-trade/src/api_server/auth.py algo-trade/tests/test_auth.py
git commit -m "feat(auth): stdlib HMAC session + credential helpers with Tier-C seam"
```

---

## Task 2: Wire error + auth middlewares into the server

**Files:**
- Modify: `algo-trade/src/api_server/server.py` (imports near line 19-24; router/app block at lines 1567-1587)
- Modify: `algo-trade/tests/e2e/conftest.py` (top of file — set `DEV_MODE`)
- Test: `algo-trade/tests/e2e/auth.spec.py` (create)

**Interfaces:**
- Consumes from Task 1: `auth.auth_enabled`, `auth.assert_auth_config`, `auth.verify_credentials`, `auth.sign_session`, `auth.verify_session`, `auth.COOKIE_NAME`, `auth.EXEMPT_PATHS`.
- Produces: middlewares `error_middleware`, `auth_middleware`; handlers `login_page` (`GET /login`), `do_login` (`POST /login`), `do_logout` (`POST /logout`); `web.Application(middlewares=[error_middleware, auth_middleware])`.

- [ ] **Step 1: Make existing E2E suite auth-agnostic**

At the very top of `algo-trade/tests/e2e/conftest.py`, immediately after the module docstring and before other imports take effect, add:

```python
import os
os.environ.setdefault("DEV_MODE", "1")  # existing specs run with auth disabled
```

- [ ] **Step 2: Write failing auth E2E tests**

Create `algo-trade/tests/e2e/auth.spec.py`:

```python
# file: tests/e2e/auth.spec.py
"""E2E tests for the auth middleware + login flow."""
from __future__ import annotations

import os
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
        async with TestClient(TestServer(auth_app())) as client:
            resp = await client.get("/health")
            assert resp.status == 200

    async def test_api_route_401_without_session(self, auth_app):
        async with TestClient(TestServer(auth_app())) as client:
            resp = await client.get("/signals", headers={"Accept": "application/json"})
            assert resp.status == 401

    async def test_dashboard_redirects_to_login(self, auth_app):
        async with TestClient(TestServer(auth_app())) as client:
            resp = await client.get("/", headers={"Accept": "text/html"},
                                    allow_redirects=False)
            assert resp.status == 302
            assert resp.headers["Location"] == "/login"

    async def test_login_page_accessible(self, auth_app):
        async with TestClient(TestServer(auth_app())) as client:
            resp = await client.get("/login")
            assert resp.status == 200
            assert "password" in (await resp.text()).lower()


class TestLoginFlow:
    async def test_wrong_password_rejected(self, auth_app):
        async with TestClient(TestServer(auth_app())) as client:
            resp = await client.post("/login", data={"password": "wrong"},
                                     allow_redirects=False)
            assert resp.status == 401

    async def test_correct_password_sets_cookie_and_unlocks(self, auth_app):
        async with TestClient(TestServer(auth_app())) as client:
            resp = await client.post("/login", data={"password": "hunter2"},
                                     allow_redirects=False)
            assert resp.status == 302
            # cookie persists on the client jar → protected route now works
            ok = await client.get("/signals", headers={"Accept": "application/json"})
            assert ok.status == 200

    async def test_logout_clears_session(self, auth_app):
        async with TestClient(TestServer(auth_app())) as client:
            await client.post("/login", data={"password": "hunter2"})
            await client.post("/logout")
            resp = await client.get("/signals", headers={"Accept": "application/json"})
            assert resp.status == 401


class TestAuthDisabled:
    async def test_open_when_no_password(self, make_app, monkeypatch):
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        monkeypatch.setenv("DEV_MODE", "1")
        async with TestClient(TestServer(make_app())) as client:
            resp = await client.get("/signals", headers={"Accept": "application/json"})
            assert resp.status == 200
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd algo-trade && python -m pytest tests/e2e/auth.spec.py -v`
Expected: FAIL — `/login` 404 / no auth enforced (404 or 200 where 401/302 expected).

- [ ] **Step 4: Add the auth import to `server.py`**

In `algo-trade/src/api_server/server.py`, after the existing `from src.market_hours import ...` import (around line 24), add:

```python
from src.api_server import auth as _auth
```

- [ ] **Step 5: Add middlewares + login/logout handlers inside `create_app`**

In `server.py`, immediately **before** the `# ── Router ──` comment / `app = web.Application()` line (~1565), add:

```python
    # ── Middlewares ───────────────────────────────────────────────────────
    @web.middleware
    async def error_middleware(request: web.Request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — last-resort guard
            log.error(
                "unhandled_request_error",
                method=request.method,
                path=request.path,
                error=str(exc),
            )
            return web.json_response({"error": "internal server error"}, status=500)

    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        if not _auth.auth_enabled() or request.path in _auth.EXEMPT_PATHS:
            return await handler(request)
        subject = _auth.verify_session(request.cookies.get(_auth.COOKIE_NAME))
        if subject is not None:
            return await handler(request)
        if "text/html" in request.headers.get("Accept", ""):
            raise web.HTTPFound("/login")
        return web.json_response({"error": "unauthorized"}, status=401)

    def _login_html(error: str = "") -> str:
        msg = f'<p class="err">{_html.escape(error)}</p>' if error else ""
        return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · AlgoTrade</title>
<style>body{{font-family:system-ui,sans-serif;background:#06070A;color:#E7EAF3;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}}
form{{background:rgba(255,255,255,.04);padding:32px;border:1px solid rgba(255,255,255,.1);
border-radius:16px;width:300px}}h1{{font-size:18px;margin:0 0 4px}}
p.sub{{color:#8A90A6;font-size:13px;margin:0 0 20px}}input{{width:100%;padding:11px 13px;
border-radius:10px;border:1px solid rgba(255,255,255,.14);background:#0d0f15;color:#E7EAF3;
font-size:14px;box-sizing:border-box}}button{{width:100%;margin-top:14px;padding:11px;
border:0;border-radius:10px;background:#5BA8FF;color:#06070A;font-weight:700;cursor:pointer}}
p.err{{color:#FF5D73;font-size:13px;margin:12px 0 0}}</style></head>
<body><form method="post" action="/login"><h1>AlgoTrade</h1>
<p class="sub">📄 Paper-trading dashboard — sign in</p>
<input type="password" name="password" placeholder="Password" autofocus required>
<button type="submit">Sign in</button>{msg}</form></body></html>"""

    async def login_page(request: web.Request) -> web.Response:
        return web.Response(text=_login_html(), content_type="text/html")

    async def do_login(request: web.Request) -> web.Response:
        data = await request.post()
        subject = _auth.verify_credentials(None, str(data.get("password", "")))
        if subject is None:
            return web.Response(
                text=_login_html("Incorrect password."),
                content_type="text/html",
                status=401,
            )
        resp = web.HTTPFound("/")
        secure = not _auth._truthy(os.getenv("DEV_MODE"))
        resp.set_cookie(
            _auth.COOKIE_NAME, _auth.sign_session(subject),
            httponly=True, secure=secure, samesite="Lax", max_age=7 * 24 * 3600,
        )
        return resp

    async def do_logout(request: web.Request) -> web.Response:
        resp = web.HTTPFound("/login")
        resp.del_cookie(_auth.COOKIE_NAME)
        return resp

```

- [ ] **Step 6: Register middlewares + routes**

In `server.py`, change the app construction line (currently `app = web.Application()` at ~1567) to:

```python
    app = web.Application(middlewares=[error_middleware, auth_middleware])
```

Then, in the router block, add these three lines right after the existing `app.router.add_get("/health", health)` line:

```python
    app.router.add_get("/login",            login_page)
    app.router.add_post("/login",           do_login)
    app.router.add_post("/logout",          do_logout)
```

- [ ] **Step 7: Add the fail-fast boot check in `main.py`**

In `algo-trade/src/cli/main.py`, immediately before the `app = create_app(...)` call (line ~152), add:

```python
    from src.api_server import auth as _auth
    _auth.assert_auth_config()
```

- [ ] **Step 8: Run the full E2E + unit suite**

Run: `cd algo-trade && python -m pytest tests/e2e/auth.spec.py tests/e2e/health.spec.py tests/e2e/signals.spec.py tests/test_auth.py -v`
Expected: PASS — new auth tests pass AND existing health/signals specs still pass (auth off via `DEV_MODE`).

- [ ] **Step 9: Commit**

```bash
git add algo-trade/src/api_server/server.py algo-trade/src/cli/main.py algo-trade/tests/e2e/conftest.py algo-trade/tests/e2e/auth.spec.py
git commit -m "feat(auth): gate dashboard + API with session middleware, login/logout, fail-fast boot"
```

---

## Task 3: UX polish — banner, loading/empty/error states, responsive, about

**Files:**
- Modify: `algo-trade/src/api_server/server.py` (dashboard f-string, ~235-913)
- Test: `algo-trade/tests/e2e/dashboard.spec.py` (add assertions)

**Interfaces:**
- Consumes: existing `dashboard` handler returning `web.Response(text=html, content_type="text/html")`.
- Produces: dashboard HTML now contains the verbatim banner string, an `id="about-panel"` section, a `.state-msg` helper class, and a `@media (max-width:760px)` block. No signature changes.

- [ ] **Step 1: Write failing dashboard content tests**

In `algo-trade/tests/e2e/dashboard.spec.py`, add this class (keep existing tests):

```python
class TestDashboardPublishPolish:
    async def test_paper_banner_present(self, make_app):
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(make_app())) as client:
            html = await (await client.get("/")).text()
            assert "📄 PAPER TRADING — simulated orders, not financial advice" in html

    async def test_about_panel_present(self, make_app):
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(make_app())) as client:
            html = await (await client.get("/")).text()
            assert 'id="about-panel"' in html

    async def test_responsive_media_query_present(self, make_app):
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(make_app())) as client:
            html = await (await client.get("/")).text()
            assert "@media (max-width:760px)" in html

    async def test_state_helper_present(self, make_app):
        from aiohttp.test_utils import TestClient, TestServer
        async with TestClient(TestServer(make_app())) as client:
            html = await (await client.get("/")).text()
            assert "renderState" in html  # JS loading/empty/error helper
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd algo-trade && python -m pytest tests/e2e/dashboard.spec.py::TestDashboardPublishPolish -v`
Expected: FAIL — strings not found in dashboard HTML.

- [ ] **Step 3: Add the banner**

In the dashboard f-string in `server.py`, find the opening `<body ...>` content (the first element rendered inside `<body>`). Insert this banner as the first child of `<body>` (note doubled braces inside the f-string):

```html
<div class="paper-banner">📄 PAPER TRADING — simulated orders, not financial advice</div>
```

And add to the `<style>` block (before the closing `</style>`):

```css
.paper-banner{{position:sticky;top:0;z-index:50;text-align:center;padding:7px 12px;
  font-family:var(--f-ui);font-size:12.5px;font-weight:700;letter-spacing:.3px;
  color:#06070A;background:linear-gradient(90deg,var(--yellow),var(--teal));}}
```

- [ ] **Step 4: Add the about panel**

Immediately after the banner `<div>`, insert:

```html
<section id="about-panel">
  <strong>AlgoTrade</strong> — an educational, event-driven options paper-trading demo.
  It scans the market, generates RSI/MACD momentum signals, and simulates trades with
  ATR-based risk. No real orders are placed. Not financial advice.
</section>
```

And in `<style>`:

```css
#about-panel{{max-width:1100px;margin:14px auto 0;padding:12px 16px;font-size:13px;
  color:var(--muted);background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r);line-height:1.55;}}
#about-panel strong{{color:var(--text);}}
```

- [ ] **Step 5: Add the responsive media query**

Before `</style>`, add:

```css
@media (max-width:760px){{
  body{{font-size:13px;}}
  #about-panel{{margin:12px;}}
  .paper-banner{{font-size:11px;}}
  table{{display:block;overflow-x:auto;white-space:nowrap;}}
}}
```

> Note: if the dashboard already has a sidebar with a fixed width, also add inside this media query: `.sidebar{{display:none;}}` (verify the actual sidebar class name in the HTML first and use it).

- [ ] **Step 6: Add the JS loading/empty/error helper**

In the dashboard's `<script>` block, add this helper function near the other JS helpers (e.g. right after the `$ = id => document.getElementById(id)` style helper, or at the top of the script):

```javascript
function renderState(el, state, msg){{
  if(!el) return;
  const m = {{loading:'Loading…', empty: msg||'No data yet.', error: msg||'Failed to load.'}};
  el.innerHTML = '<div class="state-msg state-'+state+'">'+m[state]+'</div>';
}}
```

And in `<style>`:

```css
.state-msg{{padding:18px;text-align:center;color:var(--dim);font-size:13px;}}
.state-msg.state-error{{color:var(--red);}}
```

Then wrap the **signals** and **positions** fetch calls to use it. Locate each `fetch('/signals')`/`fetch('/positions')` (or equivalent) data-loading function and apply this pattern (adapt the container id and existing render call to the actual code):

```javascript
async function loadSignals(){{
  const box = $('signals-body') || $('signals');   // use the real container id
  renderState(box, 'loading');
  try{{
    const r = await fetch('/signals');
    if(!r.ok) throw new Error(r.status);
    const rows = await r.json();
    if(!rows || rows.length === 0){{ renderState(box, 'empty', 'No signals yet — scanner is warming up.'); return; }}
    /* existing render logic that fills `box` with rows */
  }}catch(e){{ renderState(box, 'error', 'Could not load signals.'); }}
}}
```

> Apply the same loading→empty→error wrapping to the positions loader. Keep each panel's existing successful-render logic; only add the loading/empty/error branches around it.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd algo-trade && python -m pytest tests/e2e/dashboard.spec.py -v`
Expected: PASS (new polish tests + existing dashboard tests).

- [ ] **Step 8: Manually verify rendering (sanity)**

Run: `cd algo-trade && DEV_MODE=1 python -m src.cli.main --mode paper` (PowerShell: `$env:DEV_MODE=1; python -m src.cli.main --mode paper`), open `http://localhost:8181/`, confirm banner is visible, about panel renders, and resizing the window narrow stacks the layout. Stop with Ctrl+C.

- [ ] **Step 9: Commit**

```bash
git add algo-trade/src/api_server/server.py algo-trade/tests/e2e/dashboard.spec.py
git commit -m "feat(dashboard): paper banner, about panel, loading/empty/error states, responsive layout"
```

---

## Task 4: Deploy hardening + Supabase persistence (`render.yaml`)

**Files:**
- Modify: `render.yaml`

**Interfaces:**
- Consumes: existing persistence (`src/persistence.py` already reads `DATABASE_URL`). No code change.
- Produces: a Render blueprint that deploys from `main` with `DASHBOARD_PASSWORD`, `SESSION_SECRET`, `DATABASE_URL` set in the dashboard (`sync: false`).

- [ ] **Step 1: Update the branch**

In `render.yaml`, change:

```yaml
    branch: test
```
to:
```yaml
    branch: main
```

- [ ] **Step 2: Add the secret env vars to the blueprint**

In `render.yaml`, under the existing `envVars:` list, append:

```yaml
      # Set these in the Render dashboard — never commit real values.
      - key: DASHBOARD_PASSWORD
        sync: false
      - key: SESSION_SECRET
        sync: false
      - key: DATABASE_URL          # Supabase: postgresql://...@db.<ref>.supabase.co:5432/postgres?sslmode=require
        sync: false
```

- [ ] **Step 3: Verify the blueprint parses**

Run: `python -c "import yaml,sys; yaml.safe_load(open('render.yaml')); print('render.yaml OK')"`
Expected: `render.yaml OK`

- [ ] **Step 4: Commit**

```bash
git add render.yaml
git commit -m "chore(deploy): publish from main; add DASHBOARD_PASSWORD/SESSION_SECRET/DATABASE_URL (Supabase) env"
```

> **Manual deploy steps (performed by the operator, not in code):**
> 1. Create a Supabase project; copy the **direct** connection string (host `db.<ref>.supabase.co`, port `5432`), append `?sslmode=require`.
> 2. In Render → service → Environment, set `DATABASE_URL` to it, `DASHBOARD_PASSWORD` to a long random value, `SESSION_SECRET` to another long random value.
> 3. Deploy. Confirm: visiting the URL shows `/login`; after login the dashboard loads; the 6 tables (`positions`, `cooldowns`, `signals`, `strategy_performance`, `actions`, `config_overrides`) appear in Supabase; a paper order placed before a manual restart is still present after it.

---

## Task 5: README for publish credibility

**Files:**
- Modify: `algo-trade/README.md` (top section, lines 1-30)

**Interfaces:** none (docs only).

- [ ] **Step 1: Add a live-demo block under the title**

In `algo-trade/README.md`, immediately after the `# Algo-Trade: Algorithmic Options Trading System` title and the existing legal disclaimer block, insert:

```markdown
## 🚀 Live Demo

**Live dashboard:** <https://algo-trade-dashboard.onrender.com> &nbsp;·&nbsp; paper-trading only

![AlgoTrade dashboard](docs/screenshot-dashboard.png)

> The live demo runs in **paper mode** — orders are simulated, no real money moves.
> Access is password-protected. The free Render dyno may take ~30s to wake on first hit.

### Deploy your own

1. Fork this repo and connect it to Render (the included `render.yaml` is a one-click Blueprint).
2. Create a free Supabase project and copy its **direct** connection string (port `5432`, add `?sslmode=require`).
3. In Render, set env vars: `DATABASE_URL` (Supabase), `DASHBOARD_PASSWORD` (long random), `SESSION_SECRET` (long random).
4. Deploy. Optionally add a free uptime monitor pinging `/health` every ~10 min to avoid cold starts.
```

- [ ] **Step 2: Capture the screenshot**

With the dashboard running locally (Task 3, Step 8), take a screenshot of the dashboard and save it to `algo-trade/docs/screenshot-dashboard.png`. (If deferring the screenshot, leave the image line — it renders as alt text until the file exists.)

- [ ] **Step 3: Commit**

```bash
git add algo-trade/README.md algo-trade/docs/screenshot-dashboard.png
git commit -m "docs(readme): live demo URL, screenshot, deploy-your-own guide"
```

---

## Self-Review Notes

- **Spec §3.1 Auth** → Task 1 (helpers) + Task 2 (middleware, login/logout, fail-fast). Exempt `/health`/`/login`/`/logout` ✓. JSON 401 vs HTML redirect ✓. Tier-C seam in `verify_credentials` ✓.
- **Spec §3.2 Supabase** → Task 4 (set `DATABASE_URL`, direct conn + `sslmode=require`, no code change) ✓.
- **Spec §3.3 Deploy hardening** → Task 4 (branch→main, 3 `sync:false` env, keep-alive documented in Task 5) ✓.
- **Spec §3.4 UX polish** → Task 3 (banner, loading/empty/error, responsive, about) ✓.
- **Spec §3.5 Observability + README** → Task 2 (`error_middleware`) + Task 5 (README) ✓.
- **Acceptance criteria 1-7** all map to tests in Tasks 1-3 or manual deploy steps in Task 4 ✓.
- Type consistency: `verify_credentials`/`sign_session`/`verify_session`/`COOKIE_NAME`/`EXEMPT_PATHS`/`auth_enabled`/`assert_auth_config` defined in Task 1 and used with identical names in Task 2 ✓. `_truthy` is referenced in Task 2 Step 5 (`_auth._truthy`) and defined in Task 1 ✓.
- Known soft spots for the implementer: Task 3 JS edits must adapt to the **actual container ids / loader function names** in the existing dashboard script — the test only asserts the helper + markers exist, so the implementer must read the surrounding JS before wiring loaders.
