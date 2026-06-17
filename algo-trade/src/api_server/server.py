# file: src/api_server/server.py
"""
Read-only HTTP API server (aiohttp).

Endpoints:
    GET /health     — liveness + market-hours status
    GET /signals    — recent signal events
    GET /positions  — live open positions from persistence store
    GET /metrics    — counts and uptime
    GET /           — HTML dashboard
"""

from __future__ import annotations

import asyncio
import hmac
import html as _html
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from aiohttp import web

from src.config import get_config, update_config, deep_merge
from src.logger import get_logger
from src.market_hours import is_market_open, now_et

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
) -> web.Application:
    _action_store: List[Dict] = action_store if action_store is not None else []

    async def health(request: web.Request) -> web.Response:
        cfg = get_config()
        db_ok = position_store.check_connection() if position_store else False
        return web.json_response({
            "status": "ok",
            "uptime_s": round(time.time() - _START_TIME, 1),
            "market_open": is_market_open(),
            "market_time_et": now_et().strftime("%Y-%m-%d %H:%M:%S ET"),
            "mode": cfg.get("mode", "paper"),
            "broker": cfg.get("broker", {}).get("name", "mock"),
            "database_connected": db_ok,
        })

    async def get_signals(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(500, int(request.rel_url.query.get("limit", _MAX_SIGNALS))))
        except (TypeError, ValueError):
            limit = _MAX_SIGNALS
        return web.json_response(signal_store[-limit:])

    async def get_positions(request: web.Request) -> web.Response:
        positions = position_store.get_positions() if position_store else {}
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
        open_count = position_store.open_count if position_store else 0
        return web.json_response({
            "uptime_s": round(time.time() - _START_TIME, 1),
            "signal_count": len(signal_store),
            "open_positions": open_count,
            "market_open": is_market_open(),
        })

    async def get_history(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(500, int(request.rel_url.query.get("limit", 50))))
        except (TypeError, ValueError):
            limit = 50
        return web.json_response(list(reversed(_action_store[-limit:])))

    async def get_status(request: web.Request) -> web.Response:
        from src.daily_circuit_breaker import DailyCircuitBreaker
        cfg        = get_config()
        open_count = position_store.open_count if position_store else 0
        db_ok      = position_store.check_connection() if position_store else False
        pnl        = position_store.get_pnl_summary() if position_store else {
            "total_pnl": 0.0, "trade_count": 0, "win_count": 0, "loss_count": 0,
            "win_rate": 0.0, "avg_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0,
        }
        paper_capital = float(cfg.get("paper_trading", {}).get("initial_capital", 1000.0))
        cb_status = DailyCircuitBreaker(cfg, position_store).status
        pending_count = strategy_engine.get_pending_count() if strategy_engine else 0
        return web.json_response({
            # system
            "uptime_s":          round(time.time() - _START_TIME, 1),
            "market_open":       is_market_open(),
            "market_time_et":    now_et().strftime("%Y-%m-%d %H:%M:%S ET"),
            "mode":              cfg.get("mode", "paper"),
            "broker":            cfg.get("broker", {}).get("name", "mock"),
            "database_connected": db_ok,
            # live counts
            "open_positions":    open_count,
            "signal_count":      len(signal_store),
            "action_count":      len(_action_store),
            "pending_signals":   pending_count,
            # paper trading capital
            "paper_capital":     paper_capital,
            # p&l
            **pnl,
            # circuit breaker
            "circuit_breaker":   cb_status,
            # recent activity (newest first)
            "recent_actions":    list(reversed(_action_store[-30:])),
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
                open_count = position_store.open_count if position_store else 0
                db_ok      = position_store.check_connection() if position_store else False
                positions  = position_store.get_positions() if position_store else {}
                sigs       = signal_store[-20:] if signal_store else []
                acts       = list(reversed(_action_store[-30:])) if _action_store else []
                pnl        = position_store.get_pnl_summary() if position_store else {
                    "total_pnl": 0.0, "trade_count": 0, "win_count": 0,
                    "loss_count": 0, "win_rate": 0.0, "avg_pnl": 0.0,
                    "best_trade": 0.0, "worst_trade": 0.0,
                }
                daily_pnl  = position_store.get_daily_pnl() if position_store else 0.0
                paper_capital = float(cfg.get("paper_trading", {}).get("initial_capital", 25000.0))
                cb = DailyCircuitBreaker(cfg, position_store).status
                pending_count = strategy_engine.get_pending_count() if strategy_engine else 0

                payload = _json.dumps({
                    "market_open":    is_market_open(),
                    "market_time":    now_et().strftime("%Y-%m-%d %H:%M:%S ET"),
                    "mode":           cfg.get("mode", "paper"),
                    "open_positions": open_count,
                    "signal_count":   len(signal_store),
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

    async def dashboard(request: web.Request) -> web.Response:
        cfg = get_config()
        mode = cfg.get("mode", "paper")
        paper_capital = float(cfg.get("paper_trading", {}).get("initial_capital", 25000.0))

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AlgoTrade Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{{
  --bg:#0d0f14;--surface:#141720;--border:#1e2330;--border2:#252c3d;
  --text:#cdd6f4;--muted:#6c7086;--green:#a6e3a1;--red:#f38ba8;
  --blue:#89b4fa;--yellow:#f9e2af;--teal:#94e2d5;--lavender:#b4befe;
  --green-dim:#1e3228;--red-dim:#3b1a20;--blue-dim:#1a2340;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  background:var(--bg);color:var(--text);min-height:100vh;font-size:14px}}
.topnav{{background:var(--surface);border-bottom:1px solid var(--border);
  padding:0 20px;height:52px;display:flex;align-items:center;gap:12px}}
.topnav-brand{{font-weight:700;font-size:16px;color:var(--blue);letter-spacing:.5px;margin-right:auto}}
.topnav-brand span{{color:var(--muted);font-weight:400;font-size:12px;margin-left:6px}}
.pill{{padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;border:1px solid}}
.pill-green{{background:var(--green-dim);color:var(--green);border-color:var(--green)}}
.pill-red{{background:var(--red-dim);color:var(--red);border-color:var(--red)}}
.pill-yellow{{background:#2a200a;color:var(--yellow);border-color:var(--yellow)}}
.pill-blue{{background:var(--blue-dim);color:var(--blue);border-color:var(--blue)}}
.live-dot{{width:7px;height:7px;border-radius:50%;background:var(--green);
  animation:pulse 2s infinite;display:inline-block;margin-right:4px}}
@keyframes pulse{{0%,100%{{opacity:1;box-shadow:0 0 0 0 rgba(166,227,161,.4)}}
  50%{{opacity:.6;box-shadow:0 0 0 4px rgba(166,227,161,0)}}}}
.main{{padding:16px 20px;max-width:1400px;margin:0 auto}}
.kpi-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:14px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:14px 16px;position:relative;overflow:hidden}}
.kpi::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px}}
.kpi-green::before{{background:var(--green)}}
.kpi-red::before{{background:var(--red)}}
.kpi-blue::before{{background:var(--blue)}}
.kpi-yellow::before{{background:var(--yellow)}}
.kpi-teal::before{{background:var(--teal)}}
.kpi-label{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px}}
.kpi-value{{font-size:22px;font-weight:700;line-height:1}}
.kpi-sub{{font-size:11px;color:var(--muted);margin-top:4px}}
.pos-green{{color:var(--green)}} .pos-red{{color:var(--red)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}}
.grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:12px}}
@media(max-width:900px){{.grid2,.grid3{{grid-template-columns:1fr}}}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px}}
.card-hdr{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.card-title{{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}}
.badge{{background:var(--border2);color:var(--muted);font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600}}
table{{width:100%;border-collapse:collapse}}
thead th{{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;
  color:var(--muted);padding:6px 10px;border-bottom:1px solid var(--border);text-align:left}}
tbody td{{padding:7px 10px;border-bottom:1px solid var(--border);font-size:13px;color:var(--text)}}
tbody tr:last-child td{{border-bottom:none}}
tbody tr:hover td{{background:rgba(255,255,255,.03)}}
.empty{{color:var(--muted);font-style:italic;text-align:center;padding:20px;font-size:13px}}
.call{{color:var(--green);font-weight:700}} .put{{color:var(--red);font-weight:700}}
.ev-fill{{color:var(--green)}} .ev-reject{{color:var(--red)}} .ev-start{{color:var(--blue)}} .ev-other{{color:var(--muted)}}
.cb-ok{{color:var(--green)}} .cb-halt{{color:var(--red)}}
.chart-wrap{{position:relative;height:160px}}
.warn-bar{{background:#2a200a;border:1px solid var(--yellow);color:var(--yellow);
  padding:8px 16px;font-size:12px;text-align:center;border-radius:6px;margin-bottom:12px}}
.footer{{color:var(--muted);font-size:11px;padding:12px 20px;border-top:1px solid var(--border);
  display:flex;flex-wrap:wrap;gap:8px}}
.footer a{{color:var(--blue);text-decoration:none}}
</style>
</head>
<body>

<nav class="topnav">
  <span class="topnav-brand">&#9651; AlgoTrade <span>Options Automation</span></span>
  <span id="nav-mkt" class="pill pill-blue">—</span>
  <span id="nav-mode" class="pill pill-yellow">{mode.upper()}</span>
  <span><span class="live-dot"></span><span id="nav-time" style="font-size:12px;color:var(--muted)">connecting...</span></span>
</nav>

<div class="main">
{"" if mode=="automated" else f'<div class="warn-bar">&#9888; {mode.upper()} MODE — {"No real orders are placed" if mode=="paper" else "Manual approval required"}</div>'}

<!-- KPI row -->
<div class="kpi-row">
  <div class="kpi kpi-blue">
    <div class="kpi-label">Account Value</div>
    <div class="kpi-value" id="k-capital">—</div>
    <div class="kpi-sub">Starting: ${paper_capital:,.0f}</div>
  </div>
  <div class="kpi kpi-green">
    <div class="kpi-label">Total P&amp;L</div>
    <div class="kpi-value" id="k-total-pnl">—</div>
    <div class="kpi-sub" id="k-total-pnl-sub">all time</div>
  </div>
  <div class="kpi kpi-teal">
    <div class="kpi-label">Today&apos;s P&amp;L</div>
    <div class="kpi-value" id="k-daily-pnl">—</div>
    <div class="kpi-sub" id="k-daily-pnl-sub">today</div>
  </div>
  <div class="kpi kpi-yellow">
    <div class="kpi-label">Win Rate</div>
    <div class="kpi-value" id="k-winrate">—</div>
    <div class="kpi-sub" id="k-trades">0 trades</div>
  </div>
  <div class="kpi kpi-blue">
    <div class="kpi-label">Open Positions</div>
    <div class="kpi-value" id="k-positions">—</div>
    <div class="kpi-sub" id="k-pending">0 pending signals</div>
  </div>
  <div class="kpi kpi-green">
    <div class="kpi-label">Best Trade</div>
    <div class="kpi-value" id="k-best">—</div>
    <div class="kpi-sub" id="k-worst">worst: —</div>
  </div>
</div>

<!-- P&L chart + circuit breaker -->
<div class="grid2">
  <div class="card">
    <div class="card-hdr">
      <span class="card-title">P&amp;L History</span>
      <span class="badge" id="cb-status">Circuit Breaker: —</span>
    </div>
    <div class="chart-wrap"><canvas id="pnl-chart"></canvas></div>
  </div>
  <div class="card">
    <div class="card-hdr">
      <span class="card-title">Trade Stats</span>
      <span class="badge" id="db-badge">DB: —</span>
    </div>
    <div class="chart-wrap"><canvas id="win-chart"></canvas></div>
  </div>
</div>

<!-- Positions + Signals -->
<div class="grid2">
  <div class="card">
    <div class="card-hdr">
      <span class="card-title">Open Positions</span>
      <span class="badge" id="pos-badge">0</span>
    </div>
    <div id="positions-body"><p class="empty">No open positions.</p></div>
  </div>
  <div class="card">
    <div class="card-hdr">
      <span class="card-title">Recent Signals</span>
      <span class="badge" id="sig-badge">0</span>
    </div>
    <div id="signals-body"><p class="empty">No signals yet.</p></div>
  </div>
</div>

<!-- Activity log -->
<div class="card">
  <div class="card-hdr">
    <span class="card-title">Activity Log</span>
    <span class="badge" id="act-badge">0</span>
  </div>
  <div id="activity-body"><p class="empty">No activity yet.</p></div>
</div>

</div><!-- /main -->

<div class="footer">
  <span>Uptime: <b id="uptime">—</b></span>
  <a href="/health">/health</a>
  <a href="/signals">/signals</a>
  <a href="/positions">/positions</a>
  <a href="/metrics">/metrics</a>
  <a href="/status">/status</a>
</div>

<script>
/* ── helpers ── */
const $ = id => document.getElementById(id);
function esc(v){{const d=document.createElement('div');d.textContent=String(v??'—');return d.textContent}}
function fmt(ts){{return ts?String(ts).slice(0,19).replace('T',' '):'—'}}
function money(v){{
  const n=Number(v)||0;
  const sign=n>=0?'+':'-';
  return (n>=0?'':'-')+'$'+Math.abs(n).toLocaleString('en-US',{{minimumFractionDigits:2,maximumFractionDigits:2}});
}}
function pct(v){{return((Number(v)||0)*100).toFixed(1)+'%'}}
function setColor(el,v){{el.className=el.className.replace(/pos-(green|red)/g,'');el.classList.add(Number(v)>=0?'pos-green':'pos-red')}}

/* ── P&L chart ── */
const pnlCtx=$('pnl-chart').getContext('2d');
const pnlChart=new Chart(pnlCtx,{{
  type:'line',
  data:{{labels:[],datasets:[{{label:'Cumulative P&L',data:[],borderColor:'#89b4fa',
    backgroundColor:'rgba(137,180,250,.08)',fill:true,tension:.3,pointRadius:3,
    pointBackgroundColor:ctx=>{{
      const v=ctx.dataset.data[ctx.dataIndex]||0;
      return v>=0?'#a6e3a1':'#f38ba8';
    }}
  }}]}},
  options:{{responsive:true,maintainAspectRatio:false,animation:{{duration:300}},
    plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>money(c.parsed.y)}}}}}},
    scales:{{
      x:{{ticks:{{color:'#6c7086',maxRotation:0,maxTicksLimit:6}},grid:{{color:'#1e2330'}}}},
      y:{{ticks:{{color:'#6c7086',callback:v=>money(v)}},grid:{{color:'#1e2330'}}}}
    }}
  }}
}});
let pnlHistory=[{{label:'Start',value:0}}];

/* ── Win/Loss donut ── */
const winCtx=$('win-chart').getContext('2d');
const winChart=new Chart(winCtx,{{
  type:'doughnut',
  data:{{labels:['Wins','Losses','No trades'],datasets:[{{
    data:[0,0,1],
    backgroundColor:['#a6e3a1','#f38ba8','#1e2330'],
    borderColor:['#a6e3a1','#f38ba8','#1e2330'],
    borderWidth:1,hoverOffset:4
  }}]}},
  options:{{responsive:true,maintainAspectRatio:false,animation:{{duration:300}},
    cutout:'65%',
    plugins:{{legend:{{position:'right',labels:{{color:'#6c7086',font:{{size:11}},boxWidth:12}}}},
      tooltip:{{callbacks:{{label:c=>c.label+': '+c.parsed}}}}}}
  }}
}});

/* ── render tables ── */
function renderPositions(pos){{
  const keys=Object.keys(pos||{{}});
  if(!keys.length)return'<p class="empty">No open positions.</p>';
  let h='<table><thead><tr><th>Contract</th><th>Symbol</th><th>Dir</th><th>Qty</th><th>Entry</th><th>Stop</th><th>Target</th></tr></thead><tbody>';
  for(const k of keys){{
    const p=pos[k];
    const dir=(p.direction||'').toUpperCase();
    h+=`<tr>
      <td style="font-family:monospace;font-size:12px;color:var(--muted)">${{esc(k)}}</td>
      <td><b>${{esc(p.symbol||k.split('_')[0])}}</b></td>
      <td class="${{dir==='CALL'?'call':'put'}}">${{esc(dir)}}</td>
      <td>${{esc(p.quantity)}}</td>
      <td>$${{esc(p.entry_price)}}</td>
      <td style="color:var(--red)">$${{esc(p.stop_loss)}}</td>
      <td style="color:var(--green)">$${{esc(p.take_profit)}}</td>
    </tr>`;
  }}
  return h+'</tbody></table>';
}}

function renderSignals(sigs){{
  if(!sigs||!sigs.length)return'<p class="empty">No signals yet.</p>';
  let h='<table><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Stop</th><th>Target</th><th>Strategy</th></tr></thead><tbody>';
  for(const s of [...sigs].reverse()){{
    const dir=(s.direction||'').toUpperCase();
    h+=`<tr>
      <td style="color:var(--muted);font-size:12px">${{fmt(s.ts)}}</td>
      <td><b>${{esc(s.symbol)}}</b></td>
      <td class="${{dir==='CALL'?'call':'put'}}">${{esc(dir)}}</td>
      <td>$${{esc(s.entry)}}</td>
      <td style="color:var(--red)">$${{esc(s.stop)}}</td>
      <td style="color:var(--green)">$${{esc(s.target)}}</td>
      <td style="color:var(--muted);font-size:12px">${{esc(s.strategy)}}</td>
    </tr>`;
  }}
  return h+'</tbody></table>';
}}

function evCls(ev){{
  if(!ev)return'ev-other';
  if(ev.includes('FILL')||ev.includes('CLOSED'))return'ev-fill';
  if(ev.includes('REJECT'))return'ev-reject';
  if(ev.includes('START'))return'ev-start';
  return'ev-other';
}}

function renderActivity(acts){{
  if(!acts||!acts.length)return'<p class="empty">No activity yet.</p>';
  let h='<table><thead><tr><th>Time</th><th>Event</th><th>Symbol</th><th>Detail</th></tr></thead><tbody>';
  for(const a of acts){{
    h+=`<tr>
      <td style="color:var(--muted);font-size:12px">${{fmt(a.ts)}}</td>
      <td class="${{evCls(a.event||'')}}" style="font-weight:600">${{esc(a.event)}}</td>
      <td><b>${{esc(a.symbol)}}</b></td>
      <td style="color:var(--muted);font-size:12px">${{esc(a.detail)}}</td>
    </tr>`;
  }}
  return h+'</tbody></table>';
}}

/* ── main update ── */
function update(d){{
  /* nav bar */
  const open=d.market_open;
  $('nav-mkt').textContent=open?'MARKET OPEN':'MARKET CLOSED';
  $('nav-mkt').className='pill '+(open?'pill-green':'pill-red');
  $('nav-time').textContent=d.market_time||'—';
  $('uptime').textContent=d.uptime_s!=null?d.uptime_s+'s':'—';

  /* KPI cards */
  const cap=d.paper_capital||25000;
  const totalPnl=d.total_pnl||0;
  const accountVal=cap+totalPnl;
  $('k-capital').textContent='$'+accountVal.toLocaleString('en-US',{{minimumFractionDigits:2,maximumFractionDigits:2}});

  $('k-total-pnl').textContent=money(totalPnl);
  setColor($('k-total-pnl'),totalPnl);
  $('k-total-pnl-sub').textContent=(d.trade_count||0)+' closed trades';

  const dpnl=d.daily_pnl||0;
  $('k-daily-pnl').textContent=money(dpnl);
  setColor($('k-daily-pnl'),dpnl);
  const dpct=cap?((dpnl/cap)*100).toFixed(2):0;
  $('k-daily-pnl-sub').textContent=(dpnl>=0?'+':'')+dpct+'% today';

  const wr=d.win_rate||0;
  $('k-winrate').textContent=pct(wr);
  $('k-trades').textContent=(d.win_count||0)+'W / '+(d.loss_count||0)+'L';

  $('k-positions').textContent=d.open_positions??'—';
  $('k-pending').textContent=(d.pending_signals||0)+' pending signals';

  $('k-best').textContent=money(d.best_trade||0);
  $('k-best').className='kpi-value '+(( d.best_trade||0)>=0?'pos-green':'pos-red');
  $('k-worst').textContent='worst: '+money(d.worst_trade||0);

  /* circuit breaker */
  const cb=d.circuit_breaker||{{}};
  const cbHalt=cb.halted||false;
  $('cb-status').textContent='Circuit Breaker: '+(cbHalt?'HALTED ⚠':'OK');
  $('cb-status').style.color=cbHalt?'var(--red)':'var(--green)';

  /* DB badge */
  $('db-badge').textContent='DB: '+(d.db_ok?'Connected':'Error');
  $('db-badge').style.color=d.db_ok?'var(--green)':'var(--red)';

  /* P&L chart — append new point if value changed */
  const lastVal=pnlHistory[pnlHistory.length-1].value;
  if(totalPnl!==lastVal){{
    const lbl=d.market_time?d.market_time.slice(11,16):'now';
    pnlHistory.push({{label:lbl,value:totalPnl}});
    if(pnlHistory.length>100)pnlHistory.shift();
    pnlChart.data.labels=pnlHistory.map(p=>p.label);
    pnlChart.data.datasets[0].data=pnlHistory.map(p=>p.value);
    pnlChart.update('none');
  }}

  /* Win/Loss donut */
  const wins=d.win_count||0,losses=d.loss_count||0;
  if(wins+losses>0){{
    winChart.data.labels=['Wins','Losses'];
    winChart.data.datasets[0].data=[wins,losses];
    winChart.data.datasets[0].backgroundColor=['#a6e3a1','#f38ba8'];
    winChart.data.datasets[0].borderColor=['#a6e3a1','#f38ba8'];
  }}else{{
    winChart.data.labels=['No trades'];
    winChart.data.datasets[0].data=[1];
    winChart.data.datasets[0].backgroundColor=['#1e2330'];
    winChart.data.datasets[0].borderColor=['#1e2330'];
  }}
  winChart.update('none');

  /* tables */
  const posKeys=Object.keys(d.positions||{{}});
  $('pos-badge').textContent=posKeys.length;
  $('positions-body').innerHTML=renderPositions(d.positions);

  $('sig-badge').textContent=(d.signals||[]).length;
  $('signals-body').innerHTML=renderSignals(d.signals);

  $('act-badge').textContent=(d.activity||[]).length;
  $('activity-body').innerHTML=renderActivity(d.activity);
}}

const es=new EventSource('/stream');
es.onmessage=ev=>{{try{{update(JSON.parse(ev.data))}}catch(err){{console.error(err)}}}};
es.onerror=()=>{{$('nav-time').textContent='reconnecting...';setTimeout(()=>location.reload(),5000)}};
</script>
</body></html>"""
        return web.Response(text=html, content_type="text/html")

    def _mask(value: str) -> str:
        """Return a masked version of a secret string."""
        return ("*" * 8) if value else ""

    async def get_config_endpoint(request: web.Request) -> web.Response:
        # Merge DB overrides (Railway-safe) on top of base config
        base = get_config()
        db_overrides = position_store.get_config_overrides() if position_store else {}
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
        if position_store:
            position_store.merge_config_overrides(updates)

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
        db_overrides = position_store.get_config_overrides() if position_store else {}
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
        if not market_adapter:
            return web.json_response({"error": "market adapter unavailable"}, status=503)
        try:
            gainers = await market_adapter.get_top_gainers(limit=5)
            losers  = await market_adapter.get_top_losers(limit=5)
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
        if not market_adapter:
            return web.json_response({"error": "market adapter unavailable"}, status=503)
        try:
            bars  = await market_adapter.get_historical_bars(symbol, range_str, interval)
            quote = await market_adapter.get_quote(symbol)
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
        total = len(signal_store)
        calls = sum(1 for s in signal_store if s.get("direction") == "CALL")
        puts  = total - calls
        seen: dict = {}
        sig_counts: dict = {}
        for s in signal_store:
            sym = s.get("symbol")
            if sym:
                seen[sym] = True
            sname = _extract_strategy_name(s)
            if sname:
                sig_counts[sname] = sig_counts.get(sname, 0) + 1

        perf = position_store.get_strategy_scores() if position_store else {}

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
        if not position_store:
            return web.json_response({"error": "no persistence store"}, status=503)
        try:
            from src.persistence import (
                PositionRecord, CooldownRecord, SignalRecord,
                ActionRecord, StrategyPerformanceRecord,
            )
            from datetime import datetime, timezone as _tz
            with position_store.SessionLocal() as session:
                session.query(PositionRecord).delete()
                session.query(CooldownRecord).delete()
                session.query(SignalRecord).delete()
                session.query(ActionRecord).delete()
                session.query(StrategyPerformanceRecord).delete()
                session.commit()

            signal_store.clear()
            _action_store.clear()

            if broker_adapter is not None and hasattr(broker_adapter, "reset"):
                broker_adapter.reset()

            reset_entry = {
                "event": "SYSTEM_RESET", "symbol": None,
                "detail": "Paper trading data reset by user",
                "data": {}, "ts": datetime.now(_tz.utc).isoformat(),
            }
            _action_store.append(reset_entry)
            log.info("paper trading data reset by user")
            return web.json_response({"ok": True})
        except Exception as exc:
            log.error("reset failed", error=str(exc))
            return web.json_response({"error": str(exc)}, status=500)

    async def run_backtest_endpoint(request: web.Request) -> web.Response:
        """Run a real backtest using Yahoo Finance historical data."""
        if not market_adapter:
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
            bars = await market_adapter.get_historical_bars(symbol, range_str, interval)
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
        _action_store.append(entry)
        if position_store:
            position_store.add_action(
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
        cb  = DailyCircuitBreaker(cfg, position_store)
        return web.json_response(cb.status)

    # ── Pending signals (awaiting confirmation) ─────────────────────────────

    async def get_pending_signals(request: web.Request) -> web.Response:
        from datetime import timezone as _tz
        if strategy_engine is None:
            return web.json_response({"pending": []})
        pending = getattr(strategy_engine, "_pending", {})
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
                "confirmations_needed": getattr(strategy_engine, "_confirm_wait_bars", 2),
                "strike":               plan.contract.strike if plan else None,
                "entry":                plan.entry_limit if plan else None,
                "first_seen_at":        first_seen.isoformat() if first_seen else None,
                "expires_in_s":         round(expires_in),
            })
        return web.json_response({"pending": result})

    # ── Router ──────────────────────────────────────────────────────────────

    app = web.Application()
    app.router.add_get("/health",           health)
    app.router.add_get("/signals",          get_signals)
    app.router.add_get("/positions",        get_positions)
    app.router.add_get("/metrics",          get_metrics)
    app.router.add_get("/history",          get_history)
    app.router.add_get("/status",           get_status)
    app.router.add_get("/overview",         get_overview)
    app.router.add_get("/quote/{symbol}",   get_quote)
    app.router.add_get("/strategies",       get_strategies)
    app.router.add_post("/reset",           post_reset)
    app.router.add_post("/order",           post_order)
    app.router.add_post("/backtest/run",    run_backtest_endpoint)
    app.router.add_get("/config",                get_config_endpoint)
    app.router.add_post("/config",               post_config_endpoint)
    app.router.add_post("/config/test-email",    test_email_endpoint)
    app.router.add_get("/circuit-breaker",       get_circuit_breaker)
    app.router.add_get("/pending-signals",       get_pending_signals)
    app.router.add_get("/stream",           sse_stream)
    app.router.add_get("/",                 dashboard)
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
