"""Broker adapters.

The PaperBroker is fully functional (no external venue). Polymarket and Kalshi
adapters now implement real REST flows but are GATED off by default:
    USER must have live_mode=True
  AND USER's live_broker preference must be set to "polymarket" or "kalshi"
  AND the env vars listed below must be present.

If any precondition is unmet, calls fall back to PaperBroker so paper-only
deployments keep working.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("trackmind.broker")


class BaseBroker:
    name = "base"

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        raise NotImplementedError


# ----------------- Paper -----------------
class PaperBroker(BaseBroker):
    """Simulated execution. Fills at quoted price + small slippage."""
    name = "paper"

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        slippage = 0.002 if signal.get("liquidity", 0.5) > 0.6 else 0.005
        side = signal.get("side", "YES")
        price = signal.get("price", 0.5)
        fill_price = price + slippage if side != "ARB" else price + slippage
        contracts = notional / max(0.01, fill_price)
        return {
            "id": f"fill_{uuid.uuid4().hex[:12]}",
            "broker": self.name,
            "user_id": user_id,
            "engine": signal["engine"],
            "canonical_id": signal["canonical_id"],
            "market": signal["market"],
            "venue": signal.get("venue", "paper"),
            "side": side,
            "price": round(fill_price, 4),
            "contracts": round(contracts, 2),
            "notional": round(notional, 2),
            "fees": round(notional * 0.005, 4),
            "status": "filled",
            "ts": time.time(),
        }


# ----------------- Polymarket -----------------
class PolymarketBroker(BaseBroker):
    """Place limit orders on Polymarket CLOB via the official REST endpoint.

    Requires env:
      POLYMARKET_PRIVATE_KEY   — hex private key of an EOA funded with USDC.e on Polygon
      POLYMARKET_API_KEY       — L2 API credential (from `derive_api_key`)
      POLYMARKET_API_SECRET    — L2 API secret
      POLYMARKET_API_PASSPHRASE — L2 API passphrase
      POLYMARKET_HOST (optional) — defaults to https://clob.polymarket.com

    NB: This adapter intentionally does NOT pull in the heavy `py-clob-client`
    SDK at import time so paper-only deployments stay lean. The SDK is imported
    lazily on first .place_order() — install it via:
        pip install py-clob-client web3
    """
    name = "polymarket"
    host = os.environ.get("POLYMARKET_HOST", "https://clob.polymarket.com")

    def __init__(self) -> None:
        self._client: Any = None  # lazily-built py_clob_client.ClobClient

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        for k in (
            "POLYMARKET_PRIVATE_KEY",
            "POLYMARKET_API_KEY",
            "POLYMARKET_API_SECRET",
            "POLYMARKET_API_PASSPHRASE",
        ):
            if not os.environ.get(k):
                raise RuntimeError(f"Missing {k}")
        try:
            from py_clob_client.client import ClobClient  # type: ignore
            from py_clob_client.clob_types import ApiCreds  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "py-clob-client not installed; run `pip install py-clob-client web3`"
            ) from e
        self._client = ClobClient(
            host=self.host,
            chain_id=137,  # Polygon
            key=os.environ["POLYMARKET_PRIVATE_KEY"],
        )
        self._client.set_api_creds(
            ApiCreds(
                api_key=os.environ["POLYMARKET_API_KEY"],
                api_secret=os.environ["POLYMARKET_API_SECRET"],
                api_passphrase=os.environ["POLYMARKET_API_PASSPHRASE"],
            )
        )
        return self._client

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        import asyncio

        # token_id is required by the CLOB; expected to be on the signal courtesy of live_market.py.
        token_ids = signal.get("polymarket_token_ids") or signal.get("clobTokenIds") or []
        if not token_ids or len(token_ids) < 2:
            raise RuntimeError("polymarket_token_ids missing on signal — fetch markets via live_market.py")
        side = signal.get("side", "YES")
        token_id = token_ids[0] if side == "YES" else token_ids[1]
        price = max(0.01, min(0.99, float(signal["price"])))
        size_shares = round(notional / price, 2)

        def _submit() -> Dict[str, Any]:
            client = self._ensure_client()
            from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore
            args = OrderArgs(
                price=price,
                size=size_shares,
                side="BUY",   # we always BUY shares of the chosen outcome
                token_id=token_id,
            )
            signed = client.create_order(args)
            resp = client.post_order(signed, OrderType.GTC)
            return resp

        try:
            resp = await asyncio.to_thread(_submit)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"polymarket order failed: {e}") from e

        order_id = resp.get("orderID") or resp.get("orderId") or f"polymarket_{uuid.uuid4().hex[:10]}"
        fee_bps = 200  # Polymarket charges up to 2% on taker side; conservative reserve
        fees = round(notional * fee_bps / 10_000, 4)
        return {
            "id": str(order_id),
            "broker": self.name,
            "user_id": user_id,
            "engine": signal["engine"],
            "canonical_id": signal["canonical_id"],
            "market": signal["market"],
            "venue": "polymarket",
            "side": side,
            "price": round(price, 4),
            "contracts": size_shares,
            "notional": round(notional, 2),
            "fees": fees,
            "status": "submitted",
            "ts": time.time(),
            "raw": resp,
        }


# ----------------- Kalshi -----------------
class KalshiBroker(BaseBroker):
    """Place limit orders on Kalshi via the v2 REST API.

    Requires env:
      KALSHI_KEY_ID            — UUID from the dashboard
      KALSHI_PRIVATE_KEY_PEM   — full PEM contents of the downloaded private key
      KALSHI_BASE (optional)   — defaults to https://api.elections.kalshi.com/trade-api/v2

    Signing scheme (RSA-PSS over SHA-256):
        msg     = timestamp_ms_str + method + path
        header  = {
            KALSHI-ACCESS-KEY:       <key_id>,
            KALSHI-ACCESS-TIMESTAMP: <timestamp_ms_str>,
            KALSHI-ACCESS-SIGNATURE: base64(rsa_sign(msg))
        }
    """
    name = "kalshi"
    base = os.environ.get("KALSHI_BASE", "https://api.elections.kalshi.com/trade-api/v2")

    def _sign_headers(self, method: str, path: str) -> Dict[str, str]:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        pem = os.environ.get("KALSHI_PRIVATE_KEY_PEM")
        key_id = os.environ.get("KALSHI_KEY_ID")
        if not pem or not key_id:
            raise RuntimeError("KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PEM required")
        priv = serialization.load_pem_private_key(pem.encode(), password=None)
        ts = str(int(time.time() * 1000))
        msg = (ts + method.upper() + path).encode()
        sig = priv.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "Content-Type": "application/json",
        }

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        ticker = signal.get("kalshi_ticker")
        if not ticker:
            raise RuntimeError("kalshi_ticker missing on signal — fetch markets via live_market.py")
        side = signal.get("side", "YES")
        price = float(signal["price"])
        yes_price_cents = max(1, min(99, int(round(price * 100))))
        count = max(1, int(notional / max(0.01, price)))

        body = {
            "ticker": ticker,
            "action": "buy",
            "side": "yes" if side == "YES" else "no",
            "type": "limit",
            "count": count,
            "yes_price": yes_price_cents,
            "client_order_id": uuid.uuid4().hex,
        }
        body_bytes = json.dumps(body).encode()
        path = "/trade-api/v2/portfolio/orders"
        headers = self._sign_headers("POST", path)

        async with httpx.AsyncClient(base_url=self.base, timeout=15.0) as client:
            r = await client.post("/portfolio/orders", content=body_bytes, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"kalshi order failed: {r.status_code} {r.text[:200]}")
        order = (r.json() or {}).get("order", {})
        return {
            "id": str(order.get("order_id") or f"kalshi_{uuid.uuid4().hex[:10]}"),
            "broker": self.name,
            "user_id": user_id,
            "engine": signal["engine"],
            "canonical_id": signal["canonical_id"],
            "market": signal["market"],
            "venue": "kalshi",
            "side": side,
            "price": round(price, 4),
            "contracts": count,
            "notional": round(notional, 2),
            "fees": round(notional * 0.07 * (1 - price), 4),  # Kalshi fee = 7% × p × (1−p), conservative
            "status": "submitted",
            "ts": time.time(),
            "raw": order,
        }


# ----------------- factory -----------------
def get_broker(name: str) -> BaseBroker:
    n = (name or "paper").lower()
    if n == "paper":
        return PaperBroker()
    if n == "polymarket":
        return PolymarketBroker()
    if n == "kalshi":
        return KalshiBroker()
    raise ValueError(f"Unknown broker: {name}")


def is_live_configured(name: str) -> bool:
    """True if all env vars for the named live broker are present."""
    n = (name or "").lower()
    if n == "polymarket":
        return all(
            os.environ.get(k)
            for k in (
                "POLYMARKET_PRIVATE_KEY",
                "POLYMARKET_API_KEY",
                "POLYMARKET_API_SECRET",
                "POLYMARKET_API_PASSPHRASE",
            )
        )
    if n == "kalshi":
        return bool(os.environ.get("KALSHI_KEY_ID") and os.environ.get("KALSHI_PRIVATE_KEY_PEM"))
    return False


def resolve_broker_for_user(user: dict) -> BaseBroker:
    """Pick the right broker for a user, falling back to paper if anything's misconfigured."""
    if not user.get("live_mode"):
        return PaperBroker()
    desired = (user.get("live_broker") or "").lower()
    if desired in ("polymarket", "kalshi") and is_live_configured(desired):
        return get_broker(desired)
    # Live requested but venue not ready → fall back, never crash a user trade.
    logger.warning(
        f"live_mode requested but '{desired}' not configured — falling back to paper",
        extra={"user_id": str(user.get("_id"))},
    )
    return PaperBroker()
