# file: src/cli/main.py
"""
Command-line entry point — production version.

Features:
  - Auto-loads .env file
  - Graceful shutdown on Ctrl+C or SIGTERM
  - Wires PositionStore and Notifier into all components via build_pipeline
  - Market-hours gate built into screener
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any, Dict

from src.api_server.server import create_app, run_api_server
from src.config import load_config
from src.logger import get_logger
from src.runtime.pipeline_builder import build_pipeline
from src.runtime.session_manager import SessionManager

log = get_logger(__name__)


def _attach_shutdown(loop: asyncio.AbstractEventLoop, tasks: list) -> None:
    """Register SIGINT/SIGTERM handlers for graceful shutdown."""
    def _shutdown():
        log.info("shutdown signal received — cancelling tasks")
        for t in tasks:
            t.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            # Windows: fall back to signal.signal for SIGINT (Ctrl+C)
            try:
                signal.signal(sig, lambda *_: loop.call_soon_threadsafe(_shutdown))
            except (OSError, ValueError):
                pass


async def _run_pipeline(
    config: Dict[str, Any],
    mode: str,
    *,
    market_adapter: Any = None,
    sim_clock: Any = None,
) -> None:
    # Restore any config overrides the user saved via the dashboard.
    from src.persistence import PositionStore
    from src.config import update_config
    _ov_store = PositionStore()
    _db_overrides = _ov_store.get_config_overrides()
    if _db_overrides:
        update_config(_db_overrides)
        log.info("loaded config overrides from database")

    # Build the initial pipeline (live, or sim when an adapter/clock is injected).
    ctx, runnables = await build_pipeline(
        config, mode, market_adapter=market_adapter, sim_clock=sim_clock,
    )

    manager = SessionManager(config, ctx)
    # Adopt the just-built pipeline as the manager's current session.
    manager.adopt_running(ctx, runnables, state="running" if sim_clock is not None else "idle")

    api_cfg = config.get("api_server", {})
    # Railway injects PORT; respect it over the config file value.
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

    # Shutdown is signalled by setting this event. The pipeline tasks are owned
    # by the SessionManager (and may be swapped during a sim), so we do NOT
    # gather them here — instead we keep the process alive on the event while
    # the aiohttp runner serves in the background, then tear down on signal.
    stop_event = asyncio.Event()

    def _trigger_stop() -> None:
        log.info("shutdown signal received")
        stop_event.set()

    for _sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(_sig, _trigger_stop)
        except NotImplementedError:
            # Windows: fall back to signal.signal for SIGINT (Ctrl+C)
            try:
                signal.signal(_sig, lambda *_: loop.call_soon_threadsafe(_trigger_stop))
            except (OSError, ValueError):
                pass

    async def _run_all() -> None:
        # Starts the aiohttp site and RETURNS immediately (it is not long-lived);
        # the runner keeps serving while the loop stays alive on the event below.
        await run_api_server(app, api_cfg.get("host", "0.0.0.0"), api_port)
        try:
            await stop_event.wait()
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


def _run_backtest(config: Dict[str, Any], data_path: str) -> None:
    from src.backtester import Backtester
    bt = Backtester(config)
    result = bt.run(data_path)
    result.print_report()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Algorithmic options trading system."
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "manual", "automated", "backtest"],
        default="paper",
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--data",   default="sample_data/minute_sample.csv")
    args = parser.parse_args()

    config = load_config(Path(args.config))

    if args.mode == "backtest":
        _run_backtest(config, args.data)
        sys.exit(0)

    try:
        asyncio.run(_run_pipeline(config, args.mode))
    except KeyboardInterrupt:
        log.info("keyboard interrupt — exiting")


if __name__ == "__main__":
    main()
