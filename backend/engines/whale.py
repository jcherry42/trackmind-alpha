"""Whale engine — tracks elite wallets and detects consensus entries."""

from __future__ import annotations

import hashlib
import random
from typing import List

from markets import market_store


# A curated synthetic set of "elite" wallets with hit-rates and avg edge.
ELITE_WALLETS = [
    {"id": "0xA7f3...91Be", "label": "Sentinel-04", "hit_rate": 0.71, "avg_edge": 0.06, "size_bias": 1.2},
    {"id": "0xC2e1...4aD0", "label": "Helios",      "hit_rate": 0.66, "avg_edge": 0.05, "size_bias": 0.9},
    {"id": "0x55F8...77c3", "label": "Atlas-Prime", "hit_rate": 0.69, "avg_edge": 0.055, "size_bias": 1.1},
    {"id": "0xBb19...0eA2", "label": "Nightowl",    "hit_rate": 0.63, "avg_edge": 0.04, "size_bias": 0.8},
    {"id": "0x9F74...e125", "label": "Ironclad",    "hit_rate": 0.74, "avg_edge": 0.07, "size_bias": 1.4},
    {"id": "0x30Ad...82fF", "label": "Obsidian",    "hit_rate": 0.65, "avg_edge": 0.045, "size_bias": 1.0},
    {"id": "0x14bE...c901", "label": "Specter",     "hit_rate": 0.68, "avg_edge": 0.05, "size_bias": 0.95},
]


def _deterministic_consensus(canonical_id: str, tick_seed: float) -> List[dict]:
    """Pick a deterministic-but-time-varying subset of whales betting on this market."""
    h = int(hashlib.sha1(f"{canonical_id}:{int(tick_seed)}".encode()).hexdigest(), 16)
    rng = random.Random(h)
    n = rng.randint(0, 5)
    chosen = rng.sample(ELITE_WALLETS, k=min(n, len(ELITE_WALLETS)))
    side = "YES" if rng.random() > 0.5 else "NO"
    return [{**w, "side": side} for w in chosen]


def run() -> List[dict]:
    import time as _t

    signals: List[dict] = []
    bucket = int(_t.time() // 60)  # rotates every minute
    for q in market_store.all():
        if q.venue != "polymarket":
            continue
        cluster = _deterministic_consensus(q.canonical_id, bucket)
        if len(cluster) < 3:
            continue
        side_counts = {"YES": 0, "NO": 0}
        weight = 0.0
        for w in cluster:
            side_counts[w["side"]] += 1
            weight += w["hit_rate"] * w["size_bias"]
        consensus_side = "YES" if side_counts["YES"] >= side_counts["NO"] else "NO"
        agreement = max(side_counts.values()) / len(cluster)
        avg_edge = sum(w["avg_edge"] for w in cluster) / len(cluster)

        confidence = round(min(0.95, 0.45 + agreement * 0.3 + (weight / 10)), 3)
        expected_edge = round(avg_edge * agreement, 4)
        risk = round(max(0.01, 0.05 - avg_edge * 0.4), 4)
        liquidity = round(min(1.0, q.liquidity), 4)
        size_hint = round(max(0.03, min(0.16, confidence * expected_edge * 3 + 0.05)), 4)
        price = q.yes if consensus_side == "YES" else q.no

        signals.append({
            "engine": "whale",
            "canonical_id": q.canonical_id,
            "venue": q.venue,
            "market": q.title,
            "side": consensus_side,
            "price": price,
            "confidence": confidence,
            "expected_edge": expected_edge,
            "risk": risk,
            "liquidity": liquidity,
            "size_hint": size_hint,
            "rationale": f"{len(cluster)} elite wallets · {int(agreement*100)}% consensus · weight {weight:.1f}",
            "wallets": [{"id": w["id"], "label": w["label"], "side": w["side"], "hit_rate": w["hit_rate"]} for w in cluster],
        })
    signals.sort(key=lambda s: s["confidence"] * s["expected_edge"], reverse=True)
    return signals[:8]
