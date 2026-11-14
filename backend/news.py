"""Real news for the Alpha engine — GDELT DOC 2.0 (free, no API key).

Design constraints:
  * GDELT asks for at most one request every 5 seconds, so engines never call it
    directly. ``news_loop`` refreshes headlines for the top live markets in the
    background (one request per ``GDELT_MIN_INTERVAL_SEC``) into an in-memory
    cache; Alpha only reads the cache.
  * GDELT returns articles, not a view on any market. Whether a headline is
    relevant to a market — and which way it points — is judged by the LLM in
    ``engines/alpha.py``. Without an LLM key, Alpha emits nothing from real news.

Enable with ``ALPHA_NEWS_SOURCE=gdelt``.

ArtList response shape (format=json)::

    {"articles": [{"url": "...", "url_mobile": "", "title": "...",
                   "seendate": "20261114T031500Z", "socialimage": "...",
                   "domain": "reuters.com", "language": "English",
                   "sourcecountry": "United States"}]}
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("trackmind.news")

GDELT_DOC_URL = os.environ.get("GDELT_DOC_URL", "https://api.gdeltproject.org/api/v2/doc/doc")
GDELT_MIN_INTERVAL_SEC = float(os.environ.get("GDELT_MIN_INTERVAL_SEC", "6"))
NEWS_TTL_SEC = float(os.environ.get("NEWS_TTL_SEC", "900"))          # GDELT updates every 15 min
NEWS_TIMESPAN = os.environ.get("NEWS_TIMESPAN", "24h")
NEWS_MAX_ARTICLES = int(os.environ.get("NEWS_MAX_ARTICLES", "12"))
ALPHA_NEWS_MARKETS = int(os.environ.get("ALPHA_NEWS_MARKETS", "40"))  # markets refreshed per cycle


def news_source() -> Optional[str]:
    v = (os.environ.get("ALPHA_NEWS_SOURCE") or "").strip().lower()
    return v or None


# ---------------- query building ----------------
_STOP = {
    "will", "the", "and", "for", "with", "this", "that", "from", "than", "into", "over", "under",
    "above", "below", "before", "after", "next", "end", "yes", "who", "what", "which", "win",
    "wins", "price", "market", "by", "of", "in", "on", "at", "to", "a", "an", "be", "is", "are",
    "does", "do", "any", "more", "less", "most", "least", "reach", "hit", "close", "closes",
    "between", "during", "within", "since", "until", "least", "least", "vs", "o/u", "team",
    "total", "spread", "game", "match", "season", "2025", "2026", "2027", "2028",
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "sept", "oct", "nov", "dec", "week", "month", "year", "quarter", "today", "tomorrow",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "tonight",
}


def build_gdelt_query(title: str, max_terms: int = 4) -> Optional[str]:
    """Search terms from a market title. Proper nouns first; GDELT rejects very short terms."""
    words = re.findall(r"\$?[A-Za-z0-9][A-Za-z0-9'&.-]*", title or "")
    seen, proper, other = set(), [], []
    for w in words:
        lw = w.lower().lstrip("$").strip(".'-")
        if lw in _STOP or len(lw) < 3 or lw in seen or re.fullmatch(r"[\d.,]+", lw):
            continue  # stopwords, very short terms (GDELT rejects them) and bare numbers
        seen.add(lw)
        (proper if w[0].isupper() else other).append(lw)
    terms = (proper + other)[:max_terms]
    if len(terms) < 2:
        return None  # too vague to search meaningfully
    return " ".join(terms) + " sourcelang:english"


# ---------------- parsing ----------------
def _seen_ts(s: Any) -> Optional[float]:
    try:
        return datetime.strptime(str(s), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def parse_gdelt_artlist(data: Any) -> List[dict]:
    """Normalise an ArtList JSON response into [{title, url, domain, seen_ts}], newest first, deduped by title."""
    rows = (data or {}).get("articles") if isinstance(data, dict) else None
    out, titles = [], set()
    for a in rows or []:
        title = (a.get("title") or "").strip()
        url = a.get("url")
        if not title or not url:
            continue
        key = re.sub(r"\W+", " ", title.lower()).strip()
        if key in titles:
            continue  # syndicated copies of the same story
        titles.add(key)
        out.append({
            "title": title[:240],
            "url": url,
            "domain": a.get("domain"),
            "seen_ts": _seen_ts(a.get("seendate")),
            "source": "gdelt",
        })
    out.sort(key=lambda h: h["seen_ts"] or 0, reverse=True)
    return out


# ---------------- cache + fetch loop ----------------
class NewsCache:
    def __init__(self) -> None:
        self._rows: Dict[str, dict] = {}  # canonical_id -> {fetched_at, query, headlines}
        self.last_error: Optional[str] = None
        self.requests = 0

    def get(self, canonical_id: str, now: Optional[float] = None, max_age: float = NEWS_TTL_SEC * 2) -> Optional[List[dict]]:
        row = self._rows.get(canonical_id)
        if not row:
            return None
        if (now or time.time()) - row["fetched_at"] > max_age:
            return None  # too stale to act on
        return row["headlines"]

    def put(self, canonical_id: str, query: str, headlines: List[dict], now: Optional[float] = None) -> None:
        self._rows[canonical_id] = {"fetched_at": now or time.time(), "query": query, "headlines": headlines}

    def age(self, canonical_id: str, now: Optional[float] = None) -> Optional[float]:
        row = self._rows.get(canonical_id)
        return None if not row else (now or time.time()) - row["fetched_at"]

    def status(self) -> dict:
        return {"markets_cached": len(self._rows), "requests": self.requests, "last_error": self.last_error}


news_cache = NewsCache()


async def fetch_gdelt(client, query: str) -> List[dict]:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(NEWS_MAX_ARTICLES * 2),
        "timespan": NEWS_TIMESPAN,
        "sort": "datedesc",
    }
    r = await client.get(GDELT_DOC_URL, params=params, timeout=15.0)
    r.raise_for_status()
    # GDELT answers some errors (e.g. "phrase too short") as 200 + plain text.
    if "json" not in (r.headers.get("content-type") or "") and not r.text.lstrip().startswith("{"):
        raise RuntimeError(f"gdelt: {r.text.strip()[:160]}")
    return parse_gdelt_artlist(r.json())[:NEWS_MAX_ARTICLES]


def markets_to_refresh(store, cache: NewsCache, now: float, limit: int = ALPHA_NEWS_MARKETS) -> list:
    """Top Polymarket markets by 24h volume whose news is missing or older than the TTL."""
    quotes = [q for q in store.all() if q.venue == "polymarket" and (q.end_ts is None or q.end_ts > now)]
    quotes.sort(key=lambda q: q.volume_24h or 0, reverse=True)
    due = []
    for q in quotes[:limit]:
        age = cache.age(q.canonical_id, now)
        if age is None or age > NEWS_TTL_SEC:
            due.append(q)
    return due


async def news_loop(store, cache: NewsCache = news_cache) -> None:
    """Background refresher. One GDELT request per GDELT_MIN_INTERVAL_SEC, never faster."""
    import httpx

    logger.info("news loop starting (gdelt)")
    async with httpx.AsyncClient(headers={"User-Agent": "TRACKMIND/1.0"}) as client:
        while True:
            try:
                if store.mode != "live":
                    await asyncio.sleep(30)
                    continue
                due = markets_to_refresh(store, cache, time.time())
                if not due:
                    await asyncio.sleep(30)
                    continue
                for q in due:
                    query = build_gdelt_query(q.title)
                    if query:
                        try:
                            cache.requests += 1
                            cache.put(q.canonical_id, query, await fetch_gdelt(client, query))
                        except Exception as e:  # noqa: BLE001
                            cache.last_error = f"{type(e).__name__}: {str(e)[:160]}"
                            logger.warning(f"gdelt fetch failed for {q.canonical_id}: {cache.last_error}")
                    else:
                        cache.put(q.canonical_id, "", [])  # nothing searchable; don't retry every cycle
                    await asyncio.sleep(GDELT_MIN_INTERVAL_SEC)
            except Exception as e:  # noqa: BLE001 — never let the loop die
                logger.warning(f"news loop error: {e}")
                await asyncio.sleep(30)
