"""TRACKMIND master-key rotation tool.

Rotates `TRACKMIND_MASTER_KEY` by re-encrypting every row in
`broker_connections` with a new Fernet key, in place.

Modes:
  --verify          Sanity-check: decrypts every row with the CURRENT key
                    and reports any rows that fail. No writes.
  --dry-run         Generates the new key and shows what WOULD change, but
                    does not touch Mongo or .env.
  --rotate          Performs the rotation. WRITES to Mongo and to .env.
                    The backend MUST be stopped first (otherwise concurrent
                    writes would land under the old key and break).

Backup:
  Always writes a JSON backup of all broker_connections rows to
  `/tmp/broker_connections_backup_<utc-iso>.json` before mutating.

Example:
    sudo supervisorctl stop backend
    cd /app/backend && python rotate_master_key.py --verify
    cd /app/backend && python rotate_master_key.py --rotate
    sudo supervisorctl start backend
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values, load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ENV_PATH = Path(__file__).resolve().parent / ".env"
ENV_VAR = "TRACKMIND_MASTER_KEY"


def _b64_key_to_fernet(b64_str: str) -> Fernet:
    """Convert a base64-encoded 32-byte raw key into a Fernet instance."""
    raw = base64.b64decode(b64_str)
    if len(raw) != 32:
        raise SystemExit(f"key '{b64_str[:8]}…' is {len(raw)}B, not 32B")
    return Fernet(base64.urlsafe_b64encode(raw))


def _generate_new_key() -> str:
    """32 random bytes, base64-encoded — matches the format crypto.py expects."""
    return base64.b64encode(os.urandom(32)).decode()


async def _load_rows(db) -> list[dict[str, Any]]:
    return [r async for r in db.broker_connections.find({})]


def _decrypt_with(fernet: Fernet, ciphertext: str) -> dict[str, Any]:
    return json.loads(fernet.decrypt(ciphertext.encode()))


def _encrypt_with(fernet: Fernet, data: dict[str, Any]) -> str:
    return fernet.encrypt(json.dumps(data).encode()).decode()


def _backup_path() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(f"/tmp/broker_connections_backup_{ts}.json")


async def cmd_verify(db, old_fernet: Fernet) -> int:
    rows = await _load_rows(db)
    if not rows:
        print("→ no broker_connections rows. Nothing to verify.")
        return 0
    ok = bad = 0
    for r in rows:
        try:
            _decrypt_with(old_fernet, r["encrypted_payload"])
            ok += 1
        except (InvalidToken, KeyError, json.JSONDecodeError) as e:
            print(f"  ✗ {r.get('user_id', '?')}/{r.get('venue', '?')}: {e}")
            bad += 1
    print(f"→ verify: {ok} ok / {bad} bad of {len(rows)} total")
    return 0 if bad == 0 else 1


async def cmd_rotate(db, old_fernet: Fernet, new_key_b64: str, dry_run: bool) -> int:
    rows = await _load_rows(db)
    if not rows:
        print("→ no broker_connections rows. Nothing to rotate.")
        if not dry_run:
            _write_new_key_to_env(new_key_b64)
            print(f"→ wrote new {ENV_VAR} to {ENV_PATH}")
        return 0

    # 1. Always back up the ciphertexts BEFORE touching anything.
    backup = _backup_path()
    backup.write_text(json.dumps(
        [{"_id": str(r["_id"]),
          "user_id": r.get("user_id"),
          "venue": r.get("venue"),
          "encrypted_payload": r.get("encrypted_payload"),
          "fingerprint": r.get("fingerprint"),
          "updated_at": r.get("updated_at").isoformat() if r.get("updated_at") else None}
         for r in rows],
        indent=2,
    ))
    print(f"→ wrote backup ({len(rows)} rows) to {backup}")

    # 2. Decrypt all with OLD, prepare ciphertexts under NEW.
    new_fernet = _b64_key_to_fernet(new_key_b64)
    to_update: list[tuple[Any, str]] = []
    failed: list[str] = []
    for r in rows:
        try:
            plain = _decrypt_with(old_fernet, r["encrypted_payload"])
            new_ct = _encrypt_with(new_fernet, plain)
            to_update.append((r["_id"], new_ct))
        except Exception as e:  # noqa: BLE001
            failed.append(f"{r.get('user_id')}/{r.get('venue')}: {e}")

    if failed:
        print("✗ refusing to rotate — could not re-encrypt these rows:")
        for line in failed:
            print(f"    {line}")
        return 2

    print(f"→ ready to re-encrypt {len(to_update)} rows")
    if dry_run:
        print(f"→ dry-run only. New key would be: {new_key_b64}")
        return 0

    # 3. Apply atomically per row. Each $set is a single Mongo write — if we
    #    crash midway the backup file + the old key both let us recover.
    rotated_at = datetime.now(timezone.utc)
    for _id, new_ct in to_update:
        await db.broker_connections.update_one(
            {"_id": _id},
            {"$set": {
                "encrypted_payload": new_ct,
                "rotated_at": rotated_at,
                "status": "untested",   # force user to re-test after rotation
            }},
        )
    print(f"→ re-encrypted {len(to_update)} rows under the new key")

    # 4. Swap the key in .env.
    _write_new_key_to_env(new_key_b64)
    print(f"→ wrote new {ENV_VAR} to {ENV_PATH}")
    print("→ DONE. Restart the backend (`sudo supervisorctl restart backend`)")
    return 0


def _write_new_key_to_env(new_key_b64: str) -> None:
    """In-place replace the TRACKMIND_MASTER_KEY line in .env (or append it).

    Preserves all other lines & comments exactly.
    """
    if not ENV_PATH.exists():
        ENV_PATH.write_text(f'{ENV_VAR}="{new_key_b64}"\n')
        return
    lines = ENV_PATH.read_text().splitlines()
    replaced = False
    for i, line in enumerate(lines):
        s = line.lstrip()
        if s.startswith(f"{ENV_VAR}=") or s.startswith(f"export {ENV_VAR}="):
            lines[i] = f'{ENV_VAR}="{new_key_b64}"'
            replaced = True
            break
    if not replaced:
        lines.append(f'{ENV_VAR}="{new_key_b64}"')
    ENV_PATH.write_text("\n".join(lines) + "\n")


async def main_async(args: argparse.Namespace) -> int:
    # Load .env explicitly so this works whether run via supervisorctl or by hand.
    load_dotenv(ENV_PATH)
    env_vals = dotenv_values(ENV_PATH)
    old_b64 = env_vals.get(ENV_VAR) or os.environ.get(ENV_VAR)
    if not old_b64:
        print(f"✗ {ENV_VAR} not set. Nothing to rotate from.", file=sys.stderr)
        return 1
    old_fernet = _b64_key_to_fernet(old_b64)

    mongo_url = env_vals.get("MONGO_URL") or os.environ.get("MONGO_URL")
    if not mongo_url:
        print("✗ MONGO_URL not set.", file=sys.stderr)
        return 1
    db_name = (env_vals.get("DB_NAME") or os.environ.get("DB_NAME") or "trackmind")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    try:
        if args.verify:
            return await cmd_verify(db, old_fernet)
        new_key = args.new_key or _generate_new_key()
        return await cmd_rotate(db, old_fernet, new_key, dry_run=args.dry_run)
    finally:
        client.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Rotate TRACKMIND_MASTER_KEY in-place")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify", action="store_true", help="Decrypt all rows with current key; no writes")
    g.add_argument("--dry-run", action="store_true", help="Show what would change; no writes")
    g.add_argument("--rotate", action="store_true", help="Perform the rotation (writes to Mongo + .env)")
    p.add_argument("--new-key", help="Optional pre-generated base64 key. Default = random 32B.")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
