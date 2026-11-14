"""Broker adapters.

The PaperBroker is fully functional (no external venue). Polymarket and Kalshi
adapters implement real REST flows.

Credentials come from one of two sources, in priority order:
    1. Explicit `creds` dict passed at construction (loaded per-user from the
       Mongo `broker_connections` collection, decrypted with TRACKMIND_MASTER_KEY).
    2. Environment variables (operator-wide fallback).

If neither yields a complete credential set, the live broker raises
RuntimeError on `.place_order(...)` and the caller falls back to PaperBroker.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

import httpx

from fees import order_fee

logger = logging.getLogger("trackmind.broker")


class BaseBroker:
    name = "base"

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        raise NotImplementedError


# ----------------- Paper -----------------
class PaperBroker(BaseBroker):
    """Simulated execution at the signal's executable (ask) price.

    Signals are priced at top-of-book asks, so the only extra cost modelled is a
    small size-impact slippage for thin books. Fees use each venue's fee model;
    for a cross-venue arb the per-contract fee of both legs is applied.
    """
    name = "paper"

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        side = signal.get("side", "YES")
        price = float(signal.get("price", 0.5))
        slippage = 0.0 if signal.get("liquidity", 0.5) > 0.6 else 0.002
        fill_price = min(0.999 if side != "ARB" else 1.999, price + slippage)
        contracts = notional / max(0.01, fill_price)
        if side == "ARB":
            fees = float(signal.get("fees_per_contract", 0.0)) * contracts
        else:
            fees = order_fee(signal.get("venue", "polymarket"), fill_price, contracts)
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
            "fees": round(fees, 4),
            "status": "filled",
            "order_status": "filled",
            "ts": time.time(),
        }


# ----------------- Polymarket -----------------
POLYMARKET_REQUIRED = ("private_key", "api_key", "api_secret", "api_passphrase")


class PolymarketBroker(BaseBroker):
    """Place limit orders on Polymarket CLOB via the official REST endpoint.

    Required credentials (dict keys, or POLYMARKET_* env vars):
      private_key       — hex EOA private key funded with USDC.e on Polygon
      api_key           — L2 API credential (from `derive_api_key`)
      api_secret        — L2 API secret
      api_passphrase    — L2 API passphrase
    """
    name = "polymarket"

    def __init__(self, creds: Optional[Dict[str, str]] = None) -> None:
        self.host = os.environ.get("POLYMARKET_HOST", "https://clob.polymarket.com")
        self._creds = self._resolve_creds(creds or {})
        self._client: Any = None  # lazily-built py_clob_client.ClobClient

    @staticmethod
    def _resolve_creds(creds: Dict[str, str]) -> Dict[str, str]:
        return {
            "private_key": creds.get("private_key") or os.environ.get("POLYMARKET_PRIVATE_KEY", ""),
            "api_key": creds.get("api_key") or os.environ.get("POLYMARKET_API_KEY", ""),
            "api_secret": creds.get("api_secret") or os.environ.get("POLYMARKET_API_SECRET", ""),
            "api_passphrase": creds.get("api_passphrase") or os.environ.get("POLYMARKET_API_PASSPHRASE", ""),
        }

    def is_configured(self) -> bool:
        return all(self._creds.get(k) for k in POLYMARKET_REQUIRED)

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.is_configured():
            missing = [k for k in POLYMARKET_REQUIRED if not self._creds.get(k)]
            raise RuntimeError(f"polymarket missing credentials: {','.join(missing)}")
        try:
            from py_clob_client.client import ClobClient  # type: ignore
            from py_clob_client.clob_types import ApiCreds  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "py-clob-client not installed; run `pip install py-clob-client web3`"
            ) from e
        self._client = ClobClient(host=self.host, chain_id=137, key=self._creds["private_key"])
        self._client.set_api_creds(
            ApiCreds(
                api_key=self._creds["api_key"],
                api_secret=self._creds["api_secret"],
                api_passphrase=self._creds["api_passphrase"],
            )
        )
        return self._client

    async def test_connection(self) -> Dict[str, Any]:
        """Probe credentials without placing an order. Returns {ok, detail}."""
        import asyncio
        if not self.is_configured():
            missing = [k for k in POLYMARKET_REQUIRED if not self._creds.get(k)]
            return {"ok": False, "detail": f"missing: {','.join(missing)}"}

        def _probe() -> Dict[str, Any]:
            try:
                client = self._ensure_client()
                # `get_api_keys()` is an authenticated GET — confirms HMAC works.
                resp = client.get_api_keys()
                return {"ok": True, "detail": f"authenticated · {len(resp or [])} api keys"}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "detail": str(e)[:200]}

        return await asyncio.to_thread(_probe)

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        import asyncio
        token_ids = signal.get("polymarket_token_ids") or signal.get("clobTokenIds") or []
        if not token_ids or len(token_ids) < 2:
            raise RuntimeError("polymarket_token_ids missing on signal — fetch markets via live_market.py")
        side = signal.get("side", "YES")
        if side not in ("YES", "NO"):
            raise RuntimeError(f"polymarket cannot execute side {side!r} as a single order")
        token_id = token_ids[0] if side == "YES" else token_ids[1]
        price = max(0.01, min(0.99, float(signal["price"])))
        size_shares = round(notional / price, 2)

        def _submit() -> Dict[str, Any]:
            client = self._ensure_client()
            from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore
            args = OrderArgs(price=price, size=size_shares, side="BUY", token_id=token_id)
            signed = client.create_order(args)
            resp = client.post_order(signed, OrderType.GTC)
            return resp

        try:
            resp = await asyncio.to_thread(_submit)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"polymarket order failed: {e}") from e
        resp = resp or {}
        if resp.get("success") is False or resp.get("errorMsg"):
            raise RuntimeError(f"polymarket rejected order: {resp.get('errorMsg') or resp}")

        order_id = resp.get("orderID") or resp.get("orderId")
        if not order_id:
            raise RuntimeError(f"polymarket returned no order id: {resp}")
        # "matched" = filled immediately; "live"/"delayed" = resting on the book.
        order_status = "filled" if (resp.get("status") or "").lower() == "matched" else "submitted"
        fees = order_fee("polymarket", price, size_shares)
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
            "order_status": order_status,
            "ts": time.time(),
            "raw": resp,
        }


# ----------------- Kalshi -----------------
KALSHI_REQUIRED = ("key_id", "private_key_pem")


class KalshiBroker(BaseBroker):
    """Place limit orders on Kalshi via the v2 REST API.

    Required credentials (dict keys, or KALSHI_* env vars):
      key_id            — UUID from the dashboard
      private_key_pem   — full PEM contents of the downloaded private key
    """
    name = "kalshi"

    def __init__(self, creds: Optional[Dict[str, str]] = None) -> None:
        self.base = os.environ.get("KALSHI_BASE", "https://api.elections.kalshi.com/trade-api/v2")
        self._creds = self._resolve_creds(creds or {})

    @staticmethod
    def _resolve_creds(creds: Dict[str, str]) -> Dict[str, str]:
        return {
            "key_id": creds.get("key_id") or os.environ.get("KALSHI_KEY_ID", ""),
            "private_key_pem": creds.get("private_key_pem") or os.environ.get("KALSHI_PRIVATE_KEY_PEM", ""),
        }

    def is_configured(self) -> bool:
        return all(self._creds.get(k) for k in KALSHI_REQUIRED)

    def _sign_headers(self, method: str, path: str) -> Dict[str, str]:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        if not self.is_configured():
            missing = [k for k in KALSHI_REQUIRED if not self._creds.get(k)]
            raise RuntimeError(f"kalshi missing credentials: {','.join(missing)}")
        priv = serialization.load_pem_private_key(self._creds["private_key_pem"].encode(), password=None)
        ts = str(int(time.time() * 1000))
        msg = (ts + method.upper() + path).encode()
        sig = priv.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self._creds["key_id"],
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "Content-Type": "application/json",
        }

    async def test_connection(self) -> Dict[str, Any]:
        """Probe credentials by hitting an authenticated read endpoint."""
        if not self.is_configured():
            missing = [k for k in KALSHI_REQUIRED if not self._creds.get(k)]
            return {"ok": False, "detail": f"missing: {','.join(missing)}"}
        try:
            path = "/trade-api/v2/portfolio/balance"
            headers = self._sign_headers("GET", path)
            async with httpx.AsyncClient(base_url=self.base, timeout=10.0) as client:
                r = await client.get("/portfolio/balance", headers=headers)
            if r.status_code == 200:
                return {"ok": True, "detail": "authenticated"}
            return {"ok": False, "detail": f"http {r.status_code}: {r.text[:160]}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)[:200]}

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        ticker = signal.get("kalshi_ticker")
        if not ticker:
            raise RuntimeError("kalshi_ticker missing on signal — fetch markets via live_market.py")
        side = signal.get("side", "YES")
        if side not in ("YES", "NO"):
            raise RuntimeError(f"kalshi cannot execute side {side!r} as a single order")
        # `price` is the ask for the side being bought. Kalshi takes the limit in
        # cents on that side's own field: yes_price for YES, no_price for NO.
        price = float(signal["price"])
        price_cents = max(1, min(99, int(round(price * 100))))
        count = int(round(notional * 100)) // price_cents  # integer cents: no float floor error
        if count < 1:
            raise RuntimeError(f"notional ${notional:.2f} buys less than one contract at {price_cents}c")

        body = {
            "ticker": ticker,
            "action": "buy",
            "side": "yes" if side == "YES" else "no",
            "type": "limit",
            "count": count,
            ("yes_price" if side == "YES" else "no_price"): price_cents,
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
        if not order.get("order_id"):
            raise RuntimeError(f"kalshi returned no order id: {str(order)[:200]}")
        # "executed" = fully filled; "resting" = on the book; "canceled" = rejected/expired.
        k_status = (order.get("status") or "").lower()
        if k_status == "canceled":
            raise RuntimeError("kalshi order was canceled immediately")
        order_status = "filled" if k_status == "executed" else "submitted"
        exec_price = price_cents / 100.0
        return {
            "id": str(order["order_id"]),
            "broker": self.name,
            "user_id": user_id,
            "engine": signal["engine"],
            "canonical_id": signal["canonical_id"],
            "market": signal["market"],
            "venue": "kalshi",
            "side": side,
            "price": round(exec_price, 4),
            "contracts": count,
            "notional": round(count * exec_price, 2),
            "fees": order_fee("kalshi", exec_price, count),
            "status": "submitted",
            "order_status": order_status,
            "ts": time.time(),
            "raw": order,
        }


# ----------------- factory -----------------
def get_broker(name: str, creds: Optional[Dict[str, str]] = None) -> BaseBroker:
    n = (name or "paper").lower()
    if n == "paper":
        return PaperBroker()
    if n == "polymarket":
        return PolymarketBroker(creds)
    if n == "kalshi":
        return KalshiBroker(creds)
    raise ValueError(f"Unknown broker: {name}")


def env_is_live_configured(name: str) -> bool:
    """True if env-only credentials for the named live broker are present (no DB lookup)."""
    n = (name or "").lower()
    if n == "polymarket":
        return PolymarketBroker().is_configured()
    if n == "kalshi":
        return KalshiBroker().is_configured()
    return False


# Backwards-compat alias used by older callers — same behaviour as env-only check.
is_live_configured = env_is_live_configured


async def load_user_broker_creds(db, user_id: str, venue: str) -> Optional[Dict[str, str]]:
    """Look up & decrypt a user's stored credentials for `venue`. Returns None if absent."""
    from crypto import decrypt_payload
    row = await db.broker_connections.find_one({"user_id": str(user_id), "venue": venue})
    if not row or not row.get("encrypted_payload"):
        return None
    try:
        return decrypt_payload(row["encrypted_payload"])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"broker creds decrypt failed for {user_id}/{venue}: {e}")
        return None


async def resolve_broker_for_user_async(db, user: dict) -> BaseBroker:
    """Pick the right broker for a user, loading per-user creds from Mongo.

    Falls back to env, then to PaperBroker — never raises.
    """
    if not user.get("live_mode"):
        return PaperBroker()
    desired = (user.get("live_broker") or "").lower()
    if desired not in ("polymarket", "kalshi"):
        return PaperBroker()
    creds = await load_user_broker_creds(db, str(user["_id"]), desired)
    broker = get_broker(desired, creds=creds)
    # Final readiness check — be conservative.
    if hasattr(broker, "is_configured") and not broker.is_configured():  # type: ignore[attr-defined]
        logger.warning(
            f"live_mode='{desired}' but no creds (db or env) — falling back to paper",
            extra={"user_id": str(user.get("_id"))},
        )
        return PaperBroker()
    return broker


def resolve_broker_for_user(user: dict) -> BaseBroker:
    """Legacy sync resolver kept for tests. Uses env-only creds.

    New code MUST use `resolve_broker_for_user_async(db, user)`.
    """
    if not user.get("live_mode"):
        return PaperBroker()
    desired = (user.get("live_broker") or "").lower()
    if desired in ("polymarket", "kalshi"):
        b = get_broker(desired)
        if hasattr(b, "is_configured") and b.is_configured():  # type: ignore[attr-defined]
            return b
    logger.warning(
        f"sync resolver: '{desired}' not env-configured — falling back to paper",
        extra={"user_id": str(user.get("_id"))},
    )
    return PaperBroker()
