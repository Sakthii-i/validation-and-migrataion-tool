"""
Validation Engine — Pure Python validation logic extracted from app.py.
No Streamlit dependencies. Used by the React API backend.
"""
from __future__ import annotations

import re
import uuid
import logging
from datetime import datetime, timezone

import pandas as pd
import psycopg2

from validation_tool.connections.postgres import POSTGRES_CONFIG
from validation_tool.query_builder import (
    build_shallow_query,
    build_schema_query,
    build_numeric_stats_query,
    build_row_hash_query,
    get_numeric_columns,
)
try:
    from validation_tool.datatype_utils import normalize_datatype as canonical_normalize_datatype
except ImportError:
    from datatype_utils import normalize_datatype as canonical_normalize_datatype

logger = logging.getLogger(__name__)

# ══════════════════════════════════════
# HELPERS
# ══════════════════════════════════════

def execute_query(engine, conn, query):
    engine = engine.lower()
    if engine == "bigquery":
        job = conn.query(query)
        return [dict(row) for row in job.result()]
    elif engine in ["databricks", "snowflake"]:
        cur = conn.cursor()
        cur.execute(query)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
        cur.close()
        return [dict(zip(cols, row)) for row in rows]
    elif engine in ["postgres", "postgresql"]:
        cur = conn.cursor()
        cur.execute(query)
        cols = [c[0] for c in cur.description] if cur.description else []
        rows = cur.fetchall()
        cur.close()
        return [dict(zip(cols, row)) for row in rows]
    else:
        raise ValueError(f"Unsupported engine: {engine}")


def normalize_result(row: dict):
    return {k.lower(): v for k, v in row.items()}


def normalize_where_input(where_value, default="1=1"):
    if where_value is None:
        return default
    text = str(where_value).strip()
    return text if text else default


def parse_table_path(path: str):
    if not path:
        return None, None, None

    def _clean_ident(value: str) -> str:
        text = str(value).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'", "`"}:
            return text[1:-1].strip()
        return text

    parts = [_clean_ident(p) for p in path.split(".")]
    if len(parts) != 3:
        return None, None, None
    return parts[0], parts[1], parts[2]


def bool_to_status(val):
    if val is True:
        return "PASS"
    if val is False:
        return "FAIL"
    return None


DATA_TYPE_EQUIVALENCE = {
    "int": "INT", "integer": "INT", "bigint": "INT", "smallint": "INT",
    "tinyint": "INT", "byteint": "INT", "int64": "INT",
    "decimal": "DECIMAL", "numeric": "DECIMAL", "bignumeric": "DECIMAL", "number": "DECIMAL",
    "float": "DOUBLE", "double": "DOUBLE", "real": "DOUBLE", "float64": "DOUBLE",
    "string": "STRING", "varchar": "STRING", "char": "STRING", "text": "STRING",
    "binary": "BINARY", "varbinary": "BINARY",
    "boolean": "BOOLEAN", "bool": "BOOLEAN",
    "timestamp": "TIMESTAMP", "timestamp_ntz": "TIMESTAMP", "timestamp_ltz": "TIMESTAMP",
    "timestamp_tz": "TIMESTAMP", "datetime": "TIMESTAMP",
    "date": "DATE",
    "time": "STRING",
    "variant": "STRUCT", "object": "STRUCT", "array": "ARRAY",
}


def normalize_datatype(dtype, column_name=None):
    return canonical_normalize_datatype(dtype, column_name)
    if not dtype:
        return "unknown"
    dtype_lower = str(dtype).lower().strip()
    if dtype_lower.startswith("struct<"):
        return "STRUCT"
    if dtype_lower.startswith("array<"):
        return "ARRAY"
    if dtype_lower.startswith("map<"):
        return "STRUCT"
    base_type = dtype_lower.split("(")[0].strip()
    if base_type == "number" and column_name:
        col_lower = column_name.lower()
        int_indicators = ['id', 'age', 'count', 'num', 'idx', 'index', 'row', 'key']
        if any(ind in col_lower for ind in int_indicators):
            return "INT"
        else:
            return "DECIMAL"
    return DATA_TYPE_EQUIVALENCE.get(base_type, base_type.upper())


def normalize_schema_df(schema_rows: list) -> pd.DataFrame:
    if not schema_rows:
        raise ValueError("Schema query returned no rows (empty schema)")
    df = pd.DataFrame(schema_rows)
    df.columns = [c.lower() for c in df.columns]
    if "column_name" not in df.columns:
        for c in ["column_name", "col_name", "name", "column"]:
            if c in df.columns:
                df["column_name"] = df[c]
                break
    if "data_type" not in df.columns:
        for c in ["data_type", "type", "dtype"]:
            if c in df.columns:
                df["data_type"] = df[c]
                break
    missing = {"column_name", "data_type"} - set(df.columns)
    if missing:
        raise ValueError(f"Schema query returned unexpected columns: {df.columns.tolist()}")
    return df[["column_name", "data_type"]]


def fetch_schema(engine, conn, catalog, schema, table):
    query = build_schema_query(engine, catalog, schema, table)
    schema_raw = execute_query(engine, conn, query)
    return [normalize_result(r) for r in schema_raw]


def get_dashboard_postgres_conn():
    return psycopg2.connect(
        host=POSTGRES_CONFIG["host"],
        port=POSTGRES_CONFIG["port"],
        dbname=POSTGRES_CONFIG["db"],
        user=POSTGRES_CONFIG["user"],
        password=POSTGRES_CONFIG["password"],
        sslmode=POSTGRES_CONFIG.get("sslmode", "require"),
    )


def ensure_validation_table(pg_conn):
    ddl = """
    CREATE SCHEMA IF NOT EXISTS table_validation;
    CREATE TABLE IF NOT EXISTS table_validation.validation_results (
        validation_id TEXT PRIMARY KEY,
        validation_ts TIMESTAMPTZ,
        src_table_name TEXT,
        tgt_table_name TEXT,
        validation_type TEXT,
        run_by TEXT,
        row_count TEXT,
        schema_check TEXT,
        numeric_check TEXT,
        hash_validation TEXT
    );
    """
    cur = pg_conn.cursor()
    for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
        cur.execute(stmt)
    # Backward-compatible migrations for existing deployments.
    cur.execute("ALTER TABLE IF EXISTS table_validation.validation_results ADD COLUMN IF NOT EXISTS run_by TEXT")
    pg_conn.commit()
    cur.close()


# ══════════════════════════════════════
# VALIDATION FUNCTIONS
# ══════════════════════════════════════

def run_row_count(
    engine, source_conn, target_conn, src, tgt,
    threshold=None, source_where="1=1", target_where="1=1",
):
    metrics = {"row_count": True}
    src_query = build_shallow_query(
        engine, src["catalog"], src["schema"], src["table"],
        metrics, where_clause=normalize_where_input(source_where),
    )
    tgt_query = build_shallow_query(
        "Databricks", tgt["catalog"], tgt["schema"], tgt["table"],
        metrics, where_clause=normalize_where_input(target_where),
    )
    src_res = normalize_result(execute_query(engine, source_conn, src_query)[0])
    tgt_res = normalize_result(execute_query("Databricks", target_conn, tgt_query)[0])

    src_count = src_res["row_count"]
    tgt_count = tgt_res["row_count"]

    if src_count == tgt_count:
        return True
    elif threshold is not None and threshold > 0:
        if src_count == 0 and tgt_count == 0:
            return True
        elif src_count == 0 or tgt_count == 0:
            match_ratio = 0.0
        else:
            match_ratio = min(src_count, tgt_count) / max(src_count, tgt_count)
        return match_ratio >= threshold
    else:
        return False


def run_schema_validation(
    engine, source_conn, target_conn, src, tgt, case_sensitive=False,
):
    src_schema = fetch_schema(engine, source_conn, src["catalog"], src["schema"], src["table"])
    tgt_schema = fetch_schema("Databricks", target_conn, tgt["catalog"], tgt["schema"], tgt["table"])

    src_df = normalize_schema_df(src_schema).rename(columns={"data_type": "source_type"})
    tgt_df = normalize_schema_df(tgt_schema).rename(columns={"data_type": "target_type"})

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
        type_match = src_normalized == tgt_normalized
        return "MATCH" if names_match and type_match else "NOT MATCH"

    cmp["status"] = cmp.apply(check_match, axis=1)
    mismatches = cmp[cmp["status"] == "NOT MATCH"]
    return mismatches.empty


def run_numeric_validation(
    engine, source_conn, target_conn, src, tgt,
    threshold=None, source_where="1=1", target_where="1=1",
):
    def _to_float_4(value):
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return None

    src_schema = fetch_schema(engine, source_conn, src["catalog"], src["schema"], src["table"])
    tgt_schema = fetch_schema("Databricks", target_conn, tgt["catalog"], tgt["schema"], tgt["table"])

    src_numeric = get_numeric_columns(src_schema)
    tgt_numeric = get_numeric_columns(tgt_schema)
    common = sorted(set(c.lower() for c in src_numeric) & set(c.lower() for c in tgt_numeric))

    if not common:
        logger.info("No common numeric columns found")
        return True

    all_pass = True
    for col in common:
        src_q = build_numeric_stats_query(
            engine, src["catalog"], src["schema"], src["table"], col,
            where_clause=normalize_where_input(source_where),
        )
        tgt_q = build_numeric_stats_query(
            "Databricks", tgt["catalog"], tgt["schema"], tgt["table"], col,
            where_clause=normalize_where_input(target_where),
        )
        src_res = normalize_result(execute_query(engine, source_conn, src_q)[0])
        tgt_res = normalize_result(execute_query("Databricks", target_conn, tgt_q)[0])

        for stat in ["min_val", "max_val", "avg_val"]:
            s = src_res.get(stat)
            t = tgt_res.get(stat)
            if s is None and t is None:
                continue
            if s is None or t is None:
                all_pass = False
                continue
            try:
                sf, tf = _to_float_4(s), _to_float_4(t)
                if sf is None or tf is None:
                    all_pass = False
                    continue
                if sf != tf:
                    if threshold and max(abs(sf), abs(tf)) > 0:
                        ratio = min(abs(sf), abs(tf)) / max(abs(sf), abs(tf))
                        if ratio < threshold:
                            all_pass = False
                    else:
                        all_pass = False
            except (ValueError, TypeError):
                if str(s) != str(t):
                    all_pass = False

    return all_pass


def run_row_hash_validation(
    engine, source_conn, target_conn, src, tgt,
    include_timestamp_columns=True, threshold=None,
    source_where="1=1", target_where="1=1",
    categorical_columns=None
):
    from validation_tool.query_builder import build_shallow_query
    # 1. Enforce 1M row limit
    metrics_shallow = {"row_count": True}
    src_count_query = build_shallow_query(engine, src["catalog"], src["schema"], src["table"], metrics_shallow, where_clause=normalize_where_input(source_where))
    src_count_res = execute_query(engine, source_conn, src_count_query)
    source_row_count = int(src_count_res[0].get("row_count", src_count_res[0].get("ROW_COUNT", 0))) if src_count_res else 0

    cat_cols_str = str(categorical_columns or "").strip()
    cat_cols = [c.strip() for c in cat_cols_str.split(",")] if cat_cols_str else []

    if source_row_count > 1000000 and not cat_cols:
        raise ValueError("Table has > 1,000,000 rows. Categorical Columns are required to optimize hash validation. Please select 1 or 2 categorical columns.")

    if cat_cols:
        from validation_tool.validation_core import validate_categorical_hash
        return validate_categorical_hash(
            engine,
            source_conn,
            target_conn,
            src,
            tgt,
            cat_cols,
            include_timestamp_columns,
            source_where=source_where,
            target_where=target_where
        )

    src_schema = fetch_schema(engine, source_conn, src["catalog"], src["schema"], src["table"])
    tgt_schema = fetch_schema("Databricks", target_conn, tgt["catalog"], tgt["schema"], tgt["table"])

    def get_hash(row):
        for k in ["hash_value", "HASH_VALUE"]:
            if k in row:
                return row[k]
        return None

    # Build column lists using common columns
    src_map = {str(r.get("column_name", "")).lower(): r for r in src_schema}
    tgt_map = {str(r.get("column_name", "")).lower(): r for r in tgt_schema}
    common_keys = sorted(set(src_map) & set(tgt_map))

    if not common_keys:
        return False

    src_columns = []
    tgt_columns = []
    for k in common_keys:
        s = src_map[k]
        t = tgt_map[k]
        dtype = str(s.get("data_type", "")).upper()
        if any(x in dtype for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
            continue
        if not include_timestamp_columns and ("TIMESTAMP" in dtype or "DATETIME" in dtype):
            continue
        src_columns.append({"name": s["column_name"], "type": dtype, "raw_type": s.get("data_type", "")})
        tgt_columns.append({"name": t["column_name"], "type": dtype, "raw_type": t.get("data_type", "")})

    if not src_columns:
        return False

    src_query = build_row_hash_query(
        engine, src["catalog"], src["schema"], src["table"],
        columns=src_columns, where_clause=normalize_where_input(source_where),
    )
    tgt_query = build_row_hash_query(
        "Databricks", tgt["catalog"], tgt["schema"], tgt["table"],
        columns=tgt_columns, where_clause=normalize_where_input(target_where),
    )

    src_rows = execute_query(engine, source_conn, src_query)
    tgt_rows = execute_query("Databricks", target_conn, tgt_query)

    src_hashes = {h for r in src_rows if (h := get_hash(r)) is not None}
    tgt_hashes = {h for r in tgt_rows if (h := get_hash(r)) is not None}

    if src_hashes == tgt_hashes:
        return True
    elif threshold is not None and threshold > 0:
        total_unique = len(src_hashes | tgt_hashes)
        matching = len(src_hashes & tgt_hashes)
        match_ratio = matching / total_unique if total_unique > 0 else 1.0
        return match_ratio >= threshold
    return False


# ══════════════════════════════════════
# RESULT GENERATION & PERSISTANCE
# ══════════════════════════════════════

def generate_validation_record(
    validation_type, src, tgt,
    row_selected, schema_selected, numeric_selected, hash_selected,
    run_by: str | None = None,
):
    return {
        "validation_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "src_table_name": f"{src['catalog']}.{src['schema']}.{src['table']}",
        "tgt_table_name": f"{tgt['catalog']}.{tgt['schema']}.{tgt['table']}",
        "validation_type": validation_type,
        "run_by": run_by,
        "row_count": row_selected if row_selected else None,
        "schema_check": schema_selected if schema_selected else None,
        "numeric_check": (
            "None" if validation_type == "shallow"
            else (numeric_selected if numeric_selected else None)
        ),
        "hash_validation": (
            "None" if validation_type == "shallow"
            else (hash_selected if hash_selected else None)
        ),
    }


def insert_validation_result(record):
    pg = None
    try:
        pg = get_dashboard_postgres_conn()
        try:
            ensure_validation_table(pg)
        except Exception as e:
            logger.warning(f"Could not create validation table: {e}")

        cur = pg.cursor()
        insert_sql = """
        INSERT INTO table_validation.validation_results (
            validation_id, validation_ts, src_table_name, tgt_table_name,
            validation_type, run_by, row_count, schema_check, numeric_check, hash_validation
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            record.get("validation_id"),
            record.get("timestamp"),
            record.get("src_table_name"),
            record.get("tgt_table_name"),
            record.get("validation_type"),
            record.get("run_by"),
            record.get("row_count"),
            record.get("schema_check"),
            record.get("numeric_check"),
            record.get("hash_validation"),
        )
        cur.execute(insert_sql, params)
        pg.commit()
        cur.close()
        return record.get("validation_id")
    except Exception as e:
        logger.error(f"Insert to Postgres failed: {e}")
        return None
    finally:
        if pg:
            try:
                pg.close()
            except Exception:
                pass


def run_checks_in_order(checks: list[tuple]) -> dict:
    """Run a list of (name, callable) checks and return results map."""
    results = {}
    for name, fn in checks:
        try:
            results[name] = fn()
        except Exception as e:
            logger.error(f"Check '{name}' failed: {e}")
            results[name] = False
    return results
