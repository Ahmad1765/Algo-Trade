# Start/Stop Replay Sim from the Dashboard — Design

**Date:** 2026-06-22
**Status:** Approved (design), pending implementation plan
**Author:** brainstorming session
**Builds on:** `2026-06-22-replay-simulation-design.md` (SimClock, ReplayMarketAdapter,
data_loader, `/sim/status`, `/sim/control`, `scripts/simulate.py`).

## Problem

The replay simulation currently can only be **started from a terminal**
(`scripts/simulate.py`). The dashboard control (`SimControls`) can pause and
change the speed of a sim that is already running, but there is no way to
**start or stop** a sim from the UI. The user wants a "Start Simulation" panel
in the dashboard: pick a date + speed, click Start, watch it run, click Stop.

## Goals

- Start a replay sim from the dashboard (date + speed → Start), no terminal.
- Stop a running sim from the dashboard, returning to normal live/paper mode.
- Keep replay **non-destructive**: sim paper trades go to an isolated sandbox;
  real paper-trading positions/P&L/history in the main DB are never touched.
- Handle the slow first-load (~485 S&P symbols, 1–2 min) without blocking,
  with a visible loading state.

## Non-Goals

- No separate sim-server process — the **same running server** does live/paper
  and can switch into a sim and back (unified server, per the brainstorming
  decision).
- No multi-sim concurrency — at most one pipeline runs at a time.
- No new market-data source; reuse `data_loader.load_day` and the S&P 500 universe.
- No persistence of sim results beyond the running session (sandbox is ephemeral).

## Key Decisions (from brainstorming)

| Decision | Choice |
|----------|--------|
| Server model | **One unified server** — tear down live pipeline, run sim, rebuild live on stop |
| Sim data isolation | **Isolated sandbox** — sim trades go to an ephemeral `PositionStore`; real DB untouched |
| Start while market closed | Already handled — sim drives a `SimClock`; engine trading-hours is sim-aware |
| Loading UX | **Async with status states** (idle → loading → running → stopping → idle; plus error) |

## Architectural Core

Today every API endpoint closes over **fixed** references captured at
`create_app(...)` time: `position_store`, `market_adapter`, `strategy_engine`,
`signal_store`, `action_store`, `sim_clock`. To switch into a sim and back,
those references must become **swappable** at runtime.

Two new units:

### `RuntimeContext` (`src/runtime/context.py`)
A mutable holder of the active pipeline's components:
- `position_store`, `market_adapter`, `strategy_engine`, `signal_store`,
  `action_store`, `sim_clock`, `mode` (`"live"` | `"sim"`).
- Endpoints read `ctx.position_store` (etc.) **live** instead of closed-over
  locals, so a swap is observed immediately by all handlers.

### `SessionManager` (`src/runtime/session_manager.py`)
Owns the running pipeline tasks and the start/stop lifecycle:
- `async start_sim(sim_date, speed)` — validates date; sets state `loading`;
  in a background task: `load_day(sim_date)` → build a **sim** `RuntimeContext`
  (sandbox `PositionStore` + `ReplayMarketAdapter` + `SimClock` + fresh
  `MultiStrategyEngine`/`OrderManager`/`Screener`/`OptionsFetcher`) → cancel the
  live tasks → start the sim tasks → repoint `ctx` → state `running`.
- `async stop_sim()` — cancel sim tasks (awaited), close sim adapter, rebuild the
  live/paper `RuntimeContext`, restart live tasks, repoint `ctx` → state `idle`.
- `status() -> dict` — `{state, sim_time, speed, paused, sim_date, error}`.
- Internal state machine: `idle | loading | running | stopping | error`.

At most one pipeline runs at a time.

## Components

| Unit | New/Change | Purpose |
|------|-----------|---------|
| `src/runtime/__init__.py` | new | package marker |
| `src/runtime/context.py` | new | `RuntimeContext` mutable component holder |
| `src/runtime/session_manager.py` | new | start/stop/status lifecycle + task management |
| `src/runtime/pipeline_builder.py` | new | extract pipeline wiring (build components + return startable coroutines) so both live and sim paths reuse it |
| `src/cli/main.py` | change | build live `RuntimeContext` via the builder; create `SessionManager`; pass both into `create_app`; run via the manager |
| `src/api_server/server.py` | change | endpoints read from `ctx`; add `POST /sim/start`, `POST /sim/stop`; extend `/sim/status` with lifecycle state; `/sim/control` operates on `ctx.sim_clock` |
| `src/persistence` (reuse) | — | sandbox = `PositionStore` on an ephemeral temp-file SQLite DB; no new persistence code |
| `frontend/components/dashboard/sim-launcher.tsx` | new | Start Simulation panel: date input + speed select + Start/Stop + loading state |
| `frontend/lib/api.ts` | change | `simStart(date, speed)`, `simStop()`; extend `SimStatus` with lifecycle fields |
| `frontend/components/layout/topbar.tsx` (or dashboard page) | change | render the launcher; existing `SimControls` shows once running |

## Data Flow

```
idle ──POST /sim/start{date,speed}──▶ loading ──(load_day done, pipeline built)──▶ running
  ▲                                      │                                           │
  │                                 (fetch/cache ~485 syms,                    (sim pipeline live,
  │                                  build sim context+tasks)                   SimControls active)
  └──────────── POST /sim/stop ◀── stopping ◀──────────────────────────────────────┘
```

- `POST /sim/start` validates the date, flips state to `loading`, kicks off a
  background asyncio task, returns immediately (HTTP 202-style: returns current
  status).
- The launcher polls `/sim/status` (UI already polls every 1s) and renders:
  **Loading market data…** spinner → then the running `SimControls`.
- `POST /sim/stop` cancels sim tasks, rebuilds live, state back to `idle`.
- `/sim/status` reports `{state, sim_time, speed, paused, sim_date, error}` where
  `state ∈ {idle, loading, running, stopping, error}`.

## Sandbox Isolation

- When a sim starts, the sim `RuntimeContext` gets a **fresh `PositionStore`**
  bound to an ephemeral **temp-file** SQLite DB (a unique path under the OS temp
  dir, e.g. `sqlite:///<tempdir>/algo_sim_<uuid>.db`), deleted on stop. A
  temp-file (not `:memory:`) is required because `PositionStore` uses SQLAlchemy
  sessions across multiple connections, which an in-memory DB would not share.
  Same `PositionStore` class and logic — only the DB URL differs.
- All endpoints read `ctx.position_store`, so during a sim the dashboard shows
  **sim** positions/P&L; on stop, `ctx` repoints to the real store and the
  dashboard shows real data again.
- The real paper DB is never opened for writes by the sim pipeline.
- `signal_store` / `action_store` are per-context lists; the sim gets its own,
  discarded on stop.

## Endpoints

| Method | Path (+/api alias) | Body | Behaviour |
|--------|--------------------|------|-----------|
| POST | `/sim/start` | `{date: "YYYY-MM-DD", speed: number}` | 422 invalid/weekend/holiday date or bad speed; 409 if already running/loading; else state→loading, returns status |
| POST | `/sim/stop` | — | no-op 200 if idle; else cancel sim, rebuild live, state→idle |
| GET | `/sim/status` | — | `{state, sim_time, speed, paused, sim_date, error}` (extends current payload; `active` kept = `state=="running"` for back-compat) |
| POST | `/sim/control` | `{action, speed?}` | unchanged; operates on `ctx.sim_clock`; 409 when no sim running |

## Frontend

- `sim-launcher.tsx`: a small panel (dashboard page or topbar) with a date field
  (default `2026-06-17`), a speed select (`1x/10x/60x/Max`→`1/10/60/600`), and a
  primary button that is **Start** when `state==="idle"`, a **spinner +
  "Loading market data…"** when `state==="loading"`, and **Stop** when
  `state==="running"`. Shows `error` text when `state==="error"`.
- `lib/api.ts`: `simStart(date, speed)` → `POST /sim/start`; `simStop()` →
  `POST /sim/stop`; both `r.ok`-checked (match `fetchJSON`). `SimStatus` gains
  `state` and optional `error`.
- The existing `SimControls` (pause/speed) continues to render only when
  `state==="running"`.
- Requires `npm run build` to regenerate the `web/` export.

## Error Handling & Edge Cases

- **load_day fails / no data for date** → state `error` with message; live
  pipeline is restored (never left torn down).
- **Start while loading/running** → 409.
- **Stop while idle** → 200 no-op.
- **Invalid/weekend/holiday date or bad speed** → 422 (reuse `validate_sim_date`).
- **Teardown safety** → task cancellation is awaited before building the next
  pipeline, so two pipelines can never overlap on the shared queues.
- **Auth/startup** → unchanged (`DASHBOARD_PASSWORD` or `DEV_MODE` still gates).
- **Process model** → the live pipeline must be (re)startable by the builder; the
  builder returns the components + the set of coroutines to run as tasks.

## Testing

- **Unit — `SessionManager`:** start→loading→running→stop→idle with a fake loader
  (no network) and a fake task set; error path (loader raises → state `error`,
  live restored); double-start → 409 semantics; stop-while-idle no-op.
- **Unit — `RuntimeContext`:** after a swap, a handler-style reader sees the new
  component instances.
- **E2E (existing `TestClient` pattern):** `POST /sim/start` (stubbed fast
  loader) drives status loading→running; `POST /sim/stop` → idle; assert the real
  `PositionStore` is untouched (sandbox isolation); `/sim/control` 409 when idle.
- Existing tests stay green; non-sim behaviour identical when no sim is started.

## Out of Scope / Future

- Multiple concurrent sims, persisting sim results, scrubbing the clock backward,
  a dedicated sim-only server process, and replaying arbitrary historical movers
  from a paid source.
