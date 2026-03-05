---
description: Initialize context for the SolutionDemo (Hot Stock) quantitative trading signal system
---

# Project Overview

**Hot Stock** — A-share short-term limit-up (涨停板) strategy system, migrated from JoinQuant to run locally. It auto-selects stocks, scores them, generates buy/sell signals, and supports tick-level real-time sell monitoring.

- **Language**: Python 3.11+
- **Framework**: FastAPI (API layer) + APScheduler (task scheduling)
- **Entry points**: `main.py` (daemon mode), `scan.py` (one-shot scan CLI)

## Project Structure

```
SolutionDemo/
├── main.py                 # Entry: scheduler + tick monitor + API server (daemon)
├── scan.py                 # Intraday stock scan CLI (manual one-shot)
├── config.py               # Global config (auto-loads .env)
├── scheduler.py            # APScheduler scheduled tasks
├── api/
│   ├── app.py              # FastAPI app factory
│   ├── schemas.py          # Pydantic request/response models
│   └── routers/
│       ├── portfolio.py    # Portfolio CRUD endpoints
│       ├── trades.py       # Trade signals & history endpoints
│       ├── scan.py         # Trigger scan endpoint
│       └── market.py       # Market stats & scheduler status
├── strategy/
│   ├── core.py             # GlobalState + Context + utility functions
│   ├── stock_select.py     # 5-pattern stock selection
│   ├── scoring.py          # 6-factor scoring system
│   ├── sell_rules.py       # 3-layer sell rules
│   ├── buy.py              # Buy logic (incl. Friday branch)
│   └── tick_monitor.py     # Tick-level real-time sell monitor
├── data/
│   ├── __init__.py         # create_provider() factory
│   ├── provider.py         # Abstract data interface
│   ├── tushare_src.py      # Tushare Pro implementation
│   ├── akshare_src.py      # AKShare implementation (free)
│   ├── eastmoney_src.py    # EastMoney realtime + tick
│   ├── composite.py        # Tushare + EastMoney composite source
│   └── cache.py            # SQLite local data cache
├── portfolio/
│   ├── models.py           # Position / TradeRecord dataclasses
│   └── tracker.py          # Local portfolio tracker (JSON persistence)
├── notify/
│   ├── signal.py           # Signal formatting
│   └── push.py             # Push: console / ServerChan / DingTalk
├── utils/
│   ├── logger.py           # Standard logging setup
│   ├── code_convert.py     # Security code format conversion
│   └── trade_calendar.py   # Trade calendar (AKShare + SQLite cache)
├── data_store/             # Runtime data (cache.db, portfolio.json)
├── docs/
│   └── strategy.md         # Detailed strategy documentation
├── hot_stock.py            # Archived original JoinQuant version (reference)
├── requirements.txt        # tushare, akshare, pandas, numpy, requests, apscheduler, fastapi, uvicorn
├── .env / .env.example     # Environment variables
├── Dockerfile              # Container build
└── docker-compose.yml      # Docker deployment
```

## Architecture & Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  main.py (Entry Point)                                      │
│  ┌───────────┐   ┌──────────────┐   ┌────────────────────┐  │
│  │ Scheduler │   │ Tick Monitor │   │  FastAPI (Thread)  │  │
│  │ (APSched) │   │ (3s polling) │   │  :8000/docs        │  │
│  └─────┬─────┘   └──────┬───────┘   └────────┬───────────┘  │
│        │                │                     │              │
│        v                v                     v              │
│  ┌──────────────────────────────────────────────────┐        │
│  │  strategy/  (Core Logic)                         │        │
│  │  stock_select → scoring → buy / sell_rules       │        │
│  │  GlobalState + Context orchestration             │        │
│  └──────────────────┬───────────────────────────────┘        │
│                     │                                        │
│        ┌────────────┼────────────┐                           │
│        v            v            v                           │
│   ┌─────────┐ ┌──────────┐ ┌──────────┐                     │
│   │  data/  │ │portfolio/│ │ notify/  │                     │
│   │ Tushare │ │ tracker  │ │ push     │                     │
│   │ AKShare │ │ (JSON)   │ │ signal   │                     │
│   │ EastMon │ └──────────┘ └──────────┘                     │
│   │ cache   │                                               │
│   └─────────┘                                               │
└─────────────────────────────────────────────────────────────┘
```

## Strategy Details

### 5 Stock Selection Patterns
1. **连板龙头** — Leading consecutive limit-up stocks, high-open at auction
2. **弱转强** — Weak-to-strong: yesterday's broken limit-up, today strong auction
3. **一进二** — First-to-second board: first limit-up → next day open 1-6%
4. **首板低开** — First board low-open: next day gap-down ≤3%, relatively low position
5. **反向首板低开** — Reverse first board: yesterday limit-down, today gap-up

### 6 Scoring Factors (Max 40, Threshold 14)
- Limit-up quality (0-5)
- Technical indicators (0-10)
- Volume MA ratio (0-5)
- Hot concept/theme (0-5)
- Market sentiment (0-5)
- Institutional money flow (0-10)

### 3-Layer Sell System
1. **Auction sell** (09:28) — limit-down open, high-volume upper shadow
2. **5-min technical stop-loss** (09:36-10:30, 13:05-14:45) — technical indicators
3. **15-min strategy sell** (10:31-14:50) — mutually exclusive with layer 2

### Tick-Level Monitor (3s polling)
- Rapid drop stop-loss (>3% in 1 min)
- High-volume big drop (>6% with 1.5x volume)
- Limit-up break detection

## Key Config Parameters (`config.py`)
- `POSITION_LIMIT = 5` — Max positions
- `MIN_SCORE = 14` — Minimum score threshold
- `MAX_SINGLE_POSITION = 0.30` — Max single stock weight
- `INITIAL_CASH = 10,000` — Starting virtual capital
- `TICK_POLL_INTERVAL = 3` — Tick poll interval (seconds)

## Running

```bash
# Install deps
pip install -r requirements.txt

# Copy and configure env
cp .env.example .env

# Daemon mode (scheduler + tick + API)
python main.py

# One-shot scan
python scan.py
python scan.py --min-score 16 --notify

# Docker
docker compose up -d --build
```

## Coding Conventions
- Internal stock code format: Tushare style (`000300.SH`, `600519.SH`)
- Data caching via SQLite in `data_store/cache.db`
- Portfolio persistence via JSON in `data_store/portfolio.json`
- Logging via `utils.logger.log` (standard Python logging)
- All strategy state managed through `strategy.core.Context` object
- Notifications abstracted behind `NOTIFY_BACKEND` config
