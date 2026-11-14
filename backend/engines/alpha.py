"""Alpha engine — narrative / catalyst-driven repricing.

STATUS: the catalyst feed below is SYNTHETIC (hand-written demo headlines).
Synthetic news must never drive trades on real markets, so:

  * mock data mode  → runs on the demo catalog with the synthetic feed.
  * live data mode  → emits NOTHING unless a real news source is wired in
    (``ALPHA_NEWS_SOURCE``; not yet implemented) or the operator explicitly
    sets ``ALPHA_ALLOW_SYNTHETIC=true`` for testing. ``status()`` reports why.

Estimation: the narrative score in [-1, 1] *shifts* the market's own mid by at
most ``ALPHA_MAX_SHIFT`` probability points (default 0.08). It does not replace
the market price with an absolute guess. Edge is reported net of spread + fees.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from typing import List

from fees import fee_per_contract
from markets import ask_for, execution_refs, market_store

ALPHA_MAX_SHIFT = float(os.environ.get("ALPHA_MAX_SHIFT", "0.08"))
ALPHA_MIN_NET_EDGE = float(os.environ.get("ALPHA_MIN_NET_EDGE", "0.01"))

logger = logging.getLogger(__name__)


# Synthetic catalyst feed (rotates every few minutes).
CATALYSTS = [
    {"market_keywords": ["btc", "100k"], "headline": "Spot BTC ETF inflows hit $1.2B record day", "sentiment": +0.8},
    {"market_keywords": ["btc"],         "headline": "On-chain analyst flags weakening miner flows",  "sentiment": -0.4},
    {"market_keywords": ["eth", "5k"],   "headline": "ETH staking yield drops below 3%",              "sentiment": -0.3},
    {"market_keywords": ["fed", "rate"], "headline": "Fed minutes: 'patient' stance, rate-cut window narrows", "sentiment": -0.6},
    {"market_keywords": ["cpi"],         "headline": "Shelter CPI cooling per Zillow leading print",  "sentiment": -0.5},
    {"market_keywords": ["election"],    "headline": "Two key swing-state polls flip 3pts overnight", "sentiment": +0.7},
    {"market_keywords": ["nvda"],        "headline": "NVDA channel checks signal record H100 demand", "sentiment": +0.75},
    {"market_keywords": ["recession"],   "headline": "ISM manufacturing breaks above 50, expansion territory", "sentiment": -0.6},
    {"market_keywords": ["oil", "wti"],  "headline": "OPEC+ extends voluntary cuts to year-end",      "sentiment": +0.55},
    {"market_keywords": ["s&p", "spx"],  "headline": "Mega-cap earnings tape comes in 80% beat rate", "sentiment": +0.6},
    {"market_keywords": ["super bowl", "afc"],   "headline": "Star QB cleared from injury list ahead of playoffs", "sentiment": +0.5},
    {"market_keywords": ["oscar"],       "headline": "Critics' guild awards split unexpectedly",       "sentiment": -0.3},
]


_KW_PATTERNS = {
    kw: re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])")
    for c in CATALYSTS for kw in c["market_keywords"]
}


def _match_catalysts(title: str, cid: str = "") -> List[dict]:
    """Whole-word keyword match (so "eth" doesn't match "Netherlands")."""
    t = f"{title} {cid}".lower().replace("_", " ")
    return [c for c in CATALYSTS if any(_KW_PATTERNS[k].search(t) for k in c["market_keywords"])]


def _synthetic_allowed() -> bool:
    if market_store.mode != "live":
        return True
    return os.environ.get("ALPHA_ALLOW_SYNTHETIC", "false").lower() in {"1", "true", "yes"}


def status() -> dict:
    allowed = _synthetic_allowed()
    return {
        "catalyst_source": "synthetic",
        "data_mode": market_store.mode,
        "active": allowed,
        "reason": None if allowed else (
            "Live market data is on but Alpha only has a synthetic demo news feed. "
            "Connect a real news source (ALPHA_NEWS_SOURCE) to enable it."
        ),
    }


def _heuristic_score(catalysts: List[dict]) -> float:
    if not catalysts:
        return 0.0
    return sum(c["sentiment"] for c in catalysts) / len(catalysts)


async def _llm_narrative_score(market_title: str, catalysts: List[dict], cache_key: str | None = None) -> float | None:
    """Ask the LLM for a unified narrative score in [-1, 1]. Returns None on failure.

    Caller passes ``cache_key`` (canonical_id + bucket) — scores are cached via the
    module-level cache in server.py.  This module does NOT cache itself; it's a pure call.
    """
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key or not catalysts:
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore
    except Exception:
        logger.warning("emergentintegrations not available; falling back to heuristic")
        return None

    headlines = "\n".join(f"- ({c['sentiment']:+.2f}) {c['headline']}" for c in catalysts)
    prompt = (
        "You are an institutional prediction-market analyst. "
        "Given a market and recent catalysts, return ONLY a single JSON object: "
        '{"score": <float in [-1,1]>, "thesis": "<1-sentence rationale>"}. '
        "Score > 0 means catalysts argue YES is under-priced; < 0 means NO under-priced.\n\n"
        f"Market: {market_title}\nCatalysts:\n{headlines}\n"
    )
    try:
        chat = (
            LlmChat(api_key=api_key, session_id=f"alpha-{int(time.time())}", system_message="Return JSON only.")
            .with_model("anthropic", "claude-sonnet-4-5-20250929")
        )
        text = await asyncio.wait_for(chat.send_message(UserMessage(text=prompt)), timeout=8.0)
        text = (text or "").strip()
        # Defensive JSON extraction
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        data = json.loads(text[start : end + 1])
        score = float(data.get("score", 0.0))
        return max(-1.0, min(1.0, score))
    except Exception as e:
        logger.warning(f"alpha LLM call failed: {e}")
        return None


async def run_async(use_llm: bool = True, score_cache: dict | None = None, metrics: dict | None = None) -> List[dict]:
    """Generate alpha signals.

    ``score_cache``: optional {canonical_id: {"bucket": int, "score": float}} dict reused across
    invocations to skip LLM calls when bucket hasn't advanced.  ``metrics`` is a simple counter dict.
    """
    signals: List[dict] = []
    if not _synthetic_allowed():
        return signals
    bucket = int(time.time() // 180)
    rng = random.Random(bucket)

    for q in market_store.all():
        if q.venue != "polymarket":
            continue  # one view per event; Kalshi duplicates are covered by Micro
        catalysts = _match_catalysts(q.title, q.canonical_id)
        if not catalysts:
            continue

        # Add minor synthetic noise so signals evolve over time.
        noisy = [
            {**c, "sentiment": max(-1.0, min(1.0, c["sentiment"] + rng.uniform(-0.1, 0.1)))}
            for c in catalysts
        ]

        score: float | None = None
        cache_hit = False
        if score_cache is not None:
            cached = score_cache.get(q.canonical_id)
            if cached and cached.get("bucket") == bucket:
                score = cached["score"]
                cache_hit = True
                if metrics is not None:
                    metrics["cache_hits"] = metrics.get("cache_hits", 0) + 1

        if score is None and use_llm:
            t0 = time.time()
            score = await _llm_narrative_score(q.title, noisy)
            if metrics is not None:
                metrics["calls"] = metrics.get("calls", 0) + 1
                metrics["total_ms"] = metrics.get("total_ms", 0.0) + (time.time() - t0) * 1000
                if score is None:
                    metrics["failures"] = metrics.get("failures", 0) + 1
            if score is not None and score_cache is not None:
                score_cache[q.canonical_id] = {"bucket": bucket, "score": score}
        if score is None:
            score = _heuristic_score(noisy)

        # The narrative nudges the market's own price; it doesn't replace it.
        implied = max(0.02, min(0.98, q.yes + score * ALPHA_MAX_SHIFT))
        side = "YES" if implied > q.yes else "NO"
        fair = implied if side == "YES" else 1.0 - implied
        price = ask_for(q, side)
        net_gap = fair - price - fee_per_contract(q.venue, price)
        if net_gap < ALPHA_MIN_NET_EDGE:
            continue

        confidence = round(max(0.0, min(0.95, 0.45 + abs(score) * 0.4)), 3)
        expected_edge = round(net_gap * 0.5, 4)  # haircut for narrative noise
        risk = round(max(0.015, 0.05 - abs(score) * 0.02), 4)
        liquidity = round(min(1.0, q.liquidity), 4)
        size_hint = round(max(0.02, min(0.15, confidence * expected_edge * 3 + 0.03)), 4)

        h = hashlib.sha1(f"{q.canonical_id}-{bucket}".encode()).hexdigest()[:8]
        signals.append({
            "engine": "alpha",
            "canonical_id": q.canonical_id,
            "market": q.title,
            "side": side,
            "price": round(price, 4),
            "mid": round(q.yes if side == "YES" else q.no, 4),
            "confidence": confidence,
            "expected_edge": expected_edge,
            "risk": risk,
            "liquidity": liquidity,
            "size_hint": size_hint,
            "rationale": f"Narrative {score:+.2f} → fair YES {implied:.2f} vs mid {q.yes:.2f} (ask {price:.2f} {side})",
            "catalysts": [c["headline"] for c in noisy],
            "catalyst_source": "synthetic",
            "narrative_id": h,
            "cache_hit": cache_hit,
            **execution_refs(q),
        })
    signals.sort(key=lambda s: s["confidence"] * s["expected_edge"], reverse=True)
    return signals[:8]
