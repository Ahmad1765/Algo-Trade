# file: src/risk_manager/manager.py
"""
Risk manager — enforces capital limits and position-sizing rules.

Rules enforced:
1. Maximum equity per trade:  position_size * entry_limit * 100
   <= max_position_pct * equity.
2. Maximum concurrent open positions: max_open_positions.
3. PDT (Pattern Day Trader) check: warns when equity < pdt_threshold
   and the account has < 4 day-trades remaining.
4. Sets TradePlan.position_size based on available equity.

Stop-loss and take-profit levels are verified to be consistent (not inverted).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.events import SignalDirection, TradePlan
from src.logger import get_logger

log = get_logger(__name__)


class RiskManager:
    """
    Validates and sizes trade plans.

    Parameters
    ----------
    config : application configuration dict.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config  # kept by reference so live updates are reflected
        risk = config.get("risk", {})
        self._pdt_threshold: float = float(risk.get("pdt_equity_threshold", 25_000))
        self._sl_mult: float = float(risk.get("stop_loss_atr_mult", 1.5))
        self._tp_mult: float = float(risk.get("take_profit_atr_mult", 3.0))
        self._open_positions: List[str] = []  # option symbols of open positions

    @property
    def _max_open(self) -> int:
        """Read live from config so runtime updates take effect immediately."""
        return int(self._config.get("risk", {}).get("max_open_positions", 5))

    @property
    def _max_pos_pct(self) -> float:
        """Read live from config so settings-page changes take effect immediately."""
        return float(self._config.get("risk", {}).get("max_position_pct", 0.25))

    @property
    def open_position_count(self) -> int:
        return len(self._open_positions)

    def register_open(self, option_symbol: str) -> None:
        """Record that a position has been opened."""
        self._open_positions.append(option_symbol)

    def register_close(self, option_symbol: str) -> None:
        """Remove a closed position from tracking."""
        if option_symbol in self._open_positions:
            self._open_positions.remove(option_symbol)

    def check_pdt(self, equity: float, day_trades_used: int = 0) -> bool:
        """
        Return True if it is safe to place a day trade.

        Logs a warning when approaching PDT limits.
        """
        if equity >= self._pdt_threshold:
            return True
        remaining = 3 - day_trades_used
        if remaining <= 0:
            log.warning(
                "PDT limit reached",
                equity=equity,
                pdt_threshold=self._pdt_threshold,
            )
            return False
        log.warning(
            "PDT warning: equity below threshold",
            equity=equity,
            pdt_threshold=self._pdt_threshold,
            day_trades_remaining=remaining,
        )
        return True

    def approve(self, plan: TradePlan, equity: float) -> Tuple[bool, str]:
        """
        Validate and size a TradePlan.

        Modifies plan.position_size in-place.

        Returns
        -------
        (bool, reason) — True + empty string if approved; False + reason string if rejected.
        """
        if self.open_position_count >= self._max_open:
            log.warning(
                "max open positions reached",
                limit=self._max_open,
                current=self.open_position_count,
            )
            return False, f"max open positions reached ({self.open_position_count}/{self._max_open})"

        # We are always LONG the option (buy call or buy put).
        # Option profit = price rises, loss = price drops — same for both directions.
        # Valid layout is always: stop_loss < entry_limit < take_profit.
        if not (plan.stop_loss < plan.entry_limit < plan.take_profit):
            log.warning(
                "invalid SL/TP",
                direction=plan.direction.value,
                sl=plan.stop_loss,
                entry=plan.entry_limit,
                tp=plan.take_profit,
            )
            return False, f"invalid SL/TP: stop={plan.stop_loss} entry={plan.entry_limit} tp={plan.take_profit}"

        # Position sizing: 1 contract = 100 shares.
        max_capital = equity * self._max_pos_pct
        contract_cost = plan.entry_limit * 100  # one contract
        if contract_cost <= 0:
            return False, "entry price is zero"

        size = int(max_capital // contract_cost)
        if size < 1:
            log.warning(
                "insufficient equity for minimum position",
                max_capital=max_capital,
                contract_cost=contract_cost,
            )
            return False, (
                f"per-trade budget too small: 1 contract costs ${contract_cost:.2f}, "
                f"but the max per trade is ${max_capital:.2f} "
                f"({self._max_pos_pct:.0%} of ${equity:,.0f} equity)"
            )

        plan.position_size = size
        log.info(
            "trade plan approved",
            symbol=plan.symbol,
            direction=plan.direction.value,
            size=size,
            entry=plan.entry_limit,
            stop=plan.stop_loss,
            target=plan.take_profit,
        )
        return True, ""
