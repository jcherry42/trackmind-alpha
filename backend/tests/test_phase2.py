"""Phase 2: resolution parsing, engine scorecard, whale track records, GDELT news for Alpha.

Resolution fixtures are copied from real Polymarket Gamma / Kalshi v2 responses
(fetched 2026-11-14). DB tests use mongomock-motor (installed in CI).
"""
from __future__ import annotations

import asyncio
import os
import random
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import markets  # noqa: E402
import news  # noqa: E402
import scorecard  # noqa: E402
import whale_records  # noqa: E402
from engines import alpha as alpha_engine  # noqa: E402
from engines import whale as whale_engine  # noqa: E402
from markets import MarketStore, Quote  # noqa: E402

httpx = pytest.importorskip("httpx")
import live_market  # noqa: E402
import resolutions  # noqa: E402


def _q(venue="polymarket", cid="m1", yes=0.50, bid=None, ask=None, title="Will X happen?", **kw) -> Quote:
    q = Quote(venue=venue, canonical_id=cid, title=title, yes=yes, no=round(1 - yes, 4), volume_24h=10_000,
              liquidity=0.8, updated_at=kw.pop("updated_at", time.time()), history=[], source="live", **kw)
    if bid is not None and ask is not None:
        q.yes_bid, q.yes_ask = bid, ask
        q.no_bid, q.no_ask = round(1 - ask, 4), round(1 - bid, 4)
    return q


@pytest.fixture
def store(monkeypatch):
    s = MarketStore()
    for mod in (markets, alpha_engine, whale_engine):
        monkeypatch.setattr(mod, "market_store", s)
    return s


@pytest.fixture
def db():
    mm = pytest.importorskip("mongomock_motor")
    return mm.AsyncMongoMockClient()["trackmind_test"]


def run(coro):
    return asyncio.run(coro)


# ---------------- resolution parsing (real response shapes) ----------------
POLY_RESOLVED = {  # Gamma, closed market with Over/Under outcomes
    "conditionId": "0xf5a6adfe3b2778ad61b27dc00b0bde7f7735e0291b1eaab22982f2d36033c71b",
    "question": "Grizzlies Team Total: O/U 130.5", "closed": True,
    "outcomes": '["Over", "Under"]', "outcomePrices": '["1", "0"]',
    "umaResolutionStatus": "resolved", "endDate": "2026-11-14T01:00:00Z",
    "bestBid": 0.991, "bestAsk": 1, "acceptingOrders": False,
}
KALSHI_FINAL = {  # Kalshi v2, finalized market
    "ticker": "KXMVECROSSCATEGORY-S2026JWOTYOGQ4KAPTMIW-T4BQRRVTS", "status": "finalized",
    "result": "no", "settlement_value_dollars": "0.0000", "market_type": "binary",
}


class TestResolutionParsing:
    def test_polymarket_resolved_is_positional(self):
        # "Over" is outcome 0 → our "YES" (token_ids[0]).
        assert live_market.parse_polymarket_resolution(POLY_RESOLVED) == "YES"
        flipped = {**POLY_RESOLVED, "outcomePrices": '["0", "1"]'}
        assert live_market.parse_polymarket_resolution(flipped) == "NO"

    def test_polymarket_not_final_until_uma_resolved(self):
        assert live_market.parse_polymarket_resolution({**POLY_RESOLVED, "umaResolutionStatus": "proposed"}) is None
        assert live_market.parse_polymarket_resolution({**POLY_RESOLVED, "closed": False}) is None
        assert live_market.parse_polymarket_resolution({**POLY_RESOLVED, "outcomePrices": '["0.5","0.5"]'}) == "VOID"

    def test_kalshi_finalized(self):
        assert live_market.parse_kalshi_resolution(KALSHI_FINAL) == "NO"
        assert live_market.parse_kalshi_resolution({**KALSHI_FINAL, "status": "active"}) is None

    def test_kalshi_liquidity_from_sizes(self):
        m = {"ticker": "KX-1", "title": "t", "yes_bid_dollars": "0.40", "yes_ask_dollars": "0.42",
             "yes_bid_size_fp": "20000", "yes_ask_size_fp": "5000"}
        q = live_market._parse_kalshi_market(m, time.time())
        assert q.liquidity == 1.0  # 25k contracts × 0.41 ≈ $10k → saturates

    def test_resolution_cache_hits_venue_once(self, db, monkeypatch):
        calls = []

        async def fake(client, ref):
            calls.append(ref)
            return "YES"

        monkeypatch.setattr(resolutions, "fetch_polymarket_resolution", fake)
        assert run(resolutions.get_outcome(db, None, "polymarket", "0xABC")) == "YES"
        assert run(resolutions.get_outcome(db, None, "polymarket", "0xabc")) == "YES"
        assert calls == ["0xABC"]

    def test_unresolved_is_rechecked_only_after_interval(self, db, monkeypatch):
        calls = []

        async def fake(client, ref):
            calls.append(ref)
            return None

        monkeypatch.setattr(resolutions, "fetch_kalshi_resolution", fake)
        run(resolutions.get_outcome(db, None, "kalshi", "KX-1"))
        run(resolutions.get_outcome(db, None, "kalshi", "KX-1"))
        assert len(calls) == 1


# ---------------- scorecard ----------------
def _row(fair, won, mid, price=None, fee=0.0, edge=0.02):
    price = mid if price is None else price
    return {"fair_prob": fair, "won": won, "mid": mid, "expected_edge": edge,
            "pnl_per_contract": (1.0 if won else 0.0) - price - fee}


class TestScorecardMetrics:
    def test_empty(self):
        assert scorecard.summarize([])["n_resolved"] == 0

    def test_skill_positive_for_a_better_forecaster(self):
        rng = random.Random(0)
        rows = []
        for _ in range(400):
            true_p = rng.uniform(0.2, 0.8)
            won = rng.random() < true_p
            # Engine knows the true probability; the market is noisier.
            mkt = min(0.95, max(0.05, true_p + rng.uniform(-0.2, 0.2)))
            rows.append(_row(true_p, won, mkt))
        m = scorecard.summarize(rows)
        assert m["skill_vs_market"] > 0
        assert m["brier"] < m["brier_market"]

    def test_no_skill_when_engine_just_copies_market(self):
        rng = random.Random(1)
        rows = []
        for _ in range(400):
            p = rng.uniform(0.2, 0.8)
            rows.append(_row(p, rng.random() < p, p))
        m = scorecard.summarize(rows)
        assert m["skill_vs_market"] == 0.0
        assert "no clear edge" in m["verdict"] or "losing" in m["verdict"]

    def test_verdict_needs_sample(self):
        m = scorecard.summarize([_row(0.6, True, 0.5) for _ in range(5)])
        assert m["verdict"].startswith("not enough data")

    def test_calibration_bins(self):
        rows = [_row(0.65, i < 13, 0.6) for i in range(20)]
        cal = scorecard.summarize(rows)["calibration"]
        assert cal == [{"bin": [0.6, 0.7], "n": 20, "mean_pred": 0.65, "freq": 0.65}]

    def test_score_episode_net_of_fee(self):
        ep = {"side": "NO", "venue": "kalshi", "canonical_id": "kx", "entry_price": 0.30, "fee": 0.0147}
        upd = scorecard.score_episode(ep, {0: "NO"})
        assert upd["won"] is True and upd["pnl_per_contract"] == pytest.approx(0.70 - 0.0147)
        assert scorecard.score_episode(ep, {0: "YES"})["pnl_per_contract"] == pytest.approx(-0.3147)


class TestScorecardDb:
    SIG = {"engine": "quant", "venue": "polymarket", "canonical_id": "0xab", "polymarket_condition_id": "0xab",
           "side": "YES", "price": 0.41, "mid": 0.40, "fair_prob": 0.46, "expected_edge": 0.03,
           "confidence": 0.7, "data_source": "live", "market": "m"}

    def test_episodes_dedupe_within_gap(self, db):
        t0 = 1_000_000.0
        assert run(scorecard.log_predictions(db, [self.SIG], now=t0)) == 1
        assert run(scorecard.log_predictions(db, [self.SIG], now=t0 + 60)) == 0
        assert run(scorecard.log_predictions(db, [self.SIG], now=t0 + 60 + scorecard.EPISODE_GAP_SEC + 1)) == 1
        ep = run(db.predictions.find_one({"first_seen": t0}))
        assert ep["n_obs"] == 2 and ep["entry_price"] == 0.41  # scored at the first actionable price

    def test_live_episode_scores_after_market_leaves_store(self, db, store, monkeypatch):
        async def fake_outcome(db_, client, venue, ref):
            return "YES" if ref == "0xab" else None

        monkeypatch.setattr(resolutions, "get_outcome", fake_outcome)
        run(scorecard.log_predictions(db, [self.SIG], now=1000.0))
        store.set_live_snapshot([_q(cid="0xab", polymarket_condition_id="0xab")])
        counts = run(scorecard.score_pending(db, None, store, now=2000.0))
        assert counts["live"] == 0  # still listed and not past close → not looked up
        store.set_live_snapshot([_q(cid="other", polymarket_condition_id="0xzz")])
        counts = run(scorecard.score_pending(db, None, store, now=3000.0))
        assert counts["live"] == 1
        ep = run(db.predictions.find_one({}))
        assert ep["status"] == "resolved" and ep["won"] is True
        assert ep["pnl_per_contract"] == pytest.approx(0.59)
        report = run(scorecard.engine_report(db, "live", since=0))
        assert report["engines"]["quant"]["n_resolved"] == 1

    def test_mock_episode_resolves_by_simulation(self, db):
        s = MarketStore()  # mock mode
        sig = {**self.SIG, "data_source": "mock", "canonical_id": "btc_100k_eom", "price": 0.6, "mid": 0.59}
        run(scorecard.log_predictions(db, [sig], now=1000.0))
        counts = run(scorecard.score_pending(db, None, s, now=1000.0 + 10**7, rng=random.Random(3)))
        assert counts["mock"] == 1
        assert run(db.predictions.find_one({}))["resolution_source"] == "simulated"


# ---------------- whale records ----------------
class TestWhaleRecords:
    def test_score_entry_and_stats(self):
        wins = [whale_records.score_entry({"transaction_hash": f"w{i}", "wallet_address": "0xA", "outcome_index": 0,
                                           "price": 0.40, "usdc_size": 100}, "YES") for i in range(6)]
        losses = [whale_records.score_entry({"transaction_hash": f"l{i}", "wallet_address": "0xA", "outcome_index": 0,
                                             "price": 0.40, "usdc_size": 100}, "NO") for i in range(4)]
        st = whale_records.wallet_stats(wins + losses)
        assert st["record_n"] == 10 and st["record_win_rate"] == 0.6
        assert st["record_edge"] == pytest.approx(0.20)   # 0.6 − 0.4
        assert st["record_roi"] == pytest.approx(0.5)     # 6×250 − 1000 over 1000

    def test_shrinkage(self):
        assert whale_records.shrunk_edge(0, 0.3) == 0.0
        assert whale_records.shrunk_edge(20, 0.2, k=20) == pytest.approx(0.1)
        assert whale_records.shrunk_edge(980, 0.2, k=20) == pytest.approx(0.196)

    def test_update_records_end_to_end(self, db, monkeypatch):
        async def fake_outcome(db_, client, venue, ref):
            return {"0xwin": "YES", "0xlose": "NO"}.get(ref)

        monkeypatch.setattr(resolutions, "get_outcome", fake_outcome)
        run(db.tracked_wallets.insert_one({"address": "0xa"}))
        for i, cond in enumerate(["0xwin", "0xwin", "0xlose", "0xpending"]):
            run(db.whale_entries.insert_one({"transaction_hash": f"t{i}", "wallet_address": "0xa", "condition_id": cond,
                                             "outcome_index": 0, "price": 0.5, "usdc_size": 100, "ts": 0.0}))
        counts = run(whale_records.update_records(db, None, now=10_000.0))
        assert counts["scored"] == 3 and counts["wallets"] == 1
        w = run(db.tracked_wallets.find_one({"address": "0xa"}))
        assert w["record_n"] == 3 and w["record_win_rate"] == pytest.approx(0.6667, abs=1e-3)
        # Re-running doesn't double count.
        assert run(whale_records.update_records(db, None, now=10_000.0))["scored"] == 0


class TestWhaleEngineCalibration:
    def _seed(self, db, record_n):
        now = time.time()
        from datetime import datetime, timezone
        for addr in ("0xa", "0xb"):
            run(db.tracked_wallets.insert_one({"address": addr, "record_n": record_n, "record_edge": 0.10}))
            run(db.whale_entries.insert_one({
                "transaction_hash": f"tx-{addr}", "wallet_address": addr, "condition_id": "0xmkt",
                "outcome_index": 0, "outcome": "Yes", "price": 0.40, "usdc_size": 5000.0, "ts": now - 60,
                "observed_at": datetime.now(timezone.utc),
            }))

    def test_calibrated_edge_from_records(self, db, store):
        self._seed(db, record_n=80)
        store.set_live_snapshot([_q(cid="0xmkt", polymarket_condition_id="0xmkt", yes=0.42, bid=0.415, ask=0.425)])
        sigs = run(whale_engine.run_async(db))
        assert len(sigs) == 1
        s = sigs[0]
        assert s["edge_calibrated"] is True and s["record_n"] == 160
        shrunk = whale_records.shrunk_edge(80, 0.10)
        assert s["fair_prob"] == pytest.approx(0.40 + shrunk, abs=1e-4)
        assert s["expected_edge"] == pytest.approx(0.40 + shrunk - 0.425, abs=1e-3)

    def test_price_already_moved_kills_edge(self, db, store):
        self._seed(db, record_n=80)
        # Whales paid 0.40; the market has since run to 0.55 — their edge is gone.
        store.set_live_snapshot([_q(cid="0xmkt", polymarket_condition_id="0xmkt", yes=0.55, bid=0.545, ask=0.555)])
        assert run(whale_engine.run_async(db)) == []

    def test_uncalibrated_when_records_thin(self, db, store):
        self._seed(db, record_n=3)
        store.set_live_snapshot([_q(cid="0xmkt", polymarket_condition_id="0xmkt", yes=0.40, bid=0.395, ask=0.405)])
        sigs = run(whale_engine.run_async(db))
        assert sigs and sigs[0]["edge_calibrated"] is False and "UNCALIBRATED" in sigs[0]["rationale"]


# ---------------- GDELT news + Alpha ----------------
GDELT_SAMPLE = {"articles": [
    {"url": "https://a.example/1", "title": "Fed signals December rate cut", "seendate": "20261114T031500Z",
     "domain": "reuters.com", "language": "English", "sourcecountry": "United States"},
    {"url": "https://b.example/1", "title": "Fed signals December rate cut!", "seendate": "20261114T020000Z",
     "domain": "syndicate.example", "language": "English"},
    {"url": "https://c.example/2", "title": "Powell speaks at Jackson Hole", "seendate": "20261113T120000Z",
     "domain": "apnews.com"},
    {"title": "missing url"},
]}


class TestNews:
    def test_parse_artlist_dedupes_and_sorts(self):
        rows = news.parse_gdelt_artlist(GDELT_SAMPLE)
        assert [r["domain"] for r in rows] == ["reuters.com", "apnews.com"]
        assert rows[0]["seen_ts"] == pytest.approx(1794626100.0)

    def test_query_building(self):
        q = news.build_gdelt_query("Will the Fed cut rates in December 2026?")
        assert q.startswith("fed") and "december" not in q and q.endswith("sourcelang:english")
        assert news.build_gdelt_query("Yes?") is None

    def test_markets_to_refresh_by_volume_and_ttl(self, store):
        a = _q(cid="a", polymarket_condition_id="0xa"); a.volume_24h = 10
        b = _q(cid="b", polymarket_condition_id="0xb"); b.volume_24h = 99
        store.set_live_snapshot([a, b])
        cache = news.NewsCache()
        now = time.time()
        cache.put("b", "q", [], now=now)
        assert [q.canonical_id for q in news.markets_to_refresh(store, cache, now)] == ["a"]


class TestAlphaGdelt:
    def _setup(self, store, monkeypatch, view):
        monkeypatch.setenv("ALPHA_NEWS_SOURCE", "gdelt")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        q = _q(cid="0xfed", polymarket_condition_id="0xfed", title="Will the Fed cut rates in December?",
               yes=0.40, bid=0.395, ask=0.405)
        store.set_live_snapshot([q])
        cache = news.NewsCache()
        cache.put("0xfed", "fed", news.parse_gdelt_artlist(GDELT_SAMPLE))
        monkeypatch.setattr(news, "news_cache", cache)
        prompts = []

        async def fake_llm(prompt, timeout=15.0):
            prompts.append(prompt)
            return view

        monkeypatch.setattr(alpha_engine, "_llm_json", fake_llm)
        return prompts

    def test_relevant_news_makes_a_signal(self, store, monkeypatch):
        prompts = self._setup(store, monkeypatch, {"relevant": True, "score": 0.8, "used": [0], "thesis": "dovish"})
        assert alpha_engine.mode() == "gdelt" and alpha_engine.status()["active"]
        sigs = run(alpha_engine.run_async(use_llm=True, score_cache={}))
        assert len(sigs) == 1
        s = sigs[0]
        assert s["catalyst_source"] == "gdelt" and s["side"] == "YES"
        assert s["headline_urls"] == ["https://a.example/1"]
        assert s["fair_prob"] == pytest.approx(0.40 + 0.8 * alpha_engine.ALPHA_MAX_SHIFT)
        assert "0.400" in prompts[0] and "NOT yet priced in" in prompts[0]

    def test_irrelevant_or_zero_news_makes_nothing(self, store, monkeypatch):
        self._setup(store, monkeypatch, {"relevant": False, "score": 0.9, "used": []})
        assert run(alpha_engine.run_async(use_llm=True, score_cache={})) == []

    def test_llm_judgement_is_cached_per_headline_set(self, store, monkeypatch):
        prompts = self._setup(store, monkeypatch, {"relevant": True, "score": 0.8, "used": [0]})
        cache: dict = {}
        run(alpha_engine.run_async(use_llm=True, score_cache=cache))
        run(alpha_engine.run_async(use_llm=True, score_cache=cache))
        assert len(prompts) == 1

    def test_off_without_llm_key(self, store, monkeypatch):
        self._setup(store, monkeypatch, None)
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
        st = alpha_engine.status()
        assert st["active"] is False and "LLM key" in st["reason"]
        assert run(alpha_engine.run_async(use_llm=True, score_cache={})) == []
