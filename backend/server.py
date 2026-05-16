"""TRACKMIND backend — FastAPI + MongoDB.

Single-file API surface that orchestrates:
  - JWT auth (cookies + Bearer fallback)
  - Mock prediction-market data store + tick scheduler
  - 4 trading-intelligence engines (Alpha / Quant / Whale / Micro)
  - Master allocator with risk constraints
  - Paper broker with simulated fills, position lifecycle, settlement
  - Dashboard / signals / positions / analytics / controls endpoints
"""

from __future__ import annotations

from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import asyncio
import logging
import os
import random
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import bcrypt
import jwt
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bson import ObjectId
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, EmailStr, Field

from markets import market_store, quote_to_dict
from allocator import RiskConfig, allocate
from engines import alpha as alpha_engine, quant as quant_engine, whale as whale_engine, micro as micro_engine
from broker import (
    get_broker,
    resolve_broker_for_user,
    resolve_broker_for_user_async,
    is_live_configured,
    load_user_broker_creds,
    POLYMARKET_REQUIRED,
    KALSHI_REQUIRED,
)
from crypto import encrypt_payload, fingerprint_for
from push import push_to_user as _push_to_user_async, DEFAULT_NOTIFY_PREFS
from live_market import LIVE_INGEST_ENABLED, ingest_forever
from observability import (
    configure_json_logging,
    request_id_var,
    user_id_var,
    new_request_id,
    metrics_payload,
    http_requests_total,
    http_request_latency,
    llm_calls_total,
    llm_cache_hits_total,
    llm_failures_total,
    llm_latency,
    llm_quota_exceeded_total,
    open_positions_gauge,
    engine_signals_gauge,
)

logger = logging.getLogger("trackmind")
configure_json_logging(level=logging.INFO)

# --- Mongo ---
mongo_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = mongo_client[os.environ["DB_NAME"]]

# --- App ---
app = FastAPI(title="TRACKMIND API", version="1.0.0")
api = APIRouter(prefix="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # mobile uses Bearer header — wildcard origins OK
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    """Attach a request ID, log one structured access entry, and record Prometheus metrics."""
    # Respect upstream trace id when present (e.g., from a gateway), else synthesise one.
    rid = request.headers.get("x-request-id") or new_request_id()
    rid_token = request_id_var.set(rid)
    uid_token = user_id_var.set("-")
    started = time.time()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        elapsed = time.time() - started
        # Normalise path: collapse raw IDs to '{id}' in label to keep cardinality bounded.
        raw_path = request.url.path or "/"
        parts = []
        for seg in raw_path.split("/"):
            if len(seg) == 24 and all(c in "0123456789abcdef" for c in seg):
                parts.append("{id}")
            elif len(seg) == 32 and all(c in "0123456789abcdef" for c in seg):
                parts.append("{id}")
            else:
                parts.append(seg)
        label_path = "/".join(parts) or "/"
        try:
            http_requests_total.labels(request.method, label_path, str(status_code)).inc()
            http_request_latency.labels(request.method, label_path).observe(elapsed)
        except Exception:
            pass
        # One structured access line per request.
        logger.info(
            "http_access",
            extra={
                "method": request.method,
                "path": raw_path,
                "status": status_code,
                "latency_ms": round(elapsed * 1000, 2),
                "ip": (request.client.host if request.client else "-"),
                "ua": request.headers.get("user-agent", "-")[:128],
            },
        )
        request_id_var.reset(rid_token)
        user_id_var.reset(uid_token)

JWT_ALGORITHM = "HS256"
ACCESS_TTL_MIN = 60 * 24  # 24h on mobile (refresh available)
REFRESH_TTL_DAYS = 30
DEFAULT_BANKROLL = 10_000.0
INITIAL_BANKROLL = 10_000.0

# ── Risk limit defaults (overridable per-user via PATCH /api/me/risk-limits) ──
# Daily loss circuit breaker: kill trades once today's realized PnL drops below
# -(starting_bankroll * daily_loss_pct). 0.05 = 5%.
DEFAULT_DAILY_LOSS_PCT = 0.05
# Per-trade size cap: a single trade can be at most max_trade_pct * bankroll.
DEFAULT_MAX_TRADE_PCT = 0.02
# Hard floor on user customisation — even if user overrides, never allow > 25%
# daily loss or > 20% per-trade.  Mostly a UX safety rail.
MAX_DAILY_LOSS_PCT = 0.25
MAX_PER_TRADE_PCT = 0.20

# Fail fast if someone deploys with the insecure default.
_JWT_SECRET = os.environ.get("JWT_SECRET", "")
if not _JWT_SECRET or _JWT_SECRET in {"change_me_in_prod", "change_me", "dev", "secret"}:
    # Allow local dev auto-generation; log loudly.
    _JWT_SECRET = uuid.uuid4().hex + uuid.uuid4().hex
    logger.warning("JWT_SECRET missing/weak — generated ephemeral secret. Set JWT_SECRET in prod.")
os.environ["JWT_SECRET"] = _JWT_SECRET

# Rate limiter: lightweight sliding-window per (key, bucket). Dependency-injected.
class RateLimit:
    """Sliding-window rate limiter per-IP for a named bucket. Call :meth:`check` manually."""

    _windows: Dict[str, List[float]] = defaultdict(list)

    def __init__(self, bucket: str, max_calls: int, per_seconds: float):
        self.bucket = bucket
        self.max = max_calls
        self.window = per_seconds

    def check(self, request: Request) -> None:
        # Prefer the left-most entry in X-Forwarded-For (originating client) when behind a proxy
        # so the limiter windows correctly even across multiple ingress pods.
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

# Named limiters.
_rl_login = RateLimit("login", 60, 60)
_rl_register = RateLimit("register", 5, 3600)
_rl_refresh = RateLimit("refresh", 60, 60)
_rl_trade = RateLimit("trade", 60, 60)
_rl_auto_deploy = RateLimit("auto_deploy", 30, 60)

# Per-user async locks for settlement / balance mutation.
_user_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

# LLM narrative score cache: keyed by (canonical_id, bucket) with 3-min bucket.
_alpha_cache: Dict[str, Dict[str, Any]] = {}
_alpha_cache_lock = asyncio.Lock()
_llm_metrics = {"calls": 0, "cache_hits": 0, "failures": 0, "total_ms": 0.0}


# ---------- helpers ----------
def jwt_secret() -> str:
    return os.environ["JWT_SECRET"]


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), h.encode())
    except Exception:
        return False


def create_access(user_id: str, email: str, role: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "role": role,
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TTL_MIN),
        },
        jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )


def create_refresh(user_id: str, jti: Optional[str] = None) -> tuple[str, str]:
    """Returns (token, jti). jti is used for revocation tracking."""
    token_jti = jti or uuid.uuid4().hex
    token = jwt.encode(
        {
            "sub": user_id,
            "jti": token_jti,
            "type": "refresh",
            "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS),
        },
        jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )
    return token, token_jti


async def revoke_jti(jti: str, exp: datetime) -> None:
    await db.revoked_tokens.update_one(
        {"jti": jti},
        {"$set": {"jti": jti, "revoked_at": datetime.now(timezone.utc), "exp": exp}},
        upsert=True,
    )


async def is_jti_revoked(jti: str) -> bool:
    if not jti:
        return False
    return await db.revoked_tokens.find_one({"jti": jti}) is not None


def public_user(u: dict) -> dict:
    return {
        "id": str(u["_id"]),
        "email": u["email"],
        "name": u.get("name", ""),
        "role": u.get("role", "user"),
        "biometrics_enabled": u.get("biometrics_enabled", False),
        "live_mode": u.get("live_mode", False),
        "bankroll": round(u.get("bankroll", INITIAL_BANKROLL), 2),
        "starting_bankroll": round(u.get("starting_bankroll", INITIAL_BANKROLL), 2),
        "paused_engines": u.get("paused_engines", []),
        "kill_switch": u.get("kill_switch", False),
        "daily_loss_pct": float(u.get("daily_loss_pct", DEFAULT_DAILY_LOSS_PCT)),
        "max_trade_pct": float(u.get("max_trade_pct", DEFAULT_MAX_TRADE_PCT)),
        "notification_prefs": {**DEFAULT_NOTIFY_PREFS, **(u.get("notification_prefs") or {})},
        "created_at": u.get("created_at", datetime.now(timezone.utc)).isoformat(),
    }


def _risk_limits_for(user: dict) -> tuple[float, float]:
    """Return (daily_loss_pct, max_trade_pct) for this user, clamped to safe bounds."""
    dl = float(user.get("daily_loss_pct", DEFAULT_DAILY_LOSS_PCT) or DEFAULT_DAILY_LOSS_PCT)
    mt = float(user.get("max_trade_pct", DEFAULT_MAX_TRADE_PCT) or DEFAULT_MAX_TRADE_PCT)
    dl = max(0.005, min(MAX_DAILY_LOSS_PCT, dl))
    mt = max(0.001, min(MAX_PER_TRADE_PCT, mt))
    return dl, mt


def _check_daily_loss_circuit(user: dict) -> None:
    """Raise 400 if today's realized PnL has breached the daily loss circuit.

    Also fires a one-time-per-UTC-day push notification to the user when the
    circuit first trips.
    """
    dl_pct, _ = _risk_limits_for(user)
    starting = float(user.get("starting_bankroll", INITIAL_BANKROLL))
    realized_today = float(user.get("realized_pnl_today", 0.0))
    threshold = -starting * dl_pct
    if realized_today <= threshold:
        # Fire B3 push at most once per UTC day per user.
        today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if user.get("circuit_tripped_alert_date") != today_key:
            asyncio.create_task(_fire_circuit_tripped(user, today_key, realized_today, threshold))
        raise HTTPException(
            status_code=400,
            detail=(
                f"Daily loss circuit tripped (lost ${abs(realized_today):.2f} / "
                f"limit ${abs(threshold):.2f} = {dl_pct*100:.1f}% of starting bankroll). "
                f"Trading paused until UTC midnight."
            ),
        )


async def _fire_circuit_tripped(user: dict, today_key: str, realized: float, threshold: float) -> None:
    """B3: notify the user once-per-day that their daily loss circuit tripped."""
    try:
        # Stamp the user doc atomically so concurrent callers don't double-fire.
        res = await db.users.update_one(
            {"_id": user["_id"], "$or": [
                {"circuit_tripped_alert_date": {"$exists": False}},
                {"circuit_tripped_alert_date": {"$ne": today_key}},
            ]},
            {"$set": {"circuit_tripped_alert_date": today_key}},
        )
        if res.modified_count != 1:
            return  # someone else already fired today
        fresh = await db.users.find_one({"_id": user["_id"]})
        if not fresh:
            return
        await _push_to_user_async(
            db, fresh,
            category="daily_loss_tripped",
            title="🛑 Daily Loss Circuit Tripped",
            body=f"Lost ${abs(realized):.2f} today (limit ${abs(threshold):.2f}). Trading paused until UTC midnight.",
            data={"type": "daily_loss_tripped", "realized": realized, "threshold": threshold},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"push.daily_loss_tripped failed: {e}")


def _clamp_notional_to_cap(user: dict, notional: float) -> float:
    """Cap `notional` to user's max_trade_pct * bankroll."""
    _, mt_pct = _risk_limits_for(user)
    bankroll = float(user.get("bankroll", INITIAL_BANKROLL))
    cap = max(1.0, bankroll * mt_pct)
    return min(notional, cap)


async def get_current_user(request: Request) -> dict:
    token: Optional[str] = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        # jti check for access tokens (future-proof; access currently has no jti)
        if payload.get("jti") and await is_jti_revoked(payload["jti"]):
            raise HTTPException(status_code=401, detail="Token revoked")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        # Propagate user id into logging context for this request.
        user_id_var.set(str(user["_id"]))
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


async def audit(user_id: str, action: str, payload: dict | None = None) -> None:
    await db.audit_logs.insert_one({
        "user_id": str(user_id),
        "action": action,
        "payload": payload or {},
        "ts": datetime.now(timezone.utc),
    })


# ---------- schemas ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(default="", max_length=80)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    user: dict


class TradeApproveIn(BaseModel):
    signal_id: str
    notional: Optional[float] = None  # override sizing


class ControlsIn(BaseModel):
    paused_engines: Optional[List[str]] = None
    kill_switch: Optional[bool] = None
    live_mode: Optional[bool] = None
    biometrics_enabled: Optional[bool] = None


# ---------- engine cache ----------
_engine_cache: Dict[str, Any] = {
    "signals": [],         # list of all signals across engines
    "by_id": {},            # signal_id -> signal
    "by_engine": {"alpha": [], "quant": [], "whale": [], "micro": []},
    "updated_at": 0.0,
}
_engine_cache_lock = asyncio.Lock()


async def refresh_engines() -> None:
    """Run all engines, cache signals (with stable IDs)."""
    market_store.tick()

    # Attribute engine-refresh LLM usage to the primary admin (engines are a system
    # service). If the admin has exhausted quota, alpha falls back to cached scores.
    admin_user = await db.users.find_one({"role": "admin"})
    quota_ok = True
    if admin_user:
        # Best-effort *pre-consume* (up to 12 calls per refresh). If insufficient
        # credits, disable LLM for this tick and serve from cache / heuristic.
        quota_ok = await _check_and_consume_llm_quota(admin_user["_id"], amount=12)
        if not quota_ok:
            try:
                llm_quota_exceeded_total.labels(str(admin_user["_id"])).inc()
            except Exception:
                pass
            logger.info("llm_quota_exhausted_for_engine_refresh", extra={"admin_id": str(admin_user["_id"])})

    # Snapshot LLM metric deltas before run so we can publish precise counters.
    pre_calls = int(_llm_metrics.get("calls", 0))
    pre_hits = int(_llm_metrics.get("cache_hits", 0))
    pre_fails = int(_llm_metrics.get("failures", 0))
    pre_ms = float(_llm_metrics.get("total_ms", 0.0))

    try:
        alpha_signals = await alpha_engine.run_async(
            use_llm=bool(quota_ok), score_cache=_alpha_cache, metrics=_llm_metrics,
        )
    except Exception as e:
        logger.warning(f"alpha engine error: {e}")
        alpha_signals = []

    # Propagate to Prometheus.
    try:
        llm_calls_total.inc(int(_llm_metrics.get("calls", 0)) - pre_calls)
        llm_cache_hits_total.inc(int(_llm_metrics.get("cache_hits", 0)) - pre_hits)
        llm_failures_total.inc(int(_llm_metrics.get("failures", 0)) - pre_fails)
        delta_ms = float(_llm_metrics.get("total_ms", 0.0)) - pre_ms
        new_calls = int(_llm_metrics.get("calls", 0)) - pre_calls
        if new_calls > 0:
            # Average-over-calls histogram observation (one sample per call).
            avg_s = (delta_ms / new_calls) / 1000.0
            for _ in range(new_calls):
                llm_latency.observe(avg_s)
    except Exception:
        pass

    quant_signals = quant_engine.run()
    whale_signals = whale_engine.run()
    micro_signals = micro_engine.run()

    # After signals are computed below, attempt to fan out a master-signal alert.
    # This runs at the END of refresh_engines (deferred via asyncio.create_task) so
    # we don't block the engine refresh on push delivery.
    asyncio.create_task(_maybe_send_master_signal_alert())

    all_signals: List[dict] = []
    by_engine: Dict[str, List[dict]] = {"alpha": [], "quant": [], "whale": [], "micro": []}
    by_id: Dict[str, dict] = {}
    for grp in (alpha_signals, quant_signals, whale_signals, micro_signals):
        for s in grp:
            sid = f"sig_{s['engine']}_{s['canonical_id']}_{int(time.time() // 60)}"
            s["id"] = sid
            s["ts"] = time.time()
            all_signals.append(s)
            by_engine[s["engine"]].append(s)
            by_id[sid] = s

    async with _engine_cache_lock:
        _engine_cache["signals"] = all_signals
        _engine_cache["by_engine"] = by_engine
        _engine_cache["by_id"] = by_id
        _engine_cache["updated_at"] = time.time()
    logger.info(
        f"engines refreshed: alpha={len(alpha_signals)} quant={len(quant_signals)} "
        f"whale={len(whale_signals)} micro={len(micro_signals)}"
    )


async def settle_open_positions(user: dict) -> float:
    """Tick existing open positions: mark-to-market, randomly settle ~7% per tick.

    Returns realized PnL delta added to bankroll. Serialised per user to prevent
    double settlement across concurrent requests.
    """
    user_id = str(user["_id"])
    lock = _user_locks[user_id]
    settled_events: List[dict] = []
    async with lock:
        realized_delta = 0.0
        cursor = db.positions.find({"user_id": user_id, "status": "open"})
        async for pos in cursor:
            # Mark to market using current quote
            venue = pos.get("venue", "polymarket")
            if pos.get("engine") == "micro":
                current_value = 1.0  # arb pays 1.00 at settle modulo fees
            else:
                q = market_store.get(venue if venue != "cross" else "polymarket", pos["canonical_id"])
                if not q:
                    continue
                current_value = q.yes if pos["side"] == "YES" else q.no
            await db.positions.update_one(
                {"_id": pos["_id"]},
                {"$set": {"current_price": round(current_value, 4),
                          "unrealized_pnl": round((current_value - pos["entry_price"]) * pos["contracts"], 2),
                          "updated_at": datetime.now(timezone.utc)}},
            )

            # Randomly settle ~7% per tick (paper-mode quick cycles for engaging UX).
            # Use atomic conditional update so concurrent callers can't double-settle.
            if random.random() < 0.07:
                win_prob = 0.5 + min(0.25, pos.get("expected_edge", 0.0) * 4)
                won = random.random() < win_prob
                payout = (1.0 if won else 0.0) if pos["engine"] != "micro" else (1.0 - pos.get("fees_pct", 0.01))
                realized = round((payout - pos["entry_price"]) * pos["contracts"] - pos.get("fees", 0), 2)
                settle_res = await db.positions.update_one(
                    {"_id": pos["_id"], "status": "open"},
                    {"$set": {
                        "status": "closed",
                        "exit_price": round(payout, 4),
                        "realized_pnl": realized,
                        "closed_at": datetime.now(timezone.utc),
                    }},
                )
                if settle_res.modified_count == 1:
                    realized_delta += realized
                    await audit(user_id, "position.settled", {"position_id": str(pos["_id"]), "won": won, "pnl": realized})
                    settled_events.append({
                        "won": won,
                        "pnl": realized,
                        "market": pos.get("market", "—"),
                        "engine": pos.get("engine", "—"),
                        "position_id": str(pos["_id"]),
                    })

        if realized_delta != 0:
            await db.users.update_one(
                {"_id": user["_id"]},
                {"$inc": {"bankroll": realized_delta, "realized_pnl_today": realized_delta}},
            )

    # Fire B1 push notifications AFTER releasing the per-user lock to avoid extending
    # the critical section by an outbound HTTP call. Best-effort: never raises.
    if settled_events:
        fresh = await db.users.find_one({"_id": user["_id"]})
        if fresh:
            asyncio.create_task(_fire_settled_pushes(fresh, settled_events))
    return realized_delta


async def _fire_settled_pushes(user: dict, events: List[dict]) -> None:
    """Send up to 5 position-settled pushes per tick (avoid notification fatigue)."""
    try:
        for ev in events[:5]:
            won = ev["won"]
            sign = "+" if ev["pnl"] >= 0 else ""
            title = f"{'✓ WON' if won else '✗ LOST'} · {ev['engine'].upper()}"
            body = f"{ev['market'][:60]} · {sign}${ev['pnl']:.2f}"
            await _push_to_user_async(
                db, user,
                category="position_settled",
                title=title, body=body,
                data={"type": "position_settled", "position_id": ev["position_id"], "pnl": ev["pnl"]},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"push.position_settled failed: {e}")


# ---------- auth routes ----------
@api.post("/auth/register")
async def register(payload: RegisterIn, request: Request, response: Response):
    _rl_register.check(request)
    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    now = datetime.now(timezone.utc)
    doc = {
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": payload.name or email.split("@")[0],
        "role": "user",
        "bankroll": INITIAL_BANKROLL,
        "starting_bankroll": INITIAL_BANKROLL,
        "realized_pnl_today": 0.0,
        "biometrics_enabled": False,
        "live_mode": False,
        "paused_engines": [],
        "kill_switch": False,
        "created_at": now,
    }
    res = await db.users.insert_one(doc)
    user = await db.users.find_one({"_id": res.inserted_id})
    access = create_access(str(user["_id"]), user["email"], user["role"])
    refresh, _ = create_refresh(str(user["_id"]))
    response.set_cookie("access_token", access, httponly=True, samesite="lax", max_age=ACCESS_TTL_MIN * 60, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, samesite="lax", max_age=REFRESH_TTL_DAYS * 86400, path="/")
    await audit(str(user["_id"]), "auth.register")
    return {"access_token": access, "refresh_token": refresh, "user": public_user(user)}


@api.post("/auth/login")
async def login(payload: LoginIn, request: Request, response: Response):
    _rl_login.check(request)
    email = payload.email.lower().strip()
    ip = request.client.host if request.client else "unknown"
    identifier = f"{ip}:{email}"

    # Brute force lockout — coerce stored timestamp to tz-aware before subtraction.
    attempt = await db.login_attempts.find_one({"identifier": identifier})
    if attempt and attempt.get("count", 0) >= 5:
        last_at = attempt["last_at"]
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_at).total_seconds() < 900:
            raise HTTPException(status_code=429, detail="Too many failed attempts, try again later")
        await db.login_attempts.delete_one({"identifier": identifier})

    user = await db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        await db.login_attempts.update_one(
            {"identifier": identifier},
            {"$inc": {"count": 1}, "$set": {"last_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    await db.login_attempts.delete_one({"identifier": identifier})
    access = create_access(str(user["_id"]), user["email"], user["role"])
    refresh, _ = create_refresh(str(user["_id"]))
    response.set_cookie("access_token", access, httponly=True, samesite="lax", max_age=ACCESS_TTL_MIN * 60, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, samesite="lax", max_age=REFRESH_TTL_DAYS * 86400, path="/")
    await audit(str(user["_id"]), "auth.login")
    return {"access_token": access, "refresh_token": refresh, "user": public_user(user)}


@api.post("/auth/refresh")
async def refresh_token(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token")
        old_jti = payload.get("jti")
        if old_jti and await is_jti_revoked(old_jti):
            # Token replay — revoke all sessions for this user (compromise response).
            await db.users.update_one(
                {"_id": ObjectId(payload["sub"])},
                {"$set": {"tokens_revoked_at": datetime.now(timezone.utc)}},
            )
            raise HTTPException(status_code=401, detail="Token reuse detected; please sign in again")
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Rotate: revoke old, issue new.
        if old_jti:
            exp_dt = datetime.fromtimestamp(payload["exp"], tz=timezone.utc) if payload.get("exp") else datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS)
            await revoke_jti(old_jti, exp_dt)

        access = create_access(str(user["_id"]), user["email"], user["role"])
        new_refresh, _ = create_refresh(str(user["_id"]))
        response.set_cookie("access_token", access, httponly=True, samesite="lax", max_age=ACCESS_TTL_MIN * 60, path="/")
        response.set_cookie("refresh_token", new_refresh, httponly=True, samesite="lax", max_age=REFRESH_TTL_DAYS * 86400, path="/")
        return {"access_token": access, "refresh_token": new_refresh, "user": public_user(user)}
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


@api.post("/auth/logout")
async def logout(request: Request, response: Response, user: dict = Depends(get_current_user)):
    # Revoke refresh token if present.
    rt = request.cookies.get("refresh_token") or ""
    if not rt:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            rt = auth[7:]
    if rt:
        try:
            p = jwt.decode(rt, jwt_secret(), algorithms=[JWT_ALGORITHM], options={"verify_exp": False})
            if p.get("type") == "refresh" and p.get("jti"):
                exp_dt = datetime.fromtimestamp(p["exp"], tz=timezone.utc) if p.get("exp") else datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS)
                await revoke_jti(p["jti"], exp_dt)
        except Exception:
            pass
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")
    await audit(str(user["_id"]), "auth.logout")
    return {"ok": True}


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return public_user(user)


# ---------- dashboard ----------
@api.get("/dashboard")
async def dashboard(user: dict = Depends(get_current_user)):
    await settle_open_positions(user)
    user = await db.users.find_one({"_id": user["_id"]})  # refresh

    # Compute unrealized PnL
    unrealized = 0.0
    open_count = 0
    async for pos in db.positions.find({"user_id": str(user["_id"]), "status": "open"}):
        unrealized += pos.get("unrealized_pnl", 0.0)
        open_count += 1
    realized_today = float(user.get("realized_pnl_today", 0.0))
    today_pnl = round(realized_today + unrealized, 2)
    starting = float(user.get("starting_bankroll", INITIAL_BANKROLL))
    today_pnl_pct = round(today_pnl / max(starting, 1.0), 4)

    # Allocator preview
    async with _engine_cache_lock:
        signals = list(_engine_cache["signals"])
        engines_updated = _engine_cache["updated_at"]
    paused = set(user.get("paused_engines", []))
    if user.get("kill_switch"):
        paused = {"alpha", "quant", "whale", "micro"}
    plan = allocate(signals, float(user["bankroll"]), today_pnl_pct, paused)

    top_signal = plan["orders"][0] if plan["orders"] else (signals[0] if signals else None)

    # Engine health
    engine_status = []
    for name in ("alpha", "quant", "whale", "micro"):
        active = name not in paused
        count = sum(1 for s in signals if s["engine"] == name)
        avg_conf = round(
            sum(s["confidence"] for s in signals if s["engine"] == name) / max(1, count), 3
        )
        engine_status.append({
            "engine": name,
            "active": active,
            "signals": count,
            "avg_confidence": avg_conf,
            "allocation_pct": plan["engine_allocation"].get(name, 0.0),
            "notional": plan["engine_notional"].get(name, 0.0),
        })

    return {
        "bankroll": round(float(user["bankroll"]), 2),
        "starting_bankroll": round(starting, 2),
        "today_pnl": today_pnl,
        "today_pnl_pct": today_pnl_pct,
        "realized_pnl_today": round(realized_today, 2),
        "unrealized_pnl": round(unrealized, 2),
        "open_positions": open_count,
        "mode": "live" if user.get("live_mode") else "paper",
        "kill_switch": bool(user.get("kill_switch", False)),
        "engines_updated_at": engines_updated,
        "engine_status": engine_status,
        "plan": {
            "deployed": plan["deployed"],
            "deployable_cash": plan["deployable_cash"],
            "cash_buffer": plan["cash_buffer"],
            "defensive_mode": plan["defensive_mode"],
            "drawdown_ratio": plan["drawdown_ratio"],
            "orders_count": len(plan["orders"]),
        },
        "top_signal": top_signal,
    }


# ---------- signals ----------
@api.get("/signals")
async def list_signals(engine: Optional[str] = None, user: dict = Depends(get_current_user)):
    async with _engine_cache_lock:
        signals = list(_engine_cache["signals"])
        updated_at = _engine_cache["updated_at"]
    if engine:
        signals = [s for s in signals if s["engine"] == engine]
    signals.sort(key=lambda s: s["confidence"] * s["expected_edge"], reverse=True)
    return {"signals": signals, "updated_at": updated_at}


@api.get("/signals/micro")
async def micro_signals(user: dict = Depends(get_current_user)):
    async with _engine_cache_lock:
        signals = [s for s in _engine_cache["signals"] if s["engine"] == "micro"]
    return {"signals": signals}


# ---------- positions ----------
def _serialize_position(p: dict) -> dict:
    return {
        "id": str(p["_id"]),
        "engine": p["engine"],
        "canonical_id": p["canonical_id"],
        "market": p["market"],
        "venue": p.get("venue", "paper"),
        "side": p["side"],
        "entry_price": p["entry_price"],
        "current_price": p.get("current_price", p["entry_price"]),
        "exit_price": p.get("exit_price"),
        "contracts": p["contracts"],
        "notional": p["notional"],
        "fees": p.get("fees", 0),
        "status": p["status"],
        "unrealized_pnl": p.get("unrealized_pnl", 0.0),
        "realized_pnl": p.get("realized_pnl"),
        "expected_edge": p.get("expected_edge", 0.0),
        "confidence": p.get("confidence", 0.0),
        "fill_id": p.get("fill_id"),
        "signal_id": p.get("signal_id"),
        "broker": p.get("broker"),
        "opened_at": p.get("opened_at", datetime.now(timezone.utc)).isoformat(),
        "closed_at": p["closed_at"].isoformat() if p.get("closed_at") else None,
    }


@api.get("/positions")
async def list_positions(user: dict = Depends(get_current_user)):
    # Read-only: settlement only fires on /dashboard or the scheduler so GET /positions
    # is idempotent and cannot mutate PnL on concurrent polling.
    user_id = str(user["_id"])
    open_pos = [p async for p in db.positions.find({"user_id": user_id, "status": "open"}).sort("opened_at", -1)]
    closed_pos = [p async for p in db.positions.find({"user_id": user_id, "status": "closed"}).sort("closed_at", -1).limit(50)]
    return {
        "open": [_serialize_position(p) for p in open_pos],
        "closed": [_serialize_position(p) for p in closed_pos],
    }


async def _acquire_idempotency(key: str, user_id: str) -> tuple[bool, Optional[dict]]:
    """Atomically claim an idempotency key using a single findOneAndUpdate+upsert.

    Returns:
        (True,  None)       — caller OWNS the key; proceed with the work, then call
                              _finalize_idempotency to record the response.
        (False, response)   — another caller already finished; return cached response.
        (False, None)       — another caller is IN FLIGHT; caller should wait then retry.
    """
    if not key:
        return True, None
    from pymongo import ReturnDocument  # local import keeps top-of-file clean
    now = datetime.now(timezone.utc)
    # Upsert. Prior doc (if any) is returned via return_document=BEFORE.
    prior = await db.idempotency_keys.find_one_and_update(
        {"key": key, "user_id": user_id},
        {
            "$setOnInsert": {
                "key": key,
                "user_id": user_id,
                "created_at": now,
                "status": "in_progress",
            }
        },
        upsert=True,
        return_document=ReturnDocument.BEFORE,
    )
    if prior is None:
        # We just inserted the doc — we OWN this idempotency slot.
        return True, None
    if prior.get("status") == "done":
        return False, prior.get("response")
    # Another request is still in flight for this key.
    return False, None


async def _finalize_idempotency(key: str, user_id: str, response: dict) -> None:
    """Mark an owned idempotency slot as done and stash the response."""
    if not key:
        return
    await db.idempotency_keys.update_one(
        {"key": key, "user_id": user_id},
        {"$set": {"status": "done", "response": response}},
    )


async def _abort_idempotency(key: str, user_id: str) -> None:
    """Release a claimed slot on failure so retries aren't blocked forever."""
    if not key:
        return
    await db.idempotency_keys.delete_one({"key": key, "user_id": user_id, "status": "in_progress"})


async def _wait_for_idempotency_result(key: str, user_id: str, timeout_s: float = 4.0) -> Optional[dict]:
    """Poll the idempotency doc until it transitions to done, or timeout."""
    if not key:
        return None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        doc = await db.idempotency_keys.find_one({"key": key, "user_id": user_id})
        if doc and doc.get("status") == "done":
            return doc.get("response")
        await asyncio.sleep(0.1)
    return None


async def _idempotency_seen(key: str, user_id: str, payload: dict) -> Optional[dict]:
    """Backwards-compat shim — never call directly in new code; use _acquire_idempotency."""
    if not key:
        return None
    row = await db.idempotency_keys.find_one({"key": key, "user_id": user_id, "status": "done"})
    return row["response"] if row else None


async def _idempotency_store(key: str, user_id: str, response: dict) -> None:
    """Backwards-compat shim — retained so any old callsite still works."""
    await _finalize_idempotency(key, user_id, response)


async def _atomic_bankroll_debit(user_id_obj: ObjectId, amount: float) -> bool:
    """Atomically deduct `amount` from bankroll iff sufficient. Returns True on success."""
    res = await db.users.update_one(
        {"_id": user_id_obj, "bankroll": {"$gte": amount}},
        {"$inc": {"bankroll": -amount}},
    )
    return res.modified_count == 1


# ---------- trade ----------
@api.post("/trade/approve")
async def approve_trade(
    payload: TradeApproveIn,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: dict = Depends(get_current_user),
):
    _rl_trade.check(request)
    user = await db.users.find_one({"_id": user["_id"]})
    user_id = str(user["_id"])

    # ---- Idempotency: atomically CLAIM the key before doing any work. ----
    ikey = idempotency_key or ""
    if ikey:
        is_owner, prior = await _acquire_idempotency(ikey, user_id)
        if not is_owner:
            if prior is not None:
                return prior
            waited = await _wait_for_idempotency_result(ikey, user_id)
            if waited is not None:
                return waited
            raise HTTPException(status_code=409, detail="Idempotent request in flight, try again")

    try:
        if user.get("kill_switch"):
            raise HTTPException(status_code=400, detail="Kill switch is active")
        # Daily loss circuit breaker — hard kill all trading once breached.
        _check_daily_loss_circuit(user)

        async with _engine_cache_lock:
            sig = _engine_cache["by_id"].get(payload.signal_id)
        if not sig:
            raise HTTPException(status_code=404, detail="Signal not found or expired")
        if sig["engine"] in user.get("paused_engines", []):
            raise HTTPException(status_code=400, detail="Engine is paused")

        bankroll = float(user["bankroll"])
        # Caller-supplied notional must fit within bankroll — enforce strict guard BEFORE clamping
        # so the user gets a real "insufficient bankroll" error instead of a silent 95% fill.
        if payload.notional is not None and float(payload.notional) > bankroll:
            raise HTTPException(status_code=400, detail="Insufficient bankroll")
        notional = payload.notional or round(min(bankroll * sig["size_hint"], bankroll * 0.15), 2)
        notional = max(1.0, min(notional, bankroll * 0.95))
        # Per-trade size cap (user-configurable risk limit).
        capped = _clamp_notional_to_cap(user, notional)
        if payload.notional is not None and float(payload.notional) > capped + 0.005:
            _, mt_pct = _risk_limits_for(user)
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Trade exceeds per-trade cap (${float(payload.notional):.2f} > "
                    f"${capped:.2f} = {mt_pct*100:.2f}% of bankroll)."
                ),
            )
        notional = capped

        # Atomically debit bankroll BEFORE placing order; if debit fails, refuse.
        ok = await _atomic_bankroll_debit(user["_id"], notional)
        if not ok:
            raise HTTPException(status_code=400, detail="Insufficient bankroll")

        try:
            broker = await resolve_broker_for_user_async(db, user)
            fill = await broker.place_order(user_id=user_id, signal=sig, notional=notional)
        except Exception as e:
            # Refund on broker failure.
            await db.users.update_one({"_id": user["_id"]}, {"$inc": {"bankroll": notional}})
            raise HTTPException(status_code=502, detail=f"Broker error: {e}")

        pos_doc = {
            "user_id": user_id,
            "engine": sig["engine"],
            "canonical_id": sig["canonical_id"],
            "market": sig["market"],
            "venue": sig.get("venue", "paper"),
            "side": sig["side"],
            "entry_price": fill["price"],
            "current_price": fill["price"],
            "contracts": fill["contracts"],
            "notional": fill["notional"],
            "fees": fill["fees"],
            "fees_pct": fill["fees"] / max(1.0, fill["notional"]),
            "expected_edge": sig["expected_edge"],
            "confidence": sig["confidence"],
            "status": "open",
            "unrealized_pnl": 0.0,
            "opened_at": datetime.now(timezone.utc),
            "broker": fill["broker"],
            "fill_id": fill["id"],
            "signal_id": payload.signal_id,
        }
        res = await db.positions.insert_one(pos_doc)
        # Apply difference if broker sized slightly differently than the debited amount.
        if abs(fill["notional"] - notional) > 0.005:
            diff = notional - fill["notional"]  # positive = refund
            if diff != 0:
                await db.users.update_one({"_id": user["_id"]}, {"$inc": {"bankroll": diff}})
        await audit(user_id, "trade.approve", {"signal_id": payload.signal_id, "notional": fill["notional"]})
        pos_doc["_id"] = res.inserted_id
        response = {"position": _serialize_position(pos_doc), "fill": fill}
        if ikey:
            await _finalize_idempotency(ikey, user_id, response)
        return response
    except HTTPException:
        if ikey:
            await _abort_idempotency(ikey, user_id)
        raise
    except Exception:
        if ikey:
            await _abort_idempotency(ikey, user_id)
        raise


@api.post("/trade/auto-deploy")
async def auto_deploy(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    user: dict = Depends(get_current_user),
):
    """One-tap: take the top N orders from the allocator and execute them."""
    _rl_auto_deploy.check(request)
    user = await db.users.find_one({"_id": user["_id"]})
    user_id = str(user["_id"])

    # ---- Idempotency: claim the key before doing any work. ----
    ikey = idempotency_key or ""
    if ikey:
        is_owner, prior = await _acquire_idempotency(ikey, user_id)
        if not is_owner:
            if prior is not None:
                return prior
            waited = await _wait_for_idempotency_result(ikey, user_id)
            if waited is not None:
                return waited
            raise HTTPException(status_code=409, detail="Idempotent request in flight, try again")

    try:
        if user.get("kill_switch"):
            raise HTTPException(status_code=400, detail="Kill switch is active")
        _check_daily_loss_circuit(user)
        async with _engine_cache_lock:
            signals = list(_engine_cache["signals"])

        realized_today = float(user.get("realized_pnl_today", 0.0))
        starting = float(user.get("starting_bankroll", INITIAL_BANKROLL))
        pnl_pct = round(realized_today / max(starting, 1.0), 4)

        paused = set(user.get("paused_engines", []))
        plan = allocate(signals, float(user["bankroll"]), pnl_pct, paused)

        deployed = []
        for order in plan["orders"][:3]:
            # Re-clamp to per-trade cap (the allocator doesn't know user prefs).
            order["notional"] = _clamp_notional_to_cap(user, float(order["notional"]))
            ok = await _atomic_bankroll_debit(user["_id"], order["notional"])
            if not ok:
                break  # out of bankroll; stop deploying more
            try:
                broker = await resolve_broker_for_user_async(db, user)
                fill = await broker.place_order(user_id=user_id, signal=order, notional=order["notional"])
            except Exception as e:
                await db.users.update_one({"_id": user["_id"]}, {"$inc": {"bankroll": order["notional"]}})
                logger.warning(f"auto_deploy broker error: {e}")
                continue

            pos_doc = {
                "user_id": user_id,
                "engine": order["engine"],
                "canonical_id": order["canonical_id"],
                "market": order["market"],
                "venue": order.get("venue", "paper"),
                "side": order["side"],
                "entry_price": fill["price"],
                "current_price": fill["price"],
                "contracts": fill["contracts"],
                "notional": fill["notional"],
                "fees": fill["fees"],
                "fees_pct": fill["fees"] / max(1.0, fill["notional"]),
                "expected_edge": order["expected_edge"],
                "confidence": order["confidence"],
                "status": "open",
                "unrealized_pnl": 0.0,
                "opened_at": datetime.now(timezone.utc),
                "broker": fill["broker"],
                "fill_id": fill["id"],
                "signal_id": order["id"],
            }
            res = await db.positions.insert_one(pos_doc)
            pos_doc["_id"] = res.inserted_id
            deployed.append(_serialize_position(pos_doc))
        await audit(user_id, "trade.auto_deploy", {"count": len(deployed)})
        response = {"deployed": deployed, "plan": {"orders_count": len(plan["orders"])}}
        # B2 push: notify user when auto-deploy actually placed orders.
        if deployed:
            asyncio.create_task(_fire_auto_deploy_push(user, deployed))
        if ikey:
            await _finalize_idempotency(ikey, user_id, response)
        return response
    except HTTPException:
        if ikey:
            await _abort_idempotency(ikey, user_id)
        raise
    except Exception:
        if ikey:
            await _abort_idempotency(ikey, user_id)
        raise


# ---------- analytics ----------
@api.get("/analytics")
async def analytics(user: dict = Depends(get_current_user)):
    user_id = str(user["_id"])
    closed = [p async for p in db.positions.find({"user_id": user_id, "status": "closed"}).sort("closed_at", 1)]
    wins = [p for p in closed if (p.get("realized_pnl") or 0) > 0]
    losses = [p for p in closed if (p.get("realized_pnl") or 0) <= 0]
    win_rate = len(wins) / len(closed) if closed else 0.0
    total_pnl = sum((p.get("realized_pnl") or 0.0) for p in closed)
    avg_win = sum((p.get("realized_pnl") or 0.0) for p in wins) / len(wins) if wins else 0.0
    avg_loss = sum((p.get("realized_pnl") or 0.0) for p in losses) / len(losses) if losses else 0.0

    # Equity curve
    starting = float(user.get("starting_bankroll", INITIAL_BANKROLL))
    equity = [starting]
    for p in closed:
        equity.append(round(equity[-1] + (p.get("realized_pnl") or 0.0), 2))

    # Drawdown
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak else 0
        max_dd = max(max_dd, dd)

    # By engine
    by_engine = {"alpha": {"trades": 0, "pnl": 0.0, "wins": 0},
                 "quant": {"trades": 0, "pnl": 0.0, "wins": 0},
                 "whale": {"trades": 0, "pnl": 0.0, "wins": 0},
                 "micro": {"trades": 0, "pnl": 0.0, "wins": 0}}
    for p in closed:
        e = p["engine"]
        if e in by_engine:
            by_engine[e]["trades"] += 1
            by_engine[e]["pnl"] += p.get("realized_pnl") or 0.0
            if (p.get("realized_pnl") or 0) > 0:
                by_engine[e]["wins"] += 1
    for e in by_engine:
        by_engine[e]["pnl"] = round(by_engine[e]["pnl"], 2)
        by_engine[e]["win_rate"] = round(by_engine[e]["wins"] / by_engine[e]["trades"], 3) if by_engine[e]["trades"] else 0.0

    # Sharpe-like (toy): mean / stdev of trade pnl
    pnls = [(p.get("realized_pnl") or 0.0) for p in closed]
    if len(pnls) > 2:
        mean = sum(pnls) / len(pnls)
        var = sum((x - mean) ** 2 for x in pnls) / len(pnls)
        std = var ** 0.5
        sharpe = round(mean / std, 3) if std > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "trades_total": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 3),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_drawdown": round(max_dd, 4),
        "sharpe": sharpe,
        "equity_curve": equity[-100:],  # cap
        "by_engine": by_engine,
    }


# ---------- controls ----------
@api.post("/controls/update")
async def update_controls(payload: ControlsIn, user: dict = Depends(get_current_user)):
    update: Dict[str, Any] = {}
    if payload.paused_engines is not None:
        valid = {"alpha", "quant", "whale", "micro"}
        update["paused_engines"] = [e for e in payload.paused_engines if e in valid]
    kill_switch_changed = False
    new_kill_value = None
    if payload.kill_switch is not None:
        new_kill_value = bool(payload.kill_switch)
        if new_kill_value != bool(user.get("kill_switch", False)):
            kill_switch_changed = True
        update["kill_switch"] = new_kill_value
    if payload.live_mode is not None:
        update["live_mode"] = bool(payload.live_mode)
    if payload.biometrics_enabled is not None:
        update["biometrics_enabled"] = bool(payload.biometrics_enabled)
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")
    await db.users.update_one({"_id": user["_id"]}, {"$set": update})
    await audit(str(user["_id"]), "controls.update", update)
    user = await db.users.find_one({"_id": user["_id"]})
    # B4 push: kill-switch transition confirmation.
    if kill_switch_changed:
        asyncio.create_task(_fire_kill_switch_push(user, new_kill_value))
    return public_user(user)


async def _fire_auto_deploy_push(user: dict, deployed: List[dict]) -> None:
    """B2: notify user when one-tap auto-deploy fires."""
    try:
        # Refresh user (latest bankroll snapshot for nicer body).
        fresh = await db.users.find_one({"_id": user["_id"]})
        if not fresh:
            return
        n = len(deployed)
        total = sum(float(d.get("notional", 0.0)) for d in deployed)
        engines = ",".join(sorted({d.get("engine", "") for d in deployed if d.get("engine")}))
        await _push_to_user_async(
            db, fresh,
            category="auto_deploy",
            title=f"⚡ AUTO-DEPLOY · {n} trade{'s' if n != 1 else ''}",
            body=f"${total:.2f} routed across {engines or '—'}.",
            data={"type": "auto_deploy", "count": n, "total_notional": round(total, 2)},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"push.auto_deploy failed: {e}")


async def _fire_kill_switch_push(user: dict, enabled: bool) -> None:
    """B4: notify user that they toggled the global kill switch."""
    try:
        await _push_to_user_async(
            db, user,
            category="kill_switch",
            title="🧨 KILL SWITCH " + ("ENABLED" if enabled else "DISABLED"),
            body=(
                "All engines halted. Open positions kept; new trades blocked."
                if enabled
                else "Trading resumed. Engines are live again."
            ),
            data={"type": "kill_switch", "enabled": enabled},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"push.kill_switch failed: {e}")


# ---------- notification preferences ----------
class NotifyPrefsIn(BaseModel):
    master_signal: Optional[bool] = None
    position_settled: Optional[bool] = None
    auto_deploy: Optional[bool] = None
    daily_loss_tripped: Optional[bool] = None
    kill_switch: Optional[bool] = None


@api.get("/me/notification-prefs")
async def get_notification_prefs(user: dict = Depends(get_current_user)):
    stored = user.get("notification_prefs") or {}
    return {"prefs": {**DEFAULT_NOTIFY_PREFS, **stored}, "defaults": DEFAULT_NOTIFY_PREFS}


@api.patch("/me/notification-prefs")
async def update_notification_prefs(
    payload: NotifyPrefsIn,
    user: dict = Depends(get_current_user),
):
    update_fields: Dict[str, Any] = {}
    for key in DEFAULT_NOTIFY_PREFS.keys():
        val = getattr(payload, key, None)
        if val is not None:
            update_fields[f"notification_prefs.{key}"] = bool(val)
    if not update_fields:
        raise HTTPException(status_code=400, detail="Nothing to update")
    await db.users.update_one({"_id": user["_id"]}, {"$set": update_fields})
    await audit(str(user["_id"]), "notify_prefs.update", update_fields)
    user = await db.users.find_one({"_id": user["_id"]})
    return await get_notification_prefs(user)


@api.post("/controls/pause")
async def pause_engine(engine: str, user: dict = Depends(get_current_user)):
    if engine not in {"alpha", "quant", "whale", "micro"}:
        raise HTTPException(status_code=400, detail="Unknown engine")
    paused = set(user.get("paused_engines", []))
    paused.add(engine)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"paused_engines": list(paused)}})
    return {"paused_engines": list(paused)}


@api.post("/controls/resume")
async def resume_engine(engine: str, user: dict = Depends(get_current_user)):
    if engine not in {"alpha", "quant", "whale", "micro"}:
        raise HTTPException(status_code=400, detail="Unknown engine")
    paused = set(user.get("paused_engines", []))
    paused.discard(engine)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"paused_engines": list(paused)}})
    return {"paused_engines": list(paused)}


@api.post("/controls/reset")
async def reset_account(user: dict = Depends(get_current_user)):
    """Wipe positions + reset bankroll (paper account utility)."""
    user_id = str(user["_id"])
    await db.positions.delete_many({"user_id": user_id})
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "bankroll": INITIAL_BANKROLL,
            "starting_bankroll": INITIAL_BANKROLL,
            "realized_pnl_today": 0.0,
            "kill_switch": False,
            "paused_engines": [],
        }},
    )
    await audit(user_id, "controls.reset")
    user = await db.users.find_one({"_id": user["_id"]})
    return public_user(user)


# ---------- markets debug ----------
@api.get("/markets")
async def markets(user: dict = Depends(get_current_user)):
    return {"quotes": [quote_to_dict(q) for q in market_store.all()]}


@api.get("/health")
async def health():
    return {"ok": True, "service": "trackmind", "ts": time.time()}


# ---------- admin observability ----------
@api.get("/admin/metrics")
async def metrics(user: dict = Depends(require_admin)):
    """Return lightweight ops metrics (LLM usage, cache hit rate, engine counts)."""
    async with _engine_cache_lock:
        total_signals = len(_engine_cache["signals"])
        updated_at = _engine_cache["updated_at"]
        by_engine_count = {k: len(v) for k, v in _engine_cache["by_engine"].items()}
    total_calls = _llm_metrics.get("calls", 0)
    cache_hits = _llm_metrics.get("cache_hits", 0)
    failures = _llm_metrics.get("failures", 0)
    hit_rate = (cache_hits / max(1, cache_hits + total_calls)) if (cache_hits + total_calls) else 0.0
    avg_ms = (_llm_metrics.get("total_ms", 0.0) / max(1, total_calls)) if total_calls else 0.0
    users_total = await db.users.count_documents({})
    positions_open = await db.positions.count_documents({"status": "open"})
    positions_total = await db.positions.count_documents({})
    revoked_tokens = await db.revoked_tokens.count_documents({})
    return {
        "llm": {
            "calls": total_calls,
            "cache_hits": cache_hits,
            "failures": failures,
            "hit_rate": round(hit_rate, 3),
            "avg_latency_ms": round(avg_ms, 1),
        },
        "engines": {
            "total_signals": total_signals,
            "by_engine": by_engine_count,
            "last_refresh_ts": updated_at,
        },
        "db": {
            "users": users_total,
            "positions_open": positions_open,
            "positions_total": positions_total,
            "revoked_tokens": revoked_tokens,
        },
        "ts": time.time(),
    }


@api.get("/audit")
async def user_audit(limit: int = 50, user: dict = Depends(get_current_user)):
    """Return the caller's own audit trail (last N events)."""
    limit = max(1, min(200, limit))
    rows = [
        {
            "action": r.get("action"),
            "payload": r.get("payload", {}),
            "ts": r["ts"].isoformat() if r.get("ts") else None,
        }
        async for r in db.audit_logs.find({"user_id": str(user["_id"])}).sort("ts", -1).limit(limit)
    ]
    return {"events": rows}


ADMIN_LLM_DAILY_QUOTA = 5000
USER_LLM_DAILY_QUOTA = 500


def _today_utc_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _resolve_user_quota(user: dict) -> int:
    """Resolve a user's LLM daily quota: user-override > role default."""
    override = user.get("llm_daily_quota")
    if isinstance(override, int) and override > 0:
        return override
    return ADMIN_LLM_DAILY_QUOTA if user.get("role") == "admin" else USER_LLM_DAILY_QUOTA


async def _check_and_consume_llm_quota(user_id_obj: ObjectId, amount: int = 1) -> bool:
    """Atomically consume ``amount`` LLM credits from today's bucket. Returns True if consumed.

    Bucket rolls over at midnight UTC (date key change).  We store ``llm_daily_usage`` as
    {"date": "YYYY-MM-DD", "count": int} so there's no cron-dependent reset.
    """
    today = _today_utc_key()
    # If a stale day row exists, reset it.
    await db.users.update_one(
        {"_id": user_id_obj, "llm_daily_usage.date": {"$ne": today}},
        {"$set": {"llm_daily_usage": {"date": today, "count": 0}}},
    )
    user = await db.users.find_one({"_id": user_id_obj}, {"role": 1, "llm_daily_quota": 1, "llm_daily_usage": 1})
    if not user:
        return False
    quota = await _resolve_user_quota(user)
    used = int((user.get("llm_daily_usage") or {}).get("count", 0))
    if used + amount > quota:
        return False
    await db.users.update_one(
        {"_id": user_id_obj, "llm_daily_usage.date": today},
        {"$inc": {"llm_daily_usage.count": amount}},
    )
    return True


async def _current_llm_quota_snapshot(user: dict) -> dict:
    today = _today_utc_key()
    bucket = user.get("llm_daily_usage") or {}
    if bucket.get("date") != today:
        used = 0
    else:
        used = int(bucket.get("count", 0))
    quota = await _resolve_user_quota(user)
    return {
        "date_utc": today,
        "used": used,
        "quota": quota,
        "remaining": max(0, quota - used),
        "rolls_over_at": (datetime.combine(
            (datetime.now(timezone.utc) + timedelta(days=1)).date(), datetime.min.time(), tzinfo=timezone.utc)
        ).isoformat(),
    }


@api.get("/me/quota")
async def me_quota(user: dict = Depends(get_current_user)):
    """Return the caller's LLM quota + today's usage."""
    return await _current_llm_quota_snapshot(user)


@api.get("/brokers/status")
async def brokers_status(user: dict = Depends(get_current_user)):
    """Report which live brokers are usable for THIS user.

    A venue is "configured" if EITHER the operator's env vars are set OR the
    user has saved their own encrypted credentials in the broker_connections
    collection. Never exposes any credentials.
    """
    out_venues: Dict[str, Dict[str, Any]] = {}
    for venue in ("polymarket", "kalshi"):
        env_ready = is_live_configured(venue)
        creds = await load_user_broker_creds(db, str(user["_id"]), venue)
        user_ready = creds is not None and get_broker(venue, creds=creds).is_configured()  # type: ignore[attr-defined]
        out_venues[venue] = {
            "configured": bool(env_ready or user_ready),
            "source": "user" if user_ready else ("env" if env_ready else None),
        }
    return {
        "live_ingest": LIVE_INGEST_ENABLED,
        "venues": out_venues,
        "current_user": {
            "live_mode": bool(user.get("live_mode")),
            "live_broker": user.get("live_broker") or None,
        },
    }


class LiveBrokerIn(BaseModel):
    broker: Optional[str] = None  # "polymarket" | "kalshi" | null to clear


@api.patch("/me/live-broker")
async def set_live_broker(payload: LiveBrokerIn, user: dict = Depends(get_current_user)):
    """Choose the venue routed to when live_mode is on. Refuses values whose creds aren't ready.

    Credentials may come from the per-user `broker_connections` collection OR
    operator env vars — either is acceptable.
    """
    desired = (payload.broker or "").lower() or None
    if desired and desired not in ("polymarket", "kalshi"):
        raise HTTPException(status_code=400, detail="broker must be 'polymarket', 'kalshi', or null")
    if desired:
        creds = await load_user_broker_creds(db, str(user["_id"]), desired)
        broker_inst = get_broker(desired, creds=creds)
        configured = bool(getattr(broker_inst, "is_configured", lambda: False)())
        if not configured:
            raise HTTPException(
                status_code=400,
                detail=f"No credentials for {desired}. Connect the broker in Settings first.",
            )
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"live_broker": desired}})
    await audit(str(user["_id"]), "user.live_broker_set", {"broker": desired})
    return {"ok": True, "live_broker": desired}


# ---------- broker connections (in-app secure credential vault) ----------
class BrokerConnIn(BaseModel):
    venue: str  # "polymarket" | "kalshi"
    # Polymarket fields
    private_key: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    api_passphrase: Optional[str] = None
    # Kalshi fields
    key_id: Optional[str] = None
    private_key_pem: Optional[str] = None


def _validate_broker_creds(venue: str, raw: dict) -> dict:
    """Return a clean creds dict for `venue`, raising 400 on missing required fields."""
    if venue == "polymarket":
        creds = {k: (raw.get(k) or "").strip() for k in POLYMARKET_REQUIRED}
        missing = [k for k in POLYMARKET_REQUIRED if not creds[k]]
        if missing:
            raise HTTPException(status_code=400, detail=f"Polymarket missing fields: {', '.join(missing)}")
        # Lightweight sanity: private key should be 0x...64hex (66 chars) or 64 hex.
        pk = creds["private_key"]
        pk_hex = pk[2:] if pk.startswith("0x") else pk
        if len(pk_hex) != 64 or not all(c in "0123456789abcdefABCDEF" for c in pk_hex):
            raise HTTPException(status_code=400, detail="Polymarket private_key must be 64-char hex (optional 0x prefix)")
        return creds
    if venue == "kalshi":
        creds = {k: (raw.get(k) or "").strip() for k in KALSHI_REQUIRED}
        missing = [k for k in KALSHI_REQUIRED if not creds[k]]
        if missing:
            raise HTTPException(status_code=400, detail=f"Kalshi missing fields: {', '.join(missing)}")
        if "BEGIN" not in creds["private_key_pem"] or "PRIVATE KEY" not in creds["private_key_pem"]:
            raise HTTPException(status_code=400, detail="Kalshi private_key_pem must be a PEM-encoded key")
        return creds
    raise HTTPException(status_code=400, detail="venue must be 'polymarket' or 'kalshi'")


def _public_broker_connection(row: dict) -> dict:
    return {
        "venue": row["venue"],
        "fingerprint": row.get("fingerprint", "—"),
        "status": row.get("status", "untested"),
        "last_tested_at": row["last_tested_at"].isoformat() if row.get("last_tested_at") else None,
        "last_test_result": row.get("last_test_result"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


@api.get("/me/broker-connections")
async def list_broker_connections(user: dict = Depends(get_current_user)):
    """List the user's stored broker connections — NEVER returns secret material."""
    rows: List[dict] = []
    async for r in db.broker_connections.find({"user_id": str(user["_id"])}).sort("venue", 1):
        rows.append(_public_broker_connection(r))
    return {"connections": rows}


@api.post("/me/broker-connections")
async def upsert_broker_connection(payload: BrokerConnIn, user: dict = Depends(get_current_user)):
    """Encrypt & store a venue's credentials at-rest in Mongo.

    Idempotent on (user_id, venue) — calling with the same venue overwrites.
    """
    venue = (payload.venue or "").lower()
    raw = payload.model_dump(exclude_none=True)
    raw.pop("venue", None)
    creds = _validate_broker_creds(venue, raw)

    # Encrypt → at-rest. The Fernet key (TRACKMIND_MASTER_KEY) is the only thing
    # the operator must protect.
    try:
        encrypted = encrypt_payload(creds)
    except RuntimeError as e:
        # Almost always missing master key.
        raise HTTPException(status_code=500, detail=str(e))
    fp = fingerprint_for(venue, creds)
    now = datetime.now(timezone.utc)
    await db.broker_connections.update_one(
        {"user_id": str(user["_id"]), "venue": venue},
        {
            "$set": {
                "encrypted_payload": encrypted,
                "fingerprint": fp,
                "status": "untested",
                "updated_at": now,
            },
            "$setOnInsert": {
                "user_id": str(user["_id"]),
                "venue": venue,
                "created_at": now,
            },
        },
        upsert=True,
    )
    await audit(str(user["_id"]), "broker.connect", {"venue": venue, "fingerprint": fp})
    row = await db.broker_connections.find_one({"user_id": str(user["_id"]), "venue": venue})
    return _public_broker_connection(row or {})


@api.post("/me/broker-connections/test")
async def test_broker_connection(payload: BrokerConnIn, user: dict = Depends(get_current_user)):
    """Run an authenticated probe against the venue using the stored creds.

    If `payload` includes credential fields, those are used (allows "test before save").
    Otherwise we decrypt the user's saved record.
    """
    venue = (payload.venue or "").lower()
    raw = payload.model_dump(exclude_none=True)
    raw.pop("venue", None)
    creds: Optional[dict] = None
    if any(raw.get(k) for k in (POLYMARKET_REQUIRED + KALSHI_REQUIRED)):
        creds = _validate_broker_creds(venue, raw)
    else:
        creds = await load_user_broker_creds(db, str(user["_id"]), venue)
        if not creds:
            raise HTTPException(status_code=404, detail="No saved connection for that venue")

    broker_inst = get_broker(venue, creds=creds)
    result = await broker_inst.test_connection()  # type: ignore[attr-defined]
    now = datetime.now(timezone.utc)
    # Persist the test outcome on the saved row (if one exists).
    await db.broker_connections.update_one(
        {"user_id": str(user["_id"]), "venue": venue},
        {
            "$set": {
                "status": "active" if result.get("ok") else "failed",
                "last_tested_at": now,
                "last_test_result": result.get("detail", ""),
            }
        },
    )
    await audit(str(user["_id"]), "broker.test", {"venue": venue, "ok": bool(result.get("ok"))})
    return {"ok": bool(result.get("ok")), "detail": result.get("detail", "")}


@api.delete("/me/broker-connections/{venue}")
async def delete_broker_connection(venue: str, user: dict = Depends(get_current_user)):
    venue = venue.lower()
    if venue not in ("polymarket", "kalshi"):
        raise HTTPException(status_code=400, detail="venue must be 'polymarket' or 'kalshi'")
    res = await db.broker_connections.delete_one({"user_id": str(user["_id"]), "venue": venue})
    # If the active live_broker was this venue, clear it so we don't dangle.
    if (user.get("live_broker") or "").lower() == venue:
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"live_broker": None, "live_mode": False}},
        )
    await audit(str(user["_id"]), "broker.disconnect", {"venue": venue})
    return {"ok": True, "deleted": res.deleted_count}


# ---------- risk limits ----------
class RiskLimitsIn(BaseModel):
    daily_loss_pct: Optional[float] = None  # e.g. 0.05 = 5% of starting bankroll
    max_trade_pct: Optional[float] = None   # e.g. 0.02 = 2% of bankroll per trade


@api.get("/me/risk-limits")
async def get_risk_limits(user: dict = Depends(get_current_user)):
    dl, mt = _risk_limits_for(user)
    starting = float(user.get("starting_bankroll", INITIAL_BANKROLL))
    bankroll = float(user.get("bankroll", INITIAL_BANKROLL))
    realized_today = float(user.get("realized_pnl_today", 0.0))
    daily_loss_dollar = -starting * dl
    return {
        "daily_loss_pct": round(dl, 4),
        "max_trade_pct": round(mt, 4),
        "defaults": {
            "daily_loss_pct": DEFAULT_DAILY_LOSS_PCT,
            "max_trade_pct": DEFAULT_MAX_TRADE_PCT,
        },
        "caps": {
            "daily_loss_pct_max": MAX_DAILY_LOSS_PCT,
            "max_trade_pct_max": MAX_PER_TRADE_PCT,
        },
        "preview": {
            "daily_loss_dollar": round(daily_loss_dollar, 2),
            "max_trade_dollar": round(bankroll * mt, 2),
            "realized_pnl_today": round(realized_today, 2),
            "circuit_tripped": realized_today <= daily_loss_dollar,
        },
    }


@api.patch("/me/risk-limits")
async def update_risk_limits(payload: RiskLimitsIn, user: dict = Depends(get_current_user)):
    update: dict = {}
    if payload.daily_loss_pct is not None:
        dl = float(payload.daily_loss_pct)
        if dl <= 0 or dl > MAX_DAILY_LOSS_PCT:
            raise HTTPException(
                status_code=400,
                detail=f"daily_loss_pct must be in (0, {MAX_DAILY_LOSS_PCT}]",
            )
        update["daily_loss_pct"] = dl
    if payload.max_trade_pct is not None:
        mt = float(payload.max_trade_pct)
        if mt <= 0 or mt > MAX_PER_TRADE_PCT:
            raise HTTPException(
                status_code=400,
                detail=f"max_trade_pct must be in (0, {MAX_PER_TRADE_PCT}]",
            )
        update["max_trade_pct"] = mt
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")
    await db.users.update_one({"_id": user["_id"]}, {"$set": update})
    await audit(str(user["_id"]), "risk_limits.update", update)
    user = await db.users.find_one({"_id": user["_id"]})
    return await get_risk_limits(user)  # type: ignore[arg-type]



# ---------- push notifications ----------
class PushTokenIn(BaseModel):
    token: str
    platform: Optional[str] = None  # "ios" | "android" | "web"


@api.post("/me/push-token")
async def register_push_token(payload: PushTokenIn, user: dict = Depends(get_current_user)):
    """Idempotently store the device's Expo push token under the user's profile."""
    if not payload.token:
        raise HTTPException(status_code=400, detail="token is required")
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$addToSet": {
                "push_tokens": {
                    "token": payload.token,
                    "platform": payload.platform or "unknown",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        },
    )
    await audit(str(user["_id"]), "push.register", {"platform": payload.platform})
    return {"ok": True}


@api.delete("/me/push-token")
async def remove_push_token(payload: PushTokenIn, user: dict = Depends(get_current_user)):
    """Remove a single device push token (e.g. on logout from that device)."""
    await db.users.update_one(
        {"_id": user["_id"]},
        {"$pull": {"push_tokens": {"token": payload.token}}},
    )
    return {"ok": True}


# Serial state for the "master signal" alert deduper.
_master_signal_state: Dict[str, Any] = {"last_id": None, "last_ts": 0.0}
MASTER_SIGNAL_THRESHOLD = float(os.environ.get("MASTER_SIGNAL_THRESHOLD", "0.65"))  # confidence × edge_norm
MASTER_SIGNAL_COOLDOWN_SEC = int(os.environ.get("MASTER_SIGNAL_COOLDOWN_SEC", "1200"))  # 20 min


async def _maybe_send_master_signal_alert() -> None:
    """If the strongest current signal crosses the threshold, fan out a push to all users.

    Deduped by signal id + cooldown so we don't spam.
    """
    async with _engine_cache_lock:
        signals = list(_engine_cache["signals"])
    if not signals:
        return
    top = max(
        signals,
        key=lambda s: float(s.get("confidence", 0)) * float(s.get("expected_edge", 0)) * 10,
    )
    score = float(top.get("confidence", 0)) * float(top.get("expected_edge", 0)) * 10
    if score < MASTER_SIGNAL_THRESHOLD:
        return
    now = time.time()
    if (
        _master_signal_state["last_id"] == top["id"]
        and (now - _master_signal_state["last_ts"]) < MASTER_SIGNAL_COOLDOWN_SEC
    ):
        return
    _master_signal_state["last_id"] = top["id"]
    _master_signal_state["last_ts"] = now

    title = f"MASTER SIGNAL · {top['engine'].upper()} {top['side']}"
    body = f"{top['market'][:80]} · conf {int(top['confidence']*100)}% · edge +{top['expected_edge']*100:.1f}%"
    data = {"signal_id": top["id"], "engine": top["engine"], "kind": "master_signal", "type": "master_signal"}
    delivered = 0
    skipped = 0
    try:
        async for u in db.users.find({"push_tokens.0": {"$exists": True}}):
            # push_to_user already filters by `notification_prefs.master_signal`
            # (default ON) and auto-prunes dead tokens.
            r = await _push_to_user_async(db, u, category="master_signal", title=title, body=body, data=data)
            if r.get("skipped"):
                skipped += 1
            else:
                delivered += int(r.get("ok") or 0)
        logger.info(
            "master_signal_alert_sent",
            extra={"signal_id": top["id"], "engine": top["engine"], "delivered": delivered, "opted_out": skipped},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"master signal alert error: {e}")


@api.get("/admin/audit")
async def admin_audit(
    user_id: Optional[str] = None,
    limit: int = 100,
    admin: dict = Depends(require_admin),
):
    """Admin view of the audit log. Filter by user_id if provided."""
    limit = max(1, min(500, limit))
    q: dict = {}
    if user_id:
        q["user_id"] = user_id
    rows = [
        {
            "action": r.get("action"),
            "user_id": r.get("user_id"),
            "payload": r.get("payload", {}),
            "ts": r["ts"].isoformat() if r.get("ts") else None,
        }
        async for r in db.audit_logs.find(q).sort("ts", -1).limit(limit)
    ]
    return {"events": rows, "count": len(rows)}


app.include_router(api)


# ---------- Prometheus /metrics ----------
# Also mirrored under /api/metrics for kube-ingress compatibility.
from starlette.responses import Response as _StarletteResponse  # noqa: E402


async def _prom_metrics_response() -> _StarletteResponse:
    try:
        async with _engine_cache_lock:
            for eng, sigs in _engine_cache["by_engine"].items():
                engine_signals_gauge.labels(eng).set(len(sigs))
        open_positions_gauge.set(await db.positions.count_documents({"status": "open"}))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"metrics gauge refresh failed: {e}")
    body, ctype = metrics_payload()
    return _StarletteResponse(content=body, media_type=ctype)


@app.get("/metrics")
async def _metrics_root():
    return await _prom_metrics_response()


@app.get("/api/metrics")
async def _metrics_api():
    return await _prom_metrics_response()


# ---------- startup ----------
scheduler: Optional[AsyncIOScheduler] = None


@app.on_event("startup")
async def on_startup():
    # Indexes
    await db.users.create_index("email", unique=True)
    await db.positions.create_index([("user_id", 1), ("status", 1)])
    await db.login_attempts.create_index("identifier")
    # New: TTL auto-purge on revoked tokens (exp is stored absolute) and idempotency records.
    await db.revoked_tokens.create_index("jti", unique=True)
    await db.revoked_tokens.create_index("exp", expireAfterSeconds=0)
    await db.idempotency_keys.create_index([("user_id", 1), ("key", 1)], unique=True)
    await db.idempotency_keys.create_index("created_at", expireAfterSeconds=600)  # 10 min
    # Broker connections — encrypted at-rest per user/venue.
    await db.broker_connections.create_index([("user_id", 1), ("venue", 1)], unique=True)
    # Audit log retention (30 days) — drop any pre-existing ts index then create TTL.
    try:
        await db.audit_logs.drop_index("ts_1")
    except Exception:
        pass
    await db.audit_logs.create_index("ts", expireAfterSeconds=60 * 60 * 24 * 30, name="audit_ttl")

    # Optional live ingest. Off by default; flip LIVE_INGEST_ENABLED=true to enable.
    if LIVE_INGEST_ENABLED:
        asyncio.create_task(ingest_forever())
        logger.info("live ingest task scheduled")

    # Seed admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@trackmind.io")
    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin123!")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "email": admin_email,
            "password_hash": hash_password(admin_password),
            "name": "Admin",
            "role": "admin",
            "bankroll": INITIAL_BANKROLL,
            "starting_bankroll": INITIAL_BANKROLL,
            "realized_pnl_today": 0.0,
            "biometrics_enabled": False,
            "live_mode": False,
            "paused_engines": [],
            "kill_switch": False,
            "created_at": datetime.now(timezone.utc),
        })
        logger.info(f"seeded admin {admin_email}")
    elif not verify_password(admin_password, existing["password_hash"]):
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"password_hash": hash_password(admin_password)}},
        )

    # Initial engine refresh
    try:
        await refresh_engines()
    except Exception as e:
        logger.exception(f"initial engine refresh failed: {e}")

    # Scheduler
    global scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(refresh_engines, "interval", seconds=60, id="engines", replace_existing=True)

    async def daily_rollover():
        await db.users.update_many({}, {"$set": {"realized_pnl_today": 0.0}})

    scheduler.add_job(daily_rollover, "cron", hour=0, minute=0, id="rollover", replace_existing=True)
    scheduler.start()
    logger.info("scheduler started")


@app.on_event("shutdown")
async def on_shutdown():
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
    mongo_client.close()
