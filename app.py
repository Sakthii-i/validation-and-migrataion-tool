import streamlit as st
import pandas as pd
import uuid
from datetime import datetime, timedelta
import psycopg2
from backend.auth_crypto import hash_password
from backend.auth_service import is_admin_login, is_user_authorized
from backend.auth_store import get_pg_conn as get_auth_pg_conn, upsert_user
from connections.bigquery import connect_bigquery
from connections.databricks import connect_databricks
from connections.postgres import POSTGRES_CONFIG
from connections.snowflake import connect_snowflake
from metadata.catalog_fetcher import get_catalogs, get_schemas, get_tables
from query_builder import build_shallow_query, build_schema_query, get_numeric_columns,build_numeric_stats_query,build_row_hash_query, build_row_signature_sample_query, build_row_hash_mismatch_rows_query_v2, build_column_diff_query
import os
os.getenv("DASHBOARD_DBX_TOKEN")
import plotly.express as px
import base64
import json
import requests
import re
import html
import threading
from pathlib import Path
from api.auth import load_locked_credentials


# =========================================================
# AUTH (LOGIN GATE)
# =========================================================
if "auth_role" not in st.session_state:
    st.session_state["auth_role"] = None  # "user" | "admin" | None
if "auth_user" not in st.session_state:
    st.session_state["auth_user"] = None
if "auth_version" not in st.session_state:
    st.session_state["auth_version"] = None


@st.cache_resource
def _get_auth_version_store() -> dict:
    # Shared across Streamlit sessions/tabs (within the same server process).
    # Used to invalidate auth in other open tabs after logout.
    return {"lock": threading.Lock(), "version_by_user": {}}


def _current_auth_version(username: str) -> int:
    store = _get_auth_version_store()
    with store["lock"]:
        return int(store["version_by_user"].get(username, 0))


def _bump_auth_version(username: str) -> None:
    store = _get_auth_version_store()
    with store["lock"]:
        store["version_by_user"][username] = int(store["version_by_user"].get(username, 0)) + 1


def _apply_auth_page_style() -> None:
        st.markdown(
                """
                <style>
                    /* Make auth screens feel like a centered page */
                    .rf-auth-card {
                        padding: 22px 22px;
                        border: 1px solid rgba(0,0,0,0.08);
                        border-radius: 14px;
                        background: rgba(255,255,255,0.92);
                    }
                    .rf-auth-form {
                        padding: 10px 10px;
                    }
                    .rf-auth-title {
                        font-size: 30px;
                        font-weight: 700;
                        margin: 0 0 2px 0;
                    }
                    .rf-auth-sub {
                        opacity: 0.75;
                        margin: 0 0 14px 0;
                    }
                    /* Tighten top padding Streamlit adds */
                    section.main > div.block-container {
                        padding-top: 1.75rem;
                        max-width: 980px;
                    }
                </style>
                """,
                unsafe_allow_html=True,
        )


def _logout(*, broadcast: bool = True) -> None:
    # If logout was triggered via query param, clear it to avoid rerun loops.
    try:
        if "logout" in st.query_params:
            del st.query_params["logout"]
    except Exception:
        try:
            qp = st.experimental_get_query_params()
            if "logout" in qp:
                st.experimental_set_query_params()
        except Exception:
            pass

    if broadcast:
        u = (st.session_state.get("auth_user") or "").strip()
        if u:
            _bump_auth_version(u)

    st.session_state["auth_role"] = None
    st.session_state["auth_user"] = None
    st.session_state["auth_version"] = None
    # Keep the rest of session_state intact (connections, filters, etc.)
    st.rerun()


# Support logout via a simple link: ?logout=1
try:
    if "logout" in st.query_params:
        _logout(broadcast=True)
except Exception:
    try:
        if "logout" in (st.experimental_get_query_params() or {}):
            _logout(broadcast=True)
    except Exception:
        pass


def _enforce_logout_if_revoked() -> None:
    role = st.session_state.get("auth_role")
    if not role:
        return
    u = (st.session_state.get("auth_user") or "").strip()
    if not u:
        return

    expected = _current_auth_version(u)
    seen = st.session_state.get("auth_version")
    if seen is None:
        st.session_state["auth_version"] = expected
        return

    if int(seen) != int(expected):
        _logout(broadcast=False)


def _render_admin_page() -> None:
    _apply_auth_page_style()

    left, mid, right = st.columns([1, 1.4, 1])
    with mid:
        st.markdown(
            """
            <div class="rf-auth-card">
              <div class="rf-auth-title">🔐 Admin</div>
              <div class="rf-auth-sub">Manage which users are allowed to access the tool.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        card = st.container(border=True)
        with card:
            st.subheader("Add / Update User")
            st.caption("Passwords are stored as salted hashes in Postgres.")

            username = st.text_input("Username", placeholder="e.g. aswath", key="admin_add_username")
            password = st.text_input("Password", type="password", key="admin_add_password")

            c1, c2 = st.columns(2)
            with c1:
                if st.button("Add User", use_container_width=True, key="admin_add_user"):
                    if not username.strip() or not password:
                        st.error("Please enter username and password")
                    else:
                        try:
                            pg = get_auth_pg_conn()
                            try:
                                upsert_user(pg, username.strip(), hash_password(password))
                            finally:
                                try:
                                    pg.close()
                                except Exception:
                                    pass
                            st.success("User added/updated successfully")
                        except Exception as e:
                            st.error(f"Failed to save user: {e}")

            with c2:
                if st.button("Logout", use_container_width=True, key="admin_logout"):
                    _logout(broadcast=True)


def _render_login_page() -> None:
    _apply_auth_page_style()

    left, mid, right = st.columns([0.8, 2.2, 0.8])
    with mid:
        st.title("Reconciliation Framework")
        st.caption("Sign in to validate data consistency and completeness.")

        st.markdown("<div class=\"rf-auth-form\">", unsafe_allow_html=True)
        box = st.container(border=True)
        with box:
            user_tab, admin_tab = st.tabs(["User Login", "Admin Login"])

            with user_tab:
                u = st.text_input("Username", placeholder="Your username", key="login_user_username")
                p = st.text_input("Password", type="password", key="login_user_password")
                if st.button("Login", use_container_width=True, key="login_user_btn"):
                    try:
                        pg = get_auth_pg_conn()
                        try:
                            ok = is_user_authorized(pg, u.strip(), p)
                        finally:
                            try:
                                pg.close()
                            except Exception:
                                pass

                        if ok:
                            st.session_state["auth_role"] = "user"
                            st.session_state["auth_user"] = u.strip()
                            st.session_state["auth_version"] = _current_auth_version(u.strip())
                            st.rerun()
                        else:
                            st.error("You dont have authentication contact to administrator")
                    except Exception as e:
                        st.error(f"Login failed: {e}")

            with admin_tab:
                a = st.text_input("Admin Username", placeholder="Admin username", key="login_admin_username")
                ap = st.text_input("Admin Password", type="password", key="login_admin_password")
                if st.button("Admin Login", use_container_width=True, key="login_admin_btn"):
                    if is_admin_login(a.strip(), ap):
                        st.session_state["auth_role"] = "admin"
                        st.session_state["auth_user"] = a.strip()
                        st.session_state["auth_version"] = _current_auth_version(a.strip())
                        st.rerun()
                    else:
                        st.error("Invalid admin credentials")

        st.markdown("</div>", unsafe_allow_html=True)


_enforce_logout_if_revoked()

if not st.session_state.get("auth_role"):
    _render_login_page()
    st.stop()

if st.session_state.get("auth_role") == "admin":
    _render_admin_page()
    st.stop()

# At this point, only authenticated users continue into the tool.

# Sidebar profile (hover to show name + logout)
with st.sidebar:
        auth_user = (st.session_state.get("auth_user") or "").strip() or "User"
        safe_user = html.escape(auth_user)
        st.markdown(
            f"""
            <style>
                .rf-profile {{
                    position: relative;
                    display: inline-block;
                    margin-bottom: 8px;
                }}
                .rf-profile .rf-menu {{
                    display: none;
                    position: absolute;
                    top: 32px;
                    left: 0;
                    z-index: 1000;
                    padding: 8px 10px;
                    border: 1px solid rgba(0,0,0,0.10);
                    border-radius: 8px;
                    background: rgba(255,255,255,0.98);
                    white-space: nowrap;
                    min-width: 140px;
                }}
                .rf-profile:hover .rf-menu {{
                    display: block;
                }}
                .rf-profile .rf-icon {{
                    cursor: default;
                    user-select: none;
                    font-size: 26px;
                    line-height: 1;
                }}
                .rf-profile .rf-name {{
                    margin-bottom: 6px;
                }}
                /* Keep the Streamlit button compact inside the hover menu */
                .rf-profile .rf-menu div[data-testid="stButton"] > button {{
                    padding: 0.25rem 0.6rem;
                    width: 100%;
                }}
            </style>

            <div class="rf-profile" title="{safe_user}">
                <span class="rf-icon">👤</span>
                <div class="rf-menu">
                    <div class="rf-name">{safe_user}</div>
            """,
            unsafe_allow_html=True,
        )

        if st.button("Logout", use_container_width=True, key="user_logout"):
            _logout(broadcast=True)

        st.markdown(
            """
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

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

def get_dashboard_postgres_conn():
    """
    Returns a psycopg2 connection to the hardcoded NeonDB Postgres database.
    """
    return psycopg2.connect(
        host=POSTGRES_CONFIG["host"],
        port=POSTGRES_CONFIG["port"],
        dbname=POSTGRES_CONFIG["db"],
        user=POSTGRES_CONFIG["user"],
        password=POSTGRES_CONFIG["password"],
        sslmode=POSTGRES_CONFIG["sslmode"],
    )


DATE_FILTER_OPTIONS = [
    "All time",
    "Today",
    "Past 3 days",
    "Past 15 days",
    "Past 30 days",
    "Custom",
]


def _compute_date_range(filter_key: str, custom_start=None, custom_end=None):
    """Returns (start_date, end_date) as datetime.date objects, or (None, None) for no filter."""
    today = datetime.utcnow().date()

    if filter_key == "All time":
        return None, None

    if filter_key == "Today":
        return today, today
    if filter_key == "Past 3 days":
        return today - timedelta(days=2), today
    if filter_key == "Past 15 days":
        return today - timedelta(days=14), today
    if filter_key == "Past 30 days":
        return today - timedelta(days=29), today

    # Custom
    if custom_start is None:
        custom_start = today
    if custom_end is None:
        custom_end = today
    if custom_start > custom_end:
        custom_start, custom_end = custom_end, custom_start
    return custom_start, custom_end


def _build_validation_ts_where_clause(start_date, end_date):
    """Build a SQL WHERE clause filtering by validation_ts (inclusive date range)."""
    if start_date is None or end_date is None:
        return "TRUE"
    start_s = start_date.isoformat()
    end_s = end_date.isoformat()
    return f"validation_ts::date BETWEEN '{start_s}' AND '{end_s}'"


# ✅ ADD THIS
def missing(values):
    return any(v is None or v == "" for v in values)


def _trim_text(val):
    if isinstance(val, str):
        return val.strip()
    return val


def _get_api_base_url() -> str | None:
    v = os.getenv("VALIDATION_API_BASE_URL")
    if not v or not v.strip():
        return None
    base_url = v.strip().rstrip("/")
    # 0.0.0.0 is a bind-all address, not a usable client destination.
    base_url = base_url.replace("://0.0.0.0", "://localhost")
    return base_url


def _get_api_key() -> str | None:
    # Prefer a single key var; fall back to the first in a comma-separated list.
    v = os.getenv("VALIDATION_API_KEY")
    if v and v.strip():
        return v.strip()
    v = os.getenv("VALIDATION_API_KEYS")
    if v and v.strip():
        return v.split(",")[0].strip()
    return None


def create_backend_session_from_ui(
    credential_password: str,
    source_engine: str,
    project_id: str | None,
    dataset_location: str | None,
    bq_key_path: str | None,
    dbx_server: str | None,
    dbx_http_path: str | None,
    dbx_token: str | None,
):
    base_url = _get_api_base_url()
    api_key = _get_api_key()

    if not base_url or not api_key:
        return None

    source_engine_l = (source_engine or "").strip().lower()
    if source_engine_l == "bigquery":
        source_payload = {
            "project_id": (project_id or "").strip(),
            "dataset_location": (dataset_location or "US").strip(),
            "service_account_key_path": (bq_key_path or "").strip(),
        }
        target_payload = {
            "server_hostname": (dbx_server or "").strip(),
            "http_path": (dbx_http_path or "").strip(),
            "access_token": (dbx_token or "").strip(),
        }
    else:
        source_payload = {}
        target_payload = None

    payload = {
        "source_engine": source_engine_l,
        "credential_password": (credential_password or "").strip(),
        "source": source_payload,
    }
    
    if target_payload:
        payload["target"] = target_payload

    resp = requests.post(
        f"{base_url}/sessions",
        headers={"x-api-key": api_key},
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

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
    """
    Normalizes query result keys to lowercase
    to handle engine-specific casing differences
    """
    return {k.lower(): v for k, v in row.items()}


def friendly_error(exc) -> str:
    """
    Convert exceptions into a cleaned, user-friendly message.

    Behavior:
    - Map common DB/Databricks/Postgres errors to helpful messages
    - Strip SQLSTATE codes, Databricks prefixes, line/position refs, Java classpaths
    - Truncate to 200 chars and provide a generic fallback message
    """
    s = "" if exc is None else str(exc)

    # Specific mappings (best-effort)
    low = s.lower()
    # Column unresolved / SQLSTATE 42703
    if re.search(r"unresolved_column|sqlstate\s*:?\s*42703|cannot be resolved|column .*not found", low, re.I):
        # try to extract a column name
        m = re.search(r"['\"]?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?\s*(?:cannot be resolved|not found|was not found)", s, re.I)
        if not m:
            m = re.search(r"column\s+([a-zA-Z_][a-zA-Z0-9_]*)", s, re.I)
        if m:
            col = m.group(1)
            return f"Column '{col}' not found in target table. Run Schema Validation first to identify mismatches."
        return "Column not found in target table. Run Schema Validation first to identify mismatches."

    # Table not found
    if re.search(r"table not found|table_not_found|does not exist|table .*does not exist", low, re.I):
        return "Table not found — check catalog, schema, table name"

    # Permission issues
    if re.search(r"permission denied|access denied|insufficient privilege|not authorized|authorization failed", low, re.I):
        return "Permission denied — check credentials have SELECT access"

    # Query timeout
    if re.search(r"timed out|timeout|query.*timed out", low, re.I):
        return "Query timed out — table may be too large"

    # Postgres socket/connect issues
    if re.search(r"could not connect to server|socket|could not connect|connection refused|no such file or directory", low, re.I):
        return "Could not connect to database — check your credentials"

    # General cleanup steps
    # 1) strip SQLSTATE fragments
    s = re.sub(r";?\s*SQLSTATE: ?[0-9A-Z]+;?", "", s, flags=re.I)
    # 2) strip databricks/unresolved prefixes like [UNRESOLVED_COLUMN.WITH_SUGGESTION]
    s = re.sub(r"\[[A-Z0-9_\.\-]+\]", "", s)
    # 3) strip line/pos references
    s = re.sub(r"line\s*\d+\s*pos\s*\d+", "", s, flags=re.I)
    s = re.sub(r"line\s*\d+", "", s, flags=re.I)
    s = re.sub(r"pos\s*\d+", "", s, flags=re.I)
    # 4) strip Java classpaths (simple heuristic)
    s = re.sub(r"org\.apache[\.\w\$]*", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    if not s:
        return "An unexpected error occurred — check your table names and connection."

    # Truncate to 200 chars
    if len(s) > 200:
        s = s[:197].rstrip() + "..."

    return s

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
    """Return base64 for an icon path, or None if missing."""
    try:
        p = Path(path)
        candidates = []
        if p.is_absolute():
            candidates.append(p)
        else:
            candidates.append(Path.cwd() / p)
            candidates.append(Path(__file__).resolve().parent / p)

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                data = candidate.read_bytes()
                return base64.b64encode(data).decode()
    except Exception:
        # Never crash the app due to a missing banner icon.
        return None
    return None

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
            # Use a run-level timestamp if present so multiple inserts in one run share the same ts
            "timestamp": st.session_state.get("current_run_ts", datetime.utcnow().isoformat()),
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
def ensure_validation_table(pg_conn):
    """Create schema and validation_results table if they don't exist."""
    ddl = """
    CREATE SCHEMA IF NOT EXISTS table_validation;

    CREATE TABLE IF NOT EXISTS table_validation.validation_results (
        validation_id TEXT PRIMARY KEY,
        validation_ts TIMESTAMPTZ,
        src_table_name TEXT,
        tgt_table_name TEXT,
        validation_type TEXT,
        row_count TEXT,
        schema_check TEXT,
        numeric_check TEXT,
        hash_validation TEXT
    );
    """
    cur = pg_conn.cursor()
    # Execute each statement separately because psycopg2 doesn't allow multiple
    # statements in a single execute when autocommit is off.
    for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
        cur.execute(stmt)
    pg_conn.commit()
    cur.close()

def insert_validation_result(conn, record):
    try:
        # Use hardcoded Postgres config to insert validation results
        pg = get_dashboard_postgres_conn()

        # ensure table exists before inserting
        try:
            ensure_validation_table(pg)
        except Exception as e:
            st.warning(f"Could not create validation table: {friendly_error(e)}")
            # continue to attempt insert

        cur = pg.cursor()
        insert_sql = """
        INSERT INTO table_validation.validation_results (
            validation_id,
            validation_ts,
            src_table_name,
            tgt_table_name,
            validation_type,
            row_count,
            schema_check,
            numeric_check,
            hash_validation
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """

        params = (
            record.get("validation_id"),
            record.get("timestamp"),
            record.get("src_table_name"),
            record.get("tgt_table_name"),
            record.get("validation_type"),
            record.get("row_count"),
            record.get("schema_check"),
            record.get("numeric_check"),
            record.get("hash_validation"),
        )

        cur.execute(insert_sql, params)
        pg.commit()

        cur.close()
        pg.close()

        st.session_state["last_insert_id"] = record.get("validation_id")
        return record.get("validation_id")

    except Exception as e:
        st.error(f"Insert to Postgres failed: {friendly_error(e)}")
        import traceback
        traceback.print_exc()
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        try:
            if pg:
                pg.close()
        except Exception:
            pass
        return None

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
        "source_catalog",
        "source_schema",
        "source_table",
        "target_catalog",
        "target_schema",
        "target_table",
        "validation_type",
        "case_sensitive",
        "include_timestamp",   # ✅ NEW
    }

    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        raise ValueError(f"Missing columns: {missing_cols}")

    if not df["validation_type"].str.lower().isin(["shallow", "deep"]).all():
        raise ValueError("validation_type must be shallow or deep")

    valid_bool = {"yes", "no", "true", "false", "1", "0"}

    if not df["case_sensitive"].astype(str).str.lower().isin(valid_bool).all():
        raise ValueError("case_sensitive must be yes/no/true/false/1/0")

    if not df["include_timestamp"].astype(str).str.lower().isin(valid_bool).all():
        raise ValueError("include_timestamp must be yes/no/true/false/1/0")

    # Validate optional row_threshold column if present
    if "row_threshold" in df.columns:
        for idx, val in df["row_threshold"].items():
            if pd.notna(val) and str(val).strip() != "":
                try:
                    fval = float(val)
                    if fval < 0 or fval > 1:
                        raise ValueError(f"row_threshold must be between 0 and 1, got {fval} at row {idx}")
                except (ValueError, TypeError):
                    raise ValueError(f"row_threshold must be a number between 0 and 1, got '{val}' at row {idx}")

        
def run_csv_validations(df):


    for idx, row in df.iterrows():
        st.markdown(f"### ▶ Processing row {idx + 1}")

        # --------------------------
        # Build Source & Target
        # --------------------------
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

        validation_type = str(row["validation_type"]).strip().lower()

        # --------------------------
        # 🔑 NEW: Read Case Sensitive from CSV
        # --------------------------
        case_value = str(row["case_sensitive"]).strip().lower()

        case_sensitive = case_value in ["yes", "true", "1"]

        # --------------------------
        # 🔑 Read include_timestamp from CSV  ✅ INSERT HERE
        # --------------------------
        ts_value = str(row["include_timestamp"]).strip().lower()
        include_timestamp = ts_value in ["yes", "true", "1"]

        # --------------------------
        # 🔑 Read row_threshold from CSV (optional)
        # --------------------------
        row_threshold = None
        if "row_threshold" in row.index:
            rt_val = row["row_threshold"]
            if pd.notna(rt_val) and str(rt_val).strip() != "":
                try:
                    row_threshold = float(rt_val)
                except (ValueError, TypeError):
                    row_threshold = None

        # --------------------------
        # Determine Metrics
        # --------------------------
        if validation_type == "deep":

            if "metrics" not in row or pd.isna(row["metrics"]):
                st.error("❌ Deep validation requires 'metrics' column.")
                continue

            metrics_list = [
                m.strip().lower()
                for m in str(row["metrics"]).split(",")
                if m.strip()
            ]

            selected_validations = {
                "row_count": "row_count" in metrics_list,
                "schema": "schema" in metrics_list,
                "numeric": "numeric" in metrics_list,
                "hash": "hash" in metrics_list,
            }

            if not any(selected_validations.values()):
                st.warning("⚠️ No valid metrics specified. Skipping row.")
                continue

        else:  # shallow
            selected_validations = {
                "row_count": True,
                "schema": True,
                "numeric": False,
                "hash": False,
            }

        

        # --------------------------
        # SHALLOW
        # --------------------------
        if validation_type == "shallow":

            row_res = run_row_count(
                st.session_state["engine"],
                st.session_state["source_conn"],
                st.session_state["target_conn"],
                src, tgt,
                threshold=row_threshold,
            )

            schema_res = run_schema_validation(
                st.session_state["engine"],
                st.session_state["source_conn"],
                st.session_state["target_conn"],
                src, tgt,
                case_sensitive=case_sensitive,   # ✅ USING CSV VALUE
            )

            record = generate_validation_record(
                "shallow",
                src,
                tgt,
                bool_to_status(row_res),
                bool_to_status(schema_res),
                "N/A",
                "N/A"
            )

        # --------------------------
        # DEEP
        # --------------------------
        else:

            checks = []

            if selected_validations["row_count"]:
                checks.append(("Row Count Validation",
                    lambda s=src, t=tgt, thr=row_threshold: run_row_count(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        s, t,
                        threshold=thr,
                    )))

            if selected_validations["schema"]:
                checks.append(("Schema Validation",
                    lambda s=src, t=tgt: run_schema_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        s, t,
                        case_sensitive=case_sensitive,  # ✅ USING CSV VALUE
                    )))

            if selected_validations["numeric"]:
                checks.append(("Numeric Statistics Validation",
                    lambda s=src, t=tgt, thr=row_threshold: run_numeric_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        s, t,
                        threshold=thr,
                    )))

            if selected_validations["hash"]:
                checks.append(("Row Hash Validation",
                    lambda s=src, t=tgt, thr=row_threshold: run_row_hash_validation(
                        st.session_state["engine"],
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        s,
                        t,
                        include_timestamp_columns=include_timestamp,
                        threshold=thr,
                    )))

            results_map = run_checks_in_order(checks)

            record = generate_validation_record(
                "deep",
                src,
                tgt,
                bool_to_status(results_map.get("Row Count Validation")),
                bool_to_status(results_map.get("Schema Validation")),
                bool_to_status(results_map.get("Numeric Statistics Validation")),
                bool_to_status(results_map.get("Row Hash Validation")),
            )

        # --------------------------
        # Insert Result
        # --------------------------
        insert_id = insert_validation_result(
            st.session_state["target_conn"],
            record
        )

        if insert_id:
            st.success("✅ Validation completed")
            st.info(f"Postgres insert committed: {insert_id}")
        else:
            st.error("Postgres insert failed — check logs or credentials")
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

        insert_id = insert_validation_result(
            st.session_state["target_conn"],
            record
        )

        st.success(
            f"✅ Validation completed for "
            f"{src['catalog']}.{src['schema']}.{src['table']} → "
            f"{tgt['catalog']}.{tgt['schema']}.{tgt['table']}"
        )
        if insert_id:
            st.info(f"Postgres insert committed: {insert_id}")
        else:
            st.error("Postgres insert failed — check logs or credentials")

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
    key = "schema_check"

    # default selections (safe defaults to avoid undefined names)
    row_count_check = True
    schema_check = True

    metrics = {"row_count": row_count_check}

    if not any(metrics.values()) and not schema_check:
        st.warning("⚠️ Select at least one validation")
        return

    if not st.button("🚀 Run Validation", use_container_width=True):
        return

    # =============================
    # ROW COUNT VALIDATION
    # =============================
def run_row_count(engine, source_conn, target_conn, src, tgt, threshold=None):
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

    src_count = src_res["row_count"]
    tgt_count = tgt_res["row_count"]

    if src_count == tgt_count:
        c3.success("✅ PASS (Exact Match)")
        return True
    elif threshold is not None and threshold > 0:
        # Calculate match ratio
        if src_count == 0 and tgt_count == 0:
            c3.success("✅ PASS (Both Empty)")
            return True
        elif src_count == 0 or tgt_count == 0:
            match_ratio = 0.0
        else:
            match_ratio = min(src_count, tgt_count) / max(src_count, tgt_count)

        if match_ratio >= threshold:
            c3.success(f"✅ PASS ({match_ratio:.4%} ≥ {threshold:.4%} threshold)")
            st.caption(f"Row count difference: {abs(src_count - tgt_count)} rows | Match ratio: {match_ratio:.4%}")
            return True
        else:
            c3.error(f"❌ FAIL ({match_ratio:.4%} < {threshold:.4%} threshold)")
            st.caption(f"Row count difference: {abs(src_count - tgt_count)} rows | Match ratio: {match_ratio:.4%}")
            return False
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
# COLUMN-LEVEL DIFF
# =============================
def _fetch_col_diff_available_cols(engine, source_conn, target_conn, src, tgt, include_timestamp):
    """
    Fetch schema from both sides and return the sorted list of common column
    names (lowercase), filtered by the same rules used during hashing.
    Called on-demand when the user checks Perform column-level diff.
    """
    try:
        src_schema = fetch_schema(engine, source_conn, src["catalog"], src["schema"], src["table"])
        tgt_schema = fetch_schema("Databricks", target_conn, tgt["catalog"], tgt["schema"], tgt["table"])
    except Exception:
        return []

    def _keep(col_dict, include_ts):
        dtype = str(col_dict.get("data_type") or "").upper()
        if any(x in dtype for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
            return False
        if not include_ts and ("TIMESTAMP" in dtype or "DATETIME" in dtype):
            return False
        return True

    src_names = {r["column_name"].lower() for r in src_schema if r.get("column_name") and _keep(r, include_timestamp)}
    tgt_names = {r["column_name"].lower() for r in tgt_schema if r.get("column_name") and _keep(r, include_timestamp)}
    return sorted(src_names & tgt_names)


_MISSING_SENTINEL = "⬛ MISSING ROW"


def _normalize_val(v) -> str:
    """Convert any DB value to a trimmed, comparable string.

    - None -> "<NULL>"
    - bytes/bytearray/memoryview -> decoded UTF-8 if possible, else base64
    - string forms like "b'ABC'" or "bytearray(b'ABC')" -> extract inner bytes content
    - otherwise return trimmed string
    """
    if v is None:
        return "<NULL>"

    # Raw bytes-like objects: decode if possible, otherwise base64-encode
    if isinstance(v, (bytes, bytearray, memoryview)):
        try:
            return bytes(v).decode("utf-8", errors="strict").strip()
        except Exception:
            return base64.b64encode(bytes(v)).decode("ascii").strip()

    s = str(v).strip()

    # Common string representations of byte values: b'xxx' or b"xxx"
    m = re.match(r"^b['\"](.*)['\"]$", s)
    if m:
        return m.group(1).strip()

    # Patterns like bytearray(b'xxx') or other wrappers containing b'...'
    m2 = re.search(r"b['\"](.*)['\"]", s)
    if m2:
        return m2.group(1).strip()

    return s


def run_column_level_diff(
    engine: str,
    source_conn,
    target_conn,
    src: dict,
    tgt: dict,
    key_columns: list[str],
    src_schema_rows: list[dict],
    tgt_schema_rows: list[dict],
    missing_in_target_hashes: set,
    extra_in_target_hashes: set,
    src_sig_cols: list[str],
    tgt_sig_cols: list[str],
    include_timestamp_columns: bool = True,
) -> None:
    """
    Perform a column-level diff between source and target for rows whose
    row-hash mismatched.  Called only when the user has opted in and
    provided at least one key column.

    Renders results directly into the Streamlit UI.
    """
    st.subheader("🔬 Column-Level Diff")

    # ── 1. Determine columns available in both sides ─────────────────────────
    def _is_excluded(schema_rows, col_name_lower, include_ts):
        """Return True if this column should be excluded (same rules as hash)."""
        for r in schema_rows:
            if r.get("column_name", "").lower() == col_name_lower:
                dtype = str(r.get("data_type") or "").upper()
                if any(x in dtype for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
                    return True
                if not include_ts and ("TIMESTAMP" in dtype or "DATETIME" in dtype):
                    return True
        return False

    src_col_names = [r["column_name"] for r in src_schema_rows if r.get("column_name")]
    tgt_col_names = [r["column_name"] for r in tgt_schema_rows if r.get("column_name")]

    src_col_lower = {c.lower(): c for c in src_col_names}
    tgt_col_lower = {c.lower(): c for c in tgt_col_names}

    # Validate key columns exist in both schemas
    invalid_keys = [k for k in key_columns if k.lower() not in src_col_lower or k.lower() not in tgt_col_lower]
    if invalid_keys:
        st.error(f"❌ Key column(s) not found in both source and target: {invalid_keys}")
        return

    # Non-key columns that exist on both sides, filtered by the same exclusion
    # rules used during hashing (timestamp, complex types).
    common_lower = set(src_col_lower.keys()) & set(tgt_col_lower.keys())
    key_lower = {k.lower() for k in key_columns}
    nonkey_lower = sorted(
        c for c in (common_lower - key_lower)
        if not _is_excluded(src_schema_rows, c, include_timestamp_columns)
        and not _is_excluded(tgt_schema_rows, c, include_timestamp_columns)
    )

    # All columns to fetch for each side: keys + non-keys (in their native case)
    def _build_fetch_cols(col_lower_map: dict, keys: list[str], nonkeys_sorted: list[str]) -> list[str]:
        ordered = [col_lower_map[k.lower()] for k in keys]
        ordered += [col_lower_map[nk] for nk in nonkeys_sorted if nk in col_lower_map]
        return ordered

    src_fetch_cols = _build_fetch_cols(src_col_lower, key_columns, nonkey_lower)
    tgt_fetch_cols = _build_fetch_cols(tgt_col_lower, key_columns, nonkey_lower)

    # ── 2. Fetch mismatched rows from source (hashes only in src) ────────────
    # We need the actual key-column values.  The mismatch-row query returns
    # hash_value + row_signature (pipe-delimited string).  We need to re-query
    # the table directly with the composite key so we can do per-column lookup.
    #
    # Strategy: fetch all mismatched rows from source and target, then build
    # composite-key dictionaries and compare.

    def _fetch_raw_rows_by_hash(
        eng: str,
        conn,
        cat: str, sch: str, tbl: str,
        schema_rows_for_hash: list[dict],
        hash_set: set,
        fetch_cols: list[str],
        include_ts: bool,
    ) -> dict:
        """
        Returns {composite_key_tuple: {col: raw_value, ...}, ...}
        """
        if not hash_set:
            return {}

        # Build a mismatch-rows query to get hash_value + row_signature
        hash_list = [str(h).upper() for h in hash_set if h]
        if not hash_list:
            return {}

        q = build_row_hash_mismatch_rows_query_v2(
            eng, cat, sch, tbl,
            schema_rows=schema_rows_for_hash,
            hash_values=hash_list,
            include_timestamp=include_ts,
            timestamp_mode=None,
            limit=len(hash_list),
        )
        rows = execute_query(eng, conn, q)

        if not rows:
            return {}

        # Now we have hash_value + row_signature — we need key column values.
        # Re-fetch using the composite key by querying directly.
        # We'll collect all keys from the signature, then do one batched fetch.
        # Since the signature is pipe-delimited and columns are sorted
        # alphabetically (same order as hash query), extract key positions.

        sig_col_list = [
            r["column_name"]
            for r in sorted(schema_rows_for_hash, key=lambda x: str(x.get("column_name", "")).lower())
            if r.get("column_name") and
               not any(t in str(r.get("data_type", "")).upper() for t in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]) and
               (include_ts or not any(t in str(r.get("data_type", "")).upper() for t in ["TIMESTAMP", "DATETIME"]))
        ]

        # Determine key column positions within sig_col_list
        key_positions = []
        for kc in fetch_cols[:len(key_columns)]:  # first N entries are key cols
            kc_lower = kc.lower()
            for idx, sc in enumerate(sig_col_list):
                if sc.lower() == kc_lower:
                    key_positions.append(idx)
                    break

        # Extract composite-key tuples from the pipe-split signature
        key_tuples = set()
        for row in rows:
            sig_col = None
            for col in row.keys():
                if col.lower() == "row_signature":
                    sig_col = col
                    break
            if sig_col is None:
                continue
            parts = str(row[sig_col]).split("|")
            if len(parts) < len(sig_col_list):
                continue
            try:
                key_tuple = tuple(parts[pos] for pos in key_positions)
            except IndexError:
                continue
            key_tuples.add(key_tuple)

        if not key_tuples:
            return {}

        # Fetch actual column data using the extracted keys
        diff_q = build_column_diff_query(
            eng, cat, sch, tbl,
            key_columns=[c.lower() if eng == "snowflake" else c for c in fetch_cols[:len(key_columns)]],
            all_columns=fetch_cols,
            mismatch_key_values=list(key_tuples),
        )
        raw_rows = execute_query(eng, conn, diff_q)

        result = {}
        for raw in raw_rows:
            raw_lower = {k.lower(): v for k, v in raw.items()}
            key_vals = tuple(
                _normalize_val(raw_lower.get(kc.lower()))
                for kc in fetch_cols[:len(key_columns)]
            )
            result[key_vals] = {k.lower(): v for k, v in raw_lower.items()}
        return result

    # ── 3. Build schema rows for hash (same filtering as the main hash query) ──
    def _build_hash_schema(schema_rows: list[dict], include_ts: bool) -> list[dict]:
        out = []
        for r in schema_rows:
            col = r.get("column_name")
            dtype = str(r.get("data_type") or "").upper()
            if not col:
                continue
            if any(x in dtype for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
                continue
            if not include_ts and ("TIMESTAMP" in dtype or "DATETIME" in dtype):
                continue
            out.append(r)
        return out

    src_hash_schema = _build_hash_schema(src_schema_rows, include_timestamp_columns)
    tgt_hash_schema = _build_hash_schema(tgt_schema_rows, include_timestamp_columns)

    with st.spinner("Fetching mismatched rows for column-level diff…"):
        try:
            src_rows_map = _fetch_raw_rows_by_hash(
                engine, source_conn,
                src["catalog"], src["schema"], src["table"],
                src_hash_schema, missing_in_target_hashes,
                src_fetch_cols, include_timestamp_columns,
            )
        except Exception as e:
            st.error(f"Could not fetch source diff rows: {friendly_error(e)}")
            src_rows_map = {}

        try:
            tgt_rows_map = _fetch_raw_rows_by_hash(
                "databricks", target_conn,
                tgt["catalog"], tgt["schema"], tgt["table"],
                tgt_hash_schema, extra_in_target_hashes,
                tgt_fetch_cols, include_timestamp_columns,
            )
        except Exception as e:
            st.error(f"Could not fetch target diff rows: {friendly_error(e)}")
            tgt_rows_map = {}

    if not src_rows_map and not tgt_rows_map:
        st.warning("No diff rows could be retrieved — cannot produce column-level diff.")
        return

    # ── 4. Compare row-by-row ─────────────────────────────────────────────────
    all_keys = sorted(set(src_rows_map.keys()) | set(tgt_rows_map.keys()))

    diff_records = []

    for key_tuple in all_keys:
        composite_key_str = " | ".join(
            f"{kc}={kv}" for kc, kv in zip(key_columns, key_tuple)
        )

        src_row = src_rows_map.get(key_tuple)
        tgt_row = tgt_rows_map.get(key_tuple)

        if src_row is None:
            # Row exists only in target
            for nk in nonkey_lower:
                tgt_val = tgt_row.get(nk)
                diff_records.append({
                    "Composite Key": composite_key_str,
                    "Column": nk,
                    "Source Value": _MISSING_SENTINEL,
                    "Target Value": _normalize_val(tgt_val),
                    "Status": "🔴 MISSING IN SOURCE",
                })
        elif tgt_row is None:
            # Row exists only in source
            for nk in nonkey_lower:
                src_val = src_row.get(nk)
                diff_records.append({
                    "Composite Key": composite_key_str,
                    "Column": nk,
                    "Source Value": _normalize_val(src_val),
                    "Target Value": _MISSING_SENTINEL,
                    "Status": "🔴 MISSING IN TARGET",
                })
        else:
            # Row exists in both — compare non-key columns
            for nk in nonkey_lower:
                src_val = _normalize_val(src_row.get(nk))
                tgt_val = _normalize_val(tgt_row.get(nk))
                if src_val != tgt_val:
                    diff_records.append({
                        "Composite Key": composite_key_str,
                        "Column": nk,
                        "Source Value": src_val,
                        "Target Value": tgt_val,
                        "Status": "⚠️ VALUE MISMATCH",
                    })

    # ── 5. Render results ─────────────────────────────────────────────────────
    total_rows_compared = len(all_keys)
    mismatch_rows = sum(1 for r in diff_records if "MISMATCH" in r["Status"])
    missing_src = sum(1 for r in diff_records if "MISSING IN SOURCE" in r["Status"])
    missing_tgt = sum(1 for r in diff_records if "MISSING IN TARGET" in r["Status"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows Compared", total_rows_compared)
    m2.metric("Value Mismatches", mismatch_rows)
    m3.metric("Missing in Source", missing_src)
    m4.metric("Missing in Target", missing_tgt)

    if not diff_records:
        st.success("✅ No column-level differences found in the sampled rows (hashes may differ due to data-type normalisation).")
        return

    diff_df = pd.DataFrame(diff_records)

    # Summary: which columns differ most often
    if "VALUE MISMATCH" in diff_df["Status"].values:
        mismatch_cols = (
            diff_df[diff_df["Status"] == "⚠️ VALUE MISMATCH"]
            .groupby("Column")
            .size()
            .reset_index(name="Mismatch Count")
            .sort_values("Mismatch Count", ascending=False)
        )
        with st.expander(f"📊 Columns with Most Mismatches ({len(mismatch_cols)} column(s))", expanded=True):
            st.dataframe(mismatch_cols, use_container_width=True)

    st.subheader("📋 Detailed Column-Level Diff")
    st.dataframe(diff_df, use_container_width=True, height=400)

    # Download
    csv_bytes = diff_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇ Download Diff as CSV",
        data=csv_bytes,
        file_name="column_diff_report.csv",
        mime="text/csv",
    )


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
    threshold=None,
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
    elif threshold is not None and threshold > 0:
        # Calculate hash match ratio
        total_unique = len(src_hashes | tgt_hashes)
        matching = len(src_hashes & tgt_hashes)
        if total_unique == 0:
            match_ratio = 1.0
        else:
            match_ratio = matching / total_unique

        if match_ratio >= threshold:
            c3.success(f"✅ PASS ({match_ratio:.4%} ≥ {threshold:.4%} threshold)")
            st.caption(f"Matching hashes: {matching}/{total_unique} | Match ratio: {match_ratio:.4%}")
            return True
        else:
            c3.error(f"❌ FAIL ({match_ratio:.4%} < {threshold:.4%} threshold)")
            st.caption(f"Matching hashes: {matching}/{total_unique} | Match ratio: {match_ratio:.4%}")
    else:
        c3.error("❌ HASH MISMATCH")

    st.warning(
        "💡 **Column comparison below:** "
        "Rows are split by column to identify which field(s) have different values."
    )

    # Detailed mismatch analysis — persist to session_state so the
    # column-diff UI survives Streamlit reruns triggered by widget interaction.
    missing_in_target = src_hashes - tgt_hashes
    extra_in_target = tgt_hashes - src_hashes

    # Build common column names respecting the same timestamp exclusion
    # used during hashing — so available PK options match exactly.
    def _is_timestamp_col(col_dict):
        dtype = str(col_dict.get("raw_type") or col_dict.get("type") or "").upper()
        return "TIMESTAMP" in dtype or "DATETIME" in dtype

    common_col_names = sorted(
        set(c.get("name", "").lower() for c in src_columns if c.get("name")
            and (include_timestamp_columns or not _is_timestamp_col(c)))
        & set(c.get("name", "").lower() for c in tgt_columns if c.get("name")
            and (include_timestamp_columns or not _is_timestamp_col(c)))
    )

    # Populate the available-cols list for whichever tab triggered this run,
    # so the multiselect is pre-filled on the next rerender.
    for _tab in ("browse", "manual", "config"):
        if st.session_state.get(f"col_diff_enabled_{_tab}"):
            st.session_state[f"col_diff_available_cols_{_tab}"] = common_col_names

    st.session_state["_col_diff_state"] = {
        "engine": engine,
        "src": src,
        "tgt": tgt,
        "src_schema_rows": src_schema_rows,
        "tgt_schema_rows": tgt_schema_rows,
        "src_columns": src_columns,
        "tgt_columns": tgt_columns,
        "missing_in_target": missing_in_target,
        "extra_in_target": extra_in_target,
        "include_timestamp_columns": include_timestamp_columns,
        "common_col_names": common_col_names,
    }

    # Auto-run column diff immediately if the user already opted in
    # and selected key columns before running validation.
    for _tab in ("browse", "manual", "config"):
        if st.session_state.get(f"col_diff_enabled_{_tab}"):
            _key_cols = st.session_state.get(f"col_diff_key_columns_{_tab}", [])
            if _key_cols:
                st.divider()
                try:
                    run_column_level_diff(
                        engine=engine,
                        source_conn=source_conn,
                        target_conn=target_conn,
                        src=src,
                        tgt=tgt,
                        key_columns=_key_cols,
                        src_schema_rows=src_schema_rows,
                        tgt_schema_rows=tgt_schema_rows,
                        missing_in_target_hashes=missing_in_target,
                        extra_in_target_hashes=extra_in_target,
                        src_sig_cols=src_columns,
                        tgt_sig_cols=tgt_columns,
                        include_timestamp_columns=include_timestamp_columns,
                    )
                except Exception as _e:
                    st.error(f"Column-level diff failed: {friendly_error(_e)}")
                break  # only run once

    m1, m2 = st.columns(2)
    m1.metric("Missing in Target", len(missing_in_target))
    m2.metric("Extra in Target", len(extra_in_target))
    
    # Summary statistics
    total_unique = len(src_hashes | tgt_hashes)
    matching = len(src_hashes & tgt_hashes)
    if total_unique > 0:
        st.info(f"**Summary:** {matching} of {total_unique} unique rows match ({100*matching/total_unique:.1f}%)")

    st.subheader("Column-Level Breakdown (Sample Rows)")
    
    # Build schema rows for display
    try:
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
            st.caption("Columns included in hash (in order):")
            st.code(" | ".join(sig_cols), language="text")
        
        df_missing = None
        df_extra = None

        if missing_in_target and len(missing_in_target) > 0:
            st.subheader("🔴 Source-Only Rows (in source, NOT in target)")
            try:
                missing_hashes = [str(h).upper() for h in list(missing_in_target)[:50] if h]
                if missing_hashes:
                    q = build_row_hash_mismatch_rows_query_v2(
                        engine,
                        src["catalog"],
                        src["schema"],
                        src["table"],
                        schema_rows=src_schema_rows_for_hash,
                        hash_values=missing_hashes,
                        include_timestamp=include_timestamp_columns,
                        timestamp_mode=None,
                        limit=10,
                    )
                    rows = execute_query(engine, source_conn, q)

                    if rows:
                        df_missing = pd.DataFrame(rows)
                        # Find row_signature column (case-insensitive)
                        sig_col = None
                        for col in df_missing.columns:
                            if col.lower() == 'row_signature':
                                sig_col = col
                                break
                        
                        if sig_col and sig_cols:
                            parts = df_missing[sig_col].astype(str).str.split(r"\|", expand=True, regex=True)
                            if parts.shape[1] >= len(sig_cols):
                                parts.columns = sig_cols[:parts.shape[1]]
                                df_missing = pd.concat([df_missing.drop(columns=[sig_col]), parts], axis=1)
                        st.dataframe(df_missing, use_container_width=True, height=200)
                    else:
                        st.info("No sample rows found")
            except Exception as e:
                st.error(f"Could not fetch source-only rows: {friendly_error(e)}")

        if extra_in_target and len(extra_in_target) > 0:
            st.subheader("🟢 Target-Only Rows (in target, NOT in source)")
            try:
                extra_hashes = [str(h).upper() for h in list(extra_in_target)[:50] if h]
                if extra_hashes:
                    q = build_row_hash_mismatch_rows_query_v2(
                        "databricks",
                        tgt["catalog"],
                        tgt["schema"],
                        tgt["table"],
                        schema_rows=tgt_schema_rows_for_hash,
                        hash_values=extra_hashes,
                        include_timestamp=include_timestamp_columns,
                        timestamp_mode=None,
                        limit=10,
                    )
                    rows = execute_query("Databricks", target_conn, q)

                    if rows:
                        df_extra = pd.DataFrame(rows)
                        # Find row_signature column (case-insensitive)
                        sig_col = None
                        for col in df_extra.columns:
                            if col.lower() == 'row_signature':
                                sig_col = col
                                break
                        
                        if sig_col and sig_cols:
                            parts = df_extra[sig_col].astype(str).str.split(r"\|", expand=True, regex=True)
                            if parts.shape[1] >= len(sig_cols):
                                parts.columns = sig_cols[:parts.shape[1]]
                                df_extra = pd.concat([df_extra.drop(columns=[sig_col]), parts], axis=1)
                        st.dataframe(df_extra, use_container_width=True, height=200)
                    else:
                        st.info("No sample rows found")
            except Exception as e:
                st.error(f"Could not fetch target-only rows: {friendly_error(e)}")

        # Automatic column diff if both samples exist
        if df_missing is not None and df_extra is not None and len(sig_cols) > 0:
            if len(df_missing) > 0 and len(df_extra) > 0:
                try:
                    # Parse signatures for both dataframes (case-insensitive column lookup)
                    def get_signature_col(df):
                        for col in df.columns:
                            if col.lower() == 'row_signature':
                                return col
                        return None
                    
                    sig_col_missing = get_signature_col(df_missing)
                    sig_col_extra = get_signature_col(df_extra)
                    
                    # If signature exists but columns not parsed, parse them now
                    if sig_col_missing and 'row_signature' not in df_missing.columns:
                        parts = df_missing[sig_col_missing].astype(str).str.split(r"\|", expand=True, regex=True)
                        if parts.shape[1] >= len(sig_cols):
                            parts.columns = sig_cols[:parts.shape[1]]
                            df_missing = pd.concat([df_missing.drop(columns=[sig_col_missing]), parts], axis=1)
                    
                    if sig_col_extra and 'row_signature' not in df_extra.columns:
                        parts = df_extra[sig_col_extra].astype(str).str.split(r"\|", expand=True, regex=True)
                        if parts.shape[1] >= len(sig_cols):
                            parts.columns = sig_cols[:parts.shape[1]]
                            df_extra = pd.concat([df_extra.drop(columns=[sig_col_extra]), parts], axis=1)
                    
                    # Get available columns from dataframe
                    available_cols = [c for c in sig_cols if c in df_missing.columns and c in df_extra.columns]
                    
                    if available_cols:
                        row_s = df_missing.iloc[0][available_cols]
                        row_t = df_extra.iloc[0][available_cols]
                        
                        diff_cols = [c for c in available_cols if str(row_s.get(c, "")).strip() != str(row_t.get(c, "")).strip()]
                    else:
                        st.warning("Could not extract column values from signature rows for comparison.")
                except Exception as e:
                    st.error(f"Error comparing rows: {friendly_error(e)}")


    except Exception as e:
        st.warning(f"Could not build detailed column breakdown: {friendly_error(e)}")

    return False
def approx_equal(a, b, tol=1e-6):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol
    
def run_numeric_validation(engine, source_conn, target_conn, src, tgt, threshold=None):
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

    # Compute variance tolerance from threshold (e.g., 0.99 → 1% tolerance)
    variance_tolerance = (1 - threshold) if (threshold is not None and threshold > 0) else None

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

            if variance_tolerance is not None:
                # Variance-based comparison
                def _within_variance(a, b, tol):
                    if a is None or b is None:
                        return a is None and b is None
                    if a == 0 and b == 0:
                        return True
                    denominator = max(abs(a), abs(b))
                    if denominator == 0:
                        return True
                    return abs(a - b) / denominator <= tol

                min_match = _within_variance(src_min, tgt_min, variance_tolerance)
                max_match = _within_variance(src_max, tgt_max, variance_tolerance)
                avg_match = _within_variance(src_avg, tgt_avg, variance_tolerance)
            else:
                # Exact match (original logic)
                min_match = approx_equal(src_min, tgt_min)
                max_match = approx_equal(src_max, tgt_max)
                avg_match = approx_equal(src_avg, tgt_avg)
            
            matched = min_match and max_match and avg_match

            if not matched:
                overall_pass = False
                differences = []
                if not min_match:
                    variance_pct = (abs((src_min or 0) - (tgt_min or 0)) / max(abs(src_min or 0), abs(tgt_min or 0), 1)) * 100
                    differences.append(f"MIN: {src_min} vs {tgt_min} (variance: {variance_pct:.4f}%)")
                if not max_match:
                    variance_pct = (abs((src_max or 0) - (tgt_max or 0)) / max(abs(src_max or 0), abs(tgt_max or 0), 1)) * 100
                    differences.append(f"MAX: {src_max} vs {tgt_max} (variance: {variance_pct:.4f}%)")
                if not avg_match:
                    variance_pct = (abs((src_avg or 0) - (tgt_avg or 0)) / max(abs(src_avg or 0), abs(tgt_avg or 0), 1)) * 100
                    differences.append(f"AVG: {src_avg} vs {tgt_avg} (variance: {variance_pct:.4f}%)")
                
                failed_numeric_cols.append({
                    "column": col,
                    "source": (src_min, src_max, src_avg),
                    "target": (tgt_min, tgt_max, tgt_avg),
                    "differences": differences
                })

            # Build result label
            if matched and variance_tolerance is not None:
                result_label = f"✅ PASS (within {variance_tolerance*100:.2f}% variance)"
            elif matched:
                result_label = "✅ PASS"
            else:
                result_label = "❌ FAIL"

            numeric_rows.append({
                "Column": col,
                "Source Min": src_min,
                "Target Min": tgt_min,
                "Source Max": src_max,
                "Target Max": tgt_max,
                "Source Avg": round(src_avg, 4) if src_avg is not None else None,
                "Target Avg": round(tgt_avg, 4) if tgt_avg is not None else None,
                "Result": result_label
            })

        st.subheader("🔢 Numeric Column Statistics")
        if variance_tolerance is not None:
            st.caption(f"Using variance tolerance: {variance_tolerance*100:.2f}% (threshold: {threshold})")
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

        if variance_tolerance is not None:
            def _within_variance_str(a, b, tol):
                if a is None or b is None:
                    return a is None and b is None
                if a == 0 and b == 0:
                    return True
                denominator = max(abs(a), abs(b))
                if denominator == 0:
                    return True
                return abs(a - b) / denominator <= tol

            matched = (
                _within_variance_str(src_min_len, tgt_min_len, variance_tolerance) and
                _within_variance_str(src_max_len, tgt_max_len, variance_tolerance) and
                _within_variance_str(src_avg_len, tgt_avg_len, variance_tolerance)
            )
        else:
            # Use approx_equal for string length comparisons too
            matched = (
                approx_equal(src_min_len, tgt_min_len) and
                approx_equal(src_max_len, tgt_max_len) and
                approx_equal(src_avg_len, tgt_avg_len)
            )

        if not matched:
            overall_pass = False
            differences = []
            if variance_tolerance is not None:
                if not _within_variance_str(src_min_len, tgt_min_len, variance_tolerance):
                    differences.append(f"MIN length: {src_min_len} vs {tgt_min_len}")
                if not _within_variance_str(src_max_len, tgt_max_len, variance_tolerance):
                    differences.append(f"MAX length: {src_max_len} vs {tgt_max_len}")
                if not _within_variance_str(src_avg_len, tgt_avg_len, variance_tolerance):
                    differences.append(f"AVG length: {src_avg_len:.2f} vs {tgt_avg_len:.2f}")
            else:
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

        if matched and variance_tolerance is not None:
            str_result_label = f"✅ PASS (within {variance_tolerance*100:.2f}% variance)"
        elif matched:
            str_result_label = "✅ PASS"
        else:
            str_result_label = "❌ FAIL"

        string_rows.append({
            "Column": f"{col} (string)",
            "Source Min Len": src_min_len,
            "Target Min Len": tgt_min_len,
            "Source Max Len": src_max_len,
            "Target Max Len": tgt_max_len,
            "Source Avg Len": round(src_avg_len, 2) if src_avg_len else None,
            "Target Avg Len": round(tgt_avg_len, 2) if tgt_avg_len else None,
            "Result": str_result_label
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
        if variance_tolerance is not None:
            st.success(f"✅ All Numeric Statistics PASSED (within {variance_tolerance*100:.2f}% variance tolerance)")
        else:
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

        insert_id = insert_validation_result(
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
    icon_html = (
        f'<img src="data:image/png;base64,{icon_base64}" width="55"/>'
        if icon_base64
        else ""
    )

    st.markdown(
        f"""
        <div style="
            display:flex;
            align-items:center;
            justify-content:center;
            gap:12px;
            margin-top:10px;
        ">
            {icon_html}
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
sidebar_status_placeholder = None

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
    sidebar_status_placeholder = st.empty()
    if st.session_state.get("source_conn") and st.session_state.get("target_conn"):
        sidebar_status_placeholder.success("🟢 Status: Connected")
    else:
        sidebar_status_placeholder.error("🔴 Status: Not Connected")

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

    if "dashboard_date_filter" not in st.session_state:
        st.session_state["dashboard_date_filter"] = "All time"
    if "dashboard_custom_start" not in st.session_state:
        st.session_state["dashboard_custom_start"] = datetime.utcnow().date() - timedelta(days=29)
    if "dashboard_custom_end" not in st.session_state:
        st.session_state["dashboard_custom_end"] = datetime.utcnow().date()

    header_left, header_right = st.columns([0.75, 0.25])
    with header_right:
        # Prefer popover (filter icon UX). Fall back to expander if running on older Streamlit.
        popover = getattr(st, "popover", None)
        if callable(popover):
            with st.popover("🔎 Filter", use_container_width=True):
                st.session_state["dashboard_date_filter"] = st.selectbox(
                    "Date range",
                    DATE_FILTER_OPTIONS,
                    index=(
                        DATE_FILTER_OPTIONS.index(st.session_state["dashboard_date_filter"])
                        if st.session_state["dashboard_date_filter"] in DATE_FILTER_OPTIONS
                        else 0
                    ),
                )
                if st.session_state["dashboard_date_filter"] == "Custom":
                    st.session_state["dashboard_custom_start"] = st.date_input(
                        "Start date",
                        value=st.session_state["dashboard_custom_start"],
                        key="dashboard_custom_start_picker",
                    )
                    st.session_state["dashboard_custom_end"] = st.date_input(
                        "End date",
                        value=st.session_state["dashboard_custom_end"],
                        key="dashboard_custom_end_picker",
                    )
        else:
            with st.expander("🔎 Filter", expanded=False):
                st.session_state["dashboard_date_filter"] = st.selectbox(
                    "Date range",
                    DATE_FILTER_OPTIONS,
                    index=(
                        DATE_FILTER_OPTIONS.index(st.session_state["dashboard_date_filter"])
                        if st.session_state["dashboard_date_filter"] in DATE_FILTER_OPTIONS
                        else 0
                    ),
                )
                if st.session_state["dashboard_date_filter"] == "Custom":
                    st.session_state["dashboard_custom_start"] = st.date_input(
                        "Start date",
                        value=st.session_state["dashboard_custom_start"],
                        key="dashboard_custom_start_picker",
                    )
                    st.session_state["dashboard_custom_end"] = st.date_input(
                        "End date",
                        value=st.session_state["dashboard_custom_end"],
                        key="dashboard_custom_end_picker",
                    )

    start_date, end_date = _compute_date_range(
        st.session_state["dashboard_date_filter"],
        st.session_state.get("dashboard_custom_start"),
        st.session_state.get("dashboard_custom_end"),
    )
    where_clause = _build_validation_ts_where_clause(start_date, end_date)
    if start_date is None or end_date is None:
        st.caption("Showing all time validations")
    else:
        st.caption(f"Showing results from {start_date.isoformat()} to {end_date.isoformat()}")

    DASHBOARD_TABLE = "table_validation.validation_results"

    dashboard_query = f"""
        SELECT
            --COUNT(DISTINCT concat(catalog,'.',schema,'.',table)) AS tables_validated,
            COUNT(src_table_name) AS tables_validated,
            COUNT(DISTINCT validation_ts) AS total_runs,
            SUM(CASE WHEN row_count = 'PASS' THEN 1 ELSE 0 END) AS row_count_pass,
            SUM(CASE WHEN schema_check = 'PASS' THEN 1 ELSE 0 END) AS schema_pass,
            SUM(CASE WHEN numeric_check = 'PASS' THEN 1 ELSE 0 END) AS numeric_pass,
            SUM(CASE WHEN hash_validation = 'PASS' THEN 1 ELSE 0 END) AS row_hash_pass,
            SUM(CASE WHEN row_count = 'FAIL' THEN 1 ELSE 0 END) AS row_count_fail,
            SUM(CASE WHEN schema_check = 'FAIL' THEN 1 ELSE 0 END) AS schema_fail,
            SUM(CASE WHEN numeric_check = 'FAIL' THEN 1 ELSE 0 END) AS numeric_fail,
            SUM(CASE WHEN hash_validation = 'FAIL' THEN 1 ELSE 0 END) AS row_hash_fail
        FROM {DASHBOARD_TABLE}
        WHERE {where_clause}
    """
    dashboard_conn = get_dashboard_postgres_conn()

    result = execute_query(
        "postgres",
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
    c2.metric(
        "🧪 Total Validation Runs",
        result.get("total_runs", 0),
    )
    c3.metric("✅ Row Count Passed", result["row_count_pass"])

    c4.metric("📐 Schema Passed", result["schema_pass"])
    c5.metric("📈 Numeric Passed", result["numeric_pass"])
    c6.metric("🔐 Row Hash Passed", result["row_hash_pass"])

    st.divider()

    btn_label = "📊 View Stats" if not st.session_state["show_pie_stats"] else "❌ Hide Stats"

    if st.button(btn_label, use_container_width=True, key="toggle_pie_stats"):
        st.session_state["show_pie_stats"] = not st.session_state["show_pie_stats"]
        st.rerun()

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

    st.caption("All validation executions captured from PostgreSQl")

    # 🔹 Postgres connection (dashboard/results use only)
    results_conn = get_dashboard_postgres_conn()

    if "results_date_filter" not in st.session_state:
        st.session_state["results_date_filter"] = "Past 30 days"
    if "results_custom_start" not in st.session_state:
        st.session_state["results_custom_start"] = datetime.utcnow().date() - timedelta(days=29)
    if "results_custom_end" not in st.session_state:
        st.session_state["results_custom_end"] = datetime.utcnow().date()

    results_left, results_right = st.columns([0.75, 0.25])
    with results_right:
        popover = getattr(st, "popover", None)
        if callable(popover):
            with st.popover("🔎 Filter", use_container_width=True):
                st.session_state["results_date_filter"] = st.selectbox(
                    "Date range",
                    DATE_FILTER_OPTIONS,
                    index=(
                        DATE_FILTER_OPTIONS.index(st.session_state["results_date_filter"])
                        if st.session_state["results_date_filter"] in DATE_FILTER_OPTIONS
                        else DATE_FILTER_OPTIONS.index("Past 30 days")
                    ),
                    key="results_date_filter_select",
                )
                if st.session_state["results_date_filter"] == "Custom":
                    st.session_state["results_custom_start"] = st.date_input(
                        "Start date",
                        value=st.session_state["results_custom_start"],
                        key="results_custom_start_picker",
                    )
                    st.session_state["results_custom_end"] = st.date_input(
                        "End date",
                        value=st.session_state["results_custom_end"],
                        key="results_custom_end_picker",
                    )
        else:
            with st.expander("🔎 Filter", expanded=False):
                st.session_state["results_date_filter"] = st.selectbox(
                    "Date range",
                    DATE_FILTER_OPTIONS,
                    index=(
                        DATE_FILTER_OPTIONS.index(st.session_state["results_date_filter"])
                        if st.session_state["results_date_filter"] in DATE_FILTER_OPTIONS
                        else DATE_FILTER_OPTIONS.index("Past 30 days")
                    ),
                    key="results_date_filter_select",
                )
                if st.session_state["results_date_filter"] == "Custom":
                    st.session_state["results_custom_start"] = st.date_input(
                        "Start date",
                        value=st.session_state["results_custom_start"],
                        key="results_custom_start_picker",
                    )
                    st.session_state["results_custom_end"] = st.date_input(
                        "End date",
                        value=st.session_state["results_custom_end"],
                        key="results_custom_end_picker",
                    )

    r_start, r_end = _compute_date_range(
        st.session_state["results_date_filter"],
        st.session_state.get("results_custom_start"),
        st.session_state.get("results_custom_end"),
    )
    results_where = _build_validation_ts_where_clause(r_start, r_end)
    with results_left:
        if r_start is None or r_end is None:
            st.caption("Showing all time validations")
        else:
            st.caption(f"Showing results from {r_start.isoformat()} to {r_end.isoformat()}")
    
    RESULTS_QUERY = f"""
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
        FROM table_validation.validation_results
        WHERE {results_where}
        ORDER BY validation_ts DESC
    """

    results = execute_query("postgres", results_conn, RESULTS_QUERY)

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
    ["BigQuery", "Snowflake"],
    key="ui_source_engine",
)

# If the user changes the selected engine, invalidate any previously established connections.
if st.session_state.get("engine") and st.session_state.get("engine") != source_engine:
    st.session_state["source_conn"] = None
    st.session_state["target_conn"] = None
    st.session_state["engine"] = None
    if "backend_session_id" in st.session_state:
        del st.session_state["backend_session_id"]

# =========================================================
# STEP 2: CREDENTIALS (SIDE-BY-SIDE)
# =========================================================
st.subheader("🔐 Credentials Configuration")

left, right = st.columns(2)

# -----------------------------
# SOURCE CREDENTIALS
# -----------------------------
with left:
    if source_engine == "BigQuery":
        st.markdown("### 🧩 Source Engine")
        project_id = _trim_text(st.text_input("GCP Project ID"))
        dataset_location = _trim_text(st.text_input("Dataset Location", value="US"))
        bq_key_path = _trim_text(st.text_input("Service Account Key Path"))
        # persist BigQuery creds in session
        st.session_state["project_id"] = project_id
        st.session_state["dataset_location"] = dataset_location
        st.session_state["bq_key_path"] = bq_key_path
    elif source_engine == "Snowflake":
        credential_file_password = _trim_text(st.text_input("Credential File Unlock Password", type="password"))
        st.session_state["credential_file_password"] = credential_file_password

# -----------------------------
# DATABRICKS CREDENTIALS
# -----------------------------
with right:
    if source_engine == "BigQuery":
        st.markdown("### 🎯 Databricks")
        dbx_server = _trim_text(st.text_input("Databricks Server Hostname"))
        dbx_http_path = _trim_text(st.text_input("HTTP Path"))
        dbx_token = _trim_text(st.text_input("Access Token", type="password"))
        st.session_state["dbx_server"] = dbx_server
        st.session_state["dbx_http_path"] = dbx_http_path
        st.session_state["dbx_token"] = dbx_token

# =============================
# SESSION STATE INITIALIZATION
# =============================
# =============================
# SESSION STATE INITIALIZATION
# =============================
DEFAULT_SESSION_KEYS = {
    "total_validation_runs_counter": 0,
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
    "total_validation_runs_counter_initialized": False,
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
            if missing([dbx_server, dbx_http_path, dbx_token]):
                st.error("❌ Please fill all Databricks credentials")
                st.stop()
        else:
            if missing([credential_file_password]):
                st.error("❌ Please enter credential file unlock password")
                st.stop()

        # === CONNECTION LOGIC ===
        if source_engine == "BigQuery":
            source_conn = connect_bigquery(project_id, bq_key_path, dataset_location)
            target_conn = connect_databricks(dbx_server, dbx_http_path, dbx_token)
        else:
            locked = load_locked_credentials(credential_file_password)
            sf = locked["snowflake"]
            dbx = locked["databricks"]
            source_conn = connect_snowflake(
                sf.get("account"),
                sf.get("user"),
                sf.get("password"),
                sf.get("warehouse"),
                sf.get("role"),
            )
            target_conn = connect_databricks(
                dbx.get("server_hostname"),
                dbx.get("http_path"),
                dbx.get("access_token"),
            )

        st.session_state["source_conn"] = source_conn
        st.session_state["target_conn"] = target_conn
        st.session_state["engine"] = source_engine

        # Update sidebar status immediately (sidebar rendered earlier in the script).
        if sidebar_status_placeholder is not None:
            sidebar_status_placeholder.success("🟢 Status: Connected")

        st.success("✅ Connections established successfully")

        # Optional: create a backend session_id for async API calls (Databricks).
        # This does NOT reuse the live Python connection; it stores credentials
        # server-side with a TTL so the worker can reconnect when executing.
        try:
            api_base = _get_api_base_url()
            api_key = _get_api_key()
            if api_base and api_key:
                sess = create_backend_session_from_ui(
                    credential_password=credential_file_password if source_engine == "Snowflake" else "",
                    source_engine=source_engine,
                    project_id=project_id if source_engine == "BigQuery" else None,
                    dataset_location=dataset_location if source_engine == "BigQuery" else None,
                    bq_key_path=bq_key_path if source_engine == "BigQuery" else None,
                    dbx_server=dbx_server if source_engine == "BigQuery" else None,
                    dbx_http_path=dbx_http_path if source_engine == "BigQuery" else None,
                    dbx_token=dbx_token if source_engine == "BigQuery" else None,
                )
                if sess and sess.get("session_id"):
                    st.session_state["backend_session_id"] = sess.get("session_id")
                    st.success(
                        f"🔑 Backend session created (TTL). Session ID: {st.session_state['backend_session_id']}"
                    )
                    if sess.get("expires_at"):
                        st.caption(f"Session expires at: {sess.get('expires_at')}")
        except Exception as e:
            st.warning(f"Backend session creation failed (API optional): {e}")
        


    except Exception as e:
        st.error(f"❌ Connection failed: {friendly_error(e)}")
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
            "Perform column-level diff on hash mismatch",
            key="col_diff_enabled_config",
            value=st.session_state.get("col_diff_enabled_config", False),
            help=(
                "When hash validation fails, compare each non-key column value "
                "individually between source and target rows."
            ),
        )
        if st.session_state.get("col_diff_enabled_config"):
            # Fetch available columns on-demand so the multiselect is populated
            # immediately without needing to run hash validation first.
            _include_ts_config = st.session_state.get("include_timestamp_in_hash_config", True)
            _avail_key_config = f"col_diff_available_cols_config_{_include_ts_config}"
            if _avail_key_config not in st.session_state:
                _src_config = st.session_state.get('_col_diff_state', {}).get('src')
                _tgt_config = st.session_state.get('_col_diff_state', {}).get('tgt')
                if _src_config and _tgt_config and st.session_state.get("source_conn") and st.session_state.get("target_conn"):
                    st.session_state[_avail_key_config] = _fetch_col_diff_available_cols(
                        st.session_state.get('engine', 'snowflake'),
                        st.session_state["source_conn"],
                        st.session_state["target_conn"],
                        _src_config, _tgt_config,
                        _include_ts_config,
                    )
                else:
                    st.session_state[_avail_key_config] = []
            _opts_config = st.session_state.get(_avail_key_config, [])
            st.session_state["col_diff_available_cols_config"] = _opts_config
            st.session_state["col_diff_key_columns_config"] = st.multiselect(
                "Primary Key Column(s) for diff",
                options=_opts_config,
                default=[c for c in st.session_state.get("col_diff_key_columns_config", []) if c in _opts_config],
                key="col_diff_key_columns_widget_config",
                help="Columns that uniquely identify each row.",
            )
            if not _opts_config:
                st.caption("Connect to source and target first so columns can be loaded.")

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
            st.error(f"Invalid JSON: {friendly_error(e)}")
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
        # Count this config submission as a single validation run
        st.session_state["total_validation_runs_counter"] = st.session_state.get("total_validation_runs_counter", 0) + 1
        st.success("🎉 Config-driven validations completed")

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
            st.checkbox(
                "Perform column-level diff on hash mismatch",
                key="col_diff_enabled_manual",
                value=st.session_state.get("col_diff_enabled_manual", False),
                help=(
                    "When hash validation fails, compare each non-key column value "
                    "individually between source and target rows."
                ),
            )

        override_mode = st.checkbox(
            "⚙️ Override validations per table",
            value=False,
            key="manual_override"
        )

        # Threshold for deep mode
        manual_use_threshold = st.checkbox(
            "Use acceptable threshold for passing",
            value=False,
            key="manual_use_row_threshold_deep",
            help="If enabled, Row Count, Numeric Stats, and Row Hash use a match ratio threshold instead of exact match. Schema is always exact."
        )
        manual_row_threshold = None
        if manual_use_threshold:
            manual_row_threshold = st.number_input(
                "Acceptable Threshold (e.g., 0.99 = 99%)",
                min_value=0.0,
                max_value=1.0,
                value=0.99,
                step=0.01,
                format="%.4f",
                key="manual_row_threshold_value_deep",
                help="Row Count: match ratio. Numeric: variance tolerance. Hash: hash match ratio. Schema is NOT affected."
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

        # Threshold for row count in shallow mode
        manual_use_threshold = st.checkbox(
            "Use acceptable threshold for Row Count",
            value=False,
            key="manual_use_row_threshold",
            help="If enabled, row count validation passes if the match ratio meets the threshold (e.g., 0.99 = 99%)."
        )
        manual_row_threshold = None
        if manual_use_threshold:
            manual_row_threshold = st.number_input(
                "Row Count Match Threshold (e.g., 0.99 = 99%)",
                min_value=0.0,
                max_value=1.0,
                value=0.99,
                step=0.01,
                format="%.4f",
                key="manual_row_threshold_value",
                help="Enter a value between 0 and 1. For example, 0.99 means 99% of rows must match."
            )

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

                    table_has_any_selection = any([r, s, n, h])
                    hash_applicable = bool(h or ((not table_has_any_selection) and hash_check))
                    include_ts = None
                    if hash_applicable:
                        include_ts = st.checkbox(
                            "Include TIMESTAMP columns in row hash",
                            value=st.session_state.get(
                                "include_timestamp_in_hash_manual", True
                            ),
                            key=f"{table_id}_include_ts",
                            help=(
                                "If unchecked, columns with TIMESTAMP/DATETIME datatype are excluded "
                                "from the row hash calculation for this table pair."
                            ),
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
                        "include_timestamp": include_ts,
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

        # ── Column-diff key picker (shown when col_diff enabled and tables are selected) ──
        if st.session_state.get("col_diff_enabled_manual") and st.session_state.get("source_selections") and st.session_state.get("target_selections"):
            _include_ts_manual = st.session_state.get("include_timestamp_in_hash_manual", True)
            _avail_key_manual = f"col_diff_available_cols_manual_{_include_ts_manual}_{st.session_state.get('source_selections',[{}])[0].get('table','')}"
            if _avail_key_manual not in st.session_state:
                _src_manual = st.session_state["source_selections"][0]
                _tgt_manual = st.session_state["target_selections"][0]
                st.session_state[_avail_key_manual] = _fetch_col_diff_available_cols(
                    st.session_state.get("engine", "snowflake"),
                    st.session_state["source_conn"],
                    st.session_state["target_conn"],
                    _src_manual, _tgt_manual,
                    _include_ts_manual,
                )
            _opts_manual = st.session_state.get(_avail_key_manual, [])
            st.session_state["col_diff_key_columns_manual"] = st.multiselect(
                "Primary Key Column(s) for diff",
                options=_opts_manual,
                default=[c for c in st.session_state.get("col_diff_key_columns_manual", []) if c in _opts_manual],
                key="col_diff_key_columns_widget_manual",
                help="Columns that uniquely identify each row. Used to look up and compare mismatched rows.",
            )

        # =========================
        # RUN BUTTON
        # =========================
        if st.button("🚀 Run Manual Validations", use_container_width=True):
            # mark a run timestamp so all per-table inserts share the same validation_ts
            st.session_state["current_run_ts"] = datetime.utcnow().isoformat()
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
                                    s, t,
                                    threshold=manual_row_threshold if manual_use_threshold else None,
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
                                    s, t,
                                    threshold=manual_row_threshold if manual_use_threshold else None,
                                )))

                        if effective_hash:
                            effective_include_timestamp = config.get("include_timestamp")
                            if effective_include_timestamp is None:
                                effective_include_timestamp = st.session_state.get(
                                    "include_timestamp_in_hash_manual", True
                                )
                            checks.append(("Row Hash Validation", lambda s=src, t=tgt:
                                run_row_hash_validation(
                                    st.session_state["engine"],
                                    st.session_state["source_conn"],
                                    st.session_state["target_conn"],
                                    s,
                                    t,
                                    include_timestamp_columns=effective_include_timestamp,
                                    threshold=manual_row_threshold if manual_use_threshold else None,
                                )))

                        if checks:
                            results_map = run_checks_in_order(checks)
                            record = generate_validation_record(
                                "deep",
                                src, tgt,
                                bool_to_status(results_map.get("Row Count Validation")),
                                bool_to_status(results_map.get("Schema Validation")),
                                bool_to_status(results_map.get("Numeric Statistics Validation")),
                                bool_to_status(results_map.get("Row Hash Validation")),
                            )
                            insert_id = insert_validation_result(
                                st.session_state["target_conn"],
                                record
                            )
                            if insert_id:
                                st.info(f"Postgres insert committed: {insert_id}")
                            else:
                                st.error("Postgres insert failed — check logs or credentials")
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
                                     s, t,
                                     threshold=manual_row_threshold if manual_use_threshold else None,
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
                                        s, t,
                                        threshold=manual_row_threshold if manual_use_threshold else None,
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
                                        s, t,
                                        threshold=manual_row_threshold if manual_use_threshold else None,
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
                                        threshold=manual_row_threshold if manual_use_threshold else None,
                                    )))

                        if checks:
                            results_map = run_checks_in_order(checks)
                            record = generate_validation_record(
                                validation_type,
                                src, tgt,
                                bool_to_status(results_map.get("Row Count Validation")),
                                bool_to_status(results_map.get("Schema Validation")),
                                bool_to_status(results_map.get("Numeric Statistics Validation")),
                                bool_to_status(results_map.get("Row Hash Validation")),
                            )
                            insert_id = insert_validation_result(
                                st.session_state["target_conn"],
                                record
                            )
                            if insert_id:
                                st.info(f"Postgres insert committed: {insert_id}")
                            else:
                                st.error("Postgres insert failed — check logs or credentials")
                        else:
                            st.warning("⚠️ No validations selected.")

                # increment run counter by 1 for this execution
                st.session_state["total_validation_runs_counter"] = st.session_state.get("total_validation_runs_counter", 0) + 1
                st.success("🎉 Manual validations completed")

            except Exception as e:
                st.error(friendly_error(e))


with tab_csv:

    st.subheader("📄 CSV Template Format")

    # Define the canonical template columns
    template_columns = [
        "validation_type",
        "source_catalog",
        "source_schema",
        "source_table",
        "target_catalog",
        "target_schema",
        "target_table",
        "metrics",
        "case_sensitive",
        "include_timestamp",
        "row_threshold",
    ]

    # Build sample rows: one for deep and one for shallow (horizontal display)
    deep_sample = [
        "deep",               # validation_type
        "SNOWFLAKE_LEARNING_DB",  # source_catalog
        "PUBLIC",             # source_schema
        "DATATYPE_DEMO2",     # source_table
        "workspace",          # target_catalog
        "public",             # target_schema
        "datatype_demo",      # target_table
        "row_count,schema,numeric,hash",  # metrics
        "no",                 # case_sensitive
        "yes",                # include_timestamp
        "",                   # row_threshold (empty = exact match)
    ]

    shallow_sample = [
        "shallow",            # validation_type
        "SNOWFLAKE_LEARNING_DB",  # source_catalog
        "PUBLIC",             # source_schema
        "DATATYPE_DEMO2",           # source_table
        "workspace",          # target_catalog
        "public",             # target_schema
        "datatype_demo",           # target_table
        "",                   # metrics (empty for shallow)
        "no",                 # case_sensitive
        "yes",                # include_timestamp
        "0.99",               # row_threshold (99% match)
    ]

    # Build a two-row DataFrame with columns as headers and sample rows
    template_df = pd.DataFrame([deep_sample, shallow_sample], columns=template_columns)

    # Display the template horizontally (column headers across, two sample rows)
    st.dataframe(template_df, use_container_width=True)

    # Provide a downloadable CSV that contains only the header (column names)
    header_only_csv = ",".join(template_columns) + "\n"

    st.download_button(
        label="⬇ Download CSV Template (header-only)",
        data=header_only_csv,
        file_name="reconciliation_template_header_only.csv",
        mime="text/csv",
    )

    st.subheader("📂 Upload CSV for Multiple Tables")

    st.divider()

    uploaded_file = st.file_uploader(
        "Upload reconciliation CSV",
        type=["csv"]
    )

    if uploaded_file:
        # 🔥 IMPORTANT: Ignore _commented _lines
        df = pd.read_csv(uploaded_file, comment="#")

        st.subheader("🔍 Preview Uploaded CSV")
        st.dataframe(df, use_container_width=True)

        if st.button("🚀 Run CSV Validations", use_container_width=True):
            try:
                # mark a run timestamp so all per-table inserts share the same validation_ts
                st.session_state["current_run_ts"] = datetime.utcnow().isoformat()
                validate_csv(df)
                run_csv_validations(df)
                # increment run counter by 1 for this execution
                st.session_state["total_validation_runs_counter"] = st.session_state.get("total_validation_runs_counter", 0) + 1
                st.success("🎉 All CSV validations completed")
            except Exception as e:
                st.error(f"❌ {friendly_error(e)}")




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
            st.checkbox(
                "Perform column-level diff on hash mismatch",
                key="col_diff_enabled_browse",
                value=st.session_state.get("col_diff_enabled_browse", False),
                help=(
                    "When hash validation fails, compare each non-key column value "
                    "individually between source and target rows."
                ),
            )

        override_mode = st.checkbox(
            "⚙️ Override validations per table",
            value=False,
            key="browse_override"
        )

        # Threshold for deep mode
        browse_use_threshold = st.checkbox(
            "Use acceptable threshold for passing",
            value=False,
            key="browse_use_row_threshold_deep",
            help="If enabled, Row Count, Numeric Stats, and Row Hash use a match ratio threshold instead of exact match. Schema is always exact."
        )
        browse_row_threshold = None
        if browse_use_threshold:
            browse_row_threshold = st.number_input(
                "Acceptable Threshold (e.g., 0.99 = 99%)",
                min_value=0.0,
                max_value=1.0,
                value=0.99,
                step=0.01,
                format="%.4f",
                key="browse_row_threshold_value_deep",
                help="Row Count: match ratio. Numeric: variance tolerance. Hash: hash match ratio. Schema is NOT affected."
            )

    else:
        override_mode = False
        st.info("Shallow mode will run Row Count and Schema validation automatically.")

        browse_row_check = True
        browse_schema_check = True
        browse_numeric_check = False
        browse_hash_check = False

        # Threshold for row count in shallow mode
        browse_use_threshold = st.checkbox(
            "Use acceptable threshold for Row Count",
            value=False,
            key="browse_use_row_threshold",
            help="If enabled, row count validation passes if the match ratio meets the threshold (e.g., 0.99 = 99%)."
        )
        browse_row_threshold = None
        if browse_use_threshold:
            browse_row_threshold = st.number_input(
                "Row Count Match Threshold (e.g., 0.99 = 99%)",
                min_value=0.0,
                max_value=1.0,
                value=0.99,
                step=0.01,
                format="%.4f",
                key="browse_row_threshold_value",
                help="Enter a value between 0 and 1. For example, 0.99 means 99% of rows must match."
            )

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

                table_has_any_selection = any([r, s, n, h])
                hash_applicable = bool(h or ((not table_has_any_selection) and browse_hash_check))
                include_ts = None
                if hash_applicable:
                    include_ts = st.checkbox(
                        "Include TIMESTAMP columns in row hash",
                        value=st.session_state.get(
                            "include_timestamp_in_hash_browse", True
                        ),
                        key=f"{table_id}_include_ts",
                        help=(
                            "If unchecked, columns with TIMESTAMP/DATETIME datatype are excluded "
                            "from the row hash calculation for this table pair."
                        ),
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
                    "include_timestamp": include_ts,
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

    # ── Column-diff key picker (shown when col_diff enabled and tables are selected) ──
    if st.session_state.get("col_diff_enabled_browse") and st.session_state.get("source_selections") and st.session_state.get("target_selections"):
        _include_ts_browse = st.session_state.get("include_timestamp_in_hash_browse", True)
        _avail_key_browse = f"col_diff_available_cols_browse_{_include_ts_browse}_{st.session_state.get('source_selections',[{}])[0].get('table','')}"
        if _avail_key_browse not in st.session_state:
            _src_browse = st.session_state["source_selections"][0]
            _tgt_browse = st.session_state["target_selections"][0]
            st.session_state[_avail_key_browse] = _fetch_col_diff_available_cols(
                st.session_state.get("engine", "snowflake"),
                st.session_state["source_conn"],
                st.session_state["target_conn"],
                _src_browse, _tgt_browse,
                _include_ts_browse,
            )
        _opts_browse = st.session_state.get(_avail_key_browse, [])
        st.session_state["col_diff_key_columns_browse"] = st.multiselect(
            "Primary Key Column(s) for diff",
            options=_opts_browse,
            default=[c for c in st.session_state.get("col_diff_key_columns_browse", []) if c in _opts_browse],
            key="col_diff_key_columns_widget_browse",
            help="Columns that uniquely identify each row. Used to look up and compare mismatched rows.",
        )

    # =================================================
    # RUN BUTTON
    # =================================================
    if st.button("🚀 Run Browse Validations", use_container_width=True):
        # mark a run timestamp so all per-table inserts share the same validation_ts
        st.session_state["current_run_ts"] = datetime.utcnow().isoformat()
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
                                s, t,
                                threshold=browse_row_threshold if browse_use_threshold else None,
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
                                s, t,
                                threshold=browse_row_threshold if browse_use_threshold else None,
                            )))

                    if effective_hash:
                        effective_include_timestamp = config.get("include_timestamp")
                        if effective_include_timestamp is None:
                            effective_include_timestamp = st.session_state.get(
                                "include_timestamp_in_hash_browse", True
                            )
                        checks.append(("Row Hash Validation",
                            lambda s=src, t=tgt: run_row_hash_validation(
                                st.session_state["engine"],
                                st.session_state["source_conn"],
                                st.session_state["target_conn"],
                                s,
                                t,
                                include_timestamp_columns=effective_include_timestamp,
                                threshold=browse_row_threshold if browse_use_threshold else None,
                            )))

                    if checks:
                        results_map = run_checks_in_order(checks)
                        record = generate_validation_record(
                            "deep",
                            src, tgt,
                            bool_to_status(results_map.get("Row Count Validation")),
                            bool_to_status(results_map.get("Schema Validation")),
                            bool_to_status(results_map.get("Numeric Statistics Validation")),
                            bool_to_status(results_map.get("Row Hash Validation")),
                        )
                        insert_id = insert_validation_result(
                            st.session_state["target_conn"],
                            record
                        )
                        if insert_id:
                            st.info(f"Postgres insert committed: {insert_id}")
                        else:
                            st.error("Postgres insert failed — check logs or credentials")
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
                                 s, t,
                                 threshold=browse_row_threshold if browse_use_threshold else None,
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
                                    s, t,
                                    threshold=browse_row_threshold if browse_use_threshold else None,
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
                                    s, t,
                                    threshold=browse_row_threshold if browse_use_threshold else None,
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
                                    threshold=browse_row_threshold if browse_use_threshold else None,
                                )))

                    if checks:
                        results_map = run_checks_in_order(checks)
                        record = generate_validation_record(
                            validation_type,
                            src, tgt,
                            bool_to_status(results_map.get("Row Count Validation")),
                            bool_to_status(results_map.get("Schema Validation")),
                            bool_to_status(results_map.get("Numeric Statistics Validation")),
                            bool_to_status(results_map.get("Row Hash Validation")),
                        )
                        insert_id = insert_validation_result(
                            st.session_state["target_conn"],
                            record
                        )
                        if insert_id:
                            st.info(f"Postgres insert committed: {insert_id}")
                        else:
                            st.error("Postgres insert failed — check logs or credentials")
                    else:
                        st.warning("⚠️ No validations selected.")

            # increment run counter by 1 for this execution
            st.session_state["total_validation_runs_counter"] = st.session_state.get("total_validation_runs_counter", 0) + 1
            st.success("🎉 Browse validations completed")

        except Exception as e:
            st.error(friendly_error(e))