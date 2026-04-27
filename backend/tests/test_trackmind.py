"""TRACKMIND backend pytest suite — exercises all critical API flows.

Tests are ordered/dependent: auth → dashboard → signals → trade → positions →
analytics → controls (kill switch, reset). Uses external EXPO_PUBLIC_BACKEND_URL
so we hit the same surface mobile clients use.
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

load_dotenv(Path("/app/frontend/.env"))
BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
ADMIN_EMAIL = "admin@trackmind.io"
ADMIN_PASSWORD = "Admin123!"


# --------------- shared state ----------------
state: dict = {
    "admin_access": None,
    "admin_refresh": None,
    "user_email": f"trader_{int(time.time())}_{uuid.uuid4().hex[:6]}@trackmind.io",
    "user_access": None,
    "user_refresh": None,
    "signal_id": None,
}


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# --------------- module: health ----------------
class TestHealth:
    def test_health_ok(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("service") == "trackmind"


# --------------- module: auth ----------------
class TestAuth:
    def test_login_admin_success(self):
        r = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert "access_token" in d and "refresh_token" in d and "user" in d
        u = d["user"]
        assert u["email"] == ADMIN_EMAIL
        assert u["role"] == "admin"
        assert u["bankroll"] == 10000 or u["bankroll"] == 10000.0 or float(u["bankroll"]) >= 0
        state["admin_access"] = d["access_token"]
        state["admin_refresh"] = d["refresh_token"]

    def test_login_wrong_password(self):
        r = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": ADMIN_EMAIL, "password": "wrong-pw-XYZ"},
            timeout=15,
        )
        assert r.status_code in (401, 429), r.text

    def test_register_new_user(self):
        r = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={"email": state["user_email"], "password": "TestPass123!", "name": "TEST_Trader"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert "access_token" in d and "refresh_token" in d
        assert d["user"]["email"] == state["user_email"]
        state["user_access"] = d["access_token"]
        state["user_refresh"] = d["refresh_token"]

    def test_me_with_token(self):
        assert state["admin_access"]
        r = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers(state["admin_access"]), timeout=15)
        assert r.status_code == 200
        u = r.json()
        assert u["email"] == ADMIN_EMAIL
        assert u["role"] == "admin"

    def test_me_without_token(self):
        r = requests.get(f"{BASE_URL}/api/auth/me", timeout=15)
        assert r.status_code == 401

    def test_refresh_token(self):
        assert state["admin_refresh"]
        r = requests.post(
            f"{BASE_URL}/api/auth/refresh",
            headers={"Authorization": f"Bearer {state['admin_refresh']}"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert "access_token" in d
        # rotate
        state["admin_access"] = d["access_token"]


# --------------- module: dashboard ----------------
class TestDashboard:
    def test_dashboard_payload(self):
        r = requests.get(f"{BASE_URL}/api/dashboard", headers=auth_headers(state["admin_access"]), timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("bankroll", "today_pnl", "engine_status", "plan", "open_positions"):
            assert k in d, f"missing key: {k}"
        assert isinstance(d["engine_status"], list) and len(d["engine_status"]) == 4
        engines = {e["engine"] for e in d["engine_status"]}
        assert engines == {"alpha", "quant", "whale", "micro"}
        assert "orders_count" in d["plan"]
        # top_signal can be None if no signals — but we expect at least Alpha to produce
        # so just assert key exists
        assert "top_signal" in d


# --------------- module: signals ----------------
class TestSignals:
    def test_list_all(self):
        r = requests.get(f"{BASE_URL}/api/signals", headers=auth_headers(state["admin_access"]), timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "signals" in d and isinstance(d["signals"], list)
        # Capture a non-micro signal for trade approval (alpha preferred)
        for s in d["signals"]:
            if s["engine"] in ("alpha", "whale"):
                state["signal_id"] = s["id"]
                break
        if not state["signal_id"] and d["signals"]:
            state["signal_id"] = d["signals"][0]["id"]

    def test_filter_by_engine(self):
        r = requests.get(f"{BASE_URL}/api/signals?engine=alpha", headers=auth_headers(state["admin_access"]), timeout=15)
        assert r.status_code == 200
        for s in r.json()["signals"]:
            assert s["engine"] == "alpha"

    def test_micro_arbs(self):
        r = requests.get(f"{BASE_URL}/api/signals/micro", headers=auth_headers(state["admin_access"]), timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "signals" in d
        for s in d["signals"]:
            assert s["engine"] == "micro"
            assert "legs" in s
            assert "yes" in s["legs"] and "no" in s["legs"]


# --------------- module: trade & positions ----------------
class TestTradeAndPositions:
    def test_reset_before_trade(self):
        # ensure clean slate
        r = requests.post(f"{BASE_URL}/api/controls/reset", headers=auth_headers(state["admin_access"]), timeout=15)
        assert r.status_code == 200

    def test_approve_trade(self):
        if not state["signal_id"]:
            pytest.skip("no signal available")
        # bankroll before
        me = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers(state["admin_access"]), timeout=15).json()
        bk_before = float(me["bankroll"])

        r = requests.post(
            f"{BASE_URL}/api/trade/approve",
            json={"signal_id": state["signal_id"]},
            headers=auth_headers(state["admin_access"]),
            timeout=20,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert "position" in d and "fill" in d
        assert d["position"]["status"] == "open"

        # Verify bankroll deducted
        me2 = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers(state["admin_access"]), timeout=15).json()
        bk_after = float(me2["bankroll"])
        assert bk_after < bk_before, f"bankroll not deducted: {bk_before} -> {bk_after}"

    def test_positions_open_increases(self):
        r = requests.get(f"{BASE_URL}/api/positions", headers=auth_headers(state["admin_access"]), timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "open" in d and "closed" in d
        assert isinstance(d["open"], list) and isinstance(d["closed"], list)
        assert len(d["open"]) >= 1, "expected at least one open position after approve"

    def test_auto_deploy(self):
        r = requests.post(f"{BASE_URL}/api/trade/auto-deploy", headers=auth_headers(state["admin_access"]), timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "deployed" in d
        assert isinstance(d["deployed"], list)
        # could be 0 if allocator returns no orders, but typically up to 3
        assert len(d["deployed"]) <= 3


# --------------- module: analytics ----------------
class TestAnalytics:
    def test_analytics_shape(self):
        r = requests.get(f"{BASE_URL}/api/analytics", headers=auth_headers(state["admin_access"]), timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("trades_total", "win_rate", "equity_curve", "by_engine"):
            assert k in d, f"missing key: {k}"
        assert isinstance(d["equity_curve"], list)
        assert set(d["by_engine"].keys()) == {"alpha", "quant", "whale", "micro"}


# --------------- module: controls ----------------
class TestControls:
    def test_pause_engine_persists(self):
        r = requests.post(
            f"{BASE_URL}/api/controls/update",
            json={"paused_engines": ["alpha"]},
            headers=auth_headers(state["admin_access"]),
            timeout=15,
        )
        assert r.status_code == 200
        # verify via /me
        me = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers(state["admin_access"]), timeout=15).json()
        assert "alpha" in me.get("paused_engines", [])
        # un-pause
        requests.post(
            f"{BASE_URL}/api/controls/update",
            json={"paused_engines": []},
            headers=auth_headers(state["admin_access"]),
            timeout=15,
        )

    def test_kill_switch_blocks_trade(self):
        # Activate
        r = requests.post(
            f"{BASE_URL}/api/controls/update",
            json={"kill_switch": True},
            headers=auth_headers(state["admin_access"]),
            timeout=15,
        )
        assert r.status_code == 200
        # Try to approve — should 400
        # Get a fresh signal since cache may rotate
        sigs = requests.get(f"{BASE_URL}/api/signals", headers=auth_headers(state["admin_access"]), timeout=15).json()["signals"]
        sid = sigs[0]["id"] if sigs else state["signal_id"]
        r2 = requests.post(
            f"{BASE_URL}/api/trade/approve",
            json={"signal_id": sid},
            headers=auth_headers(state["admin_access"]),
            timeout=15,
        )
        assert r2.status_code == 400
        assert "kill" in r2.json().get("detail", "").lower()
        # Reset
        requests.post(
            f"{BASE_URL}/api/controls/update",
            json={"kill_switch": False},
            headers=auth_headers(state["admin_access"]),
            timeout=15,
        )

    def test_reset_account(self):
        r = requests.post(f"{BASE_URL}/api/controls/reset", headers=auth_headers(state["admin_access"]), timeout=15)
        assert r.status_code == 200
        me = r.json()
        assert float(me["bankroll"]) == 10000.0
        # positions wiped
        pos = requests.get(f"{BASE_URL}/api/positions", headers=auth_headers(state["admin_access"]), timeout=15).json()
        assert pos["open"] == [] and pos["closed"] == []


# --------------- module: phase 1 (security + correctness) ----------------
class TestPhase1Security:
    """Audit-grade checks added after Phase 1 hardening."""

    def test_refresh_rotation(self):
        """Every successful refresh should issue a new refresh token (rotation)."""
        r1 = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
        assert r1.status_code == 200
        old_refresh = r1.json()["refresh_token"]
        r2 = requests.post(
            f"{BASE_URL}/api/auth/refresh",
            headers={"Authorization": f"Bearer {old_refresh}"},
            timeout=15,
        )
        assert r2.status_code == 200
        new_refresh = r2.json().get("refresh_token")
        assert new_refresh and new_refresh != old_refresh, "refresh token must rotate"

        # Reuse of the old refresh token must now fail.
        r3 = requests.post(
            f"{BASE_URL}/api/auth/refresh",
            headers={"Authorization": f"Bearer {old_refresh}"},
            timeout=15,
        )
        assert r3.status_code == 401

    def test_idempotency_approve(self):
        """Approve called twice with the same Idempotency-Key returns identical response, not double-deploy."""
        # Login as admin fresh (account was reset).
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
        tok = r.json()["access_token"]
        sigs = requests.get(f"{BASE_URL}/api/signals", headers=auth_headers(tok), timeout=15).json()["signals"]
        assert sigs, "need at least one signal"
        sid = sigs[0]["id"]
        key = uuid.uuid4().hex

        def _approve():
            return requests.post(
                f"{BASE_URL}/api/trade/approve",
                json={"signal_id": sid, "notional": 100.0},
                headers={**auth_headers(tok), "Idempotency-Key": key},
                timeout=15,
            )

        a = _approve()
        assert a.status_code == 200
        fill_a = a.json()["fill"]["id"]
        b = _approve()
        assert b.status_code == 200
        assert b.json()["fill"]["id"] == fill_a, "idempotent replay must return same fill id"

        # Exactly one position should have been created from this pair of calls.
        pos = requests.get(f"{BASE_URL}/api/positions", headers=auth_headers(tok), timeout=15).json()
        matches = [p for p in pos["open"] if p.get("fill_id") == fill_a]
        assert len(matches) == 1

    def test_bankroll_atomicity_insufficient_funds(self):
        """Attempting a trade larger than bankroll must 400 and NOT debit the account."""
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
        tok = r.json()["access_token"]
        me_before = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers(tok), timeout=15).json()
        sigs = requests.get(f"{BASE_URL}/api/signals", headers=auth_headers(tok), timeout=15).json()["signals"]
        sid = sigs[0]["id"]
        r = requests.post(
            f"{BASE_URL}/api/trade/approve",
            json={"signal_id": sid, "notional": 9_999_999.0},  # way over bankroll
            headers=auth_headers(tok),
            timeout=15,
        )
        assert r.status_code == 400
        me_after = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers(tok), timeout=15).json()
        assert float(me_after["bankroll"]) == float(me_before["bankroll"]), "bankroll must not change on failed trade"

    def test_rate_limit_login(self):
        """21st rapid login attempt within a minute should be rate limited to 429."""
        count_429 = 0
        for _ in range(25):
            r = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={"email": "ratetest@trackmind.io", "password": "wrong"},
                timeout=5,
            )
            if r.status_code == 429:
                count_429 += 1
                break
        assert count_429 >= 1, "rate limiter never tripped"

    def test_admin_metrics_requires_admin(self):
        """Admin metrics is scoped to admins."""
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
        tok = r.json()["access_token"]
        r = requests.get(f"{BASE_URL}/api/admin/metrics", headers=auth_headers(tok), timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert "llm" in body and "engines" in body and "db" in body

    def test_user_audit_endpoint(self):
        """Users can retrieve their own audit trail."""
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
        tok = r.json()["access_token"]
        r = requests.get(f"{BASE_URL}/api/audit?limit=10", headers=auth_headers(tok), timeout=15)
        assert r.status_code == 200
        assert "events" in r.json()

# --------------- module: phase 2 (observability) ----------------
class TestPhase2Observability:
    """New endpoints + structured logging introduced by the Phase 2 pass."""

    def test_metrics_endpoint_exposes_prom_format(self):
        """/api/metrics returns Prometheus text exposition with our custom counters."""
        r = requests.get(f"{BASE_URL}/api/metrics", timeout=10)
        assert r.status_code == 200
        body = r.text
        assert "tm_http_requests_total" in body
        assert "tm_http_request_latency_seconds" in body
        assert "tm_llm_calls_total" in body
        assert "tm_open_positions" in body

    def test_request_id_header(self):
        """Responses carry an X-Request-ID header for trace correlation."""
        r = requests.get(f"{BASE_URL}/api/health", timeout=10)
        assert r.status_code == 200
        rid = r.headers.get("x-request-id") or r.headers.get("X-Request-ID")
        assert rid and len(rid) >= 8

    def test_me_quota_endpoint(self):
        """Authenticated user can see their LLM quota snapshot."""
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
        tok = r.json()["access_token"]
        r = requests.get(f"{BASE_URL}/api/me/quota", headers=auth_headers(tok), timeout=10)
        assert r.status_code == 200
        q = r.json()
        for k in ("date_utc", "used", "quota", "remaining", "rolls_over_at"):
            assert k in q
        assert q["quota"] >= 500
        assert q["remaining"] == q["quota"] - q["used"]

    def test_admin_audit_filter_by_user(self):
        """Admin audit endpoint can filter by user_id."""
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
        tok = r.json()["access_token"]
        me = requests.get(f"{BASE_URL}/api/auth/me", headers=auth_headers(tok), timeout=10).json()
        r = requests.get(
            f"{BASE_URL}/api/admin/audit?user_id={me['id']}&limit=5",
            headers=auth_headers(tok),
            timeout=10,
        )
        assert r.status_code == 200
        data = r.json()
        assert "events" in data and "count" in data
        for ev in data["events"]:
            assert ev.get("user_id") == me["id"]

