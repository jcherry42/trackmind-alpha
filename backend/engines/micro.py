"""Micro engine — cross-venue arbitrage on VERIFIED market pairs.

For a pair of contracts that resolve on the same event, buying complementary
sides on two venues pays exactly $1.00 per contract pair at resolution. The
trade is an arbitrage when the cost of the two ASKS plus both venues' fees is
below $1.00.

Rules that keep this honest:
  * Only pairs from ``market_store.pairs()`` are considered. In live mode those
    come from the human-reviewed ``market_pairs.json``; fuzzy title matches are
    never traded (see ``pairs.py``).
  * Prices are top-of-book asks, never mids. A live quote with no ask is skipped.
  * Fees use each venue's fee model (``fees.py``).
  * Stale quotes (older than MICRO_MAX_QUOTE_AGE_SEC) are skipped.
  * Two-venue execution isn't built yet, so signals carry
    ``executable_live=False``; ``/trade/approve`` refuses them in live mode.
    Paper trading works.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional, Tuple

from fees import fee_per_contract
from markets import Quote, ask_for, execution_refs, market_store

MIN_EDGE = float(os.environ.get("MICRO_MIN_EDGE", "0.01"))  # $ per $1 contract pair, after fees
MAX_QUOTE_AGE_SEC = float(os.environ.get("MICRO_MAX_QUOTE_AGE_SEC", "60"))
MAX_SIGNALS = int(os.environ.get("MICRO_MAX_SIGNALS", "6"))


def _leg_price(q: Quote, side: str) -> Optional[float]:
    """Executable ask for a leg; None if a live quote has no book on that side."""
    if q.source == "live":
        ask = q.yes_ask if side == "YES" else q.no_ask
        return ask
    return ask_for(q, side)


def _combos(p: Quote, k: Quote, polarity: str) -> List[Tuple[Tuple[Quote, str], Tuple[Quote, str]]]:
    """Leg combinations that together cover both outcomes exactly once."""
    if polarity == "inverted":
        # Kalshi YES == Polymarket NO ⇒ YES+YES and NO+NO are complementary.
        return [((p, "YES"), (k, "YES")), ((p, "NO"), (k, "NO"))]
    return [((p, "YES"), (k, "NO")), ((k, "YES"), (p, "NO"))]


def run(now: Optional[float] = None) -> List[dict]:
    now = now or time.time()
    signals: List[dict] = []
    for p, k, pair_id, polarity in market_store.pairs():
        if p.source == "live" and (now - p.updated_at > MAX_QUOTE_AGE_SEC or now - k.updated_at > MAX_QUOTE_AGE_SEC):
            continue
        best = None
        for (qa, sa), (qb, sb) in _combos(p, k, polarity):
            pa, pb = _leg_price(qa, sa), _leg_price(qb, sb)
            if pa is None or pb is None:
                continue
            fees = fee_per_contract(qa.venue, pa) + fee_per_contract(qb.venue, pb)
            cost = pa + pb
            edge = 1.0 - cost - fees
            if best is None or edge > best["edge"]:
                best = {"a": (qa, sa, pa), "b": (qb, sb, pb), "cost": cost, "fees": fees, "edge": edge}
        if not best or best["edge"] < MIN_EDGE:
            continue

        (qa, sa, pa), (qb, sb, pb) = best["a"], best["b"]
        edge = best["edge"]
        liquidity = round(min(1.0, qa.liquidity, qb.liquidity), 4)
        signals.append({
            "engine": "micro",
            "canonical_id": pair_id,
            "venue": "cross",
            "market": f"{qa.title} / {qb.title}",
            "side": "ARB",
            "price": round(best["cost"], 4),
            "confidence": round(min(0.97, 0.7 + edge * 8), 3),
            "expected_edge": round(edge, 4),
            # Residual risk is settlement / rules mismatch + legging risk, not price.
            "risk": 0.01,
            "liquidity": liquidity,
            "size_hint": round(max(0.02, min(0.15, edge * 5 + 0.02)), 4),
            "rationale": (
                f"{sa}@{qa.venue} {pa:.3f} + {sb}@{qb.venue} {pb:.3f} + fees {best['fees']:.3f} "
                f"= {best['cost'] + best['fees']:.3f} < 1.000"
            ),
            "executable_live": False,
            "fair_prob": None,  # an arb has no directional forecast; scored on realized payout
            "data_source": p.source,
            "pair_polarity": polarity,
            "legs": [
                {"side": sa, "price": round(pa, 4), "market": qa.title, "canonical_id": qa.canonical_id, **execution_refs(qa)},
                {"side": sb, "price": round(pb, 4), "market": qb.title, "canonical_id": qb.canonical_id, **execution_refs(qb)},
            ],
            "fees_per_contract": round(best["fees"], 4),
        })
    signals.sort(key=lambda s: s["expected_edge"], reverse=True)
    return signals[:MAX_SIGNALS]
