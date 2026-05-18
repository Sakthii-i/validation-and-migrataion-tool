import re
import logging
import sqlglot
from sqlglot import exp
from typing import List, Dict, Tuple, Optional
import networkx as nx
from dataclasses import dataclass, field


@dataclass
class QueryChunk:
    """Represents a chunk of SQL that can be translated independently."""
    id: str
    sql: str
    dependencies: List[str]
    chunk_type: str  # 'cte', 'main', 'subquery', 'union'
    cte_name: Optional[str] = None          # name of this CTE if chunk_type == 'cte'
    original_node: Optional[exp.Expression] = None


class SQLPreprocessor:
    """Handles SQL cleaning, comment extraction, and whitespace normalisation."""

    # SQL scripting keywords that should be preserved as-is (BQ → Databricks 2026 GA)
    _SCRIPTING_KEYWORDS = re.compile(
        r'^\s*(?:DECLARE|SET|BEGIN|END|IF|ELSEIF|ELSE|THEN|LOOP|END\s+LOOP|'
        r'WHILE|END\s+WHILE|REPEAT|UNTIL|END\s+REPEAT|FOR|END\s+FOR|'
        r'LEAVE|ITERATE|RETURN|CALL|BEGIN\s+TRANSACTION|COMMIT\s+TRANSACTION|'
        r'ROLLBACK\s+TRANSACTION|COMMIT|ROLLBACK)\b',
        re.IGNORECASE,
    )

    _BQ_HINTS = [
        r"`[^`]+`",  # backtick-quoted identifiers
        r"\bTO_HEX\s*\(",
        r"\bUNNEST\s*\(",
        r"\bSAFE_(?:CAST|DIVIDE|ADD|SUBTRACT|MULTIPLY|NEGATE)\s*\(",
        r"\bSAFE_(?:OFFSET|ORDINAL)\s*\(",
        r"\bCOUNTIF\s*\(",
        r"\bAPPROX_QUANTILES\s*\(",
        r"\bGENERATE_(?:ARRAY|DATE_ARRAY|TIMESTAMP_ARRAY)\s*\(",
        r"\bFORMAT_(?:DATE|TIMESTAMP|DATETIME)\s*\(",
        r"\bPARSE_(?:DATE|TIMESTAMP|DATETIME)\s*\(",
        r"\bTO_JSON_STRING\s*\(",
        r"\bJSON_EXTRACT_(?:SCALAR|ARRAY)\s*\(",
        r"\bARRAY<",  # BigQuery type literal
        r"\bSTRUCT<",  # BigQuery type literal
        r"@\w+",  # BigQuery parameter
        r"\bSELECT\s+AS\s+STRUCT\b",
    ]

    _SNOWFLAKE_HINTS = [
        r"::\s*[A-Za-z_][A-Za-z0-9_]*",  # Snowflake cast
        r"\bMD5_HEX\s*\(",
        r"\bOBJECT_AGG\s*\(",
        r"\bMEDIAN\s*\(",
        r"\bBOOLOR_AGG\s*\(",
        r"\bBOOLAND_AGG\s*\(",
        r"\bCOUNT_IF\s*\(",
        r"\bHASH_AGG\s*\(",
        r"\bAPPROX_PERCENTILE\s*\(",
        r"\bILIKE\b",
        r"\bSPLIT_PART\s*\(",
        r"\bOBJECT_CONSTRUCT\s*\(",
        r"\bARRAY_CONSTRUCT\s*\(",
        r"\bTO_VARIANT\s*\(",
        r"\bFLATTEN\s*\(",
        r"\bIDENTIFIER\s*\(",
        r"\bSEQ[248]\s*\(",
        r"\bCURRENT_(?:DATABASE|SCHEMA|ROLE|WAREHOUSE)\s*\(",
        r"\bZEROIFNULL\s*\(",
        r"\bNVL2\s*\(",
        r"\bIFF\s*\(",
    ]

    @staticmethod
    def is_scripting_block(sql: str) -> bool:
        """Detect if SQL contains procedural scripting constructs."""
        scripting_patterns = [
            r'\bDECLARE\s+\w+',
            r'\bBEGIN\b(?!\s+TRANSACTION)',  # BEGIN as scripting block, not transaction
            r'\bIF\s+.+\s+THEN\b',
            r'\bLOOP\b',
            r'\bWHILE\s+.+\s+DO\b',
            r'\bFOR\s+\w+\s+IN\b',
        ]
        for pat in scripting_patterns:
            if re.search(pat, sql, re.IGNORECASE):
                return True
        return False

    @staticmethod
    def _can_parse(sql: str, dialect: str) -> bool:
        try:
            sqlglot.parse_one(sql, read=dialect)
            return True
        except Exception:
            return False

    @staticmethod
    def detect_source_engine(sql: str) -> str:
        """Best-effort detection of BigQuery vs Snowflake input SQL."""
        cleaned = SQLPreprocessor.clean_sql(sql or "")
        if not cleaned:
            return "unknown"

        def _score(patterns: List[str]) -> int:
            return sum(1 for pat in patterns if re.search(pat, cleaned, re.IGNORECASE))

        bq_score = _score(SQLPreprocessor._BQ_HINTS)
        sf_score = _score(SQLPreprocessor._SNOWFLAKE_HINTS)

        bq_parse = SQLPreprocessor._can_parse(cleaned, "bigquery")
        sf_parse = SQLPreprocessor._can_parse(cleaned, "snowflake")
        if bq_parse and not sf_parse:
            bq_score += 1
        elif sf_parse and not bq_parse:
            sf_score += 1

        if bq_score == 0 and sf_score == 0:
            return "unknown"
        if bq_score > sf_score:
            return "bigquery"
        if sf_score > bq_score:
            return "snowflake"
        return "ambiguous"

    @staticmethod
    def clean_sql(sql: str) -> str:
        """Remove comments, blank lines, compress whitespace. Preserves placeholder comments."""
        sql = re.sub(r"--[^\n]*", "", sql)
        # Remove block comments EXCEPT our __MLC_/__SLC_ placeholders
        sql = re.sub(r"/\*(?!__(?:MLC|SLC)_\d+__\*/).*?\*/", "", sql, flags=re.DOTALL)
        lines = [line.strip() for line in sql.splitlines() if line.strip()]
        sql = "\n".join(lines)
        sql = re.sub(r" {2,}", " ", sql)
        return sql.strip()

    @staticmethod
    def remove_comment_placeholders(sql: str) -> str:
        """Remove extracted comment placeholders from SQL used as LLM-only context."""
        sql = re.sub(r'/\*__(?:MLC|SLC)_\d+__\*/', ' ', sql)
        sql = re.sub(r' {2,}', ' ', sql)
        sql = re.sub(r'\n\s*\n+', '\n', sql)
        return sql.strip()

    @staticmethod
    def extract_comments(sql: str) -> Tuple[str, Dict[str, str]]:
        """
        Extract comments and replace with placeholders so they survive translation
        and can be restored afterwards.

        Handles nested /* */ comments correctly.
        Wraps placeholders in valid SQL block comments so AST parsers ignore them.

        Returns:
            (sql_without_comments, {placeholder: original_comment})
        """
        comments: Dict[str, str] = {}
        counter = [0]  # mutable for inner closure

        # First pass: handle multi-line comments (supports nesting)
        def extract_multiline(sql: str) -> str:
            result = []
            i = 0
            while i < len(sql):
                if sql[i:i+2] == '/*':
                    # Find matching close, tracking nesting depth
                    depth = 1
                    start = i
                    i += 2
                    while i < len(sql) and depth > 0:
                        if sql[i:i+2] == '/*':
                            depth += 1
                            i += 2
                        elif sql[i:i+2] == '*/':
                            depth -= 1
                            i += 2
                        else:
                            i += 1
                    comment_text = sql[start:i]
                    ph = f"/*__MLC_{counter[0]}__*/"
                    comments[ph] = comment_text
                    counter[0] += 1
                    result.append(" " + ph + " ")
                else:
                    result.append(sql[i])
                    i += 1
            return "".join(result)

        sql = extract_multiline(sql)

        # Second pass: single-line comments
        def replace_singleline(match: re.Match) -> str:
            ph = f"/*__SLC_{counter[0]}__*/"
            comments[ph] = match.group(0).rstrip("\n")
            counter[0] += 1
            return ph + "\n"

        sql = re.sub(r"--[^\n]*", replace_singleline, sql)
        return sql, comments

    @staticmethod
    def restore_comments(sql: str, comments: Dict[str, str]) -> str:
        """Restore extracted comments from placeholders.

        Restores in reverse insertion order so that outer wrappers (e.g. a
        single-line comment ``-- /* ... */`` whose inner block comment was
        extracted first) are unwound before the inner placeholders are
        substituted.  Multiple passes handle arbitrary nesting depth.
        """
        reversed_items = list(reversed(list(comments.items())))
        # Multiple passes to resolve nested placeholders
        for _ in range(3):
            changed = False
            for placeholder, comment in reversed_items:
                if placeholder in sql:
                    sql = sql.replace(placeholder, comment)
                    changed = True
                # Also match spaced variant sqlglot may produce: /*__X__*/ → /* __X__ */
                inner = placeholder[2:-2]  # strip /* and */
                spaced = f"/* {inner} */"
                if spaced in sql:
                    sql = sql.replace(spaced, comment)
                    changed = True
            if not changed:
                break
        return sql

    @staticmethod
    def convert_dbt_partition_config(sql: str) -> str:
        """
        Convert dbt-bigquery config options to dbt-databricks equivalents.

        Handles:
          partition_by  dict/list-of-dicts → list of field names
          incremental_strategy  kept as-is (insert_overwrite, merge, append all valid in dbt-databricks)
        """
        def _replace_partition_dict(m: re.Match) -> str:
            dict_str = m.group(1)
            fields = re.findall(r"['\"]field['\"]\s*:\s*['\"]([^'\"]+)['\"]", dict_str)
            if fields:
                field_list = ', '.join(f"'{f}'" for f in fields)
                return f"partition_by=[{field_list}]"
            return m.group(0)

        # partition_by = { ... } or partition_by = [{ ... }]
        sql = re.sub(
            r"partition_by\s*=\s*(\{[^}]+\}|\[\s*\{[^\]]+\])",
            _replace_partition_dict, sql
        )

        # cluster_by — keep as-is; dbt-databricks supports cluster_by natively
        # (liquid_clustered_by is optional and not always appropriate)

        # incremental_strategy mapping:
        #   BQ 'insert_overwrite' → Databricks 'append' (no direct equivalent)
        #   BQ 'merge'            → Databricks 'merge'  (same, no change needed)
        def _replace_strategy(m: re.Match) -> str:
            strategy = m.group(1)
            bq_to_dbx = {
                'merge': 'merge',
                'insert_overwrite': 'insert_overwrite',
                'append': 'append',
            }
            return f"incremental_strategy='{bq_to_dbx.get(strategy, strategy)}'"

        sql = re.sub(
            r"incremental_strategy\s*=\s*['\"](\w+)['\"]",
            _replace_strategy, sql
        )

        return sql


class QueryChunker:
    """Splits large queries into independently-translatable CTE chunks."""

    # Known SQL keywords that are NOT CTE/table names
    _SQL_KEYWORDS = frozenset({
        "select", "from", "where", "join", "left", "right", "inner", "outer",
        "full", "cross", "on", "group", "by", "order", "having", "limit",
        "offset", "union", "all", "distinct", "as", "and", "or", "not",
        "in", "exists", "between", "like", "is", "null", "true", "false",
        "case", "when", "then", "else", "end", "with", "recursive",
        "insert", "update", "delete", "create", "drop", "alter", "table",
        "view", "index", "set", "values", "into", "over", "partition",
        "rows", "range", "unbounded", "preceding", "following", "current",
        "row", "asc", "desc", "nulls", "first", "last", "lateral",
        "date", "timestamp", "interval", "cast", "try_cast", "coalesce",
        "if", "ifnull", "nvl", "struct", "array", "map",
    })

    def __init__(self, max_chunk_size: int = 500):
        self.max_chunk_size = max_chunk_size
        self._logger = logging.getLogger(__name__)

    def chunk_query(self, sql: str) -> List[QueryChunk]:
        """
        Parse SQL and split into CTE chunks + main query.
        Each chunk contains ONLY its own SQL, not the full query.
        """
        # Scripting blocks (DECLARE, IF/THEN, LOOP, etc.) cannot be chunked
        if SQLPreprocessor.is_scripting_block(sql):
            self._logger.info("SQL scripting block detected - disabling chunking")
            return [QueryChunk(id="main", sql=sql, dependencies=[], chunk_type="main")]

        # Multi-statement transactions should not be chunked
        if re.search(r'\bBEGIN\s+TRANSACTION\b', sql, re.IGNORECASE):
            self._logger.info("Transaction block detected - disabling chunking")
            return [QueryChunk(id="main", sql=sql, dependencies=[], chunk_type="main")]

        # Recursive CTEs create cyclic dependencies that break topological sort
        if "WITH RECURSIVE" in sql.upper():
            self._logger.warning("Recursive CTE detected - disabling chunking")
            return [QueryChunk(id="main", sql=sql, dependencies=[], chunk_type="main")]

        # Use parse() instead of parse_one() so multi-statement SQL is not
        # silently truncated to the first statement.
        try:
            trees = sqlglot.parse(sql, read="bigquery")
            trees = [t for t in trees if t is not None]
            if len(trees) == 0:
                return [QueryChunk(id="main", sql=sql, dependencies=[], chunk_type="main")]
            if len(trees) > 1:
                # Multi-statement input — return each statement as its own chunk
                chunks: List[QueryChunk] = []
                for idx, stmt_tree in enumerate(trees):
                    chunks.append(QueryChunk(
                        id=f"stmt_{idx}",
                        sql=stmt_tree.sql(dialect="bigquery", pretty=True),
                        dependencies=[],
                        chunk_type="main",
                        original_node=stmt_tree,
                    ))
                # Safety check: if regenerated SQL lost >40% of content,
                # fall back to original SQL as single chunk to prevent truncation
                regenerated_total = sum(len(c.sql) for c in chunks)
                if len(sql.strip()) > 2000 and regenerated_total < len(sql.strip()) * 0.6:
                    self._logger.warning(
                        "AST regeneration lost content (%d -> %d chars, %.0f%% loss). "
                        "Falling back to original SQL to prevent truncation.",
                        len(sql.strip()), regenerated_total,
                        (1 - regenerated_total / len(sql.strip())) * 100,
                    )
                    return [QueryChunk(id="main", sql=sql, dependencies=[], chunk_type="main")]
                return chunks
            tree = trees[0]

            # Safety check for single-tree parse: if regenerated SQL is
            # significantly shorter, the parser silently dropped content.
            regenerated = tree.sql(dialect="bigquery", pretty=True)
            if len(sql.strip()) > 2000 and len(regenerated) < len(sql.strip()) * 0.6:
                self._logger.warning(
                    "Single-tree AST regeneration lost content (%d -> %d chars). "
                    "Falling back to original SQL.",
                    len(sql.strip()), len(regenerated),
                )
                return [QueryChunk(id="main", sql=sql, dependencies=[], chunk_type="main")]

        except Exception:
            return [QueryChunk(
                id="main", sql=sql, dependencies=[], chunk_type="main"
            )]

        chunks: List[QueryChunk] = []
        cte_names: List[str] = []

        # Extract each CTE as its own chunk
        with_clause = tree.args.get("with") or tree.args.get("with_")
        if with_clause:
            for i, cte in enumerate(with_clause.expressions):
                cte_name = cte.alias
                cte_names.append(cte_name)
                # Get just the CTE body SQL (not the full WITH clause)
                cte_body_sql = cte.this.sql(dialect="bigquery", pretty=True) if cte.this else ""
                if len(cte_body_sql) > self.max_chunk_size:
                    # Split oversized CTE on UNION ALL boundaries
                    sub_parts = self._split_on_union(cte_body_sql)
                    if len(sub_parts) > 1:
                        self._logger.info(
                            "CTE '%s' split into %d sub-chunks (%d chars)",
                            cte_name, len(sub_parts), len(cte_body_sql),
                        )
                        for j, part in enumerate(sub_parts):
                            part_deps = self._find_cte_dependencies(part, cte_names[:-1])
                            chunks.append(QueryChunk(
                                id=f"cte_{i}_{cte_name}_part{j}",
                                sql=part,
                                dependencies=part_deps,
                                chunk_type="cte_part",
                                cte_name=cte_name,
                                original_node=cte,
                            ))
                        continue
                    else:
                        self._logger.warning(
                            "CTE '%s' exceeds max_chunk_size (%d > %d chars) "
                            "but cannot be split further",
                            cte_name, len(cte_body_sql), self.max_chunk_size,
                        )
                deps = self._find_cte_dependencies(cte_body_sql, cte_names[:-1])
                chunks.append(QueryChunk(
                    id=f"cte_{i}_{cte_name}",
                    sql=cte_body_sql,
                    dependencies=deps,
                    chunk_type="cte",
                    cte_name=cte_name,
                    original_node=cte,
                ))

        # Main query body (strip the WITH clause to avoid duplication)
        # Clone tree without CTEs to get just the final SELECT
        main_tree = tree.copy()
        if main_tree.args.get("with") or main_tree.args.get("with_"):
            main_tree.args.pop("with", None)
            main_tree.args.pop("with_", None)
        main_sql = main_tree.sql(dialect="bigquery", pretty=True)
        main_deps = self._find_cte_dependencies(main_sql, cte_names)

        chunks.append(QueryChunk(
            id="main",
            sql=main_sql,
            dependencies=main_deps,
            chunk_type="main",
            original_node=tree,
        ))

        return chunks

    @staticmethod
    def _split_on_union(sql: str) -> List[str]:
        """
        Split SQL on top-level UNION ALL boundaries.
        Returns a list of parts; if no UNION ALL found, returns [sql].
        Only splits at top level (not inside parentheses).
        Accounts for string literals to avoid counting parens inside strings.
        """
        parts: List[str] = []
        depth = 0
        in_string = False
        string_char = ''
        escape_next = False
        
        current_start = 0
        upper_sql = sql.upper()
        i = 0
        
        while i < len(sql):
            ch = sql[i]
            
            if escape_next:
                escape_next = False
            elif ch == '\\':
                escape_next = True
            elif in_string:
                if ch == string_char:
                    in_string = False
            elif ch in ("'", '"'):
                in_string = True
                string_char = ch
            elif ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif depth == 0 and not in_string and upper_sql[i:i+9] == 'UNION ALL':
                # Check it's a word boundary
                before_ok = (i == 0 or not upper_sql[i-1].isalnum())
                after_ok = (i + 9 >= len(sql) or not upper_sql[i+9].isalnum())
                if before_ok and after_ok:
                    part = sql[current_start:i].strip()
                    if part:
                        parts.append(part)
                    current_start = i + 9
                    i += 9
                    continue
            i += 1
            
        # Last segment
        tail = sql[current_start:].strip()
        if tail:
            parts.append(tail)
        return parts if len(parts) > 1 else [sql]

    def _find_cte_dependencies(self, sql: str, known_ctes: List[str]) -> List[str]:
        """
        Return the subset of known_ctes that are actually referenced in sql.
        Uses sqlglot to find table references, falling back to improved regex.
        """
        if not known_ctes:
            return []

        # Try AST-based detection first (accurate, no false positives)
        try:
            tree = sqlglot.parse_one(sql, read="bigquery")
            referenced_tables = {
                t.name.lower()
                for t in tree.find_all(exp.Table)
            }
            return [name for name in known_ctes if name.lower() in referenced_tables]
        except Exception:
            pass

        # Fallback: regex that avoids substring matches by requiring SQL context
        # A CTE name must follow FROM/JOIN/,/( or be at word boundary with non-alphanumeric neighbors
        sql_lower = sql.lower()
        deps = []
        for name in known_ctes:
            escaped = re.escape(name.lower())
            # Match name preceded by FROM/JOIN/comma or whitespace, followed by non-alphanumeric
            pattern = r'(?:(?:FROM|JOIN)\s+|,\s*)' + escaped + r'(?:\s|$|[),;])'
            if re.search(pattern, sql_lower, re.IGNORECASE):
                deps.append(name)
        return deps

    def reassemble(self, chunks: List[QueryChunk], translated_map: Dict[str, str]) -> str:
        """
        Reassemble translated chunks back into a complete WITH ... SELECT query.
        """
        cte_chunks = [c for c in chunks if c.chunk_type in ("cte", "cte_part")]
        main_chunks = [c for c in chunks if c.chunk_type == "main"]

        parts = []

        if cte_chunks:
            # Group cte_part chunks back together per CTE name with UNION ALL
            cte_bodies: Dict[str, List[str]] = {}
            cte_order: List[str] = []
            for c in cte_chunks:
                body = translated_map.get(c.id, c.sql)
                if c.cte_name not in cte_bodies:
                    cte_bodies[c.cte_name] = []
                    cte_order.append(c.cte_name)
                cte_bodies[c.cte_name].append(body)

            cte_parts = []
            for name in cte_order:
                body = "\nUNION ALL\n".join(cte_bodies[name])
                cte_parts.append(f"{name} AS (\n{body}\n)")
            parts.append("WITH " + ",\n\n".join(cte_parts))

        # Multi-statement SQL can produce multiple main chunks (stmt_0, stmt_1, ...).
        # Preserve all statements in original order instead of keeping only the first one.
        if main_chunks:
            for main_chunk in main_chunks:
                parts.append(translated_map.get(main_chunk.id, main_chunk.sql))

        return "\n\n".join(parts)

    def build_dependency_graph(self, chunks: List[QueryChunk]) -> nx.DiGraph:
        """Build directed graph of chunk dependencies."""
        G = nx.DiGraph()
        # Map CTE name → all chunk IDs (handles cte_part splits)
        ids_by_cte_name: Dict[str, List[str]] = {}

        for chunk in chunks:
            G.add_node(chunk.id, chunk=chunk)
            if chunk.cte_name:
                ids_by_cte_name.setdefault(chunk.cte_name, []).append(chunk.id)

        for chunk in chunks:
            for dep_name in chunk.dependencies:
                dep_ids = ids_by_cte_name.get(dep_name, [])
                for dep_id in dep_ids:
                    if dep_id != chunk.id:
                        G.add_edge(dep_id, chunk.id)

        return G

    def get_translation_order(self, chunks: List[QueryChunk]) -> List[str]:
        """Topologically sorted translation order."""
        G = self.build_dependency_graph(chunks)
        try:
            return list(nx.topological_sort(G))
        except nx.NetworkXUnfeasible:
            return [chunk.id for chunk in chunks]


class ExpressionCache:
    """In-memory cache for translated SQL expressions within a session."""

    def __init__(self):
        self._cache: Dict[str, str] = {}
        self.hits = 0
        self.misses = 0

    def get(self, expr: str) -> Optional[str]:
        if expr in self._cache:
            self.hits += 1
            return self._cache[expr]
        self.misses += 1
        return None

    def set(self, expr: str, translation: str) -> None:
        self._cache[expr] = translation

    def clear_all(self) -> None:
        """Clear the expression cache entirely."""
        self._cache.clear()
        self.hits = 0
        self.misses = 0

    def stats(self) -> Dict:
        total = self.hits + self.misses
        hit_rate = self.hits / total if total > 0 else 0.0
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": f"{hit_rate:.1%}",
            "size": len(self._cache),
        }
