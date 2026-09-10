from migration.translator_service import TranslatorService
from migration.rule_engine import RuleEngine

service = TranslatorService()
components = service.components

sql = 'SELECT col::INT AS x'
print('Input:', sql)

# Get both engines
bq_engine = components['rule_engine']
sf_engine = components['snowflake_rule_engine']

# Apply Snowflake function translation (which handles :: casting)
result = sf_engine.apply_function_translation(sql)
print('After SF apply_function_translation:', result)

# What if we apply BigQuery rules to that result?
result2 = bq_engine.apply_rules(result)
print('After BQ apply_rules:', result2)

result3 = bq_engine.apply_function_translation(result2)
print('After BQ apply_function_translation:', result3)

# Now test with DOUBLE
print('\n=== Test with DOUBLE ===')
sql2 = 'SELECT col::DOUBLE AS x'
print('Input:', sql2)

result = sf_engine.apply_function_translation(sql2)
print('After SF apply_function_translation:', result)

result2 = bq_engine.apply_rules(result)
print('After BQ apply_rules:', result2)

result3 = bq_engine.apply_function_translation(result2)
print('After BQ apply_function_translation:', result3)
