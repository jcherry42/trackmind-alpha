"""Micro engine — cross-venue YES/NO arbitrage scanner."""

from __future__ import annotations

from typing import List

from markets import market_store


FEES_PER_LEG = 0.005  # 50bps per leg (configurable in real adapter)


def run() -> List[dict]:
    signals: List[dict] = []
    for cid in market_store.catalog_ids():
        legs = market_store.by_canonical(cid)
        if len(legs) < 2:
            continue
        # Find best YES on one venue + best NO on another.
        best: dict | None = None
        for a in legs:
            for b in legs:
                if a.venue == b.venue:
                    continue
                cost = a.yes + b.no  # buy YES on A + buy NO on B
                fees = FEES_PER_LEG * 2
                edge = 1.0 - cost - fees  # payout is exactly 1.00 if equivalent markets
                if best is None or edge > best["edge"]:
                    best = {
                        "yes_venue": a.venue,
                        "yes_price": a.yes,
                        "yes_market": a.title,
                        "no_venue": b.venue,
                        "no_price": b.no,
                        "no_market": b.title,
                        "edge": edge,
                        "cost": cost,
                        "liquidity": min(a.liquidity, b.liquidity),
                    }
        if not best:
            continue

        edge = best["edge"]
        if edge <= 0.005:
            continue

        confidence = round(min(0.97, 0.7 + edge * 8), 3)
        expected_edge = round(max(0.0, edge), 4)
        risk = 0.01  # near-zero given equivalent payoff (modulo settlement risk)
        liquidity = round(min(1.0, best["liquidity"]), 4)
        size_hint = round(max(0.05, min(0.20, edge * 6 + 0.05)), 4)

        signals.append({
            "engine": "micro",
            "canonical_id": cid,
            "venue": "cross",
            "market": f"{best['yes_market']} / {best['no_market']}",
            "side": "ARB",
            "price": round(best["cost"], 4),
            "confidence": confidence,
            "expected_edge": expected_edge,
            "risk": risk,
            "liquidity": liquidity,
            "size_hint": size_hint,
            "rationale": f"YES@{best['yes_venue']} {best['yes_price']:.3f} + NO@{best['no_venue']} {best['no_price']:.3f} = {best['cost']:.3f}",
            "legs": {
                "yes": {"venue": best["yes_venue"], "price": best["yes_price"], "market": best["yes_market"]},
                "no":  {"venue": best["no_venue"],  "price": best["no_price"],  "market": best["no_market"]},
                "fees": FEES_PER_LEG * 2,
            },
        })
    signals.sort(key=lambda s: s["expected_edge"], reverse=True)
    return signals[:6]
