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
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from markets import Quote, market_store

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


def _slug_to_canonical(s: str) -> str:
    """Stable, length-bounded canonical id from a venue slug."""
    return (s or "unknown").lower().replace(" ", "_").replace("/", "_")[:64]


# ---- Polymarket -------------------------------------------------------------
async def _fetch_polymarket(client: httpx.AsyncClient) -> List[Quote]:
    """Pull active Polymarket markets via Gamma REST and shape into our Quote dataclass."""
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
    quotes: List[Quote] = []
    now = time.time()
    for m in data:
        q = _parse_polymarket_market(m, now)
        if q is not None:
            quotes.append(q)
    return quotes


def _parse_polymarket_market(m: Dict[str, Any], now: float) -> Optional[Quote]:
    """Shape a single Polymarket Gamma market dict into our Quote. Returns None
    if the market lacks a real two-sided price.

    Shared by the top-by-volume and per-sport fetchers below.
    """
    # outcomePrices typically a JSON string like '["0.62", "0.38"]'.
    prices = m.get("outcomePrices") or []
    if isinstance(prices, str):
        import json
        try:
            prices = json.loads(prices)
        except Exception:
            prices = []
    if not prices or len(prices) < 2:
        return None
    yes = _safe_float(prices[0])
    no = _safe_float(prices[1])
    if yes <= 0 or no <= 0:
        return None
    # Skip locked/resolved markets (0/1 or 1/0 — common for losing teams late in a series).
    if yes >= 0.995 or no >= 0.995:
        return None
    title = (m.get("question") or m.get("title") or m.get("slug") or "?")[:160]
    cid = _slug_to_canonical(m.get("conditionId") or m.get("slug") or title)
    liq = _safe_float(m.get("liquidity") or m.get("liquidityNum"))
    vol = _safe_float(m.get("volume24hr") or m.get("volume"))
    tokens = m.get("clobTokenIds") or []
    if isinstance(tokens, str):
        import json
        try:
            tokens = json.loads(tokens)
        except Exception:
            tokens = []
    q = Quote(
        venue="polymarket",
        canonical_id=cid,
        title=title,
        yes=round(yes, 4),
        no=round(no, 4),
        volume_24h=vol,
        liquidity=min(1.0, liq / 10_000) if liq else 0.3,
        updated_at=now,
        history=[yes],
    )
    setattr(q, "polymarket_condition_id", m.get("conditionId"))
    setattr(q, "polymarket_token_ids", tokens)
    return q


async def _fetch_polymarket_sport(client: httpx.AsyncClient, tag_slug: str) -> List[Quote]:
    """Fetch markets for a single sports tag via Gamma /events.

    The /events endpoint actually filters by tag_slug (the /markets one returns
    the global top-100 regardless of tag_slug). Each event contains an embedded
    `markets` array which we flatten + parse via _parse_polymarket_market.
    Markets are sorted by event volume24hr descending so we get the most
    actively-traded games first. Capped at MAX_MARKETS_PER_SPORT.
    """
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
            # Tag the quote with its sports category so the UI / engines can
            # color-code or filter later.
            setattr(q, "sport_tag", tag_slug)
            quotes.append(q)
            if len(quotes) >= MAX_MARKETS_PER_SPORT:
                return quotes
    return quotes


# ---- Kalshi -----------------------------------------------------------------
async def _fetch_kalshi(client: httpx.AsyncClient) -> List[Quote]:
    """Pull open Kalshi markets via public v2 API.

    Kalshi switched to dollar-decimal strings (no _cents fields) in mid-2025.
    We read ``yes_bid_dollars`` / ``yes_ask_dollars`` and convert.
    """
    url = f"{KALSHI_BASE}/markets"
    params = {"status": "open", "limit": str(MAX_MARKETS_PER_VENUE)}
    r = await client.get(url, params=params, timeout=8.0)
    r.raise_for_status()
    data = r.json()
    rows = data.get("markets") or []
    if not isinstance(rows, list):
        return []
    quotes: List[Quote] = []
    now = time.time()
    for m in rows:
        # Skip multivariate placeholders with no real pricing.
        yes_bid = _safe_float(m.get("yes_bid_dollars"))
        yes_ask = _safe_float(m.get("yes_ask_dollars"))
        last = _safe_float(m.get("last_price_dollars"))
        # Prefer mid-of-book; fall back to last price.
        if yes_bid > 0 and yes_ask > 0:
            mid_yes = (yes_bid + yes_ask) / 2.0
        elif yes_ask > 0:
            mid_yes = yes_ask  # one-sided book
        elif last > 0:
            mid_yes = last
        else:
            continue
        if mid_yes <= 0 or mid_yes >= 1:
            continue
        mid_no = 1.0 - mid_yes
        title = (m.get("title") or m.get("yes_sub_title") or m.get("ticker") or "?")[:160]
        cid = _slug_to_canonical(m.get("ticker") or m.get("event_ticker") or title)
        liq = _safe_float(m.get("liquidity_dollars"))
        vol = _safe_float(m.get("volume_24h_fp")) or _safe_float(m.get("volume_fp"))
        q = Quote(
            venue="kalshi",
            canonical_id=cid,
            title=title,
            yes=round(mid_yes, 4),
            no=round(mid_no, 4),
            volume_24h=vol,
            liquidity=min(1.0, liq / 10_000) if liq else 0.3,
            updated_at=now,
            history=[mid_yes],
        )
        setattr(q, "kalshi_ticker", m.get("ticker"))
        setattr(q, "kalshi_event_ticker", m.get("event_ticker"))
        quotes.append(q)
    return quotes


# ---- canonical id matching across venues -----------------------------------
def _match_cross_venue(polys: List[Quote], kalshis: List[Quote]) -> None:
    """If a Polymarket and a Kalshi market look like they're about the same event,
    rewrite Kalshi's canonical_id to match Polymarket's so the Micro (arb) engine can pair them.

    Naive keyword overlap — good enough for MVP; replace with a labelled mapping later.
    """
    def tokens(t: str) -> set:
        return {w.lower() for w in t.split() if len(w) > 2}

    for k in kalshis:
        kt = tokens(k.title)
        best: Optional[Quote] = None
        best_score = 0
        for p in polys:
            pt = tokens(p.title)
            overlap = len(kt & pt)
            if overlap >= 3 and overlap > best_score:
                best, best_score = p, overlap
        if best:
            k.canonical_id = best.canonical_id


# ---- ingest loop ------------------------------------------------------------
_running = False


async def _refresh_once() -> None:
    """Single fetch+swap cycle. Failures are absorbed silently with a warning.

    Pulls Polymarket markets from two sources concurrently:
      1. Top-N by volume (existing — politics/crypto-heavy).
      2. Per-sport via /events?tag_slug=<sport> for NBA/NFL/MLB/NHL/NCAA F&B.
    Deduplicates by (venue, canonical_id) and respects POLYMARKET_HARD_CAP.
    """
    async with httpx.AsyncClient(headers={"User-Agent": "TRACKMIND/1.0"}) as client:
        # Top-by-volume (Polymarket + Kalshi) in parallel.
        top_results = await asyncio.gather(
            _fetch_polymarket(client),
            _fetch_kalshi(client),
            return_exceptions=True,
        )
        polys_top, kalshis = top_results
        # Per-sport Polymarket fetches in parallel.
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

    # Combine: top-by-volume first (preferred), then sports in declared order.
    # Dedup by canonical_id — sports markets that ARE in the top-30 won't be double-counted.
    seen: set = set()
    polys: List[Quote] = []
    sport_counts: Dict[str, int] = {tag: 0 for tag in SPORTS_TAGS}
    for q in polys_top:
        if q.canonical_id in seen:
            continue
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
        # Total venue outage: keep prior snapshot.
        return

    _match_cross_venue(polys, kalshis)

    # Swap into the in-memory store. We REPLACE rather than merge so stale markets disappear.
    with market_store._lock:  # safe enough; module-level singleton  # noqa: SLF001
        prev = {(q.venue, q.canonical_id): q for q in market_store._quotes.values()}  # noqa: SLF001
        new_quotes: Dict[tuple, Quote] = {}
        for q in (polys + kalshis):
            prior = prev.get((q.venue, q.canonical_id))
            if prior:
                q.history = (prior.history + [q.yes])[-120:]
            new_quotes[(q.venue, q.canonical_id)] = q
        if new_quotes:
            market_store._quotes = new_quotes  # noqa: SLF001
    sports_summary = " ".join(f"{tag}={n}" for tag, n in sport_counts.items() if n > 0)
    logger.info(
        "live_ingest_refreshed",
        extra={
            "polymarket": len(polys),
            "kalshi": len(kalshis),
            "total": len(polys) + len(kalshis),
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
