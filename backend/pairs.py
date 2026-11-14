"""Cross-venue market pairing.

Arbitrage is only safe when two contracts resolve on exactly the same event
under compatible rules. Title similarity is NOT enough (different dates,
thresholds, data sources and resolution criteria are common), so:

* ``load_verified_pairs()`` reads human-reviewed pairs from a JSON file
  (``MARKET_PAIRS_FILE``, default ``backend/market_pairs.json``). Only these are
  used by the Micro engine to emit tradable arb signals.
* ``suggest_pairs()`` produces *candidates* for an operator to review. They are
  surfaced via ``GET /api/markets/pairs`` and never traded automatically.

Pair file format::

    [
      {
        "polymarket_condition_id": "0xabc...",
        "kalshi_ticker": "KXFEDDECISION-25DEC-C25",
        "polarity": "same",            # or "inverted" (Kalshi YES == Polymarket NO)
        "label": "Fed cuts 25bp in Dec",
        "verified_by": "jc",
        "notes": "Both resolve on FOMC statement; checked rules 2026-11-13"
      }
    ]
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("trackmind.pairs")

DEFAULT_PAIRS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_pairs.json")

_STOP = {
    "the", "and", "for", "will", "with", "this", "that", "from", "than", "into", "over",
    "under", "above", "below", "before", "after", "next", "end", "yes", "who", "what",
    "which", "win", "wins", "price", "market", "be", "by", "of", "in", "on", "at", "to", "a",
}


def _norm_tokens(title: str) -> set:
    words = re.findall(r"[a-z0-9$%.]+", (title or "").lower())
    return {w.strip(".") for w in words if len(w) > 1 and w not in _STOP}


def similarity(a: str, b: str) -> float:
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def load_verified_pairs(path: Optional[str] = None) -> List[dict]:
    p = path or os.environ.get("MARKET_PAIRS_FILE", DEFAULT_PAIRS_FILE)
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"could not read pairs file {p}: {e}")
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        cond = (r.get("polymarket_condition_id") or "").lower()
        tick = r.get("kalshi_ticker") or ""
        if not cond or not tick:
            continue
        polarity = r.get("polarity", "same")
        if polarity not in ("same", "inverted"):
            continue
        out.append({**r, "polymarket_condition_id": cond, "polarity": polarity})
    return out


def resolve_pairs(polys: list, kalshis: list, verified: List[dict]) -> List[Tuple[Tuple[str, str], Tuple[str, str], str, str]]:
    """Map verified pair rows onto quote keys present in this snapshot.

    Returns tuples (polymarket_key, kalshi_key, pair_id, polarity).
    """
    by_cond: Dict[str, object] = {(q.polymarket_condition_id or "").lower(): q for q in polys if q.polymarket_condition_id}
    by_tick: Dict[str, object] = {q.kalshi_ticker: q for q in kalshis if q.kalshi_ticker}
    out = []
    for r in verified:
        p = by_cond.get(r["polymarket_condition_id"])
        k = by_tick.get(r["kalshi_ticker"])
        if p is None or k is None:
            continue
        pid = f"pair_{r['kalshi_ticker'].lower()}"
        out.append(((p.venue, p.canonical_id), (k.venue, k.canonical_id), pid, r["polarity"]))
    return out


def suggest_pairs(polys: list, kalshis: list, min_score: float = 0.45, limit: int = 50) -> List[dict]:
    """Candidate pairs for human review — NEVER traded automatically."""
    cands = []
    for k in kalshis:
        best, best_s = None, 0.0
        for p in polys:
            s = similarity(k.title, p.title)
            if s > best_s:
                best, best_s = p, s
        if best is not None and best_s >= min_score:
            cands.append({
                "score": round(best_s, 3),
                "polymarket_condition_id": best.polymarket_condition_id,
                "polymarket_title": best.title,
                "kalshi_ticker": k.kalshi_ticker,
                "kalshi_title": k.title,
            })
    cands.sort(key=lambda c: c["score"], reverse=True)
    return cands[:limit]
