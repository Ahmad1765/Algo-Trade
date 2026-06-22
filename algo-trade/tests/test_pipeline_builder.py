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
