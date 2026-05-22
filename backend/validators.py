from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from validation_tool.connections.bigquery import connect_bigquery
from validation_tool.connections.databricks import connect_databricks
from validation_tool.connections.snowflake import connect_snowflake
from validation_tool.query_builder import (
    build_numeric_stats_query,
    build_row_hash_query,
    build_schema_query,
    build_shallow_query,
    get_numeric_columns,
)
try:
    from validation_tool.datatype_utils import normalize_datatype as canonical_normalize_datatype
except ImportError:
    from datatype_utils import normalize_datatype as canonical_normalize_datatype


# Keep schema/type normalization consistent with the core validation logic.
DATA_TYPE_EQUIVALENCE = {
    # Integer types
    "int": "INT",
    "integer": "INT",
    "bigint": "INT",
    "smallint": "INT",
    "tinyint": "INT",
    "byteint": "INT",
    "int64": "INT",

    # Decimal types (NUMBER handled separately with column logic)
    "decimal": "DECIMAL",
    "numeric": "DECIMAL",
    "bignumeric": "DECIMAL",
    "number": "DECIMAL",

    # Float types
    "float": "DOUBLE",
    "double": "DOUBLE",
    "real": "DOUBLE",
    "float64": "DOUBLE",

    # String types
    "string": "STRING",
    "varchar": "STRING",
    "char": "STRING",
    "text": "STRING",

    # Binary
    "binary": "BINARY",
    "varbinary": "BINARY",

    # Boolean
    "boolean": "BOOLEAN",
    "bool": "BOOLEAN",

    # Timestamps
    "timestamp": "TIMESTAMP",
    "timestamp_ntz": "TIMESTAMP",
    "timestamp_ltz": "TIMESTAMP",
    "timestamp_tz": "TIMESTAMP",
    "datetime": "TIMESTAMP",

    # Date
    "date": "DATE",

    # Time - Databricks stores as STRING
    "time": "STRING",

    # VARIANT - target expects STRUCT
    "variant": "STRUCT",

    # Object/Array
    "object": "STRUCT",
    "array": "ARRAY",
}


def normalize_datatype(dtype, column_name: str | None = None) -> str:
    return canonical_normalize_datatype(dtype, column_name)
    if not dtype:
        return "unknown"

    dtype_lower = str(dtype).lower().strip()

    # Handle nested/complex Spark-style types early (e.g. struct<...>, array<...>, map<...>)
    if dtype_lower.startswith("struct<"):
        return "STRUCT"
    if dtype_lower.startswith("array<"):
        return "ARRAY"
    if dtype_lower.startswith("map<"):
        return "STRUCT"

    # Extract base type (remove precision/scale like NUMBER(10,2) → NUMBER)
    base_type = dtype_lower.split("(")[0].strip()

    # Special handling for NUMBER - check column name to decide INT vs DECIMAL
    if base_type == "number" and column_name:
        col_lower = str(column_name).lower()
        int_indicators = ["id", "age", "count", "num", "idx", "index", "row", "key"]
        if any(ind in col_lower for ind in int_indicators):
            return "INT"
        return "DECIMAL"

    return DATA_TYPE_EQUIVALENCE.get(base_type, base_type.upper())


def parse_table_path(path: str):
    parts = [p.strip() for p in (path or "").split(".")]
    if len(parts) != 3 or any(not p for p in parts):
        raise ValueError(f"Invalid table path '{path}'. Expected catalog.schema.table")
    return parts[0], parts[1], parts[2]


def execute_query(engine: str, conn, query: str):
    engine = engine.lower()

    if engine == "bigquery":
        job = conn.query(query)
        rows = list(job.result())
        return [dict(r) for r in rows]

    if engine in {"databricks", "snowflake"}:
        cur = conn.cursor()
        cur.execute(query)
        cols = [c[0] for c in cur.description] if cur.description else []
        rows = cur.fetchall()
        cur.close()
        return [dict(zip(cols, row)) for row in rows]

    raise ValueError(f"Unsupported engine: {engine}")


def normalize_result(row: dict) -> dict:
    return {str(k).lower(): v for k, v in (row or {}).items()}


def normalize_keys(d: dict) -> dict:
    return {str(k).lower(): v for k, v in (d or {}).items()}


def normalize_numeric(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(val)
    except Exception:
        return None


def approx_equal(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def fetch_schema(engine: str, conn, catalog: str, schema: str, table: str) -> list[dict]:
    rows = execute_query(engine, conn, build_schema_query(engine, catalog, schema, table))
    return [normalize_result(r) for r in rows]


def validate_row_count(source_engine: str, source_conn, target_conn, src, tgt) -> bool:
    metrics = {"row_count": True}

    src_query = build_shallow_query(source_engine, src[0], src[1], src[2], metrics)
    tgt_query = build_shallow_query("databricks", tgt[0], tgt[1], tgt[2], metrics)

    src_res = normalize_result(execute_query(source_engine, source_conn, src_query)[0])
    tgt_res = normalize_result(execute_query("databricks", target_conn, tgt_query)[0])

    return src_res.get("row_count") == tgt_res.get("row_count")


def validate_schema(source_engine: str, source_conn, target_conn, src, tgt, case_sensitive: bool) -> bool:
    src_schema = fetch_schema(source_engine, source_conn, src[0], src[1], src[2])
    tgt_schema = fetch_schema("databricks", target_conn, tgt[0], tgt[1], tgt[2])

    def index_schema(rows):
        m = {}
        for r in rows:
            col = r.get("column_name")
            dtype = r.get("data_type")
            if not col:
                continue
            key = col if case_sensitive else str(col).lower()
            m[key] = normalize_datatype(dtype, str(col))
        return m

    s = index_schema(src_schema)
    t = index_schema(tgt_schema)

    if set(s.keys()) != set(t.keys()):
        return False

    for k in s.keys():
        if s.get(k) != t.get(k):
            return False

    return True


def validate_numeric(source_engine: str, source_conn, target_conn, src, tgt) -> bool:
    src_schema = fetch_schema(source_engine, source_conn, src[0], src[1], src[2])
    numeric_cols = get_numeric_columns(src_schema)
    if not numeric_cols:
        return True

    for col in numeric_cols:
        s_q = build_numeric_stats_query(source_engine, src[0], src[1], src[2], col)
        t_q = build_numeric_stats_query("databricks", tgt[0], tgt[1], tgt[2], col)

        s = normalize_keys(execute_query(source_engine, source_conn, s_q)[0])
        t = normalize_keys(execute_query("databricks", target_conn, t_q)[0])

        if not (
            approx_equal(normalize_numeric(s.get("min_val")), normalize_numeric(t.get("min_val")))
            and approx_equal(normalize_numeric(s.get("max_val")), normalize_numeric(t.get("max_val")))
            and approx_equal(normalize_numeric(s.get("avg_val")), normalize_numeric(t.get("avg_val")))
        ):
            return False

    return True


def _get_hash_value(row: dict) -> str | None:
    if not isinstance(row, dict):
        return None
    v = row.get("hash_value")
    if v is None:
        v = row.get("HASH_VALUE")
    if v is None:
        return None
    return str(v).strip().lower()


def validate_hash(
    source_engine: str,
    source_conn,
    target_conn,
    src,
    tgt,
    include_timestamp: bool,
) -> bool:
    # Use schemas from each engine and include only common columns.
    src_schema = fetch_schema(source_engine, source_conn, src[0], src[1], src[2])
    tgt_schema = fetch_schema("databricks", target_conn, tgt[0], tgt[1], tgt[2])

    def to_map(rows):
        m = {}
        for r in rows:
            col = r.get("column_name")
            dtype = r.get("data_type")
            if not col:
                continue
            m[str(col).lower()] = {"name": col, "raw_type": dtype, "type": str(dtype or "")}
        return m

    s_map = to_map(src_schema)
    t_map = to_map(tgt_schema)

    common = sorted(set(s_map.keys()) & set(t_map.keys()))
    if not common:
        return False

    src_cols = []
    tgt_cols = []
    for k in common:
        s = s_map[k]
        t = t_map[k]
        if not include_timestamp:
            raw_s = str(s.get("raw_type") or "").lower()
            raw_t = str(t.get("raw_type") or "").lower()
            if "timestamp" in raw_s or "datetime" in raw_s or "timestamp" in raw_t or "datetime" in raw_t:
                continue
        # keep raw_type as-is; query_builder normalizes per engine
        src_cols.append({"name": s["name"], "raw_type": s.get("raw_type"), "type": s.get("type")})
        tgt_cols.append({"name": t["name"], "raw_type": t.get("raw_type"), "type": t.get("type")})

    s_q = build_row_hash_query(source_engine, src[0], src[1], src[2], columns=src_cols)
    t_q = build_row_hash_query("databricks", tgt[0], tgt[1], tgt[2], columns=tgt_cols)

    s_rows = execute_query(source_engine, source_conn, s_q)
    t_rows = execute_query("databricks", target_conn, t_q)

    s_hashes = {h for r in s_rows if (h := _get_hash_value(r)) is not None}
    t_hashes = {h for r in t_rows if (h := _get_hash_value(r)) is not None}

    return s_hashes == t_hashes


def run_validation_job(session_payload: dict, row: dict) -> dict:
    source_engine = str(session_payload.get("source_engine") or "").lower()
    if source_engine not in {"bigquery", "snowflake"}:
        raise ValueError("Unsupported source_engine in session")

    target = session_payload.get("target") or {}

    source = session_payload.get("source") or {}

    source_conn = None
    target_conn = None
    try:
        if source_engine == "bigquery":
            source_conn = connect_bigquery(
                source.get("project_id"),
                source.get("service_account_key_path"),
                source.get("dataset_location", "US"),
            )
        else:
            source_conn = connect_snowflake(
                source.get("account"),
                source.get("user"),
                source.get("password"),
                source.get("warehouse"),
                source.get("role"),
            )

        target_conn = connect_databricks(
            target.get("server_hostname"),
            target.get("http_path"),
            target.get("access_token"),
        )

        src = parse_table_path(row["source_table"])
        tgt = parse_table_path(row["target_table"])

        metric_set = {m.strip().lower() for m in (row.get("metrics") or []) if m}
        if "all" in metric_set:
            metric_set = {"row_count", "schema", "numeric", "hash"}

        validation_type = row.get("validation_type", "shallow")
        if validation_type == "shallow":
            metric_set = {m for m in metric_set if m in {"row_count", "schema"}}

        row_count_status = None
        schema_status = None
        numeric_status = None
        hash_status = None

        if "row_count" in metric_set:
            row_count_status = (
                "PASS"
                if validate_row_count(source_engine, source_conn, target_conn, src, tgt)
                else "FAIL"
            )

        if "schema" in metric_set:
            schema_status = (
                "PASS"
                if validate_schema(
                    source_engine,
                    source_conn,
                    target_conn,
                    src,
                    tgt,
                    bool(row.get("case_sensitive")),
                )
                else "FAIL"
            )

        if "numeric" in metric_set:
            numeric_status = (
                "PASS"
                if validate_numeric(source_engine, source_conn, target_conn, src, tgt)
                else "FAIL"
            )

        if "hash" in metric_set:
            # Enforce 1M row limit for row-by-row hash
            metrics = {"row_count": True}
            src_count_query = build_shallow_query(source_engine, src[0], src[1], src[2], metrics)
            src_count_res = normalize_result(execute_query(source_engine, source_conn, src_count_query)[0])
            source_row_count = int(src_count_res.get("row_count", 0))

            cat_cols_str = str(row.get("categorical_columns") or "").strip()
            cat_cols = [c.strip() for c in cat_cols_str.split(",")] if cat_cols_str else []

            if source_row_count > 1000000 and not cat_cols:
                raise ValueError("Table has > 1,000,000 rows. Categorical Columns are required to optimize hash validation. Please select 1 or 2 categorical columns.")

            if cat_cols:
                from validation_tool.validation_core import validate_categorical_hash
                hash_status = (
                    "PASS"
                    if validate_categorical_hash(
                        source_engine,
                        source_conn,
                        target_conn,
                        {"catalog": src[0], "schema": src[1], "table": src[2]},
                        {"catalog": tgt[0], "schema": tgt[1], "table": tgt[2]},
                        cat_cols,
                        bool(row.get("include_timestamp", True)),
                    )
                    else "FAIL"
                )
            else:
                hash_status = (
                    "PASS"
                    if validate_hash(
                        source_engine,
                        source_conn,
                        target_conn,
                        src,
                        tgt,
                        bool(row.get("include_timestamp", True)),
                    )
                    else "FAIL"
                )

        statuses = [s for s in [row_count_status, schema_status, numeric_status, hash_status] if s is not None]
        overall_status = "PASS" if statuses and all(s == "PASS" for s in statuses) else "FAIL"

        now = datetime.now(timezone.utc)
        validation_id = row.get("validation_id") or str(uuid.uuid4())

        return {
            "validation_id": validation_id,
            "validation_ts": now,
            "src_table_name": row.get("source_table"),
            "tgt_table_name": row.get("target_table"),
            "validation_type": validation_type,
            "row_count": row_count_status,
            "schema_check": schema_status,
            "numeric_check": numeric_status,
            "hash_validation": hash_status,
            "overall_status": overall_status,
        }
    finally:
        # Best-effort closes; drivers vary in API surface.
        try:
            if source_engine == "bigquery" and source_conn is not None:
                source_conn.close()
        except Exception:
            pass

        try:
            if source_engine == "snowflake" and source_conn is not None:
                source_conn.close()
        except Exception:
            pass

        try:
            if target_conn is not None:
                target_conn.close()
        except Exception:
            pass
