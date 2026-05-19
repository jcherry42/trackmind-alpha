"""Real-money settlement watchers for Polymarket (Polygon) and Kalshi (WS).

Day-1 scope:
  - Polygon CTF event poller (read-only, eth_getLogs every 30s, 12-block conf depth)
  - Per-user Polymarket address derivation (EOA + Gnosis-Safe proxy)
  - Status endpoint plumbing
  - Heartbeat tick into `db.system_state.polygon`

Day-2+ scope (stubbed but not yet wired):
  - Kalshi WS fill stream per user-with-creds
  - Position settlement matching on PayoutRedemption / PositionsMerge

Design notes:
  - No paid SDK. Raw JSON-RPC over httpx. eth-account for address derivation.
  - The poller is a single coroutine: it does NOT spawn per-user tasks. It scans the
    chain once per tick and dispatches matched events out to the right user(s).
  - State (last_scanned_block) lives in `db.system_state` so we can resume across
    restarts without re-scanning from block 0.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from eth_account import Account

logger = logging.getLogger("trackmind.settlement")


# ---------- constants ----------
# Polymarket's CTF (Conditional Tokens Framework) on Polygon.
CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# Event topic0 hashes — keccak256 of the canonical event signature.
# These are the Gnosis Conditional Tokens events that Polymarket actually emits.
TOPIC_PAYOUT_REDEMPTION = "0x2682012a4a4f1973119f1c9b90745d1bd91fa2bab387344f044cb3586864d18d"
TOPIC_POSITIONS_MERGE = "0x6f13ca62553fcc2bcd2372180a43949c1e4cebba603901ede2f4e14f36b282ca"
TOPIC_POSITION_SPLIT = "0x2e6bb91f8cbcda0c93623c54d0403a43514fabc40084ec96b6d5379a74786298"
TOPIC_CONDITION_RESOLUTION = "0xb44d84d3289691f71497564b85d4233648d9dbae8cbdbb4329f301c3a0185894"

TOPIC_NAMES = {
    TOPIC_PAYOUT_REDEMPTION: "PayoutRedemption",
    TOPIC_POSITIONS_MERGE: "PositionsMerge",
    TOPIC_POSITION_SPLIT: "PositionSplit",
    TOPIC_CONDITION_RESOLUTION: "ConditionResolution",
}

# Polygon chain id.
POLYGON_CHAIN_ID = 137

# How deep to lag the scan tip — 12 blocks ≈ 25s on Polygon, safely past reorgs.
CONF_DEPTH_BLOCKS = 12

# How often the poll loop ticks.
POLL_INTERVAL_SEC = 30.0

# eth_getLogs span limit: Alchemy FREE tier caps at 10 blocks per request,
# regardless of address filter. We chunk the scan into 10-block windows and
# fire several per tick to keep pace with the chain (~30 blocks/min on Polygon).
SCAN_CHUNK_BLOCKS = 10
SCAN_MAX_CHUNKS_PER_TICK = 30  # = 300 blocks ≈ 10 minutes of chain per tick


# ---------- module state ----------
class SettlementState:
    """Mutable runtime state for the Polygon watcher."""

    def __init__(self) -> None:
        self.started_at: float = 0.0
        self.last_tick_at: float = 0.0
        self.latest_block: Optional[int] = None
        self.last_scanned_block: Optional[int] = None
        self.tick_count: int = 0
        self.error_count: int = 0
        self.last_error: Optional[str] = None
        # Cache (eoa_or_proxy address lower-case) -> user_id. Refreshed every tick.
        self.address_to_user: Dict[str, str] = {}


state = SettlementState()


# ---------- key + address derivation ----------
def derive_polymarket_addresses(private_key: str) -> Dict[str, str]:
    """Return (eoa, proxy) for a Polymarket private key.

    EOA: derived directly from secp256k1 private key.
    Proxy: Polymarket uses a Gnosis-Safe-style proxy factory with deterministic
           CREATE2 addressing keyed off the EOA. We compute the proxy address
           using the Polymarket factory + a fixed singleton + the EOA as the
           CREATE2 salt source. NOTE: as of mid-2025 Polymarket's exact proxy
           derivation is a Safe-Proxy clone; for Day 1 we ship the EOA only.
           Proxy derivation will be added in Day 2 alongside event matching.
    """
    if not private_key:
        return {}
    try:
        # eth-account accepts both 0x-prefixed and bare hex.
        acct = Account.from_key(private_key)
        return {"eoa": acct.address.lower()}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"derive_polymarket_addresses failed: {e}")
        return {}


# ---------- JSON-RPC ----------
async def _rpc(client: httpx.AsyncClient, url: str, method: str, params: list) -> Any:
    """JSON-RPC call. `url` is the FULL Alchemy URL (no trailing slash).

    We pass the URL explicitly per-request because httpx with `base_url=` and
    POST("") appends a trailing slash that Alchemy rejects with 400.
    """
    r = await client.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=12.0,
    )
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"rpc error {method}: {body['error']}")
    return body.get("result")


async def polygon_status() -> Dict[str, Any]:
    """One-shot health probe: returns chain id + latest block.

    Used by /api/exchange/status. Tries the RPC; reports both connected=true
    on success and connected=false (with reason) on failure.
    """
    url = os.environ.get("POLYGON_RPC_URL")
    if not url:
        return {"connected": False, "reason": "POLYGON_RPC_URL not set"}
    try:
        async with httpx.AsyncClient() as c:
            chain_hex = await _rpc(c, url, "eth_chainId", [])
            block_hex = await _rpc(c, url, "eth_blockNumber", [])
        return {
            "connected": True,
            "chain_id": int(chain_hex, 16),
            "latest_block": int(block_hex, 16),
            "rpc_host": _safe_rpc_host(url),
        }
    except Exception as e:  # noqa: BLE001
        return {"connected": False, "reason": str(e)[:160], "rpc_host": _safe_rpc_host(url)}


def _safe_rpc_host(url: str) -> str:
    """Strip the API key path from the URL for safe display."""
    try:
        # alchemy: https://polygon-mainnet.g.alchemy.com/v2/<key>
        return url.split("/v2/")[0] + "/v2/…" if "/v2/" in url else url.split("?")[0]
    except Exception:  # noqa: BLE001
        return "(redacted)"


# ---------- poll loop ----------
async def _refresh_watched_addresses(db) -> Dict[str, str]:
    """Reload (address -> user_id) map from broker_connections.polymarket_addresses.

    Cheap to do every tick (typically <100 rows even at scale).
    """
    out: Dict[str, str] = {}
    async for row in db.broker_connections.find({"venue": "polymarket"}):
        for _label, addr in (row.get("polymarket_addresses") or {}).items():
            if addr:
                out[addr.lower()] = str(row.get("user_id") or "")
    return out


async def _scan_ctf_logs(client: httpx.AsyncClient, url: str, from_block: int, to_block: int) -> List[dict]:
    """Fetch ALL CTF contract logs in [from_block, to_block].

    We do NOT filter by topic at the RPC layer — Polymarket CTF emits relatively
    few events per minute, and pulling them all lets us match against any user
    address regardless of which sub-event fired. If volume grows we can topic-
    filter against `PayoutRedemption` and `PositionsMerge`.
    """
    return await _rpc(
        client,
        url,
        "eth_getLogs",
        [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": CTF_CONTRACT,
        }],
    )


def _addresses_in_log(log: dict) -> List[str]:
    """Pull any 20-byte addresses from a log's topics (indexed args)."""
    out: List[str] = []
    for t in log.get("topics", [])[1:]:  # skip topic0 (event sig)
        if isinstance(t, str) and len(t) == 66:  # 32-byte topic, 0x + 64 hex
            # Indexed addresses are right-padded into 32 bytes.
            out.append("0x" + t[-40:].lower())
    return out


def decode_ctf_event(log: dict) -> Dict[str, Any]:
    """Decode a CTF event log into a dict of named fields.

    Returns at minimum: {event, address, condition_id}. For PayoutRedemption it
    also returns: {payout_wei, payout_usdc, index_sets}.
    All other events return raw fields only. Unknown topic0 → {event: "unknown"}.
    """
    topics = log.get("topics") or []
    if not topics:
        return {"event": "unknown"}
    topic0 = topics[0]
    name = TOPIC_NAMES.get(topic0, "unknown")
    out: Dict[str, Any] = {"event": name, "topic0": topic0}
    # All four CTF events we track have this indexed layout:
    #   topic[1] = address (redeemer / stakeholder)
    #   topic[2] = collateralToken address (indexed)
    #   topic[3] = parentCollectionId (PayoutRedemption) OR conditionId (Merge/Split)
    # For PayoutRedemption: conditionId is in `data` (non-indexed!), and parentCollectionId is topic[3].
    if len(topics) > 1 and isinstance(topics[1], str) and len(topics[1]) == 66:
        out["address"] = "0x" + topics[1][-40:].lower()
    if len(topics) > 2 and isinstance(topics[2], str) and len(topics[2]) == 66:
        out["collateral_token"] = "0x" + topics[2][-40:].lower()
    if len(topics) > 3:
        # For Merge/Split, topic[3] is the conditionId directly.
        # For PayoutRedemption, topic[3] is parentCollectionId — conditionId lives in data.
        if name in ("PositionsMerge", "PositionSplit"):
            out["condition_id"] = topics[3]
        else:
            out["parent_collection_id"] = topics[3]

    data_hex = log.get("data", "0x")
    data_bytes = bytes.fromhex(data_hex[2:]) if data_hex.startswith("0x") and len(data_hex) > 2 else b""

    if name == "PayoutRedemption":
        # data layout (ABI-encoded, big-endian 32-byte words):
        #   word 0: conditionId (bytes32)
        #   word 1: offset to indexSets[] (uint256, typically 0x60)
        #   word 2: payout (uint256, in collateral wei)
        #   word 3: indexSets length (uint256)
        #   word 4..: indexSets[i] values
        if len(data_bytes) >= 96:
            out["condition_id"] = "0x" + data_bytes[0:32].hex()
            payout_wei = int.from_bytes(data_bytes[64:96], "big")
            out["payout_wei"] = payout_wei
            # USDC.e on Polygon has 6 decimals.
            out["payout_usdc"] = round(payout_wei / 1_000_000, 6)
        if len(data_bytes) >= 128:
            n = int.from_bytes(data_bytes[96:128], "big")
            out["index_sets"] = [
                int.from_bytes(data_bytes[128 + i * 32: 160 + i * 32], "big")
                for i in range(min(n, 16))   # safety bound
            ]
    elif name in ("PositionsMerge", "PositionSplit"):
        # data: indexSets[] + amount (uint256)
        if len(data_bytes) >= 64:
            n = int.from_bytes(data_bytes[32:64], "big")
            tail_off = 64 + n * 32
            if len(data_bytes) >= tail_off + 32:
                out["amount_wei"] = int.from_bytes(data_bytes[tail_off:tail_off + 32], "big")

    return out


async def _settle_polymarket_position(db, event: Dict[str, Any], user_id: str) -> Optional[dict]:
    """For a PayoutRedemption event, settle any matching open position.

    Match key: positions.polymarket_condition_id == event.condition_id (case-insensitive)
    AND positions.user_id == user_id AND positions.status == "open".

    Uses an atomic findOneAndUpdate({status:"open"}) so a duplicate event delivery
    (re-orgs, double-tick) can't double-settle. Returns the settled position dict
    or None when nothing matched.
    """
    cond = (event.get("condition_id") or "").lower()
    payout_usdc = float(event.get("payout_usdc") or 0.0)
    if not cond:
        return None
    open_pos = await db.positions.find_one_and_update(
        {
            "user_id": user_id,
            "polymarket_condition_id": cond,
            "status": "open",
        },
        {"$set": {
            "status": "closed",
            "exit_price": round(min(1.0, payout_usdc / max(0.0001, float(0))) if False else min(1.0, payout_usdc), 4),
            "settle_source": "polygon_ctf",
            "settle_tx": event.get("tx"),
            "settle_block": event.get("block"),
            "closed_at": datetime.now(timezone.utc),
        }},
    )
    if not open_pos:
        return None
    contracts = float(open_pos.get("contracts") or 0.0)
    entry_price = float(open_pos.get("entry_price") or 0.0)
    fees = float(open_pos.get("fees") or 0.0)
    realized = round(payout_usdc - entry_price * contracts - fees, 2)
    # exit_price should be payout-per-contract, clamped [0, 1] for prediction markets.
    exit_price_per_contract = round(min(1.0, max(0.0, payout_usdc / max(0.01, contracts))), 4)
    await db.positions.update_one(
        {"_id": open_pos["_id"]},
        {"$set": {"realized_pnl": realized, "exit_price": exit_price_per_contract}},
    )
    # `user_id` in positions is a string; in users it's an ObjectId — bridge them.
    from bson import ObjectId
    try:
        user_oid = ObjectId(user_id) if isinstance(user_id, str) and len(user_id) == 24 else user_id
    except Exception:  # noqa: BLE001
        user_oid = user_id
    await db.users.update_one(
        {"_id": user_oid},
        {"$inc": {"bankroll": realized, "realized_pnl_today": realized}},
    )
    logger.info(
        f"settlement: position {open_pos['_id']} closed via on-chain PayoutRedemption "
        f"payout=${payout_usdc:.2f} pnl=${realized:.2f}"
    )
    return {**open_pos, "realized_pnl": realized, "payout_usdc": payout_usdc, "exit_price": exit_price_per_contract}


async def poll_loop(db) -> None:
    """Forever: tick once per POLL_INTERVAL_SEC."""
    url = os.environ.get("POLYGON_RPC_URL")
    if not url:
        logger.warning("POLYGON_RPC_URL not set — settlement watcher disabled")
        return
    state.started_at = time.time()
    backoff = 1.0
    async with httpx.AsyncClient() as client:
        # Bootstrap last_scanned_block from system_state, or start at tip-CONF.
        sysdoc = await db.system_state.find_one({"_id": "polygon"})
        if sysdoc and isinstance(sysdoc.get("last_scanned_block"), int):
            state.last_scanned_block = sysdoc["last_scanned_block"]
            logger.info(f"settlement watcher resuming from block {state.last_scanned_block}")
        else:
            try:
                latest_hex = await _rpc(client, url, "eth_blockNumber", [])
                tip = int(latest_hex, 16)
                state.last_scanned_block = max(0, tip - CONF_DEPTH_BLOCKS)
                logger.info(f"settlement watcher cold start at block {state.last_scanned_block}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"settlement watcher boot probe failed: {e}")
                state.last_error = str(e)[:160]
                # Retry quickly on first failure.
                await asyncio.sleep(5)
                return

        while True:
            try:
                await _tick(client, url, db)
                backoff = 1.0
            except Exception as e:  # noqa: BLE001
                state.error_count += 1
                state.last_error = str(e)[:200]
                logger.warning(f"settlement watcher tick failed: {e}")
                await asyncio.sleep(min(60.0, backoff))
                backoff *= 2
                continue
            await asyncio.sleep(POLL_INTERVAL_SEC)


async def _tick(client: httpx.AsyncClient, url: str, db) -> None:
    """One scan: advance last_scanned_block in 10-block chunks (Alchemy free-tier limit)."""
    state.tick_count += 1
    state.last_tick_at = time.time()

    latest_hex = await _rpc(client, url, "eth_blockNumber", [])
    latest = int(latest_hex, 16)
    state.latest_block = latest
    safe_tip = latest - CONF_DEPTH_BLOCKS
    if state.last_scanned_block is None or state.last_scanned_block >= safe_tip:
        # Up to date.
        return

    # Refresh address watch list at most once per tick.
    state.address_to_user = await _refresh_watched_addresses(db)
    if not state.address_to_user:
        # Nobody has connected Polymarket — skip eth_getLogs entirely. Just
        # advance the cursor so we don't have to backfill if/when someone connects.
        new_cursor = min(safe_tip, state.last_scanned_block + (SCAN_CHUNK_BLOCKS * SCAN_MAX_CHUNKS_PER_TICK))
        state.last_scanned_block = new_cursor
        await db.system_state.update_one(
            {"_id": "polygon"},
            {"$set": {
                "last_scanned_block": new_cursor,
                "latest_block": latest,
                "tick_count": state.tick_count,
                "updated_at": time.time(),
            }},
            upsert=True,
        )
        return

    matched: List[dict] = []
    chunks_done = 0
    cursor = state.last_scanned_block + 1
    while cursor <= safe_tip and chunks_done < SCAN_MAX_CHUNKS_PER_TICK:
        chunk_end = min(safe_tip, cursor + SCAN_CHUNK_BLOCKS - 1)
        logs = await _scan_ctf_logs(client, url, cursor, chunk_end)
        for log in logs:
            for addr in _addresses_in_log(log):
                if addr in state.address_to_user:
                    decoded = decode_ctf_event(log)
                    matched.append({
                        "address": addr,
                        "user_id": state.address_to_user[addr],
                        "tx": log.get("transactionHash"),
                        "block": int(log.get("blockNumber", "0x0"), 16),
                        "topic0": (log.get("topics") or [None])[0],
                        "event": decoded.get("event", "unknown"),
                        "condition_id": decoded.get("condition_id"),
                        "payout_usdc": decoded.get("payout_usdc"),
                        "log_index": int(log.get("logIndex", "0x0"), 16),
                        "raw": log,
                        "seen_at": time.time(),
                    })
        cursor = chunk_end + 1
        chunks_done += 1

    settled: List[dict] = []
    if matched:
        # Persist decoded events for inspection/audit.
        await db.polygon_events.insert_many([m.copy() for m in matched])
        # Run settlement on PayoutRedemption events. Idempotent via the open-status guard.
        for m in matched:
            if m.get("event") != "PayoutRedemption":
                continue
            res = await _settle_polymarket_position(db, {
                "condition_id": m.get("condition_id"),
                "payout_usdc": m.get("payout_usdc"),
                "tx": m.get("tx"),
                "block": m.get("block"),
            }, m["user_id"])
            if res:
                settled.append(res)
        logger.info(
            f"settlement watcher: matched {len(matched)} CTF event(s); "
            f"settled {len(settled)} position(s)"
        )

    state.last_scanned_block = cursor - 1
    await db.system_state.update_one(
        {"_id": "polygon"},
        {"$set": {
            "last_scanned_block": state.last_scanned_block,
            "latest_block": latest,
            "tick_count": state.tick_count,
            "updated_at": time.time(),
        }},
        upsert=True,
    )


# ---------- runtime status snapshot ----------
async def settlement_status_snapshot(db) -> Dict[str, Any]:
    """Detailed status for /api/exchange/status (admin + per-user)."""
    poly_health = await polygon_status()
    # Kalshi WS is Day-2 — return a stub for now.
    return {
        "polygon": {
            **poly_health,
            "watcher": {
                "running": state.started_at > 0,
                "ticks": state.tick_count,
                "errors": state.error_count,
                "last_error": state.last_error,
                "last_tick_at": state.last_tick_at,
                "last_scanned_block": state.last_scanned_block,
                "lag_blocks": (
                    (state.latest_block - state.last_scanned_block)
                    if state.latest_block and state.last_scanned_block
                    else None
                ),
                "watched_addresses": len(state.address_to_user),
            },
        },
        "kalshi": {
            "ws_connected": False,
            "note": "Kalshi WebSocket fill stream — scheduled for Day 2",
        },
    }
