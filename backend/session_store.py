from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone

import redis

from validation_tool.backend.settings import redis_url, session_ttl_seconds


EMPTY_QUERY_STATS = {
    "total_queries_processed": 0,
    "successful_migrations": 0,
    "validated_queries": 0,
    "simple_queries": 0,
    "medium_queries": 0,
    "complex_queries": 0,
}

GLOBAL_QUERY_STATS_KEY = "query_stats:global"
GLOBAL_QUERY_STATS_HASH_KEY = "query_stats:global:v2"


def _client() -> redis.Redis:
    return redis.Redis.from_url(redis_url(), decode_responses=True)


def _normalize_query_stats(payload: dict) -> dict:
    stats = dict(EMPTY_QUERY_STATS)
    for key in stats:
        value = payload.get(key, 0)
        try:
            stats[key] = int(value)
        except (TypeError, ValueError):
            stats[key] = 0
    return stats


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


def get_query_stats(session_id: str) -> dict:
    r = _client()

    payload = r.hgetall(GLOBAL_QUERY_STATS_HASH_KEY)
    if payload:
        return _normalize_query_stats(payload)

    raw = r.get(GLOBAL_QUERY_STATS_KEY)
    if not raw:
        return dict(EMPTY_QUERY_STATS)

    try:
        legacy_payload = json.loads(raw)
    except Exception:
        return dict(EMPTY_QUERY_STATS)

    if isinstance(legacy_payload, dict):
        normalized = _normalize_query_stats(legacy_payload)
        ttl = session_ttl_seconds()
        pipe = r.pipeline()
        pipe.hset(GLOBAL_QUERY_STATS_HASH_KEY, mapping={key: str(value) for key, value in normalized.items()})
        pipe.expire(GLOBAL_QUERY_STATS_HASH_KEY, ttl)
        pipe.execute()
        return normalized

    return dict(EMPTY_QUERY_STATS)


def update_query_stats(
    session_id: str,
    *,
    migrated: bool = False,
    validated: bool = False,
    complexity_level: str | None = None,
) -> dict:
    current = get_query_stats(session_id)
    current["total_queries_processed"] += 1
    if migrated:
        current["successful_migrations"] += 1
    if validated:
        current["validated_queries"] += 1

    level = (complexity_level or "").upper()
    if level == "SIMPLE":
        current["simple_queries"] += 1
    elif level == "MEDIUM":
        current["medium_queries"] += 1
    elif level == "COMPLEX":
        current["complex_queries"] += 1

    ttl = session_ttl_seconds()
    pipe = _client().pipeline()
    pipe.hset(GLOBAL_QUERY_STATS_HASH_KEY, mapping={key: str(value) for key, value in current.items()})
    pipe.expire(GLOBAL_QUERY_STATS_HASH_KEY, ttl)
    pipe.execute()
    return current
