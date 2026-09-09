from migration.translator_service import TranslatorService
from migration.snowflake_rule_loader import SnowflakeRuleEngine

service = TranslatorService()
sf_engine = SnowflakeRuleEngine([], [])

# Test the double-colon cast through snowflake's apply_function_translation
test_cases = [
    'SELECT col::INT AS x',
    'SELECT CAST(col AS INT) AS x',
    'CAST(col AS INT)',
]

print("=== Snowflake apply_function_translation ===")
for sql in test_cases:
    result = sf_engine.apply_function_translation(sql)
    print(f"Input:  {sql}")
    print(f"Output: {result}")
    print()

# Check if the issue is in the rules
print("=== Snowflake apply_rules ===")
for sql in test_cases:
    result = sf_engine.apply_rules(sql)
    print(f"Input:  {sql}")
    print(f"Output: {result}")
    print()

# Test what happens through the full pipeline for the Snowflake engine
print("=== Test through SnowflakeRuleEngine methods directly ===")
sql = 'SELECT col::INT AS x'
print(f"Input: {sql}")

# Step 1: apply_pre_ast_translation
result = sf_engine.apply_pre_ast_translation(sql)
print(f"After pre_ast: {result}")

# Step 2: sqlglot transpile
t, _ = service._transpile_to_databricks(result, 'snowflake')
print(f"After transpile: {t.strip()}")

# Step 3: apply_rules
t = sf_engine.apply_rules(t)
print(f"After rules: {t.strip()}")

# Step 4: apply_function_translation
t = sf_engine.apply_function_translation(t)
print(f"After function_translation: {t.strip()}")

# Step 5: ExpressionOptimizer
from migration.ast_transformer import ExpressionOptimizer
t = ExpressionOptimizer.optimize(t)
print(f"After optimize: {t.strip()}")

print("\n" + "="*50)
print("Now test the full run_pipeline:")
result = service.run_pipeline(sql, 'gpt-5-nano', provider='OpenAI', api_key='', use_llm=False, source_engine='snowflake')[0]
print(f"Final result: {result.strip()}")
