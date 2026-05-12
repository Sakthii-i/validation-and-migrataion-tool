import sqlglot
import re
from typing import Tuple, Optional, List
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    """Result of SQL validation."""
    is_valid: bool
    error_message: Optional[str] = None
    error_type: Optional[str] = None
    line_number: Optional[int] = None
    suggestions: List[str] = field(default_factory=list)


class SQLValidator:
    """Validates Databricks SQL syntax using sqlglot."""

    # sqlglot dialects to try in order (databricks first, spark as fallback)
    _DIALECTS = ["databricks", "spark"]

    def __init__(self):
        self.common_error_handlers = {
            "PARSE_ERROR": self._handle_parse_error,
            "TABLE_OR_VIEW_NOT_FOUND": self._handle_missing_table,
            "UNRESOLVED_COLUMN": self._handle_missing_column,
            "DATATYPE_MISMATCH": self._handle_type_mismatch,
            "RESIDUAL_BQ_SYNTAX": self._handle_residual_bq,
        }

    def validate(self, sql: str) -> ValidationResult:
        """
        Validate Databricks SQL syntax.
        Tries 'databricks' dialect first, falls back to 'spark'.
        Skips validation entirely if Jinja/dbt template syntax is detected,
        as sqlglot cannot parse {{ }}, {% %}, or {# #} blocks.
        Also skips validation for SQL scripting blocks (DECLARE, IF/THEN, etc.)
        and multi-statement transactions, as sqlglot may not parse these.
        """
        import re

        # Skip validation for SQL containing Jinja/dbt templates
        if re.search(r'\{\{|\{%|\{#', sql):
            return ValidationResult(
                is_valid=True,
                suggestions=["Contains Jinja/dbt templates — validation skipped; manual review recommended"],
            )

        # Skip validation for SQL scripting blocks (2026 GA)
        scripting_patterns = [
            r'\bIF\s+.+\s+THEN\b',
            r'\bBEGIN\b\s*$',
            r'\bLOOP\b',
            r'\bWHILE\s+.+\s+DO\b',
            r'\bFOR\s+\w+\s+IN\b',
        ]
        for pat in scripting_patterns:
            if re.search(pat, sql, re.IGNORECASE | re.MULTILINE):
                return ValidationResult(
                    is_valid=True,
                    suggestions=["Contains SQL scripting constructs — validation skipped; Databricks 2026 supports SQL scripting natively"],
                )

        # Skip validation for multi-statement transactions
        if re.search(r'\bBEGIN\s+TRANSACTION\b', sql, re.IGNORECASE):
            return ValidationResult(
                is_valid=True,
                suggestions=["Contains multi-statement transaction — validation skipped; Databricks 2026 supports transactions natively"],
            )

        last_error: Optional[Exception] = None

        for dialect in self._DIALECTS:
            try:
                # Use parse() instead of parse_one() so multi-statement
                # SQL is fully validated, not silently truncated.
                trees = sqlglot.parse(sql, read=dialect)
                if not any(t is not None for t in trees):
                    raise sqlglot.errors.ParseError("Empty parse result")
                # Syntax is valid — now check for untranslated BigQuery patterns
                residual = self._check_residual_bigquery(sql)
                if residual:
                    return ValidationResult(
                        is_valid=False,
                        error_message=f"Untranslated BigQuery syntax detected: {'; '.join(residual)}",
                        error_type="RESIDUAL_BQ_SYNTAX",
                    )
                return ValidationResult(is_valid=True)
            except sqlglot.errors.ParseError as e:
                last_error = e
            except Exception as e:
                last_error = e

        error_msg = self._strip_ansi(str(last_error))
        return ValidationResult(
            is_valid=False,
            error_message=error_msg,
            error_type="PARSE_ERROR",
            line_number=self._extract_line_number(error_msg),
        )

    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI escape sequences from a string."""
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)

    # ── BigQuery-only patterns that should never survive translation ──────────
    # Each tuple: (compiled regex, human-readable description)
    _RESIDUAL_BQ_PATTERNS = [
        (re.compile(r'\bUNNEST\s*\(', re.IGNORECASE),
         "UNNEST() — should be LATERAL VIEW EXPLODE or ARRAY_CONTAINS"),
        (re.compile(r'\bIN\s+LATERAL\s+VIEW\b', re.IGNORECASE),
         "Invalid IN + LATERAL VIEW usage — use IN (...) for static lists, array_contains(arr, val) for array membership, or proper LATERAL VIEW in FROM"),
        (re.compile(r'\bWHERE\b[\s\S]*\bLATERAL\s+VIEW\b', re.IGNORECASE),
         "LATERAL VIEW found in WHERE clause — LATERAL VIEW must be in FROM"),
        (re.compile(r'\bARRAY_LENGTH\s*\(', re.IGNORECASE),
         "ARRAY_LENGTH() — should be SIZE()"),
        (re.compile(r'\bIF\s*\(\s*[^)]+,\s*[^)]+,\s*[^)]+\)', re.IGNORECASE),
         None),  # IF() is valid in both — skip
        (re.compile(r'\bSAFE_DIVIDE\s*\(', re.IGNORECASE),
         "SAFE_DIVIDE() — should be TRY_DIVIDE()"),
        (re.compile(r'\bSAFE_MULTIPLY\s*\(', re.IGNORECASE),
         "SAFE_MULTIPLY() — should be TRY_MULTIPLY()"),
        (re.compile(r'\bSAFE_ADD\s*\(', re.IGNORECASE),
         "SAFE_ADD() — should be TRY_ADD()"),
        (re.compile(r'\bSAFE_SUBTRACT\s*\(', re.IGNORECASE),
         "SAFE_SUBTRACT() — should be TRY_SUBTRACT()"),
        (re.compile(r'\bSAFE_NEGATE\s*\(', re.IGNORECASE),
         "SAFE_NEGATE() — should be TRY_SUBTRACT(0, x)"),
        (re.compile(r'\bSAFE_CAST\s*\(', re.IGNORECASE),
         "SAFE_CAST() — should be TRY_CAST()"),
        (re.compile(r'\bFARM_FINGERPRINT\s*\(', re.IGNORECASE),
         "FARM_FINGERPRINT() — should be hash()"),
        (re.compile(r'\bGENERATE_DATE_ARRAY\s*\(', re.IGNORECASE),
         "GENERATE_DATE_ARRAY() — should be sequence()"),
        (re.compile(r'\bGENERATE_TIMESTAMP_ARRAY\s*\(', re.IGNORECASE),
         "GENERATE_TIMESTAMP_ARRAY() — should be sequence()"),
        (re.compile(r'\bGENERATE_ARRAY\s*\(', re.IGNORECASE),
         "GENERATE_ARRAY() — should be sequence()"),
        (re.compile(r'\bDATE_TRUNC\s*\(\s*\w+\s*,\s*(DAY|WEEK|MONTH|QUARTER|YEAR)\b', re.IGNORECASE),
         "DATE_TRUNC(expr, part) — BQ arg order; Databricks uses DATE_TRUNC(part, expr)"),
        (re.compile(r'\bFORMAT_DATE\s*\(', re.IGNORECASE),
         "FORMAT_DATE() — should be DATE_FORMAT()"),
        (re.compile(r'\bPARSE_DATE\s*\(', re.IGNORECASE),
         "PARSE_DATE() — should be TO_DATE()"),
        (re.compile(r'\bPARSE_TIMESTAMP\s*\(', re.IGNORECASE),
         "PARSE_TIMESTAMP() — should be TO_TIMESTAMP()"),
        (re.compile(r'\bFORMAT_TIMESTAMP\s*\(', re.IGNORECASE),
         "FORMAT_TIMESTAMP() — should be DATE_FORMAT()"),
        (re.compile(r'\bJSON_EXTRACT\s*\(', re.IGNORECASE),
         "JSON_EXTRACT() — should be get_json_object()"),
        (re.compile(r'\bJSON_EXTRACT_SCALAR\s*\(', re.IGNORECASE),
         "JSON_EXTRACT_SCALAR() — should be get_json_object()"),
        (re.compile(r'\bJSON_EXTRACT_ARRAY\s*\(', re.IGNORECASE),
         "JSON_EXTRACT_ARRAY() — should be from_json()"),
        (re.compile(r'\bTO_HEX\s*\(', re.IGNORECASE),
         "TO_HEX() — should be hex()"),
        (re.compile(r'\bFROM_HEX\s*\(', re.IGNORECASE),
         "FROM_HEX() — should be unhex()"),
        (re.compile(r'\bTO_BASE64\s*\(', re.IGNORECASE),
         "TO_BASE64() — should be base64()"),
        (re.compile(r'\bFROM_BASE64\s*\(', re.IGNORECASE),
         "FROM_BASE64() — should be unbase64()"),
        (re.compile(r'\bNET\.\w+\s*\(', re.IGNORECASE),
         "NET.* functions — not available in Databricks"),
        (re.compile(r'\bSTRUCT\s*\([^)]*\bAS\b', re.IGNORECASE),
         "STRUCT(x AS field) — Databricks uses NAMED_STRUCT or STRUCT<field:type>"),
        (re.compile(r'\bARRAY_AGG\s*\(', re.IGNORECASE),
         "ARRAY_AGG() — should be COLLECT_LIST() or COLLECT_SET()"),
        (re.compile(r'\bSTRING_AGG\s*\(', re.IGNORECASE),
         "STRING_AGG() — should be CONCAT_WS + COLLECT_LIST"),
        (re.compile(r'\bCOUNT_IF\s*\(', re.IGNORECASE),
         "COUNT_IF() detected — use SUM(CASE WHEN ... THEN 1 ELSE 0 END) for compatibility"),
        (re.compile(r'\bQUALIFY\b', re.IGNORECASE),
         "QUALIFY detected — rewrite using subquery/CTE and outer WHERE rn = N"),
        (re.compile(r'^\s*DECLARE\b', re.IGNORECASE | re.MULTILINE),
         "DECLARE detected — dbt models should use Jinja variables ({% set var = ... %})"),
        (re.compile(r'\bset_sql_header\s*\(', re.IGNORECASE),
         "set_sql_header() detected — remove for Databricks/dbt-spark"),
        (re.compile(r'\b[A-Za-z_][A-Za-z0-9_]*\.get\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*0\s*\)', re.IGNORECASE),
         "obj.get(field, 0) detected — use CASE WHEN size(obj.field) > 0 THEN obj.field[0] ELSE NULL END"),
        (re.compile(r'\bIN\s+UNNEST\s*\(', re.IGNORECASE),
         "IN UNNEST() — should be array_contains()"),
        (re.compile(r'\[OFFSET\s*\(\d+\)\]', re.IGNORECASE),
         "[OFFSET(n)] — should be [n]"),
        (re.compile(r'\[SAFE_OFFSET\s*\(', re.IGNORECASE),
         "[SAFE_OFFSET(n)] — should be get(arr, n)"),
        (re.compile(r'\[ORDINAL\s*\(\d+\)\]', re.IGNORECASE),
         "[ORDINAL(n)] — should be [n-1]"),
        (re.compile(r'\bINT64\b', re.IGNORECASE),
         "INT64 — should be BIGINT"),
        (re.compile(r'\bFLOAT64\b', re.IGNORECASE),
         "FLOAT64 — should be DOUBLE"),
        (re.compile(r'\bBOOL\b(?!\s*EAN)', re.IGNORECASE),
         "BOOL — should be BOOLEAN"),
    ]

    def _check_residual_bigquery(self, sql: str) -> List[str]:
        """
        Scan translated SQL for BigQuery-only patterns that should have been
        converted. Returns list of human-readable descriptions of residual patterns.
        """
        found = []
        for pattern, description in self._RESIDUAL_BQ_PATTERNS:
            if description is None:
                continue
            if pattern.search(sql):
                found.append(description)
        return found

    def _extract_line_number(self, error: str) -> Optional[int]:
        import re
        match = re.search(r'line (\d+)', error)
        return int(match.group(1)) if match else None

    def _handle_parse_error(self, error: str) -> str:
        if "expecting" in error.lower():
            return "Check for missing parentheses, commas, or keywords"
        return "Verify SQL syntax is compatible with Databricks"

    def _handle_missing_table(self, _error: str) -> str:
        return "Verify table exists and is referenced with the correct catalog.schema.table path"

    def _handle_missing_column(self, _error: str) -> str:
        return "Check column names and aliases"

    def _handle_type_mismatch(self, _error: str) -> str:
        return "Check data type compatibility between BQ and Databricks types"

    def _handle_residual_bq(self, error: str) -> str:
        return f"Deterministic translation incomplete — {error}"

    def suggest_fixes(self, result: ValidationResult) -> List[str]:
        """Generate human-readable suggestions for fixing errors."""
        suggestions: List[str] = []

        if result.error_type in self.common_error_handlers:
            suggestions.append(
                self.common_error_handlers[result.error_type](result.error_message or "")
            )

        msg = result.error_message or ""
        if "UNNEST" in msg.upper():
            suggestions.append("Use LATERAL VIEW EXPLODE instead of UNNEST")
        if "LATERAL VIEW" in msg.upper() and "WHERE" in msg.upper():
            suggestions.append("Move LATERAL VIEW to FROM; use array_contains(arr, value) for boolean membership checks")
        if "QUALIFY" in msg.upper():
            suggestions.append("Rewrite QUALIFY ROW_NUMBER() to a subquery/CTE and filter in the outer WHERE")
        if "COUNT_IF" in msg.upper():
            suggestions.append("Use SUM(CASE WHEN condition THEN 1 ELSE 0 END) for COUNT_IF compatibility")
        if "STRUCT" in msg.upper():
            suggestions.append("STRUCT field syntax requires colons: STRUCT<field:type>")
        if "INTERVAL" in msg.upper():
            suggestions.append("Use plural interval units: INTERVAL 1 DAYS, not INTERVAL 1 DAY")
        if "ARRAY_UNION" in msg.upper():
            suggestions.append("Use concat() to preserve duplicates; ARRAY_UNION deduplicates")
        if "COLLATE" in msg.upper():
            suggestions.append("Use Databricks 2026 collation names: UTF8_LCASE (case-insensitive), UTF8_BINARY (case-sensitive)")
        if "TRANSACTION" in msg.upper():
            suggestions.append("Multi-statement transactions are supported in Databricks 2026: BEGIN TRANSACTION; ... COMMIT TRANSACTION;")
        if "DECLARE" in msg.upper() or "SCRIPTING" in msg.upper():
            suggestions.append("SQL scripting (DECLARE, IF/THEN, LOOP) is GA in Databricks 2026")

        return suggestions


class LLMFixerPrompt:
    """Generate prompts for LLM to fix validation errors."""

    @staticmethod
    def create_fix_prompt(original_bq: str, translated: str, error: str) -> str:
        """Full-query fix prompt (used when the whole query fails validation)."""
        return (
            "MISSION: Translate this BigQuery SQL into valid Databricks SQL (Spark SQL) and dbt template code (if applicable) that will compile and run correctly in Databricks 2026.\n\n"
            "CRITICAL RULES:\n"
            "1. Databricks SQL does not support JavaScript UDFs. Replace them using built-in functions, SQL UDF, or Python UDF appropriate for Databricks.\n"
            "2. For JSON parsing, use supported Databricks built-ins such as from_json(json, schema), parse_json(json), or get_json_object(json, path) with explicit schemas.\n"
            "2a. If the JSON schema is not fixed or varies across records, infer schema with schema_of_json() or schema_of_json_agg() when possible.\n"
            "2b. For from_json(), use permissive parsing options so missing or extra fields become NULL instead of errors.\n"
            "2c. Ensure nested and optional JSON fields are modeled as nullable in the resulting schema.\n"
            "2d. For values extracted via get_json_object(), normalize JSON text first (trim whitespace/extraneous chars, normalize quotes/escapes, use try_parse_json() or regexp_replace() to handle invalid/unexpected characters) before from_json() or further parsing.\n"
            "2e. Prefer this normalization flow for get_json_object() outputs: TRIM()/regexp_replace() cleanup → try_parse_json() to VARIANT (NULL if invalid) → from_json(json, schema, map('mode','PERMISSIVE')) when typed structs/arrays are needed → variant_get() for safe nested extraction.\n"
            "2f. After normalization, apply additional JSON parsing and explode/unnest logic as needed.\n"
            "3. dbt template syntax (macros/jinja) should use dbt-databricks adapter conventions and avoid BigQuery-specific functions.\n"
            "4. dbt ref(), source(), and config() usage should be respected and preserved where applicable.\n"
            "5. If the translated SQL would fail in Databricks SQL or dbt compilation, revise until it is valid.\n"
            "6. When in doubt, prefer built-ins over UDFs.\n"
            "7. If a BigQuery function has no Databricks equivalent, suggest a valid alternative.\n"
            "8. Return ONLY the translated dbt/Databricks SQL code.\n\n"
            "Original BigQuery SQL:\n"
            f"```sql\n{original_bq}\n```\n\n"
            "Current (broken) Databricks SQL:\n"
            f"```sql\n{translated}\n```\n\n"
            f"Error: {error}\n\n"
            "Detailed Rules:\n"
            "- NEVER revert or modify logic that has already been converted by the deterministic rules. Only focus on what was missed.\n"
            "- DO NOT undo edge case translations derived from the learnings CSV. If it is valid Databricks SQL, leave it alone.\n"
            "- For dbt config `partitions`, assign the list variable directly (e.g., `partitions = your_list_variable`) instead of wrapping it in `[{{ ... }}]` string formatting.\n"
            "- CRITICAL: If the error is 'Error tokenizing' or 'Unexpected token' near quotes, check for malformed BigQuery triple-quotes or escaped quotes (e.g., \\\"\\\"\\\" or \\'\\'\\'). Normalize these to standard Databricks single-quoted strings: 'content'.\n"
            "- Ensure every open quote is properly closed.\n"
            "- ARRAY of STRUCTS: [STRUCT('a' AS k, value)] -> array(struct('a' as key_col, value))\n"
            "- SUBSTR: 0-based index adjust to 1-based\n"
            "- ARRAY_LENGTH(arr) -> size(arr)\n"
            "- TO_JSON_STRING(x) -> to_json(x)\n"
            "- TO_JSON_STRING(struct, [options]) -> to_json(struct) (no equivalent ignoreNullFields option)\n"
            "- REGEXP_REPLACE(x, r'[^0-9]+', '') -> regexp_replace(x, '[^0-9]+', '') (remove r prefix and escape)\n"
            "- REGEXP_EXTRACT_ALL(x, r'\"(\\w+)\":null') -> regexp_extract_all(x, '\"(\\\\w+)\":null')\n"
            "- DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY) -> date_sub(current_date(), 14)\n"
            "- EXTRACT(ISOWEEK FROM date) -> weekofyear(date)\n"
            "- EXTRACT(WEEK FROM date) -> floor(datediff(date, date_trunc('year', date)) / 7) + 1\n"
            "- EXTRACT(YEAR FROM date) -> year(date)\n"
            "- SAFE_CAST(x AS FLOAT64) -> try_cast(x as double)\n"
            "- ARRAY_LENGTH(REGEXP_EXTRACT_ALL(...)) -> size(regexp_extract_all(...))\n"
            "- DATE(timestamp) -> to_date(timestamp)\n"
            "- ARRAY of STRUCT null handling: Databricks may drop or stringify differently unless explicitly handled.\n"
            "- IMPORTANT NUANCES (Semantic differences to keep in mind):\n"
            "  1. JSON ordering differences: Spark to_json output key order may differ from BigQuery TO_JSON_STRING.\n"
            "  2. WEEK numbering mismatch: Spark uses ISO weeks, BigQuery uses calendar-based logic; numbering remains fundamentally not identical.\n"
            "  3. STRUCT field ordering: Spark may reorder fields in JSON output.\n"
            "  4. FLOAT rounding differences: ROUND(AVG(...),0) behaves slightly differently across engines.\n"
            "- Preserve placeholder comments like /*__MLC_0__*/ and /*__SLC_0__*/ exactly if present\n"
            "- Do NOT include derived metric columns (e.g. digit_count, char_count) in GROUP BY\n"
            "- AVG() over nullable columns: use COALESCE or filter NULLs to match BQ behaviour\n"
            "- Target is Databricks 2026 (Runtime 18.0+) with SQL scripting GA, collations, and multi-statement transactions\n"
            "- Collations: map 'und:ci' → UTF8_LCASE, 'und:cs' → UTF8_BINARY\n"
            "- ST_* geospatial functions are now natively supported (lowercase)\n"
            "- Parameter markers (:param, ?) should be preserved as-is\n"
            "- Function safety check: ensure every function exists in Databricks SQL with correct argument order\n"
            "- DATE_TRUNC must be DATE_TRUNC('UNIT', expr). Never output DATE_TRUNC(expr, 'UNIT')\n"
            "- Valid DATE_TRUNC units only: DAY, WEEK, MONTH, QUARTER, YEAR, HOUR, MINUTE, SECOND\n"
            "- Ensure semantic equivalence, not just syntax equivalence\n"
            "- Never replace column identifiers with quoted placeholder strings (e.g., 'date', 'column')\n"
            "\n"
            "Provide ONLY the corrected Databricks SQL. No explanation, no markdown."
        )

    @staticmethod
    def create_chunk_fix_prompt(chunk_sql: str, original_chunk_sql: str, error: str) -> str:
        """
        Chunk-level fix prompt.
        chunk_sql  — the specific CTE/subquery body that failed
        original_chunk_sql — the original pre-translated SQL for context
        error — the validation error message
        """
        return (
            "MISSION: Translate this BigQuery SQL chunk into valid Databricks SQL (Spark SQL) and dbt template code (if applicable) that will compile and run correctly in Databricks 2026.\n\n"
            "CRITICAL RULES:\n"
            "1. Databricks SQL does not support JavaScript UDFs. Replace them using built-in functions, SQL UDF, or Python UDF appropriate for Databricks.\n"
            "2. For JSON parsing, use supported Databricks built-ins such as from_json(json, schema), parse_json(json), or get_json_object(json, path) with explicit schemas.\n"
            "2a. If the JSON schema is not fixed or varies across records, infer schema with schema_of_json() or schema_of_json_agg() when possible.\n"
            "2b. For from_json(), use permissive parsing options so missing or extra fields become NULL instead of errors.\n"
            "2c. Ensure nested and optional JSON fields are modeled as nullable in the resulting schema.\n"
            "2d. For values extracted via get_json_object(), normalize JSON text first (trim whitespace/extraneous chars, normalize quotes/escapes, use try_parse_json() or regexp_replace() to handle invalid/unexpected characters) before from_json() or further parsing.\n"
            "2e. Prefer this normalization flow for get_json_object() outputs: TRIM()/regexp_replace() cleanup → try_parse_json() to VARIANT (NULL if invalid) → from_json(json, schema, map('mode','PERMISSIVE')) when typed structs/arrays are needed → variant_get() for safe nested extraction.\n"
            "2f. After normalization, apply additional JSON parsing and explode/unnest logic as needed.\n"
            "3. dbt template syntax (macros/jinja) should use dbt-databricks adapter conventions and avoid BigQuery-specific functions.\n"
            "4. dbt ref(), source(), and config() usage should be respected and preserved where applicable.\n"
            "5. If the translated SQL would fail in Databricks SQL or dbt compilation, revise until it is valid.\n"
            "6. When in doubt, prefer built-ins over UDFs.\n"
            "7. If a BigQuery function has no Databricks equivalent, suggest a valid alternative.\n"
            "8. Return ONLY the translated dbt/Databricks SQL code.\n\n"
            "This is a chunk (CTE/subquery body) that is part of a larger query.\n\n"
            "Original BigQuery SQL for this specific chunk:\n"
            f"```sql\n{original_chunk_sql}\n```\n\n"
            "The specific partially-translated chunk that needs fixing (Databricks SQL):\n"
            f"```sql\n{chunk_sql}\n```\n\n"
            f"Validation error: {error}\n\n"
            "Detailed Rules:\n"
            "- NEVER revert or modify logic that has already been converted by the deterministic rules. Only focus on what was missed.\n"
            "- DO NOT undo edge case translations derived from the learnings CSV. If it is valid Databricks SQL, leave it alone.\n"
            "- STRUCT() is natively supported in Databricks. Do NOT convert it to named_struct().\n"
            "- For dbt config `partitions`, assign the list variable directly (e.g., `partitions = your_list_variable`) instead of wrapping it in `[{{ ... }}]` string formatting.\n"
            "- ARRAY of STRUCTS: [STRUCT('a' AS k, value)] -> array(struct('a' as key_col, value))\n"
            "- SUBSTR: 0-based index adjust to 1-based\n"
            "- ARRAY_LENGTH(arr) -> size(arr)\n"
            "- TO_JSON_STRING(x) -> to_json(x)\n"
            "- TO_JSON_STRING(struct, [options]) -> to_json(struct) (no equivalent ignoreNullFields option)\n"
            "- REGEXP_REPLACE(x, r'[^0-9]+', '') -> regexp_replace(x, '[^0-9]+', '') (remove r prefix and escape)\n"
            "- REGEXP_EXTRACT_ALL(x, r'\"(\\w+)\":null') -> regexp_extract_all(x, '\"(\\\\w+)\":null')\n"
            "- DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY) -> date_sub(current_date(), 14)\n"
            "- EXTRACT(ISOWEEK FROM date) -> weekofyear(date)\n"
            "- EXTRACT(WEEK FROM date) -> floor(datediff(date, date_trunc('year', date)) / 7) + 1\n"
            "- EXTRACT(YEAR FROM date) -> year(date)\n"
            "- SAFE_CAST(x AS FLOAT64) -> try_cast(x as double)\n"
            "- ARRAY_LENGTH(REGEXP_EXTRACT_ALL(...)) -> size(regexp_extract_all(...))\n"
            "- DATE(timestamp) -> to_date(timestamp)\n"
            "- ARRAY of STRUCT null handling: Databricks may drop or stringify differently unless explicitly handled.\n"
            "- IMPORTANT NUANCES (Semantic differences to keep in mind):\n"
            "  1. JSON ordering differences: Spark to_json output key order may differ from BigQuery TO_JSON_STRING.\n"
            "  2. WEEK numbering mismatch: Spark uses ISO weeks, BigQuery uses calendar-based logic; numbering remains fundamentally not identical.\n"
            "  3. STRUCT field ordering: Spark may reorder fields in JSON output.\n"
            "  4. FLOAT rounding differences: ROUND(AVG(...),0) behaves slightly differently across engines.\n"
            "- Preserve placeholder comments like /*__MLC_0__*/ and /*__SLC_0__*/ exactly if present\n"
            "- Rewrite QUALIFY ROW_NUMBER() to subquery/CTE + outer WHERE rn = N\n"
            "- SELECT * EXCEPT (...) is supported natively — do NOT rewrite\n"
            "- JOIN ... USING (...) is supported natively — do NOT rewrite\n"
            "- unix_date(), unix_seconds(), timestamp_seconds() etc. are native — do NOT use DATEDIFF/FROM_UNIXTIME\n"
            "- ARRAY_CONCAT should become concat(), not ARRAY_UNION\n"
            "- Do NOT include derived metric columns (e.g. digit_count, char_count) in GROUP BY \u2014 they fragment aggregation\n"
            "- AVG() over nullable columns: wrap with COALESCE or use AVG(NULLIF(col, 0)) to handle NULLs correctly\n"
            "- Target is Databricks 2026 (Runtime 18.0+) with SQL scripting GA, collations, and multi-statement transactions\n"
            "- Collations: map 'und:ci' → UTF8_LCASE, 'und:cs' → UTF8_BINARY\n"              "- STRUCT() is natively supported in Databricks. Do NOT convert it to named_struct().\n"            "- ST_* geospatial functions are now natively supported (lowercase)\n"
            "- Avoid DECLARE in dbt model SQL output; use Jinja {% set var = ... %} for variables\n"
            "- Parameter markers (:param, ?) should be preserved as-is\n"
            "- Function safety check: ensure every function exists in Databricks SQL with correct argument order\n"
            "- DATE_TRUNC must be DATE_TRUNC('UNIT', expr). Never output DATE_TRUNC(expr, 'UNIT')\n"
            "- Valid DATE_TRUNC units only: DAY, WEEK, MONTH, QUARTER, YEAR, HOUR, MINUTE, SECOND\n"
            "- Ensure semantic equivalence, not just syntax equivalence\n"
            "- Never replace column identifiers with quoted placeholder strings (e.g., 'date', 'column')\n"
            "\n"
            "Provide ONLY the corrected chunk SQL. No explanation, no markdown fences."
        )

    @staticmethod
    def create_residual_fix_prompt(chunk_sql: str, original_chunk_sql: str, residual_patterns: str) -> str:
        """
        Targeted prompt for fixing only the untranslated BigQuery patterns.
        Instead of asking Claude to fix the whole query, we tell it exactly
        which BQ functions/syntax survived and what each should become.
        """
        return (
            "MISSION: Convert this BigQuery SQL into valid Databricks SQL (Spark SQL) and dbt template code (if applicable) that will compile and run correctly in Databricks 2026.\n\n"
            "CRITICAL RULES:\n"
            "1. Databricks SQL does not support JavaScript UDFs. Replace them using built-in functions, SQL UDF, or Python UDF appropriate for Databricks.\n"
            "2. For JSON parsing, use supported Databricks built-ins such as from_json(json, schema), parse_json(json), or get_json_object(json, path) with explicit schemas.\n"
            "2a. If the JSON schema is not fixed or varies across records, infer schema with schema_of_json() or schema_of_json_agg() when possible.\n"
            "2b. For from_json(), use permissive parsing options so missing or extra fields become NULL instead of errors.\n"
            "2c. Ensure nested and optional JSON fields are modeled as nullable in the resulting schema.\n"
            "2d. For values extracted via get_json_object(), normalize JSON text first (trim whitespace/extraneous chars, normalize quotes/escapes, use try_parse_json() or regexp_replace() to handle invalid/unexpected characters) before from_json() or further parsing.\n"
            "2e. Prefer this normalization flow for get_json_object() outputs: TRIM()/regexp_replace() cleanup → try_parse_json() to VARIANT (NULL if invalid) → from_json(json, schema, map('mode','PERMISSIVE')) when typed structs/arrays are needed → variant_get() for safe nested extraction.\n"
            "2f. After normalization, apply additional JSON parsing and explode/unnest logic as needed.\n"
            "3. dbt template syntax (macros/jinja) should use dbt-databricks adapter conventions and avoid BigQuery-specific functions.\n"
            "4. dbt ref(), source(), and config() usage should be respected and preserved where applicable.\n"
            "5. If the translated SQL would fail in Databricks SQL or dbt compilation, revise until it is valid.\n"
            "6. When in doubt, prefer built-ins over UDFs.\n"
            "7. If a BigQuery function has no Databricks equivalent, suggest a valid alternative.\n"
            "8. Return ONLY the translated dbt/Databricks SQL code.\n\n"
            "The following Databricks SQL still contains untranslated BigQuery syntax.\n"
            "Convert ONLY the listed BigQuery patterns to their Databricks equivalents.\n"
            "CRITICAL: The current SQL already incorporates edge case mappings and custom metrics from deterministic rules.\n"
            "Do NOT change or undo anything else in the query. Preserve all existing valid Databricks syntax exactly.\n\n"
            "Original BigQuery SQL for this specific chunk:\n"
            f"```sql\n{original_chunk_sql}\n```\n\n"
            "Current partially-translated SQL (fix ONLY the listed patterns):\n"
            f"```sql\n{chunk_sql}\n```\n\n"
            f"Untranslated BigQuery patterns found:\n{residual_patterns}\n\n"
            "Preserve placeholder comments like /*__MLC_0__*/ and /*__SLC_0__*/ exactly if present.\n\n"
            "Key Databricks equivalents:\n"
            "- UNNEST(arr) AS x → use LATERAL VIEW EXPLODE(arr) _t AS x in FROM only\n"
            "- col IN UNNEST(arr) → COALESCE(array_contains(arr, col), false)\n"
            "- col NOT IN UNNEST(arr) → NOT COALESCE(array_contains(arr, col), FALSE)\n"
            "- NEVER write \"value IN LATERAL VIEW EXPLODE(...)\"; that shape is invalid SQL\n"
            "- LEFT JOIN UNNEST → LATERAL VIEW OUTER EXPLODE\n"
            "- ARRAY_LENGTH(arr) → SIZE(arr)\n"
            "- SAFE_DIVIDE(a,b) → TRY_DIVIDE(a,b)\n"
            "- SAFE_CAST(x AS type) → TRY_CAST(x AS type)\n"
            "- JSON_EXTRACT_SCALAR(col, path) → get_json_object(col, path)\n"
            "- JSON_EXTRACT_ARRAY(col, path) → from_json(get_json_object(col, path), 'array<variant>')\n"
            "- FORMAT_DATE(fmt, d) → DATE_FORMAT(d, fmt)  (note: args swap)\n"
            "- PARSE_DATE(fmt, s) → TO_DATE(s, fmt)  (note: args swap)\n"
            "- FORMAT_TIMESTAMP(fmt, ts) → DATE_FORMAT(ts, fmt)\n"
            "- PARSE_TIMESTAMP(fmt, s) → TO_TIMESTAMP(s, fmt)\n"
            "- GENERATE_DATE_ARRAY(start, end) → sequence(start, end, INTERVAL 1 DAY)\n"
            "- GENERATE_ARRAY(start, end) → sequence(start, end)\n"
            "- ARRAY_AGG(x) → COLLECT_LIST(x)\n"
            "- STRING_AGG(x, sep) → CONCAT_WS(sep, COLLECT_LIST(x))\n"
            "- TO_HEX(x) → hex(x), FROM_HEX(x) → unhex(x)\n"
            "- TO_BASE64(x) → base64(x), FROM_BASE64(x) → unbase64(x)\n"
            "- INT64 → BIGINT, FLOAT64 → DOUBLE, BOOL → BOOLEAN\n"
            "- [OFFSET(n)] → [n], [SAFE_OFFSET(n)] → get(arr, n)\n"
            "- STRUCT() is natively supported in Databricks. Do NOT convert it to named_struct().\n"
            "- For dbt config `partitions`, assign the list variable directly (e.g., `partitions = your_list_variable`) instead of wrapping it in `[{{ ... }}]` string formatting.\n"
            "- DO NOT modify any translations that are already valid Databricks syntax.\n"
            "- FARM_FINGERPRINT(x) → hash(x)  /* different algorithm */\n"
            "- DATE_TRUNC(expr, MONTH) → DATE_TRUNC('MONTH', expr)  (args swap)\n"
            "- DATE_TRUNC must be DATE_TRUNC('UNIT', expr). Never output DATE_TRUNC(expr, 'UNIT')\n"
            "- Valid DATE_TRUNC units only: DAY, WEEK, MONTH, QUARTER, YEAR, HOUR, MINUTE, SECOND\n"
            "- Ensure semantic equivalence, not just syntax equivalence\n"
            "- Never replace column identifiers with quoted placeholder strings (e.g., 'date', 'column')\n"
            "\n"
            "Provide ONLY the corrected SQL. No explanation, no markdown fences."
        )

    @staticmethod
    def create_proactive_migration_prompt(chunk_sql: str, original_chunk_sql: str) -> str:
        """
        Proactive prompt sent after deterministic rules have run.
        Asks Claude to finish migrating anything the rule engine missed,
        even if the SQL currently passes validation.
        """
        return (
            "MISSION: Translate this BigQuery SQL into valid Databricks SQL (Spark SQL) and dbt template code (if applicable) that will compile and run correctly in Databricks 2026.\n\n"
            "CRITICAL RULES:\n"
            "1. Databricks SQL does not support JavaScript UDFs. Replace them using built-in functions, SQL UDF, or Python UDF appropriate for Databricks.\n"
            "2. For JSON parsing, use supported Databricks built-ins such as from_json(json, schema), parse_json(json), or get_json_object(json, path) with explicit schemas.\n"
            "2a. If the JSON schema is not fixed or varies across records, infer schema with schema_of_json() or schema_of_json_agg() when possible.\n"
            "2b. For from_json(), use permissive parsing options so missing or extra fields become NULL instead of errors.\n"
            "2c. Ensure nested and optional JSON fields are modeled as nullable in the resulting schema.\n"
            "2d. For values extracted via get_json_object(), normalize JSON text first (trim whitespace/extraneous chars, normalize quotes/escapes, use try_parse_json() or regexp_replace() to handle invalid/unexpected characters) before from_json() or further parsing.\n"
            "2e. Prefer this normalization flow for get_json_object() outputs: TRIM()/regexp_replace() cleanup → try_parse_json() to VARIANT (NULL if invalid) → from_json(json, schema, map('mode','PERMISSIVE')) when typed structs/arrays are needed → variant_get() for safe nested extraction.\n"
            "2f. After normalization, apply additional JSON parsing and explode/unnest logic as needed.\n"
            "3. dbt template syntax (macros/jinja) should use dbt-databricks adapter conventions and avoid BigQuery-specific functions.\n"
            "4. dbt ref(), source(), and config() usage should be respected and preserved where applicable.\n"
            "5. If the translated SQL would fail in Databricks SQL or dbt compilation, revise until it is valid.\n"
            "6. When in doubt, prefer built-ins over UDFs.\n"
            "7. If a BigQuery function has no Databricks equivalent, suggest a valid alternative.\n"
            "8. Return ONLY the translated dbt/Databricks SQL code.\n\n"
            "You are a BigQuery → Databricks SQL migration expert.\n"
            "CRITICAL: The following SQL has already been heavily processed by a rigid rule engine.\n"
            "This rule engine has ALREADY implemented all learnings, edge cases, and deterministic transformations.\n"
            "You are strictly a fallback and must NOT touch valid code or make 'improvements'.\n"
            "DO NOT undo or rewrite logic that has been successfully translated. Leave all custom logic completely alone.\n"
            "Your ONLY job is to find and convert any remaining explicit BigQuery-specific syntax that the rules missed.\n"
            "DO NOT format, refactor, or 'clean up' the query. Return the exact same text if it is already valid.\n\n"
            "Original BigQuery SQL for this specific chunk:\n"
            f"```sql\n{original_chunk_sql}\n```\n\n"
            "Current partially-translated SQL (complete this migration to Databricks SQL):\n"
            f"```sql\n{chunk_sql}\n```\n\n"
            "Common patterns to look for and convert:\n"
            "- UNNEST(arr) AS x → use LATERAL VIEW EXPLODE(arr) _t AS x in FROM only\n"
            "- col IN UNNEST(arr) → COALESCE(array_contains(arr, col), false)\n"
            "- NEVER write \"value IN LATERAL VIEW EXPLODE(...)\"; that shape is invalid SQL\n"
            "- ARRAY_LENGTH(arr) → SIZE(arr)\n"
            "- SAFE_DIVIDE(a,b) → TRY_DIVIDE(a,b)\n"
            "- SAFE_CAST(x AS type) → TRY_CAST(x AS type)\n"
            "- SAFE_ADD/SUBTRACT/MULTIPLY/NEGATE → equivalent TRY_* forms\n"
            "- JSON_EXTRACT_SCALAR(col, path) → get_json_object(col, path)\n"
            "- JSON_EXTRACT_ARRAY(col, path) → from_json(get_json_object(col, path), 'array<variant>')\n"
            "- JSON_EXTRACT(col, path) → get_json_object(col, path)\n"
            "- FORMAT_DATE(fmt, d) → DATE_FORMAT(d, fmt)  [args swap]\n"
            "- PARSE_DATE(fmt, s) → TO_DATE(s, fmt)  [args swap]\n"
            "- FORMAT_TIMESTAMP(fmt, ts) → DATE_FORMAT(ts, fmt)\n"
            "- PARSE_TIMESTAMP(fmt, s) → TO_TIMESTAMP(s, fmt)\n"
            "- DATE_TRUNC(expr, MONTH) → DATE_TRUNC('MONTH', expr)  [args swap]\n"
            "- GENERATE_DATE_ARRAY(start, end) → sequence(start, end, INTERVAL 1 DAY)\n"
            "- GENERATE_ARRAY(start, end) → sequence(start, end)\n"
            "- ARRAY_AGG(x) → COLLECT_LIST(x)\n"
            "- STRING_AGG(x, sep) → CONCAT_WS(sep, COLLECT_LIST(x))\n"
            "- TO_HEX(x) → hex(x), FROM_HEX(x) → unhex(x)\n"
            "- TO_BASE64(x) → base64(x), FROM_BASE64(x) → unbase64(x)\n"
            "- INT64 → BIGINT, FLOAT64 → DOUBLE, BOOL → BOOLEAN\n"
            "- STRUCT() is natively supported in Databricks. Do NOT convert it to named_struct().\n"
            "- For dbt config `partitions`, assign the list variable directly (e.g., `partitions = your_list_variable`) instead of wrapping it in `[{{ ... }}]` string formatting.\n"
            "- DO NOT modify edge cases, metrics, or any code that has already been converted and is valid.\n"
            "- FARM_FINGERPRINT(x) → hash(x)\n"
            "- TIMESTAMP_DIFF(t1, t2, unit) → TIMESTAMPDIFF(unit, t2, t1)\n"
            "- DATE_DIFF(d1, d2, DAY) → DATEDIFF(d1, d2)\n"
            "- DATE_DIFF(d1, d2, MONTH) → CAST(FLOOR(MONTHS_BETWEEN(d1, d2)) AS INT)\n"
            "- DATE_DIFF(d1, d2, YEAR) → FLOOR(MONTHS_BETWEEN(d1, d2) / 12)\n"
            "- REGEXP_EXTRACT(str, re) → regexp_extract(str, re)\n"
            "- REGEXP_EXTRACT_ALL(str, re) → regexp_extract_all(str, re)\n"
            "- SUBSTR/SUBSTRING with 0-based index → adjust to 1-based\n"
            "- Backtick field access on STRUCT: `field` → .field\n"
            "- BQ backtick-quoted identifiers: `project.dataset.table` → use plain identifiers\n"
            "- [OFFSET(n)] / [SAFE_OFFSET(n)] → [n+1] / get(arr, n)\n"
            "- ANY_VALUE(x) → FIRST(x) or FIRST_VALUE(x) OVER (...)\n"
            "- COUNTIF(cond) → SUM(CASE WHEN cond THEN 1 ELSE 0 END)\n"
            "- APPROX_COUNT_DISTINCT → approx_count_distinct\n"
            "- ST_* geospatial: preserve as-is (natively supported in Databricks 2026)\n"
            "- COLLATE 'und:ci' → COLLATE 'UTF8_LCASE'; COLLATE 'und:cs' → COLLATE 'UTF8_BINARY'\n"
            "\n"
            "Rules:\n"
            "- If the SQL is already fully correct Databricks SQL, return it unchanged\n"
              "- DO NOT improve, refactor, or reformat the code. Your job is ONLY to translate residual BigQuery syntax.\n"
              "- NEVER revert or modify logic that has already been converted by the deterministic rules. Only focus on what was missed.\n"
              "- STRUCT() is natively supported in Databricks. Do NOT convert it to named_struct().\n"
            "- ARRAY of STRUCTS: [STRUCT('a' AS k, value)] -> array(struct('a' as key_col, value))\n"
            "- SUBSTR: 0-based index adjust to 1-based\n"
            "- ARRAY_LENGTH(arr) -> size(arr)\n"
            "- TO_JSON_STRING(x) -> to_json(x)\n"
            "- TO_JSON_STRING(struct, [options]) -> to_json(struct) (no equivalent ignoreNullFields option)\n"
            "- REGEXP_REPLACE(x, r'[^0-9]+', '') -> regexp_replace(x, '[^0-9]+', '') (remove r prefix and escape)\n"
            "- REGEXP_EXTRACT_ALL(x, r'\"(\\w+)\":null') -> regexp_extract_all(x, '\"(\\\\w+)\":null')\n"
            "- DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY) -> date_sub(current_date(), 14)\n"
            "- EXTRACT(ISOWEEK FROM date) -> weekofyear(date)\n"
            "- EXTRACT(WEEK FROM date) -> floor(datediff(date, date_trunc('year', date)) / 7) + 1\n"
            "- EXTRACT(YEAR FROM date) -> year(date)\n"
            "- SAFE_CAST(x AS FLOAT64) -> try_cast(x as double)\n"
            "- ARRAY_LENGTH(REGEXP_EXTRACT_ALL(...)) -> size(regexp_extract_all(...))\n"
            "- DATE(timestamp) -> to_date(timestamp)\n"
            "- ARRAY of STRUCT null handling: Databricks may drop or stringify differently unless explicitly handled.\n"
            "- IMPORTANT NUANCES (Semantic differences to keep in mind):\n"
            "  1. JSON ordering differences: Spark to_json output key order may differ from BigQuery TO_JSON_STRING.\n"
            "  2. WEEK numbering mismatch: Spark uses ISO weeks, BigQuery uses calendar-based logic; numbering remains fundamentally not identical.\n"
            "  3. STRUCT field ordering: Spark may reorder fields in JSON output.\n"
            "  4. FLOAT rounding differences: ROUND(AVG(...),0) behaves slightly differently across engines.\n"
            "- Preserve placeholder comments like /*__MLC_0__*/ and /*__SLC_0__*/ exactly if present\n"
            "- Preserve all comments, aliases, formatting, and query logic exactly\n"
            "- Do NOT add, remove, or reorder columns\n"
            "- Do NOT include derived/computed columns (e.g. digit_count, char_count) in GROUP BY\n"
            "- Target: Databricks 2026 (Runtime 18.0+)\n"
            "- Function safety check: ensure every function exists in Databricks SQL with correct argument order\n"
            "- DATE_TRUNC must be DATE_TRUNC('UNIT', expr). Never output DATE_TRUNC(expr, 'UNIT')\n"
            "- Valid DATE_TRUNC units only: DAY, WEEK, MONTH, QUARTER, YEAR, HOUR, MINUTE, SECOND\n"
            "- Ensure semantic equivalence, not just syntax equivalence\n"
            "- Never replace column identifiers with quoted placeholder strings (e.g., 'date', 'column')\n"
            "\n"
            "Provide ONLY the final Databricks SQL. No explanation, no markdown fences."
        )
