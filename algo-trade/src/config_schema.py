# file: src/config_schema.py
"""
Typed configuration schema using Pydantic v2.

All config sections are validated at startup.  Invalid values raise
ValidationError with a clear message rather than silently using a wrong
default deep inside the application.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ScreenerConfig(BaseModel):
    top_n: int = Field(10, ge=1, le=100)
    poll_interval_seconds: int = Field(60, ge=5)
    provider: str = "yahoo"
    market_hours_only: bool = True


class OptionsFilterConfig(BaseModel):
    min_volume: int = Field(100, ge=0)
    min_open_interest: int = Field(500, ge=0)
    max_spread_pct: float = Field(0.10, gt=0, le=1.0)
    max_dte: int = Field(30, ge=1)
    min_dte: int = Field(1, ge=0)
    max_otm_pct: float = Field(0.15, gt=0, le=1.0)
    min_iv: float = Field(0.10, ge=0.0, le=1.0)
    max_iv: float = Field(0.80, gt=0.0, le=5.0)


class IndicatorsConfig(BaseModel):
    rsi_period: int = Field(14, ge=2)
    rsi_overbought: float = Field(70.0, gt=50, lt=100)
    rsi_oversold: float = Field(30.0, gt=0, lt=50)
    macd_fast: int = Field(12, ge=1)
    macd_slow: int = Field(26, ge=2)
    macd_signal: int = Field(9, ge=1)
    atr_period: int = Field(14, ge=1)
    lookback_bars: int = Field(50, ge=10)
    signal_cooldown_minutes: int = Field(30, ge=0)
    volume_confirm_mult: float = Field(1.2, ge=0.0)

    @field_validator("macd_slow")
    @classmethod
    def slow_gt_fast(cls, v: int, info) -> int:
        fast = info.data.get("macd_fast", 12)
        if v <= fast:
            raise ValueError(f"macd_slow ({v}) must be greater than macd_fast ({fast})")
        return v


class RiskConfig(BaseModel):
    max_position_pct: float = Field(0.05, gt=0, le=1.0)
    max_open_positions: int = Field(5, ge=1)
    pdt_equity_threshold: float = Field(25000.0, ge=0)
    stop_loss_atr_mult: float = Field(1.5, gt=0)
    take_profit_atr_mult: float = Field(3.0, gt=0)

    @field_validator("take_profit_atr_mult")
    @classmethod
    def tp_gt_sl(cls, v: float, info) -> float:
        sl = info.data.get("stop_loss_atr_mult", 1.5)
        if v <= sl:
            raise ValueError(
                f"take_profit_atr_mult ({v}) must exceed stop_loss_atr_mult ({sl})"
            )
        return v


class WebullConfig(BaseModel):
    device_id: str = ""
    access_token: str = ""
    refresh_token: str = ""
    trade_token: str = ""
    account_id: str = ""


class RobinhoodConfig(BaseModel):
    username: str = ""
    password: str = ""
    mfa_code: str = ""


class BrokerConfig(BaseModel):
    name: str = "mock"
    webull: WebullConfig = Field(default_factory=WebullConfig)
    robinhood: RobinhoodConfig = Field(default_factory=RobinhoodConfig)


class MarketDataConfig(BaseModel):
    fmp_api_key: str = ""
    base_url: str = "https://financialmodelingprep.com/api/v3"
    request_timeout: int = Field(10, ge=1)
    retry_max: int = Field(3, ge=1)
    retry_backoff: float = Field(2.0, gt=0)


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_format: bool = True
    log_file: Optional[str] = "logs/algo-trade.log"


class ApiServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(8181, ge=1, le=65535)


class DatabaseConfig(BaseModel):
    url: str = "sqlite:///data/algo_trade.db"


class PaperTradingConfig(BaseModel):
    initial_capital: float = Field(25000.0, gt=0)


class EmailNotifyConfig(BaseModel):
    enabled: bool = False
    provider: str = "smtp"
    api_key: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = Field(587, ge=1, le=65535)
    username: str = ""
    password: str = ""
    recipient: str = ""


class WebhookNotifyConfig(BaseModel):
    enabled: bool = False
    url: str = ""


class NotificationsConfig(BaseModel):
    email: EmailNotifyConfig = Field(default_factory=EmailNotifyConfig)
    webhook: WebhookNotifyConfig = Field(default_factory=WebhookNotifyConfig)


class TradingHoursConfig(BaseModel):
    start: str = "09:45"
    end: str = "15:30"

    @field_validator("start", "end", mode="before")
    @classmethod
    def validate_time_format(cls, v):
        import re
        # PyYAML may parse unquoted HH:MM as sexagesimal integer (e.g. 09:45 → 585)
        if isinstance(v, (int, float)):
            total_min = int(v)
            h, m = divmod(total_min, 60)
            return f"{h:02d}:{m:02d}"
        v = str(v)
        if not re.match(r"^\d{1,2}:\d{2}$", v):
            raise ValueError(f"Time must be in HH:MM format, got '{v}'")
        h, m = v.split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"Invalid time value: '{v}'")
        return f"{h:02d}:{m:02d}"

    @model_validator(mode="after")
    def start_before_end(self) -> "TradingHoursConfig":
        if self.start >= self.end:
            raise ValueError(
                f"trading_hours.start ({self.start}) must be before end ({self.end})"
            )
        return self


class ConfirmationConfig(BaseModel):
    wait_bars: int = Field(2, ge=1)
    expire_minutes: float = Field(10.0, gt=0)


class DailyLimitsConfig(BaseModel):
    profit_target: float = Field(500.0, gt=0)
    loss_limit: float = Field(200.0, gt=0)


class AppConfig(BaseModel):
    """Root configuration model — validates the entire config dict at startup."""

    mode: Literal["paper", "live", "manual"] = "paper"
    screener: ScreenerConfig = Field(default_factory=ScreenerConfig)
    options_filter: OptionsFilterConfig = Field(default_factory=OptionsFilterConfig)
    indicators: IndicatorsConfig = Field(default_factory=IndicatorsConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    api_server: ApiServerConfig = Field(default_factory=ApiServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    paper_trading: PaperTradingConfig = Field(default_factory=PaperTradingConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    trading_hours: TradingHoursConfig = Field(default_factory=TradingHoursConfig)
    confirmation: ConfirmationConfig = Field(default_factory=ConfirmationConfig)
    daily_limits: DailyLimitsConfig = Field(default_factory=DailyLimitsConfig)
