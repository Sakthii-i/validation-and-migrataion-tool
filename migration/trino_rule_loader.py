from __future__ import annotations

import csv
import os
import re
from typing import Dict, List


BASE_DIR = os.path.dirname(__file__)
DEFAULT_XLSX_PATH = os.getenv("TRINO_RULES_XLSX_PATH") or r"C:\Users\sakth\Downloads\Trino_to_Databricks_Mapping_v2_Updated.xlsx"
DEFAULT_CSV_PATH = os.path.join(BASE_DIR, "conversion_rules_trino.csv")


def _split_top_level_args(arg_text: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    in_quote: str | None = None
    escape = False
    for ch in arg_text:
        if in_quote:
            current.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_quote:
                in_quote = None
            continue

        if ch in ("'", '"'):
            in_quote = ch
            current.append(ch)
            continue

        if ch == "(":
            depth += 1
            current.append(ch)
            continue
        if ch == ")":
            if depth > 0:
                depth -= 1
            current.append(ch)
            continue
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)

    if current or arg_text.strip():
        parts.append("".join(current).strip())
    return [p for p in parts if p != ""]


class TrinoRuleEngine:
    def __init__(self, rules_list: List[Dict[str, str]] | None = None) -> None:
        self.rules_list = rules_list or []

    @staticmethod
    def _escape_regex_special_chars(s: str) -> str:
        """Escape regex special characters for use in patterns."""
        return re.escape(s)

    def apply_csv_rules(self, sql: str) -> str:
        """Apply rules loaded from CSV using word boundaries and case-insensitive matching."""
        for rule in self.rules_list:
            source_key = "trino_syntax"
            if source_key not in rule or "databricks_sql_syntax" not in rule:
                continue
            
            source_syntax_raw = rule.get(source_key, "").strip()
            target_syntax = rule.get("databricks_sql_syntax", "").strip()
            
            if not source_syntax_raw or not target_syntax:
                continue
            
            # Handle alternative patterns separated by "/"
            # E.g., "DECIMAL(p,s) / NUMERIC(p,s)" -> try both DECIMAL(p,s) and NUMERIC(p,s)
            source_patterns = [s.strip() for s in source_syntax_raw.split("/")]
            
            for source_syntax in source_patterns:
                if not source_syntax:
                    continue
                
                # For simple type mappings like INTEGER -> INT, use word boundaries
                if "(" not in source_syntax and "'" not in source_syntax:
                    # Simple keyword replacement with word boundaries
                    pattern = rf"\b{re.escape(source_syntax)}\b"
                    sql = re.sub(pattern, target_syntax, sql, flags=re.IGNORECASE)
                else:
                    # For patterns with parentheses like NUMERIC(p,s) or functions
                    escaped_source = re.escape(source_syntax)
                    
                    # Handle parameterized types
                    pattern = escaped_source
                    replacement = target_syntax
                    
                    # Order matters: replace (p,s) before (p)
                    for placeholder in ["p,s", "p", "n", "s", "t", "T"]:
                        # Search for \(placeholder\) in the pattern
                        search_pattern = rf"\({placeholder}\)"
                        if search_pattern in pattern:
                            # Replace \(p\) with \(([^)]+)\) - keep parens, add capturing group
                            pattern = pattern.replace(search_pattern, rf"\(([^)]+)\)", 1)
                            
                            # In the replacement target, replace (p) with (\1)
                            group_num = pattern.count("(") - pattern.count(r"\(")
                            target_placeholder = f"({placeholder})"
                            
                            if target_placeholder in replacement:
                                replacement = replacement.replace(target_placeholder, f"(\\{group_num})", 1)
                    
                    # Add word boundary at start
                    pattern = r"\b" + pattern
                    
                    if pattern != r"\b" + escaped_source:  # Pattern has wildcards
                        try:
                            sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)
                        except Exception:
                            pass
        
        return sql

    @staticmethod
    def apply_pre_ast_translation(sql: str) -> str:
        return sql

    @staticmethod
    def _normalize_legacy_date_calls(sql: str) -> str:
        sql = re.sub(r"\bGETDATE\s*\(\s*\)", "CURRENT_TIMESTAMP()", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bSYSDATE\s*\(\s*\)", "CURRENT_DATE()", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bCURRENT_DATE\s*\(\s*,\s*([^)]*?)\s*\)", r"CURRENT_DATE(\1)", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bCURRENT_TIMESTAMP\s*\(\s*,\s*([^)]*?)\s*\)", r"CURRENT_TIMESTAMP(\1)", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bCURRENT_TIME\s*\(\s*\)", "CURRENT_TIMESTAMP()", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bADD_MONTHS\s*\(\s*CURRENT_DATE\s*\(\s*\)\s*,\s*([^)]*?)\s*\)", r"ADD_MONTHS(CURRENT_DATE(), \1)", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bADD_MONTHS\s*\(\s*CURRENT_DATE\s*\(\s*,\s*([^)]*?)\s*\)\s*\)", r"ADD_MONTHS(CURRENT_DATE(), \1)", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bDATEDIFF\s*\(\s*GETDATE\s*\(\s*\)\s*,\s*([^)]*?)\s*\)", r"DATEDIFF(CURRENT_DATE(), \1)", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bDATEDIFF\s*\(\s*CURRENT_DATE\s*\(\s*,\s*([^)]*?)\s*\)\s*\)", r"DATEDIFF(CURRENT_DATE(), \1)", sql, flags=re.IGNORECASE)

        def _rewrite_date_exprs(text: str) -> str:
            pattern = re.compile(r"\b(?:DATEADD|DATEDIFF|DATE_ADD|DATE_DIFF|TIMESTAMPADD)\s*\(", re.IGNORECASE)
            result: List[str] = []
            last = 0
            while True:
                match = pattern.search(text, last)
                if not match:
                    result.append(text[last:])
                    break

                start = match.start()
                open_idx = match.end() - 1
                depth = 0
                end_idx = None
                in_quote: str | None = None
                escape = False
                for idx in range(open_idx, len(text)):
                    ch = text[idx]
                    if in_quote:
                        if escape:
                            escape = False
                        elif ch == "\\":
                            escape = True
                        elif ch == in_quote:
                            in_quote = None
                        continue
                    if ch in ("'", '"'):
                        in_quote = ch
                        continue
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            end_idx = idx
                            break
                if end_idx is None:
                    result.append(text[start:])
                    break

                call = text[start:end_idx + 1]
                fn = call[: call.find("(")].strip()
                args_text = call[call.find("(") + 1:-1]
                parts = _split_top_level_args(args_text)
                rewritten = call
                try:
                    upper_fn = fn.upper()
                    if upper_fn in {"DATEADD", "DATE_ADD"} and len(parts) >= 3:
                        unit = parts[0].strip().strip("'\"")
                        amount = parts[1].strip()
                        date_expr = parts[2].strip()
                        upper_unit = unit.upper()
                        if upper_unit in {"DAY", "DD"}:
                            rewritten = f"DATE_ADD({date_expr}, {amount})"
                        elif upper_unit in {"MONTH", "MM"}:
                            rewritten = f"ADD_MONTHS({date_expr}, {amount})"
                        elif upper_unit in {"YEAR", "YY", "YYYY"}:
                            rewritten = f"ADD_MONTHS({date_expr}, ({amount}) * 12)"
                    elif upper_fn in {"DATEDIFF", "DATE_DIFF"} and len(parts) >= 3:
                        unit = parts[0].strip().strip("'\"")
                        left_expr = parts[1].strip()
                        right_expr = parts[2].strip()
                        upper_unit = unit.upper()
                        if upper_unit in {"DAY", "DD"}:
                            rewritten = f"DATEDIFF({right_expr}, {left_expr})"
                        elif upper_unit in {"MONTH", "MM"}:
                            rewritten = f"CAST(FLOOR(MONTHS_BETWEEN({right_expr}, {left_expr})) AS INT)"
                        elif upper_unit in {"YEAR", "YY", "YYYY"}:
                            rewritten = f"FLOOR(MONTHS_BETWEEN({right_expr}, {left_expr}) / 12)"
                    elif upper_fn == "TIMESTAMPADD" and len(parts) >= 3:
                        unit = parts[0].strip().strip("'\"")
                        amount = parts[1].strip()
                        ts_expr = parts[2].strip()
                        upper_unit = unit.upper()
                        if upper_unit in {"DAY", "DD"}:
                            rewritten = f"DATE_ADD({ts_expr}, {amount})"
                        elif upper_unit in {"MONTH", "MM"}:
                            rewritten = f"ADD_MONTHS({ts_expr}, {amount})"
                        elif upper_unit in {"YEAR", "YY", "YYYY"}:
                            rewritten = f"ADD_MONTHS({ts_expr}, ({amount}) * 12)"
                except Exception:
                    rewritten = call

                result.append(text[last:start])
                result.append(rewritten)
                last = end_idx + 1

            return "".join(result)

        sql = _rewrite_date_exprs(sql)
        sql = re.sub(r"\bCURRENT_DATE\s*\(\s*\)\s*\(\s*,", "CURRENT_DATE(),", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bADD_MONTHS\s*\(\s*CURRENT_DATE\s*\(\s*,\s*([^)]*?)\s*\)\s*\)", r"ADD_MONTHS(CURRENT_DATE(), \1)", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bDATEDIFF\s*\(\s*CURRENT_DATE\s*\(\s*,\s*([^)]*?)\s*\)\s*\)", r"DATEDIFF(CURRENT_DATE(), \1)", sql, flags=re.IGNORECASE)
        return sql

    def apply_rules(self, sql: str) -> str:
        # First apply CSV-based rules
        sql = self.apply_csv_rules(sql)
        
        # Then apply hardcoded transformation rules
        sql = self._rewrite_cross_join_unnest(sql)
        sql = self._rewrite_extract(sql)
        sql = self._normalize_legacy_date_calls(sql)
        sql = re.sub(r"\bCARDINALITY\s*\(", "SIZE(", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bFROM_UNIXTIME\s*\(", "TIMESTAMP_SECONDS(", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bCURRENT_DATE\b(?!\s*\()", "CURRENT_DATE()", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bCURRENT_TIMESTAMP\b(?!\s*\()", "CURRENT_TIMESTAMP()", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bCURRENT_TIME\b(?!\s*\()", "CURRENT_TIMESTAMP()", sql, flags=re.IGNORECASE)
        return sql

    @staticmethod
    def apply_function_translation(sql: str) -> str:
        type_rules = [
            (r"\bTIMESTAMP\s+WITH\s+TIME\s+ZONE\b", "TIMESTAMP"),
            (r"\bINTEGER\b", "INT"),
            (r"\bNUMERIC\s*\(([^)]*)\)", r"DECIMAL(\1)"),
            (r"\bNUMERIC\b", "DECIMAL"),
            (r"\bVARCHAR\s*\([^)]*\)", "STRING"),
            (r"\bVARCHAR\b", "STRING"),
            (r"\bJSON\b", "STRING"),
            (r"\bIPADDRESS\b", "STRING"),
            (r"\bUUID\b", "STRING"),
            (r"\bVARBINARY\b", "BINARY"),
            (r"\bHYPERLOGLOG\b", "BINARY"),
            (r"\bP4HYPERLOGLOG\b", "BINARY"),
            (r"\bQDIGEST\s*<[^>]+>", "BINARY"),
            (r"\bTDIGEST\b", "BINARY"),
        ]
        for pattern, replacement in type_rules:
            sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bARRAY\s*\(\s*INTEGER\s*\)", "ARRAY<INT>", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bARRAY\s*\(\s*VARCHAR\s*\)", "ARRAY<STRING>", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bMAP\s*\(\s*VARCHAR\s*,\s*VARCHAR\s*\)", "MAP<STRING, STRING>", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bMAP\s*\(\s*VARCHAR\s*,\s*INTEGER\s*\)", "MAP<STRING, INT>", sql, flags=re.IGNORECASE)
        return sql

    @staticmethod
    def _rewrite_extract(sql: str) -> str:
        extract_map = {
            "YEAR": "YEAR",
            "QUARTER": "QUARTER",
            "MONTH": "MONTH",
            "WEEK": "WEEKOFYEAR",
            "DAY": "DAY",
            "DAY_OF_YEAR": "DAYOFYEAR",
            "HOUR": "HOUR",
            "MINUTE": "MINUTE",
            "SECOND": "SECOND",
        }
        for part, fn in extract_map.items():
            sql = re.sub(
                rf"\bEXTRACT\s*\(\s*{part}\s+FROM\s+([^)]+)\)",
                rf"{fn}(\1)",
                sql,
                flags=re.IGNORECASE,
            )
        sql = re.sub(
            r"\bEXTRACT\s*\(\s*DAY_OF_WEEK\s+FROM\s+([^)]+)\)",
            r"EXTRACT(DAYOFWEEK_ISO FROM \1)",
            sql,
            flags=re.IGNORECASE,
        )
        return sql

    @staticmethod
    def _rewrite_cross_join_unnest(sql: str) -> str:
        sql = re.sub(
            r"\bCROSS\s+JOIN\s+UNNEST\s*\(\s*([^)]+?)\s*\)\s+WITH\s+ORDINALITY\b",
            r"LATERAL VIEW POSEXPLODE(\1)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"\bUNNEST\s*\(\s*([^)]+?)\s*\)\s+WITH\s+ORDINALITY\b",
            r"POSEXPLODE(\1)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"\bCROSS\s+JOIN\s+UNNEST\s*\(\s*([^)]+?)\s*\)\s+AS\s+(\w+)\s*\(\s*(\w+)\s*\)",
            r"LATERAL VIEW EXPLODE(\1) \2 AS \3",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"\bCROSS\s+JOIN\s+UNNEST\s*\(\s*([^)]+?)\s*\)",
            r"LATERAL VIEW EXPLODE(\1)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(r"\bUNNEST\s*\(\s*([^)]+?)\s*\)", r"EXPLODE(\1)", sql, flags=re.IGNORECASE)
        return sql


def load_trino_rules(path: str | None = None) -> List[Dict[str, str]]:
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
