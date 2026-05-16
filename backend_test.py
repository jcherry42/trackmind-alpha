"""TRACKMIND Phase D backend tests.

Focus: secure broker credential vault + risk limits (daily loss circuit + per-trade cap).
"""
from __future__ import annotations

import os
import re
import sys
import time
import uuid
import json
import requests

# ── env ──
FRONTEND_ENV = "/app/frontend/.env"
BASE = None
with open(FRONTEND_ENV) as f:
    for line in f:
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE = line.strip().split("=", 1)[1].strip().strip('"')
            break
if not BASE:
    print("ERROR: EXPO_PUBLIC_BACKEND_URL not found"); sys.exit(2)
API = BASE.rstrip("/") + "/api"

ADMIN_EMAIL = "admin@trackmind.io"
ADMIN_PASS = "Admin123!"

# fake but format-valid creds
POLY_PRIVATE_KEY = "0x" + "ab" * 32  # 64 hex chars
POLY_API_KEY = "test-api-key-" + uuid.uuid4().hex[:10]
POLY_API_SECRET = "test-api-secret-" + uuid.uuid4().hex[:10]
POLY_API_PASS = "test-passphrase-" + uuid.uuid4().hex[:10]
KALSHI_KEY_ID = "kalshi-key-" + uuid.uuid4().hex[:16]
KALSHI_PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDfakeNotReal\n"
    "-----END PRIVATE KEY-----\n"
)

results = []  # list of (name, ok, detail)


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    icon = "PASS" if ok else "FAIL"
    print(f"[{icon}] {name} — {detail}")


def login(email: str, password: str) -> str:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def register(email: str, password: str, name: str = "Phase D Tester") -> str:
    r = requests.post(
        f"{API}/auth/register",
        json={"email": email, "password": password, "name": name},
        timeout=20,
    )
    if r.status_code == 400 and "exists" in r.text.lower():
        return login(email, password)
    r.raise_for_status()
    return r.json()["access_token"]


def auth_hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# ── 0. Connectivity ──
def t_health():
    r = requests.get(f"{API}/health", timeout=10)
    record("health", r.status_code == 200, f"{r.status_code}")


# ── 1. Auth ──
admin_token = None
user_token = None
user_email = None


def t_admin_login():
    global admin_token
    admin_token = login(ADMIN_EMAIL, ADMIN_PASS)
    record("admin_login", bool(admin_token), "token acquired")


def t_user_register():
    global user_token, user_email
    user_email = f"phased+{uuid.uuid4().hex[:8]}@trackmind.io"
    user_token = register(user_email, "PhaseDPass123!")
    record("user_register", bool(user_token), user_email)


# ── 2. /auth/me public_user fields ──
def t_public_user_has_risk_fields():
    r = requests.get(f"{API}/auth/me", headers=auth_hdr(user_token), timeout=15)
    if r.status_code != 200:
        record("public_user_risk_fields", False, f"http {r.status_code}: {r.text[:160]}")
        return
    me = r.json()
    has_dl = "daily_loss_pct" in me
    has_mt = "max_trade_pct" in me
    dl_default_ok = abs(float(me.get("daily_loss_pct", 0)) - 0.05) < 1e-6
    mt_default_ok = abs(float(me.get("max_trade_pct", 0)) - 0.02) < 1e-6
    ok = has_dl and has_mt and dl_default_ok and mt_default_ok
    record(
        "public_user_risk_fields",
        ok,
        f"daily_loss_pct={me.get('daily_loss_pct')}, max_trade_pct={me.get('max_trade_pct')}",
    )


# ── 3. unauth checks (401) ──
def t_unauth_endpoints():
    paths = [
        ("GET", "/me/broker-connections", None),
        ("POST", "/me/broker-connections", {"venue": "polymarket"}),
        ("POST", "/me/broker-connections/test", {"venue": "polymarket"}),
        ("DELETE", "/me/broker-connections/polymarket", None),
        ("GET", "/me/risk-limits", None),
        ("PATCH", "/me/risk-limits", {"daily_loss_pct": 0.05}),
    ]
    fails = []
    for method, path, body in paths:
        if body is None:
            r = requests.request(method, f"{API}{path}", timeout=15)
        else:
            r = requests.request(method, f"{API}{path}", json=body, timeout=15)
        if r.status_code not in (401, 403):
            fails.append(f"{method} {path} -> {r.status_code}")
    record("unauth_401", not fails, "; ".join(fails) if fails else "all return 401/403")


# ── 4. Broker connections — validation ──
def t_broker_validation():
    # Missing fields
    r = requests.post(
        f"{API}/me/broker-connections",
        headers=auth_hdr(user_token),
        json={"venue": "polymarket"},
        timeout=15,
    )
    miss_ok = r.status_code == 400
    # Bad private key
    r2 = requests.post(
        f"{API}/me/broker-connections",
        headers=auth_hdr(user_token),
        json={
            "venue": "polymarket",
            "private_key": "not_hex_zzz",
            "api_key": "k",
            "api_secret": "s",
            "api_passphrase": "p",
        },
        timeout=15,
    )
    bad_pk_ok = r2.status_code == 400
    # Bad PEM
    r3 = requests.post(
        f"{API}/me/broker-connections",
        headers=auth_hdr(user_token),
        json={
            "venue": "kalshi",
            "key_id": "x",
            "private_key_pem": "not a pem",
        },
        timeout=15,
    )
    bad_pem_ok = r3.status_code == 400
    # Unknown venue
    r4 = requests.post(
        f"{API}/me/broker-connections",
        headers=auth_hdr(user_token),
        json={"venue": "bogus", "private_key": "0x" + "a" * 64},
        timeout=15,
    )
    bad_venue_ok = r4.status_code == 400
    ok = miss_ok and bad_pk_ok and bad_pem_ok and bad_venue_ok
    record(
        "broker_validation_400s",
        ok,
        f"missing={r.status_code}, bad_pk={r2.status_code}, bad_pem={r3.status_code}, bad_venue={r4.status_code}",
    )


# ── 5. POST + GET + leak check ──
def t_broker_post_get_leak():
    # Save polymarket creds
    r = requests.post(
        f"{API}/me/broker-connections",
        headers=auth_hdr(user_token),
        json={
            "venue": "polymarket",
            "private_key": POLY_PRIVATE_KEY,
            "api_key": POLY_API_KEY,
            "api_secret": POLY_API_SECRET,
            "api_passphrase": POLY_API_PASS,
        },
        timeout=15,
    )
    if r.status_code != 200:
        record("broker_post_polymarket", False, f"http {r.status_code}: {r.text[:160]}")
        return
    body = r.json()
    post_text = r.text
    leak = (
        POLY_PRIVATE_KEY in post_text
        or POLY_API_SECRET in post_text
        or POLY_API_PASS in post_text
        or POLY_API_KEY in post_text
        or "encrypted_payload" in post_text
    )
    record(
        "broker_post_polymarket",
        body.get("venue") == "polymarket"
        and body.get("status") == "untested"
        and body.get("last_tested_at") is None
        and "fingerprint" in body
        and not leak,
        f"body keys={list(body.keys())}, leak={leak}",
    )

    # Save kalshi creds
    r2 = requests.post(
        f"{API}/me/broker-connections",
        headers=auth_hdr(user_token),
        json={
            "venue": "kalshi",
            "key_id": KALSHI_KEY_ID,
            "private_key_pem": KALSHI_PEM,
        },
        timeout=15,
    )
    kal_ok = r2.status_code == 200 and r2.json().get("venue") == "kalshi"
    kal_leak = KALSHI_PEM[:60] in r2.text or KALSHI_KEY_ID in r2.text or "encrypted_payload" in r2.text
    record("broker_post_kalshi", kal_ok and not kal_leak, f"status={r2.status_code}, leak={kal_leak}")

    # GET list — no secrets
    g = requests.get(f"{API}/me/broker-connections", headers=auth_hdr(user_token), timeout=15)
    if g.status_code != 200:
        record("broker_get_list", False, f"http {g.status_code}")
        return
    gt = g.text
    venues = {c["venue"] for c in g.json().get("connections", [])}
    list_leak = (
        POLY_PRIVATE_KEY in gt
        or POLY_API_SECRET in gt
        or POLY_API_PASS in gt
        or POLY_API_KEY in gt
        or KALSHI_PEM[:60] in gt
        or KALSHI_KEY_ID in gt
        or "encrypted_payload" in gt
    )
    record(
        "broker_get_list",
        venues == {"polymarket", "kalshi"} and not list_leak,
        f"venues={venues}, leak={list_leak}",
    )


# ── 6. Idempotent upsert (same venue overwrites, not duplicates) ──
def t_broker_idempotent_upsert():
    new_api_key = "test-key-rotated-" + uuid.uuid4().hex[:8]
    r = requests.post(
        f"{API}/me/broker-connections",
        headers=auth_hdr(user_token),
        json={
            "venue": "polymarket",
            "private_key": POLY_PRIVATE_KEY,
            "api_key": new_api_key,
            "api_secret": POLY_API_SECRET,
            "api_passphrase": POLY_API_PASS,
        },
        timeout=15,
    )
    if r.status_code != 200:
        record("broker_idempotent_upsert", False, f"http {r.status_code}")
        return
    g = requests.get(f"{API}/me/broker-connections", headers=auth_hdr(user_token), timeout=15)
    poly_rows = [c for c in g.json().get("connections", []) if c["venue"] == "polymarket"]
    record(
        "broker_idempotent_upsert",
        len(poly_rows) == 1,
        f"polymarket rows after re-POST = {len(poly_rows)}",
    )


# ── 7. /brokers/status source field ──
def t_brokers_status_source():
    r = requests.get(f"{API}/brokers/status", headers=auth_hdr(user_token), timeout=15)
    if r.status_code != 200:
        record("brokers_status_source", False, f"http {r.status_code}")
        return
    js = r.json()
    venues = js.get("venues", {})
    poly = venues.get("polymarket", {})
    kal = venues.get("kalshi", {})
    # We expect source == "user" because we just saved creds.
    ok = (
        poly.get("configured") is True
        and poly.get("source") == "user"
        and kal.get("configured") is True
        and kal.get("source") == "user"
    )
    record("brokers_status_source", ok, f"polymarket={poly}, kalshi={kal}")


# ── 8. brokers/status source=null for fresh user ──
def t_brokers_status_null_source_for_fresh_user():
    fresh_email = f"phased-fresh+{uuid.uuid4().hex[:8]}@trackmind.io"
    tok = register(fresh_email, "PhaseDPass123!")
    r = requests.get(f"{API}/brokers/status", headers=auth_hdr(tok), timeout=15)
    if r.status_code != 200:
        record("brokers_status_fresh_null", False, f"http {r.status_code}")
        return
    venues = r.json().get("venues", {})
    # In this env operator env vars are NOT set (TRACKMIND_MASTER_KEY is set but
    # POLYMARKET_* / KALSHI_* aren't). Expect source=None for both.
    poly = venues.get("polymarket", {})
    kal = venues.get("kalshi", {})
    ok = (
        poly.get("source") is None
        and poly.get("configured") is False
        and kal.get("source") is None
        and kal.get("configured") is False
    )
    record(
        "brokers_status_fresh_null",
        ok,
        f"polymarket={poly}, kalshi={kal} (assumes no env creds set)",
    )


# ── 9. test endpoint w/ payload creds (no persist) ──
def t_broker_test_payload_creds():
    # Use FRESH user (no stored creds) to validate payload-only path
    fresh_email = f"phased-testpath+{uuid.uuid4().hex[:8]}@trackmind.io"
    tok = register(fresh_email, "PhaseDPass123!")
    r = requests.post(
        f"{API}/me/broker-connections/test",
        headers=auth_hdr(tok),
        json={
            "venue": "polymarket",
            "private_key": POLY_PRIVATE_KEY,
            "api_key": POLY_API_KEY,
            "api_secret": POLY_API_SECRET,
            "api_passphrase": POLY_API_PASS,
        },
        timeout=30,
    )
    if r.status_code != 200:
        record("broker_test_payload_creds", False, f"http {r.status_code}: {r.text[:160]}")
        return
    body = r.json()
    leak = (
        POLY_PRIVATE_KEY in r.text
        or POLY_API_SECRET in r.text
        or POLY_API_PASS in r.text
    )
    ok = (
        "ok" in body
        and isinstance(body.get("ok"), bool)
        and body.get("ok") is False  # fake creds → must fail
        and isinstance(body.get("detail"), str)
        and len(body.get("detail", "")) > 0
        and not leak
    )
    record(
        "broker_test_payload_creds",
        ok,
        f"ok={body.get('ok')}, detail='{(body.get('detail') or '')[:80]}', leak={leak}",
    )


# ── 10. test endpoint w/ saved creds (no payload) ──
def t_broker_test_saved_creds():
    r = requests.post(
        f"{API}/me/broker-connections/test",
        headers=auth_hdr(user_token),
        json={"venue": "polymarket"},
        timeout=30,
    )
    if r.status_code != 200:
        record("broker_test_saved_creds", False, f"http {r.status_code}: {r.text[:160]}")
        return
    body = r.json()
    ok = body.get("ok") is False and isinstance(body.get("detail"), str)
    record(
        "broker_test_saved_creds",
        ok,
        f"ok={body.get('ok')}, detail='{(body.get('detail') or '')[:80]}'",
    )

    # Persistence side-effect: GET list should now show last_tested_at + status
    g = requests.get(f"{API}/me/broker-connections", headers=auth_hdr(user_token), timeout=15)
    rows = g.json().get("connections", [])
    poly = next((c for c in rows if c["venue"] == "polymarket"), None)
    persisted_ok = (
        poly is not None
        and poly.get("last_tested_at")
        and poly.get("status") in ("active", "failed")
    )
    record(
        "broker_test_persists_last_tested",
        bool(persisted_ok),
        f"status={poly and poly.get('status')}, last_tested_at={poly and poly.get('last_tested_at')}",
    )


# ── 11. test endpoint 404 when no stored & no payload ──
def t_broker_test_404_no_creds():
    fresh_email = f"phased-404+{uuid.uuid4().hex[:8]}@trackmind.io"
    tok = register(fresh_email, "PhaseDPass123!")
    r = requests.post(
        f"{API}/me/broker-connections/test",
        headers=auth_hdr(tok),
        json={"venue": "kalshi"},
        timeout=15,
    )
    record("broker_test_404", r.status_code == 404, f"http {r.status_code}")


# ── 12. PATCH /me/live-broker validation + DELETE clears live_broker ──
def t_live_broker_set_and_delete_clears():
    # Set live_broker = polymarket
    r = requests.patch(
        f"{API}/me/live-broker",
        headers=auth_hdr(user_token),
        json={"broker": "polymarket"},
        timeout=15,
    )
    set_ok = r.status_code == 200 and r.json().get("live_broker") == "polymarket"
    record("live_broker_set", set_ok, f"http {r.status_code}, body={r.text[:160]}")

    # DELETE the polymarket connection
    d = requests.delete(
        f"{API}/me/broker-connections/polymarket",
        headers=auth_hdr(user_token),
        timeout=15,
    )
    del_ok = d.status_code == 200 and d.json().get("ok") is True and d.json().get("deleted") == 1
    record("broker_delete_polymarket", del_ok, f"http {d.status_code}, body={d.text[:160]}")

    # /auth/me — live_broker should now be cleared
    me = requests.get(f"{API}/auth/me", headers=auth_hdr(user_token), timeout=15).json()
    cleared = (me.get("live_broker") in (None, "")) and (me.get("live_mode") in (False, None))
    record("delete_clears_live_broker", cleared, f"me.live_broker={me.get('live_broker')}, live_mode={me.get('live_mode')}")

    # Re-POST polymarket → should succeed again (round trip)
    r2 = requests.post(
        f"{API}/me/broker-connections",
        headers=auth_hdr(user_token),
        json={
            "venue": "polymarket",
            "private_key": POLY_PRIVATE_KEY,
            "api_key": POLY_API_KEY,
            "api_secret": POLY_API_SECRET,
            "api_passphrase": POLY_API_PASS,
        },
        timeout=15,
    )
    record("broker_repost_after_delete", r2.status_code == 200, f"http {r2.status_code}")


# ── 13. PATCH /me/live-broker refuses when no creds (404 / 400) ──
def t_live_broker_refuses_without_creds():
    fresh_email = f"phased-nocreds+{uuid.uuid4().hex[:8]}@trackmind.io"
    tok = register(fresh_email, "PhaseDPass123!")
    r = requests.patch(
        f"{API}/me/live-broker",
        headers=auth_hdr(tok),
        json={"broker": "polymarket"},
        timeout=15,
    )
    record(
        "live_broker_refuses_unconfigured",
        r.status_code == 400,
        f"http {r.status_code}, body={r.text[:160]}",
    )


# ── 14. risk-limits GET defaults + preview shape ──
def t_risk_limits_get():
    # Reuse the already-registered user_token (avoids /register rate limit).
    # Reset its risk limits to defaults first so the test is hermetic.
    requests.patch(
        f"{API}/me/risk-limits",
        headers=auth_hdr(user_token),
        json={"daily_loss_pct": 0.05, "max_trade_pct": 0.02},
        timeout=15,
    )
    r = requests.get(f"{API}/me/risk-limits", headers=auth_hdr(user_token), timeout=15)
    if r.status_code != 200:
        record("risk_limits_get", False, f"http {r.status_code}")
        return
    js = r.json()
    caps = js.get("caps", {})
    prev = js.get("preview", {})
    ok = (
        abs(js.get("daily_loss_pct", 0) - 0.05) < 1e-6
        and abs(js.get("max_trade_pct", 0) - 0.02) < 1e-6
        and caps.get("daily_loss_pct_max") == 0.25
        and caps.get("max_trade_pct_max") == 0.20
        and "daily_loss_dollar" in prev
        and "max_trade_dollar" in prev
        and "realized_pnl_today" in prev
        and "circuit_tripped" in prev
        and prev.get("circuit_tripped") is False  # realized_pnl_today=0 → not tripped
    )
    record("risk_limits_get", ok, f"caps={caps}, preview.circuit_tripped={prev.get('circuit_tripped')}")


def t_risk_limits_patch_out_of_bounds():
    # Reuse user_token (avoid register rate-limit).
    tok = user_token
    # daily_loss_pct=0.50 → 400
    r1 = requests.patch(
        f"{API}/me/risk-limits",
        headers=auth_hdr(tok),
        json={"daily_loss_pct": 0.50},
        timeout=15,
    )
    bad1 = r1.status_code == 400
    # max_trade_pct=0.5 → 400
    r2 = requests.patch(
        f"{API}/me/risk-limits",
        headers=auth_hdr(tok),
        json={"max_trade_pct": 0.50},
        timeout=15,
    )
    bad2 = r2.status_code == 400
    # zero → 400
    r3 = requests.patch(
        f"{API}/me/risk-limits",
        headers=auth_hdr(tok),
        json={"daily_loss_pct": 0.0},
        timeout=15,
    )
    bad3 = r3.status_code == 400
    # valid values → 200
    r4 = requests.patch(
        f"{API}/me/risk-limits",
        headers=auth_hdr(tok),
        json={"daily_loss_pct": 0.1, "max_trade_pct": 0.05},
        timeout=15,
    )
    good = r4.status_code == 200 and abs(r4.json().get("daily_loss_pct") - 0.1) < 1e-6
    record(
        "risk_limits_patch_bounds",
        bad1 and bad2 and bad3 and good,
        f"50%dl={r1.status_code}, 50%mt={r2.status_code}, 0%dl={r3.status_code}, valid={r4.status_code}",
    )


# ── 15. Per-trade cap rejects oversized trade ──
def _pick_signal_id(tok: str) -> str | None:
    # /signals returns signals with id. Get first one.
    r = requests.get(f"{API}/signals", headers=auth_hdr(tok), timeout=20)
    if r.status_code != 200:
        return None
    sigs = r.json() if isinstance(r.json(), list) else r.json().get("signals", [])
    for s in sigs:
        sid = s.get("id") or s.get("signal_id")
        if sid:
            return sid
    return None


def t_per_trade_cap_blocks():
    # Reuse user_token (default bankroll $10k, max_trade_pct=0.02 → $200 cap).
    tok = user_token
    # Set max_trade_pct=0.02 (explicit, in case prior test changed it)
    p = requests.patch(
        f"{API}/me/risk-limits",
        headers=auth_hdr(tok),
        json={"max_trade_pct": 0.02},
        timeout=15,
    )
    if p.status_code != 200:
        record("per_trade_cap_blocks", False, f"setup PATCH failed: {p.status_code}")
        return
    sid = _pick_signal_id(tok)
    if not sid:
        record("per_trade_cap_blocks", False, "no signals available to test approve")
        return
    # bankroll = $10k, cap = $200. Try notional=1000.
    r = requests.post(
        f"{API}/trade/approve",
        headers={**auth_hdr(tok), "Idempotency-Key": uuid.uuid4().hex},
        json={"signal_id": sid, "notional": 1000.0},
        timeout=20,
    )
    detail = ""
    try:
        detail = r.json().get("detail", "")
    except Exception:
        detail = r.text[:200]
    ok = r.status_code == 400 and "per-trade cap" in detail.lower()
    record("per_trade_cap_blocks", ok, f"http {r.status_code}, detail='{detail[:120]}'")


# ── 16. Daily loss circuit preview flag math when realized_pnl_today=0 ──
def t_circuit_flag_false_when_no_loss():
    tok = user_token
    # Aggressive tiny limit
    p = requests.patch(
        f"{API}/me/risk-limits",
        headers=auth_hdr(tok),
        json={"daily_loss_pct": 0.005},
        timeout=15,
    )
    if p.status_code != 200:
        record("circuit_flag_false_when_no_loss", False, f"setup PATCH failed: {p.status_code}")
        return
    g = requests.get(f"{API}/me/risk-limits", headers=auth_hdr(tok), timeout=15)
    js = g.json()
    prev = js.get("preview", {})
    # realized_pnl_today may be non-zero if the admin/user has open positions settled today.
    # We just want to verify the math: daily_loss_dollar = -starting * pct and
    # circuit_tripped == (realized_pnl_today <= daily_loss_dollar).
    rl = float(prev.get("realized_pnl_today", 0.0))
    dl = float(prev.get("daily_loss_dollar", 0.0))
    expected_tripped = rl <= dl
    # And for a fresh-ish account, daily_loss_dollar ≈ -50 (0.5% of $10k starting bankroll)
    ok = (
        prev.get("circuit_tripped") is expected_tripped
        and abs(dl - (-50.0)) < 5.0
    )
    # Restore defaults so the next test isn't biased
    requests.patch(
        f"{API}/me/risk-limits",
        headers=auth_hdr(tok),
        json={"daily_loss_pct": 0.05, "max_trade_pct": 0.02},
        timeout=15,
    )
    record(
        "circuit_flag_math",
        ok,
        f"daily_loss_dollar={dl}, realized_pnl_today={rl}, tripped={prev.get('circuit_tripped')}, expected={expected_tripped}",
    )


# ── 17. DELETE nonexistent venue returns deleted=0 ──
def t_delete_nonexistent():
    # Reuse user_token (kalshi may not be connected on user_token)
    tok = user_token
    # Ensure kalshi not connected for this user
    requests.delete(f"{API}/me/broker-connections/kalshi", headers=auth_hdr(tok), timeout=15)
    r = requests.delete(f"{API}/me/broker-connections/kalshi", headers=auth_hdr(tok), timeout=15)
    ok = r.status_code == 200 and r.json().get("deleted") == 0 and r.json().get("ok") is True
    record("delete_nonexistent_ok_zero", ok, f"http {r.status_code}, body={r.text[:120]}")


# ── 18. DELETE bad venue → 400 ──
def t_delete_bad_venue():
    r = requests.delete(f"{API}/me/broker-connections/bogus", headers=auth_hdr(user_token), timeout=15)
    record("delete_bad_venue_400", r.status_code == 400, f"http {r.status_code}")


def main() -> int:
    print(f"BASE={API}")
    t_health()
    t_admin_login()
    t_user_register()
    t_public_user_has_risk_fields()
    t_unauth_endpoints()
    t_broker_validation()
    t_broker_post_get_leak()
    t_broker_idempotent_upsert()
    t_brokers_status_source()
    t_brokers_status_null_source_for_fresh_user()
    t_broker_test_payload_creds()
    t_broker_test_saved_creds()
    t_broker_test_404_no_creds()
    t_live_broker_set_and_delete_clears()
    t_live_broker_refuses_without_creds()
    t_risk_limits_get()
    t_risk_limits_patch_out_of_bounds()
    t_per_trade_cap_blocks()
    t_circuit_flag_false_when_no_loss()
    t_delete_nonexistent()
    t_delete_bad_venue()

    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print("\n=== Summary ===")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {detail}")
    print(f"\nTotal: {n_pass} passed, {n_fail} failed (of {len(results)})")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
