"""Whale activity tracker — polls Polymarket's public Data API per tracked wallet.

For each wallet in `db.tracked_wallets`, hit `GET data-api.polymarket.com/activity?user=<addr>`
every WHALE_POLL_INTERVAL_SEC (default 90s) and persist any new BUY trades into
`db.whale_entries`. The engine then queries that collection to generate real
consensus signals (replacing the synthetic ELITE_WALLETS list).

Design notes:
  - Read-only. Burns 1 HTTP request per wallet per cycle. Polymarket has no
    documented rate limit on data-api, but we round-robin politely with a
    100ms gap between calls to stay friendly.
  - The natural dedupe key is `transactionHash` — unique index in Mongo.
  - We map Polymarket's `conditionId` to our internal `canonical_id` via the
    live_market store, so whale entries can be joined to current open markets.
  - State (last_polled_at) is per-wallet in `db.tracked_wallets`, not in memory,
    so restarts resume cleanly.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("trackmind.whale_tracker")

POLY_DATA_API = "https://data-api.polymarket.com"
WHALE_POLL_INTERVAL_SEC = float(os.environ.get("WHALE_POLL_INTERVAL_SEC", "90"))
WHALE_ACTIVITY_LIMIT = int(os.environ.get("WHALE_ACTIVITY_LIMIT", "20"))
# Min trade size (USDC) to count as a meaningful whale entry. Filters dust.
WHALE_MIN_USDC_SIZE = float(os.environ.get("WHALE_MIN_USDC_SIZE", "500"))
# Inter-wallet courtesy delay between Polymarket calls.
WHALE_REQUEST_GAP_SEC = 0.1
# How many seconds of activity we consider "recent" when first onboarding a wallet.
WHALE_BACKFILL_SEC = float(os.environ.get("WHALE_BACKFILL_SEC", str(24 * 3600)))  # 24h


# ---------- module state ----------
class WhaleTrackerState:
    def __init__(self) -> None:
        self.started_at: float = 0.0
        self.last_tick_at: float = 0.0
        self.tick_count: int = 0
        self.wallets_polled_total: int = 0
        self.entries_persisted_total: int = 0
        self.last_error: Optional[str] = None
        self.error_count: int = 0


state = WhaleTrackerState()


# ---------- default seed list ----------
# Top-volume + top-profit Polymarket wallets, scraped from the live leaderboard
# (lb-api.polymarket.com) and individually verified to have BUY activity ≥ $500 USDC.
# Updated 2026-05-22. Refreshable via POST /api/admin/whales/seed (future) or
# manually by editing this list + restarting the backend.
#
# Note: addresses are case-preserved here for readability but stored lowercased
# in Mongo (whale_tracker uses .lower() at insert time).
DEFAULT_SEED_WALLETS: List[Dict[str, Any]] = [
    # Highest sustained activity — confirmed $1M+ in last 20 BUY trades:
    {"address": "0x1887879a1bda615e88f280b582514c7d54e2678a", "label": "tubeyou",
     "note": "$13M 7d volume · always trading"},
    {"address": "0x492442EaB586F242B53bDa933fD5dE859c8A3782", "label": "Multicolored-Self",
     "note": "+$1.77M monthly profit · sports specialist"},
    # Top-volume traders (mixed categories):
    {"address": "0x204f72f35326db932158cba6adff0b9a1da95e14", "label": "swisstony",
     "note": "$27M 7d volume · +$624k 7d profit"},
    {"address": "0xe40ea00e74059c76c0035c919ef6b99c3e25a94d", "label": "restart77",
     "note": "$25M 7d volume"},
    {"address": "0x2005d16a84ceefa912d4e380cd32e7ff827875ea", "label": "RN1",
     "note": "$20M 7d volume · politics + sports"},
    {"address": "0x84cfffc3f16dcc353094de30d4a45226eccd2f63", "label": "mooseborzoi",
     "note": "$18M 7d volume"},
    # Top-profit traders:
    {"address": "0x9495425feeb0c250accb89275c97587011b19a27", "label": "LaBradfordSmith22",
     "note": "+$714k 7d profit · top monthly"},
    {"address": "0xb45a797faa52b0fd8adc56d30382022b7b12192c", "label": "bcda",
     "note": "+$557k 7d profit · high hit rate"},
    {"address": "0xfbf3d501e88815464642d0e913f15379c3eeb218", "label": "VPenguin",
     "note": "+$899k 7d profit · top profit ranker"},
    {"address": "0xf705fa045201391d9632b7f3cde06a5e24453ca7", "label": "Whale-F705",
     "note": "+$1.85M all-time crypto profit"},
]


# ---------- DB helpers ----------
async def ensure_indexes(db) -> None:
    """Create unique + TTL indexes for whale tracking collections."""
    # tracked_wallets: address must be unique (case-insensitive — we lowercase before insert).
    await db.tracked_wallets.create_index("address", unique=True)
    await db.tracked_wallets.create_index("is_active")
    # whale_entries: transaction_hash unique to dedupe across cycles.
    await db.whale_entries.create_index("transaction_hash", unique=True)
    await db.whale_entries.create_index("observed_at")
    await db.whale_entries.create_index("canonical_id")
    await db.whale_entries.create_index("wallet_address")
    # TTL: prune entries older than 14 days (336h) to keep the collection lean.
    try:
        await db.whale_entries.create_index("observed_at", expireAfterSeconds=14 * 24 * 3600)
    except Exception:  # noqa: BLE001
        # Index already exists with a different option — acceptable.
        pass


async def seed_default_wallets(db) -> int:
    """Reconcile `tracked_wallets` against DEFAULT_SEED_WALLETS.

    Idempotent + refreshing:
      - Upserts each wallet in DEFAULT_SEED_WALLETS (preserving entries_count etc).
      - Marks any admin-source wallet NOT in the current default list as
        is_active=False (so old curated wallets stop being polled but their
        history isn't destroyed).
      - User-added wallets (source="user") are never touched here.

    Returns the number of admin wallets inserted-or-updated.
    """
    now = datetime.now(timezone.utc)
    new_addrs = {w["address"].lower() for w in DEFAULT_SEED_WALLETS}
    touched = 0
    for w in DEFAULT_SEED_WALLETS:
        addr = w["address"].lower()
        res = await db.tracked_wallets.update_one(
            {"address": addr},
            {
                "$set": {
                    "label": w["label"],
                    "note": w["note"],
                    "source": "admin",
                    "is_active": True,
                },
                "$setOnInsert": {
                    "address": addr,
                    "added_by_user_id": None,
                    "hit_rate_30d": None,
                    "total_volume_30d": None,
                    "last_polled_at": None,
                    "last_entry_at": None,
                    "entries_count": 0,
                    "created_at": now,
                },
            },
            upsert=True,
        )
        if res.upserted_id or res.modified_count:
            touched += 1
    # Deactivate retired admin wallets (no longer in the default list).
    retired = await db.tracked_wallets.update_many(
        {"source": "admin", "address": {"$nin": list(new_addrs)}},
        {"$set": {"is_active": False, "retired_at": now}},
    )
    if retired.modified_count:
        logger.info(f"whale_tracker: retired {retired.modified_count} admin wallets (off the new seed list)")
    logger.info(f"whale_tracker: reconciled {touched}/{len(DEFAULT_SEED_WALLETS)} admin wallets (active)")
    return touched


# ---------- Polymarket data-api ----------
async def fetch_activity(client: httpx.AsyncClient, address: str, since_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    """Fetch recent activity for a wallet. Returns BUY trade rows above the size floor."""
    try:
        r = await client.get(
            f"{POLY_DATA_API}/activity",
            params={"user": address, "limit": WHALE_ACTIVITY_LIMIT},
            timeout=10.0,
        )
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list):
            return []
        out = []
        for row in rows:
            if row.get("type") != "TRADE":
                continue
            if (row.get("side") or "").upper() != "BUY":
                continue
            if not row.get("transactionHash") or not row.get("conditionId"):
                continue
            usdc_size = float(row.get("usdcSize") or 0)
            if usdc_size < WHALE_MIN_USDC_SIZE:
                continue
            if since_ts is not None and float(row.get("timestamp") or 0) < since_ts:
                continue
            out.append(row)
        return out
    except Exception as e:  # noqa: BLE001
        state.last_error = f"{address[:10]}…: {str(e)[:120]}"
        state.error_count += 1
        logger.warning(f"whale_tracker: fetch_activity failed for {address}: {e}")
        return []


async def _resolve_canonical_id(db, condition_id: str) -> Optional[str]:
    """Try to look up our internal canonical_id for a Polymarket conditionId.

    The live_market store keeps Polymarket markets keyed by canonical_id; the raw
    conditionId is stored alongside. Falls back to None if we don't track that
    market — the entry is still persisted (just unjoinable until we ingest it).
    """
    try:
        from markets import market_store
        cond_lower = (condition_id or "").lower()
        for q in market_store.all():
            if q.venue != "polymarket":
                continue
            poly_cond = (getattr(q, "polymarket_condition_id", "") or "").lower()
            if poly_cond and poly_cond == cond_lower:
                return q.canonical_id
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------- ingest ----------
async def _ingest_wallet(client: httpx.AsyncClient, db, wallet: Dict[str, Any]) -> int:
    """Poll one wallet, write new entries. Returns # of new entries persisted."""
    addr = wallet["address"]
    state.wallets_polled_total += 1
    # First-time wallets backfill up to WHALE_BACKFILL_SEC ago; subsequent polls
    # only fetch entries newer than the last observed_at.
    last_entry_at = wallet.get("last_entry_at")
    if isinstance(last_entry_at, datetime):
        since_ts = last_entry_at.timestamp() - 60  # 60s overlap to avoid edge gaps
    else:
        since_ts = time.time() - WHALE_BACKFILL_SEC
    rows = await fetch_activity(client, addr, since_ts=since_ts)
    if not rows:
        await db.tracked_wallets.update_one(
            {"_id": wallet["_id"]},
            {"$set": {"last_polled_at": datetime.now(timezone.utc)}},
        )
        return 0

    persisted = 0
    newest_ts = since_ts
    for row in rows:
        tx_hash = row.get("transactionHash")
        ts = float(row.get("timestamp") or 0)
        if ts > newest_ts:
            newest_ts = ts
        cond_id = (row.get("conditionId") or "").lower()
        canonical_id = await _resolve_canonical_id(db, cond_id)
        entry_doc = {
            "transaction_hash": tx_hash,
            "wallet_address": addr,
            "wallet_label": wallet.get("label"),
            "condition_id": cond_id,
            "canonical_id": canonical_id,
            "side": (row.get("side") or "").upper(),
            "outcome": row.get("outcome"),
            "outcome_index": row.get("outcomeIndex"),
            "price": float(row.get("price") or 0),
            "size_shares": float(row.get("size") or 0),
            "usdc_size": float(row.get("usdcSize") or 0),
            "title": row.get("title"),
            "slug": row.get("slug"),
            "pseudonym": row.get("pseudonym"),
            "ts": ts,
            "observed_at": datetime.now(timezone.utc),
        }
        try:
            await db.whale_entries.insert_one(entry_doc)
            persisted += 1
            state.entries_persisted_total += 1
        except Exception as e:  # noqa: BLE001
            # Duplicate transaction_hash (unique index) — silent skip.
            if "duplicate key" not in str(e).lower() and "E11000" not in str(e):
                logger.warning(f"whale_tracker: persist failed for {tx_hash}: {e}")
    await db.tracked_wallets.update_one(
        {"_id": wallet["_id"]},
        {"$set": {
            "last_polled_at": datetime.now(timezone.utc),
            "last_entry_at": datetime.fromtimestamp(newest_ts, tz=timezone.utc) if newest_ts > 0 else None,
        }, "$inc": {"entries_count": persisted}},
    )
    return persisted


async def _tick(client: httpx.AsyncClient, db) -> Dict[str, int]:
    """One polling pass over all active tracked wallets."""
    state.tick_count += 1
    state.last_tick_at = time.time()
    wallets = [w async for w in db.tracked_wallets.find({"is_active": True})]
    total_persisted = 0
    for w in wallets:
        try:
            n = await _ingest_wallet(client, db, w)
            total_persisted += n
        except Exception as e:  # noqa: BLE001
            state.last_error = str(e)[:160]
            state.error_count += 1
            logger.warning(f"whale_tracker: ingest failed for {w.get('address')}: {e}")
        await asyncio.sleep(WHALE_REQUEST_GAP_SEC)
    return {"wallets": len(wallets), "persisted": total_persisted}


async def poll_loop(db) -> None:
    """Forever: tick once per WHALE_POLL_INTERVAL_SEC."""
    state.started_at = time.time()
    await ensure_indexes(db)
    await seed_default_wallets(db)
    logger.info(
        f"whale_tracker starting (every {WHALE_POLL_INTERVAL_SEC:.0f}s, "
        f"min_size=${WHALE_MIN_USDC_SIZE:.0f})"
    )
    async with httpx.AsyncClient(headers={"User-Agent": "TRACKMIND/1.0"}) as client:
        while True:
            try:
                result = await _tick(client, db)
                if result["persisted"] > 0:
                    logger.info(
                        f"whale_tracker tick: {result['wallets']} wallets polled, "
                        f"{result['persisted']} new entries"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                state.last_error = str(e)[:160]
                state.error_count += 1
                logger.warning(f"whale_tracker tick failed: {e}")
            await asyncio.sleep(WHALE_POLL_INTERVAL_SEC)


def status_snapshot() -> Dict[str, Any]:
    """Sync status reflection for /admin/metrics + /exchange/status."""
    age = (time.time() - state.last_tick_at) if state.last_tick_at else None
    return {
        "available": True,
        "started_at": state.started_at,
        "last_tick_at": state.last_tick_at,
        "last_tick_age_sec": round(age, 1) if age is not None else None,
        "tick_count": state.tick_count,
        "wallets_polled_total": state.wallets_polled_total,
        "entries_persisted_total": state.entries_persisted_total,
        "error_count": state.error_count,
        "last_error": state.last_error,
        "poll_interval_sec": WHALE_POLL_INTERVAL_SEC,
        "min_usdc_size": WHALE_MIN_USDC_SIZE,
    }
