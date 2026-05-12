import sqlglot
from sqlglot import exp
from typing import Optional, Any
import re
import logging

logger = logging.getLogger(__name__)

# ── Jinja/dbt template protection ─────────────────────────────────────────────
# sqlglot cannot parse {{ }}, {% %}, {# #} blocks.  We swap them out before
# parsing and restore them after SQL generation.

_JINJA_PATTERN = re.compile(
    r'\{\{.*?\}\}'
    r'|\{%.*?%\}'
    r'|\{#.*?#\}',
    re.DOTALL,
)

# Jinja *control* blocks ({% if %}, {% for %}, {% endif %}, etc.) produce
# bare identifiers that break SQL grammar.  Expression blocks ({{ expr }})
# can stand in for a column/table name and are usually parseable.
_JINJA_CONTROL_PATTERN = re.compile(r'\{%.*?%\}', re.DOTALL)
_AT_PARAM_PATTERN = re.compile(r'@([A-Za-z_][A-Za-z0-9_]*)')


def _extract_jinja(sql: str):
    """Replace Jinja blocks with safe placeholders. Returns (clean_sql, mapping)."""
    placeholders = {}
    counter = [0]

    def _replace(m):
        token = m.group(0)
        ph = f"__JINJA_{counter[0]}__"
        placeholders[ph] = token
        counter[0] += 1
        # Use a bare identifier so sqlglot treats it as a table/column name
        return ph

    return _JINJA_PATTERN.sub(_replace, sql), placeholders


def _restore_jinja(sql: str, placeholders: dict) -> str:
    """Put Jinja blocks back, removing the comment wrappers."""
    for ph, original in placeholders.items():
        sql = sql.replace(f"/*{ph}*/", original)
        sql = sql.replace(ph, original)
    # Safety check: warn if any placeholders survived (corrupted restore)
    remaining = re.findall(r'__JINJA_\d+__', sql)
    if remaining:
        logger.warning("Jinja restore incomplete — %d placeholder(s) remain: %s",
                        len(remaining), remaining[:5])
    return sql


def _extract_at_params(sql: str):
    """Replace BigQuery @params with safe identifiers for AST parsing."""
    placeholders = {}
    counter = [0]

    def _replace(m):
        token = m.group(0)
        ph = f"__AT_PARAM_{counter[0]}__"
        placeholders[ph] = token
        counter[0] += 1
        return ph

    return _AT_PARAM_PATTERN.sub(_replace, sql), placeholders


def _restore_at_params(sql: str, placeholders: dict) -> str:
    """Restore BigQuery @params after AST transformation."""
    for ph, original in placeholders.items():
        sql = sql.replace(ph, original)
    return sql


class BigQueryToDatabricksTransformer:
    """AST-level transformer: handles things regex can't (nested expressions, argument positions)."""

    def __init__(self):
        self.transformations = [
            self.fix_substr_indices,
            self.fix_safe_cast,
            self.fix_json_functions,
            self.fix_string_functions,
            self.fix_date_functions,
            self.fix_hash_functions,
            self.fix_array_functions,
            self.fix_interval_syntax,
            self.fix_regex_patterns,
            self.fix_collation,
            self.fix_unnest,
            self.fix_array_concat_agg,
            self.fix_ignore_nulls,
        ]

        # BigQuery collation → Databricks collation mapping
        self._collation_map = {
            'und:ci': 'UTF8_LCASE',
            'und:cs': 'UTF8_BINARY',
            'und': 'UTF8_BINARY',
        }

    def transform(self, sql: str) -> str:
        """Apply all AST transformations to SQL. Falls back to original on parse failure."""
        # Skip AST parsing for scripting blocks — they pass through as-is for 2026
        from .sql_processor import SQLPreprocessor
        if SQLPreprocessor.is_scripting_block(sql):
            logger.info("Scripting block detected, skipping AST transforms")
            return sql

        try:
            clean_sql, jinja_map = _extract_jinja(sql)
            clean_sql, at_param_map = _extract_at_params(clean_sql)

            # If query contains QUALIFY, skip AST transforms to preserve QUALIFY
            # semantics and let regex rules handle engine-specific rewrites.
            if re.search(r'\bQUALIFY\b', clean_sql, re.IGNORECASE):
                logger.info("QUALIFY detected — skipping AST transforms to preserve clause")
                return sql

            # If Jinja control blocks ({% %}) were extracted, the resulting SQL
            # will have bare __JINJA_N__ identifiers on their own lines that
            # sqlglot cannot parse.  Skip AST transforms and let the regex
            # rule engine handle the translation instead.
            if _JINJA_CONTROL_PATTERN.search(sql):
                logger.info("Jinja control blocks detected, skipping AST transforms (regex rules still apply)")
                return sql

            trees = [t for t in sqlglot.parse(clean_sql, read="bigquery") if t is not None]
            parts = []
            for tree in trees:
                for transform_fn in self.transformations:
                    tree = tree.transform(transform_fn)
                parts.append(tree.sql(dialect="spark", pretty=True))
            # Generate as generic SQL; rule_engine handles Databricks-specific syntax
            result = ";\n".join(parts)

            # Safety check: if AST regeneration lost >40% of content,
            # the parser silently dropped syntax it couldn't handle.
            orig_len = len(sql.strip())
            result_len = len(result.strip())
            if orig_len > 2000 and result_len < orig_len * 0.6:
                logger.warning(
                    "AST transform lost content (%d -> %d chars, %.0f%% loss). "
                    "Returning original SQL.",
                    orig_len, result_len,
                    (1 - result_len / orig_len) * 100,
                )
                return sql

            result = _restore_at_params(result, at_param_map)
            return _restore_jinja(result, jinja_map)
        except sqlglot.errors.ParseError as e:
            logger.warning("AST parse failed, returning original SQL: %s", e)
            return sql
        except Exception as e:
            logger.warning("AST transform failed: %s", e)
            return sql

    # ── SUBSTR / SUBSTRING indices ──────────────────────────────────────────
    @staticmethod
    def fix_substr_indices(node: exp.Expression) -> exp.Expression:
        """BQ SUBSTR is 0-based; Databricks is 1-based."""
        if isinstance(node, (exp.Substring,)):
            start = node.args.get("start")
            if isinstance(start, exp.Literal) and start.is_number and int(start.name) == 0:
                node.set("start", exp.Literal.number(1))
        return node

    # ── SAFE_CAST → TRY_CAST ───────────────────────────────────────────────
    @staticmethod
    def fix_safe_cast(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Cast) and node.args.get("safe"):
            return exp.TryCast(this=node.this, to=node.args["to"], safe=True)
        # Preserve safe flag on existing TryCast nodes (sqlglot auto-converts SAFE_CAST)
        if isinstance(node, exp.TryCast) and not node.args.get("safe"):
            node.set("safe", True)
        return node

    # ── JSON FUNCTIONS ──────────────────────────────────────────────────────
    @staticmethod
    def fix_json_functions(node: exp.Expression) -> exp.Expression:
        if isinstance(node, (exp.JSONExtract, exp.JSONExtractScalar)):
            return exp.Anonymous(
                this="get_json_object",
                expressions=[node.this, node.expression]
            )
        if isinstance(node, exp.JSONFormat):
            # TO_JSON_STRING → to_json with ignoreNullFields=false
            # Without this, Databricks silently omits null fields from JSON output
            map_expr = exp.Anonymous(
                this="map",
                expressions=[
                    exp.Literal.string("ignoreNullFields"),
                    exp.Literal.string("false"),
                ]
            )
            return exp.Anonymous(this="to_json", expressions=[node.this, map_expr])
        return node

    # ── STRING FUNCTIONS ────────────────────────────────────────────────────
    @staticmethod
    def fix_string_functions(node: exp.Expression) -> exp.Expression:
        # STRPOS(str, sub) → LOCATE(sub, str)  — args reversed
        if isinstance(node, exp.StrPosition):
            substr = node.args.get("substr")
            this = node.this
            if substr and this:
                return exp.Anonymous(this="LOCATE", expressions=[substr, this])

        # REGEXP_CONTAINS → RLIKE predicate
        if isinstance(node, exp.RegexpLike):
            return exp.Like(
                this=node.this,
                expression=node.expression,
            )
        return node

    # ── DATE FUNCTIONS ──────────────────────────────────────────────────────
    @staticmethod
    def fix_date_functions(node: exp.Expression) -> exp.Expression:
        # DATE math: CURRENT_DATE() - 61 → date_sub(current_date(), 61)
        if isinstance(node, (exp.Sub, exp.Add)):
            left = node.this
            right = node.expression
            
            # If it's a date function/literal and the right side is NOT an interval (implies days integer)
            is_date_left = isinstance(left, (exp.CurrentDate, exp.CurrentTimestamp, exp.Date, exp.DateAdd, exp.DateSub, exp.DateTrunc))
            if is_date_left and not isinstance(right, exp.Interval):
                func_name = "date_sub" if isinstance(node, exp.Sub) else "date_add"
                return exp.Anonymous(this=func_name, expressions=[left, right])

        # UNIX_DATE(x) → unix_date(x)  (Databricks native; prevent sqlglot DATEDIFF expansion)
        if isinstance(node, exp.UnixDate):
            return exp.Anonymous(this="unix_date", expressions=[node.this])

        # TIMESTAMP_SECONDS(x) → timestamp_seconds(x)  (Databricks native; prevent FROM_UNIXTIME)
        if isinstance(node, exp.UnixToTime) and not node.args.get("scale"):
            return exp.Anonymous(this="timestamp_seconds", expressions=[node.this])

        # UNIX_TIMESTAMP(ts) → unix_timestamp(ts)  (Databricks native)
        if isinstance(node, exp.TimeToUnix):
            return exp.Anonymous(this="unix_timestamp", expressions=[node.this])

        # DATE_DIFF(a, b, unit) → DATEDIFF(a, b)  (unit DAY only; others handled by regex)
        if isinstance(node, exp.DateDiff):
            unit = node.args.get("unit")
            unit_str = unit.name.upper() if unit else "DAY"
            if unit_str == "DAY":
                # Manually transform children that won't be visited after
                # this node is replaced with Anonymous
                arg_this = node.this
                arg_expr = node.expression
                if isinstance(arg_this, exp.DateTrunc):
                    arg_this = BigQueryToDatabricksTransformer._make_date_trunc(arg_this)
                if isinstance(arg_expr, exp.DateTrunc):
                    arg_expr = BigQueryToDatabricksTransformer._make_date_trunc(arg_expr)
                return exp.Anonymous(
                    this="DATEDIFF",
                    expressions=[arg_this, arg_expr]
                )

        # DATE_ADD(date, INTERVAL n DAY) → DATE_ADD(date, n)
        if isinstance(node, exp.DateAdd):
            unit = node.args.get("unit") or node.args.get("interval")
            if hasattr(node, 'expression'):
                amount = node.expression
                # Handle old sqlglot versions that nest amount in exp.Interval
                if isinstance(amount, exp.Interval):
                    unit_val = amount.args.get("unit")
                    unit_str = unit_val.name.upper() if unit_val else "DAY"
                    amount = amount.this
                else:
                    unit_str = unit.name.upper() if unit else "DAY"

                if unit_str == "DAY":
                    if isinstance(amount, exp.Literal) and amount.args.get("is_string") and amount.this.isdigit():
                        amount.set("is_string", False)
                    return exp.Anonymous(
                        this="DATE_ADD",
                        expressions=[node.this, amount]
                    )

        # DATE_SUB(date, INTERVAL n DAY) → DATE_SUB(date, n)
        if isinstance(node, exp.DateSub):
            unit = node.args.get("unit") or node.args.get("interval")
            if hasattr(node, 'expression'):
                amount = node.expression
                # Handle old sqlglot versions that nest amount in exp.Interval
                if isinstance(amount, exp.Interval):
                    unit_val = amount.args.get("unit")
                    unit_str = unit_val.name.upper() if unit_val else "DAY"
                    amount = amount.this
                else:
                    unit_str = unit.name.upper() if unit else "DAY"

                if unit_str == "DAY":
                    if isinstance(amount, exp.Literal) and amount.args.get("is_string") and amount.this.isdigit():
                        amount.set("is_string", False)
                    return exp.Anonymous(
                        this="DATE_SUB",
                        expressions=[node.this, amount]
                    )

        # DATE_TRUNC(date, UNIT) → DATE_TRUNC('UNIT', date)
        if isinstance(node, exp.DateTrunc):
            return BigQueryToDatabricksTransformer._make_date_trunc(node)

        return node

    @staticmethod
    def _make_date_trunc(node: exp.DateTrunc) -> exp.Expression:
        """Convert DateTrunc to Anonymous DATE_TRUNC('UNIT', date)."""
        unit = node.args.get("unit")
        this = node.this
        if unit:
            unit_lit = exp.Literal.string(str(unit).strip("'\""  ).upper())
            return exp.Anonymous(
                this="DATE_TRUNC",
                expressions=[unit_lit, this]
            )
        return node

    # ── HASH FUNCTIONS ──────────────────────────────────────────────────────
    @staticmethod
    def fix_hash_functions(node: exp.Expression) -> exp.Expression:
        """
        TO_HEX(MD5(x))    → md5(x)
        TO_HEX(SHA256(x)) → sha2(x, 256)
        etc.
        Databricks hash functions already return hex strings.

        Also handles standalone MD5/SHA/SHA2 nodes that SQLGlot parses as
        built-in types (with arg in node.this, not node.expressions).
        """
        # Case 1: Hex(Anonymous(...)) — TO_HEX wrapping a hash call
        if isinstance(node, exp.Hex):
            inner = node.this
            if isinstance(inner, exp.Anonymous):
                fn = inner.this.upper()
                args = inner.expressions
                if fn == "MD5" and args:
                    return exp.Anonymous(this="md5", expressions=args)
                if fn in ("SHA1", "SHA") and args:
                    return exp.Anonymous(this="sha1", expressions=args)
                if fn == "SHA256" and args:
                    return exp.Anonymous(this="sha2", expressions=[args[0], exp.Literal.number(256)])
                if fn == "SHA512" and args:
                    return exp.Anonymous(this="sha2", expressions=[args[0], exp.Literal.number(512)])
            # Case 1b: Hex(MD5(...)) — sqlglot built-in node inside Hex
            if isinstance(inner, (exp.MD5, exp.MD5Digest)) and inner.this:
                return exp.Anonymous(this="md5", expressions=[inner.this])
            if isinstance(inner, (exp.SHA, getattr(exp, "SHADigest", type("Dummy",(),{})))) and inner.this:
                return exp.Anonymous(this="sha1", expressions=[inner.this])
            if isinstance(inner, (exp.SHA2, getattr(exp, "SHA2Digest", type("Dummy",(),{})))) and inner.this:
                length = getattr(inner, "args", {}).get("length") or exp.Literal.number(256)
                return exp.Anonymous(this="sha2", expressions=[inner.this, length])

        # Case 2: Standalone exp.MD5(x) — sqlglot built-in, arg in node.this
        # Wrap as Anonymous to prevent sqlglot from dropping the argument
        if isinstance(node, (exp.MD5, exp.MD5Digest)) and node.this:
            return exp.Anonymous(this="md5", expressions=[node.this])

        # Case 3: Standalone exp.SHA/SHA2
        if isinstance(node, (exp.SHA, getattr(exp, "SHADigest", type("Dummy",(),{})))) and node.this:
            return exp.Anonymous(this="sha1", expressions=[node.this])
        if isinstance(node, (exp.SHA2, getattr(exp, "SHA2Digest", type("Dummy",(),{})))) and node.this:
            length = getattr(node, "args", {}).get("length") or exp.Literal.number(256)
            return exp.Anonymous(this="sha2", expressions=[node.this, length])

        return node

    # ── ARRAY FUNCTIONS ─────────────────────────────────────────────────────
    @staticmethod
    def fix_array_functions(node: exp.Expression) -> exp.Expression:        # GENERATE_DATE_ARRAY → sequence(to_date(start), to_date(end), INTERVAL 1 DAY)
        if isinstance(node, exp.GenerateDateArray):
            start = node.args.get("start")
            end = node.args.get("end")
            step = node.args.get("step")

            def _ensure_date(expr):
                if not expr:
                    return expr
                if isinstance(expr, exp.Literal) and expr.is_string:
                    return exp.Anonymous(this="to_date", expressions=[expr])
                return expr

            start = _ensure_date(start)
            end = _ensure_date(end)
            if not step:
                step = exp.Interval(this=exp.Literal.string("1"), unit=exp.var("DAY"))
                
            return exp.Anonymous(this="sequence", expressions=[start, end, step])
        # ARRAY_LENGTH → SIZE
        if isinstance(node, exp.ArraySize):
            return exp.Anonymous(this="SIZE", expressions=[node.this])

        # array[OFFSET(n)] → array[n]  and array[SAFE_OFFSET(n)] → get(array, n)
        if isinstance(node, exp.Bracket):
            new_expressions = []
            convert_to_get = False
            get_index = None

            for i, idx in enumerate(node.expressions):
                if isinstance(idx, exp.Anonymous):
                    fn = idx.this.upper()
                    if fn == "OFFSET" and idx.expressions:
                        new_expressions.append(idx.expressions[0])
                        continue
                    elif fn == "SAFE_OFFSET" and idx.expressions:
                        # Must convert entire bracket to get(array, n)
                        convert_to_get = True
                        get_index = idx.expressions[0]
                        break
                    elif fn == "SAFE_ORDINAL" and idx.expressions:
                        # SAFE_ORDINAL is 1-based → get(array, n-1)
                        convert_to_get = True
                        get_index = exp.Sub(
                            this=idx.expressions[0],
                            expression=exp.Literal.number(1),
                        )
                        break
                new_expressions.append(idx)

            if convert_to_get and get_index is not None:
                return exp.Anonymous(
                    this="get",
                    expressions=[node.this, get_index]
                )
            if new_expressions:
                node.set("expressions", new_expressions)

        return node

    # ── INTERVAL SYNTAX ─────────────────────────────────────────────────────
    @staticmethod
    def fix_interval_syntax(node: exp.Expression) -> exp.Expression:
        """DAY → DAYS, MONTH → MONTHS, etc."""
        if isinstance(node, exp.Interval):
            unit = node.args.get("unit")
            if unit:
                unit_map = {
                    "DAY": "DAYS", "MONTH": "MONTHS", "YEAR": "YEARS",
                    "HOUR": "HOURS", "MINUTE": "MINUTES", "SECOND": "SECONDS",
                    "WEEK": "WEEKS",
                }
                unit_str = str(unit).strip("'\"").upper()
                if unit_str in unit_map:
                    node.set("unit", exp.Var(this=unit_map[unit_str]))
        return node

    # ── REGEX PATTERNS ──────────────────────────────────────────────────────
    @staticmethod
    def fix_regex_patterns(node: exp.Expression) -> exp.Expression:
        """Double backslashes in regex literals for Databricks."""
        if isinstance(node, (exp.RegexpLike, exp.RegexpExtract, exp.RegexpReplace)):
            # Check both 'expression' and 'pattern' args for the regex pattern
            for arg_name in ("expression", "pattern"):
                pattern_node = node.args.get(arg_name)
                if pattern_node and isinstance(pattern_node, exp.Literal) and pattern_node.is_string:
                    s = pattern_node.this
                    # Replace single backslashes that aren't already doubled
                    # Use a proper approach: split on \\, process each part, rejoin
                    parts = s.split('\\\\')
                    parts = [p.replace('\\', '\\\\') for p in parts]
                    s = '\\\\'.join(parts)
                    node.set(arg_name, exp.Literal.string(s))
                    break
        return node

    # ── COLLATION (2026) ────────────────────────────────────────────────────
    @staticmethod
    def fix_collation(node: exp.Expression) -> exp.Expression:
        """Map BigQuery collation specs to Databricks 2026 collation names."""
        # Handle Collate expressions if sqlglot parses them
        if isinstance(node, exp.Collate):
            collation = node.args.get("this")
            if collation and hasattr(collation, 'name'):
                coll_name = collation.name.strip("'\"").lower()
                collation_map = {
                    'und:ci': 'UTF8_LCASE',
                    'und:cs': 'UTF8_BINARY',
                    'und': 'UTF8_BINARY',
                }
                if coll_name in collation_map:
                    node.set("this", exp.Var(this=collation_map[coll_name]))
                else:
                    # TODO: language-specific collations ('th', 'ja', 'zh', etc.)
                    # are not yet mapped; Databricks 2026 may not have equivalents.
                    logger.warning("Unmapped collation: %s", coll_name)
        return node
    @staticmethod
    def fix_unnest(node: exp.Expression) -> exp.Expression:
        """Transform BigQuery JOIN UNNEST to Databricks LATERAL VIEW EXPLODE."""
        if isinstance(node, exp.Select):
            new_joins = []
            new_laterals = list(node.args.get('laterals') or [])
            
            for j in (node.args.get('joins') or []):
                if isinstance(j.this, exp.Unnest):
                    is_outer = (j.side == 'LEFT')
                    unnest = j.this
                    exprs = unnest.expressions
                    is_offset = unnest.args.get('offset')
                    
                    if is_offset:
                        func = exp.Posexplode(expressions=exprs)
                        pos_alias = 'pos'
                        if isinstance(is_offset, exp.Alias):
                            pos_alias = is_offset.alias
                        elif isinstance(is_offset, exp.Identifier):
                            pos_alias = is_offset.this
                            
                        alias = unnest.args.get('alias')
                        cols = [exp.to_identifier(pos_alias)]
                        if alias and alias.args.get('columns'):
                            cols.append(alias.args['columns'][0])
                        elif alias and alias.alias:
                            cols.append(exp.to_identifier(alias.alias))
                        table_alias = exp.TableAlias(this=exp.to_identifier('_t'), columns=cols) if alias else exp.TableAlias(this=exp.to_identifier('_t'))
                    else:
                        func = exp.Explode(expressions=exprs)
                        alias = unnest.args.get('alias')
                        cols = []
                        if alias and alias.args.get('columns'):
                            cols.append(alias.args['columns'][0])
                        elif alias and alias.alias:
                            cols.append(exp.to_identifier(alias.alias))
                        table_alias = exp.TableAlias(this=exp.to_identifier('_t'), columns=cols) if alias else exp.TableAlias(this=exp.to_identifier('_t'))
                            
                    lat = exp.Lateral(
                        this=func,
                        view=True,
                        outer=is_outer,
                        alias=table_alias
                    )
                    new_laterals.append(lat)
                else:
                    new_joins.append(j)
                    
            if new_joins:
                node.set('joins', new_joins)
            elif 'joins' in node.args:
                node.args.pop('joins')
                
            if new_laterals:
                node.set('laterals', new_laterals)
        return node

    @staticmethod
    def fix_array_concat_agg(node: exp.Expression) -> exp.Expression:
        """Transform ARRAY_CONCAT_AGG to flatten(collect_list(...))."""
        array_concat_agg_cls = getattr(exp, "ArrayConcatAgg", None)
        group_concat_cls = getattr(exp, "GroupConcat", None)

        is_array_concat_agg = (
            bool(array_concat_agg_cls) and isinstance(node, array_concat_agg_cls)
        ) or (
            bool(group_concat_cls)
            and isinstance(node, group_concat_cls)
            and (getattr(node, "name", "") or "").upper() == "ARRAY_CONCAT_AGG"
        ) or (
            isinstance(node, exp.Anonymous)
            and (getattr(node, "this", "") or "").upper() == "ARRAY_CONCAT_AGG"
        )

        if is_array_concat_agg:
            exprs = node.expressions if getattr(node, "expressions", None) else node.this
            if not isinstance(exprs, list):
                exprs = [exprs]

            collect_list = exp.Anonymous(this='collect_list', expressions=exprs)
            flatten = exp.Anonymous(this='flatten', expressions=[collect_list])
            return flatten
        return node

    @staticmethod
    def fix_ignore_nulls(node: exp.Expression) -> exp.Expression:
        """Transform FIRST_VALUE/LAST_VALUE IGNORE NULLS wrapper into boolean flag argument."""
        if isinstance(node, exp.IgnoreNulls):
            func = node.this
            if hasattr(func, 'this') and hasattr(func, 'sql_name'):
                name = func.sql_name().upper()
                if name in ('FIRST_VALUE', 'LAST_VALUE'):
                    return exp.Anonymous(this=name, expressions=[func.this, exp.var('true')])
        return node

class ExpressionOptimizer:
    """Light optimizations on the translated AST."""

    @staticmethod
    def optimize(sql: str) -> str:
        """Apply performance optimizations. Falls back gracefully."""
        try:
            clean_sql, jinja_map = _extract_jinja(sql)
            # Use parse() instead of parse_one() to avoid truncating multi-statement SQL
            try:
                trees = [t for t in sqlglot.parse(clean_sql, read="databricks") if t is not None]
            except Exception:
                trees = [t for t in sqlglot.parse(clean_sql, read="spark") if t is not None]
            if not trees:
                return sql

            parts = []
            for tree in trees:
                tree = tree.transform(ExpressionOptimizer._optimize_array_operations)
                tree = tree.transform(ExpressionOptimizer._preserve_native_functions)
                try:
                    parts.append(tree.sql(dialect="databricks", pretty=True))
                except Exception:
                    parts.append(tree.sql(dialect="spark", pretty=True))
            result = ";\n".join(parts)

            # Safety check: if optimization lost >40% of content, skip it
            orig_len = len(sql.strip())
            result_len = len(result.strip())
            if orig_len > 2000 and result_len < orig_len * 0.6:
                logger.warning(
                    "ExpressionOptimizer lost content (%d -> %d chars). Skipping.",
                    orig_len, result_len,
                )
                return sql

            return _restore_jinja(result, jinja_map)
        except Exception as e:
            logger.debug("Expression optimizer skipped: %s", e)
            return sql

    @staticmethod
    def _optimize_array_operations(node: exp.Expression) -> exp.Expression:
        """COUNT(DISTINCT EXPLODE(arr)) → size(array_distinct(arr))"""
        if isinstance(node, exp.Count) and node.args.get("distinct"):
            arg = node.this
            if isinstance(arg, exp.Explode):
                array = arg.this
                return exp.Anonymous(
                    this="size",
                    expressions=[exp.Anonymous(this="array_distinct", expressions=[array])]
                )
        return node

    @staticmethod
    def _preserve_native_functions(node: exp.Expression) -> exp.Expression:
        """Prevent sqlglot from expanding Databricks-native functions."""
        if isinstance(node, exp.UnixDate):
            return exp.Anonymous(this="unix_date", expressions=[node.this])
        if isinstance(node, exp.UnixToTime):
            scale = node.args.get("scale")
            if not scale:
                return exp.Anonymous(this="timestamp_seconds", expressions=[node.this])
            scale_val = str(scale) if not hasattr(scale, 'name') else scale.name
            fn_map = {"3": "timestamp_millis", "6": "timestamp_micros"}
            fn_name = fn_map.get(scale_val)
            if fn_name:
                return exp.Anonymous(this=fn_name, expressions=[node.this])
        if isinstance(node, exp.DateFromUnixDate):
            return exp.Anonymous(this="date_from_unix_date", expressions=[node.this])
        # Preserve TRY_CAST safe flag (optimizer re-parse may strip it)
        if isinstance(node, exp.TryCast) and not node.args.get("safe"):
            node.set("safe", True)
        # Preserve DATE_TRUNC (optimizer may render as TRUNC)
        if isinstance(node, exp.DateTrunc):
            unit = node.args.get("unit")
            if unit:
                unit_lit = exp.Literal.string(str(unit).strip("'\""  ).upper())
                return exp.Anonymous(this="DATE_TRUNC", expressions=[unit_lit, node.this])
        return node
