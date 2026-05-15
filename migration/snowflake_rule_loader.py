"""
snowflake_rule_engine.py
========================
Deterministic rule-based translation engine: Snowflake SQL → Databricks SQL.

Mirrors the architecture of the BigQuery RuleEngine exactly:
  - apply_pre_ast_translation(sql)   – string rewrites before any AST pass
  - apply_rules(sql)                 – regex pattern rules, priority-ordered
  - apply_function_translation(sql)  – second-pass type/syntax cleanup
  - _enforce_spark_sql_safety(sql)   – final safety rails

Priority ladder (highest wins):
  110 – edge-case overrides loaded from CSV
  100 – CSV-derived function prefix rewrites
   10 – critical exact rewrites (must not be overridden by CSV)
    9 – common function remaps
    8 – syntax / operator fixes
    7 – lower-priority / context-dependent
    5 – reserved for future CSV-derived patterns
"""

from __future__ import annotations

import re
import logging
from typing import Callable, Dict, List, Optional

# basic logging setup to avoid "No logger" warnings
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


# ── tiny Jinja helpers (mirrors ast_transformer stubs) ──────────────────────

_JINJA_PLACEHOLDER = "__JINJA_{n}__"
_JINJA_RE = re.compile(r"\{[{%#].*?[}%#]\}", re.DOTALL)


def _extract_jinja(sql: str):
    """Replace Jinja blocks with opaque placeholders so regex rules cannot corrupt them."""
    store: Dict[str, str] = {}
    counter = [0]

    def _replace(m: re.Match) -> str:
        key = _JINJA_PLACEHOLDER.format(n=counter[0])
        store[key] = m.group(0)
        counter[0] += 1
        return key

    return _JINJA_RE.sub(_replace, sql), store


def _restore_jinja(sql: str, store: Dict[str, str]) -> str:
    for key, val in store.items():
        sql = sql.replace(key, val)
    return sql


# ════════════════════════════════════════════════════════════════════════════
class SnowflakeRuleEngine:
    """
    Deterministic Snowflake → Databricks SQL translation engine.

    Usage
    -----
    engine = SnowflakeRuleEngine(rules_list, edge_cases)
    sql = engine.apply_pre_ast_translation(sql)
    sql = engine.apply_rules(sql)
    sql = engine.apply_function_translation(sql)
    """

    def __init__(
        self,
        rules_list: List[Dict],
        edge_cases: List[Dict] = None,
    ) -> None:
        self.edge_cases = edge_cases or []
        self.pattern_rules = self._build_pattern_rules(rules_list, self.edge_cases)

    # ── Pre-AST string rewrites ──────────────────────────────────────────────

    @staticmethod
    def apply_pre_ast_translation(sql: str) -> str:
        """
        String-level rewrites that MUST happen before any AST parsing stage
        because the target syntax would confuse or be destroyed by the parser.
        """

        # ── Snowflake colon-path VARIANT accessor: col:field::type ──────────
        # e.g. payload:user_id::STRING  →  get_json_object(payload, '$.user_id')
        # Handle optional ::TYPE cast suffix first so it doesn't leak downstream.
        def _replace_colon_path(query: str) -> str:
            # col:a.b.c::TYPE  (with optional cast)
            pat = re.compile(
                r'\b([A-Za-z_][A-Za-z0-9_.]*)'
                r'((?::[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)+)'
                r'(?:::([A-Za-z_][A-Za-z0-9_<>, ]*))?',
                re.IGNORECASE,
            )

            def _repl(m: re.Match) -> str:
                col = m.group(1)
                path_raw = m.group(2)  # e.g. :user:nested
                cast_type = m.group(3)
                # Build JSONPath from the colon segments
                segments = [s for s in path_raw.split(':') if s]
                json_path = '$.' + '.'.join(segments)
                result = f"get_json_object({col}, '{json_path}')"
                if cast_type:
                    ct = cast_type.strip().upper()
                    # Map SF types to Databricks equivalents
                    type_map = {
                        'VARCHAR': 'STRING', 'TEXT': 'STRING', 'CHAR': 'STRING',
                        'NUMBER': 'DECIMAL', 'FLOAT': 'DOUBLE', 'FLOAT4': 'DOUBLE',
                        'FLOAT8': 'DOUBLE', 'INT': 'INT', 'INTEGER': 'INT',
                        'BIGINT': 'BIGINT', 'BOOLEAN': 'BOOLEAN', 'DATE': 'DATE',
                        'TIMESTAMP': 'TIMESTAMP', 'TIMESTAMP_NTZ': 'TIMESTAMP',
                        'TIMESTAMP_LTZ': 'TIMESTAMP', 'TIMESTAMP_TZ': 'TIMESTAMP',
                    }
                    db_type = type_map.get(ct, ct)
                    result = f"CAST({result} AS {db_type})"
                return result

            return pat.sub(_repl, query)

        sql = _replace_colon_path(sql)

        # ── LATERAL FLATTEN(INPUT => col) → LATERAL VIEW EXPLODE(col) ───────
        def _replace_lateral_flatten(query: str) -> str:
            # LATERAL FLATTEN(INPUT => col, ...) AS alias
            pat = re.compile(
                r'\bLATERAL\s+FLATTEN\s*\(\s*INPUT\s*=>\s*([^\s,)]+)(?:[^)]*?)\)'
                r'(?:\s+AS\s+(\w+))?',
                re.IGNORECASE,
            )
            def _repl(m: re.Match) -> str:
                arr_col = m.group(1)
                alias = m.group(2) or '_flat'
                return f"LATERAL VIEW EXPLODE({arr_col}) _lf AS {alias}"
            return pat.sub(_repl, query)

        sql = _replace_lateral_flatten(sql)

        # LATERAL FLATTEN(col) shorthand  (no INPUT =>)
        def _replace_lateral_flatten_short(query: str) -> str:
            pat = re.compile(
                r'\bLATERAL\s+FLATTEN\s*\(\s*([^\s),]+)\s*\)'
                r'(?:\s+AS\s+(\w+))?',
                re.IGNORECASE,
            )
            def _repl(m: re.Match) -> str:
                col = m.group(1)
                alias = m.group(2) or '_flat'
                return f"LATERAL VIEW EXPLODE({col}) _lf AS {alias}"
            return pat.sub(_repl, query)

        sql = _replace_lateral_flatten_short(sql)

        # ── FLATTEN() as a table function: TABLE(FLATTEN(input=>col)) ────────
        sql = re.sub(
            r'\bTABLE\s*\(\s*FLATTEN\s*\(\s*(?:INPUT\s*=>\s*)?([^)]+?)\s*\)\s*\)',
            r'EXPLODE(\1)',
            sql, flags=re.IGNORECASE,
        )

        # ── GENERATOR(ROWCOUNT => n) → sequence(0, n-1) ──────────────────────
        sql = re.sub(
            r'\bTABLE\s*\(\s*GENERATOR\s*\(\s*ROWCOUNT\s*=>\s*(\d+)\s*\)\s*\)',
            lambda m: f"(SELECT EXPLODE(sequence(0, {int(m.group(1)) - 1})) AS idx)",
            sql, flags=re.IGNORECASE,
        )

        # ── TOP n → LIMIT n  (must be pre-AST to avoid parser confusion) ────
        sql = re.sub(
            r'\bSELECT\s+TOP\s+(\d+)\b',
            r'SELECT',
            sql, flags=re.IGNORECASE,
        )
        # Keep the LIMIT by appending; exact position fixed in apply_function_translation.

        # ── SAMPLE(n ROWS) → TABLESAMPLE(n ROWS) ────────────────────────────
        sql = re.sub(
            r'\bSAMPLE\s*\(\s*(\d+)\s+ROWS\s*\)',
            r'TABLESAMPLE(\1 ROWS)',
            sql, flags=re.IGNORECASE,
        )
        sql = re.sub(
            r'\bSAMPLE\s*\(\s*(\d[\d.]*)\s*(?:PERCENT|%)\s*\)',
            r'TABLESAMPLE(\1 PERCENT)',
            sql, flags=re.IGNORECASE,
        )

        # ── MINUS → EXCEPT ───────────────────────────────────────────────────
        sql = re.sub(r'\bMINUS\b', 'EXCEPT', sql, flags=re.IGNORECASE)

        # ── ILIKE → LOWER(col) LIKE LOWER(pattern) ──────────────────────────
        sql = re.sub(
            r'\bILIKE\b',
            'LIKE /* TODO: ILIKE (case-insensitive LIKE) replaced with LIKE; '
            'add LOWER() on both sides if case-insensitivity is required */',
            sql, flags=re.IGNORECASE,
        )

        # ── Snowflake $n positional parameter → :param_n ────────────────────
        sql = re.sub(r'\$(\d+)\b', r':param_\1', sql)

        # ── Qualify → subquery rewrite placeholder (full rewrite in apply_rules) ─
        # Mark QUALIFY for downstream handling
        # (Snowflake QUALIFY is more common than BQ; handled in apply_rules)

        # ── Triple-dollar quoted strings $$ ... $$ → 'content' ──────────────
        def _replace_dollar_quoted(query: str) -> str:
            pat = re.compile(r'\$\$(.*?)\$\$', re.DOTALL)
            def _repl(m: re.Match) -> str:
                inner = m.group(1).replace("'", "\\'")
                return f"'{inner}'"
            return pat.sub(_repl, query)
        sql = _replace_dollar_quoted(sql)

        return sql

    # ── Pattern-rule builder ─────────────────────────────────────────────────

    def _build_pattern_rules(
        self,
        rules_list: List[Dict],
        edge_cases: List[Dict] = None,
    ) -> List[Dict]:
        if edge_cases is None:
            edge_cases = []

        # Functions whose Databricks equivalents are identical — skip CSV overrides.
        protected_native_fns = {
            'TO_DATE', 'TO_TIMESTAMP', 'DATEDIFF', 'DATEADD',
            'MONTHS_BETWEEN', 'ADD_MONTHS', 'LAST_DAY', 'NEXT_DAY',
            'DATE_TRUNC', 'DATE_PART', 'EXTRACT',
            'ARRAY_DISTINCT', 'ARRAY_APPEND',
            'MD5', 'SHA1', 'SHA2',
            'REGEXP_REPLACE', 'REGEXP_EXTRACT_ALL',
            'COALESCE', 'NULLIF',
        }

        builtins: List[Dict] = [

            # ══ DATA TYPE REWRITES ══════════════════════════════════════════

            # NUMBER(p,s) / NUMBER → DECIMAL
            {
                'pattern': r'\bNUMBER\s*\(([^)]+)\)',
                'replacement': r'DECIMAL(\1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bNUMBER\b(?!\s*\()',
                'replacement': 'DECIMAL',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # BYTEINT → TINYINT
            {
                'pattern': r'\bBYTEINT\b',
                'replacement': 'TINYINT',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # FLOAT4 / FLOAT8 → DOUBLE
            {
                'pattern': r'\bFLOAT[48]\b',
                'replacement': 'DOUBLE',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DOUBLE PRECISION / REAL → DOUBLE
            {
                'pattern': r'\bDOUBLE\s+PRECISION\b',
                'replacement': 'DOUBLE',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bREAL\b',
                'replacement': 'DOUBLE',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # DECFLOAT → DECIMAL(38,18)
            {
                'pattern': r'\bDECFLOAT\b',
                'replacement': 'DECIMAL(38, 18)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # VARCHAR → STRING  (bare, without (n))
            {
                'pattern': r'\bVARCHAR\b(?!\s*\()',
                'replacement': 'STRING',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # TEXT → STRING
            {
                'pattern': r'\bTEXT\b',
                'replacement': 'STRING',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # CHAR / CHARACTER (bare) → STRING
            {
                'pattern': r'\bCHARACTER\b(?!\s*(?:_LENGTH|ISTICS|\()))',
                'replacement': 'CHAR',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # VARBINARY → BINARY
            {
                'pattern': r'\bVARBINARY\b',
                'replacement': 'BINARY',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TIMESTAMP_LTZ / TIMESTAMP_TZ → TIMESTAMP
            {
                'pattern': r'\bTIMESTAMP_LTZ\b',
                'replacement': 'TIMESTAMP',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bTIMESTAMP_TZ\b',
                'replacement': (
                    'TIMESTAMP'
                    ' /* TODO: TIMESTAMP_TZ (with timezone offset) not natively supported;'
                    ' use TIMESTAMP + separate offset column if needed */'
                ),
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TIMESTAMP_NTZ → TIMESTAMP_NTZ (same in Databricks, keep)
            # DATETIME → TIMESTAMP
            {
                'pattern': r'\bDATETIME\b(?!\s*\()',
                'replacement': 'TIMESTAMP',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TIME (data type) → STRING with note
            {
                'pattern': r'(?<=\s)TIME\b(?!\s*\(|\s+ZONE|\s+WITH)',
                'replacement': 'STRING /* TODO: Snowflake TIME type has no Databricks equivalent; store as HH:mm:ss STRING */',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # VARIANT → VARIANT (DBR 15+) with note
            {
                'pattern': r'\bVARIANT\b(?!\s*\()',
                'replacement': 'VARIANT /* DBR 15+: native VARIANT; use STRING + JSON functions on earlier runtimes */',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # OBJECT → MAP<STRING, STRING> with note
            {
                'pattern': r'\bOBJECT\b(?!\s*_)',
                'replacement': 'MAP<STRING, STRING> /* TODO: Snowflake OBJECT mapped to MAP; consider STRUCT<> or VARIANT */',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # GEOGRAPHY → STRING WKT
            {
                'pattern': r'\bGEOGRAPHY\b',
                'replacement': 'STRING /* TODO: GEOGRAPHY type not natively supported; use WKT STRING with H3/Spatial functions */',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # GEOMETRY → STRING WKT
            {
                'pattern': r'\bGEOMETRY\b',
                'replacement': 'STRING /* TODO: GEOMETRY type not natively supported; store as WKT STRING */',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # AUTOINCREMENT / AUTO_INCREMENT → GENERATED ALWAYS AS IDENTITY
            {
                'pattern': r'\bAUTOINCREMENT\b',
                'replacement': 'GENERATED ALWAYS AS IDENTITY',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bAUTO_INCREMENT\b',
                'replacement': 'GENERATED ALWAYS AS IDENTITY',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # VECTOR(type, n) → ARRAY<FLOAT>
            {
                'pattern': r'\bVECTOR\s*\([^)]+\)',
                'replacement': 'ARRAY<FLOAT> /* TODO: Snowflake VECTOR type; use Mosaic AI Vector Search for similarity queries */',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # UUID (column type) → STRING
            {
                'pattern': r'\bUUID\b(?!\s*\(\s*\))',
                'replacement': 'STRING /* UUID stored as STRING */',
                'flags': re.IGNORECASE, 'priority': 7,
            },

            # ══ STRING FUNCTIONS ════════════════════════════════════════════

            # CHARINDEX(substr, str, pos) → INSTR(str, substr)
            # Snowflake: CHARINDEX(needle, haystack [, start])
            # Databricks: INSTR(haystack, needle [, start])
            {
                'pattern': r'\bCHARINDEX\s*\(([^,]+),\s*([^,)]+)\)',
                'replacement': r'INSTR(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bCHARINDEX\s*\(([^,]+),\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'INSTR(\2, \1, \3)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # POSITION(substr IN str) → LOCATE(substr, str)
            {
                'pattern': r'\bPOSITION\s*\(([^)]+)\s+IN\s+([^)]+)\)',
                'replacement': r'LOCATE(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # STRTOK(s, delim, part_no) → SPLIT_PART(s, delim, part_no)
            {
                'pattern': r'\bSTRTOK\s*\(([^,]+),\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'SPLIT_PART(\1, \2, \3)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # UNICODE(s) → ORD(s)
            {
                'pattern': r'\bUNICODE\s*\(',
                'replacement': r'ORD(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # REGEXP_LIKE(s, pat) → s RLIKE pat
            {
                'pattern': r'\bREGEXP_LIKE\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'\1 RLIKE \2',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # REGEXP_SUBSTR(s, pat, pos, occ) → REGEXP_EXTRACT(s, pat, idx)
            {
                'pattern': r'\bREGEXP_SUBSTR\s*\(([^,]+),\s*([^,]+),\s*[^,]+,\s*([^)]+)\)',
                'replacement': r'REGEXP_EXTRACT(\1, \2, \3)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bREGEXP_SUBSTR\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'REGEXP_EXTRACT(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # REGEXP_COUNT(s, pat) → SIZE(REGEXP_EXTRACT_ALL(s, pat))
            {
                'pattern': r'\bREGEXP_COUNT\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'SIZE(REGEXP_EXTRACT_ALL(\1, \2))',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # REGEXP_INSTR(s, pat, ...) → no direct match, flag
            {
                'pattern': r'\bREGEXP_INSTR\s*\(([^)]+)\)',
                'replacement': r'\1 /* TODO: REGEXP_INSTR has no direct Databricks equivalent; use REGEXP_EXTRACT_ALL + array indexing */',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # LEN(s) → LENGTH(s)
            {
                'pattern': r'\bLEN\s*\(',
                'replacement': r'LENGTH(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # BTRIM(s, chars) → TRIM(BOTH chars FROM s)
            {
                'pattern': r'\bBTRIM\s*\(([^,)]+),\s*([^)]+)\)',
                'replacement': r'TRIM(BOTH \2 FROM \1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TRIM(chars FROM s) — Snowflake supports this, Databricks too, keep
            # EDITDISTANCE → LEVENSHTEIN
            {
                'pattern': r'\bEDITDISTANCE\s*\(',
                'replacement': r'LEVENSHTEIN(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # STARTSWITH(s, prefix) → STARTSWITH(s, prefix) (same in DBR)
            # ENDSWITH / CONTAINS same — no change needed

            # ══ NUMERIC FUNCTIONS ═══════════════════════════════════════════

            # TRUNCATE(n, d) / TRUNC(n, d) → TRUNC(n, d)
            {
                'pattern': r'\bTRUNCATE\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'TRUNC(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # UNIFORM(min, max, gen) → FLOOR(RAND() * (max - min + 1)) + min
            {
                'pattern': r'\bUNIFORM\s*\(([^,]+),\s*([^,]+),\s*[^)]+\)',
                'replacement': r'(FLOOR(RAND() * (\2 - \1 + 1)) + \1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DIV0(a, b) → try_divide(a, b) (returns 0 on div-by-zero)
            {
                'pattern': r'\bDIV0\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'COALESCE(try_divide(\1, \2), 0)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # DIV0NULL(a, b) → try_divide(a, b) (returns NULL on div-by-zero)
            {
                'pattern': r'\bDIV0NULL\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'try_divide(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # ZEROIFNULL(a) → COALESCE(a, 0)
            {
                'pattern': r'\bZEROIFNULL\s*\(([^)]+)\)',
                'replacement': r'COALESCE(\1, 0)',
                'flags': re.IGNORECASE, 'priority': 105,  # beats CSV prefix (100)
            },
            # NULLIFZERO(a) → NULLIF(a, 0)
            {
                'pattern': r'\bNULLIFZERO\s*\(([^)]+)\)',
                'replacement': r'NULLIF(\1, 0)',
                'flags': re.IGNORECASE, 'priority': 105,
            },
            # SQUARE(n) → POWER(n, 2)
            {
                'pattern': r'\bSQUARE\s*\(([^)]+)\)',
                'replacement': r'POWER(\1, 2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # CBRT(n) → POWER(n, 1.0/3)
            {
                'pattern': r'\bCBRT\s*\(([^)]+)\)',
                'replacement': r'POWER(\1, 1.0/3)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # HAVERSINE(lat1, lon1, lat2, lon2) → comment
            {
                'pattern': r'\bHAVERSINE\s*\(([^)]+)\)',
                'replacement': r'\1 /* TODO: HAVERSINE has no Databricks equivalent; implement manually */',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ══ CONDITIONAL FUNCTIONS ═══════════════════════════════════════

            # IFF(cond, a, b) → IF(cond, a, b)
            {
                'pattern': r'\bIFF\s*\(',
                'replacement': r'IF(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # NVL(a, b) → COALESCE(a, b)
            {
                'pattern': r'\bNVL\s*\(',
                'replacement': r'COALESCE(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # NVL2(a, b, c) → IF(a IS NOT NULL, b, c)
            {
                'pattern': r'\bNVL2\s*\(([^,]+),\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'IF(\1 IS NOT NULL, \2, \3)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # EQUAL_NULL(a, b) → a IS NOT DISTINCT FROM b
            {
                'pattern': r'\bEQUAL_NULL\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'(\1 IS NOT DISTINCT FROM \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # DECODE(expr, v1, r1, ..., def) — simple 2-branch form
            {
                'pattern': r'\bDECODE\s*\(([^,]+),\s*([^,]+),\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'CASE WHEN \1 = \2 THEN \3 ELSE \4 END',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # BOOLAND / BOOLOR / BOOLXOR → AND / OR / XOR
            {
                'pattern': r'\bBOOLAND\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'(\1 AND \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bBOOLOR\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'(\1 OR \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bBOOLXOR\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'((\1 OR \2) AND NOT (\1 AND \2))',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ══ DATE & TIME FUNCTIONS ════════════════════════════════════════

            # GETDATE() / SYSDATE() → current_timestamp()
            {
                'pattern': r'\bGETDATE\s*\(\s*\)',
                'replacement': r'current_timestamp()',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bSYSDATE\s*\(\s*\)',
                'replacement': r'current_timestamp() /* NOTE: SYSDATE deprecated in Databricks; using current_timestamp() */',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # CURRENT_TIME() → date_format(now(), 'HH:mm:ss')
            {
                'pattern': r'\bCURRENT_TIME\s*\(\s*\)',
                'replacement': r"date_format(now(), 'HH:mm:ss')",
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # DATEADD(part, n, d) → appropriate Databricks form
            # For DAY → date_add; for MONTH → add_months; for YEAR → add_months * 12
            {
                'pattern': r'\bDATEADD\s*\(\s*(?:DAY|DD)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'date_add(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEADD\s*\(\s*(?:MONTH|MM)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'add_months(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEADD\s*\(\s*(?:YEAR|YY|YYYY)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'add_months(\2, (\1) * 12)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEADD\s*\(\s*(?:HOUR|HH)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'(\2 + INTERVAL \1 HOURS)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEADD\s*\(\s*(?:MINUTE|MI)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'(\2 + INTERVAL \1 MINUTES)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEADD\s*\(\s*(?:SECOND|SS)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'(\2 + INTERVAL \1 SECONDS)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEADD\s*\(\s*(?:WEEK|WK)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'date_add(\2, (\1) * 7)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEADD\s*\(\s*(?:QUARTER|QQ)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'add_months(\2, (\1) * 3)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # TIMESTAMPADD — alias of DATEADD
            {
                'pattern': r'\bTIMESTAMPADD\s*\(\s*(?:DAY|DD)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'date_add(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bTIMESTAMPADD\s*\(\s*(?:MONTH|MM)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'add_months(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bTIMESTAMPADD\s*\(\s*(\w+)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'(\3 + INTERVAL \2 \1S)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DATEDIFF(part, d1, d2) — Snowflake returns d2 - d1
            # Databricks DATEDIFF(end, start) for days; DATEDIFF(part, start, end) otherwise
            {
                'pattern': r'\bDATEDIFF\s*\(\s*(?:DAY|DD)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'datediff(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEDIFF\s*\(\s*(?:MONTH|MM)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'CAST(FLOOR(MONTHS_BETWEEN(\2, \1)) AS INT)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEDIFF\s*\(\s*(?:YEAR|YY|YYYY)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'FLOOR(MONTHS_BETWEEN(\2, \1) / 12)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEDIFF\s*\(\s*(?:HOUR|HH)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'(UNIX_TIMESTAMP(\2) - UNIX_TIMESTAMP(\1)) / 3600',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEDIFF\s*\(\s*(?:MINUTE|MI)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'(UNIX_TIMESTAMP(\2) - UNIX_TIMESTAMP(\1)) / 60',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEDIFF\s*\(\s*(?:SECOND|SS)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'(UNIX_TIMESTAMP(\2) - UNIX_TIMESTAMP(\1))',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEDIFF\s*\(\s*(?:WEEK|WK)\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'FLOOR(datediff(\2, \1) / 7)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # TO_CHAR(d, fmt) / TO_VARCHAR(d, fmt) → DATE_FORMAT(d, fmt)
            {
                'pattern': r'\bTO_(?:CHAR|VARCHAR)\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'DATE_FORMAT(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # TO_CHAR(x) → CAST(x AS STRING)
            {
                'pattern': r'\bTO_CHAR\s*\(([^,)]+)\)',
                'replacement': r'CAST(\1 AS STRING)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DATE_TRUNC — Snowflake: DATE_TRUNC(part, d)  Databricks: DATE_TRUNC('part', d) — same arg order
            # Normalize unquoted part to quoted
            {
                'pattern': r"\bDATE_TRUNC\s*\(\s*(YEAR|MONTH|DAY|WEEK|HOUR|MINUTE|SECOND|QUARTER)\s*,\s*([^)]+)\)",
                'replacement': r"DATE_TRUNC('\1', \2)",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # CONVERT_TIMEZONE(src_tz, tgt_tz, ts) → CONVERT_TIMEZONE(tgt_tz, src_tz, ts)
            # Databricks: CONVERT_TIMEZONE(sourceTimeZone, targetTimeZone, sourceTimestamp)
            # Snowflake:  CONVERT_TIMEZONE(sourceTimeZone, targetTimeZone, timestamp)
            # Actually identical — but if called as 2-arg (just tgt_tz, ts):
            {
                'pattern': r'\bCONVERT_TIMEZONE\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'CONVERT_TIMEZONE(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TIME_SLICE(ts, n, part) → DATE_TRUNC or floor arithmetic
            {
                'pattern': r'\bTIME_SLICE\s*\([^)]+\)',
                'replacement': r'\0 /* TODO: TIME_SLICE has no direct Databricks equivalent; use DATE_TRUNC or floor arithmetic */',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ══ AGGREGATE FUNCTIONS ══════════════════════════════════════════

            # LISTAGG(col, delim) → CONCAT_WS(delim, COLLECT_LIST(col))
            {
                'pattern': r'\bLISTAGG\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'CONCAT_WS(\2, COLLECT_LIST(\1))',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # ARRAY_AGG(col) → collect_list(col)
            {
                'pattern': r'\bARRAY_AGG\s*\(',
                'replacement': r'collect_list(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # OBJECT_AGG(key, val) → MAP_FROM_ENTRIES(COLLECT_LIST(struct(key, val)))
            {
                'pattern': r'\bOBJECT_AGG\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'MAP_FROM_ENTRIES(COLLECT_LIST(struct(\1, \2)))',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # MEDIAN(col) → PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY col)
            {
                'pattern': r'\bMEDIAN\s*\(([^)]+)\)',
                'replacement': r'PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY \1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # APPROX_PERCENTILE(col, p) → APPROX_PERCENTILE(col, p)  — same, keep
            # BOOLOR_AGG / BOOLAND_AGG
            {
                'pattern': r'\bBOOLOR_AGG\s*\(',
                'replacement': r'BOOL_OR(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bBOOLAND_AGG\s*\(',
                'replacement': r'BOOL_AND(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # COUNT_IF(cond) → SUM(CASE WHEN cond THEN 1 ELSE 0 END)
            {
                'pattern': r'\bCOUNTIF\s*\(',
                'replacement': r'COUNT_IF(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # HASH_AGG → TODO comment
            {
                'pattern': r'\bHASH_AGG\s*\([^)]*\)',
                'replacement': r'NULL /* TODO: HASH_AGG has no Databricks equivalent; use checksums or XOR of hashes */',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ══ WINDOW FUNCTIONS ═════════════════════════════════════════════

            # RANGE BETWEEN INTERVAL 'n' DAY → RANGE BETWEEN INTERVAL n DAYS
            {
                'pattern': r"RANGE\s+BETWEEN\s+INTERVAL\s+'(\d+)'\s+(DAY|MONTH|YEAR|HOUR|MINUTE|SECOND)\s+PRECEDING",
                'replacement': r'RANGE BETWEEN INTERVAL \1 \2S PRECEDING',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r"RANGE\s+BETWEEN\s+INTERVAL\s+'(\d+)'\s+(DAY|MONTH|YEAR|HOUR|MINUTE|SECOND)\s+FOLLOWING",
                'replacement': r'RANGE BETWEEN INTERVAL \1 \2S FOLLOWING',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # QUALIFY row filter → subquery (flag for downstream rewrite)
            {
                'pattern': r'\bQUALIFY\s+',
                'replacement': r'QUALIFY /* NOTE: Databricks supports QUALIFY natively in DBR 12.2+; subquery needed for earlier runtimes */ ',
                'flags': re.IGNORECASE, 'priority': 7,
            },

            # ══ CONVERSION FUNCTIONS ═════════════════════════════════════════

            # TRY_CAST — same in Databricks
            # TO_NUMBER(s, fmt) → CAST(s AS DECIMAL)
            {
                'pattern': r'\bTO_NUMBER\s*\(([^,)]+)\)',
                'replacement': r'CAST(\1 AS DECIMAL)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bTO_NUMBER\s*\(([^,]+),\s*([^,)]+)\)',
                'replacement': r'CAST(\1 AS DECIMAL)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # TO_DECIMAL(s, fmt, p, s) → CAST(s AS DECIMAL(p, s))
            {
                'pattern': r'\bTO_DECIMAL\s*\(([^,]+),\s*[^,]+,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'CAST(\1 AS DECIMAL(\2, \3))',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # TO_BOOLEAN(s) → CAST(s AS BOOLEAN)
            {
                'pattern': r'\bTO_BOOLEAN\s*\(([^)]+)\)',
                'replacement': r'CAST(\1 AS BOOLEAN)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TO_VARCHAR(val) (no format) → CAST(val AS STRING)
            {
                'pattern': r'\bTO_VARCHAR\s*\(([^,)]+)\)',
                'replacement': r'CAST(\1 AS STRING)',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # TRY_TO_NUMBER / TRY_TO_DECIMAL → TRY_CAST
            {
                'pattern': r'\bTRY_TO_NUMBER\s*\(([^)]+)\)',
                'replacement': r'TRY_CAST(\1 AS DECIMAL)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bTRY_TO_DECIMAL\s*\(([^)]+)\)',
                'replacement': r'TRY_CAST(\1 AS DECIMAL)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bTRY_TO_BOOLEAN\s*\(([^)]+)\)',
                'replacement': r'TRY_CAST(\1 AS BOOLEAN)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bTRY_TO_DATE\s*\(([^,)]+)\)',
                'replacement': r'TRY_CAST(\1 AS DATE)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bTRY_TO_TIMESTAMP\s*\(([^,)]+)\)',
                'replacement': r'TRY_CAST(\1 AS TIMESTAMP)',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ══ SEMI-STRUCTURED / JSON FUNCTIONS ════════════════════════════

            # PARSE_JSON(s) → from_json(s, schema) — schema must be supplied by user
            {
                'pattern': r'\bPARSE_JSON\s*\(',
                'replacement': r'from_json( /* TODO: provide schema string as second arg, e.g. from_json(col, \'MAP<STRING,STRING>\') */ ',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # GET(obj, key) → get_json_object(obj, '$.key')
            {
                'pattern': r"\bGET\s*\(([^,]+),\s*'([^']+)'\s*\)",
                'replacement': r"get_json_object(\1, '$.\2')",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # GET_PATH(obj, 'a.b.c') → get_json_object(obj, '$.a.b.c')
            {
                'pattern': r"\bGET_PATH\s*\(([^,]+),\s*'([^']+)'\s*\)",
                'replacement': r"get_json_object(\1, '$.\2')",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # OBJECT_KEYS(obj) → json_object_keys(col)
            {
                'pattern': r'\bOBJECT_KEYS\s*\(',
                'replacement': r'json_object_keys( /* DBR 12+; on older runtimes use from_json + schema inference */ ',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # OBJECT_CONSTRUCT(k1, v1, ...) → named_struct(k1, v1, ...)
            {
                'pattern': r'\bOBJECT_CONSTRUCT\s*\(',
                'replacement': r'named_struct(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # OBJECT_CONSTRUCT_KEEP_NULL → named_struct (same behavior in DBR)
            {
                'pattern': r'\bOBJECT_CONSTRUCT_KEEP_NULL\s*\(',
                'replacement': r'named_struct(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # ARRAY_CONSTRUCT(v1, v2, ...) → ARRAY(v1, v2, ...)
            {
                'pattern': r'\bARRAY_CONSTRUCT\s*\(',
                'replacement': r'ARRAY(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # ARRAY_CONSTRUCT_COMPACT(v1, ...) → ARRAY_COMPACT(ARRAY(v1, ...))
            {
                'pattern': r'\bARRAY_CONSTRUCT_COMPACT\s*\(',
                'replacement': r'array_compact(ARRAY(',
                'flags': re.IGNORECASE, 'priority': 9,
                'note': 'Adds extra open paren; downstream must close it',
            },
            # ARRAY_SIZE(arr) → SIZE(arr)
            {
                'pattern': r'\bARRAY_SIZE\s*\(',
                'replacement': r'SIZE(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # ARRAY_CONTAINS(val, arr) → ARRAY_CONTAINS(arr, val)  ← args SWAPPED in Snowflake
            {
                'pattern': r'\bARRAY_CONTAINS\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'array_contains(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # ARRAY_CAT(arr1, arr2) → CONCAT(arr1, arr2)
            {
                'pattern': r'\bARRAY_CAT\s*\(',
                'replacement': r'CONCAT(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # ARRAY_SLICE(arr, from, to) → SLICE(arr, from+1, to-from)
            # Snowflake: 0-indexed; Databricks SLICE: 1-indexed, length-based
            {
                'pattern': r'\bARRAY_SLICE\s*\(([^,]+),\s*(\d+),\s*(\d+)\)',
                'replacement': lambda m: (
                    f"SLICE({m.group(1)}, {int(m.group(2)) + 1}, "
                    f"{int(m.group(3)) - int(m.group(2))})"
                ),
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # ARRAY_INTERSECTION(a1, a2) → ARRAY_INTERSECT(a1, a2)
            {
                'pattern': r'\bARRAY_INTERSECTION\s*\(',
                'replacement': r'ARRAY_INTERSECT(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # ARRAYS_OVERLAP(a1, a2) → SIZE(ARRAY_INTERSECT(a1, a2)) > 0
            {
                'pattern': r'\bARRAYS_OVERLAP\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'(SIZE(ARRAY_INTERSECT(\1, \2)) > 0)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # ARRAY_TO_STRING(arr, delim) → ARRAY_JOIN(arr, delim)
            {
                'pattern': r'\bARRAY_TO_STRING\s*\(',
                'replacement': r'ARRAY_JOIN(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TO_JSON(variant) → TO_JSON(struct_col) — same name, keep with note
            {
                'pattern': r'\bAS_VARCHAR\s*\(',
                'replacement': r'CAST(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TO_ARRAY(val) → ARRAY(val)
            {
                'pattern': r'\bTO_ARRAY\s*\(([^)]+)\)',
                'replacement': r'ARRAY(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # FLATTEN table function (residual after pre-AST pass)
            {
                'pattern': r'\bFLATTEN\s*\(\s*(?:INPUT\s*=>\s*)?([^\s,)]+)(?:[^)]*?)\)',
                'replacement': r'EXPLODE(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ══ HASH / ENCODING ══════════════════════════════════════════════

            # SHA1 / SHA1_HEX → sha1
            {
                'pattern': r'\bSHA1_HEX\s*\(',
                'replacement': r'sha1(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # HASH(val) → xxhash64(val) (closest equivalent)
            {
                'pattern': r'\bHASH\s*\(',
                'replacement': r'xxhash64( /* NOTE: Snowflake HASH output differs from xxhash64 values */ ',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # MD5_HEX → MD5
            {
                'pattern': r'\bMD5_HEX\s*\(',
                'replacement': r'md5(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # MD5 return is already hex in Databricks — no wrap needed

            # ══ CONTEXT / SYSTEM FUNCTIONS ══════════════════════════════════

            # CURRENT_ROLE() → no equivalent; stub
            {
                'pattern': r'\bCURRENT_ROLE\s*\(\s*\)',
                'replacement': r"current_user() /* NOTE: CURRENT_ROLE has no Databricks equivalent; using CURRENT_USER */",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # CURRENT_WAREHOUSE() → no equivalent
            {
                'pattern': r'\bCURRENT_WAREHOUSE\s*\(\s*\)',
                'replacement': r"'N/A' /* TODO: CURRENT_WAREHOUSE has no Databricks equivalent */",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # CURRENT_SESSION() → no equivalent
            {
                'pattern': r'\bCURRENT_SESSION\s*\(\s*\)',
                'replacement': r"spark_partition_id() /* TODO: CURRENT_SESSION; using spark_partition_id as proxy */",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # CURRENT_VERSION() → no equivalent
            {
                'pattern': r'\bCURRENT_VERSION\s*\(\s*\)',
                'replacement': r"'' /* TODO: CURRENT_VERSION has no Databricks equivalent */",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # SYSTEM$TYPEOF(expr) → TYPEOF(expr) [DBR 14.2+]
            {
                'pattern': r'\bSYSTEM\$TYPEOF\s*\(',
                'replacement': r'TYPEOF( /* DBR 14.2+ required */ ',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # RESULT_SCAN → no equivalent
            {
                'pattern': r'\bRESULT_SCAN\s*\([^)]*\)',
                'replacement': r'NULL /* TODO: RESULT_SCAN has no Databricks equivalent */',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ══ DATA GENERATION ══════════════════════════════════════════════

            # SEQ1/2/4/8() → MONOTONICALLY_INCREASING_ID()
            {
                'pattern': r'\bSEQ[1248]\s*\(\s*\)',
                'replacement': r'MONOTONICALLY_INCREASING_ID()',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # UUID_STRING() → UUID()
            {
                'pattern': r'\bUUID_STRING\s*\(\s*\)',
                'replacement': r'UUID()',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # RANDSTR(len, gen) → TODO
            {
                'pattern': r'\bRANDSTR\s*\(([^,]+),\s*[^)]+\)',
                'replacement': r"SUBSTRING(SHA2(CAST(RAND() AS STRING), 256), 1, \1)",
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ══ DDL / SQL COMMANDS ══════════════════════════════════════════

            # CREATE TRANSIENT TABLE → CREATE TABLE USING DELTA
            {
                'pattern': r'\bCREATE\s+TRANSIENT\s+TABLE\b',
                'replacement': r'CREATE TABLE /* NOTE: Snowflake TRANSIENT TABLE; no direct Databricks equivalent */ ',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # CREATE TEMPORARY TABLE → CREATE TEMPORARY VIEW
            {
                'pattern': r'\bCREATE\s+(?:OR\s+REPLACE\s+)?TEMPORARY\s+TABLE\b',
                'replacement': r'CREATE OR REPLACE TEMPORARY VIEW',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # CLUSTER BY → OPTIMIZE + ZORDER comment
            {
                'pattern': r'\bCLUSTER\s+BY\s*\(([^)]+)\)',
                'replacement': r'/* NOTE: Snowflake CLUSTER BY replaced; run: OPTIMIZE <tr> ZORDER BY (\1) */',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # AT(timestamp => ...) / AT(offset => ...) time travel
            {
                'pattern': r'\bAT\s*\(\s*TIMESTAMP\s*=>\s*([^)]+)\)',
                'replacement': r'TIMESTAMP AS OF \1',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bAT\s*\(\s*OFFSET\s*=>\s*([^)]+)\)',
                'replacement': r'VERSION AS OF \1 /* NOTE: offset-based time travel approximated; verify version number */',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # BEFORE(statement => ...) / BEFORE(offset => ...)
            {
                'pattern': r'\bBEFORE\s*\(\s*STATEMENT\s*=>\s*[^)]+\)',
                'replacement': r'/* TODO: BEFORE(STATEMENT => ...) has no Databricks equivalent; use VERSION AS OF */',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # SAMPLE / TABLESAMPLE SYSTEM → TABLESAMPLE
            {
                'pattern': r'\bTABLESAMPLE\s+SYSTEM\s*\(',
                'replacement': r'TABLESAMPLE(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # AUTOINCREMENT → GENERATED ALWAYS AS IDENTITY (DDL)
            # (handled above in type section)
            # SEQUENCE nextval / currval → IDENTITY
            {
                'pattern': r'\b(\w+)\.NEXTVAL\b',
                'replacement': r'\1_nextval() /* TODO: Snowflake SEQUENCE NEXTVAL; use IDENTITY or a custom sequence function */',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # CREATE STREAM → Delta CDF comment
            {
                'pattern': r'\bCREATE\s+(?:OR\s+REPLACE\s+)?STREAM\b',
                'replacement': (
                    r"/* TODO: Snowflake STREAM → Delta Lake Change Data Feed.\n"
                    r"   Enable with: ALTER TABLE <t> SET TBLPROPERTIES ('delta.enableChangeDataFeed'='true') */\n"
                    r"-- CREATE STREAM"
                ),
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # CREATE TASK → Databricks Workflows note
            {
                'pattern': r'\bCREATE\s+(?:OR\s+REPLACE\s+)?TASK\b',
                'replacement': r'/* TODO: Snowflake TASK → use Databricks Workflows / Jobs for scheduling */\n-- CREATE TASK',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # CREATE PIPE → Auto Loader note
            {
                'pattern': r'\bCREATE\s+(?:OR\s+REPLACE\s+)?PIPE\b',
                'replacement': r'/* TODO: Snowflake PIPE → use Databricks Auto Loader (cloudFiles) via Structured Streaming */\n-- CREATE PIPE',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # UNDROP TABLE → RESTORE TABLE
            {
                'pattern': r'\bUNDROP\s+TABLE\s+(\w+)',
                'replacement': r'RESTORE TABLE \1 TO VERSION AS OF 0 /* TODO: replace 0 with the correct Delta version */',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # CONNECT BY → Recursive CTE note
            {
                'pattern': r'\bCONNECT\s+BY\b',
                'replacement': r'/* TODO: Snowflake CONNECT BY → rewrite as: WITH RECURSIVE cte AS (...) */ CONNECT BY',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # GENERATOR(ROWCOUNT => n) fallback (after pre-AST)
            {
                'pattern': r'\bGENERATOR\s*\(\s*ROWCOUNT\s*=>\s*(\d+)\s*\)',
                'replacement': lambda m: f"EXPLODE(sequence(0, {int(m.group(1)) - 1}))",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # QUALIFY (bare, if survived pre-AST)
            # Top-10: QUALIFY ROW_NUMBER() OVER (...) = 1
            # → handled in apply_function_translation via subquery rewrite
            # MINUS (catch any residual)
            {
                'pattern': r'\bMINUS\b',
                'replacement': r'EXCEPT',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # TOP n SELECT → LIMIT (heuristic; pre-AST already stripped SELECT part)
            {
                'pattern': r'\bSELECT\s+TOP\s+(\d+)\b',
                'replacement': r'SELECT /* TODO: move LIMIT \1 to end of query */',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # Snowflake double-colon cast ::type → CAST(expr AS type)
            # Handled paren-aware in apply_function_translation; simple suffix:
            {
                'pattern': r'::(FLOAT|DOUBLE|INT|INTEGER|BIGINT|BOOLEAN|DATE|TIMESTAMP|STRING|VARCHAR|TEXT|BINARY)\b',
                'replacement': lambda m: f' /* ::{m.group(1)} cast; wrap outer expr in CAST(... AS {m.group(1)}) */',
                'flags': re.IGNORECASE, 'priority': 8,
            },
        ]

        builtins.sort(key=lambda r: r.get('priority', 0), reverse=True)

        # ── Integrate Edge Cases as priority 110 ───────────────────────────
        for edge in edge_cases:
            sf = (edge.get("snowflake") or edge.get("sf") or "").strip()
            dbx = (edge.get("databricks") or edge.get("dbx") or "").strip()
            if not sf or not dbx or sf == dbx:
                continue

            pattern_str = re.escape(sf)
            repl_str = dbx
            placeholders = re.findall(r'<([^>]+)>', pattern_str)
            current_group = 1
            for _ in placeholders:
                pattern_str = pattern_str.replace(r'\<[^>]+\>', r'(.+?)', 1)
                repl_str = re.sub(r'<[^>]+>', f'\\\\{current_group}', repl_str, count=1)
                current_group += 1

            if re.match(r'^\w', sf):
                pattern_str = r'\b' + pattern_str

            builtins.append({
                'pattern': pattern_str,
                'replacement': repl_str,
                'flags': re.IGNORECASE,
                'priority': 110,
                'source': 'edge_case',
            })

        # ── Integrate CSV rules_list at priority 100 ───────────────────────
        for row in rules_list:
            sf = (row.get("Snowflake") or row.get("snowflake") or "").strip()
            db = (row.get("Databricks_Equivalent") or row.get("databricks") or "").strip()
            if not sf or not db or sf == db:
                continue
            # Skip "No direct equivalent" mappings — we handle those in builtins
            if db.lower().startswith("no ") or db.lower().startswith("no direct"):
                continue
            # Only simple function-prefix rewrites
            fn_match = re.match(r'^(\w+)\s*\(', sf)
            if not fn_match:
                continue
            fn_name = fn_match.group(1)
            if fn_name.upper() in protected_native_fns:
                continue
            # Require the target to also be a simple function prefix
            if not re.match(r'^\w+\s*\([^()]*\)\s*$', db):
                continue
            repl_match = re.match(r'^(\w+)\s*\(', db)
            if repl_match:
                builtins.append({
                    'pattern': r'\b' + re.escape(fn_name) + r'\s*\(',
                    'replacement': repl_match.group(1) + '(',
                    'flags': re.IGNORECASE,
                    'priority': 100,
                    'source': 'csv_functions',
                })

        builtins.sort(key=lambda r: r.get('priority', 0), reverse=True)
        return builtins

    # ── Helper: balanced arg extraction ─────────────────────────────────────

    @staticmethod
    def _extract_balanced_arg(sql: str, start: int) -> str:
        """Return the content between parentheses; `start` points after the opening '('."""
        depth = 1
        i = start
        while i < len(sql) and depth > 0:
            if sql[i] == '(':
                depth += 1
            elif sql[i] == ')':
                depth -= 1
            if depth > 0:
                i += 1
        return sql[start:i]

    @staticmethod
    def _split_top_level_commas(text: str) -> List[str]:
        """Split on commas respecting nested parentheses."""
        parts: List[str] = []
        depth = 0
        start = 0
        for i, ch in enumerate(text):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ',' and depth == 0:
                parts.append(text[start:i].strip())
                start = i + 1
        parts.append(text[start:].strip())
        return [p for p in parts if p]

    # ── apply_rules ──────────────────────────────────────────────────────────

    def apply_rules(self, sql: str) -> str:
        """Apply all deterministic pattern rules in priority order."""
        sql, jinja_map = _extract_jinja(sql)

        # ── Rewrite QUALIFY ROW_NUMBER() OVER (...) = 1 to subquery ─────────
        def _rewrite_qualify(query: str) -> str:
            pat = re.compile(
                r'^\s*SELECT\s+(?P<select>[\s\S]+?)\s+FROM\s+(?P<from>[\s\S]+?)\s+'
                r'QUALIFY\s+(?:ROW_NUMBER|RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\((?P<over>[\s\S]+?)\)\s*=\s*(?P<n>\d+)\s*;?\s*$',
                re.IGNORECASE,
            )
            m = pat.match(query)
            if not m:
                return query
            return (
                "SELECT *\nFROM (\n"
                f"  SELECT {m.group('select')},\n"
                f"    ROW_NUMBER() OVER ({m.group('over')}) AS _rn\n"
                f"  FROM {m.group('from')}\n"
                ") _qualify_wrap\n"
                f"WHERE _rn = {m.group('n')}"
            )
        sql = _rewrite_qualify(sql)

        # ── Apply regex pattern rules ────────────────────────────────────────
        for rule in self.pattern_rules:
            pattern = rule['pattern']
            replacement = rule['replacement']
            flags = rule.get('flags', 0)
            try:
                prev = sql
                sql = re.sub(pattern, replacement, sql, flags=flags)
                if sql != prev:
                    logger.debug("SF Rule fired: %s", pattern[:80])
            except Exception as exc:
                logger.debug("SF Rule failed: %s — %s", pattern[:60], exc)

        sql = _restore_jinja(sql, jinja_map)
        return sql

    # ── Helper: Convert STRUCT(col AS alias, ...) to NAMED_STRUCT('alias', col, ...) ──────

    def _convert_struct_with_as_to_named_struct(self, sql: str) -> str:
        """
        Convert STRUCT(col AS alias, col2 AS alias2, ...) to NAMED_STRUCT('alias', col, 'alias2', col2, ...).
        Respects nested parentheses and quotes.
        """
        def _process_struct(match):
            inner = match.group(1)
            # Split by comma while respecting nesting
            parts = []
            depth = 0
            current = []
            in_quote = None
            for ch in inner:
                if in_quote:
                    if ch == in_quote and (len(current) == 0 or current[-1] != '\\'):
                        in_quote = None
                    current.append(ch)
                else:
                    if ch in ("'", '"', '`'):
                        in_quote = ch
                        current.append(ch)
                    elif ch == '(': 
                        depth += 1
                        current.append(ch)
                    elif ch == ')': 
                        depth -= 1
                        current.append(ch)
                    elif ch == ',' and depth == 0:
                        parts.append("".join(current).strip())
                        current = []
                    else:
                        current.append(ch)
            if current:
                parts.append("".join(current).strip())
            
            # Check if any part has AS clause
            has_as = any(re.search(r'\s+AS\s+', p, re.IGNORECASE) for p in parts)
            if not has_as:
                return f"STRUCT({inner})"
            
            # Convert each part to named_struct argument
            named_args = []
            for part in parts:
                as_match = re.search(r'\s+AS\s+([A-Za-z0-9_]+)$', part, re.IGNORECASE)
                if as_match:
                    alias = as_match.group(1)
                    val = part[:as_match.start()].strip()
                    named_args.append(f"'{alias}'")
                    named_args.append(val)
                else:
                    # No AS clause, treat as-is
                    clean_part = part.replace('"', '').replace('`', '')
                    named_args.append(f"'{clean_part}'")
                    named_args.append(part)
            
            return f"NAMED_STRUCT({', '.join(named_args)})"
        
        # Match STRUCT(...) with balanced parentheses
        pattern = r'\bSTRUCT\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)'
        result = re.sub(pattern, _process_struct, sql, flags=re.IGNORECASE)
        return result

    # ── apply_function_translation ───────────────────────────────────────────

    def apply_function_translation(self, sql: str) -> str:
        """Second-pass: type names, cast syntax, trailing commas, safety fixes."""
        sql, jinja_map = _extract_jinja(sql)

        # ── Convert STRUCT(col AS alias, ...) to NAMED_STRUCT('alias', col, ...) ──────
        sql = self._convert_struct_with_as_to_named_struct(sql)

        # ── Double-colon cast: expr::TYPE → CAST(expr AS TYPE) ────────────
        # Walk paren-balanced expressions for ::
        def _replace_double_colon_casts(query: str) -> str:
            type_map = {
                'VARCHAR': 'STRING', 'TEXT': 'STRING', 'CHAR': 'STRING',
                'NUMBER': 'DECIMAL', 'NUMERIC': 'DECIMAL',
                'FLOAT': 'DOUBLE', 'FLOAT4': 'DOUBLE', 'FLOAT8': 'DOUBLE',
                'DOUBLE': 'DOUBLE', 'REAL': 'DOUBLE',
                'INT': 'INT', 'INTEGER': 'INT', 'BIGINT': 'BIGINT',
                'SMALLINT': 'SMALLINT', 'TINYINT': 'TINYINT', 'BYTEINT': 'TINYINT',
                'BOOLEAN': 'BOOLEAN', 'BOOL': 'BOOLEAN',
                'DATE': 'DATE', 'TIMESTAMP': 'TIMESTAMP',
                'TIMESTAMP_NTZ': 'TIMESTAMP', 'TIMESTAMP_LTZ': 'TIMESTAMP',
                'TIMESTAMP_TZ': 'TIMESTAMP',
                'BINARY': 'BINARY', 'VARBINARY': 'BINARY',
                'STRING': 'STRING',
            }
            # Match simple: word_or_literal::TYPE
            pat = re.compile(
                r'(?P<expr>'
                r'(?:[A-Za-z_][A-Za-z0-9_.]*'  # identifier
                r"|\d+(?:\.\d+)?"               # number literal
                r"|'[^']*'"                      # string literal
                r')'
                r')'
                r'::(?P<type>[A-Za-z_][A-Za-z0-9_]*(?:\s*\([^)]*\))?)',
                re.IGNORECASE,
            )
            def _repl(m: re.Match) -> str:
                expr = m.group('expr')
                raw_type = m.group('type').strip()
                base = raw_type.split('(')[0].upper()
                db_type = type_map.get(base, raw_type)
                # Preserve precision if any
                suffix_m = re.search(r'\([^)]*\)', raw_type)
                if suffix_m and base not in ('VARCHAR', 'TEXT', 'CHAR'):
                    db_type = db_type + suffix_m.group(0)
                return f'CAST({expr} AS {db_type})'
            return pat.sub(_repl, query)
        sql = _replace_double_colon_casts(sql)

        # ── DATE_FORMAT(expr) (missing format) → CAST(expr AS STRING) ───────
        def _fix_date_format_missing_format(query: str) -> str:
            pat = re.compile(r'\bDATE_FORMAT\s*\(', re.IGNORECASE)
            i = 0
            out = []
            while True:
                m = pat.search(query, i)
                if not m:
                    out.append(query[i:])
                    break
                out.append(query[i:m.start()])
                start = m.end() - 1  # position of the opening '('
                arg = self._extract_balanced_arg(query, start + 1)
                end = start + 1 + len(arg) + 1

                has_comma = False
                depth = 0
                in_quote = None
                for ch in arg:
                    if in_quote:
                        if ch == in_quote:
                            in_quote = None
                    else:
                        if ch in ("'", '"', '`'):
                            in_quote = ch
                        elif ch == '(':
                            depth += 1
                        elif ch == ')':
                            depth -= 1
                        elif ch == ',' and depth == 0:
                            has_comma = True
                            break

                if not has_comma:
                    out.append(f"CAST({arg.strip()} AS STRING)")
                else:
                    out.append(query[m.start():end])
                i = end
            return "".join(out)

        sql = _fix_date_format_missing_format(sql)

        # ── PERCENTILE_APPROX(col, p[, acc]) → PERCENTILE_CONT(p) WITHIN GROUP ─────
        def _rewrite_percentile_approx(query: str) -> str:
            pat = re.compile(r'\bPERCENTILE_APPROX\s*\(', re.IGNORECASE)
            i = 0
            out = []
            while True:
                m = pat.search(query, i)
                if not m:
                    out.append(query[i:])
                    break
                out.append(query[i:m.start()])
                start = m.end() - 1
                arg = self._extract_balanced_arg(query, start + 1)
                end = start + 1 + len(arg) + 1

                parts = self._split_top_level_commas(arg)
                if len(parts) >= 2:
                    col = parts[0].strip()
                    pct = parts[1].strip()
                    out.append(f"PERCENTILE_CONT({pct}) WITHIN GROUP (ORDER BY {col})")
                else:
                    out.append(query[m.start():end])
                i = end
            return "".join(out)

        sql = _rewrite_percentile_approx(sql)

        # ── QUALIFY rewrite for ROW_NUMBER = 1 (full SELECT shape) ──────────
        def _rewrite_qualify_full(query: str) -> str:
            pat = re.compile(
                r'^\s*SELECT\s+(?P<select>[\s\S]+?)\s+FROM\s+(?P<from>[\s\S]+?)\s+'
                r'QUALIFY\s+(?:ROW_NUMBER|RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\((?P<over>[\s\S]+?)\)\s*=\s*(?P<n>\d+)\s*;?\s*$',
                re.IGNORECASE,
            )
            m = pat.match(query)
            if not m:
                return query
            return (
                "SELECT *\nFROM (\n"
                f"  SELECT {m.group('select')},\n"
                f"    ROW_NUMBER() OVER ({m.group('over')}) AS _rn\n"
                f"  FROM {m.group('from')}\n"
                ") _qualify_wrap\nWHERE _rn = {m.group('n')}"
            )
        sql = _rewrite_qualify_full(sql)

        # ── Column / DDL type name normalisation ─────────────────────────────
        sql = re.sub(r'\bNUMBER\b(?!\s*\()', 'DECIMAL', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bFLOAT4\b', 'DOUBLE', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bFLOAT8\b', 'DOUBLE', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bDOUBLE\s+PRECISION\b', 'DOUBLE', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bDECFLOAT\b', 'DECIMAL(38, 18)', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bVARBINARY\b', 'BINARY', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bBYTEINT\b', 'TINYINT', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bTIMESTAMP_LTZ\b', 'TIMESTAMP', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bDATETIME\b(?!\s*\()', 'TIMESTAMP', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bBOOL\b', 'BOOLEAN', sql, flags=re.IGNORECASE)

        # INTEGER → INT (stand-alone DDL contexts)
        sql = re.sub(r'\bINTEGER\b', 'INT', sql, flags=re.IGNORECASE)

        # ── MINUS → EXCEPT (residual) ────────────────────────────────────────
        sql = re.sub(r'\bMINUS\b', 'EXCEPT', sql, flags=re.IGNORECASE)

        # ── Snowflake $n positional parameters → :param_n ────────────────────
        sql = re.sub(r'\$(\d+)\b', r':param_\1', sql)

        # ── Trailing comma cleanup ────────────────────────────────────────────
        sql = re.sub(r',\s*\n\s*FROM\b', '\nFROM', sql, flags=re.IGNORECASE)
        sql = re.sub(r',\s*\)', ')', sql)

        # ── DATE_ADD(d, -N) → date_sub(d, N) ─────────────────────────────────
        sql = re.sub(
            r'\bdate_add\s*\(\s*([^,]+?)\s*,\s*(-\d+)\s*\)',
            lambda m: f"date_sub({m.group(1)}, {m.group(2)[1:]})",
            sql, flags=re.IGNORECASE,
        )

        # ── Normalize QUALIFY patterns that Databricks does not support ───────
        # Strip QUALIFY + note if it survived
        sql = re.sub(
            r'\bQUALIFY\s+(?:ROW_NUMBER|RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\([^)]+\)\s*=\s*\d+',
            '/* TODO: QUALIFY clause removed; wrap query in subquery and filter on rn = N */',
            sql, flags=re.IGNORECASE,
        )

        # ── ISNULL(x) → x IS NULL ────────────────────────────────────────────
        sql = re.sub(r'\bISNULL\s*\(([^)]+)\)', r'\1 IS NULL', sql, flags=re.IGNORECASE)

        # ── IS TRUE / IS FALSE normalisation ─────────────────────────────────
        sql = re.sub(r'\bIS\s+TRUE\b', '= true', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bIS\s+FALSE\b', '= false', sql, flags=re.IGNORECASE)

        # ── ARRAY_CONSTRUCT_COMPACT closing paren ─────────────────────────────
        # We added an extra open paren when rewriting; close it.
        def _close_array_compact(q: str) -> str:
            search = 'array_compact(ARRAY('
            idx = 0
            while True:
                pos = q.lower().find(search.lower(), idx)
                if pos == -1:
                    break
                inner_start = pos + len(search) - 1  # at the '(' of ARRAY(
                arg = self._extract_balanced_arg(q, inner_start + 1)
                end = inner_start + 1 + len(arg) + 1  # after ARRAY(...)
                if end < len(q) and q[end] != ')':
                    q = q[:end] + ')' + q[end:]
                idx = end + 1
            return q
        sql = _close_array_compact(sql)

        # ── Raw-string normalization (Snowflake $$...$$) should already be done
        # but catch any stragglers.
        sql = re.sub(r'\$\$(.*?)\$\$', lambda m: "'" + m.group(1).replace("'", "\\'") + "'", sql, flags=re.DOTALL)

        # Final safety rails
        sql = self._enforce_spark_sql_safety(sql)

        sql = _restore_jinja(sql, jinja_map)
        return sql

    # ── _enforce_spark_sql_safety ────────────────────────────────────────────

    @staticmethod
    def _enforce_spark_sql_safety(sql: str) -> str:
        """Final normalisation pass — mirrors the BQ engine's safety layer."""

        # COUNT_IF → SUM(CASE WHEN ... THEN 1 ELSE 0 END)
        def _rewrite_count_if(query: str) -> str:
            pat = re.compile(r'\bCOUNT_IF\s*\(', re.IGNORECASE)
            idx = 0
            while True:
                m = pat.search(query, idx)
                if not m:
                    break
                start = m.end() - 1
                depth, end = 0, -1
                for i in range(start, len(query)):
                    if query[i] == '(':
                        depth += 1
                    elif query[i] == ')':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                if end == -1:
                    idx = m.end()
                    continue
                cond = query[start + 1:end].strip()
                replacement = f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)"
                query = query[:m.start()] + replacement + query[end + 1:]
                idx = m.start() + len(replacement)
            return query
        sql = _rewrite_count_if(sql)

        # Trailing commas before FROM / closing paren
        sql = re.sub(r',\s+(FROM\b)', r' \1', sql, flags=re.IGNORECASE)
        sql = re.sub(r',\s*\)', ')', sql)

        # Normalize double-quoted strings to single-quoted (Databricks prefers single)
        sql = re.sub(r'(\bTHEN\s+)"([^"\n]*)"', r"\1'\2'", sql, flags=re.IGNORECASE)
        sql = re.sub(r'(\bELSE\s+)"([^"\n]*)"', r"\1'\2'", sql, flags=re.IGNORECASE)
        sql = re.sub(r'([=<>]\s*)"([^"\n]*)"', r"\1'\2'", sql)

        # Normalize ARRAY_CONTAINS to canonical form with COALESCE
        sql = re.sub(r'\bARRAY_CONTAINS\s*\(', 'array_contains(', sql, flags=re.IGNORECASE)
        sql = re.sub(
            r'(?<!COALESCE\()\barray_contains\s*\(([^,]+?),\s*([^\)]+?)\)',
            r'COALESCE(array_contains(\1, \2), false)',
            sql, flags=re.IGNORECASE,
        )

        # Remove Snowflake-specific SET session params that are no-ops in Databricks
        sql = re.sub(
            r"ALTER\s+SESSION\s+SET\s+\w+\s*=\s*[^;]+;?",
            r'/* NOTE: ALTER SESSION SET removed (Snowflake-specific) */',
            sql, flags=re.IGNORECASE,
        )

        return sql


# ── Loader helper ─────────────────────────────────────────────────────────────

def load_snowflake_rules_from_csv(csv_path: str):
    """
    Load rules_list and edge_cases from the combined Snowflake↔Databricks CSV.

    CSV columns: Type, Snowflake, Databricks_Equivalent

    Returns
    -------
    rules_list : list[dict]
        All rows (used for CSV-derived prefix rewrites)
    edge_cases : list[dict]
        Rows from the EDGE CASES / FUNCTION / DATE / JSON / SQL sections
        that contain targeted function-level rewrites.
    """
    import csv

    rules_list: List[Dict] = []
    edge_cases: List[Dict] = []

    edge_case_types = {
        'EDGE CASES', 'FUNCTION', 'DATE', 'JSON', 'SQL',
    }

    with open(csv_path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            clean = {k: v.strip() for k, v in row.items() if v}
            rules_list.append(clean)
            if clean.get('Type', '').upper() in edge_case_types:
                sf = clean.get('Snowflake', '')
                db = clean.get('Databricks_Equivalent', '')
                if sf and db and sf != db:
                    edge_cases.append({'snowflake': sf, 'databricks': db})

    return rules_list, edge_cases


def build_engine_from_csv(csv_path: str) -> SnowflakeRuleEngine:
    """Convenience: load CSV and return a ready-to-use engine."""
    rules_list, edge_cases = load_snowflake_rules_from_csv(csv_path)
    return SnowflakeRuleEngine(rules_list, edge_cases)


# ════════════════════════════════════════════════════════════════════════════
# Runnable entry point – converts a given Snowflake SQL to Databricks SQL
# ════════════════════════════════════════════════════════════════════════════
def convert_snowflake_to_databricks(snowflake_sql: str) -> str:
    """
    High-level function to convert a Snowflake SQL string to Databricks SQL.
    Uses only built‑in rules (no external CSV).
    """
    engine = SnowflakeRuleEngine(rules_list=[], edge_cases=[])
    sql = engine.apply_pre_ast_translation(snowflake_sql)
    sql = engine.apply_rules(sql)
    sql = engine.apply_function_translation(sql)
    return sql

