# file: src/config.py
"""
Centralised configuration loader.

Load order (later overrides earlier):
  1. Hard-coded defaults
  2. config.yaml  (or CONFIG_PATH env var)
  3. .env file    (or DOTENV_PATH env var)
  4. Environment variables (ALGO_<SECTION>_<KEY>)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_CONFIG: Optional[Dict[str, Any]] = None
_CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.yaml"))
_DOTENV_PATH = Path(os.getenv("DOTENV_PATH", ".env"))
_LOADED_PATH: Optional[Path] = None  # tracks the actual path used by load_config


def _load_dotenv(path: Path) -> None:
    """Parse a .env file and set values into os.environ (no overwrite)."""
    if not path.exists():
        return
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            # Strip inline comments (e.g. VALUE=foo  # comment → foo)
            # Only strip when the # is preceded by whitespace to avoid
            # breaking values that contain # (e.g. URLs with fragments).
            if not (val.startswith('"') or val.startswith("'")):
                hash_pos = val.find(" #")
                if hash_pos != -1:
                    val = val[:hash_pos]
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def deep_merge(base: Dict, override: Dict) -> Dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result

# Keep underscore alias for internal callers
_deep_merge = deep_merge


def _apply_env_overrides(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Apply ALGO_<SECTION>_<KEY> environment variable overrides."""
    prefix = "ALGO_"
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        parts = env_key[len(prefix):].lower().split("_", 1)
        if len(parts) == 2:
            section, key = parts
            if section in cfg and isinstance(cfg[section], dict):
                if env_val.lower() in ("true", "1", "yes"):
                    cfg[section][key] = True
                elif env_val.lower() in ("false", "0", "no"):
                    cfg[section][key] = False
                else:
                    try:
                        cfg[section][key] = int(env_val)
                    except ValueError:
                        try:
                            cfg[section][key] = float(env_val)
                        except ValueError:
                            cfg[section][key] = env_val
    return cfg


def load_config(path: Optional[Path] = None, dotenv: Optional[Path] = None) -> Dict[str, Any]:
    """Load configuration from YAML + .env + environment variables."""
    global _CONFIG, _LOADED_PATH

    # Load .env first so its values are available when building defaults.
    _load_dotenv(dotenv or _DOTENV_PATH)

    config_path = path or _CONFIG_PATH
    _LOADED_PATH = config_path  # remember for update_config writes
    if config_path.exists():
        with config_path.open() as fh:
            raw = yaml.safe_load(fh) or {}
    else:
        raw = {}

    defaults: Dict[str, Any] = {
        "mode": os.getenv("MODE", "paper"),
        "screener": {
            "top_n": 10,
            "poll_interval_seconds": 60,
            "provider": os.getenv("ALGO_SCREENER_PROVIDER", "yahoo"),
            "market_hours_only": True,
        },
        "options_filter": {
            "min_volume": 100,
            "min_open_interest": 500,
            "max_spread_pct": 0.10,
            "max_dte": 30,
            "min_dte": 1,
            "max_otm_pct": 0.15,
            "min_iv": 0.10,
            "max_iv": 0.80,
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
            "volume_confirm_mult": 1.2,
        },
        "risk": {
            "max_position_pct": 0.05,
            "max_open_positions": 5,
            "pdt_equity_threshold": 25000,
            "stop_loss_atr_mult": 1.5,
            "take_profit_atr_mult": 3.0,
        },
        "broker": {
            "name": os.getenv("BROKER", "mock"),
            "webull": {
                "device_id":     os.getenv("WEBULL_DEVICE_ID", ""),
                "access_token":  os.getenv("WEBULL_ACCESS_TOKEN", ""),
                "refresh_token": os.getenv("WEBULL_REFRESH_TOKEN", ""),
                "trade_token":   os.getenv("WEBULL_TRADE_TOKEN", ""),
                "account_id":    os.getenv("WEBULL_ACCOUNT_ID", ""),
            },
            "robinhood": {
                "username": os.getenv("ROBINHOOD_USERNAME", ""),
                "password": os.getenv("ROBINHOOD_PASSWORD", ""),
                "mfa_code": os.getenv("ROBINHOOD_MFA_CODE", ""),
            },
        },
        "market_data": {
            "fmp_api_key":    os.getenv("FMP_API_KEY", ""),
            "base_url":       "https://financialmodelingprep.com/api/v3",
            "request_timeout": 10,
            "retry_max":      3,
            "retry_backoff":  2.0,
        },
        "logging": {
            "level":       os.getenv("LOG_LEVEL", "INFO"),
            "json_format": True,
            "log_file":    os.getenv("LOG_FILE", "logs/algo-trade.log"),
        },
        "api_server": {
            "host": "0.0.0.0",
            "port": int(os.getenv("API_PORT", "8181")),
        },
        "database": {
            "url": os.getenv("DATABASE_URL", "sqlite:///data/algo_trade.db"),
        },
        "paper_trading": {
            "initial_capital": float(os.getenv("PAPER_CAPITAL", "1000")),
        },
        "notifications": {
            "email": {
                "enabled":   False,
                "provider":  os.getenv("NOTIFY_EMAIL_PROVIDER", "smtp"),  # smtp | brevo | sendgrid | resend
                "api_key":   os.getenv("NOTIFY_EMAIL_API_KEY", ""),
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "username":  os.getenv("NOTIFY_EMAIL_USER") or "",
                "password":  os.getenv("NOTIFY_EMAIL_PASS") or "",
                "recipient": os.getenv("NOTIFY_EMAIL_USER") or "",
            },
            "webhook": {
                "enabled": False,
                "url":     os.getenv("NOTIFY_WEBHOOK_URL", ""),
            },
        },
    }

    merged = deep_merge(defaults, raw)
    merged = _apply_env_overrides(merged)

    # Validate the merged config against the Pydantic schema.
    # Errors are logged as warnings so a partially-broken config still boots
    # rather than crashing — but the problem is surfaced immediately.
    try:
        from src.config_schema import AppConfig
        AppConfig.model_validate(merged)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Config validation warning — some values may be invalid: %s", exc
        )
        raise

    _CONFIG = merged
    return merged


def get_config() -> Dict[str, Any]:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    return _CONFIG


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *updates* into the live in-memory config and persist to config.yaml.

    Changes apply immediately without restart. Values set via environment
    variables take precedence on next full load_config() call.
    """
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = load_config()
    merged = deep_merge(_CONFIG, updates)
    # Update in-place so existing references (e.g. RiskManager._config) see the change.
    _CONFIG.clear()
    _CONFIG.update(merged)
    # Invalidate strategy params cache since config dict was mutated in place.
    try:
        from src.strategy_engine.strategies import clear_params_cache
        clear_params_cache()
    except ImportError:
        pass
    # Persist to disk so settings survive restarts
    write_path = _LOADED_PATH or _CONFIG_PATH
    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)
        with write_path.open("w") as fh:
            yaml.dump(_CONFIG, fh, default_flow_style=False, allow_unicode=True)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning("Failed to persist config to %s: %s", write_path, exc)
        raise
    return _CONFIG
