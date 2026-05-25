from decimal import Decimal
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from validation_tool.validation_engine import (
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
