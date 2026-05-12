from typing import Dict, List, Any, Optional, Callable
import re


class RuleEngine:
    """
    Deterministic rule-based translation engine.

    Priority order (highest wins):
      10  — critical exact rewrites (must not be overridden by CSV)
            {
                'pattern': r"\bDATE_FORMAT\s*\(\s*TO_DATE\s*\(([^)]+)\)\s*,\s*'%V'\s*\)",
                'replacement': r'WEEKOFYEAR(TO_DATE(\1))',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r"\bDATE_FORMAT\s*\(\s*([^,]+?)\s*,\s*'%V'\s*\)",
                'replacement': r'WEEKOFYEAR(\1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
       9  — common function remaps
       8  — syntax / operator fixes
       7  — lower-priority / context-dependent
       5  — reserved for future CSV-derived patterns
    """

    def __init__(self, rules_list: List[Dict], edge_cases: List[Dict] = None):
        self.edge_cases = edge_cases or []
        self.pattern_rules = self._build_pattern_rules(rules_list, self.edge_cases)

    @staticmethod
    def apply_pre_ast_translation(sql: str) -> str:
        """
        String-based replacements that MUST happen before SQLGlot AST parsing,
        because SQLGlot destructively alters the target syntax or fails on it.
        Uses manual parenthesis matching to avoid regex nested-bracket failures.
        """
        # ── Standalone FROM UNNEST → subquery with EXPLODE ──────────────────
        # BigQuery: FROM UNNEST(arr) AS x  →  Databricks: FROM (SELECT EXPLODE(arr) AS x) _unnest
        def _replace_from_unnest(query: str) -> str:
            pat = re.compile(r'\bFROM\s+UNNEST\s*\(', re.IGNORECASE)
            idx = 0
            while True:
                match = pat.search(query, idx)
                if not match:
                    break
                start_paren = match.end() - 1
                depth = 0
                end_paren = -1
                for i in range(start_paren, len(query)):
                    if query[i] == '(':
                        depth += 1
                    elif query[i] == ')':
                        depth -= 1
                        if depth == 0:
                            end_paren = i
                            break
                if end_paren == -1:
                    idx = match.end()
                    continue
                arr_content = query[start_paren + 1:end_paren]
                rest = query[end_paren + 1:]
                alias_match = re.match(
                    r'\s+(?:AS\s+)?(\w+)(?:\s+WITH\s+OFFSET\s+(?:AS\s+)?(\w+))?',
                    rest, re.IGNORECASE,
                )
                if alias_match:
                    alias = alias_match.group(1)
                    offset_alias = alias_match.group(2)
                    alias_len = alias_match.end()
                    if offset_alias:
                        replacement = f"FROM (SELECT POSEXPLODE({arr_content}) AS ({offset_alias}, {alias})) _unnest"
                    else:
                        replacement = f"FROM (SELECT EXPLODE({arr_content}) AS {alias}) _unnest"
                    query = query[:match.start()] + replacement + query[end_paren + 1 + alias_len:]
                    idx = match.start() + len(replacement)
                else:
                    idx = match.end()
            return query

        sql = _replace_from_unnest(sql)

        # ── IN UNNEST(array) → array_contains (null-safe per CIQ edge cases) ──
        # BigQuery: col IN UNNEST(arr)  →  Databricks: COALESCE(array_contains(arr, col), FALSE)
        # BigQuery: col NOT IN UNNEST(arr) → Databricks: NOT COALESCE(array_contains(arr, col), FALSE)
        #   (NOT IN returns TRUE in BQ when arr is empty/[], but array_contains
        #    returns NULL when arr is NULL in Databricks — coalesce handles that)
        def _replace_in_unnest(query: str) -> str:
            pat = re.compile(r'((?:`[^`]+`|\b[\w.]+))\s+(NOT\s+)?IN\s+UNNEST\s*\(', re.IGNORECASE)
            idx = 0
            while True:
                match = pat.search(query, idx)
                if not match:
                    break
                expr = match.group(1)
                is_not = match.group(2)
                start_paren = match.end() - 1
                depth = 0
                end_paren = -1
                for i in range(start_paren, len(query)):
                    if query[i] == '(':
                        depth += 1
                    elif query[i] == ')':
                        depth -= 1
                        if depth == 0:
                            end_paren = i
                            break
                if end_paren == -1:
                    idx = match.end()
                    continue
                arr_content = query[start_paren + 1:end_paren]
                if is_not:
                    replacement = f"NOT COALESCE(array_contains({arr_content}, {expr}), FALSE)"
                else:
                    replacement = f"COALESCE(array_contains({arr_content}, {expr}), FALSE)"
                query = query[:match.start()] + replacement + query[end_paren + 1:]
                idx = match.start() + len(replacement)
            return query

        sql = _replace_in_unnest(sql)

        # ── Fix array(struct(...), struct(...)) → [struct(...), struct(...)] ──
        # sqlglot cannot parse array() function calls containing multiple
        # struct() constructors with AS aliases. Bracket notation works fine.
        def _fix_array_of_structs(query: str) -> str:
            pat = re.compile(r'\barray\s*\(\s*struct\s*\(', re.IGNORECASE)
            idx = 0
            while idx < len(query):
                m = pat.search(query, idx)
                if not m:
                    break
                # Find the opening paren of array(
                arr_start = query.index('(', m.start())
                # Find matching close paren using depth tracking
                depth = 0
                end_idx = -1
                for i in range(arr_start, len(query)):
                    if query[i] == '(':
                        depth += 1
                    elif query[i] == ')':
                        depth -= 1
                        if depth == 0:
                            end_idx = i
                            break
                if end_idx == -1:
                    idx = m.end()
                    continue
                # Replace array(...) with [...]
                inner = query[arr_start + 1:end_idx]
                query = query[:m.start()] + '[' + inner + ']' + query[end_idx + 1:]
                idx = m.start() + len(inner) + 2
            return query

        sql = _fix_array_of_structs(sql)

        # ---- Remove dbt Jinja call to set_sql_header if present (not a standard pattern)
        # Remove full call/endcall blocks and any standalone call lines to avoid
        # emitting adapter-specific header macros into Databricks SQL output.
        sql = re.sub(
            r"\{%\s*call\s+set_sql_header\s*\([^%]*%\}.*?\{%\s*endcall\s*%\}",
            '',
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        sql = re.sub(r"\{%\s*call\s+set_sql_header\s*\([^%]*%\}", '', sql, flags=re.IGNORECASE)

        # ---- Normalize Jinja DATE literal assignments to string form
        # e.g. {% set look_back_date_min = DATE '2025-03-01' %} -> {% set look_back_date_min = '2025-03-01' %}
        sql = re.sub(
            r"\{%\s*set\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*DATE\s*'([^']+)'\s*%\}",
            r"{% set \1 = '\2' %}",
            sql,
            flags=re.IGNORECASE,
        )

        # ── Rewrite COUNT(DISTINCT col) OVER (PARTITION BY ...) into lookup+join ──
        # Databricks does not support COUNT(DISTINCT ...) as a window aggregate.
        # We rewrite to:
        #   1) <metric>_lookup CTE with GROUP BY partition keys
        #   2) INNER JOIN back to original source on those keys
        def _rewrite_count_distinct_over(query: str) -> str:
            window_pat = re.compile(
                r'COUNT\s*\(\s*DISTINCT\s+(?P<col>[^\)]+?)\s*\)\s*'
                r'OVER\s*\(\s*PARTITION\s+BY\s*(?P<parts>[^\)]+?)\s*\)\s*'
                r'(?:AS\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?',
                re.IGNORECASE,
            )
            m = window_pat.search(query)
            if not m:
                return query

            distinct_col = m.group('col').strip()
            part_text = m.group('parts').strip()
            metric_alias = (m.group('alias') or 'metric').strip()
            lookup_name = f"{metric_alias}_lookup"
            lookup_alias = 'mrl'

            # If the window expression lives inside a CTE, keep rewrite scoped
            # to that CTE body and insert the lookup CTE right before it.
            cte_insert_pos = None
            scope_end = len(query)
            cte_matches = list(re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(', query[:m.start()], re.IGNORECASE))
            if cte_matches:
                cte_m = cte_matches[-1]
                open_paren = cte_m.end() - 1
                depth = 0
                close_paren = -1
                for i in range(open_paren, len(query)):
                    if query[i] == '(':
                        depth += 1
                    elif query[i] == ')':
                        depth -= 1
                        if depth == 0:
                            close_paren = i
                            break
                if close_paren != -1 and close_paren >= m.end():
                    cte_insert_pos = cte_m.start()
                    scope_end = close_paren

            scoped_tail = query[m.end():scope_end]

            from_pat = re.compile(
                r'\bFROM\s+(?P<from_expr>[\s\S]+?)'
                r'(?=\bWHERE\b|\bGROUP\s+BY\b|\bHAVING\b|\bQUALIFY\b|\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|$)',
                re.IGNORECASE,
            )
            from_m = from_pat.search(scoped_tail)
            if not from_m:
                return query

            from_expr = from_m.group('from_expr').strip()
            if re.search(r'\bJOIN\b', from_expr, re.IGNORECASE):
                return query

            where_pat = re.compile(
                r'\bWHERE\s+(?P<where_expr>[\s\S]+?)'
                r'(?=\bGROUP\s+BY\b|\bHAVING\b|\bQUALIFY\b|\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|$)',
                re.IGNORECASE,
            )
            where_m = where_pat.search(scoped_tail, from_m.end())
            where_expr = where_m.group('where_expr').strip() if where_m else ''

            src_alias_match = re.search(
                r'(?:\bAS\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*$',
                from_expr,
                re.IGNORECASE,
            )
            src_alias = src_alias_match.group(1) if src_alias_match else ''

            part_exprs = RuleEngine._split_top_level_commas(part_text)
            if not part_exprs:
                return query

            select_lines: List[str] = []
            group_by_exprs: List[str] = []
            join_conds: List[str] = []

            for idx, raw_expr in enumerate(part_exprs, start=1):
                expr = raw_expr.strip()
                simple = re.match(r'^(?:([A-Za-z_][A-Za-z0-9_]*)\.)?([A-Za-z_][A-Za-z0-9_]*)$', expr)

                if simple:
                    col_name = simple.group(2)
                    select_qlines.append(f"    {expr} AS {col_name}")
                    group_by_exprs.append(expr)
                    left_expr = expr
                    if src_alias and '.' not in expr:
                        left_expr = f"{src_alias}.{expr}"
                    join_conds.append(f"{left_expr} = {lookup_alias}.{col_name}")
                else:
                    key_alias = f"part_key_{idx}"
                    select_lines.append(f"    {expr} AS {key_alias}")
                    group_by_exprs.append(expr)
                    join_conds.append(f"{expr} = {lookup_alias}.{key_alias}")

            select_block = ',\n'.join(select_lines)
            group_by_block = ', '.join(group_by_exprs)
            join_on = '\n    AND '.join(join_conds)

            where_clause = f"\n  WHERE {where_expr}" if where_expr else ''
            lookup_cte = (
                f"{lookup_name} AS (\n"
                f"  SELECT\n"
                f"{select_block},\n"
                f"    COUNT(DISTINCT {distinct_col}) AS {metric_alias}\n"
                f"  FROM {from_expr}"
                f"{where_clause}\n"
                f"  GROUP BY {group_by_block}\n"
                f")"
            )

            replacement = f"{lookup_alias}.{metric_alias}"
            if m.group('alias'):
                replacement += f" AS {metric_alias}"

            rewritten = query[:m.start()] + replacement + query[m.end():]
            delta = len(replacement) - (m.end() - m.start())

            base_offset = m.end()
            where_start_abs = (base_offset + where_m.start()) if where_m else None
            from_end_abs = base_offset + from_m.end()
            insert_pos = (where_start_abs + delta) if where_start_abs is not None else (from_end_abs + delta)
            join_block = (
                f"\n  INNER JOIN {lookup_name} AS {lookup_alias}\n"
                f"    ON {join_on}\n"
            )
            rewritten = rewritten[:insert_pos] + join_block + rewritten[insert_pos:]

            if re.search(rf'\b{re.escape(lookup_name)}\s+AS\s*\(', rewritten, re.IGNORECASE):
                return rewritten

            if cte_insert_pos is not None:
                return rewritten[:cte_insert_pos] + lookup_cte + ',\n' + rewritten[cte_insert_pos:]

            if re.match(r'^\s*WITH\b', rewritten, re.IGNORECASE):
                return re.sub(
                    r'^(\s*WITH\s+)',
                    rf'\1{lookup_cte},\n',
                    rewritten,
                    count=1,
                    flags=re.IGNORECASE,
                )

            return f"WITH {lookup_cte}\n{rewritten}"

        sql = _rewrite_count_distinct_over(sql)

        # ── Fix date_sub(d, N) / date_add(d, N) with plain integers ──
        # BigQuery accepts date_sub(d, 14) but sqlglot expects INTERVAL.
        # Convert to date_sub(d, INTERVAL N DAY)
        sql = re.sub(
            r'\bdate_sub\s*\(\s*([^,]+?)\s*,\s*(\d+)\s*\)',
            r'date_sub(\1, INTERVAL \2 DAY)',
            sql, flags=re.IGNORECASE,
        )
        sql = re.sub(
            r'\bdate_add\s*\(\s*([^,]+?)\s*,\s*(\d+)\s*\)',
            r'date_add(\1, INTERVAL \2 DAY)',
            sql, flags=re.IGNORECASE,
        )

        # ── Triple-quoted strings normalization ──────────────────────────────
        # BigQuery: """content""" or '''content'''  →  'content' (standard)
        def _normalize_all_sql_strings(query: str) -> str:
            """
            Unified handler for all BigQuery string types. 
            Processes triple-quotes first, then standard quotes carefully.
            """
            # 1. Triple-quotes first (handle both clean and escaped versions)
            def _replace_triple(match):
                full = match.group(0)
                # Remove any backslashes that might be escaping the quotes themselves
                content = full.replace('\\"', '"').replace("\\'", "'")
                # Now extract the content between the first 3 and last 3
                inner = content[3:-3]
                escaped = inner.replace("'", "\\'")
                return f"'{escaped}'"

            # Match triplets of 3 or more quotes (optionally with backslashes)
            query = re.sub(r'(\\?"){3,}([\s\S]*?)(\\?"){3,}(?!(?!\\)")', _replace_triple, query)
            query = re.sub(r"(\\?'){3,}([\s\S]*?)(\\?'){3,}(?!(?!\\)')", _replace_triple, query)

            # 2. Standard double-quotes (only if NOT inside an already converted single string)
            def _replace_standard(match):
                # If we matched a single-quoted string, return it as-is
                if match.group(1):
                    return match.group(1)
                # If we matched a double-quoted string, convert it
                content = match.group(3)
                escaped = content.replace("'", "\\'")
                return f"'{escaped}'"

            # Regex: Match '...' (Group 1) OR "..." (Group 2, with content in Group 3)
            combined_pattern = r"('(?:[^'\\]|\\.)*')|(\"((?:[^\"\\]|\\.)*)\")"
            query = re.sub(combined_pattern, _replace_standard, query)
            return query

        sql = _normalize_all_sql_strings(sql)

        # ── SELECT AS STRUCT → SELECT ──
        # BigQuery's SELECT AS STRUCT generates a row for each struct; Spark/Databricks doesn't support the AS STRUCT clause.
        sql = re.sub(r'\bSELECT\s+AS\s+STRUCT\b', 'SELECT', sql, flags=re.IGNORECASE)

        # Preserve ARRAY_AGG(... ORDER BY ...) semantics before AST translation,
        # because sqlglot can normalize ordered ARRAY_AGG into collect_list(...) and drop ordering.
        sql = RuleEngine._rewrite_array_agg_top1_to_min_max_by(sql)
        sql = RuleEngine._rewrite_array_agg_order_by(sql)

        return sql

    def _build_pattern_rules(self, rules_list: List[Dict], edge_cases: List[Dict] = None) -> List[Dict]:
        if edge_cases is None: edge_cases = []
        protected_native_fns = {
            'UNIX_DATE',
            'DATE_FROM_UNIX_DATE',
            'UNIX_SECONDS',
            'UNIX_MILLIS',
            'UNIX_MICROS',
            'TIMESTAMP_SECONDS',
            'TIMESTAMP_MILLIS',
            'TIMESTAMP_MICROS',
            'UNIX_TIMESTAMP',
        }
        builtins: List[Dict] = [

            # ── HASH / ENCODING ──────────────────────────────────────────────
            # Databricks md5/sha1/sha2 already return hex strings → drop TO_HEX or HEX wrapper
            {
                'pattern': r'\b(?:TO_HEX|HEX)\s*\(\s*MD5\s*\(([^()]+)\)\s*\)',
                'replacement': r'md5(\1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\b(?:TO_HEX|HEX)\s*\(\s*SHA1\s*\(([^()]+)\)\s*\)',
                'replacement': r'sha1(\1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\b(?:TO_HEX|HEX)\s*\(\s*SHA256\s*\(([^()]+)\)\s*\)',
                'replacement': r'sha2(\1, 256)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\b(?:TO_HEX|HEX)\s*\(\s*SHA512\s*\(([^()]+)\)\s*\)',
                'replacement': r'sha2(\1, 512)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bTO_BASE64\s*\(\s*MD5\s*\(([^()]+)\)\s*\)',
                'replacement': r'base64(unhex(md5(\1)))',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # Substr/conv around HEX(md5(...)) → prefer md5(...) with conv/substr
            {
                'pattern': r"\bSUBSTR\s*\(\s*(?:TO_HEX|HEX)\s*\(\s*MD5\s*\(([^()]+)\)\s*\)\s*,\s*([^\)]+)\)",
                'replacement': r"SUBSTR(md5(\1), \2)",
                'flags': re.IGNORECASE, 'priority': 11,
            },
            {
                'pattern': r"\bCONV\s*\(\s*(?:TO_HEX|HEX)\s*\(\s*MD5\s*\(([^()]+)\)\s*\)\s*,\s*16\s*,\s*10\s*\)",
                'replacement': r"CONV(md5(\1), 16, 10)",
                'flags': re.IGNORECASE, 'priority': 11,
            },
            {
                'pattern': r'\bTO_BASE64\s*\(\s*SHA256\s*\(([^()]+)\)\s*\)',
                'replacement': r'base64(unhex(sha2(\1, 256)))',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bTO_HEX\s*\(',
                'replacement': r'hex(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bFROM_HEX\s*\(',
                'replacement': r'unhex(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bTO_BASE64\s*\(',
                'replacement': r'base64(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bFROM_BASE64\s*\(',
                'replacement': r'unbase64(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # FARM_FINGERPRINT — different algorithm from hash(); add warning comment
            {
                'pattern': r'\bFARM_FINGERPRINT\s*\(',
                'replacement': (
                    r'/* TODO: FARM_FINGERPRINT algorithm differs from hash(); '
                    r'numeric values will not match */ hash('
                ),
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # ── EPOCH / UNIX FUNCTIONS — all native in Databricks ───────────
            # CSV wrongly maps these to DATEDIFF/FROM_UNIXTIME etc.
            # Priority 10 identity rules ensure they are kept as-is.
            {
                'pattern': r'\bUNIX_TIMESTAMP\s*\(',
                'replacement': r'unix_timestamp(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bUNIX_DATE\s*\(',
                'replacement': r'unix_date(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bUNIX_SECONDS\s*\(',
                'replacement': r'unix_seconds(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bUNIX_MILLIS\s*\(',
                'replacement': r'unix_millis(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bUNIX_MICROS\s*\(',
                'replacement': r'unix_micros(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bTIMESTAMP_SECONDS\s*\(',
                'replacement': r'timestamp_seconds(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bTIMESTAMP_MILLIS\s*\(',
                'replacement': r'timestamp_millis(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bTIMESTAMP_MICROS\s*\(',
                'replacement': r'timestamp_micros(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATE_FROM_UNIX_DATE\s*\(',
                'replacement': r'date_from_unix_date(',
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # ── SAFE_* MATH FUNCTIONS ────────────────────────────────────────
            {
                'pattern': r'\bSAFE_DIVIDE\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'try_divide(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bSAFE_MULTIPLY\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'try_multiply(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bSAFE_ADD\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'try_add(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bSAFE_NEGATE\s*\(([^,]+)\)',
                'replacement': r'try_subtract(0, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # ── CAST / TYPE ──────────────────────────────────────────────────
            {
                'pattern': r'\bSAFE_CAST\s*\((.+?)\s+AS\s+(.+?)\)',
                'replacement': r'TRY_CAST(\1 AS \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bAS\s+FLOAT64\b',
                'replacement': r'AS DOUBLE',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bAS\s+INT64\b',
                'replacement': r'AS BIGINT',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bAS\s+BIGNUMERIC\b',
                'replacement': r'AS DECIMAL(38,9)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bAS\s+NUMERIC\b',
                'replacement': r'AS DECIMAL(38,9)',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ── JSON FUNCTIONS ───────────────────────────────────────────────
            {
                'pattern': r'\bJSON_EXTRACT_SCALAR\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'get_json_object(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bJSON_EXTRACT\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'get_json_object(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bJSON_VALUE\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'get_json_object(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bJSON_QUERY\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'get_json_object(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # TO_JSON_STRING -> to_json
            {
                'pattern': r'\bTO_JSON_STRING\s*\(',
                'replacement': r'to_json(',
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # ── STRING FUNCTIONS ─────────────────────────────────────────────
            # STRPOS(str, sub) → LOCATE(sub, str)  ← args REVERSED
            {
                'pattern': r'\bSTRPOS\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'LOCATE(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bCOUNTIF\s*\(',
                'replacement': r'COUNT_IF(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bSTARTS_WITH\s*\(',
                'replacement': r'STARTSWITH(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bENDS_WITH\s*\(',
                'replacement': r'ENDSWITH(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bCONTAINS_SUBSTR\s*\(',
                'replacement': r'CONTAINS(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # FORMAT('%s', x) → format_string('%s', x)
            {
                'pattern': r'\bFORMAT\s*\(',
                'replacement': r'format_string(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bREGEXP_CONTAINS\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'\1 RLIKE \2',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # STRING_AGG(col, delim) → concat_ws(delim, collect_list(col))
            {
                'pattern': r'\bSTRING_AGG\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'concat_ws(\2, collect_list(\1))',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bGENERATE_UUID\s*\(\s*\)',
                'replacement': r'uuid()',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bSESSION_USER\s*\(\s*\)',
                'replacement': r'CURRENT_USER()',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bCHAR\s*\((\d+)\)',
                'replacement': r'CHR(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ── SUBSTRING 0-BASED FIX ────────────────────────────────────────
            # BQ SUBSTR(s, 0, n) is 0-indexed; Databricks is 1-indexed
            {
                'pattern': r'\bSUBSTR\s*\(([^,]+),\s*0\s*,',
                'replacement': r'SUBSTR(\1, 1,',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bSUBSTRING\s*\(([^,]+),\s*0\s*,',
                'replacement': r'SUBSTRING(\1, 1,',
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # ── DATE / TIME ──────────────────────────────────────────────────
            # CURRENT_DATE() — Databricks supports with parens, keep as-is
            # CURRENT_TIMESTAMP() — Databricks supports with parens, keep as-is
            # CURRENT_DATETIME() → CURRENT_TIMESTAMP() (BQ-only function)
            {
                'pattern': r'\bCURRENT_DATETIME\s*\(\s*\)',
                'replacement': r'CURRENT_TIMESTAMP()',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # BigQuery: STRUCT('a' AS k, 1 AS v) -> named_struct('k', 'a', 'v', 1)
            {
                'pattern': r'\bSTRUCT\((.*?)\)',
                'replacement': lambda m: self._map_bq_struct_to_named(m.group(1)),
                'flags': re.IGNORECASE, 'priority': 15,
            },
            # BigQuery: FROM data, UNNEST(arr) -> FROM data LATERAL VIEW EXPLODE(arr)
            {
                'pattern': r',\s*UNNEST\((.*?)\)(?:\s+AS\s+(\w+))?',
                'replacement': r' LATERAL VIEW EXPLODE(\1) _t AS \2',
                'flags': re.IGNORECASE, 'priority': 20,
            },
            # DATE_DIFF by unit
            {
                'pattern': r'\bDATE_DIFF\s*\(([^,]+),\s*([^,]+),\s*DAY\s*\)',
                'replacement': r'DATEDIFF(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bDATEDIFF\s*\(\s*MONTH\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'CAST(FLOOR(MONTHS_BETWEEN(\2, \1)) AS INT)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEDIFF\s*\(\s*YEAR\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'FLOOR(MONTHS_BETWEEN(\2, \1) / 12)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEDIFF\s*\(\s*WEEK\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'FLOOR(DATEDIFF(\2, \1) / 7)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATEDIFF\s*\(\s*DAY\s*,\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'DATEDIFF(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bDATE_DIFF\s*\(\s*(.+?)\s*,\s*(.+?)\s*,\s*MONTH\s*\)',
                'replacement': r'CAST(FLOOR(MONTHS_BETWEEN(\1, \2)) AS INT)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bDATE_DIFF\s*\(([^,]+),\s*([^,]+),\s*WEEK\s*\)',
                'replacement': r'FLOOR(DATEDIFF(\1, \2) / 7)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bDATE_DIFF\s*\(([^,]+),\s*([^,]+),\s*YEAR\s*\)',
                'replacement': r'FLOOR(MONTHS_BETWEEN(\1, \2) / 12)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DATE_ADD / DATE_SUB  INTERVAL form
            {
                'pattern': r'\bDATE_ADD\s*\(([^,]+),\s*INTERVAL\s+(\d+)\s+DAYS?\s*\)',
                'replacement': r'DATE_ADD(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bDATE_SUB\s*\(([^,]+),\s*INTERVAL\s+(\d+)\s+DAYS?\s*\)',
                'replacement': r'DATE_SUB(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TIMESTAMP_ADD / _SUB / DATETIME_ADD / _SUB → arithmetic
            {
                'pattern': r'\bTIMESTAMP_ADD\s*\(([^,]+),\s*INTERVAL\s+(\w+)\s+(\w+)\s*\)',
                'replacement': r'\1 + INTERVAL \2 \3S',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bTIMESTAMP_SUB\s*\(([^,]+),\s*INTERVAL\s+(\w+)\s+(\w+)\s*\)',
                'replacement': r'\1 - INTERVAL \2 \3S',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bDATETIME_ADD\s*\(([^,]+),\s*INTERVAL\s+(\w+)\s+(\w+)\s*\)',
                'replacement': r'\1 + INTERVAL \2 \3S',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bDATETIME_SUB\s*\(([^,]+),\s*INTERVAL\s+(\w+)\s+(\w+)\s*\)',
                'replacement': r'\1 - INTERVAL \2 \3S',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TIMESTAMP_DIFF / DATETIME_DIFF
            {
                'pattern': r'\bTIMESTAMP_DIFF\s*\(([^,]+),\s*([^,]+),\s*(\w+)\s*\)',
                'replacement': r'TIMESTAMPDIFF(\3, \2, \1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bDATETIME_DIFF\s*\(([^,]+),\s*([^,]+),\s*(\w+)\s*\)',
                'replacement': r'TIMESTAMPDIFF(\3, \2, \1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DATE_TRUNC(date, UNIT) → DATE_TRUNC('UNIT', date)  ← args swapped
            {
                'pattern': r"\bDATE_TRUNC\s*\(\s*'([A-Za-z_][A-Za-z0-9_\.]*)'\s*,\s*'(DAY|WEEK|MONTH|QUARTER|YEAR|HOUR|MINUTE|SECOND)'\s*\)",
                'replacement': r"DATE_TRUNC('\2', \1)",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r"\bDATE_TRUNC\s*\(\s*([^,]+?)\s*,\s*'(DAY|WEEK|MONTH|QUARTER|YEAR|HOUR|MINUTE|SECOND)'\s*\)",
                'replacement': r"DATE_TRUNC('\2', \1)",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bDATE_TRUNC\s*\(([^,]+),\s*(DAY|WEEK|MONTH|QUARTER|YEAR|HOUR|MINUTE|SECOND)\s*\)',
                'replacement': r"DATE_TRUNC('\2', \1)",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bTIMESTAMP_TRUNC\s*\(([^,]+),\s*(DAY|WEEK|MONTH|QUARTER|YEAR|HOUR|MINUTE|SECOND)\s*\)',
                'replacement': r"DATE_TRUNC('\2', \1)",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bDATETIME_TRUNC\s*\(([^,]+),\s*(DAY|WEEK|MONTH|QUARTER|YEAR|HOUR|MINUTE|SECOND)\s*\)',
                'replacement': r"DATE_TRUNC('\2', \1)",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # EXTRACT(UNIT FROM col) → unit_fn(col)
            {
                'pattern': r'\bEXTRACT\s*\(\s*DAYOFWEEK\s+FROM\s+([^)]+)\)',
                'replacement': r'DAYOFWEEK(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                # BQ EXTRACT(WEEK ...) is Sunday-based 0..53; WEEKOFYEAR is ISO Mon-based 1..53
                # Use a formula that replicates BQ Sunday-based week numbering exactly from Edge Cases sheet
                'pattern': r'\bEXTRACT\s*\(\s*WEEK\s+FROM\s+([^)]+)\)',
                'replacement': r"floor(datediff(\1, date_trunc('year', \1)) / 7) + 1",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bEXTRACT\s*\(\s*ISOWEEK\s+FROM\s+([^)]+)\)',
                'replacement': r'WEEKOFYEAR(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bEXTRACT\s*\(\s*YEAR\s+FROM\s+([^)]+)\)',
                'replacement': r'YEAR(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bEXTRACT\s*\(\s*MONTH\s+FROM\s+([^)]+)\)',
                'replacement': r'MONTH(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bEXTRACT\s*\(\s*DAY\b[^)]*FROM\s+([^)]+)\)',
                'replacement': r'DAY(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bEXTRACT\s*\(\s*HOUR\s+FROM\s+([^)]+)\)',
                'replacement': r'HOUR(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bEXTRACT\s*\(\s*MINUTE\s+FROM\s+([^)]+)\)',
                'replacement': r'MINUTE(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bEXTRACT\s*\(\s*SECOND\s+FROM\s+([^)]+)\)',
                'replacement': r'SECOND(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bEXTRACT\s*\(\s*QUARTER\s+FROM\s+([^)]+)\)',
                'replacement': r'QUARTER(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bEXTRACT\s*\(\s*DAYOFYEAR\s+FROM\s+([^)]+)\)',
                'replacement': r'DAYOFYEAR(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # FORMAT_DATE('%V', date) returns ISO week number in BigQuery.
            # Databricks equivalent is WEEKOFYEAR(date).
            {
                'pattern': r"\bFORMAT_DATE\s*\(\s*'%V'\s*,\s*([^)]+)\)",
                'replacement': r'WEEKOFYEAR(\1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # LLM may emit DATE_FORMAT(..., '%V'); normalize to Databricks week fn.
            {
                'pattern': r"\bDATE_FORMAT\s*\(\s*TO_DATE\s*\(([^)]+)\)\s*,\s*'%V'\s*\)",
                'replacement': r'WEEKOFYEAR(TO_DATE(\1))',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r"\bDATE_FORMAT\s*\(\s*([^,]+?)\s*,\s*'%V'\s*\)",
                'replacement': r'WEEKOFYEAR(\1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # FORMAT_DATE / FORMAT_TIMESTAMP / FORMAT_DATETIME
            {
                'pattern': r'\bFORMAT_DATE\s*\(',
                'replacement': r'DATE_FORMAT(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bFORMAT_TIMESTAMP\s*\(',
                'replacement': r'DATE_FORMAT(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bFORMAT_DATETIME\s*\(',
                'replacement': r'DATE_FORMAT(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # PARSE_DATE / PARSE_TIMESTAMP / PARSE_DATETIME
            {
                'pattern': r'\bPARSE_DATE\s*\(',
                'replacement': r'TO_DATE(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bPARSE_TIMESTAMP\s*\(',
                'replacement': r'TO_TIMESTAMP(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bPARSE_DATETIME\s*\(',
                'replacement': r'TO_TIMESTAMP(',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ── INTERVAL UNIT NORMALISATION ──────────────────────────────────
            # INTERVAL n DAY → INTERVAL n DAYS  etc.
            {
                'pattern': r'\bINTERVAL\s+(\d+)\s+DAY\b(?!S)',
                'replacement': r'INTERVAL \1 DAYS',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bINTERVAL\s+(\d+)\s+MONTH\b(?!S)',
                'replacement': r'INTERVAL \1 MONTHS',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bINTERVAL\s+(\d+)\s+YEAR\b(?!S)',
                'replacement': r'INTERVAL \1 YEARS',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bINTERVAL\s+(\d+)\s+HOUR\b(?!S)',
                'replacement': r'INTERVAL \1 HOURS',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bINTERVAL\s+(\d+)\s+MINUTE\b(?!S)',
                'replacement': r'INTERVAL \1 MINUTES',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bINTERVAL\s+(\d+)\s+SECOND\b(?!S)',
                'replacement': r'INTERVAL \1 SECONDS',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bINTERVAL\s+(\d+)\s+WEEK\b(?!S)',
                'replacement': r'INTERVAL \1 WEEKS',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── ARRAY FUNCTIONS ──────────────────────────────────────────────
            {
                'pattern': r'\bARRAY_LENGTH\s*\(',
                'replacement': r'SIZE(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # ARRAY_CONCAT → concat  (NOT ARRAY_UNION — that removes duplicates)
            {
                'pattern': r'\bARRAY_CONCAT\s*\(',
                'replacement': r'concat(',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bARRAY_TO_STRING\s*\(',
                'replacement': r'array_join(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bARRAY_REVERSE\s*\(',
                'replacement': r'reverse(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bARRAY_INCLUDES\s*\(',
                'replacement': r'array_contains(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bARRAY_TRANSFORM\s*\(',
                'replacement': r'transform(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bARRAY_FILTER\s*\(',
                'replacement': r'filter(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bARRAY_CONCAT_AGG\s*\(',
                'replacement': r'flatten(collect_list(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bARRAY_AGG\s*\(\s*DISTINCT\s+([^)]+)\)',
                'replacement': r'collect_set(\1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bARRAY_AGG\s*\(',
                'replacement': r'collect_list(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # table, UNNEST(GENERATE_DATE_ARRAY('2024-04-01', CURRENT_DATE())) AS date
            #   -> table LATERAL VIEW EXPLODE(SEQUENCE(TO_DATE('2024-04-01'), CURRENT_DATE())) AS date
            {
                'pattern': (
                    r',\s*UNNEST\s*\(\s*GENERATE_DATE_ARRAY\s*\('
                    r'\s*((?:\'(?:[^\']|\'\')*\')|(?:"(?:[^"]|"")*"))\s*,\s*'
                    r'CURRENT_DATE\s*\(?\s*\)?\s*'
                    r'\)\s*\)\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)'
                ),
                'replacement': r' LATERAL VIEW EXPLODE(SEQUENCE(TO_DATE(\1), CURRENT_DATE())) AS \2',
                'flags': re.IGNORECASE,
                'priority': 10,
            },
            # UNNEST(GENERATE_DATE_ARRAY(DATE(...), DATE_SUB(CURRENT_DATE(), INTERVAL X DAY)))
            {
                'pattern': (
                    r'\bUNNEST\s*\(\s*GENERATE_DATE_ARRAY\s*\('
                    r'\s*DATE\s*\(([^\)]+)\)\s*,\s*'
                    r'DATE_SUB\s*\(\s*CURRENT_DATE\s*(?:\(\s*\))?\s*,\s*INTERVAL\s*(\d+)\s*DAY\s*\)\s*'
                    r'\)\s*\)'
                ),
                'replacement': r'explode(sequence(to_date(\1), date_sub(current_date(), \2), interval 1 day))',
                'flags': re.IGNORECASE,
                'priority': 1,
            },
            # UNNEST(GENERATE_DATE_ARRAY('yyyy-mm-dd', CURRENT_DATE()-N)) fallback
            {
                'pattern': (
                    r'\bUNNEST\s*\(\s*GENERATE_DATE_ARRAY\s*\('
                    r"\s*('(?:[^']|'')*')\s*,\s*CURRENT_DATE\s*\(?\s*\)?\s*-\s*(\d+)\s*"
                    r'\)\s*\)'
                ),
                'replacement': r"EXPLODE(sequence(to_date(\1), date_sub(current_date(), \2), interval 1 day))",
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # UNNEST wrapper normalization to EXPLODE sequence
            {
                'pattern': (
                    r'\bUNNEST\s*\(\s*GENERATE_DATE_ARRAY\s*\('
                    r'\s*([^,]+?)\s*,\s*([^\)]+?)\s*'
                    r'\)\s*\)'
                ),
                'replacement': r'EXPLODE(sequence(to_date(\1), \2, interval 1 day))',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # Normalize UNNEST over subquery
            {
                'pattern': (
                    r'SELECT\s+\*\s+FROM\s*\(\s*SELECT\s+'
                    r'UNNEST\s*\((.*?)\)\s+AS\s+(\w+)\s*\)\s*(?:AS\s+\w+)?'
                ),
                'replacement': r'SELECT \2 FROM UNNEST(\1) AS \2',
                'flags': re.IGNORECASE | re.DOTALL,
                'priority': 10,
            },
            # Normalize UNNEST over subquery
            {
                'pattern': (
                    r'SELECT\s+\*\s+FROM\s*\(\s*SELECT\s+'
                    r'UNNEST\s*\((.*?)\)\s+AS\s+(\w+)\s*\)\s*(?:AS\s+\w+)?'
                ),
                'replacement': r'SELECT \2 FROM UNNEST(\1) AS \2',
                'flags': re.IGNORECASE | re.DOTALL, 'priority': 10,
            },
            # Normalize generated date-array explode output to requested Databricks form
            {
                'pattern': (
                    r'EXPLODE\s*\(\s*SEQUENCE\s*\('
                    r'\s*TO_DATE\s*\(([^\)]+)\)\s*,\s*'
                    r'DATE_SUB\s*\(\s*CURRENT_DATE\s*(?:\(\s*\))?\s*,\s*(\d+)\s*\)\s*,\s*'
                    r'INTERVAL\s*\'?1\'?\s*DAYS?\s*\)\s*\)'
                ),
                'replacement': r'explode(sequence(to_date(\1), date_sub(current_date(), \2), interval 1 day))',
                'flags': re.IGNORECASE, 'priority': 1,
            },
            # GENERATE_ARRAY → SEQUENCE
            {
                'pattern': r'\bGENERATE_ARRAY\s*\(',
                'replacement': r'sequence(',
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # array[OFFSET(n)] → array[n]
            {
                'pattern': r'\[OFFSET\s*\((\d+)\)\]',
                'replacement': r'[\1]',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # array[ORDINAL(n)] → array[n-1]  (ORDINAL is 1-based)
            {
                'pattern': r'\[ORDINAL\s*\((\d+)\)\]',
                'replacement': lambda m: f'[{int(m.group(1)) - 1}]',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # array[SAFE_OFFSET(n)] → get(array, n)
            {
                'pattern': r'(\w+)\[SAFE_OFFSET\s*\((\w+)\)\]',
                'replacement': r'get(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bARRAY_FIRST\s*\((\w+)\)',
                'replacement': r'\1[0]',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bARRAY_LAST\s*\((\w+)\)',
                'replacement': r'element_at(\1, -1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ── AGGREGATE ────────────────────────────────────────────────────
            # ANY_VALUE — Databricks supports natively (DBR 11.3+), keep as-is
            {
                'pattern': r'\bLOGICAL_AND\s*\(',
                'replacement': r'BOOL_AND(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bLOGICAL_OR\s*\(',
                'replacement': r'BOOL_OR(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bAPPROX_QUANTILES\s*\(([^,]+),\s*4\s*\)',
                'replacement': r'percentile_approx(\1, array(0.0, 0.25, 0.5, 0.75, 1.0))',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ── MATH ─────────────────────────────────────────────────────────
            # LOG(x, base) → LOG(base, x)  ← arg order reversed in Databricks
            {
                'pattern': r'\bLOG\s*\(([^,()]+),\s*([^)]+)\)',
                'replacement': r'LOG(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # LOG(x) single arg = natural log in BQ → LN in Databricks
            {
                'pattern': r'\bLOG\s*\(([^,)]+)\)',
                'replacement': r'LN(\1)',
                'flags': re.IGNORECASE, 'priority': 7,
            },
            {
                'pattern': r'(?<!\bDATE_)\bTRUNC\s*\(',
                'replacement': r'TRUNCATE(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bIS_NAN\s*\(',
                'replacement': r'isnan(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bIEEE_DIVIDE\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'(\1 / \2)',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bMOD\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'\1 % \2',
                'flags': re.IGNORECASE, 'priority': 7,
            },
            {
                'pattern': r'\bDIV\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'\1 DIV \2',
                'flags': re.IGNORECASE, 'priority': 7,
            },

            # ── SYNTAX DIFFERENCES ───────────────────────────────────────────
            # NOTE: SELECT * EXCEPT — Databricks supports natively, do NOT rewrite
            # NOTE: QUALIFY — Databricks supports natively, do NOT rewrite
            # NOTE: JOIN USING — Databricks supports USING natively, do NOT rewrite

            # Backtick project.dataset.table → catalog.schema.table
            {
                'pattern': r'`([a-zA-Z0-9_\-]+)\.([a-zA-Z0-9_\-]+)\.([a-zA-Z0-9_\-]+)`',
                'replacement': r'\1.\2.\3',
                'flags': 0, 'priority': 8,
            },
            # PARTITION BY col → PARTITIONED BY (col)  in DDL context
            {
                'pattern': r'\bPARTITION\s+BY\s*\(([^)]+)\)(?=\s*(?:CLUSTER|OPTIONS|AS\s|;|$))',
                'replacement': r'PARTITIONED BY (\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'(?i)\b(CREATE\s+(?:OR\s+REPLACE\s+)?)TEMP(?:ORARY)?\s+TABLE\b',
                'replacement': r'\1TEMPORARY VIEW',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bTABLESAMPLE\s+SYSTEM\s*\(',
                'replacement': r'TABLESAMPLE(',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── STRUCT ───────────────────────────────────────────────────────
            # STRUCT<field STRING> → STRUCT<field:STRING>
            {
                'pattern': r'\bSTRUCT\s*<([^>]+)>',
                'replacement': lambda m: 'STRUCT<' + re.sub(
                    r'(\b\w+)\s+((?:ARRAY|STRUCT|STRING|INT|BIGINT|DOUBLE|FLOAT|BOOLEAN|DATE|TIMESTAMP|DECIMAL)[^,>]*)',
                    r'\1:\2',
                    m.group(1)
                ) + '>',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── CONDITIONAL ──────────────────────────────────────────────────
            # NVL → COALESCE (IFNULL already works in Databricks)
            {
                'pattern': r'\bNVL\s*\(',
                'replacement': r'COALESCE(',
                'flags': re.IGNORECASE, 'priority': 7,
            },
            # IFF / IIF → IF
            {
                'pattern': r'\bI[FI]F\s*\(',
                'replacement': r'IF(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # NVL2(a, b, c) → IF(a IS NOT NULL, b, c)
            {
                'pattern': r'\bNVL2\s*\(([^,]+),\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'IF(\1 IS NOT NULL, \2, \3)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DECODE(col, v1, r1, v2, r2, default) → CASE WHEN
            # Simple 2-branch form: DECODE(col, v1, r1, def)
            {
                'pattern': r'\bDECODE\s*\(([^,]+),\s*([^,]+),\s*([^,]+),\s*([^)]+)\)',
                'replacement': r'CASE WHEN \1 = \2 THEN \3 ELSE \4 END',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── DATE ARITHMETIC (MONTH/YEAR variants) ────────────────────────
            # DATE_ADD(d, INTERVAL n MONTH) → ADD_MONTHS(d, n)
            {
                'pattern': r'\bDATE_ADD\s*\(([^,]+),\s*INTERVAL\s+(\d+)\s+MONTHS?\s*\)',
                'replacement': r'ADD_MONTHS(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DATE_ADD(d, INTERVAL n YEAR) → ADD_MONTHS(d, n * 12)
            {
                'pattern': r'\bDATE_ADD\s*\(([^,]+),\s*INTERVAL\s+(\d+)\s+YEARS?\s*\)',
                'replacement': r'ADD_MONTHS(\1, \2 * 12)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DATETIME_DIFF(a, b, DAY) → DATEDIFF(a, b)  — must be before generic DATETIME_DIFF
            {
                'pattern': r'\bDATETIME_DIFF\s*\(([^,]+),\s*([^,]+),\s*DAY\s*\)',
                'replacement': r'DATEDIFF(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # DATETIME_DIFF(a, b, MONTH) → CAST(FLOOR(MONTHS_BETWEEN(a, b)) AS INT)
            {
                'pattern': r'\bDATETIME_DIFF\s*\(([^,]+),\s*([^,]+),\s*MONTH\s*\)',
                'replacement': r'CAST(FLOOR(MONTHS_BETWEEN(\1, \2)) AS INT)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # TIMESTAMP_DIFF(a, b, DAY) → DATEDIFF(a, b)
            {
                'pattern': r'\bTIMESTAMP_DIFF\s*\(([^,]+),\s*([^,]+),\s*DAY\s*\)',
                'replacement': r'DATEDIFF(\1, \2)',
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # ── CURRENT_TIME ─────────────────────────────────────────────────
            {
                'pattern': r'\bCURRENT_TIME\s*\(\s*\)',
                'replacement': r"date_format(CURRENT_TIMESTAMP, 'HH:mm:ss')",
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # ── JSON ARRAY EXTRACTION ─────────────────────────────────────────
            {
                'pattern': r'\bJSON_EXTRACT_ARRAY\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r"from_json(get_json_object(\1, \2), 'array<variant>')",
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bJSON_VALUE_ARRAY\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r"from_json(get_json_object(\1, \2), 'array<variant>')",
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bJSON_QUERY_ARRAY\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r"from_json(get_json_object(\1, \2), 'array<variant>')",
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # JSON_OBJECT('key', val, ...) → to_json(map('key', val, ...))
            {
                'pattern': r'\bJSON_OBJECT\s*\(',
                'replacement': r'to_json(map(',
                'flags': re.IGNORECASE, 'priority': 10,
                'note': 'Adds extra open paren; post-pass in apply_function_translation closes it',
            },
            # JSON_ARRAY(val, ...) → to_json(array(val, ...))
            {
                'pattern': r'\bJSON_ARRAY\s*\(',
                'replacement': r'to_json(array(',
                'flags': re.IGNORECASE, 'priority': 10,
                'note': 'Adds extra open paren; post-pass in apply_function_translation closes it',
            },

            # ── ARRAY INDEXING (remaining) ────────────────────────────────────
            # arr[SAFE_ORDINAL(n)] → get(arr, n-1)
            {
                'pattern': r'(\w+)\[SAFE_ORDINAL\s*\((\d+)\)\]',
                'replacement': lambda m: f'get({m.group(1)}, {int(m.group(2)) - 1})',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ── ARRAY HIGHER-ORDER ────────────────────────────────────────────
            # REDUCE(arr, init, (acc, x) -> expr) → aggregate(arr, init, (acc, x) -> expr)
            {
                'pattern': r'\bREDUCE\s*\(',
                'replacement': r'aggregate(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # FLATTEN already same in Databricks but ensure case-insensitive
            {
                'pattern': r'\bFLATTEN\s*\(',
                'replacement': r'flatten(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # ZIP_ARRAY → arrays_zip
            {
                'pattern': r'\bZIP_ARRAY\s*\(',
                'replacement': r'arrays_zip(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # ARRAY_COMPACT → array_compact (same, normalise case)
            {
                'pattern': r'\bARRAY_COMPACT\s*\(',
                'replacement': r'array_compact(',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── STRING / BYTES ────────────────────────────────────────────────
            # BigQuery TRIM(expr, chars) -> Databricks TRIM(BOTH chars FROM expr)
            {
                'pattern': r'\bTRIM\s*\(\s*([^,\)]+?)\s*,\s*([^\)]+?)\s*\)',
                'replacement': r'TRIM(BOTH \2 FROM \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # SAFE_CONVERT_BYTES_TO_STRING → decode(expr, 'UTF-8')
            {
                'pattern': r'\bSAFE_CONVERT_BYTES_TO_STRING\s*\(([^)]+)\)',
                'replacement': r"decode(\1, 'UTF-8')",
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # BYTE_LENGTH → OCTET_LENGTH
            {
                'pattern': r'\bBYTE_LENGTH\s*\(',
                'replacement': r'OCTET_LENGTH(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # NORMALIZE(s) → s with TODO (no Databricks equivalent)
            {
                'pattern': r'\bNORMALIZE(?:_AND_CASEFOLD)?\s*\(([^,)]+)(?:,[^)]*)?\)',
                'replacement': r'\1 /* TODO: NORMALIZE not supported in Databricks */',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── MATH ─────────────────────────────────────────────────────────
            # IS_INF(x) → TODO comment + approximation
            {
                'pattern': r'\bIS_INF\s*\(([^)]+)\)',
                'replacement': r'(/* TODO: IS_INF */ isnan(\1 - \1) AND NOT isnan(\1))',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── MATH (additional) ──────────────────────────────────────────────
            # IS_NAN(x) → isnan(x)
            {
                'pattern': r'\bIS_NAN\s*\(',
                'replacement': r'isnan(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # IEEE_DIVIDE(a, b) → a / b  (Databricks default is IEEE)
            {
                'pattern': r'\bIEEE_DIVIDE\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'(\1 / \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DIV(a, b) → FLOOR(a / b)  (integer division)
            {
                'pattern': r'\bDIV\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'FLOOR(\1 / \2)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # SAFE_NEGATE(x) → TRY_SUBTRACT(0, x)
            {
                'pattern': r'\bSAFE_NEGATE\s*\(([^)]+)\)',
                'replacement': r'(-\1) /* TODO: SAFE_NEGATE overflow not caught */',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # SAFE_ADD(a, b) → TRY_ADD(a, b)
            {
                'pattern': r'\bSAFE_ADD\s*\(',
                'replacement': r'TRY_ADD(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # SAFE_SUBTRACT(a, b) → TRY_SUBTRACT(a, b)
            {
                'pattern': r'\bSAFE_SUBTRACT\s*\(',
                'replacement': r'TRY_SUBTRACT(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # SAFE_MULTIPLY(a, b) → TRY_MULTIPLY(a, b)
            {
                'pattern': r'\bSAFE_MULTIPLY\s*\(',
                'replacement': r'TRY_MULTIPLY(',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ── APPROXIMATE AGGREGATION ─────────────────────────────────────────
            # APPROX_COUNT_DISTINCT → APPROX_COUNT_DISTINCT (same)
            # APPROX_QUANTILES(x, n) → PERCENTILE_APPROX(x, array(0.25, 0.5, 0.75))
            {
                'pattern': r'\bAPPROX_QUANTILES\s*\(([^,]+),\s*(\d+)\s*\)',
                'replacement': r'PERCENTILE_APPROX(\1, SEQUENCE(0, 1, 1.0/\2)) /* TODO: verify quantile buckets */',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # APPROX_TOP_COUNT → APPROX_TOP_K (available in Databricks 2026)
            # TODO: Schema difference — BQ returns struct(value, count), Databricks returns array of struct(item, count)
            {
                'pattern': r'\bAPPROX_TOP_COUNT\s*\(',
                'replacement': r'APPROX_TOP_K( /* TODO: output schema differs from BQ APPROX_TOP_COUNT; Databricks returns array<struct<item,count>> */ ',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # APPROX_TOP_SUM → no direct equivalent
            {
                'pattern': r'\bAPPROX_TOP_SUM\s*\(([^)]+)\)',
                'replacement': r'\1 /* TODO: APPROX_TOP_SUM has no Databricks equivalent */',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── DATE / TIME (additional) ───────────────────────────────────────
            # LAST_DAY(date, MONTH) → LAST_DAY(date)
            {
                'pattern': r'\bLAST_DAY\s*\(([^,)]+),\s*MONTH\s*\)',
                'replacement': r'LAST_DAY(\1)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # LAST_DAY(date, QUARTER) → DATE_SUB(DATE_TRUNC('QUARTER', ADD_MONTHS(date, 3)), 1)
            {
                'pattern': r'\bLAST_DAY\s*\(([^,)]+),\s*QUARTER\s*\)',
                'replacement': r"DATE_SUB(DATE_TRUNC('QUARTER', ADD_MONTHS(\1, 3)), 1)",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # LAST_DAY(date, YEAR) → MAKE_DATE(YEAR(date), 12, 31)
            {
                'pattern': r'\bLAST_DAY\s*\(([^,)]+),\s*YEAR\s*\)',
                'replacement': r'MAKE_DATE(YEAR(\1), 12, 31)',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # LAST_DAY(date, WEEK) → DATE_ADD(DATE_TRUNC('WEEK', date), 6)
            {
                'pattern': r'\bLAST_DAY\s*\(([^,)]+),\s*WEEK\s*\)',
                'replacement': r"DATE_ADD(DATE_TRUNC('WEEK', \1), 6)",
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # (duplicate CURRENT_DATETIME rule removed — handled above at priority 10)
            # PARSE_DATE(format, str) → TO_DATE(str, format)  — swapped args
            # Use lazy match for second arg to handle nested expressions
            {
                'pattern': r'\bPARSE_DATE\s*\(([^,]+),\s*(.+?)\)(?=\s|,|$|;|\))',
                'replacement': r'TO_DATE(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # PARSE_TIMESTAMP(format, str) → TO_TIMESTAMP(str, format)
            {
                'pattern': r'\bPARSE_TIMESTAMP\s*\(([^,]+),\s*(.+?)\)(?=\s|,|$|;|\))',
                'replacement': r'TO_TIMESTAMP(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # PARSE_DATETIME(format, str) → TO_TIMESTAMP(str, format)
            {
                'pattern': r'\bPARSE_DATETIME\s*\(([^,]+),\s*(.+?)\)(?=\s|,|$|;|\))',
                'replacement': r'TO_TIMESTAMP(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # PARSE_TIME(format, str) → DATE_FORMAT(TO_TIMESTAMP(str, format), format)
            {
                'pattern': r'\bPARSE_TIME\s*\(([^,]+),\s*(.+?)\)(?=\s|,|$|;|\))',
                'replacement': r'DATE_FORMAT(TO_TIMESTAMP(\2, \1), \1) /* TODO: verify TIME format mapping */',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # TIMESTAMP_SECONDS(x) → TIMESTAMP_SECONDS(x) (Databricks-native)
            {
                'pattern': r'\bTIMESTAMP_SECONDS\s*\(',
                'replacement': r'timestamp_seconds(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TIMESTAMP_MILLIS(x) → timestamp_millis(x) (Databricks-native)
            {
                'pattern': r'\bTIMESTAMP_MILLIS\s*\(',
                'replacement': r'timestamp_millis(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # TIMESTAMP_MICROS(x) → timestamp_micros(x) (Databricks-native)
            {
                'pattern': r'\bTIMESTAMP_MICROS\s*\(',
                'replacement': r'timestamp_micros(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # DATE_FROM_UNIX_DATE(x) → DATE_FROM_UNIX_DATE(x) (Databricks-native)
            {
                'pattern': r'\bDATE_FROM_UNIX_DATE\s*\(',
                'replacement': r'date_from_unix_date(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # UNIX_DATE(x) → unix_date(x) (Databricks-native)
            {
                'pattern': r'\bUNIX_DATE\s*\(',
                'replacement': r'unix_date(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # MAKE_TIMESTAMP(y, m, d, h, mi, s) → MAKE_TIMESTAMP(y, m, d, h, mi, s)
            {
                'pattern': r'\bMAKE_TIMESTAMP\s*\(',
                'replacement': r'make_timestamp(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # MAKE_DATE(y, m, d) → MAKE_DATE(y, m, d)
            {
                'pattern': r'\bMAKE_DATE\s*\(',
                'replacement': r'make_date(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # MAKE_INTERVAL(y, m, d, h, mi, s) → MAKE_INTERVAL(y, m, 0, d, h, mi, s)
            {
                'pattern': r'\bMAKE_INTERVAL\s*\(',
                'replacement': r'make_interval( /* TODO: BigQuery MAKE_INTERVAL arg order differs from Databricks */ ',
                'flags': re.IGNORECASE, 'priority': 7,
            },

            # ── NETWORKING FUNCTIONS ────────────────────────────────────────────
            # NET.IP_FROM_STRING, NET.SAFE_IP_FROM_STRING, etc. — no Databricks equivalent
            {
                'pattern': r'\bNET\.(IP_FROM_STRING|SAFE_IP_FROM_STRING|IP_TO_STRING|IP_NET_MASK|IP_TRUNC|IPV4_FROM_INT64|IPV4_TO_INT64|HOST|PUBLIC_SUFFIX|REG_DOMAIN)\s*\(([^)]+)\)',
                'replacement': r'\2 /* TODO: NET.\1 has no Databricks equivalent */',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── UTILITY / SYSTEM ───────────────────────────────────────────────
            # GENERATE_UUID() → UUID() (Databricks)
            {
                'pattern': r'\bGENERATE_UUID\s*\(\s*\)',
                'replacement': r'UUID()',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # SESSION_USER() → CURRENT_USER()
            {
                'pattern': r'\bSESSION_USER\s*\(\s*\)',
                'replacement': r'CURRENT_USER()',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # ERROR(msg) → RAISE_ERROR(msg)
            {
                'pattern': r'\bERROR\s*\(',
                'replacement': r'RAISE_ERROR(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            # BIT_COUNT(x) → BIT_COUNT(x)  (same in Databricks)
            {
                'pattern': r'\bBIT_COUNT\s*\(',
                'replacement': r'bit_count(',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── FORMAT FUNCTIONS ───────────────────────────────────────────────
            # FORMAT_DATE(fmt, date) → DATE_FORMAT(date, fmt)  — swapped args
            {
                'pattern': r'\bFORMAT_DATE\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'DATE_FORMAT(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # FORMAT_TIMESTAMP(fmt, ts) → DATE_FORMAT(ts, fmt)
            {
                'pattern': r'\bFORMAT_TIMESTAMP\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'DATE_FORMAT(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # FORMAT_DATETIME(fmt, dt) → DATE_FORMAT(dt, fmt)
            {
                'pattern': r'\bFORMAT_DATETIME\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'DATE_FORMAT(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # FORMAT_TIME(fmt, time) → DATE_FORMAT(time, fmt)
            {
                'pattern': r'\bFORMAT_TIME\s*\(([^,]+),\s*([^)]+)\)',
                'replacement': r'DATE_FORMAT(\2, \1)',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # FORMAT('%s %d', a, b) → FORMAT_STRING('%s %d', a, b)
            {
                'pattern': r'\bFORMAT\s*\(',
                'replacement': r'FORMAT_STRING(',
                'flags': re.IGNORECASE, 'priority': 7,
            },

            # ── TIME TRAVEL ───────────────────────────────────────────────────
            # FOR SYSTEM_TIME AS OF → TIMESTAMP AS OF
            # 2026: Databricks strictly enforces deletedFileRetentionDuration
            {
                'pattern': r'\bFOR\s+SYSTEM_TIME\s+AS\s+OF\b',
                'replacement': (
                    r'TIMESTAMP AS OF'
                    r' /* NOTE: Ensure table deletedFileRetentionDuration >= time travel interval */'
                ),
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # ── STRING (additional) ────────────────────────────────────────────
            # CHAR_LENGTH → CHARACTER_LENGTH (same in Databricks; normalize)
            {
                'pattern': r'\bCHAR_LENGTH\s*\(',
                'replacement': r'CHARACTER_LENGTH(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            # CODE_POINTS_TO_STRING → no direct equiv
            {
                'pattern': r'\bCODE_POINTS_TO_STRING\s*\(([^)]+)\)',
                'replacement': r'\1 /* TODO: CODE_POINTS_TO_STRING has no Databricks equivalent */',
                'flags': re.IGNORECASE, 'priority': 7,
            },
            # TO_CODE_POINTS → no direct equiv
            {
                'pattern': r'\bTO_CODE_POINTS\s*\(([^)]+)\)',
                'replacement': r'\1 /* TODO: TO_CODE_POINTS has no Databricks equivalent */',
                'flags': re.IGNORECASE, 'priority': 7,
            },
            # COLLATE(s, spec) → COLLATE(s, spec)  (similar but specs differ)
            # 2026: Databricks now supports collations natively
            {
                'pattern': r"\bCOLLATE\s+'und:ci'",
                'replacement': r'COLLATE UTF8_LCASE',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r"\bCOLLATE\s+'und:cs'",
                'replacement': r'COLLATE UTF8_BINARY',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r"\bCOLLATE\s+'und'",
                'replacement': r'COLLATE UTF8_BINARY',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r"\bSTRING\s+COLLATE\s+'und:ci'",
                'replacement': r'STRING COLLATE UTF8_LCASE',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r"\bSTRING\s+COLLATE\s+'und:cs'",
                'replacement': r'STRING COLLATE UTF8_BINARY',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r"\bSTRING\s+COLLATE\s+'und'",
                'replacement': r'STRING COLLATE UTF8_BINARY',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            # TRANSLATE(s, from, to) → TRANSLATE(s, from, to)  (same)
            # (already native in Databricks)
            # SOUNDEX → SOUNDEX (same)
            # Unicode / ASCII → already same

            # ── WINDOW FUNCTIONS (additional) ──────────────────────────────────
            {
                'pattern': r'\bFIRST_VALUE\s*\(\s*(.+?)\s+IGNORE\s+NULLS\s*\)',
                'replacement': r'FIRST_VALUE(\1) IGNORE NULLS',
                'flags': re.IGNORECASE,
                'priority': 10,
            },
            {
                'pattern': r'\bLAST_VALUE\s*\(\s*(.+?)\s+IGNORE\s+NULLS\s*\)',
                'replacement': r'LAST_VALUE(\1) IGNORE NULLS',
                'flags': re.IGNORECASE,
                'priority': 10,
            },
            # QUALIFY → Databricks supports QUALIFY natively since DBR 12.2+
            # No change needed, but flag for pre-12.2 compatibility:
            # { 'pattern': r'\bQUALIFY\b', ... }

            # ── MERGE (syntax) ─────────────────────────────────────────────────
            # MERGE INTO ... WHEN NOT MATCHED BY SOURCE → DELETE
            # Databricks supports MERGE with same syntax, but BQ-specific
            # WHEN NOT MATCHED BY SOURCE is not available:
            {
                'pattern': r'\bWHEN\s+NOT\s+MATCHED\s+BY\s+SOURCE\b',
                'replacement': r'WHEN NOT MATCHED BY SOURCE /* TODO: Databricks may not support this clause; use DELETE instead */',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── GEOSPATIAL FUNCTIONS (2026 — native support) ─────────────────
            # These are now 1:1 in Databricks 2026; normalize to lowercase
            {
                'pattern': r'\bST_AZIMUTH\s*\(',
                'replacement': r'st_azimuth(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_BOUNDARY\s*\(',
                'replacement': r'st_boundary(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_CLOSESTPOINT\s*\(',
                'replacement': r'st_closestpoint(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_GEOGFROMEWKT\s*\(',
                'replacement': r'st_geomfromewkt(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_GEOGFROMTEXT\s*\(',
                'replacement': r'st_geomfromwkt(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_GEOGFROMWKB\s*\(',
                'replacement': r'st_geomfromwkb(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_GEOGPOINT\s*\(',
                'replacement': r'st_point(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_ASTEXT\s*\(',
                'replacement': r'st_astext(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_ASBINARY\s*\(',
                'replacement': r'st_asbinary(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_DISTANCE\s*\(',
                'replacement': r'st_distance(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_AREA\s*\(',
                'replacement': r'st_area(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_LENGTH\s*\(',
                'replacement': r'st_length(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_CONTAINS\s*\(',
                'replacement': r'st_contains(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_INTERSECTS\s*\(',
                'replacement': r'st_intersects(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_UNION\s*\(',
                'replacement': r'st_union(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_INTERSECTION\s*\(',
                'replacement': r'st_intersection(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_BUFFER\s*\(',
                'replacement': r'st_buffer(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_CENTROID\s*\(',
                'replacement': r'st_centroid(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_WITHIN\s*\(',
                'replacement': r'st_within(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_COVERS\s*\(',
                'replacement': r'st_covers(',
                'flags': re.IGNORECASE, 'priority': 9,
            },
            {
                'pattern': r'\bST_COVEREDBY\s*\(',
                'replacement': r'st_coveredby(',
                'flags': re.IGNORECASE, 'priority': 9,
            },

            # ── APPROXIMATE SKETCH FUNCTIONS (2026) ──────────────────────────
            # APPROX_COUNT_DISTINCT → APPROX_COUNT_DISTINCT (already same)
            {
                'pattern': r'\bAPPROX_COUNT_DISTINCT\s*\(',
                'replacement': r'APPROX_COUNT_DISTINCT(',
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # ── BITWISE (2026) ───────────────────────────────────────────────
            # BIT_AND → BIT_AND (same), BITMAP_AND_AGG now available
            {
                'pattern': r'\bBIT_AND\s*\(',
                'replacement': r'BIT_AND(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bBIT_OR\s*\(',
                'replacement': r'BIT_OR(',
                'flags': re.IGNORECASE, 'priority': 8,
            },
            {
                'pattern': r'\bBIT_XOR\s*\(',
                'replacement': r'BIT_XOR(',
                'flags': re.IGNORECASE, 'priority': 8,
            },

            # ── TRANSACTION SUPPORT (2026) ───────────────────────────────────
            # Multi-statement transactions: 1:1 syntax mapping
            {
                'pattern': r'\bBEGIN\s+TRANSACTION\b',
                'replacement': r'BEGIN TRANSACTION',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bCOMMIT\s+TRANSACTION\b',
                'replacement': r'COMMIT TRANSACTION',
                'flags': re.IGNORECASE, 'priority': 10,
            },
            {
                'pattern': r'\bROLLBACK\s+TRANSACTION\b',
                'replacement': r'ROLLBACK TRANSACTION',
                'flags': re.IGNORECASE, 'priority': 10,
            },

            # ── SQL SCRIPTING (2026 GA) ──────────────────────────────────────
            # BQ scripting syntax is now nearly identical in Databricks 2026
            # DECLARE var TYPE DEFAULT val → same
            {
                'pattern': r'\bDECLARE\s+(\w+)\s+DEFAULT\b',
                'replacement': r'DECLARE \1 DEFAULT',
                'flags': re.IGNORECASE, 'priority': 10,
            },
        ]

        builtins.sort(key=lambda r: r.get('priority', 0), reverse=True)

        # ── Integrate Edge Cases as Highest Priority (110) ──────────
        for edge in edge_cases:
            bq = (edge.get("bq") or "").strip()
            dbx = (edge.get("dbx") or "").strip()
            if not bq or not dbx or bq == dbx:
                continue
            
            # Escape the string first
            pattern_str = re.escape(bq)
            repl_str = dbx
            
            # Find all <Something> placeholders and replace them with capture groups (\1, \2 etc.)
            placeholders = re.findall(r'<([^>]+)>', pattern_str)
            
            current_group = 1
            for p in placeholders:
                pattern_str = pattern_str.replace(f'<{p}>', f'(.+?)', 1)
                repl_str = repl_str.replace(f'<{p}>', f'\\{current_group}')
                current_group += 1

            if re.match(r'^\w', bq):
                pattern_str = r'\b' + pattern_str

            builtins.append({
                'pattern': pattern_str,
                'replacement': repl_str,
                'flags': re.IGNORECASE,
                'priority': 110,
                'source': 'edge_case',
            })

        # ── Integrate CSV/Functions configs rules at priority 100 ──────────
        for row in rules_list:
            bq = (row.get("bigquery_syntax") or "").strip()
            db = (row.get("databricks_sql_syntax") or "").strip()
            if not bq or not db or bq == db:
                continue
            
            # Avoid toxic blanket overrides for complex syntaxes (e.g., EXTRACT(YEAR FROM date))
            if ' ' in bq or ('(' in bq and not bq.endswith(')')):
                continue
            
            # Extract the function name from BQ syntax (e.g. "FUNC(..." -> "FUNC")
            fn_match = re.match(r'^(\w+)\s*\(', bq)
            if not fn_match:
                continue
            fn_name = fn_match.group(1)

            # Never allow spreadsheet rules to override native Databricks unix/date epoch functions.
            if fn_name.upper() in protected_native_fns:
                continue
            
            # Build a simple pattern: \bFUNC\s*\( → replacement prefix
            pattern = r'\b' + re.escape(fn_name) + r'\s*\('

            # Only apply CSV-derived prefix rewrites for simple one-layer function mappings.
            # Complex targets like flatten(collect_list(x)) cannot be represented by a
            # plain prefix replacement and would incorrectly collapse to flatten(x).
            if not re.match(r'^\w+\s*\([^()]*\)\s*$', db):
                continue
            
            # Extract the replacement function call prefix
            repl_match = re.match(r'^(\w+)\s*\(', db)
            if repl_match:
                replacement = repl_match.group(1) + '('
                builtins.append({
                    'pattern': pattern,
                    'replacement': replacement,
                    'flags': re.IGNORECASE,
                    'priority': 100,
                    'source': 'csv_functions',
                })
        
        # ── Filter out hardcoded builtins if Excel explicitly defines them ──────────
        excel_base_fns = set()
        for rule in builtins:
            if rule.get('source') in ('edge_case', 'csv_functions'):
                # Extract first word after \b (or start) as base fn
                m = re.search(r'^[\\^]*b?([A-Za-z0-9_]+)', rule['pattern'], re.IGNORECASE)
                if m:
                    excel_base_fns.add(m.group(1).upper())
        
        final_builtins = []
        for rule in builtins:
            if rule.get('source') in ('edge_case', 'csv_functions'):
                final_builtins.append(rule)
                continue
                
            m = re.search(r'^[\\^]*b?([A-Za-z0-9_]+)', rule['pattern'], re.IGNORECASE)
            if m:
                base_fn = m.group(1).upper()
                # Skip built-ins if Excel dynamically overwrites them (unless they are broad families)
                if base_fn in excel_base_fns and base_fn not in (
                    'EXTRACT', 'DATE', 'TIMESTAMP', 'JSON_EXTRACT', 'ARRAY',
                    *protected_native_fns,
                ):
                    continue
                    
            final_builtins.append(rule)
            
        builtins = final_builtins

        builtins.sort(key=lambda r: r.get('priority', 0), reverse=True)
        return builtins

    @staticmethod
    def _extract_balanced_arg(sql: str, start: int) -> str:
        """Extract the content between parentheses starting at `start`.

        `start` should point to the first character *after* the opening '('.
        Returns the substring up to (but not including) the matching close ')'.
        """
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
    def _normalize_date_add_sub_interval(sql: str) -> str:
        """Convert DATE_ADD/DATE_SUB(date_expr, INTERVAL expr DAY) to Spark-style DATE_ADD/DATE_SUB(date_expr, expr)."""

        def _split_top_level_args(args_text: str):
            depth = 0
            for i, ch in enumerate(args_text):
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                elif ch == ',' and depth == 0:
                    return args_text[:i], args_text[i + 1:]
            return None, None

        for fn in ("DATE_ADD", "DATE_SUB"):
            pat = re.compile(rf'\b{fn}\s*\(', re.IGNORECASE)
            idx = 0
            while True:
                m = pat.search(sql, idx)
                if not m:
                    break

                inner_start = m.end()
                inner_args = RuleEngine._extract_balanced_arg(sql, inner_start)
                if inner_start + len(inner_args) >= len(sql):
                    idx = m.end()
                    continue

                arg1, arg2 = _split_top_level_args(inner_args)
                if arg1 is None:
                    idx = m.end()
                    continue

                interval_match = re.match(
                    r'^\s*INTERVAL\s+(.+?)\s+DAYS?\s*$',
                    arg2,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if not interval_match:
                    idx = m.end()
                    continue

                day_expr = interval_match.group(1).strip()
                simple_num = re.match(r"^'?(\d+)'?$", day_expr)
                if simple_num:
                    day_expr = simple_num.group(1)

                replacement = f"{fn}({arg1.strip()}, {day_expr})"
                end_pos = inner_start + len(inner_args) + 1
                sql = sql[:m.start()] + replacement + sql[end_pos:]
                idx = m.start() + len(replacement)

        return sql

    @staticmethod
    def _add_to_json_map_param(sql: str) -> str:
        """Add map('ignoreNullFields','false') to bare to_json(expr) calls.

        Uses paren-aware matching so nested args like to_json(struct(...)) work.
        """
        pat = re.compile(r'\bto_json\s*\(', re.IGNORECASE)
        result = []
        pos = 0
        while pos < len(sql):
            m = pat.search(sql, pos)
            if not m:
                result.append(sql[pos:])
                break
            result.append(sql[pos:m.start()])
            # Find matching close paren for to_json(
            inner_start = m.end()
            arg = RuleEngine._extract_balanced_arg(sql, inner_start)
            end_pos = inner_start + len(arg) + 1  # +1 for ')'
            # Check for top-level comma (= already has multiple params)
            has_top_level_comma = False
            depth = 0
            for ch in arg:
                if ch == '(':
                    depth += 1
                elif ch == ')':
                    depth -= 1
                elif ch == ',' and depth == 0:
                    has_top_level_comma = True
                    break
            if has_top_level_comma:
                result.append(sql[m.start():end_pos])
            else:
                result.append(f"to_json({arg.strip()}, map('ignoreNullFields', 'false'))")
            pos = end_pos
        return ''.join(result)

    @staticmethod
    def _normalize_unnest_wrapper(sql: str) -> str:
        """Normalize SELECT * FROM (SELECT UNNEST(...) AS x) _unnest to SELECT x FROM UNNEST(...) AS x."""
        pattern = re.compile(
            r'SELECT\s+\*\s+FROM\s*\(\s*SELECT\s+UNNEST\s*\((?P<inner>[\s\S]*?)\)\s+AS\s+(?P<alias>\w+)\s*\)\s*(?:AS\s+\w+)?',
            re.IGNORECASE,
        )

        def _repl(m: re.Match) -> str:
            alias = m.group('alias')
            inner = m.group('inner').strip()
            return f"SELECT {alias} FROM UNNEST({inner}) AS {alias}"

        sql = pattern.sub(_repl, sql)

        # Fragment form: FROM (SELECT UNNEST(...) AS x) _unnest -> FROM UNNEST(...) AS x
        from_pattern = re.compile(
            r'FROM\s*\(\s*SELECT\s+UNNEST\s*\((?P<inner>[\s\S]*?)\)\s+AS\s+(?P<alias>\w+)\s*\)\s*(?:AS\s+\w+)?',
            re.IGNORECASE,
        )

        def _from_repl(m: re.Match) -> str:
            inner = m.group('inner').strip()
            alias = m.group('alias')
            return f"FROM UNNEST({inner}) AS {alias}"

        return from_pattern.sub(_from_repl, sql)

    @staticmethod
    def _normalize_current_date_arithmetic(sql: str) -> str:
        """Normalize CURRENT_DATE +/- n and DATE_SUB/DATE_ADD forms to canonical Databricks style."""
        sql = re.sub(
            r'\bCURRENT_DATE\s*(?:\(\s*\))?\s*-\s*(\d+)\b',
            r'date_sub(current_date(), \1)',
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r'\bCURRENT_DATE\s*(?:\(\s*\))?\s*\+\s*(\d+)\b',
            r'date_add(current_date(), \1)',
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r'\bDATE_SUB\s*\(\s*CURRENT_DATE\s*(?:\(\s*\))?\s*,\s*(\d+)\s*\)',
            r'date_sub(current_date(), \1)',
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r'\bDATE_ADD\s*\(\s*CURRENT_DATE\s*(?:\(\s*\))?\s*,\s*(\d+)\s*\)',
            r'date_add(current_date(), \1)',
            sql,
            flags=re.IGNORECASE,
        )
        return sql

    @staticmethod
    def _normalize_generate_date_array_explode(sql: str) -> str:
        """Canonicalize explode(sequence(to_date(...), date_sub(current_date(), n), interval 1 day)) across style variants."""
        pattern = re.compile(
            r'explode\s*\(\s*sequence\s*\(\s*'
            r'to_date\s*\((?P<start>[^\)]+)\)\s*,\s*'
            r'(?:(?:date_sub\s*\(\s*current_date\s*(?:\(\s*\))?\s*,\s*(?P<n1>\d+)\s*\))'
            r'|(?:current_date\s*(?:\(\s*\))?\s*-\s*(?P<n2>\d+)))\s*,\s*'
            r'interval\s*\'?1\'?\s*day(?:s)?\s*\)\s*\)',
            re.IGNORECASE,
        )

        def _repl(m: re.Match) -> str:
            start = m.group('start').strip()
            n = m.group('n1') or m.group('n2')
            return f"explode(sequence(to_date({start}), date_sub(current_date(), {n}), interval 1 day))"

        return pattern.sub(_repl, sql)

    @staticmethod
    def _split_top_level_commas(text: str) -> List[str]:
        """Split comma-separated SQL expressions while respecting nested parentheses."""
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

    @staticmethod
    def _rewrite_array_select_struct(sql: str) -> str:
        """Rewrite ARRAY(SELECT [DISTINCT] STRUCT(...) FROM ...) to collect_list/collect_set form."""

        def _build_struct_payload(struct_args: str) -> str:
            args = RuleEngine._split_top_level_commas(struct_args)
            if not args:
                return ""

            # User-requested behavior: STRUCT(single_col) -> collect_list/set(single_col)
            if len(args) == 1:
                single = args[0]
                alias_match = re.match(r'(.+?)\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$', single, flags=re.IGNORECASE)
                return (alias_match.group(1) if alias_match else single).strip()

            kv_parts: List[str] = []
            for idx, arg in enumerate(args, start=1):
                alias_match = re.match(r'(.+?)\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$', arg, flags=re.IGNORECASE)
                if alias_match:
                    expr = alias_match.group(1).strip()
                    field_name = alias_match.group(2).strip()
                else:
                    expr = arg.strip()
                    simple_name = re.match(r'.*?([A-Za-z_][A-Za-z0-9_]*)\s*$', expr)
                    field_name = simple_name.group(1) if simple_name else f"field_{idx}"
                kv_parts.append(f"'{field_name}', {expr}")

            return f"named_struct({', '.join(kv_parts)})"

        array_pat = re.compile(r'\bARRAY\s*\(', re.IGNORECASE)
        idx = 0
        while True:
            m = array_pat.search(sql, idx)
            if not m:
                break

            inner_start = m.end()
            inner = RuleEngine._extract_balanced_arg(sql, inner_start)
            if inner_start + len(inner) >= len(sql):
                idx = m.end()
                continue

            inner_strip = inner.strip()
            # sqlglot or prior rewrites may wrap the inner SELECT with one extra pair of parens.
            if inner_strip.startswith('(') and inner_strip.endswith(')'):
                probe = inner_strip[1:-1].strip()
                if re.match(r'^SELECT\b', probe, flags=re.IGNORECASE):
                    inner_strip = probe
            select_match = re.match(r'^SELECT\s+(DISTINCT\s+)?', inner_strip, flags=re.IGNORECASE | re.DOTALL)
            if not select_match:
                idx = m.end()
                continue

            distinct = bool(select_match.group(1))
            tail = inner_strip[select_match.end():].lstrip()

            # Support both SELECT STRUCT(...) and SELECT AS STRUCT ... shapes.
            if re.match(r'^AS\s+STRUCT\b', tail, flags=re.IGNORECASE):
                tail = re.sub(r'^AS\s+STRUCT\b', '', tail, flags=re.IGNORECASE).lstrip()

            struct_match = re.match(r'^STRUCT\s*\(', tail, flags=re.IGNORECASE | re.DOTALL)
            if not struct_match:
                idx = m.end()
                continue

            struct_args_start = struct_match.end()
            struct_args = RuleEngine._extract_balanced_arg(tail, struct_args_start)
            struct_close_pos = struct_args_start + len(struct_args)
            if struct_close_pos >= len(tail):
                idx = m.end()
                continue

            after_struct = tail[struct_close_pos + 1:].lstrip()
            if not re.match(r'^FROM\b', after_struct, flags=re.IGNORECASE):
                idx = m.end()
                continue

            payload = _build_struct_payload(struct_args)
            if not payload:
                idx = m.end()
                continue

            agg_fn = 'collect_set' if distinct else 'collect_list'
            replacement = f"(SELECT {agg_fn}({payload}) {after_struct})"
            end_pos = inner_start + len(inner) + 1
            sql = sql[:m.start()] + replacement + sql[end_pos:]
            idx = m.start() + len(replacement)

        return sql

    @staticmethod
    def _rewrite_array_select_generic(sql: str) -> str:
        """Rewrite ARRAY(SELECT [DISTINCT] expr FROM ...) to collect_list/collect_set form."""
        array_pat = re.compile(r'\bARRAY\s*\(', re.IGNORECASE)
        idx = 0
        while True:
            m = array_pat.search(sql, idx)
            if not m:
                break

            inner_start = m.end()
            inner = RuleEngine._extract_balanced_arg(sql, inner_start)
            if inner_start + len(inner) >= len(sql):
                idx = m.end()
                continue

            inner_strip = inner.strip()
            if inner_strip.startswith('(') and inner_strip.endswith(')'):
                probe = inner_strip[1:-1].strip()
                if re.match(r'^SELECT\b', probe, flags=re.IGNORECASE):
                    inner_strip = probe

            select_match = re.match(r'^SELECT\s+(DISTINCT\s+)?', inner_strip, flags=re.IGNORECASE | re.DOTALL)
            if not select_match:
                idx = m.end()
                continue

            distinct = bool(select_match.group(1))
            tail = inner_strip[select_match.end():].lstrip()

            # STRUCT-shaped arrays are handled by the dedicated rewrite.
            if re.match(r'^(AS\s+STRUCT\b|STRUCT\s*\()', tail, flags=re.IGNORECASE):
                idx = m.end()
                continue

            from_match = re.search(r'\bFROM\b', tail, flags=re.IGNORECASE)
            if not from_match:
                idx = m.end()
                continue

            value_expr = tail[:from_match.start()].strip()
            from_tail = tail[from_match.start():].strip()
            if not value_expr or not from_tail:
                idx = m.end()
                continue

            agg_fn = 'collect_set' if distinct else 'collect_list'
            replacement = f"(SELECT {agg_fn}({value_expr}) {from_tail})"
            end_pos = inner_start + len(inner) + 1
            sql = sql[:m.start()] + replacement + sql[end_pos:]
            idx = m.start() + len(replacement)

        return sql

    @staticmethod
    def _rewrite_array_agg_top1_to_min_max_by(sql: str) -> str:
        """Rewrite ARRAY_AGG(v ORDER BY k DESC/ASC LIMIT 1)[SAFE_OFFSET(0)] to MAX_BY/MIN_BY."""

        def _find_top_level_keyword(text: str, keyword: str) -> int:
            depth = 0
            in_single = False
            in_double = False
            k = keyword.upper()
            i = 0
            while i < len(text):
                ch = text[i]
                if ch == "'" and not in_double:
                    in_single = not in_single
                elif ch == '"' and not in_single:
                    in_double = not in_double
                elif not in_single and not in_double:
                    if ch == '(':
                        depth += 1
                    elif ch == ')':
                        depth -= 1
                    elif depth == 0 and text[i:i + len(k)].upper() == k:
                        prev_ok = i == 0 or text[i - 1].isspace()
                        next_i = i + len(k)
                        next_ok = next_i >= len(text) or text[next_i].isspace()
                        if prev_ok and next_ok:
                            return i
                i += 1
            return -1

        def _has_top_level_comma(text: str) -> bool:
            depth = 0
            in_single = False
            in_double = False
            for ch in text:
                if ch == "'" and not in_double:
                    in_single = not in_single
                elif ch == '"' and not in_single:
                    in_double = not in_double
                elif not in_single and not in_double:
                    if ch == '(':
                        depth += 1
                    elif ch == ')':
                        depth -= 1
                    elif ch == ',' and depth == 0:
                        return True
            return False

        agg_pat = re.compile(r'\bARRAY_AGG\s*\(', re.IGNORECASE)
        idx = 0

        while True:
            m = agg_pat.search(sql, idx)
            if not m:
                break

            inner_start = m.end()
            inner = RuleEngine._extract_balanced_arg(sql, inner_start)
            end_pos = inner_start + len(inner)
            if end_pos >= len(sql):
                idx = m.end()
                continue

            suffix = sql[end_pos + 1:]
            off_match = re.match(
                r'\s*\[\s*(?:SAFE_OFFSET|OFFSET)\s*\(\s*0\s*\)\s*\]',
                suffix,
                flags=re.IGNORECASE,
            )
            if not off_match:
                idx = m.end()
                continue

            order_pos = _find_top_level_keyword(inner, 'ORDER BY')
            if order_pos == -1:
                idx = m.end()
                continue

            value_expr = inner[:order_pos].strip()
            if re.match(r'^DISTINCT\b', value_expr, flags=re.IGNORECASE):
                idx = m.end()
                continue

            order_tail = inner[order_pos + len('ORDER BY'):].strip()
            limit_pos = _find_top_level_keyword(order_tail, 'LIMIT')
            if limit_pos == -1:
                idx = m.end()
                continue

            order_expr = order_tail[:limit_pos].strip()
            limit_arg = order_tail[limit_pos + len('LIMIT'):].strip()
            if not re.match(r'^1\b', limit_arg, flags=re.IGNORECASE):
                idx = m.end()
                continue

            # MAX_BY/MIN_BY only supports one ordering key.
            if _has_top_level_comma(order_expr):
                idx = m.end()
                continue

            dir_match = re.search(r'\s+(ASC|DESC)\s*$', order_expr, flags=re.IGNORECASE)
            direction = dir_match.group(1).upper() if dir_match else 'ASC'
            key_expr = re.sub(r'\s+(ASC|DESC)\s*$', '', order_expr, flags=re.IGNORECASE).strip()

            if not value_expr or not key_expr:
                idx = m.end()
                continue

            agg_fn = 'MAX_BY' if direction == 'DESC' else 'MIN_BY'
            replacement = f"{agg_fn}({value_expr}, {key_expr})"

            full_end = end_pos + 1 + off_match.end()
            sql = sql[:m.start()] + replacement + sql[full_end:]
            idx = m.start() + len(replacement)

        return sql

    @staticmethod
    def _rewrite_array_agg_order_by(sql: str) -> str:
        """Rewrite ARRAY_AGG(value ORDER BY key [DESC]) preserving order in Databricks."""

        def _find_top_level_order_by(text: str) -> int:
            depth = 0
            in_single = False
            in_double = False
            i = 0
            while i < len(text):
                ch = text[i]
                if ch == "'" and not in_double:
                    in_single = not in_single
                elif ch == '"' and not in_single:
                    in_double = not in_double
                elif not in_single and not in_double:
                    if ch == '(':
                        depth += 1
                    elif ch == ')':
                        depth -= 1
                    elif depth == 0 and text[i:i + 8].upper() == 'ORDER BY':
                        prev_ok = i == 0 or text[i - 1].isspace()
                        if prev_ok:
                            return i
                i += 1
            return -1

        def _first_order_term(order_by_text: str) -> str:
            depth = 0
            in_single = False
            in_double = False
            for i, ch in enumerate(order_by_text):
                if ch == "'" and not in_double:
                    in_single = not in_single
                elif ch == '"' and not in_single:
                    in_double = not in_double
                elif not in_single and not in_double:
                    if ch == '(':
                        depth += 1
                    elif ch == ')':
                        depth -= 1
                    elif ch == ',' and depth == 0:
                        return order_by_text[:i].strip()
            return order_by_text.strip()

        agg_pat = re.compile(r'\bARRAY_AGG\s*\(', re.IGNORECASE)
        idx = 0
        while True:
            m = agg_pat.search(sql, idx)
            if not m:
                break

            inner_start = m.end()
            inner = RuleEngine._extract_balanced_arg(sql, inner_start)
            if inner_start + len(inner) >= len(sql):
                idx = m.end()
                continue

            order_pos = _find_top_level_order_by(inner)
            if order_pos == -1:
                idx = m.end()
                continue

            value_expr = inner[:order_pos].strip()
            order_expr_full = _first_order_term(inner[order_pos + len('ORDER BY'):].strip())

            is_distinct = False
            if re.match(r'^DISTINCT\b', value_expr, flags=re.IGNORECASE):
                is_distinct = True
                value_expr = re.sub(r'^DISTINCT\s+', '', value_expr, flags=re.IGNORECASE).strip()

            dir_match = re.search(r'\s+(ASC|DESC)\s*$', order_expr_full, flags=re.IGNORECASE)
            direction = dir_match.group(1).upper() if dir_match else 'ASC'
            key_expr = re.sub(r'\s+(ASC|DESC)\s*$', '', order_expr_full, flags=re.IGNORECASE).strip()

            if not value_expr or not key_expr:
                idx = m.end()
                continue

            # DISTINCT + ORDER BY is only safely representable with collect_set when
            # the ordering key is the same as the aggregated value expression.
            if is_distinct:
                if re.sub(r'\s+', '', key_expr).lower() != re.sub(r'\s+', '', value_expr).lower():
                    idx = m.end()
                    continue
                sorted_vals = f"array_sort(collect_set({value_expr}))"
                if direction == 'DESC':
                    sorted_vals = f"reverse({sorted_vals})"
                replacement = sorted_vals
                end_pos = inner_start + len(inner) + 1
                sql = sql[:m.start()] + replacement + sql[end_pos:]
                idx = m.start() + len(replacement)
                continue

            sorted_structs = (
                "array_sort(collect_list(named_struct('__ord', "
                f"{key_expr}, '__val', {value_expr})))"
            )
            if direction == 'DESC':
                sorted_structs = f"reverse({sorted_structs})"

            replacement = f"transform({sorted_structs}, x -> x.__val)"
            end_pos = inner_start + len(inner) + 1
            sql = sql[:m.start()] + replacement + sql[end_pos:]
            idx = m.start() + len(replacement)

        return sql

    def apply_rules(self, sql: str) -> str:
        """Apply all deterministic pattern rules to SQL."""
        import logging
        logger = logging.getLogger(__name__)
        from .ast_transformer import _extract_jinja, _restore_jinja
        sql, jinja_map = _extract_jinja(sql)

        # Top-1 ARRAY_AGG access patterns can map directly to MAX_BY/MIN_BY.
        sql = self._rewrite_array_agg_top1_to_min_max_by(sql)

        # Remove problematic partitioning keys from RANK/DENSE_RANK window defs.
        # Specifically drop any `extract.rank` occurrences from PARTITION BY lists
        # because including a rank column in the partition makes deduplication
        # ineffective (each row becomes its own partition).
        def _fix_rank_partition(query: str) -> str:
            pat = re.compile(
                r'\b(?P<fn>RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\(\s*PARTITION\s+BY\s+(?P<parts>[^)]*?)\s*ORDER\s+BY\s+(?P<order>[^)]+?)\)',
                re.IGNORECASE,
            )

            def _repl(m: re.Match) -> str:
                fn = m.group('fn')
                parts = m.group('parts')
                order = m.group('order')
                # split top-level commas
                part_list = [p.strip() for p in re.split(r'\s*,\s*', parts) if p.strip()]
                filtered = [p for p in part_list if not re.search(r'\bextract\.rank\b', p, re.IGNORECASE)]
                if filtered:
                    return f"{fn}() OVER (PARTITION BY {', '.join(filtered)} ORDER BY {order})"
                # If everything was removed, drop PARTITION BY entirely
                return f"{fn}() OVER (ORDER BY {order})"

            return pat.sub(_repl, query)

        sql = _fix_rank_partition(sql)

        # ARRAY_AGG(value ORDER BY key) must be handled before generic ARRAY_AGG rewrites.
        sql = self._rewrite_array_agg_order_by(sql)

        # Guard rails before generic rules: DISTINCT ARRAY_AGG must become collect_set,
        # and CSV overrides may emit collect_list(DISTINCT ...) which is invalid in target semantics.
        sql = re.sub(
            r'\bARRAY_AGG\s*\(\s*DISTINCT\s+([^)]+?)\)',
            r'collect_set(\1)',
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r'\bcollect_list\s*\(\s*DISTINCT\s+([^)]+?)\)',
            r'collect_set(\1)',
            sql,
            flags=re.IGNORECASE,
        )

        for rule in self.pattern_rules:
            pattern = rule['pattern']
            replacement = rule['replacement']
            flags = rule.get('flags', 0)
            try:
                prev = sql
                if callable(replacement):
                    sql = re.sub(pattern, replacement, sql, flags=flags)
                else:
                    sql = re.sub(pattern, replacement, sql, flags=flags)
                if sql != prev:
                    logger.debug("Rule fired: %s", pattern[:80])
            except Exception as exc:
                logger.debug("Rule failed: %s — %s", pattern[:60], exc)
                continue

        # Final canonicalization pass for common style-variant outputs.
        sql = self._rewrite_array_select_struct(sql)
        sql = self._rewrite_array_select_generic(sql)
        sql = self._normalize_date_add_sub_interval(sql)
        sql = self._normalize_unnest_wrapper(sql)
        sql = self._normalize_generate_date_array_explode(sql)
        sql = self._normalize_current_date_arithmetic(sql)

        sql = _restore_jinja(sql, jinja_map)
        return sql

    def apply_function_translation(self, sql: str) -> str:
        """Second-pass: type names, raw strings, trailing commas."""
        from .ast_transformer import _extract_jinja, _restore_jinja

        def _rewrite_qualify_row_number(query: str) -> str:
            """Rewrite BigQuery QUALIFY ROW_NUMBER() to Databricks-compatible subquery form."""
            # Pattern 1: QUALIFY ROW_NUMBER() OVER (...) = N
            pat1 = re.compile(
                r'^\s*SELECT\s+(?P<select>[\s\S]+?)\s+FROM\s+(?P<from>[\s\S]+?)\s+'
                r'QUALIFY\s+(?:ROW_NUMBER|RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\((?P<over>[\s\S]+?)\)\s*=\s*(?P<n>\d+)\s*;?\s*$',
                re.IGNORECASE,
            )
            # Pattern 2: QUALIFY N = ROW_NUMBER() OVER (...)  (reversed comparison)
            pat2 = re.compile(
                r'^\s*SELECT\s+(?P<select>[\s\S]+?)\s+FROM\s+(?P<from>[\s\S]+?)\s+'
                r'QUALIFY\s+(?P<n>\d+)\s*=\s*(?:ROW_NUMBER|RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\((?P<over>[\s\S]+?)\)\s*;?\s*$',
                re.IGNORECASE,
            )
            m = pat1.match(query)
            if not m:
                m = pat2.match(query)
            if not m:
                return query
            select_expr = m.group('select').strip()
            from_expr = m.group('from').strip()
            over_expr = m.group('over').strip()
            n_val = m.group('n')
            return (
                "SELECT *\n"
                "FROM (\n"
                f"  SELECT {select_expr},\n"
                f"    ROW_NUMBER() OVER ({over_expr}) AS rn\n"
                f"  FROM {from_expr}\n"
                ") t\n"
                f"WHERE rn = {n_val}"
            )

        sql, jinja_map = _extract_jinja(sql)

        # Normalize QUALIFY usages that reference a SELECT alias back to the
        # original window expression where possible. Example: `QUALIFY insertrank = 1`
        # when `ROW_NUMBER() OVER (...) AS insertrank` exists in the SELECT list
        # will be rewritten to `QUALIFY ROW_NUMBER() OVER (...) = 1` (safer).
        def _rewrite_qualify_alias_to_expr(query: str) -> str:
            m = re.search(r"\bQUALIFY\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\d+)", query, flags=re.IGNORECASE)
            if not m:
                return query
            alias = m.group(1)
            val = m.group(2)
            # locate select list
            sel_m = re.search(r"^\s*SELECT\s+(?P<select>[\s\S]+?)\s+FROM\b", query, flags=re.IGNORECASE)
            if not sel_m:
                return query
            select_block = sel_m.group('select')
            # find a window-function expression aliased to `alias`
            expr_pat = re.compile(
                r"(?P<expr>\b(?:ROW_NUMBER|RANK|DENSE_RANK|FIRST_VALUE|LAST_VALUE)\s*\([^)]*\)\s*OVER\s*\([^)]*\))\s+AS\s+"
                + re.escape(alias),
                flags=re.IGNORECASE,
            )
            em = expr_pat.search(select_block)
            if not em:
                return query
            expr = em.group('expr')
            return re.sub(rf"\bQUALIFY\s+{re.escape(alias)}\s*=\s*{re.escape(val)}",
                          f"QUALIFY {expr} = {val}", query, flags=re.IGNORECASE)

        sql = _rewrite_qualify_alias_to_expr(sql)
        # If we have a QUALIFY clause, try to rewrite ROW_NUMBER()/RANK() patterns
        # into a subquery filter for engines that don't support QUALIFY.
        sql = _rewrite_qualify_row_number(sql)

        # DATE_ADD(d, -N) -> date_sub(d, N)
        sql = re.sub(
            r'\bdate_add\s*\(\s*([^,]+?)\s*,\s*(-\d+)\s*\)',
            lambda m: f"date_sub({m.group(1)}, {m.group(2)[1:]})",
            sql, flags=re.IGNORECASE,
        )

        # DATE(expr) -> to_date(expr)
        sql = re.sub(r'\bDATE\s*\(([^)]+)\)', r'to_date(\1)', sql, flags=re.IGNORECASE)

        # Column/DDL type name remaps (comprehensive)
        sql = re.sub(r'\bBIGNUMERIC\b', 'DECIMAL(38,9)', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bNUMERIC\b(?!\s*\()', 'DECIMAL(38,9)', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bFLOAT64\b', 'DOUBLE', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bINT64\b', 'BIGINT', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bINTEGER\b', 'INT', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bBYTES\b', 'BINARY', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bDATETIME\b(?!\s*\()', 'TIMESTAMP', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bBOOL\b', 'BOOLEAN', sql, flags=re.IGNORECASE)

        # JSON type → VARIANT (Databricks 2026 native semi-structured type)
        sql = re.sub(
            r'(?<=\s)JSON\b(?!\s*\(|_)',
            r'VARIANT /* Databricks 2026: native VARIANT type for semi-structured JSON data */',
            sql, flags=re.IGNORECASE,
        )

        # GEOGRAPHY → STRING with TODO (2026: Databricks has native ST_* functions but no GEOGRAPHY type)
        sql = re.sub(
            r'\bGEOGRAPHY\b',
            r'STRING /* TODO: GEOGRAPHY type not natively supported; use ST_* functions with WKT STRING representation */',
            sql, flags=re.IGNORECASE,
        )

        # TIME type → STRING (Databricks has no standalone TIME type)
        # Careful: only match standalone TIME as a type, not TIME(...) function calls
        sql = re.sub(
            r'(?<=\s)TIME\b(?!\s*\(|\s+ZONE|\s+WITH)',
            r'STRING /* TODO: no native TIME type in Databricks */',
            sql, flags=re.IGNORECASE,
        )

        # RANGE<type> → STRUCT<start:type, end:type>
        sql = re.sub(
            r'\bRANGE\s*<(\w+)>',
            r'STRUCT<start:\1, end:\1> /* TODO: RANGE type emulated as STRUCT */',
            sql, flags=re.IGNORECASE,
        )

        # ── 2026: Collation spec mapping (DDL contexts) ──────────────────
        # STRING COLLATE 'und:ci' → STRING COLLATE UTF8_LCASE
        sql = re.sub(r"\bCOLLATE\s+'und:ci'", r'COLLATE UTF8_LCASE', sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bCOLLATE\s+'und:cs'", r'COLLATE UTF8_BINARY', sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bCOLLATE\s+'und'", r'COLLATE UTF8_BINARY', sql, flags=re.IGNORECASE)

        # ── Parameter marker normalization ────────────────────────────────
        # BigQuery commonly uses @param while Databricks SQL supports :param.
        # Normalize alternate placeholder renderings to :param.
        sql = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', r':\1', sql)
        sql = re.sub(r'@([A-Za-z_][A-Za-z0-9_]*)', r':\1', sql)

        # Remove BQ raw-string r'' prefix and double backslashes for Databricks
        def fix_raw_string(m: re.Match) -> str:
            inner = m.group(1)
            inner = re.sub(r'(?<!\\)\\(?!\\)', r'\\\\', inner)
            return f"'{inner}'"
        sql = re.sub(r"\br'((?:[^'\\]|\\.)*)'", fix_raw_string, sql)

        # Trailing comma cleanup before FROM / closing paren
        sql = re.sub(r',\s*\n\s*FROM\b', '\nFROM', sql, flags=re.IGNORECASE)
        sql = re.sub(r',\s*\)', ')', sql)

        # Catch-all for any remaining TO_JSON_STRING
        sql = re.sub(r'\bTO_JSON_STRING\s*\(', 'to_json(', sql, flags=re.IGNORECASE)

        # Close extra paren from JSON_OBJECT/JSON_ARRAY → to_json(map/array(...))
        # Pattern: to_json(map(...)) needs an extra ) after the inner close
        # to_json(array(...)) similarly
        def _close_wrapper_paren(sql: str, prefix: str) -> str:
            """Find to_json(prefix(...) and add the closing ) for to_json."""
            search_str = f'to_json({prefix}('
            idx = 0
            while True:
                pos = sql.lower().find(search_str.lower(), idx)
                if pos == -1:
                    break
                # Find the matching close paren for the inner function
                inner_start = pos + len(f'to_json({prefix}')
                depth = 0
                i = inner_start
                while i < len(sql):
                    if sql[i] == '(':
                        depth += 1
                    elif sql[i] == ')':
                        depth -= 1
                        if depth == 0:
                            # i is the closing paren of inner func
                            # Check if next char is already ')'
                            if i + 1 < len(sql) and sql[i + 1] == ')':
                                idx = i + 2  # already closed
                            else:
                                sql = sql[:i + 1] + ')' + sql[i + 1:]
                                idx = i + 2
                            break
                    i += 1
                else:
                    idx = pos + 1  # couldn't find match, skip
            return sql

        sql = _close_wrapper_paren(sql, 'map')
        sql = _close_wrapper_paren(sql, 'array')

        # DDL: bare PARTITION BY col (no parens) → PARTITIONED BY (col)
        # Only in CREATE TABLE/VIEW context, not in OVER(PARTITION BY ...)
        def _fix_ddl_partition(m: re.Match) -> str:
            full = m.group(0)
            col = m.group(1)
            # Don't touch window function PARTITION BY
            prefix = sql[:m.start()]
            if re.search(r'OVER\s*\(\s*$', prefix, re.IGNORECASE):
                return full
            return f'PARTITIONED BY ({col})'

        if re.search(r'\bCREATE\b', sql, re.IGNORECASE):
            sql = re.sub(
                r'\bPARTITION\s+BY\s+(\w+)(?=\s|$|;)',
                _fix_ddl_partition, sql, flags=re.IGNORECASE,
            )

        # Prefer Databricks-compatible LAST_VALUE/FIRST_VALUE form with boolean
        # second argument instead of the SQL `IGNORE NULLS` clause.
        # Uses paren-aware parsing to safely handle nested IF(), CASE, etc.
        def _rewrite_ignore_nulls(query: str) -> str:
            for fn_name in ('LAST_VALUE', 'FIRST_VALUE'):
                pat = re.compile(rf'\b{fn_name}\s*\(', re.IGNORECASE)
                idx = 0
                while True:
                    m = pat.search(query, idx)
                    if not m:
                        break
                    fn_start = m.start()
                    paren_start = m.end() - 1
                    depth = 0
                    end = -1
                    for i in range(paren_start, len(query)):
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
                    inside = query[paren_start + 1:end].strip()
                    # Check if IGNORE NULLS is inside the parens
                    ignore_inside = re.search(r'\s+IGNORE\s+NULLS\s*$', inside, re.IGNORECASE)
                    # Check if IGNORE NULLS is right after the closing paren
                    after_close = query[end + 1:end + 20]
                    ignore_outside = re.match(r'\s*IGNORE\s+NULLS\b', after_close, re.IGNORECASE)
                    if not ignore_inside and not ignore_outside:
                        idx = end + 1
                        continue
                    # Strip IGNORE NULLS from inside if present
                    expr = re.sub(r'\s+IGNORE\s+NULLS\s*$', '', inside, flags=re.IGNORECASE).strip()
                    # Already has ', true' → skip (idempotency)
                    if re.search(r',\s*true\s*$', expr, re.IGNORECASE):
                        idx = end + 1
                        continue
                    replacement = f"{fn_name.lower()}({expr}, true)"
                    # Calculate how much to replace after the closing paren
                    consume_after = 0
                    if ignore_outside:
                        consume_after = ignore_outside.end()
                    query = query[:fn_start] + replacement + query[end + 1 + consume_after:]
                    idx = fn_start + len(replacement)
            return query
        sql = _rewrite_ignore_nulls(sql)

        # ── Rewrite FILTER (WHERE ...) into IF(..., NULL) ───────────────
        # Spark SQL does not consistently support FILTER (WHERE) in window functions.
        def _rewrite_filter_where(query: str) -> str:
            pat = re.compile(r'\bFILTER\s*\(\s*WHERE\b\s*', re.IGNORECASE)
            idx = 0
            while True:
                m = pat.search(query, idx)
                if not m:
                    break
                filter_start = m.start()
                cond_start = m.end()
                
                # find end of FILTER (WHERE ... )
                depth = 1
                cond_end = -1
                in_quote = None
                for i in range(cond_start, len(query)):
                    ch = query[i]
                    if in_quote:
                        if ch == in_quote and query[i-1] != '\\':
                            in_quote = None
                    else:
                        if ch in ("'", '"'):
                            in_quote = ch
                        elif ch == '(': depth += 1
                        elif ch == ')':
                            depth -= 1
                            if depth == 0:
                                cond_end = i
                                break
                if cond_end == -1:
                    idx = m.end()
                    continue
                    
                cond = query[cond_start:cond_end].strip()
                filter_full_end = cond_end + 1
                
                # Find preceding function invocation: `func(args) ... FILTER`
                i = filter_start - 1
                while i >= 0 and query[i].isspace(): i -= 1
                
                if i < 0 or query[i] != ')':
                    idx = filter_full_end
                    continue
                func_paren_end = i
                
                depth = 1
                func_paren_start = -1
                in_quote = None
                i -= 1
                while i >= 0:
                    ch = query[i]
                    if in_quote:
                        if ch == in_quote and query[i-1] != '\\':
                            in_quote = None
                    else:
                        if ch in ("'", '"'):
                            in_quote = ch
                        elif ch == ')': depth += 1
                        elif ch == '(':
                            depth -= 1
                            if depth == 0:
                                func_paren_start = i
                                break
                    i -= 1
                    
                if func_paren_start == -1:
                    idx = filter_full_end
                    continue
                    
                args_str = query[func_paren_start + 1 : func_paren_end]
                
                parts = []
                d = 0
                curr = []
                in_quote = None
                for ch in args_str:
                    if in_quote:
                        if ch == in_quote and (len(curr) == 0 or curr[-1] != '\\'):
                            in_quote = None
                        curr.append(ch)
                    else:
                        if ch in ("'", '"'):
                            in_quote = ch
                            curr.append(ch)
                        elif ch == '(':
                            d += 1
                            curr.append(ch)
                        elif ch == ')':
                            d -= 1
                            curr.append(ch)
                        elif ch == ',' and d == 0:
                            parts.append(''.join(curr))
                            curr = []
                        else:
                            curr.append(ch)
                parts.append(''.join(curr))
                
                if not parts or not parts[0].strip():
                    idx = filter_full_end
                    continue
                    
                first_arg = parts[0].strip()
                if first_arg == '*':
                    new_first_arg = f"IF({cond}, 1, NULL)"
                else:
                    new_first_arg = f"IF({cond}, {first_arg}, NULL)"
                parts[0] = new_first_arg
                
                new_args_str = ",".join(parts)
                
                query = (query[:func_paren_start + 1] + 
                         new_args_str + 
                         query[func_paren_end:filter_start].rstrip() +
                         query[filter_full_end:])
                         
                idx = func_paren_start + len(new_args_str) + 1
                
            return query
        sql = _rewrite_filter_where(sql)

        # Handle legacy 3-argument LAST_VALUE patterns sometimes emitted by
        # imperfect rewrites (e.g. LAST_VALUE(IF(cond, val, NULL), NULL, col)).
        # Convert common IF(...) shapes into CASE WHEN and use the boolean arg.
        sql = re.sub(
            r"\bLAST_VALUE\s*\(\s*IF\s*\(\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*NULL\s*\)\s*,\s*NULL\s*,\s*([^)]+?)\s*\)",
            lambda m: f"last_value(CASE WHEN {m.group(1)} THEN {m.group(2)} ELSE NULL END, true)",
            sql,
            flags=re.IGNORECASE,
        )

        # Generic fallback: convert three-arg LAST_VALUE/FIRST_VALUE(..., NULL, x)
        # into two-arg form LAST_VALUE(expr, true)
        sql = re.sub(
            r"\b(FIRST_VALUE|LAST_VALUE)\s*\(\s*([^,]+?)\s*,\s*NULL\s*,\s*([^)]+?)\s*\)",
            lambda m: f"{m.group(1).lower()}({m.group(2)}, true)",
            sql,
            flags=re.IGNORECASE,
        )

        # ── Window frame enforcement for LAST_VALUE / FIRST_VALUE ─────
        # When using IGNORE NULLS (now rewritten to `fn(expr, true)`),
        # Databricks may default to a different window frame than BigQuery.
        # BigQuery's default is always RANGE BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING.
        # Add explicit frame if missing to prevent result drift.
        def _enforce_window_frame(query: str) -> str:
            # Match: last_value(..., true) OVER(... ) where there's no ROWS/RANGE
            pat = re.compile(
                r'(\b(?:last_value|first_value)\s*\([^)]*,\s*true\s*\)\s*OVER\s*\()([^)]*)\)',
                re.IGNORECASE,
            )
            def _add_frame(m):
                over_content = m.group(2)
                # If already has ROWS or RANGE specification, don't touch
                if re.search(r'\b(?:ROWS|RANGE)\s+BETWEEN\b', over_content, re.IGNORECASE):
                    return m.group(0)
                # Add default BigQuery-compatible frame
                return f"{m.group(1)}{over_content} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
            return pat.sub(_add_frame, query)
        sql = _enforce_window_frame(sql)

        sql = re.sub(r'(\bTHEN\s+)"([^"\n]*)"', r"\1'\2'", sql, flags=re.IGNORECASE)
        sql = re.sub(r'(\bELSE\s+)"([^"\n]*)"', r"\1'\2'", sql, flags=re.IGNORECASE)
        sql = re.sub(r'([=<>]\s*)"([^"\n]*)"', r"\1'\2'", sql)

        # BigQuery DATE(col) normalizes to to_date(col) for Databricks timestamp/date casts.
        sql = re.sub(
            r'\bDATE\s*\(\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\)',
            r'to_date(\1)',
            sql,
            flags=re.IGNORECASE,
        )

        # sqlglot may emit TRUNCATE(date_expr, 'MONTH') for date truncation.
        # Normalize to DATE_TRUNC('MONTH', date_expr) in Databricks style.
        sql = re.sub(
            r"\bTRUNCATE\s*\(\s*([^,]+?)\s*,\s*'\s*(DAY|WEEK|MONTH|QUARTER|YEAR)\s*'\s*\)",
            r"DATE_TRUNC('\2', \1)",
            sql,
            flags=re.IGNORECASE,
        )

        # ══════════════════════════════════════════════════════════════════
        # Critical BigQuery → Databricks function translations
        # These MUST be deterministic — the LLM cannot be trusted for them.
        # ══════════════════════════════════════════════════════════════════

        # ── Array access: [SAFE_OFFSET(n)] → null-safe element_at ───────
        # BigQuery's SAFE_OFFSET returns NULL if index is out of bounds.
        # Databricks element_at() may throw for invalid indices depending
        # on config, so we wrap in CASE WHEN size() >= n+1 for safety.
        def _rewrite_safe_offset(query: str) -> str:
            pat = re.compile(r'(\b[A-Za-z_][A-Za-z0-9_.]*)\[SAFE_OFFSET\s*\(\s*(\d+)\s*\)\]', re.IGNORECASE)
            def _safe_element_at(m):
                arr = m.group(1)
                idx_0 = int(m.group(2))  # 0-based
                idx_1 = idx_0 + 1        # 1-based for Databricks
                return f"CASE WHEN {arr} IS NOT NULL AND size({arr}) >= {idx_1} THEN element_at({arr}, {idx_1}) ELSE NULL END"
            return pat.sub(_safe_element_at, query)
        sql = _rewrite_safe_offset(sql)

        # ── Array access: [OFFSET(n)] → [n] (0-indexed already in Databricks) ──
        sql = re.sub(
            r'\[OFFSET\s*\(\s*(\d+)\s*\)\]',
            r'[\1]',
            sql,
            flags=re.IGNORECASE,
        )

        # ── Array access: [ORDINAL(n)] → [n-1] (BQ ORDINAL is 1-indexed) ──
        sql = re.sub(
            r'\[ORDINAL\s*\(\s*(\d+)\s*\)\]',
            lambda m: f'[{int(m.group(1)) - 1}]',
            sql,
            flags=re.IGNORECASE,
        )

        # ── SAFE_DIVIDE(a, b) → TRY_DIVIDE(a, b) ─────────────────────
        sql = re.sub(r'\bSAFE_DIVIDE\s*\(', 'TRY_DIVIDE(', sql, flags=re.IGNORECASE)

        # ── SAFE_CAST(x AS type) → TRY_CAST(x AS type) ───────────────
        sql = re.sub(r'\bSAFE_CAST\s*\(', 'TRY_CAST(', sql, flags=re.IGNORECASE)

        # ── SAFE_MULTIPLY / SAFE_ADD / SAFE_SUBTRACT / SAFE_NEGATE ────
        sql = re.sub(r'\bSAFE_MULTIPLY\s*\(', 'TRY_MULTIPLY(', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bSAFE_ADD\s*\(', 'TRY_ADD(', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bSAFE_SUBTRACT\s*\(', 'TRY_SUBTRACT(', sql, flags=re.IGNORECASE)

        # ── ARRAY_LENGTH(arr) → SIZE(arr) ─────────────────────────────
        sql = re.sub(r'\bARRAY_LENGTH\s*\(', 'SIZE(', sql, flags=re.IGNORECASE)

        # ── FARM_FINGERPRINT(x) → hash(x) ────────────────────────────
        sql = re.sub(r'\bFARM_FINGERPRINT\s*\(', 'hash(', sql, flags=re.IGNORECASE)

        # ── FORMAT_DATE(fmt, date_expr) → DATE_FORMAT(date_expr, fmt) ─
        # Args are swapped between BigQuery and Databricks.
        sql = re.sub(
            r'\bFORMAT_DATE\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
            r'DATE_FORMAT(\2, \1)',
            sql,
            flags=re.IGNORECASE,
        )

        # ── PARSE_DATE(fmt, str) → TO_DATE(str, fmt) ─────────────────
        sql = re.sub(
            r'\bPARSE_DATE\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
            r'TO_DATE(\2, \1)',
            sql,
            flags=re.IGNORECASE,
        )

        # ── FORMAT_TIMESTAMP(fmt, ts) → DATE_FORMAT(ts, fmt) ─────────
        sql = re.sub(
            r'\bFORMAT_TIMESTAMP\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
            r'DATE_FORMAT(\2, \1)',
            sql,
            flags=re.IGNORECASE,
        )

        # ── PARSE_TIMESTAMP(fmt, str) → TO_TIMESTAMP(str, fmt) ───────
        sql = re.sub(
            r'\bPARSE_TIMESTAMP\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
            r'TO_TIMESTAMP(\2, \1)',
            sql,
            flags=re.IGNORECASE,
        )

        # ── GENERATE_DATE_ARRAY(start, end[, INTERVAL n UNIT]) → sequence(start, end, INTERVAL n UNIT) ──
        sql = re.sub(
            r'\bGENERATE_DATE_ARRAY\s*\(\s*([^,]+?)\s*,\s*([^,)]+?)(?:\s*,\s*(INTERVAL\s+.+?))?\s*\)',
            lambda m: f"sequence({m.group(1)}, {m.group(2)}, {m.group(3) or 'INTERVAL 1 DAY'})",
            sql,
            flags=re.IGNORECASE,
        )

        # ── GENERATE_TIMESTAMP_ARRAY(start, end, INTERVAL) → sequence(start, end, INTERVAL) ──
        sql = re.sub(
            r'\bGENERATE_TIMESTAMP_ARRAY\s*\(\s*([^,]+?)\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
            r'sequence(\1, \2, \3)',
            sql,
            flags=re.IGNORECASE,
        )

        # ── GENERATE_ARRAY(start, end[, step]) → sequence(start, end[, step]) ──
        sql = re.sub(r'\bGENERATE_ARRAY\s*\(', 'sequence(', sql, flags=re.IGNORECASE)

        # ── DATE_DIFF(a, b, UNIT) → DATEDIFF(UNIT, b, a) ─────────────
        # BigQuery: DATE_DIFF(date1, date2, part) → date1 - date2
        # Databricks: DATEDIFF(part, start, end) or DATEDIFF(end, start) for days
        # Note: BigQuery arg order is (later, earlier, unit), Databricks is (unit, earlier, later)
        def _rewrite_date_diff(query: str) -> str:
            pat = re.compile(r'\bDATE_DIFF\s*\(', re.IGNORECASE)
            idx = 0
            while True:
                m = pat.search(query, idx)
                if not m:
                    break
                start = m.end() - 1
                depth = 0
                end = -1
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
                inside = query[start + 1:end].strip()
                # Split on top-level commas
                parts = []
                d = 0
                current = []
                for ch in inside:
                    if ch == '(':
                        d += 1
                    elif ch == ')':
                        d -= 1
                    elif ch == ',' and d == 0:
                        parts.append(''.join(current).strip())
                        current = []
                        continue
                    current.append(ch)
                parts.append(''.join(current).strip())
                if len(parts) == 3:
                    later, earlier, unit = parts
                    unit_clean = unit.strip().strip("'\"").upper()
                    if unit_clean == 'DAY':
                        replacement = f"DATEDIFF({later}, {earlier})"
                    else:
                        replacement = f"DATEDIFF({unit_clean}, {earlier}, {later})"
                else:
                    idx = m.end()
                    continue
                query = query[:m.start()] + replacement + query[end + 1:]
                idx = m.start() + len(replacement)
            return query
        sql = _rewrite_date_diff(sql)

        # ── TIMESTAMP_DIFF(a, b, UNIT) → same logic as DATE_DIFF ──────
        def _rewrite_timestamp_diff(query: str) -> str:
            pat = re.compile(r'\bTIMESTAMP_DIFF\s*\(', re.IGNORECASE)
            idx = 0
            while True:
                m = pat.search(query, idx)
                if not m:
                    break
                start = m.end() - 1
                depth = 0
                end = -1
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
                inside = query[start + 1:end].strip()
                parts = []
                d = 0
                current = []
                for ch in inside:
                    if ch == '(':
                        d += 1
                    elif ch == ')':
                        d -= 1
                    elif ch == ',' and d == 0:
                        parts.append(''.join(current).strip())
                        current = []
                        continue
                    current.append(ch)
                parts.append(''.join(current).strip())
                if len(parts) == 3:
                    later, earlier, unit = parts
                    unit_clean = unit.strip().strip("'\"").upper()
                    replacement = f"DATEDIFF({unit_clean}, {earlier}, {later})"
                else:
                    idx = m.end()
                    continue
                query = query[:m.start()] + replacement + query[end + 1:]
                idx = m.start() + len(replacement)
            return query
        sql = _rewrite_timestamp_diff(sql)

        # ── JSON_EXTRACT_SCALAR(col, path) → get_json_object(col, path) ──
        sql = re.sub(r'\bJSON_EXTRACT_SCALAR\s*\(', 'get_json_object(', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bJSON_EXTRACT\s*\(', 'get_json_object(', sql, flags=re.IGNORECASE)

        # ── TO_HEX / FROM_HEX / TO_BASE64 / FROM_BASE64 ─────────────
        sql = re.sub(r'\bTO_HEX\s*\(', 'hex(', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bFROM_HEX\s*\(', 'unhex(', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bTO_BASE64\s*\(', 'base64(', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bFROM_BASE64\s*\(', 'unbase64(', sql, flags=re.IGNORECASE)

        # ── ARRAY_AGG(x) → COLLECT_LIST(x) ───────────────────────────
        sql = re.sub(r'\bARRAY_AGG\s*\(', 'COLLECT_LIST(', sql, flags=re.IGNORECASE)

        # ── STRING_AGG(expr, sep) → CONCAT_WS(sep, COLLECT_LIST(expr)) ──
        sql = re.sub(
            r'\bSTRING_AGG\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
            r'CONCAT_WS(\2, COLLECT_LIST(\1))',
            sql,
            flags=re.IGNORECASE,
        )

        # ── IN UNNEST(arr) → array_contains(arr, expr) ───────────────
        sql = re.sub(
            r'(\b[\w.`]+)\s+IN\s+UNNEST\s*\(\s*([^)]+?)\s*\)',
            r'COALESCE(array_contains(\2, \1), false)',
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r'(\b[\w.`]+)\s+NOT\s+IN\s+UNNEST\s*\(\s*([^)]+?)\s*\)',
            r'NOT COALESCE(array_contains(\2, \1), false)',
            sql,
            flags=re.IGNORECASE,
        )

        # ── FROM UNNEST(...) AS alias → FROM (SELECT EXPLODE(...) AS alias) ──
        # BigQuery uses UNNEST as a table expression. Databricks requires EXPLODE in SELECT.
        def _rewrite_from_unnest(query: str) -> str:
            pat = re.compile(r'\bFROM\s+UNNEST\s*\(\s*(.+?)\s*\)\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)', re.IGNORECASE)
            return pat.sub(r'FROM (SELECT EXPLODE(\1) AS \2)', query)
        sql = _rewrite_from_unnest(sql)

        # ── UNNEST(sequence(...)) → EXPLODE(sequence(...)) for date generation ──
        sql = re.sub(
            r'\bUNNEST\s*\(\s*(sequence\s*\([^)]+?\))\s*\)',
            r'EXPLODE(\1)',
            sql,
            flags=re.IGNORECASE,
        )

        # ── EXTRACT(DAYOFWEEK FROM expr) → dayofweek(expr) ───────────
        sql = re.sub(
            r'\bEXTRACT\s*\(\s*DAYOFWEEK\s+FROM\s+([^)]+?)\s*\)',
            r'dayofweek(\1)',
            sql,
            flags=re.IGNORECASE,
        )

        # ── DATE_ADD/DATE_SUB(expr, INTERVAL n DAY) → date_add/date_sub(expr, n) ──
        # BigQuery often uses INTERVAL objects inside these functions.
        sql = re.sub(
            r'\bDATE_ADD\s*\(\s*([^,]+?)\s*,\s*INTERVAL\s*(\d+)\s*DAY\s*\)',
            r'date_add(\1, \2)',
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r'\bDATE_SUB\s*\(\s*([^,]+?)\s*,\s*INTERVAL\s*(\d+)\s*DAY\s*\)',
            r'date_sub(\1, \2)',
            sql,
            flags=re.IGNORECASE,
        )

        # ── Date arithmetic: date_func() +/- integer → date_add/date_sub ──
        # BigQuery: CURRENT_DATE() - 7 -> Databricks: date_sub(current_date(), 7)
        # CONSERVATIVE: Only match known DATE-returning functions/columns to avoid
        # accidentally rewriting timestamp or numeric arithmetic.
        def _rewrite_date_arithmetic(query: str) -> str:
            # Only match functions/columns that are KNOWN to return DATE type
            date_funcs = (
                r'CURRENT_DATE\(\)'
                r'|current_date\(\)'
                r'|to_date\([^)]+\)'
            )
            query = re.sub(
                rf'({date_funcs})\s*([\+\-])\s*(\d+)\b',
                lambda m: f"{'date_add' if m.group(2) == '+' else 'date_sub'}({m.group(1)}, {m.group(3)})",
                query,
                flags=re.IGNORECASE,
            )
            return query
        sql = _rewrite_date_arithmetic(sql)

        # ── SELECT * EXCEPT(col1, col2, ...) → add warning comment ────
        # Databricks supports EXCEPT in newer runtimes (14.1+), but for
        # broader compatibility we flag it clearly.
        sql = re.sub(
            r'\bSELECT\s+\*\s+EXCEPT\s*\(',
            'SELECT * EXCEPT( /* WARNING: SELECT * EXCEPT requires Databricks Runtime 14.1+; rewrite to explicit columns if targeting older runtimes */ ',
            sql,
            flags=re.IGNORECASE,
        )

        # ── DATE_TRUNC(expr, UNIT) → DATE_TRUNC('UNIT', expr) ────────
        # BigQuery arg order is (expr, unit), Databricks is ('unit', expr)
        sql = re.sub(
            r"\bDATE_TRUNC\s*\(\s*([^,]+?)\s*,\s*(DAY|WEEK|MONTH|QUARTER|YEAR|HOUR|MINUTE|SECOND)\s*\)",
            r"DATE_TRUNC('\2', \1)",
            sql,
            flags=re.IGNORECASE,
        )

        # Final deterministic safety rails applied for both deterministic and LLM paths.
        sql = self._enforce_spark_sql_safety(sql)

        sql = _restore_jinja(sql, jinja_map)
        return sql

    @staticmethod
    def _enforce_spark_sql_safety(sql: str) -> str:
        """Normalize a few high-risk patterns that often appear in imperfect translations."""
        def _rewrite_declare_to_jinja_sets(query: str) -> str:
            """Convert BigQuery DECLARE ... DEFAULT ...; statements to dbt Jinja {% set %}."""
            out_lines = []
            for line in query.splitlines():
                m = re.match(
                    r'^\s*DECLARE\s+([A-Za-z_][A-Za-z0-9_]*)\s+.+?\s+DEFAULT\s+(.+?)\s*;?\s*$',
                    line,
                    flags=re.IGNORECASE,
                )
                if m:
                    var_name = m.group(1)
                    default_expr = m.group(2).strip().rstrip(';').strip()
                    out_lines.append(f"{{% set {var_name} = {default_expr} %}}")
                else:
                    out_lines.append(line)
            return "\n".join(out_lines)

        sql = _rewrite_declare_to_jinja_sets(sql)

        def _rewrite_count_if(query: str) -> str:
            """Rewrite COUNT_IF(cond) to SUM(CASE WHEN cond THEN 1 ELSE 0 END)."""
            pat = re.compile(r'\bCOUNT_IF\s*\(', re.IGNORECASE)
            idx = 0
            while True:
                m = pat.search(query, idx)
                if not m:
                    break
                start = m.end() - 1  # opening '('
                depth = 0
                end = -1
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

        def _rewrite_date_trunc_week(query: str) -> str:
            """Ensure BigQuery Sunday-start WEEK matches Databricks Monday-start WEEK semantics."""
            pat = re.compile(r'\bDATE_TRUNC\s*\(', re.IGNORECASE)
            idx = 0
            while True:
                m = pat.search(query, idx)
                if not m:
                    break
                start = m.end() - 1  # opening '('
                depth = 0
                end = -1
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
                
                inside = query[start + 1:end].strip()
                
                # Check if it has WEEK as an argument
                # Databricks style: DATE_TRUNC('WEEK', expr)
                week_dbx_pat = re.compile(r"^\s*['\"]?(?i:WEEK)['\"]?\s*,\s*(.+)$")
                # BigQuery style: DATE_TRUNC(expr, WEEK)
                week_bq_pat = re.compile(r"^(.+?)\s*,\s*['\"]?(?i:WEEK)['\"]?\s*$")
                
                m_dbx = week_dbx_pat.match(inside)
                m_bq = week_bq_pat.match(inside) if not m_dbx else None
                
                if m_dbx:
                    expr = m_dbx.group(1).strip()
                elif m_bq:
                    expr = m_bq.group(1).strip()
                else:
                    idx = m.end()
                    continue
                
                # Idempotency check: don't double-wrap if already converted
                if re.search(r'(?i)\+\s*INTERVAL\s+1\s+DAY', expr):
                    idx = m.end()
                    continue
                    
                replacement = f"DATE_TRUNC('WEEK', {expr} + INTERVAL 1 DAY) - INTERVAL 1 DAY"
                query = query[:m.start()] + replacement + query[end + 1:]
                idx = m.start() + len(replacement)
            return query

        # Canonical function style.
        sql = re.sub(r'\bARRAY_CONTAINS\s*\(', 'array_contains(', sql, flags=re.IGNORECASE)
        # Null-safe default for positive membership checks in DBX filtering contexts.
        sql = re.sub(
            r'(?<!COALESCE\()\barray_contains\s*\(([^,]+?),\s*([^\)]+?)\)',
            r'COALESCE(array_contains(\1, \2), false)',
            sql,
            flags=re.IGNORECASE,
        )
        sql = _rewrite_date_trunc_week(sql)
        sql = _rewrite_count_if(sql)

        # Invalid shape: "expr IN LATERAL VIEW EXPLODE(arr)" -> array_contains(arr, expr)
        sql = re.sub(
            r'((?:`[^`]+`|\b[\w.]+))\s+IN\s+LATERAL\s+VIEW(?:\s+OUTER)?\s+EXPLODE\s*\(\s*([^\)]+?)\s*\)',
            r'array_contains(\2, \1)',
            sql,
            flags=re.IGNORECASE,
        )

        # BigQuery macro artifact not valid for Databricks/dbt-spark output.
        sql = re.sub(r'\bset_sql_header\s*\(', '/* set_sql_header removed for Databricks */(', sql, flags=re.IGNORECASE)

        # Common accidental map/struct accessor shape: s.get(imageURL, 0)
        sql = re.sub(
            r'\b([A-Za-z_][A-Za-z0-9_]*)\.get\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*0\s*\)',
            r'CASE WHEN \1.\2 IS NOT NULL AND size(\1.\2) > 0 THEN \1.\2[0] ELSE NULL END',
            sql,
            flags=re.IGNORECASE,
        )

        # Fix trailing commas before FROM clauses (often an artifact of automated rewrites).
        sql = re.sub(r',\s+(FROM\b)', r' \1', sql, flags=re.IGNORECASE)

        # Normalize `IS TRUE` / `IS FALSE` to `= true` / `= false` for Databricks
        sql = re.sub(r"(\b[A-Za-z_][A-Za-z0-9_.]*)\s+IS\s+TRUE", r"\1 = true", sql, flags=re.IGNORECASE)
        sql = re.sub(r"(\b[A-Za-z_][A-Za-z0-9_.]*)\s+IS\s+FALSE", r"\1 = false", sql, flags=re.IGNORECASE)

        return sql
    def _map_bq_struct_to_named(self, inner: str) -> str:
        """
        Converts 'val AS name, val2 AS name2' to NAMED_STRUCT('name', val, 'name2', val2).
        If no AS present, just keeps it as STRUCT(val).
        """
        # Split by comma but respect nested structures
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
                if ch in ("'", '"'):
                    in_quote = ch
                    current.append(ch)
                elif ch == '(': depth += 1; current.append(ch)
                elif ch == ')': depth -= 1; current.append(ch)
                elif ch == ',' and depth == 0:
                    parts.append("".join(current).strip())
                    current = []
                else:
                    current.append(ch)
        if current:
            parts.append("".join(current).strip())
        
        # Check if we need to use NAMED_STRUCT
        if any(re.search(r'\s+AS\s+', p, re.IGNORECASE) for p in parts):
            named_args = []
            for p in parts:
                alias_match = re.search(r'\s+AS\s+([A-Za-z0-9_]+)$', p, re.IGNORECASE)
                if alias_match:
                    alias = alias_match.group(1)
                    val = p[:alias_match.start()].strip()
                    named_args.extend([f"'{alias}'", val])
                else:
                    name = p.replace("'", "").replace("`", "").strip()
                    named_args.extend([f"'{name}'", p])
            return f"NAMED_STRUCT({', '.join(named_args)})"
        
        return f"STRUCT({inner})"

