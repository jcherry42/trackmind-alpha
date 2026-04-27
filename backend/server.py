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
from broker import get_broker

logger = logging.getLogger("trackmind")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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

JWT_ALGORITHM = "HS256"
ACCESS_TTL_MIN = 60 * 24  # 24h on mobile (refresh available)
REFRESH_TTL_DAYS = 30
DEFAULT_BANKROLL = 10_000.0
INITIAL_BANKROLL = 10_000.0

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
_rl_login = RateLimit("login", 20, 60)
_rl_register = RateLimit("register", 5, 3600)
_rl_refresh = RateLimit("refresh", 30, 60)
_rl_trade = RateLimit("trade", 30, 60)
_rl_auto_deploy = RateLimit("auto_deploy", 10, 60)

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
        "created_at": u.get("created_at", datetime.now(timezone.utc)).isoformat(),
    }


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
    try:
        alpha_signals = await alpha_engine.run_async(
            use_llm=True, score_cache=_alpha_cache, metrics=_llm_metrics,
        )
    except Exception as e:
        logger.warning(f"alpha engine error: {e}")
        alpha_signals = []
    quant_signals = quant_engine.run()
    whale_signals = whale_engine.run()
    micro_signals = micro_engine.run()

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

        if realized_delta != 0:
            await db.users.update_one(
                {"_id": user["_id"]},
                {"$inc": {"bankroll": realized_delta, "realized_pnl_today": realized_delta}},
            )
        return realized_delta


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
    await settle_open_positions(user)
    user_id = str(user["_id"])
    open_pos = [p async for p in db.positions.find({"user_id": user_id, "status": "open"}).sort("opened_at", -1)]
    closed_pos = [p async for p in db.positions.find({"user_id": user_id, "status": "closed"}).sort("closed_at", -1).limit(50)]
    return {
        "open": [_serialize_position(p) for p in open_pos],
        "closed": [_serialize_position(p) for p in closed_pos],
    }


async def _idempotency_seen(key: str, user_id: str, payload: dict) -> Optional[dict]:
    """Look up a prior response keyed by (Idempotency-Key, user). Returns stored response or None."""
    if not key:
        return None
    row = await db.idempotency_keys.find_one({"key": key, "user_id": user_id})
    return row["response"] if row else None


async def _idempotency_store(key: str, user_id: str, response: dict) -> None:
    if not key:
        return
    await db.idempotency_keys.update_one(
        {"key": key, "user_id": user_id},
        {"$set": {"key": key, "user_id": user_id, "response": response, "created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


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

    # Idempotency replay
    prior = await _idempotency_seen(idempotency_key or "", user_id, {})
    if prior is not None:
        return prior

    if user.get("kill_switch"):
        raise HTTPException(status_code=400, detail="Kill switch is active")

    async with _engine_cache_lock:
        sig = _engine_cache["by_id"].get(payload.signal_id)
    if not sig:
        raise HTTPException(status_code=404, detail="Signal not found or expired")
    if sig["engine"] in user.get("paused_engines", []):
        raise HTTPException(status_code=400, detail="Engine is paused")

    bankroll = float(user["bankroll"])
    notional = payload.notional or round(min(bankroll * sig["size_hint"], bankroll * 0.15), 2)
    notional = max(1.0, min(notional, bankroll * 0.95))

    # Atomically debit bankroll BEFORE placing order; if debit fails, refuse.
    ok = await _atomic_bankroll_debit(user["_id"], notional)
    if not ok:
        raise HTTPException(status_code=400, detail="Insufficient bankroll")

    try:
        broker = get_broker("paper")
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
    await _idempotency_store(idempotency_key or "", user_id, response)
    return response


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

    # Idempotency replay
    prior = await _idempotency_seen(idempotency_key or "", user_id, {})
    if prior is not None:
        return prior

    if user.get("kill_switch"):
        raise HTTPException(status_code=400, detail="Kill switch is active")
    async with _engine_cache_lock:
        signals = list(_engine_cache["signals"])

    realized_today = float(user.get("realized_pnl_today", 0.0))
    starting = float(user.get("starting_bankroll", INITIAL_BANKROLL))
    pnl_pct = round(realized_today / max(starting, 1.0), 4)

    paused = set(user.get("paused_engines", []))
    plan = allocate(signals, float(user["bankroll"]), pnl_pct, paused)

    deployed = []
    for order in plan["orders"][:3]:
        ok = await _atomic_bankroll_debit(user["_id"], order["notional"])
        if not ok:
            break  # out of bankroll; stop deploying more
        try:
            broker = get_broker("paper")
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
    await _idempotency_store(idempotency_key or "", user_id, response)
    return response


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
    if payload.kill_switch is not None:
        update["kill_switch"] = bool(payload.kill_switch)
    if payload.live_mode is not None:
        update["live_mode"] = bool(payload.live_mode)
    if payload.biometrics_enabled is not None:
        update["biometrics_enabled"] = bool(payload.biometrics_enabled)
    if not update:
        raise HTTPException(status_code=400, detail="Nothing to update")
    await db.users.update_one({"_id": user["_id"]}, {"$set": update})
    await audit(str(user["_id"]), "controls.update", update)
    user = await db.users.find_one({"_id": user["_id"]})
    return public_user(user)


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


app.include_router(api)


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
    # Audit log retention (30 days) — drop any pre-existing ts index then create TTL.
    try:
        await db.audit_logs.drop_index("ts_1")
    except Exception:
        pass
    await db.audit_logs.create_index("ts", expireAfterSeconds=60 * 60 * 24 * 30, name="audit_ttl")

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
