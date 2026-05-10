"""Deterministic unit tests for the allocator and engines.

Uses fixed seeds / crafted inputs so results are reproducible.
These run offline (no HTTP / no DB) and are fast.
"""
from __future__ import annotations

import os
import random
import sys

import pytest

# Make `backend/` importable as root when running `pytest tests/`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from allocator import RiskConfig, allocate  # noqa: E402
from engines import quant as quant_engine, whale as whale_engine, micro as micro_engine  # noqa: E402


# ------------ allocator ------------
class TestAllocator:
    def _sig(self, engine: str, edge: float, conf: float, size: float = 0.06) -> dict:
        return {
            "id": f"{engine}-1",
            "engine": engine,
            "canonical_id": "q1",
            "venue": "polymarket",
            "market": "Test",
            "side": "YES",
            "price": 0.5,
            "confidence": conf,
            "expected_edge": edge,
            "risk": 0.04,
            "liquidity": 0.8,
            "size_hint": size,
            "rationale": "",
        }

    def test_empty_signals_returns_empty_plan(self):
        plan = allocate([], bankroll=10_000, pnl_today_pct=0.0, paused_engines=set())
        assert plan["orders"] == []
        assert plan["deployed"] == 0

    def test_respects_bankroll_cap(self):
        # Two high-conviction alpha signals wanting 50% sizes — allocator should clamp to bankroll
        # minus the min_cash_buffer (default 20%).
        sigs = [
            self._sig("alpha", 0.15, 0.90, size=0.50),
            self._sig("quant", 0.15, 0.90, size=0.50),
        ]
        plan = allocate(sigs, bankroll=1_000, pnl_today_pct=0.0, paused_engines=set())
        total = sum(o["notional"] for o in plan["orders"])
        # Can't spend more than 80% of bankroll (keep 20% cash buffer).
        assert total <= 800.01

    def test_paused_engines_are_excluded(self):
        sigs = [
            self._sig("alpha", 0.12, 0.80),
            self._sig("quant", 0.10, 0.75),
            self._sig("whale", 0.09, 0.70),
        ]
        plan = allocate(sigs, bankroll=10_000, pnl_today_pct=0.0, paused_engines={"alpha", "whale"})
        engines_in_plan = {o["engine"] for o in plan["orders"]}
        assert "alpha" not in engines_in_plan
        assert "whale" not in engines_in_plan
        if plan["orders"]:
            assert engines_in_plan == {"quant"}

    def test_drawdown_scales_size_down(self):
        """As pnl_today_pct goes more negative, allocator should ship less."""
        sigs = [self._sig("alpha", 0.12, 0.85, size=0.10)]
        ok = allocate(sigs, bankroll=10_000, pnl_today_pct=0.0, paused_engines=set())
        dd = allocate(sigs, bankroll=10_000, pnl_today_pct=-0.04, paused_engines=set())
        ok_total = sum(o["notional"] for o in ok["orders"])
        dd_total = sum(o["notional"] for o in dd["orders"])
        assert dd_total < ok_total, "drawdown must scale size down"

    def test_drawdown_clamps_near_limit(self):
        """Past the max_daily_drawdown (5% default), sizing is dampened to ≤40% of base."""
        sigs = [self._sig("alpha", 0.12, 0.85, size=0.10)]
        baseline = allocate(sigs, bankroll=10_000, pnl_today_pct=0.0, paused_engines=set())
        deep_dd = allocate(sigs, bankroll=10_000, pnl_today_pct=-0.10, paused_engines=set())
        base_total = sum(o["notional"] for o in baseline["orders"])
        dd_total = sum(o["notional"] for o in deep_dd["orders"])
        assert dd_total <= base_total * 0.45  # dampener clamps to ~40%


# ------------ engines ------------
class TestQuantEngine:
    def test_run_returns_valid_signals(self):
        random.seed(42)
        sigs = quant_engine.run()
        assert isinstance(sigs, list)
        for s in sigs:
            assert s["engine"] == "quant"
            assert 0 <= s["confidence"] <= 1
            assert s["side"] in ("YES", "NO")
            assert s["expected_edge"] > 0
            assert 0 <= s["risk"] <= 1
            assert "canonical_id" in s and "market" in s

    def test_is_deterministic_with_seed(self):
        random.seed(7)
        a = quant_engine.run()
        random.seed(7)
        b = quant_engine.run()
        assert [s["canonical_id"] for s in a] == [s["canonical_id"] for s in b]


class TestWhaleEngine:
    def test_run_returns_valid_signals(self):
        random.seed(42)
        sigs = whale_engine.run()
        for s in sigs:
            assert s["engine"] == "whale"
            assert 0 <= s["confidence"] <= 1
            assert s["side"] in ("YES", "NO")
            # whale signals should include wallet cluster metadata
            assert "rationale" in s


class TestMicroEngine:
    def test_run_returns_valid_arbs(self):
        random.seed(42)
        sigs = micro_engine.run()
        for s in sigs:
            assert s["engine"] == "micro"
            assert 0 <= s["confidence"] <= 1
            # arb signals have non-zero edge by construction (else filtered out)
            assert s["expected_edge"] >= 0
