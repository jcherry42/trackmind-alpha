"""Paper-position valuation and settlement.

Replaces the old "settle 7% of positions per request with a win chance tilted
by the signal's own claimed edge" logic, which guaranteed paper profits and made
every analytics number meaningless.

Rules now:
  * Only PAPER positions are ever settled here. Live positions are settled by the
    venue watchers (``exchange_settlement.py`` for Polygon, ``kalshi_ws.py``).
  * Live market data → a paper position settles when the REAL market resolves,
    at the real outcome. Paper P&L is then an honest forward test of the engines.
  * Mock market data → markets never resolve, so we simulate a resolution with a
    time-based hazard and draw the outcome from the market's own current price
    (unbiased). With no informational edge, expected P&L is negative after
    spread + fees — which is the truth about the demo engines.

Everything here is pure (no DB) so it can be unit tested.
"""
from __future__ import annotations

import math
import os
import random
from typing import Callable, Dict, List, Optional

MOCK_MEAN_LIFE_SEC = float(os.environ.get("PAPER_MOCK_MEAN_LIFE_SEC", "1800"))

Outcome = str  # "YES" | "NO" | "VOID"
QuoteLookup = Callable[[str, str], Optional[object]]  # (venue, canonical_id) -> Quote | None


def is_paper(pos: dict) -> bool:
    return (pos.get("broker") or "paper") == "paper"


def legs_of(pos: dict) -> List[dict]:
    """Normalise a position into legs: [{venue, canonical_id, side, ...}]."""
    legs = pos.get("legs")
    if isinstance(legs, list) and legs:
        return legs
    if pos.get("side") == "ARB":
        # Legacy arb positions (pre-legs schema) came from the mock catalog, where the
        # same canonical_id is listed on both venues: YES on one + NO on the other.
        cid = pos.get("canonical_id")
        return [
            {"venue": "polymarket", "canonical_id": cid, "side": "YES"},
            {"venue": "kalshi", "canonical_id": cid, "side": "NO"},
        ]
    return [{
        "venue": pos.get("venue", "polymarket"),
        "canonical_id": pos.get("canonical_id"),
        "side": pos.get("side", "YES"),
        "polymarket_condition_id": pos.get("polymarket_condition_id"),
        "kalshi_ticker": pos.get("kalshi_ticker"),
    }]


def _bid(q, side: str) -> float:
    if side == "YES":
        return q.yes_bid if getattr(q, "yes_bid", None) is not None else q.yes
    return q.no_bid if getattr(q, "no_bid", None) is not None else q.no


def mark_value(pos: dict, lookup: QuoteLookup) -> Optional[float]:
    """Liquidation value per contract (sum of leg bids). None if any leg is unquoted."""
    total = 0.0
    for leg in legs_of(pos):
        q = lookup(leg.get("venue"), leg.get("canonical_id"))
        if q is None:
            return None
        total += _bid(q, leg.get("side", "YES"))
    return total


def leg_payout(leg: dict, outcome: Outcome, entry_leg_price: Optional[float] = None) -> float:
    """$ payout per contract for one leg given its market's outcome."""
    if outcome == "VOID":
        if (leg.get("venue") or "") == "kalshi" and entry_leg_price is not None:
            return float(entry_leg_price)  # Kalshi voids refund cost
        return 0.5                         # Polymarket 50/50 resolution
    return 1.0 if leg.get("side") == outcome else 0.0


def realized_pnl(pos: dict, outcomes: Dict[int, Outcome]) -> tuple[float, float]:
    """(payout_per_contract, realized_pnl) given an outcome for every leg index."""
    payout = 0.0
    for i, leg in enumerate(legs_of(pos)):
        payout += leg_payout(leg, outcomes[i], leg.get("price"))
    contracts = float(pos.get("contracts", 0.0))
    pnl = (payout - float(pos["entry_price"])) * contracts - float(pos.get("fees", 0.0))
    return round(payout, 4), round(pnl, 2)


def mock_outcomes(
    pos: dict,
    lookup: QuoteLookup,
    dt_sec: float,
    rng: random.Random | None = None,
    mean_life_sec: float = MOCK_MEAN_LIFE_SEC,
) -> Optional[Dict[int, Outcome]]:
    """Maybe resolve a mock-data position. Returns outcomes per leg, or None.

    Probability of resolving in ``dt_sec`` is 1 − exp(−dt/mean_life) — independent
    of how often anyone polls. The event outcome is drawn once (legs of an arb
    are the same event) with P(YES) = current mid of the first leg's market.
    """
    rng = rng or random
    if dt_sec <= 0:
        return None
    if rng.random() >= 1.0 - math.exp(-dt_sec / max(1.0, mean_life_sec)):
        return None
    legs = legs_of(pos)
    # The event is defined in terms of a reference leg (Polymarket if present).
    ref = next((lg for lg in legs if lg.get("venue") == "polymarket"), legs[0])
    q = lookup(ref.get("venue"), ref.get("canonical_id"))
    p_yes = q.yes if q is not None else 0.5
    ref_yes = rng.random() < p_yes
    inverted_pair = pos.get("pair_polarity") == "inverted"
    out: Dict[int, Outcome] = {}
    for i, leg in enumerate(legs):
        flip = inverted_pair and leg.get("venue") != ref.get("venue")
        yes = (not ref_yes) if flip else ref_yes
        out[i] = "YES" if yes else "NO"
    return out
