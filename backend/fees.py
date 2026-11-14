"""Venue fee models (per contract, in dollars, for a $1-payout binary contract).

Kalshi: taker fee = 0.07 × P × (1 − P) per contract, rounded up to the cent per
order. Polymarket: most markets are fee-free; some carry taker fees. The rate is
configurable via POLYMARKET_TAKER_FEE_BPS (default 0) so it can be tuned per
deployment without code changes. Both rates should be re-checked against each
venue's published schedule before going live.
"""
from __future__ import annotations

import math
import os

KALSHI_TAKER_COEFF = float(os.environ.get("KALSHI_TAKER_FEE_COEFF", "0.07"))


def _poly_bps() -> float:
    try:
        return float(os.environ.get("POLYMARKET_TAKER_FEE_BPS", "0"))
    except ValueError:
        return 0.0


def fee_per_contract(venue: str, price: float) -> float:
    p = max(0.0, min(1.0, float(price)))
    v = (venue or "").lower()
    if v == "kalshi":
        return KALSHI_TAKER_COEFF * p * (1.0 - p)
    if v == "polymarket":
        return p * _poly_bps() / 10_000.0
    return 0.0


def order_fee(venue: str, price: float, contracts: float) -> float:
    """Total fee for an order. Kalshi rounds up to the next cent per order."""
    raw = fee_per_contract(venue, price) * max(0.0, contracts)
    if (venue or "").lower() == "kalshi":
        return math.ceil(raw * 100 - 1e-9) / 100.0
    return round(raw, 4)
