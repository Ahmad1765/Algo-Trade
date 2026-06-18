# Algo-Trade: Algorithmic Options Trading System

> **IMPORTANT LEGAL DISCLAIMER**
> This software is provided strictly for **educational and paper-trading purposes**.
> It is **not financial advice**. Using this system with real funds carries substantial
> risk of loss. You are solely responsible for ensuring compliance with all applicable
> exchange, broker, and regulatory requirements (SEC, FINRA, etc.) before executing
> any live trades. **Always paper-trade and validate extensively before risking capital.**

---

## 🚀 Live Demo

**Live dashboard:** <https://algo-trade-dashboard.onrender.com> &nbsp;·&nbsp; paper-trading only

![AlgoTrade dashboard](docs/screenshot-dashboard.png)

> The live demo runs in **paper mode** — orders are simulated, no real money moves.
> Access is password-protected. The free Render dyno may take ~30s to wake on first hit.

### Deploy your own

1. Fork this repo and connect it to Render — the included [`render.yaml`](../render.yaml) is a one-click Blueprint.
2. Create a free [Supabase](https://supabase.com) project and copy its **direct** connection string (port `5432`, then append `?sslmode=require`).
3. In Render → service → Environment, set:
   - `DATABASE_URL` — the Supabase connection string
   - `DASHBOARD_PASSWORD` — a long random value (this gates the dashboard)
   - `SESSION_SECRET` — another long random value (signs session cookies)
4. Deploy. Optionally add a free uptime monitor pinging `/health` every ~10 min to avoid cold starts.

> The server **fails fast on boot** if `DASHBOARD_PASSWORD`/`SESSION_SECRET` are unset (so a public deploy can never launch unauthenticated). For local development, set `DEV_MODE=1` to run without auth.

---

## Overview

Algo-Trade is a production-grade, event-driven algorithmic options-trading system
written in Python 3.7+. It implements:

- Real-time market scanning (top gainers/losers via FinancialModelingPrep)
- Options liquidity filtering (volume, OI, spread, DTE, moneyness)
- Momentum signal generation (RSI + MACD)
- Structured trade plans (entry limit, stop-loss, take-profit via ATR)
- Broker integration (Webull adapter + Mock adapter)
- Automated and manual operating modes
- Paper-trade and backtest engines
- Minimal REST API (health check, signals, positions)
- Structured JSON logging with secret redaction

---

## Project Structure

```
algo-trade/
├── src/
│   ├── config.py              # Configuration loader (YAML + env overrides)
│   ├── events.py              # Typed event/dataclass definitions
│   ├── market_adapter/        # FMP + Mock market data adapters
│   ├── screener/              # Top-N gainer/loser screener
│   ├── options_fetcher/       # Option chain fetcher + liquidity filter
│   ├── indicators/            # RSI, MACD, ATR (pure functions)
│   ├── strategy_engine/       # CEP-style signal engine
│   ├── execution/             # Broker adapters + order manager
│   ├── risk_manager/          # Position sizing + risk checks
│   ├── logger/                # Structured JSON logger
│   ├── api_server/            # aiohttp REST API
│   ├── backtester/            # Historical CSV backtester
│   └── cli/                   # Command-line entry point
├── tests/                     # pytest unit + integration tests
├── scripts/backtest.py        # Backtest runner script
├── sample_data/               # Minute-bar CSV for backtesting
├── systemd/                   # systemd unit file
├── config.yaml.template       # Configuration template
├── .env.template              # Environment variable template
├── Dockerfile                 # Container build
├── docker-compose.yml         # Compose for dev/paper-trade
├── requirements.txt           # Pinned dependencies
├── pyproject.toml             # Build + linting configuration
├── run_paper_demo.sh          # Quick demo script
├── README.md                  # This file
└── ARCHITECTURE.md            # System design documentation
```

---

## Quick Start

### 1. Prerequisites

- Python 3.8+ (3.11 recommended)
- `pip` or `venv`
- (Optional) Docker and Docker Compose

### 2. Installation

```bash
# Clone or copy the project
cd algo-trade

# Create virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

```bash
# Copy templates
cp config.yaml.template config.yaml
cp .env.template .env

# Edit .env — minimum required for paper-trade (mock mode):
# MODE=paper
# No API keys required when provider=mock and broker=mock
```

For live screener data, set `FMP_API_KEY` in `.env` and change `provider: fmp` in `config.yaml`.

### 4. Run the Paper-Trade Demo

```bash
# Quick demo using synthetic data (no credentials required):
bash run_paper_demo.sh

# Or run directly:
python -m src.cli.main --mode paper
```

### 5. Run in Manual Mode

Manual mode generates recommendations without placing orders:

```bash
python -m src.cli.main --mode manual
```

### 6. Run Backtest

```bash
python scripts/backtest.py sample_data/minute_sample.csv
```

### 7. Run Tests

```bash
pytest -q
# With coverage:
pytest --cov=src --cov-report=term-missing
```

---

## Operating Modes

| Mode | Description |
|------|-------------|
| `paper` | Full pipeline with mock broker — orders simulated, no real execution |
| `manual` | Signals logged as recommendations — no orders placed |
| `automated` | Live order placement via configured broker adapter |
| `backtest` | Replay historical CSV data and print P/L report |

Set mode via `--mode` CLI flag, `MODE` environment variable, or `mode:` in `config.yaml`.

---

## Configuration Reference

All configuration lives in `config.yaml` (or environment variable overrides).

### Critical Configuration Keys

| Key | Description | Default |
|-----|-------------|---------|
| `mode` | Operating mode | `paper` |
| `screener.provider` | `fmp` or `mock` | `mock` |
| `screener.top_n` | Candidates to scan | `10` |
| `screener.poll_interval_seconds` | Refresh interval | `60` |
| `options_filter.min_volume` | Min option volume | `100` |
| `options_filter.max_spread_pct` | Max bid-ask spread | `0.10` |
| `options_filter.max_dte` | Max days to expiry | `30` |
| `indicators.rsi_overbought` | RSI CALL threshold | `70` |
| `indicators.rsi_oversold` | RSI PUT threshold | `30` |
| `risk.max_position_pct` | Max equity per trade | `0.05` |
| `broker.name` | `mock` or `webull` | `mock` |

### Environment Variable Overrides

```bash
ALGO_SCREENER_TOP_N=15
ALGO_OPTIONS_FILTER_MIN_VOLUME=200
ALGO_RISK_MAX_POSITION_PCT=0.03
```

---

## Docker Deployment

### Build and run (paper mode):

```bash
docker build -t algo-trade .
docker run --env-file .env -p 8080:8080 algo-trade
```

### Docker Compose:

```bash
# Paper-trade (default)
docker-compose up -d

# Manual mode
docker-compose --profile manual up -d

# Run backtest
docker-compose --profile backtest run --rm algo-trade-backtest
```

---

## Deployment on Hostinger KVM (Linux VPS)

### Recommended: Docker + systemd

```bash
# On the VPS:
apt update && apt install -y docker.io docker-compose
systemctl enable docker

# Copy project files
scp -r algo-trade/ user@your-vps:/opt/algo-trade

# Configure
cd /opt/algo-trade
cp .env.template .env
nano .env    # Set FMP_API_KEY, MODE=paper, etc.

# Build and start
docker-compose up -d

# Check logs
docker-compose logs -f algo-trade
```

### Alternative: systemd service (without Docker)

```bash
# Install Python 3.11
apt install -y python3.11 python3.11-venv

# Set up project
mkdir -p /opt/algo-trade
cp -r . /opt/algo-trade/
cd /opt/algo-trade
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Create service user
useradd -m -s /bin/bash appuser
chown -R appuser:appuser /opt/algo-trade
chmod 600 /opt/algo-trade/.env

# Install systemd unit
cp systemd/algo-trade.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now algo-trade

# Monitor
journalctl -u algo-trade -f
```

---

## API Endpoints

The API server runs on `http://localhost:8080` (configurable):

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness check — returns `{"status": "ok"}` |
| `GET /signals` | Recent signal events (read-only) |
| `GET /positions` | Current open positions |
| `GET /metrics` | Uptime, signal count, open position count |
| `GET /` | HTML dashboard |

**Security note**: The API is read-only and unauthenticated. Restrict access at
the firewall/reverse-proxy level in production.

---

## Webull Integration

1. Install the Webull package: `pip install webull`
2. Obtain your credentials (device ID, tokens) from the Webull app
3. Set environment variables:
   ```bash
   WEBULL_DEVICE_ID=...
   WEBULL_ACCESS_TOKEN=...
   WEBULL_REFRESH_TOKEN=...
   WEBULL_TRADE_TOKEN=...
   WEBULL_ACCOUNT_ID=...
   ```
4. Set `broker.name: webull` in `config.yaml`
5. **Test in paper mode first** (`mode: paper` with mock data)

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError: src` | Run from project root; `pip install -r requirements.txt` |
| `FMP_API_KEY not set` | Set env var or use `provider: mock` for demo |
| `webull` import error | `pip install webull` or use `broker.name: mock` |
| API server port conflict | Change `API_PORT` in `.env` or `api_server.port` in config |
| RSI insufficient data | Increase `screener.poll_interval_seconds` or `lookback_bars` |
| Tests failing | Run `pytest -v` for detail; ensure `pip install -r requirements.txt` complete |

---

## Acceptance Test Checklist

Run these commands to validate the system:

```bash
# 1. Unit + integration tests (all mocked, no network)
pytest -q

# 2. Docker build
docker build -t algo-trade .

# 3. Docker paper-trade run (produces signals in logs)
docker run --env-file .env -e MODE=paper algo-trade

# 4. Backtest report
python scripts/backtest.py sample_data/minute_sample.csv

# 5. API health check (while system is running)
curl http://localhost:8080/health
```

Expected output for step 4: a report showing signal count, trades, and win rate.
Expected output for step 5: `{"status": "ok", "uptime_s": ...}`
