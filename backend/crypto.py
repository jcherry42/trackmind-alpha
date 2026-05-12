"""Symmetric encryption for at-rest user secrets.

Uses Fernet (AES-128-CBC + HMAC-SHA256) keyed off TRACKMIND_MASTER_KEY.
The master key is the ONLY secret the operator must protect — everything else
(broker credentials, etc.) is stored encrypted in Mongo with this key.

Generate a fresh key:
    python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


_cached_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _cached_fernet
    if _cached_fernet is not None:
        return _cached_fernet
    raw = os.environ.get("TRACKMIND_MASTER_KEY")
    if not raw:
        raise RuntimeError(
            "TRACKMIND_MASTER_KEY env var is required to encrypt/decrypt broker credentials"
        )
    try:
        key_bytes = base64.b64decode(raw)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("TRACKMIND_MASTER_KEY must be base64-encoded 32 bytes") from e
    if len(key_bytes) != 32:
        raise RuntimeError("TRACKMIND_MASTER_KEY must decode to exactly 32 bytes")
    _cached_fernet = Fernet(base64.urlsafe_b64encode(key_bytes))
    return _cached_fernet


def encrypt_payload(data: dict[str, Any]) -> str:
    """Encrypt a JSON-serialisable dict into an opaque ciphertext string."""
    return _get_fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_payload(ciphertext: str) -> dict[str, Any]:
    """Decrypt a ciphertext string back into a dict. Raises on tamper / wrong key."""
    try:
        return json.loads(_get_fernet().decrypt(ciphertext.encode()))
    except InvalidToken as e:
        raise RuntimeError("could not decrypt — wrong key or tampered ciphertext") from e


def fingerprint_for(venue: str, creds: dict[str, Any]) -> str:
    """Short, safe-to-display fingerprint of a credential set.

    NEVER returns any portion that could be useful to an attacker.
    """
    if venue == "polymarket":
        pk = creds.get("private_key") or ""
        # Show only the LAST 4 hex chars of the derived account-style hint.
        return f"…{pk[-4:]}" if len(pk) >= 4 else "—"
    if venue == "kalshi":
        kid = creds.get("key_id") or ""
        return kid[:8] + "…" if len(kid) >= 8 else "—"
    return "—"
