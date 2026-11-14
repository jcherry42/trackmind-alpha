"""Shared market-resolution cache.

One place that answers "how did this market resolve?" for paper settlement,
the engine scorecard and whale track records, so each market is looked up at
most once per ``RESOLUTION_RECHECK_SEC`` no matter how many things ask.

Collection ``market_resolutions``::

    {_id: "polymarket:0xabc…" | "kalshi:KX-…", venue, ref,
     outcome: "YES" | "NO" | "VOID" | None, checked_at, resolved_at}

"YES"/"NO" are positional for Polymarket (outcome 0 / outcome 1).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from live_market import fetch_kalshi_resolution, fetch_polymarket_resolution

logger = logging.getLogger("trackmind.resolutions")

RESOLUTION_RECHECK_SEC = float(os.environ.get("RESOLUTION_RECHECK_SEC", "300"))


def key_for(venue: str, ref: str) -> Optional[str]:
    if not venue or not ref:
        return None
    ref = ref.lower() if venue == "polymarket" else ref
    return f"{venue}:{ref}"


async def ensure_indexes(db) -> None:
    await db.market_resolutions.create_index("checked_at")


async def cached(db, venue: str, ref: str) -> Optional[str]:
    """Cached outcome only — never calls a venue."""
    k = key_for(venue, ref)
    if not k:
        return None
    row = await db.market_resolutions.find_one({"_id": k})
    return (row or {}).get("outcome")


async def get_outcome(db, client, venue: str, ref: str, *, now: Optional[datetime] = None) -> Optional[str]:
    """Resolved outcome for a market, using the cache; looks up the venue when stale.

    Returns None while the market is unresolved (or the lookup failed).
    """
    k = key_for(venue, ref)
    if not k:
        return None
    now = now or datetime.now(timezone.utc)
    row = await db.market_resolutions.find_one({"_id": k})
    if row and row.get("outcome"):
        return row["outcome"]
    checked = (row or {}).get("checked_at")
    if checked is not None:
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if now - checked < timedelta(seconds=RESOLUTION_RECHECK_SEC):
            return None
    try:
        if venue == "polymarket":
            outcome = await fetch_polymarket_resolution(client, ref)
        elif venue == "kalshi":
            outcome = await fetch_kalshi_resolution(client, ref)
        else:
            return None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"resolution lookup failed for {k}: {e}")
        outcome = None
    upd = {"venue": venue, "ref": ref, "checked_at": now, "outcome": outcome}
    if outcome:
        upd["resolved_at"] = now
    await db.market_resolutions.update_one({"_id": k}, {"$set": upd}, upsert=True)
    return outcome
