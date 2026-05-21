"""Kalshi real-time fill stream over WebSocket.

Connects ONE persistent WebSocket per user-with-Kalshi-credentials, subscribed
to the `fill` and `market_lifecycle_v2` channels. Auth uses the same signed
challenge that the REST broker uses (key_id + PSS-SHA256 signature of the path).

Per-user lifecycle:
    1. `start_all_streams(db)` runs at backend boot — spawns one client per user
       in `broker_connections` with venue=kalshi.
    2. `restart_user_stream(db, user_id)` should be called whenever a user
       upserts or deletes their kalshi credentials.
    3. `stop_user_stream(user_id)` tears down the task cleanly.

Resilience:
    - Exponential backoff on disconnect (1s, 2s, 4s … capped at 60s).
    - Reconnects forever unless explicitly stopped.
    - All per-user state lives in `_streams` so the rest of the app can read it
      via `kalshi_status_snapshot()`.

Persistence:
    - Each `fill` event is appended to `db.kalshi_fills` (raw).
    - For each fill we try to match an open position by (user_id, kalshi_ticker)
      and update it — fully-filled SELL → close + realize, otherwise mark-to-market.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger("trackmind.kalshi_ws")


KALSHI_WS_URL = os.environ.get(
    "KALSHI_WS_URL",
    "wss://api.elections.kalshi.com/trade-api/ws/v2",
)
KALSHI_WS_PATH = "/trade-api/ws/v2"  # used in signature, NOT in URL


def _sign_ws_headers(key_id: str, private_key_pem: str) -> Dict[str, str]:
    """Build the auth headers Kalshi requires on the WS upgrade request."""
    priv = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    ts = str(int(time.time() * 1000))
    msg = (ts + "GET" + KALSHI_WS_PATH).encode()
    sig = priv.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
    }


class KalshiWsClient:
    """A long-running asyncio task that holds one user's WS connection."""

    def __init__(self, db, user_id: str, creds: Dict[str, str]) -> None:
        self.db = db
        self.user_id = user_id
        self.creds = creds
        self.task: Optional[asyncio.Task] = None
        self.connected = False
        self.started_at: float = 0.0
        self.last_msg_at: float = 0.0
        self.msg_count = 0
        self.reconnect_count = 0
        self.last_error: Optional[str] = None
        self._stopping = False

    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        self._stopping = False
        self.started_at = time.time()
        self.task = asyncio.create_task(self._run(), name=f"kalshi_ws[{self.user_id}]")

    async def stop(self) -> None:
        self._stopping = True
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.connected = False

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopping:
            try:
                headers = _sign_ws_headers(self.creds["key_id"], self.creds["private_key_pem"])
                # websockets 15 uses additional_headers (newer than 12.x's extra_headers).
                async with websockets.connect(
                    KALSHI_WS_URL,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    self.connected = True
                    self.last_error = None
                    backoff = 1.0
                    # Subscribe to fills + market lifecycle in one message.
                    sub = {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {"channels": ["fill", "market_lifecycle_v2"]},
                    }
                    await ws.send(json.dumps(sub))
                    logger.info(f"kalshi_ws[{self.user_id}] connected + subscribed")

                    async for raw in ws:
                        self.last_msg_at = time.time()
                        self.msg_count += 1
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        await self._handle_message(msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.connected = False
                self.last_error = str(e)[:180]
                self.reconnect_count += 1
                # P2 observability counter
                try:
                    from observability import ws_reconnects_total
                    ws_reconnects_total.labels(venue="kalshi").inc()
                except Exception:  # noqa: BLE001
                    pass
                # Distinguish unrecoverable config errors from transient network failures.
                # If the PEM can't be loaded at all, no amount of reconnecting will fix it —
                # stop the loop until the user re-saves credentials.
                if "PEM" in self.last_error or "InvalidPadding" in self.last_error or "InvalidData" in self.last_error:
                    logger.warning(
                        f"kalshi_ws[{self.user_id}] unrecoverable cred error "
                        f"({self.last_error[:100]}); stopping until user re-saves creds"
                    )
                    self._stopping = True
                    break
                logger.warning(
                    f"kalshi_ws[{self.user_id}] connection lost ({self.last_error}), "
                    f"reconnect #{self.reconnect_count} in {backoff:.0f}s"
                )
            if self._stopping:
                break
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)

    async def _handle_message(self, msg: Dict[str, Any]) -> None:
        kind = msg.get("type") or msg.get("channel")
        if kind == "fill" or msg.get("msg", {}).get("type") == "fill":
            await self._handle_fill(msg)
        elif kind == "subscribed":
            logger.info(f"kalshi_ws[{self.user_id}] subscribed: {msg.get('msg')}")
        elif kind == "error":
            logger.warning(f"kalshi_ws[{self.user_id}] server error: {msg}")
        # Other channels (market_lifecycle_v2, ticker) — silently drop for now.

    async def _handle_fill(self, msg: Dict[str, Any]) -> None:
        """Persist the fill + try to match an open position by ticker."""
        body = msg.get("msg") or msg
        ticker = body.get("ticker") or body.get("market_ticker")
        price_cents = body.get("yes_price") or body.get("price")
        side = (body.get("side") or "yes").upper()
        action = (body.get("action") or "buy").lower()
        count = int(body.get("count") or 0)
        if not ticker:
            return
        # Persist raw fill for inspection.
        await self.db.kalshi_fills.insert_one({
            "user_id": self.user_id,
            "ticker": ticker,
            "price_cents": price_cents,
            "side": side,
            "action": action,
            "count": count,
            "raw": body,
            "seen_at": time.time(),
        })
        # P2 observability counter
        try:
            from observability import live_fills_received_total
            live_fills_received_total.labels(venue="kalshi", action=action).inc()
        except Exception:  # noqa: BLE001
            pass
        logger.info(
            f"kalshi_ws[{self.user_id}] fill: {ticker} {action} {side} "
            f"{count}@{price_cents}¢"
        )
        # Match an open position by ticker. A 'sell' fill closes the slot; a
        # 'buy' fill only mark-to-markets (entry was already recorded server-side).
        pos = await self.db.positions.find_one_and_update(
            {"user_id": self.user_id, "kalshi_ticker": ticker, "status": "open"},
            {"$set": {
                "current_price": round((price_cents or 0) / 100.0, 4),
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        if pos and action == "sell":
            # Realize: payout = (price_cents/100) * count, less fees.
            payout = (price_cents or 0) / 100.0 * count
            entry = float(pos.get("entry_price") or 0.0) * float(pos.get("contracts") or 0.0)
            fees = float(pos.get("fees") or 0.0)
            realized = round(payout - entry - fees, 2)
            await self.db.positions.update_one(
                {"_id": pos["_id"]},
                {"$set": {
                    "status": "closed",
                    "exit_price": round((price_cents or 0) / 100.0, 4),
                    "realized_pnl": realized,
                    "settle_source": "kalshi_ws",
                    "closed_at": datetime.now(timezone.utc),
                }},
            )
            # Bridge str→ObjectId user_id when crediting bankroll.
            from bson import ObjectId
            try:
                user_oid = ObjectId(self.user_id) if isinstance(self.user_id, str) and len(self.user_id) == 24 else self.user_id
            except Exception:  # noqa: BLE001
                user_oid = self.user_id
            await self.db.users.update_one(
                {"_id": user_oid},
                {"$inc": {"bankroll": realized, "realized_pnl_today": realized}},
            )
            logger.info(
                f"kalshi_ws[{self.user_id}] settled position {pos['_id']} → pnl=${realized:.2f}"
            )


# ---------- module-level registry of per-user streams ----------
_streams: Dict[str, KalshiWsClient] = {}


async def start_all_streams(db) -> int:
    """Boot WS clients for every user that has stored Kalshi credentials.

    Returns the count of streams started. Idempotent — safe to call repeatedly.
    """
    from broker import load_user_broker_creds  # local import to avoid cycle
    started = 0
    async for row in db.broker_connections.find({"venue": "kalshi"}):
        user_id = str(row.get("user_id") or "")
        if not user_id or user_id in _streams:
            continue
        creds = await load_user_broker_creds(db, user_id, "kalshi")
        if not creds:
            continue
        client = KalshiWsClient(db, user_id, creds)
        _streams[user_id] = client
        await client.start()
        started += 1
    if started:
        logger.info(f"kalshi_ws: started {started} user stream(s)")
    return started


async def restart_user_stream(db, user_id: str) -> None:
    """Call this when a user upserts or deletes their Kalshi credentials.

    If credentials are present and decrypted, ensures a fresh stream is running.
    If credentials are gone, tears down any existing stream.
    """
    user_id = str(user_id)
    await stop_user_stream(user_id)
    from broker import load_user_broker_creds  # local import to avoid cycle
    creds = await load_user_broker_creds(db, user_id, "kalshi")
    if not creds:
        return
    client = KalshiWsClient(db, user_id, creds)
    _streams[user_id] = client
    await client.start()


async def stop_user_stream(user_id: str) -> None:
    """Tear down a user's WS stream cleanly. Safe if no stream exists."""
    user_id = str(user_id)
    client = _streams.pop(user_id, None)
    if client:
        await client.stop()


def kalshi_status_snapshot() -> Dict[str, Any]:
    """Aggregate WS state for /api/exchange/status."""
    items: List[Dict[str, Any]] = []
    for uid, c in _streams.items():
        items.append({
            "user_id": uid,
            "connected": c.connected,
            "messages": c.msg_count,
            "reconnects": c.reconnect_count,
            "last_msg_at": c.last_msg_at,
            "last_error": c.last_error,
            "started_at": c.started_at,
        })
    return {
        "ws_url": KALSHI_WS_URL,
        "active_streams": sum(1 for c in _streams.values() if c.connected),
        "total_streams": len(_streams),
        "streams": items,
    }


def my_stream_status(user_id: str) -> Optional[Dict[str, Any]]:
    """Per-user view used by /api/exchange/status."""
    c = _streams.get(str(user_id))
    if not c:
        return None
    return {
        "connected": c.connected,
        "messages": c.msg_count,
        "reconnects": c.reconnect_count,
        "last_msg_at": c.last_msg_at,
        "last_error": c.last_error,
    }
