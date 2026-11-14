"""Quant engine — short-horizon persistence (momentum) on observed price history.

Works on whatever history the market store holds: real snapshots in live mode,
the demo random walk in mock mode. In live mode nothing synthetic is mixed in.

Edge is reported NET of the cost to trade: the half-spread (we buy at the ask,
fair value is the mid) and the venue's taker fee. A signal is only emitted if
that net edge is positive, so wide or illiquid books are filtered out.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

from fees import fee_per_contract
from markets import ask_for, execution_refs, half_spread, market_store, realized_vol

MIN_HISTORY = int(os.environ.get("QUANT_MIN_HISTORY", "12"))
MIN_NET_EDGE = float(os.environ.get("QUANT_MIN_NET_EDGE", "0.005"))
MAX_SIGNALS = int(os.environ.get("QUANT_MAX_SIGNALS", "8"))


def _persistence_score(history: List[float]) -> float:
    """Fraction of consecutive moves in the same direction over the recent window."""
    if len(history) < 6:
        return 0.5
    h = history[-30:]
    same = 0
    total = 0
    prev_dir = 0
    for i in range(1, len(h)):
        d = 1 if h[i] > h[i - 1] else -1 if h[i] < h[i - 1] else 0
        if d == 0:
            continue
        if prev_dir != 0:
            total += 1
            if d == prev_dir:
                same += 1
        prev_dir = d
    return same / total if total else 0.5


def run(now: Optional[float] = None) -> List[dict]:
    now = now or time.time()
    signals: List[dict] = []
    for q in market_store.all():
        if q.end_ts is not None and q.end_ts <= now:
            continue  # past close — no new positions
        hist = q.history
        if len(hist) < MIN_HISTORY:
            continue

        persistence = _persistence_score(hist)
        rv = realized_vol(hist)
        recent = sum(hist[-5:]) / 5
        window = hist[-30:]
        baseline = sum(window) / len(window)
        drift = recent - baseline
        side = "YES" if drift >= 0 else "NO"

        confidence = max(0.0, min(1.0, 0.4 + (persistence - 0.5) * 1.4 + abs(drift) * 6))
        gross_edge = max(0.0, abs(drift) * 0.6 - rv * 0.5)
        price = ask_for(q, side)
        cost = half_spread(q, side) + fee_per_contract(q.venue, price)
        expected_edge = round(gross_edge - cost, 4)
        if confidence < 0.45 or expected_edge < MIN_NET_EDGE:
            continue

        risk = round(max(0.005, rv * 1.2), 4)
        liquidity = round(min(1.0, q.liquidity), 4)
        size_hint = round(max(0.02, min(0.15, confidence * expected_edge * 4 + 0.03)), 4)
        signals.append({
            "engine": "quant",
            "canonical_id": q.canonical_id,
            "market": q.title,
            "side": side,
            "price": round(price, 4),
            "mid": round(q.yes if side == "YES" else q.no, 4),
            "confidence": round(confidence, 3),
            "expected_edge": expected_edge,
            "risk": risk,
            "liquidity": liquidity,
            "size_hint": size_hint,
            "rationale": (
                f"Persistence {persistence:.0%} · drift {drift:+.3f} · rv {rv:.3f} · "
                f"cost {cost:.3f} · {len(hist)} obs"
            ),
            **execution_refs(q),
        })
    signals.sort(key=lambda s: s["confidence"] * s["expected_edge"], reverse=True)
    return signals[:MAX_SIGNALS]
