"""Whale engine — generates signals from REAL on-chain Polymarket whale activity.

Replaces the prior synthetic ELITE_WALLETS list. Now driven by:
  - `db.tracked_wallets` — curated set of wallets being monitored (8 admin-seeded
    + any per-user additions).
  - `db.whale_entries` — immutable log of BUY trades observed via Polymarket's
    public Data API (`/activity?user=<addr>`). See `whale_tracker.py`.

Signal generation:
  1. Read `whale_entries` from the last WHALE_LOOKBACK_MIN minutes (default 90).
  2. Group by canonical_id (skipping entries we couldn't map to a live market).
  3. For each group, count distinct wallets + sum notional. If
     `distinct_wallets >= WHALE_MIN_CONSENSUS` (default 2), emit a signal.
  4. Confidence rises with: count of distinct wallets, total notional,
     recency, and majority-side agreement.

This module reads `db` synchronously via a closure injected by the engine
refresher in server.py (`whale.run_async(db)`). Falls back to an empty list if
db is None (e.g., during unit tests).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fees import fee_per_contract
import whale_records
from markets import ask_for, execution_refs, market_store, mid_for

logger = logging.getLogger("trackmind.engine.whale")

# How far back to look when computing consensus.
WHALE_LOOKBACK_MIN = int(os.environ.get("WHALE_LOOKBACK_MIN", "90"))
# Min distinct wallets needed to fire a signal.
WHALE_MIN_CONSENSUS = int(os.environ.get("WHALE_MIN_CONSENSUS", "2"))
# Min total USDC notional across the consensus to fire a signal.
WHALE_MIN_NOTIONAL = float(os.environ.get("WHALE_MIN_NOTIONAL", "2500"))
# Hard cap on signals returned (top-K by confidence × edge).
WHALE_MAX_SIGNALS = int(os.environ.get("WHALE_MAX_SIGNALS", "8"))
# Resolved trades (summed across the consensus wallets) needed before the edge
# is taken from measured records instead of the uncalibrated prior.
WHALE_MIN_RECORD = int(os.environ.get("WHALE_MIN_RECORD", "30"))
# If true, emit no whale signals until the wallets have measured records.
WHALE_REQUIRE_CALIBRATION = os.environ.get("WHALE_REQUIRE_CALIBRATION", "false").lower() in {"1", "true", "yes"}


def _confidence(distinct_wallets: int, notional_usdc: float, agreement: float,
                recency_sec: float) -> float:
    """Weighted confidence in [0, 0.95].

    distinct_wallets: more wallets ⇒ more confidence (saturates at 5).
    notional_usdc:    larger size ⇒ smarter money. Log-scaled.
    agreement:        share of consensus on the same side ∈ [0.5, 1.0].
    recency_sec:      newer entries score higher (linear decay over lookback).
    """
    import math
    w_count = min(1.0, distinct_wallets / 5.0)  # 5+ wallets = max
    w_size = min(1.0, math.log10(max(notional_usdc, 100)) / 5.0)  # $100k = max
    w_agree = max(0.0, (agreement - 0.5) * 2.0)  # 50% = 0, 100% = 1
    lookback = WHALE_LOOKBACK_MIN * 60.0
    w_recent = max(0.0, 1.0 - (recency_sec / lookback))
    raw = 0.35 * w_count + 0.30 * w_size + 0.20 * w_agree + 0.15 * w_recent
    return round(min(0.95, 0.40 + raw * 0.55), 3)


def _outcome_to_side(outcome: Optional[str], outcome_index: Optional[int]) -> str:
    """Map Polymarket's outcome label to YES/NO in our schema.

    Polymarket markets always have exactly 2 outcomes; index 0 = YES side.
    """
    if outcome_index is not None:
        return "YES" if int(outcome_index) == 0 else "NO"
    o = (outcome or "").lower()
    if o in ("yes", "true"):
        return "YES"
    if o in ("no", "false"):
        return "NO"
    return "YES"  # default — outcome_index is reliable so this rarely hits


async def run_async(db: Any = None) -> List[dict]:
    """Compute whale-consensus signals from the last WHALE_LOOKBACK_MIN minutes."""
    if db is None:
        # No db wired (unit test path) — return nothing rather than synthetic noise.
        return []

    since = datetime.now(timezone.utc) - timedelta(minutes=WHALE_LOOKBACK_MIN)
    # Group on the Polymarket conditionId (stable), not our canonical_id, so entries
    # ingested before their market entered the live store still count.
    cursor = db.whale_entries.find(
        {"observed_at": {"$gte": since}, "condition_id": {"$nin": [None, ""]}}
    ).sort("ts", -1)

    # Aggregate per canonical_id (in-memory — recent entries volume is bounded).
    groups: Dict[str, Dict[str, Any]] = {}
    async for e in cursor:
        cid = (e.get("condition_id") or "").lower()
        if not cid:
            continue
        side = _outcome_to_side(e.get("outcome"), e.get("outcome_index"))
        g = groups.setdefault(cid, {
            "wallets": set(),
            "wallet_meta": [],
            "side_counts": {"YES": 0, "NO": 0},
            "notional": 0.0,
            "ts_newest": 0.0,
            "entries": [],
        })
        wallet = (e.get("wallet_address") or "").lower()
        if wallet:
            g["wallets"].add(wallet)
        g["wallet_meta"].append({
            "address": wallet,
            "label": e.get("wallet_label"),
            "side": side,
            "usdc_size": float(e.get("usdc_size") or 0.0),
            "ts": float(e.get("ts") or 0.0),
        })
        g["side_counts"][side] = g["side_counts"].get(side, 0) + 1
        g["notional"] += float(e.get("usdc_size") or 0.0)
        g["ts_newest"] = max(g["ts_newest"], float(e.get("ts") or 0.0))
        g["entries"].append({
            "transaction_hash": e.get("transaction_hash"),
            "price": float(e.get("price") or 0.0),
            "side": side,
            "usdc_size": float(e.get("usdc_size") or 0.0),
            "wallet": wallet,
            "label": e.get("wallet_label"),
        })

    records = await whale_records.load_records(db)
    signals: List[dict] = []
    now = time.time()
    for cid, g in groups.items():
        n_wallets = len(g["wallets"])
        if n_wallets < WHALE_MIN_CONSENSUS:
            continue
        if g["notional"] < WHALE_MIN_NOTIONAL:
            continue
        total = max(1, g["side_counts"]["YES"] + g["side_counts"]["NO"])
        majority_side = "YES" if g["side_counts"]["YES"] >= g["side_counts"]["NO"] else "NO"
        agreement = g["side_counts"][majority_side] / total
        recency_sec = max(0.0, now - g["ts_newest"])

        # Pull live market for price + venue context. Skip if not (or no longer) live.
        quote = market_store.find_by_condition_id(cid)
        if quote is None:
            continue
        price = ask_for(quote, majority_side)
        fee = fee_per_contract(quote.venue, price)
        confidence = _confidence(n_wallets, g["notional"], agreement, recency_sec)

        # Fair value = what the majority paid on average + their measured edge
        # (each wallet's record shrunk toward 0 by sample size, weighted by size).
        side_meta = [w for w in g["wallet_meta"] if w["side"] == majority_side]
        side_entries = [en for en in g["entries"] if en["side"] == majority_side and 0 < en["price"] < 1]
        notional_side = sum(en["usdc_size"] for en in side_entries) or 0.0
        whale_avg_price = (
            sum(en["price"] * en["usdc_size"] for en in side_entries) / notional_side
            if notional_side > 0 else mid_for(quote, majority_side)
        )
        rec_weight, rec_edge_sum, record_n = 0.0, 0.0, 0
        seen_wallets: set = set()
        for w in side_meta:
            rec = records.get(w["address"])
            if not rec:
                continue
            wt = max(1.0, w["usdc_size"])
            rec_edge_sum += wt * whale_records.shrunk_edge(rec["n"], rec["edge"])
            rec_weight += wt
            if w["address"] not in seen_wallets:
                record_n += rec["n"]
                seen_wallets.add(w["address"])
        calibrated = record_n >= WHALE_MIN_RECORD and rec_weight > 0
        if calibrated:
            group_edge = rec_edge_sum / rec_weight
        else:
            if WHALE_REQUIRE_CALIBRATION:
                continue
            # Uncalibrated prior (no measured record yet): a modest assumed edge.
            group_edge = (0.04 + 0.02 * agreement + 0.01 * min(2.0, n_wallets / 2.0)) * (0.5 + agreement / 2.0)
        fair_prob = min(0.99, max(0.01, whale_avg_price + group_edge))
        expected_edge = round(fair_prob - price - fee, 4)
        if expected_edge <= 0:
            continue
        risk = round(max(0.015, 0.05 - expected_edge * 0.6), 4)
        liquidity = round(min(1.0, quote.liquidity), 4)
        size_hint = round(max(0.03, min(0.15, confidence * expected_edge * 3 + 0.05)), 4)

        # Most-recent + largest wallets surface first in the rationale list.
        top_meta = sorted(g["wallet_meta"], key=lambda w: w["usdc_size"], reverse=True)[:5]
        rationale_wallets = [
            {"address": w["address"], "label": w["label"], "side": w["side"],
             "usdc_size": round(w["usdc_size"], 0)} for w in top_meta
        ]

        signals.append({
            "engine": "whale",
            "canonical_id": quote.canonical_id,
            "market": quote.title,
            "side": majority_side,
            "price": round(price, 4),
            "confidence": confidence,
            "expected_edge": expected_edge,
            "risk": risk,
            "liquidity": liquidity,
            "size_hint": size_hint,
            "rationale": (
                f"{n_wallets} whales bought · ${g['notional']:,.0f} notional · "
                f"{int(agreement * 100)}% on {majority_side} @ avg {whale_avg_price:.2f} · "
                f"{int(recency_sec / 60)}m ago · "
                + (f"edge from {record_n} resolved trades" if calibrated else "edge UNCALIBRATED (no track record yet)")
            ),
            # Surfaced in the signal card UI:
            "wallets": rationale_wallets,
            "fair_prob": round(fair_prob, 4),
            "mid": round(mid_for(quote, majority_side), 4),
            "whale_avg_price": round(whale_avg_price, 4),
            "edge_calibrated": calibrated,
            "record_n": record_n,
            **execution_refs(quote),
        })

    # Sort by composite score, take top-K.
    signals.sort(key=lambda s: s["confidence"] * (1 + s["expected_edge"]), reverse=True)
    return signals[:WHALE_MAX_SIGNALS]


def run() -> List[dict]:
    """Sync wrapper kept for parity with other engines' module API.

    The server's engine refresher calls `run_async(db)` directly when db is
    available, but legacy callers (unit tests, ad-hoc CLI) may still call `run()`.
    Returns [] without a db — the synthetic fallback is intentionally gone.
    """
    return []
