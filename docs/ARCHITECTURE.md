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
                                                     │ _execute_    │
                                                     │ signal →     │
                                                     │ Paper / Poly │
                                                     │ / Kalshi     │
                                                     └──────────────┘
```

## Engine contract
Every engine emits dicts of the shape:
```jsonc
{
  "engine": "quant",
  "canonical_id": "btc_1h_above",
  "market": "BTC 1H ABOVE 100K",
  "side": "YES",
  "price": 0.52,              // executable ASK for `side`
  "mid": 0.515,
  "polymarket_token_ids": ["…yes", "…no"],
  "polymarket_condition_id": "0x…",
  "kalshi_ticker": null,
  "data_source": "live",       // or "mock"
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

## Settlement
- **Live positions** settle only via venue watchers (Polygon CTF redemptions, Kalshi fills).
- **Paper positions on live data** settle when the real market resolves (`paper_resolution_loop`
  polls Gamma `/markets?condition_ids=` and Kalshi `/markets/{ticker}`).
- **Paper positions on demo data** resolve with a time-based hazard; the outcome is drawn from the
  market's own current price (no edge baked in).
- Bankroll: stake debited at entry; `payout × contracts − fees` credited at close.

Every request marks open positions to market at the **bid** (liquidation value), net of fees.
