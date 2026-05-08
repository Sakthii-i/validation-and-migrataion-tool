from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = (os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or "").strip()
SUPABASE_JOBS_TABLE = (os.getenv("SUPABASE_JOBS_TABLE") or "validation_jobs").strip()


def is_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers(prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _jobs_endpoint() -> str:
    return f"{SUPABASE_URL}/rest/v1/{SUPABASE_JOBS_TABLE}"


def _request(method: str, url: str, **kwargs):
    kwargs.setdefault("timeout", 20)
    resp = requests.request(method, url, **kwargs)
    if resp.status_code >= 300:
        raise RuntimeError(f"Supabase request failed {resp.status_code}: {resp.text}")
    if not resp.text:
        return None
    return resp.json()


def _to_iso(value: Any) -> str:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def get_pg_conn():
    """Dummy connection object for compatibility. Supabase doesn't use connection objects."""
    return None


def ensure_validation_results_table(conn=None) -> None:
    """No-op for Supabase. Table must be created manually or via migrations."""
    # Expected table: validation_results with columns:
    # - validation_id (TEXT, PRIMARY KEY)
    # - validation_ts (TIMESTAMP)
    # - src_table_name (TEXT)
    # - tgt_table_name (TEXT)
    # - validation_type (TEXT)
    # - run_by (TEXT)
    # - row_count (TEXT)
    # - schema_check (TEXT)
    # - numeric_check (TEXT)
    # - hash_validation (TEXT)
    pass


def ensure_jobs_table(conn=None) -> None:
    """No-op for Supabase. Table must be created manually or via migrations."""
    # Expected table: validation_jobs with columns:
    # - validation_id (TEXT, PRIMARY KEY)
    # - state (TEXT)
    # - overall_status (TEXT)
    # - submitted_ts (TIMESTAMP)
    # - started_ts (TIMESTAMP)
    # - completed_ts (TIMESTAMP)
    # - error_message (TEXT)
    pass


def upsert_job_state(
    conn,
    validation_id: str,
    state: str,
    overall_status: str | None = None,
    started_ts: datetime | None = None,
    completed_ts: datetime | None = None,
    error_message: str | None = None,
) -> None:
    if not is_enabled():
        return

    # First, try to get existing job
    get_url = f"{_jobs_endpoint()}?validation_id=eq.{validation_id}&select=validation_id"
    try:
        existing = _request("GET", get_url, headers=_headers()) or []
    except Exception:
        existing = []

    payload = {
        "validation_id": validation_id,
        "state": state,
        "overall_status": overall_status,
        "submitted_ts": _to_iso(datetime.now()) if not existing else None,
        "started_ts": _to_iso(started_ts),
        "completed_ts": _to_iso(completed_ts),
        "error_message": error_message,
    }

    # Remove None values to avoid overwriting
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        if existing:
            # Update existing job
            update_url = f"{_jobs_endpoint()}?validation_id=eq.{validation_id}"
            _request(
                "PATCH",
                update_url,
                headers=_headers("return=minimal"),
                json=payload,
            )
        else:
            # Insert new job
            payload["validation_id"] = validation_id
            payload["state"] = state
            payload["submitted_ts"] = _to_iso(datetime.now())
            _request(
                "POST",
                _jobs_endpoint(),
                headers=_headers("return=minimal"),
                json=payload,
            )
    except Exception as e:
        logger.error("Supabase upsert_job_state failed: %s", e)


def insert_validation_result(conn, record: dict) -> None:
    if not is_enabled():
        return

    payload = {
        "validation_id": record.get("validation_id"),
        "validation_ts": _to_iso(record.get("validation_ts")),
        "src_table_name": record.get("src_table_name"),
        "tgt_table_name": record.get("tgt_table_name"),
        "validation_type": record.get("validation_type"),
        "run_by": record.get("run_by"),
        "row_count": record.get("row_count"),
        "schema_check": record.get("schema_check"),
        "numeric_check": record.get("numeric_check"),
        "hash_validation": record.get("hash_validation"),
    }

    try:
        _request(
            "POST",
            _jobs_endpoint().replace(SUPABASE_JOBS_TABLE, "validation_results"),
            headers=_headers("resolution=merge-duplicates,return=minimal"),
            json=payload,
        )
    except Exception as e:
        logger.error("Supabase insert_validation_result failed: %s", e)


def get_job(conn, validation_id: str) -> dict | None:
    if not is_enabled():
        return None

    query = urlencode({
        "select": "*",
        "validation_id": f"eq.{validation_id}",
        "limit": "1",
    })

    try:
        data = _request("GET", f"{_jobs_endpoint()}?{query}", headers=_headers()) or []
        if data:
            return data[0]
        return None
    except Exception as e:
        logger.error("Supabase get_job failed: %s", e)
        return None


def get_result(conn, validation_id: str) -> dict | None:
    if not is_enabled():
        return None

    query = urlencode({
        "select": "*",
        "validation_id": f"eq.{validation_id}",
        "limit": "1",
    })

    try:
        results_endpoint = _jobs_endpoint().replace(SUPABASE_JOBS_TABLE, "validation_results")
        data = _request("GET", f"{results_endpoint}?{query}", headers=_headers()) or []
        if data:
            return data[0]
        return None
    except Exception as e:
        logger.error("Supabase get_result failed: %s", e)
        return None
