#!/usr/bin/env python3
import sys
from migration.translator_service import TranslatorService
from migration.rule_engine import RuleEngine
from migration.snowflake_rule_loader import SnowflakeRuleEngine

service = TranslatorService()

# Patch apply_rules to trace what's happening
original_bq_apply_rules = RuleEngine.apply_rules
original_sf_apply_rules = SnowflakeRuleEngine.apply_rules

def traced_bq_apply_rules(self, sql):
    result = original_bq_apply_rules(self, sql)
    if 'INT' in sql or 'INT' in result:
        print(f"BQ apply_rules: {sql[:60]} -> {result[:60]}", file=sys.stderr)
    return result

def traced_sf_apply_rules(self, sql):
    result = original_sf_apply_rules(self, sql)
    if 'INT' in sql or 'INT' in result:
        print(f"SF apply_rules: {sql[:60]} -> {result[:60]}", file=sys.stderr)
    return result

RuleEngine.apply_rules = traced_bq_apply_rules
SnowflakeRuleEngine.apply_rules = traced_sf_apply_rules

# Also patch apply_function_translation
original_bq_apply_func = RuleEngine.apply_function_translation
original_sf_apply_func = SnowflakeRuleEngine.apply_function_translation

def traced_bq_apply_func(self, sql):
    result = original_bq_apply_func(self, sql)
    if 'INT' in sql or 'INT' in result:
        print(f"BQ apply_function_translation: {sql[:60]} -> {result[:60]}", file=sys.stderr)
    return result

def traced_sf_apply_func(self, sql):
    result = original_sf_apply_func(self, sql)
    if 'INT' in sql or 'INT' in result:
        print(f"SF apply_function_translation: {sql[:60]} -> {result[:60]}", file=sys.stderr)
    return result

RuleEngine.apply_function_translation = traced_bq_apply_func
SnowflakeRuleEngine.apply_function_translation = traced_sf_apply_func

# Test
sql = 'SELECT CAST(col AS INT) AS x'
print("\n===== Testing Snowflake input =====")
print(f"Input: {sql}")
result = service.run_pipeline(
    sql,
    'gpt-5-nano',
    provider='OpenAI',
    api_key='',
    use_llm=False,
    source_engine='snowflake'
)[0].strip()
print(f"Output: {result}")
