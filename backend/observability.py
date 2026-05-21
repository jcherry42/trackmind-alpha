"""Observability primitives: JSON structured logging + Prometheus metrics.

Keep this module tiny and dependency-light so it's easy to swap the transport later
(e.g., adding Sentry, OTLP, or Loki) without touching business code.
"""
from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# ----- request-scoped context (propagates through asyncio) -----
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")


class JsonLogFormatter(logging.Formatter):
    """One-line JSON per record; fails safe to plain text if a value can't serialise."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        base: dict[str, Any] = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "user_id": user_id_var.get(),
        }
        # stash any extras (record.__dict__ contains standard + custom attrs)
        for k, v in record.__dict__.items():
            if k in base or k.startswith("_"):
                continue
            if k in ("args", "msg", "levelname", "levelno", "pathname", "filename",
                     "module", "exc_info", "exc_text", "stack_info", "lineno",
                     "funcName", "created", "msecs", "relativeCreated", "thread",
                     "threadName", "processName", "process", "name", "taskName",
                     "getMessage"):
                continue
            try:
                json.dumps(v)
                base[k] = v
            except Exception:
                base[k] = repr(v)
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        try:
            return json.dumps(base, default=str)
        except Exception:
            return super().format(record)


def configure_json_logging(level: int = logging.INFO) -> None:
    """Replace the default handlers on the root logger with one JSON stream handler.

    Call this ONCE at process startup. Idempotent.
    """
    root = logging.getLogger()
    if getattr(root, "_json_configured", False):
        return
    # Wipe existing handlers so we don't double-log.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    # Dampen chatty third-party noise in prod.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    setattr(root, "_json_configured", True)


# ----- Prometheus metrics -----
http_requests_total = Counter(
    "tm_http_requests_total",
    "HTTP requests, counted",
    ["method", "path", "status"],
)
http_request_latency = Histogram(
    "tm_http_request_latency_seconds",
    "HTTP request latency (seconds)",
    ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
llm_calls_total = Counter("tm_llm_calls_total", "LLM calls initiated (any outcome)")
llm_cache_hits_total = Counter("tm_llm_cache_hits_total", "LLM cache hits (saved a call)")
llm_failures_total = Counter("tm_llm_failures_total", "LLM calls that returned None / raised")
llm_latency = Histogram("tm_llm_latency_seconds", "LLM call end-to-end latency (seconds)")
llm_quota_exceeded_total = Counter(
    "tm_llm_quota_exceeded_total", "Times a user hit their per-day LLM quota", ["user_id"]
)
active_users_gauge = Gauge("tm_active_users", "Distinct users seen in the last 5 minutes")
open_positions_gauge = Gauge("tm_open_positions", "Currently open positions")
engine_signals_gauge = Gauge("tm_engine_signals", "Signals currently cached", ["engine"])

# ----- Live-trading & exchange health metrics (P2 observability) -----
# Counter: every successful live order placed at a real venue (paper trades excluded).
live_orders_placed_total = Counter(
    "tm_live_orders_placed_total",
    "Live (real-money) orders successfully placed",
    ["venue", "side"],
)
# Counter: every fill we observe — either from the Kalshi WS fill channel, or from
# a matched Polymarket PayoutRedemption event on Polygon. action ∈ {buy, sell, settle}.
live_fills_received_total = Counter(
    "tm_live_fills_received_total",
    "Live fills observed from broker WS / on-chain settlement",
    ["venue", "action"],
)
# Counter: WS reconnect attempts (excluding the initial connect). venue=kalshi for now.
ws_reconnects_total = Counter(
    "tm_ws_reconnects_total",
    "WebSocket reconnect attempts",
    ["venue"],
)
# Counter: RPC errors hitting the chain providers (Alchemy, etc.).
rpc_errors_total = Counter(
    "tm_rpc_errors_total",
    "JSON-RPC errors from the upstream chain provider",
    ["provider"],
)
# Gauge: today's realized PnL across all users (USD). Refreshed on each /admin/metrics call.
realized_pnl_today_usd_gauge = Gauge(
    "tm_realized_pnl_today_usd",
    "Sum of realized PnL across all users for the current UTC day",
)
# Gauge: today's live (real-money) notional placed across all users.
live_notional_today_usd_gauge = Gauge(
    "tm_live_notional_today_usd",
    "Sum of live-mode notional placed across all users for the current UTC day",
)


def new_request_id() -> str:
    """Compact request ID (first 12 hex chars of a uuid4)."""
    return uuid.uuid4().hex[:12]


def metrics_payload() -> tuple[bytes, str]:
    """Return (body_bytes, content_type) for a /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
