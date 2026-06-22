# Start/Stop Replay Sim from the Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user start and stop a replay simulation from the dashboard (date + speed → Start; Stop returns to live/paper), with sim trades isolated in an ephemeral sandbox store so real paper data is never touched.

**Architecture:** A `RuntimeContext` holds the active pipeline's components (stores, adapter, engine, clock, mode) and is read live by every API endpoint. A `SessionManager` owns the running pipeline tasks and a state machine (`idle→loading→running→stopping`/`error`); it tears down the live pipeline, builds a sim pipeline (sandbox `PositionStore` + `ReplayMarketAdapter` + `SimClock`) on `start_sim`, and rebuilds live on `stop_sim`. A `pipeline_builder` extracts the component-wiring so live and sim reuse it. The API server runs continuously across swaps. New endpoints `POST /sim/start`, `POST /sim/stop`, extended `/sim/status`; a dashboard launcher panel drives them.

**Tech Stack:** Python 3.11+ (asyncio, aiohttp, SQLAlchemy via existing `PositionStore`), pytest; Next.js/React/TypeScript frontend.

## Global Constraints

- Python stdlib + existing deps only (`aiohttp`, `sqlalchemy`, `pyyaml`, `pytest`). No new packages.
- Files under 500 lines; keep each new unit single-responsibility.
- **No behaviour change when no sim is started** — live/paper pipeline and all existing endpoints behave identically; the full existing test suite must stay green after every task.
- Sandbox = a `PositionStore` on an ephemeral temp-file SQLite DB (`sqlite:///<tempdir>/algo_sim_<uuid>.db`), deleted on stop. NOT `:memory:` (SQLAlchemy multi-connection won't share it).
- At most one pipeline runs at a time; task cancellation is awaited before building the next pipeline.
- `scripts/simulate.py` (terminal launch) must keep working.
- Reuse `data_loader.load_day`, `validate_sim_date` (from `scripts/simulate.py`), `SimClock`, `ReplayMarketAdapter`.
- Bar/quote/event types unchanged. No secrets committed.

---

### Task 1: RuntimeContext

**Files:**
- Create: `algo-trade/src/runtime/__init__.py`
- Create: `algo-trade/src/runtime/context.py`
- Test: `algo-trade/tests/test_runtime_context.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RuntimeContext` dataclass with mutable attributes `mode: str` (`"live"`|`"sim"`), `risk_manager`, `position_store`, `market_adapter`, `strategy_engine`, `broker_adapter`, `signal_store: list`, `action_store: list`, `sim_clock` (all default `None` except the two lists default to empty list and `mode` defaults to `"live"`).

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_runtime_context.py`:

```python
# file: tests/test_runtime_context.py
from src.runtime.context import RuntimeContext


def test_defaults():
    ctx = RuntimeContext()
    assert ctx.mode == "live"
    assert ctx.position_store is None
    assert ctx.market_adapter is None
    assert ctx.strategy_engine is None
    assert ctx.sim_clock is None
    assert ctx.signal_store == []
    assert ctx.action_store == []


def test_independent_list_instances():
    a = RuntimeContext()
    b = RuntimeContext()
    a.signal_store.append(1)
    assert b.signal_store == []  # no shared mutable default


def test_fields_are_swappable():
    ctx = RuntimeContext()
    ctx.mode = "sim"
    ctx.position_store = "STORE_A"
    assert ctx.mode == "sim"
    ctx.position_store = "STORE_B"
    assert ctx.position_store == "STORE_B"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_runtime_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.runtime'`.

- [ ] **Step 3: Write minimal implementation**

Create `algo-trade/src/runtime/__init__.py` (empty file).

Create `algo-trade/src/runtime/context.py`:

```python
# file: src/runtime/context.py
"""
RuntimeContext — mutable holder of the currently-active pipeline's components.

API endpoints read their dependencies from a single RuntimeContext instance so
that the SessionManager can swap the whole set (live <-> sim) atomically and
every handler observes the change immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RuntimeContext:
    mode: str = "live"  # "live" | "sim"
    risk_manager: Any = None
    position_store: Any = None
    market_adapter: Any = None
    strategy_engine: Any = None
    broker_adapter: Any = None
    sim_clock: Any = None
    signal_store: List[Dict] = field(default_factory=list)
    action_store: List[Dict] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd algo-trade && python -m pytest tests/test_runtime_context.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add algo-trade/src/runtime/__init__.py algo-trade/src/runtime/context.py algo-trade/tests/test_runtime_context.py
git commit -m "feat(runtime): add RuntimeContext mutable component holder"
```

---

### Task 2: Route server endpoints through RuntimeContext (compat shim)

**Files:**
- Modify: `algo-trade/src/api_server/server.py`
- Test: `algo-trade/tests/e2e/runtime_ctx.spec.py`

**Interfaces:**
- Consumes: `RuntimeContext` (Task 1).
- Produces: `create_app(...)` gains a trailing keyword `ctx: Optional[RuntimeContext] = None`. When `ctx` is None it is BUILT from the existing positional/keyword args (backward compatible — existing callers and the e2e `conftest` keep working unchanged). Every endpoint reads its dependencies from `ctx.*` (live), so a later swap of `ctx.*` is observed immediately. Behaviour is identical to today.

**Why a shim:** the e2e `conftest.make_app` and `cli/main` call `create_app(risk_manager, signal_store, position_store, ...)`. Building `ctx` internally from those args when `ctx is None` keeps every existing test green while moving all reads behind `ctx`.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/e2e/runtime_ctx.spec.py`:

```python
# file: tests/e2e/runtime_ctx.spec.py
"""Endpoints must read live from RuntimeContext so a swap is observed."""
from aiohttp.test_utils import TestClient, TestServer

from src.api_server.server import create_app
from src.runtime.context import RuntimeContext


class _Risk:
    pass


async def test_create_app_accepts_ctx_and_reads_from_it():
    ctx = RuntimeContext(risk_manager=_Risk(), signal_store=[], position_store=None)
    app = create_app(_Risk(), [], None, ctx=ctx)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/health")
        assert resp.status == 200


async def test_signal_swap_is_observed_live():
    ctx = RuntimeContext(risk_manager=_Risk(), signal_store=[{"symbol": "AAA"}])
    app = create_app(_Risk(), [], None, ctx=ctx)
    async with TestClient(TestServer(app)) as client:
        first = await (await client.get("/signals")).json()
        assert len(first) == 1
        # Swap the signal store on the context; endpoint must see the new list.
        ctx.signal_store = [{"symbol": "BBB"}, {"symbol": "CCC"}]
        second = await (await client.get("/signals")).json()
        assert len(second) == 2
        assert second[-1]["symbol"] == "CCC"


async def test_legacy_args_still_work_without_ctx():
    # No ctx passed -> built internally from legacy args (back-compat).
    app = create_app(_Risk(), [{"symbol": "X"}], None)
    async with TestClient(TestServer(app)) as client:
        sigs = await (await client.get("/signals")).json()
        assert sigs == [{"symbol": "X"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/e2e/runtime_ctx.spec.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'ctx'`.

- [ ] **Step 3: Add the `ctx` param + build-from-legacy shim**

In `algo-trade/src/api_server/server.py`, change the `create_app` signature to add a trailing keyword (keep all existing params):

```python
def create_app(
    risk_manager: Any,
    signal_store: List[Dict],
    position_store: Optional[Any] = None,
    market_adapter: Optional[Any] = None,
    action_store: Optional[List[Dict]] = None,
    broker_adapter: Optional[Any] = None,
    strategy_engine: Optional[Any] = None,
    sim_clock: Optional[Any] = None,
    ctx: Optional[Any] = None,
) -> web.Application:
```

At the very top of the function body (replacing the existing `_action_store = ...` line), build/adopt the context:

```python
    from src.runtime.context import RuntimeContext
    if ctx is None:
        ctx = RuntimeContext(
            mode="live",
            risk_manager=risk_manager,
            position_store=position_store,
            market_adapter=market_adapter,
            strategy_engine=strategy_engine,
            broker_adapter=broker_adapter,
            sim_clock=sim_clock,
            signal_store=signal_store if signal_store is not None else [],
            action_store=action_store if action_store is not None else [],
        )
```

- [ ] **Step 4: Route every handler read through `ctx`**

Replace the closed-over names inside the handler bodies with `ctx.*`. Apply this exact mapping to EVERY handler that references them (do not change the helper-call argument lists that already take a value, only the source of the value):

| Old reference (closure var) | New reference |
|---|---|
| `position_store` | `ctx.position_store` |
| `signal_store` | `ctx.signal_store` |
| `_action_store` | `ctx.action_store` |
| `market_adapter` | `ctx.market_adapter` |
| `broker_adapter` | `ctx.broker_adapter` |
| `strategy_engine` | `ctx.strategy_engine` |
| `risk_manager` | `ctx.risk_manager` |
| `sim_clock` (in `_market_open_now`, `_market_time_str`, `sim_status`, `sim_control`) | `ctx.sim_clock` |

Handlers to update (each reads the attrs noted): `health` (position_store), `get_signals` (signal_store), `get_positions` (position_store), `get_metrics` (position_store), `get_history` (action_store), `get_status` (position_store, strategy_engine), `sse_stream` (position_store, strategy_engine, signal_store, action_store), `get_config_endpoint` (position_store), `post_config_endpoint` (position_store), `test_email_endpoint` (position_store), `get_overview` (market_adapter), `get_quote` (market_adapter), `get_strategies` (signal_store, position_store), `post_reset` (position_store, broker_adapter, signal_store, action_store), `run_backtest_endpoint` (market_adapter), `post_order` (position_store, action_store), `get_circuit_breaker` (position_store), `get_pending_signals` (strategy_engine), `_market_open_now`/`_market_time_str`/`sim_status`/`sim_control` (sim_clock).

Two representative conversions (apply the same pattern everywhere):

```python
    async def health(request: web.Request) -> web.Response:
        cfg = get_config()
        db_ok = ctx.position_store.check_connection() if ctx.position_store else False
        return web.json_response({
            "status": "ok",
            "uptime_s": round(time.time() - _START_TIME, 1),
            "market_open": _market_open_now(),
            "market_time_et": _market_time_str(),
            "mode": cfg.get("mode", "paper"),
            "broker": cfg.get("broker", {}).get("name", "mock"),
            "database_connected": db_ok,
        })
```

```python
    def _market_open_now() -> bool:
        return ctx.sim_clock.is_open() if ctx.sim_clock is not None else is_market_open()

    def _market_time_str() -> str:
        src_dt = ctx.sim_clock.now() if ctx.sim_clock is not None else now_et()
        return src_dt.strftime("%Y-%m-%d %H:%M:%S ET")
```

For `post_reset`, the lines `signal_store.clear()` and `_action_store.clear()` become `ctx.signal_store.clear()` and `ctx.action_store.clear()`; `broker_adapter` → `ctx.broker_adapter`.

IMPORTANT: do not rename the function parameters; only change reads inside handler bodies. The `_action_store` local (previously `action_store if action_store is not None else []`) is removed — all reads go through `ctx.action_store`.

- [ ] **Step 5: Run the new test + full regression**

Run: `cd algo-trade && python -m pytest tests/e2e/runtime_ctx.spec.py -v`
Expected: PASS (3 passed).
Run: `cd algo-trade && python -m pytest tests/e2e -q`
Expected: PASS — all existing e2e tests green (the shim preserves behaviour).

- [ ] **Step 6: Commit**

```bash
git add algo-trade/src/api_server/server.py algo-trade/tests/e2e/runtime_ctx.spec.py
git commit -m "refactor(server): route endpoints through swappable RuntimeContext"
```

---

### Task 3: Pipeline builder

**Files:**
- Create: `algo-trade/src/runtime/pipeline_builder.py`
- Test: `algo-trade/tests/test_pipeline_builder.py`

**Interfaces:**
- Consumes: `RuntimeContext` (Task 1); existing `RiskManager`, `PositionStore`, `Notifier`, `Screener`, `OptionsFetcher`, `MultiStrategyEngine`, `OrderManager`, `create_broker_adapter`, `create_market_adapter`.
- Produces:
  `async build_pipeline(config, mode, *, market_adapter=None, sim_clock=None, position_store=None) -> tuple[RuntimeContext, list[Callable[[], Awaitable]]]`
  — constructs all pipeline components, returns a populated `RuntimeContext` (mode set) and a list of **zero-arg coroutine functions** to run as tasks: `[screener.run, fetcher.run, engine.run, order_mgr.run, signal_tap]`. It performs `await order_mgr.recover_open_positions()` and the DB restore of signals/actions into the context's lists. The API server is NOT included (it runs independently of pipeline swaps).

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_pipeline_builder.py`:

```python
# file: tests/test_pipeline_builder.py
import asyncio
import inspect
from datetime import date

from src.runtime.context import RuntimeContext
from src.runtime import pipeline_builder


def _min_config(tmp_path):
    return {
        "mode": "paper",
        "screener": {"provider": "mock", "top_n": 3, "poll_interval_seconds": 1,
                     "market_hours_only": False},
        "options_filter": {"min_volume": 1, "min_open_interest": 1, "max_spread_pct": 1.0,
                           "max_dte": 60, "min_dte": 0, "max_otm_pct": 1.0},
        "indicators": {"rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30,
                       "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
                       "atr_period": 14, "lookback_bars": 50, "signal_cooldown_minutes": 30},
        "risk": {"max_position_pct": 0.05, "max_open_positions": 5,
                 "pdt_equity_threshold": 25000, "stop_loss_atr_mult": 1.5,
                 "take_profit_atr_mult": 3.0},
        "broker": {"name": "mock"},
        "market_data": {"fmp_api_key": "x", "request_timeout": 5, "retry_max": 1,
                        "retry_backoff": 0.1},
        "logging": {"level": "WARNING", "json_format": False},
        "database": {"url": f"sqlite:///{tmp_path}/build.db"},
        "notifications": {"email": {"enabled": False}, "webhook": {"enabled": False}},
    }


def test_build_pipeline_returns_ctx_and_runnables(tmp_path, monkeypatch):
    # Point PositionStore at the temp DB via config patch (matches conftest pattern).
    import src.persistence as pm
    monkeypatch.setattr(pm, "get_config", lambda: {"database": {"url": f"sqlite:///{tmp_path}/build.db"}})

    cfg = _min_config(tmp_path)
    ctx, runnables = asyncio.run(pipeline_builder.build_pipeline(cfg, "paper"))

    assert isinstance(ctx, RuntimeContext)
    assert ctx.mode == "live"
    assert ctx.market_adapter is not None
    assert ctx.strategy_engine is not None
    assert ctx.position_store is not None
    # Runnables are zero-arg coroutine factories.
    assert len(runnables) == 5
    for r in runnables:
        assert callable(r)
        assert inspect.iscoroutinefunction(r)


def test_build_pipeline_uses_injected_adapter_and_clock(tmp_path, monkeypatch):
    import src.persistence as pm
    monkeypatch.setattr(pm, "get_config", lambda: {"database": {"url": f"sqlite:///{tmp_path}/build2.db"}})

    sentinel_adapter = object()
    sentinel_clock = object()
    cfg = _min_config(tmp_path)
    ctx, _ = asyncio.run(pipeline_builder.build_pipeline(
        cfg, "sim", market_adapter=sentinel_adapter, sim_clock=sentinel_clock,
    ))
    assert ctx.market_adapter is sentinel_adapter
    assert ctx.sim_clock is sentinel_clock
    assert ctx.mode == "sim"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_pipeline_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.runtime.pipeline_builder'`.

- [ ] **Step 3: Write minimal implementation**

Create `algo-trade/src/runtime/pipeline_builder.py`. Move the component-wiring out of `cli/main._run_pipeline` (do not delete from main yet — Task 5 rewires main to call this):

```python
# file: src/runtime/pipeline_builder.py
"""
Build a trading pipeline (live or sim) and return its RuntimeContext plus the
set of coroutine factories to run as asyncio tasks. Shared by the live launch
path (cli/main) and the on-demand sim path (SessionManager).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import asyncio

from src.execution.base import create_broker_adapter
from src.execution.order_manager import OrderManager
from src.logger import get_logger
from src.market_adapter.base import create_market_adapter
from src.notifier import Notifier
from src.options_fetcher import OptionsFetcher
from src.persistence import PositionStore
from src.risk_manager import RiskManager
from src.runtime.context import RuntimeContext
from src.screener import Screener
from src.strategy_engine import MultiStrategyEngine

log = get_logger(__name__)


async def build_pipeline(
    config: Dict[str, Any],
    mode: str,
    *,
    market_adapter: Optional[Any] = None,
    sim_clock: Optional[Any] = None,
    position_store: Optional[Any] = None,
) -> Tuple[RuntimeContext, List[Callable[[], Awaitable]]]:
    if market_adapter is None:
        market_adapter = create_market_adapter(config)
    broker_adapter = create_broker_adapter(config)
    risk_manager = RiskManager(config)
    if position_store is None:
        position_store = PositionStore()
    notifier = Notifier(config)

    signal_store: List[Dict] = []
    action_store: List[Dict] = []

    # Restore history into this context's lists (sandbox stores start empty).
    signal_store.extend(position_store.get_signals(limit=200))
    action_store.extend(position_store.get_actions(limit=200))

    position_store.add_action("SYSTEM_STARTED", None, f"Pipeline started in {mode} mode", {"mode": mode})
    action_store.append({
        "event": "SYSTEM_STARTED", "symbol": None,
        "detail": f"Pipeline started in {mode} mode", "data": {"mode": mode},
        "ts": datetime.now(timezone.utc).isoformat(),
    })

    candidate_queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    chain_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    signal_queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    tap_queue: asyncio.Queue = asyncio.Queue(maxsize=50)

    screener = Screener(market_adapter, candidate_queue, config)
    fetcher = OptionsFetcher(broker_adapter, candidate_queue, chain_queue, config)
    engine = MultiStrategyEngine(
        market_adapter, chain_queue, signal_queue, config,
        position_store=position_store, notifier=notifier, tap_queue=tap_queue,
        sim_clock=sim_clock,
    )
    order_mgr = OrderManager(
        broker_adapter, risk_manager, signal_queue, mode, config,
        position_store=position_store, notifier=notifier,
        action_store=action_store, market_adapter=market_adapter,
    )

    await order_mgr.recover_open_positions()

    async def _signal_tap() -> None:
        from src.events import SignalEvent
        while True:
            try:
                sig: SignalEvent = await asyncio.wait_for(tap_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            plan = sig.trade_plan
            data = {
                "symbol": plan.symbol, "direction": plan.direction.value,
                "strike": plan.contract.strike, "expiry": plan.contract.expiry,
                "entry": plan.entry_limit, "stop": plan.stop_loss,
                "target": plan.take_profit, "size": plan.position_size,
                "rationale": plan.rationale, "strategy": plan.strategy_name,
                "ts": sig.timestamp.isoformat(),
            }
            signal_store.append(data)
            position_store.add_signal(data)
            if len(signal_store) > 200:
                signal_store.pop(0)

    ctx = RuntimeContext(
        mode="sim" if sim_clock is not None else "live",
        risk_manager=risk_manager,
        position_store=position_store,
        market_adapter=market_adapter,
        strategy_engine=engine,
        broker_adapter=broker_adapter,
        sim_clock=sim_clock,
        signal_store=signal_store,
        action_store=action_store,
    )

    runnables: List[Callable[[], Awaitable]] = [
        screener.run, fetcher.run, engine.run, order_mgr.run, _signal_tap,
    ]
    return ctx, runnables
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd algo-trade && python -m pytest tests/test_pipeline_builder.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add algo-trade/src/runtime/pipeline_builder.py algo-trade/tests/test_pipeline_builder.py
git commit -m "feat(runtime): extract pipeline builder for live + sim reuse"
```

---

### Task 4: SessionManager

**Files:**
- Create: `algo-trade/src/runtime/session_manager.py`
- Test: `algo-trade/tests/test_session_manager.py`

**Interfaces:**
- Consumes: `RuntimeContext` (Task 1); `build_pipeline` (Task 3); `SimClock`, `ReplayMarketAdapter`, `load_day`, `validate_sim_date`.
- Produces: `SessionManager(config, ctx, *, build_fn=build_pipeline, load_fn=load_day, clock_factory=SimClock, adapter_factory=ReplayMarketAdapter, store_factory=...)` (factories injectable for tests). Methods:
  - `async start_live()` — build the live pipeline, start its tasks, point `self.ctx` at the live ctx, state `idle` (live running but not a sim).
  - `async start_sim(sim_date: date, speed: float)` — guards (`409` if state in {loading, running}); state→`loading`; spawn a background task that loads data, builds the sim pipeline (sandbox temp-file store), cancels live tasks, starts sim tasks, repoints ctx, state→`running`; on failure state→`error` and live is restored.
  - `async stop_sim()` — if not running, no-op; else state→`stopping`, cancel sim tasks (awaited), delete sandbox DB file, rebuild + restart live, repoint ctx, state→`idle`.
  - `status() -> dict` — `{state, sim_time, speed, paused, sim_date, error, active}` where `active == (state=="running")`; `sim_time/speed/paused` from `ctx.sim_clock` when running.
  - state attribute: `self.state in {"idle","loading","running","stopping","error"}`.

Note: because endpoints copy ctx fields by reference, `SessionManager` mutates the SAME `RuntimeContext` instance the server holds (`copy_into(self.ctx, new_ctx)`), rather than replacing the object — so the server's `ctx` reference stays valid. Implement an internal `_adopt(new_ctx)` that copies every field of `new_ctx` onto `self.ctx`.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_session_manager.py`:

```python
# file: tests/test_session_manager.py
import asyncio
from datetime import date

import pytest

from src.runtime.context import RuntimeContext
from src.runtime.session_manager import SessionManager


# --- Fakes -----------------------------------------------------------------

async def _idle_coro():
    try:
        while True:
            await asyncio.sleep(0.01)
    except asyncio.CancelledError:
        return


def _fake_build(mark):
    async def _build(config, mode, *, market_adapter=None, sim_clock=None, position_store=None):
        ctx = RuntimeContext(mode="sim" if sim_clock is not None else "live",
                             market_adapter=market_adapter, sim_clock=sim_clock,
                             position_store=position_store or f"{mark}-store")
        return ctx, [_idle_coro, _idle_coro]
    return _build


class _FakeClock:
    def __init__(self, *_a, **_k):
        pass
    def now(self):
        from datetime import datetime, timezone
        return datetime(2026, 6, 17, 9, 30, tzinfo=timezone.utc)
    def status(self):
        return {"sim_time": "2026-06-17 09:30:00 ET", "speed": 60.0, "paused": False}


async def _ok_loader(sim_date, **kw):
    return {"AAPL": [{"datetime": "2026-06-17T13:30:00+00:00", "open": 1, "high": 1,
                      "low": 1, "close": 1, "volume": 1}]}


async def _failing_loader(sim_date, **kw):
    raise RuntimeError("no data")


def _mgr(loader=_ok_loader):
    ctx = RuntimeContext()
    return SessionManager(
        config={"database": {"url": "sqlite:///unused"}},
        ctx=ctx,
        build_fn=_fake_build("live"),
        load_fn=loader,
        clock_factory=_FakeClock,
        adapter_factory=lambda data, clock: ("ADAPTER", data, clock),
        store_factory=lambda: "SANDBOX-STORE",
    )


# --- Tests -----------------------------------------------------------------

def test_start_live_sets_idle_and_runs():
    async def go():
        m = _mgr()
        await m.start_live()
        assert m.state == "idle"
        assert m.ctx.mode == "live"
        await m._cancel_tasks()  # cleanup
    asyncio.run(go())


def test_start_sim_transitions_to_running():
    async def go():
        m = _mgr()
        await m.start_live()
        await m.start_sim(date(2026, 6, 17), 60.0)
        # allow the background load+build task to complete
        for _ in range(50):
            if m.state in ("running", "error"):
                break
            await asyncio.sleep(0.01)
        assert m.state == "running"
        assert m.ctx.mode == "sim"
        assert m.status()["active"] is True
        await m.stop_sim()
        assert m.state == "idle"
        assert m.ctx.mode == "live"
        await m._cancel_tasks()
    asyncio.run(go())


def test_double_start_sim_is_rejected():
    async def go():
        m = _mgr()
        await m.start_live()
        await m.start_sim(date(2026, 6, 17), 60.0)
        with pytest.raises(RuntimeError):
            await m.start_sim(date(2026, 6, 17), 60.0)  # already loading/running
        await m._cancel_tasks()
    asyncio.run(go())


def test_loader_failure_sets_error_and_restores_live():
    async def go():
        m = _mgr(loader=_failing_loader)
        await m.start_live()
        await m.start_sim(date(2026, 6, 17), 60.0)
        for _ in range(50):
            if m.state in ("running", "error", "idle"):
                if m.state != "loading":
                    break
            await asyncio.sleep(0.01)
        assert m.state == "error"
        assert m.status()["error"]
        assert m.ctx.mode == "live"  # live restored
        await m._cancel_tasks()
    asyncio.run(go())


def test_stop_when_idle_is_noop():
    async def go():
        m = _mgr()
        await m.start_live()
        await m.stop_sim()  # idle -> no-op
        assert m.state == "idle"
        await m._cancel_tasks()
    asyncio.run(go())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_session_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.runtime.session_manager'`.

- [ ] **Step 3: Write minimal implementation**

Create `algo-trade/src/runtime/session_manager.py`:

```python
# file: src/runtime/session_manager.py
"""
SessionManager — owns the running pipeline tasks and the start/stop lifecycle.

States: idle (live running, no sim) | loading (fetching sim data + building) |
running (sim live) | stopping (tearing down sim) | error (load/build failed,
live restored).

The same RuntimeContext instance held by the API server is mutated in place
(_adopt) so handlers keep a valid reference across swaps.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from datetime import date
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.logger import get_logger
from src.runtime.context import RuntimeContext
from src.runtime.pipeline_builder import build_pipeline as _default_build

log = get_logger(__name__)


def _default_store_factory():
    """Sandbox PositionStore on an ephemeral temp-file SQLite DB. Returns (store, db_path)."""
    from src.persistence import PositionStore
    db_path = os.path.join(tempfile.gettempdir(), f"algo_sim_{uuid.uuid4().hex}.db")
    from unittest.mock import patch
    with patch("src.persistence.get_config", return_value={"database": {"url": f"sqlite:///{db_path}"}}):
        store = PositionStore()
    return store, db_path


class SessionManager:
    def __init__(
        self,
        config: Dict[str, Any],
        ctx: RuntimeContext,
        *,
        build_fn: Callable = _default_build,
        load_fn: Optional[Callable] = None,
        clock_factory: Optional[Callable] = None,
        adapter_factory: Optional[Callable] = None,
        store_factory: Optional[Callable] = None,
    ) -> None:
        self._config = config
        self.ctx = ctx
        self._build = build_fn
        if load_fn is None:
            from src.sim.data_loader import load_day as load_fn  # type: ignore
        self._load = load_fn
        if clock_factory is None:
            from src.sim.clock import SimClock as clock_factory  # type: ignore
        self._clock_factory = clock_factory
        if adapter_factory is None:
            from src.market_adapter.replay_adapter import ReplayMarketAdapter as adapter_factory  # type: ignore
        self._adapter_factory = adapter_factory
        self._store_factory = store_factory  # returns store or (store, path); None -> default

        self.state: str = "idle"
        self.error: Optional[str] = None
        self._sim_date: Optional[str] = None
        self._tasks: List[asyncio.Task] = []
        self._sim_db_path: Optional[str] = None
        self._load_task: Optional[asyncio.Task] = None

    # -- task helpers --------------------------------------------------------

    def _start_tasks(self, runnables: List[Callable[[], Awaitable]]) -> None:
        self._tasks = [asyncio.ensure_future(r()) for r in runnables]

    async def _cancel_tasks(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def _adopt(self, new_ctx: RuntimeContext) -> None:
        """Copy new_ctx fields onto the shared ctx so server handlers stay valid."""
        for f in ("mode", "risk_manager", "position_store", "market_adapter",
                  "strategy_engine", "broker_adapter", "sim_clock",
                  "signal_store", "action_store"):
            setattr(self.ctx, f, getattr(new_ctx, f))

    # -- lifecycle -----------------------------------------------------------

    async def start_live(self) -> None:
        new_ctx, runnables = await self._build(self._config, self._config.get("mode", "paper"))
        self._adopt(new_ctx)
        self._start_tasks(runnables)
        self.state = "idle"
        self.error = None

    async def start_sim(self, sim_date: date, speed: float) -> None:
        if self.state in ("loading", "running", "stopping"):
            raise RuntimeError(f"cannot start sim while state={self.state}")
        self.state = "loading"
        self.error = None
        self._sim_date = sim_date.isoformat()
        self._load_task = asyncio.ensure_future(self._load_and_run_sim(sim_date, speed))

    async def _load_and_run_sim(self, sim_date: date, speed: float) -> None:
        try:
            data = await self._load(sim_date)
            from datetime import datetime
            from src.sim.clock import ET
            clock = self._clock_factory(datetime(sim_date.year, sim_date.month, sim_date.day, tzinfo=ET), speed=speed) \
                if self._clock_factory.__name__ == "SimClock" else self._clock_factory()
            adapter = self._adapter_factory(data, clock)
            store, db_path = self._make_store()
            new_ctx, runnables = await self._build(
                self._config, "paper", market_adapter=adapter, sim_clock=clock, position_store=store,
            )
            await self._cancel_tasks()           # tear down live
            self._adopt(new_ctx)
            self._sim_db_path = db_path
            self._start_tasks(runnables)
            self.state = "running"
        except Exception as exc:  # noqa: BLE001 — surface + restore live
            log.error("sim start failed", error=str(exc))
            self.error = str(exc)
            await self._cancel_tasks()
            await self.start_live()
            self.state = "error"

    def _make_store(self):
        if self._store_factory is None:
            return _default_store_factory()
        result = self._store_factory()
        if isinstance(result, tuple):
            return result
        return result, None

    async def stop_sim(self) -> None:
        if self.state != "running":
            return
        self.state = "stopping"
        await self._cancel_tasks()
        self._cleanup_sim_db()
        await self.start_live()
        self.state = "idle"

    def _cleanup_sim_db(self) -> None:
        if self._sim_db_path:
            try:
                if os.path.exists(self._sim_db_path):
                    os.remove(self._sim_db_path)
            except OSError:
                pass
            self._sim_db_path = None

    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "state": self.state,
            "active": self.state == "running",
            "sim_date": self._sim_date,
            "error": self.error,
            "sim_time": None, "speed": None, "paused": None,
        }
        if self.state == "running" and self.ctx.sim_clock is not None:
            cs = self.ctx.sim_clock.status()
            out["sim_time"] = cs.get("sim_time")
            out["speed"] = cs.get("speed")
            out["paused"] = cs.get("paused")
        return out
```

NOTE TO IMPLEMENTER: the `clock_factory.__name__ == "SimClock"` branch distinguishes the real SimClock (needs a datetime+speed) from a test fake (zero-arg). Keep it; the test fakes are zero-arg. If the real `SimClock` constructor signature differs from `(datetime, speed=...)`, adjust the real-clock branch to match `src/sim/clock.py` and the `scripts/simulate.py` construction exactly.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd algo-trade && python -m pytest tests/test_session_manager.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add algo-trade/src/runtime/session_manager.py algo-trade/tests/test_session_manager.py
git commit -m "feat(runtime): add SessionManager start/stop sim lifecycle"
```

---

### Task 5: Rewire cli/main to builder + SessionManager

**Files:**
- Modify: `algo-trade/src/cli/main.py:60-205` (`_run_pipeline` and helpers)
- Test: `algo-trade/tests/test_main_uses_session_manager.py`

**Interfaces:**
- Consumes: `build_pipeline` (Task 3), `SessionManager` (Task 4), `create_app(..., ctx=, session_manager=)` (Tasks 2 + 6).
- Produces: `_run_pipeline(config, mode, *, market_adapter=None, sim_clock=None)` keeps its signature and external behaviour (terminal `simulate.py` still works), but internally: builds the initial pipeline via `SessionManager` (live, or sim when `sim_clock`/`market_adapter` injected), starts the API server once with the shared `ctx` + `session_manager`, and runs until shutdown. The module-level `_signal_store`/`_action_store` globals are removed in favour of the context's lists.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/test_main_uses_session_manager.py`:

```python
# file: tests/test_main_uses_session_manager.py
import inspect

from src.cli import main


def test_run_pipeline_signature_preserved():
    sig = inspect.signature(main._run_pipeline)
    assert "market_adapter" in sig.parameters
    assert "sim_clock" in sig.parameters


def test_main_module_imports_session_manager():
    src = inspect.getsource(main)
    assert "SessionManager" in src
    assert "build_pipeline" in src or "from src.runtime" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/test_main_uses_session_manager.py -v`
Expected: FAIL — `assert 'SessionManager' in src` (not yet wired).

- [ ] **Step 3: Rewire `_run_pipeline`**

In `algo-trade/src/cli/main.py`, replace the body of `_run_pipeline` (the component wiring now lives in `build_pipeline`). Imports at top: add `from src.runtime.pipeline_builder import build_pipeline` and `from src.runtime.session_manager import SessionManager` and `from src.runtime.context import RuntimeContext`. Remove the now-unused direct component imports only if they are no longer referenced elsewhere in the file (keep `create_app`, `run_api_server`, `load_config`, `auth`).

New `_run_pipeline`:

```python
async def _run_pipeline(
    config: Dict[str, Any],
    mode: str,
    *,
    market_adapter: Any = None,
    sim_clock: Any = None,
) -> None:
    # Load UI-saved config overrides from a throwaway store read (same as before).
    # Build the initial pipeline (live, or sim when an adapter/clock is injected).
    ctx, runnables = await build_pipeline(
        config, mode, market_adapter=market_adapter, sim_clock=sim_clock,
    )

    manager = SessionManager(config, ctx)
    # Adopt the just-built pipeline as the manager's current session.
    manager.ctx = ctx
    manager._tasks = [asyncio.ensure_future(r()) for r in runnables]
    manager.state = "running" if sim_clock is not None else "idle"

    api_cfg = config.get("api_server", {})
    api_port = int(os.environ.get("PORT") or os.environ.get("API_PORT") or api_cfg.get("port", 8181))
    from src.api_server import auth as _auth
    _auth.assert_auth_config()
    app = create_app(
        ctx.risk_manager, ctx.signal_store, ctx.position_store, ctx.market_adapter,
        ctx.action_store, ctx.broker_adapter, strategy_engine=ctx.strategy_engine,
        sim_clock=ctx.sim_clock, ctx=ctx, session_manager=manager,
    )

    log.info("pipeline starting", mode=mode)
    loop = asyncio.get_event_loop()

    async def _run_all() -> None:
        server_task = asyncio.ensure_future(
            run_api_server(app, api_cfg.get("host", "0.0.0.0"), api_port)
        )
        all_tasks = manager._tasks + [server_task]
        _attach_shutdown(loop, all_tasks)
        try:
            await asyncio.gather(server_task, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        finally:
            log.info("shutting down — cancelling pipeline")
            await manager._cancel_tasks()
            try:
                if ctx.market_adapter:
                    await ctx.market_adapter.close()
                if ctx.broker_adapter:
                    await ctx.broker_adapter.close()
            except Exception:
                pass

    await _run_all()
```

NOTE TO IMPLEMENTER: this changes the shutdown model so the API server task drives process lifetime (pipeline tasks are managed by the manager). Confirm `_attach_shutdown` still cancels the listed tasks. Delete the `_signal_store`/`_action_store` module globals and the inline `_signal_tap` (now in the builder). If `create_market_adapter`/`MultiStrategyEngine`/etc. imports become unused in `main.py`, remove them.

- [ ] **Step 4: Run the new test + full regression**

Run: `cd algo-trade && python -m pytest tests/test_main_uses_session_manager.py -v`
Expected: PASS.
Run: `cd algo-trade && python -m pytest -q`
Expected: full suite green (existing pipeline/e2e behaviour preserved).

- [ ] **Step 5: Smoke-check the terminal sim path still imports**

Run: `cd algo-trade && python -c "import scripts.simulate as s; print('ok', callable(s.main))"`
Expected: prints `ok True` (no import errors from the rewire).

- [ ] **Step 6: Commit**

```bash
git add algo-trade/src/cli/main.py algo-trade/tests/test_main_uses_session_manager.py
git commit -m "refactor(cli): run pipeline via SessionManager + builder"
```

---

### Task 6: Sim start/stop endpoints

**Files:**
- Modify: `algo-trade/src/api_server/server.py`
- Test: `algo-trade/tests/e2e/sim_lifecycle.spec.py`

**Interfaces:**
- Consumes: `SessionManager` (Task 4), `validate_sim_date` (from `scripts/simulate.py` — move/duplicate as `src/sim/calendar.py:validate_sim_date` to avoid importing from scripts; see step 3).
- Produces: `create_app(..., session_manager=None)` param; routes `POST /sim/start` (`{date, speed}`), `POST /sim/stop`; `/sim/status` returns the manager's status when a manager is present (else the legacy `{active:false}` / clock status). `/sim/control` returns 409 unless `state=="running"`.

- [ ] **Step 1: Write the failing test**

Create `algo-trade/tests/e2e/sim_lifecycle.spec.py`:

```python
# file: tests/e2e/sim_lifecycle.spec.py
import asyncio

from aiohttp.test_utils import TestClient, TestServer

from src.api_server.server import create_app
from src.runtime.context import RuntimeContext
from src.runtime.session_manager import SessionManager


class _Risk:
    pass


def _fake_build(_mark="live"):
    async def _idle():
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            return
    async def _build(config, mode, *, market_adapter=None, sim_clock=None, position_store=None):
        ctx = RuntimeContext(mode="sim" if sim_clock is not None else "live",
                             market_adapter=market_adapter, sim_clock=sim_clock)
        return ctx, [_idle]
    return _build


class _FakeClock:
    def __init__(self, *a, **k): pass
    def now(self):
        from datetime import datetime, timezone
        return datetime(2026, 6, 17, 9, 30, tzinfo=timezone.utc)
    def is_open(self): return True
    def status(self):
        return {"sim_time": "2026-06-17 09:30:00 ET", "speed": 60.0, "paused": False}


async def _loader(sim_date, **kw):
    return {"AAPL": [{"datetime": "2026-06-17T13:30:00+00:00", "open": 1, "high": 1,
                      "low": 1, "close": 1, "volume": 1}]}


def _make_manager():
    ctx = RuntimeContext()
    return SessionManager(
        config={"mode": "paper"}, ctx=ctx,
        build_fn=_fake_build(), load_fn=_loader, clock_factory=_FakeClock,
        adapter_factory=lambda d, c: object(),
        store_factory=lambda: ("SANDBOX", None),
    ), ctx


async def _wait_state(mgr, target, tries=100):
    for _ in range(tries):
        if mgr.state == target:
            return
        await asyncio.sleep(0.01)


async def test_start_then_stop_sim():
    mgr, ctx = _make_manager()
    await mgr.start_live()
    app = create_app(_Risk(), [], None, ctx=ctx, session_manager=mgr)
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/sim/start", json={"date": "2026-06-17", "speed": 60})
        assert r.status in (200, 202)
        await _wait_state(mgr, "running")
        st = await (await client.get("/sim/status")).json()
        assert st["state"] == "running"
        assert st["active"] is True

        r = await client.post("/sim/stop")
        assert r.status == 200
        await _wait_state(mgr, "idle")
        st = await (await client.get("/sim/status")).json()
        assert st["state"] == "idle"
    await mgr._cancel_tasks()


async def test_start_rejects_bad_date():
    mgr, ctx = _make_manager()
    await mgr.start_live()
    app = create_app(_Risk(), [], None, ctx=ctx, session_manager=mgr)
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/sim/start", json={"date": "2026-06-20", "speed": 60})  # Saturday
        assert r.status == 422
    await mgr._cancel_tasks()


async def test_control_409_when_not_running():
    mgr, ctx = _make_manager()
    await mgr.start_live()
    app = create_app(_Risk(), [], None, ctx=ctx, session_manager=mgr)
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/sim/control", json={"action": "pause"})
        assert r.status == 409
    await mgr._cancel_tasks()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd algo-trade && python -m pytest tests/e2e/sim_lifecycle.spec.py -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'session_manager'`.

- [ ] **Step 3: Extract `validate_sim_date` and add endpoints**

First, create `algo-trade/src/sim/calendar.py` so the server doesn't import from `scripts/`:

```python
# file: src/sim/calendar.py
"""Trading-day validation for replay dates (shared by server + simulate.py)."""
from __future__ import annotations

from datetime import date

from src.market_hours import _HOLIDAYS


def validate_sim_date(s: str) -> date:
    """Return a date for a valid NYSE trading day, else raise ValueError."""
    d = date.fromisoformat(s)
    if d.weekday() >= 5:
        raise ValueError(f"{s} is a weekend")
    if d in _HOLIDAYS:
        raise ValueError(f"{s} is a NYSE holiday")
    return d
```

Update `algo-trade/scripts/simulate.py` to reuse it (replace its local `validate_sim_date` body with a call, preserving the SystemExit behaviour):

```python
from src.sim.calendar import validate_sim_date as _validate

def validate_sim_date(s: str):
    try:
        return _validate(s)
    except ValueError as exc:
        raise SystemExit(f"{exc} — pick a trading day.")
```

In `server.py`, add `session_manager: Optional[Any] = None` as the final `create_app` param. Add handlers (near the other sim handlers):

```python
    async def sim_status(request: web.Request) -> web.Response:
        if session_manager is not None:
            return web.json_response(session_manager.status())
        if ctx.sim_clock is None:
            return web.json_response({"active": False, "state": "idle"})
        return web.json_response(ctx.sim_clock.status())

    async def sim_start(request: web.Request) -> web.Response:
        if session_manager is None:
            return web.json_response({"error": "sim control unavailable"}, status=503)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        from src.sim.calendar import validate_sim_date
        try:
            d = validate_sim_date(str(body.get("date", "")))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=422)
        try:
            speed = float(body.get("speed", 60))
            if speed <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return web.json_response({"error": "speed must be a positive number"}, status=422)
        try:
            await session_manager.start_sim(d, speed)
        except RuntimeError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        return web.json_response(session_manager.status(), status=202)

    async def sim_stop(request: web.Request) -> web.Response:
        if session_manager is None:
            return web.json_response({"error": "sim control unavailable"}, status=503)
        await session_manager.stop_sim()
        return web.json_response(session_manager.status())
```

Modify `sim_control` so it 409s unless a sim is running:

```python
    async def sim_control(request: web.Request) -> web.Response:
        running = (session_manager is not None and session_manager.state == "running") or \
                  (session_manager is None and ctx.sim_clock is not None)
        if not running:
            return web.json_response({"error": "no sim running"}, status=409)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        clock = ctx.sim_clock
        action = body.get("action")
        if action == "pause":
            clock.pause()
        elif action == "resume":
            clock.resume()
        elif action == "set_speed":
            try:
                clock.set_speed(float(body.get("speed")))
            except (TypeError, ValueError):
                return web.json_response({"error": "speed must be a positive number"}, status=422)
        else:
            return web.json_response({"error": "action must be pause|resume|set_speed"}, status=422)
        status = session_manager.status() if session_manager is not None else clock.status()
        return web.json_response(status)
```

Register the new routes in `api_routes` (both root + `/api` via the existing loop):

```python
        ("GET",  "/sim/status",  sim_status),
        ("POST", "/sim/start",   sim_start),
        ("POST", "/sim/stop",    sim_stop),
        ("POST", "/sim/control", sim_control),
```

(Replace the previous single `/sim/status` + `/sim/control` registrations with these four.)

- [ ] **Step 4: Run the new test + regression**

Run: `cd algo-trade && python -m pytest tests/e2e/sim_lifecycle.spec.py tests/e2e/sim.spec.py -v`
Expected: PASS (new lifecycle tests + the existing sim endpoint tests; update the old `sim.spec.py` only if a status-shape assertion now needs `state` — keep `active` working).
Run: `cd algo-trade && python -m pytest -q`
Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add algo-trade/src/api_server/server.py algo-trade/src/sim/calendar.py algo-trade/scripts/simulate.py algo-trade/tests/e2e/sim_lifecycle.spec.py
git commit -m "feat(sim): add POST /sim/start, /sim/stop + lifecycle status"
```

---

### Task 7: Dashboard launcher panel

**Files:**
- Modify: `algo-trade/frontend/lib/api.ts`
- Create: `algo-trade/frontend/components/dashboard/sim-launcher.tsx`
- Modify: `algo-trade/frontend/components/layout/topbar.tsx`
- Build: regenerate `algo-trade/web/`

**Interfaces:**
- Consumes: `POST /sim/start`, `POST /sim/stop`, extended `/sim/status` (Task 6).
- Produces: `api.simStart(date, speed)`, `api.simStop()`; `SimStatus` extended with `state` + `error`; `<SimLauncher />` rendered in the topbar.

- [ ] **Step 1: Extend the API client**

In `algo-trade/frontend/lib/api.ts`, extend `SimStatus`:

```ts
export interface SimStatus {
  active: boolean;
  state?: "idle" | "loading" | "running" | "stopping" | "error";
  sim_time?: string;
  sim_time_iso?: string;
  speed?: number;
  paused?: boolean;
  sim_date?: string;
  day_complete?: boolean;
  market_open?: boolean;
  error?: string | null;
}
```

Add methods to `api` (both `r.ok`-checked):

```ts
  simStart: (date: string, speed: number) =>
    fetch(`${API_BASE}/sim/start`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date, speed }),
    }).then((r) => { if (!r.ok && r.status !== 202) return r.json().then((b) => { throw new Error(b.error ?? `sim/start ${r.status}`); }); return r.json() as Promise<SimStatus>; }),
  simStop: () =>
    fetch(`${API_BASE}/sim/stop`, { method: "POST" })
      .then((r) => { if (!r.ok) throw new Error(`sim/stop ${r.status}`); return r.json() as Promise<SimStatus>; }),
```

- [ ] **Step 2: Create the launcher component**

Create `algo-trade/frontend/components/dashboard/sim-launcher.tsx`:

```tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { Play, Square, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type SimStatus } from "@/lib/api";

const SPEEDS = [
  { label: "1x", value: 1 },
  { label: "10x", value: 10 },
  { label: "60x", value: 60 },
  { label: "Max", value: 600 },
];

export function SimLauncher() {
  const [sim, setSim] = useState<SimStatus | null>(null);
  const [date, setDate] = useState("2026-06-17");
  const [speed, setSpeed] = useState(60);
  const [err, setErr] = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      setSim(await api.simStatus());
    } catch {
      setSim(null);
    }
  }, []);

  useEffect(() => {
    poll();
    const t = setInterval(poll, 1000);
    return () => clearInterval(t);
  }, [poll]);

  const state = sim?.state ?? "idle";

  async function start() {
    setErr(null);
    try {
      setSim(await api.simStart(date, speed));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to start");
    }
  }
  async function stop() {
    setErr(null);
    try {
      setSim(await api.simStop());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to stop");
    }
  }

  return (
    <div className="hidden md:flex items-center gap-1.5">
      {state === "idle" || state === "error" ? (
        <>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="px-2 py-1 rounded-md bg-zinc-800/60 border border-zinc-700/40 text-xs text-zinc-200"
          />
          <select
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
            className="px-2 py-1 rounded-md bg-zinc-800/60 border border-zinc-700/40 text-xs text-zinc-200"
          >
            {SPEEDS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
          <button
            onClick={start}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-emerald-500/15 border border-emerald-500/30 text-xs text-emerald-300 hover:bg-emerald-500/25"
          >
            <Play className="w-3.5 h-3.5" /> Start Sim
          </button>
          {(err || (state === "error" && sim?.error)) && (
            <span className="text-[11px] text-rose-400 max-w-[180px] truncate">
              {err || sim?.error}
            </span>
          )}
        </>
      ) : state === "loading" ? (
        <span className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-indigo-500/10 border border-indigo-500/25 text-xs text-indigo-300">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading market data…
        </span>
      ) : (
        <button
          onClick={stop}
          className="flex items-center gap-1 px-2.5 py-1 rounded-md bg-rose-500/15 border border-rose-500/30 text-xs text-rose-300 hover:bg-rose-500/25"
        >
          <Square className="w-3.5 h-3.5" /> Stop Sim
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Render it in the topbar**

In `algo-trade/frontend/components/layout/topbar.tsx`, add the import:

```tsx
import { SimLauncher } from "@/components/dashboard/sim-launcher";
```

Render `<SimLauncher />` as the first child of the right-hand controls group `<div className="flex items-center gap-2 shrink-0">` (before `<SimControls />`). The existing `<SimControls />` keeps showing pause/speed only when a sim is running.

- [ ] **Step 4: Build the export**

Run: `cd algo-trade/frontend && npm run build`
Expected: clean build, no TypeScript errors. Mirror the existing flow to populate `algo-trade/web/` (copy `frontend/out/*` if that is what the Docker/build step does — `web/` is gitignored, so do NOT commit it).

- [ ] **Step 5: Commit (source only)**

```bash
git add algo-trade/frontend/lib/api.ts algo-trade/frontend/components/dashboard/sim-launcher.tsx algo-trade/frontend/components/layout/topbar.tsx
git commit -m "feat(sim): add Start/Stop simulation launcher to dashboard"
```

---

## Final Verification

- [ ] `cd algo-trade && python -m pytest -q` → all green (new runtime + sim-lifecycle tests plus the unchanged existing suite).
- [ ] `python -c "import scripts.simulate as s"` → terminal sim path still imports.
- [ ] Manual smoke: launch the server (`DEV_MODE=1 python -m src.cli.main --mode paper`), open the dashboard, click **Start Sim** (date 2026-06-17, 60x) → status goes Loading → running, positions/signals appear in the sim sandbox, **Stop Sim** → dashboard returns to real paper data. (Heavy first fetch ~1–2 min.)
- [ ] Confirm real paper DB rows are unchanged after a sim run (sandbox isolation).

## Self-Review Notes (coverage vs spec)

- `RuntimeContext` swappable holder → Task 1. ✓
- Endpoints read via ctx (live swap observed) → Task 2 (compat shim keeps existing tests green). ✓
- `pipeline_builder` shared live/sim wiring → Task 3. ✓
- `SessionManager` lifecycle (idle/loading/running/stopping/error, sandbox temp-file store, 409/error/restore) → Task 4. ✓
- cli/main rewired; API server runs continuously; simulate.py preserved → Task 5. ✓
- `POST /sim/start`, `/sim/stop`, extended `/sim/status`, `/sim/control` 409-when-idle, `validate_sim_date` shared via `src/sim/calendar.py` → Task 6. ✓
- Dashboard launcher + async loading state + api client → Task 7. ✓
- Sandbox isolation asserted (real store untouched) → Task 6 e2e + final manual check. ✓
- No-behaviour-change-when-off guaranteed by ctx shim defaults + sim_clock/session_manager defaults None. ✓
```
