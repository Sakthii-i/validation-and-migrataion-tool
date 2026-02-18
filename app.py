
import streamlit as st
import pandas as pd
import uuid
from datetime import datetime
from connections.bigquery import connect_bigquery
from connections.databricks import connect_databricks
from connections.snowflake import connect_snowflake
from metadata.catalog_fetcher import get_catalogs, get_schemas, get_tables
from query_builder import build_shallow_query, build_schema_query, get_numeric_columns,build_numeric_stats_query,build_row_hash_query, build_row_signature_sample_query, build_row_hash_mismatch_rows_query_v2
import os
os.getenv("DASHBOARD_DBX_TOKEN")
import plotly.express as px
import base64
import json




DEFAULT_CONFIG = {
  "validation_framework": {
    "default": {
      "validation_type": "shallow",
      "metrics": ["row_count", "schema"]
    },
    "tables": {
      "product_data": {
        "input_table": {
          "source": "efundamentals.product_data",
          "target": "common_catalog.product_data"
        },
        "validation_type": "deep",
        "metrics": ["row_count", "schema", "hash", "numeric"]
      },
      "sales_data": {
        "input_table": {
          "source": "efundamentals.sales_data",
          "target": "common_catalog.sales_data"
        },
        "validation_type": "shallow",
        "metrics": ["row_count"]
      },
      "inventory_data": {
        "input_table": {
          "source": "efundamentals.inventory_data",
          "target": "common_catalog.inventory_data"
        },
        "metrics": ["all"]
      }
    }
  }
}


#from validation_utils import run_shallow_validation
#from query_builder import build_checksum_query
if "page" not in st.session_state:
    st.session_state["page"] = "validation"
if "show_pie_stats" not in st.session_state:
    st.session_state["show_pie_stats"] = False
# =============================
# PAGE ROUTING STATE
# =============================
if "active_page" not in st.session_state:
    st.session_state["active_page"] = "main"
if "case_sensitive_schema" not in st.session_state:
    st.session_state["case_sensitive_schema"] = False

if "browse_case_sensitive_global" not in st.session_state:
    st.session_state["browse_case_sensitive_global"] = False
if "manual_case_sensitive_global" not in st.session_state:
    st.session_state["manual_case_sensitive_global"] = False
if "csv_case_sensitive_global" not in st.session_state:
    st.session_state["csv_case_sensitive_global"] = False
if "config_case_sensitive_global" not in st.session_state:
    st.session_state["config_case_sensitive_global"] = False


def render_pie_chart(title, passed, failed):
    df = pd.DataFrame({
        "Status": ["PASS", "FAIL"],
        "Count": [passed, failed]
    })

    fig = px.pie(
        df,
        names="Status",
        values="Count",
        title=title,
        color="Status",
        color_discrete_map={
            "PASS": "#2ecc71",
            "FAIL": "#e74c3c"
        }
    )

    fig.update_traces(textinfo="percent+label")
    fig.update_layout(height=300, margin=dict(t=40, b=0))

    st.plotly_chart(fig, use_container_width=True)

def get_dashboard_dbx_conn():
    """
    Static Databricks connection for dashboard metrics
    (Independent of validation UI)
    """
    DASHBOARD_DBX_HOST = "dbc-a3d893f2-6b53.cloud.databricks.com"
    DASHBOARD_DBX_HTTP_PATH = "/sql/1.0/warehouses/24e7458d4f627152"
    DASHBOARD_DBX_TOKEN = "dapi391b3aef63658eb332cc27f165e24ade"

    return connect_databricks(
        DASHBOARD_DBX_HOST,
        DASHBOARD_DBX_HTTP_PATH,
        DASHBOARD_DBX_TOKEN
    )


# ✅ ADD THIS
def missing(values):
    return any(v is None or v == "" for v in values)

def execute_query(engine, conn, query):
    """
    Executes query and returns result as dict / list of dicts
    """
    engine = engine.lower()

    if engine == "bigquery":
        job = conn.query(query)
        rows = list(job.result())
        return [dict(row) for row in rows]

    elif engine in ["databricks", "snowflake"]:
        cur = conn.cursor()
        cur.execute(query)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
        cur.close()
        return [dict(zip(cols, row)) for row in rows]

    else:
        raise ValueError(f"Unsupported engine: {engine}")

def normalize_result(row: dict):
    """
    Normalizes query result keys to lowercase
    to handle engine-specific casing differences
    """
    return {k.lower(): v for k, v in row.items()}

st.set_page_config(page_title="Reconciliation Framework", layout="wide")
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
    
    # VARIANT - Your target expects STRUCT
    "variant": "STRUCT",
    
    # Object/Array
    "object": "STRUCT",
    "array": "ARRAY",
}

def normalize_datatype(dtype, column_name=None):
    """
    Normalize data type with column-specific logic for NUMBER types
    """
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
        col_lower = column_name.lower()
        # Columns that should be INT (identifiers, counts, ages)
        int_indicators = ['id', 'age', 'count', 'num', 'idx', 'index', 'row', 'key']
        if any(ind in col_lower for ind in int_indicators):
            return "INT"
        else:
            return "DECIMAL"
    
    # Default mapping lookup
    return DATA_TYPE_EQUIVALENCE.get(base_type, base_type.upper())

def parse_table_path(path: str):
    """
    Parses catalog.schema.table
    Returns (catalog, schema, table) or (None, None, None)
    """
    if not path:
        return None, None, None

    parts = [p.strip() for p in path.split(".")]
    if len(parts) != 3:
        return None, None, None

    return parts[0], parts[1], parts[2]



def load_icon(path):
    with open(path, "rb") as f:
        data = f.read()
    return base64.b64encode(data).decode()

def generate_validation_record(
    validation_type,
    src,
    tgt,
    row_selected,
    schema_selected,
    numeric_selected,
    hash_selected
):
    """
    Returns a dict ready for insert
    """
    return {
        "validation_id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "src_table_name": f"{src['catalog']}.{src['schema']}.{src['table']}",
        "tgt_table_name": f"{tgt['catalog']}.{tgt['schema']}.{tgt['table']}",
        "validation_type": validation_type,
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
def insert_validation_result(conn, record):
    conn = get_dashboard_dbx_conn()
    cur = conn.cursor()
    """
    Inserts validation metadata into Databricks table
    """
    insert_sql = f"""
    INSERT INTO table_validation.results.validation_results
    VALUES (
        '{record["validation_id"]}',
        '{record["timestamp"]}',
        '{record["src_table_name"]}',
        '{record["tgt_table_name"]}',
        '{record["validation_type"]}',
        {sql_value(record["row_count"])},
        {sql_value(record["schema_check"])},
        {sql_value(record["numeric_check"])},
        {sql_value(record["hash_validation"])}
    )
    """

    cur.execute(insert_sql)
    cur.close()

def sql_value(val):
    if val is None:
        return "NULL"
    return f"'{val}'"

def bool_to_status(val):
    if val is True:
        return "PASS"
    if val is False:
        return "FAIL"
    return None

def validate_csv(df):
    required_cols = {
        "source_catalog", "source_schema", "source_table",
        "target_catalog", "target_schema", "target_table",
        "validation_type"
    }

    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    invalid_types = df[
        ~df["validation_type"].str.lower().isin(["shallow", "deep"])
    ]

    if not invalid_types.empty:
        raise ValueError("validation_type must be shallow or deep")

        
def run_browse_validations(
    source_selections,
    target_selections,
    validation_type,
    include_timestamp_columns=True,
    case_sensitive=False,
    selected_validations=None,
):
    if selected_validations is None:
        selected_validations = {
            "row_count": True,
            "schema": True,
            "numeric": True,
            "hash": True,
        }
    if not source_selections or not target_selections:
        raise Exception("Source and Target selections cannot be empty")

    if len(source_selections) != len(target_selections):
        raise Exception(
            "Number of source tables must match number of target tables"
        )

    for idx, (src, tgt) in enumerate(zip(source_selections, target_selections)):
        st.markdown(f"### ▶ Processing table pair {idx + 1}")

        st.code(
            f"SRC: {src['catalog']}.{src['schema']}.{src['table']}\n"
            f"TGT: {tgt['catalog']}.{tgt['schema']}.{tgt['table']}"
        )

        if validation_type == "shallow":
            row_res = (
                run_row_count(
                    st.session_state["engine"],
                    st.session_state["source_conn"],
                    st.session_state["target_conn"],
                    src, tgt
                )
                if selected_validations.get("row_count")
                else None
            )

            schema_res = (
                run_schema_validation(
                    st.session_state["engine"],
                    st.session_state["source_conn"],
                    st.session_state["target_conn"],
                    src, tgt,
                    case_sensitive=case_sensitive,
                )
                if selected_validations.get("schema")
                else None
            )

            record = generate_validation_record(
                "shallow",
                src, tgt,
                bool_to_status(row_res),
                bool_to_status(schema_res),
                "N/A",
                "N/A"
            )

        else:  # deep
            checks = []
            if selected_validations.get("row_count"):
                checks.append((
                    "Row Count Validation",
                    lambda: run_row_count(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt
                    )
                ))
            if selected_validations.get("schema"):
                checks.append((
                    "Schema Validation",
                    lambda: run_schema_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt,
                        case_sensitive=case_sensitive,
                    )
                ))
            if selected_validations.get("numeric"):
                checks.append((
                    "Numeric Statistics Validation",
                    lambda: run_numeric_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt
                    )
                ))
            if selected_validations.get("hash"):
                checks.append((
                    "Row Hash Validation",
                    lambda: run_row_hash_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src,
                        tgt,
                        include_timestamp_columns=include_timestamp_columns,
                    )
                ))

            results_map = run_checks_in_order(checks)

            record = generate_validation_record(
                "deep",
                src, tgt,
                bool_to_status(results_map.get("Row Count Validation")),
                bool_to_status(results_map.get("Schema Validation")),
                bool_to_status(results_map.get("Numeric Statistics Validation")),
                bool_to_status(results_map.get("Row Hash Validation")),
            )

        insert_validation_result(
            st.session_state["target_conn"],
            record
        )

        st.success("✅ Validation completed")

def run_csv_validations(df):
    for idx, row in df.iterrows():
        st.markdown(f"### ▶ Processing row {idx + 1}")

        src = {
            "catalog": row["source_catalog"],
            "schema": row["source_schema"],
            "table": row["source_table"]
        }

        tgt = {
            "catalog": row["target_catalog"],
            "schema": row["target_schema"],
            "table": row["target_table"]
        }

        validation_type = row["validation_type"].lower()

        selected_validations = {
            "row_count": st.session_state.get("include_row_count_csv", True),
            "schema": st.session_state.get("include_schema_csv", True),
            "numeric": st.session_state.get("include_numeric_csv", True),
            "hash": st.session_state.get("include_hash_csv", True),
        }

        case_sensitive = st.session_state.get("csv_case_sensitive_global", False)

        if validation_type == "shallow":
            if not (
                selected_validations.get("row_count")
                or selected_validations.get("schema")
            ):
                st.warning("⚠️ No validation methods selected; skipping")
                continue

            row_res = (
                run_row_count(
                    st.session_state["engine"],
                    st.session_state["source_conn"],
                    st.session_state["target_conn"],
                    src, tgt
                )
                if selected_validations.get("row_count")
                else None
            )

            schema_res = (
                run_schema_validation(
                    st.session_state["engine"],
                    st.session_state["source_conn"],
                    st.session_state["target_conn"],
                    src, tgt,
                    case_sensitive=case_sensitive,
                )
                if selected_validations.get("schema")
                else None
            )

            record = generate_validation_record(
                "shallow",
                src, tgt,
                bool_to_status(row_res),
                bool_to_status(schema_res),
                "N/A",
                "N/A"
            )

        else:  # deep
            checks = []
            if selected_validations.get("row_count"):
                checks.append((
                    "Row Count Validation",
                    lambda: run_row_count(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt
                    )
                ))
            if selected_validations.get("schema"):
                checks.append((
                    "Schema Validation",
                    lambda: run_schema_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt,
                        case_sensitive=case_sensitive,
                    )
                ))
            if selected_validations.get("numeric"):
                checks.append((
                    "Numeric Statistics Validation",
                    lambda: run_numeric_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt
                    )
                ))
            if selected_validations.get("hash"):
                checks.append((
                    "Row Hash Validation",
                    lambda: run_row_hash_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src,
                        tgt,
                        include_timestamp_columns=st.session_state.get(
                            "include_timestamp_in_hash_csv", True
                        ),
                    )
                ))

            if not checks:
                st.warning("⚠️ No validation methods selected; skipping")
                continue

            results_map = run_checks_in_order(checks)

            record = generate_validation_record(
                "deep",
                src, tgt,
                bool_to_status(results_map.get("Row Count Validation")),
                bool_to_status(results_map.get("Schema Validation")),
                bool_to_status(results_map.get("Numeric Statistics Validation")),
                bool_to_status(results_map.get("Row Hash Validation")),
            )

        insert_validation_result(
            st.session_state["target_conn"],
            record
        )

        st.success("✅ Validation completed")
def run_browse_validations(
    source_selections,
    target_selections,
    validation_type,
    include_timestamp_columns=True,
    case_sensitive=False,
    selected_validations=None,
):
    if selected_validations is None:
        selected_validations = {
            "row_count": True,
            "schema": True,
            "numeric": True,
            "hash": True,
        }
    if not source_selections or not target_selections:
        raise ValueError("Source and Target selections cannot be empty")

    if len(source_selections) != len(target_selections):
        raise ValueError(
            "Number of source tables must match number of target tables"
        )

    for idx, (src, tgt) in enumerate(zip(source_selections, target_selections)):
        st.markdown(f"### ▶ Processing table pair {idx + 1}")

        if validation_type == "shallow":
            row_res = (
                run_row_count(
                    st.session_state["engine"],
                    st.session_state["source_conn"],
                    st.session_state["target_conn"],
                    src, tgt
                )
                if selected_validations.get("row_count")
                else None
            )

            schema_res = (
                run_schema_validation(
                    st.session_state["engine"],
                    st.session_state["source_conn"],
                    st.session_state["target_conn"],
                    src, tgt,
                    case_sensitive=case_sensitive,
                )
                if selected_validations.get("schema")
                else None
            )

            record = generate_validation_record(
                "shallow",
                src, tgt,
                bool_to_status(row_res),
                bool_to_status(schema_res),
                "N/A",
                "N/A"
            )

        else:  # deep
            checks = []
            if selected_validations.get("row_count"):
                checks.append((
                    "Row Count Validation",
                    lambda: run_row_count(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt
                    )
                ))
            if selected_validations.get("schema"):
                checks.append((
                    "Schema Validation",
                    lambda: run_schema_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt,
                        case_sensitive=case_sensitive,
                    )
                ))
            if selected_validations.get("numeric"):
                checks.append((
                    "Numeric Statistics Validation",
                    lambda: run_numeric_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt
                    )
                ))
            if selected_validations.get("hash"):
                checks.append((
                    "Row Hash Validation",
                    lambda: run_row_hash_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src,
                        tgt,
                        include_timestamp_columns=include_timestamp_columns,
                    )
                ))

            results_map = run_checks_in_order(checks)

            record = generate_validation_record(
                "deep",
                src, tgt,
                bool_to_status(results_map.get("Row Count Validation")),
                bool_to_status(results_map.get("Schema Validation")),
                bool_to_status(results_map.get("Numeric Statistics Validation")),
                bool_to_status(results_map.get("Row Hash Validation")),
            )

        insert_validation_result(
            st.session_state["target_conn"],
            record
        )

        st.success(
            f"✅ Validation completed for "
            f"{src['catalog']}.{src['schema']}.{src['table']} → "
            f"{tgt['catalog']}.{tgt['schema']}.{tgt['table']}"
        )

# =========================================================
# GLOBAL STYLES
# =========================================================
def parse_config_tables(config):
    cfg = config["validation_framework"]

    default_type = cfg["default"]["validation_type"]
    default_metrics = cfg["default"]["metrics"]

    parsed = []

    for name, table_cfg in cfg["tables"].items():
        input_table = table_cfg["input_table"]

        validation_type = table_cfg.get("validation_type", default_type)
        metrics = table_cfg.get("metrics", default_metrics)

        if "all" in metrics:
            metrics = ["row_count", "schema", "hash", "numeric"]

        parsed.append({
            "name": name,
            "source": input_table["source"],
            "target": input_table["target"],
            "validation_type": validation_type,
            "metrics": metrics
        })

    return parsed

# =========================================================
# RECONCILIATION READINESS
# =========================================================
def run_shallow_validation(engine, source_conn, target_conn, src_sel, tgt_sel):
    st.info("Select Validation Metrics")
    key="schema_check"

    metrics = {"row_count": row_count_check}

    if not any(metrics.values()) and not schema_check:
        st.warning("⚠️ Select at least one validation")
        return

    if not st.button("🚀 Run Validation", use_container_width=True):
        return

    # =============================
    # ROW COUNT VALIDATION
    # =============================
def run_row_count(engine, source_conn, target_conn, src, tgt):
    metrics = {"row_count": True}

    src_query = build_shallow_query(
        engine,
        src["catalog"],
        src["schema"],
        src["table"],
        metrics
    )

    tgt_query = build_shallow_query(
        "Databricks",
        tgt["catalog"],
        tgt["schema"],
        tgt["table"],
        metrics
    )

    src_res = normalize_result(
        execute_query(engine, source_conn, src_query)[0]
    )

    tgt_res = normalize_result(
        execute_query("Databricks", target_conn, tgt_query)[0]
    )

    #st.subheader("📊 Row Count Validation")
    c1, c2, c3 = st.columns(3)
    c1.metric("Source", src_res["row_count"])
    c2.metric("Target", tgt_res["row_count"])

    if src_res["row_count"] == tgt_res["row_count"]:
        c3.success("✅ PASS")
        return True
    else:
        c3.error("❌ FAIL")
        return False
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
        raise ValueError(
            f"Schema query returned unexpected columns: {df.columns.tolist()}"
        )

    return df[["column_name", "data_type"]]

def safe_dtype(row):
    s = row.get("data_type_x")
    t = row.get("data_type_y")
    return "MATCH" if normalize_datatype(s) == normalize_datatype(t) else "NOT MATCH"

#cmp["status"] = cmp.apply(safe_dtype, axis=1)

    # =============================
    # SCHEMA VALIDATION
    # =============================
def run_schema_validation(
    engine,
    source_conn,
    target_conn,
    src,
    tgt,
    case_sensitive=False
):

    src_schema = fetch_schema(
        engine, source_conn, src["catalog"], src["schema"], src["table"]
    )
    tgt_schema = fetch_schema(
        "Databricks", target_conn, tgt["catalog"], tgt["schema"], tgt["table"]
    )

    src_df = normalize_schema_df(src_schema).rename(
        columns={"data_type": "source_type"}
    )

    tgt_df = normalize_schema_df(tgt_schema).rename(
        columns={"data_type": "target_type"}
    )


    # 🔑 CASE HANDLING
    if not case_sensitive:
        src_df["join_col"] = src_df["column_name"].str.lower()
        tgt_df["join_col"] = tgt_df["column_name"].str.lower()
    else:
        src_df["join_col"] = src_df["column_name"]
        tgt_df["join_col"] = tgt_df["column_name"]

    cmp = src_df.merge(
        tgt_df,
        on="join_col",
        how="outer",
        suffixes=("_src", "_tgt")
    )

    # Column-aware type matching
    def check_match(row):
        # Name match check
        if case_sensitive:
            names_match = row["column_name_src"] == row["column_name_tgt"]
        else:
            names_match = str(row["column_name_src"]).lower() == str(row["column_name_tgt"]).lower()
        
        # Type match check with column context
        col_name = row.get("column_name_src") or row.get("join_col")
        src_normalized = normalize_datatype(row["source_type"], col_name)
        tgt_normalized = normalize_datatype(row["target_type"], col_name)
        
        type_match = src_normalized == tgt_normalized
        
        return "MATCH" if names_match and type_match else "NOT MATCH"

    cmp["status"] = cmp.apply(check_match, axis=1)

    # Show full comparison table
    st.dataframe(
        cmp[[
            "column_name_src",
            "column_name_tgt",
            "source_type",
            "target_type",
            "status"
        ]],
        use_container_width=True
    )

    # Show mismatch details if any
    mismatches = cmp[cmp["status"] == "NOT MATCH"]
    
    if not mismatches.empty:
        st.error(f"❌ Schema mismatches found: {len(mismatches)} column(s)")
        
        # Detailed mismatch breakdown
        for idx, row in mismatches.iterrows():
            col_src = row.get("column_name_src") or "MISSING"
            col_tgt = row.get("column_name_tgt") or "MISSING"
            type_src = row.get("source_type") or "N/A"
            type_tgt = row.get("target_type") or "N/A"
            
            # Determine specific mismatch reason
            col_name = row.get("column_name_src") or row.get("join_col")
            src_norm = normalize_datatype(type_src, col_name) if type_src != "N/A" else "N/A"
            tgt_norm = normalize_datatype(type_tgt, col_name) if type_tgt != "N/A" else "N/A"
            
            if col_src == "MISSING":
                reason = f"Column '{col_tgt}' exists in target but NOT in source"
            elif col_tgt == "MISSING":
                reason = f"Column '{col_src}' exists in source but NOT in target"
            elif col_src.lower() != col_tgt.lower():
                reason = f"Name mismatch: '{col_src}' vs '{col_tgt}'"
            elif src_norm != tgt_norm:
                reason = f"Type mismatch: {type_src} ({src_norm}) vs {type_tgt} ({tgt_norm})"
            else:
                reason = "Unknown mismatch"
            
            st.markdown(f"• **{col_src}** → **{col_tgt}**: {reason}")
        
        return False
    else:
        st.success("✅ Schema matches - All columns compatible")
        return True



def run_numeric_stats(engine, conn, catalog, schema, table, numeric_cols):
    stats = {}

    for col in numeric_cols:
        query = build_numeric_stats_query(
            engine, catalog, schema, table, col
        )
        res = execute_query(engine, conn, query)[0]
        stats[col] = res

    return stats

# =============================
# ROW HASH VALIDATION
# =============================
def get_hash(row):
    if isinstance(row, dict):
        val = row.get("hash_value") or row.get("HASH_VALUE")
    else:
        val = row[0]  # Databricks tuple

    if val is None:
        return None

    if isinstance(val, (bytes, bytearray)):
        try:
            val = val.decode("utf-8")
        except Exception:
            val = str(val)

    return str(val).strip().lower()

def run_row_hash_validation(
    engine,
    source_conn,
    target_conn,
    src,
    tgt,
    include_timestamp_columns=None,
):
    st.subheader("🔐 Row Hash Validation")

    if include_timestamp_columns is None:
        include_timestamp_columns = True

    src_schema_rows = fetch_schema(
        engine,
        source_conn,
        src["catalog"],
        src["schema"],
        src["table"],
    )

    tgt_schema_rows = fetch_schema(
        "Databricks",
        target_conn,
        tgt["catalog"],
        tgt["schema"],
        tgt["table"],
    )

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

    src_map = build_colmap(src_schema_rows)
    tgt_map = build_colmap(tgt_schema_rows)

    common_keys = sorted(set(src_map.keys()) & set(tgt_map.keys()))
    if not common_keys:
        st.error(
            "❌ Row hash cannot run: no common columns between source and target"
        )
        return False

    schema_excluded = sorted(set(src_map.keys()) ^ set(tgt_map.keys()))
    timestamp_excluded = []

    def canonical_type(src_type: str, tgt_type: str) -> str:
        s = (src_type or "").upper()
        t = (tgt_type or "").upper()
        pair = {s, t}

        if "TIMESTAMP" in pair:
            return "TIMESTAMP"
        if "DATE" in pair:
            return "DATE"
        if "BOOLEAN" in pair:
            return "BOOLEAN"
        if "BINARY" in pair:
            return "BINARY"

        # Preserve complex types when both sides support them.
        if "STRUCT" in pair or "ARRAY" in pair:
            return "STRUCT" if "STRUCT" in pair else "ARRAY"

        if "DOUBLE" in pair:
            return "DOUBLE"
        if "DECIMAL" in pair:
            return "DECIMAL"
        if "INT" in pair:
            return "INT"
        if "STRING" in pair:
            return "STRING"

        # Fallback: prefer source normalized type, else target.
        return s or t or "STRING"

    src_columns = []
    tgt_columns = []
    for k in common_keys:
        s = src_map[k]
        t = tgt_map[k]

        # Force both sides to use the same canonical datatype
        canon = canonical_type(s.get("type"), t.get("type"))
        s = {"name": s.get("name"), "type": canon, "raw_type": s.get("raw_type")}
        t = {"name": t.get("name"), "type": canon, "raw_type": t.get("raw_type")}

        if (not include_timestamp_columns) and (
            s.get("type") == "TIMESTAMP" or t.get("type") == "TIMESTAMP"
        ):
            timestamp_excluded.append(k)
            continue

        src_columns.append(s)
        tgt_columns.append(t)

    if not src_columns or not tgt_columns:
        st.error(
            "❌ Row hash cannot run: no columns available after filtering"
        )
        return False

    if (not include_timestamp_columns) and timestamp_excluded:
        with st.expander(
            f"Excluded TIMESTAMP columns ({len(timestamp_excluded)})"
        ):
            st.write(", ".join(timestamp_excluded))


    if schema_excluded:
        with st.expander(
            f"Excluded non-common columns ({len(schema_excluded)})"
        ):
            st.write(", ".join(schema_excluded))

    # Build queries
    src_query = build_row_hash_query(
        engine,
        src["catalog"],
        src["schema"],
        src["table"],
        columns=src_columns,
    )

    tgt_query = build_row_hash_query(
        "Databricks",
        tgt["catalog"],
        tgt["schema"],
        tgt["table"],
        columns=tgt_columns,
    )

    with st.spinner("Computing row hashes..."):
        src_rows = execute_query(engine, source_conn, src_query)
        tgt_rows = execute_query("Databricks", target_conn, tgt_query)

    src_hashes = {h for r in src_rows if (h := get_hash(r)) is not None}
    tgt_hashes = {h for r in tgt_rows if (h := get_hash(r)) is not None}

    c1, c2, c3 = st.columns(3)
    c1.metric("Source Hash Count", len(src_hashes))
    c2.metric("Target Hash Count", len(tgt_hashes))

    if src_hashes == tgt_hashes:
        c3.success("✅ HASH MATCH")
        return True
    else:
        c3.error("❌ HASH MISMATCH")
        
        # Detailed mismatch analysis
        missing_in_target = src_hashes - tgt_hashes
        extra_in_target = tgt_hashes - src_hashes
        
        st.error("❌ Hash Validation Failed")

        m1, m2 = st.columns(2)
        m1.metric("Missing in Target", len(missing_in_target))
        m2.metric("Extra in Target", len(extra_in_target))
        
        # Summary statistics
        total_unique = len(src_hashes | tgt_hashes)
        matching = len(src_hashes & tgt_hashes)
        st.info(f"**Summary:** {matching} of {total_unique} unique rows match ({100*matching/total_unique:.1f}%)")

        # Print where hashing failed: show sample mismatched signatures
        st.subheader("Where Hashing Failed (Sample)")
        try:
            # Build the exact schema_rows used for hashing (common columns only)
            src_schema_rows_for_hash = [
                {
                    "column_name": c.get("name"),
                    "data_type": (c.get("raw_type") or c.get("type") or ""),
                }
                for c in (src_columns or [])
                if c.get("name")
            ]
            tgt_schema_rows_for_hash = [
                {
                    "column_name": c.get("name"),
                    "data_type": (c.get("raw_type") or c.get("type") or ""),
                }
                for c in (tgt_columns or [])
                if c.get("name")
            ]

            def _included_signature_cols(schema_rows_for_hash):
                included = []
                for r in schema_rows_for_hash:
                    col = r.get("column_name")
                    dtype = str(r.get("data_type") or "").upper()
                    if not col:
                        continue
                    if any(x in dtype for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
                        continue
                    if (not include_timestamp_columns) and ("TIMESTAMP" in dtype or "DATETIME" in dtype):
                        continue
                    included.append(str(col))
                return included

            sig_cols = _included_signature_cols(src_schema_rows_for_hash)
            if sig_cols:
                st.caption("Signature column order (included in hash)")
                st.code(" | ".join(sig_cols))
        except Exception:
            pass

        if missing_in_target:
            st.caption("Source-only rows (present in source, missing in target) — sample")
            try:
                missing_hashes = [str(h).upper() for h in missing_in_target if h]
                q = build_row_hash_mismatch_rows_query_v2(
                    engine,
                    src["catalog"],
                    src["schema"],
                    src["table"],
                    schema_rows=src_schema_rows_for_hash,
                    hash_values=missing_hashes,
                    include_timestamp=include_timestamp_columns,
                    timestamp_mode=None,
                    limit=25,
                )
                rows = execute_query(engine, source_conn, q)
                st.dataframe(rows, use_container_width=True)
            except Exception as e:
                st.error(f"Failed to fetch source-only sample rows: {e}")

        if extra_in_target:
            st.caption("Target-only rows (present in target, missing in source) — sample")
            try:
                extra_hashes = [str(h).upper() for h in extra_in_target if h]
                q = build_row_hash_mismatch_rows_query_v2(
                    "databricks",
                    tgt["catalog"],
                    tgt["schema"],
                    tgt["table"],
                    schema_rows=tgt_schema_rows_for_hash,
                    hash_values=extra_hashes,
                    include_timestamp=include_timestamp_columns,
                    timestamp_mode=None,
                    limit=25,
                )
                rows = execute_query("Databricks", target_conn, q)
                st.dataframe(rows, use_container_width=True)
            except Exception as e:
                st.error(f"Failed to fetch target-only sample rows: {e}")
        
        return False
def approx_equal(a, b, tol=1e-6):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol
    
def run_numeric_validation(engine, source_conn, target_conn, src, tgt):
    st.subheader("📈 Deep Table Statistics")

    # ---------- Fetch schema ----------
    src_schema = fetch_schema(
        engine, source_conn,
        src["catalog"], src["schema"], src["table"]
    )

    numeric_cols = get_numeric_columns(src_schema)
    string_cols = [
        r["column_name"]
        for r in src_schema
        if normalize_datatype(r["data_type"]) == "string"
    ]

    overall_pass = True
    failed_numeric_cols = []
    failed_string_cols = []

    # ---------- Null count ----------
    null_query = f"""
        SELECT
        {', '.join([
            f"SUM(CASE WHEN {r['column_name']} IS NULL THEN 1 ELSE 0 END) AS {r['column_name']}_nulls"
            for r in src_schema
        ])}
        FROM {src['catalog']}.{src['schema']}.{src['table']}
    """
    null_res = execute_query("snowflake", source_conn, null_query)[0]

    null_df = pd.DataFrame([
        {"Column": k.replace("_nulls", ""), "Null Count": v}
        for k, v in null_res.items()
    ])

    st.subheader("ℹ Null Counts")
    st.dataframe(null_df, use_container_width=True)

    if null_df["Null Count"].sum() > 0:
        overall_pass = False
        failed_null_cols = null_df[null_df["Null Count"] > 0]['Column'].tolist()
        st.warning(f"⚠️ Columns with NULL values: {', '.join(failed_null_cols)}")

    # ---------- Numeric statistics ----------
    numeric_rows = []

    if numeric_cols:
        src_stats = run_numeric_stats(
            engine, source_conn,
            src["catalog"], src["schema"], src["table"],
            numeric_cols
        )
        tgt_stats = run_numeric_stats(
            "databricks", target_conn,
            tgt["catalog"], tgt["schema"], tgt["table"],
            numeric_cols
        )

        for col in numeric_cols:
            s = normalize_keys(src_stats[col])
            t = normalize_keys(tgt_stats[col])

            src_min = normalize_numeric(s["min_val"])
            src_max = normalize_numeric(s["max_val"])
            src_avg = normalize_numeric(s["avg_val"])
            
            tgt_min = normalize_numeric(t["min_val"])
            tgt_max = normalize_numeric(t["max_val"])
            tgt_avg = normalize_numeric(t["avg_val"])

            # Use approx_equal for all comparisons (handles floating point precision)
            min_match = approx_equal(src_min, tgt_min)
            max_match = approx_equal(src_max, tgt_max)
            avg_match = approx_equal(src_avg, tgt_avg)
            
            matched = min_match and max_match and avg_match

            if not matched:
                overall_pass = False
                differences = []
                if not min_match:
                    differences.append(f"MIN: {src_min} vs {tgt_min} (diff: {abs((src_min or 0) - (tgt_min or 0)):.4f})")
                if not max_match:
                    differences.append(f"MAX: {src_max} vs {tgt_max} (diff: {abs((src_max or 0) - (tgt_max or 0)):.4f})")
                if not avg_match:
                    differences.append(f"AVG: {src_avg} vs {tgt_avg} (diff: {abs((src_avg or 0) - (tgt_avg or 0)):.4f})")
                
                failed_numeric_cols.append({
                    "column": col,
                    "source": (src_min, src_max, src_avg),
                    "target": (tgt_min, tgt_max, tgt_avg),
                    "differences": differences
                })

            numeric_rows.append({
                "Column": col,
                "Source Min": src_min,
                "Target Min": tgt_min,
                "Source Max": src_max,
                "Target Max": tgt_max,
                "Source Avg": round(src_avg, 4) if src_avg is not None else None,
                "Target Avg": round(tgt_avg, 4) if tgt_avg is not None else None,
                "Result": "✅ PASS" if matched else "❌ FAIL"
            })

        st.subheader("🔢 Numeric Column Statistics")
        st.dataframe(pd.DataFrame(numeric_rows), use_container_width=True)

        # Show failed numeric columns with details
        if failed_numeric_cols:
            st.error(f"❌ Numeric Statistics Failed for {len(failed_numeric_cols)} column(s):")
            for fail in failed_numeric_cols:
                st.markdown(f"• **{fail['column']}**:")
                for diff in fail["differences"]:
                    st.markdown(f"  - {diff}")

    # ---------- String length statistics ----------
    string_rows = []

    for col in string_cols:
        src_q = f"""
            SELECT
                MIN(round(LENGTH({col}),4)) AS min_len,
                MAX(round(LENGTH({col}),4)) AS max_len,
                AVG(round(LENGTH({col}),4)) AS avg_len
            FROM {src['catalog']}.{src['schema']}.{src['table']}
        """
        tgt_q = f"""
            SELECT
                MIN(round(LENGTH({col}),4)) AS min_len,
                MAX(round(LENGTH({col}),4)) AS max_len,
                AVG(round(LENGTH({col}),4)) AS avg_len
            FROM {tgt['catalog']}.{tgt['schema']}.{tgt['table']}
        """

        s = normalize_keys(execute_query("snowflake", source_conn, src_q)[0])
        t = normalize_keys(execute_query("databricks", target_conn, tgt_q)[0])

        src_min_len = normalize_numeric(s["min_len"])
        src_max_len = normalize_numeric(s["max_len"])
        src_avg_len = normalize_numeric(s["avg_len"])
        
        tgt_min_len = normalize_numeric(t["min_len"])
        tgt_max_len = normalize_numeric(t["max_len"])
        tgt_avg_len = normalize_numeric(t["avg_len"])

        # Use approx_equal for string length comparisons too
        matched = (
            approx_equal(src_min_len, tgt_min_len) and
            approx_equal(src_max_len, tgt_max_len) and
            approx_equal(src_avg_len, tgt_avg_len)
        )

        if not matched:
            overall_pass = False
            differences = []
            if not approx_equal(src_min_len, tgt_min_len):
                differences.append(f"MIN length: {src_min_len} vs {tgt_min_len}")
            if not approx_equal(src_max_len, tgt_max_len):
                differences.append(f"MAX length: {src_max_len} vs {tgt_max_len}")
            if not approx_equal(src_avg_len, tgt_avg_len):
                differences.append(f"AVG length: {src_avg_len:.2f} vs {tgt_avg_len:.2f}")
            
            failed_string_cols.append({
                "column": col,
                "differences": differences
            })

        string_rows.append({
            "Column": f"{col} (string)",
            "Source Min Len": src_min_len,
            "Target Min Len": tgt_min_len,
            "Source Max Len": src_max_len,
            "Target Max Len": tgt_max_len,
            "Source Avg Len": round(src_avg_len, 2) if src_avg_len else None,
            "Target Avg Len": round(tgt_avg_len, 2) if tgt_avg_len else None,
            "Result": "✅ PASS" if matched else "❌ FAIL"
        })

    if string_rows:
        st.subheader("🔤 String Length Statistics")
        st.dataframe(pd.DataFrame(string_rows), use_container_width=True)

        if failed_string_cols:
            st.error(f"❌ String Length Statistics Failed for {len(failed_string_cols)} column(s):")
            for fail in failed_string_cols:
                st.markdown(f"• **{fail['column']}**: {', '.join(fail['differences'])}")

    # Overall summary
    if overall_pass:
        st.success("✅ All Numeric Statistics PASSED")
    else:
        total_failures = len(failed_numeric_cols) + len(failed_string_cols)
        st.error(f"❌ Numeric Statistics FAILED - {total_failures} column(s) with mismatches")

    return overall_pass

def fetch_schema(engine, conn, catalog, schema, table):
    schema_raw = execute_query(
        engine,
        conn,
        build_schema_query(engine, catalog, schema, table)
    )
    return [normalize_result(r) for r in schema_raw]

from decimal import Decimal

def normalize_numeric(val):
    if val is None:
        return None
    
    # Already numeric
    if isinstance(val, (int, float)):
        return float(val)

    # Decimal instance
    if isinstance(val, Decimal):
        return float(val)

    # Values returned as string "Decimal('100.00')"
    if isinstance(val, str) and "Decimal" in val:
        cleaned = (
            val.replace("Decimal(", "")
               .replace("'", "")
               .replace(")", "")
        )
        try:
            return float(cleaned)
        except:
            return None

    # Final fallback
    try:
        return float(val)
    except:
        return None
def normalize_keys(d):
    """
    Convert all dict keys to lowercase for comparison
    """
    return {k.lower(): v for k, v in d.items()}

def run_checks_in_order(checks):
    results = {}
    stop = False

    for name, check in checks:
        st.markdown(f"### ▶ {name}")

        if stop:
            st.error("⛔ SKIPPED (Previous validation failed)")
            results[name] = None
            continue

        passed = check()
        results[name] = passed

        if not passed:
            st.error(f"⛔ Validation failed {name}")
            stop = True

    return results

# NOTE: The legacy "Validation Plan (Per Table)" UI was removed.
# Manual/Browse/CSV/Config tabs now run validations directly, and this
# block caused an extra component to appear at the top after running
# Browse validations.
st.markdown(
    """
    <style>
        .centered {
            text-align: center;
        }
    </style>
    """,
    unsafe_allow_html=True
)
def run_validation(src, tgt, validation_type, metrics):

        metric_set = set(m.lower() for m in (metrics or []))
        if "all" in metric_set:
            metric_set = {"row_count", "schema", "numeric", "hash"}

        selected_validations = {
            "row_count": (
                ("row_count" in metric_set)
                and st.session_state.get("include_row_count_config", True)
            ),
            "schema": (
                ("schema" in metric_set)
                and st.session_state.get("include_schema_config", True)
            ),
            "numeric": (
                ("numeric" in metric_set)
                and st.session_state.get("include_numeric_config", True)
            ),
            "hash": (
                ("hash" in metric_set)
                and st.session_state.get("include_hash_config", True)
            ),
        }

        results = {}
        if validation_type == "shallow":
            if not (
                selected_validations.get("row_count")
                or selected_validations.get("schema")
            ):
                st.warning("⚠️ No validation methods selected; skipping")
                return

            row_res = (
                run_row_count(
                    st.session_state["engine"],
                    st.session_state["source_conn"],
                    st.session_state["target_conn"],
                    src, tgt
                )
                if selected_validations.get("row_count")
                else None
            )

            schema_res = (
                run_schema_validation(
                    st.session_state["engine"],
                    st.session_state["source_conn"],
                    st.session_state["target_conn"],
                    src, tgt,
                    case_sensitive=st.session_state.get(
                        "config_case_sensitive_global", False
                    ),
                )
                if selected_validations.get("schema")
                else None
            )

            record = generate_validation_record(
                "shallow",
                src, tgt,
                bool_to_status(row_res),
                bool_to_status(schema_res),
                "N/A",
                "N/A"
            )

        else:  # deep
            checks = []
            if selected_validations.get("row_count"):
                checks.append((
                    "Row Count Validation",
                    lambda: run_row_count(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt
                    )
                ))
            if selected_validations.get("schema"):
                checks.append((
                    "Schema Validation",
                    lambda: run_schema_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt,
                        case_sensitive=st.session_state.get(
                            "config_case_sensitive_global", False
                        ),
                    )
                ))
            if selected_validations.get("numeric"):
                checks.append((
                    "Numeric Statistics Validation",
                    lambda: run_numeric_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src, tgt
                    )
                ))
            if selected_validations.get("hash"):
                checks.append((
                    "Row Hash Validation",
                    lambda: run_row_hash_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        src,
                        tgt,
                        include_timestamp_columns=st.session_state.get(
                            "include_timestamp_in_hash_config", True
                        ),
                    )
                ))

            if not checks:
                st.warning("⚠️ No validation methods selected; skipping")
                return

            results = run_checks_in_order(checks)
               
            record = generate_validation_record(
                validation_type,
                src,
                tgt,
                bool_to_status(results.get("Row Count Validation")),
                bool_to_status(results.get("Schema Validation")),
                bool_to_status(results.get("Numeric Statistics Validation")),
                bool_to_status(results.get("Row Hash Validation")),
            )

        insert_validation_result(
            st.session_state["target_conn"],
            record
        )


# =========================================================
# LANDING BANNER (ONLY WHEN NOT CONFIGURED)
# =========================================================
_active_page = st.session_state.get("active_page", "main")

# Keep the banner visible on the main validation experience so
# switching modes (e.g., shallow/deep) doesn't cause the header space
# to disappear after connections are established.
if _active_page in ("main", "validation"):
    icon_base64 = load_icon("reconciliation.png")

    st.markdown(
        f"""
        <div style="
            display:flex;
            align-items:center;
            justify-content:center;
            gap:12px;
            margin-top:10px;
        ">
            <img src="data:image/png;base64,{icon_base64}" width="55"/>
            <h1 style="margin:0;">Reconciliation Framework</h1>
        </div>

        <p style="text-align:center; font-size: 16px; margin-top:6px;">
            Validate <b>data consistency and completeness</b><br>
            across heterogeneous analytical engines.<br>
            <i>Configure source and target systems, then validate data parity.</i>
        </p>
        """,
        unsafe_allow_html=True,
    )

    st.divider()

# =========================================================
# SIDEBAR — FRAMEWORK INFO
# =========================================================
with st.sidebar:

    st.markdown(
        """
        **Supported Engines**
        • BigQuery ↔️ Databricks  
        • Snowflake ↔️ Databricks
        """
    )

    st.divider()

    st.subheader("📊 Quick Stats")
    if "source_conn" in st.session_state and "target_conn" in st.session_state:
        st.success("🟢 Status: Connected")
    else:
        st.error("🔴 Status: Not Connected")

    st.metric("Source Engine", st.session_state.get("engine", "—"))
    st.metric("Target Engine", "Databricks")

    if st.button("📊 Open Dashboard", use_container_width=True,key="open_dashboard_sidebar"):
        st.session_state["active_page"] = "dashboard"
    

    #st.divider()

    #if st.button("📊 Open Dashboard", use_container_width=True,key="open_dashboard_sidebar"):
     #   st.session_state["active_page"] = "dashboard"

    if st.button("📋 Open Results", use_container_width=True,key="open_results_sidebar"):
        st.session_state["active_page"] = "results"


# ===============================
# DASHBOARD PAGE (EARLY EXIT)
# ===============================
if st.session_state["active_page"] == "dashboard":

    st.title("📊 Validation Dashboard")

    if st.button("⬅️ Back to Validation"):
        st.session_state["active_page"] = "validation"
        st.rerun()

    st.divider()

    DASHBOARD_TABLE = "table_validation.results.validation_results"

    dashboard_query = f"""
        SELECT
            --COUNT(DISTINCT concat(catalog,'.',schema,'.',table)) AS tables_validated,
            COUNT(src_table_name) AS tables_validated,
            COUNT(*) AS total_validations,
            SUM(CASE WHEN row_count = 'PASS' THEN 1 ELSE 0 END) AS row_count_pass,
            SUM(CASE WHEN schema_check = 'PASS' THEN 1 ELSE 0 END) AS schema_pass,
            SUM(CASE WHEN numeric_check = 'PASS' THEN 1 ELSE 0 END) AS numeric_pass,
            SUM(CASE WHEN hash_validation = 'PASS' THEN 1 ELSE 0 END) AS row_hash_pass,
            SUM(CASE WHEN row_count = 'FAIL' THEN 1 ELSE 0 END) AS row_count_fail,
            SUM(CASE WHEN schema_check = 'FAIL' THEN 1 ELSE 0 END) AS schema_fail,
            SUM(CASE WHEN numeric_check = 'FAIL' THEN 1 ELSE 0 END) AS numeric_fail,
            SUM(CASE WHEN hash_validation = 'FAIL' THEN 1 ELSE 0 END) AS row_hash_fail
        FROM {DASHBOARD_TABLE}
    """
    dashboard_conn = get_dashboard_dbx_conn()

    result = execute_query(
        "Databricks",
        dashboard_conn,
        dashboard_query
    )[0]


    result = normalize_result(result)
    row_fail = result["row_count_fail"]
    schema_fail = result["schema_fail"]
    numeric_fail = result["numeric_fail"]
    hash_fail = result["row_hash_fail"]

    c1, c2, c3 = st.columns(3)
    c4, c5, c6 = st.columns(3)

    c1.metric("📂 Tables Validated", result["tables_validated"])
    c2.metric("🧪 Total Validations", result["total_validations"])
    c3.metric("✅ Row Count Passed", result["row_count_pass"])

    c4.metric("📐 Schema Passed", result["schema_pass"])
    c5.metric("📈 Numeric Passed", result["numeric_pass"])
    c6.metric("🔐 Row Hash Passed", result["row_hash_pass"])

    st.divider()

    btn_label = "📊 View Stats" if not st.session_state["show_pie_stats"] else "❌ Hide Stats"

    if st.button(btn_label, use_container_width=True):
        st.session_state["show_pie_stats"] = not st.session_state["show_pie_stats"]

    if st.session_state["show_pie_stats"]:

        st.subheader("📊 Validation Overview")

        c1, c2 = st.columns(2)
        c3, c4 = st.columns(2)

        with c1:
            render_pie_chart(
                "Row Count Validation",
                result["row_count_pass"],
                row_fail
            )

        with c2:
            render_pie_chart(
                "Schema Validation",
                result["schema_pass"],
                schema_fail
            )

        with c3:
            render_pie_chart(
                "Numeric Validation",
                result["numeric_pass"],
                numeric_fail
            )

        with c4:
            render_pie_chart(
                "Row Hash Validation",
                result["row_hash_pass"],
                hash_fail
            )


    # 🚨 STOP HERE — do NOT run validation UI
    st.stop()

# =========================================================
# RESULTS PAGE
# =========================================================
if st.session_state["active_page"] == "results":

    st.title("📋 Validation Results")

    st.caption("All validation executions captured from Databricks")

    # 🔹 STATIC Databricks connection (dashboard/results use only)
    results_conn = get_dashboard_dbx_conn()
    
    RESULTS_QUERY = """
        SELECT
            validation_id,
            validation_ts,
            validation_type,
            src_table_name as source_table_name,
            tgt_table_name as target_table_name,
            row_count as count_validation,            
            hash_validation,
            numeric_check,
            schema_check
        FROM table_validation.results.validation_results
        ORDER BY validation_ts DESC
    """

    results = execute_query("databricks", results_conn, RESULTS_QUERY)

    if not results:
        st.warning("No validation results found.")
    else:
        df = pd.DataFrame(results)

        st.dataframe(df, use_container_width=True)

    st.divider()

    if st.button("⬅ Back to Main"):
        st.session_state["active_page"] = "main"

    st.stop()


# =========================================================
# STEP 1: ENGINE SELECTION
# =========================================================
source_engine = st.selectbox(
    "Select Source Compute Engine",
    ["BigQuery", "Snowflake"]
)

# =========================================================
# STEP 2: CREDENTIALS (SIDE-BY-SIDE)
# =========================================================
st.subheader("🔐 Credentials Configuration")

left, right = st.columns(2)

# -----------------------------
# SOURCE CREDENTIALS
# -----------------------------
with left:
    st.markdown("### 🧩 Source Engine")

    if source_engine == "BigQuery":
        project_id = st.text_input("GCP Project ID")
        dataset_location = st.text_input("Dataset Location", value="US")
        bq_key_path = st.text_input("Service Account Key Path")

    elif source_engine == "Snowflake":
        sf_account = st.text_input("Account")
        sf_user = st.text_input("Username")
        sf_password = st.text_input("Password", type="password")
        sf_warehouse = st.text_input("Warehouse")
        sf_role = st.text_input("Role")

# -----------------------------
# DATABRICKS CREDENTIALS
# -----------------------------
with right:
    st.markdown("### 🎯 Databricks")

    dbx_server = st.text_input("Databricks Server Hostname")
    dbx_http_path = st.text_input("HTTP Path")
    dbx_token = st.text_input("Access Token", type="password")
# =============================
# SESSION STATE INITIALIZATION
# =============================
# =============================
# SESSION STATE INITIALIZATION
# =============================
DEFAULT_SESSION_KEYS = {
    "engine": None,
    "source_conn": None,
    "target_conn": None,
    "source_selections": [],
    "target_selections": [],
    "validation_plan": [],
    "active_page": "main",
    "show_pie_stats": False,
    "include_timestamp_in_hash_plan": True,
    "include_timestamp_in_hash_browse": True,
    "include_timestamp_in_hash_manual": True,
    "include_timestamp_in_hash_csv": True,
    "include_timestamp_in_hash_config": True,

    # Schema case sensitivity (per tab)
    "browse_case_sensitive_global": False,
    "manual_case_sensitive_global": False,
    "csv_case_sensitive_global": False,
    "config_case_sensitive_global": False,

    # Per-flow validation method toggles
    "include_row_count_plan": True,
    "include_schema_plan": True,
    "include_numeric_plan": True,
    "include_hash_plan": True,

    "include_row_count_browse": True,
    "include_schema_browse": True,
    "include_numeric_browse": True,
    "include_hash_browse": True,

    "include_row_count_manual": True,
    "include_schema_manual": True,
    "include_numeric_manual": True,
    "include_hash_manual": True,

    "include_row_count_csv": True,
    "include_schema_csv": True,
    "include_numeric_csv": True,
    "include_hash_csv": True,

    "include_row_count_config": True,
    "include_schema_config": True,
    "include_numeric_config": True,
    "include_hash_config": True,
}

for key, default in DEFAULT_SESSION_KEYS.items():
    if key not in st.session_state:
        st.session_state[key] = default


# =========================================================
# STEP 3: CONNECT (LOGIC UNCHANGED)
# =========================================================
st.divider()
connect_clicked = st.button("🔌 Establish Connections", use_container_width=True)

if connect_clicked:
    try:
        # Validate source credentials
        if source_engine == "BigQuery":
            if missing([project_id, bq_key_path]):
                st.error("❌ Please fill all BigQuery credentials")
                st.stop()
        else:
            if missing([sf_account, sf_user, sf_password, sf_warehouse]):
                st.error("❌ Please fill all Snowflake credentials")
                st.stop()

        # Validate Databricks credentials
        if missing([dbx_server, dbx_http_path, dbx_token]):
            st.error("❌ Please fill all Databricks credentials")
            st.stop()

        # === CONNECTION LOGIC (UNCHANGED) ===
        if source_engine == "BigQuery":
            source_conn = connect_bigquery(project_id, bq_key_path, dataset_location)
        else:
            source_conn = connect_snowflake(
                sf_account, sf_user, sf_password, sf_warehouse, sf_role
            )

        target_conn = connect_databricks(dbx_server, dbx_http_path, dbx_token)

        st.session_state["source_conn"] = source_conn
        st.session_state["target_conn"] = target_conn
        st.session_state["engine"] = source_engine

        st.success("✅ Connections established successfully")
        


    except Exception as e:
        st.error(f"❌ Connection failed: {e}")
if not st.session_state.get("source_conn") or not st.session_state.get("target_conn"):
    st.info("🔌 Please establish connections to continue")
    st.stop()

# =========================================================
# TABLE SELECTION MODE
# =========================================================
st.divider()
st.subheader("📋 Table Selection")

tab_browse, tab_default, tab_csv ,tab_manual = st.tabs(
    ["🔽 Browse & Select", "⚙️Config Driven","📂 Upload CSV","✍️ Manual Entry"]
)



# =========================================================
# STEP 4: METADATA SELECTION (SIDE-BY-SIDE)
# =========================================================

if "source_conn" in st.session_state and "target_conn" in st.session_state:
 with tab_default:


    st.subheader("🧩 Validation Config (JSON Driven)")

    st.caption("Validation Methods")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.checkbox(
            "Row Count",
            key="include_row_count_config",
            value=st.session_state.get("include_row_count_config", True),
        )
    with c2:
        st.checkbox(
            "Schema",
            key="include_schema_config",
            value=st.session_state.get("include_schema_config", True),
        )
    with c3:
        st.checkbox(
            "Numeric",
            key="include_numeric_config",
            value=st.session_state.get("include_numeric_config", True),
        )
    with c4:
        st.checkbox(
            "Hash",
            key="include_hash_config",
            value=st.session_state.get("include_hash_config", True),
        )

    if st.session_state.get("include_hash_config"):
        st.checkbox(
            "Include TIMESTAMP columns in row hash",
            key="include_timestamp_in_hash_config",
            value=st.session_state.get(
                "include_timestamp_in_hash_config", True
            ),
            help=(
                "If unchecked, columns with TIMESTAMP datatype are excluded "
                "from the row hash calculation."
            ),
        )

    st.checkbox(
        "Case-sensitive schema validation",
        key="config_case_sensitive_global",
        value=st.session_state.get("config_case_sensitive_global", False),
        help=(
            "If enabled, column names must match exactly (case-sensitive). "
            "If disabled, schema matching is case-insensitive."
        ),
    )

    config_text = st.text_area(
        "Edit validation config and click Submit",
        value=json.dumps(DEFAULT_CONFIG, indent=2),
        height=350
    )

    if st.button("✅ Submit Config", use_container_width=True):
        try:
            st.session_state["validation_config"] = json.loads(config_text)
            st.success("Config loaded successfully")
        except Exception as e:
            st.error(f"Invalid JSON: {e}")
    if "validation_config" in st.session_state:

        tables = parse_config_tables(st.session_state["validation_config"])

        st.divider()
        st.subheader("🚀 Running Config Driven Validations")

        for t in tables:
            st.markdown(f"### 🔍 {t['name']}")

            src_cat, src_sch, src_tbl = parse_table_path(t["source"])
            tgt_cat, tgt_sch, tgt_tbl = parse_table_path(t["target"])

            src = {"catalog": src_cat, "schema": src_sch, "table": src_tbl}
            tgt = {"catalog": tgt_cat, "schema": tgt_sch, "table": tgt_tbl}

            run_validation(
                src=src,
                tgt=tgt,
                validation_type=t["validation_type"],
                metrics=t["metrics"]
            )

 with tab_manual:
    validation_type = st.radio(
        "Validation Type",
        ["shallow", "deep"],
        horizontal=True,
        key="manual_validation_type"
    )

    # =========================
    # VALIDATION OPTIONS
    # =========================
    if validation_type == "deep":

        st.subheader("⚙️ Select Deep Validations (Global Mode)")
        col1, col2 = st.columns(2)

        with col1:
            row_check = st.checkbox(
                "Row Count Validation",
                value=False,
                key="manual_global_row"
            )
            schema_check = st.checkbox(
                "Schema Validation",
                value=False,
                key="manual_global_schema"
            )

        with col2:
            numeric_check = st.checkbox(
                "Numeric Statistics Validation",
                value=False,
                key="manual_global_numeric"
            )
            hash_check = st.checkbox(
                "Row Hash Validation",
                value=False,
                key="manual_global_hash"
            )

        if hash_check:
            st.checkbox(
                "Include TIMESTAMP columns in row hash",
                value=st.session_state.get(
                    "include_timestamp_in_hash_manual", True
                ),
                key="include_timestamp_in_hash_manual",
                help=(
                    "If unchecked, columns with TIMESTAMP datatype are excluded "
                    "from the row hash calculation."
                ),
            )

        override_mode = st.checkbox(
            "⚙️ Override validations per table",
            value=False,
            key="manual_override"
        )

    else:
        # SHALLOW MODE
        st.info("Shallow mode will automatically run:")
        st.markdown("• ✅ Row Count Validation")
        st.markdown("• ✅ Schema Validation")

        row_check = True
        schema_check = True
        numeric_check = False
        hash_check = False
        override_mode = False

    manual_case_sensitive_global = st.checkbox(
        "Case-sensitive schema validation (global)",
        key="manual_case_sensitive_global",
        value=st.session_state.get("manual_case_sensitive_global", False),
        help=(
            "Default schema matching mode for all tables in this run. "
            "If per-table override is enabled, that table pair uses the opposite of this setting."
        ),
    )

    st.divider()
    st.subheader("✍️ Enter Table Paths (Multiple Supported)")
    st.caption("Enter one table per line OR comma-separated\nFormat: catalog.schema.table")

    src_raw = st.text_area(
        "Source Table Paths",
        placeholder="VALIDATION_DB.SCHEMA.TABLE1\nVALIDATION_DB.SCHEMA.TABLE2",
        key="src_table_paths",
        height=120
    )

    tgt_raw = st.text_area(
        "Target Table Paths",
        placeholder="workspace.default.table1\nworkspace.default.table2",
        key="tgt_table_paths",
        height=120
    )

    if src_raw and tgt_raw:

        # ✅ Normalize input: split by comma OR newline
        def normalize_paths(raw_text):
            return [
                p.strip()
                for p in raw_text.replace(",", "\n").splitlines()
                if p.strip()
            ]

        src_paths = normalize_paths(src_raw)
        tgt_paths = normalize_paths(tgt_raw)

        # ✅ Count check
        if len(src_paths) != len(tgt_paths):
            st.error("❌ Source and Target table counts must match")
            st.stop()

        source_selections = []
        target_selections = []
        errors = False

        for s, t in zip(src_paths, tgt_paths):

            src_cat, src_sch, src_tbl = parse_table_path(s)
            tgt_cat, tgt_sch, tgt_tbl = parse_table_path(t)

            if not all([src_cat, src_sch, src_tbl]):
                st.error(f"❌ Invalid source path: {s}")
                errors = True
                continue

            if not all([tgt_cat, tgt_sch, tgt_tbl]):
                st.error(f"❌ Invalid target path: {t}")
                errors = True
                continue

            source_selections.append({
                "catalog": src_cat,
                "schema": src_sch,
                "table": src_tbl
            })

            target_selections.append({
                "catalog": tgt_cat,
                "schema": tgt_sch,
                "table": tgt_tbl
            })

        if errors:
            st.stop()

        st.session_state["source_selections"] = source_selections
        st.session_state["target_selections"] = target_selections

        st.success(f"✅ Loaded {len(source_selections)} table pairs")

        table_pairs = list(zip(source_selections, target_selections))

        # ====================================================
        # OVERRIDE UI PER TABLE
        # ====================================================
        per_table_validations = {}
        if validation_type == "deep" and override_mode and table_pairs:

            st.divider()
            st.markdown("## 🛠 Override Validations Per Table")

            for i, (src, tgt) in enumerate(table_pairs):

                table_id = f"manual_{src['schema']}_{src['table']}_{i}"

                with st.container():

                    st.markdown(
                        f"### 🔹 {src['schema']}.{src['table']} → "
                        f"{tgt['schema']}.{tgt['table']}"
                    )

                    colA, colB = st.columns(2)

                    with colA:
                        r = st.checkbox(
                            "Row Count",
                            value=False,
                            key=f"{table_id}_row"
                        )
                        s = st.checkbox(
                            "Schema",
                            value=False,
                            key=f"{table_id}_schema"
                        )

                    with colB:
                        n = st.checkbox(
                            "Numeric Stats",
                            value=False,
                            key=f"{table_id}_numeric"
                        )
                        h = st.checkbox(
                            "Row Hash",
                            value=False,
                            key=f"{table_id}_hash"
                        )

                    case_override = st.checkbox(
                        "Override case sensitivity",
                        value=False,
                        key=f"{table_id}_case_override",
                        help=(
                            "If checked, this table pair uses the opposite of the global "
                            "case-sensitive setting."
                        ),
                    )

                    effective_case_sensitive = (
                        (not manual_case_sensitive_global)
                        if case_override
                        else manual_case_sensitive_global
                    )
                    st.caption(
                        "Schema case sensitivity: "
                        f"{'ON' if effective_case_sensitive else 'OFF'}"
                        + (" (Override)" if case_override else "")
                    )

                    per_table_validations[table_id] = {
                        "row": r,
                        "schema": s,
                        "numeric": n,
                        "hash": h,
                        "case_override": case_override,
                        "src": src,
                        "tgt": tgt
                    }

            st.session_state["manual_per_table_validations"] = per_table_validations

            override_count = sum(
                1
                for v in per_table_validations.values()
                if v.get("case_override")
            )
            st.info(
                "Case sensitivity default: "
                f"{'ON' if manual_case_sensitive_global else 'OFF'}"
                f" | Overrides: {override_count}/{len(per_table_validations)}"
            )

        # =========================
        # RUN BUTTON
        # =========================
        if st.button("🚀 Run Manual Validations", use_container_width=True):

            try:

                # =========================
                # OVERRIDE MODE
                # =========================
                if validation_type == "deep" and override_mode:

                    for table_id, config in st.session_state.get(
                        "manual_per_table_validations", {}
                    ).items():

                        src = config["src"]
                        tgt = config["tgt"]

                        st.divider()
                        st.markdown(
                            f"## 🔍 {src['schema']}.{src['table']} → "
                            f"{tgt['schema']}.{tgt['table']}"
                        )

                        checks = []

                        case_override = bool(config.get("case_override"))
                        effective_case_sensitive = (
                            (not manual_case_sensitive_global)
                            if case_override
                            else manual_case_sensitive_global
                        )
                        st.caption(
                            "Schema case sensitivity: "
                            f"{'ON' if effective_case_sensitive else 'OFF'}"
                            + (" (Override)" if case_override else "")
                        )

                        # If nothing selected for table → use global
                        if not any([
                            config["row"],
                            config["schema"],
                            config["numeric"],
                            config["hash"]
                        ]):

                            effective_row = row_check
                            effective_schema = schema_check
                            effective_numeric = numeric_check
                            effective_hash = hash_check

                        else:
                            effective_row = config["row"]
                            effective_schema = config["schema"]
                            effective_numeric = config["numeric"]
                            effective_hash = config["hash"]

                        if effective_row:
                            checks.append(("Row Count Validation", lambda s=src, t=tgt:
                                run_row_count(
                                    st.session_state["engine"],
                                    st.session_state["source_conn"],
                                    st.session_state["target_conn"],
                                    s, t
                                )))

                        if effective_schema:
                            checks.append(("Schema Validation", lambda s=src, t=tgt:
                                run_schema_validation(
                                    st.session_state["engine"],
                                    st.session_state["source_conn"],
                                    st.session_state["target_conn"],
                                    s, t,
                                    case_sensitive=effective_case_sensitive,
                                )))

                        if effective_numeric:
                            checks.append(("Numeric Statistics Validation", lambda s=src, t=tgt:
                                run_numeric_validation(
                                    st.session_state["engine"],
                                    st.session_state["source_conn"],
                                    st.session_state["target_conn"],
                                    s, t
                                )))

                        if effective_hash:
                            checks.append(("Row Hash Validation", lambda s=src, t=tgt:
                                run_row_hash_validation(
                                    st.session_state["engine"],
                                    st.session_state["source_conn"],
                                    st.session_state["target_conn"],
                                    s,
                                    t,
                                    include_timestamp_columns=st.session_state.get(
                                        "include_timestamp_in_hash_manual", True
                                    ),
                                )))

                        if checks:
                            run_checks_in_order(checks)
                        else:
                            st.warning("⚠️ No validations selected.")

                # =========================
                # NORMAL MODE
                # =========================
                else:

                    for src, tgt in table_pairs:

                        st.divider()
                        st.markdown(
                            f"## 🔍 {src['schema']}.{src['table']} → "
                            f"{tgt['schema']}.{tgt['table']}"
                        )

                        checks = []

                        if validation_type == "shallow":

                            checks.extend([
                                ("Row Count Validation",
                                 lambda s=src, t=tgt: run_row_count(
                                     st.session_state["engine"],
                                     st.session_state["source_conn"],
                                     st.session_state["target_conn"],
                                     s, t
                                 )),
                                ("Schema Validation",
                                 lambda s=src, t=tgt: run_schema_validation(
                                     st.session_state["engine"],
                                     st.session_state["source_conn"],
                                     st.session_state["target_conn"],
                                     s, t,
                                     case_sensitive=manual_case_sensitive_global,
                                 )),
                            ])

                        else:
                            if row_check:
                                checks.append(("Row Count Validation",
                                    lambda s=src, t=tgt: run_row_count(
                                        st.session_state["engine"],
                                        st.session_state["source_conn"],
                                        st.session_state["target_conn"],
                                        s, t
                                    )))

                            if schema_check:
                                checks.append(("Schema Validation",
                                    lambda s=src, t=tgt: run_schema_validation(
                                        st.session_state["engine"],
                                        st.session_state["source_conn"],
                                        st.session_state["target_conn"],
                                        s, t,
                                        case_sensitive=manual_case_sensitive_global,
                                    )))

                            if numeric_check:
                                checks.append(("Numeric Statistics Validation",
                                    lambda s=src, t=tgt: run_numeric_validation(
                                        st.session_state["engine"],
                                        st.session_state["source_conn"],
                                        st.session_state["target_conn"],
                                        s, t
                                    )))

                            if hash_check:
                                checks.append(("Row Hash Validation",
                                    lambda s=src, t=tgt: run_row_hash_validation(
                                        st.session_state["engine"],
                                        st.session_state["source_conn"],
                                        st.session_state["target_conn"],
                                        s,
                                        t,
                                        include_timestamp_columns=st.session_state.get(
                                            "include_timestamp_in_hash_manual", True
                                        ),
                                    )))

                        if checks:
                            run_checks_in_order(checks)
                        else:
                            st.warning("⚠️ No validations selected.")

                st.success("🎉 Manual validations completed")

            except Exception as e:
                st.error(str(e))


with tab_csv:
    st.subheader("📂 Upload CSV for Multiple Tables")

    # ===============================
    # CSV FORMAT GUIDE (Before Upload)
    # ===============================
    st.markdown("""
    ### 📘 CSV Format Guide

    #### ✅ Required Columns
    - validation_type (shallow / deep)
    - source_catalog
    - source_schema
    - source_table
    - target_catalog
    - target_schema
    - target_table
    - metrics (required only for deep)

    ---
    #### 📊 Allowed Metrics (for deep validation)
    - `row_count`
    - `schema`
    - `numeric`
    - `hash`

    ---
    #### 📝 Example (Deep)
    ```
    validation_type,source_catalog,source_schema,source_table,target_catalog,target_schema,target_table,metrics
    deep,SNOWFLAKE_DB,PUBLIC,DATATYPE_DEMO,workspace,default,datatype_demo,"row_count,schema,hash"
    ```

    #### 📝 Example (Shallow)
    ```
    validation_type,source_catalog,source_schema,source_table,target_catalog,target_schema,target_table
    shallow,SNOWFLAKE_DB,PUBLIC,DATATYPE_DEMO,workspace,default,datatype_demo
    ```
    """)

    st.divider()

    uploaded_file = st.file_uploader(
        "Upload reconciliation CSV",
        type=["csv"]
    )

    if uploaded_file:
        df = pd.read_csv(uploaded_file)

        st.subheader("⚙️ Validation Settings")

        st.caption("Validation Methods")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.checkbox(
                "Row Count",
                key="include_row_count_csv",
                value=st.session_state.get("include_row_count_csv", True),
            )
        with c2:
            st.checkbox(
                "Schema",
                key="include_schema_csv",
                value=st.session_state.get("include_schema_csv", True),
            )
        with c3:
            st.checkbox(
                "Numeric",
                key="include_numeric_csv",
                value=st.session_state.get("include_numeric_csv", True),
            )
        with c4:
            st.checkbox(
                "Hash",
                key="include_hash_csv",
                value=st.session_state.get("include_hash_csv", True),
            )

        if st.session_state.get("include_hash_csv"):
            st.checkbox(
                "Include TIMESTAMP columns in row hash",
                key="include_timestamp_in_hash_csv",
                value=st.session_state.get(
                    "include_timestamp_in_hash_csv", True
                ),
                help=(
                    "If unchecked, columns with TIMESTAMP datatype are excluded "
                    "from the row hash calculation."
                ),
            )

        st.checkbox(
            "Case-sensitive schema validation",
            key="csv_case_sensitive_global",
            value=st.session_state.get("csv_case_sensitive_global", False),
            help=(
                "If enabled, column names must match exactly (case-sensitive). "
                "If disabled, schema matching is case-insensitive."
            ),
        )

        st.subheader("🔍 Preview Uploaded CSV")
        st.dataframe(df, use_container_width=True)

        if st.button("🚀 Run CSV Validations", use_container_width=True):
            try:
                validate_csv(df)
                run_csv_validations(df)
                st.success("🎉 All CSV validations completed")
            except Exception as e:
                st.error(f"❌ {str(e)}")




with tab_browse:
    st.subheader("📂 Select Tables from Catalog")
    st.subheader("⚙️ Validation Settings")

    validation_type = st.radio(
        "Validation Type",
        ["shallow", "deep"],
        horizontal=True,
        key="browse_validation_type"
    )

    browse_case_sensitive_global = st.checkbox(
        "Case-sensitive schema validation (global)",
        key="browse_case_sensitive_global",
        value=st.session_state.get("browse_case_sensitive_global", False),
        help=(
            "Default schema matching mode for all tables in this run. "
            "If per-table override is enabled, that table pair uses the opposite of this setting."
        ),
    )

    # ===============================
    # GLOBAL DEEP CHECKBOXES
    # ===============================
    if validation_type == "deep":

        st.markdown("### Select Deep Validation Metrics (Global Mode)")

        colA, colB = st.columns(2)

        with colA:
            browse_row_check = st.checkbox(
                "Row Count Validation",
                value=False,
                key="browse_global_row"
            )
            browse_schema_check = st.checkbox(
                "Schema Validation",
                value=False,
                key="browse_global_schema"
            )

        with colB:
            browse_numeric_check = st.checkbox(
                "Numeric Statistics Validation",
                value=False,
                key="browse_global_numeric"
            )
            browse_hash_check = st.checkbox(
                "Row Hash Validation",
                value=False,
                key="browse_global_hash"
            )

        if browse_hash_check:
            st.checkbox(
                "Include TIMESTAMP columns in row hash",
                value=st.session_state.get(
                    "include_timestamp_in_hash_browse", True
                ),
                key="include_timestamp_in_hash_browse",
                help=(
                    "If unchecked, columns with TIMESTAMP datatype are excluded "
                    "from the row hash calculation."
                ),
            )

        override_mode = st.checkbox(
            "⚙️ Override validations per table",
            value=False,
            key="browse_override"
        )

    else:
        override_mode = False
        st.info("Shallow mode will run Row Count and Schema validation automatically.")

        browse_row_check = True
        browse_schema_check = True
        browse_numeric_check = False
        browse_hash_check = False

    st.divider()
    left, right = st.columns(2)

    # =================================================
    # SOURCE METADATA
    # =================================================
    with left:

        st.markdown("### 🧩 Source")

        catalogs = get_catalogs(
            st.session_state["engine"],
            st.session_state["source_conn"]
        )

        selected_catalog = st.selectbox(
            "Catalog",
            catalogs,
            key="src_catalog"
        )

        schemas = get_schemas(
            st.session_state["engine"],
            st.session_state["source_conn"],
            selected_catalog
        )

        selected_schemas = st.multiselect(
            "Schema(s)",
            schemas,
            key="src_schemas"
        )

        source_selections = []

        for sch in selected_schemas:
            tables = get_tables(
                st.session_state["engine"],
                st.session_state["source_conn"],
                selected_catalog,
                sch
            )

            selected_tables = st.multiselect(
                f"Tables in {sch}",
                tables,
                key=f"src_tables_{sch}"
            )

            for tbl in selected_tables:
                source_selections.append({
                    "catalog": selected_catalog,
                    "schema": sch,
                    "table": tbl
                })

        st.session_state["source_selections"] = source_selections

    # =================================================
    # TARGET METADATA
    # =================================================
    with right:

        st.markdown("### 🎯 Databricks")

        dbx_catalogs = get_catalogs(
            "Databricks",
            st.session_state["target_conn"]
        )

        selected_dbx_catalog = st.selectbox(
            "Catalog",
            dbx_catalogs,
            key="tgt_catalog"
        )

        dbx_schemas = get_schemas(
            "Databricks",
            st.session_state["target_conn"],
            selected_dbx_catalog
        )

        selected_dbx_schemas = st.multiselect(
            "Schema(s)",
            dbx_schemas,
            key="tgt_schemas"
        )

        target_selections = []

        for sch in selected_dbx_schemas:
            tables = get_tables(
                "Databricks",
                st.session_state["target_conn"],
                selected_dbx_catalog,
                sch
            )

            selected_tables = st.multiselect(
                f"Tables in {sch}",
                tables,
                key=f"tgt_tables_{sch}"
            )

            for tbl in selected_tables:
                target_selections.append({
                    "catalog": selected_dbx_catalog,
                    "schema": sch,
                    "table": tbl
                })

        st.session_state["target_selections"] = target_selections

    # =================================================
    # OVERRIDE UI (PER TABLE)
    # =================================================
    table_pairs = list(zip(
        st.session_state.get("source_selections", []),
        st.session_state.get("target_selections", [])
    ))

    per_table_validations = {}

    if validation_type == "deep" and override_mode and table_pairs:

        st.divider()
        st.markdown("## 🛠 Override Validations Per Table")

        for i, (src, tgt) in enumerate(table_pairs):

            table_id = f"browse_{src['schema']}_{src['table']}_{i}"

            with st.container():

                st.markdown(
                    f"### 🔹 {src['schema']}.{src['table']} → "
                    f"{tgt['schema']}.{tgt['table']}"
                )

                col1, col2 = st.columns(2)

                with col1:
                    r = st.checkbox(
                        "Row Count",
                        value=False,
                        key=f"{table_id}_row"
                    )
                    s = st.checkbox(
                        "Schema",
                        value=False,
                        key=f"{table_id}_schema"
                    )

                with col2:
                    n = st.checkbox(
                        "Numeric Stats",
                        value=False,
                        key=f"{table_id}_numeric"
                    )
                    h = st.checkbox(
                        "Row Hash",
                        value=False,
                        key=f"{table_id}_hash"
                    )

                case_override = st.checkbox(
                    "Override case sensitivity",
                    value=False,
                    key=f"{table_id}_case_override",
                    help=(
                        "If checked, this table pair uses the opposite of the global "
                        "case-sensitive setting."
                    ),
                )

                effective_case_sensitive = (
                    (not browse_case_sensitive_global)
                    if case_override
                    else browse_case_sensitive_global
                )
                st.caption(
                    "Schema case sensitivity: "
                    f"{'ON' if effective_case_sensitive else 'OFF'}"
                    + (" (Override)" if case_override else "")
                )

                per_table_validations[table_id] = {
                    "row": r,
                    "schema": s,
                    "numeric": n,
                    "hash": h,
                    "case_override": case_override,
                    "src": src,
                    "tgt": tgt
                }

        st.session_state["browse_per_table_validations"] = per_table_validations

        override_count = sum(
            1
            for v in per_table_validations.values()
            if v.get("case_override")
        )
        st.info(
            "Case sensitivity default: "
            f"{'ON' if browse_case_sensitive_global else 'OFF'}"
            f" | Overrides: {override_count}/{len(per_table_validations)}"
        )

    # =================================================
    # RUN BUTTON
    # =================================================
    if st.button("🚀 Run Browse Validations", use_container_width=True):

        try:
            if not st.session_state.get("source_selections") or not st.session_state.get("target_selections"):
                st.error("❌ Please select source and target tables")
                st.stop()

            if len(st.session_state["source_selections"]) != len(st.session_state["target_selections"]):
                st.error("❌ Source and Target table counts must match")
                st.stop()

            # ==========================
            # OVERRIDE MODE
            # ==========================
            if validation_type == "deep" and override_mode:

                for table_id, config in st.session_state.get(
                    "browse_per_table_validations", {}
                ).items():

                    src = config["src"]
                    tgt = config["tgt"]

                    st.divider()
                    st.markdown(
                        f"## 🔍 {src['schema']}.{src['table']} → "
                        f"{tgt['schema']}.{tgt['table']}"
                    )

                    checks = []

                    case_override = bool(config.get("case_override"))
                    effective_case_sensitive = (
                        (not browse_case_sensitive_global)
                        if case_override
                        else browse_case_sensitive_global
                    )
                    st.caption(
                        "Schema case sensitivity: "
                        f"{'ON' if effective_case_sensitive else 'OFF'}"
                        + (" (Override)" if case_override else "")
                    )

                    # If nothing selected → fallback to global
                    if not any([
                        config["row"],
                        config["schema"],
                        config["numeric"],
                        config["hash"]
                    ]):

                        effective_row = browse_row_check
                        effective_schema = browse_schema_check
                        effective_numeric = browse_numeric_check
                        effective_hash = browse_hash_check

                    else:
                        effective_row = config["row"]
                        effective_schema = config["schema"]
                        effective_numeric = config["numeric"]
                        effective_hash = config["hash"]

                    if effective_row:
                        checks.append(("Row Count Validation",
                            lambda s=src, t=tgt: run_row_count(
                                st.session_state["engine"],
                                st.session_state["source_conn"],
                                st.session_state["target_conn"],
                                s, t
                            )))

                    if effective_schema:
                        checks.append(("Schema Validation",
                            lambda s=src, t=tgt: run_schema_validation(
                                st.session_state["engine"],
                                st.session_state["source_conn"],
                                st.session_state["target_conn"],
                                s, t,
                                case_sensitive=effective_case_sensitive,
                            )))

                    if effective_numeric:
                        checks.append(("Numeric Statistics Validation",
                            lambda s=src, t=tgt: run_numeric_validation(
                                st.session_state["engine"],
                                st.session_state["source_conn"],
                                st.session_state["target_conn"],
                                s, t
                            )))

                    if effective_hash:
                        checks.append(("Row Hash Validation",
                            lambda s=src, t=tgt: run_row_hash_validation(
                                st.session_state["engine"],
                                st.session_state["source_conn"],
                                st.session_state["target_conn"],
                                s,
                                t,
                                include_timestamp_columns=st.session_state.get(
                                    "include_timestamp_in_hash_browse", True
                                ),
                            )))

                    if checks:
                        run_checks_in_order(checks)
                    else:
                        st.warning("⚠️ No validations selected.")

            # ==========================
            # NORMAL MODE
            # ==========================
            else:

                for src, tgt in table_pairs:

                    st.divider()
                    st.markdown(
                        f"## 🔍 {src['schema']}.{src['table']} → "
                        f"{tgt['schema']}.{tgt['table']}"
                    )

                    checks = []

                    if validation_type == "shallow":

                        checks.extend([
                            ("Row Count Validation",
                             lambda s=src, t=tgt: run_row_count(
                                 st.session_state["engine"],
                                 st.session_state["source_conn"],
                                 st.session_state["target_conn"],
                                 s, t
                             )),
                            ("Schema Validation",
                             lambda s=src, t=tgt: run_schema_validation(
                                 st.session_state["engine"],
                                 st.session_state["source_conn"],
                                 st.session_state["target_conn"],
                                 s, t,
                                 case_sensitive=browse_case_sensitive_global,
                             )),
                        ])

                    else:

                        if browse_row_check:
                            checks.append(("Row Count Validation",
                                lambda s=src, t=tgt: run_row_count(
                                    st.session_state["engine"],
                                    st.session_state["source_conn"],
                                    st.session_state["target_conn"],
                                    s, t
                                )))

                        if browse_schema_check:
                            checks.append(("Schema Validation",
                                lambda s=src, t=tgt: run_schema_validation(
                                    st.session_state["engine"],
                                    st.session_state["source_conn"],
                                    st.session_state["target_conn"],
                                    s, t,
                                    case_sensitive=browse_case_sensitive_global,
                                )))

                        if browse_numeric_check:
                            checks.append(("Numeric Statistics Validation",
                                lambda s=src, t=tgt: run_numeric_validation(
                                    st.session_state["engine"],
                                    st.session_state["source_conn"],
                                    st.session_state["target_conn"],
                                    s, t
                                )))

                        if browse_hash_check:
                            checks.append(("Row Hash Validation",
                                lambda s=src, t=tgt: run_row_hash_validation(
                                    st.session_state["engine"],
                                    st.session_state["source_conn"],
                                    st.session_state["target_conn"],
                                    s,
                                    t,
                                    include_timestamp_columns=st.session_state.get(
                                        "include_timestamp_in_hash_browse", True
                                    ),
                                )))

                    if checks:
                        run_checks_in_order(checks)
                    else:
                        st.warning("⚠️ No validations selected.")

            st.success("🎉 Browse validations completed")

        except Exception as e:
            st.error(str(e))
  
