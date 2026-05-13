import csv
from pathlib import Path
from typing import Dict, List, Tuple


def load_snowflake_rules(csv_path: str = None) -> Tuple[List[Dict[str, str]], Dict[str, List[Dict[str, str]]], List[Dict[str, str]]]:
    """Load Snowflake -> Databricks conversion rules from CSV and return in the same
    shape as TranslatorService.load_conversion_rules(): (rules_list, rules_dict, edge_cases)

    The CSV is expected to contain at least two columns mapping a Snowflake syntax
    to a Databricks equivalent. Common headers: 'Snowflake', 'Databricks_Equivalent',
    'Type' (optional category).
    """
    rules_list = []
    rules_dict = {}
    edge_cases = []

    default_candidates = [
        Path(__file__).parent.parent / 'combined_snowflake_databricks_rules(in).csv',
        Path(__file__).parent.parent / 'combined_snowflake_databricks_rules.csv',
    ]

    csv_file = None
    if csv_path:
        p = Path(csv_path)
        if p.exists():
            csv_file = p
    if csv_file is None:
        for c in default_candidates:
            if c.exists():
                csv_file = c
                break

    if not csv_file:
        return rules_list, rules_dict, edge_cases

    with open(csv_file, 'r', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            # Try several common header names
            sf = (row.get('Snowflake') or row.get('snowflake') or row.get('Source') or row.get('source') or '').strip()
            dbx = (row.get('Databricks_Equivalent') or row.get('Databricks') or row.get('databricks_equivalent') or row.get('databricks') or row.get('Target') or row.get('target') or '').strip()
            if not sf or not dbx or sf == dbx:
                continue
            cat = (row.get('Type') or row.get('type') or row.get('Category') or row.get('category') or '').strip()
            # Map into the shape expected by RuleEngine loader: use bigquery_syntax field to hold source example
            rules_list.append({'category': cat, 'bigquery_syntax': sf, 'databricks_sql_syntax': dbx})
            if cat:
                rules_dict.setdefault(cat, []).append({'bq': sf, 'db': dbx, 'example_bq': row.get('example_bq', ''), 'example_dbsql': row.get('example_dbsql', '')})

    return rules_list, rules_dict, edge_cases
