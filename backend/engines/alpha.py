"""Alpha engine — narrative / sentiment / catalyst-driven repricing.

Uses the Emergent Universal LLM key (Claude Sonnet 4.5) to score a curated
catalyst feed against current market prices and emit signals when narrative
strength diverges from price.

The LLM is called with a tight, structured prompt and the response is parsed
defensively. If the LLM is unavailable we fall back to a deterministic
sentiment score derived from synthetic catalyst tags so the engine always
emits something coherent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from typing import List

from markets import market_store

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
    {"market_keywords": ["spx", "ath"],  "headline": "Mega-cap earnings tape comes in 80% beat rate", "sentiment": +0.6},
    {"market_keywords": ["sb", "afc"],   "headline": "Star QB cleared from injury list ahead of playoffs", "sentiment": +0.5},
    {"market_keywords": ["oscar"],       "headline": "Critics' guild awards split unexpectedly",       "sentiment": -0.3},
]


def _match_catalysts(title: str, cid: str) -> List[dict]:
    t = (title + " " + cid).lower()
    return [c for c in CATALYSTS if any(k in t for k in c["market_keywords"])]


def _heuristic_score(catalysts: List[dict]) -> float:
    if not catalysts:
        return 0.0
    return sum(c["sentiment"] for c in catalysts) / len(catalysts)


async def _llm_narrative_score(market_title: str, catalysts: List[dict]) -> float | None:
    """Ask the LLM for a unified narrative score in [-1, 1]. Returns None on failure."""
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


async def run_async(use_llm: bool = True) -> List[dict]:
    signals: List[dict] = []
    bucket = int(time.time() // 180)
    rng = random.Random(bucket)

    for q in market_store.all():
        if q.venue != "polymarket":
            continue
        catalysts = _match_catalysts(q.title, q.canonical_id)
        if not catalysts:
            continue

        # Add minor synthetic noise so signals evolve over time.
        noisy = [
            {**c, "sentiment": max(-1.0, min(1.0, c["sentiment"] + rng.uniform(-0.1, 0.1)))}
            for c in catalysts
        ]
        score: float | None = None
        if use_llm:
            score = await _llm_narrative_score(q.title, noisy)
        if score is None:
            score = _heuristic_score(noisy)

        # Implied probability vs market price reveals repricing edge.
        implied = max(0.05, min(0.95, 0.5 + score * 0.35))
        side = "YES" if implied > q.yes else "NO"
        gap = abs(implied - (q.yes if side == "YES" else q.no))
        if gap < 0.03:
            continue

        confidence = round(max(0.0, min(0.95, 0.45 + abs(score) * 0.4)), 3)
        expected_edge = round(gap * 0.5, 4)  # haircut for narrative noise
        risk = round(max(0.015, 0.05 - abs(score) * 0.02), 4)
        liquidity = round(min(1.0, q.liquidity), 4)
        size_hint = round(max(0.02, min(0.15, confidence * expected_edge * 3 + 0.04)), 4)
        price = q.yes if side == "YES" else q.no

        h = hashlib.sha1(f"{q.canonical_id}-{bucket}".encode()).hexdigest()[:8]
        signals.append({
            "engine": "alpha",
            "canonical_id": q.canonical_id,
            "venue": q.venue,
            "market": q.title,
            "side": side,
            "price": price,
            "confidence": confidence,
            "expected_edge": expected_edge,
            "risk": risk,
            "liquidity": liquidity,
            "size_hint": size_hint,
            "rationale": f"Narrative {score:+.2f} → implied {implied:.2f} vs market {q.yes:.2f}",
            "catalysts": [c["headline"] for c in noisy],
            "narrative_id": h,
        })
    signals.sort(key=lambda s: s["confidence"] * s["expected_edge"], reverse=True)
    return signals[:8]
