"""Alpha engine — narrative / catalyst-driven repricing.

STATUS: the catalyst feed below is SYNTHETIC (hand-written demo headlines).
Synthetic news must never drive trades on real markets, so:

  * mock data mode  → runs on the demo catalog with the synthetic feed.
  * live data mode  → real headlines when ``ALPHA_NEWS_SOURCE=gdelt`` (see
    ``news.py``) AND an LLM key is set to judge them; otherwise emits NOTHING
    (``ALPHA_ALLOW_SYNTHETIC=true`` overrides for testing). ``status()`` says why.

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
import news
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


ALPHA_MAX_LLM_CALLS = int(os.environ.get("ALPHA_MAX_LLM_CALLS_PER_REFRESH", "12"))
ALPHA_LLM_MODEL = os.environ.get("ALPHA_LLM_MODEL", "claude-sonnet-5-5")
ANTHROPIC_URL = os.environ.get("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")


def _allow_synthetic_on_live() -> bool:
    return os.environ.get("ALPHA_ALLOW_SYNTHETIC", "false").lower() in {"1", "true", "yes"}


def _llm_available() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.environ.get("EMERGENT_LLM_KEY"):
        try:
            import emergentintegrations  # noqa: F401  # type: ignore
            return True
        except Exception:  # noqa: BLE001
            return False
    return False


def mode() -> str:
    """Which feed Alpha runs on right now: "synthetic", "gdelt" or "off"."""
    if market_store.mode != "live":
        return "synthetic"  # demo markets get the demo feed
    src = news.news_source()
    if src == "gdelt":
        return "gdelt" if _llm_available() else "off"
    if _allow_synthetic_on_live():
        return "synthetic"
    return "off"


def status() -> dict:
    m = mode()
    src = news.news_source()
    reason = None
    if m == "off":
        if src == "gdelt":
            reason = ("GDELT news is on, but no LLM key is set to judge headlines "
                      "(set ANTHROPIC_API_KEY or EMERGENT_LLM_KEY).")
        elif src:
            reason = f"Unknown ALPHA_NEWS_SOURCE '{src}' (supported: gdelt)."
        else:
            reason = ("Live market data is on but Alpha only has a synthetic demo news feed. "
                      "Set ALPHA_NEWS_SOURCE=gdelt to use real news.")
    out = {"catalyst_source": m if m != "off" else None, "data_mode": market_store.mode,
           "active": m != "off", "reason": reason}
    if m == "gdelt":
        out["news"] = news.news_cache.status()
    return out


def _heuristic_score(catalysts: List[dict]) -> float:
    if not catalysts:
        return 0.0
    return sum(c["sentiment"] for c in catalysts) / len(catalysts)


# ---------------- LLM ----------------
def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


async def _llm_json(prompt: str, timeout: float = 15.0) -> dict | None:
    """One JSON-only completion. Anthropic API directly if ANTHROPIC_API_KEY, else Emergent."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        if key:
            import httpx
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    ANTHROPIC_URL,
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json={"model": ALPHA_LLM_MODEL, "max_tokens": 300, "system": "Return JSON only.",
                          "messages": [{"role": "user", "content": prompt}]},
                )
            r.raise_for_status()
            blocks = (r.json() or {}).get("content") or []
            return _extract_json("".join(b.get("text", "") for b in blocks if b.get("type") == "text"))
        ekey = os.environ.get("EMERGENT_LLM_KEY")
        if not ekey:
            return None
        from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore
        chat = (
            LlmChat(api_key=ekey, session_id=f"alpha-{int(time.time())}", system_message="Return JSON only.")
            .with_model("anthropic", "claude-sonnet-4-5-20250929")
        )
        return _extract_json(await asyncio.wait_for(chat.send_message(UserMessage(text=prompt)), timeout=timeout))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"alpha LLM call failed: {e}")
        return None


def _clamp_score(data: dict | None) -> float | None:
    if not data:
        return None
    try:
        return max(-1.0, min(1.0, float(data.get("score", 0.0))))
    except (TypeError, ValueError):
        return None


async def _llm_narrative_score(market_title: str, catalysts: List[dict], cache_key: str | None = None) -> float | None:
    """Synthetic-feed scoring (demo mode)."""
    if not catalysts:
        return None
    headlines = "\n".join(f"- ({c['sentiment']:+.2f}) {c['headline']}" for c in catalysts)
    prompt = (
        "You are an institutional prediction-market analyst. "
        "Given a market and recent catalysts, return ONLY a single JSON object: "
        '{"score": <float in [-1,1]>, "thesis": "<1-sentence rationale>"}. '
        "Score > 0 means catalysts argue YES is under-priced; < 0 means NO under-priced.\n\n"
        f"Market: {market_title}\nCatalysts:\n{headlines}\n"
    )
    return _clamp_score(await _llm_json(prompt))


def news_prompt(market_title: str, outcomes: List[str], yes_price: float, end_ts: float | None,
                headlines: List[dict], now: float) -> str:
    yes_label = (outcomes or ["Yes"])[0]
    closes = time.strftime("%Y-%m-%d", time.gmtime(end_ts)) if end_ts else "unknown"
    lines = []
    for i, h in enumerate(headlines):
        age_h = (now - h["seen_ts"]) / 3600 if h.get("seen_ts") else None
        when = f"{age_h:.0f}h ago" if age_h is not None else "time unknown"
        lines.append(f"[{i}] ({when}, {h.get('domain') or '?'}) {h['title']}")
    return (
        "You are a careful prediction-market analyst. Decide whether recent news gives an edge "
        "on this market at its CURRENT price.\n\n"
        f"Market: {market_title}\n"
        f'"YES" in this question means the outcome "{yes_label}".\n'
        f"Current market price for YES: {yes_price:.3f} (the crowd's probability). Market closes: {closes}.\n\n"
        "Headlines (from a keyword search, so many may be irrelevant):\n" + "\n".join(lines) + "\n\n"
        "Rules:\n"
        "- Ignore headlines that are not specifically about this market's outcome.\n"
        "- Assume the price already reflects widely reported news. Only give a non-zero score if the "
        "headlines contain information that plausibly is NOT yet priced in. Most of the time the right answer is 0.\n"
        "- score in [-1, 1]: > 0 means YES is under-priced, < 0 means YES is over-priced.\n\n"
        'Return ONLY JSON: {"relevant": true|false, "score": <float>, "used": [<headline indices>], '
        '"thesis": "<one sentence>"}'
    )


async def _llm_news_view(q, headlines: List[dict], now: float) -> dict | None:
    data = await _llm_json(news_prompt(q.title, q.outcomes, q.yes, q.end_ts, headlines, now))
    if not data:
        return None
    score = _clamp_score(data)
    if score is None:
        return None
    used = [i for i in (data.get("used") or []) if isinstance(i, int) and 0 <= i < len(headlines)]
    return {"relevant": bool(data.get("relevant")), "score": score,
            "thesis": str(data.get("thesis") or "")[:240], "used": used}


# ---------------- signal construction ----------------
def _signal_from_score(q, score: float, catalysts: List[str], source: str, cache_hit: bool,
                       bucket_key: str, extra: dict | None = None) -> dict | None:
    """Turn a narrative score into a priced signal, or None if no edge after costs."""
    implied = max(0.02, min(0.98, q.yes + score * ALPHA_MAX_SHIFT))
    side = "YES" if implied > q.yes else "NO"
    fair = implied if side == "YES" else 1.0 - implied
    price = ask_for(q, side)
    net_gap = fair - price - fee_per_contract(q.venue, price)
    if net_gap < ALPHA_MIN_NET_EDGE:
        return None
    confidence = round(max(0.0, min(0.95, 0.45 + abs(score) * 0.4)), 3)
    expected_edge = round(net_gap * 0.5, 4)  # haircut for narrative noise
    sig = {
        "engine": "alpha",
        "canonical_id": q.canonical_id,
        "market": q.title,
        "side": side,
        "price": round(price, 4),
        "mid": round(q.yes if side == "YES" else q.no, 4),
        "fair_prob": round(fair, 4),  # model probability that `side` wins
        "confidence": confidence,
        "expected_edge": expected_edge,
        "risk": round(max(0.015, 0.05 - abs(score) * 0.02), 4),
        "liquidity": round(min(1.0, q.liquidity), 4),
        "size_hint": round(max(0.02, min(0.15, confidence * expected_edge * 3 + 0.03)), 4),
        "rationale": f"Narrative {score:+.2f} → fair YES {implied:.2f} vs mid {q.yes:.2f} (ask {price:.2f} {side})",
        "catalysts": catalysts,
        "catalyst_source": source,
        "narrative_id": hashlib.sha1(f"{q.canonical_id}-{bucket_key}".encode()).hexdigest()[:8],
        "cache_hit": cache_hit,
        **execution_refs(q),
    }
    if extra:
        sig.update(extra)
    return sig


def _count(metrics: dict | None, key: str, inc: float = 1) -> None:
    if metrics is not None:
        metrics[key] = metrics.get(key, 0) + inc


async def _run_synthetic(use_llm: bool, score_cache: dict | None, metrics: dict | None) -> List[dict]:
    signals: List[dict] = []
    bucket = int(time.time() // 180)
    rng = random.Random(bucket)
    for q in market_store.all():
        if q.venue != "polymarket":
            continue  # one view per event; Kalshi duplicates are covered by Micro
        catalysts = _match_catalysts(q.title, q.canonical_id)
        if not catalysts:
            continue
        noisy = [
            {**c, "sentiment": max(-1.0, min(1.0, c["sentiment"] + rng.uniform(-0.1, 0.1)))}
            for c in catalysts
        ]
        score: float | None = None
        cache_hit = False
        cached = (score_cache or {}).get(q.canonical_id)
        if cached and cached.get("bucket") == bucket:
            score, cache_hit = cached["score"], True
            _count(metrics, "cache_hits")
        if score is None and use_llm:
            t0 = time.time()
            score = await _llm_narrative_score(q.title, noisy)
            _count(metrics, "calls")
            _count(metrics, "total_ms", (time.time() - t0) * 1000)
            if score is None:
                _count(metrics, "failures")
            elif score_cache is not None:
                score_cache[q.canonical_id] = {"bucket": bucket, "score": score}
        if score is None:
            score = _heuristic_score(noisy)
        sig = _signal_from_score(q, score, [c["headline"] for c in noisy], "synthetic", cache_hit, str(bucket))
        if sig:
            signals.append(sig)
    return signals


async def _run_gdelt(use_llm: bool, score_cache: dict | None, metrics: dict | None) -> List[dict]:
    """Real headlines from the news cache, judged by the LLM. No LLM view ⇒ no signal."""
    signals: List[dict] = []
    now = time.time()
    calls = 0
    for q in market_store.all():
        if q.venue != "polymarket" or (q.end_ts is not None and q.end_ts <= now):
            continue
        headlines = news.news_cache.get(q.canonical_id, now)
        if not headlines:
            continue
        # Re-judge only when the headline set changes.
        set_key = hashlib.sha1("|".join(h["url"] for h in headlines).encode()).hexdigest()[:12]
        view = None
        cache_hit = False
        cached = (score_cache or {}).get(q.canonical_id)
        if cached and cached.get("bucket") == set_key:
            view, cache_hit = cached.get("view"), True
            _count(metrics, "cache_hits")
        elif use_llm and calls < ALPHA_MAX_LLM_CALLS:
            calls += 1
            t0 = time.time()
            view = await _llm_news_view(q, headlines, now)
            _count(metrics, "calls")
            _count(metrics, "total_ms", (time.time() - t0) * 1000)
            if view is None:
                _count(metrics, "failures")
            elif score_cache is not None:
                score_cache[q.canonical_id] = {"bucket": set_key, "score": view["score"], "view": view}
        if not view or not view["relevant"] or view["score"] == 0:
            continue
        used = [headlines[i] for i in view["used"]] or headlines[:3]
        sig = _signal_from_score(
            q, view["score"], [h["title"] for h in used], "gdelt", cache_hit, set_key,
            extra={"thesis": view["thesis"], "headline_urls": [h["url"] for h in used]},
        )
        if sig:
            signals.append(sig)
    return signals


async def run_async(use_llm: bool = True, score_cache: dict | None = None, metrics: dict | None = None) -> List[dict]:
    """Generate alpha signals from whichever feed `mode()` selects."""
    m = mode()
    if m == "synthetic":
        signals = await _run_synthetic(use_llm, score_cache, metrics)
    elif m == "gdelt":
        signals = await _run_gdelt(use_llm, score_cache, metrics)
    else:
        return []
    signals.sort(key=lambda s: s["confidence"] * s["expected_edge"], reverse=True)
    return signals[:8]
