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
│   ├── markets.py            # mock prediction-market store
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

## Engines
| Engine | Strategy | Output |
| ------ | -------- | ------ |
| **Alpha** | LLM-scored narrative / catalysts vs implied price | YES/NO with edge gap |
| **Quant** | Markov persistence + drift on price history | YES/NO short-window edge |
| **Whale** | Elite-wallet cluster consensus | YES/NO with weight + agreement |
| **Micro** | Cross-venue YES + NO < $1.00 spread | ARB with legs |

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

## Live execution
`PolymarketBroker` and `KalshiBroker` are stubs — they raise `NotImplementedError`.
Wire up the venue auth/signing flows in `backend/broker.py`, then flip `live_mode` per user.

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
