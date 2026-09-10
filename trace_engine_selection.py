from migration.translator_service import TranslatorService
import sys

service = TranslatorService()

# Monkey-patch to trace which engine is selected
original_get_engine = service._get_rule_engine_for_source

def traced_get_engine(source_key, components, sql):
    engine = original_get_engine(source_key, components, sql)
    engine_name = type(engine).__name__
    print(f"DEBUG: Using engine: {engine_name} for source_key={source_key}", file=sys.stderr)
    return engine

service._get_rule_engine_for_source = staticmethod(traced_get_engine)

# Test Snowflake
sql = 'SELECT CAST(col AS INT) AS x'
print("Testing Snowflake input:", sql)
result = service.run_pipeline(
    sql,
    'gpt-5-nano',
    provider='OpenAI',
    api_key='',
    use_llm=False,
    source_engine='snowflake'
)[0].strip()

print("Result:", result)
