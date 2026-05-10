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
