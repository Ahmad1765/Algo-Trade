# Ship the Next.js Frontend (Static Export served by Python) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the polished Next.js app in `algo-trade/frontend` the UI that the production Docker container actually serves, and delete the legacy inline-HTML dashboard from `server.py`.

**Architecture:** Build the Next.js app as a *static export* (`output: 'export'`) at Docker build time in a Node stage. The existing Python aiohttp server serves those static files from `/app/web`, gates HTML pages behind the existing auth cookie, exposes the JSON API under both `/*` (unchanged, for the Docker healthcheck) and `/api/*` (what the frontend client calls), and redirects `/` → `/dashboard/`. No Node process at runtime — the container stays Python-only.

**Tech Stack:** Next.js 16 (App Router, static export) · React 19 · Tailwind v4 · Python 3.11 · aiohttp · pytest + aiohttp test utils · Docker multi-stage.

## Global Constraints

- Work on branch `test` (current deploy branch). Do not touch `main`.
- Runtime container must remain **Python-only** — Node is a build stage only.
- **Never weaken auth:** every non-exempt HTML route must still require a valid session cookie when `DASHBOARD_PASSWORD` is set.
- Frontend API client base is `/api` (`frontend/lib/api.ts:4`, `const API_BASE = "/api"`) — do not change it; serve `/api/*` on the Python side instead so local `next dev` (which rewrites `/api/*`) keeps working unchanged.
- Keep `/health` reachable **without auth** at the root path — the Docker `HEALTHCHECK` (`Dockerfile:38-39`) calls `/health` directly.
- Files under 500 lines where practical; follow existing code style (structlog logging, `web.json_response`, type hints).
- Do not commit secrets. Do not commit build output (`frontend/out/`, `web/`).
- Verify the Next export build (`npm run build`) and the Python test suite pass before the final commit of each task.
- **Test conventions (verified):** `pytest.ini` sets `asyncio_mode = auto` — do **not** add `@pytest.mark.asyncio`; plain `async def test_*` is collected automatically. The `make_app` fixture is **only** in `tests/e2e/conftest.py` and is **not** visible to top-level `tests/*.py` — build the app inline with `create_app(risk_manager=MagicMock(), signal_store=[], position_store=None)` (the static/auth tests only exercise `/health`, `/api/health`, and static routes, none of which need a real risk manager). Run tests from the `algo-trade` dir.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `frontend/next.config.ts` | Build mode | Modify — enable `output: 'export'`, `trailingSlash`, `images.unoptimized` |
| `frontend/app/page.tsx` | Root route | Modify — client redirect to `/dashboard` (export has no config redirect) |
| `src/api_server/server.py` | API + static serving | Modify — add web-dir serving, `/api/*` aliases, root redirect, SPA handler; **delete** inline `dashboard()` |
| `src/api_server/auth.py` | Auth exemptions | Modify — exempt public static assets (`/_next/`, icon) |
| `tests/test_static_serving.py` | Static-serving tests | Create |
| `tests/test_auth_static.py` | Auth-vs-static tests | Create |
| `Dockerfile` | Image build | Modify — add Node build stage, copy `out/` → `web/` |
| `.dockerignore` | Build context hygiene | Create/Modify |
| `.gitignore` | Ignore build output | Modify |

---

### Task 1: Configure Next.js for static export

**Files:**
- Modify: `frontend/next.config.ts`
- Modify: `frontend/app/page.tsx`

**Interfaces:**
- Consumes: nothing (entry task).
- Produces: a `frontend/out/` directory after `npm run build`, containing `dashboard/index.html`, `positions/index.html`, `signals/index.html`, `status/index.html`, `strategies/index.html`, `backtest/index.html`, `settings/index.html`, `history/index.html`, `_next/static/...`, `icon.svg`, `404.html`. Task 2 (Python serving) and Task 4 (Docker) rely on this layout.

- [ ] **Step 1: Rewrite `frontend/next.config.ts`**

Replace the entire file with:

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static HTML export — served by the Python aiohttp server in production.
  output: "export",
  // Each route becomes <route>/index.html so a plain file server can resolve it.
  trailingSlash: true,
  // next/image optimization needs a server; disable for static export.
  images: { unoptimized: true },
  typescript: {
    // Auto-generated .next/types files conflict with TypeScript 5.9+.
    ignoreBuildErrors: true,
  },
  // NOTE: redirects()/rewrites() are intentionally omitted. They are ignored by
  // `output: export`. In production the Python server handles `/` → /dashboard/
  // and serves the JSON API under /api/*. For local `next dev`, run the Python
  // backend and use NEXT_PUBLIC_API_BASE if you need cross-origin calls.
};

export default nextConfig;
```

- [ ] **Step 2: Make the root route redirect client-side**

Replace `frontend/app/page.tsx` with:

```tsx
import { redirect } from "next/navigation";

// Root has no UI of its own — send users to the dashboard.
// (Static export can't use next.config redirects, so do it here.)
export default function HomePage() {
  redirect("/dashboard");
}
```

- [ ] **Step 3: Run the export build and verify output layout**

Run:
```bash
cd "algo-trade/frontend" && npm run build
```
Expected: build completes; `out/` exists. Verify key files:
```bash
ls out/dashboard/index.html out/_next out/icon.svg out/404.html
```
Expected: all paths listed, no "No such file".

Note: Next may print a warning that `redirects` are not applied to the export — expected and harmless (none are defined now).

- [ ] **Step 4: Commit**

```bash
git add frontend/next.config.ts frontend/app/page.tsx
git commit -m "feat(frontend): build Next.js as a static export"
```

---

### Task 2: Serve the static export from the Python server

**Files:**
- Modify: `src/api_server/server.py` (imports near line 23; route/handler block ~1664-1689; delete `dashboard()` ~236-942)
- Test: `tests/test_static_serving.py` (create)

**Interfaces:**
- Consumes: the `out/` layout from Task 1 (copied to the web dir at runtime by Task 4).
- Produces:
  - Module-level `def _web_dir() -> Path` — returns `Path(os.getenv("WEB_DIR") or (Path(__file__).resolve().parents[2] / "web"))`, read fresh each call so tests can override via env.
  - Inside `create_app`: handlers `root_redirect(request)` (302 → `/dashboard/`) and `spa_handler(request)` (serves a file from the web dir, falling back to `404.html`).
  - JSON API routes registered under **both** `"<path>"` and `"/api<path>"`.
  - The inline `dashboard()` handler and its route are removed.

- [ ] **Step 1: Write failing tests for static serving**

Create `tests/test_static_serving.py`:

```python
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
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:
```bash
cd algo-trade && python -m pytest tests/test_static_serving.py -v
```
Expected: FAIL — `/api/health` 404 (alias not registered), `/` returns the old dashboard not a 302, no `spa_handler`. (`/health` at root may already pass — that's fine.)

- [ ] **Step 3: Add the web-dir resolver and imports**

In `src/api_server/server.py`, ensure `Path` is imported. Near the existing imports (the `from aiohttp import web` line is at ~23), add at module top-level (after imports):

```python
from pathlib import Path

def _web_dir() -> Path:
    """Directory holding the exported Next.js site (read fresh for tests)."""
    return Path(os.getenv("WEB_DIR") or (Path(__file__).resolve().parents[2] / "web"))
```

(Confirm `import os` already exists — it does, used elsewhere. Add only if missing.)

- [ ] **Step 4: Delete the inline dashboard handler**

Remove the entire `async def dashboard(request: web.Request) -> web.Response:` function — the block starting at the `html = f"""<!DOCTYPE html> ...` assignment through `return web.Response(text=html, content_type="text/html")` (currently lines ~231-942). Keep `import html as _html` (still used by the login page).

- [ ] **Step 5: Add `root_redirect` and `spa_handler` inside `create_app`**

Add these two handlers inside `create_app` (alongside the other nested handlers, before the `# ── Router ──` section):

```python
    async def root_redirect(request: web.Request) -> web.Response:
        raise web.HTTPFound("/dashboard/")

    async def spa_handler(request: web.Request) -> web.Response:
        web_root = _web_dir().resolve()
        rel = request.path.lstrip("/")
        target = (web_root / rel).resolve()
        # Block path traversal outside the web root.
        if web_root != target and web_root not in target.parents:
            raise web.HTTPNotFound()
        # 1) exact file (assets like icon.svg, *.js under /_next/)
        if target.is_file():
            return web.FileResponse(target)
        # 2) route directory -> its index.html  (trailingSlash export layout)
        index = target / "index.html"
        if index.is_file():
            return web.FileResponse(index)
        # 3) <route>.html  (defensive: non-trailing-slash exports)
        html_file = web_root / (rel.rstrip("/") + ".html")
        if html_file.is_file() and (web_root in html_file.resolve().parents):
            return web.FileResponse(html_file)
        # 4) fallback to the exported 404 page
        notfound = web_root / "404.html"
        if notfound.is_file():
            return web.FileResponse(notfound, status=404)
        raise web.HTTPNotFound()
```

- [ ] **Step 6: Rewrite the router block to add `/api` aliases and the SPA catch-all**

Replace the router block (currently `app = web.Application(...)` through `app.router.add_get("/", dashboard)` and `return app`, ~1666-1689) with:

```python
    app = web.Application(middlewares=[error_middleware, auth_middleware])

    # Auth pages (root only — not under /api).
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", do_login)
    app.router.add_post("/logout", do_logout)

    # JSON API — registered at the root path (Docker healthcheck, back-compat)
    # AND under /api/* (what the frontend client calls). One source of truth.
    api_routes = [
        ("GET",  "/health",            health),
        ("GET",  "/signals",           get_signals),
        ("GET",  "/positions",         get_positions),
        ("GET",  "/metrics",           get_metrics),
        ("GET",  "/history",           get_history),
        ("GET",  "/status",            get_status),
        ("GET",  "/overview",          get_overview),
        ("GET",  "/quote/{symbol}",    get_quote),
        ("GET",  "/strategies",        get_strategies),
        ("POST", "/reset",             post_reset),
        ("POST", "/order",             post_order),
        ("POST", "/backtest/run",      run_backtest_endpoint),
        ("GET",  "/config",            get_config_endpoint),
        ("POST", "/config",            post_config_endpoint),
        ("POST", "/config/test-email", test_email_endpoint),
        ("GET",  "/circuit-breaker",   get_circuit_breaker),
        ("GET",  "/pending-signals",   get_pending_signals),
        ("GET",  "/stream",            sse_stream),
    ]
    for method, path, handler in api_routes:
        app.router.add_route(method, path, handler)
        app.router.add_route(method, "/api" + path, handler)

    # Static Next.js export.
    app.router.add_get("/", root_redirect)
    app.router.add_get("/{tail:.*}", spa_handler)
    return app
```

- [ ] **Step 7: Run the tests to confirm they pass**

Run:
```bash
cd algo-trade && python -m pytest tests/test_static_serving.py -v
```
Expected: PASS (6 passed).

- [ ] **Step 8: Run the existing suite to confirm no regressions**

Run:
```bash
cd algo-trade && python -m pytest tests/ -q
```
Expected: no new failures vs. baseline (the `/health`, `/signals`, etc. root routes still work; auth tests unaffected).

- [ ] **Step 9: Commit**

```bash
git add src/api_server/server.py tests/test_static_serving.py
git commit -m "feat(server): serve Next.js static export, add /api aliases, drop inline dashboard"
```

---

### Task 3: Keep auth correct for static assets

**Files:**
- Modify: `src/api_server/auth.py`
- Modify: `src/api_server/server.py` (`auth_middleware`, ~1610-1619)
- Test: `tests/test_auth_static.py` (create)

**Interfaces:**
- Consumes: `spa_handler`/route layout from Task 2; `_auth.EXEMPT_PATHS`, `_auth.auth_enabled`, `_auth.verify_session` from `auth.py`.
- Produces: `auth.is_public_asset(path: str) -> bool` in `auth.py`; `auth_middleware` calls it so `/_next/*`, `/icon.svg`, `/favicon.ico`, `/robots.txt` load without a session, while HTML pages still redirect to `/login`.

- [ ] **Step 1: Write failing auth tests**

Create `tests/test_auth_static.py`:

```python
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
```

- [ ] **Step 2: Run to confirm failure**

Run:
```bash
cd algo-trade && python -m pytest tests/test_auth_static.py -v
```
Expected: `test_next_assets_are_public` FAILS (asset request redirected to /login because auth is on and the path is not exempt).

- [ ] **Step 3: Add `is_public_asset` to `auth.py`**

In `src/api_server/auth.py`, after `EXEMPT_PATHS` (line ~20) add:

```python
_PUBLIC_ASSET_PREFIXES = ("/_next/",)
_PUBLIC_ASSET_FILES = {"/favicon.ico", "/icon.svg", "/robots.txt"}


def is_public_asset(path: str) -> bool:
    """Static assets that may load before/without a session."""
    return path in _PUBLIC_ASSET_FILES or any(
        path.startswith(p) for p in _PUBLIC_ASSET_PREFIXES
    )
```

- [ ] **Step 4: Use it in `auth_middleware`**

In `src/api_server/server.py`, change the first line of `auth_middleware` (currently `if not _auth.auth_enabled() or request.path in _auth.EXEMPT_PATHS:`) to:

```python
        if (
            not _auth.auth_enabled()
            or request.path in _auth.EXEMPT_PATHS
            or _auth.is_public_asset(request.path)
        ):
            return await handler(request)
```

- [ ] **Step 5: Run both auth and static suites**

Run:
```bash
cd algo-trade && python -m pytest tests/test_auth_static.py tests/test_static_serving.py tests/test_auth.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/api_server/auth.py src/api_server/server.py tests/test_auth_static.py
git commit -m "feat(auth): exempt public static assets from the session gate"
```

---

### Task 4: Build & ship the export in Docker

**Files:**
- Modify: `Dockerfile`
- Create: `.dockerignore` (at `algo-trade/.dockerignore` — build context root is `algo-trade`)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `npm run build` from Task 1 (produces `frontend/out/`), the `_web_dir()` runtime path from Task 2 (`/app/web`).
- Produces: a runtime image containing `/app/web` (the exported site) and the Python server.

- [ ] **Step 1: Add a Node build stage to the Dockerfile**

In `Dockerfile`, insert this stage **before** the `# Stage 1: Python dependencies` block (it becomes the first `FROM`):

```dockerfile
# Stage 0: Build the Next.js static export
FROM node:20-slim AS web-builder
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build
# Output: /web/out
```

- [ ] **Step 2: Copy the export into the runtime image**

In the `runtime` stage of `Dockerfile`, after the existing `COPY scripts/ ./scripts/` line (~27), add:

```dockerfile
COPY --from=web-builder /web/out ./web
```

Then ensure the `chown` line still covers it — the existing `RUN mkdir -p data logs && chown -R appuser:appuser /app` (line ~30) runs after this COPY, so `/app/web` is owned correctly. If the COPY is placed after that `chown`, move the COPY above it.

- [ ] **Step 3: Create `algo-trade/.dockerignore`**

```
frontend/node_modules
frontend/.next
frontend/out
node_modules
.venv
data/*.db
logs
**/__pycache__
```

- [ ] **Step 4: Ignore build output in git**

Append to `algo-trade/.gitignore` (create the lines if absent):

```
# Next.js static export build output
frontend/out/
# Exported site copied into the image at build time
/web/
```

- [ ] **Step 5: Verify the Docker build (if Docker is available locally)**

Run:
```bash
cd algo-trade && docker build -t algo-trade:plan-test -f Dockerfile .
```
Expected: build succeeds; both stages run; final image created.

If Docker is **not** available locally, skip and rely on Render's build — note this in the commit message and verify via the Render deploy log instead.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile .dockerignore .gitignore
git commit -m "build(docker): compile Next.js export in a Node stage and serve it from Python"
```

---

### Task 5: End-to-end smoke test & deploy verification

**Files:** none (verification only).

**Interfaces:** Consumes the full stack from Tasks 1-4.

- [ ] **Step 1: Build the export locally for the running server**

Run:
```bash
cd algo-trade/frontend && npm run build && cd .. && rm -rf web && cp -r frontend/out web
```
(Windows PowerShell equivalent: `Remove-Item -Recurse -Force web -ErrorAction SilentlyContinue; Copy-Item -Recurse frontend/out web`.)

- [ ] **Step 2: Run the server in dev mode and smoke-test routes**

Run (in one shell):
```bash
cd algo-trade && DEV_MODE=1 python -m src.cli.main --mode paper
```
In another shell:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8181/health        # 200
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" http://localhost:8181/   # 302 .../dashboard/
curl -s http://localhost:8181/dashboard/ | grep -o "<title>[^<]*"             # dashboard HTML
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8181/api/metrics    # 200
```
Expected: health 200, `/` 302 → `/dashboard/`, dashboard HTML returned, `/api/metrics` 200.

- [ ] **Step 3: Verify auth gate with a password set**

Stop the dev server. Run:
```bash
cd algo-trade && DASHBOARD_PASSWORD=secret SESSION_SECRET=test python -m src.cli.main --mode paper
```
Then:
```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Accept: text/html" http://localhost:8181/dashboard/  # 302 (to /login)
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8181/_next/static/  # not a redirect to /login
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8181/health  # 200
```
Expected: dashboard 302, `_next` not gated, health 200.

- [ ] **Step 4: Confirm the Render service settings**

In the Render dashboard for the deploying service, confirm (these mirror `render.yaml`):
- Branch: `test`
- Root Directory: `algo-trade`
- Dockerfile Path: `algo-trade/Dockerfile`

Then **Manual Deploy → Clear build cache & deploy**. Watch the log for: Node stage runs `npm ci`/`npm run build`, Python stage builds, healthcheck passes.

- [ ] **Step 5: Verify the live site**

Open the Render URL → expect redirect to `/login` (password set) → sign in → the Next.js dashboard renders with the redesigned UI. Click through Signals / Positions / Settings.

- [ ] **Step 6: Final cleanup commit (if any local web/ artifacts were created)**

```bash
git status   # ensure web/ and frontend/out/ are untracked/ignored
git add -A && git commit -m "chore: ship Next.js frontend as the served dashboard" --allow-empty
```

---

## Self-Review

**Spec coverage:**
- Static export → Task 1. ✓
- Python serves files + `/api` alias + `/` redirect + delete inline dashboard → Task 2. ✓
- Auth exempts static assets, still gates HTML → Task 3. ✓
- Dockerfile Node stage + copy + ignores → Task 4. ✓
- Deploy fix (Root Directory / Dockerfile Path / branch) → Task 5 Step 4. ✓
- Frontend API base unchanged (`/api`) honored by server aliases → Task 2 Step 6. ✓
- Healthcheck `/health` stays public/root → Task 2 (root route kept) + Task 3 (exempt). ✓

**Placeholder scan:** No TBD/TODO; every code step contains complete code; every command has an expected result.

**Type/name consistency:** `_web_dir()` defined in Task 2 Step 3, used by `spa_handler` (Task 2 Step 5) and referenced conceptually in Task 4 (`/app/web`). `is_public_asset` defined in `auth.py` (Task 3 Step 3) and called as `_auth.is_public_asset` in `server.py` (Task 3 Step 4) — matches the existing `_auth.` import alias. `api_routes` handler names (`health`, `get_signals`, …) match the existing nested handler definitions in `create_app`.

**Known risk / rollback:** All changes are on branch `test` and are revertable by `git revert`. The single highest-risk change is the auth-middleware edit (Task 3 Step 4) — covered by `tests/test_auth_static.py` plus the existing `tests/test_auth.py`. If the Render deploy fails, the previous image keeps serving until the new one passes its healthcheck.

---

## Open question for the reviewer

The legacy `_login_html` login page (`server.py` ~1621-1637) still uses the old multi-color palette (`#5BA8FF` blue button, etc.) and an emoji. It is **out of scope** for this plan (it's Python-rendered, not part of the Next.js app). Flag for a follow-up: either restyle it to match the Next.js dark/emerald system, or move login into the Next.js app. Not blocking.
