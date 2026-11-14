"""Engine scorecard: did the engines' predictions come true?

Every signal an engine emits is logged as a *prediction episode* the first time
it appears (an engine re-emits the same view every minute; one episode = one
call, scored once, at the price it was first actionable). When the market
resolves, the episode is scored:

  * Brier score of ``fair_prob`` vs the outcome, and the market's own Brier
    score (its mid at first sight) as the baseline. ``skill = 1 − brier/market``:
    > 0 means the engine beat the market's own odds, ≤ 0 means it didn't.
  * Log loss, calibration buckets (predicted vs realized frequency).
  * Realized P&L per $1 contract at the logged ask, net of fees, against the
    edge the engine claimed. This is the number that decides whether to trade it.

Live-data episodes resolve at the venue's real outcome. Demo (mock) episodes
resolve by the same unbiased simulation as paper positions; they're reported
separately and say nothing about real skill.
"""
from __future__ import annotations

import math
import random
import time
from typing import Dict, Iterable, List, Optional

import paper_settlement as ps
from fees import fee_per_contract

EPISODE_GAP_SEC = 1800       # a signal absent this long starts a new episode
MIN_SAMPLE_FOR_VERDICT = 30  # below this, the scorecard says "not enough data"
_EPS = 1e-6


# ---------------- logging ----------------
def _ref_for(sig: dict) -> Optional[str]:
    if sig.get("side") == "ARB":
        return sig.get("canonical_id")
    if sig.get("venue") == "polymarket":
        return (sig.get("polymarket_condition_id") or "").lower() or sig.get("canonical_id")
    if sig.get("venue") == "kalshi":
        return sig.get("kalshi_ticker") or sig.get("canonical_id")
    return sig.get("canonical_id")


def episode_key(sig: dict) -> str:
    return f"{sig['engine']}|{sig.get('venue')}|{_ref_for(sig)}|{sig['side']}"


def new_episode(sig: dict, now: float) -> dict:
    price = float(sig["price"])
    if sig.get("side") == "ARB":
        fee = float(sig.get("fees_per_contract", 0.0))
    else:
        fee = fee_per_contract(sig.get("venue", ""), price)
    return {
        "key": episode_key(sig),
        "engine": sig["engine"],
        "venue": sig.get("venue"),
        "ref": _ref_for(sig),
        "canonical_id": sig.get("canonical_id"),
        "market": sig.get("market"),
        "side": sig["side"],
        "fair_prob": sig.get("fair_prob"),
        "entry_price": price,
        "mid": sig.get("mid"),
        "fee": round(fee, 5),
        "expected_edge": sig.get("expected_edge"),
        "confidence": sig.get("confidence"),
        "edge_calibrated": sig.get("edge_calibrated"),
        "data_source": sig.get("data_source") or "mock",
        "polymarket_condition_id": sig.get("polymarket_condition_id"),
        "kalshi_ticker": sig.get("kalshi_ticker"),
        "end_ts": sig.get("end_ts"),
        "legs": sig.get("legs"),
        "pair_polarity": sig.get("pair_polarity"),
        "first_seen": now,
        "last_seen": now,
        "n_obs": 1,
        "status": "pending",
    }


async def ensure_indexes(db) -> None:
    await db.predictions.create_index([("key", 1), ("last_seen", -1)])
    await db.predictions.create_index([("status", 1), ("data_source", 1)])
    await db.predictions.create_index([("engine", 1), ("status", 1), ("resolved_at", -1)])


async def log_predictions(db, signals: Iterable[dict], now: Optional[float] = None) -> int:
    """Open a new episode for each signal not seen in the last EPISODE_GAP_SEC. Returns # new."""
    now = now or time.time()
    created = 0
    for sig in signals:
        if sig.get("price") is None or not sig.get("engine"):
            continue
        key = episode_key(sig)
        res = await db.predictions.update_one(
            {"key": key, "status": "pending", "last_seen": {"$gte": now - EPISODE_GAP_SEC}},
            {"$set": {"last_seen": now, "last_fair_prob": sig.get("fair_prob"), "last_price": sig.get("price")},
             "$inc": {"n_obs": 1}},
        )
        if res.matched_count == 0:
            await db.predictions.insert_one(new_episode(sig, now))
            created += 1
    return created


# ---------------- scoring ----------------
def score_episode(ep: dict, outcomes: Dict[int, str]) -> dict:
    """Fields to $set on an episode once every leg's outcome is known."""
    pos_like = {**ep, "contracts": 1.0, "fees": ep.get("fee", 0.0)}
    payout, pnl = ps.realized_pnl(pos_like, outcomes)
    legs = ps.legs_of(pos_like)
    void = any(v == "VOID" for v in outcomes.values())
    won: Optional[bool] = None
    if not void and ep.get("side") in ("YES", "NO"):
        won = outcomes[0] == ep["side"]
    return {
        "status": "resolved",
        "outcomes": {str(k): v for k, v in outcomes.items()},
        "void": void,
        "won": won,
        "payout": payout,
        # P&L per $1-payout contract (pnl is already for 1 contract); unrounded is fine here.
        "pnl_per_contract": round(payout - float(ep["entry_price"]) - float(ep.get("fee", 0.0)), 5),
        "n_legs": len(legs),
    }


def mock_outcomes_for(ep: dict, lookup, now: float, rng: random.Random | None = None) -> Optional[Dict[int, str]]:
    """Simulated resolution for a demo episode (same unbiased rule as paper positions)."""
    pos_like = {**ep, "contracts": 1.0}
    dt = now - float(ep.get("last_checked", ep["first_seen"]))
    return ps.mock_outcomes(pos_like, lookup, dt, rng=rng)


# ---------------- metrics (pure) ----------------
def _clip(p: float) -> float:
    return min(1 - _EPS, max(_EPS, p))


def summarize(rows: List[dict], bins: int = 10) -> dict:
    """Metrics for one engine's resolved episodes."""
    n = len(rows)
    out: dict = {"n_resolved": n}
    if n == 0:
        out["verdict"] = "no resolved predictions yet"
        return out

    pnl = [float(r["pnl_per_contract"]) for r in rows if r.get("pnl_per_contract") is not None]
    claimed = [float(r["expected_edge"]) for r in rows if r.get("expected_edge") is not None]
    if pnl:
        mean = sum(pnl) / len(pnl)
        var = sum((x - mean) ** 2 for x in pnl) / max(1, len(pnl) - 1)
        out["realized_pnl_per_contract"] = round(mean, 4)
        out["realized_pnl_se"] = round(math.sqrt(var / len(pnl)), 4)
    if claimed:
        out["claimed_edge"] = round(sum(claimed) / len(claimed), 4)

    scored = [r for r in rows if r.get("won") is not None and r.get("fair_prob") is not None]
    out["n_scored"] = len(scored)
    if scored:
        y = [1.0 if r["won"] else 0.0 for r in scored]
        p = [float(r["fair_prob"]) for r in scored]
        out["hit_rate"] = round(sum(y) / len(y), 4)
        out["mean_fair_prob"] = round(sum(p) / len(p), 4)
        brier = sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(y)
        out["brier"] = round(brier, 4)
        out["log_loss"] = round(-sum(yi * math.log(_clip(pi)) + (1 - yi) * math.log(1 - _clip(pi)) for pi, yi in zip(p, y)) / len(y), 4)
        mkt = [(float(r["mid"]), yi) for r, yi in zip(scored, y) if r.get("mid") is not None]
        if mkt:
            brier_mkt = sum((m - yi) ** 2 for m, yi in mkt) / len(mkt)
            out["brier_market"] = round(brier_mkt, 4)
            out["skill_vs_market"] = round(1 - brier / brier_mkt, 4) if brier_mkt > 0 else None
        cal = []
        for b in range(bins):
            lo, hi = b / bins, (b + 1) / bins
            members = [(pi, yi) for pi, yi in zip(p, y) if (lo <= pi < hi) or (b == bins - 1 and pi == 1.0)]
            if members:
                cal.append({
                    "bin": [round(lo, 2), round(hi, 2)],
                    "n": len(members),
                    "mean_pred": round(sum(m[0] for m in members) / len(members), 4),
                    "freq": round(sum(m[1] for m in members) / len(members), 4),
                })
        out["calibration"] = cal

    out["verdict"] = verdict(out)
    return out


def verdict(m: dict) -> str:
    """Plain-language read of the numbers, conservative by design."""
    n = m.get("n_resolved", 0)
    if n < MIN_SAMPLE_FOR_VERDICT:
        return f"not enough data ({n} resolved, need {MIN_SAMPLE_FOR_VERDICT})"
    pnl, se = m.get("realized_pnl_per_contract"), m.get("realized_pnl_se") or 0.0
    if pnl is None:
        return "no P&L recorded"
    if pnl - 2 * se > 0:
        return "profitable after fees (statistically clear)"
    if pnl + 2 * se < 0:
        return "losing after fees (statistically clear)"
    return "no clear edge yet (within noise)"


# ---------------- background scoring job ----------------
def _leg_ref(leg: dict) -> tuple[Optional[str], Optional[str]]:
    venue = leg.get("venue")
    if venue == "polymarket":
        return venue, (leg.get("polymarket_condition_id") or "").lower() or None
    if venue == "kalshi":
        return venue, leg.get("kalshi_ticker")
    return venue, None


async def score_pending(db, client, store, now: Optional[float] = None, max_lookups: int = 100,
                        rng: random.Random | None = None) -> dict:
    """Resolve + score pending episodes. Returns counts {live, mock, lookups}."""
    import resolutions  # local: keeps the pure helpers importable without httpx

    now = now or time.time()
    counts = {"live": 0, "mock": 0, "lookups": 0}
    cache: Dict[tuple, Optional[str]] = {}
    async for ep in db.predictions.find({"status": "pending"}):
        legs = ps.legs_of({**ep})
        if ep.get("data_source") == "mock":
            if store.mode != "mock":
                continue  # demo market no longer exists; leave unscored
            outcomes = mock_outcomes_for(ep, store.get, now, rng=rng)
            if outcomes is None:
                await db.predictions.update_one({"_id": ep["_id"]}, {"$set": {"last_checked": now}})
                continue
            src = "mock"
        else:
            still_listed = all(store.get(lg.get("venue"), lg.get("canonical_id")) is not None for lg in legs)
            end_ts = ep.get("end_ts")
            if still_listed and (end_ts is None or float(end_ts) > now):
                continue
            outcomes = {}
            for i, leg in enumerate(legs):
                venue, ref = _leg_ref(leg)
                if not ref:
                    break
                if (venue, ref) not in cache:
                    if counts["lookups"] >= max_lookups:
                        break
                    counts["lookups"] += 1
                    cache[(venue, ref)] = await resolutions.get_outcome(db, client, venue, ref)
                if cache[(venue, ref)] is None:
                    break
                outcomes[i] = cache[(venue, ref)]
            if len(outcomes) != len(legs):
                continue
            src = "live"
        upd = score_episode(ep, outcomes)
        upd["resolved_at"] = now
        upd["resolution_source"] = "simulated" if src == "mock" else "venue"
        res = await db.predictions.update_one({"_id": ep["_id"], "status": "pending"}, {"$set": upd})
        if res.modified_count:
            counts[src] += 1
    return counts


async def engine_report(db, source: str, since: float) -> dict:
    """Scorecard for every engine over resolved episodes since `since` (epoch seconds)."""
    rows_by_engine: Dict[str, List[dict]] = {e: [] for e in ("alpha", "quant", "whale", "micro")}
    pending: Dict[str, int] = {e: 0 for e in rows_by_engine}
    async for r in db.predictions.find({"data_source": source, "status": "resolved", "resolved_at": {"$gte": since}}):
        rows_by_engine.setdefault(r["engine"], []).append(r)
    async for r in db.predictions.find({"data_source": source, "status": "pending"}, {"engine": 1}):
        pending[r["engine"]] = pending.get(r["engine"], 0) + 1
    engines = {}
    for e, rows in rows_by_engine.items():
        m = summarize(rows)
        m["n_pending"] = pending.get(e, 0)
        engines[e] = m
    return {"source": source, "since": since, "engines": engines}
