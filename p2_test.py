"""P2 Observability verification — /api/admin/metrics shape + /api/metrics families.

Hits external EXPO_PUBLIC_BACKEND_URL only. No code changes."""
from __future__ import annotations

import re
import sys
import time
import uuid
import json
import requests

# Load base from frontend/.env
BASE = None
with open("/app/frontend/.env") as f:
    for line in f:
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE = line.strip().split("=", 1)[1].strip().strip('"')
            break
API = BASE.rstrip("/") + "/api"

ADMIN_EMAIL = "admin@trackmind.io"
ADMIN_PASS = "Admin123!"

results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")


def login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def register(email, password, name="P2 Tester"):
    r = requests.post(f"{API}/auth/register", json={"email": email, "password": password, "name": name}, timeout=20)
    if r.status_code == 400 and "exists" in r.text.lower():
        return login(email, password)
    r.raise_for_status()
    return r.json()["access_token"]


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


# -------- Tests --------
def test_health():
    r = requests.get(f"{API}/health", timeout=10)
    record("health", r.status_code == 200, str(r.status_code))


admin_tok = None
user_tok = None


def test_login():
    global admin_tok, user_tok
    admin_tok = login(ADMIN_EMAIL, ADMIN_PASS)
    record("admin_login", bool(admin_tok), "ok")
    # Try to register, fallback to existing user login (register may be rate-limited).
    email = f"p2user+{uuid.uuid4().hex[:8]}@trackmind.io"
    try:
        user_tok = register(email, "PhaseDPass123!")
        record("user_register_or_login", bool(user_tok), f"new user {email}")
    except Exception as e:
        # Try existing test users from prior runs (known password)
        candidates = [
            "phased-nocreds+4bd28683@trackmind.io",
            "phased-404+16aeef7b@trackmind.io",
            "phased-testpath+ab966829@trackmind.io",
            "phased-fresh+3be4e383@trackmind.io",
            "phased+cf884a9f@trackmind.io",
        ]
        for c in candidates:
            try:
                user_tok = login(c, "PhaseDPass123!")
                record("user_register_or_login", True, f"login as existing {c} (register 429)")
                break
            except Exception:
                continue
        if not user_tok:
            record("user_register_or_login", False, f"register+login both failed: {e}")


def test_admin_metrics_shape():
    """Verify shape per review request."""
    r = requests.get(f"{API}/admin/metrics", headers=auth(admin_tok), timeout=20)
    if r.status_code != 200:
        record("admin_metrics_status", False, f"http {r.status_code}: {r.text[:200]}")
        return
    record("admin_metrics_status", True, "200")
    body = r.json()

    # Existing keys must still be present
    for k in ("llm", "engines", "db", "ts"):
        record(f"admin_metrics_has_{k}", k in body, f"present={k in body}")

    # live
    live = body.get("live", {})
    live_keys = ["orders_today", "orders_lifetime", "fills_today_kalshi",
                 "settlements_today", "notional_today_usd", "realized_pnl_today_usd"]
    missing = [k for k in live_keys if k not in live]
    record("admin_metrics_live_keys", not missing,
           f"keys={sorted(live.keys())}, missing={missing}")
    # type checks
    int_keys = ["orders_today", "orders_lifetime", "fills_today_kalshi", "settlements_today"]
    float_keys = ["notional_today_usd", "realized_pnl_today_usd"]
    type_fails = []
    for k in int_keys:
        v = live.get(k)
        if not isinstance(v, int):
            type_fails.append(f"{k}={v!r}({type(v).__name__})")
    for k in float_keys:
        v = live.get(k)
        if not isinstance(v, (int, float)):
            type_fails.append(f"{k}={v!r}({type(v).__name__})")
    record("admin_metrics_live_types", not type_fails, "; ".join(type_fails) or "ok")

    # pnl_7d
    pnl_7d = body.get("pnl_7d", [])
    is_list = isinstance(pnl_7d, list)
    len_ok = is_list and len(pnl_7d) == 7
    record("admin_metrics_pnl_7d_length", len_ok,
           f"type={type(pnl_7d).__name__}, len={len(pnl_7d) if is_list else 'N/A'}")
    # Each item has date,pnl,n with correct types & date format
    item_fails = []
    dates = []
    if is_list:
        for i, item in enumerate(pnl_7d):
            if not isinstance(item, dict):
                item_fails.append(f"item[{i}] not dict")
                continue
            d = item.get("date")
            if not (isinstance(d, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", d or "")):
                item_fails.append(f"item[{i}].date={d!r}")
            else:
                dates.append(d)
            if not isinstance(item.get("pnl"), (int, float)):
                item_fails.append(f"item[{i}].pnl bad type")
            if not isinstance(item.get("n"), int):
                item_fails.append(f"item[{i}].n bad type")
    record("admin_metrics_pnl_7d_items", not item_fails,
           "; ".join(item_fails[:3]) or "all 7 items shape ok")
    # Sorted oldest->newest
    sorted_ok = dates == sorted(dates)
    record("admin_metrics_pnl_7d_sorted", sorted_ok, f"dates={dates}")
    # Last entry should be today UTC
    import datetime as _dt
    today_utc = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    last_is_today = dates and dates[-1] == today_utc
    record("admin_metrics_pnl_7d_today", bool(last_is_today),
           f"last={dates[-1] if dates else 'none'}, today_utc={today_utc}")

    # exchanges
    exch = body.get("exchanges", {})
    record("admin_metrics_exchanges_keys", set(exch.keys()) >= {"polygon", "kalshi_ws"},
           f"keys={sorted(exch.keys())}")

    # polygon
    poly = exch.get("polygon", {}) or {}
    poly_required = ["available", "tick_count", "error_count", "last_tick_age_sec", "rpc_url_set"]
    poly_missing = [k for k in poly_required if k not in poly]
    record("admin_metrics_polygon_keys", not poly_missing,
           f"present={sorted(poly.keys())[:8]}, missing={poly_missing}")
    poly_type_fail = []
    if poly.get("available") is not True:
        poly_type_fail.append(f"available={poly.get('available')!r}")
    if not isinstance(poly.get("tick_count"), int):
        poly_type_fail.append(f"tick_count={poly.get('tick_count')!r}")
    if not isinstance(poly.get("error_count"), int):
        poly_type_fail.append(f"error_count={poly.get('error_count')!r}")
    lt = poly.get("last_tick_age_sec")
    if not (lt is None or isinstance(lt, (int, float))):
        poly_type_fail.append(f"last_tick_age_sec={lt!r}")
    if not isinstance(poly.get("rpc_url_set"), bool):
        poly_type_fail.append(f"rpc_url_set={poly.get('rpc_url_set')!r}")
    record("admin_metrics_polygon_types", not poly_type_fail, "; ".join(poly_type_fail) or "ok")

    # latest_block: may be null on fresh boot, retry after sleep
    latest_block = poly.get("latest_block")
    if latest_block is None:
        time.sleep(5)
        r2 = requests.get(f"{API}/admin/metrics", headers=auth(admin_tok), timeout=20)
        latest_block = (r2.json().get("exchanges", {}).get("polygon", {}) or {}).get("latest_block")
    lb_ok = latest_block is None or isinstance(latest_block, int)
    # Strict: per spec it should be int after a few seconds — but allow None graceful
    record("admin_metrics_polygon_latest_block",
           isinstance(latest_block, int),
           f"latest_block={latest_block!r} (int expected after warm-up)")

    # kalshi_ws
    kw = exch.get("kalshi_ws", {}) or {}
    kw_required = ["active_streams", "total_streams", "streams"]
    kw_missing = [k for k in kw_required if k not in kw]
    record("admin_metrics_kalshi_ws_keys", not kw_missing,
           f"present={sorted(kw.keys())}, missing={kw_missing}")
    kw_type_fail = []
    if not isinstance(kw.get("active_streams"), int):
        kw_type_fail.append(f"active_streams={kw.get('active_streams')!r}")
    if not isinstance(kw.get("total_streams"), int):
        kw_type_fail.append(f"total_streams={kw.get('total_streams')!r}")
    if not isinstance(kw.get("streams"), list):
        kw_type_fail.append(f"streams={type(kw.get('streams')).__name__}")
    record("admin_metrics_kalshi_ws_types", not kw_type_fail, "; ".join(kw_type_fail) or "ok")


def test_admin_metrics_forbids_non_admin():
    r = requests.get(f"{API}/admin/metrics", headers=auth(user_tok), timeout=15)
    record("admin_metrics_non_admin_403", r.status_code == 403,
           f"http {r.status_code}, body={r.text[:120]}")


def test_metrics_prom_endpoint():
    r = requests.get(f"{API}/metrics", timeout=20)
    if r.status_code != 200:
        record("metrics_status", False, f"http {r.status_code}")
        return
    record("metrics_status", True, "200")
    body = r.text
    new_families = [
        "tm_live_orders_placed_total",
        "tm_live_fills_received_total",
        "tm_ws_reconnects_total",
        "tm_rpc_errors_total",
        "tm_realized_pnl_today_usd",
        "tm_live_notional_today_usd",
    ]
    existing_families = [
        "tm_http_requests_total",
        "tm_llm_calls_total",
        "tm_open_positions",
    ]
    missing_new = [f for f in new_families if f not in body]
    missing_old = [f for f in existing_families if f not in body]
    record("metrics_new_families", not missing_new,
           f"missing={missing_new}" if missing_new else "all 6 new families present")
    record("metrics_regression_existing_families", not missing_old,
           f"missing={missing_old}" if missing_old else "all existing families present")


def main():
    print(f"BASE={API}")
    test_health()
    test_login()
    test_admin_metrics_shape()
    test_admin_metrics_forbids_non_admin()
    test_metrics_prom_endpoint()

    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print(f"\n=== Summary: {n_pass} pass, {n_fail} fail (of {len(results)}) ===")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {detail}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
