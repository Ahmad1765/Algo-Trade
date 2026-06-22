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
