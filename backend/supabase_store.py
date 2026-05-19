from __future__ import annotations

import logging
import os
import uuid
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
SUPABASE_BQ_RESULTS_TABLE = (os.getenv("SUPABASE_BQ_RESULTS_TABLE") or "validation_results_bigquery").strip()
SUPABASE_QUERY_STATS_TABLE = (os.getenv("SUPABASE_QUERY_STATS_TABLE") or "query_dashboard_stats").strip()
SUPABASE_BQ_QUERY_TABLE = (os.getenv("SUPABASE_BQ_QUERY_TABLE") or "query_history_bigquery").strip()
SUPABASE_SNOWFLAKE_QUERY_TABLE = (os.getenv("SUPABASE_SNOWFLAKE_QUERY_TABLE") or "query_history_snowflake").strip()


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


def _endpoint(table: str | None = None) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table or SUPABASE_TABLE}"


def _results_table_for_engine(source_engine: str | None) -> str:
    engine = (source_engine or "").strip().lower()
    if engine == "bigquery":
        return SUPABASE_BQ_RESULTS_TABLE
    return SUPABASE_TABLE


def _query_table_for_engine(source_engine: str | None) -> str:
    engine = (source_engine or "").strip().lower()
    return SUPABASE_BQ_QUERY_TABLE if engine == "bigquery" else SUPABASE_SNOWFLAKE_QUERY_TABLE


def make_query_id() -> str:
    return f"QRY_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6].upper()}"


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

    payloads_by_table: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        details = _json_safe(row.get("details") or {})
        if not isinstance(details, dict):
            details = {}
        row_engine = str(row.get("source_engine") or "").strip().lower()
        if row_engine:
            details["source_engine"] = row_engine

        payload = {
            "validation_id": _json_safe(row.get("validation_id")),
            "validation_ts": _to_iso(row.get("validation_ts")),
            "validation_type": _json_safe(row.get("validation_type")),
            "src_table_name": _json_safe(row.get("src_table_name") or row.get("source_table_name")),
            "tgt_table_name": _json_safe(row.get("tgt_table_name") or row.get("target_table_name")),
            "row_count": _json_safe(row.get("row_count")),
            "schema_check": _json_safe(row.get("schema_check")),
            "numeric_check": _json_safe(row.get("numeric_check")),
            "hash_validation": _json_safe(row.get("hash_validation")),
            "run_by": _json_safe(row.get("run_by")),
            "details": details,
        }
        payloads_by_table.setdefault(_results_table_for_engine(row_engine), []).append(payload)

    for table, payload in payloads_by_table.items():
        try:
            _request(
                "POST",
                _endpoint(table),
                headers=_headers("resolution=merge-duplicates,return=minimal"),
                json=payload,
            )
        except Exception as e:
            logger.error("Supabase upsert failed for %s: %s", table, e)
            if table != SUPABASE_TABLE:
                try:
                    _request(
                        "POST",
                        _endpoint(),
                        headers=_headers("resolution=merge-duplicates,return=minimal"),
                        json=payload,
                    )
                except Exception as fallback_e:
                    logger.error("Supabase fallback upsert failed: %s", fallback_e)


def _date_filters(start_date: str | None, end_date: str | None) -> list[tuple[str, str]]:
    filters: list[tuple[str, str]] = []
    if start_date:
        filters.append(("validation_ts", f"gte.{start_date}T00:00:00"))
    if end_date:
        end_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
        filters.append(("validation_ts", f"lt.{end_dt.date()}T00:00:00"))
    return filters


def _row_source_engine(row: dict[str, Any]) -> str | None:
    direct = row.get("source_engine")
    if direct is not None:
        text = str(direct).strip().lower()
        if text:
            return text

    details = row.get("details")
    if isinstance(details, dict):
        nested = details.get("source_engine")
        if nested is not None:
            text = str(nested).strip().lower()
            if text:
                return text
    return None


def _matches_source_engine(row: dict[str, Any], source_engine: str | None) -> bool:
    if not source_engine:
        return True

    wanted = source_engine.strip().lower()
    row_engine = _row_source_engine(row)
    if row_engine:
        return row_engine == wanted

    # Legacy rows may not carry source-engine metadata.
    # Keep these visible in Snowflake view (historical default), but hide in BigQuery view.
    return wanted == "snowflake"


def list_results(
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 500,
    source_engine: str | None = None,
) -> list[dict[str, Any]]:
    if not is_enabled():
        return []

    table = _results_table_for_engine(source_engine)
    params: list[tuple[str, str]] = [
        ("select", "validation_id,validation_ts,validation_type,src_table_name,tgt_table_name,row_count,schema_check,numeric_check,hash_validation,run_by,details"),
        ("order", "validation_ts.desc"),
        ("limit", str(limit)),
    ]
    params.extend(_date_filters(start_date, end_date))
    query = urlencode(params, doseq=True)
    try:
        data = _request("GET", f"{_endpoint(table)}?{query}", headers=_headers()) or []
    except Exception as e:
        logger.error("Supabase list failed for %s: %s", table, e)
        if table == SUPABASE_TABLE:
            return []
        data = _request("GET", f"{_endpoint()}?{query}", headers=_headers()) or []

    rows = []
    for row in data:
        normalized = {
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
            "run_by": row.get("run_by"),
            "details": row.get("details"),
            "source_engine": _row_source_engine(row),
        }
        if _matches_source_engine(normalized, source_engine):
            rows.append(normalized)
    return rows


def get_result_by_id(validation_id: str) -> dict[str, Any] | None:
    if not is_enabled():
        return None

    query = urlencode({
        "select": "validation_id,validation_ts,validation_type,src_table_name,tgt_table_name,row_count,schema_check,numeric_check,hash_validation,run_by,details",
        "validation_id": f"eq.{validation_id}",
        "limit": "1",
    })
    data = []
    for table in (SUPABASE_BQ_RESULTS_TABLE, SUPABASE_TABLE):
        try:
            data = _request("GET", f"{_endpoint(table)}?{query}", headers=_headers()) or []
        except Exception as e:
            logger.error("Supabase get failed for %s: %s", table, e)
            continue
        if data:
            break
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
        "run_by": row.get("run_by"),
        "details": row.get("details"),
        "source_engine": _row_source_engine(row),
    }


def get_query_stats(source_engine: str | None = None) -> dict[str, int]:
    empty = {
        "total_queries_processed": 0,
        "successful_migrations": 0,
        "validated_queries": 0,
        "simple_queries": 0,
        "medium_queries": 0,
        "complex_queries": 0,
    }
    if not is_enabled():
        return empty

    engine = (source_engine or "global").strip().lower() or "global"
    query = urlencode({
        "select": "total_queries_processed,successful_migrations,validated_queries,simple_queries,medium_queries,complex_queries",
        "source_engine": f"eq.{engine}",
        "limit": "1",
    })
    try:
        data = _request("GET", f"{_endpoint(SUPABASE_QUERY_STATS_TABLE)}?{query}", headers=_headers()) or []
    except Exception as e:
        logger.error("Supabase query stats get failed: %s", e)
        return empty
    if not data:
        return empty
    row = data[0]
    return {key: int(row.get(key) or 0) for key in empty}


def upsert_query_stats(source_engine: str | None, stats: dict[str, int]) -> None:
    if not is_enabled():
        return

    engine = (source_engine or "global").strip().lower() or "global"
    payload = {
        "source_engine": engine,
        "total_queries_processed": int(stats.get("total_queries_processed") or 0),
        "successful_migrations": int(stats.get("successful_migrations") or 0),
        "validated_queries": int(stats.get("validated_queries") or 0),
        "simple_queries": int(stats.get("simple_queries") or 0),
        "medium_queries": int(stats.get("medium_queries") or 0),
        "complex_queries": int(stats.get("complex_queries") or 0),
        "updated_at": datetime.utcnow().isoformat(),
    }
    try:
        _request(
            "POST",
            _endpoint(SUPABASE_QUERY_STATS_TABLE),
            headers=_headers("resolution=merge-duplicates,return=minimal"),
            json=payload,
        )
    except Exception as e:
        logger.error("Supabase query stats upsert failed: %s", e)


def upsert_query_history(row: dict[str, Any]) -> None:
    if not is_enabled():
        return

    details = _json_safe(row.get("details") or {})
    if not isinstance(details, dict):
        details = {}
    source_engine = str(row.get("source_engine") or "").strip().lower()
    payload = {
        "query_id": _json_safe(row.get("query_id")),
        "query_name": _json_safe(row.get("query_name")),
        "source_engine": source_engine,
        "run_by": _json_safe(row.get("run_by")),
        "last_ran_ts": _to_iso(row.get("last_ran_ts") or datetime.utcnow()),
        "source_latency_ms": _json_safe(row.get("source_latency_ms")),
        "target_latency_ms": _json_safe(row.get("target_latency_ms")),
        "migration_mode": _json_safe(row.get("migration_mode")),
        "validation_status": _json_safe(row.get("validation_status") or "NOT RUN"),
        "pushed_to_git": bool(row.get("pushed_to_git") or False),
        "source_sql": _json_safe(row.get("source_sql")),
        "translated_sql": _json_safe(row.get("translated_sql")),
        "details": details,
    }
    try:
        _request(
            "POST",
            _endpoint(_query_table_for_engine(source_engine)),
            headers=_headers("resolution=merge-duplicates,return=minimal"),
            json=payload,
        )
    except Exception as e:
        logger.error("Supabase query history upsert failed: %s", e)


def update_query_history(query_id: str, source_engine: str | None, updates: dict[str, Any]) -> None:
    if not is_enabled() or not query_id:
        return
    payload = {k: _json_safe(v) for k, v in updates.items() if v is not None}
    if not payload:
        return
    try:
        _request(
            "PATCH",
            f"{_endpoint(_query_table_for_engine(source_engine))}?query_id=eq.{query_id}",
            headers=_headers("return=minimal"),
            json=payload,
        )
    except Exception as e:
        logger.error("Supabase query history update failed: %s", e)


def list_query_history(source_engine: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    if not is_enabled():
        return []
    table = _query_table_for_engine(source_engine)
    query = urlencode({
        "select": "query_id,query_name,source_engine,run_by,last_ran_ts,source_latency_ms,target_latency_ms,migration_mode,validation_status,pushed_to_git,reviewers,source_sql,translated_sql,details",
        "order": "last_ran_ts.desc",
        "limit": str(limit),
    })
    try:
        return _request("GET", f"{_endpoint(table)}?{query}", headers=_headers()) or []
    except Exception as e:
        logger.error("Supabase query history list failed: %s", e)
        return []


def dashboard_stats(
    start_date: str | None = None,
    end_date: str | None = None,
    source_engine: str | None = None,
) -> dict[str, int]:
    rows = list_results(start_date=start_date, end_date=end_date, limit=5000, source_engine=source_engine)
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
