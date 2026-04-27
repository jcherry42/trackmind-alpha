"""
Mock market data generator for TRACKMIND.

Generates realistic prediction-market quotes for Polymarket / Kalshi style venues.
Each market has a YES price + NO price (sum slightly > 1.0 due to spread / fees).
A small random walk drives prices over time so engines can detect persistence,
arb opportunities, and narrative repricing.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Tuple


VENUES = ("polymarket", "kalshi")


# Curated catalog of equivalent / related markets across venues.
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
    yes: float
    no: float
    volume_24h: float
    liquidity: float
    updated_at: float
    history: List[float] = field(default_factory=list)  # last N yes prices


class MarketStore:
    """In-memory time-evolving market store. Thread-safe enough for our uses."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._quotes: Dict[Tuple[str, str], Quote] = {}
        self._init_quotes()

    def _init_quotes(self) -> None:
        for cid, poly_title, kalshi_title, base_p, vol in _MARKET_CATALOG:
            for venue, title in (("polymarket", poly_title), ("kalshi", kalshi_title)):
                # Slight cross-venue dispersion creates arbitrage opportunities.
                jitter = random.uniform(-0.04, 0.04)
                yes = max(0.02, min(0.98, base_p + jitter))
                spread = random.uniform(0.005, 0.025)
                no = max(0.02, min(0.98, 1.0 - yes + spread))
                liquidity = round(vol * random.uniform(0.6, 1.4), 2)
                volume = round(vol * random.uniform(50_000, 250_000), 0)
                self._quotes[(venue, cid)] = Quote(
                    venue=venue,
                    canonical_id=cid,
                    title=title,
                    yes=round(yes, 4),
                    no=round(no, 4),
                    volume_24h=volume,
                    liquidity=liquidity,
                    updated_at=time.time(),
                    history=[yes],
                )

    def tick(self) -> None:
        """Advance every market by a small mean-reverting random walk."""
        with self._lock:
            for q in self._quotes.values():
                drift = (0.5 - q.yes) * 0.01  # gentle pull to 0.5
                shock = random.gauss(0.0, 0.012)
                q.yes = max(0.01, min(0.99, q.yes + drift + shock))
                spread = random.uniform(0.005, 0.025)
                q.no = max(0.01, min(0.99, 1.0 - q.yes + spread))
                q.yes = round(q.yes, 4)
                q.no = round(q.no, 4)
                q.history.append(q.yes)
                if len(q.history) > 120:
                    q.history = q.history[-120:]
                q.updated_at = time.time()

    def all(self) -> List[Quote]:
        with self._lock:
            return list(self._quotes.values())

    def by_canonical(self, canonical_id: str) -> List[Quote]:
        with self._lock:
            return [q for q in self._quotes.values() if q.canonical_id == canonical_id]

    def get(self, venue: str, canonical_id: str) -> Quote | None:
        with self._lock:
            return self._quotes.get((venue, canonical_id))

    def catalog_ids(self) -> List[str]:
        return [c[0] for c in _MARKET_CATALOG]


market_store = MarketStore()


def quote_to_dict(q: Quote) -> dict:
    return {
        "venue": q.venue,
        "canonical_id": q.canonical_id,
        "title": q.title,
        "yes": q.yes,
        "no": q.no,
        "volume_24h": q.volume_24h,
        "liquidity": q.liquidity,
        "updated_at": q.updated_at,
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
