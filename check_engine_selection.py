#!/usr/bin/env python3
import sys
from migration.translator_service import TranslatorService

service = TranslatorService()

# Get components
components = service.components

# Test which engine is returned for snowflake
from migration.translator_service import TranslatorService as TS

source_engine = 'snowflake'
sql = 'SELECT CAST(col AS INT) AS x'

source_key = (source_engine or "").strip().lower()
input_is_bigquery_like = TS._looks_like_bigquery_sql(sql)
is_snowflake = source_key == "snowflake" and not input_is_bigquery_like

print(f"source_key: {source_key}")
print(f"input_is_bigquery_like: {input_is_bigquery_like}")
print(f"is_snowflake: {is_snowflake}")

if input_is_bigquery_like:
    source_key = "bigquery"

rule_engine = service._get_rule_engine_for_source(source_key, components, sql)
print(f"rule_engine type: {type(rule_engine).__name__}")
print(f"rule_engine is BigQuery engine: {rule_engine is components['rule_engine']}")
print(f"rule_engine is Snowflake engine: {rule_engine is components['snowflake_rule_engine']}")
print(f"rule_engine is Trino engine: {rule_engine is components['trino_rule_engine']}")
