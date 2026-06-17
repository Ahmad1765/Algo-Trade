# file: tests/e2e/full_pipeline.spec.py
"""
Full end-to-end pipeline integration tests.

Tests the complete async pipeline:
  MockMarketAdapter → Screener → [candidate_queue] → OptionsFetcher (mocked)
  → [chain_queue] → StrategyEngine → [signal_queue] → OrderManager

Each test drives the pipeline by injecting events at the queue level
to avoid the latency of real polling loops, while exercising the actual
inter-component handshaking and data transformations.

Covers:
  - A CandidateEvent with gainers flows through to produce a SignalEvent
  - Signal event direction matches indicator conditions
  - Position is persisted after a successful paper-trade fill
  - Signal is NOT generated when risk manager rejects the trade
  - Signal cooldown prevents a duplicate signal for the same ticker
  - WebSocket clients receive the signal broadcast
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import src.api_server.server as server_mod
from aiohttp.test_utils import TestClient, TestServer

from src.events import (
    CandidateEvent,
    OptionChainEvent,
    SignalDirection,
    SignalEvent,
)
from src.strategy_engine.engine import StrategyEngine
from tests.e2e.test_helpers import (
    drain_queue,
    make_candidate_event,
    make_chain_event,
    make_market_quote,
    make_option_contract,
    make_signal_event,
)

pytestmark = [pytest.mark.e2e, pytest.mark.pipeline]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_strategy_engine(e2e_config, chain_q, signal_q, position_store=None):
    from src.market_adapter.mock_market import MockMarketAdapter
    return StrategyEngine(
        market_adapter=MockMarketAdapter(),
        chain_queue=chain_q,
        signal_queue=signal_q,
        config=e2e_config,
        position_store=position_store,
        notifier=None,
    )


def _make_order_manager(e2e_config, signal_q, position_store=None):
    from src.execution.mock_adapter import MockBrokerAdapter
    from src.execution.order_manager import OrderManager
    from src.risk_manager import RiskManager

    broker = MockBrokerAdapter(equity=100_000)
    rm = RiskManager(e2e_config)
    return OrderManager(
        broker=broker,
        risk_manager=rm,
        signal_queue=signal_q,
        mode="paper",
        config=e2e_config,
        position_store=position_store,
        notifier=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFullPipelineSignalGeneration:
    async def test_chain_event_with_overbought_rsi_produces_call_signal(self, e2e_config):
        """StrategyEngine receives an OptionChainEvent and emits a CALL SignalEvent."""
        chain_q = asyncio.Queue()
        signal_q = asyncio.Queue()
        engine = _make_strategy_engine(e2e_config, chain_q, signal_q)

        chain_event = make_chain_event("AAPL", contracts=[
            make_option_contract("AAPL", option_type="call"),
            make_option_contract("AAPL", option_type="put"),
        ])

        with patch.object(
            engine, "_compute_indicators",
            new=AsyncMock(return_value=(74.0, MagicMock(histogram=0.10), 1.5))
        ):
            await engine._process_chain(chain_event)

        signals = await drain_queue(signal_q, expected=1, timeout=1.0)
        assert len(signals) == 1
        assert signals[0].trade_plan.direction == SignalDirection.CALL
        assert signals[0].trade_plan.symbol == "AAPL"

    async def test_chain_event_with_oversold_rsi_produces_put_signal(self, e2e_config):
        chain_q = asyncio.Queue()
        signal_q = asyncio.Queue()
        engine = _make_strategy_engine(e2e_config, chain_q, signal_q)

        chain_event = make_chain_event("SPY", contracts=[
            make_option_contract("SPY", option_type="call"),
            make_option_contract("SPY", option_type="put", delta=-0.42),
        ])

        with patch.object(
            engine, "_compute_indicators",
            new=AsyncMock(return_value=(27.0, MagicMock(histogram=-0.08), 2.0))
        ):
            await engine._process_chain(chain_event)

        signals = await drain_queue(signal_q, expected=1, timeout=1.0)
        assert len(signals) == 1
        assert signals[0].trade_plan.direction == SignalDirection.PUT


class TestFullPipelineExecution:
    async def test_signal_leads_to_persisted_position(self, e2e_config, position_store):
        """OrderManager paper-trades a signal and stores the position."""
        signal_q = asyncio.Queue()
        mgr = _make_order_manager(e2e_config, signal_q, position_store)

        signal = make_signal_event("TSLA", SignalDirection.CALL)
        await signal_q.put(signal)

        task = asyncio.ensure_future(mgr.run())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        positions = position_store.get_positions()
        assert any(v["symbol"] == "TSLA" for v in positions.values())

    async def test_second_signal_for_same_ticker_skipped_by_cooldown(
        self, e2e_config, position_store
    ):
        """After a signal is processed, cooldown prevents a second one for the same symbol."""
        chain_q = asyncio.Queue()
        signal_q = asyncio.Queue()
        engine = _make_strategy_engine(e2e_config, chain_q, signal_q, position_store)

        chain_event = make_chain_event("NVDA", contracts=[
            make_option_contract("NVDA", option_type="call"),
        ])

        with patch.object(
            engine, "_compute_indicators",
            new=AsyncMock(return_value=(75.0, MagicMock(histogram=0.12), 1.8))
        ):
            # First call — should generate signal and set cooldown
            await engine._process_chain(chain_event)
            position_store.set_cooldown("NVDA")  # Simulate cooldown being set

            # Second call — should be suppressed
            await engine._process_chain(chain_event)

        signals = await drain_queue(signal_q, expected=1, timeout=0.5)
        # Only 1 signal should be in the queue (second was blocked by cooldown)
        assert len(signals) == 1

    async def test_signal_not_generated_when_risk_rejects(self, e2e_config):
        """When the risk manager has no capacity left, no order is placed."""
        from src.risk_manager import RiskManager

        # Fill up all position slots
        filled_risk = MagicMock(spec=RiskManager)
        filled_risk.approve.return_value = (False, "mock rejection")

        signal_q = asyncio.Queue()
        from src.execution.mock_adapter import MockBrokerAdapter
        from src.execution.order_manager import OrderManager

        mgr = OrderManager(
            broker=MockBrokerAdapter(equity=100_000),
            risk_manager=filled_risk,
            signal_queue=signal_q,
            mode="paper",
            config=e2e_config,
            position_store=None,
        )

        placed_orders = []
        original_place = mgr._broker.place_limit_order

        async def spy_place(*args, **kwargs):
            placed_orders.append(True)
            return await original_place(*args, **kwargs)

        mgr._broker.place_limit_order = spy_place

        signal = make_signal_event("AAPL")
        await mgr._handle_signal(signal)

        assert len(placed_orders) == 0


@pytest.mark.skip(reason="WebSocket endpoint (/ws) and _broadcast/_ws_clients removed from current server")
class TestFullPipelineWebSocketBroadcast:
    async def test_signal_broadcast_reaches_connected_ws_client(
        self, make_app, signal_store
    ):
        """When a signal is broadcast (e.g. after OrderManager fills),
        connected WebSocket clients receive the signal message."""
        server_mod._ws_clients.clear()
        app = make_app(sig_store=signal_store)

        async with TestClient(TestServer(app)) as client:
            ws = await client.ws_connect("/ws")
            await ws.receive_json(timeout=3)  # consume snapshot

            await server_mod._broadcast({
                "type": "signal",
                "data": {"symbol": "AAPL", "direction": "CALL", "entry": 2.55}
            })

            msg = await ws.receive_json(timeout=3)
            assert msg["type"] == "signal"
            assert msg["data"]["symbol"] == "AAPL"
            await ws.close()

    async def test_position_changes_reflected_in_ws_snapshot_on_next_connect(
        self, make_app, signal_store, position_store
    ):
        """After a position is added, a newly-connecting WS client sees it in the snapshot."""
        server_mod._ws_clients.clear()
        position_store.add_position(
            "AAPL_2026-05-16_175.0_C", "AAPL", "CALL", 2.55, 1.85, 4.15, 10
        )
        app = make_app(sig_store=signal_store, pos_store=position_store)

        async with TestClient(TestServer(app)) as client:
            ws = await client.ws_connect("/ws")
            msg = await ws.receive_json(timeout=3)
            assert "AAPL_2026-05-16_175.0_C" in msg["positions"]
            await ws.close()
