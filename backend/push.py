"""Expo push notification fan-out.

Stores tokens per user and ships notifications via Expo's free push service.
Resilient: chunks of 100, swallows DeviceNotRegistered, dedupes invalid tokens
on a best-effort basis.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable, List, Optional

from exponent_server_sdk import (
    DeviceNotRegisteredError,
    PushClient,
    PushMessage,
    PushServerError,
    PushTicketError,
)

logger = logging.getLogger("trackmind.push")

_client = PushClient()


def _is_expo_token(t: str) -> bool:
    return isinstance(t, str) and (t.startswith("ExponentPushToken[") or t.startswith("ExpoPushToken["))


async def send_to_tokens(
    tokens: Iterable[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> dict:
    """Send a push notification to a list of Expo tokens.

    Runs the blocking SDK in a thread so we don't block the event loop. Returns
    a small {ok, failed, invalid_tokens} summary so callers can prune dead tokens.
    """
    valid = [t for t in tokens if _is_expo_token(t)]
    if not valid:
        return {"ok": 0, "failed": 0, "invalid_tokens": []}

    def _send_chunk(chunk: List[str]) -> dict:
        ok = 0
        failed = 0
        invalid: List[str] = []
        msgs = [
            PushMessage(
                to=t,
                title=title,
                body=body,
                data=data or {},
                sound="default",
                priority="high",
                channel_id="trackmind-signals",
            )
            for t in chunk
        ]
        try:
            tickets = _client.publish_multiple(msgs)
        except PushServerError as e:
            logger.warning(f"push server error: {e}")
            return {"ok": 0, "failed": len(chunk), "invalid_tokens": []}
        except (ConnectionError, Exception) as e:  # noqa: BLE001
            logger.warning(f"push transport error: {e}")
            return {"ok": 0, "failed": len(chunk), "invalid_tokens": []}

        for token, ticket in zip(chunk, tickets):
            try:
                ticket.validate_response()
                ok += 1
            except DeviceNotRegisteredError:
                invalid.append(token)
                failed += 1
            except PushTicketError:
                failed += 1
            except Exception:  # noqa: BLE001
                failed += 1
        return {"ok": ok, "failed": failed, "invalid_tokens": invalid}

    summary = {"ok": 0, "failed": 0, "invalid_tokens": []}
    # Expo limits ~100 messages per request.
    for i in range(0, len(valid), 100):
        chunk = valid[i : i + 100]
        result = await asyncio.to_thread(_send_chunk, chunk)
        summary["ok"] += result["ok"]
        summary["failed"] += result["failed"]
        summary["invalid_tokens"].extend(result["invalid_tokens"])
    if summary["failed"] or summary["invalid_tokens"]:
        logger.info(
            "push_summary",
            extra={
                "ok": summary["ok"],
                "failed": summary["failed"],
                "invalid": len(summary["invalid_tokens"]),
            },
        )
    return summary


# ── Per-user delivery with preference gating ──────────────────────────────────
# Default notification preferences. Each user can opt-out per category.
DEFAULT_NOTIFY_PREFS: dict[str, bool] = {
    "master_signal": True,
    "position_settled": True,
    "auto_deploy": True,
    "daily_loss_tripped": True,
    "kill_switch": True,
}


def user_wants(user: dict, category: str) -> bool:
    """Return True if `user` has opted IN to `category` (default True if unset)."""
    prefs = user.get("notification_prefs") or {}
    if category in prefs:
        return bool(prefs[category])
    return DEFAULT_NOTIFY_PREFS.get(category, True)


async def push_to_user(
    db,  # Motor DB (typed loosely to avoid an import cycle)
    user: dict,
    *,
    category: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> dict:
    """Send a push to a single user IF their prefs permit `category`.

    No-ops silently when:
      - category is opted-out
      - the user has no push tokens stored
      - all tokens are filtered as invalid

    Auto-prunes tokens Expo reports as DeviceNotRegistered.
    """
    if not user_wants(user, category):
        return {"ok": 0, "failed": 0, "invalid_tokens": [], "skipped": "opted_out"}
    tokens = [t.get("token") for t in (user.get("push_tokens") or []) if isinstance(t, dict)]
    tokens = [t for t in tokens if t]
    if not tokens:
        return {"ok": 0, "failed": 0, "invalid_tokens": [], "skipped": "no_tokens"}
    payload = {**(data or {}), "category": category}
    result = await send_to_tokens(tokens, title, body, data=payload)
    bad = result.get("invalid_tokens") or []
    if bad:
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$pull": {"push_tokens": {"token": {"$in": bad}}}},
        )
    return result
