# TRACKMIND

> Institutional-grade prediction market intelligence + execution platform.
> Hedge fund operating system, consumer-grade Expo mobile app.

[![CI](https://img.shields.io/badge/CI-passing-brightgreen)]() [![License](https://img.shields.io/badge/license-Proprietary-blue)]()

## Stack
- **Backend:** Python 3.11 · FastAPI · MongoDB (Motor) · APScheduler · bcrypt + JWT
- **Mobile:** Expo SDK 54 · React Native 0.81 · expo-router · TypeScript
- **AI:** Claude Sonnet 4.5 (Emergent Universal LLM Key) for the Alpha engine
- **Infra:** docker-compose · Dockerfiles · GitHub Actions

## Repository layout
```
trackmind/
├── backend/                  # FastAPI app
│   ├── server.py             # main entrypoint, all routes
│   ├── markets.py            # market store (demo catalog or live snapshot)
│   ├── live_market.py        # Polymarket + Kalshi ingest + resolution lookups
│   ├── pairs.py              # verified cross-venue pairs + review candidates
│   ├── fees.py               # venue fee models
│   ├── paper_settlement.py   # mark-to-market + honest paper settlement
│   ├── allocator.py          # master allocator + risk constraints
│   ├── broker.py             # Paper/Polymarket/Kalshi adapters
│   ├── engines/              # alpha, quant, whale, micro
│   └── tests/                # pytest suite
├── frontend/                 # Expo React Native app
│   ├── app/                  # file-based routes (expo-router)
│   ├── components/           # Card, Button, Tag, ProgressBar, Sparkline
│   └── lib/                  # api, auth, theme, format
├── infra/
│   ├── docker-compose.yml
│   ├── backend.Dockerfile
│   └── github-actions.yml    # template
├── docs/
│   └── ARCHITECTURE.md
└── memory/
    ├── PRD.md
    └── test_credentials.md
```

## Getting started — local

### Backend
```bash
cd backend
pip install -r requirements.txt
# .env defines MONGO_URL, DB_NAME, JWT_SECRET, ADMIN_EMAIL, ADMIN_PASSWORD, EMERGENT_LLM_KEY
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```
On first launch the backend seeds an admin user and starts the engine scheduler.

### Frontend
```bash
cd frontend
yarn install
yarn start  # opens Expo dev server (use Expo Go on your phone, or web preview)
```
The mobile app reads `EXPO_PUBLIC_BACKEND_URL` from `frontend/.env`.

## Demo credentials
- **Admin:** `admin@trackmind.io` / `Admin123!`
- Each user starts with **$10,000 paper bankroll**.

## Data modes
| Mode | How to enable | What it is |
| ---- | ------------- | ---------- |
| **Demo (mock)** | default | 12 sample markets on both venues, prices move by a random walk. Good for UI work. Nothing it shows says anything about real edge. The dashboard shows a red **DEMO DATA** chip. |
| **Live data** | `LIVE_INGEST_ENABLED=true` | Real Polymarket (Gamma) + Kalshi (v2) snapshots every `LIVE_REFRESH_SEC` (15s) with top-of-book bid/ask and the venue IDs needed to route orders. Nothing synthetic is ever mixed in. |

Live data and live *trading* are separate switches: you can paper-trade against live data, and paper
positions then settle at the market's **real** resolution, so paper P&L is an honest forward test.

## Engines
| Engine | Strategy | Status |
| ------ | -------- | ------ |
| **Alpha** | Narrative/catalyst score nudges the market's own price (max ±`ALPHA_MAX_SHIFT`) | **Demo only.** The news feed is synthetic, so Alpha switches itself off on live data until a real news source is connected (`ALPHA_ALLOW_SYNTHETIC=true` overrides for testing). |
| **Quant** | Short-horizon persistence/momentum on observed history | Works on live snapshots. Edge is net of half-spread + venue fees. |
| **Whale** | Consensus buys by tracked Polymarket wallets (on-chain Data API) | Real data. Edge formula is an uncalibrated prior (`edge_calibrated: false`) until wallet hit rates are measured. |
| **Micro** | Cross-venue arb: asks of complementary legs + both venues' fees < $1.00 | Only scans **human-verified** pairs from `backend/market_pairs.json` (see `market_pairs.example.json`). Title-similarity matches are listed at `GET /api/markets/pairs` for review and are never traded. Paper only until two-venue execution exists. |

All signals are priced at the **ask** (what you'd actually pay) and carry `polymarket_token_ids` /
`polymarket_condition_id` / `kalshi_ticker`, so a live broker can route them.

The **master allocator** ranks all signals by `confidence × edge × liquidity / risk`,
applies risk constraints, and emits sized orders for execution.

## API endpoints

### Auth
- `POST /api/auth/register` — `{email, password, name?}`
- `POST /api/auth/login` — `{email, password}`
- `POST /api/auth/refresh` — refresh access token
- `POST /api/auth/logout`
- `GET /api/auth/me`

### Trading
- `GET /api/dashboard`
- `GET /api/signals[?engine=alpha|quant|whale|micro]`
- `GET /api/signals/micro`
- `GET /api/positions`
- `POST /api/trade/approve` — `{signal_id, notional?}`
- `POST /api/trade/auto-deploy` — top-N orders from allocator
- `GET /api/analytics`

### Controls
- `POST /api/controls/update` — `{paused_engines?, kill_switch?, live_mode?, biometrics_enabled?}`
- `POST /api/controls/reset` — wipe positions, restore bankroll

## Risk system
| Constraint | Default |
| ---------- | ------- |
| Max single trade | 15% of bankroll |
| Max correlated exposure | 35% per market |
| Min cash buffer | 20% |
| Max daily drawdown | 5% |

Above 60% drawdown ratio the allocator auto-switches into **defensive mode** (60% size cut).

## Execution
One pipeline (`_execute_signal` in `server.py`) serves `/trade/approve`, `/trade/auto-deploy` and
`/whales/mirror`, so every route enforces the same guards: kill switch, daily-loss circuit, paused
engines, per-trade cap, the live daily $ cap (`MAX_DAILY_LIVE_NOTIONAL`, default $50), and refusal to
send live orders for demo markets or for arb signals.

- `PaperBroker` fills at the signal's ask (+ small slippage on thin books) with venue fee models (`fees.py`).
- `PolymarketBroker` signs CLOB limit orders via `py-clob-client`; `KalshiBroker` sends RSA-PSS-signed
  v2 orders (`yes_price` for YES, `no_price` for NO). Positions record `order_status`
  (`filled` | `submitted` for a resting order).
- Live positions are settled only by the venue watchers (`exchange_settlement.py`, `kalshi_ws.py`).

Before real money: verify fee schedules in `fees.py`, check whether Polymarket now requires
its v2 CLOB client, and add order-status polling + cancel (see *Known gaps*).

## Settlement
| Position | Settles when | Outcome |
| -------- | ------------ | ------- |
| Paper, live data | Market actually resolves (polled every `PAPER_RESOLUTION_POLL_SEC`) | Real outcome from Gamma / Kalshi |
| Paper, demo data | Time-based hazard (mean `PAPER_MOCK_MEAN_LIFE_SEC`, 30 min) | Drawn from the market's own price, so there's no built-in edge |
| Live | Venue watcher sees the redemption / fill | Real payout |

Bankroll accounting: the stake is debited at entry; at close the bankroll is credited
`payout × contracts − fees` and realized P&L is that minus the stake.

## Known gaps (next phases)
- Real news/data feed for Alpha; calibration (Brier/log-loss) per engine on resolved markets.
- Whale edge from measured wallet hit rates; backtesting on historical resolutions.
- Order lifecycle: poll/cancel resting orders, partial fills, kill switch cancels open orders.
- Two-venue arb execution with legging protection.
- Live bankroll reconciled against real venue balances (today live trades debit the paper bankroll).
- Order-book depth (not just top of book) for sizing.

## Docker
```bash
docker compose -f infra/docker-compose.yml up --build
```
Spins up `mongo`, `redis`, `api`, and `nginx` (reverse proxy) on a shared network.

## Tests
```bash
cd backend && pytest -q tests/
```

## License
Proprietary — TRACKMIND © 2026
