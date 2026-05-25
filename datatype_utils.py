import re


def _base_type(dtype: str) -> str:
    return re.sub(r"\s+", " ", str(dtype or "").strip().lower())


def _decimal_signature(base: str, dtype: str) -> str:
    match = re.search(r"\((\d+)\s*,\s*(\d+)\)", dtype)
    if match:
        return f"DECIMAL({match.group(1)},{match.group(2)})"
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

    if base in {"int64"}:
        return "BIGINT"
    if base in {"int", "integer"}:
        return "INT"
    if base in {"bigint", "long"}:
        return "BIGINT"
    if base == "smallint":
        return "SMALLINT"
    if base in {"tinyint", "byteint"}:
        return "TINYINT"

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

    if base == "variant":
        return "VARIANT"
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


DATA_TYPE_EQUIVALENCE = {}
