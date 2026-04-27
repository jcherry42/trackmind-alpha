# TRACKMIND — Product Requirements Document

## Vision
Institutional-grade prediction market intelligence + execution platform.
Hedge-fund operating system aesthetic in a consumer-grade Expo mobile app.

## Stack
- **Backend:** FastAPI + MongoDB (Motor) + APScheduler
- **Mobile:** Expo React Native (iOS + Android) with expo-router
- **AI:** Claude Sonnet 4.5 via Emergent Universal LLM Key (Alpha sentiment engine)
- **Auth:** JWT (access 24h + refresh 30d), bcrypt, Bearer headers + httpOnly cookies
- **Deployment scaffolding:** docker-compose, Dockerfiles, GitHub Actions (in `infra/`)

## Core trading-intelligence engines
1. **ALPHA** — narrative / catalyst / sentiment repricing (LLM-powered).
2. **QUANT** — Markov persistence + drift edges on price history.
3. **WHALE** — synthetic elite wallet consensus + cluster detection.
4. **MICRO** — cross-venue YES + NO < $1.00 arbitrage scanner.

## Master allocator
- Ranks all engine signals by `confidence × edge × liquidity / risk`.
- Risk constraints: max 15% per trade, 35% per correlated market, 20% cash buffer, 5% daily-DD.
- Defensive mode auto-engages as drawdown approaches limit.

## Mobile screens
- Login (with Face ID toggle), Register
- Dashboard (bankroll, today P&L, 4 engine cards, top signal, allocator with Auto-Deploy)
- Signals (filter chips per engine + 1-tap Approve)
- Positions (open/closed with live PnL)
- Micro Mode (cross-venue arb visualizer)
- Analytics (equity curve, win rate, sharpe, drawdown, by-engine)
- Controls modal (kill switch, engine pause/resume, risk constraints, paper reset)
- Settings modal (account, biometrics, mode, logout)

## API surface
Auth: `/api/auth/{register,login,refresh,logout,me}`
Trading: `/api/dashboard`, `/api/signals`, `/api/signals/micro`, `/api/positions`,
`/api/trade/approve`, `/api/trade/auto-deploy`, `/api/analytics`,
`/api/controls/{update,pause,resume,reset}`, `/api/markets`, `/api/health`

## Data model (MongoDB)
- `users` (email, password_hash, role, bankroll, paused_engines, kill_switch, live_mode, biometrics_enabled)
- `positions` (engine, canonical_id, market, side, entry/current/exit price, contracts, notional, status, pnl)
- `audit_logs` (action trail per user)
- `login_attempts` (brute-force protection)

## Engine scheduler
APScheduler runs `refresh_engines()` every 60s — ticks markets, regenerates signals,
caches them by stable minute-bucketed IDs. Daily rollover at 00:00 UTC resets `realized_pnl_today`.

## Mocking & live integration
- Polymarket / Kalshi quotes are simulated via `markets.py` (random walk).
- `PolymarketBroker` and `KalshiBroker` raise `NotImplementedError` — paper-only.
- Live integration entry points exist; require API key wiring + signing flows.

## Business enhancement ("Auto-Deploy")
One-tap **Master Allocator Auto-Deploy** opens up to 3 best-ranked positions in a single
gesture — converts the platform from passive analytics into active execution and
compounds engagement.
