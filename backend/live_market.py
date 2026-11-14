"""Live read-only market data ingest for Polymarket + Kalshi.

This module is best-effort: if either venue is unreachable, we keep the previous
in-memory snapshot so the engines never starve.

Polymarket — Gamma REST (no auth required for read-only):
    https://gamma-api.polymarket.com/markets?closed=false&limit=200&order=volume&ascending=false

Kalshi — public elections trading API (no auth required for market data):
    https://api.elections.kalshi.com/trade-api/v2/markets?status=open&limit=200

Both endpoints are stable, public, and unmetered (no API key needed).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from markets import Quote, market_store
from pairs import load_verified_pairs, resolve_pairs, suggest_pairs

logger = logging.getLogger("trackmind.live_market")

POLYMARKET_GAMMA = os.environ.get("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com")
KALSHI_BASE = os.environ.get("KALSHI_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2")

# How many markets to keep per venue. More = richer signals but more LLM calls.
MAX_MARKETS_PER_VENUE = int(os.environ.get("LIVE_MARKETS_LIMIT", "30"))

# Per-sport cap — events endpoint is fetched for each tag, top markets are picked.
MAX_MARKETS_PER_SPORT = int(os.environ.get("LIVE_SPORTS_PER_TAG", "10"))

# Polymarket sports tags to ingest. Order matters: earlier sports get priority on
# the global cap. Each tag → /events?tag_slug=<tag> → flatten embedded markets.
SPORTS_TAGS: List[str] = [
    "nba", "nfl", "mlb", "nhl",
    "ncaaf", "college-football",
    "ncaab", "college-basketball",
]

# Global hard cap on Polymarket markets (top-by-volume + all sports combined,
# deduped). Keeps LLM cost bounded.
POLYMARKET_HARD_CAP = int(os.environ.get("LIVE_POLY_HARD_CAP", "100"))

# Tunable: live ingest refresh cadence (seconds).
LIVE_REFRESH_SEC = int(os.environ.get("LIVE_REFRESH_SEC", "15"))

# Master switch. Defaults to False so existing mock behaviour is preserved
# unless the operator explicitly opts in. Engines + UI work identically.
LIVE_INGEST_ENABLED = os.environ.get("LIVE_INGEST_ENABLED", "false").lower() in {"1", "true", "yes"}


# ---- helpers ----------------------------------------------------------------
def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _opt_price(x: Any) -> Optional[float]:
    """Parse a 0..1 price; None if missing / out of range."""
    v = _safe_float(x, -1.0)
    return v if 0.0 < v < 1.0 else None


def _json_list(x: Any) -> list:
    if isinstance(x, list):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            return v if isinstance(v, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


def _iso_to_ts(s: Any) -> Optional[float]:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _slug_to_canonical(s: str) -> str:
    """Stable, length-bounded canonical id from a venue slug."""
    return (s or "unknown").lower().replace(" ", "_").replace("/", "_")[:80]


# ---- Polymarket -------------------------------------------------------------
async def _fetch_polymarket(client: httpx.AsyncClient) -> List[Quote]:
    """Pull active Polymarket markets via Gamma REST and shape into Quotes."""
    url = f"{POLYMARKET_GAMMA}/markets"
    params = {
        "closed": "false",
        "active": "true",
        "limit": str(MAX_MARKETS_PER_VENUE),
        "order": "volume",
        "ascending": "false",
    }
    r = await client.get(url, params=params, timeout=8.0)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "markets" in data:
        data = data["markets"]
    if not isinstance(data, list):
        return []
    now = time.time()
    return [q for q in (_parse_polymarket_market(m, now) for m in data) if q is not None]


def _parse_polymarket_market(m: Dict[str, Any], now: float) -> Optional[Quote]:
    """Shape one Gamma market into a Quote. None if not a live two-sided binary.

    Gamma reports ``bestBid`` / ``bestAsk`` for outcome 0 (the "YES" token). On a
    binary CLOB the NO book mirrors it: NO ask = 1 − YES bid, NO bid = 1 − YES ask.
    """
    prices = _json_list(m.get("outcomePrices"))
    tokens = _json_list(m.get("clobTokenIds"))
    outcomes = _json_list(m.get("outcomes")) or ["Yes", "No"]
    if len(prices) != 2 or len(tokens) != 2:
        return None  # multi-outcome / malformed — not supported as a binary
    yes = _safe_float(prices[0])
    no = _safe_float(prices[1])
    if yes <= 0 or no <= 0 or yes >= 0.995 or no >= 0.995:
        return None  # resolved / locked
    if m.get("acceptingOrders") is False or m.get("enableOrderBook") is False:
        return None
    cond = (m.get("conditionId") or "").lower()
    if not cond:
        return None
    yes_bid = _opt_price(m.get("bestBid"))
    yes_ask = _opt_price(m.get("bestAsk"))
    if yes_bid is not None and yes_ask is not None and yes_bid < yes_ask:
        mid_yes = (yes_bid + yes_ask) / 2.0
    else:
        mid_yes = yes
    title = (m.get("question") or m.get("title") or m.get("slug") or "?")[:160]
    liq = _safe_float(m.get("liquidityNum") or m.get("liquidity"))
    vol = _safe_float(m.get("volume24hr") or m.get("volume24hrClob") or m.get("volume"))
    return Quote(
        venue="polymarket",
        canonical_id=_slug_to_canonical(cond),
        title=title,
        yes=round(mid_yes, 4),
        no=round(1.0 - mid_yes, 4),
        volume_24h=vol,
        liquidity=min(1.0, liq / 10_000) if liq else 0.3,
        updated_at=now,
        history=[],
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=round(1.0 - yes_ask, 4) if yes_ask is not None else None,
        no_ask=round(1.0 - yes_bid, 4) if yes_bid is not None else None,
        polymarket_condition_id=cond,
        polymarket_token_ids=[str(t) for t in tokens],
        outcomes=[str(o) for o in outcomes[:2]],
        end_ts=_iso_to_ts(m.get("endDate")),
        source="live",
    )


async def _fetch_polymarket_sport(client: httpx.AsyncClient, tag_slug: str) -> List[Quote]:
    """Fetch markets for one sports tag via Gamma /events (which honours tag_slug)."""
    url = f"{POLYMARKET_GAMMA}/events"
    params = {
        "closed": "false",
        "active": "true",
        "tag_slug": tag_slug,
        "limit": "50",
        "order": "volume24hr",
        "ascending": "false",
    }
    try:
        r = await client.get(url, params=params, timeout=8.0)
        r.raise_for_status()
        events = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"polymarket sport fetch failed for {tag_slug}: {e}")
        return []
    if isinstance(events, dict):
        events = events.get("events", [])
    if not isinstance(events, list):
        return []
    quotes: List[Quote] = []
    now = time.time()
    for ev in events:
        for m in (ev.get("markets") or []):
            q = _parse_polymarket_market(m, now)
            if q is None:
                continue
            q.sport_tag = tag_slug
            quotes.append(q)
            if len(quotes) >= MAX_MARKETS_PER_SPORT:
                return quotes
    return quotes


# ---- Kalshi -----------------------------------------------------------------
def _parse_kalshi_market(m: Dict[str, Any], now: float) -> Optional[Quote]:
    """Shape one Kalshi v2 market (dollar-decimal fields) into a Quote."""
    yes_bid = _opt_price(m.get("yes_bid_dollars"))
    yes_ask = _opt_price(m.get("yes_ask_dollars"))
    no_bid = _opt_price(m.get("no_bid_dollars"))
    no_ask = _opt_price(m.get("no_ask_dollars"))
    # Derive the missing side of the book from the mirror if needed.
    if no_ask is None and yes_bid is not None:
        no_ask = round(1.0 - yes_bid, 4)
    if no_bid is None and yes_ask is not None:
        no_bid = round(1.0 - yes_ask, 4)
    last = _opt_price(m.get("last_price_dollars"))
    if yes_bid is not None and yes_ask is not None:
        mid_yes = (yes_bid + yes_ask) / 2.0
    elif last is not None:
        mid_yes = last
    else:
        return None  # no tradable price
    ticker = m.get("ticker")
    if not ticker:
        return None
    title = (m.get("title") or m.get("yes_sub_title") or ticker)[:160]
    sub = m.get("yes_sub_title")
    if sub and sub not in title:
        title = f"{title} — {sub}"[:160]
    liq = _safe_float(m.get("liquidity_dollars"))
    if not liq:
        # `liquidity_dollars` was dropped from the v2 market object; approximate
        # top-of-book depth in dollars from the quoted sizes instead.
        sizes = _safe_float(m.get("yes_bid_size_fp")) + _safe_float(m.get("yes_ask_size_fp"))
        liq = sizes * mid_yes
    vol = _safe_float(m.get("volume_24h_fp")) or _safe_float(m.get("volume_24h")) or _safe_float(m.get("volume_fp"))
    return Quote(
        venue="kalshi",
        canonical_id=_slug_to_canonical(ticker),
        title=title,
        yes=round(mid_yes, 4),
        no=round(1.0 - mid_yes, 4),
        volume_24h=vol,
        liquidity=min(1.0, liq / 10_000) if liq else 0.3,
        updated_at=now,
        history=[],
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        kalshi_ticker=ticker,
        kalshi_event_ticker=m.get("event_ticker"),
        end_ts=_iso_to_ts(m.get("close_time")),
        source="live",
    )


async def _fetch_kalshi(client: httpx.AsyncClient) -> List[Quote]:
    url = f"{KALSHI_BASE}/markets"
    params = {"status": "open", "limit": str(MAX_MARKETS_PER_VENUE), "mve_filter": "exclude"}
    r = await client.get(url, params=params, timeout=8.0)
    r.raise_for_status()
    rows = (r.json() or {}).get("markets") or []
    if not isinstance(rows, list):
        return []
    now = time.time()
    return [q for q in (_parse_kalshi_market(m, now) for m in rows) if q is not None]


# ---- resolution lookups (used by paper settlement) ---------------------------
def parse_polymarket_resolution(m: Dict[str, Any]) -> Optional[str]:
    """Outcome of one Gamma market dict: "YES" (outcome 0 won), "NO" (outcome 1), "VOID", or None.

    "YES"/"NO" are positional: outcome 0 / outcome 1, whatever their labels
    (e.g. "Over"/"Under", team names). That matches token_ids[0] / token_ids[1].
    """
    if not m.get("closed"):
        return None
    uma = (m.get("umaResolutionStatus") or "").lower()
    if uma and uma != "resolved":
        return None  # proposed / disputed — prices aren't final yet
    prices = [_safe_float(p) for p in _json_list(m.get("outcomePrices"))]
    if len(prices) != 2:
        return None
    if prices[0] >= 0.99 and prices[1] <= 0.01:
        return "YES"
    if prices[1] >= 0.99 and prices[0] <= 0.01:
        return "NO"
    if abs(prices[0] - 0.5) < 0.01 and abs(prices[1] - 0.5) < 0.01:
        return "VOID"
    return None


def parse_kalshi_resolution(m: Dict[str, Any]) -> Optional[str]:
    """Outcome of one Kalshi v2 market dict once settled/finalized, else None."""
    if (m.get("status") or "").lower() not in ("settled", "finalized", "determined"):
        return None
    result = (m.get("result") or "").lower()
    if result == "yes":
        return "YES"
    if result == "no":
        return "NO"
    if result in ("void", "voided"):
        return "VOID"
    return None


async def fetch_polymarket_resolution(client: httpx.AsyncClient, condition_id: str) -> Optional[str]:
    r = await client.get(f"{POLYMARKET_GAMMA}/markets", params={"condition_ids": condition_id, "closed": "true"}, timeout=8.0)
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else (data or {}).get("markets", [])
    for m in rows or []:
        if (m.get("conditionId") or "").lower() == (condition_id or "").lower():
            return parse_polymarket_resolution(m)
    return None


async def fetch_kalshi_resolution(client: httpx.AsyncClient, ticker: str) -> Optional[str]:
    r = await client.get(f"{KALSHI_BASE}/markets/{ticker}", timeout=8.0)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return parse_kalshi_resolution((r.json() or {}).get("market") or {})


# ---- ingest loop ------------------------------------------------------------
_running = False
# Latest unverified cross-venue candidates, for operator review via the API.
pair_candidates: List[dict] = []


async def _refresh_once() -> None:
    """One fetch + swap cycle. Venue failures keep the previous snapshot."""
    global pair_candidates
    async with httpx.AsyncClient(headers={"User-Agent": "TRACKMIND/1.0"}) as client:
        top_results = await asyncio.gather(
            _fetch_polymarket(client),
            _fetch_kalshi(client),
            return_exceptions=True,
        )
        polys_top, kalshis = top_results
        sport_results = await asyncio.gather(
            *[_fetch_polymarket_sport(client, tag) for tag in SPORTS_TAGS],
            return_exceptions=True,
        )

    if isinstance(polys_top, Exception):
        logger.warning(f"polymarket fetch failed: {polys_top}")
        polys_top = []
    if isinstance(kalshis, Exception):
        logger.warning(f"kalshi fetch failed: {kalshis}")
        kalshis = []

    seen: set = set()
    polys: List[Quote] = []
    sport_counts: Dict[str, int] = {tag: 0 for tag in SPORTS_TAGS}
    for q in polys_top:
        if q.canonical_id not in seen:
            seen.add(q.canonical_id)
            polys.append(q)
    for tag, result in zip(SPORTS_TAGS, sport_results):
        if isinstance(result, Exception):
            logger.warning(f"polymarket sport {tag} failed: {result}")
            continue
        for q in result:
            if len(polys) >= POLYMARKET_HARD_CAP:
                break
            if q.canonical_id in seen:
                continue
            seen.add(q.canonical_id)
            polys.append(q)
            sport_counts[tag] += 1

    if not polys and not kalshis:
        return  # total outage: keep prior snapshot

    # If one venue failed this cycle, carry its previous quotes forward so a
    # transient outage on one side doesn't wipe it from the store.
    if not polys or not kalshis:
        prev = market_store.all() if market_store.mode == "live" else []
        if not polys:
            polys = [q for q in prev if q.venue == "polymarket"]
        if not kalshis:
            kalshis = [q for q in prev if q.venue == "kalshi"]

    pairs = resolve_pairs(polys, kalshis, load_verified_pairs())
    pair_candidates = suggest_pairs(polys, kalshis)
    stored = market_store.set_live_snapshot(polys + kalshis, pairs=pairs)

    sports_summary = " ".join(f"{tag}={n}" for tag, n in sport_counts.items() if n > 0)
    logger.info(
        "live_ingest_refreshed",
        extra={
            "polymarket": len(polys),
            "kalshi": len(kalshis),
            "total": stored,
            "verified_pairs": len(pairs),
            "pair_candidates": len(pair_candidates),
            "sports": sports_summary or None,
        },
    )


async def ingest_forever() -> None:
    """Background loop. Started by the FastAPI lifespan only when LIVE_INGEST_ENABLED."""
    global _running
    if _running:
        return
    _running = True
    logger.info(f"live ingest starting (every {LIVE_REFRESH_SEC}s)")
    while True:
        try:
            await _refresh_once()
        except Exception as e:  # noqa: BLE001 — never let this loop die
            logger.warning(f"live ingest error: {e}")
        await asyncio.sleep(LIVE_REFRESH_SEC)
