from decimal import Decimal
from pathlib import Path
import sys
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from connections.redshift import connect_redshift
from query_builder import build_schema_query, qualify_table
from validation_engine import (
    _normalize_hash_scalar,
    normalize_column_list,
    normalize_hash_value,
    numeric_values_equal,
)


def test_normalize_column_list_accepts_react_array():
    assert normalize_column_list(["BRANCH", "ACCOUNT_TYPE"]) == ["BRANCH", "ACCOUNT_TYPE"]


def test_normalize_column_list_accepts_csv_string():
    assert normalize_column_list("BRANCH, ACCOUNT_TYPE") == ["BRANCH", "ACCOUNT_TYPE"]


def test_normalize_column_list_cleans_legacy_array_string():
    assert normalize_column_list("['BRANCH', 'ACCOUNT_TYPE']") == ["BRANCH", "ACCOUNT_TYPE"]


def test_normalize_hash_value_ignores_case_and_padding():
    assert normalize_hash_value(" AB12CD ") == "ab12cd"


def test_numeric_values_equal_ignores_decimal_formatting():
    assert numeric_values_equal(Decimal("123.0000"), "123")


def test_normalize_hash_scalar_sorts_json_keys():
    value = '{"Skill":"Python","Experience":"3 Years","Location":"Chennai"}'
    assert _normalize_hash_scalar(value) == '{"Experience":"3 Years","Location":"Chennai","Skill":"Python"}'


def test_normalize_hash_scalar_sorts_nested_array_values():
    value = '{"Experience":"2 Years","Location":"Delhi","Skill":["Data Science","Analytics"]}'
    assert _normalize_hash_scalar(value) == '{"Experience":"2 Years","Location":"Delhi","Skill":[Analytics,Data Science]}'


def test_normalize_hash_scalar_sorts_comma_separated_values():
    assert _normalize_hash_scalar("excel,python") == "excel,python"
    assert _normalize_hash_scalar("python, excel") == "excel,python"


def test_redshift_query_builder_uses_information_schema_like_postgres():
    assert qualify_table("redshift", "analytics", "public", "orders") == "analytics.public.orders"
    sql = build_schema_query("redshift", "analytics", "public", "orders")
    assert "INFORMATION_SCHEMA.COLUMNS" in sql.upper()
    assert "TABLE_SCHEMA" in sql.upper()


def test_redshift_connect_skips_live_connection_when_details_are_missing():
    with patch("psycopg2.connect") as mock_connect:
        assert connect_redshift() is None
        mock_connect.assert_not_called()


def test_redshift_nvl2_is_not_flagged_as_snowflake():
    from migration.sql_processor import SQLPreprocessor

    sql = """SELECT
    customer_id,
    NVL2(email, 'EMAIL_AVAILABLE', 'EMAIL_MISSING') AS email_status
FROM customers;"""

    assert SQLPreprocessor.detect_source_engine(sql) == "redshift"


def test_redshift_nvl2_translates_to_case_when_not_null():
    from migration.translator_service import TranslatorService

    sql = """SELECT
    customer_id,
    NVL2(email, 'EMAIL_AVAILABLE', 'EMAIL_MISSING') AS email_status
FROM customers;"""

    service = TranslatorService()
    service.components["cache"].clear_all()
    translated_sql, _, _, error = service.run_pipeline(
        sql,
        model="x",
        provider="OpenAI",
        api_key="",
        source_engine="redshift",
        force_llm=False,
        use_llm=False,
    )
    normalized = " ".join(translated_sql.replace("\n", " ").split())

    assert error is None
    assert "CASE WHEN email IS NOT NULL THEN 'EMAIL_AVAILABLE' ELSE 'EMAIL_MISSING' END" in normalized.upper()


def test_redshift_dateadd_and_datediff_translate_to_databricks_forms():
    from migration.translator_service import TranslatorService

    sql = """SELECT
  order_id,
  order_date,
  DATEADD(day, 5, order_date) AS plus_five_days,
  DATEDIFF(day, order_date, CURRENT_DATE) AS days_ago
FROM orders;"""

    service = TranslatorService()
    service.components["cache"].clear_all()
    translated_sql, _, _, error = service.run_pipeline(
        sql,
        model="x",
        provider="OpenAI",
        api_key="",
        source_engine="redshift",
        force_llm=False,
        use_llm=False,
    )
    normalized = " ".join(translated_sql.replace("\n", " ").split()).upper()

    assert error is None
    assert "DATE_ADD(ORDER_DATE, 5)" in normalized
    assert "DATEDIFF(CURRENT_DATE(), ORDER_DATE)" in normalized
    assert "DATE_ADD(DAY, 5, ORDER_DATE)" not in normalized
    assert "DATEDIFF(DAY, ORDER_DATE, DAY)" not in normalized


def test_redshift_benchmark_date_query_uses_valid_databricks_syntax():
    from migration.translator_service import TranslatorService

    sql = """SELECT
  order_id,
  order_date,
  DATEADD(day, 7, order_date) AS due_date,
  DATEDIFF(day, order_date, GETDATE()) AS age_days
FROM orders
WHERE order_date >= DATEADD(month, -3, GETDATE());"""

    service = TranslatorService()
    service.components["cache"].clear_all()
    translated_sql, _, _, error = service.run_pipeline(
        sql,
        model="x",
        provider="OpenAI",
        api_key="",
        source_engine="redshift",
        force_llm=False,
        use_llm=False,
    )
    normalized = " ".join(translated_sql.replace("\n", " ").split()).upper()

    assert error is None
    assert "GETDATE" not in normalized
    assert "CURRENT_DATE()" in normalized
    assert "ADD_MONTHS(CURRENT_DATE(), -3)" in normalized
    assert "DATEDIFF(CURRENT_DATE(), ORDER_DATE)" in normalized
    assert "DATE_ADD(ORDER_DATE, 7)" in normalized
    assert "ADD_MONTHS(CURRENT_TIMESTAMP(, -3)" not in normalized
