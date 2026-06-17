# Tier B — Publish algo-trade as a Live Web App

**Date:** 2026-06-18
**Status:** Approved design — ready for implementation plan
**Scope:** Tier B (ship-safe + presentable). Tier C (multi-user/billing) is the long-term goal; this design avoids choices that block it.

---

## 1. Goal

Make the `algo-trade` paper-trading dashboard safe and presentable to deploy as a public live web app on Render. Currently the deployed app (Python aiohttp server, embedded dashboard) has **no authentication** and runs on Render's free tier where **state is wiped on every restart**. This design fixes the publish blockers and adds the polish that makes a first-time visitor trust the page.

**Architecture decision (settled):** Keep the embedded dashboard served by `src/api_server/server.py`. The separate Next.js app in `frontend/` is stale (all recent UI work went into `server.py`) and is **out of scope** — it is neither deployed nor modified here.

**Database decision (settled):** Supabase Postgres via `DATABASE_URL`. No persistence code changes required.

---

## 2. Current State (verified)

- `render.yaml` deploys a single Docker web service from branch `test`, `MODE=paper`, free plan, health check at `/health`.
- `src/api_server/server.py` (~85KB) serves the embedded dashboard at `/`, a REST API, and an SSE stream at `/stream`.
- **Auth:** only `POST /config` is optionally gated by `CONFIG_API_KEY`. All other routes — including `POST /order`, `POST /reset`, `GET /config` (returns masked secrets), `POST /backtest/run` — are fully open.
- **Persistence:** `src/persistence.py` uses SQLAlchemy. `__init__` reads `database.url` from config (which falls back to `os.getenv("DATABASE_URL", "sqlite:///data/algo_trade.db")`), already branches on non-SQLite URLs to add pool settings + `pool_pre_ping`, and runs `Base.metadata.create_all()` on startup. Six tables: `positions`, `cooldowns`, `signals`, `strategy_performance`, `actions`, `config_overrides`. `psycopg2-binary` is already a dependency.
- On Render free tier the SQLite file lives on an ephemeral disk → lost on every redeploy/restart.

---

## 3. Design

### 3.1 Authentication (blocker #1)

A single aiohttp `@web.middleware` gates the entire application.

- **Behavior:**
  - Request without a valid session → HTML/page routes redirect (302) to `/login`; API/JSON routes return `401 {"error":"unauthorized"}`.
  - `GET /login` renders a minimal single-password login page (styled to match the dashboard theme).
  - `POST /login` compares the submitted password against env `DASHBOARD_PASSWORD` using `hmac.compare_digest`. On success, set a signed session cookie and redirect to `/`.
  - `POST /logout` clears the cookie.
- **Exempt routes (no auth):** `/health` only (Render health check must stay reachable). `/login` and `/logout` are inherently accessible.
- **Session cookie:** stdlib-only signed token — `base64(payload).hmac_sha256(SESSION_SECRET)` — set with `HttpOnly`, `Secure`, `SameSite=Lax`, and an expiry (e.g. 7 days). **No new dependency** (avoids pulling in `aiohttp-session`/`cryptography`). Cookie carries an issued-at timestamp; middleware rejects expired or tampered tokens.
- **Config endpoint:** keep the existing `CONFIG_API_KEY` check on `POST /config` as defense-in-depth (it still works for programmatic callers); session auth now covers it for browser use.
- **Tier-C seam:** all credential logic lives in a new `src/api_server/auth.py` exposing:
  - `verify_credentials(username: str | None, password: str) -> str | None` → returns a user id/subject on success, else `None`. Today: ignores username, checks the shared `DASHBOARD_PASSWORD`. Later: swap the body for a `users` table lookup — middleware, cookie signing, and route code do not change.
  - `sign_session(subject) -> str`, `verify_session(token) -> subject | None`.
- **Required env:** `DASHBOARD_PASSWORD` and `SESSION_SECRET` are **always required** — the app refuses to start (fail-fast on boot) if either is unset, so a public deploy can never accidentally launch unauthenticated. The only exception is local development, where a `DEV_MODE`/local flag may supply safe defaults and a non-`Secure` cookie.

### 3.2 Supabase Postgres persistence

- Set `DATABASE_URL` to the Supabase **direct** connection string (host `db.<ref>.supabase.co`, port `5432`, session mode) with `sslmode=require`. The direct connection suits this long-lived server with its own SQLAlchemy pool; the 6543 transaction pooler is for serverless and is not used.
- No code change: `src/persistence.py` already auto-creates all tables on first boot against Postgres.
- Provision the Supabase project/DB (via Supabase MCP or dashboard) as an implementation step; capture the connection string into Render env (never committed).
- Verify: on first deploy, confirm the 6 tables appear in Supabase and that a placed paper order survives a service restart.

### 3.3 Deploy hardening (`render.yaml`)

- Change `branch: test` → `branch: main`.
- Add env vars to the blueprint, all `sync: false` (set in Render dashboard, never committed):
  - `DASHBOARD_PASSWORD`, `SESSION_SECRET`, `DATABASE_URL`.
- Keep `MODE=paper`, `PYTHONUNBUFFERED=1`, existing health check.
- **Keep-alive:** document an external uptime ping (e.g. a free uptime monitor or GitHub Action cron) hitting `/health` every ~10 min so the free dyno does not cold-start visitors into a ~30s spinner. (External config, not committed secrets.)

### 3.4 UX polish (embedded dashboard in `server.py`)

- **Paper-trading banner:** persistent, always-visible top banner: "📄 PAPER TRADING — simulated orders, not financial advice." Cannot be dismissed.
- **Loading / empty / error states:** every data panel (signals, positions, history, metrics, strategies) shows a loading indicator while fetching, a friendly empty state when the API returns no rows, and an error state when a fetch fails — instead of rendering blank/broken.
- **Mobile/responsive:** dashboard layout works on a phone viewport (sidebar collapses, tables scroll/stack, banner stays readable).
- **Landing / about:** a short "what is this" section (above the fold on `/`, or a small `/about` panel) so a first-time visitor immediately understands the system is an educational paper-trading demo.

### 3.5 Observability + credibility

- **Error-logging middleware:** wrap request handling so unhandled exceptions are logged via the existing structured logger (`src/logger`) with method/path/status, and return a clean JSON/HTML error rather than a stack trace. Ordering: error middleware outermost, auth middleware inside it.
- **README:** rewrite `algo-trade/README.md` top section with the live URL, a screenshot of the dashboard, the paper-trading disclaimer, and a concise "deploy your own" pointer to `render.yaml` + required env vars.

---

## 4. Out of Scope (Tier C, deferred)

Multi-user accounts/registration, billing, rate limiting, analytics, custom domain, the Next.js `frontend/` app, real-money/live broker hardening. The auth design leaves a clean seam for accounts (3.1) but implements none of it now.

---

## 5. Risks / Notes

- **Supabase free tier** pauses a project after ~7 days of inactivity; the keep-alive ping (3.3) also keeps the DB warm, or the project can be unpaused from the dashboard. Acceptable for a demo.
- **`DASHBOARD_PASSWORD` strength** is the entire security boundary for Tier B — must be a long random value, set only in Render env.
- **Backtest endpoint** (`POST /backtest/run`) can be CPU-heavy; behind auth it is no longer an open abuse vector. Rate limiting deferred to Tier C.
- Cookie `Secure` flag requires HTTPS — fine on Render (TLS by default); for local dev over HTTP, allow a non-secure cookie when `MODE`/a dev flag indicates local.

---

## 6. Acceptance Criteria

1. Visiting the deployed URL unauthenticated shows the login page; no dashboard data or API JSON is reachable without logging in (except `/health`).
2. Correct `DASHBOARD_PASSWORD` logs in and persists across reloads; wrong password is rejected; logout clears the session.
3. `DATABASE_URL` points at Supabase; all 6 tables auto-create; a paper order placed before a service restart is still present after it.
4. `render.yaml` deploys from `main` with the three new env vars set in the dashboard.
5. Every dashboard data panel shows loading/empty/error states; the paper-trading banner is always visible; layout is usable on mobile.
6. Unhandled server errors are logged structurally and do not leak stack traces to the client.
7. README shows the live URL + screenshot + disclaimer.
