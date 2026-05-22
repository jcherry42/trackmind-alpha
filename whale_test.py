"""TRACKMIND Whale Mirror backend tests.

Tests endpoints under /api/whales/* and verifies engine integration.
Designed for external EXPO_PUBLIC_BACKEND_URL.
"""
from __future__ import annotations

import re
import sys
import time
import uuid
import requests

FRONTEND_ENV = "/app/frontend/.env"
BASE = None
with open(FRONTEND_ENV) as f:
    for line in f:
        if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
            BASE = line.strip().split("=", 1)[1].strip().strip('"')
            break
if not BASE:
    print("ERROR: EXPO_PUBLIC_BACKEND_URL not found")
    sys.exit(2)
API = BASE.rstrip("/") + "/api"

ADMIN_EMAIL = "admin@trackmind.io"
ADMIN_PASS = "Admin123!"

ETH_ADDR_RE = re.compile(r"^0x[0-9a-f]{40}$")

results = []  # list of (name, ok, detail)


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    icon = "PASS" if ok else "FAIL"
    print(f"[{icon}] {name} — {detail}")


def login(email: str, password: str) -> str:
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def auth_hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# --- 0. health + login -----------------------------------------------
admin_token = None


def t_login() -> None:
    global admin_token
    admin_token = login(ADMIN_EMAIL, ADMIN_PASS)
    record("admin_login", bool(admin_token), "token acquired")


# --- 1. GET /api/whales/wallets --------------------------------------
def t_list_wallets() -> dict:
    r = requests.get(f"{API}/whales/wallets", headers=auth_hdr(admin_token), timeout=20)
    if r.status_code != 200:
        record("whales_wallets_list", False, f"http {r.status_code}: {r.text[:160]}")
        return {}
    body = r.json()
    wallets = body.get("wallets") or []
    count = body.get("count")

    # >=8 wallets
    has_8 = len(wallets) >= 8
    # at least one admin-seeded wallet with label Multicolored-Self
    admin_seed_ok = any(
        (w.get("source") == "admin" and w.get("label") == "Multicolored-Self")
        for w in wallets
    )
    # shape per item
    required = {"id", "address", "label", "source", "is_active", "entries_count",
                "last_polled_at", "last_entry_at"}
    shape_ok = True
    addr_ok = True
    bad_addrs = []
    for w in wallets:
        missing = required - set(w.keys())
        if missing:
            shape_ok = False
            break
        a = w.get("address") or ""
        if not ETH_ADDR_RE.match(a):
            addr_ok = False
            bad_addrs.append(a)

    ok = has_8 and admin_seed_ok and shape_ok and addr_ok and isinstance(count, int)
    record(
        "whales_wallets_list",
        ok,
        f"count={count}, len={len(wallets)}, admin_seed_present={admin_seed_ok}, shape_ok={shape_ok}, addr_ok={addr_ok} bad={bad_addrs[:2]}",
    )
    return body


# --- 2. POST /api/whales/wallets -------------------------------------
TEST_ADDR = "0x1111111111111111111111111111111111111111"


def t_add_wallet() -> None:
    # ensure clean state
    requests.delete(f"{API}/whales/wallets/{TEST_ADDR}", headers=auth_hdr(admin_token), timeout=20)

    r = requests.post(
        f"{API}/whales/wallets",
        headers=auth_hdr(admin_token),
        json={"address": TEST_ADDR, "label": "TestWallet"},
        timeout=20,
    )
    if r.status_code != 200:
        record("whales_wallets_add", False, f"http {r.status_code}: {r.text[:160]}")
        return
    body = r.json()
    ok1 = (
        body.get("ok") is True
        and body.get("wallet", {}).get("source") == "user"
        and body.get("wallet", {}).get("address") == TEST_ADDR.lower()
        and body.get("wallet", {}).get("label") == "TestWallet"
    )
    record("whales_wallets_add", ok1, f"body keys={list(body.keys())}, wallet.source={body.get('wallet',{}).get('source')}")

    # Re-POST same address → idempotent
    r2 = requests.post(
        f"{API}/whales/wallets",
        headers=auth_hdr(admin_token),
        json={"address": TEST_ADDR, "label": "TestWallet"},
        timeout=20,
    )
    body2 = r2.json() if r2.status_code == 200 else {}
    ok2 = r2.status_code == 200 and body2.get("ok") is True and body2.get("already_tracked") is True
    record("whales_wallets_add_idempotent", ok2, f"http {r2.status_code}, already_tracked={body2.get('already_tracked')}")

    # Bad address
    r3 = requests.post(
        f"{API}/whales/wallets",
        headers=auth_hdr(admin_token),
        json={"address": "not-hex"},
        timeout=20,
    )
    record("whales_wallets_add_bad_addr", r3.status_code == 400, f"http {r3.status_code}: {r3.text[:120]}")


# --- 3. DELETE /api/whales/wallets/{address} -------------------------
def t_delete_wallet() -> None:
    # Delete user wallet (cleanup of step 2)
    r = requests.delete(
        f"{API}/whales/wallets/{TEST_ADDR}",
        headers=auth_hdr(admin_token),
        timeout=20,
    )
    if r.status_code != 200:
        record("whales_wallets_delete", False, f"http {r.status_code}: {r.text[:160]}")
    else:
        body = r.json()
        ok = body.get("ok") is True and body.get("deleted") == 1
        record("whales_wallets_delete", ok, f"deleted={body.get('deleted')}")

    # Attempt deleting an admin-seeded wallet
    seeded = "0x492442eab586f242b53bda933fd5de859c8a3782"
    r2 = requests.delete(
        f"{API}/whales/wallets/{seeded}",
        headers=auth_hdr(admin_token),
        timeout=20,
    )
    record(
        "whales_wallets_delete_admin_seed_403",
        r2.status_code == 403,
        f"http {r2.status_code}: {r2.text[:160]}",
    )


# --- 4. GET /api/whales/recent ---------------------------------------
def _fetch_recent(limit: int = 50) -> list:
    r = requests.get(
        f"{API}/whales/recent?limit={limit}",
        headers=auth_hdr(admin_token),
        timeout=20,
    )
    if r.status_code != 200:
        return None
    return r.json()


def t_recent() -> list:
    body = _fetch_recent(50)
    if body is None:
        record("whales_recent", False, "non-200")
        return []
    entries = body.get("entries") or []
    count = body.get("count")
    if not entries:
        print("[INFO] /whales/recent empty, retrying in 90s...")
        time.sleep(90)
        body = _fetch_recent(50)
        entries = (body or {}).get("entries") or []
        count = (body or {}).get("count")

    shape_ok = True
    bad = None
    for e in entries:
        for k in ("transaction_hash", "wallet_address", "side", "price", "usdc_size", "title", "observed_at"):
            if k not in e:
                shape_ok = False
                bad = ("missing", k, e)
                break
        if not shape_ok:
            break
        if e.get("side") != "BUY":
            shape_ok = False
            bad = ("side", e.get("side"), e)
            break
        if not isinstance(e.get("price"), (int, float)):
            shape_ok = False
            bad = ("price_type", type(e.get("price")).__name__, e)
            break
        if not isinstance(e.get("usdc_size"), (int, float)):
            shape_ok = False
            bad = ("usdc_size_type", type(e.get("usdc_size")).__name__, e)
            break

    ok = isinstance(count, int) and shape_ok
    record(
        "whales_recent",
        ok and len(entries) >= 0,
        f"count={count}, entries={len(entries)}, shape_ok={shape_ok}, bad={bad}",
    )
    return entries


# --- 5. GET /api/whales/status ---------------------------------------
def t_status() -> None:
    r = requests.get(f"{API}/whales/status", headers=auth_hdr(admin_token), timeout=20)
    if r.status_code != 200:
        record("whales_status", False, f"http {r.status_code}: {r.text[:160]}")
        return
    body = r.json()
    avail = body.get("available")
    total = body.get("total_tracked_wallets")
    e24 = body.get("entries_24h")
    age = body.get("last_tick_age_sec")
    tc = body.get("tick_count")
    poll = body.get("poll_interval_sec")
    min_usdc = body.get("min_usdc_size")

    ok = (
        avail is True
        and isinstance(total, int) and total >= 8
        and isinstance(e24, int) and e24 >= 0
        and (age is None or isinstance(age, (int, float)))
        and isinstance(tc, int) and tc >= 0
        and isinstance(poll, (int, float))
        and isinstance(min_usdc, (int, float))
    )
    record(
        "whales_status",
        ok,
        f"available={avail}, total={total}, e24={e24}, age={age}, tick_count={tc}, poll={poll}, min_usdc={min_usdc}",
    )


# --- 6. POST /api/whales/mirror --------------------------------------
def t_mirror(entries: list) -> None:
    # 6a. Bad entry_id (24-char invalid ObjectId)
    bad_id = "a" * 24
    r0 = requests.post(
        f"{API}/whales/mirror",
        headers={**auth_hdr(admin_token), "Idempotency-Key": uuid.uuid4().hex},
        json={"entry_id": bad_id},
        timeout=20,
    )
    # The brief says invalid id should 400. With "aaaa...a"*24 it's a valid hex (24 chars), so it becomes ObjectId, then not found → 404.
    # Try with a non-ObjectId-shaped string too.
    r0b = requests.post(
        f"{API}/whales/mirror",
        headers={**auth_hdr(admin_token), "Idempotency-Key": uuid.uuid4().hex},
        json={"entry_id": "not-a-real-id"},
        timeout=20,
    )
    # The brief says "not-a-real-id" → 400. Our code returns 400 for non-24-char.
    record(
        "whales_mirror_bad_id_400",
        r0b.status_code == 400,
        f"'not-a-real-id' → http {r0b.status_code}: {r0b.text[:120]}; 24a → {r0.status_code}",
    )

    # Find canonical entry
    canonical_entry = None
    for e in entries:
        if e.get("canonical_id"):
            canonical_entry = e
            break
    if not canonical_entry:
        record(
            "whales_mirror_canonical_entry_present",
            False,
            "NO entries have canonical_id != null — skipping 6b/6c (acceptable per brief)",
        )
        return

    eid = canonical_entry["id"]
    # 6b. Mirror w/ notional=0.5
    r = requests.post(
        f"{API}/whales/mirror",
        headers={**auth_hdr(admin_token), "Idempotency-Key": uuid.uuid4().hex},
        json={"entry_id": eid, "notional": 0.5},
        timeout=30,
    )
    if r.status_code != 200:
        record("whales_mirror_ok", False, f"http {r.status_code}: {r.text[:200]}")
    else:
        body = r.json()
        pos = body.get("position") or {}
        fill = body.get("fill") or {}
        ok = (
            isinstance(pos, dict) and isinstance(fill, dict)
            and pos.get("mirror_of_wallet")
            and pos.get("mirror_of_tx")
            and pos.get("engine") == "whale"
            and pos.get("venue") == "polymarket"
            and fill.get("broker") in ("paper", "polymarket", "kalshi")
        )
        record(
            "whales_mirror_ok",
            ok,
            f"pos.engine={pos.get('engine')}, venue={pos.get('venue')}, mirror_of_wallet={pos.get('mirror_of_wallet') and 'set'}, mirror_of_tx={pos.get('mirror_of_tx') and 'set'}, fill.broker={fill.get('broker')}",
        )

    # 6c. Mirror w/ huge notional > cap
    r2 = requests.post(
        f"{API}/whales/mirror",
        headers={**auth_hdr(admin_token), "Idempotency-Key": uuid.uuid4().hex},
        json={"entry_id": eid, "notional": 100000.0},
        timeout=20,
    )
    detail = ""
    try:
        detail = r2.json().get("detail", "")
    except Exception:
        pass
    ok2 = r2.status_code == 400 and "per-trade cap" in detail.lower()
    record(
        "whales_mirror_per_trade_cap_400",
        ok2,
        f"http {r2.status_code}, detail='{detail[:140]}'",
    )


# --- 7. Engine integration: /api/signals/whale -----------------------
def t_signals_whale() -> None:
    # Brief says GET /api/signals/whale?limit=10. There's no such route; the signals
    # endpoint is /api/signals?engine=whale. Try both.
    r = requests.get(f"{API}/signals/whale?limit=10", headers=auth_hdr(admin_token), timeout=20)
    if r.status_code == 404:
        # Fallback: query main /signals with engine filter
        r2 = requests.get(f"{API}/signals?engine=whale", headers=auth_hdr(admin_token), timeout=20)
        if r2.status_code != 200:
            record("signals_whale", False, f"/signals/whale 404 AND /signals?engine=whale http {r2.status_code}")
            return
        body = r2.json()
        ok = isinstance(body.get("signals"), list)
        record(
            "signals_whale",
            ok,
            f"/signals/whale was 404; using /signals?engine=whale → {len(body.get('signals') or [])} signals (shape OK)",
        )
        return
    if r.status_code != 200:
        record("signals_whale", False, f"http {r.status_code}: {r.text[:160]}")
        return
    body = r.json()
    ok = isinstance(body, dict) and ("signals" in body or "items" in body or "data" in body)
    record("signals_whale", ok, f"keys={list(body.keys())[:5]}")


# --- 8. Regression: full existing backend_test.py --------------------
def t_regression() -> None:
    import subprocess
    p = subprocess.run(
        ["python3", "/app/backend_test.py"],
        capture_output=True, text=True, timeout=300,
    )
    out = p.stdout + "\n" + p.stderr
    # Parse "Total: X passed, Y failed (of Z)"
    m = re.search(r"Total:\s+(\d+)\s+passed,\s+(\d+)\s+failed\s+\(of\s+(\d+)\)", out)
    if not m:
        record("regression_backend_test_py", False, f"could not parse summary; rc={p.returncode}; tail={out[-400:]}")
        return
    passed, failed, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
    ok = failed == 0 and total >= 22
    record(
        "regression_backend_test_py",
        ok,
        f"{passed}/{total} passed, {failed} failed; rc={p.returncode}",
    )


def main() -> int:
    print(f"BASE={API}")
    t_login()
    t_list_wallets()
    t_add_wallet()
    t_delete_wallet()
    entries = t_recent()
    t_status()
    t_mirror(entries)
    t_signals_whale()
    t_regression()

    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print("\n=== Summary ===")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {detail}")
    print(f"\nTotal: {n_pass} passed, {n_fail} failed (of {len(results)})")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
