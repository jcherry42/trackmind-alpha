"""Standalone helper: derive Polymarket L2 API credentials from a Polygon private key.

Run this ON YOUR OWN MACHINE (not on the TRACKMIND server) so your private key
never leaves your computer. The output is the 4 values you paste into the
TRACKMIND app's Settings → Connect Polymarket modal.

Setup (one-time):
    python -m venv .venv && source .venv/bin/activate
    pip install py-clob-client web3

Usage:
    python derive_polymarket_keys.py

You will be prompted for your private key (input is masked). The script prints:
    private_key:     0x...
    api_key:         <uuid>
    api_secret:      <base64>
    api_passphrase:  <string>

These are the 4 fields the app needs. Nothing is sent over the network —
derive_api_key() makes a single signed REST call to Polymarket's CLOB endpoint
to register/retrieve your L2 keys.

Safety:
    - Never paste your private key into a chat, GitHub issue, or Slack.
    - Use a FRESH wallet you only use for Polymarket — never your main wallet.
    - Fund it with USDC.e (NOT USDC) + a small amount of MATIC for gas.
"""
from __future__ import annotations

import getpass
import sys


def main() -> int:
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.constants import POLYGON
    except ImportError:
        print("ERROR: py-clob-client not installed. Run: pip install py-clob-client web3")
        return 1

    print("Polymarket L2 credential derivation\n" + "=" * 40)
    pk = getpass.getpass("Polygon private key (input hidden): ").strip()
    if not pk:
        print("✗ no key entered")
        return 1
    if not pk.startswith("0x"):
        pk = "0x" + pk
    if len(pk) != 66:
        print(f"✗ expected 66 chars (0x + 64 hex), got {len(pk)}")
        return 1

    print("\n→ contacting clob.polymarket.com to derive/fetch L2 keys…")
    try:
        client = ClobClient("https://clob.polymarket.com", chain_id=POLYGON, key=pk)
        creds = client.create_or_derive_api_creds()
    except Exception as e:  # noqa: BLE001
        print(f"✗ derive_api_key failed: {e}")
        print("\nCommon causes:")
        print("  - Wallet not funded with USDC.e on Polygon (need ≥$1)")
        print("  - Network issue (check internet)")
        print("  - Wrong chain — make sure you're on Polygon, not Ethereum")
        return 1

    print("\n" + "=" * 40)
    print("PASTE THESE INTO TRACKMIND → SETTINGS → BROKER CONNECTIONS → POLYMARKET → CONNECT:")
    print("=" * 40)
    print(f"private_key:    {pk}")
    print(f"api_key:        {creds.api_key}")
    print(f"api_secret:     {creds.api_secret}")
    print(f"api_passphrase: {creds.api_passphrase}")
    print("=" * 40)
    print("\nKeep these private. Anyone with all 4 can place orders from your wallet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
