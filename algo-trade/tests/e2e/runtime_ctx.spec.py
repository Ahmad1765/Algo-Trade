# file: tests/e2e/runtime_ctx.spec.py
"""Endpoints must read live from RuntimeContext so a swap is observed."""
from aiohttp.test_utils import TestClient, TestServer

from src.api_server.server import create_app
from src.runtime.context import RuntimeContext


class _Risk:
    pass


async def test_create_app_accepts_ctx_and_reads_from_it():
    ctx = RuntimeContext(risk_manager=_Risk(), signal_store=[], position_store=None)
    app = create_app(_Risk(), [], None, ctx=ctx)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/health")
        assert resp.status == 200


async def test_signal_swap_is_observed_live():
    ctx = RuntimeContext(risk_manager=_Risk(), signal_store=[{"symbol": "AAA"}])
    app = create_app(_Risk(), [], None, ctx=ctx)
    async with TestClient(TestServer(app)) as client:
        first = await (await client.get("/signals")).json()
        assert len(first) == 1
        # Swap the signal store on the context; endpoint must see the new list.
        ctx.signal_store = [{"symbol": "BBB"}, {"symbol": "CCC"}]
        second = await (await client.get("/signals")).json()
        assert len(second) == 2
        assert second[-1]["symbol"] == "CCC"


async def test_legacy_args_still_work_without_ctx():
    # No ctx passed -> built internally from legacy args (back-compat).
    app = create_app(_Risk(), [{"symbol": "X"}], None)
    async with TestClient(TestServer(app)) as client:
        sigs = await (await client.get("/signals")).json()
        assert sigs == [{"symbol": "X"}]
