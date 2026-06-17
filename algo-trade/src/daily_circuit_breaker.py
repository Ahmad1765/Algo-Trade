# file: src/daily_circuit_breaker.py
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional, Tuple

from src.logger import get_logger

log = get_logger(__name__)


class DailyCircuitBreaker:
    """
    Halts all new signal generation for the rest of the trading day when either:
      - Daily P&L >= +daily_profit_target_pct × starting_equity  (lock in gains)
      - Daily P&L <= -daily_loss_limit_pct × starting_equity     (cut losses)

    Resets automatically at midnight (new calendar day).
    """

    def __init__(self, config: Dict[str, Any], position_store=None) -> None:
        cb = config.get("circuit_breaker", {})
        self._profit_target_pct = float(cb.get("daily_profit_target_pct", 0.30))
        self._loss_limit_pct    = float(cb.get("daily_loss_limit_pct", 0.20))
        self._starting_equity   = float(
            config.get("paper_trading", {}).get("initial_capital", 25000)
        )
        self._store         = position_store
        self._halted        = False
        self._halt_reason   = ""
        self._trading_date  = date.today()

    # ------------------------------------------------------------------ #

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != self._trading_date:
            self._halted       = False
            self._halt_reason  = ""
            self._trading_date = today
            log.info("circuit_breaker reset for new trading day", date=str(today))

    def _daily_pnl(self) -> float:
        if self._store is None:
            return 0.0
        try:
            return self._store.get_daily_pnl()
        except Exception:
            return 0.0

    # ------------------------------------------------------------------ #

    def check(self) -> Tuple[bool, str]:
        """
        Evaluate circuit-breaker conditions.
        Returns (is_halted, reason_string).
        Call this before generating any new signal.
        """
        self._reset_if_new_day()

        if self._halted:
            return True, self._halt_reason

        pnl      = self._daily_pnl()
        equity   = self._starting_equity
        pnl_pct  = pnl / equity if equity else 0.0

        profit_thresh = self._profit_target_pct * equity
        loss_thresh   = -self._loss_limit_pct * equity

        if pnl >= profit_thresh:
            reason = (
                f"daily profit target hit: +{pnl:.2f} "
                f"(+{pnl_pct*100:.1f}% >= +{self._profit_target_pct*100:.0f}%)"
            )
            self._halted      = True
            self._halt_reason = reason
            log.warning("CIRCUIT BREAKER TRIGGERED — profit target", reason=reason)
            return True, reason

        if pnl <= loss_thresh:
            reason = (
                f"daily loss limit hit: {pnl:.2f} "
                f"({pnl_pct*100:.1f}% <= -{self._loss_limit_pct*100:.0f}%)"
            )
            self._halted      = True
            self._halt_reason = reason
            log.warning("CIRCUIT BREAKER TRIGGERED — loss limit", reason=reason)
            return True, reason

        return False, ""

    @property
    def is_halted(self) -> bool:
        halted, _ = self.check()
        return halted

    @property
    def status(self) -> Dict[str, Any]:
        self._reset_if_new_day()
        pnl     = self._daily_pnl()
        equity  = self._starting_equity
        return {
            "halted":              self._halted,
            "halt_reason":         self._halt_reason,
            "daily_pnl":           pnl,
            "daily_pnl_pct":       round(pnl / equity * 100, 2) if equity else 0.0,
            "profit_target_pct":   self._profit_target_pct * 100,
            "loss_limit_pct":      self._loss_limit_pct * 100,
            "starting_equity":     equity,
            "trading_date":        str(self._trading_date),
        }
