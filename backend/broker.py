"""Broker adapter pattern. Paper broker fully functional; venue brokers are stubs."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict


class BaseBroker:
    name = "base"

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        raise NotImplementedError

    async def settle(self, *, fill: dict, outcome: float) -> float:
        raise NotImplementedError


class PaperBroker(BaseBroker):
    """Simulated execution. Fills at quoted price + small slippage."""
    name = "paper"

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        slippage = 0.002 if signal.get("liquidity", 0.5) > 0.6 else 0.005
        side = signal.get("side", "YES")
        price = signal.get("price", 0.5)
        if side == "ARB":
            fill_price = price + slippage  # higher cost = worse
        else:
            fill_price = price + slippage if side == "YES" else price + slippage
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


class PolymarketBroker(BaseBroker):  # TODO: wire signing flow
    name = "polymarket"

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        raise NotImplementedError("Polymarket live execution requires wallet signing — not enabled.")


class KalshiBroker(BaseBroker):  # TODO: wire OAuth signing flow
    name = "kalshi"

    async def place_order(self, *, user_id: str, signal: dict, notional: float) -> Dict[str, Any]:
        raise NotImplementedError("Kalshi live execution requires API key auth — not enabled.")


def get_broker(name: str) -> BaseBroker:
    if name == "paper":
        return PaperBroker()
    if name == "polymarket":
        return PolymarketBroker()
    if name == "kalshi":
        return KalshiBroker()
    raise ValueError(f"Unknown broker: {name}")
