// Backend API client — all requests go through Next.js proxy at /api/*
// which rewrites to http://localhost:8181/* (configured in next.config.ts)

const API_BASE = "/api";

export interface Health {
  status: string;
  uptime_s: number;
  market_open: boolean;
  market_time_et: string;
  mode: string;
  broker: string;
  database_connected: boolean;
}

export interface Metrics {
  uptime_s: number;
  signal_count: number;
  open_positions: number;
  market_open: boolean;
}

export interface Position {
  symbol: string;
  option_symbol: string;
  direction: "CALL" | "PUT";
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  quantity: number;
  underlying_price: number;
  opened_at: string;
  /** Cost basis = entry_price × quantity × 100 (computed by server) */
  cost_basis: number;
  /** Unrealized P&L in dollars. null when live price is unavailable. */
  unrealized_pnl: number | null;
  /** Unrealized P&L as a percentage of cost_basis. null when unavailable. */
  unrealized_pnl_pct: number | null;
}

export interface PositionsResponse {
  open_positions: Record<string, Position>;
  count: number;
  /** Sum of all position cost bases */
  total_cost_basis: number;
}

export interface Signal {
  symbol: string;
  direction: "CALL" | "PUT";
  strike: number;
  expiry: string;
  entry: number;
  stop: number;
  target: number;
  size: number;
  rationale: string;
  strategy?: string;
  ts: string;
}

export interface SimStatus {
  active: boolean;
  sim_time?: string;
  sim_time_iso?: string;
  speed?: number;
  paused?: boolean;
  sim_date?: string;
  day_complete?: boolean;
  market_open?: boolean;
}

export interface ConfigPayload {
  mode?: string;
  broker_name?: string;
  screener_provider?: string;
  screener_poll_interval_seconds?: number;
  screener_top_n?: number;
  screener_market_hours_only?: boolean;
  fmp_api_key?: string;
  fmp_api_key_set?: boolean;
  risk_max_position_pct?: number;
  risk_max_open_positions?: number;
  risk_pdt_equity_threshold?: number;
  risk_stop_loss_atr_mult?: number;
  risk_take_profit_atr_mult?: number;
  notify_email_enabled?: boolean;
  notify_email_provider?: string;
  notify_email_api_key?: string;
  notify_email_api_key_set?: boolean;
  notify_email_smtp_host?: string;
  notify_email_smtp_port?: number;
  notify_email_username?: string;
  notify_email_password?: string;
  notify_email_password_set?: boolean;
  notify_email_recipient?: string;
  notify_webhook_enabled?: boolean;
  notify_webhook_url?: string;
  // circuit breaker
  cb_daily_profit_target_pct?: number;
  cb_daily_loss_limit_pct?: number;
  // signal confirmation
  confirm_wait_bars?: number;
  confirm_expire_minutes?: number;
  // trading hours
  trading_hours_start?: string;
  trading_hours_end?: string;
  webull_device_id?: string;
  webull_access_token?: string;
  webull_refresh_token?: string;
  webull_trade_token?: string;
  webull_account_id?: string;
  webull_account_id_set?: boolean;
}

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${path} returned ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  health:       ()                        => fetchJSON<Health>("/health"),
  metrics:      ()                        => fetchJSON<Metrics>("/metrics"),
  positions:    ()                        => fetchJSON<PositionsResponse>("/positions"),
  signals:      (limit = 100)             => fetchJSON<Signal[]>(`/signals?limit=${limit}`),
  overview:     ()                        => fetchJSON<OverviewResponse>("/overview"),
  quote:        (symbol: string, range = "1d", interval = "1m") =>
                  fetchJSON<QuoteResponse>(`/quote/${symbol}?range=${range}&interval=${interval}`),
  strategies:   ()                        => fetchJSON<StrategiesResponse>("/strategies"),
  backtest:     (req: BacktestRequest)    => fetch(`${API_BASE}/backtest/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  }).then((r) => r.json() as Promise<BacktestResponse>),
  history:      (limit = 50)              => fetchJSON<Action[]>(`/history?limit=${limit}`),
  status:       ()                        => fetchJSON<StatusResponse>("/status"),
  circuitBreaker: ()                      => fetchJSON<CircuitBreakerStatus>("/circuit-breaker"),
  pendingSignals: ()                      => fetchJSON<PendingSignalsResponse>("/pending-signals"),
  getConfig:    ()                        => fetchJSON<ConfigPayload>("/config"),
  updateConfig: async (payload: ConfigPayload) => {
    const r = await fetch(`${API_BASE}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({})) as { error?: string };
      throw new Error(body.error ?? `Config update failed (${r.status})`);
    }
  },
  testEmail:    ()                        => fetch(`${API_BASE}/config/test-email`, {
    method: "POST",
  }).then((r) => r.json() as Promise<{ ok?: boolean; recipient?: string; error?: string }>),
  reset:        ()                        => fetch(`${API_BASE}/reset`, {
    method: "POST",
  }).then((r) => r.json() as Promise<{ ok?: boolean; error?: string }>),
  placeOrder:   (req: OrderRequest)       => fetch(`${API_BASE}/order`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  }).then((r) => r.json() as Promise<{ ok?: boolean; detail?: string; error?: string }>),
  simStatus:    ()                        => fetchJSON<SimStatus>("/sim/status"),
  simControl:   (action: "pause" | "resume" | "set_speed", speed?: number) =>
    fetch(`${API_BASE}/sim/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, speed }),
    }).then((r) => {
      if (!r.ok) throw new Error(`sim/control ${r.status}`);
      return r.json() as Promise<SimStatus>;
    }),
};

export interface MarketMover {
  symbol: string;
  price: number;
  change_pct: number;
  volume: number;
}

export interface OverviewResponse {
  gainers: MarketMover[];
  losers: MarketMover[];
  refreshed_at: string;
}

export interface PriceBar {
  datetime: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface QuoteResponse {
  symbol: string;
  current_price: number;
  change_pct: number;
  bars: PriceBar[];
}

export interface StrategyStats {
  name: string;
  description: string;
  signals: number;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  total_pnl: number;
}

export interface StrategiesResponse {
  is_active: boolean;
  total_signals: number;
  call_signals: number;
  put_signals: number;
  symbols_traded: string[];
  strategies: StrategyStats[];
}

export interface BacktestRequest {
  symbol: string;
  period: string;
}

export interface OrderRequest {
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price?: number;
  orderType: "market" | "limit";
}

export interface BacktestResponse {
  signals: number;
  trades: number;
  winners?: number;
  win_rate: number;
  avg_pnl_pct: number;
  total_pnl_pct: number;
  equity_curve: { date: string; equity: number }[];
  symbol: string;
  period: string;
  error?: string;
}

export interface CircuitBreakerStatus {
  halted: boolean;
  halt_reason: string;
  daily_pnl: number;
  daily_pnl_pct: number;
  profit_target_pct: number;
  loss_limit_pct: number;
  starting_equity: number;
  trading_date: string;
}

export interface PendingSignal {
  symbol: string;
  strategy: string;
  direction: "CALL" | "PUT";
  confirmations: number;
  confirmations_needed: number;
  strike: number | null;
  entry: number | null;
  first_seen_at: string;
  expires_in_s: number;
}

export interface PendingSignalsResponse {
  pending: PendingSignal[];
}

export interface StatusResponse {
  // system
  uptime_s: number;
  market_open: boolean;
  market_time_et: string;
  mode: string;
  broker: string;
  database_connected: boolean;
  // live counts
  open_positions: number;
  signal_count: number;
  action_count: number;
  pending_signals: number;
  // paper trading starting capital
  paper_capital: number;
  // p&l
  total_pnl: number;
  trade_count: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  avg_pnl: number;
  best_trade: number;
  worst_trade: number;
  // circuit breaker
  circuit_breaker: CircuitBreakerStatus;
  // recent activity
  recent_actions: Action[];
}

export type ActionEvent =
  | "ORDER_FILLED"
  | "POSITION_CLOSED"
  | "SIGNAL_REJECTED"
  | "SYSTEM_STARTED"
  | "SYSTEM_STOPPED";

export interface Action {
  event: ActionEvent;
  symbol: string | null;
  detail: string;
  data: Record<string, unknown>;
  ts: string;
}

export function formatUptime(seconds: number): string {
  if (seconds < 60)   return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}
