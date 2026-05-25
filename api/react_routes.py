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
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

# ── Internal imports ──
from validation_tool.connections.bigquery import connect_bigquery
from validation_tool.connections.databricks import connect_databricks
from validation_tool.connections.snowflake import connect_snowflake
from validation_tool.metadata.catalog_fetcher import get_catalogs, get_schemas, get_tables
from validation_tool.query_builder import (
    build_schema_query,
    build_shallow_query,
    build_numeric_stats_query,
    build_row_hash_query,
    build_categorical_hash_query,
    build_categorical_hash_samples_query,
    build_row_hash_mismatch_rows_query_v2,
)
from validation_tool.backend import supabase_store
from validation_tool.backend.session_store import update_query_stats
from validation_tool.migration.sql_processor import SQLPreprocessor

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_BQ_TEMP_DATASET = (os.getenv("BQ_TEMP_DATASET") or "validation_tool_tmp").strip()
_BQ_TEMP_TTL_HOURS = int(os.getenv("BQ_TEMP_TTL_HOURS") or "6")
_DBX_TEMP_CATALOG = (os.getenv("DATABRICKS_TEMP_CATALOG") or "").strip()
_DBX_TEMP_SCHEMA = (os.getenv("DATABRICKS_TEMP_SCHEMA") or "").strip()

# ══════════════════════════════════════
# HELPERS
# ══════════════════════════════════════

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


def _row_count_for_table(engine: str, conn, catalog: str, schema: str, table: str) -> int:
    from validation_tool.validation_engine import execute_query, normalize_result

    query = build_shallow_query(engine, catalog, schema, table, {"row_count": True})
    rows = execute_query(engine, conn, query)
    if not rows:
        return 0
    return int(normalize_result(rows[0]).get("row_count") or 0)


def _table_fqn(catalog: str, schema: str, table: str) -> str:
    return ".".join([str(catalog).strip(), str(schema).strip(), str(table).strip()]).strip(".")


def _normalize_categorical_columns(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = str(value).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def _enforce_source_engine_match(source_engine: str, sql: str) -> None:
    normalized_engine = (source_engine or "").strip().lower()
    if normalized_engine not in ("bigquery", "snowflake"):
        raise HTTPException(status_code=400, detail="source_engine must be bigquery or snowflake")

    detected = SQLPreprocessor.detect_source_engine(sql)
    expected = "Snowflake" if normalized_engine == "snowflake" else "BigQuery"
    if detected in ("unknown", "ambiguous"):
        return
    if detected != normalized_engine:
        actual = "Snowflake" if detected == "snowflake" else "BigQuery"
        raise HTTPException(
            status_code=400,
            detail=f"Selected source engine is {expected}, but the SQL looks like {actual}.",
        )

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
    from validation_tool.backend.supabase_auth_store import get_password_hash

    if req.role == "admin":
        if req.username.strip() != ADMIN_USERNAME:
            raise HTTPException(status_code=401, detail="Invalid admin credentials")
        if not verify_password(req.password, ADMIN_PASSWORD_HASH):
            raise HTTPException(status_code=401, detail="Invalid admin credentials")
        token = f"admin-{uuid.uuid4().hex[:16]}"
        return {"role": "admin", "token": token, "username": req.username}
    else:
        try:
            stored = get_password_hash(None, req.username.strip())
        except Exception:
            raise HTTPException(status_code=500, detail="Database connection error")
        if not stored or not verify_password(req.password, stored):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = f"user-{uuid.uuid4().hex[:16]}"
        return {"role": "user", "token": token, "username": req.username}

@router.get("/auth/users")
def auth_list_users():
    from validation_tool.backend.supabase_auth_store import list_usernames
    try:
        return {"users": list_usernames(None)}
    except Exception:
        return {"users": []}

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
def dashboard_stats(
    date_filter: str = "Past 30 days",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source_engine: Optional[str] = None,
):
    s, e = _date_range(date_filter, start_date, end_date)
    engine_filter = (source_engine or "").strip().lower() or None
    
    try:
        stats = supabase_store.dashboard_stats(start_date=s, end_date=e, source_engine=engine_filter)
        return stats
    except Exception as ex:
        logger.error("Dashboard stats error: %s", ex)
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

# ══════════════════════════════════════
# RESULTS
# ══════════════════════════════════════

@router.get("/results")
def list_results(
    date_filter: str = "Past 30 days",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source_engine: Optional[str] = None,
):
    s, e = _date_range(date_filter, start_date, end_date)
    engine_filter = (source_engine or "").strip().lower() or None
    
    try:
        rows = supabase_store.list_results(start_date=s, end_date=e, limit=500, source_engine=engine_filter)
        return {"results": rows}
    except Exception as ex:
        logger.error("List results error: %s", ex)
        return {"results": []}

@router.get("/results/{validation_id}")
def get_result_by_id(validation_id: str):
    if not supabase_store.is_enabled():
        raise HTTPException(status_code=503, detail="Supabase is not configured for validation details")

    try:
        row = supabase_store.get_result_by_id(validation_id)
        if row:
            return row
        raise HTTPException(status_code=404, detail="Validation ID not found")
    except HTTPException:
        raise
    except Exception as ex:
        logger.error("Get result error: %s", ex)
        raise HTTPException(status_code=500, detail="Failed to retrieve validation details")

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
                project_id = (
                    req.source.get("project_id")
                    or os.getenv("BQ_PROJECT_ID")
                    or os.getenv("GOOGLE_CLOUD_PROJECT")
                    or ""
                )
                key_path = req.source.get("bq_key_path") or os.getenv("BQ_KEY_PATH") or ""
                dataset_location = req.source.get("dataset_location") or os.getenv("BQ_DATASET_LOCATION") or "US"
                source_conn = connect_bigquery(
                    project_id,
                    key_path,
                    dataset_location,
                )
            elif engine == "Snowflake":
                sf_creds = creds["snowflake"]
                source_conn = connect_snowflake(
                    sf_creds["account"],
                    sf_creds["user"],
                    sf_creds["password"],
                    sf_creds["warehouse"],
                    sf_creds.get("role"),
                    sf_creds.get("database") or os.getenv("SNOWFLAKE_DATABASE"),
                    sf_creds.get("schema") or os.getenv("SNOWFLAKE_SCHEMA"),
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
                    req.source.get("database") or os.getenv("SNOWFLAKE_DATABASE"),
                    req.source.get("schema") or os.getenv("SNOWFLAKE_SCHEMA"),
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
            "bq_dataset_location": (
                (req.source.get("dataset_location") if isinstance(req.source, dict) else None)
                or os.getenv("BQ_DATASET_LOCATION")
                or "US"
            ),
            "temp_objects": [],
        }

        return {"session_id": session_id, "status": "connected"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")

@router.get("/connections/status")
def connection_status():
    return {"active_sessions": len(_sessions)}


@router.post("/connections/disconnect")
def close_connection(session_id: Optional[str] = None):
    """Close an active backend connection session and cleanup session temp objects."""
    if session_id and session_id in _sessions:
        sid = session_id
    elif _sessions:
        sid = list(_sessions.keys())[-1]
    else:
        return {"status": "ok", "message": "No active session"}

    sess = _sessions.pop(sid, None)
    if not sess:
        return {"status": "ok", "session_id": sid}

    temp_objects = sess.get("temp_objects") or []
    for obj in list(reversed(temp_objects)):
        try:
            eng = (obj.get("engine") or "").lower()
            table = obj.get("table")
            if not table:
                continue
            if eng == "databricks":
                cur = sess["target_conn"].cursor()
                cur.execute(f"DROP TABLE IF EXISTS {table}")
                cur.close()
            elif eng == "snowflake":
                cur = sess["source_conn"].cursor()
                cur.execute(f"DROP TABLE IF EXISTS {table}")
                cur.close()
            elif eng == "bigquery":
                try:
                    sess["source_conn"].query(f"DROP TABLE IF EXISTS `{table}`").result()
                except Exception:
                    pass
        except Exception:
            pass

    for key in ("target_conn", "source_conn"):
        try:
            conn = sess.get(key)
            if conn and hasattr(conn, "close"):
                conn.close()
        except Exception:
            pass

    return {"status": "ok", "session_id": sid}

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


def _strip_sql(text: str) -> str:
    return (text or "").strip().rstrip(";").strip()


def _ensure_session_temp_list(sess: dict) -> list[dict]:
    items = sess.get("temp_objects")
    if not isinstance(items, list):
        items = []
        sess["temp_objects"] = items
    return items


def _bq_ensure_dataset(client, dataset_id: str, location: str | None = None) -> None:
    from google.cloud import bigquery

    project = client.project
    full = f"{project}.{dataset_id}"
    try:
        client.get_dataset(full)
        return
    except Exception:
        pass

    ds = bigquery.Dataset(full)
    if location:
        ds.location = location
    try:
        client.create_dataset(ds, exists_ok=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unable to create or access BigQuery temp dataset '{full}'. "
                f"Set env BQ_TEMP_DATASET to an existing dataset you can write to. Error: {exc}"
            ),
        ) from exc


def _materialize_source_query_to_table(sess: dict, session_id: str, sql_text: str) -> str:
    engine = (sess.get("engine") or "").lower()
    source_conn = sess.get("source_conn")
    sql_text = _strip_sql(sql_text)
    if not sql_text:
        raise HTTPException(status_code=400, detail="Source SQL is required")

    def _sf_clean(value):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if text.upper() in {"NONE", "NULL"}:
            return None
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'", "`"}:
            return text[1:-1].strip()
        return text

    suffix = uuid.uuid4().hex[:10]
    table_name = f"qc_src_{session_id.replace('-', '')[:10]}_{suffix}"

    if engine == "bigquery":
        project = getattr(source_conn, "project", None) or os.getenv("BQ_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise HTTPException(status_code=400, detail="BigQuery project is not configured")

        dataset_location = sess.get("bq_dataset_location") or os.getenv("BQ_DATASET_LOCATION") or "US"
        _bq_ensure_dataset(source_conn, _BQ_TEMP_DATASET, location=dataset_location)
        fqn_backtick = f"`{project}.{_BQ_TEMP_DATASET}.{table_name}`"
        ddl = (
            "CREATE OR REPLACE TABLE "
            f"{fqn_backtick} "
            f"OPTIONS(expiration_timestamp=TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL {_BQ_TEMP_TTL_HOURS} HOUR)) "
            f"AS {sql_text}"
        )

        try:
            job = source_conn.query(ddl)
            job.result()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to materialize BigQuery source query: {exc}") from exc

        fqn = f"{project}.{_BQ_TEMP_DATASET}.{table_name}"
        _ensure_session_temp_list(sess).append({"engine": "bigquery", "table": fqn})
        return fqn

    if engine == "snowflake":
        try:
            cur = source_conn.cursor()
            cur.execute("SELECT CURRENT_DATABASE() AS db, CURRENT_SCHEMA() AS sch")
            row = cur.fetchone()
            cur.close()
            db, sch = row[0], row[1]
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to determine Snowflake database/schema: {exc}") from exc

        db = _sf_clean(db)
        sch = _sf_clean(sch)

        if not db or not sch:
            # Fallbacks when the Snowflake session has no current database/schema.
            # 1) Explicit temp target db/schema via env
            env_db = _sf_clean(os.getenv("SNOWFLAKE_TEMP_DATABASE"))
            env_sch = _sf_clean(os.getenv("SNOWFLAKE_TEMP_SCHEMA"))
            # 2) Infer from the query itself by finding the first DB.SCHEMA.TABLE reference.
            inferred_db = inferred_sch = None
            if not (env_db and env_sch):
                match = re.search(r"\b([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)\b", sql_text)
                if match:
                    inferred_db, inferred_sch = match.group(1), match.group(2)

            db = db or env_db or inferred_db
            sch = sch or env_sch or inferred_sch

        if not db or not sch:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Snowflake session has no current database/schema; cannot create temporary tables for query validation. "
                    "Fix by either: (1) referencing a fully-qualified table like DB.SCHEMA.TABLE in the source query so the tool can infer where to create temp tables, "
                    "or (2) setting env vars SNOWFLAKE_TEMP_DATABASE and SNOWFLAKE_TEMP_SCHEMA to a writable location."
                ),
            )

        fqn = f"{db}.{sch}.{table_name}"
        ddl = f"CREATE OR REPLACE TEMPORARY TABLE {fqn} AS {sql_text}"
        try:
            cur = source_conn.cursor()
            # Best-effort: set the context to avoid surprises with identifier resolution.
            try:
                cur.execute(f"USE DATABASE {db}")
                cur.execute(f"USE SCHEMA {db}.{sch}")
            except Exception:
                pass
            cur.execute(ddl)
            cur.close()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to materialize Snowflake source query: {exc}") from exc

        _ensure_session_temp_list(sess).append({"engine": "snowflake", "table": fqn})
        return fqn

    raise HTTPException(status_code=400, detail=f"Unsupported session source engine: {engine}")


def _materialize_databricks_query_to_table(sess: dict, session_id: str, sql_text: str) -> str:
    target_conn = sess.get("target_conn")
    sql_text = _strip_sql(sql_text)
    if not sql_text:
        raise HTTPException(status_code=400, detail="Target SQL is required")

    suffix = uuid.uuid4().hex[:10]
    table_name = f"qc_tgt_{session_id.replace('-', '')[:10]}_{suffix}"
    catalog = _DBX_TEMP_CATALOG
    schema = _DBX_TEMP_SCHEMA

    def _dbx_ident(value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _dbx_get_current_catalog_schema(cur):
        # Databricks SQL supports current_catalog() / current_schema() in UC workspaces.
        cur.execute("SELECT current_catalog() AS catalog, current_schema() AS schema")
        row = cur.fetchone()
        if not row:
            return None, None
        return _dbx_ident(row[0]), _dbx_ident(row[1])

    def _looks_like_hive_metastore_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "uc_hive_metastore_disabled_exception" in msg
            or "hive metastore" in msg and "disabled" in msg
            or "legacy access" in msg
        )

    def _try_materialize(cur, cat: str, sch: str) -> str:
        fqn = f"{cat}.{sch}.{table_name}"
        ddl = f"CREATE OR REPLACE TABLE {fqn} AS {sql_text}"
        try:
            cur.execute(ddl)
            return fqn
        except Exception as exc:
            msg = str(exc).lower()
            schema_missing = (
                "schema_not_found" in msg
                or "schema does not exist" in msg
                or "no such schema" in msg
                or "unknown schema" in msg
            )
            if not schema_missing:
                raise
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {cat}.{sch}")
            cur.execute(ddl)
            return fqn

    # If not explicitly configured, prefer the session's current UC catalog/schema.
    try:
        cur = target_conn.cursor()
        if not catalog or not schema:
            current_cat, current_sch = _dbx_get_current_catalog_schema(cur)
            catalog = catalog or current_cat
            schema = schema or current_sch
        cur.close()
    except Exception:
        # We'll let the create attempt below surface an actionable error.
        pass

    try:
        if not catalog or not schema:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Databricks catalog/schema is not set for query validation temp tables. "
                    "Set env vars DATABRICKS_TEMP_CATALOG and DATABRICKS_TEMP_SCHEMA to a writable Unity Catalog location."
                ),
            )

        cur = target_conn.cursor()
        try:
            fqn = _try_materialize(cur, catalog, schema)
        except Exception as exc:
            # Common in Unity Catalog-only workspaces if someone configured hive_metastore.
            if _looks_like_hive_metastore_error(exc):
                current_cat, current_sch = _dbx_get_current_catalog_schema(cur)
                if current_cat and current_sch and (current_cat.lower() != "hive_metastore"):
                    fqn = _try_materialize(cur, current_cat, current_sch)
                else:
                    raise
            else:
                raise
        finally:
            cur.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to materialize Databricks target query: {exc}") from exc

    _ensure_session_temp_list(sess).append({"engine": "databricks", "table": fqn})
    return fqn

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

class RowCountRequest(BaseModel):
    session_id: Optional[str] = None
    target: str
    catalog: str
    schema_name: str
    table_name: str

@router.post("/metadata/row-count")
def get_row_count_endpoint(req: RowCountRequest):
    sess = _get_session(req.session_id)
    engine = sess["engine"] if req.target == "source" else "Databricks"
    conn = sess["source_conn"] if req.target == "source" else sess["target_conn"]
    try:
        from validation_tool.query_builder import build_shallow_query
        from validation_tool.validation_core import execute_query
        from validation_tool.backend.validators import normalize_result
        query = build_shallow_query(engine, req.catalog, req.schema_name, req.table_name, {"row_count": True})
        res = normalize_result(execute_query(engine, conn, query)[0])
        return {"row_count": int(res.get("row_count", 0))}
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
    run_by: Optional[str] = None

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
        normalize_column_list,
        normalize_hash_value,
        numeric_values_equal,
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
                source_row_count = _row_count_for_table(engine, source_conn, src["catalog"], src["schema"], src["table"])
                categorical_columns = _normalize_categorical_columns(settings.get("categoricalColumns"))
                if source_row_count > 1_000_000 and not categorical_columns:
                    raise HTTPException(status_code=400, detail=f"Source table {_table_fqn(src['catalog'], src['schema'], src['table'])} has {source_row_count:,} rows. Please select categorical columns before running row hash validation.")
                checks.append(("Row Hash Validation", lambda s=src, t=tgt: run_row_hash_validation(engine, source_conn, target_conn, s, t, include_timestamp_columns=include_ts, threshold=threshold, source_where=src_where, target_where=tgt_where, categorical_columns=settings.get("categoricalColumns"))))

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
                except Exception as e:
                    details["row_count"] = {"error": str(e)}

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
                except Exception as e:
                    details["schema"] = {"rows": [], "error": str(e)}

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
                        return f"{cat}.{sch}.{tbl}"

                    src_all_map = {str(r.get("column_name", "")).lower(): str(r.get("column_name", "")) for r in src_schema_rows}
                    tgt_all_map = {str(r.get("column_name", "")).lower(): str(r.get("column_name", "")) for r in tgt_schema_rows}
                    common_all = sorted(set(src_all_map.keys()) & set(tgt_all_map.keys()))

                    null_rows_dict = {}
                    if common_all:
                        # Build bulk query for source
                        src_null_cols_sql = [
                            f"SUM(CASE WHEN {_quote_ident(engine, src_all_map[c])} IS NULL THEN 1 ELSE 0 END) AS {_quote_ident(engine, c + '_nulls')}"
                            for c in common_all
                        ]
                        src_null_bulk_q = f"SELECT {', '.join(src_null_cols_sql)} FROM {_qualify_fqn(engine, src['catalog'], src['schema'], src['table'])} WHERE {normalize_where_input(src_where)}"
                        src_null_res = normalize_result(execute_query(engine, source_conn, src_null_bulk_q)[0])

                        # Build bulk query for target
                        tgt_null_cols_sql = [
                            f"SUM(CASE WHEN {_quote_ident('Databricks', tgt_all_map[c])} IS NULL THEN 1 ELSE 0 END) AS {_quote_ident('Databricks', c + '_nulls')}"
                            for c in common_all
                        ]
                        tgt_null_bulk_q = f"SELECT {', '.join(tgt_null_cols_sql)} FROM {_qualify_fqn('Databricks', tgt['catalog'], tgt['schema'], tgt['table'])} WHERE {normalize_where_input(tgt_where)}"
                        tgt_null_res = normalize_result(execute_query("Databricks", target_conn, tgt_null_bulk_q)[0])

                        for c in common_all:
                            src_null_count = src_null_res.get(c + "_nulls", 0)
                            tgt_null_count = tgt_null_res.get(c + "_nulls", 0)
                            null_rows_dict[c] = {
                                "column": src_all_map[c],
                                "source_null_count": int(src_null_count or 0),
                                "target_null_count": int(tgt_null_count or 0)
                            }

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

                        null_counts = null_rows_dict.get(col_key, {})

                        numeric_rows.append({
                            "column": src_col,
                            "source_null_count": null_counts.get("source_null_count", 0),
                            "target_null_count": null_counts.get("target_null_count", 0),
                            "source_min": src_res.get("min_val"),
                            "source_max": src_res.get("max_val"),
                            "source_avg": src_res.get("avg_val"),
                            "target_min": tgt_res.get("min_val"),
                            "target_max": tgt_res.get("max_val"),
                            "target_avg": tgt_res.get("avg_val"),
                        })

                    details["numeric"] = {
                        "rows": numeric_rows,
                        "null_rows": list(null_rows_dict.values())
                    }
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
                        s_raw = str(s.get("data_type", "") or "")
                        t_raw = str(t.get("data_type", "") or "")
                        s_norm = normalize_datatype(s_raw, s.get("column_name"))
                        t_norm = normalize_datatype(t_raw, t.get("column_name"))
                        canon = s_norm if s_norm == t_norm else "STRING"

                        dtype = str(canon or "").upper()
                        if any(x in dtype for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
                            continue
                        if not include_ts and ("TIMESTAMP" in dtype or "DATETIME" in dtype):
                            continue

                        src_columns.append({
                            "name": s["column_name"],
                            "type": dtype,
                            "raw_type": "STRING" if canon == "STRING" else s_raw,
                        })
                        tgt_columns.append({
                            "name": t["column_name"],
                            "type": dtype,
                            "raw_type": "STRING" if canon == "STRING" else t_raw,
                        })

                    hash_mode = "row"
                    categorical_columns = normalize_column_list(settings.get("categoricalColumns"))
                    category_rows = []
                    source_hash_count = 0
                    target_hash_count = 0
                    matched_hash_count = 0
                    source_not_in_target_count = 0
                    target_not_in_source_count = 0
                    debug_category_mismatch = None
                    if categorical_columns and src_columns:
                        hash_mode = "categorical"
                        src_schema_for_hash = [{"column_name": c["name"], "data_type": c["raw_type"] or c["type"]} for c in src_columns]
                        tgt_schema_for_hash = [{"column_name": c["name"], "data_type": c["raw_type"] or c["type"]} for c in tgt_columns]
                        src_query = build_categorical_hash_query(
                            engine,
                            src["catalog"],
                            src["schema"],
                            src["table"],
                            schema_rows=src_schema_for_hash,
                            categorical_columns=categorical_columns,
                            include_timestamp=include_ts,
                            where_clause=normalize_where_input(src_where),
                        )
                        tgt_query = build_categorical_hash_query(
                            "Databricks",
                            tgt["catalog"],
                            tgt["schema"],
                            tgt["table"],
                            schema_rows=tgt_schema_for_hash,
                            categorical_columns=categorical_columns,
                            include_timestamp=include_ts,
                            where_clause=normalize_where_input(tgt_where),
                        )

                        def _categorical_map(rows):
                            mapped = {}
                            for row in rows:
                                normalized = normalize_result(row)
                                key = tuple(str(normalized.get(f"group_key_{i + 1}", "")).strip() for i in range(len(categorical_columns)))
                                mapped[key] = {
                                    "row_count": normalized.get("row_count"),
                                    "group_hash_sum": normalized.get("group_hash_sum"),
                                }
                            return mapped

                        src_groups = _categorical_map(execute_query(engine, source_conn, src_query))
                        tgt_groups = _categorical_map(execute_query("Databricks", target_conn, tgt_query))
                        for key in sorted(set(src_groups) | set(tgt_groups)):
                            src_group = src_groups.get(key)
                            tgt_group = tgt_groups.get(key)
                            src_count = int(src_group.get("row_count") or 0) if src_group else 0
                            tgt_count = int(tgt_group.get("row_count") or 0) if tgt_group else 0
                            count_match = src_group is not None and tgt_group is not None and numeric_values_equal(src_count, tgt_count)
                            hash_match = (
                                src_group is not None
                                and tgt_group is not None
                                and numeric_values_equal(src_group.get("group_hash_sum"), tgt_group.get("group_hash_sum"))
                            )
                            status = "MATCH" if count_match and hash_match else "NOT MATCH"
                            category_rows.append({
                                "category_values": {categorical_columns[i]: key[i] if i < len(key) else "" for i in range(len(categorical_columns))},
                                "source_row_count": src_count,
                                "target_row_count": tgt_count,
                                "source_hash_sum": None if src_group is None else str(src_group.get("group_hash_sum") or ""),
                                "target_hash_sum": None if tgt_group is None else str(tgt_group.get("group_hash_sum") or ""),
                                "status": status,
                            })
                            source_hash_count += src_count
                            target_hash_count += tgt_count
                            if status == "MATCH":
                                matched_hash_count += src_count
                            else:
                                source_not_in_target_count += src_count
                                target_not_in_source_count += tgt_count

                                if debug_category_mismatch is None:
                                    debug_category_mismatch = {
                                        "category_values": {categorical_columns[i]: key[i] if i < len(key) else "" for i in range(len(categorical_columns))},
                                        "source_row_count": src_count,
                                        "target_row_count": tgt_count,
                                        "source_hash_sum": None if src_group is None else str(src_group.get("group_hash_sum") or ""),
                                        "target_hash_sum": None if tgt_group is None else str(tgt_group.get("group_hash_sum") or ""),
                                    }

                        results_map["Row Hash Validation"] = all(row["status"] == "MATCH" for row in category_rows)

                        if debug_category_mismatch is not None:
                            try:
                                mismatch_key = [debug_category_mismatch["category_values"][c] for c in categorical_columns]
                                src_sample_query = build_categorical_hash_samples_query(
                                    engine,
                                    src["catalog"],
                                    src["schema"],
                                    src["table"],
                                    schema_rows=src_schema_for_hash,
                                    categorical_columns=categorical_columns,
                                    group_key_values=mismatch_key,
                                    include_timestamp=include_ts,
                                    where_clause=normalize_where_input(src_where),
                                    limit=5,
                                )
                                tgt_sample_query = build_categorical_hash_samples_query(
                                    "Databricks",
                                    tgt["catalog"],
                                    tgt["schema"],
                                    tgt["table"],
                                    schema_rows=tgt_schema_for_hash,
                                    categorical_columns=categorical_columns,
                                    group_key_values=mismatch_key,
                                    include_timestamp=include_ts,
                                    where_clause=normalize_where_input(tgt_where),
                                    limit=5,
                                )
                                debug_category_mismatch["source_samples"] = execute_query(engine, source_conn, src_sample_query)
                                debug_category_mismatch["target_samples"] = execute_query("Databricks", target_conn, tgt_sample_query)
                            except Exception as e:
                                debug_category_mismatch["error"] = str(e)
                        if not settings.get("colDiffEnabled", False):
                            src_columns = []

                    src_only_hashes = []
                    tgt_only_hashes = []
                    src_only_rows = []
                    tgt_only_rows = []
                    hash_columns = []
                    mismatched_columns = []
                    src_hashes = []
                    tgt_hashes = []
                    matched_hashes = []
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
                        src_hashes = sorted({h for r in src_rows if (h := normalize_hash_value(get_hash(r))) is not None})
                        tgt_hashes = sorted({h for r in tgt_rows if (h := normalize_hash_value(get_hash(r))) is not None})

                        tgt_set = set(tgt_hashes)
                        src_set = set(src_hashes)
                        src_only_hashes = [h for h in src_hashes if h not in tgt_set]
                        tgt_only_hashes = [h for h in tgt_hashes if h not in src_set]
                        matched_hashes = sorted(src_set & tgt_set)
                        source_hash_count = len(src_hashes)
                        target_hash_count = len(tgt_hashes)
                        matched_hash_count = len(matched_hashes)
                        source_not_in_target_count = len(src_only_hashes)
                        target_not_in_source_count = len(tgt_only_hashes)

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

                        # ─── Improved Mismatch Identification ───
                        pks_input = settings.get("primaryKeys", "")
                        pks = [k.strip() for k in str(pks_input).split(",") if k.strip()]
                        
                        if pks and src_only_rows and tgt_only_rows:
                            # Use PKs to correlate rows and find specific mismatched columns
                            # We create a map of PK-tuple -> row for both sides
                            def get_pk_val(row):
                                return tuple(str(row.get(k, "")) for k in pks)
                            
                            src_pk_map = {get_pk_val(r): r for r in src_only_rows}
                            tgt_pk_map = {get_pk_val(r): r for r in tgt_only_rows}
                            
                            common_pks = set(src_pk_map.keys()) & set(tgt_pk_map.keys())
                            
                            for pk_val in common_pks:
                                s_row = src_pk_map[pk_val]
                                t_row = tgt_pk_map[pk_val]
                                for col_name in hash_columns:
                                    if col_name in pks: continue
                                    s_val = "" if s_row.get(col_name) is None else str(s_row.get(col_name))
                                    t_val = "" if t_row.get(col_name) is None else str(t_row.get(col_name))
                                    if s_val != t_val and col_name not in mismatched_columns:
                                        mismatched_columns.append(col_name)
                        else:
                            # Fallback to the original set-based comparison if no PK is provided
                            for col_name in hash_columns:
                                src_vals = {"" if r.get(col_name) is None else str(r.get(col_name)) for r in src_only_rows}
                                tgt_vals = {"" if r.get(col_name) is None else str(r.get(col_name)) for r in tgt_only_rows}
                                if src_vals != tgt_vals:
                                    mismatched_columns.append(col_name)

                    details["row_hash"] = {
                        "mode": hash_mode,
                        "columns": hash_columns,
                        "categorical_columns": categorical_columns,
                        "categories": category_rows,
                        "category_debug": debug_category_mismatch,
                        "mismatched_columns": mismatched_columns,
                        "source_hash_count": source_hash_count,
                        "target_hash_count": target_hash_count,
                        "matched_hash_count": matched_hash_count,
                        "source_not_in_target_count": source_not_in_target_count,
                        "target_not_in_source_count": target_not_in_source_count,
                        "source_not_in_target_rows": src_only_rows,
                        "target_not_in_source_rows": tgt_only_rows,
                    }

                    if (
                        details["row_hash"]["source_hash_count"] == details["row_hash"]["target_hash_count"]
                        and details["row_hash"]["source_hash_count"] == details["row_hash"]["matched_hash_count"]
                        and details["row_hash"]["source_not_in_target_count"] == 0
                        and details["row_hash"]["target_not_in_source_count"] == 0
                    ):
                        results_map["Row Hash Validation"] = True
                except Exception as e:
                    details["row_hash"] = {"error": str(e)}

            record = generate_validation_record(
                vtype, src, tgt,
                bool_to_status(results_map.get("Row Count Validation")),
                bool_to_status(results_map.get("Schema Validation")),
                bool_to_status(results_map.get("Numeric Statistics Validation")),
                bool_to_status(results_map.get("Row Hash Validation")),
                req.run_by,
            )
            try:
                insert_validation_result(record)
            except Exception as e:
                logger.error(f"Insert failed: {e}")

            results_list.append({
                "validation_id": record.get("validation_id"),
                "validation_ts": record.get("timestamp"),
                "run_by": record.get("run_by"),
                "source_engine": engine.lower(),
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

    if results_list:
        supabase_store.upsert_results(results_list)

    return {"results": results_list}


class QueryValidationRequest(BaseModel):
    session_id: Optional[str] = None
    validation_type: str = "shallow"
    settings: dict = {}
    run_by: Optional[str] = None
    source_sql: str
    target_sql: str


@router.post("/validate/query")
def run_query_validation(req: QueryValidationRequest):
    if not (req.session_id or "").strip():
        raise HTTPException(status_code=400, detail="session_id is required")

    sess = _get_session(req.session_id)
    session_id = req.session_id
    _enforce_source_engine_match(sess.get("engine"), req.source_sql)

    src_table = _materialize_source_query_to_table(sess, session_id, req.source_sql)
    tgt_table = _materialize_databricks_query_to_table(sess, session_id, req.target_sql)

    run_req = RunValidationRequest(
        session_id=session_id,
        validation_type=(req.validation_type or "shallow").strip().lower(),
        table_pairs=[{"source": src_table, "target": tgt_table}],
        settings=req.settings or {},
        run_by=req.run_by,
    )

    response = run_validation(run_req)
    update_query_stats(
        session_id,
        migrated=False,
        validated=True,
        complexity_level=None,
        source_engine=sess.get("engine"),
    )
    response["temp_tables"] = {"source": src_table, "target": tgt_table}
    return response

# ══════════════════════════════════════
# CSV VALIDATION
# ══════════════════════════════════════

@router.post("/validate/csv")
async def run_csv_validation(
    session_id: str = Form(""),
    settings: str = Form("{}"),
    file: UploadFile = File(...),
):
    _get_session(session_id)
    contents = await file.read()
    df = pd.read_csv(io.BytesIO(contents), comment="#")

    required_cols = {
        "source_catalog",
        "source_schema",
        "source_table",
        "target_catalog",
        "target_schema",
        "target_table",
        "validation_type",
    }
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise HTTPException(status_code=400, detail=f"Missing columns: {missing_cols}")

    try:
        ui_settings = json.loads(settings or "{}") if isinstance(settings, str) else (settings or {})
    except Exception:
        ui_settings = {}

    def _to_bool(value, default=False):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"yes", "true", "1", "y"}:
            return True
        if text in {"no", "false", "0", "n"}:
            return False
        return default

    def _val(row, key, default=""):
        raw = row.get(key, default)
        if isinstance(raw, float) and pd.isna(raw):
            return default
        return raw

    all_results = []
    for _, row in df.iterrows():
        src = f"{_val(row, 'source_catalog')}.{_val(row, 'source_schema')}.{_val(row, 'source_table')}"
        tgt = f"{_val(row, 'target_catalog')}.{_val(row, 'target_schema')}.{_val(row, 'target_table')}"

        if not src.strip(".") or not tgt.strip("."):
            continue

        use_separate_where = _to_bool(_val(row, "use_separate_where", "no"), False)
        shared_where = str(_val(row, "where_clause", "1=1") or "1=1")
        source_where = str(_val(row, "source_where", shared_where) or shared_where) if use_separate_where else shared_where
        target_where = str(_val(row, "target_where", shared_where) or shared_where) if use_separate_where else shared_where

        vtype = str(_val(row, "validation_type", "shallow") or "shallow").strip().lower()
        metrics_text = str(_val(row, "metrics", "") or "")
        metrics = [m.strip().lower() for m in metrics_text.split(",") if m.strip()]

        table_settings = dict(ui_settings or {})
        table_settings["caseSensitive"] = _to_bool(_val(row, "case_sensitive", table_settings.get("caseSensitive", False)), table_settings.get("caseSensitive", False))
        table_settings["includeTimestamp"] = _to_bool(_val(row, "include_timestamp", table_settings.get("includeTimestamp", False)), table_settings.get("includeTimestamp", False))

        if vtype == "shallow":
            table_settings.update({"rowCount": True, "schema": True, "numeric": False, "hash": False})
        elif metrics:
            table_settings.update({
                "rowCount": "row_count" in metrics,
                "schema": "schema" in metrics,
                "numeric": "numeric" in metrics,
                "hash": "hash" in metrics,
            })

        row_threshold = _val(row, "row_threshold", "")
        if row_threshold not in ("", None):
            try:
                threshold_val = float(row_threshold)
                if threshold_val > 1:
                    threshold_val = threshold_val / 100.0
                table_settings["useThreshold"] = True
                table_settings["threshold"] = threshold_val
            except Exception:
                pass

        run_req = RunValidationRequest(
            session_id=session_id,
            validation_type=vtype,
            table_pairs=[{
                "source": src,
                "target": tgt,
                "source_where": source_where,
                "target_where": target_where,
            }],
            settings=table_settings,
        )

        response = run_validation(run_req)
        all_results.extend(response.get("results", []))

    return {"results": all_results}

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
    run_by: Optional[str] = None
    config: dict
    settings: dict = {}

@router.post("/validate/config")
def run_config_validation(req: ConfigValidationRequest):
    """Accept JSON config and run validations for each table entry."""
    tables = req.config.get("tables", [])
    if not tables:
        raise HTTPException(status_code=400, detail="No tables defined in config")

    def _to_bool(value, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"yes", "true", "1", "y"}:
            return True
        if text in {"no", "false", "0", "n"}:
            return False
        return default

    all_results = []
    for t in tables:
        src = t.get("source") or ".".join([
            str(t.get("source_catalog", "")).strip(),
            str(t.get("source_schema", "")).strip(),
            str(t.get("source_table", "")).strip(),
        ]).strip(".")
        tgt = t.get("target") or ".".join([
            str(t.get("target_catalog", "")).strip(),
            str(t.get("target_schema", "")).strip(),
            str(t.get("target_table", "")).strip(),
        ]).strip(".")

        if not src or not tgt:
            continue

        use_separate_where = _to_bool(t.get("use_separate_where"), False)
        shared_where = t.get("where_clause", t.get("where", "1=1")) or "1=1"
        src_where = (t.get("source_where") or shared_where) if use_separate_where else shared_where
        tgt_where = (t.get("target_where") or shared_where) if use_separate_where else shared_where

        table_settings = dict(req.settings or {})

        metrics_raw = t.get("metrics")
        metric_items = []
        if isinstance(metrics_raw, str):
            metric_items = [m.strip().lower() for m in metrics_raw.split(",") if m.strip()]
        elif isinstance(metrics_raw, list):
            metric_items = [str(m).strip().lower() for m in metrics_raw if str(m).strip()]

        if metric_items:
            table_settings.update({
                "rowCount": "row_count" in metric_items,
                "schema": "schema" in metric_items,
                "numeric": "numeric" in metric_items,
                "hash": "hash" in metric_items,
            })

        if "case_sensitive" in t:
            table_settings["caseSensitive"] = _to_bool(t.get("case_sensitive"), False)
        if "include_timestamp" in t:
            table_settings["includeTimestamp"] = _to_bool(t.get("include_timestamp"), False)

        categorical_columns = _normalize_categorical_columns(
            t.get("categorical_columns") or t.get("categoricalColumns") or table_settings.get("categoricalColumns")
        )
        if categorical_columns:
            table_settings["categoricalColumns"] = categorical_columns

        row_threshold = t.get("row_threshold")
        if row_threshold not in (None, ""):
            try:
                threshold_value = float(row_threshold)
                if threshold_value > 1:
                    threshold_value = threshold_value / 100.0
                table_settings["useThreshold"] = True
                table_settings["threshold"] = threshold_value
            except Exception:
                pass

        if table_settings.get("hash"):
            source_catalog, source_schema, source_table = src.split(".", 2)
            source_row_count = _row_count_for_table(engine, source_conn, source_catalog, source_schema, source_table)
            if source_row_count > 1_000_000 and not categorical_columns:
                raise HTTPException(status_code=400, detail=f"Source table {_table_fqn(source_catalog, source_schema, source_table)} has {source_row_count:,} rows. Please select categorical columns before running row hash validation.")

        run_req = RunValidationRequest(
            session_id=req.session_id,
            validation_type=str(t.get("validation_type", "shallow") or "shallow").lower(),
            run_by=req.run_by,
            table_pairs=[{
                "source": src,
                "target": tgt,
                "source_where": src_where,
                "target_where": tgt_where,
            }],
            settings=table_settings,
        )

        response = run_validation(run_req)
        all_results.extend(response.get("results", []))

    return {"results": all_results}
