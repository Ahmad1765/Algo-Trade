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
