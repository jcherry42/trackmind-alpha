"""Market data model + in-memory store for TRACKMIND.

Two data modes:

* ``mock`` (default) — a curated 12-market demo catalog listed on both venues.
  A mean-reverting random walk moves prices so the UI has something to show.
  Mock data is for demos only; nothing it produces says anything about real edge.

* ``live`` — snapshots from Polymarket Gamma + Kalshi v2 (see ``live_market.py``).
  In live mode the store is NEVER perturbed: ``tick()`` is a no-op, history is
  built only from real snapshots, and every quote carries the venue identifiers
  needed to route an order.

Prices: ``yes`` / ``no`` are mid-prices (used for display and fair value).
``yes_ask`` / ``no_ask`` are what you would actually pay to BUY that side and
``yes_bid`` / ``no_bid`` what you would receive selling it. Engines must price
edges against asks — a mid-price edge is not tradable.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, Iterable, List, Optional, Tuple


VENUES = ("polymarket", "kalshi")
HISTORY_LEN = 240


# Curated catalog of equivalent markets across venues (MOCK MODE ONLY).
# (canonical_id, polymarket_title, kalshi_title, base_yes_prob, vol_score)
_MARKET_CATALOG: List[Tuple[str, str, str, float, float]] = [
    ("btc_100k_eom",     "BTC closes above $100K end of month",     "BTC ABOVE 100K MONTH-END",       0.58, 0.85),
    ("btc_1h_above",     "BTC 1H candle above $100K",               "BTC 1H > 100K",                  0.52, 0.95),
    ("eth_5k_q",         "ETH above $5,000 by quarter end",         "ETH > 5K QUARTER",               0.34, 0.70),
    ("fed_rate_cut",     "Fed cuts rates at next FOMC",             "FED CUTS NEXT FOMC",             0.41, 0.80),
    ("cpi_above_3",      "CPI YoY above 3.0% next print",           "CPI > 3.0 NEXT PRINT",           0.46, 0.65),
    ("election_swing",   "Swing-state poll lead flips this week",   "SWING FLIP THIS WEEK",           0.29, 0.55),
    ("nvda_earnings",    "NVDA beats EPS estimate next earnings",   "NVDA EPS BEAT",                  0.62, 0.75),
    ("oscar_best_pic",   "Best Picture goes to top contender",      "BEST PIC TOP CONTENDER",         0.48, 0.40),
    ("nfl_sb_winner",    "AFC team wins next Super Bowl",           "AFC WINS SUPER BOWL",            0.51, 0.60),
    ("recession_24mo",   "US recession declared in next 24 months", "RECESSION NEXT 24M",             0.37, 0.50),
    ("oil_above_90",     "WTI crude closes above $90 this month",   "WTI > 90 MONTH",                 0.33, 0.55),
    ("spx_ath_q",        "S&P500 hits new ATH this quarter",        "SPX NEW ATH QUARTER",            0.55, 0.72),
]


@dataclass
class Quote:
    venue: str
    canonical_id: str
    title: str
    yes: float                      # YES mid
    no: float                       # NO mid
    volume_24h: float
    liquidity: float                # normalised 0..1 depth score
    updated_at: float
    history: List[float] = field(default_factory=list)  # YES mids, oldest first
    history_ts: List[float] = field(default_factory=list)
    # Top of book (None when the venue didn't report it).
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    no_bid: Optional[float] = None
    no_ask: Optional[float] = None
    # Execution identifiers.
    polymarket_condition_id: Optional[str] = None
    polymarket_token_ids: List[str] = field(default_factory=list)  # [outcome0, outcome1]
    kalshi_ticker: Optional[str] = None
    kalshi_event_ticker: Optional[str] = None
    # Metadata.
    outcomes: List[str] = field(default_factory=lambda: ["Yes", "No"])
    end_ts: Optional[float] = None
    sport_tag: Optional[str] = None
    source: str = "mock"            # "mock" | "live"


# ---------- pricing helpers ----------
def ask_for(q: Quote, side: str) -> float:
    """Price to BUY ``side`` (YES/NO). Falls back to mid when no book is known."""
    if side == "YES":
        return q.yes_ask if q.yes_ask is not None else q.yes
    return q.no_ask if q.no_ask is not None else q.no


def bid_for(q: Quote, side: str) -> float:
    """Price to SELL ``side`` (YES/NO). Falls back to mid when no book is known."""
    if side == "YES":
        return q.yes_bid if q.yes_bid is not None else q.yes
    return q.no_bid if q.no_bid is not None else q.no


def mid_for(q: Quote, side: str) -> float:
    return q.yes if side == "YES" else q.no


def half_spread(q: Quote, side: str) -> float:
    return max(0.0, ask_for(q, side) - mid_for(q, side))


def execution_refs(q: Quote) -> dict:
    """Identifiers a broker needs to route an order for this quote.

    Engines merge this into every signal so `/trade/approve` can execute live.
    """
    return {
        "venue": q.venue,
        "polymarket_condition_id": (q.polymarket_condition_id or "").lower() or None,
        "polymarket_token_ids": list(q.polymarket_token_ids or []),
        "kalshi_ticker": q.kalshi_ticker,
        "outcomes": list(q.outcomes or ["Yes", "No"]),
        "end_ts": q.end_ts,
        "data_source": q.source,
    }


class MarketStore:
    """In-memory market store. Holds either the mock catalog or a live snapshot."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._quotes: Dict[Tuple[str, str], Quote] = {}
        self.mode: str = "mock"
        self.live_updated_at: Optional[float] = None
        # Verified cross-venue pairs: (polymarket_key, kalshi_key, pair_id, polarity).
        # polarity "same": Kalshi YES == Polymarket YES; "inverted": Kalshi YES == Polymarket NO.
        self._pairs: List[Tuple[Tuple[str, str], Tuple[str, str], str, str]] = []
        self._init_quotes()

    # ----- mock -----
    @staticmethod
    def _mock_book(q: Quote) -> None:
        """Synthesize a top-of-book around the mid for mock quotes."""
        hs = round(random.uniform(0.004, 0.015), 4)
        q.yes_bid = round(max(0.01, q.yes - hs), 4)
        q.yes_ask = round(min(0.99, q.yes + hs), 4)
        q.no_bid = round(max(0.01, q.no - hs), 4)
        q.no_ask = round(min(0.99, q.no + hs), 4)

    def _init_quotes(self) -> None:
        now = time.time()
        for cid, poly_title, kalshi_title, base_p, vol in _MARKET_CATALOG:
            for venue, title in (("polymarket", poly_title), ("kalshi", kalshi_title)):
                # Slight cross-venue dispersion (keeps the arb UI non-empty in demos).
                jitter = random.uniform(-0.03, 0.03)
                yes = round(max(0.02, min(0.98, base_p + jitter)), 4)
                q = Quote(
                    venue=venue,
                    canonical_id=cid,
                    title=title,
                    yes=yes,
                    no=round(1.0 - yes, 4),
                    volume_24h=round(vol * random.uniform(50_000, 250_000), 0),
                    liquidity=round(min(1.0, vol * random.uniform(0.6, 1.4)), 2),
                    updated_at=now,
                    history=[yes],
                    history_ts=[now],
                    kalshi_ticker=f"MOCK-{cid.upper()}" if venue == "kalshi" else None,
                    source="mock",
                )
                self._mock_book(q)
                self._quotes[(venue, cid)] = q
        self._pairs = [(("polymarket", cid), ("kalshi", cid), cid, "same") for cid, *_ in _MARKET_CATALOG]

    def tick(self) -> None:
        """Advance MOCK markets by a mean-reverting random walk. No-op in live mode."""
        if self.mode != "mock":
            return
        now = time.time()
        with self._lock:
            # Venues listing the same event share a common shock and are pulled
            # toward each other (arbitrageurs), so demo arbs stay rare and small.
            groups: Dict[str, List[Quote]] = {}
            for q in self._quotes.values():
                groups.setdefault(q.canonical_id, []).append(q)
            for legs in groups.values():
                avg = sum(q.yes for q in legs) / len(legs)
                common = (0.5 - avg) * 0.01 + random.gauss(0.0, 0.012)
                for q in legs:
                    converge = (avg - q.yes) * 0.15
                    idio = random.gauss(0.0, 0.008)
                    q.yes = round(max(0.01, min(0.99, q.yes + common + converge + idio)), 4)
            for q in self._quotes.values():
                q.no = round(1.0 - q.yes, 4)
                self._mock_book(q)
                q.history.append(q.yes)
                q.history_ts.append(now)
                if len(q.history) > HISTORY_LEN:
                    q.history = q.history[-HISTORY_LEN:]
                    q.history_ts = q.history_ts[-HISTORY_LEN:]
                q.updated_at = now

    # ----- live -----
    def set_live_snapshot(
        self,
        quotes: Iterable[Quote],
        pairs: Optional[List[Tuple[Tuple[str, str], Tuple[str, str], str, str]]] = None,
    ) -> int:
        """Replace the store with a live snapshot, carrying forward price history.

        Switches the store into live mode permanently (mock data never returns
        once real data has arrived). Returns the number of quotes stored.
        """
        now = time.time()
        with self._lock:
            prev = self._quotes if self.mode == "live" else {}
            new_quotes: Dict[Tuple[str, str], Quote] = {}
            for q in quotes:
                key = (q.venue, q.canonical_id)
                q.source = "live"
                prior = prev.get(key)
                if prior is q:
                    pass  # carried forward from a failed venue fetch — no new observation
                elif prior and prior.history:
                    q.history = (prior.history + [q.yes])[-HISTORY_LEN:]
                    q.history_ts = (prior.history_ts + [now])[-HISTORY_LEN:]
                else:
                    q.history = [q.yes]
                    q.history_ts = [now]
                new_quotes[key] = q
            if not new_quotes:
                return 0
            self._quotes = new_quotes
            self._pairs = [p for p in (pairs or []) if p[0] in new_quotes and p[1] in new_quotes]
            self.mode = "live"
            self.live_updated_at = now
            return len(new_quotes)

    # ----- reads -----
    def all(self) -> List[Quote]:
        with self._lock:
            return list(self._quotes.values())

    def by_canonical(self, canonical_id: str) -> List[Quote]:
        with self._lock:
            return [q for q in self._quotes.values() if q.canonical_id == canonical_id]

    def get(self, venue: str, canonical_id: str) -> Quote | None:
        with self._lock:
            return self._quotes.get((venue, canonical_id))

    def find_by_condition_id(self, condition_id: str) -> Quote | None:
        cond = (condition_id or "").lower()
        if not cond:
            return None
        with self._lock:
            for q in self._quotes.values():
                if q.venue == "polymarket" and (q.polymarket_condition_id or "").lower() == cond:
                    return q
        return None

    def pairs(self) -> List[Tuple[Quote, Quote, str, str]]:
        """Verified pairs present: (polymarket_quote, kalshi_quote, pair_id, polarity)."""
        with self._lock:
            out = []
            for pk, kk, pid, polarity in self._pairs:
                p, k = self._quotes.get(pk), self._quotes.get(kk)
                if p and k:
                    out.append((p, k, pid, polarity))
            return out

    def catalog_ids(self) -> List[str]:
        """Back-compat: ids of verified cross-venue pairs."""
        return [pid for _, _, pid, _ in self.pairs()]

    def status(self) -> dict:
        with self._lock:
            n = len(self._quotes)
            by_venue: Dict[str, int] = {}
            for q in self._quotes.values():
                by_venue[q.venue] = by_venue.get(q.venue, 0) + 1
            return {
                "mode": self.mode,
                "quotes": n,
                "by_venue": by_venue,
                "verified_pairs": len(self._pairs),
                "live_updated_at": self.live_updated_at,
                "live_age_sec": round(time.time() - self.live_updated_at, 1) if self.live_updated_at else None,
            }


market_store = MarketStore()


def quote_to_dict(q: Quote) -> dict:
    return {
        "venue": q.venue,
        "canonical_id": q.canonical_id,
        "title": q.title,
        "yes": q.yes,
        "no": q.no,
        "yes_bid": q.yes_bid,
        "yes_ask": q.yes_ask,
        "no_bid": q.no_bid,
        "no_ask": q.no_ask,
        "volume_24h": q.volume_24h,
        "liquidity": q.liquidity,
        "updated_at": q.updated_at,
        "end_ts": q.end_ts,
        "outcomes": q.outcomes,
        "source": q.source,
        "spark": q.history[-30:],
    }


def percentile_rank(values: List[float], v: float) -> float:
    if not values:
        return 0.5
    below = sum(1 for x in values if x <= v)
    return below / len(values)


def realized_vol(history: List[float]) -> float:
    if len(history) < 3:
        return 0.0
    diffs = [history[i] - history[i - 1] for i in range(1, len(history))]
    mean = sum(diffs) / len(diffs)
    var = sum((d - mean) ** 2 for d in diffs) / len(diffs)
    return math.sqrt(var)
