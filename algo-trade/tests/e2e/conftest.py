# file: tests/e2e/conftest.py
"""
E2E test fixtures — shared across all spec files in this directory.

Design principles:
  - All external I/O (broker SDKs, SMTP, webhooks, FMP API) is mocked.
  - signal_store is a plain list (matching the current server's list-based store).
  - PositionStore always writes to pytest's tmp_path.
  - Each fixture creates a clean slate; no global state bleeds between tests.
"""
from __future__ import annotations

import os
os.environ.setdefault("DEV_MODE", "1")  # existing specs run with auth disabled

import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Minimal but complete config
# ---------------------------------------------------------------------------

@pytest.fixture
def e2e_config() -> Dict[str, Any]:
    """Full config dict suitable for wiring all components together."""
    return {
        "mode": "paper",
        "screener": {
            "provider": "mock",
            "top_n": 5,
            "poll_interval_seconds": 0.05,
            "market_hours_only": False,
        },
        "options_filter": {
            "min_volume": 100,
            "min_open_interest": 500,
            "max_spread_pct": 0.10,
            "max_dte": 30,
            "min_dte": 1,
            "max_otm_pct": 0.15,
        },
        "indicators": {
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "atr_period": 14,
            "lookback_bars": 50,
            "signal_cooldown_minutes": 30,
        },
        "risk": {
            "max_position_pct": 0.05,
            "max_open_positions": 5,
            "pdt_equity_threshold": 25000,
            "stop_loss_atr_mult": 1.5,
            "take_profit_atr_mult": 3.0,
        },
        "broker": {"name": "mock"},
        "market_data": {
            "fmp_api_key": "test-key",
            "base_url": "https://financialmodelingprep.com/api/v3",
            "request_timeout": 5,
            "retry_max": 3,
            "retry_backoff": 0.1,
        },
        "logging": {"level": "WARNING", "json_format": False},
        "api_server": {"host": "127.0.0.1", "port": 18081},
        "notifications": {
            "email": {"enabled": False},
            "webhook": {"enabled": False},
        },
    }


# ---------------------------------------------------------------------------
# Signal store — plain list (matches current server's list-based store).
# Bug fix: removed SignalStore dependency; src.signal_store was removed from
# the codebase. The current server uses a plain list for signal_store.
# ---------------------------------------------------------------------------

@pytest.fixture
def signal_store() -> List[Dict[str, Any]]:
    """Empty in-memory signal list — clean slate per test."""
    return []


@pytest.fixture
def seeded_signal_store() -> List[Dict[str, Any]]:
    """Signal list pre-populated with fixture data from seed_signals.json."""
    return json.loads((FIXTURES_DIR / "seed_signals.json").read_text())


# ---------------------------------------------------------------------------
# Seed signal helper
# ---------------------------------------------------------------------------

@pytest.fixture
def seed_signals() -> List[Dict[str, Any]]:
    """Return the raw fixture signal list."""
    return json.loads((FIXTURES_DIR / "seed_signals.json").read_text())


# ---------------------------------------------------------------------------
# PositionStore (tmp_path-backed)
# Bug fix: removed _LOCK_FILE patch — that attribute was never present in
# the current persistence.py, causing AttributeError on test setup.
# ---------------------------------------------------------------------------

@pytest.fixture
def position_store(tmp_path):
    """PositionStore using isolated temp SQLite — clean slate per test."""
    import src.persistence as pm

    db_url = f"sqlite:///{tmp_path}/test.db"

    with patch("src.persistence.get_config", return_value={"database": {"url": db_url}}):
        store = pm.PositionStore()

    yield store


# ---------------------------------------------------------------------------
# Mock broker
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_broker():
    """MockBrokerAdapter with $100 000 starting equity."""
    from src.execution.mock_adapter import MockBrokerAdapter
    return MockBrokerAdapter(equity=100_000)


# ---------------------------------------------------------------------------
# RiskManager
# ---------------------------------------------------------------------------

@pytest.fixture
def risk_manager(e2e_config):
    from src.risk_manager import RiskManager
    return RiskManager(e2e_config)


# ---------------------------------------------------------------------------
# aiohttp test app factory
# Bug fix: removed broker_adapter param (no longer accepted by create_app).
#          Removed _ws_clients.clear() call (_ws_clients removed from server).
# ---------------------------------------------------------------------------

@pytest.fixture
def make_app(e2e_config, signal_store, risk_manager):
    """
    Factory that returns a configured aiohttp.web.Application.

    Usage in tests::

        async def test_something(make_app):
            app = make_app()
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/health")
    """
    def _factory(
        sig_store=None,
        pos_store=None,
    ):
        from src.api_server.server import create_app

        return create_app(
            risk_manager=risk_manager,
            signal_store=sig_store if sig_store is not None else signal_store,
            position_store=pos_store,
        )

    return _factory


# ---------------------------------------------------------------------------
# Sample OHLCV bars helper
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_bars_path() -> Path:
    """Absolute path to the sample_bars.csv fixture file."""
    return FIXTURES_DIR / "sample_bars.csv"


# ---------------------------------------------------------------------------
# Option contract builder
# ---------------------------------------------------------------------------

@pytest.fixture
def make_contract():
    """Factory for OptionContract objects with sensible defaults."""
    from datetime import date, timedelta
    from src.events import OptionContract

    def _build(
        symbol: str = "AAPL",
        strike: float = 175.0,
        option_type: str = "call",
        bid: float = 2.40,
        ask: float = 2.50,
        volume: int = 3000,
        open_interest: int = 12000,
        iv: float = 0.32,
        delta: float = 0.48,
        underlying_price: float = 174.50,
        dte: int = 14,
    ) -> OptionContract:
        expiry = (date.today() + timedelta(days=dte)).isoformat()
        return OptionContract(
            symbol=symbol,
            expiry=expiry,
            strike=strike,
            option_type=option_type,
            bid=bid,
            ask=ask,
            volume=volume,
            open_interest=open_interest,
            implied_volatility=iv,
            delta=delta,
            underlying_price=underlying_price,
        )

    return _build
