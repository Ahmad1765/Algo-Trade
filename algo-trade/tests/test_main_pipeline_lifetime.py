# file: tests/test_main_pipeline_lifetime.py
"""
Regression: _run_pipeline must keep the process alive after starting the API
server. run_api_server() returns as soon as the aiohttp site is bound (it is
NOT long-lived), so the launcher must block on a shutdown event — not on the
server coroutine, which would complete instantly and trigger immediate exit.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.cli import main
from src.runtime.context import RuntimeContext


def test_run_pipeline_stays_alive_until_shutdown(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "1")  # let assert_auth_config pass
    cfg = {"mode": "paper", "api_server": {"host": "127.0.0.1", "port": 0}}

    fake_ctx = RuntimeContext(mode="live")

    async def _fake_build(config, mode, *, market_adapter=None, sim_clock=None):
        return fake_ctx, []  # no pipeline runnables

    monkeypatch.setattr(main, "build_pipeline", _fake_build)
    monkeypatch.setattr(main, "create_app", MagicMock(return_value=object()))
    # run_api_server returns immediately (mirrors real behaviour: binds + returns)
    monkeypatch.setattr(main, "run_api_server", AsyncMock(return_value=None))

    fake_store = MagicMock()
    fake_store.get_config_overrides.return_value = {}
    monkeypatch.setattr("src.persistence.PositionStore", MagicMock(return_value=fake_store))

    async def go():
        task = asyncio.ensure_future(main._run_pipeline(cfg, "paper"))
        await asyncio.sleep(0.2)
        alive = not task.done()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return alive

    alive = asyncio.run(go())
    assert alive, "regression: _run_pipeline exited immediately after starting the API server"
