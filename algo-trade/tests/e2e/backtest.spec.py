# file: tests/e2e/backtest.spec.py
"""
E2E tests for GET /backtest-run

Covers:
  - HTTP 200 with correct JSON keys when Backtester succeeds
  - HTTP 500 with {"error": ...} when Backtester raises
  - ?data= query param is forwarded to Backtester.run()
  - Default data path is used when ?data= is omitted
  - Numeric result fields are present and correctly typed
  - equity_curve is a list
  - trade_log is a list
  - Each trade_log entry has required fields
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

pytestmark = [pytest.mark.e2e, pytest.mark.skip(reason="GET /backtest-run endpoint removed from current server")]

EXPECTED_KEYS = {
    "total_return_pct", "win_rate", "total_trades", "winning_trades",
    "losing_trades", "max_drawdown_pct", "sharpe_ratio",
    "avg_win", "avg_loss", "profit_factor", "equity_curve", "trade_log",
}


def _mock_backtest_result(
    total_return_pct=12.34,
    win_rate=0.60,
    total_trades=10,
):
    result = MagicMock()
    result.total_return_pct = total_return_pct
    result.win_rate = win_rate
    result.total_trades = total_trades
    result.winning_trades = 6
    result.losing_trades = 4
    result.max_drawdown_pct = 5.0
    result.sharpe_ratio = 1.25
    result.avg_win = 2.50
    result.avg_loss = 1.00
    result.profit_factor = 1.80
    result.equity_curve = [
        {"bar": 0, "equity": 10000.0},
        {"bar": 1, "equity": 10200.0},
        {"bar": 2, "equity": 10150.0},
        {"bar": 3, "equity": 10400.0},
    ]
    result.trade_log = [
        {"symbol": "AAPL", "direction": "CALL", "entry": 1.50, "exit": 2.50,
         "pnl": 100.0, "bars_held": 5},
        {"symbol": "SPY", "direction": "PUT", "entry": 3.00, "exit": 2.00,
         "pnl": 100.0, "bars_held": 3},
    ]
    return result


class TestBacktestRunEndpoint:
    async def test_backtest_returns_200_on_success(self, make_app):
        mock_bt = MagicMock()
        mock_bt.run.return_value = _mock_backtest_result()

        with patch("src.backtester.Backtester", return_value=mock_bt):
            async with TestClient(TestServer(make_app())) as client:
                resp = await client.get("/backtest-run?data=test.csv")
                assert resp.status == 200

    async def test_backtest_response_contains_all_expected_keys(self, make_app):
        mock_bt = MagicMock()
        mock_bt.run.return_value = _mock_backtest_result()

        with patch("src.backtester.Backtester", return_value=mock_bt):
            async with TestClient(TestServer(make_app())) as client:
                body = await (await client.get("/backtest-run?data=test.csv")).json()
                assert EXPECTED_KEYS.issubset(body.keys())

    async def test_backtest_total_return_pct_is_correct(self, make_app):
        mock_bt = MagicMock()
        mock_bt.run.return_value = _mock_backtest_result(total_return_pct=18.75)

        with patch("src.backtester.Backtester", return_value=mock_bt):
            async with TestClient(TestServer(make_app())) as client:
                body = await (await client.get("/backtest-run?data=test.csv")).json()
                assert body["total_return_pct"] == pytest.approx(18.75)

    async def test_backtest_equity_curve_is_list(self, make_app):
        mock_bt = MagicMock()
        mock_bt.run.return_value = _mock_backtest_result()

        with patch("src.backtester.Backtester", return_value=mock_bt):
            async with TestClient(TestServer(make_app())) as client:
                body = await (await client.get("/backtest-run?data=test.csv")).json()
                assert isinstance(body["equity_curve"], list)
                assert len(body["equity_curve"]) > 0

    async def test_backtest_equity_curve_entries_have_bar_and_equity(self, make_app):
        mock_bt = MagicMock()
        mock_bt.run.return_value = _mock_backtest_result()

        with patch("src.backtester.Backtester", return_value=mock_bt):
            async with TestClient(TestServer(make_app())) as client:
                body = await (await client.get("/backtest-run?data=test.csv")).json()
                for entry in body["equity_curve"]:
                    assert "bar" in entry
                    assert "equity" in entry

    async def test_backtest_trade_log_is_list(self, make_app):
        mock_bt = MagicMock()
        mock_bt.run.return_value = _mock_backtest_result()

        with patch("src.backtester.Backtester", return_value=mock_bt):
            async with TestClient(TestServer(make_app())) as client:
                body = await (await client.get("/backtest-run?data=test.csv")).json()
                assert isinstance(body["trade_log"], list)

    async def test_backtest_trade_log_entries_have_required_fields(self, make_app):
        mock_bt = MagicMock()
        mock_bt.run.return_value = _mock_backtest_result()
        required = {"symbol", "direction", "entry", "exit", "pnl", "bars_held"}

        with patch("src.backtester.Backtester", return_value=mock_bt):
            async with TestClient(TestServer(make_app())) as client:
                body = await (await client.get("/backtest-run?data=test.csv")).json()
                for trade in body["trade_log"]:
                    assert required.issubset(trade.keys())

    async def test_backtest_returns_500_on_backtester_exception(self, make_app):
        with patch("src.backtester.Backtester", side_effect=RuntimeError("CSV not found")):
            async with TestClient(TestServer(make_app())) as client:
                resp = await client.get("/backtest-run?data=missing.csv")
                assert resp.status == 500
                body = await resp.json()
                assert "error" in body
                assert "CSV not found" in body["error"]

    async def test_backtest_forwards_data_query_param_to_backtester_run(self, make_app):
        mock_bt = MagicMock()
        mock_bt.run.return_value = _mock_backtest_result()
        mock_bt_class = MagicMock(return_value=mock_bt)

        with patch("src.backtester.Backtester", mock_bt_class):
            async with TestClient(TestServer(make_app())) as client:
                await client.get("/backtest-run?data=custom/path/data.csv")

        mock_bt.run.assert_called_once_with("custom/path/data.csv")

    async def test_backtest_uses_default_data_path_when_param_omitted(self, make_app):
        mock_bt = MagicMock()
        mock_bt.run.return_value = _mock_backtest_result()
        mock_bt_class = MagicMock(return_value=mock_bt)

        with patch("src.backtester.Backtester", mock_bt_class):
            async with TestClient(TestServer(make_app())) as client:
                await client.get("/backtest-run")

        # Default path defined in server.py
        call_arg = mock_bt.run.call_args[0][0]
        assert call_arg == "sample_data/minute_sample.csv"
