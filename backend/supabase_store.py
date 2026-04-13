from __future__ import annotations

import logging
import os
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# User provided publishable key can be overridden via env.
DEFAULT_SUPABASE_KEY = "sb_publishable_HzIXmmpx_rJP4K8cNMLhRg_oQY3Yt_b"
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = (os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or DEFAULT_SUPABASE_KEY).strip()
SUPABASE_TABLE = (os.getenv("SUPABASE_RESULTS_TABLE") or "validation_results").strip()


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


def _endpoint() -> str:
    return f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"


def _request(method: str, url: str, **kwargs):
    kwargs.setdefault("timeout", 20)
    resp = requests.request(method, url, **kwargs)
    if resp.status_code >= 300:
        raise RuntimeError(f"Supabase request failed {resp.status_code}: {resp.text}")
    if not resp.text:
        return None
    return resp.json()


def _to_iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Last-resort normalization for driver-specific objects.
    return str(value)


def upsert_results(rows: list[dict[str, Any]]) -> None:
    if not is_enabled() or not rows:
        return

    payload = []
    for row in rows:
        payload.append({
            "validation_id": _json_safe(row.get("validation_id")),
            "validation_ts": _to_iso(row.get("validation_ts")),
            "validation_type": _json_safe(row.get("validation_type")),
            "src_table_name": _json_safe(row.get("src_table_name") or row.get("source_table_name")),
            "tgt_table_name": _json_safe(row.get("tgt_table_name") or row.get("target_table_name")),
            "row_count": _json_safe(row.get("row_count")),
            "schema_check": _json_safe(row.get("schema_check")),
            "numeric_check": _json_safe(row.get("numeric_check")),
            "hash_validation": _json_safe(row.get("hash_validation")),
            "details": _json_safe(row.get("details") or {}),
        })

    try:
        _request(
            "POST",
            _endpoint(),
            headers=_headers("resolution=merge-duplicates,return=minimal"),
            json=payload,
        )
    except Exception as e:
        logger.error("Supabase upsert failed: %s", e)


def _date_filters(start_date: str | None, end_date: str | None) -> list[tuple[str, str]]:
    filters: list[tuple[str, str]] = []
    if start_date:
        filters.append(("validation_ts", f"gte.{start_date}T00:00:00"))
    if end_date:
        end_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
        filters.append(("validation_ts", f"lt.{end_dt.date()}T00:00:00"))
    return filters


def list_results(start_date: str | None = None, end_date: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    if not is_enabled():
        return []

    params: list[tuple[str, str]] = [
        ("select", "validation_id,validation_ts,validation_type,src_table_name,tgt_table_name,row_count,schema_check,numeric_check,hash_validation,details"),
        ("order", "validation_ts.desc"),
        ("limit", str(limit)),
    ]
    params.extend(_date_filters(start_date, end_date))
    query = urlencode(params, doseq=True)
    data = _request("GET", f"{_endpoint()}?{query}", headers=_headers()) or []

    rows = []
    for row in data:
        rows.append({
            "validation_id": row.get("validation_id"),
            "validation_ts": row.get("validation_ts"),
            "validation_type": row.get("validation_type"),
            "source_table_name": row.get("src_table_name"),
            "target_table_name": row.get("tgt_table_name"),
            "count_validation": row.get("row_count"),
            "row_count": row.get("row_count"),
            "schema_check": row.get("schema_check"),
            "numeric_check": row.get("numeric_check"),
            "hash_validation": row.get("hash_validation"),
            "details": row.get("details"),
        })
    return rows


def get_result_by_id(validation_id: str) -> dict[str, Any] | None:
    if not is_enabled():
        return None

    query = urlencode({
        "select": "validation_id,validation_ts,validation_type,src_table_name,tgt_table_name,row_count,schema_check,numeric_check,hash_validation,details",
        "validation_id": f"eq.{validation_id}",
        "limit": "1",
    })
    data = _request("GET", f"{_endpoint()}?{query}", headers=_headers()) or []
    if not data:
        return None

    row = data[0]
    return {
        "validation_id": row.get("validation_id"),
        "validation_ts": row.get("validation_ts"),
        "validation_type": row.get("validation_type"),
        "source_table_name": row.get("src_table_name"),
        "target_table_name": row.get("tgt_table_name"),
        "count_validation": row.get("row_count"),
        "row_count": row.get("row_count"),
        "schema_check": row.get("schema_check"),
        "numeric_check": row.get("numeric_check"),
        "hash_validation": row.get("hash_validation"),
        "details": row.get("details"),
    }


def dashboard_stats(start_date: str | None = None, end_date: str | None = None) -> dict[str, int]:
    rows = list_results(start_date=start_date, end_date=end_date, limit=5000)
    if not rows:
        return {
            "tables_validated": 0,
            "total_runs": 0,
            "row_count_pass": 0,
            "schema_pass": 0,
            "numeric_pass": 0,
            "row_hash_pass": 0,
            "row_count_fail": 0,
            "schema_fail": 0,
            "numeric_fail": 0,
            "row_hash_fail": 0,
        }

    table_pairs = {
        f"{r.get('source_table_name') or ''}|{r.get('target_table_name') or ''}" for r in rows
    }

    def _count(field: str, value: str) -> int:
        return sum(1 for r in rows if str(r.get(field) or "").upper() == value)

    return {
        "tables_validated": len(table_pairs),
        "total_runs": len(rows),
        "row_count_pass": _count("row_count", "PASS"),
        "schema_pass": _count("schema_check", "PASS"),
        "numeric_pass": _count("numeric_check", "PASS"),
        "row_hash_pass": _count("hash_validation", "PASS"),
        "row_count_fail": _count("row_count", "FAIL"),
        "schema_fail": _count("schema_check", "FAIL"),
        "numeric_fail": _count("numeric_check", "FAIL"),
        "row_hash_fail": _count("hash_validation", "FAIL"),
    }
