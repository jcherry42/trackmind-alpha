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
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import bcrypt
import jwt
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bson import ObjectId
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status
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


def create_refresh(user_id: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "type": "refresh",
            "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS),
        },
        jwt_secret(),
        algorithm=JWT_ALGORITHM,
    )


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
        alpha_signals = await alpha_engine.run_async(use_llm=True)
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
    """Tick existing open positions: mark-to-market, randomly settle ~5% per tick.

    Returns realized PnL delta added to bankroll.
    """
    user_id = str(user["_id"])
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

        # Randomly settle ~7% per tick (paper-mode quick cycles for engaging UX)
        if random.random() < 0.07:
            # Settlement: 60-70% probability of being on the right side weighted by edge
            win_prob = 0.5 + min(0.25, pos.get("expected_edge", 0.0) * 4)
            won = random.random() < win_prob
            payout = (1.0 if won else 0.0) if pos["engine"] != "micro" else (1.0 - pos.get("fees_pct", 0.01))
            realized = round((payout - pos["entry_price"]) * pos["contracts"] - pos.get("fees", 0), 2)
            await db.positions.update_one(
                {"_id": pos["_id"]},
                {"$set": {
                    "status": "closed",
                    "exit_price": round(payout, 4),
                    "realized_pnl": realized,
                    "closed_at": datetime.now(timezone.utc),
                }},
            )
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
async def register(payload: RegisterIn, response: Response):
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
    refresh = create_refresh(str(user["_id"]))
    response.set_cookie("access_token", access, httponly=True, samesite="lax", max_age=ACCESS_TTL_MIN * 60, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, samesite="lax", max_age=REFRESH_TTL_DAYS * 86400, path="/")
    await audit(str(user["_id"]), "auth.register")
    return {"access_token": access, "refresh_token": refresh, "user": public_user(user)}


@api.post("/auth/login")
async def login(payload: LoginIn, request: Request, response: Response):
    email = payload.email.lower().strip()
    ip = request.client.host if request.client else "unknown"
    identifier = f"{ip}:{email}"

    # Brute force lockout
    attempt = await db.login_attempts.find_one({"identifier": identifier})
    if attempt and attempt.get("count", 0) >= 5:
        if (datetime.now(timezone.utc) - attempt["last_at"]).total_seconds() < 900:
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
    refresh = create_refresh(str(user["_id"]))
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
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        access = create_access(str(user["_id"]), user["email"], user["role"])
        response.set_cookie("access_token", access, httponly=True, samesite="lax", max_age=ACCESS_TTL_MIN * 60, path="/")
        return {"access_token": access, "user": public_user(user)}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


@api.post("/auth/logout")
async def logout(response: Response, user: dict = Depends(get_current_user)):
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


# ---------- trade ----------
@api.post("/trade/approve")
async def approve_trade(payload: TradeApproveIn, user: dict = Depends(get_current_user)):
    user = await db.users.find_one({"_id": user["_id"]})
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

    broker = get_broker("paper")  # always paper unless live_mode + integration ready
    fill = await broker.place_order(user_id=str(user["_id"]), signal=sig, notional=notional)

    pos_doc = {
        "user_id": str(user["_id"]),
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
    await db.users.update_one({"_id": user["_id"]}, {"$inc": {"bankroll": -fill["notional"]}})
    await audit(str(user["_id"]), "trade.approve", {"signal_id": payload.signal_id, "notional": fill["notional"]})
    pos_doc["_id"] = res.inserted_id
    return {"position": _serialize_position(pos_doc), "fill": fill}


@api.post("/trade/auto-deploy")
async def auto_deploy(user: dict = Depends(get_current_user)):
    """One-tap: take the top N orders from the allocator and execute them."""
    user = await db.users.find_one({"_id": user["_id"]})
    if user.get("kill_switch"):
        raise HTTPException(status_code=400, detail="Kill switch is active")
    async with _engine_cache_lock:
        signals = list(_engine_cache["signals"])

    # Recompute today's pnl_pct quickly
    realized_today = float(user.get("realized_pnl_today", 0.0))
    starting = float(user.get("starting_bankroll", INITIAL_BANKROLL))
    pnl_pct = round(realized_today / max(starting, 1.0), 4)

    paused = set(user.get("paused_engines", []))
    plan = allocate(signals, float(user["bankroll"]), pnl_pct, paused)

    deployed = []
    for order in plan["orders"][:3]:
        broker = get_broker("paper")
        fill = await broker.place_order(user_id=str(user["_id"]), signal=order, notional=order["notional"])
        pos_doc = {
            "user_id": str(user["_id"]),
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
        await db.users.update_one({"_id": user["_id"]}, {"$inc": {"bankroll": -fill["notional"]}})
        pos_doc["_id"] = res.inserted_id
        deployed.append(_serialize_position(pos_doc))
    await audit(str(user["_id"]), "trade.auto_deploy", {"count": len(deployed)})
    return {"deployed": deployed, "plan": {"orders_count": len(plan["orders"])}}


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


app.include_router(api)


# ---------- startup ----------
scheduler: Optional[AsyncIOScheduler] = None


@app.on_event("startup")
async def on_startup():
    # Indexes
    await db.users.create_index("email", unique=True)
    await db.positions.create_index([("user_id", 1), ("status", 1)])
    await db.audit_logs.create_index("ts")
    await db.login_attempts.create_index("identifier")

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
