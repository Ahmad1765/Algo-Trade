# Historical Replay Simulation ("Sim Mode") — Design

**Date:** 2026-06-22
**Status:** Approved (design), pending implementation plan
**Author:** brainstorming session

## Problem

The market is closed, so the live trading pipeline cannot be exercised against
real data. The user wants to **replay a specific past trading day** (default:
**2026-06-17**) through the *live* system so the dashboard shows prices ticking,
signals firing, and paper trades executing as if it were happening live — driven
by frozen historical data on a simulated clock.

This is **not** a backtest (the existing `Backtester` already does instant
batch replay + summary stats). This is a real-time-feeling **simulation** that
drives the full live pipeline and dashboard.

## Goals

- Replay real 2026-06-17 intraday data through the existing pipeline unchanged:
  `screener → options fetcher → strategy engine → order manager → dashboard`.
- Rank **real top gainers/losers** from a defined universe (S&P 500), computed
  from market-open up to the current simulated time (no look-ahead).
- Show the simulated day as "live" on the dashboard: sim clock time, "Market
  Open" status, prices/signals/P&L updating as the clock advances.
- Let the user control replay **speed and pause live from the dashboard**
  (`Pause / 1x / 10x / 60x / Max`).

## Non-Goals

- No changes to strategy logic, risk rules, or order execution behaviour.
- No paid/historical mover data source — movers are ranked from the S&P 500
  universe's real intraday data, not Yahoo's (today-only) predefined screeners.
- No precise options P&L modelling beyond what the live paper pipeline already does.

## Key Decisions (from brainstorming)

| Decision | Choice |
|----------|--------|
| What the sim drives | **Full live pipeline** (signals + paper trades + dashboard), paper mode |
| Replay speed | User-chosen, **live-adjustable** (pause + 1x/10x/60x/max) |
| Speed control location | **Embedded in the Next.js dashboard** (topbar), requires `npm run build` |
| Symbol universe | **S&P 500** (~500 names), ranked from real 2026-06-17 data |
| Movers approach | Compute intraday from universe (Yahoo has no historical mover endpoint) |
| Data caching | **Mandatory** — fetch once, cache to disk, load instantly on restart |

## Core Concept

A new **`ReplayMarketAdapter`** implements the same `MarketDataAdapter` interface
as the Yahoo/mock adapters but serves real 2026-06-17 data sliced at a
**simulated clock** position. Because the entire pipeline reads through that one
interface, nothing downstream changes — the system behaves exactly like a live
day with a frozen data source and a fake clock.

A shared **`SimClock`** is the heart of it:
- `sim_now` = `2026-06-17 09:30 ET` + (real elapsed seconds × speed)
- Holds `speed` (1/10/60/max) and `paused` — both adjustable live
- Read by the adapter (how much data is "visible so far") **and** by the server
  (so the dashboard shows sim time + "Market Open")

**No look-ahead:** the adapter never returns a bar with timestamp > `sim_now`,
so signals fire on exactly the information available at that simulated moment.

## Components

| # | Unit | New/Change | Purpose |
|---|------|-----------|---------|
| 1 | `src/sim/clock.py` | new | `SimClock`: sim time, speed, pause (~60 lines) |
| 2 | `src/sim/data_loader.py` | new | Fetch S&P 500 2026-06-17 1m bars (bounded concurrency), cache to `sim_data/2026-06-17.json`, load on restart; fall back to 5m if 1m aged out of Yahoo's 7-day window |
| 3 | `src/sim/sp500.py` | new | ~500 ticker universe list (data only) |
| 4 | `src/market_adapter/replay_adapter.py` | new | `ReplayMarketAdapter` — slices cached data at `sim_clock.sim_now`; ranks real gainers/losers from open→cursor |
| 5 | `src/market_adapter/base.py` | change | Add `provider == "replay"` branch to `create_market_adapter` factory |
| 6 | `src/api_server/server.py` | change | When `SimClock` present: `/health`, `/status`, `/stream` report sim time + market_open; add `GET /sim/status` + `POST /sim/control` |
| 7 | `frontend/components/layout/SimControls` + `topbar.tsx` + `lib/api.ts` | new/change | Live pause/speed control + sim-clock readout; only visible when sim mode active; rebuild `web/` export |
| 8 | `scripts/simulate.py` | new | Entry point: build SimClock + loader + replay adapter, run existing pipeline in paper mode |

## Data Flow

```
scripts/simulate.py --date 2026-06-17 [--speed 60]
   │
   ├─ SimClock(start=2026-06-17 09:30 ET, speed=60)
   ├─ data_loader → cache hit? load json : fetch 500×1m from Yahoo, save cache
   └─ ReplayMarketAdapter(cache, sim_clock)
        │
   ┌────┴──── runs the EXISTING pipeline (paper mode) ────┐
   screener → fetcher → strategy engine → order manager → DB
        every read of get_top_gainers / get_intraday_bars / get_quote /
        get_historical_bars returns 2026-06-17 data sliced at sim_clock.sim_now
        │
   server /stream → dashboard shows sim time, prices ticking,
        signals firing, paper P&L moving — as if live
        │
   topbar SimControls → POST /sim/control → SimClock (pause / set speed)
```

## Adapter Behaviour (June 17 data, sliced at sim_now)

- `get_intraday_bars(symbol, limit)` → that symbol's 2026-06-17 bars with
  `datetime <= sim_now`, last `limit`.
- `get_quote(symbol)` → latest bar at/just before `sim_now`; `change_pct` =
  (price_at_cursor − day_open) / day_open.
- `get_top_gainers/losers(limit)` → for every universe symbol, compute
  change since day-open up to `sim_now`, sort, return top `limit`.
- `get_historical_bars(symbol, range, interval)` → 2026-06-17 bars up to
  `sim_now` (used by `/quote` endpoint).
- `close()` → no-op (data is in memory/cache).

## SimClock API (sketch)

- `now()` → simulated `datetime` in ET.
- `is_open()` → whether sim_now is within 09:30–16:00 of the sim date.
- `set_speed(speed: float)` / `pause()` / `resume()`.
- Internally tracks: `sim_date`, `market_open_dt`, `real_anchor` (wall time at
  last speed/pause change), `sim_anchor` (sim time at that change), `speed`,
  `paused`. On each `now()`: if paused, return `sim_anchor`; else
  `sim_anchor + (wall_now − real_anchor) × speed`, clamped to ≤ 16:00.

## Server Integration

- `create_app(...)` gains an optional `sim_clock` parameter.
- When present, `/health`, `/status`, and `/stream` substitute
  `sim_clock.now()` for `now_et()` and `sim_clock.is_open()` for
  `is_market_open()`, so the dashboard displays the simulated day as live.
- New endpoints (only registered/active when `sim_clock` present):
  - `GET /sim/status` → `{ active, sim_time, speed, paused, sim_date, day_complete }`
  - `POST /sim/control` → body `{ action: "pause"|"resume"|"set_speed", speed? }`
- `scripts/simulate.py` passes the `SimClock` into both the `ReplayMarketAdapter`
  and `create_app`.

## Frontend Integration

- `topbar.tsx` already polls `/health` and renders a "Market Open/Closed" badge —
  this lights up "Market Open" automatically once the server reports sim values.
- New `SimControls` component (pause + speed buttons + sim-clock readout),
  rendered in the topbar right side, visible only when `api.simStatus()` reports
  `active: true`.
- `lib/api.ts` gains `simStatus()` and `simControl(action, speed?)` plus types.
- Requires `npm run build` in `frontend/` to regenerate the `web/` static export
  (consistent with the existing Docker build flow).

## Error Handling & Edge Cases

- **Not cached + Yahoo fetch fails** → loader raises a clear startup error; sim
  refuses to boot rather than run on bad/empty data.
- **1m history aged out of Yahoo's ~7-day window** → auto-fall back to 5m bars
  for the date; log the resolution drop.
- **Sim clock passes 16:00 ET** → adapter serves the final bar and holds;
  `day_complete` state; pipeline idles (no crash).
- **Partial fetch** (some symbols lack June 17 data) → skip them, log the count,
  proceed with what loaded.
- **Invalid `--date`** (weekend / NYSE holiday) → validated against
  `market_hours._HOLIDAYS`; refuses with a clear message.
- **2026-06-17 validity:** Wednesday, not a NYSE holiday (Juneteenth is 06-19) →
  valid trading day.

## Testing

- **Unit:**
  - `SimClock` time math: speed scaling, pause/resume, clamp at 16:00.
  - `ReplayMarketAdapter` slicing: never returns a bar with `datetime > sim_now`.
  - Gainer/loser ranking from open→cursor.
  - `data_loader` cache round-trip with mocked Yahoo responses.
- **E2E (follows `tests/e2e/` patterns):**
  - `/sim/status` + `/sim/control` endpoints (pause/resume/set_speed).
  - `/health` reflects sim time when a `SimClock` is injected.
- Existing tests must remain green (no behaviour change when sim mode is off).

## Caching Detail

- Cache file: `sim_data/<date>.json` (e.g. `sim_data/2026-06-17.json`),
  keyed by date + interval; gitignored.
- Schema: `{ "date": "2026-06-17", "interval": "1m", "fetched_at": "...",
  "symbols": { "AAPL": [ {datetime,open,high,low,close,volume}, ... ], ... } }`.
- First run fetches + writes; subsequent runs load instantly. Also preserves the
  data after Yahoo drops 1m history past ~2026-06-24.

## Out of Scope / Future

- Multi-day replay, replaying arbitrary historical movers from a paid source,
  scrubbing the clock backward, and a standalone (non-dashboard) control page.
