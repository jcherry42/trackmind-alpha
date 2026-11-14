"""Core phase 1: market data integrity, execution identifiers, honest arb and settlement.

Offline, deterministic, no DB. Each test builds its own MarketStore state so it
can't leak into other tests.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fees  # noqa: E402
import markets  # noqa: E402
import pairs  # noqa: E402
import paper_settlement as ps  # noqa: E402
from engines import alpha as alpha_engine  # noqa: E402
from engines import micro as micro_engine  # noqa: E402
from engines import quant as quant_engine  # noqa: E402
from markets import MarketStore, Quote  # noqa: E402


# ---------------- helpers ----------------
def _q(venue="polymarket", cid="m1", yes=0.50, bid=None, ask=None, title="Will X happen by Friday?", **kw) -> Quote:
    now = kw.pop("updated_at", time.time())
    q = Quote(
        venue=venue, canonical_id=cid, title=title, yes=yes, no=round(1 - yes, 4),
        volume_24h=10_000, liquidity=0.8, updated_at=now, history=[], source="live", **kw,
    )
    if bid is not None and ask is not None:
        q.yes_bid, q.yes_ask = bid, ask
        q.no_bid, q.no_ask = round(1 - ask, 4), round(1 - bid, 4)
    return q


@pytest.fixture
def store(monkeypatch):
    """Fresh store patched into every module that imported the singleton."""
    s = MarketStore()
    for mod in (markets, micro_engine, quant_engine, alpha_engine):
        monkeypatch.setattr(mod, "market_store", s)
    return s


# ---------------- market store ----------------
class TestMarketStore:
    def test_tick_is_noop_once_live(self, store):
        store.set_live_snapshot([_q(yes=0.40, bid=0.39, ask=0.41)])
        before = (store.get("polymarket", "m1").yes, list(store.get("polymarket", "m1").history))
        for _ in range(10):
            store.tick()
        q = store.get("polymarket", "m1")
        assert (q.yes, q.history) == before, "live quotes must never be perturbed"
        assert store.mode == "live"

    def test_history_carries_forward_across_snapshots(self, store):
        store.set_live_snapshot([_q(yes=0.40)])
        store.set_live_snapshot([_q(yes=0.42)])
        store.set_live_snapshot([_q(yes=0.45)])
        assert store.get("polymarket", "m1").history == [0.40, 0.42, 0.45]

    def test_carried_forward_quote_adds_no_observation(self, store):
        store.set_live_snapshot([_q(yes=0.40)])
        same = store.get("polymarket", "m1")
        store.set_live_snapshot([same])  # e.g. venue fetch failed, previous quote reused
        assert store.get("polymarket", "m1").history == [0.40]

    def test_mock_mode_has_books_and_pairs(self):
        s = MarketStore()
        q = s.all()[0]
        assert q.yes_ask > q.yes > q.yes_bid
        assert len(s.pairs()) == 12

    def test_find_by_condition_id(self, store):
        store.set_live_snapshot([_q(polymarket_condition_id="0xabc")])
        assert store.find_by_condition_id("0xABC").canonical_id == "m1"
        assert store.find_by_condition_id("0xnope") is None

    def test_execution_refs(self):
        q = _q(polymarket_condition_id="0xabc", polymarket_token_ids=["t0", "t1"])
        refs = markets.execution_refs(q)
        assert refs["polymarket_token_ids"] == ["t0", "t1"]
        assert refs["polymarket_condition_id"] == "0xabc"
        assert refs["data_source"] == "live"


# ---------------- fees ----------------
class TestFees:
    def test_kalshi_fee_formula_and_rounding(self):
        # 0.07 * 0.5 * 0.5 = 0.0175 per contract; 10 contracts = 0.175 -> rounds UP to 0.18
        assert fees.fee_per_contract("kalshi", 0.5) == pytest.approx(0.0175)
        assert fees.order_fee("kalshi", 0.5, 10) == 0.18

    def test_polymarket_default_fee_free(self, monkeypatch):
        monkeypatch.delenv("POLYMARKET_TAKER_FEE_BPS", raising=False)
        assert fees.fee_per_contract("polymarket", 0.5) == 0.0
        monkeypatch.setenv("POLYMARKET_TAKER_FEE_BPS", "100")
        assert fees.fee_per_contract("polymarket", 0.5) == pytest.approx(0.005)


# ---------------- pairs ----------------
class TestPairs:
    def test_load_verified_pairs_validates_rows(self, tmp_path):
        p = tmp_path / "pairs.json"
        p.write_text(json.dumps([
            {"polymarket_condition_id": "0xAB", "kalshi_ticker": "KX-1", "polarity": "same"},
            {"polymarket_condition_id": "0xCD", "kalshi_ticker": "KX-2", "polarity": "sideways"},  # bad
            {"kalshi_ticker": "KX-3"},  # missing condition id
        ]))
        rows = pairs.load_verified_pairs(str(p))
        assert [r["kalshi_ticker"] for r in rows] == ["KX-1"]
        assert rows[0]["polymarket_condition_id"] == "0xab"

    def test_resolve_pairs_maps_onto_snapshot(self):
        p = _q("polymarket", "pm1", polymarket_condition_id="0xab")
        k = _q("kalshi", "kx-1", kalshi_ticker="KX-1")
        out = pairs.resolve_pairs([p], [k], [{"polymarket_condition_id": "0xab", "kalshi_ticker": "KX-1", "polarity": "inverted"}])
        assert out == [(("polymarket", "pm1"), ("kalshi", "kx-1"), "pair_kx-1", "inverted")]

    def test_similar_titles_are_only_suggestions(self, store):
        p = _q("polymarket", "pm1", title="Fed cuts rates in December 2026", polymarket_condition_id="0xab", bid=0.30, ask=0.31)
        k = _q("kalshi", "kx-1", title="Fed cuts rates December 2026", kalshi_ticker="KX-1", bid=0.80, ask=0.81)
        assert pairs.suggest_pairs([p], [k]), "should be suggested for review"
        store.set_live_snapshot([p, k], pairs=[])  # nobody verified it
        assert micro_engine.run() == [], "unverified pairs must never be traded"


# ---------------- live parsing ----------------
class TestLiveParsing:
    def setup_method(self):
        pytest.importorskip("httpx")
        import live_market
        self.lm = live_market

    def test_polymarket_book_mirrors_to_no_side(self):
        m = {
            "conditionId": "0xABC", "question": "Will it rain?", "outcomePrices": '["0.40","0.60"]',
            "clobTokenIds": '["tok_yes","tok_no"]', "outcomes": '["Yes","No"]',
            "bestBid": "0.39", "bestAsk": "0.42", "liquidityNum": "5000", "endDate": "2026-12-01T00:00:00Z",
        }
        q = self.lm._parse_polymarket_market(m, time.time())
        assert q.polymarket_token_ids == ["tok_yes", "tok_no"]
        assert q.polymarket_condition_id == "0xabc"
        assert (q.yes_bid, q.yes_ask) == (0.39, 0.42)
        assert q.no_ask == pytest.approx(0.61) and q.no_bid == pytest.approx(0.58)
        assert q.end_ts is not None

    def test_polymarket_rejects_multi_outcome_and_resolved(self):
        base = {"conditionId": "0x1", "clobTokenIds": '["a","b","c"]', "outcomePrices": '["0.3","0.3","0.4"]'}
        assert self.lm._parse_polymarket_market(base, 0) is None
        resolved = {"conditionId": "0x1", "clobTokenIds": '["a","b"]', "outcomePrices": '["1","0"]'}
        assert self.lm._parse_polymarket_market(resolved, 0) is None

    def test_kalshi_parse(self):
        m = {"ticker": "KX-1", "title": "Rain?", "yes_bid_dollars": "0.30", "yes_ask_dollars": "0.33",
             "no_bid_dollars": "0.67", "no_ask_dollars": "0.70", "close_time": "2026-12-01T00:00:00Z"}
        q = self.lm._parse_kalshi_market(m, time.time())
        assert q.kalshi_ticker == "KX-1" and q.no_ask == 0.70 and q.yes == pytest.approx(0.315)


# ---------------- micro ----------------
class TestMicro:
    def _pair(self, store, p_bid, p_ask, k_bid, k_ask, polarity="same", age=0.0):
        now = time.time() - age
        p = _q("polymarket", "pm1", bid=p_bid, ask=p_ask, yes=(p_bid + p_ask) / 2, polymarket_condition_id="0xab",
               polymarket_token_ids=["t0", "t1"], updated_at=now)
        k = _q("kalshi", "kx-1", bid=k_bid, ask=k_ask, yes=(k_bid + k_ask) / 2, kalshi_ticker="KX-1", updated_at=now)
        store.set_live_snapshot([p, k], pairs=[(("polymarket", "pm1"), ("kalshi", "kx-1"), "pair_kx-1", polarity)])
        # set_live_snapshot doesn't touch updated_at, so staleness tests work.

    def test_real_arb_on_asks_after_fees(self, store, monkeypatch):
        monkeypatch.delenv("POLYMARKET_TAKER_FEE_BPS", raising=False)
        # Poly YES ask 0.40 ; Kalshi NO ask = 1 - 0.55 = 0.45 → 0.85 + kalshi fee(0.45)=0.0173 → edge ≈ 0.133
        self._pair(store, 0.39, 0.40, 0.55, 0.56)
        sigs = micro_engine.run()
        assert len(sigs) == 1
        s = sigs[0]
        assert s["executable_live"] is False
        assert s["expected_edge"] == pytest.approx(1 - 0.40 - 0.45 - 0.07 * 0.45 * 0.55, abs=1e-3)
        legs = {(leg["venue"], leg["side"]) for leg in s["legs"]}
        assert legs == {("polymarket", "YES"), ("kalshi", "NO")}
        assert any(leg.get("polymarket_token_ids") == ["t0", "t1"] for leg in s["legs"])

    def test_mid_arb_that_disappears_at_the_ask_is_not_emitted(self, store):
        # Mids: 0.48 YES poly + NO kalshi (1-0.505)=0.495 → 0.975 < 1 looks like an arb on mids,
        # but asks are 0.50 + 0.51 = 1.01 → no arb.
        self._pair(store, 0.46, 0.50, 0.49, 0.52)
        assert micro_engine.run() == []

    def test_stale_quotes_skipped(self, store):
        self._pair(store, 0.39, 0.40, 0.55, 0.56, age=600)
        assert micro_engine.run() == []

    def test_inverted_polarity_uses_yes_plus_yes(self, store):
        # Kalshi YES == Poly NO. Poly YES ask 0.40 + Kalshi YES ask 0.45 → covers both outcomes.
        self._pair(store, 0.39, 0.40, 0.44, 0.45, polarity="inverted")
        s = micro_engine.run()[0]
        assert {leg["side"] for leg in s["legs"]} == {"YES"}


# ---------------- quant / alpha ----------------
class TestQuantAlpha:
    def _trending_store(self, store, bid_ask_spread):
        hist = [0.40 + 0.004 * i for i in range(30)]
        q = _q(yes=hist[-1], bid=hist[-1] - bid_ask_spread / 2, ask=hist[-1] + bid_ask_spread / 2,
               polymarket_condition_id="0xab", polymarket_token_ids=["t0", "t1"])
        store.set_live_snapshot([q])
        store.get("polymarket", "m1").history = hist

    def test_quant_signals_carry_execution_ids(self, store):
        self._trending_store(store, 0.01)
        sigs = quant_engine.run()
        assert sigs and sigs[0]["polymarket_token_ids"] == ["t0", "t1"]
        assert sigs[0]["price"] == store.get("polymarket", "m1").yes_ask  # priced at the ask

    def test_quant_wide_spread_kills_edge(self, store):
        self._trending_store(store, 0.20)
        assert quant_engine.run() == []

    def test_alpha_refuses_synthetic_news_on_live_data(self, store, monkeypatch):
        monkeypatch.delenv("ALPHA_ALLOW_SYNTHETIC", raising=False)
        store.set_live_snapshot([_q(title="Will BTC close above $100K?", bid=0.40, ask=0.41, yes=0.405)])
        assert asyncio.run(alpha_engine.run_async(use_llm=False)) == []
        st = alpha_engine.status()
        assert st["active"] is False and "synthetic" in st["reason"]

    def test_alpha_whole_word_matching(self):
        assert alpha_engine._match_catalysts("Will the Pittsburgh Steelers win?") == []
        assert alpha_engine._match_catalysts("Netherlands to win Euro 2028") == []
        assert alpha_engine._match_catalysts("ETH above $5,000 by quarter end")

    def test_alpha_anchors_on_market_price(self, store, monkeypatch):
        monkeypatch.setenv("ALPHA_ALLOW_SYNTHETIC", "true")
        store.set_live_snapshot([_q(title="NVDA beats EPS estimate", yes=0.90, bid=0.895, ask=0.905)])
        for s in asyncio.run(alpha_engine.run_async(use_llm=False)):
            # Max shift is ALPHA_MAX_SHIFT (0.08) from the market's own mid — a 0.90 market
            # can't be called a NO just because the narrative is positive.
            assert s["side"] == "YES"


# ---------------- paper settlement ----------------
def _lookup_from(*quotes):
    m = {(q.venue, q.canonical_id): q for q in quotes}
    return lambda v, c: m.get((v, c))


class TestPaperSettlement:
    def test_live_positions_are_never_paper_settled(self):
        assert ps.is_paper({"broker": "paper"}) and ps.is_paper({})
        assert not ps.is_paper({"broker": "polymarket"})

    def test_realized_pnl_win_and_loss(self):
        pos = {"side": "YES", "venue": "polymarket", "canonical_id": "m1", "entry_price": 0.40, "contracts": 100, "fees": 1.0}
        assert ps.realized_pnl(pos, {0: "YES"}) == (1.0, 59.0)
        assert ps.realized_pnl(pos, {0: "NO"}) == (0.0, -41.0)

    def test_arb_pays_one_regardless_of_outcome(self):
        q = _q("polymarket", "pm1", yes=0.5)
        pos = {"side": "ARB", "entry_price": 0.90, "contracts": 10, "fees": 0.2, "pair_polarity": "same",
               "legs": [{"venue": "polymarket", "canonical_id": "pm1", "side": "YES"},
                        {"venue": "kalshi", "canonical_id": "kx", "side": "NO"}]}
        rng = random.Random(3)
        for _ in range(50):
            out = ps.mock_outcomes(pos, _lookup_from(q), dt_sec=10**9, rng=rng)
            assert ps.realized_pnl(pos, out) == (1.0, 0.8)

    def test_inverted_arb_pays_one(self):
        q = _q("polymarket", "pm1", yes=0.5)
        pos = {"side": "ARB", "entry_price": 0.90, "contracts": 10, "fees": 0.0, "pair_polarity": "inverted",
               "legs": [{"venue": "kalshi", "canonical_id": "kx", "side": "YES"},
                        {"venue": "polymarket", "canonical_id": "pm1", "side": "YES"}]}
        rng = random.Random(5)
        for _ in range(50):
            out = ps.mock_outcomes(pos, _lookup_from(q), dt_sec=10**9, rng=rng)
            assert ps.realized_pnl(pos, out)[0] == 1.0

    def test_mock_resolution_is_unbiased(self):
        """Buying at the market price with no edge must not make money on average."""
        q = _q(yes=0.30)
        pos = {"side": "YES", "venue": "polymarket", "canonical_id": "m1", "entry_price": 0.30, "contracts": 1, "fees": 0.0,
               "expected_edge": 0.25}  # a big claimed edge must NOT tilt the outcome
        rng = random.Random(11)
        pnls = [ps.realized_pnl(pos, ps.mock_outcomes(pos, _lookup_from(q), 10**9, rng))[1] for _ in range(4000)]
        assert abs(sum(pnls) / len(pnls)) < 0.03

    def test_resolution_hazard_is_time_based(self):
        q = _q(yes=0.5)
        pos = {"side": "YES", "venue": "polymarket", "canonical_id": "m1", "entry_price": 0.5, "contracts": 1}
        rng = random.Random(1)
        hits = sum(ps.mock_outcomes(pos, _lookup_from(q), 1.0, rng, mean_life_sec=1800) is not None for _ in range(5000))
        assert hits < 20, "1s of elapsed time should almost never resolve a 30-minute-life position"

    def test_legacy_arb_position_without_legs_still_pays_one(self):
        pos = {"side": "ARB", "canonical_id": "btc", "entry_price": 0.95, "contracts": 10, "fees": 0.0}
        q = _q("polymarket", "btc", yes=0.6)
        out = ps.mock_outcomes(pos, _lookup_from(q), 10**9, random.Random(2))
        assert ps.realized_pnl(pos, out)[0] == 1.0

    def test_mark_value_uses_bid(self):
        q = _q(yes=0.50, bid=0.48, ask=0.52)
        pos = {"side": "YES", "venue": "polymarket", "canonical_id": "m1"}
        assert ps.mark_value(pos, _lookup_from(q)) == 0.48
        assert ps.mark_value(pos, _lookup_from()) is None


# ---------------- brokers ----------------
class TestBrokers:
    def setup_method(self):
        pytest.importorskip("httpx")
        import broker
        self.broker = broker

    def test_paper_fees_follow_venue(self):
        b = self.broker.PaperBroker()
        sig = {"engine": "quant", "canonical_id": "x", "market": "m", "side": "YES", "price": 0.5,
               "liquidity": 0.9, "venue": "kalshi"}
        fill = asyncio.run(b.place_order(user_id="u", signal=sig, notional=50.0))
        assert fill["price"] == 0.5 and fill["contracts"] == 100
        assert fill["fees"] == fees.order_fee("kalshi", 0.5, 100)

    def test_kalshi_no_order_uses_no_price(self, monkeypatch):
        captured = {}

        class FakeResp:
            status_code = 201
            text = ""

            def json(self):
                return {"order": {"order_id": "o1", "status": "resting"}}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, path, content=None, headers=None):
                captured["body"] = json.loads(content)
                return FakeResp()

        monkeypatch.setattr(self.broker.httpx, "AsyncClient", FakeClient)
        kb = self.broker.KalshiBroker({"key_id": "k", "private_key_pem": "x"})
        monkeypatch.setattr(kb, "_sign_headers", lambda m, p: {})
        sig = {"engine": "quant", "canonical_id": "x", "market": "m", "side": "NO", "price": 0.30, "kalshi_ticker": "KX-1"}
        fill = asyncio.run(kb.place_order(user_id="u", signal=sig, notional=30.0))
        body = captured["body"]
        assert body["side"] == "no" and body["no_price"] == 30 and "yes_price" not in body
        assert body["count"] == 100
        assert fill["order_status"] == "submitted"

    def test_live_brokers_refuse_arb(self):
        kb = self.broker.KalshiBroker({"key_id": "k", "private_key_pem": "x"})
        with pytest.raises(RuntimeError):
            asyncio.run(kb.place_order(user_id="u", signal={"side": "ARB", "price": 0.9, "kalshi_ticker": "K"}, notional=10))
