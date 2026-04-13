"""
React UI API Routes — serves as the backend for the React frontend.
These routes wrap existing backend functions for auth, dashboard, results,
connections, metadata, validation, and schema viewing.
"""
from __future__ import annotations

import json
import logging
import os
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import psycopg2
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

# ── Internal imports ──
from validation_tool.connections.postgres import POSTGRES_CONFIG
from validation_tool.connections.bigquery import connect_bigquery
from validation_tool.connections.databricks import connect_databricks
from validation_tool.connections.snowflake import connect_snowflake
from validation_tool.metadata.catalog_fetcher import get_catalogs, get_schemas, get_tables
from validation_tool.query_builder import (
    build_schema_query,
    build_shallow_query,
    build_numeric_stats_query,
    build_row_hash_query,
    build_row_hash_mismatch_rows_query_v2,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# ══════════════════════════════════════
# HELPERS
# ══════════════════════════════════════

def _pg():
    return psycopg2.connect(
        host=POSTGRES_CONFIG["host"],
        port=POSTGRES_CONFIG["port"],
        dbname=POSTGRES_CONFIG["db"],
        user=POSTGRES_CONFIG["user"],
        password=POSTGRES_CONFIG["password"],
        sslmode=POSTGRES_CONFIG.get("sslmode", "require"),
    )

def _execute(conn, query):
    cur = conn.cursor()
    cur.execute(query)
    columns = [desc[0] for desc in cur.description] if cur.description else []
    rows = cur.fetchall() or []
    cur.close()
    return [dict(zip(columns, row)) for row in rows]

DASHBOARD_TABLE = "table_validation.validation_results"
DATE_FILTER_MAP = {
    "Today": 0,
    "Past 3 days": 2,
    "Past 15 days": 14,
    "Past 30 days": 29,
}

def _date_range(date_filter, start_date=None, end_date=None):
    if date_filter == "All Time":
        return None, None
    if date_filter == "Custom":
        return start_date, end_date
    days = DATE_FILTER_MAP.get(date_filter, 29)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    return str(start), str(end)

def _where_clause(start, end):
    if not start or not end:
        return "1=1"
    return f"validation_ts >= '{start}'::date AND validation_ts <= '{end}'::date + INTERVAL '1 day'"

# ── Active connections store (in-memory, per-process) ──
_sessions = {}


def _backend_credential_password() -> str:
    return (os.getenv("CREDENTIAL_PASSWORD") or "").strip()

# ══════════════════════════════════════
# AUTH
# ══════════════════════════════════════

class LoginRequest(BaseModel):
    username: str
    password: str
    role: str = "user"

class GrantRequest(BaseModel):
    username: str
    password: str

class RevokeRequest(BaseModel):
    username: str

@router.post("/auth/login")
def auth_login(req: LoginRequest):
    from validation_tool.backend.auth_config import ADMIN_USERNAME, ADMIN_PASSWORD_HASH
    from validation_tool.backend.auth_crypto import verify_password
    from validation_tool.backend.auth_store import get_pg_conn, get_password_hash

    if req.role == "admin":
        if req.username.strip() != ADMIN_USERNAME:
            raise HTTPException(status_code=401, detail="Invalid admin credentials")
        if not verify_password(req.password, ADMIN_PASSWORD_HASH):
            raise HTTPException(status_code=401, detail="Invalid admin credentials")
        token = f"admin-{uuid.uuid4().hex[:16]}"
        return {"role": "admin", "token": token, "username": req.username}
    else:
        try:
            conn = get_pg_conn()
            stored = get_password_hash(conn, req.username.strip())
            conn.close()
        except Exception:
            raise HTTPException(status_code=500, detail="Database connection error")
        if not stored or not verify_password(req.password, stored):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = f"user-{uuid.uuid4().hex[:16]}"
        return {"role": "user", "token": token, "username": req.username}

@router.get("/auth/users")
def auth_list_users():
    from validation_tool.backend.auth_store import get_pg_conn, list_usernames
    conn = get_pg_conn()
    try:
        return {"users": list_usernames(conn)}
    finally:
        conn.close()

@router.post("/auth/grant")
def auth_grant(req: GrantRequest):
    from validation_tool.backend.auth_service import grant_user_access
    try:
        grant_user_access(req.username, req.password)
        return {"status": "ok", "message": f"Access granted to {req.username}"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/auth/revoke")
def auth_revoke(req: RevokeRequest):
    from validation_tool.backend.auth_service import revoke_user_access
    result = revoke_user_access(req.username)
    if not result:
        raise HTTPException(status_code=404, detail="User not found")
    return {"status": "ok", "message": f"Access revoked for {req.username}"}

# ══════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════

@router.get("/dashboard/stats")
def dashboard_stats(date_filter: str = "Past 30 days", start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _date_range(date_filter, start_date, end_date)
    where = _where_clause(s, e)

    query = f"""
        SELECT
            COUNT(DISTINCT COALESCE(src_table_name,'') || '|' || COALESCE(tgt_table_name,'')) AS tables_validated,
            COUNT(*) AS total_runs,
            SUM(CASE WHEN row_count = 'PASS' THEN 1 ELSE 0 END) AS row_count_pass,
            SUM(CASE WHEN schema_check = 'PASS' THEN 1 ELSE 0 END) AS schema_pass,
            SUM(CASE WHEN numeric_check = 'PASS' THEN 1 ELSE 0 END) AS numeric_pass,
            SUM(CASE WHEN hash_validation = 'PASS' THEN 1 ELSE 0 END) AS row_hash_pass,
            SUM(CASE WHEN row_count = 'FAIL' THEN 1 ELSE 0 END) AS row_count_fail,
            SUM(CASE WHEN schema_check = 'FAIL' THEN 1 ELSE 0 END) AS schema_fail,
            SUM(CASE WHEN numeric_check = 'FAIL' THEN 1 ELSE 0 END) AS numeric_fail,
            SUM(CASE WHEN hash_validation = 'FAIL' THEN 1 ELSE 0 END) AS row_hash_fail
        FROM {DASHBOARD_TABLE}
        WHERE {where}
    """

    conn = _pg()
    try:
        result = _execute(conn, query)
        if result:
            row = result[0]
            # Normalize None to 0
            return {k: (v or 0) for k, v in row.items()}
        return {}
    finally:
        conn.close()

# ══════════════════════════════════════
# RESULTS
# ══════════════════════════════════════

@router.get("/results")
def list_results(date_filter: str = "Past 30 days", start_date: Optional[str] = None, end_date: Optional[str] = None):
    s, e = _date_range(date_filter, start_date, end_date)
    where = _where_clause(s, e)

    query = f"""
        SELECT
            validation_id, validation_ts, validation_type,
            src_table_name AS source_table_name,
            tgt_table_name AS target_table_name,
            row_count AS count_validation,
            hash_validation, numeric_check, schema_check
        FROM {DASHBOARD_TABLE}
        WHERE {where}
        ORDER BY validation_ts DESC
        LIMIT 500
    """
    conn = _pg()
    try:
        rows = _execute(conn, query)
        # Convert datetimes to strings
        for row in rows:
            for k, v in row.items():
                if hasattr(v, 'isoformat'):
                    row[k] = v.isoformat()
        return {"results": rows}
    finally:
        conn.close()

@router.get("/results/{validation_id}")
def get_result_by_id(validation_id: str):
    query = f"""
        SELECT
            validation_id,
            validation_ts,
            validation_type,
            src_table_name AS source_table_name,
            tgt_table_name AS target_table_name,
            row_count AS count_validation,
            hash_validation,
            numeric_check,
            schema_check
        FROM {DASHBOARD_TABLE}
        WHERE validation_id = %s
        LIMIT 1
    """
    conn = _pg()
    try:
        cur = conn.cursor()
        cur.execute(query, (validation_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Validation ID not found")
        cols = [desc[0] for desc in cur.description]
        result = dict(zip(cols, row))
        for k, v in result.items():
            if hasattr(v, 'isoformat'):
                result[k] = v.isoformat()
        return result
    finally:
        conn.close()

# ══════════════════════════════════════
# CONNECTIONS
# ══════════════════════════════════════

class ConnectRequest(BaseModel):
    source_engine: str
    use_stored_credentials: bool = False
    file_password: str = ""
    source: dict = {}
    target: dict = {}

@router.post("/connections/connect")
def establish_connection(req: ConnectRequest):
    engine = req.source_engine.strip()
    session_id = str(uuid.uuid4())

    try:
        from validation_tool.api.auth import load_locked_credentials
        
        if req.use_stored_credentials:
            file_password = (req.file_password or "").strip() or _backend_credential_password()
            creds = load_locked_credentials(file_password)
            dbx_creds = creds["databricks"]
            target_conn = connect_databricks(
                dbx_creds["server_hostname"],
                dbx_creds["http_path"],
                dbx_creds["access_token"],
            )

            if engine == "BigQuery":
                source_conn = connect_bigquery(
                    req.source.get("project_id", ""),
                    req.source.get("bq_key_path", ""),
                    req.source.get("dataset_location", "US"),
                )
            elif engine == "Snowflake":
                sf_creds = creds["snowflake"]
                source_conn = connect_snowflake(
                    sf_creds["account"],
                    sf_creds["user"],
                    sf_creds["password"],
                    sf_creds["warehouse"],
                    sf_creds.get("role"),
                )
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported engine: {engine}")

        else:
            if engine == "BigQuery":
                source_conn = connect_bigquery(
                    req.source.get("project_id", ""),
                    req.source.get("bq_key_path", ""),
                    req.source.get("dataset_location", "US"),
                )
            elif engine == "Snowflake":
                source_conn = connect_snowflake(
                    req.source.get("account", ""),
                    req.source.get("user", ""),
                    req.source.get("password", ""),
                    req.source.get("warehouse", ""),
                    req.source.get("role"),
                )
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported engine: {engine}")

            target_conn = connect_databricks(
                req.target.get("server_hostname", ""),
                req.target.get("http_path", ""),
                req.target.get("access_token", ""),
            )

        _sessions[session_id] = {
            "engine": engine,
            "source_conn": source_conn,
            "target_conn": target_conn,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        return {"session_id": session_id, "status": "connected"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")

@router.get("/connections/status")
def connection_status():
    return {"active_sessions": len(_sessions)}

# ══════════════════════════════════════
# METADATA
# ══════════════════════════════════════

class MetadataRequest(BaseModel):
    target: str  # "source" or "target"
    catalog: Optional[str] = None
    schema_name: Optional[str] = None
    session_id: Optional[str] = None

def _get_session(session_id):
    """Get an active session or the most recent one."""
    if session_id and session_id in _sessions:
        return _sessions[session_id]
    if _sessions:
        return list(_sessions.values())[-1]
    raise HTTPException(status_code=400, detail="No active session. Please connect first.")

@router.post("/metadata/catalogs")
def get_catalogs_endpoint(req: MetadataRequest):
    sess = _get_session(req.session_id if hasattr(req, 'session_id') else None)
    engine = sess["engine"] if req.target == "source" else "Databricks"
    conn = sess["source_conn"] if req.target == "source" else sess["target_conn"]
    try:
        catalogs = get_catalogs(engine, conn)
        return {"catalogs": catalogs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/metadata/schemas")
def get_schemas_endpoint(req: MetadataRequest):
    sess = _get_session(req.session_id if hasattr(req, 'session_id') else None)
    engine = sess["engine"] if req.target == "source" else "Databricks"
    conn = sess["source_conn"] if req.target == "source" else sess["target_conn"]
    try:
        schemas = get_schemas(engine, conn, req.catalog)
        return {"schemas": schemas}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/metadata/tables")
def get_tables_endpoint(req: MetadataRequest):
    sess = _get_session(req.session_id if hasattr(req, 'session_id') else None)
    engine = sess["engine"] if req.target == "source" else "Databricks"
    conn = sess["source_conn"] if req.target == "source" else sess["target_conn"]
    try:
        tables_list = get_tables(engine, conn, req.catalog, req.schema_name)
        return {"tables": tables_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ══════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════

class RunValidationRequest(BaseModel):
    session_id: Optional[str] = None
    validation_type: str = "shallow"
    table_pairs: list = []
    settings: dict = {}

@router.post("/validate/run")
def run_validation(req: RunValidationRequest):
    sess = _get_session(req.session_id)
    engine = sess["engine"]
    source_conn = sess["source_conn"]
    target_conn = sess["target_conn"]

    from validation_tool.validation_engine import (
        parse_table_path, run_row_count, run_schema_validation,
        run_numeric_validation, run_row_hash_validation,
        run_checks_in_order, generate_validation_record,
        bool_to_status, insert_validation_result, normalize_where_input,
        fetch_schema, normalize_schema_df, normalize_datatype,
        get_numeric_columns, execute_query, normalize_result,
    )

    results_list = []
    for pair in req.table_pairs:
        src_path = pair.get("source", "")
        tgt_path = pair.get("target", "")
        src_where = normalize_where_input(pair.get("source_where", "1=1"))
        tgt_where = normalize_where_input(pair.get("target_where", "1=1"))

        src_cat, src_sch, src_tbl = parse_table_path(src_path) if isinstance(src_path, str) else (src_path.get("catalog"), src_path.get("schema"), src_path.get("table"))
        tgt_cat, tgt_sch, tgt_tbl = parse_table_path(tgt_path) if isinstance(tgt_path, str) else (tgt_path.get("catalog"), tgt_path.get("schema"), tgt_path.get("table"))

        src = {"catalog": src_cat, "schema": src_sch, "table": src_tbl}
        tgt = {"catalog": tgt_cat, "schema": tgt_sch, "table": tgt_tbl}

        settings = req.settings
        threshold = settings.get("threshold") if settings.get("useThreshold") else None
        include_ts = settings.get("includeTimestamp", True)
        case_sensitive = settings.get("caseSensitive", False)
        vtype = req.validation_type

        row_enabled = (vtype == "shallow") or settings.get("rowCount", True)
        schema_enabled = (vtype == "shallow") or settings.get("schema", True)
        numeric_enabled = (vtype == "deep") and settings.get("numeric", False)
        hash_enabled = (vtype == "deep") and settings.get("hash", False)

        checks = []

        if vtype == "shallow":
            checks.append(("Row Count Validation", lambda s=src, t=tgt: run_row_count(engine, source_conn, target_conn, s, t, threshold=threshold, source_where=src_where, target_where=tgt_where)))
            checks.append(("Schema Validation", lambda s=src, t=tgt: run_schema_validation(engine, source_conn, target_conn, s, t, case_sensitive=case_sensitive)))
        else:
            if settings.get("rowCount", True):
                checks.append(("Row Count Validation", lambda s=src, t=tgt: run_row_count(engine, source_conn, target_conn, s, t, threshold=threshold, source_where=src_where, target_where=tgt_where)))
            if settings.get("schema", True):
                checks.append(("Schema Validation", lambda s=src, t=tgt: run_schema_validation(engine, source_conn, target_conn, s, t, case_sensitive=case_sensitive)))
            if settings.get("numeric", False):
                checks.append(("Numeric Statistics Validation", lambda s=src, t=tgt: run_numeric_validation(engine, source_conn, target_conn, s, t, threshold=threshold, source_where=src_where, target_where=tgt_where)))
            if settings.get("hash", False):
                checks.append(("Row Hash Validation", lambda s=src, t=tgt: run_row_hash_validation(engine, source_conn, target_conn, s, t, include_timestamp_columns=include_ts, threshold=threshold, source_where=src_where, target_where=tgt_where)))

        details = {}

        if checks:
            results_map = run_checks_in_order(checks)

            src_schema_rows = None
            tgt_schema_rows = None

            if row_enabled:
                try:
                    src_q = build_shallow_query(
                        engine, src["catalog"], src["schema"], src["table"],
                        {"row_count": True}, where_clause=normalize_where_input(src_where),
                    )
                    tgt_q = build_shallow_query(
                        "Databricks", tgt["catalog"], tgt["schema"], tgt["table"],
                        {"row_count": True}, where_clause=normalize_where_input(tgt_where),
                    )
                    src_cnt = normalize_result(execute_query(engine, source_conn, src_q)[0]).get("row_count")
                    tgt_cnt = normalize_result(execute_query("Databricks", target_conn, tgt_q)[0]).get("row_count")
                    details["row_count"] = {
                        "source_count": int(src_cnt or 0),
                        "target_count": int(tgt_cnt or 0),
                        "difference": int(abs((src_cnt or 0) - (tgt_cnt or 0))),
                    }
                except Exception:
                    details["row_count"] = None

            if schema_enabled or numeric_enabled or hash_enabled:
                try:
                    src_schema_rows = fetch_schema(engine, source_conn, src["catalog"], src["schema"], src["table"])
                    tgt_schema_rows = fetch_schema("Databricks", target_conn, tgt["catalog"], tgt["schema"], tgt["table"])
                except Exception:
                    src_schema_rows = None
                    tgt_schema_rows = None

            if schema_enabled and src_schema_rows is not None and tgt_schema_rows is not None:
                try:
                    src_df = normalize_schema_df(src_schema_rows).rename(columns={"data_type": "source_type"})
                    tgt_df = normalize_schema_df(tgt_schema_rows).rename(columns={"data_type": "target_type"})

                    if not case_sensitive:
                        src_df["join_col"] = src_df["column_name"].str.lower()
                        tgt_df["join_col"] = tgt_df["column_name"].str.lower()
                    else:
                        src_df["join_col"] = src_df["column_name"]
                        tgt_df["join_col"] = tgt_df["column_name"]

                    cmp = src_df.merge(tgt_df, on="join_col", how="outer", suffixes=("_src", "_tgt"))

                    def check_match(row):
                        if case_sensitive:
                            names_match = row["column_name_src"] == row["column_name_tgt"]
                        else:
                            names_match = str(row["column_name_src"]).lower() == str(row["column_name_tgt"]).lower()
                        col_name = row.get("column_name_src") or row.get("join_col")
                        src_normalized = normalize_datatype(row["source_type"], col_name)
                        tgt_normalized = normalize_datatype(row["target_type"], col_name)
                        return "MATCH" if names_match and src_normalized == tgt_normalized else "NOT MATCH"

                    cmp["status"] = cmp.apply(check_match, axis=1)
                    schema_rows = []
                    for _, r in cmp.iterrows():
                        schema_rows.append({
                            "column_name_src": None if pd.isna(r.get("column_name_src")) else str(r.get("column_name_src")),
                            "column_name_tgt": None if pd.isna(r.get("column_name_tgt")) else str(r.get("column_name_tgt")),
                            "source_type": None if pd.isna(r.get("source_type")) else str(r.get("source_type")),
                            "target_type": None if pd.isna(r.get("target_type")) else str(r.get("target_type")),
                            "status": str(r.get("status")),
                        })

                    details["schema"] = {
                        "rows": schema_rows,
                        "total_columns": int(len(schema_rows)),
                        "mismatch_count": int(sum(1 for x in schema_rows if x["status"] != "MATCH")),
                    }
                except Exception:
                    details["schema"] = None

            if numeric_enabled and src_schema_rows is not None and tgt_schema_rows is not None:
                try:
                    src_numeric = get_numeric_columns(src_schema_rows)
                    tgt_numeric = get_numeric_columns(tgt_schema_rows)
                    src_numeric_map = {str(c).lower(): str(c) for c in src_numeric}
                    tgt_numeric_map = {str(c).lower(): str(c) for c in tgt_numeric}
                    common = sorted(set(src_numeric_map.keys()) & set(tgt_numeric_map.keys()))

                    def _quote_ident(q_engine: str, name: str) -> str:
                        value = str(name or "")
                        if q_engine.lower() in {"bigquery", "databricks"}:
                            return f"`{value}`"
                        # Snowflake
                        escaped = value.replace('"', '""')
                        return f'"{escaped}"'

                    def _qualify_fqn(q_engine: str, cat: str, sch: str, tbl: str) -> str:
                        if q_engine.lower() == "bigquery":
                            return f"`{cat}.{sch}.{tbl}`"
                        if q_engine.lower() == "databricks":
                            return f"{_quote_ident(q_engine, cat)}.{_quote_ident(q_engine, sch)}.{_quote_ident(q_engine, tbl)}"
                        return f'{_quote_ident(q_engine, cat)}.{_quote_ident(q_engine, sch)}.{_quote_ident(q_engine, tbl)}'

                    numeric_rows = []
                    for col_key in common:
                        src_col = src_numeric_map[col_key]
                        tgt_col = tgt_numeric_map[col_key]

                        src_q = build_numeric_stats_query(
                            engine, src["catalog"], src["schema"], src["table"], src_col,
                            where_clause=normalize_where_input(src_where),
                        )
                        tgt_q = build_numeric_stats_query(
                            "Databricks", tgt["catalog"], tgt["schema"], tgt["table"], tgt_col,
                            where_clause=normalize_where_input(tgt_where),
                        )
                        src_res = normalize_result(execute_query(engine, source_conn, src_q)[0])
                        tgt_res = normalize_result(execute_query("Databricks", target_conn, tgt_q)[0])

                        src_null_q = (
                            f"SELECT COUNT(*) AS null_count "
                            f"FROM {_qualify_fqn(engine, src['catalog'], src['schema'], src['table'])} "
                            f"WHERE {normalize_where_input(src_where)} AND {_quote_ident(engine, src_col)} IS NULL"
                        )
                        tgt_null_q = (
                            f"SELECT COUNT(*) AS null_count "
                            f"FROM {_qualify_fqn('Databricks', tgt['catalog'], tgt['schema'], tgt['table'])} "
                            f"WHERE {normalize_where_input(tgt_where)} AND {_quote_ident('Databricks', tgt_col)} IS NULL"
                        )

                        src_null = normalize_result(execute_query(engine, source_conn, src_null_q)[0]).get("null_count")
                        tgt_null = normalize_result(execute_query("Databricks", target_conn, tgt_null_q)[0]).get("null_count")

                        numeric_rows.append({
                            "column": src_col,
                            "source_null_count": int(src_null or 0),
                            "target_null_count": int(tgt_null or 0),
                            "source_min": src_res.get("min_val"),
                            "source_max": src_res.get("max_val"),
                            "source_avg": src_res.get("avg_val"),
                            "target_min": tgt_res.get("min_val"),
                            "target_max": tgt_res.get("max_val"),
                            "target_avg": tgt_res.get("avg_val"),
                        })

                    details["numeric"] = {"rows": numeric_rows}
                except Exception as e:
                    details["numeric"] = {"rows": [], "error": str(e)}

            if hash_enabled and src_schema_rows is not None and tgt_schema_rows is not None:
                try:
                    def get_hash(row):
                        for k in ["hash_value", "HASH_VALUE"]:
                            if k in row:
                                return row[k]
                        return None

                    src_map = {str(r.get("column_name", "")).lower(): r for r in src_schema_rows}
                    tgt_map = {str(r.get("column_name", "")).lower(): r for r in tgt_schema_rows}
                    common_keys = sorted(set(src_map) & set(tgt_map))

                    src_columns = []
                    tgt_columns = []
                    for k in common_keys:
                        s = src_map[k]
                        t = tgt_map[k]
                        dtype = str(s.get("data_type", "")).upper()
                        if any(x in dtype for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
                            continue
                        if not include_ts and ("TIMESTAMP" in dtype or "DATETIME" in dtype):
                            continue
                        src_columns.append({"name": s["column_name"], "type": dtype, "raw_type": s.get("data_type", "")})
                        tgt_columns.append({"name": t["column_name"], "type": dtype, "raw_type": t.get("data_type", "")})

                    src_only_hashes = []
                    tgt_only_hashes = []
                    src_only_rows = []
                    tgt_only_rows = []
                    hash_columns = []
                    if src_columns:
                        src_query = build_row_hash_query(
                            engine, src["catalog"], src["schema"], src["table"],
                            columns=src_columns, where_clause=normalize_where_input(src_where),
                        )
                        tgt_query = build_row_hash_query(
                            "Databricks", tgt["catalog"], tgt["schema"], tgt["table"],
                            columns=tgt_columns, where_clause=normalize_where_input(tgt_where),
                        )

                        src_rows = execute_query(engine, source_conn, src_query)
                        tgt_rows = execute_query("Databricks", target_conn, tgt_query)
                        src_hashes = sorted({str(h) for r in src_rows if (h := get_hash(r)) is not None})
                        tgt_hashes = sorted({str(h) for r in tgt_rows if (h := get_hash(r)) is not None})

                        tgt_set = set(tgt_hashes)
                        src_set = set(src_hashes)
                        src_only_hashes = [h for h in src_hashes if h not in tgt_set]
                        tgt_only_hashes = [h for h in tgt_hashes if h not in src_set]
                        matched_hashes = sorted(src_set & tgt_set)

                        # Keep the same included-column order used in hash signature generation.
                        hash_columns = [str(c.get("name")) for c in src_columns if c.get("name")]

                        def _rows_from_mismatch_hashes(
                            q_engine,
                            q_catalog,
                            q_schema,
                            q_table,
                            q_schema_rows,
                            mismatch_hashes,
                            q_where,
                            q_conn,
                        ):
                            if not mismatch_hashes:
                                return []

                            q = build_row_hash_mismatch_rows_query_v2(
                                q_engine,
                                q_catalog,
                                q_schema,
                                q_table,
                                schema_rows=q_schema_rows,
                                hash_values=[str(h).upper() for h in mismatch_hashes[:50] if h],
                                include_timestamp=include_ts,
                                timestamp_mode=None,
                                limit=50,
                                where_clause=normalize_where_input(q_where),
                            )
                            rows = execute_query(q_engine if q_engine.lower() != "databricks" else "Databricks", q_conn, q)
                            normalized = [normalize_result(r) for r in rows]
                            parsed_rows = []
                            for r in normalized:
                                sig = r.get("row_signature") or r.get("ROW_SIGNATURE") or ""
                                parts = str(sig).split("|") if sig is not None else []
                                item = {}
                                for i, col_name in enumerate(hash_columns):
                                    item[col_name] = parts[i] if i < len(parts) else None
                                parsed_rows.append(item)
                            return parsed_rows

                        src_only_rows = _rows_from_mismatch_hashes(
                            engine,
                            src["catalog"],
                            src["schema"],
                            src["table"],
                            src_schema_rows,
                            src_only_hashes,
                            src_where,
                            source_conn,
                        )
                        tgt_only_rows = _rows_from_mismatch_hashes(
                            "databricks",
                            tgt["catalog"],
                            tgt["schema"],
                            tgt["table"],
                            tgt_schema_rows,
                            tgt_only_hashes,
                            tgt_where,
                            target_conn,
                        )

                    details["row_hash"] = {
                        "columns": hash_columns,
                        "source_hash_count": len(src_hashes),
                        "target_hash_count": len(tgt_hashes),
                        "matched_hash_count": len(matched_hashes),
                        "source_not_in_target_count": len(src_only_hashes),
                        "target_not_in_source_count": len(tgt_only_hashes),
                        "source_not_in_target_rows": src_only_rows,
                        "target_not_in_source_rows": tgt_only_rows,
                    }
                except Exception:
                    details["row_hash"] = None

            record = generate_validation_record(
                vtype, src, tgt,
                bool_to_status(results_map.get("Row Count Validation")),
                bool_to_status(results_map.get("Schema Validation")),
                bool_to_status(results_map.get("Numeric Statistics Validation")),
                bool_to_status(results_map.get("Row Hash Validation")),
            )
            try:
                insert_validation_result(record)
            except Exception as e:
                logger.error(f"Insert failed: {e}")

            results_list.append({
                "validation_id": record.get("validation_id"),
                "validation_ts": record.get("timestamp"),
                "source_table_name": f"{src_cat}.{src_sch}.{src_tbl}",
                "target_table_name": f"{tgt_cat}.{tgt_sch}.{tgt_tbl}",
                "src_table": f"{src_cat}.{src_sch}.{src_tbl}",
                "tgt_table": f"{tgt_cat}.{tgt_sch}.{tgt_tbl}",
                "validation_type": vtype,
                "row_count": record.get("row_count", "N/A"),
                "schema_check": record.get("schema_check", "N/A"),
                "numeric_check": record.get("numeric_check", "N/A"),
                "hash_validation": record.get("hash_validation", "N/A"),
                "details": details,
            })

    return {"results": results_list}

# ══════════════════════════════════════
# CSV VALIDATION
# ══════════════════════════════════════

@router.post("/validate/csv")
async def run_csv_validation(session_id: str = Form(""), file: UploadFile = File(...)):
    sess = _get_session(session_id)
    contents = await file.read()
    df = pd.read_csv(io.BytesIO(contents), comment="#")
    # Re-use the existing CSV validation logic
    # Basic CSV validation
    required_cols = {"source_catalog", "source_schema", "source_table", "target_catalog", "target_schema", "target_table", "validation_type", "case_sensitive", "include_timestamp"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise HTTPException(status_code=400, detail=f"Missing columns: {missing_cols}")

    return {"status": "ok", "rows_processed": len(df)}

# ══════════════════════════════════════
# SCHEMA VIEWER
# ══════════════════════════════════════

class SchemaViewRequest(BaseModel):
    engine: str = "bigquery" # bigquery or snowflake
    table_path: str
    file_password: str = "" # for auto-connect

@router.post("/schema/view")
def view_schema(req: SchemaViewRequest):
    parts = req.table_path.strip().split(".")
    if req.engine.lower() == "bigquery" and len(parts) != 3:
        raise HTTPException(status_code=400, detail="BigQuery Table path must be project.dataset.table")
    if req.engine.lower() == "snowflake" and len(parts) != 3:
        raise HTTPException(status_code=400, detail="Snowflake Table path must be catalog.schema.table")

    catalog, schema, table = parts
    engine = req.engine.lower()
    
    # Try using active session connection
    conn = None
    for sess in _sessions.values():
        if sess.get("engine").lower() == engine:
            conn = sess["source_conn"]
            break

    if not conn:
        # Try automatic connection
        try:
            if engine == "bigquery":
                key_path = os.getenv("BQ_KEY_PATH", "")
                if key_path:
                    conn = connect_bigquery(catalog, key_path, "US")
            elif engine == "snowflake":
                from validation_tool.api.auth import load_locked_credentials
                file_password = (req.file_password or "").strip() or _backend_credential_password()
                creds = load_locked_credentials(file_password)
                sf_creds = creds["snowflake"]
                conn = connect_snowflake(
                    sf_creds["account"],
                    sf_creds["user"],
                    sf_creds["password"],
                    sf_creds["warehouse"],
                    sf_creds.get("role"),
                )
        except Exception as e:
            pass

    if not conn:
        raise HTTPException(status_code=400, detail=f"No active {engine} connection. Please connect first.")

    query = build_schema_query(engine, catalog, schema, table)
    try:
        from validation_tool.validation_engine import execute_query, normalize_result
        schema_raw = execute_query(engine, conn, query)
        rows = [normalize_result(r) for r in schema_raw]
        return {"columns": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")

# ══════════════════════════════════════
# CONFIG VALIDATION
# ══════════════════════════════════════

class ConfigValidationRequest(BaseModel):
    session_id: Optional[str] = None
    config: dict
    settings: dict = {}

@router.post("/validate/config")
def run_config_validation(req: ConfigValidationRequest):
    """Accept JSON config and run validations for each table entry."""
    sess = _get_session(req.session_id)
    tables = req.config.get("tables", [])
    if not tables:
        raise HTTPException(status_code=400, detail="No tables defined in config")

    # Delegate to run endpoint with converted pairs
    pairs = []
    for t in tables:
        pairs.append({
            "source": t.get("source", ""),
            "target": t.get("target", ""),
            "source_where": t.get("source_where", t.get("where", "1=1")),
            "target_where": t.get("target_where", t.get("where", "1=1")),
        })

    run_req = RunValidationRequest(
        session_id=req.session_id,
        validation_type=tables[0].get("validation_type", "shallow") if tables else "shallow",
        table_pairs=pairs,
        settings=req.settings,
    )
    return run_validation(run_req)
