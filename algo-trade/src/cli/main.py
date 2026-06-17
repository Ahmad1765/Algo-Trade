# file: src/cli/main.py
"""
Command-line entry point — production version.

Features:
  - Auto-loads .env file
  - Graceful shutdown on Ctrl+C or SIGTERM
  - Wires PositionStore and Notifier into all components
  - Market-hours gate built into screener
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Any, Dict, List

from src.api_server.server import create_app, run_api_server
from src.config import load_config
from src.events import SignalEvent
from src.execution.base import create_broker_adapter
from src.execution.order_manager import OrderManager
from src.logger import get_logger
from src.market_adapter.base import create_market_adapter
from src.notifier import Notifier
from src.options_fetcher import OptionsFetcher
from src.persistence import PositionStore
from src.risk_manager import RiskManager
from src.screener import Screener
from src.strategy_engine import MultiStrategyEngine

log = get_logger(__name__)

_signal_store: List[Dict] = []
_action_store: List[Dict] = []


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


async def _run_pipeline(config: Dict[str, Any], mode: str) -> None:
    market_adapter  = create_market_adapter(config)
    broker_adapter  = create_broker_adapter(config)
    risk_manager    = RiskManager(config)
    position_store  = PositionStore()
    notifier        = Notifier(config)

    # Load UI-saved config overrides from DB (Railway-safe persistence)
    db_overrides = position_store.get_config_overrides()
    if db_overrides:
        from src.config import update_config as _update_cfg
        _update_cfg(db_overrides)
        log.info("loaded config overrides from database")

    # Note: positions are re-registered with the risk manager inside
    # order_mgr.recover_open_positions() — no need to register here.
    if position_store.open_count:
        log.info("restored positions from database", count=position_store.open_count)

    # Restore recent signals from DB
    existing_signals = position_store.get_signals(limit=200)
    _signal_store.extend(existing_signals)
    if existing_signals:
        log.info("restored signal history from database", count=len(existing_signals))

    # Restore recent activity from DB
    existing_actions = position_store.get_actions(limit=200)
    _action_store.extend(existing_actions)
    if existing_actions:
        log.info("restored action history from database", count=len(existing_actions))

    # Log system startup as an action
    position_store.add_action("SYSTEM_STARTED", None, f"Pipeline started in {mode} mode", {"mode": mode})
    from datetime import datetime, timezone as _tz
    _action_store.append({
        "event": "SYSTEM_STARTED", "symbol": None,
        "detail": f"Pipeline started in {mode} mode",
        "data": {"mode": mode},
        "ts": datetime.now(_tz.utc).isoformat(),
    })

    candidate_queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    chain_queue:     asyncio.Queue = asyncio.Queue(maxsize=50)
    signal_queue:    asyncio.Queue = asyncio.Queue(maxsize=20)
    tap_queue:       asyncio.Queue = asyncio.Queue(maxsize=50)

    screener   = Screener(market_adapter, candidate_queue, config)
    fetcher    = OptionsFetcher(broker_adapter, candidate_queue, chain_queue, config)
    engine     = MultiStrategyEngine(
        market_adapter, chain_queue, signal_queue, config,
        position_store=position_store, notifier=notifier, tap_queue=tap_queue,
    )
    order_mgr  = OrderManager(
        broker_adapter, risk_manager, signal_queue, mode, config,
        position_store=position_store, notifier=notifier,
        action_store=_action_store, market_adapter=market_adapter,
    )

    # Re-arm stop monitors for positions that survived a restart.
    await order_mgr.recover_open_positions()

    # Signal tap: observe signals on the dedicated tap_queue (separate from order manager).
    async def _signal_tap() -> None:
        while True:
            try:
                sig: SignalEvent = await asyncio.wait_for(tap_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            plan = sig.trade_plan
            data = {
                "symbol":    plan.symbol,
                "direction": plan.direction.value,
                "strike":    plan.contract.strike,
                "expiry":    plan.contract.expiry,
                "entry":     plan.entry_limit,
                "stop":      plan.stop_loss,
                "target":    plan.take_profit,
                "size":      plan.position_size,
                "rationale": plan.rationale,
                "strategy":  plan.strategy_name,
                "ts":        sig.timestamp.isoformat(),
            }
            _signal_store.append(data)
            position_store.add_signal(data)  # Persist to DB
            if len(_signal_store) > 200:
                _signal_store.pop(0)

    api_cfg = config.get("api_server", {})
    # Railway injects PORT; respect it over the config file value.
    api_port = int(os.environ.get("PORT") or os.environ.get("API_PORT") or api_cfg.get("port", 8181))
    from src.api_server import auth as _auth
    _auth.assert_auth_config()
    app = create_app(risk_manager, _signal_store, position_store, market_adapter, _action_store, broker_adapter, strategy_engine=engine)

    log.info("pipeline starting", mode=mode)

    loop = asyncio.get_event_loop()
    task_list = []

    async def _run_all() -> None:
        tasks = [
            asyncio.ensure_future(screener.run()),
            asyncio.ensure_future(fetcher.run()),
            asyncio.ensure_future(engine.run()),
            asyncio.ensure_future(order_mgr.run()),
            asyncio.ensure_future(_signal_tap()),
            asyncio.ensure_future(
                run_api_server(app, api_cfg.get("host", "0.0.0.0"), api_port)
            ),
        ]
        task_list.extend(tasks)
        _attach_shutdown(loop, tasks)
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                    log.error("pipeline task failed", error=str(result), exc_info=result)
        except asyncio.CancelledError:
            pass
        finally:
            log.info("shutting down — closing adapters")
            await market_adapter.close()
            await broker_adapter.close()

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
