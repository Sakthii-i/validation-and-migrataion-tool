from __future__ import annotations

import csv
import os
import re
from typing import Dict, List


BASE_DIR = os.path.dirname(__file__)
DEFAULT_XLSX_PATH = os.getenv("TRINO_RULES_XLSX_PATH") or r"C:\Users\sakth\Downloads\Trino_to_Databricks_Mapping_v2_Updated.xlsx"
DEFAULT_CSV_PATH = os.path.join(BASE_DIR, "conversion_rules_trino.csv")


class TrinoRuleEngine:
    def __init__(self, rules_list: List[Dict[str, str]] | None = None) -> None:
        self.rules_list = rules_list or []

    @staticmethod
    def apply_pre_ast_translation(sql: str) -> str:
        return sql

    def apply_rules(self, sql: str) -> str:
        sql = self._rewrite_cross_join_unnest(sql)
        sql = self._rewrite_extract(sql)
        sql = re.sub(
            r"\bDATE_ADD\s*\(\s*'day'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"DATE_ADD(\2, \1)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"\bDATE_ADD\s*\(\s*'month'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"ADD_MONTHS(\2, \1)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"\bDATE_DIFF\s*\(\s*'day'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"DATEDIFF(\2, \1)",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"\bDATE_DIFF\s*\(\s*'month'\s*,\s*([^,]+)\s*,\s*([^)]+)\)",
            r"CAST(MONTHS_BETWEEN(\2, \1) AS INT)",
            sql,
            flags=re.IGNORECASE,
        )
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
