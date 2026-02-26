from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

import redis

from validation_tool.backend.settings import redis_url, session_ttl_seconds


def _client() -> redis.Redis:
    return redis.Redis.from_url(redis_url(), decode_responses=True)


def create_session(payload: dict) -> tuple[str, datetime]:
    session_id = secrets.token_urlsafe(24)
    key = f"session:{session_id}"

    ttl = session_ttl_seconds()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

    r = _client()
    r.setex(key, ttl, json.dumps(payload))

    return session_id, expires_at


def get_session(session_id: str) -> dict | None:
    if not session_id:
        return None

    key = f"session:{session_id}"
    r = _client()
    raw = r.get(key)
    if not raw:
        return None

    try:
        return json.loads(raw)
    except Exception:
        return None
