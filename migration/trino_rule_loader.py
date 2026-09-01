from __future__ import annotations

import csv
import os
import re
from typing import Dict, List


BASE_DIR = os.path.dirname(__file__)
DEFAULT_XLSX_PATH = os.getenv("TRINO_RULES_XLSX_PATH") or ""
DEFAULT_CSV_PATH = os.path.join(BASE_DIR, "conversion_rules_trino.csv")


class TrinoRuleEngine:
    """
    Deterministic rule-based Trino -> Databricks SQL translation engine.

    Mirrors the structure of the BigQuery RuleEngine:
      - apply_pre_ast_translation: string-level fixes that must happen
        before any AST-based parsing (safe to run even without sqlglot).
      - apply_rules: main regex-based rewrite pass, ordered by priority.
      - apply_function_translation: second pass for type names, literal
        quoting, and any trailing cleanup.

    Priority order (highest wins):
      10 — critical exact rewrites (data types, hashing, error handling)
       9 — common function remaps (array, string, math)
       8 — syntax / operator fixes
       7 — lower-priority / context-dependent
     100 — CSV-derived simple prefix rewrites (see load_trino_rules)
    """

    def __init__(self, rules_list: List[Dict[str, str]] | None = None) -> None:
        self.rules_list = rules_list or []
        self.pattern_rules = self._build_pattern_rules(self.rules_list)

    # ------------------------------------------------------------------
    # Pre-AST string-level fixes
    # ------------------------------------------------------------------
    @staticmethod
    def apply_pre_ast_translation(sql: str) -> str:
        """
        String-based replacements that should happen before any AST-based
        parsing, because they involve syntax shapes that generic SQL
        parsers often choke on (e.g. CROSS JOIN UNNEST ... WITH ORDINALITY).
        """
        sql = TrinoRuleEngine._rewrite_cross_join_unnest_static(sql)
        return sql

    @staticmethod
    def _rewrite_cross_join_unnest_static(sql: str) -> str:
        # CROSS JOIN UNNEST(arr) WITH ORDINALITY -> LATERAL VIEW POSEXPLODE(arr)
        sql = re.sub(
            r"\bCROSS\s+JOIN\s+UNNEST\s*\(\s*([^)]+?)\s*\)\s+WITH\s+ORDINALITY\b",
            r"LATERAL VIEW POSEXPLODE(\1)",
            sql,
            flags=re.IGNORECASE,
        )
        # UNNEST(arr) WITH ORDINALITY -> POSEXPLODE(arr)
        sql = re.sub(
            r"\bUNNEST\s*\(\s*([^)]+?)\s*\)\s+WITH\s+ORDINALITY\b",
            r"POSEXPLODE(\1)",
            sql,
            flags=re.IGNORECASE,
        )
        # CROSS JOIN UNNEST(arr) AS t(item) -> LATERAL VIEW EXPLODE(arr) t AS item
        sql = re.sub(
            r"\bCROSS\s+JOIN\s+UNNEST\s*\(\s*([^)]+?)\s*\)\s+AS\s+(\w+)\s*\(\s*(\w+)\s*\)",
            r"LATERAL VIEW EXPLODE(\1) \2 AS \3",
            sql,
            flags=re.IGNORECASE,
        )
        # CROSS JOIN UNNEST(arr) -> LATERAL VIEW EXPLODE(arr)
        sql = re.sub(
            r"\bCROSS\s+JOIN\s+UNNEST\s*\(\s*([^)]+?)\s*\)",
            r"LATERAL VIEW EXPLODE(\1)",
            sql,
            flags=re.IGNORECASE,
        )
        # Standalone UNNEST(arr) -> EXPLODE(arr)
        sql = re.sub(r"\bUNNEST\s*\(\s*([^)]+?)\s*\)", r"EXPLODE(\1)", sql, flags=re.IGNORECASE)
        return sql

    # ------------------------------------------------------------------
    # Pattern rule construction
    # ------------------------------------------------------------------
    def _build_pattern_rules(self, rules_list: List[Dict[str, str]]) -> List[Dict]:
        builtins: List[Dict] = [

            # ── DATA TYPES ───────────────────────────────────────────────
            {'pattern': r'\bINTEGER\b', 'replacement': 'INT', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bREAL\b', 'replacement': 'FLOAT', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bNUMERIC\s*\(([^)]*)\)', 'replacement': r'DECIMAL(\1)', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bNUMERIC\b(?!\s*\()', 'replacement': 'DECIMAL', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bVARCHAR\s*\([^)]*\)', 'replacement': 'STRING', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bVARCHAR\b', 'replacement': 'STRING', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bVARBINARY\b', 'replacement': 'BINARY', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bTIMESTAMP\s+WITH\s+TIME\s+ZONE\b', 'replacement': 'TIMESTAMP', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'(?<=\s)TIME\s*\(\s*\d+\s*\)', 'replacement': 'STRING', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'(?<=\s)TIME\b(?!\s*\(|\s+ZONE|\s+WITH)', 'replacement': 'STRING', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bJSON\b(?!\s*\()', 'replacement': 'STRING', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bUUID\b', 'replacement': 'STRING', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bIPADDRESS\b', 'replacement': 'STRING', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bHYPERLOGLOG\b', 'replacement': 'BINARY', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bP4HYPERLOGLOG\b', 'replacement': 'BINARY', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bQDIGEST\s*<[^>]+>', 'replacement': 'BINARY', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bTDIGEST\b', 'replacement': 'BINARY', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bDOUBLE\b', 'replacement': 'DOUBLE', 'flags': re.IGNORECASE, 'priority': 8},

            # ── COMPLEX TYPES ────────────────────────────────────────────
            {'pattern': r'\bARRAY\s*\(\s*INTEGER\s*\)', 'replacement': 'ARRAY<INT>', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bARRAY\s*\(\s*VARCHAR\s*\)', 'replacement': 'ARRAY<STRING>', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bMAP\s*\(\s*VARCHAR\s*,\s*VARCHAR\s*\)', 'replacement': 'MAP<STRING, STRING>', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bMAP\s*\(\s*VARCHAR\s*,\s*INTEGER\s*\)', 'replacement': 'MAP<STRING, INT>', 'flags': re.IGNORECASE, 'priority': 10},
            # ROW(a INT, b VARCHAR) -> STRUCT<a:INT, b:STRING> (best-effort field mapping)
            {
                'pattern': r'\bROW\s*<([^>]+)>',
                'replacement': lambda m: 'STRUCT<' + re.sub(
                    r'(\b\w+)\s+((?:ARRAY|ROW|VARCHAR|INTEGER|BIGINT|DOUBLE|REAL|BOOLEAN|DATE|TIMESTAMP|DECIMAL)[^,>]*)',
                    r'\1:\2',
                    m.group(1),
                ) + '>',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ── ERROR HANDLING ───────────────────────────────────────────
            {
                'pattern': r'\bTRY\s*\(\s*CAST\s*\(([^)]+?)\s+AS\s+([^)]+?)\)\s*\)',
                'replacement': r'TRY_CAST(\1 AS \2)',
                'flags': re.IGNORECASE, 'priority': 11,
            },
            {
                'pattern': r'\bTRY_CAST\s*\(([^)]+?)\s+AS\s+INTEGER\)',
                'replacement': r'TRY_CAST(\1 AS INT)',
                'flags': re.IGNORECASE, 'priority': 11,
            },
            {
                'pattern': r'\bTRY\s*\(([^()]+)\)',
                'replacement': r'TRY_CAST(\1)',
                'flags': re.IGNORECASE, 'priority': 8,
                'note': 'Generic TRY(expr) fallback — verify semantics manually',
            },

            # ── HASH / ENCODING ──────────────────────────────────────────
            {'pattern': r'\bTO_HEX\s*\(', 'replacement': 'hex(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bFROM_HEX\s*\(', 'replacement': 'unhex(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bTO_BASE64\s*\(', 'replacement': 'base64(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bFROM_BASE64\s*\(', 'replacement': 'unbase64(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bTO_BASE64URL\s*\(', 'replacement': 'base64(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bFROM_BASE64URL\s*\(', 'replacement': 'unbase64(', 'flags': re.IGNORECASE, 'priority': 9},
            {
                'pattern': r'\bMD5\s*\(\s*TO_UTF8\s*\(([^()]+)\)\s*\)',
                'replacement': r'md5(\1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bSHA256\s*\(\s*TO_UTF8\s*\(([^()]+)\)\s*\)',
                'replacement': r'sha2(\1, 256)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bSHA512\s*\(\s*TO_UTF8\s*\(([^()]+)\)\s*\)',
                'replacement': r'sha2(\1, 512)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {'pattern': r'\bTO_UTF8\s*\(', 'replacement': '(', 'flags': re.IGNORECASE, 'priority': 7,
             'note': 'Databricks strings are already UTF-8; drop the wrapper'},
            {'pattern': r'\bFROM_UTF8\s*\(', 'replacement': '(', 'flags': re.IGNORECASE, 'priority': 7},

            # ── ARRAY FUNCTIONS ──────────────────────────────────────────
            {'pattern': r'\bCARDINALITY\s*\(', 'replacement': 'SIZE(', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bARRAY_DISTINCT\s*\(', 'replacement': 'array_distinct(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bARRAY_UNION\s*\(', 'replacement': 'array_union(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bARRAY_INTERSECT\s*\(', 'replacement': 'array_intersect(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bARRAY_EXCEPT\s*\(', 'replacement': 'array_except(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bARRAY_JOIN\s*\(', 'replacement': 'array_join(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bARRAY_SORT\s*\(', 'replacement': 'array_sort(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bARRAY_MAX\s*\(', 'replacement': 'array_max(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bARRAY_MIN\s*\(', 'replacement': 'array_min(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bARRAY_POSITION\s*\(([^,]+),\s*([^)]+)\)', 'replacement': r'array_position(\1, \2)', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bCONTAINS\s*\(([^,]+),\s*([^)]+)\)', 'replacement': r'array_contains(\1, \2)', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bFLATTEN\s*\(', 'replacement': 'flatten(', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bREVERSE\s*\(', 'replacement': 'reverse(', 'flags': re.IGNORECASE, 'priority': 7},
            {'pattern': r'\bSEQUENCE\s*\(', 'replacement': 'sequence(', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bZIP\s*\(', 'replacement': 'arrays_zip(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bTRANSFORM\s*\(', 'replacement': 'transform(', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bFILTER\s*\(\s*([A-Za-z_][\w.]*)\s*,', 'replacement': r'filter(\1,', 'flags': re.IGNORECASE, 'priority': 9,
             'note': 'Only rewrites array FILTER(arr, lambda); leave FILTER (WHERE ...) untouched'},
            {'pattern': r'\bREDUCE\s*\(', 'replacement': 'aggregate(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bELEMENT_AT\s*\(', 'replacement': 'element_at(', 'flags': re.IGNORECASE, 'priority': 8},

            # ── JSON FUNCTIONS ───────────────────────────────────────────
            {'pattern': r'\bJSON_EXTRACT_SCALAR\s*\(([^,]+),\s*([^)]+)\)', 'replacement': r'get_json_object(\1, \2)', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bJSON_EXTRACT\s*\(([^,]+),\s*([^)]+)\)', 'replacement': r'get_json_object(\1, \2)', 'flags': re.IGNORECASE, 'priority': 10},
            {'pattern': r'\bJSON_FORMAT\s*\(', 'replacement': 'to_json(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bJSON_PARSE\s*\(', 'replacement': 'from_json(', 'flags': re.IGNORECASE, 'priority': 9,
             'note': 'from_json requires a schema second argument in Databricks — verify manually'},
            {'pattern': r'\bJSON_ARRAY_LENGTH\s*\(', 'replacement': 'json_array_length(', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bIS_JSON_SCALAR\s*\(([^)]+)\)', 'replacement': r'\1 IS NOT NULL /* TODO: IS_JSON_SCALAR has no direct Databricks equivalent */', 'flags': re.IGNORECASE, 'priority': 8},

            # ── STRING FUNCTIONS ─────────────────────────────────────────
            {'pattern': r'\bSTRPOS\s*\(([^,]+),\s*([^)]+)\)', 'replacement': r'LOCATE(\2, \1)', 'flags': re.IGNORECASE, 'priority': 9,
             'note': 'Trino STRPOS(str, sub) args reversed vs Databricks LOCATE(sub, str)'},
            {'pattern': r'\bSTARTS_WITH\s*\(', 'replacement': 'STARTSWITH(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bLENGTH\s*\(', 'replacement': 'LENGTH(', 'flags': re.IGNORECASE, 'priority': 7},
            {'pattern': r'\bREGEXP_LIKE\s*\(([^,]+),\s*([^)]+)\)', 'replacement': r'\1 RLIKE \2', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bREGEXP_EXTRACT_ALL\s*\(', 'replacement': 'regexp_extract_all(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bREGEXP_EXTRACT\s*\(', 'replacement': 'regexp_extract(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bREGEXP_REPLACE\s*\(', 'replacement': 'regexp_replace(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bREGEXP_SPLIT\s*\(', 'replacement': 'split(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bSPLIT_PART\s*\(([^,]+),\s*([^,]+),\s*([^)]+)\)', 'replacement': r'split(\1, \2)[\3 - 1]', 'flags': re.IGNORECASE, 'priority': 9,
             'note': 'Trino SPLIT_PART is 1-indexed; adjusted to 0-indexed array access'},
            {'pattern': r'\bLEVENSHTEIN_DISTANCE\s*\(', 'replacement': 'levenshtein(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bCODEPOINT\s*\(', 'replacement': 'ascii(', 'flags': re.IGNORECASE, 'priority': 8,
             'note': 'Approximate — CODEPOINT supports full Unicode, ascii() is ASCII-only'},

            # ── ARRAY INDEXING (0/1-based conversion) ─────────────────────
            # Trino/SQL arrays are 1-indexed already — same as Databricks, so
            # no offset conversion needed here (unlike BigQuery's 0-indexed access).

            # ── AGGREGATE / WINDOW FUNCTIONS ───────────────────────────────
            {'pattern': r'\bAPPROX_DISTINCT\s*\(', 'replacement': 'approx_count_distinct(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bAPPROX_PERCENTILE\s*\(', 'replacement': 'percentile_approx(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bBOOL_AND\s*\(', 'replacement': 'BOOL_AND(', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bBOOL_OR\s*\(', 'replacement': 'BOOL_OR(', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bARBITRARY\s*\(', 'replacement': 'FIRST(', 'flags': re.IGNORECASE, 'priority': 9},

            # ── ERROR HANDLING (2) ─────────────────────────────────────────
            {'pattern': r'\bTRY_CAST\s*\(', 'replacement': 'TRY_CAST(', 'flags': re.IGNORECASE, 'priority': 7},

            # ── MATH ──────────────────────────────────────────────────────
            {'pattern': r'\bTRUNCATE\s*\(', 'replacement': 'TRUNCATE(', 'flags': re.IGNORECASE, 'priority': 7},
            {'pattern': r'\bPOWER\s*\(', 'replacement': 'POWER(', 'flags': re.IGNORECASE, 'priority': 7},
            {'pattern': r'\bMOD\s*\(([^,]+),\s*([^)]+)\)', 'replacement': r'\1 % \2', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bINFINITY\s*\(\s*\)', 'replacement': "CAST('Infinity' AS DOUBLE)", 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bIS_NAN\s*\(', 'replacement': 'isnan(', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bIS_INFINITE\s*\(([^)]+)\)', 'replacement': r'(\1 = CAST(\'Infinity\' AS DOUBLE) OR \1 = CAST(\'-Infinity\' AS DOUBLE))', 'flags': re.IGNORECASE, 'priority': 9},

            # ── DATE / TIME (kept for direct calls; EXTRACT/DATE_ADD/DIFF
            #    are also handled by dedicated methods below) ──────────────
            {'pattern': r'\bCURRENT_DATE\b(?!\s*\()', 'replacement': 'CURRENT_DATE()', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bCURRENT_TIMESTAMP\b(?!\s*\()', 'replacement': 'CURRENT_TIMESTAMP()', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bCURRENT_TIME\b(?!\s*\()', 'replacement': 'CURRENT_TIMESTAMP()', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bFROM_UNIXTIME\s*\(', 'replacement': 'TIMESTAMP_SECONDS(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bTO_UNIXTIME\s*\(', 'replacement': 'unix_timestamp(', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bNOW\s*\(\s*\)', 'replacement': 'CURRENT_TIMESTAMP()', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bLOCALTIME\b(?!\s*\()', 'replacement': 'CURRENT_TIMESTAMP()', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bDATE_FORMAT\s*\(', 'replacement': 'date_format(', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bDATE_PARSE\s*\(([^,]+),\s*([^)]+)\)', 'replacement': r'to_timestamp(\1, \2)', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bLAST_DAY_OF_MONTH\s*\(', 'replacement': 'last_day(', 'flags': re.IGNORECASE, 'priority': 9},

            # ── CONDITIONAL ───────────────────────────────────────────────
            {'pattern': r'\bIF\s*\(', 'replacement': 'IF(', 'flags': re.IGNORECASE, 'priority': 6},
            {'pattern': r'\bCOALESCE\s*\(', 'replacement': 'COALESCE(', 'flags': re.IGNORECASE, 'priority': 6},
            {'pattern': r'\bNULLIF\s*\(', 'replacement': 'NULLIF(', 'flags': re.IGNORECASE, 'priority': 6},

            # ── DDL / SYNTAX ──────────────────────────────────────────────
            {
                'pattern': r'(?i)\b(CREATE\s+(?:OR\s+REPLACE\s+)?)TEMP(?:ORARY)?\s+TABLE\b',
                'replacement': r'\1TEMPORARY VIEW',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {'pattern': r'\bWITH\s+NO\s+DATA\b', 'replacement': '', 'flags': re.IGNORECASE, 'priority': 9},
            {'pattern': r'\bTABLESAMPLE\s+BERNOULLI\s*\(', 'replacement': 'TABLESAMPLE(', 'flags': re.IGNORECASE, 'priority': 8},
            {'pattern': r'\bTABLESAMPLE\s+SYSTEM\s*\(', 'replacement': 'TABLESAMPLE(', 'flags': re.IGNORECASE, 'priority': 8},
        ]

        builtins.sort(key=lambda r: r.get('priority', 0), reverse=True)

        # ── Integrate CSV-derived rules at priority 100 ─────────────────
        # Only apply simple, unambiguous prefix rewrites: FUNC( -> newfunc(
        # to avoid corrupting anything with spaces or nested constructs
        # (e.g. EXTRACT(YEAR FROM dt) is handled by dedicated methods, not here).
        for row in rules_list:
            trino_syntax = (row.get("trino_syntax") or "").strip()
            dbx_syntax = (row.get("databricks_sql_syntax") or "").strip()
            if not trino_syntax or not dbx_syntax or trino_syntax == dbx_syntax:
                continue

            # Skip multi-word / EXTRACT / DATE_ADD / DATE_DIFF shapes —
            # those need argument reordering handled by dedicated regexes above.
            if ' ' in trino_syntax or trino_syntax.upper().startswith(('EXTRACT', 'DATE_ADD', 'DATE_DIFF', 'CURRENT_')):
                continue

            fn_match = re.match(r'^(\w+)\s*\(', trino_syntax)
            if not fn_match:
                continue
            fn_name = fn_match.group(1)

            # Only apply for simple one-layer function mappings on the target side.
            repl_match = re.match(r'^(\w+)\s*\(', dbx_syntax)
            if not repl_match:
                continue

            pattern = r'\b' + re.escape(fn_name) + r'\s*\('
            replacement = repl_match.group(1) + '('
            builtins.append({
                'pattern': pattern,
                'replacement': replacement,
                'flags': re.IGNORECASE,
                'priority': 100,
                'source': 'csv_functions',
            })

        # ── Filter hardcoded builtins where CSV explicitly overrides them ──
        csv_base_fns = set()
        for rule in builtins:
            if rule.get('source') == 'csv_functions':
                m = re.search(r'^[\\^]*b?([A-Za-z0-9_]+)', rule['pattern'], re.IGNORECASE)
                if m:
                    csv_base_fns.add(m.group(1).upper())

        final_builtins = []
        for rule in builtins:
            if rule.get('source') == 'csv_functions':
                final_builtins.append(rule)
                continue
            m = re.search(r'^[\\^]*b?([A-Za-z0-9_]+)', rule['pattern'], re.IGNORECASE)
            if m and m.group(1).upper() in csv_base_fns:
                continue
            final_builtins.append(rule)
        builtins = final_builtins

        builtins.sort(key=lambda r: r.get('priority', 0), reverse=True)
        return builtins

    # ------------------------------------------------------------------
    # EXTRACT / DATE_ADD / DATE_DIFF (argument-order-sensitive, kept as
    # dedicated methods rather than simple regex table entries)
    # ------------------------------------------------------------------
    @staticmethod
    def _rewrite_extract(sql: str) -> str:
        extract_map = {
            "YEAR": "YEAR",
            "QUARTER": "QUARTER",
            "MONTH": "MONTH",
            "WEEK": "WEEKOFYEAR",
            "DAY": "DAY",
            "DAY_OF_MONTH": "DAY",
            "DAY_OF_YEAR": "DAYOFYEAR",
            "DOY": "DAYOFYEAR",
            "HOUR": "HOUR",
            "MINUTE": "MINUTE",
            "SECOND": "SECOND",
            "TIMEZONE_HOUR": "0",
            "TIMEZONE_MINUTE": "0",
        }
        for part, fn in extract_map.items():
            if fn in ("0",):
                sql = re.sub(
                    rf"\bEXTRACT\s*\(\s*{part}\s+FROM\s+([^)]+)\)",
                    "0 /* TODO: TIMEZONE_HOUR/MINUTE not supported in Databricks */",
                    sql,
                    flags=re.IGNORECASE,
                )
                continue
            sql = re.sub(
                rf"\bEXTRACT\s*\(\s*{part}\s+FROM\s+([^)]+)\)",
                rf"{fn}(\1)",
                sql,
                flags=re.IGNORECASE,
            )
        # DAY_OF_WEEK is Trino's ISO weekday (1=Monday..7=Sunday)
        sql = re.sub(
            r"\bEXTRACT\s*\(\s*DAY_OF_WEEK\s+FROM\s+([^)]+)\)",
            r"EXTRACT(DAYOFWEEK_ISO FROM \1)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"\bEXTRACT\s*\(\s*DOW\s+FROM\s+([^)]+)\)",
            r"EXTRACT(DAYOFWEEK_ISO FROM \1)",
            sql,
            flags=re.IGNORECASE,
        )
        return sql

    @staticmethod
    def _rewrite_date_add(sql: str) -> str:
        # DATE_ADD('day', n, date) -> DATE_ADD(date, n)
        sql = re.sub(
            r"\bDATE_ADD\s*\(\s*'day'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"DATE_ADD(\2, \1)",
            sql,
            flags=re.IGNORECASE,
        )
        # DATE_ADD('week', n, date) -> DATE_ADD(date, n * 7)
        sql = re.sub(
            r"\bDATE_ADD\s*\(\s*'week'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"DATE_ADD(\2, (\1) * 7)",
            sql,
            flags=re.IGNORECASE,
        )
        # DATE_ADD('month', n, date) -> ADD_MONTHS(date, n)
        sql = re.sub(
            r"\bDATE_ADD\s*\(\s*'month'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"ADD_MONTHS(\2, \1)",
            sql,
            flags=re.IGNORECASE,
        )
        # DATE_ADD('quarter', n, date) -> ADD_MONTHS(date, n * 3)
        sql = re.sub(
            r"\bDATE_ADD\s*\(\s*'quarter'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"ADD_MONTHS(\2, (\1) * 3)",
            sql,
            flags=re.IGNORECASE,
        )
        # DATE_ADD('year', n, date) -> ADD_MONTHS(date, n * 12)
        sql = re.sub(
            r"\bDATE_ADD\s*\(\s*'year'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"ADD_MONTHS(\2, (\1) * 12)",
            sql,
            flags=re.IGNORECASE,
        )
        # DATE_ADD('hour'/'minute'/'second', n, ts) -> ts + INTERVAL n UNIT
        for unit, dbx_unit in (("hour", "HOURS"), ("minute", "MINUTES"), ("second", "SECONDS")):
            sql = re.sub(
                rf"\bDATE_ADD\s*\(\s*'{unit}'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
                rf"(\2 + INTERVAL \1 {dbx_unit})",
                sql,
                flags=re.IGNORECASE,
            )
        return sql

    @staticmethod
    def _rewrite_date_diff(sql: str) -> str:
        # DATE_DIFF('day', d1, d2) -> DATEDIFF(d2, d1)
        sql = re.sub(
            r"\bDATE_DIFF\s*\(\s*'day'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"DATEDIFF(\2, \1)",
            sql,
            flags=re.IGNORECASE,
        )
        # DATE_DIFF('week', d1, d2) -> FLOOR(DATEDIFF(d2, d1) / 7)
        sql = re.sub(
            r"\bDATE_DIFF\s*\(\s*'week'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"FLOOR(DATEDIFF(\2, \1) / 7)",
            sql,
            flags=re.IGNORECASE,
        )
        # DATE_DIFF('month', d1, d2) -> CAST(MONTHS_BETWEEN(d2, d1) AS INT)
        sql = re.sub(
            r"\bDATE_DIFF\s*\(\s*'month'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"CAST(MONTHS_BETWEEN(\2, \1) AS INT)",
            sql,
            flags=re.IGNORECASE,
        )
        # DATE_DIFF('quarter', d1, d2) -> CAST(MONTHS_BETWEEN(d2, d1) / 3 AS INT)
        sql = re.sub(
            r"\bDATE_DIFF\s*\(\s*'quarter'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"CAST(MONTHS_BETWEEN(\2, \1) / 3 AS INT)",
            sql,
            flags=re.IGNORECASE,
        )
        # DATE_DIFF('year', d1, d2) -> FLOOR(MONTHS_BETWEEN(d2, d1) / 12)
        sql = re.sub(
            r"\bDATE_DIFF\s*\(\s*'year'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"FLOOR(MONTHS_BETWEEN(\2, \1) / 12)",
            sql,
            flags=re.IGNORECASE,
        )
        # DATE_DIFF('hour'/'minute'/'second', ts1, ts2) -> TIMESTAMPDIFF(UNIT, ts1, ts2)
        for unit in ("hour", "minute", "second"):
            sql = re.sub(
                rf"\bDATE_DIFF\s*\(\s*'{unit}'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
                rf"TIMESTAMPDIFF({unit.upper()}, \1, \2)",
                sql,
                flags=re.IGNORECASE,
            )
        return sql

    @staticmethod
    def _rewrite_date_trunc(sql: str) -> str:
        # Trino: DATE_TRUNC('unit', date) — same argument order as Databricks.
        # Normalize unit casing/quoting only.
        sql = re.sub(
            r"\bDATE_TRUNC\s*\(\s*'(day|week|month|quarter|year|hour|minute|second)'\s*,\s*([^)]+)\)",
            lambda m: f"DATE_TRUNC('{m.group(1).upper()}', {m.group(2)})",
            sql,
            flags=re.IGNORECASE,
        )
        return sql

    # ------------------------------------------------------------------
    # Main entry points
    # ------------------------------------------------------------------
    def apply_rules(self, sql: str) -> str:
        sql = self._rewrite_cross_join_unnest_static(sql)
        sql = self._rewrite_extract(sql)
        sql = self._rewrite_date_add(sql)
        sql = self._rewrite_date_diff(sql)
        sql = self._rewrite_date_trunc(sql)

        for rule in self.pattern_rules:
            pattern = rule['pattern']
            replacement = rule['replacement']
            flags = rule.get('flags', 0)
            try:
                sql = re.sub(pattern, replacement, sql, flags=flags)
            except Exception:
                continue

        return sql

    def apply_function_translation(self, sql: str) -> str:
        """Second pass: type names, literal cleanup, trailing fixes."""
        # ROW(...) constructor without field names -> STRUCT(...)
        sql = re.sub(r'\bROW\s*\(', 'STRUCT(', sql, flags=re.IGNORECASE)

        # Double-quoted identifiers used as string literals (rare in Trino
        # SQL but occasionally emitted) -> single-quoted.
        sql = re.sub(r'(\bTHEN\s+)"([^"\n]*)"', r"\1'\2'", sql, flags=re.IGNORECASE)
        sql = re.sub(r'(\bELSE\s+)"([^"\n]*)"', r"\1'\2'", sql, flags=re.IGNORECASE)
        sql = re.sub(r'([=<>]\s*)"([^"\n]*)"', r"\1'\2'", sql)

        # Trailing comma cleanup
        sql = re.sub(r',\s*\n\s*FROM\b', '\nFROM', sql, flags=re.IGNORECASE)
        sql = re.sub(r',\s*\)', ')', sql)

        return sql


# ----------------------------------------------------------------------
# Rule loading
# ----------------------------------------------------------------------
def load_trino_rules(path: str | None = None) -> List[Dict[str, str]]:
    """
    Load Trino -> Databricks mapping rules.

    Order of preference:
      1. An .xlsx workbook path from TRINO_RULES_XLSX_PATH (optional —
         safe to leave unset; falls through automatically if missing).
      2. conversion_rules_trino.csv sitting next to this file.
    """
    path = path or DEFAULT_XLSX_PATH
    if path and os.path.exists(path):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            headers = [str(h).strip() if h else "" for h in rows[0]]
            rules = []
            for row in rows[1:]:
                item = {headers[i]: (str(row[i]).strip() if row[i] is not None else "") for i in range(len(headers))}
                if item.get("trino_syntax") and item.get("databricks_sql_syntax"):
                    rules.append(item)
            wb.close()
            return rules
        except Exception:
            pass

    if os.path.exists(DEFAULT_CSV_PATH):
        with open(DEFAULT_CSV_PATH, newline="", encoding="utf-8") as fh:
            return [dict(row) for row in csv.DictReader(fh)]
    return []


def build_trino_engine(path: str | None = None) -> TrinoRuleEngine:
    return TrinoRuleEngine(load_trino_rules(path))
