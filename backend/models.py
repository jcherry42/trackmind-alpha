"""Pydantic request models for the TRACKMIND API.

Kept separate from route handlers so the wire schema is easy to scan and
import-able from tests without pulling the whole FastAPI app.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


# ── auth ──────────────────────────────────────────────────────────────────────
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


# ── trade ─────────────────────────────────────────────────────────────────────
class TradeApproveIn(BaseModel):
    signal_id: str
    notional: Optional[float] = None  # override sizing


# ── controls ──────────────────────────────────────────────────────────────────
class ControlsIn(BaseModel):
    paused_engines: Optional[List[str]] = None
    kill_switch: Optional[bool] = None
    live_mode: Optional[bool] = None
    biometrics_enabled: Optional[bool] = None


class LiveBrokerIn(BaseModel):
    broker: Optional[str] = None  # "polymarket" | "kalshi" | null to clear


# ── brokers (Phase D) ─────────────────────────────────────────────────────────
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


# ── risk limits (Phase D) ─────────────────────────────────────────────────────
class RiskLimitsIn(BaseModel):
    daily_loss_pct: Optional[float] = None  # e.g. 0.05 = 5% of starting bankroll
    max_trade_pct: Optional[float] = None   # e.g. 0.02 = 2% of bankroll per trade


# ── notification prefs (Task B) ───────────────────────────────────────────────
class NotifyPrefsIn(BaseModel):
    master_signal: Optional[bool] = None
    position_settled: Optional[bool] = None
    auto_deploy: Optional[bool] = None
    daily_loss_tripped: Optional[bool] = None
    kill_switch: Optional[bool] = None


# ── push tokens ──────────────────────────────────────────────────────────────
class PushTokenIn(BaseModel):
    token: str
    platform: Optional[str] = None  # "ios" | "android" | "web"
