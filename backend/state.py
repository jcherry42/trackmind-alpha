"""Process-wide mutable state for the TRACKMIND backend.

Lives in a dedicated module so route handlers and helper functions can share
caches, locks, and rate-limit windows without each one redefining them.
Kept purely as a single source of truth — NO route handlers belong here.

State owners:
  - RateLimit: per-IP sliding window per named bucket (auth/trade/etc.)
  - _user_locks: per-user asyncio.Lock for serialising bankroll mutations
  - _engine_cache: latest signals from the 4 engines, keyed by ID
  - _alpha_cache: LLM narrative scores, 3-minute bucket
  - _llm_metrics: counters surfaced to Prometheus
  - _master_signal_state: dedupe state for the master-signal fan-out
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any, Dict, List

from fastapi import HTTPException, Request


# ---------- rate limiter ----------
class RateLimit:
    """Sliding-window rate limiter per-IP for a named bucket.

    Call :meth:`check(request)` at the top of a route handler. Raises 429 if
    the caller has exceeded `max_calls` in the last `per_seconds` window.
    """

    # Shared window store across instances. Key is "<bucket>:<ip>".
    _windows: Dict[str, List[float]] = defaultdict(list)

    def __init__(self, bucket: str, max_calls: int, per_seconds: float):
        self.bucket = bucket
        self.max = max_calls
        self.window = per_seconds

    def check(self, request: Request) -> None:
        # Use the left-most XFF entry when behind an L7 proxy so the limiter
        # windows the originating client, not the gateway pod.
        xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For") or ""
        ip = xff.split(",")[0].strip() if xff else (request.client.host if request.client else "unknown")
        key = f"{self.bucket}:{ip}"
        now = time.time()
        hist = RateLimit._windows[key]
        cutoff = now - self.window
        while hist and hist[0] < cutoff:
            hist.pop(0)
        if len(hist) >= self.max:
            retry = int(hist[0] + self.window - now) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {self.max} per {int(self.window)}s. Retry in {retry}s.",
            )
        hist.append(now)


# Named limiter instances. Adjust these to tune surface-area limits.
rl_login = RateLimit("login", 60, 60)
rl_register = RateLimit("register", 5, 3600)
rl_refresh = RateLimit("refresh", 60, 60)
rl_trade = RateLimit("trade", 60, 60)
rl_auto_deploy = RateLimit("auto_deploy", 30, 60)


# ---------- per-user serialisation ----------
# Used by settlement & bankroll mutators so two concurrent requests from the
# same user can't double-settle a position.
user_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


# ---------- engine cache ----------
# Latest snapshot from all 4 engines. Replaced wholesale by refresh_engines().
engine_cache: Dict[str, Any] = {
    "signals": [],   # list of all signals across engines
    "by_id": {},     # signal_id -> signal dict
    "by_engine": {"alpha": [], "quant": [], "whale": [], "micro": []},
    "updated_at": 0.0,
}
engine_cache_lock = asyncio.Lock()


# ---------- LLM caching & metrics ----------
# Alpha engine narrative score cache: keyed by (canonical_id, 3-minute bucket).
alpha_cache: Dict[str, Dict[str, Any]] = {}
alpha_cache_lock = asyncio.Lock()

# LLM telemetry — published to Prometheus in refresh_engines().
llm_metrics: Dict[str, Any] = {
    "calls": 0,
    "cache_hits": 0,
    "failures": 0,
    "total_ms": 0.0,
}


# ---------- master-signal fan-out dedupe ----------
# Holds the last signal id + ts we fanned out, to enforce the cooldown window.
master_signal_state: Dict[str, Any] = {"last_id": None, "last_ts": 0.0}
