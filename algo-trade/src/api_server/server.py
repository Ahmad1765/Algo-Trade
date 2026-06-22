# file: src/api_server/server.py
"""
Read-only HTTP API server (aiohttp).

Endpoints:
    GET /health     — liveness + market-hours status
    GET /signals    — recent signal events
    GET /positions  — live open positions from persistence store
    GET /metrics    — counts and uptime
    GET /           — redirects to /dashboard/ (Next.js static export)

Every JSON endpoint is also served under an /api/* alias for the frontend
client; static export files are served from the web dir (see _web_dir()).
"""

from __future__ import annotations

import asyncio
import hmac
import html as _html
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from aiohttp import web

from src.config import get_config, update_config, deep_merge
from src.logger import get_logger
from src.market_hours import is_market_open, now_et
from src.api_server import auth as _auth


def _web_dir() -> Path:
    """Directory holding the exported Next.js site (read fresh for tests)."""
    return Path(os.getenv("WEB_DIR") or (Path(__file__).resolve().parents[2] / "web"))

log = get_logger(__name__)

_START_TIME = time.time()
_MAX_SIGNALS = 100

# C-1: optional API key protecting POST /config (set CONFIG_API_KEY env var to enable)
_CONFIG_API_KEY = os.getenv("CONFIG_API_KEY", "")
_MASK_SENTINEL = "********"
_CONTRACT_MULTIPLIER = 100  # standard options contract multiplier

# C-2: validation allowlists / ranges
_ENUM_FIELDS: Dict[str, set] = {
    "mode":                  {"paper", "manual", "automated"},
    "broker_name":           {"mock", "webull"},
    "screener_provider":     {"yahoo", "fmp", "mock"},
    "notify_email_provider": {"smtp", "brevo", "sendgrid", "resend"},
}
_POSITIVE_INT_FIELDS = {
    "screener_poll_interval_seconds",
    "screener_top_n",
    "risk_max_open_positions",
    "risk_pdt_equity_threshold",
    "notify_email_smtp_port",
}
_POSITIVE_FLOAT_FIELDS = {
    "risk_max_position_pct",
    "risk_stop_loss_atr_mult",
    "risk_take_profit_atr_mult",
    "cb_daily_profit_target_pct",
    "cb_daily_loss_limit_pct",
    "confirm_expire_minutes",
}
_POSITIVE_INT_FIELDS_EXTENDED = {
    "confirm_wait_bars",
}
# H-2: permitted webhook domains
_ALLOWED_WEBHOOK_HOSTS = {"discord.com", "discordapp.com", "hooks.slack.com"}


def create_app(
    risk_manager: Any,
    signal_store: List[Dict],
    position_store: Optional[Any] = None,
    market_adapter: Optional[Any] = None,
    action_store: Optional[List[Dict]] = None,
    broker_adapter: Optional[Any] = None,
    strategy_engine: Optional[Any] = None,
    sim_clock: Optional[Any] = None,
    ctx: Optional[Any] = None,
    session_manager: Optional[Any] = None,
) -> web.Application:
    from src.runtime.context import RuntimeContext
    if ctx is None:
        ctx = RuntimeContext(
            mode="live",
            risk_manager=risk_manager,
            position_store=position_store,
            market_adapter=market_adapter,
            strategy_engine=strategy_engine,
            broker_adapter=broker_adapter,
            sim_clock=sim_clock,
            signal_store=signal_store if signal_store is not None else [],
            action_store=action_store if action_store is not None else [],
        )

    def _market_open_now() -> bool:
        return ctx.sim_clock.is_open() if ctx.sim_clock is not None else is_market_open()

    def _market_time_str() -> str:
        src_dt = ctx.sim_clock.now() if ctx.sim_clock is not None else now_et()
        return src_dt.strftime("%Y-%m-%d %H:%M:%S ET")

    async def health(request: web.Request) -> web.Response:
        cfg = get_config()
        db_ok = ctx.position_store.check_connection() if ctx.position_store else False
        return web.json_response({
            "status": "ok",
            "uptime_s": round(time.time() - _START_TIME, 1),
            "market_open": _market_open_now(),
            "market_time_et": _market_time_str(),
            "mode": cfg.get("mode", "paper"),
            "broker": cfg.get("broker", {}).get("name", "mock"),
            "database_connected": db_ok,
        })

    async def get_signals(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(500, int(request.rel_url.query.get("limit", _MAX_SIGNALS))))
        except (TypeError, ValueError):
            limit = _MAX_SIGNALS
        return web.json_response(ctx.signal_store[-limit:])

    async def get_positions(request: web.Request) -> web.Response:
        positions = ctx.position_store.get_positions() if ctx.position_store else {}
        total_cost = 0.0
        enriched: Dict[str, Any] = {}
        for sym, pos in positions.items():
            entry = float(pos.get("entry_price", 0) or 0)
            qty   = int(pos.get("quantity", 0) or 0)
            cost_basis = round(entry * qty * _CONTRACT_MULTIPLIER, 2)
            total_cost += cost_basis
            enriched[sym] = {
                **pos,
                "cost_basis": cost_basis,
                "unrealized_pnl": None,
                "unrealized_pnl_pct": None,
            }
        return web.json_response({
            "open_positions": enriched,
            "count": len(enriched),
            "total_cost_basis": round(total_cost, 2),
        })

    async def get_metrics(request: web.Request) -> web.Response:
        open_count = ctx.position_store.open_count if ctx.position_store else 0
        return web.json_response({
            "uptime_s": round(time.time() - _START_TIME, 1),
            "signal_count": len(ctx.signal_store),
            "open_positions": open_count,
            "market_open": _market_open_now(),
        })

    async def get_history(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(500, int(request.rel_url.query.get("limit", 50))))
        except (TypeError, ValueError):
            limit = 50
        return web.json_response(list(reversed(ctx.action_store[-limit:])))

    async def get_status(request: web.Request) -> web.Response:
        from src.daily_circuit_breaker import DailyCircuitBreaker
        cfg        = get_config()
        open_count = ctx.position_store.open_count if ctx.position_store else 0
        db_ok      = ctx.position_store.check_connection() if ctx.position_store else False
        pnl        = ctx.position_store.get_pnl_summary() if ctx.position_store else {
            "total_pnl": 0.0, "trade_count": 0, "win_count": 0, "loss_count": 0,
            "win_rate": 0.0, "avg_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
        }
        paper_capital = float(cfg.get("paper_trading", {}).get("initial_capital", 25000.0))
        cb_status = DailyCircuitBreaker(cfg, ctx.position_store).status
        pending_count = ctx.strategy_engine.get_pending_count() if ctx.strategy_engine else 0
        return web.json_response({
            # system
            "uptime_s":          round(time.time() - _START_TIME, 1),
            "market_open":       _market_open_now(),
            "market_time_et":    _market_time_str(),
            "mode":              cfg.get("mode", "paper"),
            "broker":            cfg.get("broker", {}).get("name", "mock"),
            "database_connected": db_ok,
            # live counts
            "open_positions":    open_count,
            "signal_count":      len(ctx.signal_store),
            "action_count":      len(ctx.action_store),
            "pending_signals":   pending_count,
            # paper trading capital
            "paper_capital":     paper_capital,
            # p&l
            **pnl,
            # circuit breaker
            "circuit_breaker":   cb_status,
            # recent activity (newest first)
            "recent_actions":    list(reversed(ctx.action_store[-30:])),
        })

    async def sse_stream(request: web.Request) -> web.StreamResponse:
        """Server-Sent Events — pushes full dashboard state every 5 seconds."""
        import json as _json
        from src.daily_circuit_breaker import DailyCircuitBreaker
        resp = web.StreamResponse(headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        })
        await resp.prepare(request)
        try:
            while True:
                cfg        = get_config()
                open_count = ctx.position_store.open_count if ctx.position_store else 0
                db_ok      = ctx.position_store.check_connection() if ctx.position_store else False
                positions  = ctx.position_store.get_positions() if ctx.position_store else {}
                sigs       = ctx.signal_store[-20:] if ctx.signal_store else []
                acts       = list(reversed(ctx.action_store[-30:])) if ctx.action_store else []
                pnl        = ctx.position_store.get_pnl_summary() if ctx.position_store else {
                    "total_pnl": 0.0, "trade_count": 0, "win_count": 0,
                    "loss_count": 0, "win_rate": 0.0, "avg_pnl": 0.0,
                    "best_trade": 0.0, "worst_trade": 0.0,
                }
                daily_pnl  = ctx.position_store.get_daily_pnl() if ctx.position_store else 0.0
                paper_capital = float(cfg.get("paper_trading", {}).get("initial_capital", 25000.0))
                cb = DailyCircuitBreaker(cfg, ctx.position_store).status
                pending_count = ctx.strategy_engine.get_pending_count() if ctx.strategy_engine else 0

                payload = _json.dumps({
                    "market_open":    _market_open_now(),
                    "market_time":    _market_time_str(),
                    "mode":           cfg.get("mode", "paper"),
                    "open_positions": open_count,
                    "signal_count":   len(ctx.signal_store),
                    "pending_signals": pending_count,
                    "db_ok":          db_ok,
                    "uptime_s":       round(time.time() - _START_TIME),
                    "signals":        sigs,
                    "positions":      positions,
                    "activity":       acts,
                    # P&L
                    "paper_capital":  paper_capital,
                    "total_pnl":      pnl.get("total_pnl", 0.0),
                    "daily_pnl":      round(daily_pnl, 2),
                    "trade_count":    pnl.get("trade_count", 0),
                    "win_count":      pnl.get("win_count", 0),
                    "loss_count":     pnl.get("loss_count", 0),
                    "win_rate":       pnl.get("win_rate", 0.0),
                    "avg_pnl":        pnl.get("avg_pnl", 0.0),
                    "best_trade":     pnl.get("best_trade", 0.0),
                    "worst_trade":    pnl.get("worst_trade", 0.0),
                    "circuit_breaker": cb,
                })
                await resp.write(f"data: {payload}\n\n".encode())
                await asyncio.sleep(5)
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        return resp


    def _mask(value: str) -> str:
        """Return a masked version of a secret string."""
        return ("*" * 8) if value else ""

    async def get_config_endpoint(request: web.Request) -> web.Response:
        # Merge DB overrides (Railway-safe) on top of base config
        base = get_config()
        db_overrides = ctx.position_store.get_config_overrides() if ctx.position_store else {}
        cfg = deep_merge(base, db_overrides) if db_overrides is not None else base
        broker = cfg.get("broker", {})
        wb = broker.get("webull", {})
        screener = cfg.get("screener", {})
        risk = cfg.get("risk", {})
        market_data = cfg.get("market_data", {})
        notif = cfg.get("notifications", {})
        email = notif.get("email", {})
        webhook = notif.get("webhook", {})
        cb = cfg.get("circuit_breaker", {})
        confirm = cfg.get("confirmation", {})
        trading_hours = cfg.get("trading_hours", {})
        return web.json_response({
            "mode": cfg.get("mode", "paper"),
            "broker_name": broker.get("name", "mock"),
            "screener_provider": screener.get("provider", "yahoo"),
            "screener_poll_interval_seconds": screener.get("poll_interval_seconds", 60),
            "screener_top_n": screener.get("top_n", 10),
            "screener_market_hours_only": screener.get("market_hours_only", True),
            "fmp_api_key_set": bool(market_data.get("fmp_api_key", "")),
            "risk_max_position_pct": risk.get("max_position_pct", 0.05),
            "risk_max_open_positions": risk.get("max_open_positions", 5),
            "risk_pdt_equity_threshold": risk.get("pdt_equity_threshold", 25000),
            "risk_stop_loss_atr_mult": risk.get("stop_loss_atr_mult", 1.5),
            "risk_take_profit_atr_mult": risk.get("take_profit_atr_mult", 3.0),
            "notify_email_enabled": email.get("enabled", False),
            "notify_email_provider": email.get("provider", "smtp"),
            "notify_email_api_key_set": bool(os.getenv("NOTIFY_EMAIL_API_KEY") or email.get("api_key", "")),
            "notify_email_smtp_host": email.get("smtp_host", "smtp.gmail.com"),
            "notify_email_smtp_port": int(email.get("smtp_port", 587)),
            "notify_email_username": email.get("username", ""),
            "notify_email_password_set": bool(email.get("password", "")),
            "notify_email_recipient": email.get("recipient", ""),
            "notify_webhook_enabled": webhook.get("enabled", False),
            "notify_webhook_url": webhook.get("url", ""),
            "webull_device_id":     _mask(wb.get("device_id", "")),
            "webull_access_token":  _mask(wb.get("access_token", "")),
            "webull_refresh_token": _mask(wb.get("refresh_token", "")),
            "webull_trade_token":   _mask(wb.get("trade_token", "")),
            "webull_account_id_set": bool(wb.get("account_id", "")),
            # circuit breaker
            "cb_daily_profit_target_pct": float(cb.get("daily_profit_target_pct", 0.30)),
            "cb_daily_loss_limit_pct":    float(cb.get("daily_loss_limit_pct", 0.20)),
            # signal confirmation
            "confirm_wait_bars":      int(confirm.get("wait_bars", 2)),
            "confirm_expire_minutes": float(confirm.get("expire_minutes", 10)),
            # trading hours
            "trading_hours_start": trading_hours.get("start", "09:45"),
            "trading_hours_end":   trading_hours.get("end", "15:30"),
        })

    async def post_config_endpoint(request: web.Request) -> web.Response:
        # C-1: API key auth (enforced only when CONFIG_API_KEY env var is set)
        if _CONFIG_API_KEY:
            if not hmac.compare_digest(request.headers.get("X-Api-Key", ""), _CONFIG_API_KEY):
                return web.json_response({"error": "unauthorized"}, status=401)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        # C-2: enum allowlist validation
        for field, allowed in _ENUM_FIELDS.items():
            if field in body and body[field] not in allowed:
                return web.json_response(
                    {"error": f"{field} must be one of {sorted(allowed)}"}, status=422
                )

        # C-2: positive integer validation (extended fields)
        for field in _POSITIVE_INT_FIELDS_EXTENDED:
            if field in body:
                try:
                    v = int(body[field])
                except (TypeError, ValueError):
                    return web.json_response({"error": f"{field} must be a positive integer"}, status=422)
                if v < 1:
                    return web.json_response({"error": f"{field} must be >= 1"}, status=422)

        # C-2: positive integer validation
        for field in _POSITIVE_INT_FIELDS:
            if field in body:
                try:
                    v = int(body[field])
                except (TypeError, ValueError):
                    return web.json_response({"error": f"{field} must be a positive integer"}, status=422)
                if v < 1:
                    return web.json_response({"error": f"{field} must be >= 1"}, status=422)

        # C-2: positive float validation
        for field in _POSITIVE_FLOAT_FIELDS:
            if field in body:
                try:
                    v = float(body[field])
                except (TypeError, ValueError):
                    return web.json_response({"error": f"{field} must be a positive number"}, status=422)
                if v <= 0:
                    return web.json_response({"error": f"{field} must be > 0"}, status=422)

        # H-2: webhook URL must be https:// from an allowed domain
        if "notify_webhook_url" in body:
            url = body["notify_webhook_url"]
            if url and url != _MASK_SENTINEL:
                try:
                    parsed = urlparse(url)
                    host = parsed.netloc.lower().split(":")[0]
                    if parsed.scheme != "https" or not any(
                        host == d or host.endswith("." + d) for d in _ALLOWED_WEBHOOK_HOSTS
                    ):
                        return web.json_response(
                            {"error": "notify_webhook_url must be https:// from discord.com, discordapp.com, or hooks.slack.com"},
                            status=422,
                        )
                except Exception:
                    return web.json_response({"error": "notify_webhook_url is invalid"}, status=422)

        # Build a nested updates dict from the flat payload
        updates: Dict[str, Any] = {}

        def _set(keys: list, val: Any) -> None:
            d = updates
            for k in keys[:-1]:
                d = d.setdefault(k, {})
            d[keys[-1]] = val

        mapping = {
            "mode":                         ["mode"],
            "broker_name":                  ["broker", "name"],
            "screener_provider":            ["screener", "provider"],
            "screener_poll_interval_seconds": ["screener", "poll_interval_seconds"],
            "screener_top_n":               ["screener", "top_n"],
            "screener_market_hours_only":   ["screener", "market_hours_only"],
            "fmp_api_key":                  ["market_data", "fmp_api_key"],
            "risk_max_position_pct":        ["risk", "max_position_pct"],
            "risk_max_open_positions":      ["risk", "max_open_positions"],
            "risk_pdt_equity_threshold":    ["risk", "pdt_equity_threshold"],
            "risk_stop_loss_atr_mult":      ["risk", "stop_loss_atr_mult"],
            "risk_take_profit_atr_mult":    ["risk", "take_profit_atr_mult"],
            "notify_email_enabled":         ["notifications", "email", "enabled"],
            "notify_email_provider":        ["notifications", "email", "provider"],
            "notify_email_api_key":         ["notifications", "email", "api_key"],
            "notify_email_smtp_host":       ["notifications", "email", "smtp_host"],
            "notify_email_smtp_port":       ["notifications", "email", "smtp_port"],
            "notify_email_username":        ["notifications", "email", "username"],
            "notify_email_password":        ["notifications", "email", "password"],
            "notify_email_recipient":       ["notifications", "email", "recipient"],
            "notify_webhook_enabled":       ["notifications", "webhook", "enabled"],
            "notify_webhook_url":           ["notifications", "webhook", "url"],
            "webull_device_id":             ["broker", "webull", "device_id"],
            "webull_access_token":          ["broker", "webull", "access_token"],
            "webull_refresh_token":         ["broker", "webull", "refresh_token"],
            "webull_trade_token":           ["broker", "webull", "trade_token"],
            "webull_account_id":            ["broker", "webull", "account_id"],
            # circuit breaker
            "cb_daily_profit_target_pct":   ["circuit_breaker", "daily_profit_target_pct"],
            "cb_daily_loss_limit_pct":      ["circuit_breaker", "daily_loss_limit_pct"],
            # signal confirmation
            "confirm_wait_bars":            ["confirmation", "wait_bars"],
            "confirm_expire_minutes":       ["confirmation", "expire_minutes"],
            # trading hours
            "trading_hours_start":          ["trading_hours", "start"],
            "trading_hours_end":            ["trading_hours", "end"],
        }

        for flat_key, path in mapping.items():
            if flat_key in body:
                val = body[flat_key]
                # H-1: skip empty strings and mask sentinels — never overwrite with blank/masked
                if isinstance(val, str) and (val == "" or val == _MASK_SENTINEL):
                    continue
                _set(path, val)

        if not updates:
            known_keys = (
                set(mapping)
                | set(_ENUM_FIELDS)
                | _POSITIVE_INT_FIELDS
                | _POSITIVE_INT_FIELDS_EXTENDED
                | _POSITIVE_FLOAT_FIELDS
            )
            if not any(k in known_keys for k in body):
                return web.json_response({"error": "no recognized configuration fields"}, status=400)
            return web.json_response({"ok": True, "changed": False})

        # 1. Persist to DB (survives Railway redeployments)
        if ctx.position_store:
            ctx.position_store.merge_config_overrides(updates)

        # 2. Apply to in-memory config immediately (no restart needed)
        update_config(updates)
        return web.json_response({"ok": True})

    # ── Test email endpoint ────────────────────────────────────────────────

    async def test_email_endpoint(request: web.Request) -> web.Response:
        """Send a test email using the current email configuration."""
        import smtplib
        import ssl as _ssl
        import json as _json
        import urllib.request as _urlreq
        from email.mime.text import MIMEText as _MIMEText

        base = get_config()
        db_overrides = ctx.position_store.get_config_overrides() if ctx.position_store else {}
        cfg   = deep_merge(base, db_overrides) if db_overrides is not None else base
        email = cfg.get("notifications", {}).get("email", {})

        if not email.get("enabled", False):
            return web.json_response({"error": "Email alerts are disabled — enable them first."}, status=400)

        provider  = os.getenv("NOTIFY_EMAIL_PROVIDER") or email.get("provider", "smtp")
        api_key   = os.getenv("NOTIFY_EMAIL_API_KEY") or email.get("api_key", "")
        user      = os.getenv("NOTIFY_EMAIL_USER") or email.get("username", "")
        password  = os.getenv("NOTIFY_EMAIL_PASS") or email.get("password", "")
        recipient = email.get("recipient", "") or user
        smtp_host = email.get("smtp_host", "smtp.gmail.com")
        smtp_port = int(email.get("smtp_port", 587))

        if not user:
            return web.json_response({"error": "Sender email is not configured."}, status=400)
        if not recipient:
            return web.json_response({"error": "Recipient email is not configured."}, status=400)

        body_text = (
            "This is a test message from AlgoTrade.\n\n"
            f"Provider  : {provider}\n"
            f"Sender    : {user}\n"
            f"Recipient : {recipient}\n\n"
            "Your email notification configuration is working correctly."
        )

        # ── HTTP API providers (not blocked by Railway) ────────────────────
        if provider in ("brevo", "sendgrid", "resend"):
            if not api_key:
                return web.json_response(
                    {"error": f"API key not set. Add NOTIFY_EMAIL_API_KEY env var or set it in Settings."},
                    status=400,
                )

            if provider == "brevo":
                url     = "https://api.brevo.com/v3/smtp/email"
                headers = {"api-key": api_key, "Content-Type": "application/json"}
                payload = {
                    "sender":      {"email": user},
                    "to":          [{"email": recipient}],
                    "subject":     "[AlgoTrade] Test Email",
                    "textContent": body_text,
                }
            elif provider == "sendgrid":
                url     = "https://api.sendgrid.com/v3/mail/send"
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {
                    "personalizations": [{"to": [{"email": recipient}]}],
                    "from":    {"email": user},
                    "subject": "[AlgoTrade] Test Email",
                    "content": [{"type": "text/plain", "value": body_text}],
                }
            else:  # resend
                url     = "https://api.resend.com/emails"
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {"from": user, "to": [recipient], "subject": "[AlgoTrade] Test Email", "text": body_text}

            try:
                loop = asyncio.get_running_loop()

                def _http_send():
                    data = _json.dumps(payload).encode()
                    req  = _urlreq.Request(url, data=data, headers=headers, method="POST")
                    with _urlreq.urlopen(req, timeout=15) as resp:
                        return resp.getcode(), resp.read().decode(errors="replace")

                status_code, resp_body = await loop.run_in_executor(None, _http_send)
                if status_code not in (200, 201, 202):
                    return web.json_response(
                        {"error": f"{provider} API returned {status_code}: {resp_body}"},
                        status=500,
                    )
                log.info("test email sent via api", provider=provider, recipient=recipient, api_response=resp_body)
                return web.json_response({"ok": True, "recipient": recipient, "provider": provider, "api_response": resp_body})
            except Exception as exc:
                log.error("test email api failed", provider=provider, error=str(exc))
                return web.json_response({"error": str(exc)}, status=500)

        # ── SMTP path ──────────────────────────────────────────────────────
        if not password:
            return web.json_response({"error": "App password is not configured."}, status=400)

        try:
            msg = _MIMEText(body_text)
            msg["Subject"] = "[AlgoTrade] Test Email"
            msg["From"]    = user
            msg["To"]      = recipient

            context = _ssl.create_default_context()
            loop    = asyncio.get_running_loop()

            def _send():
                if smtp_port == 465:
                    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=15) as srv:
                        srv.login(user, password)
                        srv.sendmail(user, recipient, msg.as_string())
                else:
                    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as srv:
                        srv.starttls(context=context)
                        srv.login(user, password)
                        srv.sendmail(user, recipient, msg.as_string())

            await loop.run_in_executor(None, _send)
            log.info("test email sent", recipient=recipient)
            return web.json_response({"ok": True, "recipient": recipient, "provider": "smtp"})
        except smtplib.SMTPAuthenticationError:
            return web.json_response(
                {"error": "Authentication failed. Check your app password (Gmail requires a dedicated App Password, not your account password)."},
                status=500,
            )
        except smtplib.SMTPConnectError:
            return web.json_response(
                {"error": f"Cannot connect to {smtp_host}:{smtp_port}. Check host/port settings."},
                status=500,
            )
        except OSError as exc:
            if getattr(exc, "errno", None) in (101, 111, 110):
                log.error("test email failed — SMTP blocked by platform", error=str(exc))
                return web.json_response(
                    {"error": (
                        "Railway blocks outbound SMTP. "
                        "Switch Email Provider to 'brevo' in Settings and add your Brevo API key."
                    )},
                    status=500,
                )
            log.error("test email failed", error=str(exc))
            return web.json_response({"error": str(exc)}, status=500)
        except Exception as exc:
            log.error("test email failed", error=str(exc))
            return web.json_response({"error": str(exc)}, status=500)

    # ── Market data endpoints ──────────────────────────────────────────────

    async def get_overview(request: web.Request) -> web.Response:
        """Top gainers/losers from live market data."""
        if not ctx.market_adapter:
            return web.json_response({"error": "market adapter unavailable"}, status=503)
        try:
            gainers = await ctx.market_adapter.get_top_gainers(limit=5)
            losers  = await ctx.market_adapter.get_top_losers(limit=5)
            return web.json_response({
                "gainers": [
                    {"symbol": q.symbol, "price": q.price,
                     "change_pct": round(q.change_pct, 2), "volume": q.volume}
                    for q in gainers
                ],
                "losers": [
                    {"symbol": q.symbol, "price": q.price,
                     "change_pct": round(q.change_pct, 2), "volume": q.volume}
                    for q in losers
                ],
                "refreshed_at": now_et().isoformat(),
            })
        except Exception as exc:
            log.error("overview endpoint failed", error=str(exc))
            return web.json_response({"error": "failed to fetch market data"}, status=503)

    async def get_quote(request: web.Request) -> web.Response:
        """Price bars for a symbol. Query params: range (default 1d), interval (default 1m)."""
        symbol    = request.match_info.get("symbol", "").upper()
        if not symbol:
            return web.json_response({"error": "symbol required"}, status=400)
        range_str = request.rel_url.query.get("range", "1d")
        interval  = request.rel_url.query.get("interval", "1m")
        if not ctx.market_adapter:
            return web.json_response({"error": "market adapter unavailable"}, status=503)
        try:
            bars  = await ctx.market_adapter.get_historical_bars(symbol, range_str, interval)
            quote = await ctx.market_adapter.get_quote(symbol)
            return web.json_response({
                "symbol":        symbol,
                "current_price": quote.price,
                "change_pct":    round(quote.change_pct, 2),
                "bars": bars,
            })
        except Exception as exc:
            log.error("quote endpoint failed", symbol=symbol, error=str(exc))
            return web.json_response({"error": "failed to fetch quote"}, status=503)

    _STRATEGY_META = [
        ("RSIMACD",            "RSI overbought/oversold + MACD histogram direction"),
        ("EMACross",           "EMA 9 crosses above/below EMA 21"),
        ("BollingerBreakout",  "Price breaks outside Bollinger Bands (20-period, 2σ)"),
        ("Momentum",           "5-bar price change exceeds 0.5% threshold"),
        ("MeanReversion",      "Price > 2σ from SMA20 — fade the extreme"),
        ("VWAP",               "Price deviates > 0.3% from VWAP intraday"),
        ("RSIAggressive",      "Pure RSI with aggressive thresholds (80 / 20)"),
        ("TrendFollowing",     "SMA20 > SMA50 uptrend + RSI > 50 confirmation"),
        ("VolatilityBreakout", "Last candle range > 2× ATR — follow direction"),
        ("MACDCross",          "MACD line crosses signal line (crossover event)"),
    ]

    def _extract_strategy_name(sig: dict) -> str:
        """Return strategy name from signal dict (new field, or parsed from rationale)."""
        name = sig.get("strategy", "")
        if not name:
            rationale = sig.get("rationale", "")
            if rationale.startswith("[") and "]" in rationale:
                name = rationale[1:rationale.index("]")]
        return name

    async def get_strategies(request: web.Request) -> web.Response:
        """All 10 strategies with live signal counts and DB performance stats."""
        total = len(ctx.signal_store)
        calls = sum(1 for s in ctx.signal_store if s.get("direction") == "CALL")
        puts  = total - calls
        seen: dict = {}
        sig_counts: dict = {}
        for s in ctx.signal_store:
            sym = s.get("symbol")
            if sym:
                seen[sym] = True
            sname = _extract_strategy_name(s)
            if sname:
                sig_counts[sname] = sig_counts.get(sname, 0) + 1

        perf = ctx.position_store.get_strategy_scores() if ctx.position_store else {}

        strategies = []
        for name, description in _STRATEGY_META:
            row = perf.get(name, {})
            strategies.append({
                "name":        name,
                "description": description,
                "signals":     sig_counts.get(name, 0),
                "trades":      row.get("trades", 0),
                "wins":        row.get("wins", 0),
                "losses":      row.get("losses", 0),
                "win_rate":    row.get("win_rate", 0.0),
                "total_pnl":   row.get("total_pnl", 0.0),
            })

        return web.json_response({
            "is_active":      True,
            "total_signals":  total,
            "call_signals":   calls,
            "put_signals":    puts,
            "symbols_traded": list(seen.keys())[-20:],
            "strategies":     strategies,
        })

    async def post_reset(request: web.Request) -> web.Response:
        """Reset all paper trading data (positions, signals, cooldowns, strategy stats, actions)."""
        if _CONFIG_API_KEY:
            if not hmac.compare_digest(request.headers.get("X-Api-Key", ""), _CONFIG_API_KEY):
                return web.json_response({"error": "unauthorized"}, status=401)
        if not ctx.position_store:
            return web.json_response({"error": "no persistence store"}, status=503)
        try:
            from src.persistence import (
                PositionRecord, CooldownRecord, SignalRecord,
                ActionRecord, StrategyPerformanceRecord,
            )
            from datetime import datetime, timezone as _tz
            with ctx.position_store.SessionLocal() as session:
                session.query(PositionRecord).delete()
                session.query(CooldownRecord).delete()
                session.query(SignalRecord).delete()
                session.query(ActionRecord).delete()
                session.query(StrategyPerformanceRecord).delete()
                session.commit()

            ctx.signal_store.clear()
            ctx.action_store.clear()

            if ctx.broker_adapter is not None and hasattr(ctx.broker_adapter, "reset"):
                ctx.broker_adapter.reset()

            reset_entry = {
                "event": "SYSTEM_RESET", "symbol": None,
                "detail": "Paper trading data reset by user",
                "data": {}, "ts": datetime.now(_tz.utc).isoformat(),
            }
            ctx.action_store.append(reset_entry)
            log.info("paper trading data reset by user")
            return web.json_response({"ok": True})
        except Exception as exc:
            log.error("reset failed", error=str(exc))
            return web.json_response({"error": str(exc)}, status=500)

    async def run_backtest_endpoint(request: web.Request) -> web.Response:
        """Run a real backtest using Yahoo Finance historical data."""
        if not ctx.market_adapter:
            return web.json_response({"error": "market adapter unavailable"}, status=503)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        symbol = str(body.get("symbol", "SPY")).upper()
        period = str(body.get("period", "1 Year"))

        _range_map = {
            "3 Months": ("3mo", "1d"),
            "6 Months": ("6mo", "1d"),
            "1 Year":   ("1y",  "1d"),
            "2 Years":  ("2y",  "1wk"),
            "5 Years":  ("5y",  "1wk"),
        }
        range_str, interval = _range_map.get(period, ("1y", "1d"))

        try:
            import asyncio as _aio
            bars = await ctx.market_adapter.get_historical_bars(symbol, range_str, interval)
            if not bars or len(bars) < 30:
                return web.json_response(
                    {"error": f"Insufficient historical data for {symbol} ({len(bars)} bars)"},
                    status=422,
                )

            from src.backtester import Backtester
            from src.config import get_config
            cfg = get_config()
            bt  = Backtester(cfg)
            result = await _aio.get_running_loop().run_in_executor(None, bt.run_from_bars, bars)
            summary = result.summary()

            # Build equity curve from trade sequence
            equity = 10_000.0
            equity_curve = [{"date": bars[0]["datetime"][:10], "equity": round(equity)}]
            for trade in result.trades:
                if trade.pnl_pct is not None:
                    equity *= (1 + trade.pnl_pct / 100)
                    idx = min(trade.exit_bar or 0, len(bars) - 1)
                    equity_curve.append({
                        "date":   bars[idx]["datetime"][:10],
                        "equity": round(equity),
                    })

            return web.json_response({
                **summary,
                "equity_curve": equity_curve,
                "symbol": symbol,
                "period": period,
            })
        except Exception as exc:
            log.error("backtest endpoint failed", symbol=symbol, error=str(exc))
            return web.json_response({"error": f"Backtest failed: {exc}"}, status=500)

    async def post_order(request: web.Request) -> web.Response:
        """Place a manual paper trading order and record it to the activity log."""
        if _CONFIG_API_KEY:
            if not hmac.compare_digest(request.headers.get("X-Api-Key", ""), _CONFIG_API_KEY):
                return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        from datetime import datetime, timezone as _tz

        symbol     = str(body.get("symbol", "")).strip().upper()
        side       = str(body.get("side", "")).strip().lower()
        order_type = str(body.get("orderType", "market")).strip().lower()
        raw_qty    = body.get("qty", 0)
        raw_price  = body.get("price", 0)

        if not symbol:
            return web.json_response({"error": "symbol is required"}, status=400)
        if side not in ("buy", "sell"):
            return web.json_response({"error": "side must be 'buy' or 'sell'"}, status=400)
        try:
            qty = int(raw_qty)
            if qty <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return web.json_response({"error": "qty must be a positive integer"}, status=400)

        fill_price = 0.0
        if order_type == "limit":
            try:
                fill_price = float(raw_price)
                if fill_price <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return web.json_response({"error": "price must be a positive number for limit orders"}, status=400)

        detail = f"Manual {side.upper()} {qty} {symbol}"
        if fill_price:
            detail += f" @ ${fill_price:.2f}"

        entry = {
            "event":  "ORDER_FILLED",
            "symbol": symbol,
            "detail": detail,
            "data":   {"side": side, "qty": qty, "price": fill_price, "orderType": order_type},
            "ts":     datetime.now(_tz.utc).isoformat(),
        }
        ctx.action_store.append(entry)
        if ctx.position_store:
            ctx.position_store.add_action(
                event="ORDER_FILLED",
                symbol=symbol,
                detail=detail,
                data={"side": side, "qty": qty, "price": fill_price, "orderType": order_type},
            )
        log.info("manual order placed", symbol=symbol, side=side, qty=qty, price=fill_price)
        return web.json_response({"ok": True, "detail": detail})

    # ── Circuit breaker status ──────────────────────────────────────────────

    async def get_circuit_breaker(request: web.Request) -> web.Response:
        from src.daily_circuit_breaker import DailyCircuitBreaker
        cfg = get_config()
        cb  = DailyCircuitBreaker(cfg, ctx.position_store)
        return web.json_response(cb.status)

    # ── Pending signals (awaiting confirmation) ─────────────────────────────

    async def get_pending_signals(request: web.Request) -> web.Response:
        from datetime import timezone as _tz
        if ctx.strategy_engine is None:
            return web.json_response({"pending": []})
        pending = getattr(ctx.strategy_engine, "_pending", {})
        now = __import__("datetime").datetime.now(_tz.utc)
        expire_min = float(
            get_config().get("confirmation", {}).get("expire_minutes", 10)
        )
        result = []
        for symbol, entry in pending.items():
            first_seen = entry.get("first_seen_at")
            elapsed    = (now - first_seen).total_seconds() if first_seen else 0
            expires_in = max(0, expire_min * 60 - elapsed)
            plan       = entry.get("plan")
            result.append({
                "symbol":               symbol,
                "strategy":             entry.get("strategy_name", ""),
                "direction":            entry.get("direction").value if entry.get("direction") else "",
                "confirmations":        entry.get("confirmations", 0),
                "confirmations_needed": getattr(ctx.strategy_engine, "_confirm_wait_bars", 2),
                "strike":               plan.contract.strike if plan else None,
                "entry":                plan.entry_limit if plan else None,
                "first_seen_at":        first_seen.isoformat() if first_seen else None,
                "expires_in_s":         round(expires_in),
            })
        return web.json_response({"pending": result})

    # ── Sim clock endpoints ───────────────────────────────────────────────

    async def sim_status(request: web.Request) -> web.Response:
        if session_manager is not None:
            return web.json_response(session_manager.status())
        if ctx.sim_clock is None:
            return web.json_response({"active": False, "state": "idle"})
        return web.json_response(ctx.sim_clock.status())

    async def sim_start(request: web.Request) -> web.Response:
        if session_manager is None:
            return web.json_response({"error": "sim control unavailable"}, status=503)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        from src.sim.calendar import validate_sim_date
        try:
            d = validate_sim_date(str(body.get("date", "")))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=422)
        try:
            speed = float(body.get("speed", 60))
            if speed <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return web.json_response({"error": "speed must be a positive number"}, status=422)
        try:
            await session_manager.start_sim(d, speed)
        except RuntimeError as exc:
            return web.json_response({"error": str(exc)}, status=409)
        return web.json_response(session_manager.status(), status=202)

    async def sim_stop(request: web.Request) -> web.Response:
        if session_manager is None:
            return web.json_response({"error": "sim control unavailable"}, status=503)
        await session_manager.stop_sim()
        return web.json_response(session_manager.status())

    async def sim_control(request: web.Request) -> web.Response:
        running = (session_manager is not None and session_manager.state == "running") or \
                  (session_manager is None and ctx.sim_clock is not None)
        if not running:
            return web.json_response({"error": "no sim running"}, status=409)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        clock = session_manager.ctx.sim_clock if session_manager is not None else ctx.sim_clock
        action = body.get("action")
        if action == "pause":
            clock.pause()
        elif action == "resume":
            clock.resume()
        elif action == "set_speed":
            try:
                clock.set_speed(float(body.get("speed")))
            except (TypeError, ValueError):
                return web.json_response({"error": "speed must be a positive number"}, status=422)
        else:
            return web.json_response({"error": "action must be pause|resume|set_speed"}, status=422)
        status = session_manager.status() if session_manager is not None else clock.status()
        return web.json_response(status)

    # ── Middlewares ───────────────────────────────────────────────────────
    @web.middleware
    async def error_middleware(request: web.Request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — last-resort guard
            log.error(
                "unhandled_request_error",
                method=request.method,
                path=request.path,
                error=str(exc),
            )
            return web.json_response({"error": "internal server error"}, status=500)

    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        if (
            not _auth.auth_enabled()
            or request.path in _auth.EXEMPT_PATHS
            or _auth.is_public_asset(request.path)
        ):
            return await handler(request)
        subject = _auth.verify_session(request.cookies.get(_auth.COOKIE_NAME))
        if subject is not None:
            return await handler(request)
        if "text/html" in request.headers.get("Accept", ""):
            raise web.HTTPFound("/login")
        return web.json_response({"error": "unauthorized"}, status=401)

    def _login_html(error: str = "") -> str:
        msg = f'<p class="err">{_html.escape(error)}</p>' if error else ""
        return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · AlgoTrade</title>
<style>body{{font-family:system-ui,sans-serif;background:#06070A;color:#E7EAF3;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}}
form{{background:rgba(255,255,255,.04);padding:32px;border:1px solid rgba(255,255,255,.1);
border-radius:16px;width:300px}}h1{{font-size:18px;margin:0 0 4px}}
p.sub{{color:#8A90A6;font-size:13px;margin:0 0 20px}}input{{width:100%;padding:11px 13px;
border-radius:10px;border:1px solid rgba(255,255,255,.14);background:#0d0f15;color:#E7EAF3;
font-size:14px;box-sizing:border-box}}button{{width:100%;margin-top:14px;padding:11px;
border:0;border-radius:10px;background:#5BA8FF;color:#06070A;font-weight:700;cursor:pointer}}
p.err{{color:#FF5D73;font-size:13px;margin:12px 0 0}}</style></head>
<body><form method="post" action="/login"><h1>AlgoTrade</h1>
<p class="sub">📄 Paper-trading dashboard — sign in</p>
<input type="password" name="password" placeholder="Password" autofocus required>
<button type="submit">Sign in</button>{msg}</form></body></html>"""

    async def login_page(request: web.Request) -> web.Response:
        return web.Response(text=_login_html(), content_type="text/html")

    async def do_login(request: web.Request) -> web.Response:
        data = await request.post()
        subject = _auth.verify_credentials(None, str(data.get("password", "")))
        if subject is None:
            return web.Response(
                text=_login_html("Incorrect password."),
                content_type="text/html",
                status=401,
            )
        secure = not _auth._truthy(os.getenv("DEV_MODE"))
        resp = web.Response(status=302, headers={"Location": "/"})
        resp.set_cookie(
            _auth.COOKIE_NAME, _auth.sign_session(subject),
            httponly=True, secure=secure, samesite="Lax", max_age=7 * 24 * 3600,
        )
        return resp

    async def do_logout(request: web.Request) -> web.Response:
        resp = web.Response(status=302, headers={"Location": "/login"})
        resp.del_cookie(_auth.COOKIE_NAME)
        return resp

    async def root_redirect(request: web.Request) -> web.Response:
        raise web.HTTPFound("/dashboard/")

    async def spa_handler(request: web.Request) -> web.Response:
        web_root = _web_dir().resolve()
        rel = request.path.lstrip("/")
        target = (web_root / rel).resolve()
        # Block path traversal outside the web root.
        if web_root != target and web_root not in target.parents:
            raise web.HTTPNotFound()
        # 1) exact file (assets like icon.svg, *.js under /_next/)
        if target.is_file():
            return web.FileResponse(target)
        # 2) route directory -> its index.html  (trailingSlash export layout)
        index = target / "index.html"
        if index.is_file():
            return web.FileResponse(index)
        # 3) <route>.html  (defensive: non-trailing-slash exports)
        html_file = web_root / (rel.rstrip("/") + ".html")
        if html_file.is_file() and (web_root in html_file.resolve().parents):
            return web.FileResponse(html_file)
        # 4) fallback to the exported 404 page
        notfound = web_root / "404.html"
        if notfound.is_file():
            return web.FileResponse(notfound, status=404)
        raise web.HTTPNotFound()

    # ── Router ──────────────────────────────────────────────────────────────

    app = web.Application(middlewares=[error_middleware, auth_middleware])

    # Auth pages (root only — not under /api).
    app.router.add_get("/login", login_page)
    app.router.add_post("/login", do_login)
    app.router.add_post("/logout", do_logout)

    # JSON API — registered at the root path (Docker healthcheck, back-compat)
    # AND under /api/* (what the frontend client calls). One source of truth.
    api_routes = [
        ("GET",  "/health",            health),
        ("GET",  "/signals",           get_signals),
        ("GET",  "/positions",         get_positions),
        ("GET",  "/metrics",           get_metrics),
        ("GET",  "/history",           get_history),
        ("GET",  "/status",            get_status),
        ("GET",  "/overview",          get_overview),
        ("GET",  "/quote/{symbol}",    get_quote),
        ("GET",  "/strategies",        get_strategies),
        ("POST", "/reset",             post_reset),
        ("POST", "/order",             post_order),
        ("POST", "/backtest/run",      run_backtest_endpoint),
        ("GET",  "/config",            get_config_endpoint),
        ("POST", "/config",            post_config_endpoint),
        ("POST", "/config/test-email", test_email_endpoint),
        ("GET",  "/circuit-breaker",   get_circuit_breaker),
        ("GET",  "/pending-signals",   get_pending_signals),
        ("GET",  "/stream",            sse_stream),
        ("GET",  "/sim/status",        sim_status),
        ("POST", "/sim/start",         sim_start),
        ("POST", "/sim/stop",          sim_stop),
        ("POST", "/sim/control",       sim_control),
    ]
    for method, path, handler in api_routes:
        app.router.add_route(method, path, handler)
        app.router.add_route(method, "/api" + path, handler)

    # Static Next.js export.
    app.router.add_get("/", root_redirect)
    app.router.add_get("/{tail:.*}", spa_handler)
    return app


async def run_api_server(
    app: web.Application,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> None:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("api_server started", host=host, port=port)
