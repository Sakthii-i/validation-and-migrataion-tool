from __future__ import annotations

from datetime import datetime, timezone

import redis

from validation_tool.backend.supabase_jobs_store import (
    ensure_jobs_table,
    get_pg_conn,
    insert_validation_result,
    upsert_job_state,
)
from validation_tool.backend.settings import (
    max_concurrent_jobs_per_session,
    redis_url,
    session_concurrency_slot_ttl_seconds,
)
from validation_tool.backend.session_store import get_session
from validation_tool.backend.validators import run_validation_job


class SessionConcurrencyLimit(Exception):
    pass


_ACQUIRE_SLOT_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local current = tonumber(redis.call('get', key) or '0')
if current >= limit then
  return 0
end
current = redis.call('incr', key)
if ttl and ttl > 0 then
  redis.call('expire', key, ttl)
end
return 1
"""


_RELEASE_SLOT_LUA = """
local key = KEYS[1]
local current = tonumber(redis.call('get', key) or '0')
if current <= 1 then
  redis.call('del', key)
  return 1
end
redis.call('decr', key)
return 1
"""


def _session_slot_key(session_id: str) -> str:
    return f"validation:session:{session_id}:running"


def _try_acquire_session_slot(conn: redis.Redis, session_id: str) -> bool:
    limit = max_concurrent_jobs_per_session()
    if limit <= 0:
        return True

    ttl = session_concurrency_slot_ttl_seconds()
    res = conn.eval(_ACQUIRE_SLOT_LUA, 1, _session_slot_key(session_id), str(limit), str(ttl))
    return bool(res)


def _release_session_slot(conn: redis.Redis, session_id: str) -> None:
    try:
        conn.eval(_RELEASE_SLOT_LUA, 1, _session_slot_key(session_id))
    except Exception:
        # Best-effort: TTL is the safety net.
        pass


def run_validation_task(validation_id: str, session_id: str, row: dict) -> None:
    pg = get_pg_conn()
    redis_conn = redis.Redis.from_url(redis_url())
    slot_acquired = False
    try:
        ensure_jobs_table(pg)

        slot_acquired = _try_acquire_session_slot(redis_conn, session_id)
        if not slot_acquired:
            # Keep job in PENDING state; RQ retry will requeue later.
            upsert_job_state(pg, validation_id, state="PENDING")
            raise SessionConcurrencyLimit("Session is at concurrency limit")

        upsert_job_state(pg, validation_id, state="RUNNING", started_ts=datetime.now(timezone.utc))

        session_payload = get_session(session_id)
        if not session_payload:
            upsert_job_state(
                pg,
                validation_id,
                state="FAILED",
                overall_status="ERROR",
                completed_ts=datetime.now(timezone.utc),
                error_message="Session expired or not found",
            )
            return

        row = dict(row or {})
        row["validation_id"] = validation_id

        record = run_validation_job(session_payload, row)
        insert_validation_result(pg, record)

        upsert_job_state(
            pg,
            validation_id,
            state="SUCCEEDED",
            overall_status=record.get("overall_status"),
            completed_ts=datetime.now(timezone.utc),
        )

    except SessionConcurrencyLimit:
        raise
    except Exception as e:
        upsert_job_state(
            pg,
            validation_id,
            state="FAILED",
            overall_status="ERROR",
            completed_ts=datetime.now(timezone.utc),
            error_message=str(e),
        )
        raise
    finally:
        if slot_acquired:
            _release_session_slot(redis_conn, session_id)
        try:
            pg.close()
        except Exception:
            pass
