# file: src/execution/mock_adapter.py
"""
Mock broker adapter for paper trading and unit/integration tests.

Behaviour:
- get_option_chain: returns a synthetic chain of 20 contracts per symbol.
- place_limit_order: immediately transitions to PARTIALLY_FILLED then FILLED
  with a simulated fill price within ±0.5% of the limit price.
- cancel_order: always succeeds if order is not yet FILLED.
- get_order_status: reflects the current in-memory state.
- get_account_equity: returns a fixed configurable value (default $100,000).

All methods print human-readable order actions to stdout (visible in paper mode).
"""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from src.events import OptionContract, OrderEvent, OrderSide, OrderStatus
from src.execution.base import BrokerAdapter
from src.logger import get_logger

log = get_logger(__name__)

_EQUITY = 1000.0
_NEUTRAL_SPOT = 150.0  # baseline underlying price used for delta-based option pricing


def _make_chain(symbol: str, spot: float = 150.0) -> List[OptionContract]:
    """Generate a synthetic option chain for *symbol* with delta-based pricing.

    Option prices respond to *spot* so that stop-loss / take-profit thresholds in
    the stop monitor can be triggered as the simulated underlying drifts.
    """
    expiry = (date.today() + timedelta(days=14)).isoformat()
    spot_move = spot - _NEUTRAL_SPOT
    contracts = []
    for offset in [-10, -5, 0, 5, 10]:
        for opt_type in ("call", "put"):
            strike = round(spot + offset, 0)
            base_mid = max(0.10, 2.0 - abs(offset) * 0.1)
            # Delta approximation: calls gain when spot rises, puts gain when spot falls.
            delta = 0.5 - abs(offset) * 0.03
            if opt_type == "put":
                delta = -delta
            mid = round(max(0.05, base_mid + delta * spot_move * 0.02), 2)
            bid = round(max(0.05, mid - 0.05), 2)
            ask = round(bid + 0.10, 2)
            contracts.append(
                OptionContract(
                    symbol=symbol,
                    expiry=expiry,
                    strike=strike,
                    option_type=opt_type,
                    bid=bid,
                    ask=ask,
                    volume=1_000 + abs(offset) * 50,
                    open_interest=5_000 + abs(offset) * 100,
                    implied_volatility=0.35 + abs(offset) * 0.01,
                    delta=0.5 - offset * 0.05 if opt_type == "call" else -0.5 + offset * 0.05,
                    underlying_price=spot,
                )
            )
    return contracts


class MockBrokerAdapter(BrokerAdapter):
    """In-memory broker adapter; no network access."""

    def __init__(self, equity: float = _EQUITY, config: dict = None) -> None:
        if config is not None:
            equity = float(
                config.get("paper_trading", {}).get("initial_capital", equity)
            )
        self._initial_equity = equity
        self._equity = equity
        self._orders: Dict[str, OrderEvent] = {}
        # Tracks per-symbol cumulative price drift so the stop monitor sees
        # changing option prices on every poll and can trigger exits.
        self._price_offsets: Dict[str, float] = {}

    def reset(self) -> None:
        """Reset all in-memory state to initial conditions (called on paper-trading reset)."""
        self._equity = self._initial_equity
        self._orders.clear()
        self._price_offsets.clear()

    async def get_option_chain(self, symbol: str, underlying_price: float = 0.0) -> List[OptionContract]:
        # Advance the simulated underlying by a random-walk step (~1.5 pt std dev).
        self._price_offsets[symbol] = self._price_offsets.get(symbol, 0.0) + random.gauss(0, 1.5)
        spot = (underlying_price if underlying_price > 0 else _NEUTRAL_SPOT) + self._price_offsets[symbol]
        return _make_chain(symbol, spot=spot)

    async def place_limit_order(
        self,
        option_symbol: str,
        side: str,
        quantity: int,
        limit_price: float,
    ) -> OrderEvent:
        order_id = str(uuid.uuid4())[:8]
        order = OrderEvent(
            order_id=order_id,
            symbol=option_symbol.split("_")[0] if "_" in option_symbol else option_symbol,
            option_symbol=option_symbol,
            side=OrderSide(side),
            quantity=quantity,
            limit_price=limit_price,
        )
        order.transition(OrderStatus.SUBMITTED)

        # Simulate partial fill then full fill.
        order.filled_qty = quantity // 2
        order.avg_fill_price = round(limit_price * 0.998, 2)
        order.transition(OrderStatus.PARTIALLY_FILLED)

        order.filled_qty = quantity
        order.avg_fill_price = round(limit_price * 0.999, 2)
        order.transition(OrderStatus.FILLED)

        self._orders[order_id] = order

        # Update equity: BUY costs capital, SELL returns it.
        fill_value = order.avg_fill_price * quantity * 100
        if side.upper() == OrderSide.BUY.value.upper():
            self._equity -= fill_value
        else:
            self._equity += fill_value

        log.info(
            "[MOCK BROKER] order placed and filled",
            order_id=order_id,
            option_symbol=option_symbol,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            fill_price=order.avg_fill_price,
        )
        return order

    async def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if order is None:
            return False
        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            return False
        order.transition(OrderStatus.CANCELLED)
        log.info("[MOCK BROKER] order cancelled", order_id=order_id)
        return True

    async def get_order_status(self, order_id: str) -> OrderEvent:
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"Unknown order_id: {order_id}")
        return order

    async def get_account_equity(self) -> float:
        return self._equity
