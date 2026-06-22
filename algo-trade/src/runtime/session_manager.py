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
            saved_error = str(exc)
            await self._cancel_tasks()
            await self.start_live()
            self.error = saved_error   # restore after start_live clears it
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
