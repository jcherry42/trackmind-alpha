"""Quant engine — Markov-style persistence + mean-reversion edge detection."""

from __future__ import annotations

from typing import List

from markets import market_store, realized_vol


def _persistence_score(history: List[float]) -> float:
    """Fraction of consecutive moves in the same direction over recent window."""
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


def run() -> List[dict]:
    signals: List[dict] = []
    for q in market_store.all():
        if q.venue != "polymarket":
            # Quant engine deduplicates across venues by using polymarket as canonical view
            continue
        hist = q.history
        if len(hist) < 8:
            continue

        persistence = _persistence_score(hist)
        rv = realized_vol(hist)
        recent = sum(hist[-5:]) / 5
        baseline = sum(hist[-30:]) / max(1, len(hist[-30:]))
        drift = recent - baseline

        # Confidence rises when persistence is strong AND drift is meaningful.
        confidence = max(0.0, min(1.0, 0.4 + (persistence - 0.5) * 1.4 + abs(drift) * 6))
        # Expected edge: small persistent edge, dampened by noise.
        expected_edge = round(max(0.0, abs(drift) * 0.6 - rv * 0.5), 4)
        risk = round(max(0.005, rv * 1.2), 4)
        liquidity = round(min(1.0, q.liquidity), 4)
        size_hint = round(max(0.02, min(0.18, confidence * expected_edge * 4 + 0.04)), 4)
        side = "YES" if drift >= 0 else "NO"
        price = q.yes if side == "YES" else q.no

        if confidence < 0.45 or expected_edge < 0.005:
            continue

        signals.append({
            "engine": "quant",
            "canonical_id": q.canonical_id,
            "venue": q.venue,
            "market": q.title,
            "side": side,
            "price": price,
            "confidence": round(confidence, 3),
            "expected_edge": expected_edge,
            "risk": risk,
            "liquidity": liquidity,
            "size_hint": size_hint,
            "rationale": f"Persistence {persistence:.0%} · drift {drift:+.3f} · rv {rv:.3f}",
        })
    signals.sort(key=lambda s: s["confidence"] * s["expected_edge"], reverse=True)
    return signals[:8]
