import re


def _base_type(dtype: str) -> str:
    return re.sub(r"\s+", " ", str(dtype or "").strip().lower())


def _decimal_signature(base: str, dtype: str) -> str:
    """Return INT when scale is 0 (integer-like), else DECIMAL (no precision/scale)."""
    match = re.search(r"\((\d+)\s*,\s*(\d+)\)", dtype)
    if match:
        scale = int(match.group(2))
        if scale == 0:
            return "INT"
        return "DECIMAL"
    return "DECIMAL"


def normalize_datatype(dtype, column_name=None):
    if not dtype:
        return "UNKNOWN"

    t = _base_type(dtype)
    compact = re.sub(r"\s+", "", t)

    if compact.startswith("struct<") or compact.startswith("object("):
        return "STRUCT"
    if compact.startswith("array<") or compact.startswith("array("):
        return "ARRAY"
    if compact.startswith("map<") or compact.startswith("map("):
        return "MAP"

    base = re.split(r"[\s(]", t, maxsplit=1)[0]

    if base in {"number", "decimal", "dec", "numeric", "bignumeric"}:
        return _decimal_signature(base, t)

    # Collapse all integer sub-types into a single INT canonical type
    if base in {"int64", "int", "integer", "bigint", "long", "smallint", "tinyint", "byteint"}:
        return "INT"

    if base in {"float", "float4"}:
        return "DOUBLE"
    if base in {"float8", "double", "double precision", "real", "float64"}:
        return "DOUBLE"

    if base in {"varchar", "char", "character", "string", "text"}:
        return "STRING"
    if base in {"binary", "varbinary", "bytes"}:
        return "BINARY"
    if base in {"boolean", "bool"}:
        return "BOOLEAN"

    if base in {"timestamp", "timestamp_ltz", "timestamp_ntz", "timestamp_tz", "datetime"}:
        return "TIMESTAMP"
    if t.startswith("timestamp without time zone"):
        return "TIMESTAMP"
    if t.startswith("timestamp with"):
        return "TIMESTAMP"
    if base == "date":
        return "DATE"
    if base == "time":
        return "STRING"

    # VARIANT (Snowflake) has no Databricks equivalent; treat as STRING
    if base == "variant":
        return "STRING"
    if base == "object":
        return "STRUCT"
    if base == "array":
        return "ARRAY"
    if base == "map":
        return "MAP"

    if base in {"geography", "geometry", "uuid", "vector"}:
        return base.upper()

    if base in {"void", "null"}:
        return "NULL"

    return base.upper()


def type_family(dtype: str) -> str:
    if not dtype:
        return "UNKNOWN"
    t = str(dtype).upper()

    if any(k in t for k in [
        "INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT",
        "NUMBER", "NUMERIC", "DECIMAL", "BIGNUMERIC",
        "FLOAT", "DOUBLE", "REAL", "LONG",
    ]):
        return "NUMERIC"

    if "TIMESTAMP" in t or "DATETIME" in t:
        return "TIMESTAMP"
    if "DATE" in t:
        return "DATE"
    if "BOOLEAN" in t or t == "BOOL":
        return "BOOLEAN"
    if "BINARY" in t or "BYTES" in t:
        return "BINARY"
    if any(k in t for k in ["STRUCT", "ARRAY", "MAP", "OBJECT", "VARIANT"]):
        return "COMPLEX"
    if any(k in t for k in ["STRING", "VARCHAR", "CHAR", "TEXT"]):
        return "STRING"

    return "STRING"


def canonicalize_compatible_type(source_dtype, target_dtype, column_name=None) -> str:
    s_norm = normalize_datatype(source_dtype, column_name)
    t_norm = normalize_datatype(target_dtype, column_name)
    s_family = type_family(s_norm)
    t_family = type_family(t_norm)

    if s_family == t_family:
        if s_family == "NUMERIC":
            return "NUMBER"
        if s_family == "TIMESTAMP":
            return "TIMESTAMP"
        if s_family == "DATE":
            return "DATE"
        if s_family == "BOOLEAN":
            return "BOOLEAN"
        if s_family == "BINARY":
            return "BINARY"
        if s_family == "COMPLEX":
            return "COMPLEX"
        return "STRING"

    return "STRING"


def datatypes_compatible(source_dtype, target_dtype, column_name=None) -> bool:
    s_norm = normalize_datatype(source_dtype, column_name)
    t_norm = normalize_datatype(target_dtype, column_name)

    if s_norm == t_norm:
        return True

    return type_family(s_norm) == type_family(t_norm)


DATA_TYPE_EQUIVALENCE = {}
