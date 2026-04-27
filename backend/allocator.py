"""Master allocator + risk system.

Takes signals from all engines, applies risk constraints, returns a ranked
list of deployable orders sized to bankroll.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class RiskConfig:
    max_daily_drawdown: float = 0.05
    max_single_trade: float = 0.15
    max_correlated_exposure: float = 0.35
    min_cash_buffer: float = 0.20


def _score(sig: dict) -> float:
    """Higher is better. Penalises risk, rewards confidence × edge × liquidity."""
    return (
        sig["confidence"] * sig["expected_edge"] * (0.5 + 0.5 * sig["liquidity"])
        / max(0.005, sig["risk"])
    )


def allocate(
    signals: List[dict],
    bankroll: float,
    pnl_today_pct: float,
    paused_engines: set[str],
    config: RiskConfig | None = None,
) -> dict:
    cfg = config or RiskConfig()

    # 1. Filter paused engines
    candidates = [s for s in signals if s["engine"] not in paused_engines]

    # 2. Defensive mode if drawdown approached
    drawdown_ratio = max(0.0, -pnl_today_pct) / cfg.max_daily_drawdown if cfg.max_daily_drawdown else 0
    drawdown_ratio = min(1.0, drawdown_ratio)
    sizing_dampener = 1.0 - 0.6 * drawdown_ratio  # up to 60% size cut as DD nears limit
    defensive = drawdown_ratio > 0.6

    # 3. Score & rank
    candidates.sort(key=_score, reverse=True)

    deployable_cash = max(0.0, bankroll * (1.0 - cfg.min_cash_buffer))
    spent = 0.0
    correlated: Dict[str, float] = {}  # by canonical_id

    orders: List[dict] = []
    for s in candidates:
        size_pct = min(s["size_hint"], cfg.max_single_trade) * sizing_dampener
        notional = round(min(deployable_cash - spent, bankroll * size_pct), 2)
        if notional <= 1.0:
            continue
        # Correlation cap by canonical market
        cap = bankroll * cfg.max_correlated_exposure
        used = correlated.get(s["canonical_id"], 0.0)
        if used + notional > cap:
            notional = round(max(0.0, cap - used), 2)
            if notional <= 1.0:
                continue
        correlated[s["canonical_id"]] = used + notional
        spent += notional
        orders.append({
            **s,
            "notional": notional,
            "rank_score": round(_score(s), 4),
        })
        if spent >= deployable_cash:
            break

    # 4. Engine allocation summary
    by_engine: Dict[str, float] = {"alpha": 0, "quant": 0, "whale": 0, "micro": 0}
    for o in orders:
        by_engine[o["engine"]] = by_engine.get(o["engine"], 0) + o["notional"]
    total_dep = sum(by_engine.values()) or 1.0
    allocation_pct = {k: round(100 * v / total_dep, 1) for k, v in by_engine.items()}

    return {
        "orders": orders,
        "deployed": round(spent, 2),
        "deployable_cash": round(deployable_cash, 2),
        "cash_buffer": round(bankroll - spent, 2),
        "defensive_mode": defensive,
        "drawdown_ratio": round(drawdown_ratio, 3),
        "engine_allocation": allocation_pct,
        "engine_notional": {k: round(v, 2) for k, v in by_engine.items()},
    }
