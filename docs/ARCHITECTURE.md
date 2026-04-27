# Architecture

```
                            ┌──────────────────────┐
                            │  Expo Mobile App     │
                            │  (iOS + Android)     │
                            │  expo-router         │
                            └────────┬─────────────┘
                                     │ HTTPS  (Bearer JWT)
                                     ▼
                            ┌──────────────────────┐
                            │  FastAPI Backend     │
                            │  /api/*              │
                            └────┬───────┬─────────┘
                                 │       │
              ┌──────────────────┘       └──────────────────┐
              ▼                                              ▼
       ┌─────────────┐                                ┌─────────────┐
       │  MongoDB    │                                │ Engine Loop │
       │  users,     │◀── reads/writes               │ APScheduler│
       │  positions, │                                │  60s tick   │
       │  audit_logs│                                ├─────────────┤
       └─────────────┘                                │ Alpha (LLM) │
                                                      │ Quant       │
                                                      │ Whale       │
                                                      │ Micro       │
                                                      └──────┬──────┘
                                                             ▼
                                                   ┌──────────────────┐
                                                   │ Master Allocator │
                                                   │ + Risk system    │
                                                   └─────────┬────────┘
                                                             ▼
                                                     ┌──────────────┐
                                                     │ Paper Broker │
                                                     │ (Polymarket/ │
                                                     │  Kalshi —    │
                                                     │  stubs)      │
                                                     └──────────────┘
```

## Engine contract
Every engine emits dicts of the shape:
```json
{
  "engine": "quant",
  "canonical_id": "btc_1h_above",
  "market": "BTC 1H ABOVE 100K",
  "side": "YES",
  "price": 0.52,
  "confidence": 0.88,
  "expected_edge": 0.05,
  "risk": 0.02,
  "liquidity": 0.80,
  "size_hint": 0.12,
  "rationale": "..."
}
```

## Allocator scoring
```
score = confidence * expected_edge * (0.5 + 0.5 * liquidity) / max(0.005, risk)
```
Sized via `min(size_hint, 0.15) * sizing_dampener`, where the dampener tightens with drawdown ratio.

## Auth flow
1. Register / login → server issues `access` (24h) + `refresh` (30d) JWTs.
2. Mobile stores them in AsyncStorage; sends `Authorization: Bearer <access>`.
3. On 401, the API client transparently retries via `/api/auth/refresh`.

## Settlement (paper)
Each `/dashboard` or `/positions` request marks-to-market all open positions and randomly
settles ~7% of them per tick — chance-of-win is `0.5 + 0.25 * expected_edge_norm`.
Realized PnL accrues to bankroll and `realized_pnl_today`.
