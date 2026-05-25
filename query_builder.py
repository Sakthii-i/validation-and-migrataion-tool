def qualify_table(engine, catalog, schema, table):
    engine = engine.lower()

    if engine == "bigquery":
        return f"`{catalog}.{schema}.{table}`"

    if engine in ["databricks", "snowflake"]:
        return f"{catalog}.{schema}.{table}"

    raise ValueError(f"Unsupported engine: {engine}")


def build_shallow_query(engine, catalog, schema, table, metrics, where_clause="1=1"):
    """
    Build ONE query that calculates all selected shallow metrics
    """
    select_exprs = []

    if metrics.get("row_count"):
        select_exprs.append("COUNT(*) AS row_count")

    if not select_exprs:
        return None

    table_fqn = qualify_table(engine, catalog, schema, table)

    where_sql = (str(where_clause).strip() or "1=1")

    return f"""
    SELECT
        {', '.join(select_exprs)}
    FROM {table_fqn}
    WHERE {where_sql}
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
            CASE
                WHEN lower(data_type) IN ('decimal', 'dec', 'numeric')
                    THEN data_type || '(' || numeric_precision || ',' || numeric_scale || ')'
                ELSE data_type
            END AS data_type,
            is_nullable
        FROM system.information_schema.columns
                WHERE lower(table_catalog) = lower('{catalog}')
                    AND lower(table_schema) = lower('{schema}')
                    AND lower(table_name) = lower('{table}')
        ORDER BY ordinal_position
        """

    if engine == "snowflake":
        return f"""
        SELECT
            column_name,
            CASE
                WHEN data_type IN ('NUMBER', 'DECIMAL', 'NUMERIC')
                    THEN data_type || '(' || numeric_precision || ',' || numeric_scale || ')'
                ELSE data_type
            END AS data_type,
            is_nullable
        FROM {catalog}.information_schema.columns
                WHERE lower(table_schema) = lower('{schema}')
                    AND lower(table_name) = lower('{table}')
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
        "INT64", "FLOAT64", "BIGNUMERIC", "LONG",
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
def build_numeric_stats_query(
    engine,
    catalog,
    schema,
    table,
    column,
    where_clause="1=1",
):
    table_fqn = qualify_table(engine, catalog, schema, table)
    where_sql = (str(where_clause).strip() or "1=1")

    return f"""
    SELECT
        MIN({column}) AS min_val,
        MAX({column}) AS max_val,
        AVG({column}) AS avg_val
    FROM {table_fqn}
    WHERE {where_sql}
    """
    

def _col_name(col):
    if isinstance(col, dict):
        return col.get("name")
    return col


def _col_type(col):
    if isinstance(col, dict):
        return (col.get("type") or "").upper()
    return ""


def _col_raw_type(col) -> str:
    if isinstance(col, dict):
        raw = col.get("raw_type")
        return str(raw).lower().strip() if raw is not None else ""
    return ""


def _quote_col(engine: str, col_name: str) -> str:
    engine = engine.lower()
    if engine in ["bigquery", "databricks"]:
        return f"`{col_name}`"
    # Snowflake identifiers are case-sensitive when quoted.
    # We preserve source case from INFORMATION_SCHEMA, so unquoted refs
    # keep compatibility with standard uppercase object names.
    return col_name


def _normalize_numeric_expr(engine: str, expr: str) -> str:
    engine = engine.lower()
    if engine == "bigquery":
        stripped = (
            "REGEXP_REPLACE(" 
            "REGEXP_REPLACE(" 
            f"{expr}, r'(\\.[0-9]*?)0+$', r'\\1')," 
            "r'\\.$', ''"
            ")"
        )
        return (
            "REGEXP_REPLACE(" 
            f"{stripped}, r'^-0(\\.0+)?$', '0'"
            ")"
        )

    if engine == "databricks":
        stripped = (
            "regexp_replace(" 
            "regexp_replace(" 
            f"{expr}, '(\\.\\d*?)0+$', '$1')," 
            "'\\.$', ''"
            ")"
        )
        return fr"regexp_replace({stripped}, '^-0(\.0+)?$', '0')"

    if engine == "snowflake":
        stripped = (
            "REGEXP_REPLACE(" 
            "REGEXP_REPLACE(" 
            f"{expr}, '(\\\\.[0-9]*?)0+$', '\\\\1')," 
            "'\\\\.$', ''"
            ")"
        )
        return f"REGEXP_REPLACE({stripped}, '^-0(\\\\.0+)?$', '0')"

    return expr


def _is_float_type(col_type: str) -> bool:
    t = (col_type or "").upper()
    return any(k in t for k in ("FLOAT", "DOUBLE", "REAL"))


def _is_numeric_type(col_type: str) -> bool:
    t = (col_type or "").upper()
    return any(
        k in t
        for k in (
            "INT",
            "INTEGER",
            "BIGINT",
            "SMALLINT",
            "TINYINT",
            "BYTEINT",
            "DECIMAL",
            "NUMERIC",
            "BIGNUMERIC",
            "NUMBER",
            "FLOAT",
            "DOUBLE",
            "REAL",
            "LONG",
        )
    )


def _value_expr(engine: str, col: dict | str) -> str:
    """Return a string expression for a single column, normalized per type."""
    engine = engine.lower()
    col_name = _col_name(col)
    col_type = _col_type(col)
    col_raw_type = _col_raw_type(col)
    col_ref = _quote_col(engine, col_name)

    null_token = "'<NULL>'"

    # Binary
    if col_type == "BINARY":
        if engine == "bigquery":
            return f"COALESCE(TRIM(TO_HEX({col_ref})), {null_token})"
        if engine == "databricks":
            return f"COALESCE(TRIM(upper(hex({col_ref}))), {null_token})"
        if engine == "snowflake":
            return f"COALESCE(TRIM(upper(TO_VARCHAR({col_ref}, 'HEX'))), {null_token})"

    # Booleans
    if col_type == "BOOLEAN":
        if engine == "bigquery":
            return (
                f"TRIM((CASE WHEN {col_ref} IS NULL THEN {null_token} "
                f"WHEN {col_ref} THEN 'true' ELSE 'false' END))"
            )
        if engine == "databricks":
            return (
                f"TRIM((CASE WHEN {col_ref} IS NULL THEN {null_token} "
                f"WHEN {col_ref} THEN 'true' ELSE 'false' END))"
            )
        if engine == "snowflake":
            return (
                f"TRIM((IFF({col_ref} IS NULL, {null_token}, IFF({col_ref}, 'true', 'false'))))"
            )

    # Dates
    if col_type == "DATE":
        if engine == "bigquery":
            return f"COALESCE(TRIM(FORMAT_DATE('%F', {col_ref})), {null_token})"
        if engine == "databricks":
            return f"COALESCE(TRIM(date_format({col_ref}, 'yyyy-MM-dd')), {null_token})"
        if engine == "snowflake":
            return f"COALESCE(TRIM(TO_VARCHAR({col_ref}, 'YYYY-MM-DD')), {null_token})"

    # Timestamps
    if col_type == "TIMESTAMP":
        if engine == "bigquery":
            # BigQuery DATETIME has no timezone; TIMESTAMP is UTC.
            if col_raw_type == "datetime":
                return (
                    "COALESCE(" 
                    f"TRIM(FORMAT_DATETIME('%Y-%m-%d %H:%M:%E3S', {col_ref}))," 
                    f"{null_token})"
                )

            # Default to TIMESTAMP formatting in UTC.
            return (
                "COALESCE(" 
                f"TRIM(FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%E3S', CAST({col_ref} AS TIMESTAMP), 'UTC'))," 
                f"{null_token})"
            )
        if engine == "databricks":
            return (
                "COALESCE(" 
                # Normalize to a UTC string regardless of Spark session timezone.
                f"TRIM(date_format(to_utc_timestamp({col_ref}, current_timezone()), 'yyyy-MM-dd HH:mm:ss.SSS'))," 
                f"{null_token})"
            )
        if engine == "snowflake":
            # Avoid shifting TIMESTAMP_NTZ. For LTZ/TZ, convert to UTC.
            if "timestamp_ntz" in col_raw_type:
                return (
                    "COALESCE(" 
                    f"TRIM(TO_VARCHAR({col_ref}::TIMESTAMP_NTZ, 'YYYY-MM-DD HH24:MI:SS.FF3'))," 
                    f"{null_token})"
                )

            if "timestamp_ltz" in col_raw_type:
                return (
                    "COALESCE(" 
                    f"TRIM(TO_VARCHAR(CONVERT_TIMEZONE('UTC', {col_ref}::TIMESTAMP_LTZ), 'YYYY-MM-DD HH24:MI:SS.FF3'))," 
                    f"{null_token})"
                )

            if "timestamp_tz" in col_raw_type:
                return (
                    "COALESCE(" 
                    f"TRIM(TO_VARCHAR(CONVERT_TIMEZONE('UTC', {col_ref}::TIMESTAMP_TZ), 'YYYY-MM-DD HH24:MI:SS.FF3'))," 
                    f"{null_token})"
                )

            return (
                "COALESCE(" 
                f"TRIM(TO_VARCHAR(CONVERT_TIMEZONE('UTC', {col_ref}::TIMESTAMP_TZ), 'YYYY-MM-DD HH24:MI:SS.FF3'))," 
                f"{null_token})"
            )

    # Numeric types
    if _is_numeric_type(col_type):
        if engine == "bigquery":
            if _is_float_type(col_type):
                numeric_str = f"FORMAT('%.15f', CAST({col_ref} AS FLOAT64))"
            else:
                numeric_str = f"CAST({col_ref} AS STRING)"
        elif engine == "databricks":
            if _is_float_type(col_type):
                numeric_str = f"CAST(CAST({col_ref} AS DECIMAL(38,15)) AS STRING)"
            else:
                numeric_str = f"CAST({col_ref} AS STRING)"
        else:  # snowflake
            if _is_float_type(col_type):
                numeric_str = f"TO_VARCHAR(TO_DECIMAL({col_ref}, 38, 15))"
            else:
                numeric_str = f"TO_VARCHAR({col_ref})"

        return f"COALESCE(TRIM({_normalize_numeric_expr(engine, numeric_str)}), {null_token})"

    # Complex types: prefer JSON when available
    if col_type in {"STRUCT", "ARRAY"}:
        if engine == "bigquery":
            return f"COALESCE(TRIM(TO_JSON_STRING({col_ref})), {null_token})"
        if engine == "databricks":
            # If underlying type is MAP, sort keys for deterministic JSON.
            if col_raw_type.startswith("map<"):
                return f"COALESCE(TRIM(to_json(map_sort({col_ref}))), {null_token})"
            return f"COALESCE(TRIM(to_json({col_ref})), {null_token})"
        if engine == "snowflake":
            return f"COALESCE(TRIM(TO_JSON({col_ref})), {null_token})"

    # Default: string cast
    if engine == "bigquery":
        return f"COALESCE(TRIM(CAST({col_ref} AS STRING)), {null_token})"
    if engine == "databricks":
        return f"COALESCE(TRIM(CAST({col_ref} AS STRING)), {null_token})"
    if engine == "snowflake":
        return f"COALESCE(TRIM(TO_VARCHAR({col_ref})), {null_token})"

    return f"COALESCE(TRIM(CAST({col_ref} AS STRING)), {null_token})"


def _row_signature_expr(engine: str, columns) -> str:
    engine = engine.lower()
    parts = [_value_expr(engine, c) for c in (columns or [])]
    if not parts:
        return "'<EMPTY>'"

    delim = "'|'"

    def concat_with_delim(concat_func: str) -> str:
        # Build CONCAT(p1, '|', p2, '|', p3, ...)
        expr_parts = [parts[0]]
        for p in parts[1:]:
            expr_parts.append(delim)
            expr_parts.append(p)
        return f"{concat_func}(" + ", ".join(expr_parts) + ")"

    if engine == "bigquery":
        return concat_with_delim("CONCAT")
    if engine == "databricks":
        return concat_with_delim("concat")
    if engine == "snowflake":
        expr = parts[0]
        for p in parts[1:]:
            expr = f"({expr} || '|' || {p})"
        return expr

    return "CONCAT(" + ", '||', ".join(parts) + ")"


def build_row_hash_query(
    engine,
    catalog,
    schema,
    table,
    columns=None,
    where_clause="1=1",
):
    return build_row_hash_query_v2(
        engine,
        catalog,
        schema,
        table,
        schema_rows=_columns_to_schema_rows(columns),
        include_timestamp=True,
        timestamp_mode=None,
        where_clause=where_clause,
    )


def _columns_to_schema_rows(columns):
    rows = []
    for c in (columns or []):
        if isinstance(c, dict):
            col_name = c.get("name")
            dtype = c.get("raw_type") or c.get("type")
        else:
            col_name = c
            dtype = None

        if not col_name:
            continue

        rows.append({"column_name": col_name, "data_type": dtype or ""})
    return rows


def _quote_col_v2(engine: str, col_name: str) -> str:
    engine = engine.lower()
    if engine in {"bigquery", "databricks"}:
        return f"`{col_name}`"
    # follow user-requested approach: Snowflake uses uppercased, unquoted identifiers
    return str(col_name).upper()


def _numeric_expr_v2(engine: str, col_ref: str) -> str:
    engine = engine.lower()
    if engine == "snowflake":
        # Only strip trailing zeros when a decimal point exists.
        # This avoids corrupting integers like 10 -> 1.
        return (
            "COALESCE("
            "IFF("
            "POSITION('.' IN TRIM(" + col_ref + "::STRING)) > 0,"
            "RTRIM(RTRIM(TRIM(" + col_ref + "::STRING), '0'), '.'),"
            "TRIM(" + col_ref + "::STRING)"
            "),"
            "''"
            ")"
        )
    if engine == "bigquery":
        return (
            "COALESCE("
            "REGEXP_REPLACE("
            "REGEXP_REPLACE(TRIM(CAST(" + col_ref + " AS STRING)), r'(\\\\.[0-9]*?)0+$', r'\\\\1'),"
            "r'\\\\.$', ''"
            "),"
            "''"
            ")"
        )
    # databricks
    return (
        "COALESCE("
        "regexp_replace("
        "regexp_replace("
        "TRIM(CAST(CAST(" + col_ref + " AS DECIMAL(38,8)) AS STRING)),"
        "'(\\\\.\\\\d*?)0+$', '$1'"
        "),"
        "'\\\\.$', ''"
        "),"
        "''"
        ")"
    )


def _is_string_type(dtype_upper: str) -> bool:
    return any(x in (dtype_upper or "") for x in ("STRING", "VARCHAR", "CHAR", "TEXT"))


def _is_date_like_string(col_name: str, dtype_upper: str) -> bool:
    if not _is_string_type(dtype_upper):
        return False
    return "date" in str(col_name or "").lower()


def _string_date_expr(engine: str, col_ref: str) -> str:
    engine = engine.lower()
    if engine == "snowflake":
        return (
            "COALESCE("
            "TO_VARCHAR(TRY_TO_DATE(" + col_ref + ", 'YYYY-MM-DD'),'YYYY-MM-DD'),"
            "TO_VARCHAR(TRY_TO_DATE(" + col_ref + ", 'DD-MM-YYYY'),'YYYY-MM-DD'),"
            "TRIM(" + col_ref + "::STRING),"
            "''"
            ")"
        )
    if engine == "bigquery":
        return (
            "COALESCE("
            "FORMAT_DATE('%F', SAFE.PARSE_DATE('%F', " + col_ref + ")) ,"
            "FORMAT_DATE('%F', SAFE.PARSE_DATE('%d-%m-%Y', " + col_ref + ")) ,"
            "TRIM(CAST(" + col_ref + " AS STRING)),"
            "''"
            ")"
        )
    # databricks
    return (
        "COALESCE("
        "date_format(coalesce(to_date(" + col_ref + ", 'yyyy-MM-dd'), to_date(" + col_ref + ", 'dd-MM-yyyy')), 'yyyy-MM-dd'),"
        "TRIM(CAST(" + col_ref + " AS STRING)),"
        "''"
        ")"
    )


def _timestamp_expr_v2(engine: str, col_ref: str, dtype_upper: str, timestamp_mode: str | None):
    engine = engine.lower()
    use_date_only = timestamp_mode == "Use Date Only (Recommended)"

    if engine == "snowflake":
        fmt = "YYYY-MM-DD" if use_date_only else "YYYY-MM-DD HH24:MI:SS.FF3"
        return f"COALESCE(TO_CHAR({col_ref}, '{fmt}'),'')"

    if engine == "bigquery":
        if "DATETIME" in dtype_upper:
            fmt = "%Y-%m-%d" if use_date_only else "%Y-%m-%d %H:%M:%E3S"
            return f"COALESCE(FORMAT_DATETIME('{fmt}', {col_ref}), '')"
        # TIMESTAMP
        fmt = "%Y-%m-%d" if use_date_only else "%Y-%m-%d %H:%M:%E3S"
        # Do not force timezone conversion here; treat as stored.
        return f"COALESCE(FORMAT_TIMESTAMP('{fmt}', CAST({col_ref} AS TIMESTAMP)), '')"

    # databricks
    fmt = "yyyy-MM-dd" if use_date_only else "yyyy-MM-dd HH:mm:ss.SSS"
    return f"COALESCE(date_format({col_ref}, '{fmt}'),'')"


def build_row_hash_query_v2(
    engine,
    catalog,
    schema,
    table,
    schema_rows,
    include_timestamp=True,
    timestamp_mode=None,
    where_clause="1=1",
):
    engine = engine.lower()
    table_fqn = qualify_table(engine, catalog, schema, table)

    concat_parts = []
    schema_rows = sorted(schema_rows or [], key=lambda x: str(x.get("column_name", "")).lower())

    for row in schema_rows:
        col = row.get("column_name")
        if not col:
            continue

        dtype_upper = str(row.get("data_type") or "").upper()
        col_ref = _quote_col_v2(engine, col)

        # Skip unsupported complex types (per user-provided logic)
        if any(x in dtype_upper for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
            continue

        # BINARY: include as deterministic HEX string
        if "BINARY" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(UPPER(TO_VARCHAR({col_ref}, 'HEX')), '')"
            elif engine == "databricks":
                expr = f"COALESCE(UPPER(HEX({col_ref})),'')"
            else:  # bigquery
                expr = f"COALESCE(UPPER(TO_HEX({col_ref})), '')"

            concat_parts.append(str(expr).strip())
            continue

        # TIMESTAMP / DATETIME
        if ("TIMESTAMP" in dtype_upper) or ("DATETIME" in dtype_upper):
            if not include_timestamp:
                continue
            expr = _timestamp_expr_v2(engine, col_ref, dtype_upper, timestamp_mode)

        # DATE
        elif "DATE" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(TO_CHAR({col_ref}, 'DD-MM-YYYY'),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(FORMAT_DATE('%d-%m-%Y', CAST({col_ref} AS DATE)), '')"
            else:
                expr = f"COALESCE(date_format({col_ref}, 'dd-MM-yyyy'),'')"

        # TIME
        elif "TIME" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(TO_CHAR({col_ref}, 'HH24:MI:SS'),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(FORMAT_TIME('%H:%M:%S', CAST({col_ref} AS TIME)), '')"
            else:
                expr = f"COALESCE(substr(CAST({col_ref} AS STRING),1,8),'')"

        # BOOLEAN
        elif "BOOLEAN" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(LOWER({col_ref}::STRING),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(LOWER(CAST({col_ref} AS STRING)), '')"
            else:
                expr = f"COALESCE(LOWER(CAST({col_ref} AS STRING)),'')"

        # NUMERIC
        elif any(x in dtype_upper for x in ["INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT", "NUMBER", "NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT", "DOUBLE", "REAL", "LONG"]):
            expr = _numeric_expr_v2(engine, col_ref)

        # STRING / OTHER SIMPLE TYPES
        else:
            if _is_date_like_string(col, dtype_upper):
                expr = _string_date_expr(engine, col_ref)
            elif engine == "snowflake":
                expr = f"COALESCE(TRIM({col_ref}::STRING),'')"
            else:
                expr = f"COALESCE(TRIM(CAST({col_ref} AS STRING)),'')"

        concat_parts.append(str(expr).strip())

    if not concat_parts:
        raise ValueError("No columns available for hashing")

    concat_expr = ",\n                    ".join(concat_parts)

    where_sql = (str(where_clause).strip() or "1=1")

    if engine == "snowflake":
        return f"""
        SELECT
            UPPER(MD5_HEX(
                CONCAT_WS('|',
                    {concat_expr}
                )
            )) AS hash_value
        FROM {table_fqn}
        WHERE {where_sql}
        ORDER BY hash_value
        """.strip()

    if engine == "databricks":
        return f"""
        SELECT
            UPPER(md5(
                concat_ws('|',
                    {concat_expr}
                )
            )) AS hash_value
        FROM {table_fqn}
        WHERE {where_sql}
        ORDER BY hash_value
        """.strip()

    if engine == "bigquery":
        # BigQuery has no concat_ws; build CONCAT(p1,'|',p2,'|',...)
        if len(concat_parts) == 1:
            signature_expr = concat_parts[0]
        else:
            signature_expr = "CONCAT(" + ", '|' , ".join(concat_parts) + ")"

        return f"""
        WITH source_data AS (
            SELECT {signature_expr} AS row_signature
            FROM {table_fqn}
            WHERE {where_sql}
        )
        SELECT
            UPPER(TO_HEX(MD5(row_signature))) AS hash_value
        FROM source_data
        ORDER BY hash_value
        """.strip()

    raise ValueError(f"Row hash not supported for engine: {engine}")


def build_categorical_hash_query(
    engine,
    catalog,
    schema,
    table,
    schema_rows,
    categorical_columns,
    include_timestamp=True,
    timestamp_mode=None,
    where_clause="1=1",
):
    engine = engine.lower()
    table_fqn = qualify_table(engine, catalog, schema, table)
    
    if not categorical_columns:
        raise ValueError("categorical_columns is required for categorical hash validation")

    concat_parts = []
    schema_rows = sorted(schema_rows or [], key=lambda x: str(x.get("column_name", "")).lower())

    for row in schema_rows:
        col = row.get("column_name")
        if not col:
            continue
        dtype_upper = str(row.get("data_type") or "").upper()
        col_ref = _quote_col_v2(engine, col)

        if any(x in dtype_upper for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
            continue

        if "BINARY" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(UPPER(TO_VARCHAR({col_ref}, 'HEX')), '')"
            elif engine == "databricks":
                expr = f"COALESCE(UPPER(HEX({col_ref})),'')"
            else:  # bigquery
                expr = f"COALESCE(UPPER(TO_HEX({col_ref})), '')"
            concat_parts.append(str(expr).strip())
            continue

        if ("TIMESTAMP" in dtype_upper) or ("DATETIME" in dtype_upper):
            if not include_timestamp:
                continue
            expr = _timestamp_expr_v2(engine, col_ref, dtype_upper, timestamp_mode)
        elif "DATE" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(TO_CHAR({col_ref}, 'YYYY-MM-DD'),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(FORMAT_DATE('%F', CAST({col_ref} AS DATE)), '')"
            else:
                expr = f"COALESCE(date_format({col_ref}, 'yyyy-MM-dd'),'')"
        elif "TIME" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(TO_CHAR({col_ref}, 'HH24:MI:SS'),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(FORMAT_TIME('%H:%M:%S', CAST({col_ref} AS TIME)), '')"
            else:
                expr = f"COALESCE(substr(CAST({col_ref} AS STRING),1,8),'')"
        elif "BOOLEAN" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(LOWER({col_ref}::STRING),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(LOWER(CAST({col_ref} AS STRING)), '')"
            else:
                expr = f"COALESCE(LOWER(CAST({col_ref} AS STRING)),'')"
        elif any(x in dtype_upper for x in ["INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT", "NUMBER", "NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT", "DOUBLE", "REAL", "LONG"]):
            expr = _numeric_expr_v2(engine, col_ref)
        else:
            if _is_date_like_string(col, dtype_upper):
                expr = _string_date_expr(engine, col_ref)
            elif engine == "snowflake":
                expr = f"COALESCE(TRIM({col_ref}::STRING),'')"
            else:
                expr = f"COALESCE(TRIM(CAST({col_ref} AS STRING)),'')"
        
        concat_parts.append(str(expr).strip())

    if not concat_parts:
        raise ValueError("No columns available for hashing")

    def _null_to_token(expr: str) -> str:
        return f"COALESCE(NULLIF({expr}, ''), '<NULL>')"

    def _group_key_expr(engine_name: str, col_name: str, dtype_upper: str, raw_type: str) -> str:
        col_ref = _quote_col_v2(engine_name, col_name)

        if "BINARY" in dtype_upper:
            if engine_name == "snowflake":
                return f"COALESCE(UPPER(TO_VARCHAR({col_ref}, 'HEX')), '<NULL>')"
            if engine_name == "databricks":
                return f"COALESCE(UPPER(HEX({col_ref})), '<NULL>')"
            return f"COALESCE(UPPER(TO_HEX({col_ref})), '<NULL>')"

        if ("TIMESTAMP" in dtype_upper) or ("DATETIME" in dtype_upper):
            return _null_to_token(_timestamp_expr_v2(engine_name, col_ref, dtype_upper, timestamp_mode))

        if "DATE" in dtype_upper:
            if engine_name == "snowflake":
                return _null_to_token(f"COALESCE(TO_CHAR({col_ref}, 'YYYY-MM-DD'),'')")
            if engine_name == "bigquery":
                return _null_to_token(f"COALESCE(FORMAT_DATE('%F', CAST({col_ref} AS DATE)), '')")
            return _null_to_token(f"COALESCE(date_format({col_ref}, 'yyyy-MM-dd'),'')")

        if "TIME" in dtype_upper:
            if engine_name == "snowflake":
                return _null_to_token(f"COALESCE(TO_CHAR({col_ref}, 'HH24:MI:SS'),'')")
            if engine_name == "bigquery":
                return _null_to_token(f"COALESCE(FORMAT_TIME('%H:%M:%S', CAST({col_ref} AS TIME)), '')")
            return _null_to_token(f"COALESCE(substr(CAST({col_ref} AS STRING),1,8),'')")

        if "BOOLEAN" in dtype_upper:
            if engine_name == "snowflake":
                return _null_to_token(f"COALESCE(LOWER({col_ref}::STRING),'')")
            return _null_to_token(f"COALESCE(LOWER(CAST({col_ref} AS STRING)), '')")

        if any(x in dtype_upper for x in ["INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT", "NUMBER", "NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT", "DOUBLE", "REAL", "LONG"]):
            return _null_to_token(_numeric_expr_v2(engine_name, col_ref))

        if _is_date_like_string(col_name, dtype_upper):
            return _null_to_token(_string_date_expr(engine_name, col_ref))

        if engine_name == "snowflake":
            return _null_to_token(f"COALESCE(TRIM({col_ref}::STRING),'')")

        return _null_to_token(f"COALESCE(TRIM(CAST({col_ref} AS STRING)),'')")

    schema_type_map = {str(r.get("column_name", "")).lower(): r for r in (schema_rows or [])}

    # Group Key Selection (type-aware normalization to avoid false mismatches)
    group_select_parts = []
    for i, cat_col in enumerate(categorical_columns):
        schema_row = schema_type_map.get(str(cat_col).lower())
        dtype_upper = str(schema_row.get("data_type") or "").upper() if schema_row else ""
        raw_type = str(schema_row.get("data_type") or "") if schema_row else ""
        expr = _group_key_expr(engine, cat_col, dtype_upper, raw_type)
        group_select_parts.append(f"{expr} AS group_key_{i+1}")
    
    group_select_expr = ", ".join(group_select_parts)
    group_by_clause = ", ".join(str(i+1) for i in range(len(categorical_columns)))
    order_by_clause = ", ".join(f"group_key_{i+1}" for i in range(len(categorical_columns)))
    
    where_sql = (str(where_clause).strip() or "1=1")

    group_key_names = [f"group_key_{i+1}" for i in range(len(categorical_columns))]
    outer_group_by_clause = ", ".join(group_key_names)

    if engine == "snowflake":
        concat_expr = ",\n                        ".join(concat_parts)
        signature_expr = f"CONCAT_WS('|',\n                        {concat_expr}\n                    )"
        row_hash_expr = f"UPPER(MD5_HEX({signature_expr}))"
        group_hash_expr = "UPPER(MD5_HEX(LISTAGG(row_hash, '|') WITHIN GROUP (ORDER BY row_hash)))"
    elif engine == "databricks":
        concat_expr = ",\n                        ".join(concat_parts)
        signature_expr = f"concat_ws('|',\n                        {concat_expr}\n                    )"
        row_hash_expr = f"UPPER(md5({signature_expr}))"
        group_hash_expr = "UPPER(md5(concat_ws('|', sort_array(collect_list(row_hash)))))"
    elif engine == "bigquery":
        if len(concat_parts) == 1:
            signature_expr = concat_parts[0]
        else:
            signature_expr = "CONCAT(" + ", '|' , ".join(concat_parts) + ")"
        row_hash_expr = f"UPPER(TO_HEX(MD5({signature_expr})))"
        group_hash_expr = "UPPER(TO_HEX(MD5(STRING_AGG(row_hash, '|' ORDER BY row_hash))))"
    else:
        raise ValueError(f"Categorical hash not supported for engine: {engine}")

    return f"""
    WITH row_hashes AS (
        SELECT
            {group_select_expr},
            {row_hash_expr} AS row_hash
        FROM {table_fqn}
        WHERE {where_sql}
    )
    SELECT
        {outer_group_by_clause},
        COUNT(*) AS row_count,
        {group_hash_expr} AS group_hash_sum
    FROM row_hashes
    GROUP BY {outer_group_by_clause}
    ORDER BY {order_by_clause}
    """.strip()


def build_categorical_hash_samples_query(
    engine,
    catalog,
    schema,
    table,
    schema_rows,
    categorical_columns,
    group_key_values,
    include_timestamp=True,
    timestamp_mode=None,
    where_clause="1=1",
    limit: int = 5,
):
    engine = engine.lower()
    table_fqn = qualify_table(engine, catalog, schema, table)

    if not categorical_columns:
        raise ValueError("categorical_columns is required for categorical hash validation")

    concat_parts = []
    schema_rows = sorted(schema_rows or [], key=lambda x: str(x.get("column_name", "")).lower())

    for row in schema_rows:
        col = row.get("column_name")
        if not col:
            continue
        dtype_upper = str(row.get("data_type") or "").upper()
        col_ref = _quote_col_v2(engine, col)

        if any(x in dtype_upper for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
            continue

        if "BINARY" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(UPPER(TO_VARCHAR({col_ref}, 'HEX')), '')"
            elif engine == "databricks":
                expr = f"COALESCE(UPPER(HEX({col_ref})),'')"
            else:  # bigquery
                expr = f"COALESCE(UPPER(TO_HEX({col_ref})), '')"
            concat_parts.append(str(expr).strip())
            continue

        if ("TIMESTAMP" in dtype_upper) or ("DATETIME" in dtype_upper):
            if not include_timestamp:
                continue
            expr = _timestamp_expr_v2(engine, col_ref, dtype_upper, timestamp_mode)
        elif "DATE" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(TO_CHAR({col_ref}, 'YYYY-MM-DD'),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(FORMAT_DATE('%F', CAST({col_ref} AS DATE)), '')"
            else:
                expr = f"COALESCE(date_format({col_ref}, 'yyyy-MM-dd'),'')"
        elif "TIME" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(TO_CHAR({col_ref}, 'HH24:MI:SS'),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(FORMAT_TIME('%H:%M:%S', CAST({col_ref} AS TIME)), '')"
            else:
                expr = f"COALESCE(substr(CAST({col_ref} AS STRING),1,8),'')"
        elif "BOOLEAN" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(LOWER({col_ref}::STRING),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(LOWER(CAST({col_ref} AS STRING)), '')"
            else:
                expr = f"COALESCE(LOWER(CAST({col_ref} AS STRING)),'')"
        elif any(x in dtype_upper for x in ["INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT", "NUMBER", "NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT", "DOUBLE", "REAL", "LONG"]):
            expr = _numeric_expr_v2(engine, col_ref)
        else:
            if _is_date_like_string(col, dtype_upper):
                expr = _string_date_expr(engine, col_ref)
            elif engine == "snowflake":
                expr = f"COALESCE(TRIM({col_ref}::STRING),'')"
            else:
                expr = f"COALESCE(TRIM(CAST({col_ref} AS STRING)),'')"

        concat_parts.append(str(expr).strip())

    if not concat_parts:
        raise ValueError("No columns available for hashing")

    def _null_to_token(expr: str) -> str:
        return f"COALESCE(NULLIF({expr}, ''), '<NULL>')"

    def _group_key_expr(engine_name: str, col_name: str, dtype_upper: str, raw_type: str) -> str:
        col_ref = _quote_col_v2(engine_name, col_name)

        if "BINARY" in dtype_upper:
            if engine_name == "snowflake":
                return f"COALESCE(UPPER(TO_VARCHAR({col_ref}, 'HEX')), '<NULL>')"
            if engine_name == "databricks":
                return f"COALESCE(UPPER(HEX({col_ref})), '<NULL>')"
            return f"COALESCE(UPPER(TO_HEX({col_ref})), '<NULL>')"

        if ("TIMESTAMP" in dtype_upper) or ("DATETIME" in dtype_upper):
            return _null_to_token(_timestamp_expr_v2(engine_name, col_ref, dtype_upper, timestamp_mode))

        if "DATE" in dtype_upper:
            if engine_name == "snowflake":
                return _null_to_token(f"COALESCE(TO_CHAR({col_ref}, 'YYYY-MM-DD'),'')")
            if engine_name == "bigquery":
                return _null_to_token(f"COALESCE(FORMAT_DATE('%F', CAST({col_ref} AS DATE)), '')")
            return _null_to_token(f"COALESCE(date_format({col_ref}, 'yyyy-MM-dd'),'')")

        if "TIME" in dtype_upper:
            if engine_name == "snowflake":
                return _null_to_token(f"COALESCE(TO_CHAR({col_ref}, 'HH24:MI:SS'),'')")
            if engine_name == "bigquery":
                return _null_to_token(f"COALESCE(FORMAT_TIME('%H:%M:%S', CAST({col_ref} AS TIME)), '')")
            return _null_to_token(f"COALESCE(substr(CAST({col_ref} AS STRING),1,8),'')")

        if "BOOLEAN" in dtype_upper:
            if engine_name == "snowflake":
                return _null_to_token(f"COALESCE(LOWER({col_ref}::STRING),'')")
            return _null_to_token(f"COALESCE(LOWER(CAST({col_ref} AS STRING)), '')")

        if any(x in dtype_upper for x in ["INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT", "NUMBER", "NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT", "DOUBLE", "REAL", "LONG"]):
            return _null_to_token(_numeric_expr_v2(engine_name, col_ref))

        if _is_date_like_string(col_name, dtype_upper):
            return _null_to_token(_string_date_expr(engine_name, col_ref))

        if engine_name == "snowflake":
            return _null_to_token(f"COALESCE(TRIM({col_ref}::STRING),'')")

        return _null_to_token(f"COALESCE(TRIM(CAST({col_ref} AS STRING)),'')")

    schema_type_map = {str(r.get("column_name", "")).lower(): r for r in (schema_rows or [])}
    group_select_parts = []
    for i, cat_col in enumerate(categorical_columns):
        schema_row = schema_type_map.get(str(cat_col).lower())
        dtype_upper = str(schema_row.get("data_type") or "").upper() if schema_row else ""
        raw_type = str(schema_row.get("data_type") or "") if schema_row else ""
        expr = _group_key_expr(engine, cat_col, dtype_upper, raw_type)
        group_select_parts.append(f"{expr} AS group_key_{i+1}")

    group_select_expr = ", ".join(group_select_parts)
    group_key_names = [f"group_key_{i+1}" for i in range(len(categorical_columns))]
    where_sql = (str(where_clause).strip() or "1=1")

    if engine == "snowflake":
        concat_expr = ",\n                        ".join(concat_parts)
        signature_expr = f"CONCAT_WS('|',\n                        {concat_expr}\n                    )"
        row_hash_expr = f"UPPER(MD5_HEX({signature_expr}))"
    elif engine == "databricks":
        concat_expr = ",\n                        ".join(concat_parts)
        signature_expr = f"concat_ws('|',\n                        {concat_expr}\n                    )"
        row_hash_expr = f"UPPER(md5({signature_expr}))"
    elif engine == "bigquery":
        if len(concat_parts) == 1:
            signature_expr = concat_parts[0]
        else:
            signature_expr = "CONCAT(" + ", '|' , ".join(concat_parts) + ")"
        row_hash_expr = f"UPPER(TO_HEX(MD5({signature_expr})))"
    else:
        raise ValueError(f"Categorical hash not supported for engine: {engine}")

    def _escape_literal(value: str) -> str:
        return str(value).replace("'", "''")

    filters = []
    for i, raw in enumerate(group_key_values or []):
        col_name = f"group_key_{i+1}"
        filters.append(f"{col_name} = '{_escape_literal(raw)}'")
    filter_sql = " AND ".join(filters) if filters else "1=1"

    return f"""
    WITH row_hashes AS (
        SELECT
            {group_select_expr},
            {signature_expr} AS row_signature,
            {row_hash_expr} AS row_hash
        FROM {table_fqn}
        WHERE {where_sql}
    )
    SELECT
        row_hash,
        row_signature,
        {', '.join(group_key_names)}
    FROM row_hashes
    WHERE {filter_sql}
    ORDER BY row_hash
    LIMIT {int(limit)}
    """.strip()


def build_row_hash_mismatch_rows_query_v2(
    engine,
    catalog,
    schema,
    table,
    schema_rows,
    hash_values,
    include_timestamp=True,
    timestamp_mode=None,
    limit: int = 50,
    where_clause="1=1",
):
    """Return a query that outputs (hash_value, row_signature) for provided hashes."""
    engine = engine.lower()
    table_fqn = qualify_table(engine, catalog, schema, table)

    if not hash_values:
        raise ValueError("hash_values is required")

    # Limit the IN list to keep queries safe and fast
    hash_values = list(hash_values)[: max(1, int(limit))]

    schema_rows = sorted(schema_rows or [], key=lambda x: str(x.get("column_name", "")).lower())
    parts = []
    for row in schema_rows:
        col = row.get("column_name")
        if not col:
            continue
        dtype_upper = str(row.get("data_type") or "").upper()
        col_ref = _quote_col_v2(engine, col)

        if any(x in dtype_upper for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
            continue

        if "BINARY" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(UPPER(TO_VARCHAR({col_ref}, 'HEX')), '')"
            elif engine == "databricks":
                expr = f"COALESCE(UPPER(HEX({col_ref})),'')"
            else:  # bigquery
                expr = f"COALESCE(UPPER(TO_HEX({col_ref})), '')"

            parts.append(str(expr).strip())
            continue

        if ("TIMESTAMP" in dtype_upper) or ("DATETIME" in dtype_upper):
            if not include_timestamp:
                continue
            expr = _timestamp_expr_v2(engine, col_ref, dtype_upper, timestamp_mode)
        elif "DATE" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(TO_CHAR({col_ref}, 'YYYY-MM-DD'),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(FORMAT_DATE('%F', CAST({col_ref} AS DATE)), '')"
            else:
                expr = f"COALESCE(date_format({col_ref}, 'yyyy-MM-dd'),'')"
        elif "TIME" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(TO_CHAR({col_ref}, 'HH24:MI:SS'),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(FORMAT_TIME('%H:%M:%S', CAST({col_ref} AS TIME)), '')"
            else:
                expr = f"COALESCE(substr(CAST({col_ref} AS STRING),1,8),'')"
        elif "BOOLEAN" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(LOWER({col_ref}::STRING),'')"
            else:
                expr = f"COALESCE(LOWER(CAST({col_ref} AS STRING)),'')"
        elif any(x in dtype_upper for x in ["INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT", "NUMBER", "NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT", "DOUBLE", "REAL", "LONG"]):
            expr = _numeric_expr_v2(engine, col_ref)
        else:
            if engine == "snowflake":
                expr = f"COALESCE(TRIM({col_ref}::STRING),'')"
            else:
                expr = f"COALESCE(TRIM(CAST({col_ref} AS STRING)),'')"

        parts.append(str(expr).strip())

    if not parts:
        raise ValueError("No columns available for hashing")

    if engine == "bigquery":
        signature_expr = parts[0] if len(parts) == 1 else "CONCAT(" + ", '|' , ".join(parts) + ")"
        hash_expr = "UPPER(TO_HEX(MD5(row_signature)))"
    else:
        signature_expr = "CONCAT_WS('|',\n                    " + ",\n                    ".join(parts) + "\n                )"
        hash_expr = "UPPER(MD5_HEX(row_signature))" if engine == "snowflake" else "UPPER(md5(row_signature))"

    # Build IN list
    in_list = ", ".join([f"'{str(h)}'" for h in hash_values])

    where_sql = (str(where_clause).strip() or "1=1")

    return f"""
    WITH data AS (
        SELECT
            {signature_expr} AS row_signature
        FROM {table_fqn}
        WHERE {where_sql}
    ), hashed AS (
        SELECT
            {hash_expr} AS hash_value,
            row_signature
        FROM data
    )
    SELECT hash_value, row_signature
    FROM hashed
    WHERE hash_value IN ({in_list})
    ORDER BY hash_value
    LIMIT {int(limit)}
    """.strip()


def build_row_signature_sample_query(engine, catalog, schema, table, columns=None, limit: int = 5):
    engine = engine.lower()
    table_fqn = qualify_table(engine, catalog, schema, table)
    schema_rows = _columns_to_schema_rows(columns)

    # Reuse the exact same signature construction used for hashing.
    schema_rows = sorted(schema_rows or [], key=lambda x: str(x.get("column_name", "")).lower())
    parts = []
    for row in schema_rows:
        col = row.get("column_name")
        if not col:
            continue
        dtype_upper = str(row.get("data_type") or "").upper()
        col_ref = _quote_col_v2(engine, col)

        if any(x in dtype_upper for x in ["VARIANT", "STRUCT", "ARRAY", "OBJECT", "MAP"]):
            continue

        if "BINARY" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(UPPER(TO_VARCHAR({col_ref}, 'HEX')), '')"
            elif engine == "databricks":
                expr = f"COALESCE(UPPER(HEX({col_ref})),'')"
            else:  # bigquery
                expr = f"COALESCE(UPPER(TO_HEX({col_ref})), '')"

            parts.append(str(expr).strip())
            continue

        if ("TIMESTAMP" in dtype_upper) or ("DATETIME" in dtype_upper):
            expr = _timestamp_expr_v2(engine, col_ref, dtype_upper, timestamp_mode=None)
        elif "DATE" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(TO_CHAR({col_ref}, 'YYYY-MM-DD'),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(FORMAT_DATE('%F', CAST({col_ref} AS DATE)), '')"
            else:
                expr = f"COALESCE(date_format({col_ref}, 'yyyy-MM-dd'),'')"
        elif "TIME" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(TO_CHAR({col_ref}, 'HH24:MI:SS'),'')"
            elif engine == "bigquery":
                expr = f"COALESCE(FORMAT_TIME('%H:%M:%S', CAST({col_ref} AS TIME)), '')"
            else:
                expr = f"COALESCE(substr(CAST({col_ref} AS STRING),1,8),'')"
        elif "BOOLEAN" in dtype_upper:
            if engine == "snowflake":
                expr = f"COALESCE(LOWER({col_ref}::STRING),'')"
            else:
                expr = f"COALESCE(LOWER(CAST({col_ref} AS STRING)),'')"
        elif any(x in dtype_upper for x in ["INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT", "NUMBER", "NUMERIC", "DECIMAL", "BIGNUMERIC", "FLOAT", "DOUBLE", "REAL", "LONG"]):
            expr = _numeric_expr_v2(engine, col_ref)
        else:
            if engine == "snowflake":
                expr = f"COALESCE(TRIM({col_ref}::STRING),'')"
            else:
                expr = f"COALESCE(TRIM(CAST({col_ref} AS STRING)),'')"

        parts.append(str(expr).strip())

    if not parts:
        signature_expr = "''"
    elif engine == "bigquery":
        if len(parts) == 1:
            signature_expr = parts[0]
        else:
            signature_expr = "CONCAT(" + ", '|' , ".join(parts) + ")"
    elif engine in {"databricks", "snowflake"}:
        signature_expr = "CONCAT_WS('|',\n                    " + ",\n                    ".join(parts) + "\n                )"
    else:
        signature_expr = parts[0]

    return f"""
    SELECT {signature_expr} AS row_signature
    FROM {table_fqn}
    ORDER BY row_signature
    LIMIT {int(limit)}
    """.strip()



# =============================
# COLUMN-LEVEL DIFF QUERY BUILDER
# =============================

def build_column_diff_query(
    engine: str,
    catalog: str,
    schema: str,
    table: str,
    key_columns: list[str],
    all_columns: list[str],
    mismatch_key_values: list[tuple],
    base_where_clause: str = "1=1",
) -> str:
    """
    Build a query that fetches raw column values for rows identified by
    composite primary-key values whose hashes mismatched.

    Parameters
    ----------
    engine            : 'snowflake' | 'databricks' | 'bigquery'
    catalog / schema / table : table coordinates
    key_columns       : ordered list of PK column names
    all_columns       : all column names to SELECT (including key columns)
    mismatch_key_values : list of tuples, each a composite-key value set
                          e.g. [('1', 'foo'), ('2', 'bar')]

    Returns
    -------
    SQL string ready for execution on the given engine.
    """
    engine = engine.lower()
    table_fqn = qualify_table(engine, catalog, schema, table)

    def quote(col: str) -> str:
        if engine in {"bigquery", "databricks"}:
            return f"`{col}`"
        return col  # Snowflake: unquoted (schema query lowercases names)

    select_cols = ", ".join(quote(c) for c in all_columns)

    base_where = (str(base_where_clause).strip() or "1=1")

    # Build key-matching clause: each key combo becomes one (k1='v1' AND k2='v2') block
    if not mismatch_key_values:
        key_clause = "1=0"
    else:
        conditions = []
        for key_tuple in mismatch_key_values:
            if not isinstance(key_tuple, (list, tuple)):
                key_tuple = (key_tuple,)
            pairs = []
            for col, val in zip(key_columns, key_tuple):
                col_ref = quote(col)
                escaped = str(val).replace("'", "''")
                pairs.append(f"{col_ref} = '{escaped}'")
            conditions.append("(" + " AND ".join(pairs) + ")")
        key_clause = " OR ".join(conditions)

    where_clause = f"({base_where}) AND ({key_clause})"

    return (
        f"SELECT {select_cols}\n"
        f"FROM {table_fqn}\n"
        f"WHERE {where_clause}"
    )
