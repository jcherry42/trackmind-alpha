"""Measured track records for tracked whale wallets.

Each BUY a tracked wallet makes (``whale_entries``) is scored when its market
resolves: did the outcome they bought win, and what did that return at the price
they paid? Results go to ``whale_results`` (no TTL, unlike ``whale_entries``,
which expires after 14 days) and roll up per wallet onto ``tracked_wallets``:

    record_n         resolved buys in the window
    record_win_rate  fraction that won
    record_edge      mean(won − price paid): how much better than the market's
                     implied odds the wallet does, per $1 contract
    record_roi       USDC-weighted return on cost

Assumes positions are held to resolution (a wallet that sells early is scored as
if it held). The Whale engine shrinks each wallet's edge toward 0 by sample size,
so a handful of lucky wins can't masquerade as skill.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger("trackmind.whale_records")

RECORD_WINDOW_DAYS = int(os.environ.get("WHALE_RECORD_DAYS", "90"))
SHRINKAGE_K = float(os.environ.get("WHALE_EDGE_SHRINKAGE_K", "20"))
MIN_ENTRY_AGE_SEC = 3600  # don't look up a market minutes after the whale bought


def _side(outcome_index) -> Optional[str]:
    try:
        return "YES" if int(outcome_index) == 0 else "NO"
    except (TypeError, ValueError):
        return None


def score_entry(entry: dict, outcome: str) -> dict:
    side = _side(entry.get("outcome_index"))
    price = float(entry.get("price") or 0.0)
    usdc = float(entry.get("usdc_size") or 0.0)
    if outcome == "VOID" or side is None or not 0 < price < 1:
        won = None
        payout = usdc * (0.5 / price) if (outcome == "VOID" and 0 < price < 1) else usdc
    else:
        won = side == outcome
        payout = usdc / price if won else 0.0
    return {
        "_id": entry.get("transaction_hash") or str(entry.get("_id")),
        "wallet": (entry.get("wallet_address") or "").lower(),
        "condition_id": (entry.get("condition_id") or "").lower(),
        "side": side,
        "price": price,
        "usdc_size": usdc,
        "ts": float(entry.get("ts") or 0.0),
        "outcome": outcome,
        "won": won,
        "payout": round(payout, 4),
    }


def wallet_stats(rows: Iterable[dict]) -> dict:
    """Roll up scored results for one wallet. VOIDs are excluded from win rate / edge."""
    rows = [r for r in rows if r.get("won") is not None]
    n = len(rows)
    if n == 0:
        return {"record_n": 0, "record_win_rate": None, "record_edge": None, "record_roi": None}
    wins = sum(1 for r in rows if r["won"])
    edge = sum((1.0 if r["won"] else 0.0) - r["price"] for r in rows) / n
    cost = sum(r["usdc_size"] for r in rows)
    roi = (sum(r["payout"] for r in rows) - cost) / cost if cost > 0 else None
    return {
        "record_n": n,
        "record_win_rate": round(wins / n, 4),
        "record_edge": round(edge, 4),
        "record_roi": round(roi, 4) if roi is not None else None,
    }


def shrunk_edge(n: int, edge: Optional[float], k: float = SHRINKAGE_K) -> float:
    """Empirical-Bayes style shrinkage toward zero edge: n/(n+k) × measured edge."""
    if not n or edge is None:
        return 0.0
    return (n / (n + k)) * edge


async def ensure_indexes(db) -> None:
    await db.whale_results.create_index([("wallet", 1), ("ts", -1)])
    await db.whale_entries.create_index("scored")


async def update_records(db, client, now: Optional[float] = None, max_lookups: int = 50) -> dict:
    """Score newly resolved whale entries and refresh the affected wallets' records."""
    import resolutions

    now = now or time.time()
    counts = {"lookups": 0, "scored": 0, "wallets": 0}
    by_cond: Dict[str, List[dict]] = {}
    async for e in db.whale_entries.find(
        {"scored": {"$ne": True}, "condition_id": {"$nin": [None, ""]}, "ts": {"$lte": now - MIN_ENTRY_AGE_SEC}}
    ):
        by_cond.setdefault(e["condition_id"].lower(), []).append(e)

    touched: set = set()
    for cond, entries in by_cond.items():
        if counts["lookups"] >= max_lookups:
            break
        outcome = await resolutions.cached(db, "polymarket", cond)
        if outcome is None:
            counts["lookups"] += 1
            outcome = await resolutions.get_outcome(db, client, "polymarket", cond)
        if outcome is None:
            continue
        for e in entries:
            row = score_entry(e, outcome)
            row["resolved_at"] = datetime.now(timezone.utc)
            await db.whale_results.update_one({"_id": row["_id"]}, {"$set": row}, upsert=True)
            await db.whale_entries.update_one({"_id": e["_id"]}, {"$set": {"scored": True}})
            touched.add(row["wallet"])
            counts["scored"] += 1

    since = now - RECORD_WINDOW_DAYS * 86400
    for wallet in touched:
        rows = [r async for r in db.whale_results.find({"wallet": wallet, "ts": {"$gte": since}})]
        stats = wallet_stats(rows)
        stats["record_updated_at"] = datetime.now(timezone.utc)
        stats["hit_rate_30d"] = stats["record_win_rate"]  # legacy field the UI already shows
        await db.tracked_wallets.update_one({"address": wallet}, {"$set": stats})
        counts["wallets"] += 1
    return counts


async def load_records(db) -> Dict[str, dict]:
    """address → {n, edge} for every tracked wallet with a record."""
    out: Dict[str, dict] = {}
    async for w in db.tracked_wallets.find({"record_n": {"$gt": 0}}, {"address": 1, "record_n": 1, "record_edge": 1}):
        out[(w.get("address") or "").lower()] = {"n": int(w.get("record_n") or 0), "edge": w.get("record_edge")}
    return out
