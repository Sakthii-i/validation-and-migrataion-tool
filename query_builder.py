def qualify_table(engine, catalog, schema, table):
    engine = engine.lower()

    if engine == "bigquery":
        return f"`{catalog}.{schema}.{table}`"

    if engine in ["databricks", "snowflake"]:
        return f"{catalog}.{schema}.{table}"

    raise ValueError(f"Unsupported engine: {engine}")


def build_shallow_query(engine, catalog, schema, table, metrics):
    """
    Build ONE query that calculates all selected shallow metrics
    """
    select_exprs = []

    if metrics.get("row_count"):
        select_exprs.append("COUNT(*) AS row_count")

    if not select_exprs:
        return None

    table_fqn = qualify_table(engine, catalog, schema, table)

    return f"""
    SELECT
        {', '.join(select_exprs)}
    FROM {table_fqn}
    """.strip()


def build_schema_query(engine, catalog, schema, table):
    engine = engine.lower()

    if engine == "bigquery":
        return f"""
        SELECT
            column_name,
            data_type,
            is_nullable
        FROM `{catalog}.{schema}.INFORMATION_SCHEMA.COLUMNS`
        WHERE table_name = '{table}'
        ORDER BY ordinal_position
        """

    if engine == "databricks":
        return f"""
        SELECT
            column_name,
            data_type,
            is_nullable
        FROM system.information_schema.columns
        WHERE table_catalog = '{catalog}'
          AND table_schema = '{schema}'
          AND table_name = '{table}'
        ORDER BY ordinal_position
        """

    if engine == "snowflake":
        return f"""
        SELECT
            lower(column_name) as column_name,
            data_type,
            is_nullable
        FROM {catalog}.information_schema.columns
        WHERE table_schema = '{schema}'
          AND table_name = '{table}'
        ORDER BY ordinal_position
        """

    raise ValueError(f"Unsupported engine: {engine}")

 

# =============================
# NUMERIC COLUMN IDENTIFICATION
# =============================
def get_numeric_columns(schema_rows):
    numeric_keywords = (
        "INT", "INTEGER", "BIGINT", "SMALLINT",
        "FLOAT", "DOUBLE", "REAL",
        "NUMERIC", "DECIMAL", "NUMBER",
        "INT64", "FLOAT64", "BIGNUMERIC"
    )

    numeric_cols = []

    for row in schema_rows:
        col = row["column_name"]
        dtype = row["data_type"]

        if dtype and any(k in dtype.upper() for k in numeric_keywords):
            numeric_cols.append(col)

    return numeric_cols


# =============================
# NUMERIC STATS QUERY BUILDER
# =============================
def build_numeric_stats_query(engine, catalog, schema, table, column):
    table_fqn = qualify_table(engine, catalog, schema, table)

    return f"""
    SELECT
        MIN({column}) AS min_val,
        MAX({column}) AS max_val,
        AVG({column}) AS avg_val
    FROM {table_fqn}
    """
    

def _col_name(col):
    if isinstance(col, dict):
        return col.get("name")
    return col


def _col_type(col):
    if isinstance(col, dict):
        return (col.get("type") or "").upper()
    return ""


def _quote_col(engine: str, col_name: str) -> str:
    engine = engine.lower()
    if engine in ["bigquery", "databricks"]:
        return f"`{col_name}`"
    # Snowflake is case-sensitive when quoted; the app's schema query
    # returns lower(column_name), so keep unquoted identifiers here.
    return col_name


def _normalize_numeric_expr(engine: str, expr: str) -> str:
    engine = engine.lower()
    if engine == "bigquery":
        return (
            "REGEXP_REPLACE(" 
            "REGEXP_REPLACE(" 
            f"{expr}, r'(\\.[0-9]*?)0+$', r'\\1')," 
            "r'\\.$', ''"
            ")"
        )

    if engine == "databricks":
        return (
            "regexp_replace(" 
            "regexp_replace(" 
            f"{expr}, '(\\.\\d*?)0+$', '$1')," 
            "'\\.$', ''"
            ")"
        )

    if engine == "snowflake":
        return (
            "REGEXP_REPLACE(" 
            "REGEXP_REPLACE(" 
            f"{expr}, '(\\\\.[0-9]*?)0+$', '\\\\1')," 
            "'\\\\.$', ''"
            ")"
        )

    return expr


def _value_expr(engine: str, col: dict | str) -> str:
    """Return a string expression for a single column, normalized per type."""
    engine = engine.lower()
    col_name = _col_name(col)
    col_type = _col_type(col)
    col_ref = _quote_col(engine, col_name)

    null_token = "'<NULL>'"

    # Booleans
    if col_type == "BOOLEAN":
        if engine == "bigquery":
            return (
                f"(CASE WHEN {col_ref} IS NULL THEN {null_token} "
                f"WHEN {col_ref} THEN 'true' ELSE 'false' END)"
            )
        if engine == "databricks":
            return (
                f"(CASE WHEN {col_ref} IS NULL THEN {null_token} "
                f"WHEN {col_ref} THEN 'true' ELSE 'false' END)"
            )
        if engine == "snowflake":
            return (
                f"(IFF({col_ref} IS NULL, {null_token}, IFF({col_ref}, 'true', 'false')))"
            )

    # Dates
    if col_type == "DATE":
        if engine == "bigquery":
            return f"COALESCE(FORMAT_DATE('%F', {col_ref}), {null_token})"
        if engine == "databricks":
            return f"COALESCE(date_format({col_ref}, 'yyyy-MM-dd'), {null_token})"
        if engine == "snowflake":
            return f"COALESCE(TO_VARCHAR({col_ref}, 'YYYY-MM-DD'), {null_token})"

    # Timestamps
    if col_type == "TIMESTAMP":
        if engine == "bigquery":
            # Cast handles DATETIME->TIMESTAMP as well.
            return (
                "COALESCE(" 
                f"FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%E3SZ', CAST({col_ref} AS TIMESTAMP), 'UTC')," 
                f"{null_token})"
            )
        if engine == "databricks":
            return (
                "COALESCE(" 
                f"date_format(to_utc_timestamp({col_ref}, 'UTC'), \"yyyy-MM-dd'T'HH:mm:ss.SSS'Z'\")," 
                f"{null_token})"
            )
        if engine == "snowflake":
            return (
                "COALESCE(" 
                f"TO_VARCHAR(CONVERT_TIMEZONE('UTC', {col_ref}::TIMESTAMP_TZ), 'YYYY-MM-DD\"T\"HH24:MI:SS.FF3\"Z\"')," 
                f"{null_token})"
            )

    # Numeric types
    if col_type in {"INT", "DECIMAL", "DOUBLE"}:
        if engine == "bigquery":
            numeric_str = f"CAST({col_ref} AS STRING)"
        elif engine == "databricks":
            numeric_str = f"CAST({col_ref} AS STRING)"
        else:  # snowflake
            numeric_str = f"TO_VARCHAR({col_ref})"
        return f"COALESCE({_normalize_numeric_expr(engine, numeric_str)}, {null_token})"

    # Complex types: prefer JSON when available
    if col_type in {"STRUCT", "ARRAY"}:
        if engine == "bigquery":
            return f"COALESCE(TO_JSON_STRING({col_ref}), {null_token})"
        if engine == "databricks":
            return f"COALESCE(to_json({col_ref}), {null_token})"
        if engine == "snowflake":
            return f"COALESCE(TO_JSON({col_ref}), {null_token})"

    # Default: string cast
    if engine == "bigquery":
        return f"COALESCE(CAST({col_ref} AS STRING), {null_token})"
    if engine == "databricks":
        return f"COALESCE(CAST({col_ref} AS STRING), {null_token})"
    if engine == "snowflake":
        return f"COALESCE(TO_VARCHAR({col_ref}), {null_token})"

    return f"COALESCE(CAST({col_ref} AS STRING), {null_token})"


def _row_signature_expr(engine: str, columns) -> str:
    engine = engine.lower()
    parts = [_value_expr(engine, c) for c in (columns or [])]
    if not parts:
        return "'<EMPTY>'"

    if engine == "bigquery":
        return "CONCAT(" + ", '||', ".join(parts) + ")"
    if engine == "databricks":
        return "concat_ws('||', " + ", ".join(parts) + ")"
    if engine == "snowflake":
        expr = parts[0]
        for p in parts[1:]:
            expr = f"({expr} || '||' || {p})"
        return expr

    return "CONCAT(" + ", '||', ".join(parts) + ")"


def build_row_hash_query(engine, catalog, schema, table, columns=None):
    engine = engine.lower()
    table_fqn = qualify_table(engine, catalog, schema, table)

    signature_expr = _row_signature_expr(engine, columns)

    if engine == "databricks":
        return f"""
        WITH source_data AS (
            SELECT {signature_expr} AS row_signature
            FROM {table_fqn}
        )
        SELECT lower(md5(row_signature)) AS hash_value
        FROM source_data
        ORDER BY hash_value
        """.strip()

    if engine == "bigquery":
        return f"""
        WITH source_data AS (
            SELECT {signature_expr} AS row_signature
            FROM {table_fqn}
        )
        SELECT lower(TO_HEX(MD5(row_signature))) AS hash_value
        FROM source_data
        ORDER BY hash_value
        """.strip()

    if engine == "snowflake":
        return f"""
        WITH source_data AS (
            SELECT {signature_expr} AS row_signature
            FROM {table_fqn}
        )
        SELECT lower(MD5_HEX(row_signature)) AS hash_value
        FROM source_data
        ORDER BY hash_value
        """.strip()

    raise ValueError(f"Row hash not supported for engine: {engine}")

