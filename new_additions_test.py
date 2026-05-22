"""Verify 3 new TRACKMIND backend additions:
   (1) Sports market ingest -> /api/markets has >=80 quotes with sports-looking polymarket titles.
   (2) Curated wallet seed   -> /api/whales/wallets returns 10 active (admin), include_retired=17 (7 retired).
   (3) Signal aliases        -> /api/signals/{whale,alpha,quant} with limit clamp.
"""
from __future__ import annotations
import re
import sys
import requests

with open("/app/frontend/.env") as f:
    BASE = next(line.split("=", 1)[1].strip().strip('"')
                for line in f if line.startswith("EXPO_PUBLIC_BACKEND_URL="))
API = BASE.rstrip("/") + "/api"
ADMIN_EMAIL = "admin@trackmind.io"
ADMIN_PASS = "Admin123!"


def login(email, password):
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def hdr(t):
    return {"Authorization": f"Bearer {t}"}


results = []


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")


def main():
    print(f"BASE={API}")
    tok = login(ADMIN_EMAIL, ADMIN_PASS)

    # ============== 1) Sports Market Ingest ==============
    r = requests.get(f"{API}/markets", headers=hdr(tok), timeout=30)
    if r.status_code != 200:
        record("markets_count_>=80", False, f"http {r.status_code}: {r.text[:160]}")
    else:
        quotes = r.json().get("quotes", [])
        n = len(quotes)
        record("markets_count_>=80", n >= 80, f"got {n} quotes")

        # Match sports-looking polymarket titles
        sports_terms = [
            "spread:", " vs.", "vs ", "points o/u", "moneyline",
            "knicks", "lakers", "thunder", "cavaliers",
            "yankees", "dodgers", "patriots", "chiefs",
            "hurricanes", "canadiens",
        ]
        sports_matches = []
        for q in quotes:
            if q.get("venue") != "polymarket":
                continue
            title = (q.get("title") or q.get("question") or "").lower()
            for term in sports_terms:
                if term in title:
                    sports_matches.append(title[:90])
                    break
        record(
            "sports_polymarket_>=5",
            len(sports_matches) >= 5,
            f"matched {len(sports_matches)} sports-looking polymarket titles. Samples: "
            + " | ".join(sports_matches[:6]),
        )

    # ============== 2) Curated Wallet Seed ==============
    r = requests.get(f"{API}/whales/wallets", headers=hdr(tok), timeout=20)
    if r.status_code != 200:
        record("whales_active_10", False, f"http {r.status_code}: {r.text[:160]}")
    else:
        wallets = r.json().get("wallets", [])
        n = len(wallets)
        record("whales_active_10", n == 10, f"got {n} active wallets (expected 10)")

        all_admin_active = all(w.get("source") == "admin" and w.get("is_active") is True for w in wallets)
        record(
            "all_admin_active",
            all_admin_active,
            f"all 10 wallets source=admin & is_active=true: {all_admin_active}",
        )

        expected_active = {
            "tubeyou", "Multicolored-Self", "swisstony", "restart77", "RN1",
            "mooseborzoi", "LaBradfordSmith22", "bcda", "VPenguin", "Whale-F705",
        }
        labels = {w.get("label") for w in wallets}
        missing_active = expected_active - labels
        record(
            "active_labels_present",
            not missing_active,
            f"missing labels={missing_active}. Got labels={labels}",
        )

    # include_retired=true
    r = requests.get(f"{API}/whales/wallets?include_retired=true", headers=hdr(tok), timeout=20)
    if r.status_code != 200:
        record("whales_total_17_with_retired", False, f"http {r.status_code}: {r.text[:160]}")
    else:
        wallets = r.json().get("wallets", [])
        n = len(wallets)
        retired = [w for w in wallets if w.get("is_active") is False]
        record(
            "whales_total_17_with_retired",
            n == 17,
            f"got {n} total (expected 17), retired count={len(retired)}",
        )
        record(
            "retired_count_7",
            len(retired) == 7,
            f"got {len(retired)} retired (is_active=false) wallets (expected 7)",
        )

        expected_retired = {
            "Whale-2C53", "Polywhale-9D7B", "RiskManager-F424", "Atlas-A28E",
            "Sentinel-FCAE", "Ironclad-46F4", "Specter-F99C",
        }
        retired_labels = {w.get("label") for w in retired}
        missing_retired = expected_retired - retired_labels
        record(
            "retired_labels_present",
            not missing_retired,
            f"missing retired labels={missing_retired}. Got retired labels={retired_labels}",
        )

    # ============== 3) Signal Aliases ==============
    for engine in ("whale", "alpha", "quant"):
        r = requests.get(f"{API}/signals/{engine}?limit=5", headers=hdr(tok), timeout=20)
        if r.status_code != 200:
            record(f"signals_{engine}_basic", False, f"http {r.status_code}: {r.text[:160]}")
            continue
        body = r.json()
        sigs = body.get("signals", [])
        count = body.get("count")
        shape_ok = isinstance(sigs, list) and isinstance(count, int)
        engine_ok = all(s.get("engine") == engine for s in sigs)
        record(
            f"signals_{engine}_basic",
            shape_ok and engine_ok,
            f"http 200, signals.count={len(sigs)}, count_field={count}, engine_ok={engine_ok}",
        )

    # limit=999 -> 200 (clamped)
    r = requests.get(f"{API}/signals/whale?limit=999", headers=hdr(tok), timeout=20)
    record(
        "signals_whale_limit_999_clamped",
        r.status_code == 200,
        f"http {r.status_code} (must be 200, not 422)",
    )

    # limit=0 -> 200 (clamped to 1)
    r = requests.get(f"{API}/signals/whale?limit=0", headers=hdr(tok), timeout=20)
    record(
        "signals_whale_limit_0_clamped",
        r.status_code == 200,
        f"http {r.status_code} (must be 200)",
    )

    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    print("\n=== Summary ===")
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  — {detail}")
    print(f"\nTotal: {n_pass} passed, {n_fail} failed")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
