import logging
import uuid
import re
import hashlib
import json
import pandas as pd
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any, List

from google.oauth2 import service_account
from google.cloud import bigquery
import snowflake.connector
from databricks import sql as dbx_sql
import psycopg2
from psycopg2.extensions import connection as PgConnection

# Import query builders from existing file
from .query_builder import (
    build_shallow_query,
    build_schema_query,
    build_numeric_stats_query,
    build_row_value_query_v2,
    build_row_hash_query_v2,
    get_numeric_columns,
)
try:
    from .datatype_utils import (
        datatypes_compatible,
        normalize_datatype as canonical_normalize_datatype,
        canonicalize_compatible_type,
    )
except ImportError:
    from datatype_utils import (
        datatypes_compatible,
        normalize_datatype as canonical_normalize_datatype,
        canonicalize_compatible_type,
    )

# ---------- Logging setup ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Connection helpers ----------
def create_bigquery_connection(service_account_info: dict, project_id: str = None):
    """Create BigQuery client from service account JSON dict."""
    credentials = service_account.Credentials.from_service_account_info(service_account_info)
    return bigquery.Client(project=project_id or credentials.project_id, credentials=credentials)

def create_snowflake_connection(account: str, user: str, password: str, warehouse: str, role: str = None):
    """Create Snowflake connection."""
    return snowflake.connector.connect(
        account=account,
        user=user,
        password=password,
        warehouse=warehouse,
        role=role,
        autocommit=True
    )

def create_databricks_connection(server_hostname: str, http_path: str, access_token: str):
    """Create Databricks SQL connector connection."""
    return dbx_sql.connect(
        server_hostname=server_hostname,
        http_path=http_path,
        access_token=access_token
    )

def create_trino_connection(host=None, port=None, user=None, catalog=None, schema=None, http_scheme=None, password=None):
    """Create Trino DB-API connection."""
    from connections.trino import connect_trino

    return connect_trino(host, port, user, catalog, schema, http_scheme, password)

def get_dashboard_postgres_conn() -> PgConnection:
    """Return a psycopg2 connection to the Postgres dashboard DB."""
    import psycopg2
    return psycopg2.connect(
        host="sakthi.postgres.database.azure.com",
        port=5432,
        dbname="postgres",
        user="sakthi",
        password="Petchi@2811",
        sslmode="require"
    )

def ensure_validation_table(pg_conn: PgConnection):
    """Create schema and validation_results table if they don't exist."""
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

# ---------- Generic helpers ----------
def parse_table_path(path: str):
    """Parse 'catalog.schema.table' into (catalog, schema, table)."""
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

def bool_to_status(val: Optional[bool]) -> Optional[str]:
    """Convert boolean to PASS/FAIL."""
    if val is True:
        return "PASS"
    if val is False:
        return "FAIL"
    return None

def generate_validation_record(validation_type, src, tgt, row_status, schema_status, numeric_status, hash_status, run_by=None):
    """Return a dict ready for insert."""
    return {
        "validation_id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "src_table_name": f"{src['catalog']}.{src['schema']}.{src['table']}",
        "tgt_table_name": f"{tgt['catalog']}.{tgt['schema']}.{tgt['table']}",
        "validation_type": validation_type,
        "run_by": run_by,
        "row_count": row_status,
        "schema_check": schema_status,
        "numeric_check": numeric_status,
        "hash_validation": hash_status,
    }

def insert_validation_result(record: dict) -> str:
    """Insert record into Supabase and return validation_id."""
    try:
        from backend import supabase_store
        
        # Map validation_core record format to supabase_store format
        supabase_record = {
            "validation_id": record["validation_id"],
            "validation_ts": record.get("timestamp"),
            "src_table_name": record.get("src_table_name"),
            "tgt_table_name": record.get("tgt_table_name"),
            "validation_type": record.get("validation_type"),
            "run_by": record.get("run_by"),
            "row_count": record.get("row_count"),
            "schema_check": record.get("schema_check"),
            "numeric_check": record.get("numeric_check"),
            "hash_validation": record.get("hash_validation"),
        }
        
        supabase_store.upsert_results([supabase_record])
        return record["validation_id"]
    except Exception as e:
        logger.error(f"Supabase insert failed: {e}")
        raise

# ---------- Query execution & normalization ----------
def execute_query(engine: str, conn, query: str) -> List[dict]:
    """Execute query and return list of dicts (lowercase keys)."""
    engine = engine.lower()
    try:
        if engine == "bigquery":
            job = conn.query(query)
            rows = list(job.result())
            return [dict(row) for row in rows]
        elif engine in ["databricks", "snowflake", "trino"]:
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
    except Exception as e:
        logger.error(f"Query execution failed on {engine}: {e}\nQuery: {query}")
        raise

def normalize_result(row: dict) -> dict:
    """Convert all dict keys to lowercase."""
    return {k.lower(): v for k, v in row.items()}


def normalize_column_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = str(value).split(",")

    columns = []
    for item in raw_items:
        text = str(item).strip().strip("\"'`[]")
        if text:
            columns.append(text)
    return columns


def numeric_values_equal(left, right):
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError, TypeError):
        return str(left).strip() == str(right).strip()

def normalize_numeric(val):
    """Convert various numeric representations to float or None."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, str) and "Decimal" in val:
        cleaned = val.replace("Decimal(", "").replace("'", "").replace(")", "")
        try:
            return float(cleaned)
        except:
            return None
    try:
        return float(val)
    except:
        return None

def normalize_keys(d: dict) -> dict:
    return {k.lower(): v for k, v in d.items()}

# ---------- Data type normalization ----------
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
    "variant": "STRUCT",
    "object": "STRUCT",
    "array": "ARRAY",
}

def normalize_datatype(dtype: str, column_name: str = None) -> str:
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

# ---------- Schema helpers ----------
def normalize_schema_df(schema_rows: List[dict]) -> pd.DataFrame:
    if not schema_rows:
        raise ValueError("Schema query returned no rows")
    df = pd.DataFrame(schema_rows)
    df.columns = [c.lower() for c in df.columns]
    if "column_name" not in df.columns:
        for c in ["col_name", "name", "column"]:
            if c in df.columns:
                df["column_name"] = df[c]
                break
    if "data_type" not in df.columns:
        for c in ["type", "dtype"]:
            if c in df.columns:
                df["data_type"] = df[c]
                break
    missing = {"column_name", "data_type"} - set(df.columns)
    if missing:
        raise ValueError(f"Schema query missing columns: {missing}")
    return df[["column_name", "data_type"]]

def fetch_schema(engine: str, conn, catalog: str, schema: str, table: str) -> List[dict]:
    """Fetch schema rows for a table, keys lowercased."""
    schema_raw = execute_query(engine, conn, build_schema_query(engine, catalog, schema, table))
    return [normalize_result(r) for r in schema_raw]

# ---------- Core validations (return bool) ----------
def validate_row_count(engine: str, source_conn, target_conn, src: dict, tgt: dict) -> bool:
    metrics = {"row_count": True}
    src_query = build_shallow_query(engine, src["catalog"], src["schema"], src["table"], metrics)
    tgt_query = build_shallow_query("databricks", tgt["catalog"], tgt["schema"], tgt["table"], metrics)

    src_res = normalize_result(execute_query(engine, source_conn, src_query)[0])
    tgt_res = normalize_result(execute_query("databricks", target_conn, tgt_query)[0])

    return src_res["row_count"] == tgt_res["row_count"]

def validate_schema(engine: str, source_conn, target_conn, src: dict, tgt: dict, case_sensitive: bool = False) -> bool:
    src_schema = fetch_schema(engine, source_conn, src["catalog"], src["schema"], src["table"])
    tgt_schema = fetch_schema("databricks", target_conn, tgt["catalog"], tgt["schema"], tgt["table"])

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
        type_match = datatypes_compatible(row["source_type"], row["target_type"], col_name)
        return names_match and type_match

    cmp["match"] = cmp.apply(check_match, axis=1)
    return cmp["match"].all()

def approx_equal(a, b, tol=1e-6):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol

def validate_numeric(engine: str, source_conn, target_conn, src: dict, tgt: dict) -> bool | None:
    # Fetch source schema to identify numeric columns
    src_schema = fetch_schema(engine, source_conn, src["catalog"], src["schema"], src["table"])
    numeric_cols = get_numeric_columns(src_schema)   # from query_builder

    if not numeric_cols:
        return None

    all_pass = True
    for col in numeric_cols:
        src_stats = execute_query(
            engine, source_conn,
            build_numeric_stats_query(engine, src["catalog"], src["schema"], src["table"], col)
        )[0]
        tgt_stats = execute_query(
            "databricks", target_conn,
            build_numeric_stats_query("databricks", tgt["catalog"], tgt["schema"], tgt["table"], col)
        )[0]

        src_min = normalize_numeric(src_stats.get("min_val"))
        src_max = normalize_numeric(src_stats.get("max_val"))
        src_avg = normalize_numeric(src_stats.get("avg_val"))
        tgt_min = normalize_numeric(tgt_stats.get("min_val"))
        tgt_max = normalize_numeric(tgt_stats.get("max_val"))
        tgt_avg = normalize_numeric(tgt_stats.get("avg_val"))

        min_match = approx_equal(src_min, tgt_min)
        max_match = approx_equal(src_max, tgt_max)
        avg_match = approx_equal(src_avg, tgt_avg)

        if not (min_match and max_match and avg_match):
            all_pass = False
            logger.info(f"Numeric mismatch on column {col}: min {src_min} vs {tgt_min}, "
                        f"max {src_max} vs {tgt_max}, avg {src_avg} vs {tgt_avg}")
    return all_pass

def validate_row_hash(engine: str, source_conn, target_conn, src: dict, tgt: dict,
                      include_timestamp: bool = True) -> bool:
    # Fetch schemas for both tables
    src_schema = fetch_schema(engine, source_conn, src["catalog"], src["schema"], src["table"])
    tgt_schema = fetch_schema("databricks", target_conn, tgt["catalog"], tgt["schema"], tgt["table"])

    # Build column maps for filtering
    def build_colmap(schema_rows):
        m = {}
        for r in schema_rows:
            col = r.get("column_name")
            dtype = r.get("data_type")
            if not col:
                continue
            key = str(col).lower()
            m[key] = {
                "name": col,
                "type": normalize_datatype(dtype, col),
                "raw_type": dtype,
            }
        return m

    src_map = build_colmap(src_schema)
    tgt_map = build_colmap(tgt_schema)

    common_keys = sorted(set(src_map.keys()) & set(tgt_map.keys()))
    if not common_keys:
        logger.error("No common columns for hashing")
        return False

    # Build list of columns to include
    src_columns = []
    tgt_columns = []
    for k in common_keys:
        s = src_map[k]
        t = tgt_map[k]

        canon = canonicalize_compatible_type(s.get("raw_type"), t.get("raw_type"), s.get("name"))
        if canon == "COMPLEX":
            continue

        s["type"] = canon
        t["type"] = canon
        if canon == "STRING":
            s["raw_type"] = "STRING"
            t["raw_type"] = "STRING"
        else:
            s["raw_type"] = canon
            t["raw_type"] = canon

        if (not include_timestamp) and (canon == "TIMESTAMP"):
            continue

        src_columns.append(s)
        tgt_columns.append(t)

    if not src_columns:
        logger.error("No columns left after filtering")
        return False

    # Build normalized row-value queries so key order and array order can be canonicalized in Python.
    src_schema_for_hash = [{"column_name": c["name"], "data_type": c["raw_type"] or c["type"]} for c in src_columns]
    tgt_schema_for_hash = [{"column_name": c["name"], "data_type": c["raw_type"] or c["type"]} for c in tgt_columns]

    src_query = build_row_value_query_v2(
        engine, src["catalog"], src["schema"], src["table"],
        schema_rows=src_schema_for_hash, include_timestamp=include_timestamp
    )
    tgt_query = build_row_value_query_v2(
        "databricks", tgt["catalog"], tgt["schema"], tgt["table"],
        schema_rows=tgt_schema_for_hash, include_timestamp=include_timestamp
    )

    def _normalize_hash_scalar(value):
        if value is None:
            return "<NULL>"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float, Decimal)):
            return str(value).strip()
        if isinstance(value, (list, tuple, set)):
            items = sorted((_normalize_hash_scalar(item) for item in value), key=lambda item: item.lower())
            return f"[{','.join(items)}]"
        if isinstance(value, dict):
            parts = []
            for key in sorted(value.keys(), key=lambda item: str(item).strip().lower()):
                normalized_key = str(key).strip()
                normalized_value = _normalize_hash_scalar(value[key])
                parts.append(f"{json.dumps(normalized_key, ensure_ascii=False, separators=(',', ':'))}:{normalized_value}")
            return f"{{{','.join(parts)}}}"

        text = str(value).strip()
        if not text:
            return ""

        try:
            parsed = json.loads(text)
        except Exception:
            if "," in text:
                items = [item.strip() for item in text.split(",") if item.strip()]
                if len(items) > 1:
                    return ",".join(sorted(items, key=lambda item: item.lower()))
            return text

        return _normalize_hash_scalar(parsed)

    def _row_hash_from_values(row: dict, value_columns: list[str]) -> str:
        signature_parts = [_normalize_hash_scalar(row.get(column)) for column in value_columns]
        signature = "|".join(signature_parts)
        return hashlib.md5(signature.encode("utf-8")).hexdigest().upper()

    value_columns = [f"col_{i + 1}" for i in range(len(src_columns))]

    src_hashes = Counter(_row_hash_from_values(row, value_columns) for row in execute_query(engine, source_conn, src_query))
    tgt_hashes = Counter(_row_hash_from_values(row, value_columns) for row in execute_query("databricks", target_conn, tgt_query))

    return bool(src_hashes) and bool(tgt_hashes) and src_hashes == tgt_hashes


def _normalize_where_clause(where_value, default="1=1"):
    if where_value is None:
        return default
    text = str(where_value).strip()
    return text if text else default


def validate_categorical_hash(
    engine: str,
    source_conn,
    target_conn,
    src: dict,
    tgt: dict,
    categorical_columns: list,
    include_timestamp: bool = True,
    source_where: str = "1=1",
    target_where: str = "1=1",
) -> bool:
    from .query_builder import build_categorical_hash_query

    categorical_columns = normalize_column_list(categorical_columns)
    if not categorical_columns:
        logger.error("No categorical columns provided for categorical hashing")
        return False

    src_schema = fetch_schema(engine, source_conn, src["catalog"], src["schema"], src["table"])
    tgt_schema = fetch_schema("databricks", target_conn, tgt["catalog"], tgt["schema"], tgt["table"])

    def build_colmap(schema_rows):
        m = {}
        for r in schema_rows:
            col = r.get("column_name")
            dtype = r.get("data_type")
            if not col:
                continue
            key = str(col).lower()
            m[key] = {
                "name": col,
                "type": normalize_datatype(dtype, col),
                "raw_type": dtype,
            }
        return m

    src_map = build_colmap(src_schema)
    tgt_map = build_colmap(tgt_schema)

    common_keys = sorted(set(src_map.keys()) & set(tgt_map.keys()))
    if not common_keys:
        logger.error("No common columns for categorical hashing")
        return False

    src_columns = []
    tgt_columns = []
    for k in common_keys:
        s = src_map[k]
        t = tgt_map[k]

        canon = canonicalize_compatible_type(s.get("raw_type"), t.get("raw_type"), s.get("name"))
        if canon == "COMPLEX":
            continue

        s["type"] = canon
        t["type"] = canon
        if canon == "STRING":
            s = {**s, "raw_type": "STRING"}
            t = {**t, "raw_type": "STRING"}
        else:
            s = {**s, "raw_type": canon}
            t = {**t, "raw_type": canon}

        if (not include_timestamp) and (canon == "TIMESTAMP"):
            continue

        src_columns.append(s)
        tgt_columns.append(t)

    if not src_columns:
        logger.error("No columns left after filtering for categorical hashing")
        return False

    src_schema_for_hash = [{"column_name": c["name"], "data_type": c["raw_type"] or c["type"]} for c in src_columns]
    tgt_schema_for_hash = [{"column_name": c["name"], "data_type": c["raw_type"] or c["type"]} for c in tgt_columns]

    src_query = build_categorical_hash_query(
        engine, src["catalog"], src["schema"], src["table"],
        schema_rows=src_schema_for_hash,
        categorical_columns=categorical_columns,
        include_timestamp=include_timestamp,
        where_clause=_normalize_where_clause(source_where),
    )
    tgt_query = build_categorical_hash_query(
        "databricks", tgt["catalog"], tgt["schema"], tgt["table"],
        schema_rows=tgt_schema_for_hash,
        categorical_columns=categorical_columns,
        include_timestamp=include_timestamp,
        where_clause=_normalize_where_clause(target_where),
    )

    src_res = execute_query(engine, source_conn, src_query)
    tgt_res = execute_query("databricks", target_conn, tgt_query)

    def normalize_results(res):
        norm = {}
        for row in res:
            row_norm = normalize_result(row)
            # Create a tuple of group keys
            key_tuple = tuple(str(row_norm.get(f"group_key_{i+1}", "")).strip().lower() for i in range(len(categorical_columns)))
            norm[key_tuple] = {
                "row_count": row_norm.get("row_count"),
                "group_hash_sum": str(row_norm.get("group_hash_sum") or "")
            }
        return norm

    src_norm = normalize_results(src_res)
    tgt_norm = normalize_results(tgt_res)
    if not src_norm or not tgt_norm:
        return False

    match = True
    for key, src_val in src_norm.items():
        if key not in tgt_norm:
            logger.info(f"Group {key} missing in target")
            match = False
            continue
        tgt_val = tgt_norm[key]
        if not numeric_values_equal(src_val["row_count"], tgt_val["row_count"]):
            logger.info(f"Group {key} row count mismatch: Source={src_val['row_count']}, Target={tgt_val['row_count']}")
            match = False
        if not numeric_values_equal(src_val["group_hash_sum"], tgt_val["group_hash_sum"]):
            logger.info(f"Group {key} hash sum mismatch: Source={src_val['group_hash_sum']}, Target={tgt_val['group_hash_sum']}")
            match = False

    for key in tgt_norm:
        if key not in src_norm:
            logger.info(f"Group {key} missing in source")
            match = False

    return match
